# -*- coding: utf-8 -*-
"""郭嘉·预判对账司 — 市场级信号对账（K-ORG-191）· 账本富化（K-ORG-193）

命名取自三国郭嘉（军师祭酒）：十胜十败论=预判框架，遗计定辽东=留一本
身后可对账的账本。职责一句话：把 JEV 每日判定的方向预判按市场细分
（A股/港股/美股/商品/加密/外汇），与次日基准行情对账，沉淀信号级账本
（verdicts.jsonl）与聚合快照（summary.json），供 H5 推演图呈现
「今天的判断是不是前期的预判、第二天是否吻合」与各市场过往能力
（滚动命中率曲线）。属标准情报板块产出，向 Hermes 汇报（公告板登记，
K-ORG-180 收口后挂汇报链）。

对账口径（v1，呈现于 H5 口径节）：
  - 信号日 T = news.created_at 的 CST 日历日；T+1 = 该市场基准 K 线
    序列中日期严格大于 T 的下一根（跨时区市场同口径，预判作用于下一
    根收盘对上一根收盘的涨跌幅）。
  - 命中：bias=bull 且 T+1 涨幅>0，或 bias=bear 且 T+1 涨幅<0；
    bias=neutral 记 skip（不入命中率分母，推演图灰点）；T+1 K 线未出
    记 pending（不入账，下次重跑自动补）；超 40 日仍无 K 线记 no_bar
    （终止重试）。
  - scope ∈ {cn,hk,us,commodity,crypto,fx} 参与对账；macro 不对账
    （口径太泛）；空 scope 入待收编计数。
  - fx 为枚举扩容新市场（K-ORG-191），自扩容日起产生信号，历史无数据。
  - 账本富化（K-ORG-193）：行内携带 title（截断 80 字）与 ents（实体名，
    主源 news.stocks 列 ∪ jev.companies 兜底，去重上限 8），支撑门户
    /api/guojia/drill 智能下钻；对账数学（T+1/命中/聚合）零改动。

基准映射（2026-10-18 探针实证，全部免费公开接口）：
  cn        sh000001   上证指数        sina getKLineData scale=240
  hk        hkHSI      恒生指数        腾讯 ifzq fqkline（qfq）
  us        .INX       标普500         sina US_MinKService.getDailyK（全量）
  commodity GC         COMEX黄金       sina GlobalFuturesService（全量）
  crypto    BTC_USDT   比特币          gate candlesticks 1d（只取已收盘）
  fx        fx_susdcnh 美元/离岸人民币 sina NewForexService.getDayKLine

局限注记（不阻塞 v1，后续单细化）：commodity 单基准对原油/金属信号有
偏；跨时区信号用 CST 日历日近似；幂等键为 (rowid, day, scope)，
rowid 复用由 day+scope 联合防串账。

用法：
    python guojia_reconcile.py                 # 跑批 → verdicts.jsonl + summary.json
    python guojia_reconcile.py --print-summary # 跑批后打印聚合摘要
退出码：0 正常（含个别行情源降级，见 summary.pipeline）；2 致命
（news.db 缺失/查询失败）。零第三方依赖，仅标准库。
"""
import bisect
import json
import os
import sqlite3
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "news.db")
OUT_DIR = os.path.join(HERE, "data", "guojia")
VERDICTS_PATH = os.path.join(OUT_DIR, "verdicts.jsonl")
SUMMARY_PATH = os.path.join(OUT_DIR, "summary.json")

CST = timezone(timedelta(hours=8))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
      "Referer": "https://finance.sina.com.cn"}
SCOPES = ("cn", "hk", "us", "commodity", "crypto", "fx")
SCOPE_CN = {"cn": "A股", "hk": "港股", "us": "美股", "commodity": "商品",
            "crypto": "加密", "fx": "外汇", "macro": "宏观"}
BENCHMARKS = {
    "cn": {"code": "sh000001", "name": "上证指数"},
    "hk": {"code": "hkHSI", "name": "恒生指数"},
    "us": {"code": ".INX", "name": "标普500"},
    "commodity": {"code": "GC", "name": "COMEX黄金"},
    "crypto": {"code": "BTC_USDT", "name": "比特币"},
    "fx": {"code": "fx_susdcnh", "name": "美元/离岸人民币"},
}
ROLLING_WINDOW = 20     # 过往能力曲线滚动窗口（按方向样本数计）
ROLLING_MIN = 5         # 样本 <5 不出点（小样本不输出结论铁律）
NO_BAR_GRACE_DAYS = 40  # 信号日后 40 日仍无基准 K 线 → no_bar 终止重试
TITLE_MAX = 80          # 账本富化：下钻行标题截断（K-ORG-193）
ENTS_MAX = 8            # 账本富化：实体名去重上限（K-ORG-193）


