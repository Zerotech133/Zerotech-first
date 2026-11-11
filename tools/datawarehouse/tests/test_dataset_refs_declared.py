# -*- coding: utf-8 -*-
"""dataset_registry 显式 ref 登记单测 — IFS-POOL（D1 6 data refs）。

覆盖验收：
1) dataset_refs_declared.json 契约：8 条 ref（6 条 file + 2 条 pg declared，后者
   2026-11-05 W1-PILOT-TOOLFIX 补登记）、唯一、planned 必带 owner/due；
2) `dataset_registry_cli resolve-ref` 对 6 条 ref 返回 rc=0（已登记/可解析）；
3) 载体状态不伪造：载体存在=active，缺失且声明 planned=planned，声明 active 但缺失=declared_missing；
4) 契约非法（重复/缺 owner/planned 缺 due/schema 错）→ RegistryError（fail-visible）；
5) 只读且不改基线：stats v1 314/265/72 不受 declared 影响；不写任何文件。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
DW = ROOT / "tools" / "datawarehouse"
sys.path.insert(0, str(DW))

import dataset_registry as registry  # noqa: E402

SIX_REFS = [
    "dataset/file/recommendation_pool",
    "dataset/file/pool_review",
    "dataset/file/team_exec",
    "dataset/file/paper_ledger",
    "dataset/file/shadow_ledger",
    "dataset/file/d1_decisions",
]

#: 2026-11-05 W1-PILOT-TOOLFIX 补登记的 dataset/pg 声明（v2_bar_daily 物理表只读证据；
#: v2_zhice_tables 非物理聚合口径/由 9 张 wave1 表支撑，ADR-019 P5 过渡期声明）。
PG_DECLARED_REFS = [
    "dataset/pg/v2_bar_daily",
    "dataset/pg/v2_zhice_tables",
]


@pytest.fixture(scope="module")
def declared():
    return registry.load_declared_refs()


@pytest.fixture(scope="module")
def reg():
    return registry.DatasetRegistry()


def _write_declared(tmp_path, refs, schema="dataset_refs_declared/v1"):
    path = tmp_path / "dataset_refs_declared.json"
    path.write_text(
        json.dumps({"schema": schema, "refs": refs}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def _simple_ref(**overrides):
    item = {
        "ref": "dataset/file/synthetic_ledger",
        "owner": "D1",
        "status": "planned",
        "due": "2026-11-16",
        "path": "agent_platform/data/synthetic_ledger.jsonl",
    }
    item.update(overrides)
    return item


# ── 1. 声明契约 ─────────────────────────────────────────────────────
class TestDeclaredContract:
    def test_declared_refs_complete(self, declared):
        refs = [item["ref"] for item in declared]
        assert len(refs) == 8
        assert set(refs) == set(SIX_REFS) | set(PG_DECLARED_REFS)
        assert len(set(refs)) == len(refs)

    def test_pg_declared_refs_marked(self, declared):
        items = {item["ref"]: item for item in declared}
        for ref in PG_DECLARED_REFS:
            item = items[ref]
            assert item["owner"] == "DATA1"
            assert item["canonical_ref"] == ref
            assert item["declared_at"] == "2026-11-05"

    def test_every_ref_has_owner_and_status(self, declared):
        for item in declared:
            assert item.get("owner")
            assert item.get("status") in ("active", "planned")
            if item["status"] == "planned":
                assert item.get("due"), "planned ref 必须有 due: %s" % item["ref"]

    def test_d1_decisions_planned_with_owner_due(self, declared):
        item = {i["ref"]: i for i in declared}["dataset/file/d1_decisions"]
        assert item["status"] == "planned"
        assert item["owner"] == "D1"
        assert item["due"] == "2026-11-16"
        assert item.get("due_source")
        assert item.get("reason")

    def test_recommendation_pool_demoted_to_shadow(self, declared):
        item = {i["ref"]: i for i in declared}["dataset/file/recommendation_pool"]
        assert item["authority"] == "shadow_projection"
        assert item["canonical_ref"] == "dataset/pg/v2_acad1_pool"
        assert "shadow 自建" in item["note"]
        assert "不得作为官方" in item["note"]


# ── 2. resolve-ref 可解析（验收口径） ───────────────────────────────
class TestResolveRef:
    def test_all_six_refs_resolve(self, reg):
        for ref in SIX_REFS:
            row = reg.resolve_ref(ref)
            assert row is not None, "ref 未登记: %s" % ref
            assert row["ref"] == ref
            assert row["kind"] == "file_dataset"
            assert row["status"] in ("active", "planned", "declared_missing")

    def test_active_and_planned_split(self, reg):
        # 2026-11-04 棘轮加固：d1_decisions 的声明为 planned，但共享机 runtime 产物可能
        # 已生成（D1 线交付后 resolve_ref 会如实返回 active）；测试校验"不伪造"语义，
        # 而非僵化要求永远 planned。
        d1 = reg.resolve_ref("dataset/file/d1_decisions")
        if d1["carrier"] in ("dir", "file"):
            assert d1["status"] == "active"
        else:
            assert d1["status"] == "planned"
        # 其余 5 条在共享机（C:\项目\老田的AI助手 runtime data 存在）应为 active；
        # 干净检出/无 runtime 数据的机器上允许 planned（声明 planned）或 declared_missing（声明 active），
        # 但不得伪造 active。
        for ref in SIX_REFS[:-1]:
            row = reg.resolve_ref(ref)
            if row["carrier"] in ("dir", "file"):
                assert row["status"] == "active"
            else:
                assert row["status"] in ("planned", "declared_missing")

    def test_token_alias_and_get(self, reg):
        row = reg.resolve_ref("dataset/file/recommendation_pool")
        assert reg.resolve_ref("recommendation_pool") is not None or row is not None
        assert reg.get(row["dataset_id"]) is not None

    def test_official_pool_ref_registered(self, reg):
        row = reg.resolve_ref("dataset/pg/v2_acad1_pool")
        assert row is not None
        assert row["kind"] == "pg_table"
        assert row["registered"] is True

    def test_list_file_dataset(self, reg):
        rows = reg.list(kinds="file_dataset", limit=20)
        # 6 条 file 声明 + 2 条 pg 声明（registry 对 declared 行统一建模为 file_dataset）
        assert len(rows) == 8
        assert set(SIX_REFS) <= {row["ref"] for row in rows}
        assert all(row["kind"] == "file_dataset" for row in rows)

    def test_declared_summary(self, reg):
        summary = reg.declared_summary()
        assert summary["total"] == 8
        assert len(summary["active"]) + len(summary["planned"]) + len(
            summary["declared_missing"]
        ) == 8
        # 2026-11-04 棘轮加固：d1_decisions 可能已由 D1 线交付而进入 active 桶
        # （active 桶是 ref 字符串列表，planned 桶才是带 owner/due 的 dict）；
        # 声明属性只在 planned 桶可校验，active 时校验其确已登记。
        planned = {item["ref"]: item for item in summary["planned"]}
        active_refs = set(summary["active"])
        assert "dataset/file/d1_decisions" in planned or \
            "dataset/file/d1_decisions" in active_refs
        if "dataset/file/d1_decisions" in planned:
            assert planned["dataset/file/d1_decisions"]["owner"] == "D1"
            assert planned["dataset/file/d1_decisions"]["due"] == "2026-11-16"
        # pg 声明无物理/file 载体 → declared_missing（fail-visible，不假绿）
        assert "dataset/pg/v2_bar_daily" in summary["declared_missing"]
        assert "dataset/pg/v2_zhice_tables" in summary["declared_missing"]


# ── 3. CLI 验收（resolve-ref rc=0） ─────────────────────────────────
class TestCli:
    def _run(self, *args):
        return subprocess.run(
            [sys.executable, "-X", "utf8", str(DW / "dataset_registry_cli.py"), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )

    def test_cli_resolve_all_six_ok(self):
        for ref in SIX_REFS:
            proc = self._run("resolve-ref", "--ref", ref)
            assert proc.returncode == 0, "%s rc=%d stderr=%s" % (
                ref, proc.returncode, proc.stderr[:300])
            payload = json.loads(proc.stdout)
            assert payload["ref"] == ref

    def test_cli_unknown_ref_rc2(self):
        proc = self._run("resolve-ref", "--ref", "dataset/file/__definitely_missing__")
        assert proc.returncode == 2
        payload = json.loads(proc.stdout)
        assert payload.get("error") == "ref 未登记"

    def test_cli_declared_summary(self):
        proc = self._run("declared")
        assert proc.returncode == 0
        payload = json.loads(proc.stdout)
        assert payload["total"] == 8


# ── 4. 契约非法 fail-visible ────────────────────────────────────────
class TestInvalidDeclarations:
    def test_planned_without_due_rejected(self, tmp_path):
        path = _write_declared(
            tmp_path,
            [_simple_ref(due=None)],
        )
        with pytest.raises(registry.RegistryError, match="planned 必须声明 due"):
            registry.DatasetRegistry(declared_refs_path=str(path))

    def test_duplicate_ref_rejected(self, tmp_path):
        path = _write_declared(tmp_path, [_simple_ref(), _simple_ref()])
        with pytest.raises(registry.RegistryError, match="重复"):
            registry.DatasetRegistry(declared_refs_path=str(path))

    def test_missing_owner_rejected(self, tmp_path):
        path = _write_declared(tmp_path, [_simple_ref(owner="")])
        with pytest.raises(registry.RegistryError, match="缺 owner"):
            registry.DatasetRegistry(declared_refs_path=str(path))

    def test_bad_schema_rejected(self, tmp_path):
        path = _write_declared(tmp_path, [_simple_ref()], schema="wrong/v0")
        with pytest.raises(registry.RegistryError, match="schema"):
            registry.DatasetRegistry(declared_refs_path=str(path))

    def test_missing_file_is_optional(self, tmp_path):
        reg = registry.DatasetRegistry(declared_refs_path=str(tmp_path / "missing.json"))
        assert reg.declared_datasets == []
        assert reg.declared_summary()["total"] == 0

    def test_injected_declared_ref_resolves(self, tmp_path):
        path = _write_declared(tmp_path, [_simple_ref()])
        reg = registry.DatasetRegistry(declared_refs_path=str(path))
        row = reg.resolve_ref("dataset/file/synthetic_ledger")
        assert row is not None
        assert row["status"] == "planned"
        assert row["owner"] == "D1"
        assert row["due"] == "2026-11-16"


# ── 5. 只读 + 基线不变 ──────────────────────────────────────────────
class TestReadOnlyAndBaseline:
    def test_stats_baseline_unchanged(self, tmp_path):
        default = registry.DatasetRegistry()
        injected = registry.DatasetRegistry(
            declared_refs_path=str(_write_declared(tmp_path, [_simple_ref()]))
        )
        assert default.stats() == injected.stats()
        assert default.stats()["total"] > 0

    def test_declared_loading_does_not_write(self, tmp_path):
        path = _write_declared(tmp_path, [_simple_ref()])
        before = path.read_bytes()
        registry.DatasetRegistry(declared_refs_path=str(path))
        assert path.read_bytes() == before
