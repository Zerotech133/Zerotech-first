# -*- coding: utf-8 -*-
"""kg_boost 适配器测试：mock parquet 与 neo4j driver，不真连库。

K-STOCK-426：打分口径从「社区峰值分位」切换为「Security 自身
pagerank 全网分位」，概念热度从 MAPS_TO 入度升级为 Concept
pagerank 分位（旧 parquet 无 Concept 行时回退入度）。
"""
import os

import pandas as pd
import pytest

from strategy_layer.adapters.kg_boost import KgBoostAdapter


# ---------------------------------------------------------------------- #
# 测试替身
# ---------------------------------------------------------------------- #
class FakeSession:
    """模拟 neo4j Session：按查询语句分发两组结果。"""

    def __init__(self, main_rows, heat_rows):
        self.main_rows = main_rows
        self.heat_rows = heat_rows

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def run(self, query):
        if "count(DISTINCT s)" in query:
            return [dict(r) for r in self.heat_rows]
        return [dict(r) for r in self.main_rows]


class FakeDriver:
    """模拟 neo4j Driver；fail=True 时 session() 直接抛错。"""

    def __init__(self, main_rows=None, heat_rows=None, fail=False):
        self.main_rows = main_rows or []
        self.heat_rows = heat_rows or []
        self.fail = fail

    def session(self):
        if self.fail:
            raise RuntimeError("neo4j 连接失败(模拟)")
        return FakeSession(self.main_rows, self.heat_rows)


def _make_parquet_dir(tmp_path, files=("kg_scores_2026-10-15.parquet",
                                       "kg_scores_2026-10-16.parquet")):
    """造假的 parquet 文件名（内容无关，读取被 mock）。"""
    d = tmp_path / "kg_analysis"
    d.mkdir(exist_ok=True)
    for name in files:
        (d / name).write_bytes(b"fake")
    return str(d / "kg_scores_*.parquet")


_SEC_NAMES = ["甲股", "乙股", "丙股", "丁股", "戊股", "贵州茅台", "某股"]


def _score_df():
    """7 只 Security + 5 个 Concept。

    Security pagerank 分位（rank pct × 100）：
      甲 14.29 / 乙 28.57 / 丙 42.86 / 丁 57.14 / 戊 71.43 /
      贵州茅台 85.71 / 某股 100
    Concept pagerank 分位：c1 20 / c2 40 / c3 60 / c4 80 / c5 100
    （高分概念 >= 60：c3/c4/c5）
    """
    return pd.DataFrame({
        "label": ["Security"] * 7 + ["Concept"] * 5,
        "name": _SEC_NAMES + ["c1", "c2", "c3", "c4", "c5"],
        "pagerank": [5.0, 10.0, 20.0, 40.0, 80.0, 90.0, 95.0]
                    + [1.0, 2.0, 3.0, 4.0, 5.0],
        "community_id": [1, 1, 2, 2, 3, 3, 3] + [9] * 5,
        "degree_kg": [0.0] * 12,
    })


def _adapter(tmp_path, driver, main_read_df=_score_df):
    return KgBoostAdapter(
        score_glob=_make_parquet_dir(tmp_path),
        driver=driver,
        password="unused",
        read_parquet=lambda path: main_read_df(),
    )


def _stock_row(ts, cid, concepts, name="某股"):
    return {"ts": ts, "name": name, "cid": cid, "concepts": concepts}


