# -*- coding: utf-8 -*-
"""combined_verdicts（B3 后验引擎扩展 signals_combined）单元测试。

全部内存夹具，不碰 PG / 生产数据 / 行情湖。
"""
import json
import os
import sys

import pytest

_QB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _QB not in sys.path:
    sys.path.insert(0, _QB)

from strategy_layer.combined_verdicts import (  # noqa: E402
    _psql_rows, after_days, blk, build, load_benchmark_mid, load_rows_file,
    normalize_rows, read_shadow_view, shadow_tags_of, weight_band,
)

CAL = ["20261012", "20261013", "20261014", "20261015", "20261016",
       "20261019", "20261020", "20261021", "20261022", "20261023"]


def _ret_table(spec=None, default=2.0):
    """收益注入：spec[(sym, exec, d1)] -> r；缺省 default。"""
    spec = spec or {}
    return lambda sym, d0, d1: spec.get((sym, d0, d1), default)


def _base_rows():
    return [
        # A：双源 buy 事件（kg 0.6 / chan 0.4）
        {"trade_date": "20261012", "symbol": "000001.SZ", "action": "buy",
         "score": 70.0, "confidence": 0.8,
         "contributions": {"kg_boost": {"score": 80, "weight": 0.6,
                                        "action": "buy"},
                           "chan_monitor": {"score": 55, "weight": 0.4,
                                            "action": "buy"}},
         "divergence": False, "rationale": "共振"},
        # B：sell 事件——不是成绩样本，不入任何桶
        {"trade_date": "20261012", "symbol": "000002.SZ", "action": "sell",
         "score": 30.0, "confidence": 0.6,
         "contributions": {"kg_boost": {"score": 30, "weight": 1.0,
                                        "action": "sell"}},
         "divergence": False, "rationale": "卖点"},
        # C：env=down 恐慌 buy（影子 panic_buy）
        {"trade_date": "20261015", "symbol": "000003.SZ", "action": "buy",
         "score": 60.0, "confidence": 0.5,
         "contributions": {"kg_boost": {"score": 60, "weight": 0.9,
                                        "action": "buy"}},
         "divergence": False, "rationale": "恐慌"},
        # D：T1 除权 void（exdiv 哨兵）
        {"trade_date": "20261012", "symbol": "000004.SZ", "action": "buy",
         "score": 65.0, "confidence": 0.7,
         "contributions": {"kg_boost": {"score": 65, "weight": 1.0,
                                        "action": "buy"}},
         "divergence": False, "rationale": "x"},
        # E：contributions 为 JSON 串（PG ::text 形态）+ 低权重带
        {"trade_date": "20261012", "symbol": "000005.SZ", "action": "buy",
         "score": 55.0, "confidence": 0.4,
         "contributions": json.dumps({"kg_boost": {"score": 55,
                                                   "weight": 0.3,
                                                   "action": "buy"}}),
         "divergence": False, "rationale": "x"},
        # F：缺 weight——策略桶计入、权重带跳过
        {"trade_date": "20261012", "symbol": "000006.SZ", "action": "buy",
         "score": 50.0, "confidence": 0.5,
         "contributions": {"kg_boost": {"score": 50, "action": "buy"}},
         "divergence": False, "rationale": "x"},
        # G：rationale 携 reg=down（env 缺失时影子兜底标注）
        {"trade_date": "20261015", "symbol": "000007.SZ", "action": "buy",
         "score": 45.0, "confidence": 0.3,
         "contributions": {"kg_boost": {"score": 45, "weight": 1.0,
                                        "action": "buy"}},
         "divergence": False, "rationale": "环境 reg=down 回避"},
    ]


EXDIV_SYM = "000004.SZ"


def _ret_fn(sym, d0, d1):
    if sym == EXDIV_SYM:
        return "exdiv"
    if sym == "000001.SZ" and d1 == "20261015":
        return -1.0  # 制造一笔亏样本压 hit
    return 2.0


