# -*- coding: utf-8 -*-
"""api_backtest 拆分 B6（2026-10-30 P71）：回测作业/状态辅助函数（custom/cbt/pulse/
sa_worker/hist/strategies/get_backtest）。

自 portal/api_backtest.py 纯移动（函数体零改动）；api_backtest 再导出兼容。
"""
import datetime
import json
import logging
import os
import re
import subprocess
import sys
import time
from api_common import _read_json, log_exc, STATION, REPORTS_DIR, QBT_DIR


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


_CBT_JOBS_JSONL = os.path.join(REPORTS_DIR, "cbt_jobs.jsonl")


_CBT_STATE_JSON = os.path.join(REPORTS_DIR, "cbt_queue_state.json")


_CBT_GATE = "2026-10-16"


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


STRATEGIES_FILE = os.path.join(STATION, "portal", "strategies.json")
