#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HQ 会议纪要草稿器（C2，dry-run；2026-11-03）。

把「五角色晨会草案（hq_meeting_prep --json）+ 五角色 RoleOpinion（可缺省）」整理为
**hq_meeting v2 格式的纪要草稿**，默认 dry-run：只打印，不写生产会议、不调模型、不自动派单。

真源与契约（全部只读）：
- 议程输入：``tools/hq_meeting_prep.py --json``（schema ``hq/meeting_prep/v1``）或其落盘文件；
- 意见输入：RoleOpinion v1（``agent_platform/command_hq/role_contract.py``：schema
  ``hq_role_opinion/v1``，position/evidence/risks/conditions/escalate 校验）；
- 纪要格式：``agent_platform/legion_command/hq_meeting.py``（schema 2.0 的
  agenda/attendees/transcript/decisions/orders/acceptance/status/source_verification）。

纪律（硬边界）：
- **默认 dry-run**：只打印草稿；``--out`` 只写调用者指定文件，且**拒绝**写到
  ``agent_platform/data/meetings/hq/``（生产会议目录）；
- ``--write`` 生产写入**本单不实现**（默认禁止；返回 rc=2 并说明须 A0 确认/另单）；
- 缺角色/无证据/非法意见 → 显式 ``missing``/``unverified``/``invalid``，**绝不伪造**角色意见；
- 不生成 decisions/orders（决议与派单由 A0 会上给出，草稿留空）；
- 无有效议程输入 → fail-visible（rc=2），不生成空纪要。

用法：
  python -X utf8 tools/hq_meeting_prep.py --json --out C:\Temp\agenda.json
  python -X utf8 tools/hq_meeting_record.py --agenda C:\Temp\agenda.json
  python -X utf8 tools/hq_meeting_record.py --agenda C:\Temp\agenda.json --opinions C:\Temp\opinions.json
  python -X utf8 tools/hq_meeting_record.py --agenda - --opinions - < (stdin 不支持双输入，二选一)
  python -X utf8 tools/hq_meeting_record.py --agenda C:\Temp\agenda.json --markdown
  python -X utf8 tools/hq_meeting_record.py --agenda C:\Temp\agenda.json --out C:\Temp\record.json
  python -X utf8 tools/hq_meeting_record.py --agenda C:\Temp\agenda.json --write   # rc=2（未实现）

退出码：0 成功（草稿）；2 fail-visible（缺/坏议程、坏意见 JSON、生产写入被拒、参数错误）。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
AP = ROOT / "agent_platform"
PROD_MEETINGS = AP / "data" / "meetings" / "hq"
ROLE_CONTRACT = AP / "command_hq" / "role_contract.py"

SCHEMA = "hq_meeting_record/draft/v1"
AGENDA_SCHEMA = "hq/meeting_prep/v1"
MEETING_SCHEMA = "2.0"
ROLE_ORDER = ("TIANFENG", "ZHUGE", "ZHAOYUN", "HUANGZHONG", "GUANYU")
ROLE_CN = {"TIANFENG": "田丰", "ZHUGE": "诸葛亮", "ZHAOYUN": "赵云",
           "HUANGZHONG": "黄忠", "GUANYU": "关羽"}
SEATS = ("A0", "AI1", "ZHAOYUN", "GUANYU", "HUANGZHONG", "LAOTIAN")
DEFAULT_ATTENDEES = ["A0", "AI1", "ZHAOYUN", "HUANGZHONG", "GUANYU"]
MINUTES_PER_TOPIC = 5          # 3 议题 ≤15 分钟（晨会运行卡）
MID_RE = re.compile(r"^hq-\d{8}-\d{6}$")
SOURCE_VERIFICATION = "dry_run_draft_not_server_recorded"
READ_ONLY = True


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _text(v, limit: int = 400) -> str:
    return str(v if v is not None else "").strip()[:limit]


def _load_role_contract():
    spec = importlib.util.spec_from_file_location("hq_role_contract_for_record", ROLE_CONTRACT)
    if spec is None or spec.loader is None:
        raise ImportError("role_contract unavailable")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _read_input(source: str, what: str) -> tuple[object, str]:
    """--agenda/--opinions：空 → error；'-' → stdin；否则读文件。→ (obj, error)。"""
    src = _text(source, 260)
    if not src:
        return None, "missing_%s" % what
    try:
        if src == "-":
            raw = sys.stdin.read()
        else:
            raw = Path(src).read_text(encoding="utf-8-sig")
    except OSError as exc:
        return None, "%s_read_error:%s" % (what, type(exc).__name__)
    try:
        return json.loads(raw), ""
    except ValueError as exc:
        return None, "%s_bad_json:%s" % (what, str(exc)[:120])


