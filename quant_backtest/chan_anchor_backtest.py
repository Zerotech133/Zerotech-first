# -*- coding: utf-8 -*-
"""A3 缠论样板全历史回测锚 + 参数网格（BACK1 口径，Sprint A / 2026-10-19）。

依据：docs/plans/2026-10-19_选股策略独立化与选股Agent_M0计划.md
§五 Sprint A/A3 与 §三 G5（回测锚缺失）；范本 QPZM_RD_PLAN S4 /
qpzm_backtest.py（独立跑台 + 网格 + 报告三件套）。

做法：不新建回测引擎——复用产线 backtest_engine_v2.run_job，吃既有
事件缓存 sig_events_market_v3_20231010_20261013.json（market_scan
24 workers 362s 扫出，universe 5036 / 28111 事件 / 六类买卖点均衡）。
BASE 自愈沿用 bt2_tune_compare 判例：引擎硬编码 C:/项目/老田的AI助手，
本机 E: 无盘（不自愈则静默加载 0 只股票假空跑）。

窗口：20231010 → 20261013（约三年）。D0_MIN=20231010 为引擎数据诚实
上限（273 体检：因子湖 2023-07 起连续）；d1=20261013 为精确命中既有
v3 缓存的最大日期（数据湖个股覆盖至 20261016，末三日不入窗）。

网格轴（全部为产线 CLI 参数，无新概念）：
  buy_filter  3(引擎默认)/23(10-13 拍板)/2/all —— 交易哪些买点类型
  runup_cap   0.05(现行)/0.10/0.15   —— T+1 开盘跳空帽松紧
  gate_mode   off(现行)/high         —— regime 闸门
  min_score   0(现行)/80             —— 信号最低分
  stop_pct    0(现行)/0.05           —— 止损（缠论现无止损的对照臂）

base 锚 = 引擎 DEFAULTS 中 chanlun 相关默认值（gate off·min0·
runup 0.05·buy_filter 3·hist_st·limit_check），与 327 chan_base 臂同款；
不是 10-13 老田拍板的 buy_filter=23 口径，后者单列 buy23 臂。

口径声明（诚实边界，报告须原样携带）：
· 纸面回测：T 收盘确认 → T+1 开盘买入（runup 帽），往返成本 0.15%，
  不止损除非显式配置；理论价非可成交价，结果只答「参数有没有数据支撑」，
  不构成实盘依据。
· 事件缓存为 2026-10-13/14 扫描产物，非本次重扫；缓存新鲜度问题由
  market_scan --force 重扫解决，本报告不回答信号新鲜度。
· 窗口受数据湖覆盖约束（个股 2023-04 起、指数修复至 2026-10-16），
  是「数据可证范围的全历史」，非「有史以来」。

用法：
  python chan_anchor_backtest.py                 # 跑全网格（幂等：存在即跳过）
  python chan_anchor_backtest.py --only base     # 只跑指定 tag（逗号分隔）
  python chan_anchor_backtest.py --force         # 覆盖重跑

产物：reports/chan_anchor_report.json（机器可读，含每臂 summary+meta）
      reports/chan_anchor_summary.md（人类可读对比表）
"""

import argparse
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import backtest_engine_v2 as eng  # noqa: E402


# ── BASE 自愈（bt2_tune_compare 判例；引擎硬编码 E: 本机无盘） ──────────
def _selfheal_base() -> bool:
    if os.path.isdir(eng.BASE):
        return False
    base = os.path.dirname(_HERE)  # 站根 C:/项目/老田的AI助手
    eng.BASE = base
    eng.DATA = os.path.join(base, "quant_backtest", "data", "ashare")
    eng.POOL_SRC = os.path.join(
        base, "k_stock", "data", "poc_adapter_chanlun_20261012.json"
    )
    eng.LEDGER = os.path.join(
        base, "quant_backtest", "data", "daily_ledger", "ledger_20251013_20261012.json"
    )
    eng.OUT_DIR = os.path.join(base, "quant_backtest", "data", "bt2")
    eng.MINUTE_OUT_DIR = os.path.join(base, "quant_backtest", "data", "minute_exec")
    # 非 market 路径的模块级缓存路径同源修正（防呆，本跑台走 market 不触发）
    sig_dir = os.path.join(base, "quant_backtest", "data", "sig_events")
    eng.SIG_EVENTS_CACHE = os.path.join(sig_dir, "sig_events_%s_%s.json")
    return True


_SELFHEALED = _selfheal_base()

# ── 窗口与网格定义 ───────────────────────────────────────────────────────
D0, D1 = "20231010", "20261013"  # 约三年；d1 精确命中既有 v3 缓存

# 现行参数 = 引擎 DEFAULTS（chanlun 分支默认值即 327 chan_base 同款口径）
BASE_PARAMS = {
    "strategy": "chanlun",
    "d0": D0,
    "d1": D1,
    "universe": "market",
    "gate_mode": "off",
    "min_score": 0,
    "runup_cap": 0.05,
    "buy_filter": "3",
    "hist_st": True,
    "limit_check": True,
}

