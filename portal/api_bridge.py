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


# ══ Handler 方法壳（经 MRO 并入基座，批2补录）══

class Handler:
    def _bridge_act_page(self, qs):
        """K56-B3 两步确认页 /bridge/act（零 JS 闸门）：confirm=1 执行
        并 3s 回舱；缺省黄页确认（note 先填备注表单）。资金动作不在
        白名单——这是指挥舱的闸门纪律。"""
        import html as _h
        from urllib.parse import parse_qs as _pqs, quote_plus as _qp
        e = _h.escape
        q = _pqs(qs or "")
        cmd = (q.get("cmd") or [""])[0]
        text = (q.get("text") or [""])[0]
        confirm = (q.get("confirm") or ["0"])[0] == "1"
        desc = bridge_act_desc(cmd, text)
        back = ("<a href='/bridge' style='color:var(--blu-500);"
                "text-decoration:none;font-size:12px'>← 返回驾驶舱</a>")
        if desc is None:
            body = ("<div style='color:var(--red-500);font-size:14px;"
                    "font-weight:700;margin:20px 0 8px'>⛔ 未知指令</div>"
                    "<div style='color:var(--gray-5);font-size:12px'>cmd=%s "
                    "不在指挥台白名单</div>%s"
                    % (e(cmd[:80] or "(空)"), back))
        elif confirm:
            r = bridge_act(cmd, text)
            col = "#3fb950" if r.get("ok") else "#f85149"
            body = ("<div style='color:%s;font-size:14px;font-weight:700;"
                    "margin:20px 0 8px'>%s %s</div>%s"
                    % (col, "✅" if r.get("ok") else "❌",
                       e(str(r.get("msg"))), back))
            head = ("<meta http-equiv='refresh' content='3;url=/bridge'>"
                    if r.get("ok") else "")
            self._html("<!DOCTYPE html><html><head><meta charset='utf-8'>%s"
                       "<title>指挥台 · 执行</title></head><body "
                       "style='background:var(--gray-0);margin:0;padding:12px 16px'>"
                       "%s%s</body></html>"
                       % (head, self._COCKPIT_CSS, body))
            return
        else:
            d, lv = desc
            note_form = ""
            if cmd.startswith("note:"):
                note_form = ("<form action='/bridge/act' method='get' "
                             "style='margin:10px 0'><input type='hidden' "
                             "name='cmd' value='%s'>"
                             "<input name='text' placeholder='备注内容"
                             "（≤200 字，将回流记忆闭环）' value='%s' "
                             "style='width:420px;padding:6px 8px;"
                             "background:var(--gray-0);color:var(--gray-7);"
                             "border:1px solid var(--gray-4);border-radius:6px'> "
                             "<button style='padding:6px 14px;background:"
                             "var(--gray-3);color:var(--blu-500);border:1px solid "
                             "var(--gray-4);border-radius:6px;cursor:pointer'>"
                             "下一步 →</button></form>"
                             % (e(cmd), e(text[:200])))
                go = ("<a href='/bridge/act?cmd=%s&text=%s&confirm=1' "
                      "style='display:inline-block;padding:6px 18px;"
                      "background:var(--gray-3);color:var(--grn-500);border:1px solid "
                      "var(--grn-500);border-radius:6px;text-decoration:none;"
                      "font-weight:600'>✅ 确认执行</a>" % (e(cmd), _qp(text[:200])))
            else:
                go = ("<a href='/bridge/act?cmd=%s&confirm=1' style='"
                      "display:inline-block;padding:6px 18px;background:"
                      "var(--gray-3);color:var(--grn-500);border:1px solid var(--grn-500);"
                      "border-radius:6px;text-decoration:none;font-weight:600'"
                      ">✅ 确认执行</a>" % e(cmd))
            body = ("<div style='color:var(--ylw-500);font-size:14px;"
                    "font-weight:700;margin:20px 0 8px'>⚠ 指挥台 · 执行确认"
                    "</div><div style='white-space:pre-wrap;color:var(--gray-7);"
                    "font-size:13px;line-height:1.8;background:var(--gray-2);"
                    "border:1px solid var(--gray-4);border-radius:8px;"
                    "padding:12px 14px;max-width:560px'>%s</div>%s"
                    "<div style='margin:12px 0'>%s &nbsp; %s</div>"
                    % (e(d), note_form, go, back))
        self._html("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                   "<title>指挥台 · 确认</title></head><body "
                   "style='background:var(--gray-0);margin:0;padding:12px 16px'>"
                   "%s%s</body></html>" % (self._COCKPIT_CSS, body))
    def _bridge_page(self, qs):
        """K56-B1 投研驾驶舱 /bridge：顶板五仪——数据顶板（15 表+服务灯）/
        运行仪表（今日跑批+投委会）/预警仪（bad 任务+待跟进+警报池+需注意）/
        结构图台账（缠论池近 15 票）/账户态势（4 模拟盘）。服务端渲染，只读；
        每条数据绑下钻目的地（图即导航），横幅红黄绿分级提醒。"""
        import html as _h
        e = _h.escape
        s = bridge_summary()
        _TB = ("border-collapse:collapse;width:100%;font-size:12px;"
               "font-family:Consolas,monospace")
        _TD = "border:1px solid #30363d;padding:3px 8px;color:#e6edf3"
        _H2 = ("color:#e6edf3;font-size:13px;font-weight:600;"
               "margin:14px 0 6px;border-left:3px solid #58a6ff;"
               "padding-left:8px")

        def _sec(title, href, hint):
            return ("<div style='%s'>%s <a href='%s' style='color:var(--blu-500);"
                    "font-size:11px;text-decoration:none'>进详情 →</a>"
                    "<span style='color:var(--gray-5);font-size:11px;"
                    "margin-left:8px'>%s</span></div>"
                    % (_H2, title, href, hint))

        # ---- 舱头横幅：红黄绿分级 + 红黄计数（w=预热中）----
        warming = bool(s.get("warming"))
        lv = "w" if warming else s.get("level", "g")
        col, txt = {"r": ("#f85149", "红 · 今日必须处理"),
                    "y": ("#d29922", "黄 · 知晓即可"),
                    "g": ("#3fb950", "绿 · 安心"),
                    "w": ("#58a6ff", "⏳ 时点校对预热中")}[lv]
        reds = s.get("red_tables") or []
        lags = s.get("lag_tables") or []
        bad = s.get("tasks", {}).get("bad") or []
        detail = []
        if reds:
            detail.append("空表: " + ", ".join(str(x) for x in reds[:5]))
        if bad:
            detail.append("坏任务: " + ", ".join(
                str(b.get("name")) for b in bad[:5]))
        if lags:
            detail.append("滞后: " + ", ".join(str(x) for x in lags[:5]))
        detail_txt = ("15 表时点校对装载中（~100s）· 页面 30s 自动刷新"
                      if warming
                      else ("；".join(detail) if detail
                            else "各表在容忍内，无坏任务"))
        banner = ("<div style='border:1px solid %s;border-radius:10px;"
                  "padding:10px 14px;margin:10px 0;display:flex;"
                  "justify-content:space-between;align-items:center;"
                  "background:var(--gray-2)'>"
                  "<div><b style='color:%s;font-size:15px'>● %s</b>"
                  "<span style='color:var(--gray-5);font-size:12px;"
                  "margin-left:10px'>数据期望日 %s · 体检 %s · 聚合 %s</span>"
                  "<div style='color:var(--gray-5);font-size:11px;margin-top:3px'>%s</div></div>"
                  "<div style='font-size:13px;color:var(--gray-7);white-space:nowrap'>"
                  "🔴 %d &nbsp; 🟡 %d</div></div>"
                  % (col, col, txt, e(str(s.get("expect") or "?")),
                     e(str(s.get("health_ts") or "?")),
                     e(str(s.get("ts") or "?")),
                     e(detail_txt),
                     s.get("n_red", 0), s.get("n_yellow", 0)))

        # ---- 仪1 数据顶板：15 表新鲜度 + 六服务灯（🔍 穿透到数据行）----
        rows = ["<table style='%s'><tr>" % _TB
                + "".join("<th style='%s;background:var(--gray-3)'>%s</th>"
                          % (_TD, x)
                          for x in ("表", "市场", "最新", "状态"))
                + "</tr>"]
        if warming:
            rows.append("<tr><td style='%s;color:var(--blu-500)' colspan='4'>"
                        "⏳ 时点校对冷算中（约 100s）——完成后本页自动刷新，"
                        "其余四仪已可用</td></tr>" % _TD)
        for r in s.get("freshness", []):
            st = str(r.get("status") or "")
            sc = ("#3fb950" if st == "ok"
                  else ("#f85149" if st == "empty" else "#d29922"))
            tb = str(r.get("table") or "")
            rows.append(
                "<tr><td style='%s'>"
                "<span style='cursor:pointer;margin-right:6px' "
                "title='穿透看数据' onclick=\"tp_toggle('%s')\">🔍</span>"
                "<a href='/bt-data-health' "
                "style='color:var(--blu-500);text-decoration:none'>%s</a></td>"
                "<td style='%s;color:var(--gray-5)'>%s</td>"
                "<td style='%s'>%s</td>"
                "<td style='%s;color:%s'>%s</td></tr>"
                % (_TD, e(tb), e(tb), _TD,
                   e(_TP_MKT.get(tb, str(r.get("group") or ""))), _TD,
                   e(str(r.get("latest") or "—")), _TD, sc, e(st))
                + _tp_row(tb))
        rows.append("</table>")
        svcs = s.get("services") or {}
        lights = " ".join(
            "<a href='/sys-health' style='text-decoration:none;"
            "font-size:12px;color:%s;margin-right:10px'>●%s</a>"
            % ("#3fb950" if v else "#f85149", e(str(k)))
            for k, v in svcs.items()) or "<span style='color:var(--gray-5)'>—</span>"
        dk = s.get("docker") or {}
        _cs = s.get("collectors") or {}
        _csrc = _cs.get("sources") or {}
        _cdots = " ".join(
            "<span style='font-size:11px;color:%s;margin-right:6px'>"
            "●%s%s</span>"
            % ("#3fb950" if v.get("ok") else "#f85149", e(str(k)),
               (" +%s" % v["new"]) if v.get("new") else "")
            for k, v in _csrc.items())
        _cline = (("<div style='margin:4px 0 0;font-size:11px;"
                   "color:var(--gray-5)'>采集 %s%s · 末次 %s · 24h %s 条</div>"
                   % (_cdots,
                      "" if _cs.get("last_ok") else " · 采集异常!",
                      e(str(_cs.get("last_collect") or "?")),
                      e(str(_cs.get("news_24h") or "?"))))
                  if _cs else "")
        m1 = (banner
              + _sec("① 数据顶板 · 核心表新鲜度", "/bt-data-health",
                     "点表名进数据仓；点灯进系统体检")
              + "".join(rows)
              + "<div style='margin:8px 0'>%s"
                "<span style='color:var(--gray-5);font-size:11px;"
                "margin-left:10px'>docker %s 容器%s</span>%s</div>"
              % (lights, dk.get("total", "?"),
                 "" if dk.get("ai_portal") else " · ai_portal!", _cline))

        # ---- 仪2 运行仪表：今日跑批+投委会 ----
        runs = s.get("runs") or []
        m2 = (_sec("② 运行仪表 · 今日运行", "/council", "跑批+四会+扫描计数")
              + ("<div style='color:var(--gray-5);font-size:12px'>今日无运行记录"
                 "</div>" if not runs else
                 "<div style='font-size:12px;line-height:1.9;"
                 "font-family:Consolas,monospace'>%s</div>"
                 % "<br>".join(e(str(x)) for x in runs[:14]))
              + "<div style='color:var(--gray-5);font-size:11px'>"
                "缠论池今日新扫 %d 票</div>" % s.get("chanlun_today", 0))

        # ---- 仪3 预警仪：bad 任务+弱案例+警报池+需注意 ----
        w = s.get("weak") or {}
        al = ["<table style='%s'><tr>" % _TB
              + "".join("<th style='%s;background:var(--gray-3)'>%s</th>"
                        % (_TD, x)
                        for x in ("预警源", "数值", "下钻"))
              + "</tr>"]
        for label, val, href, vcol in (
                ("坏任务", "%d 个" % len(bad), "/sys-health",
                 "#f85149" if bad else "#3fb950"),
                ("弱于板块·待跟进", str(w.get("pending", "?")), "/bt-weak",
                 "#d29922" if w.get("pending") else "#3fb950"),
                ("弱于板块·在案", str(w.get("open", "?")), "/bt-weak",
                 "#8b949e"),
                ("九维警报池(≥75)", str(s.get("nine_alerts", 0)), "/bt-9dim",
                 "#d29922" if s.get("nine_alerts") else "#3fb950"),
                ("盯盘需注意", str(s.get("watch_note", 0)), "/council",
                 "#d29922" if s.get("watch_note") else "#3fb950")):
            al.append("<tr><td style='%s'>%s</td>"
                      "<td style='%s;color:%s;font-weight:600'>%s</td>"
                      "<td style='%s'><a href='%s' style='color:var(--blu-500);"
                      "text-decoration:none'>去处理 →</a></td></tr>"
                      % (_TD, e(label), _TD, vcol, e(str(val)), _TD, href))
        nl = s.get("nine_list") or []
        if nl:
            al.append("<tr><td style='%s' colspan='3'>警报池 top: %s</td></tr>"
                      % (_TD, " ".join(
                          "<a href='/kg?c=%s' style='color:var(--ylw-500);"
                          "text-decoration:none'>%s(%.0f)</a>"
                          % (e(str(x.get("code") or "")),
                             e(str(x.get("name") or x.get("code"))),
                             float(x.get("score") or 0))
                          for x in nl[:8])))
        al.append("</table>")
        m3 = (_sec("③ 预警仪 · 待处理与知晓", "/sys-health",
                   "红=今日必须处理；黄=知晓")
              + "".join(al))

        # ---- 仪4 结构图台账：缠论池近 15 票（图为哪个事件而画）----
        gl = ["<table style='%s'><tr>" % _TB
              + "".join("<th style='%s;background:var(--gray-3)'>%s</th>"
                        % (_TD, x)
                        for x in ("代码", "名称", "信号", "信号日", "发现日"))
              + "</tr>"]
        for g in s.get("graphs", []):
            gl.append("<tr><td style='%s'><a href='/kg?c=%s' "
                      "style='color:var(--blu-500);text-decoration:none'>%s</a></td>"
                      "<td style='%s'>%s</td><td style='%s;color:var(--ylw-500)'>%s</td>"
                      "<td style='%s'>%s</td><td style='%s'>%s</td></tr>"
                      % (_TD, e(str(g.get("code") or "")), e(str(g.get("code") or "")),
                         _TD, e(str(g.get("name") or "")), _TD,
                         e(str(g.get("type") or "")), _TD,
                         e(str(g.get("dt") or "")), _TD,
                         e(str(g.get("detect_date") or ""))))
        gl.append("</table>")
        m4 = (_sec("④ 结构图台账 · 缠论池近 15 票", "/bt-chanlun-pool",
                   "点代码进图谱溯源")
              + "".join(gl))

        # ---- 仪5 账户态势：4 模拟盘 ----
        ac = ["<table style='%s'><tr>" % _TB
              + "".join("<th style='%s;background:var(--gray-3)'>%s</th>"
                        % (_TD, x)
                        for x in ("账户", "持仓", "现金", "净值", "较昨日"))
              + "</tr>"]
        for a in s.get("accounts", []):
            nav = a.get("nav")
            ps = a.get("ps")
            pn = a.get("pnl_pct")
            pc = ("#3fb950" if (pn or 0) >= 0 else "#f85149"
                  if isinstance(pn, (int, float)) else "#8b949e")
            ac.append("<tr><td style='%s'><a href='/bt-report' "
                      "style='color:var(--blu-500);text-decoration:none'>%s %s</a></td>"
                      "<td style='%s'>%d</td><td style='%s'>%.0f</td>"
                      "<td style='%s'>%s</td><td style='%s;color:%s'>%s</td></tr>"
                      % (_TD, e(str(a.get("icon") or "")), e(str(a.get("name") or "")),
                         _TD, int(a.get("open") or 0), _TD,
                         float(a.get("cash") or 0), _TD,
                         e(("%.0f" % nav) if isinstance(nav, (int, float)) else "—"),
                         _TD, pc,
                         e(("%+.2f%%" % ((pn or 0) * 100))
                           if isinstance(pn, (int, float))
                           else (("ps %s" % ps) if ps else "—"))))
        ac.append("</table>")
        m5 = (_sec("⑤ 账户态势 · 4 模拟盘", "/bt-report", "点账户进报告")
              + "".join(ac))

        # ---- 仪6 决策层：四会决策流时间线（点条目展开观点原文）----
        dd = bridge_decisions()
        acts = bridge_actions_index()
        _ROLE = (("bull", "多头"), ("bear", "空头"), ("aggr", "激进"),
                 ("cons", "保守"), ("tech", "技术面"), ("senti", "情绪面"))
        tl = ([("🏛 立案", "#58a6ff", p.get("ts") or "", p)
               for p in dd["props"]]
              + [("👁 盯盘", "#d29922", w.get("ts") or "", w)
                 for w in dd["watches"]]
              + [("🧾 复盘", "#bc8cff", r.get("ts") or "", r)
                 for r in dd["reviews"]])
        tl.sort(key=lambda x: x[1], reverse=True)
        items = []
        for tag, tc, ts, r in tl[:24]:
            code = str(r.get("code") or "")
            nm = str(r.get("name") or code)
            hm = str(ts)[5:16] if ts else "?"
            if tag == "🏛 立案":
                memo = r.get("memo") or {}
                vd = str(memo.get("verdict") or "?")
                vc = {"支持": "#3fb950", "反对": "#f85149"}.get(vd, "#d29922")
                head = ("<b style='color:%s'>%s</b> · 分歧 %s ｜ %s"
                        % (vc, e(vd), e(str(r.get("dissent"))),
                           e(str(memo.get("thesis") or ""))))
                vrows = []
                for rk, rcn in _ROLE:
                    v = (r.get("views") or {}).get(rk) or {}
                    st = v.get("stance") or 0
                    stc = ("#3fb950" if st > 0
                           else "#f85149" if st < 0 else "#8b949e")
                    vrows.append(
                        "<div style='margin:3px 0'><b style='color:%s'>"
                        "%s %+d</b> <span style='color:var(--gray-7)'>%s</span></div>"
                        % (stc, rcn, int(st),
                           e(str(v.get("answer") or "").strip())))
                risks = "、".join(str(x) for x in (memo.get("risks") or []))
                body = ("%s<div style='color:var(--gray-5);font-size:11px;"
                        "margin-top:5px'>风险：%s ｜ 若看错：%s ｜ 信心 %s"
                        "/100 · 主观分歧 %s · 耗时 %.0fs</div>"
                        % ("".join(vrows), e(risks),
                           e(str(memo.get("if_wrong") or "")),
                           e(str(memo.get("conviction"))),
                           e(str(memo.get("dissent_hat"))),
                           float(r.get("elapsed_s") or 0)))
            elif tag == "👁 盯盘":
                j = r.get("judge") or {}
                lv = str(j.get("level") or "?")
                vc = "#d29922" if lv == "需注意" else "#3fb950"
                head = ("<b style='color:%s'>%s · %s</b> · 浮盈 %+.1f%%"
                        % (vc, e(lv), e(str(j.get("bias") or "")),
                           float(r.get("pnl_pct") or 0)))
                evs = "".join(
                    "<div style='color:var(--gray-5);font-size:11px'>· %s %s</div>"
                    % (e(str(x.get("ts") or ""))[:10],
                       e(str(x.get("text") or "")))
                    for x in (r.get("events") or [])[:3])
                body = "%s<div style='color:var(--gray-7)'>%s</div>" % (
                    evs, e(str(j.get("view") or "")))
            else:
                br = str(r.get("bear_right")
                         or (r.get("close") or {}).get("bear_right") or "?")
                at = str(r.get("attribution")
                         or (r.get("post") or {}).get("attribution") or "?")
                head = "<b style='color:#bc8cff'>空头%s · 归因%s</b>" % (
                    e(br), e(at))
                post = (r.get("post") or {})
                cf = str(r.get("counterfactual")
                         or post.get("counterfactual") or "")
                ls = (r.get("lessons") or post.get("lessons") or [])
                body = ("<div style='color:var(--gray-7)'>%s</div>%s"
                        % (e(cf),
                           "".join("<div style='color:var(--ylw-500);"
                                   "font-size:11px;margin-top:3px'>⚑ %s</div>"
                                   % e(str(x.get("text") or x
                                           if isinstance(x, dict) else x))
                                   for x in ls[:3])))
            a0 = acts.get(r.get("ts") or "")
            abg = ("<b style='color:%s;font-size:11px'>%s</b>"
                   % ({"agree": "#3fb950", "reject": "#f85149"}.get(
                       (a0 or {}).get("action"), "#d29922"),
                      {"agree": "✅已同意", "reject": "⛔已否决"}.get(
                          (a0 or {}).get("action"), "📝有备注"))) if a0 else ""
            items.append(
                "<details style='border-left:3px solid %s;background:var(--gray-2);"
                "padding:5px 10px;margin:4px 0;border-radius:6px'>"
                "<summary style='cursor:pointer'><span style='color:var(--gray-5);"
                "font-size:11px;font-family:Consolas,monospace'>%s</span> %s "
                "<a href='/kg?c=%s' style='color:var(--blu-500);text-decoration:"
                "none'>%s %s</a>%s <span style='font-size:12px;color:var(--gray-7)'>"
                "%s</span></summary><div style='padding:6px 4px 2px;"
                "font-size:12px;line-height:1.7'>%s</div></details>"
                % (tc, hm, tag, e(code), e(code), e(nm), abg, head, body))
        m6 = (_sec("⑥ 决策层 · 四会决策流", "/council",
                   "点条目展开六视角观点原文（立案→盯盘→复盘）")
              + ("".join(items)
                 or "<div style='color:var(--gray-5);font-size:12px'>"
                   "暂无决策记录（跑批后 23:50 自动产生）</div>"))

        # ---- 仪7 影子对照：分歧度×前向收益（转正门槛 n/30）----
        def _fmt_r(v):
            return ("待开市" if not isinstance(v, (int, float))
                    else "%+.1f%%" % (v * 100))
        g = dd["gate"] or {}
        sm = dd["samples"] or []
        bd = dd["by_dissent"] or []
        gs = str(g.get("status") or "样本积累中")
        gc = {"显著·建议转正评估": "#3fb950",
              "样本积累中": "#58a6ff"}.get(gs, "#d29922")
        mx = max([b.get("n") or 0 for b in bd] + [1])
        bars = "".join(
            "<div style='display:flex;align-items:center;gap:6px;"
            "margin:2px 0'><span style='width:56px;color:var(--gray-5);"
            "font-size:11px'>%s</span><div style='height:10px;"
            "background:var(--ylw-500);border-radius:3px;width:%dpx'></div>"
            "<span style='color:var(--gray-7);font-size:11px'>%d 票</span></div>"
            % (e(str(b.get("bucket"))), 6 + int(140 * (b.get("n") or 0) / mx),
               b.get("n") or 0) for b in bd)
        srows = "".join(
            "<tr><td style='%s'><a href='/kg?c=%s' style='color:var(--blu-500);"
            "text-decoration:none'>%s</a></td><td style='%s'>%s</td>"
            "<td style='%s'>%d</td><td style='%s'>%s</td><td style='%s'>%s"
            "</td><td style='%s'>%s</td></tr>"
            % (_TD, e(str(s.get("code") or "")), e(str(s.get("code") or "")),
               _TD, e(str(s.get("name") or "")), _TD,
               int(s.get("dissent") or 0), _TD, e(str(s.get("verdict") or "")),
               _TD, e(_fmt_r(s.get("r5"))), _TD, e(_fmt_r(s.get("r20"))))
            for s in sm[:12])
        pts = [s for s in sm if isinstance(s.get("r20"), (int, float))]
        scatter = ""
        if pts:
            dots = "".join(
                "<a href='/kg?c=%s'><circle cx='%d' cy='%d' r='5' "
                "fill='#58a6ff' opacity='0.85'><title>%s 分歧%d r20 "
                "%+.1f%%</title></circle></a>"
                % (e(str(s.get("code") or "")),
                   30 + int(3.0 * (s.get("dissent") or 0)),
                   150 - int(140 * max(-0.2, min(0.4, s.get("r20") or 0)) / 0.6),
                   e(str(s.get("name") or s.get("code"))),
                   int(s.get("dissent") or 0),
                   float(s.get("r20") or 0) * 100)
                for s in pts[:40])
            scatter = ("<svg width='360' height='165' style='background:"
                       "var(--gray-2);border-radius:8px;margin:6px 0'>"
                       "<line x1='30' y1='150' x2='335' y2='150' "
                       "stroke='#30363d'/><line x1='30' y1='10' x2='30' "
                       "y2='150' stroke='#30363d'/>%s<text x='338' y='162' "
                       "fill='#8b949e' font-size='10'>分歧度</text><text "
                       "x='6' y='16' fill='#8b949e' font-size='10'>r20</text>"
                       "</svg>" % dots)
        m7 = (_sec("⑦ 影子对照 · 分歧度×前向收益", "/kg/shadow",
                   "点票进图谱；开市后前向收益自然累积")
              + "<div style='font-size:12px;margin:4px 0'><b style='color:"
                "%s'>● %s</b> <span style='color:var(--gray-5)'>前向样本 %s/%s · "
                "更新 %s</span></div>" % (gc, e(gs), e(str(g.get("n"))),
                                          e(str(g.get("need"))),
                                          e(dd["shadow_updated"] or "?"))
              + "<table style='%s'><tr>" % _TB
              + "".join("<th style='%s;background:var(--gray-3)'>%s</th>"
                        % (_TD, x)
                        for x in ("票", "名称", "分歧", "结论", "r5", "r20"))
              + "</tr>%s</table>" % (srows
                                     or "<tr><td style='%s' colspan='6'>"
                                        "暂无在册提案</td></tr>" % _TD)
              + scatter + "<div style='margin-top:6px'>%s</div>" % bars)

        # ---- 仪8 指挥台：待确认队列三动作 + 快捷指令（两步确认闸门）----
        from urllib.parse import quote as _uq
        _BTN = ("display:inline-block;padding:3px 10px;margin:2px 3px;"
                "border-radius:12px;font-size:11px;text-decoration:none;"
                "border:1px solid %s;color:%s;background:#161b22")
        pend = [p for p in dd["props"]
                if (p.get("ts") or "") not in acts][:6]
        plines = []
        for p in pend:
            memo = p.get("memo") or {}
            vd = str(memo.get("verdict") or "?")
            vc = {"支持": "#3fb950", "反对": "#f85149"}.get(vd, "#d29922")
            key = _uq(str(p.get("ts") or ""))
            plines.append(
                "<div style='padding:6px 8px;margin:4px 0;background:var(--gray-2);"
                "border-radius:8px;display:flex;justify-content:space-between;"
                "align-items:center;flex-wrap:wrap;gap:6px'>"
                "<div style='font-size:12px'><a href='/kg?c=%s' style='"
                "color:var(--blu-500);text-decoration:none'>%s %s</a> "
                "<b style='color:%s'>%s</b> · 分歧 %s · %s</div>"
                "<div><a href='/bridge/act?cmd=agree:%s' style='%s'>"
                "✅ 同意</a><a href='/bridge/act?cmd=reject:%s' style='%s'>"
                "⛔ 否决</a><a href='/bridge/act?cmd=note:%s' style='%s'>"
                "📝 备注</a></div></div>"
                % (e(str(p.get("code") or "")), e(str(p.get("code") or "")),
                   e(str(p.get("name") or "")), vc, e(vd),
                   e(str(p.get("dissent"))), e(str(p.get("d"))),
                   key, _BTN % ("#3fb950", "#3fb950"),
                   key, _BTN % ("#f85149", "#f85149"),
                   key, _BTN % ("#d29922", "#d29922")))
        quick = "".join(
            "<a href='%s' style='%s'>%s</a>"
            % ((("/bridge/act?cmd=" + _uq(c)) if c else "/api/alert/push"),
               _BTN % ("#30363d", "#58a6ff"), lb)
            for lb, c in (("🔄 同步 tdx", "sync:tdx"),
                          ("🔄 同步 tushare", "sync:tushare"),
                          ("🔄 同步 kg", "sync:kg"),
                          ("🛰 缠论扫描", "scan:chanlun"),
                          ("🛰 弱案例扫描", "scan:weak"),
                          ("🛰 九维扫描", "scan:nine"),
                          ("📣 预警推送", None)))
        m8 = (_sec("⑧ 指挥台 · 待确认与快捷指令", "/council",
                   "两步确认闸门；资金动作走 /screen 既有确认流程")
              + ("".join(plines)
                 or "<div style='color:var(--gray-5);font-size:12px'>"
                   "待确认队列空（近 20 条备忘录均已处理）</div>")
              + "<div style='margin:10px 0 4px;color:var(--gray-5);font-size:11px'>"
                "快捷指令（观察类 · 点后确认执行）</div>" + quick
              + "<div style='margin-top:8px'><a href='/screen' style='"
                "display:inline-block;padding:6px 14px;background:var(--gray-2);"
                "border:1px solid var(--gray-4);border-radius:8px;color:var(--ylw-500);"
                "text-decoration:none;font-size:12px'>💰 资金动作 → 模拟仓"
                "操作台（建仓/平仓/加减仓，二次确认在彼处——舱的闸门纪律）"
                "</a></div>")

        content = ("%s%s%s%s%s%s%s%s%s%s"
                   % (m1, m2, m3, m4, m5, m6, m7, m8,
                      "<div style='color:var(--gray-5);font-size:11px;"
                      "margin:16px 0'>K56 全量交付 · 五仪+决策层+指挥台 · "
                      "数据穿透：点仪① 🔍 就地展开表内数据 · "
                      "API: /api/bridge/summary</div>", _TP_JS))
        body = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                + ("<meta http-equiv='refresh' content='30'>" if warming else "")
                + "<title>驾驶舱 · 老田的AI助手</title></head><body "
                "style='background:var(--gray-0);margin:0;padding:12px 16px'>"
                + self._COCKPIT_CSS + self._KG_CSS
                + pill_nav("bridge")
                + "<div style='color:var(--gray-7);font-size:18px;"
                  "font-weight:700;margin:10px 0 2px'>🚀 投研驾驶舱</div>"
                  "<div style='color:var(--gray-5);font-size:12px;"
                  "margin-bottom:6px'>指挥大舱不是数据屏——数据到目的地、"
                  "预警不断提醒、红黄绿分级</div>"
                + content + "</body></html>")
        self._html(body)