# ---------------------------------------------------------------------- #
# 分位映射（Security 自身 pagerank）
# ---------------------------------------------------------------------- #
class TestScoreMapping:
    def test_security_percentile_mapping(self, tmp_path):
        # 戊股分位 100（最高 pagerank 80 中排第 5/7？——80 为第 5 名 → 71.43；
        # 用最高/最低两端验证：某股 95→100，甲股 5→14.29
        drv = FakeDriver(main_rows=[
            _stock_row("600519.SH", 3, ["白酒"], name="某股"),
            _stock_row("000338.SZ", 1, ["潍柴动力"], name="甲股"),
        ])
        sigs = _adapter(tmp_path, drv).score("20261016")
        by_sym = {s.symbol: s for s in sigs}
        assert by_sym["600519.SH"].score == 100.0
        assert by_sym["000338.SZ"].score == pytest.approx(14.29, abs=0.02)

    def test_action_thresholds(self, tmp_path):
        # 100 → buy；42.86 → watch；14.29 → hold
        drv = FakeDriver(main_rows=[
            _stock_row("A.SH", 3, ["x"], name="某股"),
            _stock_row("B.SH", 2, ["y"], name="丙股"),
            _stock_row("C.SH", 1, ["z"], name="甲股"),
        ])
        sigs = {s.symbol: s for s in _adapter(tmp_path, drv).score("20261016")}
        assert sigs["A.SH"].action == "buy"
        assert sigs["B.SH"].action == "watch"
        assert sigs["C.SH"].action == "hold"

    def test_results_sorted_by_score_desc(self, tmp_path):
        drv = FakeDriver(main_rows=[
            _stock_row("A.SH", 1, ["x"], name="乙股"),
            _stock_row("B.SH", 3, ["y"], name="某股"),
        ])
        sigs = _adapter(tmp_path, drv).score("20261016")
        assert [s.score for s in sigs] == sorted(
            [s.score for s in sigs], reverse=True)

    def test_no_security_rows_degrades_empty(self, tmp_path):
        """parquet 无 Security 行（异常管线）→ 全量降级空列表。"""
        df = _score_df()
        df = df[df["label"] == "Concept"]
        drv = FakeDriver(main_rows=[_stock_row("A.SH", 1, ["x"])])
        assert _adapter(tmp_path, drv, main_read_df=lambda: df)\
            .score("20261016") == []


# ---------------------------------------------------------------------- #
# 置信度规则（高分概念数量，概念热度 = Concept pagerank 分位）
# ---------------------------------------------------------------------- #
class TestConfidence:
    def _sig_for(self, tmp_path, concepts):
        drv = FakeDriver(main_rows=[_stock_row("600519.SH", 3, concepts)])
        sigs = _adapter(tmp_path, drv).score("20261016")
        assert len(sigs) == 1
        return sigs[0]

    def test_zero_high_concepts(self, tmp_path):
        # c1=20, c2=40 均 < 60
        assert self._sig_for(tmp_path, ["c1", "c2"]).confidence == 0.4

    def test_one_high_concept(self, tmp_path):
        # c3=60 恰好达线
        assert self._sig_for(tmp_path, ["c1", "c3"]).confidence == 0.5

    def test_two_high_concepts(self, tmp_path):
        assert self._sig_for(tmp_path, ["c3", "c4"]).confidence == 0.65

    def test_three_plus_high_concepts(self, tmp_path):
        assert self._sig_for(tmp_path, ["c3", "c4", "c5"]).confidence == 0.8

    def test_concept_heat_from_parquet_not_indegree(self, tmp_path):
        """有 Concept 行时走 parquet 分位，不触发 MAPS_TO 入度回退。

        heat_rows 为空（回退路径若被触发会因无入度数据返回 {}，
        c5 热度变 0 → 置信度 0.4 而非 0.5），故断言 0.5 即证明
        走的是 parquet Concept pagerank 分位主路径。
        """
        drv = FakeDriver(main_rows=[_stock_row("600519.SH", 3, ["c5"])],
                         heat_rows=[])
        sig = self._sig_for(tmp_path, ["c5"])
        assert sig.confidence == 0.5  # 1 个高分概念（c5 分位 100）


# ---------------------------------------------------------------------- #
# rationale 与字段契约
# ---------------------------------------------------------------------- #
class TestRationale:
    def test_rationale_contains_concepts(self, tmp_path):
        drv = FakeDriver(main_rows=[
            _stock_row("600519.SH", 3, ["白酒概念", "超级品牌", "AI芯片"],
                       name="贵州茅台"),
        ])
        s = _adapter(tmp_path, drv).score("20261016")[0]
        assert "图谱热度" in s.rationale
        assert "pagerank分位86" in s.rationale  # 6/7 = 85.71 → :.0f = 86
        assert "白酒概念" in s.rationale
        assert s.strategy == "kg_boost"
        assert s.trade_date == "20261016"
        assert s.symbol == "600519.SH"

    def test_concepts_capped_in_rationale(self, tmp_path):
        cons = [f"概念{i}" for i in range(6)]
        drv = FakeDriver(main_rows=[_stock_row("600519.SH", 3, cons)])
        s = _adapter(tmp_path, drv).score("20261016")[0]
        assert "概念0" in s.rationale and "概念5" not in s.rationale


