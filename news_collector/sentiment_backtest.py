# -*- coding: utf-8 -*-
"""个股消息情绪 × 次日涨跌 相关性回测（v2 重建版）。

背景：观察记录 v1（n=49）的回测脚本未落盘，本脚本按同思路重建并
固化口径，供每日计划任务 StockCorrBacktest 自动重跑。

数据链：
  news.db news 表（jev = LLM 情绪判定 {bias: bull/bear/neutral, conf}）
  → 个股归属三路：① stocks 列真 ts_code ② stocks 列自选股名称→
  news_config watchlist 映射 ③ 标题前缀代码 [600853 龙建股份] /
  [个股研报·org] 300059 标题（announcement/survey/report 源）
  → 按 (code, 新闻日期) 去重取最高置信判定（一码一日一样本）
  → datamart trade_cal 定交易日、ts_daily 取收盘与当日全市场均值
  → 分组统计 + Pearson 相关

样本口径（计入 n）：
  A股（.SH/.SZ/.BJ）、ts 可解析（MM-DD 缺年份用 created_at 年份补）、
  T+1 有行情（base/t1 收盘均存在）；T+2 缺失（样本太新）单独标注。

映射规则：
  D = 新闻日期；base = ≤D 最近交易日；t1 = D 后第一个交易日；
  t2 = t1 后第一个交易日。
  pct1 = close(t1)/close(base) - 1；pct2 = close(t2)/close(t1) - 1。
  （非交易日新闻：base=节前收盘、t1=节后首日 → 度量节后首日反应；
   交易日新闻：base=当日收盘、t1=次日 → 度量次日反应，口径一致。

用法：
  python sentiment_backtest.py           # 跑回测，写 JSON，打印报告
  python sentiment_backtest.py --push    # 达成里程碑（n≥200 起，每 +100）
                                         # 且未推送过时发飞书
输出：
  data/sentiment_bt_result.json（原子写）
  data/sentiment_bt_pushed.json（推送水位）
"""

import argparse
import json
import math
import os
import re
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
QBT = os.path.normpath(os.path.join(HERE, "..", "quant_backtest"))
sys.path.insert(0, QBT)

DB = os.path.join(HERE, "news.db")
CFG = os.path.join(HERE, "news_config.json")
RESULT = os.path.join(HERE, "data", "sentiment_bt_result.json")
PUSHED = os.path.join(HERE, "data", "sentiment_bt_pushed.json")
A_SHARE_SUFFIX = (".SH", ".SZ", ".BJ")
SCORE = {"bull": 1.0, "bear": -1.0, "neutral": 0.0}
SCORE_CN = {"bull": "偏多", "bear": "偏空", "neutral": "中性"}
MILESTONE_BASE = 200
MILESTONE_STEP = 100

RE_TITLE_CODE = re.compile(r"^\[(\d{6})[\s·\]]")  # [600853 龙建股份]
RE_TITLE_CODE2 = re.compile(r"^\[[^\]]+\]\s*(\d{6})\s")  # [个股研报·org] 300059 x


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _suffix(c6):
    """6 位代码 → 交易所后缀；无法判定返回 None。"""
    if c6.startswith(("60", "68", "90")):
        return ".SH"
    if c6.startswith(("00", "30", "20")):
        return ".SZ"
    if c6.startswith(("43", "83", "87", "92")):
        return ".BJ"
    return None


def _watch_names():
    """news_config watchlist → {A股名称/别名: 'NNNNNN.XX'}（仅 A 股条目）。"""
    out = {}
    try:
        with open(CFG, encoding="utf-8") as f:
            wl = json.load(f).get("watchlist", []) or []
    except Exception as e:
        print(
            "BT watchlist cfg read err %s" % str(e)[:100]
        )  # C2批2 吞错落盘（配置损坏将静默跑空回测）
        return out
    for w in wl:
        code = str(w.get("code") or "").strip().lower()
        m = re.match(r"^(?:sh|sz|bj)?(\d{6})$", code)
        if not m:
            continue
        suf = _suffix(m.group(1))
        if not suf:
            continue
        full = m.group(1) + suf
        for term in [w.get("name")] + list(w.get("aliases") or []):
            t = (term or "").strip()
            if t:
                out[t] = full
    return out


