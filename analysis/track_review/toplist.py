# -*- coding: utf-8 -*-
"""每日盘后「事件—价格影响 Top 列表」与 track.db 批量落库。

Top 列表在最近 N 个交易日内读取事件，按 ``(股票, d0)`` 聚成事件簇，计算
CAR 各窗口、行业基准与背离，按 |CAR[0,3]|（不可得时 |CAR[0,1]|）排序输出
Markdown + JSON。默认只读数据仓，产物写 ``<work_root>/toplist/``。
"""
from __future__ import annotations

import time
from collections import Counter
from datetime import datetime

import pandas as pd

from .benchmark import (
    METHOD_LABELS,
    BenchmarkProvider,
    divergence_flag,
    evaluate_event,
)
from .config import (
    Paths,
    default_paths,
    ensure_dirs,
    load_stock_names,
    normalize_date,
    write_json,
    write_text,
)
from .events import Event, assign_d0, count_by_source, load_events_for_range
from .store import TrackStore, evaluation_to_rows

POST_WINDOWS = ("d0_1", "d0_3", "d0_5", "d0_10")


# ---------------------------------------------------------------------------
# 事件簇
# ---------------------------------------------------------------------------
def cluster_events(events: list[Event]) -> list[dict]:
    """把事件按 ``(ts_code, d0)`` 聚成事件簇（同一股票同一生效日合并展示）。"""
    groups: dict[tuple[str, str], list[Event]] = {}
    for event in events:
        if not event.d0:
            continue
        groups.setdefault((event.ts_code, event.d0), []).append(event)
    clusters: list[dict] = []
    for (code, d0), items in groups.items():
        items.sort(key=lambda e: (e.event_date, e.source, e.category))
        primary = max(items, key=lambda e: (e.confidence * (abs(e.sentiment) + 0.1), e.event_date))
        weight = sum(e.confidence for e in items) or 1.0
        sentiment_net = sum(e.sentiment * e.confidence for e in items) / weight
        categories: list[str] = []
        for item in items:
            if item.category not in categories:
                categories.append(item.category)
        sources: list[str] = []
        for item in items:
            if item.source not in sources:
                sources.append(item.source)
        clusters.append(
            {
                "ts_code": code,
                "d0": d0,
                "event_date": min(e.event_date for e in items),
                "categories": categories,
                "category": primary.category,
                "sentiment": primary.sentiment,
                "sentiment_net": round(max(-1.0, min(1.0, sentiment_net)), 2),
                "confidence": primary.confidence,
                "sources": sources,
                "event_count": len(items),
                "summary": primary.summary,
                "dedupe_keys": sorted({e.dedupe_key for e in items}),
            }
        )
    return clusters


# ---------------------------------------------------------------------------
# 评估
# ---------------------------------------------------------------------------
def _rank_window(car: dict) -> tuple[str | None, float]:
    for window in ("d0_3", "d0_1"):
        value = car.get(window)
        if value is not None:
            return window, abs(float(value))
    return None, 0.0


