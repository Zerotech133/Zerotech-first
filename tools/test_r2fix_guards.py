# -*- coding: utf-8 -*-
"""K-QUANT-20261026-R2FIX：畸形上游输入守卫回归测试。"""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WT = HERE.parent


def _load(rel, name):
    path = (WT / rel) if rel.startswith("agent_platform") else (HERE / rel)
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


def _mk(tmp_path, tasks_body, status=True):
    (tmp_path / "STATUS.md").write_text("x", encoding="utf-8")
    (tmp_path / "tasks.json").write_text(tasks_body, encoding="utf-8")
    (tmp_path / "E.md").write_text("x", encoding="utf-8")
    return tmp_path / "STATUS.md", tmp_path / "tasks.json", tmp_path / "E.md"


def test_evidence_tasks_not_dict_no_crash(tmp_path):
    E = _load("seat_evidence_packet.py", "r2f_ev")
    st, tk, ex = _mk(tmp_path, "[1,2,3]")
    pkt = E.build(st, tk, ex)
    assert pkt["tickets"]["total"] == 0
    assert pkt["tickets"].get("error") == "tasks_not_dict"
    assert pkt["tickets"]["exists"] is True  # 仍可产出（其它源正常），错误在字段内可见


def test_a0_prompt_malformed_fields_no_crash():
    A = _load("agent_platform/a0_evidence_judge.py", "r2f_a0")
    text = A.build_prompt({"schema": "x", "generated_at": "t",
                           "tickets": "bad", "experience": [1],
                           "sources": "bad", "health": "bad"})
    assert "【证据包】" in text and "【工单】" in text and "【经验】" in text


def test_zhuge_payload_not_dict_marked_false():
    Z = _load("zhuge_brief_snapshot.py", "r2f_zh")
    snap = Z.build({"ok": True, "payload": [1, 2]}, "u")
    assert snap["ok"] is False
    assert snap["error"] == "upstream_payload_not_dict"
    assert snap["suggestions"] == []


def test_glm_probe_non_dict_results(tmp_path):
    G = _load("glm_retry_probe.py", "r2f_glm")
    # ping 返回非 dict → rc=3，不崩
    rc = G.probe(out_dir=tmp_path / "o1", packets_dir=tmp_path,
                 chat_fn=lambda u: "bad")
    assert rc == 3
    # ping ok 但无证据包 → rc=0（available_no_evidence）
    rc2 = G.probe(out_dir=tmp_path / "o2", packets_dir=tmp_path / "empty",
                  chat_fn=lambda u: {"ok": True, "data": {"text": "OK"}})
    assert rc2 == 0
    assert (tmp_path / "o2" / "GLM_AVAILABLE.txt").is_file()

