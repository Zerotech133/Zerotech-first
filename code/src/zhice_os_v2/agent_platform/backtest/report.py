"""回测统计报告：路径分布 / 方向占比 / 分方法票准确率（T+5 前向收益符号）/ 冲突率。

输出 Markdown + JSON 双格式到 code/agent_platform_reports/。
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from zhice_os_v2.agent_platform.backtest.engine import BacktestResult

logger = logging.getLogger(__name__)

# 档位 → 24/7 理论 bar 数/天（CRYPTO 口径）
BARS_PER_DAY: dict[str, int] = {"5m": 288, "15m": 96, "1h": 24}
_COVERAGE_GAP_THRESHOLD = 0.85   # 单日覆盖率 <85% → 标注"数据断档"


def _daily_coverage(result: BacktestResult) -> dict:
    """按天 bar 覆盖统计（P2b：断档透明化，全日历范围口径）。

    - 日历范围 = result.start ~ result.end 的全部 UTC 自然日（含零 bar 天：
      ETL 整日缺口同样显式列出并标注断档，不允许"无数据=隐身"）
    - per_day：每天实际 bar 数（决策数=重放 bar 数）vs 理论值（标的数×档位 24/7）
    - 首末日为 warmup/窗口边界日，天然部分覆盖，不计断档；
      中间日覆盖率 <85%（含 0）→ data_gap
    - overall_coverage = 总实际 / (理论×总天数)——与 QA 全范围口径一致
    """
    freq = result.freq if result.freq in BARS_PER_DAY else "15m"
    per_day_theory = BARS_PER_DAY[freq] * max(1, len(result.symbols))
    days: dict[str, int] = {}
    for d in result.decisions:
        bt = d.get("bar_time")
        if bt:
            day = str(bt)[:10]
            days[day] = days.get(day, 0) + 1

    # 全日历范围（start/end 为 ISO 串；解析失败回退为有数据日的范围）
    try:
        cal_start = str(result.start)[:10]
        cal_end = str(result.end)[:10]
        calendar_days = _calendar_range(cal_start, cal_end)
    except Exception:
        calendar_days = sorted(days.keys())

    per_day: list[dict] = []
    gap_days: list[str] = []
    total_actual = 0
    n_cal = len(calendar_days)
    for i, day in enumerate(calendar_days):
        n = days.get(day, 0)
        coverage = n / per_day_theory
        is_boundary = i == 0 or i == n_cal - 1
        row = {
            "date": day,
            "bars": n,
            "theoretical": per_day_theory,
            "coverage": round(coverage, 4),
            "boundary_day": is_boundary,
            "data_gap": (not is_boundary) and coverage < _COVERAGE_GAP_THRESHOLD,
        }
        if row["data_gap"]:
            gap_days.append(day)
        per_day.append(row)
        total_actual += n
    overall = (total_actual / (per_day_theory * n_cal)) if n_cal else 0.0
    return {
        "freq_theoretical_per_day": per_day_theory,
        "per_day": per_day,
        "overall_coverage": round(overall, 4),
        "gap_days": gap_days,
    }


def _calendar_range(start_day: str, end_day: str) -> list[str]:
    """闭区间日历日列表（UTC）。"""
    from datetime import date as _date
    from datetime import timedelta as _td

    d0 = _date.fromisoformat(start_day)
    d1 = _date.fromisoformat(end_day)
    if d1 < d0:
        d0, d1 = d1, d0
    return [(d0 + _td(days=i)).isoformat() for i in range((d1 - d0).days + 1)]


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def compute_stats(result: BacktestResult) -> dict:
    """核心统计（纯函数，dict 供 JSON / Markdown 共用）。"""
    decisions = result.decisions
    n = len(decisions)
    stats: dict = {
        "generated_at": datetime.now(UTC).isoformat(),
        "market": result.market,
        "freq": result.freq,
        "requested_freq": result.requested_freq,
        "freq_fallback": result.freq_fallback,
        "fallback_reason": result.fallback_reason,
        "symbols": result.symbols,
        "n_symbols": len(result.symbols),
        "start": result.start,
        "end": result.end,
        "total_bars": result.total_bars,
        "decision_total": n,
        "elapsed_s": round(result.elapsed_s, 2),
        "avg_ms_per_bar": round(result.avg_ms_per_bar, 3),
        "llm_mode": "MOCK" if result.llm_mock else "REAL(sample)",
        "llm_real_calls": result.llm_real_calls,
    }

    if n == 0:
        stats["note"] = "无决策产出（数据或方法未触发）"
        return stats

    # 路径分布
    paths = {"WEIGHTED": 0, "REGIME_VETO": 0, "LLM": 0, "DROPPED": 0}
    actions = {"SELL": 0, "HOLD": 0, "WAIT": 0}
    for d in decisions:
        paths[d.get("arbitration_path", "?")] = paths.get(d.get("arbitration_path", "?"), 0) + 1
        actions[d.get("action", "?")] = actions.get(d.get("action", "?"), 0) + 1
    stats["path_distribution"] = paths
    stats["action_distribution"] = actions
    stats["path_pct"] = {k: round(v / n, 4) for k, v in paths.items()}
    stats["action_pct"] = {k: round(v / n, 4) for k, v in actions.items()}

    # 冲突率 = 进 LLM 或 DROPPED 占比
    conflict = paths.get("LLM", 0) + paths.get("DROPPED", 0)
    stats["conflict_rate"] = round(conflict / n, 4)

    # 分方法票准确率：以 T+5 前向收益符号为准
    #   SELL 票正确 ⇔ fwd_ret < 0（卖出后跌）；HOLD 票正确 ⇔ fwd_ret ≥ 0
    method_acc: dict[str, dict] = {}
    for d in decisions:
        fwd = d.get("fwd_ret_5d")
        if fwd is None:
            continue
        for v in d.get("votes_json", []):
            mid = v.get("method_id")
            direction = v.get("direction")
            if mid is None or direction == "WAIT":
                continue
            bucket = method_acc.setdefault(mid, {"hit": 0, "total": 0})
            bucket["total"] += 1
            if (direction == "SELL" and fwd < 0) or (direction == "HOLD" and fwd >= 0):
                bucket["hit"] += 1
    for mid, b in method_acc.items():
        b["accuracy"] = round(b["hit"] / b["total"], 4) if b["total"] else None
    stats["method_accuracy"] = method_acc

    # 决策级 T+5 准确率（SELL 决策之后 5 根内确实下跌）
    sell_decisions = [d for d in decisions if d.get("action") == "SELL" and d.get("fwd_ret_5d") is not None]
    if sell_decisions:
        hits = sum(1 for d in sell_decisions if d["fwd_ret_5d"] < 0)
        stats["sell_decision_t5_accuracy"] = {
            "total": len(sell_decisions),
            "hit": hits,
            "accuracy": round(hits / len(sell_decisions), 4),
        }
    else:
        stats["sell_decision_t5_accuracy"] = {"total": 0, "hit": 0, "accuracy": None}

    # 按天数据覆盖（断档透明化；只加不改既有字段）
    stats["daily_coverage"] = _daily_coverage(result)
    return stats


def to_markdown(stats: dict) -> str:
    """stats dict → 人读 Markdown 报告。"""
    lines: list[str] = []
    a = lines.append
    a("# 盯盘中台回测报告（agent_platform）\n")
    a(f"- 生成时间(UTC): {stats.get('generated_at')}")
    a(f"- 市场: {stats.get('market')}  档位: {stats.get('freq')}"
      + (f"（请求 {stats.get('requested_freq')}，**已降级**）" if stats.get("freq_fallback") else ""))
    if stats.get("fallback_reason"):
        a(f"  - 降级原因: {stats['fallback_reason']}")
    a(f"- 标的数: {stats.get('n_symbols')}  区间: {stats.get('start')} ~ {stats.get('end')}")
    a(f"- 重放 bar 数: {stats.get('total_bars')}  耗时: {stats.get('elapsed_s')}s"
      f"  单bar均耗: {stats.get('avg_ms_per_bar')}ms")
    a(f"- LLM 模式: {stats.get('llm_mode')}  真调用: {stats.get('llm_real_calls')}")
    cov = stats.get("daily_coverage")
    if cov:
        a(f"- **数据覆盖率: {_pct(cov['overall_coverage'])}**"
          + (f"（⚠ 存在断档日: {', '.join(cov['gap_days'])}）" if cov["gap_days"] else ""))
    a("")

    if stats.get("decision_total", 0) == 0:
        a(f"> {stats.get('note', '无决策')}\n")
        return "\n".join(lines)

    n = stats["decision_total"]
    a("## 决策概览\n")
    a(f"- 决策总数: {n}")
    a(f"- 冲突率(进LLM+DROPPED): {_pct(stats['conflict_rate'])}\n")

    a("## 仲裁路径分布\n")
    a("| 路径 | 次数 | 占比 |")
    a("|---|---:|---:|")
    for k, v in stats["path_distribution"].items():
        a(f"| {k} | {v} | {_pct(stats['path_pct'][k])} |")
    a("")

    a("## 决策方向分布\n")
    a("| 方向 | 次数 | 占比 |")
    a("|---|---:|---:|")
    for k, v in stats["action_distribution"].items():
        a(f"| {k} | {v} | {_pct(stats['action_pct'][k])} |")
    a("")

    a("## 分方法票准确率（T+5 前向收益符号）\n")
    acc = stats.get("method_accuracy", {})
    if acc:
        a("| 方法 | 有效票 | 命中 | 准确率 |")
        a("|---|---:|---:|---:|")
        for mid, b in acc.items():
            acc_s = "—" if b.get("accuracy") is None else _pct(b["accuracy"])
            a(f"| {mid} | {b['total']} | {b['hit']} | {acc_s} |")
    else:
        a("（无带 T+5 回填的票样本）")
    a("")

    t5 = stats.get("sell_decision_t5_accuracy", {})
    acc_s = "—" if t5.get("accuracy") is None else _pct(t5["accuracy"])
    a(f"## SELL 决策 T+5 准确率: {t5.get('hit', 0)}/{t5.get('total', 0)} = {acc_s}\n")

    cov = stats.get("daily_coverage")
    if cov and cov.get("per_day"):
        a("## 按天数据覆盖\n")
        a(f"- 理论值/天: {cov['freq_theoretical_per_day']} bar（标的数 × {stats.get('freq')} 档 24/7）")
        a("- 统计口径：start~end 全日历范围（含零 bar 天）；首末为 warmup/窗口边界日，天然部分覆盖不计断档\n")
        a("| 日期 | 实际bar | 理论 | 覆盖率 | 备注 |")
        a("|---|---:|---:|---:|---|")
        for row in cov["per_day"]:
            note = []
            if row["boundary_day"]:
                note.append("边界日")
            if row["data_gap"]:
                note.append("**数据断档**")
            a(f"| {row['date']} | {row['bars']} | {row['theoretical']} | "
              f"{_pct(row['coverage'])} | {','.join(note) or '—'} |")
        a("")
    return "\n".join(lines)


def write_reports(result: BacktestResult, out_dir: str | Path | None = None,
                  tag: str = "") -> tuple[Path, Path, dict]:
    """写 Markdown + JSON 双报告。返回 (md_path, json_path, stats)。"""
    stats = compute_stats(result)
    base = Path(out_dir) if out_dir else Path("agent_platform_reports")
    base.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    tag_s = f"-{tag}" if tag else ""
    md_path = base / f"backtest-{result.market}-{result.freq}-{ts}{tag_s}.md"
    json_path = base / f"backtest-{result.market}-{result.freq}-{ts}{tag_s}.json"
    md_path.write_text(to_markdown(stats), encoding="utf-8")
    json_path.write_text(
        json.dumps({"stats": stats, "decisions": result.decisions},
                   ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    logger.info("回测报告已写: %s / %s", md_path, json_path)
    return md_path, json_path, stats
