# -*- coding: utf-8 -*-
"""数据仓健康扫描器（只读盘点，不改任何数据文件）。

设计原则（对齐项目纪律）：
· 纯函数 scan_data_health() -> dict，一次性扫描并返回结构化健康报告；
· 目录探测用 pathlib.Path.rglob("*.parquet")，按 _SH/_SZ/_BJ 后缀分类计数；
· 单文件抽样读 1 只 parquet 验证行数和字段（pyarrow.parquet）；
· news.db 用 sqlite3 读表结构 + MIN(ts)/MAX(ts) + GROUP BY source；
· 策略产物清单硬编码已知文件名，读 os.path.getmtime；
· data_integrity 增加真实键覆盖、日历缺口、空字段与读取探针。

产物消费（全部只读）：
· K 线    quant_backtest/data/ashare/*.parquet
· 新闻    news_collector/news.db
· 策略产物 quant_backtest/data/*.json|jsonl
"""
import json
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
NEWS_DB = os.path.join(HERE, "..", "news_collector", "news.db")

# 已知策略产物清单：(相对目录, 文件名) → 中文名 + 所属工作台 URL
ARTIFACT_MANIFEST = [
    (("data",), "chanlun_pool.jsonl", "缠论预警池", "/bt-chanlun-pool"),
    (("data",), "chanlun_paper.json", "缠论模拟持仓", "/bt-chanlun-pool"),
    (("data",), "nine_dim_scores.json", "九维评分", "/bt-9dim"),
    (("data",), "nine_dim_replay.jsonl", "九维回放", "/bt-9dim"),
    (("reports",), "rps_latest.json", "RPS共振快照", "/bt-qpzm"),
    (("reports",), "premarket_latest.json", "盘前分析", "/premarket"),
]


def _scan_kline(market):
    """扫描指定市场 K 线目录。market: cn/us/hk/crypto"""
    if market == "cn":
        subdir = os.path.join(DATA, "ashare")
        pattern = "*.parquet"
        suffix_map = {"_SH": "sh", "_SZ": "sz", "_BJ": "bj"}
    else:
        subdir = os.path.join(DATA, market)
        pattern = "*.parquet"
        suffix_map = {}
    p = Path(subdir)
    if not p.exists():
        return {"count": 0, "exists": False, "path": subdir}
    files = list(p.rglob(pattern))
    result = {"count": len(files), "exists": True, "path": subdir,
              "size_mb": round(sum(f.stat().st_size for f in files) / 1024 / 1024, 1)}
    if market == "cn":
        for suffix, key in suffix_map.items():
            result[key] = sum(1 for f in files if f.name.endswith(suffix + ".parquet"))
    if files:
        latest = max(f.stat().st_mtime for f in files)
        result["latest_file"] = datetime.fromtimestamp(latest).strftime("%Y-%m-%d")
    return result


def _sample_parquet(market="cn"):
    """抽样读 1 只 parquet，验证行数和字段。"""
    if market == "cn":
        subdir = Path(DATA) / "ashare"
    else:
        subdir = Path(DATA) / market
    if not subdir.exists():
        return None
    files = list(subdir.rglob("*.parquet"))
    if not files:
        return None
    try:
        import pyarrow.parquet as pq
        df = pq.read_table(str(files[0])).to_pandas()
        return {"file": files[0].name, "rows": len(df),
                "cols": list(df.columns)}
    except Exception as e:
        return {"error": str(e)}


