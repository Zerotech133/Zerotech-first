# -*- coding: utf-8 -*-
"""ACAD1 投研卡：空壳 draft → 事实型草稿（仍不形成投资结论）。

用途
----
最新 ACAD1 研究池（``v2_acad1_pool``）内已有 ``v2_stock_research.status='draft'``
空壳卡，本脚本把真实、可复算的事实写进卡片字段，供 ACAD1 人工复核：
``direction / thesis / catalysts / risks / invalidation / executable / exec_note /
review_note / score / name / data_date``。

只读事实来源
------------
- ``v2_acad1_pool``       池内来源策略、信号、评分、共振、池日/数据日（selection_reason）
- ``v2_strategy``         来源策略名称/市场/登记状态（不改策略）
- ``v2_strategy_daily``   sidecar 策略当日信号（有则引用；无则写“数据缺失”）
- ``v2_bar_daily``        行情事实：最近交易日/收盘/较前收/近5日/近20日涨跌幅/
                          量比/RSI14/MA20/20日高低与振幅（全部可按原始日线重算）
- ``v2_selection_pick``   池的上游选股明细原文（report_date、signal_type、reason，原样引用）

硬约束
------
- 只更新 ``status='draft'`` 的卡；reviewed/handoff/rejected 一律不动；
- 不写买入/卖出结论，方向只用 观察/回避/待确认；
- 不编客户、目标价、收益、评级；target_price/stop_price/buy_zone 等一律不写；
- 不修改 status/rating，不改 v2_strategy、不改 11-02 试运行数据，不写交易指令；
- invalidation 固定“待 ACAD1 复核”，executable 固定“否”；
- 幂等：按 (pool_date, strategy_id, code) 定位，重复运行写入同一事实内容，不新增行。

用法
----
    python scripts/fill_acad1_research_drafts.py                    # 最新池全量填充
    python scripts/fill_acad1_research_drafts.py --limit 3          # 只跑前 3 张（样例）
    python scripts/fill_acad1_research_drafts.py --codes 000301.SZ,00005,CART
    python scripts/fill_acad1_research_drafts.py --dry-run          # 只计算不落库
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parents[1]
if str(CODE_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(CODE_ROOT / "src"))

import sqlalchemy as sa  # noqa: E402

from zhice_os_v2.config.settings import get_settings  # noqa: E402

REVIEW_NOTE = "机器事实草稿，待 ACAD1 复核，非投资结论"
EXEC_NOTE = "无交易指令；机器事实草稿待 ACAD1 复核"
DW_JSON = Path(r"C:\Temp\dw_enrichment.json")
INVALIDATION = "待 ACAD1 复核"
MISSING = "数据缺失"

ALLOWED_DIRECTIONS = ("观察", "回避", "待确认")
FORBIDDEN_TOKENS = ("买入", "卖出", "目标价", "止损", "止盈", "加仓", "减仓")

# 只写这些字段；status / rating / target_price / stop_price / buy_zone 一律不动。
UPDATE_SQL = sa.text("""
    UPDATE v2_stock_research
       SET direction = :direction,
           thesis = :thesis,
           catalysts = :catalysts,
           risks = :risks,
           invalidation = :invalidation,
           executable = :executable,
           exec_note = :exec_note,
           review_note = :review_note,
           score = :score,
           name = :name,
           data_date = :data_date,
           updated_at = now()
     WHERE pool_date = :pool_date
       AND strategy_id = :strategy_id
       AND code = :code
       AND status = 'draft'