def _parse_day(ts, created_at):
    """news.ts → 'YYYY-MM-DD'；'MM-DD HH:MM' 用 created_at 年份补。"""
    s = str(ts or "").strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    m = re.match(r"^(\d{2})-(\d{2})\b", s)
    if m:
        y = str(created_at or "")[:4]
        if y.isdigit() and len(y) == 4:
            return "%s-%s-%s" % (y, m.group(1), m.group(2))
    return None


def _load_samples():
    """news.db → 去重前原始 A 股判定列表 [{code, day, bias, conf, scope, source}]。

    K-ORG-156：追加捕获 scope（scope 分布统计用；一码一日去重口径不变）。
    K-ORG-167（E7）：追加捕获 source（来源异质衰减实证用；一码一日去重
    取最高置信那条的 source 一并归档）。
    K-ORG-173（承接 K-ORG-169）：追加 impact，供 CAR 分层实证。"""
    if not os.path.exists(DB):
        return []
    wnames = _watch_names()
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute(
        "SELECT ts, created_at, title, stocks, source, jev FROM news "
        "WHERE jev IS NOT NULL AND jev != ''"
    )
    raw = []
    for ts, created_at, title, stocks, source, jev in cur.fetchall():
        day = _parse_day(ts, created_at)
        if not day:
            continue
        try:
            j = json.loads(jev)
        except Exception:
            continue  # 豁免（C2批2）：jev 坏行跳过，正常容错
        bias = j.get("bias")
        conf = j.get("conf")
        scope = j.get("scope") or ""
        impact = str(j.get("impact") or "unknown").strip().lower()
        if impact not in ("low", "medium", "high"):
            impact = "unknown"
        if bias not in SCORE or not isinstance(conf, (int, float)):
            continue
        codes = set()
        if stocks:
            try:
                lst = json.loads(stocks)
            except Exception:
                lst = []
            for x in lst if isinstance(lst, list) else []:
                if not isinstance(x, str):
                    continue
                if x.endswith(A_SHARE_SUFFIX):
                    codes.add(x)
                elif x in wnames:
                    codes.add(wnames[x])
        title = title or ""
        for m in (RE_TITLE_CODE.match(title), RE_TITLE_CODE2.match(title)):
            if m:
                suf = _suffix(m.group(1))
                if suf:
                    codes.add(m.group(1) + suf)
        for c in codes:
            raw.append(
                {
                    "code": c,
                    "day": day,
                    "bias": bias,
                    "conf": float(conf),
                    "scope": scope,
                    "impact": impact,
                    "source": str(source or ""),
                }
            )
    conn.close()
    # 一码一日取最高置信（同置信 bear 优先，保守口径）
    rank = {"bear": 0, "bull": 1, "neutral": 2}
    best = {}
    for r in raw:
        k = (r["code"], r["day"])
        old = best.get(k)
        if old is None or (r["conf"], -rank[r["bias"]]) > (
            old["conf"],
            -rank[old["bias"]],
        ):
            best[k] = r
    return sorted(best.values(), key=lambda r: (r["day"], r["code"]))


def _trade_days():
    """datamart trade_cal → 升序交易日列表 'YYYYMMDD'。"""
    import datamart

    df = datamart.read("trade_cal")
    if df is None or df.empty or "cal_date" not in df.columns:
        return []
    if "is_open" in df.columns:
        df = df[df["is_open"].astype(float) == 1]
    return sorted(str(v)[:8].replace("-", "") for v in df["cal_date"])


