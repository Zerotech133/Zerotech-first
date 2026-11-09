# -*- coding: utf-8 -*-
"""tools/tests/test_book_batch_test.py · 批量四关初筛（tools/research/book_batch_test.py）单元测试。

覆盖（任务验收要求）：
  1. **17 项映射覆盖**：`research/strategy_specs/*.yaml` 中 test_status=untested 的 17 项全部进入映射表；
     A/B 级 13 项映射到已注册因子，C 级 4 项必须给出不可计算原因；
  2. **结果 schema**：run_batch（合成小面板，不跑真实全量回测）输出 meta/mapping/results/priority/
     grade_counts，逐战法含映射因子/事件数/IC/IS-OOS 超额/t/p/grade，JSON 可序列化；
  3. **PIT 无前视静态检查**：源码无负 lag（shift(-n)），新因子计算函数源码同样无负 lag；
  4. **动态 PIT 截断不变性**：面板截断到 T 后重算，T 日因子值与全样本一致（无未来数据依赖）；
  5. **空/不可计算容错**：空面板 → 17 项全 F 且不崩溃/可序列化；事件超额无信号 → 返回 None；
  6. 评级函数 A/B/C/D/F 口径与阈值；事件超额数学口径（direction=-1 反号）。

不真跑全量回测：合成面板 130 交易日 × 60 只，单测整体 < 10s。
运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_book_batch_test.py
"""
from __future__ import annotations

import ast
import inspect
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "tools" / "research"
sys.path.insert(0, str(RESEARCH))

import book_batch_test as bt  # noqa: E402
import factor_lib as fl      # noqa: E402

BATCH = ROOT / "tools" / "research" / "book_batch_test.py"
SPEC_DIR = ROOT / "research" / "strategy_specs"
N_DATES, N_CODES = 130, 60


# ------------------------------------------------------------------ 合成面板
def make_panel(n_dates: int = N_DATES, n_codes: int = N_CODES, seed: int = 7) -> fl.Panel:
    """随机面板（字段齐全、复权=收盘），用于 run_batch / PIT / 超额单测。"""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2026-01-05", periods=n_dates).strftime("%Y%m%d")
    codes = [f"{i:06d}.SZ" for i in range(1, n_codes + 1)]
    ret = rng.normal(0.0005, 0.02, size=(n_dates, n_codes))
    close = 10.0 * np.exp(np.cumsum(np.log1p(ret), axis=0))
    pre_close = np.vstack([close[0:1], close[:-1]])
    pct_chg = (close / pre_close - 1.0) * 100.0
    open_ = pre_close * (1.0 + rng.normal(0.0, 0.005, close.shape))
    high = np.maximum(open_, close) * (1.0 + np.abs(rng.normal(0.0, 0.005, close.shape)))
    low = np.minimum(open_, close) * (1.0 - np.abs(rng.normal(0.0, 0.005, close.shape)))
    vol = rng.lognormal(11.0, 0.5, size=close.shape)
    amount = vol * close * 0.1

    def frame(arr):
        return pd.DataFrame(arr, index=dates, columns=codes)

    fields = {
        "open": frame(open_), "high": frame(high), "low": frame(low), "close": frame(close),
        "pre_close": frame(pre_close), "pct_chg": frame(pct_chg), "vol": frame(vol),
        "amount": frame(amount), "adj_close": frame(close),
    }
    return fl.Panel(fields)


@pytest.fixture(scope="module")
def small_batch() -> dict:
    """一次合成面板批量运行（多测试复用，避免重复计算）。"""
    panel = make_panel()
    return bt.run_batch(panel=panel, start=str(panel.dates[0]), end=str(panel.dates[-1]),
                        is_end=str(panel.dates[75]), n_boot=20, min_cross_section=10)


