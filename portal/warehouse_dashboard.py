"""数据仓五视图：服务端只消费快照，每日操作经浏览器明确预览与确认。"""

import html
import json
import math
import re


_FAMILIES = {
    "tdx_sector_daily": "板块日线",
    "ts_margin_detail": "融资融券明细",
    "ts_daily_basic": "股票每日指标",
    "ts_daily": "股票日线",
    "sector_minute": "板块分钟",
}
_SPECIAL = {
    "calendar": ("交易日历", "交易日"),
    "daily_basic_key_reconciliation": ("股票行情与每日指标键对账", "差异键"),
    "sector_definition_key_reconciliation": ("板块定义与日线键对账", "差异键"),
    "listed_stock_latest_coverage": ("上市股票行情资格核对", "未解释股票"),
    "sector_minute_manifest": ("板块分钟分片清单", "记录"),
    "sector_minute_tail_manifest": ("板块分钟尾部分片清单", "记录"),
    "query_daily_history": ("股票跨源历史查询", "查询返回行"),
    "query_cb_daily": ("可转债日线查询", "查询返回行"),
    "query_income": ("利润表查询", "查询返回行"),
    "query_top10_holders": ("十大股东查询", "查询返回行"),
    "query_top10_floatholders": ("十大流通股东查询", "查询返回行"),
    "query_reader": ("历史查询读取器", "查询结果"),
}
_SUFFIXES = {
    "required_nulls": ("关键字段空值", "空字段"),
    "optional_nulls": ("可选字段空值", "空字段"),
    "duplicate_keys": ("重复键", "重复记录"),
    "calendar_gaps": ("整日缺口", "交易日"),
    "daily_coverage": ("按日唯一代码覆盖", "码日缺键"),
    "scan": ("扫描异常", "扫描结果"),
}
_STATUS = {
    "FAIL": "异常",
    "WARN": "待核实",
    "OK": "通过",
    "INFO": "参考",
    "UNKNOWN": "未知",
}


