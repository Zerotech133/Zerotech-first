# -*- coding: utf-8 -*-
"""数据中台目录构建器：扫描 data/tdx + data/tushare 两仓 → data/catalog.json。

按《数据中台架构设计.md》§5：
- tables：物理表登记（source/store/layout/schema/date_col/code_col/freq/
  priority/manifest/freshness/rows/files/done/desc）
- aliases：逻辑别名 → 真源优先级候选（§5.3 真源矩阵）

自动探测（零迁移）：
- layout 按文件名推断：part_NNNN→part、d_YYYYMMDD→by_date、
  p_YYYYMMDD→by_period、<code>_<YYYYMM>→by_code_month、其余→by_code
- schema 取首个 parquet 的列；done 取仓库 _manifest.json；新鲜度按布局算
- priority/desc/date_col/code_col 特例用 TABLE_DEFS 声明，其余给默认

原子写（tmp+replace）；只读仓库数据文件，不改任何存量。

用法：
  python build_catalog.py            # 重建 data/catalog.json 并打印摘要
"""
import glob
import json
import os
import re
import sqlite3
from datetime import datetime

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "catalog.json")
WAREHOUSES = [  # (source, 数据根, manifest 路径)
    ("xtcloud", os.path.join(HERE, "data", "tdx"),
     os.path.join(HERE, "data", "tdx", "_manifest.json")),
    ("tushare", os.path.join(HERE, "data", "tushare"),
     os.path.join(HERE, "data", "tushare", "_manifest.json")),
]
LEGACY_US = os.path.join(HERE, "data", "us")       # 旧美股逐股（deprecated）
CRYPTO_DIR = os.path.join(HERE, "data", "crypto")  # 加密货币日线
INFO_DB = os.path.join(HERE, "data", "tdx", "tdx_info.db")

# tdx_read 同款口径：财务类日期列 end_date
END_DATE_TABLES = {"ts_income", "ts_balancesheet", "ts_cashflow",
                   "ts_fina_indicator",
                   "income", "balancesheet", "cashflow", "fina_indicator"}
