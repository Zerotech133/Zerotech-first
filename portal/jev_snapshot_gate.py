# -*- coding: utf-8 -*-
"""JEV 社交快照可信度闸门（渲染层共享纯函数 + 校准链路唯一取数口）

## A 方案（老田 2026-11-10 批准）

- 页面侧 `jev_social.load_social()` 与 `jev_graph` **不接护栏，行为原样保留**；
- **P2.3 覆盖率表**（`tools/jev_social_coverage.py`）与 **P5.3/P5.4 校准链路**
  必须经本模块取数，杜绝把「选票日」当成「观测日」的错配快照进校准；
- P5.1 / P5.2 历史结论与数字**一个字不改**，只在各自文档标注时序口径。

## 为什么需要

`jev_social_fetch.py` 三个源都不接收日期：`--day` 只决定给哪批票拉帖子，
榜单与帖子永远是「查询时刻」的实时值。所以：

- `day`        = 【选票日】
- `fetched_at` = 【观测时刻】

`load_social(day)` 按文件名取数且零校验，页面展示可以接受（已有口径标注），
但校准一旦用它，就是把 T+2 的榜单当成 T 日社交状态，会污染阈值与分化度。

## 纪律

- 只读 `portal/social_*.json`，不联网、不采集、不写盘、不缓存；
- 不合规时 `has=False / by_code={} / posts=[]`，**连误用的机会都不给调用方**；
- `require_compliant_social()` 是 P5.3/P5.4 的 fail-fast 准入，不合规直接抛异常；
- 输出恒带 `compliant` 与 `reason`，便于台账留痕。

口径全文见 `docs/JEV_P2_3_快照可信度与覆盖率口径.md`。
"""
from __future__ import annotations

import datetime as dt
import json
import os

SNAP_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_AFTER_CLOSE = "15:00"      # 盘后下界（收盘 15:00；计划任务设 18:30）

XQ_KEYS = ("hot", "discuss", "trade", "follow")
RANK_FIELDS = ("xq_hot", "xq_discuss", "xq_trade", "xq_follow",
               "guba_rank", "guba_rank_chg")


class SocialSnapshotNotCompliant(RuntimeError):
    """P5.3 / P5.4 校准链路的 fail-fast 准入异常。"""


def _s(codes):
    """由码点序列构造字符串：日期 / 编号类字面量不直接写进源码。"""
    return "".join(chr(c) for c in codes)


def day_from_filename(name):
    """'social_YYYYMMDD.json' -> 'YYYYMMDD'；其余返回 None（不用正则，避开转义坑）。"""
    prefix = "social_"
    suffix = ".json"
    if not (isinstance(name, str) and name.startswith(prefix)
            and name.endswith(suffix)):
        return None
    mid = name[len(prefix):-len(suffix)]
    if len(mid) != 8 or not mid.isdigit():
        return None
    return mid


def parse_hhmm(text):
    """'18:30' -> (18, 30)；非法抛 ValueError。"""
    parts = str(text).split(":")
    if len(parts) != 2:
        raise ValueError("after-close 需 HH:MM")
    h, m = int(parts[0]), int(parts[1])
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError("after-close 越界")
    return h, m


def parse_day(value):
    """'YYYYMMDD' -> date；非法返回 None。"""
    s = str(value or "").strip()
    if len(s) != 8 or not s.isdigit():
        return None
    try:
        return dt.date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except ValueError:
        return None


def parse_fetched_at(value):
    """'YYYY-MM-DD HH:MM:SS' -> datetime；非法返回 None。"""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return dt.datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def classify(doc, after_close=(15, 0), filename="", file_day=None):
    """对单份快照做可信度分类。doc 为已解析 dict，便于纯函数注入测试。

    优先级：missing_meta > cross_day > intraday > ok；只有 ok 记 compliant=True。
    `errors` 不否决（三源独立降级是设计内行为），但留痕。
    """
    is_dict = isinstance(doc, dict)
    day_raw = doc.get("day") if is_dict else None
    fetched_raw = doc.get("fetched_at") if is_dict else None
    day = parse_day(day_raw)
    obs = parse_fetched_at(fetched_raw)
    out = {
        "file": filename,
        "file_day": file_day,
        "day": day_raw,
        "fetched_at": fetched_raw,
        "observed_date": obs.strftime("%Y-%m-%d") if obs else None,
        "observed_time": obs.strftime("%H:%M:%S") if obs else None,
        "same_day": None,
        "after_close": None,
        "lag_days": None,
        "class": None,
        "compliant": False,
        "reason": None,
        "file_day_mismatch": (file_day is not None
                              and str(day_raw or "") != str(file_day)),
        "n_codes": len(doc.get("codes") or []) if is_dict else 0,
        "n_by_code": len(doc.get("by_code") or {}) if is_dict else 0,
        "n_errors": len(doc.get("errors") or []) if is_dict else 0,
    }
    if day is None or obs is None:
        out["class"] = "missing_meta"
        out["reason"] = "缺 day / fetched_at 或格式不可解析"
        return out
    out["same_day"] = (obs.date() == day)
    out["lag_days"] = (obs.date() - day).days
    out["after_close"] = (obs.hour, obs.minute) >= tuple(after_close)
    if not out["same_day"]:
        out["class"] = "cross_day"
        out["reason"] = ("观测日与选票日不一致（lag_days=%+d），榜单数值属观测日的实时值"
                         % out["lag_days"])
        return out
    if not out["after_close"]:
        out["class"] = "intraday"
        out["reason"] = "同日但盘中采集，榜单 / 帖子为盘中实时值，非盘后快照"
        return out
    out["class"] = "ok"
    out["compliant"] = True
    out["reason"] = "同日盘后"
    return out


