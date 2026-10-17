# -*- coding: utf-8 -*-
"""portal mixin pytest 收编（M3-P5）：api_capability + api_unified 读侧。

此前 portal 层测试全是脚本式直跑自检（conftest collect_ignore 判例），
规范 pytest 覆盖是盲区——本文件首次以 pytest 语义覆盖只读 mixin：

- 模块级 _REPO_ROOT monkeypatch 注入 tmp 仓（绝不写真实 agent_platform/data）；
- mode=ro 投影端点不得因 GET 建库（tmp 仓只放显式构造的库）；
- 参数白名单非法 → 400 fail-visible（不吞成默认值）；
- 产线覆盖双口径（target_type 精确 ∪ target_id 'domain:ref' 前缀兜底）。

运行：python -m pytest portal/test_capability_mixin.py -q
"""

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
_REPO = HERE.parent
if str(_REPO / "agent_platform") not in sys.path:
    sys.path.insert(0, str(_REPO / "agent_platform"))

import api_capability  # noqa: E402
import api_unified  # noqa: E402

from contracts import Evidence  # type: ignore  # noqa: E402
from contracts.objects import Approval  # type: ignore  # noqa: E402
from engine.artifacts import Artifact, ArtifactStore  # type: ignore  # noqa: E402
from engine.approvals import ApprovalStore  # type: ignore  # noqa: E402
from engine.evidence import EvidenceStore  # type: ignore  # noqa: E402


# ----------------------------------------------------------------------
# 夹具与桩
# ----------------------------------------------------------------------
def _stub(mixin_mod):
    """最小 Handler 桩：只补 mixin 依赖的 _json（多继承主 Handler 兜底）。"""

    class _Stub(mixin_mod.Handler):
        def __init__(self):
            self.payload = None
            self.status = 200

        def _json(self, obj, status=200):
            self.payload = obj
            self.status = status

    return _Stub()