def _daily_panel(start, end):
    """datamart daily → (close[code][d8], pct[code][d8], mkt_mean[d8])。

    pct 优先用 ts_daily 的 pct_chg（除权日按交易所调整昨收，比收盘
    比值准确），缺失时回退收盘比值。"""
    import datamart

    fields = ["ts_code", "trade_date", "close", "pct_chg"]
    df = datamart.read("daily", start=start, end=end, fields=fields)
    close, pct, mkt = {}, {}, {}
    if df is None or df.empty:
        return close, pct, mkt
    has_pct = "pct_chg" in df.columns
    for i in range(len(df)):
        code = df["ts_code"].iat[i]
        d8 = str(df["trade_date"].iat[i])[:8].replace("-", "")
        try:
            c = float(df["close"].iat[i])
        except Exception:
            continue  # 豁免（C2批2）：行情坏值行跳过，正常容错
        close.setdefault(code, {})[d8] = c
        r = None
        if has_pct:
            try:
                r = float(df["pct_chg"].iat[i]) / 100.0
            except Exception:
                r = None
        if r is not None:
            pct.setdefault(code, {})[d8] = r
            mkt.setdefault(d8, []).append(r)
    return close, pct, {d: sum(v) / len(v) for d, v in mkt.items() if v}


def _corr(xs, ys):
    n = len(xs)
    if n < 3 or len(ys) != n:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    sx = math.sqrt(sum((a - mx) ** 2 for a in xs))
    sy = math.sqrt(sum((b - my) ** 2 for b in ys))
    return round(sxy / (sx * sy), 3) if sx > 0 and sy > 0 else None


