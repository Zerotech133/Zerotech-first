# -*- coding: utf-8 -*-
"""多策略前向模拟盘（paper arena）—— 让系统自己完成买卖动作

为什么需要它（2026-11-11 老田提出）：
    人不可能天天盯盘决定何时平仓，所以「择时」「择势」必须由系统按写死的规则
    自动完成。回测数字不能代替前向验证，必须把每天选出的股、用几种打法、各自
    结果，一天一天沉下来。

三层解耦（这是本文件存在的理由）：
    信号层  cohorts/cohort_*.json  —— 每日真实登记，本文件只读不改
    交易层  本文件：N 个策略各自按规则买入/持有/卖出，独立账本
    呈现层  paper_arena.html       —— 某日选了什么 × 几种打法 × 各自结果

五条铁律：
    1. 只用 ≤ 当日的行情，绝不看未来。未到期就是未到期，不许拿回测结果填补。
    2. 买入价 = 信号次日**开盘价**（selection 文件 note 原话：
       「信号日收盘→次交易日开盘买」）。全程用复权因子换算，除权送股不影响收益。
    3. **每条轨 = 一个信号 × 一个策略，各自独立 1000 万，不共享资金。**
       共享资金会让「先到的那批把现金吃光」，后面的信号只能买零头，策略差异
       被资金顺序污染 —— 首版就踩了这个坑（231 只持仓、现金 0%）。
    4. 策略之间只有规则不同：同一批信号、同样成本、同样等权，否则不可比。
    5. 全量重算、覆盖写。账本只有几千行，重算比增量补丁安全。
"""
import json
import os
import sys
import glob
import io
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
COHORT_DIR = os.path.join(HERE, "cohorts")
ARENA_DIR = os.path.join(HERE, "arena")

if HERE not in sys.path:
    sys.path.insert(0, HERE)

import cohort_track as CT  # noqa: E402  复用它的 load_daily / load_adjf / trade_cal

CAPITAL = 10_000_000.0   # 每条轨的本金（不共享），与 portfolio_sim 一致便于对照
COST_ONE = 0.0015        # 单边成本（佣金+滑点），双边 0.3%
MIN_SAMPLE_DAYS = 20     # 样本门槛：不满 20 个交易日一律不下结论
MIN_SAMPLE_TRADES = 30

STRATEGIES = [
    # S0 不是策略，是对账锚点。cohort 台账的「N 日」= 买入起第 N 个交易日收盘，
    # 所以「1 日」= 买入**当日**收盘卖（entry 与 m 同一天，2026-11-06/auto 实测
    # entry=20261109 m=20261109）。这是 T+0，A 股做不了，只能用来检验引擎口径，
    # 不参与排名。
    dict(id="S0",  name="当日收盘卖（对账用）", hold=0, reconcile_only=True,
         desc="开盘买、当日收盘卖。与 cohort 台账「1 日」口径一致，仅用于核对引擎，不可交易。"),
    dict(id="S1",  name="持有 1 天",        hold=1,
         desc="信号次日开盘买，下一交易日收盘卖（T+1，可交易）。检验 1 日 alpha。"),
    dict(id="S3",  name="持有 3 天",        hold=3,
         desc="买入后第 3 个交易日收盘卖。检验 alpha 衰减速度。"),
    dict(id="S5",  name="持有 5 天",        hold=5,
         desc="原设定周期。第 5 个交易日收盘卖。"),
    dict(id="S10", name="持有 10 天",       hold=10,
         desc="第 10 个交易日收盘卖。检验长持有是否把 alpha 磨平。"),
    dict(id="S5G", name="5 天 + 闸门",      hold=5, use_gate=True,
         desc="只在闸门判 safe=true 的次日买入。检验「坏日子不交易」到底值不值。"),
    dict(id="S5L", name="5 天 + 止损 5%",   hold=5, stop_loss=0.05,
         desc="收盘价跌破成本 5% 即卖。检验止损是降回撤还是砍掉反弹。"),
    dict(id="S5T", name="5 天 + 止盈 10%",  hold=5, take_profit=0.10,
         desc="收盘价涨过成本 10% 即卖。检验止盈是否砍掉右尾。"),
]
STRAT_BY_ID = {s["id"]: s for s in STRATEGIES}


