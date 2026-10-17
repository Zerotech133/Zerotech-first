# -*- coding: utf-8 -*-
"""M4-P5 投研绩效看板 pytest：api_perf mixin（perf board + 四环回放）。

- 模块级 _REPO_ROOT monkeypatch 注入 tmp 仓（零真仓写副作用）；
- 绿地 ledger_exists=false 空投影（观测面不报错）；
- ref 缺失 400 fail-visible；limit 白名单 400；
- perf_chain_view 四环聚合（perf_key 命中→链+明细+分组）。

运行：python -m pytest portal/test_perf_mixin.py -q
"""

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
_REPO = HERE.parent
if str(_REPO / "agent_platform") not in sys.path:
    sys.path.insert(0, str(_REPO / "agent_platform"))

import api_perf  # noqa: E402

from adapters.trace_link import LinkStore  # type: ignore  # noqa: E402
from engine.perf_ledger import (  # type: ignore  # noqa: E402
    perf_key_for, record_perf_rows)


@pytest.fixture()
def tmp_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(api_perf, "_REPO_ROOT", tmp_path)
    return tmp_path


def _feed(root, run_id="run-1", ret=1.25, prof="relaxed"):
    record_perf_rows([{
        "date": "2026-10-16", "symbol": "600519", "run_id": run_id,
        "score": 3.2, "confidence": 0.7, "ret_pct": ret, "horizon": 3,
        "base": 1500.0, "exit": 1518.75, "recommendation": "buy",
        "stance": "坚守", "dk_regime": "rotation", "dk_profile": prof,
        "dk_blocked": False,
    }], root, link_store=LinkStore(root / "agent_platform" / "data"
                                   / "trace" / "trace_links.jsonl"))


def _stub():
    """最小 Handler 桩：只补 mixin 依赖的 _json（沿 capability 判例）。"""

    class _Stub(api_perf.Handler):
        def __init__(self):
            self.payload = None
            self.status = 200

        def _json(self, obj, status=200):
            self.payload = obj
            self.status = status

    return _Stub()


class TestPerfBoard:
    def test_green_field_empty_projection(self, tmp_repo):
        b = api_perf.build_perf_board()
        assert b["ledger_exists"] is False
        assert b["total"] == 0
        assert b["thresholds"]["min_samples"] == 5

    def test_board_projection(self, tmp_repo):
        _feed(tmp_repo)
        b = api_perf.build_perf_board()
        assert b["ledger_exists"] is True
        assert b["total"] == 1
        assert b["profile_stats"]["relaxed"]["n"] == 1
        assert b["recent"][0]["symbol"] == "600519"

    def test_board_without_roster_pool(self, tmp_repo):
        _feed(tmp_repo)
        b = api_perf.build_perf_board()
        assert b["roster_verdicts"] == []  # 裁决池未建=空列表非报错

    def test_endpoint_limit_whitelist(self, tmp_repo):
        h = _stub()
        api_perf.Handler._perf_board(h, {"limit": ["500"]})   # 超上界
        assert h.status == 400
        api_perf.Handler._perf_board(h, {"limit": ["abc"]})   # 非数字
        assert h.status == 400


class TestPerfReplay:
    def test_ref_required(self, tmp_repo):
        h = _stub()
        api_perf.Handler._perf_replay(h, {"ref": [""]})
        assert h.status == 400
        assert h.payload["error"]

    def test_not_found_is_legal_answer(self, tmp_repo):
        _feed(tmp_repo)
        r = api_perf.build_perf_replay("no-such-anchor")
        assert r["verdict"] == "NOT_FOUND"
        assert r["findings"]

    def test_full_chain_via_run_id(self, tmp_repo):
        _feed(tmp_repo, run_id="run-xyz")
        key = perf_key_for("2026-10-16", "600519", "run-xyz")
        r = api_perf.build_perf_replay("run-xyz")
        assert r["verdict"] == "OK"
        assert r["perf_rows"] and r["perf_rows"][0]["perf_key"] == key
        assert r["profile_stats"]["relaxed"]["n"] == 1
        segs = set()
        for m in r["matches"]:
            segs |= set(m["chain"])
        assert {"shadow", "perf"} <= segs
        missing_all = [m["missing_segments"] for m in r["matches"]]
        assert any("paper" in ms and "execution" in ms
                   for ms in missing_all)  # 缺环显式留痕

    def test_full_chain_via_perf_key(self, tmp_repo):
        _feed(tmp_repo, run_id="run-pk")
        key = perf_key_for("2026-10-16", "600519", "run-pk")
        r = api_perf.build_perf_replay(key)
        assert r["verdict"] == "OK"
        assert r["perf_rows"][0]["run_id"] == "run-pk"

    def test_ledger_missing_chain_missing(self, tmp_repo):
        # 链账本未建 → NOT_FOUND 留 finding（不伪造空 OK）
        r = api_perf.build_perf_replay("anything")
        assert r["verdict"] == "NOT_FOUND"