def _agenda_topics(agenda: dict) -> tuple[list, str]:
    if not isinstance(agenda, dict):
        return [], "agenda_not_object"
    if agenda.get("ok") is False:
        return [], "agenda_not_ok:%s" % _text(agenda.get("error"), 80)
    topics = agenda.get("topics")
    if not isinstance(topics, list) or not topics:
        return [], "agenda_no_topics"
    out = []
    for i, t in enumerate(topics):
        if not isinstance(t, dict):
            return [], "agenda_topic_%d_not_object" % i
        if not _text(t.get("topic_id"), 120) or not _text(t.get("title"), 300):
            return [], "agenda_topic_%d_missing_id_or_title" % i
        out.append(t)
    return out, ""


def _agenda_roles(agenda: dict, topics: list) -> list:
    rows = []
    for r in agenda.get("roles") or []:
        if isinstance(r, dict) and _text(r.get("role"), 24).upper() in ROLE_ORDER:
            rows.append({"role": _text(r.get("role"), 24).upper(),
                         "name": _text(r.get("name"), 40) or ROLE_CN[_text(r.get("role"), 24).upper()],
                         "seat": _text(r.get("seat"), 24)})
    known = {r["role"] for r in rows}
    for t in topics:
        for role in (t.get("roles_expected") or []):
            role = _text(role, 24).upper()
            if role in ROLE_ORDER and role not in known:
                rows.append({"role": role, "name": ROLE_CN[role], "seat": ""})
                known.add(role)
    return [r for r in rows if r["role"] in ROLE_ORDER]


def _evidence_refs(evidence) -> list[str]:
    refs = []
    for item in evidence or []:
        if isinstance(item, dict):
            ref = _text(item.get("ref"), 300)
            if ref:
                refs.append(ref)
        elif isinstance(item, str) and item.strip():
            refs.append(_text(item, 300))
    return refs


def _verified_evidence(evidence) -> int:
    return sum(1 for item in (evidence or [])
               if isinstance(item, dict) and item.get("verified") is True
               and _text(item.get("ref"), 300))


def _status_key(topic_id: str, role: str) -> tuple[str, str]:
    return (topic_id, role)


def _evaluate_opinions(opinions, topics: list, rc_mod) -> tuple[dict, list, list]:
    """校验意见 → (按(topic,role)的状态表, warnings, 输入条数)。不伪造任何意见。"""
    warnings: list[str] = []
    items = opinions
    if isinstance(opinions, dict) and isinstance(opinions.get("opinions"), list):
        items = opinions["opinions"]
    if items is None:
        items = []
    if not isinstance(items, list):
        return {}, ["opinions_not_list"], 0
    # 去重：同 (topic_id, role, round) 保留最新（复用 role_contract.dedupe_opinions）
    valid_for_dedupe = []
    invalid_rows = []
    for idx, op in enumerate(items):
        if not isinstance(op, dict):
            invalid_rows.append({"index": idx, "role": "", "topic_id": "",
                                 "status": "invalid", "errors": ["opinion 必须是对象"]})
            continue
        valid_for_dedupe.append(op)
    try:
        deduped = rc_mod.dedupe_opinions(valid_for_dedupe, by="role_round")["unique"]
    except Exception as exc:  # noqa: BLE001 —— 去重失败降级为原样（仍逐条校验）
        warnings.append("dedupe_failed:%s" % type(exc).__name__)
        deduped = valid_for_dedupe
    table: dict[tuple[str, str], dict] = {}
    for idx, op in enumerate(deduped):
        errors = rc_mod.validate_opinion(op)
        role = _text(op.get("role"), 24).upper()
        topic_id = _text(op.get("topic_id"), 120)
        base = {"role": role, "topic_id": topic_id,
                "position": _text(op.get("position"), 24),
                "recommendation": _text(op.get("recommendation"), 500),
                "escalate": bool(op.get("escalate")),
                "index": idx}
        if errors:
            base.update({"status": "invalid", "errors": [_text(e, 240) for e in errors],
                         "evidence": [], "verified_evidence": 0})
            table[_status_key(topic_id, role)] = base
            continue
        normalized = rc_mod.normalize_opinion(op)
        evidence = normalized.get("evidence") or []
        verified_n = _verified_evidence(evidence)
        base.update({
            "status": "verified" if verified_n > 0 else "unverified",
            "errors": [],
            "evidence": evidence,
            "verified_evidence": verified_n,
            "position": normalized.get("position"),
            "recommendation": normalized.get("recommendation"),
            "risks": normalized.get("risks") or [],
            "conditions": normalized.get("conditions") or [],
            "escalate_reason": normalized.get("escalate_reason") or "",
            "created_at": normalized.get("created_at") or "",
        })
        table[_status_key(topic_id, role)] = base
    for row in invalid_rows:
        table[_status_key(row["topic_id"], row["role"])] = {
            **row, "evidence": [], "verified_evidence": 0, "position": ""}
    for topic in topics:
        tid = _text(topic.get("topic_id"), 120)
        for role in ROLE_ORDER:
            if _status_key(tid, role) not in table:
                table[_status_key(tid, role)] = {
                    "role": role, "topic_id": tid, "status": "missing",
                    "position": "", "recommendation": "", "escalate": False,
                    "errors": [], "evidence": [], "verified_evidence": 0, "index": -1}
    return table, warnings, len(items)


