# -*- coding: utf-8 -*-
"""标的四面聚合接口 —— `/api/jev/target/{code}`（2026-11-07，v2 升级）。

老田令：把「一只股票的基本面 + 行情 + 情绪 + 全部相关判定 + 后验」
一页出——下游（投研工作台/Agent）最想调的能力（目录 jev_target_drill 的
聚合形态）。

    GET /api/jev/target/600519?days=30&limit=20
        code   6 位代码（或带后缀 ts_code）
        days   判定/行情回看窗，1..180，默认 30
        limit  判定明细返回条数，1..100，默认 20（**只影响 items 长度**：
               total/dist/net_bias 始终覆盖整个窗口，不受 limit 截断）

聚合四块 + 形态量价（全只读，60s 进程内缓存）：
  profile    基本面快照：stock_basic（名称/行业/地域/上市日）× datamart
  quotes     行情面：daily 近 days 日（close/pct_chg/vol/amount/振幅，
             含区间累计涨跌）+ form 形态量价（MA/位置分位/连涨跌/量比/
             振幅/波动率 + 规则化 label，附 basis 可复算）
  sentiment  情绪面：该股**全部**相关消息（含未判定行）的热度/来源/事件/
             极性结构；极性来自 JEV 判定 bias，故称「判定口径情绪」
  judgments  判定面：该股相关 JEV 判定明细（带 evidence_item 溯源路径）
             + T+1 后验（dec_audit 联查），带方向分布
             口径：total/dist/net_bias = 窗口内**全部**命中判定；
             items 只是按时间倒序的前 limit 条（limit 不改变统计）

v2 变更（2026-11-07）：情绪面由「judgments.dist 改名」升级为独立取数块；
quotes 新增 form 派生块；新增 block_status 逐块可用性。v1 字段名全部保留。

只读纪律：news.db / jev_audit.db mode=ro；datamart 读层只读；
不写任何库、不调 Ollama。失败块降级（available:false + reason），不炸整页。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sqlite3
import sys
import time
from urllib.parse import parse_qs

STATION = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NEWS_DB = os.path.join(STATION, "news_collector", "news.db")
AUDIT_DB = os.path.join(STATION, "agent_platform", "data", "jev_audit",
                        "jev_audit.db")

MIN_DAYS, MAX_DAYS, DEF_DAYS = 1, 180, 30
MIN_LIMIT, MAX_LIMIT, DEF_LIMIT = 1, 100, 20
CACHE_TTL_S = 60

_CACHE: dict = {"key": None, "at": 0.0, "data": None}

_CODE_RE = re.compile(r"^(\d{6})(?:\.(SH|SZ|BJ))?$")


def _ro(path):
    return sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"),
                            uri=True, timeout=10)


def _norm_code(raw: str) -> tuple[str, str] | None:
    """'600519'/'600519.SH' → (ts_code '600519.SH', code6 '600519')。"""
    m = _CODE_RE.match((raw or "").strip().upper())
    if not m:
        return None
    code6, suf = m.group(1), m.group(2)
    if not suf:
        suf = "SH" if code6.startswith(("6", "9", "5")) else (
            "BJ" if code6.startswith(("4", "8")) else "SZ")
    return "%s.%s" % (code6, suf), code6


_QP_DIR = os.path.join(STATION, "quant_backtest")


def _dm():
    """取 datamart（只读读层）；sys.path 只插一次，避免重复调用累积。"""
    if _QP_DIR not in sys.path:
        sys.path.insert(0, _QP_DIR)
    import datamart
    return datamart


def _profile(code6: str) -> dict:
    """基本面快照（datamart stock_basic，只读）。"""
    try:
        dm = _dm()
        sb = dm.read("stock_basic", codes=["%s" % code6], limit=5)
        hit = sb[sb["symbol"] == code6] if len(sb) else sb
        if not len(hit):
            return {"available": False, "reason": "stock_basic 无此代码"}
        r = hit.iloc[0]
        return {
            "available": True,
            "name": r.get("name"), "industry": r.get("industry"),
            "area": r.get("area"), "list_date": str(r.get("list_date") or ""),
            "list_status": r.get("list_status"),
        }
    except Exception as e:  # noqa: BLE001
        return {"available": False, "reason": str(e)[:120]}


def _n(v):
    """NaN/None → None，其余转 float；用于「不可用就不装作有」。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def _form(bars: list, cum_chg_pct) -> dict:
    """形态与量价关系（只从已取到的日线派生，不外取数据）。

    刻意做成**规则化 + 带 basis**：label 是人话，basis 是算它的原值，
    下游不信结论可以直接照 basis 复算，避免「AI 随口感叹形态」。
    """
    closes = [b["close"] for b in bars if b.get("close") is not None]
    if len(closes) < 2:
        return {"available": False, "reason": "有效 bar 不足 2 根，无法判形态"}

    def ma(n):
        return round(sum(closes[-n:]) / min(n, len(closes)), 2) if len(closes) >= n else None

    hi, lo = max(closes), min(closes)
    pos = round((closes[-1] - lo) / (hi - lo), 3) if hi > lo else None

    pcts = [b["pct_chg"] for b in bars if b.get("pct_chg") is not None]
    streak, sign = 0, 0
    for p in reversed(pcts):
        s = 1 if p > 0 else (-1 if p < 0 else 0)
        if s == 0:
            break
        if sign == 0:
            sign = s
        elif s != sign:
            break
        streak += 1

    vols = [b["vol"] for b in bars if b.get("vol") is not None]
    vr = None
    if len(vols) >= 20:
        m5 = sum(vols[-5:]) / 5
        m20 = sum(vols[-20:]) / 20
        vr = round(m5 / m20, 3) if m20 else None
    elif len(vols) >= 5:
        m5 = sum(vols[-5:]) / 5
        base = sum(vols) / len(vols)
        vr = round(m5 / base, 3) if base else None

    amps = [b["amplitude"] for b in bars if b.get("amplitude") is not None]
    amp = round(sum(amps) / len(amps), 2) if amps else None

    mean_p = sum(pcts) / len(pcts) if pcts else 0.0
    std = (sum((p - mean_p) ** 2 for p in pcts) / len(pcts)) ** 0.5 if len(pcts) > 1 else None
    cum = cum_chg_pct

    pos_lab = "高位" if (pos is not None and pos >= 0.8) else (
        "低位" if (pos is not None and pos <= 0.2) else "中位")
    if vr is None:
        vol_lab = "量能未知"
    elif vr >= 1.5:
        vol_lab = "放量"
    elif vr <= 0.7:
        vol_lab = "缩量"
    else:
        vol_lab = "平量"
    dir_lab = "上行" if (cum or 0) > 0 else ("下行" if (cum or 0) < 0 else "横盘")

    return {
        "available": True,
        "label": "%s%s%s" % (pos_lab, vol_lab, dir_lab),
        "pos_in_range": pos,
        "ma5": ma(5), "ma20": ma(20), "ma60": ma(60),
        "above_ma20": (closes[-1] > ma(20)) if ma(20) else None,
        "streak": streak, "streak_dir": ("up" if sign > 0 else ("down" if sign < 0 else None)),
        "vol_ratio_5_20": vr, "amplitude_avg": amp,
        "volatility_daily": round(std, 3) if std is not None else None,
        "basis": {
            "last_close": closes[-1], "range": [round(lo, 2), round(hi, 2)],
            "cum_chg_pct": cum, "n_bars": len(closes),
            "note": "pos_in_range=收盘在区间的位置；vol_ratio=近5日均量/近20日均量；"
                    "streak=末端连续同向交易日数；label 由这三项按阈值拼出，可照 basis 复算",
        },
    }