ENV = {"20261015": "down", "20261012": {"diff": True, "regime": "up"}}


# ── 工具函数 ───────────────────────────────────────────────────────────
def test_after_days_strict_and_overflow():
    assert after_days(CAL, "20261012", 2) == "20261014"   # exec 日
    assert after_days(CAL, "20261014", 1) == "20261015"
    assert after_days(CAL, "20261014", 5) == "20261021"
    assert after_days(CAL, "20261023", 1) is None          # 溢出 → pending
    assert after_days(CAL, "20261011", 1) == "20261012"    # 非交易日信号


def test_weight_band_boundaries():
    assert weight_band(0.0) == "lt025"
    assert weight_band(0.25) == "b025_050"
    assert weight_band(0.50) == "b050_075"
    assert weight_band(0.75) == "ge075"


def test_normalize_rows_parses_json_string_and_skips_bad():
    rows = normalize_rows(_base_rows())
    e = next(r for r in rows if r["symbol"] == "000005.SZ")
    assert isinstance(e["contributions"], dict)
    assert normalize_rows([{"trade_date": "bad", "symbol": "000001.SZ"},
                           {"trade_date": "20261012", "symbol": "00700.HK"},
                           "junk"]) == [
        {"trade_date": "20261012", "symbol": "00700.HK", "action": "hold",
         "score": 0.0, "confidence": 0.0, "contributions": {},
         "divergence": False, "rationale": ""}]


def test_blk_stats():
    assert blk([]) == {"n": 0, "hit": None, "avg": None, "med": None}
    b = blk([-1.0, 2.0, 2.0])
    assert b["n"] == 3 and b["hit"] == 66.7 and b["avg"] == 1.0
    assert b["med"] == 2.0


# ── 影子标注（Δ5 P3：只标不拦） ────────────────────────────────────────
def test_shadow_tags_env_and_rationale_fallback():
    rows = normalize_rows(_base_rows())
    by_sym = {r["symbol"]: r for r in rows}
    assert shadow_tags_of(by_sym["000003.SZ"], ENV) == ["panic_buy"]
    assert shadow_tags_of(by_sym["000001.SZ"], ENV) == ["env_diff"]
    assert shadow_tags_of(by_sym["000007.SZ"], None) == ["panic_buy"]
    # sell 事件不入收益桶，但 diff 日 env_diff 影子标注照标（只标不拦）
    assert shadow_tags_of(by_sym["000002.SZ"], ENV) == ["env_diff"]


# ── 后验主流程：分层统计 ───────────────────────────────────────────────
@pytest.fixture()
def res():
    return build(_base_rows(), CAL, _ret_fn, env=ENV)


def test_windows_event_buckets(res):
    w = res["summary"]["windows"]
    # buy 事件 6 个（A~G 除 B sell），D exdiv void → T1 n=5
    assert w["1"]["n"] == 5
    assert w["1"]["hit"] == 80.0     # A 的 T1 为 -1，其余 +2
    assert w["1"]["avg"] is not None and w["1"]["med"] is not None
    # C/G（sig=20261015）T5 越界 → 事件缺；A/E/F 有 T5
    assert w["5"]["n"] == 3
    assert w["3"]["n"] == 5


def test_by_strategy_multi_source_cross_bucket(res):
    bs = res["summary"]["by_strategy"]
    # 多源事件跨桶：A 同时进 kg_boost 与 chan_monitor
    assert bs["kg_boost"]["1"]["n"] == 5
    assert bs["chan_monitor"]["1"]["n"] == 1
    # sell 事件不入桶（B 只有 kg_boost 一源）
    assert "n" in bs["kg_boost"]["1"]


def test_by_weight_group(res):
    bw = res["summary"]["by_weight_group"]
    assert bw["kg_boost"]["b050_075"]["1"]["n"] == 1   # A w=0.6
    assert bw["kg_boost"]["ge075"]["1"]["n"] == 2      # C w=0.9 + G w=1.0
    assert bw["kg_boost"]["b025_050"]["1"]["n"] == 1   # E w=0.3
    assert "lt025" not in bw["kg_boost"]               # 无样本不出带
    # F 缺 weight → 只有策略桶、无权重带（kg T1=5 含 F，但各带 n 合计 4）
    assert sum(v["1"]["n"] for v in bw["kg_boost"].values()) == 4


