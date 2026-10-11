# -*- coding: utf-8 -*-
"""P4 批 2：桥接域 Mixin（bridge 决策/预览/动作族）。
消费方显式 import（禁 import *）。
"""
import json, os, subprocess, sys, time
from api_common import (
    log_exc,
    QBT_DIR,
    STATION,
    ROOT,
    DATA_SYNC_CMDS,
    DATA_SYNC_JOBS,
    _DATA_LOCK,
    _FRESH_BG_LOCK,
    _FRESH_BG_ERROR,
    data_freshness_bg,
    data_sync_start,
    _read_json,
    _env_market_key,
    pill_nav,
    _TP_JS,
    _TP_MKT,
    _tp_row)


BRIDGE_SCAN_TASKS = {"chanlun": "ChanlunScan", "weak": "WeakScan",
                     "nine": "NineDimScan"}


def bridge_table_preview(table, n=8):
    """K56 数据穿透：datamart.table_preview 门户包装（异常兜底为 error）。"""
    import sys as _sy
    if QBT_DIR not in _sy.path:
        _sy.path.insert(0, QBT_DIR)
    import datamart as _dm
    try:
        return _dm.table_preview(table, n)
    except Exception as ex:
        return {"error": str(ex), "table": str(table)[:40]}


def bridge_summary():
    """K56-B1 驾驶舱聚合：数据顶板/运行仪表/预警仪/图台账/账户态势。

    只读不写；freshness 走 5min 缓存（复用 data_freshness）；level 汇总：
    r=空表或 bad 任务（今日必须处理）y=lag/待跟进/需注意（知晓）g=安心。"""
    import time as _t
    today = _t.strftime("%Y-%m-%d")
    out = {"ts": _t.strftime("%Y-%m-%d %H:%M"), "today": today,
           "freshness": [], "expect": None, "services": {}, "docker": {},
           "tasks": {"bad": []}, "weak": {}, "health_ts": "", "collectors": {},
           "runs": [], "alerts": [], "graphs": [], "accounts": [],
           "nine_alerts": 0, "watch_note": 0, "chanlun_today": 0,
           "watch_list": [], "nine_list": []}
    try:
        fr = data_freshness_bg(1800)
        if fr:
            out["freshness"] = fr.get("rows") or []
            out["expect"] = fr.get("expect")
        else:
            out["warming"] = True
    except Exception:
        log_exc("bridge_summary:freshness")   # P0-5
    # 体检快照：六服务灯 + bad 任务 + 弱案例统计（pending/open/closed）
    try:
        with open(os.path.join(STATION, "ops", "health_latest.json"),
                  encoding="utf-8") as f:
            h = json.load(f)
        out["services"] = h.get("services") or {}
        out["docker"] = h.get("docker") or {}
        out["tasks"] = h.get("tasks") or {}
        out["weak"] = h.get("data") or {}
        out["health_ts"] = h.get("ts") or ""
    except Exception:
        log_exc("bridge_summary:health_latest.json")   # P0-5
    # 六源采集状态（news_collector/status.json：last_ok + 每源 ok/new）
    try:
        with open(os.path.join(STATION, "news_collector", "status.json"),
                  encoding="utf-8") as f:
            cs = json.load(f)
        out["collectors"] = {
            "last_collect": cs.get("last_collect") or "",
            "last_ok": cs.get("last_ok"),
            "news_24h": cs.get("news_24h"),
            "sources": {k: {"ok": (v or {}).get("ok"),
                            "new": (v or {}).get("new")}
                        for k, v in (cs.get("sources") or {}).items()}}
    except Exception:
        log_exc("bridge_summary:collectors(status.json)")   # P0-5
    # 运行仪表：投委会当日运行段（council_run.log ===== today ===== 块）
    try:
        seg, cur = [], None
        with open(os.path.join(QBT_DIR, "reports", "council_run.log"),
                  encoding="utf-8") as f:
            for line in f:
                ls = line.strip()
                if ls.startswith("====="):
                    cur = [] if today in ls else None
                    if cur is not None:
                        seg = cur
                    continue
                if cur is not None and ls:
                    cur.append(ls)
        out["runs"] = seg
    except Exception:
        log_exc("bridge_summary:council_run.log")   # P0-5
    # 运行仪表：跑批当日摘要行（PORTFOLIO nav 行，尾部 8 行）
    try:
        po = os.path.join(QBT_DIR, "logs", "portfolio_task.out")
        if (os.path.exists(po) and _t.strftime(
                "%Y-%m-%d", _t.localtime(os.path.getmtime(po))) == today):
            with open(po, encoding="utf-8") as f:
                out["runs"] = [x.strip() for x in f
                               if x.strip().startswith("PORTFOLIO")][-8:] + out["runs"]
    except Exception:
        log_exc("bridge_summary:portfolio_task.out")   # P0-5
    # 图台账：缠论池尾 15 票 + 当日扫描计数（first_seen 以当日开头）
    try:
        tail = []
        with open(os.path.join(QBT_DIR, "data", "chanlun_pool.jsonl"),
                  encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    log_exc("bridge_summary:chanlun_pool.jsonl 行解析")   # P0-5
                    continue
                if str(r.get("first_seen") or "").startswith(today):
                    out["chanlun_today"] += 1
                tail.append(r)
        out["graphs"] = tail[-15:][::-1]
    except Exception:
        log_exc("bridge_summary:chanlun_pool.jsonl")   # P0-5
    # 预警仪：九维警报池（band 为字符串 alert/alert2，按 code 去重取
    # 最新；history 未生成时回退九维快照 scores.json 同口径行）
    try:
        last = {}
        hp = os.path.join(QBT_DIR, "data", "nine_dim_history.jsonl")
        if os.path.exists(hp):
            with open(hp, encoding="utf-8") as f:
                for line in f:
                    try:
                        r = json.loads(line)
                    except Exception:
                        log_exc("bridge_summary:nine_dim_history 行解析")   # P0-5
                        continue
                    if r.get("band") in ("alert", "alert2") \
                            or r.get("pool_type") == "alert":
                        last[r.get("code") or "?"] = r
        else:
            with open(os.path.join(QBT_DIR, "data",
                                   "nine_dim_scores.json"),
                      encoding="utf-8") as f:
                sc = json.load(f)
            for r in (sc.get("rows") or []):
                if r.get("band") in ("alert", "alert2"):
                    r = dict(r)
                    r.setdefault("dt", sc.get("trade_date"))
                    last[r.get("code") or "?"] = r
        out["nine_alerts"] = len(last)
        out["nine_list"] = sorted(last.values(),
                                  key=lambda x: -(x.get("score") or 0))[:10]
    except Exception:
        log_exc("bridge_summary:nine_dim")   # P0-5
    # 预警仪：盯盘会需注意（council_watch 最新 5 条 watch）
    try:
        wl = []
        with open(os.path.join(QBT_DIR, "reports", "council_watch.jsonl"),
                  encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    log_exc("bridge_summary:council_watch 行解析")   # P0-5
                    continue
                if r.get("type") == "watch":
                    wl.append(r)
        # 需注意级别在 judge.level（LLM 解析失败降级行无 judge，不计）
        out["watch_note"] = sum(
            1 for r in wl
            if str((r.get("judge") or {}).get("level") or "") == "需注意")
        out["watch_list"] = wl[-5:][::-1]
    except Exception:
        log_exc("bridge_summary:council_watch")   # P0-5
    # 账户态势：4 模拟盘 nav/持仓
    try:
        with open(os.path.join(QBT_DIR, "reports", "portfolio.json"),
                  encoding="utf-8") as f:
            pf = json.load(f)
        for sid, a in (pf.get("accounts") or {}).items():
            out["accounts"].append({
                "sid": sid, "icon": a.get("icon", ""), "name": a.get("name", sid),
                "open": len(a.get("open") or []), "cash": a.get("cash", 0),
                "capital": a.get("capital", 0), "nav": a.get("nav"),
                "ps": a.get("ps"), "pnl_pct": a.get("pnl_pct")})
    except Exception:
        log_exc("bridge_summary:portfolio.json 账户")   # P0-5
    # 汇总：分级横幅（红=必须处理 黄=知晓 绿=安心）
    reds = [r.get("table") for r in out["freshness"]
            if r.get("status") == "empty"]
    lags = [r.get("table") for r in out["freshness"]
            if str(r.get("status") or "").startswith("lag")]
    bad = out["tasks"].get("bad") or []
    out["red_tables"] = reds
    out["lag_tables"] = lags
    out["n_red"] = len(reds) + len(bad)
    out["n_yellow"] = (len(lags) + int(bool(out["weak"].get("pending")))
                       + int(bool(out["watch_note"])) + int(bool(out["nine_alerts"])))
    out["level"] = ("r" if (reds or bad)
                    else ("y" if out["n_yellow"] else "g"))
    return out


def bridge_decisions():
    """K56-B2 决策层聚合：四会决策流时间线（立案/盯盘/复盘按 ts 合并）
    + 影子对照（分歧度分桶 + 前向收益样本）。只读不写；views 观点原文
    随记录携带，供舱页 <details> 展开（点节点看该会结论+角色观点）。"""
    out = {"props": [], "watches": [], "reviews": [], "samples": [],
           "gate": {}, "by_dissent": [], "shadow_updated": ""}
    try:
        with open(os.path.join(QBT_DIR, "reports",
                               "council_proposals.jsonl"),
                  encoding="utf-8") as f:
            for line in f:
                try:
                    out["props"].append(json.loads(line))
                except Exception:
                    log_exc("bridge_decisions:council_proposals 行解析")   # P0-5
    except Exception:
        log_exc("bridge_decisions:council_proposals.jsonl")   # P0-5
    out["props"] = out["props"][-20:][::-1]
    try:
        with open(os.path.join(QBT_DIR, "reports",
                               "council_watch.jsonl"), encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    log_exc("bridge_decisions:council_watch 行解析")   # P0-5
                    continue
                if r.get("type") == "watch":
                    out["watches"].append(r)
    except Exception:
        log_exc("bridge_decisions:council_watch.jsonl")   # P0-5
    out["watches"] = out["watches"][-20:][::-1]
    try:
        with open(os.path.join(QBT_DIR, "reports",
                               "council_reviews.jsonl"),
                  encoding="utf-8") as f:
            for line in f:
                try:
                    out["reviews"].append(json.loads(line))
                except Exception:
                    log_exc("bridge_decisions:council_reviews 行解析")   # P0-5
    except Exception:
        log_exc("bridge_decisions:council_reviews.jsonl")   # P0-5
    out["reviews"] = out["reviews"][-10:][::-1]
    try:
        with open(os.path.join(QBT_DIR, "reports",
                               "council_shadow_stats.json"),
                  encoding="utf-8") as f:
            st = json.load(f)
        out["samples"] = st.get("samples") or []
        out["gate"] = st.get("gate") or {}
        out["by_dissent"] = st.get("by_dissent") or []
        out["shadow_updated"] = st.get("updated") or ""
    except Exception:
        log_exc("bridge_decisions:council_shadow_stats.json")   # P0-5
    return out


def bridge_actions_index():
    """K56-B3 人工动作留痕索引：council_actions.jsonl（append-only 第四张表）
    prop_ts → 最新动作记录；文件缺省空。"""
    idx = {}
    try:
        with open(os.path.join(QBT_DIR, "reports",
                               "council_actions.jsonl"),
                  encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                idx[r.get("prop_ts") or ""] = r
    except Exception:
        pass
    return idx


def bridge_act(cmd, text=""):
    """K56-B3 指挥台动作执行（严格白名单，无 shell 拼接）：
    agree:/reject:/note:<prop_ts> 三动作 append-only 留痕；
    sync:<src> 复用 data_sync_start 单实例后台；scan:<k> 计划任务 /run。
    观察类指令一键直达，资金动作不在词表（走 /screen 既有闸门）。"""
    cmd = (cmd or "").strip()
    if cmd.startswith(("agree:", "reject:", "note:")):
        act, _, key = cmd.partition(":")
        prop = None
        try:
            with open(os.path.join(QBT_DIR, "reports",
                                   "council_proposals.jsonl"),
                      encoding="utf-8") as f:
                for line in f:
                    try:
                        r = json.loads(line)
                    except Exception:
                        continue
                    if r.get("ts") == key:
                        prop = r
        except Exception:
            pass
        if prop is None:
            return {"ok": False, "msg": "备忘录不存在（ts=%s）" % key}
        if act == "note" and not (text or "").strip():
            return {"ok": False, "msg": "备注内容为空"}
        rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
               "d": time.strftime("%Y-%m-%d"), "prop_ts": key,
               "code": prop.get("code"), "name": prop.get("name"),
               "sid": prop.get("sid"), "action": act,
               "text": (text or "").strip()[:200]}
        ap = os.path.join(QBT_DIR, "reports", "council_actions.jsonl")
        with open(ap, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        cn = {"agree": "同意立案", "reject": "否决",
              "note": "人工备注"}[act]
        return {"ok": True,
                "msg": "已留痕：%s — %s %s" % (
                    cn, rec["code"], ("「%s」" % rec["text"]) if act == "note" else "")}
    if cmd.startswith("sync:"):
        src = cmd.split(":", 1)[1]
        r = data_sync_start(src)
        if r.get("started"):
            return {"ok": True, "msg": "已在后台启动 %s 同步（pid %s，"
                                       "日志 portal_data_%s.log）"
                                 % (src, r.get("pid"), src)}
        return {"ok": False, "msg": "未启动：%s" % r.get("error")}
    if cmd.startswith("scan:"):
        k = cmd.split(":", 1)[1]
        tn = BRIDGE_SCAN_TASKS.get(k)
        if not tn:
            return {"ok": False, "msg": "未知扫描 %s" % k}
        try:
            p = subprocess.run(["schtasks", "/run", "/tn", tn],
                               capture_output=True, text=True, timeout=30)
        except Exception as ex:
            return {"ok": False, "msg": "触发失败：%s" % ex}
        if p.returncode == 0:
            return {"ok": True, "msg": "已触发计划任务 %s 立即运行" % tn}
        return {"ok": False, "msg": "触发失败 rc=%d：%s"
                               % (p.returncode,
                                  (p.stderr or p.stdout or "").strip()[:200])}
    return {"ok": False, "msg": "未知指令"}


def bridge_act_desc(cmd, text=""):
    """确认页文案（白名单预检）：返回 (描述, 级别) 或 None。
    资金动作刻意不在词表——舱是人的手，钱走 /screen 既有确认流程。"""
    cmd = (cmd or "").strip()
    if cmd.startswith(("agree:", "reject:", "note:")):
        act, _, key = cmd.partition(":")
        prop = None
        try:
            with open(os.path.join(QBT_DIR, "reports",
                                   "council_proposals.jsonl"),
                      encoding="utf-8") as f:
                for line in f:
                    try:
                        r = json.loads(line)
                    except Exception:
                        continue
                    if r.get("ts") == key:
                        prop = r
        except Exception:
            pass
        if prop is None:
            return None
        memo = prop.get("memo") or {}
        cn = {"agree": "✅ 同意立案（人工背书，留痕）",
              "reject": "⛔ 否决（留痕，供转正统计人工否决率）",
              "note": "📝 人工备注回流（留痕，供记忆闭环检索）"}[act]
        return ("%s\n标的：%s %s（%s 备忘录 · %s · 分歧 %s）"
                "%s" % (cn, prop.get("code"), prop.get("name"),
                        prop.get("d"), memo.get("verdict"),
                        prop.get("dissent"),
                        ("\n备注：「%s」" % text.strip()[:200]
                         if act == "note" else "")), "y")
    if cmd.startswith("sync:"):
        src = cmd.split(":", 1)[1]
        if src not in DATA_SYNC_CMDS:
            return None
        return ("🔄 后台启动 %s 同步（单实例；已在跑则拒绝；"
                "结束后 freshness 缓存自动失效）" % src, "g")
    if cmd.startswith("scan:"):
        k = cmd.split(":", 1)[1]
        tn = BRIDGE_SCAN_TASKS.get(k)
        if not tn:
            return None
        return ("🛰 触发计划任务 %s 立即运行（不影响夜间排程）" % tn, "g")
    return None


