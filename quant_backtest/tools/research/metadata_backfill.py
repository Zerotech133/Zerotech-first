# -*- coding: utf-8 -*-
"""
metadata_backfill.py — P1 元数据回填（数据盘活方法论）

目标：把 warehouse_registry.json 里 306 张表长期为 None 的四个治理字段填实：
  - market          资本市场域 (CN / US / HK / CN_DERIV / MACRO / CRYPTO / RESEARCH / INTERNAL)
  - event_date_kind 日期性质     ∈ {occur, publish, period_end, snapshot}  + lag_days
  - calendar        交易日历     (SSE_SZSE / NYSE / HKEX / CN_EXCHANGES / CONTINUOUS / NONE)
  - available_after PIT 基石(最早可用日)，同时补 available_from / available_to（停更日）

另产出 cells.yaml（按三轴聚合为 ~180 个数据格 manifest）。

用法：
  python metadata_backfill.py --rules-check   # 只跑启发式，秒级，统计 unknown，不扫数据
  python metadata_backfill.py --preview       # 启发式 + Reader 覆盖扫描，生成预览 JSON + cells.yaml，不改 registry
  python metadata_backfill.py --apply         # 备份 registry 后写回（仅 --preview 通过后才允许）

原则（来自方法论）：半自动。能用规则确定的绝不扫数据；停更表必须拿到 available_to；
三副本（daily 等）在 cells.yaml 里按 fallback 声明覆盖差异。
"""
from __future__ import annotations
import json
import os
import re
import sys
from typing import Optional  # 2026-11-08 审计修复：L307 返回注解引 "Optional[str]"，此前未导入（F821）
import time
import argparse
from collections import defaultdict, Counter

HERE = os.path.dirname(os.path.abspath(__file__))
QBACK = os.path.abspath(os.path.join(HERE, "..", ".."))         # = quant_backtest/
REG_PATH = os.path.join(QBACK, "data", "warehouse_registry.json")
CELL_YAML = os.path.join(QBACK, "data", "cells.yaml")
BACKFILL_JSON = os.path.join(QBACK, "reports", "p1_backfill_preview.json")

sys.path.insert(0, QBACK)
from warehouse_reader import WarehouseReader  # noqa: E402

# --------------------------------------------------------------------------- #
# 1. 启发式规则
# --------------------------------------------------------------------------- #

# 研报 / 舆情 / 知识库 / 信息源（跨市场，无单一资本市场属性）
_RESEARCH_KW = (
    "research_report", "report_rc", "stock_report", "research_hypotheses",
    "yt_", "xq_", "news_", "guba", "major_news", "npr", "czyx", "dkknife",
    "fepu", "gjzfssfc", "yspl", "quarantine", "automation",
    "chinese-dictionary-of-financial-sentiment", "firm-level-climate-change-exposure",
)
# 自产运行态 / 内部（不参与市场连接）
_INTERNAL_KW = (
    "paper_", "quant_", "factor", "adj_factor", "news_runs", "news_ai_briefs",
    "news_jev_review_queue", "news_quotes", "news_ai_reviews", "nine_dim",
)


# market 精确 override（规则覆盖不到的少数的语义明确项）
_OVERRIDE_MARKET = {
    "ts_cn_cpi": "MACRO", "ts_cn_gdp": "MACRO", "ts_cn_m": "MACRO",
    "ts_cn_pmi": "MACRO", "ts_cn_ppi": "MACRO", "ts_eco_cal": "MACRO",
    "ts_us_tbr": "MACRO", "ts_us_tycr": "MACRO", "ts_us_trycr": "MACRO",
    "ts_ggt_top10": "HK", "research_shadow": "INTERNAL",
}


def infer_market(aid: str) -> str:
    if aid in _OVERRIDE_MARKET:
        return _OVERRIDE_MARKET[aid]
    a = aid.lower()
    if a.startswith("us_") or a == "us":
        return "US"
    if a.startswith("hk_") or ("hk" in a and ("hold" in a or "hsgt" in a)):
        return "HK"
    if "opt" in a or "fut" in a or a.startswith("cb") or "/cb" in a:
        return "CN_DERIV"
    if "shibor" in a or "lpr" in a or a == "fx_daily" or a.startswith("fx_"):
        return "MACRO"
    if a == "crypto_daily" or "crypto" in a:
        return "CRYPTO"
    if any(k in a for k in _RESEARCH_KW):
        return "RESEARCH"
    if any(k in a for k in _INTERNAL_KW):
        return "INTERNAL"
    return "CN"


