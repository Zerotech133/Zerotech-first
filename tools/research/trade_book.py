# -*- coding: utf-8 -*-
"""模拟交易账本（trade_book）：把缠论 paper 台账接成平台可溯源账本。

**为什么复用台账而不是重造**

仓库里早就有缠论的 paper 台账（`chanlun_paper.json` / `chanlun_paper_full.json`），
由 `chanlun_scan.py` 在产线里逐日写入。它已经天然满足「可溯源」：

    sig_dt（笔终点，结构成立的信号日）
      → detect_date（产线检测到该笔的日子，通常晚 lag 个交易日）
      → entry_date / entry_px（实际入场日与价）
      → exit_date / exit_px / exit_reason（出场）

重造一份只会多一套口径。本脚本只做四件事：**过滤、接入行业、标注势档、标注 v3 合规**。

**口径（必须一起读）**

- 主账本用 `chanlun_paper_full.json`（n_rows=11626，池全量重建，绕过 CLOSED_CAP=5000
  截留）；`chanlun_paper.json` 只用来标注「这笔在产线台账里是否仍在途」。
  两者 entry = 检出日收盘，exit = 反向信号确认日收盘或满 20 日收盘（hold20）。
- **台账双向**：1/2/3买 是多头信号，1/2/3卖 是减仓提示。A 股不能裸卖空，
  所以 `side=sell_hint` 的那部分**不是一笔可执行的空单**，只用于「该减了」的提示。
  混在一起算「买了多少」会把卖点当成买入，这是本脚本要显式分开的原因。
- 势档用中证全指 `000985.CSI` 的 20 日动量，阈值 ±2%：
  `弱 < -2%`、`强 > +2%`、其余 `中`。判定取 **entry_date 当日收盘**，与
  `shadow_book.py` 的 `reg()` 完全同源，否则两处结论会打架。
- **v3 合规** = 1买 且 势≠弱 且 持有期符合（强持 8 日 / 中持 1 日）。这只是标注，
  不改变台账本身的盈亏；台账是「产线实际怎么走」，v3 是「规则说该怎么走」。

**输出**

- `portal/trade_book.json`        摘要 + 当前持仓 + 分组聚合 + 每票聚合（首屏用）
- `portal/trade_trades.json`      已平仓逐笔（精简字段，点开个股时取）

用法::

    python -X utf8 tools/research/trade_book.py
    python -X utf8 tools/research/trade_book.py --degrade   # 退化副本，自检用
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import statistics
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools", "research"))

import hf_cost as HC          # 唯一的成本口径（第十八轮），本文件不再自己算成本

QBT = os.path.join(ROOT, "quant_backtest", "data")
TDX = os.path.join(QBT, "tdx")
FULL = os.path.join(QBT, "chanlun_paper_full.json")
PAPER = os.path.join(QBT, "chanlun_paper.json")
OUT = os.path.join(ROOT, "portal", "trade_book.json")
OUT_TRADES = os.path.join(ROOT, "portal", "trade_trades.json")

IDX_CODE = "000985.CSI"
TH = 0.02
HOLD_BY_REGIME = {"强": 8, "中": 1}
BUY_TYPES = ("1买", "2买", "3买")
SELL_TYPES = ("1卖", "2卖", "3卖")


# ---------------------------------------------------------------- 指数与势档
def load_index(n=20):
    """→ (axis, close, mom20)。axis 只含指数有收盘的交易日。"""
    import pandas as pd
    clos = {}
    for p in sorted(glob.glob(os.path.join(TDX, "ts_index_daily", "*.parquet"))):
        d = pd.read_parquet(p, columns=["ts_code", "trade_date", "close"])
        d = d[d["ts_code"] == IDX_CODE]
        for dt, c in zip(d["trade_date"].astype(str), d["close"].astype(float)):
            if math.isfinite(c) and c > 0:
                clos[dt.replace("-", "")[:8]] = c
    clos = dict(sorted(clos.items()))
    ds = list(clos)
    mom = {ds[i]: clos[ds[i]] / clos[ds[i - n]] - 1.0 for i in range(n, len(ds))}
    return ds, clos, mom


def load_index_codes():
    """指数黑名单。

    `ts_daily` 是分片 parquet（part_0000..0061）而不是按代码分文件，所以拿不到
    「个股清单」。但台账里混进来的只有指数，改用 `ts_index_daily` 的 ts_code 集合
    反向排除更省——`000001.SH`（上证指数）与 `000688.SH`（科创50）就靠这个剔掉，
    而 `000001.SZ`（平安银行）不受影响。
    """
    import pandas as pd
    bad = set()
    for p in glob.glob(os.path.join(TDX, "ts_index_daily", "*.parquet")):
        d = pd.read_parquet(p, columns=["ts_code"])
        bad |= set(d["ts_code"].astype(str).unique())
    return bad


# ---------------------------------------------------------------- 行业
def load_industry(asof):
    """申万一级行业（PIT）。返回 (dict code→name, 覆盖率说明)。"""
    try:
        from pit_industry import load_sw_l1_industry
        l1 = load_sw_l1_industry()
        s = l1.at(asof)
        return {str(k): str(v) for k, v in s.items()}, "SW-L1 PIT @ %s" % asof
    except Exception as e:                                   # noqa: BLE001
        print("  ! 行业映射加载失败：%s" % e)
        return {}, "UNAVAILABLE"


# ---------------------------------------------------------------- 账本
def _d(s):
    return str(s or "").replace("-", "")[:8]


def build(full, paper, universe, ind, mom, axis, degrade=False,
          comm_bp=None, slip_bp=0.0):
    """把台账行整理成统一结构。degrade=True 时故意把溯源字段抹掉，供自检确认断言有效。

    universe 这里是**要排除的指数集合**（见 load_index_codes）。
    axis 是交易日历，用来给「未出场」的笔补算已持有交易日数——台账只给已平仓的
    days_held，在途笔是 None，前端显示「持 None 日」等于没信息。

    成本（第十八轮）：台账的 `ret` 是**零成本毛收益**（实测 ret 与
    `exit_px/entry_px - 1` 逐位相同，差 0.0bp），`alpha` 也是毛的。
    这里额外算一份 `ret_net` / `alpha_net`，**旧的 ret/alpha 一个字不改**——
    并行 session 正在看那两个数，静默改数比改错更难查。
    """
    open_keys = {(r["code"], r["type"], _d(r["sig_dt"])) for r in paper.get("open") or []}
    asof = axis[-1] if axis else None
    _AX_POS = {d: i for i, d in enumerate(axis)}

    def held_now(ed):
        """entry → as-of 的交易日数；as-of 当天入场的算 1 日。"""
        if not asof or ed not in _AX_POS:
            return None
        return _AX_POS[asof] - _AX_POS[ed] + 1

    def reg_of(d):
        m = mom.get(d)
        if m is None:
            return None, None
        if degrade:                                    # 退化：势档一律判强，v3 会全放行
            return "强", 0.05
        return ("弱" if m < -TH else ("强" if m > TH else "中")), m

    rows, dropped = [], Counter()
    for r in full:
        code = r.get("code") or ""
        if code in universe:
            dropped["index"] += 1
            continue
        if not r.get("entry_date") or not r.get("entry_px"):
            dropped["no_entry"] += 1
            continue
        ed = _d(r["entry_date"])
        g, m = reg_of(ed)
        typ = r.get("type") or ""
        side = "buy" if typ in BUY_TYPES else ("sell_hint" if typ in SELL_TYPES else "other")
        v3_ok = bool(side == "buy" and typ == "1买" and g in HOLD_BY_REGIME)
        why = []
        if not v3_ok:
            if side != "buy":
                why.append("非买点（%s 是减仓提示）" % typ)
            elif typ != "1买":
                why.append("v3 只做 1买，此为 %s" % typ)
            elif g == "弱":
                why.append("弱市不做")
        rec = {
            "code": code,
            "name": r.get("name") or "",
            "ind": ind.get(code, "未分类"),
            "type": typ,
            "side": side,
            "sig_dt": _d(r.get("dt")),
            "detect": _d(r.get("detect_date")),
            "lag": r.get("lag"),
            "entry": ed,
            "entry_px": round(float(r["entry_px"]), 3),
            "reg": g,
            "mom20": None if m is None else round(m * 100, 2),
            "vv": r.get("vv"), "bei": r.get("bei"), "bei2": r.get("bei2"),
            "tbe": r.get("tbe"), "npv": r.get("npv"),
            "breg": r.get("reg"),                      # 笔方向 up/down/flat，不是市势
            "v3_ok": v3_ok,
            "v3_why": "；".join(why),
            "status": r.get("status"),
            # in_paper：这笔在**产线台账**里确实还挂着（真在途）；未出场但
            # in_paper=False 的是「重建出来还开着、产线没这笔」。混在一起报
            # 「当前持仓 N 笔」会高估真实在途规模。
            "in_paper": (code, typ, _d(r.get("dt"))) in open_keys,
            # 卖点是减仓提示：A 股不能裸卖空，它的 ret/alpha 是「做空口径」，
            # 数字再好看也不可执行，前端必须标出来。
            "exec": side == "buy",
        }
        if r.get("exit_date"):
            rec.update({
                "exit": _d(r["exit_date"]),
                "exit_px": round(float(r["exit_px"]), 3) if r.get("exit_px") else None,
                "held": r.get("days_held"),
                "reason": r.get("exit_reason"),
                "ret": None if r.get("ret") is None else round(r["ret"] * 100, 3),
                "alpha": None if r.get("alpha") is None else round(r["alpha"] * 100, 3),
                "idx_ret": None if r.get("idx_ret") is None else round(r["idx_ret"] * 100, 3),
                "beta": r.get("beta"),
                # 成本口径（第十八轮）：只新增，不改上面四个字段。
                # alpha_net = net_ret − β×br，而 β×br = ret − alpha，
                # 所以直接减 (ret − alpha) 即可，不必重取基准。
                "ret_net": None if r.get("ret") is None else round(
                    HC.net_ret(r["ret"], comm_bp, slip_bp) * 100, 3),
                "alpha_net": (None if (r.get("ret") is None or r.get("alpha") is None)
                              else round((HC.net_ret(r["ret"], comm_bp, slip_bp)
                                          - (r["ret"] - r["alpha"])) * 100, 3)),
            })
        else:
            rec.update({"exit": None, "exit_px": None, "held": held_now(ed),
                        "reason": None, "ret": None, "alpha": None,
                        "idx_ret": None, "beta": r.get("beta"),
                        "ret_net": None, "alpha_net": None})
        if degrade:
            rec["sig_dt"] = ""
            rec["detect"] = ""
        rows.append(rec)

    if degrade:                                        # 退化：把已平仓的出场原因抹掉
        for r in rows:
            if r["exit"]:
                r["exit"] = ""
    rows.sort(key=lambda x: (x["code"], x["type"], x["sig_dt"]))
    return rows, dict(dropped)


# ---------------------------------------------------------------- 聚合
def _stat(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return {"n": 0, "mean": None, "median": None, "win": None}
    return {"n": len(xs), "mean": round(statistics.mean(xs), 3),
            "median": round(statistics.median(xs), 3),
            "win": round(sum(1 for x in xs if x > 0) / len(xs) * 100, 1)}


def _lag(xs):
    """信号日 → 检测日 的交易日间隔分布。

    这是「信号能不能用」的核心代价：dt 是笔的终点，笔要等后面的笔确认，
    所以产线看到信号时已经过去一到两周。把中位数和 p95 一起报，
    因为尾部（最长 60 日）才是会亏钱的那部分。
    """
    if not xs:
        return {"n": 0}
    xs = sorted(xs)
    pick = lambda f: xs[min(len(xs) - 1, int(f * len(xs)))]  # noqa: E731
    return {"n": len(xs), "p50": pick(0.5), "p95": pick(0.95), "max": xs[-1],
            "le3": round(sum(1 for x in xs if x <= 3) / len(xs) * 100, 1),
            "gt20": round(sum(1 for x in xs if x > 20) / len(xs) * 100, 1)}


def aggregate(rows):
    closed = [r for r in rows if r["exit"] and r["ret"] is not None]
    opens = [r for r in rows if not r["exit"]]

    def bucket(keyfn):
        g = defaultdict(list)
        for r in rows:
            g[keyfn(r)].append(r)
        out = {}
        for k, xs in g.items():
            cl = [x for x in xs if x["exit"] and x["ret"] is not None]
            out[str(k)] = {
                "n": len(xs), "n_closed": len(cl), "n_open": len(xs) - len(cl),
                "ret": _stat([x["ret"] for x in cl]),
                "alpha": _stat([x["alpha"] for x in cl if x["alpha"] is not None]),
                "ret_net": _stat([x["ret_net"] for x in cl if x.get("ret_net") is not None]),
                "alpha_net": _stat([x["alpha_net"] for x in cl
                                    if x.get("alpha_net") is not None]),
            }
        return dict(sorted(out.items(), key=lambda kv: -kv[1]["n"]))

    # 每票聚合：前端「点板块 → 看股票」用的就是这张表
    per = defaultdict(list)
    for r in rows:
        per[r["code"]].append(r)
    stocks = []
    for code, xs in per.items():
        xs.sort(key=lambda z: (z["entry"], z["type"]))
        last = xs[-1]
        cl = [x for x in xs if x["exit"] and x["ret"] is not None]
        stocks.append({
            "code": code, "name": last["name"], "ind": last["ind"],
            "type": last["type"], "side": last["side"],
            "n": len(xs), "n_closed": len(cl),
            "last_entry": last["entry"], "last_exit": last["exit"],
            "last_ret": last["ret"], "last_reason": last["reason"],
            "last_reg": last["reg"], "v3_ok": last["v3_ok"],
            "sum_ret": round(sum(x["ret"] for x in cl), 2) if cl else None,
            "win": round(sum(1 for x in cl if x["ret"] > 0) / len(cl) * 100, 1) if cl else None,
            "open": not last["exit"],
        })
    stocks.sort(key=lambda z: (-z["n"], z["code"]))

    # 可执行 vs 不可执行必须分开报。台账里 1/2/3卖 占 64%，它们的 ret/alpha 是
    # 「做空口径」，A 股裸卖空不存在。合并统计会把不可执行的那部分算成业绩。
    buy_cl = [r for r in closed if r["exec"]]
    sell_cl = [r for r in closed if not r["exec"]]

    return {
        "n_rows": len(rows), "n_closed": len(closed), "n_open": len(opens),
        "n_open_paper": sum(1 for r in opens if r["in_paper"]),
        "n_buy": sum(1 for r in rows if r["exec"]),
        "n_sell_hint": sum(1 for r in rows if not r["exec"]),
        "by_type": bucket(lambda r: r["type"]),
        "by_side": bucket(lambda r: r["side"]),
        "by_regime": bucket(lambda r: r["reg"] or "未知"),
        "by_industry": bucket(lambda r: r["ind"]),
        "ret": _stat([r["ret"] for r in closed]),
        "alpha": _stat([r["alpha"] for r in closed if r["alpha"] is not None]),
        "ret_buy": _stat([r["ret"] for r in buy_cl]),
        "alpha_buy": _stat([r["alpha"] for r in buy_cl if r["alpha"] is not None]),
        "ret_sell": _stat([r["ret"] for r in sell_cl]),
        "alpha_sell": _stat([r["alpha"] for r in sell_cl if r["alpha"] is not None]),
        # ---- 成本口径（第十八轮）：上面六个是毛的，下面六个是净的 ----
        "ret_net": _stat([r["ret_net"] for r in closed if r.get("ret_net") is not None]),
        "alpha_net": _stat([r["alpha_net"] for r in closed
                            if r.get("alpha_net") is not None]),
        "ret_buy_net": _stat([r["ret_net"] for r in buy_cl if r.get("ret_net") is not None]),
        "alpha_buy_net": _stat([r["alpha_net"] for r in buy_cl
                                if r.get("alpha_net") is not None]),
        "ret_sell_net": _stat([r["ret_net"] for r in sell_cl
                               if r.get("ret_net") is not None]),
        "alpha_sell_net": _stat([r["alpha_net"] for r in sell_cl
                                 if r.get("alpha_net") is not None]),
        "n_v3_ok": sum(1 for r in rows if r["v3_ok"]),
        "lag": _lag([r["lag"] for r in closed if r.get("lag") is not None]),
        "stocks": stocks,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", default=None, help="行业映射取点，默认取台账最新入场日")
    ap.add_argument("--degrade", action="store_true", help="退化副本（自检用）")
    ap.add_argument("--slip", type=float, default=51.0,
                    help="单边滑点 bp，默认 51（hf_phase5 全成本档）；0 = 只扣佣金")
    ap.add_argument("--comm", type=float, default=None,
                    help="佣金双边 bp，默认 20（买 7.5/卖 12.5）；0 = 完全不扣佣金")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--out-trades", default=OUT_TRADES)
    a = ap.parse_args()

    print("=" * 92)
    print("模拟交易账本｜trade_book%s" % ("（退化副本）" if a.degrade else ""))
    print("=" * 92)

    full = json.load(open(FULL, encoding="utf-8"))["rows"]
    paper = json.load(open(PAPER, encoding="utf-8"))
    print("  台账 full  %d 行（%s）" % (len(full), json.load(open(FULL, encoding="utf-8")).get("note", "")[:40]))
    print("  台账 paper %d 在途 / %d 已平 / %d 被拦"
          % (len(paper.get("open") or []), len(paper.get("closed") or []),
             len(paper.get("entry_blocked") or [])))

    axis, clos, mom = load_index()
    universe = load_index_codes()
    asof = a.asof or max(_d(r["entry_date"]) for r in full if r.get("entry_date"))
    if asof not in clos:
        asof = axis[-1]
    ind, ind_note = load_industry("%s-%s-%s" % (asof[:4], asof[4:6], asof[6:]))
    print("  行业      %s，覆盖 %d 只" % (ind_note, len(ind)))
    print("  指数黑名单 %d 个｜势档基准 %s 20 日动量 ±2%%" % (len(universe), IDX_CODE))

    rows, dropped = build(full, paper, universe, ind, mom, axis, degrade=a.degrade,
                          comm_bp=a.comm, slip_bp=a.slip)
    if dropped:
        print("  过滤      %s" % dropped)
    agg = aggregate(rows)

    # 持仓：仅多头（side=buy）才算「模拟交易的仓」；卖点是减仓提示，不占仓位
    pos = [r for r in rows if not r["exit"] and r["exec"]]
    pos.sort(key=lambda z: (z["entry"], z["code"]))
    # 减仓提示只留列表要用的字段：1092 笔 × 全字段要 539KB，首屏不该为它买单
    HINT_KEEP = ("code", "name", "ind", "type", "entry", "entry_px", "reg", "held")
    hint = [{k: r[k] for k in HINT_KEEP}
            for r in rows if not r["exit"] and not r["exec"]]

    closed = [r for r in rows if r["exit"]]
    closed.sort(key=lambda z: (z["exit"], z["code"]))
    trades = [{k: r[k] for k in ("code", "name", "ind", "type", "side", "sig_dt",
                                 "detect", "entry", "entry_px", "exit", "exit_px",
                                 "held", "reason", "ret", "alpha", "reg", "v3_ok",
                                 "exec", "in_paper", "lag")}
              for r in closed]

    doc = {
        "asof": asof,
        "source": {
            "full": "chanlun_paper_full.json（池全量重建，绕过 CLOSED_CAP）",
            "paper": "chanlun_paper.json（产线实际台账）",
            "industry": ind_note,
            "regime": "%s 20日动量，±2%% 分档，取 entry_date 当日收盘" % IDX_CODE,
            "hold": "台账 hold20（满 20 日收盘）；v3 规则为强 8 / 中 1",
            "caveat": "台账双向：1/2/3买 是多头，1/2/3卖 是减仓提示，后者不占仓位",
            # 第十八轮：把「毛」和「净」摆在同一份文件里说清楚。
            # 台账 ret 实测等于 exit_px/entry_px−1（差 0.0bp），是零成本毛收益；
            # 任何人拿 ret/alpha 下结论都在假设「成交免费」。
            "cost": {
                "gross_fields": ["ret", "alpha", "ret_buy", "alpha_buy",
                                 "ret_sell", "alpha_sell"],
                "net_fields": ["ret_net", "alpha_net", "ret_buy_net", "alpha_buy_net",
                               "ret_sell_net", "alpha_sell_net"],
                "comm_bp": a.comm if a.comm is not None else HC.COMM_RT_BP,
                "slip_bp": a.slip,
                "total_bp": round(HC.total_bp(a.comm, a.slip), 1),
                "caliber": HC.describe(a.comm, a.slip),
                "why": ("台账本身不含任何成本。ret 与 exit_px/entry_px−1 逐位相同，"
                        "所以 ret/alpha 是毛的；净口径另算在 *_net 里。"
                        "决策一律看 net，gross 只用于对齐历史结论。"),
                "levels": [{"k": x["k"], "label": x["label"],
                            "total_bp": round(HC.total_bp(x["comm_bp"], x["slip_bp"]), 1)}
                           for x in HC.LEVELS],
            },
        },
        "stats": {k: v for k, v in agg.items() if k != "stocks"},
        "positions": pos,
        "sell_hints": hint,
        "blocked": paper.get("entry_blocked") or [],
        "dropped": dropped,
    }
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
    # 逐笔 + 每票聚合单独一份：5000 只票的聚合有 1.4MB，首屏不该为它买单，
    # 点开个股详情时再取。
    with open(a.out_trades, "w", encoding="utf-8") as f:
        json.dump({"asof": asof, "n": len(trades),
                   "rows": trades, "stocks": agg["stocks"]}, f,
                  ensure_ascii=False, separators=(",", ":"))

    print()
    print("-" * 92)
    print("汇总")
    print("-" * 92)
    print("  逐笔 %d（已平 %d / 在途 %d）｜其中多头 %d、减仓提示 %d"
          % (agg["n_rows"], agg["n_closed"], agg["n_open"],
             sum(1 for r in rows if r["exec"]), sum(1 for r in rows if not r["exec"])))
    def fmt(d, k="mean"):
        v = (d or {}).get(k)
        return "%+.3f%%" % v if v is not None else "—"

    print("  v3 合规（1买+势非弱+持有期匹配）%d 笔" % agg["n_v3_ok"])
    print()
    print("  ★ 可执行（多头买点）——A 股只能做多，这才是真实业绩")
    print("    已平 %d 笔｜均值 %s｜中位 %s｜胜率 %s"
          % (agg["ret_buy"]["n"], fmt(agg["ret_buy"]), fmt(agg["ret_buy"], "median"),
             "%.1f%%" % agg["ret_buy"]["win"] if agg["ret_buy"]["win"] is not None else "—"))
    print("    alpha 均值 %s｜中位 %s｜胜率 %s   ← 毛口径（不扣任何成本）"
          % (fmt(agg["alpha_buy"]), fmt(agg["alpha_buy"], "median"),
             "%.1f%%" % agg["alpha_buy"]["win"] if agg["alpha_buy"]["win"] is not None else "—"))
    print("    净 alpha 均值 %s｜中位 %s｜胜率 %s   ← 扣 %s"
          % (fmt(agg["alpha_buy_net"]), fmt(agg["alpha_buy_net"], "median"),
             "%.1f%%" % agg["alpha_buy_net"]["win"]
             if agg["alpha_buy_net"]["win"] is not None else "—",
             HC.describe(a.comm, a.slip).split("→")[-1].strip()))
    print()
    print("  ○ 不可执行（减仓提示，做空口径）——不能裸卖空，只作「该减了」提示")
    print("    已平 %d 笔｜均值 %s｜alpha %s   ← 这个正数不计入业绩"
          % (agg["ret_sell"]["n"], fmt(agg["ret_sell"]), fmt(agg["alpha_sell"])))
    print()
    print("  合并口径（含做空，勿引用）均值 %s｜alpha %s"
          % (fmt(agg["ret"]), fmt(agg["alpha"])))
    print()
    print("  按类型")
    for k, v in agg["by_type"].items():
        print("    %-4s %5d 笔（在途 %4d）｜已平均值 %s｜alpha %s"
              % (k, v["n"], v["n_open"], fmt(v["ret"]), fmt(v["alpha"])))
    print()
    print("  当前模拟持仓 %d 笔（多头、未出场）｜减仓提示 %d 笔" % (len(pos), len(hint)))
    for r in pos[:5]:
        print("    %-10s %-6s %-6s %s 入 %8.2f 持 %s 日 %s"
              % (r["code"], r["name"][:6], r["ind"][:6], r["entry"], r["entry_px"],
                 r["held"], r["type"]))
    if len(pos) > 5:
        print("    … 另 %d 笔" % (len(pos) - 5))

    sz1 = os.path.getsize(a.out) / 1e6
    sz2 = os.path.getsize(a.out_trades) / 1e6
    print()
    print("  输出 %s（%.2f MB）、%s（%.2f MB）"
          % (os.path.basename(a.out), sz1, os.path.basename(a.out_trades), sz2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
