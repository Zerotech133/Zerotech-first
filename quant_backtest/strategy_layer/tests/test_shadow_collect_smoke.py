# -*- coding: utf-8 -*-
"""S4 影子采集对接冒烟测试（shadow_collect × council_shadow 口径对齐）。

覆盖（S4 开闸前验收）：
1. 口径一致性：N_MIN / NS / BUCKETS 与 council_shadow 同名常量逐项相等；
   _bucket 分桶边界、_agg 聚合、Pearson r+t 统计与 council_shadow 实现
   逐值一致（相关公式镜像 council_shadow.compute_stats 内联段）。
2. 冒烟链路：合成 Signal → collect_once 落档（pytest tmp_path）→
   load_rows 回读 → compute_shadow_stats（fwd_fn 注入）→ 报告模板渲染，
   每步断言关键字段。
3. gate 三态：n<N_MIN 不可结论（样本积累中）、显著正向（转正评估）、
   显著反向（淘汰）、不显著（维持影子），均为确定性构造。
4. 边界：空输入 / 坏行 / trade_date 非法 fail-visible 不崩溃。

纪律：零 LLM 调用、零网络（前向收益全部 fwd_fn 注入）、零全局 patch
（不碰 time.sleep）、测试时间动态生成、唯一写副作用限定 pytest tmp_path。
"""
import json
import math
import os
import time
from datetime import date, timedelta

import numpy as np
import pytest

from strategy_layer.onboarding import shadow_collect as sc
from strategy_layer.signal import Signal

import council_shadow as cs  # noqa: E402  （conftest 已把 quant_backtest 置于 sys.path）


# ---------------------------------------------------------------------- #
# 合成数据工厂（numpy 固定种子；Signal 走真契约校验）
# ---------------------------------------------------------------------- #
ADAPTER_ID = "s4_smoke_strategy"


def _sig(symbol: str, action: str, score: float, td8: str) -> Signal:
    return Signal(symbol=symbol, action=action, score=score,
                  confidence=0.5, rationale="s4 冒烟合成信号",
                  strategy=ADAPTER_ID, trade_date=td8)


class _FakeAdapter:
    """duck-type ClassicStrategyAdapter：strategy_id + score(td8) -> [Signal]。"""

    def __init__(self, signals_by_date):
        self.strategy_id = ADAPTER_ID
        self._by_date = signals_by_date

    def score(self, trade_date):
        return list(self._by_date.get(str(trade_date).replace("-", ""), []))


def _mk_rows(n, seed, slope):
    """n 行合成影子档行（score 均匀铺满四分桶）+ 注入式前向收益表。

    r20 = slope*score + N(0,1)，r5 = 0.3*score；slope>0 → 显著正向，
    slope<0 → 显著反向。返回 (rows, fwd_map)，fwd_map 键为 trade_date。
    """
    rng = np.random.default_rng(seed)
    scores = np.linspace(2.0, 98.0, n)  # 覆盖 0-25/25-50/50-75/75-100 各桶
    base = date(2026, 1, 5)
    rows, fwd_map = [], {}
    for i, s in enumerate(scores):
        td = (base + timedelta(days=i)).strftime("%Y%m%d")
        rows.append({"symbol": "SMK.SH", "trade_date": td,
                     "score": round(float(s), 2), "confidence": 0.5,
                     "action": "buy"})
        fwd_map[td] = {5: round(0.3 * float(s) + float(rng.normal(0, 0.5)), 2),
                       20: round(slope * float(s) + float(rng.normal(0, 1.0)), 2)}
    return rows, fwd_map


def _council_style_corr(pairs):
    """逐行镜像 council_shadow.compute_stats 内联相关段
    （quant_backtest/council_shadow.py:138-147），用于口径一致性对拍。"""
    if len(pairs) < 3:
        return None
    n = len(pairs)
    mx = sum(x for x, _ in pairs) / n
    my = sum(y for _, y in pairs) / n
    vx = sum((x - mx) ** 2 for x, _ in pairs)
    vy = sum((y - my) ** 2 for _, y in pairs)
    if vx > 0 and vy > 0:
        r = sum((x - mx) * (y - my) for x, y in pairs) / math.sqrt(vx * vy)
        t = r * math.sqrt((n - 2) / max(1e-12, 1 - r * r))
        return {"n": n, "r": round(r, 3), "t": round(t, 2)}
    return None