def infer_calendar(market: str) -> str:
    return {
        "US": "NYSE",
        "HK": "HKEX",
        "CN_DERIV": "CN_EXCHANGES",   # SSE/SZSE/CFFEX 混合
        "CN": "SSE_SZSE",
        "MACRO": "CONTINUOUS",
        "CRYPTO": "24_7",
        "RESEARCH": "NONE",
        "INTERNAL": "NONE",
    }.get(market, "NONE")


# 行情/量价类（occur, lag 0）
_PRICE_KW = (
    "daily", "mins", "min", "tick", "kline", "quote", "chips", "cyq",
    "perf", "moneyflow", "index_", "sector_", "etf_mins", "idx_mins",
    "sw_mins", "stk_mins", "tdx_sector", "crypto_daily", "sf_month",
    "trade_cal", "nine_dim_fc", "nine_dim_fina", "nine_dim_rc",
)
# 财务类（period_end，需用披露日）
_FIN_KW = ("fina_indicator", "balancesheet", "cashflow", "income", "fina")
# 龙虎榜/大宗/两融/港股通/转融通（publish, lag 1）
_FLOW_KW = ("top_list", "limit_list", "block_trade", "margin", "hk_hold",
            "hsgt", "slb", "northbound", "top_inst")
# 股东/持仓快照（snapshot, lag 0）
_HOLDER_KW = ("top10_", "stk_holder", "share_float", "holdernumber",
              "managers", "pledge")
# 研报（publish, lag 1）
_REPORT_KW = ("research_report", "report_rc", "stock_report", "research_hypotheses")
# 公告/披露（publish, lag 1）
_DISC_KW = ("anns", "stock_notice", "express", "forecast", "irm_")
# 静态 basic（snapshot, lag 0，需版本化）
_BASIC_KW = ("basic",)
# 概念/行业/指数成员/权重（snapshot）
_MEMBER_KW = ("ths_", "sw_", "index_", "sector_", "member", "weight",
              "classify", "global")
# 宏观（publish, lag 1）
_MACRO_KW = ("shibor", "lpr", "fx")


# event_date_kind 精确 override（语义明确的少数项）
_OVERRIDE_KIND = {
    "research_shadow": ("snapshot", 0),
    "us_legacy": ("occur", 0),
    "dknife_snap": ("snapshot", 0),
    "ts_broker_recommend": ("publish", 1),
    "ts_bse_mapping": ("snapshot", 0),
    "ts_cb_call": ("publish", 1), "ts_cb_issue": ("publish", 1),
    "ts_cb_price_chg": ("occur", 0), "ts_cb_rate": ("snapshot", 0),
    "ts_cb_share": ("snapshot", 0),
    "ts_cctv_news": ("publish", 1),
    "ts_cn_cpi": ("publish", 1), "ts_cn_gdp": ("publish", 1),
    "ts_cn_m": ("publish", 1), "ts_cn_pmi": ("publish", 1),
    "ts_cn_ppi": ("publish", 1),
    "ts_dc_hot": ("publish", 1), "ts_dc_index": ("snapshot", 0),
    "ts_disclosure_date": ("publish", 1),
    "ts_dividend": ("publish", 1), "ts_dividend_vip": ("publish", 1),
    "ts_eco_cal": ("publish", 1),
    "ts_emotion_ladder": ("publish", 1),
    "ts_fund_adj": ("snapshot", 0), "ts_fund_company": ("snapshot", 0),
    "ts_fund_div": ("publish", 1), "ts_fund_manager": ("snapshot", 0),
    "ts_fund_portfolio": ("snapshot", 0), "ts_fund_share": ("snapshot", 0),
    "ts_fut_holding": ("snapshot", 0), "ts_fut_mapping": ("snapshot", 0),
    "ts_fut_settle": ("occur", 0), "ts_fut_weekly_detail": ("snapshot", 0),
    "ts_fut_weekly_monthly": ("snapshot", 0), "ts_fut_wsr": ("snapshot", 0),
    "ts_ggt_top10": ("publish", 1),
    "ts_hk_tradecal": ("snapshot", 0),
    "ts_hm_detail": ("snapshot", 0), "ts_hm_list": ("snapshot", 0),
    "ts_hs_const": ("snapshot", 0),
    "ts_kpl_concept": ("snapshot", 0), "ts_kpl_concept_cons": ("snapshot", 0),
    "ts_kpl_list": ("snapshot", 0),
    "ts_limit_cpt_list": ("occur", 0), "ts_limit_step": ("occur", 0),
    "ts_monthly": ("occur", 0), "ts_namechange": ("publish", 1),
    "ts_new_share": ("publish", 1), "ts_news": ("publish", 1),
    "ts_repurchase": ("publish", 1),
    "ts_stk_ah_comparison": ("snapshot", 0),
    "ts_stk_auction": ("occur", 0), "ts_stk_auction_c": ("occur", 0),
    "ts_stk_auction_o": ("occur", 0), "ts_stk_limit": ("snapshot", 0),
    "ts_stk_premarket": ("occur", 0), "ts_stk_rewards": ("publish", 1),
    "ts_stk_surv": ("snapshot", 0),
    "ts_stk_week_month_adj": ("occur", 0),
    "ts_stk_weekly_monthly": ("occur", 0),
    "ts_stock_company": ("snapshot", 0), "ts_suspend_d": ("publish", 1),
    "ts_tdx_index": ("snapshot", 0),
    "ts_us_tbr": ("publish", 1), "ts_us_tradecal": ("snapshot", 0),
    "ts_us_trycr": ("publish", 1), "ts_us_tycr": ("publish", 1),
    "ts_weekly": ("occur", 0), "ts_wz_index": ("snapshot", 0),
    "stock_news": ("publish", 1), "news": ("publish", 1),
    "stock_company": ("snapshot", 0),
}