def _scan_news():
    """扫描 news.db：总数、来源分布、日期范围、JEV 判断数。"""
    if not os.path.exists(NEWS_DB):
        return {"exists": False, "path": NEWS_DB}
    try:
        db = sqlite3.connect(NEWS_DB)
        total = db.execute("SELECT COUNT(*) FROM news").fetchone()[0]
        date_range = db.execute("SELECT MIN(ts), MAX(ts) FROM news").fetchone()
        sources = dict(db.execute(
            "SELECT source, COUNT(*) FROM news GROUP BY source ORDER BY 2 DESC"
        ).fetchall())
        jev_judged = db.execute(
            "SELECT COUNT(*) FROM news WHERE jev IS NOT NULL AND jev != ''"
        ).fetchone()[0]
        db.close()
        return {"exists": True, "total": total,
                "date_range": "%s ~ %s" % (date_range[0], date_range[1]),
                "sources": sources, "jev_judged": jev_judged,
                "jev_pending": total - jev_judged,
                "has_announcement": sources.get("announcement", 0) > 0,
                "has_research": sources.get("survey", 0) > 0,
                "has_report": sources.get("report", 0) > 0}
    except Exception as e:
        return {"exists": True, "error": str(e)}


def _scan_artifacts():
    """扫描策略产物：文件存在性 + 最后更新时间 + 距今天数。"""
    out = []
    for subdirs, fname, cname, url in ARTIFACT_MANIFEST:
        fpath = os.path.join(HERE, *subdirs, fname)
        if not os.path.exists(fpath):
            out.append({"name": cname, "file": fname, "exists": False,
                        "url": url})
            continue
        mtime = os.path.getmtime(fpath)
        age_hours = round((time.time() - mtime) / 3600, 1)
        out.append({"name": cname, "file": fname, "exists": True,
                    "url": url,
                    "mtime": datetime.fromtimestamp(mtime).strftime(
                        "%Y-%m-%d %H:%M"),
                    "age_hours": age_hours,
                    "size_mb": round(os.path.getsize(fpath) / 1024 / 1024, 2)})
    return out


def _build_gaps(kline, news):
    """根据扫描结果生成缺口清单。"""
    gaps = []
    if not kline["us"]["exists"] or kline["us"]["count"] == 0:
        gaps.append("美股 K 线缺失：盘前 L1 恒指/纳指/标普无本地验证")
    if not kline["hk"]["exists"] or kline["hk"]["count"] == 0:
        gaps.append("港股 K 线缺失：JEV 港股判断无数据支撑")
    if not kline["crypto"]["exists"] or kline["crypto"]["count"] == 0:
        gaps.append("加密 K 线缺失：BTC/ETH 等无本地数据")
    if news.get("exists") and not news.get("has_announcement"):
        gaps.append("新闻无公告源：财报/重大事项无法自动采集")
    if news.get("exists") and not news.get("has_research"):
        gaps.append("新闻无调研源：机构调研/会议纪要缺失")
    if news.get("exists") and not news.get("has_report"):
        gaps.append("新闻无研报源：券商研报/行业分析缺失")
    if news.get("exists") and news.get("jev_pending", 0) > 100:
        gaps.append("JEV 积压 %d 条未判断" % news["jev_pending"])
    return gaps


def scan_data_health():
    """一次性扫描数据仓，返回结构化健康报告。"""
    kline = {m: _scan_kline(m) for m in ("cn", "us", "hk", "crypto")}
    sample = _sample_parquet("cn")
    if sample and "cols" in sample:
        known = {"ts_code", "trade_date", "open", "high", "low", "close",
                 "volume", "amount", "pre_close", "pct_chg"}
        missing = [c for c in ("turnover", "market_cap", "pe")
                   if c not in known and c not in sample["cols"]]
        if missing:
            kline["cn"]["missing_fields"] = missing
        kline["cn"]["avg_rows"] = sample["rows"]
    news = _scan_news()
    artifacts = _scan_artifacts()
    return {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "kline": kline, "news": news, "strategy_artifacts": artifacts,
            "gaps": _build_gaps(kline, news)}


