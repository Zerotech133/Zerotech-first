# -*- coding: utf-8 -*-
"""D1（鲁肃）部门日报只读聚合（P1，2026-11-01 WO-D1-DAILY-20261101）。

职责：给 A0 一份 D1 部门（invest 量化交易部）的进度快照——
  · 成员名单与中文名：读 ``agent_platform/registry/org.json`` 的 invest 部门（真源）；
  · 进度：读 ``.worktrees/tasks.json``，按 owner 归属成员，统计 doing/review/done；
  · 超期：status ∈ {doing, review} 且 created 距今 >24h；created 缺失/不可解析的
    工单**跳过不猜**（不拿 mtime / ID 反推时间）；
  · 输出 ``d1_daily/v1`` dict + 给 A0 的 Markdown 摘要；CLI 支持 ``--json``。

边界（铁律）：
  · **只读**：``READ_ONLY=True``——不写 tasks.json / org.json、不写生产台账、
    不写 reviewed/handoff、不派单、不生成交易单；
  · 失源显式：org.json / tasks.json 不可读时报告降级并写进 ``notes``，不造数；
  · 与 ``tools/daily_review.py``（写盘的四段复盘流）互补：本模块只做聚合投影。

口径（版本化，改动须同步测试）：
  · 成员 = ``departments[dept=invest].agents``，顺序即 org 声明顺序；
  · owner 归属 = 精确 ID，或「ID + 分隔符」（如 ``ST1 席位（缠论分析师）``→ST1），
    或中文名精确/前缀（如 ``鲁肃``→D1）；不做拼音/模糊猜测；
  · ``progress`` 除逐成员状态桶外含 ``total`` 汇总键（小写，与成员 ID 大写不冲突）。

公共接口：``members()`` / ``build_daily()`` / ``render_markdown()`` / ``main()``。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

SCHEMA = "d1_daily/v1"
DEPT = "invest"
DEPT_NAME_FALLBACK = "量化交易部"
LEAD_FALLBACK = "D1"
LEAD_NAME_FALLBACK = "鲁肃"
REPORTS_TO = "A0"
OVERDUE_HOURS = 24
STATUSES = ("doing", "review", "done")
OPEN_STATUSES = ("doing", "review")
TOTAL_KEY = "total"
READ_ONLY = True
PAPER_ONLY = True

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ORG = ROOT / "agent_platform" / "registry" / "org.json"

# tasks.json 三代 created 格式并存（见 tools/validate_json._TS_RE 注释）；只认不猜。
_CREATED_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
)
# owner 里 ID 后允许出现的分隔符（"ST1 席位…" / "T1·甲" / "D1（主席）" 等）。
_OWNER_SEPS = frozenset(" \t\u3000·×（()）/|｜：:、，,；;")
_DESC_LIMIT = 120


class D1DailyError(ValueError):
    """参数/口径错误（CLI 层映射非零退出码）。"""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


# ----------------------------------------------------------------------
# 基础工具
# ----------------------------------------------------------------------
def _naive(dt):
    """统一种口径：带时区的时间转本地无时区（tasks.json 全是本地naive）。"""
    if isinstance(dt, datetime) and dt.tzinfo is not None:
        return dt.astimezone().replace(tzinfo=None)
    return dt


def _parse_created(value):
    """created → naive datetime；解析失败/缺失返回 None（**不猜**）。"""
    raw = str(value or "").strip()
    if not raw:
        return None
    for fmt in _CREATED_FORMATS:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    try:
        return _naive(datetime.fromisoformat(raw))
    except ValueError:
        return None


def _coerce_now(now):
    if now is None:
        return datetime.now()
    if isinstance(now, datetime):
        return _naive(now)
    if isinstance(now, date):
        return datetime(now.year, now.month, now.day)
    try:
        return _naive(datetime.fromisoformat(str(now).strip()))
    except ValueError:
        raise D1DailyError("bad_now", "now 必须是 datetime/ISO 时间（当前=%r）" % (now,))


def _coerce_day(day, now_dt):
    """day → ``YYYY-MM-DD``；缺省取 now 的日期。"""
    if day is None:
        return now_dt.date().isoformat()
    if isinstance(day, datetime):
        return day.date().isoformat()
    if isinstance(day, date):
        return day.isoformat()
    raw = str(day).strip()
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date().isoformat()
    except ValueError:
        raise D1DailyError("bad_day", "day 必须是 YYYY-MM-DD（当前=%r）" % raw)


def _profile_name(profile):
    """档案中文名：cn_name 优先，persona.name 兜底；都没有返回空串（不编造）。"""
    if not isinstance(profile, dict):
        return ""
    name = str(profile.get("cn_name") or "").strip()
    if name:
        return name
    persona = profile.get("persona")
    if isinstance(persona, dict):
        return str(persona.get("name") or "").strip()
    return ""


def _iter_profiles(node):
    """递归遍历 org.json 里所有带 agent_id 的档案节点（矩阵任职的跨部门兜底）。"""
    if isinstance(node, dict):
        if node.get("agent_id"):
            yield node
        for value in node.values():
            yield from _iter_profiles(value)
    elif isinstance(node, list):
        for value in node:
            yield from _iter_profiles(value)


def _load_json(path):
    """读 JSON → (obj, error)；error 非空即失源（不抛异常，日报要能降级出）。"""
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        return None, "%s 不可读：%s" % (path.name, exc)
    try:
        obj = json.loads(raw)
    except ValueError as exc:
        return None, "%s 解析失败：%s" % (path.name, exc)
    return obj, None


# ----------------------------------------------------------------------
# 组织真源：invest 部门成员
# ----------------------------------------------------------------------
def _find_invest_dept(org):
    if not isinstance(org, dict):
        return None
    for dept in org.get("departments") or []:
        if isinstance(dept, dict) and str(dept.get("dept") or "").strip() == DEPT:
            return dept
    return None


def _load_org(org_path):
    """→ (members, lead_id, lead_name, dept_name, error)。members 为 dict 列表。"""
    org, err = _load_json(org_path or DEFAULT_ORG)
    if err:
        return [], LEAD_FALLBACK, LEAD_NAME_FALLBACK, "", err
    dept = _find_invest_dept(org)
    if dept is None:
        return [], LEAD_FALLBACK, LEAD_NAME_FALLBACK, "", "org.json 无 %s 部门" % DEPT

    profiles = {}
    for profile in dept.get("agent_profiles") or []:
        if isinstance(profile, dict) and profile.get("agent_id"):
            profiles[str(profile["agent_id"]).strip()] = profile

    agent_ids = []
    for raw in dept.get("agents") or []:
        aid = str(raw or "").strip()
        if aid and aid not in agent_ids:
            agent_ids.append(aid)

    # 矩阵任职：成员可能只在别的部门建档——全局兜底，优先带中文名的档案。
    missing = [aid for aid in agent_ids if aid not in profiles]
    if missing:
        extra = {}
        for profile in _iter_profiles(org):
            aid = str(profile.get("agent_id") or "").strip()
            if aid not in missing:
                continue
            cur = extra.get(aid)
            if cur is None or (not _profile_name(cur) and _profile_name(profile)):
                extra[aid] = profile
        profiles.update(extra)

    lead_id = str(dept.get("lead") or "").strip() or LEAD_FALLBACK
    members_rows = []
    lead_name = ""
    for aid in agent_ids:
        profile = profiles.get(aid) or {}
        name = _profile_name(profile)
        if aid == lead_id and name:
            lead_name = name
        members_rows.append(
            {
                "id": aid,
                "name": name,
                "role": str(profile.get("role_profile") or "").strip(),
                "is_lead": aid == lead_id,
                "reports_to": str(profile.get("reports_to") or "").strip(),
            }
        )
    dept_name = str(dept.get("name") or "").strip() or DEPT_NAME_FALLBACK
    return members_rows, lead_id, lead_name or LEAD_NAME_FALLBACK, dept_name, None


def members(org_path=None):
    """D1 部门在册成员（id/中文名/角色/是否部长/汇报对象），org 声明顺序。

    org.json 读不到 → 返回 ``[]``（失源由 :func:`build_daily` 记入 notes，不造名单）。
    """
    rows, _lead, _lead_name, _dept_name, _err = _load_org(org_path)
    return rows


# ----------------------------------------------------------------------
# 工单账本：进度 + 超期
# ----------------------------------------------------------------------
def _default_tasks_path():
    """默认 tasks.json：仓库根 ``.worktrees/tasks.json``；worktree 检出时退回上级
    ``.worktrees/tasks.json``（``.worktrees/<TID>/`` 内再套一层 .worktrees 不存在）。"""
    primary = ROOT / ".worktrees" / "tasks.json"
    if primary.exists():
        return primary
    sibling = ROOT.parent / "tasks.json"
    if ROOT.parent.name == ".worktrees" and sibling.exists():
        return sibling
    return primary


def _load_tasks(tasks_path):
    obj, err = _load_json(tasks_path)
    if err:
        return {}, err
    if not isinstance(obj, dict):
        return {}, "tasks.json 顶层必须是 dict（实际 %s）" % type(obj).__name__
    return obj, None


def _match_member(owner, ids, name_to_id):
    """owner → 成员 ID；只认精确 ID / ID+分隔符 / 中文名精确或前缀，不做模糊猜测。"""
    raw = str(owner or "").strip()
    if not raw:
        return None
    upper = raw.upper()
    for mid in ids:
        if mid.upper() == upper:
            return mid
    for mid in ids:
        mid_up = mid.upper()
        if upper.startswith(mid_up) and len(upper) > len(mid_up):
            if upper[len(mid_up)] in _OWNER_SEPS:
                return mid
    for name, mid in name_to_id.items():
        if not name:
            continue
        if raw == name:
            return mid
        if raw.startswith(name) and len(raw) > len(name):
            if raw[len(name)] in _OWNER_SEPS:
                return mid
    return None


def _empty_bucket():
    return {"doing": 0, "review": 0, "done": 0, TOTAL_KEY: 0, "overdue": 0}


def _as_dict(value):
    return value if isinstance(value, dict) else {}


def build_daily(day=None, tasks_path=None, org_path=None, now=None):
    """聚合 D1 部门日报（只读）。

    :param day: 报告日期 ``YYYY-MM-DD``（date/datetime 亦可）；缺省取 now 当天。
    :param tasks_path: 工单账本路径；缺省按 :func:`_default_tasks_path` 解析。
    :param org_path: 组织真源路径；缺省 ``agent_platform/registry/org.json``。
    :param now: 判定基准时间（datetime，测试注入）；缺省 ``datetime.now()``。
    :return: dict，schema=``d1_daily/v1``；含 date/lead/lead_name/members/progress/
             overdue/reports_to/paper_only/read_only，另带 dept/dept_name/sources/
             scope/notes/overdue_hours 便于 A0 复核口径。
    """
    now_dt = _coerce_now(now)
    day_str = _coerce_day(day, now_dt)
    org_p = Path(org_path) if org_path else DEFAULT_ORG
    tasks_p = Path(tasks_path) if tasks_path else _default_tasks_path()

    member_rows, lead_id, lead_name, dept_name, org_err = _load_org(org_p)
    ids = [row["id"] for row in member_rows]
    name_to_id = {}
    for row in member_rows:
        if row["name"]:
            name_to_id.setdefault(row["name"], row["id"])
    name_by_id = {row["id"]: row["name"] for row in member_rows}

    progress = {mid: _empty_bucket() for mid in ids}
    tasks, tasks_err = _load_tasks(tasks_p)

    notes = []
    if org_err:
        notes.append(org_err)
    elif not member_rows:
        notes.append("org.json %s 部门成员名单为空" % DEPT)
    if tasks_err:
        notes.append(tasks_err)

    overdue = []
    attributed = 0
    unattributed = 0
    created_unparsable = 0
    for tid, task in tasks.items():
        record = _as_dict(task)
        if not record:
            continue
        status = str(record.get("status") or "").strip().lower()
        if status not in STATUSES:
            continue
        mid = _match_member(record.get("owner"), ids, name_to_id)
        if mid is None:
            unattributed += 1
            continue
        attributed += 1
        bucket = progress[mid]
        bucket[status] += 1
        bucket[TOTAL_KEY] += 1
        if status not in OPEN_STATUSES:
            continue
        created_dt = _parse_created(record.get("created"))
        if created_dt is None:
            created_unparsable += 1
            continue
        age_hours = round((now_dt - created_dt).total_seconds() / 3600.0, 1)
        if age_hours <= OVERDUE_HOURS:
            continue
        bucket["overdue"] += 1
        overdue.append(
            {
                "id": str(tid),
                "owner": str(record.get("owner") or ""),
                "owner_name": name_by_id.get(mid, ""),
                "status": status,
                "created": str(record.get("created") or ""),
                "age_hours": age_hours,
                "desc": " ".join(str(record.get("desc") or "").split())[:_DESC_LIMIT],
            }
        )
    overdue.sort(key=lambda row: (-row["age_hours"], row["id"]))

    if created_unparsable:
        notes.append(
            "created 缺失/不可解析：%d 单 doing/review 未参与超期判定（不猜）"
            % created_unparsable
        )

    totals = _empty_bucket()
    for mid in ids:
        for key in totals:
            totals[key] += progress[mid][key]
    progress[TOTAL_KEY] = totals

    return {
        "schema": SCHEMA,
        "date": day_str,
        "lead": lead_id,
        "lead_name": lead_name,
        "dept": DEPT,
        "dept_name": dept_name,
        "members": member_rows,
        "progress": progress,
        "overdue": overdue,
        "overdue_hours": OVERDUE_HOURS,
        "reports_to": REPORTS_TO,
        "paper_only": PAPER_ONLY,
        "read_only": READ_ONLY,
        "sources": {
            "tasks": str(tasks_p),
            "org": str(org_p),
            "tasks_ok": not tasks_err,
            "org_ok": not org_err,
        },
        "scope": {
            "tasks_total": len(tasks),
            "tasks_attributed": attributed,
            "tasks_unattributed": unattributed,
            "created_unparsable": created_unparsable,
        },
        "notes": notes,
    }


# ----------------------------------------------------------------------
# Markdown（给 A0 的摘要）
# ----------------------------------------------------------------------
def _counts(progress, mid):
    row = _as_dict(_as_dict(progress).get(mid))
    out = {}
    for key in STATUSES + ("overdue",):
        try:
            out[key] = int(row.get(key) or 0)
        except (TypeError, ValueError):
            out[key] = 0
    return out


def _totals_of(report):
    progress = _as_dict(report.get("progress"))
    row = _as_dict(progress.get(TOTAL_KEY))
    if row:
        return _counts({TOTAL_KEY: row}, TOTAL_KEY)
    agg = _empty_bucket()
    for member in report.get("members") or []:
        mid = member if isinstance(member, str) else str(_as_dict(member).get("id") or "")
        counts = _counts(progress, mid)
        for key in STATUSES + ("overdue",):
            agg[key] += counts[key]
    agg[TOTAL_KEY] = agg["doing"] + agg["review"] + agg["done"]
    return agg


def _cell(value, limit=0):
    text = " ".join(str(value or "").split()).replace("|", "丨")
    return text[:limit] if limit else text


def _source_label(sources, key):
    info = _as_dict(sources)
    path = str(info.get(key) or "")
    name = Path(path).name if path else key
    return "%s（%s）" % (name, "ok" if info.get("%s_ok" % key) else "缺失")


def render_markdown(report):
    """``build_daily`` 报告 → 给 A0 的 Markdown 日报摘要。"""
    report = _as_dict(report)
    day = str(report.get("date") or "")
    lead = str(report.get("lead") or LEAD_FALLBACK)
    lead_name = str(report.get("lead_name") or LEAD_NAME_FALLBACK)
    reports_to = str(report.get("reports_to") or REPORTS_TO)
    dept = str(report.get("dept") or DEPT)
    dept_name = str(report.get("dept_name") or DEPT_NAME_FALLBACK)
    hours = report.get("overdue_hours") or OVERDUE_HOURS
    progress = _as_dict(report.get("progress"))
    member_rows = [m for m in report.get("members") or [] if m]
    overdue = [row for row in report.get("overdue") or [] if isinstance(row, dict)]
    notes = [str(n) for n in report.get("notes") or [] if str(n).strip()]

    lines = []
    lines.append("# D1 部门日报 %s（%s → %s）" % (day, lead_name, reports_to))
    lines.append("")
    lines.append("- 部门：%s（%s）｜部长：%s %s" % (dept_name, dept, lead, lead_name))
    lines.append("- 报呈：%s｜口径：只读聚合（不写台账 · 不派单 · 不下单）" % reports_to)
    lines.append(
        "- 数据源：%s / %s"
        % (_source_label(report.get("sources"), "tasks"), _source_label(report.get("sources"), "org"))
    )
    lines.append("")
    lines.append("## 一、成员进度")
    lines.append("")
    lines.append("| 成员 | 中文名 | 角色 | doing | review | done | 超期 |")
    lines.append("|---|---|---|---:|---:|---:|---:|")
    for member in member_rows:
        mid = member if isinstance(member, str) else str(_as_dict(member).get("id") or "")
        info = {} if isinstance(member, str) else _as_dict(member)
        counts = _counts(progress, mid)
        lines.append(
            "| %s | %s | %s | %d | %d | %d | %d |"
            % (
                mid or "—",
                _cell(info.get("name")) or "—",
                _cell(info.get("role")) or "—",
                counts["doing"],
                counts["review"],
                counts["done"],
                counts["overdue"],
            )
        )
    totals = _totals_of(report)
    lines.append(
        "| **合计** | — | — | %d | %d | %d | %d |"
        % (totals["doing"], totals["review"], totals["done"], totals["overdue"])
    )
    if not member_rows:
        lines.append("")
        lines.append("> ⚠ 成员名单缺失或为空，上表仅合计占位。")

    lines.append("")
    lines.append("## 二、超期未完成（doing/review 且 >%s 小时）" % hours)
    lines.append("")
    if overdue:
        lines.append("| 工单 | 负责人 | 状态 | 创建 | 已超期(h) | 摘要 |")
        lines.append("|---|---|---|---|---:|---|")
        for row in overdue:
            owner = _cell(row.get("owner")) or "—"
            owner_name = _cell(row.get("owner_name"))
            if owner_name:
                owner = "%s %s" % (owner, owner_name)
            age = row.get("age_hours")
            lines.append(
                "| %s | %s | %s | %s | %s | %s |"
                % (
                    _cell(row.get("id")) or "—",
                    owner,
                    _cell(row.get("status")) or "—",
                    _cell(row.get("created")) or "—",
                    "—" if age is None else age,
                    _cell(row.get("desc"), 60) or "—",
                )
            )
    else:
        lines.append("无超期工单。")

    lines.append("")
    lines.append("## 三、口径与边界")
    lines.append("")
    lines.append(
        "- 超期判据：status ∈ {doing, review} 且 created 距今 >%s 小时；"
        "created 缺失/不可解析的单跳过，不猜。" % hours
    )
    lines.append("- 只读：不写生产台账、不写 reviewed/handoff、不生成交易单、不派单。")
    lines.append(
        "- 报呈 %s；paper_only=%s，read_only=%s。"
        % (
            reports_to,
            str(bool(report.get("paper_only", PAPER_ONLY))).lower(),
            str(bool(report.get("read_only", READ_ONLY))).lower(),
        )
    )
    if notes:
        lines.append("")
        lines.append("> 数据源提示：" + "；".join(notes))
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------
def _emit(text):
    """stdout 输出；控制台编码不吃中文时退 UTF-8 字节（不因编码炸掉日报）。"""
    try:
        sys.stdout.write(text)
        sys.stdout.flush()
    except UnicodeEncodeError:
        buffer = getattr(sys.stdout, "buffer", None)
        if buffer is None:
            raise
        buffer.write(text.encode("utf-8"))
        buffer.flush()


def main(argv=None):
    parser = argparse.ArgumentParser(description="D1 部门日报（只读聚合，报呈 A0）")
    parser.add_argument("--json", action="store_true", help="输出 d1_daily/v1 JSON（默认 Markdown）")
    parser.add_argument("--day", default=None, help="报告日期 YYYY-MM-DD（默认今天）")
    parser.add_argument("--tasks", default=None, help="tasks.json 路径（默认 .worktrees/tasks.json）")
    parser.add_argument("--org", default=None, help="org.json 路径（默认 agent_platform/registry/org.json）")
    args = parser.parse_args(argv)
    try:
        report = build_daily(
            day=args.day, tasks_path=args.tasks, org_path=args.org, now=None
        )
    except D1DailyError as exc:
        sys.stderr.write("D1 部门日报生成失败[%s]：%s\n" % (exc.code, exc))
        return 2
    if args.json:
        _emit(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    else:
        _emit(render_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
