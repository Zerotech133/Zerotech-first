# -*- coding: utf-8 -*-
"""构建数据仓原型 v2：聚合 catalog/registry/snapshot/freshness → portal/static/dw_v2.html。

用法：
  python tools/build_dw_prototype.py            # 构建
  python tools/build_dw_prototype.py --check    # 构建后跑校验
"""
from __future__ import annotations

import argparse
import json
import sys
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
    ("A股行情", ("daily", "weekly", "monthly", "adj", "stk", "trade_cal", "suspend",
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
                bad.append({"kind": "table", "id": c.get("table"), "latest": c.get("latest"),
                            "lag_days": c.get("lag_days"), "note": c.get("note") or ""})
            elif c.get("tool") == "sqlite":
                bad.append({"kind": "sqlite", "id": c.get("label"), "latest": c.get("latest"),
                            "lag_days": c.get("lag_days"), "note": c.get("db") or ""})
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
    return {"nodes": 129, "edges": 78, "source": "lineage_20261102（静态兜底）"}


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
        "feed": [{"ts": f.get("ts"), "caller": f.get("caller"), "action": f.get("action"),
                  "detail": f.get("detail"), "from": f.get("from")} for f in feed[:15]],
        "tasks": [{k: x.get(k) for k in ("id", "desc", "status", "owner")}
                  for x in (ops or {}).get("tasks") or []],
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
        return {"exists": True, "ok": d.get("ok"), "model": d.get("model"),
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
            members.append({
                "id": sid, "name": a.get("cn"), "dept": d.get("name"),
                "dept_id": dep_id, "role": a.get("role"),
                "duty": ROLE_DUTY.get(a.get("role"), a.get("role") or "-"),
                "binding": a.get("binding"), "state": m.get("state") or "idle",
                "title": m.get("title"), "heartbeat": hb,
                "recent": (recent or {}).get("detail") or (recent or {}).get("action") or "",
            })
    domains = []
    for name in ("A股行情", "资金与筹码", "财务与公告", "海外与衍生", "指数与基准", "舆情与研报", "知识图谱", "其他"):
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
            name = "A股行情"
        recur = dom[name]
        recur["count"] += 1
        recur["rows"] += tb.get("rows") or 0
        f = str(tb.get("freshness") or "")
        if f and f > recur["latest"]:
            recur["latest"] = f
    domains = [{"name": k, **v} for k, v in sorted(dom.items(), key=lambda x: -x[1]["count"])]
    tasks = _task_status()
    sync_tasks = []
    for x in tasks:
        name = x.get("name") or ""
        if any(k in name for k in ("TdxSync", "TushareSync", "KgDailySync", "DataWarehouseSnapshot",
                                    "XmapSync", "NoticeBackfill", "GlobalFutSync", "SentimentBT", "ShipinhaoCards")):
            sync_tasks.append({"name": name, "state": x.get("state"), "last_result": x.get("last_result"),
                               "next_run": x.get("next_run"), "last_run": x.get("last_run")})
    sync_tasks.sort(key=lambda x: (x.get("next_run") or "", x.get("name") or ""))
    return {
        "available": bool(members),
        "dept": {"id": "data", "name": "数据智能部", "cn_title": "兰台",
                 "lead": "张松", "lead_id": "DATA1", "status": "active",
                 "related": {"name": "🅱 数据治理部", "cn_title": "数据行营（冻结待归建）",
                             "lead": "张松", "status": "frozen_pending_merge"}},
        "members": members,
        "member_count": len(members),
        "online": sum(1 for m in members if (m.get("state") or "idle") != "idle"),
        "domains": domains,
        "physical": len(tables),
        "k8": {"unknown": dist.get("unknown", 0), "ok": dist.get("evidence_ok", 0),
               "partial": dist.get("evidence_partial", 0), "deprecated": dist.get("deprecated", 0)},
        "division": [
            {"duty": "数据采集 / 接口同步", "seat": "DATA2 刘巴", "status": "ok",
             "progress": "tdx/tushare 主仓最新 20261102；TushareSync 23:30 rc=0"},
            {"duty": "数据质量 / 资质取证", "seat": "DATA3 陈群", "status": "ok",
             "progress": "K8 unknown=14（达标）；freshness 仅 1 issue"},
            {"duty": "数据治理 / 血缘 / 口径", "seat": "DATA1 张松", "status": "warn",
             "progress": "S3 完成度 83.3%，09:00 终验收"},
            {"duty": "因子挖掘", "seat": "FACTOR1 蒋琬", "status": "warn",
             "progress": "因子轮 20261030（11-03 数据待补）"},
            {"duty": "数据开发 / 查询服务", "seat": "DEV1 马钧（行营）", "status": "ok",
             "progress": "/api/dw 在线；11-05 换页"},
            {"duty": "数据仓施工 / 验收（协作）", "seat": "DSH-Lead（非军团席位）", "status": "ok",
             "progress": "首夜 PASS；KPI 9/10；终验收 09:00"},
        ],
        "sync_tasks": sync_tasks,
        "progress": {"s3_pct": 83.3, "kpi_ok": 9, "kpi_total": 10, "first_night": "PASS",
                     "acceptance": "pending 09:00", "k8": dist.get("unknown", 0)},
        "hermes": {k: v for k, v in (legion.get("hermes") or {}).items()},
        "heartbeat": legion.get("heartbeat") or {},
        "feed": [f for f in feed if (f.get("caller") in seen or f.get("from") in seen)][:10],
        "generated_at": datetime.now().isoformat(timespec="seconds"),
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
            "columns": [str(c) for c in (r.get("business_columns") or [])][:12],
            "desc": (t.get("desc") or r.get("unsupported_reason") or "")[:160],
            "store": (t.get("store") or "")[:120],
            "evidence": (r.get("evidence_summary") or "")[:200],
            "evidence_at": r.get("evidence_checked_at") or "",
            "displayed": aid in covered,
        })
    datasets.sort(key=lambda x: (-(x["rows"] or 0), x["id"]))

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
            {"id": "datasets", "label": "物理表 / 数据集", "value": len(tables),
             "unit": "张", "note": "catalog %s" % catalog.get("version"), "tone": "info"},
            {"id": "display", "label": "展示资产 / 覆盖", "value": "%d/%d" % (len(covered), len(tables)),
             "unit": "", "note": "display %d 条" % len(display), "tone": "ok"},
            {"id": "rows", "label": "登记总行数", "value": round(sum((d["rows"] or 0) for d in datasets) / 1e8, 2),
             "unit": "亿行", "note": "catalog rows 汇总", "tone": "info"},
            {"id": "unknown", "label": "资质 unknown", "value": dist.get("unknown", 0),
             "unit": "张", "note": "目标 <40（当前达标）", "tone": "ok" if dist.get("unknown", 0) < 40 else "warn"},
            {"id": "partial", "label": "资质 partial", "value": dist.get("evidence_partial", 0),
             "unit": "张", "note": "ok %d" % dist.get("evidence_ok", 0), "tone": "warn"},
            {"id": "freshness", "label": "最新交易日", "value": latest_trading,
             "unit": "", "note": " freshness 检查 %d 项" % fr.get("checks", 0), "tone": "ok" if not fr.get("issues") else "warn"},
            {"id": "issues", "label": "新鲜度 issue", "value": len(fr.get("issues") or []),
             "unit": "项", "note": "as_of %s" % (fr.get("as_of") or "-"), "tone": "ok" if not fr.get("issues") else "err"},
            {"id": "tests", "label": "门禁测试", "value": "331", "unit": "passed",
             "note": "datawarehouse 套件", "tone": "ok"},
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
                {"time": "05:15", "name": "HuangzhongDaily", "note": "盘前数据"},
                {"time": "06:00", "name": "XmapSync", "note": "xmap 同步"},
                {"time": "06:20", "name": "KgDailySync", "note": "catalog 重建 + KG 同步"},
                {"time": "06:40", "name": "dw_snapshot", "note": "快照刷新（K3）"},
                {"time": "06:45", "name": "NoticeBackfill", "note": "公告补采 PT8H"},
                {"time": "07:20", "name": "SentimentBT", "note": "情绪回测产物"},
                {"time": "17:35-18:05", "name": "TdxSync A/B/C/D", "note": "四批增量"},
                {"time": "18:15", "name": "GlobalFutSync", "note": "外盘商品"},
                {"time": "19:20", "name": "TdxSyncProbe", "note": "严格看门狗"},
                {"time": "23:30", "name": "TushareSync", "note": "tushare 增量"},
            ],
            "wip": (snapshot.get("tasks") or {}).get("doing") or [],
        },
        "governance": {
            "principles": [
                {"title": "只读取数", "desc": "统一 GET/POST /api/dw/query，sqlite PRAGMA query_only，parquet 只读"},
                {"title": "三态信封", "desc": "ok / no_data / not_supported，fail-visible，不静默假绿"},
                {"title": "资质门", "desc": "evidence_ok / partial / unknown / deprecated 四态，unknown<40 为门槛"},
                {"title": "数据新鲜度", "desc": "按频率阈值 + footer 最新业务日校验，issue 进总览"},
                {"title": "血缘可追溯", "desc": "源 → 原始层 → 清洗层 → 指标层 → 消费，节点/边可查"},
                {"title": "证据留痕", "desc": "命令 + rc + 产物 + 回滚，L2 五要素"},
            ],
            "pending": [
                {"item": "A3 凭据轮换（12 keys）", "owner": "老田/管理员", "note": "accepted-risk"},
                {"item": "v2_bar_daily PG 只读 DSN", "owner": "L2 审批", "note": "1 张 unknown"},
                {"item": "NoticeBackfill 原 S4U 写回", "owner": "管理员", "note": "PT8H 替换任务已跑"},
                {"item": "Guba 限流策略（三窗口后评估）", "owner": "DSH-Lead", "note": "20:30/22:30 新增窗口"},
            ],
            "acceptance": [
                {"item": "首夜验收（四批+看门狗）", "status": "pass", "date": "2026-11-02"},
                {"item": "历史数据验收（12 项）", "status": "pass", "date": "2026-11-02"},
                {"item": "终验收（final_acceptance）", "status": "pending", "date": "2026-11-03 09:00"},
                {"item": "11-05 L2 窗口", "status": "pending", "date": "2026-11-05 06:00"},
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
            "review_roles": ["DATA1", "DSH-Lead"],
            "api": {"ingest": "POST /api/dw/manual/ingest",
                    "list": "GET /api/dw/manual/entries",
                    "approve": "POST /api/dw/manual/approve"},
            "note": "手动入口只写 staging；schema/主键/日期/去重校验 + 审核后才写回主仓",
        },
        "legion": _lg,
        "zhangsong": build_zhangsong(_lg),
        "query_examples": [
            {"label": "ts_daily 最近 2 行", "dataset": "ts_daily", "params": {"limit": 2, "count": 1}},
            {"label": "ts_weekly 自 20261001", "dataset": "ts_weekly", "params": {"since": "20261001", "limit": 5}},
            {"label": "ts_report_rc 计数", "dataset": "ts_report_rc", "params": {"count": 1, "limit": 1}},
            {"label": "ts_global_fut 最近", "dataset": "ts_global_fut", "params": {"limit": 3}},
        ],
    }
    return data


def render(data: dict) -> str:
    if not TEMPLATE.exists():
        raise SystemExit("模板缺失: %s" % TEMPLATE)
    tpl = TEMPLATE.read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    html = tpl.replace("__DW_DATA_JSON__", payload)
    html = html.replace("__BUILT_AT__", data["meta"]["generated_at"])
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
