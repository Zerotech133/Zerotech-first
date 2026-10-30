# -*- coding: utf-8 -*-
"""P4 批 6：回测域 Mixin（backtest 页族/pulse/custom 批族）。
Handler 壳经基座多继承并入（禁 import *）。
"""
import datetime
import logging
import json
import os
import re
import subprocess
import sys
import time
from api_common import (
    _read_json,
    log_exc,
    STATION,
    REPORTS_DIR,
    _env_market_key,
    QBT_DIR,
    pill_nav,
    ROOT,
    MKT_LABEL,
    CANDLE_CN, _matrix_cards, _stock_chip_summary, _stock_sector_rows,
    _HERMES_FAB_HTML)


# ── K255 勾选股票自定义批次回测（前端勾选 → 批次 JSONL → 引擎子进程 → 轮询）──
_BT_CUSTOM_BATCHES = os.path.join(REPORTS_DIR, "rps_custom_batches.jsonl")


_BT_CUSTOM_REPORT = os.path.join(REPORTS_DIR, "rps_custom_backtest.json")


_BT_CUSTOM_STATE = os.path.join(REPORTS_DIR, "rps_custom_backtest.state.json")


_BT_CUSTOM_LOG = os.path.join(STATION, "quant_backtest", "logs",
                              "k255_custom_bt.out")


def get_backtest():
    try:
        with open(os.path.join(STATION, "quant_backtest", "last_backtest.json"),
                  "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        log_exc("get_backtest:last_backtest.json")  # C2批1
        return None


# 领域第二行导航分组（单一数据源）：组名 None = 该领域平铺不折叠。
# pill_nav 按组渲染 details 折叠（当前页所在组自动展开），
# SECTION_PAGES 由它拍平派生——两处永不漂移。
# 2026-10-30 P69（拆分方案 B2）：回测页 HTML builder 迁至 api_backtest_pages_html.py。
from api_backtest_pages_html import (  # noqa: F401  再导出（拆分 B2）
    fut_page_html,
    sellpoint_page_html,
)

def _bt_norm_code(c):
    """K255：6 位裸码按板块补前缀；已带 sz/sh/bj 前缀原样收；其余 None。"""
    c = str(c or "").strip().lower()
    if len(c) == 8 and c[:2] in ("sz", "sh", "bj") and c[2:].isdigit():
        return c
    if len(c) == 6 and c.isdigit():
        if c[:2] in ("60", "68", "90"):
            return "sh" + c
        if c[:2] in ("00", "30", "20"):
            return "sz" + c
        if c[:1] in ("4", "8") or c[:2] == "92":
            return "bj" + c
    return None


def _bt_custom_start(rec):
    """收 codes/hold → 写自定义批次 JSONL → spawn 产线引擎子进程（--run）。

    批次/报告/状态文件独立于产线（rps_custom_*），互不污染；
    date=latest 由引擎解析为最新可回测信号日（门户不持有交易日历）。
    """
    codes, seen = [], set()
    for c in (rec.get("codes") or []):
        nc = _bt_norm_code(c)
        if nc and nc not in seen:
            seen.add(nc)
            codes.append(nc)
    if len(codes) > 100:
        codes = codes[:100]
    try:
        holds = sorted({int(x) for x in
                        str(rec.get("hold") or "5,10,20").split(",")
                        if x.strip()})
    except Exception:
        # 豁免（C2批1）：hold 参数解析失败回默认值，控制流非数据吞错
        holds = [5, 10, 20]
    holds = [h for h in holds if 1 <= h <= 250] or [5, 10, 20]
    if not codes:
        return {"ok": False, "error": "no valid codes"}
    batch = {"date": str(rec.get("date") or "latest"),
             "source": "custom_web", "env": {"safe": True},
             "picks": [{"code": c, "market": "CN", "action": "买入"}
                       for c in codes]}
    state = {"submitted_at": time.strftime("%Y-%m-%d %H:%M:%S"),
             "n_codes": len(codes),
             "hold": ",".join(str(h) for h in holds)}
    os.makedirs(REPORTS_DIR, exist_ok=True)
    with open(_BT_CUSTOM_BATCHES, "w", encoding="utf-8") as f:
        f.write(json.dumps(batch, ensure_ascii=False) + "\n")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    with open(_BT_CUSTOM_LOG, "ab") as logf:
        p = subprocess.Popen(
            [sys.executable, os.path.join(QBT_DIR, "qpzm_daily_batch.py"),
             "--run", "--batches", _BT_CUSTOM_BATCHES,
             "--out", _BT_CUSTOM_REPORT, "--hold", state["hold"]],
            stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
            env=env, cwd=QBT_DIR)
    state["pid"] = p.pid
    with open(_BT_CUSTOM_STATE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    return dict(state, ok=True)


def _bt_custom_status():
    """轮询：report.generated_at > state.submitted_at 即完成；
    未完成且 pid 消亡=进程失败（dead）。"""
    state = _read_json(_BT_CUSTOM_STATE) or {}
    if not isinstance(state, dict):
        state = {}
    if not state.get("submitted_at"):
        return {"ok": True, "running": False, "report": None}
    rep = _read_json(_BT_CUSTOM_REPORT) or {}
    if not isinstance(rep, dict):
        rep = {}
    gen = str((rep.get("meta") or {}).get("generated_at") or "")
    done = bool(gen) and gen > str(state["submitted_at"])
    running, dead = False, False
    if not done:
        pid = state.get("pid")
        alive = True
        if pid:
            try:
                r = subprocess.run(["tasklist", "/FI", "PID eq %s" % pid],
                                   capture_output=True, text=True,
                                   timeout=10)
                alive = str(pid) in (r.stdout or "")
            except Exception:
                alive = True
        running, dead = bool(alive), not alive
    return {"ok": True, "running": running, "dead": dead,
            "submitted_at": state.get("submitted_at"),
            "n_codes": state.get("n_codes"), "hold": state.get("hold"),
            "report": rep if done else None}


# ── K-STOCK-275 组合回测任务队列（paper.html 组合回测器真接线）──────────
# 动线：前端登记 → 本队列真实落盘 →（10-16 开闸后包 C 引擎消费）跑批 →
# 结果回填。开闸前不跑批（验收冻结区拍板，不造假）：任务如实「排队中」，
# 进度条显示队列位置与开闸闸门；引擎侧后续只写 _CBT_STATE_JSON 覆盖状态
# （queued→running(pct/stage)→done(result)/dead），本模块零改动兼容。
_CBT_JOBS_JSONL = os.path.join(REPORTS_DIR, "cbt_jobs.jsonl")
_CBT_STATE_JSON = os.path.join(REPORTS_DIR, "cbt_queue_state.json")
_CBT_GATE = "2026-10-16"          # 引擎开闸日（K-STOCK 冻结拍板：10-13~15 验收冻结）


def _cbt_start(rec):
    """POST /api/combo_backtest：登记一个组合回测任务（纯落盘排队，不跑引擎）。"""
    manager = str(rec.get("manager") or "director_apex").strip()[:64]
    trader = str(rec.get("trader") or "").strip()[:64]
    analysts = [str(a).strip()[:64] for a in (rec.get("analysts") or [])
                if str(a).strip()][:16]
    start = str(rec.get("start") or "").strip()[:10]
    ln = str(rec.get("len") or "3m").strip()[:4]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", start):
        return {"ok": False, "error": "start 须为 YYYY-MM-DD"}
    if ln not in ("1m", "3m", "6m", "1y"):
        return {"ok": False, "error": "len 须为 1m/3m/6m/1y"}
    if not trader:
        return {"ok": False, "error": "trader 必填"}
    now = datetime.datetime.now()
    jid = "cbt-" + now.strftime("%Y%m%d-%H%M%S") + "-" + os.urandom(2).hex()
    job = {"id": jid, "ts": now.strftime("%Y-%m-%d %H:%M:%S"),
           "manager": manager, "trader": trader, "analysts": analysts,
           "start": start, "len": ln, "status": "queued"}
    os.makedirs(REPORTS_DIR, exist_ok=True)
    with open(_CBT_JOBS_JSONL, "a", encoding="utf-8") as f:
        f.write(json.dumps(job, ensure_ascii=False) + "\n")
    return {"ok": True, "job": job, "gate": _CBT_GATE}


def _cbt_status():
    """GET /api/combo_backtest_jobs：任务清单（≤20 条倒序）+ 队列位置 +
    引擎状态覆盖合并（queued/running/done/dead + pct/stage/result/error）。"""
    jobs = []
    if os.path.exists(_CBT_JOBS_JSONL):
        with open(_CBT_JOBS_JSONL, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    j = json.loads(line)
                except Exception:
                    continue
                if isinstance(j, dict) and j.get("id"):
                    jobs.append(j)
    st = _read_json(_CBT_STATE_JSON)
    if not isinstance(st, dict):
        st = {}
    qn = 0
    for j in jobs:                    # jsonl 顺序 = 提交顺序
        ov = st.get(j["id"]) or {}
        if not isinstance(ov, dict):
            ov = {}
        j["status"] = str(ov.get("status") or j.get("status") or "queued")
        if j["status"] == "queued":
            qn += 1
            j["queue_pos"] = qn
        for k in ("pct", "stage", "result", "error"):
            if k in ov:
                j[k] = ov[k]
    return {"ok": True, "gate": _CBT_GATE, "total": len(jobs),
            "jobs": list(reversed(jobs[-20:]))}


# ── 个股全景页辅助（量化中心下钻目标） ──────────────────
def _bt_resolve(q):
    """输入(名/代码/watchlist代码) → watchlist 条目；失败返回 None。"""
    import re as _re
    # ts_code 形态（000002.SZ/600519.SH/920088.BJ，KG 系页面跳转格式）
    # → 归一化小写两段式（sh600519）重解析，递归一层即止
    m = _re.fullmatch(r"(\d{6})\.(SH|SZ|BJ|sh|sz|bj)", str(q or "").strip())
    if m:
        return _bt_resolve("%s%s" % (m.group(2).lower(), m.group(1)))
    try:
        sys.path.insert(0, os.path.join(STATION, "news_collector"))
        import stock_analyze
        e = stock_analyze.resolve_stock(q)
        if e and e.get("name"):
            return e
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "api_backtest.py", exc)
    # A股 RPS 榜形态（sh605177）：直接成条目，名称从快照 tops 反查
    if _re.fullmatch(r"[a-z]{2}\d{6}", q):
        nm = ""
        try:
            snap = json.load(open(os.path.join(
                STATION, "quant_backtest", "reports",
                "rps_latest.json"), encoding="utf-8"))
            for t in snap.get("tops", {}).get("CN", []):
                if t.get("sym") == "%s.%s" % (q[2:8], q[:2].upper()):
                    nm = t.get("name") or ""
                    break
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "api_backtest.py", exc)
        if not nm:
            # K18-FIX：榜外票（不在 RPS tops）名称从图谱 Security 反查
            try:
                sys.path.insert(0, os.path.join(STATION, "quant_backtest"))
                import kg_portal as _KP
                r = _KP._q("MATCH (s:Security {ts_code:$tc}) "
                           "RETURN s.name AS nm LIMIT 1",
                           tc="%s.%s" % (q[2:8], q[:2].upper()))
                nm = ((r or [{}])[0] or {}).get("nm") or ""
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "api_backtest.py", exc)
        return {"name": nm or q.upper(), "code": q, "aliases": []}
    try:
        wl = json.load(open(os.path.join(STATION, "news_collector",
                        "news_config.json"), encoding="utf-8"))["watchlist"]
    except Exception:
        return None
    for it in wl:
        if q in (it.get("code"), it.get("name")) or \
                q in (it.get("aliases") or []):
            return {"name": it.get("name") or q, "code": it.get("code", ""),
                    "aliases": it.get("aliases") or []}
    return None


def _bt_jev_news(name, aliases, hours=24 * 7):
    """近7天该标的关联的已判断新闻（news.jev）。"""
    import sqlite3
    from datetime import datetime as _dt, timedelta as _td
    since = (_dt.now() - _td(hours=hours)).strftime("%Y-%m-%d %H:%M")
    kws = {name} | {a for a in aliases if len(str(a)) >= 2}
    kws.discard("")
    out = []
    try:
        db = sqlite3.connect(os.path.join(STATION, "news_collector",
                                          "news.db"))
        rows = db.execute(
            "SELECT ts, title, jev FROM news WHERE jev IS NOT NULL "
            "AND created_at>=? ORDER BY id DESC LIMIT 3000",
            (since,)).fetchall()
        db.close()
    except Exception:
        log_exc("_bt_jev_news:news.db 查询")  # C2批1
        return out
    for ts, title, j in rows:
        if not title or not any(k in title for k in kws):
            continue
        try:
            jv = json.loads(j)
        except Exception:
            continue
        t = ts or ""
        if len(t) < 16 and t[:2].isdigit() and "-" in t[:6] \
                and not t.startswith("20"):
            # 部分新闻源 ts 无年份（如 "09-29 04:06"）：补当前年；
            # 跨年回看（拼出的日期在"未来"超1天）回退一年，保证
            # 时间轴比较与按日归组（/api/kline news 键）可用
            try:
                _p = _dt.strptime(t[:5], "%m-%d")
                _y = _dt.now().year
                if (_dt(_y, _p.month, _p.day) - _dt.now()).days > 1:
                    _y -= 1
                t = "%04d-%s" % (_y, t)
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "api_backtest.py", exc)
        out.append({"ts": t[:16], "title": title,
                    "bias_cn": jv.get("bias_cn", "?"),
                    "conf": round((jv.get("conf") or 0) * 100),
                    "reason": jv.get("reason", "")})
        if len(out) >= 8:
            break
    return out


def _pulse_exit_line(path, tag):
    """K57：读任务日志最后一条 '<tag> exit=' 行 → (code, ts)。
    兼容 OPS-1004 句柄缓存后的数字码；空槽/路径槽返回原样供 sub 展示。"""
    last = None
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for ln in f:
                if ln.startswith(tag + " exit="):
                    last = ln.strip()
    except Exception:
        return None, None
    if not last:
        return None, None
    parts = last[len(tag) + 6:].split(None, 1)
    return parts[0], (parts[1].strip() if len(parts) > 1 else "")


def _pulse_fresh(ts_text, hours=28):
    """锚点时间是否在 hours 小时内（ISO/T 带时区 / 'YYYY-MM-DD HH:MM'）。"""
    if not ts_text:
        return False
    import datetime as _dt
    try:
        s = str(ts_text).strip()
        if "T" in s:
            t = _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
            if t.tzinfo:
                t = t.astimezone().replace(tzinfo=None)
        else:
            t = _dt.datetime.strptime(s[:16], "%Y-%m-%d %H:%M")
        return (_dt.datetime.now() - t).total_seconds() < hours * 3600
    except Exception:
        return False


def _pulse_ago(ts_text):
    """锚点距今多久（人性化："42分" / "3.6时" / "2.1天"；空/不可解析→""）。"""
    if not ts_text:
        return ""
    import datetime as _dt
    try:
        s = str(ts_text).strip()
        if "T" in s:
            t = _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
            if t.tzinfo:
                t = t.astimezone().replace(tzinfo=None)
        else:
            t = _dt.datetime.strptime(s[:16], "%Y-%m-%d %H:%M")
        sec = (_dt.datetime.now() - t).total_seconds()
        if sec < 0:
            sec = 0
        if sec < 3600:
            return "%d分" % (sec // 60)
        if sec < 86400:
            return "%.1f时" % (sec / 3600)
        return "%.1f天" % (sec / 86400)
    except Exception:
        return ""


def build_daily_pulse():
    """K57 大屏 P1「每日更新流水」：夜链/晨链八任务时点+健康锚。
    全部读文件锚（json 内嵌 ts / task 日志 exit= 行 / jsonl 尾行），
    不碰 schtasks、不扫 parquet——大屏 60s 轮询零重量级调用。"""
    items = []

    def add(name, when, ts, ok=True, sub=""):
        items.append({"name": name, "when": when, "ts": ts or "",
                      "ok": bool(ok), "sub": sub,
                      "fresh": _pulse_fresh(ts),
                      "ago": _pulse_ago(ts)})

    code, ts = _pulse_exit_line(r"C:\Temp\tushare_sync_task.log",
                                "tushare_sync")
    add("TushareSync 四市场", "23:30", ts, ok=(code == "0"),
        sub=("exit=" + str(code)) if code else "未见 exit 行")
    rps = _read_json(os.path.join(REPORTS_DIR, "rps_latest.json"))
    add("RPS 共振强度榜", "23:35", rps.get("ts", ""),
        sub="数据日 " + str(rps.get("latest", "")))
    pf = _read_json(os.path.join(REPORTS_DIR, "portfolio.json"))
    add("虚拟账户跑批", "23:50", pf.get("ts", ""))
    code, ts = _pulse_exit_line(r"C:\Temp\daily_report_task.log", "daily")
    add("统一日报 + 备份", "03:00", ts, ok=(code == "0"),
        sub=("exit=" + str(code)) if code else "未见 exit 行")
    try:
        tail = None
        with open(os.path.join(QBT_DIR, "data", "chanlun_pool.jsonl"),
                  encoding="utf-8") as f:
            for ln in f:
                if ln.strip():
                    tail = ln
        row = json.loads(tail) if tail else {}
        add("缠论扫描 ChanlunScan", "05:50",
            (row.get("first_seen") or "").replace("Z", ""),
            sub="最新信号日 " + str(row.get("detect_date", "")))
    except Exception:
        add("缠论扫描 ChanlunScan", "05:50", "", sub="池文件不可读")
    gc = _read_json(os.path.join(QBT_DIR, "data", "graph_check",
                                 "latest.json"))
    add("KG 二十步链体检", "06:20", gc.get("ts", ""),
        ok=(gc.get("ok") is True) if gc else False,
        sub=("kg_check " + ("ok" if gc.get("ok") else "有红项"))
        if gc else "无体检快照")
    nd = _read_json(os.path.join(QBT_DIR, "data", "nine_dim_scores.json"))
    add("九维 NineDimScan", "06:40", nd.get("generated_at", ""),
        sub="数据日 " + str(nd.get("trade_date", "")))
    ap = _read_json(os.path.join(STATION, "news_collector", "outbox",
                                 "alert_push_last.json"))
    add("预警推送", "07:10/16:05", ap.get("ts", ""),
        ok=(ap.get("ok") is True) if ap else False,
        sub=str(ap.get("mode", "")) if ap else "无推送记录")
    return items



def _sa_worker(jid, q):
    try:
        sys.path.insert(0, os.path.join(STATION, "news_collector"))
        import stock_analyze
        d = stock_analyze.analyze_detail(q)
        d["card"] = False
        if d.get("ok"):  # SYSTEM 上下文无 lark-cli 凭证：入队，由
            # 「AI个股分析轮询」任务(Administrator)每 5 分钟代推飞书卡
            try:
                rec = {k: d.get(k) for k in (
                    "name", "bias", "strength", "news_n", "px", "stat",
                    "outlook", "judged")}
                with open(os.path.join(STATION, "news_collector",
                                       "sa_push_queue.jsonl"), "a",
                          encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                d["card"] = "queued"
            except OSError as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "api_backtest.py", exc)
        SA_JOBS[jid] = {"status": "done", "d": d}
        if d.get("ok"):
            _sa_hist_append(d)
    except Exception as ex:
        import traceback
        SA_JOBS[jid] = {"status": "done",
                        "d": {"ok": False, "msg": "分析异常：%s" % str(ex)[:200]}}
        try:  # 线程内异常落盘，便于排查门户任务上下文问题
            with open(os.path.join(STATION, "news_collector",
                                   "sa_page_error.log"), "a",
                      encoding="utf-8") as f:
                f.write("[%s] %s\n" % (q, traceback.format_exc()))
        except OSError as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "api_backtest.py", exc)
    finally:
        SA_BUSY["v"] = False
def _sa_hist_read(n=8):
    try:
        with open(SA_HIST, encoding="utf-8") as f:
            lines = [x for x in f.read().splitlines() if x.strip()]
        return [json.loads(x) for x in lines[-n:]][::-1]
    except Exception:
        log_exc("_sa_hist_read:sa_history.jsonl")  # C2批1
        return []
def _sa_hist_append(rec):
    try:
        os.makedirs(os.path.dirname(SA_HIST), exist_ok=True)
        with open(SA_HIST, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "api_backtest.py", exc)
SA_HIST = os.path.join(STATION, "news_collector", "sa_history.jsonl")
SA_BUSY = {"v": False}
# ── 个股分析（极简入口：一个输入框，后台跑 analyze_detail） ──
SA_JOBS = {}
def strategies_data():
    """注册表（mtime 缓存）+ 轻量实时补充：weak 在案数。"""
    try:
        mt = os.path.getmtime(STRATEGIES_FILE)
    except OSError:
        mt = 0.0
    if _STRAT_MTIME["data"] is None or mt != _STRAT_MTIME["t"]:
        reg = _read_json(STRATEGIES_FILE)
        _STRAT_MTIME.update(t=mt, data=reg)
    reg = _STRAT_MTIME["data"]
    # 深拷贝 metrics：否则 insert 会写进缓存里的共享 list，随调用次数累积重复
    items = [dict(s, metrics=list(s.get("metrics") or []))
             for s in (reg.get("strategies") or [])]
    # weak 在案数 = 状态流 jsonl 计数（在案即有跟踪流）
    try:
        act = len([f for f in os.listdir(os.path.join(STATION, "reports"))
                   if f.startswith("weak_track_") and f.endswith(".jsonl")])
    except OSError:
        act = None
    for s in items:
        if s.get("id") == "weak" and act is not None:
            s.setdefault("metrics", []).insert(0, {"k": "在案", "v": "%d 只" % act})
    return {"ts": time.strftime("%Y-%m-%d %H:%M"),
            "meta": reg.get("_meta") or {},
            "strategies": items}
_STRAT_MTIME = {"t": 0.0, "data": None}
# ── 策略资产注册表（策略中心/首页组合区/导航单一数据源，K36）──
# 上新三步（详见 /strategies 页 SOP）：strategies.json 加条目 →
# 策略页面挂路由 → 计划任务上线；本表驱动三处 UI，前端零改动。
STRATEGIES_FILE = os.path.join(STATION, "portal", "strategies.json")