# 特例声明（未列出的表给默认值；desc 为空则自动留空）
TABLE_DEFS = {
    # ── tushare 仓 ──
    "anns_d":          {"date_col": "ann_date", "freq": "D",
                        "desc": "公告（按日分片，tushare 兜底源）"},
    "trade_cal":       {"date_col": "cal_date", "code_col": None,
                        "primary_key": ["exchange", "cal_date"],
                        "desc": "交易日历（SSE 全量）"},
    "stock_basic":     {"primary_key": ["ts_code"],
                        "desc": "A股基础信息（tushare）"},
    "stock_company":   {"code_col": "ts_code", "freq": "Q",
                        "desc": "A股上市公司详情（经营范围/高管/员工，图谱公司实体源）"},
    "income":          {"code_col": "ts_code", "date_col": "end_date", "freq": "Q",
                        "desc": "A股利润表（全字段85列，逐码，图谱质量因子源）"},
    "balancesheet":    {"code_col": "ts_code", "date_col": "end_date", "freq": "Q",
                        "desc": "A股资产负债表（全字段152列，逐码）"},
    "cashflow":        {"code_col": "ts_code", "date_col": "end_date", "freq": "Q",
                        "desc": "A股现金流量表（全字段97列，逐码，FCFF/FCFE）"},
    "fina_indicator":  {"code_col": "ts_code", "date_col": "end_date", "freq": "Q",
                        "desc": "A股财务指标（全字段108列，逐码，ROE/ROA/ROIC）"},
    "shibor_lpr":      {"date_col": "date", "freq": "M",
                        "desc": "LPR 利率 1y/5y（monetary_policy 收紧后的替代）"},
    "sf_month":        {"date_col": "month", "freq": "M",
                        "desc": "社融月度（增量/累计/存量，月份 6 位）"},
    "hk_basic":        {"desc": "港股基础信息"},
    "us_basic":        {"desc": "美股基础信息"},
    "us_daily":        {"freq": "D", "desc": "美股日线（逐码，建设中）"},
    "daily":           {"date_col": "trade_date", "code_col": "ts_code",
                        "freq": "D",
                        "desc": "A股日线深历史（tushare 按日，1990→2023-07）"},
    "adj_factor":      {"date_col": "trade_date", "code_col": "ts_code",
                        "freq": "D",
                        "desc": "A股复权因子（tushare 按日，回测前复权源）"},
    "cb_basic":        {"desc": "可转债基础"},
    "cb_daily":        {"freq": "D", "desc": "可转债日线（逐码）"},
    "bak_basic":       {"freq": "D", "desc": "备选基础信息（逐码）"},
    "stk_mins":        {"date_col": "trade_time", "freq": "1min",
                        "desc": "A股 1 分钟线（中证800成分，逐码月度）"},
    "idx_mins":        {"date_col": "trade_time", "freq": "1min",
                        "desc": "指数 1 分钟线（逐码月度）"},
    "sw_mins":         {"date_col": "trade_time", "freq": "1min",
                        "desc": "申万指数 1 分钟线（逐码月度）"},
    "etf_mins":        {"date_col": "trade_time", "freq": "1min",
                        "desc": "ETF 1 分钟线（逐码月度）"},
    "sw_classify_l1":  {"code_col": None, "desc": "申万一级分类"},
    "crypto_daily":    {"date_col": "trade_date", "code_col": "ts_code",
                        "freq": "D", "manifest": None,
                        "desc": "加密货币日线（37 交易对，BTC/ETH 等，逐对单文件）"},
    "index_global":    {"date_col": "trade_date", "code_col": "ts_code",
                        "freq": "D", "manifest": None,
                        "desc": "全球指数日线（20 指数 HSI/SPX/DJI/IXIC/N225 等，逐指数单文件）"},
    "news":            {"date_col": "datetime", "freq": "D",
                        "desc": "新闻快讯（tushare）"},
    "major_news":      {"date_col": "pub_time", "freq": "D",
                        "desc": "大事提醒（tushare）"},
    "npr":             {"date_col": "pubtime", "freq": "D",
                        "desc": "停复牌（tushare，事件驱动节奏）"},
    "research_report": {"desc": "研报（tushare 兜底源）"},
    "top10_holders":   {"layout": "by_period", "date_col": "ann_date",
                        "primary_key": ["ts_code", "ann_date", "end_date",
                                        "holder_name", "holder_type"],
                        "freq": "Q", "desc": "十大股东（报告期分片，公告日查询）"},
    "top10_floatholders": {"layout": "by_period", "date_col": "ann_date",
                          "primary_key": ["ts_code", "ann_date", "end_date",
                                          "holder_name", "holder_type"],
                          "freq": "Q", "desc": "十大流通股东（报告期分片）"},
    "top_list":        {"date_col": "trade_date", "freq": "D",
                        "desc": "龙虎榜每日明细（tushare 兜底源）"},
    "irm_qa_sh":       {"freq": "D", "desc": "互动易问答-沪（逐码）"},
    "irm_qa_sz":       {"freq": "D", "desc": "互动易问答-深（逐码）"},
    # ── tdx 仓（仅 desc；其余与 tdx_read 口径一致）──
    "ts_daily":        {"freq": "D", "desc": "A股日线行情（xtcloud，真源）"},
    "ts_stock_basic":  {"primary_key": ["ts_code"],
                        "desc": "A股基础信息（xtcloud，真源）"},
    "ts_daily_basic":  {"freq": "D", "desc": "A股每日指标（xtcloud）"},
    "ts_hk_daily":     {"freq": "D", "desc": "港股日线（xtcloud，真源）"},
    "ts_index_daily":  {"freq": "D", "desc": "指数日线（xtcloud）"},
    "ts_index_basic":  {"desc": "指数基础信息"},
    "ts_index_weight": {"desc": "指数成分权重"},
    "ts_etf_basic":    {"desc": "ETF 基础信息"},
    "ts_fund_basic":   {"desc": "基金基础信息"},
    "ts_fund_daily":   {"freq": "D", "desc": "基金日线"},
    "ts_margin_detail": {"freq": "D", "desc": "融资融券明细"},
    "ts_moneyflow":    {"freq": "D", "desc": "个股资金流向"},
    "ts_moneyflow_hsgt": {"freq": "D", "desc": "沪深港通资金"},
    "ts_top_list":     {"freq": "D", "desc": "龙虎榜"},
    "ts_top_inst":     {"freq": "D", "desc": "龙虎榜机构明细"},
    "ts_ths_daily":    {"freq": "D", "desc": "同花顺板块日线"},
    "ts_ths_index":    {"desc": "同花顺指数"},
    "ts_ths_member":   {"desc": "同花顺板块成分"},
    "tdx_sector_daily": {"code_col": "code", "freq": "D",
                         "desc": "通达信板块日线"},
    "tdx_sector_def":  {"desc": "通达信板块定义"},
    "tdx_sector_member": {"desc": "通达信板块成分"},
    "tdx_sector_min":  {"date_col": "trade_date", "code_col": "code",
                        "primary_key": ["code", "trade_date", "trade_time"],
                        "freq": "1min",
                        "desc": "通达信板块分钟线"},
    "tdx_sector_min_tail": {"date_col": "trade_date", "code_col": "code",
                            "primary_key": ["code", "trade_date", "trade_time"],
                            "freq": "1min", "desc": "通达信板块分钟线增量尾部"},
    "us_legacy":       {"source": "legacy", "priority": 2, "deprecated": True,
                        "freq": "D", "manifest": None,
                        "desc": "旧美股日线（逐股单文件，待 us_daily 收口后废弃）"},
}

