# -*- coding: utf-8 -*-
"""雪球情报出口 mixin（两线预案 M-RT3 消费端门户挂载，2026-10-18）。

GET /api/xq/intel?date=YYYY-MM-DD
    → 情报日报机读投影：读 automation_runs/xq_report/daily/xq_intel_YYYYMMDD.json；
      缺省 date → latest.json 固定名副本（19:00 automation 01f1a51c 产出）。

GET /api/xq/intel/dates
    → 近 N 日有产物日报日期导航（扫 daily/ 文件名，降序，最多 14 个）；
      供门户 xq_intel.html 日期快捷 chips 消费，只读零参数。

GET /api/xq/intel/ticker?code=600519&days=7
    → 标的下钻聚合：扫 daily/ 最近 days 天 xq_intel_*.json，按 code 聚合
      高置信命中条目（按日期降序）、异动信号、情绪计数；纯文件聚合只读，
      不查 SQLite、不 import quant_backtest（门户不进库契约）。

GET /api/xq/intel/coverage
    → 分类覆盖进度卡（读 automation_runs/xq_classify/coverage_snapshot.json，
      由生成器侧 quant_backtest/tools/classify_coverage.py 定期落盘）；
      纯文件读，不查 SQLite、不 import quant_backtest（门户不进库契约）；
      快照缺失返回 snapshot_exists=false 合法空态，坏 JSON 带 error fail-visible。

只读纪律（沿 api_capability 判例）：mode=ro / no-store / fail-visible——
报告未生成返回 report_exists=false 投影而非 500（观测面，首跑前为常态）；
坏 JSON 返回 error 字段（不吞异常）；date 非法 400。
门户不 import quant_backtest 任何模块（依赖链隔离判例）：日报文件是唯一契约，
生成器换实现不影响出口。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

# A8（2026-11-10）：路径锚统一走 env AIOS_ROOT 覆盖 + C:/aios 别名默认
# （与 api_daily_data REPO 判例一致），不再写死双斜杠全路径。
REPO = Path(os.environ.get("AIOS_ROOT", "C:/aios"))
REPORT_ROOT = REPO / "automation_runs" / "xq_report"
COVERAGE_SNAPSHOT = REPO / "automation_runs" / "xq_classify" / "coverage_snapshot.json"
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TICKER_CODE_RE = re.compile(r"^[0-9A-Za-z]{4,6}$")
_TICKER_DAYS_RE = re.compile(r"^\d{1,2}$")


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


def read_xq_intel_ticker(code: str, days: int = 7) -> dict:
    """按标的聚合最近 days 天日报（纯文件只读，fail-visible）。

    扫 daily/ xq_intel_YYYYMMDD.json 文件名取最近 days 天（降序），
    聚合该 code 的：high_confidence 命中条目（日期降序）、anomaly_signals 命中、
    情绪计数（从 high_confidence 统计）。单日文件坏 JSON 记入 errors 不吞异常。
    """
    out = {"code": code, "days": days, "dates_found": 0,
           "high_confidence": [], "anomaly": [],
           "sentiment_count": {"bullish": 0, "neutral": 0, "bearish": 0}}
    try:
        d = REPORT_ROOT / "daily"
        stems = []
        if d.is_dir():
            for p in d.iterdir():
                m = re.fullmatch(r"xq_intel_(\d{8})\.json", p.name)
                if m:
                    stems.append(m.group(1))
        stems.sort(reverse=True)
        stems = stems[:days]
        out["dates_found"] = len(stems)
        errors = []
        for s in stems:
            date = "%s-%s-%s" % (s[:4], s[4:6], s[6:])
            try:
                data = json.loads((d / ("xq_intel_%s.json" % s)).read_text(encoding="utf-8"))
            except Exception as e:  # noqa: BLE001
                errors.append({"date": date, "error": repr(e)})
                continue
            if not isinstance(data, dict):
                errors.append({"date": date, "error": "top-level json is not a dict"})
                continue
            for r in data.get("high_confidence") or []:
                if isinstance(r, dict) and r.get("code") == code:
                    out["high_confidence"].append({
                        "date": date,
                        "confidence": r.get("confidence"),
                        "sentiment": r.get("sentiment"),
                        "event_type": r.get("event_type"),
                        "title": r.get("title"),
                    })
                    st = r.get("sentiment")
                    if st in out["sentiment_count"]:
                        out["sentiment_count"][st] += 1
            for r in data.get("anomaly_signals") or []:
                if isinstance(r, dict) and r.get("code") == code:
                    out["anomaly"].append({
                        "date": date,
                        "n": r.get("n"),
                        "bearish": r.get("bearish"),
                        "baseline6d": r.get("baseline6d"),
                    })
        # 文件按日期降序扫描 → 命中天然日期降序，无需再排
        if errors:
            out["errors"] = errors
        return out
    except Exception as e:  # noqa: BLE001
        out["error"] = "scan failed: %r" % e
        return out


def read_xq_intel_coverage() -> dict:
    """分类覆盖进度卡（纯文件读快照，fail-visible）。

    快照由生成器侧 classify_coverage.py 落盘（门户只消费 JSON 不进库）；
    文件缺失是合法空态（首跑前），返回 snapshot_exists=false；
    解析失败带 error 键不吞异常。
    """
    if not COVERAGE_SNAPSHOT.exists():
        return {"snapshot_exists": False,
                "hint": "快照由 quant_backtest/tools/classify_coverage.py 生成"}
    try:
        data = json.loads(COVERAGE_SNAPSHOT.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"snapshot_exists": True, "source": COVERAGE_SNAPSHOT.name,
                "error": "bad json: %r" % e}
    if isinstance(data, dict):
        data["snapshot_exists"] = True
        data["source"] = COVERAGE_SNAPSHOT.name
    return data


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

    def _xq_intel_ticker(self, qs):
        # 标的下钻出口：code 必填（防注入白名单正则）、days 可选 1-30、其余参数 400
        args = self._norm_qs(qs)
        extra = [k for k in args if k not in ("code", "days")]
        if extra:
            self._json({"error": "非法参数: %s" % ",".join(sorted(extra))}, 400)
            return
        code = (args.get("code") or [""])[0].strip()
        if not _TICKER_CODE_RE.match(code):
            self._json({"error": "code 必填且须为 4-6 位数字/字母"}, 400)
            return
        days_s = (args.get("days") or [""])[0].strip()
        if days_s:
            if not _TICKER_DAYS_RE.match(days_s) or not 1 <= int(days_s) <= 30:
                self._json({"error": "days 须为 1-30 整数"}, 400)
                return
            days = int(days_s)
        else:
            days = 7
        self._json(read_xq_intel_ticker(code, days))

    def _xq_intel_coverage(self, qs):
        # 分类覆盖进度卡出口：零参数白名单，带任何参数走 400（api_perf 判例）
        if self._norm_qs(qs):
            self._json({"error": "本出口不接受任何参数"}, 400)
            return
        self._json(read_xq_intel_coverage())
