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

PARQUET_DIRS = ["tdx/ts_daily", "tdx/ts_monthly", "tdx/ts_weekly", "tdx/ts_index_daily"]


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
        "pipelines": pipes,
        "governance": {"model_dist": model_dist,
                       "hint": "打标 lineage 字段 model/prompt_version 支持版本换代 A/B；"
                               "Hermes/调度系统直接 GET 本接口（fail-visible JSON）即得全局数据地图"},
        "errors": errors,
    })
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
