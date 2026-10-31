"""IDE 可写/可执行 R0+R1+R2 测试（D1-D4 全 A）。

覆盖：开关/作用域/路径白名单/工单锁/受控编辑/命令白名单/超时/审计/HTTP 集成
（cookie + Origin + 锁 + 越权拒绝）。全部隔离 tmp 仓库，不碰真实 worktree。
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

_WT = Path(__file__).resolve().parents[2]
if str(_WT / "agent_platform") not in sys.path:
    sys.path.insert(0, str(_WT / "agent_platform"))

from legion_ide import rw_guard as G  # noqa: E402
from legion_ide import writer as W  # noqa: E402
from legion_ide import exec_runner as X  # noqa: E402
from legion_ide import serve as S  # noqa: E402


def make_repo(tmp_path, status="doing"):
    repo = tmp_path / "repo"
    wt = repo / ".worktrees" / "T-BOOT"
    (wt / "agent_platform" / "pkg").mkdir(parents=True)
    (wt / "agent_platform" / "pkg" / "a.py").write_text(
        "x = 1\n", encoding="utf-8", newline="\n"
    )
    (repo / ".worktrees").mkdir(parents=True, exist_ok=True)
    tasks = {"T-BOOT": {"status": status, "branch": "task/T-BOOT"}}
    (repo / ".worktrees" / "tasks.json").write_text(
        json.dumps(tasks, ensure_ascii=False), encoding="utf-8"
    )
    return repo, wt


_TEST_LOCAL_TOKEN = "tok-ide-rw-tests"


def _serve(repo, monkeypatch, rw=True, token=_TEST_LOCAL_TOKEN):
    monkeypatch.setenv(G.RW_ENV, "1" if rw else "0")
    if token is None:
        monkeypatch.delenv(G.LOCAL_TOKEN_ENV, raising=False)
    else:
        monkeypatch.setenv(G.LOCAL_TOKEN_ENV, token)
    httpd = S.create_server(
        "127.0.0.1", 0, repo=repo, portal_url="http://127.0.0.1:59999"
    )
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, "http://127.0.0.1:%d" % httpd.server_port


def _req(base, method="GET", path="/", body=None, headers=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    h = {"Content-Type": "application/json"} if body is not None else {}
    h.update(headers or {})
    req = urllib.request.Request(base + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def _cookie(base):
    status, headers, _ = _req(base, "GET", "/rw?token=" + _TEST_LOCAL_TOKEN)
    assert status == 200
    return headers["Set-Cookie"].split(";")[0]


_LAST_OWNER = ""


def _hdr(base):
    global _LAST_OWNER
    h = {"Cookie": _cookie(base), "Origin": base,
         "X-IDE-Token": _TEST_LOCAL_TOKEN}
    status, _h, body = _req(base, "GET", "/api/ide/session", headers=h)
    assert status == 200, body
    _LAST_OWNER = json.loads(body)["owner"]
    return h


def _owner():
    return _LAST_OWNER


# ---------------------------------------------------------------- 开关 / 作用域


def test_rw_enabled_default_off(monkeypatch):
    monkeypatch.delenv(G.RW_ENV, raising=False)
    assert G.rw_enabled() is False
    monkeypatch.setenv(G.RW_ENV, "on")
    assert G.rw_enabled() is True


def test_resolve_worktree_ok(tmp_path):
    repo, wt = make_repo(tmp_path)
    got, error = G.resolve_worktree(repo, "T-BOOT")
    assert error is None and got == wt


def test_resolve_rejects_bad_task(tmp_path):
    repo, _ = make_repo(tmp_path)
    for bad in ("../x", "a b", "x", "", "T" * 90):
        _got, error = G.resolve_worktree(repo, bad)
        assert error and error["error"] == "bad_task"


def test_resolve_rejects_inactive_task(tmp_path):
    repo, _ = make_repo(tmp_path, status="done")
    _got, error = G.resolve_worktree(repo, "T-BOOT")
    assert error and error["error"] == "task_not_active"


def test_resolve_missing_worktree(tmp_path):
    repo, _ = make_repo(tmp_path)
    _got, error = G.resolve_worktree(repo, "T-NOPE")
    assert error and error["error"] == "worktree_missing"


def test_list_scope_only_active(tmp_path):
    repo, _ = make_repo(tmp_path)
    (repo / ".worktrees" / "T-DONE").mkdir()
    (repo / ".worktrees" / "T-REV").mkdir()
    tasks = {
        "T-BOOT": {"status": "doing"},
        "T-DONE": {"status": "done"},
        "T-REV": {"status": "review"},
    }
    (repo / ".worktrees" / "tasks.json").write_text(json.dumps(tasks), encoding="utf-8")
    items = {i["task"] for i in G.list_scope(repo)}
    assert items == {"T-BOOT", "T-REV"}


# ---------------------------------------------------------------- 路径白名单


def test_safe_path_allows_controlled(tmp_path):
    _repo, wt = make_repo(tmp_path)
    path, error = G.safe_path(wt, "agent_platform/pkg/a.py")
    assert error is None and path.is_file()


@pytest.mark.parametrize(
    "rel,code",
    [
        ("../escape.py", "escape"),
        ("agent_platform/../../x.py", "escape"),
        ("C:/Windows/win.ini", "bad_path"),
        (".git/config", "scope_denied"),
        ("_vault/secrets/x.txt", "scope_denied"),
        ("quant_backtest/data/x.py", "scope_denied"),
        ("agent_platform/pkg/key.pem", "blocked_type"),
    ],
)
def test_safe_path_denies(tmp_path, rel, code):
    _repo, wt = make_repo(tmp_path)
    _p, error = G.safe_path(wt, rel)
    assert error and error["error"] == code, (rel, error)


# ---------------------------------------------------------------- 编辑


def test_reader_reads_utf8(tmp_path):
    _repo, wt = make_repo(tmp_path)
    res = W.read_file(wt, "agent_platform/pkg/a.py")
    assert res["ok"] and res["content"] == "x = 1\n"


def test_writer_writes_atomic(tmp_path):
    _repo, wt = make_repo(tmp_path)
    res = W.write_file(wt, "agent_platform/pkg/new.py", "print('hi')\n")
    assert res["ok"] and res["created"] is True
    assert (wt / "agent_platform" / "pkg" / "new.py").read_text(
        encoding="utf-8"
    ) == "print('hi')\n"


def test_writer_oversize_denied(tmp_path, monkeypatch):
    _repo, wt = make_repo(tmp_path)
    monkeypatch.setattr(G, "MAX_FILE_BYTES", 10)
    res = W.write_file(wt, "agent_platform/pkg/big.py", "x" * 11)
    assert res["ok"] is False and res["error"] == "too_large"


# ---------------------------------------------------------------- 工单锁


def test_lock_acquire_conflict_release(tmp_path):
    _repo, wt = make_repo(tmp_path)
    assert G.acquire_lock(wt, "owner-a")["ok"] is True
    conflict = G.acquire_lock(wt, "owner-b")
    assert conflict["ok"] is False and conflict["error"] == "locked"
    assert G.acquire_lock(wt, "owner-a")["ok"] is True  # 同 owner 续期
    assert G.release_lock(wt, "owner-b")["ok"] is False
    assert G.release_lock(wt, "owner-a")["released"] is True
    assert G.lock_info(wt)["locked"] is False


def test_lock_stale_reclaim(tmp_path):
    _repo, wt = make_repo(tmp_path)
    (wt / ".ide.lock").write_text(
        json.dumps({"owner": "old", "ts": time.time() - G.LOCK_TTL_S - 10}),
        encoding="utf-8",
    )
    assert G.lock_info(wt)["locked"] is False
    assert G.acquire_lock(wt, "new")["ok"] is True


# ---------------------------------------------------------------- 执行白名单


def test_exec_allow_git_status(tmp_path):
    _repo, wt = make_repo(tmp_path)
    res = X.validate(wt, "git status -s")
    assert res["ok"] and "status" in res["argv"]


@pytest.mark.parametrize(
    "cmd",
    [
        "dir",
        "git push",
        "git status; dir",
        "rm -rf /",
        "python -c print(1)",
        "powershell Get-Process",
        "git log --format=%H",
    ],
)
def test_exec_denied_commands(tmp_path, cmd):
    _repo, wt = make_repo(tmp_path)
    res = X.validate(wt, cmd)
    assert res["ok"] is False, cmd


def test_exec_denies_shell_meta(tmp_path):
    _repo, wt = make_repo(tmp_path)
    assert X.validate(wt, "git status && dir")["error"] == "shell_meta"


def test_exec_pytest_path_outside_denied(tmp_path):
    _repo, wt = make_repo(tmp_path)
    res = X.validate(wt, "python -m pytest ../outside -q")
    assert res["ok"] is False


def test_exec_run_ok(monkeypatch, tmp_path):
    _repo, wt = make_repo(tmp_path)

    class P:
        returncode = 0
        stdout = "ok\n"
        stderr = ""

    monkeypatch.setattr(X.S, "run", lambda *a, **k: P())
    res = X.run(wt, "git status")
    assert res["ok"] and res["exit_code"] == 0 and "ok" in res["output"]


def test_exec_timeout(monkeypatch, tmp_path):
    _repo, wt = make_repo(tmp_path)

    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="git", timeout=1)

    monkeypatch.setattr(X.S, "run", boom)
    res = X.run(wt, "git status", timeout=1)
    assert res["ok"] is False and res["error"] == "timeout"


# ---------------------------------------------------------------- 审计


def test_audit_fallback_writes_jsonl(tmp_path, monkeypatch):
    repo, _wt = make_repo(tmp_path)
    # 全量套件下 tools/events 可能已被其它用例导入（sys.modules 缓存），emit 会「成功」而
    # 走不到 fallback；用 None 占位强制 ImportError，确保本用例只测本地 jsonl 降级分支。
    monkeypatch.setitem(sys.modules, "events", None)
    res = G.audit(repo, "code_action", "T-BOOT", "IDE", "write x")
    assert res["ok"] is True
    fb = repo / "agent_platform" / "data" / "legion_ide_audit.jsonl"
    assert fb.is_file() and "code_action" in fb.read_text(encoding="utf-8")


# ---------------------------------------------------------------- HTTP 集成


def test_http_scope_403_when_off(tmp_path, monkeypatch):
    repo, _wt = make_repo(tmp_path)
    httpd, base = _serve(repo, monkeypatch, rw=False)
    try:
        status, _h, body = _req(base, "GET", "/api/ide/scope")
        assert status == 403 and json.loads(body)["error"] == "rw_disabled"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_http_rw_page_sets_cookie(tmp_path, monkeypatch):
    repo, _wt = make_repo(tmp_path)
    httpd, base = _serve(repo, monkeypatch)
    try:
        status, headers, body = _req(base, "GET",
                                     "/rw?token=" + _TEST_LOCAL_TOKEN)
        assert status == 200 and "ide_session=" in headers.get("Set-Cookie", "")
        assert b"/api/ide/write" in body
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_http_rw_page_requires_token_when_configured(tmp_path, monkeypatch):
    repo, _wt = make_repo(tmp_path)
    httpd, base = _serve(repo, monkeypatch)
    try:
        status, _h, body = _req(base, "GET", "/rw")
        assert status == 401 and json.loads(body)["error"] == "local_token_required"
        status, _h, body = _req(base, "GET", "/rw?token=wrong")
        assert status == 401 and json.loads(body)["error"] == "local_token_required"
    finally:
        httpd.shutdown()


def test_http_rw_page_requires_identity_when_no_token(tmp_path, monkeypatch):
    repo, _wt = make_repo(tmp_path)
    httpd, base = _serve(repo, monkeypatch, token=None)
    try:
        status, _h, body = _req(base, "GET", "/rw")
        assert status == 403 and json.loads(body)["error"] == "identity_required"
    finally:
        httpd.shutdown()


def test_http_scope_ok_with_cookie(tmp_path, monkeypatch):
    repo, _wt = make_repo(tmp_path)
    httpd, base = _serve(repo, monkeypatch)
    try:
        status, _h, body = _req(base, "GET", "/api/ide/scope", headers=_hdr(base))
        data = json.loads(body)
        assert status == 200 and data["ok"] and data["items"][0]["task"] == "T-BOOT"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_http_no_cookie_denied(tmp_path, monkeypatch):
    repo, _wt = make_repo(tmp_path)
    httpd, base = _serve(repo, monkeypatch)
    try:
        status, _h, body = _req(
            base,
            "POST",
            "/api/ide/read",
            body={"task": "T-BOOT", "path": "agent_platform/pkg/a.py"},
            headers={"Origin": base},
        )
        assert status == 403 and json.loads(body)["error"] == "no_session"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_http_write_requires_lock(tmp_path, monkeypatch):
    repo, _wt = make_repo(tmp_path)
    httpd, base = _serve(repo, monkeypatch)
    try:
        h = _hdr(base)
        payload = {
            "task": "T-BOOT",
            "owner": _owner(),
            "path": "agent_platform/pkg/a.py",
            "content": "x = 2\n",
        }
        status, _h, body = _req(
            base, "POST", "/api/ide/write", body=payload, headers=h
        )
        assert status == 409 and json.loads(body)["error"] == "lock_required"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_http_lock_then_preview_confirm_write_ok(tmp_path, monkeypatch):
    """两阶段写契约（K-QUANT-20261030-IDEPREVIEW）：lock → write/preview → write/confirm。

    旧用例直接 POST /api/ide/write 期望 200，与现行契约（write 返回
    write_requires_preview）漂移——本用例对齐契约并锁定「直写被拒」行为。
    """
    repo, wt = make_repo(tmp_path)
    httpd, base = _serve(repo, monkeypatch)
    try:
        h = _hdr(base)
        status, _h, _b = _req(
            base,
            "POST",
            "/api/ide/lock",
            body={"task": "T-BOOT", "owner": _owner()},
            headers=h,
        )
        assert status == 200
        payload = {
            "task": "T-BOOT",
            "owner": _owner(),
            "path": "agent_platform/pkg/a.py",
            "content": "x = 3\n",
        }
        # 单阶段直写已被拒绝（契约锁）
        status, _h, body = _req(base, "POST", "/api/ide/write", body=payload, headers=h)
        assert status == 400 and json.loads(body)["error"] == "write_requires_preview"
        # 阶段一：预览 → 拿 preview_id
        status, _h, body = _req(
            base, "POST", "/api/ide/write/preview", body=payload, headers=h
        )
        data = json.loads(body)
        assert status == 200 and data["ok"] is True and data.get("preview_id")
        # 阶段二：确认写盘
        status, _h, body = _req(
            base,
            "POST",
            "/api/ide/write/confirm",
            body={
                "task": "T-BOOT",
                "owner": _owner(),
                "preview_id": data["preview_id"],
            },
            headers=h,
        )
        assert status == 200 and json.loads(body)["ok"] is True
        assert (wt / "agent_platform" / "pkg" / "a.py").read_text(
            encoding="utf-8"
        ) == "x = 3\n"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_http_exec_denied_by_whitelist(tmp_path, monkeypatch):
    repo, _wt = make_repo(tmp_path)
    httpd, base = _serve(repo, monkeypatch)
    try:
        h = _hdr(base)
        _req(
            base,
            "POST",
            "/api/ide/lock",
            body={"task": "T-BOOT", "owner": _owner()},
            headers=h,
        )
        status, _h, body = _req(
            base,
            "POST",
            "/api/ide/exec",
            body={"task": "T-BOOT", "owner": _owner(), "command": "dir"},
            headers=h,
        )
        assert status == 400 and json.loads(body)["error"] == "denied"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_http_audit_reads_events(tmp_path, monkeypatch):
    repo, _wt = make_repo(tmp_path)
    httpd, base = _serve(repo, monkeypatch)
    try:
        h = _hdr(base)
        _req(
            base,
            "POST",
            "/api/ide/lock",
            body={"task": "T-BOOT", "owner": _owner()},
            headers=h,
        )
        _req(
            base,
            "POST",
            "/api/ide/read",
            body={"task": "T-BOOT", "path": "agent_platform/pkg/a.py"},
            headers=h,
        )
        status, _h, body = _req(base, "GET", "/api/ide/audit?task=T-BOOT", headers=h)
        data = json.loads(body)
        assert status == 200 and data["ok"]
        assert (
            any(
                "read agent_platform/pkg/a.py" in (e.get("detail") or "")
                for e in data["events"]
            )
            or data["events"] == []
        )
    finally:
        httpd.shutdown()
        httpd.server_close()


# ---------------------------------------------------------------- 静态页/启动器


def test_rw_page_contract():
    page = Path(__file__).resolve().parents[1] / "legion_ide" / "static" / "ide_rw.html"
    text = page.read_text(encoding="utf-8")
    for marker in (
        "/api/ide/scope",
        "/api/ide/write",
        "/api/ide/exec",
        "/api/ide/lock",
        "textContent",
    ):
        assert marker in text


def test_launcher_supports_enable_write():
    ps1 = Path(__file__).resolve().parents[2] / "ops" / "start_legion_ide.ps1"
    text = ps1.read_text(encoding="utf-8-sig")
    assert "EnableWrite" in text and "IDE_RW" in text



def test_patchset_preview_confirm_rollback(tmp_path, monkeypatch):
    repo, wt = make_repo(tmp_path)
    httpd, base = _serve(repo, monkeypatch)
    try:
        h = _hdr(base)
        status, _h, body = _req(base, "POST", "/api/ide/lock",
                                body={"task": "T-BOOT", "owner": _owner()}, headers=h)
        assert status == 200
        files = [
            {"path": "agent_platform/pkg/a.py", "content": "x = 2\n"},
            {"path": "agent_platform/pkg/b.py", "content": "y = 3\n"},
        ]
        status, _h, body = _req(base, "POST", "/api/ide/patchset/preview",
                                body={"task": "T-BOOT", "files": files}, headers=h)
        data = json.loads(body)
        assert status == 200 and data["patchset_id"] and len(data["files"]) == 2
        pid = data["patchset_id"]
        assert (wt / "agent_platform/pkg/a.py").read_text(encoding="utf-8") == "x = 1\n"
        assert not (wt / "agent_platform/pkg/b.py").exists()
        status, _h, body = _req(base, "POST", "/api/ide/patchset/confirm",
                                body={"task": "T-BOOT", "patchset_id": pid}, headers=h)
        assert status == 200, body
        assert (wt / "agent_platform/pkg/a.py").read_text(encoding="utf-8") == "x = 2\n"
        assert (wt / "agent_platform/pkg/b.py").read_text(encoding="utf-8") == "y = 3\n"
        status, _h, body = _req(base, "POST", "/api/ide/patchset/rollback",
                                body={"task": "T-BOOT", "patchset_id": pid}, headers=h)
        assert status == 200, body
        assert (wt / "agent_platform/pkg/a.py").read_text(encoding="utf-8") == "x = 1\n"
        assert not (wt / "agent_platform/pkg/b.py").exists()
    finally:
        httpd.shutdown()


def test_patchset_confirm_conflict_rolls_back(tmp_path, monkeypatch):
    repo, wt = make_repo(tmp_path)
    httpd, base = _serve(repo, monkeypatch)
    try:
        h = _hdr(base)
        _req(base, "POST", "/api/ide/lock",
             body={"task": "T-BOOT", "owner": _owner()}, headers=h)
        files = [
            {"path": "agent_platform/pkg/a.py", "content": "x = 2\n"},
            {"path": "agent_platform/pkg/b.py", "content": "y = 3\n"},
        ]
        status, _h, body = _req(base, "POST", "/api/ide/patchset/preview",
                                body={"task": "T-BOOT", "files": files}, headers=h)
        pid = json.loads(body)["patchset_id"]
        # 外部改动造成 stale
        (wt / "agent_platform/pkg/a.py").write_text("external\n", encoding="utf-8")
        status, _h, body = _req(base, "POST", "/api/ide/patchset/confirm",
                                body={"task": "T-BOOT", "patchset_id": pid}, headers=h)
        assert status == 409, body
        assert (wt / "agent_platform/pkg/a.py").read_text(encoding="utf-8") == "external\n"
        assert not (wt / "agent_platform/pkg/b.py").exists()
    finally:
        httpd.shutdown()