# (tag, 中文说明, 相对 BASE_PARAMS 的覆盖)
GRID = [
    ("base", "引擎默认锚（gate off·min0·runup.05·bf3·无止损）", {}),
    ("buy23", "仅二买+三买（10-13 老田拍板口径，一买不入选）", {"buy_filter": "23"}),
    ("buy2", "仅二买", {"buy_filter": "2"}),
    ("buyall", "全买点类型（buy_filter=all，1/2/3 买都交易）", {"buy_filter": "all"}),
    ("runup10", "T+1 跳空帽放宽到 10%", {"runup_cap": 0.10}),
    ("runup15", "T+1 跳空帽放宽到 15%", {"runup_cap": 0.15}),
    ("gatehigh", "regime 闸门 high（低位市停手）", {"gate_mode": "high"}),
    ("min80", "信号最低分 80", {"min_score": 80}),
    (
        "gatehigh_min80",
        "闸门 high + 最低分 80（327 chan_final 口径）",
        {"gate_mode": "high", "min_score": 80},
    ),
    ("stop5", "止损 5%（对照臂：缠论现无止损）", {"stop_pct": 0.05}),
]

REPORT_DIR = os.path.join(_HERE, "reports")
REPORT_JSON = os.path.join(REPORT_DIR, "chan_anchor_report.json")
REPORT_MD = os.path.join(REPORT_DIR, "chan_anchor_summary.md")

# summary 中进对比表的字段（顺序即表列）
_TABLE_FIELDS = [
    ("total_return_pct", "总收益%"),
    ("annualized_pct", "年化%"),
    ("max_drawdown_pct", "回撤%"),
    ("profit_factor", "PF"),
    ("win_rate_pct", "胜率%"),
    ("n_trades", "笔数"),
    ("avg_invested_pct", "平均仓位%"),
]


def _run_one(tag: str, desc: str, over: dict) -> dict:
    """跑单臂：幂等（bt2_a3_<tag>.json 存在即跳过，--force 覆盖）。"""
    params = dict(BASE_PARAMS)
    params.update(over)
    job_id = "a3_%s" % tag
    out_fp = os.path.join(eng.OUT_DIR, "bt2_%s.json" % job_id)
    if os.path.exists(out_fp) and not FORCE:
        try:
            with open(out_fp, encoding="utf-8") as f:
                d = json.load(f)
            meta = d.get("meta") or {}
            cfg = meta.get("config") or {}
            expected = dict(eng.DEFAULTS)
            # 2026-11-10：引擎默认预设改为 v2，本脚本的历史产物是按旧 DEFAULTS 生成的，
            # 校验时必须钉死旧口径，否则 recache 门会误判「配置不一致」。
            expected.setdefault("preset", "none")
            expected.update(params)
            for key in (
                "d0",
                "d1",
                "buy_filter",
                "gate_mode",
                "min_score",
                "runup_cap",
                "stop_pct",
                "universe",
            ):
                actual = (
                    cfg.get(key)
                    if key in ("d0", "d1", "runup_cap", "stop_pct")
                    else meta.get(key)
                )
                if str(actual) != str(expected[key]):
                    raise ValueError(
                        "%s 既有产物参数不匹配: %r != %r" % (key, actual, expected[key])
                    )
            if not isinstance(d.get("summary"), dict):
                raise ValueError("既有产物缺 summary")
            return {
                "tag": tag,
                "desc": desc,
                "over": over,
                "status": "skipped_exists",
                "summary": d["summary"],
                "meta": {
                    k: d.get("meta", {}).get(k)
                    for k in (
                        "window",
                        "signal_source",
                        "buy_filter",
                        "gate_mode",
                        "min_score",
                        "universe",
                        "hist_st",
                        "limit_check",
                    )
                },
            }
        except Exception as exc:  # noqa: BLE001
            print("[a3] 既有产物损坏，重跑 %s: %s" % (tag, exc))
    t0 = time.time()
    res, err = eng.run_job(params, job_id)
    if err or not res:
        return {
            "tag": tag,
            "desc": desc,
            "over": over,
            "status": "error",
            "error": (err or "")[:400],
            "seconds": round(time.time() - t0, 1),
        }
    return {
        "tag": tag,
        "desc": desc,
        "over": over,
        "status": "ok",
        "summary": res.get("summary"),
        "meta": {
            k: res.get("meta", {}).get(k)
            for k in (
                "window",
                "signal_source",
                "buy_filter",
                "gate_mode",
                "min_score",
                "universe",
                "hist_st",
                "limit_check",
            )
        },
        "seconds": round(time.time() - t0, 1),
    }


def _fmt(v, nd=2):
    if v is None:
        return "-"
    if isinstance(v, float):
        return ("%." + str(nd) + "f") % v
    return str(v)


