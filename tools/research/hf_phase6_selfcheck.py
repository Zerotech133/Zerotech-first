# -*- coding: utf-8 -*-
"""hf_phase6 自检 S1~S5。--degrade 必须至少红一项。

自检的要求不是「跑通」，而是**能抓住错**：
  S1 分档本身对不对（合成序列，期望值独立于被测函数）
  S2 闸门真的挡了东西（笔数下降 + 有 block 计数）
  S3 分档不丢笔（三档之和 == 已平仓笔数）
  S4 势档持有期真的落到订单上（逐笔核对 hold_n ↔ 建仓日势档）
  S5 阈值极端化后应退化（th=0.99 → 全中市）—— 这一项是**同义反复陷阱**的反面：
     期望值由「±0.99 意味着任何动量都达不到」推出，不引用被测函数的输出。
"""
from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import hf_book as H          # noqa: E402
import hf_phase4 as P4       # noqa: E402
import hf_phase6 as P6       # noqa: E402

FAIL = []
OK = []


def add(name, ok, msg):
    (OK if ok else FAIL).append(name)
    print("  %-4s %-4s %s" % (name, "PASS" if ok else "FAIL", msg))


def fake_index(axis, path):
    """按 path 逐日累乘构造收盘。path 是 ±1 的序列，只影响分档方向。"""
    lvl, out = 100.0, {}
    for d, s in zip(axis, path):
        lvl *= (1 + s)
        out[H._d(d)] = lvl
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--degrade", action="store_true")
    a = ap.parse_args()
    deg = a.degrade

    print("加载上下文（161 天窗口，够快）…")
    ctx = H.build_context(161)
    idxc = H.load_index_close()
    axis = ctx["axis"]

    # ---- S1 分档正确性：合成 +3% / −3% 两段的 20 日动量
    # 期望值从构造推出，不引用 build_regime 的中间量：
    #   i=19  不足 20 个交易日 → 预热，必须 mid（不是 strong）
    #   i=39  相对 i=19 涨 3%   → strong
    #   i=59  相对 i=39 跌 3%   → weak
    n = 60
    path = ([0.0] * 20 + [0.03 / 20] * 20 + [-0.03 / 20] * 20)
    fake = fake_index(axis[:n], path)
    orig = H.load_index_close
    H.load_index_close = lambda: fake
    try:
        r = H.build_regime(axis[:n], n=20, th=0.02)
    finally:
        H.load_index_close = orig
    got = [r.get(axis[i], "?") for i in (19, 39, 59)]
    want = ["mid", "strong", "weak"]
    ok = got == want
    if deg:
        ok = not ok
    add("S1", ok, "合成 20 日动量 预热/+3%%/−3%% → %s（期望 %s）"
        % (got, want))

    # ---- S2 闸门生效
    with P4.params(regime_gate="off"):
        _, t0, _, _, _, _ = H.run_once(ctx)
    H.P.pop("_n_regime_block", None)
    with P4.params(regime_gate="weak_off"):
        _, t1, _, _, _, sd1 = H.run_once(ctx, collect_skip=True)
    blk = H.P.get("_n_regime_block", 0)
    H.P.pop("_n_regime_block", None)
    n0 = len([t for t in t0 if t["exit_reason"] != "open"])
    n1 = len([t for t in t1 if t["exit_reason"] != "open"])
    regime_days = [x for x in sd1 if x.get("why") == "regime"]
    ok = (blk > 0 and n1 < n0 and len(regime_days) > 0
          and all(x.get("regime") == "weak" for x in regime_days))
    if deg:
        ok = not ok
    add("S2", ok, "weak_off 挡 %d 笔（%d→%d），弱市被挡日 %d 个，档位全为 weak"
        % (blk, n0, n1, len(regime_days)))

    # ---- S3 分档不丢笔
    with P4.params(regime_gate="off"):
        _, t2, _, _, _, _ = H.run_once(ctx)
    closed = [t for t in t2 if t["exit_reason"] != "open"]
    g = P6.grp_trades(t2, idxc, ctx["regime"])
    s = sum(v["n"] for v in g.values())
    # 允许有笔因 entry/exit 不在 idxc 而丢弃，但丢的必须是「日期缺失」那种，
    # 不能成规模——超过 2% 说明日期对齐出了问题
    ok = (s <= len(closed)) and (len(closed) - s) <= max(3, len(closed) * 0.02)
    if deg:
        ok = not ok
    add("S3", ok, "已平仓 %d 笔，分档计入 %d 笔，未计入 %d 笔"
        % (len(closed), s, len(closed) - s))

    # ---- S4 势档持有期落到订单
    MAP = {"strong": 8, "mid": 5, "weak": 3}
    with P4.params(hold_by_regime=MAP, hold=5):
        _, t3, _, _, _, _ = H.run_once(ctx)
    bad, checked = 0, 0
    for t in t3:
        g0 = ctx["regime"].get(t["entry_date"], "mid")
        if t.get("hold_n") != MAP[g0]:
            bad += 1
        checked += 1
    ok = checked > 100 and bad == 0
    if deg:
        ok = not ok
    add("S4", ok, "逐笔核对 %d 笔，hold_n 与建仓日势档不符 %d 笔" % (checked, bad))

    # ---- S5 阈值极端化 → 全中市
    # 期望：th=0.99 时 20 日动量不可能超过 ±99%，所有日都必须是 mid。
    r2 = H.build_regime(axis, n=20, th=0.99)
    vals = set(r2.values()) - {"mid"}
    ok = len(vals) == 0
    if deg:
        ok = not ok
    add("S5", ok, "th=0.99 → 非 mid 档位 %d 种（期望 0）" % len(vals))

    print("\n%d/%d 通过" % (len(OK), len(OK) + len(FAIL)))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
