#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""学习项目日增流水线健康检查（红绿灯）。

只读工具：不写文件、不建目录、不改数据库（SQLite 以 mode=ro 打开）。
仅依赖 Python 标准库。

用法:
    python tools/kb_corpus/learning_health.py [--project <id>] [--json] [--days 7]

退出码: 绿=0, 黄=1, 红=2
"""

from __future__ import annotations

import argparse
import collections
import datetime as _dt
import json
import math
import os
import re
import sqlite3
import sys
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

KB_ROOT = r"C:/项目/知识库"
DB_PATH = os.path.join(KB_ROOT, "00_数据仓", "learning.db")
REGISTRY_PATH = r"C:/项目/老田的AI助手/agent_platform/registry/learning_projects.json"

DEFAULT_PROJECT = "视频号学习_北斗股术1688"

# 指标阈值（与团队约定一致，不要为了让结果好看而调）
BASELINE_DAILY = 1.56          # 实测基线：114 条 / 73 天
GREEN_MIN_RATE = 1.0           # 指标1 绿线：>=1.0 条/天
YELLOW_MIN_RATE = 0.5          # 指标1 黄区下限：0.5~1.0 为黄，<0.5 为红
IDLE_RED_HOURS = 72            # 指标1 断更 >=72h 判红（精确到分钟，避免日历日边界抖动）
T_SHARE_GREEN = (0.60, 0.95)   # 指标2 绿区 60%~95%
PENDING_GREEN_MAX = 10         # 指标4 绿线（近 N 天新增待审）
PENDING_YELLOW_MAX = 30        # 指标4 黄线上限
STALE_RESIDUE_RE = re.compile(r"文件|mp|时长|字数")

STATUS_LABEL = {
    "green": "[绿]",
    "yellow": "[黄]",
    "red": "[红]",
    "unknown": "[黄]",  # 缺数据视为黄，不阻断整体判定
    "info": "[提示]",   # 只展示、不计入整体灯色
}
STATUS_RANK = {"green": 0, "unknown": 1, "yellow": 1, "red": 2, "info": 0}


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def dwidth(text: str) -> int:
    """终端显示宽度（中日韩全角按 2 计）。"""
    width = 0
    for ch in str(text):
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def pad(text: str, width: int) -> str:
    text = str(text)
    gap = width - dwidth(text)
    return text + " " * gap if gap > 0 else text


def fmt_num(value: Any, digits: int = 2) -> str:
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def today() -> _dt.date:
    return _dt.date.today()


def now_iso() -> str:
    return _dt.datetime.now().astimezone().replace(microsecond=0).isoformat()


def metric(mid: int, name: str, value: Any, unit: str, status: str, note: str,
           threshold: str = "", counted: bool = True) -> Dict[str, Any]:
    return {
        "id": mid,
        "name": name,
        "value": value,
        "unit": unit,
        "status": status,
        "counted": counted,   # False = 只提示，不参与 overall 判定
        "note": note,
        "threshold": threshold,
    }


def unknown(mid: int, name: str, unit: str, note: str, threshold: str = "",
            counted: bool = True) -> Dict[str, Any]:
    return metric(mid, name, None, unit, "unknown", note, threshold, counted)


def parse_day(raw: Any) -> Optional[_dt.date]:
    """解析 created_at（ISO 串，可能带时区）为日期。"""
    if not raw:
        return None
    try:
        return _dt.datetime.fromisoformat(str(raw)).date()
    except ValueError:
        return None


def in_window(raw: Any, days: int, t: Optional[_dt.date] = None) -> bool:
    """created_at 是否落在近 N 天窗口内；时间戳缺失时按"在窗口内"处理。"""
    day = parse_day(raw)
    if day is None:
        return True
    return 0 <= ((t or today()) - day).days < days


# --------------------------------------------------------------------------- #
# 路径解析
# --------------------------------------------------------------------------- #
def resolve_paths(project: str) -> Dict[str, str]:
    """按知识库约定目录推出各数据源路径（只推导，不创建）。"""
    return {
        "source_md": os.path.join(KB_ROOT, "05_学习源件", project, "source", f"{project}.md"),
        "units_jsonl": os.path.join(KB_ROOT, "30_知识单元", f"{project}_units_effective.jsonl"),
        "units_draft": os.path.join(KB_ROOT, "30_知识单元", f"{project}_units_draft.jsonl"),
        # unit_classify.py 的默认 sidecar 产物：<输入名>_classified.jsonl =
        # {project}_units_effective_classified.jsonl（effective 的超集副本 + unit_class 字段）。
        # 指标 2/3/5 只认 unit_class，缺了它必然 unknown→黄，故需在此登记。
        "units_classified": os.path.join(
            KB_ROOT, "30_知识单元", f"{project}_units_effective_classified.jsonl"),
        # 兼容 --out 自定义为 {project}_units_classified.jsonl 的情形
        "units_classified_alt": os.path.join(
            KB_ROOT, "30_知识单元", f"{project}_units_classified.jsonl"),
        "concepts": os.path.join(KB_ROOT, "35_知识提炼", f"{project}_concepts_draft.json"),
        "db": DB_PATH,
        "registry": REGISTRY_PATH,
    }


def read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError):
        return None


def read_jsonl(path: str) -> Optional[List[Dict[str, Any]]]:
    text = read_text(path)
    if text is None:
        return None
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def open_db_ro(path: str) -> Optional[sqlite3.Connection]:
    """只读打开；文件不存在返回 None。

    WAL 为空时追加 immutable=1，避免 SQLite 触碰 -shm（真正零写入）；
    WAL 有未落盘内容时退回纯 mode=ro，保证读到最新数据。
    """
    if not os.path.isfile(path):
        return None
    wal = path + "-wal"
    wal_pending = os.path.isfile(wal) and os.path.getsize(wal) > 0
    query = "?mode=ro" if wal_pending else "?mode=ro&immutable=1"
    try:
        con = sqlite3.connect("file:" + path.replace("\\", "/") + query, uri=True, timeout=5)
        con.execute("PRAGMA query_only = ON")
        return con
    except sqlite3.Error:
        return None


# --------------------------------------------------------------------------- #
# 数据加载
# --------------------------------------------------------------------------- #
def load_project_meta(project: str) -> Tuple[Optional[str], str]:
    """从注册表取项目名；失败返回 (None, 说明)。"""
    text = read_text(REGISTRY_PATH)
    if text is None:
        return None, "注册表缺失"
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None, "注册表 JSON 解析失败"
    for item in data.get("projects", []) or []:
        if item.get("project_id") == project:
            return item.get("name") or project, ""
    return None, "注册表中无该项目"


def parse_source_stamps(path: str) -> Optional[List[_dt.datetime]]:
    """解析源 md 的 `## YYYYMMDD_HHMM` 标题为本地时间戳（naive）。"""
    text = read_text(path)
    if text is None:
        return None
    stamps = []
    for raw in re.findall(r"^##\s+(\d{8}_\d{4})\s", text, re.M):
        try:
            stamps.append(_dt.datetime.strptime(raw, "%Y%m%d_%H%M"))
        except ValueError:
            continue
    return stamps


def source_coverage(path: str, rows: Optional[List[Dict[str, Any]]]) -> Optional[Dict[str, Any]]:
    """按 evidence.line + evidence_list 把 unit 映回视频段。

    covered 是"段下标集合"，同一段无论被多少条 unit / 多少个 evidence_list 命中，
    只计一次（去重口径）。返回 None 表示缺数据。
    """
    text = read_text(path)
    if text is None or rows is None:
        return None

    segments: List[Tuple[int, str]] = []  # (起始行号, YYYYMMDD_HHMM)
    for idx, line in enumerate(text.splitlines(), start=1):
        m = re.match(r"^##\s+(\d{8}_\d{4})\s", line)
        if m:
            segments.append((idx, m.group(1)))
    if not segments:
        return None

    starts = [seg[0] for seg in segments]
    covered = set()          # 存段下标，天然去重
    merged_units = 0

    def mark(line_no: Any) -> None:
        try:
            ln = int(line_no)
        except (TypeError, ValueError):
            return
        pos = None
        for i, start in enumerate(starts):
            if start <= ln:
                pos = i
            else:
                break
        if pos is not None:
            covered.add(pos)

    for row in rows:
        ev = row.get("evidence")
        if isinstance(ev, dict):
            mark(ev.get("line"))
        extra = row.get("evidence_list") or []
        if extra:
            merged_units += 1
        for item in extra:
            if isinstance(item, dict):
                mark(item.get("line"))

    total = len(segments)
    assert len(covered) <= total, "覆盖段数不可能超过总段数（去重失败）"

    newest_idx = max(range(total), key=lambda i: segments[i][1])
    missing = [segments[i][1] for i in range(total) if i not in covered]
    return {
        "total": total,
        "covered": len(covered),
        "missing": missing,
        "merged_units": merged_units,
        "newest_key": segments[newest_idx][1],
        "newest_covered": newest_idx in covered,
    }


def load_units(paths: Dict[str, str],
               override: Optional[str] = None) -> Tuple[Optional[List[Dict[str, Any]]], str]:
    """选取 units 数据源，返回 (rows, 实际使用的文件名)。

    优先级：--units 显式指定 > _classified（且不旧于 effective）> effective > draft。
    `_classified.jsonl` 是 unit_classify 产出的 effective 超集副本（同样行 + unit_class
    字段），读它对指标 1/4/6/8 语义等价，对 2/3/5 是唯一可用来源。但它是**副本**，若
    早于 effective 落盘就必须弃用——否则会用旧数据掩盖新增量，故做 mtime 新鲜度防御。
    """
    if override:
        rows = read_jsonl(override)
        return rows, os.path.basename(override)

    effective = paths["units_jsonl"]
    # unit_classify 的默认产物是 <输入名>_classified.jsonl，即
    # {project}_units_effective_classified.jsonl；用 --out 自定义时才会是别的名字。
    # 两种命名都试，避免因路径约定不一致而静默读不到分型（该坑 2026-11-06 实际踩过）。
    candidates = [p for p in (paths.get("units_classified"),
                              paths.get("units_classified_alt")) if p]
    for classified in candidates:
        if not os.path.exists(classified):
            continue
        try:
            fresh = os.path.getmtime(classified) >= os.path.getmtime(effective)
        except OSError:
            fresh = False
        if fresh:
            rows = read_jsonl(classified)
            if rows is not None:
                return rows, os.path.basename(classified)
        else:
            sys.stderr.write(
                f"[warn] {os.path.basename(classified)} 早于 "
                f"{os.path.basename(effective)} 落盘，已弃用（分型需重跑 unit_classify）\n")

    for key in ("units_jsonl", "units_draft"):
        rows = read_jsonl(paths[key])
        if rows is not None:
            return rows, os.path.basename(paths[key])
    return None, ""


def db_unit_stats(con: sqlite3.Connection, project: str) -> Optional[Dict[str, Any]]:
    try:
        rows = list(con.execute(
            "SELECT unit_id, status, created_at FROM fact_learning_unit WHERE project_id = ?",
            (project,)
        ))
    except sqlite3.Error:
        return None
    statuses = collections.Counter((r[1] or "unknown") for r in rows)
    return {
        "ids": {r[0] for r in rows if r[0]},
        "rows": rows,
        "count": len(rows),
        "statuses": statuses,
    }


# --------------------------------------------------------------------------- #
# 7 项指标（指标 4 拆两条，共 8 行输出）
# --------------------------------------------------------------------------- #
def metric_1_daily_source(path: str, days: int,
                          coverage: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    name = "日增视频数"
    threshold = (f">={GREEN_MIN_RATE} 条/天 绿；{YELLOW_MIN_RATE}~{GREEN_MIN_RATE} 黄；"
                 f"<{YELLOW_MIN_RATE} 红；距上次更新>={IDLE_RED_HOURS}h 红；"
                 "最新段无 unit 黄")
    stamps = parse_source_stamps(path)
    if stamps is None:
        return unknown(1, name, "条/天", f"缺少源 md：{os.path.basename(path)}", threshold)
    if not stamps:
        return unknown(1, name, "条/天", "源 md 中未解析到 `## YYYYMMDD_HHMM` 标题行", threshold)

    t = today()
    dates = [stamp.date() for stamp in stamps]
    recent = [d for d in dates if 0 <= (t - d).days < days]
    rate = len(recent) / float(days)
    last_ts = max(stamps)
    idle_hours = (_dt.datetime.now() - last_ts).total_seconds() / 3600.0
    # 展示值向下取 0.1h，避免 71.98h 显示成 72.0h 却判黄这种自相矛盾
    shown_hours = math.floor(idle_hours * 10) / 10.0
    note = (f"近 {days} 天 {len(recent)} 条，最新 {last_ts.strftime('%Y-%m-%d %H:%M')}；"
            f"基线 {BASELINE_DAILY} 条/天；距上次更新 {shown_hours:.1f}h / 阈值 {IDLE_RED_HOURS}h")

    if idle_hours >= IDLE_RED_HOURS:
        status = "red"
        note += "（已断更）"
    elif rate >= GREEN_MIN_RATE:
        status = "green"
    elif rate >= YELLOW_MIN_RATE:
        status = "yellow"
        note += "；低于绿线"
    else:
        status = "red"
        note += f"；低于 {YELLOW_MIN_RATE} 条/天"

    if coverage and coverage.get("total"):
        note += f"；unit 覆盖 {coverage['covered']}/{coverage['total']} 段"
        if coverage["merged_units"]:
            note += f"（含 {coverage['merged_units']} 条合并单元）"
        missing = coverage["missing"]
        if missing:
            shown = "、".join(missing[:6]) + ("等" if len(missing) > 6 else "")
            note += f"，缺 {len(missing)} 段：{shown}"
        if not coverage["newest_covered"]:
            note += f"；源已到 {coverage['newest_key']} 但该段无 unit，疑只进源不出单元"
            if status == "green":
                status = "yellow"

    return metric(1, name, round(rate, 3), "条/天", status, note, threshold)


def _class_bucket(value: Any) -> str:
    text = str(value or "").strip().upper()
    if not text:
        return ""
    if text.startswith("T"):
        return "T"
    if text.startswith("M"):
        return "M"
    return text[0]


def metric_2_t_share(rows: Optional[List[Dict[str, Any]]], src: str, days: int) -> Dict[str, Any]:
    name = f"近 {days} 天 T 类占比"
    threshold = "T 占比 60%~95% 为绿；<60% 或 >95% 为黄"
    if rows is None:
        return unknown(2, name, "%", "缺少 units jsonl（effective/draft 均不存在）", threshold)

    windowed = [r for r in rows if in_window(r.get("created_at"), days)]
    if not windowed:
        return unknown(2, name, "%", f"{src} 近 {days} 天无新增 unit", threshold)

    buckets = collections.Counter(_class_bucket(r.get("unit_class")) for r in windowed)
    buckets.pop("", None)
    total = sum(buckets.values())
    if total == 0:
        return unknown(2, name, "%",
                       f"{src} 近 {days} 天 {len(windowed)} 条，无任何 unit_class 字段（分流未实施）",
                       threshold)

    share = buckets.get("T", 0) / float(total)
    status = "green" if T_SHARE_GREEN[0] <= share <= T_SHARE_GREEN[1] else "yellow"
    note = (f"{src} 近 {days} 天有分类 {total} 条，T={buckets.get('T', 0)}，"
            f"M={buckets.get('M', 0)}")
    return metric(2, name, round(share * 100, 1), "%", status, note, threshold)


def metric_3_m_count(rows: Optional[List[Dict[str, Any]]], src: str, days: int) -> Dict[str, Any]:
    name = f"近 {days} 天 M 类条数"
    threshold = ">0 为绿；=0 且连续 7 天为 0 为黄"
    if rows is None:
        return unknown(3, name, "条", "缺少 units jsonl", threshold)

    windowed = [r for r in rows if in_window(r.get("created_at"), days)]
    if not windowed:
        return unknown(3, name, "条", f"{src} 近 {days} 天无新增 unit", threshold)

    buckets = collections.Counter(_class_bucket(r.get("unit_class")) for r in windowed)
    buckets.pop("", None)
    if not buckets:
        return unknown(3, name, "条",
                       f"{src} 近 {days} 天无 unit_class 字段，M 类无法计数（分流未实施）", threshold)

    m_count = buckets.get("M", 0)
    status = "green" if m_count > 0 else "yellow"
    note = f"{src} 近 {days} 天 M={m_count}，T={buckets.get('T', 0)}"
    return metric(3, name, m_count, "条", status, note, threshold)


def metric_4_pending(db: Optional[Dict[str, Any]], rows: Optional[List[Dict[str, Any]]],
                     db_note: str, days: int) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """返回两条：(近 N 天新增待审[计灯], 存量待审[仅提示不计灯])。"""
    pending_keys = ("pending_review", "pending", "待审", "review", "needs_review")
    threshold = f"近 {days} 天新增 <=10 绿；11~30 黄；>30 红"
    stamps: List[Any] = []
    base = ""

    if db is not None:
        hits = {k: v for k, v in db["statuses"].items() if k in pending_keys}
        if hits:
            stamps = [r[2] for r in db["rows"] if (r[1] or "") in pending_keys]
            base = "fact_learning_unit：" + "、".join(f"{k}={v}" for k, v in hits.items())
        elif rows is not None:
            stamps = [r.get("created_at") for r in rows if (r.get("status") or "") != "expired"]
            base = ("DB 无 pending_review 状态（现有：" +
                    "、".join(f"{k}={v}" for k, v in db["statuses"].most_common()) +
                    "），回退用 units 中 status!=expired 估算")
    elif rows is not None:
        stamps = [r.get("created_at") for r in rows if (r.get("status") or "") != "expired"]
        base = f"数据库不可用（{db_note}），回退用 units 中 status!=expired 估算"

    if not base:
        reason = ("DB 无 pending_review 状态且缺少 units jsonl" if db is not None
                  else f"数据库不可用（{db_note}）且缺少 units jsonl")
        return (unknown(4, f"近 {days} 天新增待审", "条", reason, threshold),
                unknown(8, "存量待审（仅提示）", "条", reason, "不计灯", counted=False))

    total = len(stamps)
    recent = sum(1 for stamp in stamps if in_window(stamp, days))

    if recent <= PENDING_GREEN_MAX:
        status = "green"
    elif recent <= PENDING_YELLOW_MAX:
        status = "yellow"
    else:
        status = "red"

    m4 = metric(4, f"近 {days} 天新增待审", recent, "条", status,
                f"{base}；窗口内 {recent} 条 / 存量 {total} 条", threshold)
    m8 = metric(8, "存量待审（仅提示）", total, "条", "info",
                f"{base}；存量 {total} 条（历史累积，不计入灯色）",
                "仅提示，不参与整体判定", counted=False)
    return m4, m8


def metric_5_expired(rows: Optional[List[Dict[str, Any]]], src: str) -> Dict[str, Any]:
    name = "时效池过期数"
    threshold = "T 类中 >0 条已过期（分流在跑）为绿；=0 且 T 类>0 为黄"
    if rows is None:
        return unknown(5, name, "条", "缺少 units jsonl", threshold)

    t_units = [r for r in rows if _class_bucket(r.get("unit_class")) == "T"]
    if not t_units:
        return unknown(5, name, "条",
                       f"{src} 中 T 类为 0（无 unit_class 字段，时效分流未实施）", threshold)

    t = today()
    expired = 0
    for row in t_units:
        if (row.get("status") or "") == "expired":
            expired += 1
            continue
        raw = row.get("expire_at")
        if not raw:
            continue
        try:
            if _dt.datetime.fromisoformat(str(raw)).date() < t:
                expired += 1
        except ValueError:
            continue

    status = "green" if expired > 0 else "yellow"
    note = f"T 类 {len(t_units)} 条，其中已过期 {expired} 条"
    return metric(5, name, expired, "条", status, note, threshold)


def metric_6_conflict(rows: Optional[List[Dict[str, Any]]], db: Optional[Dict[str, Any]],
                      db_note: str) -> Dict[str, Any]:
    name = "unit_id 冲突数"
    threshold = "0 为绿；>0 为红"
    if rows is None:
        return unknown(6, name, "个", "缺少 units jsonl，无法比对 ID", threshold)

    ids = [r.get("unit_id") for r in rows if r.get("unit_id")]
    dup = sum(v - 1 for v in collections.Counter(ids).values() if v > 1)
    jsonl_ids = set(ids)

    if db is None:
        status = "green" if dup == 0 else "red"
        note = f"jsonl 内重复 {dup}；数据库不可用（{db_note}），未做跨源比对"
        return metric(6, name, dup, "个", status, note, threshold)

    only_jsonl = jsonl_ids - db["ids"]
    only_db = db["ids"] - jsonl_ids
    diff = len(only_jsonl) + len(only_db)
    total = dup + diff
    status = "green" if total == 0 else "red"
    note = (f"jsonl 内重复 {dup}；jsonl 独有 {len(only_jsonl)}，DB 独有 {len(only_db)}"
            f"（jsonl={len(jsonl_ids)}，DB={len(db['ids'])}）")
    return metric(6, name, total, "个", status, note, threshold)


def metric_7_top_terms(path: str) -> Dict[str, Any]:
    name = "top_terms 元数据残留"
    threshold = "命中 文件|mp|时长|字数 的条数为 0 才绿；>0 为红"
    text = read_text(path)
    if text is None:
        return unknown(7, name, "条", f"缺少概念稿：{os.path.basename(path)}", threshold)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return unknown(7, name, "条", "概念稿 JSON 解析失败", threshold)

    terms = data.get("top_terms") if isinstance(data, dict) else None
    if not isinstance(terms, list):
        return unknown(7, name, "条", "概念稿中无 top_terms 列表", threshold)

    residues = []
    for item in terms:
        term = str((item or {}).get("term") or "")
        if term and STALE_RESIDUE_RE.search(term):
            residues.append(f"{term}({(item or {}).get('count')})")

    status = "green" if not residues else "red"
    if residues:
        note = ("clean 层未剥离元数据行（clean_md 仅折叠空行），top_terms 从 clean 文本统计，"
                "故命中 " + "、".join(residues))
    else:
        note = f"top_terms 共 {len(terms)} 项，无元数据残留"
    return metric(7, name, len(residues), "条", status, note, threshold)


# --------------------------------------------------------------------------- #
# 汇总与输出
# --------------------------------------------------------------------------- #
def overall_status(metrics: List[Dict[str, Any]]) -> str:
    counted = [m for m in metrics if m.get("counted", True)]
    worst = max(STATUS_RANK.get(m["status"], 1) for m in counted)
    return {0: "green", 1: "yellow", 2: "red"}[worst]


def render_table(payload: Dict[str, Any]) -> str:
    metrics = payload["metrics"]
    headers = ["#", "指标", "实测值", "阈值", "状态"]
    rows = [[str(m["id"]), m["name"],
             "-" if m["value"] is None else f"{fmt_num(m['value'])} {m['unit']}".strip(),
             m.get("threshold", ""), STATUS_LABEL.get(m["status"], m["status"])]
            for m in metrics]

    widths = [max(dwidth(h), max(dwidth(r[i]) for r in rows)) for i, h in enumerate(headers)]
    line = "  ".join(pad(h, widths[i]) for i, h in enumerate(headers))
    sep = "  ".join("-" * widths[i] for i in range(len(headers)))
    body = ["  ".join(pad(cell, widths[i]) for i, cell in enumerate(r)) for r in rows]

    out = [
        f"学习项目健康检查  项目={payload['project']}  生成于 {payload['generated_at']}",
        "",
        line,
        sep,
    ]
    out.extend(body)
    out.append("")

    for m in metrics:
        if m["status"] in ("red", "yellow", "unknown"):
            out.append(f"  #{m['id']} {STATUS_LABEL[m['status']]} {m['note']}")

    out.append("")
    verdict = {"green": "绿", "yellow": "黄", "red": "红"}[payload["overall"]]
    out.append(f"整体：{STATUS_LABEL[payload['overall']]} {verdict}"
               f"  退出码 {payload['exit_code']}")
    return "\n".join(out)


def build_payload(project: str, days: int,
                  units_override: Optional[str] = None) -> Dict[str, Any]:
    paths = resolve_paths(project)
    proj_name, reg_note = load_project_meta(project)

    rows, units_src = load_units(paths, units_override)

    con = open_db_ro(paths["db"])
    db_stats = None
    db_note = "文件不存在"
    if con is not None:
        try:
            db_stats = db_unit_stats(con, project)
            db_note = "查询失败" if db_stats is None else ""
        finally:
            con.close()

    coverage = source_coverage(paths["source_md"], rows)
    src_name = units_src or "units"

    metrics = [
        metric_1_daily_source(paths["source_md"], days, coverage),
        metric_2_t_share(rows, src_name, days),
        metric_3_m_count(rows, src_name, days),
    ]
    metrics.extend(metric_4_pending(db_stats, rows, db_note, days))
    metrics.extend([
        metric_5_expired(rows, src_name),
        metric_6_conflict(rows, db_stats, db_note),
        metric_7_top_terms(paths["concepts"]),
    ])

    metrics.sort(key=lambda m: m["id"])
    overall = overall_status(metrics)
    payload = {
        "generated_at": now_iso(),
        "project": project,
        "project_name": proj_name or project,
        "registry_note": reg_note,
        "window_days": days,
        "overall": overall,
        "metrics": metrics,
    }
    payload["exit_code"] = {"green": 0, "yellow": 1, "red": 2}[overall]
    return payload


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="学习项目日增流水线健康检查（只读，红绿灯）")
    parser.add_argument("--project", default=DEFAULT_PROJECT, help="项目 ID")
    parser.add_argument("--json", action="store_true", help="输出结构化 JSON")
    parser.add_argument("--days", type=int, default=7, help="统计窗口天数（默认 7）")
    parser.add_argument("--units", default=None,
                        help="显式指定 units jsonl（默认自动选 _classified > effective > draft）")
    args = parser.parse_args(argv)

    days = args.days if args.days and args.days > 0 else 7
    payload = build_payload(args.project, days, args.units)

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_table(payload))
    return payload["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
