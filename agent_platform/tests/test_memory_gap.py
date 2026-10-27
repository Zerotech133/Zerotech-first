# -*- coding: utf-8 -*-
"""记忆 v2 补差测试（WO-MEMORY-V2-GAP-20261027）：namespace/append-only/有效期/episode 溯源。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_WT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "memory_store_gap", str(_WT / "agent_platform" / "memory" / "store.py")
)
MS = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(MS)


def _db(tmp_path, monkeypatch):
    db = tmp_path / "memory.sqlite"
    monkeypatch.setattr(MS, "DB", db)
    monkeypatch.setattr(MS, "EVAL_OUT", tmp_path / "eval_latest.json")
    return db


def test_namespace_filter_and_bridge(tmp_path, monkeypatch):
    db = _db(tmp_path, monkeypatch)
    assert MS.add("semantic", "席位记忆 A", "内容 A", namespace="agent/ACAD1", db=db)["ok"]
    assert MS.add("semantic", "席位记忆 B", "内容 B", namespace="agent/BACK1", db=db)["ok"]
    rep = MS.search("席位记忆", db=db, namespace="agent/ACAD1")
    assert rep["ok"] and len(rep["items"]) == 1
    assert rep["items"][0]["namespace"] == "agent/ACAD1"
    ns = MS.list_namespaces(db=db)["namespaces"]
    assert ns["agent/ACAD1"] == 1 and ns["agent/BACK1"] == 1
    assert MS.gns_to_namespace("dept/invest/strategy") == "dept/invest"
    assert MS.gns_to_namespace("agent/ARCH1/memory") == "agent/ARCH1"
    assert MS.gns_to_namespace("domain/quant") == "domain"
    assert MS.namespace_for_agent("BACK1") == "agent/BACK1"


def test_append_only_versions_and_history(tmp_path, monkeypatch):
    db = _db(tmp_path, monkeypatch)
    r1 = MS.add("semantic", "判例 X", "第一版：旧判断", mid="semantic:x1", db=db)
    assert r1["version"] == 1
    r2 = MS.add("semantic", "判例 X", "第二版：新判断", mid="semantic:x1", db=db)
    assert r2["version"] == 2 and r2["id"] == "semantic:x1"
    rep = MS.search("判例 X", db=db)
    assert rep["items"][0]["content"].startswith("第二版")
    assert rep["items"][0]["version"] == 2
    h = MS.history("semantic:x1", db=db)
    assert h["current"]["content"].startswith("第二版")
    assert len(h["history"]) == 1
    old = h["history"][0]
    assert old["content"].startswith("第一版")
    assert old["valid_to"] and old["reason"] == "superseded"
    assert old["valid_from"]


def test_episode_provenance(tmp_path, monkeypatch):
    db = _db(tmp_path, monkeypatch)
    raw = "601318 中国平安 2026-10-27 公告：XXX"
    r = MS.add("episodic", "601318 事件", "公告利好，需核验",
               source="announcement", episode_source="news_db",
               episode_content=raw, db=db)
    assert r["episode_id"]
    ep = MS.get_episode(r["episode_id"], db=db)
    assert ep["ok"] is True
    assert len(ep["episode"]["sha256"]) == 64
    assert ep["episode"]["source"] == "news_db"
    # 同一原文幂等复用同一 episode
    r2 = MS.add("episodic", "601318 事件2", "另一条", episode_source="news_db",
                episode_content=raw, db=db)
    assert r2["episode_id"] == r["episode_id"]
    rep = MS.search("601318 公告", db=db)
    assert rep["items"][0]["episode_id"] == r["episode_id"]


def test_old_schema_migration(tmp_path, monkeypatch):
    import sqlite3

    db = tmp_path / "memory.sqlite"
    monkeypatch.setattr(MS, "DB", db)
    monkeypatch.setattr(MS, "EVAL_OUT", tmp_path / "eval_latest.json")
    # 模拟旧版表（无 namespace/valid_from 列）
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE memories(
        id TEXT PRIMARY KEY, layer TEXT NOT NULL, scope TEXT DEFAULT 'zhaoyun',
        cue TEXT NOT NULL, content TEXT NOT NULL, source TEXT DEFAULT '',
        confidence REAL DEFAULT 0.8, tags TEXT DEFAULT '', created_at TEXT NOT NULL,
        last_used_at TEXT, use_count INTEGER DEFAULT 0, ttl_days INTEGER DEFAULT 365)""")
    conn.execute("INSERT INTO memories(id,layer,cue,content,created_at) "
                 "VALUES('old:1','semantic','旧记忆','旧内容','2026-01-01T00:00:00+0800')")
    conn.commit()
    conn.close()
    rep = MS.search("旧记忆", db=db)
    assert rep["items"][0]["namespace"] == "agent/zhaoyun"
    assert rep["items"][0]["valid_from"] == "2026-01-01T00:00:00+0800"
