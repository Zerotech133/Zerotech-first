# -*- coding: utf-8 -*-
"""T7 舆情因子第一版：雪球标注 → 可统计因子 → 分层收益统计（只读管道）。

数据源（全程只读，绝不写任何表）：
  data/xueqiu/xueqiu_info.db
    xq_status(status_id, created_at ISO 字符串, ...)
    xq_status_classified(status_id, event_type, sentiment,
      confidence, prompt_version, norm_tickers, ...)
  · norm_tickers 是 JSON 数组字符串，元素 {"raw","kind","canonical"}；
    只取 kind=='stock_a' 且 canonical 形如 SH/SZ+6 位 的 canonical 作
    股票键（stock_hk 路由不支持，本轮如实记为覆盖缺口）。
  · created_at 是 ISO 字符串：判日期一律 substr(1,10)，禁 strftime
    unixepoch（会得 1970）。
  · 已知指数污染：norm 模型把沪深300/中证500/上证50 标成
    SZ000300/SZ000905/SZ000016（kind=stock_a），显式黑名单剔除。

因子（两因子分开，不合成）：
  因子1 情绪分 senti(date, sym) = (bull - bear) / max(1, bull + bear)
        （按当日提及该股的已分类帖计）
  因子2 热度 mention(date, sym) = 当日提及该股的已分类帖数

对齐：日频因子日 D → 该股 D 后首个交易日 open→close 收益；
     周频因子汇总至周日 → 该股周日后首个交易日 open→close 收益。

分层：每日截面样本稀薄（实测），默认合并为 ISO 周截面；截面内按
因子值排 5 分位（至少 5 股且因子无同分；同分截面跳过），统计前向
收益均值/中位数/单调性。IC 用逐截面
Spearman(因子, ret)，报均值与 ICIR（mean/std）。

label_version 纪律（判例：v1.2-re vs v1.3 广告口径漂移 5.8pp）：
跨版本统计必须分层。主统计用最新版 prompt_version='v1.3'，其余版本
（v1.2-re / v1.3-rule / v1.2）单独附表，同管道同口径。

日线：xq_quote_router.fetch_daily(syms, days=N)（tushare 主源 +
akshare 备援，两源全挂 fail-visible）；days 按最早帖日期自动推算。
个别码全挂 → 记 errs 跳过该码，绝不静默。日线缓存落
C:/aios/automation_runs/xq_t7_factor/daily_cache.json（rerun 免重拉）。
"""
import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

DB_PATH = os.path.join(ROOT, "data", "xueqiu", "xueqiu_info.db")
CACHE_DIR = r"C:\aios\automation_runs\xq_t7_factor"
CACHE_PATH = os.path.join(CACHE_DIR, "daily_cache.json")

MAIN_VERSION = "v1.3"
OFFLINE_ALGORITHM_VERSION = "t7-weekly-v2-offline"

# norm 模型指数污染（kind=stock_a 但实为指数），显式剔除
INDEX_POLLUTION = {"SZ000300", "SZ000905", "SZ000016", "SZ000852",
                   "SZ399001", "SZ399006", "SZ399300", "SZ399005"}


# ── 数据加载（只读）──────────────────────────────────────────
def parse_stock_keys(norm_tickers: str) -> list:
    """norm_tickers JSON → canonical 股票键列表。

    只取 kind=='stock_a' 且 canonical 形如 SH/SZ+6 位数字的键；
    剔除指数污染（INDEX_POLLUTION）；解析失败返回空。
    """
    stocks = []
    try:
        for t in json.loads(norm_tickers or "[]"):
            if t.get("kind") != "stock_a":
                continue
            c = str(t.get("canonical") or "").upper()
            if len(c) == 8 and c[:2] in ("SH", "SZ") \
                    and c[2:].isdigit() and c not in INDEX_POLLUTION:
                stocks.append(c)
    except (ValueError, TypeError):
        return []
    return sorted(set(stocks))


def load_posts(db_path: str = DB_PATH) -> list:
    """读 xq_status × xq_status_classified（只读连接），展平为帖记录。

    返回 list[dict]：{status_id, date, sentiment, version, stocks}
    stocks 为 canonical 股票键列表（stock_a 且 SH/SZ 前缀，剔除指数污染）。
    """
    con = sqlite3.connect("file:%s?mode=ro" % db_path.replace("\\", "/"),
                          uri=True, timeout=30)
    try:
        cur = con.cursor()
        rows = cur.execute(
            "SELECT c.status_id, substr(s.created_at,1,10), c.sentiment,"
            " c.prompt_version, c.norm_tickers"
            " FROM xq_status_classified c"
            " JOIN xq_status s ON s.status_id = c.status_id"
        ).fetchall()
    finally:
        con.close()
    posts = []
    for sid, d, senti, ver, nt in rows:
        if not d:
            continue
        posts.append({"status_id": sid, "date": d,
                      "sentiment": (senti or "neutral").lower(),
                      "version": ver or "?",
                      "stocks": parse_stock_keys(nt)})
    return posts


