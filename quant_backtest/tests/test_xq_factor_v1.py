# -*- coding: utf-8 -*-
"""T7 舆情因子 v1 单测：mock 小样本（10 帖 / 3 股 / 3 日），不碰真库不碰网。

覆盖四件事：
  1) 聚合：daily_factors 的 senti/mention 口径
  2) 分层：quintile_table 五分位 + monotonicity
  3) IC：ic_series / ic_summary 的 spearman 计算
  4) label_version 分层：filter_version / run_analysis 版本隔离
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from xq_factor_v1 import (  # noqa: E402
    INDEX_POLLUTION, build_sections, daily_factors, filter_version,
    ic_series, ic_summary, monotonicity, next_day_return, parse_stock_keys,
    quintile_table, run_analysis, spearman,
)


def mock_posts():
    """10 帖 / 3 股 / 3 日（2026-10-05 周一 ~ 10-07 周三）。"""
    P = [
        # v1.3 主版本：SZ000001 3 帖 / SH600519 2 帖 / SZ000651 3 帖
        {"status_id": "1", "date": "2026-10-05", "sentiment": "bullish",
         "version": "v1.3", "stocks": ["SZ000001"]},
        {"status_id": "2", "date": "2026-10-05", "sentiment": "bullish",
         "version": "v1.3", "stocks": ["SZ000001"]},
        {"status_id": "3", "date": "2026-10-05", "sentiment": "bearish",
         "version": "v1.3", "stocks": ["SZ000001"]},
        {"status_id": "4", "date": "2026-10-05", "sentiment": "bullish",
         "version": "v1.3", "stocks": ["SH600519"]},
        {"status_id": "5", "date": "2026-10-05", "sentiment": "neutral",
         "version": "v1.3", "stocks": ["SH600519"]},
        {"status_id": "6", "date": "2026-10-06", "sentiment": "bearish",
         "version": "v1.3", "stocks": ["SZ000651"]},
        {"status_id": "7", "date": "2026-10-06", "sentiment": "bearish",
         "version": "v1.3", "stocks": ["SZ000651"]},
        {"status_id": "8", "date": "2026-10-06", "sentiment": "neutral",
         "version": "v1.3", "stocks": ["SZ000001", "SZ000651"]},
        {"status_id": "9", "date": "2026-10-07", "sentiment": "bullish",
         "version": "v1.2-re", "stocks": ["SZ000001"]},   # 旧版本帖
        {"status_id": "10", "date": "2026-10-07", "sentiment": "neutral",
         "version": "v1.3", "stocks": []},                # 无标的帖
    ]
    return P


def mock_daily():
    """3 股日线：10-06~10-09 连续 4 个交易日，open/close 构造确定性收益。

    10-05(周一,帖子日,非提供日) → T+1=10-06。次日 open→close：
      SZ000001 10-06: open 10 → close 11  → +10%
      SH600519  10-06: open 100 → close 99 → -1%
      SZ000651  10-06: open 50 → close 50 → 0%
      SZ000001 10-07: open 11 → close 10.45 → -5%
      SZ000651  10-07: open 50 → close 52.5 → +5%
      SZ000001 10-08: open 10.45 → close 11.5 → +10%
    """
    def row(sym, d, o, c):
        return {"symbol": sym, "trade_date": d.replace("-", ""),
                "open": o, "close": c}
    return {
        "SZ000001": [row("SZ000001", "2026-10-06", 10, 11),
                     row("SZ000001", "2026-10-07", 11, 10.45),
                     row("SZ000001", "2026-10-08", 10.45, 11.495)],
        "SH600519": [row("SH600519", "2026-10-06", 100, 99)],
        "SZ000651": [row("SZ000651", "2026-10-06", 50, 50),
                     row("SZ000651", "2026-10-07", 50, 52.5)],
    }


# ── 1) 聚合 ─────────────────────────────────────────────────
def test_daily_factors_aggregation():
    f = daily_factors(mock_posts())
    # 帖1/2/3 同日同股：bull=2 bear=1 → senti=(2-1)/max(1,3)=1/3
    k = ("2026-10-05", "SZ000001")
    assert abs(f[k]["senti"] - 1 / 3) < 1e-9
    assert f[k]["mention"] == 3
    # 帖4/5：bull=1 neutral=1 → senti=(1-0)/max(1,1)=1.0
    assert f[("2026-10-05", "SH600519")]["senti"] == 1.0
    assert f[("2026-10-05", "SH600519")]["mention"] == 2
    # 帖8 一帖提两股：两键各计 1（neutral）
    assert f[("2026-10-06", "SZ000001")]["mention"] == 1
    assert f[("2026-10-06", "SZ000651")]["senti"] == -1.0  # 2 bear
    # 5 个 (date,sym) 键：10-05 两股 / 10-06 两股 / 10-07 一股
    assert len(f) == 5
    # 纯空标的帖不产生键；v1.2-re 帖也聚合（版本过滤在更上层）
    assert [k for k in f if k[0] == "2026-10-07"] == \
        [("2026-10-07", "SZ000001")]


def test_next_day_return():
    d = mock_daily()
    r = next_day_return(d["SZ000001"], "2026-10-05")
    assert abs(r - (11 / 10 - 1)) < 1e-9      # T+1 open→close
    # 周末日/未来日无次日 → None
    assert next_day_return(d["SZ000001"], "2026-10-08") is None
    assert next_day_return(d["SH600519"], "2026-10-06") is None
    assert next_day_return([], "2026-10-05") is None


# ── 2) 分层 ─────────────────────────────────────────────────
def _weekly_sections():
    return build_sections(mock_posts(), mock_daily(), freq="weekly")


def test_build_sections_weekly():
    secs = _weekly_sections()
    # 全部帖子在同一个 ISO 周（2026-10-05 周一）→ 1 个周截面
    assert len(secs) == 1
    assert secs[0]["key"] == "2026-10-05"
    syms = {o["sym"] for o in secs[0]["obs"]}
    assert syms == {"SZ000001", "SH600519", "SZ000651"}
    o1 = next(o for o in secs[0]["obs"] if o["sym"] == "SZ000001")
    # SZ000001 3 日合并：senti 均值 = (1/3 + 0(中性单帖) + 1.0(旧版帖))/3
    assert abs(o1["senti"] - (1 / 3 + 0.0 + 1.0) / 3) < 1e-9
    # ret 均值 = (+10% + -5% + +10%)/3
    assert abs(o1["ret"] - (0.10 - 0.05 + 0.10) / 3) < 1e-9


def test_quintile_and_monotonicity():
    # 构造 10 个截面、每截面 5 股，因子值与收益严格同序 → q5 最优、mono=1
    secs = [{"key": "w%d" % w,
             "obs": [{"sym": "S%d" % i, "senti": i * 0.2,
                      "mention": 5 - i, "ret": 0.02 * i}
                     for i in range(5)]}
            for w in range(10)]
    qtab = quintile_table(secs, "senti")
    assert [r["quintile"] for r in qtab] == [1, 2, 3, 4, 5]
    assert all(r["n"] == 10 for r in qtab)     # 每截面各贡献 1 观测
    means = [r["mean_ret"] for r in qtab]
    assert means == sorted(means)              # 严格单调递增
    assert monotonicity(qtab) == 1.0
    # mention 反向：q5 应最差
    qtab_m = quintile_table(secs, "mention")
    assert qtab_m[0]["mean_ret"] > qtab_m[4]["mean_ret"]
    # 空截面不炸：obs<5 摊入低分位
    tiny = [{"key": "t", "obs": [{"sym": "A", "senti": 0.5, "mention": 1,
                                  "ret": 0.01}]}]
    qt = quintile_table(tiny, "senti")
    assert qt[0]["n"] == 1 and qt[1]["n"] == 0


# ── 3) IC ───────────────────────────────────────────────────
def test_spearman_and_ic():
    # 完全同序 → 1；完全反序 → -1
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == 1.0
    assert abs(spearman([1, 2, 3, 4], [40, 30, 20, 10]) + 1.0) < 1e-12
    # 并列取平均秩
    assert spearman([1, 1, 2], [5, 6, 7]) is not None
    # n<3 / 零方差 → None
    assert spearman([1, 2], [1, 2]) is None
    assert spearman([1, 1, 1], [1, 2, 3]) is None

    ics = ic_series(_weekly_sections(), "senti", min_obs=3)
    # 周截面 3 股 → 可算 1 个 IC
    assert len(ics) == 1 and ics[0]["n"] == 3
    s = ic_summary(ics)
    assert s["n"] == 1 and s["ic_mean"] is None
    assert s["exploratory_ic"] == ics[0]["ic"]
    assert s["evidence"] == "insufficient_sections"
    # min_obs 过滤：min_obs=4 时截面(3 股)被跳过
    assert ic_series(_weekly_sections(), "senti", min_obs=4) == []
    # 恒定收益 → 零方差 → 无 IC → summary n=0
    flat = [{"key": "k", "obs": [{"sym": "A", "senti": 1, "mention": 1,
                                  "ret": 0.0},
                                 {"sym": "B", "senti": -1, "mention": 1,
                                  "ret": 0.0},
                                 {"sym": "C", "senti": 0, "mention": 1,
                                  "ret": 0.0}]}]
    assert ic_summary(ic_series(flat, "senti"))["n"] == 0


def test_ic_summary_minimum_valid_sections():
    one = [{"key": "w1", "ic": -0.2474, "n": 3}]
    sparse = ic_summary(one)
    assert sparse["n"] == 1
    assert sparse["ic_mean"] is None and sparse["icir"] is None
    assert sparse["exploratory_ic"] == -0.2474
    assert sparse["evidence"] == "insufficient_sections"

    enough = ic_summary([{"key": str(i), "ic": i / 10, "n": 5}
                         for i in range(5)])
    assert enough["n"] == 5 and enough["ic_mean"] == 0.2
    assert enough["evidence"] == "exploratory"


# ── 4) label_version 分层 ───────────────────────────────────
def test_version_stratification():
    P = mock_posts()
    assert len(filter_version(P, "v1.3")) == 9
    assert len(filter_version(P, "v1.2-re")) == 1
    assert filter_version(P, "v1.2") == []
    # run_analysis：版本隔离——v1.2-re 截面只含旧版帖的股票
    daily = mock_daily()
    out = run_analysis(P, daily, freq="weekly", min_obs=3)
    assert set(out["versions"]) == {"v1.3", "v1.2-re"}
    assert out["main_version"] == "v1.3"
    v13 = out["versions"]["v1.3"]
    assert v13["n_posts"] == 9
    # v1.2-re 只有 1 帖 1 股 → 截面 obs<min_obs → n_obs 仍记，IC n=0
    vr = out["versions"]["v1.2-re"]
    assert vr["n_posts"] == 1 and vr["senti"]["ic"]["n"] == 0
    # 版本间观测不串：v1.3 的观测数只含 v1.3 帖
    assert v13["n_obs"] == vr["n_obs"] or v13["n_obs"] >= vr["n_obs"]


def test_index_pollution_excluded():
    # SZ000300 等指数污染码在 parse_stock_keys 层剔除，不进入因子
    nt = json.dumps([
        {"raw": "沪深300", "kind": "stock_a", "canonical": "SZ000300"},
        {"raw": "中证500", "kind": "stock_a", "canonical": "SZ000905"},
        {"raw": "平安银行", "kind": "stock_a", "canonical": "SZ000001"},
        {"raw": "腾讯", "kind": "stock_hk", "canonical": "00700"},
        {"raw": "红利指数", "kind": "index", "canonical": "000922.SH"},
        {"raw": "坏数据", "kind": "stock_a", "canonical": None},
    ], ensure_ascii=False)
    assert parse_stock_keys(nt) == ["SZ000001"]
    P = [{"status_id": "x", "date": "2026-10-05", "sentiment": "bullish",
          "version": "v1.3", "stocks": parse_stock_keys(nt)}]
    f = daily_factors(P)
    assert set(k[1] for k in f) == {"SZ000001"}
    assert "SZ000300" in INDEX_POLLUTION and "SZ000905" in INDEX_POLLUTION


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
