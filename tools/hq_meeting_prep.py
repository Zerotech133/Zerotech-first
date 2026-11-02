#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""HQ 五角色晨会议程准备（C2，2026-11-03）。

把「每日五角色晨会」的会前准备自动化成**只读草案**：
读五角色真身份（command_hq.roles）+ Directive（hq_directives）+ 军团报告（hq_reports）
+ 任务板，按「未派单 Directive > 红/黄军团 > 到期事项」挑选最多 3 个议题，
生成带 role_prompts / evidence_needed / desired_output / due / agenda_items 的草案。

**纪律（硬边界）**：
- 只读聚合，**不写生产会议**、不写任务、不自动派单、不调模型、不启停服务；
- 默认只打印；`--out PATH` 只写指定文件（.json / .md），不动 `agent_platform/data/**`；
- 角色提示（role_prompts）只给「意见征询提示」，**不伪造任何角色意见/结论**；
- 无真源数据 → fail-visible（ok=false, rc=2），不以空草案冒充成功。

用法：
  python -X utf8 tools/hq_meeting_prep.py                    # Markdown 草案（默认打印）
  python -X utf8 tools/hq_meeting_prep.py --json             # 纯 JSON（stdout 只输出 JSON）
  python -X utf8 tools/hq_meeting_prep.py --markdown         # 显式 Markdown
  python -X utf8 tools/hq_meeting_prep.py --max-topics 2 --date 2026-11-03
  python -X utf8 tools/hq_meeting_prep.py --out C:\Temp\hq_prep.json
  python -X utf8 tools/hq_meeting_prep.py --out C:\Temp\hq_prep.md --markdown

退出码：0 成功；2 fail-visible（无真源/无候选/参数/环境错误）。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import time
from datetime import date as _date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
AP = ROOT / "agent_platform"
MEETINGS = AP / "data" / "meetings" / "hq"
COUNCIL = ROOT / "portal" / "data"
TASKS = ROOT / ".worktrees" / "tasks.json"

SCHEMA = "hq/meeting_prep/v1"
READ_ONLY = True
NO_WRITE = True
ROLE_ORDER = ("TIANFENG", "ZHUGE", "ZHAOYUN", "HUANGZHONG", "GUANYU")
ROLE_CN = {"TIANFENG": "田丰", "ZHUGE": "诸葛亮", "ZHAOYUN": "赵云",
           "HUANGZHONG": "黄忠", "GUANYU": "关羽"}
KINDS = ("研发", "挖掘", "分析", "商业", "其他")
DUE_WINDOW_DAYS = 2          # 到期窗口：今天 + 2 天（含逾期）
MAX_TOPICS = 3
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")
UNRESOLVED_STATUSES = ("open", "draft", "pending")
LEGION_OWNER = {
    "acad1-legion": "ACAD1", "data-warehouse": "DATA1", "lusu-d1-flow": "D1",
    "quant-strategy-line": "STRAT1", "huangzhong-acceptance": "HUANGZHONG",
    "hq-command": "A0", "hq-governance": "ZHAOYUN", "hq-audit": "GUANYU",
}
DESIRED_OUTPUT = {
    "研发": "方案 + 代码/工单 + 验收命令（含 rc 与证据包路径）",
    "挖掘": "因子/策略结论 + 回测证据（样本/OOS/成本口径）",
    "分析": "研判报告 + 证据链（source_id/data_date/lineage）",
    "商业": "内容/行程/定价方案 + 验收口径",
    "其他": "结论 + owner + due + acceptance",
}
FALLBACK_ROLES = {
    "TIANFENG": {"seat_id": "A0", "duty": "目标拆解、任务 DAG、派单、汇总、推进",
                 "never": ["不代替四签", "不代替老田 L2"]},
    "ZHUGE": {"seat_id": "AI1", "duty": "反向质询、pre-mortem、找反例、挑战前提",
              "never": ["不拍板", "不写生产代码"]},
    "ZHAOYUN": {"seat_id": "ZHAOYUN", "duty": "独立证据复核、审计、经验归档",
                "never": ["不做业务决策", "不改生产"]},
    "HUANGZHONG": {"seat_id": "HUANGZHONG", "duty": "验收标准、抽样、质量终检",
                   "never": ["不代签", "不修代码"]},
    "GUANYU": {"seat_id": "GUANYU", "duty": "代码审计、技术债、门禁、整改建议",
               "never": ["不自行合并", "不碰生产"]},
}


