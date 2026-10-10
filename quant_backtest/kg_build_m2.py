# -*- coding: utf-8 -*-
"""M2-W1：外部事件 × 盘面事件 → DRIVE 边（规则版共现，只算共现不判因果）。

口径（实测后定稿，2026-09-29）：
· 匹配：同标的上，Event 日 <= MarketEvent 日，lag ∈ [0,10] 天
  （Security -HAS_STRUCTURE/HAS_VOL_EVENT-> MarketEvent；Security -INVOLVES-> Event）
  注意 INVOLVES 方向是 Security→Event（勿写反）；Security 主键是 ts_code。
· 分组 group = e.tag（公告主题）否则 e.kind（news/notice）
· p = P(该组事件伴随在前10日窗 | 同 mtype 信号)，
  分母 = 信号日 >= 事件流起点 D0（Event min date）的该 mtype 信号总数，
  分子 = 其中前10日窗内出现该组事件的信号数（每信号每组只计一次）。
  Event 流 2026-08-29 起才沉淀（此前信号无事件数据，不计入分母，避免系统性低估）；
  每日链重跑 SET 覆盖更新，覆盖期变长 p 自动修正。
· 边属性：lag_days / group / mtype / p / n_pairs；validStart=事件日。
· MERGE 键 (Event.id, MarketEvent.evt_id, validStart)——同对同起日本天然唯一。

用法：
  python kg_build_m2.py            # 实跑入图
  python kg_build_m2.py --dryrun   # 只打印配对/组合表，不写库
"""
import sys
import os
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kg_schema as S
from kg_client import KgClient

DRYRUN = "--dryrun" in sys.argv
LAG_WIN = 10  # 天

CYP_PAIRS = """
MATCH (sec:Security)-[:HAS_STRUCTURE|HAS_VOL_EVENT]->(m:MarketEvent)
MATCH (sec)-[:INVOLVES]->(e:Event)
WHERE m.date >= e.date
  AND duration.inDays(date(e.date), date(m.date)).days <= %d
RETURN e.id AS eid, e.date AS edate, coalesce(e.tag, e.kind) AS grp,
       m.evt_id AS mevt, m.type AS mtype, m.date AS mdate
""" % LAG_WIN

CYP_DENOM = """
MATCH (:Security)-[:HAS_STRUCTURE|HAS_VOL_EVENT]->(m:MarketEvent)
WHERE m.date >= $d0
RETURN m.type AS mtype, count(DISTINCT m.evt_id) AS total
"""


def build_edges(s):
    d0 = s.run("MATCH (e:Event) RETURN min(e.date) AS d").single()["d"]
    print("事件流起点 D0 = %s" % d0)

    pairs = list(s.run(CYP_PAIRS))
    print("配对总数 %d（缠论+量能，lag<=%d 天）" % (len(pairs), LAG_WIN))

    denom = {r["mtype"]: r["total"] for r in s.run(CYP_DENOM, d0=d0)}
    print("分母（信号日>=D0 的信号数）: %s" % denom)

    # 分子：每 (信号, 组) 只计一次 → (group, mtype) -> 信号集合
    sig_groups = {}
    for p in pairs:
        sig_groups.setdefault((p["grp"], p["mtype"]), set()).add(p["mevt"])

    combo = {}
    for (grp, mtype), sigs in sig_groups.items():
        total = denom.get(mtype, 0)
        combo[(grp, mtype)] = {
            "num": len(sigs),
            "total": total,
            "p": round(len(sigs) / total, 4) if total else 0.0,
        }

    print("\n组合伴随率表（group × mtype, 按实例数排序 Top 25）:")
    pair_cnt = {}
    for p in pairs:
        pair_cnt[(p["grp"], p["mtype"])] = pair_cnt.get((p["grp"], p["mtype"]), 0) + 1
    for (grp, mtype), c in sorted(pair_cnt.items(), key=lambda kv: -kv[1])[:25]:
        v = combo[(grp, mtype)]
        print("  %-12s %-4s 实例%5d 信号%5d/%-5d p=%.3f"
              % (grp, mtype, c, v["num"], v["total"], v["p"]))

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    edges = []
    for p in pairs:
        v = combo[(p["grp"], p["mtype"])]
        edges.append({
            "src": p["eid"], "dst": p["mevt"],
            "validStart": p["edate"], "validEnd": S.VALID_END_OPEN,
            "lag_days": (datetime.strptime(p["mdate"], "%Y%m%d")
                         - datetime.strptime(p["edate"], "%Y%m%d")).days,
            "group": p["grp"], "mtype": p["mtype"],
            "p": v["p"], "n_pairs": pair_cnt[(p["grp"], p["mtype"])],
            "source": "cooccur_lag%d" % LAG_WIN, "updated_at": now,
        })
    print("\nDRIVE 边 %d 条" % len(edges))
    return edges


