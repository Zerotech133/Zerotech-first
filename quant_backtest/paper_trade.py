# -*- coding: utf-8 -*-
"""模拟盘（paper trading）v0.1 —— 策略调校与训练数据积累。

用户规则（2026-11-13 老田拍板，原文转译）：
  · 单策略上限 1000 万（模拟资金）；
  · 每只首仓 ≤5%；盘中出现加仓点且在买点上 → 可加仓，单票封顶 30%；
  · 不达预期直接卖掉（v0.1 口径：持有次日收盘浮亏即卖，参数可调）；
  · 每日收盘后筛「不在趋势上、次日可能出问题」的票，结合次日盘前
    数据卖出空仓；
  · 产出：数据（trades/equity 流水）+ 曲线图 + 未来训练模型的数据集。

三个产出物：
  data/paper/account.json   账户状态（现金/持仓/挂单）——原子写
  data/paper/trades.jsonl   交易流水（每笔带信号快照=训练样本；平仓回填收益）
  data/paper/equity.jsonl   每日净值（收盘后记一条）

审计（JUDGMENT_AUDIT 台账纪律）：
  assumptions: ①成交价=当日开盘价（L1 快照可得，模拟盘无滑点）；
               ②「不达预期」=-阈值浮亏（用户原话的量化，待调校）；
               ③「不在趋势上」= pool_state 失守/下离枢组 或 trend_dir<=0。
  falsifier:  实盘对照滑点>1%、或止损口径过松/过紧（后验胜率<基准）。
  cost_if_wrong: 模拟盘无真金损失，代价=训练数据被污染（口径变更必须
                 在 trades 里记 rules_version，防止混样本训练）。

用法：
  python paper_trade.py open            # 开盘：执行隔夜卖出单+买入单
  python paper_trade.py review          # 盘后：止损判定+生成隔夜卖出清单
  python paper_trade.py equity          # 收盘净值入账+曲线数据
  python paper_trade.py status          # 账户概览
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

PAPER_DIR = os.path.join(HERE, "data", "paper")
ACC = os.path.join(PAPER_DIR, "account.json")
TRADES = os.path.join(PAPER_DIR, "trades.jsonl")
EQUITY = os.path.join(PAPER_DIR, "equity.jsonl")

# ── 决策时点定价 / 抑制令（v2）────────────────────────────────────────
# 分钟行情：每根 K 的 time 是该分钟起点，close 要等这一分钟走完才可见。
# 因此「决策时刻 T 可见」= time < T 的最后一根，绝不能取 time == T。
MINUTE_ROOT = os.path.join(HERE, "data", "tdx", "ts_rt_min_roll")
DELIVERABLES = os.environ.get("ZB_DELIVERABLES") or os.path.abspath(
    os.path.join(HERE, "..", "..", "deliverables"))
SUP_CONSUMER_ID = "quant_backtest/paper_trade.py"
SUP_SCHEMA = "preopen_suppressed/v1"
# 交易时段：不在时段内一律拒绝成交（封死「盘后补录开盘单」这条回填路径）
SESSIONS = (("09:30", "11:30"), ("13:00", "15:00"))


# ── 规则参数（rules_v1；改动必须 bump 并在 trades 记版本）──────────────
RULES = {
    "version": "v2",
    "capital": 10_000_000.0,        # 单策略上限 1000 万
    "init_pos_pct": 0.05,           # 首仓 5%
    "max_pos_pct": 0.30,            # 加仓后单票上限 30%
    "add_on_signal": True,          # 新买点加仓（用户：「加仓点且在买点上」）
    "stop_loss_pct": None,          # 硬止损（未设；用户口径=浮亏即卖）
    "lose_next_day_sell": True,     # 持有次日收盘浮亏 → 卖（不达预期）
    "audit": {
        "assumptions": "成交价=严格早于决策时刻的最后一分钟收盘价；"
                       "浮亏即卖=不达预期；失守/下离枢=不在趋势上",
        "falsifier": "实盘滑点>1%；口径过松/紧（后验胜率<基准）",
        "cost_if_wrong": "训练数据污染——rules_version 必须随档",
    },
}


def _atomic_write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _append_jsonl(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def load_account():
    if os.path.exists(ACC):
        return json.load(open(ACC, encoding="utf-8"))
    acc = {
        "created": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "rules": RULES,
        "cash": RULES["capital"],
        "positions": {},        # code -> {shares, avg_cost, opened, last_signal, snapshot}
        "pending_buys": [],     # [{code, name, tier, sources...}]
        "pending_sells": [],    # [{code, why}]
        "realized_pnl": 0.0,
    }
    _atomic_write_json(ACC, acc)
    return acc


def _now_hhmm():
    return dt.datetime.now().strftime("%H:%M")


def _resolve_day(day=None):
    return day or dt.date.today().strftime("%Y%m%d")


def _assert_session(decision_time):
    """决策时刻必须落在交易时段内；否则直接阻断，不许补录。"""
    if len(decision_time) != 5 or decision_time[2] != ":":
        raise RuntimeError("decision_time 必须是 HH:MM，实际=%s" % decision_time)
    if not any(lo <= decision_time <= hi for lo, hi in SESSIONS):
        raise RuntimeError("决策时刻 %s 不在交易时段内，拒绝成交定价" % decision_time)


def resolve_visible_prices(codes, day, decision_time):
    """返回决策时点「已可见」的成交价，绝不回填更早或更晚的价格。

    口径：分钟行情里 time 严格小于 decision_time 的最后一根 close。
      · 无可见分钟数据的票直接不成交（不回退 open、不回退实时快照）；
      · 同一 (day, decision_time) 永远得到同一结果 —— 可复算；
      · 返回值带 price_visible_at，训练样本可独立审计 I1 时点单调。
    """
    import glob
    import pandas as pd

    _assert_session(decision_time)
    iso = "%s-%s-%s" % (day[:4], day[4:6], day[6:])
    cut = "%s %s:00" % (iso, decision_time)
    parts = sorted(glob.glob(os.path.join(MINUTE_ROOT, "d_" + day,
                                          "part_*.parquet")))
    if not parts or not codes:
        return {}
    m = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)
    m["time"] = m["time"].astype(str)
    m = m[m["time"].str.startswith(iso)]
    m = m[m["ts_code"].isin(set(codes))]
    if m.empty:
        return {}
    m = m.drop_duplicates(["ts_code", "time"]).sort_values(["ts_code", "time"])

    # 09:30 集合竞价：开盘价在 09:30:00 即已确定，是此刻唯一可见价。
    # 取当日第一根 K 的 open 并显式标注，不构成「用更早价格回填」。
    if decision_time == "09:30":
        auc = m[m["time"] == iso + " 09:30:00"]
        out = {}
        for c in codes:
            rows = auc[auc["ts_code"] == c]
            if rows.empty:
                continue
            p = float(rows.iloc[0]["open"])
            if p > 0:
                out[c] = {"price": p, "name": "",
                          "price_source": "auction_open_0930",
                          "price_visible_at": iso + " 09:30:00"}
        return out

    # 盘中：取 time 严格小于决策时刻的最后一根 close。
    m = m[(m["time"] >= iso + " 09:30:00") & (m["time"] < cut)]
    if m.empty:
        return {}
    g = m.groupby("ts_code", sort=False)
    last_px = g["close"].last().to_dict()
    last_t = g["time"].last().to_dict()
    out = {}
    for c in codes:
        p = float(last_px.get(c, 0) or 0)
        if p > 0:
            out[c] = {"price": p, "name": "", "price_source": "minute_last_close",
                      "price_visible_at": last_t[c]}
    return out


def _audit_meta(quote, day, decision_time):
    """成交流水里必须留下的时点证据（训练样本可独立复核）。"""
    return {
        "decision_time": "%s-%s-%s %s:00" % (day[:4], day[4:6], day[6:],
                                            decision_time),
        "price_source": quote.get("price_source", "UNKNOWN"),
        "price_visible_at": quote.get("price_visible_at", ""),
        "exec_basis": "visible_minute_close_before_decision",
    }


def load_suppression(day):
    """读人工抑制令。字段不符一律抛错阻断（失败关闭），绝不静默当「今日无抑制」。"""
    p = os.path.join(DELIVERABLES, "preopen_suppressed_%s.json" % day)
    if not os.path.isfile(p):
        return None
    j = json.load(open(p, encoding="utf-8"))
    if j.get("schema") != SUP_SCHEMA:
        raise RuntimeError("抑制令 schema 不符：%s" % j.get("schema"))
    action = j.get("action")
    if action not in ("suppress", "allow"):
        raise RuntimeError("抑制令 action 无法识别：%s" % action)
    if str(j.get("exec_day")) != str(day):
        raise RuntimeError("抑制令 exec_day=%s 与执行日 %s 不符" % (j.get("exec_day"), day))
    consumers = j.get("consumers")
    if not isinstance(consumers, list) or SUP_CONSUMER_ID not in consumers:
        raise RuntimeError("抑制令未登记本消费方 %s，失败关闭" % SUP_CONSUMER_ID)
    return j if action == "suppress" else None


def _today_open_prices(codes):
    """[DEPRECATED / 仅展示] 新浪 L1 快照取当日开盘价。

    禁止用于成交定价：无论何时调用都返回当日 09:30 开盘价，
    盘中或盘后调用即形成「用更早价格回填成交」的前视。
    成交一律走 resolve_visible_prices(codes, day, decision_time)。
    """
    root = os.path.abspath(os.path.join(HERE, "..", "news_collector"))
    if root not in sys.path:
        sys.path.insert(0, root)
    from quote_snapshot import fetch_quotes  # noqa: E402

    q = {}
    for i in range(0, len(codes), 200):
        for x in fetch_quotes(codes[i:i + 200]) or []:
            q[x["code"]] = x
    out = {}
    for c in codes:
        sc = ("sh" if c.endswith(".SH") else "bj" if c.endswith(".BJ")
              else "sz") + c[:6]
        x = q.get(sc)
        if x and x.get("open"):
            out[c] = {"open": float(x["open"]), "close": float(x.get("price")
                                                             or x["open"]),
                      "name": x.get("name", "")}
    return out


def _to_sina(code):
    pre = "sh" if code.endswith(".SH") else ("bj" if code.endswith(".BJ")
                                             else "sz")
    return pre + code[:6]


def _cur_price_map(codes):
    """当日最新价（盘中=实时，盘后=收盘）。"""
    root = os.path.abspath(os.path.join(HERE, "..", "news_collector"))
    if root not in sys.path:
        sys.path.insert(0, root)
    from quote_snapshot import fetch_quotes  # noqa: E402

    out = {}
    for i in range(0, len(codes), 200):
        for x in fetch_quotes(codes[i:i + 200]) or []:
            c = x["code"]
            code = c[2:] + (".SH" if c.startswith("sh")
                            else ".BJ" if c.startswith("bj") else ".SZ")
            out[code] = float(x.get("price") or 0)
    return out


# ── 开盘：执行买卖挂单 ─────────────────────────────────────────────
def cmd_open(day=None, decision_time=None):
    day = _resolve_day(day)
    decision_time = decision_time or _now_hhmm()
    _assert_session(decision_time)

    # 抑制令优先于一切：不建仓、不写账户、不写流水（按规则跳过，exit 0）
    sup = load_suppression(day)
    if sup:
        print("[SKIP] %s 盘前已人工抑制：本链不建仓、不写流水、不生成挂单" % day)
        print("       理由：%s" % str(sup.get("reason", ""))[:100])
        return 0

    acc = load_account()
    picks = json.load(open(os.path.join(HERE, "..", "portal", "data",
                                        "todays_picks.json"),
                           encoding="utf-8"))
    # 1) 执行隔夜卖出单（价=决策时点可见价）
    sells = [s["code"] for s in acc.get("pending_sells") or []]
    buy_codes = {b["code"] for b in acc.get("pending_buys") or []}
    px = resolve_visible_prices(sorted(set(sells) | buy_codes), day,
                                decision_time)
    for s in acc.get("pending_sells") or []:
        c = s["code"]
        pos = acc["positions"].get(c)
        quote = px.get(c)
        if not pos or not quote:
            continue
        price = quote["price"]
        proceeds = pos["shares"] * price
        acc["cash"] += proceeds
        pnl = (price - pos["avg_cost"]) * pos["shares"]
        acc["realized_pnl"] += pnl
        del acc["positions"][c]
        _append_jsonl(TRADES, {
            "day": day, "action": "SELL", "code": c,
            "name": pos.get("name", ""), "price": round(price, 3),
            "shares": pos["shares"], "pnl": round(pnl, 0),
            "why": s.get("why", ""), "rules": RULES["version"],
            **_audit_meta(quote, day, decision_time),
            "ts": dt.datetime.now().isoformat(timespec="seconds"),
        })
        print("[open] 卖出 %s %s @%.2f 盈亏 %+.0f（%s）"
              % (c, pos.get("name", ""), price, pnl, s.get("why", "")))
    acc["pending_sells"] = []

    # 2) 执行买入挂单（T2 名单，首仓 5%；已持仓且信号在→加仓至≤30%）
    budget = acc["cash"]
    for b in acc.get("pending_buys") or []:
        c = b["code"]
        quote = px.get(c)
        if not quote:
            print("[open] %s 决策时点无可见价，不成交" % c)
            continue
        price = quote["price"]
        pos = acc["positions"].get(c)
        cap_total = RULES["capital"] * RULES["max_pos_pct"]
        if pos:
            if not RULES["add_on_signal"]:
                continue
            cur_val = pos["shares"] * price
            add_val = min(RULES["capital"] * RULES["init_pos_pct"],
                          cap_total - cur_val, budget)
            if add_val < price * 100:
                continue
            shares = int(add_val / price / 100) * 100
            if shares <= 0:
                continue
            pos["avg_cost"] = (pos["avg_cost"] * pos["shares"] + price * shares) \
                / (pos["shares"] + shares)
            pos["shares"] += shares
            acc["cash"] -= shares * price
            budget = acc["cash"]
            _append_jsonl(TRADES, {
                "day": day, "action": "ADD", "code": c, "price": round(price, 3),
                "shares": shares, "rules": RULES["version"],
                "why": "买点加仓", "signal": b.get("sources"),
                **_audit_meta(quote, day, decision_time),
                "ts": dt.datetime.now().isoformat(timespec="seconds"),
            })
            print("[open] 加仓 %s +%d 股 @%.2f" % (c, shares, price))
        else:
            val = min(RULES["capital"] * RULES["init_pos_pct"], budget)
            shares = int(val / price / 100) * 100
            if shares <= 0:
                print("[open] 现金不足，跳过 %s" % c)
                continue
            snapshot = {
                "tier": b.get("tier"),
                "sources": b.get("sources"),
                "risk_gate": picks.get("risk_gate"),
                "picks_generated": picks.get("generated_at"),
                "basis": picks.get("trade_basis"),
            }
            acc["positions"][c] = {
                "name": b.get("name", quote.get("name", "")),
                "shares": shares, "avg_cost": price,
                "opened": day, "snapshot": snapshot,
            }
            acc["cash"] -= shares * price
            budget = acc["cash"]
            _append_jsonl(TRADES, {
                "day": day, "action": "BUY", "code": c,
                "name": b.get("name", ""), "price": round(price, 3),
                "shares": shares, "rules": RULES["version"],
                "signal": snapshot,      # 训练样本：入场时的完整信号快照
                **_audit_meta(quote, day, decision_time),
                "ts": dt.datetime.now().isoformat(timespec="seconds"),
            })
            print("[open] 买入 %s %s %d 股 @%.2f（首仓 %.0f 万）"
                  % (c, b.get("name", ""), shares, price,
                     shares * price / 1e4))
    acc["pending_buys"] = []
    _atomic_write_json(ACC, acc)
    print("现金余 %.0f 万，持仓 %d 只"
          % (acc["cash"] / 1e4, len(acc["positions"])))


# ── 盘后评审：不达预期判定 + 明日卖出清单 + 次日买入挂单 ─────────────
def cmd_review(day=None):
    acc = load_account()
    day = _resolve_day(day)
    codes = list(acc["positions"])
    if not codes:
        print("无持仓")
    px = _cur_price_map(codes) if codes else {}

    pending_sells = []
    for c, pos in acc["positions"].items():
        price = px.get(c)
        if not price:
            continue
        ret = price / pos["avg_cost"] - 1
        held_days = 1 if pos.get("opened") == day else 2  # 简化：非当日即次日+
        # 规则①：持有次日收盘浮亏 → 不达预期，直接卖
        if RULES["lose_next_day_sell"] and pos.get("opened") != day and ret < 0:
            pending_sells.append({"code": c, "why":
                                  "不达预期（浮亏 %.1f%%）" % (ret * 100)})
            continue
        # 规则②：不在趋势上（盘后结构筛选——失守/下离枢）
        # 数据源：当天 pool_live 最新快照 groups
        # T+1 防护：当天开仓的票不进隔夜卖单（当日买入不可卖），
        # 留到次日 review 再判——首版漏判例（2026-11-13 老田 T+1 检查）。
        if pos.get("opened") == day:
            continue
        try:
            import glob
            snaps = sorted(glob.glob(os.path.join(
                HERE, "reports", "pool_live_%s_*.json" % day)))
            bad_groups = ("买点失守", "三买失守", "三买失守", "下离枢")
            flag = None
            if snaps:
                lv = json.load(open(snaps[-1], encoding="utf-8"))
                for gname, items in (lv.get("groups") or {}).items():
                    if any(gname.startswith(bg) for bg in bad_groups):
                        if any(it.get("code") == c for it in items or []):
                            flag = gname
                            break
            if flag:
                pending_sells.append({"code": c, "why": "不在趋势上（%s）" % flag})
        except Exception as e:  # noqa: BLE001
            print("[review] 结构筛选失败（软）：%s" % str(e)[:60])

    acc["pending_sells"] = pending_sells

    # 次日买入挂单：今日名单 T2（若今日已过开盘，名单已在 open 消费——
    # review 生成的是「明早执行」的挂单，来源=今晚 18:20 生成的新名单。
    # 白天手动跑 review 时读到的还是昨夜名单 → 这里挂单留给明早 open。
    try:
        picks = json.load(open(os.path.join(HERE, "..", "portal", "data",
                                            "todays_picks.json"),
                               encoding="utf-8"))
        t2 = [s for s in picks.get("stocks") or [] if s.get("tier") == "T2"]
        pb = []
        add_sigs = []      # 已持仓票的新买点信号 → 次日 open 走加仓分支
        for s in t2:
            held = s["code"] in acc["positions"]
            sig = {"code": s["code"], "name": s.get("name"), "tier": "T2",
                   "sources": [{"s": x.get("s"), "tag": x.get("tag"),
                                "dt": x.get("dt")}
                               for x in s.get("sources") or []]}
            if held:
                add_sigs.append(sig)   # 加仓挂单：open 的 pos 分支消费
                continue
            pb.append(sig)
        # 弱势日仓位减半建仓（盘面联动；rules_v1 口径）
        rg = picks.get("risk_gate") or {}
        if rg.get("weak_day"):
            print("[review] 弱势日：明日建仓名单仅保留前 6 只（半仓试错）")
            pb = pb[:6]
        sup = load_suppression(day)
        if sup:
            print("[review] 抑制日：不生成买入挂单（止损卖出判定保留，只减敞口不造信号）")
            pb = []
            add_sigs = []   # 加仓同样是买入指令，抑制日一律不发
        acc["pending_buys"] = pb + add_sigs   # 首仓单+加仓单（open 分支区分）
    except Exception as e:  # noqa: BLE001
        print("[review] 名单读取失败（保留空挂单）：%s" % str(e)[:60])

    _atomic_write_json(ACC, acc)
    print("[review] 明日卖出 %d 单：%s" % (len(pending_sells),
          "、".join(s["code"] for s in pending_sells) or "无"))


# ── 收盘净值 ───────────────────────────────────────────────────────
def cmd_equity(day=None):
    acc = load_account()
    day = _resolve_day(day)
    codes = list(acc["positions"])
    px = _cur_price_map(codes) if codes else {}
    mv = sum(pos["shares"] * px.get(c, pos["avg_cost"])
             for c, pos in acc["positions"].items())
    total = acc["cash"] + mv
    _append_jsonl(EQUITY, {
        "day": day, "cash": round(acc["cash"], 0),
        "market_value": round(mv, 0), "total": round(total, 0),
        "nav": round(total / RULES["capital"], 4),
        "n_pos": len(acc["positions"]),
        "suppressed": bool(load_suppression(day)),
        "realized_pnl": round(acc["realized_pnl"], 0),
        "ts": dt.datetime.now().isoformat(timespec="seconds"),
    })
    print("[equity] %s 总资产 %.0f 万（nav %.4f）持仓 %d 只"
          % (day, total / 1e4, total / RULES["capital"],
             len(acc["positions"])))


def cmd_status():
    acc = load_account()
    codes = list(acc["positions"])
    px = _cur_price_map(codes) if codes else {}
    mv = sum(pos["shares"] * px.get(c, pos["avg_cost"])
             for c, pos in acc["positions"].items())
    total = acc["cash"] + mv
    print("模拟账户 %s（账户 rules %s / 代码 rules %s）"
          % (acc.get("created"), acc["rules"]["version"],
             RULES["version"]))
    print("现金 %.0f 万 | 市值 %.0f 万 | 总资产 %.0f 万（nav %.4f）| 已实现盈亏 %+.0f"
          % (acc["cash"] / 1e4, mv / 1e4, total / 1e4,
             total / RULES["capital"], acc["realized_pnl"]))
    for c, pos in acc["positions"].items():
        p = px.get(c)
        ret = (p / pos["avg_cost"] - 1) * 100 if p else 0
        print("  %-11s %-8s %d股 成本%.2f 现价%.2f 浮动%+.1f%%（%s入）"
              % (c, pos.get("name", ""), pos["shares"], pos["avg_cost"],
                 p or 0, ret, pos.get("opened")))
    for s in acc.get("pending_sells") or []:
        print("  [明日卖] %s %s" % (s["code"], s["why"]))


def main():
    ap = argparse.ArgumentParser(description="模拟盘 v2（决策时点定价+抑制令感知）")
    ap.add_argument("cmd", choices=["open", "review", "equity", "status"])
    ap.add_argument("--date", default=None,
                    help="数据日 YYYYMMDD；复算历史必须同时给 --decision-time")
    ap.add_argument("--decision-time", default=None,
                    help="成交决策时刻 HH:MM；决定「哪些价格已经可见」")
    a = ap.parse_args()
    if a.date and a.date != dt.date.today().strftime("%Y%m%d") \
            and not a.decision_time:
        print("[BLOCK] 复算历史日必须显式给 --decision-time，"
              "禁止用当前时刻冒充历史决策时刻")
        return 2
    if a.decision_time and a.cmd != "open":
        print("[BLOCK] --decision-time 只对 open 有意义")
        return 2
    try:
        return {"open": lambda: cmd_open(a.date, a.decision_time),
                "review": lambda: cmd_review(a.date),
                "equity": lambda: cmd_equity(a.date),
                "status": cmd_status}[a.cmd]() or 0
    except RuntimeError as e:
        print("[BLOCK] %s" % e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