# --------------------------------------------------------------------------- #
# 基础工具（只读）
# --------------------------------------------------------------------------- #
def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _rel(path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(ROOT.resolve())).replace("\\", "/")
    except (ValueError, OSError):
        return str(path).replace("\\", "/")


def _text(v, limit: int = 400) -> str:
    return str(v if v is not None else "").strip()[:limit]


def _title(text, limit: int = 200) -> str:
    """议题标题：去掉换行/连续空白，避免破坏 Markdown 标题与 JSON 可读性。"""
    return re.sub(r"\s+", " ", str(text if text is not None else "")).strip()[:limit]


def _load_tool(name: str):
    """按文件路径加载兄弟工具（避免 sys.path/同名包问题）。"""
    path = TOOLS / (name + ".py")
    spec = importlib.util.spec_from_file_location("hqmeet_" + name, path)
    if spec is None or spec.loader is None:
        raise ImportError("%s unavailable" % name)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _parse_date(value):
    s = _text(value, 40)
    if not s or not DATE_RE.match(s):
        return None
    try:
        return _date.fromisoformat(s[:10])
    except ValueError:
        return None


def _today(date_str: str | None = None) -> _date:
    parsed = _parse_date(date_str) if date_str else None
    return parsed or _date.today()


# --------------------------------------------------------------------------- #
# 数据读取（全部只读；失败/降级显式 warning）
# --------------------------------------------------------------------------- #
def _load_directives(meetings_dir, council_dir, tasks_file) -> tuple[dict, list]:
    warnings: list[str] = []
    try:
        hd = _load_tool("hq_directives")
        payload = hd.build_directives(meetings_dir, council_dir, tasks_file)
    except Exception as exc:  # noqa: BLE001 —— fail-visible
        return {"ok": False, "error": "directives_unavailable:%s" % type(exc).__name__,
                "searched": [], "warnings": [str(exc)[:200]], "directives": []}, \
               ["directives_unavailable:%s" % type(exc).__name__]
    warnings += list(payload.get("warnings") or [])
    return payload, warnings


def _load_reports(tasks_file, meetings_dir, council_dir) -> tuple[dict, list]:
    try:
        hr = _load_tool("hq_reports")
        payload = hr.build_reports(tasks_file, meetings_dir, council_dir)
    except Exception as exc:  # noqa: BLE001 —— 降级：红/黄规则不可用，但不假绿
        return ({"ok": False, "legions": [], "summary": {}},
                ["reports_unavailable:%s" % type(exc).__name__])
    warnings = list(payload.get("warnings") or [])
    if not payload.get("ok"):
        warnings.append("reports_fail:%s" % payload.get("error"))
    return payload, warnings