def now_iso():
    return datetime.now(CST).isoformat(timespec="seconds")


def _http(url, tries=2, timeout=15):
    """无代理直连 + 一次重试；全失败抛 RuntimeError（调用方按源降级）。"""
    last = None
    for _ in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            return op.open(req, timeout=timeout).read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.0)
    raise RuntimeError("%s: %s" % (url[:80], last))


def _slice_json(text):
    """剥 jsonp/脚本头：取首个 '['/'"' 到末个 ']'/'"' 之间的载荷。"""
    i, j = text.find("["), text.rfind("]")
    if 0 <= i < j:
        return json.loads(text[i:j + 1])
    i, j = text.find('"'), text.rfind('"')
    if 0 <= i < j:
        return json.loads('"%s"' % text[i + 1:j])
    raise ValueError("no payload")


def _bars_from_closes(pairs):
    """[{"date","close"}...] → 升序 + pct（首根 None）。"""
    bars = sorted(({"date": d, "close": float(c)} for d, c in pairs
                   if d and c), key=lambda b: b["date"])
    out = []
    for i, b in enumerate(bars):
        pct = None
        if i and bars[i - 1]["close"] > 0:
            pct = round((b["close"] / bars[i - 1]["close"] - 1) * 100, 2)
        out.append({"date": b["date"], "close": b["close"], "pct": pct})
    return out


def _fetch_cn():
    t = _http("https://quotes.sina.cn/cn/api/json_v2.php/"
              "CN_MarketDataService.getKLineData?symbol=sh000001"
              "&scale=240&ma=no&datalen=800")
    return [(r["day"], r["close"]) for r in json.loads(t)]


def _fetch_hk():
    t = _http("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
              "?param=hkHSI,day,,,800,qfq")
    rows = json.loads(t)["data"]["hkHSI"]["day"]
    return [(r[0], r[2]) for r in rows]   # [date, open, close, high, low, vol]


def _fetch_us():
    t = _http("https://stock.finance.sina.com.cn/usstock/api/json_v2.php/"
              "US_MinKService.getDailyK?symbol=.INX")
    return [(r["d"], r["c"]) for r in json.loads(t)]


def _fetch_commodity():
    t = _http("https://stock2.finance.sina.com.cn/futures/api/jsonp.php/"
              "var%20t=/GlobalFuturesService.getGlobalFuturesDailyKLine"
              "?symbol=GC")
    return [(r["date"], r["close"]) for r in _slice_json(t)]


def _fetch_crypto():
    t = _http("https://api.gateio.ws/api/v4/spot/candlesticks"
              "?currency_pair=BTC_USDT&interval=1d&limit=800")
    out = []
    for r in json.loads(t):
        if str(r[7]).lower() != "true":   # 未收盘窗不入账
            continue
        day = datetime.fromtimestamp(int(r[0]), timezone.utc).strftime("%Y-%m-%d")
        out.append((day, r[2]))           # [ts, quote_vol, close, high, low, open, ...]
    return out


def _fetch_fx():
    t = _http("https://vip.stock.finance.sina.com.cn/forex/api/jsonp.php/"
              "var%20t=/NewForexService.getDayKLine?symbol=fx_susdcnh&_=1")
    s = _slice_json(t)
    out = []
    for seg in s.split("|"):
        p = seg.strip().split(",")
        if len(p) >= 5 and p[0]:
            out.append((p[0], p[4]))      # date,open,low,high,close
    return out


FETCHERS = {"cn": _fetch_cn, "hk": _fetch_hk, "us": _fetch_us,
            "commodity": _fetch_commodity, "crypto": _fetch_crypto,
            "fx": _fetch_fx}


def fetch_bars(scope):
    """单市场基准日线 → (bars 升序带 pct, None) 或 (None, 错误串)。"""
    try:
        return _bars_from_closes(FETCHERS[scope]()), None
    except Exception as e:  # noqa: BLE001  单源降级不拖垮整批
        return None, str(e)[:120]


def _norm_title(raw):
    """标题归一：去空白 + 截断 TITLE_MAX（K-ORG-193 账本富化）。"""
    return str(raw or "").strip()[:TITLE_MAX] or None