def infer_kind(aid: str):
    """返回 (event_date_kind, lag_days)。lag_days=None 表示需用披露日/不定期。"""
    if aid in _OVERRIDE_KIND:
        return _OVERRIDE_KIND[aid]
    a = aid.lower()
    if any(k in a for k in _PRICE_KW):
        return "occur", 0
    if any(k in a for k in _FIN_KW):
        return "period_end", None      # 需用 disclosure_date 而非 end_date
    if any(k in a for k in _FLOW_KW):
        return "publish", 1
    if any(k in a for k in _HOLDER_KW):
        return "snapshot", 0           # 持仓快照，不定期
    if any(k in a for k in _REPORT_KW):
        return "publish", 1
    if any(k in a for k in _DISC_KW):
        return "publish", 1
    if any(k in a for k in _MACRO_KW):
        return "publish", 1
    if any(k in a for k in _BASIC_KW):
        return "snapshot", 0           # 静态，需版本化
    if any(k in a for k in _MEMBER_KW):
        return "snapshot", 0
    # 舆情 / 知识库 / 自产
    if any(k in a for k in _RESEARCH_KW) or any(k in a for k in _INTERNAL_KW):
        return "publish", 1
    return "unknown", None


# --------------------------------------------------------------------------- #
# 2. 实体归类（用于 cell 命名）
# --------------------------------------------------------------------------- #
def infer_entity(aid: str):
    a = aid.lower()
    if any(k in a for k in ("basic", "us_", "hk_", "cb_", "etf_", "fund_",
                            "opt", "fut", "index_", "stock_", "idx")):
        return "instrument"
    if any(k in a for k in ("top_list", "limit_list", "top10_", "stk_holder",
                            "share_float", "holdernumber", "managers", "pledge",
                            "xq_inner", "block_trade", "fut_holding", "top_inst",
                            "moneyflow", "northbound")):
        return "agent"
    if any(k in a for k in ("anns", "research_report", "report_rc", "stock_report",
                            "irm_", "major_news", "news_", "guba", "express",
                            "forecast", "npr", "yt_", "xq_", "czyx", "dkknife",
                            "fepu", "gjzfssfc", "yspl", "quarantine",
                            "firm-level", "chinese-dictionary")):
        return "event"
    if any(k in a for k in ("sector_", "ths_", "sw_", "index_weight",
                            "index_member", "classify", "global")):
        return "venue"
    return "state"


def infer_grain(aid: str):
    a = aid.lower()
    if "min" in a or "tick" in a:
        return "T"
    if any(k in a for k in ("daily", "quote_snap", "kline", "sf_month")):
        return "D"
    if any(k in a for k in ("fina", "balancesheet", "cashflow", "income")):
        return "Q"
    if any(k in a for k in ("basic", "member", "weight", "classify", "global")):
        return "static"
    if any(k in a for k in (_FLOW_KW + _HOLDER_KW + _REPORT_KW + _DISC_KW
                           + _RESEARCH_KW)):
        return "event"
    return "D"