def _load_roles_meta() -> tuple[list[dict], list[str]]:
    """五角色真身份（command_hq.roles）；失败降级为静态说明并 warning。"""
    warnings: list[str] = []
    rows: list[dict] = []
    try:
        if str(AP) not in sys.path:
            sys.path.insert(0, str(AP))
        from command_hq import roles as hq_roles  # noqa: E402

        for binding in hq_roles.all_role_bindings():
            role = str(binding.get("role") or "")
            if role not in ROLE_ORDER:
                continue
            rows.append({
                "role": role,
                "name": binding.get("cn_name") or ROLE_CN[role],
                "seat": binding.get("seat_id") or "",
                "duty": binding.get("duty") or "",
                "never": list(binding.get("never") or []),
                "model": (binding.get("model") or {}).get("effective")
                or binding.get("llm_binding") or "",
                "prompt_exists": bool((binding.get("prompt") or {}).get("exists")),
            })
    except Exception as exc:  # noqa: BLE001 —— 降级但不伪造绑定
        warnings.append("roles_unavailable:%s" % type(exc).__name__)
    by_role = {r["role"]: r for r in rows}
    if len(by_role) < len(ROLE_ORDER):
        warnings.append("roles_partial:%d/%d" % (len(by_role), len(ROLE_ORDER)))
    for role in ROLE_ORDER:
        if role not in by_role:
            fb = FALLBACK_ROLES[role]
            by_role[role] = {"role": role, "name": ROLE_CN[role],
                             "seat": fb["seat_id"], "duty": fb["duty"],
                             "never": list(fb["never"]), "model": "",
                             "prompt_exists": False, "fallback": True}
    return [by_role[r] for r in ROLE_ORDER], warnings


# --------------------------------------------------------------------------- #
# 议题分类 / 到期判断 / 角色提示
# --------------------------------------------------------------------------- #
def _classify_kind(title: str, agenda_mod=None) -> str:
    """优先复用 hq_agenda._kind 的分类规则；不可用则用内置同口径规则。"""
    text = str(title or "")
    if agenda_mod is not None and hasattr(agenda_mod, "_kind"):
        try:
            kind = str(agenda_mod._kind(text))
            if kind in KINDS:
                return kind
        except Exception:  # noqa: BLE001
            pass
    if any(k in text for k in ("研发", "代码", "工单", "dev", "merge", "测试", "bug", "修复")):
        return "研发"
    if any(k in text for k in ("挖掘", "因子", "选股", "策略", "回测", "数据", "线索")):
        return "挖掘"
    if any(k in text for k in ("分析", "研究", "研判", "复盘", "报告")):
        return "分析"
    if any(k in text for k in ("商业", "客户", "定价", "销售", "订阅", "收入", "内容", "获客", "市场")):
        return "商业"
    return "其他"


def _due_state(due: str, today: _date, window: int = DUE_WINDOW_DAYS) -> str:
    d = _parse_date(due)
    if d is None:
        return ""
    if d < today:
        return "overdue"
    if d <= today + timedelta(days=window):
        return "due_soon"
    return ""


def _role_prompt(roles_meta: list[dict], role: str, topic_id: str, title: str,
                 evidence: list[str], kind: str) -> str:
    """确定性角色提示：只给意见征询提示，不生成/不伪造角色意见。"""
    meta = next((r for r in roles_meta if r["role"] == role), None) or {
        "role": role, "name": ROLE_CN[role], "seat": "", "duty": "", "never": []}
    never = "；".join(meta.get("never") or []) or "（未登记边界）"
    ev = "；".join(evidence[:6]) or "（无显式证据，会上补）"
    return "\n".join([
        "【%s｜%s / 注册身份 %s】议题 %s：%s" % (
            meta.get("name") or ROLE_CN[role], role, meta.get("seat") or "?",
            topic_id, title),
        "职责：%s" % (meta.get("duty") or "（未登记职责）"),
        "边界：%s；只出意见，不执行、不签字、不改生产。" % never,
        "证据要求：%s" % ev,
        "输出：按 RoleOpinion v1（hq_role_opinion/v1）给出 position"
        "（support|conditional|oppose|abstain）/recommendation/evidence[]/risks[]/"
        "conditions[]/escalate；无证据不得 verified。",
        "注：本提示由 hq_meeting_prep 只读生成，**未调用模型、未产生角色意见**；"
        "意见由会上五角色本人（或授权代理）给出。议题类型：%s。" % kind,
    ])


