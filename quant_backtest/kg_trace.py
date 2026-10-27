# -*- coding: utf-8 -*-
"""M2-W3：证据链双向查询（正向传导树 + 逆向 ACH 三假设打分）。

用法：
  python kg_trace.py 002261.SZ              # 全景（fwd + ach，近 30 天）
  python kg_trace.py 002261.SZ --days 60
  python kg_trace.py 002261.SZ --fwd        # 仅正向：事件→盘面传导树
  python kg_trace.py 002261.SZ --ach        # 仅逆向：ACH 三假设

口径：
· 正向 --fwd：标的的 MarketEvent 时间倒序，每事件列 DRIVE 来源
  （group / lag_days / p；notice 为高密度基线组单独标注）
· 逆向 --ach 三假设（克制规则版，分数仅排序辅助，证据列表为主）：
  H1 事件驱动   = 窗口内实质主题组 DRIVE 边数（回购增持/中标合同/资产收购…，非 notice 基线）
  H2 资金驱动   = 游资席位 TRADED_ON 净买痕迹（净买额 + 主体数）
  H3 博弈驱动   = 量能事件密度 × 无佐证信号占比（纯量价行为，无外部事件）
· Event 流 2026-08-29 起沉淀：窗口内"无佐证"可能是"无数据"，D0 前不采信
"""

import sys
import os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kg_client import KgClient

