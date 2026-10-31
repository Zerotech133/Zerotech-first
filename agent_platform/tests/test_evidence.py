# -*- coding: utf-8 -*-
"""Phase C evidence artifact 测试（含 P0 证据链内嵌 prev_hash/record_hash）。

覆盖：new_run/append/finalize 基本行为、链字段 canonical 口径、顺序链、未完成记录不参与
链接、旧文件（无链字段）兼容、append 清链、HMAC 口径不变、原子写失败不留半条、
并发 finalize 不分叉、锁释放、默认目录不变。
"""
import hashlib
import hmac
import importlib.util
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MOD = ROOT / "agent_platform" / "legion_command" / "evidence.py"


def _load():
    spec = importlib.util.spec_from_file_location("evidence_test", MOD)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _canonical_chain(rec):
    """独立复算链 canonical（排除 signature/prev_hash/record_hash）。"""
    body = {k: v for k, v in rec.items()
            if k not in ("signature", "prev_hash", "record_hash")}
    return hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _canonical_hmac(rec, key):
    """独立复算 HMAC（口径不变：除 signature 外整条记录，含链字段）。"""
    body = {k: v for k, v in rec.items() if k != "signature"}
    return hmac.new(key, json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8"),
                    hashlib.sha256).hexdigest()


def _use_tmp(monkeypatch, tmp_path, with_key=True):
    evidence_dir = tmp_path / "ev"
    monkeypatch.setenv("LEGION_EVIDENCE_DIR", str(evidence_dir))
    if with_key:
        key_file = tmp_path / "cap.key"
        key_file.write_bytes(b"k" * 32)
        monkeypatch.setenv("LEGION_CAP_KEY_FILE", str(key_file))
        return evidence_dir, b"k" * 32
    # 显式指向不存在的 key，避免回退到生产 cap key（测试不得依赖/误用生产密钥）
    monkeypatch.setenv("LEGION_CAP_KEY_FILE", str(tmp_path / "missing-cap.key"))
    return evidence_dir, b""


def _write_legacy(evidence_dir, run_id, key=b""):
    """写一条旧格式（无链字段）记录；key 非空时按旧 HMAC 口径签名。"""
    evidence_dir.mkdir(parents=True, exist_ok=True)
    rec = {"schema": "legion-evidence/v1", "run_id": run_id, "kind": "orchestrate",
           "task": "T-LEGACY", "agent": "A0",
           "created_at": "2026-10-30T10:00:00+08:00",
           "finalized_at": "2026-10-30T10:05:00+08:00", "events": []}
    if key:
        rec["signature"] = _canonical_hmac(rec, key)
    path = evidence_dir / ("%s.json" % run_id)
    path.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    return path, rec


# ---------------------------------------------------------------------------
# 基础行为（原有）
# ---------------------------------------------------------------------------
def test_new_append_finalize(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path, with_key=False)
    m = _load()
    m.new_run("run-x", task="T1", agent="A0")
    m.append("run-x", "hermes_plan", {"plan": "p"})
    m.finalize("run-x", mode="answer")
    data = m.get("run-x")
    assert data is not None
    assert data["run_id"] == "run-x" and data["task"] == "T1"
    assert data["mode"] == "answer"
    assert len(data["events"]) == 1
    assert data["events"][0]["event"] == "hermes_plan"
    # 无 key 时 signature 为空，但链字段仍写入
    assert data["signature"] == ""
    assert len(data["record_hash"]) == 64
    assert data["prev_hash"] == m.GENESIS_HASH


