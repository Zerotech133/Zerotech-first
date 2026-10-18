# -*- coding: utf-8 -*-
"""雪球情报出口 mixin（两线预案 M-RT3 消费端门户挂载，2026-10-18）。

GET /api/xq/intel?date=YYYY-MM-DD
    → 情报日报机读投影：读 automation_runs/xq_report/daily/xq_intel_YYYYMMDD.json；
      缺省 date → latest.json 固定名副本（19:00 automation 01f1a51c 产出）。

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


class Handler:
    def _xq_intel(self, qs):
        args = self._norm_qs(qs)
        date_str = (args.get("date") or [""])[0].strip()
        self._json(read_xq_intel(date_str))
