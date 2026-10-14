# -*- coding: utf-8 -*-
"""青萍之末 · 飞书订阅推送引擎（K245）。

设计目标（老田语音「后面可以做按键订阅，通过飞书推送到指定飞书/他人飞书」）：
    把「个人单点推送」升级为「订阅体系」——用户可自选订阅什么、推给谁。

四类订阅（kind）:
    stock_report   个股分析师报告——出报告即推（复用 K244 portal_report）
    daily_batch    每日选股批次——当日选股 + 批次回测表现
    alert_signal   预警信号触发——缠论/九维/共振等五路变化（复用 alert_watch 规则）
    quote_move     自选股异动——涨跌幅/量能超阈（复用 quote_alert 阈值）

两类触发时机（when）:
    intraday       盘中定时（30min 计划任务），只推「有变化」的
    eod            盘后汇总（15:30 计划任务），全量出一份

接收人（receivers.json）:
    名单可配（open_id / chat_id），每个订阅可指定 receiver 名单，
    为后续「他人飞书付费订阅」铺路。

状态与防轰炸:
    outbox/subscribe_state.json  记录每个订阅上次推送值与日期，
    配合 daily_cap 每日熔断（对齐 alert_watch 判例：每路每日 ≤3 推）。

用法:
    python subscribe.py --scan intraday     # 盘中扫描推变化
    python subscribe.py --scan eod          # 盘后全量汇总
    python subscribe.py --list              # 打印订阅清单（调试）
    python subscribe.py --test <sub_id>     # 试推某订阅（门户一键试推）
"""
import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
STATION = os.path.dirname(HERE)
NEWS_DIR = os.path.join(STATION, "news_collector")
PORTAL_API = "http://127.0.0.1:8091"
SUB_PATH = os.path.join(HERE, "subscriptions.json")
RECV_PATH = os.path.join(HERE, "receivers.json")
STATE_PATH = os.path.join(HERE, "outbox", "subscribe_state.json")

DAILY_CAP = 3          # 每个订阅每日最多推 3 次（盘中防轰炸，对齐 alert_watch）
SUBP_BAND_PCT = 1.5    # K250 买点触发带半宽（%）：回踩到约定位 ±1.5% 视为「到位」
ALERT_URL = PORTAL_API + "/api/alerts"


# ---------------------------------------------------------------- IO

