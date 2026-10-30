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
import hashlib
import json
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from valuation_field_reconcile import join_daily_basic_from_dir, valuation_field_coverage
import logging

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
NEWS_DB = os.path.join(HERE, "..", "news_collector", "news.db")
DAILY_BASIC_DIRNAME = os.path.join("tushare", "daily_basic")   # 2026-10-17 切生产受控仓（tdx 湖 ts_daily_basic 停更 20261014 致估值 81 行未匹配）

# 已知策略产物清单：(相对目录, 文件名) → 中文名 + 所属工作台 URL
ARTIFACT_MANIFEST = [
    (("data",), "chanlun_pool.jsonl", "缠论预警池", "/bt-chanlun-pool"),
    (("data",), "chanlun_paper.json", "缠论模拟持仓", "/bt-chanlun-pool"),
    (("data",), "nine_dim_scores.json", "九维评分", "/bt-9dim"),
    (("data",), "nine_dim_replay.jsonl", "九维回放", "/bt-9dim"),
    (("reports",), "rps_latest.json", "RPS共振快照", "/bt-qpzm"),
    (("reports",), "premarket_latest.json", "盘前分析", "/premarket"),
]

# 日更策略产物超过一个工作日仍未刷新时，健康页必须显式提示。
# 取 26 小时以覆盖周末前的收盘任务和时区边界，避免只看文件存在性。
ARTIFACT_STALE_HOURS = 26


def scan_recent_refresh_receipts(receipt_dir):
    """读取最近刷新回执，供看板显示状态和体量（只读）。

    回执缺失或 JSONL 中存在损坏行时返回 ``blocked``，不把部分结果
    误报为已更新。生产开关始终保持关闭，发布由后续受控流程完成。
    """
    root = Path(receipt_dir)
    path = root / "source_receipt.jsonl"
    if not path.exists():
        return {"status": "blocked", "reason": "receipt_missing", "qualified": 0,
                "blocked": 0, "rejected": 0, "rows": 0,
                "production_write": False, "global_activation": False}
    counts = {"qualified": 0, "blocked": 0, "rejected": 0}
    rows = 0
    errors = []
    try:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except (TypeError, ValueError) as exc:
                errors.append(f"line_{number}:{type(exc).__name__}")
                continue
            status = str(item.get("status", "rejected"))
            if status not in counts:
                status = "rejected"
            counts[status] += 1
            rows += int(item.get("result_rows") or 0)
    except OSError as exc:
        errors.append(f"read:{type(exc).__name__}")
    overall = "blocked" if errors or counts["blocked"] or counts["rejected"] else "updated"
    return {"status": overall, "reason": "receipt_parse_error" if errors else None,
            **counts, "rows": rows, "errors": errors,
            "production_write": False, "global_activation": False}


