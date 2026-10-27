# -*- coding: utf-8 -*-
"""zhuge_c3_decisions 单测（C3 采纳 ↔ 决策记录，离线）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import zhuge_c3_decisions as b  # noqa: E402
import zhuge_decision_record as dr  # noqa: E402


def use_repo(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("ZHUGE_C3DEC_REPO", str(repo))
    monkeypatch.setenv("ZHUGE_DECISION_REPO", str(repo))
    return repo


def write_adoption(repo: Path, domains: list[str]) -> Path:
    path = repo / "portal" / "data" / "zhuge_adoption.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for i, domain in enumerate(domains):
            fh.write(json.dumps({"at": "2026-10-27T10:0%d:00+08:00" % i,
                                 "domain": domain, "ua": "test",
                                 "received_at": "2026-10-27T10:00:00+08:00"},
                                ensure_ascii=False) + "\n")
    return path


def make_decision(monkeypatch, tmp_path, domain: str = "notify:deadline:us:postclose"):
    repo = use_repo(monkeypatch, tmp_path)
    out = dr.create(title="BM25 启用", question="是否启用 BM25？",
                    recommendation="先 BM25 观察 1 周", confidence="high",
                    adoption_domains=[domain])
    rid = out["id"]
    assert dr.decide(rid, "approved", by="老田")["ok"] is True
    return repo, rid, domain


def test_sync_links_domain(monkeypatch, tmp_path):
    repo, rid, domain = make_decision(monkeypatch, tmp_path)
    write_adoption(repo, [domain, domain])
    out = b.sync_adoptions()
    assert out["ok"] and out["adoption_events"] == 2
    assert out["linked"] and out["linked"][0]["id"] == rid
    rec = dr.show(rid)["record"]
    assert rec["adoption_id"] == "C3:%s" % domain
    assert "2 条" in rec["outcome"]


def test_sync_unmatched_reported(monkeypatch, tmp_path):
    repo, rid, domain = make_decision(monkeypatch, tmp_path)
    write_adoption(repo, ["other:domain", "other:domain"])
    out = b.sync_adoptions()
    assert out["linked"] == []
    assert "other:domain" in out["unmatched_domains"]


def test_dry_run_no_update(monkeypatch, tmp_path):
    repo, rid, domain = make_decision(monkeypatch, tmp_path)
    write_adoption(repo, [domain])
    out = b.sync_adoptions(dry_run=True)
    assert out["linked"] and out["linked"][0]["action"] == "would_update"
    rec = dr.show(rid)["record"]
    assert "adoption_id" not in rec or not rec.get("adoption_id")


def test_report_aggregates(monkeypatch, tmp_path):
    repo = use_repo(monkeypatch, tmp_path)
    write_adoption(repo, ["a", "a", "b"])
    adoptions = b._load_adoptions()
    agg = b.aggregate(adoptions)
    assert agg["a"]["count"] == 2 and agg["b"]["count"] == 1


def test_missing_ledger(monkeypatch, tmp_path):
    use_repo(monkeypatch, tmp_path)
    assert b._load_adoptions() == []
    out = b.sync_adoptions()
    assert out["ok"] and out["adoption_events"] == 0


def test_exact_decision_id_links_without_domains(monkeypatch, tmp_path):
    repo, rid, domain = make_decision(monkeypatch, tmp_path)
    path = repo / "portal" / "data" / "zhuge_adoption.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for i in range(2):
            fh.write(json.dumps({
                "at": "2026-10-27T10:0%d:00+08:00" % i,
                "domain": "unknown:domain",
                "decision_id": rid,
                "ua": "test"}, ensure_ascii=False) + "\n")
    out = b.sync_adoptions()
    assert out["ok"] and out["decisions"] == 1
    assert out["linked"] and out["linked"][0]["decision_id"] == rid
    rec = dr.show(rid)["record"]
    assert rec["adoption_id"] == "C3:decision:%s" % rid
    assert "精确匹配" in rec["outcome"]


def test_exact_decision_id_unmatched_reported(monkeypatch, tmp_path):
    repo = use_repo(monkeypatch, tmp_path)
    path = repo / "portal" / "data" / "zhuge_adoption.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "at": "2026-10-27T10:00:00+08:00", "domain": "研发",
        "decision_id": "DR-20261027-99"}, ensure_ascii=False) + "\n",
        encoding="utf-8")
    out = b.sync_adoptions()
    assert out["unmatched_decision_ids"] == ["DR-20261027-99"]
