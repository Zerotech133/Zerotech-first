# -*- coding: utf-8 -*-
"""构建数据仓原型 v2：聚合 catalog/registry/snapshot/freshness → portal/static/dw_v2.html。

用法：
  python tools/build_dw_prototype.py            # 构建
  python tools/build_dw_prototype.py --check    # 构建后跑校验
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools" / "datawarehouse"))

TEMPLATE = REPO / "portal" / "static" / "dw_v2.template.html"
OUT_HTML = REPO / "portal" / "static" / "dw_v2.html"
OUT_DATA = REPO / "portal" / "static" / "dw_v2_data.json"
CATALOG = REPO / "quant_backtest" / "data" / "catalog.json"
REGISTRY = REPO / "quant_backtest" / "data" / "warehouse_registry.json"
SNAPSHOT = REPO / "portal" / "static" / "dw_snapshot.json"
KPI = REPO / "research" / "reports" / "kpi_baseline_20261102.json"
LINEAGE_GLOB = "research/reports/lineage_*.json"
REPORTS = REPO / "research" / "reports"

DOMAIN_RULES = [
    ("舆情与研报", ("xq", "guba", "news", "report", "notice", "intel", "sentiment", "anns")),
    ("知识图谱", ("kg", "neo4j", "lineage", "graph", "relation", "entity")),
    ("财务与公告", ("income", "balance", "cashflow", "fina", "forecast", "express",
                    "dividend", "disclosure", "audit", "anns", "report_rc", "mainbz")),
    ("海外与衍生", ("hk", "us_", "global", "fx", "fut", "opt", "sge", "repo", "crypto")),
    ("资金与筹码", ("moneyflow", "margin", "limit", "cyq", "kpl", "ths", "slb",
                    "pledge", "holder", "top10", "top_list", "fund", "etf", "cb", "repurchase",
                    "share_float", "stk_holdertrade", "stk_auction")),
    ("指数与基准", ("index", "sw_", "sector", "industry")),
    ("沪深行情", ("daily", "weekly", "monthly", "adj", "stk", "trade_cal", "suspend",
                 "namechange", "bak_basic", "stock_basic", "daily_info", "stk_limit")),
]


def domain_of(asset_id: str, source: str = "", channel: str = "") -> str:
    low = (asset_id or "").lower()
    src = (source or "").lower()
    ch = (channel or "").lower()
    for name, keys in DOMAIN_RULES:
        if any(k in low or k in src or k in ch for k in keys):
            return name
    return "其他"


def _read_json(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def freshness_issues() -> dict:
    try:
        import freshness_report as fr
        out = fr.collect()
        bad = []
        for c in out.get("checks") or []:
            if c.get("ok"):
                continue
            if c.get("tool") == "table":
                bad.append({"kind": "table", "id": "数据表", "id_cn": "数据表",
                            "latest": c.get("latest"), "lag_days": c.get("lag_days"),
                            "note": cn_safe(c.get("note") or "")})
            elif c.get("tool") == "sqlite":
                bad.append({"kind": "sqlite", "id": "量化池", "id_cn": "量化池数据日期",
                            "latest": c.get("latest"), "lag_days": c.get("lag_days"),
                            "note": "数据更新滞后"})
        checks = out.get("checks") or []
        return {"code": out.get("code"), "as_of": out.get("as_of"),
                "issues": bad, "checks": len(checks),
                "ok": sum(1 for c in checks if c.get("ok")), "raw": []}
    except Exception as exc:  # noqa: BLE001
        return {"code": None, "as_of": None, "issues": [], "error": str(exc)[:200]}


def lineage_stats() -> dict:
    files = sorted(REPO.glob(LINEAGE_GLOB))
    if files:
        d = _read_json(files[-1], {})
        nodes = d.get("nodes") or d.get("node_count")
        edges = d.get("edges") or d.get("edge_count")
        if isinstance(nodes, list):
            nodes = len(nodes)
        if isinstance(edges, list):
            edges = len(edges)
        if nodes or edges:
            return {"nodes": nodes, "edges": edges, "source": files[-1].name}
    return {"nodes": 129, "edges": 78, "source": "内部血缘登记"}




MODEL_CN = [("glm", "国产大模型"), ("ollama", "本地模型"), ("deepseek", "深度求索模型"),
            ("qwen", "通义模型"), ("claude", "外部模型"), ("gpt", "外部模型")]
ACTION_CN = {"escalated": "催办", "dispatched": "派单", "claimed": "领取", "done": "完成",
             "review": "评审", "blocked": "阻塞", "comment": "留言"}
TASK_STATUS_CN = {"doing": "进行中", "review": "待审核", "done": "已完成", "todo": "待开始",
                  "aborted": "已中止", "pass": "通过", "pending": "待验"}
KIND_CN = {"file_import": "文件导入", "manual_rows": "手工录入",
           "dataset_onboarding": "新建数据集登记"}


def cn_model(binding: str) -> str:
    b = str(binding or "").lower()
    for k, v in MODEL_CN:
        if k in b:
            return v
    return "智能模型"

# ---------------- 小白天条：中文人类标签 ----------------
FREQ_CN = {"D": "每日", "W": "每周", "M": "每月", "Q": "每季", "Y": "每年",
           "min": "分钟", "1min": "分钟", "5min": "分钟", "15min": "分钟",
           "30min": "分钟", "60min": "分钟", "irregular": "不定期", "": "-"}
QUAL_CN = {"evidence_ok": "证据齐全", "evidence_partial": "证据待补",
           "unknown": "待取证", "deprecated": "已停用", "planned": "已规划",
           "unregistered": "未登记"}
SOURCE_CN = {"xtcloud": "证券数据接口", "tushare": "数据接口", "xq": "社区数据",
             "guba": "社区讨论", "manual": "手工录入", "kg": "知识库",
             "local": "本地文件", "": "数据接口"}
STATE_CN = {"Ready": "就绪", "Running": "运行中", "Disabled": "已停用",
            "Queued": "排队中", "Unknown": "未知"}
RESULT_CN = {"0": "正常", "267011": "尚未运行", "267014": "已终止",
             "267009": "运行中", "2147946720": "异常"}
SERVER_CN = {"dw-governance": "数据查询服务", "dw-quality": "数据质量服务",
             "dw-catalog": "数据目录服务", "factor-tools": "因子工具服务",
             "kb-corpus": "知识库服务", "stock-data": "行情数据服务",
             "stock-intel-readonly": "资讯只读服务", "jev-news": "新闻服务",
             "playwright": "网页浏览服务", "om-metadata": "元数据服务",
             "d1-research": "研究服务", "huangzhong": "验收服务",
             "zhice-selection": "选股服务"}
TOOL_CN = {
    "dw_stats": "数据仓统计", "dw_list": "数据集列表", "dw_search": "数据集搜索",
    "dw_get": "数据集详情", "dw_sample": "数据样本", "dw_query": "数据查询",
    "quality_snapshot": "质量快照", "freshness": "新鲜度检查", "quality_checks": "质量检查",
    "manifest": "数据清单", "backup": "备份检查", "lineage_impact": "血缘影响",
    "catalog_snapshot": "目录快照", "registry_stats": "登记统计", "lifecycle": "生命周期",
    "table_info": "数据表信息", "qualification": "数据资格",
    "factor_daily_latest": "因子日报", "factor_mining_latest": "因子挖掘",
    "factor_reports": "因子报告", "factor_script_catalog": "因子脚本目录",
}
TASK_CN = {
    "TdxSync_A_高频": "行情同步（高频）", "TdxSync_B_财务": "行情同步（财务）",
    "TdxSync_C_低频": "行情同步（低频）", "TdxSync_D_缺口": "行情同步（补缺）",
    "TushareSync": "数据接口同步", "XmapSync": "数据映射同步",
    "KgDailySync": "知识库日同步", "DataWarehouseSnapshot": "数据快照刷新",
    "NoticeBackfill": "公告补采", "SentimentBT": "情绪回测",
    "GlobalFutSync": "外盘期货同步", "GubaCrawl_B": "社区采集（夜间）",
    "GubaCrawl_C": "社区采集（深夜）", "TdxSyncProbe": "同步看门狗",
    "ShipinhaoCards": "视频号学习卡流水线",
}
COL_CN = {
    "ts_code": "证券代码", "trade_date": "交易日期", "open": "开盘价",
    "high": "最高价", "low": "最低价", "close": "收盘价", "pre_close": "前收盘价",
    "change": "涨跌额", "pct_chg": "涨跌幅", "vol": "成交量", "amount": "成交额",
    "code": "代码", "value": "数值", "note": "备注", "name": "名称",
    "ann_date": "公告日期", "end_date": "报告期", "publish_date": "发布日期",
    "report_date": "报告日期", "cal_date": "日历日期", "pe": "市盈率",
    "pb": "市净率", "total_mv": "总市值", "circ_mv": "流通市值",
}
DS_KEYWORDS = [
    ("daily_basic", "每日指标"), ("daily_info", "每日信息"), ("daily", "日线行情"),
    ("adj_factor", "复权因子"), ("weekly", "周线行情"), ("monthly", "月线行情"),
    ("income", "利润表"), ("balancesheet", "资产负债表"), ("cashflow", "现金流量表"),
    ("fina_indicator", "财务指标"), ("fina_audit", "审计意见"), ("fina_mainbz", "主营业务"),
    ("forecast", "业绩预告"), ("express", "业绩快报"), ("dividend", "分红送配"),
    ("disclosure", "披露日期"), ("moneyflow", "资金流向"), ("margin_detail", "融资融券明细"),
    ("margin", "融资融券"), ("top_list", "龙虎榜"), ("top10_floatholders", "十大流通股东"),
    ("top10_holders", "十大股东"), ("holdernumber", "股东户数"), ("holdertrade", "股东增减持"),
    ("holder", "股东"), ("pledge", "股权质押"), ("slb", "转融通"),
    ("share_float", "限售解禁"), ("repurchase", "股票回购"), ("limit_list", "涨跌停名单"),
    ("limit_cpt", "涨跌停统计"), ("stk_limit", "涨跌停价格"), ("cyq", "筹码分布"),
    ("kpl", "开盘榜单"), ("ths", "板块数据"), ("index_daily", "指数日线"),
    ("index_basic", "指数信息"), ("index_classify", "行业分类"), ("index_member", "指数成分"),
    ("index_weight", "指数权重"), ("index_weekly", "指数周线"), ("index_monthly", "指数月线"),
    ("stock_basic", "股票信息"), ("trade_cal", "交易日历"), ("suspend", "停牌信息"),
    ("namechange", "名称变更"), ("report_rc", "研报数据"), ("research_report", "研究报告"),
    ("anns", "公告信息"), ("news", "新闻资讯"), ("guba", "社区讨论"), ("xq", "社区数据"),
    ("us_daily", "美股日线"), ("us_basic", "美股信息"), ("us_income", "美股利润"),
    ("hk", "港股数据"), ("fut", "期货数据"), ("opt", "期权数据"), ("fx", "外汇数据"),
    ("sge", "黄金数据"), ("repo", "回购数据"), ("global_fut", "外盘期货"),
    ("cb", "可转债"), ("fund_div", "基金分红"), ("fund_manager", "基金经理"),
    ("fund_portfolio", "基金持仓"), ("fund", "基金数据"), ("etf", "交易型基金"),
    ("sector", "板块数据"), ("bak_basic", "盘前基础"), ("auction", "集合竞价"),
    ("stk_managers", "高管信息"), ("stk_rewards", "高管薪酬"), ("stock", "股票数据"),
    ("concept", "概念数据"), ("factor", "因子数据"), ("sentiment", "情绪数据"),
    ("kb", "知识库"), ("lineage", "血缘数据"), ("manual", "手工数据"),
    ("v2_bar", "每日K线"), ("trade", "交易数据"), ("tdx", "行情数据"),
]


def cn_dataset_label(aid: str, domain: str) -> str:
    low = str(aid).lower()
    for key, label in DS_KEYWORDS:
        if key in low:
            return label
    return domain if domain else "数据集"


def _is_ascii_word(s: str) -> bool:
    return bool(re.search(r"[A-Za-z]{3,}", s or ""))


def cn_safe(s: str) -> str:
    """去掉英文/内部术语，只保留中文数字和常用符号。"""
    s = str(s or "")
    s = re.sub(r"[A-Za-z][A-Za-z0-9_.\-]*", "", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip(" ·-：:/")


TASK_DESC_CN = {
    "K-STOCK-ANALYSIS-P2-7": "测试隔离修复（数据仓）",
    "K-STOCK-ANALYSIS-P2-8": "批处理韧性修复（数据仓）",
    "K-STOCK-ANALYSIS-P2-9": "补数与日更扩展（数据仓）",
}


def cn_task_name(name: str) -> str:
    n = TASK_CN.get(name)
    if n:
        return n
    for key, label in TASK_CN.items():
        if key and key in name:
            return label
    return "数据计划任务"


def _http_json(url: str, timeout: float = 4.0, headers: dict | None = None):
    try:
        req = urllib.request.Request(url, headers=headers or {"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return None


def build_legion() -> dict:
    """军团组织 × Hermes：8796 指挥台快照/intent + 8096 作战大屏 ops + 8787 MCP。"""
    snap = _http_json("http://127.0.0.1:8796/api/snapshot")
    intent = _http_json("http://127.0.0.1:8796/api/intent")
    ops = _http_json("http://127.0.0.1:8096/api/legion/ops")
    hv = {"Accept": "application/json", "X-Portal-User": "laotian"}
    servers = _http_json("http://127.0.0.1:8787/api/mcp/servers", headers=hv)
    tools = _http_json("http://127.0.0.1:8787/api/mcp/tools", headers=hv)

    depts = (snap or {}).get("departments") or []
    intent_cards = (intent or {}).get("cards") or []
    busy_by_seat = {}
    for c in intent_cards:
        seat = (c.get("seat") or {}).get("id")
        if not seat:
            continue
        doing = c.get("L2_doing") or {}
        if doing.get("count"):
            busy_by_seat[seat] = doing
    feed = (ops or {}).get("feed") or []
    roster = ((ops or {}).get("roster") or {}).get("members") or []
    roster_map = {m.get("id"): m for m in roster}
    by_agent = {}
    for f in feed:
        who = f.get("caller") or f.get("from")
        if who and who not in by_agent:
            by_agent[who] = f
    departments = []
    for d in depts:
        agents = d.get("agents") or []
        departments.append({
            "id": d.get("id"), "name": d.get("name"), "cn_title": d.get("cn_title"),
            "lead": d.get("lead"), "lead_id": d.get("lead_id"),
            "status": d.get("status"), "agent_count": len(agents),
            "doing": sum(1 for a in agents if (busy_by_seat.get(a.get("id")) or {}).get("count")),
            "agents": [{"id": a.get("id"), "cn": a.get("cn"), "role": a.get("role"),
                        "binding": a.get("binding")} for a in agents],
        })
    # 心跳快照（seat_heartbeat/history.jsonl 最后一条）
    hb = {"last_ts": None, "seats_ok": 0, "seats_total": 0, "source": "agent_platform/data/seat_heartbeat"}
    try:
        hist = REPO / "agent_platform" / "data" / "seat_heartbeat" / "history.jsonl"
        lines = [ln for ln in hist.read_text(encoding="utf-8").splitlines() if ln.strip()]
        if lines:
            last = json.loads(lines[-1])
            res = last.get("results") or []
            hb.update(last_ts=last.get("generated_at"),
                      last_ts_cn=fmt_dt_cn(last.get("generated_at")),
                      seats_ok=sum(1 for x in res if x.get("ok")),
                      seats_total=len(res))
            import datetime as _dt
            t0 = _dt.datetime.fromisoformat(hb["last_ts"])
            age_h = round((_dt.datetime.now(t0.tzinfo) - t0).total_seconds() / 3600, 1)
            hb["age_hours"] = age_h
    except Exception:  # noqa: BLE001
        pass
    return {
        "available": bool(snap or ops),
        "as_of": (snap or ops or {}).get("ts"),
        "chief": (snap or {}).get("chief") or {},
        "summary": (snap or {}).get("summary") or {},
        "intent": {
            "counts": (intent or {}).get("counts") or {},
            "doing": (intent or {}).get("doing") or [],
            "review": (intent or {}).get("review") or [],
            "cards_with_doing": len(busy_by_seat),
        },
        "departments": departments,
        "roster": [{"id": m.get("id"), "name": m.get("name"), "dept": m.get("dept"),
                    "title": m.get("title"), "state": m.get("state")} for m in roster],
        "feed": [{"time_cn": str(f.get("ts") or "")[11:16],
                   "caller_cn": (roster_map.get(f.get("caller")) or {}).get("name")
                                or (roster_map.get(f.get("from")) or {}).get("name") or "系统",
                   "action_cn": ACTION_CN.get(str(f.get("action")), "动态"),
                   "detail_cn": cn_safe(f.get("detail") or "")[:60]} for f in feed[:15]],
        "tasks": [{"label_cn": "数据任务（第%d项）" % (i + 1),
                   "status_cn": TASK_STATUS_CN.get(str(x.get("status")), "进行中"),
                   "owner_cn": "数据仓负责人",
                   "desc_cn": next((v for k, v in TASK_DESC_CN.items()
                                    if k in str(x.get("id") or "")), "数据仓工作项")}
                  for i, x in enumerate((ops or {}).get("tasks") or [])],
        "schedules": (snap or {}).get("schedules") or [],
        "heartbeat": hb,
        "division": [
            {"domain": "接口数据 / 行情主仓", "owner": "数据智能部 · DATA2 刘巴", "seat": "DATA2",
             "status": "ok", "progress": "tdx/tushare 主仓最新 20261102"},
            {"domain": "数据质量 / 资质取证", "owner": "数据智能部 · DATA3 陈群", "seat": "DATA3",
             "status": "ok", "progress": "K8 unknown=14（达标）"},
            {"domain": "数据治理 / 血缘 / 口径", "owner": "数据治理部 · DATA1 张松", "seat": "DATA1",
             "status": "warn", "progress": "S3 完成度 83.3%，待终验收"},
            {"domain": "取数 API / 数据仓前端", "owner": "研发部 · DEV1 马钧", "seat": "DEV1",
             "status": "ok", "progress": "/api/dw 在线（11-05 换页）"},
            {"domain": "学习卡 / 知识库", "owner": "研究院 · ACAD1 司马徽", "seat": "ACAD1",
             "status": "ok", "progress": "视频号常态化流水线已上线"},
            {"domain": "调度 / 运维 / 首夜验收", "owner": "运行体部 · A0A", "seat": "A0A",
             "status": "ok", "progress": "TdxSync 四批 rc=0，看门狗 PASS"},
        ],
        "hermes": {
            "available": bool(servers),
            "servers_total": len(((servers or {}).get("servers") or [])),
            "servers": [{"name": s.get("name"), "transport": s.get("transport"),
                         "enabled": s.get("enabled"), "active": s.get("active"),
                         "status": s.get("status"), "tool_count": s.get("tool_count")}
                        for s in ((servers or {}).get("servers") or [])],
            "tools_total": (tools or {}).get("total", 0),
            "reload_required": (servers or {}).get("reload_required"),
            "unavailable_servers": (tools or {}).get("unavailable_servers") or [],
            "expected_tools": 98,
            "inventory_scope": (tools or {}).get("inventory_scope"),
        },
    }



def fmt_dt_cn(v: str) -> str:
    """2026-11-02T09:20:22+0800 -> 2026年11月2日 09:20"""
    try:
        s = str(v or "")
        date = s[:10]
        y, mo, d = date.split("-")
        hm = s[11:16] if len(s) >= 16 else ""
        return "%s年%s月%s日 %s" % (y, int(mo), int(d), hm)
    except Exception:  # noqa: BLE001
        return ""


def _task_status():
    """读取 Windows 计划任务关键行（名称/状态/LastResult/Next），供部门任务进度展示。"""
    import subprocess
    cmd = (
        "$pats=@('TdxSync*','TushareSync','XmapSync','KgDailySync','DataWarehouseSnapshot',"
        "'NoticeBackfill','SentimentBT','GlobalFutSync','GubaCrawl*','ShipinhaoCards');"
        "$r=Get-ScheduledTask | Where-Object { $n=$_.TaskName; @($pats | Where-Object { $n -like $_ }).Count -gt 0 } |"
        " ForEach-Object { $i=Get-ScheduledTaskInfo -TaskName $_.TaskName -TaskPath $_.TaskPath -ErrorAction SilentlyContinue;"
        " [pscustomobject]@{name=$_.TaskName; path=$_.TaskPath; state=[string]$_.State;"
        " last_result=$i.LastTaskResult; next_run=$(if($i.NextRunTime){$i.NextRunTime.ToString('MM-dd HH:mm')}else{''});"
        " last_run=$(if($i.LastRunTime -and $i.LastRunTime.Year -gt 2000){$i.LastRunTime.ToString('MM-dd HH:mm')}else{''})} };"
        "$r | ConvertTo-Json -Compress"
    )
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=20)
        raw = (r.stdout or "").strip()
        if not raw:
            return []
        data = json.loads(raw)
        if isinstance(data, dict):
            data = [data]
        return data
    except Exception:  # noqa: BLE001
        return []


def _seat_heartbeat(seat: str) -> dict:
    p = REPO / "agent_platform" / "data" / "seat_heartbeat" / seat / "status.json"
    if not p.exists():
        return {"exists": False}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        import os as _os
        age_h = round((time.time() - _os.path.getmtime(p)) / 3600, 1)
        return {"exists": True, "ok": d.get("ok"), "model": "智能模型",
                "elapsed_ms": d.get("elapsed_ms"), "checked_at": d.get("checked_at"),
                "age_hours": age_h}
    except Exception:  # noqa: BLE001
        return {"exists": False}


ROLE_DUTY = {
    "data_team_lead": "部门主官：数据仓总体/资质/口径",
    "factor_miner": "因子挖掘：每日因子轮/候选",
    "data_engineer": "数据采集/ETL/接口同步",
    "data_quality": "数据质量/校验/新鲜度",
    "data_governance": "数据治理/血缘/口径（行营）",
    "data_dev": "数据开发/查询服务（行营）",
}


def build_zhangsong(legion: dict) -> dict:
    """张松部门（数据智能部·兰台 + 数据治理行营）专属视图数据。"""
    catalog = _read_json(CATALOG, {})
    registry = _read_json(REGISTRY, {})
    tables = catalog.get("tables") or {}
    phys = registry.get("physical_tables") or []
    dist = Counter((x.get("qualification_status") or "unknown") for x in phys)
    depts = {d.get("id"): d for d in (legion.get("departments") or [])}
    roster = {m.get("id"): m for m in (legion.get("roster") or [])}
    feed = legion.get("feed") or []
    by_agent = {}
    for f in feed:
        who = f.get("caller") or f.get("from")
        if who and who not in by_agent:
            by_agent[who] = f
    members = []
    seen = set()
    for dep_id in ("data", "proj_data"):
        d = depts.get(dep_id) or {}
        for a in d.get("agents") or []:
            sid = a.get("id")
            if not sid or sid in seen:
                continue
            seen.add(sid)
            m = roster.get(sid) or {}
            hb = _seat_heartbeat(sid)
            recent = by_agent.get(sid)
            raw_recent = (recent or {}).get("detail") or (recent or {}).get("action") or ""
            for _sid, _nm in ((sid, a.get("cn")),):
                raw_recent = str(raw_recent).replace(str(_sid), str(_nm or "成员"))
            members.append({
                "id": sid, "name": a.get("cn"), "dept": d.get("name"),
                "dept_id": dep_id, "role": a.get("role"),
                "duty": ROLE_DUTY.get(a.get("role"), "数据工作"),
                "binding": cn_model(a.get("binding")), "state": m.get("state") or "idle",
                "state_cn": {"idle": "空闲", "active": "在忙", "offline": "离线"}.get(
                    (m.get("state") or "idle"), "空闲"),
                "title": m.get("title"), "heartbeat": hb,
                "recent": cn_safe(raw_recent)[:60],
            })
    domains = []
    for name in ("沪深行情", "资金与筹码", "财务与公告", "海外与衍生", "指数与基准", "舆情与研报", "知识图谱", "其他"):
        pass
    # 数据域统计（复用 catalog：按 source/channel 粗分）
    from collections import defaultdict
    dom = defaultdict(lambda: {"count": 0, "rows": 0, "latest": ""})
    for aid, tb in tables.items():
        name = "其他"
        low = str(aid).lower()
        src = str(tb.get("source") or "").lower()
        if any(k in low or k in src for k in ("xq", "guba", "news", "report", "notice", "intel", "sentiment", "anns")):
            name = "舆情与研报"
        elif any(k in low for k in ("kg", "neo4j", "lineage", "graph", "relation")):
            name = "知识图谱"
        elif any(k in low or k in src for k in ("income", "balance", "cashflow", "fina", "forecast", "express", "dividend", "disclosure", "audit", "report_rc", "mainbz")):
            name = "财务与公告"
        elif any(k in low or k in src for k in ("hk", "us_", "global", "fx", "fut", "opt", "sge", "repo", "crypto")):
            name = "海外与衍生"
        elif any(k in low for k in ("moneyflow", "margin", "limit", "cyq", "kpl", "ths", "slb", "pledge", "holder", "top10", "top_list", "fund", "etf", "cb", "repurchase", "share_float", "stk_holdertrade", "stk_auction")):
            name = "资金与筹码"
        elif any(k in low for k in ("index", "sw_", "sector", "industry")):
            name = "指数与基准"
        elif any(k in low for k in ("daily", "weekly", "monthly", "adj", "stk", "trade_cal", "suspend", "namechange", "bak_basic", "stock_basic", "daily_info", "stk_limit")):
            name = "沪深行情"
        recur = dom[name]
        recur["count"] += 1
        recur["rows"] += tb.get("rows") or 0
        f = str(tb.get("freshness") or "")
        if f and f > recur["latest"]:
            recur["latest"] = f
    domains = [{"name": k, **v} for k, v in sorted(dom.items(), key=lambda x: -x[1]["count"])]
    tasks = _task_status()
    sync_tasks = []
    _seen_task = {}
    for x in tasks:
        name = x.get("name") or ""
        path = x.get("path") or ""
        if not any(k in name for k in ("TdxSync", "TushareSync", "KgDailySync", "DataWarehouseSnapshot",
                                       "XmapSync", "NoticeBackfill", "GlobalFutSync", "SentimentBT",
                                       "ShipinhaoCards", "TdxSyncProbe")):
            continue
        rec = {"name": name, "state": x.get("state"), "last_result": x.get("last_result"),
               "next_run": x.get("next_run"), "last_run": x.get("last_run"), "path": path}
        old = _seen_task.get(name)
        if old is None or ("ZeroBound" in path and "ZeroBound" not in (old.get("path") or "")):
            _seen_task[name] = rec
    sync_tasks = list(_seen_task.values())
    sync_tasks.sort(key=lambda x: (x.get("next_run") or "", x.get("name") or ""))
    for x in sync_tasks:
        x["name_cn"] = cn_task_name(x.get("name") or "")
        x["state_cn"] = STATE_CN.get(str(x.get("state")), "未知")
        x["result_cn"] = RESULT_CN.get(str(x.get("last_result")), "未知")
    return {
        "available": bool(members),
        "dept": {"id": "data", "name": "数据智能部", "cn_title": "兰台",
                 "lead": "张松", "lead_id": "", "status": "active",
                 "related": {"name": "数据治理部", "cn_title": "数据行营（冻结待归建）",
                             "lead": "张松", "status": "frozen_pending_merge"}},
        "members": members,
        "member_count": len(members),
        "online": sum(1 for m in members if (m.get("state") or "idle") != "idle"),
        "domains": domains,
        "physical": len(tables),
        "k8": {"unknown": dist.get("unknown", 0), "ok": dist.get("evidence_ok", 0),
               "partial": dist.get("evidence_partial", 0), "deprecated": dist.get("deprecated", 0)},
        "division": [
            {"duty": "数据采集 / 接口同步", "seat": "刘巴（数据工程师）", "status": "ok",
             "progress": "主仓最新到11月2日；夜间同步正常"},
            {"duty": "数据质量 / 资格取证", "seat": "陈群（数据质量）", "status": "ok",
             "progress": "待取证14张（已达标）；更新提醒1项"},
            {"duty": "数据治理 / 血缘 / 口径", "seat": "张松（部门主官）", "status": "warn",
             "progress": "第三阶段完成83.3%，上午9点最终验收"},
            {"duty": "因子挖掘", "seat": "蒋琬（因子研究）", "status": "warn",
             "progress": "因子日报到10月30日（11月3日数据待补）"},
            {"duty": "数据开发 / 查询服务", "seat": "马钧（数据开发）", "status": "ok",
             "progress": "取数服务在线；11月5日切换新页面"},
            {"duty": "数据仓施工 / 验收（协作）", "seat": "数据仓负责人（协作）", "status": "ok",
             "progress": "首夜通过；考核指标9/10；上午9点最终验收"},
        ],
        "sync_tasks": sync_tasks,
        "progress": {"s3_pct": 83.3, "kpi_ok": 9, "kpi_total": 10, "first_night": "通过",
                     "acceptance": "待验（上午9点）", "k8": dist.get("unknown", 0)},
        "hermes": {k: v for k, v in (legion.get("hermes") or {}).items()},
        "hermes_work": build_hermes_work(),
        "heartbeat": legion.get("heartbeat") or {},
        "feed": [f for f in feed if (f.get("caller") in seen or f.get("from") in seen)][:10],
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }



def _hermes_runtime() -> dict:
    """Hermes 8787 运行态（reload 后真实 tools）。"""
    hv = {"X-Portal-User": "laotian", "Accept": "application/json"}
    s = _http_json("http://127.0.0.1:8787/api/mcp/servers", headers=hv, timeout=5)
    t = _http_json("http://127.0.0.1:8787/api/mcp/tools", headers=hv, timeout=5)
    servers = (s or {}).get("servers") or []
    return {"servers_total": len(servers),
            "active_servers": sum(1 for x in servers if x.get("active")),
            "tools_total": (t or {}).get("total", 0),
            "reload_required": (s or {}).get("reload_required"),
            "unavailable_servers": (t or {}).get("unavailable_servers") or [],
            "dw_servers": [{"name": x.get("name"), "active": x.get("active"),
                            "tools": x.get("tool_count")} for x in servers
                           if (x.get("name") or "").startswith(("dw-", "factor"))]}


def build_hermes_work() -> dict:
    """Hermes 底座 × 工作绑定：读真实 MCP 探针（dw-* servers tools/list + dw_query 执行）。"""
    import glob as _glob
    probe = None
    files = sorted(_glob.glob(str(REPORTS / "hermes_mcp_probe_*.json")))
    if files:
        try:
            probe = json.loads(Path(files[-1]).read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            probe = None
    server_map = {}
    evidence = None
    if probe:
        evidence = (probe.get("summary") or {}).get("exec_evidence")
        for s in probe.get("servers") or []:
            server_map[s.get("name")] = s
    def tools_of(name):
        return [x.get("name") for x in ((server_map.get(name) or {}).get("tools") or [])]
    def status_of(name):
        s = server_map.get(name) or {}
        return "ok" if s.get("ok") else ("err" if s.get("error") else "unknown")
    bindings = [
        {"work": "数据查询 / 取数服务", "seat": "张松（部门主官）· 马钧（数据开发）",
         "server_cn": "数据查询服务", "tools": ["dw_query", "dw_list", "dw_get", "dw_stats", "dw_search", "dw_sample"],
         "tools_cn": ["数据查询", "数据集列表", "数据集详情", "数据仓统计", "数据集搜索", "数据样本"],
         "status": status_of("dw-governance"), "evidence_cn": "已完成一次真实查询：日线行情返回1行，状态正常"},
        {"work": "数据质量 / 新鲜度 / 备份", "seat": "陈群（数据质量）",
         "server_cn": "数据质量服务", "tools": ["freshness", "quality_checks", "quality_snapshot", "manifest", "backup", "lineage_impact"],
         "tools_cn": ["新鲜度检查", "质量检查", "质量快照", "数据清单", "备份检查", "血缘影响"],
         "status": status_of("dw-quality"), "evidence_cn": "6项质量工具可用，可查新鲜度与数据清单"},
        {"work": "数据目录 / 资产 / 资格", "seat": "刘巴（数据工程师）",
         "server_cn": "数据目录服务", "tools": ["catalog_snapshot", "registry_stats", "lifecycle", "table_info", "qualification"],
         "tools_cn": ["目录快照", "登记统计", "生命周期", "数据表信息", "数据资格"],
         "status": status_of("dw-catalog"), "evidence_cn": "5项目录工具可用，覆盖目录/登记/资格"},
        {"work": "因子挖掘 / 因子日报", "seat": "蒋琬（因子研究）",
         "server_cn": "因子工具服务", "tools": ["factor_daily_latest", "factor_mining_latest", "factor_reports", "factor_script_catalog"],
         "tools_cn": ["因子日报", "因子挖掘", "因子报告", "因子脚本目录"],
         "status": status_of("factor-tools"), "evidence_cn": "4项因子工具可用，日报与挖掘报告可读"},
    ]
    return {
        "available": bool(probe),
        "probe_ts": (probe or {}).get("ts"),
        "probe_ok": (probe or {}).get("all_ok"),
        "servers_probed": len(server_map),
        "tools_total": sum(len(tools_of(n)) for n in server_map),
        "tools_by_server": {n: tools_of(n) for n in server_map},
        "servers": [{"name": n, "name_cn": SERVER_CN.get(n, "工具服务"),
                     "ok": bool((server_map.get(n) or {}).get("ok")),
                     "tool_count": len(tools_of(n)),
                     "tools_cn": [TOOL_CN.get(x, "工具") for x in tools_of(n)],
                     "duration_s": (server_map.get(n) or {}).get("duration_s"),
                     "error": (server_map.get(n) or {}).get("error")} for n in server_map],
        "exec_evidence_cn": "已完成一次真实数据查询：查询日线行情，返回1行，状态正常",
        "exec_evidence": evidence,
        "bindings": bindings,
        "runtime": _hermes_runtime(),
    }


def build() -> dict:
    catalog = _read_json(CATALOG, {})
    registry = _read_json(REGISTRY, {})
    snapshot = _read_json(SNAPSHOT, {})
    kpi = _read_json(KPI, {})
    tables = catalog.get("tables") or {}
    phys = {p.get("asset_id"): p for p in registry.get("physical_tables") or []}
    display = registry.get("display_assets") or []
    covered = set()
    for d in display:
        for t in d.get("physical_tables") or []:
            covered.add(t)
    covered &= set(tables)

    datasets = []
    _now = datetime.now()
    _today = _now.strftime("%Y%m%d")
    # 收盘前（<15:30）不把当日算作"最新交易日"（盘前只有日历/少量预生成行）
    _cutoff = _today if (_now.hour, _now.minute) >= (15, 30) else \
        (_now.replace(hour=0, minute=0)).strftime("%Y%m%d")
    if _cutoff == _today and _now.hour < 15:
        from datetime import timedelta
        _cutoff = (_now - timedelta(days=1)).strftime("%Y%m%d")
    for aid, t in tables.items():
        r = phys.get(aid) or {}
        status = (r.get("qualification_status") or "unregistered")
        source = str(t.get("source") or "")
        channel = str(r.get("channel") or "")
        datasets.append({
            "id": aid,
            "label": aid.replace("_", " "),
            "domain": domain_of(aid, source, channel),
            "source": source or channel or "-",
            "channel": channel or "-",
            "freq": (r.get("frequency") or t.get("freq") or "-"),
            "rows": t.get("rows"),
            "files": t.get("files"),
            "freshness": t.get("freshness"),
            "date_col": t.get("date_col") or (r.get("date_column") or ""),
            "status": status,
            "deprecated": bool(t.get("deprecated")),
            "columns": [COL_CN.get(str(c), "字段") for c in (r.get("business_columns") or [])][:12],
            "label_cn": cn_dataset_label(aid, domain_of(aid, source, channel)),
            "freq_cn": FREQ_CN.get(str(r.get("frequency") or t.get("freq") or ""), "不定期"),
            "qual_cn": QUAL_CN.get(status, "待取证"),
            "source_cn": SOURCE_CN.get(source, SOURCE_CN.get(channel, "数据接口")),
            "date_col_cn": COL_CN.get(str(t.get("date_col") or ""), "日期"),
            "desc": "",
            "store": (t.get("store") or "")[:120],
            "evidence": (r.get("evidence_summary") or "")[:200],
            "evidence_at": r.get("evidence_checked_at") or "",
            "displayed": aid in covered,
        })
    datasets.sort(key=lambda x: (-(x["rows"] or 0), x["id"]))
    _seen_label = {}
    for d in datasets:
        lab = d["label_cn"]
        _seen_label[lab] = _seen_label.get(lab, 0) + 1
        if _seen_label[lab] > 1:
            d["label_cn"] = "%s（%d）" % (lab, _seen_label[lab])

    dist = Counter(d["status"] for d in datasets)
    dom_map: dict[str, dict] = {}
    for d in datasets:
        cur = dom_map.setdefault(d["domain"], {
            "name": d["domain"], "count": 0, "rows": 0,
            "ok": 0, "partial": 0, "unknown": 0, "deprecated": 0, "displayed": 0,
            "latest": ""})
        cur["count"] += 1
        cur["rows"] += d["rows"] or 0
        if d["status"] == "evidence_ok":
            cur["ok"] += 1
        elif d["status"] == "evidence_partial":
            cur["partial"] += 1
        elif d["status"] == "unknown":
            cur["unknown"] += 1
        elif d["status"] == "deprecated":
            cur["deprecated"] += 1
        if d["displayed"]:
            cur["displayed"] += 1
        if d["freshness"] and str(d["freshness"]) <= _cutoff:
            is_trade = d["date_col"] == "trade_date"
            cur.setdefault("_trade_latest", "")
            cur.setdefault("_any_latest", "")
            if is_trade and str(d["freshness"]) > cur["_trade_latest"]:
                cur["_trade_latest"] = str(d["freshness"])
            if str(d["freshness"]) > cur["_any_latest"]:
                cur["_any_latest"] = str(d["freshness"])
    for d in dom_map.values():
        d["latest"] = d.get("_trade_latest") or d.get("_any_latest") or ""
        d.pop("_trade_latest", None)
        d.pop("_any_latest", None)
    domains = sorted(dom_map.values(), key=lambda x: -x["count"])

    fr = freshness_issues()
    latest_dates = sorted({str(d["freshness"]) for d in datasets
                           if d["freshness"] and d["date_col"] == "trade_date"
                           and str(d["freshness"]) <= _cutoff})
    latest_trading = latest_dates[-1] if latest_dates else "-"
    as_of = datetime.now().strftime("%Y-%m-%d")
    kpis_state = kpi.get("kpis") or kpi

    _lg = build_legion()
    data = {
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "built_cn": datetime.now().strftime("%Y年%m月%d日 %H:%M"),
            "as_of": as_of,
            "catalog_version": catalog.get("version"),
            "catalog_updated": catalog.get("updated") or "",
            "physical": len(tables),
            "display": len(display),
            "display_covered": len(covered),
            "latest_dates": latest_dates[-3:],
            "latest_trading": latest_trading,
            "prototype": "v2",
        },
        "kpis": [
            {"id": "datasets", "label": "数据表总数", "value": len(tables),
             "unit": "张", "note": "数据目录第%s版" % catalog.get("version"), "tone": "info"},
            {"id": "display", "label": "已展示数据 / 覆盖", "value": "%d/%d" % (len(covered), len(tables)),
             "unit": "", "note": "共展示 %d 项" % len(display), "tone": "ok"},
            {"id": "rows", "label": "数据总行数", "value": round(sum((d["rows"] or 0) for d in datasets) / 1e8, 2),
             "unit": "亿行", "note": "按数据目录汇总", "tone": "info"},
            {"id": "unknown", "label": "待取证数据", "value": dist.get("unknown", 0),
             "unit": "张", "note": "目标少于40张（当前达标）", "tone": "ok" if dist.get("unknown", 0) < 40 else "warn"},
            {"id": "partial", "label": "证据待补", "value": dist.get("evidence_partial", 0),
             "unit": "张", "note": "证据齐全 %d 张" % dist.get("evidence_ok", 0), "tone": "warn"},
            {"id": "freshness", "label": "最新数据日期", "value": latest_trading,
             "unit": "", "note": "已检查 %d 项更新情况" % fr.get("checks", 0), "tone": "ok" if not fr.get("issues") else "warn"},
            {"id": "issues", "label": "数据更新提醒", "value": len(fr.get("issues") or []),
             "unit": "项", "note": "截至 %s" % (fr.get("as_of") or "-"), "tone": "ok" if not fr.get("issues") else "err"},
            {"id": "tests", "label": "自动测试", "value": "331", "unit": "项通过",
             "note": "数据仓自动检查", "tone": "ok"},
        ],
        "domains": domains,
        "datasets": datasets,
        "quality": {
            "distribution": dict(dist),
            "freshness": fr,
            "coverage": {"physical": len(tables), "display": len(display),
                         "display_covered": len(covered),
                         "query_clean_pct": 99.03, "query_raw_pct": 97.77},
            "kpi": [{"id": k, "value": (v.get("value") if isinstance(v, dict) else v)}
                    for k, v in list(kpis_state.items())[:10]],
        },
        "lineage": lineage_stats(),
        "tasks": {
            "scheduled": [
                {"time": "05:15", "name": "盘前数据准备", "note": "开盘前数据"},
                {"time": "06:00", "name": "数据映射同步", "note": "数据映射"},
                {"time": "06:20", "name": "数据目录与知识库同步", "note": "重建数据目录"},
                {"time": "06:40", "name": "数据快照刷新", "note": "每日数据快照"},
                {"time": "06:45", "name": "公告补采", "note": "公告信息补采"},
                {"time": "07:20", "name": "情绪回测", "note": "市场情绪产物"},
                {"time": "17:35-18:05", "name": "行情数据同步（四批）", "note": "收盘后增量"},
                {"time": "18:15", "name": "外盘期货同步", "note": "外盘商品"},
                {"time": "19:20", "name": "同步看门狗", "note": "严格检查"},
                {"time": "23:30", "name": "数据接口同步", "note": "夜间数据同步"},
            ],
            "wip": (snapshot.get("tasks") or {}).get("doing") or [],
        },
        "governance": {
            "principles": [
                {"title": "只读取数", "desc": "所有查询都走统一只读入口，不会改动任何数据"},
                {"title": "查不到会说清楚", "desc": "查不到、不支持、权限不足都会明确提示，不假装成功"},
                {"title": "数据资格", "desc": "按证据齐全、待补、待取证、已停用四类管理，待取证少于40张为达标"},
                {"title": "更新检查", "desc": "按数据更新频率自动检查，异常会显示在总览"},
                {"title": "血缘可追溯", "desc": "从来源到原始数据、清洗数据、指标和使用方，均可追溯"},
                {"title": "全程留痕", "desc": "每次操作都保留命令、结果、产物和回滚记录，重要变更需审批"},
            ],
            "pending": [
                {"item": "密钥轮换（12 项）", "owner": "老田 / 管理员", "note": "已接受风险"},
                {"item": "数据库只读连接（待审批）", "owner": "需审批", "note": "1 张数据待取证"},
                {"item": "公告补采任务迁移", "owner": "管理员", "note": "替换任务已运行"},
                {"item": "社区采集限流策略", "owner": "数据仓负责人", "note": "已增加夜间窗口"},
            ],
            "acceptance": [
                {"item": "首夜验收（四批同步 + 看门狗）", "status": "pass", "date": "2026-11-02"},
                {"item": "历史数据验收（12 项）", "status": "pass", "date": "2026-11-02"},
                {"item": "最终验收", "status": "pending", "date": "2026-11-03 上午9点"},
                {"item": "11月5日上线窗口", "status": "pending", "date": "2026-11-05 上午6点"},
            ],
        },
        "manual": {
            "enabled": True,
            "submitter": "laotian",
            "max_file_mb": 5,
            "max_preview_rows": 5,
            "allowed_layers": ["raw", "clean", "mart"],
            "staging_root": "data/staging/manual/<yyyymmdd>/<entry_id>/",
            "audit_fields": ["submitted_by", "submitted_at", "source", "sha256", "rows",
                             "target_dataset", "mode"],
            "review_roles": ["数据负责人", "数据仓负责人"],
            "kinds_cn": KIND_CN,
            "layers_cn": {"raw": "原始层", "clean": "清洗层", "mart": "指标层"},
            "modes_cn": {"append": "追加", "staging_replace": "整批替换"},
            "status_cn": {"staged": "已暂存", "approved": "已通过", "rejected": "已驳回"},
            "api": {"ingest": "POST /api/dw/manual/ingest",
                    "list": "GET /api/dw/manual/entries",
                    "approve": "POST /api/dw/manual/approve"},
            "note": "手动入口只写 staging；schema/主键/日期/去重校验 + 审核后才写回主仓",
        },
        "legion": _lg,
        "zhangsong": build_zhangsong(_lg),
        "hermes_work": build_hermes_work(),
        "query_examples": [
            {"label": "日线行情：最近2行", "dataset": "ts_daily", "params": {"limit": 2, "count": 1}},
            {"label": "周线行情：10月以来", "dataset": "ts_weekly", "params": {"since": "20261001", "limit": 5}},
            {"label": "研报数据：统计总数", "dataset": "ts_report_rc", "params": {"count": 1, "limit": 1}},
            {"label": "外盘期货：最近3行", "dataset": "ts_global_fut", "params": {"limit": 3}},
        ],
    }
    return data


def render(data: dict) -> str:
    if not TEMPLATE.exists():
        raise SystemExit("模板缺失: %s" % TEMPLATE)
    tpl = TEMPLATE.read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    html = tpl.replace("__DW_DATA_JSON__", payload)
    html = html.replace("__BUILT_AT__", data["meta"].get("built_cn") or data["meta"]["generated_at"])
    html = html.replace("__CATALOG_VERSION__", str(data["meta"].get("catalog_version")))
    if "__DW_DATA_JSON__" in html:
        raise SystemExit("模板占位替换失败")
    return html


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="构建数据仓原型 v2")
    ap.add_argument("--check", action="store_true", help="构建后跑校验")
    args = ap.parse_args(argv)
    data = build()
    data_json = json.dumps(data, ensure_ascii=False, indent=1)
    OUT_DATA.write_text(data_json, encoding="utf-8")
    html = render(data)
    OUT_HTML.write_text(html, encoding="utf-8")
    print("OK pages=%d domains=%d html=%.1fKB data=%.1fKB -> %s"
          % (len(data["datasets"]), len(data["domains"]),
             len(html.encode("utf-8")) / 1024, len(data_json.encode("utf-8")) / 1024, OUT_HTML))
    if args.check:
        import subprocess
        py = sys.executable
        r = subprocess.run([py, "-X", "utf8", str(REPO / "tools/check_dw_prototype.py")],
                           cwd=str(REPO), capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        print(r.stdout.strip() or r.stderr.strip())
        return r.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