# ---------------------------------------------------------------------- #
# 降级路径
# ---------------------------------------------------------------------- #
class TestDegradation:
    def test_no_parquet_returns_empty(self, tmp_path, capsys):
        adapter = KgBoostAdapter(
            score_glob=str(tmp_path / "none" / "kg_scores_*.parquet"),
            driver=FakeDriver(main_rows=[_stock_row("A.SH", 1, ["x"])]),
            read_parquet=pd.read_parquet,
        )
        assert adapter.score("20261016") == []
        assert "降级" in capsys.readouterr().out

    def test_neo4j_failure_returns_empty(self, tmp_path, capsys):
        adapter = _adapter(tmp_path, FakeDriver(fail=True))
        assert adapter.score("20261016") == []
        assert "降级" in capsys.readouterr().out

    def test_empty_concepts_skipped(self, tmp_path):
        drv = FakeDriver(main_rows=[
            _stock_row("A.SH", 3, []), _stock_row("B.SH", 3, ["x"]),
        ])
        sigs = _adapter(tmp_path, drv).score("20261016")
        assert [s.symbol for s in sigs] == ["B.SH"]

    def test_unknown_stock_name_skipped(self, tmp_path):
        # parquet 无该股名（新上市/名称变动）→ 跳过不产信号
        drv = FakeDriver(main_rows=[_stock_row("A.SH", 1, ["x"],
                                               name="不存在的股")])
        assert _adapter(tmp_path, drv).score("20261016") == []

    def test_never_raises_to_caller(self, tmp_path, capsys):
        # read_parquet 抛异常也不 raise
        adapter = KgBoostAdapter(
            score_glob=_make_parquet_dir(tmp_path),
            driver=FakeDriver(),
            read_parquet=lambda path: (_ for _ in ()).throw(IOError("disk")),
        )
        assert adapter.score("20261016") == []
        assert "降级" in capsys.readouterr().out


# ---------------------------------------------------------------------- #
# parquet 选日
# ---------------------------------------------------------------------- #
class TestParquetPicking:
    def test_prefers_trade_date_file(self, tmp_path):
        glob_pat = _make_parquet_dir(tmp_path)
        seen = {}

        def fake_read(path):
            seen["path"] = path
            return _score_df()

        adapter = KgBoostAdapter(score_glob=glob_pat, driver=FakeDriver(),
                                 read_parquet=fake_read)
        adapter._load_scores("20261015")
        assert seen["path"].endswith("kg_scores_2026-10-15.parquet")

    def test_falls_back_to_latest_file(self, tmp_path):
        glob_pat = _make_parquet_dir(tmp_path)
        seen = {}

        def fake_read(path):
            seen["path"] = path
            return _score_df()

        adapter = KgBoostAdapter(score_glob=glob_pat, driver=FakeDriver(),
                                 read_parquet=fake_read)
        adapter._load_scores("20200101")
        assert seen["path"].endswith("kg_scores_2026-10-16.parquet")


# ---------------------------------------------------------------------- #
# 密码处理（绝不打印）
# ---------------------------------------------------------------------- #
class TestPasswordSafety:
    def test_missing_password_degrades_not_leaks(self, tmp_path, monkeypatch,
                                                 capsys):
        from strategy_layer.adapters import kg_boost as mod
        monkeypatch.setattr(mod, "_neo4j_password", lambda: None)
        adapter = KgBoostAdapter(
            score_glob=_make_parquet_dir(tmp_path),
            driver=None,  # 触发真实建连路径 → 密码缺失 → 降级
            read_parquet=lambda path: _score_df(),
        )
        assert adapter.score("20261016") == []
        out = capsys.readouterr().out
        assert "NEO4J_PASSWORD" not in out
        assert os.environ.get("NEO4J_PASSWORD", "") == ""