# --------------------------------------------------------------------------- #
# 候选议题构建与排序
# --------------------------------------------------------------------------- #
def _evidence_for(directive: dict, kind: str, category: list[str],
                  due_state: str, legion: str) -> list[str]:
    out: list[str] = []
    for ev in (directive.get("evidence") or [])[:8]:
        e = _text(ev, 240)
        if e:
            out.append(e)
    if directive.get("task_id"):
        out.append("task:%s" % directive["task_id"])
    out.append("meeting.hq 纪要（人工确认后落盘）：五日晨会 C2")
    out.append("五角色 RoleOpinion（每人 1 条；无证据不得 verified）")
    if "未派单" in category:
        out.append("派单证据：Directive → task/order（owner/due/acceptance）")
    if "红黄军团" in category:
        out.append("legion_report:%s（blockers 逐条证据）" % (legion or "unmapped"))
    if due_state:
        out.append("到期证据：due=%s（%s）" % (directive.get("due") or "", due_state))
    if kind in ("研发", "挖掘"):
        out.append("验收命令 + rc + 证据包路径（rc=0 不等于有产出）")
    return _dedupe(out)[:12]


def _candidate_from_directive(d: dict, today: _date, red: set, amber: set,
                              agenda_mod=None) -> dict | None:
    status = _text(d.get("status"), 24)
    kind_raw = _text(d.get("kind"), 24)
    title = _title(d.get("topic")) or "（无标题 Directive）"
    due = _text(d.get("due"), 40)
    legion = _text(d.get("legion"), 64) or "unmapped"
    unresolved = ((not d.get("task_id")) and status in UNRESOLVED_STATUSES
                  and kind_raw in ("decision", "order", "topic"))
    due_state = _due_state(due, today) if status != "closed" else ""
    is_red, is_amber = legion in red, legion in amber
    if not (unresolved or due_state or is_red or is_amber):
        return None
    cat: list[str] = []
    score = 0
    if unresolved:
        cat.append("未派单")
        score += 100
    if is_red:
        cat.append("红黄军团")
        score += 40
    elif is_amber:
        cat.append("红黄军团")
        score += 30
    if due_state:
        cat.append("到期")
        score += 12 if due_state == "overdue" else 10
    kind = _classify_kind(title, agenda_mod)
    source_id = _text(d.get("id"), 96) or ("DIR-%s" % title[:24])
    return {
        "source": "directive",
        "source_id": source_id,
        "directive": d,
        "legion": legion,
        "owner": _text(d.get("owner"), 32) or LEGION_OWNER.get(legion, "A0"),
        "title": title,
        "kind": kind,
        "due": due,
        "due_state": due_state,
        "category": cat,
        "score": score,
        "role_hint": _text(d.get("source_role"), 24),
        "created_at": _text(d.get("created_at"), 40),
        "status": status,
        "task_id": _text(d.get("task_id"), 64),
    }


def _candidate_from_legion(row: dict, today: _date) -> dict | None:
    scope = row.get("scope") or {}
    legion = _text(scope.get("legion"), 64)
    status = _text(row.get("status"), 16)
    if not legion or status not in ("red", "amber"):
        return None
    blockers = row.get("blockers") or []
    top = blockers[0] if isinstance(blockers, list) and blockers else {}
    desc = _text(top.get("desc") or top.get("reason"), 160)
    title = _title("军团 %s %s：%s" % (
        legion, "红" if status == "red" else "黄",
        desc or row.get("status_reason") or "查看 LegionReport blockers"))
    due = _text(row.get("eta"), 40)
    fake = {"id": "DIR-LEGION-%s-%s" % (legion, status), "kind": "topic",
            "topic": title, "owner": LEGION_OWNER.get(legion, "A0"),
            "legion": legion, "due": due, "status": "open", "task_id": "",
            "evidence": ["legion_report:%s#%s" % (legion, status),
                         "legion_report:%s#blockers_count=%s"
                         % (legion, row.get("blockers_count"))],
            "created_at": ""}
    return {
        "source": "legion_report",
        "source_id": fake["id"],
        "directive": fake,
        "legion": legion,
        "owner": fake["owner"],
        "title": title,
        "kind": _classify_kind(title),
        "due": due,
        "due_state": _due_state(due, today),
        "category": ["红黄军团"],
        "score": 45 if status == "red" else 35,
        "role_hint": "",
        "created_at": "",
        "status": "open",
        "task_id": "",
        "legion_status": status,
        "legion_blockers": row.get("blockers_count"),
    }


