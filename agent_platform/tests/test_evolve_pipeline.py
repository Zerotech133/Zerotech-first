# -*- coding: utf-8 -*-
"""WO-LEGION-S27 进化层端到端流水线 + 指挥台进化视图测试。

验收：探→试→裁→记 一条链跑通；未签字停在签字门且零入库；
/api/evolution 只读视图不泄经验正文、不改任何文件。
"""

from __future__ import annotations

import json
import sys
import threading
import urllib.request
from pathlib import Path

import pytest

_PLATFORM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PLATFORM))
sys.path.insert(0, str(_PLATFORM / "engine"))

from engine import experience_store as S  # noqa: E402
from engine import evolve_pipeline as P  # noqa: E402
from legion_command.serve import build_server  # noqa: E402


def _run(tmp, signed="", **kw):
    base = dict(hypothesis="判据改样本外口径是否更优",
                candidate_diff={"criteria/rg.md": "样本外条款"},
                adversary_diff={"criteria/rg.md": "维持原判据"},
                baseline_metrics={"misblock_lower_better": 0.18, "oos_return": 0.004},
                candidate_metrics={"misblock_lower_better": 0.11, "oos_return": 0.009},
                adversary_metrics={"misblock_lower_better": 0.17, "oos_return": 0.005},
                signed_by=signed,
                reason="误拦率 -7pp",
                experience_root=tmp)
    base.update(kw)
    return P.run(**base)


# ---------------------------------------------------------------- 链路

def test_unsigned_stops_at_signature_gate(tmp_path):
    res = _run(tmp_path)
    assert res.decision_required is True
    assert res.entry is None
    assert "签字门" in res.note
    assert S.search("误拦率", root=tmp_path) == []      # 零入库


def test_signed_writes_entry_and_searchable(tmp_path):
    res = _run(tmp_path, signed="laotian")
    assert res.decision_required is False
    assert res.entry is not None and res.entry.domain == "stock_screen"
    hits = S.search("误拦率", root=tmp_path)
    assert hits and hits[0]["entry_id"] == res.entry.entry_id


def test_pipeline_marks_winner_and_reveals_after_sign(tmp_path):
    res = _run(tmp_path, signed="laotian")
    assert res.review.revealed is True
    reveal = res.card.verdict["review"]["reveal_map"]
    assert reveal[res.review.winner] == "candidate"


def test_unsigned_keeps_review_blind(tmp_path):
    res = _run(tmp_path)
    assert res.review.revealed is False
    assert res.card.verdict["review"]["revealed"] is False


def test_riskguard_domain_rejected_at_write(tmp_path):
    with pytest.raises(S.StoreError):
        _run(tmp_path, signed="laotian", domain="riskguard")


def test_adopt_requires_evidence(tmp_path):
    """候选更差时签字也不得采纳（应写成 rejected，仍入库但 lesson 为否决）。"""
    res = _run(tmp_path, signed="laotian",
               baseline_metrics={"x": 2}, candidate_metrics={"x": 1})
    assert res.card.status == "rejected"
    assert res.entry is not None
    assert res.entry.lesson.startswith("否决")


def test_leak_diff_blocked_before_card(tmp_path):
    with pytest.raises(Exception) as ei:
        _run(tmp_path, signed="laotian", candidate_diff={"forward_return": "未来列"})
    assert "lookahead" in str(ei.value)


def test_report_contains_all_sections(tmp_path):
    res = _run(tmp_path)
    md = P.report(res)
    for token in ("假设", "三臂 diff", "对比表", "匿名横评", "签字门"):
        assert token in md


# ---------------------------------------------------------------- 指挥台只读端点

@pytest.fixture()
def server():
    srv = build_server(port=0)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield "http://127.0.0.1:%d" % srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def test_api_evolution_shape(server):
    with urllib.request.urlopen(server + "/api/evolution", timeout=10) as r:
        data = json.loads(r.read().decode("utf-8"))
    assert data["ok"] is True
    assert set(data["experience_domains"]) == {
        "stock_screen", "execution", "tooling", "riskguard", "supervision"}
    assert data["experience_domains"]["riskguard"]["readonly"] is True
    assert isinstance(data["experiments"], list)


def test_api_evolution_readonly_no_body_leak(server):
    with urllib.request.urlopen(server + "/api/evolution", timeout=10) as r:
        raw = r.read().decode("utf-8")
    data = json.loads(raw)
    assert "lesson" not in json.dumps(data)   # 不暴露经验正文
    assert "cue" not in json.dumps(data)
