# -*- coding: utf-8 -*-
"""glm_retry_probe 单测（K-QUANT-20261026-GLMPROBE）。"""
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
    (d / "evidence_packet_test.md").write_text("# pack\n工单 3\n", encoding="utf-8")
    return d


def test_success_writes_judgment_and_marker(tmp_path):
    M = _load()
    out = tmp_path / "out"
    env = {"ok": True, "data": {"text": "判读草稿", "status": "ok"}}
    rc = M.probe(out_dir=out, packets_dir=_packets(tmp_path),
                 chat_fn=lambda user: env)
    assert rc == 0
    assert (out / "JUDGMENT_READY.txt").is_file()
    judgments = list(out.glob("judgment_*.md"))
    assert judgments and "判读草稿" in judgments[0].read_text(encoding="utf-8")
    # 已 ready：跳过且不再调用
    called = []
    rc2 = M.probe(out_dir=out, packets_dir=_packets(tmp_path),
                  chat_fn=lambda user: called.append(1) or env)
    assert rc2 == 0 and called == []


def test_still_unavailable_rc3_and_log(tmp_path):
    M = _load()
    out = tmp_path / "out"
    env = {"ok": False, "error": {"message": "HTTP Error 429"}}
    rc = M.probe(out_dir=out, packets_dir=_packets(tmp_path),
                 chat_fn=lambda user: env)
    assert rc == 3
    assert not (out / "JUDGMENT_READY.txt").exists()
    logs = [json.loads(x) for x in (out / "retry_log.jsonl").read_text(
        encoding="utf-8").splitlines()]
    assert logs and "429" in logs[-1]["error"]


def test_no_packet_rc2(tmp_path):
    M = _load()
    empty = tmp_path / "empty"
    empty.mkdir()
    rc = M.probe(out_dir=tmp_path / "out", packets_dir=empty, chat_fn=lambda u: {})
    assert rc == 2

