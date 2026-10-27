# -*- coding: utf-8 -*-
"""eval_shadow J2 逐行后验修复测试（K-STOCK-397）。

覆盖：
  1. spearman 基础性质（正/负/并列/n<3）
  2. posterior_row 三分支（正常窗口 / 零持有期 pending / 决策日不在K线兜底）
  3. main() 逐行采样：零持有期行计入 pending 不入 J2，其余行入样且 rho 正确
  4. 全零后验告警（价格源哨）
  5. 决策字段缺失行 [skip] 可见（带日期）
入口范式：test_all()（P0 判例：顶层 sys.exit 会炸 pytest 收集）。
"""

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent  # agent_platform/
sys.path.insert(0, str(HERE))

import eval_shadow  # noqa: E402


def _reset(tmp, ledger_rows, kline_stub, quote_stub):
    """公共装置：账本落临时文件、断开真实 DB、打桩行情函数。"""
    fp = Path(tmp) / "shadow_ledger.jsonl"
    fp.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in ledger_rows),
        encoding="utf-8",
    )
    eval_shadow.LEDGER = fp
    eval_shadow.DB = Path(tmp) / "no_such_checkpoints.db"
    eval_shadow.kline_daily = kline_stub
    eval_shadow.quote_snapshot = quote_stub


def test_spearman():
    assert eval_shadow.spearman([1, 2, 3], [10, 20, 30]) == 1.0
    assert eval_shadow.spearman([1, 2, 3], [30, 20, 10]) == -1.0
    # 并列全同 → rx 方差 0 → den=0 → rho None（不可计算语义）
    assert eval_shadow.spearman([2, 2, 2], [1, 2, 3]) is None
    assert eval_shadow.spearman([1, 2], [1, 2]) is None  # n<3
    print("ok 1 spearman 基础性质")


def test_posterior_row():
    eval_shadow.quote_snapshot = lambda s: {"price": 105.0}  # 兜底分支打桩，不触网
    bars = [
        ["2026-10-08", 0, "100"],
        ["2026-10-09", 0, "105"],
        ["2026-10-12", 0, "110"],
    ]
    ret, hz, base, exit_px = eval_shadow.posterior_row(
        bars, "2026-10-08", 99.0, "600519"
    )
    assert abs(ret - 10.0) < 1e-9 and hz == 2 and base == 100.0 and exit_px == 110.0
    # 零持有期：决策日=最新 bar
    ret, hz, base, exit_px = eval_shadow.posterior_row(
        bars, "2026-10-12", 99.0, "600519"
    )
    assert ret == "pending" and hz == 0
    # 决策日不在 K 线：账本价作基准、快照作出场
    ret, hz, base, exit_px = eval_shadow.posterior_row(
        bars, "2026-10-05", 100.0, "600519"
    )
    assert abs(ret - 5.0) < 1e-9 and hz is None and base == 100.0 and exit_px == 105.0
    print("ok 2 posterior_row 三分支")


def test_main_per_row_sampling():
    tmp = tempfile.mkdtemp(prefix="evalshadow_t3_")

    def kline(sym, n=40):
        return {
            "600519": [
                ["2026-10-08", 0, "100"],
                ["2026-10-09", 0, "105"],
                ["2026-10-12", 0, "110"],
            ],
            "000858": [
                ["2026-10-08", 0, "200"],
                ["2026-10-09", 0, "190"],
                ["2026-10-12", 0, "180"],
            ],
        }[sym]

    def quote(sym):
        return {"price": 1.0}

    rows = []
    for d, sym, sc, px in [
        ("2026-10-08", "600519", 5, 100.0),
        ("2026-10-08", "000858", 1, 200.0),
        ("2026-10-09", "600519", 3, 105.0),
        ("2026-10-09", "000858", 2, 190.0),
    ]:
        rows.append(
            {
                "date": d,
                "symbol": sym,
                "name": sym,
                "status": "done",
                "score": sc,
                "confidence": 0.7,
                "price_at_decision": px,
                "recommendation": "BUY",
                "run_id": "r_%s_%s" % (d, sym),
            }
        )
    # 最新日追加一行（决策日=最新 bar → pending）
    rows.append(
        {
            "date": "2026-10-12",
            "symbol": "600519",
            "name": "600519",
            "status": "done",
            "score": 4,
            "confidence": 0.6,
            "price_at_decision": 110.0,
            "recommendation": "HOLD",
            "run_id": "r_pending",
        }
    )
    _reset(tmp, rows, kline, quote)
    buf = io.StringIO()
    argv_bak = sys.argv
    sys.argv = ["eval_shadow.py"]
    try:
        with contextlib.redirect_stdout(buf):
            eval_shadow.main()
    finally:
        sys.argv = argv_bak
    out = buf.getvalue()
    # 期望入样 4 行（10-12 行零持有期 pending）：
    # rets=[+10, -10, +4.76, -5.26] 对 scores=[5,1,3,2] 完全同序 → rho=1.0
    assert "[J2 秩相关] PASS" in out, out
    assert "rho=1.0" in out and "n=4" in out, out
    assert "[pending] 1 行零持有期" in out, out
    print("ok 3 逐行采样+pending 可见")


def test_zero_ret_warning():
    tmp = tempfile.mkdtemp(prefix="evalshadow_t4_")

    def kline(sym, n=40):
        return [
            ["2026-10-08", 0, "100"],
            ["2026-10-09", 0, "100"],
            ["2026-10-12", 0, "100"],
        ]

    rows = [
        {
            "date": "2026-10-08",
            "symbol": "600519",
            "name": "x",
            "status": "done",
            "score": 5,
            "confidence": 0.7,
            "price_at_decision": 100.0,
            "recommendation": "BUY",
            "run_id": "r1",
        }
    ]
    _reset(tmp, rows, kline, lambda s: {"price": 100.0})
    buf = io.StringIO()
    argv_bak = sys.argv
    sys.argv = ["eval_shadow.py"]
    try:
        with contextlib.redirect_stdout(buf):
            eval_shadow.main()
    finally:
        sys.argv = argv_bak
    out = buf.getvalue()
    assert "[warn] 全部后验收益=0" in out, out
    assert "rho=None" in out or "WAIT/FAIL" in out, out  # 零方差→den=0→rho None
    print("ok 4 全零后验告警")


def test_skip_visible():
    tmp = tempfile.mkdtemp(prefix="evalshadow_t5_")

    def kline(sym, n=40):
        return [["2026-10-08", 0, "100"], ["2026-10-12", 0, "110"]]

    rows = [
        {
            "date": "2026-10-08",
            "symbol": "300750",
            "name": "x",
            "status": "done",
            "score": None,
            "confidence": None,
            "price_at_decision": 286.75,
            "recommendation": "HOLD",
            "run_id": "r1",
        }
    ]
    _reset(tmp, rows, kline, lambda s: {"price": 110.0})
    buf = io.StringIO()
    argv_bak = sys.argv
    sys.argv = ["eval_shadow.py"]
    try:
        with contextlib.redirect_stdout(buf):
            eval_shadow.main()
    finally:
        sys.argv = argv_bak
    out = buf.getvalue()
    assert "[skip] 1 行决策字段缺失" in out and "2026-10-08/300750" in out, out
    print("ok 5 skip 行可见（带日期）")


def test_all():
    test_spearman()
    test_posterior_row()
    test_main_per_row_sampling()
    test_zero_ret_warning()
    test_skip_visible()
    print("ALL PASS: 5/5")


if __name__ == "__main__":
    test_all()