def _write_reports(arms: list) -> None:
    os.makedirs(REPORT_DIR, exist_ok=True)
    payload = {
        "plan": "docs/plans/2026-10-19_选股策略独立化与选股Agent_M0计划.md",
        "sprint": "A / A3 样板全历史回测锚（BACK1 口径）",
        "window": {
            "d0": D0,
            "d1": D1,
            "note": "约三年；d1 精确命中 v3 事件缓存；D0_MIN 数据诚实上限",
        },
        "base_selfhealed": _SELFHEALED,
        "base_params": BASE_PARAMS,
        "grid_axes": ["buy_filter", "runup_cap", "gate_mode", "min_score", "stop_pct"],
        "caveats": [
            "纸面回测：T 收盘确认→T+1 开盘买入（runup 帽），往返成本 0.15%",
            "事件缓存为 2026-10-13/14 扫描产物，非本次重扫",
            "窗口为数据可证范围（个股 2023-04 起），非有史以来全历史",
        ],
        "arms": arms,
    }
    with open(REPORT_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    lines = [
        "# 缠论样板全历史回测锚 · 参数网格（A3 / BACK1 口径）",
        "",
        "- 窗口：%s → %s（约三年；D0_MIN=20231010 数据诚实上限）" % (D0, D1),
        "- base = 引擎 DEFAULTS 的 buy_filter=3（327 chan_base 同款）；"
        "10-13 拍板的 buy_filter=23 单列 buy23；BASE 自愈：%s"
        % ("是（E: 无盘→本仓）" if _SELFHEALED else "否"),
        "- 事件缓存：sig_events_market_v3_%s_%s.json（universe 5036 / "
        "28111 事件）" % (D0, D1),
        "- 口径：纸面回测 T+1 开盘买入、往返成本 0.15%、缓存非本次重扫、"
        "窗口为数据可证范围",
        "",
        "| 臂 | 说明 | " + " | ".join(t for _, t in _TABLE_FIELDS) + " |",
        "|" + "---|" * (2 + len(_TABLE_FIELDS)),
    ]
    for a in arms:
        s = a.get("summary") or {}
        cells = [_fmt(s.get(k), 1 if "pct" in k else 2) for k, _ in _TABLE_FIELDS]
        lines.append("| %s | %s | %s |" % (a["tag"], a["desc"], " | ".join(cells)))
    by_tag = {a["tag"]: a for a in arms}
    if "base" in by_tag and "buy23" in by_tag:
        base = by_tag["base"].get("summary") or {}
        approved = by_tag["buy23"].get("summary") or {}
        lines += [
            "",
            "## 核验与结论",
            "",
            "- 引擎默认（仅三买）与 10-13 拍板（二买+三买）不是同一基线；"
            "必须分开列示，不能把默认值误称生产现行口径。",
            "- 同窗引擎默认：总收益 %s%%、%s 笔；拍板 buy23：总收益 %s%%、%s 笔。"
            % (
                _fmt(base.get("total_return_pct")),
                base.get("n_trades"),
                _fmt(approved.get("total_return_pct")),
                approved.get("n_trades"),
            ),
            "- buy2 仅 28 笔；buyall 的交易笔数反而少于 base，"
            "需核对买点过滤与入场竞争/仓位机制；不得按买点做可加性"
            "归因，也不得把样本内收益最优当可迁移增益。",
            "- 门②前置：冻结评审口径，审计逐日信息时点/历史 ST 过滤与"
            "成本，做时间样本外重放和容量检查；未通过不得影子合龙。",
            "",
        ]
    else:
        lines += [
            "",
            "## 状态",
            "",
            "- 单臂调试报告：缺 base 或 buy23，不可作为 A3 全网格结论。",
            "",
        ]
    with open(REPORT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main():
    global FORCE
    ap = argparse.ArgumentParser(description="A3 缠论回测锚网格跑台")
    ap.add_argument("--only", default="", help="只跑指定 tag（逗号分隔），缺省全网格")
    ap.add_argument(
        "--force", action="store_true", help="覆盖重跑（缺省幂等跳过既有产物）"
    )
    args = ap.parse_args()
    FORCE = args.force
    only = set(t.strip() for t in args.only.split(",") if t.strip())

    arms = []
    for tag, desc, over in GRID:
        if only and tag not in only:
            continue
        a = _run_one(tag, desc, over)
        arms.append(a)
        st = a.get("status")
        if st == "ok":
            s = a["summary"]
            print(
                "[a3] %-16s ret=%s%% PF=%s trades=%s（%ss）"
                % (
                    tag,
                    s.get("total_return_pct"),
                    s.get("profit_factor"),
                    s.get("n_trades"),
                    a.get("seconds"),
                ),
                flush=True,
            )
        elif st == "skipped_exists":
            print("[a3] %-16s skip（既有产物）" % tag, flush=True)
        else:
            print(
                "[a3] %-16s ERROR %s" % (tag, (a.get("error") or "")[:200]), flush=True
            )
    _write_reports(arms)
    print("[a3] 报告已写：%s / %s" % (REPORT_JSON, REPORT_MD), flush=True)


if __name__ == "__main__":
    main()