# ---------------------------------------------------------------------- #
# 1. 口径一致性（shadow_collect vs council_shadow）
# ---------------------------------------------------------------------- #
def test_constants_parity_with_council_shadow():
    assert sc.NS == cs.NS == (5, 20)          # r5/r20 前向收益窗口
    assert sc.BUCKETS == cs.BUCKETS           # 四分桶边界命名
    assert sc.N_MIN == cs.N_MIN == 30         # 转正门槛样本量
    assert sc.SIG_T == 2.0                    # council 内联判据 abs(t)>=2


def test_bucket_boundary_parity_with_council_shadow():
    # 25/50/75 边界值归属必须逐点一致（左闭右开分桶）
    probes = [0.0, 1.0, 24.9, 25.0, 49.9, 50.0, 74.9, 75.0, 99.0, 100.0]
    for v in probes:
        assert sc._bucket(v) == cs._bucket(v)
    assert sc._bucket(24.9) == "0-25"
    assert sc._bucket(25.0) == "25-50"
    assert sc._bucket(50.0) == "50-75"
    assert sc._bucket(75.0) == "75-100"


def test_agg_parity_with_council_shadow():
    rows = [{"r5": 1.5, "r20": 2.0}, {"r5": -0.5, "r20": -1.0},
            {"r5": None, "r20": 3.0}, {"r5": 0.2, "r20": None},
            {"r5": None, "r20": None}]
    assert sc._agg(rows) == cs._agg(rows)
    a = sc._agg(rows)
    assert a["n"] == 5
    assert a["win5"] == round(100.0 * 2 / 3, 1)   # r5>0 记胜：1.5/0.2 共 2 笔
    assert a["r5"] == round((1.5 - 0.5 + 0.2) / 3, 2)
    assert a["r20"] == round((2.0 - 1.0 + 3.0) / 3, 2)


def test_corr_parity_with_council_shadow_and_numpy():
    datasets = [
        [(1.0, 2.0), (2.0, 4.1), (3.0, 5.9), (4.0, 8.2)],        # 近线性正向
        [(10.0, 5.0), (20.0, 3.0), (30.0, 8.0), (40.0, 1.0)],    # 弱相关
        [(1.0, 9.0), (2.0, 7.0), (3.0, 5.5), (4.0, 2.1)],        # 反向
        [(1.0, 5.0), (2.0, 5.0), (3.0, 5.0)],                    # vy=0 → None
    ]
    for pairs in datasets:
        assert sc._corr(pairs) == _council_style_corr(pairs)
        if len(pairs) >= 3:
            got = sc._corr(pairs)
            xs = np.array([x for x, _ in pairs])
            ys = np.array([y for _, y in pairs])
            if ys.std() > 0:
                expect_r = round(float(np.corrcoef(xs, ys)[0, 1]), 3)
                assert got["r"] == expect_r
    assert sc._corr([(1.0, 5.0), (2.0, 5.0)]) is None   # 样本 <3 不评判


# ---------------------------------------------------------------------- #
# 2. 冒烟链路：采集落档 → 统计 → 报告渲染（pytest tmp_path）
# ---------------------------------------------------------------------- #
def _four_bucket_signals(td8):
    # 四分桶各至少 1 条样本（幂等键 strategy|symbol|date → 每 symbol 一条）
    return [_sig("SMK1.SH", "buy", 10.0, td8),
            _sig("SMK2.SH", "sell", 40.0, td8),
            _sig("SMK3.SH", "buy", 60.0, td8),
            _sig("SMK4.SH", "watch", 90.0, td8)]


