# -*- coding: utf-8 -*-
"""P5.2 案例库 / 演化路径验证器回归测试。

铁律：本文件**不出现任何手写日期或编号字面量**。日期一律 chr() 码点构造，
且用 ord() 双向复核——本机输出/传输通道会把数字串味，凭打印判断会漏拦。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jev_casebook as C  # noqa: E402
import jev_pattern as P  # noqa: E402

# 日期一律由下方码点元组 chr() 构造，本文件不写任何日期字面量。
_D1 = "".join(chr(x) for x in (50, 48, 50, 54, 49, 49, 48, 54))
_D2 = "".join(chr(x) for x in (50, 48, 50, 54, 49, 49, 48, 57))
_D3 = "".join(chr(x) for x in (50, 48, 50, 54, 49, 49, 49, 48))
_CP1 = (50, 48, 50, 54, 49, 49, 48, 54)
_CP3 = (50, 48, 50, 54, 49, 49, 49, 48)


def _case(code: str, path: str | None, hits=(), **kw) -> C.Case:
    return C.Case(code=code, day=_D1, path=path,
                  hits=[{"pattern": p} for p in hits], **kw)


# ── 码点自证（防止测试文件自身被串味） ─────────────────────────
def test_day_literals_are_codepoint_constructed():
    assert [ord(c) for c in _D1] == list(_CP1)
    assert [ord(c) for c in _D3] == list(_CP3)
    assert len(_D1) == len("YYYYMMDD")
    assert len(_D2) == len(_D1)
    assert _D1 < _D2 < _D3
    assert _D1.isdigit() and _D2.isdigit() and _D3.isdigit()


def test_no_handwritten_date_literal_in_this_file():
    src = Path(__file__).read_text(encoding="utf-8")
    # 形如 2026xxxx 的 8 位连续数字，只允许出现在码点元组里（元组有逗号分隔）
    assert re.search(r"(?<![\d,])\d{8}(?![\d,])", src) is None


# ── 路径分类 ──────────────────────────────────────────────────
@pytest.mark.parametrize("v,expect", [
    (-9.9, C.PATH_FALL), (-3.01, C.PATH_FALL),
    (-3.0, C.PATH_FLAT), (0.0, C.PATH_FLAT), (3.0, C.PATH_FLAT),
    (3.01, C.PATH_RISE), (10.02, C.PATH_RISE),
    (None, C.PATH_MISSING), ("bad", C.PATH_MISSING), (float("nan"), C.PATH_MISSING),
])
def test_classify_path(v, expect):
    assert C.classify_path(v) == expect


def test_classify_path_band_configurable():
    cth = C.CaseThresholds(path_band=1.0)
    assert C.classify_path(2.0, cth) == C.PATH_RISE
    assert C.classify_path(2.0) == C.PATH_FLAT


# ── 后验交易日扫描 ────────────────────────────────────────────
def test_next_trading_days_scans_directory(tmp_path):
    for d in (_D1, _D2, _D3):
        (tmp_path / ("d_%s.parquet" % d)).write_bytes(b"")
    (tmp_path / "not_a_day.parquet").write_bytes(b"")
    (tmp_path / ("d_%s.json" % _D2)).write_bytes(b"")
    got = C.next_trading_days(_D1, 3, daily_dir=str(tmp_path))
    assert got == [_D2, _D3], [ord(c) for d in got for c in d]


def test_next_trading_days_no_fabrication(tmp_path):
    assert C.next_trading_days(_D3, 2, daily_dir=str(tmp_path)) == []
    assert C.next_trading_days(_D1, 2, daily_dir=str(tmp_path / "missing")) == []


def test_load_daily_rows_missing_file_is_empty(tmp_path):
    assert C.load_daily_rows(_D1, daily_dir=str(tmp_path)) == {}


# ── 路径分布 / 分化度 ─────────────────────────────────────────
def _pool(n_fall, n_rise, n_flat, pattern="P1"):
    out = []
    for i, (path, n) in enumerate(((C.PATH_FALL, n_fall),
                                   (C.PATH_RISE, n_rise),
                                   (C.PATH_FLAT, n_flat))):
        for j in range(n):
            out.append(_case("%06d" % (i * 100 + j), path, hits=(pattern,)))
    return out


def test_path_distribution_counts_and_divergence():
    d = C.path_distribution(_pool(6, 2, 0))
    assert d["n"] == 8
    assert d["counts"] == {C.PATH_FALL: 6, C.PATH_RISE: 2, C.PATH_FLAT: 0}
    assert d["dominant"] == C.PATH_FALL
    assert d["divergence_pp"] == pytest.approx(50.0)
    assert d["probs"][C.PATH_FALL] == pytest.approx(0.75)


def test_small_sample_never_reports_divergence():
    d = C.path_distribution(_pool(1, 1, 0))
    assert d["divergence_pp"] is None
    assert d["reason"] == "insufficient"
    assert C.path_distribution([])["n"] == 0


def test_missing_path_excluded_from_distribution():
    cases = _pool(5, 1, 0) + [_case("999999", None), _case("999998", C.PATH_MISSING)]
    d = C.path_distribution(cases)
    assert d["n"] == 6
    assert C.path_distribution(cases, pattern="P7")["n"] == 0


# ── 分化节点 ──────────────────────────────────────────────────
def test_fork_nodes_insufficient_when_branch_too_small():
    cases = _pool(3, 1, 0)
    nodes = C.fork_nodes(cases, "P1")
    assert nodes
    assert all(n["status"] == "insufficient" for n in nodes)
    assert all(n["delta_pp"] is None for n in nodes)


def test_fork_nodes_detects_real_migration():
    # 两侧各 6 例：一侧 5 回落，另一侧 1 回落 → 主路径概率差 > 10pp
    hot = [_case("%06d" % i, C.PATH_FALL, hits=("P1",), social_covered=True)
           for i in range(5)]
    hot += [_case("900005", C.PATH_RISE, hits=("P1",), social_covered=True)]
    cold = [_case("80000%d" % i, C.PATH_RISE, hits=("P1",), social_covered=False)
            for i in range(5)]
    cold += [_case("900006", C.PATH_FALL, hits=("P1",), social_covered=False)]
    nodes = {n["signal"]: n for n in C.fork_nodes(hot + cold, "P1")}
    assert nodes["social_covered"]["status"] == "fork"
    assert nodes["social_covered"]["delta_pp"] > C.DEFAULT_CTH.fork_min_pp


def test_availability_proxy_never_counted_as_fork():
    """time_ready 只代表「有本地分钟数据」（中证800 覆盖偏倚），不是信号取值。

    即使它分出 100pp 的概率差，也绝不能算进 v2 §六 条件③ 的分化节点数。
    """
    hot = [_case("%06d" % i, C.PATH_FALL, hits=("P1",),
                 delta_kind="price_lead", delta_min=41.0, social_covered=True)
           for i in range(6)]
    cold = [_case("80000%d" % i, C.PATH_RISE, hits=("P1",),
                  delta_kind="unpriced", delta_min=None, social_covered=True)
            for i in range(6)]
    cases = hot + cold
    nodes = {n["signal"]: n for n in C.fork_nodes(cases, "P1")}
    t = nodes["time_ready"]
    assert t["node_class"] == "availability_proxy"
    assert t["status"] == "fork"           # 诊断数值仍保留
    assert t["delta_pp"] is not None
    assert t["countable"] is False          # 但不计数
    r = C.validate(cases, day=_D1)
    assert r["n_pattern_with_fork"] == 0
    assert r["n_proxy_fork_total"] >= 1
    assert r["by_pattern"]["P1"]["n_fork"] == 0


def test_degenerate_fork_is_not_countable():
    """pattern 自身的命中维度做分叉必然一侧为空，不能算分化节点。"""
    cases = [_case("%06d" % i, C.PATH_FALL, hits=("P1",),
                   main_net=800.0, net_mf=-300.0, social_covered=True)
             for i in range(6)]
    nodes = {n["signal"]: n for n in C.fork_nodes(cases, "P1")}
    assert nodes["fund_divergence"]["degenerate"] is True
    assert nodes["social_covered"]["degenerate"] is True


def test_unpriced_time_is_none_not_false():
    c = _case("000001", C.PATH_FALL, delta_kind="unpriced", delta_min=None)
    flags = C._fork_flags(c)
    assert flags["time_ready"] is False          # unpriced 明确为无数据侧
    assert flags["price_lead"] is None           # 不参与「是否价格先行」二分
    assert flags["fund_divergence"] is None      # 资金缺失不判背离


# ── 验证器护栏 ────────────────────────────────────────────────
def test_validate_is_never_gate_eligible():
    cases = _pool(30, 10, 5, pattern="P1")
    r = C.validate(cases, day=_D1)
    assert r["feasibility_only"] is True
    assert r["gate_eligible"] is False
    assert r["calibrated"] is False
    assert r["gate_block_reasons"]
    assert r["coverage"] == pytest.approx(1.0)
    assert r["n_cases"] == len(cases)
    assert set(r["by_pattern"]) == {"P" + str(i) for i in range(1, 9)}
    assert r["horizons"] == ["T+1"]


def test_validate_coverage_counts_unhit_cases():
    cases = _pool(4, 1, 0, pattern="P1") + [_case("777777", C.PATH_FLAT)]
    r = C.validate(cases, day=_D1)
    assert r["n_hit_any"] == len(cases) - 1
    assert r["coverage"] == pytest.approx(round((len(cases) - 1) / len(cases), 4))


def test_validate_all_missing_path_still_safe():
    r = C.validate([_case("000001", None)], day=_D1)
    assert r["n_with_path"] == 0
    assert r["baseline_dist"]["divergence_pp"] is None
    assert r["gate_eligible"] is False


# ── 落盘 / 序列化 ─────────────────────────────────────────────
def test_write_casebook_atomic_and_roundtrip(tmp_path):
    out = tmp_path / "casebook.json"
    payload = C.validate(_pool(6, 2, 0, pattern="P1"), day=_D1)
    payload["cases"] = [c.as_dict() for c in _pool(6, 2, 0, pattern="P1")]
    C.write_casebook(str(out), payload)
    assert out.exists()
    assert not (tmp_path / "casebook.json.tmp").exists()
    back = json.loads(out.read_text(encoding="utf-8"))
    assert back["feasibility_only"] is True
    assert back["gate_eligible"] is False
    assert len(back["cases"]) == 8


def test_case_as_dict_json_serializable():
    c = _case("000001", C.PATH_FALL, hits=("P1",),
              horizons={"T+1": {"day": _D2, "path": C.PATH_FALL}},
              delta_kind="price_lead", delta_min=41.0)
    assert json.loads(json.dumps(c.as_dict(), ensure_ascii=False))["hits"][0]["pattern"] == "P1"


# ── 与 P5.1 契约对齐 ──────────────────────────────────────────
def test_match_signal_hits_compatible_with_case_hit_ids():
    s = P.Signal(code="000001", day=_D1, main_net=800.0, net_mf=-300.0,
                 flow_has=True, guba_rank=3, social_has=True,
                 delta_kind="price_lead", delta_min=41.0)
    hits = [h.as_dict() for h in P.match_signal(s)]
    c = _case("000001", C.PATH_FALL, hits=())
    c.hits = hits
    assert c.hit_ids() == [h["pattern"] for h in hits]
    assert all("pattern" in h and "dims" in h for h in hits)