def _dedupe(items: list[str]) -> list[str]:
    seen, out = set(), []
    for x in items:
        k = str(x).strip()
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


def _rank(c: dict) -> tuple:
    due_d = _parse_date(c.get("due")) or _date.max
    # 主序：未派单(2) > 红/黄(1) > 仅到期(0)；同序内到期越早越前、创建越新越前
    primary = 2 if "未派单" in c["category"] else (
        1 if "红黄军团" in c["category"] else 0)
    secondary = 1 if "到期" in c["category"] else 0
    return (-primary, -secondary, due_d, -_created_ordinal(c), c["source_id"])


def _created_ordinal(c: dict) -> int:
    s = _text(c.get("created_at"), 40)
    if not DATE_RE.match(s):
        return 0
    try:
        return int(s[:10].replace("-", ""))
    except ValueError:
        return 0


def _select_candidates(directives: list, reports: dict, today: _date,
                       max_topics: int, agenda_mod=None) -> tuple[list, dict]:
    red, amber = set(), set()
    for row in (reports.get("legions") or []):
        if not isinstance(row, dict):
            continue
        legion = _text((row.get("scope") or {}).get("legion"), 64)
        st = _text(row.get("status"), 16)
        if not legion or legion == "unmapped":
            continue  # unmapped 不是真军团，不参与"红/黄军团"规则（如实不猜）
        if st == "red":
            red.add(legion)
        elif st == "amber":
            amber.add(legion)
    candidates: list[dict] = []
    seen_ids: set[str] = set()
    for d in directives:
        if not isinstance(d, dict):
            continue
        c = _candidate_from_directive(d, today, red, amber, agenda_mod)
        if c and c["source_id"] not in seen_ids:
            seen_ids.add(c["source_id"])
            candidates.append(c)
    # 红/黄军团即使没有对应 Directive 也必须上会（来自 LegionReport 真数据）
    covered_legions = {c["legion"] for c in candidates
                       if "红黄军团" in c["category"]}
    for row in (reports.get("legions") or []):
        if not isinstance(row, dict):
            continue
        legion = _text((row.get("scope") or {}).get("legion"), 64)
        if legion and legion != "unmapped" and legion not in covered_legions:
            c = _candidate_from_legion(row, today)
            if c and c["source_id"] not in seen_ids:
                seen_ids.add(c["source_id"])
                candidates.append(c)
    candidates.sort(key=_rank)
    selected = candidates[:max_topics]
    facts = {
        "candidates": len(candidates),
        "red_legions": sorted(red),
        "amber_legions": sorted(amber),
        "unresolved": sum(1 for c in candidates if "未派单" in c["category"]),
        "due": sum(1 for c in candidates if "到期" in c["category"]),
        "selected": len(selected),
    }
    return selected, facts


# --------------------------------------------------------------------------- #
# 草案构建（主函数；纯函数，不写盘）
# --------------------------------------------------------------------------- #
def _fail(error: str, searched: list, warnings: list) -> dict:
    return {"schema": SCHEMA, "ok": False, "error": error, "searched": searched,
            "warnings": warnings, "read_only": READ_ONLY, "no_write": NO_WRITE,
            "generated_at": _now(), "topics": [], "agenda_items": []}


