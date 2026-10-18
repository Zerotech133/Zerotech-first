# -*- coding: utf-8 -*-
"""数据运营指挥台 /api/dataops/overview —— 采集动线 + 数据池 + 管道调度聚合视图。

定位（2026-10-18 老田拍板）：日报只是单维下游，本接口是全局数据地图——
Hermes / 门户 / 其他调度系统读这一个接口即知：什么时间拔（采集动线）、
拔了多少（增量）、池里有多少（存量）、结构化到什么程度（打标率）。

只读原则：SQLite ro 连接、状态文件只读、计划任务 XML 直读，零写操作。
自进化挂载点：新数据源在 COLLECTORS 加一行、新库表在 POOLS 加一行，
即自动接入指挥台与下游消费（v2 可落库）。
fail-visible：任何单源探测失败进 errors，不静默吞掉。
"""
import glob
import json
import os
import re
import socket
import sqlite3
import time

QB = r"C:/项目/老田的AI助手/quant_backtest"
DATA = os.path.join(QB, "data")
RUNS = r"C:/aios/automation_runs"
TASKS_DIR = r"C:/Windows/System32/Tasks"
SEED_QUEUE_LEN = 11   # 与 xq_seed_auto.SEED_QUEUE 同步（20000..230000）

# (key, 名称, 载体, 节奏, 产出落点, 状态来源)
COLLECTORS = [
    ("XueqiuSync",            "雪球同步",     "计划任务",   "每日 17:40",      "xueqiu_info.db",            "schtasks"),
    ("GubaCrawl",             "股吧采集",     "计划任务",   "每日 17:50",      "股吧数据",                  "schtasks"),
    ("XueqiuCrawl",           "雪球日采集",   "计划任务",   "每日 18:00",      "xq_status 当日增量",        "schtasks"),
    ("xq_seed",               "雪球历史播种", "automation", "每 30 分钟",      "xq_status 历史回填",        "seed"),
    ("xq_classify",           "舆情分类追平", "automation", "每 1 小时 probe", "xq_status_classified",      "classify"),
    ("xq_classify_daily",     "分类日轮",     "automation", "每日 18:30",      "xq_status_classified 增量", "classify"),
    ("xq_report",             "情报日报",     "automation", "每日 19:00",      "automation_runs/xq_report", "report"),
    ("AgentShadowDaily",      "影子日巡",     "计划任务",   "每日 17:10",      "agent_platform",            "schtasks"),
    ("AgentMondayCheckpoint", "周一检查点",   "计划任务",   "周一 17:25",      "agent_platform",            "schtasks"),
    ("AgentChiefPush",        "酋长推送",     "计划任务",   "每日 17:35",      "agent_platform",            "schtasks"),
    ("AgentShadowEvalWeekly", "影子周评",     "计划任务",   "周五 17:45",      "agent_platform",            "schtasks"),
]

# (库相对路径, [(表名, 说明), ...])
POOLS = [
    ("xueqiu/xueqiu_info.db", [
        ("xq_status",          "雪球帖存量"),
        ("xq_status_classified", "舆情打标（结构化层）"),
    ]),
    ("tdx/tdx_info.db", [
        ("stock_notice",  "公司公告"),
        ("stock_news",    "个股新闻"),
        ("stock_report",  "研报评级"),
    ]),
    ("quant.db", [
        ("chanlun_pool",  "缠论池"),
        ("nine_dim_fc",   "九维-资金"),
        ("nine_dim_rc",   "九维-风险"),
        ("nine_dim_fina", "九维-财务"),
    ]),
]
# 各表「今日入库」时间列（无则不算增量，靠库文件 mtime 近似）
TODAY_COL = {"xq_status": "fetched_at", "stock_notice": "_fetched_at"}

# 每日采拿量回溯源（2026-10-18 老田定标准：第一标准=每天采多少，第二标准=工具在不在跑）
# (库路径, 表, 入库时间列, 源标签)——时间列格式均为 'YYYY-MM-DD HH:MM:SS'，可 GROUP BY 日期回溯
INTAKE_SOURCES = [
    (os.path.join(DATA, "xueqiu", "xueqiu_info.db"), "xq_status",     "fetched_at",   "雪球帖"),
    (os.path.join(DATA, "tdx",     "tdx_info.db"),   "stock_notice", "_fetched_at",  "公告"),
    (os.path.join(DATA, "tdx",     "tdx_info.db"),   "stock_news",   "_fetched_at",  "个股新闻"),
    (os.path.join(DATA, "tdx",     "tdx_info.db"),   "stock_report", "_fetched_at",  "研报"),
]


