# -*- coding: utf-8 -*-
"""动作流中心 API — flow.html 后端（P4 批7 mixin）

统一聚合工作站「执行 / 推送 / 分析 / 归档」四类事件为单一时间线，
供 flow.html 消费；事件 schema 即数据池对接契约（字段稳定可直接入池）。

事件 schema：
  {id, ts, kind: run|push|analysis|archive|tune,
   actor, action, status, summary, artifacts: [{name, size}]}

端点（由 portal_api.do_GET/do_POST 分发）：
  GET  /api/flow/stats?days=7                 按日统计 + 进行中任务 + JEV 健康块 + 白名单产线
  GET  /api/flow/events?day=YYYY-MM-DD&kind=  当日事件流（缺省近 48h，倒序）
  GET  /api/flow/artifact?p=<相对路径>        产物预览（白名单根内）
  POST /api/flow/rerun {"task": ..., "caller": ...}
                                      白名单产线立即重跑（走 _MUTATION_POSTS 鉴权）。
                                      R4：幂等阀前置（当日已有产出/非运行窗拒绝，
                                      响应带 blocked 字段）；caller 归因入 observe
                                      账本（agent_platform/data/llm_tool_audit.jsonl）

数据源：
  news.db runs 表（执行）· work/push_flow.jsonl（推送）· work/tune_requests.jsonl（调优）
  · 产物目录 mtime 扫描（分析/归档）· schtasks State=Running（进行中，stats 内）
"""
import os
import logging
import json
import time
import sqlite3
import subprocess
import datetime as _dt

from api_common import ROOT, STATION, REPORTS_DIR, run, NO_WINDOW

# ---- 路径候选（news_collector 在 C:\aios 一级 junction 下）----
NEWS_DB_CAND = [
    r"C:\aios\news_collector\news.db",
    os.path.join(os.path.dirname(STATION), "news_collector", "news.db"),
]
WORK = os.path.join(ROOT, "work")