def _build_draft(agenda: dict, opinions, rc_mod, opinions_source: str = "") -> dict:
    topics, err = _agenda_topics(agenda)
    if err:
        return _fail(err, [agenda if isinstance(agenda, dict) else {}], [])
    roles = _agenda_roles(agenda, topics)
    role_seat = {r["role"]: r["seat"] for r in roles}
    table, warnings, opinions_count = _evaluate_opinions(opinions, topics, rc_mod)

    # 议程（hq_meeting v2 agenda 结构）
    agenda_rows = []
    for t in topics:
        agenda_rows.append({
            "item": "[%s] %s" % (_text(t.get("kind"), 16) or "其他", _text(t.get("title"), 200)),
            "owner": _text(t.get("owner"), 24),
            "minutes": MINUTES_PER_TOPIC,
            "topic_id": _text(t.get("topic_id"), 120),
            "due": _text(t.get("due"), 40),
        })
    # 参会人：roles_expected 并集 → seat；A0 固定；只收合法 SEATS
    expected = []
    for t in topics:
        for role in (t.get("roles_expected") or ROLE_ORDER):
            role = _text(role, 24).upper()
            if role in ROLE_ORDER and role not in expected:
                expected.append(role)
    attendees = []
    for role in ("TIANFENG",) + tuple(r for r in ROLE_ORDER if r != "TIANFENG"):
        if role not in expected:
            continue
        seat = role_seat.get(role) or ""
        if seat in SEATS and seat not in attendees:
            attendees.append(seat)
    for seat in DEFAULT_ATTENDEES:
        if seat not in attendees:
            attendees.append(seat)
    attendees = [a for a in attendees if a in SEATS][:6] or list(DEFAULT_ATTENDEES)

    # transcript：只收真实存在且非 invalid 的意见（原样推荐结论）；缺角色不补
    transcript = []
    for t in topics:
        tid = _text(t.get("topic_id"), 120)
        for role in ROLE_ORDER:
            row = table.get(_status_key(tid, role)) or {}
            if row.get("status") in ("missing", "invalid"):
                continue
            seat = role_seat.get(role) or ""
            if seat not in SEATS:
                continue
            text = "[%s] %s" % (row.get("position") or "abstain",
                                row.get("recommendation") or "（无推荐结论）")
            transcript.append({
                "by": seat,
                "text": _text(text, 2000),
                "src": "role_opinion",
                "evidence": _evidence_refs(row.get("evidence"))[:10],
                "role": role,
                "topic_id": tid,
                "verified": row.get("status") == "verified",
            })

    role_status = []
    for t in topics:
        tid = _text(t.get("topic_id"), 120)
        for role in ROLE_ORDER:
            row = table.get(_status_key(tid, role)) or {}
            role_status.append({
                "topic_id": tid,
                "role": role,
                "name": ROLE_CN[role],
                "status": row.get("status") or "missing",
                "position": row.get("position") or "",
                "recommendation": row.get("recommendation") or "",
                "verified_evidence": int(row.get("verified_evidence") or 0),
                "errors": row.get("errors") or [],
            })
    counts = {k: sum(1 for r in role_status if r["status"] == k)
              for k in ("verified", "unverified", "invalid", "missing")}
    draft_id = "hq-%s-%s-draft" % (time.strftime("%Y%m%d"), time.strftime("%H%M%S"))
    assert not MID_RE.match(draft_id)  # 休眠护栏：草稿 id 不匹配生产 meeting_id 格式
    return {
        "schema": SCHEMA,
        "ok": counts["invalid"] == 0,
        "dry_run": True,
        "read_only": READ_ONLY,
        "generated_at": _now(),
        "agenda_source": {
            "schema": _text(agenda.get("schema"), 40),
            "date": _text(agenda.get("date"), 20),
            "topics": len(topics),
            "selection": agenda.get("selection") or {},
        },
        "opinions_source": _text(opinions_source, 260),
        "opinions_count": opinions_count,
        "meeting": {
            "schema_version": MEETING_SCHEMA,
            "meeting_id": draft_id,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "type": "morning",
            "agenda": agenda_rows,
            "attendees": attendees,
            "transcript": transcript,
            "decisions": [],
            "orders": [],
            "acceptance": [],
            "status": "draft",
            "source_verification": SOURCE_VERIFICATION,
            "note": "dry-run 纪要草稿：由 hq_meeting_record 由晨会草案+RoleOpinion 生成；"
                    "decisions/orders 由 A0 会上确认后填写；未写生产会议、未派单、未调模型。",
        },
        "role_opinion_status": role_status,
        "summary": {
            "topics": len(topics),
            "roles_expected": len(expected),
            "verified": counts["verified"],
            "unverified": counts["unverified"],
            "invalid": counts["invalid"],
            "missing": counts["missing"],
            "transcript": len(transcript),
            "decisions": 0,
            "orders": 0,
        },
        "warnings": _dedupe(warnings),
        "next": "A0 在会上确认：补缺失/未验证意见 → 填 decisions（owner/due/acceptance）"
                " → orders 派单；之后由 /hq 晨会服务落盘（本工具不写生产会议，--write 未实现）。",
    }


