# -*- coding: utf-8 -*-
"""N7 冻结时点与业务日消费时点对齐自动化（六度选股 · 门②/R1 收口，2026-10-21 老田拍板实施）。

背景：R1 试运行 `outputs/r0_m3_20261020/execute_trial.py` 用 `LIVE_POOL.stat().st_mtime`
作为「冻结时点」——即**人工运行脚本那一刻的文件 mtime**，与「被消费的业务日」无关，
属 R1 报告已披露的「冻结时点为人工驱动（非业务日消费时点对齐）」偏差。

本模块把冻结锚定到「被消费的业务日 + 该日规范消费时点（交易日收盘）」，并做成
**可调度、确定性（给定 pool 路径与业务日即幂等）**，从而取消人工驱动依赖。

设计要点：
- 逻辑冻结时点 = business_day 的规范消费时点（默认 A 股收盘 15:00 Asia/Shanghai；
  可传入 `calendar_provider.CalendarProvider` 覆盖为真实会话边界，生产权威）。
- 物理冻结产物（frozen copy + sha256）只依赖 (pool 内容, business_day)，与「何时运行」无关
  → 可调度、幂等；`frozen_at` 仅记录实际执行墙钟，供审计。
- 纯函数 + 仅标准库（zoneinfo），不触网/库/生产。
- **默认不改动**既有 `execute_trial.py`（成熟 R1 链保留）；本模块为 N7 独立交付，
  R2 接入时替换其 mtime 用法即可。

红线：研究/自动化副本；不触生产、不造数、不覆盖既有产物目录（除非同 business_day 幂等刷新）。
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import date, datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import logging

DEFAULT_TZ = "Asia/Shanghai"
A_SHARE_CLOSE = time(15, 0, 0)  # A 股规范消费时点（收盘）回退默认


def _coerce_date(value):
    """YYYYMMDD / YYYY-MM-DD / date / datetime → date。"""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip().replace("-", "")
    return date(int(s[:4]), int(s[4:6]), int(s[6:8]))


def d_iso(value) -> str:
    return _coerce_date(value).isoformat()


def business_day_consumption_timepoint(
    business_day, *, tz=DEFAULT_TZ, close=A_SHARE_CLOSE, provider=None
):
    """被消费业务日的规范消费时点（**逻辑冻结时点**）。

    - 若传入 `provider`（实现 `calendar_provider.CalendarProvider` 的 `session_bounds`），
      优先用该日真实会话 `close_at`（生产权威）；失败/缺失则回退默认。
    - 否则回退 A 股收盘 15:00 Asia/Shanghai（确定性默认，足够冻结对齐）。
    返回 timezone-aware datetime（Asia/Shanghai）。
    """
    d = _coerce_date(business_day)
    if provider is not None:
        try:
            sess = provider.session_bounds(d)
            if sess.close_at is not None:
                return sess.close_at.astimezone(ZoneInfo(tz))
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/tools/freeze_alignment.py", exc)
    return datetime.combine(d, close, tzinfo=ZoneInfo(tz))


def freeze_pool(
    pool_path, business_day, out_dir, *, provider=None, consumer="freeze_alignment"
):
    """确定性冻结：读 live pool 一次，按 business_day 锚定逻辑消费时点，写 frozen copy。

    返回冻结记录 dict：
      source / sha256 / business_day / consumption_timepoint(ISO) /
      frozen_at(ISO 墙钟) / frozen_copy / consumer

    - 逻辑字段（business_day / consumption_timepoint / sha256）只依赖 (pool 内容, business_day)，
      与运行时刻无关 → 可调度、幂等（同 business_day 重复跑 sha256 不变）。
    - frozen_at 记录实际执行墙钟（审计用），与 consumption_timepoint 解耦。
    """
    pool_path = Path(pool_path)
    if not pool_path.exists():
        raise FileNotFoundError(pool_path)
    raw = pool_path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    frozen_copy = out_dir / "pool_freeze_reference.jsonl"
    frozen_copy.write_bytes(raw)  # 幂等：同内容覆盖，sha 不变
    ctp = business_day_consumption_timepoint(business_day, provider=provider)
    frozen_at = datetime.now(timezone.utc).astimezone(ZoneInfo(DEFAULT_TZ))
    rec = {
        "source": str(pool_path),
        "sha256": sha,
        "business_day": d_iso(business_day),
        "consumption_timepoint": ctp.isoformat(),
        "frozen_at": frozen_at.isoformat(),
        "frozen_copy": str(frozen_copy),
        "consumer": consumer,
    }
    (out_dir / "freeze_record.json").write_text(
        json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return rec


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="N7 冻结时点与业务日消费时点对齐")
    ap.add_argument("--pool", required=True, help="live pool 路径 (chanlun_pool.jsonl)")
    ap.add_argument("--day", required=True, help="业务日 YYYYMMDD 或 YYYY-MM-DD")
    ap.add_argument(
        "--out",
        default=None,
        help="冻结产物目录（默认 <pool_dir>/freeze_<day>）；同 business_day 幂等刷新",
    )
    ap.add_argument("--consumer", default="cli")
    a = ap.parse_args(argv)
    out = a.out or str(Path(a.pool).parent / f"freeze_{d_iso(a.day).replace('-', '')}")
    rec = freeze_pool(a.pool, a.day, out, consumer=a.consumer)
    print(json.dumps(rec, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
