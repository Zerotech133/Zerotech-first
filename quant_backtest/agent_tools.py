# -*- coding: utf-8 -*-
"""P-A 多智能体工具层：把既有能力包成统一工具接口（纯 python，零框架）。

设计铁律（对齐项目记忆 Agno 路线 P-A）：
  · 每个工具 = 纯函数，json 可序列化出参，异常兜底 {"ok": False, "err": ...}
  · 重型依赖（fastembed/pandas/pyarrow）全部函数内惰性导入——import 本模块
    不触发任何模型加载，门户/CLI 安全
  · 只读不写：所有工具为信息层，不改信号/推送触发/拦截口径（P-B 才接线）
  · agent 无关：Agno/自研编排/CLI 都直接调 call(name, **kw)

用法：
  python agent_tools.py list                     # 工具清单（名称+说明+参数）
  python agent_tools.py call tgraph_slots '{"sig_type":"1卖","tbe":true}'
  python agent_tools.py call events_for_stock '{"code":"688411","days":30}'
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def _norm_code(code):
    """6 位裸代码 → 尽力补交易所后缀（6→SH，0/3→SZ，4/8→BJ）。"""
    s = str(code or "").strip().upper()
    if "." in s:
        return s
    if len(s) == 6 and s.isdigit():
        suf = "SH" if s[0] == "6" else "BJ" if s[0] in "48" else "SZ"
        return "%s.%s" % (s, suf)
    return s


_NAME_MAP = None   # 进程内缓存：code(带后缀) → 股票名称（chanlun_pool.jsonl）
_STOCK_NAMES = None  # 进程内缓存：全市场名称映射（data/stock_names.json）


def _name_map():
    """加载池内 code→name 映射（全池首次加载后进程内复用）。"""
    global _NAME_MAP
    if _NAME_MAP is None:
        m = {}
        path = os.path.join(HERE, "data", "chanlun_pool.jsonl")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    try:
                        r = json.loads(line)
                    except Exception:
                        continue
                    c, n = r.get("code"), r.get("name")
                    if c and n:
                        m[str(c).upper()] = n
        _NAME_MAP = m
    return _NAME_MAP


def _stock_names():
    """全市场名称映射（现用名+曾用名，fetch_stock_names.py 生成）。

    结构：code2name / name2code / alias_of（曾用名→现用名）。
    json 缺失时回落 datamart stock_basic 动态建现用名映射（无曾用名，
    保证映射表未生成时机器人仍具备基本反查能力）。"""
    global _STOCK_NAMES
    if _STOCK_NAMES is None:
        sn = {}
        path = os.path.join(HERE, "data", "stock_names.json")
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    sn = json.load(f)
            except Exception:
                sn = {}
        if not sn.get("name2code"):
            try:
                import datamart as dm
                df = dm.read("stock_basic")
                c2n, n2c = {}, {}
                for c, n in zip(df["ts_code"].astype(str).str.upper(),
                                df["name"].astype(str).str.strip()):
                    if c and n and n.lower() != "nan":
                        c2n[c] = n
                        n2c.setdefault(n, c)
                sn = {"code2name": c2n, "name2code": n2c,
                      "alias_of": {}}
            except Exception:
                sn = {}
        _STOCK_NAMES = sn
    return _STOCK_NAMES


def _fix_codes(codes):
    """codes 参数归一（防 300419.SH 型后缀错配致 n=0）：
    str/逗号分隔串 → list；后缀纠错仅限首位即可判定的情形（3开头只在深市、
    6开头只在沪市；0开头沪市存在指数如 000001.SH，不动）；6 位裸码补后缀。"""
    if codes is None:
        return None
    if isinstance(codes, str):
        codes = re.split(r"[,;，、\s]+", codes)
    out = []
    for c in codes or []:
        s = str(c).strip().upper()
        if not s:
            continue
        if "." in s:
            head, suf = s.split(".", 1)
            if len(head) == 6 and head.isdigit() and suf in ("SH", "SZ", "BJ"):
                if head[0] == "3":
                    s = "%s.SZ" % head
                elif head[0] == "6":
                    s = "%s.SH" % head
        else:
            s = _norm_code(s)
        out.append(s)
    return out


def _jsonable(v):
    """递归转 json 安全类型（numpy/datetime/set/tuple → 原生）。"""
    import math
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, (list, tuple, set)):
        return [_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if hasattr(v, "isoformat"):
        return str(v)
    if hasattr(v, "item"):          # numpy 标量
        return _jsonable(v.item())
    if hasattr(v, "tolist"):        # numpy 数组
        return _jsonable(v.tolist())
    return str(v)


def tool(fn):
    """工具包装：异常兜底 + jsonable + ok 标记。"""
    def wrap(*a, **kw):
        try:
            out = fn(*a, **kw)
            if isinstance(out, dict) and "ok" in out:
                return _jsonable(out)
            return _jsonable({"ok": True, "data": out})
        except Exception as e:
            return {"ok": False, "err": "%s: %s" % (type(e).__name__,
                                                    str(e)[:200])}
    wrap.__name__ = fn.__name__
    wrap.__doc__ = fn.__doc__
    return wrap


# ── ① 结构时序图谱（chanlun_tgraph，P8/P10） ─────────────────
@tool
def tgraph_slots(sig_type=None, tbe=None):
    """结构槽位边际战绩：bis6/amp/pv/npv 各取值分组（胜率/均值/左尾）。"""
    from chanlun_tgraph import slot_stats
    return slot_stats(sig_type, tbe)


@tool
def tgraph_eval(sig_type="1卖", tbe=True):
    """P10 升级预演：npv≤2 vs 3+ 的左尾深度/时间分散/regime 分层。"""
    from chanlun_tgraph import eval_upgrade
    return eval_upgrade(sig_type, tbe)


@tool
def tgraph_left_tail(sig_type="1卖", tbe=True):
    """npv 分层左尾统计（机器可读）：all/le2/3p 各自
    n/win/avg/p10/worst/<-10%·<-20%·<-30% 计数 + na_n。"""
    from chanlun_tgraph import rows, _sel

    def _tail(rets):
        if not rets:
            return None
        s = sorted(rets)
        n = len(s)
        i10 = max(0, -(-int(n * 10) // 100) - 1)   # ceil(n*0.10)-1
        return {"n": n,
                "win": round(sum(1 for v in s if v > 0) * 100.0 / n, 1),
                "avg": round(sum(s) * 100.0 / n, 2),
                "p10": round(s[i10] * 100.0, 2),
                "worst": round(s[0] * 100.0, 2),
                "lt10": sum(1 for v in s if v < -0.10),
                "lt20": sum(1 for v in s if v < -0.20),
                "lt30": sum(1 for v in s if v < -0.30)}

    rs = _sel(rows(), sig_type, tbe)
    g = {"all": [], "le2": [], "3p": []}
    na_n = 0
    for r in rs:
        v, npv = r.get("ret"), r.get("npv")
        if v is None:
            continue
        g["all"].append(v)
        if npv is None:
            na_n += 1
        elif npv <= 2:
            g["le2"].append(v)
        else:
            g["3p"].append(v)
    return {"all": _tail(g["all"]), "le2": _tail(g["le2"]),
            "3p": _tail(g["3p"]), "na_n": na_n}


@tool
def tgraph_fps(sig_type=None, tbe=None, min_n=5):
    """结构指纹精确分组战绩（按 n 降序）。"""
    from chanlun_tgraph import fp_stats
    return fp_stats(sig_type, tbe, min_n)


# ── ② 信号先例（semantica_mvp，P1-6） ────────────────────────
@tool
def signal_precedents(code, sig_type, tbe=None, k=8):
    """先例检索：同型信号历史案例 topk（语义相似度+20日方向收益+左尾）。"""
    from semantica_mvp import precedents
    return precedents(_norm_code(code), sig_type, tbe, k)


@tool
def signal_enrich(code, sig_type, dt, tbe=None):
    """信号增强：先例战绩 + 语义命中摘要（推送/研判复用）。"""
    from semantica_mvp import enrich_signal
    return enrich_signal(_norm_code(code), sig_type, dt, tbe)


# ── ③ 事件图谱（event_graph，P7） ────────────────────────────
@tool
def events_for_stock(code, days=30):
    """个股事件流：近 N 天公告/新闻事件（偏性/置信/标题）。"""
    from event_graph import events_for_stock as f
    return f(_norm_code(code), days)


@tool
def related_events(query, k=10, kind=None):
    """语义检索相关事件（kind=bull/bear 先过滤后 topk）。"""
    from event_graph import related_events as f
    return f(query, k, kind)


@tool
def event_summary(code, days=7, topn=4):
    """飞书摘要口径：个股近期事件多空统计+强事件列表。"""
    from event_graph import summary_for_stock
    return summary_for_stock(_norm_code(code), days, topn)


@tool
def recent_events(n=8, days=7):
    """近期强事件（bull/bear 各前 n/2，conf 降序）。"""
    from event_graph import recent_strong
    return recent_strong(n, days)


# ── ④ 数据中台读层（datamart） ───────────────────────────────
@tool
def datamart_read(table, start=None, end=None, codes=None, fields=None,
                  limit=0):
    """读层统一入口：真源命中即止+别名降级+日期/代码归一（codes 自动纠后缀）。"""
    import datamart as dm
    df = dm.read(table, start=start, end=end, codes=_fix_codes(codes),
                 fields=fields, limit=limit)
    return {"n": len(df), "rows": df.to_dict("records")}


@tool
def datamart_latest(table):
    """新鲜度现算：表/别名最新日期（进程内 TTL 缓存）。"""
    import datamart as dm
    return {"table": table, "latest": dm.latest(table)}


@tool
def datamart_ready(table):
    """表/别名可用性：ready + 真源 + schema 说明。"""
    import datamart as dm
    r = bool(dm.ready(table))
    return {"table": table, "ready": r,
            "explain": dm.explain(table) if r else None}


# ── ⑤ 数据健康（data_health） ────────────────────────────────
@tool
def data_health():
    """全仓健康扫描：三仓+新鲜度监控组（较重 ~14s，有 TTL 缓存）。"""
    from data_health import scan_warehouse
    return scan_warehouse()


# ── ⑥ 缠论池/账本（纯文件读，不 import chanlun_scan） ─────────
@tool
def pool_rows(sig_type=None, tbe=None, code=None, limit=50):
    """预警池行（可按类型/tbe/code 过滤，dt 降序截 limit）。"""
    path = os.path.join(HERE, "data", "chanlun_pool.jsonl")
    wc = str(code).strip().split(".")[0] if code else None
    if not os.path.exists(path):
        return {"n": 0, "rows": []}
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:
                continue
            if sig_type and r.get("type") != sig_type:
                continue
            if tbe is not None and r.get("tbe") != tbe:
                continue
            if wc and not str(r.get("code") or "").startswith(wc):
                continue
            out.append(r)
    out.sort(key=lambda r: r.get("dt") or "", reverse=True)
    return {"n": len(out), "rows": out[:limit]}


@tool
def stock_info(code):
    """股票身份卡：名称（全市场映射，含曾用名）+ 最新行情（ts_daily 尾3行）
    + 池内信号。用户给 6 位代码查个股时必须先调此工具（防编造名称）；
    也支持中文名称入参（全市场反查代码，防猜码）。"""
    s = str(code or "").strip()
    c = _norm_code(s)
    name_hits = []
    if not (len(c.split(".")[0]) == 6 and c.split(".")[0].isdigit()):
        sn = _stock_names()
        hit = (sn.get("name2code") or {}).get(s)
        if hit:
            c = hit
        else:
            nm = _name_map()
            name_hits = sorted(k for k, v in nm.items()
                               if v == s or s in v)
            if not name_hits:
                return {"name_q": s, "code": None,
                        "hint": "全市场名称映射（含曾用名）无此名称，"
                                "无法反查代码。答案里明说无法确认，"
                                "绝不硬猜代码"}
            c = name_hits[0]
    sn = _stock_names()
    out = {"code": c, "name": _name_map().get(c)
           or (sn.get("code2name") or {}).get(c)}
    if s in (sn.get("alias_of") or {}):
        out["name_note"] = "曾用名 %s，现名 %s" % (s, out.get("name"))
    if len(name_hits) > 1:
        out["name_candidates"] = ["%s %s" % (k, _name_map()[k])
                                  for k in name_hits]
    sigs = []
    path = os.path.join(HERE, "data", "chanlun_pool.jsonl")
    wc = c.split(".")[0]
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if str(r.get("code") or "").startswith(wc):
                    sigs.append({k: r[k] for k in
                                 ("dt", "type", "tbe", "trend", "entry_px")
                                 if r.get(k) is not None})
    sigs.sort(key=lambda r: r.get("dt") or "", reverse=True)
    out["pool_signals_n"] = len(sigs)
    out["pool_signals_recent"] = sigs[:5]
    try:
        import datamart as dm
        df = dm.read("ts_daily", codes=[c])
        if len(df) and "trade_date" in df.columns:
            df = df.sort_values("trade_date")
        keep = [k for k in ("trade_date", "close", "pct_chg",
                            "vol", "amount") if k in df.columns]
        out["ts_rows_n"] = len(df)
        out["latest"] = df[keep].tail(3).to_dict("records") if len(df) else []
    except Exception as e:
        out["latest_err"] = "%s: %s" % (type(e).__name__, str(e)[:120])
    return out


@tool
def paper_positions():
    """模拟账本：当前持仓 + 最近 closed 结算（方向收益口径）。"""
    path = os.path.join(HERE, "data", "chanlun_paper.json")
    d = {"open": [], "closed": []}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            d = json.load(f) or d
    return {"open": d.get("open", []),
            "closed": d.get("closed", [])[-30:]}


# ── 注册表（agent 发现入口） ─────────────────────────────────
TOOLS = {
    "tgraph_slots":   (tgraph_slots,   "结构槽位边际战绩", {"sig_type": "信号类型如1卖", "tbe": "趋✓布尔"}),
    "tgraph_eval":    (tgraph_eval,    "P10 升级预演分层",  {"sig_type": "信号类型", "tbe": "布尔"}),
    "tgraph_left_tail": (tgraph_left_tail, "npv分层左尾统计",
                          {"sig_type": "信号类型", "tbe": "布尔"}),
    "tgraph_fps":     (tgraph_fps,     "结构指纹分组战绩",  {"sig_type": "信号类型", "tbe": "布尔", "min_n": "最小样本"}),
    "signal_precedents": (signal_precedents, "先例检索topk",
                          {"code": "代码", "sig_type": "信号类型", "tbe": "布尔", "k": "条数"}),
    "signal_enrich":  (signal_enrich,  "信号增强(先例+摘要)",
                          {"code": "代码", "sig_type": "信号类型", "dt": "信号日YYYY-MM-DD", "tbe": "布尔"}),
    "events_for_stock": (events_for_stock, "个股事件流",
                          {"code": "代码", "days": "回看天数"}),
    "related_events": (related_events, "语义检索事件",
                          {"query": "查询文本", "k": "条数", "kind": "bull/bear"}),
    "event_summary":  (event_summary,  "个股事件摘要", {"code": "代码", "days": "天数"}),
    "recent_events":  (recent_events,  "近期强事件", {"n": "条数", "days": "天数"}),
    "datamart_read":  (datamart_read,  "数据中台读层",
                          {"table": "表/别名", "start": "YYYYMMDD", "end": "YYYYMMDD",
                           "codes": "代码列表", "fields": "列列表", "limit": "行数"}),
    "datamart_latest": (datamart_latest, "表最新日期", {"table": "表/别名"}),
    "datamart_ready": (datamart_ready, "表可用性", {"table": "表/别名"}),
    "data_health":    (data_health,    "全仓健康扫描", {}),
    "pool_rows":      (pool_rows,      "预警池查询",
                          {"sig_type": "信号类型", "tbe": "布尔",
                           "code": "股票代码", "limit": "条数"}),
    "stock_info":     (stock_info,     "股票身份卡(名称+最新行情+池内信号；中文名称可反查代码，支持曾用名如改名前旧称)",
                          {"code": "6位股票代码或中文名称(含曾用名)"}),
    "paper_positions": (paper_positions, "模拟账本", {}),
}


def list_tools():
    return [{"name": n, "desc": d, "args": a}
            for n, (f, d, a) in TOOLS.items()]


def call(name, **kw):
    ent = TOOLS.get(name)
    if not ent:
        return {"ok": False, "err": "unknown tool: %s" % name}
    return ent[0](**kw)


# ── CLI ──────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="多智能体工具层")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    p_call = sub.add_parser("call")
    p_call.add_argument("name")
    p_call.add_argument("args", nargs="?", default="{}")
    a = ap.parse_args()
    if a.cmd == "list":
        for t in list_tools():
            print("%-18s %s | %s" % (t["name"], t["desc"],
                                     json.dumps(t["args"], ensure_ascii=False)))
    else:
        try:
            kw = json.loads(a.args) if a.args else {}
        except Exception as e:
            print(json.dumps({"ok": False, "err": "bad json: %s" % e},
                             ensure_ascii=False))
            return
        print(json.dumps(call(a.name, **kw), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