def _quotes(ts_code: str, days: int) -> dict:
    """行情面：近 days 日日线 + 形态量价（datamart 只读）。"""
    try:
        dm = _dm()
        end = dt.date.today().strftime("%Y%m%d")
        start = (dt.date.today() - dt.timedelta(days=int(days * 1.6) + 10)
                 ).strftime("%Y%m%d")  # 自然日→交易日冗余
        d = dm.read("daily", codes=[ts_code], start=start, end=end, limit=400)
        if d is None or not len(d):
            return {"available": False, "reason": "daily 无数据（停牌/未上市/源未就绪）"}
        d = d.sort_values("trade_date").tail(days)
        rows = []
        for r in d.itertuples():
            close, pre = _n(getattr(r, "close", None)), _n(getattr(r, "pre_close", None))
            high, low = _n(getattr(r, "high", None)), _n(getattr(r, "low", None))
            amp = (round((high - low) / pre * 100, 2)
                   if (high is not None and low is not None and pre) else None)
            rows.append({
                "date": str(int(r.trade_date)),
                "close": round(close, 2) if close is not None else None,
                "pct_chg": round(_n(getattr(r, "pct_chg", None)), 2)
                           if _n(getattr(r, "pct_chg", None)) is not None else None,
                "vol": _n(getattr(r, "vol", None)),
                "amount": _n(getattr(r, "amount", None)),
                "amplitude": amp,
            })
        closes = [x["close"] for x in rows if x["close"] is not None]
        cum = (round((closes[-1] / closes[0] - 1) * 100, 2)
               if len(closes) > 1 and closes[0] else None)
        out = {
            "available": True, "bars": rows,
            "window_days": days, "n_bars": len(rows),
            "last_close": closes[-1] if closes else None, "cum_chg_pct": cum,
            "span": [rows[0]["date"], rows[-1]["date"]],
        }
        out["form"] = _form(rows, cum)
        return out
    except Exception as e:  # noqa: BLE001
        return {"available": False, "reason": str(e)[:120]}


