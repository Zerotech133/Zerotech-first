# -*- coding: utf-8 -*-
"""seat_evidence_packet.py — 田丰取数门面：确定性证据包生成器（零 LLM / 零网络 / 只读源）。

用途（B 分工）：
  Hermes 田丰只做"取数门面"：用本脚本把 健康卡状态 / 工单账本 / 经验库 汇总成
  带来源+时间戳+sha256 的**证据包**（JSON+MD），供 a0_agent / GLM 判读；
  9B 不再负责自由复核与判读，避免路径保真差/输出截断/复核浅的问题。

边界：
  - 只读源文件；只写 --out 目录（默认 %LOCALAPPDATA%\aios\seat_brief\，可用 --out 覆盖）；
  - 不联网、不启动服务、不发消息、不改业务数据；
  - 缺源文件如实记 exists=False，不编造。

用法：
  python -X utf8 tools/seat_evidence_packet.py
  python -X utf8 tools/seat_evidence_packet.py --out %TEMP%\\seat_brief
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _repo_paths import repo_root  # noqa: E402

REPO = repo_root(Path(__file__))
DEFAULT_STATUS = REPO.parent / "STATUS.md"
DEFAULT_TASKS = REPO / ".worktrees" / "tasks.json"
DEFAULT_EXPERIENCE = (Path.home() / ".hermes" / "profiles" / "zhaoyun"
                      / "experience" / "supervision" / "EXPERIENCE.md")
DEFAULT_OUT = Path(os.environ.get("SEAT_EVIDENCE_OUT") or
                     (Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / "aios" / "seat_brief"))
STATUSES = ("doing", "review", "done", "aborted", "pending", "blocked")


def _sha(p: Path) -> str:
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        return ""


def _source(p: Path) -> dict:
    return {"path": str(p), "exists": p.is_file(),
            "size": p.stat().st_size if p.is_file() else 0,
            "sha256": _sha(p)}


def _tail_lines(p: Path, n: int = 40) -> list:
    if not p.is_file():
        return []
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return lines[-n:]


def _load_tasks(p: Path) -> dict:
    if not p.is_file():
        return {"exists": False, "counts": {}, "doing_review": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"exists": True, "error": "invalid_json", "counts": {}, "doing_review": []}
    if not isinstance(data, dict):
        return {"exists": True, "error": "tasks_not_dict", "counts": {},
                "doing_review": [], "total": 0}
    counts = {s: 0 for s in STATUSES}
    rows = []
    for tid, meta in data.items():
        if not isinstance(meta, dict):
            continue
        st = str(meta.get("status") or "")
        if st in counts:
            counts[st] += 1
        if st in ("doing", "review"):
            rows.append({"tid": tid, "status": st,
                         "desc": str(meta.get("desc") or "")[:200],
                         "owner": str(meta.get("owner") or ""),
                         "created": str(meta.get("created") or "")})
    rows.sort(key=lambda r: r.get("created") or "", reverse=True)
    return {"exists": True, "counts": counts,
            "doing_review": rows[:10], "total": len(data)}


def _parse_experience(p: Path) -> dict:
    if not p.is_file():
        return {"exists": False, "entries": []}
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"exists": True, "entries": []}
    # 审计 A-M1：HTML 注释里的「条目模板」不是真实经验，先剔除再解析。
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    entries = []
    blocks = re.split(r"(?=^## )", text, flags=re.M)
    for block in blocks:
        m = re.match(r"##\s+(EXP-SUPERVISION-\d+)", block)
        if not m:
            continue
        eid = m.group(1)

        def field(name):
            fm = re.search(r"\*\*%s\*\*[:：]\s*(.+)" % re.escape(name), block)
            return fm.group(1).strip() if fm else ""

        entries.append({"id": eid, "cue": field("cue"), "lesson": field("lesson"),
                        "evidence": field("evidence"), "confidence": field("confidence")})
    entries.sort(key=lambda e: e["id"])
    return {"exists": True, "entries": entries[-6:], "total": len(entries)}


def build(status_path: Path, tasks_path: Path, exp_path: Path) -> dict:
    return {
        "schema": "seat_evidence_packet/v1",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "seat": "tianfeng",
        "purpose": "取数门面：证据汇总，供 a0_agent/GLM 判读；本包不含判读结论",
        "sources": [_source(status_path), _source(tasks_path), _source(exp_path)],
        "health": {"path": str(status_path),
                   "tail": _tail_lines(status_path, 40)},
        "tickets": _load_tasks(tasks_path),
        "experience": _parse_experience(exp_path),
        "boundaries": ["只读源", "不联网", "不发消息", "不改业务数据", "不产出判读结论"],
    }


def render_md(packet: dict) -> str:
    lines = ["# 田丰证据包（取数门面，供 GLM/a0_agent 判读）", "",
             "- 生成时间：%s" % packet["generated_at"], ""]
    tk = packet["tickets"]
    lines += ["## 工单账本", "",
              "- 总单数：%s" % tk.get("total", "?"),
              "- 状态计数：%s" % json.dumps(tk.get("counts") or {}, ensure_ascii=False)]
    for r in tk.get("doing_review") or []:
        lines.append("- `%s` [%s] %s" % (r["tid"], r["status"], r["desc"]))
    lines += ["", "## 经验库（最近条目）", ""]
    for e in packet["experience"].get("entries") or []:
        lines.append("- **%s** confidence=%s cue=%s evidence=%s"
                     % (e["id"], e["confidence"] or "?", (e["cue"] or "")[:80],
                        (e["evidence"] or "")[:80]))
    lines += ["", "## 健康卡原文尾段（来源：%s）" % packet["health"]["path"], "", "```"]
    lines += packet["health"]["tail"]
    lines += ["```", "", "> 本包只做证据汇总；判读结论以 a0_agent(GLM-5.3) 产线为准。"]
    return "\n".join(lines)


def _emit_audit(json_path: Path, packet: dict) -> dict:
    """取数门面运行审计：落 dev_events（patrol_verdict，task/agent=A0）；失败静默。"""
    try:
        import events  # tools/events.py（同目录，脚本运行/测试均可导入）
        return events.emit(
            kind="patrol_verdict", task="A0", agent="A0",
            detail="evidence_packet %s tickets=%s experience=%s" % (
                Path(json_path).name,
                (packet.get("tickets") or {}).get("total", "?"),
                (packet.get("experience") or {}).get("total", 0)))
    except Exception:  # noqa: BLE001 —— 事件层不得挡住取数
        return {"ok": False}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="田丰取数门面：确定性证据包")
    ap.add_argument("--status", default=str(DEFAULT_STATUS))
    ap.add_argument("--tasks", default=str(DEFAULT_TASKS))
    ap.add_argument("--experience", default=str(DEFAULT_EXPERIENCE))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    packet = build(Path(args.status), Path(args.tasks), Path(args.experience))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    jp = out / ("evidence_packet_%s.json" % ts)
    mp = out / ("evidence_packet_%s.md" % ts)
    jp.write_text(json.dumps(packet, ensure_ascii=False, indent=1), encoding="utf-8")
    mp.write_text(render_md(packet), encoding="utf-8")
    audit = _emit_audit(jp, packet)
    if not args.quiet:
        print(json.dumps({"ok": True, "json": str(jp), "md": str(mp),
                          "tickets_doing_review": len(packet["tickets"].get("doing_review") or []),
                          "experience_entries": packet["experience"].get("total", 0),
                          "audit": audit}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