def _number(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _metric_title(name):
    if name in _SPECIAL:
        return _SPECIAL[name]
    for prefix, title in _FAMILIES.items():
        if name.startswith(prefix + "_"):
            suffix = name[len(prefix) + 1 :]
            label, unit = _SUFFIXES.get(suffix, (suffix, "指标值"))
            return title + " · " + label, unit
    return name, "指标值"


_PAGE_STATE = {
    "ok": "正常",
    "stale": "数据可能过期",
    "no_data": "无数据",
    "not_found": "未找到",
}


def _data_date(data):
    """页面数据时点：优先扫描生成时间，其次完整度时间/应到交易日/快照日期。"""
    integrity = data.get("integrity") or {}
    daily = data.get("daily_snapshot") or {}
    for raw in (
        data.get("generated_at"),
        integrity.get("generated_at"),
        integrity.get("expected_latest"),
        daily.get("date"),
    ):
        text = str(raw or "").strip()
        hit = re.search(r"\d{4}-\d{2}-\d{2}", text)
        if hit:
            return hit.group(0)
        hit = re.search(r"(?<!\d)\d{8}(?!\d)", text)
        if hit:
            day = hit.group(0)
            return "%s-%s-%s" % (day[:4], day[4:6], day[6:])
    return None


def _page_state(data, state):
    """页面三态：not_found（无快照）/ no_data（空扫描）/ stale（刷新异常沿用上次）/ ok。"""
    if not isinstance(data, dict):
        return "not_found", "扫描结果不存在（尚无核验快照）"
    if state.get("error"):
        return "stale", "刷新失败，当前显示上次扫描结果：" + str(state["error"])
    if state.get("refreshing"):
        return "stale", "重新核验中，当前显示上次扫描结果。"
    if not ((data.get("integrity") or {}).get("metrics") or data.get("groups")):
        return "no_data", "本次扫描结果为空（无核验项目/资产），请先完成核验后刷新。"
    return "ok", ""


def build_model(data, state):
    """统一展示口径；不同单位及重复对账指标永远不相加。"""
    raw_data = data
    data = data or {}
    integrity = data.get("integrity") or {}
    metrics, coverage, assets = [], [], []
    counts = dict.fromkeys(_STATUS, 0)
    for raw in integrity.get("metrics") or []:
        name = str(raw.get("name", "未命名指标"))
        label, unit = _metric_title(name)
        if name in ("ts_daily_daily_coverage", "ts_daily_basic_daily_coverage"):
            unit = "码日参考数量差"
        status = raw.get("status") if raw.get("status") in counts else "UNKNOWN"
        details = raw.get("details") or {}
        policy = details.get("_summary", {}) if isinstance(details, dict) else {}
        metrics.append(
            {
                "name": name,
                "label": label,
                "unit": unit,
                "status": status,
                "status_label": _STATUS[status],
                "value": raw.get("value"),
                "details": details,
                "policy": policy.get("value_unit") or details.get("value_unit", "")
                if isinstance(details, dict)
                else "",
            }
        )
        counts[status] += 1
    by_name = {m["name"]: m for m in metrics}
    for metric in metrics:
        if not metric["name"].endswith("_daily_coverage") or not isinstance(
            metric["details"], dict
        ):
            continue
        days = {}
        summary = metric["details"].get("_summary", {})
        for date, row in metric["details"].items():
            if not re.fullmatch(r"\d{8}", date) or not isinstance(row, dict):
                continue
            actual, reference = (
                row.get("unique_codes"),
                row.get("reference_unique_codes"),
            )
            missing = row.get("missing")
            count = row.get("missing_count")
            # 日线数量覆盖没有逐键列表时，沿用同一次扫描的定义对账证据。
            if metric["name"] == "tdx_sector_daily_daily_coverage":
                reconciliation = by_name.get(
                    "sector_definition_key_reconciliation", {}
                ).get("details", {})
                reconciled = reconciliation.get(date, {})
                if (
                    isinstance(reconciled.get("missing"), list)
                    and _number(reconciled.get("defined"))
                    and reconciled["defined"] > 0
                ):
                    missing = reconciled["missing"]
                    count = len(missing)
                    reference = reconciled["defined"]
                    actual = reconciled.get("actual", actual)
            per_key = (
                isinstance(missing, list)
                and summary.get("reference_source") != "recent_max_unique_count"
            )
            if not _number(count):
                count = (
                    len(missing)
                    if isinstance(missing, list)
                    else (
                        max(0, reference - actual)
                        if _number(reference) and reference > 0 and _number(actual)
                        else None
                    )
                )
            valid = (
                _number(actual)
                and actual >= 0
                and _number(reference)
                and reference > 0
                and _number(count)
                and count >= 0
            )
            source_state = row.get("source_state") if isinstance(row, dict) else None
            days[date] = {
                "actual": actual,
                "reference": reference,
                "missing_count": count,
                "missing": missing,
                "status": ("gap" if count else "ok") if valid else "unknown",
                "source_state": source_state,
                "source_empty_pending": source_state == "source_empty_pending",
                "gap_type": "keys" if per_key else "reference",
                "qualified": bool(valid and count == 0 and actual < reference),
                "details": row,
            }
        coverage.append(
            {
                "name": metric["name"],
                "label": metric["label"].split(" · ")[0],
                "policy": metric["policy"] or "以本次扫描参考集合为准",
                "days": days,
                "summary": metric["details"].get("_summary", {}),
            }
        )
    for group in data.get("groups") or []:
        if group.get("kind") in ("integrity", "freshness", "manifest_receipt"):
            continue
        for row in group.get("rows") or []:
            age = row.get("age_hours")
            freshness = (
                "unknown"
                if not _number(age)
                else "ok"
                if age <= 26
                else "warn"
                if age <= 72
                else "old"
            )
            assets.append(
                {**row, "group": group.get("name", "未分组"), "freshness": freshness}
            )
    notice = ""
    if state.get("refreshing"):
        notice = "重新核验中，当前显示上次扫描结果。"
    elif state.get("error"):
        notice = "刷新失败，当前显示上次扫描结果。"
    if state.get("error"):
        notice += " " + str(state["error"])
    health = (
        "unknown"
        if not metrics
        else "fail"
        if counts["FAIL"]
        else "unknown"
        if counts["UNKNOWN"]
        else "warn"
        if counts["WARN"]
        else "ok"
    )
    page_state, page_reason = _page_state(raw_data, state)
    return {
        "generated_at": data.get("generated_at", "未知"),
        "integrity_at": integrity.get("generated_at", "未知"),
        "expected_latest": integrity.get("expected_latest", "未知"),
        "data_date": _data_date(data),
        "state": page_state,
        "state_label": _PAGE_STATE[page_state],
        "state_reason": page_reason,
        "counts": counts,
        "metrics": metrics,
        "coverage": coverage,
        "assets": assets,
        "kpi": data.get("kpi") or {},
        "health_status": health,
        "freshness": data.get("freshness") or {},
        "monitoring": data.get("monitoring") or {},
        "manifest_receipts": [
            row
            for g in data.get("groups") or []
            if g.get("kind") == "manifest_receipt"
            for row in g.get("rows") or []
        ],
        "stale": bool(state.get("refreshing") or state.get("error")),
        "notice": notice,
        "group_notes": [
            {"name": g.get("name", "未分组"), "note": g["note"]}
            for g in data.get("groups") or []
            if g.get("note") and g.get("kind") != "integrity"
        ],
        "other_gaps": data.get("gaps") or [],
    }


_DAILY_HTML = """
<section id="wh-daily" role="tabpanel" aria-labelledby="wh-tab-daily" hidden>
<div class="wh-section"><h2>每日更新</h2><p class="muted">按浏览日期核对目标、来源与本次写入。执行结束后，以目标核验决定是否已更新。</p>
<div class="wh-tools wh-daily-tools"><label>浏览日期<input id="wh-daily-date" type="date" required></label>
<label>更新来源<select id="wh-daily-source"><option value="tdx">TDX</option><option value="tushare">Tushare</option></select></label>
<label>更新市场<select id="wh-daily-market"><option value="all">全部市场</option><option value="cn">A股</option><option value="hk">港股</option><option value="us">美股</option></select></label>
<button id="wh-daily-refresh">刷新每日核验</button><button id="wh-daily-preview">预览当前来源范围</button><button id="wh-daily-batch">预览当天未完成项</button>
<button id="wh-daily-export">导出每日状态与来源 JSON</button></div>
<p id="wh-daily-message" role="status" aria-live="polite">进入此视图后读取每日目标。</p>
<p id="wh-daily-summary" class="muted"></p><div class="wh-table-scroll"><table class="wh-daily-table"><thead><tr>
<th>数据 / 市场</th><th>提供方 / 渠道</th><th>应到时点 / 资格</th><th>数据状态</th><th>本次写入</th><th>操作</th></tr></thead><tbody id="wh-daily-rows"></tbody></table></div>
<p id="wh-daily-empty" class="muted" hidden>该日期和来源尚无可核验项目，请查看登记范围或更换筛选。</p></div>
<div class="wh-section"><h2>任务与逐请求来源</h2><p class="muted">计划单元全部获得结果表示执行结束；失败和来源空结果分别列出。</p>
<div id="wh-daily-runs" class="wh-tools"></div><div class="wh-tools"><label>任务标识<input id="wh-daily-run-id" maxlength="32" aria-label="任务标识" placeholder="32位任务标识"></label><button id="wh-daily-open-run">查看任务</button><button id="wh-daily-poll">刷新任务状态</button><button id="wh-daily-retry" disabled>预览失败项重试</button></div>
<p id="wh-daily-retry-note" class="muted">精确单元重试资格待确认。</p>
<h3 id="wh-daily-run-title">尚未选择任务</h3><p id="wh-daily-run-state" role="status" aria-live="polite"></p>
<progress id="wh-daily-progress" max="1" aria-label="计划单元执行进度"></progress><p id="wh-daily-progress-label" class="muted">计划单元分母未确定</p>
<p id="wh-daily-run-metrics"></p><div id="wh-daily-receipts" class="wh-receipts"></div>
<details><summary>单元结果与事件记录</summary><pre id="wh-daily-events">尚未取得事件记录。</pre></details></div></section>
<dialog id="wh-daily-plan-dialog" aria-labelledby="wh-daily-plan-title"><div class="wh-dialog-head"><h2 id="wh-daily-plan-title">确认更新范围</h2><button id="wh-daily-cancel">取消</button></div>
<p id="wh-daily-plan-summary"></p><div id="wh-daily-plan-body"></div><p id="wh-daily-plan-message" role="status"></p>
<div class="wh-tools"><button id="wh-daily-confirm" disabled>确认启动</button></div></dialog>
"""


_CSS = """
<style>
.wh{max-width:1440px;margin:0 auto;color:var(--text);font-size:var(--fs-m)}
.wh *{box-sizing:border-box}.wh [hidden]{display:none!important}
.wh-header{display:flex;justify-content:space-between;align-items:flex-start;gap:var(--sp-5);margin:var(--sp-5) 0}
.wh h1{margin:0 0 var(--sp-2);font-size:28px}.wh h2{font-size:18px;margin:0 0 var(--sp-3)}
.wh p{line-height:1.7;margin:var(--sp-2) 0;max-width:80ch}.wh small,.wh .muted{color:var(--text-dim)}
.wh button,.wh input,.wh select{font:inherit;color:var(--text);background:var(--bg-raised);border:1px solid var(--border);border-radius:var(--r-s);padding:8px 12px}
.wh button{cursor:pointer}.wh button:hover{border-color:var(--link)}.wh button:disabled{opacity:.6;cursor:wait}
.wh :focus-visible{outline:2px solid var(--link);outline-offset:3px}
.wh-header-right{text-align:right;line-height:1.8;flex-shrink:0}
.wh-tabs{display:flex;gap:var(--sp-2);border-bottom:1px solid var(--border);padding-bottom:var(--sp-3);margin-bottom:var(--sp-5);flex-wrap:wrap}
.wh-tabs button[aria-selected=true]{background:var(--accent);border-color:var(--link);color:var(--text)}
.wh-section{padding:var(--sp-5);background:var(--bg-surface);border:1px solid var(--border-weak);border-radius:var(--r-m);margin-bottom:var(--sp-4)}
.wh-status{border-left:4px solid var(--text-dim);display:flex;align-items:center;justify-content:space-between;gap:var(--sp-4)}
.wh-status.fail{border-left-color:var(--color-err)}.wh-status.warn{border-left-color:var(--color-warn)}.wh-status.ok{border-left-color:var(--color-ok)}
.wh-status strong{font-size:20px}.wh-stats{display:flex;gap:var(--sp-5);flex-wrap:wrap;margin:var(--sp-4) 0 0}.wh-stat b{font-size:26px;display:block;font-variant-numeric:tabular-nums}
.wh-split{display:grid;grid-template-columns:2fr 1fr;gap:var(--sp-4)}
.wh-note{padding:var(--sp-3) var(--sp-4);border:1px solid var(--color-warn);border-radius:var(--r-s);color:var(--color-warn);margin-bottom:var(--sp-4)}
.wh-tools{display:flex;align-items:center;gap:var(--sp-3);flex-wrap:wrap;margin:var(--sp-4) 0}.wh-tools input{flex:1;min-width:180px}
.wh-table-scroll{overflow:auto}.wh table{width:100%;border-collapse:collapse;font-size:var(--fs-s)}
.wh th{text-align:left;color:var(--text-dim);font-weight:500;background:var(--bg-raised);position:sticky;top:0;z-index:1}
.wh td,.wh th{padding:10px 12px;border-bottom:1px solid var(--border-weak);vertical-align:top}
.wh td small{display:block;margin-top:4px}.wh td.num{font-variant-numeric:tabular-nums;white-space:nowrap}
.wh-badge{display:inline-block;border:1px solid currentColor;border-radius:var(--r-pill);padding:2px 9px;font-size:var(--fs-xs);white-space:nowrap}
.wh .FAIL,.wh .old{color:var(--color-err)}.wh .WARN,.wh .warn{color:var(--color-warn)}.wh .OK,.wh .ok{color:var(--color-ok)}.wh .INFO,.wh .UNKNOWN,.wh .unknown{color:var(--text-dim)}
.wh-calendar th{white-space:nowrap;text-align:center}.wh-calendar th:first-child{text-align:left;position:sticky;left:0;z-index:2}
.wh-calendar td{padding:4px}.wh-calendar td:first-child{min-width:150px;position:sticky;left:0;background:var(--bg-surface)}
.wh-calendar button{min-width:76px;width:100%;padding:9px 5px;line-height:1.6;border-radius:var(--r-s);font-variant-numeric:tabular-nums}
.wh-calendar button.ok{color:var(--color-ok);border-color:var(--color-ok)}.wh-calendar button.gap{color:var(--color-warn);border-color:var(--color-warn)}
.wh-calendar button.empty{color:var(--text-dim);border-style:dashed}.wh-calendar small{display:block;font-size:10px}
.wh-legend{display:flex;gap:var(--sp-4);flex-wrap:wrap;margin:var(--sp-3) 0}.wh-mini-list{display:grid;gap:var(--sp-3)}
.wh-mini-list button{text-align:left;display:block;width:100%;line-height:1.6}.wh-mini-list small{display:block}
.wh-task-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:var(--sp-3)}
.wh-task{border:1px solid var(--border);border-radius:var(--r-s);padding:var(--sp-4)}.wh-task h3{font-size:var(--fs-l);margin:0 0 8px}
.wh-task-controls{display:flex;align-items:center;gap:var(--sp-3);flex-wrap:wrap}.wh pre{background:var(--bg-page);border:1px solid var(--border-weak);padding:var(--sp-3);border-radius:var(--r-s);white-space:pre-wrap;overflow-wrap:anywhere;max-height:300px;overflow:auto;font-size:var(--fs-s)}
.wh dialog{background:var(--bg-surface);color:var(--text);border:1px solid var(--border);border-radius:var(--r-m);width:min(940px,94vw);max-height:88vh;padding:var(--sp-5)}
.wh dialog::backdrop{background:var(--bg-page);opacity:.82}.wh-dialog-head{display:flex;align-items:flex-start;justify-content:space-between;gap:var(--sp-4)}
.wh textarea{width:100%;min-height:280px;resize:vertical;background:var(--bg-page);color:var(--text);border:1px solid var(--border);padding:var(--sp-3);font-size:var(--fs-s)}
.wh-evidence-block{border-top:1px solid var(--border);padding:var(--sp-3) 0}.wh-key-list{line-height:1.8;color:var(--text);max-height:220px;overflow:auto;overflow-wrap:anywhere}
.wh-footer{color:var(--text-dim);font-size:var(--fs-xs);line-height:1.8;margin:var(--sp-5) 0}
.wh-daily-tools label{display:grid;gap:var(--sp-2)}.wh-daily-tools input{min-width:0}.wh-daily-tools{align-items:end}
.wh-daily-table td{max-width:34ch;overflow-wrap:anywhere}.wh progress{width:100%;height:12px;accent-color:var(--link)}
.wh-receipts article{border-top:1px solid var(--border);padding:var(--sp-3) 0}.wh-receipts p{font-size:var(--fs-s)}
.wh-daily-table button{margin:0 var(--sp-2) var(--sp-2) 0}.wh-daily-table button:disabled{cursor:not-allowed}
@media(max-width:800px){.wh-header{display:block}.wh-header-right{text-align:left;margin-top:var(--sp-3)}.wh-split,.wh-task-grid{grid-template-columns:1fr}.wh-section{padding:var(--sp-4)}.wh h1{font-size:24px}.wh-status{display:block}.wh-tabs button{flex:1;white-space:nowrap}}
@media(max-width:700px){.wh-daily-table thead{display:none}.wh-daily-table,.wh-daily-table tbody,.wh-daily-table tr,.wh-daily-table td{display:block;width:100%;max-width:none}.wh-daily-table tr{border-bottom:1px solid var(--border);padding:var(--sp-3) 0}.wh-daily-table td{border:0;padding:5px 0}.wh-daily-table td:before{content:attr(data-label);color:var(--text-dim);display:block;font-size:var(--fs-xs)}.wh-daily-tools label{flex:1;min-width:130px}}
</style>
"""


def render_dashboard(
    data, state, *, freshness_html, graph_html, nav, base_css, preview_js
):
    """SSR 首屏可读，浏览器仅筛选本次快照，接口沿用原入口。"""
    model = build_model(data, state)
    model["daily_read_only"] = bool(state.get("preview") or data.get("daily_read_only"))
    model["daily_snapshot"] = data.get("daily_snapshot")
    e = lambda value: html.escape(str(value if value is not None else "—"), quote=True)
    fmt = lambda value: format(value, ",") if _number(value) else "—"
    counts, kpi = model["counts"], model["kpi"]
    status_title = {
        "unknown": "尚无完整核验结论",
        "fail": "数据仍有待处理缺口",
        "warn": "有项目需要核实",
        "ok": "本次核验项目全部通过",
    }[model["health_status"]]
    notices = []
    if model["notice"]:
        notices.append(
            '<div class="wh-note" role="status">%s</div>' % e(model["notice"])
        )
    if model["state"] in ("no_data", "not_found") and model["state_reason"]:
        notices.append(
            '<div class="wh-note" role="status">%s：%s</div>'
            % (e(model["state"]), e(model["state_reason"]))
        )
    notice = "".join(notices)
    stats = "".join(
        '<div class="wh-stat %s"><b>%s</b>%s项</div>' % (st, counts[st], _STATUS[st])
        for st in ("FAIL", "WARN", "OK", "INFO", "UNKNOWN")
        if counts[st] or st in ("FAIL", "WARN", "OK")
    )
    stats += (
        '<div class="wh-stat"><b>%s</b>资产表</div><div class="wh-stat"><b>%s</b>MB 存储</div>'
        % (
            fmt(kpi.get("n_tables")),
            fmt(round(kpi["total_mb"])) if _number(kpi.get("total_mb")) else "—",
        )
    )
    stats += (
        '<div class="wh-stat"><b id="wh-running-count">待查询</b>手动运行任务</div>'
    )
    fresh_counts = model["freshness"].get("counts") or {}
    if fresh_counts:
        freshness_html = (
            '<div class="wh-section"><h2>独立新鲜度结果</h2><p>新鲜度：%s</p><p class="muted">完整度与新鲜度分别计数；12表仓库监控与15表读层策略口径不同，未统一市场及发布时间资格。</p></div>'
            % e(
                " · ".join(
                    "%s %s" % (s, fresh_counts.get(s, 0))
                    for s in ("WARN", "STALE", "FAIL", "SNAP", "NA", "UNKNOWN", "OK")
                )
            )
            + freshness_html
        )
    monitor = model["monitoring"]
    if monitor:
        nine = monitor.get("nine_dim") or {}
        qa = monitor.get("graph_qa") or {}
        graph_counts = monitor.get("graph_counts") or {}
        factor_text = " · ".join(
            "%s 不可判 %s，未完整 %s"
            % (
                f.get("name", name),
                f.get("unjudgeable", "待核验"),
                f.get("incomplete", "待核验"),
            )
            for name, f in (nine.get("factors") or {}).items()
        )
        graph_html = (
            '<div class="wh-section"><h2>九维与图谱回执</h2><p>九维 %s · 完整评分 %s / %s · 评分时点 %s · 业务日期 %s</p>'
            '<p>%s</p><p class="muted">%s</p><p>图谱 QA %s · 回执时点 %s · 实时连接未核验</p>'
            '<p class="muted">图谱数量快照 %s · %s；数量不代表 QA 通过。</p>'
            "<details><summary>完整监控证据</summary><pre>%s</pre></details></div>"
        ) % (
            e(nine.get("status", "UNKNOWN")),
            e(nine.get("n_full")),
            e(nine.get("n_candidates")),
            e(nine.get("generated_at")),
            e(nine.get("trade_date")),
            e(factor_text),
            e(nine.get("source_note", "来源资格待核验")),
            e(qa.get("status", "UNKNOWN")),
            e(qa.get("generated_at")),
            e(graph_counts.get("status", "UNKNOWN")),
            e(graph_counts.get("generated_at")),
            e(json.dumps(monitor, ensure_ascii=False, indent=2)),
        ) + graph_html
    metric_rows = []
    for index, metric in enumerate(model["metrics"]):
        metric_rows.append(
            '<tr data-metric="%d" data-status="%s"><td><span class="wh-badge %s">%s</span></td>'
            '<td>%s<small>%s</small></td><td class="num">%s <small>%s</small></td>'
            '<td>%s</td><td><button data-detail="%d">查看明细</button></td></tr>'
            % (
                index,
                metric["status"],
                metric["status"],
                metric["status_label"],
                e(metric["label"]),
                e(metric["name"]),
                fmt(metric["value"]),
                e(metric["unit"]),
                e(metric["policy"] or "见本次核验证据"),
                index,
            )
        )
    dates = sorted({date for row in model["coverage"] for date in row["days"]})

    def calendar(selected_dates):
        if not model["coverage"] or not selected_dates:
            return '<p class="muted">本次快照没有按日覆盖结果，请完成核验后刷新。</p>'
        headers = "".join(
            '<th scope="col">%s<br><small>%s</small></th>'
            % (date[4:6] + "/" + date[6:], date[:4])
            for date in selected_dates
        )
        rows = []
        for index, row in enumerate(model["coverage"]):
            cells = []
            for date in selected_dates:
                day = row["days"].get(date)
                if day is None:
                    cells.append('<td><span class="muted">无结果</span></td>')
                    continue
                if day.get("source_empty_pending"):
                    label = "待来源核验"
                elif day["status"] == "gap":
                    label = (
                        "缺 %s" if day["gap_type"] == "keys" else "参考差 %s"
                    ) % fmt(day["missing_count"])
                elif day["status"] == "ok":
                    label = (
                        "资格后无缺键"
                        if day["qualified"]
                        else "无缺键"
                        if day["gap_type"] == "keys"
                        else "数量一致"
                    )
                else:
                    label = "待核验"
                accessible = row["label"] + " " + date + " " + label
                cells.append(
                    '<td><button class="%s" data-coverage="%d" data-date="%s" aria-label="%s">%s<small>%s / %s</small></button></td>'
                    % (
                        day["status"],
                        index,
                        date,
                        e(accessible),
                        e(label),
                        fmt(day["actual"]),
                        fmt(day["reference"]),
                    )
                )
            rows.append("<tr><td>%s</td>%s</tr>" % (e(row["label"]), "".join(cells)))
        return (
            '<div class="wh-table-scroll"><table class="wh-calendar"><thead><tr><th scope="col">数据 / 交易日</th>%s</tr></thead><tbody>%s</tbody></table></div>'
            % (headers, "".join(rows))
        )

    problems = [
        (i, m)
        for i, m in enumerate(model["metrics"])
        if m["status"] in ("FAIL", "WARN", "UNKNOWN")
    ]
    priority = "".join(
        '<button data-detail="%d"><span class="%s">%s</span> %s<small>%s %s</small></button>'
        % (
            i,
            m["status"],
            m["status_label"],
            e(m["label"]),
            fmt(m["value"]),
            e(m["unit"]),
        )
        for i, m in problems[:6]
    )
    priority = priority or '<p class="muted">%s</p>' % (
        "当前没有异常指标。" if model["metrics"] else "尚无完整核验结果。"
    )
    asset_rows = []
    freshness_labels = {
        "unknown": "文件时点未知",
        "ok": "文件 ≤26小时",
        "warn": "文件 26–72小时",
        "old": "文件 >72小时",
    }
    for index, row in enumerate(model["assets"]):
        size = "%.1f MB" % row["size_mb"] if _number(row.get("size_mb")) else "未计量"
        time_label = {
            "file_mtime": "文件修改时间",
            "business_date": "业务日期",
            "event_time": "事件／披露时间",
        }.get(row.get("latest_kind"), "时点类型待核验")
        time_text = e(row.get("latest")) + "<small>" + e(time_label) + "</small>"
        if row.get("business_date"):
            time_text += "<small>业务日期：%s</small>" % e(row["business_date"])
        if row.get("measured_at"):
            time_text += "<small>计量／核验时间：%s</small>" % e(row["measured_at"])
        if row.get("source_times"):
            time_text += (
                "<details><summary>逐来源事件与入库时间</summary><pre>%s</pre></details>"
                % e(json.dumps(row["source_times"], ensure_ascii=False, indent=2))
            )
        asset = e(row.get("asset"))
        url = row.get("url") or ""
        if url.startswith("/") and not url.startswith("//"):
            asset = '<a href="%s">%s</a>' % (e(url), asset)
        asset_rows.append(
            '<tr data-asset="%d"><td>%s<small>%s</small></td><td class="num">%s</td><td>%s</td><td class="num">%s</td><td><span class="%s">%s</span><small>%s</small></td></tr>'
            % (
                index,
                asset,
                e(row["group"]),
                e(row.get("rows") if row.get("rows") is not None else "未计量"),
                time_text,
                size,
                row["freshness"],
                freshness_labels[row["freshness"]],
                e(row.get("extra") or ""),
            )
        )
    groups = sorted({row["group"] for row in model["assets"]})
    options = "".join(
        '<option value="%s">%s</option>' % (e(group), e(group)) for group in groups
    )
    group_notes = "".join(
        '<p class="muted">%s：%s</p>' % (e(g["name"]), e(g["note"]))
        for g in model["group_notes"]
    )
    if kpi.get("capacity_scope"):
        group_notes += '<p class="muted">总容量范围：%s；计量时点：%s</p>' % (
            e(kpi["capacity_scope"]),
            e(kpi.get("capacity_measured_at")),
        )
    if model["manifest_receipts"]:
        group_notes += (
            "<details><summary>独立manifest回执（不计物理资产）</summary>%s</details>"
            % "".join(
                "<p>%s · 登记行数 %s · %s</p>"
                % (e(r.get("asset")), e(r.get("rows")), e(r.get("extra")))
                for r in model["manifest_receipts"]
            )
        )
    tasks = []
    for src, title, desc in (
        ("tdx", "TDX 行情增量", "进入每日更新，先确认日期、表范围与来源资格"),
        ("tushare", "Tushare 增量", "进入每日更新，与定时范围一致地预览实际任务"),
        ("kg", "图谱四步链", "更新现有图谱处理链"),
        ("catalog", "目录重建校对", "重建现有数据资产目录"),
    ):
        tasks.append(
            '<article class="wh-task"><h3>%s</h3><p class="muted">%s</p><div class="wh-task-controls">'
            '<button data-sync="%s" data-label="%s">%s</button><button data-job="%s">查询状态</button>'
            '<span id="wh-job-%s" role="status">状态未查询</span></div><details><summary>查看任务日志</summary>'
            '<pre id="wh-log-%s">尚未取得日志。</pre></details></article>'
            % (
                title,
                desc,
                src,
                title,
                "进入每日更新" if src in ("tdx", "tushare") else "运行更新",
                src,
                src,
                src,
            )
        )
    # JSON 不是 HTML：阻止不可信字段提前关闭 script 标签。
    payload = (
        json.dumps(model, ensure_ascii=False, allow_nan=False)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
    return (
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>数据仓管理</title>"
        + base_css
        + _CSS
        + "</head><body>"
        + nav
        + '<main class="wh">'
        '<header class="wh-header"><div><h1>数据仓 · 全部资产</h1><p class="muted">看清覆盖、定位缺口、核对查询，再安排更新。</p></div>'
        '<div class="wh-header-right"><div>仓库扫描：%s</div><div>数据时点：%s · 状态：%s（%s）</div><small>完整度核验：%s</small><br><button id="wh-refresh">刷新核验快照</button></div></header>%s'
        '<nav class="wh-tabs" role="tablist" aria-label="数据仓视图"><button id="wh-tab-overview" role="tab" aria-controls="wh-overview" data-view="overview" aria-selected="true">总览</button>'
        '<button id="wh-tab-coverage" role="tab" aria-controls="wh-coverage" data-view="coverage" aria-selected="false">覆盖日历</button>'
        '<button id="wh-tab-gaps" role="tab" aria-controls="wh-gaps" data-view="gaps" aria-selected="false">缺口管理</button>'
        '<button id="wh-tab-assets" role="tab" aria-controls="wh-assets" data-view="assets" aria-selected="false">资产与任务</button>'
        '<button id="wh-tab-daily" role="tab" aria-controls="wh-daily" data-view="daily" aria-selected="false">每日更新</button></nav>'
        '<section id="wh-overview" role="tabpanel" aria-labelledby="wh-tab-overview"><div class="wh-section wh-status %s"><div><strong>%s</strong>'
        '<p class="muted">异常数按核验项目计；码日缺键、空字段、查询行数分别展示。</p><div class="wh-stats">%s</div></div></div>'
        '<div class="wh-split"><div class="wh-section"><h2>近期交易日覆盖</h2><p class="muted">格内为实际唯一代码 / 参考代码，点击日期查看证据。</p>%s'
        '<div class="wh-tools"><button data-view="coverage">查看完整覆盖日历</button></div></div><div class="wh-section"><h2>优先核对</h2><div class="wh-mini-list">%s</div>'
        '<div class="wh-tools"><button data-view="gaps">查看全部核验项目</button></div></div></div>%s%s</section>'
        '<section id="wh-coverage" role="tabpanel" aria-labelledby="wh-tab-coverage"><div class="wh-section"><h2>按交易日唯一代码覆盖</h2>'
        '<p class="muted">以本次报告的参考集合为准。参考差表示数量差，需结合停牌与上市资格核对；已提供逐码列表的显示缺键。参考基准不可用时显示待核验。</p>'
        '<div class="wh-legend"><span class="ok">无缺键</span><span class="warn">存在缺键</span><span class="unknown">待核验 / 无结果</span></div>%s'
        '<div class="wh-tools"><button id="wh-export-coverage">导出覆盖证据 JSON</button></div></div></section>'
        '<section id="wh-gaps" role="tabpanel" aria-labelledby="wh-tab-gaps"><div class="wh-section"><h2>缺口与实际查询核验</h2>'
        '<p class="muted">同一缺口可能同时触发覆盖与跨表对账，不能累加成缺键总数。来源资格以明细证据为准。</p>'
        '<div class="wh-tools"><input id="wh-gap-search" aria-label="搜索核验项目" placeholder="搜索中文问题或指标名">'
        '<select id="wh-gap-status" aria-label="筛选核验状态"><option value="problems">异常与待核实</option><option value="all">全部项目</option>'
        '<option value="FAIL">异常</option><option value="WARN">待核实</option><option value="OK">通过</option><option value="INFO">参考</option><option value="UNKNOWN">未知</option></select>'
        '<button id="wh-export-gaps">导出筛选明细 JSON</button><button id="wh-export-csv">导出项目 CSV</button></div>'
        '<p id="wh-gap-count" class="muted" role="status"></p><div class="wh-table-scroll"><table><thead><tr><th>核验状态</th><th>核验项目</th><th>指标值 / 单位</th><th>统计口径</th><th>证据</th></tr></thead>'
        '<tbody id="wh-gap-rows">%s</tbody></table></div><p id="wh-gap-empty" class="muted" hidden>没有匹配项目，请更换关键词或状态。</p>'
        "<details><summary>仓库扫描原始提示</summary><pre>%s</pre></details></div></section>"
        '<section id="wh-assets" role="tabpanel" aria-labelledby="wh-tab-assets"><div class="wh-section"><h2>数据资产</h2>'
        '<p class="muted">文件修改时间、业务日期与计量／核验时间分开展示。manifest状态不推定正在运行；完整度请查看核验项目。逐码文件规模沿用目录统计口径。</p>'
        '<div class="wh-tools"><input id="wh-asset-search" aria-label="搜索数据资产" placeholder="搜索表名、分组或状态说明">'
        '<select id="wh-asset-group" aria-label="筛选资产分组"><option value="all">全部分组</option>%s</select></div>%s<p id="wh-asset-count" class="muted" role="status"></p>'
        '<div class="wh-table-scroll"><table><thead><tr><th>资产 / 分组</th><th>目录规模</th><th>数据或文件最新时点</th><th>存储大小</th><th>文件新鲜度 / 原始说明</th></tr></thead><tbody id="wh-asset-rows">%s</tbody></table></div>'
        '<p id="wh-asset-empty" class="muted" hidden>没有匹配资产，请更换关键词或分组。</p></div><div class="wh-section"><h2>更新任务</h2>'
        '<p class="muted">TDX／Tushare 先预览每日范围；图谱与目录沿用原入口。任务退出成功后仍需重新核验数据完整度。</p><div class="wh-task-grid">%s</div></div></section>'
        "%s"
        '<dialog id="wh-dialog" aria-labelledby="wh-detail-title"><div class="wh-dialog-head"><div><h2 id="wh-detail-title">核验明细</h2><p id="wh-detail-policy" class="muted"></p></div>'
        '<button id="wh-detail-close" aria-label="关闭明细">关闭</button></div><div id="wh-detail-body"></div><div class="wh-tools"><button id="wh-export-detail">导出完整证据 JSON</button></div></dialog>'
        '<dialog id="wh-export-dialog" aria-labelledby="wh-export-title"><div class="wh-dialog-head"><h2 id="wh-export-title">导出结果</h2><button id="wh-export-close" aria-label="关闭导出">关闭</button></div>'
        '<p class="muted">已生成完整导出内容，文件保存由浏览器处理。若下载受限，可全选下方内容后复制保存。</p><textarea id="wh-export-text" aria-label="完整导出内容" readonly></textarea>'
        '<div class="wh-tools"><button id="wh-export-select">全选导出内容</button></div></dialog>'
        '<dialog id="wh-sync-dialog" aria-labelledby="wh-sync-title"><h2 id="wh-sync-title">运行更新任务</h2><p id="wh-sync-description"></p>'
        '<p class="muted">沿用现有同步入口，单实例执行。任务结束后仍需重新核验数据完整度。</p>'
        '<div class="wh-tools"><button id="wh-sync-confirm">确认运行</button><button id="wh-sync-cancel">取消</button></div></dialog>'
        '<footer class="wh-footer">扫描消费后台快照，刷新可能先显示上次结果。仓库快照缓存 60 秒，完整度扫描缓存约 600 秒。完整度、文件新鲜度与任务进度分别核对。'
        '<br>实际样本沿用时点校对表的 🔍 入口，仅支持已登记的查询表。<a href="/col-audit">查看列对账报告</a></footer>'
        '<script type="application/json" id="warehouse-data">%s</script>'
        % (
            e(model["generated_at"]),
            e(model["data_date"] or "—"),
            e(model["state"]),
            e(model["state_label"]),
            e(model["integrity_at"]),
            notice,
            model["health_status"],
            status_title,
            stats,
            calendar(dates[-8:]),
            priority,
            freshness_html,
            graph_html,
            calendar(dates),
            "".join(metric_rows),
            e("\n".join(map(str, model["other_gaps"])) or "暂无原始提示"),
            options,
            group_notes,
            "".join(asset_rows),
            "".join(tasks),
            _DAILY_HTML.replace(
                "进入此视图后读取每日目标。",
                "非实时快照，启动已禁用。"
                if model["daily_read_only"]
                else "进入此视图后读取每日目标。",
            ),
            payload,
        )
        + preview_js
        + _JS
        + "</main></body></html>"
    )


_JS = r"""
<script>
(() => {
  'use strict';
  const data = JSON.parse(document.getElementById('warehouse-data').textContent);
  const el = id => document.getElementById(id);
  const tabs = [...document.querySelectorAll('.wh-tabs [data-view]')];
  function view(name, focus = false) {
    if (!tabs.some(t => t.dataset.view === name)) name = 'overview';
    tabs.forEach(t => { const selected = t.dataset.view === name; t.setAttribute('aria-selected', selected); t.tabIndex = selected ? 0 : -1; el('wh-' + t.dataset.view).hidden = !selected; });
    history.replaceState(null, '', '#'+name);
    if (focus) el('wh-tab-'+name).focus();
    if (name === 'daily') enterDaily();
  }
  document.querySelectorAll('.wh [data-view]').forEach(b => b.addEventListener('click', () => view(b.dataset.view)));
  tabs.forEach((tab, i) => tab.addEventListener('keydown', event => {
    let target;
    if (event.key === 'ArrowRight') target = (i + 1) % tabs.length;
    if (event.key === 'ArrowLeft') target = (i + tabs.length - 1) % tabs.length;
    if (event.key === 'Home') target = 0;
    if (event.key === 'End') target = tabs.length - 1;
    if (target !== undefined) { event.preventDefault(); view(tabs[target].dataset.view, true); }
  }));
  el('wh-refresh').addEventListener('click', () => location.reload());
  const jsonExport = (name, value) => download(name, JSON.stringify(value, null, 2), 'application/json;charset=utf-8');
  function download(name, text, mime) {
    const url = URL.createObjectURL(new Blob([text], {type: mime}));
    const a = document.createElement('a'); a.href = url; a.download = name; a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    el('wh-export-title').textContent = name;
    el('wh-export-text').value = text;
    if (!el('wh-export-dialog').open) el('wh-export-dialog').showModal();
  }
  el('wh-export-close').addEventListener('click', () => el('wh-export-dialog').close());
  el('wh-export-select').addEventListener('click', () => { el('wh-export-text').focus(); el('wh-export-text').select(); });
  function filteredMetrics() {
    const search = el('wh-gap-search').value.trim().toLowerCase(), status = el('wh-gap-status').value;
    return data.metrics.map((m,i) => ({m,i})).filter(({m}) => {
      const accepted = status === 'all' || (status === 'problems' ? ['FAIL','WARN','UNKNOWN'].includes(m.status) : m.status === status);
      return accepted && (m.name+' '+m.label+' '+m.policy).toLowerCase().includes(search);
    });
  }
  function filterGaps() {
    const selected = new Set(filteredMetrics().map(x => x.i));
    document.querySelectorAll('[data-metric]').forEach(r => r.hidden = !selected.has(Number(r.dataset.metric)));
    el('wh-gap-count').textContent = '显示 '+selected.size+' / '+data.metrics.length+' 个核验项目';
    el('wh-gap-empty').hidden = selected.size > 0;
  }
  ['wh-gap-search','wh-gap-status'].forEach(id => el(id).addEventListener('input', filterGaps)); filterGaps();
  function filterAssets() {
    const search = el('wh-asset-search').value.trim().toLowerCase(), group = el('wh-asset-group').value;
    let count = 0;
    document.querySelectorAll('[data-asset]').forEach(r => {
      const a = data.assets[Number(r.dataset.asset)];
      const selected = (group === 'all' || a.group === group) && (a.asset+' '+a.group+' '+(a.extra||'')).toLowerCase().includes(search);
      r.hidden = !selected; if (selected) count++;
    });
    el('wh-asset-count').textContent = '显示 '+count+' / '+data.assets.length+' 项资产'; el('wh-asset-empty').hidden = count > 0;
  }
  ['wh-asset-search','wh-asset-group'].forEach(id => el(id).addEventListener('input', filterAssets)); filterAssets();
  const envelope = value => ({generated_at:data.generated_at, integrity_at:data.integrity_at, stale:data.stale, expected_latest:data.expected_latest, data:value});
  el('wh-export-gaps').addEventListener('click', () => jsonExport('数据仓核验明细.json', envelope(filteredMetrics().map(x => x.m))));
  el('wh-export-coverage').addEventListener('click', () => jsonExport('数据仓覆盖证据.json', envelope(data.coverage)));
  el('wh-export-csv').addEventListener('click', () => {
    const cell = value => { let s = String(value ?? ''); if (/^[=+\-@\t\r]/.test(s)) s = "'"+s; return '"'+s.replaceAll('"','""')+'"'; };
    const rows = [['指标','说明','状态','指标值','单位','统计口径','核验时间']].concat(filteredMetrics().map(({m}) => [m.name,m.label,m.status_label,m.value,m.unit,m.policy,data.integrity_at]));
    download('数据仓核验项目.csv','\uFEFF'+rows.map(r => r.map(cell).join(',')).join('\r\n'),'text/csv;charset=utf-8');
  });
  const labels = {name:'指标名',label:'中文说明',unit:'单位',status:'原始状态',status_label:'核验结论',value:'指标值',details:'完整证据',date:'交易日',reference_policy:'参考规则',reference:'参考唯一代码',gap_type:'差异类别',qualified:'是否已作资格排除',rows:'记录数',unique_codes:'实际唯一代码',reference_unique_codes:'参考唯一代码',missing_count:'缺键数',missing:'缺少代码',extra:'多出代码',actual:'实际数量',defined:'定义数量',nulls:'关键列空值',missing_columns:'缺少列',invalid_date_keys:'无效日期键',missing_days:'缺失交易日',expected_days:'期望交易日',actual_days:'实际交易日',expected_latest:'期望最新交易日',first:'首个交易日',last:'末个交易日',equal:'两种代码查询一致',six_digit_rows:'六位代码返回行',error:'错误',value_unit:'指标口径',policy:'参考规则',reference_date:'参考日期',reference_source:'参考来源',eligible_suspended:'已证全天停牌',missing_expected:'未解释缺行情',observed:'实际有行情股票',listed:'上市集合数量',vol:'成交量',amount:'成交额'};
  function evidence(value, depth=0) {
    const block = document.createElement('div');
    if (Array.isArray(value)) { block.className = 'wh-key-list'; block.textContent = value.length ? value.map(v => typeof v === 'object' ? JSON.stringify(v) : String(v)).join('，') : '空集合'; return block; }
    if (value && typeof value === 'object' && depth < 4) {
      for (const [key, item] of Object.entries(value)) {
        const part = document.createElement('div'); part.className = 'wh-evidence-block';
        const title = document.createElement('strong'); title.textContent = labels[key] || key; part.appendChild(title);
        part.appendChild(evidence(item, depth+1)); block.appendChild(part);
      }
      if (!Object.keys(value).length) block.textContent = '没有附加明细。';
    } else block.textContent = value === null || value === undefined ? '未提供' : typeof value === 'object' ? JSON.stringify(value) : String(value);
    return block;
  }
  let detailPayload = null;
  function detail(title, policy, value) {
    detailPayload = value; el('wh-detail-title').textContent = title; el('wh-detail-policy').textContent = policy;
    el('wh-detail-body').replaceChildren(evidence(value)); el('wh-dialog').showModal();
  }
  document.querySelectorAll('[data-detail]').forEach(b => b.addEventListener('click', () => {
    const m = data.metrics[Number(b.dataset.detail)]; detail(m.label, m.status_label+'；指标 '+(m.value ?? '未知')+' '+m.unit+'。'+(m.policy||''), m);
  }));
  document.querySelectorAll('[data-coverage]').forEach(b => b.addEventListener('click', () => {
    const row = data.coverage[Number(b.dataset.coverage)], day = row.days[b.dataset.date];
    detail(row.label+' '+b.dataset.date, row.policy+'。实有 / 参考不等同于官方全市场覆盖率。', {date:b.dataset.date,...day,reference_policy:row.summary});
  }));
  el('wh-detail-close').addEventListener('click', () => el('wh-dialog').close());
  el('wh-export-detail').addEventListener('click', () => jsonExport('数据仓完整证据.json', envelope(detailPayload)));
  let confirmResolve = null;
  function confirmSync(label) {
    el('wh-sync-description').textContent = '将运行 '+label+'。';
    el('wh-sync-dialog').showModal();
    return new Promise(resolve => { confirmResolve=resolve; });
  }
  function finishConfirm(accepted) {
    const resolve=confirmResolve; confirmResolve=null;
    el('wh-sync-dialog').close(); if (resolve) resolve(accepted);
  }
  el('wh-sync-confirm').addEventListener('click', () => finishConfirm(true));
  el('wh-sync-cancel').addEventListener('click', () => finishConfirm(false));
  el('wh-sync-dialog').addEventListener('cancel', () => finishConfirm(false));
  el('wh-sync-dialog').addEventListener('close', () => { if(confirmResolve) finishConfirm(false); });
  const jobs = new Map();
  let disposed=false;
  async function request(url, options={}, acceptRunError=false) {
    const controller = new AbortController(), timeout = setTimeout(() => controller.abort(),12000);
    try {
      const response = await fetch(url, {signal:controller.signal,cache:'no-store',...options});
      const result = await response.json();
      if (!result || typeof result !== 'object') throw new Error('任务接口返回无效结果');
      if (!response.ok && !(acceptRunError && /^[a-f0-9]{32}$/.test(result.run_id || ''))) throw new Error('请求失败（HTTP '+response.status+'）');
      if (result.error && !acceptRunError) throw new Error(String(result.error));
      return result;
    } finally { clearTimeout(timeout); }
  }
  function jobStatus(src, message, state='unknown') { const node=el('wh-job-'+src); node.textContent=message; node.className=state; }
  function syncDisabled(src, disabled) { document.querySelector('[data-sync="'+src+'"]').disabled=disabled; }
  function runningCount() {
    const known = [...jobs.values()].filter(j => typeof j.running === 'boolean');
    const count = known.filter(j => j.running).length;
    el('wh-running-count').textContent = known.length === 4 ? String(count) : count+'（部分状态未知）';
  }
  function ensureJob(src) {
    if (!jobs.has(src)) jobs.set(src,{busy:false,timer:null,rounds:0,version:0,starting:false,wait:null});
    return jobs.get(src);
  }
  async function poll(src) {
    if(disposed)return;
    if(data.daily_read_only){jobStatus(src,'非实时快照，运行状态待核验');return;}
    const job = ensureJob(src);
    if (job.busy) return job.wait;
    if (job.starting) return;
    clearTimeout(job.timer); job.busy=true;
    const version=job.version;
    let release;
    job.wait=new Promise(resolve => { release=resolve; });
    try {
      const status=await request('/api/data/job?src='+encodeURIComponent(src));
      if (version !== job.version) return;
      if (typeof status.running !== 'boolean') throw new Error('任务状态缺少运行标记');
      job.running=status.running; runningCount();
      if (status.tail) el('wh-log-'+src).textContent=String(status.tail);
      if (status.running) {
        jobStatus(src,'运行中','warn'); syncDisabled(src,true); job.rounds++;
        if (job.rounds < 450) job.timer=setTimeout(() => poll(src),4000);
        else jobStatus(src,'自动查询已暂停，可点击查询状态继续','warn');
      } else {
        syncDisabled(src,false); job.rounds=0;
        if (status.exit === 0) jobStatus(src,'更新已结束，完整度仍需重新核验','ok');
        else if (status.exit === null || status.exit === undefined) jobStatus(src,'本进程没有已登记任务');
        else jobStatus(src,'任务未成功结束，请查看日志','old');
        if (status.clear_err) jobStatus(src,'任务结束，但缓存刷新失败；请查看日志','warn');
      }
    } catch (error) { if (version !== job.version) return; job.running=null; runningCount(); jobStatus(src,(error.name==='AbortError'?'查询超时':error.message)+'，可点击查询状态重试','old'); syncDisabled(src,false); }
    finally { job.busy=false; job.wait=null; release(); }
  }
  const daily = {loaded:false,dateVersion:0,dailyRequestVersion:0,requestedDate:null,selectionVersion:0,planVersion:0,runVersion:0,pollVersion:0,
    model:null,plan:null,starting:false,planning:false,run:null,events:new Map(),receipts:new Map(),seq:0,timer:null,finished:false};
  const terminal = new Set(['succeeded','partial','failed','interrupted','blocked']);
  const stateLabels = {up_to_date:'已更新',not_updated:'未更新',partial:'部分更新',waiting_release:'等待发布',unknown:'待核验',
    queued:'已登记，等待执行',running:'执行中',verifying:'目标核验中',succeeded:'执行结束',failed:'执行失败',interrupted:'执行中断',blocked:'执行受阻'};
  const kindLabels = {authorized_data:'来源返回数据',authorized_empty:'来源返回空',connection_error:'连接失败',permission_denied:'未获授权',rate_limited:'请求限流',parse_error:'响应解析失败',unknown:'来源结果待核验'};
  const operationLabels = {paged_incremental:'分页增量',paged_full:'分页全量',per_code_incremental:'逐码增量',full_replace:'整表覆盖',recent_replace:'近期覆盖',date_replace:'按日覆盖',unsafe_window_replace:'窗口替换尚未接管'};
  const reasonLabels = {verification_unknown:'核验证据不足',qualification_unknown:'来源资格待核验',historical_unsupported:'历史日期尚未接管',waiting_release:'尚待发布',unsafe_window_replace:'窗口替换尚未接管',target_qualification_unknown:'目标资格待核验',verification_qualification_unknown:'核验规则待确认',frozen_executor_unavailable:'冻结执行范围尚未接管'};
  const measured = value => typeof value==='number' && Number.isFinite(value) ? value.toLocaleString('zh-CN') : '未计量';
  const sourceOf = row => ['tdx','tushare'].includes(row.update_source || row.source) ? (row.update_source || row.source) :
    ['tdx','xtcloud'].includes(row.channel) ? 'tdx' : row.channel==='tushare' ? 'tushare' : null;
  function node(tag, text, cls) { const n=document.createElement(tag); if(text!==undefined)n.textContent=text; if(cls)n.className=cls; return n; }
  function dailyMessage(text) { el('wh-daily-message').textContent=text; }
  function volume(metrics={}) { return '新增 '+measured(metrics.new_rows)+'；修订 '+measured(metrics.revised_rows)+'；重复 '+measured(metrics.duplicate_rows)+'；写入 '+measured(metrics.bytes_written)+' 字节；净存储 '+measured(metrics.net_storage_bytes)+' 字节'; }
  function currentRows() {
    return (daily.model?.assets || []).filter(row => (!sourceOf(row) || sourceOf(row)===el('wh-daily-source').value) &&
      (el('wh-daily-market').value==='all' || !row.market || row.market===el('wh-daily-market').value));
  }
  function activeSource(source) { return (daily.model?.runs || []).some(run => run.plan?.source===source && !terminal.has(run.status)); }
  function controls() {
    const disabled=!!data.daily_read_only || daily.planning || daily.starting;
    el('wh-daily-preview').disabled=disabled; el('wh-daily-batch').disabled=disabled;
  }
  function cancelPlan() {
    daily.planVersion++; daily.plan=null; daily.planning=false;
    el('wh-daily-confirm').disabled=true;
    if(el('wh-daily-plan-dialog').open)el('wh-daily-plan-dialog').close();
    controls();
  }
  function selectionChanged() {
    daily.selectionVersion++; cancelPlan();
    if(el('wh-daily-source').value==='tdx' && !['all','cn'].includes(el('wh-daily-market').value))el('wh-daily-market').value='all';
    renderDaily();renderRun();
  }
  function renderDaily() {
    const rows=currentRows(), body=el('wh-daily-rows'); body.replaceChildren();
    const counts=new Map();
    for(const row of rows) {
      counts.set(row.data_status,(counts.get(row.data_status)||0)+1);
      const tr=node('tr'), tables=Array.isArray(row.physical_tables)?row.physical_tables:[];
      const cells=[node('td'),node('td'),node('td'),node('td'),node('td'),node('td')];
      ['数据 / 市场','提供方 / 渠道','应到时点 / 资格','数据状态','本次写入','操作'].forEach((label,i)=>cells[i].dataset.label=label);
      cells[0].append(node('strong',row.name || row.label || row.asset_id || tables.join('、')),node('small',(row.market || '市场待核验')+' / '+(row.frequency || '频率待核验')));
      cells[1].append(node('span','提供方：'+(row.provider || '待核验')),node('small','渠道：'+(row.channel || '待核验')));
      const targets=Array.isArray(row.targets)?row.targets:[];
      cells[2].append(node('span',targets.map(t=>t?.target || '待核验').join('、') || '待核验'),node('small',reasonLabels[row.reason] || row.reason || '按登记规则核验'));
      cells[3].append(node('span',stateLabels[row.data_status] || '待核验','wh-badge '+(row.data_status==='up_to_date'?'ok':row.data_status==='partial'?'warn':'unknown')));
      let metrics=row.metrics;
      if(!metrics && tables.length===1) {
        const latest=(daily.model?.runs || []).filter(run=>run.date===daily.model.date && (run.units || []).some(u=>u.plan?.table===tables[0])).sort((a,b)=>(b.created_at||0)-(a.created_at||0))[0];
        const results=(latest?.units || []).filter(u=>u.plan?.table===tables[0]).map(u=>u.result);
        if(results.length)metrics=Object.fromEntries(['new_rows','revised_rows','duplicate_rows','bytes_written','net_storage_bytes'].map(key=>[key,results.every(r=>r && typeof r[key]==='number')?results.reduce((total,r)=>total+r[key],0):null]));
      }
      cells[4].textContent=volume(metrics);
      const preview=node('button','预览范围'), update=node('button','立即更新');
      preview.disabled=!!data.daily_read_only || daily.planning || daily.starting || !tables.length;
      update.disabled=preview.disabled || row.update_enabled!==true || !sourceOf(row) || activeSource(sourceOf(row)) || daily.model?.date!==el('wh-daily-date').value;
      preview.addEventListener('click',()=>previewPlan(tables)); update.addEventListener('click',()=>previewPlan(tables));
      cells[5].append(preview,update); cells.forEach(cell=>tr.appendChild(cell));body.appendChild(tr);
    }
    el('wh-daily-empty').hidden=rows.length>0;
    el('wh-daily-summary').textContent='浏览日期 '+(daily.model?.date || el('wh-daily-date').value)+'；'+[...counts].map(([state,count])=>(stateLabels[state] || '待核验')+' '+count+' 项').join('；');
    const runs=el('wh-daily-runs'); runs.replaceChildren();
    (daily.model?.runs || []).filter(run=>run.plan?.source===el('wh-daily-source').value).forEach(run=>{
      if(!/^[a-f0-9]{32}$/.test(run.run_id || ''))return;
      const button=node('button',(stateLabels[run.status] || '状态待核验')+' '+run.run_id.slice(0,8));
      button.addEventListener('click',()=>openRun(run.run_id));runs.appendChild(button);
    });
    controls();
  }
  function enterDaily() {
    if(daily.loaded)return;
    daily.loaded=true;
    if(!el('wh-daily-date').value)el('wh-daily-date').value=new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
    if(data.daily_read_only) {
      daily.model=data.daily_snapshot || {date:el('wh-daily-date').value,assets:[],runs:[]};
      if(daily.model.date)el('wh-daily-date').value=daily.model.date;
      dailyMessage('非实时快照；启动已禁用，来源与任务只显示此快照。');renderDaily();return;
    }
    loadDaily();
  }
  async function loadDaily(preserveRun=false) {
    if(disposed)return;
    if(data.daily_read_only){dailyMessage('非实时快照；启动已禁用。');renderDaily();return;}
    const version=++daily.dailyRequestVersion,date=el('wh-daily-date').value;
    if(daily.requestedDate!==date){daily.requestedDate=date;daily.dateVersion++;}
    if(!preserveRun){daily.runVersion++;daily.run=null;daily.events.clear();daily.receipts.clear();daily.seq=0;clearTimeout(daily.timer);cancelPlan();renderRun();}
    dailyMessage('正在读取 '+date+' 的每日目标…');
    try {
      const value=await request('/api/data/daily?date='+encodeURIComponent(date));
      if(version!==daily.dailyRequestVersion || date!==el('wh-daily-date').value)return;
      if(value.date!==date || !Array.isArray(value.assets) || !Array.isArray(value.runs))throw new Error('每日接口日期或范围无效');
      daily.model=value;renderDaily();dailyMessage('当前日期 '+date+'；'+(value.execution_reason || '执行状态与目标核验分别展示。'));
      const active=value.runs.find(run=>run.plan?.source===el('wh-daily-source').value && !terminal.has(run.status));
      if(active && !daily.run && !daily.starting)openRun(active.run_id);
    } catch(error) {
      if(version!==daily.dailyRequestVersion)return;
      dailyMessage('每日读取失败，保留上次结果；请核对上次日期后重试。'+(error.name==='AbortError'?'请求超时。':error.message));
      renderDaily();
    }
  }
  async function previewPlan(tables=null, retryOf=null, retryUnits=[]) {
    if(disposed || data.daily_read_only || daily.planning || daily.starting)return;
    const context={date:el('wh-daily-date').value,source:el('wh-daily-source').value,market:el('wh-daily-market').value,
      dateVersion:daily.dateVersion,selectionVersion:daily.selectionVersion,planVersion:++daily.planVersion};
    daily.planning=true;renderDaily();dailyMessage('正在预览实际范围，不启动采集。');
    try {
      const value=await request('/api/data/update-plan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({source:context.source,market:context.market,date:context.date,selected_tables:tables,retry_of:retryOf,retry_units:retryUnits})});
      if(context.planVersion!==daily.planVersion || context.dateVersion!==daily.dateVersion || context.selectionVersion!==daily.selectionVersion)return;
      const plan=value.plan;
      if(!plan || plan.date!==context.date || plan.source!==context.source || plan.market!==context.market || !value.plan_id || !plan.plan_digest)throw new Error('预览范围与当前选择不一致');
      daily.plan={...value,context,requestId:crypto.randomUUID()};
      el('wh-daily-plan-summary').textContent=context.date+' / '+context.source.toUpperCase()+' / '+context.market+'；'+(plan.units || []).length+' 个计划单元。计划有效至 '+new Date(value.expires_at*1000).toLocaleString('zh-CN');
      const body=el('wh-daily-plan-body');body.replaceChildren();
      body.append(node('h3','目标与冻结表清单'),evidence({selected_tables:plan.selected_tables,targets:plan.targets,units:plan.units}));
      body.append(node('h3','实际操作与必要依赖'));
      for(const operation of plan.operations || [])body.append(node('p',(operation.table || '')+'：'+(operationLabels[operation.operation] || operation.operation || '操作待核验')),evidence({scope:operation.scope,dependencies:operation.dependencies || []}));
      const unsupported=plan.unsupported || [];
      if(unsupported.length)body.append(node('h3','未获执行资格'),evidence(unsupported.map(item=>typeof item==='object'?{...item,reason:reasonLabels[item.reason] || item.reason}:item)));
      const safe=!unsupported.length && (plan.units || []).length>0 && (plan.operations || []).length>0 && plan.operations.every(operation=>operation.executable===true);
      el('wh-daily-confirm').disabled=!safe;
      el('wh-daily-plan-message').textContent=safe?'确认后仅执行上述服务端冻结范围。':'此预览包含未接管或资格不明范围，不能启动。';
      el('wh-daily-plan-dialog').showModal();el(safe?'wh-daily-confirm':'wh-daily-cancel').focus();
    } catch(error) {
      if(context.planVersion===daily.planVersion)dailyMessage('预览失败，请重新核对日期与范围。'+error.message);
    } finally {
      if(context.planVersion===daily.planVersion){daily.planning=false;renderDaily();}
    }
  }
  async function startPlan() {
    const selected=daily.plan;
    if(disposed || !selected || daily.starting || data.daily_read_only || el('wh-daily-confirm').disabled)return;
    if(selected.context.dateVersion!==daily.dateVersion || selected.context.selectionVersion!==daily.selectionVersion || selected.context.date!==el('wh-daily-date').value){cancelPlan();return;}
    if(typeof selected.expires_at!=='number' || selected.expires_at<=Date.now()/1000){cancelPlan();dailyMessage('计划已过期，请重新预览。');return;}
    daily.starting=true;el('wh-daily-confirm').disabled=true;
    const runVersion=++daily.runVersion,dateVersion=daily.dateVersion,selectionVersion=daily.selectionVersion;
    el('wh-daily-plan-dialog').close();renderDaily();dailyMessage('提交已确认计划，等待任务登记回执。');
    try {
      const result=await request('/api/data/update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({plan_id:selected.plan_id,plan_digest:selected.plan.plan_digest,request_id:selected.requestId})},true);
      if(runVersion!==daily.runVersion || dateVersion!==daily.dateVersion || selectionVersion!==daily.selectionVersion)return;
      if(!/^[a-f0-9]{32}$/.test(result.run_id || ''))throw new Error(result.error || '未取得已登记任务标识');
      dailyMessage(result.started===true?'任务已登记；开始读取执行状态。':'任务已登记，启动状态待核实；请查看任务记录。');
      openRun(result.run_id);
    } catch(error) {
      if(runVersion!==daily.runVersion || dateVersion!==daily.dateVersion || selectionVersion!==daily.selectionVersion)return;
      dailyMessage('启动回执未确认，请先查看任务状态；重试保留原请求标识。'+error.message);
      daily.plan=selected;el('wh-daily-confirm').disabled=false;el('wh-daily-plan-dialog').showModal();
    } finally { daily.starting=false;renderDaily(); }
  }
  function renderRun() {
    const run=daily.run;
    if(!run){
      el('wh-daily-run-title').textContent='尚未选择任务';el('wh-daily-run-state').textContent='';
      el('wh-daily-progress').removeAttribute('value');el('wh-daily-progress-label').textContent='分母未确定';
      el('wh-daily-run-metrics').textContent=volume();el('wh-daily-receipts').replaceChildren();el('wh-daily-events').textContent='';
      el('wh-daily-retry').disabled=true;el('wh-daily-retry-note').textContent='尚未选择任务。';return;
    }
    el('wh-daily-run-title').textContent='任务 '+run.run_id+' / '+(run.plan?.source || '来源待核验')+' / '+(run.date || '日期待核验');
    const uncertain=!terminal.has(run.status) && (run.process_status!=='alive' || !run.pid || typeof run.heartbeat_at!=='number' || Date.now()/1000-run.heartbeat_at>90);
    el('wh-daily-run-state').textContent=(data.daily_read_only?'非实时快照；':'')+(uncertain?'执行运行状态待核实':stateLabels[run.status] || '执行状态待核验')+'；数据状态以每日目标核验为准。';
    const progress=run.progress || {}, total=progress.total_units, completed=progress.completed_units, meter=el('wh-daily-progress');
    const plannedUnit=['planned_unit','计划单元'].includes(progress.unit);
    const known=plannedUnit && Number.isInteger(total) && total>0 && Number.isInteger(completed) && completed>=0 && completed<=total;
    if(known){meter.setAttribute('max',total);meter.setAttribute('value',completed);}else meter.removeAttribute('value');
    el('wh-daily-progress-label').textContent=known?'已完成 '+completed+' / '+total+' 个计划单元（'+Math.floor(completed/total*100)+'%）；失败 '+measured(progress.failed_units)+' 个':
      plannedUnit?'已完成 '+measured(completed)+' 个计划单元；分母未确定；失败 '+measured(progress.failed_units)+' 个':'进度单位待核验；可靠计划单元分母未确定。';
    el('wh-daily-run-metrics').textContent=volume(run.metrics);
    const receipts=el('wh-daily-receipts');receipts.replaceChildren();
    for(const receipt of daily.receipts.values()) {
      const article=node('article');article.append(node('strong',(kindLabels[receipt.result_kind] || '来源结果待核验')+' / '+receipt.table),
        node('p','提供方 '+(receipt.provider || '待核验')+'；渠道 '+(receipt.channel || '待核验')+'；接口 '+(receipt.api_name || '待核验')),
        node('p','请求时间 '+(receipt.requested_at || '待核验')+'；响应时间 '+(receipt.received_at || '待核验')),
        node('p',(receipt.source_time?.kind==='business_time'?'业务源时间：':receipt.source_time?.kind==='response_time'?'接口响应源时间：':'源时点：')+(receipt.source_time?.value || '待核验')),
        node('p','单元 '+receipt.unit_id+'；返回 '+measured(receipt.result_rows)+' 行；回执 '+receipt.receipt_id),evidence(receipt.request_scope || {}));
      receipts.appendChild(article);
    }
    if(!daily.receipts.size)receipts.appendChild(node('p','尚无逐请求来源回执，源时点待核验。','muted'));
    el('wh-daily-events').textContent=JSON.stringify({units:run.units || [],events:[...daily.events.values()]},null,2);
    const failed=(run.units || []).filter(unit=>['failed','interrupted'].includes(unit.result?.execution_status));
    const retrySafe=run.retry_supported===true && terminal.has(run.status) && failed.length>0 && run.plan?.source===el('wh-daily-source').value;
    el('wh-daily-retry').disabled=!!data.daily_read_only || !retrySafe;
    el('wh-daily-retry-note').textContent=retrySafe?'仅预览原任务失败／中断的 '+failed.length+' 个单元，不包含已成功单元。':'该入口精确单元重试未接管；请重新预览实际范围。';
  }
  function openRun(runId) {
    if(disposed)return;
    if(!/^[a-f0-9]{32}$/.test(runId || '')){dailyMessage('请选择已登记的32位任务标识。');return;}
    const saved=data.daily_read_only?(daily.model?.runs || []).find(run=>run.run_id===runId):null;
    if(data.daily_read_only && !saved){dailyMessage('非实时快照未保存此任务；在线查询已禁用。');return;}
    clearTimeout(daily.timer);daily.runVersion++;daily.run={run_id:runId};daily.events.clear();daily.receipts.clear();daily.seq=0;daily.finished=false;
    el('wh-daily-run-id').value=runId;
    if(saved){
      daily.run=saved;
      for(const event of saved.events || []){
        if(Number.isInteger(event.seq))daily.events.set(event.seq,event);
        if(event.kind==='source_receipt' && event.payload?.receipt_id)daily.receipts.set(event.payload.receipt_id,event.payload);
      }
      for(const item of saved.receipts || []){const receipt=item.payload || item;if(receipt.receipt_id)daily.receipts.set(receipt.receipt_id,receipt);}
      renderRun();dailyMessage('非实时快照；仅显示已保存的任务、来源回执与计量。');return;
    }
    renderRun();pollRun();
  }
  async function pollRun() {
    if(disposed || !daily.run || data.daily_read_only)return;
    clearTimeout(daily.timer);
    const runId=daily.run.run_id,runVersion=daily.runVersion,dateVersion=daily.dateVersion,ticket=++daily.pollVersion,seq=daily.seq;
    try {
      const run=await request('/api/data/runs/'+runId+'?after_seq='+seq+'&limit=100');
      if(runVersion!==daily.runVersion || dateVersion!==daily.dateVersion || ticket!==daily.pollVersion || daily.run?.run_id!==runId)return;
      if(run.run_id!==runId || run.date!==el('wh-daily-date').value)throw new Error('任务标识或日期与当前选择不一致');
      if(!Number.isInteger(run.next_seq) || run.next_seq<seq || (run.has_more && run.next_seq===seq))throw new Error('事件回执序号无效');
      for(const event of run.events || []) {
        if(Number.isInteger(event.seq) && event.seq>0)daily.events.set(event.seq,event);
        if(event.kind==='source_receipt' && event.payload?.receipt_id)daily.receipts.set(event.payload.receipt_id,event.payload);
      }
      for(const item of run.receipts || []) { const receipt=item.payload || item; if(receipt.receipt_id)daily.receipts.set(receipt.receipt_id,receipt); }
      daily.seq=run.next_seq;daily.run={...run,events:[...daily.events.values()],receipts:[...daily.receipts.values()]};renderRun();
      if(run.has_more){daily.timer=setTimeout(pollRun,0);pollRun();return;}
      if(terminal.has(run.status)) {
        if(!daily.finished){daily.finished=true;dailyMessage('执行已结束，正在重新读取目标核验；不由退出码判定已更新。');loadDaily(true);}
      } else daily.timer=setTimeout(pollRun,4000);
    } catch(error) {
      if(runVersion===daily.runVersion && dateVersion===daily.dateVersion && ticket===daily.pollVersion)
        el('wh-daily-run-state').textContent='任务读取失败，保留上次状态；可刷新任务重试。'+error.message;
    }
  }
  el('wh-daily-date').addEventListener('change',()=>{daily.selectionVersion++;loadDaily();});
  ['wh-daily-source','wh-daily-market'].forEach(id=>el(id).addEventListener('change',selectionChanged));
  el('wh-daily-refresh').addEventListener('click',()=>loadDaily(true));
  el('wh-daily-preview').addEventListener('click',()=>previewPlan());
  el('wh-daily-batch').addEventListener('click',()=>{
    if(data.daily_read_only || daily.model?.date!==el('wh-daily-date').value)return;
    const rows=currentRows().filter(row=>row.update_enabled===true && sourceOf(row)===el('wh-daily-source').value && ['not_updated','partial'].includes(row.data_status) && !activeSource(sourceOf(row)) &&
      row.targets?.length && row.targets.every(target=>target?.status==='due' && target.target));
    const tables=[...new Set(rows.flatMap(row=>row.physical_tables || []))];
    if(!tables.length){dailyMessage('当前没有到期且已获更新资格的未完成项；等待发布、历史未接管与未知项已排除。');return;}
    previewPlan(tables);
  });
  el('wh-daily-cancel').addEventListener('click',cancelPlan);
  el('wh-daily-plan-dialog').addEventListener('cancel',cancelPlan);
  el('wh-daily-confirm').addEventListener('click',startPlan);
  el('wh-daily-open-run').addEventListener('click',()=>openRun(el('wh-daily-run-id').value.trim()));
  el('wh-daily-poll').addEventListener('click',pollRun);
  el('wh-daily-retry').addEventListener('click',()=>{
    const run=daily.run;if(!run || el('wh-daily-retry').disabled)return;
    const failed=(run.units || []).filter(unit=>['failed','interrupted'].includes(unit.result?.execution_status));
    previewPlan([...new Set(failed.map(unit=>unit.plan.table))],run.run_id,failed.map(unit=>unit.plan.unit_id));
  });
  el('wh-daily-export').addEventListener('click',()=>jsonExport('每日更新来源与任务.json',{...daily.model,requested_date:el('wh-daily-date').value,read_only:!!data.daily_read_only,run:daily.run}));
  document.querySelectorAll('[data-job]').forEach(b => b.addEventListener('click', () => { ensureJob(b.dataset.job).rounds=0; return poll(b.dataset.job); }));
  document.querySelectorAll('[data-sync]').forEach(b => b.addEventListener('click', async () => {
    const src=b.dataset.sync;
    if(src==='tdx' || src==='tushare'){el('wh-daily-source').value=src;selectionChanged();view('daily');return;}
    if(data.daily_read_only){dailyMessage('非实时快照；启动已禁用。');return;}
    if (!await confirmSync(b.dataset.label)) return;
    const job=ensureJob(src); job.version++; job.starting=true; job.running=null;
    clearTimeout(job.timer); runningCount();
    b.disabled=true; jobStatus(src,'启动中…','warn');
    try { const result=await request('/api/data/sync?src='+encodeURIComponent(src)); if (result.started !== true) throw new Error('接口未确认任务启动'); if(job.wait) await job.wait; job.starting=false; await poll(src); }
    catch (error) { job.starting=false; jobStatus(src,(error.name==='AbortError'?'启动请求超时，状态尚未确认':error.message)+'；请查询状态后再决定是否重试','old'); b.disabled=false; }
  }));
  ['tdx','tushare','kg','catalog'].forEach(src => poll(src));
  view(location.hash.slice(1));
  controls();
  window.addEventListener('pagehide', () => {
    disposed=true;jobs.forEach(job => {job.version++;clearTimeout(job.timer);});clearTimeout(daily.timer);
    daily.dateVersion++;daily.dailyRequestVersion++;daily.selectionVersion++;daily.planVersion++;daily.runVersion++;daily.pollVersion++;
  });
})();
</script>
"""
