# -*- coding: utf-8 -*-
"""资金迁徙查询层（K197 · 蓝图 B 阶段）。

从 TRADED_ON（席位×票×日 net_buy，20230731~20260930）推导迁徙：
  迁出：席位 d 日于票 X 净卖出（卖出额 ≥ 阈值）
  迁入：同席位在 d/d+1/d2 交易日于票 Y 净买入（Y≠X）
守恒打分：窗口内 Σ净卖出 vs Σ净买入（剔除 X 回补）差比 → 迁联置信度。
社区接力：同 EXP_Community 内其他席位同向（依赖 K196-P2 IN_GROUP 边）。

用法：python kg_migration.py <hotmoney_name> [days=60] [--min-amt 3000000]
"""

import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from kg_client import KgClient  # noqa: E402


def _next_td(s, d, k=2):
    """d(YYYYMMDD) 的后 k 个交易日（NEXT_TRADE_DAY 链）。"""
    out = []
    cur = d
    for _ in range(k):
        r = s.run(
            "MATCH (a:TradeDate {date:$d})-[:NEXT_TRADE_DAY]->(b) "
            "RETURN b.date AS nd LIMIT 1",
            d=cur,
        ).single()
        if not r or not r["nd"]:
            break
        cur = r["nd"]
        out.append(cur)
    return out


def hotmoney_seats(s, hm_name):
    """HotMoney → OCCUPIES → 席位名集。"""
    return [
        r["n"]
        for r in s.run(
            "MATCH (h:HotMoney {name:$n})-[:OCCUPIES]->(s:Seat) RETURN s.name AS n",
            n=hm_name,
        ).data()
    ]


def seat_flows(s, seat, d0, d1):
    """席位在 [d0,d1] 的日度资金流。"""
    return s.run(
        "MATCH (s:Seat {name:$n})-[t:TRADED_ON]->(sec:Security) "
        "WHERE t.validStart >= $d0 AND t.validStart <= $d1 "
        "RETURN t.validStart AS d, sec.ts_code AS code, sec.name AS name, "
        "t.net_buy AS nb, t.buy AS b, t.sell AS sl ORDER BY d",
        n=seat,
        d0=d0,
        d1=d1,
    ).data()


def derive_migrations(flows, min_amt, next_td):
    """日度流 → 候选迁徙对（卖出日 d → d/d+1/d2 净买 Y≠X）。"""
    by_date = {}
    for r in flows:
        by_date.setdefault(r["d"], []).append(r)
    dates = sorted(by_date)
    nxt = {}
    for d in dates:
        chain = next_td(d)
        nxt[d] = chain
    window = set([d for d in dates] + [x for c in nxt.values() for x in c])
    out = []
    for d in dates:
        sells = [
            (r["code"], r["name"], -r["nb"])
            for r in by_date[d]
            if r["nb"] is not None and r["nb"] <= -min_amt
        ]
        if not sells:
            continue
        buy_days = [x for x in [d] + nxt.get(d, []) if x in by_date]
        buys = [
            (r["code"], r["name"], r["nb"], dd)
            for dd in buy_days
            for r in by_date[dd]
            if r["nb"] is not None and r["nb"] >= min_amt
        ]
        for sc, sn, amt in sells:
            for bc, bn, b, dd in buys:
                if bc == sc:
                    continue
                out.append(
                    {
                        "date": d,
                        "sell_code": sc,
                        "sell_name": sn,
                        "sell_amt": amt,
                        "buy_date": dd,
                        "buy_code": bc,
                        "buy_name": bn,
                        "buy_amt": b,
                        "lag": (int(dd) - int(d)) if dd.isdigit() else None,
                    }
                )
    return out


