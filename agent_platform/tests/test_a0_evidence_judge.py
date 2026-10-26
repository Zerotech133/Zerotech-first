# -*- coding: utf-8 -*-
"""a0_evidence_judge 单测（K-QUANT-20261026-A0EVJ）。"""
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MOD_PATH = HERE.parent / "a0_evidence_judge.py"


def _load():
    spec = importlib.util.spec_from_file_location("a0_evidence_judge_t", MOD_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


M = _load()

PACKET = {
    "schema": "seat_evidence_packet/v1",
    "generated_at": "2026-10-26T17:55:00+0800",
    "sources": [{"path": "C:/x/STATUS.md", "sha256": "abc123def456", "exists": True},
                {"path": "C:/x/tasks.json", "sha256": "fff000", "exists": True}],
    "tickets": {"total": 512,
                "counts": {"doing": 23, "review": 2, "done": 429},
                "doing_review": [
                    {"tid": "WO-LEGION-X", "status": "doing", "desc": "示例单", "owner": "dev"},
                ]},
    "experience": {"total": 13, "entries": [
        {"id": "EXP-SUPERVISION-011", "cue": "模型空白输入", "lesson": "须带证据",
         "evidence": "Z4 卡 11", "confidence": "0.75"}]},
    "health": {"tail": ["- 健康卡 20绿/11黄/2红", "- 末行"]},
}


class FakeLLM:
    def __init__(self, text="判读：结论/证据/风险/下一步", fail=False):
        self.text = text
        self.fail = fail
        self.calls = []

    def chat(self, system, user):
        self.calls.append((len(system), len(user)))
        if self.fail:
            raise RuntimeError("模型 429")
        return self.text, "ok"


def test_build_prompt_contains_evidence():
    p = M.build_prompt(PACKET)
    assert "WO-LEGION-X" in p and "EXP-SUPERVISION-011" in p
    assert "20绿" in p and "sha256=abc123def456"[:20] in p
    assert len(p) <= 12000


def test_dry_run_no_call(tmp_path):
    res = M.judge(PACKET, live=False, out_dir=tmp_path)
    assert res["ok"] is True and res["dry_run"] is True
    assert res["text"] == ""
    assert "prompt_head" in res
    assert not list(tmp_path.iterdir())


def test_live_with_fake_llm_writes_files(tmp_path):
    fake = FakeLLM()
    res = M.judge(PACKET, model="glm/glm-5.3", live=True, out_dir=tmp_path, llm=fake)
    assert res["ok"] is True and res["text"] == fake.text
    assert fake.calls and fake.calls[0][0] > 0 and fake.calls[0][1] > 0
    jp = Path(res["out_json"])
    mp = Path(res["out_md"])
    assert jp.is_file() and mp.is_file()
    data = json.loads(jp.read_text(encoding="utf-8"))
    assert data["ok"] is True and data["model"] == "glm/glm-5.3"
    assert "待老田签" in mp.read_text(encoding="utf-8")


def test_live_failure_not_fake_success(tmp_path):
    fake = FakeLLM(fail=True)
    res = M.judge(PACKET, live=True, out_dir=tmp_path, llm=fake)
    assert res["ok"] is False
    assert "429" in (res["error"] or "")
    assert Path(res["out_md"]).is_file()