def filter_version(posts: list, version: str) -> list:
    """label_version 纪律：按 prompt_version 取子集。"""
    return [p for p in posts if p["version"] == version]


# ── 因子构造 ────────────────────────────────────────────────
def daily_factors(posts: list) -> dict:
    """每日截面因子：dict[(date, sym)] -> {'senti', 'mention', 'bull',
    'bear', 'neutral'}。

    senti = (bull - bear) / max(1, bull + bear)；mention = 帖数
    （一帖提多股按多 (date,sym) 键各计 1，两因子同口径）。
    """
    agg = defaultdict(lambda: {"bullish": 0, "bearish": 0, "neutral": 0})
    for p in posts:
        for sym in p["stocks"]:
            k = (p["date"], sym)
            if p["sentiment"] in ("bullish", "bearish", "neutral"):
                agg[k][p["sentiment"]] += 1
    out = {}
    for (d, sym), c in agg.items():
        bull, bear = c["bullish"], c["bearish"]
        out[(d, sym)] = {
            "senti": (bull - bear) / max(1, bull + bear),
            "mention": bull + bear + c["neutral"],
            "bull": bull, "bear": bear, "neutral": c["neutral"],
        }
    return out


# ── 收益对齐 ────────────────────────────────────────────────
def next_day_return(daily_rows: list, post_date: str,
                    max_date: str | None = None) -> float | None:
    """post_date 后首个该股交易日的 open→close 收益。

    daily_rows 的 trade_date 为 YYYYMMDD；max_date 限制首个交易日的
    最晚日期，防止停牌后跨越研究窗口；无可用行情则返回 None。
    """
    if not daily_rows:
        return None
    key = post_date.replace("-", "")
    after = sorted(r["trade_date"] for r in daily_rows
                   if r.get("trade_date") and r["trade_date"] > key)
    if not after or (max_date and after[0] > max_date.replace("-", "")):
        return None
    nxt = next((r for r in daily_rows if r["trade_date"] == after[0]), None)
    o, c = nxt.get("open"), nxt.get("close")
    if not o or not c:
        return None
    return c / o - 1.0


def week_key(date_str: str) -> str:
    """ISO 周键（周一日期），样本不足日合并为周截面用。"""
    d = datetime.strptime(date_str, "%Y-%m-%d")
    monday = d - timedelta(days=d.weekday())
    return monday.strftime("%Y-%m-%d")


# 周期切分锚点（周一）：双周块以 2018-01-01 为固定锚，保证可复现
PERIOD_ANCHOR = datetime(2018, 1, 1)


def period_key(date_str: str, freq: str) -> str:
    """按 freq 返回截面键：daily=当日；weekly=ISO 周一；
    biweekly=自 PERIOD_ANCHOR 起第 n 个 14 天块的周一；
    monthly=当月 1 号。"""
    if freq == "daily":
        return date_str
    d = datetime.strptime(date_str, "%Y-%m-%d")
    if freq == "weekly":
        return week_key(date_str)
    if freq == "biweekly":
        block = (d - PERIOD_ANCHOR).days // 14
        return (PERIOD_ANCHOR + timedelta(days=14 * block)).strftime("%Y-%m-%d")
    if freq == "monthly":
        return d.strftime("%Y-%m-01")
    raise ValueError("不支持的 freq=%s（仅 daily/weekly/biweekly/monthly）" % freq)


def period_cutoff(key: str, freq: str) -> str:
    """截面键 → 截面截止日（周五/块末周日/月末；周内逻辑与 week_key 相同）。

    weekly=周一+6；biweekly=块起点+13；monthly=当月最后一天。
    """
    d = datetime.strptime(key, "%Y-%m-%d")
    if freq == "weekly":
        days = 6
    elif freq == "biweekly":
        days = 13
    elif freq == "monthly":
        nxt = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
        d = nxt - timedelta(days=1)
        days = 0
    else:
        raise ValueError("不支持的 freq=%s" % freq)
    return (d + timedelta(days=days)).strftime("%Y-%m-%d")