def test_by_shadow_additive_view_not_blocking(res):
    sh = res["summary"]["by_shadow"]
    # C（env down）+ G（reg=down 兜底）→ panic_buy 2 事件
    assert sh["panic_buy"]["1"]["n"] == 2
    # A/D/E/F（env diff 日）→ env_diff 4 事件，D exdiv void 不计
    assert sh["env_diff"]["1"]["n"] == 3
    # 只标不拦：影子标注不影响基础层（windows n 仍含被标注事件）
    assert res["summary"]["windows"]["1"]["n"] == 5
    # 事件级 per_code 影子标签留痕
    assert res["per_code"]["000003.SZ"]["shadow_tags"] == ["panic_buy"]
    assert res["per_code"]["000001.SZ"]["shadow_tags"] == ["env_diff"]


def test_exdiv_void_and_pending(res):
    pc = res["per_code"]
    # win 键为 int 窗口号（落 JSON 时自然序列化为字符串，同 picks 链）
    assert pc[EXDIV_SYM]["win"][1] == {"r": None, "exec": "20261014",
                                       "void": "exdiv"}
    g = pc["000007.SZ"]
    assert g["win"][5] is None                       # 窗口未满 pending
    assert g["win"][1]["exec"] == "20261019"         # exec=信号+2


def test_per_code_latest_event_and_event_level_buckets():
    # 同票跨档=独立事件（四十审）：桶计两次，per_code 留最近一次
    rows = [
        {"trade_date": "20261012", "symbol": "000009.SZ", "action": "buy",
         "score": 60, "confidence": 0.5,
         "contributions": {"kg_boost": {"score": 60, "weight": 1.0,
                                        "action": "buy"}},
         "divergence": False, "rationale": "旧"},
        {"trade_date": "20261015", "symbol": "000009.SZ", "action": "buy",
         "score": 70, "confidence": 0.6,
         "contributions": {"kg_boost": {"score": 70, "weight": 1.0,
                                        "action": "buy"}},
         "divergence": False, "rationale": "新"},
    ]
    res = build(rows, CAL, _ret_table())
    assert res["summary"]["windows"]["1"]["n"] == 2     # 事件级双计
    assert res["per_code"]["000009.SZ"]["date"] == "20261015"
    assert res["per_code"]["000009.SZ"]["score"] == 70


def test_load_rows_file(tmp_path):
    p = tmp_path / "rows.json"
    p.write_text(json.dumps({"rows": _base_rows()}, ensure_ascii=False),
                 encoding="utf-8")
    rows = load_rows_file(str(p))
    assert len(rows) == 7
    assert all(isinstance(r["contributions"], dict) for r in rows)


def test_summary_caliber_self_desc(res):
    c = res["summary"]["caliber"]
    assert c["id"] == "exec-d2-open-20261010"
    assert "只标不拦" in c["layering"]
    assert res["summary"]["asof"] == "20261015"
    assert res["summary"]["events"] == 7


# ── psql 兜底读路径（mock 子进程，不连真实 PG） ─────────────────────────
def test_psql_rows_parses_csv_and_bool(monkeypatch):
    import subprocess as sp
    csv_out = (
        "trade_date,symbol,action,score,confidence,contributions,"
        "divergence,rationale\r\n"
        "20261012,000001.SZ,buy,70,0.8,\"{\"" + "\"kg: {}}\",t,x\r\n"
        "20261012,000002.SZ,sell,30,0.6,{},f,y\r\n"
    ).encode("utf-8")

    class P:
        returncode = 0
        stdout = csv_out
        stderr = b""

    seen = {}

    def fake_run(cmd, capture_output=True, timeout=None):
        seen["cmd"] = cmd
        return P()

    monkeypatch.setattr(sp, "run", fake_run)
    rows = _psql_rows("SELECT 1", ("docker", "exec", "pg", "psql"))
    # COPY TO STDOUT 包裹命令形态
    assert any("COPY (SELECT 1) TO STDOUT" in str(a) for a in seen["cmd"])
    assert len(rows) == 2
    assert rows[0]["symbol"] == "000001.SZ"
    # psql 布尔文本 t/f → bool
    assert rows[0]["divergence"] is True
    assert rows[1]["divergence"] is False