def _daily_intake(errors, days=14):
    """每日采拿量：近 N 天按入库时间戳列回溯，跨源合计（第一标准的数据底座）。"""
    dates = [time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400 * i))
             for i in range(days - 1, -1, -1)]
    per_source = {}
    for db, t, col, label in INTAKE_SOURCES:
        try:
            con = _ro(db)
            for d, n in con.execute(
                    "SELECT substr(%s,1,10) d, COUNT(*) FROM %s WHERE %s >= ? GROUP BY d"
                    % (col, t, col), (dates[0] + " 00:00:00",)):
                per_source.setdefault(label, {})[d] = n
            con.close()
        except Exception as e:
            errors.append("intake %s: %s" % (label, str(e)[:50]))
    series = []
    for d in dates:
        row = {"date": d, "total": 0}
        for label in per_source:
            n = per_source[label].get(d, 0)
            row[label] = n
            row["total"] += n
        series.append(row)
    last7 = [r["total"] for r in series[-7:]]
    return {"days": days, "series": series, "sources": list(per_source.keys()),
            "today": series[-1]["total"] if series else 0,
            "yesterday": series[-2]["total"] if len(series) > 1 else 0,
            "avg7": round(sum(last7) / max(1, len(last7)), 1),
            "sum14": sum(r["total"] for r in series)}

PARQUET_DIRS = ["tdx/ts_daily", "tdx/ts_monthly", "tdx/ts_weekly", "tdx/ts_index_daily"]

# ── 定时任务职能分组（2026-10-18 全量清点，系统噪音 AMD/WPS/OneDrive 不入册）──
# 平台规划总案：docs/2026-10-18_数据平台规划总案.md §3
TASK_GROUPS = [
    ("行情/数据同步", ["TushareSync", "TushareIncrementGuard", "TdxSync", "AdjFactorSync",
                       "QuantMetaSync", "XmapSync", "Quote", "QuoteSnapshot", "CryptoSync",
                       "FutQuant", "KgCl1Sync", "KgDailySync", "NoticeBackfill",
                       "TriplesBackfill", "JevBackfill"]),
    ("舆情/资讯采集", ["XueqiuCrawl", "XueqiuSync", "XueqiuTokenRefresh", "GubaCrawl",
                       "NewsCollect", "NewsAI", "NewsReview"]),
    ("策略扫描", ["BtScan", "ChanlunScan", "ChanlunScanHK", "ChanlunScanUS", "NineDimScan",
                  "WeakScan", "WeakTrackCheck", "RpsDaily", "SeatProfile", "NameChange",
                  "StrongEod", "PickMonitor", "TodaysPicks", "StrategyDaily", "TbeStats",
                  "QuantWatch"]),
    ("告警/晨报/推送", ["AlertPush", "AlertPushMorning", "AlertWatch", "SubPushEod",
                        "SubPushIntraday", "HermesBriefPush", "KgResonanceNotify",
                        "FutQuantNotify", "KgDigest", "DailyBrief0745", "DailyReport",
                        "MorningReport"]),
    ("KG/知识工程", ["KGAnalysis", "KgChainWatchdog", "KgEventShadow", "KgExtractShadow",
                     "KgLeiden", "KgLeidenSummary", "KG-Neo4jWeeklyDump", "KG-QuantMorning",
                     "KG-QuantRetune", "KgDataDashboard"]),
    ("弱者体系", ["WeakDailyHealth", "WeakMonthlyArchive", "WeakMonthlyGuide",
                  "WeakMonthlyLearn", "WeakMonthlyReview", "WeakMonthlySummary",
                  "WeakWeeklyReport"]),
    ("Hermes/门户守护", ["HermesWebUI-Guard", "HermesWebUI-Guard2", "HermesWebUI-Next-Guard",
                         "AI工作站门户API", "AI工作站门户Nginx", "datamart_api_keepalive",
                         "AI工作站推送中台守护8097"]),
    ("数字军团/agent", ["AgentShadowDaily", "AgentMondayCheckpoint", "AgentChiefPush",
                        "AgentShadowEvalWeekly", "AgentWatchdog", "AgentWatchdog2",
                        "A0Patrol", "LegionDailyReview", "LegionMorningMeeting",
                        "LegionOps", "LegionScheduler"]),
    ("风控/影子评估", ["RiskGuardR3Weekly", "RiskGuardShadowDaily", "K33ShadowCheck",
                       "GoldShadow"]),
    ("运维/守护", ["BackupDaily", "OffsiteSync", "OllamaWatchdog", "AI工作站执行失败巡检",
                   "AI工作站调优回测批次", "PytestWeekly", "ColAudit", "MissCheck", "N1Check",
                   "P5cRecheck", "PG-Robot", "JevDriftDaily", "JevFeed-Daily",
                   "JevFeed-Verify", "JevStack"]),
]


def _task_groups(errors):
    """全量计划任务按职能分组探测（Enabled + 每日触发时刻）。"""
    groups = []
    armed_total = 0
    seen = set()
    for gname, names in TASK_GROUPS:
        rows = []
        for n in names:
            seen.add(n)
            t = _read_task(n)
            h = "armed" if t.get("enabled") else ("unknown" if t.get("enabled") is None else "disabled")
            if t.get("enabled"):
                armed_total += 1
            rows.append({"name": n, "health": h, "at": t.get("at", ""),
                         "error": t.get("error")})
        groups.append({"group": gname, "count": len(rows), "armed": sum(1 for r in rows if r["health"] == "armed"), "tasks": rows})
    return {"groups": groups, "total": sum(g["count"] for g in groups),
            "armed_total": armed_total}


