# -*- coding: utf-8 -*-
"""seat_evidence_packet 单测（K-QUANT-20261026-TFEVID）。"""
import ast
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
MOD_PATH = HERE / "seat_evidence_packet.py"


def _load():
    spec = importlib.util.spec_from_file_location("seat_evidence_packet_t", MOD_PATH)
    mod = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


M = _load()


def _make(tmp_path: Path):
    status = tmp_path / "STATUS.md"
    status.write_text("# S\n健康卡 21 绿/8 黄/1 红\n- 末行：未做 commit\n", encoding="utf-8")
    tasks = tmp_path / "tasks.json"
    tasks.write_text(json.dumps({
        "T-A": {"desc": "a", "status": "doing", "owner": "A", "created": "2026-10-26 10:00:00"},
        "T-B": {"desc": "b", "status": "review", "owner": "B", "created": "2026-10-26 11:00:00"},
        "T-C": {"desc": "c", "status": "done", "owner": "C", "created": "2026-10-25 09:00:00"},
        "T-D": {"desc": "d", "status": "aborted", "owner": "D"},
    }, ensure_ascii=False), encoding="utf-8")
    exp = tmp_path / "EXPERIENCE.md"
    exp.write_text(
        "# EXPERIENCE\n\n## EXP-SUPERVISION-001\n- **cue**: c1\n- **lesson**: l1\n"
        "- **evidence**: Z4-1\n- **confidence**: 0.8\n\n"
        "## EXP-SUPERVISION-002\n- **cue**: c2\n- **lesson**: l2\n"
        "- **evidence**: Z4-2\n- **confidence**: 0.9\n", encoding="utf-8")
    return status, tasks, exp


def test_build_fields(tmp_path):
    status, tasks, exp = _make(tmp_path)
    packet = M.build(status, tasks, exp)
    assert packet["schema"] == "seat_evidence_packet/v1"
    assert packet["tickets"]["counts"]["doing"] == 1
    assert packet["tickets"]["counts"]["review"] == 1
    assert packet["tickets"]["counts"]["done"] == 1
    assert packet["tickets"]["counts"]["aborted"] == 1
    assert [r["tid"] for r in packet["tickets"]["doing_review"]] == ["T-B", "T-A"]
    ids = [e["id"] for e in packet["experience"]["entries"]]
    assert ids == ["EXP-SUPERVISION-001", "EXP-SUPERVISION-002"]
    assert packet["experience"]["entries"][0]["confidence"] == "0.8"
    assert all(s["exists"] for s in packet["sources"])
    assert packet["health"]["tail"][-1] == "- 末行：未做 commit"


def test_render_md(tmp_path):
    status, tasks, exp = _make(tmp_path)
    md = M.render_md(M.build(status, tasks, exp))
    assert "T-A" in md and "T-B" in md
    assert "EXP-SUPERVISION-001" in md
    assert "未做 commit" in md
    assert "判读结论以 a0_agent" in md


def test_missing_sources(tmp_path):
    packet = M.build(tmp_path / "no1.md", tmp_path / "no2.json", tmp_path / "no3.md")
    assert all(not s["exists"] for s in packet["sources"])
    assert packet["tickets"]["doing_review"] == []
    assert packet["experience"]["entries"] == []


def test_readonly_sources(tmp_path):
    status, tasks, exp = _make(tmp_path)
    before = {p: (p.stat().st_size, p.read_bytes()) for p in (status, tasks, exp)}
    M.build(status, tasks, exp)
    for p in (status, tasks, exp):
        assert (p.stat().st_size, p.read_bytes()) == before[p]


def test_no_network_imports():
    tree = ast.parse(MOD_PATH.read_text(encoding="utf-8"))
    banned = {"socket", "urllib", "requests", "http.client", "httpx", "aiohttp", "feishu"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                assert a.name.split(".")[0] not in {b.split(".")[0] for b in banned}
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[0] not in {b.split(".")[0] for b in banned}
