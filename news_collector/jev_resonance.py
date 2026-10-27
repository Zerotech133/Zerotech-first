# -*- coding: utf-8 -*-
"""JEV 情绪 × 行情行为共振实证校准表（E2，K-ORG-168）。

数据链复用 sentiment_backtest.run()（同源口径：news.db jev 判定 → A股
一码一日去重 → ts_daily T+1 行情），本模块对方向样本的 T+1 交易日补查
日线 vol/high/low/open，构建「方向样本 × 日内行为」交叉表，回答 E2
核心问题（借鉴 AI-CROWD/FinSPF 文本×价格 z-score 交叉验证 + 经典量价
行为判据）：

  1) 放量共振：T+1 放量（量比≥vol_cut）样本的命中率是否显著优于全体
     方向样本（确认信号有信息量，利多+放量=确认）？
  2) 分歧背离：冲高回落（长上影）/低开高走（长下影）样本命中率是否
     显著劣于全体（背离/反向指示，利多+冲高回落=分歧告警）？

实证达标（applied）才在 news_ai.py 下发运行时共振观察段（数据驱动：
不达标只出实证报告、运行时不启用，与 E1/E7 同纪律）。

输出：news_collector/data/jev_resonance.json（原子写）
  { generated_at, n_dir, hi_cut, vol_cut,
    base: {n, hit_pct1, dir_vs_mkt1},
    vol_all: {n, hit_pct1, dir_vs_mkt1, gain},
    shadow_all: {n, hit_pct1, dir_vs_mkt1, gain},
    low_open_all: {n, hit_pct1, dir_vs_mkt1, gain},
    applied: {vol: bool, shadow: bool},
    hint }

用法：python jev_resonance.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
QBT = os.path.normpath(os.path.join(HERE, "..", "quant_backtest"))
sys.path.insert(0, HERE)
sys.path.insert(0, QBT)
import sentiment_backtest as sbt  # noqa: E402  (复用 run/_load_samples 链)
import datamart                    # noqa: E402  (补查 vol/high/low/open)

RESULT = os.path.join(HERE, "data", "jev_resonance.json")
DIRS = {"bull": 1.0, "bear": -1.0}
HI_CUT = 0.7          # 对齐 _JEV_LOW_CONF（低置信不参与净倾向）
VOL_CUT = 1.3         # 量比阈值：T+1 成交量 / 前20交易日日均量
MIN_N = 30            # 分组最小样本数（不足不下发）
GAP = 0.05            # 增益阈值（同 E1 ASYM_PERSIST_GAP 量级）
SHADOW_RNG = 1.0      # 振幅% 阈值（日线近似日内波动显著）
SHADOW_RATIO = 0.35   # 上影/下影占振幅比例阈值（冲高回落/低开高走）


def _hit_pct1(rows):
    """方向样本 T+1 市场相对口径命中率（%）：DIR(bias)*vs_mkt1>0。

    vs_mkt1 = pct1 - 当日全市场均值（sbt 链已捕获），去市况污染，
    与 K-ORG-167（E1）市场相对口径一致。空组返回 (None, 0)。
    """
    if not rows:
        return None, 0
    hit = sum(1 for r in rows
              if r.get("vs_mkt1") is not None
              and DIRS[r["bias"]] * r["vs_mkt1"] > 0)
    return round(hit / len(rows) * 100, 1), len(rows)


def _dir_vs_mkt1(rows):
    """方向化超额收益均值（%）：DIR(bias)*vs_mkt1 平均。"""
    vals = [DIRS[r["bias"]] * r["vs_mkt1"] for r in rows
            if r.get("vs_mkt1") is not None]
    if not vals:
        return None
    return round(sum(vals) / len(vals), 2)


def _vol_base(vol, d8, tds):
    """d8 之前 20 个交易日均量；有效样本不足 10 个返回 None。"""
    if d8 not in tds:
        return None
    idx = tds.index(d8)
    if idx < 10:
        return None
    vals = [vol.get(d) for d in tds[max(0, idx - 20):idx]]
    vals = [v for v in vals if v]
    if len(vals) < 10:
        return None
    return sum(vals) / len(vals)


def main():
    res = sbt.run()
    if not res:
        print("RESONANCE no result (datamart/样本缺失)")
        return None
    rows = [r for r in res.get("rows", []) if r["bias"] in DIRS
            and r["conf"] >= HI_CUT and r.get("t1") is not None]
    n_dir = len(rows)
    if n_dir < MIN_N:
        print("RESONANCE 方向样本不足 n_dir=%d" % n_dir)
        return None
    days = sorted({r["day"] for r in rows})
    end = max(r["t1"] for r in rows)
    # 量能窗口需覆盖 T+1 前 20 交易日（早于最早新闻日），start 前推 60 天
    import datetime as _dt
    start = (_dt.datetime.strptime(days[0], "%Y-%m-%d")
             - _dt.timedelta(days=60)).strftime("%Y%m%d")
    df = datamart.read("daily", start=start, end=end,
                       fields=["ts_code", "trade_date", "vol", "open",
                               "high", "low", "close"])
    vol, hi, lo, op, close = {}, {}, {}, {}, {}
    if df is not None and not df.empty:
        for i in range(len(df)):
            code = df["ts_code"].iat[i]
            d8 = str(df["trade_date"].iat[i])[:8].replace("-", "")
            try:
                v = float(df["vol"].iat[i])
                if v > 0:
                    vol.setdefault(code, {})[d8] = v
            except Exception:
                pass  # 豁免（C2批2）：坏值行跳过，正常容错
            for bucket, col in ((hi, "high"), (lo, "low"),
                                (op, "open"), (close, "close")):
                try:
                    bucket.setdefault(code, {})[d8] = float(df[col].iat[i])
                except Exception:
                    pass  # 豁免（C2批2）：坏值行跳过，正常容错
    tds = [t for t in sbt._trade_days() if t <= end]

    enriched = []
    for r in rows:
        code, d8 = r["code"], r["t1"]
        v1 = vol.get(code, {}).get(d8)
        v0 = _vol_base(vol.get(code, {}), d8, tds)
        ratio = (v1 / v0) if (v1 and v0 and v0 > 0) else None
        h, l, o, c = (hi.get(code, {}).get(d8), lo.get(code, {}).get(d8),
                      op.get(code, {}).get(d8), close.get(code, {}).get(d8))
        up_shadow = dn_shadow = None
        if h and l and o and c and h > l:
            rng = (h - l) / l * 100
            if rng >= SHADOW_RNG:
                up_shadow = (h - max(o, c)) / (h - l)
                dn_shadow = (min(o, c) - l) / (h - l)
        r["vol_ratio"] = ratio
        r["up_shadow"] = up_shadow
        r["dn_shadow"] = dn_shadow
        enriched.append(r)

    def _hit_pct(rows):
        h, n = _hit_pct1(rows)
        return {"n": n, "hit_pct1": h, "dir_vs_mkt1": _dir_vs_mkt1(rows)}

    base = _hit_pct(enriched)
    vol_all = _hit_pct([r for r in enriched
                        if r.get("vol_ratio") is not None
                        and r["vol_ratio"] >= VOL_CUT])
    shadow_all = _hit_pct([r for r in enriched
                           if r.get("up_shadow") is not None
                           and r["up_shadow"] >= SHADOW_RATIO])
    low_open_all = _hit_pct([r for r in enriched
                             if r.get("dn_shadow") is not None
                             and r["dn_shadow"] >= SHADOW_RATIO])

    def _gain(grp):
        if grp.get("hit_pct1") is None or base.get("hit_pct1") is None:
            return None
        return round(grp["hit_pct1"] - base["hit_pct1"], 1)

    vol_applied = (vol_all["n"] >= MIN_N and _gain(vol_all) is not None
                   and _gain(vol_all) >= GAP * 100)
    shadow_applied = (shadow_all["n"] >= MIN_N
                      and base["hit_pct1"] is not None
                      and shadow_all["hit_pct1"] is not None
                      and base["hit_pct1"] - shadow_all["hit_pct1"]
                      >= GAP * 100)

    obj = {
        "generated_at": res["generated_at"],
        "n_total": res.get("n", 0),
        "n_dir": n_dir,
        "hi_cut": HI_CUT, "vol_cut": VOL_CUT,
        "base": base,
        "vol_all": dict(vol_all, gain=_gain(vol_all)),
        "shadow_all": dict(shadow_all, gain=_gain(shadow_all)),
        "low_open_all": dict(low_open_all, gain=_gain(low_open_all)),
        "applied": {"vol": bool(vol_applied), "shadow": bool(shadow_applied)},
        "hint": ("applied.vol=true 时 news_ai 下发「利多×放量上行=共振确认」"
                 "观察（放量组命中率显著高于全体）；applied.shadow=true 时"
                 "下发「利多×冲高回落=分歧警示」（冲高回落组命中率显著低于"
                 "全体）；未达标档位运行时不启用（数据驱动，同 E1/E7 纪律）。"),
    }
    sbt._write_json(RESULT, obj)
    print("JEV 共振实证表 %s  n_dir=%d  base_hit=%s%%  applied vol=%s shadow=%s"
          % (obj["generated_at"][:16], n_dir, base["hit_pct1"],
             vol_applied and "是" or "否", shadow_applied and "是" or "否"))
    for key, cn in (("base", "全体"), ("vol_all", "放量全体"),
                    ("shadow_all", "冲高回落"), ("low_open_all", "低开高走")):
        g = obj[key]
        print("  %-10s n=%-4d hit=%s%%  gain=%s%%  dir_vs_mkt=%s%%"
              % (cn, g["n"], g.get("hit_pct1"), g.get("gain"),
                 g.get("dir_vs_mkt1")))
    print("saved -> %s" % RESULT)
    return 0  # rc=0 正常（sys.exit 吃返回值）


if __name__ == "__main__":
    sys.exit(main())