def _catalog_summary(errors):
    """行情仓 catalog.json 聚合（233 表 × source × layout，登记真源）。"""
    try:
        cat = json.load(open(os.path.join(DATA, "catalog.json"), encoding="utf-8"))
        tables = cat.get("tables", {})
        by_source = {}
        for t in tables.values():
            s = t.get("source", "?")
            d = by_source.setdefault(s, {"count": 0, "layouts": {}})
            d["count"] += 1
            d["layouts"][t.get("layout", "?")] = d["layouts"].get(t.get("layout", "?"), 0) + 1
        return {"version": cat.get("version"), "updated": cat.get("updated"),
                "table_count": len(tables), "by_source": by_source,
                "hint": "catalog.json 为行情仓登记真源（source/store/schema/layout），"
                        "由 warehouse 进程自动刷新；v3 逐表新鲜度在 store mtime 上做"}
    except Exception as e:
        errors.append("catalog: %s" % str(e)[:60])
        return {"error": str(e)[:80]}


def _ro(db_path):
    return sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)


def _tail(path, n=3):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return [l.rstrip() for l in f.readlines()[-n:]]
    except Exception:
        return []


def _read_task(name):
    """直读计划任务 XML（UTF-16），取 Enabled 与每日触发时刻。"""
    try:
        raw = open(os.path.join(TASKS_DIR, name), "rb").read().decode("utf-16-le", errors="replace")
        m = re.search(r"<StartBoundary>([^<]+)", raw)
        hhmm = m.group(1)[11:16] if m else ""
        return {"enabled": "<Enabled>true</Enabled>" in raw, "at": hhmm}
    except Exception as e:
        return {"enabled": None, "at": "", "error": str(e)[:50]}


def _pool_stats(errors):
    pools = []
    for rel, tables in POOLS:
        db_path = os.path.join(DATA, rel)
        entry = {"db": rel, "size_mb": None, "mtime": None, "tables": []}
        try:
            entry["size_mb"] = os.path.getsize(db_path) // 1048576
            entry["mtime"] = time.strftime("%m-%d %H:%M", time.localtime(os.path.getmtime(db_path)))
        except Exception as e:
            errors.append("stat %s: %s" % (rel, str(e)[:60]))
            entry["error"] = str(e)[:80]
        con = None
        try:
            con = _ro(db_path)
            for t, note in tables:
                row = {"name": t, "note": note}
                try:
                    row["rows"] = con.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
                    col = TODAY_COL.get(t)
                    if col:
                        try:
                            row["today"] = con.execute(
                                "SELECT COUNT(*) FROM %s WHERE %s >= ?" % (t, col), (time.strftime("%Y-%m-%d"),)).fetchone()[0]
                        except Exception:
                            row["today"] = None
                    else:
                        row["today"] = None
                except Exception as e:
                    row["error"] = str(e)[:60]
                entry["tables"].append(row)
        except Exception as e:
            errors.append("open %s: %s" % (rel, str(e)[:60]))
        finally:
            if con:
                con.close()
        pools.append(entry)
    return pools


def _pipeline_states(errors):
    """管道层：播种队列 / 分类追平 / 日报。全部读状态文件与日志尾行。"""
    pipes = {}
    # 播种：状态机（queue_idx/done/streaks）+ 断点档（当前 seed 的类目挖穿数）
    seed = {"queue_len": SEED_QUEUE_LEN, "queue_idx": 0, "done": False,
            "blocked_streak": 0, "low_streak": 0, "last": {}, "current_seed": None,
            "exhausted_cats": None}
    try:
        st = json.load(open(os.path.join(RUNS, "xq_backfill", "seed_auto_state.json"), encoding="utf-8"))
        last = (st.get("rounds") or [{}])[-1]
        seed.update({"queue_idx": st.get("queue_idx", 0), "done": st.get("done", False),
                     "blocked_streak": st.get("blocked_streak", 0), "low_streak": st.get("low_streak", 0),
                     "last": last, "current_seed": st.get("current_seed") or last.get("seed")})
        sc = json.load(open(os.path.join(RUNS, "xq_backfill", "seed_cursor.json"), encoding="utf-8"))
        cur = sc.get(str(seed["current_seed"] or ""), {})
        if isinstance(cur, dict) and cur:
            seed["exhausted_cats"] = sum(1 for v in cur.values()
                                         if isinstance(v, dict) and v.get("exhausted") is True)
    except Exception as e:
        errors.append("seed_state: %s" % str(e)[:60])
    pipes["seed"] = seed
    # 分类追平
    try:
        con = _ro(os.path.join(DATA, "xueqiu", "xueqiu_info.db"))
        total = con.execute("SELECT COUNT(*) FROM xq_status").fetchone()[0]
        done = con.execute("SELECT COUNT(*) FROM xq_status_classified").fetchone()[0]
        con.close()
        pipes["classify"] = {"total": total, "done": done, "pending": total - done,
                             "log_tail": _tail(os.path.join(RUNS, "xq_classify", "xq_classify_status.log"), 3)}
    except Exception as e:
        errors.append("classify: %s" % str(e)[:60])
        pipes["classify"] = {"error": str(e)[:80]}
    # 日报
    tail = _tail(os.path.join(RUNS, "xq_report", "report_status.log"), 2)
    m = re.search(r"posts=(\d+) ingested=(\d+)", tail[-1]) if tail else None
    pipes["report"] = {"log_tail": tail,
                       "posts_today": int(m.group(1)) if m else None,
                       "ingested_today": int(m.group(2)) if m else None}
    return pipes


