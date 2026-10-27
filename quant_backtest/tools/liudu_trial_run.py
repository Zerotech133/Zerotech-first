# -*- coding: utf-8 -*-
"""六度选股「决定后配置」端到端 trial 试运行（非生产、合成数据、零凭证、不触 R2）。

本运行器是「按计划组织实施」对 5 个已拍板非生产决策门（N1/N4/N8/N10/N11）的
实际落地执行——不是再建脚手架，而是**用决定后的真实配置驱动真实选择逻辑**。

它复用 `strategy_layer.xunyou` 中老田已拍板的 T1/T2 分档选择逻辑
（`assign_tiers` / `build_pool`），喂入**合成**的 combined 信号，跑出真实结构
的候选池产物，证明：

  - N1 universe=market：样本域决议生效（在产物标记中回显）
  - N4 止损参数：fixed[-5%/-8%/-12%]+trailing 10% 已对选中标的做仿真
  - N8 reconcile 模式 B：仍以 NOT_VERIFIED 护栏标记（不私自 READY）
  - N10 入口：产物附能力页深链（不挂顶栏胶囊）
  - N11 tiering 方案 B：产物附分层流转说明（不强制归并上游）

铁律不变：
  - 绝不读取/注入真实密钥（N5 real_auth 未启用）
  - 绝不触发 R2 切流（R2CutoverStaging 未武装，本产物 scope=TRIAL）
  - 不落生产 picks（产物路径 reports/liudu_trial_<date>.json，带 TRIAL 标）
  - 不发起任何网络请求、不写 PG

运行（仓库根）：
    python tools/liudu_trial_run.py [trade_date]
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "strategy_layer"))
sys.path.insert(0, str(_REPO / "tools"))
sys.path.insert(0, str(_REPO / "experiments"))

# === 已拍板决策（与脚手架模块一致，运行器直接沿用，不另设决策）===
from liudu_universe import SELECTED_UNIVERSE  # noqa: E402
from reconcile_semantics import (  # noqa: E402
    SELECTED_RECONCILE_MODE,
    reconcile,
)
from signal_pool_writer import (  # noqa: E402
    TIERING_PLAN,
    TIER_LAYER_ORDER,
    SIGNAL_POOL_AUTO_WRITE,
    build_record,
    write_signals,
    default_output_path,
)
from stop_loss_harness import simulate, default_params  # noqa: E402

# 复用荀攸真实已拍板 T1/T2 分档选择逻辑（老田 2026-10-19 拍板口径）
from strategy_layer.xunyou import build_pool  # noqa: E402


# ---------------------------------------------------------------------------
# 合成数据生成（纯 stdlib；仅用于证明决定后的选择逻辑可跑，非真实行情）
# ---------------------------------------------------------------------------
import random  # noqa: E402

_SYNTH_SYMBOLS = [
    "600519.SH",
    "000858.SZ",
    "601318.SH",
    "300750.SZ",
    "002594.SZ",
    "600036.SH",
    "000333.SZ",
    "601012.SH",
    "600276.SH",
    "000651.SZ",
    "603259.SH",
    "002415.SZ",
    "600900.SH",
    "601888.SH",
    "000001.SZ",
    "688981.SH",
    "600030.SH",
    "002475.SZ",
    "601166.SH",
    "600585.SH",
    "000725.SZ",
    "601398.SH",
    "600887.SH",
    "002714.SZ",
    "603288.SH",
    "600309.SH",
    "000792.SZ",
    "601857.SH",
    "600048.SH",
    "002304.SZ",
]


def _synth_combined(trade_date: str, seed: int = 20261021) -> tuple:
    """合成 combined 信号 + divergences，结构对齐 SignalAggregator.run 输出。

    评分分布刻意跨过 T1(>99)/T2(95-99)/不入档(<95) 三个断层，以真实触发
    分档逻辑；少量 divergence=true 触发人工复核分支。
    """
    rng = random.Random(seed)
    combined = []
    divergences = []
    for i, sym in enumerate(_SYNTH_SYMBOLS):
        # 评分：约 20% 入 T1、约 30% 入 T2、其余不入档
        roll = rng.random()
        if roll < 0.20:
            score = round(rng.uniform(99.05, 99.9), 2)
        elif roll < 0.50:
            score = round(rng.uniform(95.0, 99.0), 2)
        else:
            score = round(rng.uniform(80.0, 94.9), 2)
        divergent = i % 11 == 0  # 少量分歧票
        row = {
            "symbol": sym,
            "action": "buy",
            "score": score,
            "confidence": round(rng.uniform(0.4, 0.95), 2),
            "divergence": divergent,
            "rationale": "合成 trial 信号（非真实行情）：六度选股决定后配置试运行",
            "contributions": {
                "kg_boost": {"score": round(score * 0.6, 2), "superseded": ()},
                "chan_monitor": {"score": round(score * 0.4, 2), "superseded": ()},
            },
        }
        combined.append(row)
        if divergent:
            divergences.append({"symbol": sym, "reason": "合成分歧标记"})
    return combined, divergences


def _synth_price_path(seed: int) -> list:
    """合成单标的 10 日价格路径（随机游走，仅供 N4 止损仿真）。"""
    rng = random.Random(seed)
    price = 100.0
    path = [price]
    for _ in range(9):
        price *= 1 + rng.uniform(-0.06, 0.06)
        path.append(round(price, 2))
    return path


def main(argv: list) -> int:
    trade_date = argv[1] if len(argv) > 1 else datetime.now().strftime("%Y%m%d")
    print("=== 六度选股 决定后配置 端到端 trial 试运行 ===")
    print(
        f"[ctx] universe={SELECTED_UNIVERSE} reconcile_mode={SELECTED_RECONCILE_MODE}"
        f" tiering={TIERING_PLAN}"
    )

    # --- N1 + 真实选择逻辑：构造合成信号并跑荀攸已拍板 T1/T2 分档 ---
    combined, divergences = _synth_combined(trade_date)
    pool = build_pool(trade_date, combined, divergences)
    print(
        f"[N1/N10] 荀攸真实分档逻辑跑通：buy={pool['stats']['buy_total']}"
        f" T1={pool['stats']['tier1_total']} T2={pool['stats']['tier2_total']}"
        f" 复核={pool['stats']['review_total']}"
    )
    assert pool["stats"]["tier1_total"] > 0, "T1 断层逻辑未触发（合成分布异常）"

    # --- N8 reconcile 模式 B：仍以 NOT_VERIFIED 护栏（不私自 READY）---
    rec = reconcile(trade_date=trade_date, mode=SELECTED_RECONCILE_MODE)
    print(
        f"[N8] reconcile(mode={SELECTED_RECONCILE_MODE}) -> status={rec.get('status')}"
        f" ready={rec.get('ready')}"
    )
    assert rec.get("status") in ("NOT_VERIFIED", "REJECTED")
    assert rec.get("ready") is False

    # --- N4 止损仿真：对 T1/T2 选中标的跑 fixed+trailing ---
    params = default_params()
    sel = [r for r in pool["pool"] if r["tier"] in ("T1", "T2")]
    sl_rows = []
    for idx, r in enumerate(sel):
        path = _synth_price_path(seed=hash(r["symbol"]) % (10**8))
        fixed = simulate(path, "fixed", params)
        trail = simulate(path, "trailing", params)
        sl_rows.append(
            {
                "symbol": r["symbol"],
                "tier": r["tier"],
                "score": r["score"],
                "stop_loss_fixed_pnl": round(fixed["final_pnl"], 4),
                "stop_loss_trailing_pnl": round(trail["final_pnl"], 4),
                "max_drawdown": round(fixed["max_drawdown"], 4),
            }
        )
    avg_fixed = sum(x["stop_loss_fixed_pnl"] for x in sl_rows) / max(len(sl_rows), 1)
    avg_trail = sum(x["stop_loss_trailing_pnl"] for x in sl_rows) / max(len(sl_rows), 1)
    print(
        f"[N4] 止损仿真覆盖 {len(sl_rows)} 只 | fixed均值PnL={avg_fixed:.4f}"
        f" trailing均值PnL={avg_trail:.4f}"
    )

    # --- N11 tiering 方案 B：决定后配置激活 SIGNAL_POOL_AUTO_WRITE，显式落分层产物 ---
    signal_pool_path = default_output_path()
    n_written = 0
    if SIGNAL_POOL_AUTO_WRITE:
        # 方案 B：xunyou_pool 作为上游之一，分层汇聚到统一消费入口 signal_pool.jsonl
        records = [
            build_record(
                r["symbol"],
                r["score"],
                "xunyou_pool",
                trade_date,
                meta={
                    "tier": r["tier"],
                    "action": r["action"],
                    "tiering_plan": TIERING_PLAN,
                    "tier_layer_order": list(TIER_LAYER_ORDER),
                },
            )
            for r in pool["pool"]
        ]
        n_written = write_signals(records, signal_pool_path)
        print(
            f"[N11] 方案B分层落账 → {signal_pool_path}（写入 {n_written} 条，幂等 upsert，重跑不累积）"
        )
    else:
        print("[N11] SIGNAL_POOL_AUTO_WRITE=False，未落账（保持休眠）")

    artifact = {
        "scope": "TRIAL_NOT_PRODUCTION",
        "cutover_verdict": "BLOCK_CUTOVER",
        "run_kind": "liudu_decided_config_trial_run",
        "trade_date": trade_date,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "decided_config": {
            "N1_universe": SELECTED_UNIVERSE,
            "N8_reconcile_mode": SELECTED_RECONCILE_MODE,
            "N11_tiering_plan": TIERING_PLAN,
            "N11_tier_layer_order": list(TIER_LAYER_ORDER),
            "N4_stop_loss_params": params,
            "N10_entry": "portal/static/liudu_entry.html（子页入口，不挂顶栏胶囊）",
        },
        "reconcile_guard": rec,
        "pool_stats": pool["stats"],
        "selected_with_stop_loss": sl_rows,
        "signal_pool_written": n_written,
        "signal_pool_path": signal_pool_path if n_written else None,
        "notes": [
            "本产物为决定后配置 trial 试运行，非生产 picks，不构成交易指令。",
            "N5 真实数据源（real_auth）未启用；本运行全程合成数据、零凭证。",
            "R2 切流未武装，任何路径不可达。",
            f"N11 方案B分层落账：SIGNAL_POOL_AUTO_WRITE={SIGNAL_POOL_AUTO_WRITE}，"
            f"本次写入 data/signal_pool.jsonl {n_written} 条（幂等 upsert，trial 数据，重跑不累积）。",
            "生产统一入口落账仍须 R2 切流批准（N12）。",
        ],
    }

    out_dir = Path(_REPO) / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"liudu_trial_{trade_date}.json"
    tmp = str(out_path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(artifact, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, str(out_path))

    print(f"[out] trial 产物 → {out_path}")
    print(
        "[done] 决定后配置端到端试运行通过：真实分档逻辑跑通 + N4 仿真覆盖 + "
        "N8 护栏生效。"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
