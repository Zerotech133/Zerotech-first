# -*- coding: utf-8 -*-
"""池子观察台：把「选」和「买」分开之后，池子该看什么（K-RES-20261108-BOARD）。

前提（老田 11-08 定调）
----------------------
一买二买三买不等于买卖。买点只负责**入池**，出不出手由势和时机决定。
所以池子需要的不是「谁要涨」的预测，而是一张**离出手还差几条、本周在补
还是在丢**的账。

六条条件，不加权、不拟合
------------------------
每一条都能从当天数据直接判真伪，没有一条需要猜：

  C1 结构未破    stage != broken 且信号后最低点未破信号价 8%
  C2 中枢上移    pv_up == 1（笔序列新高，中枢在抬）
  C3 站上 ZG     zone == above
  C4 位置合格    现价与 ZG 的距离 ≤8%（下方 8% 内 = 快够到；上方 8% 内 = 刚站上没追高）
  C5 止损可用    生死线余量 3%~20%（<3% 噪音就打掉；>20% 等于没有；已破的直接出局）
  C6 类型过关    1买（2买 任何持有期实测为负，只留观察；3买 出池）

ready = 命中条数 / 6。**ready 只决定看的顺序，不决定买**——六条全中也只是
「够格等势」。

超前看的是「变化」，不是「分数」
--------------------------------
分数是静态的：它只说今天什么样，说不了明天。能超前一点的只有一件事——
**距离触发条件还有多远，以及这个距离在本周是缩短还是拉长**。

所以每天另算两个位移量，都从价格面板直接取，不重算缠论；面板是复权价、
池子里是原始价，两者差一个复权因子，因此只传递**比值**，绝不直接比价位：

  d5_zg   近 5 日「到 ZG 的距离」缩短了多少 pp（正 = 在向门槛靠近）
  d5_dth  近 5 日「生死线余量」缩了多少 pp（正 = 在逼近生死线，恶化）
  d5_ret  近 5 日涨跌（%）

**距离变近有两个来路，必须用 d5_ret 分开**：
  · 在 ZG 下方往上够 → 蓄势待突破，这才是「超前看」要看的；
  · 在 ZG 上方往下掉 → 回落触线，是恶化不是蓄势。
大业股份 5 日 Δ+46.7pp 就是后者：它从 ZG 上方 53% 处跌到 6.6%，跌了 30%。
只看「变近」会把它排到第一，那是把崩塌当成了蓄势。

一只 4/6 但本周靠近 6pp 的票，比一只 5/6 但一个月没动的票更值得盯——
前者在走向可出手，后者只是停在那里。这就是「超前看」能看的全部，
再往前就是预测，本脚本不做。

不能回测，只能往前攒
--------------------
池子只有 as-of 当日的快照，历史上的 ready 无法重建，所以「ready 高的票
后续是否更好」**现在无法验证**。这正是每天往 observe_log.jsonl 追加一行
的理由：三个月后才有可能回答这个问题。在那之前，这张表是观察工具，
不是选股信号，任何按 ready 排序买入的做法都没有依据。

用法:
    python tools/research/observe_board.py
    python tools/research/observe_board.py --asof 20261106 --top 12
    python tools/research/observe_board.py --no-log        # 自检用，不写留痕
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)

from portfolio_mtm import load_price_panel  # noqa: E402

POOL = os.path.join(ROOT, "portal", "chanlun_pool_20261106.json")
TDX = os.path.join(ROOT, "quant_backtest", "data", "tdx")
OUT = os.path.join(ROOT, "portal", "observe_board.json")
LOG = os.path.join(ROOT, "portal", "observe_log.jsonl")

COND = ("C1结构未破", "C2中枢上移", "C3站上ZG", "C4位置合格", "C5止损可用", "C6类型过关")

NEAR_PP = 1.5      # 5 日内缩短 ≥1.5pp 才算「在靠近」
WORSEN_PP = 1.5    # 5 日内余量缩 ≥1.5pp 才算「在恶化」


def conds(r, gap_zg):
    """六条可观测条件，逐条 True/False，不加权。"""
    brk = r.get("break_pct")
    return [
        r.get("stage") != "broken" and (brk if brk is not None else 0.0) >= -8.0,  # C1
        r.get("pv_up") == 1,                                                     # C2
        r.get("zone") == "above",                                                # C3
        gap_zg is not None and gap_zg <= 8.0,                                    # C4
        r.get("death_state") == "valid",                                         # C5
        r.get("type") == "1买",                                                  # C6
    ]


def load_ind(asof):
    """申万一级行业（PIT）。前端要「按板块布局」，而池子本身不带行业字段。

    取 as-of 当日的 PIT 映射，不用当前快照，否则历史快照会带上未来信息。
    缺映射的票标「未分类」，不丢数据。
    """
    try:
        from pit_industry import load_sw_l1_industry
        s = load_sw_l1_industry().at("%s-%s-%s" % (asof[:4], asof[4:6], asof[6:]))
        return {str(k): str(v) for k, v in s.items()}
    except Exception as e:                                   # noqa: BLE001
        print("  ! 行业映射不可用（%s），将全部标为未分类" % e)
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", default=POOL)
    ap.add_argument("--asof", default=None)
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--log", default=LOG)
    ap.add_argument("--no-log", action="store_true", help="不写当日快照（自检用）")
    a = ap.parse_args()

    d = json.load(open(a.pool, encoding="utf-8"))
    rows = d["pool"]
    asof = (a.asof or d["asof"]).replace("-", "")[:8]   # 池子里写的是 2026-11-06
    IND = load_ind(asof)
    P = load_price_panel(sorted({r["code"] for r in rows}), TDX)

    def back(code, n):
        """n 个交易日前的复权收盘；不足 n 天返回 None。"""
        b = P.get(code)
        if not b or asof not in b["pos"]:
            return None
        i = b["pos"][asof]
        return b["c"][i - n] if i - n >= 0 else None

    def cnow(code):
        b = P.get(code)
        if not b or asof not in b["pos"]:
            return None
        return b["c"][b["pos"][asof]]

    out = []
    for r in rows:
        cn = cnow(r["code"])
        if not cn:
            continue
        # 面板复权价与池子原始价差一个因子；比值在两者间通用，绝对价不通用。
        px = float(r.get("px") or cn)
        zg, dth = r.get("zg"), r.get("death_px")

        gap_zg_s = (px / zg - 1.0) * 100 if zg else None      # 带符号：负=在 ZG 下方
        gap_zg = abs(gap_zg_s) if gap_zg_s is not None else None
        gap_dth = (px / dth - 1.0) * 100 if dth else None

        def shift(n):
            """把 n 日前的复权价换算成「以池子原始价为单位」的价位。"""
            prev = back(r["code"], n)
            return None if prev is None else px * (prev / cn)

        def d_gap(n, ref):
            """n 日内「到 ref 的距离」缩短了多少 pp（正 = 靠近）。"""
            p = shift(n)
            if p is None or not ref:
                return None
            return abs(p / ref - 1.0) * 100 - abs(px / ref - 1.0) * 100

        def d_room(n, ref):
            """n 日内「生死线余量」缩了多少 pp（正 = 恶化）。"""
            p = shift(n)
            if p is None or not ref:
                return None
            return (p / ref - 1.0) * 100 - (px / ref - 1.0) * 100

        cs = conds(r, gap_zg)
        g5, g10 = d_gap(5, zg), d_gap(10, zg)
        rm5 = d_room(5, dth)
        p5 = shift(5)
        out.append({
            "code": r["code"], "name": r["name"], "type": r["type"],
            "ind": IND.get(r["code"], "未分类"),
            "px": round(px, 2), "grade": r.get("grade"), "stage": r.get("stage"),
            "zone": r.get("zone"), "death_state": r.get("death_state"),
            "ready": sum(cs), "c": "".join("1" if x else "0" for x in cs),
            "fails": [n for n, x in zip(COND, cs) if not x],
            "gap_zg": None if gap_zg is None else round(gap_zg, 2),
            "gap_zg_s": None if gap_zg_s is None else round(gap_zg_s, 2),
            "gap_dth": None if gap_dth is None else round(gap_dth, 2),
            "d5_zg": None if g5 is None else round(g5, 2),
            "d10_zg": None if g10 is None else round(g10, 2),
            "d5_dth": None if rm5 is None else round(rm5, 2),
            "d5_ret": None if p5 is None else round((px / p5 - 1.0) * 100, 2),
            "sig_age": r.get("sig_age"), "break_pct": r.get("break_pct"),
        })

    say = print
    reg = d.get("regime", {}) or {}
    say("=" * 106)
    say("池子观察台｜as-of %s｜入池 %d 只｜势 %s（%s）"
        % (asof, len(out), reg.get("grade"), reg.get("primary")))
    say("=" * 106)
    say("评分 = 六条可观测条件的命中数：C1结构未破 / C2中枢上移 / C3站上ZG / "
        "C4位置合格 / C5止损可用 / C6类型过关")
    say("ready 只决定看的顺序，不决定买。买不买由势和时机决定。")

    dist = Counter(x["ready"] for x in out)
    say("\n【就绪度】" + "  ".join("%d/6:%d只" % (k, dist[k]) for k in range(7) if dist.get(k))
        + "    中位 %d/6" % sorted(x["ready"] for x in out)[len(out) // 2])

    say("\n【池子在告诉你什么】按缺的条件统计（一只可缺多条）")
    fc = Counter(n for x in out for n in x["fails"])
    for n in COND:
        say("  缺 %-10s %3d 只（%.0f%%）" % (n, fc.get(n, 0), fc.get(n, 0) / len(out) * 100))

    # A 栏：够格等势（类型必须过关，否则五条全中也不够格）
    a_lst = sorted([x for x in out if x["ready"] >= 5 and x["type"] == "1买"],
                   key=lambda z: (-z["ready"], z["gap_zg"] if z["gap_zg"] is not None else 999))
    n_full = sum(1 for x in a_lst if x["ready"] == 6)
    say("\n" + "-" * 106)
    say("A｜够格等势（1买 且 ready≥5，%d 只，其中六条全中 %d 只）⚠=5日跌超10%%（砸下来的）"
        % (len(a_lst), n_full))
    say("-" * 106)
    if a_lst:
        say("  %-12s %-8s %-6s %7s %8s %8s %9s %-8s"
            % ("代码", "名称", "条件", "距ZG", "5日Δ", "生死余量", "5日涨跌", "止损态"))
        for x in a_lst[:a.top]:
            # 从上方砸下来的（5 日跌 ≥10%）不是蓄势，标出来，别让它混在名单里
            flag = "⚠" if (x["d5_ret"] or 0) <= -10.0 else " "
            say("  %-12s %-8s %-6s %+6.2f%% %+7.2fpp %+7.2f%% %+8.2f%%%s%-8s"
                % (x["code"], x["name"][:8], x["c"], x["gap_zg_s"] or 0,
                   x["d5_zg"] or 0, x["gap_dth"] or 0, x["d5_ret"] or 0,
                   flag, x["death_state"]))
    else:
        say("  无。没有一只 1买 同时满足五条以上。")

    # B 栏：蓄势 —— 必须「在 ZG 下方、往上够」，否则是把崩塌当蓄势
    #
    # 只用「变近 + 上涨」会筛出 222 只，比池子的一半还多，等于没筛。真正的
    # 蓄势要同时成立：结构没破（C1）、中枢在抬（C2）、类型还在可做范围（1/2买）、
    # 距离够近（8% 以内，几周内够得到）、本周在向上靠近。这样剩 17 只。
    b_lst = sorted([x for x in out
                    if (x["gap_zg_s"] or 0) < 0 and (x["gap_zg_s"] or 0) >= -8.0
                    and x["c"][0] == "1" and x["c"][1] == "1"
                    and x["type"] in ("1买", "2买")
                    and (x["d5_zg"] or -99) >= NEAR_PP and (x["d5_ret"] or 0) > 0],
                   key=lambda z: -z["d5_zg"])
    say("\n" + "-" * 106)
    say("B｜蓄势待突破（未破+中枢上移+1/2买+仍距 ZG 8%% 内+本周向上靠近 ≥%.1fpp，%d 只）"
        "——超前看的就是这一栏" % (NEAR_PP, len(b_lst)))
    say("-" * 106)
    if b_lst:
        say("  %-12s %-8s %-4s %-6s %7s %8s %9s  %s"
            % ("代码", "名称", "类型", "条件", "距ZG", "5日Δ", "5日涨跌", "还缺"))
        for x in b_lst[:a.top]:
            say("  %-12s %-8s %-4s %-6s %+6.2f%% %+7.2fpp %+8.2f%%  %s"
                % (x["code"], x["name"][:8], x["type"], x["c"], x["gap_zg_s"] or 0,
                   x["d5_zg"] or 0, x["d5_ret"] or 0, ",".join(x["fails"])))
    else:
        say("  无：没有一只在从下方往上够门槛。池子在退潮，不在蓄势。")

    # C 栏：恶化 —— 余量在缩，且是价格在跌（不是结构线被挪动）
    c_lst = sorted([x for x in out if x["death_state"] in ("valid", "tight")
                    and (x["d5_dth"] or -99) >= WORSEN_PP and (x["d5_ret"] or 0) < 0],
                   key=lambda z: -z["d5_dth"])
    say("\n" + "-" * 106)
    say("C｜本周在恶化（余量 5 日内缩 ≥%.1fpp，%d 只）——这批从「等」改成「盯」"
        % (WORSEN_PP, len(c_lst)))
    say("-" * 106)
    if c_lst:
        say("  %-12s %-8s %-4s %9s %8s %9s %7s %-8s"
            % ("代码", "名称", "类型", "生死余量", "5日Δ", "5日涨跌", "距ZG", "止损态"))
        for x in c_lst[:min(8, a.top)]:
            say("  %-12s %-8s %-4s %+8.2f%% %+7.2fpp %+8.2f%% %+6.2f%% %-8s"
                % (x["code"], x["name"][:8], x["type"], x["gap_dth"],
                   x["d5_dth"], x["d5_ret"] or 0, x["gap_zg_s"] or 0, x["death_state"]))
    else:
        say("  无。")

    say("\n" + "=" * 106)
    say("结论：%d 只够格（ready≥5），%d 只本周在靠近，%d 只本周在恶化；势 %s。"
        % (len(a_lst), len(b_lst), len(c_lst), reg.get("grade")))
    say("      够格不等于买——势这一步还没过；靠近不等于要买——只是「先看这一批」。")
    say("=" * 106)

    # 三栏的入选名单由脚本给出，前端只负责渲染。
    # 前端若自己再算一遍，阈值改一处就会两边不一样，而且没人会发现。
    views = {
        "a": [x["code"] for x in a_lst],
        "b": [x["code"] for x in b_lst],
        "c": [x["code"] for x in c_lst],
    }

    # 分组也由脚本给：前端「按板块 / 按买点」的卡片布局直接读这里，
    # 不在页面上自己 groupBy，否则两边的分组口径会漂。
    def group_by(keyfn):
        g = {}
        for x in out:
            g.setdefault(str(keyfn(x) or "未分类"), []).append(x["code"])
        return dict(sorted(g.items(), key=lambda kv: -len(kv[1])))

    groups = {
        "by_ind": group_by(lambda x: x["ind"]),
        "by_type": group_by(lambda x: x["type"]),
        "by_grade": group_by(lambda x: x["grade"]),
        "by_stage": group_by(lambda x: x["stage"]),
    }

    with open(a.out, "w", encoding="utf-8") as f:
        json.dump({"asof": asof, "regime": reg, "n": len(out),
                   "by_ready": {int(k): int(v) for k, v in dist.items()},
                   "cond_missing": {n: fc.get(n, 0) for n in COND},
                   "cond_names": list(COND),
                   "views": views,
                   "groups": groups,
                   "counts": {"a": len(a_lst), "b": len(b_lst), "c": len(c_lst),
                              "full": n_full, "near_pp": NEAR_PP, "worsen_pp": WORSEN_PP},
                   "rows": out}, f, ensure_ascii=False, indent=1)

    if not a.no_log:
        rec = {"d": asof, "regime": reg.get("grade"), "n": len(out),
               "rows": [{"code": x["code"], "ready": x["ready"], "c": x["c"],
                         "gap_zg": x["gap_zg_s"], "gap_dth": x["gap_dth"],
                         "d5_zg": x["d5_zg"], "d5_ret": x["d5_ret"], "px": x["px"],
                         "stage": x["stage"], "death_state": x["death_state"]}
                        for x in out]}
        hist = {}
        if os.path.exists(a.log):
            for ln in open(a.log, encoding="utf-8"):
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    o = json.loads(ln)
                    hist[o["d"]] = ln
                except (ValueError, KeyError):
                    continue
        hist[asof] = json.dumps(rec, ensure_ascii=False)
        with open(a.log, "w", encoding="utf-8") as f:
            f.write("\n".join(hist[k] for k in sorted(hist)) + "\n")
        say("\n快照 %d 日已留存 -> %s（将来检验 ready 有没有前瞻性只能靠它）"
            % (len(hist), os.path.basename(a.log)))

    print("\nwritten -> %s" % a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
