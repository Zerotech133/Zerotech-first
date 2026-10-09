# -*- coding: utf-8 -*-
"""M3-W1：知识图谱门户数据层（JSON，纯查询不写库）。

portal_api.py 的 /kg/* 端点调本模块 api(kind, arg, days)；
也可 CLI 自测：
  python kg_portal.py stats
  python kg_portal.py stock 002743.SZ
  python kg_portal.py stock 002743.SZ --days 60
  python kg_portal.py hotmoney 章建平
  python kg_portal.py trace 002743.SZ --days 60

口径与 kg_trace.py 完全同源（ACH 三假设/信号标注/基线组），
区别只在输出形态：dict 供门户消费，非 print。
连接：模块级懒加载单例 KgClient(retry=1)，neo4j driver 线程安全，
会话每请求独立（门户为 ThreadingHTTPServer）。
"""
import json
import re
import sys
import os
import threading
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kg_schema as S
import pandas as pd                    # B 类特征文件直读（cyq/holder_num）

BASELINE_GRP = {"notice", "news", "jev"}  # 高密度基线组，不算实质主题证据

MINS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "data", "tushare", "stk_mins")

_lock = threading.Lock()
_cli = None

# ── 盘口域覆盖清单（G2 盲区透明化：分钟库文件即覆盖边界）──
_cov_cache = {"ts": 0.0, "set": frozenset()}


def candle_coverage():
    """返回分钟库覆盖标的集合（文件名即清单，600s TTL 缓存）。"""
    now = datetime.now().timestamp()
    with _lock:
        if now - _cov_cache["ts"] < 600:
            return _cov_cache["set"]
    codes = set()
    try:
        for fn in os.listdir(MINS_DIR):
            if fn.endswith(".parquet"):
                # 文件名 tc.replace('.','_')+'_YYYYMM.parquet'
                stem = fn[:-8]  # 去 .parquet
                stem = stem.rsplit("_", 1)[0].replace("_", ".")
                if "." in stem:
                    codes.add(stem)
    except OSError:
        pass
    with _lock:
        _cov_cache["ts"] = now
        _cov_cache["set"] = frozenset(codes)
    return _cov_cache["set"]


def _client():
    global _cli
    with _lock:
        if _cli is None:
            from kg_client import KgClient
            # 门户场景：快失败（Neo4j 未启动时立即返回错误 JSON，不等 75s）
            _cli = KgClient(retry=1, retry_wait=1)
        return _cli


def _q(cy, **kw):
    with _client()._session() as s:
        return [dict(r) for r in s.run(cy, **kw)]


def _d0(days):
    return (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")


# ── /kg/stats：图谱计数 + 保鲜时点（L1 注册表驱动）────────
def stats():
    """全量标签/边型计数 + 保鲜 + 注册表漂移可见。

    L1（2026-10-09）：计数清单从硬编码 13 标签/16 边型子集改为
    ontology.py 注册表口径全量（2 条全图扫描替代逐类型计数；
    labels(n)[0] 取主标签，与 source_col_audit.schema_snapshot 同口径）。
    形状兼容 portal 健康检查（nodes/edges/freshness/hm_tags 四键保留），
    新增 registry 键：图内未注册类型在线可见（K264 旁路写入防线）。
    """
    import ontology as OT
    nodes = {r["lbl"]: r["c"] for r in _q(
        "MATCH (n) RETURN labels(n)[0] AS lbl, count(*) AS c"
    ) if r.get("lbl")}
    edges = {r["t"]: r["c"] for r in _q(
        "MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS c")}
    fresh = _q(
        "MATCH (m:MarketEvent) WITH max(m.date) AS me "
        "OPTIONAL MATCH (e:Event) WITH me, max(e.date) AS ee "
        "OPTIONAL MATCH ()-[t:TRADED_ON]->() WITH me, ee, "
        "max(t.validStart) AS te "
        "OPTIONAL MATCH ()-[cd:HAS_CANDLE]->() "
        "RETURN me, ee, te, max(cd.validStart) AS cd")[0]
    tags = {r["t"]: r["n"] for r in _q(
        "MATCH ()-[r:HAS_STRUCTURE]->() WHERE r.hm_tag IS NOT NULL "
        "RETURN r.hm_tag AS t, count(*) AS n")}
    return {"ok": True, "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "nodes": nodes, "edges": edges, "freshness": fresh,
            "hm_tags": tags,
            "registry": {"version": OT.VERSION,
                         "registered": {"nodes": len(OT.NODE_TYPES),
                                        "edges": len(OT.EDGE_TYPES)},
                         "unregistered": {
                             "labels": sorted(
                                 set(nodes) - set(OT.NODE_TYPES)),
                             "rels": sorted(
                                 set(edges) - set(OT.EDGE_TYPES))}}}


# ── /kg/stock/{code}：个股情报全景 ────────────────────────
def stock(code, days=120):
    basic = _q(
        "MATCH (n:Security {ts_code:$c}) "
        "OPTIONAL MATCH (n)-[:OF_COMPANY]->(co:Company) "
        "OPTIONAL MATCH (n)-[:SW_L1_OF]->(sw) "
        "RETURN n.name AS name, n.industry AS industry, n.area AS area, "
        "n.list_date AS list_date, co.com_name AS company, sw.name AS sw_l1",
        c=code)
    if not basic:
        return {"ok": False, "error": "未找到标的 %s" % code}
    d0 = _d0(days)
    structures = _q(
        "MATCH (sec:Security {ts_code:$c})-[r:HAS_STRUCTURE]->(m:MarketEvent) "
        "WHERE m.date >= $d0 "
        "RETURN m.date AS date, m.type AS type, r.confidence AS conf, "
        "r.q_struct AS q_struct, r.q_evid AS q_evid, r.q_hm AS q_hm, "
        "r.hm_tag AS hm_tag, r.corroboration_count AS cc "
        "ORDER BY date DESC LIMIT 60", c=code, d0=d0)
    vol = _q(
        "MATCH (sec:Security {ts_code:$c})-[:HAS_VOL_EVENT]->(m:MarketEvent) "
        "WHERE m.date >= $d0 "
        "RETURN m.type AS type, count(*) AS n, max(m.date) AS last "
        "ORDER BY n DESC", c=code, d0=d0)
    hm = _q(
        "MATCH (h:HotMoney)-[:OCCUPIES]->(:Seat)-[t:TRADED_ON]->"
        "(sec:Security {ts_code:$c}) WHERE t.validStart >= $d0 "
        "RETURN h.name AS hm, count(*) AS n, sum(t.net_buy) AS net, "
        "max(t.validStart) AS last "
        "ORDER BY net DESC LIMIT 15", c=code, d0=d0)
    drive = _q(
        "MATCH (e:Event)-[d:DRIVE]->(m:MarketEvent)"
        "<-[:HAS_STRUCTURE|HAS_VOL_EVENT]-(sec:Security {ts_code:$c}) "
        "WHERE d.validStart >= $d0 "
        "RETURN d.group AS grp, count(*) AS n, max(d.validStart) AS last, "
        "collect(DISTINCT left(coalesce(e.text,''),40))[..3] AS texts "
        "ORDER BY n DESC LIMIT 12", c=code, d0=d0)
    candles = _q(
        "MATCH (sec:Security {ts_code:$c})-[:HAS_CANDLE]->(m:MarketEvent) "
        "OPTIONAL MATCH (m)-[:USES_TECH]->(t:Technique) "
        "WHERE m.date >= $d0 "
        "RETURN m.date AS date, m.type AS type, m.mtime AS mtime, "
        "m.pct AS pct, m.n_touch AS n_touch, m.tail_vol AS tail_vol, "
        "t.name AS tech, t.reg_tag AS reg "
        "ORDER BY date DESC LIMIT 30", c=code, d0=d0)
    # B 类：涨跌停名单（连板/封单/炸板）+ 十大流通股东（近 3 期）
    limits = _q(
        "MATCH (sec:Security {ts_code:$c})-[:HAS_LIMIT]->(m:MarketEvent) "
        "WHERE m.date >= $d0 "
        "RETURN m.date AS date, m.limit_times AS lt, m.pct_chg AS pct, "
        "m.limit_amount AS lamt, m.open_times AS ot, m.up_stat AS stat, "
        "m.limit_type AS ltp "
        "ORDER BY date DESC LIMIT 30", c=code, d0=d0)
    # P2-① 榜后差额（topgap：前五买卖差×席位性质×次日穿透，近 30 条；
    # gap/gap_ratio 单位元/占比，pen=1 差额方向打进次日价格）
    topgaps = _q(
        "MATCH (sec:Security {ts_code:$c})-[:HAS_TOPGAP]->(m:MarketEvent) "
        "WHERE m.date >= $d0 "
        "RETURN m.date AS date, m.gap AS gap, m.gap_ratio AS gr, "
        "m.dom_nature AS nat, m.quant_ratio AS qr, m.ret1 AS ret1, "
        "m.oret1 AS oret1, m.pen AS pen, m.reason AS reason "
        "ORDER BY date DESC LIMIT 30", c=code, d0=d0)
    # P2-② K线相似（最新窗口 top-5：无向匹配取 dist 升序；
    # simseats=同席位窗口内双碰本票+相似票 → 手法复现线索）
    similar = _q(
        "MATCH (a:Security {ts_code:$c})-[r:SIMILAR_K]-(b:Security) "
        "RETURN b.ts_code AS tc, b.name AS nm, r.dist AS dist, "
        "r.corr AS corr, r.win_start AS ws, r.win_end AS we "
        "ORDER BY r.validStart DESC, r.dist ASC LIMIT 5", c=code)
    simseats = _q(
        "MATCH (a:Security {ts_code:$c})-[r:SIMILAR_K]-(b:Security) "
        "WITH a, b, r ORDER BY r.validStart DESC, r.dist ASC LIMIT 5 "
        "MATCH (st:Seat)-[t1:TRADED_ON]->(a), (st)-[t2:TRADED_ON]->(b) "
        "WHERE t1.validStart >= $d0 AND t2.validStart >= $d0 "
        "RETURN st.name AS seat, b.ts_code AS tc, b.name AS nm, "
        "t1.validStart AS d1, t2.validStart AS d2 "
        "ORDER BY d1 DESC LIMIT 20", c=code, d0=d0)
    # tushare 空值经 parquet 落为 NaN（Neo4j float 可存 NaN）——
    # JSON 序列化 NaN 会让前端/渲染端炸（"%d" % NaN ValueError），归一为 None
    import math as _m
    for r in limits:
        for k in ("lt", "pct", "lamt", "ot"):
            if isinstance(r.get(k), float) and _m.isnan(r[k]):
                r[k] = None
    holders = _q(
        "MATCH (o:Organization)-[h:HOLDS]->(sec:Security {ts_code:$c}) "
        "RETURN h.validStart AS period, o.name AS name, "
        "h.hold_ratio AS ratio, h.hold_amount AS amt "
        "ORDER BY period DESC, ratio DESC LIMIT 30", c=code)
    for r in holders:
        for k in ("ratio", "amt"):
            if isinstance(r.get(k), float) and _m.isnan(r[k]):
                r[k] = None
    # B 类：筹码分布摘要（特征文件直读，不入图）
    cyq = {}
    try:
        _cf = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "data", "kg_cyq.parquet")
        _cd = pd.read_parquet(_cf)
        _cd = _cd[_cd["ts_code"] == code]
        if len(_cd):
            r0 = _cd.iloc[-1]
            cyq = {"date": r0["trade_date"],
                   "winner_rate": float(r0["winner_rate"]),
                   "wr_chg20": (None if pd.isna(r0["wr_chg20"])
                                else float(r0["wr_chg20"])),
                   "cost_span": float(r0["cost_span"]),
                   "weight_avg": float(r0["weight_avg"])}
    except Exception:
        pass
    cov = candle_coverage()
    return {"ok": True, "code": code, "window_days": days,
            "basic": basic[0], "structures": structures,
            "vol_events": vol, "hotmoney": hm, "candles": candles,
            "limits": limits, "holders": holders, "cyq": cyq,
            "topgaps": topgaps,
            "similar": similar, "simseats": simseats,
            "candle_cov": {"covered": code in cov, "n_cov": len(cov)},
            "drive_groups": [{"grp": r["grp"],
                              "baseline": r["grp"] in BASELINE_GRP,
                              "n": r["n"], "texts": r["texts"],
                              "last": r.get("last")}
                             for r in drive]}


# ── /kg/company360/{code}：公司本体全景（KG-O 中心辐射六扇区）──
# 理念移植自 知识图谱/_study/studio（千江图谱工作室）：本体 schema 驱动
# （实体类型→扇区/着色）+ 关系统计（主端/对端/强度/次数，可筛选上图），
# 栈沿用现有 G6 v5 + ECharts，不引入 React 构建链。
C360_ROLE_RANK = (
    ("董事长", 100), ("CEO", 88), ("总裁", 85), ("总经理", 75),
    ("CFO", 70), ("财务", 68), ("副总经理", 60), ("副总裁", 60),
    ("独立董事", 50), ("董事", 48), ("监事会", 44), ("监事", 40),
    ("委员会", 32), ("董秘", 66), ("秘书", 50),
)


def _role_rank(role):
    r = role or ""
    for kw, sc in C360_ROLE_RANK:
        if kw in r:
            return sc
    return 25


