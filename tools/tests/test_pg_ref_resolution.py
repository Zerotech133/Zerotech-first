# -*- coding: utf-8 -*-
"""ADR-019 P5：dataset/pg/* 以张松仓 registry 登记命中为证据（soft warning）。

覆盖：
1. registry 证据加载：wave1 9 表（dataset_registry.PG_WAVE1_TABLES）+ declared canonical_ref
   （v2_bar_daily 物理表只读证据；v2_zhice_tables 非物理聚合口径、由 9 张 wave1 表支撑，
   2026-11-05 W1-PILOT-TOOLFIX 补登记，data-warehouse manifest 契约未改）；
2. 已登记 pg 引用 resolve 无 warning；未登记 pg 引用 → 显式 soft warning（非 error）；
3. file refs 解析逻辑不被 pg 改动误杀；
4. 各组合 manifest 的 `dataset/pg/*` 与 registry 对账（2026-11-05 收口后未登记项应为空）；
5. `composition.py validate --all` errors=0 且无未登记 pg soft warning；strict 提升机制保留。

只读：不连 PG、不写生产数据、不改 manifest。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import composition as c  # noqa: E402

#: ADR-019 wave1 已登记 PG 表（dataset_registry.PG_WAVE1_TABLES；2026-11-05 共 9 张）
WAVE1_TABLES = (
    "v2_acad1_pool",
    "v2_selection_pool",
    "v2_stock_research",
    "v2_selection_report",
    "v2_strategy_ownership",
    "v2_acad1_review",
    "v2_base_signal",
    "v2_score_component",
    "v2_lusu_decision",
)

#: 组合 → 至少包含的 pg 表（映射对账，防止静默增删）
PG_MANIFEST_EXPECTED = {
    "acad1-legion": {
        "v2_acad1_pool",
        "v2_selection_pool",
        "v2_stock_research",
        "v2_selection_report",
        "v2_strategy_ownership",
        "v2_acad1_review",
        "v2_lusu_decision",
    },
    "lusu-d1-flow": {"v2_acad1_pool", "v2_base_signal",
                     "v2_acad1_review", "v2_lusu_decision"},
    # 2026-11-05 W1-PILOT-TOOLFIX：manifest 契约未改；聚合口径以 declared 声明登记
    "data-warehouse": {"v2_zhice_tables"},
    "quant-domain": {"v2_bar_daily", "v2_acad1_pool",
                     "v2_selection_pool", "v2_stock_research"},
}

#: 未登记项：2026-11-05 收口后应为空（v2_bar_daily 物理表按 declared 只读证据登记；
#: v2_zhice_tables 聚合口径按 declared 声明登记、由 9 张 wave1 表支撑）。
UNREGISTERED_PG_TABLES = set()


def _regs():
    return c.load_registries()


def _resolve(ref, regs=None):
    regs = regs if regs is not None else _regs()
    errors, warnings = [], []
    c.resolve_ref(ref, regs, errors, warnings)
    return errors, warnings


def _manifest_refs(composition_id):
    path = ROOT / "compositions" / composition_id / "manifest.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    requires = ((data.get("spec") or {}).get("data") or {}).get("requires") or []
    return [item.get("ref") if isinstance(item, dict) else item for item in requires]


def _pg_tables_in_manifest(composition_id):
    return {ref.split("/")[-1] for ref in _manifest_refs(composition_id)
            if isinstance(ref, str) and ref.startswith("dataset/pg/")}


# ── 1. registry 证据加载 ────────────────────────────────────────────
def test_wave1_registry_evidence_loaded():
    pg = _regs()["pg_registered"]
    for table in WAVE1_TABLES:
        assert table in pg, "wave1 登记表缺失: %s" % table
        assert pg[table]["wave"] == "wave1"
        assert pg[table]["source"].endswith("#PG_WAVE1_TABLES")
        assert pg[table]["registered"] is True
    assert pg["v2_acad1_pool"]["owner"] == "ACAD1"
    assert pg["v2_acad1_review"]["owner"] == "ACAD1"
    assert pg["v2_base_signal"]["owner"] == "D1"
    assert pg["v2_lusu_decision"]["owner"] == "D1"
    # 聚合口径 v2_zhice_tables 不冒充 wave1 物理表，但经 declared 声明登记（wave=declared）
    assert "v2_zhice_tables" not in WAVE1_TABLES
    agg = pg["v2_zhice_tables"]
    assert agg["wave"] == "declared"
    assert agg["registered"] is True and agg["owner"] == "DATA1"
    assert agg["source"] == "tools/datawarehouse/dataset_refs_declared.json"
    # v2_bar_daily 经 dataset_refs_declared 只读证据登记（wave=declared）
    bar = pg["v2_bar_daily"]
    assert bar["wave"] == "declared"
    assert bar["registered"] is True and bar["owner"] == "DATA1"
    assert bar["source"] == "tools/datawarehouse/dataset_refs_declared.json"


def test_declared_canonical_ref_evidence():
    """dataset_refs_declared.json 的 canonical_ref=dataset/pg/* 也是登记证据。"""
    path = ROOT / "tools" / "datawarehouse" / "dataset_refs_declared.json"
    declared = json.loads(path.read_text(encoding="utf-8"))
    canonical = {str(item.get("canonical_ref")) for item in declared.get("refs") or []
                 if str(item.get("canonical_ref") or "").startswith("dataset/pg/")}
    assert "dataset/pg/v2_acad1_pool" in canonical
    assert "dataset/pg/v2_bar_daily" in canonical  # W1-PILOT-TOOLFIX 补登记（物理表证据）
    assert "dataset/pg/v2_zhice_tables" in canonical  # 聚合口径 declared 声明
    pg = _regs()["pg_registered"]
    assert pg["v2_acad1_pool"]["wave"] == "wave1"  # wave1 优先去重
    assert pg["v2_bar_daily"]["wave"] == "declared"
    assert pg["v2_zhice_tables"]["wave"] == "declared"


# ── 2. resolve：登记命中 / 未登记 soft warning ─────────────────────
def test_registered_pg_refs_resolve_without_warning():
    for table in WAVE1_TABLES:
        errors, warnings = _resolve("dataset/pg/%s" % table)
        assert errors == [], "%s errors=%r" % (table, errors)
        assert warnings == [], "%s warnings=%r" % (table, warnings)


def test_aggregate_declared_ref_resolves_without_warning():
    """非物理聚合口径以 declared 声明为登记证据（不冒充物理表，但解析不告警）。"""
    errors, warnings = _resolve("dataset/pg/v2_zhice_tables")
    assert errors == [], errors
    assert warnings == [], warnings


def test_unregistered_pg_ref_emits_soft_warning():
    errors, warnings = _resolve("dataset/pg/v2_not_registered")
    assert errors == [], "未登记 PG 表不得直接 error（ADR-019 P5 过渡期 soft）"
    assert len(warnings) == 1
    warning = warnings[0]
    assert "dataset/pg 未在 registry 登记" in warning
    assert "soft warning" in warning and "ADR-019" in warning
    assert "v2_not_registered" in warning


def test_file_ref_not_killed_by_pg_registry(tmp_path, monkeypatch):
    """file ref 仍走原路径/登记解析（pg registry 改动不误杀）。"""
    monkeypatch.setattr(c, "ROOT", tmp_path)
    data_dir = tmp_path / "agent_platform" / "data"
    data_dir.mkdir(parents=True)
    (data_dir / "paper_ledger.jsonl").write_text("", encoding="utf-8")
    errors, warnings = _resolve("dataset/file/paper_ledger")
    assert errors == [] and warnings == []


# ── 3. manifest 映射与 registry 对账 ───────────────────────────────
@pytest.mark.parametrize("composition_id", sorted(PG_MANIFEST_EXPECTED))
def test_manifest_pg_refs_mapped(composition_id):
    tables = _pg_tables_in_manifest(composition_id)
    expected = PG_MANIFEST_EXPECTED[composition_id]
    assert expected <= tables, "%s 缺 pg refs: %s" % (
        composition_id, sorted(expected - tables))


def test_manifest_pg_refs_registered_or_explicitly_unregistered():
    pg = _regs()["pg_registered"]
    for composition_id in PG_MANIFEST_EXPECTED:
        tables = _pg_tables_in_manifest(composition_id)
        for table in tables:
            assert (table in pg) or (table in UNREGISTERED_PG_TABLES), (
                "%s 的 dataset/pg/%s 既未登记也未列入未登记清单（禁止静默）"
                % (composition_id, table))


def test_unregistered_items_reconciled_across_compositions():
    """所有 manifest 的 dataset/pg/* 对账：未登记项必须恰为已知清单。"""
    pg = _regs()["pg_registered"]
    all_tables = set()
    for path in sorted((ROOT / "compositions").glob("*/manifest.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        requires = ((data.get("spec") or {}).get("data") or {}).get("requires") or []
        for item in requires:
            ref = item.get("ref") if isinstance(item, dict) else item
            if isinstance(ref, str) and ref.startswith("dataset/pg/"):
                all_tables.add(ref.split("/")[-1])
    unregistered = sorted(all_tables - set(pg))
    assert unregistered == sorted(UNREGISTERED_PG_TABLES), (
        "未登记项漂移：%r（期望 %r）" % (unregistered, sorted(UNREGISTERED_PG_TABLES)))