def evaluate_clusters(
    provider: BenchmarkProvider,
    clusters: list[dict],
    panel_days: list[str],
    names: dict[str, str] | None = None,
) -> tuple[list[dict], dict]:
    """逐簇计算 CAR/行业基准/背离，返回 (按影响力排序的行, 统计)。"""
    names = names or {}
    stats: dict = {
        "clusters": len(clusters),
        "evaluated": 0,
        "panels_missing": 0,
        "no_post_window": 0,
        "no_industry": 0,
        "methods": Counter(),
        "industry_evidence": {},
    }
    rows: list[dict] = []
    panel_cache: dict[str, pd.DataFrame] = {}
    for cluster in clusters:
        code = cluster["ts_code"]
        d0 = cluster["d0"]
        panel = panel_cache.get(code)
        if panel is None:
            panel = provider.panel_from_cache(code, panel_days)
            panel_cache[code] = panel
        if panel is None or len(panel) == 0:
            stats["panels_missing"] += 1
            continue
        evaluation = evaluate_event(panel, d0)
        if not any(evaluation["car"].get(w) is not None for w in POST_WINDOWS):
            stats["no_post_window"] += 1
            continue
        attrs = panel.attrs or {}
        industry = attrs.get("industry") or {}
        methods = industry.get("methods") or []
        method_label = "、".join(industry.get("method_labels") or [METHOD_LABELS["market_proxy"]])
        for method in methods:
            stats["methods"][method] += 1
        index_code = industry.get("index_code")
        if index_code:
            stats["industry_evidence"][index_code] = {
                "industry_name": industry.get("industry_name"),
                "methods": methods,
                "evidence": (industry.get("industries") or [{}])[0],
            }
        else:
            stats["no_industry"] += 1
        car = evaluation["car"]
        rank_window, score = _rank_window(car)
        sentiment = float(cluster["sentiment"])
        divergence = divergence_flag(sentiment, car.get("d0_3"))
        row = {
            "code": code,
            "name": names.get(code, code),
            "event_date": cluster["event_date"],
            "d0": d0,
            "category": cluster["category"],
            "categories": cluster["categories"],
            "sentiment": sentiment,
            "sentiment_net": cluster["sentiment_net"],
            "confidence": cluster["confidence"],
            "sources": cluster["sources"],
            "event_count": cluster["event_count"],
            "car": car,
            "complete": evaluation["complete"],
            "n_obs": evaluation["n_obs"],
            "layer": evaluation["layer"],
            "rank_window": rank_window,
            "score": round(score, 2),
            "industry_index": index_code,
            "industry_name": industry.get("industry_name") or "",
            "industry_method": methods[0] if methods else "market_proxy",
            "industry_method_label": method_label,
            "industry_proxy_days": industry.get("proxy_days", 0),
            "divergence": divergence,
            "summary": cluster["summary"][:80],
        }
        row["explanation"] = _explain(row)
        rows.append(row)
        stats["evaluated"] += 1

    rows.sort(
        key=lambda r: (
            -float(r["score"]),
            -abs(float(r["sentiment"])),
            -int(r["event_count"]),
            str(r["d0"]),
        )
    )
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
    stats["methods"] = dict(stats["methods"])
    return rows, stats


def _explain(row: dict) -> str:
    """一句话解释：方向 × 类别 × CAR[0,3] × 行业基准 × 背离。"""
    sentiment = float(row.get("sentiment") or 0.0)
    if sentiment > 0.2:
        direction = "利好"
    elif sentiment < -0.2:
        direction = "利空"
    else:
        direction = "中性"
    car3 = (row.get("car") or {}).get("d0_3")
    if car3 is None:
        car3_text = "CAR[0,3] 待数据补齐"
    else:
        car3_text = f"CAR[0,3]={car3:+.2f}%"
    industry = row.get("industry_name") or "无行业归属"
    method = row.get("industry_method_label") or ""
    parts = [f"{row.get('category') or '事件'}{direction}", car3_text]
    if row.get("event_count", 1) > 1:
        parts.append(f"同日 {row['event_count']} 条事件")
    parts.append(f"行业基准：{industry}({method})")
    divergence = row.get("divergence")
    if divergence == "利好不涨":
        parts.append("利好不涨，关注兑现节奏与资金面")
    elif divergence == "利空不跌":
        parts.append("利空不跌，关注预期差修复")
    return "；".join(parts)


# ---------------------------------------------------------------------------
# 数据准备
# ---------------------------------------------------------------------------
def prepare_window(date: str | None, days: int, paths: Paths, provider: BenchmarkProvider) -> dict:
    """确定生效交易日、事件窗口与评估日期范围。"""
    requested = normalize_date(date)
    days = max(1, int(days or 10))
    all_days = provider.trade_days(None, requested)
    if not all_days:
        raise SystemExit(f"交易日历无数据（date={requested}）")
    effective = requested if requested in set(all_days) else all_days[-1]
    window_days = all_days[-days:]
    start = window_days[0]
    start_idx = all_days.index(start)
    panel_start_idx = max(0, start_idx - 5)
    panel_days = all_days[panel_start_idx:]
    return {
        "requested_date": requested,
        "effective_date": effective,
        "days": len(window_days),
        "window_days": window_days,
        "window_start": start,
        "window_end": window_days[-1],
        "panel_days": panel_days,
        "calendar_days": all_days,
    }


