# -*- coding: utf-8 -*-
"""qd_scanner 尾盘复核量纲修复（W1-2 任务B 20261014）单测。

覆盖（三条路径 + 集成冒烟，纯合成数据，不联网）:
  1. 湖口径优先：历史 19 根用湖 amount（准确千元），仅当日实时 bar 估算
  2. 退化近似：湖缺失 → 全序列 vol*close/1000（旧行为）
  3. 错日 skip：湖末根 ≠ 实时序列前一日（断更/错位）→ 整体退回近似
  4. 集成：intraday_recheck 端到端——湖在时 note 无标注，湖缺时
     note 带 amount_approx；输出格式（键集）不变

运行:
  "C:/Program Files/Python311/python.exe" portal/test_qd_amount_fix.py
"""

import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

PORTAL = Path(__file__).resolve().parent
sys.path.insert(0, str(PORTAL))
sys.path.insert(0, str(PORTAL.parent / "quant_backtest"))

import qd_scanner as qd  # noqa: E402

PASS = []


def check(name, cond):
    PASS.append((name, bool(cond)))
    print("[%s] %s" % ("PASS" if cond else "FAIL", name))


TODAY = date.today()
YDAY = TODAY - timedelta(days=1)


def make_d(n=30, yday=YDAY):
    """实时序列：n-1 根历史 + 当日实时 bar（date 列 'YYYYMMDD' 混合形态
    与腾讯源一致——历史带 '-'，当日也可能带 '-'，merge_amount 兼容）。"""
    rows = []
    for i in range(n - 1):
        d = yday - timedelta(days=n - 2 - i)
        rows.append([d.isoformat(), 10.0, 10.1, 10.3, 9.9, 5000.0])
    rows.append([TODAY.isoformat(), 10.1, 10.2, 10.4, 10.0, 6000.0])
    return pd.DataFrame(rows, columns=["date", "open", "close", "high", "low", "vol"])


def make_lake(d, offset=0):
    """湖 amount 映射：与 d 历史 root 逐日对齐（offset 模拟断更错日）。"""
    lake = {}
    for x in list(d["date"])[:-1][offset:]:
        td8 = str(x).replace("-", "")
        lake[td8] = 123456.0
    return lake


# ── 1. 湖口径优先 ─────────────────────────────────────────
def test_lake_first():
    d = make_d()
    lake = make_lake(d)
    amt, used = qd.merge_amount(d, lake)
    check("湖口径: used_lake=True", used)
    check(
        "湖口径: 历史 19 根=湖 amount",
        all(abs(amt.iloc[i] - 123456.0) < 1e-6 for i in range(len(d) - 1)),
    )
    est = d["vol"].iloc[-1] * d["close"].iloc[-1] / 1000.0
    check("湖口径: 当日=实时估算增量", abs(amt.iloc[-1] - est) < 1e-6)
    check(
        "湖口径: 不改原 d 列",
        list(d.columns) == ["date", "open", "close", "high", "low", "vol"],
    )


# ── 2. 退化近似（湖缺失） ─────────────────────────────────
def test_fallback_missing():
    d = make_d()
    amt, used = qd.merge_amount(d, None)
    approx = d["vol"] * d["close"] / 1000.0
    check("湖缺失: used_lake=False", not used)
    check("湖缺失: 全序列=旧近似口径", np.allclose(amt.to_numpy(), approx.to_numpy()))
    # 湖为空 dict 同样退回
    _, used2 = qd.merge_amount(d, {})
    check("湖缺失: 空映射退回", not used2)


# ── 3. 错日 skip ──────────────────────────────────────────
def test_fallback_stale():
    d = make_d()
    # 湖断更 1 日（缺昨日）→ 末根错位，整体退回近似
    lake = make_lake(d, offset=1)
    amt, used = qd.merge_amount(d, lake)
    approx = d["vol"] * d["close"] / 1000.0
    check("错日: used_lake=False（skip 湖口径）", not used)
    check("错日: 退回全近似", np.allclose(amt.to_numpy(), approx.to_numpy()))
    # 历史日期部分缺失（湖只覆盖一半）→ 同样退回
    half = {
        k: v
        for k, v in make_lake(d).items()
        if k < str(d["date"].iloc[len(d) // 2]).replace("-", "")
    }
    _, used3 = qd.merge_amount(d, half)
    check("错日: 历史未全覆盖退回", not used3)


# ── 4. 集成冒烟：intraday_recheck 端到端 ──────────────────
def test_intraday_recheck_integration():
    code = "000001.SZ"
    n = 150
    kl = []
    for i in range(n - 1):
        d = (YDAY - timedelta(days=n - 2 - i)).isoformat()
        kl.append([d, "10.0", "10.1", "10.3", "9.9", "5000"])
    kl.append([TODAY.isoformat(), "10.1", "10.2", "10.4", "10.0", "6000"])

    real_kd = qd.__dict__.get("kline_daily")
    try:
        import tools

        qd.kline_daily = tools.kline_daily = lambda *a, **k: kl
        # 引擎输出恒触发信号（隔离真实信号条件，专注量纲路径）
        fake_out = pd.DataFrame(
            {
                "double_knife_signal": [True] * n,
                "regime_label": ["range"] * n,
            }
        )
        real_qdk = qd.quant_double_knife
        qd.quant_double_knife = lambda df, profile="strict": fake_out
        # 湖缺失 → note 带 amount_approx
        with tempfile.TemporaryDirectory() as td:
            qd.ASHARE = Path(td)
            qd._LAKE_AMOUNT.clear()
            sigs, checked, note = qd.intraday_recheck([code])
            check("集成: 湖缺失仍复核通过", checked == 1 and len(sigs) == 1)
            check(
                "集成: 湖缺失 note 标注 amount_approx",
                "amount_approx" in sigs[0]["note"],
            )
            # 湖在且对齐 → note 无标注，历史 amount=湖值
            d = pd.DataFrame(
                kl, columns=["date", "open", "close", "high", "low", "vol"]
            )
            lake = make_lake(d)
            qd._LAKE_AMOUNT[code] = lake
            sigs, checked, _ = qd.intraday_recheck([code])
            check(
                "集成: 湖对齐时 note 无 amount_approx",
                checked == 1
                and len(sigs) == 1
                and "amount_approx" not in sigs[0]["note"],
            )
            # 湖断更错日 → 标注回来
            qd._LAKE_AMOUNT[code] = make_lake(d, offset=2)
            sigs, _, _ = qd.intraday_recheck([code])
            check(
                "集成: 错日 note 标注 amount_approx",
                len(sigs) == 1 and "amount_approx" in sigs[0]["note"],
            )
            # 输出格式不变（下游 qd_view/qd_adapter 依赖键集）
            check(
                "集成: 输出键集不变",
                set(sigs[0]) == {"code", "close", "regime", "note"},
            )
        qd.quant_double_knife = real_qdk
        if real_kd is not None:
            qd.kline_daily = real_kd
        tools.kline_daily = real_kd if real_kd else tools.kline_daily
    finally:
        pass


def main():
    test_lake_first()
    test_fallback_missing()
    test_fallback_stale()
    test_intraday_recheck_integration()
    n_fail = sum(1 for _, ok in PASS if not ok)
    print(
        "\n== qd amount 量纲修复回归: %d/%d PASS ==" % (len(PASS) - n_fail, len(PASS))
    )
    sys.exit(1 if n_fail else 0)


if __name__ == "__main__":
    main()