def build_meeting_prep(meetings_dir=None, council_dir=None, tasks_file=None,
                       reports: dict | None = None, date_str: str | None = None,
                       max_topics: int = MAX_TOPICS, with_prompts: bool = True) -> dict:
    """生成今日晨会草案（纯函数；只读；不写会议/任务/服务）。"""
    meetings_dir = Path(meetings_dir or MEETINGS)
    council_dir = Path(council_dir or COUNCIL)
    tasks_file = Path(tasks_file or TASKS)
    today = _today(date_str)
    max_topics = max(1, min(int(max_topics or MAX_TOPICS), MAX_TOPICS))
    searched = [_rel(meetings_dir), _rel(council_dir / "council_new_topics.json"),
                _rel(tasks_file)]
    warnings: list[str] = []

    directives_payload, d_warn = _load_directives(meetings_dir, council_dir, tasks_file)
    warnings += d_warn
    if not directives_payload.get("ok"):
        return _fail("no_source_data", searched,
                     warnings + ["directives_fail:%s" % directives_payload.get("error")])
    directives = directives_payload.get("directives") or []

    if reports is None:
        reports, r_warn = _load_reports(tasks_file, meetings_dir, council_dir)
        warnings += r_warn
    else:
        warnings += list(reports.get("warnings") or [])
    roles_meta, role_warn = _load_roles_meta()
    warnings += role_warn
    try:
        agenda_mod = _load_tool("hq_agenda")
    except Exception:  # noqa: BLE001 —— 分类降级
        agenda_mod = None
        warnings.append("agenda_module_unavailable:classifier_fallback")

    selected, facts = _select_candidates(directives, reports or {}, today,
                                         max_topics, agenda_mod)
    if not selected:
        return _fail("no_candidates", searched, warnings + ["no_open_directive_or_legion"])

    role_cn = {r["role"]: r["name"] for r in roles_meta}
    topics = []
    for i, c in enumerate(selected, 1):
        topic_id = "HQ-%s-%02d" % (today.strftime("%Y%m%d"), i)
        d = c["directive"]
        evidence_needed = _evidence_for(d, c["kind"], c["category"],
                                        c["due_state"], c["legion"])
        roles_expected = list(ROLE_ORDER)
        role_prompts = {}
        if with_prompts:
            for role in roles_expected:
                role_prompts[role] = _role_prompt(
                    roles_meta, role, topic_id, c["title"], evidence_needed, c["kind"])
        agenda_items = [
            "① 背景与证据：%s（来源 %s）" % (c["title"], c["source_id"]),
            "② 五角色意见：%s 各出 1 条 RoleOpinion（无证据不得 verified）"
            % "、".join(role_cn[r] for r in roles_expected),
            "③ A0 决议：结论 + owner + due + acceptance（人工拍板，不代签）",
            "④ 派单/关闭：Directive → task/order；未派单进下次晨会红标",
        ]
        topics.append({
            "order": i,
            "topic_id": topic_id,
            "kind": c["kind"],
            "title": c["title"],
            "owner": c["owner"],
            "legion": c["legion"],
            "due": c["due"],
            "due_state": c["due_state"],
            "roles_expected": roles_expected,
            "role_prompts": role_prompts,
            "evidence_needed": evidence_needed,
            "desired_output": DESIRED_OUTPUT.get(c["kind"], DESIRED_OUTPUT["其他"]),
            "agenda_items": agenda_items,
            "priority": {
                "category": c["category"],
                "score": c["score"],
                "directive_id": c["source_id"],
                "status": c["status"],
                "task_id": c.get("task_id") or "",
                "legion_status": c.get("legion_status", ""),
            },
            "source_refs": [r for r in (d.get("evidence") or [])[:6] if r],
            "read_only": True,
        })

    return {
        "schema": SCHEMA,
        "ok": True,
        "read_only": READ_ONLY,
        "no_write": NO_WRITE,
        "date": today.isoformat(),
        "generated_at": _now(),
        "sources": {
            "meetings_dir": _rel(meetings_dir),
            "meetings": (directives_payload.get("sources") or {}).get("meetings"),
            "council_topics": (directives_payload.get("sources") or {}).get("council_topics"),
            "tasks_file": _rel(tasks_file),
            "tasks_available": (directives_payload.get("sources") or {}).get("tasks_available"),
            "directives": len(directives),
            "reports_ok": bool((reports or {}).get("ok")),
            "roles": len(roles_meta),
        },
        "selection": {
            "rules": ["未派单 Directive", "红/黄军团", "到期事项（今天+%d 天）" % DUE_WINDOW_DAYS],
            "max_topics": max_topics,
            **facts,
        },
        "roles": roles_meta,
        "topics": topics,
        "agenda_items": [
            {"order": t["order"], "topic_id": t["topic_id"], "kind": t["kind"],
             "title": t["title"], "owner": t["owner"], "due": t["due"]}
            for t in topics
        ],
        "warnings": _dedupe(warnings),
        "next": "A0 主持确认 → 人工在 /hq 晨会落纪要（本草案不写生产会议）；"
                "决议 → Directive（owner/due/acceptance）。",
    }


