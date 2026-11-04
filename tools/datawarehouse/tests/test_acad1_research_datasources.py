# -*- coding: utf-8 -*-
"""ACAD1 research reviews/decisions 数据源登记（ACAD1-DATAREFS，2026-11-04 task-58）。

口径：
1. `PG_WAVE1_TABLES` 增 `v2_acad1_review`（owner ACAD1）与 `v2_lusu_decision`（owner D1），
   registered/exposed=true；canonical ref=`dataset/pg/v2_acad1_review` /
   `dataset/pg/v2_lusu_decision`；
2. DSN 可用（注入 probe/连接）时 get/sample 两表 active；表缺失 → table_missing
   （fail-visible，不伪造）；DSN 不可用 → missing_dsn；
3. `dataset_refs_declared.json` 的 file ref 保留 fallback，canonical 只指向真实官方载体；
4. compositions/acad1-legion + lusu-d1-flow 补 canonical refs 且保留 file fallback；
   `tools/composition.py validate --strict` errors=0。

全程 mock PG（不连真实库）；真实两表状态由本单 live 证据（CLI）单独记录。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
DW = REPO / "tools" / "datawarehouse"
COMP = REPO / "compositions"
sys.path.insert(0, str(DW))
import dataset_registry as dr  # noqa: E402

CANONICAL = {
    "dataset/pg/v2_acad1_review": ("v2_acad1_review", "ACAD1"),
    "dataset/pg/v2_lusu_decision": ("v2_lusu_decision", "D1"),
}


# ── 工具 ──────────────────────────────────────────────────────────────
def _inventory(tmp_path: Path) -> Path:
    inv = tmp_path / "dataset_inventory_test.json"
    inv.write_text(json.dumps({"datasets": []}, ensure_ascii=False), encoding="utf-8")
    return inv


def _reg(tmp_path: Path, **kwargs) -> dr.DatasetRegistry:
    kwargs.setdefault("allowed_roots", [tmp_path])
    kwargs.setdefault("temp_whitelist", [])
    return dr.DatasetRegistry(_inventory(tmp_path), **kwargs)


def _both_tables_probe(target, tables):
    return {("public", "v2_acad1_review"): 7, ("public", "v2_lusu_decision"): 1}


class _FakeCursor:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.description = [("id",)]
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return list(self.rows)

    def close(self):
        pass


class _FakeConn:
    def __init__(self, cursors):
        self._cursors = list(cursors)
        self.closed = False

    def cursor(self):
        assert self._cursors, "unexpected extra cursor() call"
        return self._cursors.pop(0)

    def close(self):
        self.closed = True


def _manifest(name: str) -> dict:
    with (COMP / name).open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _data_refs(manifest: dict) -> list[str]:
    data = (manifest.get("spec") or {}).get("data") or {}
    return [str(item.get("ref")) for item in (data.get("requires") or []) if isinstance(item, dict)]


# ── 1. wave1 声明与 canonical ref 解析 ──────────────────────────────
class TestWave1Declaration:
    def test_wave1_contains_two_tables_with_owner(self):
        rows = {row["table"]: row for row in dr.PG_WAVE1_TABLES}
        for table, owner in (("v2_acad1_review", "ACAD1"),
                             ("v2_lusu_decision", "D1")):
            assert table in rows, "wave1 缺表 %s" % table
            row = rows[table]
            assert row["owner"] == owner
            assert row["registered"] is True and row["exposed"] is True

    def test_canonical_refs_resolve(self, tmp_path):
        reg = _reg(tmp_path, pg_dsn="postgresql://fake/zhice_os_v2",
                   pg_probe=_both_tables_probe)
        for ref, (table, owner) in CANONICAL.items():
            row = reg.resolve_ref(ref)
            assert row is not None, "canonical ref 未登记: %s" % ref
            assert row["kind"] == "pg_table"
            assert row["registered"] is True
            assert row["owner"] == owner
            assert row["physical"]["table"] == table
            # 短 token 别名也命中
            assert reg.resolve_ref("dataset/pg/%s" % table) is not None
            assert reg.get(row["dataset_id"]) is not None

    def test_both_tables_active_when_present(self, tmp_path):
        reg = _reg(tmp_path, pg_dsn="postgresql://fake/zhice_os_v2",
                   pg_probe=_both_tables_probe)
        for ref, (table, _owner) in CANONICAL.items():
            row = reg.resolve_ref(ref)
            assert row["status"] == "active" and row["available"] is True
            assert row["row_estimate"] in (7, 1)
            assert row["access"] == "read_only"
        sv2 = reg.stats_v2()
        assert sv2["connector_status"] == "ok"
        assert sv2["pg"]["total"] == len(dr.PG_WAVE1_TABLES)
        assert sv2["pg"]["available"] == 2


# ── 2. 缺表 / 缺 DSN fail-visible（不伪造） ─────────────────────────
class TestFailVisible:
    def test_missing_table_marks_table_missing_and_sample_blocked(self, tmp_path):
        def only_review(target, tables):
            return {("public", "v2_acad1_review"): 7}

        reg = _reg(tmp_path, pg_dsn="postgresql://fake/zhice_os_v2",
                   pg_probe=only_review)
        review = reg.resolve_ref("dataset/pg/v2_acad1_review")
        lusu = reg.resolve_ref("dataset/pg/v2_lusu_decision")
        assert review["status"] == "active" and review["available"] is True
        assert lusu["status"] == "table_missing" and lusu["available"] is False
        with pytest.raises(dr.ConnectorUnavailable) as exc:
            reg.sample("pg_public_v2_lusu_decision")
        assert exc.value.status == "table_missing"
        # 不伪造：available 只计真实命中的表
        assert reg.stats_v2()["pg"]["available"] == 1

    def test_no_dsn_marks_missing_dsn(self, tmp_path, monkeypatch):
        monkeypatch.delenv(dr.PG_DSN_ENV, raising=False)
        monkeypatch.setenv(dr.PG_DSN_FALLBACK_ENV, "0")
        reg = _reg(tmp_path)
        assert reg.connector_status == "missing_dsn"
        for ref in CANONICAL:
            row = reg.resolve_ref(ref)
            assert row["status"] == "missing_dsn" and row["available"] is False
        with pytest.raises(dr.ConnectorUnavailable) as exc:
            reg.sample("pg_public_v2_acad1_review")
        assert exc.value.status == "missing_dsn"


# ── 3. sample 两表（mock 连接，只读 SELECT） ─────────────────────────
class TestSample:
    def test_sample_both_tables(self, tmp_path):
        review_cur = _FakeCursor([(1, "2026-11-03", "topic")])
        lusu_cur = _FakeCursor([(1, "2026-11-03", "600519")])
        calls = []

        def connect(target):
            calls.append(target)
            if len(calls) == 1:
                return _FakeConn([review_cur])
            return _FakeConn([lusu_cur])

        reg = _reg(tmp_path, pg_dsn="postgresql://fake/zhice_os_v2",
                   pg_probe=_both_tables_probe, pg_connect=connect)
        out = reg.sample("pg_public_v2_acad1_review", n=2)
        assert out["dataset_id"] == "pg_public_v2_acad1_review"
        assert out["sample"] == [[1, "2026-11-03", "topic"]]
        out2 = reg.sample("pg_public_v2_lusu_decision", n=2)
        assert out2["sample"] == [[1, "2026-11-03", "600519"]]
        # 只读 SELECT 且表名/limit 参数化（不拼任意输入）
        assert "SELECT * FROM" in review_cur.executed[0][0]
        assert review_cur.executed[0][1] == (2,)


# ── 4. declared refs / manifests canonical + file fallback ──────────
class TestDeclaredAndManifests:
    def test_declared_refs_canonical_mapping(self):
        declared = {item["ref"]: item for item in dr.load_declared_refs()}
        pool_review = declared["dataset/file/pool_review"]
        assert pool_review["canonical_ref"] == "dataset/pg/v2_acad1_review"
        assert "review_date" in pool_review["note"]
        d1 = declared["dataset/file/d1_decisions"]
        assert d1["status"] == "planned"
        assert not d1.get("canonical_ref")
        assert "dataset/pg/v2_lusu_decision" in d1["note"]
        assert "不冒充正式账" in d1["note"]

    @pytest.mark.parametrize("name", ["acad1-legion/manifest.yaml",
                                      "lusu-d1-flow/manifest.yaml"])
    def test_manifests_have_canonical_refs_and_file_fallback(self, name):
        refs = _data_refs(_manifest(name))
        for ref in CANONICAL:
            assert ref in refs, "%s 缺 canonical ref %s" % (name, ref)
        if name.startswith("acad1-legion"):
            assert "dataset/file/acad1_evidence_bundle" in refs
        else:
            assert "dataset/file/pool_review" in refs
            assert "dataset/file/d1_decisions" in refs
            assert "dataset/file/recommendation_pool" in refs

    def test_composition_registry_reader_sees_wave1_tables(self):
        sys.path.insert(0, str(REPO / "tools"))
        try:
            import composition  # noqa: PLC0415

            registered = composition._load_pg_registry()
        finally:
            sys.path.pop(0)
        for table, owner in (("v2_acad1_review", "ACAD1"),
                             ("v2_lusu_decision", "D1")):
            assert table in registered, "composition K5 未读到 wave1 表: %s" % table
            item = registered[table]
            assert item["wave"] == "wave1"
            assert item["owner"] == owner
            assert item["registered"] is True and item["exposed"] is True

    @pytest.mark.parametrize("name", ["acad1-legion/manifest.yaml",
                                      "lusu-d1-flow/manifest.yaml"])
    def test_composition_validate_strict_no_new_errors(self, name):
        proc = subprocess.run(
            [sys.executable, "-X", "utf8",
             str(REPO / "tools" / "composition.py"), "validate",
             str(COMP / name), "--strict", "--json"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(REPO),
        )
        results = json.loads(proc.stdout)
        assert isinstance(results, list) and results, proc.stdout
        item = results[0]
        errors = list(item.get("errors") or [])
        warnings = list(item.get("warnings") or [])
        # 本单 canonical refs 不得引入任何 error/warning
        for ref in CANONICAL:
            assert not any(ref in msg for msg in errors + warnings), (
                "canonical ref 未解析：%s errors=%s" % (ref, errors))
        if name.startswith("acad1-legion"):
            assert proc.returncode == 0, "acad1-legion validate errors: %s" % errors
            assert errors == [] and warnings == []
        else:
            # lusu-d1-flow 的 file refs 在 worktree（无未跟踪 runtime data）会 pre-existing
            # 报"dataset 未在登记/路径不存在"；主仓有 runtime 数据时应为 errors=0。
            # 断言：若失败，只能是这些既有 file 载体缺失，不是本单 PG ref。
            allowed = {
                "dataset/file/recommendation_pool", "dataset/file/pool_review",
                "dataset/file/team_exec", "dataset/file/shadow_ledger",
            }
            leftover = [msg for msg in errors
                        if not any(ref in msg for ref in allowed)]
            assert leftover == [], "lusu validate 出现非既有错误: %s" % leftover
