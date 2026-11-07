# -*- coding: utf-8 -*-
"""P4 批7-MISC：杂域合并 Mixin（qpzm/org/aim/vd/subscribe/feishu/intel/audit/devops 页族
+ news/alerts/overview/brief/backups/video/gzh 函数族 + 备份/镜像/hermes/短屏常量）。
Handler 壳经基座多继承并入（禁 import *）。
"""
import glob
import logging
import hmac
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from api_common import (
    _read_json,
    log_exc,
    STATION,
    REPORTS_DIR,
    QBT_DIR,
    _env_market_key,
    pill_nav,
    ROOT,
    _tabs_html,
    _back_link_html,
    run,
    MKT_LABEL,
    build_alerts,
    _PILL, _PILL_ON)

from api_misc_home import (  # noqa: F401  A5FROZEN：K-STOCK-167 驾驶舱数据面拆出
    _HOME_ORG_JSON, _HOME_PAPER_DB, _HOME_ROOT, _HOME_SHADOW,  # noqa: F401
    _home_cal, _home_money, _home_mt, _home_now_context, _home_org,  # noqa: F401
    _home_timeline, build_home_cockpit)  # noqa: F401

TASKS = ["AI工作站每日备份", "AI工作站告警转发", "JevFOCUS首期报告",
         "ZhiceWatchdog", "金融新闻采集", "金融新闻日报", "金融新闻AI晨报",
         "金融新闻收盘复盘", "自选股行情快照", "公众号选题撰文",
         "金融量化每日扫描", "ChanlunScan",
         "金融量化季度调参"]
PS_TASKS = (
    "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
    "$n=@(" + ",".join("'%s'" % t for t in TASKS) + ");"
    "Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue | ForEach-Object {"
    "$i = $_ | Get-ScheduledTaskInfo;"
    "[pscustomobject]@{name=$_.TaskName; state=[string]$_.State;"
    "last=[int]$i.LastTaskResult; lastrun=[string]$i.LastRunTime;"
    "next=[string]$i.NextRunTime} | ConvertTo-Json -Compress}"
)

from api_data import _news_db
from api_backtest import get_backtest
from api_screen import get_quotes

def get_disks():
    out = {}
    for letter in ("E:\\", "D:\\", "C:\\"):
        try:
            u = shutil.disk_usage(letter)
            out[letter[0]] = {"free_gb": round(u.free / 2 ** 30, 1),
                              "total_gb": round(u.total / 2 ** 30, 1)}
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "api_misc.py", exc)
    return out
def get_tasks():
    out = run(["powershell", "-NoProfile", "-Command", PS_TASKS], timeout=45)
    tasks = []
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                tasks.append(json.loads(line))
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "api_misc.py", exc)
    return tasks
def get_containers():
    out = run(["docker", "ps", "-a", "--format", "{{json .}}"], timeout=25)
    groups = {}
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            c = json.loads(line)
        except Exception:
            continue  # 豁免（C2批1）：docker ps 输出坏行跳过，正常容错
        name = c.get("Names", "")
        if name.startswith("quantdinger"):
            g = "QuantDinger"
        elif name.startswith("newquantsystem"):
            g = "NewQuantSystem v2"
        elif name.startswith("zhice"):
            g = "智策 OS"
        else:
            g = "其他服务"
        groups.setdefault(g, []).append((name, c.get("State", "")))
    res = {}
    for g, items in groups.items():
        running = sum(1 for _, st in items if st == "running")
        bad = [n for n, st in items if st != "running"]
        res[g] = {"running": running, "total": len(items), "exited": bad[:6]}
    return res
def tcp_ok(port, t=0.5):
    # 双栈探测：vite 等开发服务器可能只绑 IPv6 ::1
    for host in ("127.0.0.1", "::1"):
        try:
            s = socket.create_connection((host, port), timeout=t)
            s.close()
            return True
        except Exception:
            continue  # 豁免（C2批1）：双栈探测逐栈尝试，::1 失败切 127.0.0.1 属正常
    return False
JEV = os.path.join(STATION, "jev-eval")


# 055-L3 短屏化：长页锚点条+分区折叠（h2>=6 折叠、>=4 锚点；flex/grid 守卫）
_SS_TAG = '<script src="/static/shortscreen.js" defer></script>'
# K-STOCK-037 全站超级助手浮动图标（非 _COCKPIT_CSS 页的内联版）
_HERMES_UP_CACHE = (0.0, False)  # (ts, up) 30s TTL 探活缓存 K-STOCK-101
MIRROR = r"D:\backups_mirror"
BACKUPS = os.path.join(STATION, "backups")


def build_brief():
    """首页四板块卡的动态小字（轻量，直接读快照+news.db 计数）。"""
    pre = _read_json(os.path.join(REPORTS_DIR, "premarket_latest.json"))
    snap = _read_json(os.path.join(REPORTS_DIR, "rps_latest.json"))
    buy = [r for r in snap.get("res", [])
           if r.get("action") == "买入"]
    nw = _news_db()
    # G8 来源评级→门户红点：断流表数 / catalog 滞后在首页情报卡冒红/琥珀点
    # （纯文件读，与 /kg 折叠面板同一产物 kg_source_rating.json，单一数据源）
    rt = _read_json(os.path.join(QBT_DIR, "data",
                                 "kg_source_rating.json"))
    st = rt.get("staleness") or {}
    return {
        "ts": time.strftime("%Y-%m-%d %H:%M"),
        "fin": {"verdict": pre.get("verdict"), "prob": pre.get("prob"),
                "buy": len(buy), "latest": snap.get("latest", "")},
        "intel": {"today": nw["today"], "hit": nw["hit"], **nw["dist"]},
        "kg": {"stale": len(st.get("stale_tables") or []),
               "catalog_stale": bool(st.get("catalog_stale")),
               "catalog_age_days": st.get("catalog_age_days"),
               "ts": rt.get("ts")},
    }
def build_os_cockpit():
    """个人 OS 指挥大屏聚合（K-STOCK-260）：今天采集了什么 / Agent 在干什么 /
    产出多少量化交易 / 入库与分析了多少——四域一炮。
    全部轻量文件与 sqlite 读（无 docker/PS 子进程，毫秒级）；任务矩阵与容器
    由前端并行另拉 /api/overview，此刻/钱/组织/日历走 /api/home。"""
    today = time.strftime("%Y-%m-%d")
    out = {"ok": True, "ts": time.strftime("%Y-%m-%d %H:%M"), "today": today}

    # ── ① 采集：news.db 总量 + 今日 + 今日已判（与 /api/brief 同源独立读）──
    news = {"total": 0, "today": 0, "judged": 0}
    try:
        import sqlite3
        db = sqlite3.connect(os.path.join(STATION, "news_collector", "news.db"))
        news["total"] = db.execute("SELECT COUNT(*) FROM news").fetchone()[0]
        news["today"] = db.execute(
            "SELECT COUNT(*) FROM news WHERE "
            "created_at>=date('now','localtime')").fetchone()[0]
        news["judged"] = db.execute(
            "SELECT COUNT(*) FROM news WHERE jev IS NOT NULL AND "
            "created_at>=date('now','localtime')").fetchone()[0]
        db.close()
    except Exception:
        log_exc("build_os_cockpit:news.db 计数")  # 采集域缺库时前端亮未知点
    out["collect"] = news

    # ── ② Agent 动向：影子日报/点评 + 任务单审议产物 ──
    sh = os.path.join(STATION, "agent_platform", "data", "shadow")
    ksd = os.path.join(STATION, "k_stock", "data")
    daily = sorted(glob.glob(os.path.join(sh, "daily_report_*.md")))
    a0rev = sorted(glob.glob(os.path.join(sh, "a0_review_*.md")))
    plans = glob.glob(os.path.join(ksd, "plan_review", "*"))
    judges = glob.glob(os.path.join(ksd, "judge_reviews", "*"))

    def _today(p):
        try:
            return time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(p))) == today
        except OSError:
            return False
    out["agents"] = {
        "daily_report": os.path.basename(daily[-1]) if daily else None,
        "a0_review": os.path.basename(a0rev[-1]) if a0rev else None,
        "plan_total": len(plans),
        "plan_today": sum(1 for p in plans if _today(p)),
        "judge_total": len(judges),
        "judge_today": sum(1 for p in judges if _today(p)),
    }

    # ── ③ 量化产出：paper.db 订单/持仓 + 量子双刀扫描快照 ──
    paper = {"orders_total": 0, "orders_today": 0, "orders_yday": 0,
             "positions": 0, "ledger_today": 0}
    try:
        import sqlite3
        db = sqlite3.connect(os.path.join(ksd, "paper.db"))
        paper["orders_total"] = db.execute(
            "SELECT COUNT(*) FROM orders").fetchone()[0]
        paper["orders_today"] = db.execute(
            "SELECT COUNT(*) FROM orders WHERE date(ts)=?",
            (today,)).fetchone()[0]
        paper["orders_yday"] = db.execute(
            "SELECT COUNT(*) FROM orders WHERE date(ts)=date('now','-1 day')"
        ).fetchone()[0]
        paper["ledger_today"] = db.execute(
            "SELECT COUNT(*) FROM ledger WHERE date(ts)=?",
            (today,)).fetchone()[0]
        paper["positions"] = db.execute(
            "SELECT COUNT(*) FROM positions").fetchone()[0]
        db.close()
    except Exception:
        log_exc("build_os_cockpit:paper.db 计数")  # 交易域缺库时前端亮未知点
    out["paper"] = paper

    # ── ③b 个人模拟资产 · 分市场账户矩阵（K-STOCK-262）──
    # 账户即策略账户，市场维度走代码内映射（不改 paper.db schema）；
    # A股/美股/港股/虚拟币四板块常开，大宗 commodities 预留、开立即现。
    MARKETS = [("cn", "A股"), ("us", "美股"), ("hk", "港股"),
               ("crypto", "虚拟币"), ("commodities", "大宗")]
    ACCOUNT_MARKET = {
        "main": "cn", "taishan": "cn", "chaser": "cn",
        "hawk_test": "cn", "us_qingping": "us",        # 青萍美股
    }
    accounts = []
    try:
        import sqlite3
        db = sqlite3.connect(os.path.join(ksd, "paper.db"))
        db.row_factory = sqlite3.Row
        last_mark = db.execute(
            "SELECT account_id, MAX(date) d FROM equity_marks "
            "GROUP BY account_id")
        mark = {r["account_id"]: r["d"] for r in last_mark}
        for a in db.execute("SELECT * FROM account"):
            aid = a["account_id"]
            mk = mark.get(aid)
            eq = None
            if mk:
                eq = db.execute(
                    "SELECT total, cash, market_value, pos_pct "
                    "FROM equity_marks WHERE account_id=? AND date=?",
                    (aid, mk)).fetchone()
            npos = db.execute(
                "SELECT COUNT(*) FROM positions WHERE account_id=?",
                (aid,)).fetchone()[0]
            total = eq["total"] if eq else a["cash"]
            accounts.append({
                "id": aid, "market": ACCOUNT_MARKET.get(aid, "cn"),
                "initial": a["initial_cash"], "cash": a["cash"],
                "total": total, "mv": eq["market_value"] if eq else 0,
                "pos_pct": eq["pos_pct"] if eq else 0,
                "ret_pct": ((total - a["initial_cash"]) /
                            a["initial_cash"] * 100) if a["initial_cash"] else 0,
                "positions": npos, "as_of": mk,
            })
        db.close()
    except Exception:
        log_exc("build_os_cockpit:accounts 矩阵")  # 缺库时前端亮未知点
    by_market = {}
    for mkey, mname in MARKETS:
        rows = [x for x in accounts if x["market"] == mkey]
        by_market[mkey] = {
            "name": mname,
            "accounts": rows,
            "total": sum(x["total"] for x in rows),
            "initial": sum(x["initial"] for x in rows),
            "positions": sum(x["positions"] for x in rows),
            "opened": bool(rows),
        }
    out["accounts"] = {"markets": by_market,
                       "live_note": "实盘接入讯投QMT后在此并列真实账户"}
    qd = _read_json(os.path.join(ROOT, "qd_scan_cache.json")) or {}
    out["qd"] = {"ts": qd.get("ts"), "scanned": qd.get("scanned", 0),
                 "skipped": qd.get("skipped", 0),
                 "star_pool_n": qd.get("star_pool_n", 0),
                 "regime": qd.get("regime_now") or {}}
    strat = {"live": 0, "shadow": 0, "tool": 0, "total": 0}
    try:
        from api_backtest import strategies_data
        rows = (strategies_data() or {}).get("strategies") or []
        strat["total"] = len(rows)
        for s in rows:
            k = s.get("status")
            if k in strat:
                strat[k] += 1
    except Exception:
        log_exc("build_os_cockpit:strategies 计数")  # 策略注册表缺文件时降级
    out["strategies"] = strat

    # ── ④ 入库与分析：图谱源健康 + 回测报告库 ──
    rt = _read_json(os.path.join(QBT_DIR, "data", "kg_source_rating.json")) or {}
    st = rt.get("staleness") or {}
    rep = glob.glob(os.path.join(REPORTS_DIR, "*"))
    out["warehouse"] = {
        "kg_stale": len(st.get("stale_tables") or []),
        "catalog_age_days": st.get("catalog_age_days"),
        "reports_total": len(rep),
        "reports_today": sum(1 for p in rep if _today(p)),
    }

    # ── ⑤ 今日产出（给老田看的东西，K-STOCK-261 扩容）──
    pre = _read_json(os.path.join(REPORTS_DIR, "premarket_latest.json")) or {}
    ab = _read_json(os.path.join(STATION, "news_collector", "ai_brief.json")) or {}
    ar = _read_json(os.path.join(STATION, "news_collector", "ai_review.json")) or {}
    tps = _read_json(os.path.join(ROOT, "data", "todays_picks.json")) or {}
    pv = _read_json(os.path.join(ROOT, "data", "pick_verdicts.json")) or {}
    sl = _read_json(os.path.join(ROOT, "data", "strong_latest.json")) or {}
    gzh = _read_json(os.path.join(STATION, "gzh", "drafts", "index.json")) or []
    vids = _read_json(os.path.join(STATION, "video", "output", "index.json")) or []
    tpc = tps.get("counts") or {}
    out["output"] = {
        "brief": {"date": ab.get("date"), "ok": ab.get("ok"),
                  "pushed": ab.get("pushed"), "ts": ab.get("ts"),
                  "news_count": ab.get("news_count")},
        "premarket": {"verdict": pre.get("verdict"), "prob": pre.get("prob")},
        "ai_review": {"date": ar.get("date"), "ok": ar.get("ok")},
        "picks": {"total": tpc.get("total", 0),
                  "t1": tpc.get("T1_resonance", 0),
                  "t2": tpc.get("T2_high_value", 0),
                  "t3": tpc.get("T3_single", 0),
                  "generated_at": tps.get("generated_at")},
        "verdicts": len(pv.get("per_code") or {}),
        "strong": {"date": sl.get("date"), "items": len(sl.get("items") or [])},
        "gzh": len(gzh) if isinstance(gzh, list) else 0,
        "video": len(vids) if isinstance(vids, list) else 0,
    }

    # ── ⑥ AI 研发：git 提交即敲代码留痕（解析 .git/logs/HEAD reflog，
    #     零外部命令——服务会话无 git PATH，subprocess 方案已实测吃 0）──
    coding = {"commits_today": 0, "last_commit": None}
    try:
        today0 = time.mktime(time.strptime(today, "%Y-%m-%d"))
        n = 0
        last_ts = None
        with open(os.path.join(STATION, ".git", "logs", "HEAD"),
                  encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if "\t" not in line:
                    continue
                meta, msg = line.split("\t", 1)
                if not (msg.startswith("commit") or msg.startswith("merge")):
                    continue  # 豁免：reflog 里 reset/checkout 等非提交动作不计
                parts = meta.split()
                try:
                    ts = int(parts[-2])
                except (ValueError, IndexError):
                    continue  # 豁免：坏行跳过
                if ts >= today0:
                    n += 1
                    last_ts = ts
        coding["commits_today"] = n
        if last_ts:
            coding["last_commit"] = time.strftime("%m-%d %H:%M", time.localtime(last_ts))
    except Exception:
        log_exc("build_os_cockpit:reflog 提交计数")  # reflog 缺失时前端显示 0 笔
    out["coding"] = coding

    # ── ⑦ 待办提醒：健康亮牌 + 商务提醒 + 在研文稿（告警收件箱前端走 overview）──
    hp = _read_json(os.path.join(ROOT, "data", "health.json")) or {}
    bz = _read_json(os.path.join(ROOT, "data", "biz.json")) or {}
    nt = _read_json(os.path.join(ROOT, "data", "notes.json")) or {}
    remind = 0
    now = time.time()
    for m in bz.get("minutes") or []:
        ra = m.get("remind_at")
        if not ra:
            continue
        try:
            if time.mktime(time.strptime(str(ra)[:16], "%Y-%m-%d %H:%M")) > now:
                remind += 1
        except ValueError as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "api_misc.py", exc)
    out["todo"] = {
        "health": len(hp.get("issues") or []),
        "biz_remind": remind,
        "notes_doing": sum(1 for d in nt.get("docs") or []
                           if d.get("status") == "在研"),
    }

    # ── ⑧ 商务办公与个人整理（K-STOCK-267）：出行/会议/纪要/在研文稿 ──
    today_d = today
    near = sorted((bz.get("trips") or []) + (bz.get("meetings") or []),
                  key=lambda x: str(x.get("date") or ""))
    biz = {
        # 未来 7 天内的出行与会议（含今天）
        "near": [x for x in near
                 if str(x.get("date") or "") >= today_d
                 and str(x.get("date") or "") <= time.strftime(
                     "%Y-%m-%d", time.localtime(now + 7 * 86400))][:6],
        "trips_total": len(bz.get("trips") or []),
        "meetings_total": len(bz.get("meetings") or []),
        "minutes_total": len(bz.get("minutes") or []),
        "remind_next": min((str(m.get("remind_at"))[:16]
                            for m in bz.get("minutes") or [] if m.get("remind_at")
                            and str(m.get("remind_at")) >= time.strftime("%Y-%m-%d %H:%M", time.localtime(now))),
                           default=None),
        # 在研文稿明细（个人整理：报告/PPT/课件/公文）
        "notes": [d for d in (nt.get("docs") or [])
                  if d.get("status") == "在研"][:6],
        "notes_total": len(nt.get("docs") or []),
        # 学习域：智能体学院·缠论专业（263 数据链终审 + 269 组合制立项）
        "learn": {
            "ready": True,
            "academy": "智能体学院 · 缠论专业",
            "stage": "组合制新方案研究期（269 立项 10-13）",
            "wins": [
                "263 数据链终审：除权污染与伪信号已修，1 买前瞻 +10.71%（胜率 97.7%）",
                "269 七臂归因：单票择时全负贡献，再平衡 α +23.07%",
                "组合层 10 日再平衡 + regime 调仓：+15.37% / 回撤 -16.81%",
            ],
        },
    }
    out["biz"] = biz
    return out