# ── 信号层：只读 cohort 台账 ──────────────────────────────────────
def load_signals():
    """[{asof, pool, pool_label, codes, entry_date}]，按 entry_date 升序。

    台账是每日 08:40/09:40/10:40 真实跑出来的。本文件不筛选、不重排——重排就等于
    在交易层偷偷换信号，上下游会对不上。
    """
    out = []
    for fp in sorted(glob.glob(os.path.join(COHORT_DIR, "cohort_*.json"))):
        try:
            d = json.load(io.open(fp, encoding="utf-8"))
        except Exception as e:
            print("[warn] 跳过坏 cohort 文件 %s: %s" % (os.path.basename(fp), e))
            continue
        codes = [c for c in (d.get("codes") or []) if isinstance(c, str)]
        if not codes or not d.get("entry_date"):
            continue
        out.append({"key": "%s_%s" % (d["asof"], d.get("pool", "?")),
                    "asof": str(d["asof"]),
                    "pool": d.get("pool", "?"),
                    "pool_label": d.get("pool_label", d.get("pool", "?")),
                    "codes": codes,
                    "entry_date": str(d["entry_date"])})
    out.sort(key=lambda x: (x["entry_date"], x["asof"], x["pool"]))
    return out


# ── 闸门 ─────────────────────────────────────────────────────────
def load_gate():
    fp = os.path.join(CT.DATA, "gate_decisions.jsonl")
    m = {}
    if os.path.exists(fp):
        for line in io.open(fp, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                g = json.loads(line)
            except Exception:
                continue
            d = str(g.get("date") or "")
            if d:
                m[d] = bool(g.get("safe"))
    return m


def gate_safe_on(gate_map, date):
    """date（含）之前最后一条闸门判定的 safe；无记录返回 None（未知 ≠ 安全）。"""
    ks = [k for k in gate_map if k <= date]
    return gate_map[max(ks)] if ks else None


# ── 行情层：复权价 ───────────────────────────────────────────────
class Px:
    def __init__(self):
        self._daily, self._adjf = {}, {}

    def day(self, date):
        if date not in self._daily:
            self._daily[date] = CT.load_daily(date)
        return self._daily[date]

    def adjf(self, date):
        if date not in self._adjf:
            self._adjf[date] = CT.load_adjf(date)
        return self._adjf[date]

    def price(self, date, code, field):
        """复权价；缺价（停牌/未上市）返回 None。"""
        r = self.day(date).get(code)
        if not r or not r.get(field):
            return None
        f = self.adjf(date).get(code)
        return float(r[field]) * float(f) if f else None


def first_usable_cal(px, cal_all, signals):
    """能估值的最早/最晚交易日。从信号里取探测票，避免用死票误判「行情没出」。"""
    codes = []
    for s in signals:
        codes += s["codes"]
    codes = list(dict.fromkeys(codes))[:5]
    lo = min(s["entry_date"] for s in signals)
    last = None
    for d in reversed([x for x in cal_all if x >= lo]):
        if any(px.price(d, c, "close") for c in codes):
            last = d
            break
    return lo, last


# ── 交易层：一条轨 = 一个信号 × 一个策略 ──────────────────────────
def run_one(sig, st, cal, gate_map, px):
    """单条轨。返回 dict 或 None（闸门拒绝 / 全部买不进）。

    净值曲线从 entry_date 起算，本金 1000 万，等权买入当日能开盘成交的全部票。
    """
    entry = sig["entry_date"]
    if st.get("use_gate") and gate_safe_on(gate_map, entry) is not True:
        return {"skipped": "gate", "sig": sig, "st": st}

    days = [d for d in cal if d >= entry]
    buyable = []
    for c in sig["codes"]:
        p = px.price(entry, c, "open")
        if p:
            buyable.append((c, p))
    if not buyable:
        return {"skipped": "no_fill", "sig": sig, "st": st}

    per = CAPITAL / len(buyable)
    pos = {c: {"alloc": per, "entry_px": p, "code": c} for c, p in buyable}
    trades, nav_rows, last_close = [], [], {}
    idx = {d: i for i, d in enumerate(cal)}

    for d in days:
        for code in list(pos.keys()):
            p = pos[code]
            c = px.price(d, code, "close")
            if c is not None:
                last_close[code] = c
            mark = c if c is not None else last_close.get(code)
            if mark is None:
                continue
            ret_gross = mark / p["entry_px"] - 1.0
            held = idx[d] - idx[entry]
            reason = None
            if held >= st["hold"]:
                reason = "到期"
            elif st.get("stop_loss") and ret_gross <= -st["stop_loss"]:
                reason = "止损"
            elif st.get("take_profit") and ret_gross >= st["take_profit"]:
                reason = "止盈"
            if reason is None:
                continue
            gross = p["alloc"] * (1 - COST_ONE) * (mark / p["entry_px"])
            got = gross * (1 - COST_ONE)
            trades.append({"code": code, "sig": sig["key"], "entry_date": entry,
                           "exit_date": d, "entry_px": round(p["entry_px"], 4),
                           "exit_px": round(mark, 4), "alloc": round(p["alloc"], 2),
                           "ret": round(got / p["alloc"] - 1.0, 6),
                           "held_days": held, "reason": reason,
                           "asof": sig["asof"], "pool": sig["pool"]})
            del pos[code]

        mv = 0.0
        for code, p in pos.items():
            c = px.price(d, code, "close")
            if c is not None:
                last_close[code] = c
            mark = c if c is not None else last_close.get(code)
            mv += p["alloc"] * (1 - COST_ONE) * ((mark / p["entry_px"]) if mark else 1.0)
        closed_val = sum(t["alloc"] * (1 + t["ret"]) for t in trades)
        cash_equiv = CAPITAL - sum(p["alloc"] for p in pos.values()) - sum(
            t["alloc"] for t in trades)
        nav = cash_equiv + closed_val + mv
        nav_rows.append({"date": d, "nav": round(nav, 2), "n_pos": len(pos),
                         "mv": round(mv, 2), "closed": len(trades)})
        if not pos:
            break     # 全部平掉，后面没有新的价格暴露

    return {"sig": sig, "st": st, "nav_rows": nav_rows, "trades": trades, "ok": True}


def aggregate(tracks):
    """把多条轨拼成一个组合净值：各轨等权，**平仓后冻结在最终值**。

    首版错在这里：只把「当天有记录的轨」取平均，于是已平仓的轨被排除，
    最后一天的组合净值只剩还在持仓的那几条（S1 显示 +3.15%，实际七条轨平均
    是另一个数）。已了结的战绩必须一直留在组合里，否则越早平仓的越看不见。
    """
    if not tracks:
        return []
    series = [{r["date"]: r["nav"] / CAPITAL for r in t["nav_rows"]} for t in tracks]
    starts = [min(s) for s in series if s]
    series = [s for s in series if s]
    all_dates = sorted({d for s in series for d in s})
    out = []
    for d in all_dates:
        vals = []
        for s, d0 in zip(series, starts):
            if d < d0:
                continue                 # 这条轨还没开仓，不参与
            if d in s:
                vals.append(s[d])
            else:
                prev = [k for k in s if k <= d]
                if prev:
                    vals.append(s[max(prev)])   # 已平仓 → 冻结在最后净值
        if vals:
            out.append({"date": d, "nav": round(CAPITAL * sum(vals) / len(vals), 2),
                        "n_tracks": len(vals)})
    return out


def summarize(st, tracks, gate_map):
    ok = [t for t in tracks if t.get("ok")]
    skipped_gate = [t for t in tracks if t.get("skipped") == "gate"]
    skipped_fill = [t for t in tracks if t.get("skipped") == "no_fill"]
    trades = [x for t in ok for x in t["trades"]]
    navs = aggregate(ok)

    base = {"id": st["id"], "name": st["name"], "desc": st["desc"], "hold": st["hold"],
            "use_gate": bool(st.get("use_gate")), "stop_loss": st.get("stop_loss"),
            "take_profit": st.get("take_profit"),
            "reconcile_only": bool(st.get("reconcile_only")),
            "n_tracks_ok": len(ok), "n_tracks_gate_skip": len(skipped_gate),
            "n_tracks_no_fill": len(skipped_fill)}

    if not ok or not navs:
        base.update({"status": "all_gated", "n_closed": 0,
                     "note": "全部信号被闸门拒绝，未产生任何交易"})
        return base

    nav0, nav_end = CAPITAL, navs[-1]["nav"]
    peak, mdd = nav0, 0.0
    for r in navs:
        peak = max(peak, r["nav"])
        mdd = min(mdd, r["nav"] / peak - 1.0)
    wins = [t for t in trades if t["ret"] > 0]
    n = len(trades)
    base.update({
        "status": "ok",
        "nav": round(nav_end, 2),
        "ret": round(nav_end / nav0 - 1.0, 6),
        "max_dd": round(mdd, 6),
        "n_closed": n,
        "n_open": sum(len([1 for c in t.get("nav_rows", [])[-1:] if c["n_pos"] > 0])
                      for t in ok),
        "win_rate": round(len(wins) / n, 4) if n else None,
        "avg_ret": round(sum(t["ret"] for t in trades) / n, 6) if n else None,
        "days": len(navs),
        "enough_sample": (len(navs) >= MIN_SAMPLE_DAYS and n >= MIN_SAMPLE_TRADES),
    })
    return base


def build(verbose=True):
    os.makedirs(ARENA_DIR, exist_ok=True)
    signals = load_signals()
    if not signals:
        print("没有 cohort 信号，无法建 arena")
        return None
    gate_map = load_gate()
    px = Px()
    cal_all = CT.trade_cal()
    lo, last = first_usable_cal(px, cal_all, signals)
    if not last:
        print("行情未就绪：%s 之后没有任何可用价格" % lo)
        return None
    cal = [d for d in cal_all if lo <= d <= last]

    result = {"generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
              "capital": CAPITAL, "cost_one_side": COST_ONE,
              "cal": cal, "cal_first": cal[0], "cal_last": cal[-1],
              "n_signals": len(signals),
              "signals": [{"key": s["key"], "asof": s["asof"], "pool": s["pool"],
                           "pool_label": s["pool_label"], "n": len(s["codes"]),
                           "codes": s["codes"],
                           "entry_date": s["entry_date"]} for s in signals],
              "gate": {d: gate_safe_on(gate_map, d) for d in cal},
              "strategies": [], "tracks": {}, "open_positions": {}}

    for st in STRATEGIES:
        tracks = []
        for s in signals:
            t = run_one(s, st, cal, gate_map, px)
            if t is not None:
                tracks.append(t)
        sd = os.path.join(ARENA_DIR, st["id"])
        os.makedirs(sd, exist_ok=True)
        all_tr = [x for t in tracks if t.get("ok") for x in t["trades"]]
        with io.open(os.path.join(sd, "trades.jsonl"), "w", encoding="utf-8", newline="") as f:
            f.write("\n".join(json.dumps(r, ensure_ascii=False) for r in all_tr) + "\n")
        navs = aggregate([t for t in tracks if t.get("ok")])
        with io.open(os.path.join(sd, "nav.jsonl"), "w", encoding="utf-8", newline="") as f:
            f.write("\n".join(json.dumps(r, ensure_ascii=False) for r in navs) + "\n")

        summ = summarize(st, tracks, gate_map)
        result["strategies"].append(summ)
        # 每轨的明细，页面按「信号日 × 策略」矩阵展开用
        result["tracks"][st["id"]] = []
        for t in tracks:
            if t.get("ok"):
                tr = t["trades"]
                last_nav = t["nav_rows"][-1]["nav"] if t["nav_rows"] else CAPITAL
                result["tracks"][st["id"]].append({
                    "sig": t["sig"]["key"], "asof": t["sig"]["asof"],
                    "pool": t["sig"]["pool"], "entry_date": t["sig"]["entry_date"],
                    "n_buy": len(t["nav_rows"]) and None,
                    "n_closed": len(tr), "n_left": t["nav_rows"][-1]["n_pos"] if t["nav_rows"] else 0,
                    "ret": round(last_nav / CAPITAL - 1.0, 6),
                    "win_rate": round(len([x for x in tr if x["ret"] > 0]) / len(tr), 4) if tr else None,
                    "done": t["nav_rows"][-1]["n_pos"] == 0 if t["nav_rows"] else False,
                })
            else:
                result["tracks"][st["id"]].append({
                    "sig": t["sig"]["key"], "asof": t["sig"]["asof"],
                    "pool": t["sig"]["pool"], "entry_date": t["sig"]["entry_date"],
                    "skipped": t.get("skipped")})
        if verbose:
            s = result["strategies"][-1]
            if s.get("status") == "all_gated":
                print("  %-4s %-14s 闸门全部拒绝，零交易（%d 个信号）"
                      % (st["id"], st["name"], s["n_tracks_gate_skip"]))
            else:
                print("  %-4s %-14s 组合收益 %+7.2f%%  回撤 %6.2f%%  已平 %3d 笔  胜率 %5s"
                      % (st["id"], st["name"], s["ret"] * 100, s["max_dd"] * 100,
                         s["n_closed"],
                         ("%.1f%%" % (s["win_rate"] * 100)) if s["win_rate"] is not None else "-"))

    with io.open(os.path.join(ARENA_DIR, "latest.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    return result


def main():
    verbose = "--quiet" not in sys.argv
    if verbose:
        print("=== 多策略模拟盘（arena） ===")
    r = build(verbose)
    if r is None:
        return 1
    if verbose:
        print("\n区间 %s ~ %s（%d 个交易日），信号 %d 条，策略 %d 个"
              % (r["cal_first"], r["cal_last"], len(r["cal"]), r["n_signals"],
                 len(r["strategies"])))
        short = [s["id"] for s in r["strategies"] if not s.get("enough_sample")]
        if short:
            print("样本不足（<%d 交易日或 <%d 笔）：%s"
                  % (MIN_SAMPLE_DAYS, MIN_SAMPLE_TRADES, ", ".join(short)))
            print("→ 这些数字只做留痕与对照，**不构成结论**。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
