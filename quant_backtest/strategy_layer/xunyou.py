# -*- coding: utf-8 -*-
r"""策略研究 Agent「荀攸」（公达）盘后作业运行器（2026-10-19 老田口谕定立）。

角色章程（deliverables/量化策略研究Agent_荀攸_角色章程_20261019.md）：
- 量化交易部门下属独立策略线；唯一上行 = 量化交易部门老大；
  **不直连飞书**——被调用/主动产出一律先交上级分发。
- 每日盘后作业（大量工作集中于此）；次日盘前 08:00 前股票池就绪。

本运行器职责（v1，M0 门②③未过，全部产出为研究产物）：
  1. 复用 SignalAggregator.run(trade_date)（与 run_daily.py 同一融合链，
     含方案 C 同票预聚合与 PG 权威替换写库）；
  2. 从融合结果裁决股票池：action=buy 按 score 降序；**不发明分档阈值**
     （T1/T2 等分级待拍板，池中不做分级）；
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

AGENT_NAME = "荀攸（公达）"
REPORT_DIR = os.path.join(_QUANT_BT, "reports")
VERDICT = "BLOCK_CUTOVER"
SCOPE = "RESEARCH_ONLY_NOT_PRODUCTION"


def build_pool(trade_date: str, combined: list, divergences: list) -> dict:
    """融合结果 → 荀攸股票池（研究产物，buy 按 score 降序，不分档）。"""
    buys = sorted(
        (row for row in combined if row.get("action") == "buy"),
        key=lambda r: (-float(r.get("score", 0.0)),
                       str(r.get("symbol", ""))))
    superseded_total = sum(
        len(c.get("superseded", ()))
        for row in combined
        for c in (row.get("contributions") or {}).values()
        if isinstance(c, dict))
    return {
        "agent": AGENT_NAME,
        "trade_date": trade_date,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "scope": SCOPE,
        "cutover_verdict": VERDICT,
        "pool": [
            {"symbol": r["symbol"], "action": r["action"],
             "score": r["score"], "confidence": r["confidence"],
             "divergence": r["divergence"], "rationale": r["rationale"],
             "contributions": r["contributions"]}
            for r in buys],
        "stats": {
            "combined_total": len(combined),
            "buy_total": len(buys),
            "divergence_total": len(divergences),
            "superseded_total": superseded_total,
        },
        "notes": [
            "分档阈值（T1/T2 等）未拍板，池内不做分级",
            "确认门②③未过：不切线上 picks、不改权重、不触交易",
        ],
    }


def write_upstream_report(trade_date: str, pool: dict,
                          enabled: list, a4: dict | None = None) -> str:
    """给量化交易部门老大的汇报信（落盘返回路径；不直连飞书）。

    纪律（章程）：本函数只负责落盘 reports/xunyou_report_{date}.md 并返回
    路径，产物先呈上级（量化交易部门），**绝不直连飞书**；对外分发属上级
    的接线职责，不在本函数（也不在本工位）实现。
    """
    lines = [
        "# 荀攸盘后汇报（%s）" % trade_date,
        "",
        "- 致：量化交易部门老大（单一上行路，不直连飞书）",
        "- 生成：%s ｜ 性质：%s ｜ %s"
        % (pool["generated_at"], SCOPE, VERDICT),
        "- 启用策略：%s" % ", ".join(enabled) if enabled else "- 启用策略：无",
        "",
        "## 股票池摘要",
        "- 融合 %d 条；buy 候选 **%d** 只（按 score 降序，未分级）"
        % (pool["stats"]["combined_total"], pool["stats"]["buy_total"]),
        "- 分歧标的 %d；同票预聚合覆盖明细 %d 条（可回放）"
        % (pool["stats"]["divergence_total"],
           pool["stats"]["superseded_total"]),
        "",
    ]
    for i, row in enumerate(pool["pool"][:10], 1):
        lines.append("%d. %s score=%.1f conf=%.2f | %s"
                     % (i, row["symbol"], row["score"],
                        row["confidence"], row["rationale"][:70]))
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
    except Exception as exc:  # noqa: BLE001 — 对账降级不阻断池产出
        return {"status": "DEGRADED", "reason": "%s: %s"
                % (type(exc).__name__, str(exc)[:120])}


# ── G4 只读工具集：读证据链 / 读后验 ──────────────────────────────────
def _evidence_read_store(db_path: str | None = None):
    """打开只读证据链库（file:?mode=ro，不建表、不写 WAL）。"""
    import sqlite3

    import evidence_chain as _ec
    store = _ec.EvidenceChainStore(db_path or _ec.DEFAULT_DB_PATH,
                                   read_only=True)
    # read_only 分支未设 row_factory（不改 evidence_chain.py，仅连接级设置）
    store._con.row_factory = sqlite3.Row
    return store


def read_evidence_state(trade_date: str | None = None,
                        state: str | None = None,
                        symbol: str | None = None,
                        limit: int = 200,
                        db_path: str | None = None) -> dict:
    """【只读工具】读证据链：当前态投影 STATE 查询（evidence_chain 查询面）。

    纪律：荀攸不写证据链——read_only 连接打开，绝不调用 transition/
    rebuild_state；任何异常降级 DEGRADED 不阻断池产出。
    """
    try:
        store = _evidence_read_store(db_path)
        try:
            rows = store.query_state(trade_date=trade_date, state=state,
                                     symbol=symbol, limit=limit)
        finally:
            store.close()
        return {"status": "OK", "count": len(rows), "states": rows}
    except Exception as exc:  # noqa: BLE001 — 降级不阻断池产出
        return {"status": "DEGRADED", "reason": "%s: %s"
                % (type(exc).__name__, str(exc)[:120])}


def replay_evidence_entry(entry_id: str,
                          db_path: str | None = None) -> dict:
    """【只读工具】读证据链：单条目事件回放（入池→授权→出池→结果全链）。

    纪律：只读 get_history/get_state 查询面，不写入。
    """
    try:
        store = _evidence_read_store(db_path)
        try:
            return {"status": "OK",
                    "entry_id": entry_id,
                    "state": store.get_state(entry_id),
                    "events": store.get_history(entry_id)}
        finally:
            store.close()
    except Exception as exc:  # noqa: BLE001 — 降级不阻断池产出
        return {"status": "DEGRADED", "reason": "%s: %s"
                % (type(exc).__name__, str(exc)[:120])}


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
    except Exception as exc:  # noqa: BLE001 — 降级不阻断池产出
        return {"status": "DEGRADED", "reason": "%s: %s"
                % (type(exc).__name__, str(exc)[:120])}
    summary = doc.get("summary") if isinstance(doc, dict) else None
    if not isinstance(summary, dict):
        return {"status": "DEGRADED", "reason": "统计输出缺 summary 块"}
    return {"status": "OK", "out_path": path,
            "asof": summary.get("asof", ""),
            "windows": summary.get("windows", {}),
            "by_strategy": summary.get("by_strategy", {}),
            "by_weight_group": summary.get("by_weight_group", {}),
            "by_shadow": summary.get("by_shadow", {})}


def main(argv: list) -> int:
    trade_date = (argv[1] if len(argv) > 1
                  else datetime.now().strftime("%Y%m%d"))
    try:
        registry = bootstrap_default_adapters(Registry())
    except Exception as exc:  # noqa: BLE001 — bootstrap 致命
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
    _atomic_write(pool_path,
                  json.dumps(pool, ensure_ascii=False, indent=2)
                  .encode("utf-8"))
    report_path = write_upstream_report(trade_date, pool, enabled, a4=a4)
    print("[xunyou] 股票池 %d 只（buy）→ %s"
          % (pool["stats"]["buy_total"], pool_path))
    print("[xunyou] 汇报信已呈上级（不直连飞书）→ %s" % report_path)
    print("[xunyou] %s ｜ 分歧 %d ｜ 覆盖明细 %d ｜ A4对账 %s"
          % (VERDICT, pool["stats"]["divergence_total"],
             pool["stats"]["superseded_total"], a4.get("status")))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
