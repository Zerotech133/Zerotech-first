# -*- coding: utf-8 -*-
"""2026-11-11 · 24 课背驰标注前瞻收益对照（任务 #100，T3 评审前动作）。

问题：11-10 落地的 24 课背驰（bei24=盘整背驰·3买/3卖；tbe24=趋势背驰·1买/1卖）
标注在池内已非空，但**从未做前瞻收益验证**。本脚本第一次回答：
「带 24 课背驰标注的事件，之后涨得更好吗？」

数据：data/sig_events/sig_events_market_v3_20251111_20261111.json
  （11-11 15:14 全市场重扫，5170 只 / 13122 事件 / 16 workers / 205.5s）

⚠ 字段事实（2026-11-11 核实，影响解读）：
  * 事件缓存里 1买 档只落了 bei/bei2/bei24（全部 None——chanlun_signal
    给 1买 的是 tbe24/mbei，market_scan 的 out[(code,type)] 字典没带它们）。
    → 一买档趋势背驰的验证**在本缓存上不可行**，只能验证 3买 的 bei24。
  * 3买 1163 只：bei24=True 362 / False 801 —— 可做 362 vs 801 对照。
  * day≠dt 全量成立（延伸笔口径），**对齐分析一律用 dt**（铁律）。

口径：
  * 前瞻窗口：信号 dt 之后 5/20 个交易日（不含信号日本身）。
  * 前瞻收益：close(t+5)/close(t+1) − 1（t+1 起算，模拟 T+1 开盘可买）。
  * 超额 = 个股前瞻收益 − 同 dt 全体 3买事件等权前瞻收益（同日市场/风格漂移剥离）。
  * 复合：log-cumsum 后 expm1（不许简单收益相除）。
  * 零成本口径，如实标注（成本 122bp 往返会吃掉约 1/3 的 5 日级别超额）。
  * t 值 = mean/se，se 用逐事件超额的标准误（不做 Newey-West，样本说明）。

产物：reports/beichi24_forward_20261111.json
"""
import json
import math
import os
from collections import defaultdict

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(HERE, "data", "sig_events",
                   "sig_events_market_v3_20251111_20261111.json")
OUT = os.path.join(HERE, "reports", "beichi24_forward_20261111.json")
HOR = (5, 20)
COST_NOTE = "零成本口径；122bp 往返未扣（5 日窗约吃掉 1/3 超额）"


def load_closes():
    """全宇宙收盘价矩阵：{stock: {day: close}}（原始价，只用于收益比，量纲无关）。"""
    import pandas as pd
    data_dir = os.path.join(HERE, "data", "ashare")
    stocks = set()
    j = json.load(open(SRC, encoding="utf-8"))
    for e in j["events"]:
        stocks.add(e["stock"])
    closes = {}
    for s in stocks:
        fp = os.path.join(data_dir, s.replace(".", "_") + ".parquet")
        if not os.path.exists(fp):
            continue
        b = pd.read_parquet(fp, columns=["trade_date", "close"])
        closes[s] = dict(zip(b["trade_date"].astype(str), b["close"].astype(float)))
    return closes


def main():
    j = json.load(open(SRC, encoding="utf-8"))
    ev = j["events"]
    closes = load_closes()

    # 日历（用平安银行文件）
    import pandas as pd
    cal = pd.read_parquet(os.path.join(HERE, "data", "ashare", "000001_SZ.parquet"),
                          columns=["trade_date"])["trade_date"].astype(str).tolist()
    cal_idx = {d: i for i, d in enumerate(cal)}

    def fwd(stock, dt, n):
        """dt 后 n 个交易日收益（t+1 买入口径）。"""
        m = closes.get(stock)
        if not m or dt not in cal_idx:
            return None
        i0 = cal_idx[dt]
        i1, i2 = i0 + 1, i0 + 1 + n
        if i2 >= len(cal):
            return None
        d1, d2 = cal[i1], cal[i2]
        a, b = m.get(d1), m.get(d2)
        if not a or not b:
            return None
        return b / a - 1.0

    # 只验 3买（bei24 有对照）；1买 缓存无 tbe24 字段——字段缺口如实登记
    rows3 = [e for e in ev if e.get("type") == "3买"]
    # dt 规范成 str
    for e in rows3:
        e["_dt"] = str(e.get("dt") or e.get("day"))

    res = {"meta": {
        "src": os.path.basename(SRC), "scan_meta": j.get("meta"),
        "horizons": HOR, "cost_note": COST_NOTE,
        "field_facts": {
            "bei24_true": sum(1 for e in rows3 if e.get("bei24")),
            "bei24_false": sum(1 for e in rows3 if not e.get("bei24")),
            "note_1buy": "1买档事件缓存未落 tbe24/mbei 字段（market_scan out 字典缺键），"
                         "趋势背驰验证不可行，本报告只覆盖 3买 bei24",
        },
    }, "groups": {}}

    for n in HOR:
        # 逐日等权基准（同 dt 全体 3买）
        day_rets = defaultdict(list)
        for e in rows3:
            r = fwd(e["stock"], e["_dt"], n)
            if r is not None:
                day_rets[e["_dt"]].append(r)
        day_mean = {d: sum(v) / len(v) for d, v in day_rets.items() if v}

        def bucket(flag):
            xs, raws = [], []
            for e in rows3:
                if bool(e.get("bei24")) is not flag:
                    continue
                r = fwd(e["stock"], e["_dt"], n)
                bm = day_mean.get(e["_dt"])
                if r is None or bm is None:
                    continue
                xs.append(r - bm)
                raws.append(r)
            if not xs:
                return None
            mean = sum(xs) / len(xs)
            var = sum((x - mean) ** 2 for x in xs) / max(1, len(xs) - 1)
            se = math.sqrt(var / len(xs)) if var > 0 else None
            # 复合超额：log-cumsum
            comp = math.expm1(sum(math.log1p(x) for x in raws) / len(raws))
            comp_bench = math.expm1(
                sum(math.log1p(day_mean[e["_dt"]])
                    for e in rows3
                    if bool(e.get("bei24")) is flag and e["_dt"] in day_mean)
                / max(1, sum(1 for e in rows3
                             if bool(e.get("bei24")) is flag and e["_dt"] in day_mean)))
            return {
                "n": len(xs), "mean_excess_bp": round(mean * 1e4, 1),
                "t": round(mean / se, 2) if se else None,
                "raw_fwd_pct": round(comp * 100, 2),
                "bench_fwd_pct": round(comp_bench * 100, 2),
            }

        res["groups"]["h%d" % n] = {
            "bei24_true": bucket(True), "bei24_false": bucket(False),
        }

    json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(res, ensure_ascii=False, indent=1))
    print("\n产物:", OUT)


if __name__ == "__main__":
    main()
