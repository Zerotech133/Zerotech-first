# -*- coding: utf-8 -*-
"""筛选引擎（H2）：ScreeningDSL v1 → 候选清单 + 可解释命中理由。

设计原则（v2 方案 §5）：
- 透明可解释：字段白名单、算子白名单；每个候选带"命中理由"；
- PIT：数据按 as_of 截止（行情/估值用交易日、财务用 ann_date、行业/指数成分用 as_of）；
- 不构成建议：输出 research_only 标记与免责声明；
- 数据缺字段：标注并剔除（不推算）。

对外入口：
- validate(spec) → {ok, errors, spec}
- apply_filters(df, spec) → df + _reasons/_risk_tags（纯函数，便于测试）
- run(spec, provider=None) → {ok, as_of, matched, candidates, warnings, ...}

DSL 示例见 `docs`/`contracts`；NL 解析见 `analysis/screen_parse.py`。
"""
import datetime
import math
import os
import time
from typing import Any, Callable, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.environ.get("ANALYSIS_WORK") or os.path.join(os.path.dirname(ROOT), "analysis_work")
CURATED = os.path.join(ROOT, "quant_backtest", "data", "tushare_curated")
SW_CACHE = os.path.join(WORK, "cache", "sw")
SCREEN_CACHE = os.path.join(WORK, "cache", "screener")

# 字段白名单：kind = number/string/bool；unit = ratio（小数，展示×100%）/ percent（本身即百分数）
FIELDS: Dict[str, Dict[str, Any]] = {
    # 估值
    "ps": {"kind": "number", "label": "PS(TTM)"},
    "ps_pct_hist": {"kind": "number", "label": "PS 历史分位", "fmt": "%", "unit": "percent"},
    "pe_ttm": {"kind": "number", "label": "PE(TTM)"},
    "dv_ratio": {"kind": "number", "label": "股息率", "fmt": "%", "unit": "percent"},
    "total_mv_yi": {"kind": "number", "label": "总市值(亿)"},
    # 成长/质量
    "revenue_yoy": {"kind": "number", "label": "营收同比", "fmt": "%", "unit": "ratio"},
    "net_profit_yoy": {"kind": "number", "label": "净利同比", "fmt": "%", "unit": "ratio"},
    "roe": {"kind": "number", "label": "ROE", "fmt": "%", "unit": "ratio"},
    "debt_ratio": {"kind": "number", "label": "资产负债率", "fmt": "%", "unit": "ratio"},
    # 行情/流动性
    "ret_20": {"kind": "number", "label": "20日涨跌", "fmt": "%", "unit": "ratio"},
    "ret_60": {"kind": "number", "label": "60日涨跌", "fmt": "%", "unit": "ratio"},
    "amount_20_yi": {"kind": "number", "label": "20日均额(亿)"},
    "turnover_rate": {"kind": "number", "label": "换手率", "fmt": "%", "unit": "percent"},
    # 五维评分（仅已分析股票）
    "quality_score": {"kind": "number", "label": "质量分"},
    # 分类/事件
    "industry_l1": {"kind": "string", "label": "行业(L1)"},
    "industry_l2": {"kind": "string", "label": "行业(L2)"},
    "flag_st": {"kind": "bool", "label": "ST"},
    "event_types": {"kind": "string", "label": "事件类别"},
}

