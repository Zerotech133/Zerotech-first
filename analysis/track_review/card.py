# -*- coding: utf-8 -*-
"""单股事件—价格影响复盘卡（TR1）。

在 TR0 原型口径上重构：

- 交易日序列、事件过滤、d0 对齐、CAR 口径、背离判定与 TR0 完全一致（保证可比）；
- 行业层升级：PIT 行业成员 + 申万 L1 指数优先、否则流通市值加权（evidence 落盘）；
- 产物写 ``<work_root>/artifacts/<code>_<end>_tr1.md/.json``，不覆盖 TR0 产物，
  并读取同名 TR0 JSON 生成对照段落（事件数/行业基准差异）。
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime

from .benchmark import (
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
    read_curated,
    today_str,
    write_json,
    write_text,
)
from .events import assign_d0, count_by_source, load_events_for_code


def _fmt(value) -> str:
    return f"{value:+.2f}" if isinstance(value, (int, float)) else "N/A"


def _cat_key(category: str) -> str:
    """公告类取细分关键词首个；其余取类别本名（与 TR0 一致）。"""
    if category.startswith("公告·"):
        return category.split("·", 1)[1].split("、")[0]
    return category


def _summary(events: list[dict]) -> dict:
    result: dict = {}
    for event in events:
        key = _cat_key(event["category"])
        bucket = result.setdefault(key, {"n": 0, "car3": [], "div": 0})
        bucket["n"] += 1
        car3 = (event.get("car") or {}).get("d0_3")
        if car3 is not None:
            bucket["car3"].append(car3)
        if event.get("divergence"):
            bucket["div"] += 1
    for bucket in result.values():
        values = bucket.pop("car3")
        bucket["avg_car3"] = round(sum(values) / len(values), 2) if values else None
    return result


def _load_tr0_json(code: str, end: str, paths: Paths) -> dict | None:
    path = os.path.join(paths.artifacts_dir, f"{code.replace('.', '_')}_{end}.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        data["_path"] = path
        return data
    except (OSError, ValueError):
        return None


def _tr0_comparison(tr0: dict | None, meta: dict, summary: dict, end: str) -> dict:
    if not tr0:
        return {
            "available": False,
            "note": "未找到同窗口 TR0 产物；跳过对照（TR0 卡属于只读原型，不修改）",
        }
    tr0_meta = tr0.get("meta") or {}
    tr0_summary = tr0.get("summary") or {}
    cats = sorted(
        {str(k) for k in tr0_summary} | {str(k) for k in summary},
        key=lambda k: -(int((tr0_summary.get(k) or {}).get("n", 0)) + int((summary.get(k) or {}).get("n", 0))),
    )
    summary_rows = []
    for cat in cats:
        left = tr0_summary.get(cat) or {}
        right = summary.get(cat) or {}
        summary_rows.append(
            {
                "category": cat,
                "tr0_n": left.get("n", 0),
                "tr1_n": right.get("n", 0),
                "tr0_avg_car3": left.get("avg_car3"),
                "tr1_avg_car3": right.get("avg_car3"),
                "tr0_div": left.get("div", 0),
                "tr1_div": right.get("div", 0),
            }
        )
    return {
        "available": True,
        "path": tr0.get("_path"),
        "events_total": {"tr0": tr0_meta.get("events_total"), "tr1": meta.get("events_total")},
        "events_aligned": {"tr0": tr0_meta.get("events_aligned"), "tr1": meta.get("events_aligned")},
        "trade_days": {"tr0": tr0_meta.get("trade_days"), "tr1": meta.get("trade_days")},
        "industry": {"tr0": tr0_meta.get("industry"), "tr1": meta.get("industry")},
        "industry_peers": {"tr0": tr0_meta.get("industry_peers"), "tr1": meta.get("industry_peers")},
        "industry_layer": {
            "tr0": "SW L1 成分等权（未扣权重）",
            "tr1": meta.get("industry_benchmark", {}).get("method_label"),
        },
        "summary_by_category": summary_rows,
        "car_note": "CAR=Σ(个股−沪深300) 口径与 TR0 相同，同一事件 CAR 不随行业基准变化；"
        "行业层改为 PIT 流通市值加权后，行业超额/个股特质会变化。",
    }


def build_card(
    code: str,
    end: str | None = None,
    days: int = 250,
    top: int = 15,
    paths: Paths | None = None,
    provider: BenchmarkProvider | None = None,
    write_outputs: bool = True,
) -> dict:
    """生成单股复盘卡，返回 payload（同时写 md/json/log）。"""
    started = time.time()
    paths = ensure_dirs(paths or default_paths())
    code = str(code or "").strip()
    if not code:
        raise ValueError("code is required")
    end = normalize_date(end) or today_str()
    days = max(20, int(days or 250))
    top = max(1, int(top or 15))
    provider = provider or BenchmarkProvider(paths)

    stock = read_curated(
        "daily.parquet",
        columns=["ts_code", "trade_date", "close", "pct_chg", "vol", "amount"],
        filters=[("ts_code", "==", code), ("trade_date", "<=", end)],
        paths=paths,
    )
    if stock is None or len(stock) == 0:
        raise SystemExit(f"no daily data for {code}")
    stock = stock.copy()
    stock["trade_date"] = stock["trade_date"].map(normalize_date)
    stock = stock.dropna(subset=["trade_date"]).drop_duplicates(subset=["trade_date"], keep="last")
    stock = stock.sort_values("trade_date").reset_index(drop=True)
    if len(stock) == 0:
        raise SystemExit(f"no valid daily data for {code}")
    market = provider.market_frame()
    if len(market) == 0:
        raise SystemExit("no index_daily data for 000300.SH")
    common = [d for d in stock["trade_date"].tolist() if d in market.index]
    if not common:
        raise SystemExit(f"no overlapping trade days for {code} and 000300.SH")
    dates = common[-days:]
    start = dates[0]
    panel = provider.panel_from_frame(code, stock, dates)
    if panel is None or len(panel) == 0:
        raise SystemExit(f"cannot build panel for {code}")
    panel_dates = [str(d) for d in panel.index]

    events_objs = load_events_for_code(code, start, end, paths=paths)
    assign_d0(events_objs, panel_dates)
    events: list[dict] = []
    for event in events_objs:
        record = {
            "event_id": event.event_id,
            "date": event.event_date,
            "d0": event.d0,
            "category": event.category,
            "sentiment": event.sentiment,
            "desc": event.summary,
            "source": event.source,
            "confidence": event.confidence,
            "dedupe_key": event.dedupe_key,
            "car": {},
            "complete": {},
            "n_obs": {},
            "layer": {},
            "divergence": None,
        }
        if event.d0:
            evaluation = evaluate_event(panel, event.d0)
            record["car"] = evaluation["car"]
            record["complete"] = evaluation["complete"]
            record["n_obs"] = evaluation["n_obs"]
            record["layer"] = evaluation["layer"]
            record["divergence"] = divergence_flag(event.sentiment, evaluation["car"].get("d0_3"))
        events.append(record)

    aligned = [e for e in events if e["d0"]]
    recent = aligned[-top:]
    summary = _summary(aligned)
    tr0 = _load_tr0_json(code, end, paths)

    attrs = panel.attrs or {}
    industry = attrs.get("industry") or {}
    industry_benchmark = {
        "method": (industry.get("methods") or ["market_proxy"])[0],
        "method_label": "、".join(industry.get("method_labels") or ["沪深300代理"]),
        "index_code": industry.get("index_code"),
        "industry_name": industry.get("industry_name"),
        "membership_source": industry.get("membership_source"),
        "proxy_days": industry.get("proxy_days", 0),
        "date_count": industry.get("date_count", len(panel_dates)),
        "industries": industry.get("industries") or [],
    }
    peers = provider._industry_members(str(industry.get("index_code"))) if industry.get("index_code") else []
    if not peers:
        peers = []
    names = load_stock_names([code], paths)
    meta = {
        "module": "track_review.card",
        "code": code,
        "name": names.get(code, code),
        "start": start,
        "end": end,
        "trade_days": len(panel_dates),
        "industry": industry.get("industry_name"),
        "industry_index": industry.get("index_code"),
        "industry_peers": len(peers),
        "industry_benchmark": industry_benchmark,
        "events_total": len(events),
        "events_aligned": len(aligned),
        "events_by_source": count_by_source(events_objs),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "runtime_sec": None,
        "car_definition": "CAR=ΣAR，AR=个股pct_chg−沪深300pct_chg（百分点）；窗口越界为 null",
    }
    comparison = _tr0_comparison(tr0, meta, summary, end)
    latest_close = None
    if "close" in stock.columns and len(stock):
        try:
            latest_close = {
                "date": panel_dates[-1],
                "close": float(stock[stock["trade_date"] <= panel_dates[-1]]["close"].iloc[-1]),
            }
        except (IndexError, TypeError, ValueError):
            latest_close = None

    payload = {
        "meta": meta,
        "events": recent,
        "events_all_count": len(events),
        "summary": summary,
        "benchmark": industry_benchmark,
        "tr0_comparison": comparison,
        "latest_close": latest_close,
    }

    stem = f"{code.replace('.', '_')}_{end}_tr1"
    md_path = os.path.join(paths.artifacts_dir, stem + ".md")
    json_path = os.path.join(paths.artifacts_dir, stem + ".json")
    runtime = time.time() - started
    meta["runtime_sec"] = round(runtime, 2)
    payload["meta"] = meta
    markdown = render_markdown(payload)
    if write_outputs:
        write_text(md_path, markdown)
        write_json(json_path, payload)
        log_path = os.path.join(paths.logs_dir, f"{stem}_{datetime.now():%H%M%S}.json")
        write_json(
            log_path,
            {"meta": meta, "markdown": md_path, "json": json_path, "benchmark": industry_benchmark},
        )
        meta["outputs"] = {"markdown": md_path, "json": json_path, "log": log_path}
        payload["meta"] = meta
        write_json(json_path, payload)
    return payload


def render_markdown(payload: dict) -> str:
    """渲染单股卡片 Markdown（结构对齐 TR0，新增行业基准与 TR0 对照两节）。"""
    meta = payload.get("meta") or {}
    events = payload.get("events") or []
    summary = payload.get("summary") or {}
    benchmark = payload.get("benchmark") or {}
    comparison = payload.get("tr0_comparison") or {}
    code = meta.get("code")
    lines: list[str] = []
    lines.append(f"# {meta.get('name')}（{code}）· 事件—价格影响复盘卡（TR1）")
    lines.append("")
    lines.append(f"- 区间：{meta.get('start')} ~ {meta.get('end')}（{meta.get('trade_days')} 个交易日）")
    lines.append(
        f"- 行业：{meta.get('industry') or '未知'}（{meta.get('industry_peers')} 只，PIT 口径；"
        f"基准：{benchmark.get('method_label')}）"
    )
    lines.append("- 市场基准：沪深300；事件对齐：盘后披露→次一交易日（与 TR0 同口径）")
    lines.append(f"- 事件总数：{meta.get('events_total')}（可对齐 {meta.get('events_aligned')}）")
    lines.append(f"- 生成时间：{meta.get('generated_at')}（耗时 {meta.get('runtime_sec')}s）")
    lines.append("")
    lines.append(f"## 一、事件时间线（最近 {len(events)} 条）")
    lines.append("")
    lines.append("| 事件日 | 对齐 d0 | 类别 | 方向 | 摘要 | CAR[0,1] | CAR[0,3] | CAR[0,5] | 背离 |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for event in reversed(events):
        car = event.get("car") or {}
        desc = str(event.get("desc") or "")[:42].replace("|", "\\|")
        lines.append(
            f"| {event.get('date')} | {event.get('d0')} | {event.get('category')} | "
            f"{float(event.get('sentiment') or 0):+.2f} | {desc} | {_fmt(car.get('d0_1'))} | "
            f"{_fmt(car.get('d0_3'))} | {_fmt(car.get('d0_5'))} | {event.get('divergence') or '-'} |"
        )
    lines.append("")
    lines.append("## 二、按类别汇总")
    lines.append("")
    lines.append("| 类别 | 事件数 | 平均 CAR[0,3] | 背离数 |")
    lines.append("|---|---|---|---|")
    for cat, bucket in sorted(summary.items(), key=lambda kv: -kv[1]["n"]):
        avg = bucket.get("avg_car3")
        avg_text = f"{avg:+.2f}" if isinstance(avg, (int, float)) else "N/A"
        lines.append(f"| {cat} | {bucket['n']} | {avg_text} | {bucket['div']} |")
    lines.append("")
    lines.append("## 三、最大单事件三层分解（|CAR[0,3]| 最大）")
    lines.append("")
    biggest = None
    for event in events:
        value = (event.get("car") or {}).get("d0_3")
        if value is None:
            continue
        if biggest is None or abs(value) > abs((biggest.get("car") or {}).get("d0_3") or 0):
            biggest = event
    if biggest:
        lines.append(f"- 事件：{biggest.get('date')} {biggest.get('category')}（{str(biggest.get('desc'))[:60]}）")
        for window, layer in (biggest.get("layer") or {}).items():
            lines.append(
                f"- {window}：个股 {layer['stock']:+.2f}% = 市场 {layer['market']:+.2f}% + "
                f"行业超额 {layer['industry_excess']:+.2f}% + 个股特质 {layer['specific']:+.2f}%"
            )
        lines.append(f"- 行业基准：{benchmark.get('industry_name')}（{benchmark.get('method_label')}）")
    else:
        lines.append("- 无可分解事件")
    lines.append("")
    lines.append("## 四、背离提示")
    lines.append("")
    divergences = [e for e in events if e.get("divergence")]
    if divergences:
        for event in divergences:
            car3 = (event.get("car") or {}).get("d0_3")
            lines.append(
                f"- {event.get('date')} {event.get('category')}：{str(event.get('desc'))[:50]} → "
                f"{event.get('divergence')}（CAR[0,3]={car3}%）"
            )
    else:
        lines.append("- 区间内未发现方向背离")
    lines.append("")
    lines.append("## 五、行业基准与数据证据")
    lines.append("")
    lines.append(f"- 主行业：{benchmark.get('industry_name')}（{benchmark.get('index_code')}），"
                 f"成员口径：{benchmark.get('membership_source')}")
    lines.append(f"- 采用方法：{benchmark.get('method_label')}；沪深300 代理日数：{benchmark.get('proxy_days')}")
    for evidence in (benchmark.get("industries") or [])[:3]:
        lines.append(
            f"- evidence：method={evidence.get('method')}；source={evidence.get('source')}；"
            f"覆盖 {evidence.get('dates_covered')}/{evidence.get('dates_required')} 日；"
            f"指数区间 {evidence.get('index_start') or '—'}~{evidence.get('index_end') or '—'}；"
            f"成员 {evidence.get('members')}、权重行 {evidence.get('weight_rows')}；"
            f"回退原因：{evidence.get('fallback_reason') or '无'}"
        )
    lines.append("")
    lines.append("## 六、与 TR0 原型对照")
    lines.append("")
    if comparison.get("available"):
        lines.append(f"- TR0 产物：{comparison.get('path')}（只读，未修改）")
        lines.append(
            f"- 事件数：TR0 {comparison['events_total']['tr0']} → TR1 {comparison['events_total']['tr1']}；"
            f"可对齐：TR0 {comparison['events_aligned']['tr0']} → TR1 {comparison['events_aligned']['tr1']}；"
            f"交易日：TR0 {comparison['trade_days']['tr0']} → TR1 {comparison['trade_days']['tr1']}"
        )
        lines.append(
            f"- 行业层：TR0「{comparison['industry_layer']['tr0']}」→ TR1「{comparison['industry_layer']['tr1']}」"
        )
        lines.append(f"- {comparison.get('car_note')}")
        rows = comparison.get("summary_by_category") or []
        if rows:
            lines.append("")
            lines.append("| 类别 | TR0 事件数 | TR1 事件数 | TR0 平均CAR[0,3] | TR1 平均CAR[0,3] |")
            lines.append("|---|---|---|---|---|")
            for row in rows:
                left = row.get("tr0_avg_car3")
                right = row.get("tr1_avg_car3")
                lines.append(
                    f"| {row['category']} | {row['tr0_n']} | {row['tr1_n']} | "
                    f"{_fmt(left) if left is not None else 'N/A'} | {_fmt(right) if right is not None else 'N/A'} |"
                )
    else:
        lines.append(f"- {comparison.get('note')}")
    lines.append("")
    lines.append("## 七、口径与局限")
    lines.append("")
    lines.append("- 事件时间：ts_anns_d/研报只有日期无披露时刻 → 一律按「T 日盘后披露、T+1 生效」对齐；")
    lines.append("- 异常收益：AR = 个股 pct_chg − 沪深300 pct_chg；CAR 为区间求和；")
    lines.append("- 行业基准：申万 L1 指数覆盖窗口时用指数，否则用 PIT 成员流通市值加权；")
    lines.append("- 本卡为研究复盘，不构成投资建议。")
    lines.append("")
    return "\n".join(lines)