def _sentiment(code6: str, name: str, days: int, limit: int) -> dict:
    """情绪面（独立取数，**不复用** judgments 的结果）。

    与判定面的分工：
      judgments = 已判定条目的明细与后验（只含有 jev 的行）
      sentiment = 该股**全部**相关消息的热度/来源/事件/极性结构（含未判定行）
    极性来源必须说清：本仓没有独立情感模型，bias 来自 JEV 判定，
    所以这里给的是「判定口径的情绪」，不是「文本情感打分」。
    """
    since = (dt.datetime.now() - dt.timedelta(days=days)).strftime("%Y-%m-%d %H:%M")
    pat_code = "%" + code6 + "%"
    sql = ("SELECT id, source, ts, title, jev FROM news WHERE ts>=? "
           "AND (stocks LIKE ? OR jev LIKE ?")
    args = [since, pat_code, pat_code]
    if name:
        sql += " OR title LIKE ?"
        args.append("%" + name + "%")
    sql += ") ORDER BY ts DESC LIMIT 2000"
    try:
        db = _ro(NEWS_DB)
        rows = db.execute(sql, args).fetchall()
        db.close()
    except sqlite3.Error as e:
        return {"available": False, "reason": str(e)[:120]}

    hit, by_name = [], 0
    for nid, source, ts, title, jev_text in rows:
        j = {}
        if jev_text:
            try:
                j = json.loads(jev_text) or {}
            except Exception:  # noqa: BLE001
                j = {}
        tags = [str(x) for x in (j.get("stocks") or [])]
        tags += [str(x) for x in (j.get("companies") or [])]
        in_code = any(code6 in t for t in tags)
        in_name = bool(name) and name in (title or "")
        if not (in_code or in_name):
            continue  # LIKE 只做粗筛，这里精确判定，避免「600519」撞「6005190」
        if in_name and not in_code:
            by_name += 1
        hit.append({
            "id": nid, "ts": ts, "source": source, "title": (title or "")[:80],
            "bias": j.get("bias") if j.get("bias") in ("bull", "bear", "neutral") else None,
            "conf": j.get("conf"), "event_type": j.get("event_type") or "unknown",
            "judged": bool(j),
        })

    dist = {"bull": 0, "bear": 0, "neutral": 0}
    src_mix, day_heat, ev_mix = {}, {}, {}
    confs, reps = [], {"bull": [], "bear": []}
    for h in hit:
        src_mix[h["source"] or "unknown"] = src_mix.get(h["source"] or "unknown", 0) + 1
        d10 = (h["ts"] or "")[:10]
        if d10:
            day_heat[d10] = day_heat.get(d10, 0) + 1
        if h["judged"]:
            ev_mix[h["event_type"]] = ev_mix.get(h["event_type"], 0) + 1
            b = h["bias"] or "neutral"
            dist[b] += 1
            c = h["conf"]
            if isinstance(c, (int, float)):
                confs.append(float(c))
            if b in reps and len(reps[b]) < 3:  # 代表标题固定各 3，不随 limit 膨胀
                reps[b].append({"id": h["id"], "ts": h["ts"], "title": h["title"],
                                "conf": c, "evidence_item": "/api/jev/evidence/item?id=%s&code=%s"
                                % (h["id"], code6)})

    judged = sum(dist.values())
    top = lambda m, n=8: [{"k": k, "n": v} for k, v in
                          sorted(m.items(), key=lambda kv: -kv[1])[:n]]
    heat = [{"date": k, "n": day_heat[k]} for k in sorted(day_heat)]
    return {
        "available": True,
        "window_days": days,
        "total": len(hit), "judged": judged, "unjudged": len(hit) - judged,
        "dist": dist,
        "net_bias": round((dist["bull"] - dist["bear"]) / judged, 3) if judged else None,
        "conf_avg": round(sum(confs) / len(confs), 3) if confs else None,
        "source_mix": top(src_mix),
        "event_mix": top(ev_mix),
        "daily_heat": heat,
        "peak_day": max(heat, key=lambda x: x["n"]) if heat else None,
        "reps": reps,
        "match_basis": {
            "code_fields": "jev.stocks / jev.companies 含 %s" % code6,
            "title_field": ("标题含公司名「%s」（仅此一路：%d 条）" % (name, by_name))
                           if name else "无公司名（未取到 profile，仅按代码匹配）",
            "polarity_from": "JEV 判定 bias（非独立文本情感模型），故称「判定口径情绪」",
            "candidate_cap": 2000,
        },
    }