# ------------------------------------------------------------------ 1. 映射覆盖
def test_untested_spec_mapping_covers_17():
    all_specs = bt.load_all_specs()
    untested = bt.load_untested_specs()
    assert len(all_specs) == 24
    assert len(untested) == 17
    mapping = bt.build_spec_mapping(untested)
    assert set(mapping) == {str(s["id"]) for s in untested}
    assert len(mapping) == 17

    # A/B 级 13 项 → 因子；C 级 4 项 → None + 原因
    testable = {sid: m for sid, m in mapping.items() if m["factor"]}
    uncomputable = {sid: m for sid, m in mapping.items() if not m["factor"]}
    assert len(testable) == 13 and len(uncomputable) == 4
    assert all(m["computability"] in ("A", "B") for m in testable.values())
    assert all(m["computability"] == "C" for m in uncomputable.values())
    assert all(m["uncomputable_reason"] for m in uncomputable.values())

    # 4 个 A + 9 个 B + 4 个 C
    from collections import Counter
    counts = Counter(str(s.get("computability")) for s in untested)
    assert counts == {"A": 4, "B": 9, "C": 4}

    # 映射因子必须可实现且为 0/1 事件因子（本批全事件；direction 合法）
    available = bt.all_factor_names()
    for sid, m in testable.items():
        assert m["factor"] in available, f"{sid} 映射因子未实现: {m['factor']}"
        fmeta = bt.factor_meta(m["factor"])
        assert fmeta is not None
        assert fmeta["kind"] == "event", f"{m['factor']} 应为事件因子"
        assert fmeta["direction"] in (1, -1)
    # 复用 factor_lib 现有因子（LX22→aokou）+ 12 个本文件新增
    assert mapping["LX22_CONCAVE_NOTCH_GOLD"]["factor"] == "aokou"
    assert mapping["LX22_CONCAVE_NOTCH_GOLD"]["impl"] == "existing"
    assert len(bt.FACTOR_IMPLS) == 12
    # 规格漂移保护：SPEC_MAP 必须覆盖全部 untested id
    assert set(bt.SPEC_MAP) == {str(s["id"]) for s in untested}


# ------------------------------------------------------------------ 2. 结果 schema
def test_result_schema_and_grade_distribution(small_batch):
    payload = small_batch
    assert set(payload) == {"meta", "mapping", "results", "priority", "grade_counts"}
    meta = payload["meta"]
    for key in ("report", "version", "generated_at", "task", "spec", "eval", "data",
                "baseline", "grade_counts", "priority_top5", "runtime_sec", "notes"):
        assert key in meta, key
    assert meta["spec"]["untested"] == 17
    assert meta["spec"]["testable_ab"] == 13
    assert meta["spec"]["uncomputable_c"] == 4
    for key in ("requested_start", "requested_end", "effective_start", "effective_end",
                "signal_days", "is_end", "horizon_days", "cost_bp_oneway",
                "block_bootstrap", "grade_thresholds"):
        assert key in meta["eval"], key
    assert meta["eval"]["horizon_days"] == 20
    assert meta["eval"]["cost_bp_oneway"] == 30
    assert meta["eval"]["block_bootstrap"]["block"] == 20

    results = payload["results"]
    assert len(results) == 17
    assert {r["spec_id"] for r in results} == set(payload["mapping"])
    for r in results:
        for key in ("spec_id", "name", "type", "computability", "factor", "factor_kind",
                    "factor_direction", "impl", "simplification", "grade", "grade_reason",
                    "ran", "n_events", "n_signal_days", "overall", "is", "oos",
                    "by_year_ic_directed", "bootstrap_ic_p_le_0"):
            assert key in r, f"{r.get('spec_id')}.{key}"
        assert r["grade"] in bt.GRADES
        assert isinstance(r["grade_reason"], str) and r["grade_reason"]
        assert r["computability"] in ("A", "B", "C")
        if r["factor"] is None:
            assert r["grade"] == "F" and r["uncomputable_reason"]
        else:
            assert r["factor"] in bt.all_factor_names()
            assert r["factor_kind"] == "event"
            for section in ("is", "oos"):
                for key in ("ic_days", "ic_mean", "ic_t", "excess_days", "excess_pct",
                            "excess_t", "excess_p_le_0", "n_events"):
                    assert key in r[section], f"{r['spec_id']}.{section}.{key}"
    # grade 计数自洽
    counts = payload["grade_counts"]
    assert set(counts) == set(bt.GRADES)
    assert sum(counts.values()) == 17
    for grade in bt.GRADES:
        assert counts[grade] == sum(1 for r in results if r["grade"] == grade)
    # 优先级清单：rank 连续、排除 F、轨道有序（forward < unclear < reverse）、同轨道内评分降序
    priority = payload["priority"]
    assert 0 <= len(priority) <= 5
    assert [p["rank"] for p in priority] == list(range(1, len(priority) + 1))
    assert all(p["grade"] != "F" for p in priority)
    tracks = [p["track"] for p in priority]
    assert tracks == sorted(tracks, key=bt.PRIORITY_TRACK_ORDER.__getitem__)
    for track in set(tracks):
        scores = [p["score"] for p in priority if p["track"] == track]
        assert scores == sorted(scores, reverse=True), track
    for p in priority:
        for key in ("track", "track_label", "score", "key_metrics", "why", "action"):
            assert key in p, key
        for key in ("n_events", "is_ic_t", "oos_ic_t", "oos_excess_pct",
                    "oos_excess_t", "oos_excess_p_le_0", "ic_p_le_0"):
            assert key in p["key_metrics"], key
    # JSON 可序列化（无 NaN/Infinity）
    text = json.dumps(payload, ensure_ascii=False)
    assert "NaN" not in text and "Infinity" not in text
    # 作用域临时注册已还原：不得污染 factor_lib 全局注册表（保护同会话其它单测）
    assert set(bt.FACTOR_IMPLS).isdisjoint(fl.FACTORS), "run_batch 后全局注册表被本模块污染"


