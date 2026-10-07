# -*- coding: utf-8 -*-
"""持仓盘中异动监控（纸面账本 open 持仓，计划任务每 30 分钟）

  positions.json open 持仓 → 新浪实时行情（复用 news 域接口）
  → 浮盈/浮亏触及 ±阈值（默认 3%，相对买价）→ 飞书卡片提醒
  → 同票同方向 30 分钟冷却（pos_alert_state.json，与 news 域同款机制）
  → 卡片失败自动降级纯文本（quant 域铁律：消息永不丢）
  非该市场交易时段跳过；无持仓直接退出；账本只读（盘后 update_ledger 权威）。

用法: python watch_positions.py [--dry] [--force] [--ledger <path>]
  --dry      检测但不推送、不写冷却
  --force    忽略交易时段门控（调试/链路验证）
  --ledger   指定账本路径（默认 positions.json；验证链路用临时账本）
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import runner  # noqa: E402  复用 _load_ledger/build_card/push_feishu/CFG

CST = timezone(timedelta(hours=8))
STATE_PATH = os.path.join(HERE, "pos_alert_state.json")
LOG_PATH = os.path.join(HERE, "logs", "watch.log")
NEWS_DB = os.path.join(runner.NEWS_DIR, "news.db")
NEWS_CFG = os.path.join(runner.NEWS_DIR, "news_config.json")


def log(msg):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write("[%s] %s\n" % (
            datetime.now(CST).strftime("%m-%d %H:%M:%S"), msg))


def mkt_open(mkt, now):
    """市场交易时段（北京时间粗粒度窗口，覆盖冬夏令时）。"""
    wd, t = now.weekday(), now.hour * 60 + now.minute
    if mkt == "US":     # 北京 21:30-次日 04:00（夏令）；冬令 22:30-05:00 亦在窗内
        return (wd <= 4 and t >= 21 * 60 + 20) or \
               (1 <= wd <= 5 and t <= 5 * 60 + 20)
    if mkt == "HK":
        return wd <= 4 and 9 * 60 + 15 <= t <= 16 * 60 + 15
    if mkt == "CN":
        return wd <= 4 and 9 * 60 + 15 <= t <= 15 * 60 + 15
    if mkt == "CRYPTO":
        return True
    return False


def sina_code(mkt, sym):
    """(market, db_symbol) → 新浪代码（resolve_watchlist 逆映射）。"""
    if mkt == "CN" and "." in sym:
        num, suf = sym.split(".")
        return suf.lower() + num
    if mkt == "HK":
        return "rt_hk" + sym
    if mkt == "US":
        return "gb_" + sym.lower().replace("_", ".")
    return None  # CRYPTO：新浪无实时源，跳过


def _load_news_cfg():
    try:
        with open(NEWS_CFG, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _recent_hits(hours=24):
    """近 N 小时命中新闻 [(ts, title, stocks_json)]，失败静默降级为空。"""
    import sqlite3
    cutoff = (datetime.now(CST) - timedelta(hours=hours)
              ).strftime("%Y-%m-%d %H:%M")
    try:
        db = sqlite3.connect(NEWS_DB)
        try:
            return db.execute(
                "SELECT ts, title, stocks FROM news"
                " WHERE hit=1 AND ts>=? ORDER BY ts DESC",
                (cutoff,)).fetchall()
        finally:
            db.close()
    except Exception as e:
        log("news_db FAIL %s" % str(e)[:120])
        return []


def _stock_terms(e, wcfg):
    """持仓股的新闻匹配词集合：名称/代码/数字代码 + watchlist 标准名与别名。"""
    terms = {e["name"], e["symbol"]}
    if "." in e["symbol"]:
        terms.add(e["symbol"].split(".")[0])
    code = sina_code(e["market"], e["symbol"])
    for w in wcfg.get("watchlist", []):
        if w.get("code") == code or w.get("name") == e["name"]:
            terms.add(w.get("name") or "")
            terms.update(w.get("aliases") or [])
    return {t for t in terms if t and len(t) >= 2}


def _drill_reasons(rows, terms, cap=3):
    """近 24h 新闻中该股相关条目：标题含匹配词（消歧）或 stocks 标注命中。"""
    if not rows or not terms:
        return []
    sys.path.insert(0, runner.NEWS_DIR)
    from news_collector import term_in
    out = []
    for ts, title, stocks in rows:
        tagged = False
        if stocks:
            try:
                tagged = bool(set(json.loads(stocks)) & terms)
            except Exception:
                pass
        if tagged or any(term_in(t, title) for t in terms):
            out.append((ts, title))
        if len(out) >= cap:
            break
    return out


def _alert_keywords(rows, wcfg):
    """近 24h 命中新闻标题里的关注词统计 → [(词, 次数)] 降序。"""
    cnt = {}
    for _, title, _ in rows:
        for k in wcfg.get("watch_keywords", []):
            if k and k in title:
                cnt[k] = cnt.get(k, 0) + 1
    return sorted(cnt.items(), key=lambda x: -x[1])


def _ext_quote_url(mkt, sym):
    """交易软件外链（东财个股页，手机浏览器打开可唤起 App）。"""
    if mkt == "CN" and "." in sym:
        num, suf = sym.split(".")
        return "https://quote.eastmoney.com/%s%s.html" % (suf.lower(), num)
    if mkt == "HK":
        return "https://quote.eastmoney.com/hk/%s.html" % sym
    if mkt == "US":
        return "https://quote.eastmoney.com/us/%s.html" % sym
    return None  # CRYPTO：东财无行情


def _build_alert_card(lines, n_alerts, n_open, stamp, cooldown_min, n_kw=0):
    """按 config.json pos_card 模板段构造异动卡（未配置回退内置默认）。

    pos_card: {title, subtitle, template, show_qr,
               columns: [{label, value: alerts|open|kw, color}],
               buttons: [{label, path, type}]}"""
    cc = runner.CFG.get("pos_card") or {}
    footer = ("纸面模拟，不构成投资建议 · 同票同方向 %d 分钟冷却 · %s"
              % (cooldown_min, stamp))

    vals = {"alerts": str(n_alerts), "open": str(n_open), "kw": str(n_kw)}
    cols = [(
        str(c.get("label", "?")),
        vals.get(c.get("value", ""), "?"),
        str(c.get("color", "grey-50")),
    ) for c in (cc.get("columns")
                or [{"label": "触发", "value": "alerts", "color": "orange-50"},
                    {"label": "持仓中", "value": "open", "color": "grey-50"}])]

    # K42-c 判例：卡内只放外网可达链接（url 直链，如飞书文档/东财）；
    # portal path 按钮（内网）与门户二维码手机不可达，一律不生成
    buttons = qr = None
    btns = [(str(b.get("label", "")), str(b.get("url")),
             str(b.get("type", "default")))
            for b in (cc.get("buttons") or []) if b.get("url")]
    if btns:
        buttons = btns

    return runner.build_card(
        str(cc.get("title", "🎯 持仓异动提醒")),
        "%s · %s" % (stamp, cc.get("subtitle", "量化信号实战（纸面）")),
        str(cc.get("template", "orange")),
        ["\n".join(lines)], footer=footer, columns=cols,
        buttons=buttons, qr_key=qr, use_schema2=True)


def main():
    dry = "--dry" in sys.argv
    force = "--force" in sys.argv
    ledger = LEDGER_OVERRIDE if "--ledger" in sys.argv else runner.LEDGER_JSON
    try:
        with open(ledger, encoding="utf-8") as f:
            led = json.load(f)
    except Exception:
        led = {}
    open_ = led.get("open") or []
    if not open_:
        print("WATCH skip: no open positions")
        return
    now = datetime.now(CST)
    watch = [(e, sina_code(e["market"], e["symbol"])) for e in open_]
    watch = [(e, c) for e, c in watch
             if c and (force or mkt_open(e["market"], now))]
    skipped_hrs = len(open_) - len(watch)
    if not watch:
        print("WATCH skip: %d open, none in market hours" % len(open_))
        return

    sys.path.insert(0, runner.NEWS_DIR)
    from quote_snapshot import fetch_quotes
    try:
        quotes = {q["code"]: q for q in fetch_quotes([c for _, c in watch])}
    except Exception as e:
        log("fetch FAIL %s" % str(e)[:120])
        print("WATCH FETCH_FAIL")
        return

    rep = runner.CFG.get("report", {})
    threshold = float(rep.get("pos_alert_pct", 0.03))
    cooldown_min = int(rep.get("pos_alert_cooldown_min", 30))
    state = {}
    if os.path.exists(STATE_PATH):
        try:
            state = json.load(open(STATE_PATH, encoding="utf-8"))
        except Exception:
            state = {}

    alerts = []
    for e, c in watch:
        q = quotes.get(c)
        if not q or not q.get("price"):
            continue
        pnl = (q["price"] - e["entry_px"]) / e["entry_px"]
        if abs(pnl) < threshold:
            continue
        d = "up" if pnl > 0 else "down"
        key = "%s:%s" % (e["market"], e["symbol"])
        last = (state.get(key) or {}).get(d)
        if last and not force:
            try:
                if now - datetime.strptime(
                        last, "%Y-%m-%d %H:%M:%S").replace(
                        tzinfo=CST) < timedelta(minutes=cooldown_min):
                    continue  # 冷却内同票同方向不重复
            except Exception:
                pass
        alerts.append((e, q, pnl))
        state.setdefault(key, {})[d] = now.strftime("%Y-%m-%d %H:%M:%S")

    stamp = now.strftime("%Y-%m-%d %H:%M")
    print("WATCH ok candidates=%d open=%d off_hours=%d%s" % (
        len(alerts), len(open_), skipped_hrs,
        " " + json.dumps([(e["name"], round(p * 100, 2))
                          for e, _, p in alerts], ensure_ascii=False)
        if alerts and dry else ""))
    if not alerts or dry:
        return

    wcfg = _load_news_cfg()
    news_rows = _recent_hits(24)
    kw_ranked = _alert_keywords(news_rows, wcfg)

    lines = ["**触发 %d 笔（浮盈/浮亏触及 ±%.1f%%）**" % (
        len(alerts), threshold * 100)]
    if kw_ranked:
        lines.append("24h 预警词：" + " / ".join(
            ("%s×%d" % (w, c)) if c > 1 else w
            for w, c in kw_ranked[:6]))
    for e, q, p in alerts[:8]:
        day = q.get("pct")
        day_s = ("当日 %+.2f%%" % day) if day is not None else ""
        lines.append("· [%s] %s · %s\n  买 %.4g → 现 %.4g  %s（%s）" % (
            e["market"], e["name"],
            report_strategy(e), e["entry_px"], q["price"],
            runner._c_pct(p), day_s))
        reasons = _drill_reasons(news_rows, _stock_terms(e, wcfg))
        for ts, title in reasons:
            lines.append("  📌 %s %s" % (ts[5:], title[:32]))
        if not reasons:
            lines.append("  📌 近24h无关联新闻命中")
        url = _ext_quote_url(e["market"], e["symbol"])
        if url:
            lines.append("  [📈 东财行情·可唤起App](%s)" % url)
    footer = ("纸面模拟，不构成投资建议 · 同票同方向 %d 分钟冷却 · %s"
              % (cooldown_min, stamp))
    try:  # 异动同步进详情看板（失败不影响推送）
        import doc_board
        doc_board.refresh(alerts_md=[
            "> %s 触发 %d 笔（±%.1f%%）" % (stamp, len(alerts), threshold * 100)]
            + lines)
    except Exception:
        pass
    card = _build_alert_card(lines, len(alerts), len(open_), stamp,
                             cooldown_min, len(kw_ranked))
    if runner.push_feishu(json.dumps(card, ensure_ascii=False), card=True):
        runner._atomic(STATE_PATH, state)
        log("pushed alerts=%d" % len(alerts))
        print("WATCH pushed %d" % len(alerts))
    else:  # 文本兜底
        txt = "\n".join(lines) + "\n—— " + footer
        if runner.push_feishu(txt):
            runner._atomic(STATE_PATH, state)
            print("WATCH text_fallback pushed %d" % len(alerts))
        else:
            print("WATCH PUSH_FAIL")


def report_strategy(e):
    import report_page
    return report_page.OPT_HEAD.get(e["strategy"], e["strategy"])


if "--ledger" in sys.argv:
    i = sys.argv.index("--ledger")
    try:
        LEDGER_OVERRIDE = sys.argv[i + 1]
    except IndexError:
        LEDGER_OVERRIDE = runner.LEDGER_JSON
else:
    LEDGER_OVERRIDE = None

if __name__ == "__main__":
    main()