def test_signature_when_key(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    m = _load()
    m.new_run("run-y")
    rec = m.finalize("run-y", mode="answer")
    assert rec.get("signature") and len(rec["signature"]) == 64


# ---------------------------------------------------------------------------
# 链字段 canonical 口径 / 顺序链
# ---------------------------------------------------------------------------
def test_chain_fields_on_finalize(monkeypatch, tmp_path):
    _evidence, key = _use_tmp(monkeypatch, tmp_path)
    m = _load()
    rec = m.finalize("run-chain", mode="answer")
    data = m.get("run-chain")
    assert data["prev_hash"] == m.GENESIS_HASH
    assert data["record_hash"] == _canonical_chain(data)
    assert data["record_hash"] == rec["record_hash"]
    # HMAC 口径不变：除 signature 外整条记录（含链字段）
    assert data["signature"] == _canonical_hmac(data, key)


def test_chain_links_sequential(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    m = _load()
    first = m.finalize("run-1")
    second = m.finalize("run-2")
    third = m.finalize("run-3")
    assert second["prev_hash"] == first["record_hash"]
    assert third["prev_hash"] == second["record_hash"]
    assert third["prev_hash"] != m.GENESIS_HASH
    # 每条 record_hash 与自身 canonical 一致
    for run_id in ("run-1", "run-2", "run-3"):
        data = m.get(run_id)
        assert data["record_hash"] == _canonical_chain(data)


def test_unfinalized_record_not_linked(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    m = _load()
    m.new_run("run-pending")  # 未完成
    done = m.finalize("run-done")
    assert done["prev_hash"] == m.GENESIS_HASH  # 只有未完成记录 → 创世
    pending = m.finalize("run-pending")
    assert pending["prev_hash"] == done["record_hash"]


def test_legacy_file_compat(monkeypatch, tmp_path):
    evidence_dir, key = _use_tmp(monkeypatch, tmp_path)
    path, _legacy = _write_legacy(evidence_dir, "run-legacy", key=key)
    before = path.read_bytes()
    m = _load()
    rec = m.finalize("run-new")
    # 旧文件保持原样（不迁移、不重写）
    assert path.read_bytes() == before
    stored = json.loads(before.decode("utf-8"))
    assert "record_hash" not in stored and "prev_hash" not in stored
    assert stored["signature"] == _canonical_hmac(stored, key)  # 旧签名仍可验
    # 新记录 prev 链接到旧记录的 canonical 补算哈希
    assert rec["prev_hash"] == _canonical_chain(stored)


def test_append_clears_chain_fields(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    m = _load()
    m.finalize("run-a")
    m.append("run-a", "test_result", {"ok": True})
    mid = m.get("run-a")
    assert "record_hash" not in mid and "prev_hash" not in mid
    again = m.finalize("run-a")
    assert again["record_hash"] == _canonical_chain(again)
    # 重新 finalize 时自身被排除 → 目录内无其它已完成记录 → 创世
    assert again["prev_hash"] == m.GENESIS_HASH


# ---------------------------------------------------------------------------
# 原子写 / 并发
# ---------------------------------------------------------------------------
def test_atomic_write_failure_leaves_old_file(monkeypatch, tmp_path):
    evidence_dir, _key = _use_tmp(monkeypatch, tmp_path)
    m = _load()
    m.finalize("run-a")
    before = (evidence_dir / "run-a.json").read_bytes()
    original_replace = os.replace

    def boom(src, dst):
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", boom)
    assert m.finalize("run-a", mode="changed") == {}
    monkeypatch.setattr(os, "replace", original_replace)
    assert (evidence_dir / "run-a.json").read_bytes() == before
    # 无半条临时文件残留
    assert not list(evidence_dir.glob("*.tmp.*"))
    # 锁已释放：后续 finalize 正常
    assert m.finalize("run-a", mode="ok")["mode"] == "ok"


def test_concurrent_finalize_no_fork(monkeypatch, tmp_path):
    evidence_dir, _key = _use_tmp(monkeypatch, tmp_path)
    m = _load()
    run_ids = ["run-c%d" % index for index in range(4)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(m.finalize, run_ids))
    assert all(results), "并发 finalize 不应返回空"
    records = {}
    for run_id in run_ids:
        rec = m.get(run_id)
        assert rec is not None and rec["record_hash"] == _canonical_chain(rec)
        records[rec["record_hash"]] = rec
    genesis_children = [rec for rec in records.values()
                        if rec["prev_hash"] == m.GENESIS_HASH]
    assert len(genesis_children) == 1, "并发下应只有一条创世后继（不分叉）"
    prevs = [rec["prev_hash"] for rec in records.values()]
    assert len(set(prevs)) == len(prevs), "prev_hash 不应被两条记录复用"
    for rec in records.values():
        if rec["prev_hash"] != m.GENESIS_HASH:
            assert rec["prev_hash"] in records, "prev 必须指向目录内已完成记录"
    # 从创世沿链应能遍历全部 4 条（单链无分叉）
    chain = [genesis_children[0]]
    while True:
        next_recs = [rec for rec in records.values()
                     if rec["prev_hash"] == chain[-1]["record_hash"]]
        if not next_recs:
            break
        chain.append(next_recs[0])
    assert len(chain) == len(run_ids)


def test_finalized_at_strictly_increasing(monkeypatch, tmp_path):
    """finalized_at 微秒精度 + 严格递增（同秒 finalize 也能稳定排序，链不分叉）。"""
    _use_tmp(monkeypatch, tmp_path)
    m = _load()
    recs = [m.finalize("run-m%d" % index) for index in range(3)]
    times = [rec["finalized_at"] for rec in recs]
    assert len(set(times)) == 3
    assert times == sorted(times)
    assert any("." in value for value in times), "应为微秒级精度"


def test_default_dir_unchanged(monkeypatch):
    monkeypatch.delenv("LEGION_EVIDENCE_DIR", raising=False)
    m = _load()
    assert str(m._dir()) == m.DEFAULT_DIR