def _judgments(code6: str, days: int, limit: int) -> dict:
    """判定面：该股相关 JEV 判定 + T+1 后验。"""
    try:
        db = _ro(NEWS_DB)
        since = (dt.datetime.now() - dt.timedelta(days=days)
                 ).strftime("%Y-%m-%d %H:%M")
        rows = db.execute(
            "SELECT id, ts, title, jev FROM news "
            "WHERE jev IS NOT NULL AND jev!='' AND ts>=? "
            "AND (stocks LIKE ? OR jev LIKE ?) "
            "ORDER BY ts DESC LIMIT ?",
            (since, "%" + code6 + "%", "%" + code6 + "%",
             max(limit * 50, 5000)),
        ).fetchall()
        db.close()
    except sqlite3.Error as e:
        return {"available": False, "reason": str(e)[:120]}

    items, dist = [], {"bull": 0, "bear": 0, "neutral": 0}
    for nid, ts, title, jev_text in rows:
        try:
            j = json.loads(jev_text)
        except Exception:
            continue
        stocks = j.get("stocks") or []
        comps = j.get("companies") or []
        if code6 not in stocks and code6 not in comps:
            continue  # LIKE 命中的误配精确过滤
        bias = j.get("bias") if j.get("bias") in dist else "neutral"
        dist[bias] += 1
        if len(items) < limit:      # 只截「展示」不截「统计」：total/dist/net_bias 覆盖全窗口
            items.append({
                "id": nid, "ts": ts, "title": (title or "")[:80],
                "bias": bias, "conf": j.get("conf"),
                "event_type": j.get("event_type"),
                "companies": comps,
                "src": j.get("src") or ("prefilter" if j.get("pref") else "model"),
                "evidence_item": "/api/jev/evidence/item?id=%s&code=%s" % (nid, code6),
            })

    # T+1 后验（有 trade_day 的判定占比 + 命中率）
    posterior = {"available": False}
    try:
        adb = _ro(AUDIT_DB)
        pat = "%" + code6 + "%"
        n_all = adb.execute(
            "SELECT COUNT(*) FROM dec_audit WHERE companies LIKE ?",
            (pat,)).fetchone()[0]
        n_hit = adb.execute(
            "SELECT COUNT(*) FROM dec_audit WHERE companies LIKE ? "
            "AND ret IS NOT NULL", (pat,)).fetchone()[0]
        hit_ok = adb.execute(
            "SELECT COUNT(*) FROM dec_audit WHERE companies LIKE ? "
            "AND ret IS NOT NULL AND hit=1", (pat,)).fetchone()[0]
        adb.close()
        posterior = {
            "available": True,
            "judged_total": n_all, "verified": n_hit,
            "hit_rate": round(hit_ok / n_hit, 3) if n_hit else None,
        }
    except Exception:  # noqa: BLE001
        pass

    total = sum(dist.values())   # 窗口内全部精确命中的判定数；不是 len(items)
    return {
        "available": True,
        "window_days": days,
        "total": total, "dist": dist,
        "net_bias": round((dist["bull"] - dist["bear"]) / total, 3) if total else None,
        "posterior": posterior,
        "items": items,
    }


