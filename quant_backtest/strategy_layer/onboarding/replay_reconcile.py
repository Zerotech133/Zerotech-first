# -*- coding: utf-8 -*-
"""S3 历史回放对账脚本：新 adapter 输出 vs 策略本体基线，信号一致率报告。

口径：
- 对账对象：REPLAY 模式下 classic_adapter 的仓位状态（由 adapter 的
  变化事件重建逐日仓位）vs 策略本体 ``strategies.REGISTRY[name](df, params)``
  的逐帧仓位（runner.py 回测同款输入口径）。
- 一致率 = 仓位一致的 (symbol, trade_date) 数 / 参与对账总数；阈值默认
  0.99（≥99% 通过），低于阈值退出码 1（S4 影子期准入闸门）。
- 数据源：data_loader.load_bars 只读接口（注入式可换合成数据），不重拉
  数据、不写任何库；本脚本唯一的写动作是 stdout 报告。

基线两种来源：
1. 缺省：脚本内部用策略本体对 as-of 数据现算（与 runner.py
   run_single 的 ``sig = strat.REGISTRY[name](df, params)`` 同款调用）；
2. ``--baseline <jsonl>``：runner.py / 上批回放导出的基线文件，每行
   ``{"strategy": ..., "symbol": ..., "trade_date": "YYYYMMDD",
     "position": 0|1}``（坏行跳过，容忍 YYYY-MM-DD 形态日期）。

CLI（对齐 runner.py 参数风格 --strategy/--market/--symbol/--start/--end）：

    python -m strategy_layer.onboarding.replay_reconcile \
        --strategy ma_cross --symbol 600519.SH --market CN \
        --start 2026-06-01 --end 2026-09-30 \
        [--baseline baseline.jsonl] [--threshold 0.99] [--json]

已知局限：缺省基线与 runner 单窗口基线在窗口起点可能有预热差异
（本脚本 as-of 用全历史，runner 用 CLI 窗口），事件帧语义策略
（rsi_reversal/boll_breakout）若 start 前已有未平仓触发会对不平——
此类策略请优先用 runner 导出的 --baseline 文件对账（见报告 warmup 注记）。
"""

import argparse
import json
import os
import sys
from typing import Any, Callable, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_QB_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _QB_ROOT not in sys.path:
    sys.path.insert(0, _QB_ROOT)

from strategy_layer.onboarding.classic_adapter import (  # noqa: E402
    ClassicStrategyAdapter,
    strategy_fn,
)

DEFAULT_THRESHOLD = 0.99  # RC-1 S3 闸门：信号一致率 ≥99%
MAX_MISMATCH_SHOWN = 20  # 报告最多列出的不一致明细条数


# ---------------------------------------------------------------------- #
# 基线来源 1：策略本体现算（as-of 全历史口径）
# ---------------------------------------------------------------------- #
def compute_baseline(
    strategy_name: str,
    symbols: List[str],
    market: str,
    start: Optional[str],
    end: Optional[str],
    params: Optional[dict] = None,
    loader: Optional[Callable] = None,
) -> Dict[str, Dict[str, int]]:
    """策略本体逐帧仓位 → {symbol: {YYYYMMDD: position}}。

    end 必填（对账窗右沿）；start 传 None 即全历史 as-of（预热最充分，
    与 adapter 侧口径一致）。只读 loader，不写任何库。
    """
    if not end:
        raise ValueError("对账窗右沿 end 必填（YYYY-MM-DD 或 YYYYMMDD）")
    if loader is None:
        from data_loader import load_bars

        loader = load_bars
    frames = loader(market, symbols, start, end) or {}
    fn = strategy_fn(strategy_name)
    p = dict(params) if params is not None else {}
    baseline: Dict[str, Dict[str, int]] = {}
    for symbol in symbols:
        df = frames.get(symbol)
        if df is None or len(df) == 0:
            continue
        pos = fn(df, p)
        baseline[symbol] = {_to_ymd8(dt): int(v) for dt, v in pos.items()}
    return baseline


def _to_ymd8(dt: Any) -> str:
    """df 索引（Timestamp/str）→ YYYYMMDD。"""
    s = str(dt).replace("-", "").replace("/", "")
    return s[:8]


# ---------------------------------------------------------------------- #
# 基线来源 2：JSONL 基线文件（runner 导出口径）
# ---------------------------------------------------------------------- #
def load_baseline_jsonl(path: str, strategy_name: str) -> Dict[str, Dict[str, int]]:
    """读基线 JSONL → {symbol: {YYYYMMDD: position}}。

    每行需含 symbol / trade_date / position；strategy 字段存在时必须与
    对账策略一致（防拿错文件）；坏行跳过不致命。
    """
    baseline: Dict[str, Dict[str, int]] = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            if row.get("strategy") and row["strategy"] != strategy_name:
                continue
            symbol = str(row.get("symbol") or "").strip()
            td = str(row.get("trade_date") or "").replace("-", "")[:8]
            pos = row.get("position")
            if not symbol or len(td) != 8 or not td.isdigit() or pos is None:
                continue
            try:
                baseline.setdefault(symbol, {})[td] = int(pos)
            except (TypeError, ValueError):
                continue
    return baseline