def company_360(code, days=90, per=None):
    """公司本体全景：以 Company 为中心收集六扇区本体（股东/高管/
    业务产品/行业归属含同业/游资/盘面事件），输出 G6 中心辐射
    nodes+edges、四类直方图数据（实体类型分布/按日事件计数/游资
    净买/结构 conf 分布）、关系统计表。纯查询不写库。"""
    head = _q(
        "MATCH (s:Security {ts_code:$c}) "
        "OPTIONAL MATCH (s)-[:OF_COMPANY]->(co:Company) "
        "OPTIONAL MATCH (s)-[:SW_L1_OF]->(sw) "
        "RETURN s.name AS name, s.industry AS industry, s.area AS area, "
        "s.list_date AS list_date, co.com_id AS com_id, "
        "co.com_name AS company, co.chairman AS chairman, "
        "co.manager AS manager, co.secretary AS secretary, "
        "co.reg_capital AS reg_capital, co.employees AS employees, "
        "co.province AS province, co.city AS city, "
        "co.main_business AS main_business, co.introduction AS intro, "
        "co.website AS website, sw.industry_name AS sw_l1", c=code)
    if not head:
        return {"ok": False, "error": "未找到标的 %s" % code}
    b = head[0]
    d0 = _d0(days)
    import math as _m

    def _nn(v):
        return None if (isinstance(v, float) and _m.isnan(v)) else v

    def _d8(ds):
        ds = ds or ""
        return "%s-%s-%s" % (ds[:4], ds[4:6], ds[6:8]) \
            if len(ds) == 8 else ds

    nodes, edges, rel_stats = [], [], []

    # 中心 Company + 标的 Security（OF_COMPANY 真实三元组）
    co_id = "co:%s" % (b.get("com_id") or code)
    nodes.append({"id": co_id, "sector": "company",
                  "label": (b.get("company") or b.get("name") or code)[:14],
                  "w": 10, "props": {
                      "全称": b.get("company") or "—",
                      "董事长": b.get("chairman") or "—",
                      "总经理": b.get("manager") or "—",
                      "董秘": b.get("secretary") or "—",
                      "员工数": _nn(b.get("employees")),
                      "注册资本(万)": _nn(b.get("reg_capital")),
                      "省市": "%s %s" % (b.get("province") or "",
                                          b.get("city") or ""),
                      "主营": (b.get("main_business") or "")[:120]}})
    nodes.append({"id": code, "sector": "security",
                  "label": "%s %s" % (code, b.get("name") or ""),
                  "w": 7, "props": {
                      "代码": code, "名称": b.get("name") or "—",
                      "上市日": b.get("list_date") or "—",
                      "行业": b.get("industry") or "—",
                      "申万一级": b.get("sw_l1") or "—"}})
    edges.append({"s": code, "t": co_id, "rel": "OF_COMPANY", "w": 1})
    rel_stats.append({"src": "Security 标的", "dst": "Company 公司",
                      "rel": "OF_COMPANY", "strength": 1, "count": 1,
                      "note": "本体锚边"})

    # ① 股东扇区：十大流通股东（报告期可切换 per）
    hold_periods = [r["per"] for r in _q(
        "MATCH ()-[h:HOLDS]->(:Security {ts_code:$c}) "
        "RETURN DISTINCT h.validStart AS per ORDER BY per DESC", c=code)]
    per0 = per if per in hold_periods else (
        hold_periods[0] if hold_periods else None)
    if per0:
        holds = _q(
            "MATCH (o:Organization)-[h:HOLDS]->(:Security {ts_code:$c}) "
            "WHERE h.validStart = $p "
            "RETURN o.name AS nm, h.hold_ratio AS ratio, "
            "h.hold_amount AS amt "
            "ORDER BY ratio DESC LIMIT 12", c=code, p=per0)
        for r in holds:
            nodes.append({"id": "org:%s" % r["nm"], "sector": "holder",
                          "label": r["nm"], "w": _nn(r["ratio"]) or 0.1,
                          "props": {"股东": r["nm"], "持股比例%":
                                    _nn(r["ratio"]), "持股数(万)":
                                    _nn(r["amt"]), "报告期": per0}})
            edges.append({"s": "org:%s" % r["nm"], "t": code,
                          "rel": "HOLDS", "w": _nn(r["ratio"]) or 0.1})
        rel_stats.append({"src": "Organization 股东", "dst": "Security 标的",
                          "rel": "HOLDS",
                          "strength": round(sum(_nn(r["ratio"]) or 0
                                                for r in holds), 1),
                          "count": len(holds), "note": "报告期 %s" % per0})

    # ② 高管扇区：Person SERVES_AS（在职/历任分层，在职优先 top16）
    proles = _q(
        "MATCH (p:Person)-[r:SERVES_AS]->(:Company)<-[:OF_COMPANY]-"
        "(:Security {ts_code:$c}) "
        "RETURN p.name AS nm, collect(DISTINCT r.role) AS roles, "
        "min(r.validStart) AS vs, max(r.validEnd) AS ve", c=code)
    ranked = []
    for r in proles:
        rk = max(_role_rank(x) for x in r["roles"]) if r["roles"] else 25
        cur = (r["ve"] or "").replace("-", "") == "99991231"
        ranked.append((0 if cur else 1, rk, r["nm"], r["roles"],
                       r["vs"], r["ve"]))
    ranked.sort(key=lambda x: (x[0], -x[1], x[2]))
    for past, rk, nm, roles, vs, ve in ranked[:16]:
        nodes.append({"id": "p:%s" % nm, "sector": "person", "label": nm,
                      "w": rk, "past": past,
                      "props": {"人物": nm,
                                "职务": " / ".join(roles[:3]),
                                "任期": "%s ~ %s" % (
                                    _d8(vs or "")[:10],
                                    _d8(ve or "")[:10]),
                                "状态": "历任" if past else "在职"}})
        edges.append({"s": "p:%s" % nm, "t": co_id, "rel": "SERVES_AS",
                      "w": rk})
    if ranked:
        n_cur = sum(1 for x in ranked if not x[0])
        rel_stats.append({"src": "Person 高管", "dst": "Company 公司",
                          "rel": "SERVES_AS", "strength": len(ranked),
                          "count": len(ranked),
                          "note": "在职 %d / 历任 %d·图上 top16（在职优先）"
                                  % (n_cur, len(ranked) - n_cur)})

    # ③ 业务/产品扇区：Company PRODUCES Product
    prods = _q(
        "MATCH (co:Company)<-[:OF_COMPANY]-(:Security {ts_code:$c}) "
        "MATCH (co)-[:PRODUCES]->(pr:Product) "
        "RETURN pr.name AS nm LIMIT 14", c=code)
    for r in prods:
        nodes.append({"id": "pr:%s" % r["nm"], "sector": "product",
                      "label": r["nm"], "w": 30,
                      "props": {"产品/业务": r["nm"]}})
        edges.append({"s": co_id, "t": "pr:%s" % r["nm"], "rel": "PRODUCES",
                      "w": 30})
    if prods:
        rel_stats.append({"src": "Company 公司", "dst": "Product 产品",
                          "rel": "PRODUCES", "strength": len(prods),
                          "count": len(prods), "note": "经营范围抽取"})

    # ④ 行业/同业/概念扇区：本体归属 + 同业 Security（真实 INDUSTRY_OF 边）
    ind_name = None
    ind = _q("MATCH (:Security {ts_code:$c})-[:INDUSTRY_OF]->(i:Industry) "
             "RETURN i.name AS nm", c=code)
    if ind:
        ind_name = ind[0]["nm"]
        nodes.append({"id": "ind:%s" % ind_name, "sector": "taxonomy",
                      "label": ind_name, "w": 60,
                      "props": {"细分行业": ind_name}})
        edges.append({"s": code, "t": "ind:%s" % ind_name,
                      "rel": "INDUSTRY_OF", "w": 60})
        rel_stats.append({"src": "Security 标的", "dst": "Industry 行业",
                          "rel": "INDUSTRY_OF", "strength": 1, "count": 1,
                          "note": ind_name})
    if b.get("sw_l1"):
        nodes.append({"id": "sw:%s" % b["sw_l1"], "sector": "taxonomy",
                      "label": "申万:%s" % b["sw_l1"], "w": 50,
                      "props": {"申万一级行业": b["sw_l1"]}})
        edges.append({"s": code, "t": "sw:%s" % b["sw_l1"],
                      "rel": "SW_L1_OF", "w": 50})
        rel_stats.append({"src": "Security 标的", "dst": "SwL1 申万一级",
                          "rel": "SW_L1_OF", "strength": 1, "count": 1,
                          "note": b["sw_l1"]})
    reg = _q("MATCH (:Security {ts_code:$c})-[:AREA_OF]->(r:Region) "
             "RETURN r.name AS nm", c=code)
    if reg:
        nodes.append({"id": "reg:%s" % reg[0]["nm"], "sector": "taxonomy",
                      "label": reg[0]["nm"], "w": 40,
                      "props": {"区域": reg[0]["nm"]}})
        edges.append({"s": code, "t": "reg:%s" % reg[0]["nm"],
                      "rel": "AREA_OF", "w": 40})
        rel_stats.append({"src": "Security 标的", "dst": "Region 区域",
                          "rel": "AREA_OF", "strength": 1, "count": 1,
                          "note": reg[0]["nm"]})
    cps = _q(
        "MATCH (:Security {ts_code:$c})-[:MAPS_TO]->(cp:Concept) "
        "RETURN cp.name AS nm LIMIT 8", c=code)
    for r in cps:
        nodes.append({"id": "cp:%s" % r["nm"], "sector": "taxonomy",
                      "label": "#%s" % r["nm"], "w": 35,
                      "props": {"概念": r["nm"]}})
        edges.append({"s": code, "t": "cp:%s" % r["nm"], "rel": "MAPS_TO",
                      "w": 35})
    if cps:
        rel_stats.append({"src": "Security 标的", "dst": "Concept 概念",
                          "rel": "MAPS_TO", "strength": len(cps),
                          "count": len(cps), "note": "via=alias/industry"})
    peers = []
    if ind_name:
        peers = _q(
            "MATCH (i:Industry {name:$n})<-[:INDUSTRY_OF]-(p:Security) "
            "WHERE p.ts_code <> $c "
            "OPTIONAL MATCH (p)-[r:HAS_STRUCTURE]->(m:MarketEvent) "
            "WHERE m.date >= $d0 "
            "RETURN p.ts_code AS tc, p.name AS nm, count(r) AS sig "
            "ORDER BY sig DESC, p.ts_code LIMIT 6",
            n=ind_name, c=code, d0=d0)
        for r in peers:
            nodes.append({"id": r["tc"], "sector": "taxonomy",
                          "label": r["nm"] or r["tc"], "w": 10 + r["sig"],
                          "props": {"同业标的": "%s %s" % (r["tc"],
                                                            r["nm"] or ""),
                                    "近%d天结构信号" % days: r["sig"]}})
            edges.append({"s": r["tc"], "t": "ind:%s" % ind_name,
                          "rel": "INDUSTRY_OF", "w": 10 + r["sig"]})
        if peers:
            rel_stats.append({"src": "Security 同业", "dst": "Industry 行业",
                              "rel": "INDUSTRY_OF", "strength": len(peers),
                              "count": len(peers),
                              "note": "%s·按近%d天信号数" % (ind_name,
                                                              days)})

    # ⑤ 游资扇区：HotMoney 经席位聚合到标的（近 N 天龙虎榜）
    hm_first = {r["hm"]: r["first"] for r in _q(
        "MATCH (h:HotMoney)-[:OCCUPIES]->(:Seat)-[t:TRADED_ON]->"
        "(:Security {ts_code:$c}) "
        "RETURN h.name AS hm, min(t.validStart) AS first", c=code)}
    hms = _q(
        "MATCH (h:HotMoney)-[:OCCUPIES]->(:Seat)-[t:TRADED_ON]->"
        "(:Security {ts_code:$c}) WHERE t.validStart >= $d0 "
        "RETURN h.name AS hm, count(*) AS n, sum(t.net_buy) AS net, "
        "max(t.validStart) AS last "
        "ORDER BY abs(sum(t.net_buy)) DESC LIMIT 12", c=code, d0=d0)
    for r in hms:
        nodes.append({"id": "hm:%s" % r["hm"], "sector": "hotmoney",
                      "label": r["hm"], "w": abs(_nn(r["net"]) or 0),
                      "props": {"游资": r["hm"], "上榜次数": r["n"],
                                 "净买(百万)": round(
                                     (_nn(r["net"]) or 0) / 1e6, 2),
                                 "区间": "%s ~ %s" % (
                                     _d8(hm_first.get(r["hm"]) or "")[:7],
                                     _d8(r["last"] or "")[:7]),
                                 "最近": _d8(r["last"])[:10] if r["last"]
                                 else "—"}})
        edges.append({"s": "hm:%s" % r["hm"], "t": code,
                      "rel": "TRADED_ON", "w": abs(_nn(r["net"]) or 0)})
    if hms:
        rel_stats.append({"src": "HotMoney 游资", "dst": "Security 标的",
                          "rel": "TRADED_ON",
                          "strength": round(sum(abs(_nn(r["net"]) or 0)
                                                for r in hms) / 1e8, 2),
                          "count": sum(r["n"] for r in hms),
                          "note": "经 Seat 席位聚合·净买合计亿"})

    # ⑥ 盘面事件扇区：MarketEvent 节点（近 N 天）+ DRIVE 来源组
    ev_rows = _q(
        "MATCH (s:Security {ts_code:$c})-[r]->(m:MarketEvent) "
        "WHERE m.date >= $d0 "
        "OPTIONAL MATCH (e:Event)-[d:DRIVE]->(m) "
        "RETURN m.evt_id AS eid, m.date AS d, m.type AS t, type(r) AS rt, "
        "r.confidence AS conf, d.group AS g "
        "ORDER BY d DESC LIMIT 160", c=code, d0=d0)
    seen, day_cnt, confs, grp_cnt, ev_grps = {}, {}, [], {}, {}
    for r in ev_rows:
        if r["eid"] not in seen:
            seen[r["eid"]] = r
            dd = r["d"] or ""
            key = _d8(dd)
            day_cnt[key] = day_cnt.get(key, 0) + 1
            if r["rt"] == "HAS_STRUCTURE" and _nn(r["conf"]) is not None:
                confs.append(round(float(r["conf"]), 3))
            ev_grps[r["eid"]] = set()
        if r["g"]:
            grp_cnt[r["g"]] = grp_cnt.get(r["g"], 0) + 1
            ev_grps.setdefault(r["eid"], set()).add(r["g"])
    for r in list(seen.values())[:14]:
        dd = r["d"] or ""
        lab = ("%s-%s" % (dd[4:6], dd[6:8])) if len(dd) == 8 else dd
        nodes.append({"id": "ev:%s" % r["eid"], "sector": "event",
                      "label": "%s %s" % (lab, r["t"] or ""),
                      "w": 10 + int((_nn(r["conf"]) or 0) * 40),
                      "props": {"盘面事件": r["t"] or "—", "日期": _d8(dd),
                                "关系": r["rt"],
                                "置信度": _nn(r["conf"])}})
        edges.append({"s": code, "t": "ev:%s" % r["eid"], "rel": r["rt"],
                      "w": 10 + int((_nn(r["conf"]) or 0) * 40)})
    top_grps = [g for g, _ in sorted(grp_cnt.items(),
                                     key=lambda x: -x[1])[:6]]
    for g in top_grps:
        nodes.append({"id": "eg:%s" % g, "sector": "event", "kind": "src",
                      "label": "来源:%s" % g, "w": 5 + grp_cnt[g],
                      "props": {"事件来源组": g, "伴随次数": grp_cnt[g],
                                "基线组": g in BASELINE_GRP}})
    for eid, gs in ev_grps.items():
        for g in gs & set(top_grps):
            edges.append({"s": "eg:%s" % g, "t": "ev:%s" % eid,
                          "rel": "DRIVE", "w": 5})
    ev_rel_cnt = {}
    for r in seen.values():
        ev_rel_cnt[r["rt"]] = ev_rel_cnt.get(r["rt"], 0) + 1
    for rt, n in ev_rel_cnt.items():
        rel_stats.append({"src": "Security 标的", "dst": "MarketEvent 盘面",
                          "rel": rt, "strength": n, "count": n,
                          "note": "近%d天（图上展示最近14条）" % days})
    if grp_cnt:
        rel_stats.append({"src": "Event 事件", "dst": "MarketEvent 盘面",
                          "rel": "DRIVE",
                          "strength": sum(grp_cnt.values()),
                          "count": sum(grp_cnt.values()),
                          "note": "来源组：" +
                                  " / ".join("%s×%d" % kv
                                             for kv in sorted(
                                                 grp_cnt.items(),
                                                 key=lambda x: -x[1])[:4])})

    # 直方图数据：实体类型分布 / 按日事件 / 游资净买 / conf 分布
    tc = {}
    for n in nodes:
        if n["sector"] != "company":
            tc[n["sector"]] = tc.get(n["sector"], 0) + 1
    bins = [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.01)]
    conf_bins = [{"label": "%.1f-%s" % (lo, "%.1f" % hi if hi <= 1
                                         else "1.0"),
                  "n": sum(1 for c in confs if lo <= c < hi)}
                 for lo, hi in bins]
    return {"ok": True, "code": code, "window_days": days, "per": per0,
            "basic": {"name": b.get("name"), "code": code,
                      "industry": b.get("industry"), "area": b.get("area"),
                      "list_date": b.get("list_date"),
                      "company": b.get("company"),
                      "sw_l1": b.get("sw_l1"),
                      "chairman": b.get("chairman"),
                      "manager": b.get("manager"),
                      "employees": _nn(b.get("employees")),
                      "reg_capital": _nn(b.get("reg_capital")),
                      "main_business": (b.get("main_business") or "")[:160],
                      "introduction": (b.get("intro") or "")[:400]},
            "nodes": nodes, "edges": edges,
            "hist": {"type_counts": sorted(tc.items(),
                                           key=lambda x: -x[1]),
                     "hold_periods": hold_periods,
                     "day_events": sorted(day_cnt.items()),
                     "hm_net": [{"k": r["hm"], "net": round(
                         (_nn(r["net"]) or 0) / 1e6, 2), "n": r["n"]}
                                for r in hms],
                     "conf_bins": conf_bins, "conf_n": len(confs)},
            "rel_stats": rel_stats}