def overview():
    errors = []
    out = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    # 数据池
    pools = _pool_stats(errors)
    total_rows = sum(t.get("rows", 0) for p in pools for t in p.get("tables", []))
    ingested_today = sum(t.get("today") or 0 for p in pools for t in p.get("tables", []))
    xq_total = xq_done = 0
    for p in pools:
        if p["db"].startswith("xueqiu"):
            for t in p["tables"]:
                if t["name"] == "xq_status":
                    xq_total = t.get("rows", 0)
                elif t["name"] == "xq_status_classified":
                    xq_done = t.get("rows", 0)
    # 打标模型版本分布（治理/自进化观测）
    model_dist = {}
    try:
        con = _ro(os.path.join(DATA, "xueqiu", "xueqiu_info.db"))
        cols = [c[1] for c in con.execute("PRAGMA table_info(xq_status_classified)")]
        if "model" in cols:
            model_dist = {str(r[0]): r[1] for r in con.execute(
                "SELECT COALESCE(model,'?'), COUNT(*) FROM xq_status_classified GROUP BY 1")}
        con.close()
    except Exception as e:
        errors.append("model_dist: %s" % str(e)[:60])
    # parquet 池
    parquet = []
    for rel in PARQUET_DIRS:
        full = os.path.join(DATA, rel)
        n = len(glob.glob(os.path.join(full, "part_*.parquet")))
        if n:
            parquet.append({"dir": rel, "shards": n})
    # 管道
    pipes = _pipeline_states(errors)
    # 每日采拿量（第一标准数据底座）
    daily = _daily_intake(errors)
    # 全量定时任务分组 + 行情仓 catalog 聚合
    task_groups = _task_groups(errors)
    catalog = _catalog_summary(errors)
    # 采集源
    collectors = []
    active = 0
    for key, name, carrier, sched, outp, src in COLLECTORS:
        c = {"key": key, "name": name, "carrier": carrier, "schedule": sched, "output": outp}
        if src == "schtasks":
            t = _read_task(key)
            c["health"] = "armed" if t.get("enabled") else ("unknown" if t.get("enabled") is None else "disabled")
            c["at"] = t.get("at", "")
            if t.get("enabled"):
                active += 1
        elif src == "seed":
            s = pipes.get("seed", {})
            if s.get("done"):
                c["health"] = "done"
            elif s.get("blocked_streak", 0) > 0:
                c["health"] = "blocked"
            else:
                c["health"] = "running"
                active += 1
            c["detail"] = "队列 %s/%s，最近轮 added=%s" % (
                s.get("queue_idx", 0) + 1, s.get("queue_len"), s.get("last", {}).get("added", "?"))
        elif src == "classify":
            cl = pipes.get("classify", {})
            pend = cl.get("pending")
            tail = cl.get("log_tail") or []
            last = tail[-1] if tail else ""
            if pend == 0:
                c["health"] = "complete"
            elif "probe busy" in last:
                c["health"] = "yielding"
            elif "rc=1" in last:
                c["health"] = "backoff"
            else:
                c["health"] = "running"
                active += 1
            c["detail"] = "pending=%s" % pend if pend is not None else "状态未知"
        elif src == "report":
            r = pipes.get("report", {})
            c["health"] = "ok" if r.get("posts_today") is not None else "unknown"
            if r.get("posts_today") is not None:
                active += 1
            c["detail"] = "今日 posts=%s ingested=%s" % (r.get("posts_today"), r.get("ingested_today"))
        c["log_tail"] = pipes.get("report", {}).get("log_tail") if src == "report" else \
            (pipes.get("classify", {}).get("log_tail") if src == "classify" else
             ["%s | %s" % ((pipes.get("seed", {}).get("last", {}) or {}).get("at", ""),
                           "added=%s" % (pipes.get("seed", {}).get("last", {}) or {}).get("added", "?"))] if src == "seed" else [])
        collectors.append(c)
    # 告警
    alerts = []
    pend = pipes.get("classify", {}).get("pending") or 0
    if pend > 1000:
        alerts.append("分类追平 pending=%d（追平 automation 错峰推进中）" % pend)
    seed = pipes.get("seed", {})
    if seed.get("blocked_streak", 0) > 0:
        alerts.append("播种轮被限流 blocked_streak=%d（等恢复窗口）" % seed["blocked_streak"])
    xq_crawl = next((c for c in collectors if c["key"] == "XueqiuCrawl"), {})
    if xq_crawl.get("health") == "armed":
        alerts.append("XueqiuCrawl 18:00 生产首跑待执行（首跑后本平台出现当日增量）")
    out.update({
        "summary": {"total_rows": total_rows, "ingested_today": ingested_today,
                    "structured_rate": round(xq_done / xq_total, 4) if xq_total else None,
                    "xq_total": xq_total, "xq_classified": xq_done,
                    "active_collectors": active, "alerts": alerts},
        "collectors": collectors, "pools": pools, "parquet": parquet,
        "pipelines": pipes, "task_groups": task_groups, "catalog": catalog,
        "daily_intake": daily,
        "governance": {"model_dist": model_dist,
                       "hint": "打标 lineage 字段 model/prompt_version 支持版本换代 A/B；"
                               "Hermes/调度系统直接 GET 本接口（fail-visible JSON）即得全局数据地图"},
        "errors": errors,
    })
    return out