def build_full_refresh_health(*, daily_stock, daily_sector, critical_nulls,
                              cross_table_keys, query_results, source_freshness=None):
    """构建全量刷新健康指标，按所有日期和实际查询结果对账。"""
    def coverage(values):
        rows = dict(values or {})
        missing = {day: max(0, int(item.get("expected", 0)) - int(item.get("actual", 0)))
                   for day, item in rows.items() if int(item.get("actual", 0)) < int(item.get("expected", 0))}
        return {"status": "FAIL" if missing else "OK", "value": sum(missing.values()),
                "days": len(rows), "missing_days": missing}

    nulls = {str(key): int(value) for key, value in (critical_nulls or {}).items()}
    key_rows = {str(key): value for key, value in (cross_table_keys or {}).items()}
    key_bad = {key: value for key, value in key_rows.items()
               if int(value.get("missing", 0)) or int(value.get("duplicate", 0))}
    query_bad = {}
    for name, value in (query_results or {}).items():
        rows = int(value.get("rows", 0) or 0)
        if value.get("error") or rows == 0:
            query_bad[str(name)] = {"rows": rows, "error": value.get("error")}
    freshness = source_freshness or {}
    return {
        "daily_unique_stock_coverage": coverage(daily_stock),
        "daily_unique_sector_coverage": coverage(daily_sector),
        "critical_nulls": {"status": "FAIL" if sum(nulls.values()) else "OK",
                            "value": sum(nulls.values()), "columns": nulls},
        "cross_table_key_reconciliation": {"status": "FAIL" if key_bad else "OK",
                                             "value": sum(int(v.get("missing", 0)) + int(v.get("duplicate", 0)) for v in key_bad.values()),
                                             "details": key_rows},
        "query_result_checks": {"status": "FAIL" if query_bad else "OK",
                                 "value": len(query_bad), "details": query_bad},
        "source_freshness": {"status": "FAIL" if any(value in {"blocked", "stale", "error"}
                                                        for value in freshness.values()) else "OK",
                              "details": freshness},
        "production_write": False,
        "global_activation": False,
    }


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
    files = sorted(subdir.rglob("*.parquet"))
    # 000001.SH 通常是上证指数，优先抽取一只股票以便与 daily_basic 对账。
    if market == "cn":
        stock_files = [f for f in files if f.name != "000001_SH.parquet"]
        files = stock_files or files
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
    measured = {'path': NEWS_DB, 'size_mb': round(os.path.getsize(NEWS_DB) / 1024**2, 3),
                'wal_size_bytes': os.path.getsize(NEWS_DB + '-wal') if os.path.exists(NEWS_DB + '-wal') else 0,
                'measured_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
    try:
        db = sqlite3.connect('file:' + NEWS_DB.replace('\\', '/') + '?mode=ro', uri=True)
        total = db.execute("SELECT COUNT(*) FROM news").fetchone()[0]
        date_range = db.execute("SELECT MIN(ts), MAX(ts) FROM news").fetchone()
        sources = dict(db.execute(
            "SELECT source, COUNT(*) FROM news GROUP BY source ORDER BY 2 DESC"
        ).fetchall())
        jev_judged = db.execute(
            "SELECT COUNT(*) FROM news WHERE jev IS NOT NULL AND jev != ''"
        ).fetchone()[0]
        source_times = {}
        # created_at 仅用于附加维度；其 schema 探针失败不能覆盖前面已成功
        # 读取的核心统计（旧库、只读锁或测试替身都可能不支持 PRAGMA）。
        try:
            columns = [r[1] for r in db.execute('PRAGMA table_info(news)')]
            if 'created_at' in columns:
                source_times = {s: {'latest_event': event, 'latest_ingested': ingested}
                                for s, event, ingested in db.execute(
                                    'SELECT source, MAX(ts), MAX(created_at) FROM news GROUP BY source')}
        except Exception:
            source_times = {}
        db.close()
        return {**measured, "exists": True, "total": total, 'source_times': source_times,
                "date_range": "%s ~ %s" % (date_range[0], date_range[1]),
                "sources": sources, "jev_judged": jev_judged,
                "jev_pending": total - jev_judged,
                "has_announcement": sources.get("announcement", 0) > 0,
                "has_research": sources.get("survey", 0) > 0,
                "has_report": sources.get("report", 0) > 0}
    except Exception as e:
        return {**measured, "exists": True, "error": str(e)}


def _scan_jev_reconciliation(news_db_path=None, queue_paths=None, receipt_paths=None):
    """只读对账新闻库 pending、JEV staging 队列和判断回执。"""
    db_path = Path(news_db_path or NEWS_DB)
    if not db_path.exists():
        return {"status": "UNKNOWN", "reason": "news_db_missing", "available": False}
    try:
        db = sqlite3.connect("file:" + str(db_path).replace("\\", "/") + "?mode=ro", uri=True)
        pending_rows = db.execute("SELECT id FROM news WHERE jev IS NULL OR jev='' ").fetchall()
        all_ids = {str(row[0]) for row in db.execute("SELECT id FROM news").fetchall()}
        db.close()
    except Exception as exc:
        return {"status": "FAIL", "reason": "news_db_read_error", "error": str(exc), "available": True}
    pending_ids = {str(row[0]) for row in pending_rows}
    root = Path(HERE).parent
    candidates = [Path(p) for p in (queue_paths or [])]
    if not candidates:
        candidates = sorted((root / "work").glob("jev_queue_staging_*.json"))
    candidates = [p for p in candidates if p.exists()]
    queue_path = max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None
    queue_items = []
    if queue_path:
        try:
            payload = json.loads(queue_path.read_text(encoding="utf-8"))
            if not isinstance(payload, list):
                raise ValueError("队列不是数组")
            queue_items = [dict(item) for item in payload if isinstance(item, dict) and item.get("id") is not None]
        except Exception as exc:
            return {"status": "FAIL", "reason": "queue_read_error", "error": str(exc),
                    "available": True, "news_pending": len(pending_ids), "queue_path": str(queue_path)}
    queue_ids = {str(item["id"]) for item in queue_items}
    queue_pending = {str(item["id"]) for item in queue_items if str(item.get("status", "pending")) == "pending"}
    queue_decided = {str(item["id"]) for item in queue_items if str(item.get("status", "")) == "decided"}
    queue_blocked = {str(item["id"]) for item in queue_items if str(item.get("status", "")) == "blocked"}
    receipt_candidates = [Path(p) for p in (receipt_paths or [])]
    if not receipt_candidates:
        receipt_candidates = sorted((root / "work").glob("jev_receipt_staging_*.jsonl"))
    receipt_candidates = [p for p in receipt_candidates if p.exists()]
    receipt_path = max(receipt_candidates, key=lambda p: p.stat().st_mtime) if receipt_candidates else None
    receipt_ids, receipt_errors = set(), []
    if receipt_path:
        for number, line in enumerate(receipt_path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
                if item.get("id") is not None:
                    receipt_ids.add(str(item["id"]))
            except (TypeError, ValueError):
                receipt_errors.append(number)
    pending_unqueued = sorted(pending_ids - queue_ids)
    queue_orphans = sorted(queue_ids - all_ids)
    receipt_orphans = sorted(receipt_ids - queue_ids)
    decided_without_receipt = sorted(queue_decided - receipt_ids)
    status = "FAIL" if (pending_unqueued or queue_blocked or receipt_errors or
                         receipt_orphans or decided_without_receipt) else "OK"
    return {
        "status": status, "available": True, "news_pending": len(pending_ids),
        "queue_total": len(queue_ids), "queue_pending": len(queue_pending),
        "queue_decided": len(queue_decided), "queue_blocked": len(queue_blocked),
        "receipt_rows": len(receipt_ids), "pending_unqueued": pending_unqueued[:100],
        "pending_unqueued_count": len(pending_unqueued),
        "queue_orphans": queue_orphans[:100], "queue_orphans_count": len(queue_orphans),
        "receipt_orphans": receipt_orphans[:100], "receipt_orphans_count": len(receipt_orphans),
        "decided_without_receipt": decided_without_receipt[:100],
        "decided_without_receipt_count": len(decided_without_receipt),
        "receipt_errors": receipt_errors, "queue_path": str(queue_path) if queue_path else None,
        "receipt_path": str(receipt_path) if receipt_path else None,
        "production_write": False, "global_activation": False,
    }


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


def _build_gaps(kline, news, artifacts=None, jev_reconciliation=None):
    """根据扫描结果生成缺口清单。"""
    gaps = []
    cn = kline.get("cn") or {}
    missing_fields = sorted(set(cn.get("missing_fields") or []))
    if missing_fields:
        gaps.append("A股关键字段缺失：%s" % ", ".join(missing_fields))
    valuation = cn.get("valuation_fields") or {}
    valuation_missing = {field: int(item.get("missing", 0)) for field, item in valuation.items()
                         if isinstance(item, dict) and int(item.get("missing", 0) or 0)}
    if valuation_missing:
        source = cn.get("valuation_source") or {}
        qualifier = ("来源未匹配/待业务分类" if source.get("unmatched_rows") else "待业务分类")
        gaps.append("A股估值字段存在%s：%s" % (
            qualifier, ", ".join("%s=%d" % (field, count)
                                  for field, count in sorted(valuation_missing.items()))))
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
    if isinstance(jev_reconciliation, dict) and jev_reconciliation.get("status") == "FAIL":
        gaps.append("JEV staging/新闻库对账失败：未入队 %d，回执孤儿 %d，已决策无回执 %d" % (
            int(jev_reconciliation.get("pending_unqueued_count", 0)),
            int(jev_reconciliation.get("receipt_orphans_count", 0)),
            int(jev_reconciliation.get("decided_without_receipt_count", 0))))
    for artifact in artifacts or []:
        if not artifact.get("exists"):
            continue
        age = artifact.get("age_hours")
        if isinstance(age, (int, float)) and age > ARTIFACT_STALE_HOURS:
            gaps.append("策略产物过期：%s（%.1f 小时未刷新）" %
                        (artifact.get("name") or artifact.get("file") or "未知产物", age))
    return gaps


def _annotate_cn_sample(kline):
    """把 A 股样本字段缺失写回扫描结果，供两个健康入口共用。"""
    sample = _sample_parquet("cn")
    if not sample or "cols" not in sample:
        return
    known = {"ts_code", "trade_date", "open", "high", "low", "close",
             "volume", "amount", "pre_close", "pct_chg"}
    missing = [c for c in ("turnover", "market_cap", "pe")
               if c not in known and c not in sample["cols"]]
    cn = kline.setdefault("cn", {})
    source_dir = os.path.join(DATA, DAILY_BASIC_DIRNAME)
    if os.path.isdir(source_dir):
        try:
            import pyarrow.parquet as pq
            sample_path = Path(DATA) / "ashare" / str(sample["file"])
            frame = pq.read_table(str(sample_path)).to_pandas()
            view = join_daily_basic_from_dir(frame, source_dir)
            coverage = valuation_field_coverage(view)
            cn["valuation_fields"] = coverage["fields"]
            cn["valuation_source"] = {
                "name": "tushare_daily_basic_prod",
                "path": source_dir,
                "status": "joined",
                "rows": int(len(view)),
                "matched_rows": coverage.get("source_matched_rows"),
                "unmatched_rows": coverage.get("source_unmatched_rows"),
            }
            # 字段来自事实源即可消除结构性缺失告警；合法空值由 coverage 单独展示。
            cn["missing_fields"] = []
        except Exception as exc:
            cn["valuation_source"] = {
                "name": "tushare_daily_basic_prod", "path": source_dir,
                "status": "error", "error": str(exc),
            }
    elif missing:
        cn["missing_fields"] = missing
    kline.setdefault("cn", {})["avg_rows"] = sample.get("rows")


def scan_data_health():
    """一次性扫描数据仓，返回结构化健康报告。"""
    kline = {m: _scan_kline(m) for m in ("cn", "us", "hk", "crypto")}
    _annotate_cn_sample(kline)
    news = _scan_news()
    jev_reconciliation = _scan_jev_reconciliation()
    artifacts = _scan_artifacts()
    return {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "kline": kline, "news": news, "strategy_artifacts": artifacts,
            "jev_reconciliation": jev_reconciliation,
            "gaps": _build_gaps(kline, news, artifacts, jev_reconciliation)}


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
        db = sqlite3.connect('file:' + path.replace('\\', '/') + '?mode=ro', uri=True)
        for tbl, dc in date_col.items():
            try:
                n, latest = db.execute(
                    "SELECT COUNT(*), MAX(%s) FROM %s" % (dc, tbl)).fetchone()  # nosec B608 - 值参数化/内部标识符（已抽查）
                out.append({"asset": cn[tbl], "table": tbl, "rows": n or 0,
                            "latest": str(latest)[:10] if latest else None,
                            "path": path, 'latest_kind': 'business_date', 'size_mb': None})
            except sqlite3.OperationalError as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/data_health.py", exc)
        db.close()
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/data_health.py", exc)
    if out:
        # 共享数据库按物理路径计一次。
        out[0]['size_mb'] = round(os.path.getsize(path) / 1024**2, 3)
    return out


def _manifest_metadata(manifest, name, files_present=True):
    entry = manifest.get(name) or {}
    return {'manifest_status': 'not_applicable' if name == 'index_global' else
            'closed' if entry.get('done') else 'open' if name in manifest else 'missing',
            'readiness_basis': 'manifest' if entry else 'file_presence' if files_present else 'unknown',
            'verification_status': 'unknown', 'latest_kind': 'file_mtime'}


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
                "asset": d, "rows": m.get(d, {}).get("rows"),
                **_manifest_metadata(m, d),
                "parts": len(parts), "done": m.get(d, {}).get("done", False),
                "size_mb": round(sum(
                    os.path.getsize(os.path.join(tdir, p)) for p in parts)
                    / 1024 / 1024, 1),
                "latest": datetime.fromtimestamp(newest).strftime(
                    "%Y-%m-%d %H:%M"),
                "age_hours": round((time.time() - newest) / 3600, 1)})
    info = _scan_tdx_info_db(os.path.join(tdx_dir, "tdx_info.db"))
    sqlite_names = {"stock_notice", "stock_news", "stock_report"}
    parquet_names = sorted(name for name in m if name not in sqlite_names)
    table_by_name = {t["asset"]: t for t in tables}
    parquet_done = sum(1 for name in parquet_names
                       if table_by_name.get(name, {}).get("done"))
    incomplete = [name for name in parquet_names
                  if not table_by_name.get(name, {}).get("done")]
    n_done = parquet_done
    return {"tables": tables, "info": info,
            "sync_done": n_done, "sync_total": len(m),
            "sync_summary": {
                "parquet_done": parquet_done,
                "parquet_total": len(parquet_names),
                "sqlite_done": len(info),
                "sqlite_total": len(sqlite_names),
                "incomplete": incomplete,
            },
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
                "asset": d, "rows": m.get(d, {}).get("rows"),
                **_manifest_metadata(m, d),
                "parts": len(sizes), "done": m.get(d, {}).get("done", False),
                "size_mb": round(sum(sizes) / 1024 / 1024, 1),
                "latest": datetime.fromtimestamp(newest).strftime(
                    "%Y-%m-%d %H:%M"),
                "age_hours": round((time.time() - newest) / 3600, 1)})
    table_by_name = {t["asset"]: t for t in tables}
    probe_names = {name for name in m if name.endswith("_probe")}
    entity_names = sorted(name for name in m if name not in probe_names)
    n_done = sum(1 for name in entity_names
                 if table_by_name.get(name, {}).get("done")
                 and m.get(name, {}).get("codes") != 0)
    manifest_only = []
    for name, entry in m.items():
        if os.path.isdir(os.path.join(ts_dir, name)):
            continue
        coverage = ('empty_probe' if name.endswith('_probe') and entry.get('rows') == 0 else
                    'no_coverage' if entry.get('codes') == 0 else 'location_unverified')
        manifest_only.append({'asset': name, 'rows': entry.get('rows'),
                              'coverage_status': coverage, **_manifest_metadata(m, name, False)})
    no_coverage = sorted(name for name in entity_names
                         if m.get(name, {}).get("codes") == 0)
    incomplete = sorted(name for name in entity_names
                        if name in no_coverage or
                        not table_by_name.get(name, {}).get("done"))
    legacy_dirs = sorted(
        d for d in os.listdir(ts_dir)
        if os.path.isdir(os.path.join(ts_dir, d))
        and not d.startswith("_") and d not in m)
    return {"tables": tables, "sync_done": n_done, "sync_total": len(m),
            'manifest_only': manifest_only,
            'legacy_dirs': legacy_dirs,
            'sync_summary': {
                "entity_done": n_done,
                "entity_total": len(entity_names),
                "probe_count": len(probe_names),
                "no_coverage": no_coverage,
                "incomplete": incomplete,
            }}


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
    """(live, today] 内 SSE 开市自然日数，免周末及多交易所重复误报。
    日历/未来日期返回 0；读层不可用返回 None（调用方退回自然日）。"""
    try:
        live, today = str(live)[:8], str(today)[:8]
        datetime.strptime(live, "%Y%m%d")
        datetime.strptime(today, "%Y%m%d")
        if live >= today:
            return 0
        import datamart as dm
        df = dm.read("trade_cal", start=live, end=today)
        if df is None or df.empty or not {
                "exchange", "cal_date", "is_open"}.issubset(df.columns):
            return None
        sse = df[df["exchange"].astype(str).str.strip() == "SSE"]
        if sse.empty:
            return None
        # 排除 live 当日，而非假定该日必定开市再减一。
        days = {str(day)[:8] for day, opened in
                sse[["cal_date", "is_open"]].itertuples(index=False, name=None)
                if live < str(day)[:8] <= today
                and str(opened).strip() in ("1", "1.0", "True")}
        return len(days)
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


