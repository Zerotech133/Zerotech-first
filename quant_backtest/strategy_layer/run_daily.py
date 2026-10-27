# -*- coding: utf-8 -*-
"""每日策略编排器（StrategyDaily 计划任务 07:30 触发）。

流程：bootstrap registry（注册引导，幂等）→ SignalAggregator.run(当日)
→ 策源中心大盘快照（regime_center，只读，失败只告警不阻断）→ 摘要输出
（融合条数/分歧/前 5）。单策略失败与写库失败由 aggregator 内部降级
（降级≠失败）；仅 bootstrap 级致命错误才退出码 1。

用法：
    python -X utf8 run_daily.py            # 当日
    python -X utf8 run_daily.py 20261017   # 指定日（补跑/回放）

权重表同步与周日进化器属 P4（weight_updater），本编排器 v1 不触。
"""

from __future__ import annotations

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


def main(argv: list) -> int:
    trade_date = argv[1] if len(argv) > 1 else datetime.now().strftime("%Y%m%d")
    try:
        registry = bootstrap_default_adapters(Registry())
    except Exception as exc:  # noqa: BLE001 — bootstrap 致命，任务失败
        print("[strategy_daily][FATAL] 注册引导失败: %s" % exc)
        traceback.print_exc()
        return 1

    enabled = registry.enabled_ids()
    print(
        "[strategy_daily] %s enabled=%s weights=%s"
        % (trade_date, enabled, registry.weights)
    )
    if not enabled:
        print(
            "[strategy_daily][WARN] 无启用策略，本轮空转（检查 strategy_registry.json）"
        )
        return 0

    aggregator = SignalAggregator(registry=registry)
    result = aggregator.run(trade_date)
    combined, divergences = result.combined, result.divergences
    print("[strategy_daily] 融合 %d 条，分歧 %d 条" % (len(combined), len(divergences)))
    for row in sorted(combined, key=lambda r: -r["score"])[:5]:
        print(
            "[strategy_daily] top: %s %s %.1f conf=%.2f | %s"
            % (
                row["symbol"],
                row["action"],
                row["score"],
                row["confidence"],
                row["rationale"][:80],
            )
        )
    if divergences:
        print(
            "[strategy_daily] 分歧标的: %s"
            % ", ".join(d["symbol"] for d in divergences[:10])
        )

    # ── 策源中心：当日大盘状况快照（只读，失败不阻断主线）────────────
    # 判据对齐 watching v1.0 §5.1；trade_date 显式传参时按该日判定，
    # 否则取指数底座最新交易日（07:30 盘前跑即 T-1，诚实不做当日）。
    try:
        import regime_center

        snap = regime_center.build_snapshot(
            trade_date=(trade_date if len(argv) > 1 else None)
        )
        regime_center.write_snapshot(snap)
        print(
            "[strategy_daily] 策源快照 regime=%s trade_date=%s"
            % (snap.get("regime"), snap.get("trade_date"))
        )
        gaps = (snap.get("data_quality") or {}).get("gaps") or []
        if gaps:
            sys.stderr.write(
                "[strategy_daily][WARN] 策源快照缺口: %s\n" % "; ".join(gaps)
            )
    except Exception as exc:  # noqa: BLE001 — 策源不阻断策略主线
        sys.stderr.write("[strategy_daily][WARN] 策源快照失败: %s\n" % exc)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