def _zhice_pg_password() -> str:
    """PG 凭据回落链：env → zhice 活部署 .env.local V2_DB_PASSWORD → 旧默认（仅兜底）。

    2026-10-30 R5：V2 口令轮换后未注入 env 的消费方会认证失败（gate_seen 块
    静默降级为 None）；补文件回落链防漂移（下次 8091 重启生效）。
    """
    pw = os.environ.get("ZHICE_PG_PASSWORD", "").strip()
    if pw:
        return pw
    try:
        with open(r"C:\项目\zhice-os-v2-windows-20260920\code\.env.local", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("V2_DB_PASSWORD=") and not line.startswith("#"):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return "zhice_pass"
PUSH_FLOW = os.path.join(WORK, "push_flow.jsonl")
TUNE_REQ = os.path.join(WORK, "tune_requests.jsonl")

# ---- JEV 健康指标数据源（jev-eval 复核域 + news.db runs 执行域）----
JEV_RD = os.path.join(STATION, "jev-eval", "review_data")
TRIPLES = os.path.join(JEV_RD, "triples.jsonl")
TRIPLES_OUT = os.path.join(JEV_RD, "triples_outcome.jsonl")
GRADUATION = os.path.join(JEV_RD, "graduation_status_v2.json")

# ---- 产物扫描表：(根, glob 模式, 事件类, 产线名, 动作) ----
_ART_SCAN = [
    (ROOT, "data", "todays_picks.json", "analysis", "TodaysPicks", "今日选股刷新"),
    (ROOT, "data", "pick_verdicts.json", "analysis", "TodaysPicks", "信号后验账本刷新"),
    (ROOT, "data", "seat_profiles.json", "analysis", "SeatProfile", "席位画像刷新"),
    (ROOT, "data", "strong_latest.json", "analysis", "StrongEod", "强势板块扫描"),
    (ROOT, "qd_daily_data", "snap_*.json", "archive", "QdSnapshot", "全市场快照归档"),
    (REPORTS_DIR, "", "rps_latest.json", "analysis", "RpsDaily", "RPS 选股刷新"),
    (REPORTS_DIR, "", "premarket_latest.json", "analysis", "AI盘前分析", "盘前分析刷新"),
    (REPORTS_DIR, "", "nine_dim_scores.json", "analysis", "NineDimScan", "九维评分刷新"),
    (REPORTS_DIR, "", "strong_list_*.jsonl", "archive", "StrongEod", "强势板块归档"),
    (REPORTS_DIR, "", "weak_scan_*.csv", "archive", "WeakScan", "弱势扫描归档"),
    (REPORTS_DIR, "", "graph_schema_*.json", "archive", "KgDailySync", "图谱 schema 归档"),
    (REPORTS_DIR, "snap", "*-top.html", "archive", "ReportSnap", "日报快照归档"),
    (REPORTS_DIR, "charts", "*.png", "archive", "ReportSnap", "净值/回测曲线归档"),
    (STATION, os.path.join("agent_platform", "data", "morning_brief"), "*.md",
     "archive", "MorningBrief", "晨会纪要归档"),
    (STATION, os.path.join("k_stock", "data", "review_minutes"), "*.json",
     "archive", "PaperReview", "复盘会纪要归档"),
]

# 预览白名单根（artifact 服务的越权闸门）
_PREV_ROOTS = [ROOT, WORK, REPORTS_DIR,
               os.path.join(STATION, "agent_platform", "data"),
               os.path.join(STATION, "k_stock", "data"),
               os.path.join(REPORTS_DIR, "snap"),
               os.path.join(REPORTS_DIR, "charts")]

# 重跑白名单（只放只读产线型任务；管理/守护/系统任务一律不放）
RERUN_OK = {
    "TodaysPicks", "StrongEod", "RpsDaily", "NineDimScan", "WeakScan",
    "ChanlunScan", "ChanlunScanHK", "ChanlunScanUS", "SeatProfile",
    "MorningReport", "DailyReport", "AI盘前分析", "QuoteSnapshot",
    "TushareSync", "JevFeed-Daily", "KG-QuantMorning", "NewsAI",
    # R4 扩容（影子链三任务，均带幂等阀，见 _rerun_blocked）：
    # AgentMondayCheckpoint 复盘档只读（报告红线），明确不进白名单。
    "AgentShadowDaily", "AgentChiefPush", "AgentShadowEvalWeekly",
}

# ---- R4 幂等阀数据面（影子链产出/日志面；STATION 下 agent_platform/data）----
_AP_DATA = os.path.join(STATION, "agent_platform", "data")
SHADOW_LEDGER = os.path.join(_AP_DATA, "shadow", "shadow_ledger.jsonl")
CHIEF_LOG = os.path.join(_AP_DATA, "shadow", "chief_last_run.log")
EVAL_LOG = os.path.join(_AP_DATA, "shadow", "eval_last_run.log")
# observe 账本（与 8093 org_registry_server.TOOL_AUDIT 同一文件，写侧单源不另立账）
OBSERVE_AUDIT = os.path.join(_AP_DATA, "llm_tool_audit.jsonl")

_KIND_CN = {"run": "执行", "push": "推送", "analysis": "分析", "archive": "归档",
            "tune": "调优", "live": "进行中"}
_FLOW_CACHE = {"t": 0, "events": [], "stats": None}
_FLOW_TTL = 30


# ---- R4 幂等阀（重跑安全阀：当日已有产出/非运行窗 → 拒绝，绝不真触发）----


def _ledger_has_date(path, today):
    """jsonl 账本已有 date==today 的行 → True；路径不存在/坏行 → False（宁放行）。

    只读探测（逐行 json 解析，坏行跳过——与 eval_shadow.load_ledger 同款容错）。
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    o = json.loads(ln)
                except Exception:
                    continue
                if str(o.get("date") or "")[:10] == today:
                    return True
    except OSError:
        return False
    return False


def _log_today_has(path, today, needle=None):
    """日志 mtime 为当日且（可选）含标记串 → True；缺文件/读失败 → False。

    mtime 当日口径：产线日志是覆盖重写（shadow_automation.ps1 的 cmd /c
    重定向），文件存在即「最近一次运行」留痕，mtime 当日 = 当日跑过。
    """
    try:
        st = os.stat(path)
        if _dt.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d") != today:
            return False
        if needle is None:
            return True
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return needle in f.read()
    except OSError:
        return False


def _rerun_blocked(task, now=None):
    """幂等阀判定：返回拦截原因（str）或 None=放行。只读探测，绝不真触发。

    口径表（R4 验收②）：
      AgentShadowDaily      ① now 为周六/周日 → 拒（排程仅周一~五；周末重跑
                              会污染 5 日观察账本——「无交易日判断会污染」判例）
                            ② shadow_ledger.jsonl 已有当日行 → 拒（同日同票幂等
                              先例 run_shadow.py；重跑=当日第二份样本）
      AgentChiefPush        chief_last_run.log 当日 mtime 且含 SEND_OK → 拒
                            （门3 判据：SEND_OK=飞书已推，重跑=重复推送）
      AgentShadowEvalWeekly eval_last_run.log 当日 mtime → 拒（当日已出件）
    路径不存在/不可读一律视为无产出放行（宁重跑勿误拦）；既有 17 任务
    返回 None（沿用无阀语义，不改变既有行为）。
    """
    now = now or _dt.datetime.now()
    today = now.strftime("%Y-%m-%d")
    if task == "AgentShadowDaily":
        if now.weekday() >= 5:  # 周一=0 … 周六=5 周日=6
            return "影子日巡排程仅周一~五（今日为周末，拒绝重跑：防污染 5 日观察账本）"
        if _ledger_has_date(SHADOW_LEDGER, today):
            return ("当日影子账本已有产出行（shadow_ledger.jsonl date=%s），"
                    "重跑将产生重复当日样本" % today)
        return None
    if task == "AgentChiefPush":
        if _log_today_has(CHIEF_LOG, today, "SEND_OK"):
            return ("当日日报已推送成功（chief_last_run.log 今日含 SEND_OK），"
                    "重跑将重复推送飞书")
        return None
    if task == "AgentShadowEvalWeekly":
        if _log_today_has(EVAL_LOG, today):
            return "当日周评已出件（eval_last_run.log 今日 mtime），重跑将覆盖当日评估"
        return None
    return None


def _observe_rerun(caller, task, ok, blocked=None):
    """rerun 归因入 observe 账本（R4 验收③）。

    落点=agent_platform/data/llm_tool_audit.jsonl——与 8093
    org_registry_server._observe 同文件同 schema（ts/caller/endpoint/method/
    ok），追加 task/blocked 两键；写侧单源，不另立 rerun 专属审计文件
    （K-ORG-112 §3 第 2 条）。fail-open：写失败只 WARN 不影响主流程（判例同款）。
    """
    try:
        d = os.path.dirname(OBSERVE_AUDIT)
        if d:
            os.makedirs(d, exist_ok=True)
        rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "caller": caller,
               "endpoint": "/api/flow/rerun", "method": "POST",
               "ok": bool(ok), "task": task}
        if blocked:
            rec["blocked"] = blocked
        with open(OBSERVE_AUDIT, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001
        print("[warn] flow rerun observe 写失败（fail-open 不影响主流程）: %s" % e)


def _today():
    return _dt.date.today().strftime("%Y-%m-%d")


def _news_db():
    for p in NEWS_DB_CAND:
        if os.path.exists(p):
            return p
    return None


def _runs_events(since_ts):
    """runs 表 → run 事件（近 N 天）"""
    db = _news_db()
    if not db:
        return []
    out = []
    try:
        con = sqlite3.connect(db, timeout=5)
        rows = con.execute(
            "SELECT id, kind, ts, ok, new_count, push_count, detail "
            "FROM runs WHERE ts >= ? ORDER BY ts DESC", (since_ts,)).fetchall()
        con.close()
    except Exception:
        return []
    for rid, kind, ts, ok, new_count, push_count, detail in rows:
        summary = "new=%s push=%s" % (new_count, push_count)
        try:
            d = json.loads(detail or "{}")
        except Exception:
            d = {}
        if kind == "collect" and d:
            parts = ["%s%d" % (k, v.get("new", 0)) for k, v in list(d.items())[:4]
                     if isinstance(v, dict)]
            if parts:
                summary += " · " + "/".join(parts)
        elif kind == "jev_feed" and d:
            src = d.get("src") or {}
            parts = ["%s+%s" % (k, v.get("inserted", 0)) for k, v in src.items()
                     if isinstance(v, dict)]
            if parts:
                summary += " · " + " ".join(parts)
        out.append({
            "id": "run:%s" % rid, "ts": ts, "kind": "run",
            "actor": "news_collector." + kind,
            "action": {"collect": "新闻采集轮", "ai": "新闻AI判定",
                       "digest": "日报摘要", "review": "信号复核",
                       "jev_feed": "JEV 情报投喂"}.get(kind, kind),
            "status": "ok" if ok else "fail", "summary": summary,
            "artifacts": [],
        })
    return out


def _jsonl_tail_events(path, since_date, mapper, max_lines=4000):
    """通用：读 jsonl 尾部 N 行 → 事件（倒序扫描提前退出）"""
    out = []
    if not os.path.exists(path):
        return out
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()[-max_lines:]
        for ln in reversed(lines):
            ln = ln.strip()
            if not ln:
                continue
            try:
                obj = json.loads(ln)
            except Exception:
                continue
            if str(obj.get("date") or obj.get("ts", ""))[:10] < since_date:
                continue
            ev = mapper(obj)
            if ev:
                out.append(ev)
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "api_flow.py", exc)
    return out


def _push_mapper(o):
    return {
        "id": "push:%s:%s:%s" % (o.get("ts"), o.get("kind"), o.get("target", "")),
        "ts": o.get("ts"), "kind": "push",
        "actor": str(o.get("kind") or "?"),
        "action": "飞书推送（%s）" % o.get("channel", "?"),
        "status": "ok" if o.get("ok") else "fail",
        "summary": "count=%s → %s" % (o.get("count", "?"), str(o.get("target", ""))[:18]),
        "artifacts": [],
    }


def _tune_mapper(o):
    stage = str(o.get("stage") or "?")
    bits = [x for x in (str(o.get("req_id") or ""), str(o.get("note") or "")) if x]
    if o.get("scope"):
        bits.append("[%s]" % o.get("scope"))
    return {
        "id": "tune:%s:%s" % (o.get("ts"), o.get("req_id", "")),
        "ts": o.get("ts") or o.get("date", ""),
        "kind": "tune",
        "actor": str(o.get("operator") or "tune_gate"),
        "action": "调优 · %s" % stage,
        "status": "ok" if stage in ("deployed", "receipt", "done") else stage,
        "summary": " · ".join(bits)[:80],
        "artifacts": [],
    }


def _artifact_events(since_date):
    """产物目录 mtime 扫描 → analysis/archive 事件"""
    import glob as _glob
    out = []
    now = time.time()
    for base, sub, pattern, kind, actor, action in _ART_SCAN:
        d = os.path.join(base, sub) if sub else base
        try:
            fps = _glob.glob(os.path.join(d, pattern))
        except Exception:
            continue
        for fp in fps:
            try:
                st = os.stat(fp)
            except OSError:
                continue
            ts = _dt.datetime.fromtimestamp(st.st_mtime)
            if ts.strftime("%Y-%m-%d") < since_date:
                continue
            out.append({
                "id": "art:%s" % fp, "ts": ts.strftime("%Y-%m-%d %H:%M"),
                "kind": kind, "actor": actor, "action": action,
                "status": "ok",
                "summary": "%s · %.1f KB" % (os.path.basename(fp), st.st_size / 1024.0),
                "artifacts": [{"name": os.path.basename(fp), "size": st.st_size,
                               "path": _rel_path(fp)}],
                "_age_h": round((now - st.st_mtime) / 3600.0, 1),
            })
    out.sort(key=lambda e: e["ts"], reverse=True)
    return out


def _rel_path(fp):
    """白名单根内的相对路径（供 artifact 预览），找不到则返回绝对路径"""
    for r in _PREV_ROOTS:
        try:
            if os.path.commonpath([os.path.abspath(fp), os.path.abspath(r)]) == os.path.abspath(r):
                return os.path.relpath(fp, r).replace("\\", "/")
        except ValueError:
            continue
    return fp.replace("\\", "/")


def _collect_events(day=None):
    """全部事件源聚合（倒序）。day=None → 近 48h；否则单日。"""
    if day:
        since_date = day
        since_ts = day + " 00:00"
    else:
        now = _dt.datetime.now() - _dt.timedelta(hours=48)
        since_date = now.strftime("%Y-%m-%d")
        since_ts = now.strftime("%Y-%m-%d %H:%M")
    evs = []
    evs += _runs_events(since_ts)
    evs += _jsonl_tail_events(PUSH_FLOW, since_date, _push_mapper)
    evs += _jsonl_tail_events(TUNE_REQ, since_date, _tune_mapper)
    evs += _artifact_events(since_date)
    evs.sort(key=lambda e: str(e.get("ts") or ""), reverse=True)
    for e in evs:
        e.pop("_age_h", None)
    return evs


def _running_tasks():
    """schtasks State=Running 的任务名（api_common.run 返回 stdout 字符串，失败静默空）"""
    try:
        out = run(["powershell", "-NoProfile", "-Command",
                   "(Get-ScheduledTask -TaskPath '\\') | Where-Object "
                   "{$_.State -eq 'Running'} | ForEach-Object {$_.TaskName}"],
                  timeout=25)
        if out:
            return [ln.strip() for ln in str(out).splitlines()
                    if ln.strip() and len(ln.strip()) < 60]
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "api_flow.py", exc)
    return []


def _jev_health():
    """JEV 三岗位健康块（本地文件 + news.db；PG gate_seen 可选，失败静默降级）

    指标口径：
      feed     最近一次 jev_feed 投喂（ts/ok/新增数）+ 今日次数
      ai       最近一次新闻 AI 判定（ts/ok）+ 今日次数
      triples  校准三元组总量 / 未复核积压 / 今日新增 / 近7日 jev_error 比例
      outcome  5 日后验回填量 + 最近回填时间
      graduation  毕业水位（adopted 版本 + 计算时间）
      gate_seen   PG 已见水位（行数 + 最近时间；连不上则为 None）
    综合灯：投喂断供>48h 或最近投喂失败 → bad；
            断供>24h / 积压>300 / 近7日 error>50% / AI 最近失败 → warn；否则 ok
    """
    today = _today()
    now = _dt.datetime.now()
    h = {"lamp": "warn", "feed": None, "ai": None,
         "triples_total": 0, "unreviewed": 0, "triples_today": 0,
         "err7_ratio": None, "outcome": None, "graduation": None,
         "gate_seen": None, "feed_age_h": None}

    # 1) triples：积压 / 今日新增 / 近7日 jev_error 比例（只扫尾部 4000 行）
    try:
        with open(TRIPLES, "r", encoding="utf-8", errors="replace") as f:
            lines = [ln for ln in f.read().splitlines() if ln.strip()][-4000:]
        d7 = (now - _dt.timedelta(days=7)).strftime("%Y-%m-%d")
        n7 = err7 = 0
        for ln in lines:
            try:
                o = json.loads(ln)
            except Exception:
                continue
            h["triples_total"] += 1
            if o.get("status") == "unreviewed":
                h["unreviewed"] += 1
            ts = str(o.get("ts") or "")
            if ts[:10] == today:
                h["triples_today"] += 1
            if ts[:10] >= d7:
                n7 += 1
                if str(o.get("reason") or "") == "jev_error":
                    err7 += 1
        if n7:
            h["err7_ratio"] = round(err7 / n7, 3)
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "api_flow.py", exc)

    # 2) runs：投喂 / AI 判定最近态 + 今日次数
    db = _news_db()
    if db:
        try:
            con = sqlite3.connect(db, timeout=5)
            for key, kind in (("feed", "jev_feed"), ("ai", "ai")):
                row = con.execute(
                    "SELECT ts, ok, new_count FROM runs WHERE kind=? "
                    "ORDER BY ts DESC LIMIT 1", (kind,)).fetchone()
                if row:
                    tot, ok_sum = con.execute(
                        "SELECT count(*), COALESCE(sum(ok),0) FROM runs "
                        "WHERE kind=? AND ts>=?", (kind, today)).fetchone()
                    h[key] = {"last_ts": row[0], "last_ok": bool(row[1]),
                              "last_new": row[2], "today": tot or 0,
                              "today_ok": ok_sum or 0}
            con.close()
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "api_flow.py", exc)

    # 3) 后验回填 + 毕业水位
    try:
        with open(TRIPLES_OUT, "r", encoding="utf-8", errors="replace") as f:
            ol = [ln for ln in f.read().splitlines() if ln.strip()]
        if ol:
            last = json.loads(ol[-1])
            h["outcome"] = {"count": len(ol),
                            "last_at": last.get("backfilled_at") or last.get("ts")}
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "api_flow.py", exc)
    try:
        with open(GRADUATION, "r", encoding="utf-8") as f:
            h["graduation"] = json.load(f)
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "api_flow.py", exc)

    # 4) PG gate_seen（凭据与 jev_common.py 同源；3s 超时静默降级为 None）
    try:
        import psycopg2
        con = psycopg2.connect(
            host="127.0.0.1", port=int(os.environ.get("ZHICE_PG_PORT", "5446")),
            user=os.environ.get("ZHICE_PG_USER", "zhice"),
            password=_zhice_pg_password(),
            dbname=os.environ.get("ZHICE_PG_DB", "zhice_os_v2"),
            connect_timeout=3)
        cur = con.cursor()
        cur.execute("SELECT count(*) FROM jev_gate_seen")
        n = cur.fetchone()[0]
        cur.execute("SELECT max(seen_at) FROM jev_gate_seen")
        mx = cur.fetchone()[0]
        h["gate_seen"] = {"count": n, "last": str(mx or "")[:19]}
        con.close()
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "api_flow.py", exc)

    # 5) 综合灯（阈值见 docstring）
    feed = h.get("feed") or {}
    try:
        lt = _dt.datetime.strptime(str(feed.get("last_ts") or "")[:16],
                                   "%Y-%m-%d %H:%M")
        age_h = (now - lt).total_seconds() / 3600.0
    except ValueError:
        age_h = 1e9
    h["feed_age_h"] = round(age_h, 1) if age_h < 1e8 else None
    if age_h > 48 or (feed and not feed.get("last_ok")):
        h["lamp"] = "bad"
    elif age_h > 24 or h["unreviewed"] > 300 \
            or (h["err7_ratio"] or 0) > 0.5 \
            or ((h.get("ai") or {}).get("last_ok") is False):
        h["lamp"] = "warn"
    else:
        h["lamp"] = "ok"
    return h


def build_flow_stats(days=7):
    evs_all = []
    day0 = _dt.date.today()
    since = (day0 - _dt.timedelta(days=days)).strftime("%Y-%m-%d")
    for i in range(days):
        d = (day0 - _dt.timedelta(days=i)).strftime("%Y-%m-%d")
        evs_all += _collect_events(d)
    per_day = {}
    for e in evs_all:
        key = str(e.get("ts") or "")[:10]
        if key < since:
            continue
        c = per_day.setdefault(key, {"date": key, "runs": 0, "pushes": 0,
                                     "analyses": 0, "archives": 0, "tunes": 0})
        k = e["kind"]
        if k == "run":
            c["runs"] += 1
        elif k == "push":
            c["pushes"] += 1
        elif k == "analysis":
            c["analyses"] += 1
        elif k == "archive":
            c["archives"] += 1
        elif k == "tune":
            c["tunes"] += 1
    days_arr = [per_day.get((day0 - _dt.timedelta(days=i)).strftime("%Y-%m-%d"),
                            {"date": (day0 - _dt.timedelta(days=i)).strftime("%Y-%m-%d"),
                             "runs": 0, "pushes": 0, "analyses": 0, "archives": 0,
                             "tunes": 0})
                for i in range(days)]
    days_arr.reverse()
    totals = {k: sum(d[k] for d in days_arr)
              for k in ("runs", "pushes", "analyses", "archives", "tunes")}
    return {
        "days": days_arr, "totals": totals,
        "today": per_day.get(_today(), {"date": _today(), "runs": 0, "pushes": 0,
                                        "analyses": 0, "archives": 0, "tunes": 0}),
        "running": _running_tasks(),
        "jev": _jev_health(),
        "rerun_whitelist": sorted(RERUN_OK),
        "kind_cn": _KIND_CN,
        "generated_at": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


class Handler:
    """动作流中心 mixin（挂 portal_api.Handler 继承链）"""

    def _flow_stats(self, qs):
        params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
        try:
            days = min(int(params.get("days", "7")), 30)
        except ValueError:
            days = 7
        if _FLOW_CACHE["stats"] and time.time() - _FLOW_CACHE["t"] < 60:
            data = _FLOW_CACHE["stats"]
        else:
            data = build_flow_stats(days)
            _FLOW_CACHE["stats"] = data
            _FLOW_CACHE["t"] = time.time()
        self._json(data)

    def _flow_events(self, qs):
        from urllib.parse import unquote
        params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
        if (time.time() - _FLOW_CACHE["t"] > _FLOW_TTL or not _FLOW_CACHE["events"]):
            _FLOW_CACHE["events"] = _collect_events()
            _FLOW_CACHE["t"] = time.time()
            _FLOW_CACHE["stats"] = None  # 事件刷新则统计同批失效
        evs = _FLOW_CACHE["events"]
        day = unquote(params.get("day", "")).strip()
        kind = unquote(params.get("kind", "")).strip()
        q = unquote(params.get("q", "")).strip().lower()
        try:
            limit = min(int(params.get("limit", "400")), 1000)
        except ValueError:
            limit = 400
        if day:
            evs = [e for e in evs if str(e.get("ts") or "").startswith(day)]
        if kind:
            evs = [e for e in evs if e.get("kind") == kind]
        if q:
            evs = [e for e in evs if q in json.dumps(e, ensure_ascii=False).lower()]
        self._json({"count": len(evs[:limit]), "events": evs[:limit],
                    "cached_at": _dt.datetime.fromtimestamp(_FLOW_CACHE["t"]).strftime(
                        "%Y-%m-%d %H:%M:%S")})

    def _flow_artifact(self, qs):
        from urllib.parse import unquote
        params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
        rel = unquote(params.get("p", "")).replace("\\", "/").strip("/")
        if not rel or ".." in rel:
            self._json({"error": "bad path"}, 400)
            return
        target = None
        for r in _PREV_ROOTS:
            cand = os.path.abspath(os.path.join(r, rel))
            try:
                if os.path.commonpath([cand, os.path.abspath(r)]) == os.path.abspath(r) \
                        and os.path.isfile(cand):
                    target = cand
                    break
            except ValueError:
                continue
        if not target:
            self._json({"error": "not found or outside whitelist"}, 404)
            return
        st = os.stat(target)
        ext = os.path.splitext(target)[1].lower()
        resp = {"path": rel, "size": st.st_size,
                "mtime": _dt.datetime.fromtimestamp(st.st_mtime).strftime(
                    "%Y-%m-%d %H:%M"), "type": "text", "data": None}
        try:
            if ext == ".json" or ext == ".jsonl":
                with open(target, "r", encoding="utf-8", errors="replace") as f:
                    raw = f.read(512 * 1024)
                if ext == ".jsonl":
                    resp["type"] = "jsonl"
                    resp["data"] = [json.loads(ln) for ln in raw.splitlines()
                                    if ln.strip()][-200:]
                else:
                    resp["type"] = "json"
                    obj = json.loads(raw)
                    resp["data"] = obj if _json_size(obj) < 900 * 1024 else \
                        {"_truncated": True, "preview": _json_trunc(obj)}
            elif ext in (".png", ".jpg", ".jpeg", ".gif"):
                import base64
                with open(target, "rb") as f:
                    b = f.read(4 * 1024 * 1024)
                resp["type"] = "image"
                resp["data"] = "data:%s;base64,%s" % (
                    {"png": "image/png", "jpg": "image/jpeg",
                     "jpeg": "image/jpeg", "gif": "image/gif"}[ext[1:]],
                    base64.b64encode(b).decode("ascii"))
            elif ext in (".html", ".htm"):
                with open(target, "r", encoding="utf-8", errors="replace") as f:
                    resp["data"] = f.read(200 * 1024)
                resp["type"] = "html"
            else:  # md / csv / txt / log
                with open(target, "r", encoding="utf-8", errors="replace") as f:
                    resp["data"] = f.read(200 * 1024)
                resp["type"] = "md" if ext == ".md" else "text"
        except Exception as exc:
            resp["type"] = "error"
            resp["data"] = "读取失败: %s" % exc
        self._json(resp)

    def _flow_rerun(self, body):
        b = body or {}
        task = str(b.get("task", "")).strip()
        # caller 归因（K-ORG-112 ④ 缺省注入口径）：body 可选，缺省 portal-console
        caller = (str(b.get("caller") or "").strip()[:64]) or "portal-console"
        if task not in RERUN_OK:
            self._json({"error": "task not in whitelist",
                        "whitelist": sorted(RERUN_OK), "caller": caller}, 400)
            return
        # R4 幂等阀（安全阀）：当日已有产出/非运行窗 → 拒绝，绝不真触发 schtasks
        blocked = _rerun_blocked(task)
        if blocked:
            _observe_rerun(caller, task, False, blocked)
            self._json({"ok": False, "task": task, "blocked": blocked,
                        "caller": caller})
            return
        try:
            # NO_WINDOW 是 creationflags 整型（api_common.run 同款用法）——
            # 原实现 **NO_WINDOW 把整型当 kwargs 解包必 TypeError（潜伏 bug 修复）
            r = subprocess.run(["schtasks", "/Run", "/TN", task],
                               capture_output=True, timeout=20,
                               creationflags=NO_WINDOW)
            ok = r.returncode == 0
            _observe_rerun(caller, task, ok)
            self._json({"ok": ok, "task": task, "caller": caller,
                        "out": (r.stdout or b"").decode("gbk", "replace").strip()[:200]})
        except Exception as exc:
            _observe_rerun(caller, task, False)
            self._json({"ok": False, "task": task, "caller": caller,
                        "error": str(exc)}, 500)


def _json_size(obj):
    try:
        return len(json.dumps(obj, ensure_ascii=False))
    except Exception:
        return 1 << 30


def _json_trunc(obj, depth=0):
    """超限 JSON 的浅层预览（dict 留键、list 留前 5 项）"""
    if depth > 3:
        return "..."
    if isinstance(obj, dict):
        return {k: _json_trunc(v, depth + 1) for k, v in list(obj.items())[:40]}
    if isinstance(obj, list):
        return [_json_trunc(v, depth + 1) for v in obj[:5]] + (["...(%d more)" % (len(obj) - 5)] if len(obj) > 5 else [])
    return obj
