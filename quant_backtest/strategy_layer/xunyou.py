# -*- coding: utf-8 -*-
r"""策略研究 Agent「荀攸」（公达）盘后作业运行器（2026-10-19 老田口谕定立）。

角色章程（deliverables/量化策略研究Agent_荀攸_角色章程_20261019.md）：
- 量化交易部门下属独立策略线；唯一上行 = 量化交易部门老大；
  **不直连飞书**——被调用/主动产出一律先交上级分发。
- 每日盘后作业（大量工作集中于此）；次日盘前 08:00 前股票池就绪。

本运行器职责（v1，M0 门②③未过，全部产出为研究产物）：
  1. 复用 SignalAggregator.run(trade_date)（与 run_daily.py 同一融合链，
     含方案 C 同票预聚合与 PG 权威替换写库）；
  2. 从融合结果裁决股票池：action=buy 按 score 降序；分档按 2026-10-19
     老田拍板落地（T1=告警档 / T2=人工关注档 / 分歧票人工复核，
     详见 deliverables/荀攸T1T2分档阈值研究_拍板备忘_20261019.md）；
  3. 池与汇报信原子落盘 reports/xunyou_pool_{date}.json /
     reports/xunyou_report_{date}.md；汇报信即"给上级的信"，不对外直发；
  4. 全程标注 RESEARCH_ONLY_NOT_PRODUCTION + BLOCK_CUTOVER。

用法：
    python -m strategy_layer.xunyou            # 当日
    python -m strategy_layer.xunyou 20261017   # 指定日（补跑/回放）
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
_QUANT_BT = os.path.dirname(_HERE)
if _QUANT_BT not in sys.path:
    sys.path.insert(0, _QUANT_BT)

from strategy_layer import Registry, bootstrap_default_adapters  # noqa: E402
from strategy_layer.aggregator import SignalAggregator  # noqa: E402

# N1 universe 休眠开关（默认 False → build_pool 不改变任何行为；
# 老田在 N1 门翻开关后才真正过滤样本域）。
from strategy_layer.liudu_universe import LIUDU_UNIVERSE_ENABLED  # noqa: E402

AGENT_NAME = "荀攸（公达）"
REPORT_DIR = os.path.join(_QUANT_BT, "reports")
VERDICT = "BLOCK_CUTOVER"
SCOPE = "RESEARCH_ONLY_NOT_PRODUCTION"

# ── T1/T2 分档常量（2026-10-19 老田三项拍板，勿擅自调整）─────────────
# 依据：deliverables/荀攸T1T2分档阈值研究_拍板备忘_20261019.md（WS-R1 方案一
# 「天然断层双断点」+ 分位封顶旋钮）。用途定义：T1/T2 = 告警档——T1 触发
# 盯盘/提醒（告警量是硬约束，故设排名封顶），T2 供人工关注；
# divergence=true 一律人工复核、不进任何档。
# T1 触发提醒的接线（量化交易部门上行链）另行设计，本模块只落分档数据。
T1_SCORE_MIN = 99.0  # T1 下沿：score > 99.0（开区间）；99.0→99.17 微断层 = kg_boost/chan_monitor 两套量纲分界
T1_RANK_CAP = (
    20  # T1 当日排名封顶：按 score 降序 1 起计，排名 ≤ 20 才入 T1（防告警量爆炸）
)
T2_SCORE_MIN = (
    95.0  # T2 下沿：95 ≤ score ≤ 99.0（95→97 断层收下沿；上沿与 T1 下沿衔接）
)


def assign_tiers(rows: list) -> list:
    """为 buy 候选标注 tier（纯函数：返回带 tier 的新 dict 列表，不改入参）。

    规则（拍板备忘方案一 + 封顶旋钮，常量见模块头注释）：
      T1 = score > T1_SCORE_MIN 且当日排名 ≤ T1_RANK_CAP
           （排名按 score 降序、1 起计；分歧票先旁路，不占排名额度）；
      T2 = T2_SCORE_MIN ≤ score ≤ T1_SCORE_MIN；
      其余 tier=""（不入档）；
      divergence=true 强制 tier="" 且 review_required=True（人工复核，
      不进任何档）。
    """

    def _score(row):
        try:
            return float(row.get("score", 0.0))
        except (TypeError, ValueError):
            return float("-inf")

    def _divergent(row):
        v = row.get("divergence")
        return v is True or v == "true"

    eligible = sorted(
        ((i, _score(r)) for i, r in enumerate(rows) if not _divergent(r)),
        key=lambda t: (-t[1], t[0]),
    )
    t1_idx = {
        i
        for rank, (i, sc) in enumerate(eligible, 1)
        if rank <= T1_RANK_CAP and sc > T1_SCORE_MIN
    }
    out = []
    for i, row in enumerate(rows):
        item = dict(row)
        sc = _score(row)
        if _divergent(row):
            item["tier"] = ""
            item["review_required"] = True  # 分歧票：一律人工复核，不进任何档
        elif i in t1_idx:
            item["tier"] = "T1"
        elif T2_SCORE_MIN <= sc <= T1_SCORE_MIN:
            item["tier"] = "T2"
        else:
            item["tier"] = ""  # 不入档（含 score>99.0 但超排名封顶的票）
        out.append(item)
    return out


def build_pool(trade_date: str, combined: list, divergences: list) -> dict:
    """融合结果 → 荀攸股票池（buy 按 score 降序，并按拍板口径标注 tier）。"""
    buys = sorted(
        (row for row in combined if row.get("action") == "buy"),
        key=lambda r: (-float(r.get("score", 0.0)), str(r.get("symbol", ""))),
    )
    # ── N1 universe 过滤（休眠默认：LIUDU_UNIVERSE_ENABLED=False → 不生效）──
    # 老田已拍板 universe=market；开关打开前本调用不执行，行为完全一致。
    # 激活后：仅保留 A 股，从样本层面消除外源 ticker 污染（见 N3/STRAT1 证据）。
    removed_by_universe = []
    if LIUDU_UNIVERSE_ENABLED:
        from strategy_layer.liudu_universe import apply_universe_filter

        buys, removed_by_universe = apply_universe_filter(buys)
    tiered = assign_tiers(buys)
    superseded_total = sum(
        len(c.get("superseded", ()))
        for row in combined
        for c in (row.get("contributions") or {}).values()
        if isinstance(c, dict)
    )
    t1 = [r for r in tiered if r["tier"] == "T1"]
    t2 = [r for r in tiered if r["tier"] == "T2"]
    review = [r for r in tiered if r.get("review_required")]
    return {
        "agent": AGENT_NAME,
        "trade_date": trade_date,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "scope": SCOPE,
        "cutover_verdict": VERDICT,
        "pool": [
            {
                "symbol": r["symbol"],
                "action": r["action"],
                "score": r["score"],
                "confidence": r["confidence"],
                "divergence": r["divergence"],
                "rationale": r["rationale"],
                "contributions": r["contributions"],
                "tier": r["tier"],
                **({"review_required": True} if r.get("review_required") else {}),
            }
            for r in tiered
        ],
        "stats": {
            "combined_total": len(combined),
            "buy_total": len(buys),
            "divergence_total": len(divergences),
            "superseded_total": superseded_total,
            "tier1_total": len(t1),
            "tier2_total": len(t2),
            "review_total": len(review),
        },
        "notes": _build_pool_notes(removed_by_universe),
    }


def _build_pool_notes(removed_by_universe: list) -> list:
    notes = [
        "分档按 2026-10-19 老田拍板落地：T1=score>99.0 且当日排名≤20"
        "（告警档，触发盯盘/提醒）；T2=95≤score≤99.0（人工关注档）；"
        "divergence=true 一律人工复核、不进任何档"
        "（依据 deliverables/荀攸T1T2分档阈值研究_拍板备忘_20261019.md）",
        "确认门②③未过：不切线上 picks、不改权重、不触交易",
    ]
    if removed_by_universe:
        notes.append(
            "N1 universe=market 已生效：剔除非A股/北交所 %d 只（样本层面防污染）"
            % len(removed_by_universe)
        )
    return notes


def write_upstream_report(
    trade_date: str, pool: dict, enabled: list, a4: dict | None = None
) -> str:
    """给量化交易部门老大的汇报信（落盘返回路径；不直连飞书）。

    纪律（章程）：本函数只负责落盘 reports/xunyou_report_{date}.md 并返回
    路径，产物先呈上级（量化交易部门），**绝不直连飞书**；对外分发属上级
    的接线职责，不在本函数（也不在本工位）实现。
    """
    lines = [
        "# 荀攸盘后汇报（%s）" % trade_date,
        "",
        "- 致：量化交易部门老大（单一上行路，不直连飞书）",
        "- 生成：%s ｜ 性质：%s ｜ %s" % (pool["generated_at"], SCOPE, VERDICT),
        "- 启用策略：%s" % ", ".join(enabled) if enabled else "- 启用策略：无",
        "",
        "## 股票池摘要",
        "- 融合 %d 条；buy 候选 **%d** 只（按 score 降序，已按拍板口径分档）"
        % (pool["stats"]["combined_total"], pool["stats"]["buy_total"]),
        "- 分歧标的 %d；同票预聚合覆盖明细 %d 条（可回放）"
        % (pool["stats"]["divergence_total"], pool["stats"]["superseded_total"]),
        "",
    ]
    tiered_rows = pool.get("pool") or []
    t1_rows = [r for r in tiered_rows if r.get("tier") == "T1"]
    t2_rows = [r for r in tiered_rows if r.get("tier") == "T2"]
    review_rows = [
        r
        for r in tiered_rows
        if r.get("review_required")
        or r.get("divergence") is True
        or r.get("divergence") == "true"
    ]
    lines += [
        "## 分层陈述（2026-10-19 拍板口径）",
        "- T1（告警档：score>99.0 且当日排名≤20）**%d** 条：%s"
        % (len(t1_rows), "、".join(str(r.get("symbol")) for r in t1_rows) or "（无）"),
        "- T2（人工关注档：95≤score≤99.0）**%d** 条：%s"
        % (len(t2_rows), "、".join(str(r.get("symbol")) for r in t2_rows) or "（无）"),
        "- 人工复核（分歧票 divergence=true，不进任何档）**%d** 只：%s"
        % (
            len(review_rows),
            "、".join(str(r.get("symbol")) for r in review_rows) or "（无）",
        ),
        "- T1 触发提醒的接线走量化交易部门上行链（另行设计）；本信只呈"
        "分档数据，不直连飞书",
        "",
    ]
    for i, row in enumerate(pool["pool"][:10], 1):
        lines.append(
            "%d. %s score=%.1f conf=%.2f | %s"
            % (i, row["symbol"], row["score"], row["confidence"], row["rationale"][:70])
        )
    if len(pool["pool"]) > 10:
        lines.append("…（其余 %d 只见池文件）" % (len(pool["pool"]) - 10))
    lines += [
        "",
        "## 阻断与待办",
        "- 确认门②③未过：本池为研究产物，非生产 picks，不构成交易指令",
        "- 剩余待拍板：必到市场清单 / ST 口径 / 止损契约",
        "- 数据侧：板块仓 881019 停 10-12（远端源头停更，恢复待批）",
        "",
    ]
    if a4 is not None:
        lines += ["## 当日 A4 只读对账"]
        if a4.get("status") == "OK":
            lines += [
                "- 归档状态 %s ｜ A股信号 %s / 标的 %s"
                % (a4["history_status"], a4["signals_a"], a4["unique_a"]),
                "- 可比性：%s" % a4["comparability"],
                "- 对账原因：%s" % ("；".join(a4["reasons"]) or "（无）"),
                "- 链路 issues：%s" % ("；".join(a4["issues"]) or "（无）"),
                "- 结论：%s（观察非授权）" % a4["cutover_verdict"],
            ]
        else:
            lines += ["- 对账降级：%s" % a4.get("reason", "unknown")]
        lines.append("")
    path = os.path.join(REPORT_DIR, "xunyou_report_%s.md" % trade_date)
    _atomic_write(path, "\n".join(lines).encode("utf-8"))
    return path


def _atomic_write(path: str, data: bytes) -> None:
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


def collect_a4_comparison(trade_date: str) -> dict:
    """A4 只读对账并入盘后作业（章程"证据对账"职责）。

    调用现有探针 inspect + 内存对账契约，仅取最小摘要进汇报信；
    任何异常降级为 DEGRADED 不阻断池产出。只读，无 PG 写入、无切流。
    """
    try:
        from strategy_layer.a4_readonly_probe import inspect as a4_inspect
        from strategy_layer.a4_comparison_contract import evaluate_comparison

        observation = a4_inspect(trade_date)
        comparison = evaluate_comparison(observation)
        return {
            "status": "OK",
            "history_status": observation["history"]["status"],
            "signals_a": observation["signals_a"],
            "unique_a": observation["unique_a"],
            "comparability": comparison["observation_comparability"],
            "reasons": comparison.get("reasons", [])[:8],
            "issues": observation.get("issues", [])[:8],
            "cutover_verdict": "BLOCK_CUTOVER",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "DEGRADED",
            "reason": "%s: %s" % (type(exc).__name__, str(exc)[:120]),
        }


# ── G4 只读工具集：读证据链 / 读后验 ──────────────────────────────────
def _evidence_read_store(db_path: str | None = None):
    """打开只读证据链库（file:?mode=ro，不建表、不写 WAL）。"""
    import sqlite3

    import evidence_chain as _ec

    store = _ec.EvidenceChainStore(db_path or _ec.DEFAULT_DB_PATH, read_only=True)
    # read_only 分支未设 row_factory（不改 evidence_chain.py，仅连接级设置）
    store._con.row_factory = sqlite3.Row
    return store


def read_evidence_state(
    trade_date: str | None = None,
    state: str | None = None,
    symbol: str | None = None,
    limit: int = 200,
    db_path: str | None = None,
) -> dict:
    """【只读工具】读证据链：当前态投影 STATE 查询（evidence_chain 查询面）。

    纪律：荀攸不写证据链——read_only 连接打开，绝不调用 transition/
    rebuild_state；任何异常降级 DEGRADED 不阻断池产出。
    """
    try:
        store = _evidence_read_store(db_path)
        try:
            rows = store.query_state(
                trade_date=trade_date, state=state, symbol=symbol, limit=limit
            )
        finally:
            store.close()
        return {"status": "OK", "count": len(rows), "states": rows}
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "DEGRADED",
            "reason": "%s: %s" % (type(exc).__name__, str(exc)[:120]),
        }


def replay_evidence_entry(entry_id: str, db_path: str | None = None) -> dict:
    """【只读工具】读证据链：单条目事件回放（入池→授权→出池→结果全链）。

    纪律：只读 get_history/get_state 查询面，不写入。
    """
    try:
        store = _evidence_read_store(db_path)
        try:
            return {
                "status": "OK",
                "entry_id": entry_id,
                "state": store.get_state(entry_id),
                "events": store.get_history(entry_id),
            }
        finally:
            store.close()
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "DEGRADED",
            "reason": "%s: %s" % (type(exc).__name__, str(exc)[:120]),
        }


def read_posterior(out_path: str | None = None) -> dict:
    """【只读工具】读后验：取 combined_verdicts.py 的落盘统计输出。

    只读 portal/data/combined_verdicts.json（by_strategy / by_weight_group
    / by_shadow 等分层统计），**不重算**（build()/load_rows_pg 等计算与
    PG 读取路径一律不触发）、不写任何文件；输出缺失/损坏降级 DEGRADED。
    """
    import json as _json

    from strategy_layer.combined_verdicts import OUT_DEFAULT

    path = out_path or OUT_DEFAULT
    try:
        with open(path, encoding="utf-8") as fh:
            doc = _json.load(fh)
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "DEGRADED",
            "reason": "%s: %s" % (type(exc).__name__, str(exc)[:120]),
        }
    summary = doc.get("summary") if isinstance(doc, dict) else None
    if not isinstance(summary, dict):
        return {"status": "DEGRADED", "reason": "统计输出缺 summary 块"}
    for key in ("windows", "by_strategy", "by_weight_group", "by_shadow"):
        if key in summary and not isinstance(summary[key], dict):
            return {
                "status": "DEGRADED",
                "reason": "统计输出 summary.%s 类型错误" % key,
            }
    return {
        "status": "OK",
        "out_path": path,
        "asof": summary.get("asof", ""),
        "windows": summary.get("windows", {}),
        "by_strategy": summary.get("by_strategy", {}),
        "by_weight_group": summary.get("by_weight_group", {}),
        "by_shadow": summary.get("by_shadow", {}),
    }


def main(argv: list) -> int:
    trade_date = argv[1] if len(argv) > 1 else datetime.now().strftime("%Y%m%d")
    try:
        registry = bootstrap_default_adapters(Registry())
    except Exception as exc:  # noqa: BLE001
        print("[xunyou][FATAL] 注册引导失败: %s" % exc)
        traceback.print_exc()
        return 1
    enabled = registry.enabled_ids()
    print("[xunyou] %s %s 启用策略=%s" % (AGENT_NAME, trade_date, enabled))

    aggregator = SignalAggregator(registry=registry)
    result = aggregator.run(trade_date)

    pool = build_pool(trade_date, result.combined, result.divergences)
    a4 = collect_a4_comparison(trade_date)
    os.makedirs(REPORT_DIR, exist_ok=True)
    pool_path = os.path.join(REPORT_DIR, "xunyou_pool_%s.json" % trade_date)
    _atomic_write(
        pool_path, json.dumps(pool, ensure_ascii=False, indent=2).encode("utf-8")
    )
    report_path = write_upstream_report(trade_date, pool, enabled, a4=a4)
    print("[xunyou] 股票池 %d 只（buy）→ %s" % (pool["stats"]["buy_total"], pool_path))
    print("[xunyou] 汇报信已呈上级（不直连飞书）→ %s" % report_path)
    print(
        "[xunyou] %s ｜ 分歧 %d ｜ 覆盖明细 %d ｜ A4对账 %s"
        % (
            VERDICT,
            pool["stats"]["divergence_total"],
            pool["stats"]["superseded_total"],
            a4.get("status"),
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