class Handler:  # Mixin 壳：经基座多继承并入
    _BT_CSS = ("<style>@import url('/static/tokens.css');"
               "body{background:var(--bg-page,#0d1117);color:var(--text,#c9d1d9);"
               "font:14px/1.6 system-ui;margin:0;padding:24px}"
               "h1{font-size:20px;color:var(--link,#58a6ff);margin:0 0 4px}"
               "h2{font-size:15px;color:#3fd6c5;margin:24px 0 8px}"
               "table{border-collapse:collapse;width:100%;margin-top:8px}"
               "td,th{border:1px solid var(--border,#30363d);padding:6px 10px;"
               "font-size:13px;text-align:left}"
               "th{background:var(--bg-raised,#161b22);color:var(--text-dim,#8b949e)}"
               ".card{border:1px solid var(--border,#30363d);border-radius:8px;"
               "padding:12px 16px;background:var(--bg-raised,#161b22);margin-top:8px}"
               ".dim{font-size:11px;color:var(--text-dim,#8b949e)}"
               ".buy{color:var(--color-up,#f85149)}.watch{color:var(--color-warn,#d29922)}"
               ".avoid{color:var(--color-dn,#3fb950)}"
               "a{color:var(--link,#58a6ff);text-decoration:none}"
               ".nav{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 18px}"
               ".nav a,.nav span{display:inline-block;padding:6px 13px;"
               "border:1px solid var(--border,#30363d);border-radius:16px;"
               "color:var(--link,#58a6ff);"
               "text-decoration:none;font-size:12.5px;background:var(--bg-raised,#161b22)}"
               ".nav span{color:var(--text,#e6edf3);border-color:var(--link,#58a6ff)}"
               ".tag{display:inline-block;padding:1px 8px;border-radius:10px;"
               "font-size:11.5px;margin-right:8px}"
               ".t-bull{background:#3a1219;color:var(--color-up,#f85149)}"
               ".t-bear{background:#0f2e1b;color:var(--color-dn,#3fb950)}"
               ".t-neu{background:#2b2f1a;color:var(--color-warn,#d29922)}"
               ".b-long{color:var(--color-up,#f85149);font-weight:600}"
               ".b-flat{color:var(--text-dim,#8b949e)}"
               ".b-short{color:var(--color-dn,#3fb950);font-weight:600}"
               "input{background:var(--bg-page,#0d1117);color:var(--text,#c9d1d9);"
               "border:1px solid var(--border,#30363d);border-radius:8px;"
               "padding:10px 14px;font-size:15px;width:280px}"
               "button{background:#238636;color:#fff;border:none;"
               "border-radius:8px;padding:10px 18px;font-size:15px;"
               "cursor:pointer}"
               "/* 方法论矩阵：8 张卡一字排开，点卡下钻 */"
               ".mgrid{display:grid;grid-template-columns:"
               "repeat(auto-fill,minmax(165px,1fr));gap:8px;margin-top:8px}"
               ".mcard{border:1px solid var(--border,#30363d);border-radius:8px;"
               "padding:10px 12px;background:var(--bg-raised,#161b22);display:block;"
               "color:var(--text,#c9d1d9);text-decoration:none}"
               ".mcard:hover{border-color:var(--link,#58a6ff);background:#1c2a3a}"
               ".mcard .mt{font-size:11px;color:var(--text-dim,#8b949e);margin-bottom:4px}"
               ".mcard .mv{font-size:14px;font-weight:600;line-height:1.3}"
               ".mcard .mu{font-size:11px;color:var(--link,#58a6ff);margin-top:4px}"
               ".mcard.mute{opacity:.55}"
               "</style>")


    def _bt_sectors(self):
        """板块热力页：news_collector/sectors.json（复盘任务落盘）。"""
        import html as _html
        data = {}
        try:
            with open(os.path.join(STATION, "news_collector",
                                   "sectors.json"), encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "api_backtest.py", exc)
        items = data.get("items") or []
        mx = max((abs(s["pct"]) for s in items), default=1.0) or 1.0
        rows = "".join(
            '<div style="display:flex;align-items:center;margin:3px 0">'
            '<div style="width:9em;overflow:hidden;text-overflow:ellipsis;'
            'white-space:nowrap;color:#c9d1d9;font-size:12px" title="%s">%s'
            '</div><div style="flex:1;background:#161b22;border-radius:4px;'
            'overflow:hidden"><div style="height:14px;width:%.1f%%;'
            'margin-left:%.1f%%;background:%s"></div></div>'
            '<div style="width:5em;text-align:right;font-size:12px;'
            'color:%s">%+.2f%%</div></div>'
            % (_html.escape(s["name"]), _html.escape(s["name"]),
               abs(s["pct"]) / mx * 50,
               50.0 if s["pct"] >= 0 else 50 - abs(s["pct"]) / mx * 50,
               "#f85149" if s["pct"] >= 0 else "#3fb950",
               "#f85149" if s["pct"] >= 0 else "#3fb950", s["pct"])
            for s in items)
        head = ("🔥 行业板块热力 · %s（%d 个：%d 涨 / %d 跌）" % (
            data.get("ts", "—"), data.get("count", len(items)),
            data.get("up", 0), (data.get("count", len(items))
                                - data.get("up", 0)))) if items else \
            "🔥 行业板块热力（暂无数据：交易日 15:30 复盘后生成）"
        # 暗色药丸导航（排除当前页 sectors）
        nav = pill_nav("sectors")
        body = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,"
                "initial-scale=1'><title>板块热力</title></head>"
                "<body style='background:#0d1117;color:#c9d1d9;"
                "font-family:Microsoft YaHei,Arial;margin:0;padding:20px'>"
                "<div style='max-width:760px;margin:0 auto'>"
                "<h2 style='font-size:18px'>%s</h2>%s%s"
                "<div style='color:#58606a;font-size:11px;margin-top:16px'>"
                "数据：东方财富/新浪 · 复盘任务落盘 · 不构成投资建议"
                "</div></div></body></html>"
                % (_html.escape(head), nav, rows)).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


    def _bt_report_page(self, qs):
        """研报全文页（本地结构化库直读，零外链）：盘前报告第七节标题下钻。
        u=pdf_url → report_texts.jsonl 记录 → 结构化头部 + 正文全文。"""
        import html as _h
        import sys as _sysr
        from urllib.parse import parse_qs, unquote as _uqr
        if QBT_DIR not in _sysr.path:
            _sysr.path.insert(0, QBT_DIR)
        body = [self._BT_CSS, "<h1>📄 券商研报 · 本地全文</h1>"]
        try:
            import report_fetch
            texts = report_fetch.load_texts()
        except Exception as ex:
            self._html("".join(body) + "<div class='err'>正文库不可用："
                       "%s</div>" % _h.escape(str(ex)[:120]))
            return
        u = _uqr((parse_qs(qs).get("u") or [""])[0]).strip()
        rec = texts.get(u)
        if not rec:
            self._html("".join(body) + "<div class='err'>该研报正文尚未"
                       "入库（盘前任务会自动抓取，明早可见；或稍后刷新"
                       "盘前报告触发）。</div><div class='dim'>url: %s</div>"
                       % _h.escape(u[:120]))
            return
        body.append(
            "<div class='card' style='margin-bottom:12px'>"
            "<div style='font-size:17px;font-weight:600'>%s</div>"
            "<div class='dim' style='margin-top:8px'>%s · %s · %s · %s"
            " · %d 页 · %s 字 · 入库 %s</div>%s"
            "<div class='dim' style='margin-top:6px'>来源 PDF：%s"
            "（已本地结构化，无需访问远端）</div></div>"
            "<div style='margin-top:10px'><a href='/premarket'>← 返回"
            "盘前报告</a> · <a href='/bt/analysts'>分析师图谱 →</a></div>"
            % (_h.escape(rec.get("title") or ""),
               _h.escape(rec.get("code") or ""),
               _h.escape(rec.get("org") or ""),
               _h.escape(rec.get("rating") or ""),
               _h.escape(rec.get("date") or ""),
               rec.get("pages") or 0, rec.get("chars") or 0,
               _h.escape(rec.get("fetched_at") or ""),
               (("<div style='margin-top:6px'>分析师：%s</div>" % " ｜ "
                 .join("%s（%s）<span class='dim'>%s %s</span>"
                       % (_h.escape(a.get("name") or ""),
                          _h.escape(a.get("org") or ""),
                          _h.escape(a.get("cert") or ""),
                          _h.escape(a.get("email") or ""))
                       for a in rec.get("analysts") or []))
                if rec.get("analysts") else ""),
               _h.escape(rec.get("url") or "")))
        pp = rec.get("post_perf") or {}
        if pp:
            segs = []
            for k, lab in (("d1", "1日"), ("d5", "5日"),
                           ("d10", "10日"), ("d20", "20日")):
                v = pp.get(k)
                segs.append("<span class='%s'>%s %s</span>"
                            % ("buy" if (v or 0) > 0 else (
                                "avoid" if (v or 0) < 0 else "dim"),
                               lab, ("%+.2f%%" % v) if v is not None else "—"))
            body.append(
                "<div class='card' style='margin-bottom:12px'>"
                "<b>股评后走势</b>（基准 %s 收盘 %.2f）：%s</div>"
                % (_h.escape(pp.get("base_date") or "?"),
                   pp.get("base_close") or 0, " ".join(segs)))
        # AI 语义结构化卡片（Ollama 抽取，JSON + 置信度）
        ai = rec.get("ai")
        if ai:
            bmap = {"bull": ("看多", "buy"), "bear": ("看空", "avoid"),
                    "neutral": ("中性", "dim")}
            blab, bcls = bmap.get(ai.get("bias"), ("中性", "dim"))
            body.append(
                "<div class='card' style='margin-bottom:12px'>"
                "<div style='display:flex;gap:10px;align-items:center;"
                "flex-wrap:wrap'><span class='%s'><b>%s</b></span>"
                "<span class='tag'>%s</span>"
                "<span class='dim'>置信度 %.0f%% · %s · 抽取 %s</span></div>"
                "<div style='margin-top:8px'>判断理由：%s</div>%s%s</div>"
                % (bcls, blab, _h.escape(ai.get("event_type") or ""),
                   (ai.get("conf") or 0) * 100,
                   _h.escape(ai.get("model") or ""),
                   _h.escape(ai.get("ts") or ""),
                   _h.escape(ai.get("reason") or ""),
                   ("<div style='margin-top:6px'>业绩亮点：%s</div>"
                    % _h.escape(ai.get("earnings"))
                    if ai.get("earnings") else ""),
                   ("<div style='margin-top:6px'>目标价："
                    "<b>%s</b></div>" % _h.escape(ai.get("target_price"))
                    if ai.get("target_price") else "")))
            eps = ai.get("eps_forecast") or []
            pts = ai.get("core_points") or []
            rks = ai.get("risks") or []
            if eps or pts or rks:
                li = ""
                if eps:
                    li += ("<div style='margin-top:8px'>📈 "
                           "<b>盈利预测</b><ul style='margin:4px 0 0 20px'>"
                           + "".join("<li>%s</li>" % _h.escape(x)
                                     for x in eps) + "</ul></div>")
                if pts:
                    li += ("<div style='margin-top:8px'>💡 "
                           "<b>核心观点</b><ul style='margin:4px 0 0 20px'>"
                           + "".join("<li>%s</li>" % _h.escape(x)
                                     for x in pts) + "</ul></div>")
                if rks:
                    li += ("<div style='margin-top:8px'>⚠️ "
                           "<b>风险提示</b><ul style='margin:4px 0 0 20px'>"
                           + "".join("<li>%s</li>" % _h.escape(x)
                                     for x in rks) + "</ul></div>")
                body.append("<div class='card' style='margin-bottom:12px'>"
                            "%s</div>" % li)
        # 原文折叠（AI 卡片优先阅读，原文保底可查）
        body.append(
            "<details><summary style='cursor:pointer;padding:6px 0'>"
            "📖 研报原文全文（%s 字）</summary>"
            "<div class='card' style='white-space:pre-wrap;font-size:14px;"
            "line-height:1.9;max-height:70vh;overflow:auto'>%s</div></details>"
            % (rec.get("chars") or 0, _h.escape(rec.get("text") or "(空正文)")))
        self._html("".join(body))


    def _bt_ind_drill_page(self, qs):
        """行业下钻页：成分股当日涨跌全量 + 主力资金个股明细（全量条形）。
        供盘前/盘后报告行业名下钻（ind=行业名&date=YYYYMMDD）。"""
        from urllib.parse import parse_qs, unquote as _uq
        import html as _h
        qp = parse_qs(qs)
        ind = _uq((qp.get("ind") or [""])[0]).strip()[:24]
        date = _uq((qp.get("date") or [""])[0]).strip()
        body = [self._BT_CSS]
        sys.path.insert(0, os.path.join(STATION, "quant_backtest"))

        def _fail(msg):
            body.append("<h1>行业下钻</h1><div class='err'>%s</div>"
                        % _h.escape(msg))
            self._html("".join(body))

        try:
            import datamart as dm
            import premarket as pm
            sb = dm.read("stock_basic",
                         fields=["ts_code", "name", "industry"])
        except Exception as ex:
            _fail("数据仓不可用：%s" % str(ex)[:120])
            return
        if sb is None or sb.empty:
            _fail("stock_basic 为空")
            return
        sb = sb.dropna(subset=["industry"])
        sb = sb[sb["industry"].astype(str).str.len() > 0]
        if not ind:
            # 缺省：全行业索引（按成分家数降序，全部可点）
            grp = sb.groupby("industry")["ts_code"].count()
            links = "".join(
                "<a href='/bt/ind_drill?ind=%s' style='display:inline-block;"
                "margin:2px 6px'>%s<span class='dim'>(%d)</span></a>"
                % (_h.escape(k, quote=True), _h.escape(k), v)
                for k, v in grp.sort_values(ascending=False).items())
            body.append("<h1>行业下钻 · 全 %d 个行业</h1>"
                        "<div>点击行业查看成分股涨跌与主力资金明细："
                        "<div style='line-height:2'>%s</div></div>"
                        % (len(grp), links))
            self._html("".join(body))
            return
        grp = sb.groupby("industry")["ts_code"]
        if ind not in set(grp.groups):
            _fail("未找到行业「%s」（stock_basic.industry 口径）"
                  % _h.escape(ind))
            return
        codes = list(grp.get_group(ind))
        names = dict(zip(sb["ts_code"], sb["name"]))
        days = pm._closed_trade_days()
        d0 = date if len(date) == 8 and date.isdigit() else (
            days[-1] if days else "")
        if not d0:
            _fail("交易日历不可用")
            return
        try:
            df = dm.read("daily", start=d0, end=d0, codes=codes,
                         fields=["ts_code", "close", "pct_chg", "amount"])
            mf = dm.read("moneyflow", start=d0, end=d0, codes=codes,
                         fields=["ts_code", "net_mf_amount"])
        except Exception as ex:
            _fail("读取失败：%s" % str(ex)[:120])
            return
        if df is None or df.empty:
            _fail("%s 在 %s 无行情数据（日线未落仓？）"
                  % (_h.escape(ind), d0))
            return
        df = df.dropna(subset=["pct_chg"]).set_index("ts_code")
        mf_yi = {}
        if mf is not None and not mf.empty:
            mf_yi = {r["ts_code"]: float(r["net_mf_amount"]) / 1e4
                     for _, r in mf.iterrows()
                     if r.get("net_mf_amount") == r.get("net_mf_amount")}
        n_up = int((df["pct_chg"] > 0).sum())
        n_dn = int((df["pct_chg"] < 0).sum())
        n_fl = int((df["pct_chg"] == 0).sum())
        n_all = len(df)
        med = float(df["pct_chg"].median())
        total_mf = sum(mf_yi.values())
        nav = ("<div class='nav'><a href='/premarket'>← 盘前报告</a> · "
               "<a href='/afterhours'>盘后复盘</a> · "
               "<span>行业下钻</span></div>")
        # 涨跌分布比例条（涨红/平灰/跌绿）
        w = lambda k: k * 100.0 / max(n_all, 1)
        dist = ("<div style='display:flex;height:14px;border-radius:3px;"
                "overflow:hidden;margin:8px 0'>"
                "<span style='background:#f85149;width:%.1f%%'></span>"
                "<span style='background:#6e7681;width:%.1f%%'></span>"
                "<span style='background:#3fb950;width:%.1f%%'></span>"
                "</div><div><b class='x'>涨 %d</b> · 平 %d · "
                "<b style='color:#3fb950'>跌 %d</b> · 中位 "
                "<b style='color:%s'>%+.2f%%</b></div>"
                % (w(n_up), w(n_fl), w(n_dn), n_up, n_fl, n_dn,
                   "#f85149" if med > 0 else "#3fb950", med))
        # 成分股涨跌全量表（按涨幅降序，涨幅微条形）
        mxp = max(abs(float(v)) for v in df["pct_chg"]) or 1
        rows_px = "".join(
            "<tr><td><a href='/kg/stock/%s'>%s</a></td><td>%s</td>"
            "<td style='color:%s'><b>%+.2f%%</b></td>"
            "<td style='width:30%%'><span style='display:inline-block;"
            "height:8px;border-radius:2px;background:%s;width:%.0f%%'>"
            "</span></td><td class='dim'>%.1f 亿</td></tr>"
            % (c[:6], c[:6], _h.escape(names.get(c, "")),
               "#f85149" if p > 0 else ("#3fb950" if p < 0 else "#8b949e"),
               p, "#f85149" if p > 0 else "#3fb950",
               abs(p) * 100.0 / mxp,
               float(r["amount"]) / 1e5 if r.get("amount") == r.get(
                   "amount") else 0)
            for c, p, r in sorted(
                ((c, float(r["pct_chg"]), r)
                 for c, r in df.iterrows()), key=lambda x: -x[1]))
        # 主力资金个股明细（全量，正红负绿条形）
        mf_html = ""
        if mf_yi:
            mxa = max(abs(v) for v in mf_yi.values()) or 1
            mf_rows = "".join(
                "<tr><td><a href='/kg/stock/%s'>%s</a></td><td>%s</td>"
                "<td style='color:%s'><b>%+.2f 亿</b></td>"
                "<td style='width:30%%'><span style='display:inline-block;"
                "height:8px;border-radius:2px;background:%s;width:%.0f%%'>"
                "</span></td></tr>"
                % (c[:6], c[:6], _h.escape(names.get(c, "")),
                   "#f85149" if v > 0 else "#3fb950", v,
                   "#f85149" if v > 0 else "#3fb950",
                   abs(v) * 100.0 / mxa)
                for c, v in sorted(mf_yi.items(), key=lambda x: -x[1]))
            mf_html = (
                "<h2>主力资金 · 个股明细（%s）</h2>"
                "<div>行业合计 <b style='color:%s'>%+.2f 亿</b> · "
                "覆盖 %d/%d 只（moneyflow 净额，万元→亿）</div>"
                "<table><tr><th>代码</th><th>名称</th><th>净流入</th>"
                "<th>相对规模</th></tr>%s</table>"
                % (d0, "#f85149" if total_mf > 0 else "#3fb950",
                   total_mf, len(mf_yi), n_all, mf_rows))
        body.append(nav)
        body.append(
            "<h1>%s <span class='dim'>（%s · %d 只成分股）</span></h1>"
            % (_h.escape(ind), d0, n_all))
        body.append(dist)
        body.append(
            "<h2>成分股涨跌全景（按涨幅降序，全量）</h2>"
            "<table><tr><th>代码</th><th>名称</th><th>涨跌幅</th>"
            "<th>幅度</th><th>成交额</th></tr>%s</table>" % rows_px)
        body.append(mf_html)
        body.append(
            "<div class='dim'>口径：daily.pct_chg 收盘涨跌 · "
            "moneyflow.net_mf_amount 主力净额 · 与盘前/盘后报告同源 · "
            "涨红跌绿</div>")
        self._html("".join(body))


    def _bt_lhb_page(self, qs):
        """龙虎榜下钻页：当日上榜全量（原因/买卖额/席位净额）+ 近60日
        上榜频次与日期 + 板块扎堆（关联炒作识别）。供盘前/盘后报告
        榜列下钻（date=YYYYMMDD）。"""
        from urllib.parse import parse_qs, unquote as _uq, quote as _qe
        import html as _h
        qp = parse_qs(qs)
        date = _uq((qp.get("date") or [""])[0]).strip()
        body = [self._BT_CSS]
        sys.path.insert(0, os.path.join(STATION, "quant_backtest"))

        def _fail(msg):
            body.append("<h1>龙虎榜</h1><div class='err'>%s</div>"
                        % _h.escape(msg))
            self._html("".join(body))

        try:
            import datamart as dm
        except Exception as ex:
            _fail("数据仓不可用：%s" % str(ex)[:120])
            return
        # 读取窗口：锚定 date（缺省今天）前 130 自然日 ≈ 88 交易日，
        # 覆盖近60日频次窗；避免全量扫（首载 14s → 秒级）
        import datetime as _dt
        try:
            _base = _dt.datetime.strptime(date, "%Y%m%d")
        except ValueError:
            _base = _dt.datetime.now()
        _ws = (_base - _dt.timedelta(days=130)).strftime("%Y%m%d")
        try:
            tl = dm.read("ts_top_list", start=_ws,
                         fields=["ts_code", "trade_date", "name",
                                 "pct_change", "net_amount", "l_buy",
                                 "l_sell", "reason"])
        except Exception as ex:
            _fail("ts_top_list 读取失败：%s" % str(ex)[:120])
            return
        if tl is None or tl.empty:
            _fail("ts_top_list 无数据（TDX 龙虎榜源待同步）")
            return
        tl = tl.copy()
        tl["d8"] = tl["trade_date"].astype(str).str[:8]
        all_days = sorted(tl["d8"].unique())
        d0 = date if date in all_days else all_days[-1]
        nav = ("<div class='nav'><a href='/premarket'>← 盘前报告</a> · "
               "<a href='/afterhours'>盘后复盘</a> · "
               "<span>龙虎榜下钻</span></div>")
        # 近60日窗口（按有数据交易日）：频次 + 每股上榜日期
        win = all_days[-60:]
        w60 = tl[tl["d8"].isin(win)]
        freq = w60.groupby("ts_code")["d8"].apply(
            lambda s: sorted(set(s), reverse=True))
        # 当日明细：同股多 reason 去重合并
        sub = tl[tl["d8"] == d0]
        recs = {}
        for r in sub.to_dict("records"):
            c = r["ts_code"]
            g = recs.setdefault(c, {
                "code": c, "name": r.get("name") or "",
                "pct": (float(r["pct_change"])
                        if r.get("pct_change") == r.get("pct_change")
                        else None),
                "buy": 0.0, "sell": 0.0, "net": 0.0,
                "reasons": []})
            for k, kk in (("buy", "l_buy"), ("sell", "l_sell"),
                          ("net", "net_amount")):
                v = r.get(kk)
                if v is not None and v == v:
                    g[k] += float(v)
            rs = str(r.get("reason") or "").strip()
            if rs and rs not in g["reasons"]:
                g["reasons"].append(rs)
        # 机构/陆股通席位净额 + 当日活跃坐席榜（ts_top_inst，exalter 粒度）
        inst = {}
        ti = None
        try:
            ti = dm.read("ts_top_inst", start=d0, end=d0,
                         fields=["ts_code", "exalter", "buy", "sell",
                                 "net_buy"])
            if ti is not None and not ti.empty:
                for r in ti.to_dict("records"):
                    if "机构" in str(r.get("exalter") or "") or \
                       "股通" in str(r.get("exalter") or ""):
                        v = r.get("net_buy")
                        if v is not None and v == v:
                            k = r["ts_code"]
                            inst[k] = inst.get(k, 0.0) + float(v)
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "api_backtest.py", exc)
        # 板块（stock_basic.industry 静态映射 ~0.1s；bak_basic 按日读
        # 实测 8-9s 为本页首瓶颈，弃用；94% 覆盖与当日快照相当）
        ind = {}
        try:
            sb = dm.read("stock_basic",
                         fields=["ts_code", "industry"])
            if sb is not None and not sb.empty:
                ind = {r["ts_code"]: str(r.get("industry") or "").strip()
                       for r in sb.to_dict("records")
                       if r.get("industry")
                       and str(r.get("industry")).strip()}
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "api_backtest.py", exc)
        n = len(recs)
        tot_net = sum(v["net"] for v in recs.values()) / 1e8
        # 板块扎堆条形（关联炒作：同板块多票同日上榜）
        ind_n = {}
        for v in recs.values():
            i_ = ind.get(v["code"])
            if i_:
                ind_n[i_] = ind_n.get(i_, 0) + 1
        hot_html = ""
        if ind_n:
            mx = max(ind_n.values())
            hi = sorted(ind_n.items(), key=lambda x: -x[1])[:10]
            hot_html = (
                "<h2>板块扎堆（同日上榜关联）</h2><div class='dim'>"
                "同板块多票上榜 → 游资抱团方向</div>%s" % "".join(
                    "<div style='display:flex;align-items:center;"
                    "gap:6px;margin:2px 0'>"
                    "<span style='width:110px;overflow:hidden;text-overflow:"
                    "ellipsis;white-space:nowrap'>%s</span>"
                    "<span style='flex:1'><span style='display:inline-block;"
                    "height:12px;border-radius:2px;background:#f85149;"
                    "opacity:%.2f;width:%.0f%%'></span></span>"
                    "<span style='width:36px;text-align:right'>%d 票"
                    "</span></div>"
                    % (_h.escape(k), 0.5 + 0.5 * v / mx,
                       v * 100.0 / mx, v)
                    for k, v in hi if v >= 2))
        # 高频榜（近60日≥3次：游资反复做）
        freq_html = ""
        hf = [(c, len(ds)) for c, ds in freq.items() if len(ds) >= 3]
        if hf:
            hf.sort(key=lambda x: -x[1])
            nm = {r["ts_code"]: r.get("name") for _, r in
                  tl.dropna(subset=["name"]).drop_duplicates(
                      "ts_code").iterrows()} if "name" in tl.columns else {}
            freq_html = (
                "<h2>高频上榜（近 %d 日 ≥3 次，游资反复做）</h2>"
                "<table><tr><th>代码</th><th>名称</th><th>次数</th>"
                "<th>上榜日期（近5次）</th></tr>%s</table>"
                % (len(win), "".join(
                    "<tr><td><a href='/kg/stock/%s'>%s</a></td>"
                    "<td>%s</td><td><b class='buy'>%d</b></td>"
                    "<td class='dim'>%s</td></tr>"
                    % (c[:6], c[:6], _h.escape(str(nm.get(c, ""))), k,
                       "、".join(freq[c][:5]))
                    for c, k in hf[:12])))
        # 当日活跃坐席榜（ts_top_inst exalter 聚合，净额绝对值排序，
        # 坐席名可点击 → /bt/seat 坐席历史下钻）
        seat_html = ""
        if ti is not None and not ti.empty:
            ti2 = ti.copy()
            ti2["ex"] = ti2["exalter"].astype(str).str.strip()
            g = ti2.groupby("ex").agg(
                n=("ts_code", "nunique"), buy=("buy", "sum"),
                sell=("sell", "sum"), net=("net_buy", "sum"))
            g = g.reindex(g["net"].abs().sort_values(ascending=False).index)
            seat_html = (
                "<h2>当日活跃坐席（净额绝对值 Top 15，点击看席位上榜历史）"
                "</h2><table><tr><th>坐席</th><th>票数</th><th>买入(亿)</th>"
                "<th>卖出(亿)</th><th>净额(亿)</th></tr>%s</table>"
                % "".join(
                    "<tr><td style='max-width:330px;overflow:hidden;"
                    "text-overflow:ellipsis;white-space:nowrap'>"
                    "<a href='/bt/seat?ex=%s' title='%s'>%s</a></td>"
                    "<td><b class='buy'>%d</b></td><td class='dim'>%.2f</td>"
                    "<td class='dim'>%.2f</td>"
                    "<td style='color:%s'><b>%+.2f</b></td></tr>"
                    % (_qe(e_), _h.escape(e_), _h.escape(e_),
                       int(r["n"]), float(r["buy"]) / 1e8,
                       float(r["sell"]) / 1e8,
                       "#f85149" if r["net"] > 0 else "#3fb950",
                       float(r["net"]) / 1e8)
                    for e_, r in g.head(15).iterrows()))
        # 当日全量明细表（净额降序）
        rows = "".join(
            "<tr><td><a href='/kg/stock/%s'>%s</a></td><td>%s</td>"
            "<td style='color:%s'><b>%+.2f%%</b></td>"
            "<td class='dim'>%s</td><td class='dim'>%.2f</td>"
            "<td class='dim'>%.2f</td>"
            "<td style='color:%s'><b>%+.2f</b></td>"
            "<td style='color:%s'>%s</td>"
            "<td class='buy' title='%s'>%d 次</td>"
            "<td class='dim' title='%s'>%s</td></tr>"
            % (v["code"][:6], v["code"][:6], _h.escape(v["name"]),
               "#f85149" if (v["pct"] or 0) > 0 else
               ("#3fb950" if (v["pct"] or 0) < 0 else "#8b949e"),
               v["pct"] if v["pct"] is not None else 0.0,
               _h.escape(ind.get(v["code"]) or "—"),
               v["buy"] / 1e8, v["sell"] / 1e8,
               "#f85149" if v["net"] > 0 else "#3fb950",
               v["net"] / 1e8,
               "#f85149" if inst.get(v["code"], 0) > 0 else
               ("#3fb950" if inst.get(v["code"], 0) < 0 else "#8b949e"),
               ("%+.2f" % (inst[v["code"]] / 1e8))
               if v["code"] in inst else "—",
               _h.escape("；".join(v["reasons"])),
               len(freq.get(v["code"], [])),
               _h.escape("、".join(freq.get(v["code"], [])[:5])),
               "等 %d 次" % len(freq.get(v["code"], []))
               if len(freq.get(v["code"], [])) > 5 else
               ("、".join(freq.get(v["code"], [])[:5]) or "—"))
            for v in sorted(recs.values(), key=lambda x: -x["net"]))
        body.append(nav)
        body.append(
            "<h1>龙虎榜下钻 <span class='dim'>（%s）</span></h1>"
            "<div>上榜 <b class='buy'>%d 只</b> · 榜内净额合计 "
            "<b style='color:%s'>%+.2f 亿</b> · 机构/股通席位覆盖 "
            "%d 只</div>" % (d0, n,
                             "#f85149" if tot_net > 0 else "#3fb950",
                             tot_net, len(inst)))
        body.append(
            "<h2>当日上榜全量（按净额降序）</h2>"
            "<table><tr><th>代码</th><th>名称</th><th>涨幅</th>"
            "<th>板块</th><th>买入(亿)</th><th>卖出(亿)</th><th>净额(亿)"
            "</th><th>机构(亿)</th><th>近60日</th><th>上榜日期</th>"
            "</tr>%s</table>" % rows)
        body.append(seat_html)
        body.append(hot_html)
        body.append(freq_html)
        body.append(
            "<div class='dim'>口径：ts_top_list（TDX 龙虎榜，金额单位元，"
            "÷1e8 折亿）；机构/股通=ts_top_inst 席位名含「机构/股通」"
            "聚合；坐席=ts_top_inst exalter 席位明细（每票代表席位一行，"
            "非完整前五买卖席位）；近60日=有数据交易日滚动窗；同股多条"
            "上榜原因去重合并；涨红跌绿</div>")
        self._html("".join(body))


    def _bt_seat_page(self, qs):
        """坐席（营业部/机构席位）下钻页：该坐席近60日上榜明细
        （日期/股票/买卖净额）+ 常做票。供龙虎榜页坐席名下钻
        （ex=<urlencoded 席位名>）。"""
        from urllib.parse import parse_qs, quote as _q, \
            unquote as _uq
        import html as _h
        qp = parse_qs(qs)
        ex = _uq((qp.get("ex") or [""])[0]).strip()[:80]
        body = [self._BT_CSS]
        sys.path.insert(0, os.path.join(STATION, "quant_backtest"))

        def _fail(msg):
            body.append("<h1>坐席</h1><div class='err'>%s</div>"
                        % _h.escape(msg))
            self._html("".join(body))

        if not ex:
            # K-STOCK-123 席位画像榜（无参入口）：读日更管线
            # portal/data/seat_profiles.json（seat_profile_daily.py 产出）
            prof = _read_json(os.path.join(ROOT, "data",
                                           "seat_profiles.json")) or {}
            seats = prof.get("seats", [])
            if not seats:
                _fail("席位画像未生成（portal/data/seat_profiles.json"
                      " 缺失）——日更管线 seat_profile_daily.py 每交易日"
                      " 23:50 产出，或手动：python quant_backtest/"
                      "seat_profile_daily.py")
                return
            def _wr_cell(s):
                wr = s.get("wr_win_rate")
                if wr is None:
                    return "<span class='dim'>n=%d</span>" % s.get("wr_n", 0)
                col = "#3fb950" if wr >= 0.5 else "#f85149"
                ar = s.get("wr_avg_ret")
                return ("<b style='color:%s'>%.1f%%</b> <span class='dim'>"
                        "(n=%d%s)</span>"
                        % (col, wr * 100, s.get("wr_n", 0),
                           " · 均向 %+.2f%%" % (ar * 100)
                           if ar is not None else ""))
            trs = "".join(
                "<tr><td><a href='/bt/seat?ex=%s'>%s</a>%s</td>"
                "<td><b class='buy'>%d</b></td><td>%d</td>"
                "<td style='color:%s'>%+.1f</td><td>%s</td>"
                "<td>%s</td><td class='dim'>%s</td></tr>"
                % (_h.escape(_q(ex2 := s.get("seat", ""))),
                   _h.escape(ex2[:26]),
                   " <span style='font-size:11px;color:#d29922'>⚡%s"
                   "</span>" % _h.escape(s.get("youzi", ""))
                   if s.get("youzi") else "",
                   s.get("n_days", 0), s.get("n_stocks", 0),
                   "#f85149" if s.get("net_yi", 0) > 0 else "#3fb950",
                   s.get("net_yi", 0.0), _h.escape(s.get("style", "—")),
                   _wr_cell(s),
                   _h.escape("、".join(
                       (t.get("name") or t.get("code", ""))
                       for t in (s.get("top_stocks") or [])[:3])
                       or "—"))
                for s in seats)
            body.append(
                "<div class='nav'><a href='/bt/lhb'>← 龙虎榜</a> · "
                "<span>席位画像</span></div>"
                "<h1>🎩 游资席位画像榜</h1>"
                "<div class='dim'>basis %s · 近 %d 个有数据交易日 · "
                "%d 席位（按上榜日数降序 top） · 点席位名下钻明细</div>"
                % (_h.escape(str(prof.get("basis", "?"))),
                   prof.get("window_days", 0), prof.get("n_seats", 0)))
            body.append(
                "<h2>画像总榜</h2>"
                "<table><tr><th>席位</th><th>上榜日</th><th>票数</th>"
                "<th>净额(亿)</th><th>风格</th><th>方向胜率</th>"
                "<th>常做票</th></tr>%s</table>"
                "<div class='dim' style='margin-top:10px'>%s</div>"
                % (trs, _h.escape(prof.get("note", ""))))
            self._html("".join(body))
            return
        try:
            import datamart as dm
        except Exception as e_:
            _fail("数据仓不可用：%s" % str(e_)[:120])
            return
        # 近130自然日窗口（同 lhb 页，覆盖60交易日）
        import datetime as _dt
        _ws = (_dt.datetime.now() - _dt.timedelta(days=130))\
            .strftime("%Y%m%d")
        try:
            ti = dm.read("ts_top_inst", start=_ws,
                         fields=["ts_code", "trade_date", "exalter",
                                 "buy", "sell", "net_buy"])
        except Exception as e_:
            _fail("ts_top_inst 读取失败：%s" % str(e_)[:120])
            return
        if ti is None or ti.empty:
            _fail("ts_top_inst 无数据（TDX 席位源待同步）")
            return
        ti = ti.copy()
        ti["d8"] = ti["trade_date"].astype(str).str[:8]
        rows_ = ti[ti["exalter"].astype(str).str.strip() == ex]
        if rows_.empty:
            _fail("坐席「%s」无上榜记录" % ex)
            return
        # 近60日窗口（按该表有数据交易日）
        win = set(sorted(ti["d8"].unique())[-60:])
        rows_ = rows_[rows_["d8"].isin(win)]
        if rows_.empty:
            _fail("坐席「%s」近 %d 个有数据交易日无上榜记录"
                  % (ex, len(win)))
            return
        # 票名（ts_top_list.name，同窗口）
        nm = {}
        try:
            tl = dm.read("ts_top_list", start=_ws,
                         fields=["ts_code", "name"])
            if tl is not None and not tl.empty:
                nm = tl.dropna(subset=["name"]).drop_duplicates(
                    "ts_code").set_index("ts_code")["name"].to_dict()
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "api_backtest.py", exc)

        def _f(v):
            try:
                v = float(v)
                return v if v == v else 0.0
            except (TypeError, ValueError):
                return 0.0

        n_days = rows_["d8"].nunique()
        n_stk = rows_["ts_code"].nunique()
        tot_b = _f(rows_["buy"].sum()) / 1e8
        tot_s = _f(rows_["sell"].sum()) / 1e8
        tot_n = _f(rows_["net_buy"].sum()) / 1e8
        # 明细（日期降序，同日净额降序）
        rrs = sorted(rows_.to_dict("records"),
                     key=lambda r: (r["d8"], _f(r.get("net_buy"))),
                     reverse=True)
        det = "".join(
            "<tr><td class='dim'>%s</td>"
            "<td><a href='/kg/stock/%s'>%s</a></td><td>%s</td>"
            "<td class='dim'>%.2f</td><td class='dim'>%.2f</td>"
            "<td style='color:%s'><b>%+.2f</b></td></tr>"
            % (r["d8"], str(r["ts_code"])[:6], str(r["ts_code"])[:6],
               _h.escape(str(nm.get(r["ts_code"], "")) or "—"),
               _f(r.get("buy")) / 1e8, _f(r.get("sell")) / 1e8,
               "#f85149" if _f(r.get("net_buy")) > 0 else "#3fb950",
               _f(r.get("net_buy")) / 1e8)
            for r in rrs)
        # 常做票（≥2 日上榜：坐席常客/票席关联）
        cnt = rows_.groupby("ts_code")["d8"].nunique()
        reg = [(c, int(k),
                _f(rows_[rows_["ts_code"] == c]["net_buy"].sum()) / 1e8)
               for c, k in cnt.items() if k >= 2]
        reg.sort(key=lambda x: -x[2])
        reg_html = ""
        if reg:
            reg_html = (
                "<h2>常做票（近 %d 日 ≥2 次上榜，席位常客/关联）</h2>"
                "<table><tr><th>代码</th><th>名称</th><th>次数</th>"
                "<th>累计净额(亿)</th></tr>%s</table>"
                % (len(win), "".join(
                    "<tr><td><a href='/kg/stock/%s'>%s</a></td>"
                    "<td>%s</td><td><b class='buy'>%d</b></td>"
                    "<td style='color:%s'>%+.2f</td></tr>"
                    % (c[:6], c[:6],
                       _h.escape(str(nm.get(c, "")) or "—"), k,
                       "#f85149" if v > 0 else "#3fb950", v)
                    for c, k, v in reg[:12])))
        body.append(
            "<div class='nav'><a href='/bt/lhb'>← 龙虎榜</a> · "
            "<a href='/bt/seat'>席位画像榜</a> · "
            "<span>坐席下钻</span></div>"
            "<h1>坐席 <span class='dim'>(%s)</span></h1>"
            "<div>近 %d 个有数据交易日 · 上榜 <b class='buy'>%d 日</b> / "
            "<b class='buy'>%d 票</b> · 合计买 <b>%.2f 亿</b> / 卖 "
            "<b>%.2f 亿</b> · 净额 <b style='color:%s'>%+.2f 亿</b></div>"
            % (_h.escape(ex), len(win), n_days, n_stk, tot_b, tot_s,
               "#f85149" if tot_n > 0 else "#3fb950", tot_n))
        # K-STOCK-123 画像摘要行（命中日更画像则显示风格/胜率徽标）
        _prof = _read_json(os.path.join(ROOT, "data",
                                        "seat_profiles.json")) or {}
        _me = next((s for s in _prof.get("seats", [])
                    if s.get("seat") == ex), None)
        if _me:
            _wr = _me.get("wr_win_rate")
            _wr_txt = ("—" if _wr is None else
                       "<b style='color:%s'>%.1f%%</b> (n=%d)"
                       % ("#3fb950" if _wr >= 0.5 else "#f85149",
                          _wr * 100, _me.get("wr_n", 0)))
            body.append(
                "<div class='dim' style='margin-top:6px'>🎩 画像（%s "
                "· 近%d日榜）：%s风格 <b>%s</b> · 方向胜率 %s</div>"
                % (_h.escape(str(_prof.get("basis", "?"))),
                   _prof.get("window_days", 0),
                   "⚡<b>%s</b> · " % _h.escape(_me.get("youzi", ""))
                   if _me.get("youzi") else "",
                   _h.escape(_me.get("style", "—")), _wr_txt))
        body.append(
            "<h2>上榜明细（日期降序）</h2>"
            "<table><tr><th>日期</th><th>代码</th><th>名称</th>"
            "<th>买入(亿)</th><th>卖出(亿)</th><th>净额(亿)</th></tr>%s"
            "</table>" % det)
        body.append(reg_html)
        body.append(
            "<div class='dim'>口径：ts_top_inst 席位明细（每票取代表席位"
            "一行，非完整前五买卖席位）；金额单位元 ÷1e8 折亿；窗口=近 "
            "%d 个有数据交易日；涨红跌绿</div>" % len(win))
        self._html("".join(body))


    def _bt_stock_page(self, qs):
        """个股全景页：行情 + 4策略信号 + 共振分解 + JEV下钻 + AI展望。"""
        from urllib.parse import parse_qs, unquote as _uq
        import html as _h
        qp = parse_qs(qs)
        q = _uq((qp.get("c") or qp.get("q") or [""])[0]).strip()[:24]
        body = [self._BT_CSS]
        if not q:
            body.append(self._bt_stock_form())
            self._html("".join(body))
            return
        e = _bt_resolve(q)
        if not e:
            body.append(self._bt_stock_form(
                "未识别「%s」。支持：中文名 / 英文代码 / 6位A股 / 5位港股"
                "（如 英伟达 · TSLA · 600519 · 00700）" % _h.escape(q)))
            self._html("".join(body))
            return
        name, code = e["name"], e.get("code", "")
        aliases = e.get("aliases") or []
        mkt_key = ("crypto" if "/" in code else "us" if code.startswith("gb_")
                   else "hk" if code.startswith("rt_hk") else "cn")
        mkt_label = {"cn": "A股", "us": "美股", "hk": "港股",
                     "crypto": "加密"}[mkt_key]
        # 市场子导航
        nav = ["<div class='nav'><a href='/bt'>🎯 选股中心</a>"]
        for k, lb in (("cn", "🇨🇳 A股"), ("us", "🇺🇸 美股"),
                      ("hk", "🇭🇰 港股"), ("crypto", "🪙 加密")):
            nav.append(("<span>%s</span>" % lb) if k == mkt_key
                       else "<a href='/bt/m/%s'>%s</a>" % (k, lb))
        body.append("".join(nav) + "</div>")
        body.append(self._back_bar_html())
        body.append("<h1>%s <span class='dim'>(%s · %s)</span></h1>"
                    % (_h.escape(name), _h.escape(code), mkt_label))
        # 🎯 综合研判 + 💼 操作建议（个股缠论日线结构 × 大盘环境，
        # 四市场通用——结构引擎市场无关；数据不足不渲染）
        try:
            import sys as _s9
            if r'C:\项目\老田的AI助手\quant_backtest' not in _s9.path:
                _s9.path.insert(0, r'C:\项目\老田的AI助手\quant_backtest')
            import chanlun_market as _CM9
            _sv = _CM9.stock_view(code)
            _lv9 = _CM9.market_brief()
            _vd9 = _CM9.verdict(_lv9)
        except Exception:
            _sv = None
        if _sv and _sv.get("ok"):
            _trc9 = {"上升": "#f85149", "下降": "#3fb950"}.get(
                _sv.get("trend"), "#d29922")
            _ph9 = []
            try:
                _pmap9 = {"cn": "chanlun_pool.jsonl",
                          "us": "chanlun_pool_us.jsonl",
                          "hk": "chanlun_pool_hk.jsonl"}
                _pf9 = _pmap9.get(mkt_key)
                if _pf9:
                    import re as _r9
                    _pdir9 = (r"C:\项目\老田的AI助手\quant_backtest\data")
                    _prows9 = [json.loads(_l) for _l in open(
                        os.path.join(_pdir9, _pf9), encoding="utf-8")
                        if _l.strip()]
                    _cands9 = {code, code.replace("gb_", ""),
                               code.replace("rt_hk", "")}
                    _m9 = _r9.search(r"(\d{6})", code)
                    if _m9:
                        _b69 = _m9.group(1)
                        _cands9 |= {_b69, "%s.SH" % _b69, "%s.SZ" % _b69,
                                    "%s.BJ" % _b69}
                    _ph9 = [r for r in _prows9
                            if r.get("code") in _cands9][-2:]
            except Exception:
                _ph9 = []
            _pool9 = ""
            if _ph9:
                _pool9 = " ｜ 池内信号：" + "、".join(
                    "<b>%s</b>(%s)" % (r.get("type"),
                                       (str(r.get("dt")) or "")[5:])
                    for r in _ph9)
            elif _sv.get("sig_txt"):
                _pool9 = " ｜ 最近信号 <b>%s</b>" % _sv["sig_txt"]
            _zg9, _zd9 = _sv.get("zg"), _sv.get("zd")
            _zone9 = ""
            if _zg9 and _zd9:
                _zone9 = " · 日线中枢 [%.2f, %.2f]" % (_zd9, _zg9)
            body.append(
                "<div class='sec'><h2>🎯 综合研判"
                "<span class='dim' style='font-size:12px;margin-left:8px'>"
                "个股缠论日线结构 × 大盘环境 · 引擎实时</span></h2>"
                "<div style='line-height:1.9'>"
                "走势：<b style='color:%s'>%s</b> · 现价 <b>%.2f</b> %s%s%s"
                "<br><span class='dim'>观察：%s ｜ 大盘环境：%s</span>"
                "%s</div></div>"
                % (_trc9, _sv.get("trend") or "—", _sv.get("px") or 0,
                   _sv.get("pos") or "—", _zone9, _pool9,
                   _sv.get("tip") or "—", _vd9[0],
                   (("<details style='margin-top:8px'><summary style="
                     "'cursor:pointer'>📈 缠论结构图（15:35 盘后生成）"
                     "</summary><img src='/chanlun-png?c=%s' "
                     "loading='lazy' style='max-width:100%%' "
                     "onerror=\"this.parentElement.style.display='none'\">"
                     "</details>") % code) if mkt_key == "cn" else ""))
            # 💼 操作建议：个股关键位观察 + 大盘仓位档（结构化派生）
            try:
                _tp9 = _CM9.trade_plan(_lv9)
                _dyn9 = _CM9.dynamic_pos(_lv9, write=False)
            except Exception:
                _tp9, _dyn9 = None, None
            if _tp9:
                _zd9v = _zd9 if isinstance(_zd9, float) else None
                if _sv.get("pos") == "中枢下方" and _zd9v:
                    _buy9 = "收复 <b>%.2f</b>（中枢下沿=重回中枢）再看上沿" % _zd9v
                    _sell9 = "反抽不回 <b>%.2f</b>=三卖，离场观望" % _zd9v
                elif _zd9v:
                    _buy9 = "回踩不破 <b>%.2f</b>（中枢上沿=三买观察）分批参与" % _zg9
                    _sell9 = "失守 <b>%.2f</b>（中枢下沿）=结构走坏离场" % _zd9v
                else:
                    _buy9, _sell9 = _sv.get("tip") or "—", "—"
                # 仓位管理三行：分批阶梯/调仓触发（大盘 POS_MGMT 同源）
                # + 池内信号联动提示（个股级增量维度）
                _mg9 = _tp9.get("mgmt") or {}
                _sig9 = ""
                if _ph9:
                    _lt9 = _ph9[-1].get("type") or ""
                    _ld9 = (str(_ph9[-1].get("dt")) or "")[5:]
                    if "卖" in _lt9:
                        _sig9 = ("<br>⚠️ 池内信号联动：<b>%s</b>(%s) 在身——"
                                 "加仓需等反向信号确认，反抽注意减"
                                 % (_lt9, _ld9))
                    elif "买" in _lt9:
                        _sig9 = ("<br>✅ 池内信号联动：<b>%s</b>(%s)——"
                                 "回踩关键位可按分批节奏参与" % (_lt9, _ld9))
                body.append(
                    "<div class='sec'><h2>💼 操作建议"
                    "<span class='dim' style='font-size:12px;margin-left:8px'>"
                    "研究视角 · 非投资建议</span></h2>"
                    "<div style='line-height:1.9'>"
                    "🎯 买点观察：%s<br>"
                    "🛑 卖点防守：%s<br>"
                    "📊 仓位参考：大盘当前 <b>%s</b> ｜ 单票上限 %s"
                    "<br>📈 加仓节奏：%s"
                    "<br>🔄 调仓触发：%s%s"
                    "</div></div>"
                    % (_buy9, _sell9,
                       (_dyn9 or {}).get("name") or "—",
                       _mg9.get("cap") or "—",
                       _mg9.get("steps") or "—",
                       _mg9.get("rebalance") or "—", _sig9))
        # K235 结论置顶 → K239 扩容为「操作面板」：目标价体系 +
        # 操作方案（贴线做T/破线走人）+ 平均筹码 + 所属板块联动
        if mkt_key == "cn":
            try:
                _stp = self._chanlun_sl_tp(code)
            except Exception:
                _stp = None
            if _stp:
                _px = _stp.get("px")

                def _pv(base):
                    try:
                        v = (float(base) / float(_px) - 1) * 100
                        return "<span class='dim'>%+.1f%%</span>" % v
                    except Exception:
                        return ""

                # 📋 操作方案：多级别中枢参数 → 贴线做T/破线走人提示
                _zd, _zg = _stp.get("dzd"), _stp.get("dzg")
                if _px and _zg and _px > _zg:
                    _plan = ("日线3买强结构：回踩 <b>%.2f</b> 不破可低吸"
                             "做 T；<b style='color:#f85149'>跌破 %.2f"
                             "（中枢上沿=结构失效）坚决走</b>"
                             % (_zg, _stp["stop"]))
                elif _zd and _zg:
                    _plan = ("日线中枢 %.2f~%.2f：贴 <b>%.2f</b> 下沿"
                             "低吸、<b>%.2f</b> 上沿减仓做 T；<b style="
                             "'color:#f85149'>跌破 %.2f（最近笔低点）"
                             "坚决走</b>"
                             % (_zd, _zg, _zd, _zg, _stp["stop"]))
                else:
                    _plan = ("以 <b style='color:#f85149'>%.2f</b> 为"
                             "纪律线，跌破坚决走" % _stp["stop"])
                # 💰 平均筹码（本地 cyq_chips 最新日，不在线拉不阻塞）
                _chip = _stock_chip_summary(code, _px)
                _chip_html = ""
                if _chip and _chip.get("avg"):
                    _pos_s = ""
                    if _chip.get("pos") is not None:
                        _pc = "#f85149" if _chip["pos"] >= 0 else "#3fb950"
                        _pos_s = ("，现价在均成本<b style='color:%s'>%s%%"
                                  "</b>" % (_pc, "%+.1f" % _chip["pos"]))
                    _win_s = ""
                    if _chip.get("winner") is not None:
                        _win_s = " · 获利盘 <b>%s%%</b>" % ("%.0f"
                                                            % _chip["winner"])
                    _chip_html = ("　💰 <b>平均筹码</b> %.2f%s%s<span "
                                  "class='dim' style='font-size:11.5px'>"
                                  "（%s 筹码口径）</span>"
                                  % (_chip["avg"], _pos_s, _win_s,
                                     _chip["d"]))
                # 🏛 所属板块联动（tdx 板块指数当日/5日涨跌 + RPS）
                _sec_html = ""
                try:
                    _secs = _stock_sector_rows(code)
                except Exception:
                    _secs = []
                if _secs:
                    _parts = []
                    for _r in _secs:
                        _c1 = "—" if _r["chg1"] is None \
                            else "%+.1f%%" % _r["chg1"]
                        _cc = "#f85149" if (_r["chg1"] or 0) >= 0 \
                            else "#3fb950"
                        _c5 = "—" if _r["chg5"] is None \
                            else "%+.1f%%" % _r["chg5"]
                        _rp = "—" if _r["rps50"] is None \
                            else "%d" % round(_r["rps50"])
                        _parts.append(
                            "%s <b style='color:%s'>%s</b>"
                            "<span class='dim' style='font-size:11px'>"
                            "（5日 %s · RPS %s）</span>"
                            % (_h.escape(str(_r["sector"])), _cc, _c1,
                               _c5, _rp))
                    _sec_html = ("　🏛 <b>所属板块</b> " + " ｜ ".join(
                        _parts))
                body.append(
                    "<div class='card' style='border-color:#58a6ff;"
                    "line-height:2.0'>"
                    "🎯 <b>结论速览</b>　🛑 止损 <b style='color:#f85149'>"
                    "%.2f</b> %s<span class='dim'>（%s）</span> ｜ 🎯 "
                    "目标1 <b style='color:#3fb950'>%.2f</b> %s ｜ 目标2 "
                    "<b style='color:#3fb950'>%.2f</b> %s<span class='dim'>"
                    "（%s）</span>"
                    "<br>📋 <b>操作方案</b> %s"
                    "%s%s　<span class='dim'>四维共振结论见③</span>"
                    "<div style='margin-top:6px'><a class='kpb' href="
                    "'/bt/analyst-report?c=%s' style='text-decoration:"
                    "none;display:inline-block'>🌊 青萍之末分析师报告"
                    "（图文·可一键发飞书）</a></div></div>"
                    % (_stp["stop"], _pv(_stp["stop"]),
                       _h.escape(str(_stp.get("basis") or "")),
                       _stp["tp1"], _pv(_stp["tp1"]),
                       _stp["tp2"], _pv(_stp["tp2"]),
                       _h.escape(str(_stp.get("tpb") or "")),
                       _plan, _chip_html, _sec_html,
                       _h.escape(str(code))))
        # K77: 缠论结构图跟随个股（A股专属：引擎只跑全A）
        if mkt_key == "cn":
            body.append(self._chanlun_stock_sec(code))

        # ── 数据收集（提前，供方法论矩阵 + 详情区块共用）──────────
        e = _h.escape

        # 行情
        qt = None
        try:
            sys.path.insert(0, os.path.join(STATION, "news_collector"))
            import stock_analyze
            qt = stock_analyze.fetch_quote(code)
        except Exception:
            qt = None

        # 共振快照（rr = 本票在自选池的共振结果）
        snap, rr = {}, None
        try:
            snap = json.load(open(os.path.join(
                STATION, "quant_backtest", "reports", "rps_latest.json"),
                encoding="utf-8"))
            rr = next((x for x in snap.get("res", [])
                       if x.get("code") == code or x.get("name") == name),
                      None)
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "api_backtest.py", exc)

        # 4 策略信号状态（实时算；供矩阵计数 + 详情表）
        SIG4_LIST = (("trend_follow", "趋势跟随"),
                     ("ma_cross", "均线交叉"),
                     ("rsi_reversal", "RSI反转"),
                     ("boll_breakout", "布林突破"))
        sig_states = {}   # sn -> "多头"|"空头"|"空仓"|"—"
        df = None         # K235: 预置 None（import 失败时行情卡量比安全降级）
        sig_err = ""
        try:
            from datetime import datetime as _dt, timedelta as _td
            sys.path.insert(0, os.path.join(STATION, "quant_backtest"))
            import runner as _R
            import data_loader as _D
            import strategies as _S
            start = (_dt.now() - _td(days=130)).strftime("%Y-%m-%d")
            df = None
            for _mk, _sy, _nm in _R.resolve_watchlist(
                    [{"name": name, "code": code, "aliases": aliases}]):
                df = _D.load_bars(_mk, [_sy], start, None).get(_sy)
                if df is not None:
                    break
            if df is None or len(df) < 55:
                sig_err = "无足够历史数据"
            else:
                for sn, _cn in SIG4_LIST:
                    try:
                        sv = int(_S.REGISTRY[sn](
                            df, dict(_R.CFG["strategies"].get(sn, {}))
                            ).values[-1])
                        sig_states[sn] = ("多头" if sv > 0 else
                                          "空头" if sv < 0 else "空仓")
                    except Exception:
                        sig_states[sn] = "—"
        except Exception as ex:
            import traceback
            try:
                with open(r"C:\Temp\bt_stock_err.log", "a",
                          encoding="utf-8") as f:
                    f.write(time.strftime("%m-%d %H:%M:%S ") + q + "\n")
                    traceback.print_exc(file=f)
            except OSError as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "api_backtest.py", exc)
            sig_err = "技术引擎暂不可用（%s）" % e(str(ex)[:60])

        # 多策略共振（cross_poll 注册表驱动聚合：各策略对该票的命中、
        # 方向、工作台 URL 全部来自注册表——新增策略改 cross_poll 即可，
        # 本页矩阵自动跟随，零改这里）
        cross_data, cross_hit = {"strategies": []}, None
        try:
            sys.path.insert(0, os.path.join(STATION, "quant_backtest"))
            import cross_poll as _CP
            cross_data = _CP.build_cross() or {"strategies": []}
            nc = _CP.norm_code(code)
            cross_hit = next((st for st in cross_data.get("stocks", [])
                              if st.get("code") == nc), None)
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "api_backtest.py", exc)

        # JEV 关联新闻
        items = _bt_jev_news(name, aliases)

        # 图谱面数据（S2 时间轴 + S3 图谱面共用，A股一次查询；
        # 美股/港股/加密无图谱，st_kg=None → 各模块显示不适用说明）
        st_kg, _er = None, {"n": 0, "bias_stat": {}, "events": []}
        _kg_ts = ""
        import re as _re_kg
        _m6 = _re_kg.search(r"(\d{6})", code or "")
        if _m6:
            _d6 = _m6.group(1)
            _kg_ts = (_d6 + ".SH" if _d6[0] == "6" else _d6 + ".SZ"
                      if _d6[0] in "03" else _d6 + ".BJ"
                      if _d6[0] in "48" else "")
        if _kg_ts:
            try:
                if QBT_DIR not in sys.path:
                    sys.path.insert(0, QBT_DIR)
                import kg_portal as _KP
                _r = _KP.stock(_kg_ts, days=90)
                if _r and _r.get("ok", True):
                    st_kg = _r
            except Exception:
                st_kg = None
            try:
                from event_graph import events_for_stock as _egf
                _er = _egf(_kg_ts, days=30)
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "api_backtest.py", exc)

        # ── 渲染 ────────────────────────────────────────────
        # ① 行情（新浪快照；K235 补全基础行情：昨收/开盘/高低/涨跌幅/
        # 成交量/成交额 + A股换手率(daily_basic 1日缓存)/量比(5日均量)）
        if qt and qt.get("price"):
            pct = qt.get("pct")
            pc = ("buy" if (pct or 0) > 0 else "avoid"
                  if (pct or 0) < 0 else "")
            prev_c = None
            if pct is not None and qt["price"]:
                try:
                    prev_c = qt["price"] / (1 + pct / 100.0)
                except (TypeError, ZeroDivisionError):
                    prev_c = None
            # A股换手率：daily_basic 估值缓存（1日）
            tr_val = None
            if mkt_key == "cn":
                try:
                    import re as _re6
                    _m6q = _re6.search(r"(\d{6})", code or "")
                    if _m6q:
                        _tsc = (_m6q.group(1) + ".SH"
                                if _m6q.group(1)[0] == "6" else
                                _m6q.group(1) + ".SZ"
                                if _m6q.group(1)[0] in "03" else "")
                        if _tsc:
                            _val = self._stock_valuation(_tsc)
                            tr_val = (_val or {}).get("turnover_rate")
                except Exception:
                    tr_val = None
            # 量比：今日成交量 / 前5日均量（同源 df，单 defend）
            lb = None
            try:
                if (df is not None and len(df) > 6 and qt.get("vol")
                        and "volume" in df.columns):
                    v5 = df["volume"].iloc[-6:-1].mean()
                    if v5 and v5 > 0:
                        lb = qt["vol"] / v5
            except Exception:
                lb = None

            def _fv(x, nd=2):
                return ("%.*f" % (nd, x)) if isinstance(
                    x, (int, float)) and x == x else "—"
            _vol = qt.get("vol")
            _vol_s = (("%.0f万手" % (_vol / 1e6)) if _vol and _vol >= 1e8
                      else (("%.0f手" % (_vol / 100)) if _vol else "—"))
            _amt = qt.get("amount")
            _amt_s = (("%.2f亿" % (_amt / 1e8)) if _amt and _amt >= 1e8
                      else (("%.0f万" % (_amt / 1e4)) if _amt else "—"))

            def _ic(lb_, v, cls_=""):
                return ("<div style='display:inline-block;margin:4px 22px "
                        "6px 0'><div class='dim' style='font-size:11px'>%s"
                        "</div><b style='font-size:15px' class='%s'>%s</b>"
                        "</div>" % (lb_, cls_, v))
            body.append(
                "<h2>① 行情</h2><div class='card'>"
                "现价 <b style='font-size:22px'>%s</b>%s"
                "<div style='margin-top:4px'>%s%s%s%s%s%s%s%s</div>"
                "<div class='dim'>%s</div></div>"
                % (qt["price"],
                   (" · <b class='%s' style='font-size:16px'>%+.2f%%</b>"
                    % (pc, pct)) if pct is not None else "",
                   _ic("昨收", _fv(prev_c)), _ic("开盘", _fv(qt.get("open"))),
                   _ic("最高", _fv(qt.get("high"))),
                   _ic("最低", _fv(qt.get("low"))),
                   _ic("成交量", _vol_s), _ic("成交额", _amt_s),
                   _ic("换手率", _fv(tr_val) + ("%" if tr_val else ""),
                       "buy" if (tr_val or 0) >= 5 else ""),
                   _ic("量比", _fv(lb),
                       "buy" if (lb or 0) >= 2 else
                       "avoid" if (lb or 0) > 0 and lb <= 0.5 else ""),
                   _h.escape(str(qt.get("ts") or ""))))
        else:
            body.append("<h2>① 行情</h2><div class='card dim'>暂无实时"
                        "行情（加密或行情源无此代码）</div>")
        # ①+ K 线图（S1）：四市场日线 + MA5/20/60/250 + 成交量副图 +
        # A股缠论结构信号点 + JEV 新闻色点 + 筹码瀑布（S5，A股专属，
        # 右侧联动可见区最后一日）；周期切换 60/120/250（前端切片零
        # 重取）。异步 /api/kline 取数不阻塞首屏
        body.append(
            "<style>.kpb{background:#161b22;border:1px solid #30363d;"
            "border-radius:8px;color:#8b949e;font-size:12px;padding:4px 12px;"
            "cursor:pointer;font-family:inherit;margin-right:4px}"
            ".kpb.on{background:#0f141c;color:#e6edf3;font-weight:600;"
            "border-color:#58a6ff}</style>"
            "<h2>①+ K 线 <span class='dim' style='font-weight:400;"
            "font-size:12px'>（日/周/月线 · MA5/20/60/250 · MACD 副图"
            " · 成交量 · 筹码瀑布（A股·右侧） · 缠论结构信号点 + JEV"
            " 新闻色点 · K157 全A实证：s1卖后20/60日均价涨5.5%%/9.9%%"
            " 勿独立减仓）</span></h2>"
            "<details style='margin:-4px 0 6px'><summary class='dim' "
            "style='cursor:pointer;font-size:12px;user-select:none'>"
            "📐 图例与信号判据全量说明（点开）</summary><div class='dim'"
            " style='font-size:12px;line-height:1.8;margin:4px 0 0'>"
            "MA5/20/60/250 随级别重算 · MACD(12,26,9) 副图随级别重算，"
            "副图圆点=金叉(绿)/死叉(红) · 灰折线=笔（悬浮显向/幅）"
            " · 金粗线=线段（悬浮显向/幅/笔数） · 黄带=笔中枢 ZD-ZG"
            "（随级别：日/周/月/季/年线，色深=段数，悬浮显值）· 红倒三角"
            "=顶分型 · 绿三角=底分型（分型+笔联动：仅显示成笔分型=笔端点，"
            "白描边高亮，未成笔分型不再淡化显示）· 菱形标字=三类买卖点"
            "（简化判据：1=背驰〔幅衰减或MACD面积萎缩，趋=趋势背驰/盘="
            "盘整背驰〕 · 2=回抽不新极 · 3=沿外回踩；悬停信号点显「形态"
            "共振」=该信号日同现的经典K线形态（P2 15形态自研引擎，锤头/"
            "吞没/启明星等）；b1盘整背驰双窗微正+0.5%%、趋势背驰更弱）"
            "</div></details>"
            "<div class='card' style='padding:10px'>"
            "<div style='margin-bottom:8px'>"
            "<button class='kpb' data-n='60'>60</button>"
            "<button class='kpb' data-n='120'>120</button>"
            "<button class='kpb on' data-n='250'>250</button>"
            "<span class='dim' style='margin:0 4px'>根</span>"
            "<button class='kpb on' data-m='d'>日线</button>"
            "<button class='kpb' data-m='w'>周线</button>"
            "<button class='kpb' data-m='m'>月线</button>"
            "<button class='kpb' data-m='q'>季线</button>"
            "<button class='kpb' data-m='y'>年线</button>"
            "<span class='dim' style='margin-left:8px' id='kmsg'>"
            "K线加载中…</span></div>"
            "<div style='display:flex;gap:6px;align-items:stretch'>"
            "<div id='kx' style='height:432px;flex:1 1 auto;min-width:0'>"
            "</div><div id='kxc' style='height:432px;width:210px;"
            "flex:0 0 210px;display:none'></div></div></div>"
            "<script src='/static/echarts.min.js'></script><script>"
            "var _kc=%s,_kb=null,_ke=null,_kds=[],_kmk={},_knw={},"
            "_kpt={},"
            "_kcc=null,_kcm={},_kcd='',_kmode='d',_kwb=null,_kmb=null,"
            "_kqs=null,_kys=null,_kmsgbase='',"
            "_kn=250;"
            "function _wkkey(ds){var t=new Date(ds+'T00:00:00');"
            "var k=new Date(t);k.setDate(t.getDate()-((t.getDay()+6)%%7));"
            "return k.getFullYear()+'-'+('0'+(k.getMonth()+1)).slice(-2)"
            "+'-'+('0'+k.getDate()).slice(-2)}"
            "function _wk(){if(_kwb)return _kwb;var bs=_kb.bars,g={},ks=[],i;"
            "for(i=0;i<bs.length;i++){var r=bs[i],k=_wkkey(r.d);"
            "if(!g[k]){g[k]={d:r.d,w0:r.d,o:r.o,h:r.h,l:r.l,c:r.c,v:r.v};"
            "ks.push(k)}"
            "else{var w=g[k];w.d=r.d;w.c=r.c;w.v+=r.v;"
            "if(r.h>w.h)w.h=r.h;if(r.l<w.l)w.l=r.l}}"
            "ks.sort();"
            "var wb=ks.map(function(k){return g[k]});"
            "var cl=wb.map(function(r){return r.c});"
            "[5,20,60,250].forEach(function(K){var s=0;"
            "for(i=0;i<wb.length;i++){s+=cl[i];if(i>=K)s-=cl[i-K];"
            "wb[i]['m'+K]=i>=K-1?Math.round(s/K*1000)/1000:null}});"
            "_kwb={bars:wb,marks:_kb.marks||[],news:_kb.news||{},"
            "bis:_kb.wbis||[],pivots:_kb.wpivots||[],"
            "fx:_kb.wfx||[],sgs:_kb.wsgs||[],"
            "sigs:_kb.wsigs||[]};"
            "return _kwb}"
            "function _mokey(ds){return ds.slice(0,7)+'-01'}"
            "function _mo(){if(_kmb)return _kmb;var bs=_kb.bars,g={},"
            "ks=[],i;"
            "for(i=0;i<bs.length;i++){var r=bs[i],k=_mokey(r.d);"
            "if(!g[k]){g[k]={d:r.d,w0:r.d,o:r.o,h:r.h,l:r.l,c:r.c,v:r.v};"
            "ks.push(k)}"
            "else{var w=g[k];w.d=r.d;w.c=r.c;w.v+=r.v;"
            "if(r.h>w.h)w.h=r.h;if(r.l<w.l)w.l=r.l}}"
            "ks.sort();"
            "var mb=ks.map(function(k){return g[k]});"
            "var cl=mb.map(function(r){return r.c});"
            "[5,20,60,250].forEach(function(K){var s=0;"
            "for(i=0;i<mb.length;i++){s+=cl[i];if(i>=K)s-=cl[i-K];"
            "mb[i]['m'+K]=i>=K-1?Math.round(s/K*1000)/1000:null}});"
            "_kmb={bars:mb,marks:_kb.marks||[],news:_kb.news||{},"
            "bis:_kb.mbis||[],pivots:_kb.mpivots||[],"
            "fx:_kb.mfx||[],sgs:_kb.msgs||[],"
            "sigs:_kb.msigs||[]};"
            "return _kmb}"
            "function _kfmt(p){var r=_kds[p.dataIndex];if(!r)return '';"
            "return (_kmode!=='d'&&r.w0?(r.w0+' ~ '+r.d):r.d)+'<br>开 '+r.o+' 收 '+r.c+' 高 '+r.h+' 低 '+r.l"
            "+'<br>量 '+(r.v>=1e8?(r.v/1e8).toFixed(2)+'亿':"
            "(r.v/1e4).toFixed(0)+'万')+'<br>MA5 '+r.m5+' · MA20 '+r.m20"
            "+' · MA60 '+r.m60+' · MA250 '+(r.m250==null?'—':r.m250)"
            "+'<br>DIF '+(r.dif!=null?r.dif:'—')+' · DEA '"
            "+(r.dea!=null?r.dea:'—')+' · 柱 '+(r.mac!=null?r.mac:'—')}"
            "function _macd(bs){var e12=null,e26=null,dea=null,"
            "out=[],i;for(i=0;i<bs.length;i++){var c=bs[i].c;"
            "e12=e12==null?c:e12*11/13+c*2/13;"
            "e26=e26==null?c:e26*25/27+c*2/27;"
            "var dif=e12-e26;dea=dea==null?dif:dea*8/10+dif*2/10;"
            "out.push({dif:Math.round(dif*1000)/1000,"
            "dea:Math.round(dea*1000)/1000,"
            "mac:Math.round((dif-dea)*2*1000)/1000})}return out}"
            "function _krender(n){if(!_ke||!_kb)return;_kn=n;"
            "var src=_kmode==='w'?_wk():_kmode==='m'?_mo()"
            ":_kmode==='q'?_kqs:_kmode==='y'?_kys:_kb;"
            "var b=src.bars;"
            "var md=_macd(b);"
            "_kds=b.slice(Math.max(0,b.length-n));"
            "_kds.forEach(function(r,i){var m=md[md.length-_kds.length+i];"
            "if(m){r.dif=m.dif;r.dea=m.dea;r.mac=m.mac}});"
            "_kds.forEach(function(r,i){if(!i)return;var p=_kds[i-1];"
            "if(p.dif==null||p.dea==null)return;"
            "if(p.dif<=p.dea&&r.dif>r.dea)r.xg=1;"
            "else if(p.dif>=p.dea&&r.dif<r.dea)r.xd=1});"
            "var dk={},dw=null;_kds.forEach(function(r){dk[r.d]=r});"
            "if(_kmode!=='d'){var kf=_kmode==='w'?_wkkey:_mokey;"
            "dw={};b.forEach(function(r){dw[kf(r.d)]=r})}"
            "_kmk={};(src.marks||[]).forEach(function(m){"
            "var r=_kmode!=='d'?dw[(_kmode==='w'?_wkkey:_mokey)(m.d)]:dk[m.d];"
            "if(r)_kmk[r.d]=m});"
            "_knw={};var nw=src.news||{};"
            "Object.keys(nw).forEach(function(dd){"
            "var r=_kmode!=='d'?dw[(_kmode==='w'?_wkkey:_mokey)(dd)]:dk[dd];"
            "if(r)_knw[r.d]=nw[dd]});"
            "var up='#f85149',dn='#3fb950',mp=[],np=[];"
            "var bl=[],za=[];(src.bis||[]).forEach(function(x){"
            "if(dk[x.s]&&dk[x.e])bl.push([{coord:[x.s,x.sp],"
            "name:(x.d>=0?'↑ ':'↓ ')+(x.pp!=null?x.pp:'')+'%%'},"
            "{coord:[x.e,x.ep]}])});"
            "(src.pivots||[]).forEach(function(z){"
            "if(dk[z.s]||dk[z.e]){var s0=dk[z.s]?z.s:_kds[0].d,"
            "e0=dk[z.e]?z.e:_kds[_kds.length-1].d;"
            "var nn=z.n||0,aa=nn>=9?0.26:nn>=5?0.18:0.10,"
            "bb=nn>=9?0.85:nn>=5?0.65:0.45;"
            "za.push([{xAxis:s0,yAxis:z.zd,"
            "name:'ZD '+z.zd+' ~ ZG '+z.zg+(nn?' · '+nn+' 段':''),"
            "itemStyle:{color:'rgba(210,153,34,'+aa+')',"
            "borderColor:'rgba(210,153,34,'+bb+')',borderWidth:1}},"
            "{xAxis:e0,yAxis:z.zg}])}});"
            "var fxs=[];var bset={};(src.bis||[]).forEach("
            "function(x){bset[x.s]=1;bset[x.e]=1});"
            "(src.fx||[]).forEach(function(f){"
            "if(dk[f.d]&&bset[f.d])fxs.push({d:f.d,p:f.p,t:f.t,u:1})});"
            "var sbl=[];(src.sgs||[]).forEach(function(x){"
            "if(dk[x.s]&&dk[x.e])sbl.push([{coord:[x.s,x.sp],"
            "name:(x.d>=0?'↑ ':'↓ ')+(x.pp!=null?x.pp:'')"
            "+'%% · '+(x.nb||'?')+'笔'},{coord:[x.e,x.ep]}])});"
            "var sgn={b1:'买1',b2:'买2',b3:'买3',"
            "s1:'卖1',s2:'卖2',s3:'卖3'};"
            "var spts=[];(src.sigs||[]).forEach(function(g){"
            "if(dk[g.d])spts.push({d:g.d,p:g.p,"
            "n:(sgn[g.t]||g.t)+(g.sub==='tbe'?'·趋':"
            "g.sub==='pbe'?'·盘':''),"
            "b:g.t[0]==='b'?1:0})});"
            "_kds.forEach(function(r){var m=_kmk[r.d];"
            "if(m){var buy=(m.t||'').indexOf('买')>=0;"
            "mp.push({value:[r.d,buy?r.l*0.985:r.h*1.015],"
            "itemStyle:{color:buy?up:dn},label:{show:true,"
            "formatter:(m.t||'').slice(0,4),fontSize:9,"
            "position:buy?'bottom':'top'}})}"
            "var j=_knw[r.d];"
            "if(j){np.push({value:[r.d,j.b==='利多'?r.h*1.045:r.l*0.955],"
            "symbol:'diamond',symbolSize:9,"
            "itemStyle:{color:j.b==='利多'?'#d29922':'#58a6ff'}})}});"
            "_ke.setOption({animation:false,"
            "tooltip:{trigger:'axis',axisPointer:{type:'cross'},"
            "formatter:function(ps){var h='';ps.forEach(function(p){"
            "if(p.seriesType==='candlestick')h=_kfmt(p);"
            "else if(p.seriesName==='信号'){var m=_kmk[_kds[p.dataIndex].d]||{};"
            "h+='<br>结构 '+(m.t||'')+' conf '+(m.c!=null?m.c:'');"
            "var pd=_kds[p.dataIndex]?_kds[p.dataIndex].d:'';"
            "if(_kpt[pd])h+='<br><span style=\\'color:#d29922\\'>形态共振 "
            "'+_kpt[pd]+'</span>'}"
            "else if(p.seriesName==='JEV'){var j=_knw[_kds[p.dataIndex].d]||{};"
            "h+='<br>JEV '+(j.b||'')+' '+(j.c!=null?j.c+'%%':'')}});return h}},"
            "axisPointer:{link:{xAxisIndex:'all'}},"
            "grid:[{left:56,right:16,top:14,height:216},"
            "{left:56,right:16,top:246,height:44},"
            "{left:56,right:16,top:302,height:64}],"
            "xAxis:[{type:'category',data:_kds.map(function(r){return r.d}),"
            "boundaryGap:true,axisLine:{lineStyle:{color:'#30363d'}},"
            "axisLabel:{color:'#8b949e'}},"
            "{type:'category',gridIndex:1,data:_kds.map(function(r)"
            "{return r.d}),axisLabel:{show:false},axisTick:{show:false}},"
            "{type:'category',gridIndex:2,data:_kds.map(function(r)"
            "{return r.d}),axisLabel:{show:false},axisTick:{show:false}}],"
            "yAxis:[{scale:true,axisLabel:{color:'#8b949e'},"
            "splitLine:{lineStyle:{color:'#161b22'}}},"
            "{gridIndex:1,axisLabel:{show:false},splitLine:{show:false},"
            "axisLabel2:{show:false}},"
            "{gridIndex:2,axisLabel:{show:false},splitLine:{show:false}}],"
            "dataZoom:[{type:'inside',xAxisIndex:[0,1,2],start:0,end:100},"
            "{type:'slider',xAxisIndex:[0,1,2],bottom:2,height:16,"
            "borderColor:'#30363d',textStyle:{color:'#8b949e'}}],"
            "series:["
            "{name:'K',type:'candlestick',data:_kds.map(function(r)"
            "{return [r.o,r.c,r.l,r.h]}),itemStyle:{color:up,color0:dn,"
            "borderColor:up,borderColor0:dn},"
            "markArea:{itemStyle:{color:"
            "'rgba(210,153,34,0.10)',borderColor:"
            "'rgba(210,153,34,0.45)',borderWidth:1},"
            "label:{show:false},emphasis:{label:{show:true,"
            "color:'#e3b341',fontWeight:600}},data:za},"
            "markPoint:{symbol:'triangle',"
            "label:{show:false},silent:true,data:fxs.map("
            "function(f){return{coord:[f.d,f.p],"
            "symbolSize:7,"
            "symbolRotate:f.t>0?180:0,"
            "symbolOffset:f.t>0?[0,-5]:[0,5],"
            "itemStyle:{color:f.t>0?'#f85149':'#3fb950',"
            "borderColor:'#e6edf3',borderWidth:1,"
            "opacity:1}}})},"
            "markLine:{symbol:'none',"
            "lineStyle:{color:'#8b949e',width:1},"
            "label:{show:false},emphasis:{label:{show:true,"
            "color:'#e6edf3',fontWeight:600}},data:bl}},"
            "{name:'线段',type:'line',data:[],"
            "markLine:{symbol:'none',"
            "lineStyle:{color:'#e3b341',width:2},"
            "label:{show:false},emphasis:{label:{show:true,"
            "color:'#e3b341',fontWeight:600}},data:sbl}},"
            "{name:'三类点',type:'line',data:[],"
            "markPoint:{symbol:'diamond',symbolSize:9,"
            "silent:true,data:spts.map(function(g){"
            "var c=g.b?'#f85149':'#3fb950';"
            "return{coord:[g.d,g.p],name:g.n,"
            "label:{show:true,formatter:'{b}',"
            "position:g.b?'bottom':'top',"
            "color:c,fontSize:10,fontWeight:600},"
            "itemStyle:{color:c}}})}},"
            "{name:'MA5',type:'line',data:_kds.map(function(r){return r.m5}),"
            "showSymbol:false,lineStyle:{width:1,color:'#d29922'}},"
            "{name:'MA20',type:'line',data:_kds.map(function(r){return r.m20}),"
            "showSymbol:false,lineStyle:{width:1,color:'#58a6ff'}},"
            "{name:'MA60',type:'line',data:_kds.map(function(r){return r.m60}),"
            "showSymbol:false,lineStyle:{width:1,color:'#bc8cff'}},"
            "{name:'MA250',type:'line',data:_kds.map(function(r){return r.m250}),"
            "showSymbol:false,lineStyle:{width:1.2,color:'#ffa657'},"
            "connectNulls:true},"
            "{name:'信号',type:'scatter',data:mp,"
            "label:{color:'#e6edf3'},z:5},"
            "{name:'JEV',type:'scatter',data:np,z:5},"
            "{name:'量',type:'bar',xAxisIndex:1,yAxisIndex:1,"
            "data:_kds.map(function(r){return r.v}),"
            "itemStyle:{color:function(p){var r=_kds[p.dataIndex];"
            "return r.c>=r.o?up:dn}}},"
            "{name:'MACD',type:'bar',xAxisIndex:2,yAxisIndex:2,"
            "data:_kds.map(function(r){return r.mac}),"
            "itemStyle:{color:function(p){return p.value>=0?up:dn}},"
            "markPoint:{symbol:'circle',symbolSize:7,"
            "label:{show:false},silent:true,data:_kds.filter("
            "function(r){return r.xg||r.xd}).map(function(r){"
            "return{coord:[r.d,r.dif],"
            "itemStyle:{color:r.xg?dn:up}}})}},"
            "{name:'DIF',type:'line',xAxisIndex:2,yAxisIndex:2,"
            "showSymbol:false,lineStyle:{width:1,color:'#d29922'},"
            "data:_kds.map(function(r){return r.dif})},"
            "{name:'DEA',type:'line',xAxisIndex:2,yAxisIndex:2,"
            "showSymbol:false,lineStyle:{width:1,color:'#58a6ff'},"
            "data:_kds.map(function(r){return r.dea})}]},true);_kchips()}"
            "function _kday(){if(!_kds.length)return null;"
            "var o=(_ke.getOption().dataZoom||[])[0]||{};"
            "var en=(o.end==null?100:o.end);"
            "var i=Math.min(_kds.length-1,Math.max(0,"
            "Math.ceil(en/100*_kds.length)-1));return _kds[i]}"
            "function _kchips(){var r=_kday();if(!r)return;"
            "if(r.d===_kcd)return;_kcd=r.d;"
            "if(Object.prototype.hasOwnProperty.call(_kcm,r.d)){"
            "var c0=_kcm[r.d];if(c0)_kdraw(r,c0);return}"
            "_kcm[r.d]=null;"
            "fetch('/api/chips?c='+encodeURIComponent(_kc)+'&d='+r.d+'&x='+r.c)"
            ".then(function(t){return t.json()})"
            ".then(function(j){_kcm[r.d]=j;if(r.d===_kcd)_kdraw(r,j)})"
            ".catch(function(){_kcm[r.d]={ok:false}})}"
            "function _kfix(){setTimeout(function(){"
            "if(_ke)_ke.resize();if(_kcc)_kcc.resize()},30)}"
            "function _kdraw(r,j){var el=document.getElementById('kxc');"
            "if(!j||!j.ok||!j.rows||!j.rows.length){"
            "if(el.style.display!=='none'){el.style.display='none';"
            "_kfix()}return}"
            "el.style.display='block';_kfix();"
            "if(!_kcc)_kcc=echarts.init(el);"
            "var rows=j.rows,up='#f85149',bl='#58a6ff',mx=0,i,step=1;"
            "for(i=0;i<rows.length;i++)if(rows[i][1]>mx)mx=rows[i][1];"
            "for(i=1;i<rows.length;i++){step=rows[i][0]-rows[i-1][0];"
            "if(step>0)break}"
            "var xm=Math.max(1,Math.ceil(mx*1.15));"
            "_kcc.setOption({animation:false,"
            "title:{text:'筹码瀑布 '+((j.d||r.d)+'').slice(5)+' · 获利 '"
            "+(j.winner==null?'—':j.winner)+'%%'"
            "+(j.avg!=null?' · 均成本 '+j.avg:''),right:2,top:0,"
            "textStyle:{color:'#8b949e',fontSize:11,fontWeight:400}},"
            "grid:{left:46,right:8,top:22,bottom:10},"
            "xAxis:{type:'value',max:xm,axisLabel:{show:false},"
            "splitLine:{show:false},axisLine:{show:false},"
            "axisTick:{show:false}},"
            "yAxis:{type:'value',scale:true,"
            "axisLabel:{color:'#8b949e',fontSize:10},splitLine:{show:false}},"
            "series:["
            "{type:'custom',data:rows,z:2,renderItem:function(params,api){"
            "var price=api.value(0),pct=api.value(1);"
            "var p0=api.coord([0,price]),p1=api.coord([pct,price]);"
            "var h=Math.max(1.5,Math.min(api.size([0,step])[1]*0.92,14));"
            "return {type:'rect',shape:{x:p0[0],y:p1[1]-h/2,"
            "width:Math.max(0.5,p1[0]-p0[0]),height:h},"
            "style:{fill:price<=r.c?up:bl,opacity:.9}}}},"
            "{type:'line',data:[[0,r.c],[xm,r.c]],showSymbol:false,"
            "silent:true,z:3,lineStyle:{color:'#e6edf3',width:1,"
            "type:'dashed'}}]},true);_ksync()}"
            "function _ksync(){if(!_ke||!_kcc)return;var e=null;"
            "try{var s=_ke.getModel().getComponent('yAxis',0).axis.scale;"
            "e=s._extent||(s.getExtent?s.getExtent():null)}catch(ex){}"
            "if(e&&isFinite(e[0])&&isFinite(e[1])&&e[1]>e[0])"
            "_kcc.setOption({yAxis:{min:e[0],max:e[1]}})}"
            "function _kqyfetch(done){fetch('/api/kline_qy?c='+"
            "encodeURIComponent(_kc)).then(function(r){return r.json()})"
            ".then(function(j){if(!j.ok){document.getElementById('kmsg')"
            ".textContent=j.err||'季/年加载失败';return}"
            "_kqs=j.q;_kys=j.y;"
            "document.getElementById('kmsg').textContent="
            "'季/年就绪 · 深历史 '+(j.n_deep||0)+' 根日线';done()})"
            ".catch(function(){document.getElementById('kmsg')"
            ".textContent='季/年加载失败'})}"
            "function _kload(){"
            "fetch('/api/kline?c='+encodeURIComponent(_kc))"
            ".then(function(r){return r.json()}).then(function(j){"
            "var m=document.getElementById('kmsg');"
            "if(!j.ok){m.textContent=j.err||'无K线数据';return}"
            "_kb=j;var nn=Object.keys(j.news||{}).length;"
            "_kpt=j.ptmap||{};"
            "m.textContent=j.name+' · '+j.bars.length+'根'"
            "+((j.marks||[]).length?(' · 结构信号'+j.marks.length+'点'):'')"
            "+((j.bis||[]).length?(' · 笔'+j.bis.length):'')"
            "+((j.pivots||[]).length?(' · 中枢'+j.pivots.length):'')"
            "+((j.sgs||[]).length?(' · 段'+j.sgs.length):'')"
            "+((j.fx||[]).length?(' · 分型'+j.fx.length):'')"
            "+(nn?(' · JEV色点'+nn+'日'):'');"
            "_kmsgbase=m.textContent;"
            "_ke=echarts.init(document.getElementById('kx'));"
            "_ke.on('dataZoom',function(){_kchips();_ksync()});"
            "window.addEventListener('resize',function(){if(_ke)_ke.resize();"
            "if(_kcc)_kcc.resize()});"
            "_krender(250);"
            "document.querySelectorAll('.kpb').forEach(function(btn){"
            "btn.onclick=function(){"
            "if(btn.dataset.m){document.querySelectorAll('.kpb[data-m]')"
            ".forEach(function(x){x.classList.remove('on')});"
            "_kmode=btn.dataset.m;_kcd='';"
            "if((_kmode==='q'&&!_kqs)||(_kmode==='y'&&!_kys)){"
            "document.getElementById('kmsg').textContent='季/年加载中…';"
            "btn.classList.add('on');_kqyfetch(function(){_krender(_kn)});"
            "return}"
            "if(_kmode==='q'||_kmode==='y'){document.getElementById('kmsg')"
            ".textContent='季/年就绪 · '+((_kmode==='q'?_kqs:_kys)"
            ".bars.length)+' 根'}"
            "else{document.getElementById('kmsg').textContent=_kmsgbase}}"
            "else{document.querySelectorAll('.kpb[data-n]')"
            ".forEach(function(x){x.classList.remove('on')});"
            "_kn=parseInt(btn.dataset.n,10)}"
            "btn.classList.add('on');_krender(_kn)}})})"
            ".catch(function(ex){document.getElementById('kmsg').textContent="
            "'K线加载失败'})}"
            "_kload();</script>" % json.dumps(code, ensure_ascii=False))
        # ── K41 基本面（估值·盈利·成长·健康；K235 扩容为分组汇总，
        # (组,标签,值) 三元组 · 增速带前值对照；A/HK/US 三市场降级安全） ──
        frows, fnote = self._stock_fundamental(code)
        if frows:
            _gorder = []
            for g, _lb, _v in frows:
                if g not in _gorder:
                    _gorder.append(g)
            _pil = ("<div style='display:inline-block;margin:4px 24px 6px 0'>"
                    "<div class='dim' style='font-size:11px'>%s</div>"
                    "<b style='font-size:16px;font-family:Consolas,"
                    "'Microsoft YaHei',monospace'>%s</b></div>")
            body.append(
                "<h2>🏛 基本面 · 估值 / 盈利 / 成长 / 财务健康</h2>"
                "<div class='card'>"
                + "".join(
                    "<div style='margin:2px 0'><span class='dim' style="
                    "'display:inline-block;width:56px;font-size:12px;"
                    "vertical-align:top;padding-top:8px'>%s</span>%s</div>"
                    % (g, "".join(_pil % (lb, v)
                                  for gg, lb, v in frows if gg == g))
                    for g in _gorder)
                + ("<div class='dim' style='margin-top:6px'>%s · "
                   "数据源 tushare / TDX（财务 5 日缓存 · 估值 1 日缓存）"
                   "</div>" % e(fnote) if fnote else "")
                + "</div>")
        else:
            body.append("<h2>🏛 基本面</h2><div class='card dim'>%s</div>"
                        % e(fnote or "暂无基本面数据"))

        # ② 方法论矩阵（注册表驱动，点卡下钻；卡片构建抽为纯函数
        # _matrix_cards，供单元测试直接覆盖）
        mcards = _matrix_cards(cross_data, cross_hit, sig_states,
                               sig_err, items)
        body.append(
            "<h2>② 方法论矩阵 · 一只票 × 各方法论"
            "<span style='font-size:11px;color:#58a6ff;border:1px solid "
            "#58a6ff;border-radius:10px;padding:1px 8px;margin-left:8px;"
            "vertical-align:2px'>🌊 青萍之末产线</span>"
            " <span class='dim' "
            "style='font-weight:400'>（点卡下钻 · 策略卡随 cross_poll "
            "注册表自动扩展）</span></h2>"
            "<div class='mgrid'>%s</div>" % "".join(mcards))

        # ③ 共振分解（rps_latest 快照；rr/snap 已在数据收集阶段加载）
        # K235 扩容：四维丢分明细——每维得分/权重/对综合贡献/丢分缺失点
        if rr:
            cls = ("buy" if "买入" in rr["action"] else
                   "watch" if "观察" in rr["action"] else "avoid")
            _w = rr.get("w") or {}

            def _row(dim, cn, val, note):
                w = _w.get(dim)
                contrib = (val * w) if (w and val is not None) else 0
                st = ("<b>%.0f</b>" % val) if val is not None else \
                     "<span class='dim'>缺维</span>"
                return ("<tr><td>%s</td><td>%s</td><td class='dim'>%s</td>"
                        "<td>%s</td><td class='dim'>%s</td></tr>"
                        % (cn, st,
                           ("%.0f%%" % (w * 100)) if w else "—",
                           ("+%.1f 分" % contrib) if contrib else "—",
                           note))
            _nlong = sum(1 for v in sig_states.values() if v == "多头")
            _weak = "、".join(
                "%s%s" % (cn_, st_) for sn_, cn_ in SIG4_LIST
                for st_ in [sig_states.get(sn_)] if st_ and st_ != "多头") \
                or "4 策略全多头"
            _r_note = ("%s口径%s" % (rr.get("rps_label") or "",
                                     " · 前 15%%" if (rr.get("rps") or 0)
                                     >= 85 else ""))
            _e_note = ("市场闸门放行" if (rr.get("env") or 0) >= 50 and
                       rr.get("env") is not None else "市场闸门偏冷区间")
            _rows = "".join([
                _row("tech", "技术", rr["tech"],
                     "%d/4 策略多头（弱：%s）" % (_nlong, _weak)),
                _row("jev", "JEV消息面", rr.get("jev"),
                     ("近7天 %d 条已判断新闻" % (rr.get("jev_n") or 0))
                     if rr.get("jev") is not None
                     else "近7天无已判断新闻（25% 权重未计入·降权）"),
                _row("rps", "RPS动量", rr.get("rps"),
                     _r_note if rr.get("rps") is not None
                     else "无排名数据（缺维降权）"),
                _row("env", "环境闸门", rr.get("env"),
                     _e_note if rr.get("env") is not None
                     else "闸门数据缺失（缺维降权）")])
            body.append(
                "<h2>③ 共振分解（%s 更新 · 四维缺维降权）"
                "<span style='font-size:11px;color:#58a6ff;border:1px solid "
                "#58a6ff;border-radius:10px;padding:1px 8px;margin-left:8px;"
                "vertical-align:2px'>🌊 青萍之末产线</span>"
                "<span style='float:right;font-size:12px;font-weight:400'>"
                "<a href='/bt-qpzm'>→ 青萍之末工作台</a></span></h2><div "
                "class='card'>综合 <b class='%s' style='font-size:20px'>"
                "%d</b> · <b class='%s'>%s</b> · 维度 %s<table><tr><th>"
                "维度</th><th>得分</th><th>权重</th><th>贡献</th><th>丢分/"
                "缺失点</th></tr>%s</table></div>"
                % (snap.get("latest", ""), cls, rr["score"], cls,
                   rr["action"], rr["dims"], _rows))
        else:
            body.append("<h2>③ 共振分解"
                        "<span style='font-size:11px;color:#58a6ff;border:"
                        "1px solid #58a6ff;border-radius:10px;padding:1px "
                        "8px;margin-left:8px;vertical-align:2px'>🌊 青萍之末"
                        "产线</span></h2><div class='card dim'>不在"
                        "自选池（共振仅覆盖自选标的），全市场 RPS 见"
                        "对应市场页 · <a href='/bt-qpzm'>青萍之末 →</a>"
                        "</div>")

        # ④ 策略信号（数据来自上方 sig_states；点策略名进纵切页）
        body.append(
            "<h2 id='sig4'>④ 策略信号（4 策略当前持仓状态）"
            "<span style='font-size:11px;color:#58a6ff;border:1px solid "
            "#58a6ff;border-radius:10px;padding:1px 8px;margin-left:8px;"
            "vertical-align:2px'>🌊 青萍之末产线</span>"
            "<span style='float:right;font-size:12px;font-weight:400'>"
            "点策略名 → 纵切页</span></h2>")
        sig_rows = []
        for sn, cn in SIG4_LIST:
            st = sig_states.get(sn)
            if st == "多头":
                bd = "<span class='b-long'>多头</span>"
            elif st == "空头":
                bd = "<span class='b-short'>空头</span>"
            elif st == "空仓":
                bd = "<span class='b-flat'>空仓</span>"
            else:
                bd = "<span class='b-flat'>—</span>"
            sig_rows.append("<tr><td><a href='/bt/strategy/%s'>%s</a></td>"
                            "<td>%s</td></tr>" % (sn, e(cn), bd))
        body.append("<table>%s</table>" % (
            "".join(sig_rows) if sig_states else
            "<tr><td class='dim'>%s</td></tr>" % (sig_err or "—")))

        # ⑤ 全事件时间轴（S2）：JEV判断 + 公告/事件 + 缠论结构 +
        # 盘口异动 + 涨跌停 合并单条倒序时间线（近30天，每项带徽章/
        # 置信度/一句话依据）—— 替代原⑤散卡片与⑤+散卡片两块
        _es = _er.get("bias_stat") or {}
        _nev = round(_es.get("net") or 0, 2)
        _egc = ("buy" if _nev > 0.5 else "avoid" if _nev < -0.5 else "")
        _BCN = {"bull": "利多", "bear": "利空", "neutral": "中性",
                None: "未判"}
        from datetime import datetime as _dtn, timedelta as _tdn
        _cut = (_dtn.now() - _tdn(days=30)).strftime("%Y-%m-%d")

        def _dn(ds):
            # 图谱日期多为 YYYYMMDD 裸串：统一转 YYYY-MM-DD，保证
            # 30天窗口字符串比较正确与展示格式一致
            ds = ds or ""
            if len(ds) == 8 and ds.isdigit():
                return "%s-%s-%s" % (ds[:4], ds[4:6], ds[6:])
            return ds
        _tl = []
        for it in items:                    # JEV 判断新闻（近7天）
            _tl.append({"d": (it.get("ts") or "")[:10],
                        "c": ("g" if it["bias_cn"] == "利多" else
                              "r" if it["bias_cn"] == "利空" else "n"),
                        "b": "%s %d%%" % (it["bias_cn"], it["conf"]),
                        "t": it["title"],
                        "s": ((it["reason"] or "")[:66] or "—")
                        + " · JEV判断"})
        _n_rpt = 0
        for _ev in (_er.get("events") or []):   # 公告/事件（30天）
            _b = _ev.get("bias")
            _is_rpt = _ev.get("kind") == "report"
            if _is_rpt:
                _n_rpt += 1
            _tl.append({
                "d": (_ev.get("ts") or "")[:10],
                "c": ("g" if _b == "bull" else
                      "r" if _b == "bear" else "n"),
                "b": ("%s %d%%" % (_BCN.get(_b, "未判"),
                                   round((_ev.get("conf") or 0) * 100))
                      if _is_rpt else _BCN.get(_b, "未判")),
                "t": (_ev.get("text") or "")[:90],
                "s": (_ev.get("tag") or "—")
                + (" · 研报AI" if _is_rpt else " · 事件图谱")})
        if st_kg:
            for s in (st_kg.get("structures") or []):  # 缠论结构信号
                _ty = s.get("type") or ""
                _tl.append({"d": _dn(s.get("date")),
                            "c": ("g" if "买" in _ty else
                                  "r" if "卖" in _ty else "y"),
                            "b": _ty or "结构",
                            "t": "缠论%s · conf %.2f" % (
                                _ty, s.get("conf") or 0),
                            "s": "结构%.1f 佐证%.1f 资金%.1f%s · 图谱"
                            % (s.get("q_struct") or 0,
                               s.get("q_evid") or 0, s.get("q_hm") or 0,
                               (" · 实证×%d" % s["cc"])
                               if s.get("cc") else "")})
            for r in (st_kg.get("candles") or []):     # 盘口异动
                _ty = r.get("type") or ""
                _tl.append({"d": _dn(r.get("date")),
                            "c": ("g" if "seal" in _ty and _ty != "broken"
                                  else "r" if _ty in ("broken", "late_dump")
                                  else "y"),
                            "b": CANDLE_CN.get(_ty, _ty or "盘口"),
                            "t": "盘口%s · 收盘%+.2f%%" % (
                                CANDLE_CN.get(_ty, _ty),
                                (r.get("pct") or 0) * 100),
                            "s": (("触板%d分" % r["n_touch"])
                                  if r.get("n_touch")
                                  else ("尾盘量%d%%" % round(
                                      (r.get("tail_vol") or 0) * 100))
                                  if r.get("tail_vol") else "分时特征")
                            + " · 图谱"})
            for r in (st_kg.get("limits") or []):      # 涨跌停
                _lt = r.get("ltp") or "U"     # U涨停/D跌停/Z炸板（K159）
                _lab = ("涨停" if _lt == "U" else
                        "炸板" if _lt == "Z" else "跌停")
                _tl.append({"d": _dn(r.get("date")),
                            "c": "g" if _lt == "U" else
                            ("y" if _lt == "Z" else "b"),
                            "b": ("涨停%d板" % (r.get("lt") or 1))
                            if _lt == "U" else _lab,
                            "t": "%s · %+.2f%%" % (
                                _lab, (r.get("pct") or 0) * 100),
                            "s": ("封单%.0f万" % ((r.get("lamt") or 0) / 1e4)
                                  + ((" · 炸板%d次" % r["ot"])
                                     if r.get("ot") else ""))
                            + " · 图谱"})
        _tl = [x for x in _tl if x["d"] and x["d"] >= _cut]
        _tl.sort(key=lambda x: x["d"], reverse=True)
        _src = []
        if st_kg:
            _src.append("图谱结构/盘口/涨跌停")
        if _er.get("n"):
            _src.append("公告事件" + ("（含研报AI %d条）" % _n_rpt
                                     if _n_rpt else ""))
        _src.append("JEV判断")
        body.append(self._KG_CSS)
        body.append(
            "<h2 id='jev'>⑤ 全事件时间轴 <span class='dim' "
            "style='font-weight:400;font-size:12px'>（近30天 %d 条 · "
            "事件图谱多空净（时衰加权） <b class='%s'>%+.2f</b> · 来源：%s）"
            "</span></h2>"
            % (len(_tl), _egc, _nev, " / ".join(_src)))
        if _tl:
            body.append("<div class='tl'>%s</div>" % "".join(
                "<div class='ti'><span class='bdg %s'>%s</span>%s"
                "<div class='src'>%s · %s</div></div>"
                % (x["c"], e(x["b"]), e(x["t"][:100]), e(x["s"]), x["d"])
                for x in _tl[:80]))
        else:
            body.append("<div class='card dim'>近30天无事件（JEV判断 / "
                        "公告 / 结构信号 / 盘口 / 涨跌停）</div>")
        body.append("<div class='dim' style='margin-top:6px'>"
                    "<a href='/bt-jev'>全部 JEV 判断 →</a></div>")

        # （⑤+ 事件图谱与 ⑤ JEV 散卡片已合并为上方 ⑤ 全事件时间轴，
        #  数据收集提前至 st_kg/_er 区；此处不再单独渲染）

        # ── K41 公告与研报（tdx_info.db 直查 · 原文链接下钻；A 股） ──
        anns, rpts = self._stock_notices(code)
        if anns:
            body.append(
                "<h2>📰 公告与研报 <span class='dim' style='font-weight:"
                "400;font-size:12px'>（可下钻原文 · 公告近 8 条%s）"
                "</span></h2><div class='card'>"
                % (" · 研报近 %d 条" % len(rpts) if rpts else "")
                + "".join(
                    "<div style='margin:6px 0;font-size:13px'>"
                    "<span class='dim'>%s</span> <a href='%s' target="
                    "'_blank' style='color:#58a6ff'>%s ↗</a></div>"
                    % (e(str(a["date"] or "")[:10]), e(a["url"] or "#"),
                       e(a["title"] or "")) for a in anns)
                + ("<div class='dim' style='margin:10px 0 4px'>── 券商"
                   "研报 ──</div>"
                   + "".join(
                       "<div style='margin:6px 0;font-size:13px'>"
                       "<span class='dim'>%s · %s</span> <b>%s</b> "
                       "<a href='%s' target='_blank' style='color:#58a6ff'>"
                       "%s ↗</a></div>"
                       % (e(str(r["date"] or "")[:10]), e(r["org"] or ""),
                          e(r["rating"] or ""), e(r["url"] or "#"),
                          e(r["name"] or "")) for r in rpts)
                   if rpts else "")
                + "</div>")

        # ── K235 公告后验（JEV 佐证）+ 量能事件时间点（A股；均基于
        # 同源 df 130 日线，data 缺失时整块安全跳过） ──
        # ① 公告日 → 当日/3日/5日收盘涨跌：公告到底是利好还是利空，
        #   用后验数据说话；② 巨量(≥2×5日均量)/地量(≤0.5×)交易日明细
        _pv_rows, _ve_rows = [], []

        def _fs(x):
            # K-STOCK-029 修复：_fs 曾定义在下方 anns 条件块内，港股等
            # anns 为空而量能事件非空的路径在 genexpr 里 NameError 崩页
            return ("<span class='%s'>%+.1f%%</span>"
                    % ("up" if x > 0 else "dn", x)) \
                if x is not None else "<span class='dim'>—</span>"
        if (anns and df is not None and len(df) > 10
                and "close" in df.columns):
            try:
                _dts = [str(x)[:10] for x in df.index]
                _cl = df["close"].tolist()

                for a in anns:
                    d = str(a.get("date") or "")[:10].replace("-", "")
                    d = (d[:4] + "-" + d[4:6] + "-" + d[6:]) \
                        if len(d) == 8 else d
                    if len(d) != 10:
                        continue
                    i0 = next((i for i, x in enumerate(_dts) if x >= d),
                              None)      # 公告日≥d 首个交易日（顺延）
                    if i0 is None or i0 < 1 or not _cl[i0 - 1]:
                        continue
                    base = _cl[i0 - 1]

                    def _cg(i2):
                        return ((_cl[i2] / base - 1) * 100
                                if i2 < len(_cl) and _cl[i2] else None)
                    _pv_rows.append(
                        "<tr><td class='dim' style='padding:3px 8px 3px 0'>"
                        "%s</td><td style='padding:3px 8px 3px 0'>%s</td>"
                        "<td style='padding:3px 8px'>%s</td>"
                        "<td style='padding:3px 8px'>%s</td>"
                        "<td style='padding:3px 8px'>%s</td></tr>"
                        % (d, e((a.get("title") or "")[:28]),
                           _fs(_cg(i0)), _fs(_cg(i0 + 2)), _fs(_cg(i0 + 4))))
            except Exception:
                _pv_rows = []
        if df is not None and len(df) > 10 and "volume" in df.columns:
            try:
                import numpy as _np2
                _v = df["volume"]
                _v5 = _v.rolling(5).mean()
                _pc = df["close"].pct_change() * 100
                for i in range(len(df)):
                    r = _v5.iloc[i]
                    vi = _v.iloc[i]
                    if _np2.isnan(vi) or not r or r <= 0:
                        continue
                    mult = vi / r
                    tag = ("巨量" if mult >= 2 else
                           "地量" if mult <= 0.5 else None)
                    if tag:
                        _ve_rows.append((str(df.index[i])[:10], tag,
                                         mult, _pc.iloc[i]))
            except Exception:
                _ve_rows = []
        if _pv_rows or _ve_rows:
            _pv_html = _ve_html = ""
            if _pv_rows:
                _pv_html = (
                    "<div style='margin:6px 0 2px'><b>公告后验</b> "
                    "<span class='dim' style='font-size:11px'>（JEV 佐证"
                    " · 公告日 → 当日 / 3日 / 5日 收盘涨跌 · 红涨绿跌）"
                    "</span></div><table style='border-collapse:collapse;"
                    "font-size:13px'><tr class='dim'><td style='padding:"
                    "3px 8px 3px 0'>公告日</td><td style='padding:3px "
                    "8px 3px 0'>标题</td><td style='padding:3px 8px'>"
                    "当日</td><td style='padding:3px 8px'>3日</td><td "
                    "style='padding:3px 8px'>5日</td></tr>"
                    + "".join(_pv_rows) + "</table>")
            if _ve_rows:
                _ve_rows = _ve_rows[-8:][::-1]     # 最近 8 次，最新在前
                _ve_html = (
                    "<div style='margin:12px 0 2px'><b>量能事件时间点"
                    "</b> <span class='dim' style='font-size:11px'>"
                    "（巨量 ≥2× / 地量 ≤0.5× 5日均量 · 近 8 次 · 聚合"
                    "计数见下方图谱面）</span></div><table style='border-"
                    "collapse:collapse;font-size:13px'><tr class='dim'>"
                    "<td style='padding:3px 8px 3px 0'>日期</td><td "
                    "style='padding:3px 8px 3px 0'>类型</td><td style="
                    "'padding:3px 8px'>量/均量</td><td style='padding:"
                    "3px 8px'>当日涨跌</td></tr>"
                    + "".join(
                        "<tr><td class='dim' style='padding:3px 8px 3px 0'>"
                        "%s</td><td style='padding:3px 8px 3px 0'><span "
                        "class='bdg %s'>%s</span></td><td style='padding:"
                        "3px 8px'>%.1f×</td><td style='padding:3px 8px'>"
                        "%s</td></tr>"
                        % (d_, "y" if t_ == "巨量" else "n", t_, m_,
                           _fs(p_))
                        for d_, t_, m_, p_ in _ve_rows)
                    + "</table>")
            body.append(
                "<h2>🔎 后验与量能 <span class='dim' style='font-weight:"
                "400;font-size:12px'>（公告效验 · 量能拐点 · 同源 130 日"
                "线计算）</span></h2><div class='card'>"
                + _pv_html + _ve_html + "</div>")

        # ⑤++/S3 图谱关联面：游资痕迹 + 量能事件 + 筹码摘要 + 十大
        # 流通股东（A股图谱专属；美股/港股/加密显示明确不适用说明，
        # 不再静默空白）。/kg/stock 图谱视角与此页双向跳转
        if st_kg:
            from urllib.parse import quote as _qt3
            hmr = "".join(
                "<tr><td><a href='/kg/map?hm=%s'>%s</a></td><td>%d</td>"
                "<td class='%s'>%+.1f亿</td><td class='dim'>%s</td></tr>"
                % (_qt3(r["hm"]), e(r["hm"]), r["n"],
                   "up" if (r["net"] or 0) > 0 else "dn",
                   (r["net"] or 0) / 1e8, _dn(r["last"])[:10])
                for r in (st_kg.get("hotmoney") or [])[:12]) or \
                ("<tr><td colspan=4 class='dim'>窗口内无游资上榜痕迹"
                 "（龙虎榜仅上榜日披露）</td></tr>")
            volr = "".join(
                "<tr><td><span class='bdg y'>%s</span></td><td>%d 次</td>"
                "</tr>"
                % (e(v["type"] or ""), v["n"])
                for v in (st_kg.get("vol_events") or [])[:6]) or \
                "<tr><td colspan=2 class='dim'>无量能事件</td></tr>"
            cyq = st_kg.get("cyq") or {}
            _bh = st_kg.get("basic") or {}
            _cyq_html = ""
            if cyq.get("winner_rate") is not None:
                _wr = cyq["winner_rate"]
                _wr20 = cyq.get("wr_chg20")
                _cyq_html = (
                    "<tr><td>获利盘</td><td><b class='%s'>%.1f%%</b>%s"
                    "</td><td>成本跨度 %.2f · 加权成本 %.2f（%s）</td>"
                    "</tr>"
                    % ("up" if _wr >= 50 else "dn", _wr,
                       (" · 20日%+.1f" % _wr20) if _wr20 is not None
                       else "", cyq.get("cost_span") or 0,
                       cyq.get("weight_avg") or 0,
                       _dn(cyq.get("date"))))
            _hd = {}
            for r in (st_kg.get("holders") or []):
                if r.get("period") not in _hd:
                    _hd[r.get("period")] = []
                if len(_hd[r.get("period")]) < 10:
                    _hd[r.get("period")].append(r)
            _hp = max(_hd) if _hd else None
            hld = "".join(
                "<tr><td>%s</td><td>%s%%</td><td class='dim'>%s</td></tr>"
                % (e(r["name"]),
                   ("%.2f" % r["ratio"]) if r.get("ratio") is not None
                   else "—",
                   ("%.0f万股" % ((r.get("amt") or 0) / 1e4))
                   if r.get("amt") else "—")
                for r in (_hd.get(_hp) or [])) or \
                "<tr><td colspan=3 class='dim'>无股东数据</td></tr>"
            body.append(
                "<h2>⑤++ 图谱关联面 <span class='dim' style='font-weight:"
                "400;font-size:12px'>（近90天 · %s%s · <a href='/kg?c="
                "%s'>图谱视角 →</a> · <a href='/kg/company?c=%s'>🏢 公司"
                "本体全景 →</a>）</span></h2>"
                "<div class='duo'><div><h3>游资席位痕迹</h3><table "
                "class='sticky'><tr><th>席位</th><th>次数</th><th>净买"
                "</th><th>最近</th></tr>%s</table><h3 style='margin-top:"
                "14px'>量能事件</h3><table><tr><th>类型</th><th>频次"
                "</th></tr>%s</table></div><div><h3>筹码与股东</h3>"
                "<table>%s<tr><td>行业</td><td>%s</td><td>%s · 上市%s"
                "</td></tr></table><h3 style='margin-top:14px'>十大流通"
                "股东（%s）</h3><table><tr><th>股东</th><th>占比</th>"
                "<th>持股</th></tr>%s</table></div></div>"
                % (e((_bh.get("sw_l1") or _bh.get("industry") or "—")),
                   (" · " + e(_bh.get("company") or ""))
                   if _bh.get("company") else "",
                   _qt3(_kg_ts), _qt3(_kg_ts), hmr, volr, _cyq_html,
                   e(_bh.get("area") or "—"),
                   e(_bh.get("industry") or "—"),
                   e((_bh.get("list_date") or "")[:4] or "—"),
                   e(_dn(_hp) or "—"), hld))
        else:
            body.append(
                "<h2>⑤++ 图谱关联面</h2><div class='card dim'>"
                "图谱面（游资席位 / 缠论结构 / 盘口 / 股东 / 筹码）为 "
                "A 股专属——龙虎榜、公告、股东库仅覆盖 A 股；本标的为 "
                "%s，K 线与 JEV 新闻时间轴不受影响</div>" % mkt_label)

        # ⑤++ 深度研判（P-B2：事实包+本地LLM，按钮触发+当日缓存）
        body.append(
            "<h2 id='ra'>⑤++ 深度研判 <span class='dim' "
            "style='font-size:12px'>（事实包→本地LLM·≤220字·"
            "含操作建议白名单）</span></h2>"
            "<button onclick=\"_ra()\">🤖 深度研判（约10-90s）</button>"
            "<div id='raout' class='dim' style='margin-top:10px'></div>"
            "<script>var _rac=%s;"
            "function _raShow(j){document.getElementById('raout').innerHTML="
            "'<div class=card><pre style=\"white-space:pre-wrap;margin:0\">'"
            "+j.text+'</pre><div class=dim style=margin-top:6px>来源:"
            "'+j.source+(j.model?(' · '+j.model):'')+'</div></div>';}"
            "fetch('/api/research?c='+encodeURIComponent(_rac))"
            ".then(function(r){return r.json()}).then(function(j){"
            "if(j&&j.ok&&j.text&&!j.empty){_raShow(j)}});"
            "function _ra(){var o=document.getElementById('raout');"
            "o.innerHTML='研判生成中…（事实包→本地LLM，10-90s）';"
            "fetch('/api/research?c='+encodeURIComponent(_rac)"
            "+'&force=1').then(function(r){return r.json()})"
            ".then(function(j){if(!j.ok){o.innerHTML='失败：'"
            "+(j.err||'未知');return;}_raShow(j)});}"
            "</script>" % json.dumps(code, ensure_ascii=False))

        # ⑥ AI 深度分析（复用 /sa 任务：行情+新闻+JEV+Ollama 展望）
        body.append(
            "<h2 id='ai'>⑥ AI 深度分析</h2>"
            "<button onclick=\"_go()\">🤖 生成深度分析（约2-4分钟）"
            "</button><div id='saout' class='dim' style='margin-top:10px'"
            "></div><script>var _q=%s;"
            "function _go(){var o=document.getElementById('saout');"
            "o.innerHTML='分析中…（行情+关联新闻+JEV+展望）';"
            "fetch('/sa/start?q='+encodeURIComponent(_q)).then(function(r)"
            "{return r.json()}).then(function(j){if(j.error){o.innerHTML="
            "j.error;return;}_poll(j.job)});}"
            "function _poll(id){fetch('/sa/result?job='+id).then(function(r)"
            "{return r.json()}).then(function(j){if(j.status!=='done'){"
            "setTimeout(function(){_poll(id)},3000);return;}_render(j.d)});}"
            "function _render(d){if(!d||!d.ok){document.getElementById("
            "'saout').innerHTML='分析失败：'+(d&&d.msg||'未知');return;}"
            "var h='<div class=card><b>'+d.name+'</b> 综合倾向 <b>'+d.bias"
            "+'</b> · 信号 '+(d.strength>0?'+':'')+d.strength+' · 现价 '+d.px"
            "+'<br>'+d.stat+'<br><br>'+d.outlook+'</div>';"
            "if(d.judged){h+='<div class=dim style=margin-top:8px>JEV 依据:"
            "</div>';d.judged.forEach(function(x){h+='<div class=card>'+"
            "(x.title||'').slice(0,46)+' <b>['+x.bias_cn+']</b></div>'});}"
            "document.getElementById('saout').innerHTML=h;}"
            "</script>" % json.dumps(name, ensure_ascii=False))
        body.append("<div class='dim' style='margin-top:26px'>选股中心 · "
                    "纸面研究不构成投资建议</div>")
        self._html("".join(body))


    def _bt_stock_form(self, msg=""):
        return ("<h1>🔍 个股全景</h1>"
                "<div class='dim' style='margin-bottom:10px'>%s</div>"
                "<input id='q' placeholder='名称或代码，如 英伟达 / TSLA / "
                "600519 / 00700' onkeydown=\"if(event.key==='Enter')_go()\">"
                "<button style='margin-left:8px' onclick='_go()'>查看"
                "</button><script>function _go(){var v=document."
                "getElementById('q').value.trim();if(v){location='/bt/"
                "stock?c='+encodeURIComponent(v)}}</script>" % msg)


    def _bt_jev(self):
        """JEV 新闻判断图表页：news.db jev 列近 7 天。
        总览三卡 + 市场分组双向条 + 24h 净倾向时间轴 + 高置信要闻榜。"""
        import html as _html
        import sqlite3 as _sq
        from datetime import datetime as _dt, timedelta as _td
        from datetime import timezone as _tz
        cst = _tz(_td(hours=8))
        now = _dt.now(cst)
        rows = []
        try:
            db = _sq.connect(os.path.join(STATION, "news_collector", "news.db"))
            db.row_factory = _sq.Row
            rows = db.execute(
                "SELECT ts, title, jev FROM news "
                "WHERE jev IS NOT NULL AND jev!='' "
                "ORDER BY ts DESC LIMIT 3000").fetchall()
            db.close()
        except Exception:
            rows = []

        _BJ = {"bull": "利多", "bear": "利空", "neutral": "中性"}
        _SC = {"cn": "A股", "hk": "港股", "us": "美股",
               "commodity": "商品", "crypto": "加密", "macro": "宏观"}
        parsed = []
        for r in rows:
            try:
                j = json.loads(r["jev"])
                t = _dt.strptime(r["ts"], "%Y-%m-%d %H:%M").replace(tzinfo=cst)
            except Exception:
                continue
            parsed.append({
                "ts": t, "ts_s": (r["ts"] or "")[-5:], "title": r["title"] or "",
                "bias": j.get("bias", "?"), "bias_cn": _BJ.get(j.get("bias")),
                "conf": float(j.get("conf") or 0),
                "scope": j.get("scope") or "?",
                "scope_cn": _SC.get(j.get("scope")) or (j.get("scope") or "?")})

        def _cnt(items, b):
            return sum(1 for x in items if x["bias"] == b)

        _CLICK = "cursor:pointer;transition:border-color .12s;" \
                  "border:1px solid #30363d"  # 可下钻元素基础样式
        _SEL = "this.parentElement.querySelectorAll('[data-dj]')"
        _ON = ("onclick=\"for(var e of %s)e.style.borderColor="
               "'#30363d';this.style.borderColor='#58a6ff';dj(%s)\""
               % (_SEL, "%s"))

        day = [x for x in parsed if now - x["ts"] <= _td(hours=24)]
        week = parsed

        # 块1 总览三卡（近24h / 近7天，点击下钻该方向新闻）
        def _card(label, n, color, arg):
            return ("<div data-dj='1' role='button' tabindex='0' style='%s;flex:1;background:#161b22;"
                    "border-radius:8px;padding:14px;text-align:center'%s>"
                    "<div style='font-size:26px;font-weight:700;color:%s'>%d"
                    "</div><div style='font-size:12px;color:#8b949e'>%s ▾</div>"
                    "</div>" % (_CLICK, _ON % arg, color, n, label))
        blk1 = ("<div style='font-size:12px;color:#8b949e;margin:14px 0 6px'>"
                "近 24h（%d 条已判）</div><div style='display:flex;gap:10px'>"
                + _card("利多", _cnt(day, "bull"), "#f85149",
                        "{'bias':'bull','since':24}")
                + _card("利空", _cnt(day, "bear"), "#3fb950",
                        "{'bias':'bear','since':24}")
                + _card("中性", _cnt(day, "neutral"), "#8b949e",
                        "{'bias':'neutral','since':24}")
                + "</div><div style='font-size:12px;color:#8b949e;margin:12px 0"
                  " 6px'>近 7 天（%d 条已判）</div><div style='display:flex;"
                  "gap:10px'>"
                + _card("利多", _cnt(week, "bull"), "#f85149",
                        "{'bias':'bull'}")
                + _card("利空", _cnt(week, "bear"), "#3fb950",
                        "{'bias':'bear'}")
                + _card("中性", _cnt(week, "neutral"), "#8b949e",
                        "{'bias':'neutral'}")
                + "</div>")

        # 块2 市场分组双向条（近7天：左利空红、右利多绿；整行点击下钻）
        scopes = {}
        for x in week:
            scopes.setdefault(x["scope"], []).append(x)
        mx = max((max(_cnt(v, "bull"), _cnt(v, "bear"))
                  for v in scopes.values()), default=1) or 1
        bar_rows = []
        for sc in ("cn", "us", "hk", "commodity", "crypto", "macro"):
            v = scopes.get(sc) or []
            if not v:
                continue
            nb, nr = _cnt(v, "bull"), _cnt(v, "bear")
            arg = "{'scope':'%s'}" % sc
            bar_rows.append(
                "<div data-dj='1' role='button' tabindex='0' style='%s;display:flex;align-items:center;"
                "margin:4px 0;border-radius:6px;padding:2px 4px'%s>"
                '<div style="width:5em;color:#c9d1d9;font-size:12px">%s ▾</div>'
                '<div style="flex:1;display:flex;background:#161b22;'
                'border-radius:4px;overflow:hidden">'
                '<div style="width:50%%;display:flex;justify-content:flex-end">'
                '<div style="height:14px;width:%.1f%%;background:#3fb950"></div>'
                '</div><div style="width:50%%"><div style="height:14px;'
                'width:%.1f%%;background:#f85149"></div></div></div>'
                '<div style="width:7em;text-align:right;font-size:12px">'
                '<span style="color:#3fb950">%d空</span> '
                '<span style="color:#f85149">%d多</span></div></div>'
                % (_CLICK, _ON % arg, _SC.get(sc, sc), nr / mx * 100,
                   nb / mx * 100, nr, nb))
        blk2 = ("<div style='font-size:13px;color:#c9d1d9;margin:18px 0 6px'>"
                "▍按市场分组（近7天 · 左空右多 · 点击下钻）</div>" + "".join(bar_rows)
                if bar_rows else "")

        # 块3 24h 净倾向时间轴（2h 桶：利多-利空）
        buckets = [0] * 12
        for x in day:
            h = int((now - x["ts"]).total_seconds() // 7200)
            if 0 <= h < 12:
                buckets[11 - h] += 1 if x["bias"] == "bull" else (
                    -1 if x["bias"] == "bear" else 0)
        peak = max((abs(v) for v in buckets), default=1) or 1
        cols = []
        for i, v in enumerate(buckets):
            hpx = abs(v) / peak * 34
            col = "#f85149" if v > 0 else ("#3fb950" if v < 0 else "#30363d")
            cols.append(
                "<div data-dj='1' role='button' tabindex='0' style='%s;flex:1;display:flex;flex-direction:"
                "column;align-items:center;gap:2px;border-radius:4px;"
                "padding:2px 0'%s>"
                "<div style='height:34px;display:flex;align-items:flex-end'>"
                "<div style='width:14px;height:%dpx;background:%s;"
                "border-radius:2px'></div></div>"
                "<div style='font-size:10px;color:%s'>%s</div></div>"
                % (_CLICK, _ON % "{'bucket':%d}" % i, max(hpx, 2), col, col,
                   ("+%d" % v) if v > 0 else (str(v) if v else "-")))
        labels = ["%d时" % ((now - _td(hours=(11 - i) * 2)).hour)
                  for i in range(12)]
        blk3 = ("<div style='font-size:13px;color:#c9d1d9;margin:18px 0 6px'>"
                "▍净倾向时间轴（近24h · 2h 桶，多-空）</div>"
                "<div style='display:flex;gap:3px;align-items:flex-end'>"
                + "".join(
                    "<div style='flex:1;text-align:center'>%s</div>" % c
                    for c in cols)
                + "</div><div style='display:flex;gap:3px'>"
                + "".join(
                    "<div style='flex:1;text-align:center;font-size:9px;"
                    "color:#58606a;overflow:hidden'>%s</div>" % lb
                    for lb in labels)
                + "</div>") if day else ""

        # 块5 置信度分布直方图（近7天，10% 分桶，竖条内多/空/中堆叠，点击下钻）
        cbuckets = {}  # (lo%) -> {"bull":n,"bear":n,"neutral":n}
        for x in week:
            pc = int(x["conf"] * 100)
            lo = min(max((pc // 10) * 10, 40), 90)
            cbuckets.setdefault(lo, {"bull": 0, "bear": 0, "neutral": 0})
            cbuckets[lo][x["bias"] if x["bias"] in cbuckets[lo] else
                          "neutral"] += 1
        cpeak = max((sum(v.values()) for v in cbuckets.values()), default=1) or 1
        hist_cols = []
        for lo in range(40, 100, 10):
            v = cbuckets.get(lo) or {"bull": 0, "bear": 0, "neutral": 0}
            tot = sum(v.values())
            segs = ""
            for b, col in (("neutral", "#8b949e"), ("bear", "#3fb950"),
                           ("bull", "#f85149")):  # 堆叠：中在下、空、多在上
                if v[b]:
                    segs += ("<div style='width:100%%;height:%.0f%%;"
                             "background:%s'></div>" % (v[b] / cpeak * 100, col))
            hist_cols.append(
                "<div id='h%d' data-dj='1' role='button' tabindex='0' style='%s;flex:1;display:flex;flex-direction:"
                "column;align-items:center;gap:2px;border-radius:4px;"
                "padding:2px 0'%s>"
                "<div style='height:60px;width:70%%;display:flex;"
                "flex-direction:column-reverse;justify-content:flex-start;"
                "align-items:stretch;border-radius:2px'>%s</div>"
                "<div style='font-size:10px;color:%s'>%s</div>"
                "<div style='font-size:9px;color:#58606a'>%d-%d%%</div></div>"
                % (lo, _CLICK, _ON % "{'cmin':%d,'cmax':%d}" % (lo, lo + 10),
                   segs, "#c9d1d9" if tot else "#30363d",
                   str(tot) if tot else "-", lo, lo + 9))
        blk5 = ("<div style='font-size:13px;color:#c9d1d9;margin:18px 0 6px'>"
                "▍置信度分布直方图（近7天 · 竖条内自下而上：中性/利空/利多 · "
                "点击下钻）</div>"
                "<div style='display:flex;gap:3px;align-items:flex-end'>%s</div>"
                % "".join(hist_cols)) if week else ""

        # 块4 高置信要闻榜（近7天 conf≥0.7，利空在前）
        def _wt(items, bias):
            sel = sorted((x for x in items
                          if x["bias"] == bias and x["conf"] >= 0.7),
                         key=lambda x: -x["conf"])[:5]
            return [("<div style='margin:4px 0;font-size:12.5px'>"
                     "<span style='color:%s;font-weight:700'>%s %d%%</span>"
                     "<span style='color:#58606a'> · %s</span> %s</div>"
                     % ("#3fb950" if bias == "bear" else "#f85149",
                        "利空" if bias == "bear" else "利多",
                        round(x["conf"] * 100),
                        _SC.get(x["scope"], x["scope"]),
                        _html.escape(x["title"][:40]))) for x in sel]
        hot = _wt(week, "bear") + _wt(week, "bull")
        blk4 = ("<div style='font-size:13px;color:#c9d1d9;margin:18px 0 6px'>"
                "▍高置信要闻（近7天 · 置信度≥70%）</div>" + "".join(hot)
                if hot else "<div style='color:#58606a;font-size:12px;"
                            "margin:18px 0'>（暂无置信度≥70%的判断）</div>")

        # 块6 研报 AI 观点（report_events.jsonl，本地 Ollama 语义抽取，
        # 已并入事件图谱与个股时衰共振净分）
        blk6 = ""
        try:
            _rp = []
            with open(os.path.join(QBT_DIR, "data", "semantica",
                                   "report_events.jsonl"),
                      encoding="utf-8") as _fo:
                for _ln in _fo:
                    try:
                        _e = json.loads(_ln)
                    except Exception:
                        continue
                    if _e.get("kind") == "report" and _e.get("bias"):
                        _rp.append(_e)
            _cut30 = (now - _td(days=30)).strftime("%Y-%m-%d")
            _rp = [x for x in _rp if (x.get("ts") or "")[:10] >= _cut30]
            _rp.sort(key=lambda x: (x.get("ts") or ""), reverse=True)
            rows6 = []
            for x in _rp[:8]:
                _b = x.get("bias")
                _code = ((x.get("stocks") or [""])[0] or "")[:6]
                rows6.append(
                    "<div style='margin:4px 0;font-size:12.5px'>"
                    "<span style='color:%s;font-weight:700'>%s %d%%</span>"
                    "<span style='color:#58606a'> · %s · %s</span> "
                    "<a href='/kg/stock/%s' style='color:#58a6ff'>%s</a>"
                    "</div>"
                    % ("#3fb950" if _b == "bear" else "#f85149",
                       "利空" if _b == "bear" else "利多",
                       round((x.get("conf") or 0) * 100),
                       x.get("tag") or "研报",
                       (x.get("ts") or "")[:10],
                       _code, _html.escape((x.get("text") or "")[:44])))
            if rows6:
                blk6 = ("<div style='font-size:13px;color:#c9d1d9;"
                        "margin:18px 0 6px'>▍研报 AI 观点（近30天 %d 条 · "
                        "本地 Ollama 语义抽取 · 已并入事件图谱共振净分 · "
                        "点击进个股全景）</div>" % len(_rp)) + "".join(rows6)
        except Exception:
            blk6 = ""

        head = ("⚖️ JEV 新闻判断 · %s（累计已判 %d 条 · 24h %d 条）"
                % (now.strftime("%m-%d %H:%M"), len(week), len(day)))
        nav = pill_nav("jev")
        # 下钻数据 + 明细面板（点击图表元素展示背后的具体新闻依据）
        dj_data = json.dumps([{
            "t": x["ts_s"], "h": int((now - x["ts"]).total_seconds() // 7200),
            "title": x["title"][:64], "bias": x["bias"], "bias_cn": x["bias_cn"],
            "conf": round(x["conf"] * 100), "scope": x["scope"],
            "scope_cn": x["scope_cn"]} for x in week], ensure_ascii=False)
        dj_panel = ("<div id='dj-panel' style='display:none;background:#161b22;"
                    "border:1px solid #58a6ff;border-radius:8px;padding:12px;"
                    "margin:14px 0'></div>")
        dj_js = (
            "<script>var DJ=__DATA__;\n"
            "function dj(f){var p=document.getElementById('dj-panel'),rows=[];\n"
            "for(var x of DJ){\n"
            " if(f.bias&&x.bias!=f.bias)continue;\n"
            " if(f.scope&&x.scope!=f.scope)continue;\n"
            " if(f.cmin!=null&&(x.conf<f.cmin||x.conf>=f.cmax))continue;\n"
            " if(f.bucket!=null){if(11-x.h!=f.bucket)continue}\n"
            " else if(f.since){if(x.h>=f.since/2)continue}\n"
            " rows.push(x)}\n"
            "var head='（该筛选下暂无新闻）';\n"
            "if(rows.length){head='下钻依据 · '+rows.length+' 条';rows.reverse()}\n"
            "var html='<div style=\"color:#8b949e;font-size:11px;"
            "margin-bottom:4px\">'+head+'</div>';\n"
            "for(var x of rows){var c=x.bias=='bull'?'#f85149':"
            "(x.bias=='bear'?'#3fb950':'#8b949e');\n"
            " html+='<div style=\"margin:6px 0;font-size:13px\">"
            "<span style=\"color:'+c+';font-weight:700\">'+x.bias_cn+' '"
            "+x.conf+'%</span><span style=\"color:#58606a\"> · '+x.scope_cn"
            "+' · '+x.t+'</span> '+x.title+'</div>'}\n"
            "p.style.display='block';p.innerHTML=html;"
            "p.scrollIntoView({block:'nearest'})}\n"
            "</script>").replace("__DATA__", dj_data)
        body = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,"
                "initial-scale=1'><title>JEV判断</title></head>"
                "<body style='background:#0d1117;color:#c9d1d9;"
                "font-family:Microsoft YaHei,Arial;margin:0;padding:20px'>"
                "<div style='max-width:760px;margin:0 auto'>"
                "<h2 style='font-size:18px'>%s</h2>%s%s%s%s%s%s%s%s"
                "<div style='color:#58606a;font-size:11px;margin-top:16px'>"
                "数据：news.db jev 列 · 研报观点 report_events.jsonl"
                " · JEV(TypeSafe) 结构化判断随速报累积"
                " · 点击图表元素下钻查看依据 · 不构成投资建议"
                "</div></div>%s</body></html>"
                % (_html.escape(head), nav, blk1, blk2, dj_panel, blk3,
                   blk5, blk4, blk6, dj_js)).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


    def _bt_brief(self):
        """AI 晨报/复盘直达页：news.db ai_briefs / ai_reviews 最新各一条。"""
        import sqlite3 as _sq
        import html as _html
        blocks = []
        try:
            db = _sq.connect(os.path.join(STATION, "news_collector",
                                           "news.db"))
            for tag, tbl in (("🤖 AI 晨报", "ai_briefs"),
                             ("📊 收盘复盘", "ai_reviews")):
                try:
                    r = db.execute(
                        "SELECT ts, model, ok, brief FROM %s "
                        "ORDER BY ts DESC LIMIT 1" % tbl).fetchone()
                except Exception:
                    r = None
                if r:
                    blocks.append(
                        "<h3 style='color:#8b949e;font-size:13px'>%s · %s ·"
                        " %s%s</h3><pre style='white-space:pre-wrap;"
                        "font-family:inherit;font-size:13px;color:#c9d1d9;"
                        "background:#161b22;padding:12px;border-radius:8px;"
                        "border:1px solid #21262d'>%s</pre>"
                        % (tag, _html.escape(r[0] or ""),
                           _html.escape(r[1] or "?"),
                           "" if r[2] else "（降级：模型未参与）",
                           _html.escape(r[3] or "")))
            db.close()
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "api_backtest.py", exc)
        body = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,"
                "initial-scale=1'><title>AI 晨报</title></head>"
                "<body style='background:#0d1117;color:#c9d1d9;"
                "font-family:Microsoft YaHei,Arial;margin:0;padding:20px'>"
                "<div style='max-width:760px;margin:0 auto'>"
                "<h2 style='font-size:18px'>🤖 AI 金融晨报 / 📊 收盘复盘</h2>%s%s"
                "<div style='color:#58606a;font-size:11px;margin-top:16px'>"
                "信息采集域 · 本地 Ollama 客观归纳 · 不构成投资建议"
                "</div></div></body></html>"
                % (pill_nav("brief"), "".join(blocks) or
                   "<div style='color:#8b949e'>暂无记录（每日 08:00 晨报 / "
                   "交易日 15:30 复盘后生成）</div>")).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


    def _bt_news(self, qs):
        """该股相关新闻页：?name=股名（近 7 天标题/正文命中，news.db）。"""
        import sqlite3 as _sq
        from urllib.parse import parse_qs as _pq, unquote as _uq
        import html as _html
        try:
            nm = _uq(_pq(qs).get("name", [""])[0]).strip()[:40]
        except Exception:
            nm = ""
        items = []
        err = ""
        if nm:
            try:
                since = time.strftime("%Y-%m-%d",
                                      time.localtime(time.time() - 7 * 86400))
                dbn = _sq.connect(os.path.join(
                    STATION, "news_collector", "news.db"))
                rows = dbn.execute(
                    "SELECT ts, title, substr(content,1,180) FROM news "
                    "WHERE (title LIKE ? OR content LIKE ?) AND ts >= ? "
                    "ORDER BY ts DESC LIMIT 40",
                    ("%" + nm + "%", "%" + nm + "%", since)).fetchall()
                dbn.close()
                items = rows
            except Exception as e:
                err = str(e)[:120]
        lis = "".join(
            '<div style="margin:10px 0;padding:10px 12px;'
            'background:#161b22;border:1px solid #21262d;border-radius:8px">'
            '<div style="color:#8b949e;font-size:11px">%s</div>'
            '<div style="color:#c9d1d9;font-size:14px;margin:4px 0">%s</div>'
            '<div style="color:#8b949e;font-size:12px">%s…</div></div>'
            % (_html.escape(r[0]), _html.escape(r[1]),
               _html.escape((r[2] or "").strip()))
            for r in items)
        head = ("📰 相关新闻 · %s（近 7 天 %d 条）" % (nm, len(items))) if nm \
            else "📰 相关新闻（缺少 ?name= 参数）"
        body = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,"
                "initial-scale=1'><title>相关新闻</title></head>"
                "<body style='background:#0d1117;color:#c9d1d9;"
                "font-family:Microsoft YaHei,Arial;margin:0;padding:20px'>"
                "<div style='max-width:760px;margin:0 auto'>"
                "<h2 style='font-size:18px'>%s</h2>%s%s"
                "<div style='color:#58606a;font-size:11px;margin-top:16px'>"
                "信息采集域 · news.db · 不构成投资建议</div></div></body>"
                "</html>" % (
                    _html.escape(head), lis,
                    ("<div style='color:#f85149'>%s</div>" % _html.escape(err))
                    if err else "")).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
    def _sa_result(self, jid):
        j = SA_JOBS.get(jid)
        if not j:
            self._json({"error": "job not found"}, 404)
            return
        self._json({"status": j["status"], "d": j.get("d")})
    def _sa_start(self, q):
        q = (q or "").strip()[:20]
        if not q:
            self._json({"error": "请输入股票"}, 400)
            return
        if SA_BUSY["v"]:
            self._json({"error": "正在分析上一只，请稍候"}, 429)
            return
        import threading
        import uuid
        jid = uuid.uuid4().hex[:12]
        SA_BUSY["v"] = True
        SA_JOBS[jid] = {"status": "running"}
        threading.Thread(target=_sa_worker, args=(jid, q),
                         daemon=True).start()
        self._json({"job": jid})
    def _weak_cases_page(self):
        """📚 弱于板块案例库：在案状态流摘要 + 案例档案 + 最新扫描（纯读，
        不触发任何重算/拉取；数据源 reports/weak_track_*.jsonl、
        weak_cases/*、weak_scan_*.csv，均 weak 产线落盘产物）。"""
        import html as _h
        import json as _j
        import csv as _csv
        import glob as _g
        from datetime import datetime as _dt
        e = _h.escape
        REP = os.path.join(STATION, "reports")
        WC = os.path.join(REP, "weak_cases")

        def _last(fp):
            last, n = None, 0
            try:
                with open(fp, encoding="utf-8") as f:
                    for ln in f:
                        ln = ln.strip()
                        if not ln:
                            continue
                        try:
                            last = _j.loads(ln.lstrip("\ufeff"))
                            n += 1
                        except Exception as exc:
                            logging.getLogger(__name__).warning("silent-except %s: %s", "api_backtest.py", exc)
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "api_backtest.py", exc)
            return last, n

        def _rev_md(c6):
            m = sorted(_g.glob(os.path.join(WC, "weak_review_%s_*.md" % c6)))
            return os.path.basename(m[-1]) if m else ""

        td = "border-collapse:collapse;font-size:13px"
        th = ("background:#161b22;border:1px solid #30363d;padding:4px 8px;"
              "text-align:left")
        tdd = "border:1px solid #30363d;padding:4px 8px"

        # ① 在案（活跃状态流）
        rows = []
        for fp in sorted(_g.glob(os.path.join(REP, "weak_track_*.jsonl"))):
            if ".closed_" in os.path.basename(fp):
                continue
            st, n = _last(fp)
            if not st:
                continue
            c6 = os.path.basename(fp)[len("weak_track_"):-len(".jsonl")]
            code = st.get("code", c6)
            ex = st.get("ex250")
            trig = st.get("trig") or []
            rev = _rev_md(c6)
            rows.append(
                "<tr><td style='%s'><b>%s</b></td><td style='%s'>%s · %s</td>"
                "<td style='%s'>%s</td><td style='%s'>%s</td>"
                "<td style='%s'>%s</td><td style='%s'>%d 次</td>"
                "<td style='%s'>%s%s</td></tr>" % (
                    tdd, e(code), tdd, e(str(st.get("px_d", "?"))[5:]),
                    st.get("px"), tdd,
                    ("%+.1fpp" % ex) if ex is not None else "—", tdd,
                    e("/".join(str(x).replace("（均线缠绕）", "·缠")[:8]
                               for x in (st.get("trend") or ["—", "—", "—"]))),
                    tdd,
                    ("<span style='color:#f85149'>⚠ %s</span>"
                     % e("；".join(trig))) if trig else "—", tdd, n, tdd,
                    ("<a href='/bt-weak-md?f=%s'>档案</a>" % e(rev))
                    if rev else "—",
                    " ｜ <a href='/bt-linkage?code=%s'>联动</a>" % e(code)))
        live_html = (
            "<table style='%s'><tr><th style='%s'>案例</th><th style='%s'>"
            "数据日·现价</th><th style='%s'>超额250</th><th style='%s'>趋势 "
            "个/板/沪</th><th style='%s'>触发</th><th style='%s'>运行</th>"
            "<th style='%s'>链接</th></tr>%s</table>" % (
                td, th, th, th, th, th, th, th,
                "".join(rows) or ("<tr><td style='%s' colspan='7'>无在案"
                                  "</td></tr>" % tdd))) if rows else \
            "<div style='color:#8b949e'>无在案案例</div>"

        # ② 已结案（closed 状态流 + close 报告）
        cl = sorted(_g.glob(os.path.join(REP, "weak_track_*.closed_*.jsonl")))
        cl_rows = []
        for fp in cl:
            base = os.path.basename(fp)
            c6 = base[len("weak_track_"):].split(".")[0]
            md = sorted(_g.glob(os.path.join(WC, "weak_close_%s_*.md" % c6)))
            cl_rows.append(
                "<li>%s — <a href='/bt-weak-md?f=%s'>结案报告</a></li>"
                % (e(c6), e(os.path.basename(md[-1]))) if md
                else "<li>%s</li>" % e(c6))
        closed_html = ("<ul style='font-size:13px'>%s</ul>"
                       % "".join(cl_rows)) if cl_rows else \
            "<div style='color:#8b949e'>尚无结案</div>"

        # ③ 最新扫描（重点候选 ★ 优先展示——二道筛 focus 列）
        scan_html = "<div style='color:#8b949e'>尚无扫描</div>"
        sc = sorted(_g.glob(os.path.join(REP, "weak_scan_*.csv")))
        if sc:
            fp = sc[-1]
            with open(fp, encoding="utf-8-sig") as f:
                hits = list(_csv.DictReader(f))
            n_focus = sum(1 for h in hits
                          if (h.get("focus") or "").strip())
            focus = [h for h in hits if (h.get("focus") or "").strip()]
            plain = [h for h in hits if not (h.get("focus") or "").strip()]

            def _fpri(foc):
                # 二道筛价值优先级：强转弱＞强失速＞深度垫底（与卡片一致）
                if "强转弱" in foc:
                    return 0
                if "强失速" in foc:
                    return 1
                return 2

            focus.sort(key=lambda h: (
                _fpri((h.get("focus") or "").strip()),
                float(h.get("ex") or 0)))   # 同级按超额最差
            top = (focus + plain)[:10]

            def _srow(h):
                foc = (h.get("focus") or "").strip()
                star = ("<span style='color:#e3a008'>★%s</span>"
                        % e(foc)) if foc else \
                       "<span style='color:#586e75'>—</span>"
                nm = e(h.get("name", ""))
                if foc.startswith("强转弱"):
                    nm = ("<span style='color:#f85149'>%s</span>" % nm)
                return ("<tr><td style='%s'>%s</td><td style='%s'>%s</td>"
                        "<td style='%s'>%s</td><td style='%s'>%s</td>"
                        "<td style='%s'>%.1f</td>"
                        "<td style='%s'>%.0f%%（%s）</td></tr>" % (
                            tdd, e(h.get("code", "")), tdd, nm,
                            tdd, e(h.get("industry", "")), tdd, star, tdd,
                            float(h.get("ex") or 0), tdd,
                            float(h.get("pct_in") or 0),
                            e(h.get("rank_in", ""))))

            thead = ("<tr><th style='%s'>代码</th><th style='%s'>名称</th>"
                     "<th style='%s'>行业</th><th style='%s'>重点</th>"
                     "<th style='%s'>超额pp</th>"
                     "<th style='%s'>板块内分位</th></tr>"
                     % (th, th, th, th, th, th))
            # 非重点参考小表（超额最差前 5）：主表被重点占满时保底可见
            plain_html = ""
            if plain and len(focus) >= 10:
                plain_html = (
                    "<div style='font-size:12px;color:#8b949e;margin:10px 0 "
                    "4px'>非重点参考（超额最差前 5）</div>"
                    "<table style='%s'>%s%s</table>" % (
                        td, thead, "".join(_srow(h) for h in plain[:5])))
            scan_html = (
                "<div style='font-size:12px;color:#8b949e;margin:4px 0'>"
                "%s ｜ 候选 %d 只 ｜ 重点 ★%d（二道筛）｜ %s</div>"
                "<table style='%s'>%s%s</table>"
                "<div style='font-size:12px;color:#8b949e'>重点候选优先（强"
                "转弱标红，余按超额最差补足），全量见 CSV</div>%s" % (
                    e(os.path.basename(fp)), len(hits), n_focus,
                    e(_dt.fromtimestamp(os.path.getmtime(fp)).strftime(
                        "%Y-%m-%d %H:%M")),
                    td, thead, "".join(_srow(h) for h in top), plain_html))

        self._html(
            # K-STOCK-052 界面修复：此前输出裸 HTML 片段（无 DOCTYPE/CSS 壳，
            # 浏览器渲染成白底裸内容=「还没有界面」）；补全页壳+域导航，
            # 与兄弟页（bt-qpzm/bt-9dim）同口径。
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,"
            "initial-scale=1'><title>📚 弱于板块案例库</title>"
            + self._COCKPIT_CSS + "</head><body>"
            + pill_nav("cases")
            + "<h1>📚 弱于板块案例库 <span style='font-size:14px;"
            "color:#8b949e'>weak 产线：扫描→建案→归因→跟踪→结案 · "
            "影子观察</span></h1>"
            "<h2 style='margin:14px 0 6px'>① 在案案例（每日 15:30 "
            "WeakTrackCheck 自动监测）</h2>%s"
            "<h2 style='margin:14px 0 6px'>② 已结案</h2>%s"
            "<h2 style='margin:14px 0 6px'>③ 最新信号2 扫描（每周一 15:35 "
            "WeakScan）</h2>%s"
            "<div style='color:#8b949e;font-size:12px;margin-top:16px;"
            "border-top:1px solid #30363d;padding-top:8px'>本页纯读展示，"
            "数据为产线落盘产物；仅影子复盘，不构成买卖依据。</div>"
            "</body></html>"
            % (live_html, closed_html, scan_html))
    # ── 策略集市 · 多策略共振看板（L1 发现层，全下钻） ──
    def _cross_page(self):
        import html as _h
        import sys as _sy
        e = _h.escape
        if QBT_DIR not in _sy.path:
            _sy.path.insert(0, QBT_DIR)
        try:
            import cross_poll
            d = cross_poll.build_cross()
        except Exception as ex:
            self._html("<body>%s<h1>🎯 多策略共振</h1>"
                       "<div class='dim'>聚合器异常：%s</div></body>"
                       % (pill_nav("cross"), e(str(ex))))
            return

        mkt_q = ""
        try:
            from urllib.parse import urlparse, parse_qs
            mkt_q = (parse_qs(urlparse(self.path).query)
                     .get("m", [""])[0]).upper()
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "api_backtest.py", exc)

        def _in_mkt(st):
            if not mkt_q:
                return True
            return str(st.get("market", "")).upper() == mkt_q

        def _hit_badges(st):
            sp = []
            for h in st["hits"]:
                dc = {"long": "#f85149", "short": "#3fb950",
                      "neutral": "#8b949e"}[h["dir"]]
                sp.append(
                    "<a href='%s' style='display:inline-block;margin:1px "
                    "3px 1px 0;padding:2px 8px;border-radius:10px;font-size:"
                    "12px;text-decoration:none;border:1px solid %s;color:%s' "
                    "title='下钻到 %s'>%s %s</a>"
                    % (e(h["url"]), dc, dc, e(h["label"]),
                       e(h["badge"]), e(h["label"])))
            return "".join(sp)

        def _stock_link(st):
            return ("<a href='/kg/stock/%s'><b>%s</b></a>"
                    "<div class='dim'>%s · %s</div>"
                    % (e(st["code"]), e(st["name"] or st["code"]),
                       e(st["code"]), e(st.get("market") or "")))

        stocks = [s for s in d["stocks"] if _in_mkt(s)]
        multi = [s for s in stocks if s["n_hit"] >= 2]
        mixed = [s for s in multi if s["state"] == "mixed"]
        longs = [s for s in multi
                 if s["state"] == "long" and s["n_long"] >= 2]
        shorts = [s for s in multi
                  if s["state"] == "short" and s["n_short"] >= 2]

        # ① 方向冲突榜（最高优先：持仓风险提示）
        tr_mixed = "".join(
            "<tr><td>%s</td><td>%s</td></tr>"
            % (_stock_link(s), _hit_badges(s)) for s in mixed) or (
            "<tr><td colspan='2' class='dim'>当前无多空分歧标的</td></tr>")
        blk_mixed = (
            "<details class='sec' open><summary><h2>⚠️ 方向冲突 · 多策略"
            "多空分歧（%d）</h2><span class='dim'>持仓重点排查</span>"
            "</summary><div class='sec-body'>"
            "<table class='sticky'><tr><th>标的</th>"
            "<th>各策略最新判断（点徽标下钻）</th></tr>%s</table>"
            "</div></details>" % (len(mixed), tr_mixed))

        # ② 共振做多榜
        tr_long = "".join(
            "<tr><td>%s</td><td><b style='color:#f85149'>%d 策略看多</b>"
            "</td><td>%s</td></tr>"
            % (_stock_link(s), s["n_long"], _hit_badges(s))
            for s in longs[:50]) or (
            "<tr><td colspan='3' class='dim'>当前无 ≥2 策略共振做多的标的"
            "</td></tr>")
        blk_long = (
            "<details class='sec' open><summary><h2>🔥 共振做多 · ≥2 策略"
            "同时看多（%d）</h2></summary><div class='sec-body'>"
            "<table class='sticky'><tr><th>标的</th><th>共振</th>"
            "<th>策略徽标（点徽标下钻）</th></tr>%s</table>"
            "</div></details>" % (len(longs), tr_long))

        # ③ 共振预警榜
        tr_short = "".join(
            "<tr><td>%s</td><td><b style='color:#3fb950'>%d 策略看空</b>"
            "</td><td>%s</td></tr>"
            % (_stock_link(s), s["n_short"], _hit_badges(s))
            for s in shorts[:30]) or (
            "<tr><td colspan='3' class='dim'>当前无 ≥2 策略共振看空的标的"
            "</td></tr>")
        blk_short = (
            "<details class='sec' open><summary><h2>🧊 共振预警 · ≥2 策略"
            "同时看空（%d）</h2></summary><div class='sec-body'>"
            "<table class='sticky'><tr><th>标的</th><th>共振</th>"
            "<th>策略徽标</th></tr>%s</table></div></details>"
            % (len(shorts), tr_short))

        # ④ 策略覆盖矩阵（点数字下钻到该交集明细）
        strats = d["strategies"]
        mtx = d["matrix"]
        mh = "".join("<th>%s</th>" % e(s["badge"]) for s in strats)
        mr = ""
        for a in strats:
            cells = ""
            for b in strats:
                n = mtx.get(a["id"], {}).get(b["id"], 0)
                if a["id"] == b["id"]:
                    cells += ("<td class='dim' style='text-align:center'>"
                              "%d</td>" % d["n_per"].get(a["id"], 0))
                elif n:
                    cells += ("<td style='text-align:center'>"
                              "<a href='/bt-cross?pair=%s,%s' "
                              "style='color:#58a6ff'><b>%d</b></a></td>"
                              % (a["id"], b["id"], n))
                else:
                    cells += "<td class='dim' style='text-align:center'>·</td>"
            mr += ("<tr><td><a href='%s' style='color:%s'>%s</a>"
                   "<div class='dim'>%s</div></td>%s</tr>"
                   % (a["url"], a["color"], e(a["name"]),
                      e(a["markets"]), cells))
        blk_mtx = (
            "<details class='sec'><summary><h2>📊 策略覆盖矩阵</h2>"
            "<span class='dim'>点击展开</span></summary><div class='sec-body'>"
            "<div class='dim' style='margin-bottom:6px'>对角线=该策略当前"
            "覆盖标的数 · 交叉格=两策略共同命中数（点击下钻看明细）· 矩阵"
            "随注册表自动扩展——新策略上架即多一行一列</div>"
            "<table><tr><th>策略</th>%s</tr>%s</table></div></details>"
            % (mh, mr))

        # ⑤ 两两交集明细（pair 下钻视图）
        blk_pair = ""
        try:
            from urllib.parse import urlparse as _up, parse_qs as _pq
            pair = (_pq(_up(self.path).query).get("pair", [""])[0])
        except Exception:
            pair = ""
        if pair and "," in pair:
            pa, pb = pair.split(",", 1)
            names = {s["id"]: s["name"] for s in strats}
            rows_p = "".join(
                "<tr><td>%s</td><td>%s</td></tr>"
                % (_stock_link(s), _hit_badges(s))
                for s in stocks
                if pa in {h["sid"] for h in s["hits"]}
                and pb in {h["sid"] for h in s["hits"]})
            blk_pair = (
                "<div class='sec'><h2>🔗 交集明细：%s × %s</h2>"
                "<table><tr><th>标的</th><th>命中明细</th></tr>%s</table>"
                "<div style='margin-top:8px'><a class='tp' href='/bt-cross'>"
                "← 返回全览</a></div></div>"
                % (e(names.get(pa, pa)), e(names.get(pb, pb)),
                   rows_p or "<tr><td colspan='2' class='dim'>无交集"
                   "</td></tr>"))

        # 市场筛选 tab
        mkts = sorted({str(s.get("market") or "").upper()
                       for s in d["stocks"] if s.get("market")})
        tabs = ("<a class='tp' href='/bt-cross'>%s全部</a>"
                % ("<b>" if not mkt_q else "")) + "".join(
            "<a class='tp' href='/bt-cross?m=%s'>%s%s%s</a>"
            % (e(m), "<b>" if mkt_q == m else "", e(m),
               "</b>" if mkt_q == m else "") for m in mkts)

        strat_cards = "".join(
            "<a class='gate' href='%s'><b style='color:%s'>%s"
            "<span style='float:right;font-size:12px;color:#8b949e'>%d 只"
            "</span></b><div class='dim' style='margin-top:6px'>覆盖 %s"
            "</div></a>"
            % (s["url"], s["color"], e(s["name"]),
               d["n_per"].get(s["id"], 0), e(s["markets"]))
            for s in strats)

        body = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,"
                "initial-scale=1'><title>多策略共振 · 策略集市</title>"
                + self._COCKPIT_CSS + "</head><body>"
                + pill_nav("cross")
                + "<h1>🎯 多策略共振 · 策略集市</h1><div class='dim'>"
                "跨策略聚合发现层 · 同一标的被几个策略同时选中一眼可见 · "
                "数据截至 %s · 展示层聚合，不改任何策略口径</div>"
                "<div class='sec'><h2>🧺 上架策略（%d 个 · 注册表驱动，"
                "新策略注册即上架）</h2><div class='gates'>%s</div></div>"
                "<div class='sec'><h2>🌍 市场筛选</h2>%s</div>"
                % (e(d["generated_at"]), len(strats), strat_cards, tabs)
                + "<div class='duo'>" + blk_mixed + blk_long + "</div>"
                + blk_short + blk_mtx + blk_pair
                + "<div class='dim' style='margin-top:20px'>口径：方向语义"
                "三分（看多/看空/中性）· 缠论取近 90 天最新信号 · 九维≥80 "
                "入榜（观察工具口径，非买入建议）· 四策略≥3/4 持仓记看多 · "
                "聚合为纯读数，不推送不构成投资建议</div></body></html>")
        self._html(body)
    # ── L2 策略工作台：九维量化选股（影子评分，纯门户不推送） ──
    def _nine_dim_page(self):
        import html as _h
        e = _h.escape
        snap = _read_json(os.path.join(QBT_DIR, "data",
                                       "nine_dim_scores.json")) or {}
        ev = _read_json(os.path.join(QBT_DIR, "data",
                                     "nine_dim_pool_eval_cache.json")) or {}
        rbt = _read_json(os.path.join(QBT_DIR, "data",
                                      "nine_dim_replay_eval_cache.json")) or {}
        hist = []
        hp = os.path.join(QBT_DIR, "data", "nine_dim_history.jsonl")
        if os.path.exists(hp):
            try:
                with open(hp, encoding="utf-8") as f:
                    hist = [json.loads(l) for l in f if l.strip()][-15:]
            except Exception:
                log_exc("九维页:history 读取 %s" % hp)  # C2批1
                hist = []
        rows = snap.get("rows", [])
        env = snap.get("env") or {}
        wd = snap.get("wdelta") or {}

        def _chip(on, lb):
            c = "#3fb950" if on else "#30363d"
            return ("<span style='display:inline-block;padding:3px 10px;"
                    "border-radius:12px;font-size:12px;margin:0 6px 6px 0;"
                    "border:1px solid %s;color:%s'>%s</span>"
                    % (c, c if on else "#8b949e", lb))

        # ① 市场环境与动态调权
        if env:
            v1 = env.get("idx_above_ma20")
            env_line = ("科创50 <span style='color:%s'>%s</span> 20日线"
                        "（20日 %.2f%%）· 20日波动率分位 %s（≤30 视作低波动"
                        "，VIX 降级口径）· 两市成交额 %s 亿（≥10000 亿 +3）"
                        % ("#f85149" if v1 else "#3fb950",
                           "站上" if v1 else "跌破",
                           env.get("idx_month_chg") or 0,
                           "—" if env.get("vol_pct") is None
                           else env["vol_pct"],
                           "{:,}".format(int(env.get("amount_yi") or 0))))
            wdt = ("当前调权：<b>%s</b>" % e(json.dumps(
                       {k: "%+d" % v for k, v in wd.items()},
                       ensure_ascii=False))) if wd else \
                "当前无档位触发 · 按标准权重评分"
            env_blk = ("<div>%s</div><div class='dim' style='margin-top:6px'>"
                       "%s</div><div style='margin-top:8px'>%s</div>"
                       % (env_line, wdt,
                          _chip(env.get("bull"), "牛市档：板块+3 情绪+2")
                          + _chip(env.get("high_vol"),
                                  "高波动档：大盘-5 质量+5 动量-4")
                          + _chip(env.get("report_season"),
                                  "财报期档：盈利+3 技术-3")))
        else:
            env_blk = "<div class='dim'>暂无快照</div>"

        # ② 口径说明
        spec = (
            "<div class='dim' style='font-size:13px;line-height:2.0'>"
            "一句话：<b style='color:#e6edf3'>两段式九因子评分 —— 本地粗筛"
            "（RPS50&gt;80 + 成交额&gt;2亿 + 量能/布林动能）→ 候选池补齐 "
            "API 因子 → 78 分制评分，≥75 且完整覆盖入<b style='color:"
            "#f85149'>拥挤警报池</b>（回避名单，跟踪验证负收益即警报"
            "有效）</b><br>"
            "分段语义（K33 重定位，2026-10-02）：≥85 强警报 alert2 · "
            "75–85 警报 alert · 60–75 中段 mid（无信号，60–65 低波期有 "
            "beta 倾向非 alpha）· &lt;60 低位 low。原 ≥90 买入池退役"
            "（BUY_TH=90 仅作参照展示）<br>"
            "九因子（V3 结构）：公司质量15 · 盈利4 · 大盘环境7 · "
            "板块协同10 · 机构行为15 · 市场情绪8 · 动量资金4 · 估值安全10"
            " · 技术结构5（=78，总分=100×已得分/可判权重）；"
            "三档动态调权可叠加（上方状态卡）<br>"
            "V3 修订（2026-09-27，P2 回放 1565 样本逐维诊断驱动）："
            "移除北向连3日净买（陆股通净买 2024-08 停发后 tushare 字段"
            "语义已变成交额恒正，判据失真送分）、两市额≥万亿（99.6% 送分）、"
            "EPS上调≥1次（2.5% 可得=最大稀释源）、明弱实强（4.3% 可得）、"
            "量≥2倍（80% 与粗筛共线）；惩罚收紧：质量恶化&gt;35%、"
            "行业RPS&lt;50（旧阈值 51%/86% 样本误扣）<br>"
            "sv 豁免（V3 新增）：体系性缺失项（现金流字段/政策新闻覆盖前/"
            "历史大宗/融资明细）记 None 时从 cov 分母同步剔除——「体系没"
            "这个维度」不等于「样本覆盖不足」；个股级缺失（如 EV 缺）仍压 cov<br>"
            "V2 修订（2026-09-27）：移除年度预测上修（极稀有伪判据）；"
            "EPS上调≥1次(8分)、融资周增≥5%、券商覆盖≥3篇；"
            "北向连3日净买保留（后经 V3 确认字段失真移除）<br>"
            "不可判语义：无权限/无数据项记 <b>None 不计分不扣分</b>，总分 ="
            " 100×已得分/可判权重（cov=可判权重占比，&lt;0.75 不出分）<br>"
            "降级口径：机构调研接口无权限→机构行为=龙虎榜10+大宗5；情绪因子"
            "=新闻量周环比+JEV多空占比+券商覆盖（股吧/雪球/百度指数无源）；"
            "VIX→科创50已实现波动率分位；超预期概率→业绩预告预增；"
            "EV/EBITDA→总市值/EBITDA近似；行业分组用 stock_basic 行业"
            "（分位在候选池内近似）<br>"
            "影子纪律：纯门户展示不推送 · 入池日收盘价基准跟踪 r5/r20 · "
            "跨除权（缺口&gt;0.5%）剔除 · n&lt;10 不构成结论</div>")

        # ③ 评分榜单
        flbl = {"quality": "质", "eps": "盈", "env": "环", "sector": "板",
                "inst": "机", "sentiment": "情", "momentum": "动",
                "val": "估", "tech": "技"}
        full_rows = [r for r in rows if r.get("full")]
        part_rows = [r for r in rows if not r.get("full")]
        n_show = 0

        _BC = {"alert2": "#f85149", "alert": "#d29922",
               "mid": "#e3b341", "low": "#8b949e"}
        _BL = {"alert2": "强警报", "alert": "警报",
               "mid": "中段", "low": "低位", "na": "—"}

        def _row(i, r, partial=False):
            nonlocal n_show
            n_show += 1
            sc = r.get("score")
            bd = r.get("band") or ("na" if sc is None else "mid")
            hot = bd in ("alert", "alert2")
            fb = " ".join(
                "<span style='color:%s'>%s%d/%d</span>"
                % ("#f85149" if d["earned"] >= d["max"] * 0.99 else
                   "#3fb950" if d["earned"] < 0 else "#8b949e",
                   flbl.get(d["id"], "?"), d["earned"], d["max"])
                for d in r.get("detail", []))
            return ("<tr%s><td class='dim'>%d</td>"
                    "<td><a href='/kg/stock/%s'>%s</a><div class='dim'>%s"
                    "</div></td><td class='dim'>%s</td><td>%.1f</td>"
                    "<td style='color:%s'>%+.2f%%</td>"
                    "<td><b>%s</b>%s</td>"
                    "<td style='color:%s;font-size:12px'>%s</td>"
                    "<td class='dim'>%.2f</td>"
                    "<td class='dim' style='font-size:12px;white-space:"
                    "nowrap'>%s</td></tr>"
                    % (" style='background:#1c2128'" if hot else "",
                       i, e(str(r.get("code", ""))),
                       e(str(r.get("name", ""))), e(str(r.get("code", ""))),
                       e(str(r.get("industry", ""))),
                       float(r.get("rps50") or 0),
                       "#f85149" if (r.get("pct_chg") or 0) >= 0
                       else "#3fb950", float(r.get("pct_chg") or 0),
                       ("%.1f" % sc) if sc is not None else "未完整",
                       " ⚠" if hot else "",
                       _BC.get(bd, "#8b949e"), _BL.get(bd, "—"),
                       float(r.get("cov") or 0), fb))
        rank_rows = ""
        for i, r in enumerate(full_rows, 1):
            if n_show >= 40:
                break
            rank_rows += _row(i, r)
        for i, r in enumerate(part_rows[:10],
                              len(full_rows) + 1):
            if n_show >= 50:
                break
            rank_rows += _row(i, r, partial=True)
        rank_blk = (
            "<div class='dim' style='margin-bottom:4px'>候选池 %d 只 · "
            "完整评分 %d · ≥%d 拥挤警报 %d（K33：高分=回避位） · 漏斗：%s"
            " · 数据日 %s</div>"
            "<table><tr><th>#</th><th>标的</th><th>行业</th><th>RPS50"
            "</th><th>涨跌</th><th>总分</th><th>分段</th><th>cov</th>"
            "<th>九维 得分/满分（质盈环板机情动估技）</th></tr>%s</table>"
            % (snap.get("n_cand", 0), snap.get("n_full", 0),
               snap.get("alert_th", 75),
               snap.get("n_alert", snap.get("n_buy", 0)),
               " → ".join("%s %d" % (k, v)
                          for k, v in (snap.get("funnel") or {}).items()),
               e(str(snap.get("trade_date", ""))), rank_rows
               or "<tr><td colspan='9' class='dim'>暂无快照，每日 06:40 "
                  "刷新（NineDimScan）</td></tr>"))
        if not rows:
            rank_blk = "<div class='dim'>暂无快照，每日 06:40 刷新" \
                "（NineDimScan）</div>"

        # ④ 入池战绩
        def _stat_card(lb, st):
            if not st:
                return ("<div class='gate g-off'><b>%s</b><div class='dim'>"
                        "暂无成熟样本</div></div>" % lb)
            grey = st["n"] < 10
            col = "#8b949e" if grey else ("#f85149" if st["avg"] > 0
                                          else "#3fb950")
            return ("<div class='gate'><b>%s<span style='float:right;"
                    "color:%s'>%+.2f%%</span></b>"
                    "<div class='dim' style='margin-top:4px'>n=%d%s · "
                    "胜率 %.1f%%</div></div>"
                    % (lb, col, st["avg"], st["n"],
                       "（灰显：n<10）" if grey else "", st["win"]))
        perf_blk = ""
        if ev:
            perf_blk = (
                "<div class='dim' style='margin-bottom:6px'>累计入警报池 %d 只"
                " · 生成于 %s</div><div class='gates'>%s%s%s%s</div>"
                "<div class='dim' style='margin-top:6px'>%s</div>"
                % (ev.get("n_pool", 0), e(str(ev.get("generated_at", ""))),
                   _stat_card("r5 全体", (ev.get("r5") or {}).get("all")),
                   _stat_card("r5 ≥95分", (ev.get("r5") or {}).get("ge95")),
                   _stat_card("r20 全体", (ev.get("r20") or {}).get("all")),
                   _stat_card("r20 ≥95分",
                              (ev.get("r20") or {}).get("ge95")),
                   e(str(ev.get("note", "")))))
        else:
            perf_blk = "<div class='dim'>暂无入池，战绩随每日任务积累</div>"

        # ④b P2 历史回放（周频锚点 as-of 重放 · 影子）
        if rbt:
            blbl = {"ge90": "≥90", "85_90": "85–90", "80_85": "80–85",
                    "70_80": "70–80", "lt70": "&lt;70"}

            def _cell(o, k):
                v = (o or {}).get(k)
                return "—" if v is None else str(v)

            def _bt_row(w, name):
                st = ((rbt.get(w) or {}).get(name) or {})
                raw, thin = st.get("raw"), st.get("thinned")
                grey = not raw or raw.get("n", 0) < 10
                col = "#8b949e" if grey else (
                    "#f85149" if (raw.get("avg") or 0) > 0 else "#3fb950")
                return ("<tr%s><td class='dim'>%s</td><td>%s</td>"
                        "<td style='color:%s'>%s</td><td>%s</td>"
                        "<td>%s</td><td style='color:%s'>%s</td>"
                        "<td>%s</td><td>%s</td><td class='dim'>%s</td>"
                        "<td class='dim'>%s</td></tr>"
                        % (" class='dim'" if grey else "", w.upper(),
                           blbl.get(name, name), col,
                           _cell(raw, "n"), _cell(raw, "win"),
                           _cell(raw, "avg"), col,
                           _cell(raw, "exc"), _cell(raw, "p5"),
                           _cell(raw, "min"),
                           _cell(thin, "n"), _cell(thin, "avg")))

            bt_rows = "".join(
                _bt_row(w, b) for w in ("r20", "r5")
                for b in ("ge90", "85_90", "80_85", "70_80", "lt70"))
            worst = (((rbt.get("r20") or {}).get("ge90") or {})
                     .get("raw") or {}).get("worst") or []
            wrows = "".join(
                "<tr><td class='dim'>%s</td><td>%s<div class='dim'>%s</div>"
                "</td><td style='color:#3fb950'>%+.2f%%</td></tr>"
                % (e(str(x.get("anchor", ""))), e(str(x.get("name", ""))),
                   e(str(x.get("code", ""))), float(x.get("r20") or 0))
                for x in worst)
            worst_blk = ("<table><tr><th>锚点日</th><th>标的</th>"
                         "<th>r20 最差</th></tr>%s</table>" % wrows) \
                if wrows else "<div class='dim'>暂无 ≥90 成熟样本" \
                    "（≥90 阈值强选择性，历史入池极少属预期）</div>"
            arows = ""
            for a in reversed(rbt.get("by_anchor") or []):
                arows += (
                    "<tr><td class='dim'>%s</td><td>%s</td>"
                    "<td class='dim'>%s</td><td class='dim'>%s</td>"
                    "<td>%s</td><td><b>%s</b></td><td>%s</td><td>%s</td></tr>"
                    % (e(str(a.get("dt", ""))), _cell(a, "n_cand"),
                       _cell(a, "amt_yi"), _cell(a, "vol_pct"),
                       "牛市" if a.get("bull") else "—",
                       _cell(a, "n_full"), _cell(a, "ge90"),
                       _cell(a, "ge85")))
            v3_note = (
                "<div style='margin-bottom:8px;padding:8px 10px;"
                "border:1px solid #f85149;border-radius:6px;"
                "background:rgba(248,81,73,.08)'>"
                "<b style='color:#f85149'>⚠ K33 已重定位（2026-10-02）"
                "——总分反用为拥挤警报</b>。依据 V3 复评（51 锚点 / "
                "5479 样本 / 去重 4202）：分数与前向收益<b>单调负相关"
                "</b>——r20 均值 &lt;70 分 -9.2% → 70–80 分 -14.9% → "
                "75+ 分 -27.9% → 85+ 分 -71.5%，胜率 25%→21%→9%→0%；"
                "85+ 左尾（p5 -85.9%）全部为强动量拥挤票（存储双雄崩盘"
                "前夜 86 分、金诚信 92 分追顶 r20 -34%）。高分构成 = "
                "动量+机构+情绪+资金拥挤代理，与图谱拥挤度特征（K27）"
                "同构。<b>现行规则：≥75 入警报池跟踪（预期负收益 = 警报"
                "有效），85+ 强警报；BUY_TH=90 退役仅作参照</b>。65–75 "
                "观察池方案经离线网格否决（2024 段 70% 胜率系 n=10 小"
                "样本；60–65 低波 63% 胜率经超额检验拆穿为 beta 非alpha）。</div>")
            bt_blk = (
                "%s<div class='dim' style='margin-bottom:6px'>回放锚点 %d 个"
                "（周频 as-of 截断重放，bar/财报/龙虎/北向全部锚点日可见）"
                " · 样本行 %d · 基准 %s · 生成于 %s</div>"
                "<table><tr><th>窗口</th><th>分桶</th><th>n</th><th>胜率%%"
                "</th><th>均值%%</th><th>超额%%</th><th>p5</th><th>最低</th>"
                "<th>去重n</th><th>去重均值</th></tr>%s</table>"
                "<div class='dim' style='margin-top:10px'>≥90 分桶 r20 "
                "最差 5 条（左尾观察）：</div>%s"
                "<div class='dim' style='margin-top:10px'>逐锚点"
                "（新→旧）：</div>"
                "<table><tr><th>锚点日</th><th>候选池</th><th>两市额(亿)"
                "</th><th>波动分位</th><th>环境</th><th>完整评分</th>"
                "<th>≥90</th><th>≥85</th></tr>%s</table>"
                "<div class='dim' style='margin-top:6px'>%s</div>"
                % (v3_note, len(rbt.get("by_anchor") or []),
                   rbt.get("n_rows", 0),
                   e(str(rbt.get("bench", ""))),
                   e(str(rbt.get("generated_at", ""))), bt_rows, worst_blk,
                   arows, e(str(rbt.get("note", "")))))
        else:
            bt_blk = "<div class='dim'>回放未运行或结果未生成" \
                "（quant_backtest/nine_dim_backtest.py）</div>"

        # ⑤ 历史入池（K33 起为警报池；旧 ≥90 买入池行无 pool_type）
        if hist:
            hrows = ""
            for r in reversed(hist):
                r5 = r.get("r5")
                r20 = r.get("r20")
                pt = r.get("pool_type") or "buy_legacy"
                pt_html = ("<span style='color:#f85149'>警报</span>"
                           if pt == "alert"
                           else "<span class='dim'>买入(旧)</span>")
                hrows += (
                    "<tr><td class='dim'>%s</td><td>%s<div class='dim'>%s"
                    "</div></td><td><b>%.1f</b></td><td>%s</td>"
                    "<td>%.2f</td>"
                    "<td style='color:%s'>%s</td><td style='color:%s'>%s"
                    "</td><td class='dim'>%s</td></tr>"
                    % (e(str(r.get("dt", ""))), e(str(r.get("name", ""))),
                       e(str(r.get("code", ""))),
                       float(r.get("score") or 0), pt_html,
                       float(r.get("entry_px") or 0),
                       "#f85149" if (r5 or 0) >= 0 else "#3fb950",
                       "—" if r5 is None else "%+.2f%%%s" % (
                           r5, "" if r.get("mature5") else "(盯市)"),
                       "#f85149" if (r20 or 0) >= 0 else "#3fb950",
                       "—" if r20 is None else "%+.2f%%%s" % (
                           r20, "" if r.get("mature20") else "(盯市)"),
                       "⚠除权剔除" if r.get("adj") else ""))
            hist_blk = ("<table><tr><th>入池日</th><th>标的</th><th>入池分"
                        "</th><th>池类型</th><th>入池价</th><th>r5</th>"
                        "<th>r20</th><th>备注</th></tr>%s</table>"
                        "<div class='dim' style='margin-top:6px'>K33 起 ≥75 "
                        "入<b style='color:#f85149'>警报池</b>（回避名单，"
                        "跟踪负收益=警报有效）；「买入(旧)」为退役 ≥90 池"
                        "历史行，仅留档</div>" % hrows)
        else:
            hist_blk = ("<div class='dim'>暂无入池记录（警报池 ≥75 且 cov"
                        "≥0.75 才入；75+ 为罕见拥挤位，随每日任务积累）"
                        "</div>")

        body = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,"
                "initial-scale=1'><title>九维选股 · 工作台</title>"
                + self._COCKPIT_CSS + "</head><body>"
                + pill_nav("9dim")
                + "<h1>🧮 九维量化选股 · 工作台</h1><div class='dim'>"
                "策略市场 · 9 因子 100 分制 · K33 重定位：≥75 拥挤警报池"
                "（回避位跟踪）· 影子观察不推送（快照 %s）</div>"
                % e(str(snap.get("generated_at", "—")))
                + "<details class='sec'><summary><h2>📖 模型说明书与口径</h2>"
                "<span class='dim'>点击展开</span></summary><div class='sec-body'>"
                + spec + "</div></details>"
                + "<details class='sec' open><summary><h2>🌐 市场环境与动态调权</h2>"
                "</summary><div class='sec-body'>" + env_blk + "</div></details>"
                + "<details class='sec' open><summary><h2>🎯 今日评分榜单（两段式候选池）</h2>"
                "</summary><div class='sec-body'>" + rank_blk + "</div></details>"
                + "<details class='sec'><summary><h2>📈 警报池战绩（影子验证）</h2>"
                "<span class='dim'>点击展开</span></summary><div class='sec-body'>"
                + perf_blk + "</div></details>"
                + "<details class='sec'><summary><h2>⏪ P2 历史回放（周频锚点重放 · 影子）</h2>"
                "<span class='dim'>点击展开</span></summary><div class='sec-body'>"
                + bt_blk + "</div></details>"
                + "<details class='sec'><summary><h2>🕘 历史入池（近 %d 条）</h2>"
                "<span class='dim'>点击展开</span></summary><div class='sec-body'>"
                % len(hist) + hist_blk + "</div></details>"
                + "<div class='dim' style='margin-top:20px'>纸面研究不构成"
                "投资建议 · 每日 06:40 NineDimScan 用前一交易日收盘数据"
                "评分 · K33 重定位后 ≥75 入警报池（回避位，跟踪负收益"
                "=警报有效）· P2 回放已验证（as-of 重放 51 锚点，调参"
                "需经确认）· P3 推送接入需警报池影子战绩达标后经确认"
                "启用</div>"
                "</body></html>")
        self._html(body)
    def _algo_market_page(self, qs=None):
        """🛒 算法商品市场：本地算法商品化货架。数据源
        portal/algo_market.json（注册表存源，只读渲染）+
        portal/licenses.json（卡密库）。未订阅=试看模式（静态样例
        真实输出形态）；输入卡密激活后本机解锁。"""
        import html as _h
        e = _h.escape
        toast = ""
        # do_GET 传的是原始 query 串（字符串），内部直调可能传 dict——
        # 统一归一成 parse_qs dict（K-STOCK-122 修：此前 qs 字符串直接
        # .get 在带 ?key=/?id= 的 HTTP 请求上 AttributeError 打死连接，
        # 087 的卡密激活走浏览器一直 502/断连，仅直调冒烟未暴露）
        from urllib.parse import parse_qs as _aqs
        if isinstance(qs, str):
            qs = _aqs(qs)
        qs = qs or {}
        if qs.get("key"):
            ok, msg = self._algo_activate(qs["key"][0])
            toast = ("<div style='border:1px solid %s;border-radius:10px;"
                     "padding:10px 14px;margin:10px 0;font-size:13.5px'>%s%s"
                     "</div>" % ("#3fb950" if ok else "#f85149",
                                 "✅ " if ok else "❌ ", e(msg)))

        mkt = _read_json(os.path.join(ROOT, "algo_market.json")) or {}
        algos = mkt.get("algos", [])
        n_unlocked = sum(1 for a in algos if self._license_state(
            a.get("id", "")))
        # K-STOCK-122 岗位→算法互链（反向）：algo id → 在岗使用该算法的
        # 岗位名列表（源 ai_market.json algos 外键）；?id= 锚点定位高亮
        aim = _read_json(os.path.join(ROOT, "ai_market.json")) or {}
        users = {}
        for g in aim.get("groups", []):
            for a in g.get("ais", []):
                for i in a.get("algos", []) or []:
                    users.setdefault(i, []).append(
                        a.get("name", a.get("id", "")))
        anchor = (qs.get("id") or [""])[0]
        anchor_ok = anchor and any(a.get("id") == anchor for a in algos)

        # ── P1 卡3：最近真实产出（统计面）────────────────────
        # 六款商品逐个接真实数据源，只读既有缓存/账本，不造第二本
        # 账；只展示数量/日期/状态等统计摘要，不展示任何明细信号/
        # 个股名单（明细面口径待拍板，不越权）。缺数据 → 灰
        # "暂无最近产出"，不假绿；日期缺失如实标"日期未知"。
        def _live_row(ok, txt, src):
            """统计面行：真投影（绿）或灰显（数据源不可达）。"""
            if ok and txt:
                return ("<div style='font-size:11.5px;margin-top:8px;"
                        "color:#3fb950'>📡 最近真实产出（统计面）：%s"
                        "</div>" % e(txt))
            return ("<div style='font-size:11.5px;margin-top:8px;"
                    "color:#8b949e'>📡 暂无最近产出（数据源：%s 不可达）"
                    "</div>" % e(src))

        def _live_quant_dual():
            # 与 /portal 工作台同源读法：portal/qd_scan_cache.json
            # （api_misc build_overview 同一文件），只取数量+时间戳。
            src = os.path.join(ROOT, "qd_scan_cache.json")
            try:
                qd = _read_json(src) or {}
                if not qd.get("ts"):
                    return _live_row(False, "", "portal/qd_scan_cache.json")
                txt = "%s · 扫描 %s 只 · 当前信号 %d · 近端信号 %d" % (
                    str(qd.get("ts")),
                    qd.get("scanned", "?"),
                    len(qd.get("signals_now") or []),
                    len(qd.get("signals_recent") or []))
                return _live_row(True, txt, "portal/qd_scan_cache.json")
            except Exception:
                log_exc("algo_live:quant_dual")
                return _live_row(False, "", "portal/qd_scan_cache.json")

        def _live_chanlun():
            # 缠论选股池：quant_backtest/data/chanlun_pool.jsonl
            # （sixring pool 环同源），只取最近批次 date+count。
            src = os.path.join(QBT_DIR, "data", "chanlun_pool.jsonl")
            try:
                last_d, n = "", 0
                with open(src, encoding="utf-8") as f:
                    for ln in f:
                        ln = ln.strip()
                        if not ln:
                            continue
                        try:
                            r = json.loads(ln)
                        except Exception:
                            continue
                        d = str(r.get("detect_date") or "")
                        if not d:
                            continue
                        if d > last_d:
                            last_d, n = d, 1
                        elif d == last_d:
                            n += 1
                if not last_d:
                    return _live_row(False, "",
                                     "quant_backtest/data/chanlun_pool.jsonl")
                txt = "最近批次 %s · 入池 %d 条" % (last_d, n)
                return _live_row(True, txt,
                                 "quant_backtest/data/chanlun_pool.jsonl")
            except Exception:
                log_exc("algo_live:chanlun")
                return _live_row(False, "",
                                 "quant_backtest/data/chanlun_pool.jsonl")

        def _live_riskguard():
            # RiskGuard 影子账本：portal/data/risk_feedback.jsonl
            # （risk_feedback_ledger.py LEDGER_PATH 同源），统计
            # 累计拦截数按 R1-R4 分档 + 最近日期。
            src = os.path.join(ROOT, "data", "risk_feedback.jsonl")
            try:
                cnt = {"R1": 0, "R2": 0, "R3": 0, "R4": 0}
                last_d = ""
                with open(src, encoding="utf-8") as f:
                    for ln in f:
                        ln = ln.strip()
                        if not ln:
                            continue
                        try:
                            r = json.loads(ln)
                        except Exception:
                            continue
                        if r.get("action") != "blocked":
                            continue
                        rule = str(r.get("rule") or "").split("-")[0]
                        if rule in cnt:
                            cnt[rule] += 1
                        d = str(r.get("date") or "")
                        if d > last_d:
                            last_d = d
                txt = ("累计拦截 %d（R1 %d · R2 %d · R3 %d · R4 %d）"
                       "· 最近 %s" % (sum(cnt.values()), cnt["R1"],
                                      cnt["R2"], cnt["R3"], cnt["R4"],
                                      last_d or "日期未知"))
                return _live_row(True, txt,
                                 "portal/data/risk_feedback.jsonl")
            except Exception:
                log_exc("algo_live:riskguard")
                return _live_row(False, "",
                                 "portal/data/risk_feedback.jsonl")

        def _live_jev():
            # JEV 消息面情绪标注：news_collector/news.db news 表
            # jev 列（api_common _pool_bear_news 同源读法），统计
            # 近7天已研判条数 + 多/空/中性分布。
            src = os.path.join(STATION, "news_collector", "news.db")
            try:
                import sqlite3 as _sq
                from datetime import datetime as _dt, timedelta as _td
                since = (_dt.now() - _td(days=7)).strftime("%Y-%m-%d %H:%M")
                db = _sq.connect(src)
                rows = db.execute(
                    "SELECT jev FROM news WHERE jev IS NOT NULL "
                    "AND created_at>=? LIMIT 20000", (since,)).fetchall()
                latest = db.execute(
                    "SELECT MAX(created_at) FROM news "
                    "WHERE jev IS NOT NULL").fetchone()[0]
                db.close()
                if not rows:
                    return _live_row(False, "", "news_collector/news.db")
                nb = ns = nn = 0
                for (jv,) in rows:
                    try:
                        b = (json.loads(jv) or {}).get("bias")
                    except Exception:
                        b = None
                    if b == "bull":
                        nb += 1
                    elif b == "bear":
                        ns += 1
                    elif b == "neutral":
                        nn += 1
                txt = ("近7天研判 %d 条 · 多 %d / 空 %d / 中性 %d · 最近 %s"
                       % (len(rows), nb, ns, nn, latest or "日期未知"))
                return _live_row(True, txt, "news_collector/news.db")
            except Exception:
                log_exc("algo_live:jev")
                return _live_row(False, "", "news_collector/news.db")

        def _live_tdx():
            # 回测引擎最近产出：quant_backtest/last_backtest.json
            # （runner.py 落盘、get_backtest 同源消费），只取时间戳
            # +策略/模式+标的数。BacktestEngine/T0Engine 具名产出
            # 未实证到，取回测域最近真实落盘为准。
            src = os.path.join(STATION, "quant_backtest",
                               "last_backtest.json")
            try:
                bt = _read_json(src) or {}
                if not bt.get("ts"):
                    return _live_row(False, "",
                                     "quant_backtest/last_backtest.json")
                txt = "%s · 策略 %s · 模式 %s · 标的 %d" % (
                    str(bt.get("ts")),
                    bt.get("strategy") or "未知策略",
                    bt.get("mode") or "未知模式",
                    len(bt.get("results") or []))
                return _live_row(True, txt,
                                 "quant_backtest/last_backtest.json")
            except Exception:
                log_exc("algo_live:tdx")
                return _live_row(False, "",
                                 "quant_backtest/last_backtest.json")

        def _live_score():
            # Score 多因子最近一批排名：quant_backtest/reports/
            # rps_latest.json（/fin 页同源消费），只取批次号+条数。
            src = os.path.join(REPORTS_DIR, "rps_latest.json")
            try:
                rps = _read_json(src) or {}
                if not rps.get("latest"):
                    return _live_row(False,
                                     "", "quant_backtest/reports/rps_latest.json")
                txt = "批次 %s · 评分个股 %d 条 · 生成于 %s" % (
                    rps.get("latest"), len(rps.get("res") or []),
                    str(rps.get("ts") or "日期未知"))
                return _live_row(True, txt,
                                 "quant_backtest/reports/rps_latest.json")
            except Exception:
                log_exc("algo_live:score")
                return _live_row(False,
                                 "", "quant_backtest/reports/rps_latest.json")

        _live = {
            "quant_dual": _live_quant_dual(),
            "chanlun_9dim": _live_chanlun(),
            "riskguard": _live_riskguard(),
            "jev_sentiment": _live_jev(),
            "tdx_backtest": _live_tdx(),
            "score_ranking": _live_score(),
        }

        def _algo_card(a):
            sid = a.get("id", "")
            unlocked = self._license_state(sid)
            if unlocked:
                badge = ("✅ 已解锁", "#3fb950")
            else:
                badge = ("👁 试看模式", "#d29922")
            hl = ("outline:2px solid #58a6ff;outline-offset:2px;"
                  if sid == anchor and anchor_ok else "")
            use_row = ("<div class='dim' style='font-size:11.5px;"
                       "margin-top:8px'>🤖 在岗使用：%s</div>"
                       % e("、".join(users.get(sid, [])))
                       if users.get(sid) else "")
            sm = a.get("sample", {}) or {}
            feats = "".join(
                "<span class='tp' style='cursor:default'>%s</span>"
                % e(f) for f in a.get("features", []))
            sub = a.get("subscription", {}) or {}
            sample_html = ""
            live_html = _live.get(sid, "")
            if sm:
                sample_html = (
                    "<details style='margin-top:10px'>"
                    "<summary style='cursor:pointer;font-size:13px;"
                    "color:#58a6ff'>👁 试看结果 · %s</summary>"
                    "<div class='dim' style='font-size:11.5px;margin:"
                    "6px 0 4px'>%s</div>"
                    "<pre style='background:#0d1117;border:1px solid "
                    "#30363d;border-radius:8px;padding:10px;font-size:"
                    "11.5px;overflow-x:auto;white-space:pre-wrap'>%s</pre>"
                    "</details>" % (e(sm.get("title", "样例输出")),
                                    e(sm.get("meta", "")),
                                    e(sm.get("body", ""))))
            cta = ("<a class='tp' href='%s'>进入 →</a>" % e(a.get("entry", "/"))
                   if unlocked else
                   "<span class='dim' style='font-size:12px'>订阅后开放"
                   "实时入口 · %s</span>" % e(sub.get("price_hint", "")))
            return ("<div id='algo-%s' style='border:1px solid %s;"
                    "border-radius:12px;"
                    "background:#161b22;padding:16px 18px;%s'>"
                    "<div style='display:flex;align-items:baseline;gap:8px'>"
                    "<span style='font-size:17px;font-weight:700'>%s %s"
                    "</span>"
                    "<span style='font-size:11px;color:#8b949e;border:1px "
                    "solid #30363d;border-radius:10px;padding:1px 8px'>%s"
                    "</span>"
                    "<span style='margin-left:auto;font-size:11.5px;color:"
                    "%s;border:1px solid %s;border-radius:12px;padding:"
                    "2px 10px'>%s</span></div>"
                    "<div class='dim' style='font-size:12.5px;margin:"
                    "8px 0 4px'>%s</div>"
                    "<div style='font-size:13px;margin-bottom:6px'>%s</div>"
                    "<div style='display:flex;gap:6px;flex-wrap:wrap'>%s"
                    "</div>"
                    "%s"
                    "%s"
                    "%s"
                    "<div style='font-size:12.5px;margin-top:10px'>%s</div>"
                    "</div>"
                    % (e(sid), badge[1], hl,
                       e(a.get("icon", "🧩")),
                       e(a.get("name", "")), e(a.get("tier", "")),
                       badge[1], badge[1], badge[0],
                       e(a.get("tagline", "")), e(a.get("desc", "")),
                       feats, sample_html, live_html, use_row, cta))
        scroll_js = ""
        if anchor_ok:
            scroll_js = ("<script>var t=document.getElementById("
                         "'algo-%s');if(t){t.scrollIntoView({behavior:"
                         "'smooth',block:'center'});}</script>" % e(anchor))
        grid = "".join(
            "<div style='display:grid;grid-template-columns:repeat("
            "auto-fit,minmax(320px,1fr));gap:12px'>%s</div>"
            % "".join(_algo_card(a) for a in algos))
        act_form = (
            "<div class='sec'><h2>🔑 卡密激活（本机解锁）</h2>"
            "<div class='dim' style='font-size:12.5px;margin-bottom:8px'>"
            "输入卡密后本机立即解锁对应商品 · 卡密由管理员发放"
            "（portal/licenses.json 存源）· 试看模式下可查看每款算法的"
            "真实输出样例</div>"
            "<form method='get' action='/algo' style='display:flex;"
            "gap:8px'>"
            "<input name='key' placeholder='如 LTSO-XXXX-XXXX' style='"
            "background:#0d1117;border:1px solid #30363d;color:#e6edf3;"
            "border-radius:8px;padding:8px 12px;font-size:14px;min-width:"
            "240px'>"
            "<button type='submit' style='background:#238636;color:white;"
            "border:none;border-radius:8px;padding:8px 18px;font-size:"
            "14px;cursor:pointer'>激活</button></form></div>")
        body = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,"
                "initial-scale=1'><title>🛒 算法商品市场</title>"
                + self._COCKPIT_CSS + "</head><body>"
                + pill_nav("algo")
                + "<h1>🛒 算法商品市场</h1><div class='dim'>"
                + e(mkt.get("tagline", ""))
                + " · 共 %d 款商品 · 已解锁 %d 款 · 每款可试看真实输出样例"
                "</div>" % (len(algos), n_unlocked)
                + toast + grid + act_form + scroll_js
                + "<div class='dim' style='margin-top:20px'>商品源 portal/"
                "algo_market.json · 卡密库 portal/licenses.json · 内测"
                "参考价以正式发卡为准 · 不构成投资建议</div></body></html>")
        self._html(body)
    def _algo_activate(self, key):
        """卡密激活：合法且未激活的 key 写入 activated_keys（本机解锁）。
        返回 (ok, msg)。"""
        import json as _json
        import time as _t
        fp = os.path.join(ROOT, "licenses.json")
        lic = _read_json(fp) or {}
        key = (key or "").strip().upper()
        if not key:
            return False, "请输入卡密"
        today = _t.strftime("%Y-%m-%d")
        rec = next((r for r in lic.get("licenses", [])
                    if str(r.get("key", "")).upper() == key), None)
        if rec is None:
            return False, "卡密不存在"
        if str(rec.get("expiry", "")) < today:
            return False, "卡密已过期"
        act = lic.setdefault("activated_keys", [])
        if key in act:
            return True, "该卡已激活过（本机已解锁）"
        act.append(key)
        try:
            with open(fp, "w", encoding="utf-8") as f:
                _json.dump(lic, f, ensure_ascii=False, indent=2)
        except Exception:
            return False, "写入卡密库失败（文件被占用？）"
        return True, "激活成功 · 本机已解锁对应商品"
    def _fin_page(self):
        import html as _h
        from urllib.parse import quote as _q
        e = _h.escape
        pre = _read_json(os.path.join(REPORTS_DIR,
                                      "premarket_latest.json"))
        snap = _read_json(os.path.join(REPORTS_DIR, "rps_latest.json"))
        qcfg = _read_json(os.path.join(STATION, "quant_backtest",
                                       "config.json"))
        rep = qcfg.get("report", {})

        # ── A 区 · 今日全景（默认展开，一屏）─────────────────
        # A1 查一只股票（表单 → /sa?q= 自动开跑）
        sa_blk = (
            "<form action='/sa' method='get' style='display:flex;gap:8px'>"
            "<input name='q' placeholder='股票名或代码：英伟达 / TSLA / "
            "600519 / 00700' maxlength='20' style='flex:1;padding:11px 14px;"
            "font-size:15px;border:1px solid #30363d;border-radius:10px;"
            "background:#161b22;color:#e6edf3;outline:none' autofocus>"
            "<button style='padding:11px 20px;font-size:14px;border:none;"
            "border-radius:10px;background:#238636;color:#fff;cursor:pointer;"
            "font-weight:600'>分析</button></form>"
            "<div class='dim' style='margin-top:6px'>行情 + 近7天 JEV 逐条"
            "判断 + 展望 · 约 1 分钟 · 结论推飞书</div>")

        # A2 今日市场一句话 → 升级为五维拼图（首屏可见结构）
        prob = pre.get("prob")
        vals = pre.get("vals", {})
        verdict = e(str(pre.get("verdict", "—")))
        prob_txt = ("%d%%" % prob) if prob is not None else "—"
        # 五维色块：正绿负红，分值即权重
        dim_colors = {"外围": "#58a6ff", "流动性": "#d2a8ff",
                      "政策": "#f778ba", "板块": "#ffa657", "情绪": "#3fb950"}
        dim_chips = "".join(
            "<span style='display:inline-flex;align-items:baseline;gap:3px;"
            "margin:3px 10px 3px 0;padding:4px 10px;border-radius:8px;"
            "background:#161b22;border:1px solid #30363d;font-size:12px'>"
            "<span style='color:%s'>%s</span>"
            "<b style='color:%s;font-size:13px'>%s</b></span>"
            % (dim_colors.get(k, "#8b949e"), e(str(k)),
               "#8b949e" if v is None else
               ("#f85149" if v >= 0 else "#3fb950"),
               "—" if v is None else "%+d" % int(v))
            for k, v in vals.items())
        # 板块结构：取正负最强各一
        l4 = pre.get("l4", [])
        if l4:
            best = max(l4, key=lambda x: x["score"])
            worst = min(l4, key=lambda x: x["score"])
            sector_note = ("<div style='margin-top:6px;font-size:11.5px;"
                          "color:#8b949e'>板块 <span style='color:#f85149'>%s +%d</span>"
                          " · <span style='color:#3fb950'>%s %+d</span></div>"
                          % (e(str(best.get("sector", ""))), best["score"],
                             e(str(worst.get("sector", ""))), worst["score"]))
        else:
            sector_note = ""
        # 流动性/外围摘要
        l1 = pre.get("l1", {})
        l3 = pre.get("l3", {})
        l1_note = ""
        if l1.get("score") is not None:
            l1_note = "外围 %+d" % l1["score"]
        l3_note = ""
        if l3.get("score") is not None:
            l3_note = "流动性 %+d" % l3["score"]
        fx = l1.get("fx", {})
        fx_note = ""
        if fx.get("val"):
            fx_note = " · 离岸人民币 %.4f (%s)" % (
                fx["val"], "升" if fx.get("pct", 0) > 0 else "贬")
        # 公告/研报摘要（TDX l6）
        l6 = pre.get("l6") or {}
        ann_note = ""
        an6 = l6.get("ann_stats")
        if an6 and an6.get("bear"):
            ann_note = " · ⚠ 利空公告 %d" % an6["bear"]
        elif an6:
            ann_note = " · 公告 %d" % an6["total"]
        rpt_note = ""
        rp6 = l6.get("rpt_stats")
        if rp6 and rp6.get("rated"):
            rpt_note = " · 研报 %d 篇（买入 %d）" % (rp6["rated"],
                                                      rp6["buy"])
        summary_line = ""
        if l1_note or l3_note:
            summary_line = ("<div style='margin-top:5px;font-size:11.5px;"
                           "color:#8b949e'>%s%s%s%s%s</div>"
                           % (l1_note, (" · " + l3_note) if l3_note else "",
                              fx_note, ann_note, rpt_note))
        pre_blk = (
            "<div style='font-size:18px;font-weight:700;margin:2px 0 4px'>"
            "%s <span style='font-size:13px;font-weight:400;color:#8b949e'>"
            "溢价 %s</span></div>"
            "<div style='margin:6px 0'>%s</div>%s%s"
            "<div style='margin-top:6px'><a href='/premarket'>盘前详情 →</a></div>"
            % (verdict, prob_txt, dim_chips, sector_note, summary_line))

        # A3 四市场闸门（今天能不能新建仓）
        # K-STOCK-403：三态渲染（放行/否决/缺维降权）
        # + 整条横幅容器。语义与个股信号分离：
        #   闸门答「今天能不能新建仓」，信号答「这只票买不买」。
        #   缺维（safe=None，feeder 无源）必须区别于否决——不误杀（api.py safe 三值语义）。
        gates = []
        any_off = any_na = False
        for ev in snap.get("envs", []):
            mk = _env_market_key(ev.get("label", ""))
            if not mk:
                continue
            raw = ev.get("safe")
            if raw is None:
                cls, state = "g-na", "— 缺维降权"
                any_na = True
            elif raw:
                cls, state = "g-on", "✅ 放行建仓"
            else:
                cls, state = "g-off", "⛔ 否决建仓"
                any_off = True
            gates.append(
                "<a class='gate %s' href='/bt/m/%s'><b>%s</b>"
                "<span class='g-state' style='float:right;font-size:14px;"
                "font-weight:700'>%s</span>"
                "<div style='margin-top:5px;font-size:12.5px;"
                "color:var(--text-dim,#8b949e)'>环境分%s</div></a>"
                % (cls, mk, MKT_LABEL[mk], state, e(str(ev.get("score", "—")))))
        if any_na and not any_off:
            bar_cls = "gb-na"
            verdict = "部分市场数据缺维（不误杀，可建仓但无闸门保护）"
        elif any_off:
            bar_cls = "gb-off"
            verdict = "存在否决市场 —— 今日<b>暂停新建仓</b>，已持仓不受影响"
        else:
            bar_cls = "gb-on"
            verdict = "全市场放行 —— 今日<b>允许新建仓</b>"
        gates_html = (
            "<div class='gatebar %s'><p class='gb-title'>"
            "🚦 市场闸门 · 今天能不能新建仓</p>"
            "<p class='gb-note'>%s<br>闸门只管「现在能不能新建仓」；"
            "下方个股信号是另一层判断，"
            "<b>两者不冲突</b>——"
            "闸门否决时，下方仍会显示观察名单"
            "（仅供研究，不构成建仓指令）。</p>"
            "<div class='gates'>%s</div></div>"
            % (bar_cls, verdict, "".join(gates))
            if gates else "<div class='dim'>暂无快照</div>")

        # A4 今日机会（共振"买入" Top5）
        res = snap.get("res", [])
        buys = sorted((r for r in res if r.get("action") == "买入"),
                      key=lambda r: -(r.get("score") or 0))[:5]
        opp_rows = "".join(
            "<tr><td><a href='/kg/stock/%s'>%s</a></td>"
            "<td><span class='dim'>%s</span></td>"
            "<td><b>%d</b></td></tr>"
            % (e(str(r.get("code", ""))), e(str(r.get("name", ""))),
               e(str(r.get("market", ""))), round(r.get("score") or 0))
            for r in buys)
        opp_blk = (
            "<div class='dim' style='margin-bottom:4px'>全池 %d 只 · "
            "<span style='color:#f85149'>买入 %d</span></div>"
            "<table><tr><th>标的</th><th>市场</th><th>共振分</th></tr>"
            "%s</table>"
            "<div style='margin-top:6px'><a href='/bt-qpzm'>全部 →</a></div>"
            % (len(res), len(buys), opp_rows or
               "<tr><td colspan='3' class='dim'>今日无买入信号</td></tr>")
            ) if res else "<div class='dim'>暂无快照</div>"

        # ── B 区 · 策略动态（注册表驱动，新增策略零改）────────
        cross = {}
        try:
            sys.path.insert(0, os.path.join(STATION, "quant_backtest"))
            import cross_poll as _CP
            cross = _CP.build_cross() or {}
        except Exception:
            log_exc("_fin 策略动态:cross_poll")  # C2批1
            cross = {}
        n_per = cross.get("n_per", {})
        stocks = cross.get("stocks", [])
        n_multi = sum(1 for st in stocks if st.get("n_hit", 0) >= 2)
        n_mixed = sum(1 for st in stocks if st.get("state") == "mixed")
        n_long = sum(1 for st in stocks if st.get("state") == "long")
        strat_reg = cross.get("strategies", [])
        dyn_cards = "".join(
            "<a class='sstat' href='%s'><div class='st'>%s %s</div>"
            "<div class='sv'>%d 只命中</div>"
            "<div class='su'>→ 进入工作台</div></a>"
            % (s["url"], e(str(s.get("icon") or "🧪")),
               e(str(s.get("name", s["id"]))), int(n_per.get(s["id"], 0)))
            for s in strat_reg)
        cross_card = (
            "<a class='sstat' href='/bt-cross'><div class='st'>🎯 多策略共振"
            "</div><div class='sv'><span class='up'>%d</span> 多策略 · "
            "<span class='dn'>%d</span> 冲突</div>"
            "<div class='su'>全多 %d · → 共振看板</div></a>"
            % (n_multi, n_mixed, n_long))
        base_strats = "".join(
            "<a class='tp' href='/bt/strategy/%s'>🧪 %s</a>" % (sn, lb)
            for sn, lb in [("trend_follow", "趋势跟随"),
                           ("ma_cross", "均线交叉"),
                           ("rsi_reversal", "RSI 反转"),
                           ("boll_breakout", "布林突破")])
        strat_dyn = (
            "<div class='gates'>%s%s</div>"
            "<div class='dim' style='margin-top:8px'>策略卡数与口径随 "
            "cross_poll.STRATEGIES 注册表自动扩展 · 基础策略纵切：%s</div>"
            % (dyn_cards, cross_card, base_strats))

        # ── C 区 · 折叠收纳（默认收起，首屏不露）────────────
        # C1 盘前五维明细（A2 已展示摘要，此处放完整分布与镜像链接）
        vals_detail = pre.get("vals", {})
        vchips = "".join(
            "<span style='display:inline-block;margin:3px 8px 3px 0;"
            "padding:3px 10px;border-radius:9px;background:#161b22;"
            "border:1px solid #30363d;font-size:12.5px'>%s <b "
            "style='color:%s'>%s</b></span>"
            % (e(str(k)),
               "#8b949e" if v is None else
               ("#f85149" if v >= 0 else "#3fb950"),
               "—" if v is None else "%+d" % int(v))
            for k, v in sorted(vals_detail.items(),
                               key=lambda x: -abs(x[1])
                               if x[1] is not None else 0)
        ) or "<span class='dim'>暂无盘前数据</span>"
        pre_detail = vchips + (
            "<div style='margin-top:8px'><a href='/premarket'>🖥 完整盘前"
            "分析 →</a>%s</div>"
            % (("　·　<a href='%s' target='_blank'>📄 飞书镜像</a>"
                % e(rep.get("premarket_doc_url")))
               if rep.get("premarket_doc_url") else ""))

        # C2 工具与横切
        tools = "".join(
            "<a class='tp' href='%s'>%s</a>" % (href, lb) for lb, href in [
                ("🚦 明日信号", "/bt-signals"),
            ("💼 持仓跟踪", "/bt-positions"),
            ("📄 回测报告", "/bt-report"),
            ("📊 寻优榜单", "/bt-opt"),
            ("🔥 板块热力", "/bt-sectors"),
            ("⚖️ JEV 消息面", "/bt-jev"),
            ("🤖 AI 晨报", "/bt-brief"),
            ("🎯 选股中心", "/bt"),
            ("📊 数据仓健康", "/bt-data-health"),
        ])
        markets = "".join(
            "<a class='tp' href='/bt/m/%s'>%s</a>" % (mk, lb)
            for mk, lb in [("cn", "🇨🇳 A股"), ("us", "🇺🇸 美股"),
                           ("hk", "🇭🇰 港股"), ("crypto", "🪙 加密")])

        # C3 最近分析
        hist = _sa_hist_read(6)
        hist_rows = "".join(
            "<tr><td><a href='/sa?q=%s'>%s</a></td><td>%s</td>"
            "<td style='color:%s'>%s</td><td class='dim'>%s</td></tr>"
            % (_q(h["q"]), e(str(h.get("name", ""))),
               e(str(h.get("px", "")).split("（")[0]),
               {"利多": "#f85149", "利空": "#3fb950",
                "分歧": "#d29922"}.get(h.get("bias"), "#8b949e"),
               e(str(h.get("bias", ""))), e(str(h.get("t", ""))))
            for h in hist if h.get("ok"))
        hist_blk = ("<table><tr><th>股票</th><th>现价</th><th>倾向</th>"
                    "<th>时间</th></tr>%s</table>" % hist_rows
                    if hist_rows else "<div class='dim'>还没有分析记录"
                    "，从上面输入第一只吧</div>")

        body = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,"
                "initial-scale=1'><title>金融驾驶舱</title>"
                + self._COCKPIT_CSS + "</head><body>"
                + pill_nav("fin")
                + "<h1>🏦 金融驾驶舱</h1><div class='dim'>今日全景 · "
                "策略动态 · 数据截至 %s</div>" % e(str(snap.get("latest", "")))
                # A 区：今日全景（一屏，默认展开）
                + "<div class='sec'><h2>🔍 分析一只股票</h2>" + sa_blk
                + "</div>"
                + gates_html
                + "<div class='duo'>"
                + "<div class='sec'><h2>🌅 今日市场一句话</h2>" + pre_blk
                + "</div>"
                + "<div class='sec'><h2>💡 今日机会 · 买什么</h2>" + opp_blk
                + "</div></div>"
                # B 区：策略动态（一屏，默认展开）
                + "<div class='sec'><h2>🧭 策略动态 · 各方法论今天在干嘛"
                "</h2>" + strat_dyn + "</div>"
                # C 区：折叠收纳（默认收起）
                + "<details class='sec'><summary><h2>🌅 盘前五维明细</h2>"
                "<span class='dim'>评分分布 · 飞书镜像</span></summary>"
                "<div class='sec-body'>" + pre_detail + "</div></details>"
                + "<details class='sec'><summary><h2>🧰 工具与横切</h2>"
                "<span class='dim'>执行跟踪 · 市场全景 · 分析报告</span>"
                "</summary><div class='sec-body'>"
                + "<div class='dim' style='margin:6px 0 4px'>执行与跟踪："
                "</div>" + tools
                + "<div class='dim' style='margin:10px 0 4px'>市场全景："
                "</div>" + markets + "</div></details>"
                + "<details class='sec'><summary><h2>🕘 最近分析</h2>"
                "<span class='dim'>点击继续看</span></summary>"
                "<div class='sec-body'>" + hist_blk + "</div></details>"
                + "<div class='dim' style='margin-top:20px'>纸面研究"
                "不构成投资建议 · 共振与闸门数据每日 17:35 刷新 · 盘前"
                "工作日 08:30</div></body></html>")
        self._html(body)
    def _strategies_page(self):
        """策略中心（K36）：注册表全景记分卡——策略即资产，
        状态/指标/入口一页看全 + 上新三步 SOP。"""
        import html as _h
        e = _h.escape
        d = strategies_data()
        items = d["strategies"]
        n_live = sum(1 for s in items if s.get("status") == "live")
        n_shadow = sum(1 for s in items if s.get("status") == "shadow")
        n_tool = sum(1 for s in items if s.get("status") == "tool")
        # 状态 → 语义色（tokens.css 语义层，不硬编码 hex）
        ST = {"live": ("实盘", "var(--color-ok)"),
              "shadow": ("影子", "var(--color-warn)"),
              "tool": ("支撑", "var(--link)"),
              "paused": ("暂停", "var(--text-dim)")}
        # 生命周期带：研发 → 影子验证 → 决策链 → 复盘退役
        band = "".join(
            "<span style='display:inline-flex;align-items:center;"
            "gap:6px;padding:5px 12px;border:1px solid var(--border);"
            "border-radius:999px;font-size:12px;background:var(--bg-raised)"
            "'><b style='color:%s'>%s</b>"
            "<span style='color:var(--text-dim)'>%s</span></span>"
            "<span style='color:var(--text-dim);padding:0 4px'>→</span>"
            % (c, e(t), e(n)) for t, c, n in [
                ("影子验证", "var(--color-warn)", "%d 条在验" % n_shadow),
                ("决策链", "var(--color-ok)", "%d 条在产" % n_live),
                ("复盘退役", "var(--text-dim)", "结案入库")])
        cards = []
        for s in items:
            st = ST.get(s.get("status"), ("?", "var(--text-dim)"))
            mets = "".join(
                "<div style='display:flex;justify-content:space-between;"
                "gap:10px;font-size:12px;padding:3px 0'>"
                "<span style='color:var(--text-dim);flex-shrink:0'>%s</span>"
                "<span style='text-align:right'>%s</span></div>"
                % (e(m.get("k", "")), e(str(m.get("v", ""))))
                for m in (s.get("metrics") or []))
            links = []
            if s.get("href"):
                links.append("<a href='%s' style='color:var(--link);"
                             "text-decoration:none;font-size:12.5px'>"
                             "门户入口 →</a>" % e(s["href"]))
            if s.get("ext"):
                links.append("<a href='%s' target='_blank' rel='noopener' "
                             "style='color:var(--link);text-decoration:none;"
                             "font-size:12.5px'>外部系统 ↗</a>" % e(s["ext"]))
            task = ("<span style='font-size:11px;color:var(--text-dim)"
                    ";font-family:Consolas,monospace'>任务 %s</span>"
                    % e(s["task"])) if s.get("task") else ""
            cards.append(
                "<div style='background:var(--bg-raised);border:1px solid "
                "var(--border);border-radius:12px;padding:16px 18px;"
                "display:flex;flex-direction:column;gap:6px'>"
                "<div style='display:flex;align-items:center;gap:8px'>"
                "<span style='font-size:10.5px;padding:1px 8px;"
                "border:1px solid var(--border);border-radius:999px;"
                "color:var(--text-dim)'>%s</span>"
                "<span style='font-size:10.5px;padding:1px 8px;"
                "border-radius:999px;color:%s;background:var(--bg-surface)"
                ";border:1px solid var(--border)'>● %s</span></div>"
                "<div style='font-size:15px;font-weight:700'>%s</div>"
                "<div style='font-size:12px;color:var(--text-dim)'>%s</div>"
                "<div style='font-size:11.5px;color:%s'>%s</div>"
                "<div style='margin-top:4px'>%s</div>"
                "<div style='display:flex;justify-content:space-between;"
                "align-items:baseline;margin-top:auto;padding-top:8px;"
                "border-top:1px dashed var(--border-weak)'>"
                "<span style='display:flex;gap:12px'>%s</span>%s</div></div>"
                % (e(s.get("badge", "")), st[1], st[0], e(s["name"]),
                   e(s.get("tagline", "")), st[1], e(s.get("status_note", "")),
                   mets, "".join(links), task))
        cards_by = {}
        for _s, _c in zip(items, cards):
            cards_by.setdefault(_s.get("status"), []).append(_c)
        _group_meta = [
            ("live", "✅ 实盘 · 在产", True),
            ("shadow", "🟡 影子验证 · 只标注不拦截", False),
            ("tool", "⚙️ 支撑工具", False),
            ("paused", "⏸ 暂停", False),
        ]
        groups_html = ""
        for _key, _title, _open in _group_meta:
            _cards = "".join(cards_by.get(_key, []))
            if not _cards:
                continue
            _count = len(cards_by.get(_key, []))
            _head = ("<div class='strat-group-head'><h3>%s</h3>"
                     "<span>%d 条</span></div>" % (_title, _count))
            if _open:
                groups_html += ("<section class='strat-group' data-status='%s'>%s"
                                "<div class='dw-grid'>%s</div></section>"
                                % (_key, _head, _cards))
            else:
                groups_html += ("<details class='strat-group' data-status='%s'>"
                                "<summary>%s<span class='strat-open'>展开</span></summary>"
                                "<div class='dw-grid'>%s</div></details>"
                                % (_key, _head, _cards))
        if not groups_html:
            groups_html = "<div class='empty'>暂无策略资产</div>"
        nav = pill_nav("strategies")
        # K87 锦标赛交叉表：策略来源 × 六本账累计贡献（sids 汇总，
        # 数据 reports/trader_sim_eval.json；列序与 trader_sim.BOOKS 同步）
        _ev = _read_json(os.path.join(REPORTS_DIR, "trader_sim_eval.json"))
        sids = (_ev or {}).get("sids") or {}
        tourney_html = ""
        if sids:
            _BK6 = ["激进|积极", "激进|保守", "保守|积极",
                    "保守|保守", "中立|积极", "中立|保守"]

            def _cell(v):
                if v is None:
                    return ("<td style='text-align:right;padding:5px 8px;"
                            "color:var(--text-dim)'>—</td>")
                return ("<td style='text-align:right;padding:5px 8px;"
                        "color:%s'>%+.2f</td>"
                        % ("var(--color-up)" if v >= 0 else "var(--color-dn)",
                           v))
            _th = "".join(
                "<th style='text-align:right;padding:4px 8px;font-weight:400'>"
                "%s</th>" % bk.replace("|", "·") for bk in _BK6)
            _trs = []
            for sid, s in sorted(
                    sids.items(),
                    key=lambda kv: kv[1].get("total") or 0, reverse=True):
                _tot = s.get("total") or 0
                _trs.append(
                    "<tr style='border-top:1px solid var(--border-weak)'>"
                    "<td style='padding:5px 8px'><b>%s</b>"
                    "<span style='color:var(--text-dim);font-size:11px'> %s"
                    "</span></td>"
                    "<td style='text-align:right;padding:5px 8px'>%d</td>%s"
                    "<td style='text-align:right;padding:5px 8px;"
                    "font-weight:700;color:%s'>%+.2f</td>"
                    "<td style='padding:5px 8px;color:var(--text-dim);"
                    "font-size:11.5px'>%s</td></tr>"
                    % (e(s.get("label") or sid), e(sid), s.get("n", 0),
                       "".join(_cell(s.get(bk)) for bk in _BK6),
                       "var(--color-up)" if _tot >= 0 else "var(--color-dn)",
                       _tot, e((s.get("best_bk") or "").replace("|", "·"))))
            tourney_html = (
                "<div style='background:var(--bg-raised);border:1px solid "
                "var(--border);border-radius:12px;padding:16px 18px;"
                "margin-top:22px'>"
                "<div style='font-size:14px;font-weight:700;margin-bottom:4px'>"
                "♠ 操盘锦标赛 · 策略来源 × 六本账</div>"
                "<div style='font-size:12px;color:var(--text-dim);"
                "margin-bottom:10px'>投委会立案票经三操盘手（激进/保守/"
                "中立）× 两盘算（积极/保守）六账推演；单元格=该策略立案票"
                "在对应账本的累计贡献%%（中式红涨绿跌）· 数据 %s</div>"
                "<div style='overflow-x:auto'><table style='border-collapse:"
                "collapse;width:100%%;font-size:12.5px'>"
                "<tr style='color:var(--text-dim);font-size:11.5px'>"
                "<th style='text-align:left;padding:4px 8px;font-weight:400'>"
                "策略来源</th><th style='text-align:right;padding:4px 8px;"
                "font-weight:400'>票数</th>%s"
                "<th style='text-align:right;padding:4px 8px;font-weight:400'>"
                "合计</th><th style='text-align:left;padding:4px 8px;"
                "font-weight:400'>最佳账</th></tr>%s</table></div></div>"
                % (e((_ev or {}).get("ts", "")), _th, "".join(_trs)))
        body = (
            "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>策略中心 · 零界</title>"
            "<link rel='stylesheet' href='/static/tokens.css'>"
            "<link rel='stylesheet' href='/static/deep_shell.css'></head>"
            "<body class='dw-page strategies-page'>"
            + nav + _HERMES_FAB_HTML +
            "<header class='dw-hero'><div class='dw-brand-block'>"
            "<div class='dw-brand'><div class='dw-logo'>策</div><div>"
            "<div class='dw-eyebrow'>STRATEGY ASSETS · 注册表驱动</div>"
            "<h1 class='dw-title'>策略资产中心 <span>K-STOCK · STRATEGY REGISTRY</span></h1>"
            "</div></div>"
            "<p class='dw-lead'>策略即资产：状态、指标、入口与生命周期一页看全；数据快照 %s。</p>"
            "<div class='dw-tags'><span class='dw-tag live'>注册表驱动</span>"
            "<span class='dw-tag'>%d 条策略</span><span class='dw-tag warn'>影子验证 %d</span>"
            "<span class='dw-tag'>实盘 %d</span></div></div>"
            "<div class='dw-hero-side'><div class='dw-kpis'>"
            "<div class='dw-kpi'><div class='k'>策略资产</div><div class='v'>%d</div></div>"
            "<div class='dw-kpi'><div class='k'>实盘</div><div class='v'>%d</div></div>"
            "<div class='dw-kpi'><div class='k'>影子验证</div><div class='v'>%d</div></div>"
            "<div class='dw-kpi'><div class='k'>支撑工具</div><div class='v'>%d</div></div>"
            "</div></div></header>"
            "<div class='dw-context'><div class='dw-crumb'>零界 <span>/</span> 量化 <span>/</span> <b>策略资产中心</b></div>"
            "<div class='dw-context-meta'><span class='dw-dot'></span> 注册表驱动 <span>·</span> 策略即资产</div></div>"
            "<div style='display:flex;align-items:center;flex-wrap:wrap;"
            "margin:14px 0 6px'>%s</div>"
            "<div style='color:var(--text-dim);font-size:12px;margin:4px 0 16px'>"
            "策略资产记分卡 · 状态与生命周期以注册表为准；"
            "每条策略都是一条产品线。</div>"
            "%s"
            "<details class='strat-sop'><summary>➕ 上新一条策略（三步，注册即上架）</summary>"
            "<div class='strat-sop-body'>"
            "<div style='font-size:12.5px;line-height:2;color:var(--text-dim)'>"
            "① <b style='color:var(--text)'>portal/strategies.json</b> 加一条："
            "id/名称/一句话价值/状态（live·shadow·tool·paused）/入口/记分卡要点"
            " —— 首页组合区、本页、导航自动同步，前端零改动<br>"
            "② 策略自身页面挂门户路由（portal_api.py 一行 + nginx.conf 一行）"
            "<br>③ 计划任务上线（照 deploy_weak_*.ps1 模板）→ 进 "
            "<a href='/sys-health' style='color:var(--link)'>系统自检</a>守护"
            "<br><span style='color:var(--text-dim)'>状态约定：影子=只标注不"
            "</div></details>"
            "<footer class='dw-footer'><div class='dw-footer-left'>"
            "<span class='dw-dot'></span><span>策略资产中心 · 注册表驱动 · 只读研究</span></div>"
            "<div>门户 8734 · <a href='/strategies'>↻ 刷新</a></div></footer>"
            "</body></html>"
            % (e(d["ts"]), len(items), n_shadow, n_live, len(items), n_live,
               n_shadow, n_tool, band, groups_html)
            + tourney_html)
        self._html(body)
    def _sa_page(self, q=""):
        # K-STOCK-ANALYSIS-P0F：/sa 升级为「个股全维分析」页（静态文件 + /api/analysis/* 流水线）
        import os as _os
        _p = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "stock_analysis.html")
        if _os.path.exists(_p):
            try:
                return self._html(open(_p, encoding="utf-8").read())
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "api_backtest.py", exc)
        import html as _h
        qe = _h.escape(q or "")
        hist = _sa_hist_read()
        hist_rows = "".join(
            "<tr><td style='padding:7px 10px;border-bottom:1px solid #21262d'>"
            "<a href='javascript:void(0)' onclick=\"go('%s')\" "
            "style='color:#58a6ff;text-decoration:none'>%s</a></td>"
            "<td style='padding:7px 10px;border-bottom:1px solid #21262d'>"
            "%s</td><td style='padding:7px 10px;border-bottom:1px solid "
            "#21262d;color:%s'>%s</td><td style='padding:7px 10px;"
            "border-bottom:1px solid #21262d;color:#8b949e'>%s</td></tr>" % (
                h["q"], h["name"],
                h["px"].split("（")[0],
                {"利多": "#f85149", "利空": "#3fb950",
                 "分歧": "#d29922"}.get(h["bias"], "#8b949e"),
                h["bias"], h["t"])
            for h in hist if h.get("ok"))
        body = """<!DOCTYPE html><html><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>个股分析</title><style>
body{background:#0d1117;color:#e6edf3;font-family:-apple-system,
'Segoe UI','Microsoft YaHei',sans-serif;max-width:720px;margin:0 auto;
padding:24px 16px}
input{width:100%%;box-sizing:border-box;padding:14px 16px;font-size:17px;
border:1px solid #30363d;border-radius:10px;background:#161b22;
color:#e6edf3;outline:none}
input:focus{border-color:#58a6ff}
button{margin-top:12px;width:100%%;padding:14px;font-size:16px;
border:none;border-radius:10px;background:#238636;color:#fff;
cursor:pointer;font-weight:600}
button:disabled{background:#21262d;color:#8b949e;cursor:wait}
#st{color:#8b949e;font-size:13.5px;margin-top:12px;min-height:20px}
.rc{border:1px solid #30363d;border-radius:12px;padding:16px;
margin-top:16px;background:#161b22;display:none}
.k{display:flex;gap:10px;flex-wrap:wrap;margin:10px 0}
.k div{flex:1;min-width:120px;border:1px solid #30363d;border-radius:9px;
padding:10px;text-align:center}
.k .l{color:#8b949e;font-size:12px}
.k .v{font-size:19px;font-weight:700;margin-top:3px}
h3{margin:16px 0 6px;font-size:14.5px;color:#e6edf3}
.small{font-size:12.5px;color:#8b949e;line-height:1.7}
.news{font-size:13px;color:#c9d1d9;line-height:1.8;
border-left:2px solid #30363d;padding-left:10px;margin:6px 0}
table{width:100%%;border-collapse:collapse;font-size:13.5px;margin-top:6px}
</style></head><body>
%s
<h2 style='font-size:20px'>🎯 个股分析</h2>
<p class='small'>输入任意股票 → 自动拉行情、近7天新闻，逐条 JEV 判断后给结论。<br>
支持中文名（英伟达）/ 美股（TSLA）/ A股（600519）/ 港股（00700）</p>
<input id='q' placeholder='输入股票名或代码，回车开始' maxlength='20' value='%s'>
<button id='go' onclick='go()'>开始分析</button>
<div id='st'></div>
<div class='rc' id='rc'></div>
<h3>🕘 最近分析（点击重新分析）</h3>
<table><tr style='color:#8b949e'><td style='padding:7px 10px'>股票</td>
<td style='padding:7px 10px'>现价</td><td style='padding:7px 10px'>倾向</td>
<td style='padding:7px 10px'>时间</td></tr>%s</table>
<p class='small' style='margin-top:18px'>约 1 分钟出结果（JEV 逐条判断 + AI 展望）·
结论同步推飞书卡片 · 纸面研究不构成投资建议</p>
<script>
function esc(s){return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;')}
var tmr=null;
function go(preset){
  var q=(preset||document.getElementById('q').value||'').trim();
  if(!q)return;
  document.getElementById('q').value=q;
  var b=document.getElementById('go');b.disabled=true;
  var st=document.getElementById('st'),rc=document.getElementById('rc');
  rc.style.display='none';
  var tips=['正在识别股票…','拉取实时行情…','检索近7天关联新闻…',
            'JEV 逐条判断中…','AI 生成展望…'];
  var i=0;st.textContent='⏳ '+tips[0];
  tmr=setInterval(function(){i=(i+1)%%tips.length;
    st.textContent='⏳ '+tips[i]},9000);
  fetch('/sa/start?q='+encodeURIComponent(q))
    .then(function(r){return r.json()})
    .then(function(j){
      if(j.error){st.textContent='❌ '+j.error;b.disabled=false;return}
      poll(j.job)});
  function poll(job){
    fetch('/sa/result?job='+job).then(function(r){return r.json()})
    .then(function(j){
      if(j.status!='done'){setTimeout(function(){poll(job)},3000);return}
      clearInterval(tmr);b.disabled=false;st.textContent='✅ 完成';
      if(!j.d.ok){st.textContent='❌ '+j.d.msg;return}
      var d=j.d,news=d.judged.map(function(x){
        return '<div class=news>['+esc(x.bias_cn)+' '+x.conf+'%%] '+
        esc(x.title)+'<br><span class=small>'+esc(x.reason)+'</span></div>'
      }).join('')||'<div class=small>（近7天无关联新闻）</div>';
      rc.innerHTML='<div class=k>'+
        '<div><div class=l>综合倾向</div><div class=v>'+esc(d.bias)+
        '</div></div><div><div class=l>信号强度</div><div class=v>'+
        (d.strength>0?'+':'')+d.strength+'</div></div><div>'+
        '<div class=l>关联新闻</div><div class=v>'+d.news_n+
        '</div></div></div>'+
        '<h3>行情 · '+esc(d.name)+'</h3><div class=small>'+esc(d.px)+
        '　|　'+esc(d.stat)+'</div>'+
        '<h3>未来展望（1-2 周）</h3><div class=news style=border-color:#238636>'+
        esc(d.outlook)+'</div><h3>新闻依据</h3>'+news;
      rc.style.display='block';rc.scrollIntoView({behavior:'smooth'});
      setTimeout(function(){location.reload()},60000);
    });
  }
}
document.getElementById('q').addEventListener('keydown',function(e){
  if(e.key==='Enter')go()});
var P=%s;
if(P){setTimeout(function(){go(P)},400)}
</script></body></html>""" % (pill_nav("sa"), qe, hist_rows, json.dumps(q))
        self._html(body)






