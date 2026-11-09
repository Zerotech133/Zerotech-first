# -*- coding: utf-8 -*-
"""DWH-103 wave1 定向测试：pg_table / contract_artifact / C:\\Temp 白名单 / stats_v2 / CLI。

口径（ADR-019）：缺 DSN=missing_dsn 不假绿；连接失败=connector_unavailable fail-visible；
契约产物仅显式白名单直属文件（禁递归）；stats() v1（文件级）字段/数值不变，新增 stats_v2()。
"""
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dataset_registry_cli as cli  # noqa: E402
from dataset_registry import (  # noqa: E402
    ConnectorUnavailable,
    ContractArtifactUnavailable,
    DatasetRegistry,
)


def _inventory(tmp_path: Path) -> Path:
    inv = tmp_path / "inv.json"
    inv.write_text(json.dumps({"datasets": [
        {"domain": "学习派生", "label": "testdb.t1", "kind": "sqlite_table",
         "path": "testdb.db", "registered": True, "exposed": False,
         "suggested_id": "t1_table"},
    ]}, ensure_ascii=False), encoding="utf-8")
    return inv


def _reg(tmp_path: Path, **kwargs) -> DatasetRegistry:
    kwargs.setdefault("allowed_roots", [tmp_path])
    return DatasetRegistry(_inventory(tmp_path), **kwargs)


def _temp_root(tmp_path: Path) -> Path:
    root = tmp_path / "TempRoot"
    root.mkdir(exist_ok=True)
    return root


def _artifact(tmp_path: Path, *, name="acad1_evidence_bundle", content=b'{"ok": true, "n": 3}',
              sha=None, make=True) -> dict:
    root = _temp_root(tmp_path)
    path = root / (name + ".json")
    if make:
        path.write_bytes(content)
        actual = hashlib.sha256(content).hexdigest()
    else:
        actual = None
    return {
        "artifact_id": name, "path": str(path), "owner": "ACAD1",
        "produced_by_job": "job/acad1-evidence", "schema_ref": "acad1-evidence/v1",
        "retention_days": 30, "sha256": actual if sha is None else sha,
        "status": "active",
    }


# ---------- pg_table ----------

def test_pg_table_missing_dsn_is_disabled_not_green(tmp_path, monkeypatch):
    monkeypatch.delenv("ZHICE_V2_DSN", raising=False)
    # DW-PG-DSN：显式关闭 jev_common fallback，锁定「全部来源不可用」口径
    monkeypatch.setenv("DW_PG_DSN_FALLBACK", "0")
    reg = _reg(tmp_path, temp_whitelist=[])  # 本用例只看 pg 节，空契约白名单保证确定性
    assert reg.connector_status == "missing_dsn"
    assert len(reg.pg_datasets) == 9  # wave1 核心表（≤15；ACAD1-DATAREFS 2026-11-04 增 2 表）
    assert all(d["kind"] == "pg_table" and d["status"] == "missing_dsn"
               and d["available"] is False for d in reg.pg_datasets)
    sv2 = reg.stats_v2()
    assert sv2["denominator_version"] == "v2"
    assert sv2["connector_status"] == "missing_dsn"
    assert sv2["pg"]["total"] == 9 and sv2["pg"]["available"] == 0
    # 不假绿：未连接的 PG 表不计入合并可用口径
    assert sv2["all"]["total"] == reg.stats()["total"]
    with pytest.raises(ConnectorUnavailable) as exc:
        reg.sample("pg_public_v2_acad1_pool")
    assert exc.value.status == "missing_dsn"


def test_pg_connector_failure_is_fail_visible(tmp_path):
    def boom(dsn, tables):
        raise ConnectionError("connection refused (test)")

    reg = _reg(tmp_path, pg_dsn="postgresql://readonly@127.0.0.1:1/nope", pg_probe=boom)
    assert reg.connector_status == "connector_unavailable"
    assert "connection refused" in reg.connector_error
    sv2 = reg.stats_v2()
    assert sv2["connector_status"] == "connector_unavailable"
    assert sv2["pg"]["available"] == 0
    with pytest.raises(ConnectorUnavailable) as exc:
        reg.sample("pg_public_v2_base_signal")
    assert exc.value.status == "connector_unavailable"