CODE = next((a for a in sys.argv[1:] if not a.startswith("-")), None)
DAYS = int(next((sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--days"), 30))
FWD = "--fwd" in sys.argv or not ("--ach" in sys.argv)
ACH = "--ach" in sys.argv or not ("--fwd" in sys.argv)

BASELINE_GRP = {"notice", "news", "jev"}  # 高密度基线组，不算实质主题证据


def _d0():
    return (datetime.now() - timedelta(days=DAYS)).strftime("%Y%m%d")


def head(s, code):
    r = s.run(
        "MATCH (n:Security {ts_code:$c}) RETURN n.name AS name, n.industry AS ind",
        c=code,
    ).single()
    if not r:
        print("未找到标的 %s" % code)
        return None
    print("=== %s %s | %s | 窗口 %d 天 ===" % (code, r["name"], r["ind"], DAYS))
    return r["name"]


def fwd(s, code, d0):
    print("\n── 正向传导树（盘面事件 ← DRIVE 来源）──")
    rows = list(
        s.run(
            """
        MATCH (sec:Security {ts_code:$c})-[:HAS_STRUCTURE|HAS_VOL_EVENT]->(m:MarketEvent)
        WHERE m.date >= $d0
        OPTIONAL MATCH (e:Event)-[d:DRIVE]->(m)
        RETURN m.date AS md, m.type AS mt, m.evt_id AS me,
               e.id AS eid, e.text AS txt, e.tag AS tag, e.kind AS ek,
               d.group AS g, d.lag_days AS lag, d.p AS p
        ORDER BY md DESC, g""",
            c=code,
            d0=d0,
        )
    )
    if not rows:
        print("  窗口内无盘面事件")
        return
    cur = None
    for r in rows:
        if r["me"] != cur:
            cur = r["me"]
            print("  %s %-3s" % (r["md"], r["mt"]))
        if r["eid"]:
            grp = r["g"] if r["g"] not in BASELINE_GRP else "%s(基线)" % r["g"]
            txt = (r["txt"] or "")[:36].replace("\n", " ")
            print("      <- [%s lag=%dd p=%.3f] %s" % (grp, r["lag"], r["p"] or 0, txt))


def ach(s, code, d0):
    print("\n── 逆向 ACH 三假设（规则版，窗口内证据）──")
    # H1 事件驱动
    h1 = list(
        s.run(
            """
        MATCH (e:Event)-[d:DRIVE]->(:MarketEvent)<-[:HAS_STRUCTURE|HAS_VOL_EVENT]-(sec:Security {ts_code:$c})
        WHERE d.validStart >= $d0 AND NOT coalesce(d.group,'') IN $base
        RETURN d.group AS g, count(*) AS n, collect(DISTINCT left(coalesce(e.text,''),30))[..2] AS texts
        ORDER BY n DESC""",
            c=code,
            d0=d0,
            base=list(BASELINE_GRP),
        )
    )
    h1_n = sum(r["n"] for r in h1)
    print("  H1 事件驱动: %d 条实质主题证据" % h1_n)
    for r in h1[:6]:
        print(
            "     - %s ×%d  如: %s"
            % (r["g"], r["n"], r["texts"][0] if r["texts"] else "")
        )

    # H2 资金驱动/确认（M2-W4c 校准修订：实证游资多为"跟随确认者"非驱动者——
    #    纯跟随确认 post10∈(0,1亿]&pre≤0 ret5 +11.6%；游资先行又回访=诱多 -4.2%）
    h2 = list(
        s.run(
            """
        MATCH (hm:HotMoney)-[:OCCUPIES]->(st:Seat)-[t:TRADED_ON]->(sec:Security {ts_code:$c})
        WHERE t.validStart >= $d0
        RETURN hm.name AS hm, count(*) AS n, sum(t.net_buy) AS net""",
            c=code,
            d0=d0,
        )
    )
    h2_net = sum(r["net"] for r in h2)
    tg = {}
    for r in s.run(
        """
            MATCH (sec:Security {ts_code:$c})-[r:HAS_STRUCTURE]->(m:MarketEvent)
            WHERE m.date >= $d0 AND r.hm_tag IS NOT NULL
            RETURN r.hm_tag AS t, count(*) AS n""",
        c=code,
        d0=d0,
    ):
        tg[r["t"]] = r["n"]
    n_fc, n_lw = tg.get("follow_confirm", 0), tg.get("lure_warn", 0)
    print(
        "  H2 资金: %d 个游资主体, 净买合计 %.0f 百万元；"
        "信号标注: 纯跟随确认 %d / 诱多警示 %d" % (len(h2), h2_net / 1e6, n_fc, n_lw)
    )
    for r in sorted(h2, key=lambda x: -(x["net"] or 0))[:6]:
        print("     - %-14s %d 次  净买 %+.0f 百万" % (r["hm"], r["n"], r["net"] / 1e6))

    # H3 博弈驱动
    h3 = list(
        s.run(
            """
        MATCH (sec:Security {ts_code:$c})-[r]->(m:MarketEvent)
        WHERE m.date >= $d0
        OPTIONAL MATCH (:Event)-[d:DRIVE]->(m)
        WITH m, type(r) AS rt, count(d) AS nd
        RETURN rt = 'HAS_VOL_EVENT' AS is_vol, count(*) AS n,
               sum(CASE WHEN nd = 0 THEN 1 ELSE 0 END) AS n_noev""",
            c=code,
            d0=d0,
        )
    )
    n_vol = n_sig = no_ev = 0
    for r in h3:
        if r["is_vol"]:
            n_vol = r["n"]
        else:
            n_sig = r["n"]
            no_ev = r["n_noev"]
    ev_min = s.run("MATCH (e:Event) RETURN min(e.date) AS d").single()["d"]
    d0_eff = max(d0, ev_min)  # Event 流起点前无数据，不采信"无佐证"
    print(
        "  H3 博弈驱动: 量能事件 %d, 结构信号 %d（其中无佐证 %d）"
        % (n_vol, n_sig, no_ev)
    )
    if d0_eff != d0:
        print('     （注: Event 流自 %s 起有数据，更早的"无佐证"未采信）' % ev_min)

    # 规则打分（透明可解释，仅排序辅助）
    s1 = min(1.0, h1_n / 4.0)
    s2 = min(1.0, max(0.0, h2_net) / 2e8 + len(h2) / 10.0 + n_fc * 0.25)
    s3 = min(1.0, n_vol / 8.0) * (0.5 + 0.5 * (n_sig and no_ev / n_sig or 0))
    scores = [("H1 事件驱动", s1), ("H2 资金驱动", s2), ("H3 博弈驱动", s3)]
    print("\n  假设支持度: %s" % " | ".join("%s %.2f" % (h, v) for h, v in scores))
    top = max(v for _, v in scores)
    if top <= 0:
        print("  收敛方向: 窗口内证据不足（全部假设支持度≈0），不下结论")
        return
    winners = [h for h, v in scores if abs(v - top) < 1e-9]
    if len(winners) > 1:
        print(
            "  收敛方向: %s 并列（规则版排序，供 ACH 人工复核起点）"
            % " 与 ".join(winners)
        )
    else:
        print("  收敛方向: %s（规则版排序，供 ACH 人工复核起点）" % winners[0])


def _concept_size(s, name):
    r = s.run(
        """
        MATCH (c:Concept {name:$n})<-[m:MAPS_TO]-(x:Security)
        RETURN count(x) AS n,
               [p IN collect(x.name)[..4] WHERE p IS NOT NULL] AS reps""",
        n=name,
    ).single()
    return (r["n"], r["reps"]) if r else (0, [])


def explore(s, name):
    """M4 概念探索：概念 → REQUIRES 依赖 + 子概念挂接规模；断链标注。"""
    c = s.run("MATCH (c:Concept {name:$n}) RETURN c.name AS n", n=name).single()
    if not c:
        print("未找到概念 %s（词表见 data/concept_dict.csv）" % name)
        return
    n, reps = _concept_size(s, name)
    print("=== 概念探索: %s | 直接挂接 %d 标的 ===" % (name, n))
    print(
        "  (M4-W3 两源分层：根概念=行业桥宽基 via=alias；"
        "细概念=主营文本精准 via=biztext，宁缺毋滥)"
    )
    if reps:
        print("  代表: %s%s" % (" ".join(reps), " …" if n > 4 else ""))

    subs = [
        (r["n"],)
        for r in s.run(
            "MATCH (:Concept {name:$n})<-[:BELONGS_TO]-(sub:Concept) "
            "RETURN sub.name AS n ORDER BY sub.name",
            n=name,
        )
    ]
    if subs:
        print("  子概念 %d 个:" % len(subs))
        for (sub,) in subs:
            sn, sreps = _concept_size(s, sub)
            tag = "" if sn else "  ← 断链(0 标的, 数据缺口)"
            rr = " ".join(sreps[:3])
            print("    %-8s %4d 标的 %s%s" % (sub, sn, rr, tag))

    for r in s.run(
        """
        MATCH (c:Concept {name:$n})-[q:REQUIRES]->(d:Concept)
        OPTIONAL MATCH (d)<-[:BELONGS_TO]-(sub:Concept)
        WITH d, q, collect(sub.name) AS subs
        RETURN d.name AS dn, subs ORDER BY dn""",
        n=name,
    ):
        dn = r["dn"]
        dn_n, dn_reps = _concept_size(s, dn)
        print("  ├─REQUIRES→ %s [%d 标的直接挂接]" % (dn, dn_n))
        for sub in r["subs"]:
            sn, sreps = _concept_size(s, sub)
            tag = "" if sn else "  ← 断链(0 标的, 数据缺口)"
            print("  │    %-8s %4d 标的 %s%s" % (sub, sn, " ".join(sreps[:3]), tag))


def main():
    if not CODE:
        print(__doc__)
        return
    cli = KgClient()
    s = cli._session()
    if "--explore" in sys.argv:  # 概念探索模式（M4）
        explore(s, CODE)
        cli.close()
        return
    if not head(s, CODE):
        return
    d0 = _d0()
    if FWD:
        fwd(s, CODE, d0)
    if ACH:
        ach(s, CODE, d0)
    cli.close()


if __name__ == "__main__":
    main()