def _scan_tdx_info_db(path):
    """tdx_info.db（TDX 同步的公告/新闻/研报）行数与最新日期。"""
    if not os.path.exists(path):
        return []
    date_col = {"stock_notice": "notice_date", "stock_news": "pub_date",
                "stock_report": "pub_date"}
    cn = {"stock_notice": "公告", "stock_news": "个股新闻",
          "stock_report": "研报"}
    out = []
    try:
        db = sqlite3.connect(path)
        for tbl, dc in date_col.items():
            try:
                n, latest = db.execute(
                    "SELECT COUNT(*), MAX(%s) FROM %s" % (dc, tbl)).fetchone()
                out.append({"asset": cn[tbl], "table": tbl, "rows": n or 0,
                            "latest": str(latest)[:10] if latest else None,
                            "path": path})
            except sqlite3.OperationalError:
                pass
        db.close()
    except Exception:
        pass
    return out


def _scan_tdx():
    """TDX 远端仓 data/tdx：parquet 分片表 + tdx_info.db + 同步状态。"""
    tdx_dir = os.path.join(DATA, "tdx")
    manifest_p = os.path.join(tdx_dir, "_manifest.json")
    m = {}
    if os.path.exists(manifest_p):
        try:
            with open(manifest_p, encoding="utf-8") as f:
                m = json.load(f)
        except Exception:
            m = {}
    tables = []
    if os.path.isdir(tdx_dir):
        for d in sorted(os.listdir(tdx_dir)):
            tdir = os.path.join(tdx_dir, d)
            if d.startswith("_") or not os.path.isdir(tdir):
                continue
            parts = [f for f in os.listdir(tdir) if f.endswith(".parquet")]
            if not parts:
                continue
            newest = max(os.path.getmtime(os.path.join(tdir, p))
                         for p in parts)
            tables.append({
                "asset": d, "rows": m.get(d, {}).get("rows", 0),
                "parts": len(parts), "done": m.get(d, {}).get("done", False),
                "size_mb": round(sum(
                    os.path.getsize(os.path.join(tdir, p)) for p in parts)
                    / 1024 / 1024, 1),
                "latest": datetime.fromtimestamp(newest).strftime(
                    "%Y-%m-%d %H:%M"),
                "age_hours": round((time.time() - newest) / 3600, 1)})
    info = _scan_tdx_info_db(os.path.join(tdx_dir, "tdx_info.db"))
    n_done = sum(1 for t in tables if t["done"])
    return {"tables": tables, "info": info,
            "sync_done": n_done, "sync_total": len(m),
            "db_size_mb": round(
                os.path.getsize(os.path.join(tdx_dir, "tdx_info.db"))
                / 1024 / 1024, 1) if info else 0}


_WCACHE = {"t": 0, "d": None}

# 新鲜度监控核心表（架构文档 §8：真源表 + 高频消费表）
_FRESH_TABLES = ["daily", "stock_basic", "trade_cal", "notice", "news",
                 "report", "stk_mins", "us_daily", "cb_daily", "bak_basic",
                 "hk_daily", "idx_mins"]


def _scan_tushare():
    """Tushare 仓 data/tushare：manifest + 文件统计（镜像 _scan_tdx 口径）。"""
    ts_dir = os.path.join(DATA, "tushare")
    manifest_p = os.path.join(ts_dir, "_manifest.json")
    m = {}
    if os.path.exists(manifest_p):
        try:
            with open(manifest_p, encoding="utf-8") as f:
                m = json.load(f)
        except Exception:
            m = {}
    tables = []
    if os.path.isdir(ts_dir):
        for d in sorted(os.listdir(ts_dir)):
            tdir = os.path.join(ts_dir, d)
            if d.startswith("_") or not os.path.isdir(tdir):
                continue
            # os.scandir 一次遍历拿 size+mtime（stk_mins/us_daily 数万文件，
            # getmtime+getsize 分开 stat 在 Windows 上 70s+ → scandir ~2s）
            sizes, newest = [], 0.0
            with os.scandir(tdir) as it:
                for e in it:
                    if not e.name.endswith(".parquet"):
                        continue
                    st = e.stat()
                    sizes.append(st.st_size)
                    if st.st_mtime > newest:
                        newest = st.st_mtime
            if not sizes:
                continue
            tables.append({
                "asset": d, "rows": m.get(d, {}).get("rows", 0),
                "parts": len(sizes), "done": m.get(d, {}).get("done", False),
                "size_mb": round(sum(sizes) / 1024 / 1024, 1),
                "latest": datetime.fromtimestamp(newest).strftime(
                    "%Y-%m-%d %H:%M"),
                "age_hours": round((time.time() - newest) / 3600, 1)})
    n_done = sum(1 for t in tables if t["done"])
    return {"tables": tables, "sync_done": n_done, "sync_total": len(m)}