# --------------------------------------------------------------------------- #
# 渲染
# --------------------------------------------------------------------------- #
def _trim_prompt(prompt: str, lines: int = 5) -> str:
    rows = [x for x in str(prompt or "").splitlines() if x.strip()]
    return "\n".join(rows[:lines])


def render_markdown(payload: dict) -> str:
    if not payload.get("ok"):
        lines = ["# 五角色晨会草案（生成失败）", "",
                 "- error：%s" % payload.get("error"),
                 "- searched：%s" % "；".join(payload.get("searched") or []),
                 "- 说明：无真源数据时不生成草案（fail-visible，不伪造）。"]
        for w in payload.get("warnings") or []:
            lines.append("- WARN：%s" % w)
        return "\n".join(lines) + "\n"
    src = payload["sources"]
    sel = payload["selection"]
    lines = [
        "# 五角色晨会草案 · %s" % payload["date"],
        "",
        "- 性质：**只读草案**（hq_meeting_prep 生成；未写生产会议/未派单/未调模型）",
        "- 生成：%s｜来源：meetings=%s 议题=%s tasks=%s directives=%s reports=%s"
        % (payload.get("generated_at"), src.get("meetings"), src.get("council_topics"),
           "on" if src.get("tasks_available") else "off", src.get("directives"),
           "ok" if src.get("reports_ok") else "degraded"),
        "- 挑选规则：%s；候选 %d → 选中 %d（上限 %d）"
        % (" > ".join(sel.get("rules") or []), sel.get("candidates", 0),
           sel.get("selected", 0), sel.get("max_topics", 3)),
        "- 红/黄军团：%s / %s｜未派单候选 %d｜到期候选 %d"
        % (", ".join(sel.get("red_legions") or []) or "无",
           ", ".join(sel.get("amber_legions") or []) or "无",
           sel.get("unresolved", 0), sel.get("due", 0)),
        "",
        "## 五角色",
        "",
        "| 角色 | 注册身份 | 职责 | 模型 |",
        "|---|---|---|---|",
    ]
    for r in payload.get("roles") or []:
        lines.append("| %s（%s） | %s | %s | %s |"
                     % (r.get("name"), r.get("role"), r.get("seat") or "-",
                        r.get("duty") or "-", r.get("model") or "-"))
    lines += ["", "## 今日议程（最多 3 项）", ""]
    for t in payload.get("topics") or []:
        lines += [
            "### %d. [%s] %s" % (t["order"], t["kind"], t["title"]),
            "",
            "- topic_id：%s｜owner：%s｜legion：%s" % (
                t["topic_id"], t["owner"], t["legion"] or "-"),
            "- due：%s｜状态：%s｜优先级：%s"
            % (t["due"] or "未定（会上定）",
               t.get("due_state") or "正常",
               " / ".join((t.get("priority") or {}).get("category") or []) or "-"),
            "- 期望产出：%s" % t["desired_output"],
            "- 证据要求：%s" % "；".join(t.get("evidence_needed") or []),
            "- 议程项：%s" % "；".join(t.get("agenda_items") or []),
            "",
            "角色提示（只读提示，不含角色意见）：",
        ]
        prompts = t.get("role_prompts") or {}
        for role in t.get("roles_expected") or []:
            p = prompts.get(role) or "（未生成，--no-prompts）"
            first = _trim_prompt(p, 1)
            lines.append("  - `%s`：%s" % (role, first))
        lines += ["", "<details><summary>角色提示全文（JSON 见 --json）</summary>", ""]
        for role in t.get("roles_expected") or []:
            p = prompts.get(role) or ""
            if p:
                lines += ["**%s**" % role, "", "```text", _trim_prompt(p, 6), "```", ""]
        lines += ["</details>", ""]
    lines += [
        "## 边界",
        "",
        "1. 本草案**不写生产会议**：确认后在 `/hq` 晨会落纪要（hq_meeting 服务端记录）；",
        "2. **不自动派单**：派单由 A0/田丰确认后走 Directive/task 单；",
        "3. **角色提示不伪造意见**：prompts 只是意见征询提示；真实 RoleOpinion 由五角色给出；",
        "4. 无真源数据时 fail-visible（rc=2），不以空草案冒充成功。",
    ]
    if payload.get("warnings"):
        lines += ["", "## 警告", ""] + ["- %s" % w for w in payload["warnings"]]
    return "\n".join(lines) + "\n"