def enrich_structure_edges(s, dryrun):
    """M2-W2/W4c：HAS_STRUCTURE 边回填情报元属性（每日重算 SET 覆盖）。

    · corroboration_count = 前10日窗内实质主题组 DRIVE 边数（非 notice 基线）
    · corroboration_baseline = 同窗基线组边数（透明对照，展示层可忽略）
    · confidence = 0.40*q_struct + 0.35*q_evid + 0.25*q_hm（规则版，因子值随边存档）
        q_struct = 0.4*bei + 0.3*bei2 + 0.3*vv       结构质量（背驰/量确认）
        q_evid   = min(1, cc/3)                       实质主题佐证（3 条满）
        q_hm     = 1.0 if pre净买>1亿 else 0.0        阶跃式（M2-W4c 校准修订）
    · post_hm10 / hm_tag（M2-W4c 后验确认标注，窗口满才回填，每日链自动补满）：
        follow_confirm = post10∈(0,1亿] 且 pre≤0   纯跟随确认（ret5 +11.6%, n=116）
        lure_warn      = post10>0 且 pre>0          游资先行又回访=诱多（ret5 -4.2%）
        none           = 其余（含 post>1亿 flood 档 n=4 证据不足并入）
    · 校准依据（2026-09-28，n=9414，见 data/semantica/qhm_calibration.csv）：
      pre 小额(0,1亿]连续赋分证伪（ret5 -4.3%~-4.2% vs 基线 -2.1%）；
      唯一正超额先行档 >1亿（+19.1%, n=6 弱证据）；净卖≈零不惩罚；
      确认超额全部来自 pre- 子组（G 交互），样本集中 2026-04~07（外推局限）
    · is_deception 不在此落：属影子评估流程（可成交价滞后重放），见计划 W2 说明
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    today = datetime.now().strftime("%Y%m%d")
    base = ["notice", "news", "jev"]

    # 单一实时计算查询（佐证 + 游资双向窗口一起聚合，dryrun 天然只读）
    rows = list(s.run("""
        MATCH (sec:Security)-[r:HAS_STRUCTURE]->(m:MarketEvent {kind:'chanlun'})
        OPTIONAL MATCH (:Event)-[d:DRIVE]->(m)
        WITH sec, r, m,
             sum(CASE WHEN d IS NOT NULL
                       AND NOT coalesce(d.group,'') IN $base THEN 1 ELSE 0 END) AS cc,
             sum(CASE WHEN d IS NOT NULL THEN 1 ELSE 0 END) AS cb
        OPTIONAL MATCH (:HotMoney)-[:OCCUPIES]->(:Seat)-[t:TRADED_ON]->(sec)
        WHERE t.validStart <= m.date
          AND duration.inDays(date(t.validStart), date(m.date)).days <= 10
        WITH sec, r, m, cc, cb, sum(coalesce(t.net_buy, 0)) AS hm_net
        OPTIONAL MATCH (:HotMoney)-[:OCCUPIES]->(:Seat)-[q:TRADED_ON]->(sec)
        WHERE q.validStart > m.date
          AND duration.inDays(date(m.date), date(q.validStart)).days <= 10
        WITH sec, r, m, cc, cb, hm_net,
             CASE WHEN date(m.date) + duration('P10D') <= date($today)
                  THEN sum(coalesce(q.net_buy, 0)) ELSE null END AS hm_post
        RETURN m.evt_id AS evt, m.bei AS bei, m.bei2 AS bei2, m.vv AS vv,
               cc, cb, hm_net, hm_post""", base=base, today=today))
    out = []
    tags = {"follow_confirm": 0, "lure_warn": 0, "none": 0, "pending": 0}
    for x in rows:
        q_s = 0.4 * bool(x["bei"]) + 0.3 * bool(x["bei2"]) + 0.3 * bool(x["vv"])
        q_e = min(1.0, (x["cc"] or 0) / 3.0)
        # M2-W4c：阶跃式 q_hm——只有 >1 亿先行档有实证正超额
        pre = x["hm_net"] or 0
        q_h = 1.0 if pre > 1e8 else 0.0
        hp = x["hm_post"]
        if hp is None:
            tag = "pending"          # 信号后 10 日窗口未满（每日链自动补）
        elif 0 < hp <= 1e8 and pre <= 0:
            tag = "follow_confirm"
        elif hp > 0 and pre > 0:
            tag = "lure_warn"
        else:
            tag = "none"
        tags[tag] += 1
        out.append({"evt": x["evt"], "cc": x["cc"] or 0, "cb": x["cb"] or 0,
                    "conf": round(0.40 * q_s + 0.35 * q_e + 0.25 * q_h, 4),
                    "qs": round(q_s, 4), "qe": round(q_e, 4), "qh": round(q_h, 4),
                    "hp": hp, "tag": tag})
    buckets = {}
    for o in out:
        buckets[int(o["conf"] * 10) / 10.0] = buckets.get(int(o["conf"] * 10) / 10.0, 0) + 1
    print("覆盖 %d 条 HAS_STRUCTURE；confidence 分布: %s"
          % (len(out), " ".join("%.1f:%d" % (k, buckets[k]) for k in sorted(buckets))))
    print("hm_tag: follow_confirm=%d(纯跟随确认) lure_warn=%d(诱多) "
          "none=%d pending=%d(窗口未满)" % (tags["follow_confirm"],
          tags["lure_warn"], tags["none"], tags["pending"]))
    if dryrun:
        print("[dryrun] 元属性未写库")
        return
    s.run("""
        UNWIND $rows AS row
        MATCH (sec:Security)-[r:HAS_STRUCTURE]->(m:MarketEvent {evt_id: row.evt})
        SET r.corroboration_count = row.cc, r.corroboration_baseline = row.cb,
            r.confidence = row.conf, r.q_struct = row.qs,
            r.q_evid = row.qe, r.q_hm = row.qh,
            r.post_hm10 = coalesce(row.hp, r.post_hm10),
            r.hm_tag = CASE WHEN row.tag = 'pending' THEN r.hm_tag ELSE row.tag END,
            r.updated_at = $now""",
          rows=out, now=now)
    print("元属性回填: %d 条（佐证 + confidence(阶跃q_hm) + post_hm10/hm_tag）"
          % len(out))


def main():
    cli = KgClient()
    cli.ensure_schema()
    s = cli._session()
    edges = build_edges(s)
    if DRYRUN:
        print("[dryrun] 未写库")
    else:
        n = cli.upsert_edge("Event", "id", "MarketEvent", "evt_id",
                            S.REL_DRIVE, edges)
        print("DRIVE upsert 完成: %d 条" % n)
        # 抽检：一条三买信号的 DRIVE 边
        for r in s.run("""
            MATCH (e:Event)-[d:DRIVE]->(m:MarketEvent {kind:'chanlun'})
            RETURN e.id AS eid, d.group AS g, d.lag_days AS lag, d.p AS p,
                   m.evt_id AS mevt, m.type AS mt LIMIT 5"""):
            print("  抽检 %s -[DRIVE g=%s lag=%dd p=%.3f]-> %s %s"
                  % (r["eid"], r["g"], r["lag"], r["p"], r["mevt"], r["mt"]))
    # M2-W2：元属性回填（dryrun 时也打印分布预览，佐证计数除外）
    enrich_structure_edges(s, DRYRUN)
    if not DRYRUN:
        for r in s.run("""
            MATCH (sec:Security)-[r:HAS_STRUCTURE]->(m:MarketEvent {kind:'chanlun'})
            WHERE r.confidence >= 0.8
            RETURN sec.ts_code AS c, m.type AS t, m.date AS d,
                   r.confidence AS conf, r.corroboration_count AS cc,
                   r.q_hm AS qh ORDER BY r.confidence DESC LIMIT 5"""):
            print("  高分样本 %s %s %s conf=%.2f cc=%d q_hm=%.2f"
                  % (r["c"], r["t"], r["d"], r["conf"], r["cc"], r["qh"]))
    cli.close()


if __name__ == "__main__":
    t0 = time.time()
    main()
    print("耗时 %.1fs" % (time.time() - t0))