def _scan_business_monitoring():
    """只读取评分与图谱回执；数量快照不能证明实时 QA 通过。"""
    def snapshot(path):
        try:
            with open(path, encoding='utf-8') as f:
                value = json.load(f)
            if not isinstance(value, dict):
                raise ValueError('快照不是对象')
            return value
        except Exception as exc:
            return {'_read_error': type(exc).__name__}

    score = snapshot(os.path.join(DATA, 'nine_dim_scores.json'))
    rows = score.get('rows') or []
    threshold = score.get('min_cov', 0.75)
    full = sum(r.get('full') is True and isinstance(r.get('cov'), (int, float))
               and r['cov'] >= threshold for r in rows)
    factors = {}
    missing_items = 0
    for row in rows:
        for factor in row.get('detail') or []:
            item = factors.setdefault(factor['id'], {'name': factor.get('name', factor['id']),
                                      'unjudgeable': 0, 'incomplete': 0, 'observed': 0})
            item['observed'] += 1
            item['unjudgeable'] += factor.get('cov') in (None, 0)
            item['incomplete'] += (factor.get('cov') or 0) < 1
            missing_items += sum(x.get('state') is None for x in factor.get('items') or [])
    coverage = [r['cov'] for r in rows if isinstance(r.get('cov'), (int, float))]
    nine = {'status': 'UNKNOWN' if not rows else 'FAIL' if full < len(rows) else 'OK',
            'generated_at': score.get('generated_at'), 'trade_date': score.get('trade_date'),
            'n_candidates': len(rows), 'n_full': full, 'min_cov': threshold, 'factors': factors,
            'coverage_min': min(coverage) if coverage else None,
            'coverage_max': max(coverage) if coverage else None,
            'cov_histogram': {str(c): coverage.count(c) for c in sorted(set(coverage))},
            'failure_class_distribution': {'unknown': missing_items},
            'source_failure_class_distribution': {}, 'source_audit_status': 'unavailable',
            'error': score.get('_read_error'),
            'source_status': 'unknown', 'source_note': '评分缺项不区分权限、限流、源空或解析失败；来源资格待核验'}
    audit = snapshot(os.path.join(DATA, 'nine_dim_source_audit.json'))
    if not audit.get('_read_error'):
        expected_hash = (audit.get('versions') or {}).get('nine_dim_scores.json', {}).get('sha256')
        try:
            with open(os.path.join(DATA, 'nine_dim_scores.json'), 'rb') as f:
                current_hash = hashlib.sha256(f.read()).hexdigest()
        except OSError:
            current_hash = None
        nine['source_audit_status'] = 'matched' if current_hash and current_hash == expected_hash else 'version_mismatch'
        nine['source_audit_at'] = audit.get('audited_at')
        if nine['source_audit_status'] == 'matched':
            qualification = audit.get('monitoring') or {}
            for key in ('failure_class_distribution', 'source_failure_class_distribution', 'cache_score_relationship'):
                if key in qualification:
                    nine[key] = qualification[key]
            nine['source_note'] = '历史来源资格回执与当前评分版本匹配；按回执时点解释，不代表当前接口状态'
            nine['source_audit_versions'] = audit.get('versions') or {}
    cache_meta = {}
    for name in ('nine_dim_fina_cache.json', 'nine_dim_idx_cache.json', 'nine_dim_rc_cache.json'):
        path = os.path.join(DATA, name)
        cached = snapshot(path)
        cache_meta[name] = {'file_modified_at': datetime.fromtimestamp(os.path.getmtime(path)).isoformat() if os.path.exists(path) else None,
                            'trade_date': cached.get('trade_date'), 'date': cached.get('date'),
                            'error': cached.get('_read_error')}
    nine['cache_metadata'] = cache_meta
    qa = snapshot(os.path.join(DATA, 'graph_check', 'latest.json'))
    qa_status = 'OK' if qa.get('ok') is True else 'FAIL' if qa.get('ok') is False else 'UNKNOWN'
    count_files = sorted(Path(HERE, 'reports').glob('graph_schema_*.json'))
    counts = snapshot(str(count_files[-1])) if count_files else {}
    return {'nine_dim': nine,
            'graph_qa': {'status': qa_status, 'generated_at': qa.get('ts') or qa.get('generated_at'),
                         'live_verified': False, 'details': qa,
                         'note': '历史QA回执，实时连接未核验'},
            'graph_counts': {'status': 'INFO' if counts and not counts.get('_read_error') else 'UNKNOWN',
                             'generated_at': counts.get('ts') or counts.get('generated_at'),
                             'details': counts, 'note': '数量快照不代表QA通过'}}


