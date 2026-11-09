# -*- coding: utf-8 -*-
"""H3 strategy 测试：StrategySpec v1 校验/编译/描述/策略库（离线临时 ANALYSIS_WORK）。

契约：`C:\\项目\\deliverables\\2026-11-04_H3策略与回测口径规范_v1.md`（§1.4 A/B/C golden）
+ Wave B 评审返工（规范 v1.1）：禁静默单位归一、spec_hash 稳定、原子写/并发锁、
validate 业务失败保持 200。
"""
import copy
import json
import os
import re
import sys
import tempfile
import threading
import time
import unittest

WT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(WT))
sys.path.insert(0, WT)

from analysis import strategy as st  # noqa: E402


class FakeScreener:
    """假 H2a 引擎（本分支 screener 未合并时测试用；FIELDS 与 H2a 一致）。"""
    FIELDS = {
        "ps": {"kind": "number", "label": "PS(TTM)"},
        "ps_pct_hist": {"kind": "number", "label": "PS 历史分位", "fmt": "%"},
        "pe_ttm": {"kind": "number", "label": "PE(TTM)"},
        "dv_ratio": {"kind": "number", "label": "股息率", "fmt": "%"},
        "total_mv_yi": {"kind": "number", "label": "总市值(亿)"},
        "revenue_yoy": {"kind": "number", "label": "营收同比", "fmt": "%"},
        "net_profit_yoy": {"kind": "number", "label": "净利同比", "fmt": "%"},
        "roe": {"kind": "number", "label": "ROE", "fmt": "%"},
        "debt_ratio": {"kind": "number", "label": "资产负债率", "fmt": "%"},
        "ret_20": {"kind": "number", "label": "20日涨跌", "fmt": "%"},
        "ret_60": {"kind": "number", "label": "60日涨跌", "fmt": "%"},
        "amount_20_yi": {"kind": "number", "label": "20日均额(亿)"},
        "turnover_rate": {"kind": "number", "label": "换手率", "fmt": "%"},
        "quality_score": {"kind": "number", "label": "质量分"},
        "industry_l1": {"kind": "string", "label": "行业(L1)"},
        "industry_l2": {"kind": "string", "label": "行业(L2)"},
        "flag_st": {"kind": "bool", "label": "ST"},
        "event_types": {"kind": "string", "label": "事件类别"},
    }
    UNIVERSES = ("A_SHARE", "HS300", "WATCHLIST")
    NUM_OPS = ("=", "!=", ">", ">=", "<", "<=")
    STR_OPS = ("in", "notin", "contains", "contains_any")
    BOOL_OPS = ("=", "!=")
    OPS = NUM_OPS + STR_OPS + BOOL_OPS
    calls = []

    @staticmethod
    def validate(spec):
        FakeScreener.calls.append(dict(spec))
        return {"ok": True, "errors": [], "spec": spec}


# 规范 §1.4 三个标准示例（golden fixtures）
GOLDEN_A = {
    "schema_version": "v1",
    "name": "低估值高质量",
    "universe": "HS300",
    "filters": [
        {"field": "ps_pct_hist", "op": "<=", "value": 30},
        {"field": "roe", "op": ">=", "value": 0.12},
        {"field": "debt_ratio", "op": "<=", "value": 0.6},
        {"field": "flag_st", "op": "=", "value": False},
    ],
    "rank_by": [{"field": "ps_pct_hist", "order": "asc"}],
    "limit": 20,
    "rebalance": "monthly",
    "cost_bps": 20,
    "notes": "研究用：低估值+高质量，HS300 月度调仓；不构成投资建议",
}
GOLDEN_B = {
    "schema_version": "v1",
    "name": "事件驱动-业绩改善",
    "universe": "A_SHARE",
    "filters": [
        {"field": "event_types", "op": "contains_any", "value": ["业绩", "并购", "股权"]},
        {"field": "net_profit_yoy", "op": ">=", "value": 0.30},
        {"field": "amount_20_yi", "op": ">=", "value": 0.5},
    ],
    "rank_by": [
        {"field": "net_profit_yoy", "order": "desc"},
        {"field": "total_mv_yi", "order": "asc"},
    ],
    "limit": 30,
    "rebalance": "weekly",
    "cost_bps": 30,
    "notes": "研究用：事件类别依赖已分析股票的事件库覆盖；高换手下成本敏感",
}
GOLDEN_C = {
    "schema_version": "v1",
    "name": "趋势突破-强势动量",
    "universe": "A_SHARE",
    "filters": [
        {"field": "ret_20", "op": ">=", "value": 0.08},
        {"field": "ret_60", "op": ">=", "value": 0.15},
        {"field": "turnover_rate", "op": ">=", "value": 2.0},
        {"field": "amount_20_yi", "op": ">=", "value": 1.0},
        {"field": "flag_st", "op": "=", "value": False},
    ],
    "rank_by": [{"field": "ret_60", "order": "desc"}],
    "limit": 20,
    "rebalance": "daily",
    "cost_bps": 40,
    "notes": "研究用：日频调仓对成本/滑点极敏感；flag_st 非 PIT，回测标 partial",
}