def test_pg_connector_ok_marks_available_tables(tmp_path):
    seen = {}

    def probe(dsn, tables):
        seen["dsn"], seen["tables"] = dsn, list(tables)
        return {("public", "v2_acad1_pool"): 123}

    reg = _reg(tmp_path, pg_dsn="postgresql://readonly@127.0.0.1:5446/zhice_os_v2",
               pg_probe=probe, temp_whitelist=[])
    assert reg.connector_status == "ok"
    pool = reg.get("pg_public_v2_acad1_pool")
    assert pool["status"] == "active" and pool["available"] is True
    assert pool["row_estimate"] == 123 and pool["freshness"] is None
    assert pool["access"] == "read_only"
    assert reg.get("pg_public_v2_base_signal")["status"] == "table_missing"
    with pytest.raises(ConnectorUnavailable) as miss_exc:
        reg.sample("pg_public_v2_base_signal")
    assert miss_exc.value.status == "table_missing"
    sv2 = reg.stats_v2()
    assert sv2["pg"]["available"] == 1
    assert sv2["all"]["total"] == reg.stats()["total"] + 1
    assert len(seen["tables"]) == 9 and "v2_acad1_pool" in seen["tables"]
    assert "v2_acad1_review" in seen["tables"] and "v2_lusu_decision" in seen["tables"]
    assert reg.resolve_ref("dataset/pg/v2_acad1_pool") is pool


# ---------- contract_artifact / C:\Temp 白名单 ----------

def test_contract_artifact_whitelist_hit_and_miss(tmp_path):
    art = _artifact(tmp_path)
    reg = _reg(tmp_path, temp_root=_temp_root(tmp_path), temp_whitelist=[art])
    row = reg.get("tmp_acad1_evidence_bundle")
    assert row["kind"] == "contract_artifact" and row["status"] == "active"
    assert row["ref_aliases"] == ["acad1_evidence_bundle",
                                  "dataset/file/acad1_evidence_bundle"]
    assert reg.contract_path_allowed(art["path"]) is True
    evil = _temp_root(tmp_path) / "evil.json"
    evil.write_text("{}", encoding="utf-8")
    assert reg.contract_path_allowed(evil) is False
    nested = _temp_root(tmp_path) / "nested" / "x.json"
    nested.parent.mkdir()
    nested.write_text("{}", encoding="utf-8")
    assert reg.contract_path_allowed(nested) is False  # 禁递归/子目录
    assert reg.resolve_ref("dataset/file/acad1_evidence_bundle") is row
    out = reg.sample("tmp_acad1_evidence_bundle")
    assert out["status"] == "active" and out["sample"]["n"] == 3
    # 行内路径被篡改到非白名单：采样必须拒绝
    real = row["path"]
    row["path"] = str(evil)
    with pytest.raises(PermissionError):
        reg.sample("tmp_acad1_evidence_bundle")
    row["path"] = real