# 申万一级行业白名单（与 analysis/screen_parse.DEFAULT_INDUSTRIES 一致；行业筛选只收白名单，防正则注入）
INDUSTRIES_L1 = (
    "农林牧渔", "基础化工", "钢铁", "有色金属", "电子", "汽车", "家用电器", "食品饮料",
    "纺织服饰", "轻工制造", "医药生物", "公用事业", "交通运输", "房地产", "商贸零售",
    "社会服务", "综合", "建筑材料", "建筑装饰", "电力设备", "国防军工", "计算机", "传媒",
    "通信", "银行", "非银金融", "煤炭", "石油石化", "环保", "美容护理", "机械设备",
)
UNIVERSES = ("A_SHARE", "HS300", "WATCHLIST", "industry")
NUM_OPS = ("=", "!=", ">", ">=", "<", "<=")
STR_OPS = ("in", "notin", "contains", "contains_any")
BOOL_OPS = ("=", "!=")
OPS = NUM_OPS + STR_OPS + BOOL_OPS
RISK_TAGS = {
    "flag_st": "ST",
    "debt_ratio": "高负债",
    "net_profit_yoy": "亏损/负增长",
}


def _fmt(field: str, value: Any) -> str:
    meta = FIELDS.get(field) or {}
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "—"
    unit = meta.get("unit")
    if unit is None and meta.get("fmt") == "%":
        unit = "ratio"
    if unit == "percent":
        return "%.1f%%" % float(value)
    if unit == "ratio":
        return "%.1f%%" % (float(value) * 100)
    if isinstance(value, float):
        return ("%.2f" % value).rstrip("0").rstrip(".")
    return str(value)


def units() -> Dict[str, str]:
    """字段单位表（供前端展示；ratio=小数需×100，percent=本身即百分数）。"""
    return {k: str(v["unit"]) for k, v in FIELDS.items() if v.get("unit")}