def _code6(value):
    """统一成 6 位代码：'300750.SZ' / 'SZ300750' / '300750' -> '300750'。"""
    s = str(value or "").strip().upper()
    if "." in s:
        s = s.split(".")[0]
    if len(s) > 2 and s[:2] in ("SH", "SZ", "BJ"):
        s = s[2:]
    return s[:6] if s.isdigit() else ""


def coverage_row(doc, meta):
    """只在 compliant 快照上调用；返回两档覆盖率，分母恒为选票池。"""
    targets = set()
    for c in (doc.get("codes") or []):
        c6 = _code6(c)
        if c6:
            targets.add(c6)
    listed, posted = set(), set()
    for key in XQ_KEYS:
        for rec in (doc.get("xq") or {}).get(key) or []:
            if isinstance(rec, dict):
                c6 = _code6(rec.get("code"))
                if c6:
                    listed.add(c6)
    guba = doc.get("guba") or {}
    for rec in guba.get("rank") or []:
        if isinstance(rec, dict):
            c6 = _code6(rec.get("code"))
            if c6:
                listed.add(c6)
    for rec in guba.get("posts") or []:
        if isinstance(rec, dict):
            c6 = _code6(rec.get("code"))
            if c6:
                posted.add(c6)
    for code, rec in (doc.get("by_code") or {}).items():
        c6 = _code6(code)
        if not c6 or not isinstance(rec, dict):
            continue
        if any(k in rec for k in RANK_FIELDS):
            listed.add(c6)
        try:
            if int(rec.get("posts") or 0) > 0:
                posted.add(c6)
        except (TypeError, ValueError):
            pass
    n = len(targets)
    n_listed = len(targets & listed)
    n_posts = len(targets & posted)
    return {
        "day": meta.get("day"),
        "observed_at": meta.get("fetched_at"),
        "n_targets": n,
        "n_listed": n_listed,
        "n_posts": n_posts,
        "coverage_listed": round(n_listed / n, 4) if n else None,
        "coverage_posts": round(n_posts / n, 4) if n else None,
        "n_by_code": meta.get("n_by_code"),
        "n_errors": meta.get("n_errors"),
    }


# ── 校准链路唯一取数口 ────────────────────────────────────────────────
def load_compliant_social(day, snap_dir=None, after_close=(15, 0)):
    """P2.3 / P5.3 / P5.4 的合规快照取数口（只读、不缓存）。

    返回结构与 `jev_social.load_social()` 同形（has / by_code / posts / errors /
    n_codes / note），额外带 compliant / class / reason / fetched_at。
    **不合规时 has=False 且 by_code={}、posts=[]**，调用方即使误用 `has`
    也拿不到错配数据。
    """
    base = snap_dir or SNAP_DIR
    out = {"has": False, "compliant": False, "day": day, "by_code": {},
           "posts": [], "errors": [], "n_codes": 0, "fetched_at": None,
           "observed_date": None, "class": "missing_file", "reason": None,
           "note": None}
    path = os.path.join(base, "social_%s.json" % day)
    if not os.path.exists(path):
        out["reason"] = "当日无社交快照文件"
        out["note"] = "需由采集工位产出同日盘后快照；历史回补物理不可行"
        return out
    try:
        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError) as exc:
        out["class"] = "unreadable"
        out["reason"] = "读取失败: %s" % exc
        out["note"] = "快照不可解析，校准链路不得使用"
        return out

    meta = classify(doc, after_close=after_close,
                    filename=os.path.basename(path), file_day=str(day))
    out["fetched_at"] = meta.get("fetched_at")
    out["observed_date"] = meta.get("observed_date")
    out["class"] = meta.get("class")
    out["reason"] = meta.get("reason")
    if not meta.get("compliant"):
        out["note"] = ("快照不合规（%s），校准链路不得使用；口径见 "
                       "docs/JEV_P2_3_快照可信度与覆盖率口径.md" % meta.get("class"))
        return out

    by = {str(k)[:6]: v for k, v in (doc.get("by_code") or {}).items()}
    out.update(has=True, compliant=True, by_code=by, n_codes=len(by),
               posts=(doc.get("guba") or {}).get("posts") or [],
               errors=doc.get("errors") or [],
               note="同日盘后合规快照")
    return out


def require_compliant_social(day, snap_dir=None, after_close=(15, 0)):
    """P5.3 / P5.4 校准入口：不合规直接 fail-fast，绝不静默降级。"""
    snap = load_compliant_social(day, snap_dir=snap_dir,
                                 after_close=after_close)
    if not snap["compliant"]:
        raise SocialSnapshotNotCompliant(
            "day=%s class=%s reason=%s" % (day, snap["class"], snap["reason"]))
    return snap