# ── 游资主体名解析（精确→别名 CONTAINS，单候选自动跳转）──
def _resolve_hm(name):
    """返回 (注册名, None) 或 (None, 响应 dict)。"""
    if _q("MATCH (h:HotMoney {name:$n}) RETURN h.name", n=name):
        return name, None
    cands = _q("MATCH (h:HotMoney) WHERE h.name CONTAINS $n "
               "RETURN h.name AS name LIMIT 5", n=name)
    if len(cands) == 1:
        return cands[0]["name"], None
    if cands:
        return None, {"ok": False, "candidates": [c["name"] for c in cands],
                      "error": "未找到 %s，候选：%s"
                               % (name, " / ".join(c["name"] for c in cands))}
    return None, {"ok": False, "error": "未找到游资主体 %s" % name}


# ── /kg/hotmoney/{name}：游资归因链 ───────────────────────
def hotmoney(name, days=180):
    name, err = _resolve_hm(name)
    if err:
        return err
    d0 = _d0(days)
    seats = [r["seat"] for r in _q(
        "MATCH (:HotMoney {name:$n})-[:OCCUPIES]->(st:Seat) "
        "RETURN st.name AS seat ORDER BY seat", n=name)]
    tops = _q(
        "MATCH (:HotMoney {name:$n})-[:OCCUPIES]->(:Seat)"
        "-[t:TRADED_ON]->(sec:Security) WHERE t.validStart >= $d0 "
        "RETURN sec.ts_code AS tc, sec.name AS nm, count(*) AS n, "
        "sum(t.net_buy) AS net, max(t.validStart) AS last "
        "ORDER BY net DESC LIMIT 15", n=name, d0=d0)
    recent = _q(
        "MATCH (:HotMoney {name:$n})-[:OCCUPIES]->(:Seat)"
        "-[t:TRADED_ON]->(sec:Security) WHERE t.validStart >= $d0 "
        "RETURN t.validStart AS date, sec.ts_code AS tc, sec.name AS nm, "
        "t.net_buy AS net "
        "ORDER BY date DESC, abs(t.net_buy) DESC LIMIT 30", n=name, d0=d0)
    tot = _q(
        "MATCH (:HotMoney {name:$n})-[:OCCUPIES]->(:Seat)"
        "-[t:TRADED_ON]->(:Security) WHERE t.validStart >= $d0 "
        "RETURN count(*) AS n, sum(t.net_buy) AS net", n=name, d0=d0)[0]
    return {"ok": True, "name": name, "window_days": days,
            "seats": seats, "seats_n": len(seats),
            "trades_n": tot["n"], "net_buy_total": tot["net"],
            "top_securities": tops, "recent_moves": recent}


# ── /kg/map：游资地图（19 主体全景 + 共现联动 + 动向流）──
def hotmoney_map(days=90):
    d0 = _d0(days)
    roster = _q(
        "MATCH (h:HotMoney) "
        "OPTIONAL MATCH (h)-[:OCCUPIES]->(st:Seat) "
        "WITH h, count(DISTINCT st) AS seats "
        "OPTIONAL MATCH (h)-[:OCCUPIES]->(:Seat)"
        "-[t:TRADED_ON]->(sec:Security) WHERE t.validStart >= $d0 "
        "RETURN h.name AS name, seats, count(DISTINCT sec) AS nsec, "
        "count(t) AS n, sum(t.net_buy) AS net, max(t.validStart) AS last "
        "ORDER BY net DESC", d0=d0)
    flow = _q(
        "MATCH (h:HotMoney)-[:OCCUPIES]->(:Seat)"
        "-[t:TRADED_ON]->(sec:Security) WHERE t.validStart >= $d0 "
        "RETURN t.validStart AS date, h.name AS hm, sec.ts_code AS tc, "
        "sec.name AS nm, t.net_buy AS net "
        "ORDER BY date DESC, abs(t.net_buy) DESC LIMIT 40", d0=d0)
    pairs = _q(
        "MATCH (h1:HotMoney)-[:OCCUPIES]->(:Seat)"
        "-[t1:TRADED_ON]->(sec:Security) "
        "MATCH (h2:HotMoney)-[:OCCUPIES]->(:Seat)"
        "-[t2:TRADED_ON]->(sec) "
        "WHERE t1.validStart >= $d0 AND t1.validStart = t2.validStart "
        "AND h1.name < h2.name "
        "RETURN h1.name AS a, h2.name AS b, count(*) AS n, "
        "collect(DISTINCT sec.ts_code)[..3] AS tcs "
        "ORDER BY n DESC LIMIT 8", d0=d0)
    return {"ok": True, "window_days": days, "roster": roster,
            "flow": flow, "pairs": pairs}


# ── /kg/map 集团网络画布（KG-V1/V2/V3）：json 成员快照 + 在线共现边 ──
# 共现按日行 Cypher 结果带 10min TTL 缓存（map_graph/map_overview 同页共享，
# 避免总览层+席位层各查一遍大自连接）
_mg_cache = {"ts": 0.0, "d0": "", "rows": None}


def _map_pool_and_comm():
    """读 kg_communities.json → (nodes, seat_comm, pool, watchlist, meta)。"""
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "data", "kg_communities.json")
    if not os.path.exists(p):
        return None
    cm = json.load(open(p, encoding="utf-8"))
    nodes, seat_comm, pool = [], {}, []
    for c in cm.get("communities") or []:
        for m in c.get("members") or []:
            if m["seat"] in seat_comm:
                continue
            seat_comm[m["seat"]] = c["name"]
            pool.append(m["seat"])
            nodes.append({"seat": m["seat"], "reg": m.get("reg") or "",
                          "n": m.get("n") or 0, "deg": m.get("deg") or 0,
                          "comm": c["name"]})
    wl = {w["seat"]: w for w in cm.get("watchlist") or []}
    for s, w in wl.items():
        if s not in seat_comm:
            seat_comm[s] = w.get("comm") or ""
            pool.append(s)
            nodes.append({"seat": s, "reg": "", "n": w.get("n") or 0,
                          "deg": w.get("deg") or 0,
                          "comm": w.get("comm") or ""})
    return nodes, seat_comm, pool, wl, cm


def _map_rows(days, pool):
    """席位池内同日同标的共现按日行（10min TTL）。"""
    d0 = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    now = datetime.now().timestamp()
    with _lock:
        if (_mg_cache["rows"] is not None and _mg_cache["d0"] == d0
                and now - _mg_cache["ts"] < 600):
            return _mg_cache["rows"]
    rows = _q(
        "MATCH (s1:Seat)-[t1:TRADED_ON]->(sec:Security) "
        "MATCH (s2:Seat)-[t2:TRADED_ON]->(sec) "
        "WHERE t1.validStart >= $d0 AND t1.validStart = t2.validStart "
        "AND s1.name < s2.name AND s1.name IN $pool AND s2.name IN $pool "
        "WITH s1.name AS a, s2.name AS b, t1.validStart AS d, "
        "count(*) AS w, sum(CASE WHEN t1.net_buy * t2.net_buy < 0 "
        "THEN 1 ELSE 0 END) AS opp "
        "RETURN a, b, d, w, opp", d0=d0, pool=pool)
    with _lock:
        _mg_cache.update(ts=now, d0=d0, rows=rows)
    return rows


def map_graph(min_w=2):
    """节点=kg_communities.json 成员+watchlist（席位级：集团/注册主体/
    上榜次数/共现度），边=席位池内同日同标的共现（Cypher 在线拉，
    窗口跟随 json.days 快照口径，保证与集团划分一致）。
    KG-V2b：每边附 opp=对手盘共现次数（同日同标的一净买一净卖），
    供净流向对冲着色。KG-V3：按日行（pair×日）+ Python 分组出
    s=[[yyyymmdd,当期w,当期opp],...] 逐日序列，供前端操作回放
    （按交易日推进累计重渲）；边总量/对手数与序列自洽。"""
    pc = _map_pool_and_comm()
    if pc is None:
        return {"ok": False, "error": "kg_communities.json 不存在，"
                                      "先跑 kg_communities.py"}
    nodes, seat_comm, pool, wl, cm = pc
    days = int(cm.get("days") or 180)
    rows = _map_rows(days, pool)
    agg = {}
    for r in rows:
        v = agg.setdefault((r["a"], r["b"]), {"w": 0, "opp": 0, "s": []})
        v["w"] += r["w"]
        v["opp"] += r["opp"]
        v["s"].append([r["d"], r["w"], r["opp"]])
    edges = [{"a": a, "b": b, "w": v["w"], "opp": v["opp"],
              "s": sorted(v["s"])}
             for (a, b), v in agg.items() if v["w"] >= min_w]
    edges.sort(key=lambda e: -e["w"])
    edges = edges[:800]
    # P1-2 历史胜率：Seat.wr_* 注入节点（kg_seat_winrate 夜批回填）
    wrmap = {r["seat"]: r for r in _q(
        "MATCH (s:Seat) WHERE s.wr_win_rate IS NOT NULL "
        "RETURN s.name AS seat, s.wr_win_rate AS wr, s.wr_n AS wrn")}
    for n in nodes:
        w = wrmap.get(n["seat"])
        if w:
            n["wr"] = round(float(w["wr"]), 3)
            n["wrn"] = int(w["wrn"])
    return {"ok": True, "days": days, "min_w": min_w,
            "n_nodes": len(nodes), "n_edges": len(edges),
            "nodes": nodes, "edges": edges,
            "watchlist": sorted(wl.keys()),
            "generated_at": cm.get("generated_at", "")}