def _load_window_events(window: dict, paths: Paths) -> tuple[list[Event], list[Event], dict]:
    events = load_events_for_range(window["window_start"], window["window_end"], paths=paths)
    assign_d0(events, window["calendar_days"])
    aligned = [e for e in events if e.d0]
    stats = {
        "events_total": len(events),
        "events_aligned": len(aligned),
        "events_unaligned": len(events) - len(aligned),
        "events_by_source": count_by_source(events),
    }
    return events, aligned, stats


# ---------------------------------------------------------------------------
# Top 列表
# ---------------------------------------------------------------------------
def build_toplist(
    date: str | None = None,
    days: int = 10,
    limit: int = 20,
    paths: Paths | None = None,
    provider: BenchmarkProvider | None = None,
    write_outputs: bool = True,
) -> dict:
    """生成盘后事件—价格影响 Top 列表（Markdown + JSON）。"""
    started = time.time()
    paths = ensure_dirs(paths or default_paths())
    provider = provider or BenchmarkProvider(paths)
    limit = max(1, int(limit or 20))
    window = prepare_window(date, days, paths, provider)
    events, aligned, event_stats = _load_window_events(window, paths)
    provider.prime(window["panel_days"][0], window["panel_days"][-1])
    clusters = cluster_events(aligned)
    codes = sorted({c["ts_code"] for c in clusters})
    names = load_stock_names(codes, paths)
    rows, eval_stats = evaluate_clusters(provider, clusters, window["panel_days"], names)
    top_rows = rows[:limit]

    methods = Counter(r["industry_method"] for r in rows)
    benchmark_summary = {
        "method_counts": dict(methods),
        "method_labels": sorted({METHOD_LABELS.get(m, m) for m in methods}),
        "industries": len({r["industry_index"] for r in rows if r["industry_index"]}),
        "no_industry_rows": sum(1 for r in rows if not r["industry_index"]),
    }
    meta = {
        "module": "track_review.toplist",
        "requested_date": window["requested_date"],
        "date": window["effective_date"],
        "days": window["days"],
        "window_start": window["window_start"],
        "window_end": window["window_end"],
        "limit": limit,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "runtime_sec": round(time.time() - started, 2),
        "events_total": event_stats["events_total"],
        "events_aligned": event_stats["events_aligned"],
        "events_unaligned": event_stats["events_unaligned"],
        "events_by_source": event_stats["events_by_source"],
        "clusters": len(clusters),
        "evaluated": eval_stats["evaluated"],
        "rows": len(top_rows),
        "dropped": {
            "panels_missing": eval_stats["panels_missing"],
            "no_post_window": eval_stats["no_post_window"],
        },
        "benchmark": benchmark_summary,
        "benchmark_evidence": eval_stats.get("industry_evidence", {}),
        "car_definition": "CAR=ΣAR，AR=个股pct_chg−沪深300pct_chg（百分点）；窗口越界为 null",
        "data_sources": [
            "tdx:ts_anns_d/ts_forecast/ts_express/ts_repurchase/ts_stk_holdertrade/ts_block_trade/"
            "ts_share_float/ts_pledge_stat/ts_top_inst/ts_report_rc",
            "tushare_curated:daily/daily_basic/index_daily/sw_l1_member/sw_classify_l1/stock_basic/trade_cal",
            "tdx:ts_index_daily（SW L1 指数，覆盖不足时回退流通市值加权）",
        ],
    }
    payload = {"meta": meta, "rows": top_rows}
    output_paths: dict = {}
    if write_outputs:
        date_tag = window["effective_date"]
        md_path = f"{paths.toplist_dir}\\{date_tag}.md"
        json_path = f"{paths.toplist_dir}\\{date_tag}.json"
        write_text(md_path, render_markdown(payload))
        write_json(json_path, payload)
        output_paths = {"markdown": md_path, "json": json_path}
        meta["outputs"] = output_paths
        # meta 中补写产物路径（保持 md/json 一致）
        write_json(json_path, payload)
    payload["outputs"] = output_paths
    return payload


