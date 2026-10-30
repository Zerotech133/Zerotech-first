# -*- coding: utf-8 -*-
"""移动端页面数据接口 /api/m/*（P1，2026-10-25）。

职责：
  · GET  /api/m/health   契约/数据源健康（只读）
  · GET  /api/m/data?t=  令牌校验 → 按 scene 返回页面数据
  · POST /api/m/link     签发卡片/页面短链（需 X-Portal-Token 或本机）
  · POST /api/m/beacon   前端埋点批量上报（≤20 条/次）

设计约束：
  · 只读数据源：portal/data/todays_picks.json、portal/data/pick_details/<code>.json、
    k_stock/paper/portal_status.py、push_flow.jsonl 等；
  · 令牌走 m_tokens（HMAC-SHA256），不落日志明文；
  · 由 portal_api 的 do_GET/do_POST 早分支调用（handle_get/handle_post），
    不在 portal_api 内扩写业务逻辑。
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA_M = os.path.join(HERE, "data", "m")
PICK_DATA = os.path.join(HERE, "data")
WORK = os.path.join(HERE, "work")
PUSH_FLOW = os.path.join(WORK, "push_flow.jsonl")
EVENTS = os.path.join(WORK, "m_events.jsonl")
LOG_DIR = os.path.join(HERE, "logs")
SECRETS = os.path.join(WORK, "feishu_secrets")
MAX_BATCH = 20

if HERE not in sys.path:
    sys.path.insert(0, HERE)

import m_contract  # noqa: E402
import m_tokens  # noqa: E402

try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包导入时
    from portal.safe_urlopen import safe_urlopen



class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


# ── 契约/URL ────────────────────────────────────────────────
def _urls():
    return m_contract.load("urls")


def scene_route(scene):
    for r in _urls().get("routes") or []:
        if r.get("scene") == scene:
            return r
    raise ApiError("未知 scene：%s" % scene, 404)


def base_url():
    return (os.environ.get("M_BASE_URL") or _urls().get("default_base") or "").rstrip(
        "/"
    )


def page_url(scene, code="", id="", ttl=None, extra=None, base=None):
    """签发页面短链 → (url, payload)。base 缺省走 base_url()（env/契约）。"""
    route = scene_route(scene)
    ttl = ttl or (
        m_contract.load("auth").get("token", {}).get("ttl_sec", {}).get("page")
        or 604800
    )
    tok, payload = m_tokens.make_page_token(
        scene, id=id, code=code, ttl=ttl, extra=extra
    )
    params = ["t=" + tok]
    if code:
        params.append("code=" + str(code))
    if id and scene not in ("strategy", "feed"):
        params.append("id=" + str(id))
    if extra and extra.get("tab"):
        params.append("tab=" + str(extra["tab"]))
    prefix = (base or base_url()).rstrip("/")
    path = route["path"]
    if id and scene in ("strategy", "feed"):
        from urllib.parse import quote

        path = path + "/" + quote(str(id), safe="")
    return prefix + path + "?" + "&".join(params), payload


# ── 数据源 helper ───────────────────────────────────────────
def _read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _age_sec(path):
    try:
        return max(0, int(time.time() - os.path.getmtime(path)))
    except OSError:
        return None


def _tail_jsonl(path, limit=200):
    rows = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return []
    return rows[-limit:]


# ── scene payloads ──────────────────────────────────────────
def _pct_norm(v):
    """统一成百分数：行情源存在 0.1353（比例）与 -5.2（百分数）两种口径。"""
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return round(x * 100, 2) if abs(x) <= 1.5 else round(x, 2)


def picks_payload(limit=30):
    d = _read_json(os.path.join(PICK_DATA, "todays_picks.json"), {}) or {}
    stocks = []
    for s in d.get("stocks") or []:
        stocks.append(
            {
                "code": s.get("code"),
                "name": s.get("name"),
                "close": s.get("close"),
                "pct": _pct_norm(s.get("pct_chg")),
                "industry": s.get("industry"),
                "n_hit": s.get("n_hit"),
                "tier": s.get("tier") or "",
                "env_diff": bool(s.get("env_diff")),
                "why": s.get("env_diff_why") or "",
            }
        )
    stocks.sort(key=lambda x: (-(x.get("n_hit") or 0), -(x.get("pct") or 0)))
    total_all = len(stocks)
    t1_n = sum(1 for s in stocks if str(s.get("tier") or "").upper().startswith("T1"))
    t2_n = sum(1 for s in stocks if str(s.get("tier") or "").upper().startswith("T2"))
    env_n = sum(1 for s in stocks if s.get("env_diff"))
    run_date = str(d.get("scan_run_date") or "")
    stale = bool(
        run_date and not run_date.startswith(datetime.now().strftime("%Y-%m-%d"))
    )
    stocks = stocks[:limit]
    for s in stocks:  # 每只票一条缠论页短链（服务端签发，前端不拼地址）
        try:
            s["link"], _ = page_url("chan", code=str(s.get("code") or ""))
        except ApiError:
            s["link"] = ""
    headline = "今日候选 %s 只：T1 %s 只、T2 %s 只、环境差异 %s 只；行情基准 %s。" % (
        total_all,
        t1_n,
        t2_n,
        env_n,
        run_date or "—",
    )
    return {
        "ok": True,
        "scene": "picks",
        "generated_at": d.get("generated_at"),
        "scan_run_date": run_date,
        "stale": stale,
        "trade_basis": d.get("trade_basis"),
        "counts": d.get("counts") or {},
        "t1_n": t1_n,
        "t2_n": t2_n,
        "env_n": env_n,
        "headline": headline,
        "stocks": stocks,
        "note": "候选来自选股引擎当日扫描；点个股看缠论详情。",
        "total": total_all,
    }


def _resolve_facts(code):
    """code → (facts dict, path)；支持 605100 / 605100.SH 两种写法。"""
    cands = [code]
    if "." not in code:
        if code.startswith(("60", "68", "90")):
            cands.append(code + ".SH")
        elif code.startswith(("00", "30", "20")):
            cands.append(code + ".SZ")
        elif code.startswith(("43", "83", "87", "92")):
            cands.append(code + ".BJ")
    for c in cands:
        p = os.path.join(PICK_DATA, "pick_details", "%s.json" % c)
        d = _read_json(p)
        if isinstance(d, dict) and d:
            return d, p
    return None, None


def chan_payload(code):
    code = (code or "").strip()
    if not code:
        raise ApiError("缺少 code", 400)
    facts, path = _resolve_facts(code)
    if not facts:
        return {
            "ok": False,
            "scene": "chan",
            "code": code,
            "status": "facts_missing",
            "hint": "当日事实包未生成（可在 8091 侧触发现抓或稍后再试）",
        }
    ts = str(facts.get("ts") or "")
    today = datetime.now().strftime("%Y-%m-%d")
    ch = facts.get("chanlun") or {}
    quote = facts.get("quote") or {}
    px = ch.get("px") or quote.get("price") or facts.get("px") or 0
    pct = quote.get("pct_chg")
    if pct is None:
        pct = quote.get("pct")
    pct = _pct_norm(pct)
    notices = [n for n in (facts.get("notices") or [])][:3]
    sectors = [s for s in (facts.get("sectors") or [])][:5]
    lhb = facts.get("lhb") or []
    reasons = [r for r in (facts.get("no_buy_reasons") or [])][:3]
    steps = [
        {
            "name": "结构识别",
            "detail": "日线中枢 %s~%s（%s）"
            % (ch.get("dzd"), ch.get("dzg"), ch.get("pivot_state")),
        },
        {
            "name": "买点判定",
            "detail": (facts.get("entry_signal") or {}).get("name")
            or ch.get("basis")
            or "—",
        },
        {
            "name": "情报校验",
            "detail": ("命中 %d 条公告/板块" % len(notices))
            if notices
            else "情报未就绪",
        },
        {
            "name": "风险闸门",
            "detail": "止损 %s（%s%%）" % (ch.get("stop"), ch.get("stop_pct")),
        },
        {"name": "推送生成", "detail": "卡片/页面入口已生成"},
    ]
    # 2026-10-25：缠论页补 K 线图（日线/30F/60F；蜡烛 + 中枢 + 笔 + 买卖点）
    chart = _chan_chart(str(facts.get("code") or code))
    # 盯盘口径校正（2026-10-25 老田反馈"盯盘数据不对"）：
    # 事实包是缓存（可能滞后数日），行情价/涨跌/状态改以日线 K 线最新 bar 为准；
    # 止损/止盈等规则线仍来自事实包，另标 rule_cache_ts 让 UI 明示。
    facts_ts = ts
    quote_src = "事实包（缓存）"
    tag = (facts.get("entry_signal") or {}).get("name") or ch.get("pivot_state") or ""
    if chart:
        d1 = (chart.get("periods") or {}).get("1d") or {}
        bars1 = d1.get("bars") or []
        if bars1:
            lastb, prevb = bars1[-1], (bars1[-2] if len(bars1) > 1 else None)
            if lastb.get("c") is not None:
                px = lastb["c"]
                quote_src = "日线K线"
                if lastb.get("d"):
                    ts = str(lastb["d"])
                if prevb and prevb.get("c"):
                    try:
                        pct = round((float(px) / float(prevb["c"]) - 1) * 100, 2)
                    except (TypeError, ValueError):
                        pass
        piv = d1.get("pivots") or []
        if piv:
            try:
                lp = max(piv, key=lambda p: str(p.get("e") or ""))
            except (TypeError, ValueError):
                lp = piv[-1]
            zd, zg = lp.get("zd"), lp.get("zg")
            if zd is not None and zg is not None and px is not None:
                ch = dict(ch)
                ch["dzd"], ch["dzg"] = zd, zg
                ch["pivot_state"] = (
                    "上方离开"
                    if float(px) > float(zg)
                    else ("下方" if float(px) < float(zd) else "中枢震荡")
                )
                tag = ch["pivot_state"]
    return {
        "ok": True,
        "scene": "chan",
        "code": facts.get("code") or code,
        "name": facts.get("name") or "",
        "ts": ts,
        "stale": bool(ts and not ts.startswith(today)),
        "quote_src": quote_src,
        "facts_ts": facts_ts,
        "rule_cache_ts": facts_ts,
        "price": px,
        "pct": pct,
        "conclusion": (ch.get("basis") or "").strip(),
        "tag": tag,
        "chanlun": {
            "dzg": ch.get("dzg"),
            "dzd": ch.get("dzd"),
            "mzg": ch.get("mzg"),
            "mzd": ch.get("mzd"),
            "pivot_state": ch.get("pivot_state"),
            "stop": ch.get("stop"),
            "stop_pct": ch.get("stop_pct"),
            "tp1": ch.get("tp1"),
            "tp2": ch.get("tp2"),
            "basis": ch.get("basis"),
            "plan": ch.get("plan"),
        },
        "entry_signal": facts.get("entry_signal"),
        "notices": notices,
        "sectors": sectors,
        "lhb": lhb[:3],
        "no_buy_reasons": reasons,
        "decision": steps,
        "chart": chart,
        "tier": facts.get("tier"),
        "n_hit": facts.get("n_hit"),
        "sources": (facts.get("sources") or [])[:5],
    }


def _chan_chart_period(full_code, period, limit=200):
    """单周期图数据：K线(含量)+中枢+笔+买卖点(含背驰/共振/说明字段)。"""
    import urllib.parse as _up

    st, sd = _local_get(
        "/api/chanlun/structure?code=%s&period=%s&limit=%d"
        % (_up.quote(full_code), period, limit)
    )
    if st != 200 or not isinstance(sd, dict) or not sd.get("ok"):
        return None
    bars = [
        {
            "d": b.get("dt"),
            "o": b.get("o"),
            "h": b.get("h"),
            "l": b.get("l"),
            "c": b.get("c"),
            "v": b.get("v"),
        }
        for b in (sd.get("bars") or [])[-120:]
        if isinstance(b, dict)
    ]
    if not bars:
        return None
    sig_fields = (
        "type",
        "dt",
        "price",
        "note",
        "bei",
        "fv",
        "bei2",
        "fv2",
        "vv",
        "vv_ev",
        "pt",
        "pt_name",
        "mt",
        "mtv",
        "reg",
        "reg_ev",
    )
    signals = []
    for s in (sd.get("signals") or [])[-10:]:
        if isinstance(s, dict):
            sig = {k: s.get(k) for k in sig_fields if k in s}
            sig["note"] = (sig.get("note") or "")[:80]
            signals.append(sig)
    return {
        "bars": bars,
        "pivots": [
            {
                "s": p.get("s_dt"),
                "e": p.get("e_dt"),
                "zg": p.get("zg"),
                "zd": p.get("zd"),
            }
            for p in (sd.get("pivots") or [])
            if isinstance(p, dict)
        ][-6:],
        "bis": [
            {
                "s": b.get("s_dt"),
                "e": b.get("e_dt"),
                "sp": b.get("s_price"),
                "ep": b.get("e_price"),
                "dir": b.get("dir"),
            }
            for b in (sd.get("bis") or [])
            if isinstance(b, dict)
        ],
        "signals": signals,
    }


def _chan_chart(full_code):
    """日线/30F/60F 三周期图数据（任一可取即返回；失败返回 None）。"""
    periods = {}
    for p in ("1d", "30m", "60m"):
        try:
            one = _chan_chart_period(full_code, p)
        except Exception:  # noqa: BLE001
            one = None
        if one:
            periods[p] = one
    if not periods:
        return None
    default = "1d" if "1d" in periods else sorted(periods)[0]
    return {"default": default, "periods": periods}


def position_payload():
    try:
        kdir = os.path.join(ROOT, "k_stock")
        if kdir not in sys.path:
            sys.path.insert(0, kdir)
        from paper.portal_status import status as paper_status

        d = paper_status() or {}
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "scene": "position",
            "error": "模拟仓数据不可用：%s" % str(e)[:160],
        }
    latest = d.get("latest") or {}
    stops = {}
    for s in d.get("stops") or []:
        if isinstance(s, dict) and s.get("stock"):
            stops[str(s["stock"])] = s
    names = {}
    for p in d.get("positions") or []:
        code = str((p or {}).get("stock") or "")
        if not code:
            continue
        f, _ = _resolve_facts(code)
        if f and f.get("name"):
            names[code] = f["name"]
    positions = []
    for p in d.get("positions") or []:
        if not isinstance(p, dict):
            continue
        code = str(p.get("stock") or "")
        qty = p.get("qty") or 0
        cost = p.get("avg_cost") or 0
        last = p.get("last") or 0
        pnl = p.get("pnl")
        cost_amt = (cost or 0) * (qty or 0)
        stop = stops.get(code) or {}
        positions.append(
            {
                "code": code,
                "name": names.get(code) or code,
                "qty": qty,
                "cost": round(float(cost), 3),
                "price": last,
                "pnl": pnl,
                "pnl_pct": round((pnl / cost_amt * 100), 2) if cost_amt else None,
                "market_value": p.get("market_value"),
                "strategy": STRATEGY_LABELS.get(
                    str(p.get("strategy") or ""), p.get("strategy")
                ),
                "sl": stop.get("stop_price"),
                "sl_dist_pct": stop.get("distance_pct"),
                "tp": None,
            }
        )
    positions = positions[:20]
    _asof = d.get("as_of") or latest.get("date")
    _total = latest.get("total")
    _pos = latest.get("pos_pct")
    _cum = latest.get("cum_pct")
    headline = (
        "模拟仓总资产 %s 元，仓位 %s%%，累计收益 %s%%；"
        "持仓 %s 只（截至 %s）。"
        % (
            "{:,.0f}".format(_total) if isinstance(_total, (int, float)) else "—",
            "—" if _pos is None else round(float(_pos) * 100, 1),
            "—" if _cum is None else _cum,
            len(positions),
            _asof or "—",
        )
    )
    return {
        "ok": True,
        "scene": "position",
        "headline": headline,
        "as_of": _asof,
        "account_id": d.get("account_id"),
        "total": latest.get("total"),
        "cash": latest.get("cash"),
        "market_value": latest.get("market_value"),
        "pos_pct": latest.get("pos_pct"),
        "cum_pct": latest.get("cum_pct"),
        "verify_ok": latest.get("verify_ok"),
        "positions": positions,
        "note": "数据来自模拟仓快照（非实时成交）；止损线为系统挂单参考价。",
        "curve": (d.get("curve") or [])[-10:],
    }


def notify_payload(limit=15):
    rows = _tail_jsonl(PUSH_FLOW, 500)
    cutoff = time.time() - 24 * 3600

    def _ts(s):
        try:
            return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
        except ValueError:
            return 0.0

    r24 = [r for r in rows if _ts(r.get("ts", "")) >= cutoff]
    ok = sum(1 for r in r24 if r.get("ok"))
    denied = sum(1 for r in r24 if "deny:silent window" in (r.get("kind") or ""))
    fails = [
        r
        for r in r24
        if not r.get("ok") and "deny:silent window" not in (r.get("kind") or "")
    ]
    recent = []
    for r in rows[-limit:][::-1]:
        recent.append(
            {
                "ts": r.get("ts"),
                "kind": r.get("kind"),
                "kind_label": _kind_label(r.get("kind")),
                "channel": r.get("channel"),
                "channel_label": CHANNEL_LABELS.get(
                    str(r.get("channel") or ""), r.get("channel") or "—"
                ),
                "target": (r.get("target") or "")[:12] + "…",
                "ok": bool(r.get("ok")),
                "result": "成功" if r.get("ok") else "失败",
            }
        )
    headline = "最近 24 小时共 %s 次发送：成功 %s、失败 %s、被静默窗口拦截 %s。" % (
        len(r24),
        ok,
        len(fails),
        denied,
    )
    return {
        "ok": True,
        "scene": "notify",
        "window_hours": 24,
        "total": len(r24),
        "ok_count": ok,
        "denied_silent": denied,
        "fail": len(fails),
        "headline": headline,
        "recent": recent,
        "note": "渠道：OpenAPI=直发、CLI兜底=接口失败后的备用通道；"
        "静默窗口按策略拦截不算故障。",
    }


# ── 统一 H5：日报 / 账本 / 血缘 / 工单（2026-10-25 全量对接）──
def _local_get(path, timeout=15):
    """回环调用本机 8091 既有只读聚合端点（ThreadingHTTPServer，安全）。"""
    import urllib.error
    import urllib.request

    url = "http://127.0.0.1:8091" + path
    try:
        with safe_urlopen(url, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            return e.code, {"ok": False, "status": e.code}
    except Exception as e:  # noqa: BLE001
        return 0, {"ok": False, "error": str(e)[:140]}


# JEV 事件类型中文名（数据日报讲人话）
EVENT_LABELS = {
    "other": "其他",
    "earnings": "财报",
    "equity": "股权",
    "policy": "政策",
    "supply": "供给",
    "macro": "宏观",
    "price": "价格",
    "risk": "风险",
    "tech": "技术",
    "trade": "贸易",
    "demand": "需求",
    "credit": "信用",
    "debt": "债务",
    "rate": "利率",
    "geo": "地缘",
    "m&a": "并购",
    "mna": "并购",
}


def _fmt_time(s):
    """'2026-10-25T12:53:34+08:00' → '2026-10-25 12:53'。"""
    t = str(s or "")
    if "T" in t:
        t = t.replace("T", " ")[:16]
    return t


def daily_payload():
    st, d = _local_get("/api/daily/data")
    if st != 200 or not isinstance(d, dict):
        return {"ok": False, "scene": "daily", "error": "数据日报接口不可用"}
    dist = d.get("dist") or {}
    drift = d.get("drift") or {}
    health = d.get("health") or {}
    bt = d.get("bt") or {}
    items = [i for i in (health.get("items") or []) if isinstance(i, dict)]
    bad = [i for i in items if not i.get("exists")]
    total = dist.get("total") or 0
    bias = dist.get("bias") or {}
    conf = dist.get("conf_buckets") or {}

    def pc(n):
        try:
            return round(float(n or 0) / total * 100, 1) if total else 0.0
        except (TypeError, ValueError):
            return 0.0

    events = []
    ev_raw = dist.get("event_type") or {}
    if isinstance(ev_raw, dict):
        for k, v in sorted(ev_raw.items(), key=lambda kv: -(kv[1] or 0))[:6]:
            events.append(
                {"key": k, "label": EVENT_LABELS.get(k, k), "count": v, "pct": pc(v)}
            )
    alerts = []
    for a in [x for x in (drift.get("alerts") or []) if isinstance(x, dict)][:3]:
        msg = str(a.get("message") or "")
        short = msg.split("\n")[0][:120]
        alerts.append(
            {
                "title": a.get("title"),
                "short": short,
                "msg": msg[:900],
                "status": a.get("status"),
            }
        )
    g = bt.get("groups") or {}
    bull, bear = g.get("bull") or {}, g.get("bear") or {}
    bt_summary = (
        {
            "n": bt.get("n"),
            "range": bt.get("news_day_range"),
            "generated_at": _fmt_time(bt.get("generated_at")),
            "bull_avg1": bull.get("pct1_avg"),
            "bull_vs": bull.get("vs_mkt1"),
            "bear_avg1": bear.get("pct1_avg"),
            "bear_vs": bear.get("vs_mkt1"),
            "note": "样本内回看（非预测）；缺失价格样本已剔除",
        }
        if bt.get("exists")
        else None
    )
    drift_open = drift.get("open_count") or 0
    level = "warn" if (drift_open or bad) else "ok"
    event_line = (
        "、".join("%s %s%%" % (e["label"], e["pct"]) for e in events[:3]) or "—"
    )
    headline = (
        "今日情报 %s 条：偏多 %s%%、偏空 %s%%、中性 %s%%；"
        "高置信 %s%%。主要事件：%s。"
        % (
            total,
            pc(bias.get("bull")),
            pc(bias.get("bear")),
            pc(bias.get("neutral")),
            pc(conf.get("high")),
            event_line,
        )
    )
    status_line = "今日情报 %s 条 · 高置信 %s%% · 漂移提醒 %s 项" % (
        total,
        pc(conf.get("high")),
        drift_open,
    )
    return {
        "ok": True,
        "scene": "daily",
        "generated_at": _fmt_time(d.get("generated_at")),
        "level": level,
        "status_line": status_line,
        "headline": headline,
        "total": total,
        "bias": bias,
        "conf": conf,
        "bias_pct": {
            "bull": pc(bias.get("bull")),
            "bear": pc(bias.get("bear")),
            "neutral": pc(bias.get("neutral")),
        },
        "conf_pct": {
            "high": pc(conf.get("high")),
            "mid": pc(conf.get("mid")),
            "low": pc(conf.get("low")),
        },
        "events": events,
        "drift_open": drift_open,
        "alerts": alerts,
        "bt": bt_summary,
        "health_total": len(items),
        "health_bad": [{"name": i.get("name"), "path": i.get("path")} for i in bad][:5],
        "note": "偏多/偏空/中性=JEV 判定方向；置信=判定置信度；"
        "事件类型=JEV 归类；漂移=近 7 天分布变化超阈值（JS>0.05）。",
    }


# 账本/推送页中文标签（页面讲人话）
STRATEGY_LABELS = {
    "ma_cross": "均线交叉",
    "trend_follow": "趋势跟踪",
    "breakout": "突破",
    "mean_revert": "均值回归",
    "chan": "缠论",
    "chanlun": "缠论",
    "manual": "手工",
    "ai_team": "AI团队",
    "dual_knife": "双刀",
    "pick": "选票",
    "boll_breakout": "布林突破",
    "rsi_reversal": "RSI反转",
    "macd": "MACD",
    "grid": "网格",
    "momentum": "动量",
}
MARKET_LABELS = {
    "US": "美股",
    "HK": "港股",
    "CN": "A股",
    "SH": "A股",
    "SZ": "A股",
    "BJ": "A股",
    "CRYPTO": "加密",
}
CHANNEL_LABELS = {
    "openapi": "OpenAPI 直发",
    "lark-cli": "CLI 兜底",
    "hub-gate": "推送闸门",
    "hub": "中台",
}
KIND_LABELS = {
    "auto:send_feishu": "自动推送",
    "auto:news_collector": "新闻推送",
    "auto:morning_report": "晨报",
    "auto:report": "日报",
    "comm_patrol": "巡视提示",
    "hub": "中台发送",
    "w3:probe": "联调探针",
    "w3:selftest": "联调自测",
}


def _kind_label(kind):
    k = str(kind or "")
    if k.startswith("hub|deny:"):
        why = k.split("deny:", 1)[1]
        if "silent window" in why:
            return "被静默窗口拦截"
        return "被闸门拦截（%s）" % why[:16]
    if k in KIND_LABELS:
        return KIND_LABELS[k]
    for p in ("auto:", "w3:", "hub"):
        if k.startswith(p) and p.endswith(":") and len(k) > len(p):
            return KIND_LABELS.get(k, k.split(":", 1)[1][:16])
    return k[:18]


def ledger_payload(limit=20):
    d = _read_json(os.path.join(PICK_DATA, "ledger.json"), {}) or {}
    summary = d.get("summary") or {}
    ledger_ts = str(d.get("ledger_ts") or d.get("ts") or "")
    stale = bool(
        ledger_ts and not ledger_ts.startswith(datetime.now().strftime("%Y-%m-%d"))
    )
    open_rows = []
    for p in (d.get("open") or [])[:limit]:
        if not isinstance(p, dict):
            continue
        pnl = p.get("pnl")
        open_rows.append(
            {
                "market": MARKET_LABELS.get(
                    str(p.get("market") or ""), p.get("market")
                ),
                "symbol": p.get("symbol"),
                "name": p.get("name"),
                "strategy": STRATEGY_LABELS.get(
                    str(p.get("strategy") or ""), p.get("strategy")
                ),
                "entry_px": p.get("entry_px"),
                "last_px": p.get("last_px"),
                "days": p.get("days"),
                "pnl_pct": round(float(pnl) * 100, 2) if pnl is not None else None,
            }
        )
    win = summary.get("win_rate")
    avg = summary.get("avg_pnl")
    total = summary.get("closed_total")
    headline = (
        "开仓 %s 只 · 待定 %s 只 · 已平 %s 只；胜率 %s%%，"
        "已平仓平均收益 %s%%，累计 %s%%。"
        % (
            summary.get("open_n", 0),
            summary.get("pending_n", 0),
            summary.get("closed_n", 0),
            "—" if win is None else win,
            "—" if avg is None else avg,
            "—" if total is None else total,
        )
    )
    return {
        "ok": True,
        "scene": "ledger",
        "ts": ledger_ts,
        "ledger_ts": ledger_ts,
        "stale": stale,
        "headline": headline,
        "summary": summary,
        "open": open_rows,
        "pending_n": len(d.get("pending") or []),
        "closed_n": len(d.get("closed") or []),
        "note": "数据来自模拟/研究账本快照，非实时行情；收益率为相对开仓价的浮动。",
    }


# 血缘表中文名（页面讲人话；未知表回退英文原名）
TABLE_LABELS = {
    "HAS_CANDLE": "K线关系",
    "HAS_LIMIT": "涨跌停关系",
    "HAS_STRUCTURE": "缠论结构",
    "HAS_TOPGAP": "跳空关系",
    "HAS_VOL_EVENT": "量能事件",
    "HOLDS": "持仓关系",
    "HotMoney": "游资",
    "MarketEvent": "市场事件",
    "NEXT_TRADE_DAY": "下一交易日",
    "OCCUPIES": "席位占用",
    "ON_DATE": "交易日关联",
    "Organization": "机构",
    "Seat": "席位",
    "SIMILAR_K": "相似K线",
    "Technique": "技术形态",
    "TradeDate": "交易日",
    "TRADED_ON": "交易关系",
    "TRADED_ON.win_rate": "胜率",
    "cyq_feat": "筹码特征",
    "holder_feat": "股东特征",
}


def _table_label(table):
    t = str(table or "")
    name = t.split(":", 1)[1] if ":" in t else t
    return TABLE_LABELS.get(t) or TABLE_LABELS.get(name) or name


def _age_text(age):
    if age is None:
        return "更新未知"
    if age <= 0:
        return "今日已更新"
    if age == 1:
        return "1 个交易日前更新"
    return "%d 个交易日前更新" % age


def lineage_payload():
    """数据血缘「人话版」：回答"数据断没断、哪些表偏旧、最近更新"。"""
    st, d = _local_get("/api/kg/lineage")
    if st != 200 or not isinstance(d, dict):
        return {"ok": False, "scene": "lineage", "error": "血缘接口不可用"}
    data = d.get("data") or {}
    latest = [r for r in (data.get("all_latest") or []) if isinstance(r, dict)]
    fresh = data.get("freshness") or {}
    broken = [b for b in (data.get("broken") or []) if isinstance(b, dict)]
    broken_tables = {str(b.get("output_table") or "") for b in broken}
    watch = [w for w in (fresh.get("watch") or []) if isinstance(w, dict)]
    watch_tables = {str(w.get("table") or "") for w in watch}
    rows, attention = [], []
    for r in latest:
        table = str(r.get("output_table") or "")
        status = str(r.get("status") or "")
        fr = r.get("freshness")
        age = r.get("age_trade_days")
        row = {
            "table": table,
            "label": _table_label(table),
            "domain": table.split(":", 1)[0] if ":" in table else "",
            "job": r.get("job"),
            "status": status,
            "freshness": fr,
            "age_trade_days": age,
            "age_text": _age_text(age),
            "ended_at": str(r.get("ended_at") or "")[:16].replace("T", " "),
            "rows": r.get("rows_written"),
        }
        rows.append(row)
        if status != "ok" or table in broken_tables:
            attention.append({**row, "level": "bad", "why": "最近一次运行未成功"})
        elif fr == "watch" or table in watch_tables:
            attention.append(
                {**row, "level": "warn", "why": "数据偏旧（%s）" % _age_text(age)}
            )
    rows.sort(
        key=lambda x: (
            0 if x["status"] != "ok" else (1 if x["freshness"] == "watch" else 2),
            -(x.get("age_trade_days") or 0),
        )
    )
    fresh_count = fresh.get("fresh_count")
    if fresh_count is None:
        fresh_count = sum(1 for r in rows if r.get("freshness") == "fresh")
    if broken:
        level, line = "bad", "发现 %d 张表断链，需要处理" % len(broken)
    elif attention:
        level, line = "warn", "数据链路正常，但有 %d 张表偏旧" % len(attention)
    else:
        level, line = "ok", "数据链路正常，全部表均新鲜"
    return {
        "ok": True,
        "scene": "lineage",
        "view": d.get("view"),
        "level": level,
        "status_line": line,
        "tracked": data.get("tracked_tables") or len(rows),
        "healthy": data.get("healthy_count"),
        "broken_n": len(broken),
        "fresh_n": fresh_count,
        "watch_n": len(watch),
        "stale_n": len(fresh.get("stale") or []),
        # 历史累计失败运行数（不是当前断链表数！原页面误标成"异常"）
        "history_fail_runs": data.get("runs_not_ok"),
        "attention": attention[:8],
        "rows": rows[:20],
        "note": "数据血缘=每张数据表最近一次产出状态；fresh=新鲜、"
        "watch=偏旧、断链=最近运行失败。历史失败运行数仅作累计参考。",
    }


def ticket_payload(tid, open_id=""):
    import re as _re
    import urllib.parse as _up

    tid = str(tid or "").strip()
    if not _re.match(r"^lg-[a-z0-9]{6,24}$", tid):
        return {
            "ok": False,
            "scene": "ticket",
            "state": "bad_id",
            "error": "缺少或非法工单号（lg-xxxx）",
        }
    st, d = _local_get("/api/comm/ticket?id=" + _up.quote(tid))
    if st != 200 or not isinstance(d, dict):
        return {
            "ok": False,
            "scene": "ticket",
            "state": "not_found",
            "id": tid,
            "error": "工单不存在或已归档",
        }
    ticket = d.get("ticket") or {}
    status_map = {
        "inbox": "待处理",
        "queued": "排队中",
        "processing": "处理中",
        "done": "已完成",
        "closed": "已关闭",
        "cancelled": "已取消",
    }
    st_raw = str(ticket.get("state") or "")
    status_label = status_map.get(st_raw, st_raw or "—")
    headline = "工单 %s：%s · %s（%s），路由 %s。" % (
        tid,
        status_label,
        ticket.get("kind_label") or ticket.get("kind") or "工单",
        ticket.get("intent_label") or ticket.get("intent") or "—",
        ticket.get("route_desc") or "—",
    )
    _loop = {}
    try:
        import ticket_loop

        _loop = ticket_loop.summary(tid)
    except Exception:  # noqa: BLE001 —— 闭环不可用不反噬详情页
        _loop = {}
    try:
        can_reply = bool(open_id) and open_id in _handler_open_ids()
    except Exception:  # noqa: BLE001 —— 处理人名单不可判定时失败关闭
        can_reply = False
    return {
        "ok": True,
        "scene": "ticket",
        "id": tid,
        "status_label": status_label,
        "headline": headline,
        "ticket": ticket,
        "timeline": d.get("timeline") or [],
        "next_hint": d.get("next_hint"),
        "subscription": d.get("subscription"),
        "waited_min": _loop.get("waited_min"),
        "nudge_n": _loop.get("nudge_n") or 0,
        "last_nudge": _loop.get("last_nudge") or "",
        "escalate": bool(_loop.get("escalate")),
        "replies": _loop.get("replies") or [],
        "can_nudge": bool(_loop.get("can_nudge")),
        "authed": bool(open_id),
        "can_reply": can_reply,
        "reply_hint": (
            "" if can_reply else "处理人回复需先用飞书身份登录；仅处理人账号可答复"
        ),
    }


def entry_payload():
    """统一入口：所有场景 + 服务端签发链接（token 内嵌，前端零拼接）。"""
    entries = [
        ("analysis", "分析总览", "盘前盘后 / 板块 / 九维 / 卖点 / 共振", "行情"),
        ("picks", "今日选股", "候选 / 环境差异 / 缠论信号", "行情"),
        ("chan", "缠论详情", "以 605100 为例：中枢 / 买点 / 决策链", "行情"),
        ("position", "持仓止盈止损", "模拟仓线位 / 盈亏", "行情"),
        ("brief", "今日简报", "市场速览 / 命中 / 板块 / AI 晨报复盘", "情报"),
        ("feed", "投喂面", "投喂工单 / 快照归档 / 去重 / 导出", "情报"),
        ("strategy", "策略工单", "提交选股方法 / 回测 / 观察名单", "情报"),
        ("daily", "数据日报", "情绪 × 次日实证 / 漂移 / 管道体检", "情报"),
        ("ledger", "账本", "开仓 / 待定 / 已平 / 胜率", "情报"),
        ("lineage", "数据血缘", "追踪表 / 健康 / 断链", "情报"),
        ("ticket", "工单详情", "派单受理进度（示例工单）", "客户"),
        ("subscription", "我的订阅", "档位 / 到期", "客户"),
        ("push", "推送中台", "总览 / 渠道 / 死信 / 通知决策 / 群", "系统"),
        ("notify", "通知流水", "24h 发送 / 失败", "系统"),
        ("ops", "管理台", "端口 / 心跳（仅内网，公网不可达）", "系统"),
    ]
    out = []
    for scene, title, desc, section in entries:
        kw = {}
        if scene == "chan":
            kw["code"] = "605100"
        if scene == "ticket":
            kw["id"] = "lg-5917d4c4357b"
        try:
            url, _p = page_url(scene, **kw)
        except ApiError:
            url = ""
        out.append(
            {
                "scene": scene,
                "title": title,
                "desc": desc,
                "section": section,
                "url": url,
            }
        )
    return {"ok": True, "scene": "entry", "links": out}


def subscription_payload(open_id=""):
    """订阅客户面：有 open_id 时走订阅账本只读摘要（最小化+掩码）；否则空态。"""
    if not open_id:
        return {
            "ok": True,
            "scene": "subscription",
            "state": "unbound",
            "plan": None,
            "items": [],
            "note": "从飞书卡片进入会携带身份；外部分享只展示脱敏信息",
        }
    try:
        if HERE not in sys.path:
            sys.path.insert(0, HERE)
        import subscription_store as ss

        raw = ss.get_subscriber_summary(open_id) or {}
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "scene": "subscription",
            "error": "订阅账本不可用：%s" % str(e)[:120],
        }
    if raw.get("error"):
        return {
            "ok": True,
            "scene": "subscription",
            "state": "empty",
            "plan": None,
            "items": [],
            "note": str(raw["error"])[:120],
        }
    sub = raw.get("subscriber") or {}
    ent = raw.get("entitlements") or raw.get("items") or []
    items = []
    for it in ent if isinstance(ent, list) else []:
        if not isinstance(it, dict):
            continue
        items.append(
            {
                "kind": it.get("kind") or it.get("plan_id"),
                "plan": it.get("plan_name") or it.get("plan_id"),
                "start": it.get("start") or it.get("starts_at"),
                "end": it.get("end") or it.get("expires_at"),
                "status": it.get("status"),
            }
        )
    plan = None
    if items:
        plan = {
            "name": items[0].get("plan"),
            "end": items[0].get("end"),
            "status": items[0].get("status"),
        }
    return {
        "ok": True,
        "scene": "subscription",
        "state": "ok" if items else "empty",
        "plan": plan,
        "items": items,
        "display_name": sub.get("display_name"),
        "open_id_masked": (open_id[:6] + "**") if open_id else "",
        "as_of": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


def ops_payload():
    bots = []
    for rid in ("zb.quant.ticker", "zb.owner.exec"):
        p = os.path.join(LOG_DIR, "feishu_heartbeat_%s.txt" % rid)
        if rid == "zb.quant.ticker":
            p = os.path.join(LOG_DIR, "feishu_bot_heartbeat.txt")
        bots.append({"role": rid, "heartbeat_age_sec": _age_sec(p)})
    ports = {}
    for name, port in (("8097", 8097), ("8091", 8091), ("9104", 9104)):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                ports[name] = True
        except OSError:
            ports[name] = False
    return {
        "ok": True,
        "scene": "ops",
        "bots": bots,
        "ports": ports,
        "events_age_sec": _age_sec(EVENTS),
    }


def build(scene, code="", id="", open_id=""):
    if scene == "picks":
        return picks_payload()
    if scene == "chan":
        return chan_payload(code or id)
    if scene == "position":
        return position_payload()
    if scene == "notify":
        return notify_payload()
    if scene == "subscription":
        return subscription_payload(open_id)
    if scene == "ops":
        return ops_payload()
    if scene == "daily":
        return daily_payload()
    if scene == "ledger":
        return ledger_payload()
    if scene == "lineage":
        return lineage_payload()
    if scene == "ticket":
        return ticket_payload(id or code, open_id=open_id)
    if scene == "entry":
        return entry_payload()
    if scene == "analysis":
        import m_analysis

        return m_analysis.analysis_payload()
    if scene == "brief":
        import m_brief

        return m_brief.brief_payload()
    if scene == "push":
        import m_push

        return m_push.push_payload()
    if scene == "feed":
        import m_feed

        return m_feed.feed_payload(ticket=id)
    if scene == "strategy":
        import m_strategy

        return m_strategy.strategy_payload(order_id=id)
    raise ApiError("未知 scene：%s" % scene, 404)


# ── 埋点 ────────────────────────────────────────────────────
def _beacon_spec():
    return m_contract.load("beacon").get("events") or {}


def validate_events(events):
    spec = _beacon_spec()
    if not isinstance(events, list) or not events:
        raise ApiError("events 必须为非空数组")
    if len(events) > MAX_BATCH:
        raise ApiError("单批 events 不得超过 %d 条" % MAX_BATCH)
    clean = []
    for ev in events:
        if not isinstance(ev, dict):
            raise ApiError("event 必须为对象")
        name = str(ev.get("event") or "")
        if name not in spec:
            raise ApiError("未登记的事件：%s" % name)
        for f in spec[name].get("required") or []:
            if not str(ev.get(f) or "").strip():
                raise ApiError("事件 %s 缺少必填字段 %s" % (name, f))
        rec = {"event": name, "ts": ev.get("ts") or int(time.time())}
        for f in spec[name].get("fields") or []:
            if f in ev and f != "event":
                # st 令牌长度 ~300+，截断会破坏验签 → 单独放宽到 512
                limit = 512 if f == "st" else 120
                rec[f] = str(ev[f])[:limit]
        clean.append(rec)
    return clean


def _st_visit(st):
    """分享归因（K-PUSH-002 A2）：本地校验 st 并落 visit 账。

    不依赖 import share_tokens（8091 常驻进程里模块解析环境复杂），
    直接按 share_tokens 的令牌格式验签+落同构账行；失败返回 False。
    """
    try:
        import base64
        import hashlib
        import hmac

        parts = str(st or "").split(".")
        if len(parts) != 3 or parts[0] != "st" or len(parts[2]) != 16:
            return False
        sec = _read_json(os.path.join(WORK, "share_secret.json"), {}) or {}
        secret = str(sec.get("secret") or "").strip()
        if not secret:
            return False
        want = hmac.new(
            secret.encode("utf-8"), parts[1].encode("ascii"), hashlib.sha256
        ).hexdigest()[:16]
        if not hmac.compare_digest(want, parts[2]):
            return False
        pad = "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + pad).decode("utf-8"))
        if int(payload.get("exp", 0) or 0) < int(time.time()):
            return False
        row = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "date": datetime.now().strftime("%Y-%m-%d"),
            "evt": "visit",
            "jti": payload.get("jti", ""),
            "code": payload.get("code", ""),
            "kind": payload.get("kind", ""),
            "tenant": payload.get("tenant", ""),
        }
        with open(os.path.join(WORK, "share_tokens.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return True
    except Exception as e:  # noqa: BLE001
        try:
            with open(
                os.path.join(WORK, "m_st_errors.log"), "a", encoding="utf-8"
            ) as f:
                f.write("%s %r\n" % (datetime.now().isoformat(), e))
        except Exception:  # noqa: BLE001
            pass
        return False


def append_events(events):
    clean = validate_events(events)
    os.makedirs(WORK, exist_ok=True)
    with open(EVENTS, "a", encoding="utf-8") as f:
        for rec in clean:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    # K-PUSH-002 A2 兼容：页面带 st（旧 picks ?st= 归因）→ 校验并落 visit 账
    for rec in clean:
        st = str(rec.get("st") or "")
        if rec.get("event") == "m_page_view" and st.startswith("st."):
            _st_visit(st)  # 失败静默（归因不挡埋点主链路）
    return len(clean)


# ── HTTP 适配（由 portal_api 早分支调用）─────────────────────
def _qs(qs):
    from urllib.parse import parse_qs

    return {k: v[0] for k, v in parse_qs(qs or "").items()}


def _body(handler):
    try:
        n = int(handler.headers.get("Content-Length") or 0)
        raw = handler.rfile.read(n) if n else b"{}"
        return json.loads(raw.decode("utf-8") or "{}")
    except (ValueError, OSError):
        raise ApiError("请求体不是合法 JSON")


# ── 订阅页飞书免登（2026-10-25，老田拍板"要"）───────────────
NOTIFY_SECRET = os.path.join(SECRETS, "zb.ops.notify.json")
FEISHU_OPEN = "https://open.feishu.cn/open-apis"
AUTH_REDIRECT_PATH = "/m/auth/callback"


def _notify_app():
    return _read_json(NOTIFY_SECRET, {}) or {}


def _feishu_app_token():
    """应用级 app_access_token（authen/v1/access_token 需要）。"""
    import urllib.request

    app = _notify_app()
    if not (app.get("app_id") and app.get("app_secret")):
        raise ApiError("通知应用凭据缺失", 500)
    req = urllib.request.Request(
        FEISHU_OPEN + "/auth/v3/app_access_token/internal",
        data=json.dumps(
            {"app_id": app["app_id"], "app_secret": app["app_secret"]}
        ).encode(),
        headers={"Content-Type": "application/json"},
    )
    with safe_urlopen(req, timeout=15) as r:
        d = json.loads(r.read().decode())
    if d.get("code") != 0:
        raise ApiError("获取 app_access_token 失败：%s" % d.get("msg"), 502)
    return d["app_access_token"], app["app_id"]


def _make_state(ret_path, page_token, ttl=600):
    now = int(time.time())
    return m_tokens.sign(
        {
            "scope": "state",
            "ret": ret_path,
            "t": page_token,
            "nbf": now,
            "exp": now + ttl,
        }
    ), now + ttl


def _session_open_id(handler):
    """从 Cookie m_sess 解析 open_id；无/无效返回 ""。"""
    raw = ""
    try:
        raw = getattr(handler, "headers", {}).get("Cookie") or ""
    except Exception:  # noqa: BLE001
        return ""
    tok = ""
    for part in raw.split(";"):
        part = part.strip()
        if part.startswith("m_sess="):
            tok = part[len("m_sess=") :]
            break
    if not tok:
        return ""
    try:
        payload = m_tokens.verify(tok, scope="session")
        return str(payload.get("open_id") or "")
    except m_tokens.TokenError:
        return ""


def auth_start(handler, qs):
    """GET /api/m/auth/start → 302 到飞书授权页（state 里带返回页/token）。"""
    q = _qs(qs)
    page_token = q.get("t") or ""
    ret = q.get("return") or "/m/subscription"
    try:
        state, _exp = _make_state(ret, page_token)
        _tok, app_id = _feishu_app_token()
        import urllib.parse as _up

        redirect_uri = base_url() + AUTH_REDIRECT_PATH
        url = (
            FEISHU_OPEN
            + "/authen/v1/index?"
            + _up.urlencode(
                {"app_id": app_id, "redirect_uri": redirect_uri, "state": state}
            )
        )
        handler.send_response(302)
        handler.send_header("Location", url)
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
    except ApiError as e:
        handler._json({"ok": False, "error": str(e)}, e.status)


def auth_exchange(handler, body):
    """POST /api/m/auth/exchange {code,state} → 换 open_id、种 Cookie、返回跳转地址。"""
    import urllib.request

    code = str(body.get("code") or "")
    state = str(body.get("state") or "")
    if not code:
        raise ApiError("缺少授权码 code", 400)
    try:
        st = m_tokens.verify(state, scope="state")
    except m_tokens.TokenError as e:
        raise ApiError("授权状态无效或已过期：%s" % e, 401)
    app_token, _app_id = _feishu_app_token()
    req = urllib.request.Request(
        FEISHU_OPEN + "/authen/v1/access_token",
        data=json.dumps({"grant_type": "authorization_code", "code": code}).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + app_token,
        },
    )
    with safe_urlopen(req, timeout=15) as r:
        d = json.loads(r.read().decode())
    if d.get("code") != 0 or not (d.get("data") or {}).get("open_id"):
        raise ApiError("换取用户身份失败：%s" % d.get("msg"), 502)
    open_id = d["data"]["open_id"]
    now = int(time.time())
    sess = m_tokens.sign(
        {"scope": "session", "open_id": open_id, "nbf": now, "exp": now + 7 * 86400}
    )
    secure = " Secure;" if base_url().startswith("https://") else ""
    cookie = "m_sess=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=%d;%s" % (
        sess,
        7 * 86400,
        secure,
    )
    ret = str(st.get("ret") or "/m/subscription")
    page_token = str(st.get("t") or "")
    redirect = ret + (("?t=" + page_token) if page_token else "")
    payload = json.dumps(
        {"ok": True, "open_id_masked": open_id[:6] + "**", "redirect": redirect},
        ensure_ascii=False,
    ).encode()
    handler.send_response(200)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(payload)))
    handler.send_header("Set-Cookie", cookie)
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(payload)


def auth_check(handler):
    oid = _session_open_id(handler)
    handler._json(
        {
            "ok": True,
            "authed": bool(oid),
            "open_id_masked": (oid[:6] + "**") if oid else "",
        }
    )


def _handler_open_ids():
    """可核验的工单处理人身份白名单（飞书 open_id）。

    来源：env COMM_OWNER_OPEN_ID（逗号/分号分隔）∪ owners.json（open_ids/owners，
    与催办提醒的 owner 同一口径）。名单不可读/为空 → 返回空列表，答复侧失败关闭。
    """
    out = []
    ov = os.environ.get("COMM_OWNER_OPEN_ID") or ""
    for part in ov.replace(";", ",").split(","):
        part = part.strip()
        if part and part not in out:
            out.append(part)
    try:
        import ticket_loop

        with open(ticket_loop.OWNER_FILE, encoding="utf-8") as f:
            d = json.load(f)
        ids = d if isinstance(d, list) else (d.get("open_ids") or d.get("owners") or [])
        for x in ids:
            s_ = str(x or "").strip()
            if s_ and s_ not in out:
                out.append(s_)
    except Exception:  # noqa: BLE001 —— 名单不可读=空名单=失败关闭
        pass
    return out


# 2026-10-27 审计修复：内部只读汇总端点仅限本机（Hermes）或带 portal token；
# 防止经 nginx 反代被局域网/公网直接读取（无页面令牌）。
_PROTECTED_GET = {
    "/api/m/score/core",
    "/api/m/strategy/summary",
    "/api/m/feed/summary",
    "/api/m/customer/summary",
}


def _allow_internal(handler):
    try:
        local = getattr(handler, "_is_local_req", lambda: True)()
    except Exception:  # noqa: BLE001
        local = True
    if local:
        return True
    auth = getattr(handler, "_check_portal_token", None)
    if callable(auth):
        try:
            return bool(auth())
        except Exception:  # noqa: BLE001
            return False
    return False


def handle_get(handler, path, qs):
    try:
        q = _qs(qs)
        if path in _PROTECTED_GET and not _allow_internal(handler):
            handler._json({"ok": False, "error": "需要页面令牌或本机访问"}, 403)
            return
        if path == "/api/m/health":
            s = m_contract.validate_all()
            handler._json(
                {"ok": True, "contract": s, "base_url": base_url(), "ts": time.time()}
            )
            return
        if path == "/api/m/auth/start":
            auth_start(handler, qs)
            return
        if path == "/api/m/auth/check":
            auth_check(handler)
            return
        if path == "/api/m/strategy/summary":
            import m_summary

            handler._json(m_summary.strategy_summary())
            return
        if path == "/api/m/feed/summary":
            import m_summary

            handler._json(m_summary.feed_summary())
            return
        if path == "/api/m/customer/summary":
            import m_summary

            handler._json(m_summary.customer_summary())
            return
        if path == "/api/m/score/core":
            try:
                import m_score

                handler._json(m_score.core_snapshot())
            except Exception as e:  # noqa: BLE001
                handler._json({"ok": False, "error": str(e)[:200]})
            return

        if path == "/api/m/data":
            tok = q.get("t") or ""
            payload = m_tokens.verify(tok, scope="page")
            ctx_open = _session_open_id(handler)
            data = build(
                payload.get("scene"),
                code=payload.get("code") or "",
                id=payload.get("id") or "",
                open_id=ctx_open,
            )
            data["authed"] = bool(ctx_open)
            data["nav"] = {
                s: page_url(s)[0]
                for s in (
                    "entry",
                    "picks",
                    "position",
                    "daily",
                    "ledger",
                    "lineage",
                    "notify",
                    "subscription",
                )
            }
            data["_token"] = {"scene": payload.get("scene"), "exp": payload.get("exp")}
            handler._json(data)
            return
        raise ApiError("not found", 404)
    except ApiError as e:
        handler._json({"ok": False, "error": str(e)}, e.status)
    except m_tokens.TokenError as e:
        handler._json({"ok": False, "error": "令牌无效：%s" % e}, 401)
    except Exception as e:  # noqa: BLE001
        handler._json({"ok": False, "error": "服务异常：%s" % str(e)[:200]}, 500)


# ── P2 外部分享 /s/<share_token> ────────────────────────────
SHARE_SCENES = {
    "picks": "/m/picks",
    "chan": "/m/chan",
    "position": "/m/position",
    "subscription": "/m/subscription",
    "notify": "/m/notify",
}


def share_target(token):
    """校验分享令牌 → (scene, page_url) ；越权/过期抛 TokenError/ApiError。"""
    payload = m_tokens.verify(token, scope="share")
    resource = str(payload.get("resource") or "")
    if resource not in SHARE_SCENES:
        raise ApiError("分享资源未登记：%s" % resource, 403)
    auth = m_contract.load("auth").get("restrictions") or {}
    if resource in (auth.get("share_forbidden_scenes") or []):
        raise ApiError("该页面禁止外部分享", 403)
    url, _ = page_url(
        resource,
        code=payload.get("code") or "",
        id=payload.get("id") or "",
        extra={"from": "share"},
    )
    return resource, url


def handle_share(handler, path):
    """GET /s/<token> → 302 到 /m 页面（令牌过期/越权拒绝）。"""
    try:
        tok = path[len("/s/") :].strip("/")
        if not tok:
            raise ApiError("缺少分享令牌", 400)
        resource, url = share_target(tok)
        # 302 用相对 Location：同源跟随（隧道/局域网/自定义域名都成立，不写死 base）
        from urllib.parse import urlsplit as _us

        _p = _us(url)
        _loc = _p.path + (("?" + _p.query) if _p.query else "")
        handler.send_response(302)
        handler.send_header("Location", _loc)
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
    except m_tokens.TokenError as e:
        handler._json({"ok": False, "error": "分享链接无效或已过期：%s" % e}, 401)
    except ApiError as e:
        handler._json({"ok": False, "error": str(e)}, e.status)
    except Exception as e:  # noqa: BLE001
        handler._json({"ok": False, "error": "服务异常：%s" % str(e)[:200]}, 500)


def handle_post(handler, path):
    try:
        if path == "/api/m/link":
            auth = getattr(handler, "_check_portal_token", None)
            if callable(auth) and not auth():
                handler._json({"ok": False, "error": "未授权"}, 403)
                return
            body = _body(handler)
            scene = str(body.get("scene") or "")
            url, payload = page_url(
                scene,
                code=str(body.get("code") or ""),
                id=str(body.get("id") or ""),
                extra={"tab": body.get("tab")} if body.get("tab") else None,
            )
            handler._json({"ok": True, "url": url, "exp": payload.get("exp")})
            return
        if path == "/api/m/share":
            auth = getattr(handler, "_check_portal_token", None)
            if callable(auth) and not auth():
                handler._json({"ok": False, "error": "未授权"}, 403)
                return
            body = _body(handler)
            resource = str(body.get("resource") or body.get("scene") or "")
            if resource not in SHARE_SCENES:
                raise ApiError("分享资源未登记：%s" % resource, 400)
            ttl = int(body.get("ttl") or 0) or None
            tok, payload = m_tokens.make_share_token(
                resource,
                id=str(body.get("id") or ""),
                ttl=ttl
                or (
                    m_contract.load("auth")
                    .get("token", {})
                    .get("ttl_sec", {})
                    .get("share")
                    or 604800
                ),
                extra={"code": body.get("code")} if body.get("code") else None,
            )
            handler._json(
                {"ok": True, "url": base_url() + "/s/" + tok, "exp": payload.get("exp")}
            )
            return
        if path == "/api/m/auth/exchange":
            body = _body(handler)
            auth_exchange(handler, body)
            return
        if path == "/api/m/ticket/nudge":
            body = _body(handler)
            payload = m_tokens.verify(str(body.get("t") or ""), scope="page")
            if payload.get("scene") != "ticket":
                raise ApiError("令牌场景不符", 400)
            tid = str(payload.get("id") or "")
            import ticket_loop

            handler._json(ticket_loop.nudge(tid, by="customer"))
            return
        if path == "/api/m/ticket/reply":
            # 三重授权：①工单（页面令牌 scene/id）②处理人（飞书 OAuth 会话 open_id
            # ∈ owners.json 处理人名单）③动作（reply）。缺任一环失败关闭，绝不进发送链。
            body = _body(handler)
            payload = m_tokens.verify(str(body.get("t") or ""), scope="page")
            if payload.get("scene") != "ticket":
                raise ApiError("令牌场景不符", 400)
            tid = str(payload.get("id") or "")
            oid = _session_open_id(handler)
            if not oid:
                raise ApiError("答复须先以处理人飞书身份登录（/api/m/auth/start）", 401)
            try:
                allowed = oid in _handler_open_ids()
            except Exception:  # noqa: BLE001
                allowed = False
            if not allowed:
                raise ApiError("当前登录身份不是该工单处理人，无权答复", 403)
            text = str(body.get("text") or "").strip()
            if not text:
                raise ApiError("回复内容为空", 400)
            import ticket_loop

            handler._json(ticket_loop.reply(tid, text, by="处理人·" + oid[:6] + "**"))
            return
        if path == "/api/m/strategy/submit":
            body = _body(handler)
            payload = m_tokens.verify(str(body.get("t") or ""), scope="page")
            if payload.get("scene") != "strategy":
                raise ApiError("令牌场景不符", 400)
            import m_strategy

            r = m_strategy.submit(
                body.get("method") or "",
                body.get("window") or "1y",
                operator_open_id=str(payload.get("open_id") or ""),
                token_hash=str(body.get("t") or "")[:16],
            )
            handler._json(r)
            return
        if path == "/api/m/beacon":
            body = _body(handler)
            n = append_events(body.get("events") or [])
            handler._json({"ok": True, "accepted": n})
            return
        raise ApiError("not found", 404)
    except ApiError as e:
        handler._json({"ok": False, "error": str(e)}, e.status)
    except m_tokens.TokenError as e:
        handler._json({"ok": False, "error": "令牌无效：%s" % e}, 401)
    except Exception as e:  # noqa: BLE001
        handler._json({"ok": False, "error": "服务异常：%s" % str(e)[:200]}, 500)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--link", default="")
    ap.add_argument("--code", default="")
    ap.add_argument("--scene", default="")
    args = ap.parse_args()
    if args.check:
        print(
            json.dumps(
                {"contract": m_contract.validate_all(), "base_url": base_url()},
                ensure_ascii=False,
            )
        )
    elif args.link and args.scene:
        url, p = page_url(args.scene, code=args.code)
        print(url)
    else:
        url, _ = page_url("picks")
        print(
            json.dumps(
                {"example_url": url, "health": m_contract.validate_all()},
                ensure_ascii=False,
            )
        )