# 财报主键保留同报告期的报表类型及披露版本；不存在的列在扫描时剔除。
for _tbl in ("income", "balancesheet", "cashflow", "fina_indicator"):
    for _name in (_tbl, "ts_" + _tbl):
        TABLE_DEFS.setdefault(_name, {})["primary_key"] = [
            "ts_code", "end_date", "ann_date", "f_ann_date", "report_type",
            "comp_type", "end_type", "update_flag"]
for _tbl in ("anns_d", "research_report", "news", "major_news", "npr"):
    TABLE_DEFS[_tbl]["primary_key"] = []

# §5.3 逻辑别名（resolves 按真源优先级排序）
ALIASES = {
    "daily":       {"resolves": ["ts_daily", "daily"],
                    "primary": "ts_daily",
                    "note": "xtcloud 真源（2023-07 起）+ tushare 深历史（1990 起），"
                            "零重叠 concat，read() 按(code,date)去重"},
    "stock_basic": {"resolves": ["ts_stock_basic", "stock_basic"],
                    "primary": "ts_stock_basic",
                    "merge_key": ["ts_code"],
                    "note": "xtcloud 优先，tushare 补深度"},
    "hk_daily":    {"resolves": ["ts_hk_daily"],
                    "note": "tushare hk_daily 未建仓，仅 xtcloud"},
    "us_daily":    {"resolves": ["us_daily", "us_legacy"],
                    "primary": "us_daily",
                    "note": "tushare 收口前降级旧美股（done 联动自动切换）"},
    "notice":      {"resolves": ["stock_notice", "anns_d"],
                    "primary": "stock_notice",
                    "merge_key": [],
                    "merge_mode": "fallback",
                    "note": "公告：tdx 优先，tushare 补深度"},
    "report":      {"resolves": ["stock_report", "research_report"],
                    "primary": "stock_report",
                    "merge_key": [],
                    "merge_mode": "fallback",
                    "note": "研报：tdx 优先，tushare 补深度"},
    "news":        {"resolves": ["stock_news"],
                    "note": "JEV news.db 独立通道，读层 v1 未路由"},
    "index_daily": {"resolves": ["ts_index_daily"],
                    "note": "指数日线（xtcloud）"},
    "moneyflow":   {"resolves": ["ts_moneyflow"],
                    "note": "个股资金流向（xtcloud）"},
    "margin":      {"resolves": ["ts_margin_detail"],
                    "note": "融资融券明细（xtcloud）"},
    "income":      {"resolves": ["income", "ts_income"],
                    "merge_key": ["ts_code", "end_date"],
                    "note": "A股利润表：tushare 全字段优先，xtcloud 有限字段降级"},
    "balancesheet":{"resolves": ["balancesheet", "ts_balancesheet"],
                    "merge_key": ["ts_code", "end_date"],
                    "note": "A股资产负债表：tushare 全字段优先，xtcloud 降级"},
    "cashflow":    {"resolves": ["cashflow", "ts_cashflow"],
                    "merge_key": ["ts_code", "end_date"],
                    "note": "A股现金流量表：tushare 全字段优先，xtcloud 降级"},
    "fina_indicator":{"resolves": ["fina_indicator", "ts_fina_indicator"],
                      "merge_key": ["ts_code", "end_date"],
                      "note": "A股财务指标：tushare 全字段优先，xtcloud 降级"},
}


