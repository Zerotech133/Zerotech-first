# -*- coding: utf-8 -*-
"""WO-LEGION-S22 军团指挥界面套件测试：快照聚合 + HTTP 端点 + dispatch 走门面。

承重：快照只读（不写任何真源）；dispatch 唯一写路径只落 bridge_inbox inbox/
（tmp 目录注入）；静态页路由白名单校验。
"""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

_PLATFORM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PLATFORM))

from gateway import inbox_gateway  # noqa: E402
from legion_command import snapshot as snap  # noqa: E402
from legion_command.snapshot import build_snapshot  # noqa: E402
from legion_command.serve import build_server  # noqa: E402


@pytest.fixture()
def store(tmp_path, monkeypatch):
    inbox_gateway.set_store(tmp_path)
    yield tmp_path
    inbox_gateway._DEFAULT_STORE = None


# ---------------------------------------------------------------- 快照聚合

def test_snapshot_real_org(tmp_path):
    data = snap.build_snapshot()
    assert data["ok"] is True
    assert data["schema_version"] == "1.2"  # S34 起 agents 增 has_profile 等字段
    s = data["summary"]
    assert s["departments"] >= 13
    assert s["agents"] >= 25  # org 在编 29（hr 55 档含编外，org.json 为真源）
    names = [d["name"] for d in data["departments"]]
    assert any("研发" in n for n in names)
    assert data["chief"]["agent_id"] == "A0"


def test_snapshot_missing_org_fail_visible(tmp_path):
    data = snap.build_snapshot(platform_dir=tmp_path)
    assert data["ok"] is False
    assert "org.json" in data["error"]


def test_snapshot_readonly(tmp_path):
    before = (_PLATFORM / "registry" / "org.json").read_bytes()
    snap.build_snapshot()
    after = (_PLATFORM / "registry" / "org.json").read_bytes()
    assert before == after


# ---------------------------------------------------------------- HTTP 端点

@pytest.fixture()
def server(store):
    srv = build_server(port=0)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield "http://127.0.0.1:%d" % srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def _get(base, path):
    with urllib.request.urlopen(base + path, timeout=10) as r:
        return r.status, r.read()