def _parse_ents(raw_stocks, companies):
    """新闻实体表（K-ORG-193）：news.stocks 列（实体名 JSON 数组，主源，
    实证 8754 条）∪ jev.companies（生产常空，兜底）→ 去重保序，上限
    ENTS_MAX。坏 stocks 列容错：非 JSON 整串降级为单实体。"""
    ents = []

    def _push(v):
        v = str(v or "").strip()
        if v and v not in ents:
            ents.append(v)

    if isinstance(raw_stocks, str) and raw_stocks.strip():
        try:
            arr = json.loads(raw_stocks)
        except Exception:  # noqa: BLE001  老数据存在非 JSON 值
            arr = raw_stocks
        if isinstance(arr, list):
            for x in arr:
                _push(x)
        else:
            _push(arr)
    elif isinstance(raw_stocks, list):
        for x in raw_stocks:
            _push(x)
    if isinstance(companies, list):
        for x in companies:
            _push(x)
    return ents[:ENTS_MAX]


def load_signals(db_path, window_days):
    """扫窗口内 JEV 方向信号 → (signals, counts)。

    signals: [{rowid, day, ts, scope, bias, conf, event_type, title,
    ents}]（仅 scope∈SCOPES 且 bias∈{bull,bear,neutral}）；其余入
    counts 观测。title/ents 为 K-ORG-193 富化字段（下钻消费）。
    """
    since = (datetime.now(CST) - timedelta(days=window_days)).strftime(
        "%Y-%m-%d") + " 00:00"
    con = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    try:
        rows = con.execute(
            "SELECT rowid, created_at, jev, title, stocks FROM news "
            "WHERE created_at >= ? AND jev IS NOT NULL AND jev != ''",
            (since,)).fetchall()
    finally:
        con.close()
    signals, counts = [], {"total_jev": 0, "bad_jev": 0, "macro": 0,
                           "empty_scope": 0, "other_scope": 0}
    for rowid, created_at, raw, n_title, n_stocks in rows:
        counts["total_jev"] += 1
        try:
            j = json.loads(raw)
        except Exception:  # noqa: BLE001  坏行计数不中断
            counts["bad_jev"] += 1
            continue
        scope = (j.get("scope") or "").strip().lower()
        bias = j.get("bias")
        if scope == "macro":
            counts["macro"] += 1
            continue
        if not scope:
            counts["empty_scope"] += 1
            continue
        if scope not in SCOPES:
            counts["other_scope"] += 1
            continue
        if bias not in ("bull", "bear", "neutral"):
            continue
        conf = j.get("conf")
        signals.append({
            "rowid": int(rowid), "day": str(created_at or "")[:10],
            "ts": str(created_at or ""), "scope": scope, "bias": bias,
            "conf": conf if isinstance(conf, (int, float)) else None,
            "event_type": (j.get("event_type") or "").strip() or None,
            "title": _norm_title(n_title),
            "ents": _parse_ents(n_stocks, j.get("companies")),
        })
    signals.sort(key=lambda s: (s["day"], s["rowid"]))
    return signals, counts


def _t1_of(bars, day):
    """信号日 T 的 T+1：基准序列中日期严格大于 T 的下一根。"""
    i = bisect.bisect_right([b["date"] for b in bars], day)
    return bars[i] if i < len(bars) else None


def load_verdicts(path):
    """读存量账本 → {(rowid, day, scope): verdict}（末条为准，坏行跳过）。"""
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                v = json.loads(ln)
                out[(int(v["rowid"]), str(v["day"]), str(v["scope"]))] = v
            except Exception:  # noqa: BLE001  坏行容错（口径同 sentiment_backtest）
                continue
    return out


def reconcile(signals, bars_by_scope, existing):
    """对账 → (merged 账本 dict, pending_n)。

    幂等：已有 (rowid, day, scope) 的信号不重算；pending（T+1 未出）
    不入账；超 NO_BAR_GRACE_DAYS 无 K 线记 no_bar 终止重试。
    """
    merged = dict(existing)
    pending = 0
    for s in signals:
        key = (s["rowid"], s["day"], s["scope"])
        if key in merged:
            continue
        bars = bars_by_scope.get(s["scope"]) or []
        t1 = _t1_of(bars, s["day"]) if bars else None
        if t1 is None or t1["pct"] is None:
            last_bar = bars[-1]["date"] if bars else ""
            if last_bar and s["day"] < _shift_day(last_bar, -NO_BAR_GRACE_DAYS):
                merged[key] = _verdict(s, None, None, "no_bar")
            else:
                pending += 1
            continue
        if s["bias"] == "neutral":
            merged[key] = _verdict(s, t1["date"], t1["pct"], "skip")
        else:
            hit = (s["bias"] == "bull") == (t1["pct"] > 0)
            merged[key] = _verdict(s, t1["date"], t1["pct"],
                                   "hit" if hit else "miss")
    return merged, pending