def test_collect_once_writes_jsonl_and_is_idempotent(tmp_path):
    td8 = "20261016"
    out = str(tmp_path / "shadow.jsonl")
    adapter = _FakeAdapter({td8: _four_bucket_signals(td8)})

    res1 = sc.collect_once(adapter, td8, out_path=out)
    assert res1["recorded"] == 4 and res1["replayed"] == 0
    assert res1["path"] == out
    assert os.path.exists(out)

    rows = sc.load_rows(out)
    assert len(rows) == 4
    for row in rows:
        assert row["strategy_id"] == ADAPTER_ID
        assert row["mode"] == "SHADOW"            # 影子产出标注，与产线隔离
        assert row["trade_date"] == td8
        assert row["shadow_key"] == "%s|%s|%s" % (ADAPTER_ID, row["symbol"], td8)
        assert isinstance(row["score"], float) and isinstance(row["confidence"], float)
        assert row["ts"]                          # 动态生成的时间戳
    assert {r["score"] for r in rows} == {10.0, 40.0, 60.0, 90.0}

    # 幂等：同日重采全部 replayed，不追加新行
    res2 = sc.collect_once(adapter, td8, out_path=out)
    assert res2["recorded"] == 0 and res2["replayed"] == 4
    with open(out, "r", encoding="utf-8") as f:
        assert sum(1 for line in f if line.strip()) == 4

    # 坏行跳过（load_rows 容错）
    with open(out, "a", encoding="utf-8") as f:
        f.write("not-a-json-line\n")
    assert len(sc.load_rows(out)) == 4

    # 零写副作用：未触碰仓库内 SHADOW_DIR（out_path 全程走 tmp_path）
    assert not os.path.exists(sc.shadow_path(ADAPTER_ID))


def test_full_chain_collect_stats_report(tmp_path):
    td8 = "20261016"
    out = str(tmp_path / "shadow.jsonl")
    adapter = _FakeAdapter({td8: _four_bucket_signals(td8)})
    sc.collect_once(adapter, td8, out_path=out)

    rows = sc.load_rows(out)
    fwd_map = {("SMK%d.SH" % i, td8): {5: 1.5, 20: 2.5} for i in range(1, 5)}
    stats = sc.compute_shadow_stats(
        rows, fwd_fn=lambda sym, td: fwd_map.get((sym, td)))

    assert stats["n_total"] == 4
    assert stats["n_have_fwd"] == 4
    assert [b["bucket"] for b in stats["by_score_bucket"]] == list(sc.BUCKETS)
    for b in stats["by_score_bucket"]:
        assert b["n"] == 1                     # 四分桶各桶 ≥1 条样本
        assert b["r5"] == 1.5 and b["r20"] == 2.5
        assert b["win5"] == 100.0
    # 样本 <N_MIN → 不可结论态而非崩溃
    assert stats["gate"]["status"] == "样本积累中·影子延期"
    assert stats["gate"]["n"] == 4 and stats["gate"]["need"] == sc.N_MIN
    assert stats["gate"]["corr"] is None and stats["corr"] is None
    assert stats["samples"][0]["trade_date"] == td8

    # 报告渲染：模板字符串填充（只读模板文件）
    tpl_path = os.path.join(os.path.dirname(sc.__file__),
                            "shadow_report_template.md")
    with open(tpl_path, "r", encoding="utf-8") as f:
        report = f.read()
    report = report.replace("{STRATEGY_ID}", ADAPTER_ID)
    report = report.replace("{REPORT_DATE}", time.strftime("%Y-%m-%d"))
    for b in stats["by_score_bucket"]:
        tline = "| %s | {N} | {R5} | {WIN5} | {R20} |" % b["bucket"]
        rline = "| %s | %s | %s | %s | %s |" % (
            b["bucket"], b["n"], b["r5"], b["win5"], b["r20"])
        assert tline in report
        report = report.replace(tline, rline)
    report = report.replace("{PAIRS_N}", str(stats["gate"].get("n") or 0))
    report = report.replace("{R}", "n/a").replace("{T}", "n/a")
    report = report.replace("{GATE_STATUS}", stats["gate"]["status"])

    assert "{GATE_STATUS}" not in report
    assert stats["gate"]["status"] in report
    for b in stats["by_score_bucket"]:
        assert "| %s | 1 | 1.5 | 100.0 | 2.5 |" % b["bucket"] in report


