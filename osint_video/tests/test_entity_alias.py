"""实体别名归一（P1-B）的单元测试。

覆盖四层：
1. 查表与映射（``load_alias_map`` / ``resolve`` / ``canonicalize``）
2. 二义别名的拒绝（读表冲突剔除 + 回填侧冲突检测 + DDL 约束）
3. 回填的确定性规则（全半角、大小写、法定后缀、括号缩写）
4. 回填的 dry-run / 幂等 / 退出码

全部用例不依赖 PostgreSQL：查表与写库分别用 fake conn / monkeypatch 注入。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from knowledge import entity_alias as ea
from scripts import backfill_entity_alias as bf

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "store" / "schema.sql"


# ------------------------------------------------------------ fake 设施 --


class FakeConn:
    """最小 duck-typed 连接：只实现 execute(sql) → 行序列。"""

    def __init__(self, rows: list) -> None:
        self.rows = rows
        self.sqls: list[str] = []

    def execute(self, sql):  # noqa: ANN001 - 模拟驱动签名
        self.sqls.append(sql)
        return list(self.rows)


def _patch_backfill_db(monkeypatch, units, existing=None):
    """替换 backfill 依赖的 db.fetch_all / db.execute_many。"""
    writes: list[dict] = []

    def fetch_all(sql, params=None):
        if "FROM osint_entity_alias" in sql:
            return existing or []
        return units

    def execute_many(sql, seq_of_params):
        writes.append({"sql": sql, "params": list(seq_of_params)})
        return len(seq_of_params)

    monkeypatch.setattr(bf.db, "fetch_all", fetch_all)
    monkeypatch.setattr(bf.db, "execute_many", execute_many)
    return writes


# -------------------------------------------------------------- 查表映射 --


class TestLoadAliasMap:
    def test_reads_rows_and_normalizes_keys(self):
        conn = FakeConn(
            [
                {"alias": "CATL", "canonical_id": "catl"},
                {"alias": "宁德时代", "canonical_id": "catl"},
                {"alias": "", "canonical_id": "ignored"},
            ]
        )
        alias_map = ea.load_alias_map(conn)

        assert alias_map["catl"] == "catl"
        assert alias_map["宁德时代"] == "catl"
        assert "" not in alias_map

    def test_supports_tuple_rows_from_psycopg(self):
        alias_map = ea.load_alias_map(FakeConn([("CATL", "catl")]))
        assert alias_map == {"catl": "catl"}

    def test_conflicting_alias_is_dropped_entirely(self):
        """同一别名指向两个 canonical → 该别名整条剔除，不许任选其一。"""
        conn = FakeConn(
            [
                {"alias": "苹果", "canonical_id": "apple_inc"},
                {"alias": "苹果", "canonical_id": "apple_fruit"},
            ]
        )
        alias_map = ea.load_alias_map(conn)

        assert "苹果" not in alias_map
        # 漏归一可以接受：键原样保留，事件只是没合并，不会被错误合并
        assert ea.resolve({"苹果"}, alias_map) == {"苹果"}


class TestResolve:
    def test_maps_alias_to_canonical(self):
        alias_map = {"catl": "catl", "宁德时代": "catl", "宁德时代新能源": "catl"}
        assert ea.resolve({"宁德时代新能源", "catl"}, alias_map) == {"catl"}

    def test_unmatched_keys_are_kept(self):
        # 没有别名不等于实体不存在；丢掉会降低召回，是另一种失真
        assert ea.resolve({"某未登记公司"}, {"catl": "catl"}) == {"某未登记公司"}

    def test_is_idempotent(self):
        alias_map = {"catl": "catl", "宁德时代": "catl"}
        once = ea.resolve({"宁德时代", "比亚迪"}, alias_map)
        assert ea.resolve(once, alias_map) == once

    def test_empty_key_is_dropped(self):
        assert ea.resolve({"", "   "}, {}) == set()

    def test_fullwidth_lookup_hits_ascii_alias(self):
        # normalize_alias 先做 NFKC，全角字母必须能命中半角别名
        alias_map = {"catl": "catl"}
        assert ea.resolve({"ＣＡＴＬ"}, alias_map) == {"catl"}


class TestCanonicalize:
    def test_mixed_entity_shapes_converge_and_dedupe(self):
        alias_map = {"catl": "catl", "宁德时代": "catl"}
        entities = ["宁德时代", {"name": "CATL"}, "宁德时代"]
        assert ea.canonicalize(entities, alias_map) == ["catl"]

    def test_unknown_entity_survives(self):
        assert ea.canonicalize(["比亚迪"], {}) == ["比亚迪"]


class TestAliasTypes:
    def test_all_schema_types_are_valid(self):
        assert ea.ALIAS_TYPES == (
            "abbr",
            "en",
            "cn_full",
            "former_name",
            "product",
            "variant",
        )
        for alias_type in ea.ALIAS_TYPES:
            assert ea.is_valid_alias_type(alias_type)

    def test_unknown_type_is_rejected(self):
        assert not ea.is_valid_alias_type("guess")
        assert not ea.is_valid_alias_type(None)


# ---------------------------------------------------------- 二义别名约束 --


class TestAmbiguityRejection:
    def test_check_alias_conflicts_flags_two_canonicals(self):
        conflicts = ea.check_alias_conflicts(
            [("苹果", "apple_inc"), ("苹果", "apple_fruit"), ("CATL", "catl")]
        )
        assert conflicts == {"苹果": ["apple_inc", "apple_fruit"]}

    def test_same_alias_same_canonical_is_not_conflict(self):
        assert ea.check_alias_conflicts([("CATL", "catl"), ("catl", "catl")]) == {}

    def test_schema_enforces_unique_alias_not_unique_pair(self):
        """DDL 必须是 UNIQUE (alias)：允许 (alias, canonical_id) 组合唯一就会二义。"""
        sql = SCHEMA_PATH.read_text(encoding="utf-8")
        block = re.search(
            r"CREATE TABLE IF NOT EXISTS osint_entity_alias \((?P<body>.*?)\n\);",
            sql,
            re.S,
        )
        assert block is not None, "schema.sql 缺少 osint_entity_alias 建表语句"
        body = block.group("body")

        assert re.search(r"UNIQUE \(alias\)", body)
        assert "UNIQUE (alias, canonical_id)" not in body
        assert "CHECK (alias_type IN" in body
        assert "CHECK (source IN" in body


# ------------------------------------------------------------ 确定性规则 --


class TestLegalSuffixStripping:
    @pytest.mark.parametrize(
        ("left", "right"),
        [
            ("宁德时代新能源有限公司", "宁德时代新能源股份有限公司"),
            ("比亚迪股份有限公司", "比亚迪"),
            ("Contemporary Amperex Technology Co., Ltd.", "Contemporary Amperex Technology Ltd"),
            ("Apple Inc.", "Apple"),
        ],
    )
    def test_suffix_variants_share_one_canonical(self, left, right):
        aliases, _ = bf.derive_aliases([left, right])
        canonicals = {row["canonical_id"] for row in aliases}
        assert len(canonicals) == 1, f"{left} / {right} 应归一到同一 canonical"

    def test_strip_keeps_rest_of_name_intact(self):
        assert bf.strip_legal_suffix("宁德时代新能源科技股份有限公司") == "宁德时代新能源科技"
        assert bf.strip_legal_suffix("宁德时代") == "宁德时代"


class TestBracketPairing:
    def test_chinese_full_name_pairs_with_inner_abbr(self):
        aliases, _ = bf.derive_aliases(["宁德时代新能源科技股份有限公司（CATL）", "CATL"])
        canonical_of = {row["alias"]: row["canonical_id"] for row in aliases}

        assert canonical_of["宁德时代新能源科技股份有限公司（CATL）"] == "catl"
        assert canonical_of["CATL"] == "catl"

    def test_reverse_bracket_form_is_supported(self):
        aliases, _ = bf.derive_aliases(["CATL（宁德时代）", "宁德时代"])
        canonical_of = {row["alias"]: row["canonical_id"] for row in aliases}
        assert canonical_of["CATL（宁德时代）"] == "catl"

    def test_conflicting_abbr_across_companies_is_not_paired(self):
        """同一缩写对应两家公司时拒绝配对——否则会把两家合并成一家。"""
        aliases, candidates = bf.derive_aliases(["甲公司（CATL）", "乙公司（CATL）"])

        assert all(row["canonical_id"] != "catl" for row in aliases)
        assert any(item["reason"] == "ambiguous_abbr" for item in candidates)


class TestNonDeterministicGoesToCandidates:
    def test_prefix_related_names_are_not_written(self):
        """宁德时代 vs 宁德时代新能源：看起来像，但没有确定性证据，只能人工确认。"""
        aliases, candidates = bf.derive_aliases(["宁德时代", "宁德时代新能源"])

        assert aliases == []
        kinds = {item["reason"] for item in candidates}
        assert "substring" in kinds


# ------------------------------------------------------------ 回填流程 --


def _units(*entity_lists):
    return [{"entities": json.dumps(list(items), ensure_ascii=False)} for items in entity_lists]


class TestBackfillRun:
    def test_dry_run_writes_nothing(self, monkeypatch):
        writes = _patch_backfill_db(
            monkeypatch,
            _units(["宁德时代新能源有限公司", "宁德时代新能源股份有限公司"]),
        )
        report = bf.run(dry_run=True)

        assert report["dry_run"] is True
        assert report["aliases_planned"] == 2
        assert report["aliases_inserted"] == 0
        assert writes == []

    def test_write_mode_uses_on_conflict_do_nothing(self, monkeypatch):
        writes = _patch_backfill_db(
            monkeypatch,
            _units(["宁德时代新能源有限公司", "宁德时代新能源股份有限公司"]),
        )
        report = bf.run(dry_run=False)

        assert report["aliases_inserted"] == 2
        assert len(writes) == 1
        assert "ON CONFLICT (alias) DO NOTHING" in writes[0]["sql"]
        assert writes[0]["params"][0]["confidence"] == bf.BACKFILL_CONFIDENCE

    def test_existing_aliases_are_skipped(self, monkeypatch):
        _patch_backfill_db(
            monkeypatch,
            _units(["宁德时代新能源有限公司", "宁德时代新能源股份有限公司"]),
            existing=[{"alias": "宁德时代新能源有限公司"}],
        )
        report = bf.run(dry_run=True)

        assert report["aliases_planned"] == 1
        assert report["aliases_skipped_existing"] == 1

    def test_rerun_after_write_is_idempotent(self, monkeypatch):
        """第二次运行时已写入的别名会被跳过（配合 ON CONFLICT 双重保底）。"""
        _patch_backfill_db(
            monkeypatch,
            _units(["宁德时代新能源有限公司", "宁德时代新能源股份有限公司"]),
            existing=[{"alias": "宁德时代新能源有限公司"}, {"alias": "宁德时代新能源股份有限公司"}],
        )
        report = bf.run(dry_run=False)
        assert report["aliases_inserted"] == 0


class TestBackfillCli:
    def test_default_is_dry_run_and_emits_json(self, monkeypatch, capsys):
        _patch_backfill_db(
            monkeypatch,
            _units(["宁德时代新能源有限公司", "宁德时代新能源股份有限公司"]),
        )
        code = bf.main(["--json"])
        payload = json.loads(capsys.readouterr().out)

        assert code == 0
        assert payload["dry_run"] is True
        assert payload["aliases_inserted"] == 0

    def test_exception_returns_exit_code_one(self, monkeypatch, capsys):
        def boom(sql, params=None):
            raise RuntimeError("pg down")

        monkeypatch.setattr(bf.db, "fetch_all", boom)
        code = bf.main(["--json"])
        payload = json.loads(capsys.readouterr().out)

        assert code == 1
        assert payload["ok"] is False
