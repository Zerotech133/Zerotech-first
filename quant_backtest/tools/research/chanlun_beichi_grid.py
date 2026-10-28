# -*- coding: utf-8 -*-
"""
W3-4 · 缠论影子背驰标注阈值网格扫描（20261014）——只读产线，零产线行为改动。

目的: chanlun_signal.py 的影子标注（P5 盘整背驰 / P6-a MACD 背驰 / P6-b / P6-c，
      仅标注不拦截）一直未做系统评估。本脚本对最有代表性的两类做阈值网格，
      产出"标签启用门槛"建议：
  · P6-a MACD 背驰（1买，mbei）：参数化复刻 = 价创新低 + DIF 收敛幅度比 ≤ r
    （r=1.0、确认根数=1 时与产线 divergence() 完全一致，脚本内自校验）；
  · P5 盘整背驰（3买，bei）：回踩笔力度 < 离开笔力度 × r（r=1.0 即产线
    BEICHI_RATIO=1.0，直接对齐信号自带 bei 字段自校验）。

评估口径（与 v6c2 同源）:
  · 股票池: k_stock/data/poc_adapter_chanlun_20261012.json（76 只，同 v6c2）
  · 窗口 D0=20231010 → 20261012; use_hfq=true（config chanlun 节, kfac 后复权）
  · 信号事件: 复用 _v7_stream_cache.json 的 seen-ever 新鲜买点流（信号事件与
    阈值无关，无需重算; 仅取信号日 ≤ 20261012），对每个事件在其"新鲜检出日"
    用 tail(250) bars（与 v6c/v7 run_day 完全一致）补算标注原料
  · 前向收益: T+1 开盘买 → 持有 H∈{5,10,20} 交易日后收盘卖，双边成本 15bps
  · 对照: 同一事件集内「有背驰标注 vs 无标注(mbei/bei=False)」子集均值收益差;
    标注 None（窗口内无同向前序笔等无法判定）不计入任一子集、单独计数
  · 启用门槛判据: 半年度两段（窗口交易日中点分割）收益差同向且 |差| ≥ 1pp
    → 该阈值档值得启用为过滤器；否则维持仅标注。

纪律: 不 import 改写任何产线状态; chanlun_signal / chanlun_macd 仅只读调用。
解释器: C:/Program Files/Python311/python.exe；日志 /c/Temp/chanlun_beichi.log。
"""

import sys
import os
import json
import time
import logging
from collections import defaultdict

import pandas as pd

BASE = r"C:/项目/老田的AI助手"
QT = os.path.join(BASE, "quant_backtest")
DATA = os.path.join(QT, "data", "ashare")
POOL_SRC = os.path.join(BASE, "k_stock", "data", "poc_adapter_chanlun_20261012.json")
CFG = os.path.join(QT, "config.json")
STREAM_CACHE = os.path.join(QT, "_v7_stream_cache.json")
OUT = os.path.join(QT, "reports", "chanlun_beichi_grid.json")
LOGF = r"C:/Temp/chanlun_beichi.log"

D0, D_END = "20231010", "20261012"
WARMUP = 250
COST = 0.0015
HOLDS = [5, 10, 20]
GRID_P6A = [(r, n) for r in (0.8, 0.9, 1.0) for n in (1, 2)]  # 幅度比×确认根数
GRID_P5 = [0.8, 0.9, 1.0]  # 力度比

sys.path.insert(0, QT)
from chanlun_signal import analyze as chan_analyze  # noqa: E402
from chanlun_macd import macd_series  # noqa: E402

# ---------- 日志（stdout + 文件双写） ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOGF, mode="w", encoding="utf-8"),
    ],
)
log = logging.info

t0 = time.time()


def el():
    return round(time.time() - t0, 1)


# ---------- 数据装载（与 v6c/v7 同口径; use_hfq → kfac 后复权） ----------
cfg = json.load(open(CFG, encoding="utf-8"))
USE_HFQ = bool((cfg.get("chanlun") or {}).get("use_hfq", False))
assert USE_HFQ, "use_hfq 必须为 true（与产线/W1-2 口径一致）"