def test_psql_rows_nonzero_raises(monkeypatch):
    import subprocess as sp

    class P:
        returncode = 1
        stdout = b""
        stderr = "connection refused".encode("utf-8")

    monkeypatch.setattr(sp, "run", lambda *a, **k: P())
    with pytest.raises(RuntimeError, match="psql 兜底读失败"):
        _psql_rows("SELECT 1", ("docker", "exec", "pg", "psql"))


# ── benchmark 超额对照（回收 picks 链 window_mid） ──────────────────────
def test_load_benchmark_mid_recovers_table(tmp_path):
    doc = {"per_code": {"000001.SZ": {"win": {
        "1": {"r": 1.0, "base": -0.5, "exec": "20261013"},
        "3": {"r": None, "base": None, "exec": "20261013"},
        "5": None}}}}
    p = tmp_path / "pv.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    assert load_benchmark_mid(str(p)) == {("20261013", 1): -0.5}
    # 文件缺失/坏档 → 空表（显式跳过，不造数）
    assert load_benchmark_mid(str(tmp_path / "no.json")) == {}


def test_build_benchmark_excess_view():
    bench = {("20261014", 1): 0.5}
    rows = [r for r in _base_rows() if r["symbol"] == "000001.SZ"]
    res = build(rows, CAL, _ret_fn, benchmark=bench)
    w1 = res["per_code"]["000001.SZ"]["win"][1]
    assert w1["base"] == 0.5
    assert w1["ex"] == -1.5            # r=-1.0 - base=0.5
    assert res["summary"]["excess"]["1"]["n"] == 1
    assert res["summary"]["excess"]["1"]["avg"] == -1.5
    # 对照表缺口：mid 缺失 → ex=None 不入超额桶（不造数）
    assert res["summary"]["excess"]["3"]["n"] == 0
    assert res["summary"]["excess_caliber"].startswith("超额=")


def test_build_without_benchmark_shape_stable(res):
    # 无对照表：excess 恒空块，win 无 base/ex 键（向后兼容）
    assert res["summary"]["excess"]["1"]["n"] == 0
    assert res["summary"]["excess"]["1"]["avg"] is None
    assert "skip" in res["summary"]["excess_caliber"] or \
        "不可得" in res["summary"]["excess_caliber"]
    assert "base" not in res["per_code"]["000001.SZ"]["win"][1]


# ── shadow 读侧视图（只读消费口） ──────────────────────────────────────
def test_read_shadow_view(tmp_path, res):
    p = tmp_path / "cv.json"
    p.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    v = read_shadow_view(str(p))
    assert v["asof"] == "20261015"
    assert v["by_shadow"]["panic_buy"]["1"]["n"] == 2
    assert v["by_shadow"]["env_diff"]["1"]["n"] == 3
    assert v["shadow_codes"]["000003.SZ"] == {
        "date": "20261015", "action": "buy", "shadow_tags": ["panic_buy"]}
    # sell 事件的 env_diff 标注也被消费（只标不拦、标注可读）
    assert v["shadow_codes"]["000002.SZ"]["shadow_tags"] == ["env_diff"]
    # F 行同为 diff 日 buy 事件 → env_diff
    assert v["shadow_codes"]["000006.SZ"]["shadow_tags"] == ["env_diff"]


def test_read_shadow_view_missing_file(tmp_path):
    with pytest.raises(OSError):
        read_shadow_view(str(tmp_path / "no.json"))