def _infer_layout(files):
    for f in files:
        if re.fullmatch(r"part_\d+\.parquet", f):
            return "part"
    for f in files:
        if re.fullmatch(r"d_\d{8}\.parquet", f):
            return "by_date"
    for f in files:
        if re.fullmatch(r"p_\d{8}\.parquet", f):
            return "by_period"
    for f in files:
        if re.search(r"_\d{6}\.parquet$", f):
            return "by_code_month"
    return "by_code"


def _dcol(tbl, layout):
    d = TABLE_DEFS.get(tbl, {}).get("date_col")
    if d:
        return d
    if layout == "by_code_month":
        return "trade_time"
    if tbl in END_DATE_TABLES:
        return "end_date"
    return "trade_date"


def _ccol(tbl):
    return TABLE_DEFS.get(tbl, {}).get("code_col", "ts_code")


def _dmax(series):
    vals = [str(v)[:10].replace("-", "") for v in series.tolist()]
    vals = [v[:8] for v in vals if v[:8].isdigit()]
    return max(vals) if vals else None


def _rows_of(path):
    """parquet 行数（footer 快读不解数据，~0.1ms/文件）；失败返回 -1。"""
    try:
        from pyarrow.parquet import ParquetFile
        return ParquetFile(path).metadata.num_rows
    except Exception:
        return -1