pool = json.load(open(POOL_SRC, encoding="utf-8"))
stocks = sorted({x["stock"] for x in pool})
frames = {}  # stock → DataFrame(含 adj_open/adj_close)
rets = {}
for s in stocks:
    f = os.path.join(DATA, s.replace(".", "_") + ".parquet")
    if not os.path.exists(f):
        continue
    b = pd.read_parquet(f).sort_values("trade_date").reset_index(drop=True)
    r = b["pct_chg"] / 100.0
    r.iloc[0] = 0.0
    kfac = (1.0 + r).cumprod() / b["close"]
    for col in ("open", "high", "low", "close"):
        b["adj_" + col] = b[col] * kfac
    frames[s] = b
    rets[s] = b.set_index("trade_date")["pct_chg"] / 100.0
log(f"[HFQ] 池 {len(stocks)} 只, 数据在场 {len(frames)} 只, D0={D0}→{D_END}")

# 窗口交易日历（半年度分割点用）
cal_all = sorted(set().union(*[set(b["trade_date"]) for b in frames.values()]))
cal = [d for d in cal_all if D0 <= d <= D_END]
split_date = cal[len(cal) // 2]
log(f"窗口交易日 {len(cal)} 天, 半年度分割点={split_date}")

# ---------- 事件流（复用 v7 缓存: 信号事件与阈值无关） ----------
cache = json.load(open(STREAM_CACHE, encoding="utf-8"))
events = []  # (stock, day, type)
for k in sorted(cache["buy_ev"].keys(), key=int):
    if int(k) > int(D_END):
        continue
    for s, tp, _bei in cache["buy_ev"][k]:
        events.append((s, k, tp))
by_type = defaultdict(int)
for _, _, tp in events:
    by_type[tp] += 1
log(f"事件流命中缓存: 新鲜买点 {len(events)} 个 {dict(by_type)}（信号日≤{D_END}）")


# ---------- 事件原料提取（每个事件一次 analyze, tail(WARMUP) 与 run_day 一致） ----------
def bars_for(s, day):
    b = frames[s]
    seg = b[b.trade_date <= day]
    if len(seg) < WARMUP // 2:
        return None
    return [
        {
            "dt": int(r.trade_date),
            "o": float(r.adj_open),
            "h": float(r.adj_high),
            "l": float(r.adj_low),
            "c": float(r.adj_close),
        }
        for r in seg.tail(WARMUP).itertuples()
    ]


def p6a_ingredients(bars, bis, sig):
    """P6-a 原料: 信号笔端点(价,DIF) + 最近≤2根同向前序笔端点(价,DIF)。"""
    ms = macd_series(bars)
    dt2i = {b["dt"]: i for i, b in enumerate(bars)}
    end_k = None
    for i, b in enumerate(bis):
        if b["e_dt"] == sig["dt"] and b["dir"] == -1:
            end_k = i
            break
    if end_k is None:
        return None
    i_this = dt2i.get(bis[end_k]["e_dt"])
    if i_this is None:
        return None
    this = (bis[end_k]["e_price"], ms[i_this]["dif"])
    prevs = []
    for j in range(end_k - 1, -1, -1):
        if bis[j]["dir"] == -1:
            ii = dt2i.get(bis[j]["e_dt"])
            if ii is None:
                return None
            prevs.append((bis[j]["e_price"], ms[ii]["dif"]))
            if len(prevs) == 2:
                break
    return {"this": this, "prevs": prevs}


def p5_ingredients(bis, sig):
    """P5 原料: [离开笔力度, 回踩笔力度]（与 _panzheng_beichi 同口径重算）。"""
    for i, b in enumerate(bis):
        if b["e_dt"] == sig["dt"] and b["dir"] == -1 and i > 0:
            leave = bis[i - 1]
            if leave["dir"] != 1:
                return None
            v_leave = abs(leave["e_price"] - leave["s_price"])
            v_pull = abs(b["e_price"] - b["s_price"])
            return [v_leave, v_pull]
    return None


ev_data = []
n_fail = 0
for s, day, tp in events:
    b = frames[s]
    bars = bars_for(s, day)
    if bars is None:
        n_fail += 1
        continue
    st = chan_analyze(bars) or {}
    sigs = st.get("signals") or []
    # 信号 dt 可能早于检出日; 取该事件类型在末态 bars 中的对应信号（seen-ever 下
    # 该信号必然存在于检出日的末态结构中, 同型同位去重后至多一个）
    cand = [x for x in sigs if x["type"] == tp]
    if not cand:
        n_fail += 1
        continue
    sig = cand[-1]
    rec = {
        "stock": s,
        "day": day,
        "type": tp,
        "sig_dt": sig["dt"],
        "prod_bei": sig.get("bei"),
        "prod_mbei": sig.get("mbei"),
    }
    if tp == "1买":
        rec["p6a"] = p6a_ingredients(bars, st["bis"], sig)
    elif tp == "3买":
        rec["p5"] = p5_ingredients(st["bis"], sig)
    ev_data.append(rec)
log(
    f"事件原料提取完成 {len(ev_data)}/{len(events)}（无法定位/数据不足 {n_fail}）[{el()}s]"
)


# ---------- 前向收益（T+1 开盘买 → 持 H 日收盘卖, 双边 15bps） ----------
def fwd_returns(s, day):
    b = frames[s]
    td = b["trade_date"].values
    pos = int(__import__("numpy").searchsorted(td, str(day), side="right"))  # T+1
    out = {}
    for H in HOLDS:
        ie, ix = pos, pos + H - 1
        if ix >= len(b):
            out[H] = None
            continue
        entry = float(b["adj_open"].iloc[ie])
        exitp = float(b["adj_close"].iloc[ix])
        out[H] = exitp * (1 - COST) / (entry * (1 + COST)) - 1.0
    return out


for rec in ev_data:
    rec["fwd"] = fwd_returns(rec["stock"], rec["day"])


# ---------- 参数化标签 ----------
def label_p6a(ing, ratio, nconf):
    """价创新低 + DIF 收敛幅度比 ≤ ratio, 对前 nconf 根同向前序笔均成立。
    前序 DIF<0 时条件 d_this > ratio*d_prev（r=1.0 与产线 d_this>d_prev 一致）;
    前序 DIF≥0（病态）退回产线严格不等式。原料不足(前序笔缺) → None。"""
    if ing is None or len(ing["prevs"]) < nconf:
        return None
    px, d = ing["this"]
    for k in range(nconf):
        pp, dp = ing["prevs"][k]
        if not px < pp:
            return False
        ok = d > ratio * dp if dp < 0 else d > dp
        if not ok:
            return False
    return True


def label_p5(fv, ratio):
    """回踩力度 < 离开力度 × ratio; 原料缺失 → None。"""
    if fv is None:
        return None
    return fv[1] < fv[0] * ratio


# ---------- 校验: 参数化标签(r=1.0,n=1) 对齐产线字段 ----------
chk_p6a = {"n": 0, "match": 0}
chk_p5 = {"n": 0, "match": 0}
for rec in ev_data:
    if rec["type"] == "1买" and rec["p6a"] is not None:
        chk_p6a["n"] += 1
        if label_p6a(rec["p6a"], 1.0, 1) == rec["prod_mbei"]:
            chk_p6a["match"] += 1
    if rec["type"] == "3买" and rec["p5"] is not None:
        chk_p5["n"] += 1
        if label_p5(rec["p5"], 1.0) == rec["prod_bei"]:
            chk_p5["match"] += 1
log(f"校验 P6-a(r=1.0,n=1) vs 产线 mbei: {chk_p6a['match']}/{chk_p6a['n']}")
log(f"校验 P5(r=1.0) vs 产线 bei: {chk_p5['match']}/{chk_p5['n']}")


# ---------- 聚合评估 ----------
def half_of(day):
    return "H1" if day < split_date else "H2"


def year_of(day):
    return day[:4]


def evaluate(recs, label_fn):
    """label_fn(rec) → True/False/None; 对每档 hold 输出两段收益差与分年对照。"""
    res = {}
    for H in HOLDS:
        lab = [r for r in recs if label_fn(r) is True and r["fwd"][H] is not None]
        unl = [r for r in recs if label_fn(r) is False and r["fwd"][H] is not None]
        none_n = sum(1 for r in recs if label_fn(r) is None)

        def mean(rs):
            return sum(r["fwd"][H] for r in rs) / len(rs) if rs else None

        m_lab, m_unl = mean(lab), mean(unl)
        seg = {}
        for sg in ("H1", "H2"):
            ls = [r for r in lab if half_of(r["day"]) == sg]
            us = [r for r in unl if half_of(r["day"]) == sg]
            ml, mu = mean(ls), mean(us)
            seg[sg] = {
                "n_beichi": len(ls),
                "n_none": len(us),
                "mean_beichi_pct": round(ml * 100, 2) if ml is not None else None,
                "mean_none_pct": round(mu * 100, 2) if mu is not None else None,
                "diff_pp": round((ml - mu) * 100, 2)
                if (ml is not None and mu is not None)
                else None,
            }
        yr = {}
        for y in sorted({year_of(r["day"]) for r in lab + unl}):
            ls = [r for r in lab if year_of(r["day"]) == y]
            us = [r for r in unl if year_of(r["day"]) == y]
            ml, mu = mean(ls), mean(us)
            yr[y] = {
                "n_beichi": len(ls),
                "n_none": len(us),
                "mean_beichi_pct": round(ml * 100, 2) if ml is not None else None,
                "mean_none_pct": round(mu * 100, 2) if mu is not None else None,
                "diff_pp": round((ml - mu) * 100, 2)
                if (ml is not None and mu is not None)
                else None,
            }
        d1, d2 = seg["H1"]["diff_pp"], seg["H2"]["diff_pp"]
        same_dir = (
            d1 is not None
            and d2 is not None
            and (d1 > 0) == (d2 > 0)
            and d1 != 0
            and d2 != 0
        )
        both_ge1 = same_dir and abs(d1) >= 1.0 and abs(d2) >= 1.0
        # 最小样本护栏: 两段各有 ≥5 个标注样本, 判定才有统计意义
        MIN_N = 5
        n_min = min(seg["H1"]["n_beichi"], seg["H2"]["n_beichi"])
        enough = n_min >= MIN_N
        if both_ge1 and d1 > 0:
            verdict = (
                "建议启用为过滤器"
                if enough
                else f"形式性通过但样本不足(最小段n={n_min}<{MIN_N})→维持仅标注待攒样本"
            )
        elif both_ge1:
            verdict = (
                "反向显著(有背驰更差,可考虑反向排除)"
                if enough
                else f"反向形式性通过但样本不足(最小段n={n_min}<{MIN_N})→维持仅标注"
            )
        else:
            verdict = "维持仅标注"
        res[f"H{H}"] = {
            "n_beichi": len(lab),
            "n_none": len(unl),
            "n_none_judge": none_n,
            "mean_beichi_pct": round(m_lab * 100, 2) if m_lab is not None else None,
            "mean_none_pct": round(m_unl * 100, 2) if m_unl is not None else None,
            "diff_pp": round((m_lab - m_unl) * 100, 2)
            if (m_lab is not None and m_unl is not None)
            else None,
            "segments": seg,
            "by_year": yr,
            "half_same_dir": bool(same_dir),
            "half_both_ge1pp": bool(both_ge1),
            "min_seg_n_beichi": n_min,
            "verdict": verdict,
        }
    return res


results = {"p6a": {}, "p5": {}}
recs_1b = [r for r in ev_data if r["type"] == "1买"]
recs_3b = [r for r in ev_data if r["type"] == "3买"]
log(f"P6-a 样本(1买)={len(recs_1b)}, P5 样本(3买)={len(recs_3b)}")

for r, n in GRID_P6A:
    key = f"ratio{r}_conf{n}"
    results["p6a"][key] = evaluate(
        recs_1b, lambda x, r=r, n=n: label_p6a(x["p6a"], r, n)
    )
    log(
        f"P6-a {key}: "
        + json.dumps(
            {f"H{H}": results["p6a"][key][f"H{H}"]["diff_pp"] for H in HOLDS},
            ensure_ascii=False,
        )
    )
for r in GRID_P5:
    key = f"ratio{r}"
    results["p5"][key] = evaluate(recs_3b, lambda x, r=r: label_p5(x["p5"], r))
    log(
        f"P5 {key}: "
        + json.dumps(
            {f"H{H}": results["p5"][key][f"H{H}"]["diff_pp"] for H in HOLDS},
            ensure_ascii=False,
        )
    )

# ---------- 事件级明细（供复核） ----------
detail = [
    {
        "stock": r["stock"],
        "day": r["day"],
        "type": r["type"],
        "prod_bei": r["prod_bei"],
        "prod_mbei": r["prod_mbei"],
        "fwd": {
            str(H): (round(v, 6) if v is not None else None)
            for H, v in r["fwd"].items()
        },
        "p6a": r.get("p6a"),
        "p5": r.get("p5"),
    }
    for r in ev_data
]

out = {
    "meta": {
        "D0": D0,
        "D_END": D_END,
        "split_date": split_date,
        "use_hfq": USE_HFQ,
        "cost_bps": 15,
        "holds": HOLDS,
        "warmup": WARMUP,
        "pool_n": len(stocks),
        "pool_with_data": len(frames),
        "stream_cache": os.path.basename(STREAM_CACHE),
        "events_total": len(ev_data),
        "events_by_type": dict(by_type),
        "sample": {"p6a_1buy": len(recs_1b), "p5_3buy": len(recs_3b)},
        "grid_p6a": [list(x) for x in GRID_P6A],
        "grid_p5": GRID_P5,
        "criterion": "半年度两段(H1/H2, 交易日中点分割)收益差同向且|差|≥1pp → 建议启用为过滤器; 否则维持仅标注",
        "label_def": {
            "p6a": "价创新低 + DIF 收敛幅度比: 前序DIF<0 时 d_this > ratio*d_prev (ratio=1.0 退化为产线严格不等式); 确认根数 n=前 n 根同向前序笔均须成立(含各自价创新低)",
            "p5": "回踩笔力度 < 离开笔力度 × ratio (力度=笔端点绝对价差, 与产线同口径)",
        },
        "design": "只读产线; 事件流复用 _v7_stream_cache.json(seen-ever 新鲜买点); 每事件在检出日 tail(250) bars 补算标注原料; 前向收益 T+1 开盘买持 H 日收盘卖双边15bps",
    },
    "validation": {"p6a_vs_prod_mbei": chk_p6a, "p5_vs_prod_bei": chk_p5},
    "results": results,
    "events_detail": detail,
}
os.makedirs(os.path.dirname(OUT), exist_ok=True)
json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
log(f"JSON: {OUT} [{el()}s]")

# ---------- 汇总打印（供留痕摘抄） ----------
print("\n===== 启用门槛判定汇总 =====")
for fam, grid in (
    ("P6-a MACD背驰(1买)", results["p6a"]),
    ("P5 盘整背驰(3买)", results["p5"]),
):
    for key, rr in grid.items():
        for H in HOLDS:
            e = rr[f"H{H}"]
            print(
                f"{fam} {key} H{H}: diff_H1={e['segments']['H1']['diff_pp']}pp "
                f"diff_H2={e['segments']['H2']['diff_pp']}pp "
                f"(n {e['segments']['H1']['n_beichi']}/{e['segments']['H2']['n_beichi']}) → {e['verdict']}"
            )
log(f"总耗时 {el()}s")
