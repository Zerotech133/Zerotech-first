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
import logging

try:
    from safe_http import safe_urlopen
except ImportError:  # 作为包导入时
    from legion_command.safe_http import safe_urlopen


HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
MISSIONS = REPO / "work" / "missions"
GATEWAY = "http://127.0.0.1:8796/v1/chat/completions"

SAFE_ROOTS = ("agent_platform/legion_command/static/", "portal/", "deliverables/", "docs/")
SAFE_EXT = {".md", ".txt", ".html", ".htm", ".js", ".css", ".json", ".py", ".svg"}
MAX_FILES = 4
MAX_CHARS_PER_FILE = 120000
SECRET_PAT = re.compile(r"(sk-[A-Za-z0-9_\-]{16,}|AKIA[0-9A-Z]{12,}|BEGIN (RSA |EC )?PRIVATE KEY|password\s*[:=]\s*['\"][^'\"]{6,})", re.I)
SAFE_CHECK_CMDS = {"python", "python.exe", "py", "pytest", "node", "npm", "git"}
CMD_PREFIX = ("python", "py ", "node", "npm", "pytest", "git ", "devtask", "mission_worker",
              "powershell", "pwsh", "cmd ", "curl", "wget", "ollama", "ffmpeg", "make ")
SLASH_CMD_RE = re.compile(r"^/[a-z][a-z0-9]*(?:-[a-z0-9]+)+$", re.I)


def _extract_command_snippets(text: str) -> list:
    """提取文档中的反引号/代码块命令片段。"""
    out = []
    for m in re.finditer(r"```(?:[a-zA-Z0-9_-]+)?\n(.*?)```", text, re.S):
        for ln in m.group(1).splitlines():
            s = ln.strip().lstrip("$").strip()
            if s:
                out.append(s)
    for m in re.finditer(r"`([^`\n]{2,})`", text):
        out.append(m.group(1).strip())
    return out


def _command_is_grounded(s: str, allowed: list) -> bool:
    s = str(s or "").strip()
    if not s:
        return True
    low = s.lower()
    command_like = (low.startswith(CMD_PREFIX) or SLASH_CMD_RE.match(s)
                    or any(x in low for x in ("devtask.py", "mission_worker.py")))
    if not command_like:
        return True
    for a in allowed or []:
        a = str(a).strip()
        if not a:
            continue
        if a in s or s in a or s.split()[:2] == a.split()[:2]:
            return True
    return False


def _validate_commands(ops: list, allowed: list) -> str:
    for rel, content in ops:
        if Path(rel).suffix.lower() not in (".md", ".txt", ".html", ".htm"):
            continue
        for snip in _extract_command_snippets(content):
            if not _command_is_grounded(snip, allowed):
                return "ungrounded_command:%s:%s" % (rel, snip[:120])
    return ""


def _run_checks(wt: Path, checks: list) -> str:
    for c in checks or []:
        if not isinstance(c, dict):
            continue
        typ = str(c.get("type") or "")
        rel = str(c.get("path") or "").replace("\\", "/")
        p = wt / rel if rel else None
        try:
            if typ == "file_exists":
                if not p or not p.is_file():
                    return "check_failed:file_exists:%s" % rel
            elif typ == "file_min_chars":
                if not p or not p.is_file() or len(p.read_text(encoding="utf-8", errors="replace")) < int(c.get("min") or 1):
                    return "check_failed:file_min_chars:%s" % rel
            elif typ in ("file_contains", "file_not_contains"):
                needle = str(c.get("text") or "")
                if not needle:
                    return "check_failed:%s_empty:%s" % (typ, rel)
                txt = p.read_text(encoding="utf-8", errors="replace") if p and p.is_file() else ""
                if typ == "file_contains" and needle not in txt:
                    return "check_failed:file_contains:%s" % rel
                if typ == "file_not_contains" and needle in txt:
                    return "check_failed:file_not_contains:%s" % rel
            elif typ == "cmd":
                cmd = c.get("cmd")
                if isinstance(cmd, str):
                    cmd = cmd.split()
                if not isinstance(cmd, list) or not cmd:
                    return "check_failed:cmd_empty"
                head = Path(str(cmd[0])).name.lower()
                if head not in SAFE_CHECK_CMDS:
                    return "check_failed:cmd_not_allowed:%s" % head
                r = _run(cmd, wt, int(c.get("timeout") or 120))
                if r.returncode != int(c.get("expect_rc") or 0):
                    return "check_failed:cmd:%s" % " ".join(cmd)[:120]
        except Exception as e:  # noqa: BLE001
            return "check_failed:%s:%s" % (typ, str(e)[:120])
    return ""


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
              "最多 %d 个文件。内容必须是完整文件，不是 diff。"
              "文档（.md）必须写成可直接照做的说明：给出真实命令、文件路径、安全边界、回滚步骤，"
              "至少 300 字；禁止只写标题或占位句（如「说明xxx」「列出xxx」）。"
              "文档中提到命令时，只能引用 allowed_commands；不得编造任何命令。"
              "输出必须满足 checks 列出的验收断言。" % MAX_FILES)
    user = json.dumps({
        "mission": ctx["plan"].get("name"), "task": task,
        "write_scope": task.get("write_scope") or [],
        "allowed_commands": task.get("allowed_commands") or [],
        "checks": task.get("checks") or [],
        "acceptance": task.get("acceptance") or [],
        "notes": task.get("description") or "",
        "existing_files": context,
    }, ensure_ascii=False)
    return system, user


