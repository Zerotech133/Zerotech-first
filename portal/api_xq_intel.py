# -*- coding: utf-8 -*-
"""雪球情报出口 mixin（两线预案 M-RT3 消费端门户挂载，2026-10-18）。

GET /api/xq/intel?date=YYYY-MM-DD
    → 情报日报机读投影：读 automation_runs/xq_report/daily/xq_intel_YYYYMMDD.json；
      缺省 date → latest.json 固定名副本（19:00 automation 01f1a51c 产出）。

GET /api/xq/intel/dates
    → 近 N 日有产物日报日期导航（扫 daily/ 文件名，降序，最多 14 个）；
      供门户 xq_intel.html 日期快捷 chips 消费，只读零参数。

只读纪律（沿 api_capability 判例）：mode=ro / no-store / fail-visible——
报告未生成返回 report_exists=false 投影而非 500（观测面，首跑前为常态）；
坏 JSON 返回 error 字段（不吞异常）；date 非法 400。
门户不 import quant_backtest 任何模块（依赖链隔离判例）：日报文件是唯一契约，
生成器换实现不影响出口。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPORT_ROOT = Path(r"C:/aios/automation_runs/xq_report")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def read_xq_intel(date_str: str = "") -> dict:
    """单日/最新日报 JSON 只读投影（fail-visible）。"""
    if date_str:
        if not _DATE_RE.match(date_str):
            return {"report_exists": False,
                    "error": "date 须为 YYYY-MM-DD"}
        p = REPORT_ROOT / "daily" / ("xq_intel_%s.json" % date_str.replace("-", ""))
    else:
        p = REPORT_ROOT / "latest.json"
    if not p.exists():
        # 报告未生成是合法观测态（首跑前/当日未到 19:00），不是错误
        return {"report_exists": False, "source": str(p),
                "hint": "日报由 xq_daily_report.py 每日 19:00 生成"}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"report_exists": True, "source": str(p),
                "error": "bad json: %r" % e}
    if isinstance(data, dict):
        data.setdefault("report_exists", True)
        data["source"] = p.name
    return data


def list_xq_intel_dates(limit: int = 14) -> dict:
    """近 N 日有产物日报日期列表（扫 daily/ 文件名，降序，fail-visible 空列表）。

    扫描目录属观测态：daily/ 缺失（首跑前）返回空 dates 而非报错；
    扫描本身异常（权限等）则带 error 键 fail-visible，不吞异常。
    """
    try:
        d = REPORT_ROOT / "daily"
        dates = set()
        if d.is_dir():
            for p in d.iterdir():
                m = re.fullmatch(r"xq_intel_(\d{8})\.json", p.name)
                if m:
                    s = m.group(1)
                    dates.add("%s-%s-%s" % (s[:4], s[4:6], s[6:]))
        return {"dates": sorted(dates, reverse=True)[:limit]}
    except Exception as e:  # noqa: BLE001
        return {"dates": [], "error": "scan failed: %r" % e}


class Handler:
    def _xq_intel(self, qs):
        args = self._norm_qs(qs)
        date_str = (args.get("date") or [""])[0].strip()
        if date_str and not _DATE_RE.match(date_str):
            # fail-visible：参数白名单外走 400（api_perf 判例），不静默降级成空投影
            self._json({"error": "date 须为 YYYY-MM-DD"}, 400)
            return
        self._json(read_xq_intel(date_str))

    def _xq_intel_dates(self, qs):
        # 零参数出口：参数白名单为空集，带任何参数走 400（api_perf 判例）
        if self._norm_qs(qs):
            self._json({"error": "本出口不接受任何参数"}, 400)
            return
        self._json(list_xq_intel_dates())