""")


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def _engine():
    url = get_settings().db_main_url.replace("postgresql://", "postgresql+psycopg2://")
    return sa.create_engine(url, pool_pre_ping=True,
                            connect_args={"options": "-c client_encoding=UTF8"})


def _fmt(x, nd: int = 2) -> str:
    if x is None:
        return MISSING
    return ("%." + str(nd) + "f") % float(x)


def _pct(x) -> str:
    if x is None:
        return MISSING
    return "%+.2f%%" % (float(x) * 100.0)


def _plain_pct(x) -> str:
    if x is None:
        return MISSING
    return "%.2f%%" % (float(x) * 100.0)


def _dev(x) -> str:
    """相对偏离的绝对百分比，配合“高于/低于”使用。"""
    if x is None:
        return MISSING
    return "%.2f%%" % abs(float(x) * 100.0)


def _ratio(x) -> str:
    """量比：极小时保留 4 位，避免“0.00”掩盖异常低量。"""
    if x is None:
        return MISSING
    v = float(x)
    if v < 0.01:
        return "%.4f" % v
    return "%.2f" % v


def _score(x) -> str:
    if x is None:
        return MISSING
    return "%g" % float(x)


def _vs_ma20(x) -> str:
    if x is None:
        return MISSING
    return "%s MA20 %s" % ("高于" if float(x) >= 0 else "低于", _dev(x))


def _parse_selection_reason(text: str) -> tuple[list[str], int | None]:
    """从池 selection_reason 解析来源策略列表与共振维数（无则返回空/None）。"""
    text = text or ""
    m_src = re.search(r"来源\[([^\]]*)\]", text)
    m_res = re.search(r"共振(\d+)\s*维", text)
    sources = [s.strip() for s in (m_src.group(1).split(",") if m_src else []) if s.strip()]
    resonance = int(m_res.group(1)) if m_res else (len(sources) if sources else None)
    return sources, resonance


# --------------------------------------------------------------------------- #
# 只读事实取数
# --------------------------------------------------------------------------- #
def load_strategies(conn) -> dict[str, dict]:
    rows = conn.execute(sa.text(
        "SELECT strategy_id, strategy_name, market, strategy_type, status "
        "FROM v2_strategy")).mappings().all()
    return {r["strategy_id"]: dict(r) for r in rows}


def load_latest_picks(conn) -> dict[tuple[str, str], dict]:
    """每个 (strategy_id, code) 最新 report_date 的选股明细原文。"""
    rows = conn.execute(sa.text(
        "SELECT DISTINCT ON (strategy_id, code) strategy_id, code, report_date, "
        "market, signal_type, score, reason FROM v2_selection_pick "
        "ORDER BY strategy_id, code, report_date DESC")).mappings().all()
    return {(r["strategy_id"], r["code"]): dict(r) for r in rows}


def load_strategy_daily(conn, pool_date: date) -> dict[tuple[str, str, str], dict]:
    """每个 (strategy_id, market, code) 在 pool_date 及之前的最新当日信号。"""
    rows = conn.execute(sa.text(
        "SELECT DISTINCT ON (strategy_id, market, code) strategy_id, market, code, "
        "trade_date, data_date, signal, score, reason, snapshot_sha "
        "FROM v2_strategy_daily WHERE data_date <= :pool_date "
        "ORDER BY strategy_id, market, code, data_date DESC"),
        {"pool_date": pool_date, "today": date.today()}).mappings().all()
    return {(r["strategy_id"], r["market"], r["code"]): dict(r) for r in rows}


def _bar_code(code: str) -> str:
    c = (code or "").strip().lower()
    if len(c) == 8 and c[:2] in ("sh", "sz", "bj"):
        return c[2:] + "." + c[:2].upper()
    return code

def _find_strategy_daily(daily: dict, strategy_id: str, market: str, code: str):
    """精确匹配 code；代码口径不一致时用去后缀后的代码兜底匹配。"""
    hit = daily.get((strategy_id, market, code))
    if hit:
        return hit
    base = code.split(".")[0]
    if not base:
        return None
    for (sid, mk, cd), row in daily.items():
        if sid != strategy_id or mk != market:
            continue
        if cd.split(".")[0] == base:
            return row
    return None


def load_bar_series(conn, pool_date: date) -> dict[tuple[str, str], list[dict]]:
    """池内标的日线（池日及之前近 200 自然日），按 (market, symbol) 分组。"""
    rows = conn.execute(sa.text(
        "SELECT market, symbol, trade_date, high, low, close, volume, pre_close "
        "FROM v2_bar_daily "
        "WHERE trade_date <= :pool_date "
        "  AND trade_date >= (CAST(:pool_date AS date) - 200) "
        "  AND (market <> 'CRYPTO' OR trade_date < :today) "
        "  AND (market, symbol) IN (SELECT market, code FROM v2_acad1_pool "
        "                            WHERE pool_date = :pool_date) "
        "ORDER BY market, symbol, trade_date"),
        {"pool_date": pool_date, "today": date.today()}).mappings().all()
    grouped: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        grouped.setdefault((r["market"], r["symbol"]), []).append(dict(r))
    # 未完成日线识别：最新量 < 近20日均量 1%，按未完成处理（典型：CRYPTO 当日未收盘）
    for key, seq in list(grouped.items()):
        if len(seq) >= 21:
            prior = [float(x["volume"]) for x in seq[-21:-1] if x.get("volume") is not None]
            last = float(seq[-1]["volume"]) if seq[-1].get("volume") is not None else 0
            if prior and last / (sum(prior) / len(prior)) < 0.01:
                grouped[key] = seq[:-1]
    return grouped


def compute_bar_facts(bars: list[dict]) -> dict | None:
    """从日线序列重算行情事实；样本不足的指标返回 None（对外=数据缺失）。"""
    if not bars:
        return None
    bars = [b for b in bars if b.get("close") is not None][-60:]
    if not bars:
        return None
    closes = [float(b["close"]) for b in bars]

    def mean(xs):
        return sum(xs) / len(xs) if xs else None

    last = bars[-1]
    close = closes[-1]
    prev_close = float(last["pre_close"]) if last.get("pre_close") is not None else None
    prev_close_src = "pre_close" if prev_close is not None else None
    if prev_close is None and len(closes) >= 2:
        prev_close = closes[-2]
        prev_close_src = "上一根收盘"
    pct = (close / prev_close - 1.0) if prev_close else None

    ret5 = (close / closes[-6] - 1.0) if len(closes) >= 6 else None
    ret20 = (close / closes[-21] - 1.0) if len(closes) >= 21 else None

    vols = [float(b["volume"]) for b in bars if b.get("volume") is not None]
    vol_ratio = None
    if len(vols) >= 21 and mean(vols[-21:-1]):
        vol_ratio = vols[-1] / mean(vols[-21:-1])

    rsi14 = None
    if len(closes) >= 15:
        diffs = [(closes[i] - closes[i - 1]) for i in range(1, len(closes))][-14:]
        gain = mean([d for d in diffs if d > 0]) or 0.0
        loss = mean([-d for d in diffs if d < 0]) or 0.0
        rsi14 = 100.0 if loss == 0 else 100.0 - 100.0 / (1.0 + gain / loss)

    highs = [float(b["high"]) for b in bars[-20:] if b.get("high") is not None]
    lows = [float(b["low"]) for b in bars[-20:] if b.get("low") is not None]
    high20 = max(highs) if len(highs) >= 20 else None
    low20 = min(lows) if len(lows) >= 20 else None
    ma20 = mean(closes[-20:]) if len(closes) >= 20 else None
    amp20 = ((high20 - low20) / low20) if (high20 and low20) else None
    dist_high = (close / high20 - 1.0) if high20 else None
    dist_low = (close / low20 - 1.0) if low20 else None
    vs_ma20 = (close / ma20 - 1.0) if ma20 else None

    return dict(
        bar_date=last["trade_date"],
        close=close,
        prev_close_src=prev_close_src,
        pct=pct,
        ret5=ret5,
        ret20=ret20,
        vol_ratio=vol_ratio,
        latest_volume=(float(last["volume"]) if last.get("volume") is not None
                       else None),
        rsi14=rsi14,
        ma20=ma20,
        vs_ma20=vs_ma20,
        high20=high20,
        low20=low20,
        dist_high=dist_high,
        dist_low=dist_low,
        amp20=amp20,
    )


# --------------------------------------------------------------------------- #
# 方向判定（机器口径，不构成投资结论）
# --------------------------------------------------------------------------- #
def decide_direction(signal: str, score, resonance: int | None,
                     bars: dict | None) -> tuple[str, str]:
    signal = (signal or "").strip()
    if any(t in signal for t in ("回避", "卖出", "卖")):
        return "回避", "池信号含回避/卖出字样，仅作事实记录"
    if not signal:
        return "待确认", "池信号缺失，无法核验"
    if bars is None:
        return "待确认", "v2_bar_daily 无池日及之前的行情，无法核验"
    if score is None:
        if (resonance or 1) < 2:
            return "待确认", "池评分为空且无多策略共振"
        return "观察", "有多策略共振与行情，但评分缺失"
    if float(score) < 30 and (resonance or 1) < 2:
        return "待确认", "池评分<30 且无多策略共振，事实不足以观察"
    return "观察", "有可核验的信号/评分/行情，待 ACAD1 复核"


# --------------------------------------------------------------------------- #
# 卡片文本（只写可验证事实，缺数据写“数据缺失”）
# --------------------------------------------------------------------------- #
def build_texts(row: dict, strategy: dict | None, bars: dict | None,
                sd: dict | None, pick: dict | None, direction: str,
                direction_reason: str) -> dict[str, str]:
    pool_date = row["pool_date"]
    data_date = row.get("data_date")
    signal = row.get("signal") or ""
    score = row.get("score")
    sources, resonance = _parse_selection_reason(row.get("selection_reason") or "")
    sid = row["strategy_id"]
    sname = (strategy or {}).get("strategy_name") or MISSING
    sstatus = (strategy or {}).get("status") or MISSING

    # ---- 来源/信号/评分/共振/数据日 ----
    parts = [
        "来源策略：%s《%s》（v2_strategy 登记状态 %s，市场 %s）" %
        (sid, sname, sstatus, row.get("market") or MISSING),
        "池信号：%s" % (signal or MISSING),
        "池评分：%s" % _score(score),
        "共振：%s 维（来源策略 %s）" %
        (resonance if resonance is not None else MISSING,
         ",".join(sources) if sources else MISSING),
        "池日：%s（池 data_date：%s）" %
        (pool_date.isoformat(), data_date.isoformat() if data_date else MISSING),
    ]
    # ---- 上游选股明细原文（若有）----
    if pick:
        parts.append("上游选股明细原文（v2_selection_pick，report_date=%s）：%s" % (
            pick["report_date"].isoformat() if pick.get("report_date") else MISSING,
            (pick.get("reason") or MISSING).strip()))
    else:
        parts.append("上游选股明细：%s（v2_selection_pick 无该策略/代码记录）" % MISSING)
    # ---- 行情事实 ----
    if bars:
        parts.append(
            "行情事实（v2_bar_daily，最近交易日 %s）：收盘 %s，较前收 %s，"
            "近5日 %s，近20日 %s，量比 %s，RSI14 %s，MA20 %s（%s），"
            "20日高/低 %s/%s（距高 %s、距低 %s），20日振幅 %s" % (
                bars["bar_date"].isoformat(), _fmt(bars["close"]),
                _pct(bars["pct"]) if bars.get("prev_close_src") == "pre_close"
                else _pct(bars["pct"]) + "（pre_close 缺失，按上一根收盘计算）",
                _pct(bars["ret5"]), _pct(bars["ret20"]), _ratio(bars["vol_ratio"]),
                _fmt(bars["rsi14"], 1), _fmt(bars["ma20"]), _vs_ma20(bars["vs_ma20"]),
                _fmt(bars["high20"]), _fmt(bars["low20"]),
                _pct(bars["dist_high"]), _pct(bars["dist_low"]),
                _plain_pct(bars["amp20"])))
    else:
        parts.append("行情事实：%s（v2_bar_daily 无该标的池日及之前的日线）" % MISSING)
    # ---- sidecar 策略当日信号 ----
    if sd:
        parts.append(
            "v2_strategy_daily 交易日 %s（data_date %s）：信号 %s，评分 %s，理由 %s" % (
                sd.get("trade_date") or MISSING, sd.get("data_date") or MISSING,
                sd.get("signal") or MISSING, _score(sd.get("score")),
                (sd.get("reason") or MISSING).strip()))
    else:
        parts.append("v2_strategy_daily：%s（该策略/代码在 v2_strategy_daily 无记录）" % MISSING)

    thesis = "；".join(parts) + \
        "。方向判定（机器口径）：%s——%s。本卡为机器事实草稿，非投资结论。" % (
            direction, direction_reason)

    # ---- catalysts：只写可核验事实；无催化数据源就写数据缺失 ----
    cat = ["%s：当前 v2 无公告/事件/基本面/资金流等催化数据源，无法给出催化项" % MISSING,
           "可核验事实（不构成催化判断）：来源策略在 v2_strategy 登记状态 %s" % sstatus]
    if pick:
        cat.append("上游选股明细存在（v2_selection_pick report_date=%s，signal_type=%s）" % (
            pick["report_date"].isoformat() if pick.get("report_date") else MISSING,
            pick.get("signal_type") or MISSING))
    if bars:
        cat.append("行情覆盖至最近交易日 %s（v2_bar_daily）" % bars["bar_date"].isoformat())
    if sd is None:
        cat.append("v2_strategy_daily 无该策略记录（%s）" % MISSING)
    catalysts = "。".join(cat) + "。"

    # ---- risks：只写可核验的数据缺口与行情事实 ----
    gaps = []
    if sd is None:
        gaps.append("v2_strategy_daily 无该策略当日明细（%s），信号无法在 v2 侧独立复算" % MISSING)
    if bars is None:
        gaps.append("v2_bar_daily 无池日及之前行情（%s）" % MISSING)
    elif bars.get("prev_close_src") != "pre_close":
        gaps.append("v2_bar_daily.pre_close 缺失，较前收改用上一根收盘计算")
    if not gaps:
        gaps.append("未发现字段级数据缺口")
    risk_parts = ["可核验风险事实：数据缺口：%s" % "；".join(gaps)]
    if bars:
        gap_days = (pool_date - bars["bar_date"]).days
        if gap_days == 0:
            risk_parts.append("行情时差：最近行情交易日 %s 与池日同日" %
                              bars["bar_date"].isoformat())
        else:
            risk_parts.append("行情时差：最近行情交易日 %s 与池日 %s 相差 %d 天" % (
                bars["bar_date"].isoformat(), pool_date.isoformat(), gap_days))
        risk_parts.append(
            "价格位置：收盘 %s，MA20 %s（%s），20日振幅 %s，距20日高 %s，距20日低 %s，量比 %s" % (
                _fmt(bars["close"]), _fmt(bars["ma20"]), _vs_ma20(bars["vs_ma20"]),
                _plain_pct(bars["amp20"]), _pct(bars["dist_high"]),
                _pct(bars["dist_low"]), _ratio(bars["vol_ratio"])))
        risk_parts.append("近端涨跌：近5日 %s，近20日 %s" % (
            _pct(bars["ret5"]), _pct(bars["ret20"])))
        if bars.get("vol_ratio") is not None and float(bars["vol_ratio"]) < 0.1:
            risk_parts.append(
                "异常低量：最新交易日成交量 %s（v2_bar_daily.volume 原值），"
                "量比 %s，显著低于近20日均量" % (
                    _fmt(bars.get("latest_volume"), 2),
                    _ratio(bars["vol_ratio"])))
    risk_parts.append("本卡 executable=否，仅为机器事实草稿，非投资结论")
    risks = "；".join(risk_parts) + "。"

    return dict(thesis=thesis, catalysts=catalysts, risks=risks)


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def parse_args(argv=None):
    p = argparse.ArgumentParser(description="ACAD1 投研卡事实草稿填充（只改 draft 卡）")
    p.add_argument("--pool-date", default=None, help="池日 YYYY-MM-DD，默认取最新池")
    p.add_argument("--limit", type=int, default=0, help="只处理前 N 张（样例用），0=全量")
    p.add_argument("--codes", default="", help="只处理指定代码，逗号分隔（样例用）")
    p.add_argument("--dry-run", action="store_true", help="只计算并打印，不写库")
    p.add_argument("--show", type=int, default=3, help="打印 N 张完整样例，默认 3")
    p.add_argument("--json-report", default="", help="把运行报告写到指定 JSON 路径")
    return p.parse_args(argv)


def _validate(rec: dict) -> list[str]:
    errs = []
    if rec["direction"] not in ALLOWED_DIRECTIONS:
        errs.append("direction 非法：%s" % rec["direction"])
    for f in ("thesis", "catalysts", "risks"):
        if not rec.get(f):
            errs.append("%s 为空" % f)
            continue
        for tok in FORBIDDEN_TOKENS:
            if tok in rec[f]:
                errs.append("%s 含禁用词 %s" % (f, tok))
    if rec["invalidation"] != INVALIDATION:
        errs.append("invalidation 未固定为“待 ACAD1 复核”")
    if rec["executable"] != "否":
        errs.append("executable 必须为“否”")
    if rec["review_note"] != REVIEW_NOTE:
        errs.append("review_note 不符合要求")
    if "自动建卡占位" in (rec.get("thesis") or ""):
        errs.append("thesis 仍是占位文本")
    return errs


def _sample_of(rec: dict, row: dict, bars: dict | None, pick: dict | None,
               sd: dict | None) -> dict:
    return {
        "pool_date": str(rec["pool_date"]),
        "strategy_id": rec["strategy_id"],
        "code": rec["code"],
        "market": row.get("market"),
        "signal": row.get("signal"),
        "resonance": _parse_selection_reason(row.get("selection_reason") or "")[1],
        "direction": rec["direction"],
        "score": float(rec["score"]) if rec["score"] is not None else None,
        "thesis": rec["thesis"],
        "catalysts": rec["catalysts"],
        "risks": rec["risks"],
        "invalidation": rec["invalidation"],
        "executable": rec["executable"],
        "exec_note": rec["exec_note"],
        "review_note": rec["review_note"],
        "data_date": str(rec["data_date"]),
        "facts_available": {
            "bar_date": str(bars["bar_date"]) if bars else None,
            "pick_report_date": str(pick["report_date"]) if pick else None,
            "strategy_daily": bool(sd),
        },
    }


def _render_summary(r: dict) -> str:
    lines = [
        "=" * 78,
        "[fill-acad1] pool_date=%s dry_run=%s" % (r["pool_date"], r["dry_run"]),
        "池内行数=%d  draft 卡数=%d  本次目标=%d  实际更新=%d  跳过非draft=%d" % (
            r["pool_rows"], r["draft_rows"], r["target_rows"], r["updated"],
            r["skipped_non_draft"]),
        "方向分布=%s" % json.dumps(r["direction_counts"], ensure_ascii=False,
                                    sort_keys=True),
        "缺失统计=%s" % json.dumps(r["missing_stats"], ensure_ascii=False,
                                    sort_keys=True),
        "填充后该池剩余 draft=%s（脚本只写事实字段，不置 reviewed，仍需 ACAD1 人工复核）" %
        r.get("remain_draft_after"),
    ]
    if r["errors"]:
        lines.append("校验失败=%d 例：%s" % (
            len(r["errors"]), json.dumps(r["errors"][:5], ensure_ascii=False)))
    for i, s in enumerate(r["samples"], 1):
        lines.append("-" * 78)
        lines.append("样例 %d：%s %s %s  signal=%s 共振=%s  direction=%s score=%s data_date=%s" % (
            i, s["market"], s["strategy_id"], s["code"], s["signal"],
            s["resonance"], s["direction"], s["score"], s["data_date"]))
        lines.append("  thesis     : " + s["thesis"])
        lines.append("  catalysts  : " + s["catalysts"])
        lines.append("  risks      : " + s["risks"])
        lines.append("  invalidation=%s  executable=%s" % (
            s["invalidation"], s["executable"]))
        lines.append("  exec_note  : " + s["exec_note"])
        lines.append("  review_note: " + s["review_note"])
        lines.append("  facts      : " + json.dumps(s["facts_available"],
                                                     ensure_ascii=False))
    lines.append("=" * 78)
    return "\n".join(lines)


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    args = parse_args(argv)
    engine = _engine()
    report: dict = {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "dry_run": bool(args.dry_run),
        "pool_date": None,
        "pool_rows": 0,
        "draft_rows": 0,
        "target_rows": 0,
        "updated": 0,
        "skipped_non_draft": 0,
        "errors": [],
        "direction_counts": {},
        "missing_stats": {},
        "samples": [],
    }

    with engine.begin() as conn:
        # 1) 池日
        pool_date = (date.fromisoformat(args.pool_date) if args.pool_date else
                     conn.execute(sa.text(
                         "SELECT max(pool_date) FROM v2_acad1_pool")).scalar())
        if not pool_date:
            print("[fill-acad1] 无 ACAD1 池数据，退出")
            return 2
        report["pool_date"] = pool_date.isoformat()

        # 2) 池 + 卡（只认 draft）
        pool_rows = conn.execute(sa.text(
            "SELECT pool_date, strategy_id, code, name, market, signal, score, "
            "selection_reason, status, data_date FROM v2_acad1_pool "
            "WHERE pool_date=:d ORDER BY market, code"), {"d": pool_date}
        ).mappings().all()
        cards = conn.execute(sa.text(
            "SELECT pool_date, strategy_id, code, name, status FROM v2_stock_research "
            "WHERE pool_date=:d"), {"d": pool_date}).mappings().all()
        report["pool_rows"] = len(pool_rows)
        report["draft_rows"] = sum(1 for c in cards if c["status"] == "draft")
        report["skipped_non_draft"] = len(cards) - report["draft_rows"]

        # 3) 事实预载
        strategies = load_strategies(conn)
        picks = load_latest_picks(conn)
        daily = load_strategy_daily(conn, pool_date)
        bars_by_code = load_bar_series(conn, pool_date)

        # 4) 目标卡筛选（只 draft；保持池顺序）
        draft_keys = {(c["pool_date"], c["strategy_id"], c["code"])
                      for c in cards if c["status"] == "draft"}
        targets = [r for r in pool_rows
                   if (r["pool_date"], r["strategy_id"], r["code"]) in draft_keys]
        if args.codes:
            wanted = {x.strip() for x in args.codes.split(",") if x.strip()}
            targets = [r for r in targets if r["code"] in wanted]
        if args.limit and args.limit > 0:
            targets = targets[:args.limit]
        report["target_rows"] = len(targets)

        # 5) 逐票生成事实文本 + 落库（UPDATE 带 status='draft' 双保险）
        dw_data = json.loads(DW_JSON.read_text(encoding="utf-8")) if DW_JSON.exists() else None
        missing = {"strategy_daily": 0, "bars": 0, "pick": 0, "pre_close": 0}
        for row in targets:
            sid, code = row["strategy_id"], row["code"]
            strategy = strategies.get(sid)
            bars = compute_bar_facts(bars_by_code.get((row["market"], code))
                                               or bars_by_code.get((row["market"], _bar_code(code))))
            sd = _find_strategy_daily(daily, sid, row["market"], code)
            pick = picks.get((sid, code))
            if not pick:
                # 共振票 pool.strategy_id 只是首选来源，按来源列表再找明细
                for alt in _parse_selection_reason(
                        row.get("selection_reason") or "")[0]:
                    pick = picks.get((alt, code))
                    if pick:
                        break

            if sd is None:
                missing["strategy_daily"] += 1
            if bars is None:
                missing["bars"] += 1
            if pick is None:
                missing["pick"] += 1
            if bars is not None and bars.get("prev_close_src") != "pre_close":
                missing["pre_close"] += 1

            resonance = _parse_selection_reason(
                row.get("selection_reason") or "")[1]
            direction, direction_reason = decide_direction(
                row.get("signal") or "", row.get("score"), resonance, bars)
            texts = build_texts(row, strategy, bars, sd, pick,
                                direction, direction_reason)
            rec = dict(
                pool_date=row["pool_date"],
                strategy_id=sid,
                code=code,
                name=row.get("name") or code,
                direction=direction,
                thesis=texts["thesis"],
                catalysts=texts["catalysts"],
                risks=texts["risks"],
                invalidation=INVALIDATION,
                executable="否",
                exec_note=EXEC_NOTE,
                review_note=REVIEW_NOTE,
                score=row.get("score"),
                data_date=row.get("data_date") or (bars["bar_date"] if bars
                                                   else pool_date),
            )
            # 张松数据仓只读核对（仅 CN 已接入；缺失不猜）
            if dw_data:
                dd = (dw_data.get("daily") or {}).get(code)
                bb = (dw_data.get("basic") or {}).get(code)
                st = code in set(dw_data.get("st_codes") or [])
                extra = []
                if dd:
                    extra.append("pre_close=%s/close=%s" % (dd.get("pre_close"), dd.get("close")))
                if bb:
                    extra.append("list_date=%s/industry=%s" % (bb.get("list_date"), bb.get("industry")))
                if st:
                    extra.append("ST=是")
                rec["exec_note"] = EXEC_NOTE + ("；数据仓核对：" + "，".join(extra) if extra else "；数据仓未命中")
                if st:
                    rec["risks"] = (rec.get("risks") or "") + "；数据仓 ST 标记：是，人工复核前需剔除。"
            else:
                rec["exec_note"] = EXEC_NOTE + "；数据仓未加载"
            # 上游原文可能含方向词；不写入卡内结论，做透明屏蔽
            for f in ("thesis", "catalysts", "risks"):
                for tok in FORBIDDEN_TOKENS:
                    if tok in (rec.get(f) or ""):
                        rec[f] = rec[f].replace(tok, "【上游方向词已屏蔽】")
                        rec["exec_note"] = (rec.get("exec_note") or "") + "；上游原文含方向词，已屏蔽待复核"
            errs = _validate(rec)
            if errs:
                report["errors"].append({"code": code, "errors": errs})
                continue
            report["direction_counts"][direction] = \
                report["direction_counts"].get(direction, 0) + 1

            if args.dry_run:
                if len(report["samples"]) < args.show:
                    report["samples"].append(
                        _sample_of(rec, row, bars, pick, sd))
                continue

            res = conn.execute(UPDATE_SQL, {
                "direction": rec["direction"], "thesis": rec["thesis"],
                "catalysts": rec["catalysts"], "risks": rec["risks"],
                "invalidation": rec["invalidation"],
                "executable": rec["executable"], "exec_note": rec["exec_note"],
                "review_note": rec["review_note"], "score": rec["score"],
                "name": rec["name"], "data_date": rec["data_date"],
                "pool_date": rec["pool_date"], "strategy_id": rec["strategy_id"],
                "code": rec["code"],
            })
            if res.rowcount:
                report["updated"] += 1
                if len(report["samples"]) < args.show:
                    report["samples"].append(
                        _sample_of(rec, row, bars, pick, sd))
        report["missing_stats"] = missing

        # 6) 复核：填充后仍应全部为 draft（等 ACAD1 人工复核）
        report["remain_draft_after"] = conn.execute(sa.text(
            "SELECT count(*) FROM v2_stock_research "
            "WHERE pool_date=:d AND status='draft'"), {"d": pool_date}).scalar()

    report["finished_at"] = datetime.now().isoformat(timespec="seconds")
    print(_render_summary(report))
    if args.json_report:
        p = Path(args.json_report)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str),
                     encoding="utf-8")
        print("[fill-acad1] json report ->", p)
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