def test_data_warehouse_aggregate_ref_declared():
    """2026-11-05：聚合口径留在 requires（契约未改），以 declared 声明消除 soft warning。"""
    data = yaml.safe_load(
        (ROOT / "compositions" / "data-warehouse" / "manifest.yaml")
        .read_text(encoding="utf-8"))
    spec = data.get("spec") or {}
    requires = ((spec.get("data") or {}).get("requires") or [])
    item = next(i for i in requires
                if isinstance(i, dict) and i.get("ref") == "dataset/pg/v2_zhice_tables")
    note = str(item.get("note") or "")
    for keyword in ("wave1", "wave2", "ZHICE_V2_DSN", "ADR-019"):
        assert keyword in note, "v2_zhice_tables note 缺 %s: %r" % (keyword, note)
    # 声明证据：非物理聚合、owner=DATA1、由 9 张 wave1 表支撑、ADR-019 P5 过渡期
    declared = json.loads(
        (ROOT / "tools" / "datawarehouse" / "dataset_refs_declared.json")
        .read_text(encoding="utf-8"))
    entry = {i["ref"]: i for i in declared["refs"]}["dataset/pg/v2_zhice_tables"]
    assert entry["owner"] == "DATA1" and entry["status"] == "active"
    assert entry["canonical_ref"] == "dataset/pg/v2_zhice_tables"
    assert entry["declared_at"] == "2026-11-05"
    declared_note = str(entry["note"])
    for keyword in ("非物理", "聚合", "wave1", "ADR-019"):
        assert keyword in declared_note, "declared note 缺 %s: %r" % (keyword, declared_note)
    errors, warnings = _resolve("dataset/pg/v2_zhice_tables")
    assert errors == [] and warnings == []