def _freshness(store, layout, dcol, files):
    """本地最新日期（YYYYMMDD）；无日期列/空表返回 None。

    K99：by_date 布局空片回退——休市日/接口空返回时 pull 侧落 0 行空片
    防重拉（kg_bclass「0 行也落片」设计），文件名日期≠数据日期。纯按
    文件名取 max 会制造新鲜度幻觉（断流天天落空片 → freshness 天天=
    今天 → source_fresh 永远绿）。改为从新到旧读首个非空分片的真实
    最大日期；dcol 缺失/全空回退文件名口径保持兼容。

    K100：by_code/by_code_month 空壳失明修复——逐码布局 0 行空壳是
    常态（停牌/未披露/增量拉取无新数据的个体；实证 irm_qa_sz 348 空
    壳几乎全部聚集在 mtime 末端=最后一次增量批，stk_mins 811 空），原
    「取最新一个文件」实现被打瞎（7 表 fresh=None）。改为 footer 行数
    快筛（早停）跳空壳，读首个非空文件的 dcol；全空 → None（rating
    侧 files 兜底转为可见告警）。
    """
    if not files:
        return None
    try:
        if layout == "by_date":
            dated = [f for f in files if re.search(r"d_(\d{8})", f)]
            if not dated:
                return None
            if dcol:
                for f in sorted(dated, reverse=True):
                    try:
                        df = pd.read_parquet(os.path.join(store, f))
                    except Exception:
                        continue          # 单文件损坏：看更旧的
                    if dcol not in df.columns:
                        break              # 列不存在：回退文件名口径
                    v = _dmax(df[dcol])
                    if v:
                        return v           # 最新非空分片的数据日期
                else:
                    return None            # 循环走完=全是空片
            return max(re.search(r"d_(\d{8})", f).group(1) for f in dated)
        if layout == "by_code_month":
            # K100：文件名（月）降序 footer 筛跳空壳，首个非空读 dcol
            cands = sorted((f for f in files
                            if re.search(r"_\d{6}\.parquet$", f)),
                           reverse=True)
            for f in cands:
                p = os.path.join(store, f)
                if _rows_of(p) == 0:
                    continue          # 空壳：停牌/未披露个体常态
                try:
                    v = _dmax(pd.read_parquet(p, columns=[dcol])[dcol])
                except Exception:
                    continue          # 损坏或列缺失：看次新
                if v:
                    return v
            return None
        if layout == "by_code":
            # K100：mtime 降序 footer 筛跳空壳（聚集批可达数百个，
            # 实证 irm_qa_sz rank347 才见非空）。
            # K102：非空中取前 8 个读 dcol 取 max——「首个非空」有随机
            # 偏差（同批写入 mtime 平局，排序不稳定，实证 us_daily 取到
            # 停更票尾 09-30 而全表主力水位 10-02）；8 采样全停更概率≈0
            cands = sorted(files, key=lambda f: os.path.getmtime(
                os.path.join(store, f)), reverse=True)
            seen = 0
            best = None
            for f in cands:
                p = os.path.join(store, f)
                if _rows_of(p) == 0:
                    continue          # 空壳：增量拉取无新数据的个体
                try:
                    v = _dmax(pd.read_parquet(p, columns=[dcol])[dcol])
                except Exception:
                    continue          # 损坏或列缺失：看次新
                if v and (not best or v > best):
                    best = v
                seen += 1
                if seen >= 8:
                    break
            return best
        # part：全分片只读日期列（分片数少，代价可忽略）
        vals = [pd.read_parquet(os.path.join(store, f), columns=[dcol])[dcol]
                for f in files]
        if not vals:
            return None
        return _dmax(pd.concat(vals, ignore_index=True))
    except Exception:
        return None


def _scan_dir(source, root, mpath, tbl, mf, store=None):
    """扫单个表目录 → catalog 条目。store 可覆盖（us_legacy→data/us）。"""
    store = store or os.path.join(root, tbl)
    files = sorted(os.path.basename(f) for f in
                   glob.glob(os.path.join(store, "*.parquet")))
    d = TABLE_DEFS.get(tbl, {})
    layout = d.get("layout") or (_infer_layout(files) if files else "part")
    schema, freshness = [], None
    if files:
        try:
            from pyarrow.parquet import ParquetFile
            schema = ParquetFile(os.path.join(store, files[0])).schema_arrow.names
            declared = set(d.get("primary_key") or [])
            if declared - set(schema):
                # 首分片可能缺明细身份列，仅扫 footer 补齐表级 schema。
                for filename in files[1:]:
                    try:
                        names = ParquetFile(os.path.join(store, filename)).schema_arrow.names
                    except Exception:
                        continue
                    schema.extend(c for c in names if c not in schema)
                    if declared.issubset(schema):
                        break
        except Exception:
            schema = []
    dcol, ccol = _dcol(tbl, layout), _ccol(tbl)
    if files and schema:
        if dcol not in schema:
            dcol = d.get("date_col") or next(
                (c for c in ("trade_date", "cal_date", "ann_date",
                             "end_date", "trade_time", "list_date")
                 if c in schema), None)
        ccol = ccol if ccol in schema else next(
            (c for c in ("ts_code", "code", "symbol") if c in schema), None)
    freshness = _freshness(store, layout, dcol, files)
    ent = mf.get(tbl) or {}
    done = bool(ent.get("done")) if ent else bool(files)
    primary_key = d.get("primary_key", [c for c in (ccol, dcol) if c])
    return {
        "source": source, "store": store.replace("\\", "/"), "layout": layout,
        "schema": schema, "date_col": dcol, "code_col": ccol,
        "primary_key": [c for c in primary_key if c in schema],
        "freq": d.get("freq", "D"),
        "priority": d.get("priority", 1),
        "manifest": (None if d.get("manifest", "unset") is None
                     else (mpath or "").replace("\\", "/") or None),
        "freshness": freshness,
        "rows": ent.get("rows") if ent else None,
        "files": len(files), "done": done,
        "deprecated": d.get("deprecated", False),
        "desc": d.get("desc", ""),
    }