def _fail(error: str, searched: list, warnings: list) -> dict:
    return {"schema": SCHEMA, "ok": False, "dry_run": True, "read_only": READ_ONLY,
            "error": error, "searched": [_text(x, 260) for x in searched],
            "warnings": list(warnings or []), "generated_at": _now(),
            "meeting": None, "role_opinion_status": [], "summary": {}}


def _dedupe(items: list) -> list:
    seen, out = set(), []
    for x in items:
        key = str(x)
        if key and key not in seen:
            seen.add(key)
            out.append(x)
    return out


# --------------------------------------------------------------------------- #
# 渲染
# --------------------------------------------------------------------------- #
def render_markdown(payload: dict) -> str:
    if not payload.get("ok") and payload.get("meeting") is None:
        lines = ["# 晨会纪要草稿（生成失败）", "",
                 "- error：%s" % payload.get("error"),
                 "- 说明：fail-visible，不生成空纪要；不写生产会议。"]
        for w in payload.get("warnings") or []:
            lines.append("- WARN：%s" % w)
        return "\n".join(lines) + "\n"
    m = payload.get("meeting") or {}
    src = payload.get("agenda_source") or {}
    summ = payload.get("summary") or {}
    lines = [
        "# 五角色晨会纪要草稿 · %s（dry-run）" % (src.get("date") or "-"),
        "",
        "- meeting_id：`%s`（草稿 id，非生产 meeting_id 格式）" % m.get("meeting_id"),
        "- status：**draft**｜source_verification：`%s`" % m.get("source_verification"),
        "- 性质：dry-run，未写生产会议、未派单、未调模型；**decisions/orders 由 A0 会上填写**",
        "- 议程来源：`%s`（topics=%s）｜意见来源：`%s`（%s 条）"
        % (payload.get("agenda_source", {}).get("schema"),
           src.get("topics"), payload.get("opinions_source") or "（无）",
           payload.get("opinions_count")),
        "- 参会人：%s" % "、".join(m.get("attendees") or []),
        "",
        "## 议程（%d 项）" % len(m.get("agenda") or []),
        "",
        "| # | 议题 | owner | 分钟 | topic_id | due |",
        "|---|---|---|---:|---|---|",
    ]
    for i, a in enumerate(m.get("agenda") or [], 1):
        lines.append("| %d | %s | %s | %s | %s | %s |"
                     % (i, a.get("item"), a.get("owner") or "-", a.get("minutes"),
                        a.get("topic_id") or "-", a.get("due") or "-"))
    lines += ["", "## 五角色意见状态（%d 格：verified=%d / unverified=%d / invalid=%d / missing=%d）"
              % (summ.get("roles_expected", 0) * max(1, summ.get("topics", 0)),
                 summ.get("verified", 0), summ.get("unverified", 0),
                 summ.get("invalid", 0), summ.get("missing", 0)),
              "", "| topic_id | 角色 | 状态 | position | 已验证证据 | 问题 |",
              "|---|---|---|---|---:|---|"]
    for r in payload.get("role_opinion_status") or []:
        lines.append("| %s | %s（%s） | **%s** | %s | %s | %s |"
                     % (r["topic_id"], r["name"], r["role"], r["status"],
                        r.get("position") or "-", r.get("verified_evidence", 0),
                        "；".join(r.get("errors") or []) or "-"))
    lines += ["", "## Transcript（只含真实意见；缺角色不补）", ""]
    if m.get("transcript"):
        for tr in m["transcript"]:
            lines.append("- `%s`（%s）：%s" % (tr.get("by"), tr.get("role"),
                                              tr.get("text")))
    else:
        lines.append("- （无有效 RoleOpinion；五角色意见会上补齐，本工具不伪造）")
    lines += ["", "## 决议 / 派单", "",
              "- decisions：**空**（A0 会上拍板后填写 owner/due/acceptance）",
              "- orders：**空**（人工确认后派单；本工具不自动派单）",
              "", "## 下一步", "", "- " + (payload.get("next") or ""),
              "", "## 边界", "",
              "1. 不写生产会议（`agent_platform/data/meetings/hq/` 拒绝写入）；",
              "2. `--write` 生产写入未实现（默认禁止，需 A0 确认/另单）；",
              "3. 缺角色/无证据 → missing/unverified 显式标注，不伪造；",
              "4. 不调模型、不联网、不派单、不启停服务。"]
    if payload.get("warnings"):
        lines += ["", "## 警告", ""] + ["- %s" % w for w in payload["warnings"]]
    return "\n".join(lines) + "\n"


