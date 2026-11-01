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
    parser = argparse.ArgumentParser(description="知识库每日推送准备（飞书卡片 payload，dry-run 不发送）")
    parser.add_argument("command", nargs="?", default="build", choices=["build", "check"], help="build=组装（默认），check=校验现有 payload")
    parser.add_argument("--date", default=None, help="日期 YYYYMMDD（默认取 digest.date 或当天）")
    parser.add_argument("--dry-run", action="store_true", default=True, help="dry-run（默认且唯一模式，不发网络）")
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