# ---------------------------------------------------------------------- #
# adapter 侧：走 score() 公开路径逐日重建仓位（S3 对账的正主）
# ---------------------------------------------------------------------- #
def adapter_positions_from_scores(
    adapter: ClassicStrategyAdapter,
    dates_ymd8: List[str],
    symbols: List[str],
) -> Dict[str, Dict[str, int]]:
    """逐日调 adapter.score(td) → 由信号流重建 {symbol: {YYYYMMDD: position}}。

    与基线的差异来源因此真实可测：score 每日在 as-of 截断数据上重算本体
    并只吐变化事件，这里再由事件流重建逐日状态（buy→1 / sell→0）。
    只在给定日期格点上重建（对账以基线日历为准；格点外的事件在下一
    格点自然体现在状态里）。"""
    state = {s: 0 for s in symbols}
    out: Dict[str, Dict[str, int]] = {s: {} for s in symbols}
    for td in sorted(dates_ymd8):
        for sig in adapter.score(td):
            if sig.symbol in state:
                state[sig.symbol] = 1 if sig.action == "buy" else 0
        for s in symbols:
            out[s][td] = state[s]
    return out


def _default_loader(market, symbols, start, end):
    from data_loader import load_bars

    return load_bars(market, list(symbols), start, end)


# ---------------------------------------------------------------------- #
# 对账核心（纯函数，可单测）
# ---------------------------------------------------------------------- #
def run_reconcile(
    baseline: Dict[str, Dict[str, int]],
    adapter_pos: Dict[str, Dict[str, int]],
    start: Optional[str] = None,
    end: Optional[str] = None,
    threshold: float = DEFAULT_THRESHOLD,
) -> dict:
    """逐 (symbol, trade_date) 比对仓位 → 一致率报告 dict。

    只比对两侧共有的 symbol；日期范围可裁剪；baseline 独有的日期视为
    参与对账（adapter 缺失即不一致——事件流丢了信号必须暴露）。
    """
    s8 = _to_ymd8(start) if start else None
    e8 = _to_ymd8(end) if end else None
    total = 0
    matched = 0
    mismatches: List[dict] = []
    for symbol in sorted(baseline):
        base_days = baseline[symbol]
        adp_days = adapter_pos.get(symbol, {})
        for td in sorted(base_days):
            if s8 and td < s8:
                continue
            if e8 and td > e8:
                continue
            total += 1
            base_p = int(base_days[td])
            adp_p = adp_days.get(td)
            if adp_p is not None and int(adp_p) == base_p:
                matched += 1
            else:
                mismatches.append(
                    {
                        "symbol": symbol,
                        "trade_date": td,
                        "baseline": base_p,
                        "adapter": adp_p,
                    }
                )
    rate = (matched / total) if total else 0.0
    return {
        "n_total": total,
        "n_match": matched,
        "n_mismatch": total - matched,
        "agree_rate": round(rate, 6),
        "threshold": threshold,
        "passed": bool(total > 0 and rate >= threshold),
        "mismatches": mismatches[:MAX_MISMATCH_SHOWN],
        "warmup_note": "缺省基线为全历史 as-of 口径；runner 单窗口基线"
        "在窗口起点可能对不平（事件帧策略预热差异），"
        "此类请用 --baseline 文件对账",
    }


def format_report(report: dict) -> str:
    """报告文本（stdout，人读）。"""
    lines = [
        "REPLAY_RECONCILE 信号一致率报告",
        "  参与对账: %d (symbol, trade_date) 对" % report["n_total"],
        "  一致: %d  不一致: %d" % (report["n_match"], report["n_mismatch"]),
        "  一致率: %.4f%%  阈值: %.2f%%  判定: %s"
        % (
            report["agree_rate"] * 100,
            report["threshold"] * 100,
            "PASS" if report["passed"] else "FAIL",
        ),
    ]
    if report["mismatches"]:
        lines.append("  不一致明细（前 %d 条）:" % len(report["mismatches"]))
        for m in report["mismatches"]:
            lines.append(
                "    %s %s baseline=%s adapter=%s"
                % (m["symbol"], m["trade_date"], m["baseline"], m["adapter"])
            )
    lines.append("  口径注记: " + report["warmup_note"])
    return "\n".join(lines)


# ---------------------------------------------------------------------- #
# CLI
# ---------------------------------------------------------------------- #
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="S3 历史回放对账：adapter vs 策略本体基线一致率"
    )
    ap.add_argument(
        "--strategy", required=True, help="策略名（strategies.REGISTRY 键）"
    )
    ap.add_argument("--market", default="CN", help="市场（默认 CN）")
    ap.add_argument(
        "--symbol", required=True, help="标的，逗号分隔多个（如 600519.SH,000338.SZ）"
    )
    ap.add_argument("--start", default=None, help="对账窗起点 YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="对账窗右沿 YYYY-MM-DD")
    ap.add_argument(
        "--baseline",
        default=None,
        help="基线 JSONL（symbol/trade_date/position 行）；缺省用策略本体 as-of 现算",
    )
    ap.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help="一致率阈值（默认 0.99）",
    )
    ap.add_argument("--json", action="store_true", help="输出原始 JSON 报告")
    a = ap.parse_args(argv)

    symbols = [s.strip() for s in a.symbol.split(",") if s.strip()]
    if not symbols:
        print("RECONCILE_FAIL symbols 为空")
        return 2

    try:
        if a.baseline:
            baseline = load_baseline_jsonl(a.baseline, a.strategy)
            if not baseline:
                print("RECONCILE_FAIL 基线文件无有效行: %s" % a.baseline)
                return 2
        else:
            baseline = compute_baseline(a.strategy, symbols, a.market, a.start, a.end)
        dates = sorted({td for days in baseline.values() for td in days})
        adapter = ClassicStrategyAdapter(a.strategy, symbols, market=a.market)
        adapter_pos = adapter_positions_from_scores(adapter, dates, symbols)
    except Exception as exc:
        print("RECONCILE_FAIL 对账准备失败: %s" % exc)
        return 2

    report = run_reconcile(
        baseline, adapter_pos, start=a.start, end=a.end, threshold=a.threshold
    )
    if a.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        print(format_report(report))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
