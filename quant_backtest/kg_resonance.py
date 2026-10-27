# -*- coding: utf-8 -*-
"""K206 · 蓝图 E 阶段：三线共振查询（结构 × 传导 × 共识，情报本体收官）。

三线定义（给定主题 theme + 缠论信号窗口 days 自然日，按 detect_date 计）：
  线1 结构线  Security-[HAS_STRUCTURE]->(MarketEvent {kind:'chanlun'})
              detect_date >= 窗口起点——近期有缠论结构信号；
  线2 传导线  Security-[SUPPLIES_TO]->Segment ∈ 主题传导链
              （Theme-[CONDUCTION]->一级环节；一级<-[:SUB_OF]-二级）；
  线3 共识线  Commentator-[ASSESSED {theme}]->Security——有派系评估。

共振分层（单线不输出）：
  三线共振 = 情报级标的（结构买点 + 产业位置 + 派系背书齐备）；
  两线共振 = 候选观察（缺哪线即标注，如「缺结构信号」）。

影子纪律：结果落 data/kg_resonance_last.json（原子写、只记不推），
推送/看板接入另行立项，不在本模块触发。

用法：
  python kg_resonance.py query 人形机器人 [--days 30]
  python kg_resonance.py query AI 算力
"""

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from kg_client import KgClient  # noqa: E402
import kg_schema as S  # noqa: E402

OUT = os.path.join(HERE, "data", "kg_resonance_last.json")


