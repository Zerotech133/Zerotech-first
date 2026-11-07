"""数据新鲜度闸门：筛选前校验日线是否已到「应到交易日」。

## 为什么需要它

`daily_screening.py` 取数走 `latest_date()` → `detect_latest_trade_date(DAILY_DIR)`，
即「目录里已有的最新日线」。而日线由 `increment_guard` 在**每日 20:30** 落盘
（实测 11-03/04/05 三天 mtime 均为 20:30）。

原计划任务在 **17:35** 跑，比落盘早 2h55m → 每天稳定拿到 T-1，且：

    rc=0、日志正常、无告警，只是报告里的数字是昨天的

2026-11-06 那次产出 `date=20261105` 即是此形态。这类「静默降级」比报错危险，
因为没人会去看报告头部的日期。

## 判据

    应到交易日 = 交易日历中 <= 今天 的最后一个 is_open=1
    实际数据日 = daily/ 下 d_YYYYMMDD.parquet 的最大日期
    实际 < 应到  →  rc=2（滞后，不跑筛选）
    无法校验    →  rc=3（日历缺失/为空，同样不跑）

设计取向：**fail-closed**。「无法校验」等于「不知道数据新不新」，此时跑出旧数据的
代价高于当天没有报告 —— 没有报告是显性的，旧数据是隐性的。

## 用法

    python data_freshness_gate.py [--json] [--today 20261107]

退出码：0 = 新鲜；2 = 滞后；3 = 无法校验。
"""

from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import sys
from pathlib import Path

DATA = Path(r"C:\aios\quant_backtest\data\tushare")
DEFAULT_DAILY = DATA / "daily"
DEFAULT_CAL = DATA / "trade_cal"

EXIT_OK = 0
EXIT_LAG = 2
EXIT_UNVERIFIABLE = 3


def _configure_stdio() -> None:
    """GBK 控制台下带中文的输出会 UnicodeEncodeError——与 increment_guard 同款处理。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def expected_trade_date(cal_dir: Path, today: str) -> tuple[str | None, str]:
    """返回 (应到交易日, 说明)。读不到日历时返回 (None, 原因)。"""
    files = sorted(glob.glob(str(cal_dir / "*.parquet")))
    if not files:
        return None, f"交易日历为空：{cal_dir}"
    try:
        import pandas as pd

        frames = [pd.read_parquet(f, columns=["cal_date", "is_open"]) for f in files]
        cal = pd.concat(frames, ignore_index=True)
    except Exception as exc:  # noqa: BLE001
        return None, f"交易日历读取失败：{type(exc).__name__}: {exc}"

    # 分片之间有重复（实测同一天最多 18 份），先按日期塌缩再取 max
    cal["cal_date"] = cal["cal_date"].astype(str)
    cal = cal[cal["cal_date"] <= today]
    open_days = cal.loc[cal["is_open"] == 1, "cal_date"]
    if open_days.empty:
        return None, f"交易日历中 {today} 之前无开市日"
    return str(open_days.max()), "ok"


def actual_data_date(daily_dir: Path) -> str | None:
    files = glob.glob(str(daily_dir / "d_*.parquet"))
    if not files:
        return None
    return max(os.path.basename(f)[2:10] for f in files)


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    ap = argparse.ArgumentParser(description="筛选前数据新鲜度闸门")
    ap.add_argument("--daily-dir", default=str(DEFAULT_DAILY))
    ap.add_argument("--cal-dir", default=str(DEFAULT_CAL))
    ap.add_argument("--today", default="", help="yyyyMMdd，仅用于演练")
    ap.add_argument("--json", action="store_true", help="输出单行 JSON")
    args = ap.parse_args(argv)

    today = args.today or dt.date.today().strftime("%Y%m%d")
    exp, why = expected_trade_date(Path(args.cal_dir), today)
    act = actual_data_date(Path(args.daily_dir))

    if exp is None:
        rc, status = EXIT_UNVERIFIABLE, "UNVERIFIABLE"
    elif act is None:
        rc, status = EXIT_LAG, "NO_DATA"
    elif act < exp:
        rc, status = EXIT_LAG, "LAG"
    else:
        rc, status = EXIT_OK, "OK"

    payload = {
        "status": status,
        "rc": rc,
        "today": today,
        "expected": exp,
        "actual": act,
        "reason": why,
        "lag_days_hint": "" if rc == EXIT_OK else "actual < expected",
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(
            f"[freshness] status={status} today={today} "
            f"expected={exp} actual={act} ({why})"
        )
    return rc


if __name__ == "__main__":
    sys.exit(main())