def _post(base, path, obj):
    req = urllib.request.Request(base + path,
                                 data=json.dumps(obj).encode("utf-8"),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def test_http_health_and_snapshot(server):
    st, body = _get(server, "/api/health")
    assert st == 200 and json.loads(body)["ok"] is True
    st, body = _get(server, "/api/snapshot")
    data = json.loads(body)
    assert data["ok"] is True and data["summary"]["departments"] >= 13


def test_http_index_and_static(server):
    st, body = _get(server, "/")
    assert st == 200 and b"\xe6\x8c\x87\xe6\x8c\xa5" in body  # 「指挥」UTF-8
    st, body = _get(server, "/static/map.html")
    assert st == 200 and len(body) > 1000
    st, body = _get(server, "/static/meeting.html")
    assert st == 200


def test_http_static_whitelist_blocks_traversal(server):
    try:
        urllib.request.urlopen(server + "/static/..%2Fserve.py", timeout=10)
        blocked = False
    except urllib.error.HTTPError as e:
        blocked = e.code == 404
    assert blocked


def test_http_dispatch_via_gateway_inbox(server, store):
    env = _post(server, "/api/dispatch",
                {"type": "work", "title": "Z5 对照实验",
                 "attendees": ["A0", "R2"], "note": "会终指令测试"})
    assert env["ok"] is True
    msg_id = env["data"]["id"]
    files = list((store / "inbox").glob("*.json"))
    assert len(files) == 1
    rec = json.loads(files[0].read_text(encoding="utf-8"))
    assert rec["kind"] == "task_request"
    assert rec["source"] == "warroom"
    assert rec["payload"]["order_type"] == "work"
    assert rec["payload"]["attendees"] == ["A0", "R2"]


def test_http_dispatch_idempotent(server, store):
    body = {"type": "study", "title": "同题重发", "attendees": []}
    e1 = _post(server, "/api/dispatch", body)
    e2 = _post(server, "/api/dispatch", body)
    assert e1["data"]["id"] == e2["data"]["id"]
    assert e2["data"]["replayed"] is True


def test_http_dispatch_bad_type(server, store):
    try:
        _post(server, "/api/dispatch", {"type": "root", "title": "x"})
        raised = False
    except urllib.error.HTTPError as e:
        raised = e.code == 400
    assert raised
    assert not list((store / "inbox").glob("*.json"))


# ---------------------------------------------------------------- 会议室真模型通道

def test_http_chat_live_ok(server, monkeypatch):
    from gateway import llm_gateway

    class Stub:
        def chat(self, system, user, model=None):
            return "结论：先修心跳。", "normal"

    captured = {}

    def fake_chat(binding=None, system="", user=""):
        captured["binding"] = binding
        captured["user"] = user
        return llm_gateway.make_envelope(kind="llm_chat", ok=True,
                                         data={"text": "结论：先修心跳。", "status": "normal"})

    monkeypatch.setattr(llm_gateway, "chat", fake_chat)
    env = _post(server, "/api/chat",
                {"agent": "A0", "system": "你是田丰", "user": "今日优先级？"})
    assert env["ok"] is True
    assert env["agent"] == "A0"
    assert env["binding"] == "glm/glm-5.3"
    assert captured["binding"] == "glm/glm-5.3"
    assert "今日优先级" in captured["user"]


def test_http_chat_degraded_and_failure_passthrough(server, monkeypatch):
    from gateway import llm_gateway

    def degraded(binding=None, system="", user=""):
        return llm_gateway.make_envelope(kind="llm_chat", ok=True, degraded=True,
                                         data={"text": "本地兜底", "status": "degraded"})

    monkeypatch.setattr(llm_gateway, "chat", degraded)
    env = _post(server, "/api/chat", {"agent": "R2", "system": "s", "user": "u"})
    assert env["ok"] is True and env["degraded"] is True
    assert env["binding"] == "ollama/qwen3.5:9b"

    def dead(binding=None, system="", user=""):
        return llm_gateway.make_envelope(kind="llm_chat", ok=False,
                                         error={"code": "llm_error", "message": "key missing"})

    monkeypatch.setattr(llm_gateway, "chat", dead)
    env2 = _post(server, "/api/chat", {"agent": "AI1", "system": "s", "user": "u"})
    assert env2["ok"] is False and env2["binding"] == "openai/gpt-6.0"


def test_http_chat_bad_agent(server):
    try:
        _post(server, "/api/chat", {"agent": "../etc", "system": "", "user": "x"})
        raised = False
    except urllib.error.HTTPError as e:
        raised = e.code == 400
    assert raised


def test_http_chat_unknown_agent_default_binding(server, monkeypatch):
    from gateway import llm_gateway
    seen = {}

    def fake(binding=None, system="", user=""):
        seen["b"] = binding
        return llm_gateway.make_envelope(kind="llm_chat", ok=True,
                                         data={"text": "t", "status": "normal"})

    monkeypatch.setattr(llm_gateway, "chat", fake)
    env = _post(server, "/api/chat", {"agent": "DEV7", "system": "", "user": "u"})
    assert env["ok"] is True
    assert seen["b"] == "ollama/qwen3.5:9b"


def test_http_chat_glm_quota_fallback_to_local(server, monkeypatch):
    """glm 限额/故障 → 会议通道显式降级本地 9b（degraded + fallback 原因，不静默）。"""
    from gateway import llm_gateway
    calls = []

    def fake(binding=None, system="", user=""):
        calls.append(binding)
        if binding.startswith("glm/"):
            return llm_gateway.make_envelope(kind="llm_chat", ok=False,
                                             error={"code": "llm_error",
                                                    "message": "HTTP 429 限额"})
        return llm_gateway.make_envelope(kind="llm_chat", ok=True,
                                         data={"text": "本地兜底答复",
                                               "status": "normal",
                                               "binding_resolved": binding})

    monkeypatch.setattr(llm_gateway, "chat", fake)
    env = _post(server, "/api/chat", {"agent": "A0", "system": "s", "user": "u"})
    assert env["ok"] is True
    assert env["degraded"] is True
    assert env["fallback_from"] == "glm/glm-5.3"
    assert "429" in env["fallback_reason"]
    assert env["binding"] == "ollama/qwen3.5:9b"
    assert calls == ["glm/glm-5.3", "ollama/qwen3.5:9b"]


def test_http_chat_local_failure_no_fallback(server, monkeypatch):
    """本地档失败不伪造降级（fail-visible 原样透传）。"""
    from gateway import llm_gateway

    def fake(binding=None, system="", user=""):
        return llm_gateway.make_envelope(kind="llm_chat", ok=False,
                                         error={"code": "llm_error", "message": "重试耗尽"})

    monkeypatch.setattr(llm_gateway, "chat", fake)
    env = _post(server, "/api/chat", {"agent": "R2", "system": "s", "user": "u"})
    assert env["ok"] is False
    assert env["error"]["code"] == "llm_error"


def test_http_chat_openai_channel_not_ready(server, monkeypatch):
    """openai 绑定未接 → 明确 channel_not_ready，不打 Ollama 兜底（避免误导 404）。"""
    from gateway import llm_gateway
    calls = []

    def fake(binding=None, system="", user=""):
        calls.append(binding)
        return llm_gateway.make_envelope(kind="llm_chat", ok=True, data={"text": "x"})

    monkeypatch.setattr(llm_gateway, "chat", fake)
    env = _post(server, "/api/chat", {"agent": "AI1", "system": "s", "user": "u"})
    assert env["ok"] is False
    assert env["error"]["code"] == "channel_not_ready"
    assert "Q12" in env["error"]["message"]
    assert calls == []


# ---------------------------------------------------------------- 在途归属映射（S29）

def test_activity_mapping_covers_real_tasks():
    """真实 tasks.json 的 doing 应全部可归属到部门（0 unmapped）。"""
    data = build_snapshot()
    act = data["activity"]
    assert act["rules_version"] == "1.0"
    if act["doing"]:
        assert act["unmapped"] == [], "存在未归属在途单：%s" % act["unmapped"]
        assert sum(len(v) for v in act["by_dept"].values()) == len(act["doing"])


def test_activity_maps_known_prefixes():
    data = build_snapshot()
    act = data["activity"]
    if not act["doing"]:
        pytest.skip("当前无在途单")
    for t in act["doing"]:
        assert t["dept"], "任务 %s 未归属" % t["key"]
    if any(t["key"].startswith("K-INTEL") for t in act["doing"]):
        assert "intel" in act["by_dept"]


def test_activity_mapping_does_not_touch_tasks_json():
    from pathlib import Path as _P
    p = _P(__file__).resolve().parents[2] / ".worktrees" / "tasks.json"
    before = p.read_bytes() if p.exists() else None
    build_snapshot()
    after = p.read_bytes() if p.exists() else None
    assert before == after


def test_map_task_rules():
    from legion_command.snapshot import _map_task
    assert _map_task("legion-1021093009", "盘前 weak 池三线扫描") == ("invest", "D1")
    assert _map_task("x", "每日复盘三问汇总") == ("ops", "A0A")
    assert _map_task("K-INTEL-20261018-RO", "") == ("intel", "")
    assert _map_task("K-ORG-180", "") == ("ops", "")
    assert _map_task("random-task", "无关键词") == ("", "")


# ------------------------------------------------- 会终纪要落盘（S31）

@pytest.fixture()
def meeting_server(store, tmp_path, monkeypatch):
    """纪要端点定向到 tmp，绝不写真实 data/meetings。"""
    from legion_command import serve as srv_mod
    mdir = tmp_path / "warroom"
    monkeypatch.setattr(srv_mod, "MEETINGS_DIR", mdir)
    srv = build_server(port=0)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield "http://127.0.0.1:%d" % srv.server_address[1], mdir
    srv.shutdown()
    srv.server_close()


def test_minutes_saved_with_source_tags(meeting_server):
    base, mdir = meeting_server
    env = _post(base, "/api/meeting/minutes", {
        "participants": ["A0", "D1", "R3"],
        "orders": [{"title": "心跳修复", "state": "已下发"}],
        "model_stats": {"ok": 2, "fail": 1},
        "minutes": [
            {"id": "HOST", "text": "今日盘面怎么看？", "src": "host"},
            {"id": "D1", "text": "选股面：需查证池源后再报。", "src": "live"},
            {"id": "R3", "text": "演示台词内容", "src": "script"},
        ]})
    assert env["ok"] is True and env["count"] == 3
    files = list(mdir.glob("warroom-*.json"))
    assert len(files) == 1
    doc = json.loads(files[0].read_text(encoding="utf-8"))
    assert doc["schema_version"] == "1.0"
    assert doc["participants"] == ["A0", "D1", "R3"]
    assert doc["model_stats"] == {"ok": 2, "fail": 1}
    # 来源标记必须原样保留——纪要的价值就在能分辨真模型/降级/台词
    assert [m["src"] for m in doc["minutes"]] == ["host", "live", "script"]
    assert doc["minutes"][1]["text"].startswith("选股面")


def test_minutes_empty_rejected_no_file(meeting_server):
    base, mdir = meeting_server
    for bad in ({}, {"minutes": []}, {"minutes": "notalist"}):
        try:
            _post(base, "/api/meeting/minutes", bad)
            raised = False
        except urllib.error.HTTPError as e:
            raised = e.code == 400
        assert raised, bad
    assert not list(mdir.glob("*.json")) if mdir.exists() else True


def test_minutes_truncates_oversize(meeting_server):
    base, mdir = meeting_server
    # 230 条 × 2000 字 ≈ 460KB，在 512KB 长度门之内，专门验条数与单条截断
    rows = [{"id": "D1", "text": "x" * 2000, "src": "live"} for _ in range(230)]
    env = _post(base, "/api/meeting/minutes", {"minutes": rows})
    assert env["ok"] is True and env["count"] == 200
    doc = json.loads(next(mdir.glob("warroom-*.json")).read_text(encoding="utf-8"))
    assert len(doc["minutes"]) == 200
    assert len(doc["minutes"][0]["text"]) == 2000   # 单条截断上限


def test_minutes_filename_is_server_generated(meeting_server):
    """不信任客户端路径：传 filename/path 字段一律忽略，落盘只在 MEETINGS_DIR 内。"""
    base, mdir = meeting_server
    env = _post(base, "/api/meeting/minutes", {
        "filename": "../../evil.json", "path": "C:/Windows/evil.json",
        "minutes": [{"id": "A0", "text": "ok", "src": "live"}]})
    assert env["ok"] is True
    assert env["saved"].split("\\")[-1].startswith("warroom-")
    assert not (mdir.parent / "evil.json").exists()
    assert len(list(mdir.glob("warroom-*.json"))) == 1


def test_minutes_bad_length_rejected(meeting_server):
    """超 512KB 请求体直接 400（不当内存炸弹）。"""
    base, mdir = meeting_server
    big = json.dumps({"minutes": [{"id": "A0", "text": "y" * 600000, "src": "live"}]}).encode()
    req = urllib.request.Request(base + "/api/meeting/minutes", data=big,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(req, timeout=10)
        raised = False
    except urllib.error.HTTPError as e:
        raised = e.code == 400
    assert raised
    assert not list(mdir.glob("*.json")) if mdir.exists() else True


# ------------------------------------------------- 会议纪要回看（S32）

def _seed_meeting(base, participants, texts):
    rows = [{"id": "HOST", "text": texts[0], "src": "host"}]
    for aid, t, src in texts[1]:
        rows.append({"id": aid, "text": t, "src": src})
    return _post(base, "/api/meeting/minutes",
                 {"participants": participants, "minutes": rows,
                  "model_stats": {"ok": 1, "fail": 0}})


def test_meeting_list_empty_dir(meeting_server):
    base, mdir = meeting_server
    st, body = _get(base, "/api/meeting/list")
    env = json.loads(body.decode("utf-8"))
    assert st == 200 and env["ok"] is True
    assert env["meetings"] == [] and env["total"] == 0


def test_meeting_list_after_save(meeting_server):
    base, mdir = meeting_server
    _seed_meeting(base, ["A0", "D1"], ["今日盘面怎么看？", [("D1", "需查证池源", "live")]])
    env = json.loads(_get(base, "/api/meeting/list")[1].decode("utf-8"))
    assert env["total"] == 1
    m = env["meetings"][0]
    assert m["id"].startswith("warroom-") and m["broken"] is False
    assert m["count"] == 2
    assert m["participants"] == ["A0", "D1"]
    # 来源分账必须精确：这是"别把台词当模型结论"的唯一依据
    assert m["src_stats"] == {"live": 1, "degraded": 0, "script": 0, "host": 1}


def test_meeting_list_sorted_desc(meeting_server):
    base, mdir = meeting_server
    _seed_meeting(base, ["A0"], ["第一场", [("D1", "a", "live")]])
    time.sleep(1.1)                      # 时间戳秒级精度，保证 id 不同
    _seed_meeting(base, ["R3"], ["第二场", [("R3", "b", "script")]])
    env = json.loads(_get(base, "/api/meeting/list")[1].decode("utf-8"))
    assert env["total"] == 2
    ids = [m["id"] for m in env["meetings"]]
    assert ids == sorted(ids, reverse=True), "清单必须时间倒序"


def test_meeting_list_ignores_broken_file(meeting_server):
    """损坏文件不炸清单，标 broken=True（fail-visible 不静默）。"""
    base, mdir = meeting_server
    mdir.mkdir(parents=True, exist_ok=True)
    (mdir / "warroom-20200101-000000.json").write_text("{not json", encoding="utf-8")
    env = json.loads(_get(base, "/api/meeting/list")[1].decode("utf-8"))
    assert env["ok"] is True and env["total"] == 1
    assert env["meetings"][0]["broken"] is True


def test_meeting_get_roundtrip(meeting_server):
    base, mdir = meeting_server
    _seed_meeting(base, ["A0", "R3"], ["调不调仓？", [("R3", "缺乏回撤数据，无法判断", "live")]])
    mid = json.loads(_get(base, "/api/meeting/list")[1].decode("utf-8"))["meetings"][0]["id"]
    st, body = _get(base, "/api/meeting/" + mid)
    env = json.loads(body.decode("utf-8"))
    assert st == 200 and env["ok"] is True
    doc = env["meeting"]
    assert doc["minutes"][1]["text"] == "缺乏回撤数据，无法判断"
    assert [m["src"] for m in doc["minutes"]] == ["host", "live"]


def test_meeting_get_unknown_is_404(meeting_server):
    base, mdir = meeting_server
    try:
        _get(base, "/api/meeting/warroom-19990101-000000")
        raised = False
    except urllib.error.HTTPError as e:
        raised = e.code == 404
    assert raised, "合法格式但不存在的纪要应 404，不得拿 200 装成功"


def test_meeting_get_bad_id_is_400(meeting_server):
    base, mdir = meeting_server
    for bad in ("/api/meeting/warroom-2026-1-1",
                "/api/meeting/org.json",
                "/api/meeting/..%2f..%2fregistry%2forg.json"):
        try:
            _get(base, bad)
            raised = False
        except urllib.error.HTTPError as e:
            raised = e.code in (400, 404)
        assert raised, bad


def test_meeting_traversal_blocked_two_layers(meeting_server):
    """双保险：路由正则 + relative_to 各自都能拦；直调底层仍拒（证明第二道独立成立）。"""
    from legion_command import serve as srv_mod
    base, mdir = meeting_server
    secret = mdir.parent / "secret.json"
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_text('{"secret": "MUST_NOT_LEAK"}', encoding="utf-8")

    # 路由层：未编码 .. 直接 400
    try:
        st, body = _get(base, "/api/meeting/../../secret")
        leaked = b"MUST_NOT_LEAK" in body
    except urllib.error.HTTPError as e:
        leaked = False
        assert e.code == 400
    assert leaked is False

    # 底层第二道：绕过路由正则直调，仍须拒且不返回内容
    for evil in ("../../secret", "../secret", "..\\..\\secret"):
        res = srv_mod.Handler._meeting_get(evil)
        assert res["ok"] is False, evil
        assert "MUST_NOT_LEAK" not in json.dumps(res, ensure_ascii=False), evil


def test_minutes_page_served(meeting_server):
    base, mdir = meeting_server
    st, body = _get(base, "/static/minutes.html")
    assert st == 200
    assert b"/api/meeting/list" in body


# ------------------------------------------------- 编制预览端点（S33）

def _post_err(base, path, obj):
    """POST 并返回 (status, body_dict) —— 4xx 不抛，便于断言错误码与错误体。"""
    req = urllib.request.Request(base + path, data=json.dumps(obj).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def _org_sha():
    import hashlib
    return hashlib.sha256((_PLATFORM / "registry" / "org.json").read_bytes()).hexdigest()


_ORG_PRE = dict(dept="dev", agent_id="DEV9", cn_name="蒋琬", role_profile="日志工程师",
                duty="负责全链路运行日志治理与异常归因，维护日志规范与轮转策略",
                binding="ollama/qwen3.5:9b", grade="E2",
                ticket="WO-ORG-PREVIEW", signed_by="（预览·待老田签字）")


def test_preview_enroll_ok_and_no_write(server):
    before = _org_sha()
    env = _post(server, "/api/org/preview-enroll", _ORG_PRE)
    assert env["ok"] is True
    pl = env["plan"]
    assert pl["agent"]["agent_id"] == "DEV9"
    assert pl["cn_name"] == "蒋琬"
    assert pl["dept_name"]
    assert "未写入任何字节" in env["note"]
    assert _org_sha() == before, "预览端点绝不能写真实 org.json"


def test_preview_enroll_duplicate_name_rejected(server):
    before = _org_sha()
    st, env = _post_err(server, "/api/org/preview-enroll", dict(_ORG_PRE, cn_name="田丰"))
    assert st == 400 and env["ok"] is False and env["error"] == "enroll_invalid"
    assert "重名" in env["detail"]
    assert _org_sha() == before


def test_preview_enroll_bad_binding_rejected(server):
    st, env = _post_err(server, "/api/org/preview-enroll", dict(_ORG_PRE, binding="gpt-9/x"))
    assert st == 400 and "词表" in env["detail"]


def test_preview_enroll_short_duty_rejected(server):
    st, env = _post_err(server, "/api/org/preview-enroll", dict(_ORG_PRE, duty="干活"))
    assert st == 400 and "duty" in env["detail"]


def test_preview_enroll_unknown_dept_rejected(server):
    st, env = _post_err(server, "/api/org/preview-enroll", dict(_ORG_PRE, dept="nowhere"))
    assert st == 400 and "unknown dept" in env["detail"]


def test_preview_enroll_bad_body(server):
    for bad in ({}, {"dept": "dev"}):
        st, _ = _post_err(server, "/api/org/preview-enroll", bad)
        assert st == 400, bad


# ------------------------------------------------- 绑定查真源 + 规则执行体（S38）

def test_resolve_binding_prefers_org_truth():
    """org.json 是真源：编制造出的绑定必须压过本文件的硬编码表。"""
    from legion_command import serve as srv_mod
    srv_mod._BINDING_CACHE.update(mtime=None, map={})
    assert srv_mod.resolve_binding("BEACON1") == "none", "BEACON1 编制绑定 none"
    assert srv_mod.resolve_binding("KGMAP1") == "ollama/qwen3.5:9b"
    # INTEL0 的绑定会随改档演进（S39 曾由 glm-5.3-flash 改为 9b），不写死具体值，
    # 改为与 org.json 真源动态比对——写死就会在每次改档后集体转红（今日第三次踩）。
    _org = json.loads((_PLATFORM / "registry" / "org.json").read_text(encoding="utf-8"))
    _truth = {p.get("agent_id"): str(p.get("llm_binding") or "")
              for d in _org["departments"] for p in (d.get("agent_profiles") or [])}
    assert srv_mod.resolve_binding("INTEL0") == _truth["INTEL0"]
    # org 无档者回退硬编码表，再回退默认
    assert srv_mod.resolve_binding("AI1") == "openai/gpt-6.0"
    assert srv_mod.resolve_binding("NOPE9") == "ollama/qwen3.5:9b"


def test_chat_rule_only_seat_rejected_without_llm_call(server, monkeypatch):
    """规则执行体必须拒答，且**一次 LLM 都不许调**（否则就是硬绑 9b 的老毛病）。"""
    from gateway import llm_gateway
    from legion_command import serve as srv_mod
    srv_mod._BINDING_CACHE.update(mtime=None, map={})
    calls = []
    monkeypatch.setattr(llm_gateway, "chat",
                        lambda **k: calls.append(k) or llm_gateway.make_envelope(
                            kind="llm_chat", ok=True, data={"text": "不该出现"}))
    env = _post(server, "/api/chat", {"agent": "BEACON1", "system": "s", "user": "u"})
    assert env["ok"] is False
    assert env["error"]["code"] == "no_llm_capability"
    assert "规则执行体" in env["error"]["message"]
    assert env["binding"] == "none"
    assert calls == [], "规则执行体不得触发任何 LLM 调用"


def test_chat_normal_seat_still_works(server, monkeypatch):
    """改动不能误伤：普通席位仍走 LLM。"""
    from gateway import llm_gateway
    from legion_command import serve as srv_mod
    srv_mod._BINDING_CACHE.update(mtime=None, map={})
    monkeypatch.setattr(llm_gateway, "chat",
                        lambda **k: llm_gateway.make_envelope(
                            kind="llm_chat", ok=True,
                            data={"text": "真回复", "status": "normal"}))
    env = _post(server, "/api/chat", {"agent": "KGMAP1", "system": "s", "user": "u"})
    assert env["ok"] is True and env["data"]["text"] == "真回复"
    assert env["binding"] == "ollama/qwen3.5:9b"


def test_binding_cache_refreshes_on_org_change(tmp_path):
    """org.json 改动后缓存须失效——否则补档/改档后会议层仍按旧绑定说话。"""
    from legion_command import serve as srv_mod
    r = tmp_path / "plat"
    (r / "registry").mkdir(parents=True)
    org = {"departments": [{"dept": "x", "agents": ["Z1"],
                            "agent_profiles": [{"agent_id": "Z1", "llm_binding": "ollama/qwen3.5:9b"}]}]}
    p = r / "registry" / "org.json"
    p.write_text(json.dumps(org, ensure_ascii=False), encoding="utf-8")
    srv_mod._BINDING_CACHE.update(mtime=None, map={})
    assert srv_mod.resolve_binding("Z1", platform_dir=r) == "ollama/qwen3.5:9b"
    # 改档为 none（并确保 mtime 变化）
    org["departments"][0]["agent_profiles"][0]["llm_binding"] = "none"
    p.write_text(json.dumps(org, ensure_ascii=False), encoding="utf-8")
    import os
    st = p.stat()
    os.utime(p, (st.st_atime, st.st_mtime + 10))
    assert srv_mod.resolve_binding("Z1", platform_dir=r) == "none", "org 改动后缓存未失效"