SCHEMA = "jev_target.v2"
BLOCKS = ("profile", "quotes", "sentiment", "judgments")


def compute(code_raw: str, days: int, limit: int) -> dict:
    # key 含数据源路径：否则换了库（测试假库/切换数据面）仍会返回旧缓存
    key = (SCHEMA, NEWS_DB, AUDIT_DB, code_raw, days, limit)
    now = time.time()
    if _CACHE["key"] == key and now - _CACHE["at"] < CACHE_TTL_S:
        return _CACHE["data"]
    nc = _norm_code(code_raw)
    if not nc:
        raise ValueError("code 须为 6 位数字或带 .SH/.SZ/.BJ 后缀")
    ts_code, code6 = nc
    profile = _profile(code6)
    name = profile.get("name") if profile.get("available") else None
    data = {
        "schema": SCHEMA,
        "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "code": code6, "ts_code": ts_code,
        "window_days": days,
        "profile": profile,
        "quotes": _quotes(ts_code, days),
        "sentiment": _sentiment(code6, name, days, limit),
        "judgments": _judgments(code6, days, limit),
        # v1 字段保留：旧消费方读它不会崩，但新字段 sentiment 才是真情绪面
        "sentiment_note": "已升级：情绪面见 sentiment 块（独立取数，含未判定消息）；"
                          "本字段仅为 v1 兼容保留",
    }
    data["block_status"] = {k: bool((data.get(k) or {}).get("available"))
                            for k in BLOCKS}
    _CACHE.update(key=key, at=now, data=data)
    return data


def handle_get(handler, path: str, qs_raw: str) -> None:
    m = re.match(r"^/api/jev/target/([^/?]+)$", path)
    if not m:
        handler._json({"ok": False, "error": "invalid_params",
                       "message": "路径须为 /api/jev/target/{code}"}, 400)
        return
    qs = parse_qs(qs_raw or "")
    try:
        days = int((qs.get("days") or [str(DEF_DAYS)])[-1])
        limit = int((qs.get("limit") or [str(DEF_LIMIT)])[-1])
        if not MIN_DAYS <= days <= MAX_DAYS:
            raise ValueError("days %d..%d" % (MIN_DAYS, MAX_DAYS))
        if not MIN_LIMIT <= limit <= MAX_LIMIT:
            raise ValueError("limit %d..%d" % (MIN_LIMIT, MAX_LIMIT))
        result = compute(m.group(1), days, limit)
    except ValueError as exc:
        handler._json({"ok": False, "error": "invalid_params",
                       "message": str(exc)}, 400)
        return
    except Exception as exc:  # noqa: BLE001
        handler._json({"ok": False, "error": "internal",
                       "message": "%s: %s" % (type(exc).__name__, exc)}, 500)
        return
    handler._json(result)
