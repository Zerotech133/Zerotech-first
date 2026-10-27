"""六度选股非生产管线冒烟（全 trial / 合成数据，零生产触碰、零真实凭证）。

目的：证明门前准备阶段落地的 5 个非生产模块（N1/N4/N8/N10/N11 相关）
在「休眠默认仍关闭」的前提下，其 trial/合成路径可被完整串联并跑通，
不存在任何运行时断点。本脚本：
  - 不读取/注入任何真实密钥
  - 不发起任何网络请求
  - 不写入生产路径（signal_pool 仅落 tempfile，运行后自动回收）
  - 不翻转任何 *_ENABLED 开关（全部以 trial / 合成态验证）

运行（仓库根目录）：
    python tools/liudu_trial_smoke.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
for _d in ("strategy_layer", "tools", "experiments"):
    sys.path.insert(0, str(_REPO / _d))

from liudu_universe import resolve_universe, SELECTED_UNIVERSE  # noqa: E402
from reconcile_semantics import reconcile, SELECTED_RECONCILE_MODE  # noqa: E402
from signal_pool_writer import build_record, write_signals  # noqa: E402
from data_source_adapter import build_adapter, KNOWN_DATA_SOURCES  # noqa: E402
from stop_loss_harness import simulate, default_params  # noqa: E402


def main() -> int:
    print("=== 六度选股 非生产管线冒烟（trial/合成，零生产触碰）===")

    # --- N1 universe：解析选定样本域（合成校验，不实际拉取）---
    u = resolve_universe(SELECTED_UNIVERSE)
    print(f"[N1] universe resolve('{SELECTED_UNIVERSE}') -> {u}")

    # --- N8 reconcile：模式 B（R1 单源基线），仍恒 NOT_VERIFIED（不升级为 READY）---
    r = reconcile(trade_date="2026-10-21", mode=SELECTED_RECONCILE_MODE)
    print(f"[N8] reconcile(mode={SELECTED_RECONCILE_MODE}) -> status={r.get('status')}")
    assert r.get("status") in ("NOT_VERIFIED", "REJECTED"), (
        "reconcile 不得私自输出 READY"
    )

    # --- N5 数据源：trial 态合成 fetch（绝不发请求、绝不持真实凭证）---
    adapters = {src: build_adapter(src, state="trial") for src in KNOWN_DATA_SOURCES}
    print(f"[N5] trial 态适配器 {len(adapters)} 个：{', '.join(adapters)}")
    for src, ad in adapters.items():
        assert ad.get_state() == "trial"
        assert not ad.requires_credentials()
        f = ad.fetch()
        assert f["state"] == "trial"

    # --- N11 signal_pool：构建合成记录，仅落 tempfile 验证 append-only 写入 ---
    rec = build_record(
        symbol="SH600000",
        score=0.82,
        source="liudu_trial",
        trade_date="2026-10-21",
        meta={"universe": u},
    )
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "signal_pool_smoke.jsonl"
        n = write_signals(
            [rec, build_record("SZ000001", 0.77, "liudu_trial", "2026-10-21")], str(tmp)
        )
        print(f"[N11] signal_pool 写入（tempfile）条数={n}，路径={tmp.name}")

    # --- N4 止损实验：合成价格序列仿真（fixed 三档 + trailing）---
    prices = [100.0, 98.0, 95.0, 92.0, 90.0, 88.0, 91.0, 95.0, 99.0, 103.0]
    params = default_params()
    sim = simulate(prices, "fixed", params)
    trail = simulate(prices, "trailing", params)
    print(
        f"[N4] fixed 策略 final_pnl={sim['final_pnl']:.4f} max_dd={sim['max_drawdown']:.4f}"
    )
    print(
        f"[N4] trailing 策略 final_pnl={trail['final_pnl']:.4f} max_dd={trail['max_drawdown']:.4f}"
    )

    print("-" * 52)
    print("冒烟通过：五大非生产模块 trial/合成路径完整串联，无断点、无生产触碰。")
    print("注：所有 *_ENABLED 开关仍为 False；本冒烟不证明生产态可用，仅证链路可跑。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