def run():
    tds = _trade_days()
    if not tds:
        print("BT no trade_cal, abort")
        return None
    set(tds)
    today = datetime.now().strftime("%Y%m%d")
    tds = [t for t in tds if t <= today]  # 未来日无行情，截到今天
    tidx = {d: i for i, d in enumerate(tds)}

    def base_of(day):
        d8 = day.replace("-", "")
        le = [t for t in tds if t <= d8]
        return le[-1] if le else None

    def next_of(d8):
        i = tidx.get(d8)
        return tds[i + 1] if i is not None and i + 1 < len(tds) else None

    samples = _load_samples()
    if not samples:
        print("BT no samples in news.db")
        return None
    days = sorted({r["day"] for r in samples})
    start = days[0].replace("-", "")
    end = tds[-1]  # 已截到今天
    print("BT daily panel %s ~ %s ..." % (start, end))
    close, pct, mkt = _daily_panel(start, end)

    def _day_pct(d_cur, d_prev):
        """单交易日涨跌%：优先 ts_daily pct_chg（除权校正），回退收盘比值。"""
        if not (d_cur and d_prev):
            return None
        p = pct.get(r["code"], {}).get(d_cur)
        if p is not None:
            return round(p * 100, 2)
        cc = close.get(r["code"], {}).get(d_cur)
        cp = close.get(r["code"], {}).get(d_prev)
        if cc and cp and cp > 0:
            return round((cc / cp - 1) * 100, 2)
        return None

    rows, drop_no_px = [], 0
    for r in samples:
        b = base_of(r["day"])
        t1 = next_of(b) if b else None
        t2 = next_of(t1) if t1 else None
        t3 = next_of(t2) if t2 else None
        t4 = next_of(t3) if t3 else None
        t5 = next_of(t4) if t4 else None
        cb = close.get(r["code"], {}).get(b) if b else None
        c1 = close.get(r["code"], {}).get(t1) if t1 else None
        if not (cb and c1 and cb > 0):
            drop_no_px += 1
            continue
        row = dict(r)
        # T+1/2/3/5 涨跌（K-ORG-156 扩到 1/3/5 日窗口，借鉴 Qlib 事件研究）
        row["pct1"] = _day_pct(t1, b)
        row["pct2"] = _day_pct(t2, t1)
        row["pct3"] = _day_pct(t3, t2)
        row["pct5"] = _day_pct(t5, t4)
        row["mkt1"] = round(mkt.get(t1, 0.0) * 100, 2) if t1 in mkt else None
        row["mkt2"] = round(mkt.get(t2, 0.0) * 100, 2) if (t2 and t2 in mkt) else None
        row["mkt3"] = round(mkt.get(t3, 0.0) * 100, 2) if (t3 and t3 in mkt) else None
        row["mkt5"] = round(mkt.get(t5, 0.0) * 100, 2) if (t5 and t5 in mkt) else None
        # 逐行超额收益（vs 当日全市场均值，tiers/scope 分层用）
        row["vs_mkt1"] = (
            round(row["pct1"] - row["mkt1"], 2) if row["mkt1"] is not None else None
        )
        row["t1"] = t1
        rows.append(row)

    # ── 分组统计（K-ORG-156 含 T+3/T+5 窗口）────────────────
    groups = {}
    for b in ("bull", "bear", "neutral"):
        sub = [r for r in rows if r["bias"] == b]
        n = len(sub)
        g = {"n": n}
        if n:
            for k in (1, 2, 3, 5):
                subk = [r for r in sub if r.get("pct%d" % k) is not None]
                if not subk:
                    continue
                g["pct%d_avg" % k] = round(
                    sum(r["pct%d" % k] for r in subk) / len(subk), 2
                )
                mk = [r["mkt%d" % k] for r in subk if r.get("mkt%d" % k) is not None]
                g["vs_mkt%d" % k] = (
                    round(g["pct%d_avg" % k] - sum(mk) / len(mk), 2) if mk else None
                )
                g["pct%d_n" % k] = len(subk)
        groups[b] = g

    xs = [SCORE[r["bias"]] * r["conf"] for r in rows]
    ys = [r["pct1"] for r in rows]
    corr_all = _corr(xs, ys)
    hi = [r for r in rows if r["conf"] >= 0.8]
    corr_hi = (
        _corr([SCORE[r["bias"]] * r["conf"] for r in hi], [r["pct1"] for r in hi])
        if len(hi) >= 3
        else None
    )

    # ── conf 分层命中率验证（K-ORG-156，借鉴 finBERT 置信度分层）──
    # 逐档统计方向样本（多头+空头）的 T+1 方向命中率与 vs 市场一致性，
    # 回答核心问题：conf 越高，判定是否真的越准（单调性）。
    CONF_TIERS = ((0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.01))
    DIRS = {"bull": 1.0, "bear": -1.0}
    tiers = []
    for tlo, thi in CONF_TIERS:
        sub = [r for r in rows if tlo <= r["conf"] < thi]
        dr = [r for r in sub if r["bias"] in DIRS]
        t = {"tier": "%.1f~%.1f" % (tlo, thi), "n": len(sub), "n_dir": len(dr)}
        if dr:
            hit = sum(
                1
                for r in dr
                if r["pct1"] is not None and DIRS[r["bias"]] * r["pct1"] > 0
            )
            t["hit_pct1"] = round(hit / len(dr) * 100, 1)
            vm = [r for r in dr if r.get("vs_mkt1") is not None]
            if vm:
                t["dir_vs_mkt1"] = round(
                    sum(DIRS[r["bias"]] * r["vs_mkt1"] for r in vm) / len(vm), 2
                )
            t["corr_pct1"] = _corr(
                [DIRS[r["bias"]] * r["conf"] for r in dr], [r["pct1"] for r in dr]
            )
        tiers.append(t)
    seq = [t["hit_pct1"] for t in tiers if t.get("hit_pct1") is not None]
    mono = all(b >= a for a, b in zip(seq, seq[1:])) if len(seq) >= 2 else None

    # ── scope 分布（K-ORG-156）：全样本分布 + 可成交样本按 scope 分组 ──
    scope_dist = {}
    for r in samples:
        sc = r.get("scope") or "?"
        scope_dist[sc] = scope_dist.get(sc, 0) + 1
    scope_groups = {}
    for r in rows:
        sc = r.get("scope") or "?"
        g = scope_groups.setdefault(sc, {"n": 0, "s1": 0.0, "s2": 0.0, "vm_n": 0})
        g["n"] += 1
        if r["pct1"] is not None:
            g["s1"] += r["pct1"]
        if r.get("vs_mkt1") is not None:
            g["s2"] += r["vs_mkt1"]
            g["vm_n"] += 1
    for sc, g in scope_groups.items():
        g["pct1_avg"] = round(g.pop("s1") / g["n"], 2)
        vm_n = g.pop("vm_n")
        g["vs_mkt1"] = round(g.pop("s2") / vm_n, 2) if vm_n else None

    result = {
        "generated_at": _now_iso(),
        "news_day_range": [days[0], days[-1]],
        "n_dedup_total": len(samples),
        "n_dropped_no_price": drop_no_px,
        "n": len(rows),
        "groups": groups,
        "corr_score_pct1": corr_all,
        "n_high_conf": len(hi),
        "corr_score_pct1_high_conf": corr_hi,
        "conf_tiers": tiers,
        "conf_monotone": mono,
        "scope_dist": scope_dist,
        "scope_groups": scope_groups,
        "rows": rows,
    }

    # ── 报告 ─────────────────────────────────────────────
    print("=" * 62)
    print("情绪 × 次日涨跌 相关性回测 v2   %s" % result["generated_at"][:16])
    print(
        "新闻日期 %s ~ %s  去重样本 %d → 可成交 n=%d"
        % (days[0], days[-1], len(samples), len(rows))
    )
    print("（缺 T+1 行情剔除 %d 条，多为节后(10-08)才补行情的新样本）" % drop_no_px)
    print("-" * 62)
    print(
        "%-6s %6s %10s %10s %10s %10s %10s %10s"
        % (
            "组别",
            "n",
            "T+1均值%",
            "vs市场%",
            "T+2均值%",
            "T+3均值%",
            "T+5均值%",
            "vs市场5%",
        )
    )
    for b in ("bull", "bear", "neutral"):
        g = groups[b]
        print(
            "%-6s %6d %10s %10s %10s %10s %10s %10s"
            % (
                SCORE_CN[b],
                g["n"],
                g.get("pct1_avg", "-"),
                g.get("vs_mkt1", "-"),
                g.get("pct2_avg", "-"),
                g.get("pct3_avg", "-"),
                g.get("pct5_avg", "-"),
                g.get("vs_mkt5", "-"),
            )
        )
    print("-" * 62)
    print(
        "corr(情绪加权分, T+1) = %s   高置信(≥0.8) n=%d corr = %s"
        % (corr_all, len(hi), corr_hi)
    )
    print("-" * 62)
    print(
        "conf 分层（方向样本 T+1 命中率/一致性/相关）  单调=%s"
        % (mono is True and "是" or (mono is False and "否" or "样本不足"))
    )
    print(
        "%-10s %5s %6s %9s %12s %8s"
        % ("conf档", "n", "方向n", "命中%", "方向vs市场", "corr")
    )
    for t in tiers:
        print(
            "%-10s %5d %6d %9s %12s %8s"
            % (
                t["tier"],
                t["n"],
                t["n_dir"],
                ("%.1f%%" % t["hit_pct1"]) if t.get("hit_pct1") is not None else "-",
                _sgn(t.get("dir_vs_mkt1"), "pp"),
                t.get("corr_pct1", "-"),
            )
        )
    if scope_dist:
        print("-" * 62)
        print(
            "scope 分布：%s"
            % " ".join("%s=%d" % (sc, n) for sc, n in scope_dist.items())
        )
        if scope_groups:
            print(
                "可成交按 scope：%s"
                % " ".join(
                    "%s n=%d T+1 %s" % (sc, gg["n"], _sgn(gg.get("pct1_avg")))
                    for sc, gg in scope_groups.items()
                )
            )
    print("=" * 62)

    _write_json(RESULT, result)
    _append_history(result)
    print("saved -> %s" % RESULT)
    return result


