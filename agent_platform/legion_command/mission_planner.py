# -*- coding: utf-8 -*-
"""Mission Planner（阶段1）：一份文档 → 任务 DAG → plan.json。

- 默认调用 8796 网关强模型做拆解；失败自动回退规则模板；
- 产物：work/missions/<mission_id>/{doc.md,plan.json}；
- 只规划，不派工、不建 worktree、不改代码。
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from pathlib import Path
import logging

try:
    from safe_http import safe_urlopen
except ImportError:  # 作为包导入时
    from legion_command.safe_http import safe_urlopen


HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
MISSIONS = REPO / "work" / "missions"
ORG = REPO / "agent_platform" / "registry" / "org.json"
GATEWAY = "http://127.0.0.1:8796/v1/chat/completions"

ALLOWED_FALLBACK = ["ARCH1", "DEV1", "DEV2", "DEV3", "DEV5", "DEV6", "DEV7", "QA1"]
DEFAULT_SCOPES = ["agent_platform/", "tools/", "portal/"]
PLANNER_PATHS = REPO / "config" / "planner_paths.json"
DEFAULT_ROUTES = {
    "/keys": {"frontend": ["agent_platform/legion_command/static/keypool_v2.html"],
              "backend": []},
    "/mission": {"frontend": ["agent_platform/legion_command/static/mission.html"], "backend": []},
    "/ide": {"frontend": ["portal/ide.html"], "backend": []},
}
_EXPLICIT_FILE_RE = re.compile(r"[A-Za-z0-9_./-]+\.(?:py|html|js|css|md|json|ya?ml)")


def _path_config() -> dict:
    try:
        d = json.loads(PLANNER_PATHS.read_text(encoding="utf-8"))
        if isinstance(d, dict) and isinstance(d.get("routes"), dict) and d["routes"]:
            return d
    except Exception:
        pass
    return {"routes": DEFAULT_ROUTES, "keywords": {}}


def _doc_files(doc: str) -> dict:
    """从文档提取显式文件路径 + 路由/关键词映射，返回 frontend/backend/all。"""
    doc = str(doc or "")
    cfg = _path_config()
    routes = cfg.get("routes") or DEFAULT_ROUTES
    lower = doc.lower()
    frontend, backend, all_files = [], [], []

    def _add(f: str, kind: str = "") -> None:
        f = str(f or "").strip().replace("\\", "/").lstrip("./")
        if not f or ".." in f.split("/") or f.startswith("/"):
            return
        if f not in all_files:
            all_files.append(f)
        if kind == "frontend" or (not kind and f.endswith((".html", ".js", ".css"))):
            if f not in frontend:
                frontend.append(f)
        elif kind == "backend" or (not kind and f.endswith((".py", ".ps1", ".json"))):
            if f not in backend:
                backend.append(f)

    for route, spec in routes.items():
        if route and route in doc and isinstance(spec, dict):
            for f in spec.get("frontend") or []:
                _add(f, "frontend")
            for f in spec.get("backend") or []:
                _add(f, "backend")
    for kw, spec in (cfg.get("keywords") or {}).items():
        if not kw or str(kw).lower() not in lower:
            continue
        if isinstance(spec, dict):
            for f in spec.get("frontend") or []:
                _add(f, "frontend")
            for f in spec.get("backend") or []:
                _add(f, "backend")
        else:
            for f in (spec if isinstance(spec, list) else [spec]):
                _add(str(f))
    for m in _EXPLICIT_FILE_RE.findall(doc):
        f = m.strip().replace("\\", "/").lstrip("./")
        if ".." in f.split("/") or f.startswith("/"):
            continue
        if f not in all_files:
            all_files.append(f)
        if f.endswith((".html", ".js", ".css")):
            if f not in frontend:
                frontend.append(f)
        elif f.endswith(".py"):
            if f not in backend:
                backend.append(f)
    return {"frontend": frontend, "backend": backend, "all": all_files}


WORKER_SAFE_ROOTS = ("agent_platform/legion_command/static/", "portal/", "deliverables/", "docs/")


def _worker_eligible(task: dict) -> bool:
    """仅在安全页面/文档范围内允许 Worker；后端代码任务强制人工。"""
    scopes = [str(s or "").replace("\\", "/").lstrip("./") for s in (task.get("write_scope") or [])]
    if not scopes:
        return False
    if any(s.endswith((".py", ".ps1", ".sql", ".sh")) for s in scopes):
        return False
    title = str(task.get("title") or "")
    if "后端" in title or "API" in title.upper():
        return False
    return all(any(s.startswith(root) for root in WORKER_SAFE_ROOTS) for s in scopes)


def _annotate_worker(tasks: list) -> list:
    for t in tasks:
        if isinstance(t, dict):
            t["worker_eligible"] = _worker_eligible(t)
    return tasks


def _allowed_owners() -> list:
    try:
        d = json.loads(ORG.read_text(encoding="utf-8"))
        out = []
        for dep in d.get("departments") or []:
            if dep.get("dept") in ("dev", "quality"):
                for a in dep.get("agents") or []:
                    if isinstance(a, str):
                        out.append(a)
        return sorted(set(out)) or ALLOWED_FALLBACK
    except Exception:
        return ALLOWED_FALLBACK


def _slug(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "-", name or "mission").strip("-")
    return (s[:40] or "mission")


def _mission_id(name: str) -> str:
    return "M-%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), _slug(name))


def _extract_json(text: str) -> dict:
    text = str(text or "").strip()
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        text = m.group(1)
    else:
        a, b = text.find("{"), text.rfind("}")
        if a >= 0 and b > a:
            text = text[a:b + 1]
    return json.loads(text)


def _llm_plan(name: str, doc: str) -> dict:
    owners = _allowed_owners()
    system = ("你是零界军团研发指挥官。把文档拆成可执行任务 DAG。只输出 JSON，不要解释。"
              "每个任务必须包含 id/title/owner/deps/write_scope/acceptance/model。"
              "owner 只能从 %s 中选；deps 引用已有任务 id；write_scope 必须优先精确到文件路径"
              "（无法确定时用目录）；每个任务尽量给出 checks（file_exists/file_contains/cmd）；"
              "acceptance 必须是可验证条件。最多 12 个任务，必须是 DAG（无环）。" % owners)
    user = "任务名称：%s\n\n文档：\n%s" % (name, doc[:12000])
    body = json.dumps({
        "model": "auto", "stream": False,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
    }).encode()
    req = urllib.request.Request(
        GATEWAY, data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "X-DSH-Task": "planning", "X-DSH-Agent": "ARCH1"})
    with safe_urlopen(req, timeout=180) as r:
        data = json.loads(r.read().decode("utf-8", "replace"))
    text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    return _extract_json(text)


def _fallback_plan(doc: str, name: str = "") -> dict:
    slug = _slug(name or "mission")
    files = _doc_files(doc)
    spec_path = "deliverables/specs/%s.md" % slug
    accept_path = "deliverables/acceptance/%s.md" % slug
    backend_scope = files["backend"][:4] or ["agent_platform/", "tools/"]
    frontend_scope = files["frontend"][:2] or ["portal/"]
    tasks = [
            {"id": "T1", "title": "需求澄清与架构方案", "owner": "ARCH1", "deps": [],
             "write_scope": [spec_path],
             "acceptance": ["spec/plan 文档完成", "接口与数据边界明确"],
             "model": "strong", "description": "锁定目标、边界、验收口径与架构方案；产出精确文档路径"},
            {"id": "T2", "title": "后端/AI/平台实现", "owner": "DEV1", "deps": ["T1"],
             "write_scope": backend_scope, "acceptance": ["核心功能可运行", "pytest 通过"],
             "model": "auto", "description": "实现核心逻辑与接口"},
            {"id": "T3", "title": "前端/交互实现", "owner": "DEV5", "deps": ["T1"],
             "write_scope": frontend_scope, "acceptance": ["页面可访问", "浏览器控制台无错误"],
             "model": "auto", "description": "实现用户可见页面"},
            {"id": "T4", "title": "测试与验收", "owner": "QA1", "deps": ["T2", "T3"],
             "write_scope": [accept_path],
             "acceptance": ["测试全绿", "验收证据落盘"], "model": "auto",
             "description": "验证并出验收证据；产出精确验收文档"},
    ]
    return {"source": "fallback", "tasks": _annotate_worker(tasks)}


def validate_plan(plan: dict) -> list:
    errs = []
    if not isinstance(plan, dict):
        return ["plan_not_dict"]
    tasks = plan.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        return ["tasks_empty"]
    owners = set(_allowed_owners())
    ids = []
    for t in tasks:
        if not isinstance(t, dict):
            errs.append("task_not_dict")
            continue
        tid = str(t.get("id") or "")
        if not tid:
            errs.append("task_missing_id")
            continue
        if tid in ids:
            errs.append("duplicate_id:" + tid)
        ids.append(tid)
        if str(t.get("owner") or "") not in owners:
            errs.append("bad_owner:%s:%s" % (tid, t.get("owner")))
        if not isinstance(t.get("deps") or [], list):
            errs.append("bad_deps:" + tid)
        ws = t.get("write_scope") or []
        if not isinstance(ws, list) or not ws:
            errs.append("missing_write_scope:" + tid)
        ac = t.get("acceptance") or []
        if not isinstance(ac, list) or not ac:
            errs.append("missing_acceptance:" + tid)
    idset = set(ids)
    deps = {str(t.get("id")): [x for x in (t.get("deps") or [])] for t in tasks if isinstance(t, dict) and t.get("id")}
    for tid, ds in deps.items():
        for d in ds:
            if d not in idset:
                errs.append("unknown_dep:%s:%s" % (tid, d))
    # cycle detect
    visiting, done = set(), set()

    def visit(n):
        if n in done:
            return True
        if n in visiting:
            return False
        visiting.add(n)
        for m in deps.get(n, []):
            if not visit(m):
                return False
        visiting.remove(n)
        done.add(n)
        return True

    for tid in ids:
        if not visit(tid):
            errs.append("cycle:" + tid)
            break
    return errs


def plan(name: str, doc: str, use_llm: bool = True,
         apply_planner_rules: bool = True) -> dict:
    name = str(name or "").strip() or "未命名任务"
    doc = str(doc or "")
    mid = _mission_id(name)
    mdir = MISSIONS / mid
    mdir.mkdir(parents=True, exist_ok=True)
    (mdir / "doc.md").write_text("# " + name + "\n\n" + doc, encoding="utf-8")

    raw = None
    source = "fallback"
    if use_llm:
        try:
            raw = _llm_plan(name, doc)
            if validate_plan(raw):
                raw = None
            else:
                source = "llm"
        except Exception:
            raw = None
    if raw is None:
        raw = _fallback_plan(doc, name)
    try:
        doc_path = str((mdir / "doc.md").relative_to(REPO)).replace("\\", "/")
    except ValueError:
        doc_path = str(mdir / "doc.md")
    result = {
        "schema": "mission_plan/v1",
        "mission_id": mid,
        "name": name,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "source": source,
        "doc_path": doc_path,
        "tasks": raw["tasks"],
    }
    _annotate_worker(result["tasks"])
    if apply_planner_rules:
        try:
            from legion_command import planner_rules
            active = planner_rules.active_rules()
            if active.get("enabled"):
                result = planner_rules.apply_rules(result, active)
                result["planner_rules_source"] = "accepted-signed"
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/legion_command/mission_planner.py", exc)
    errs = validate_plan(result)
    result["valid"] = not errs
    result["errors"] = errs
    (mdir / "plan.json").write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n",
                                    encoding="utf-8")
    return result


def load_plan(mission_id: str) -> dict:
    p = MISSIONS / str(mission_id) / "plan.json"
    return json.loads(p.read_text(encoding="utf-8"))


def list_missions() -> list:
    out = []
    for p in sorted(MISSIONS.glob("*/plan.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            out.append({"mission_id": d.get("mission_id"), "name": d.get("name"),
                        "created": d.get("created"), "source": d.get("source"),
                        "tasks": len(d.get("tasks") or []), "valid": d.get("valid")})
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/legion_command/mission_planner.py", exc)
    return out