@pytest.fixture()
def tmp_repo(tmp_path, monkeypatch):
    """tmp 仓基准：patch 两个 mixin 的模块级 _REPO_ROOT。"""
    monkeypatch.setattr(api_capability, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(api_unified, "_REPO_ROOT", tmp_path)
    return tmp_path


def _mk_art(store, aid, chash, source="gzh.drafts", ref="d1"):
    return store.record(Artifact(
        artifact_id=aid, kind="md", path="docs/%s.md" % aid,
        content_hash=chash, fact_hash=chash, source_system=source,
        source_ref=ref, produced_by="laotian"))


def _mk_ap(store, key, target_type, target_id, chash=""):
    return store.record(Approval(
        target_type=target_type, target_id=target_id,
        from_status="draft", to_status="reviewed", decision="approved",
        decided_by="t", idempotency_key=key, content_hash=chash))


# ----------------------------------------------------------------------
# api_capability.build_capability_board
# ----------------------------------------------------------------------
def test_board_empty_when_no_ledgers(tmp_repo):
    board = api_capability.build_capability_board()
    assert board["read_only"] is True
    assert board["artifacts"]["ledger_exists"] is False
    assert board["approvals"]["ledger_exists"] is False
    assert board["artifacts"]["total"] == 0
    assert board["artifacts"]["supersede_chains"] == []
    # 五条产线全显式给行（缺位计 0，不静默漏行）
    assert [l["prefix"] for l in board["production_lines"]] == \
        ["gzh", "pitch", "dev", "skill", "intel"]
    assert all(not l["registered"] for l in board["production_lines"])


def test_board_artifacts_projection_and_chain(tmp_repo):
    db = tmp_repo / "agent_platform" / "data" / "artifacts" / "artifacts.db"
    store = ArtifactStore(db)
    try:
        _, replayed = _mk_art(store, "a1", "H1")
        assert replayed is False
        new, _ = store.supersede("a1", Artifact(
            artifact_id="a2", kind="md", path="docs/a2.md",
            content_hash="H2", fact_hash="H2"))
        assert new.revision == "r2"
    finally:
        store.close()
    board = api_capability.build_capability_board()
    art = board["artifacts"]
    assert art["ledger_exists"] is True
    assert art["total"] == 2
    assert art["superseded_count"] == 1
    assert art["by_source"] == {"gzh.drafts": 2}
    chains = art["supersede_chains"]
    assert len(chains) == 1 and len(chains[0]) == 2
    assert chains[0] == ["a1", "a2"]  # 旧→新
    # 产线覆盖：source_system 前缀 'gzh.drafts' → 'gzh' 行计数
    gzh = next(l for l in board["production_lines"]
               if l["prefix"] == "gzh")
    assert gzh["artifact_count"] == 2 and gzh["registered"] is True


def test_board_approval_binding_rate(tmp_repo):
    db = tmp_repo / "agent_platform" / "data" / "approvals" / "approvals.db"
    store = ApprovalStore(db)
    try:
        _mk_ap(store, "k1", "workitem", "gzh:w1", chash="H1")
        _mk_ap(store, "k2", "workitem", "gzh:w2", chash="H2")
        _mk_ap(store, "k3", "workitem", "gzh:w3")  # legacy 空绑定
    finally:
        store.close()
    board = api_capability.build_capability_board()
    ap = board["approvals"]
    assert ap["total"] == 3
    assert ap["content_hash_bound"] == 2
    assert ap["legacy_unbound"] == 1
    assert ap["content_hash_binding_rate"] == pytest.approx(0.6667)


def test_board_production_lines_prefix_fallback(tmp_repo):
    """workitem 型凭据产线归属走 target_id 'domain:ref' 前缀兜底。"""
    db = tmp_repo / "agent_platform" / "data" / "approvals" / "approvals.db"
    store = ApprovalStore(db)
    try:
        _mk_ap(store, "k1", "workitem", "gzh:w1", chash="H1")
        _mk_ap(store, "k2", "skill", "skill:fact-check", chash="H2")
    finally:
        store.close()
    board = api_capability.build_capability_board()
    lines = {l["prefix"]: l for l in board["production_lines"]}
    assert lines["gzh"]["approval_count"] == 1
    assert lines["skill"]["approval_count"] == 1
    assert lines["pitch"]["approval_count"] == 0


def test_handler_capability_board_stub(tmp_repo):
    h = _stub(api_capability)
    h._capability_board()
    assert h.status == 200
    for key in ("read_only", "artifacts", "approvals",
                "production_lines", "note", "generated_at"):
        assert key in h.payload


def test_board_ro_no_write_side_effect(tmp_repo):
    """空仓 GET 看板不得建库（mode=ro 判例：观测面零写副作用）。"""
    api_capability.build_capability_board()
    assert not (tmp_repo / "agent_platform" / "data").exists()


# ----------------------------------------------------------------------
# api_unified 读侧四端点（M2 收编：此前零 pytest 覆盖）
# ----------------------------------------------------------------------
def test_unified_evidence_with_data(tmp_repo):
    db = tmp_repo / "agent_platform" / "data" / "evidence" / "evidence.db"
    store = EvidenceStore(db)
    try:
        store.record(Evidence(source="t", source_ref="r1",
                              statement="s1",
                              payload={"domain": "gzh", "kind": "obs"}))
    finally:
        store.close()
    h = _stub(api_unified)
    h._unified_evidence("")
    assert h.status == 200
    assert h.payload["ledger_exists"] is True
    assert h.payload["count"] >= 1
    assert h.payload["items"][0]["payload"]["domain"] == "gzh"


def test_unified_evidence_missing_db(tmp_repo):
    h = _stub(api_unified)
    h._unified_evidence("")
    assert h.payload["ledger_exists"] is False
    assert "尚未建立" in h.payload["note"]


def test_unified_evidence_bad_limit_400(tmp_repo):
    h = _stub(api_unified)
    h._unified_evidence("limit=abc")
    assert h.status == 400
    assert h.payload["error"] == "invalid_query"
    h2 = _stub(api_unified)
    h2._unified_evidence("limit=99999")
    assert h2.status == 400  # 超界同样 400（白名单上限 500）


def test_qs_int_whitelist_bounds():
    f = api_unified.Handler._qs_int
    assert f({}, "limit", 50, 1, 500) == 50
    assert f({"limit": ["1"]}, "limit", 50, 1, 500) == 1
    assert f({"limit": ["500"]}, "limit", 50, 1, 500) == 500
    with pytest.raises(ValueError):
        f({"limit": ["0"]}, "limit", 50, 1, 500)
    with pytest.raises(ValueError):
        f({"limit": ["501"]}, "limit", 50, 1, 500)
    with pytest.raises(ValueError):
        f({"limit": ["x"]}, "limit", 50, 1, 500)


def test_unified_skills_structure():
    """真实仓只读投影：断言结构（不依赖具体目录内容）。"""
    h = _stub(api_unified)
    h._unified_skills()
    assert h.status == 200
    p = h.payload
    assert isinstance(p.get("skill_total"), int)
    assert isinstance(p.get("skills"), list)
    assert "audit" in p and p.get("read_only") is True
