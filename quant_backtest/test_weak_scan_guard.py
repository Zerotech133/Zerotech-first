# -*- coding: utf-8 -*-
"""weak_scan 行情日期护栏单测（K-QUANT-20261019-WEAKGUARD · 不碰生产数据）。

T1 expected_trade_day：交易日 15:35 → 期望=上一交易日（T-Q3 口径：
   tdx_sync 17:30 入湖，15:35 当日数据必然未到）
T2 expected_trade_day：周末（周六）→ 期望=上一交易日
T3 expected_trade_day：now 早于全部日历日 → None（护栏降级）
T4 expected_trade_day：日历目录空 → None
T5 check_trade_day 滞后：last_day 2026-10-16 < 期望 20261019 → exit 3
   + stderr 带 [WEAKGUARD] 明确日志
T6 check_trade_day 正常：last_day=期望日 → None（放行）
T7 check_trade_day 日历缺失：exp=None → WARN 放行（不硬拒）
T8 main() 集成（mock r20_table 滞后）：返回 3 且 tmp REP 零 CSV 落盘

隔离：日历用 tempfile 合成 parquet；main 集成 monkeypatch r20_table/
expected_trade_day/REP；全程不触数据湖与 reports/。全部 PASS 退出码 0。
"""

import glob
import io
import os
import sys
import tempfile
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pandas as pd  # noqa: E402
import weak_scan as ws  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print(
        "%s %s%s"
        % (
            "PASS" if cond else "FAIL",
            name,
            "｜" + detail if detail and not cond else "",
        )
    )
    if not cond:
        fails.append(name)


def make_cal(tmp, days):
    """合成 trade_cal 快照（SSE，is_open 全 1）。"""
    d = os.path.join(tmp, "cal")
    os.makedirs(d, exist_ok=True)
    pd.DataFrame(
        {"exchange": ["SSE"] * len(days), "cal_date": days, "is_open": [1] * len(days)}
    ).to_parquet(os.path.join(d, "part_0000.parquet"))
    return d


# --- T1-T4 expected_trade_day ---
tmp = tempfile.mkdtemp(prefix="weakguard_")
cal = make_cal(tmp, ["20261015", "20261016", "20261019", "20261020"])
check(
    "T1 交易日 15:35 期望=上一交易日",
    ws.expected_trade_day(now=datetime(2026, 10, 19, 15, 35), cal_dir=cal)
    == "20261016",
)
check(
    "T2 周末期望=上一交易日",
    ws.expected_trade_day(now=datetime(2026, 10, 17, 10, 0), cal_dir=cal) == "20261016",
)
check(
    "T3 早于全部日历日→None",
    ws.expected_trade_day(now=datetime(2026, 10, 14, 15, 35), cal_dir=cal) is None,
)
check(
    "T4 空日历目录→None",
    ws.expected_trade_day(
        now=datetime(2026, 10, 19, 15, 35), cal_dir=os.path.join(tmp, "nope")
    )
    is None,
)

# --- T5-T7 check_trade_day ---
err = io.StringIO()
_old = sys.stderr
sys.stderr = err
try:
    check("T5 滞后→exit 3", ws.check_trade_day("2026-10-16", exp="20261019") == 3)
    check(
        "T5b 滞后日志带 WEAKGUARD", "[WEAKGUARD]" in err.getvalue(), err.getvalue()[:80]
    )
    err.truncate(0)
    err.seek(0)
    check("T6 正常→放行", ws.check_trade_day("2026-10-19", exp="20261019") is None)
    check("T6b 正常无 WEAKGUARD 日志", "[WEAKGUARD]" not in err.getvalue())
    err.truncate(0)
    err.seek(0)
    check("T7 日历缺失→WARN 放行", ws.check_trade_day("2026-10-16", exp=None) is None)
    check("T7b 降级 WARN 留痕", "降级 WARN" in err.getvalue())
finally:
    sys.stderr = _old

# --- T8 main() 集成：mock 滞后场景，拒绝且零 CSV ---
rep = os.path.join(tmp, "rep")
os.makedirs(rep, exist_ok=True)
_old_rt, _old_et, _old_rep, _old_argv = (
    ws.r20_table,
    ws.expected_trade_day,
    ws.REP,
    sys.argv,
)
try:
    ws.r20_table = lambda days: (pd.DataFrame({"r": [1.0]}), "2026-10-16")
    ws.expected_trade_day = lambda now=None, cal_dir=None: "20261019"
    ws.REP = rep
    sys.argv = ["weak_scan.py"]
    rc = ws.main()
finally:
    ws.r20_table, ws.expected_trade_day, ws.REP, sys.argv = (
        _old_rt,
        _old_et,
        _old_rep,
        _old_argv,
    )
check("T8 滞后 main→3", rc == 3, str(rc))
check("T8b 滞后零 CSV 落盘", not glob.glob(os.path.join(rep, "*.csv")))

print(
    "\n%s"
    % ("全部 PASS" if not fails else "FAIL %d 项: %s" % (len(fails), "、".join(fails)))
)
sys.exit(1 if fails else 0)