# ─────────────────────────────────────────────────────────────
# v3 指挥操作系统（2026-10-18 老田拍板：可串联 / 可下钻 / 一图看明白）
# 契约：每个实体唯一 id（<层>:<名字>）。
#   GET /api/dataops/map            → 全拓扑（nodes+edges，health 实时合并）
#   GET /api/dataops/entity?id=xxx  → 单实体统一 schema：
#       identity / status / metrics / upstream / downstream / links / logs
# 消费方：dataops.html 拓扑图（点击抽屉下钻，?entity= 深链）、
#         static/dataops_link.js 全站连接条（每页挂自身实体健康点）。
# 新节点接入 = 在 NODES/EDGES/ENTITIES 各加一行，前端零改动。
# ─────────────────────────────────────────────────────────────
NODE_LAYERS = ["服务", "采集", "数据池", "管道", "消费"]

NODES = [
    # 服务层
    {"id": "svc:portal_api", "layer": "服务", "name": "门户 API 8091", "x": 90,  "y": 60},
    {"id": "svc:nginx",      "layer": "服务", "name": "Nginx 8080",    "x": 300, "y": 60},
    {"id": "svc:hermes",     "layer": "服务", "name": "Hermes 8787",   "x": 620, "y": 60},
    {"id": "svc:ollama",     "layer": "服务", "name": "Ollama 11434",  "x": 1010, "y": 60},
    # 采集层（taskgroup 聚合节点，下钻展开明细）
    {"id": "src:XueqiuSync",  "layer": "采集", "name": "雪球同步 17:40",  "x": 90,  "y": 170},
    {"id": "src:XueqiuCrawl", "layer": "采集", "name": "雪球日采 18:00",  "x": 270, "y": 170},
    {"id": "src:GubaCrawl",   "layer": "采集", "name": "股吧采集 17:50",  "x": 450, "y": 170},
    {"id": "pipe:seed",       "layer": "采集", "name": "历史播种 30min",  "x": 630, "y": 170},
    {"id": "tg:行情/数据同步", "layer": "采集", "name": "行情同步×15",     "x": 830, "y": 170},
    {"id": "tg:策略扫描",      "layer": "采集", "name": "策略扫描×16",     "x": 1010, "y": 170},
    {"id": "tg:其他",          "layer": "采集", "name": "其余 8 组×63",    "x": 1105, "y": 250},
    # 数据池层
    {"id": "pool:xueqiu",    "layer": "数据池", "name": "雪球库 3 表",    "x": 90,  "y": 300},
    {"id": "pool:warehouse", "layer": "数据池", "name": "行情仓 233 表",  "x": 400, "y": 300},
    {"id": "pool:quant",     "layer": "数据池", "name": "量化库 6 表",    "x": 650, "y": 300},
    {"id": "pool:parquet",   "layer": "数据池", "name": "Parquet 行情",   "x": 870, "y": 300},
    # 管道层
    {"id": "pipe:classify",  "layer": "管道", "name": "舆情打标",        "x": 180, "y": 410},
    {"id": "pipe:report",    "layer": "管道", "name": "情报日报 19:00",  "x": 400, "y": 410},
    # 消费层（两行：上排情报/审批线，下排绩效/账本线）
    {"id": "page:xq_intel",  "layer": "消费", "name": "情报日报页",      "x": 90,  "y": 530},
    {"id": "page:home",      "layer": "消费", "name": "总览首页",        "x": 250, "y": 530},
    {"id": "page:picks",     "layer": "消费", "name": "今日选股",        "x": 430, "y": 530},
    {"id": "page:approval",  "layer": "消费", "name": "审批注册表",      "x": 590, "y": 530},
    {"id": "page:capability","layer": "消费", "name": "能力看板",        "x": 750, "y": 530},
    {"id": "page:audit",     "layer": "消费", "name": "审计线状态",      "x": 910, "y": 530},
    {"id": "ledger:shadow",  "layer": "消费", "name": "影子策略账本",    "x": 1060, "y": 530},
    {"id": "ledger:riskguard", "layer": "消费", "name": "RiskGuard 账本", "x": 90,  "y": 660},
    {"id": "page:os",        "layer": "消费", "name": "个人 OS 大屏",    "x": 260, "y": 660},
    {"id": "page:perf",      "layer": "消费", "name": "投研绩效看板",    "x": 430, "y": 660},
    {"id": "ledger:perf",    "layer": "消费", "name": "绩效账本",        "x": 600, "y": 660},
    {"id": "page:dataops",   "layer": "消费", "name": "数据指挥台",      "x": 770, "y": 660},
    {"id": "ledger:trace",   "layer": "消费", "name": "血缘链接账本",    "x": 1150, "y": 660},
]