HIST = os.path.join(HERE, "data", "history", "sbt_daily.jsonl")


def _append_history(res):
    """K-ORG-186 M2：逐日快照归档（corr/n 趋势序列供数，数据日报 M3 起势图消费）。

    append-only jsonl（data/history/ 随 data/ 同源 .gitignore）；同日重跑会有
    多行，下游按 ts 取每日末条。只归档聚合口径（n/corr/分组 n 与 T+1 均值），
    不带 rows 明细；失败不阻断回测主链。"""
    try:
        os.makedirs(os.path.dirname(HIST), exist_ok=True)
        g = res.get("groups") or {}
        row = {
            "ts": res.get("generated_at"),
            "day_range": res.get("news_day_range"),
            "n": res.get("n"),
            "corr1": res.get("corr_score_pct1"),
            "n_high_conf": res.get("n_high_conf"),
            "corr1_high_conf": res.get("corr_score_pct1_high_conf"),
            "conf_monotone": res.get("conf_monotone"),
            "bull_n": (g.get("bull") or {}).get("n"),
            "bear_n": (g.get("bear") or {}).get("n"),
            "neutral_n": (g.get("neutral") or {}).get("n"),
            "bull_pct1": (g.get("bull") or {}).get("pct1_avg"),
            "bear_pct1": (g.get("bear") or {}).get("pct1_avg"),
        }
        with open(HIST, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print("history -> %s" % HIST)
    except OSError as e:
        print(
            "HISTORY append err %s" % str(e)[:100]
        )  # C2批2 吞错落盘（归档失败不阻断回测主链）


def _sgn(v, suf="%"):
    """带符号百分数；None → '-'。"""
    if v is None:
        return "-"
    return ("+" if v >= 0 else "") + ("%.2f" % v) + suf


def build_bt_card(result):
    """回测结果 → 飞书 interactive 卡片（card_build 域内共用构造器）。

    布局：标题/副标题（n 与新闻期）→ 样本概况 → 三组表现（A股习惯
    红涨绿跌：偏多红/偏空绿）→ 相关性 → 灰色口径脚注。"""
    from card_build import build_card

    g = result["groups"]
    d0, d1 = result["news_day_range"]
    md1 = "样本 n=**%d**（去重 %d · 缺行情剔除 %d，在途样本待行情解锁）" % (
        result["n"],
        result["n_dedup_total"],
        result["n_dropped_no_price"],
    )
    rows = []
    for b in ("bull", "bear", "neutral"):
        gg = g[b]
        if not gg.get("n"):
            continue
        color = {"bull": "red", "bear": "green", "neutral": "grey"}[b]
        rows.append(
            "<font color='%s'>**%s**</font> n=%d ｜ T+1 **%s**（vs市场 %s）"
            "｜ T+2 %s（%s）"
            % (
                color,
                SCORE_CN[b],
                gg["n"],
                _sgn(gg.get("pct1_avg")),
                _sgn(gg.get("vs_mkt1"), "pp"),
                _sgn(gg.get("pct2_avg")),
                _sgn(gg.get("vs_mkt2"), "pp"),
            )
        )
    md3 = "**corr**(情绪加权分 × T+1) = **%s** ｜ 高置信(≥0.8) n=%d，corr = %s\n\n" % (
        result["corr_score_pct1"],
        result["n_high_conf"],
        result["corr_score_pct1_high_conf"],
    )
    # K-ORG-156：conf 分层命中率摘要（方向样本 T+1 命中率随置信档变化）
    conf_line = []
    for t in result.get("conf_tiers", []):
        if t.get("hit_pct1") is not None and t["n_dir"] >= 5:
            conf_line.append("%s档 %s%%(n=%d)" % (t["tier"], t["hit_pct1"], t["n_dir"]))
    if conf_line:
        mono = result.get("conf_monotone")
        md3 += "conf分层T+1命中：%s%s" % (
            " → ".join(conf_line),
            "（单调↑）" if mono is True else ("（非单调）" if mono is False else ""),
        )
    footer = (
        "口径：一码一日去重 · T+1 可成交 · pct_chg 除权校正 ｜ "
        "%s 生成 · StockCorrBacktest 每日 20:35 自动刷新"
        % result["generated_at"][:16].replace("T", " ")
    )
    return build_card(
        "📊 情绪×次日涨跌 回测摘要",
        "n=%d · 新闻 %s ~ %s" % (result["n"], d0, d1[5:]),
        "blue",
        [md1, "\n".join(rows), md3],
        footer=footer,
    )


def _plain_msg(result):
    """纯文本降级模板（卡片发送失败时用）。"""
    g = result["groups"]
    msg = (
        "📊 情绪×次日涨跌 回测刷新（n=%d，新闻 %s~%s）\n"
        "偏多 n=%d T+1 %s（vs市场 %s）\n"
        "偏空 n=%d T+1 %s（vs市场 %s）\n"
        "中性 n=%d T+1 %s\n"
        "corr=%s｜高置信 n=%d corr=%s\n"
        "口径：一码一日去重、T+1 可成交、缺行情剔除"
        % (
            result["n"],
            result["news_day_range"][0],
            result["news_day_range"][1],
            g["bull"]["n"],
            _sgn(g["bull"].get("pct1_avg")),
            _sgn(g["bull"].get("vs_mkt1"), "pp"),
            g["bear"]["n"],
            _sgn(g["bear"].get("pct1_avg")),
            _sgn(g["bear"].get("vs_mkt1"), "pp"),
            g["neutral"]["n"],
            _sgn(g["neutral"].get("pct1_avg")),
            result["corr_score_pct1"],
            result["n_high_conf"],
            result["corr_score_pct1_high_conf"],
        )
    )
    conf_line = []
    for t in result.get("conf_tiers", []):
        if t.get("hit_pct1") is not None and t["n_dir"] >= 5:
            conf_line.append("%s档%s%%" % (t["tier"], t["hit_pct1"]))
    if conf_line:
        msg += "\nconf分层命中：%s" % " / ".join(conf_line)
    return msg


def _maybe_push(result):
    """里程碑推送：n≥200 首次及此后每 +100 推一次（水位防重）。

    优先 interactive 卡片（build_bt_card），失败降级纯文本。"""
    if result is None:
        return
    n = result["n"]
    if n < MILESTONE_BASE:
        print("PUSH skip: n=%d < %d" % (n, MILESTONE_BASE))
        return
    level = (
        MILESTONE_BASE + max(0, (n - MILESTONE_BASE) // MILESTONE_STEP) * MILESTONE_STEP
    )
    state = {}
    if os.path.exists(PUSHED):
        try:
            with open(PUSHED, encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            state = {}
    if state.get("level", 0) >= level and state.get("n", 0) >= n:
        print("PUSH skip: level %d already sent" % state.get("level"))
        return
    ok = False
    try:
        from card_build import push_card

        ok = push_card(build_bt_card(result))
    except Exception as e:
        print("CARD FAIL %s" % str(e)[:120])
    if not ok:
        try:
            import send_feishu

            ok = send_feishu.send_text(_plain_msg(result))
        except Exception as e:
            print("PUSH FAIL %s" % e)
            return
    if ok:
        state.update({"level": level, "n": n, "pushed_at": _now_iso()})
        _write_json(PUSHED, state)
        print("PUSH_OK level=%d n=%d" % (level, n))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--push", action="store_true", help="达标时发飞书")
    args = ap.parse_args()
    res = run()
    if args.push:
        _maybe_push(res)