# ------------------------------------------------------------------ 3. 静态 PIT 检查
def test_no_lookahead_static():
    """源码静态检查：批量脚本内不得出现负 lag（shift(-n)）等未来数据算子。"""
    source = BATCH.read_text(encoding="utf-8")
    assert "shift(-" not in source.replace(" ", ""), "book_batch_test.py 源码含负 lag（疑似前视）"
    tree = ast.parse(source)
    bad: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                    and sub.func.attr == "shift" and sub.args):
                try:
                    value = ast.literal_eval(sub.args[0])
                except (ValueError, SyntaxError):
                    continue
                if isinstance(value, (int, float)) and not isinstance(value, bool) and value < 0:
                    bad.add(node.name)
    assert bad == set(), f"负 lag 出现在: {sorted(bad)}"

    # 每个新因子实现函数源码同样不得含负 lag；且必须有描述/来源
    for name, meta in bt.FACTOR_IMPLS.items():
        src = inspect.getsource(meta["compute"]).replace(" ", "")
        assert "shift(-" not in src, f"因子 {name} 源码含负 lag（疑似前视）"
        assert meta["kind"] == "event"
        assert meta["description"].strip() and meta["source"].strip()
        assert meta["direction"] in (1, -1)


# ------------------------------------------------------------------ 4. 动态 PIT 截断不变性
def test_pit_truncation_invariance():
    """面板截断到 T 后重算，T 日因子值必须与全样本一致（无未来信息依赖）。"""
    panel = make_panel()
    cut_i = 90
    cut = str(panel.dates[cut_i])
    truncated = panel.subset(max_date=cut)
    assert len(truncated.dates) == cut_i + 1
    for name in bt.FACTOR_IMPLS:
        full = bt.compute_factor(name, panel)
        part = bt.compute_factor(name, truncated)
        a = full.loc[cut].to_numpy(dtype=float)
        b = part.iloc[-1].to_numpy(dtype=float)
        np.testing.assert_allclose(a, b, rtol=1e-10, atol=1e-12, equal_nan=True,
                                   err_msg=f"因子 {name} 在 {cut} 日存在未来数据依赖（前视）")


# ------------------------------------------------------------------ 5. 空/不可计算容错
def test_empty_panel_and_uncomputable_tolerance():
    empty = fl.Panel.empty()
    payload = bt.run_batch(panel=empty, start="20230101", end="20261030", n_boot=10)
    assert payload["meta"]["spec"]["untested"] == 17
    assert payload["grade_counts"] == {"A": 0, "B": 0, "C": 0, "D": 0, "F": 17}
    for r in payload["results"]:
        assert r["grade"] == "F"
        assert r["ran"] is False
        assert isinstance(r["grade_reason"], str) and r["grade_reason"]
    assert payload["priority"] == []
    json.dumps(payload, ensure_ascii=False)

    # 事件超额：无信号 → None；全 NaN 输入不崩溃
    car = np.zeros((10, 3))
    elig = np.ones((10, 3), dtype=bool)
    f_none = np.full((10, 3), np.nan)
    assert bt._event_excess_stats(car, f_none, elig, [f"2026010{i}" for i in range(1, 11)],
                                  "20260105", 1, n_boot=5) is None
    f_no_signal = np.zeros((10, 3))
    assert bt._event_excess_stats(car, f_no_signal, elig, [f"2026010{i}" for i in range(1, 11)],
                                  "20260105", 1, n_boot=5) is None
    assert bt._mean_t([])["n_days"] == 0