# (from, to, rel, dashed)
EDGES = [
    ("svc:nginx", "svc:portal_api", "反代", False),
    ("svc:hermes", "svc:portal_api", "消费 overview", False),
    ("svc:ollama", "pipe:classify", "算力依赖", False),
    ("src:XueqiuSync", "pool:xueqiu", "入库", False),
    ("src:XueqiuCrawl", "pool:xueqiu", "入库", False),
    ("pipe:seed", "pool:xueqiu", "回填", False),
    ("src:GubaCrawl", "pool:warehouse", "入库", False),
    ("tg:行情/数据同步", "pool:warehouse", "入库", False),
    ("tg:行情/数据同步", "pool:parquet", "行情分片", False),
    ("tg:策略扫描", "pool:quant", "读写", False),
    ("tg:其他", "pool:warehouse", "采集/加工", True),
    ("pool:xueqiu", "pipe:classify", "未打标增量", False),
    ("pipe:classify", "pipe:report", "喂数", False),
    ("pipe:report", "page:xq_intel", "latest 渲染", False),
    ("pool:quant", "page:picks", "选股产出", False),
    ("page:xq_intel", "page:home", "首页情报卡", False),
    ("page:picks", "page:home", "首页交易卡", False),
    ("page:xq_intel", "ledger:riskguard", "R3 校准(未来)", True),
    # agent 运营页 + 平台账本入图（2026-10-18 扩容）
    ("tg:其他", "page:approval", "审批登记", True),
    ("tg:其他", "page:capability", "能力盘点", True),
    ("tg:其他", "page:audit", "审计汇聚", True),
    ("tg:其他", "ledger:shadow", "影子日巡写入", True),
    ("tg:其他", "ledger:trace", "血缘登记", True),
    ("ledger:perf", "page:perf", "绩效渲染", False),
    ("page:home", "page:os", "复用首页 API", False),
]

_SVC_PORTS = {"svc:portal_api": 8091, "svc:nginx": 8080, "svc:hermes": 8787, "svc:ollama": 11434}

# agent_platform 三账本（消费层 ledger 节点：健康探测与实体下钻共用）
AP_DATA = os.path.join(os.path.dirname(QB), "agent_platform", "data")
_LEDGERS = {
    "ledger:shadow": (os.path.join(AP_DATA, "shadow", "shadow_ledger.jsonl"),
                      "影子策略账本：AgentShadowDaily 日巡写入，影子线与官方线对比评审留痕"),
    "ledger:trace":  (os.path.join(AP_DATA, "trace", "trace_links.jsonl"),
                      "血缘链接：agent 任务产出的血缘登记，供 lineage 追溯与审计核对"),
    "ledger:perf":   (os.path.join(AP_DATA, "perf_ledger.jsonl"),
                      "绩效账本：投研建议复盘打分记录，perf.html 投研绩效看板数据源"),
}


def _port_alive(port, timeout=1.0):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def _task_group_health(ov, gname):
    for g in (ov.get("task_groups", {}).get("groups") or []):
        if g["group"] == gname:
            return g
    return None