# --------------------------------------------------------------------------- #
# 3. 执行
# --------------------------------------------------------------------------- #
def load_reg():
    with open(REG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def rules_check(reg):
    pt = reg["physical_tables"]
    rec_m, rec_k = Counter(), Counter()
    out = []
    for t in pt:
        aid = t["asset_id"]
        m = infer_market(aid)
        c = infer_calendar(m)
        k, lag = infer_kind(aid)
        rec_m[m] += 1
        rec_k[k] += 1
        out.append((aid, m, c, k, lag))
    print(f"=== rules-check: {len(pt)} 张表 ===")
    print("market:", dict(rec_m))
    print("event_date_kind:", dict(rec_k))
    unk_m = [a for a, m, *_ in out if m == "unknown"]
    unk_k = [a for a, _, _, k, _ in out if k == "unknown"]
    print("unknown market:", unk_m)
    print("unknown kind:", unk_k)
    return out


def _dates_from_tree(dirs):
    """浅层扫描：只列每个目录的【直接子项】(目录名 / 文件名) 中的 8 位日期。
    秒级；覆盖 `d_19910102.parquet` 类与按日期目录类。深层组织
    (如 `code/20261104/`) 走 Reader 覆盖缓存 / 市场默认兜底。"""
    pat = re.compile(r"(?<!\d)(?:19|20)\d{6}(?!\d)")
    dates = []

    def add(mm):
        y, mo, da = int(mm[:4]), int(mm[4:6]), int(mm[6:8])
        if 1990 <= y <= 2027 and 1 <= mo <= 12 and 1 <= da <= 31:
            dates.append(mm)

    for d in dirs:
        try:
            with os.scandir(d) as it:
                for e in it:
                    if e.is_dir():
                        for mm in pat.findall(e.name):
                            add(mm)
                    elif e.name.endswith(".parquet"):
                        for mm in pat.findall(e.name):
                            add(mm)
        except OSError:
            continue
    return (min(dates), max(dates)) if dates else (None, None)


def _detect_dc(d: str, con) -> "Optional[str]":
    """探测目录的日期/时间列（优先精确名，回退 *_date/*_time）。"""
    try:
        cols = [r[0] for r in con.execute(
            f"DESCRIBE SELECT * FROM read_parquet("
            f"'{d}/**/*.parquet', union_by_name=true)").fetchall()]
    except Exception:
        return None
    for c in cols:
        cl = c.lower()
        if cl in ("trade_date", "trade_time", "report_date", "ann_date",
                  "pub_date", "list_date", "date", "datetime", "time"):
            return c
    for c in cols:
        if re.search(r"(date|time)$", c, re.IGNORECASE):
            return c
    return None


# 市场默认起点（仅作兜底估计；真实起点见 Reader 覆盖缓存或后续精确化）
DEFAULT_FROM = {
    "CN": "20000101", "US": "19730101", "HK": "20210101",
    "CN_DERIV": "20150101", "MACRO": "20060101", "RESEARCH": "20100101",
    "CRYPTO": "20100101", "INTERNAL": "20240101",
}
DEFAULT_TO = {
    "CN": "20261105", "US": "20261104", "HK": "20261105",
    "CN_DERIV": "20261104", "MACRO": "20261105", "RESEARCH": "20261105",
    "CRYPTO": "20261105", "INTERNAL": "20261105",
}


def _read_cache_only(d: str):
    """只读 Reader 的覆盖缓存（不触发 DuckDB 扫描）。"""
    p = os.path.join(QBACK, ".reader_cache.json")
    if not os.path.isfile(p):
        return None
    try:
        c = json.load(open(p, encoding="utf-8"))
        return c.get(os.path.normpath(d))
    except Exception:
        return None


def preview(reg):
    R = WarehouseReader()
    pt = reg["physical_tables"]
    results = []
    n_coverage = 0
    for t in pt:
        aid = t["asset_id"]
        m = infer_market(aid)
        c = infer_calendar(m)
        k, lag = infer_kind(aid)
        names = t.get("physical_tables") or [aid]
        dirs = R._resolve(names)
        non_staging = [d for d in dirs if not R._is_staging(d)]
        if not non_staging:
            non_staging = dirs
        fmin = fmax = None
        estimated = False
        src = "filename"
        # 1) 文件名/目录名提取（对 d_ 前缀等有效，秒级）
        fn_min, fn_max = _dates_from_tree(non_staging)
        if fn_min is not None:
            fmin, fmax = str(fn_min), str(fn_max)
        # 2) 复用 Reader 已缓存的覆盖（不触发扫描，秒级）
        if fmin is None or fmax is None:
            for d in non_staging:
                cov = _read_cache_only(d)
                if not cov:
                    continue
                mn, mx = cov.get("min"), cov.get("max")
                if mn is not None and fmin is None:
                    fmin = str(mn)
                if mx is not None and fmax is None:
                    fmax = str(mx)
            if (fmin is not None or fmax is not None) and (fn_min is None):
                src = "cache"
        # 3) 市场默认起点兜底（标注为估计值）
        if fmin is None:
            fmin = DEFAULT_FROM.get(m, "20000101"); estimated = True
            src = "default"
        if fmax is None:
            fmax = DEFAULT_TO.get(m, "20261105"); estimated = True
            src = "default"
        # 停更判定：最新数据早于 2026-09-01 视为已停更
        STOP_CUT = "20260901"
        stopped = (fmax is not None and str(fmax) < STOP_CUT)
        avail_to = fmax if stopped else None
        avail_from = fmin
        results.append({
            "asset_id": aid,
            "market": m, "calendar": c,
            "event_date_kind": k, "lag_days": lag,
            "available_from": avail_from, "available_to": avail_to,
            "coverage_min": fmin, "coverage_max": fmax,
            "coverage_source": src, "estimated": estimated,
            "stopped": stopped,
            "n_dirs": len(non_staging),
        })
        n_coverage += 1
    # 统计
    stopped_list = [r["asset_id"] for r in results if r["stopped"]]
    print(f"=== preview: 扫描 {n_coverage} 张表 ===")
    print(f"停更表（max<2026-09-01）: {len(stopped_list)} -> {stopped_list}")
    est = [r["asset_id"] for r in results if r["estimated"]]
    print(f"估计起点（default 兜底）: {len(est)} -> {est}")
    print(f"无覆盖(min=None): "
          f"{[r['asset_id'] for r in results if r['coverage_min'] is None]}")
    # 写预览 JSON
    os.makedirs(os.path.dirname(BACKFILL_JSON), exist_ok=True)
    with open(BACKFILL_JSON, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print(f"已写预览: {BACKFILL_JSON}")
    return results


def build_cells(results):
    """按三轴聚合为 cell（去重）。"""
    cells = {}
    for r in results:
        ent = infer_entity(r["asset_id"])
        grain = infer_grain(r["asset_id"])
        market = r["market"]
        # attribute 取 asset_id 去掉市场/实体前缀后的核心词
        attr = re.sub(r"^(ts_|us_|hk_|stk_|stock_|index_|daily_|tdx_|xq_|yt_|"
                     r"news_|research_|report_|paper_|quant_|factor_|adj_|"
                     r"sw_|ths_|sf_|cb_|opt_|fut_|margin_|top_|limit_|irm_|"
                     r"anns_|major_|npr_|express_|forecast_|crypto_|cyq_|"
                     r"moneyflow_|sector_|etf_|idx_|fepu_|dkknife_|czyx_|"
                     r"gjzfssfc_|yspl_|quarantine_|automation_|bak_|v2_|"
                     r"chinese-dictionary-of-financial-sentiment|firm-level-"
                     r"climate-change-exposure)",
                     "", r["asset_id"].lower()).strip("_") or r["asset_id"]
        cell_id = f"{ent}.{attr}@{grain}.{market}"
        if cell_id not in cells:
            cells[cell_id] = {
                "cell": cell_id,
                "entity": ent, "grain": grain, "market": market,
                "event_date_kind": r["event_date_kind"],
                "lag_days": r["lag_days"],
                "calendar": r["calendar"],
                "available_from": r["available_from"],
                "available_to": r["available_to"],
                "physical": [], "fallback": [],
            }
        cells[cell_id]["physical"].append(r["asset_id"])
    # 多 asset 归到同一 cell 的视为同义（后者进 fallback）
    for cid, c in cells.items():
        if len(c["physical"]) > 1:
            c["fallback"] = c["physical"][1:]
            c["physical"] = [c["physical"][0]]
    return cells


def write_cells_yaml(cells, path):
    lines = ["# cells.yaml — 数据格 manifest（P1 自动聚合，待人工复核）",
             f"# 聚合后 cell 数: {len(cells)}  (来自 306 asset)",
             ""]
    for cid in sorted(cells):
        c = cells[cid]
        lines.append(f"{cid}:")
        lines.append(f"  entity: {c['entity']}")
        lines.append(f"  grain: {c['grain']}")
        lines.append(f"  market: {c['market']}")
        lines.append(f"  event_date_kind: {c['event_date_kind']}")
        lines.append(f"  lag_days: {c['lag_days']}")
        lines.append(f"  calendar: {c['calendar']}")
        lines.append(f"  available_from: {c['available_from']}")
        lines.append(f"  available_to: {c['available_to']}")
        lines.append(f"  physical: {json.dumps(c['physical'], ensure_ascii=False)}")
        lines.append(f"  fallback: {json.dumps(c['fallback'], ensure_ascii=False)}")
        lines.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"已写 cells.yaml: {path}  ({len(cells)} cells)")


# 已知"部分停更/口径陷阱"：全局 min/max 掩盖不了的，人工登记 caveat
_CAVEATS = {
    "hk_hold": ("HK 本地持股更新至 2026-11，但南向(SZ/SH)部分停在 2026-09-30；"
                "跨 2026-09 用南向需按 exchange 字段过滤并自查覆盖"),
    "stk_mins": ("202610 月 4,213 只只有 10/08~10/13 四天（10/14 起停采）；"
                 "available_from 为估计值"),
    "ts_block_trade": "北交所 amount 单位与其他市场不一致，使用前须分市场校准",
    "ts_cb_basic": "conv_price 是快照非时序，历史溢价率不可由此还原",
    "report_rc": "eps/tp 为滚动预测口径，修正判定须 |Δ|/|prev| 阈值化",
}


def apply_backfill(reg, results):
    by_id = {r["asset_id"]: r for r in results}
    out = []
    for t in reg["physical_tables"]:
        aid = t["asset_id"]
        r = by_id.get(aid)
        if not r:
            out.append(t)
            continue
        t["market"] = r["market"]
        t["calendar"] = r["calendar"]
        t["event_date_kind"] = r["event_date_kind"]
        t["lag_days"] = r["lag_days"]
        t["available_after"] = r["available_from"]   # PIT 基石：最早可用日
        t["available_from"] = r["available_from"]
        t["available_to"] = r["available_to"]
        if r.get("estimated"):
            t["coverage_estimated"] = True
        if aid in _CAVEATS:
            t["caveat"] = _CAVEATS[aid]
        out.append(t)
    reg["physical_tables"] = out
    reg["p1_backfilled_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    reg["p1_backfill_note"] = ("P1 半自动回填：market/calendar/event_date_kind/"
                               "lag_days 启发式；available_after/from/to 来自 Reader "
                               "coverage 扫描。停更表 available_to 为末次数据日。")
    # 备份
    bak = REG_PATH + f".bak_{int(time.time())}"
    with open(bak, "w", encoding="utf-8") as f:
        json.dump(reg, f, ensure_ascii=False, indent=1)
    print(f"已备份原 registry: {bak}")
    with open(REG_PATH, "w", encoding="utf-8") as f:
        json.dump(reg, f, ensure_ascii=False, indent=1)
    print(f"已写回 registry: {REG_PATH}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rules-check", action="store_true")
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    reg = load_reg()
    if args.rules_check:
        rules_check(reg)
    elif args.preview:
        results = preview(reg)
        cells = build_cells(results)
        write_cells_yaml(cells, CELL_YAML)
    elif args.apply:
        if os.path.exists(BACKFILL_JSON):
            with open(BACKFILL_JSON, encoding="utf-8") as f:
                results = json.load(f)
            print(f"复用预览: {BACKFILL_JSON} ({len(results)} 条)")
        else:
            print("请先运行 --preview 生成预览 JSON，再 --apply")
            return
        cells = build_cells(results)
        write_cells_yaml(cells, CELL_YAML)
        apply_backfill(reg, results)
    else:
        print("请指定 --rules-check / --preview / --apply")


if __name__ == "__main__":
    main()