# 2026-10-30 P68（拆分方案 B1）：minute 框架与 bt2 机制迁至 portal/backtest/。
from api_backtest_minute import (  # noqa: F401  再导出（拆分 B1）
    _CALENDAR_VERSION_PATTERN,
    _BT2_DIR,
    _BT2_STATE_DIR,
    _BT2_LOCK,
    _MINUTE_SOURCE_REGISTRY,
    _MINUTE_SOURCE_FACTORIES,
    _MINUTE_CALENDAR_FACTORIES,
    _MINUTE_FEE_FACTORIES,
    _MINUTE_FILL_MODELS,
    _MINUTE_MARKET_RULE_FACTORIES,
    _MINUTE_MARKET_POLICIES,
    _JOB_SPEC_CATALOG,
    _JOB_SPEC_REGISTRY,
    _calendar_error_code,
    _minute_error_code,
    _descriptor_failure,
    _minute_transaction_policy,
    _validate_minute_order_policy,
    _minute_execution_config,
    _minute_output_root,
    _safe_minute_artifact,
    _calendar_provider_for,
    _validate_runtime_calendar,
    _rehydrate_minute_source,
    _rehydrate_catalog_sources,
    register_minute_calendar_factory,
    register_minute_factory,
    register_minute_source,
)
from api_backtest_bt2 import (  # noqa: F401  再导出（拆分 B1）
    _bt2_state_path,
    _MY_STRAT_FILE,
    _MY_STRAT_KEYS,
    _my_strat_load,
    _my_strat_save,
    _api_my_strategies,
    _bt2_write_state,
    _bt2_start,
    _bt2_worker,
    _bt2_nl,
    _bt2_status,
    _MINUTE_ARTIFACT_MAX_BYTES,
    _read_minute_artifact,
    _minute_artifact_unavailable,
    _valid_minute_payload,
    _bt2_result,
)