def _fmt(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{float(value):+.2f}"


def _md(text: object) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def render_markdown(payload: dict) -> str:
    """渲染 Top 列表 Markdown。"""
    meta = payload.get("meta") or {}
    rows = payload.get("rows") or []
    benchmark = meta.get("benchmark") or {}
    lines: list[str] = []
    lines.append(f"# 事件—价格影响 Top {len(rows)}（{meta.get('date')}）")
    lines.append("")
    lines.append(f"- 生成时间：{meta.get('generated_at')}（耗时 {meta.get('runtime_sec')}s）")
    lines.append(
        f"- 事件窗口：最近 {meta.get('days')} 个交易日（{meta.get('window_start')}~{meta.get('window_end')}）；"
        f"源事件 {meta.get('events_total')} 条，可对齐 {meta.get('events_aligned')} 条，"
        f"事件簇 {meta.get('clusters')} 个，可评估 {meta.get('evaluated')} 个"
    )
    lines.append(
        f"- 排名依据：|CAR[0,3]|（不可得时 |CAR[0,1]|），最多展示 {meta.get('limit')} 条"
    )
    lines.append(
        f"- 行业基准：{'、'.join(benchmark.get('method_labels') or ['—'])}（覆盖 {benchmark.get('industries')} 个行业）"
    )
    lines.append("- 数据：tdx 情报表 + tushare_curated 行情/行业（只读）")
    lines.append("")
    header = (
        "| 排名 | 代码 | 名称 | 事件日 | d0 | 类别 | 情绪 | CAR[-5,-1] | CAR[0,1] | CAR[0,3] | "
        "CAR[0,5] | CAR[0,10] | 行业（基准） | 背离 | 一句话解释 |"
    )
    lines.append(header)
    lines.append("|" + "---|" * 15)
    for row in rows:
        car = row.get("car") or {}
        industry = row.get("industry_name") or "—"
        method = row.get("industry_method_label") or ""
        industry_cell = f"{industry}<br/>{method}" if industry != "—" else (method or "—")
        lines.append(
            "| {rank} | {code} | {name} | {event_date} | {d0} | {category} | {sentiment:+.2f} | "
            "{pre} | {c1} | {c3} | {c5} | {c10} | {industry} | {div} | {expl} |".format(
                rank=row.get("rank"),
                code=row.get("code"),
                name=_md(row.get("name")),
                event_date=row.get("event_date"),
                d0=row.get("d0"),
                category=_md(row.get("category")),
                sentiment=float(row.get("sentiment") or 0.0),
                pre=_fmt(car.get("pre_5_1")),
                c1=_fmt(car.get("d0_1")),
                c3=_fmt(car.get("d0_3")),
                c5=_fmt(car.get("d0_5")),
                c10=_fmt(car.get("d0_10")),
                industry=_md(industry_cell),
                div=_md(row.get("divergence") or ""),
                expl=_md(row.get("explanation")),
            )
        )
    lines.append("")
    lines.append("## 说明")
    lines.append("")
    lines.append("- CAR 单位为百分点；AR = 个股 pct_chg − 沪深300 pct_chg；窗口越界显示“—”。")
    lines.append(
        "- 行业基准 auto 策略：申万 L1 行业指数覆盖请求窗口时用指数收益，否则用行业成分股"
        "（PIT：in_date≤d 且 out_date 为空或>d）流通市值加权；权重缺失日退化为等权。"
    )
    lines.append("- 背离：利好(sentiment>0)且 CAR[0,3]≤−1 → 利好不涨；利空且 CAR[0,3]≥+1 → 利空不跌。")
    lines.append(f"- 口径：{meta.get('car_definition')}。")
    lines.append("- 本列表仅为复盘研究用途，不构成投资建议。")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# build-db：事件 + 收益 + 案例落库
# ---------------------------------------------------------------------------
def build_db(
    end: str | None = None,
    days: int = 10,
    limit: int = 50,
    paths: Paths | None = None,
    db_path: str | None = None,
    provider: BenchmarkProvider | None = None,
) -> dict:
    """把窗口内事件、事件收益与 Top 案例写入 track.db（幂等）。"""
    started = time.time()
    paths = ensure_dirs(paths or default_paths())
    provider = provider or BenchmarkProvider(paths)
    window = prepare_window(end, days, paths, provider)
    events, aligned, event_stats = _load_window_events(window, paths)
    provider.prime(window["panel_days"][0], window["panel_days"][-1])
    clusters = cluster_events(aligned)
    codes = sorted({c["ts_code"] for c in clusters})
    names = load_stock_names(codes, paths)
    rows, eval_stats = evaluate_clusters(provider, clusters, window["panel_days"], names)

    # 逐事件收益（event_returns）
    panel_cache: dict[str, pd.DataFrame] = {}
    return_rows: list[dict] = []
    missing_panels = 0
    for event in aligned:
        panel = panel_cache.get(event.ts_code)
        if panel is None:
            panel = provider.panel_from_cache(event.ts_code, window["panel_days"])
            panel_cache[event.ts_code] = panel
        if panel is None or len(panel) == 0:
            missing_panels += 1
            continue
        evaluation = evaluate_event(panel, event.d0)
        return_rows.extend(evaluation_to_rows(event.event_id, evaluation))

    # Top 案例（cases）
    cases: list[dict] = []
    for row in rows[: max(1, int(limit or 50))]:
        cases.append(
            {
                "case_id": f"{row['code']}:{row['d0']}",
                "ts_code": row["code"],
                "name": row["name"],
                "event_date": row["event_date"],
                "d0": row["d0"],
                "category": row["category"],
                "sentiment": row["sentiment"],
                "confidence": row["confidence"],
                "car_d0_1": (row.get("car") or {}).get("d0_1"),
                "car_d0_3": (row.get("car") or {}).get("d0_3"),
                "car_d0_5": (row.get("car") or {}).get("d0_5"),
                "car_d0_10": (row.get("car") or {}).get("d0_10"),
                "industry_index": row.get("industry_index"),
                "industry_name": row.get("industry_name"),
                "industry_method": row.get("industry_method"),
                "divergence": row.get("divergence"),
                "explanation": row.get("explanation"),
                "payload": row,
            }
        )

    methods = Counter(r["industry_method"] for r in rows)
    meta = {
        "module": "track_review.build_db",
        "schema_version": "1",
        "window_start": window["window_start"],
        "window_end": window["window_end"],
        "days": window["days"],
        "requested_date": window["requested_date"],
        "effective_date": window["effective_date"],
        "built_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "runtime_sec": round(time.time() - started, 2),
        "events_total": event_stats["events_total"],
        "events_aligned": event_stats["events_aligned"],
        "clusters": len(clusters),
        "evaluated": eval_stats["evaluated"],
        "benchmark_methods": dict(methods),
        "events_by_source": event_stats["events_by_source"],
    }
    store = TrackStore(db_path=db_path, paths=paths)
    write_stats = store.save_build(events, return_rows, cases, meta)
    summary = {
        "window": {"start": window["window_start"], "end": window["window_end"], "days": window["days"]},
        "events_total": event_stats["events_total"],
        "events_aligned": event_stats["events_aligned"],
        "returns_rows": len(return_rows),
        "cases": len(cases),
        "missing_panels": missing_panels,
        "benchmark_methods": dict(methods),
        "db": write_stats,
        "db_stats": store.stats(),
        "runtime_sec": round(time.time() - started, 2),
    }
    return summary