def dumps_json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=1)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _write_out(payload: dict, out_path: Path, markdown: bool) -> None:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    text = render_markdown(payload) if (markdown or out_path.suffix.lower() == ".md") \
        else dumps_json(payload)
    out_path.write_text(text, encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="HQ 五角色晨会议程准备（C2，只读草案）")
    ap.add_argument("--json", action="store_true", help="stdout 输出纯 JSON")
    ap.add_argument("--markdown", action="store_true", help="输出 Markdown（默认）")
    ap.add_argument("--out", default="", help="只写指定文件（.json/.md；不写生产数据）")
    ap.add_argument("--date", default="", help="基准日期 YYYY-MM-DD（默认今天）")
    ap.add_argument("--max-topics", type=int, default=MAX_TOPICS)
    ap.add_argument("--meetings-dir", default=str(MEETINGS))
    ap.add_argument("--council-dir", default=str(COUNCIL))
    ap.add_argument("--tasks-file", default=str(TASKS))
    ap.add_argument("--no-prompts", action="store_true", help="跳过角色提示生成（调试/测试）")
    args = ap.parse_args(argv)
    try:
        payload = build_meeting_prep(
            meetings_dir=args.meetings_dir, council_dir=args.council_dir,
            tasks_file=args.tasks_file, date_str=args.date or None,
            max_topics=args.max_topics, with_prompts=not args.no_prompts)
    except Exception as exc:  # noqa: BLE001 —— fail-visible，不吞异常
        payload = _fail("internal_error:%s" % type(exc).__name__,
                        [args.meetings_dir, args.council_dir, args.tasks_file],
                        [str(exc)[:200]])
    if args.out:
        try:
            _write_out(payload, Path(args.out), markdown=args.markdown)
            print("已写出：%s（只读草案；未写生产会议/任务）" % args.out)
        except OSError as exc:
            print("[FAIL] 写 %s 失败：%s" % (args.out, exc), file=sys.stderr)
            return 2
    elif args.json:
        print(dumps_json(payload))
    else:
        print(render_markdown(payload), end="")
    if not payload.get("ok"):
        if not args.json and not args.out:
            print("[FAIL] %s | searched=%s"
                  % (payload.get("error"), payload.get("searched")), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