def build_sections(posts: list, daily_by_sym: dict,
                   freq: str = "weekly", diagnostics: dict | None = None,
                   valid_week_pairs: set | None = None) -> list:
    """构造截面：[{key, obs:[{sym, senti, mention, ret}]}]。

    freq='daily' 按 POST 日对齐后续首个交易日；'weekly'/'biweekly'/
    'monthly' 按 period_key 合并，同截面同股情绪取日均、热度取帖数
    合计，收益限截面截止日后一周内该股首交易日。
    仅保留 ret 可得的 obs；截面 obs<1 丢弃。可选 diagnostics 记录拒绝原因。
    """
    factors = daily_factors(posts)
    by_key = defaultdict(list)
    for (d, sym), f in factors.items():
        k = d if freq == "daily" else period_key(d, freq)
        by_key[k].append({"sym": sym, "date": d, **f})
    sections = []
    for k in sorted(by_key):
        obs = by_key[k]
        if freq != "daily":
            merged = defaultdict(list)
            for o in obs:
                merged[o["sym"]].append(o)
            obs = []
            cutoff_date = datetime.strptime(period_cutoff(k, freq), "%Y-%m-%d")
            cutoff = cutoff_date.strftime("%Y-%m-%d")
            end_date = (cutoff_date + timedelta(days=7)).strftime("%Y-%m-%d")
            for sym, lst in sorted(merged.items()):
                if valid_week_pairs is not None and (k, sym) not in valid_week_pairs:
                    continue
                ret = next_day_return(daily_by_sym.get(sym), cutoff,
                                      max_date=end_date)
                if ret is None:
                    if diagnostics is not None:
                        after = [r["trade_date"] for r in
                                 (daily_by_sym.get(sym) or [])
                                 if r.get("trade_date") and
                                 r["trade_date"] > cutoff.replace("-", "")]
                        reason = ("skipped_delayed" if after and
                                  min(after) > end_date.replace("-", "")
                                  else "skipped_no_return")
                        diagnostics[reason] = diagnostics.get(reason, 0) + 1
                    continue
                obs.append({
                    "sym": sym, "date": cutoff, "ret": ret,
                    "senti": sum(x["senti"] for x in lst) / len(lst),
                    "mention": sum(x["mention"] for x in lst),
                    "n_days": len(lst),
                })
        else:
            obs = [{**o, "ret": ret} for o in obs
                   if (ret := next_day_return(daily_by_sym.get(o["sym"]),
                                              o["date"])) is not None]
        if obs:
            sections.append({"key": k, "obs": obs})
    return sections