# ---------------------------------------------------------------------- #
# 3. gate 三态（确定性构造，n≥N_MIN=30）
# ---------------------------------------------------------------------- #
def _stats_for(rows, fwd_map):
    return sc.compute_shadow_stats(
        rows, fwd_fn=lambda sym, td: fwd_map.get(td))


def test_gate_significant_positive_promote():
    rows, fwd_map = _mk_rows(30, seed=20260101, slope=0.8)
    st = _stats_for(rows, fwd_map)
    assert st["n_total"] == 30 and st["n_have_fwd"] == 30
    assert st["gate"]["status"] == "显著正向·建议转正评估（人工拍板）"
    assert st["corr"]["t"] >= sc.SIG_T
    assert all(b["n"] >= 1 for b in st["by_score_bucket"])  # 各桶覆盖


def test_gate_significant_negative_retire():
    rows, fwd_map = _mk_rows(30, seed=20260202, slope=-0.8)
    st = _stats_for(rows, fwd_map)
    assert st["gate"]["status"] == "显著反向·建议淘汰"
    assert st["corr"]["t"] <= -sc.SIG_T


def test_gate_not_significant_hold_shadow():
    # r20 全常数 → vy=0 → corr=None → n≥N_MIN 落「不显著」分支（确定性）
    rows, fwd_map = _mk_rows(30, seed=20260303, slope=0.0)
    for td in fwd_map:
        fwd_map[td] = {5: 0.5, 20: 1.0}
    st = _stats_for(rows, fwd_map)
    assert st["corr"] is None
    assert st["gate"]["status"] == "不显著·维持影子（可再延一个观察窗）"
    assert st["gate"]["corr"] is None


def test_gate_below_n_min_inconclusive():
    rows, fwd_map = _mk_rows(10, seed=20260404, slope=0.8)
    # 前向收益窗口不足：2 个交易日无数据（fwd_fn 返回 None）
    for td in sorted(fwd_map)[:2]:
        fwd_map[td] = None
    st = _stats_for(rows, fwd_map)
    assert st["n_total"] == 10
    assert st["n_have_fwd"] == 8               # 缺窗口的样本只记录不评判
    assert st["gate"]["status"] == "样本积累中·影子延期"
    assert st["gate"]["n"] == 8 and st["gate"]["need"] == sc.N_MIN


# ---------------------------------------------------------------------- #
# 4. 边界：空输入 / 非法行 fail-visible
# ---------------------------------------------------------------------- #
def test_empty_rows_fail_visible():
    st = sc.compute_shadow_stats([])
    assert st["n_total"] == 0 and st["n_have_fwd"] == 0
    assert st["corr"] is None and st["samples"] == []
    assert st["gate"]["status"] == "样本积累中·影子延期"
    assert st["gate"]["n"] == 0 and st["gate"]["need"] == sc.N_MIN
    assert [b["n"] for b in st["by_score_bucket"]] == [0, 0, 0, 0]
    assert all(b["win5"] is None and b["r5"] is None and b["r20"] is None
               for b in st["by_score_bucket"])


def test_malformed_rows_skipped(tmp_path):
    p = tmp_path / "shadow.jsonl"
    p.write_text('{"symbol":"SMK.SH","trade_date":"20261016","score":50}\n'
                 'garbage-line\n'
                 '{"symbol":"SMK.SH","trade_date":"2026101","score":50}\n',
                 encoding="utf-8")
    rows = sc.load_rows(str(p))
    assert len(rows) == 2                       # 仅坏 JSON 行被 load_rows 跳过
    st = sc.compute_shadow_stats(
        rows, fwd_fn=lambda sym, td: {5: 1.0, 20: 2.0})
    assert st["n_total"] == 1                   # 7 位 trade_date 在统计层被跳过


def test_load_rows_missing_file_returns_empty(tmp_path):
    assert sc.load_rows(str(tmp_path / "nope.jsonl")) == []
    assert sc.load_rows(str(tmp_path / "nope_dir" / "nope.jsonl")) == []