def build_overview():
    services = [
        {"name": "智策页面(/quant/)", "port": 8734},
        {"name": "智策 API", "port": 8105},
        {"name": "卡片回调", "port": 9104},
        {"name": "Ollama", "port": 11434},
    ]
    for s in services:
        s["ok"] = tcp_ok(s["port"])
    return {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "containers": get_containers(),
        "tasks": get_tasks(),
        "backups": get_backups(),
        "alerts": get_alerts(),
        "disks": get_disks(),
        "services": services,
        "news": get_news(),
        "quotes": get_quotes(),
        "backtest": get_backtest(),
        "gzh": get_gzh(),
        "video": get_video(),
    }
def get_video():
    try:
        with open(os.path.join(STATION, "video", "output", "index.json"),
                  "r", encoding="utf-8") as f:
            items = json.load(f)
        return {"videos": items[:8]}
    except Exception:
        log_exc("get_video:video/output/index.json")  # C2批1
        return {"videos": []}
def get_gzh():
    try:
        with open(os.path.join(STATION, "gzh", "drafts", "index.json"),
                  "r", encoding="utf-8") as f:
            items = json.load(f)
        return {"drafts": items[:8]}
    except Exception:
        log_exc("get_gzh:gzh/drafts/index.json")  # C2批1
        return {"drafts": []}
def get_news():
    res = {"ok": None}
    try:
        p = os.path.join(STATION, "news_collector", "status.json")
        with open(p, "r", encoding="utf-8") as f:
            s = json.load(f)
        res = {"ok": bool(s.get("last_ok")),
               "last_collect": s.get("last_collect", ""),
               "news_24h": s.get("news_24h", 0),
               "news_total": s.get("news_total", 0),
               "push_last_run": s.get("push_last_run", 0),
               "sources": s.get("sources", {}),
               "updated": s.get("updated", "")}
    except Exception:
        log_exc("get_news:news_collector/status.json")  # C2批1
    try:
        with open(os.path.join(STATION, "news_collector", "ai_brief.json"),
                  "r", encoding="utf-8") as f:
            res["ai"] = json.load(f)
    except Exception:
        res["ai"] = None  # 豁免（C2批1）：AI 晨报未生成属常态，缺文件≠故障
    try:
        with open(os.path.join(STATION, "news_collector", "ai_review.json"),
                  "r", encoding="utf-8") as f:
            res["review"] = json.load(f)
    except Exception:
        res["review"] = None  # 豁免（C2批1）：AI 复盘未生成属常态
    return res
def get_alerts():
    res = {"pending": 0, "acks": [], "sent": 0}
    try:
        res["pending"] = len(glob.glob(os.path.join(JEV, "alerts", "*.flag")))
    except Exception:
        log_exc("get_alerts:alerts 目录 glob")  # C2批1
    p = os.path.join(JEV, "backup_card_acks.jsonl")
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            lines = [l for l in f if l.strip()]
        for l in lines[-6:]:
            try:
                j = json.loads(l)
                res["acks"].append({"ts": j.get("ts", ""),
                                    "id": j.get("alert_id", ""),
                                    "action": j.get("action", "")})
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "api_misc.py", exc)
    fl = os.path.join(JEV, "alerts", "forward.log")
    if os.path.exists(fl):
        with open(fl, "r", encoding="utf-8", errors="replace") as f:
            res["sent"] = sum(1 for l in f if "SENT" in l)
    return res
def get_backups():
    res = {"latest": None, "failed": None, "recent": [], "mirror": os.path.exists(MIRROR)}
    try:
        dirs = sorted((d for d in os.listdir(BACKUPS)
                       if os.path.isdir(os.path.join(BACKUPS, d))
                       and re.match(r"^\d{4}-\d{2}-\d{2}_", d)), reverse=True)
        res["recent"] = dirs[:3]
        if dirs:
            res["latest"] = dirs[0]
            log = os.path.join(BACKUPS, dirs[0], "backup.log")
            if os.path.exists(log):
                with open(log, "r", encoding="utf-8", errors="replace") as f:
                    lines = [l.rstrip() for l in f if l.strip()]
                done = [l for l in lines if "DONE" in l]
                if done:
                    res["failed"] = done[-1].split("failed=")[-1].strip()
    except Exception:
        log_exc("get_backups:备份目录读取")  # C2批1
    return res


# ══ Handler（类属性 + 方法，经 MRO 并入基座）══