WORKER_MODELS = ("auto", "glm-4-flash", "deepseek-chat")


def _extract_worker_json(text: str) -> dict:
    text = str(text or "").strip()
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        text = m.group(1)
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b <= a:
        raise ValueError("worker_output_not_json")
    data = json.loads(text[a:b + 1])
    if not isinstance(data, dict) or not isinstance(data.get("files"), list):
        raise ValueError("worker_output_schema_invalid")
    return data


def _call_llm(ctx: dict) -> dict:
    system, user = _prompt(ctx, _collect_context(ctx["worktree"], (ctx["plan_task"] or ctx["task"]).get("write_scope") or []))
    import urllib.request
    last = ""
    for model in WORKER_MODELS:
        try:
            body = json.dumps({"model": model, "stream": False,
                               "messages": [{"role": "system", "content": system},
                                            {"role": "user", "content": user}]}).encode()
            req = urllib.request.Request(GATEWAY, data=body, method="POST",
                                         headers={"Content-Type": "application/json",
                                                  "X-DSH-Task": "coding",
                                                  "X-DSH-Agent": str((ctx["task"].get("owner") or "DEV5"))})
            with safe_urlopen(req, timeout=240) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            if not str(text).strip():
                last = "%s: empty_response" % model
                continue
            return _extract_worker_json(text)
        except Exception as e:  # noqa: BLE001
            last = "%s: %s" % (model, str(e)[:120])
            continue
    raise ValueError("worker_llm_failed: " + (last or "no_model"))


def _restore(wt: Path, files: list) -> None:
    try:
        _run(["git", "restore", "--", *files], wt, 60)
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/legion_command/mission_worker.py", exc)


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
            except FileNotFoundError as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/legion_command/mission_worker.py", exc)
    return ""


def run_task(mission_id: str, task_id: str, dry_run: bool = False) -> dict:
    ctx = _task_ctx(mission_id, task_id)
    if not ctx:
        return {"ok": False, "error": "task_not_found"}
    task = ctx["plan_task"] or ctx["task"]
    scopes = task.get("write_scope") or []
    if task.get("worker_eligible") is False:
        return {"ok": False, "error": "worker_not_eligible",
                "reason": "task scope outside worker safe roots", "scopes": scopes,
                "allowed_roots": list(SAFE_ROOTS)}
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
                "allowed_commands": task.get("allowed_commands") or [],
                "checks": task.get("checks") or [],
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
        if p.lower().endswith(".md") and len(c.strip()) < 300:
            return {"ok": False, "error": "content_too_shallow",
                    "detail": "文档至少 300 字，必须含真实命令/路径/回滚步骤", "path": p}
        ops.append((p, c))
    cmd_err = _validate_commands(ops, task.get("allowed_commands") or [])
    if cmd_err:
        return {"ok": False, "error": "ungrounded_command", "detail": cmd_err}
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
        check_err = _run_checks(wt, task.get("checks") or [])
        if check_err:
            _restore(wt, changed)
            return {"ok": False, "error": "checks_failed", "detail": check_err}
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
    mdir = MISSIONS / mission_id
    state = _load(mdir / "state.json") or {}
    plan = _load(mdir / "plan.json") or {}
    pmap = {t.get("id"): t for t in (plan.get("tasks") or []) if isinstance(t, dict)}
    out = []
    for tid, t in (state.get("tasks") or {}).items():
        if len(out) >= limit:
            break
        if (pmap.get(tid) or {}).get("worker_eligible") is False:
            continue
        if t.get("status") in ("doing", "queued") and t.get("ticket_id"):
            out.append(run_task(mission_id, tid, dry_run))
    return {"ok": True, "mission_id": mission_id, "results": out}
