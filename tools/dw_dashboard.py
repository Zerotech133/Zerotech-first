# -*- coding: utf-8 -*-
"""数据仓驾驶舱快照生成器 v2：KPI 下钻 + 来源详情 + 今日动态 + 挖掘时间线 + 治理路线。

产出 portal/static/dw_snapshot.json（页面 fetch；支持前端下载 JSON/CSV）。
用法：python tools/dw_dashboard.py [--json]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(r"C:\项目\老田的AI助手")
DATA = REPO / "quant_backtest" / "data"
PORTAL = REPO / "portal"
OUT = PORTAL / "static" / "dw_snapshot.json"
PY = sys.executable
TODAY = datetime.now().strftime("%Y-%m-%d")

DATA_KEYWORDS = (
    "数据", "data", "tushare", "catalog", "registry", "manifest", "仓库", "备份", "backup",
    "postgres", "mysql", "sqlite", "staging", "元数据", "治理", "研究", "挖掘", "假设",
    "因子", "contract", "lineage", "freshness", "qualify", "入库", "清洗", "质检",
    "港股", "汇率", "db", "pg", "ann", "jes", "jev", "forecast", "top_list", "moneyflow",
)


def _data_related(t: dict) -> bool:
    blend = ((t.get("id") or "") + " " + (t.get("desc") or "") + " " + (t.get("owner") or "")).lower()
    return any(k.lower() in blend for k in DATA_KEYWORDS)


CONTRACTS = {"daily", "daily_basic", "index_daily", "adj_factor", "margin_detail", "moneyflow"}
SOURCE_HYP = {
    "daily": ["H1", "H2", "H3", "H4", "H7", "H8", "H9", "H11", "H12", "H13", "H14"],
    "daily_basic": ["H7", "H10", "H14"], "moneyflow": ["H4", "H13"],
    "fina_indicator": ["H7", "H10", "H14"], "income": ["H7"], "balancesheet": ["H7"],
    "cashflow": ["H7"], "forecast": ["H2", "H11", "H12"], "express": ["H2"],
    "top_list": ["H3"], "top_inst": ["H3"], "index_global": ["H9"],
    "index_daily": ["H1", "H2", "H9"], "index_member_all": ["H8", "H14"],
    "anns_d": ["H1", "H11", "H12"], "us_daily": ["H9"], "hk_daily": ["H9"],
    "fx_daily": ["H9"],
}
MINING = {
    "etf_mins", "shibor", "shibor_lpr", "cyq_chips", "cyq_perf", "stk_mins", "idx_mins", "sw_mins",
    "hk_balancesheet", "hk_cashflow", "hk_fina_indicator", "hk_income", "hk_basic",
    "us_balancesheet", "us_cashflow", "us_fina_indicator", "us_income", "us_basic", "us_daily",
    "index_weight", "irm_qa_sh", "irm_qa_sz", "major_news", "research_report", "sf_month",
    "stk_pledge", "stk_holdernumber", "stk_managers", "stock_company", "top10_holders",
    "top10_floatholders", "limit_list_d", "sw_l1_member", "npr", "news",
}
LAYER_ORDER = ["原始层", "采集流", "生产库", "研究库", "治理库"]
STATUS_ORDER = ["done", "in_progress", "mining", "pending"]
SOURCE_META = {
    "daily": ("Tushare 日线", "原始层", "done", "全市场日线；catalog/registry 登记"),
    "daily_basic": ("Tushare 每日指标", "原始层", "done", "PE/PB/市值；契约已建"),
    "moneyflow": ("Tushare 资金流", "原始层", "done", "3 年 923 文件；契约已建；H13 无 alpha"),
    "fina_indicator": ("Tushare 财务指标", "原始层", "done", "5,569 文件；PIT 可用"),
    "income": ("Tushare 利润表", "原始层", "done", "PIT 财报"),
    "balancesheet": ("Tushare 资产负债表", "原始层", "done", "PIT 财报"),
    "cashflow": ("Tushare 现金流", "原始层", "done", "PIT 财报"),
    "forecast": ("Tushare 业绩预告", "原始层", "done", "3 年 21,196 行"),
    "express": ("Tushare 业绩快报", "原始层", "done", "3 年 5,947 行"),
    "top_list": ("Tushare 龙虎榜", "原始层", "done", "3 年 760 日"),
    "top_inst": ("Tushare 龙虎榜席位", "原始层", "done", "3 年 760 日"),
    "hk_daily": ("Tushare 港股日线", "原始层", "in_progress", "726 日/152.7 万行；A/H 研究待开"),
    "fx_daily": ("Tushare 汇率", "原始层", "in_progress", "FXCM 价格字段为空，需换源"),
    "index_global": ("全球指数", "原始层", "done", "20 指数；H9 已用"),
    "index_daily": ("A 股指数日线", "原始层", "done", "宽基指数"),
    "index_member_all": ("申万行业历史成分", "原始层", "done", "5,922 条 PIT 归属"),
    "anns_d": ("公告日数据", "原始层", "done", "按日公告标题"),
    "us_daily": ("美股日线", "原始层", "done", "24,211 文件"),
    "sw_classify_l1": ("申万一级分类字典", "原始层", "done", "31 行业"),
    "etf_mins": ("ETF 分钟线", "原始层", "mining", "存在，未挖掘"),
    "shibor": ("Shibor 利率", "原始层", "mining", "存在，未挖掘"),
    "shibor_lpr": ("LPR 利率", "原始层", "mining", "存在，未挖掘"),
}
# 数据源中文名 + 接入方式：接口同步 / 爬虫采集 / 本地库 / 容器库 / 文件导入
# (中文名, 层, 状态, 说明, 接入方式)
SOURCE_META3 = {
    "daily": ("Tushare·A股日线", "接口同步", "done", "全市场日线，按交易日接口同步", "接口同步"),
    "daily_basic": ("Tushare·每日指标", "接口同步", "done", "PE/PB/市值，按交易日同步", "接口同步"),
    "moneyflow": ("Tushare·个股资金流", "接口同步", "done", "3 年 923 日，接口同步", "接口同步"),
    "fina_indicator": ("Tushare·财务指标", "接口同步", "done", "5,569 只股票文件，按股票接口拉取（PIT）", "接口同步"),
    "income": ("Tushare·利润表", "接口同步", "done", "财报，按股票接口拉取", "接口同步"),
    "balancesheet": ("Tushare·资产负债表", "接口同步", "done", "财报，按股票接口拉取", "接口同步"),
    "cashflow": ("Tushare·现金流量表", "接口同步", "done", "财报，按股票接口拉取", "接口同步"),
    "forecast": ("Tushare·业绩预告", "接口同步", "done", "3 年 21,196 行（含预告类型/幅度）", "接口同步"),
    "express": ("Tushare·业绩快报", "接口同步", "done", "3 年 5,947 行", "接口同步"),
    "top_list": ("Tushare·龙虎榜", "接口同步", "done", "3 年 760 日榜单", "接口同步"),
    "top_inst": ("Tushare·龙虎榜机构席位", "接口同步", "done", "3 年 760 日席位明细", "接口同步"),
    "hk_daily": ("Tushare·港股日线", "接口同步", "in_progress", "726 日/152.7 万行；A/H 研究待开", "接口同步"),
    "fx_daily": ("Tushare·汇率", "接口同步", "in_progress", "FXCM 价格字段为空，需换源", "接口同步"),
    "index_global": ("Tushare·全球指数", "接口同步", "done", "20 个全球指数（2018+）", "接口同步"),
    "index_daily": ("Tushare·A股指数日线", "接口同步", "done", "宽基/行业指数日线", "接口同步"),
    "index_member_all": ("Tushare·申万行业历史成分", "接口同步", "done", "5,922 条 PIT 行业归属", "接口同步"),
    "index_classify": ("Tushare·指数分类", "接口同步", "done", "行业/指数分类字典", "接口同步"),
    "anns_d": ("Tushare·公告日数据", "接口同步", "done", "按日公告标题", "接口同步"),
    "adj_factor": ("Tushare·复权因子", "接口同步", "done", "日频复权因子", "接口同步"),
    "margin_detail": ("Tushare·融资融券明细", "接口同步", "done", "两融明细", "接口同步"),
    "stock_basic": ("Tushare·股票基础信息", "接口同步", "done", "代码/行业/上市日等", "接口同步"),
    "trade_cal": ("Tushare·交易日历", "接口同步", "done", "交易日历", "接口同步"),
    "sw_classify_l1": ("Tushare·申万一级分类字典", "接口同步", "done", "31 个行业", "接口同步"),
    "sw_l1_member": ("Tushare·申万一级成分（旧）", "接口同步", "mining", "旧成分表，已被历史成分替代", "接口同步"),
    "limit_list_d": ("Tushare·涨跌停列表", "接口同步", "mining", "已入库，未用于研究", "接口同步"),
    "index_weight": ("Tushare·指数权重", "接口同步", "mining", "已入库，未用于研究", "接口同步"),
    "stk_holdernumber": ("Tushare·股东户数", "接口同步", "mining", "已入库，未用于研究", "接口同步"),
    "stk_managers": ("Tushare·高管任职", "接口同步", "mining", "已入库，未用于研究", "接口同步"),
    "stk_pledge": ("Tushare·股权质押", "接口同步", "mining", "已入库，未用于研究", "接口同步"),
    "stk_mins": ("Tushare·个股分钟线", "接口同步", "mining", "已入库，未用于研究", "接口同步"),
    "idx_mins": ("Tushare·指数分钟线", "接口同步", "mining", "已入库，未用于研究", "接口同步"),
    "sw_mins": ("Tushare·申万分钟线", "接口同步", "mining", "已入库，未用于研究", "接口同步"),
    "etf_mins": ("Tushare·ETF分钟线", "接口同步", "mining", "已入库，未用于研究", "接口同步"),
    "shibor": ("Tushare·Shibor利率", "接口同步", "mining", "已入库，未用于研究", "接口同步"),
    "shibor_lpr": ("Tushare·LPR利率", "接口同步", "mining", "已入库，未用于研究", "接口同步"),
    "cyq_chips": ("Tushare·筹码分布", "接口同步", "mining", "已入库，未用于研究", "接口同步"),
    "cyq_perf": ("Tushare·筹码胜率", "接口同步", "mining", "已入库，未用于研究", "接口同步"),
    "top10_holders": ("Tushare·十大股东", "接口同步", "mining", "已入库，未用于研究", "接口同步"),
    "top10_floatholders": ("Tushare·十大流通股东", "接口同步", "mining", "已入库，未用于研究", "接口同步"),
    "stock_company": ("Tushare·公司基本信息", "接口同步", "mining", "已入库，未用于研究", "接口同步"),
    "bak_basic": ("Tushare·备用基础表", "接口同步", "mining", "备用数据", "接口同步"),
    "cb_basic": ("Tushare·可转债基础", "接口同步", "mining", "已入库，未用于研究", "接口同步"),
    "cb_daily": ("Tushare·可转债日线", "接口同步", "mining", "已入库，未用于研究", "接口同步"),
    "major_news": ("Tushare·重大新闻", "接口同步", "mining", "已入库，未用于研究", "接口同步"),
    "news": ("Tushare·新闻快讯", "接口同步", "mining", "已入库，未用于研究", "接口同步"),
    "npr": ("Tushare·NPR公告", "接口同步", "mining", "已入库，未用于研究", "接口同步"),
    "research_report": ("Tushare·研报数据", "接口同步", "mining", "已入库，未用于研究", "接口同步"),
    "irm_qa_sh": ("Tushare·上证互动问答", "接口同步", "mining", "已入库，未用于研究", "接口同步"),
    "irm_qa_sz": ("Tushare·深证互动问答", "接口同步", "mining", "已入库，未用于研究", "接口同步"),
    "sf_month": ("Tushare·月度社会融资", "接口同步", "mining", "已入库，未用于研究", "接口同步"),
    "us_daily": ("Tushare·美股日线", "接口同步", "mining", "24,211 只股票文件；未用于研究", "接口同步"),
    "us_basic": ("Tushare·美股基础信息", "接口同步", "mining", "已入库，未用于研究", "接口同步"),
    "us_income": ("Tushare·美股利润表", "接口同步", "mining", "已入库，未用于研究", "接口同步"),
    "us_balancesheet": ("Tushare·美股资产负债表", "接口同步", "mining", "已入库，未用于研究", "接口同步"),
    "us_cashflow": ("Tushare·美股现金流量表", "接口同步", "mining", "已入库，未用于研究", "接口同步"),
    "us_fina_indicator": ("Tushare·美股财务指标", "接口同步", "mining", "已入库，未用于研究", "接口同步"),
    "hk_basic": ("Tushare·港股基础信息", "接口同步", "mining", "已入库，未用于研究", "接口同步"),
    "hk_income": ("Tushare·港股利润表", "接口同步", "mining", "已入库，未用于研究", "接口同步"),
    "hk_balancesheet": ("Tushare·港股资产负债表", "接口同步", "mining", "已入库，未用于研究", "接口同步"),
    "hk_cashflow": ("Tushare·港股现金流量表", "接口同步", "mining", "已入库，未用于研究", "接口同步"),
    "hk_fina_indicator": ("Tushare·港股财务指标", "接口同步", "mining", "已入库，未用于研究", "接口同步"),
}

METHOD_ORDER = ["接口同步", "爬虫采集", "本地库", "容器库", "文件导入"]

GOV_ITEMS = [
    ("catalog/registry 一致性", "done", "v16 / 253 表 / sha 一致", "2026-10-29"),
    ("manifest 覆盖", "done", "closed 239 / not_applicable 12 / open 1 / missing 1", "2026-10-29"),
    ("数据契约（ODCS）", "in_progress", "6 份；待扩研究用表", "2026-10-28"),
    ("资格取证", "in_progress", "evidence_chain/registry；未全量覆盖", "2026-10-28"),
    ("display 展示登记", "in_progress", "92/253", "2026-10-28"),
    ("生命周期 lifecycle", "in_progress", "data_lifecycle.yaml 9 条", "2026-10-28"),
    ("新鲜度 freshness", "done", "DW-Daily 06:30 + run_all", "2026-10-28"),
    ("staging 生命周期", "done", "retention 30d / 锁 7d", "2026-10-27"),
    ("备份链 BackupDaily", "done", "7 产物 + 异地校验 + 新增 DB section7", "2026-10-29"),
    ("可重拉策略", "done", "_source_reclaim.json 11 类", "2026-10-29"),
    ("汇率数据源缺失", "in_progress", "Tushare FXCM 价格空 → 换源(P2)", "2026-10-29"),
    ("news 历史沉淀", "in_progress", "不可回填→长期采集+保留365天", "2026-10-29"),
    ("xq 近端抓取频率", "done", "单次 3 轮（240s）", "2026-10-29"),
    ("watchdog 单实例校验", "pending", "ws 双实例现象，建议互斥加固", "2026-10-30"),
    ("approval 回写 devtask/事件", "in_progress", "approvals.jsonl 已落；回写待接", "2026-10-30"),
]
ROADMAP = [
    ("D0 家底盘清", "done", "2026-10-28", "catalog/registry 基线、db health、备份链"),
    ("D1 元数据治理收尾", "done", "2026-10-28", "manifest/freshness/staging/lifecycle"),
    ("D2–D3 Supervisor + 签字卡", "done", "2026-10-30", "L1/L3/L4 dry-run；approval:* 接线"),
    ("D4–D5 M4/v1.1 S2 签字", "done", "2026-10-30", "卡片→点击→approvals 落账"),
    ("数据补强批（Tushare）", "done", "2026-10-28~29", "forecast/express/top_*/moneyflow/hk_daily/index_member_all"),
    ("09:30 节点检查", "pending", "2026-10-30", "T19/DEV7/交接 + 签字回写验证"),
    ("H14 质量陷阱决策 A/B/C", "pending", "2026-10-30 例会", "组合层回测→影子盘"),
    ("A/H 横截面比价研究", "in_progress", "2026-10-30+", "hk_daily 已入库；汇率缺源不阻塞"),
    ("fx 汇率换源", "pending", "P2", "东财/央行中间价"),
    ("备份覆盖补洞", "in_progress", "2026-10-30", "quant_meta+thunder_model 已接入；03:00 首跑验回执"),
]


def _fmt_size(mb: float) -> str:
    return ("%.1f GB" % (mb / 1000)) if mb >= 1000 else ("%.0f MB" % mb)


def _size_mb(path: Path) -> float:
    total = 0
    for f in path.rglob("*"):
        try:
            if f.is_file():
                total += f.stat().st_size
        except OSError:
            pass
    return round(total / 1e6, 1)


def _mtime_date(p: Path) -> str:
    try:
        return datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d")
    except OSError:
        return ""


def load_hypotheses() -> list[dict]:
    db = REPO / "research" / "hypotheses.db"
    if not db.exists():
        return []
    con = sqlite3.connect("file:%s?mode=ro" % str(db).replace("\\", "/"), uri=True)
    cols = [r[1] for r in con.execute("PRAGMA table_info(hypotheses)")]
    rows = con.execute("SELECT * FROM hypotheses ORDER BY id").fetchall()
    con.close()
    out = []
    for r in rows:
        d = dict(zip(cols, r))
        out.append({"id": d.get("id"), "title": d.get("title"), "status": d.get("status"),
                    "question": (d.get("question") or "")[:200],
                    "data_sources": d.get("data_sources") or "",
                    "method": d.get("method") or "",
                    "conclusion": (d.get("conclusion") or "")[:400],
                    "evidence": d.get("evidence_path") or "",
                    "created_at": (d.get("created_at") or "")[:19],
                    "updated_at": (d.get("updated_at") or "")[:19]})
    return out


def load_sources(cat: dict) -> list[dict]:
    rows = []
    tdir = DATA / "tushare"
    for d in sorted(p for p in tdir.iterdir() if p.is_dir()):
        if d.name.startswith(".") or any(k in d.name for k in ("probe", "quarantine", "bak_")):
            continue
        meta = SOURCE_META3.get(d.name)
        if meta:
            label, _method, status, note, method = meta
            layer = "接口同步"
        elif d.name in MINING:
            label, layer, status, note, method = d.name, "接口同步", "mining", "数据已入库，尚未用于研究", "接口同步"
        else:
            label, layer, status, note, method = d.name, "接口同步", "done", "已入库/已登记（资产目录）", "接口同步"
        files = sorted(d.glob("*.parquet"))
        if files:
            files_n = len(files)
            range_s = "%s..%s" % (files[0].stem.replace("part_", "p").replace("d_", ""),
                                  files[-1].stem.replace("part_", "p").replace("d_", ""))
            today_new = sum(1 for f in files if _mtime_date(f) == TODAY)
            last_update = max((_mtime_date(f) for f in files), default="")
        else:
            files_n = sum(1 for x in d.iterdir() if x.is_file()) if d.exists() else 0
            range_s = "按股票文件"
            today_new = 0
            last_update = max((_mtime_date(x) for x in d.iterdir()), default="") if d.exists() else ""
        table = cat["tables"].get(d.name) or {}
        mb = _size_mb(d)
        rows_n = _parquet_rows(d, table.get("rows"))
        # 最新数据日期：按日文件取最后一个 d_ 文件；否则用 catalog date 状态/mtime
        latest_date = ""
        if files and files[-1].stem.startswith("d_"):
            latest_date = files[-1].stem[2:]
        lag = _lag_days(latest_date, TODAY) if latest_date else None
        rows.append({"name": d.name, "label": label, "method": method, "layer": "接口同步",
                     "status": status, "note": note, "files": files_n, "size_mb": mb,
                     "size_h": _fmt_size(mb), "rows": rows_n,
                     "rows_h": ("%s 万" % round(rows_n / 10000, 1)) if rows_n and rows_n >= 10000 else (str(rows_n) if rows_n else "—"),
                     "range": range_s, "last_update": last_update, "latest_date": latest_date,
                     "lag_days": lag, "today_new": today_new,
                     "registered": bool(table), "manifest": table.get("manifest_status") or "—",
                     "date_status": table.get("date_status") or "—",
                     "contract": d.name in CONTRACTS,
                     "hypothesis_ids": SOURCE_HYP.get(d.name, []),
                     "gap_note": "", "next_action": "—" if status == "done" else note})
    return rows


def _parquet_rows(d: Path, cat_row) -> int | None:
    """条数：优先 catalog rows；文件少则精确 footer 统计；多则抽样估算。"""
    if isinstance(cat_row, int) and cat_row > 0:
        return cat_row
    files = sorted(d.glob("*.parquet"))
    if not files:
        return None
    try:
        import pyarrow.parquet as pq
        if len(files) <= 300:
            return sum(pq.ParquetFile(f).metadata.num_rows for f in files)
        sample = files[:: max(1, len(files) // 80)][:80]
        rows = sum(pq.ParquetFile(f).metadata.num_rows for f in sample)
        return int(rows / len(sample) * len(files))
    except Exception:  # noqa: BLE001
        return None


def _sqlite_stats(path: Path, table: str, ts_col: str) -> dict:
    import sqlite3
    try:
        con = sqlite3.connect("file:%s?mode=ro" % str(path).replace("\\", "/"), uri=True, timeout=10)
        cnt = con.execute("SELECT count(*) FROM %s" % table).fetchone()[0]
        latest = con.execute("SELECT max(%s) FROM %s" % (ts_col, table)).fetchone()[0]
        con.close()
        return {"rows": cnt, "latest": str(latest or "")[:19]}
    except Exception:  # noqa: BLE001
        return {"rows": None, "latest": None}


def _lag_days(latest_date: str, today: str) -> int | None:
    try:
        fmt = "%Y%m%d" if len(latest_date) == 8 and latest_date.isdigit() else None
        if fmt:
            from datetime import datetime as _dt
            d = _dt.strptime(latest_date, fmt)
        else:
            from datetime import datetime as _dt
            d = _dt.fromisoformat(latest_date[:19])
        return max(0, (_dt.now() - d).days)
    except Exception:  # noqa: BLE001
        return None


def _container_stats() -> list[dict]:
    """容器库：zhice-PG / quant-meta-PG / thunder_model-MySQL（尽力而为，失败降级）。"""
    out = []
    def run(cmd, timeout=25):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=timeout)
            return (r.stdout or "").strip()
        except Exception:  # noqa: BLE001
            return ""
    # zhice PG：分钟线/日线/决策条数 + 最新日期
    q = ("SELECT count(*)||'|'||to_char(max(bar_time),'YYYY-MM-DD') FROM v2_bar_minute;")
    r1 = run(["docker", "exec", "zhice-db", "psql",
              "postgresql://zhice:zhice_pass@127.0.0.1:5432/zhice_os_v2", "-Atc", q])
    q2 = "SELECT count(*)||'|'||to_char(max(bar_time),'YYYY-MM-DD') FROM v2_bar_daily;"
    r2 = run(["docker", "exec", "zhice-db", "psql",
              "postgresql://zhice:zhice_pass@127.0.0.1:5432/zhice_os_v2", "-Atc", q2])
    if r1 or r2:
        parts = []
        rows_n = 0
        latest = ""
        for raw in (r1, r2):
            if "|" in raw:
                c, d = raw.split("|", 1)
                try:
                    rows_n += int(c)
                except ValueError:
                    pass
                latest = max(latest, d or "")
        parts.append("分钟线+日线合计 %s 条，最新 %s" % (("%.1f 万" % (rows_n / 10000)) if rows_n else "—", latest or "—"))
        out.append({"name": "zhice_pg", "label": "智策OS·容器库(PostgreSQL)", "method": "容器库",
                    "layer": "容器库", "status": "done", "note": "生产库：分钟线/日线/决策账本；B 批优化完成",
                    "files": 1, "size_mb": 8379, "size_h": "8.4 GB", "rows": rows_n,
                    "rows_h": ("%s 万" % round(rows_n / 10000, 1)) if rows_n else "—",
                    "range": "2026-08-11 起（分钟线）", "last_update": TODAY,
                    "latest_date": latest, "lag_days": _lag_days(latest, TODAY) if latest else None,
                    "today_new": 0, "registered": True, "manifest": "—", "date_status": "—",
                    "contract": False, "hypothesis_ids": [], "gap_note": "分钟线自 2026-08-11 起（历史更早数据未采集）",
                    "next_action": "长期沉淀；历史分钟线不在覆盖内"})
    # quant_meta PG
    q3 = ("SELECT count(*) FROM information_schema.tables WHERE table_schema='public';")
    r3 = run(["docker", "exec", "quant-pg", "psql",
              "postgresql://quant_user:quant_password_123@127.0.0.1:5432/quant_meta", "-Atc", q3])
    if r3:
        out.append({"name": "quant_meta_pg", "label": "quant元数据·容器库(PostgreSQL)", "method": "容器库",
                    "layer": "容器库", "status": "done", "note": "元数据/因子库；B 批清理 613MB",
                    "files": 1, "size_mb": 415, "size_h": "415 MB", "rows": None, "rows_h": "—",
                    "range": "元数据表 %s 张" % r3.strip(), "last_update": TODAY, "latest_date": "",
                    "lag_days": None, "today_new": 0, "registered": True, "manifest": "—",
                    "date_status": "—", "contract": False, "hypothesis_ids": [],
                    "gap_note": "", "next_action": "—"})
    return out


def load_other_sources() -> list[dict]:
    specs = [
        ("news.db", "新闻快讯·本地库(SQLite)", "news", "ts", "爬虫采集", "in_progress",
         "保留 365 天；历史不可回填（接口深度约 1 周）", REPO / "news_collector" / "news.db", ["H5", "H6"]),
        ("xueqiu_info.db", "雪球舆情·本地库(SQLite)", "xq_status", "created_at", "爬虫采集", "in_progress",
         "2012-2026；2021+ 稀疏，已加 3 轮抓取", DATA / "xueqiu" / "xueqiu_info.db", ["H5"]),
        ("jev_audit.db", "JEV判定账本·本地库(SQLite)", "dec_audit", "ts", "文件导入", "in_progress",
         "判定账本；后验/company 覆盖不足（见缺口）", REPO / "agent_platform" / "data" / "jev_audit" / "jev_audit.db", ["H6"]),
        ("ann_history.db", "公告历史·本地库(SQLite)", "ann", "notice_date", "文件导入", "in_progress",
         "49 万条（2026-02 起）", REPO / "zerobound-kg" / "data" / "ann_history" / "ann_history.db", ["H1", "H11"]),
        ("evidence_chain.db", "证据链·本地库(SQLite)", "", "", "文件导入", "done",
         "资格/取证底账", DATA / "evidence_chain.db", []),
    ]
    rows = []
    for name, label, table, ts_col, method, status, note, path, hyps in specs:
        if not path.exists():
            continue
        mb = round(path.stat().st_size / 1e6, 2)
        stats = _sqlite_stats(path, table, ts_col) if table else {"rows": None, "latest": None}
        gap = ""
        if name == "jev_audit.db":
            import sqlite3
            try:
                con = sqlite3.connect("file:%s?mode=ro" % str(path).replace("\\", "/"), uri=True, timeout=10)
                total = con.execute("SELECT count(*) FROM dec_audit").fetchone()[0]
                labeled = con.execute("SELECT count(*) FROM dec_audit WHERE ret IS NOT NULL").fetchone()[0]
                empty = con.execute("SELECT count(*) FROM dec_audit WHERE companies IS NULL OR companies IN ('','[]')").fetchone()[0]
                con.close()
                gap = "判定 %d 条 / 有后验 %d 条（%.1f%%）/ companies 空 %d 条（%.0f%%）" % (
                    total, labeled, labeled * 100.0 / max(1, total), empty, empty * 100.0 / max(1, total))
            except Exception:  # noqa: BLE001
                gap = "判定账本；覆盖未统计"
        elif name == "ann_history.db":
            gap = "仅 2026-02 起（历史公告需用 anns_d 标题源）"
        elif name == "news.db":
            gap = "仅约 1 个月历史；历史无法回填"
        elif name == "xueqiu_info.db":
            gap = "2021+ 密度低（月均数十条）"
        rows.append({"name": name, "label": label, "method": method, "layer": "本地库",
                     "status": status, "note": note, "files": 1, "size_mb": mb,
                     "size_h": _fmt_size(mb), "rows": stats.get("rows"),
                     "rows_h": (str(stats.get("rows")) if stats.get("rows") else "—"),
                     "range": name, "last_update": _mtime_date(path),
                     "latest_date": (stats.get("latest") or "")[:19],
                     "lag_days": _lag_days(stats.get("latest") or "", TODAY) if stats.get("latest") else None,
                     "today_new": 1 if _mtime_date(path) == TODAY else 0,
                     "registered": True, "manifest": "—", "date_status": "—",
                     "contract": False, "hypothesis_ids": hyps,
                     "gap_note": gap, "next_action": note if status != "done" else "—"})
    return rows + _container_stats()


REPORT_TITLES = [
    ("H14", "H14 质量反向（质量陷阱规避）"),
    ("H13", "H13 多因子复核（资金流+反转+市值+价值+质量）"),
    ("H12", "H12 盈余方向事件研究"),
    ("H11dir", "H11 盈余方向事件研究（旧版）"),
    ("H11", "H11 三年公告事件研究"),
    ("H10", "H10 数据质量敏感性（扰动框架）"),
    ("H9_tradability", "H9 可交易性检验（缺口 vs 日内）"),
    ("H9", "H9 全球指数领先滞后"),
    ("H8", "H8 行业资金轮动（PIT 行业）"),
    ("H7", "H7 财务因子分层"),
    ("H6", "H6 JEV 决策归因"),
    ("H5", "H5 舆情领先滞后"),
    ("H4", "H4 资金流背离"),
    ("H3", "H3 龙虎榜机构席位"),
    ("H2", "H2 业绩预告漂移（PEAD）"),
    ("H1", "H1 公告事件超额收益"),
]


def _report_title(name: str) -> str:
    for key, zh in REPORT_TITLES:
        if name.startswith(key):
            return zh
    return "研究报告"


def load_reports() -> list[dict]:
    rows = []
    for f in sorted((REPO / "research" / "reports").glob("*"), key=lambda x: -x.stat().st_mtime):
        if f.suffix not in (".md", ".json"):
            continue
        m = datetime.fromtimestamp(f.stat().st_mtime)
        prefix = f.name.split("_")[0]
        rows.append({"name": f.name, "title_zh": _report_title(f.name),
                     "kind": "报告正文" if f.suffix == ".md" else "原始数据",
                     "size_kb": round(f.stat().st_size / 1024, 1),
                     "date": m.strftime("%Y-%m-%d"), "time": m.strftime("%H:%M"),
                     "hid": prefix if prefix.startswith("H") else ""})
    return rows[:40]


def load_tasks() -> dict:
    f = REPO / ".worktrees" / "tasks.json"
    empty = {"doing": [], "today_pilot": [], "today_merged": [], "counts": {}}
    if not f.exists():
        return empty
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except ValueError:
        return empty
    tasks = d.get("tasks", d)
    doing, pilot_today, merged_today, counts = [], [], [], {}
    pilot_today_data, merged_today_data = [], []
    for tid, t in tasks.items():
        if not isinstance(t, dict):
            continue
        st = (t.get("status") or "").lower()
        counts[st] = counts.get(st, 0) + 1
        item = {"id": tid, "status": st, "owner": t.get("owner") or "",
                "desc": (t.get("desc") or "")[:120]}
        if st in ("doing", "review"):
            doing.append(item)
        merged = t.get("merged") or ""
        created = t.get("created") or ""
        if st in ("doing", "review") and (created.startswith(TODAY) or merged.startswith(TODAY)):
            pilot_today.append(item)
            if _data_related(t):
                pilot_today_data.append(item)
        if merged.startswith(TODAY):
            merged_today.append(item)
            if _data_related(t):
                merged_today_data.append(item)
    return {"doing": doing[:60], "doing_data": [x for x in doing if _data_related(
        next((tt for kk, tt in tasks.items() if kk == x["id"]), {}))][:30],
            "today_pilot": pilot_today, "today_pilot_data": pilot_today_data,
            "today_merged": merged_today, "today_merged_data": merged_today_data,
            "counts": counts}


def load_backup() -> dict:
    bp = REPO / "backups"
    out = {"latest": None, "products": [], "receipt": None, "age_hours": None}
    if not bp.exists():
        return out
    gens = sorted(d for d in bp.glob("2026-*") if d.is_dir())
    if not gens:
        return out
    last = gens[-1]
    out["latest"] = last.name
    out["receipt"] = next((f.name for f in last.glob("*receipt*.txt")), None)
    for f in sorted(last.glob("*.tar.gz")) + sorted(last.glob("*.gz*")):
        out["products"].append({"name": f.name, "mb": round(f.stat().st_size / 1e6, 1)})
    try:
        dt = datetime.strptime(last.name.split("_")[0], "%Y-%m-%d")
        out["age_hours"] = round((datetime.now() - dt).total_seconds() / 3600, 1)
    except ValueError:
        pass
    return out


def load_supervisor() -> dict:
    try:
        r = subprocess.run([PY, "-X", "utf8", "tools/automation_supervisor.py", "--json"],
                           cwd=str(REPO), capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=180)
        if r.returncode in (0, 2) and r.stdout.strip():
            d = json.loads(r.stdout)
            return {"red": d.get("red") or [], "generated_at": d.get("generated_at")}
    except Exception:  # noqa: BLE001
        pass
    return {"red": [], "generated_at": None}


def load_approvals() -> dict:
    f = REPO / "agent_platform" / "data" / "approvals.jsonl"
    rows = []
    if f.exists():
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    return {"count": len(rows), "today": sum(1 for r in rows if str(r.get("ts") or "").startswith(TODAY)),
            "latest": rows[-5:]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    cat = json.loads((DATA / "catalog.json").read_text(encoding="utf-8"))
    reg = json.loads((DATA / "warehouse_registry.json").read_text(encoding="utf-8"))
    sources = load_sources(cat) + load_other_sources()
    hyps = load_hypotheses()
    reports = load_reports()
    tasks = load_tasks()
    backup = load_backup()
    sup = load_supervisor()
    approvals = load_approvals()
    status_cnt = {}
    for s in sources:
        status_cnt[s["status"]] = status_cnt.get(s["status"], 0) + 1
    hyp_cnt = {}
    for h in hyps:
        hyp_cnt[h["status"]] = hyp_cnt.get(h["status"], 0) + 1
    qual_unknown = sum(1 for t in cat["tables"].values() if isinstance(t, dict)
                       and (t.get("verification_status") or "unknown") in ("unknown", ""))
    contracts_n = len(list((REPO / "agent_platform" / "catalogs" / "contracts").glob("*.yaml")))
    total = len(sources)
    done = status_cnt.get("done", 0)
    snap = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "today": TODAY,
        "catalog": {"version": cat.get("version"), "tables": len(cat["tables"]),
                    "registry": {"physical": reg.get("physical_count"), "display": reg.get("display_count")},
                    "contracts": contracts_n, "qualification_unknown": qual_unknown,
                    "manifest": {k: sum(1 for t in cat["tables"].values()
                                        if isinstance(t, dict) and t.get("manifest_status") == k)
                                 for k in ("closed", "not_applicable", "open", "missing")}},
        "cockpit": {"sources_total": total, "sources_done": done,
                    "sources_in_progress": status_cnt.get("in_progress", 0),
                    "sources_mining": status_cnt.get("mining", 0),
                    "sources_pending": status_cnt.get("pending", 0),
                    "coverage_pct": round(done * 100.0 / total, 1) if total else 0,
                    "new_today": sum(1 for s in sources if s.get("today_new")),
                    "pilot_today": len(tasks["today_pilot_data"]),
                    "merged_today": len(tasks["today_merged_data"]),
                    "hypotheses": len(hyps), "hypotheses_by_status": hyp_cnt,
                    "reports_total": len(reports),
                    "reports_today": sum(1 for r in reports if r["date"] == TODAY),
                    "approvals_today": approvals["today"],
                    "risks": [{"item": g[0], "status": g[1], "note": g[2]} for g in GOV_ITEMS if g[1] != "done"],
                    "backup_latest": backup["latest"], "backup_age_hours": backup["age_hours"],
                    "supervisor_red": sup["red"]},
        "sources": sources,
        "method_counts": {m: sum(1 for s in sources if s.get("method") == m) for m in METHOD_ORDER},
        "new_today_sources": [s for s in sources if s.get("today_new")],
        "hypotheses": hyps,
        "reports": reports,
        "tasks": tasks,
        "governance": [{"item": a, "status": b, "note": c, "date": d} for a, b, c, d in GOV_ITEMS],
        "roadmap": [{"phase": a, "status": b, "date": c, "note": d} for a, b, c, d in ROADMAP],
        "ops": {"backup": backup, "supervisor": sup, "approvals": approvals},
        "layers": LAYER_ORDER, "status_order": STATUS_ORDER,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.json:
        print(json.dumps(snap["cockpit"], ensure_ascii=False, indent=1))
    else:
        print("snapshot:", OUT, "size=", OUT.stat().st_size)
        print("cockpit:", json.dumps(snap["cockpit"], ensure_ascii=False)[:300])
    return 0


if __name__ == "__main__":
    sys.exit(main())