def test_contract_artifact_missing_and_sha_mismatch_fail_visible(tmp_path):
    missing = _artifact(tmp_path, name="missing_art", make=False)
    reg = _reg(tmp_path, temp_root=_temp_root(tmp_path), temp_whitelist=[missing])
    assert reg.get("tmp_missing_art")["status"] == "missing"
    sv2 = reg.stats_v2()
    assert sv2["contract_artifacts"]["total"] == 1
    assert sv2["contract_artifacts"]["missing"] == 1
    assert sv2["contract_artifacts"]["active"] == 0
    with pytest.raises(ContractArtifactUnavailable):
        reg.sample("tmp_missing_art")

    # R12 D4：定时产物（produced_by_job 非空）字节变动 = drift（info，仍可采样），不是篡改
    drifted = _artifact(tmp_path, name="drifted_art", content=b'{"v": 1}', sha="0" * 64)
    reg2 = _reg(tmp_path, temp_root=_temp_root(tmp_path), temp_whitelist=[drifted])
    row2 = reg2.get("tmp_drifted_art")
    assert row2["status"] == "drift" and row2["available"] is True
    assert row2["sha256_actual"] != row2["sha256_expected"]
    assert reg2.stats_v2()["contract_artifacts"]["drift"] == 1
    assert reg2.stats_v2()["contract_artifacts"]["mismatch"] == 0
    sampled = reg2.sample("tmp_drifted_art")
    assert sampled["status"] == "drift" and sampled["drift"]["sha256_actual"]

    # 静态契约产物（无 produced_by_job）字节不符仍须 fail-visible
    static = _artifact(tmp_path, name="static_art", content=b'{"v": 2}', sha="1" * 64)
    static["produced_by_job"] = None
    reg3 = _reg(tmp_path, temp_root=_temp_root(tmp_path), temp_whitelist=[static])
    assert reg3.get("tmp_static_art")["status"] == "sha256_mismatch"
    assert reg3.stats_v2()["contract_artifacts"]["mismatch"] == 1
    with pytest.raises(ContractArtifactUnavailable):
        reg3.sample("tmp_static_art")


def test_contract_artifact_directory_rejected_without_recursion(tmp_path):
    root = _temp_root(tmp_path)
    bundle = root / "bundle"
    bundle.mkdir()
    (bundle / "nested.json").write_text('{"x": 1}', encoding="utf-8")
    art = {"artifact_id": "dir_artifact", "path": str(bundle), "owner": "A0",
           "produced_by_job": "job/x", "schema_ref": "x/v1", "retention_days": 1,
           "sha256": None, "status": "active"}
    reg = _reg(tmp_path, temp_root=root, temp_whitelist=[art])
    assert reg.get("tmp_dir_artifact")["status"] == "not_a_file"
    with pytest.raises(ContractArtifactUnavailable) as exc:
        reg.sample("tmp_dir_artifact")
    assert "目录" in str(exc.value)
    assert "nested.json" not in str(exc.value)  # 未递归读取目录内容


# ---------- stats 兼容 / 默认 list 向后兼容 ----------

def test_stats_v2_preserves_v1_baseline_and_keys(tmp_path):
    art = _artifact(tmp_path)
    reg = _reg(tmp_path, temp_root=_temp_root(tmp_path), temp_whitelist=[art])
    v1 = reg.stats()
    assert set(v1) == {"inventory", "total", "registered", "exposed", "by_domain", "by_kind"}
    sv2 = reg.stats_v2()
    assert sv2["file_inventory"] == {
        "total": v1["total"], "registered": v1["registered"],
        "exposed": v1["exposed"], "by_kind": v1["by_kind"],
    }
    assert len(reg.datasets) == v1["total"]
    assert all(d["kind"] in ("dir", "sqlite_table", "corpus_layer") for d in reg.datasets)
    # 默认 list 保持 v1 文件级口径；kinds='all' 才含新 kind
    assert all(d["kind"] in ("dir", "sqlite_table", "corpus_layer")
               for d in reg.list(limit=100))
    kinds = {d["kind"] for d in reg.list(kinds="all", limit=100)}
    assert "pg_table" in kinds and "contract_artifact" in kinds


# ---------- CLI --json 契约 ----------