def _manifest_label(table):
    status = table.get('manifest_status') or ('closed' if table.get('done') else 'open')
    return {'missing': '未登记', 'open': 'manifest未收口',
            'closed': 'manifest已收口', 'not_applicable': 'manifest不适用'}.get(status, '登记待核验') + '（运行状态待核实）'


def scan_warehouse():
    """全资产盘点（60s 缓存）：TDX 远端仓 + 本地 K 线 + 新闻 + 策略产物。

    返回 {generated_at, kpi, groups:[{name, kind, rows:[...]}], gaps}。
    rows[] 统一字段：asset/rows/latest/size_mb/age_hours/extra(可选)。
    """
    if _WCACHE["d"] and time.time() - _WCACHE["t"] < 60:
        return _WCACHE["d"]
    kline = {mk: _scan_kline(mk) for mk in ("cn", "us", "hk", "crypto")}
    _annotate_cn_sample(kline)
    news = _scan_news()
    jev_reconciliation = _scan_jev_reconciliation()
    tdx = _scan_tdx()
    tush = _scan_tushare()
    fresh = _scan_freshness()
    if not isinstance(fresh, dict):
        fresh = {}
    fresh_error = fresh.get("error") or (
        "未取得新鲜度扫描结果" if not fresh.get("tables") else None)
    if fresh_error:
        # 添加独立失败记录，不修改扫描器返回的缓存对象。
        fresh = {"error": str(fresh_error), "tables": list(
            fresh.get("tables") or []) + [{
                "table": "freshness_scan", "live": None, "snap": None,
                "lag_days": None, "ready": False, "status": "FAIL",
                "error": str(fresh_error)}]}
    integrity = _scan_integrity()
    artifacts = _scan_artifacts()
    monitoring = _scan_business_monitoring()
    measured_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    groups = []
    # 1. TDX 远端仓（含同步进度）
    tdx_rows = [{"asset": t["asset"],
                 "rows": "%s (×%d片)" % (_fmt_rows(t["rows"]), t["parts"]),
                 "latest": t["latest"], "size_mb": t["size_mb"],
                 "age_hours": t["age_hours"],
                 "latest_kind": 'file_mtime', 'file_modified_at': t['latest'],
                 'measured_at': measured_at,
                 "extra": _manifest_label(t)}
                for t in tdx["tables"]]
    tdx_rows += [{"asset": i["asset"] + "（info.db）",
                  "rows": _fmt_rows(i["rows"]), "latest": i["latest"],
                  "size_mb": i.get('size_mb'), "age_hours": None,
                  'latest_kind': 'business_date', 'business_date': i['latest'],
                  'measured_at': measured_at,
                  "extra": '共享tdx_info.db，容量按物理路径计一次'} for i in tdx["info"]]
    tdx_summary = tdx.get("sync_summary")
    tdx_note = ("Parquet %d/%d；SQLite %d/%d；未完成：%s · tdx_info.db %.1f MB" % (
        tdx_summary["parquet_done"], tdx_summary["parquet_total"],
        tdx_summary["sqlite_done"], tdx_summary["sqlite_total"],
        ", ".join(tdx_summary["incomplete"]) or "无",
        tdx["db_size_mb"])) if tdx_summary else (
        "manifest已收口 %d/%d 表 · tdx_info.db %.1f MB" %
        (tdx["sync_done"], tdx["sync_total"], tdx["db_size_mb"]))
    groups.append({"name": "TDX 远端仓", "kind": "tdx", "rows": tdx_rows,
                   "note": tdx_note})
    # 1b. Tushare 仓（镜像 TDX 口径）
    tush_rows = [{"asset": t["asset"],
                  "rows": "%s (×%d文件)" % (_fmt_rows(t["rows"]), t["parts"]),
                  "latest": t["latest"], "size_mb": t["size_mb"],
                  "age_hours": t["age_hours"],
                  "latest_kind": 'file_mtime', 'file_modified_at': t['latest'],
                  'measured_at': measured_at,
                  "extra": _manifest_label(t)}
                 for t in tush["tables"]]
    tush_summary = tush.get("sync_summary")
    tush_note = ("实体表 %d/%d；探针 %d；无覆盖：%s；旧目录：%d 个" % (
        tush_summary["entity_done"], tush_summary["entity_total"],
        tush_summary["probe_count"],
        ", ".join(tush_summary["no_coverage"]) or "无",
        len(tush.get("legacy_dirs") or []))) if tush_summary else (
        "manifest已收口 %d/%d 表；运行状态待核实" %
        (tush["sync_done"], tush["sync_total"]))
    groups.append({"name": "Tushare 仓", "kind": "tushare", "rows": tush_rows,
                   "note": tush_note})
    if tush.get('manifest_only'):
        labels = {'location_unverified': '去向待核验（仅有旧回执，不推定丢失）',
                  'no_coverage': '无覆盖（codes=0，done不代表覆盖完成）',
                  'empty_probe': '合理空探针（0行，不计缺表故障）'}
        groups.append({'name': 'manifest独立回执', 'kind': 'manifest_receipt',
                       'rows': [{'asset': t['asset'], 'rows': _fmt_rows(t['rows']),
                                 'latest': None, 'size_mb': None, 'age_hours': None,
                                 'extra': labels[t['coverage_status']]} for t in tush['manifest_only']],
                       'note': '无物理目录的回执独立核对，不计物理资产数量'})
    # 1c. 新鲜度监控（catalog 驱动，§8）
    st_cn = {"OK": "✓", "WARN": "⚠", "STALE": "✗", "SNAP": "📸",
             "FAIL": "✗", "NA": "—"}
    f_rows = [{"asset": t["table"],
               "rows": ("lag %d天" % t["lag_days"])
                       if t["lag_days"] is not None else "—",
               "latest": t["live"] or "—",
               "size_mb": None, "age_hours": None,
               "extra": ("FAIL " + t["error"]) if t.get("error") else "%s 快照%s%s" % (
                   st_cn.get(t["status"], t["status"]), t["snap"] or "—",
                   "" if t["ready"] else " · 未就绪")}
              for t in fresh.get("tables", [])]
    fresh_counts = dict.fromkeys(('OK', 'WARN', 'STALE', 'FAIL', 'SNAP', 'NA', 'UNKNOWN'), 0)
    for table in fresh.get('tables') or []:
        status = table.get('status')
        fresh_counts[status if status in fresh_counts else 'UNKNOWN'] += 1
    n_bad = fresh_counts['STALE'] + fresh_counts['FAIL']
    non_ok = sum(n for s, n in fresh_counts.items() if s != 'OK')
    groups.append({"name": "新鲜度监控", "kind": "freshness", "rows": f_rows,
                   "note": ("新鲜度扫描失败：%s" % fresh_error) if fresh_error else
                   (' · '.join('%s %d 表' % (s, n) for s, n in fresh_counts.items() if n)) if non_ok
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
                       'latest_kind': 'file_mtime', 'measured_at': measured_at,
                       "extra": extra})
    groups.append({"name": "本地 K 线仓", "kind": "kline", "rows": k_rows,
                   "note": None})
    # 3. 新闻资讯（news.db）
    if news.get("exists"):
        groups.append({
            "name": "新闻资讯", "kind": "news",
            "rows": [{"asset": "快讯（%s）" % "、".join(
                          news.get("sources", {}).keys() or ["—"]),
                      "rows": _fmt_rows(news.get("total")),
                      "latest": (news.get("date_range") or "—").split(" ~ ")[-1],
                      "size_mb": news.get('size_mb'), "age_hours": None,
                      'latest_kind': 'event_time', 'source_times': news.get('source_times', {}),
                      'measured_at': news.get('measured_at'),
                      "extra": "JEV 已判 %d · 积压 %d"
                               % (news.get("jev_judged", 0),
                                  news.get("jev_pending", 0))}],
            "note": 'news.db主文件计入总容量；WAL另列 %s 字节，计量时点 %s' % (
                news.get('wal_size_bytes', '未计量'), news.get('measured_at', '待核验'))})
    if jev_reconciliation.get("available"):
        groups.append({
            "name": "JEV 队列对账", "kind": "jev_reconciliation",
            "rows": [{"asset": "新闻库 pending / staging / receipt",
                      "rows": "%d / %d / %d" % (
                          jev_reconciliation.get("news_pending", 0),
                          jev_reconciliation.get("queue_pending", 0),
                          jev_reconciliation.get("receipt_rows", 0)),
                      "latest": None, "size_mb": None, "age_hours": None,
                      "extra": jev_reconciliation.get("status")}],
            "note": "逐条 ID 对账；不把 staging 决策回写新闻库"})
    # 4. 策略产物
    groups.append({
        "name": "策略产物", "kind": "artifact",
        "rows": [{"asset": a["name"] + ("" if a.get("exists") else "（缺失）"),
                  "rows": _fmt_mb(a.get("size_mb")),
                  "latest": a.get("mtime"), "size_mb": a.get("size_mb"),
                  "age_hours": a.get("age_hours"), "extra": None,
                  'latest_kind': 'file_mtime', 'measured_at': measured_at,
                  "url": a.get("url")} for a in artifacts],
        "note": None})

    # KPI
    n_tables = (len(tdx["tables"]) + len(tdx["info"]) + len(tush["tables"])
                + 4 + 1 + len(artifacts))
    total_mb = (sum(t["size_mb"] for t in tdx["tables"]) + tdx["db_size_mb"]
                + sum(t["size_mb"] for t in tush["tables"])
                + sum(r.get("size_mb", 0) for r in kline.values())
                + sum(a.get("size_mb", 0) or 0 for a in artifacts)
                + (news.get('size_mb') or 0))
    ages = [g2["age_hours"] for g2 in tdx_rows if g2.get("age_hours")]
    freshest = ("%.1fh 前" % min(ages)) if ages else "—"
    kpi = {"n_tables": n_tables, "total_mb": round(total_mb, 1),
           "tdx_sync": "%d/%d" % (tdx["sync_done"], tdx["sync_total"]),
           "tushare_sync": "%d/%d" % (tush["sync_done"], tush["sync_total"]),
           "fresh_bad": n_bad, "freshest": freshest,
           'fresh_warn': fresh_counts['WARN'], 'fresh_snap': fresh_counts['SNAP'],
           'fresh_unknown': fresh_counts['NA'] + fresh_counts['UNKNOWN'],
           'capacity_scope': 'TDX/Tushare parquet、本地K线、tdx_info.db、news.db主文件及策略产物；共享库计一次，WAL另列',
           'capacity_measured_at': measured_at,
           "integrity_bad": integrity["fail_count"], "integrity_warn": integrity["warn_count"]}
    out = {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "kpi": kpi, "groups": groups,
           "integrity": integrity,
           "sync_summary": {"tdx": tdx.get("sync_summary"),
                            "tushare": tush.get("sync_summary")},
           'freshness': {**fresh, 'counts': fresh_counts}, 'monitoring': monitoring,
           'jev_reconciliation': jev_reconciliation,
           "gaps": _build_gaps(kline, news, artifacts, jev_reconciliation) + (
               ["FAIL freshness_scan: %s" % fresh_error] if fresh_error else []) + [
               "%s %s: %s" % (m["status"], m["name"], m["value"])
               for m in integrity["metrics"] if m["status"] in ("FAIL", "WARN")]}
    _WCACHE["t"], _WCACHE["d"] = time.time(), out
    return out


def _fmt_rows(n):
    """行数千分位 / 万位缩写。"""
    if n is None:
        return "未计量"
    return ("%.1f 万" % (n / 10000.0)) if n >= 10000 else str(n)


def _fmt_mb(mb):
    return ("%.1f MB" % mb) if mb else "—"


if __name__ == "__main__":
    d = scan_data_health()
    print(json.dumps(d, ensure_ascii=False, indent=2))