# ── /kg/overview 集团总览层（KG-V4）：总览-下钻第一层 ──
def map_overview(top=8):
    """G6 总览画布数据：节点=集团超节点（size∝成员共现度合计，
    附 top 成员按 deg 预载），边=跨集团桥接（w=集团间席位对共现
    总和，opp=对手盘次数）。watchlist 席位并入所属集团统计。
    复用 _map_pool_and_comm + _map_rows 缓存，无额外 Cypher。"""
    pc = _map_pool_and_comm()
    if pc is None:
        return {"ok": False, "error": "kg_communities.json 不存在，"
                                      "先跑 kg_communities.py"}
    nodes, seat_comm, pool, wl, cm = pc
    days = int(cm.get("days") or 180)
    groups, gid_of = [], {}
    for c in cm.get("communities") or []:
        mem = sorted(c.get("members") or [],
                     key=lambda m: -(m.get("deg") or 0))
        g = {"id": str(c.get("id") or c["name"]), "name": c["name"],
             "short": c["name"].replace(" 集团", ""),
             "anchor": c.get("anchor") or "",
             "has_reg": bool(c.get("has_reg")),
             "n_seats": c.get("n_seats") or len(mem),
             "n_pairs": c.get("n_pairs") or 0,
             "w_total": c.get("w_total") or 0,
             "deg_sum": sum(m.get("deg") or 0 for m in mem),
             "members_top": [{"seat": m["seat"], "reg": m.get("reg") or "",
                              "n": m.get("n") or 0, "deg": m.get("deg") or 0}
                             for m in mem[:int(top)]]}
        gid_of[c["name"]] = g["id"]
        groups.append(g)
    wl_n = {g["id"]: 0 for g in groups}
    for s, w in wl.items():
        cn = seat_comm.get(s)
        if cn in gid_of:
            wl_n[gid_of[cn]] += 1
    for g in groups:
        g["wl_n"] = wl_n.get(g["id"], 0)
    rows = _map_rows(days, pool)
    lagg = {}
    for r in rows:
        ca, cb = seat_comm.get(r["a"]), seat_comm.get(r["b"])
        if not ca or not cb or ca == cb:
            continue
        key = tuple(sorted((gid_of[ca], gid_of[cb])))
        v = lagg.setdefault(key, {"w": 0, "opp": 0})
        v["w"] += r["w"]
        v["opp"] += r["opp"]
    links = [{"a": a, "b": b, "w": v["w"], "opp": v["opp"]}
             for (a, b), v in lagg.items() if v["w"] > 0]
    links.sort(key=lambda x: -x["w"])
    return {"ok": True, "days": days, "generated_at":
            cm.get("generated_at", ""), "groups": groups, "links": links}


# ── /kg/map?hm=：席位风格画像（行业/区域/集中度/频次/连续段）──
def hm_profile(name, days=365):
    name, err = _resolve_hm(name)
    if err:
        return err
    d0 = _d0(days)
    ind = _q(
        "MATCH (:HotMoney {name:$n})-[:OCCUPIES]->(:Seat)"
        "-[t:TRADED_ON]->(sec:Security) WHERE t.validStart >= $d0 "
        "RETURN coalesce(sec.industry,'未分类') AS k, count(*) AS n, "
        "sum(t.net_buy) AS net ORDER BY n DESC LIMIT 6", n=name, d0=d0)
    area = _q(
        "MATCH (:HotMoney {name:$n})-[:OCCUPIES]->(:Seat)"
        "-[t:TRADED_ON]->(sec:Security) WHERE t.validStart >= $d0 "
        "RETURN coalesce(sec.area,'-') AS k, count(*) AS n "
        "ORDER BY n DESC LIMIT 6", n=name, d0=d0)
    monthly = _q(
        "MATCH (:HotMoney {name:$n})-[:OCCUPIES]->(:Seat)"
        "-[t:TRADED_ON]->(:Security) WHERE t.validStart >= $d0 "
        "RETURN left(t.validStart,6) AS ym, count(*) AS n "
        "ORDER BY ym", n=name, d0=d0)
    detail = _q(
        "MATCH (:HotMoney {name:$n})-[:OCCUPIES]->(:Seat)"
        "-[t:TRADED_ON]->(sec:Security) WHERE t.validStart >= $d0 "
        "RETURN t.validStart AS d, sec.ts_code AS tc "
        "ORDER BY d", n=name, d0=d0)
    # 连续上榜段：同一标的相邻上榜日期差 ≤3 日历日算同一波（口径近似，
    # 节假日长间隙会断段，倾向低估持有天数）
    import datetime as _dt
    from collections import defaultdict
    by_tc = defaultdict(list)
    for r in detail:
        by_tc[r["tc"]].append(_dt.datetime.strptime(r["d"], "%Y%m%d").date())
    runs = []
    for tc, ds in by_tc.items():
        run = 1
        for i in range(1, len(ds)):
            if (ds[i] - ds[i - 1]).days <= 3:
                run += 1
            else:
                runs.append(run)
                run = 1
        runs.append(run)
    runs.sort()
    n_runs = len(runs) or 1
    hm = hotmoney(name, min(days, 180))
    tops = hm.get("top_securities") or []
    top_net = sum(abs(r["net"] or 0) for r in tops[:5])
    tot_net = sum(abs(r["net"] or 0) for r in tops) or 1
    # 历史胜率（kg_seat_winrate 夜批回填的主体级属性；无则 None）
    wr = _q("MATCH (h:HotMoney {name:$n}) WHERE h.wr_n IS NOT NULL "
            "RETURN h.wr_n AS n, h.wr_win_rate AS rate, "
            "h.wr_avg_ret AS avg_ret", n=name)
    # P2-① 榜后差额参与：主体席位上榜的同票同日 topgap 事件（近 15），
    # pen_rate = 参与事件中差额方向打进次日价格的比例
    tgr = _q(
        "MATCH (:HotMoney {name:$n})-[:OCCUPIES]->(:Seat)"
        "-[t:TRADED_ON]->(sec:Security)-[:HAS_TOPGAP]->(m:MarketEvent) "
        "WHERE m.date = t.validStart "
        "RETURN DISTINCT m.date AS d, sec.ts_code AS tc, sec.name AS nm, "
        "m.gap AS gap, m.gap_ratio AS gr, m.dom_nature AS nat, "
        "m.ret1 AS ret1, m.pen AS pen "
        "ORDER BY d DESC LIMIT 15", n=name)
    pens = [r["pen"] for r in tgr if r.get("pen") is not None]
    topgap = {"rows": tgr, "n": len(tgr),
              "pen_rate": (round(sum(pens) / len(pens), 3) if pens else None)}
    return {"ok": True, "name": name, "window_days": days,
            "win_rate": (wr[0] if wr else None),
            "industry_pref": ind, "area_pref": area, "monthly": monthly,
            "run_len": {"avg": round(sum(runs) / n_runs, 1),
                        "median": runs[len(runs) // 2],
                        "max": max(runs) if runs else 0,
                        "n_runs": len(runs)},
            "concentration": round(top_net / tot_net, 2),
            "seats_n": hm.get("seats_n", 0),
            "trades_n": hm.get("trades_n", 0),
            "net_buy_total": hm.get("net_buy_total", 0),
            "top_securities": tops, "topgap": topgap}


# ── /kg/geo：空间层（P2-③）席位城市散点 + 标的注册地省份分布 ──
# 城市提取=席位名关键词匹配（"城市词后一字符≠路"规则：银河"杭州绍兴路"
# 是杭州的绍兴路，非绍兴）；静态坐标表 CITY_COORDS（城市中心近似）；
# GeoJSON 本地托管 /static/china_geo.json（DataV areas_v3 100000_full），
# ECharts geo 注册渲染，零新 JS 库依赖。
CITY_COORDS = {
    "北京": (116.41, 40.19), "上海": (121.47, 31.23), "深圳": (114.06, 22.55),
    "广州": (113.26, 23.13), "杭州": (120.16, 30.29), "南京": (118.78, 32.06),
    "成都": (104.07, 30.57), "武汉": (114.31, 30.59), "西安": (108.94, 34.34),
    "拉萨": (91.11, 29.65), "苏州": (120.58, 31.30), "宁波": (121.55, 29.87),
    "绍兴": (120.58, 30.03), "常州": (119.97, 31.81), "佛山": (113.12, 23.02),
    "天津": (117.20, 39.08), "大连": (121.61, 38.91), "宜昌": (111.29, 30.69),
    "泰州": (119.92, 32.46), "湛江": (110.36, 21.27), "郑州": (113.63, 34.75),
    "福州": (119.30, 26.08), "乐山": (103.77, 29.55), "青岛": (120.38, 36.07),
    "厦门": (118.09, 24.48), "长沙": (112.94, 28.23), "合肥": (117.23, 31.82),
    "济南": (117.12, 36.65), "重庆": (106.55, 29.56), "昆明": (102.83, 24.88),
    "沈阳": (123.43, 41.80), "哈尔滨": (126.53, 45.80), "无锡": (120.31, 31.49),
    "东莞": (113.75, 23.02), "珠海": (113.58, 22.27), "温州": (120.70, 28.00),
    "嘉兴": (120.76, 30.75), "金华": (119.65, 29.08), "义乌": (120.08, 29.31),
    "南昌": (115.86, 28.68), "贵阳": (106.63, 26.65), "兰州": (103.83, 36.06),
    "乌鲁木齐": (87.62, 43.83), "西宁": (101.78, 36.62), "银川": (106.23, 38.49),
    "呼和浩特": (111.75, 40.84), "太原": (112.55, 37.87), "石家庄": (114.51, 38.04),
    "南宁": (108.37, 22.82), "海口": (110.20, 20.04), "烟台": (121.45, 37.46),
    "南通": (120.86, 32.01), "徐州": (117.28, 34.20), "扬州": (119.42, 32.39),
    "芜湖": (118.38, 31.33), "株洲": (113.13, 27.83), "岳阳": (113.13, 29.36),
    "柳州": (109.42, 24.33), "绵阳": (104.68, 31.47), "宝鸡": (107.24, 34.36),
    "咸阳": (108.71, 34.33), "包头": (109.84, 40.66), "鞍山": (122.99, 41.11),
    "长春": (125.32, 43.90), "吉林": (126.55, 43.84), "泉州": (118.68, 24.88),
    "漳州": (117.65, 24.51), "惠州": (114.42, 23.11), "中山": (113.39, 22.52),
    "江门": (113.08, 22.58), "汕头": (116.68, 23.35), "湖州": (120.09, 30.89),
    "台州": (121.42, 28.66),
}
# 区级关键词 → 归属城市（席位名只写区不写市时兜底）
DISTRICT_CITY = {"长宁": "上海", "浦东": "上海", "静安": "上海",
                 "松江": "上海", "徐汇": "上海", "普陀": "上海",
                 "虹口": "上海", "杨浦": "上海", "闵行": "上海",
                 "陆家嘴": "上海", "中关村": "北京"}
# 省份词 → 省会（"陕西分公司"这类无城市词的分公司名兜底）
PROV_CAPITAL = {
    "浙江": "杭州", "江苏": "南京", "广东": "广州", "山东": "济南",
    "四川": "成都", "福建": "福州", "湖北": "武汉", "湖南": "长沙",
    "河南": "郑州", "安徽": "合肥", "河北": "石家庄", "陕西": "西安",
    "辽宁": "沈阳", "吉林": "长春", "黑龙江": "哈尔滨", "山西": "太原",
    "江西": "南昌", "广西": "南宁", "云南": "昆明", "贵州": "贵阳",
    "甘肃": "兰州", "青海": "西宁", "海南": "海口", "宁夏": "银川",
    "新疆": "乌鲁木齐", "内蒙古": "呼和浩特", "西藏": "拉萨",
    "北京": "北京", "上海": "上海", "天津": "天津", "重庆": "重庆",
}
# stock_basic.area 短省名 → GeoJSON feature name（"深圳"市值并入广东）
GEO_PROV = {
    "北京": "北京市", "天津": "天津市", "河北": "河北省", "山西": "山西省",
    "内蒙": "内蒙古自治区", "辽宁": "辽宁省", "吉林": "吉林省",
    "黑龙江": "黑龙江省", "上海": "上海市", "江苏": "江苏省",
    "浙江": "浙江省", "安徽": "安徽省", "福建": "福建省",
    "江西": "江西省", "山东": "山东省", "河南": "河南省",
    "湖北": "湖北省", "湖南": "湖南省", "广东": "广东省",
    "深圳": "广东省", "广西": "广西壮族自治区", "海南": "海南省",
    "重庆": "重庆市", "四川": "四川省", "贵州": "贵州省",
    "云南": "云南省", "西藏": "西藏自治区", "陕西": "陕西省",
    "甘肃": "甘肃省", "青海": "青海省", "宁夏": "宁夏回族自治区",
    "新疆": "新疆维吾尔自治区",
}


def _seat_city(seat):
    """席位名 → 城市（城市词后一字符≠'路' 规则防"杭州绍兴路"误配）。"""
    for city in CITY_COORDS:
        pos = seat.find(city)
        while pos >= 0:
            if seat[pos + len(city):pos + len(city) + 1] != "路":
                return city
            pos = seat.find(city, pos + 1)
    for kw, c in DISTRICT_CITY.items():
        if kw in seat:
            return c
    for prov, cap in PROV_CAPITAL.items():
        if prov in seat:
            return cap
    return None


def geo(days=90):
    """空间层数据：窗口内席位城市聚合（净买/笔数/历史胜率加权）+
    标的注册地省份分布。wr=K14 口径全历史次日胜率（Seat.wr_*），
    城市级按 wr_n 上榜笔数加权；无胜率席位不计入分母。"""
    d0 = _d0(days)
    seats = _q(
        "MATCH (st:Seat)-[t:TRADED_ON]->(s:Security) "
        "WHERE t.validStart >= $d0 "
        "RETURN st.name AS seat, count(*) AS n, sum(t.net_buy) AS net, "
        "st.wr_win_rate AS wr, st.wr_n AS wrn",
        d0=d0)
    cities, unres = {}, 0
    for r in seats:
        c = _seat_city(r["seat"])
        if c is None:
            unres += 1
            continue
        d = cities.setdefault(c, {"city": c, "n_seat": 0, "n": 0,
                                  "net": 0.0, "wwr": 0.0, "wwn": 0})
        d["n_seat"] += 1
        d["n"] += r["n"]
        d["net"] += r["net"] or 0.0
        if r.get("wr") is not None and r.get("wrn"):
            d["wwr"] += r["wr"] * r["wrn"]
            d["wwn"] += r["wrn"]
    city_rows = []
    for d in cities.values():
        wr = round(d["wwr"] / d["wwn"] * 100, 1) if d["wwn"] else None
        city_rows.append(
            {"city": d["city"], "coord": list(CITY_COORDS[d["city"]]),
             "n_seat": d["n_seat"], "n": d["n"], "net": round(d["net"], 0),
             "wr": wr, "wrn": d["wwn"],
             "wr_disp": ("%s%%" % wr) if wr is not None else "—",
             "wr_n_disp": ("%d 笔" % d["wwn"]) if d["wwn"] else "—"})
    city_rows.sort(key=lambda x: -abs(x["net"]))
    # K132-B：按交易日逐日矩阵（滑杆累计回放，KG-V3 口径）——
    # 日 → {城市: {net,n,wr,wrn}}；席位→城市与主聚合同解析器。
    # K134-B 滚动胜率：wr/wrn=截至该日的窗口内活跃席位按全历史
    # 笔数（wrn）加权胜率——席位首现即计入（去重），同 K132-A
    # 静态口径的窗口版；无胜率席位的城不带 wr 键。
    bd = {}
    seen = {}   # city -> {seat: True} 已计入席位
    cwr = {}    # city -> [wr*wrn 加权和, wrn 累积]
    for r in _q(
            "MATCH (st:Seat)-[t:TRADED_ON]->(s:Security) "
            "WHERE t.validStart >= $d0 "
            "RETURN t.validStart AS d, st.name AS seat, "
            "count(*) AS n, sum(t.net_buy) AS net, "
            "st.wr_win_rate AS wr, st.wr_n AS wrn", d0=d0):
        c = _seat_city(r["seat"])
        if c is None:
            continue
        dd = r["d"][:10]
        e = bd.setdefault(dd, {})
        cur = e.get(c)
        if cur is None:
            e[c] = {"net": r["net"] or 0.0, "n": r["n"]}
        else:
            cur["net"] += r["net"] or 0.0
            cur["n"] += r["n"]
        if r.get("wr") is not None and r.get("wrn"):
            ss = seen.setdefault(c, {})
            if r["seat"] not in ss:
                ss[r["seat"]] = True
                a2 = cwr.setdefault(c, [0.0, 0])
                a2[0] += r["wr"] * r["wrn"]
                a2[1] += r["wrn"]
        w = cwr.get(c)
        if w and w[1]:
            # 每行刷新为当前累积值；该日全部行处理完即终值
            e[c]["wr"] = round(w[0] / w[1], 1)
            e[c]["wrn"] = w[1]
    byday = [{"d": d, "cities": {c: {"net": round(v["net"], 0),
                                     "n": v["n"],
                                     **({"wr": v["wr"], "wrn": v["wrn"]}
                                        if "wr" in v else {})}
                                 for c, v in cs.items()}}
             for d, cs in sorted(bd.items())]
    provs = {}
    for r in _q("MATCH (s:Security)-[:AREA_OF]->(g:Region) "
                "RETURN g.name AS p, count(*) AS n"):
        g = GEO_PROV.get(r["p"])
        if g:
            provs[g] = provs.get(g, 0) + r["n"]
    prov_rows = [{"name": k, "n": v} for k, v in provs.items()]
    prov_rows.sort(key=lambda x: -x["n"])
    return {"ok": True, "days": days, "cities": city_rows,
            "provs": prov_rows, "unresolved": unres, "byday": byday}


# ── /kg/distill：蒸馏样本库统计（读 build 产物 json，不碰 parquet）──
def distill_info():
    import json
    import os
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "data", "distill", "distill_stats.json")
    if not os.path.exists(p):
        return {"ok": False,
                "error": "样本库未构建（quant_backtest: kg_distill.py build）"}
    with open(p, encoding="utf-8") as f:
        d = json.load(f)
    d["ok"] = True
    return d