def conservation(flows, min_amt):
    """窗口守恒：Σ净卖出 vs Σ净买入（同票对冲剔除），差比 → 置信度。"""
    sell = sum(-r["nb"] for r in flows if r["nb"] is not None and r["nb"] <= -min_amt)
    buy = sum(r["nb"] for r in flows if r["nb"] is not None and r["nb"] >= min_amt)
    if sell <= 0:
        return None
    ratio = min(buy, sell) / max(buy, sell) if max(buy, sell) else 0.0
    return {"sell": sell, "buy": buy, "ratio": round(ratio, 3)}


def community_relay(s, seat, d0, d1, min_amt):
    """社区接力：同 EXP_Community 其他席位在迁入窗口的同向净买。"""
    return s.run(
        "MATCH (s0:Seat {name:$seat})-[:IN_GROUP]->(c:EXP_Community)"
        "<-[:IN_GROUP]-(s1:Seat) "
        "MATCH (s1)-[t:TRADED_ON]->(sec:Security) "
        "WHERE s1.name <> $seat AND t.validStart >= $d0 "
        "AND t.validStart <= $d1 AND t.net_buy >= $amt "
        "RETURN s1.name AS seat, c.name AS grp, sec.ts_code AS code, "
        "sec.name AS name, t.net_buy AS nb, t.validStart AS d "
        "ORDER BY nb DESC LIMIT 12",
        seat=seat,
        d0=d0,
        d1=d1,
        amt=min_amt,
    ).data()


def main():
    hm = sys.argv[1] if len(sys.argv) > 1 else "陈小群"
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    min_amt = float(sys.argv[3]) if len(sys.argv) > 3 else 3_000_000
    cli = KgClient(retry=1)
    d1 = datetime.now().strftime("%Y%m%d")
    with cli._session() as s:
        seats = hotmoney_seats(s, hm)
        print("== %s 席位（OCCUPIES）: %d ==" % (hm, len(seats)))
        for st in seats:
            print("  ·", st[:50])
        if not seats:
            print("（无 OCCUPIES 席位——HotMoney 层未挂接）")
            return
        d0 = s.run(
            "MATCH (:Seat)-[t:TRADED_ON]->() RETURN max(t.validStart) AS mx"
        ).single()["mx"]
        from datetime import timedelta

        d0 = (datetime.strptime(d1, "%Y%m%d") - timedelta(days=days)).strftime("%Y%m%d")
        all_mig, cons = [], []
        for st in seats:
            flows = seat_flows(s, st, d0, d1)
            if not flows:
                continue
            migs = derive_migrations(flows, min_amt, lambda d, s=s: _next_td(s, d))
            c = conservation(flows, min_amt)
            if c:
                cons.append((st, c))
            all_mig += [dict(m, seat=st) for m in migs]
        all_mig.sort(key=lambda m: -(min(m["sell_amt"], m["buy_amt"])))
        print("== 迁徙候选（|净买卖|≥%.0f 万，top 15）==" % (min_amt / 1e4))
        for m in all_mig[:15]:
            print(
                "  %s %s → %s %s  卖 %.0f万 / 买 %.0f万  (lag %s, %s)"
                % (
                    m["date"],
                    m["sell_code"],
                    m["buy_code"],
                    (m["buy_name"] or "")[:8],
                    m["sell_amt"] / 1e4,
                    m["buy_amt"] / 1e4,
                    m["lag"],
                    m["seat"][:24],
                )
            )
        print("== 席位守恒（窗口 %d 天）==" % days)
        for st, c in cons[:8]:
            print(
                "  %s: sell %.0f万 buy %.0f万 ratio=%.3f"
                % (st[:36], c["sell"] / 1e4, c["buy"] / 1e4, c["ratio"])
            )
        print("== 社区接力（同集团同向净买，top 8）==")
        for st in seats[:3]:
            for r in community_relay(s, st, d0, d1, min_amt):
                print(
                    "  [%s] %s → %s %s  %.0f万 (%s)"
                    % (
                        (r["grp"] or "")[:12],
                        st[:20],
                        r["code"],
                        (r["name"] or "")[:8],
                        (r["nb"] or 0) / 1e4,
                        r["d"],
                    )
                )


if __name__ == "__main__":
    main()
