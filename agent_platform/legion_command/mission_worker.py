# -*- coding: utf-8 -*-
"""阶段4-1 Worker：自动接单低风险任务，在 worktree 用 LLM 生成文件改动并提交。

安全边界（先小范围）：
- 只处理 write_scope 全部位于页面/文档/静态资源内的任务；
- 只允许文本文件扩展名；禁止密钥模式；
- 只写当前任务 worktree；最多 4 文件；
- 写完运行语法检查；失败自动 git restore 回滚；
- 只 git commit，不 push、不 merge（合并仍走 devtask 评审门）。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
MISSIONS = REPO / "work" / "missions"
GATEWAY = "http://127.0.0.1:8796/v1/chat/completions"

SAFE_ROOTS = ("agent_platform/legion_command/static/", "portal/", "deliverables/", "docs/")
SAFE_EXT = {".md", ".txt", ".html", ".htm", ".js", ".css", ".json", ".py", ".svg"}
MAX_FILES = 4
MAX_CHARS_PER_FILE = 120000
SECRET_PAT = re.compile(r"(sk-[A-Za-z0-9_\-]{16,}|AKIA[0-9A-Z]{12,}|BEGIN (RSA |EC )?PRIVATE KEY|password\s*[:=]\s*['\"][^'\"]{6,})", re.I)


def _load(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception:
        return None


def _run(cmd: list, cwd: Path, timeout: int = 120):
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def _task_ctx(mission_id: str, task_id: str) -> dict:
    mdir = MISSIONS / mission_id
    plan = _load(mdir / "plan.json") or {}
    state = _load(mdir / "state.json") or {}
    task = (state.get("tasks") or {}).get(task_id) or {}
    if not task:
        return {}
    plan_task = next((t for t in (plan.get("tasks") or []) if t.get("id") == task_id), {})
    ticket = task.get("ticket_id") or plan_task.get("ticket_id")
    return {"mission_id": mission_id, "task_id": task_id, "ticket": ticket,
            "task": task, "plan": plan, "plan_task": plan_task,
            "worktree": REPO / ".worktrees" / str(ticket) if ticket else None}


def _safe_scope(scope: str) -> bool:
    s = str(scope or "").replace("\\", "/").lstrip("./")
    return any(s.startswith(root) for root in SAFE_ROOTS)


def _scope_ok(path: str, scopes: list) -> bool:
    p = str(path or "").replace("\\", "/").lstrip("./")
    if not p or ".." in p.split("/") or p.startswith("/"):
        return False
    suffix = Path(p).suffix.lower()
    if suffix not in SAFE_EXT:
        return False
    for sc in scopes or []:
        s = str(sc).replace("\\", "/").lstrip("./").rstrip("*")
        if s.endswith("/"):
            if p.startswith(s) or p == s.rstrip("/"):
                return True
        elif p == s or p.startswith(s.rstrip("/") + "/"):
            return True
    return False


def _collect_context(wt: Path, scopes: list) -> list:
    files = []
    seen = set()
    for sc in scopes or []:
        s = str(sc).replace("\\", "/").lstrip("./")
        base = wt / s
        if base.is_file() and base.suffix.lower() in SAFE_EXT:
            cand = [base]
        elif base.is_dir():
            cand = [p for p in base.rglob("*") if p.is_file() and p.suffix.lower() in SAFE_EXT][:6]
        else:
            cand = []
        for p in cand:
            rel = str(p.relative_to(wt)).replace("\\", "/")
            if rel in seen:
                continue
            seen.add(rel)
            try:
                txt = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            files.append({"path": rel, "content": txt[-12000:]})
    return files[:8]


def _prompt(ctx: dict, context: list) -> str:
    task = ctx["plan_task"] or ctx["task"]
    system = ("你是零界军团研发 worker。根据任务卡与现有文件，输出 JSON 文件修改方案。"
              "格式：{\"summary\":\"...\",\"files\":[{\"path\":\"仓库相对路径\",\"content\":\"完整新内容\"}]}。"
              "只能修改 write_scope 内文件；只输出 JSON；不要密钥；不要运行外部命令；保持现有风格。"
              "最多 %d 个文件。内容必须是完整文件，不是 diff。" % MAX_FILES)
    user = json.dumps({
        "mission": ctx["plan"].get("name"), "task": task,
        "write_scope": task.get("write_scope") or [],
        "acceptance": task.get("acceptance") or [],
        "notes": task.get("description") or "",
        "existing_files": context,
    }, ensure_ascii=False)
    return system, user


def _call_llm(ctx: dict) -> dict:
    system, user = _prompt(ctx, _collect_context(ctx["worktree"], (ctx["plan_task"] or ctx["task"]).get("write_scope") or []))
    body = json.dumps({"model": "auto", "stream": False,
                       "messages": [{"role": "system", "content": system},
                                    {"role": "user", "content": user}]}).encode()
    import urllib.request
    req = urllib.request.Request(GATEWAY, data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "X-DSH-Task": "coding",
                                          "X-DSH-Agent": str((ctx["task"].get("owner") or "DEV5"))})
    with urllib.request.urlopen(req, timeout=240) as r:
        data = json.loads(r.read().decode("utf-8", "replace"))
    text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b <= a:
        raise ValueError("worker_output_not_json")
    return json.loads(text[a:b + 1])


def _restore(wt: Path, files: list) -> None:
    try:
        _run(["git", "restore", "--", *files], wt, 60)
    except Exception:
        pass


def _syntax_check(wt: Path, files: list) -> str:
    for rel in files:
        p = wt / rel
        if p.suffix.lower() == ".py":
            r = _run([sys.executable, "-m", "py_compile", str(p)], wt, 60)
            if r.returncode != 0:
                return "py_compile_failed:" + rel + ":" + (r.stderr or "")[-300:]
        if p.suffix.lower() == ".js":
            try:
                r = _run(["node", "--check", str(p)], wt, 60)
                if r.returncode != 0:
                    return "node_check_failed:" + rel + ":" + (r.stderr or "")[-300:]
            except FileNotFoundError:
                pass
    return ""


def run_task(mission_id: str, task_id: str, dry_run: bool = False) -> dict:
    ctx = _task_ctx(mission_id, task_id)
    if not ctx:
        return {"ok": False, "error": "task_not_found"}
    task = ctx["plan_task"] or ctx["task"]
    scopes = task.get("write_scope") or []
    bad = [s for s in scopes if not _safe_scope(s)]
    if bad:
        return {"ok": False, "error": "unsafe_scope", "bad": bad,
                "allowed_roots": list(SAFE_ROOTS)}
    wt = ctx.get("worktree")
    if not wt or not wt.exists():
        return {"ok": False, "error": "worktree_missing", "ticket": ctx.get("ticket")}
    context = _collect_context(wt, scopes)
    if dry_run:
        return {"ok": True, "mode": "dry-run", "ticket": ctx["ticket"],
                "task": task.get("title"), "scopes": scopes,
                "existing_files": [x["path"] for x in context]}
    try:
        out = _call_llm(ctx)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "llm_failed", "detail": str(e)[:300]}
    files = out.get("files") or []
    if not isinstance(files, list) or not files:
        return {"ok": False, "error": "no_file_ops"}
    if len(files) > MAX_FILES:
        return {"ok": False, "error": "too_many_files"}
    ops = []
    for f in files:
        p = str((f or {}).get("path") or "")
        c = str((f or {}).get("content") or "")
        if not _scope_ok(p, scopes):
            return {"ok": False, "error": "path_outside_scope", "path": p}
        if len(c) > MAX_CHARS_PER_FILE:
            return {"ok": False, "error": "content_too_large", "path": p}
        if SECRET_PAT.search(c):
            return {"ok": False, "error": "secret_like_content", "path": p}
        ops.append((p, c))
    changed = []
    try:
        for rel, content in ops:
            p = wt / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            changed.append(rel)
        err = _syntax_check(wt, changed)
        if err:
            _restore(wt, changed)
            return {"ok": False, "error": "syntax_check_failed", "detail": err}
    except Exception as e:  # noqa: BLE001
        _restore(wt, changed)
        return {"ok": False, "error": "apply_failed", "detail": str(e)[:300]}
    # commit
    _run(["git", "add", "--", *changed], wt, 60)
    msg = "%s worker: %s" % (ctx["ticket"], str(out.get("summary") or task.get("title") or "")[:120])
    r = _run(["git", "-c", "i18n.commitEncoding=utf-8", "commit", "-m", msg], wt, 120)
    if r.returncode != 0:
        _restore(wt, changed)
        return {"ok": False, "error": "commit_failed", "detail": (r.stdout + r.stderr)[-500:]}
    state_path = MISSIONS / mission_id / "state.json"
    state = _load(state_path) or {}
    t = (state.get("tasks") or {}).get(task_id) or {}
    t["status"] = "review"
    t["worker"] = "mission-worker"
    t["worker_commit"] = _run(["git", "rev-parse", "--short", "HEAD"], wt, 30).stdout.strip()
    t["worker_files"] = changed
    t["worker_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    if state.get("tasks"):
        state["tasks"][task_id] = t
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return {"ok": True, "ticket": ctx["ticket"], "summary": out.get("summary"),
            "files": changed, "commit": t["worker_commit"]}


def poll(mission_id: str, limit: int = 1, dry_run: bool = False) -> dict:
    state = _load(MISSIONS / mission_id / "state.json") or {}
    out = []
    for tid, t in (state.get("tasks") or {}).items():
        if len(out) >= limit:
            break
        if t.get("status") in ("doing", "queued") and t.get("ticket_id"):
            out.append(run_task(mission_id, tid, dry_run))
    return {"ok": True, "mission_id": mission_id, "results": out}