# ── /kg/shadow：影子面板（信号当时可见 vs 后验标注按日对照）──
def shadow(days=60):
    """M2-W4c 影子口径的面板数据：信号面的 q 三因子是"当时可见"，
    hm_tag/post_hm10 是影子后验（pre 窗口不含未来信息，post 窗口回填）。"""
    d0 = _d0(days)
    byday = _q(
        "MATCH ()-[r:HAS_STRUCTURE]->(m:MarketEvent) WHERE m.date >= $d0 "
        "RETURN m.date AS date, count(*) AS n, "
        "sum(CASE WHEN r.hm_tag IS NOT NULL AND r.hm_tag <> 'none' "
        "AND r.hm_tag <> 'pending' THEN 1 ELSE 0 END) AS n_tag, "
        "sum(CASE WHEN r.hm_tag = 'follow_confirm' THEN 1 ELSE 0 END) AS fc, "
        "sum(CASE WHEN r.hm_tag = 'lure_warn' THEN 1 ELSE 0 END) AS lw, "
        "sum(CASE WHEN r.q_hm >= 1.0 THEN 1 ELSE 0 END) AS n_lead, "
        "round(avg(r.confidence), 3) AS conf, "
        "round(avg(r.q_hm), 2) AS q_hm "
        "ORDER BY date DESC", d0=d0)
    detail = _q(
        "MATCH (sec:Security)-[r:HAS_STRUCTURE]->(m:MarketEvent) "
        "WHERE m.date >= $d0 AND r.hm_tag IN ['follow_confirm','lure_warn'] "
        "RETURN m.date AS date, sec.ts_code AS tc, sec.name AS nm, "
        "m.type AS type, r.confidence AS conf, r.q_struct AS qs, "
        "r.q_evid AS qe, r.q_hm AS qh, r.hm_tag AS tag, "
        "r.post_hm10 AS post "
        "ORDER BY date DESC, conf DESC LIMIT 100", d0=d0)
    tot = _q(
        "MATCH ()-[r:HAS_STRUCTURE]->(m:MarketEvent) WHERE m.date >= $d0 "
        "RETURN count(*) AS n, "
        "sum(CASE WHEN r.q_hm >= 1.0 THEN 1 ELSE 0 END) AS n_lead, "
        "sum(CASE WHEN r.hm_tag = 'follow_confirm' THEN 1 ELSE 0 END) AS fc, "
        "sum(CASE WHEN r.hm_tag = 'lure_warn' THEN 1 ELSE 0 END) AS lw, "
        "min(m.date) AS d_min, max(m.date) AS d_max", d0=d0)[0]
    return {"ok": True, "window_days": days, "byday": byday,
            "detail": detail, "total": tot}


# ── /kg/trace/{code}：ACH 三假设 + 正向传导树（kg_trace 同源）──
def trace(code, days=30):
    head = _q(
        "MATCH (n:Security {ts_code:$c}) "
        "RETURN n.name AS name, n.industry AS industry", c=code)
    if not head:
        return {"ok": False, "error": "未找到标的 %s" % code}
    d0 = _d0(days)

    # H1 事件驱动（实质主题组）
    h1 = _q(
        "MATCH (e:Event)-[d:DRIVE]->(:MarketEvent)"
        "<-[:HAS_STRUCTURE|HAS_VOL_EVENT]-(sec:Security {ts_code:$c}) "
        "WHERE d.validStart >= $d0 AND NOT coalesce(d.group,'') IN $base "
        "RETURN d.group AS grp, count(*) AS n, "
        "collect(DISTINCT left(coalesce(e.text,''),30))[..2] AS texts "
        "ORDER BY n DESC", c=code, d0=d0, base=list(BASELINE_GRP))
    h1_n = sum(r["n"] for r in h1)

    # H2 资金驱动/确认（M2-W4c：游资多为跟随确认者）
    h2 = _q(
        "MATCH (hm:HotMoney)-[:OCCUPIES]->(:Seat)-[t:TRADED_ON]->"
        "(sec:Security {ts_code:$c}) WHERE t.validStart >= $d0 "
        "RETURN hm.name AS hm, count(*) AS n, sum(t.net_buy) AS net",
        c=code, d0=d0)
    h2_net = sum(r["net"] or 0 for r in h2)
    tg = {r["t"]: r["n"] for r in _q(
        "MATCH (sec:Security {ts_code:$c})-[r:HAS_STRUCTURE]->(m:MarketEvent) "
        "WHERE m.date >= $d0 AND r.hm_tag IS NOT NULL "
        "RETURN r.hm_tag AS t, count(*) AS n", c=code, d0=d0)}
    n_fc = tg.get("follow_confirm", 0)

    # H3 博弈驱动（量能密度 × 无佐证占比；Event 流 D0 前不采信）
    h3 = _q(
        "MATCH (sec:Security {ts_code:$c})-[r]->(m:MarketEvent) "
        "WHERE m.date >= $d0 "
        "OPTIONAL MATCH (:Event)-[d:DRIVE]->(m) "
        "WITH m, type(r) AS rt, count(d) AS nd "
        "RETURN rt = 'HAS_VOL_EVENT' AS is_vol, count(*) AS n, "
        "sum(CASE WHEN nd = 0 THEN 1 ELSE 0 END) AS n_noev",
        c=code, d0=d0)
    n_vol = n_sig = no_ev = 0
    for r in h3:
        if r["is_vol"]:
            n_vol = r["n"]
        else:
            n_sig, no_ev = r["n"], r["n_noev"]
    ev_min = _q("MATCH (e:Event) RETURN min(e.date) AS d")[0]["d"]
    d0_eff = max(d0, ev_min)  # Event 流起点前无数据，不采信"无佐证"

    # 规则打分（透明可解释，仅排序辅助）——与 kg_trace.ach 完全同式
    s1 = min(1.0, h1_n / 4.0)
    s2 = min(1.0, max(0.0, h2_net) / 2e8 + len(h2) / 10.0 + n_fc * 0.25)
    s3 = min(1.0, n_vol / 8.0) * (0.5 + 0.5 * (n_sig and no_ev / n_sig or 0))
    scores = {"H1 事件驱动": round(s1, 3), "H2 资金驱动": round(s2, 3),
              "H3 博弈驱动": round(s3, 3)}
    top = max(scores.values())
    if top <= 0:
        verdict = "窗口内证据不足（全部假设支持度≈0），不下结论"
    else:
        winners = [h for h, v in scores.items() if abs(v - top) < 1e-9]
        verdict = " 与 ".join(winners) + "（规则版排序，供 ACH 人工复核起点）"

    # 正向传导树（盘面事件 ← DRIVE 来源）
    rows = _q(
        "MATCH (sec:Security {ts_code:$c})"
        "-[:HAS_STRUCTURE|HAS_VOL_EVENT]->(m:MarketEvent) "
        "WHERE m.date >= $d0 "
        "OPTIONAL MATCH (e:Event)-[d:DRIVE]->(m) "
        "RETURN m.date AS md, m.type AS mt, m.evt_id AS me, "
        "e.id AS eid, left(coalesce(e.text,''),40) AS txt, "
        "d.group AS g, d.lag_days AS lag, d.p AS p "
        "ORDER BY md DESC", c=code, d0=d0)
    tree, cur = [], None
    for r in rows:
        if not cur or r["me"] != cur["evt"]:
            cur = {"date": r["md"], "type": r["mt"], "evt": r["me"],
                   "sources": []}
            tree.append(cur)
        if r["eid"]:
            cur["sources"].append({
                "grp": r["g"],
                "baseline": r["g"] in BASELINE_GRP,
                "lag": r["lag"], "p": r["p"], "text": r["txt"]})
    return {"ok": True, "code": code, "name": head[0]["name"],
            "industry": head[0]["industry"], "window_days": days,
            "ach": {"h1_evidence": h1, "h2_hotmoney": sorted(
                h2, key=lambda x: -(x["net"] or 0))[:8],
                "h2_net_total": h2_net, "hm_tags": tg,
                "h3": {"vol_events": n_vol, "signals": n_sig,
                       "no_evidence": no_ev},
                "event_flow_start": ev_min,
                "scores": scores, "verdict": verdict},
            "fwd_tree": tree[:40]}


# ── 探索页辅助：标的搜索 / 近期确认信号 ──────────────────
def sources():
    """G7 来源可靠性评级（Admiralty）：读 kg_check 落盘的 rating JSON。

    纯文件读不查图（每日链 graph_check 步骤顺带刷新该 JSON）；
    文件缺失时现算兜底（离线调试场景）。
    """
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "data", "kg_source_rating.json")
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except FileNotFoundError:
        import kg_source_rating
        d = kg_source_rating.build()
    out = []
    for fam, m in d.get("sources", {}).items():
        out.append({"family": fam, "grade": m["grade"],
                    "basis": m["basis"],
                    "stale": d.get("staleness", {}).get(
                        "stale_by_family", {}).get(fam, [])})
    out.sort(key=lambda x: (x["grade"], x["family"]))
    return {"ok": True, "ts": d.get("ts"),
            "catalog_stale": d.get("staleness", {}).get("catalog_stale",
                                                        False),
            "catalog_age_days": d.get("staleness", {}).get(
                "catalog_age_days"),
            "sources": out}


def lookup(q):
    """搜索：ts_code 精确或 name/拼音前缀包含（limit 8）。"""
    rows = _q(
        "MATCH (n:Security) WHERE n.ts_code STARTS WITH $q "
        "OR (n.name IS NOT NULL AND n.name CONTAINS $q) "
        "RETURN n.ts_code AS tc, n.name AS nm, n.industry AS ind "
        "LIMIT 8", q=q)
    return {"ok": True, "q": q, "matches": rows}


