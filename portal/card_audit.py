# -*- coding: utf-8 -*-
"""H-B · 卡片模板覆盖审计（P3-lite 波次 2026-10-20，纯离线只读）。

用途：扫描代码库指定文件（清单硬编码）中的 kind= 赋值与发送 msg_type
语境，输出对照表 {kind: [发送点:行号, msg_type, 是否已注册模板, 建议]}，
为 card_templates.py 补注册提供实测依据。

纪律（P3-lite 派工单 · 通用纪律）：
  - 纯离线只读零 IO 副作用：只读扫描文件、打印报告，不写盘、不联网、
    不真发飞书、不启服务、不改任何被扫描文件；
  - 判定全部为静态启发式（发送函数名 + 调用实参文本 + 行距就近配对），
    结论以 CURATED_NOTES 人工核对注记修正，供人复核；
  - 任何文件读取失败自动跳过并留痕，不抛出（closed on failure）。

用法：
    python portal/card_audit.py          # 打印人类可读报告
    python -c "import card_audit; r = card_audit.audit()"  # 取结构化结果
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

__all__ = ["audit", "render_report", "SCAN_FILES"]

REPO = os.path.dirname(HERE)

# ── 硬编码扫描清单（portal/、news_collector/、osint_video/common/ 生产 .py）──

SCAN_FILES = """
portal/_smoke_algo_market.py
portal/agent_auto_commit.py
portal/api_approvals.py
portal/api_backtest.py
portal/api_bridge.py
portal/api_brief.py
portal/api_capability.py
portal/api_chanlun_pages.py
portal/api_common.py
portal/api_daily_data.py
portal/api_data.py
portal/api_dataops.py
portal/api_events.py
portal/api_evolution.py
portal/api_execution.py
portal/api_flow.py
portal/api_gate.py
portal/api_guojia.py
portal/api_intel_bus.py
portal/api_intel_evidence.py
portal/api_kg.py
portal/api_legion_ops.py
portal/api_legion_ops_v2.py
portal/api_lineage.py
portal/api_misc.py
portal/api_model_admin.py
portal/api_news_intel.py
portal/api_notify.py
portal/api_perf.py
portal/api_portfolio.py
portal/api_replay.py
portal/api_screen.py
portal/api_tickets.py
portal/api_unified.py
portal/api_xq_intel.py
portal/approval_card.py
portal/block_triage.py
portal/build_daily_data_public.py
portal/build_guojia_public.py
portal/build_pick_details.py
portal/build_pick_verdicts.py
portal/build_todays_picks.py
portal/cainiao_auth.py
portal/card_actions.py
portal/card_templates.py
portal/dead_letter.py
portal/feishu_bot_ws.py
portal/feishu_card_v2.py
portal/feishu_control.py
portal/inbound_gateway.py
portal/job_spec_registry.py
portal/kg_stock_query.py
portal/km_feishu_hub.py
portal/market_performance.py
portal/notification_decision.py
portal/notify_contracts.py
portal/org_command_api.py
portal/portal_api.py
portal/portal_db.py
portal/portal_report.py
portal/publish_picks.py
portal/publish_schedule_server.py
portal/publish_watchdog.py
portal/push_605100_drill.py
portal/push_admin_server.py
portal/push_matrix.py
portal/push_matrix_send.py
portal/push_picks_digest.py
portal/push_picks_public_link.py
portal/push_picks_upgrade_note.py
portal/qd_daily_snapshot.py
portal/qd_replay_verify.py
portal/qd_scanner.py
portal/qd_view.py
portal/quant_mid/api_routes.py
portal/quant_mid/guojia_adapter.py
portal/quant_mid/trade_pool.py
portal/register_feishu_watchdog_task.py
portal/risk_feedback_ledger.py
portal/route_baseline.py
portal/run_bot_now.py
portal/server_gate.py
portal/share_tokens.py
portal/start_feishu_bot.py
portal/stock_card.py
portal/strategy_market.py
portal/strategy_pool_projection.py
portal/subscription_store.py
portal/subscriptions.py
portal/sync_ledger.py
portal/sync_publish.py
portal/tune_gate.py
portal/wall_adapter.py
portal/warehouse_dashboard.py
portal/warehouse_update_api.py
portal/warehouse_update_services.py
portal/watch_tower_linkage.py
portal/watch_tower_snapshot.py
news_collector/a3_blindtest.py
news_collector/alert_fail_wrap.py
news_collector/alert_io.py
news_collector/alert_push.py
news_collector/alert_watch.py
news_collector/card_build.py
news_collector/daily_report.py
news_collector/deadline_watchdog.py
news_collector/feishu_client.py
news_collector/guojia_reconcile.py
news_collector/jev_backfill.py
news_collector/jev_calib.py
news_collector/jev_decay.py
news_collector/jev_feed.py
news_collector/jev_feed_verify.py
news_collector/jev_impact.py
news_collector/jev_resonance.py
news_collector/news_ai.py
news_collector/news_collector.py
news_collector/news_digest.py
news_collector/news_doc_board.py
news_collector/news_jev_chart.py
news_collector/news_time.py
news_collector/news_time_migration.py
news_collector/push_targets.py
news_collector/quote_snapshot.py
news_collector/send_feishu.py
news_collector/send_wecom.py
news_collector/sentiment_backtest.py
news_collector/stock_analyze.py
osint_video/common/db.py
osint_video/common/feishu.py
osint_video/common/logging_setup.py
osint_video/common/paths.py
""".split()

# ── 发送函数 → msg_type 判定表（None = 从调用实参文本推断） ────────

SEND_FUNCS = {
    "send_msg_tracked": None,   # news_collector.feishu_client（实参第 2 位）
    "send_msg": None,           # news_collector.feishu_client
    "send_content_to": None,    # news_collector.send_feishu（OpenAPI 直连）
    "_hub_send": None,          # osint_video FeishuNotifier 内核
    "send_card": "interactive",   # feishu_card_v2 / FeishuNotifier（interactive 卡）
    "send_text": "text",          # FeishuNotifier 文本
    "send_daily_report": "text",  # FeishuNotifier 日报
    "push_card_multi": "interactive",  # news_collector.alert_push 多目标卡
}

SEND_RE = re.compile(
    r"\b(%s)\s*\(" % "|".join(sorted(SEND_FUNCS, key=len, reverse=True)))

# kind= 字面量（kwarg / 赋值；lookbehind 排除 source_kind/obs_kind 等）
KIND_RE = re.compile(
    r"(?<![A-Za-z_])kind\s*=\s*[\"']([A-Za-z0-9_:|.%%\-]+)[\"']")
# kind=f"{self.kind_prefix}:xxx"（osint_video FeishuNotifier 动态前缀，默认 osint）
KIND_F_PREFIX_RE = re.compile(
    r"(?<![A-Za-z_])kind\s*=\s*f\"\{self\.kind_prefix\}:([A-Za-z0-9_]+)\"")

# 就近配对窗距（行）：kind 赋值与其归属发送点的最大行距
MAX_WINDOW = 80

# 纯流水/拦截前缀：这些 kind 只出现在 push_flow.record 账目语境，无真实发送
FLOW_ONLY_PREFIXES = ("quota_block:",)

# ── 人工核对注记（机械判定的修正与建议，派工单建议集逐条给口径） ────

CURATED_NOTES = {
    "dead_letter_alert":
        "实测：push_admin_server.py:870-871 以 send_msg_tracked(body, \"text\", "
        "…) 发送（N6 死信告警降级链）→ text 发送，注册模板无效果，不注册（派工单口径）。"
        "如未来改发 interactive 卡可再按红色「💀 死信告警」注册。",
    "deadline_watch":
        "实测：api_notify.py:313 为四态队列条目 kind（HTTP payload / block_triage "
        "分诊用），无 msg_type 发送语境 → 注册无生效点，不注册并挂账说明。",
    "km_card":
        "实测：km_feishu_hub.py:242-254 经 feishu_card_v2.send_card 发送 interactive "
        "卡（语义明确：KM 知识卡）；kind 目前仅进 push_flow.record 流水（:264），"
        "发送侧未透传 kind。按派工单建议集补注册（蓝「🗂 KM 知识卡」）；"
        "发送侧透传 kind 后生效，挂账移交后续波次。",
    "daily:alert":
        "news_collector 预警卡（alert_push.push_card_multi）interactive 发送，"
        "但 kind 仅进 push_flow.record（alert_push.py:176），发送侧未透传 → "
        "注册无生效点；不在派工单建议集，观察不注册。",
    "daily:report":
        "news_collector 统一日报（daily_report.py:256），同 daily:alert 口径："
        "interactive 发送但 kind 未随路透传，观察不注册。",
    "daily:news_digest":
        "流水账 kind（news_digest.record_flow），无直接 msg_type 发送语境，观察。",
    "osint:cookie_expired":
        "osint_video FeishuNotifier 经 8097 api/send（kind 随路）interactive 发送，"
        "但卡片已自带 header（_simple_card）→ 注册仅保 header 无视觉增量；"
        "且前缀动态（kind_prefix 可配），观察不注册。",
    "osint:dead_letter":
        "同 osint:cookie_expired：interactive 但卡片自带 header，注册无视觉增量，观察。",
    "osint:cookie_updated":
        "send_text 文本发送 → 注册无效果，不注册。",
    "osint:daily_report":
        "send_text 文本发送 → 注册无效果，不注册。",
    "quant:watch_alert":
        "quant_mid/api_routes.py:262-263 以 msg_type=text 发送 → 注册无效果，不注册。",
    "inbound:feed":
        "inbound_gateway.py:575 经 send_msg() interactive 发送且语义明确"
        "（入站情报回流卡）→ 注册候选；不在派工单建议集，观察挂账留待下波次。",
    "jev_feed":
        "DB 行分类 kind（jev_feed_verify SQL），非发送 kind。",
}
# decision:* 前缀注记（CATEGORIES 动态拼接）
CURATED_PREFIX_NOTES = {
    "decision:":
        "decision:* 语境混杂：approval_card.py:87 interactive 发送"
        "（decision:announcement），notification_decision.execute 又以 "
        "msg_type=text 发送（notification_decision.py:412-414）→ 注册只影响"
        "前者的视觉且后者无效果；不在派工单建议集，观察挂账不注册。",
}


def _capture_call(lines, start_idx, col):
    """从 (start_idx, col) 的 '(' 起按括号配平截取调用文本（≤60 行）。

    启发式：字符串内含括号可能提前/延后截断，仅用于 msg_type 字面量探测。
    """
    buf, depth, started = [], 0, False
    for i in range(start_idx, min(start_idx + 60, len(lines))):
        seg = lines[i][col:] if i == start_idx else lines[i]
        for ch in seg:
            if ch == "(":
                depth += 1
                started = True
            elif ch == ")":
                depth -= 1
            buf.append(ch)
            if started and depth <= 0:
                return "".join(buf)
        buf.append("\n")
    return "".join(buf)


def _resolve_msg_type(func, call_text):
    """按发送函数名 + 实参文本判定 msg_type。"""
    fixed = SEND_FUNCS.get(func)
    if fixed:
        return fixed
    if '"interactive"' in call_text or "'interactive'" in call_text:
        return "interactive"
    if '"text"' in call_text or "'text'" in call_text:
        return "text"
    if "msg_type" in call_text:
        return "msg_type(变量)"
    return "未判明"


def _scan_file(rel_path):
    """扫描单文件 → (send_sites, kind_occurrences, error)。"""
    path = os.path.join(REPO, rel_path)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError as e:
        return [], [], "%s: 读取失败 %r" % (rel_path, e)
    lines = text.splitlines()
    send_sites = []   # (line_no, func, msg_type)
    for i, line in enumerate(lines):
        for m in SEND_RE.finditer(line):
            call = _capture_call(lines, i, m.end() - 1)
            send_sites.append((i + 1, m.group(1),
                               _resolve_msg_type(m.group(1), call)))
    kind_occ = []     # (line_no, kind, raw)
    for i, line in enumerate(lines):
        for m in KIND_F_PREFIX_RE.finditer(line):
            kind_occ.append((i + 1, "osint:%s" % m.group(1), m.group(0)))
        for m in KIND_RE.finditer(line):
            kind_occ.append((i + 1, m.group(1), m.group(0)))
    return send_sites, kind_occ, None


def audit():
    """跑审计 → 结构化结果 dict（纯内存，零写盘）。"""
    import card_templates  # noqa: PLC0415 —— 注册表现状（纯函数模块，无 IO）
    registered = set(card_templates.KIND_CARD_TEMPLATES)

    kinds = {}
    errors = []
    scanned = []
    for rel in SCAN_FILES:
        send_sites, kind_occ, err = _scan_file(rel)
        if err:
            errors.append(err)
            continue
        scanned.append(rel)
        for line_no, kind, raw in kind_occ:
            entry = kinds.setdefault(kind, {
                "occurrences": [], "msg_types": set(),
                "registered": kind in registered})
            # 就近配对：窗距内最近的发送点
            best, best_d = None, MAX_WINDOW + 1
            for s_line, func, mt in send_sites:
                d = abs(s_line - line_no)
                if d < best_d:
                    best, best_d = (s_line, func, mt), d
            if best is not None and best_d <= MAX_WINDOW:
                ctx = "%s:%d 经 %s() → %s" % (rel, best[0], best[1], best[2])
                msg_type = best[2]
            else:
                ctx = "%s:%d 无窗距内发送点" % (rel, line_no)
                msg_type = "无发送语境"
            if kind.startswith(FLOW_ONLY_PREFIXES):
                ctx += "（push_flow 流水 kind，非发送 kind）"
                msg_type = "无发送语境"
            entry["occurrences"].append(
                {"file": rel, "line": line_no, "raw": raw,
                 "send_point": ctx, "msg_type": msg_type})
            if msg_type not in ("无发送语境", "未判明", "msg_type(变量)"):
                entry["msg_types"].add(msg_type)

    for kind, entry in kinds.items():
        mts = sorted(entry["msg_types"])
        note = CURATED_NOTES.get(kind)
        if note is None:
            for prefix, pnote in CURATED_PREFIX_NOTES.items():
                if kind.startswith(prefix):
                    note = pnote
                    break
        if entry["registered"]:
            verdict, suggestion = "registered", "已注册模板；保持现状。"
        elif "interactive" in mts:
            verdict = "register_candidate"
            suggestion = ("interactive 发送且语义明确 → 按派工单建议集裁决："
                          "建议集内补注册，建议集外观察挂账。")
        elif "text" in mts:
            verdict, suggestion = "skip_text_send", "text 发送，注册无效果，不注册。"
        elif mts:
            verdict, suggestion = "observe", "msg_type 语境未判明，人工复核。"
        else:
            verdict = "skip_no_send_ctx"
            suggestion = "无 msg_type 发送语境（队列/流水/账目 kind），注册无生效点。"
        if note:
            suggestion = note
        entry["msg_types"] = mts
        entry["verdict"] = verdict
        entry["suggestion"] = suggestion

    verdict_counts = {}
    for entry in kinds.values():
        verdict_counts[entry["verdict"]] = \
            verdict_counts.get(entry["verdict"], 0) + 1
    return {
        "scanned_files": scanned,
        "scan_errors": errors,
        "registered_kinds": sorted(registered),
        "kinds": kinds,
        "summary": {
            "n_scanned": len(scanned),
            "n_scan_errors": len(errors),
            "n_kinds": len(kinds),
            "n_registered": len(registered),
            "verdict_counts": verdict_counts,
        },
    }


_VERDICT_LABELS = [
    ("registered", "已注册"),
    ("register_candidate", "interactive 候选（本次裁决对象）"),
    ("skip_text_send", "text 发送 → 不注册"),
    ("skip_no_send_ctx", "无发送语境 → 不注册"),
    ("observe", "观察（语境未判明）"),
]


def render_report(result):
    """结构化结果 → 人类可读对照表文本（只拼字符串，零 IO）。"""
    out = []
    s = result["summary"]
    out.append("卡片模板覆盖审计对照表（H-B · P3-lite · 纯离线只读）")
    out.append("扫描文件 %d 个（读取失败 %d），实测 kind %d 种，"
               "已注册模板 %d 种" % (s["n_scanned"], s["n_scan_errors"],
                                    s["n_kinds"], s["n_registered"]))
    out.append("")
    for verdict, label in _VERDICT_LABELS:
        bucket = sorted(k for k, e in result["kinds"].items()
                        if e["verdict"] == verdict)
        if not bucket:
            continue
        out.append("── %s（%d 种）──" % (label, len(bucket)))
        for kind in bucket:
            e = result["kinds"][kind]
            reg = "已注册" if e["registered"] else "未注册"
            for occ in e["occurrences"]:
                out.append("  %-24s | %-9s | %s" % (
                    kind, reg, occ["send_point"]))
            out.append("    建议：%s" % e["suggestion"])
        out.append("")
    if result["scan_errors"]:
        out.append("── 读取异常 ──")
        for err in result["scan_errors"]:
            out.append("  " + err)
        out.append("")
    return "\n".join(out)


def main():
    result = audit()
    print(render_report(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