class Handler:

    # K40 情报工场卡片板 CSS（与 L0 首页 .dm 同语言：图标+定位句+微指标+下钻）
    _INTEL_CARD_CSS = (
        "<style>"
        ".iboard{display:grid;grid-template-columns:"
        "repeat(auto-fill,minmax(300px,1fr));gap:14px;margin:16px 0 22px}"
        ".idm{display:flex;flex-direction:column;gap:8px;background:#161b22;"
        "border:1px solid #30363d;border-radius:14px;padding:18px 20px 15px;"
        "min-height:150px;text-decoration:none;color:#e6edf3;"
        "transition:border-color .2s}"
        "a.idm:hover{border-color:#58a6ff}"
        ".idm .ihead{display:flex;align-items:center;gap:12px}"
        ".idm .iic{width:42px;height:42px;border-radius:11px;"
        "border:1px solid #30363d;background:#0d1117;display:grid;"
        "place-items:center;font-size:21px;flex-shrink:0}"
        ".idm .it{font-size:15.5px;font-weight:700;display:flex;"
        "align-items:center}"
        ".idm .is{font-size:10.5px;color:#8b949e;letter-spacing:.5px;"
        "margin-top:2px}"
        ".idm .idesc{font-size:12px;color:#8b949e;line-height:1.65}"
        ".idm .ims{display:flex;flex-wrap:wrap;gap:5px 13px;"
        "font-size:11.5px;color:#8b949e;font-family:Consolas,"
        "'Microsoft YaHei',monospace}"
        ".idm .ims b{color:#e6edf3;font-weight:600}"
        ".idm .ifoot{display:flex;align-items:center;"
        "justify-content:space-between;margin-top:auto;padding-top:10px;"
        "border-top:1px dashed #21262d}"
        ".idm .ist{display:flex;align-items:center;gap:7px;"
        "font-size:11.5px;color:#8b949e}"
        ".idm .ienter{font-size:12px;color:#58a6ff;font-weight:600}"
        ".itag{font-size:10px;padding:1px 8px;border-radius:999px;"
        "border:1px dashed #30363d;color:#8b949e;font-weight:400;"
        "margin-left:7px}"
        ".idot{width:8px;height:8px;border-radius:50%;background:#3fb950;"
        "display:inline-block;flex-shrink:0}"
        ".idot.off{background:#f85149}"
        ".itools{display:flex;flex-wrap:wrap;gap:5px 12px;"
        "font-size:12.5px;line-height:1.9}"
        ".itools a{color:#58a6ff;text-decoration:none;"
        "border-bottom:1px dotted #30363d}"
        "</style>")
    _AUDIT_ZHIPU_MODEL = "glm-4.5-flash"
    _AUDIT_ZHIPU_BASE = "https://open.bigmodel.cn/api/paas/v4"
    _AUDIT_CFG_PATH = os.path.join(ROOT, "audit_ai_config.json")
    # ── K-STOCK-114e 独立安全审计 AI ─────────────────────────────
    _AUDIT_SYS = (
        "你是零界平台的独立安全审计官，与开发团队完全独立、只对安全负责。"
        "审计对象：代码 diff、实现方案、接口设计。"
        "审计口径：注入/XSS/越权/密钥泄露/路径穿越/竞态/资源泄漏/逻辑后门/"
        "数据校验缺失/危险调用。输出纪律："
        "①先给结论（安全/有风险/高危）②按严重度列问题：位置+成因+利用场景+修复建议 "
        "③没有问题就明说没有，不凑数④简洁，不重复代码。称呼对方为「开发侧」。")

    def _vd_list(self):
        """短视频成片库页：video/output（内嵌 video 预览 + 字幕/脚本）。"""
        import html as _html
        from urllib.parse import quote as _q
        items = []
        try:
            with open(os.path.join(STATION, "video", "output", "index.json"),
                      encoding="utf-8") as f:
                items = json.load(f)
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "api_misc.py", exc)
        secs = []
        for e in items:
            d = _q(e.get("dir", ""))
            secs.append("""
<div style='background:#161b22;border:1px solid #21262d;border-radius:10px;
padding:14px;margin:12px 0'>
<div style='display:flex;justify-content:space-between;flex-wrap:wrap;gap:6px'>
<b style='font-size:14px'>%s</b>
<span style='color:#8b949e;font-size:12px'>%s · %s 秒 · %s 场景 · %s MB</span></div>
<video controls preload='metadata' src='/vd/file/%s/video.mp4'
style='width:100%%;max-width:640px;margin-top:10px;border-radius:8px;
background:#000'></video>
<div style='margin-top:8px;font-size:12px'>
<a href='/vd/file/%s/subs.srt' style='color:#58a6ff;text-decoration:none'>📝 字幕 srt</a>
<span style='color:#30363d'> │ </span>
<a href='/vd/file/%s/script.json' style='color:#58a6ff;text-decoration:none'>📜 脚本 json</a>
<span style='color:#30363d'> │ </span>
<a href='/vd/file/%s/video.mp4' style='color:#58a6ff;text-decoration:none'>⬇ 下载 mp4</a>
</div></div>""" % (_html.escape(e.get("title", e.get("id", ""))),
                    _html.escape(str(e.get("ts", ""))),
                    _html.escape(str(e.get("duration", "?"))),
                    _html.escape(str(e.get("scenes", "?"))),
                    _html.escape(str(e.get("size_mb", "?"))), d, d, d, d))
        body = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,"
                "initial-scale=1'><title>短视频成片库</title>"
                "<link rel='stylesheet' href='/static/tokens.css'></head>"
                "<body style='background:#0d1117;color:#c9d1d9;"
                "font-family:Microsoft YaHei,Arial;margin:0;padding:20px'>"
                "<div style='max-width:860px;margin:0 auto'>%s"
                "<h2 style='font-size:18px'>🎬 短视频成片库"
                "<span style='color:#58606a;font-size:12px'>（全自动成片 · "
                "浏览器内直接预览）</span></h2>%s"
                "<div style='color:#58606a;font-size:11px;margin-top:16px'>"
                "内容生产域 · pyttsx3 配音 + ffmpeg 合成</div>"
                "</div></body></html>" % (pill_nav("vd"),
                "".join(secs) or
                "<div style='color:#8b949e'>暂无成片</div>")).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def _gzh_list(self):
        """公众号草稿库页：gzh/drafts（列表 + 全文预览链接）。"""
        import html as _html
        items = []
        try:
            with open(os.path.join(STATION, "gzh", "drafts", "index.json"),
                      encoding="utf-8") as f:
                items = json.load(f)
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "api_misc.py", exc)
        ST = {"draft": ("草稿", "#9db1d4"), "reviewed": ("已审", "#e6a23c"),
              "published": ("已发", "#f85149")}
        from urllib.parse import quote as _q
        rows = "".join(
            "<tr><td><a href='/gzh/view/%s' style='color:#58a6ff;"
            "text-decoration:none'>%s</a></td><td>%s</td><td>%s</td>"
            "<td>%s</td><td style='color:%s'>%s</td></tr>" % (
                _q(e.get("md", "") + ".preview.html"),
                _html.escape(e.get("title", e.get("id", ""))),
                _html.escape(e.get("col", "")),
                _html.escape(str(e.get("date", ""))),
                _html.escape(str(e.get("ts", ""))),
                ST.get(e.get("status"), ("?", "#9db1d4"))[1],
                ST.get(e.get("status"), ("?", "#9db1d4"))[0])
            for e in items) or "<tr><td colspan=5>—</td></tr>"
        body = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,"
                "initial-scale=1'><title>公众号草稿库</title>"
                "<link rel='stylesheet' href='/static/tokens.css'></head>"
                "<body style='background:#0d1117;color:#c9d1d9;"
                "font-family:Microsoft YaHei,Arial;margin:0;padding:20px'>"
                "<div style='max-width:860px;margin:0 auto'>%s"
                "<h2 style='font-size:18px'>📚 公众号草稿库"
                "<span style='color:#58606a;font-size:12px'>（每日 09:30 "
                "选题成文 · 点击标题阅读全文）</span></h2>"
                "<table style='border-collapse:collapse;width:100%%;"
                "font-size:13px'>"
                "<tr><th style='padding:6px 10px;text-align:left;"
                "color:#8b949e'>标题</th><th style='padding:6px 10px;"
                "color:#8b949e'>栏目</th><th style='padding:6px 10px;"
                "color:#8b949e'>日期</th><th style='padding:6px 10px;"
                "color:#8b949e'>生成</th><th style='padding:6px 10px;"
                "color:#8b949e'>状态</th></tr>%s</table>"
                "<div style='color:#58606a;font-size:11px;margin-top:16px'>"
                "内容生产域 · 飞书审阅后标记已审/已发</div>"
                "</div></body></html>" % (pill_nav("gzh"), rows)
                ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def _send_media(self, abspath, ctype):
        """小文件直发（mp4/wav/srt/json 预览用，无 Range，成片 <5MB）。"""
        try:
            with open(abspath, "rb") as f:
                data = f.read()
        except Exception:
            self._json({"error": "file not found"}, 404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
    def _handle_card_action(self, ev):
        """卡按钮被点 → 执行动作 → 回卡（作为 HTTP 响应体）。"""
        act = (ev.get("action") or {})
        val = act.get("value") or {}
        if isinstance(val, str):
            try:
                val = json.loads(val)
            except Exception:
                val = {}
        # 点击者 open_id（用于把订阅指向本人）
        operator = None
        try:
            op = ev.get("operator") or {}
            operator = op.get("open_id") or op.get("user_id") or None
        except Exception:
            operator = None
        qb = os.path.join(STATION, "quant_backtest")
        if qb not in sys.path:
            sys.path.insert(0, qb)
        import subscribe_action as _SA
        ok, msg, card = _SA.subscribe_from_card(val, operator=operator)
        if card:
            # 飞书卡回调：响应体直接当「更新后卡片」。
            # 用 cover/toast 结构让飞书就地更新并弹提示。
            self._json({
                "toast": {"type": "success" if ok else "info",
                          "content": msg},
                "card": {"type": "raw", "data": card},
            }, 200)
        else:
            self._json({"toast": {"type": "error", "content": msg}}, 200)
    def _feishu_encrypt_key(self):
        """读飞书 Encrypt Key（~/.lark-cli/config.json 或 env）。

        A4（2026-10-07）：回调验签应用与推送应用解耦——apps 数组里取
        **第一个带 encryptKey 的应用**（推送 bot apps[0] 无 key 不受影响，
        新建回调应用 apps[1] 携带 key 生效）。
        """
        ek = os.environ.get("FEISHU_ENCRYPT_KEY")
        if ek:
            return ek
        try:
            cfg = os.path.join(os.path.expanduser("~"), ".lark-cli",
                               "config.json")
            with open(cfg, encoding="utf-8") as f:
                apps = json.load(f).get("apps") or []
            for a in apps:
                k = a.get("encryptKey") or a.get("encrypt_key")
                if k and k != "EMPTY":
                    return k
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "api_misc.py", exc)
        return None
    def _feishu_unverified_alert(self):
        """K-STOCK-397：回调未验签降级告警（同日一次，A 通道 send_feishu）。

        未配 Encrypt Key 时回调不验签曾是「仅访问日志 warning」——每次
        回调刷一条且无人可观测（K-STOCK-394 审计建议 2）。现收敛为：
        flag 落盘计数 + 当日首次推飞书告警卡（走 send_feishu 判例链，
        build_todays_picks 同款 subprocess 模式），推送失败不反噬回调。
        """
        flag = os.environ.get("FEISHU_EVENT_FLAG") or os.path.join(
            STATION, "portal", "work", "feishu_event_unverified.flag")
        today = time.strftime("%Y-%m-%d")
        st = {}
        try:
            with open(flag, encoding="utf-8") as f:
                st = json.load(f)
        except Exception:
            st = {}
        if st.get("day") != today:
            st = {"day": today, "count": 0, "alerted": False}
        st["count"] = int(st.get("count") or 0) + 1
        if not st.get("alerted"):
            try:
                news = os.path.join(STATION, "news_collector")
                tmp = os.path.join(news, "outbox",
                                   "feishu_unverified_alert.txt")
                os.makedirs(os.path.dirname(tmp), exist_ok=True)
                with open(tmp, "w", encoding="utf-8") as f:
                    f.write(
                        "⚠️ 飞书回调验签降级：/feishu/event 未配 Encrypt Key，"
                        "收到回调但未验签（今日第 %d 次）。\n"
                        "修复：~/.lark-cli/config.json 给回调应用补 encryptKey "
                        "（A4 判例：apps 数组第一个带 encryptKey 的应用），"
                        "或设环境变量 FEISHU_ENCRYPT_KEY 后重启 portal。"
                        "本告警当日不重复。" % st["count"])
                subprocess.run(
                    [sys.executable, os.path.join(news, "send_feishu.py"),
                     tmp],
                    capture_output=True, timeout=60)
                st["alerted"] = True
                self.log_message(
                    "WARNING feishu/event FEISHU_ENCRYPT_KEY 未配置，回调未验签"
                    "（降级告警已按日触发；叠加 8091 绑回环，风险已降级）")
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "api_misc.py", exc)
        try:
            os.makedirs(os.path.dirname(flag), exist_ok=True)
            with open(flag, "w", encoding="utf-8") as f:
                json.dump(st, f, ensure_ascii=False)
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "api_misc.py", exc)
    def _api_feishu_event(self, raw):
        """K250 飞书事件回调入口（POST /feishu/event）。

        飞书会往这里推两类东西：
          1. url_verification 握手：需原样回 {"challenge": ...} 才算校验通过；
          2. card.action.trigger：卡片按钮被点 → 执行动作 → **就地返回**一张
             新卡（飞书支持「回调响应体即新卡」，无需另调发消息接口，最快）。

        解密：若飞书后台启用了 Encrypt Key，body 是 {"encrypt": "..."}，
        用 AES-256-CBC 解密（key=sha256(encrypt_key)，IV=密文前 16 字节）。
        安全：未配置 Encrypt Key 时一律返回 503，不进入事件分发；
        配置后强制 X-Lark-Signature 验签，并核验时间戳新鲜度。
        未通过验签的输入不触发任何订阅/卡片动作。
        """
        import hashlib
        import base64
        # 回调面 fail-closed：不论 body 是否能解析，未配 Key 一律拒绝。
        # 否则 malformed JSON 可绕开验签分支返回 200；nginx 开口时会
        # 把原先仅本机可达的降级行为暴露到局域网。
        _ek = self._feishu_encrypt_key()
        if not _ek:
            self._json({"code": 0, "msg": "callback unavailable"}, 503)
            return
        body = {}
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeError, ValueError):
            self._json({"code": 0, "msg": "invalid body"}, 400)
            return

        # ── 验签（P0-1b 配套，2026-10-08：飞书回调不走 portal token）──
        # 强制 X-Lark-Signature 校验（飞书 v2 事件签名：
        # sha256(timestamp + nonce + encrypt_key + 原始 body)）。
        # 能到这里说明 _ek 必非空（上方 503 早退已兜底）。
        _ts = (self.headers.get("X-Lark-Request-Timestamp") or "").strip()
        _nc = (self.headers.get("X-Lark-Request-Nonce") or "").strip()
        _sig = (self.headers.get("X-Lark-Signature") or "").strip()
        # K-STOCK-397：直接对原始字节拼接计算——原 raw.decode(
        # "utf-8","replace") 往返在非 UTF-8 body 上会失真，验签必败；
        # 合法 UTF-8 时与官方字符串拼接算法等价。
        _calc = hashlib.sha256(
            (_ts + _nc + _ek).encode("utf-8") + raw).hexdigest()
        if not _sig or not hmac.compare_digest(_sig, _calc):
            self._json({"code": 0, "msg": "invalid signature"}, 403)
            return
        # K-STOCK-407（审计 F1）：时间戳新鲜度窗口防重放——合法签名
        # 只证来源不证新鲜，截获的完整回调（头+body 原样重发）仍能
        # 通过验签。窗口外一律 403，与验签失败同响应（不泄露校验细节）。
        # 默认 ±300s：飞书重推为秒级、容忍本机时钟偏差；握手/事件
        # /卡片回调统一生效。FEISHU_EVENT_TOLERANCE（秒）可覆盖。
        _tol = int(os.environ.get("FEISHU_EVENT_TOLERANCE", "300"))
        try:
            _fresh = abs(time.time() - int(_ts)) <= _tol
        except ValueError:
            _fresh = False  # 时间戳非数字：按超窗拒（int() 失败）
        if not _fresh:
            self._json({"code": 0, "msg": "invalid signature"}, 403)
            return

        # ── 解密（如启用）────────────────────────────────
        if body.get("encrypt"):
            try:
                from cryptography.hazmat.primitives.ciphers import (
                    Cipher, algorithms, modes)
                data = base64.b64decode(body["encrypt"])
                iv, ct = data[:16], data[16:]
                key = hashlib.sha256(_ek.encode("utf-8")).digest()
                dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
                pt = dec.update(ct) + dec.finalize()
                pt = pt[:-pt[-1]]                     # 去 PKCS7 padding
                body = json.loads(pt.decode("utf-8"))
            except Exception as ex:
                self._json({"code": 0, "msg": "decrypt fail %s"
                            % str(ex)[:80]}, 200)
                return

        # ── 1) URL 握手 ──────────────────────────────────
        if body.get("type") == "url_verification" or body.get("challenge"):
            self._json({"challenge": body.get("challenge", "")}, 200)
            return

        # ── 2) 卡按钮回调 ────────────────────────────────
        try:
            header = body.get("header") or {}
            et = header.get("event_type") or body.get("type") or ""
            ev = body.get("event") or body
            # K-STOCK-399（397 留档 F2 收紧）：兜底分支要求 action 为
            # dict 且携带 value 键——我方卡按钮回调必带 value；外来
            # 事件含字符串 action 字段（如 "action":"created"）不再
            # 误路由进订阅处理器。
            _act = ev.get("action")
            if et in ("card.action.trigger", "card_action_trigger") \
                    or (isinstance(_act, dict) and "value" in _act):
                self._handle_card_action(ev)
                return
        except Exception as ex:
            self._json({"code": 0, "msg": "handle fail %s"
                        % str(ex)[:100]}, 200)
            return
        # 其它事件：静默 200，避免飞书重推
        self._json({"code": 0}, 200)
    def _api_recv_add(self, qs):
        from urllib.parse import parse_qs, unquote as _uq
        p = parse_qs(qs)
        name = _uq((p.get("name") or [""])[0]).strip()
        typ = (p.get("type") or ["open_id"])[0]
        rid = _uq((p.get("id") or [""])[0]).strip()
        if not name or not rid:
            self._json({"ok": False, "msg": "名称和 ID 必填"})
            return
        import json as _j
        qb = os.path.join(STATION, "quant_backtest")
        try:
            with open(os.path.join(qb, "receivers.json"), encoding="utf-8") as f:
                obj = _j.load(f)
        except Exception:
            obj = {"receivers": []}
        rs = obj.get("receivers") or []
        for r in rs:
            if r.get("name") == name:
                r["type"], r["id"] = typ, rid
                break
        else:
            rs.append({"name": name, "type": typ, "id": rid})
        obj["receivers"] = rs
        self._sub_write("receivers.json", obj)
        self._json({"ok": True})
    def _api_sub_test(self, qs):
        """POST /api/sub_test?id= → 调订阅引擎试推一条。"""
        from urllib.parse import parse_qs
        sid = (parse_qs(qs).get("id") or [""])[0]
        if not sid:
            self._json({"ok": False, "msg": "缺少 id"})
            return
        try:
            eng = self._sub_engine()
            res = eng.run_scan("intraday", only_sub=sid, force=True)
            st, _ = (res.get(sid) or ("no_result", {}))
            ok = st == "ok"
            self._json({"ok": ok, "msg": ("📨 已推送 %s" % sid) if ok
                        else ("未推送：%s" % st)})
        except Exception as ex:
            self._json({"ok": False, "msg": "试推异常：%s" % str(ex)[:150]})
    def _api_sub_del(self, qs):
        from urllib.parse import parse_qs
        sid = (parse_qs(qs).get("id") or [""])[0]
        d = self._sub_data()[0]
        keep = [s for s in d if s.get("id") != sid]
        if len(keep) == len(d):
            self._json({"ok": False, "msg": "订阅不存在"})
            return
        self._sub_write("subscriptions.json", {"subscriptions": keep})
        self._json({"ok": True})
    def _api_sub_toggle(self, qs):
        from urllib.parse import parse_qs
        p = parse_qs(qs)
        sid = (p.get("id") or [""])[0]
        en = (p.get("enabled") or ["1"])[0] in ("1", "true", "True")
        d = self._sub_data()[0]
        obj = {"subscriptions": d}
        hit = False
        for s in obj["subscriptions"]:
            if s.get("id") == sid:
                s["enabled"] = en
                hit = True
        if not hit:
            self._json({"ok": False, "msg": "订阅不存在"})
            return
        self._sub_write("subscriptions.json", obj)
        self._json({"ok": True, "enabled": en})
    def _subscribe_page(self, qs):
        """GET /bt/subscribe → 订阅管理页（K245）。"""
        import html as _h
        subs, recvs, st = self._sub_data()
        KIND_LABEL = {"stock_report": "个股报告", "daily_batch": "每日选股批次",
                      "alert_signal": "预警信号", "quote_move": "自选股异动"}
        whn = {"intraday": "盘中", "eod": "盘后"}
        today = time.strftime("%Y-%m-%d")
        rows = []
        for s in subs:
            sid = s.get("id", "")
            kind = KIND_LABEL.get(s.get("kind"), s.get("kind", "—"))
            when_s = " + ".join(whn.get(w, w) for w in (s.get("when") or []))
            recv = "、".join(s.get("receivers") or []) or "（默认）"
            sst = st.get(sid) or {}
            last = sst.get("last_ts") or "—"
            cnt = sst.get("count", 0) if sst.get("day") == today else 0
            on = s.get("enabled", True)
            rows.append(
                "<tr>"
                "<td><b>%s</b><br><span class='dim'>%s</span></td>"
                "<td>%s</td><td>%s</td><td>%s</td>"
                "<td>%s<br><span class='dim'>今日推 %d / 上限 3</span></td>"
                "<td><label class='sw'><input type='checkbox' %s "
                "onchange=\"subToggle('%s',this.checked)\"><span></span></label>"
                "</td>"
                "<td><button class='mini' onclick=\"subTest('%s')\">试推</button>"
                "<button class='mini danger' onclick=\"subDel('%s')\">删</button>"
                "</td></tr>" % (
                    _h.escape(s.get("name", sid)), _h.escape(sid),
                    _h.escape(kind), _h.escape(when_s), _h.escape(recv),
                    _h.escape(last), cnt, "checked" if on else "",
                    sid, sid, sid))

        recv_rows = []
        for r in recvs:
            recv_rows.append(
                "<tr><td><b>%s</b></td><td>%s</td>"
                "<td><code>%s</code></td><td>%s</td></tr>"
                % (_h.escape(r.get("name", "")), _h.escape(r.get("type", "")),
                   _h.escape(r.get("id", "") or ""),
                   _h.escape(r.get("note", ""))))

        css = """<style>
.sb-wrap{max-width:var(--page-w);margin:0 auto;padding:18px}
.sb-wrap h1{font-size:20px;margin:0 0 4px}
.sb-sub{color:#8b949e;font-size:13px;margin-bottom:16px}
.sb-card{background:#0d1117;border:1px solid #30363d;border-radius:10px;
padding:16px 18px;margin-bottom:16px}
.sb-card h2{font-size:15px;margin:0 0 12px;color:#e6edf3}
.sb-card table{width:100%;border-collapse:collapse;font-size:13px}
.sb-card th,.sb-card td{text-align:left;padding:9px 8px;
border-bottom:1px solid #21262d;vertical-align:middle}
.sb-card th{color:#8b949e;font-weight:500;font-size:12px}
.dim{color:#8b949e;font-size:11px}
.mini{background:#161b22;border:1px solid #30363d;color:#e6edf3;
border-radius:6px;padding:3px 10px;font-size:12px;cursor:pointer;
margin-right:5px;font-family:inherit}
.mini:hover{border-color:#58a6ff}
.mini.danger:hover{border-color:#f85149;color:#f85149}
.sw{position:relative;display:inline-block;width:40px;height:22px}
.sw input{display:none}
.sw span{position:absolute;inset:0;background:#3a4459;border-radius:11px;
transition:.2s;cursor:pointer}
.sw span:before{content:"";position:absolute;width:16px;height:16px;left:3px;
top:3px;background:#fff;border-radius:50%;transition:.2s}
.sw input:checked+span{background:#238636}
.sw input:checked+span:before{transform:translateX(18px)}
code{background:#161b22;padding:1px 5px;border-radius:4px;font-size:11px}
.sb-recv input,.sb-recv select{background:#0d1117;border:1px solid #30363d;
color:#e6edf3;border-radius:6px;padding:5px 8px;font-size:12px;margin-right:6px}
.sb-toast{position:fixed;top:20px;right:20px;background:#1f2937;color:#fff;
padding:10px 16px;border-radius:8px;font-size:13px;opacity:0;
transition:.3s;z-index:9}
.sb-toast.show{opacity:1}
</style>"""

        html = (self._COCKPIT_CSS + css + "<div class='sb-wrap'>"
                + pill_nav("qpzm")
                + "<h1>🔔 订阅推送</h1>"
                "<div class='sb-sub'>自选订阅 · 飞书自动推送 · 盘中定时 + 盘后汇总"
                " · 青萍之末 · 零界</div>"
                "<div class='sb-card'><h2>订阅清单</h2>"
                "<table><thead><tr><th>订阅</th><th>类型</th><th>时机</th>"
                "<th>接收人</th><th>最近推送</th><th>启用</th><th>操作</th>"
                "</tr></thead><tbody>"
                + ("\n".join(rows) or "<tr><td colspan='7'>（暂无订阅）</td></tr>")
                + "</tbody></table>"
                "<p class='dim' style='margin-top:10px'>※ 盘中每 30 分钟扫描，"
                "仅推「有变化」的；盘后 15:30 汇总全量。每订阅每日最多推 3 次"
                "（防轰炸）。</p></div>"
                "<div class='sb-card'><h2>接收人名单</h2>"
                "<table><thead><tr><th>名称</th><th>类型</th><th>ID</th>"
                "<th>备注</th></tr></thead><tbody>"
                + ("\n".join(recv_rows) or "<tr><td colspan='4'>（暂无）</td></tr>")
                + "</tbody></table>"
                "<div class='sb-recv' style='margin-top:14px'>"
                "<input id='rn' placeholder='名称，如 张三' style='width:140px'>"
                "<select id='rt'><option value='open_id'>open_id（个人）</option>"
                "<option value='chat_id'>chat_id（群）</option>"
                "<option value='email'>email</option>"
                "<option value='mobile'>mobile</option></select>"
                "<input id='ri' placeholder='接收人 ID（ou_xxx / oc_xxx）' "
                "style='width:280px'>"
                "<button class='mini' onclick='recvAdd()'>＋ 添加接收人</button>"
                "</div>"
                "<p class='dim' style='margin-top:8px'>※ 为后续「他人飞书付费订阅」"
                "预留：添加对方 open_id 后，可在订阅里指定推给他。</p></div>"
                "<div class='sb-toast' id='sb-toast'></div>"
                "<script>"
                "function toast(m){var t=document.getElementById('sb-toast');"
                "t.textContent=m;t.classList.add('show');"
                "setTimeout(function(){t.classList.remove('show')},2400)}"
                "function api(u,b){return fetch(u,{method:b?'POST':'GET'})"
                ".then(function(r){return r.json()})"
                ".catch(function(e){toast('请求失败');throw e})}"
                "function subToggle(id,on){api('/api/sub_toggle?id='+"
                "encodeURIComponent(id)+'&enabled='+(on?1:0),true)"
                ".then(function(d){toast(d.ok?(on?'已启用 ':'已停用 ')+id:"
                "'操作失败');if(!d.ok)location.reload()})"
                ".catch(function(){location.reload()})}"
                "function subTest(id){toast('正在试推 '+id+'（报告类需 15-40s）…');"
                "api('/api/sub_test?id='+encodeURIComponent(id),true)"
                ".then(function(d){toast(d.msg||(d.ok?'推送成功':'推送失败'))})}"
                "function subDel(id){if(!confirm('删除订阅 '+id+'？'))return;"
                "api('/api/sub_del?id='+encodeURIComponent(id),true)"
                ".then(function(d){toast(d.ok?'已删除':'删除失败');"
                "if(d.ok)location.reload()})}"
                "function recvAdd(){var n=document.getElementById('rn').value.trim();"
                "var t=document.getElementById('rt').value;"
                "var i=document.getElementById('ri').value.trim();"
                "if(!n||!i){toast('名称和 ID 都要填');return}"
                "api('/api/recv_add?name='+encodeURIComponent(n)+'&type='+"
                "encodeURIComponent(t)+'&id='+encodeURIComponent(i),true)"
                ".then(function(d){toast(d.ok?'已添加':'添加失败');"
                "if(d.ok)location.reload()})}"
                "</script></div></body></html>")
        self._html(html)
    def _sub_write(self, fn, obj):
        import json as _j
        qb = os.path.join(STATION, "quant_backtest")
        tmp = os.path.join(qb, fn + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            _j.dump(obj, f, ensure_ascii=False, indent=1)
        os.replace(tmp, os.path.join(qb, fn))
    def _sub_data(self):
        """读订阅清单 + 接收人 + 状态（门户展示用）。"""
        import json as _j
        qb = os.path.join(STATION, "quant_backtest")

        def _rd(fn, dflt):
            try:
                with open(os.path.join(qb, fn), encoding="utf-8") as f:
                    return _j.load(f)
            except Exception:
                return dflt
        subs = (_rd("subscriptions.json", {}) or {}).get("subscriptions") or []
        recvs = (_rd("receivers.json", {}) or {}).get("receivers") or []
        st = _rd(os.path.join("outbox", "subscribe_state.json"), {})
        return subs, recvs, st
    # ---------------------------------------------------- K245 订阅推送
    def _sub_engine(self):
        """惰性 import 订阅引擎（quant_backtest/subscribe.py）。"""
        qb = os.path.join(STATION, "quant_backtest")
        if qb not in sys.path:
            sys.path.insert(0, qb)
        import subscribe
        return subscribe
    def _api_report_push(self, qs):
        """POST /api/report_push?c= → 生成报告并推送飞书。"""
        from urllib.parse import parse_qs, unquote as _uq
        q = _uq((parse_qs(qs).get("c") or [""])[0]).strip()[:24]
        if not q:
            self._json({"ok": False, "msg": "缺少 code 参数"})
            return
        try:
            if os.path.join(STATION, "portal") not in sys.path:
                sys.path.insert(0, os.path.join(STATION, "portal"))
            import portal_report as _PR
            ok, msg = _PR.push_report(self, q)
            self._json({"ok": bool(ok), "msg": msg})
        except Exception as ex:
            self._json({"ok": False, "msg": "推送异常：%s"
                        % str(ex)[:150]})
    def _api_analyst_report(self, qs):
        """GET /api/analyst-report?c= → 生成报告 JSON（前端异步用）。"""
        from urllib.parse import parse_qs, unquote as _uq
        q = _uq((parse_qs(qs).get("c") or [""])[0]).strip()[:24]
        if not q:
            self._json({"ok": False, "msg": "缺少 code 参数"})
            return
        try:
            if os.path.join(STATION, "portal") not in sys.path:
                sys.path.insert(0, os.path.join(STATION, "portal"))
            import portal_report as _PR
            rep = _PR.build_report(self, q)
            self._json({"ok": True, "md": rep["md"],
                        "llm_ok": rep["llm_ok"], "ts": rep["ts"],
                        "name": rep.get("name"),
                        "px": rep.get("px")})
        except Exception as ex:
            import traceback
            try:
                with open(r"C:\Temp\analyst_report_err.log", "a",
                          encoding="utf-8") as f:
                    f.write(time.strftime("%m-%d %H:%M:%S ") + q + "\n")
                    traceback.print_exc(file=f)
            except OSError as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "api_misc.py", exc)
            self._json({"ok": False, "msg": "报告生成异常：%s"
                        % str(ex)[:150]})
    def _analyst_report_page(self, qs):
        """K244 青萍之末分析师报告页：/bt/analyst-report?c=sz000993

        事实聚合（行情/打分/缠论/筹码/板块/公告后验/量能事件/龙虎榜）
        → 本地 Ollama 大白话报告 → 一键推飞书。生成较慢（10~20s），
        前端异步拉 /api/analyst-report?c=，避免页首阻塞。"""
        from urllib.parse import parse_qs, unquote as _uq
        import html as _h
        qp = parse_qs(qs)
        q = _uq((qp.get("c") or [""])[0]).strip()[:24]
        e = _h.escape
        body = [self._COCKPIT_CSS, "</div>" if False else "",
                "<style>.arb{background:#161b22;border:1px solid #30363d;"
                "border-radius:8px;color:#8b949e;font-size:12px;"
                "padding:4px 12px;cursor:pointer;font-family:inherit}"
                ".arb.on{background:#0f141c;color:#e6edf3;font-weight:600;"
                "border-color:#58a6ff}"
                "#ar-md h2{font-size:16px;margin:16px 0 8px;"
                "color:#e6edf3;border-left:3px solid #58a6ff;"
                "padding-left:8px}"
                "#ar-md p{margin:8px 0;line-height:1.9;font-size:14px}"
                "#ar-md li{margin:6px 0;line-height:1.8}"
                ".ar-spin{display:inline-block;width:14px;height:14px;"
                "border:2px solid #30363d;border-top-color:#58a6ff;"
                "border-radius:50%;animation:ars 1s linear infinite;"
                "vertical-align:-2px;margin-right:6px}"
                "@keyframes ars{to{transform:rotate(360deg)}}</style>",
                pill_nav("qpzm")]
        if not q:
            body.append("<h1>🌊 青萍之末分析师报告</h1>"
                        "<div class='dim'>从个股页进入，或直接访问 "
                        "<code>/bt/analyst-report?c=sz000993</code></div>")
            self._html("".join(body))
            return
        body.append(
            "<h1>🌊 青萍之末分析师报告 · <span class='dim'>%s</span></h1>"
            "<div style='margin-bottom:10px'>"
            "<a class='arb' href='/kg/stock/%s'>↩ 回个股页</a> "
            "<button class='arb' onclick='_arGen()'>🔄 重新生成</button> "
            "<button class='arb on' id='ar-push' onclick='_arPush()'>"
            "📨 一键发送飞书</button> "
            "<span class='dim' id='ar-msg' style='margin-left:8px'>"
            "正在聚合事实并生成报告…</span></div>"
            "<div id='ar-md' class='card' style='line-height:1.9;"
            "font-size:14px'><div class='dim'><span class='ar-spin'></span>"
            "青萍之末分析师正在读数据（约 10~20 秒）…</div></div>"
            "<script>var _ARC=%s;function _arMd(t){"
            "var h=0,out='';t.split(/\\n/).forEach(function(ln){"
            "ln=ln.replace(/^\\s+/,'');if(!ln){out+='<p></p>';return}"
            "ln=ln.replace(/^###\\s*/,'').replace(/^##\\s*/,'')"
            ".replace(/^#\\s*/,'');"
            "var em=ln.match(/^([\\u{1F300}-\\u{1FAFF}\\u2600-\\u27BF]+)\\s*/u);"
            "var ic=em?em[1]:'';var tx=em?ln.slice(em[0].length):ln;"
            "tx=tx.replace(/\\*\\*(.+?)\\*\\*/g,'<b>$1</b>')"
            ".replace(/\\*(.+?)\\*/g,'<i>$1</i>')"
            ".replace(/`(.+?)`/g,\"<code>$1</code>\");"
            "var isH=/'^[^\\uff1a:]{2,14}[\\uff1a:]?\\$/.test(ic)||ic;"
            "if(ic){out+='<h2>'+ic+' '+tx+'</h2>'}else{out+='<p>'+tx+'</p>'}"
            "});return out}"
            "function _arLoad(){fetch('/api/analyst-report?c='+"
            "encodeURIComponent(_ARC)).then(function(r){return r.json()})"
            ".then(function(j){document.getElementById('ar-md').innerHTML="
            "j.ok?_arMd(j.md):('<div class=err>'+(j.msg||'生成失败')"
            "+'</div>');document.getElementById('ar-msg').textContent="
            "j.ok?('✅ 报告就绪 · '+(j.llm_ok?'AI 生成':'模板兜底')"
            "+' · '+j.ts):''}).catch(function(x){"
            "document.getElementById('ar-msg').textContent='生成失败：'+x})}"
            "function _arGen(){document.getElementById('ar-md').innerHTML="
            "'<div class=dim><span class=ar-spin></span>重新生成中…</div>';"
            "document.getElementById('ar-msg').textContent="
            "'正在聚合事实并生成报告…';_arLoad()}"
            "function _arPush(){var m=document.getElementById('ar-msg');"
            "var b=document.getElementById('ar-push');b.disabled=true;"
            "m.textContent='📨 正在推送飞书…';"
            "fetch('/api/report_push?c='+encodeURIComponent(_ARC),"
            "{method:'POST'}).then(function(r){return r.json()})"
            ".then(function(j){m.textContent=(j.ok?'✅ ':'⚠️ ')"
            "+(j.msg||'');b.disabled=false}).catch(function(x){"
            "m.textContent='推送失败：'+x;b.disabled=false})}"
            "_arLoad();</script>"
            % (e(q), e(q), json.dumps(q.replace("\"", ""))))
        self._html("".join(body))
    # ── L1 情报工场 v2（K40 四卡生命周期：收集→证据流→图谱探索→飞书输出） ──
    def _intel_page(self):
        import html as _h
        e = _h.escape
        nw = _news_db()
        ncfg = _read_json(os.path.join(STATION, "news_collector",
                                       "news_config.json"))
        qcfg = _read_json(os.path.join(STATION, "quant_backtest",
                                       "config.json"))
        rep = qcfg.get("report", {})
        cst = _read_json(os.path.join(STATION, "news_collector",
                                      "status.json")) or {}

        # ① 今日情报流统计 + 双向条
        d = nw["dist"]
        tot = (d.get("bull", 0) + d.get("bear", 0) + d.get("neutral", 0)) or 1
        bar = ("<div style='display:flex;height:14px;border-radius:7px;"
               "overflow:hidden;max-width:420px;margin:10px 0 4px'>"
               "<i style='width:%.0f%%;background:#f85149'></i>"
               "<i style='width:%.0f%%;background:#8b949e'></i>"
               "<i style='width:%.0f%%;background:#3fb950'></i></div>"
               "<div class='dim'>利多 %d · 中性 %d · 利空 %d（今日已判 "
               "%d 条）</div>"
               % (d.get("bull", 0) * 100.0 / tot,
                  d.get("neutral", 0) * 100.0 / tot,
                  d.get("bear", 0) * 100.0 / tot,
                  d.get("bull", 0), d.get("neutral", 0), d.get("bear", 0),
                  tot))
        flow = ("<div style='font-size:15px;margin-top:6px'>今日采集 "
                "<b>%d</b> 条 · 关键词命中 <b>%d</b> 条 · 已判 <b>%d</b> 条"
                "</div>%s" % (nw["today"], nw["hit"], tot, bar))

        # ② 四卡板（K40 生命周期：收集→证据流→图谱→飞书输出）
        srcs = cst.get("sources", {}) or {}
        n_ok = sum(1 for v in srcs.values() if v.get("ok"))
        wl = ncfg.get("watchlist", []) or []
        card_collect = (
            "<a class='idm' href='/ops'>"
            "<div class='ihead'><div class='iic'>🛰</div><div>"
            "<div class='it'>开源情报收集</div>"
            "<div class='is'>源 · OSINT 采集与监控</div></div></div>"
            "<div class='idesc'>%d 源新闻采集、%d 标的 watchlist 监控、"
            "来源新鲜度——情报从这里进来。</div>"
            "<div class='ims'><span>24h <b>%s</b></span>"
            "<span>源 <b>%d/%d</b></span><span>监控 <b>%d</b></span></div>"
            "<div class='ifoot'><span class='ist'><i class='idot%s'>"
            "</i>采集于 %s</span><span class='ienter'>采集管道 →</span>"
            "</div></a>"
            % (len(srcs), len(wl), cst.get("news_24h", "—"),
               n_ok, len(srcs), len(wl),
               "" if cst.get("last_ok") else " off",
               e(str(cst.get("last_collect", "—")))))
        card_flow = (
            "<a class='idm' href='/bt-jev'>"
            "<div class='ihead'><div class='iic'>📡</div><div>"
            "<div class='it'>证据流（读）</div>"
            "<div class='is'>今日判断了什么 · 依据是什么</div></div></div>"
            "<div class='idesc'>AI 晨报 + JEV 逐条判断（含置信度），"
            "分析过程的实锤与证据点。</div>"
            "<div class='ims'><span>今日 <b>%d</b></span>"
            "<span>利多 <b>%d</b></span><span>利空 <b>%d</b></span></div>"
            "<div class='ifoot'><span class='ist'><i class='idot'></i>"
            "关键词命中 %d 条</span><span class='ienter'>晨报 · JEV →"
            "</span></div></a>"
            % (nw["today"], d.get("bull", 0), d.get("bear", 0),
               nw["hit"]))
        card_kg = (
            "<div class='idm'>"
            "<div class='ihead'><div class='iic'>🕸</div><div>"
            "<div class='it'>知识图谱<span class='itag'>探索</span>"
            "</div><div class='is'>分析过程的工具 · 看细节的地方"
            "</div></div></div>"
            "<div class='idesc'>知识图谱是探索过程的一件工具，"
            "不是情报的全部——从这里下钻每只股票的基本面 / 技术面 / "
            "信息面，以及公告新闻原文。</div>"
            "<div class='itools'>"
            "<a href='/kg'>图谱探索</a><a href='/kg/map'>游资地图</a>"
            "<a href='/kg/flow'>板块工单</a><a href='/kg/label'>画图标注"
            "</a><a href='/kg/card'>推演卡</a>"
            "<a href='/kg/shadow'>影子面板</a></div>"
            "<div class='ifoot'><span class='ist'>六件套 · 行式下钻"
            "</span><span class='ienter'>逐件进入 ↗</span></div></div>")
        docs = [("📰 金融新闻日报", ncfg.get("doc_url")),
                ("⚖️ JEV 判断记录", ncfg.get("jev_doc_url")),
                ("🌅 盘前分析镜像", rep.get("premarket_doc_url")),
                ("📄 回测报告文档", rep.get("doc_url"))]
        doc_html = "".join(
            "<a class='doc' href='%s' target='_blank'>%s ↗</a>"
            % (e(str(u)), lb) for lb, u in docs if u) or \
            "<div class='dim'>未配置文档 URL</div>"
        # ── K42 预警实时值（与 /api/alerts 同源；cross_poll 60s 缓存）
        try:
            _al_map = {a["name"]: a for a in
                       (build_alerts().get("alerts") or [])}
        except Exception:
            _al_map = {}

        def _av(nm):
            a = _al_map.get(nm)
            return a["value"] if a else "—"

        _lst = _read_json(os.path.join(
            STATION, "news_collector", "outbox", "alert_push_last.json"))
        _push_ms = ("<div class='ims'>"
                    "<span>缠论 <b>%s</b></span><span>九维 <b>%s</b></span>"
                    "<span>weak <b>%s</b></span><span>共振 <b>%s</b></span>"
                    "</div>" % (_av("缠论信号"), _av("九维警报池"),
                                _av("弱于板块在案"), _av("共振做多")))
        if _lst.get("ts"):
            _push_ms += ("<div class='ims'><span>上次推送 <b>%s · %s</b>"
                         " %s</span><span>推了 <b>%s 路</b></span></div>"
                         % (e(_lst["ts"]),
                            "晨" if _lst.get("mode") == "morning" else "盘后",
                            "✅" if _lst.get("ok") else "❌",
                            _lst.get("n", "—")))
        else:
            _push_ms += ("<div class='ims'><span>上次推送 <b>—</b>"
                         "</span></div>")
        card_push = (
            "<div class='idm'>"
            "<div class='ihead'><div class='iic'>📨</div><div>"
            "<div class='it'>飞书预警输出<span class='itag'>情报变现"
            "</span></div><div class='is'>发 · 定时定量往外推送"
            "</div></div></div>"
            "<div class='idesc'>把其他板块的数据抽出来，每天定时定量"
            "往外发送——晨 7:10 / 盘后 16:05 定时 + 每 30 分钟阈值监听"
            "即时推（个人 + 家人群），情报工作流的出口。</div>"
            + _push_ms +
            "<div class='itools'>"
            "<a href='/api/alert/push'>📨 立即推送全量</a>"
            "<a href='/push/'>🛰 推送中台 · 情报变现 →</a>"
            "<a href='/quant'>📊 预警速览 →</a></div>"
            "<div class='ifoot'><span class='ist'>"
            "<i class='idot%s'></i>%s</span><span class='ienter'>"
            "文档枢纽 ↓</span></div>%s</div>"
            % (" off" if not (_lst.get("ts") and _lst.get("ok")) else "",
               "推送通道" if _lst.get("ok") else "未推送",
               doc_html))

        # ③ 简表：今日证据流（最近 8 条 + 多空条）
        rows8 = "".join(
            "<div style='margin:8px 0;font-size:13px'>"
            "<span style='color:%s;font-weight:600'>%s %d%%</span>"
            "<span class='dim'> · %s · %s</span> %s</div>"
            % ("#f85149" if x["bias"] == "bull" else
               "#3fb950" if x["bias"] == "bear" else "#8b949e",
               e(x["bias_cn"]), round((x["conf"] or 0) * 100),
               e(x["scope_cn"]), e(x["ts"]), e(x["title"]))
            for x in nw["recent"][:8]) or \
            "<div class='dim'>今日暂无已判断新闻</div>"
        more = ("<div style='margin-top:6px'><a href='/bt-jev'>"
                "全部 JEV 判断（可下钻新闻依据）→</a></div>")

        body = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,"
                "initial-scale=1'><title>情报工场</title>"
                + self._COCKPIT_CSS + self._INTEL_CARD_CSS + "</head><body>"
                + pill_nav("intel")
                + "<h1>🕵️ 情报工场</h1><div class='dim'>开源情报从这"
                "进来 · 证据在这沉淀 · 结论从这推走 · 更新于 %s</div>"
                % e(time.strftime("%Y-%m-%d %H:%M"))
                + "<div class='iboard'>%s%s%s%s</div>"
                % (card_collect, card_flow, card_kg, card_push)
                + "<div class='sec'><h2>📡 今日证据流</h2>"
                "<div class='dim' style='margin-bottom:6px'>最近已判断"
                " 8 条 · <a href='/bt-brief'>🤖 AI 晨报</a> · "
                "<a href='/bt-jev'>⚖️ JEV 判断</a></div>"
                + flow + rows8 + more + "</div>"
                + "</body></html>")
        self._html(body)
    # ── K238 每日选股批次回测（挂在自选池打分信号后面） ──
    def _qpzm_batch_blk(self, snap):
        import html as _h
        e = _h.escape
        rep = _read_json(os.path.join(REPORTS_DIR,
                                      "rps_batch_backtest.json")) or {}
        batches = rep.get("batches") or []
        meta = rep.get("meta") or {}

        def _pc(x, bold=False):
            if x is None:
                return "<span class='dim'>—</span>"
            c = "#f85149" if x >= 0 else "#3fb950"
            s = "%+.2f%%" % (x * 100)
            return ("<b style='color:%s'>%s</b>" % (c, s) if bold
                    else "<span style='color:%s'>%s</span>" % (c, s))

        def _hrow(h, n):
            if not h:
                return ("<td class='dim'>—</td><td class='dim'>—</td>"
                        "<td class='dim'>—</td>")
            lab = "%d日%s" % (n, "（至今）" if h.get("partial") else "")
            return ("<td>%s<div class='dim' style='font-size:11px'>%s · "
                    "成交 %d</div></td><td>%s</td><td>%s</td>"
                    % (e(lab), "胜率 " + (("%.1f%%" % (h["win_rate"] * 100))
                                          if h.get("win_rate") is not None
                                          else "—"),
                       h.get("n_traded") or 0,
                       _pc(h.get("mean"), bold=True),
                       _pc(h.get("excess"))))

        # ── K251 逐日盯表明细（每天的收益全部展开，不打包） ──
        def _daily_tbl(dl):
            if not dl or not (dl.get("k")):
                return "<div class='dim'>无逐日数据</div>"
            ks = dl.get("k") or []
            dts = dl.get("date") or []
            arr_day = dl.get("day") or []
            arr_cum = dl.get("cum") or []
            arr_ex = dl.get("excess") or []
            tr = ""
            for idx, k in enumerate(ks):
                d8 = str(dts[idx]) if idx < len(dts) else ""
                d10 = ("%s-%s-%s" % (d8[:4], d8[4:6], d8[6:8])
                       if len(d8) == 8 else d8)
                tr += ("<tr><td class='dim'>D%d</td><td class='dim'>%s</td>"
                       "<td>%s</td><td>%s</td><td>%s</td></tr>"
                       % (k, e(d10),
                          _pc(arr_day[idx] if idx < len(arr_day) else None),
                          _pc(arr_cum[idx] if idx < len(arr_cum) else None,
                              bold=True),
                          _pc(arr_ex[idx] if idx < len(arr_ex) else None)))
            lab_cum = "累计净%s" % ("（至今）" if dl.get("partial") else "")
            return ("<table><tr><th>持有</th><th>日期</th><th>当日</th>"
                    "<th>%s</th><th>超额</th></tr>%s</table>"
                    % (lab_cum, tr))

        if not batches:
            body = ("<div class='dim'>暂无批次回测数据（每日 17:35 跑批后"
                    "自动生成，或手动执行 qpzm_daily_batch.py --all）</div>")
        else:
            # ① 当日批次卡（batches 倒序首行 = 最新批次）
            cur = batches[0]
            note = {"empty": "⛔ 环境闸门收紧，当日 CN 无买入/观察批次"
                             "（回避票不计入批次回测）",
                    "await_t1": "⏳ 信号日为数据尾，待 T+1 开盘进场",
                    "no_bar": "该日无行情数据"}.get(cur.get("note"))
            src_chip = ("<span style='font-size:11px;border:1px solid %s;"
                        "color:%s;border-radius:10px;padding:1px 8px;"
                        "margin-left:8px'>%s</span>"
                        % (("#58a6ff", "#58a6ff", "产线口径")
                           if cur.get("source") == "live"
                           else ("#8b949e", "#8b949e", "复算口径·无JEV")))
            if note:
                card = ("<div style='margin-bottom:6px'><b>%s</b> 批次：%s%s"
                        "</div>" % (e(str(cur.get("date"))), note,
                                    src_chip))
            else:
                card = ("<div style='margin-bottom:6px'><b>%s</b> 批次 · "
                        "<span style='color:#f85149'>买入 %d</span> · "
                        "<span style='color:#d29922'>观察 %d</span>%s</div>"
                        "<table><tr><th>持有</th><th>批次均值</th><th>超额"
                        "</th></tr>%s</table>"
                        % (e(str(cur.get("date"))), cur.get("n_buy") or 0,
                           cur.get("n_obs") or 0, src_chip,
                           "".join("<tr>%s</tr>" % _hrow(
                               cur.get("holds", {}).get(str(n)), n)
                               for n in (5, 10, 20))))
                # K251 逐日盯市（当日批次全程展开）
                dl = cur.get("daily")
                if dl:
                    card += ("<div class='dim' style='margin:10px 0 3px'>"
                             "📅 逐日盯市 · T+1 开盘进场起 · 成交 %d 票%s"
                             "</div>%s"
                             % (dl.get("n_traded") or 0,
                                "（至今）" if dl.get("partial") else "",
                                _daily_tbl(dl)))
            # ② 历史批次表（日期倒序，最近 30 行；每行可展开逐日明细）
            rows = ""
            for b in batches[:30]:
                hs = b.get("holds") or {}
                rows += (
                    "<tr><td><b>%s</b><div class='dim' style='font-size:11px'>"
                    "%s</div></td><td class='dim'>%d / %d</td><td>%s</td>"
                    "<td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
                    "</tr>"
                    % (e(str(b.get("date"))),
                       "产线" if b.get("source") == "live" else "复算",
                       b.get("n_buy") or 0, b.get("n_obs") or 0,
                       _pc((hs.get("5") or {}).get("mean")),
                       _pc((hs.get("5") or {}).get("excess")),
                       _pc((hs.get("10") or {}).get("mean")),
                       _pc((hs.get("10") or {}).get("excess")),
                       _pc((hs.get("20") or {}).get("mean")),
                       _pc((hs.get("20") or {}).get("excess"))))
                # K251 逐日明细折叠行（点开即展开每天的收益）
                dl = b.get("daily")
                if dl:
                    rows += ("<tr><td colspan='8' style='padding:0 4px 4px'>"
                             "<details><summary class='dim' style='cursor:"
                             "pointer;font-size:12px'>📅 逐日明细</summary>"
                             "%s</details></td></tr>" % _daily_tbl(dl))
            tbl = ("<table><tr><th>批次日</th><th>买/观</th><th>5日均</th>"
                   "<th>5日超额</th><th>10日均</th><th>10日超额</th>"
                   "<th>20日均</th><th>20日超额</th></tr>%s</table>"
                   % (rows or "<tr><td colspan='8' class='dim'>暂无历史批次"
                      "</td></tr>"))
            body = (card + tbl
                    + "<div class='dim' style='margin-top:6px'>口径：%s · "
                    "共 %d 个批次 · 生成于 %s</div>"
                    % (e(str(meta.get("design", ""))),
                       meta.get("n_batches") or len(batches),
                       e(str(meta.get("generated_at", "—")))))
        return ("<div class='sec'><h2>📊 每日批次回测</h2>" + body + "</div>")
    # ── K-STOCK-037 组织架构图页（历史 SSR，已由新版 org.html 替代） ──
    def _org_page(self):
        """🏛 组织架构：human → 田丰 · 参谋长 → 部门 → 岗位 树形图。
        数据源 agent_platform/registry/{org,team_*}.json 与
        portal/ai_market.json（注册表存源，本页只读渲染）。
        岗位不设独立对话窗口——对话统一走 🦸 超级助手
        （全站右下角浮动图标 / http://127.0.0.1:8787）。"""
        import html as _h
        e = _h.escape
        org = _read_json(os.path.join(
            STATION, "agent_platform", "registry", "org.json")) or {}
        mkt = _read_json(os.path.join(ROOT, "ai_market.json")) or {}
        face = {}
        for _g in mkt.get("groups", []):
            for _a in _g.get("ais", []):
                face[_a.get("id", "")] = _a
        team_map = {}
        for _d in org.get("departments", []):
            _dk = _d.get("dept", "")
            _tj = {}
            for _cand in [_dk] + list(_d.get("teams") or []):
                _tj = _read_json(os.path.join(
                    STATION, "agent_platform", "registry",
                    "team_%s.json" % _cand)) or {}
                if _tj:
                    break
            if _tj:
                team_map[_dk] = _tj

        def _agent_card(ag):
            fid = face.get(ag.get("agent_id", ""), {}) or {}
            icon = fid.get("icon", "🤖")
            name = fid.get("name") or ag.get("agent_id", "?")
            tags = []
            if ag.get("is_debate_node"):
                tags.append("↔ 辩论 %d 轮" % (ag.get("max_debate_rounds") or 0))
            if ag.get("level") == 2:
                tags.append("⚖ L2 汇总")
            # K-ORG-020 职级徽章：E3 绿(30年档) / E2 蓝(20年档) / E1 灰(10年档)
            _grade_tag = ""
            if ag.get("grade"):
                _gcolor = {"E3": "#3fb950", "E2": "#58a6ff",
                           "E1": "#8b949e"}.get(ag["grade"], "#d29922")
                _grade_tag = ("<span style='font-size:10.5px;color:%s;"
                              "border:1px solid %s;border-radius:10px;"
                              "padding:1px 8px;margin-left:6px'>%s %s</span>"
                              % (_gcolor, _gcolor, e(str(ag["grade"])),
                                 e(str(ag.get("grade_note") or "").split("：")[0])))
            tag_html = "".join(
                "<span style='margin-left:auto;font-size:10.5px;"
                "color:#d29922;border:1px solid #d29922;border-radius:10px;"
                "padding:1px 8px'>%s</span>" % e(t) for t in tags)
            tools = "、".join(ag.get("tools") or []) or "—"
            up = "、".join(ag.get("parent_node_keys") or [])
            up_html = ("<div class='dim' style='font-size:11.5px;"
                       "margin-top:6px'>↑ 上游：%s</div>" % e(up) if up else "")
            # K-ORG-020 职责详情行（梯队说明：E2·20年档·后端：常规交付…）
            cd_html = ("<div class='dim' style='font-size:11.5px;"
                       "margin-top:5px'>%s</div>" % e(str(ag.get("charter_detail")))
                       if ag.get("charter_detail") else "")
            return (
                "<div style='border:1px solid #30363d;border-radius:12px;"
                "background:#161b22;padding:14px 16px'>"
                "<div style='display:flex;align-items:baseline;gap:8px'>"
                "<span style='font-size:16px;font-weight:700'>%s %s</span>%s"
                "</div>"
                "<div style='font-size:12.5px;margin:6px 0 2px'>%s</div>"
                "<div class='dim' style='font-size:11.5px'>%s · %s · "
                "🛠 %s</div>%s%s</div>"
                % (e(icon), e(name), tag_html + _grade_tag,
                   e(fid.get("role") or ag.get("role_profile", "")),
                   e(str(ag.get("role_profile", ""))),
                   e(str(ag.get("llm_binding", ""))), e(tools),
                   cd_html, up_html))

        def _dept_block(d):
            dept = d.get("dept", "")
            planned = d.get("status") == "planned"
            tj = team_map.get(dept) or {}
            cards = "".join(_agent_card(a) for a in tj.get("agents", []))
            if planned:
                inner = ("<div class='dim' style='font-size:12.5px;"
                         "padding:8px 0'>🚧 扩编中（K-ORG-001 P2 预留："
                         "拉代码·优化代码·训练模型，待投研部影子验收后"
                         "启动）</div>")
            elif cards:
                inner = ("<div style='display:grid;grid-template-columns:"
                         "repeat(auto-fit,minmax(250px,1fr));gap:10px'>"
                         + cards + "</div>")
            else:
                inner = "<div class='dim'>暂无编制数据</div>"
            team_line = ""
            if tj:
                team_line = ("<span class='dim' style='font-size:11.5px;"
                             "margin-left:8px'>团队 %s</span>"
                             % e(str(tj.get("team", ""))))
            return ("<div style='margin:10px 0 0 26px;border-left:2px solid "
                    "#30363d;padding-left:16px'>"
                    "<div style='font-size:15px;font-weight:700'>📁 %s%s"
                    "</div>"
                    "<div class='dim' style='font-size:12px;margin:2px 0 8px'>"
                    "%s</div>%s</div>"
                    % (e(str(d.get("name", dept))), team_line,
                       e(str(d.get("charter", ""))), inner))

        chief = org.get("chief", {})
        rep = org.get("reporting", {})
        sev = rep.get("severity", {})
        ch_status = ("骨架已立（9b 判读验证通过）"
                     if chief.get("status") == "skeleton"
                     else str(chief.get("status", "")))

        def _arrow(label):
            return ("<div class='dim' style='margin:4px 0 4px 26px;"
                    "font-size:12px'>↓ %s</div>" % e(label))

        dept_html = "".join(_dept_block(d)
                            for d in org.get("departments", []))
        tree = (
            "<div class='sec'>"
            "<div style='border:2px solid #8b949e;border-radius:12px;"
            "background:#161b22;padding:12px 16px;display:inline-block'>"
            "👤 %s（老板 · 汇报对象）</div>"
            % e(str(org.get("report_to", "human")))
            + _arrow("汇报三通道：📡 日报 %s · 🔴 P0 %s · 🟡 P1 %s"
                     % (rep.get("daily_push", "17:35"),
                        sev.get("P0", "即时"), sev.get("P1", "1h")))
            + "<div style='border:2px solid #d29922;border-radius:12px;"
              "background:#161b22;padding:12px 16px;display:inline-block'>"
              "<b style='font-size:16px'>🧠 田丰 · 参谋长</b>"
              "<span class='dim' style='font-size:12px;margin-left:8px'>"
              "%s · %s · %s</span></div>"
              % (e(str(chief.get("role_profile", ""))),
                 e(str(chief.get("llm_binding", ""))), e(ch_status))
            + _arrow("直辖部门 %d 个" % len(org.get("departments", [])))
            + dept_html + "</div>")
        banner = (
            "<div class='sec' style='border-color:#58a6ff'>"
            "🦸 <b>对话统一入口</b>：各岗位均<b>不设独立对话窗口</b>——"
            "通过超级助手直接对话即可调度全部岗位（全站右下角浮动图标，"
            "或 <a href='/hermes/' target='_blank' "
            "rel='noopener'>超级助手 /hermes/</a>）· 聘请/派单走 "
            "<a href='/aim'>AI 市场货架</a></div>")
        body = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,"
                "initial-scale=1'><title>🏛 组织架构</title>"
                + self._COCKPIT_CSS + "</head><body>"
                + pill_nav("org")
                + "<h1>🏛 组织架构</h1><div class='dim'>"
                "human → 田丰 · 参谋长 → 部门 → 岗位 · 注册表实时渲染"
                "（agent_platform/registry）</div>"
                + banner + tree
                + "<div class='dim' style='margin-top:20px'>数据源："
                "org.json（组织顶点）· team_invest.json（alpha 执行拓扑）· "
                "ai_market.json（岗位名/图标，与 AI 市场货架同源）· "
                "上新岗位改注册表即可，页面零改</div></body></html>")
        self._html(body)
    # ── K-STOCK-087 算法商品市场（/algo）：试看结果 + 本地卡密订阅解锁 ──
    def _license_state(self, algo_id):
        """订阅状态判定：activated_keys 中任一有效卡（未到期）覆盖
        algo_id → True。卡密库 portal/licenses.json 存源。"""
        import time as _t
        lic = _read_json(os.path.join(ROOT, "licenses.json")) or {}
        act = set(lic.get("activated_keys", []))
        today = _t.strftime("%Y-%m-%d")
        for rec in lic.get("licenses", []):
            if rec.get("key") not in act:
                continue
            if str(rec.get("expiry", "")) < today:
                continue
            cov = rec.get("algos", "all")
            if cov == "all" or algo_id in cov:
                return True
        return False
    # ── K-STOCK-033 AI 市场：聘请 AI 做商业化（货架页） ──
    def _aim_page(self):
        """🤖 AI 市场：可聘 AI 岗位货架。数据源 portal/ai_market.json
        （注册表存源，本页只读渲染）；卡样式与 /bt 总目录同源。"""
        import html as _h
        e = _h.escape
        mkt = _read_json(os.path.join(ROOT, "ai_market.json")) or {}
        groups = mkt.get("groups", [])
        # K-STOCK-122 岗位→算法互链：algo id → 商品卡（icon/name），
        # 岗位卡渲染「所用算法」芯片，点击跳 /algo 锚点定位
        algo_mkt = _read_json(os.path.join(ROOT, "algo_market.json")) or {}
        amap = {a.get("id", ""): a
                for a in algo_mkt.get("algos", []) if a.get("id")}

        # K-ORG-190：货架技能行切 skill_registry 同源投影——
        # agent_platform/registry/skill_registry.json 是唯一技能真源，
        # 本页只读渲染（ai_market.json 本体不改，字段迁移 P2 另行）。
        # 岗位技能引用清单 = algos id 数组（ai_market.json 的 skills
        # 字段是面向人的描述字符串，非机器外键），经 {id→title/kind}
        # 解析展示；id 查不到 → 灰字"未登记"（不假绿）。
        skill_reg = _read_json(os.path.join(
            STATION, "agent_platform", "registry",
            "skill_registry.json")) or {}
        smap = {s.get("id", ""): s
                for s in skill_reg.get("skills", []) if s.get("id")}
        _KIND_CN = {"tool": "工具", "algo": "算法", "automation": "自动化",
                    "prompt": "人设", "external": "外部"}

        def _skill_row(a):
            ids = a.get("algos", []) or []
            if not ids:
                return ""  # 空清单 → 维持既有口径（不渲染技能行）
            chips = []
            for i in ids:
                s = smap.get(i)
                if s:
                    chips.append("%s(%s)" % (
                        e(s.get("title", i)),
                        e(_KIND_CN.get(s.get("kind", ""),
                                       s.get("kind", "?")))))
                else:
                    chips.append("<span style='color:#8b949e'>%s(未登记)"
                                 "</span>" % e(i))
            return ("<div class='dim' style='font-size:11.5px;"
                    "margin-top:6px'>技能 %d 项：%s</div>"
                    % (len(ids), "、".join(chips)))

        def _ai_card(a):
            badge = a.get("badge", "可聘")
            bc = a.get("badge_color", "#58a6ff")
            algo_chips = "".join(
                "<a class='tp' href='/algo?id=%s' style='text-decoration:"
                "none;font-size:11.5px'>%s %s</a>"
                % (e(i), e(amap.get(i, {}).get("icon", "🧩")),
                   e(amap.get(i, {}).get("name", i)))
                for i in a.get("algos", []) or [] if i in amap)
            algo_row = ("<div style='display:flex;gap:6px;flex-wrap:wrap;"
                        "margin-top:8px'>%s</div>" % algo_chips
                        if algo_chips else "")
            return (
                "<div style='border:1px solid #30363d;border-radius:12px;"
                "background:#161b22;padding:16px 18px'>"
                "<div style='display:flex;align-items:baseline;gap:8px'>"
                "<span style='font-size:17px;font-weight:700'>%s %s</span>"
                "<span style='margin-left:auto;font-size:11.5px;color:%s;"
                "border:1px solid %s;border-radius:12px;padding:2px 10px'>%s"
                "</span></div>"
                "<div style='font-size:13px;margin:8px 0 4px'>%s</div>"
                "<div class='dim' style='font-size:12px;margin-bottom:6px'>%s"
                "</div>"
                "<div class='dim' style='font-size:11.5px'>⚙ %s</div>"
                "<div style='font-size:12.5px;margin:10px 0;display:flex;"
                "gap:6px;flex-wrap:wrap'>"
                "<span class='dim'>📌 %s</span><span class='dim'>·</span>"
                "<span class='dim'>🤝 %s</span></div>"
                "%s%s"
                "<a class='tp' href='%s'>聘请 →</a></div>"
                % (e(a.get("icon", "🤖")), e(a.get("name", "")), bc, bc,
                   e(badge), e(a.get("role", "")),
                   e(a.get("skills", "")), e(a.get("model", "")),
                   e(a.get("status", "")), e(a.get("hire", "")),
                   algo_row, _skill_row(a), e(a.get("entry", "/"))))
        secs = "".join(
            "<div class='sec'><h2>%s</h2>"
            "<div style='display:grid;grid-template-columns:repeat("
            "auto-fit,minmax(280px,1fr));gap:12px'>%s</div></div>"
            % (e(g.get("name", "")),
               "".join(_ai_card(a) for a in g.get("ais", [])))
            for g in groups)

        # K-STOCK-034 超级助手结合：Hermes（8787）= AI 组织架构总入口，
        # 顶部枢纽卡 + org.json 实时组织架构条
        org = _read_json(os.path.join(
            STATION, "agent_platform", "registry", "org.json")) or {}
        dept_chips = "".join(
            "<span class='tp' style='cursor:default'>%s：%s</span>"
            % (e(d.get("name", d.get("dept", ""))),
               e("、".join(d.get("agents", [])) or "扩编中"))
            for d in org.get("departments", []))
        chief = org.get("chief", {})
        hub_card = (
            "<div class='sec'><h2>🦸 超级助手 · AI 组织架构总入口</h2>"
            "<div style='display:grid;grid-template-columns:repeat("
            "auto-fit,minmax(300px,1fr));gap:12px'>"
            "<a href='/hermes/' target='_blank' style='display:"
            "block;border:1px solid #58a6ff;border-radius:12px;"
            "background:#161b22;padding:16px 18px;text-decoration:none;"
            "color:#e6edf3'>"
            "<div style='font-size:17px;font-weight:700'>🦸 Hermes 超级助手"
            "<span style='float:right;color:#3fb950;font-size:12px'>● 在线"
            " · 进入对话 →</span></div>"
            "<div class='dim' style='font-size:12.5px;margin:6px 0 10px'>"
            "与超级助手对话即可调度下方全部 AI 岗位 · 执行/记忆/插件网关"
            "</div>"
            "<div style='font-size:13px'><b>经门户直达 /hermes/</b></div>"
            "</a>"
            "<div style='border:1px solid #30363d;border-radius:12px;"
            "background:#161b22;padding:16px 18px'>"
            "<div style='font-size:15px;font-weight:700'>🏛 组织架构"
            "<span class='dim' style='font-size:11.5px;margin-left:8px'>"
            "汇报对象：人（%s）</span></div>"
            "<div style='font-size:13px;margin:8px 0'>🧠 田丰 · 参谋长"
            "<span class='dim'>（%s · %s）</span></div>"
            "<div style='display:flex;gap:6px;flex-wrap:wrap'>%s</div>"
            "<div class='dim' style='font-size:11.5px;margin-top:8px'>"
            "📡 汇报三通道：日报 17:35 · P0 即时 · P1 一小时内</div>"
            "</div></div></div>"
            % (e(str(org.get("report_to", "human"))),
               e(str(chief.get("role_profile", "chief_of_staff"))),
               e(str(chief.get("llm_binding", ""))), dept_chips))
        body = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,"
                "initial-scale=1'><title>🤖 AI 市场</title>"
                + self._COCKPIT_CSS + "</head><body>"
                + pill_nav("aim")
                + "<h1>🤖 AI 市场</h1><div class='dim'>"
                + e(mkt.get("tagline", ""))
                + " · 聘请 AI 岗位投入商业化项目 · 本地模型驱动成本可控</div>"
                + hub_card + secs
                + "<div class='dim' style='margin-top:20px'>聘请方式："
                "「在岗」岗位向 田丰 · 参谋长提任务单（飞书日报通道）· "
                "「即用」专项 AI 点入即用 · 货架源 portal/ai_market.json"
                "（上新改注册表即可）· 不构成投资建议</div></body></html>")
        self._html(body)
    def _audit_ai_page(self):
        """独立安全审计 AI 对话页（114e）：会话客户端持有，独立模型。"""
        mdl = self._audit_cfg()["model"]
        html = ("<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,initial-scale=1'>"
                "<title>🛡 安全审计 AI · 零界</title>"
                "<link rel='stylesheet' href='/static/tokens.css'>"
                "<style>body{font-family:'Segoe UI','Microsoft YaHei',system-ui,"
                "sans-serif;background:var(--bg-page);color:var(--text);}"
                "main{max-width:900px;margin:0 auto;padding:0 20px 40px;}"
                "#log{background:var(--bg-raised);border:1px solid var(--border);"
                "border-radius:12px;padding:14px 18px;min-height:300px;"
                "max-height:56vh;overflow-y:auto;font-size:13px;line-height:1.8;}"
                ".u{margin:8px 0;padding:8px 12px;border-radius:10px;"
                "background:var(--bg-surface);border:1px solid var(--border);}"
                ".a{margin:8px 0;padding:8px 12px;border-radius:10px;"
                "background:rgba(63,185,80,.06);border:1px solid rgba(63,185,80,.35);}"
                ".a b{color:var(--color-ok);}"
                "textarea{width:100%%;box-sizing:border-box;min-height:70px;"
                "background:var(--bg-surface);color:var(--text);"
                "border:1px solid var(--border);border-radius:10px;padding:10px;"
                "font-size:13px;font-family:inherit;resize:vertical;}"
                "button{padding:8px 18px;border-radius:9px;border:1px solid var(--border);"
                "background:var(--bg-raised);color:var(--text);cursor:pointer;font-size:13px;}"
                "button.pri{border-color:var(--link);color:var(--link);}"
                "button:hover{border-color:var(--link);}"
                ".num{font-family:Consolas,monospace;}</style></head><body>"
                "<header style='display:flex;justify-content:space-between;align-items:center;"
                "padding:20px 48px 10px;max-width:900px;margin:0 auto;'>"
                "<div><h1 style='font-size:17px;'>🛡 安全审计 AI</h1>"
                "<div style='font-size:10.5px;color:var(--text-dim);letter-spacing:2px;'>"
                "SECURITY AUDITOR · 独立对话 · 独立模型 <span class='num'>"
                + mdl + "</span></div></div>"
                "<a href='/devops' style='font-size:12px;'>← 指挥台</a></header><main>"
                "<div id='log'><div class='dim'>开发侧你好。贴代码让我审，或点「审计最近提交」。"
                "我只对安全负责：注入/越权/密钥泄露/危险调用/逻辑后门……有问题直说，没问题也直说。</div></div>"
                "<div style='margin-top:10px;display:flex;gap:8px;flex-wrap:wrap;'>"
                "<button onclick='auditCommit()' class='pri'>🔍 审计最近提交</button>"
                "<button onclick='document.getElementById(\"log\").innerHTML=log0;"
                "hist=[]'>🧹 清空会话</button></div>"
                "<div style='margin-top:10px;'>"
                "<textarea id='inp' placeholder='贴代码或描述要审的实现……'></textarea></div>"
                "<div style='margin-top:8px;display:flex;gap:8px;justify-content:flex-end;'>"
                "<button class='pri' onclick='send()'>发送 →</button></div>"
                "<div class='dim' id='st' style='font-size:11px;margin-top:8px;'>就绪</div>"
                "</main><script>"
                "const log0=document.getElementById('log').innerHTML;let hist=[];"
                "function esc(s){return String(s??'').replace(/[&<>]/g,"
                "c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}"
                "function push(cls,who,txt){const d=document.createElement('div');"
                "d.className=cls;d.innerHTML='<b>'+who+'</b><br>'+txt;"
                "const lg=document.getElementById('log');lg.appendChild(d);"
                "lg.scrollTop=lg.scrollHeight;return d;}"
                "async function call(payload,tag){"
                "const st=document.getElementById('st');st.textContent='审计官思考中…';"
                "try{const r=await fetch('/api/audit_chat',{method:'POST',"
                "headers:{'Content-Type':'application/json'},"
                "body:JSON.stringify(Object.assign({messages:hist},payload))});"
                "const d=await r.json();if(!d.ok)throw new Error(d.error||'HTTP '+r.status);"
                "hist.push({role:'assistant',content:d.reply});"
                "push('a','🛡 审计官 <span class=dim>('+tag+(d.backend?' · '+d.backend:'')+')</span>',"
                "esc(d.reply).replace(/\\n/g,'<br>'));"
                "}catch(e){push('a','🛡 审计官','<span style=\\'color:var(--color-err)\\'>'+"
                "esc(String(e))+'</span>');}"
                "st.textContent='就绪';}"
                "function send(){const i=document.getElementById('inp');"
                "const v=i.value.trim();if(!v)return;i.value='';"
                "hist.push({role:'user',content:v});"
                "push('u','👤 开发侧',esc(v).replace(/\\n/g,'<br>'));call({},'对话');}"
                "async function auditCommit(){push('u','👤 开发侧','🔍 请求审计最近一次提交');"
                "await call({preset:'last_commit'},'最近提交');}"
                "document.getElementById('inp').addEventListener('keydown',"
                "e=>{if(e.key==='Enter'&&(e.ctrlKey||e.metaKey))send();});"
                "</script></body></html>")
        self._html(html)
    def _api_audit_chat(self, raw):
        """POST {messages:[{role,content}...], preset?:'last_commit'} → {ok,reply}。
        preset=last_commit：服务端抓最近提交 diff 注入为审计素材。"""
        try:
            req = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            self._json({"ok": False, "error": "body 非 JSON"}, 400)
            return
        msgs = [{"role": "system", "content": self._AUDIT_SYS}]
        hist = req.get("messages") or []
        for m in hist[-12:]:  # 独立会话历史（客户端持有，限 12 轮防炸上下文）
            if isinstance(m, dict) and m.get("role") in ("user", "assistant") \
                    and m.get("content"):
                msgs.append({"role": m["role"],
                             "content": str(m["content"])[:6000]})
        if req.get("preset") == "last_commit":
            try:
                d = subprocess.run(
                    ["git", "show", "--format=commit %h %s", "--unified=3",
                     "HEAD"], cwd=ROOT, capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=15)
                msgs.append({"role": "user", "content":
                             "审计最近一次提交：\n```\n%s\n```"
                             % (d.stdout or "(空 diff)")[:12000]})
            except Exception as _ge:
                msgs.append({"role": "user",
                             "content": "抓取最近提交失败：%s" % _ge})
        try:
            reply, backend = self._audit_chat(msgs)
            self._json({"ok": True, "reply": reply, "backend": backend})
        except Exception as _oe:
            self._json({"ok": False,
                        "error": "审计模型不可达：%s" % str(_oe)[:160]}, 502)
    def _audit_chat(self, messages, timeout=150):
        """按回退链依次尝试审计模型（网关→本地→智谱），返回 (回复, 后端名)。"""
        import urllib.request as _u
        opener = _u.build_opener(_u.ProxyHandler({}))
        last_err = None
        for name, base, key, model in self._audit_backends():
            try:
                body = json.dumps({"model": model, "stream": False,
                                   "messages": messages}).encode("utf-8")
                headers = {"Content-Type": "application/json"}
                if name == "zhipu":
                    url = base.rstrip("/") + "/chat/completions"
                    headers["Authorization"] = "Bearer " + key
                elif name == "gateway":
                    url = base.rstrip("/") + "/chat/completions"
                    headers["Authorization"] = "Bearer " + key
                else:
                    url = base
                req = _u.Request(url, data=body, headers=headers)
                with opener.open(req, timeout=timeout) as resp:
                    j = json.loads(resp.read().decode("utf-8"))
                if name == "ollama":
                    text = j.get("message", {}).get("content", "")
                else:
                    if j.get("error"):
                        raise RuntimeError("网关错误：%s" % json.dumps(
                            j["error"], ensure_ascii=False)[:150])
                    text = (j.get("choices") or [{}])[0].get(
                        "message", {}).get("content", "")
                if text:
                    return text, "%s/%s" % (name, model)
                raise RuntimeError("空回复")
            except Exception as _e:
                last_err = "%s(%s): %s" % (name, model, str(_e)[:100])
        raise RuntimeError("全部后端不可达——" + str(last_err)[:300])
    def _audit_backends(self):
        """云边协同回退链：外部网关 → 本地 Ollama → 智谱（密钥见配置/env）。"""
        chain = []
        cfg = self._audit_cfg()
        if cfg["api"] == "openai":
            chain.append(("gateway", cfg["base"], cfg["key"], cfg["model"]))
        chain.append(("ollama", "http://127.0.0.1:11434/api/chat", None,
                      cfg.get("model") if cfg["api"] != "openai" else "qwen3.5:9b"))
        zk = cfg.get("zhipu_key") or os.environ.get("ZHIPUAI_API_KEY", "")
        if zk:
            chain.append(("zhipu", self._AUDIT_ZHIPU_BASE, zk, self._AUDIT_ZHIPU_MODEL))
        return chain
    def _audit_cfg(cls):
        """审计模型配置：audit_ai_config.json（不入库）> 环境变量 > 本机 Ollama。
        json: {"api":"openai","base":"https://…/v1","key":"sk-…","model":"…"}"""
        try:
            with open(cls._AUDIT_CFG_PATH, encoding="utf-8") as f:
                c = json.load(f)
            if c.get("base") and c.get("key") and c.get("model"):
                c.setdefault("api", "openai")
                return c
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "api_misc.py", exc)
        return {"api": "ollama",
                "model": os.environ.get("AUDIT_MODEL", "qwen3.5:9b")}

    # K-STOCK-352 AI 值班经理（paper 接待大厅 · 有问必答）：复用审计模型回退链
    # （网关→本地 Ollama→智谱），上下文=策略资产概况+前端随带的现场摘要。
    # 只读答疑：不下单、不改配置；qwen3.5 系 thinking 模型，回复剥离 <think> 块。
    _AI_DUTY_SYSTEM = (
        "你是「零界」模拟交易平台的 AI 值班经理。要求：①用客户语言，不说内部工程词；"
        "②亲切、具体、有下一步动作指引（如去哪个页签/点哪个卡）；③回复 200 字以内；"
        "④这是模拟盘，涉及投资判断只给信息不下结论。"
        "⑤指引只用真实入口，不得编造：页签=接待大厅→策略中心→交易员市场→持仓→历史；"
        "页面=回测中心 /bt2view、策略市场 /market-strategy。\n【平台现状】\n%s")

    def _today_context(self):
        """K-STOCK-353：值班经理当日动态上下文（今日审批单+账本概要+挂单/止损）。
        逐段 try/except 降级——任一数据源异常不拖垮整体；只报事实不下结论。"""
        out = []
        # ① 今日交易计划审批单（pf_001/pf_002 全扫，163 口径）
        try:
            _pdir = r"C:\项目\老田的AI助手\k_stock\data\plan_review"
            _today = time.strftime("%Y%m%d")
            _files = sorted(glob.glob(os.path.join(_pdir, _today + "_pf_*.json")))
            for _fp in _files:
                with open(_fp, encoding="utf-8") as _fh:
                    _rev = json.load(_fh)
                _ap, _rj = _rev.get("approved") or [], _rev.get("rejected") or []
                if _ap:
                    _names = "、".join(str(a.get("stock")) for a in _ap[:5])
                    out.append("今日计划(%s)：批准 %d 只（%s%s），驳回 %d 只。%s" % (
                        _rev.get("pf_id", "?"), len(_ap), _names,
                        "等" if len(_ap) > 5 else "", len(_rj),
                        str(_rev.get("summary") or "")[:80]))
                else:
                    out.append("今日计划(%s)：暂无批准条目，驳回 %d 只。" % (
                        _rev.get("pf_id", "?"), len(_rj)))
            if not _files:
                out.append("今日计划：审批单尚未生成。")
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "api_misc.py", exc)
        # ② 模拟仓账本概要（组合/挂单/止损哨兵，实数口径=页面一致）
        try:
            _ks = r"C:\项目\老田的AI助手\k_stock"
            if _ks not in sys.path:
                sys.path.insert(0, _ks)
            from paper.portal_status import status as _ps
            _d = _ps()
            if _d.get("ok"):
                _l = _d.get("latest") or {}
                out.append("账本快照 %s：总资产 %.0f 元，仓位 %.1f%%，累计收益 %.2f%%，现金 %.0f 元。" % (
                    _l.get("date"), _l.get("total") or 0, (_l.get("pos_pct") or 0) * 100,
                    _l.get("cum_pct") or 0, _l.get("cash") or 0))
                _pos = _d.get("positions") or []
                if _pos:
                    _seg = "、".join("%s %s%.0f 元" % (
                        p.get("stock"), "+" if (p.get("pnl") or 0) >= 0 else "",
                        p.get("pnl") or 0) for p in _pos[:6])
                    out.append("持仓 %d 只：%s%s。" % (
                        len(_pos), _seg, "等" if len(_pos) > 6 else ""))
                _pd = _d.get("pending_orders") or []
                if _pd:
                    _buy = sum(1 for x in _pd if x.get("side") == "BUY")
                    out.append("待撮合挂单 %d 笔（买 %d/卖 %d）。" % (
                        len(_pd), _buy, len(_pd) - _buy))
                _st = _d.get("stops") or []
                _hit = [s for s in _st if (s.get("distance_pct") or 9) < 0]
                if _st:
                    _near = sorted([s for s in _st if (s.get("distance_pct") or 9) >= 0],
                                   key=lambda x: x["distance_pct"])[:2]
                    _msg = "止损哨兵 %d 只在防。" % len(_st)
                    if _hit:
                        _msg += "⚠ %s 已跌破止损线！" % "、".join(
                            s.get("stock") for s in _hit)
                    elif _near:
                        _msg += "最接近触发：%s（距线 %.1f%%）。" % (
                            _near[0].get("stock"), _near[0]["distance_pct"])
                    out.append(_msg)
            else:
                out.append("账本：%s" % str(_d.get("error"))[:80])
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "api_misc.py", exc)
        return out

    def _api_ai_duty(self, raw):
        try:
            req = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            req = {}
        q = str(req.get("question") or "").strip()[:800]
        if not q:
            self._json({"ok": False, "error": "问题为空"}, 400)
            return
        ctx = []
        try:
            reg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "strategies.json")
            with open(reg_path, encoding="utf-8") as f:
                ss = (json.load(f).get("strategies")) or []
            by = {}
            for s in ss:
                by[s.get("status", "?")] = by.get(s.get("status", "?"), 0) + 1
            ctx.append("在册策略 %d 个：%s" % (
                len(ss), "；".join("%s %d 个" % (k, v) for k, v in by.items())))
            ctx.append("代表策略：" + "、".join(
                "%s（%s）" % (s.get("name"), s.get("status")) for s in ss[:6]))
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "api_misc.py", exc)
        uctx = str(req.get("ctx") or "").strip()[:1200]
        if uctx:
            ctx.append("客户当前所在：" + uctx)
        # K-STOCK-353：注入当日动态（审批单/账本/挂单/止损），让「今天注意什么」有据可答
        try:
            _today = self._today_context()
            if _today:
                ctx.append("【当日动态】\n" + "\n".join(_today))
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "api_misc.py", exc)
        sysmsg = self._AI_DUTY_SYSTEM % ("\n".join(ctx) or "（暂无）")
        try:
            reply, backend = self._audit_chat(
                [{"role": "system", "content": sysmsg},
                 {"role": "user", "content": q}], timeout=90)
            # qwen3.5 系 thinking 模型兜底：剥离 <think>…</think>（299 教训）
            import re as _re
            reply = _re.sub(r"<think>[\s\S]*?</think>", "", reply).strip()
            self._json({"ok": True, "reply": reply, "backend": backend})
        except Exception as oe:
            self._json({"ok": False,
                        "error": "值班经理暂时离线：%s" % str(oe)[:160]}, 502)

    # ==== K-STOCK-364 值班经理盘前简报（主动服务·复用当日上下文·零模型调用） ====

    def _duty_brief_phase(self):
        hhmm = time.strftime("%H%M")
        if hhmm < "0915":
            return "pre"
        if hhmm < "1500":
            return "open"
        return "closed"

    def _duty_brief_payload(self):
        """364：纯规则组装简报（只报事实不下结论，沿用 353 纪律）。
        headline 优先级：跌破止损警示 > 今日计划首条 > 首行。"""
        try:
            rows = self._today_context() or []
        except Exception:
            rows = []
        headline = ""
        for r in rows:
            if "跌破止损线" in r or "最接近触发" in r:
                headline = r
                break
        if not headline:
            for r in rows:
                if r.startswith("今日计划"):
                    headline = r
                    break
        if not headline and rows:
            headline = rows[0]
        wd = int(time.strftime("%w"))
        phase = self._duty_brief_phase()
        return {
            "ok": True,
            "date": time.strftime("%Y-%m-%d"),
            "weekday": "周" + "日一二三四五六"[wd],
            "phase": phase,
            "phase_label": {"pre": "盘前 · 正式简报 9:15 出件",
                            "open": "盘中", "closed": "已收盘"}[phase],
            "generated_at": time.strftime("%H:%M:%S"),
            "headline": headline,
            "rows": rows,
            "n": len(rows),
        }

    def _api_duty_brief(self, qs):
        """GET /api/duty_brief[?snapshot=1]。
        ?snapshot=1：9:15 出件任务调用——现算+原子落盘当日快照（357 判例 tmp+os.replace）；
        常规：9:15 后优先读当日快照（盘前时点口径），无快照/盘前则惰性现算。"""
        # K-STOCK-366 审查修复：列表段精确匹配（原 in qs 子串匹配会把
        # snapshot=12/10 误判为出件模式）；按 & 分段后整段比对。
        snap = "snapshot=1" in ((qs or "").split("&"))
        data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
        fp = os.path.join(data_dir, "duty_brief_%s.json" % time.strftime("%Y%m%d"))
        payload = None
        if snap:
            payload = self._duty_brief_payload()
            payload["snapshotted_at"] = time.strftime("%H:%M:%S")
            try:
                tmp = fp + ".tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, ensure_ascii=False)
                os.replace(tmp, fp)
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "api_misc.py", exc)
        elif os.path.exists(fp) and self._duty_brief_phase() != "pre":
            try:
                with open(fp, encoding="utf-8") as fh:
                    payload = json.load(fh)
                payload["from_snapshot"] = True
            except Exception:
                payload = None
        if payload is None:
            payload = self._duty_brief_payload()
        self._json(payload)

    def _devops_page(self):
        """开发指挥台页（108 原型落地版·只读）：四类计数 + 工单表 + 事件流。"""
        cards = "".join(
            "<div style='background:var(--bg-surface);border:1px solid var(--border);"
            "border-radius:10px;padding:12px 15px;'>"
            "<div style='font-size:11px;color:var(--text-dim);'>%s</div>"
            "<div style='font-size:22px;font-weight:700;margin-top:4px;' id='c-%s'>—</div></div>"
            % (label, key)
            for key, label in [("task_start", "🚀 建单"), ("commit", "📝 提交"),
                               ("merge_done", "✅ 合并"), ("blocked", "⛔ 卡壳")])
        html = ("<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,initial-scale=1'>"
                "<title>开发指挥台 · 零界</title>"
                "<link rel='stylesheet' href='/static/tokens.css'>"
                "<style>body{font-family:'Segoe UI','Microsoft YaHei',system-ui,sans-serif;"
                "background:var(--bg-page);color:var(--text);}"
                "main{max-width:1080px;margin:0 auto;padding:0 22px 46px;}"
                "table{width:100%%;border-collapse:collapse;font-size:12px;margin-top:8px;}"
                "th{color:var(--text-dim);text-align:left;padding:7px 9px;"
                "border-bottom:1px solid var(--border);font-weight:600;}"
                "td{padding:7px 9px;border-bottom:1px solid var(--border-weak);}"
                ".num{font-family:Consolas,monospace;}"
                ".bdg{font-size:10.5px;padding:2px 9px;border-radius:999px;border:1px solid var(--border);}"
                "</style></head><body>"
                "<header style='display:flex;justify-content:space-between;align-items:center;"
                "padding:20px 48px 10px;max-width:1080px;margin:0 auto;'>"
                "<div><h1 style='font-size:17px;'>🏭 开发指挥台</h1>"
                "<div style='font-size:10.5px;color:var(--text-dim);letter-spacing:2px;'>"
                "DEVOPS · K-STOCK-113 · 事件自动回流</div></div>"
                "<div style='display:flex;gap:14px;'>"
                "<a href='/audit-ai' style='font-size:12px;color:var(--color-ok);'>"
                "🛡 安全审计 AI</a>"
                "<a href='/' style='font-size:12px;'>← 返回首页</a></div></header><main>"
                "<div id='sum' style='display:grid;grid-template-columns:repeat(4,1fr);gap:10px;'>"
                + cards + "</div>"
                "<h2 style='font-size:14px;margin:22px 0 4px;'>在办工单</h2>"
                "<div style='background:var(--bg-raised);border:1px solid var(--border);"
                "border-radius:12px;padding:6px 14px 10px;'>"
                "<table><tr><th>工单</th><th>IDE / 模型</th><th>事件数</th><th>最近事件</th><th>最近动态</th><th>时间</th></tr>"
                "<tbody id='tasks'><tr><td class='dim'>加载中…</td></tr></tbody></table></div>"
                "<h2 style='font-size:14px;margin:22px 0 4px;'>Agent 工时榜 <span class='dim' "
                "style='font-size:11px;'>今日事件数排行</span></h2>"
                "<div id='board' style='background:var(--bg-raised);border:1px solid var(--border);"
                "border-radius:12px;padding:10px 16px;font-size:12px;'></div>"
                "<h2 style='font-size:14px;margin:22px 0 4px;'>事件流 <span class='dim' "
                "style='font-size:11px;'>最近 30 条 · 30s 自动刷新</span></h2>"
                "<div id='feed' style='background:var(--bg-surface);border:1px solid var(--border);"
                "border-radius:12px;padding:10px 16px;font-size:12px;line-height:2;'></div>"
                "</main><script>"
                "const KD={task_start:['🚀 建单','#58a6ff'],commit:['📝 提交','#8b949e'],"
                "blocked:['⛔ 卡壳','#f85149'],merge_done:['✅ 合并','#3fb950'],guard_fail:['🛑 守卫','#d29922']};"
                "async function load(){try{const r=await fetch('/api/devops',{cache:'no-store'});"
                "if(!r.ok)return;const d=await r.json();"
                "document.getElementById('c-task_start').textContent=d.by_kind.task_start||0;"
                "document.getElementById('c-commit').textContent=d.by_kind.commit||0;"
                "document.getElementById('c-merge_done').textContent=d.by_kind.merge_done||0;"
                "document.getElementById('c-blocked').textContent=d.by_kind.blocked||0;"
                "const es=Object.entries(d.tasks||{}).sort((a,b)=>(b[1].last||'').localeCompare(a[1].last||''));"
                "document.getElementById('tasks').innerHTML=es.map(([t,s])=>"
                "\"<tr><td class='num'>\"+t+\"</td>\""
                "+\"<td class='num' style='font-size:11px;'>\""
                "+((s.last_ide||s.last_model)?(s.last_ide||'—')+'<br><span class=dim>'+(s.last_model||'—')+'</span>':'<span class=dim>—</span>')+\"</td>\""
                "+\"<td class='num'>\"+s.events+\"</td>\""
                "+\"<td><span class='bdg'>\"+((KD[s.last_kind]||[s.last_kind])[0])+\"</span></td>\""
                "+\"<td style='max-width:340px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;'>\""
                "+(s.last_detail||'')+\"</td>\""
                "+\"<td class='num dim'>\"+(s.last||'').slice(11)+\"</td></tr>\").join('')"
                "||\"<tr><td class='dim'>今日暂无工单事件</td></tr>\";"
                "const ae=Object.entries(d.agents||{}).sort((a,b)=>b[1]-a[1]);"
                "const mx=ae.length?ae[0][1]:1;"
                "document.getElementById('board').innerHTML=ae.map(([a,c])=>"
                "\"<div style='display:flex;align-items:center;gap:10px;margin:5px 0;'>\""
                "+\"<span class='num' style='min-width:110px;'>\"+a+\"</span>\""
                "+\"<div style='flex:1;height:9px;border-radius:5px;background:var(--bg-surface);'>\""
                "+\"<div style='width:\"+Math.round(c*100/mx)+\"%;height:100%;border-radius:5px;"
                "background:linear-gradient(90deg,var(--blu-600),var(--link));'></div></div>\""
                "+\"<span class='num'>\"+c+\" 事件</span></div>\").join('')"
                "||\"<span class='dim'>今日暂无 Agent 事件</span>\";"
                "document.getElementById('feed').innerHTML=(d.recent||[]).map(x=>{const k=KD[x.kind]||[x.kind,'#8b949e'];"
                "return \"<div><span class='num dim'>\"+(x.ts||'').slice(11)+\"</span> \"+"
                "\"<b style='color:\"+k[1]+\"'>\"+k[0]+\"</b> \"+"
                "\"<span class='num'>\"+(x.task||'')+(x.agent?' · '+x.agent:'')+\"</span> \"+"
                "((x.ide||x.model)?\"<span class='bdg num' style='font-size:10px;'>\"+(x.ide||'?')+(x.model?' / '+x.model:'')+\"</span> \":\"\")+"
                "\"<span class='dim'>\"+(x.detail||'')+\"</span></div>\";}).join('')"
                "||\"<span class='dim'>暂无事件——提交一次代码试试（post-commit 自动上报）</span>\";"
                "}catch(e){}}load();setInterval(load,30000);</script></body></html>")
        self._html(html)
    def _devops_data(self):
        """今日开发态势：四类事件计数 + 活跃 Agent + 工单台账 + 最近事件。"""
        day = time.strftime("%Y-%m-%d")
        evs = []
        try:
            with open(self._DEV_EVENTS, "r", encoding="utf-8") as f:
                for ln in f:
                    try:
                        evs.append(json.loads(ln))
                    except Exception:
                        continue
        except OSError as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "api_misc.py", exc)
        today = [e for e in evs if e.get("day") == day]
        by_kind = {k: 0 for k in self._DEV_KINDS}
        agents = {}
        tasks = {}
        for e in today:
            by_kind[e.get("kind", "?")] = by_kind.get(e.get("kind", "?"), 0) + 1
            if e.get("agent"):
                agents[e["agent"]] = agents.get(e["agent"], 0) + 1
            t = e.get("task") or "(未编号)"
            st = tasks.setdefault(t, {"events": 0, "last": "", "last_kind": "",
                                      "last_detail": "", "last_ide": "",
                                      "last_model": ""})
            st["events"] += 1
            st["last"] = e.get("ts", "")
            st["last_kind"] = e.get("kind", "")
            st["last_detail"] = e.get("detail", "")
            st["last_ide"] = e.get("ide", "")
            st["last_model"] = e.get("model", "")
        return {"ok": True, "day": day, "total": len(today),
                "by_kind": by_kind, "agents": agents,
                "tasks": tasks, "recent": today[-30:][::-1]}
    def _qpzm_page(self, sub=""):
        """K-STOCK-032 多页面联动：主页=驾驶舱一屏（""），子页=
        res 打分信号 / rps RPS榜 / hub 四市场模块 / batch 批次回测 /
        spec 说明书——子页顶部页面级导航条互相联动。"""
        import html as _h
        e = _h.escape
        snap = _read_json(os.path.join(REPORTS_DIR, "rps_latest.json"))
        res = snap.get("res", [])

        # ① 环境闸门（一票否决层）
        gates = []
        for ev in snap.get("envs", []):
            mk = _env_market_key(ev.get("label", ""))
            if not mk:
                continue
            _raw = ev.get("safe")
            safe = bool(_raw)
            gates.append(
                "<a class='gate %s' href='/bt/m/%s'><b>%s</b>"
                "<span class='g-state' style='float:right;font-size:14px;"
                "font-weight:700'>%s</span>"
                "<div class='dim' style='margin-top:6px'>%s</div>"
                "<div style='margin-top:6px;font-size:12.5px;color:%s'>%s"
                "</div></a>"
                % (("g-na" if _raw is None else ("g-on" if safe else "g-off")),
                   mk, MKT_LABEL[mk],
                   e(str(ev.get("score", "—"))),
                   e(str(ev.get("state", ""))),
                   ("var(--color-gate-na,#5b7a99)" if _raw is None
                    else ("var(--color-gate-ok,#2f9e8f)" if safe
                          else "var(--color-gate-bad,#c98a1e)")),
                   ("— 缺维降权 · 不误杀" if _raw is None
                    else ("✅ 放行建仓" if safe else "⛔ 否决建仓"))))
        gates_html = ("<div class='gates'>%s</div>" % "".join(gates)
                      if gates else "<div class='dim'>暂无快照</div>")

        # ② 自选池打分信号（按 建议 → 综合分 排序，全量展示）
        # K-STOCK-031：全市场混排一张表 → 按 A股/美股/港股/虚拟币
        # 四市场 Tab 切换（长页治理续，与 RPS 榜/hub 模块同款交互）
        act_c = {"买入": "#f85149", "观察": "#d29922", "回避": "#3fb950"}
        order = {"买入": 0, "观察": 1, "回避": 2}

        def _base(r):
            return str(r.get("action", "")).replace("（环境闸门）", "")

        rows = sorted(res, key=lambda r: (order.get(_base(r), 3),
                                          -(r.get("score") or 0)))
        n_b = sum(1 for r in res if _base(r) == "买入")
        n_w = sum(1 for r in res if _base(r) == "观察")
        n_a = len(res) - n_b - n_w

        def _res_row(r):
            dims = " · ".join(
                "%s%s" % (lb, ("—" if r.get(k) is None
                               else "%d" % round(r[k])))
                for k, lb in (("tech", "技"), ("jev", "J"), ("rps", "R"),
                              ("env", "环")))
            return (
                "<tr><td><a href='/kg/stock/%s'>%s</a><div class='dim'>"
                "%s</div></td>"
                "<td style='color:%s;white-space:nowrap'>%s</td>"
                "<td><b>%d</b></td><td class='dim' style='font-size:12px;"
                "white-space:nowrap'>%s</td><td>%s</td>"
                "<td class='dim'>%s</td></tr>"
                % (e(str(r.get("code", ""))), e(str(r.get("name", ""))),
                   e(str(r.get("code", ""))),
                   act_c.get(_base(r), "#8b949e"),
                   e(str(r.get("action", "—"))),
                   round(r.get("score") or 0), dims,
                   ("%.2f" % r["px"]) if r.get("px") else "—",
                   e(str(r.get("date", "")))))

        _res_th = ("<tr><th>标的</th><th>建议</th><th>综合分</th>"
                   "<th>维度得分</th><th>现价</th><th>日期</th></tr>")
        _res_mkt = [("🇨🇳 A股", "CN"), ("🇺🇸 美股", "US"),
                    ("🇭🇰 港股", "HK"), ("🪙 虚拟币", "CRYPTO")]
        if res:
            res_tabs = []
            for lb, mk in _res_mkt:
                mrows = [r for r in rows if str(r.get("market", "")) == mk]
                mtrs = "".join(_res_row(r) for r in mrows) or \
                    ("<tr><td colspan='6' class='dim'>本市场今日暂无"
                     "（每日 17:35 刷新）</td></tr>")
                res_tabs.append((lb, "<table>%s%s</table>" % (_res_th, mtrs)))
            res_blk = (
                "<div class='dim' style='margin-bottom:4px'>全池 %d 只 · "
                "<span style='color:#f85149'>买入 %d</span> · <span style="
                "'color:#d29922'>观察 %d</span> · <span style='color:#3fb950'>"
                "回避 %d</span></div>"
                % (len(res), n_b, n_w, n_a))
            res_blk += _tabs_html("ressig", res_tabs)
            res_blk += (
                "<div class='dim' style='margin-top:6px'>维度：技=4策略多头占比"
                "±MA20修正 · J=近7天关联新闻多空 · R=全市场RPS · 环=市场闸门分"
                "；score≥70 买入 / 60-69 观察 / 其余回避；闸门收紧强制「回避（"
                "环境闸门）」；缺维自动降权（如无JEV：技.467 / R.333 / 环.20）"
                "</div>")
        else:
            res_blk = "<div class='dim'>暂无快照</div>"

        # ③ 全市场 RPS 强势榜（策略的选股漏斗出口）
        # K-STOCK-030：四市场竖排改 Tab 切换（长页治理——点右侧标签，
        # 不再往下滚四张表）
        top_lbl = {"CN": "🇨🇳 A股", "US": "🇺🇸 美股", "HK": "🇭🇰 港股",
                   "CRYPTO": "🪙 加密"}
        top_mkt = {"CN": "cn", "US": "us", "HK": "hk", "CRYPTO": "crypto"}
        _mk_order = ["CN", "US", "HK", "CRYPTO"]
        top_tabs = []
        for mk in _mk_order:
            trows = (snap.get("tops") or {}).get(mk)
            if not trows:
                continue
            trs = ""
            for t in trows[:8]:
                chg = t.get("chg20") or 0
                trs += (
                    "<tr><td>%s<div class='dim'>%s</div></td><td>%s</td>"
                    "<td><b>%.1f</b></td><td style='color:%s'>%+.1f%%</td>"
                    "</tr>"
                    % (e(str(t.get("sym", ""))), e(str(t.get("name", ""))),
                       ("%.2f" % t["px"]) if t.get("px") else "—",
                       float(t.get("mix") or 0),
                       "#f85149" if chg >= 0 else "#3fb950", float(chg)))
            top_tabs.append((
                top_lbl.get(mk, mk),
                "<div style='margin-top:4px'><b><a href='/bt/m/%s'>%s 强势"
                "榜 →</a></b><table><tr><th>代码 / 名称</th><th>现价</th>"
                "<th>RPS复合</th><th>20日</th></tr>%s</table></div>"
                % (top_mkt.get(mk, "cn"), e(top_lbl.get(mk, mk)), trs)))
        if top_tabs:
            top_blk = _tabs_html("rpsrank", top_tabs)
        else:
            top_blk = "<div class='dim'>暂无快照</div>"

        # ④ 策略说明书
        spec = (
            "<div class='dim' style='font-size:13px;line-height:2.1'>"
            "一句话：<b style='color:#e6edf3'>全市场动量分层 → RPS 强度筛选"
            " → 自选池多维加权打分 → 环境闸门一票否决</b><br>"
            "加权公式：技术×0.35 + JEV消息面×0.25 + RPS×0.25 + 环境×0.15"
            "（缺维按比例自动降权）<br>"
            "RPS 口径：全市场 N 日涨幅百分位(0-100)；A股 50/120/250 复合"
            "(0.4/0.3/0.3)，美股/加密 RPS50，港股 RPS50+120<br>"
            "数据：A股 tushare 日线 · 美股/港股/加密 zhice-db 全市场 · "
            "每日 17:35 刷新<br>"
            "与原版差异：原版四维为 技术.35/板块.30/RPS.20/环境.15，本站以"
            " JEV 消息面替代板块维度；方法论借鉴 D:\\青萍之末2，只读借鉴，"
            "数据与实现独立</div>")

        # ⑤ 验证与操作（纸面链路）
        tools = "".join(
            "<a class='tp' href='%s'>%s</a>" % (href, lb) for lb, href in [
                ("🚦 明日信号", "/bt-signals"),
                ("💼 持仓跟踪（纸面）", "/bt-positions"),
                ("📊 寻优榜单", "/bt-opt"),
                ("📄 回测报告", "/bt-report"),
                ("🧪 基础策略库", "/bt"),
            ])

        # ── K-STOCK-032 多页面联动（老田：不要长图，拆多页互相跳转）──
        # 主页=驾驶舱一屏（闸门+统计+模块入口卡）；打分/RPS/四市场模块/
        # 批次回测/说明书各自独立子页；子页顶部页面级导航条联动。
        _SUBS = [("🌊 工作台", "/bt-qpzm", ""),
                 ("🎯 打分信号", "/bt-qpzm/res", "res"),
                 ("📈 RPS 强势榜", "/bt-qpzm/rps", "rps"),
                 ("🗂 四市场模块", "/bt-qpzm/hub", "hub"),
                 ("📊 批次回测", "/bt-qpzm/batch", "batch"),
                 ("📖 策略说明书", "/bt-qpzm/spec", "spec")]

        def _shell(title, cur, inner):
            nav = "".join(
                ("<span style='%s'>%s</span>" % (_PILL_ON, lb)) if key == cur
                else ("<a href='%s' style='%s'>%s</a>" % (href, _PILL, lb))
                for lb, href, key in _SUBS)
            # K-STOCK-036：返回键置前 + 页面级联动导航条
            # K-STOCK-352 修复：_back_link_html 是 api_common 模块级零参函数（import 进类体后
            # self._xxx() 会把 self 当首参传入 → TypeError，/bt-qpzm 全页 500）。改直呼模块函数。
            nav = _back_link_html() + nav
            # 注意：title 走纯拼接不进 % 模板（% 与 + 混用坑第四次变体）
            return ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                    "<meta name='viewport' content='width=device-width,"
                    "initial-scale=1'><title>" + title
                    + " · 青萍之末</title>"
                    + self._COCKPIT_CSS + "</head><body>"
                    + pill_nav("qpzm")
                    + "<div style='display:flex;gap:6px;flex-wrap:wrap;"
                    "margin:12px 0'>" + nav + "</div>"
                    + "<h1>" + title + "</h1><div class='dim'>数据截至 "
                    + e(str(snap.get("latest", "—"))) + "（"
                    + e(str(snap.get("ts", ""))) + "）</div>"
                    + inner
                    + "<div class='dim' style='margin-top:20px'>纸面研究不"
                    "构成投资建议 · 方法论源自青萍之末2（RPS：全市场N日涨幅"
                    "百分位；打分：多维加权、缺维降权、环境一票否决）"
                    "</div></body></html>")

        def _card(href, border, icon, name, desc, stat):
            return ("<a href='%s' style='display:block;border:1px "
                    "solid %s;border-radius:12px;background:#161b22;"
                    "padding:16px 18px;text-decoration:none;"
                    "color:#e6edf3'>"
                    "<div style='font-size:17px;font-weight:700'>"
                    "%s %s</div>"
                    "<div class='dim' style='font-size:12.5px;"
                    "margin:6px 0 10px'>%s</div>"
                    "<div style='font-size:13px'><b>%s</b>"
                    "<span style='float:right;color:%s;font-size:"
                    "12.5px'>进入 →</span></div></a>"
                    % (href, border, icon, name, desc, stat, border))

        if sub == "res":
            inner = ("<div class='sec'><h2>🎯 自选池打分信号</h2>" + res_blk
                     + "</div>")
            self._html(_shell("🌊 打分信号 · 四市场", "res", inner))
            return
        if sub == "rps":
            inner = ("<div class='sec'><h2>📈 全市场 RPS 强势榜"
                     "（选股漏斗）</h2>" + top_blk + "</div>")
            self._html(_shell("📈 RPS 强势榜 · 四市场", "rps", inner))
            return
        if sub == "hub":
            inner = self._hub_market_tabs()
            self._html(_shell("🗂 四市场模块 · 自选池多维加权", "hub", inner))
            return
        if sub == "batch":
            inner = self._qpzm_batch_blk(snap)
            self._html(_shell("📊 每日批次回测", "batch", inner))
            return
        if sub == "spec":
            inner = "<div class='sec'><h2>📖 策略说明书</h2>" + spec + "</div>"
            self._html(_shell("📖 策略说明书", "spec", inner))
            return

        # 主页 = 驾驶舱一屏：闸门 → 核心统计 → 模块入口卡（联动枢纽）
        hub_txt = "跨市场 RPS · 策略库 · 策略工具"
        cards = "".join([
            _card("/bt-qpzm/res", "#f85149", "🎯", "打分信号",
                  "自选池多维加权 · 建议→综合分排序 · 四市场 Tab",
                  "买入 %d · 观察 %d · 回避 %d" % (n_b, n_w, n_a)),
            _card("/bt-qpzm/rps", "#58a6ff", "📈", "RPS 强势榜",
                  "策略的选股漏斗出口 · 四市场各 Top8",
                  "全市场 N 日涨幅百分位"),
            _card("/bt-qpzm/hub", "#e3b341", "🗂", "四市场模块",
                  "四法门共振按市场分域 · 同源市场分域页",
                  hub_txt),
            _card("/bt-qpzm/batch", "#3fb950", "📊", "每日批次回测",
                  "K238 每日选股批次的历史回放对照",
                  "随每日任务积累"),
            _card("/bt-qpzm/spec", "#8b949e", "📖", "策略说明书",
                  "加权公式 · RPS 口径 · 与原版差异",
                  "评分权重一览"),
        ])
        inner = (
            # ② 工具链统一紧贴标题
            "<div style='display:flex;gap:6px;flex-wrap:wrap;"
            "margin:10px 0'>" + tools + "</div>"
            # ③ 环境闸门（一票否决层，决策第一眼）
            + "<div class='sec'><h2>🚦 环境闸门 · 一票否决层</h2>"
            + gates_html + "</div>"
            # 核心统计条
            + "<div class='sec'><h2>🎯 自选池速览</h2><div class='dim' "
            "style='font-size:15px'>全池 <b>%d</b> 只 · "
            "<span style='color:#f85149'>买入 <b>%d</b></span> · "
            "<span style='color:#d29922'>观察 <b>%d</b></span> · "
            "<span style='color:#3fb950'>回避 <b>%d</b></span>"
            "（明细见「打分信号」子页）</div></div>"
            % (len(res), n_b, n_w, n_a)
            # 模块入口卡（联动枢纽）
            + "<h2 style='margin:16px 0 10px'>🧭 板块入口 · 多页联动</h2>"
            + "<div style='display:grid;grid-template-columns:repeat("
            "auto-fit,minmax(260px,1fr));gap:12px'>" + cards + "</div>")
        self._html(_shell("🌊 青萍之末 · 多维加权选股工作台", "", inner))