def _d0(days):
    """窗口起点（自然日，YYYYMMDD 字符串比较口径）。"""
    return (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")


def _chain_stocks(s, theme):
    """线2：主题传导链上的标的 → {code: {name, segs:[(seg, evidence)]}}。"""
    rows = s.run(
        "MATCH (t:%s {name:$t})-[:%s]->(g1:%s) "
        "OPTIONAL MATCH (g1)<-[:%s]-(g2:%s) "
        "WITH collect(DISTINCT g1) + collect(DISTINCT g2) AS segs "
        "UNWIND segs AS seg "
        "MATCH (sec:Security)-[sp:%s]->(seg) "
        "RETURN sec.ts_code AS code, sec.name AS name, seg.name AS seg, "
        "sp.evidence AS ev"
        % (
            S.LBL_THEME,
            S.REL_CONDUCTION,
            S.LBL_SEGMENT,
            S.REL_SUB_OF,
            S.LBL_SEGMENT,
            S.REL_SUPPLIES_TO,
        ),
        t=theme,
    ).data()
    out = {}
    for r in rows:
        d = out.setdefault(r["code"], {"name": r["name"], "segs": []})
        d["segs"].append((r["seg"], r["ev"]))
    return out


def _consensus(s, theme):
    """线3：主题共识评估 → {code: {name, verdicts:{派系:✓}, stars}}。"""
    rows = s.run(
        "MATCH (c:%s)-[a:%s {theme:$t}]->(sec:Security) "
        "RETURN sec.ts_code AS code, sec.name AS name, c.name AS school, "
        "a.verdict AS v, a.stars AS st" % (S.LBL_COMMENTATOR, S.REL_ASSESSED),
        t=theme,
    ).data()
    out = {}
    for r in rows:
        d = out.setdefault(r["code"], {"name": r["name"], "verdicts": {}, "stars": 0})
        d["verdicts"][r["school"]] = r["v"]
        d["stars"] = r["st"]
    return out


def _signals(s, d0):
    """线1：窗口内缠论信号 → {code: {name, sigs:[(type,date,detect,px)]}}。"""
    rows = s.run(
        "MATCH (sec:Security)-[:%s]->(e:%s {kind:'chanlun'}) "
        "WHERE e.detect_date >= $d0 "
        "RETURN sec.ts_code AS code, sec.name AS name, e.type AS tp, "
        "e.date AS d, e.detect_date AS dd, e.price AS px "
        "ORDER BY dd DESC" % (S.REL_HAS_STRUCTURE, S.LBL_MARKET_EVENT),
        d0=d0,
    ).data()
    out = {}
    for r in rows:
        d = out.setdefault(r["code"], {"name": r["name"], "sigs": []})
        d["sigs"].append((r["tp"], r["d"], r["dd"], r["px"]))
    return out


def query(theme, days=30):
    """三线共振查询 + 影子落盘。"""
    cli = KgClient(retry=1)
    d0 = _d0(days)
    with cli._session() as s:
        chain = _chain_stocks(s, theme)
        cons = _consensus(s, theme)
        sigs = _signals(s, d0)
    cli.close()

    codes_all = set(chain) | set(cons) | set(sigs)
    full, partial, single = [], [], []
    for c in codes_all:
        lines = [
            ln for ln, m in (("结构", sigs), ("传导", chain), ("共识", cons)) if c in m
        ]
        item = {
            "code": c,
            "name": (chain.get(c) or cons.get(c) or sigs.get(c))["name"],
            "lines": lines,
            "segs": chain.get(c, {}).get("segs"),
            "verdicts": cons.get(c, {}).get("verdicts"),
            "stars": cons.get(c, {}).get("stars", 0),
            "sigs": sigs.get(c, {}).get("sigs"),
        }
        {"3": full, "2": partial, "1": single}[str(len(lines))].append(item)
    full.sort(key=lambda x: -x["stars"])
    partial.sort(key=lambda x: -x["stars"])

    # —— CLI 输出 ——
    print("== %s 三线共振（缠论窗口 %d 自然日: %s~今日） ==" % (theme, days, d0))
    print("\n★ 三线共振（结构×传导×共识）: %d" % len(full))
    for it in full:
        seg_txt = "；".join(
            "@ %s%s" % (sg, ("（%s）" % ev) if ev else "") for sg, ev in it["segs"]
        )
        print("  %s %s（%s）" % (it["name"], it["code"], seg_txt))
        for tp, d, dd, px in it["sigs"][:3]:
            print("    信号: %s %s（detect %s）@ %s" % (tp, d, dd, px))
        vtxt = " ".join("%s%s" % (k, v) for k, v in it["verdicts"].items())
        print("    共识: %s %s" % (vtxt, "★" * it["stars"]))
    print("\n☆ 两线共振（缺线已标注）: %d" % len(partial))
    for it in partial:
        has = "+".join(it["lines"])
        miss = next(ln for ln in ("结构", "传导", "共识") if ln not in it["lines"])
        seg_txt = (" @%s" % it["segs"][0][0]) if it["segs"] else ""
        sig_txt = (
            (" 最近: %s %s（detect %s）" % it["sigs"][0][:3]) if it["sigs"] else ""
        )
        print(
            "  %s %s（%s，缺%s）%s%s"
            % (it["name"], it["code"], has, miss, seg_txt, sig_txt)
        )
    print(
        "\n共 %d 三线 / %d 两线（单线 %d 不输出）"
        % (len(full), len(partial), len(single))
    )

    # —— 影子落盘（原子写） ——
    res = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "theme": theme,
        "window_days": days,
        "d0": d0,
        "n_full": len(full),
        "n_partial": len(partial),
        "n_single": len(single),
        "full": full,
        "partial": partial,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(OUT), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    os.replace(tmp, OUT)
    print("影子落盘: %s" % OUT)
    return res


def cross(days=90):
    """E5 跨链交叉发现：同一标的挂 ≥2 链（多主题共振身份）＋近期结构
    信号加成。引擎级查询——不绑定任何主题，链扩容后自动产出。"""
    cli = KgClient(retry=1)
    d0 = _d0(days)
    with cli._session() as s:
        rows = s.run(
            "MATCH (sec:Security)-[sp:%s]->(g:Segment) "
            "WITH sec, collect(DISTINCT g.chain) AS chains, "
            "collect(DISTINCT g.name) AS segs, "
            "collect(DISTINCT sp.evidence) AS evs "
            "WHERE size(chains) >= 2 "
            "RETURN sec.ts_code AS code, sec.name AS name, chains, segs, "
            "evs ORDER BY size(chains) DESC, name" % S.REL_SUPPLIES_TO
        ).data()
        sigs = {
            r["code"]: r
            for r in s.run(
                "MATCH (sec:Security)-[:%s]->(e:%s {kind:'chanlun'}) "
                "WHERE e.detect_date >= $d0 "
                "RETURN sec.ts_code AS code, e.type AS tp, e.detect_date AS dd "
                "ORDER BY dd DESC" % (S.REL_HAS_STRUCTURE, S.LBL_MARKET_EVENT),
                d0=d0,
            ).data()
        }
    cli.close()
    print("== 跨链交叉标的（挂 ≥2 链 · 结构信号窗口 %d 自然日） ==" % days)
    if not rows:
        print("（暂无跨链标的——链扩容后本查询自动产出）")
        return []
    out = []
    for r in rows:
        sg = sigs.get(r["code"])
        sig_txt = (
            ("%s %s（detect %s）" % (sg["tp"], "", sg["dd"])) if sg else "无近期信号"
        )
        print(
            "  %s %s → %s\n    环节: %s\n    信号: %s"
            % (
                r["name"],
                r["code"],
                " + ".join(r["chains"]),
                "、".join(r["segs"]),
                sig_txt,
            )
        )
        out.append(
            {
                "code": r["code"],
                "name": r["name"],
                "chains": r["chains"],
                "segs": r["segs"],
                "has_signal": bool(sg),
            }
        )
    print("\n共 %d 跨链标的（高价值：同一公司吃多条主题的产业共振）" % len(out))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["query", "cross"])
    ap.add_argument("theme", nargs="?", default=None)
    ap.add_argument("--days", type=int, default=30)
    a = ap.parse_args()
    if a.cmd == "query":
        if not a.theme:
            raise SystemExit("query 需要主题名（含空格加引号）")
        query(a.theme, a.days)
    else:
        cross(days=max(a.days, 90) if a.days != 30 else 90)


if __name__ == "__main__":
    main()