def _scan_sqlite(mf):
    """tdx_info.db 三表 → catalog 条目（layout=sqlite）。"""
    out = {}
    if not os.path.exists(INFO_DB):
        return out
    dcol = {"stock_notice": "notice_date", "stock_news": "pub_date",
            "stock_report": "pub_date"}
    fresh = {}
    try:
        conn = sqlite3.connect(INFO_DB, timeout=10)
        for t, c in dcol.items():
            try:
                v = conn.execute("SELECT MAX(%s) FROM %s" % (c, t)).fetchone()
                s = str(v[0]) if v and v[0] else ""
                fresh[t] = s[:10].replace("-", "") if s[:4].isdigit() else None
            except sqlite3.OperationalError:
                pass
        conn.close()
    except Exception:
        pass
    for t in ("stock_notice", "stock_news", "stock_report"):
        ent = mf.get(t) or {}
        out[t] = {
            "source": "xtcloud", "store": INFO_DB.replace("\\", "/"),
            "layout": "sqlite", "schema": [], "date_col": dcol[t],
            "code_col": "ts_code", "freq": "D", "priority": 1,
            "primary_key": [],
            "manifest": WAREHOUSES[0][2].replace("\\", "/"),
            "freshness": fresh.get(t), "rows": ent.get("rows"),
            "files": 0, "done": bool(ent.get("done")),
            "deprecated": False, "desc": "tdx 信息库（公告/个股新闻/研报）",
        }
    return out


def build(data_root=None, out_path=None):
    """扫描两仓 → catalog dict（并原子落盘）。测试可传自定义根目录。"""
    tables = {}
    for source, root, mpath in (WAREHOUSES if data_root is None else
                                [(s, os.path.join(data_root, "data", n),
                                  os.path.join(data_root, "data", n,
                                               "_manifest.json"))
                                 for s, n in (("xtcloud", "tdx"),
                                              ("tushare", "tushare"))]):
        mf = {}
        if os.path.exists(mpath):
            try:
                mf = json.load(open(mpath, encoding="utf-8"))
            except Exception:
                mf = {}
        for tbl in sorted(os.listdir(root)) if os.path.isdir(root) else []:
            if not os.path.isdir(os.path.join(root, tbl)) or \
                    tbl.startswith("_"):
                continue
            tables[tbl] = _scan_dir(source, root, mpath, tbl, mf)
        if data_root is None and source == "xtcloud":   # tdx 信息库三表
            tables.update(_scan_sqlite(mf))
    if data_root is None and os.path.isdir(LEGACY_US):
        tables["us_legacy"] = _scan_dir(
            "legacy", os.path.dirname(LEGACY_US), None, "us_legacy", {},
            store=LEGACY_US)
    if data_root is None and os.path.isdir(CRYPTO_DIR):
        tables["crypto_daily"] = _scan_dir(
            "crypto", os.path.dirname(CRYPTO_DIR), None, "crypto_daily", {},
            store=CRYPTO_DIR)
    cat = {
        "version": 1,
        "updated": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "tables": tables,
        "aliases": ALIASES,
    }
    path = out_path or OUT
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cat, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    return cat


def _summarize(cat):
    print("%-18s %-8s %-14s %-6s %-10s %-8s %s"
          % ("table", "source", "layout", "done", "freshness", "files",
             "desc"))
    for t, e in sorted(cat["tables"].items()):
        print("%-18s %-8s %-14s %-6s %-10s %-8d %s"
              % (t, e["source"], e["layout"], "Y" if e["done"] else "-",
                 e["freshness"] or "—", e["files"], e["desc"][:24]))
    print("\naliases:")
    for a, d in sorted(cat["aliases"].items()):
        print("  %-12s → %s" % (a, " > ".join(d["resolves"])))


if __name__ == "__main__":
    _summarize(build())
