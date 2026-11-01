# -*- coding: utf-8 -*-
"""知识库每日推送准备（飞书交互卡片 payload，dry-run，不发送、不联网）。

输入（缺省路径，可 CLI 覆盖）：
  portal/static/kb_learning_digest.json  学习卡（每日5张）/ 简报摘要 / 策略结论
  portal/static/report_panel.json        研报候选面板（多智能体评审）
  portal/static/kb_daily_briefing.json   研报简报（digest 缺失时的兜底）

输出：
  research/reports/kb_daily_push_<date>.json   当日推送快照
  portal/static/kb_daily_push.json             门户/推送消费的静态副本
  结构：{schema_version, date, generated_at, dry_run: true, network: "none",
         disclaimer, sources, summary, feishu_payload, card_text, meta}

CLI::

    python tools/kb_corpus/kb_daily_push.py build --date 20261101 --dry-run
    python tools/kb_corpus/kb_daily_push.py build --json
    python tools/kb_corpus/kb_daily_push.py check --in portal/static/kb_daily_push.json

安全边界：默认/唯一模式 = dry-run；**本脚本不 import 任何网络库、不发起任何 HTTP 请求**。
真实发送必须另走「飞书 webhook 接线 + 审批」流程（见 docs/plans/2026-11-01_知识库每日推送准备_v1.md）。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
STATIC = REPO / "portal" / "static"
DEFAULT_DIGEST = STATIC / "kb_learning_digest.json"
DEFAULT_PANEL = STATIC / "report_panel.json"
DEFAULT_BRIEFING = STATIC / "kb_daily_briefing.json"
DEFAULT_OUT_DIR = REPO / "research" / "reports"
DEFAULT_PORTAL_OUT = STATIC / "kb_daily_push.json"
DEFAULT_BASE_URL = "http://127.0.0.1:8734"

SCHEMA_VERSION = 1
GENERATOR = "tools/kb_corpus/kb_daily_push.py"
DISCLAIMER = "本推送由本地脚本组装，仅供内部研究与学习，不构成投资建议；真实发送需经飞书 webhook 接线与人工审批。"
ZH_TITLE_PREFIX = "零界知识库日报"
SEND_RESULT_DIR = DEFAULT_OUT_DIR
SEND_KIND = "daily:report"
# 2026-11-01 task-109：群卡片通道探测（不改变 send_payload 默认优先级；权限就绪后可切换）
GROUP_TARGET_NAME = "内部测试"
GROUP_TARGET_TYPE = "chat_id"
GROUP_MSG_TYPE = "interactive"



# ---------------------------------------------------------------------------
# 通用
# ---------------------------------------------------------------------------
def read_json_tolerant(path: str | Path) -> tuple[dict | None, str | None]:
    """读取 JSON；缺失/损坏返回 (None, 原因)，不抛异常。"""
    p = Path(path)
    try:
        if not p.is_file():
            return None, f"文件不存在: {p}"
        return json.loads(p.read_text(encoding="utf-8")), None
    except Exception as exc:  # noqa: BLE001 - 输入容错边界
        return None, f"读取/解析失败: {p}: {exc}"


def describe_file(path: str | Path) -> dict:
    p = Path(path)
    info = {"path": str(p), "exists": False, "mtime": None, "size": None}
    try:
        if p.is_file():
            st = p.stat()
            info.update(exists=True, size=int(st.st_size),
                        mtime=datetime.fromtimestamp(st.st_mtime).astimezone().isoformat(timespec="seconds"))
    except OSError:
        pass
    return info


def _clean(text, limit: int = 0) -> str:
    s = " ".join(str(text or "").split())
    return s[:limit] if limit and len(s) > limit else s


def _portal_links(base_url: str) -> list[dict]:
    base = (base_url or DEFAULT_BASE_URL).rstrip("/")
    return [
        {"label": "📚 学习卡 / 今日简报", "url": f"{base}/kb_reports.html"},
        {"label": "🧠 研报候选面板", "url": f"{base}/selection.html"},
        {"label": "📊 知识入库状态", "url": f"{base}/kb_ingest.html"},
    ]


# ---------------------------------------------------------------------------
# 输入归一化
# ---------------------------------------------------------------------------
def extract_digest(digest: dict | None) -> dict:
    """从 learning_digest 提取推送所需字段（容错空数据）。"""
    digest = digest if isinstance(digest, dict) else {}
    cards = []
    for i, card in enumerate((digest.get("cards") or [])[:5], 1):
        if not isinstance(card, dict):
            continue
        cards.append(
            {
                "rank": card.get("rank") or i,
                "q": _clean(card.get("q"), 120),
                "a": _clean(card.get("a"), 220),
                "source": _clean(card.get("source"), 120),
                "quality_score": card.get("quality_score"),
            }
        )
    briefing = digest.get("briefing") if isinstance(digest.get("briefing"), dict) else {}
    notes = digest.get("strategy_notes") if isinstance(digest.get("strategy_notes"), dict) else {}
    highlights = []
    for h in (briefing.get("highlights") or [])[:3]:
        if not isinstance(h, dict):
            continue
        highlights.append({"concept": _clean(h.get("concept"), 60), "claim": _clean(h.get("claim"), 140)})
    return {
        "date": _clean(digest.get("date")),
        "headline": _clean(briefing.get("headline")),
        "cards": cards,
        "highlights": highlights,
        "strategy_takeaway": _clean(notes.get("takeaway"), 160),
        "strategy_title": _clean(notes.get("title"), 80),
    }


def extract_panel(panel: dict | None) -> dict:
    """从 report_panel 提取研报候选（容错空数据）。"""
    panel = panel if isinstance(panel, dict) else {}
    summary = panel.get("summary") if isinstance(panel.get("summary"), dict) else {}
    candidates = []
    for c in (panel.get("candidates") or [])[:5]:
        if not isinstance(c, dict):
            continue
        candidates.append(
            {
                "report_id": _clean(c.get("report_id"), 60),
                "title": _clean(c.get("title"), 100),
                "date": _clean(c.get("date"), 20),
                "pages": c.get("pages"),
                "pass_models": c.get("pass_models") or [],
                "role_agreement_rate": c.get("role_agreement_rate"),
            }
        )
    return {
        "date_tag": _clean(panel.get("date_tag")),
        "reports": summary.get("reports"),
        "candidates_count": len(candidates),
        "rejected": summary.get("rejected"),
        "page_invalid_facts": summary.get("page_invalid_facts"),
        "candidates": candidates,
        "disclaimer": _clean(panel.get("disclaimer"), 120),
    }


# ---------------------------------------------------------------------------
# 飞书卡片组装（Card 2.0；与 tools/approval_card.py 同风格）
# ---------------------------------------------------------------------------
def build_card(summary: dict, feishu_date: str) -> dict:
    """组装飞书交互卡片（Card 2.0 JSON）；纯函数，无网络。"""
    elements: list[dict] = []

    head = [f"**日期**：{feishu_date}",
            f"**摘要**：{summary['headline'] or '今日暂无新简报'}"]
    if summary["strategy_takeaway"]:
        head.append(f"**策略结论**：{summary['strategy_takeaway']}")
    elements.append({"tag": "markdown", "content": "\n".join(head)})

    card_lines = [f"**📚 学习卡（每日 {len(summary['cards'])} 张）**"]
    if summary["cards"]:
        for card in summary["cards"]:
            line = f"{card['rank']}. **{card['q']}** {card['a']}"
            if card["source"]:
                line += f"（{card['source']}）"
            card_lines.append(line)
    else:
        card_lines.append("今日暂无学习卡（上游数据缺失，稍后刷新）")
    elements.append({"tag": "markdown", "content": "\n".join(card_lines)})

    elements.append({"tag": "hr"})

    cand_lines = [f"**🧠 研报候选**：{summary['panel']['candidates_count']} 个"
                  f"（评审 {summary['panel']['reports'] or 0} 篇 / 拦截 {summary['panel']['rejected'] or 0} 篇）"]
    if summary["panel"]["candidates"]:
        for c in summary["panel"]["candidates"]:
            cand_lines.append(f"- {c['title']}（{c['date']} · {c['pages']} 页 · 一致性 "
                              f"{c['role_agreement_rate'] if c['role_agreement_rate'] is not None else '—'}）")
    else:
        cand_lines.append("今日暂无进入候选的研报（详见门户）")
    elements.append({"tag": "markdown", "content": "\n".join(cand_lines)})

    elements.append({"tag": "hr"})
    link_lines = ["**🔗 门户入口**"]
    for link in summary["portal_links"]:
        link_lines.append(f"[{link['label']}]({link['url']})")
    elements.append({"tag": "markdown", "content": "\n".join(link_lines)})
    elements.append({"tag": "markdown", "content": f"<font color='grey'>{DISCLAIMER}</font>"})

    return {
        "schema": "2.0",
        "config": {
            "width_mode": "default",
            "summary": {"content": f"{ZH_TITLE_PREFIX} {feishu_date}"},
        },
        "header": {
            "title": {"tag": "plain_text", "content": f"📚 {ZH_TITLE_PREFIX} · {feishu_date}"},
            "subtitle": {"tag": "plain_text", "content": "DRY-RUN · 未发送（学习卡/简报/研报候选）"},
            "template": "blue",
        },
        "body": {"elements": elements},
    }


def build_card_text(summary: dict, feishu_date: str) -> str:
    """纯文本降级版（飞书 text 消息 / 邮件 / 日志预览）。"""
    lines = [f"【{ZH_TITLE_PREFIX}】{feishu_date}",
             f"摘要：{summary['headline'] or '今日暂无新简报'}",
             f"学习卡（{len(summary['cards'])} 张）："]
    for card in summary["cards"]:
        lines.append(f"  {card['rank']}. {card['q']} {card['a']}")
    if not summary["cards"]:
        lines.append("  今日暂无学习卡")
    lines.append(f"研报候选：{summary['panel']['candidates_count']} 个")
    for c in summary["panel"]["candidates"]:
        lines.append(f"  - {c['title']}（{c['date']}）")
    if summary["strategy_takeaway"]:
        lines.append(f"策略结论：{summary['strategy_takeaway']}")
    for link in summary["portal_links"]:
        lines.append(f"  {link['label']}: {link['url']}")
    lines.append("研究输出，不构成投资建议（dry-run，未发送）。")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 组装完整 payload
# ---------------------------------------------------------------------------
def build_payload(
    digest: dict | None,
    panel: dict | None,
    briefing: dict | None = None,
    *,
    date: str | None = None,
    base_url: str = DEFAULT_BASE_URL,
    sources: dict | None = None,
    fallbacks: list[str] | None = None,
    now: datetime | None = None,
) -> dict:
    """组装推送 payload（纯函数，不读写文件、不联网）。"""
    now = now or datetime.now().astimezone()
    fallbacks = list(fallbacks or [])
    digest_info = extract_digest(digest)
    panel_info = extract_panel(panel)
    if not digest_info["date"] and isinstance(briefing, dict):
        digest_info["date"] = _clean(briefing.get("date"))
        digest_info["headline"] = digest_info["headline"] or "研报简报（digest 缺失，使用 briefing 兜底）"
        fallbacks.append("learning_digest 缺失/为空，摘要降级为 briefing 兜底")
    if not isinstance(digest, dict):
        fallbacks.append("learning_digest 数据缺失：学习卡为空")
    if not isinstance(panel, dict):
        fallbacks.append("report_panel 数据缺失：研报候选为空")

    stamp = date or digest_info["date"] or now.strftime("%Y%m%d")
    feishu_date = f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:]}" if len(stamp) == 8 else stamp

    summary = {
        "title": f"{ZH_TITLE_PREFIX} {feishu_date}",
        "feishu_date": feishu_date,
        "headline": digest_info["headline"],
        "highlights": digest_info["highlights"],
        "cards": digest_info["cards"],
        "strategy_title": digest_info["strategy_title"],
        "strategy_takeaway": digest_info["strategy_takeaway"],
        "panel": panel_info,
        "portal_links": _portal_links(base_url),
    }
    card = build_card(summary, feishu_date)
    return {
        "schema_version": SCHEMA_VERSION,
        "date": stamp,
        "generated_at": now.isoformat(timespec="seconds"),
        "dry_run": True,
        "network": "none",
        "disclaimer": DISCLAIMER,
        "sources": sources or {
            "digest": describe_file(DEFAULT_DIGEST),
            "panel": describe_file(DEFAULT_PANEL),
            "briefing": describe_file(DEFAULT_BRIEFING),
        },
        "summary": {
            "title": summary["title"],
            "headline": summary["headline"],
            "cards_count": len(summary["cards"]),
            "candidates_count": panel_info["candidates_count"],
            "reports_reviewed": panel_info["reports"],
            "rejected_count": panel_info["rejected"],
            "strategy_takeaway": summary["strategy_takeaway"],
            "portal_links": summary["portal_links"],
        },
        "feishu_payload": {"msg_type": "interactive", "card": card},
        "card_text": build_card_text(summary, feishu_date),
        "meta": {
            "generator": GENERATOR,
            "schema_version": SCHEMA_VERSION,
            "dry_run": True,
            "network_calls": 0,
            "fallbacks": fallbacks,
            "note": "仅组装 payload，不发送；真实发送需 webhook 接线 + 审批",
        },
    }


def load_inputs(
    digest_path: str | Path = DEFAULT_DIGEST,
    panel_path: str | Path = DEFAULT_PANEL,
    briefing_path: str | Path = DEFAULT_BRIEFING,
) -> tuple[dict | None, dict | None, dict | None, dict, list[str]]:
    """读取三路输入，返回 (digest, panel, briefing, sources, errors/fallbacks)。"""
    digest, digest_err = read_json_tolerant(digest_path)
    panel, panel_err = read_json_tolerant(panel_path)
    briefing, briefing_err = read_json_tolerant(briefing_path)
    sources = {
        "digest": describe_file(digest_path),
        "panel": describe_file(panel_path),
        "briefing": describe_file(briefing_path),
    }
    errors = [err for err in (digest_err, panel_err, briefing_err) if err]
    return digest, panel, briefing, sources, errors


def build(
    *,
    date: str | None = None,
    digest_path: str | Path = DEFAULT_DIGEST,
    panel_path: str | Path = DEFAULT_PANEL,
    briefing_path: str | Path = DEFAULT_BRIEFING,
    base_url: str = DEFAULT_BASE_URL,
    out_dir: str | Path = DEFAULT_OUT_DIR,
    portal_out: str | Path = DEFAULT_PORTAL_OUT,
    write: bool = True,
) -> dict:
    """读取输入→组装→写 research 快照 + portal 静态副本（dry-run）。"""
    digest, panel, briefing, sources, errors = load_inputs(digest_path, panel_path, briefing_path)
    payload = build_payload(
        digest, panel, briefing,
        date=date, base_url=base_url, sources=sources, fallbacks=errors,
    )
    payload["meta"]["inputs_ok"] = not errors
    if write:
        out_path = Path(out_dir) / f"kb_daily_push_{payload['date']}.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        txt = json.dumps(payload, ensure_ascii=False, indent=1) + "\n"
        out_path.write_text(txt, encoding="utf-8")
        portal_path = Path(portal_out)
        portal_path.parent.mkdir(parents=True, exist_ok=True)
        portal_path.write_text(txt, encoding="utf-8")
        payload["meta"]["out"] = str(out_path)
        payload["meta"]["portal_out"] = str(portal_path)
    return payload


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------
REQUIRED_TOP = ("schema_version", "date", "generated_at", "dry_run", "disclaimer",
                "sources", "summary", "feishu_payload", "card_text", "meta")


def resolve_group_target(target_name: str = GROUP_TARGET_NAME) -> dict | None:
    """从 news_collector.push_targets 读取群目标（不硬编码 chat_id）；查无返回 None。"""
    try:
        repo = str(REPO)
        if repo not in sys.path:
            sys.path.insert(0, repo)
        from news_collector import push_targets  # noqa: PLC0415 - 延迟导入
        entry = push_targets.target_entry(target_name)
        if not entry:
            return None
        return {"name": entry.get("name"), "id": entry.get("id"),
                "type": entry.get("type") or GROUP_TARGET_TYPE, "app": entry.get("app")}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


def send_group_card(payload: dict, *, target_name: str = GROUP_TARGET_NAME,
                    resolve_fn=None, send_group_fn=None, kind: str = SEND_KIND) -> dict:
    """尝试「群 chat_id + 交互卡片」通道（task-109 探测；成功也不改变默认 send_payload 优先级）。

    真实发送需显式调用；测试可注入 send_group_fn/resolve_fn（mock 不联网）。
    """
    errors = validate_payload(payload)
    if errors:
        return {"ok": False, "sent": False, "channel": "failed",
                "reason": "validate_failed", "errors": errors}
    card = (payload.get("feishu_payload") or {}).get("card")
    if not isinstance(card, dict) or not card:
        return {"ok": False, "sent": False, "channel": "failed", "reason": "no_feishu_card"}
    target = (resolve_fn or resolve_group_target)(target_name)
    if not isinstance(target, dict) or not target.get("id"):
        return {"ok": False, "sent": False, "channel": "failed",
                "reason": "target_not_found", "target": target}
    receive_id, receive_type = target["id"], target.get("type") or GROUP_TARGET_TYPE
    content = json.dumps(payload["feishu_payload"], ensure_ascii=False)
    try:
        if send_group_fn is None:
            from news_collector import send_feishu  # noqa: PLC0415
            send_group_fn = send_feishu.send_content_to
        ok = bool(send_group_fn(receive_id, receive_type, content, GROUP_MSG_TYPE, kind=kind))
        error = None
    except Exception as exc:  # noqa: BLE001
        ok, error = False, f"{type(exc).__name__}: {exc}"
    return {"ok": ok, "sent": ok, "channel": "group_card" if ok else "failed",
            "target": {"name": target.get("name"), "id": receive_id,
                       "type": receive_type, "app": target.get("app")},
            "msg_type": GROUP_MSG_TYPE, "kind": kind,
            "date": payload.get("date"),
            "error": error}


def send_payload(payload: dict, *, send_fn=None, send_text_fn=None, kind: str = SEND_KIND) -> dict:
    """真实发送（显式调用才联网）：复用 news_collector/send_feishu 既有 Feishu 出口。

    安全：① validate_payload 有错 → 拒绝发送；② 0 卡片且 0 候选 → 不发送；
    ③ send_fn 可注入（测试 mock，不联网）；④ 发送结果单独落盘，不改 payload.dry_run 语义。
    """
    errors = validate_payload(payload)
    if errors:
        return {"ok": False, "sent": False, "reason": "validate_failed", "errors": errors}
    summary = payload.get("summary") or {}
    if not (summary.get("cards_count") or summary.get("candidates_count")):
        return {"ok": False, "sent": False, "reason": "empty_payload（0 卡片且 0 候选，不发送）"}
    card = (payload.get("feishu_payload") or {}).get("card")
    if not isinstance(card, dict) or not card:
        return {"ok": False, "sent": False, "reason": "no_feishu_card"}
    if send_fn is None:
        repo = str(REPO)
        if repo not in sys.path:
            sys.path.insert(0, repo)
        from news_collector import send_feishu  # noqa: PLC0415 - 延迟导入，dry-run 不联网
        send_fn = send_feishu.send_content
    content = json.dumps(payload["feishu_payload"], ensure_ascii=False)
    channel = "interactive"
    ok = False
    try:
        ok = bool(send_fn(content, "interactive", kind=kind))
    except Exception as exc:  # noqa: BLE001 - 发送失败不影响主链
        ok = False
        last_error = f"{type(exc).__name__}: {exc}"
    # 兜底：卡片权限/格式不可用时发送纯文本摘要（不丢当日推送）
    if not ok:
        text = str(payload.get("card_text") or "").strip()
        if text:
            try:
                text_fn = send_text_fn
                if text_fn is None:
                    from news_collector import send_feishu as _sf  # noqa: PLC0415
                    text_fn = _sf.send_text
                ok = bool(text_fn(text, kind=kind))
                channel = "text_fallback" if ok else "failed"
            except Exception as exc:  # noqa: BLE001
                channel = "failed"
                last_error = f"{type(exc).__name__}: {exc}"
    result = {"ok": ok, "sent": ok, "kind": kind, "channel": channel,
              "date": payload.get("date"),
              "sent_at": datetime.now().astimezone().isoformat(timespec="seconds"),
              "summary": summary}
    if not ok:
        result["reason"] = locals().get("last_error") or "send_failed"
    out = Path(SEND_RESULT_DIR) / f"kb_daily_push_send_{payload.get('date') or 'unknown'}.json"
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        result["result_file"] = str(out)
    except Exception as exc:  # noqa: BLE001
        result["result_file_error"] = f"{type(exc).__name__}: {exc}"
    return result


def validate_payload(payload) -> list[str]:
    """校验推送 payload schema（含卡片字段/dry-run/免责声明），返回错误列表。"""
    errors: list[str] = []
    if not isinstance(payload, dict):
        return [f"顶层必须是 object，实际 {type(payload).__name__}"]
    for key in REQUIRED_TOP:
        if key not in payload:
            errors.append(f"缺少顶层字段 '{key}'")
    if payload.get("dry_run") is not True:
        errors.append("dry_run 必须为 true（本脚本禁止真实发送）")
    if payload.get("network") != "none":
        errors.append("network 必须为 'none'（禁止网络请求）")
    if not isinstance(payload.get("disclaimer"), str) or "不构成投资建议" not in payload["disclaimer"]:
        errors.append("disclaimer 必须包含「不构成投资建议」")

    summary = payload.get("summary")
    if not isinstance(summary, dict):
        errors.append("summary 必须是 object")
    else:
        if not _clean(summary.get("title")):
            errors.append("summary.title 不能为空")
        if not isinstance(summary.get("cards_count"), int):
            errors.append("summary.cards_count 必须是整数")
        if not isinstance(summary.get("candidates_count"), int):
            errors.append("summary.candidates_count 必须是整数")
        links = summary.get("portal_links")
        if not isinstance(links, list) or not links:
            errors.append("summary.portal_links 必须是非空数组")
        else:
            urls = [str(x.get("url")) for x in links if isinstance(x, dict)]
            for required in ("/kb_reports.html", "/selection.html"):
                if not any(required in u for u in urls):
                    errors.append(f"portal_links 缺少 {required}")
        if not isinstance(summary.get("headline"), str):
            errors.append("summary.headline 必须是字符串")

    feishu = payload.get("feishu_payload")
    if not isinstance(feishu, dict):
        errors.append("feishu_payload 必须是 object")
    else:
        if feishu.get("msg_type") != "interactive":
            errors.append("feishu_payload.msg_type 必须是 'interactive'")
        card = feishu.get("card")
        if not isinstance(card, dict):
            errors.append("feishu_payload.card 必须是 object")
        else:
            if card.get("schema") != "2.0":
                errors.append("card.schema 必须是 '2.0'")
            header = card.get("header")
            if not isinstance(header, dict):
                errors.append("card.header 必须是 object")
            else:
                title = header.get("title")
                if not isinstance(title, dict) or not _clean(title.get("content")):
                    errors.append("card.header.title.content 不能为空")
                if not title or title.get("tag") != "plain_text":
                    errors.append("card.header.title.tag 必须是 'plain_text'")
            body = card.get("body")
            if not isinstance(body, dict) or not isinstance(body.get("elements"), list):
                errors.append("card.body.elements 必须是数组")
            elif not body["elements"]:
                errors.append("card.body.elements 不能为空")
            else:
                for i, el in enumerate(body["elements"]):
                    if not isinstance(el, dict) or "tag" not in el:
                        errors.append(f"card.body.elements[{i}] 缺少 tag")
    if not isinstance(payload.get("card_text"), str) or not payload["card_text"].strip():
        errors.append("card_text 必须是非空字符串（纯文本降级版）")
    meta = payload.get("meta")
    if not isinstance(meta, dict):
        errors.append("meta 必须是 object")
    else:
        if meta.get("generator") != GENERATOR:
            errors.append(f"meta.generator 应为 {GENERATOR}")
        if meta.get("network_calls") != 0:
            errors.append("meta.network_calls 必须为 0")
    return errors


def check_payload_file(path: str | Path = DEFAULT_PORTAL_OUT) -> dict:
    p = Path(path)
    payload, err = read_json_tolerant(p)
    if payload is None:
        return {"ok": False, "path": str(p), "errors": [err or "读取失败"], "warnings": []}
    errors = validate_payload(payload)
    return {"ok": not errors, "path": str(p), "errors": errors,
            "warnings": [], "date": payload.get("date"), "dry_run": payload.get("dry_run")}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _ensure_utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover
            pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="知识库每日推送（build=准备 payload；send=真实发送，需显式指定）")
    parser.add_argument("command", nargs="?", default="build", choices=["build", "check", "send"], help="build=组装（默认），check=校验，send=真实发送（飞书）")
    parser.add_argument("--date", default=None, help="日期 YYYYMMDD（默认取 digest.date 或当天）")
    parser.add_argument("--dry-run", action="store_true", default=False, help="send 命令下只组装不发送（build 恒 dry-run）")
    parser.add_argument("--digest", default=str(DEFAULT_DIGEST), help="learning_digest JSON 路径")
    parser.add_argument("--panel", default=str(DEFAULT_PANEL), help="report_panel JSON 路径")
    parser.add_argument("--briefing", default=str(DEFAULT_BRIEFING), help="kb_daily_briefing JSON 路径（兜底）")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="门户链接 base URL")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="research 快照输出目录")
    parser.add_argument("--portal-out", default=str(DEFAULT_PORTAL_OUT), help="门户静态副本路径")
    parser.add_argument("--no-write", action="store_true", help="只组装不写盘（预览/测试）")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    return parser


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_stdout()
    args = build_parser().parse_args(argv)
    if args.command == "check":
        report = check_payload_file(args.portal_out)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        elif report["ok"]:
            print(f"校验通过：{report['path']}（date={report.get('date')} dry_run={report.get('dry_run')}）")
        else:
            print(f"校验失败：{len(report['errors'])} 个错误", file=sys.stderr)
            for err in report["errors"]:
                print(f"  - {err}", file=sys.stderr)
        return 0 if report["ok"] else 1

    if args.command == "send":
        payload = build(
            date=args.date, digest_path=args.digest, panel_path=args.panel,
            briefing_path=args.briefing, base_url=args.base_url,
            out_dir=args.out_dir, portal_out=args.portal_out, write=not args.no_write,
        )
        if args.dry_run:
            print(f"DRY-RUN 未发送：date={payload['date']} cards={payload['summary']['cards_count']} "
                  f"candidates={payload['summary']['candidates_count']}")
            return 0
        result = send_payload(payload)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(("SEND_OK" if result.get("ok") else "SEND_FAIL") + " " +
                  json.dumps({k: result.get(k) for k in ("date", "kind", "reason", "sent_at")},
                             ensure_ascii=False))
            if result.get("result_file"):
                print(f"  发送结果：{result['result_file']}")
        return 0 if result.get("ok") else 1

    payload = build(
        date=args.date,
        digest_path=args.digest,
        panel_path=args.panel,
        briefing_path=args.briefing,
        base_url=args.base_url,
        out_dir=args.out_dir,
        portal_out=args.portal_out,
        write=not args.no_write,
    )
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"推送 payload 已组装（dry-run，未发送）：date={payload['date']} | "
              f"cards={payload['summary']['cards_count']} | candidates={payload['summary']['candidates_count']} | "
              f"network_calls={payload['meta']['network_calls']}")
        if payload["meta"].get("out"):
            print(f"  快照：{payload['meta']['out']}")
            print(f"  门户副本：{payload['meta']['portal_out']}")
        for fb in payload["meta"]["fallbacks"]:
            print(f"  降级提示：{fb}", file=sys.stderr)
        print("  feishu_payload.msg_type=interactive · card.schema=2.0 · dry_run=True")
    return 0


if __name__ == "__main__":
    sys.exit(main())
