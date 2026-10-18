# -*- coding: utf-8 -*-
"""市场推送矩阵解析器（K-ORG-197 轨道A，W32 提前实装 2026-10-19）。

职责（纯逻辑，零触网零触库）：
  - load_matrix()          读 portal/push_matrix.json（fail-visible，坏配置不吞）
  - us_dst_active(d)       美国夏令时判定（3 月第 2 个周日 ~ 11 月第 1 个周日）
  - resolve_windows(mkt,d) 窗口解析：us 市场冬季自动 +winter_shift_minutes
  - in_window(now)         当前时刻落在哪个市场哪个窗口
  - next_window(now)       下一个即将开始的窗口（跨日扫描）
  - is_quiet(now, tier)    静默窗判定（critical 豁免）
  - anchor_status(id)      数据锚就绪判定：mtime_today 规则（缺失/过期可机读）

只读纪律：不写任何文件、不发任何请求；时间统一用 naive 本地（北京）语义，
与计划任务/跑批时刻同源。消费方：deadline_watchdog（轨道B）与
push_matrix_send（轨道C），配置契约见 portal/push_matrix.json。
"""

from __future__ import annotations

import calendar
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path

REPO = Path(os.environ.get("AIOS_ROOT", "C:/aios"))
MATRIX_JSON = REPO / "portal" / "push_matrix.json"


def load_matrix(path: Path | None = None) -> dict:
    """读矩阵配置；缺失/坏 JSON fail-visible（KeyError/ValueError 上抛）。"""
    p = Path(path) if path else MATRIX_JSON
    data = json.loads(p.read_text(encoding="utf-8"))
    for key in ("version", "quiet_hours", "markets", "anchors", "templates"):
        if key not in data:
            raise KeyError("push_matrix.json 缺少必需键: %s" % key)
    return data


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """某月第 n 个周X（weekday: 0=周一 .. 6=周日）。"""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def us_dst_active(d: date) -> bool:
    """美国夏令时：3 月第 2 个周日起 ~ 11 月第 1 个周日止（含两端边界日）。"""
    start = _nth_weekday(d.year, 3, calendar.SUNDAY, 2)
    end = _nth_weekday(d.year, 11, calendar.SUNDAY, 1)
    return start <= d < end  # 结束日当天已切回冬令时（11 月第 1 个周日凌晨切）


def _hhmm(s: str) -> tuple[int, int]:
    hh, mm = s.split(":")
    return int(hh), int(mm)


def resolve_windows(market_cfg: dict, d: date) -> list[dict]:
    """解析某市场某日窗口：us 夏令时口径声明，冬季整体 +winter_shift_minutes。"""
    shift = 0
    if market_cfg.get("dst") == "us" and not us_dst_active(d):
        shift = int(market_cfg.get("winter_shift_minutes", 60))
    out = []
    for w in market_cfg.get("windows", []):
        w = dict(w)
        if shift:
            for key in ("start", "end", "deadline"):
                hh, mm = _hhmm(w[key])
                t = datetime(2000, 1, 1, hh, mm) + timedelta(minutes=shift)
                w[key] = t.strftime("%H:%M")
        w["_shift_min"] = shift
        out.append(w)
    return out


def _at(d: date, hhmm: str) -> datetime:
    hh, mm = _hhmm(hhmm)
    return datetime(d.year, d.month, d.day, hh, mm)


def in_window(now: datetime, matrix: dict) -> tuple[str | None, dict | None]:
    """now（naive 本地）落窗判定 → (market, window) 或 (None, None)。"""
    d = now.date()
    for mkt, cfg in matrix["markets"].items():
        for w in resolve_windows(cfg, d):
            if _at(d, w["start"]) <= now < _at(d, w["end"]):
                return mkt, w
    return None, None


def next_window(now: datetime, matrix: dict,
                scan_days: int = 2) -> tuple[str | None, dict | None, datetime | None]:
    """下一个开始时间 > now 的窗口（今明两天扫描，跨市场按开始时间取最早）。"""
    cands: list[tuple[datetime, str, dict]] = []
    for off in range(scan_days):
        d = now.date() + timedelta(days=off)
        for mkt, cfg in matrix["markets"].items():
            for w in resolve_windows(cfg, d):
                st = _at(d, w["start"])
                if st > now:
                    cands.append((st, mkt, w))
    if not cands:
        return None, None, None
    st, mkt, w = min(cands, key=lambda x: x[0])
    return mkt, w, st


def is_quiet(now: datetime, matrix: dict, tier: str = "daily") -> bool:
    """静默窗（quiet_hours 跨午夜）内且非 critical 档 → True。"""
    if tier in matrix.get("critical_tiers", []):
        return False
    q = matrix["quiet_hours"]
    hh, mm = _hhmm(q["start"])
    q_start = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    hh, mm = _hhmm(q["end"])
    q_end = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if q_start <= q_end:  # 同日窗（防御配置）
        return q_start <= now < q_end
    return now >= q_start or now < q_end  # 跨午夜 22:00-07:30


def anchor_status(anchor_id: str, matrix: dict,
                  now: datetime | None = None,
                  repo: Path | None = None) -> dict:
    """数据锚就绪判定（mtime_today 规则）。

    返回 {status: ok|missing|stale|disabled|unknown, detail, mtime}。
    stale = 文件存在但 mtime 非今日 / 早于 min_mtime；disabled/unknown 不判
    迟到，由调用方跳过（预研期未实核路径不产生假告警）。
    """
    a = (matrix.get("anchors") or {}).get(anchor_id)
    if a is None:
        return {"status": "unknown", "detail": "矩阵未注册锚 %s" % anchor_id,
                "mtime": None}
    if not a.get("enabled", True):
        return {"status": "disabled", "detail": "锚未启用（%s）" % anchor_id,
                "mtime": None}
    root = Path(repo) if repo else REPO
    p = root / a["path"]
    if not p.exists():
        return {"status": "missing", "detail": "锚文件缺失: %s" % a["path"],
                "mtime": None}
    mt = datetime.fromtimestamp(p.stat().st_mtime)
    now = now or datetime.now()
    today = now.date()
    if mt.date() != today:
        return {"status": "stale", "detail": "mtime=%s 非今日" % mt.strftime(
            "%Y-%m-%d %H:%M:%S"), "mtime": mt}
    min_mt = a.get("min_mtime")
    if min_mt:
        hh, mm = _hhmm(min_mt)
        if mt < datetime.combine(today, datetime.min.time()).replace(
                hour=hh, minute=mm):
            return {"status": "stale", "detail": "mtime=%s 早于 %s" % (
                mt.strftime("%H:%M:%S"), min_mt), "mtime": mt}
    return {"status": "ok", "detail": "", "mtime": mt}