def signals(days=14):
    """近 N 天带 hm_tag 的结构信号（follow_confirm/lure_warn），
    探索页默认叙事：M2-W4c 校准成果直接可见。"""
    d0 = _d0(days)
    rows = _q(
        "MATCH (sec:Security)-[r:HAS_STRUCTURE]->(m:MarketEvent) "
        "WHERE m.date >= $d0 AND r.hm_tag IN ['follow_confirm','lure_warn'] "
        "RETURN m.date AS date, sec.ts_code AS tc, sec.name AS nm, "
        "m.type AS type, r.confidence AS conf, r.hm_tag AS tag "
        "ORDER BY date DESC, conf DESC LIMIT 50", d0=d0)
    return {"ok": True, "window_days": days, "signals": rows}


def alt_pairs(days=180, gap=7, min_alt=3):
    """P3-c 共现"交替"模式：席位对同标的先后上榜（非同日、间隔≤gap
    自然日）——接力/轮动手法指纹，与同日共现（协同）互补。

    口径：席位池=kg_communities 成员+watchlist（与 map_graph 一致，
    散户通道已排除）；a.name<b.name 定向防双向重复；ab=a 先 b 后
    次数（接力方向：ab 高=b 接 a 的货）。"""
    pc = _map_pool_and_comm()
    if pc is None:
        return {"ok": False, "error": "kg_communities.json 不存在"}
    _, _, pool, _, _ = pc
    d0 = _d0(days)
    rows = _q(
        "MATCH (s1:Seat)-[t1:TRADED_ON]->(sec:Security)"
        "<-[t2:TRADED_ON]-(s2:Seat) "
        "WHERE t1.validStart < t2.validStart "
        "AND toInteger(t2.validStart) - toInteger(t1.validStart) <= $gap "
        "AND t1.validStart >= $d0 AND s1.name IN $pool AND s2.name IN $pool "
        "WITH s1.name AS lo, s2.name AS hi, sec.ts_code AS tc, "
        "t1.validStart AS d1, t2.validStart AS d2, "
        "CASE WHEN s1.name < s2.name THEN 1 ELSE 0 END AS fwd "
        "WITH (CASE WHEN lo < hi THEN lo ELSE hi END) AS a, "
        "(CASE WHEN lo < hi THEN hi ELSE lo END) AS b, tc, fwd "
        "RETURN a, b, count(*) AS alt, count(DISTINCT tc) AS nsec, "
        "sum(fwd) AS ab, (count(*) - sum(fwd)) AS ba, "
        "collect(DISTINCT tc)[..3] AS tcs "
        "ORDER BY alt DESC LIMIT 20",
        d0=d0, gap=gap, pool=pool)
    rows = [r for r in rows if r["alt"] >= min_alt]
    return {"ok": True, "window_days": days, "gap": gap,
            "pairs": rows}


def day_events(date=None, n_days=5):
    """P3-d TradeDate 一等节点查询面：最近 N 个交易日的全市场事件
    透视（kind × 日期计数）+ 前后导航（NEXT_TRADE_DAY 链）。
    date 缺省=图内最新交易日。"""
    if date is None:
        r = _q("MATCH (d:TradeDate) RETURN max(d.date) AS m")
        if not r or not r[0]["m"]:
            return {"ok": False, "error": "图内无 TradeDate 节点"
                                          "（先跑 kg_tradedate.py）"}
        date = r[0]["m"]
    # 拉明细（日×kind 行）Python 侧聚合——Cypher 里按日分组逐 kind
    # 计数要 collect+reduce 语义绕，明细量小（5 日 × ~1k 行）直接算稳
    rows = _q(
        "MATCH (d:TradeDate) WHERE d.date <= $date "
        "WITH d ORDER BY d.date DESC LIMIT $n "
        "OPTIONAL MATCH (d)<-[:ON_DATE]-(m) "
        "RETURN d.date AS date, d.dow AS dow, m.kind AS kind "
        "ORDER BY date DESC", date=date, n=n_days)
    agg = {}
    for r in rows:
        v = agg.setdefault(r["date"], {"date": r["date"], "dow": r["dow"],
                                       "n": 0, "by_kind": {}})
        v["n"] += 1
        if r["kind"]:
            v["by_kind"][r["kind"]] = v["by_kind"].get(r["kind"], 0) + 1
    days = sorted(agg.values(), key=lambda x: x["date"], reverse=True)
    nav = _q(
        "MATCH (d:TradeDate {date:$date})-[:NEXT_TRADE_DAY]->(n) "
        "OPTIONAL MATCH (d)<-[:NEXT_TRADE_DAY]-(p) "
        "RETURN n.date AS next, p.date AS prev", date=date)
    return {"ok": True, "date": date, "days": days,
            "next": nav[0]["next"] if nav and nav[0]["next"] else None,
            "prev": nav[0]["prev"] if nav and nav[0]["prev"] else None}


# ── K226 画布：跨实体搜索 + 1 跳扩散（人/地/事/物/组织五类） ─────
# label → (key 属性, 五类 category, 展示字段)；TerMINAL 海量/叶子节点
# 只读详情不再扩散（Person 19 万/ServesAs 70 万边防爆炸）。
CANVAS_LABELS = {
    "Security":      ("ts_code", "thing",  ("name", "ts_code", "industry")),
    "Company":       ("name",    "org",    ("name", "industry")),
    "HotMoney":      ("name",    "person", ("name",)),
    "Commentator":   ("name",    "person", ("name",)),
    "Seat":          ("name",    "person", ("name",)),
    "Region":        ("name",    "place",  ("name",)),
    "Theme":         ("name",    "event",  ("name",)),
    "Segment":       ("name",    "thing",  ("name", "chain")),
    "Technique":     ("name",    "thing",  ("name",)),
    "Concept":       ("name",    "org",    ("name",)),
    "Industry":      ("name",    "org",    ("name",)),
    "SwL1Industry":  ("name",    "org",    ("name",)),
    "EXP_Community": ("name",    "org",    ("name",)),
    "Product":       ("name",    "thing",  ("name",)),
    "MarketEvent":   ("evt_id",  "event",  ("type", "date", "price")),
    "Event":         ("id",      "event",  ("text", "ts")),
    "TradeDate":     ("date",    "event",  ("date",)),
}
CANVAS_TERMINAL = {"MarketEvent", "Event", "TradeDate"}
CANVAS_SEARCHABLE = ("Security", "Company", "HotMoney", "Commentator",
                     "Segment", "Theme", "Concept", "EXP_Community",
                     "Industry", "Seat")
# 搜索匹配字段（key 之外补 name 类字段——Security 键是代码，中文名必须可搜）
CANVAS_SEARCH_FIELDS = {
    "Security":      ("ts_code", "name"),
    "Company":       ("name",),
    "HotMoney":      ("name",),
    "Commentator":   ("name",),
    "Segment":       ("name", "chain"),
    "Theme":         ("name",),
    "Concept":       ("name",),
    "EXP_Community": ("name",),
    "Industry":      ("name",),
    "Seat":          ("name",),
}


def canvas_search(q, limit=14):
    """跨实体包含搜索（逐 label 小查询合并，海量 label 不参与）。"""
    q = (q or "").strip()
    if not q:
        return {"ok": True, "hits": []}
    hits = []
    for lbl in CANVAS_SEARCHABLE:
        key, cat, fields = CANVAS_LABELS[lbl]
        sfields = CANVAS_SEARCH_FIELDS.get(lbl, (key,))
        cond = " OR ".join("toString(n.%s) CONTAINS $q" % f
                           for f in sfields)
        if lbl == "Security":
            # K235 二期：曾用名/别名匹配（namechange_sync 入图的 LIST 属性）
            # 空格规范化：中英混排版式差异（TCL 通讯 vs TCL通讯）可容错
            cond += (" OR any(a IN coalesce(n.aliases,[]) WHERE replace("
                     "toString(a),' ','') CONTAINS replace($q,' ',''))")
        rows = _q(
            "MATCH (n:%s) WHERE %s "
            "RETURN n.%s AS k, %s LIMIT 5"
            % (lbl, cond, key,
               ", ".join("n.%s AS f%d" % (f, i)
                         for i, f in enumerate(fields))), q=q)
        for r in rows:
            hits.append({
                "id": "%s|%s" % (lbl, r["k"]), "label": lbl,
                "category": cat,
                "name": str(r["k"]),
                "fields": {f: r.get("f%d" % i)
                           for i, f in enumerate(fields)},
            })
    hits.sort(key=lambda h: (len(h["name"]), h["label"]))
    return {"ok": True, "hits": hits[:limit]}


def canvas_expand(arg):
    """1 跳扩散：arg="Label|key" → 中心属性 + 邻居（按关系类型均衡采样）。"""
    try:
        lbl, key = (arg or "").split("|", 1)
    except ValueError:
        return {"ok": False, "error": "arg 须为 Label|key"}
    if lbl not in CANVAS_LABELS:
        return {"ok": False, "error": "未知 label %s" % lbl}
    keyp, cat, fields = CANVAS_LABELS[lbl]
    ctr = _q("MATCH (n:%s {%s:$k}) RETURN properties(n) AS p LIMIT 1"
             % (lbl, keyp), k=key)
    if not ctr:
        return {"ok": False, "error": "节点不存在 %s" % arg}
    cp = ctr[0]["p"] or {}
    center = {"id": arg, "label": lbl, "category": cat,
              "name": str(cp.get(fields[0], key)),
              "fields": {f: cp.get(f) for f in fields}}
    if lbl in CANVAS_TERMINAL:
        return {"ok": True, "center": center, "nodes": [], "edges": [],
                "terminal": True}
    tdist = _q(
        "MATCH (n:%s {%s:$k})-[r]-(m) "
        "RETURN type(r) AS t, count(*) AS c ORDER BY c DESC LIMIT 40"
        % (lbl, keyp), k=key)
    # K240：语义关系优先于高频事件关系（否则 HAS_PATTERN/SIMILAR_K 等
    # 高频类型霸榜，SUPPLIES_TO/ASSESSED 等低频语义边永远进不了采样）
    PRIO = ("SUPPLIES_TO", "IN_GROUP", "OCCUPIES", "SERVES_AS",
            "CONDUCTION", "SUB_OF", "OF_COMPANY", "ASSESSED",
            "CORRELATES_WITH", "PRODUCES", "HAS_STRUCTURE", "DRIVE",
            "REQUIRES", "MAPS_TO", "BELONGS_TO", "SIMILAR_K")
    prio = {t: i for i, t in enumerate(PRIO)}
    tlist = sorted(
        [x for x in tdist],
        key=lambda x: (prio.get(x["t"], 99), -x["c"]))[:8]
    nodes, edges, seen = {}, [], {arg}
    for td in tlist:
        rel = td["t"]
        rows = _q(
            "MATCH (n:%s {%s:$k})-[r:%s]-(m) "
            "RETURN labels(m)[0] AS ml, properties(m) AS p LIMIT 6"
            % (lbl, keyp, rel), k=key)
        for r in rows:
            ml = r["ml"]
            if ml not in CANVAS_LABELS:
                continue
            mk, mcat, mfields = CANVAS_LABELS[ml]
            p = r["p"] or {}
            mk = p.get(mk)
            if mk is None:
                continue
            mid = "%s|%s" % (ml, mk)
            if mid in seen:
                continue
            seen.add(mid)
            nodes[mid] = {
                "id": mid, "label": ml, "category": mcat,
                "terminal": ml in CANVAS_TERMINAL,
                "name": str(p.get(mfields[0], mk))[:24],
                "fields": {f: p.get(f) for f in mfields},
            }
            edges.append({"s": arg, "t": mid, "type": rel})
    return {"ok": True, "center": center,
            "nodes": list(nodes.values()), "edges": edges}


# ── K231 画布编辑器：人工建实体/补属性/连边（写图 + 审计留痕） ─────
# 画布从只读探索升级读写编辑：右键节点 → 编辑属性（表单+同类直方图参照）
# → 连边（关系类型白名单）。所有写入 upsert 幂等 + canvas_edit_log.jsonl
# 审计（时间/op/负载），出问题可按日志回溯。
CANVAS_RELS = ("SUPPLIES_TO", "IN_GROUP", "OCCUPIES", "SERVES_AS",
               "CONDUCTION", "SUB_OF", "CORRELATES_WITH", "PRODUCES")
CANVAS_EDIT_LOG = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "data", "canvas_edit_log.jsonl")
_FIELD_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,30}$")