# ------------------------------------------------------------------ 6. 评级口径
def _mk_res(**over):
    """构造评级输入（默认显著 A 档参数）。"""
    res = {
        "factor": "demo_factor", "ran": True, "n_events": 200,
        "bootstrap_ic_p_le_0": 0.01,
        "is": {"ic_days": 300, "ic_t": 0.8, "excess_pct": 0.2, "excess_t": 1.0},
        "oos": {"ic_days": 120, "ic_t": 2.5, "n_events": 80,
                "excess_pct": 0.8, "excess_t": 2.2, "excess_p_le_0": 0.01},
    }
    for key, value in over.items():
        if key in ("is", "oos"):
            res[key].update(value)
        else:
            res[key] = value
    return res


def test_grade_entry_thresholds():
    assert bt.grade_entry(_mk_res())[0] == "A"
    # A 需同时满足：p<0.05
    assert bt.grade_entry(_mk_res(oos={"excess_p_le_0": 0.20}))[0] == "B"
    # 方向对但不显著
    assert bt.grade_entry(_mk_res(oos={"ic_t": 1.2, "excess_pct": 0.3,
                                       "excess_t": 1.1, "excess_p_le_0": 0.3}))[0] == "B"
    # 不显著（方向也不对、|t|<1）
    assert bt.grade_entry(_mk_res(oos={"ic_t": -0.3, "excess_pct": -0.2,
                                       "excess_t": -0.4, "excess_p_le_0": 0.6}))[0] == "C"
    # 反向显著
    assert bt.grade_entry(_mk_res(oos={"ic_t": -2.5, "excess_pct": -1.0,
                                       "excess_t": -2.3}))[0] == "D"
    # 反向：t≤-1 且超额为负
    assert bt.grade_entry(_mk_res(oos={"ic_t": -1.2, "excess_pct": -0.5,
                                       "excess_t": -1.3}))[0] == "D"
    # 不可计算
    assert bt.grade_entry(_mk_res(factor=None, uncomputable_reason="C 级画线"))[0] == "F"
    # 数据不足（事件数太少）
    assert bt.grade_entry(_mk_res(n_events=5))[0] == "F"
    # 数据不足（OOS 有效日太少）
    assert bt.grade_entry(_mk_res(oos={"ic_days": 5}))[0] == "F"


# ------------------------------------------------------------------ 7. 事件超额口径
def test_event_excess_math_direction():
    """事件组超额：信号组 CAR − 非信号组 CAR；direction=-1 时符号翻转。"""
    # 10 日 × 4 股；第 7 日（OOS 内）只有第 0 股是信号且其 20 日 CAR 明显为正
    car = np.full((10, 4), 0.01)
    car[7, 0] = 0.06
    f = np.zeros((10, 4))
    f[7, 0] = 1.0
    dates = [f"2026010{i}" for i in range(1, 11)]
    pos = bt._event_excess_stats(car, f, np.ones((10, 4), dtype=bool), dates,
                                 "20260105", direction=1, n_boot=5)
    neg = bt._event_excess_stats(car, f, np.ones((10, 4), dtype=bool), dates,
                                 "20260105", direction=-1, n_boot=5)
    assert pos["n_events"] == 1 and pos["n_signal_days"] == 1
    # 第 7 日信号组均值 0.06、非信号组均值 0.01 → 超额 0.05（5%）
    assert pos["overall"]["mean_pct"] == pytest.approx(5.0, abs=1e-9)
    assert neg["overall"]["mean_pct"] == pytest.approx(-5.0, abs=1e-9)
    assert pos["overall"]["p_le_0"] is None          # 样本 < block*3 → 不可算
    assert pos["IS"]["mean_pct"] is None             # 信号全在 OOS，IS 无事件超额
    assert pos["OOS"]["mean_pct"] == pytest.approx(5.0, abs=1e-9)


# ------------------------------------------------------------------ 8. Markdown 渲染
def test_render_markdown_contains_sections(small_batch):
    md = bt.render_markdown(small_batch)
    assert md.startswith("# 量学战法批量四关初筛")
    for section in ("## 一、汇总表", "## 二、结论", "## 三、建议优先深测",
                    "## 四、口径与局限", "## 五、复现"):
        assert section in md, section
    # 17 项都在表里 + 4 个 C 级不可计算原因
    for r in small_batch["results"]:
        assert r["spec_id"] in md
    assert "不可计算" in md
    assert "block bootstrap" in md
    assert len(md.splitlines()) > 60