def validate(spec: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """校验并规范化 DSL；返回 {ok, errors, spec}。"""
    errors: List[str] = []
    spec = dict(spec or {})
    universe = str(spec.get("universe") or "WATCHLIST").strip()
    uni_low = universe.lower()
    if universe not in UNIVERSES and not uni_low.startswith("industry:"):
        errors.append("universe 非法：%s（可选 A_SHARE/HS300/WATCHLIST/industry:<名称>）" % universe)
    elif uni_low.startswith("industry:"):
        ind_name = universe.split(":", 1)[1].strip()
        if ind_name not in INDUSTRIES_L1:
            errors.append("universe 行业非法：%s（仅支持申万一级行业白名单）" % (ind_name or "<空>"))
    filters = spec.get("filters") or []
    if not isinstance(filters, list) or not filters:
        errors.append("filters 必须为非空数组")
        filters = []
    norm_filters = []
    for i, f in enumerate(filters):
        if not isinstance(f, dict):
            errors.append("filters[%d] 必须是对象" % i)
            continue
        field = str(f.get("field") or "")
        op = str(f.get("op") or "=")
        meta = FIELDS.get(field)
        if not meta:
            errors.append("filters[%d] 未知字段：%s" % (i, field))
            continue
        if op not in OPS:
            errors.append("filters[%d] 未知算子：%s" % (i, op))
            continue
        kind = meta["kind"]
        if kind == "bool" and op not in BOOL_OPS:
            errors.append("filters[%d] 布尔字段仅支持 =/!=" % i)
        if kind == "number" and op not in NUM_OPS:
            errors.append("filters[%d] 数值字段不支持算子 %s" % (i, op))
        if kind == "string" and op not in STR_OPS:
            errors.append("filters[%d] 字符串字段不支持算子 %s" % (i, op))
        if field == "industry_l1":
            vals = f.get("value") if isinstance(f.get("value"), list) else [f.get("value")]
            bad = [str(v) for v in vals if str(v or "") not in INDUSTRIES_L1]
            if bad:
                errors.append("filters[%d] 行业值不在申万一级白名单：%s" % (i, ",".join(bad)))
        norm_filters.append({"field": field, "op": op, "value": f.get("value")})
    rank_by = spec.get("rank_by") or []
    norm_rank = []
    if not isinstance(rank_by, list):
        errors.append("rank_by 必须为数组")
        rank_by = []
    for i, r in enumerate(rank_by):
        if not isinstance(r, dict) or str(r.get("field")) not in FIELDS:
            errors.append("rank_by[%d] 字段非法" % i)
            continue
        if FIELDS[str(r["field"])]["kind"] != "number":
            errors.append("rank_by[%d] 仅支持数值字段" % i)
            continue
        order = str(r.get("order") or "desc").lower()
        if order not in ("asc", "desc"):
            errors.append("rank_by[%d] order 必须 asc/desc" % i)
            order = "desc"
        norm_rank.append({"field": str(r["field"]), "order": order})
    try:
        limit = int(spec.get("limit", 20))
    except Exception:
        limit = 20
    limit = max(1, min(limit, 200))
    as_of = str(spec.get("as_of") or time.strftime("%Y-%m-%d"))
    try:
        as_of_date = datetime.datetime.strptime(as_of, "%Y-%m-%d").date()
    except ValueError:
        as_of_date = None
        errors.append("as_of 需为合法日期 YYYY-MM-DD（如 2026-11-03）")
    if as_of_date is not None and as_of_date > datetime.date.today():
        errors.append("as_of 不能晚于今天（%s）" % datetime.date.today().isoformat())
    norm = {
        "universe": universe,
        "filters": norm_filters,
        "rank_by": norm_rank,
        "limit": limit,
        "as_of": as_of,
        "explain": bool(spec.get("explain", True)),
    }
    return {"ok": not errors, "errors": errors, "spec": norm}


def _cmp(a: Any, op: str, b: Any) -> bool:
    if a is None or (isinstance(a, float) and math.isnan(a)):
        return False
    try:
        if op == "=":
            return a == b
        if op == "!=":
            return a != b
        if op == ">":
            return a > b
        if op == ">=":
            return a >= b
        if op == "<":
            return a < b
        if op == "<=":
            return a <= b
        if op == "in":
            return a in (b or [])
        if op == "notin":
            return a not in (b or [])
        if op == "contains":
            return str(b) in str(a)
        if op == "contains_any":
            return any(str(x) in str(a) for x in (b or []))
    except Exception:
        return False
    return False


def _match(value: Any, op: str, target: Any, kind: str) -> bool:
    if kind == "bool":
        return bool(value) == bool(target) if op == "=" else bool(value) != bool(target)
    return _cmp(value, op, target)


def _reason(f: Dict[str, Any], value: Any) -> str:
    label = FIELDS[f["field"]]["label"]
    op_map = {"<=": "≤", ">=": "≥", "<": "<", ">": ">", "=": "=", "!=": "≠"}
    if f["op"] in STR_OPS:
        return "%s%s%s" % (label, {"in": " ∈ ", "notin": " ∉ ", "contains": " 包含 ", "contains_any": " 含任一 "}[f["op"]],
                           ",".join(map(str, f["value"])) if isinstance(f["value"], list) else f["value"])
    return "%s %s %s（实际 %s）" % (label, op_map.get(f["op"], f["op"]), _fmt(f["field"], f["value"]),
                                    _fmt(f["field"], value))


def apply_filters(df, spec: Dict[str, Any]):
    """纯函数：按 spec 过滤并生成 _reasons/_risk_tags 列；返回过滤后的 DataFrame。"""
    import pandas as pd
    if df is None or len(df) == 0:
        return pd.DataFrame() if df is None else df
    mask = []
    reasons_all = []
    risk_all = []
    for _, row in df.iterrows():
        ok = True
        reasons = []
        risks = []
        for f in spec.get("filters") or []:
            field = f["field"]
            if field not in df.columns:
                ok = False
                break
            value = row.get(field)
            kind = FIELDS[field]["kind"]
            if not _match(value, f["op"], f["value"], kind):
                ok = False
                break
            reasons.append(_reason(f, value))
        if ok:
            # 风险标签（不参与筛选，供用户复核）
            if "flag_st" in df.columns and bool(row.get("flag_st")):
                risks.append("ST")
            try:
                if row.get("debt_ratio") is not None and float(row.get("debt_ratio")) > 0.7:
                    risks.append("高负债")
            except Exception:
                pass
            try:
                if row.get("net_profit_yoy") is not None and float(row.get("net_profit_yoy")) < 0:
                    risks.append("净利负增长")
            except Exception:
                pass
        mask.append(ok)
        reasons_all.append(reasons)
        risk_all.append(risks)
    out = df.loc[mask].copy()
    out["_reasons"] = [r for r, m in zip(reasons_all, mask) if m]
    out["_risk_tags"] = [r for r, m in zip(risk_all, mask) if m]
    return out


def _sort(df, spec):
    rank = spec.get("rank_by") or []
    if not rank or len(df) == 0:
        return df
    cols = []
    asc = []
    for r in rank:
        if r["field"] in df.columns:
            cols.append(r["field"])
            asc.append(r["order"] == "asc")
    if not cols:
        return df
    return df.sort_values(by=cols, ascending=asc, na_position="last")


def _candidate(row) -> Dict[str, Any]:
    metrics = {}
    for k in FIELDS:
        if k in row.index and row.get(k) is not None:
            v = row.get(k)
            try:
                if isinstance(v, float) and math.isnan(v):
                    continue
            except Exception:
                pass
            metrics[k] = (round(float(v), 4) if isinstance(v, (int, float)) and not isinstance(v, bool)
                          else v)
    return {
        "code": str(row.get("ts_code") or ""),
        "name": str(row.get("name") or ""),
        "industry": str(row.get("industry_l1") or row.get("industry") or ""),
        "metrics": metrics,
        "reasons": list(row.get("_reasons") or []),
        "risk_tags": list(row.get("_risk_tags") or []),
    }


# ---------- 真实数据 provider（curated + SW；PIT） ----------

DATA_ROOT = os.path.join(ROOT, "quant_backtest", "data", "tushare")
_BASIC_CACHE = None


def _stock_basic(root=None):
    """读数据根 stock_basic（part_*.parquet）→ {ts_code: name}（当前名称，非 PIT）。"""
    global _BASIC_CACHE
    if root is None and _BASIC_CACHE is not None:
        return _BASIC_CACHE
    base = root or DATA_ROOT
    out = {}
    try:
        import glob
        for p in sorted(glob.glob(os.path.join(base, "stock_basic", "*.parquet"))):
            d = _read_parquet(p)
            if len(d) == 0 or "ts_code" not in d.columns:
                continue
            for _, r in d.iterrows():
                code = str(r.get("ts_code") or "").strip()
                if code:
                    out[code] = str(r.get("name") or "")
    except Exception:
        out = {}
    if root is None:
        _BASIC_CACHE = out
    return out


def _apply_names(feat, names):
    """把名称与 ST 标记写入特征表（flag_st=True 表示名称含 ST/*ST）。"""
    import pandas as pd
    feat = feat.copy()
    feat["name"] = pd.Series({c: names.get(c, "") for c in feat.index})
    feat["flag_st"] = pd.Series({c: ("ST" in str(names.get(c, "")).upper()) for c in feat.index})
    return feat


def _event_types(codes, as_of, db_path=None):
    """事件库 → {code: [类别...]}（events 表列名为 code；兼容 ts_code）。"""
    out = {}
    dbp = db_path or os.path.join(WORK, "analysis.db")
    try:
        import sqlite3
        if not os.path.exists(dbp):
            return out
        con = sqlite3.connect("file:%s?mode=ro" % dbp.replace("\\", "/"), uri=True)
        cols = [r[1] for r in con.execute("PRAGMA table_info(events)")]
        code_col = "code" if "code" in cols else ("ts_code" if "ts_code" in cols else None)
        if code_col is None:
            con.close()
            return out
        rows = con.execute("SELECT %s, category_final, category, date FROM events WHERE date >= ?"
                           % code_col, (as_of[:4] + "-01-01",)).fetchall()
        con.close()
        for code, cat_f, cat, dt in rows:
            if str(code) in codes:
                out.setdefault(str(code), set()).add(str(cat_f or cat or ""))
        return {c: sorted(v) for c, v in out.items()}
    except Exception:
        return out


def _read_parquet(path, columns=None):
    import pandas as pd
    if not os.path.exists(path):
        return pd.DataFrame()
    for cols in (columns, None):
        try:
            return pd.read_parquet(path, columns=cols) if cols else pd.read_parquet(path)
        except Exception:
            continue
    return pd.DataFrame()


def _sw_industry() -> Dict[str, Dict[str, str]]:
    out = {}
    p = os.path.join(SW_CACHE, "member.parquet")
    d = _read_parquet(p)
    if len(d) == 0:
        return out
    # 兼容字段：con_code/stock_code + index_name/l1_name/l2_name
    code_col = next((c for c in ("ts_code", "con_code", "stock_code", "code") if c in d.columns), None)
    l1_col = next((c for c in ("l1_name", "index_name", "industry", "l1") if c in d.columns), None)
    l2_col = next((c for c in ("l2_name", "l2") if c in d.columns), None)
    if not code_col:
        return out
    for _, r in d.iterrows():
        code = str(r.get(code_col) or "").strip()
        if not code:
            continue
        out[code] = {"l1": str(r.get(l1_col) or "") if l1_col else "",
                     "l2": str(r.get(l2_col) or "") if l2_col else ""}
    return out


def _universe_codes(spec) -> List[str]:
    as_of = spec["as_of"].replace("-", "")
    uni = spec["universe"]
    if uni == "WATCHLIST":
        codes = []
        p = os.path.join(ROOT, "analysis", "watchlist.txt")
        if os.path.exists(p):
            for ln in open(p, encoding="utf-8"):
                t = ln.strip()
                if t and not t.startswith("#"):
                    codes.append(t.split()[0].upper())
        return codes
    if uni == "HS300":
        d = _read_parquet(os.path.join(CURATED, "index_weight.parquet"))
        if len(d) == 0:
            return []
        code_col = "con_code" if "con_code" in d.columns else "ts_code"
        d = d[d["trade_date"].astype(str) <= as_of]
        if "index_code" in d.columns:
            d = d[d["index_code"].astype(str).str.startswith("000300")]
        if len(d) == 0:
            return []
        last = str(d["trade_date"].astype(str).max())
        return sorted(d[d["trade_date"].astype(str) == last][code_col].astype(str).unique().tolist())
    # A_SHARE / industry: 最新 daily_basic 全市场
    d = _read_parquet(os.path.join(CURATED, "daily_basic.parquet"), columns=["ts_code", "trade_date"])
    if len(d) == 0:
        return []
    d = d[d["trade_date"].astype(str) <= as_of]
    if len(d) == 0:
        return []
    last = str(d["trade_date"].astype(str).max())
    codes = sorted(d[d["trade_date"].astype(str) == last]["ts_code"].astype(str).unique().tolist())
    return codes


def load_features(spec: Dict[str, Any]) -> Any:
    """从 curated/SW 构建特征表（PIT，按 as_of 截止）。缺字段标注在 warnings 由 run 收集。"""
    import pandas as pd
    as_of = spec["as_of"].replace("-", "")
    codes = set(_universe_codes(spec))
    if not codes:
        return pd.DataFrame()
    # 估值 + 市值（最新交易日 <= as_of）
    db = _read_parquet(os.path.join(CURATED, "daily_basic.parquet"),
                       columns=["ts_code", "trade_date", "ps", "pe_ttm", "dv_ratio",
                                "turnover_rate", "total_mv"])
    db = db[db["trade_date"].astype(str) <= as_of].copy()
    if len(db) == 0:
        return pd.DataFrame()
    db["trade_date"] = db["trade_date"].astype(str)
    db = db[db["ts_code"].astype(str).isin(codes)]
    last_date = str(db["trade_date"].max())
    latest = db[db["trade_date"] == last_date].set_index("ts_code")
    feat = pd.DataFrame(index=latest.index)
    feat["name"] = ""
    feat["ps"] = latest["ps"]
    feat["pe_ttm"] = latest["pe_ttm"]
    feat["dv_ratio"] = latest["dv_ratio"]
    feat["turnover_rate"] = latest["turnover_rate"]
    feat["total_mv_yi"] = latest["total_mv"] / 1e4  # 万元 → 亿元
    # PS 历史分位（近 5 年；截至 as_of）
    try:
        start = str(int(as_of[:4]) - 5) + as_of[4:]
        hist = db[db["trade_date"] >= start][["ts_code", "trade_date", "ps"]].dropna()
        pcts = {}
        for code, sub in hist.groupby("ts_code"):
            cur = sub[sub["trade_date"] == last_date]["ps"]
            if len(cur) == 0 or len(sub) < 120:
                continue
            pcts[code] = float((sub["ps"] <= cur.iloc[0]).sum()) / len(sub) * 100
        feat["ps_pct_hist"] = pd.Series(pcts)
    except Exception:
        feat["ps_pct_hist"] = float("nan")
    # 行情：20/60 日涨跌、20 日均额
    try:
        dl = _read_parquet(os.path.join(CURATED, "daily.parquet"),
                           columns=["ts_code", "trade_date", "close", "amount"])
        dl = dl[dl["trade_date"].astype(str) <= as_of]
        dl = dl[dl["ts_code"].astype(str).isin(codes)]
        dl["trade_date"] = dl["trade_date"].astype(str)
        dl = dl.sort_values(["ts_code", "trade_date"])
        ret20, ret60, amt20 = {}, {}, {}
        for code, sub in dl.groupby("ts_code"):
            sub = sub.dropna(subset=["close"])
            if len(sub) < 21:
                continue
            closes = sub["close"].astype(float).tolist()
            ret20[code] = closes[-1] / closes[-21] - 1
            if len(closes) >= 61:
                ret60[code] = closes[-1] / closes[-61] - 1
            amt = sub["amount"].astype(float).tail(20)
            amt20[code] = amt.mean() / 1e5 if len(amt) else float("nan")  # 千元→亿元
        feat["ret_20"] = pd.Series(ret20)
        feat["ret_60"] = pd.Series(ret60)
        feat["amount_20_yi"] = pd.Series(amt20)
    except Exception:
        pass
    # 财务（PIT：ann_date <= as_of，取最新 end_date/ann_date）
    try:
        fi = _read_parquet(os.path.join(CURATED, "fina_indicator.parquet"))
        keep = [c for c in ("ts_code", "ann_date", "end_date", "tr_yoy", "netprofit_yoy",
                            "roe", "debt_to_assets") if c in fi.columns]
        fi = fi[keep].copy()
        fi = fi[fi["ann_date"].astype(str) <= as_of]
        fi = fi[fi["ts_code"].astype(str).isin(codes)]
        fi = fi.sort_values(["ts_code", "end_date", "ann_date"])
        fi = fi.groupby("ts_code").tail(1).set_index("ts_code")
        feat["revenue_yoy"] = fi.get("tr_yoy") / 100.0 if "tr_yoy" in fi.columns else float("nan")
        feat["net_profit_yoy"] = fi.get("netprofit_yoy") / 100.0 if "netprofit_yoy" in fi.columns else float("nan")
        feat["debt_ratio"] = fi.get("debt_to_assets") / 100.0 if "debt_to_assets" in fi.columns else float("nan")
        feat["roe"] = fi.get("roe") / 100.0 if "roe" in fi.columns else float("nan")
    except Exception:
        pass
    # 行业
    sw = _sw_industry()
    if sw:
        feat["industry_l1"] = pd.Series({c: sw.get(c, {}).get("l1") for c in feat.index})
        feat["industry_l2"] = pd.Series({c: sw.get(c, {}).get("l2") for c in feat.index})
    # 名称 + ST 标记（数据根 stock_basic）
    basics = _stock_basic()
    if basics:
        feat = _apply_names(feat, basics)
    else:
        feat["flag_st"] = False
        feat.attrs.setdefault("warnings", []).append("ST 名单未核验（数据根 stock_basic 缺失），flag_st 过滤不可信")
    # event_types（仅已分析股票；来自 analysis.db 事件库）
    ev = _event_types(codes, as_of)
    if ev:
        feat["event_types"] = pd.Series(ev)
    if codes - set(feat.index):
        feat.attrs.setdefault("warnings", []).append("部分股票无特征数据（字段缺失/未上市/退市）")
    uni = spec["universe"]
    if uni.lower().startswith("industry:"):
        name = uni.split(":", 1)[1].strip()
        # 白名单已在 validate 校验；此处用字面量匹配（regex=False），防正则注入（如 ".*"）
        if "industry_l1" not in feat.columns:
            raise ValueError("行业(L1)数据缺失（SW 成分未就绪），无法按行业筛选")
        feat = feat[feat["industry_l1"].astype(str).str.contains(name, regex=False, na=False)]
    elif uni == "industry" and "industry_l1" in feat.columns:
        # universe=industry 需在 filters 里给 industry_l1
        pass
    feat.index.name = "ts_code"
    return feat.reset_index()


def run(spec: Dict[str, Any], provider: Optional[Callable] = None) -> Dict[str, Any]:
    """执行筛选：validate → provider → apply_filters → rank → limit → 输出候选。"""
    v = validate(spec)
    if not v["ok"]:
        return {"ok": False, "errors": v["errors"]}
    sp = v["spec"]
    warnings: List[str] = []
    try:
        df = (provider or load_features)(sp)
    except Exception as e:  # fail-visible
        return {"ok": False, "errors": ["数据加载失败: " + str(e)[:160]]}
    if df is None or len(df) == 0:
        return {"ok": True, "as_of": sp["as_of"], "universe": sp["universe"],
                "universe_size": 0, "matched": 0, "candidates": [],
                "warnings": ["股票池为空或数据缺失（检查数据仓/日期）"]}
    warnings.extend(list(getattr(df, "attrs", {}).get("warnings") or []))
    # 行业筛选依赖 industry_l1 列：缺列不静默返回全池，直接报错（数据未就绪）
    need_industry = any(f["field"] in ("industry_l1", "industry_l2") for f in sp["filters"])
    if need_industry and "industry_l1" not in df.columns:
        return {"ok": False,
                "errors": ["行业(L1)数据缺失（SW 成分未就绪），无法按行业筛选；请检查数据仓 sw/member.parquet"]}
    # 字段缺失提示
    need = {f["field"] for f in sp["filters"]} | {r["field"] for r in sp["rank_by"]}
    missing = sorted(need - set(df.columns))
    if missing:
        warnings.append("数据缺字段：%s（相关股票会被剔除/不参与排序）" % ",".join(missing))
    matched = apply_filters(df, sp)
    matched = _sort(matched, sp)
    # 质量分：如数据中有（已分析股票）
    if "quality_score" in matched.columns:
        matched["quality_score"] = matched["quality_score"]
    total = len(matched)
    top = matched.head(sp["limit"])
    candidates = [_candidate(r) for _, r in top.iterrows()]
    return {
        "ok": True,
        "as_of": sp["as_of"],
        "universe": sp["universe"],
        "universe_size": int(len(df)),
        "matched": int(total),
        "returned": len(candidates),
        "candidates": candidates,
        "warnings": warnings,
        "spec": sp,
        "units": units(),
        "research_only": True,
        "disclaimer": "筛选结果仅供研究，不构成投资建议；历史数据不代表未来。",
    }
