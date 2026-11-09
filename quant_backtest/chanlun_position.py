# -*- coding: utf-8 -*-
"""缠论持仓卡 K24 · v3 #9 卖出规则链状态机（+#10 趋势过滤联动）。

纪律链（学习素材批④ 交易纪律图 → K21 作战六环⑤持仓环）：
  [1] 持有 —— 次级别无顶分型、末笔向上延伸
  [2] 警戒 —— 次级别顶分型形成；回踩关键低不破 = 「不转折」继续持有
  [3] 减仓 —— 次级别背驰 / 1卖·2卖信号
  [4] 清仓 —— 次级别 3 卖（跌破中枢 ZD 反抽不进）
  [5] 终结 —— 日线反向笔跌破左侧低（HL 失守）= 趋势终结·无条件离场
  优先级：5 > 4 > 3 > 2 > 1（链上最深触发态即当前状态）。

只读工具：不替人做决定，只给「当前链位 + 依据 + 下一触发价位」；
数据与推演卡同源（chanlun_card.level_view），零新计算。

用法：
  python chanlun_position.py 600337.SH --cost 3.50
  python chanlun_position.py 301216.SZ --cost 16.40 --json
"""

import json
import sys

from chanlun_card import LEVELS, ANCHOR_BIS, level_view
import logging

_STATE_NAME = {1: "持有", 2: "警戒", 3: "减仓", 4: "清仓", 5: "终结"}


def sell_chain(views):
    """#9 卖出链状态机：三级联立数据 → 当前链位 + 链上五态明细。"""
    d = views.get("1d") or {}
    sub = views.get("30m") or views.get("5m") or {}
    sub_name = "30m" if views.get("30m") else "5m"

    # ── 逐态判定 ─────────────────────────────────────
    # [5] 趋势终结：日线末笔向下且跌破前一 HL（左侧低）
    end_hit, end_lv = False, None
    bt = d.get("bis_tail") or []
    if bt and bt[-1]["dir"] == -1:
        prev_dns = [b for b in bt[:-1] if b["dir"] == -1]
        if prev_dns and bt[-1]["e_price"] < prev_dns[-1]["e_price"]:
            end_hit, end_lv = True, prev_dns[-1]["e_price"]
    elif d.get("trend"):
        dns = [b for b in bt if b["dir"] == -1]
        end_lv = dns[-2]["e_price"] if len(dns) >= 2 else None

    sigs = sub.get("signals_recent") or []
    s3_list = [s for s in sigs if s["type"] in ("1卖", "2卖")]
    s4_list = [s for s in sigs if s["type"] == "3卖"]

    # [2] 警戒：次级别顶分型且晚于末笔终点（与三灯灯2 对称）
    lb, fr = sub.get("last_bi"), sub.get("last_fractal")
    top_fr = lb and fr and fr["type"] == "顶" and str(fr["dt"]) >= str(lb["e_dt"])
    # 回踩不破（s2 子判定）：现价仍高于次级别关键低 → 不转折持有
    sub_lo = (sub.get("anchors") or {}).get("low_%d" % ANCHOR_BIS)
    hold_on_dip = top_fr and sub_lo and d.get("last_px", 0) > sub_lo

    if end_hit:
        state = 5
    elif s4_list:
        state = 4
    elif s3_list:
        state = 3
    elif top_fr:
        state = 2
    else:
        state = 1

    # ── 触发价位表 ────────────────────────────────────
    sub_zd = (sub.get("pivot") or {}).get("zd")
    chain = []
    chain.append(
        {
            "n": 1,
            "name": _STATE_NAME[1],
            "hit": state == 1,
            "desc": "%s 无顶分型、末笔%s延伸" % (sub_name, lb["dir"] if lb else "?"),
        }
    )
    chain.append(
        {
            "n": 2,
            "name": _STATE_NAME[2],
            "hit": state == 2,
            "desc": (
                "%s 顶分型@%s%s"
                % (
                    sub_name,
                    ("%.2f" % fr["price"]) if fr else "?",
                    "（回踩 %.2f 未破·不转折持有）" % sub_lo if hold_on_dip else "",
                )
                if top_fr
                else "%s 向上笔终结出顶分型时" % sub_name
            ),
        }
    )
    chain.append(
        {
            "n": 3,
            "name": _STATE_NAME[3],
            "hit": state == 3,
            "desc": (
                "已触发：%s"
                % "；".join(
                    "%s@%.2f(%s)" % (s["type"], s["price"], str(s["dt"])[:16])
                    for s in s3_list[-2:]
                )
            )
            if s3_list
            else "次级别背驰 / 1卖·2卖出现时",
        }
    )
    chain.append(
        {
            "n": 4,
            "name": _STATE_NAME[4],
            "hit": state == 4,
            "desc": (
                "%s 跌破 %.2f 反抽不进 = 3卖%s"
                % (
                    sub_name,
                    sub_zd,
                    "（已触发：%s）"
                    % "；".join(
                        "%s@%.2f" % (s["type"], s["price"]) for s in s4_list[-2:]
                    )
                    if s4_list
                    else "",
                )
                if sub_zd
                else "次级别中枢 ZD 未知"
            ),
        }
    )
    chain.append(
        {
            "n": 5,
            "name": _STATE_NAME[5],
            "hit": state == 5,
            "desc": (
                "日线跌破前低 HL %.2f = 趋势终结·无条件离场%s"
                % (end_lv, "（已触发）" if end_hit else "")
            )
            if end_lv
            else "日线前低 HL 未知",
        }
    )

    why = ""
    if state == 5:
        why = "日线末笔 %.2f 已破左侧低 %.2f" % (bt[-1]["e_price"], end_lv)
    elif state == 4:
        why = "%s 近端 3卖" % sub_name
    elif state == 3:
        why = "%s 近端 %s" % (sub_name, "/".join(s["type"] for s in s3_list[-2:]))
    elif state == 2:
        why = "%s 顶分型@%s" % (sub_name, ("%.2f" % fr["price"]) if fr else "?")
        if hold_on_dip:
            why += "，但回踩 %.2f 未破（不转折·持有）" % sub_lo
    else:
        why = "%s 结构正常（无顶分型/卖点信号）" % sub_name
    return {
        "state": state,
        "state_name": _STATE_NAME[state],
        "why": why,
        "chain": chain,
        "sub_level": sub_name,
        "trend": d.get("trend"),
        "triggers": {
            "top_fractal_px": (fr or {}).get("price"),
            "sub_hold_px": sub_lo,
            "sub_3s_zd": sub_zd,
            "daily_hl_prev": end_lv,
        },
    }


