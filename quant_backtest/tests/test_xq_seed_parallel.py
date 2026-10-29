# -*- coding: utf-8 -*-
"""xq_seed_parallel 双实例播种 wrapper 的分片/推进判据/路径契约测试。

全部 tmp_path 隔离，不触生产游标/状态/锁，不真跑 collect（mock subprocess）。
"""
import json
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import xq_seed_parallel as P  # noqa: E402


# ── 分片契约 ──

def test_shards_partition_seed_queue():
    a, b = P.SHARDS["A"], P.SHARDS["B"]
    assert set(a) | set(b) == set(P.SEED_QUEUE)
    assert not (set(a) & set(b)), "A/B 种子必须不相交"
    assert len(P.SEED_QUEUE) == len(a) + len(b)
    # 与单实例版同源同序：A 取偶数位、B 取奇数位
    assert a == [20000, 45000, 105000, 150000, 190000, 230000]
    assert b == [30000, 75000, 130000, 170000, 210000]


def test_paths_for_instances_are_disjoint():
    pa, pb = P.paths_for("A"), P.paths_for("B")
    for k in ("state", "lock", "cursor"):
        assert pa[k] != pb[k]
        assert "_A" in pa[k] or "A." in pa[k].split("_")[-1] or pa[k].endswith("_A.json") or "seed_auto_A" in pa[k]
    assert pa["cursor"].endswith("seed_cursor_A.json")
    assert pb["cursor"].endswith("seed_cursor_B.json")
    assert pa["state"].endswith("seed_auto_state_A.json")
    assert pa["lock"].endswith("seed_auto_A.lock")


# ── seed_exhausted（分片游标判定）──

def _mk_cursor(tmp_path, seed, cats_state):
    p = tmp_path / "cursor.json"
    p.write_text(json.dumps({str(seed): cats_state}), encoding="utf-8")
    return str(p)


def test_exhausted_true_only_when_all_cats(tmp_path):
    full = {str(c): {"max_id": 1, "exhausted": True} for c in P.CATS}
    p = _mk_cursor(tmp_path, 20000, full)
    assert P.seed_exhausted(p, 20000) is True


def test_exhausted_false_when_any_cat_open(tmp_path):
    full = {str(c): {"max_id": 1, "exhausted": True} for c in P.CATS}
    full["104"]["exhausted"] = False
    p = _mk_cursor(tmp_path, 20000, full)
    assert P.seed_exhausted(p, 20000) is False


def test_exhausted_false_on_missing_file_or_seed(tmp_path):
    missing = str(tmp_path / "nope.json")
    assert P.seed_exhausted(missing, 20000) is False
    p = _mk_cursor(tmp_path, 99999, {})
    assert P.seed_exhausted(p, 20000) is False


# ── 状态缺省与 done 跳过 ──

def test_load_state_defaults(tmp_path):
    st = P.load_state(str(tmp_path / "absent.json"))
    assert st["queue_idx"] == 0 and st["done"] is False


def test_main_done_short_circuits(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "BASE_DIR", str(tmp_path))
    P.paths_for("A")
    (tmp_path / "seed_auto_state_A.json").write_text(
        json.dumps({"done": True}), encoding="utf-8")
    called = {"collect": 0}

    def fake_call(*a, **k):
        called["collect"] += 1
        return 0

    monkeypatch.setattr(P, "db_count", lambda: 123)
    monkeypatch.setattr(P.subprocess, "call", fake_call)
    rc = P.main(["--instance", "A"])
    assert rc == 0
    assert called["collect"] == 0


def test_main_runs_collect_with_cursor_file(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "BASE_DIR", str(tmp_path))
    captured = {}

    def fake_call(cmd, **k):
        captured["cmd"] = cmd
        return 0

    monkeypatch.setattr(P, "db_count", lambda: 0)
    monkeypatch.setattr(P.subprocess, "call", fake_call)
    rc = P.main(["--instance", "A"])
    assert rc == 0
    cmd = captured["cmd"]
    assert "--seed" in cmd and "20000" in cmd          # A 队首种子
    assert "--cursor-file" in cmd
    assert cmd[cmd.index("--cursor-file") + 1].endswith("seed_cursor_A.json")
    # 锁已释放、状态已落盘
    assert not (tmp_path / "seed_auto_A.lock").exists()
    st = json.loads((tmp_path / "seed_auto_state_A.json").read_text(encoding="utf-8"))
    assert st["rounds"] and st["rounds"][-1]["seed"] == 20000