def _node_health(ov):
    """合并实时健康：服务端口探测 + 采集/管道/池状态 → {id: (health, detail)}"""
    h = {}
    for nid, port in _SVC_PORTS.items():
        h[nid] = ("ok", "端口 %d 在线" % port) if _port_alive(port) else ("down", "端口 %d 不可达" % port)
    by_key = {c["key"]: c for c in ov.get("collectors", [])}
    for nid in ("XueqiuSync", "XueqiuCrawl", "GubaCrawl"):
        c = by_key.get(nid, {})
        # A06 归一化：health 只出受控枚举；'armed'（已布防待触发）语义归 ok，比率信息留 detail
        raw = c.get("health", "unknown")
        hh = {"armed": "ok", "disabled": "down"}.get(raw, raw if raw in ("ok", "down", "unknown") else "unknown")
        dd = c.get("detail") or c.get("at") or ""
        h["src:" + nid] = (hh, ("已布防 " + dd) if raw == "armed" and dd else dd)
    pipes = ov.get("pipelines", {})
    seed = pipes.get("seed", {})
    if seed.get("done"):
        h["pipe:seed"] = ("done", "队列完成")
    elif (seed.get("blocked_streak") or 0) > 0:
        h["pipe:seed"] = ("blocked", "限流让路 streak=%d" % seed["blocked_streak"])
    else:
        h["pipe:seed"] = ("running", "队列 %s/%s" % ((seed.get("queue_idx") or 0) + 1, seed.get("queue_len")))
    cl = pipes.get("classify", {})
    h["pipe:classify"] = ("complete" if cl.get("pending") == 0 else
                          ("yielding" if "probe busy" in (cl.get("log_tail") or [""])[-1] else "running"),
                          "pending=%s" % cl.get("pending"))
    rp = pipes.get("report", {})
    h["pipe:report"] = ("ok" if rp.get("posts_today") is not None else "unknown",
                        "posts=%s" % rp.get("posts_today"))
    for p in ov.get("pools", []):
        nid = "pool:" + p["db"].split("/")[0].replace(".db", "") if "/" in p["db"] else "pool:" + p["db"]
        h[nid] = ("ok" if not p.get("error") else "down",
                  "%s MB · %s" % (p.get("size_mb"), p.get("mtime") or ""))
    h["pool:warehouse"] = ("ok", "catalog v%s · %s 表" % (
        ov.get("catalog", {}).get("version"), ov.get("catalog", {}).get("table_count")))
    h["pool:parquet"] = ("ok", "%s 个分片目录" % len(ov.get("parquet", [])))
    for nid, gname in (("tg:行情/数据同步", "行情/数据同步"), ("tg:策略扫描", "策略扫描")):
        g = _task_group_health(ov, gname)
        if g:
            # A06 归一化：全部在岗 ok / 部分缺失 degraded / 全下线 down；"N/M 在岗" 下沉 detail
            gh = "ok" if g["armed"] == g["count"] else ("degraded" if g["armed"] > 0 else "down")
            h[nid] = (gh, "%d/%d 在岗" % (g["armed"], g["count"]))
        else:
            h[nid] = ("unknown", "")
    total = ov.get("task_groups", {})
    ta, tt = (total.get("armed_total") or 0), (total.get("total") or 0)
    h["tg:其他"] = ("ok" if ta == tt else ("degraded" if ta > 0 else "down"), "%d/%d 在岗 · 8 组聚合" % (ta, tt))
    for nid in ("page:home", "page:xq_intel", "page:picks", "page:dataops",
                "page:approval", "page:capability", "page:perf", "page:audit", "page:os"):
        h[nid] = ("ok", "静态面 8080")
    for nid, (path, _note) in _LEDGERS.items():
        try:
            h[nid] = ("ok", "%d KB" % (os.path.getsize(path) // 1024)) if os.path.exists(path) \
                else ("down", "账本缺失")
        except Exception as e:
            h[nid] = ("unknown", str(e)[:40])
    rg = os.path.join(os.path.dirname(QB), "zerobound-kg", "riskguard_ledger", "ledger.jsonl")
    h["ledger:riskguard"] = ("ok", "%d KB" % (os.path.getsize(rg) // 1024)) if os.path.exists(rg) else ("down", "账本缺失")
    return h


def datamap():
    errors = []
    ov = overview()
    health = _node_health(ov)
    nodes = []
    for n in NODES:
        h, detail = health.get(n["id"], ("unknown", ""))
        nodes.append({"id": n["id"], "layer": n["layer"], "name": n["name"],
                      "x": n["x"], "y": n["y"], "health": h, "detail": detail})
    return {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "layers": NODE_LAYERS,
            "nodes": nodes,
            "edges": [{"from": f, "to": t, "rel": rel, "dashed": d} for f, t, rel, d in EDGES],
            "summary": ov.get("summary", {}), "errors": errors}


def _find_node(nid):
    return next((n for n in NODES if n["id"] == nid), None)


def _downstream(nid):
    return [{"id": t, "rel": rel} for f, t, rel, _ in EDGES if f == nid]


def _upstream(nid):
    return [{"id": f, "rel": rel} for f, t, rel, _ in EDGES if t == nid]


def entity(nid, errors):
    """统一实体下钻 schema：identity/status/metrics/relations/links/logs。"""
    node = _find_node(nid)
    if not node:
        return None
    ov = overview()
    health = _node_health(ov)
    h, detail = health.get(nid, ("unknown", ""))
    out = {"id": nid, "type": node["layer"], "name": node["name"],
           "status": {"health": h, "detail": detail, "at": ov.get("generated_at")},
           "upstream": _upstream(nid), "downstream": _downstream(nid),
           "metrics": [], "links": [], "logs": [], "errors": errors}
    pipes = ov.get("pipelines", {})
    if nid.startswith("pool:"):
        for p in ov.get("pools", []):
            db_key = p["db"].split("/")[0].replace(".db", "") if "/" in p["db"] else p["db"].replace(".db", "")
            if nid == "pool:" + db_key:
                out["metrics"] = [{"k": t["name"], "v": "%s 行（今日 +%s）" % ((t.get("rows") or 0), t.get("today"))}
                                  for t in p.get("tables", [])]
                out["logs"] = ["库文件 mtime: %s" % p.get("mtime"), "体积: %s MB" % p.get("size_mb")]
    elif nid == "pool:warehouse":
        cat = ov.get("catalog", {})
        out["metrics"] = [{"k": "登记表数", "v": cat.get("table_count")},
                          {"k": "catalog 版本", "v": "v%s（更新 %s）" % (cat.get("version"), cat.get("updated"))}] + \
                         [{"k": "source=%s" % k, "v": "%d 表" % v["count"]} for k, v in (cat.get("by_source") or {}).items()]
    elif nid == "pool:parquet":
        out["metrics"] = [{"k": x["dir"], "v": "%d 分片" % x["shards"]} for x in ov.get("parquet", [])]
    elif nid == "pipe:classify":
        cl = pipes.get("classify", {})
        out["metrics"] = [{"k": "总帖", "v": cl.get("total")}, {"k": "已打标", "v": cl.get("done")},
                          {"k": "pending", "v": cl.get("pending")}]
        out["logs"] = cl.get("log_tail") or []
        out["links"] = [{"label": "打开日报页（下游）", "url": "/xq_intel.html"}]
    elif nid == "pipe:report":
        rp = pipes.get("report", {})
        out["metrics"] = [{"k": "今日 posts", "v": rp.get("posts_today")},
                          {"k": "今日入库", "v": rp.get("ingested_today")}]
        out["logs"] = rp.get("log_tail") or []
        out["links"] = [{"label": "打开日报页", "url": "/xq_intel.html"},
                        {"label": "打开指挥台", "url": "/dataops.html"}]
    elif nid == "pipe:seed":
        s = pipes.get("seed", {})
        out["metrics"] = [{"k": "队列进度", "v": "%s/%s 段" % ((s.get("queue_idx") or 0) + 1, s.get("queue_len"))},
                          {"k": "当前 seed", "v": s.get("current_seed")},
                          {"k": "挖穿类目", "v": ("%s/4" % s.get("exhausted_cats")) if s.get("exhausted_cats") is not None else "—"},
                          {"k": "最近轮 added", "v": (s.get("last") or {}).get("added")}]
        out["logs"] = ["%s | seed=%s added=%s" % ((s.get("last") or {}).get("at"), (s.get("last") or {}).get("seed"), (s.get("last") or {}).get("added"))]
    elif nid.startswith("src:") or nid.startswith("tg:"):
        gname = nid[3:]
        g = _task_group_health(ov, gname)
        if g:
            out["metrics"] = [{"k": t["name"], "v": "%s%s" % (t["health"], ("@" + t["at"]) if t.get("at") else "")}
                              for t in g["tasks"]]
            out["logs"] = ["组内 %s/%s 在岗" % (g["armed"], g["count"])]
        else:
            c = next((c for c in ov.get("collectors", []) if c["key"] == gname), {})
            out["metrics"] = [{"k": "载体", "v": c.get("carrier")}, {"k": "节奏", "v": c.get("schedule")},
                              {"k": "健康", "v": c.get("health")}]
            out["logs"] = c.get("log_tail") or []
    elif nid.startswith("svc:"):
        out["metrics"] = [{"k": "端口", "v": _SVC_PORTS.get(nid)}, {"k": "状态", "v": h}]
        if nid == "svc:hermes":
            out["links"] = [{"label": "打开 Hermes WebUI", "url": "http://127.0.0.1:8787"}]
        elif nid == "svc:portal_api":
            out["links"] = [{"label": "GET /api/dataops/overview", "url": "/api/dataops/overview?view=summary"}]
    elif nid.startswith("page:"):
        url = {"page:home": "/home.html", "page:xq_intel": "/xq_intel.html",
               "page:picks": "/picks.html", "page:dataops": "/dataops.html",
               "page:approval": "/approvals.html", "page:capability": "/capability.html",
               "page:perf": "/perf.html", "page:audit": "/audit.html",
               "page:os": "/os.html"}.get(nid, "/")
        out["links"] = [{"label": "打开页面", "url": url}]
        if nid == "page:xq_intel":
            rp = pipes.get("report", {})
            out["metrics"] = [{"k": "今日 posts", "v": rp.get("posts_today")}]
        elif nid == "page:perf":
            try:
                out["metrics"] = [{"k": "绩效账本行数",
                                   "v": sum(1 for _ in open(_LEDGERS["ledger:perf"][0], encoding="utf-8"))}]
            except Exception as e:
                errors.append("page:perf: %s" % str(e)[:60])
    elif nid in _LEDGERS:
        path, note = _LEDGERS[nid]
        try:
            n_lines = sum(1 for _ in open(path, encoding="utf-8")) if os.path.exists(path) else 0
            st = os.stat(path)
            out["metrics"] = [{"k": "行数", "v": n_lines},
                              {"k": "文件体积", "v": "%d KB" % (st.st_size // 1024)},
                              {"k": "mtime", "v": time.strftime("%m-%d %H:%M", time.localtime(st.st_mtime))}]
        except Exception as e:
            errors.append("%s: %s" % (nid, str(e)[:60]))
        out["logs"] = [note]
    elif nid == "ledger:riskguard":
        rg = os.path.join(os.path.dirname(QB), "zerobound-kg", "riskguard_ledger", "ledger.jsonl")
        n_lines = sum(1 for _ in open(rg, encoding="utf-8")) if os.path.exists(rg) else 0
        out["metrics"] = [{"k": "拦截事件", "v": n_lines},
                          {"k": "R3 真实拦截", "v": "532（2026-09~10）"}]
        out["logs"] = ["雪球舆情校准：待实时线积累 2-4 周后启动拦截-舆情一致性核对"]
    return out


class Handler:
    def _dataops(self, qs):
        args = self._norm_qs(qs)
        view = (args.get("view") or [""])[0].strip()
        data = overview()
        if view == "summary":  # 轻量视图：Hermes 快路径只需摘要
            keep = {k: data[k] for k in ("generated_at", "summary", "errors")}
            self._json(keep)
            return
        self._json(data)

    def _dataops_map(self):
        self._json(datamap())

    def _dataops_entity(self, qs):
        args = self._norm_qs(qs)
        nid = (args.get("id") or [""])[0].strip()
        if not nid:
            self._json({"error": "缺少 id 参数，示例 ?id=pipe:classify"}, 400)
            return
        errors = []
        data = entity(nid, errors)
        if data is None:
            known = " / ".join(n["id"] for n in NODES)
            self._json({"error": "未知实体 %r" % nid, "known": known}, 404)
            return
        self._json(data)