def _canvas_audit(op, payload):
    """编辑审计：append jsonl（失败不影响主流程，仅 print）。"""
    try:
        rec = {"ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "op": op, "payload": payload}
        with open(CANVAS_EDIT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        print("canvas audit fail: %s" % str(e)[:80])


def canvas_save(label, key, props):
    """创建/更新实体（upsert 幂等）：label 白名单 + key 必填 + 属性自由 kv。"""
    if label not in CANVAS_LABELS:
        return {"ok": False, "error": "label %s 不在白名单" % label}
    keyp = CANVAS_LABELS[label][0]
    key = str(key or "").strip()
    if not key:
        return {"ok": False, "error": "key 必填"}
    props = {str(k)[:40]: ("" if v is None else v)
             for k, v in (props or {}).items()
             if _FIELD_RE.match(str(k or ""))}
    props[keyp] = key
    cli = _client()
    n = cli.upsert_nodes(label, [keyp], [props])
    _canvas_audit("save", {"label": label, "key": key, "props": props})
    return {"ok": True, "written": n, "id": "%s|%s" % (label, key)}


def canvas_link(src, dst, rel, props=None):
    """两实体间连边（rel 白名单 + 时序三属性 + 审计）。src/dst="Label|key"。"""
    if rel not in CANVAS_RELS:
        return {"ok": False, "error": "关系 %s 不在白名单" % rel}
    out = {}
    for tag, ref in (("src", src), ("dst", dst)):
        try:
            lb, k = ref.split("|", 1)
        except ValueError:
            return {"ok": False, "error": "%s 须为 Label|key" % tag}
        if lb not in CANVAS_LABELS:
            return {"ok": False, "error": "%s label %s 不在白名单"
                    % (tag, lb)}
        out[tag] = (lb, CANVAS_LABELS[lb][0], k)
    (sl, sk, skey), (dl, dk, dkey) = out["src"], out["dst"]
    edge = {"src": skey, "dst": dkey,
            "validStart": datetime.now().strftime("%Y-%m-%d"),
            "validEnd": S.VALID_END_OPEN, "source": "canvas_manual"}
    edge.update({str(k)[:40]: ("" if v is None else v)
                 for k, v in (props or {}).items()
                 if _FIELD_RE.match(str(k or "")) and k not in edge})
    cli = _client()
    n = cli.upsert_edge(sl, sk, dl, dk, rel, [edge])
    _canvas_audit("link", {"rel": rel, "src": src, "dst": dst,
                           "props": props or {}})
    return {"ok": True, "written": n,
            "edge": "%s -[%s]-> %s" % (src, rel, dst)}


def canvas_hist(label, field, sample=5000):
    """同类属性值分布直方图（top8）：编辑面板参照「别的节点这个字段都填什么」。"""
    if label not in CANVAS_LABELS or not _FIELD_RE.match(field or ""):
        return {"ok": False, "error": "label/field 非法"}
    rows = _q("MATCH (n:%s) WHERE n.%s IS NOT NULL "
              "RETURN toString(n.%s) AS v LIMIT %d"
              % (label, field, field, sample))
    from collections import Counter
    cnt = Counter(r["v"] for r in rows if r["v"])
    return {"ok": True, "label": label, "field": field,
            "n_sampled": len(rows),
            "top": [{"v": v, "n": n} for v, n in
                    cnt.most_common(8)]}


# ── K240 四期：图原生问答 /kg/ask（实体链接→子图检索→事实注入生成）──
ASK_PROMPT = (
    "你是金融图谱问答引擎。下面是已检索到的图谱事实（逐条列出），"
    "请用 2-3 句中文直接回答用户问题；只能引用事实行内容；"
    "信号保留类型原文（如 3卖/2买）。\n"
    "用户问题：%s\n已知事实：\n%s\n"
    "只输出回答正文。"
)


def _fallback_answer(facts):
    """模板兜底：LLM 拒答时直接用图事实成文（图问答=结构化展示）。"""
    lines = []
    for f in facts:
        nb = f.get("neighbors") or []
        segs = [n["name"] for n in nb if n["via"] == "SUPPLIES_TO"]
        cons = [n["name"] for n in nb if n["via"] == "ASSESSED"]
        sigs = sorted({n["name"] for n in nb
                       if n["via"] == "HAS_STRUCTURE"})
        grp = [n["name"] for n in nb if n["via"] == "IN_GROUP"]
        parts = []
        if segs:
            parts.append("传导环节：" + "、".join(segs))
        if cons:
            parts.append("派系共识：" + "、".join(cons))
        if sigs:
            parts.append("近期信号：" + "、".join(sigs))
        if grp:
            parts.append("所属集团：" + "、".join(grp))
        if parts:
            lines.append(f["entity"] + "：" + "；".join(parts))
    return "  ".join(lines) if lines else "图谱中暂无相关信息。"


def _name_dictionary():
    """图内实体名词典（label|key|name 三元组，问句包含匹配用）。"""
    out = []
    for lbl in ("Security", "Company", "HotMoney", "Segment", "Theme",
                "Concept", "Commentator", "EXP_Community"):
        key, cat, fields = CANVAS_LABELS[lbl]
        nmf = fields[0]
        for r in _q("MATCH (n:%s) WHERE n.%s IS NOT NULL "
                    "RETURN n.%s AS k, n.%s AS nm"
                    % (lbl, key, key, nmf)):
            if r["nm"]:
                out.append({"id": "%s|%s" % (lbl, r["k"]),
                            "label": lbl, "cat": cat, "name": r["nm"]})
    return out


def canvas_ask(q):
    """图原生问答：实体链接 → 子图事实 → Ollama 生成 + 溯源。"""
    import requests as _rq
    from kg_digest import OLLAMA, MODEL, _ollama_ok
    q = (q or "").strip()
    if not q:
        return {"ok": False, "error": "问题为空"}
    if not _ollama_ok():
        return {"ok": False, "error": "Ollama 不可达"}
    # 实体链接：问句包含实体名（长名优先，最多 4 个实体入上下文）
    dict_hits = [d for d in _name_dictionary() if d["name"] and
                 d["name"] in q]
    dict_hits.sort(key=lambda d: -len(d["name"]))
    if not dict_hits:
        return {"ok": True, "answer": "图谱中未检索到相关实体。",
                "sources": []}
    seen, chosen = set(), []
    for d in dict_hits:
        if d["id"] in seen:
            continue
        seen.add(d["id"])
        chosen.append(d)
        if len(chosen) >= 4:
            break
    facts, sources = [], []
    NOISE_RELS = ("HAS_PATTERN", "HAS_CANDLE", "ON_DATE", "USES_TECH",
                  "TRADED_ON", "HAS_VOL_EVENT")
    for d in chosen:
        ex = canvas_expand(d["id"])
        if not ex.get("ok"):
            continue
        c = ex["center"]
        nb = [{"name": n["name"], "label": n["label"], "via": e["type"]}
              for n, e in zip(ex["nodes"], ex["edges"])
              if e["type"] not in NOISE_RELS][:8]
        facts.append({"entity": c["name"], "label": c["label"],
                      "props": {k: v for k, v in (c.get("fields")
                                                  or {}).items() if v},
                      "neighbors": nb})
        sources.append({"id": d["id"], "name": c["name"]})
    if not facts:
        return {"ok": True, "answer": "图谱中暂无相关信息。", "sources": []}
    fact_lines = []
    for f in facts:
        for n in f.get("neighbors") or []:
            fact_lines.append("%s -[%s]-> %s" % (f["entity"], n["via"],
                                                 n["name"]))
        for k, v in (f.get("props") or {}).items():
            fact_lines.append("%s 的%s = %s" % (f["entity"], k, v))
    answer, used = "", []
    try:
        body = {"model": MODEL, "stream": False, "think": False,
                "messages": [{"role": "user",
                              "content": ASK_PROMPT % (
                                  q, "\n".join(fact_lines))}],
                "options": {"temperature": 0.3, "num_predict": 300}}
        r = _rq.post(OLLAMA, json=body, timeout=120)
        r.raise_for_status()
        answer = re.sub(r"\s+", " ",
                        r.json()["message"]["content"]).strip()
        answer = answer.strip('"')
    except Exception as e:
        answer = ""
        err = str(e)[:80]
    if not answer or "暂无" in answer or "没有" in answer[:12]:
        answer = _fallback_answer(facts) + "（图事实直读）"
    return {"ok": True, "answer": answer, "sources": sources,
            "model": MODEL}


# ── /kg/battle 作战地图（K262）：个股事件·信号·资金·股价时序压实 ──
def _d8x(s):
    """任意日期（ISO/横杠/整数/NaN）→ YYYYMMDD 字符串，坏值回空。"""
    s = str(s or "")[:10].replace("-", "")
    return s if s.isdigit() else ""


def _chanlun_signals(code, d0):
    """chanlun_pool.jsonl → 该股窗口内信号行（dt=信号发生日）。"""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "data", "chanlun_pool.jsonl")
    out = []
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("code") != code:
                continue
            if _d8x(r.get("dt")) < d0:
                continue
            out.append(r)
    out.sort(key=lambda r: r.get("dt") or "")
    return out


def _battle_rules(sigs, tops, flows, notices):
    """三条研究规则（作战地图验证命题）Python 侧统计。

    R1 公告前3日缠论买点（前置验证：出公告时图形是否已具备买入条件）
    R2 上榜前5日缠论买点（前置验证：游资上榜前图形是否已具买入条件）
    R3 大额净流出后20日买点/背驰（后置验证：资金出逃后的修复/转折）
    """
    from datetime import datetime as _dt

    def _to_d(s):
        try:
            return _dt.strptime(str(s), "%Y%m%d").date()
        except Exception:
            return None

    buy_days, bei_days = {}, set()
    for x in sigs:
        d = _d8x(x.get("dt"))
        if not d:
            continue
        if "买" in (x.get("type") or ""):
            buy_days[d] = x
        if any(x.get(k) for k in ("bei", "bei2", "mbei", "tbe")):
            bei_days.add(d)

    def _win(anchor, days_map, lo, hi):
        """[anchor-lo, anchor+hi] 日历日窗内命中（近似交易日窗）。"""
        a = _to_d(anchor)
        if not a:
            return []
        out = []
        for d in days_map:
            dd = _to_d(d)
            if dd and abs((a - dd).days) <= 40 and -lo <= (a - dd).days <= hi:
                out.append(d)
        return sorted(out)

    r1_hit = []
    for n in notices:
        m = _win(n["date"], buy_days, 3, 0)
        if m:
            r1_hit.append({"date": n["date"], "tag": n["tag"],
                           "buy": m[-1]})
    r2_hit = []
    for t in tops:
        m = _win(t["date"], buy_days, 5, 0)
        if m:
            r2_hit.append({"date": t["date"], "reason": t.get("reason"),
                           "buy": m[-1]})
    # 大额出逃：单日净流出超 5000 万
    flees = [f for f in flows if f["net"] < -5000]
    r3_hit = []
    for f in flees:
        m = _win(f["date"], {**buy_days, **{d: None for d in bei_days}}, 0, 20)
        if m:
            r3_hit.append({"date": f["date"], "net": f["net"], "after": m[-1]})

    def _rate(h, t):
        return round(len(h) / t, 2) if t else None

    return [
        {"rule": "公告前3日缠论买点", "total": len(notices),
         "hits": len(r1_hit), "hit_rate": _rate(r1_hit, len(notices)),
         "samples": r1_hit[:5]},
        {"rule": "上榜前5日缠论买点", "total": len(tops),
         "hits": len(r2_hit), "hit_rate": _rate(r2_hit, len(tops)),
         "samples": r2_hit[:5]},
        {"rule": "大额净流出后20日买点/背驰", "total": len(flees),
         "hits": len(r3_hit), "hit_rate": _rate(r3_hit, len(flees)),
         "samples": r3_hit[:5]},
    ]


def battle_timeline(code, days=250):
    """个股作战时间轴：五图层按 YYYYMMDD 压平 + 三条研究规则统计。

    图层 layer∈{缠论,游资,资金,公告,形态}；dir∈{bull,bear,neutral}；
    各源互不阻断（datamart/event_graph/图任一失败跳过该层）。
    """
    d0 = _d0(days)
    layers = []
    # ① 缠论：池内信号
    sigs = _chanlun_signals(code, d0)
    for r in sigs:
        d = _d8x(r.get("dt"))
        flags = [k for k in ("bei", "bei2", "mbei", "tbe") if r.get(k)]
        dirn = "bull" if "买" in (r.get("type") or "") else "bear"
        layers.append({"date": d, "layer": "缠论", "kind": r.get("type"),
                       "label": r.get("type") + (("·" + "+".join(flags))
                                                 if flags else ""),
                       "dir": dirn, "value": r.get("price"),
                       "meta": {"reg": r.get("reg"),
                                "entry_px": r.get("entry_px"),
                                "detect_date": r.get("detect_date")}})
    # ② 游资：龙虎榜上榜（ts_top_list）
    tops = []
    try:
        import datamart
        df = datamart.read("ts_top_list", start=d0, codes=[code])
        for r in df.to_dict("records"):
            d = _d8x(r.get("trade_date"))
            net = r.get("net_amount")
            net = None if net is None or pd.isna(net) else float(net)
            tops.append({"date": d, "reason": r.get("reason"), "net": net})
            layers.append({"date": d, "layer": "游资", "kind": "上榜",
                           "label": str(r.get("reason") or "")[:24],
                           "dir": "bull" if (net or 0) > 0 else "bear",
                           "value": net, "meta": {"reason": r.get("reason")}})
    except Exception:
        pass
    # ③ 资金：ts_moneyflow 净流入（万元）
    flows = []
    try:
        import datamart
        df = datamart.read("ts_moneyflow", start=d0, codes=[code],
                           fields=["ts_code", "trade_date", "net_mf_amount"])
        for r in df.to_dict("records"):
            d = _d8x(r.get("trade_date"))
            v = r.get("net_mf_amount")
            if v is None or pd.isna(v):
                continue
            v = float(v)
            flows.append({"date": d, "net": v})
            layers.append({"date": d, "layer": "资金",
                           "kind": "净流入" if v > 0 else "净流出",
                           "label": "%.0f万" % v,
                           "dir": "bull" if v > 0 else "bear",
                           "value": v, "meta": {}})
    except Exception:
        pass
    # ④ 公告：events_for_stock（K260 已回填 bias/tag；中性不占图层）
    notices = []
    try:
        import event_graph
        ev = event_graph.events_for_stock(code, days=days)
        for e in ev.get("events", []):
            if e.get("tag") in (None, "", "中性披露"):
                continue
            d = _d8x(e.get("ts"))
            notices.append({"date": d, "tag": e.get("tag"),
                            "bias": e.get("bias")})
            layers.append({"date": d, "layer": "公告",
                           "kind": e.get("tag") or "事件",
                           "label": str(e.get("text") or "")[:36],
                           "dir": e.get("bias") or "neutral",
                           "value": e.get("conf"),
                           "meta": {"reason": e.get("reason"),
                                    "source": e.get("source")}})
    except Exception:
        pass
    # ⑤ 形态：图内涨跌停 + 榜后差额 + 结构（图不可达整层跳过）
    try:
        lm = _q("MATCH (s:Security {ts_code:$c})-[:HAS_LIMIT]->(m:MarketEvent) "
                "WHERE m.date >= $d0 RETURN m.date AS d, m.limit_times AS lt, "
                "m.pct_chg AS pct, m.limit_type AS ltp ORDER BY d LIMIT 60",
                c=code, d0=d0)
        for r in lm:
            up = (r.get("ltp") or "U") != "D"
            layers.append({"date": str(r["d"] or ""), "layer": "形态",
                           "kind": "涨停" if up else "跌停",
                           "label": ("%s连板" % r["lt"]) if r.get("lt") else "",
                           "dir": "bull" if up else "bear",
                           "value": r.get("pct"), "meta": {}})
        tg = _q("MATCH (s:Security {ts_code:$c})-[:HAS_TOPGAP]->(m:MarketEvent) "
                "WHERE m.date >= $d0 RETURN m.date AS d, m.gap AS gap, "
                "m.reason AS reason ORDER BY d LIMIT 60", c=code, d0=d0)
        for r in tg:
            g = r.get("gap")
            g = None if g is None or pd.isna(g) else float(g)
            layers.append({"date": str(r["d"] or ""), "layer": "游资",
                           "kind": "榜后差额",
                           "label": str(r.get("reason") or "")[:20],
                           "dir": "bull" if (g or 0) > 0 else "bear",
                           "value": g, "meta": {}})
    except Exception:
        pass
    layers.sort(key=lambda x: x["date"])
    rules = _battle_rules(sigs, tops, flows, notices)
    return {"ok": True, "code": code, "window_days": days,
            "n_layers": len(layers), "layers": layers, "rules": rules}


def battle_batch(codes, days=120):
    """批量体检：一批股票逐只 battle_timeline → 问题分级（红黄绿）。

    severity: high=红（资金持续流出/出逃无修复） mid=黄（背驰/利空公告）
    无 problems=绿。上限 50 只防接口打爆。
    """
    items = []
    for c in [c.strip() for c in codes if c.strip()][:50]:
        try:
            t = battle_timeline(c, days)
        except Exception as e:
            items.append({"code": c, "ok": False,
                          "error": str(e)[:80], "problems": []})
            continue
        problems = []
        rules = {r["rule"]: r for r in t.get("rules", [])}
        fl = [l for l in t["layers"] if l["layer"] == "资金"]
        outs = [l for l in fl if l["kind"] == "净流出"]
        if fl and len(outs) / len(fl) > 0.6:
            problems.append({
                "severity": "high", "kind": "资金持续净流出",
                "text": "窗口内 %.0f%% 记录为净流出" % (100 * len(outs) / len(fl)),
                "date": outs[-1]["date"] if outs else ""})
        r3 = rules.get("大额净流出后20日买点/背驰")
        if r3 and r3.get("total") and not r3.get("hits"):
            problems.append({
                "severity": "high", "kind": "出逃后无修复信号",
                "text": "%d 次大额净流出后 20 日内无买点/背驰" % r3["total"],
                "date": ""})
        bei = [l for l in t["layers"]
               if l["layer"] == "缠论" and "·" in (l["label"] or "")]
        if bei:
            problems.append({
                "severity": "mid", "kind": "缠论背驰",
                "text": "窗口内 %d 次背驰标记（最近 %s）"
                        % (len(bei), bei[-1]["date"]),
                "date": bei[-1]["date"]})
        nt = [l for l in t["layers"]
              if l["layer"] == "公告" and l["dir"] == "bear"]
        if nt:
            problems.append({
                "severity": "mid", "kind": "利空公告",
                "text": "窗口内 %d 条利空公告（最近 %s %s）"
                        % (len(nt), nt[-1]["date"], nt[-1]["kind"]),
                "date": nt[-1]["date"]})
        items.append({"code": c, "ok": True,
                      "n_layers": t["n_layers"], "problems": problems})
    sev = {"high": 0, "mid": 0}
    for it in items:
        for p in it.get("problems", []):
            sev[p["severity"]] = sev.get(p["severity"], 0) + 1
    return {"ok": True, "n": len(items),
            "summary": {"high": sev["high"], "mid": sev["mid"]},
            "items": items}


# ── K262 战绩层：四源聚合（单位统一 pct）─────────────────
def _agg_rets(rets):
    """rets(list[pct]) → {n, win, avg}；空组 win/avg=None。"""
    if not rets:
        return {"n": 0, "win": None, "avg": None}
    return {"n": len(rets),
            "win": round(100.0 * sum(1 for r in rets if r > 0) / len(rets), 1),
            "avg": round(sum(rets) / len(rets), 2)}


def _records_chanlun(here):
    """层1 缠论池：模拟持有 N 日的类型×侧别×复合战绩（pct）。"""
    p = os.path.join(here, "data", "chanlun_pool_eval_cache.json")
    d = json.load(open(p, encoding="utf-8"))
    rows = []
    for t in ("1买", "2买", "3买", "1卖", "2卖", "3卖"):
        it = (d.get("types") or {}).get(t) or {}
        a = it.get("all") or {}
        if not a.get("n"):
            continue
        extra = []
        for s, sv in sorted((it.get("sides") or {}).items()):
            if sv and sv.get("n", 0) >= 10:      # 影子纪律：n<10 不展示
                extra.append("%s n%d w%.1f a%+.2f"
                             % (s, sv["n"], sv["win"], sv["avg"]))
        for c in it.get("comp") or []:
            st = c.get("stats")
            if st and st.get("n", 0) >= 30:
                extra.append("%s n%d w%.1f a%+.2f"
                             % (c.get("name") or c.get("id"), st["n"],
                                st["win"], st["avg"]))
        rows.append({"group": t, "n": a["n"], "win": a.get("win"),
                     "avg_pct": a.get("avg"),
                     "extra": (" · ".join(extra)[:300] or None)})
    return {"layer": "缠论池(模拟)",
            "note": "持有 %s 日 · 池 %s 只 · 生成 %s"
                    % (d.get("hold_days"), d.get("n_pool"),
                       (d.get("generated_at") or "")[:10]),
            "rows": rows}


def _records_sellpoint(here):
    """层2 卖点锦标赛：bias 回填按 agent×type 聚合（real_ret pct）。"""
    agg, bytype = {}, {}
    with open(os.path.join(here, "data", "sellpoint_bias.jsonl"),
              encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get("real_ret") is None:
                continue
            ag = r.get("agent_name") or "?"
            v = float(r["real_ret"])
            agg.setdefault(ag, []).append(v)
            bytype.setdefault((ag, r.get("type") or "?"), []).append(v)
    rows = []
    for ag, rets in sorted(agg.items()):
        st = _agg_rets(rets)
        tp = ["%s n%d w%s a%s" % (t, s2["n"], s2["win"], s2["avg"])
              for (a2, t), tr in sorted(bytype.items()) if a2 == ag
              for s2 in [_agg_rets(tr)]]
        rows.append({"group": ag, "n": st["n"], "win": st["win"],
                     "avg_pct": st["avg"],
                     "extra": (" · ".join(tp)[:300] or None)})
    return {"layer": "卖点锦标赛(2-agent)",
            "note": "已回填 real_ret 的卖点卡按 agent 聚合（负值=卖出规避亏损）",
            "rows": rows}


def _records_jev(here):
    """层3 JEV 复核门：影子三元组按 decision 聚合（ret_nd×100 → pct）。"""
    p = os.path.normpath(os.path.join(here, "..", "jev-eval",
                                      "review_data", "triples.jsonl"))
    agg = {}
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get("status") != "reviewed" or r.get("ret_nd") is None:
                continue
            agg.setdefault(r.get("decision") or "?", []) \
               .append(float(r["ret_nd"]) * 100.0)
    rows = [{"group": dec, "n": s["n"], "win": s["win"], "avg_pct": s["avg"],
             "extra": None}
            for dec, rets in sorted(agg.items()) for s in [_agg_rets(rets)]]
    note = "影子模式（只标注不拦截）· ret_nd×100 统一口径"
    fa, pa = agg.get("FOCUS"), agg.get("PASS")
    if fa and pa:
        diff = sum(fa) / len(fa) - sum(pa) / len(pa)
        note += " · FOCUS−PASS 收益差 %+.2f pct (n=%d/%d)" \
                % (diff, len(fa), len(fa) + len(pa))
    return {"layer": "JEV复核门(影子)", "note": note, "rows": rows}


def _records_riskguard(here):
    """层4 RiskGuard：影子账本按 rule_id 聚合（pass/影子拦截计数）。"""
    base = os.path.normpath(os.path.join(here, "..", "..",
                                         "KGStockAnalysis"))
    agg = {}
    with open(os.path.join(base, "riskguard_ledger", "ledger.jsonl"),
              encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            a = agg.setdefault(r.get("rule_id") or "?",
                               {"pass": 0, "block": 0, "ts": ""})
            if "block" in (r.get("action") or ""):
                a["block"] += 1
            else:
                a["pass"] += 1
            if (r.get("ts") or "") > a["ts"]:
                a["ts"] = r.get("ts") or ""
    rows = [{"group": rid, "n": a["pass"] + a["block"], "win": None,
             "avg_pct": None,
             "extra": "pass %d · 影子拦截 %d · 最新 %s"
                      % (a["pass"], a["block"], a["ts"][:16])}
            for rid, a in sorted(agg.items())]
    note = "影子账本（只记录不拦截）"
    try:
        with open(os.path.join(base, "params.yaml"), encoding="utf-8") as f:
            m = re.search(r"^\s*riskguard_vol_mult:\s*([0-9.]+)",
                          f.read(), re.M)
        if m:
            note += " · R2 已拍板毕业 vol_mult=%s" % m.group(1)
    except Exception:
        pass
    return {"layer": "RiskGuard(影子)", "note": note, "rows": rows}


def battle_records():
    """K262 作战地图·战绩层：缠论池/卖点锦标赛/JEV/RiskGuard 四源聚合。

    单位统一 pct；逐源 try/except（缺数据出 error 行不拖垮整层）。
    """
    here = os.path.dirname(os.path.abspath(__file__))
    layers = []
    for name, fn in (("缠论池(模拟)", _records_chanlun),
                     ("卖点锦标赛(2-agent)", _records_sellpoint),
                     ("JEV复核门(影子)", _records_jev),
                     ("RiskGuard(影子)", _records_riskguard)):
        try:
            layers.append(fn(here))
        except Exception as e:
            layers.append({"layer": name, "error": str(e)[:120],
                           "rows": []})
    return {"ok": True,
            "as_of": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "layers": layers}


# ── 统一入口（门户路由调这里）─────────────────────────────
def api(kind, arg=None, days=None, per=None):
    try:
        if kind == "stats":
            return stats()
        if kind == "ontology":
            # L1 本体注册表×图内实况：元数据+计数+保鲜+漂移对账
            import ontology
            return ontology.snapshot()
        if kind == "objectview":
            # L2 Object View 装配引擎：声明式扇区体检（新鲜度/评级徽章）
            import kg_objectview
            r = kg_objectview.view(arg or "", int(days or 120))
            if isinstance(r, dict):
                r.pop("_stock", None)   # JSON 端点不带原始行集（可走 stock）
            return r
        if kind == "stock":
            return stock(arg, int(days or 120))
        if kind == "company360":
            return company_360(arg, int(days or 90), per=per)
        if kind == "hotmoney":
            return hotmoney(arg, int(days or 180))
        if kind == "trace":
            return trace(arg, int(days or 30))
        if kind == "lookup":
            return lookup(arg or "")
        if kind == "signals":
            return signals(int(days or 14))
        if kind == "map":
            return hotmoney_map(int(days or 90))
        if kind == "geo":
            return geo(int(days or 90))
        if kind == "altpairs":
            return alt_pairs(int(days or 180))
        if kind == "tradecal":
            return day_events(arg, int(days or 5))
        if kind == "mapgraph":
            return map_graph()
        if kind == "overview":
            return map_overview()
        if kind == "profile":
            return hm_profile(arg or "", int(days or 365))
        if kind == "shadow":
            return shadow(int(days or 60))
        if kind == "distill":
            return distill_info()
        if kind == "communities":
            import kg_communities
            return kg_communities.stats()
        if kind == "sources":
            return sources()
        if kind == "csearch":
            return canvas_search(arg or "")
        if kind == "cexpand":
            return canvas_expand(arg or "")
        if kind == "chist":
            # arg = "Label|field"
            try:
                lb, fd = (arg or "").split("|", 1)
            except ValueError:
                return {"ok": False, "error": "arg 须为 Label|field"}
            return canvas_hist(lb, fd)
        if kind == "gx":
            # K261 L2 图分析：arg = "sub:a[,b]"（sub∈path/shared/
            # community/centrality）；days 位复用为 hops，per 位为 topN
            import kg_analytics
            sub, _, rest = (arg or "").partition(":")
            parts = [p for p in (rest or "").split(",") if p]
            hops = int(days) if days else None
            top = int(per) if per else None
            return kg_analytics.run(sub, parts[0] if parts else None,
                                    parts[1] if len(parts) > 1 else None,
                                    hops=hops, top=top)
        if kind == "guba":
            # K263 股吧舆论（词云+情绪+事件契合；数据需先 guba_crawl 采集）
            # K263 二期②：per 位复用为 word → 词反查帖子列表
            import guba_sentiment
            return guba_sentiment.guba_summary(arg, int(days or 30),
                                               word=per)
        if kind == "timeline":
            return battle_timeline(arg, int(days or 250))
        if kind == "batch":
            # arg = "code1,code2,..."（数据集导入批量体检）
            return battle_batch((arg or "").split(","),
                                int(days or 120))
        if kind == "records":
            # K262 战绩层：缠论池/卖点/JEV/RiskGuard 四源聚合
            return battle_records()
        if kind == "cask":
            return canvas_ask(arg or "")
        return {"ok": False, "error": "未知 kind %s" % kind}
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)[:200]}


if __name__ == "__main__":
    args = sys.argv[1:]
    kind = args[0] if args else "stats"
    a = args[1] if len(args) > 1 else None
    dd = next((args[i + 1] for i, x in enumerate(args) if x == "--days"),
              None)
    print(json.dumps(api(kind, a, dd), ensure_ascii=False, indent=1))
    if _cli:
        _cli.close()
