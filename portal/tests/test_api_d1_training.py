# -*- coding: utf-8 -*-
"""D1 training episodes 只读 API 测试（IFS v1 F5；WO-IFS-TRAINAPI-20261104）。

全部离线：临时 episodes.jsonl（``D1_TRAINING_EPISODES_PATH``），不写真实数据。
覆盖：空/缺失诚实 data_unavailable、坏行 fail-visible、列表/limit、详情/404/400、
只读（文件哈希不变）、只读标记、route baseline 同批登记。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for _p in (ROOT, ROOT / "portal"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import api_d1_training as T  # noqa: E402


class FakeHandler:
    def __init__(self, path):
        self.path = path
        self.status = None
        self.payload = None

    def _json(self, obj, code=200):
        self.status = code
        self.payload = obj


def _get(path, qs=""):
    handler = FakeHandler(path)
    T.handle_get(handler, qs)
    return handler


def _episode(episode_id="ep-20261104-ABCDEF12", **kw):
    row = {
        "schema": "training_episode/v1",
        "schema_version": 1,
        "training_episode_id": episode_id,
        "combo_id": "paper-main",
        "data_snapshot": {"as_of": "2026-11-04", "source": "unit-test"},
        "ids": {
            "decision_ids": [],
            "handoff_ids": [],
            "watch_plan_ids": [],
            "confirmation_ids": [],
            "trade_ids": [],
            "outcome_ids": [],
        },
        "paper_trades": [],
        "outcomes": [],
        "errors": [],
        "corrections": [],
        "metrics": {
            "trades": 0,
            "outcomes": 0,
            "complete_outcomes": 0,
            "outcome_coverage": None,
            "directional_n": 0,
            "hit_rate": None,
            "avg_return": None,
            "brier": None,
            "win_rate": None,
        },
        "paper_only": True,
        "trading_permitted": False,
        "not_a_trade": True,
        "read_only": True,
        "auto_training": False,
    }
    row.update(kw)
    return row


def _write(path, rows):
    lines = []
    for row in rows:
        lines.append(row if isinstance(row, str) else json.dumps(row, ensure_ascii=False))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


@pytest.fixture
def episodes_file(tmp_path, monkeypatch):
    path = tmp_path / "episodes.jsonl"
    monkeypatch.setenv(T.EPISODES_ENV, str(path))
    return path


# ── 1. 缺失 / 空数据：诚实 data_unavailable，不造数 ──────────────────
class TestEmptyData:
    def test_missing_file_data_unavailable(self, episodes_file):
        assert not episodes_file.exists()
        handler = _get("/api/d1/training/episodes")
        assert handler.status == 200
        payload = handler.payload
        assert payload["ok"] is True
        assert payload["data_unavailable"] is True
        assert payload["count"] == 0 and payload["total"] == 0
        assert payload["episodes"] == []
        assert payload["invalid_lines"] == 0
        assert "不造数" in payload["note"]
        assert not episodes_file.exists()  # 不因查询创建数据文件

    def test_empty_file_is_not_unavailable(self, episodes_file):
        episodes_file.write_text("", encoding="utf-8")
        handler = _get("/api/d1/training/episodes")
        assert handler.status == 200
        payload = handler.payload
        assert payload["ok"] is True
        assert payload["data_unavailable"] is False
        assert payload["count"] == 0 and payload["total"] == 0

    def test_detail_on_missing_file(self, episodes_file):
        handler = _get("/api/d1/training/episodes/ep-20261104-ABCDEF12")
        assert handler.status == 200
        payload = handler.payload
        assert payload["ok"] is True
        assert payload["data_unavailable"] is True
        assert payload["episode"] is None and payload["count"] == 0


# ── 2. 列表 / limit / 只读标记 ───────────────────────────────────────
class TestList:
    def test_list_valid_episodes(self, episodes_file):
        _write(episodes_file, [_episode(), _episode("ep-20261104-12345678")])
        before = _sha(episodes_file)
        handler = _get("/api/d1/training/episodes")
        payload = handler.payload
        assert handler.status == 200 and payload["ok"] is True
        assert payload["data_unavailable"] is False
        assert payload["count"] == 2 and payload["total"] == 2
        assert [e["training_episode_id"] for e in payload["episodes"]] == [
            "ep-20261104-ABCDEF12", "ep-20261104-12345678"]
        assert payload["invalid_lines"] == 0
        assert _sha(episodes_file) == before  # 只读

    def test_limit(self, episodes_file):
        _write(episodes_file, [_episode(), _episode("ep-20261104-12345678")])
        payload = _get("/api/d1/training/episodes", "limit=1").payload
        assert payload["count"] == 1 and payload["total"] == 2
        assert len(payload["episodes"]) == 1
        assert _get("/api/d1/training/episodes", "limit=200").payload["count"] == 2

    @pytest.mark.parametrize("qs,err", [("limit=0", "bad_limit"), ("limit=x", "bad_limit"),
                                        ("limit=-3", "bad_limit")])
    def test_bad_limit_400(self, episodes_file, qs, err):
        handler = _get("/api/d1/training/episodes", qs)
        assert handler.status == 400
        assert handler.payload["ok"] is False and handler.payload["error"] == err

    def test_readonly_flags_on_every_response(self, episodes_file):
        _write(episodes_file, [_episode()])
        for payload in (
            _get("/api/d1/training/episodes").payload,
            _get("/api/d1/training/episodes/ep-20261104-ABCDEF12").payload,
        ):
            assert payload["readonly"] is True
            assert payload["paper_only"] is True
            assert payload["not_a_trade"] is True
            assert payload["auto_training"] is False
            assert payload["trading_permitted"] is False
            assert payload["api_schema"] == T.API_SCHEMA
            assert payload["schema_version"] == T.SCHEMA


# ── 3. 坏行 fail-visible 计数（不中断、不静默）──────────────────────
class TestBadLines:
    def test_bad_lines_counted_and_skipped(self, episodes_file):
        _write(episodes_file, [
            _episode(),
            "{bad json",
            [1, 2],  # 非 object
            {"schema": "other/v1", "training_episode_id": "ep-20261104-AAAAAAAA"},
            {"schema": "training_episode/v1", "training_episode_id": "not-an-episode"},
            "",  # 空行忽略
            json.dumps(_episode("ep-20261104-99999999")),
        ])
        payload = _get("/api/d1/training/episodes").payload
        assert payload["ok"] is True
        assert payload["data_unavailable"] is False
        assert payload["count"] == 2 and payload["total"] == 2
        assert payload["invalid_lines"] == 4
        codes = [e["code"] for e in payload["errors"]]
        assert codes == ["bad_json", "not_object", "bad_schema", "bad_episode_id"]
        assert payload["errors"][0]["line"] == 2
        assert "坏数据" in payload["note"]

    def test_all_bad_lines_still_honest(self, episodes_file):
        _write(episodes_file, ["{bad"])
        payload = _get("/api/d1/training/episodes").payload
        assert payload["ok"] is True
        assert payload["count"] == 0 and payload["total"] == 0
        assert payload["invalid_lines"] == 1
        assert payload["data_unavailable"] is False


# ── 4. 详情路由：命中 / 404 / 400 ────────────────────────────────────
class TestDetail:
    def test_detail_found(self, episodes_file):
        _write(episodes_file, [_episode()])
        handler = _get("/api/d1/training/episodes/ep-20261104-ABCDEF12")
        assert handler.status == 200
        payload = handler.payload
        assert payload["ok"] is True and payload["data_unavailable"] is False
        assert payload["count"] == 1
        assert payload["episode"]["training_episode_id"] == "ep-20261104-ABCDEF12"
        assert payload["episode"]["paper_only"] is True

    def test_detail_unknown_404(self, episodes_file):
        _write(episodes_file, [_episode()])
        handler = _get("/api/d1/training/episodes/ep-20261104-00000000")
        assert handler.status == 404
        payload = handler.payload
        assert payload["ok"] is False and payload["error"] == "episode_not_found"
        assert payload["data_unavailable"] is False

    @pytest.mark.parametrize("episode_id", ["bad", "ep-2026-ABCDEF12", "ep-20261104-xyzxyzxy"])
    def test_detail_bad_id_400(self, episodes_file, episode_id):
        handler = _get("/api/d1/training/episodes/" + episode_id)
        assert handler.status == 400
        assert handler.payload["error"] == "bad_episode_id"

    def test_detail_no_trailing_slash(self, episodes_file):
        _write(episodes_file, [_episode()])
        handler = _get("/api/d1/training/episodes/ep-20261104-ABCDEF12/")
        assert handler.status == 200
        assert handler.payload["episode"]["training_episode_id"] == "ep-20261104-ABCDEF12"

    def test_unknown_path_404(self, episodes_file):
        handler = _get("/api/d1/training/other")
        assert handler.status == 404
        assert handler.payload["error"] == "not_found"


# ── 5. 只读边界 + 真实数据不被触碰 ──────────────────────────────────
class TestReadonly:
    def test_queries_do_not_write(self, episodes_file, tmp_path):
        _write(episodes_file, [_episode()])
        before_files = sorted(p.name for p in tmp_path.iterdir())
        before_hash = _sha(episodes_file)
        for path in (
            "/api/d1/training/episodes",
            "/api/d1/training/episodes/ep-20261104-ABCDEF12",
            "/api/d1/training/episodes/ep-20261104-00000000",
            "/api/d1/training/other",
        ):
            _get(path)
        assert _sha(episodes_file) == before_hash
        assert sorted(p.name for p in tmp_path.iterdir()) == before_files

    def test_real_training_data_path_not_created(self):
        # 生产默认路径在测试中不应被创建（测试均通过 env 覆盖）；这里只做只读断言
        assert isinstance(T.DEFAULT_EPISODES, Path)
        normalized = str(T.DEFAULT_EPISODES).replace("/", "\\")
        assert normalized.endswith("agent_platform\\data\\training\\episodes.jsonl")

    def test_env_override_used(self, episodes_file):
        _write(episodes_file, [_episode()])
        payload = _get("/api/d1/training/episodes").payload
        assert "episodes.jsonl" in payload["source"]
        assert T.episodes_path() == episodes_file


# ── 6. portal_api 接线 + route baseline 同批登记 ─────────────────────
class TestWiring:
    def test_portal_api_dispatch_wired(self):
        text = (ROOT / "portal" / "portal_api.py").read_text(encoding="utf-8")
        assert "/api/d1/training/episodes" in text
        assert "api_d1_training" in text
        assert text.count("api_d1_training.handle_get") == 1

    def test_route_baseline_has_training_routes(self):
        baseline = json.loads(
            (ROOT / "tools" / "portal_route_baseline.json").read_text(encoding="utf-8")
        )
        sigs = {tuple(item) for item in baseline["signatures"]}
        assert ("GET", "exact", "/api/d1/training/episodes") in sigs
        assert ("GET", "prefix", "/api/d1/training/episodes/") in sigs
        assert baseline["total"] >= 297

    def test_cli_json(self, episodes_file, capsys):
        _write(episodes_file, [_episode()])
        rc = T.main(["--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is True and payload["count"] == 1

    def test_cli_missing_data_still_ok(self, episodes_file, capsys):
        rc = T.main(["--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["data_unavailable"] is True and payload["count"] == 0