# ── 统计 ────────────────────────────────────────────────────
def _ranks(vals: list) -> list:
    """平均秩（并列取平均），spearman 基础件。"""
    n = len(vals)
    order = sorted(range(n), key=lambda i: vals[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(xs: list, ys: list) -> float | None:
    """Spearman 秩相关（并列取平均秩）；n<3 或零方差返回 None。"""
    n = len(xs)
    if n < 3:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mx = sum(rx) / n
    my = sum(ry) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def ic_series(sections: list, factor: str, min_obs: int = 3) -> list:
    """逐截面 Spearman(因子, ret)；截面 obs < min_obs 跳过。"""
    out = []
    for sec in sections:
        if len(sec["obs"]) < min_obs:
            continue
        ic = spearman([o[factor] for o in sec["obs"]],
                      [o["ret"] for o in sec["obs"]])
        if ic is not None:
            out.append({"key": sec["key"], "ic": round(ic, 4),
                        "n": len(sec["obs"])})
    return out


def ic_summary(ics: list) -> dict:
    """IC 均值 / ICIR；少于 5 个有效截面只展示探索值，不给推断指标。"""
    n = len(ics)
    if not n:
        return {"n": 0, "ic_mean": None, "icir": None,
                "evidence": "insufficient_sections", "exploratory_ic": None}
    vals = [x["ic"] for x in ics]
    m = sum(vals) / n
    if n < 5:
        return {"n": n, "ic_mean": None, "icir": None,
                "evidence": "insufficient_sections",
                "exploratory_ic": round(m, 4)}
    var = sum((v - m) ** 2 for v in vals) / (n - 1)
    sd = math.sqrt(var)
    return {"n": n, "ic_mean": round(m, 4),
            "icir": round(m / sd, 4) if sd > 0 else None,
            "evidence": "exploratory", "exploratory_ic": None}


def quintile_table(sections: list, factor: str) -> list:
    """截面内 5 分位 → 池化前向收益均值/中位数/观测数。

    少于 5 股或存在因子同分则整截面跳过，以免按股票代码人为破同分。
    返回 5 行统计。
    """
    buckets = defaultdict(list)
    for sec in sections:
        obs = sec["obs"]
        values = sorted({o[factor] for o in obs})
        if len(obs) < 5 or len(values) != len(obs):
            continue
        group = {v: i * 5 // len(obs) for i, v in enumerate(values)}
        for o in obs:
            buckets[group[o[factor]]].append(o["ret"])
    rows = []
    for q in range(5):
        rets = sorted(buckets.get(q, []))
        if not rets:
            rows.append({"quintile": q + 1, "n": 0,
                         "mean_ret": None, "median_ret": None})
            continue
        m = sum(rets) / len(rets)
        mid = len(rets) // 2
        med = rets[mid] if len(rets) % 2 else (rets[mid - 1] + rets[mid]) / 2
        rows.append({"quintile": q + 1, "n": len(rets),
                     "mean_ret": round(m, 5), "median_ret": round(med, 5)})
    return rows


def quintile_coverage(sections: list, factor: str) -> dict:
    """按原因统计五分位可用截面及被跳过截面。"""
    counts = {"eligible": 0, "skipped_small": 0, "skipped_ties": 0}
    for sec in sections:
        obs = sec["obs"]
        if len(obs) < 5:
            counts["skipped_small"] += 1
        elif len({o[factor] for o in obs}) != len(obs):
            counts["skipped_ties"] += 1
        else:
            counts["eligible"] += 1
    return counts


def monotonicity(qtab: list) -> float | None:
    """分位序 vs 收益均值的单调性（Spearman）；分位含空返回 None。"""
    valid = [r for r in qtab if r["mean_ret"] is not None]
    if len(valid) < 3:
        return None
    return spearman([r["quintile"] for r in valid],
                    [r["mean_ret"] for r in valid])


# ── 日线（router，含缓存与诚实失败）─────────────────────────
def _stock_daily_symbol(sym: object) -> bool:
    """只接收沪深股票代码；指数锚与已知归一化指数污染均拒绝。"""
    if not isinstance(sym, str) or re.fullmatch(r"(?:SH|SZ)[0-9]{6}", sym) is None:
        return False
    if sym in INDEX_POLLUTION:
        return False
    return not ((sym.startswith("SH") and sym[2:5] in ("000", "880", "899"))
                or (sym.startswith("SZ") and sym[2:5] == "399"))


def _valid_daily_price(price: object) -> bool:
    """有限且为正的数值，防止异常超大整数导致校验崩溃。"""
    if isinstance(price, bool) or not isinstance(price, (int, float)):
        return False
    try:
        return math.isfinite(price) and price > 0
    except (OverflowError, ValueError):
        return False


def _safe_daily_rows(sym: str, rows: object, origin: str,
                     errs: list) -> list:
    """验证每行身份与最低可计算字段，拒绝异常行且不缓存空结果。"""
    if not isinstance(rows, list):
        errs.append("%s %s: 日线 rows 非列表，已剔除" % (sym, origin))
        return []
    expected_ts = "%s.%s" % (sym[2:], sym[:2])
    safe_rows = []
    for row in rows:
        if not isinstance(row, dict) or row.get("symbol") != sym or (
                "ts_code" in row and row["ts_code"] != expected_ts):
            errs.append("%s %s: 返回非请求股票/ts_code 或异常行已剔除" % (sym, origin))
            continue
        date = row.get("trade_date")
        if not isinstance(date, str) or re.fullmatch(r"[0-9]{8}", date) is None:
            errs.append("%s %s: 无效交易日期行已剔除" % (sym, origin))
            continue
        try:
            datetime.strptime(date, "%Y%m%d")
        except ValueError:
            errs.append("%s %s: 无效交易日期行已剔除" % (sym, origin))
            continue
        prices = (row.get("open"), row.get("close"))
        if any(not _valid_daily_price(price) for price in prices):
            errs.append("%s %s: 无效开收盘价行已剔除" % (sym, origin))
            continue
        safe_rows.append(row)
    if not safe_rows:
        errs.append("%s %s: 无有效请求股票日线行" % (sym, origin))
    return safe_rows


def fetch_daily_cached(syms: list, days: int) -> tuple[dict, list]:
    """fetch_daily(syms, days) 带 JSON 缓存；逐码校验且失败显式可见。

    缓存落 CACHE_PATH；days 漂移失效。旧缓存和新回包使用同一行级闸门。
    """
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache: dict = {}
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, encoding="utf-8") as source:
                cache = json.load(source)
        except (ValueError, OSError):
            cache = {}
    if not isinstance(cache, dict):
        cache = {}
    meta = cache.get("_meta")
    if not isinstance(meta, dict) or meta.get("days") != days:
        cache = {"_meta": {"days": days, "saved_at": datetime.now().isoformat()}}
    errs: list = []
    requested = list(dict.fromkeys(syms))
    valid_requested = [sym for sym in requested if _stock_daily_symbol(sym)]
    for sym in requested:
        if not _stock_daily_symbol(sym):
            errs.append("%s 非沪深股票或指数锚，跳过" % sym)
    cached = cache.get("daily")
    if not isinstance(cached, dict):
        cached = {}
    daily: dict = {}
    for sym, rows in cached.items():
        if sym not in valid_requested:
            errs.append("%s cache: 非请求股票/指数锚键已剔除" % sym)
            continue
        errors_before = len(errs)
        safe_rows = _safe_daily_rows(sym, rows, "cache", errs)
        # 旧快照含坏行意味着缓存不完整，整码失效并从路由补取。
        if safe_rows and len(errs) == errors_before:
            daily[sym] = safe_rows
    need = [sym for sym in valid_requested if sym not in daily]
    if need:
        try:
            from xq_quote_router import fetch_daily
            got, note, router_errs = fetch_daily(need, days=days)
            if isinstance(router_errs, list):
                errs.extend(str(error) for error in router_errs)
            else:
                errs.append("fetch_daily: 异常错误列表")
            if not isinstance(got, dict):
                errs.append("fetch_daily: 异常日线映射")
            else:
                for sym, rows in got.items():
                    if sym not in need:
                        errs.append("%s router: 非请求股票/指数锚键已剔除" % sym)
                        continue
                    safe_rows = _safe_daily_rows(sym, rows, "router", errs)
                    if safe_rows:
                        daily[sym] = safe_rows
            print("[factor] fetch_daily note=%s errs=%d" % (note, len(errs)))
        except Exception as error:              # noqa: BLE001
            errs.append("fetch_daily crash: %s" % str(error)[:120])
    missing = [sym for sym in valid_requested if sym not in daily]
    cache["daily"] = daily
    cache["_missing"] = missing
    cache["_errs"] = errs
    try:
        with open(CACHE_PATH, "w", encoding="utf-8") as target:
            json.dump(cache, target)
    except OSError:
        pass
    return daily, errs + ["%s 全源失败(跳过)" % sym for sym in missing
                          if not any(sym in error for error in errs)]


# ── 主统计 ──────────────────────────────────────────────────
def version_stats(posts, daily_by_sym, freq, min_obs, valid_week_pairs=None):
    """单版本全套统计（主统计与附表同管道同口径）。posts 须已过版本过滤。"""
    alignment = {}
    sections = build_sections(posts, daily_by_sym, freq=freq,
                              diagnostics=alignment,
                              valid_week_pairs=valid_week_pairs)
    res = {"n_posts": len(posts), "alignment": alignment,
           "n_sections": len(sections),
           "n_obs": sum(len(s["obs"]) for s in sections),
           "n_stocks": len({o["sym"] for s in sections for o in s["obs"]})}
    for factor in ("senti", "mention"):
        ics = ic_series(sections, factor, min_obs=min_obs)
        qtab = quintile_table(sections, factor)
        res[factor] = {
            "ic": ic_summary(ics),
            "ic_detail": ics,
            "quintiles": qtab,
            "quintile_coverage": quintile_coverage(sections, factor),
            "monotonicity": monotonicity(qtab),
        }
    return res


def run_analysis(posts: list, daily_by_sym: dict,
                 freq: str = "weekly", min_obs: int = 3,
                 valid_week_pairs: dict | None = None) -> dict:
    """label_version 分层全套：主统计 v1.3，其余版本附表。"""
    versions = sorted({p["version"] for p in posts})
    out = {"freq": freq, "min_obs_per_section": min_obs,
           "forward_return": ("ISO周周日后下一周首个该股交易日 open→close；"
                              "非整周收益" if freq == "weekly" else
                              "截面截止日后一周内首个该股交易日 open→close；"
                              "非整期收益" if freq != "daily" else
                              "帖日后首个该股交易日 open→close"),
           "weekly_max_delay_days": 7 if freq == "weekly" else None,
           "period_max_delay_days": 7 if freq != "daily" else None,
           "versions": {}}
    for v in versions:
        vp = filter_version(posts, v)
        n_stock_posts = sum(1 for p in vp if p["stocks"])
        if not any(p["stocks"] for p in vp):
            out["versions"][v] = {"n_posts": len(vp), "n_stock_posts": 0,
                                  "note": "无 A 股标的帖，跳过"}
            continue
        st = version_stats(vp, daily_by_sym, freq, min_obs,
                           None if valid_week_pairs is None else
                           valid_week_pairs.get(v, set()))
        st["n_stock_posts"] = n_stock_posts
        out["versions"][v] = st
    out["main_version"] = MAIN_VERSION if MAIN_VERSION in out["versions"] \
        and "senti" in out["versions"].get(MAIN_VERSION, {}) else None
    return out


def _audit_daily_cache(daily: dict, symbols: list) -> tuple[dict, dict]:
    """验证候选股缓存日线；任一坏行隔离整只股票，避免收益静默取错行。"""
    counts = {name: 0 for name in ("invalid_daily_rows", "invalid_row",
                                   "invalid_trade_date", "invalid_open",
                                   "invalid_close", "duplicate_trade_date")}
    details: list[dict] = []
    affected: set[str] = set()
    safe_daily: dict = {}
    for sym in symbols:
        if sym not in daily:
            continue
        rows = daily[sym]
        if not isinstance(rows, list):
            counts["invalid_daily_rows"] += 1
            counts["invalid_row"] += 1
            details.append({"symbol": sym, "reason": "invalid_row"})
            affected.add(sym)
            continue
        seen_dates: set[str] = set()
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                reasons = ["invalid_row"]
            else:
                reasons = []
                date = row.get("trade_date")
                if not isinstance(date, str) or re.fullmatch(r"[0-9]{8}", date) is None:
                    reasons.append("invalid_trade_date")
                else:
                    try:
                        datetime.strptime(date, "%Y%m%d")
                    except ValueError:
                        reasons.append("invalid_trade_date")
                    if date in seen_dates:
                        counts["duplicate_trade_date"] += 1
                        details.append({"symbol": sym, "reason": "duplicate_trade_date",
                                        "row_index": index, "trade_date": date})
                        affected.add(sym)
                    seen_dates.add(date)
                for field in ("open", "close"):
                    price = row.get(field)
                    if isinstance(price, bool) or not isinstance(price, (int, float)):
                        reasons.append("invalid_" + field)
                        continue
                    try:
                        valid_price = math.isfinite(price) and price > 0
                    except (OverflowError, ValueError):
                        valid_price = False
                    if not valid_price:
                        reasons.append("invalid_" + field)
                if "invalid_open" not in reasons and "invalid_close" not in reasons:
                    try:
                        finite_return = math.isfinite(row["close"] / row["open"] - 1.0)
                    except (OverflowError, ZeroDivisionError):
                        finite_return = False
                    if not finite_return:
                        reasons.append("invalid_close")
            if reasons:
                counts["invalid_daily_rows"] += 1
                affected.add(sym)
                for reason in reasons:
                    counts[reason] += 1
                    details.append({"symbol": sym, "reason": reason,
                                    "row_index": index})
        if sym not in affected:
            safe_daily[sym] = rows
    return safe_daily, {"counts": counts, "details": details,
                        "affected_symbols": sorted(affected)}


def offline_audit(posts: list, cache: dict, db_path: str,
                  cache_path: str) -> dict:
    """只用已载入的真库帖子和本地缓存，缺口显式化，不计算有效性结论。"""
    daily = cache["daily"]
    symbols = sorted({sym for post in posts for sym in post["stocks"]})
    safe_daily, cache_quality = _audit_daily_cache(daily, symbols)
    invalid_symbols = set(cache_quality["affected_symbols"])
    gaps = {name: [] for name in ("missing_symbol", "cache_start_after_cutoff",
                                  "late_with_prior_rows", "no_next_week_row")}
    if invalid_symbols:
        gaps["invalid_cache_rows"] = []
    matched = defaultdict(int)
    valid_week_pairs = defaultdict(set)
    pairs = {(p["version"], week_key(p["date"]), sym)
             for p in posts for sym in p["stocks"]}
    for version, week, sym in sorted(pairs):
        cutoff = datetime.strptime(week, "%Y-%m-%d") + timedelta(days=6)
        cutoff_key = cutoff.strftime("%Y%m%d")
        last_key = (cutoff + timedelta(days=7)).strftime("%Y%m%d")
        rows = safe_daily.get(sym)
        record = {"version": version, "week": week, "symbol": sym}
        if sym in invalid_symbols:
            gaps["invalid_cache_rows"].append(record)
            continue
        if not rows:
            gaps["missing_symbol"].append(record)
            continue
        dated = sorted(r["trade_date"] for r in rows if r.get("trade_date"))
        if not dated or dated[0] > cutoff_key:
            record["cache_first"] = dated[0] if dated else None
            gaps["cache_start_after_cutoff"].append(record)
            continue
        future = [d for d in dated if d > cutoff_key]
        if not future:
            record["cache_last"] = dated[-1]
            gaps["no_next_week_row"].append(record)
        elif future[0] > last_key:
            record["next_cached_trade_date"] = future[0]
            gaps["late_with_prior_rows"].append(record)
        elif next_day_return(rows, cutoff.strftime("%Y-%m-%d"),
                             max_date=(cutoff + timedelta(days=7)).strftime("%Y-%m-%d")) is None:
            gaps["no_next_week_row"].append(record)
        else:
            matched[version] += 1
            valid_week_pairs[version].add((week, sym))
    result = run_analysis(posts, safe_daily, freq="weekly", min_obs=3,
                          valid_week_pairs=valid_week_pairs)
    gap_counts = {name: len(records) for name, records in gaps.items()}
    result["cache_quality"] = cache_quality
    result["audit_status"] = ("incomplete_cache" if any(gap_counts.values())
                              or invalid_symbols else "complete_cache")
    result["audit_decision"] = "INCONCLUSIVE"
    result["coverage"] = ("partial" if any(gap_counts.values()) or invalid_symbols
                          else "complete")
    result["effectiveness_conclusion"] = None
    result["eligible_for_effectiveness_conclusion"] = False
    for stats in result["versions"].values():
        for factor in ("senti", "mention"):
            if factor in stats:
                stats[factor]["ic"]["cache_subset_computable_sections"] = \
                    stats[factor]["ic"]["n"]
                stats[factor]["ic"]["diagnostic_only"] = True
                stats[factor]["ic"]["count_unit"] = \
                    "当前缓存子集可计算截面（非因子有效性）"
                stats[factor]["ic"]["ic_mean"] = None
                stats[factor]["ic"]["icir"] = None
                stats[factor]["ic"]["exploratory_ic"] = None
                stats[factor]["ic"]["evidence"] = "INCONCLUSIVE_cache_subset"
                stats[factor]["ic_detail"] = []
                stats[factor]["quintiles"] = []
                stats[factor]["monotonicity"] = None
    result["risk_notes"] = ["离线仅保留覆盖审计与漏斗，不输出因子数值或有效性结论",
                            "行情缓存左截断独立于停牌，缺原始早期日线视为不完整",
                            "2024年及以后历史 norm_tickers NULL 是独立归一化存量缺口，后续写入修复不代表已回填",
                            "created_at 时区语义未核准；行情缺行无法仅凭缓存判停牌",
                            "日线缓存以公历日期校验格式，不代表 PIT 交易日历已经核准；朴素日期对齐仍有风险",
                            "无效或重复日线按整只标的隔离，不纳入缓存匹配或收益诊断"]
    result["source"] = {"database": os.path.abspath(db_path),
                        "cache": os.path.abspath(cache_path),
                        "cache_saved_at": cache.get("_meta", {}).get("saved_at"),
                        "cache_days_hint_not_coverage": cache.get("_meta", {}).get("days")}
    result["algorithm_version"] = OFFLINE_ALGORITHM_VERSION
    result["diagnostic_labels"] = {
        "diagnostic_only": True,
        "count_unit": "当前缓存子集可计算截面（非因子有效性）",
        "ic.n": "兼容字段；INCONCLUSIVE，仅当前缓存子集可计算截面数量",
        "valid_ic_sections": "兼容字段；INCONCLUSIVE，仅当前缓存子集可计算截面数量",
        "cache_subset_computable_sections": "当前缓存可计算的截面数量，仅覆盖诊断，不代表因子有效性",
        "matched_week_symbol_pairs": "有缓存前史且次周收益可用的去重周股对，不等于完整股票池",
        "cached_candidate_symbols": "仅候选股票键出现在缓存映射中的数量，不代表行数据质量合格或前向收益可用",
        "normalization_backlog": "2024年及以后旧标注归一化NULL未回填，与行情缓存缺口独立"}
    result["generated_at"] = datetime.now().astimezone().isoformat()
    result["funnel"] = {"classified_posts": len(posts),
                        "a_stock_posts": sum(bool(p["stocks"]) for p in posts),
                        "forward_return_observations": sum(
                            v.get("n_obs", 0) for v in result["versions"].values()),
                        "cache_subset_computable_ic_sections": {
                            v: {f: stats[f]["ic"]["cache_subset_computable_sections"]
                                for f in ("senti", "mention")}
                            for v, stats in result["versions"].items()
                            if "senti" in stats},
                        "valid_ic_sections": {
                            v: {f: stats[f]["ic"]["n"] for f in ("senti", "mention")}
                            for v, stats in result["versions"].items()
                            if "senti" in stats},
                        "candidate_symbols": len(symbols),
                        "cached_candidate_symbols": len(set(symbols) & set(daily))}
    result["funnel"]["diagnostic_only"] = True
    result["funnel"]["ic_count_unit"] = "当前缓存子集可计算截面（非因子有效性）"
    result["cache_gaps"] = {"count_unit": "(version, ISO_week, symbol) 去重候选对",
                            "counts": gap_counts, "details": gaps,
                            "unique_missing_symbols": sorted({r["symbol"] for r in gaps["missing_symbol"]}),
                            "matched_week_symbol_pairs": dict(matched),
                            "candidate_week_symbol_pairs": len(pairs)}
    result["cache_symbol_ranges"] = {
        sym: {"first": min(dates), "last": max(dates)}
        for sym in symbols if (dates := [r["trade_date"] for r in safe_daily.get(sym, [])
                                      if r.get("trade_date")])}
    for v, stats in result["versions"].items():
        if "senti" in stats:
            stats["funnel"] = {"classified_posts": stats["n_posts"],
                               "a_stock_posts": stats["n_stock_posts"],
                               "forward_return_observations": stats["n_obs"],
                               "cache_subset_computable_ic_sections": {
                                   f: stats[f]["ic"]["cache_subset_computable_sections"]
                                   for f in ("senti", "mention")},
                               "valid_ic_sections": {f: stats[f]["ic"]["n"]
                                                     for f in ("senti", "mention")},
                               "diagnostic_only": True,
                               "count_unit": "当前缓存子集可计算截面（非因子有效性）",
                               "matched_week_symbol_pairs": matched[v]}
    return result


def offline_main(db_path: str = DB_PATH, cache_path: str = CACHE_PATH,
                 output_dir: str = CACHE_DIR) -> str:
    """纯离线：只读真库及已有 JSON 缓存，产出独立审计 JSON。"""
    posts = load_posts(db_path)
    with open(cache_path, "rb") as source:
        content = source.read()
    cache = json.loads(content)
    if not isinstance(cache.get("daily"), dict):
        raise ValueError("离线缓存缺少 daily 映射，拒绝推断")
    result = offline_audit(posts, cache, db_path, cache_path)
    result["source"]["cache_sha256"] = hashlib.sha256(content).hexdigest()
    result["source"]["database_mtime"] = datetime.fromtimestamp(
        os.path.getmtime(db_path)).astimezone().isoformat()
    result["source"]["cache_mtime"] = datetime.fromtimestamp(
        os.path.getmtime(cache_path)).astimezone().isoformat()
    stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%f%z")
    filename = "factor_%s_%s.json" % (OFFLINE_ALGORITHM_VERSION, stamp)
    os.makedirs(output_dir, exist_ok=True)
    output = os.path.join(output_dir, filename)
    with open(output, "x", encoding="utf-8") as target:
        json.dump(result, target, ensure_ascii=False, indent=1)
    return output


def main():
    posts = load_posts()
    syms = sorted({s for p in posts for s in p["stocks"]})
    if not syms:
        print("无 A 股标的帖，退出")
        return
    min_d = min(p["date"] for p in posts if p["stocks"])
    span_days = (datetime.now() - datetime.strptime(min_d, "%Y-%m-%d")).days
    days = int(span_days / 1.7) + 40            # router 内部 start=now-(days*1.7+30)
    print("[factor] posts=%d syms=%d earliest=%s days=%d"
          % (len(posts), len(syms), min_d, days))
    daily, errs = fetch_daily_cached(syms, days)
    print("[factor] daily ok=%d fail=%d" % (len(daily), len(errs)))
    for e in errs[:20]:
        print("  err:", e)
    result = run_analysis(posts, daily, freq="weekly", min_obs=3)
    result["coverage"] = {
        "total_classified_posts": len(posts),
        "a_stock_universe": len(syms),
        "daily_ok": len(daily),
        "daily_fail": len(errs),
        "earliest_post": min_d,
        "window": "%s ~ %s" % (min_d, max(p["date"] for p in posts
                                          if p["stocks"])),
    }
    result["daily_errs"] = errs
    out_json = os.path.join(ROOT, "data", "xueqiu", "factor_v1_result.json")
    json.dump(result, open(out_json, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("[factor] written", out_json)
    mv = result["main_version"]
    if mv:
        st = result["versions"][mv]
        print("main=%s freq=%s sections=%d obs=%d" %
              (mv, result["freq"], st["n_sections"], st["n_obs"]))
        for f in ("senti", "mention"):
            print(" %s ic=%s icir=%s mono=%s" %
                  (f, st[f]["ic"]["ic_mean"], st[f]["ic"]["icir"],
                   st[f]["monotonicity"]))
    return result


def t3_retest():
    """T3 追平复测（只读）：现库 + 现日线缓存，绝不联网重拉、不覆盖 v1 结果。

    对 weekly（追平后基线）/biweekly/monthly 三种粒度各跑同管道同口径，
    输出 data/xueqiu/factor_v1_result_t3.json（_t3 后缀，v1 结果不动）。
    """
    posts = load_posts()
    if not os.path.exists(CACHE_PATH):
        print("[t3] 缓存缺失 %s，拒绝联网重拉，退出" % CACHE_PATH)
        return None
    cache = json.load(open(CACHE_PATH, encoding="utf-8"))
    daily = cache.get("daily") or {}
    if not daily:
        print("[t3] 缓存无 daily 映射，退出")
        return None
    cache_first = min(min(r["trade_date"] for r in rows if r.get("trade_date"))
                      for rows in daily.values() if rows)
    cache_last = max(max(r["trade_date"] for r in rows if r.get("trade_date"))
                     for rows in daily.values() if rows)
    syms = sorted({s for p in posts for s in p["stocks"]})
    result = {"retest": "T3追平复测（追平样本后同管道同缓存同口径）",
              "min_obs_per_section": 3,
              "cache": {"symbols": len(daily), "first": cache_first,
                        "last": cache_last,
                        "note": "沿用 T7 v1 快照缓存，与 v1 完全同口径可比"},
              "generated_at": datetime.now().astimezone().isoformat(),
              "freqs": {}}
    for freq in ("weekly", "biweekly", "monthly"):
        r = run_analysis(posts, daily, freq=freq, min_obs=3)
        result["freqs"][freq] = r
        mv = r.get("main_version")
        st = r["versions"].get(mv, {}) if mv else {}
        if st:
            print("[t3] %s: sections=%d obs=%d senti_n=%s mention_n=%s" % (
                freq, st["n_sections"], st["n_obs"],
                st["senti"]["ic"]["n"], st["mention"]["ic"]["n"]))
    result["coverage"] = {
        "total_classified_posts": len(posts),
        "a_stock_posts_v13": sum(1 for p in posts
                                 if p["version"] == "v1.3" and p["stocks"]),
        "a_stock_universe": len(syms),
        "window": "%s ~ %s" % (min(p["date"] for p in posts if p["stocks"]),
                               max(p["date"] for p in posts if p["stocks"])),
    }
    out_json = os.path.join(ROOT, "data", "xueqiu", "factor_v1_result_t3.json")
    json.dump(result, open(out_json, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("[t3] written", out_json)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="T7 舆情因子统计")
    parser.add_argument("--offline", action="store_true", help="只读数据库与已存在的日线缓存")
    parser.add_argument("--t3-retest", action="store_true",
                        help="T3 追平复测：weekly/biweekly/monthly 三粒度，只读现库现缓存")
    parser.add_argument("--cache", default=None,
                        help="覆盖日线缓存路径（如 daily_cache_full.json 回填合并缓存；"
                             "t9 回填工具产物，绝不指向不存在的文件）")
    args = parser.parse_args()
    if args.cache:
        if not os.path.exists(args.cache):
            print("[factor] --cache 文件不存在: %s，拒绝（防止误用空缓存）" % args.cache)
            sys.exit(2)
        CACHE_PATH = args.cache
        print("[factor] 缓存路径覆盖 -> %s" % CACHE_PATH)
    if args.t3_retest:
        t3_retest()
    elif args.offline:
        print("[factor] offline audit written", offline_main())
    else:
        main()