def _read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _atomic(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def load_subs():
    """订阅清单（list）。"""
    d = _read_json(SUB_PATH, {"subscriptions": []})
    return d.get("subscriptions") or []


def load_receivers():
    """接收人名单：{name: {"type":..., "id":...}}。"""
    d = _read_json(RECV_PATH, {"receivers": []})
    out = {}
    for r in d.get("receivers") or []:
        if r.get("name"):
            out[r["name"]] = {"type": r.get("type", "open_id"),
                              "id": r.get("id")}
    return out


def _resolve_targets(sub, receivers):
    """订阅的接收人 → push_targets 结构。

    缺省（未指定 receiver）= 沿用 news_config.json 的 push_targets（个人+家人群）。
    """
    names = sub.get("receivers") or []
    if not names:
        try:
            cfg = _read_json(os.path.join(NEWS_DIR, "news_config.json"), {})
            return cfg.get("push_targets") or [
                {"name": "个人", "type": "open_id", "id": None}]
        except Exception:
            return [{"name": "个人", "type": "open_id", "id": None}]
    targets = []
    for n in names:
        r = receivers.get(n)
        if r:
            targets.append({"name": n, "type": r["type"], "id": r["id"]})
    return targets or [{"name": "个人", "type": "open_id", "id": None}]


# ---------------------------------------------------------------- 状态 / 熔断

def _load_state():
    return _read_json(STATE_PATH, {})


def _today():
    return time.strftime("%Y-%m-%d")


def _cap_ok(state, sub_id):
    """每日熔断：同订阅当日已推 >= DAILY_CAP 则跳过。"""
    st = state.get(sub_id) or {}
    if st.get("day") != _today():
        return True
    return int(st.get("count", 0)) < DAILY_CAP


def _mark_pushed(state, sub_id):
    st = state.get(sub_id) or {}
    if st.get("day") != _today():
        st = {"day": _today(), "count": 0}
    st["count"] = int(st.get("count", 0)) + 1
    st["last_ts"] = time.strftime("%Y-%m-%d %H:%M")
    state[sub_id] = st


# ---------------------------------------------------------------- 推送

def _push_card(card, targets):
    """多目标推卡片（复用 alert_push.push_card_multi）；失败全 False 则不兜底文本。"""
    try:
        sys.path.insert(0, NEWS_DIR)
        import card_build
        from send_feishu import send_content_to, send_text
    except Exception as e:
        return {"_err": "import fail %s" % str(e)[:100]}

    results = {}
    primary = targets[0]
    try:
        results[primary["name"]] = card_build.push_card(card)
    except Exception as e:
        results[primary["name"]] = False
        results["_carderr"] = str(e)[:100]
    card_json = json.dumps(card, ensure_ascii=False)
    for t in targets[1:]:
        try:
            results[t["name"]] = send_content_to(
                t["id"], t["type"], card_json, "interactive")
        except Exception as e:
            results[t["name"]] = False
    # 卡片全失败 → 纯文本兜底
    if not any(v for k, v in results.items() if not k.startswith("_")):
        try:
            txt = _strip_md_card(card)
            results["_textfb_" + primary["name"]] = send_text(txt)
            for t in targets[1:]:
                from send_feishu import send_content_to as _sc
                results["_textfb_" + t["name"]] = _sc(
                    t["id"], t["type"],
                    json.dumps({"text": txt}, ensure_ascii=False), "text")
        except Exception as e:
            results["_texterr"] = str(e)[:100]
    return results


def _strip_md_card(card):
    """卡片 → 纯文本兜底（标题 + 各 md 块去标记）。"""
    title = (card.get("header") or {}).get("title", {}).get("content", "")
    els = card.get("elements") or (card.get("body") or {}).get("elements") or []
    parts = [title]
    for e in els:
        if e.get("tag") == "markdown":
            s = e.get("content") or ""
            s = (s.replace("**", "").replace("<font color='red'>", "")
                  .replace("<font color='green'>", "")
                  .replace("<font color='grey'>", "").replace("</font>", ""))
            if s.strip():
                parts.append(s.strip())
    return "\n".join(parts)


def _build_card(title, sub, blocks, template="blue", footer=None):
    sys.path.insert(0, NEWS_DIR)
    import card_build
    return card_build.build_card(
        title, sub, template, blocks,
        footer=footer or "AI工作站 · 订阅推送 · 不构成投资建议",
        use_schema2=True)


def _cnpct(v):
    """A 股着色：涨红跌绿。"""
    return ("<font color='%s'>%+.2f%%</font>"
            % ("red" if v >= 0 else "green", v * 100 if abs(v) < 1.5 else v))


# ---------------------------------------------------------------- 订阅构造器

def _sub_stock_report(sub, mode):
    """个股分析师报告：调门户 /api/analyst-report 取报告。"""
    code = sub.get("params", {}).get("code")
    if not code:
        return None
    url = "%s/api/analyst-report?c=%s" % (PORTAL_API, code)
    try:
        with urllib.request.urlopen(url, timeout=90) as r:
            rep = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return ("⚠️ 报告订阅异常", "生成失败 %s" % str(e)[:80],
                ["该股报告暂时无法生成，请稍后重试。"], "orange")
    if not rep.get("ok"):
        return None
    name = rep.get("name") or code
    md = rep.get("md") or ""
    # 拆分 md 段（## 标题）为多块
    blocks = _split_md_blocks(md)
    title = "📄 %s 分析师报告" % name
    sub2 = time.strftime("%Y-%m-%d %H:%M") + " · 青萍之末分析师"
    return (title, sub2, blocks, "blue")


def _split_md_blocks(md):
    """按 ## 段切块，控制飞书单块长度。"""
    lines = (md or "").split("\n")
    blocks, cur = [], []
    for ln in lines:
        if ln.startswith("## ") and cur:
            blocks.append("\n".join(cur).strip())
            cur = [ln]
        else:
            cur.append(ln)
    if cur:
        blocks.append("\n".join(cur).strip())
    blocks = [b for b in blocks if b]
    # 单块超 1800 字则合并裁剪（飞书卡片单块上限约 2000）
    out = []
    for b in blocks:
        if len(b) > 1800:
            b = b[:1800] + "\n…（详见门户）"
        out.append(b)
    return out or ["（报告为空）"]


def _sub_daily_batch(sub, mode):
    """每日选股批次：读 rps_daily_batches.jsonl 最新 + rps_batch_backtest 表现。"""
    rep_dir = os.path.join(HERE, "reports")
    jl = os.path.join(rep_dir, "rps_daily_batches.jsonl")
    if not os.path.exists(jl):
        return None
    try:
        lines = [l for l in open(jl, encoding="utf-8").read().strip().split("\n") if l]
        latest = json.loads(lines[-1])
    except Exception:
        return None
    # intraday 模式：只有日期变化才推
    if mode == "intraday" and sub.get("_last_date") == latest.get("date"):
        return None
    date = latest.get("date", "")
    dstr = "%s-%s-%s" % (date[:4], date[4:6], date[6:8]) if len(date) == 8 else date
    env = latest.get("env") or {}
    picks = latest.get("picks") or []
    buy = [p for p in picks if "买入" in (p.get("action") or "")]
    obs = [p for p in picks if "观察" in (p.get("action") or "")]
    blocks = []
    blocks.append("**📅 批次日期** %s · 源 %s\n**🌡 环境** %s"
                  % (dstr, latest.get("source", "—"),
                     env.get("state", "—")))
    if buy:
        bl = ["**✅ 买入建议（%d）**" % len(buy)]
        for p in buy[:10]:
            bl.append("%s %s  score %s" % (p.get("code", ""), p.get("name", ""),
                                           p.get("score", "")))
        blocks.append("\n".join(bl))
    if obs:
        bl = ["**👀 观察（%d）**" % len(obs)]
        for p in obs[:10]:
            bl.append("%s %s  score %s" % (p.get("code", ""), p.get("name", ""),
                                           p.get("score", "")))
        blocks.append("\n".join(bl))
    if not picks:
        blocks.append("**（当日选股为空——环境闸门收紧）**")
    # 批次回测表现（最近批次）
    perf = _batch_perf_line(date)
    if perf:
        blocks.append(perf)
    title = "🎯 每日选股 %s" % dstr
    sub2 = time.strftime("%Y-%m-%d %H:%M") + " · 共 %d 只" % len(picks)
    return (title, sub2, blocks, "turquoise")


def _batch_perf_line(date):
    """最近批次回测表现（5/10/20 日超额 + K251 逐日明细）。"""
    p = os.path.join(HERE, "reports", "rps_batch_backtest.json")
    d = _read_json(p, {})
    for b in (d.get("batches") or []):
        if b.get("date") == date:
            hs = b.get("holds") or {}
            parts = []
            for h in ("5", "10", "20"):
                hd = hs.get(h)
                if hd and hd.get("excess") is not None:
                    ex = float(hd["excess"])
                    parts.append("持%s日 %s" % (
                        h, ("<font color='%s'>%+.2f%%</font>"
                            % ("red" if ex >= 0 else "green", ex * 100))))
            if not parts:
                return None
            line = "**📊 批次后验（至今/已满期）** " + " · ".join(parts)
            # K251 逐日盯市：每天的收益不打包，全展开（最多 20 日）
            dl = b.get("daily") or {}
            ks, days, cums = (dl.get("k") or [], dl.get("day") or [],
                              dl.get("cum") or [])
            if ks and days:
                seg = " ".join(
                    "D%s%s" % (k, ("<font color='%s'>%+.2f</font>"
                                   % ("red" if v >= 0 else "green",
                                      v * 100)) if v is not None else "—")
                    for k, v in list(zip(ks, days))[:20])
                last = cums[-1] if cums else None
                tail = ("" if last is None else
                        " ｜ 累计净 <font color='%s'>%+.2f%%</font>%s"
                        % ("red" if last >= 0 else "green", last * 100,
                           "（至今）" if dl.get("partial") else ""))
                line += ("\n**📅 逐日（当日收益%%，T+1 开盘进场起）**\n%s%s"
                         % (seg, tail))
            return line
    return None


def _sub_alert_signal(sub, mode):
    """预警信号触发：复用 alert_watch 的变化规则（比对门户 /api/alerts）。"""
    try:
        with urllib.request.urlopen(ALERT_URL, timeout=90) as r:
            res = json.loads(r.read().decode("utf-8"))
    except Exception:
        return None
    alerts = res.get("alerts") or []
    if not alerts:
        return None
    state = sub.get("_watch_state") or {}
    changed = []
    for a in alerts:
        nm = a.get("name", "—")
        val = a.get("value")
        prev = state.get(nm)
        if prev is None or prev != val:
            changed.append(a)
    # intraday：无变化不推；eod：推全量
    if mode == "intraday" and not changed:
        return None
    shown = changed if mode == "intraday" else alerts
    blocks = []
    for a in shown[:12]:
        ln = "**%s** %s —— %s" % (a.get("name", "—"), a.get("value", "—"),
                                  a.get("sub") or "")
        d = a.get("delta")
        if isinstance(d, int):
            ln += " <font color='%s'>（较前日 %s%d）</font>" % (
                "red" if d >= 0 else "green", "+" if d >= 0 else "", d)
        blocks.append(ln)
    if not blocks:
        return None
    title = "⚡ 预警信号" + ("变化" if mode == "intraday" else "汇总")
    sub2 = time.strftime("%Y-%m-%d %H:%M") + (
        " · %d 路变化" % len(changed) if mode == "intraday" else " · 全量")
    return (title, sub2, blocks, "orange")


def _sub_quote_move(sub, mode):
    """自选股异动：读 quote_alert 状态/快照，筛超阈标的。"""
    try:
        sys.path.insert(0, NEWS_DIR)
        qs = os.path.join(NEWS_DIR, "quote_alert_state.json")
        st = _read_json(qs, {})
    except Exception:
        st = {}
    hits = []
    # quote_alert_state 结构：{code: {last_pct, ...}} 或 {alerts:[...]}
    arr = st.get("alerts") if isinstance(st, dict) else None
    if arr:
        for a in arr:
            hits.append(a)
    elif isinstance(st, dict):
        for code, v in st.items():
            if not isinstance(v, dict):
                continue
            pct = v.get("last_pct")
            if isinstance(pct, (int, float)) and abs(float(pct)) >= 3.0:
                hits.append({"code": code, "pct": float(pct)})
    if not hits:
        return None
    # intraday 去重：按值指纹
    fp = ",".join(sorted("%s:%.1f" % (h.get("code", ""), float(h.get("pct", 0)))
                         for h in hits))
    if mode == "intraday" and sub.get("_last_fp") == fp:
        return None
    sub["_fp_new"] = fp  # K-STOCK-401：真实指纹回写（_extract_fp 的块文本与
    # 此处格式不同，原先写读不回环 → 去重永不命中，同一异动每天推满 3 次熔断）
    blocks = ["**📈 自选股异动（%d 只）**" % len(hits)]
    for h in hits[:12]:
        code = h.get("code", "")
        pct = float(h.get("pct", 0) or 0)
        blocks.append("%s %s" % (code, _cnpct(pct)))
    title = "📈 自选股异动"
    sub2 = time.strftime("%Y-%m-%d %H:%M") + " · %d 只超阈" % len(hits)
    return (title, sub2, blocks, "purple")


def _sub_buy_point(sub, mode):
    """K250 买点/卖点提醒：价格触及卡片订阅时约定的触发位即推。

    params: {code, trigger_px, trigger_kind, trigger_label}
    trigger_kind:
      pullback_hold  买点——回踩到 trigger_px 附近（±SUBP_BAND%）且未跌破 → 提醒
      break_down     卖点/放弃——跌破 trigger_px → 提醒
    intraday 去重：按「是否处于触发带」的布尔指纹（进了带推一次，
      离开带后再进才再推），避免同一位置反复轰炸。
    """
    from subscribe_action import _norm_code, stock_name, _code6
    params = sub.get("params") or {}
    code = _norm_code(params.get("code"))
    px_trig = params.get("trigger_px")
    if not code or not px_trig:
        return None
    kind = params.get("trigger_kind") or "pullback_hold"
    label = params.get("trigger_label") or ""
    px_trig = float(px_trig)
    band = SUBP_BAND_PCT / 100.0

    # 现价：日线最后一根收盘（够用；盘中异动另有 quote_move 订阅兜）
    try:
        import data_loader as DL
        sym = _code6(code) + ("." + code[:2].upper() if len(code) == 8
                              else "")
        res = DL.load_bars("CN", [sym], "2026-01-01", None)
        df = (res or {}).get(sym)
        if df is None or len(df) == 0:
            return None
        last = df.iloc[-1]
        px = float(last["close"])
        dt = str(df.index[-1])[:10]
    except Exception:
        return None
    if not px:
        return None

    name = stock_name(_code6(code)) or _code6(code)
    if kind == "pullback_hold":
        in_band = (px_trig * (1 - band)) <= px <= (px_trig * (1 + band))
    else:  # break_down
        in_band = px < px_trig
    # 去重指纹：进/出带
    fp = "%s:%s" % (kind, "1" if in_band else "0")
    if mode == "intraday" and sub.get("_last_fp") == fp:
        return None
    if not in_band:
        return None
    sub["_fp_new"] = fp  # K-STOCK-401：同 quote_move——真实指纹回写

    if kind == "pullback_hold":
        head = "📌 买点到触发位"
        tpl = "green"
        line = ("**%s（%s）** 现价 **%.2f** 已回踩至约定位 **%.2f**（%s）附近。"
                % (name, code, px, px_trig, label or "回踩不破"))
    else:
        head = "📌 触发转弱位"
        tpl = "orange"
        line = ("**%s（%s）** 现价 **%.2f** 已跌破约定位 **%.2f**（%s）。"
                % (name, code, px, px_trig, label or "跌破转弱"))
    blocks = [line,
              "数据日：%s · 触发带 ±%.1f%%" % (dt, SUBP_BAND_PCT),
              "提醒为纸面研究辅助，不构成投资建议。"]
    sub2 = time.strftime("%Y-%m-%d %H:%M") + " · 价触提醒"
    return (head, sub2, blocks, tpl)


_BUILDERS = {
    "stock_report": _sub_stock_report,
    "daily_batch": _sub_daily_batch,
    "alert_signal": _sub_alert_signal,
    "quote_move": _sub_quote_move,
    "buy_point": _sub_buy_point,
}


# ---------------------------------------------------------------- 主流程

def run_scan(mode, only_sub=None, force=False):
    """扫描全部启用的订阅，触发即推。返回 {sub_id: (ok, targets_res)}。"""
    subs = load_subs()
    receivers = load_receivers()
    state = _load_state()
    # 记录 intraday 去重所需的上下文
    for s in subs:
        sid = s.get("id")
        st = state.get(sid) or {}
        s["_last_date"] = st.get("last_date")
        s["_last_fp"] = st.get("last_fp")
        s["_watch_state"] = st.get("watch_state")

    out = {}
    for s in subs:
        if only_sub and s.get("id") != only_sub:
            continue
        # force（--test 试推）忽略 enabled/when 限制，便于门户试推未启用项
        if not force and not s.get("enabled", True):
            continue
        when = s.get("when") or ["intraday", "eod"]
        if mode not in when and not force:
            continue
        sid = s.get("id")
        if not force and not _cap_ok(state, sid):
            out[sid] = ("capped", {})
            continue
        kind = s.get("kind")
        builder = _BUILDERS.get(kind)
        if not builder:
            out[sid] = ("unknown_kind:%s" % kind, {})
            continue
        try:
            built = builder(s, mode)
        except Exception as e:
            out[sid] = ("build_err:%s" % str(e)[:120], {})
            continue
        if not built:
            out[sid] = ("no_change", {})
            continue
        title, sub2, blocks, tpl = built
        try:
            card = _build_card(title, sub2, blocks, template=tpl)
        except Exception as e:
            out[sid] = ("card_err:%s" % str(e)[:120], {})
            continue
        targets = _resolve_targets(s, receivers)
        # 配额生效（仅非 internal 套餐；internal 永不拦）
        try:
            sys.path.insert(0, STATION)  # K-STOCK-401：push_flow 在仓库根，
            # 此前缺该行 → ModuleNotFoundError 被吞 → 配额检查静默旁路
            import push_flow as _pf
            _plan, _q, _sent, _blk = _pf.quota_check()
        except Exception:
            _blk = False
        if _blk:
            out[sid] = ("quota_block", {})
            try:
                _pf.record(kind="quota_block:subscribe:%s" % kind,
                           channel="feishu", target=",".join(
                               t["name"] for t in targets),
                           count=1, ok=False)
            except Exception:
                pass
            continue
        tres = _push_card(card, targets)
        ok = any(v for k, v in tres.items() if not k.startswith("_"))
        out[sid] = ("ok" if ok else "fail", tres)
        # 统一发送流水（商业化计费依据；fail-safe，不影响主链路）
        try:
            sys.path.insert(0, STATION)
            import push_flow
            push_flow.record(kind="subscribe:%s" % kind, channel="feishu",
                             target=",".join(t["name"] for t in targets),
                             count=1, ok=ok)
        except Exception:
            pass
        # 更新状态（K-STOCK-401：仅成功才落去重指纹——此前失败也写
        # last_date/last_fp，下轮去重误判「已推过」→ 当日漏推）
        st = state.get(sid) or {}
        if ok:
            _mark_pushed(state, sid)
            st = state.get(sid)
            st["last_date"] = _extract_date(blocks)
            st["last_fp"] = s.get("_fp_new") or _extract_fp(kind, blocks)
            if kind == "alert_signal":
                _ws = _alerts_cache()
                if _ws is not None:  # 拉取失败保持旧快照，避免全量误判变化
                    st["watch_state"] = {a.get("name"): a.get("value")
                                         for a in _ws}
        state[sid] = st
    _atomic(STATE_PATH, state)
    return out


def _alerts_cache():
    try:
        with urllib.request.urlopen(ALERT_URL, timeout=30) as r:
            return (json.loads(r.read().decode("utf-8"))).get("alerts") or []
    except Exception:
        return None


def _extract_date(blocks):
    for b in blocks:
        if "批次日期" in b:
            for tok in b.replace("·", " ").split():
                if len(tok) == 10 and tok[4] == "-":
                    return tok
    return time.strftime("%Y-%m-%d")


def _extract_fp(kind, blocks):
    if kind == "quote_move":
        return "\n".join(blocks)[:300]
    return ""


def main():
    args = sys.argv[1:]
    mode = "intraday"
    if "--eod" in args or "--scan" in args and "eod" in args:
        mode = "eod"
    if "--eod" in args:
        mode = "eod"
    if "--list" in args:
        for s in load_subs():
            print("%s  kind=%s  when=%s  enabled=%s  recv=%s"
                  % (s.get("id"), s.get("kind"), s.get("when"),
                     s.get("enabled"), s.get("receivers")))
        return 0
    only = None
    force = False
    if "--test" in args:
        i = args.index("--test")
        only = args[i + 1] if i + 1 < len(args) else None
        force = True
    if "--intraday" in args:
        mode = "intraday"
    res = run_scan(mode, only_sub=only, force=force)
    ok_n = sum(1 for v in res.values() if v[0] == "ok")
    print("SUBSCRIBE_%s mode=%s subs=%d ok=%d  %s"
          % ("OK" if ok_n else "IDLE", mode, len(res), ok_n,
             json.dumps({k: v[0] for k, v in res.items()}, ensure_ascii=False)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