def dumps_json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=1)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _is_production_path(path: Path) -> bool:
    try:
        p = path.resolve()
        return str(p).lower().startswith(str(PROD_MEETINGS.resolve()).lower())
    except OSError:
        return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="HQ 会议纪要草稿器（C2，dry-run；不写生产会议）")
    ap.add_argument("--agenda", required=True,
                    help="议程 JSON：hq_meeting_prep --json 输出文件，或 '-' 读 stdin")
    ap.add_argument("--opinions", default="",
                    help="RoleOpinion JSON（数组或 {opinions:[...]}），可缺省")
    ap.add_argument("--json", action="store_true", help="stdout 输出纯 JSON 草稿")
    ap.add_argument("--markdown", action="store_true", help="输出 Markdown 人读版")
    ap.add_argument("--out", default="", help="只写指定文件（.json/.md；禁止生产会议目录）")
    ap.add_argument("--write", action="store_true",
                    help="生产写入（本单未实现：默认禁止，返回 rc=2）")
    args = ap.parse_args(argv)

    if args.write:
        print("[FAIL] production_write_disabled：本单不实现生产写入；"
              "落盘须 A0 确认后另单/由 /hq 晨会服务执行。", file=sys.stderr)
        print(dumps_json(_fail("production_write_disabled",
                               [str(PROD_MEETINGS)], [])))
        return 2

    agenda, err = _read_input(args.agenda, "agenda")
    if err:
        payload = _fail(err, [args.agenda], [])
    else:
        opinions, oerr = (None, "")
        if args.opinions:
            opinions, oerr = _read_input(args.opinions, "opinions")
        try:
            rc_mod = _load_role_contract()
        except Exception as exc:  # noqa: BLE001 —— fail-visible
            payload = _fail("role_contract_unavailable:%s" % type(exc).__name__,
                            [str(ROLE_CONTRACT)], [str(exc)[:160]])
        else:
            payload = _fail(oerr, [args.opinions], []) if oerr else \
                _build_draft(agenda, opinions, rc_mod, opinions_source=args.opinions)

    if args.out:
        out = Path(args.out)
        if _is_production_path(out):
            print("[FAIL] production_write_forbidden：禁止写生产会议目录 %s"
                  % PROD_MEETINGS, file=sys.stderr)
            print(dumps_json(_fail("production_write_forbidden", [str(PROD_MEETINGS)], [])))
            return 2
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            text = render_markdown(payload) if (args.markdown or out.suffix.lower() == ".md") \
                else dumps_json(payload)
            out.write_text(text, encoding="utf-8")
            print("已写出：%s（dry-run 草稿；未写生产会议/未派单）" % args.out)
        except OSError as exc:
            print("[FAIL] 写 %s 失败：%s" % (args.out, exc), file=sys.stderr)
            return 2
    elif args.json:
        print(dumps_json(payload))
    elif args.markdown:
        print(render_markdown(payload), end="")
    else:
        print(dumps_json(payload))  # 默认输出 hq_meeting v2 格式草稿（JSON）

    if not payload.get("ok"):
        if not args.out:
            print("[FAIL] %s | searched=%s"
                  % (payload.get("error"), payload.get("searched")), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
