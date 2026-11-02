# -*- coding: utf-8 -*-
"""C1 激活后自检器测试（task-40）。

全部 mock（不依赖真实 8091/8734，不启停服务）：
  200 全通过 / API 404 / 8091 API 401 / 旧 snapshot 缺键或计数 / 网络异常 /
  /hq 200 登录页=PENDING 与 /hq 404=FAIL / 公共 snapshot 未登录 200=鉴权泄漏 / 未换血 PENDING /
  JSON 纯度 / Markdown 输出。
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "hq_c1_verify.py"

spec = importlib.util.spec_from_file_location("hq_c1_verify_under_test", TOOL)
c1 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c1)

B8091 = "http://m:8091"
B8734 = "http://p:8734"
import datetime as _dt

_PASS_START = "2026-11-03T07:04:46+08:00"
_PASS_TS = _dt.datetime.fromisoformat(_PASS_START).timestamp()
_STALE_START = "2026-11-03T06:00:00+08:00"
_STALE_TS = _dt.datetime.fromisoformat(_STALE_START).timestamp()
PROC_PASS = {"pid": 45552, "start": _PASS_START, "code_mtime": _PASS_TS - 3600, "error": ""}
PROC_STALE = {"pid": 1, "start": _STALE_START, "code_mtime": _STALE_TS + 3600, "error": ""}


def _snapshot(mutate=None) -> dict:
    commercial = {"schema": "hq/commercial/v1", "summary": {"tenants": 12}, "read_only": True}
    directives = {"schema": "hq/directives/v1", "ok": True,
                  "directives": [{"id": "DIR-T-1", "kind": "decision"}],
                  "summary": {"total": 1}}
    reports = {"schema": "hq/legion_reports/v1", "ok": True,
               "legions": [{"scope": {"legion": "acad1-legion"}, "status": "green"}],
               "summary": {"legions": 1}}
    roles = {"schema": "hq/roles/v1",
             "roles": [{"role": r} for r in ("TIANFENG", "ZHUGE", "ZHAOYUN",
                                             "HUANGZHONG", "GUANYU")],
             "summary": {"roles": 5}}
    snap = {"schema": "hq/snapshot/v1", "roles": roles,
            "agenda": {"items": []}, "directives": directives, "reports": reports,
            "commercial": commercial, "org": {}, "tasks": {}, "approvals": {},
            "budget": {}, "heartbeat": {}}
    if mutate:
        mutate(snap)
    return snap


def _resp_json(payload, code=200, headers=None):
    return c1.Resp(code, json.dumps(payload, ensure_ascii=False), headers or {})


def _healthy_fetch(snapshot=None, overrides=None, hq_resp=None, pub_snap_resp=None,
                   health_resp=None, error=None):
    """构造 mock fetch；overrides: {name: Resp} 覆盖指定 8091 API。"""
    snap = snapshot if snapshot is not None else _snapshot()
    overrides = overrides or {}

    def fetch(url: str):
        if error:
            return c1.Resp(None, "", {}, error, url)
        if url.startswith(B8091):
            if url.endswith("/health"):
                return health_resp or _resp_json({"ok": True, "ts": "t"})
            for name in c1.HQ_APIS:
                if url.endswith("/api/hq/" + name):
                    if name in overrides:
                        return overrides[name]
                    if name == "snapshot":
                        return _resp_json({"ok": True, "data": snap})
                    return _resp_json({"ok": True, "data": {"schema": "hq/%s/v1" % name}})
        if url.startswith(B8734):
            if url.endswith("/hq"):
                if hq_resp is not None:
                    return hq_resp
                return c1.Resp(302, "<html>302 Found</html>",
                               {"Location": "/login?next=/hq"})
            if url.endswith("/api/hq/snapshot"):
                if pub_snap_resp is not None:
                    return pub_snap_resp
                return _resp_json({"ok": False, "error": "unauthorized"}, code=401)
        return c1.Resp(404, '{"error":"not found"}', {})

    return fetch


def _run_main(argv, fetch, process_info=None):
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = c1.main(argv + ["--base-8091", B8091, "--base-8734", B8734],
                     fetch=fetch, process_info=process_info)
    return rc, buf.getvalue()


# ---------- 1) 全 PASS / JSON 纯度 ----------
def test_all_pass_json_pure():
    rc, out = _run_main(["--json"], _healthy_fetch(), PROC_PASS)
    assert rc == 0, out
    report = json.loads(out)  # stdout 必须纯 JSON
    assert report["schema"] == "hq/c1_verify/v1"
    assert report["verdict"] == "PASS"
    assert report["summary"]["fail"] == 0
    names = {r["name"]: r["status"] for r in report["rows"]}
    assert names["roles=5"] == "PASS"
    assert names["directives 非空"] == "PASS"
    assert names["reports.legions 非空"] == "PASS"
    assert names["commercial.summary"] == "PASS"
    assert names["/hq 未登录跳转"] == "PASS"
    assert names["/api/hq/snapshot 未登录=401"] == "PASS"
    assert names["8091 新代码生效（启动时间>C1代码mtime）"] == "PASS"


# ---------- 2) API 404 ----------
def test_api_404_fails():
    fetch = _healthy_fetch(overrides={
        "directives": c1.Resp(404, '{"error":"not found"}', {})})
    rc, out = _run_main(["--json"], fetch, PROC_PASS)
    assert rc == 1, out
    report = json.loads(out)
    assert report["verdict"] == "FAIL"
    bad = [r for r in report["rows"] if r["status"] == "FAIL"]
    assert any(r["name"] == "/api/hq/directives" for r in bad)


# ---------- 3) 8091 API 401（内部端点不应要求登录） ----------
def test_api_401_fails():
    fetch = _healthy_fetch(overrides={
        "roles": c1.Resp(401, '{"ok":false,"error":"unauthorized"}', {})})
    rc, out = _run_main(["--json"], fetch, PROC_PASS)
    assert rc == 1
    report = json.loads(out)
    row = [r for r in report["rows"] if r["name"] == "/api/hq/roles"][0]
    assert row["status"] == "FAIL"


# ---------- 4) 旧 snapshot 缺键/计数不符 ----------
def test_old_snapshot_missing_keys_fails():
    def mutate(snap):
        snap.pop("directives", None)
        snap["reports"] = {"legions": []}
        snap["roles"] = {"roles": [{"role": "TIANFENG"}], "summary": {"roles": 1}}
        snap["commercial"] = {"schema": "hq/commercial/v1"}

    rc, out = _run_main(["--json"], _healthy_fetch(snapshot=_snapshot(mutate)), PROC_PASS)
    assert rc == 1, out
    report = json.loads(out)
    bad = {r["name"] for r in report["rows"] if r["status"] == "FAIL"}
    assert "必需键齐全" in bad
    assert "roles=5" in bad
    assert "directives 非空" in bad
    assert "reports.legions 非空" in bad
    assert "commercial.summary" in bad
    assert report["verdict"] == "FAIL"


# ---------- 5) 网络异常 ----------
def test_network_error_fails():
    rc, out = _run_main(["--json"], _healthy_fetch(error="ConnectionRefusedError: refused"),
                        PROC_PASS)
    assert rc == 1
    report = json.loads(out)
    assert report["verdict"] == "FAIL"
    assert all(r["status"] == "FAIL" for r in report["rows"] if r["group"] in ("8091", "api"))
    assert any("网络" in r["detail"] for r in report["rows"])


# ---------- 6) /hq 200 登录页 = PENDING；--allow-pending 放行 ----------
def test_hq_login_page_pending_and_allow_pending():
    login = c1.Resp(200, "<title>登录 · 零界</title><a href='/login?next=/hq'>去登录</a>",
                    {"Content-Type": "text/html"})
    rc, out = _run_main(["--json"], _healthy_fetch(hq_resp=login), PROC_PASS)
    assert out
    report = json.loads(out)
    row = [r for r in report["rows"] if r["name"] == "/hq 未登录跳转"][0]
    assert row["status"] == "PENDING"
    assert report["verdict"] == "PENDING"
    assert rc == 2
    rc2, _ = _run_main(["--json", "--allow-pending"], _healthy_fetch(hq_resp=login), PROC_PASS)
    assert rc2 == 0


def test_hq_404_fails():
    fetch = _healthy_fetch(hq_resp=c1.Resp(404, '{"error":"not found"}', {}))
    rc, out = _run_main(["--json"], fetch, PROC_PASS)
    assert rc == 1
    report = json.loads(out)
    row = [r for r in report["rows"] if r["name"] == "/hq 未登录跳转"][0]
    assert row["status"] == "FAIL"


# ---------- 7) 公共 snapshot 未登录返回 200 = 鉴权泄漏 ----------
def test_public_snapshot_200_auth_leak_fails():
    fetch = _healthy_fetch(pub_snap_resp=_resp_json({"ok": True, "data": {}}))
    rc, out = _run_main(["--json"], fetch, PROC_PASS)
    assert rc == 1
    report = json.loads(out)
    row = [r for r in report["rows"] if r["name"] == "/api/hq/snapshot 未登录=401"][0]
    assert row["status"] == "FAIL"
    assert "鉴权失效" in row["detail"]


# ---------- 8) 未换血 = PENDING ----------
def test_activation_stale_pending():
    rc, out = _run_main(["--json"], _healthy_fetch(), PROC_STALE)
    assert rc == 2, out
    report = json.loads(out)
    row = [r for r in report["rows"] if r["group"] == "activation"][0]
    assert row["status"] == "PENDING"
    assert "换血" in row["detail"]
    assert report["verdict"] == "PENDING"
    rc2, _ = _run_main(["--json", "--allow-pending"], _healthy_fetch(), PROC_STALE)
    assert rc2 == 0


# ---------- 9) Markdown 输出 ----------
def test_markdown_output():
    rc, out = _run_main(["--markdown"], _healthy_fetch(), PROC_PASS)
    assert rc == 0
    assert "# C1 激活后自检报告" in out
    assert "判定=PASS" in out
    assert "| 组 | 检查项 | 状态 | 明细 |" in out
    assert "| api | /api/hq/directives | PASS |" in out