def _spec(**over):
    s = copy.deepcopy(GOLDEN_A)
    s.update(over)
    return s


def _minimal(**over):
    s = {"name": "最小策略", "universe": "WATCHLIST",
         "filters": [{"field": "roe", "op": ">=", "value": 0.1}]}
    s.update(over)
    return s


class _Base(unittest.TestCase):
    def setUp(self):
        self._orig = st._load_screener
        st._load_screener = lambda: FakeScreener
        FakeScreener.calls = []

    def tearDown(self):
        st._load_screener = self._orig


class TestValidateGolden(_Base):
    def test_golden_a_ok(self):
        v = st.validate(copy.deepcopy(GOLDEN_A))
        self.assertTrue(v["ok"], v["errors"])
        n = v["normalized"]
        self.assertEqual(n["schema_version"], "v1")
        self.assertEqual(n["universe"], "HS300")
        self.assertEqual(n["limit"], 20)
        self.assertEqual(n["rebalance"], "monthly")
        self.assertEqual(n["cost_bps"], 20)
        self.assertEqual(n["filters"][0]["value"], 30)       # 百分数字段不换算
        self.assertEqual(n["filters"][1]["value"], 0.12)     # 小数字段不换算
        self.assertRegex(n["as_of"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertTrue(any("flag_st" in w and "非 PIT" in w for w in v["warnings"]))

    def test_golden_b_ok(self):
        v = st.validate(copy.deepcopy(GOLDEN_B))
        self.assertTrue(v["ok"], v["errors"])
        n = v["normalized"]
        self.assertEqual(n["universe"], "A_SHARE")
        self.assertEqual(n["limit"], 30)
        self.assertEqual(n["rebalance"], "weekly")
        self.assertEqual(n["cost_bps"], 30)
        self.assertEqual(len(n["rank_by"]), 2)
        self.assertTrue(any("幸存者偏差" in w for w in v["warnings"]))
        self.assertTrue(any("event_types" in w for w in v["warnings"]))

    def test_golden_c_ok(self):
        v = st.validate(copy.deepcopy(GOLDEN_C))
        self.assertTrue(v["ok"], v["errors"])
        n = v["normalized"]
        self.assertEqual(n["rebalance"], "daily")
        self.assertEqual(n["cost_bps"], 40)
        self.assertEqual(n["filters"][0]["value"], 0.08)
        self.assertEqual(n["filters"][2]["value"], 2.0)

    def test_negative_broken_field(self):
        broken = _spec(filters=[{"field": "ps_pct_hiist", "op": "<=", "value": 30}])
        v = st.validate(broken)
        self.assertFalse(v["ok"])
        self.assertTrue(any("未知字段" in e for e in v["errors"]))

    def test_negative_unknown_top_level(self):
        v = st.validate(_spec(owner="research"))
        self.assertFalse(v["ok"])
        self.assertTrue(any("未知顶层字段" in e for e in v["errors"]))


class TestValidateRules(_Base):
    def test_defaults(self):
        v = st.validate(_minimal())
        self.assertTrue(v["ok"], v["errors"])
        n = v["normalized"]
        self.assertEqual(n["schema_version"], "v1")
        self.assertEqual(n["limit"], 20)
        self.assertEqual(n["rebalance"], "monthly")
        self.assertEqual(n["cost_bps"], 20)
        self.assertEqual(n["notes"], "")
        self.assertEqual(n["rank_by"], [])
        self.assertRegex(n["as_of"], r"^\d{4}-\d{2}-\d{2}$")

    def test_missing_name_and_long_name(self):
        v = st.validate({"filters": [{"field": "roe", "op": ">=", "value": 0.1}]})
        self.assertFalse(v["ok"])
        self.assertTrue(any("name" in e for e in v["errors"]))
        v2 = st.validate(_spec(name="x" * 41))
        self.assertFalse(v2["ok"])
        self.assertTrue(any("过长" in e for e in v2["errors"]))

    def test_universe_alias_and_invalid(self):
        self.assertEqual(st.validate(_spec(universe="沪深300"))["normalized"]["universe"], "HS300")
        self.assertEqual(st.validate(_spec(universe="全A"))["normalized"]["universe"], "A_SHARE")
        v = st.validate(_spec(universe="NASDAQ"))
        self.assertFalse(v["ok"])
        self.assertTrue(any("universe" in e for e in v["errors"]))
        self.assertTrue(st.validate(_spec(universe="industry:电子"))["ok"])

    def test_filters_shape_limits(self):
        self.assertFalse(st.validate(_spec(filters=[]))["ok"])
        self.assertFalse(st.validate(_spec(filters="bad"))["ok"])
        many = [{"field": "roe", "op": ">=", "value": 0.1} for _ in range(21)]
        v = st.validate(_spec(filters=many))
        self.assertFalse(v["ok"])
        self.assertTrue(any("最多" in e for e in v["errors"]))
        self.assertFalse(st.validate(_spec(filters=["bad"]))["ok"])

    def test_op_kind_mismatch(self):
        v = st.validate(_spec(filters=[{"field": "flag_st", "op": ">", "value": True}]))
        self.assertFalse(v["ok"])
        self.assertTrue(any("不支持算子" in e for e in v["errors"]))
        v2 = st.validate(_spec(filters=[{"field": "roe", "op": "contains", "value": [1]}]))
        self.assertFalse(v2["ok"])
        v3 = st.validate(_spec(filters=[{"field": "industry_l1", "op": "=", "value": "电子"}]))
        self.assertFalse(v3["ok"])

    def test_value_types(self):
        self.assertFalse(st.validate(_spec(filters=[{"field": "roe", "op": ">=", "value": "0.1"}]))["ok"])
        self.assertFalse(st.validate(_spec(filters=[{"field": "flag_st", "op": "=", "value": "false"}]))["ok"])
        self.assertFalse(st.validate(_spec(filters=[{"field": "event_types", "op": "in", "value": "业绩"}]))["ok"])
        nan_v = st.validate(_spec(filters=[{"field": "roe", "op": ">=", "value": float("nan")}]))
        self.assertFalse(nan_v["ok"])

    def test_unit_no_silent_rewrite_four_samples(self):
        """P1 返工：4 个评审误改样例必须保留原值（仅 warning）。"""
        cases = [("ps_pct_hist", 0.8), ("turnover_rate", 0.5),
                 ("revenue_yoy", 2.5), ("ret_20", 3.0)]
        for field, raw in cases:
            v = st.validate(_spec(filters=[{"field": field, "op": "<=", "value": raw}]))
            self.assertTrue(v["ok"], (field, v["errors"]))
            self.assertEqual(v["normalized"]["filters"][0]["value"], raw, field)
            self.assertTrue(any("保留原值" in w for w in v["warnings"]), field)

    def test_unit_warning_only_for_suspect_scale(self):
        v = st.validate(_spec(filters=[{"field": "ps_pct_hist", "op": "<=", "value": 30}]))
        self.assertTrue(v["ok"])
        self.assertFalse(any("疑似" in w for w in v["warnings"]))
        v2 = st.validate(_spec(filters=[{"field": "dv_ratio", "op": "<=", "value": 150}]))
        self.assertTrue(v2["ok"])
        self.assertTrue(any("超出权威单位范围" in w for w in v2["warnings"]))

    def test_explicit_unit_pct_converts_only_when_declared(self):
        spec = _spec(filters=[{"field": "roe", "op": ">=", "value": 12, "unit": "pct"}])
        v = st.validate(spec)
        self.assertTrue(v["ok"], v["errors"])
        nf = v["normalized"]["filters"][0]
        self.assertEqual(nf["value"], 0.12)
        self.assertEqual(nf["unit"], "pct")
        self.assertTrue(any("显式 unit=pct" in w for w in v["warnings"]))
        comp = st.compile(spec)
        self.assertEqual(comp["screener_spec"]["filters"][0]["value"], 0.12)
        self.assertNotIn("unit", comp["screener_spec"]["filters"][0])

    def test_explicit_unit_ratio_converts_percent_field(self):
        v = st.validate(_spec(filters=[{"field": "ps_pct_hist", "op": "<=",
                                        "value": 0.25, "unit": "ratio"}]))
        self.assertTrue(v["ok"], v["errors"])
        self.assertEqual(v["normalized"]["filters"][0]["value"], 25.0)

    def test_explicit_unit_invalid(self):
        v = st.validate(_spec(filters=[{"field": "ps", "op": "<=", "value": 0.2, "unit": "pct"}]))
        self.assertFalse(v["ok"])
        self.assertTrue(any("unit" in e for e in v["errors"]))
        v2 = st.validate(_spec(filters=[{"field": "roe", "op": ">=", "value": 0.1,
                                         "unit": "foo"}]))
        self.assertFalse(v2["ok"])
        v3 = st.validate(_spec(filters=[{"field": "flag_st", "op": "=", "value": False,
                                         "unit": "pct"}]))
        self.assertFalse(v3["ok"])

    def test_rank_by_rules(self):
        v = st.validate(_spec(rank_by=[{"field": "quality_score", "order": "desc"}] * 4))
        self.assertFalse(v["ok"])
        v2 = st.validate(_spec(rank_by=[{"field": "industry_l1", "order": "desc"}]))
        self.assertFalse(v2["ok"])
        v3 = st.validate(_spec(rank_by=[{"field": "ps_pct_hist", "order": "up"}]))
        self.assertFalse(v3["ok"])
        self.assertTrue(any("asc/desc" in e for e in v3["errors"]))

    def test_limit_clamp_and_type(self):
        v = st.validate(_spec(limit=0))
        self.assertTrue(v["ok"], v["errors"])
        self.assertEqual(v["normalized"]["limit"], 1)
        self.assertTrue(any("clamp" in w for w in v["warnings"]))
        v2 = st.validate(_spec(limit=999))
        self.assertEqual(v2["normalized"]["limit"], 200)
        self.assertTrue(any("clamp" in w for w in v2["warnings"]))
        v3 = st.validate(_spec(limit="20"))
        self.assertFalse(v3["ok"])
        self.assertTrue(any("limit" in e for e in v3["errors"]))

    def test_cost_bps_rules(self):
        self.assertEqual(st.validate(_minimal())["normalized"]["cost_bps"], 20)  # D2 默认
        for bad in (20.5, 101, -1, "20", True):
            v = st.validate(_spec(cost_bps=bad))
            self.assertFalse(v["ok"], bad)
            self.assertTrue(any("cost_bps" in e for e in v["errors"]), bad)

    def test_rebalance_rules(self):
        self.assertEqual(st.validate(_minimal())["normalized"]["rebalance"], "monthly")
        v = st.validate(_spec(rebalance="hourly"))
        self.assertFalse(v["ok"])
        self.assertTrue(any("rebalance" in e for e in v["errors"]))

    def test_as_of_rules(self):
        v = st.validate(_spec(as_of="2026/11/04"))
        self.assertFalse(v["ok"])
        self.assertTrue(any("as_of" in e for e in v["errors"]))
        v2 = st.validate(_spec(as_of="2026-11-07"))  # 周六
        self.assertTrue(v2["ok"], v2["errors"])
        self.assertTrue(any("非交易日" in w for w in v2["warnings"]))
        v3 = st.validate(_spec(as_of="2026-11-04"))
        self.assertEqual(v3["normalized"]["as_of"], "2026-11-04")

    def test_as_of_explicit_flag(self):
        """P2：validate 暴露 as_of_explicit 供 spec_hash 决定是否计入 as_of。"""
        self.assertFalse(st.validate(_minimal())["as_of_explicit"])
        self.assertTrue(st.validate(_spec(as_of="2026-11-04"))["as_of_explicit"])

    def test_business_validation_failure_semantics(self):
        """规范 v1.1：业务校验失败 = ok:false + errors（HTTP 200 由代理层保持）。"""
        v = st.validate({"name": ""})
        self.assertFalse(v["ok"])
        self.assertTrue(v["errors"])
        self.assertIn("normalized", v)
        self.assertIn("warnings", v)

    def test_schema_version(self):
        self.assertEqual(st.validate(_minimal())["normalized"]["schema_version"], "v1")
        v = st.validate(_spec(schema_version="v2"))
        self.assertFalse(v["ok"])
        self.assertTrue(any("schema_version" in e for e in v["errors"]))

    def test_notes_rules(self):
        v = st.validate(_spec(notes="x" * 201))
        self.assertFalse(v["ok"])
        self.assertTrue(any("notes" in e for e in v["errors"]))
        self.assertFalse(st.validate(_spec(notes=123))["ok"])

    def test_engine_missing(self):
        st._load_screener = lambda: None
        v = st.validate(copy.deepcopy(GOLDEN_A))
        self.assertFalse(v["ok"])
        self.assertEqual(v["errors"], [st.ENGINE_MISSING])

    def test_non_dict_spec(self):
        self.assertFalse(st.validate([])["ok"])


class TestCompileDescribe(_Base):
    def test_compile_golden_a(self):
        comp = st.compile(copy.deepcopy(GOLDEN_A))
        self.assertTrue(comp["ok"], comp.get("errors"))
        self.assertEqual(comp["schema_version"], "v1")
        ss = comp["screener_spec"]
        self.assertEqual(ss["universe"], "HS300")
        self.assertEqual(len(ss["filters"]), 4)
        self.assertEqual(ss["rank_by"], [{"field": "ps_pct_hist", "order": "asc"}])
        self.assertEqual(ss["limit"], 20)
        self.assertRegex(ss["as_of"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertTrue(ss["explain"])
        self.assertEqual(comp["rebalance"], "monthly")
        self.assertEqual(comp["cost_bps"], 20)
        self.assertTrue(comp["spec_hash"].startswith("sha256:"))
        self.assertEqual(len(comp["spec_hash"]), 71)
        self.assertFalse(FakeScreener.calls)  # compile 纯函数：不调 screener.validate/run

    def test_compile_invalid_only_error_keys(self):
        comp = st.compile({"name": ""})
        self.assertFalse(comp["ok"])
        self.assertEqual(set(comp.keys()), {"ok", "errors", "warnings"})

    def test_spec_hash_stable_and_sensitive(self):
        h1 = st.compile(copy.deepcopy(GOLDEN_A))["spec_hash"]
        h2 = st.compile(copy.deepcopy(GOLDEN_A))["spec_hash"]
        h3 = st.compile(_spec(limit=5))["spec_hash"]
        self.assertEqual(h1, h2)
        self.assertNotEqual(h1, h3)

    def test_describe_golden_a(self):
        text = st.describe(copy.deepcopy(GOLDEN_A))
        self.assertTrue(text)
        self.assertLessEqual(len(text), 300)
        self.assertIn("HS300 内", text)
        self.assertIn("PS 历史分位 ≤", text)
        self.assertIn("ROE ≥ 12%", text)
        self.assertIn("升序取前 20", text)
        self.assertIn("月度调仓", text)
        self.assertIn("成本 20bp（双边）", text)
        self.assertIn("非 ST", text)

    def test_describe_invalid_returns_empty(self):
        self.assertEqual(st.describe({"name": ""}), "")
        self.assertEqual(st.describe([]), "")

    def test_describe_golden_b_c(self):
        b = st.describe(copy.deepcopy(GOLDEN_B))
        self.assertIn("全A 内", b)
        self.assertIn("周频调仓", b)
        self.assertIn("成本 30bp（双边）", b)
        c = st.describe(copy.deepcopy(GOLDEN_C))
        self.assertIn("日频调仓", c)
        self.assertIn("成本 40bp（双边）", c)


class TestStrategyLibrary(_Base):
    def test_save_list_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            r = st.save_strategy(copy.deepcopy(GOLDEN_A), base=td)
            self.assertTrue(r["ok"], r)
            self.assertTrue(r["created"])
            slug = r["slug"]
            self.assertRegex(slug, st.SLUG_RE)
            self.assertTrue(os.path.isfile(r["path"]))
            listed = st.list_strategies(base=td)
            self.assertEqual(listed["warnings"], [])
            self.assertEqual(len(listed["strategies"]), 1)
            row = listed["strategies"][0]
            self.assertEqual(row["slug"], slug)
            self.assertEqual(row["name"], "低估值高质量")
            self.assertEqual(row["rebalance"], "monthly")
            self.assertEqual(row["cost_bps"], 20)
            self.assertEqual(row["limit"], 20)
            loaded = st.load_strategy(slug, base=td)
            self.assertTrue(loaded["ok"])
            # P1-1：默认 as_of 不落 spec（只记 meta），load 按 meta.as_of_explicit=false 复原
            expected = st.validate(copy.deepcopy(GOLDEN_A))["normalized"]
            expected.pop("as_of", None)
            self.assertEqual(loaded["spec"], expected)
            self.assertNotIn("as_of", loaded["spec"])
            self.assertFalse(loaded["meta"]["as_of_explicit"])
            self.assertRegex(loaded["meta"]["as_of"], r"^\d{4}-\d{2}-\d{2}$")
            self.assertEqual(loaded["meta"]["schema_version"], "v1")
            self.assertTrue(loaded["meta"]["spec_hash"].startswith("sha256:"))
            self.assertTrue(loaded["meta"]["created"])
            doc = json.load(open(r["path"], encoding="utf-8"))
            self.assertEqual(doc["schema_version"], "v1")
            self.assertEqual(doc["slug"], slug)
            self.assertEqual(len(doc["changelog"]), 1)
            self.assertEqual(doc["changelog"][0]["note"], "init")

    def test_same_name_without_slug_appends_suffix(self):
        with tempfile.TemporaryDirectory() as td:
            r1 = st.save_strategy(copy.deepcopy(GOLDEN_A), base=td)
            r2 = st.save_strategy(copy.deepcopy(GOLDEN_A), base=td)
            self.assertTrue(r1["created"])
            self.assertTrue(r2["created"])
            self.assertNotEqual(r1["slug"], r2["slug"])
            self.assertTrue(r2["slug"].endswith("-2"))
            self.assertEqual(len(st.list_strategies(base=td)["strategies"]), 2)

    def test_explicit_slug_upsert(self):
        with tempfile.TemporaryDirectory() as td:
            r1 = st.save_strategy(copy.deepcopy(GOLDEN_A), slug="my-value", base=td)
            self.assertTrue(r1["ok"])
            self.assertTrue(r1["created"])
            r2 = st.save_strategy(_spec(limit=5), slug="my-value", base=td)
            self.assertTrue(r2["ok"])
            self.assertFalse(r2["created"])
            self.assertEqual(r2["slug"], "my-value")
            loaded = st.load_strategy("my-value", base=td)
            self.assertEqual(loaded["spec"]["limit"], 5)
            doc = json.load(open(r2["path"], encoding="utf-8"))
            self.assertEqual([c["note"] for c in doc["changelog"]], ["init", "update"])
            self.assertEqual(doc["meta"]["created"], doc["changelog"][0]["ts"])

    def test_invalid_slug_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            for bad in ("Bad Slug!", "../evil", "x" * 80, "a", "my_strategy"):
                r = st.save_strategy(copy.deepcopy(GOLDEN_A), slug=bad, base=td)
                self.assertFalse(r["ok"], bad)
                self.assertIn("invalid slug", r["error"])

    def test_atomic_write_no_tmp_leftovers(self):
        with tempfile.TemporaryDirectory() as td:
            st.save_strategy(copy.deepcopy(GOLDEN_A), base=td)
            d = os.path.join(td, "strategies")
            files = os.listdir(d)
            self.assertEqual(len([f for f in files if f.endswith(".json")]), 1)
            self.assertEqual([f for f in files if ".tmp" in f], [])

    def test_list_skips_broken_file(self):
        with tempfile.TemporaryDirectory() as td:
            d = os.path.join(td, "strategies")
            os.makedirs(d)
            with open(os.path.join(d, "broken.json"), "w", encoding="utf-8") as f:
                f.write("{not-json")
            listed = st.list_strategies(base=td)
            self.assertTrue(listed["ok"])
            self.assertEqual(listed["strategies"], [])
            self.assertTrue(listed["warnings"])

    def test_load_missing_and_invalid(self):
        with tempfile.TemporaryDirectory() as td:
            r = st.load_strategy("no-such", base=td)
            self.assertFalse(r["ok"])
            self.assertEqual(r["error"], "not found")
            r2 = st.load_strategy("Bad Slug!", base=td)
            self.assertFalse(r2["ok"])
            self.assertEqual(r2["error"], "invalid slug")

    def test_slugify(self):
        self.assertEqual(st.slugify("My Strategy!"), "my-strategy")
        self.assertEqual(st.slugify("my_strategy"), "my-strategy")
        zh = st.slugify("低估值高质量")
        self.assertTrue(zh.startswith("strategy-"))
        self.assertEqual(zh, st.slugify("低估值高质量"))
        self.assertLessEqual(len(st.slugify("x" * 100)), 49)
        self.assertTrue(re.match(st.SLUG_RE, st.slugify("A")))

    def test_save_engine_missing(self):
        st._load_screener = lambda: None
        r = st.save_strategy(copy.deepcopy(GOLDEN_A))
        self.assertFalse(r["ok"])
        self.assertTrue(r.get("engine_missing"))
        self.assertEqual(r["errors"], [st.ENGINE_MISSING])


class TestSpecHashStability(_Base):
    """P2 返工：默认 as_of 不进 spec_hash（跨日同 hash）；显式 as_of 才进。"""

    def _with_today(self, day):
        import types as _types
        orig = st.time
        st.time = _types.SimpleNamespace(strftime=lambda fmt, *a, **k: day)
        self.addCleanup(lambda: setattr(st, "time", orig))

    def test_default_as_of_hash_stable_across_days(self):
        spec = copy.deepcopy(GOLDEN_A)
        self._with_today("2026-11-04")
        h1 = st.compile(spec)["spec_hash"]
        self._with_today("2026-11-05")
        h2 = st.compile(spec)["spec_hash"]
        self.assertEqual(h1, h2)

    def test_explicit_as_of_participates_in_hash(self):
        h1 = st.compile(_spec(as_of="2026-11-04"))["spec_hash"]
        h2 = st.compile(_spec(as_of="2026-11-05"))["spec_hash"]
        self.assertNotEqual(h1, h2)

    def test_default_vs_explicit_same_date_hash_differs(self):
        today = time.strftime("%Y-%m-%d")
        h_default = st.compile(copy.deepcopy(GOLDEN_A))["spec_hash"]
        h_explicit = st.compile(_spec(as_of=today))["spec_hash"]
        self.assertNotEqual(h_default, h_explicit)

    def test_hash_covers_user_fields(self):
        h1 = st.compile(copy.deepcopy(GOLDEN_A))["spec_hash"]
        h2 = st.compile(_spec(limit=5))["spec_hash"]
        h3 = st.compile(_spec(cost_bps=30))["spec_hash"]
        self.assertNotEqual(h1, h2)
        self.assertNotEqual(h1, h3)


class TestConcurrencyAndAtomic(_Base):
    """P2 返工：原子写（uuid tmp + O_EXCL）与 slug 分配文件锁。"""

    def test_concurrent_new_same_name_unique_slugs(self):
        with tempfile.TemporaryDirectory() as td:
            results = []
            guard = threading.Lock()

            def worker():
                r = st.save_strategy(copy.deepcopy(GOLDEN_A), base=td)
                with guard:
                    results.append(r)

            threads = [threading.Thread(target=worker) for _ in range(6)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=30)
            self.assertEqual(len(results), 6)
            self.assertTrue(all(r.get("ok") for r in results), results)
            slugs = [r["slug"] for r in results]
            self.assertEqual(len(set(slugs)), 6, slugs)
            d = os.path.join(td, "strategies")
            files = os.listdir(d)
            self.assertEqual(len([f for f in files if f.endswith(".json")]), 6)
            self.assertFalse([f for f in files if ".tmp" in f or f == ".lock"])

    def test_concurrent_explicit_upsert_atomic(self):
        with tempfile.TemporaryDirectory() as td:
            results = []
            guard = threading.Lock()

            def worker(i):
                r = st.save_strategy(_spec(limit=5 + i), slug="same-slug", base=td)
                with guard:
                    results.append(r)

            threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=30)
            self.assertEqual(len(results), 6)
            self.assertTrue(all(r.get("ok") for r in results), results)
            d = os.path.join(td, "strategies")
            self.assertEqual([f for f in os.listdir(d) if f.endswith(".json")],
                             ["same-slug.json"])
            doc = json.load(open(os.path.join(d, "same-slug.json"), encoding="utf-8"))
            self.assertIn(doc["spec"]["limit"], list(range(5, 11)))
            self.assertFalse([f for f in os.listdir(d) if ".tmp" in f])

    def test_lock_timeout_returns_busy(self):
        with tempfile.TemporaryDirectory() as td:
            d = os.path.join(td, "strategies")
            os.makedirs(d)
            open(os.path.join(d, ".lock"), "w").close()
            orig_t, orig_s = st.LOCK_TIMEOUT, st.LOCK_STALE
            st.LOCK_TIMEOUT, st.LOCK_STALE = 0.05, 999.0
            try:
                r = st.save_strategy(copy.deepcopy(GOLDEN_A), base=td)
            finally:
                st.LOCK_TIMEOUT, st.LOCK_STALE = orig_t, orig_s
            self.assertFalse(r["ok"])
            self.assertIn("繁忙", r["error"])

    def test_stale_lock_recovered(self):
        with tempfile.TemporaryDirectory() as td:
            d = os.path.join(td, "strategies")
            os.makedirs(d)
            lock = os.path.join(d, ".lock")
            open(lock, "w").close()
            old = time.time() - 3600
            os.utime(lock, (old, old))
            orig_s = st.LOCK_STALE
            st.LOCK_STALE = 1.0
            try:
                r = st.save_strategy(copy.deepcopy(GOLDEN_A), base=td)
            finally:
                st.LOCK_STALE = orig_s
            self.assertTrue(r["ok"], r)

    def test_write_atomic_uses_uuid_tmp_and_o_excl(self):
        with tempfile.TemporaryDirectory() as td:
            d = os.path.join(td, "strategies")
            os.makedirs(d)
            path = os.path.join(d, "x.json")
            real_open = os.open
            seen = []

            def spy(p, flags, mode=0o777):
                seen.append((p, flags))
                return real_open(p, flags, mode)

            st.os.open = spy
            try:
                st._write_atomic(path, {"a": 1})
            finally:
                st.os.open = real_open
            self.assertTrue(any(".tmp-" in p for p, _ in seen), seen)
            self.assertTrue(any(flags & os.O_EXCL for _, flags in seen))
            self.assertFalse([f for f in os.listdir(d) if ".tmp" in f])

    def test_write_atomic_flushes_and_fsyncs(self):
        """P1-1：rename 前必须 flush+fsync，避免掉电留下未落盘内容。"""
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "x.json")
            calls = []
            real_fsync = os.fsync

            def spy(fd):
                calls.append(fd)
                return real_fsync(fd)

            st.os.fsync = spy
            try:
                st._write_atomic(path, {"a": 1})
            finally:
                st.os.fsync = real_fsync
            self.assertTrue(calls, "fsync 未被调用")
            self.assertTrue(os.path.isfile(path))


class TestAsOfPersistence(_Base):
    """P1-1：默认 as_of 不落 spec，重载后 compile 的 spec_hash 必须与 meta 一致。"""

    class _FakeTime:
        def __init__(self, day):
            self.day = day

        def strftime(self, fmt, *a, **k):
            return self.day

        def __getattr__(self, name):
            return getattr(time, name)

    def _with_today(self, day):
        orig = st.time
        st.time = self._FakeTime(day)
        self.addCleanup(lambda: setattr(st, "time", orig))

    def test_default_as_of_not_persisted_and_hash_roundtrips(self):
        with tempfile.TemporaryDirectory() as td:
            r = st.save_strategy(copy.deepcopy(GOLDEN_A), base=td)
            self.assertTrue(r["ok"], r)
            doc = json.load(open(r["path"], encoding="utf-8"))
            self.assertNotIn("as_of", doc["spec"])
            self.assertFalse(doc["meta"]["as_of_explicit"])
            self.assertEqual(doc["meta"]["spec_hash"], r["spec_hash"])
            loaded = st.load_strategy(r["slug"], base=td)
            self.assertTrue(loaded["ok"], loaded)
            self.assertNotIn("as_of", loaded["spec"])
            comp = st.compile(loaded["spec"])
            self.assertTrue(comp["ok"], comp)
            self.assertEqual(comp["spec_hash"], loaded["meta"]["spec_hash"])
            self.assertEqual(comp["spec_hash"], r["spec_hash"])

    def test_default_as_of_hash_survives_next_day_load(self):
        with tempfile.TemporaryDirectory() as td:
            self._with_today("2026-11-04")
            r = st.save_strategy(copy.deepcopy(GOLDEN_A), base=td)
            self.assertTrue(r["ok"], r)
            self._with_today("2026-11-05")
            loaded = st.load_strategy(r["slug"], base=td)
            self.assertTrue(loaded["ok"], loaded)
            comp = st.compile(loaded["spec"])
            self.assertEqual(comp["spec_hash"], r["spec_hash"])
            self.assertEqual(comp["spec_hash"], loaded["meta"]["spec_hash"])
            self.assertEqual(loaded["meta"]["as_of"], "2026-11-04")  # 保存时解析值仅作展示

    def test_explicit_as_of_persisted_and_participates(self):
        with tempfile.TemporaryDirectory() as td:
            spec = _spec(as_of="2026-11-04")
            r = st.save_strategy(copy.deepcopy(spec), base=td)
            self.assertTrue(r["ok"], r)
            doc = json.load(open(r["path"], encoding="utf-8"))
            self.assertEqual(doc["spec"]["as_of"], "2026-11-04")
            self.assertTrue(doc["meta"]["as_of_explicit"])
            loaded = st.load_strategy(r["slug"], base=td)
            self.assertEqual(loaded["spec"]["as_of"], "2026-11-04")
            self.assertTrue(loaded["meta"]["as_of_explicit"])
            comp = st.compile(loaded["spec"])
            self.assertEqual(comp["spec_hash"], loaded["meta"]["spec_hash"])
            self.assertEqual(comp["spec_hash"], r["spec_hash"])
            default = st.save_strategy(copy.deepcopy(GOLDEN_A), base=td)
            self.assertNotEqual(default["spec_hash"], r["spec_hash"])

    def test_legacy_default_as_of_inferred_from_stored_hash(self):
        """旧版落盘：spec 带默认 as_of、meta 无 as_of_explicit，但 hash 按「排除 as_of」算。"""
        with tempfile.TemporaryDirectory() as td:
            d = os.path.join(td, "strategies")
            os.makedirs(d)
            spec = st.validate(copy.deepcopy(GOLDEN_A))["normalized"]
            doc = {"schema_version": "v1", "slug": "legacy", "spec": spec,
                   "meta": {"name": spec["name"], "as_of": spec["as_of"],
                            "spec_hash": st._spec_hash(spec, False)},
                   "changelog": []}
            with open(os.path.join(d, "legacy.json"), "w", encoding="utf-8") as f:
                json.dump(doc, f, ensure_ascii=False)
            loaded = st.load_strategy("legacy", base=td)
            self.assertTrue(loaded["ok"], loaded)
            self.assertNotIn("as_of", loaded["spec"])
            self.assertFalse(loaded["meta"]["as_of_explicit"])
            comp = st.compile(loaded["spec"])
            self.assertEqual(comp["spec_hash"], loaded["meta"]["spec_hash"])

    def test_legacy_explicit_as_of_kept(self):
        with tempfile.TemporaryDirectory() as td:
            d = os.path.join(td, "strategies")
            os.makedirs(d)
            spec = st.validate(_spec(as_of="2026-11-04"))["normalized"]
            doc = {"schema_version": "v1", "slug": "legacy", "spec": spec,
                   "meta": {"name": spec["name"], "as_of": spec["as_of"],
                            "spec_hash": st._spec_hash(spec, True)},
                   "changelog": []}
            with open(os.path.join(d, "legacy.json"), "w", encoding="utf-8") as f:
                json.dump(doc, f, ensure_ascii=False)
            loaded = st.load_strategy("legacy", base=td)
            self.assertTrue(loaded["ok"], loaded)
            self.assertEqual(loaded["spec"]["as_of"], "2026-11-04")
            self.assertTrue(loaded["meta"]["as_of_explicit"])
            comp = st.compile(loaded["spec"])
            self.assertEqual(comp["spec_hash"], loaded["meta"]["spec_hash"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