_FCACHE = {"t": 0, "d": None}
_ICACHE = {"t": 0, "d": None}


def _scan_integrity():
    """物理完整度及真实查询，600s 缓存；失败必须显式返回。"""
    if _ICACHE["d"] and time.time() - _ICACHE["t"] < 600:
        return _ICACHE["d"]
    try:
        from data_integrity import scan_integrity
        out = scan_integrity(DATA)
    except Exception as exc:
        out = {"fail_count": 1, "warn_count": 0, "metrics": [
            {"name": "integrity_scan", "status": "FAIL", "value": 0,
             "details": {"error": str(exc)}}]}
    _ICACHE["t"], _ICACHE["d"] = time.time(), out
    return out


def _lag_open_days(live, today):
    """live→today 的滞后交易日数（trade_cal is_open 口径，免周末误报）。
    日历/未来日期返回 0；读层不可用返回 None（调用方退回自然日）。"""
    try:
        import datamart as dm
        df = dm.read("trade_cal", start=str(live)[:8], end=today)
        if df is None or df.empty or "is_open" not in df.columns:
            return None
        vals = df["is_open"].tolist()
        n_open = sum(1 for v in vals
                     if str(v).strip() in ("1", "1.0", "True"))
        return n_open - 1                     # 含 live 当日，扣掉
    except Exception:
        return None


def _scan_freshness():
    """catalog 驱动新鲜度监控（§8）：核心表 datamart.latest 现算 vs 快照。

    lag 用滞后交易日数（trade_cal 口径，周末不算）。
    status: OK lag<=1 / WARN lag<=3 / STALE lag>3 / SNAP 快照落后现算 /
            FAIL 读层不可用 / NA 无数据。独立 TTL 600s 缓存 + latest 并发
            （冷跑 12 表 46s→~10s）。
    """
    if _FCACHE["d"] and time.time() - _FCACHE["t"] < 600:
        return _FCACHE["d"]
    try:
        import datamart as dm
    except Exception as e:
        return {"error": str(e)[:80], "tables": []}
    cat = dm._catalog()
    today = datetime.now().strftime("%Y%m%d")

    def _one(alias):
        phys = cat.get("aliases", {}).get(alias, {}).get("resolves") \
            or [alias]
        live = dm.latest(alias)
        snap = None
        for t in phys:
            e = cat.get("tables", {}).get(t) or {}
            if e.get("freshness"):
                snap = e["freshness"]
                break
        ready = dm.ready(alias)
        if not live:
            return {"table": alias, "live": None, "snap": snap,
                    "lag_days": None, "ready": ready,
                    "status": "FAIL" if ready else "NA"}
        if alias == "trade_cal":
            lag = 0 if str(live)[:8] >= today else \
                (datetime.strptime(today, "%Y%m%d")
                 - datetime.strptime(str(live)[:8], "%Y%m%d")).days
        else:
            lag = _lag_open_days(live, today)
            if lag is None:
                lag = (datetime.strptime(today, "%Y%m%d")
                       - datetime.strptime(str(live)[:8], "%Y%m%d")).days
        if lag > 3:
            status = "STALE"
        elif lag > 1:
            status = "WARN"
        elif snap and str(live)[:8] > str(snap)[:8]:
            status = "SNAP"
        else:
            status = "OK"
        return {"table": alias, "live": live, "snap": snap,
                "lag_days": lag, "ready": ready, "status": status}

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=6) as ex:
        rows = list(ex.map(_one, _FRESH_TABLES))
    out = {"tables": rows}
    _FCACHE["t"], _FCACHE["d"] = time.time(), out
    return out