def _shift_day(day, ndays):
    return (datetime.strptime(day, "%Y-%m-%d")
            + timedelta(days=ndays)).strftime("%Y-%m-%d")


def _verdict(s, t1_day, t1_pct, verdict):
    # title/ents 为 K-ORG-193 富化字段：.get 容错旧测试夹具/存量信号
    return {"rowid": s["rowid"], "day": s["day"], "ts": s["ts"],
            "scope": s["scope"], "bias": s["bias"], "conf": s["conf"],
            "event_type": s["event_type"], "t1_day": t1_day,
            "t1_pct": t1_pct, "verdict": verdict, "at": now_iso(),
            "title": s.get("title"), "ents": s.get("ents") or []}


def write_verdicts(path, merged):
    """账本原子回写（去重压缩 + 按日排序；tmp + os.replace）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    rows = sorted(merged.values(),
                  key=lambda v: (v["day"], v["scope"], v["rowid"]))
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path),
                               prefix=os.path.basename(path) + ".",
                               suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for v in rows:
                f.write(json.dumps(v, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    return len(rows)


def aggregate(merged, pipeline, counts, window_days):
    """账本 → summary.json（分市场战绩 + 推演图日序 + 滚动能力曲线）。"""
    markets = {}
    overall = {"n_reconciled": 0, "n_hit": 0, "n_miss": 0, "n_neutral": 0,
               "n_no_bar": 0}
    by_scope = {}
    for v in merged.values():
        by_scope.setdefault(v["scope"], []).append(v)
    for scope in SCOPES:
        vs = by_scope.get(scope, [])
        st = {"name": SCOPE_CN[scope], "benchmark": BENCHMARKS[scope]["name"],
              "code": BENCHMARKS[scope]["code"], "n": 0, "hit": 0, "miss": 0,
              "neutral": 0, "no_bar": 0, "hit_rate": None, "days": [],
              "rolling": [], "last_bar": None}
        if pipeline.get(scope, {}).get("last_bar"):
            st["last_bar"] = pipeline[scope]["last_bar"]
        day_map = {}
        for v in vs:
            st["n"] += 1
            if v["verdict"] == "hit":
                st["hit"] += 1
            elif v["verdict"] == "miss":
                st["miss"] += 1
            elif v["verdict"] == "skip":
                st["neutral"] += 1
            else:
                st["no_bar"] += 1
            d = day_map.setdefault(v["day"], {
                "day": v["day"], "t1_day": None, "t1_pct": None,
                "bull": 0, "bear": 0, "neutral": 0, "hit": 0, "miss": 0})
            if v["bias"] == "bull":
                d["bull"] += 1
            elif v["bias"] == "bear":
                d["bear"] += 1
            else:
                d["neutral"] += 1
            if v["verdict"] == "hit":
                d["hit"] += 1
            elif v["verdict"] == "miss":
                d["miss"] += 1
            if v["t1_day"]:
                d["t1_day"], d["t1_pct"] = v["t1_day"], v["t1_pct"]
        for d in sorted(day_map.values(), key=lambda x: x["day"]):
            d["dir"] = ("bull" if d["bull"] > d["bear"]
                        else "bear" if d["bear"] > d["bull"] else "none")
            if d["dir"] == "none" or d["t1_pct"] is None:
                d["verdict"] = "flat"
            else:
                same = (d["dir"] == "bull") == (d["t1_pct"] > 0)
                d["verdict"] = "hit" if same else "miss"
            st["days"].append(d)
        # 滚动命中率：按信号级真实判定（非日级重算）时序推进，窗口
        # ROLLING_WINDOW，<ROLLING_MIN 不出点（小样本不输出结论铁律）
        samples = sorted(((v["day"], v["verdict"], v["rowid"]) for v in vs
                          if v["verdict"] in ("hit", "miss")),
                         key=lambda x: (x[0], x[2]))
        for i in range(len(samples)):
            lo = max(0, i + 1 - ROLLING_WINDOW)
            win = samples[lo:i + 1]
            if len(win) < ROLLING_MIN:
                continue
            rate = round(sum(1 for _, x, _ in win if x == "hit") / len(win), 4)
            if not st["rolling"] or st["rolling"][-1]["day"] != win[-1][0]:
                st["rolling"].append({"day": win[-1][0], "n": len(win),
                                      "rate": rate})
            else:
                st["rolling"][-1].update({"n": len(win), "rate": rate})
        denom = st["hit"] + st["miss"]
        if denom:
            st["hit_rate"] = round(st["hit"] / denom, 4)
        overall["n_hit"] += st["hit"]
        overall["n_miss"] += st["miss"]
        overall["n_neutral"] += st["neutral"]
        overall["n_no_bar"] += st["no_bar"]
        overall["n_reconciled"] += denom
        markets[scope] = st
    overall["hit_rate"] = (round(overall["n_hit"] / overall["n_reconciled"], 4)
                           if overall["n_reconciled"] else None)
    days_span = sorted({v["day"] for v in merged.values()})
    return {
        "generated_at": now_iso(),
        "window_days": window_days,
        "overall": {**overall, "n_days": len(days_span),
                    "first_day": days_span[0] if days_span else None,
                    "last_day": days_span[-1] if days_span else None},
        "markets": markets,
        "pipeline": {"ran_at": now_iso(), "counts": counts, "sources": pipeline},
    }


def run(db_path=DB_PATH, out_dir=OUT_DIR, window_days=180):
    """跑批主入口：扫描→取行情→对账→落盘→聚合。返回 summary dict。"""
    if not os.path.exists(db_path):
        raise SystemExit("GUOJIA FATAL: news.db 不存在 %s" % db_path)
    signals, counts = load_signals(db_path, window_days)
    pipeline, bars_by_scope = {}, {}
    for scope in SCOPES:
        bars, err = fetch_bars(scope)
        if err:
            pipeline[scope] = {"ok": False, "error": err, "bars": 0,
                               "last_bar": None}
        else:
            bars_by_scope[scope] = bars
            pipeline[scope] = {"ok": True, "bars": len(bars), "error": None,
                               "last_bar": bars[-1]["date"] if bars else None}
    existing = load_verdicts(os.path.join(out_dir, "verdicts.jsonl"))
    merged, pending = reconcile(signals, bars_by_scope, existing)
    n_lines = write_verdicts(os.path.join(out_dir, "verdicts.jsonl"), merged)
    counts["pending"] = pending
    counts["signals_scoped"] = len(signals)
    summary = aggregate(merged, pipeline, counts, window_days)
    summary["pipeline"]["verdict_lines"] = n_lines
    summary_path = os.path.join(out_dir, "summary.json")
    os.makedirs(out_dir, exist_ok=True)
    fd, tmpf = tempfile.mkstemp(dir=out_dir,
                                prefix="summary.json.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=1)
        os.replace(tmpf, summary_path)
    except BaseException:
        if os.path.exists(tmpf):
            os.remove(tmpf)
        raise
    return summary


def main():
    import argparse
    ap = argparse.ArgumentParser(description="郭嘉预判对账司跑批")
    ap.add_argument("--db", default=DB_PATH, help="news.db 路径（默认本目录）")
    ap.add_argument("--out-dir", default=OUT_DIR, help="产物目录（默认 data/guojia）")
    ap.add_argument("--window", type=int, default=180, help="对账回看窗口天数")
    ap.add_argument("--print-summary", action="store_true", help="打印聚合摘要")
    a = ap.parse_args()
    s = run(db_path=a.db, out_dir=a.out_dir, window_days=a.window)
    o = s["overall"]
    print("GUOJIA OK reconciled=%s hit=%s miss=%s rate=%s pending=%s "
          "sources_ok=%d/6 -> %s" % (
              o["n_reconciled"], o["n_hit"], o["n_miss"], o["hit_rate"],
              s["pipeline"]["counts"].get("pending"),
              sum(1 for x in s["pipeline"]["sources"].values() if x["ok"]),
              os.path.join(a.out_dir, "summary.json")))
    if a.print_summary:
        print(json.dumps(s, ensure_ascii=False, indent=1)[:4000])


if __name__ == "__main__":
    try:
        main()
    except SystemExit as e:
        print(e, file=sys.stderr)
        sys.exit(2)