def position_card(code, cost=None):
    """持仓卡组装：三级联立视图 + 卖出链 + 趋势过滤 + 盈亏。"""
    views = {p: level_view(code, p) for p in LEVELS}
    sc = sell_chain(views)
    d = views.get("1d") or {}
    px = d.get("last_px")
    pnl = ((px - cost) / cost * 100) if (px and cost) else None
    return {
        "code": code,
        "cost": cost,
        "last_px": px,
        "pnl_pct": pnl,
        "levels": views,
        "sell_chain": sc,
    }


def _print(c):
    sc = c["sell_chain"]
    lab = {"1d": "日线", "30m": "30 分钟", "5m": "5 分钟"}
    print("═" * 62)
    head = "持仓卡 · %s" % c["code"]
    if c.get("cost"):
        head += " · 成本 %.2f · 现价 %.2f（%+.1f%%）" % (
            c["cost"],
            c["last_px"],
            c["pnl_pct"],
        )
    print(head)
    tr = sc.get("trend") or {}
    tr_s = {"up": "↑上升", "down": "↓下降"}.get(tr.get("state"), "→震荡")
    if tr.get("short_adjust"):
        tr_s += " · 长升短调✓"
    if tr.get("hh") and tr.get("hl"):
        tr_s += "（HH %.2f / HL %.2f）" % (tr["hh"], tr["hl"])
    print("─" * 62)
    print("趋势过滤（#10）：日线 %s" % tr_s)
    if tr.get("state") != "up":
        print("  ⚠ 非上升趋势——纪律图：永远只做上升趋势（反弹按短线处理）")
    print("卖出链状态机（#9）：【%d %s】" % (sc["state"], sc["state_name"]))
    print("  依据：%s" % sc["why"])
    for it in sc["chain"]:
        mark = "▸" if it["hit"] else "·"
        print("  %s [%d] %-6s %s" % (mark, it["n"], it["name"], it["desc"]))
    t = sc.get("triggers") or {}
    print("─" * 62)
    print(
        "关键价位：3卖结构位 %s · 趋势终结 %s%s"
        % (
            ("%.2f" % t["sub_3s_zd"]) if t.get("sub_3s_zd") else "—",
            ("%.2f" % t["daily_hl_prev"]) if t.get("daily_hl_prev") else "—",
            (
                "（距现价 %+.1f%%）"
                % ((t["daily_hl_prev"] - c["last_px"]) / c["last_px"] * 100)
                if (t.get("daily_hl_prev") and c.get("last_px"))
                else ""
            ),
        )
    )
    for p in ("1d", "30m", "5m"):
        v = c["levels"].get(p)
        if not v:
            continue
        b = v["last_bi"]
        print(
            "  [%s] 末笔：%s %.2f→%.2f（%s~%s）"
            % (
                lab[p],
                b["dir"],
                b["s_price"],
                b["e_price"],
                str(b["s_dt"])[5:16],
                str(b["e_dt"])[5:16],
            )
        )
        # 次次级别回落预警：5m/30m 与主判定级别方向相反时提示
        if (
            p == "5m"
            and sc["sub_level"] == "30m"
            and b["dir"] == "下"
            and c.get("last_px")
            and c["last_px"] < b["s_price"]
        ):
            print(
                "  ⚠ 5 分钟已回落（%.2f→%.2f）·观察 30m 是否跟进"
                % (b["s_price"], b["e_price"])
            )
    print("═" * 62)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    code = sys.argv[1]
    cost = None
    if "--cost" in sys.argv:
        try:
            cost = float(sys.argv[sys.argv.index("--cost") + 1])
        except (ValueError, IndexError) as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/chanlun_position.py", exc)
    c = position_card(code, cost)
    if "--json" in sys.argv:
        print(json.dumps(c, ensure_ascii=False, indent=1, default=str))
    else:
        _print(c)


if __name__ == "__main__":
    main()