# ── 4. validate --all：errors=0 + soft warning 显式 + strict 时机 ──
def _run(*args):
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(ROOT / "tools" / "composition.py"), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(ROOT), timeout=120,
    )
    return proc.returncode, proc.stdout, proc.stderr


def test_validate_all_errors_zero():
    rc, out, err = _run("validate", "--all")
    assert rc == 0, "rc=%s\n%s\n%s" % (rc, out, err)
    assert "errors=0" in out, out


def test_validate_all_json_no_unregistered_pg_warning():
    """2026-11-05 收口：全组合不再有「dataset/pg 未在 registry 登记」soft warning。"""
    rc, out, err = _run("validate", "--all", "--json")
    assert rc == 0, err
    rows = json.loads(out)
    leaked = {}
    for row in rows:
        bad = [w for w in row["warnings"] if "dataset/pg 未在 registry 登记" in w]
        if bad:
            leaked[row["id"]] = bad
    assert leaked == {}, "仍有未登记 pg soft warning: %r" % leaked
    # 量化域 v2_bar_daily 已登记后不得再有软告警
    quant = {row["id"]: row for row in rows}["quant-domain"]
    assert not any("dataset/pg 未在 registry 登记" in w for w in quant["warnings"])


def test_pg_soft_warning_would_promote_to_error_under_strict():
    """ADR-019 P5：过渡期 warning → 严格化后可提升为 error（机制预演，不改变当前语义）。"""
    finding = {"kind": "dataset", "id": "dataset/pg/v2_not_registered", "level": "warning"}
    assert c.summarize_findings([finding], strict=False)["rc"] == 0
    assert c.summarize_findings([finding], strict=True)["rc"] == 1
