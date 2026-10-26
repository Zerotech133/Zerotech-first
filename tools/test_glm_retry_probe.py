# -*- coding: utf-8 -*-
"""glm_retry_probe 两阶段单测（K-QUANT-20261026-GLMPROBE2）。

验证：默认只发中性 ping（不发送证据）；ping 失败 rc=3 且无证据外发；
--with-evidence 才读证据包并写判读；无证据包 rc=2；已 ready 跳过。
"""
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location("glm_retry_probe_t", HERE / "glm_retry_probe.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _packets(tmp_path):
    d = tmp_path / "packets"
    d.mkdir(exist_ok=True)
    (d / "evidence_packet_test.md").write_text("# pack\n工单 3\n内部数据示例\n", encoding="utf-8")
    return d


def test_ping_failure_rc3_sends_no_evidence(tmp_path):
    M = _load()
    out = tmp_path / "out"
    calls = []

    def fake(user):
        calls.append(user)
        return {"ok": False, "error": {"message": "HTTP Error 429"}}

    rc = M.probe(out_dir=out, packets_dir=_packets(tmp_path), chat_fn=fake)
    assert rc == 3
    assert len(calls) == 1 and "工单 3" not in calls[0] and "内部数据示例" not in calls[0]
    logs = [json.loads(x) for x in (out / "retry_log.jsonl").read_text(
        encoding="utf-8").splitlines()]
    assert logs[-1]["mode"] == "ping" and "429" in logs[-1]["error"]
    assert not (out / "JUDGMENT_READY.txt").exists()


def test_ping_ok_writes_available_without_evidence(tmp_path):
    M = _load()
    out = tmp_path / "out"
    calls = []

    def fake(user):
        calls.append(user)
        return {"ok": True, "data": {"text": "OK", "status": "ok"}}

    rc = M.probe(out_dir=out, packets_dir=_packets(tmp_path), chat_fn=fake)
    assert rc == 0
    assert (out / "GLM_AVAILABLE.txt").is_file()
    assert not list(out.glob("judgment_*.md")), "默认模式不得发送/写出证据判读"
    assert len(calls) == 1 and "工单 3" not in calls[0]


def test_with_evidence_writes_judgment(tmp_path):
    M = _load()
    out = tmp_path / "out"
    calls = []

    def fake(user):
        calls.append(user)
        if "只回复 OK" in user:
            return {"ok": True, "data": {"text": "OK"}}
        return {"ok": True, "data": {"text": "判读草稿", "status": "ok"}}

    rc = M.probe(out_dir=out, packets_dir=_packets(tmp_path), chat_fn=fake,
                 with_evidence=True)
    assert rc == 0
    assert (out / "JUDGMENT_READY.txt").is_file()
    jd = list(out.glob("judgment_*.md"))
    assert jd and "判读草稿" in jd[0].read_text(encoding="utf-8")
    assert len(calls) == 2 and "内部数据示例" in calls[1]


def test_with_evidence_no_packet_rc2(tmp_path):
    M = _load()
    empty = tmp_path / "empty"
    empty.mkdir()
    out = tmp_path / "out"
    rc = M.probe(out_dir=out, packets_dir=empty,
                 chat_fn=lambda u: {"ok": True, "data": {"text": "OK"}},
                 with_evidence=True)
    assert rc == 2


def test_ready_skips(tmp_path):
    M = _load()
    out = tmp_path / "out"
    out.mkdir()
    (out / "JUDGMENT_READY.txt").write_text("x", encoding="utf-8")
    called = []
    rc = M.probe(out_dir=out, packets_dir=_packets(tmp_path),
                 chat_fn=lambda u: called.append(u) or {"ok": True})
    assert rc == 0 and called == []


def test_agent_platform_on_sys_path_for_gateway_import():
    """计划任务/S4U 下 cwd 不是仓库；模块导入时应已把 agent_platform 加进 sys.path。"""
    import importlib.util
    M = _load()
    assert (M.REPO / "agent_platform" / "gateway").is_dir()
    assert str(M.REPO / "agent_platform") in sys.path
    assert importlib.util.find_spec("gateway") is not None

def test_approved_marker_enables_auto_evidence(tmp_path):
    """外发追认后：EVIDENCE_APPROVED.txt 存在时，默认（无 --with-evidence）也会发一次证据判读。"""
    M = _load()
    out = tmp_path / "out"
    out.mkdir()
    (out / "EVIDENCE_APPROVED.txt").write_text("approved by 老田 2026-10-26", encoding="utf-8")
    calls = []

    def fake(user):
        calls.append(user)
        if "只回复 OK" in user:
            return {"ok": True, "data": {"text": "OK"}}
        return {"ok": True, "data": {"text": "判读草稿"}}

    rc = M.probe(out_dir=out, packets_dir=_packets(tmp_path), chat_fn=fake)
    assert rc == 0
    assert (out / "JUDGMENT_READY.txt").is_file()
    assert len(calls) == 2 and "内部数据示例" in calls[1]