def test_cli_json_contract(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("ZHICE_V2_DSN", raising=False)
    monkeypatch.setenv("DW_PG_DSN_FALLBACK", "0")  # 锁定 fail-visible 缺 DSN 口径
    assert cli.main(["--json", "stats"]) == 0
    stats = json.loads(capsys.readouterr().out)
    assert "total" in stats and "by_kind" in stats and stats["total"] > 0

    rc = cli.main(["--json", "stats-v2"])
    assert rc == 2  # fail-visible：缺 DSN 不假绿
    sv2 = json.loads(capsys.readouterr().out)
    assert sv2["denominator_version"] == "v2"
    assert sv2["connector_status"] == "missing_dsn"

    assert cli.main(["--json", "list", "--kind", "pg_table", "--limit", "20"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 9 and all(r["kind"] == "pg_table" for r in rows)

    assert cli.main(["--json", "resolve-ref", "--ref", "dataset/pg/v2_acad1_pool"]) == 0
    assert json.loads(capsys.readouterr().out)["kind"] == "pg_table"

    assert cli.main(["--json", "get", "--id", "no_such_id"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]


# ---------- R12 D4：sha256 基线外置（出厂常量 → 运维基线） ----------

def test_load_contract_baseline_file_and_invalid_entries(tmp_path):
    import dataset_registry as dr
    p = tmp_path / "contract_baseline.json"
    p.write_text(json.dumps({"schema": "contract_baseline/v1", "baselines": {
        "good_art": {"sha256": "A" * 64, "confirmed_at": "2026-11-06T10:40:00",
                     "confirmed_by": "DATA1"},
        "short_sha": {"sha256": "abc"},
        "non_hex": {"sha256": "z" * 64},
        "not_a_dict": "oops",
    }}), encoding="utf-8")
    out = dr.load_contract_baseline(p)
    assert out["loaded"] is True
    assert list(out["baselines"]) == ["good_art"]      # 非法基线必须被丢弃
    assert out["baselines"]["good_art"]["sha256"] == "a" * 64  # 统一小写


def test_load_contract_baseline_missing_returns_empty(tmp_path):
    import dataset_registry as dr
    out = dr.load_contract_baseline(tmp_path / "nope.json")
    assert out["loaded"] is False and out["baselines"] == {}


def test_apply_baseline_overrides_factory_sha(tmp_path):
    reg = _reg(tmp_path, temp_root=_temp_root(tmp_path),
               temp_whitelist=[_artifact(tmp_path)])
    reg.contract_baseline = {"loaded": True, "baselines": {
        "acad1_evidence_bundle": {"sha256": "a" * 64,
                                  "confirmed_at": "2026-11-06T10:40:00",
                                  "confirmed_by": "DATA1"}}}
    spec = reg._apply_baseline({"artifact_id": "acad1_evidence_bundle", "sha256": "b" * 64})
    assert spec["sha256"] == "a" * 64
    assert spec["sha256_factory"] == "b" * 64
    assert spec["baseline_confirmed_at"] == "2026-11-06T10:40:00"
    # 未登记基线的 artifact 保持出厂常量
    untouched = reg._apply_baseline({"artifact_id": "other", "sha256": "c" * 64})
    assert untouched["sha256"] == "c" * 64 and "sha256_factory" not in untouched


def test_contract_baseline_merges_into_default_whitelist(tmp_path, monkeypatch):
    """出厂常量过期 + 基线已确认 → 不得再报红（这正是 R12 D4 的原始症状）。"""
    import dataset_registry as dr
    factory = "2" * 64
    spec = _artifact(tmp_path, name="baselined_art", content=b'{"v": 3}', sha=factory)
    actual = hashlib.sha256(b'{"v": 3}').hexdigest()
    monkeypatch.setattr(dr, "TEMP_CONTRACT_WHITELIST", (spec,))
    baseline = {"loaded": True, "baselines": {"baselined_art": {
        "sha256": actual, "confirmed_at": "2026-11-06T10:40:00", "confirmed_by": "DATA1"}}}
    reg = _reg(tmp_path, temp_root=_temp_root(tmp_path), contract_baseline=baseline)
    row = reg.get("tmp_baselined_art")
    assert row["status"] == "active"
    assert row["sha256"] == actual
    assert reg.temp_whitelist[0]["sha256_factory"] == factory
