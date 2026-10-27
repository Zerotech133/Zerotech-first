# -*- coding: utf-8 -*-
"""研究流水线显式状态机（P0⑥ · WO-RESEARCH-FSM-20261027）。

把研究院 8 阶段从「口头流程」变成可校验状态机：
  intake → hypothesis → evidence → validation → review → joint_review → decision → closed

每个阶段：
  · 有明确 required_artifact schema（JSON，键存在且非空）；
  · 离开当前阶段必须提交当前阶段 artifact；写入 history（checkpoint）；
  · decision / closed 为**审批门**：当前阶段为门时，必须 `approve --by <人>` 才能继续；
  · 状态落 JSON 文件（原子写），进程中断后 `status/resume` 读取即可继续。

设计边界：纯本地、无 LLM、无网络、无新依赖；只写 `agent_platform/data/academy/research_fsm/`。
用法：
  python -X utf8 agent_platform/tools/research_fsm.py --root <root> init --topic AC-1 --title "..."
  python -X utf8 agent_platform/tools/research_fsm.py --root <root> status --topic AC-1
  python -X utf8 agent_platform/tools/research_fsm.py --root <root> advance --topic AC-1 --to hypothesis \
      --artifact '{"question":"...","object":"...","title":"..."}'
  python -X utf8 agent_platform/tools/research_fsm.py --root <root> approve --topic AC-1 --by 老田 --note "同意"
退出码：0 成功；2 输入/状态错误；3 审批门未过。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path
from typing import NoReturn

HERE = Path(__file__).resolve()
REPO = HERE.parents[2]
FSM_REL = Path("agent_platform") / "data" / "academy" / "research_fsm"
SCHEMA = "research_fsm/v1"

STAGES = [
    "intake", "hypothesis", "evidence", "validation",
    "review", "joint_review", "decision", "closed",
]
GATED = {"decision", "closed"}
REQUIRED = {
    "intake": ["title", "question", "object"],
    "hypothesis": ["hypothesis", "falsify_conditions", "evidence_scope"],
    "evidence": ["evidence", "sources"],
    "validation": ["method", "metrics", "result_summary"],
    "review": ["reviewer", "verdict", "notes"],
    "joint_review": ["participants", "verdict", "gaps"],
    "decision": ["decision", "reason", "signed_by"],
    "closed": ["closure_summary", "archive_path"],
}
STAGE_DESC = {
    "intake": "立项：问题/对象/标题",
    "hypothesis": "假设：核心假设+否定条件+证据口径",
    "evidence": "取证：证据清单+来源",
    "validation": "验证：方法/指标/初步结果",
    "review": "评审：评审人/结论/意见",
    "joint_review": "联评：参与方/结论/缺口",
    "decision": "决策门：Go/No-Go/补证+理由+签署人",
    "closed": "结项：结项摘要+归档路径",
}


def _today() -> str:
    return dt.date.today().isoformat()


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _root(root: str | Path | None) -> Path:
    return Path(root).resolve() if root else REPO


def _dir(root: Path) -> Path:
    return root / FSM_REL


def _safe(topic: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in topic) or "topic"


def _path(root: Path, topic: str) -> Path:
    return _dir(root) / (_safe(topic) + ".json")


def _read(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": "state_read_failed: %s" % exc}, ensure_ascii=False))
        raise SystemExit(2)


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _fail(msg: str, code: int = 2) -> "NoReturn":
    print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False))
    raise SystemExit(code)


def _validate_artifact(stage: str, artifact: dict) -> list[str]:
    missing = []
    for key in REQUIRED.get(stage, []):
        val = artifact.get(key)
        if val is None:
            missing.append(key)
        elif isinstance(val, str) and not val.strip():
            missing.append(key)
        # list/dict 允许为空（如 joint_review.gaps=[] 表示无缺口）
    return missing


def init(root: Path, topic: str, title: str = "", force: bool = False,
         dry_run: bool = False) -> dict:
    path = _path(root, topic)
    if path.exists() and not force:
        _fail("state_exists: %s（--force 覆盖）" % path)
    state = {
        "schema": SCHEMA, "topic": topic, "title": title,
        "stage": STAGES[0], "status": "active",
        "created_at": _now(), "updated_at": _now(),
        "approval": {}, "history": [],
    }
    if dry_run:
        return {"ok": True, "dry_run": True, "would_create": str(path), "state": state}
    _write(path, state)
    return {"ok": True, "path": str(path), "state": state}


def load_state(root: Path, topic: str) -> tuple[Path, dict]:
    path = _path(root, topic)
    if not path.is_file():
        _fail("state_not_found: %s" % path)
    return path, _read(path)


def _next_stage(stage: str) -> str | None:
    i = STAGES.index(stage)
    return STAGES[i + 1] if i + 1 < len(STAGES) else None


def status(root: Path, topic: str) -> dict:
    path, st = load_state(root, topic)
    nxt = _next_stage(st["stage"])
    return {
        "ok": True, "path": str(path), "topic": st["topic"], "title": st.get("title", ""),
        "stage": st["stage"], "stage_desc": STAGE_DESC[st["stage"]],
        "status": st.get("status", "active"),
        "next": nxt, "next_required": REQUIRED.get(st["stage"], []),
        "approval": st.get("approval") or {},
        "gated": st["stage"] in GATED,
        "history_n": len(st.get("history") or []),
        "resume_hint": ("需先 approve --by <人> 才能继续" if st["stage"] in GATED
                        and not (st.get("approval") or {}).get("by")
                        else ("advance --to %s --artifact {...}" % nxt if nxt else "已到终态")),
    }


def advance(root: Path, topic: str, to: str, artifact: dict,
            dry_run: bool = False) -> dict:
    path, st = load_state(root, topic)
    cur = st["stage"]
    nxt = _next_stage(cur)
    if nxt is None:
        _fail("already_closed: %s" % cur)
    if to != nxt:
        _fail("bad_transition: %s -> %s（只允许 -> %s）" % (cur, to, nxt))
    if cur in GATED and not (st.get("approval") or {}).get("by"):
        print(json.dumps({"ok": False, "error": "approval_required", "stage": cur,
                          "hint": "approve --by <人> --note <理由>"}, ensure_ascii=False))
        raise SystemExit(3)
    missing = _validate_artifact(cur, artifact)
    if missing:
        _fail("artifact_schema_failed: stage=%s missing=%s required=%s"
              % (cur, missing, REQUIRED.get(cur, [])))
    entry = {"from": cur, "to": to, "at": _now(),
             "artifact": artifact, "approval": st.get("approval") or {}}
    if dry_run:
        return {"ok": True, "dry_run": True, "transition": entry}
    st["history"] = (st.get("history") or []) + [entry]
    st["stage"] = to
    st["updated_at"] = _now()
    st["approval"] = {}
    _write(path, st)
    return {"ok": True, "path": str(path), "from": cur, "to": to,
            "stage": to, "next": _next_stage(to)}


def approve(root: Path, topic: str, by: str, note: str = "",
            dry_run: bool = False) -> dict:
    path, st = load_state(root, topic)
    cur = st["stage"]
    if cur not in GATED:
        _fail("not_gated: 当前阶段 %s 不是审批门（%s）" % (cur, sorted(GATED)))
    if not by or by in ("AI", "ai", "agent"):
        _fail("invalid_approver: 审批人必须为人类（不代签）")
    approval = {"by": by, "note": note, "at": _now(), "stage": cur}
    if dry_run:
        return {"ok": True, "dry_run": True, "would_approve": approval}
    st["approval"] = approval
    st["updated_at"] = _now()
    _write(path, st)
    return {"ok": True, "path": str(path), "stage": cur, "approval": approval}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="研究流水线 8 阶段状态机")
    ap.add_argument("--root", default=str(REPO))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init")
    p_init.add_argument("--topic", required=True)
    p_init.add_argument("--title", default="")
    p_init.add_argument("--force", action="store_true")

    p_status = sub.add_parser("status")
    p_status.add_argument("--topic", required=True)

    p_adv = sub.add_parser("advance")
    p_adv.add_argument("--topic", required=True)
    p_adv.add_argument("--to", required=True, choices=STAGES)
    p_adv.add_argument("--artifact", default="", help="JSON 字符串")
    p_adv.add_argument("--artifact-file", default="")

    p_appr = sub.add_parser("approve")
    p_appr.add_argument("--topic", required=True)
    p_appr.add_argument("--by", required=True)
    p_appr.add_argument("--note", default="")

    args = ap.parse_args(argv)
    root = _root(args.root)
    if args.cmd == "init":
        out = init(root, args.topic, args.title, force=args.force, dry_run=args.dry_run)
    elif args.cmd == "status":
        out = status(root, args.topic)
    elif args.cmd == "advance":
        text = ""
        if args.artifact_file:
            try:
                text = Path(args.artifact_file).read_text(encoding="utf-8")
            except OSError as exc:
                _fail("artifact_file_unreadable: %s" % exc)
        else:
            text = args.artifact
        try:
            artifact = json.loads(text) if text else {}
        except ValueError as exc:
            _fail("artifact_json_invalid: %s" % exc)
        if not isinstance(artifact, dict):
            _fail("artifact_must_be_object")
        out = advance(root, args.topic, args.to, artifact, dry_run=args.dry_run)
    else:
        out = approve(root, args.topic, args.by, args.note, dry_run=args.dry_run)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
