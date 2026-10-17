# -*- coding: utf-8 -*-
"""JEV 后验置信校准表（A2，K-ORG-163）。

数据链复用 sentiment_backtest.run()（同源口径：news.db jev 判定 → A股
样本一码一日去重 → ts_daily T+1 行情 → 方向命中标签），本模块只做
「conf 桶 → 实测 T+1 方向命中率」校准映射，纯后处理零成本（借鉴
CalibratedLM isotonic 桶映射哲学）。

输出：news_collector/data/jev_calib.json（原子写）
  { generated_at, n_total, conf_monotone,
    mapping: [{tier, n, n_dir, hit_pct1, calib_conf}], hint }
calib_conf = hit_pct1/100（实测命中率归一）——未来置信过滤/净倾向加权
改用 calib_conf 回写真实命中率，治 conf 0.9+ 右偏档虚高。

用法：python jev_calib.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sentiment_backtest as sbt  # noqa: E402  (复用其 run/_write_json)

RESULT = os.path.join(HERE, "data", "jev_calib.json")
DIRS = {"bull": 1.0, "bear": -1.0}
# 细桶对齐 K-ORG-156 分层（0.5~1.0），另加 <0.5 兜底桶
_TIERS = ((0.0, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.01))


def main():
    res = sbt.run()
    rows = res.get("rows", [])
    tiers = []
    for tlo, thi in _TIERS:
        sub = [r for r in rows if tlo <= r["conf"] < thi]
        dr = [r for r in sub if r["bias"] in DIRS]
        t = {"tier": "%.1f~%.1f" % (tlo, min(thi, 1.0)),
             "n": len(sub), "n_dir": len(dr)}
        if dr:
            hit = sum(1 for r in dr if r["pct1"] is not None
                      and DIRS[r["bias"]] * r["pct1"] > 0)
            t["hit_pct1"] = round(hit / len(dr) * 100, 1)
            t["calib_conf"] = round(t["hit_pct1"] / 100.0, 3)
        tiers.append(t)
    seq = [t["hit_pct1"] for t in tiers if t.get("hit_pct1") is not None]
    mono = all(b >= a for a, b in zip(seq, seq[1:])) if len(seq) >= 2 else None
    obj = {
        "generated_at": res["generated_at"],
        "n_total": res.get("n", 0),
        "conf_monotone": mono,
        "mapping": tiers,
        "hint": ("未来置信过滤/净倾向加权改用 calib_conf（实测命中率归一），"
                 "0.9+ 右偏档按实证回写真实命中率；样本不足档位不下发"),
    }
    sbt._write_json(RESULT, obj)
    print("JEV 后验校准表 %s  n=%d" % (obj["generated_at"][:16],
                                        res.get("n", 0)))
    print("单调=%s" % (mono is True and "是" or (mono is False and "否" or "样本不足")))
    for t in tiers:
        print("  %s  n=%d 方向n=%d 命中=%.1f%%  calib_conf=%s" % (
            t["tier"], t["n"], t["n_dir"], t.get("hit_pct1", 0.0),
            t.get("calib_conf", "-")))
    print("saved -> %s" % RESULT)
    return obj


if __name__ == "__main__":
    sys.exit(main())
