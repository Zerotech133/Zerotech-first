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

# ── 规则参数（rules_v1；改动必须 bump 并在 trades 记版本）──────────────
RULES = {
    "version": "v1",
    "capital": 10_000_000.0,        # 单策略上限 1000 万
    "init_pos_pct": 0.05,           # 首仓 5%
    "max_pos_pct": 0.30,            # 加仓后单票上限 30%
    "add_on_signal": True,          # 新买点加仓（用户：「加仓点且在买点上」）
    "stop_loss_pct": None,          # 硬止损（未设；用户口径=浮亏即卖）
    "lose_next_day_sell": True,     # 持有次日收盘浮亏 → 卖（不达预期）
    "audit": {
        "assumptions": "开盘价成交无滑点；浮亏即卖=不达预期；"
                       "失守/下离枢=不在趋势上",
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


def _today_open_prices(codes):
    """新浪 L1 快照取当日开盘价（收盘后仍可取到当日 open）。"""
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
def cmd_open():
    acc = load_account()
    day = dt.date.today().strftime("%Y%m%d")
    picks = json.load(open(os.path.join(HERE, "..", "portal", "data",
                                        "todays_picks.json"),
                           encoding="utf-8"))
    # 1) 执行隔夜卖出单（价=开盘价）
    sells = [s["code"] for s in acc.get("pending_sells") or []]
    buy_codes = {b["code"] for b in acc.get("pending_buys") or []}
    px = _today_open_prices(sorted(set(sells) | buy_codes))
    for s in acc.get("pending_sells") or []:
        c = s["code"]
        pos = acc["positions"].get(c)
        quote = px.get(c)
        if not pos or not quote:
            continue
        price = quote["open"]
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
            print("[open] %s 无开盘价，跳过" % c)
            continue
        price = quote["open"]
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
def cmd_review():
    acc = load_account()
    day = dt.date.today().strftime("%Y%m%d")
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
        for s in t2:
            if s["code"] in acc["positions"]:
                continue   # 已持仓：加仓信号在 open 阶段处理
            pb.append({"code": s["code"], "name": s.get("name"),
                       "tier": "T2",
                       "sources": [{"s": x.get("s"), "tag": x.get("tag"),
                                    "dt": x.get("dt")}
                                   for x in s.get("sources") or []]})
        # 弱势日仓位减半建仓（盘面联动；rules_v1 口径）
        rg = picks.get("risk_gate") or {}
        if rg.get("weak_day"):
            print("[review] 弱势日：明日建仓名单仅保留前 6 只（半仓试错）")
            pb = pb[:6]
        acc["pending_buys"] = pb
    except Exception as e:  # noqa: BLE001
        print("[review] 名单读取失败（保留空挂单）：%s" % str(e)[:60])

    _atomic_write_json(ACC, acc)
    print("[review] 明日卖出 %d 单：%s" % (len(pending_sells),
          "、".join(s["code"] for s in pending_sells) or "无"))


# ── 收盘净值 ───────────────────────────────────────────────────────
def cmd_equity():
    acc = load_account()
    day = dt.date.today().strftime("%Y%m%d")
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
    print("模拟账户 %s（rules %s）" % (acc.get("created"),
                                       acc["rules"]["version"]))
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
    ap = argparse.ArgumentParser(description="模拟盘 v0.1（规则调校+训练数据）")
    ap.add_argument("cmd", choices=["open", "review", "equity", "status"])
    a = ap.parse_args()
    return {"open": cmd_open, "review": cmd_review,
            "equity": cmd_equity, "status": cmd_status}[a.cmd]()


if __name__ == "__main__":
    sys.exit(main())