def scan_warehouse():
    """全资产盘点（60s 缓存）：TDX 远端仓 + 本地 K 线 + 新闻 + 策略产物。

    返回 {generated_at, kpi, groups:[{name, kind, rows:[...]}], gaps}。
    rows[] 统一字段：asset/rows/latest/size_mb/age_hours/extra(可选)。
    """
    if _WCACHE["d"] and time.time() - _WCACHE["t"] < 60:
        return _WCACHE["d"]
    kline = {mk: _scan_kline(mk) for mk in ("cn", "us", "hk", "crypto")}
    news = _scan_news()
    tdx = _scan_tdx()
    tush = _scan_tushare()
    fresh = _scan_freshness()
    integrity = _scan_integrity()
    artifacts = _scan_artifacts()

    groups = []
    # 1. TDX 远端仓（含同步进度）
    tdx_rows = [{"asset": t["asset"],
                 "rows": "%s (×%d片)" % (_fmt_rows(t["rows"]), t["parts"]),
                 "latest": t["latest"], "size_mb": t["size_mb"],
                 "age_hours": t["age_hours"],
                 "extra": "同步完成" if t["done"] else "同步中"}
                for t in tdx["tables"]]
    tdx_rows += [{"asset": i["asset"] + "（info.db）",
                  "rows": _fmt_rows(i["rows"]), "latest": i["latest"],
                  "size_mb": tdx["db_size_mb"], "age_hours": None,
                  "extra": None} for i in tdx["info"]]
    groups.append({"name": "TDX 远端仓", "kind": "tdx", "rows": tdx_rows,
                   "note": "同步 %d/%d 表 · tdx_info.db %.1f MB"
                           % (tdx["sync_done"], tdx["sync_total"],
                              tdx["db_size_mb"])})
    # 1b. Tushare 仓（镜像 TDX 口径）
    tush_rows = [{"asset": t["asset"],
                  "rows": "%s (×%d文件)" % (_fmt_rows(t["rows"]), t["parts"]),
                  "latest": t["latest"], "size_mb": t["size_mb"],
                  "age_hours": t["age_hours"],
                  "extra": "同步完成" if t["done"] else "同步中"}
                 for t in tush["tables"]]
    groups.append({"name": "Tushare 仓", "kind": "tushare", "rows": tush_rows,
                   "note": "同步 %d/%d 表" % (tush["sync_done"],
                                              tush["sync_total"])})
    # 1c. 新鲜度监控（catalog 驱动，§8）
    st_cn = {"OK": "✓", "WARN": "⚠", "STALE": "✗", "SNAP": "📸",
             "FAIL": "✗", "NA": "—"}
    f_rows = [{"asset": t["table"],
               "rows": ("lag %d天" % t["lag_days"])
                       if t["lag_days"] is not None else "—",
               "latest": t["live"] or "—",
               "size_mb": None, "age_hours": None,
               "extra": "%s 快照%s%s" % (
                   st_cn.get(t["status"], t["status"]), t["snap"] or "—",
                   "" if t["ready"] else " · 未就绪")}
              for t in fresh.get("tables", [])]
    n_bad = sum(1 for t in fresh.get("tables", [])
                if t["status"] in ("STALE", "FAIL"))
    groups.append({"name": "新鲜度监控", "kind": "freshness", "rows": f_rows,
                   "note": ("异常 %d 表（STALE/FAIL）" % n_bad) if n_bad
                   else "核心表全部 OK"})
    integrity_rows = []
    for metric in integrity["metrics"]:
        details = metric["details"]
        summary = json.dumps(details.get("_summary", details), ensure_ascii=False, separators=(",", ":"))
        integrity_rows.append({"asset": metric["name"], "rows": str(metric["value"]),
                               "latest": integrity.get("expected_latest"), "size_mb": None,
                               "age_hours": None, "extra": metric["status"] + " " + summary[:180]})
    groups.append({"name": "数据完整度", "kind": "integrity", "rows": integrity_rows,
                   "note": "异常 %d 项 · 待核实 %d 项；完整键差与查询结果见 integrity.metrics"
                           % (integrity["fail_count"], integrity["warn_count"])})
    # 2. 本地 K 线仓
    mk_cn = {"cn": "A股", "us": "美股", "hk": "港股", "crypto": "加密"}
    k_rows = []
    for mk, r in kline.items():
        if not r.get("exists"):
            continue
        extra = None
        if mk == "cn" and r.get("sh") is not None:
            extra = "SH %d · SZ %d · BJ %d" % (r.get("sh", 0),
                                               r.get("sz", 0), r.get("bj", 0))
        k_rows.append({"asset": mk_cn[mk] + "（%d 只）" % r["count"],
                       "rows": "日线 parquet", "latest": r.get("latest_file"),
                       "size_mb": r.get("size_mb", 0), "age_hours": None,
                       "extra": extra})
    groups.append({"name": "本地 K 线仓", "kind": "kline", "rows": k_rows,
                   "note": None})
    # 3. 新闻资讯（news.db）
    if news.get("exists"):
        groups.append({
            "name": "新闻资讯", "kind": "news",
            "rows": [{"asset": "快讯（%s）" % "、".join(
                          news.get("sources", {}).keys() or ["—"]),
                      "rows": _fmt_rows(news.get("total", 0)),
                      "latest": (news.get("date_range") or "—").split(" ~ ")[-1],
                      "size_mb": None, "age_hours": None,
                      "extra": "JEV 已判 %d · 积压 %d"
                               % (news.get("jev_judged", 0),
                                  news.get("jev_pending", 0))}],
            "note": None})
    # 4. 策略产物
    groups.append({
        "name": "策略产物", "kind": "artifact",
        "rows": [{"asset": a["name"] + ("" if a.get("exists") else "（缺失）"),
                  "rows": _fmt_mb(a.get("size_mb")),
                  "latest": a.get("mtime"), "size_mb": a.get("size_mb"),
                  "age_hours": a.get("age_hours"), "extra": None,
                  "url": a.get("url")} for a in artifacts],
        "note": None})

    # KPI
    n_tables = (len(tdx["tables"]) + len(tdx["info"]) + len(tush["tables"])
                + 4 + 1 + len(artifacts))
    total_mb = (sum(t["size_mb"] for t in tdx["tables"]) + tdx["db_size_mb"]
                + sum(t["size_mb"] for t in tush["tables"])
                + sum(r.get("size_mb", 0) for r in kline.values())
                + sum(a.get("size_mb", 0) or 0 for a in artifacts))
    ages = [g2["age_hours"] for g2 in tdx_rows if g2.get("age_hours")]
    freshest = ("%.1fh 前" % min(ages)) if ages else "—"
    kpi = {"n_tables": n_tables, "total_mb": round(total_mb, 1),
           "tdx_sync": "%d/%d" % (tdx["sync_done"], tdx["sync_total"]),
           "tushare_sync": "%d/%d" % (tush["sync_done"], tush["sync_total"]),
           "fresh_bad": n_bad, "freshest": freshest,
           "integrity_bad": integrity["fail_count"], "integrity_warn": integrity["warn_count"]}
    out = {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "kpi": kpi, "groups": groups,
           "integrity": integrity,
           "gaps": _build_gaps(kline, news) + [
               "%s %s: %s" % (m["status"], m["name"], m["value"])
               for m in integrity["metrics"] if m["status"] in ("FAIL", "WARN")]}
    _WCACHE["t"], _WCACHE["d"] = time.time(), out
    return out


def _fmt_rows(n):
    """行数千分位 / 万位缩写。"""
    if not n:
        return "0"
    return ("%.1f 万" % (n / 10000.0)) if n >= 10000 else str(n)


def _fmt_mb(mb):
    return ("%.1f MB" % mb) if mb else "—"


if __name__ == "__main__":
    d = scan_data_health()
    print(json.dumps(d, ensure_ascii=False, indent=2))
