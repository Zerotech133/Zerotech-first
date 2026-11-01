# -*- coding: utf-8 -*-
"""学习卡/简报/策略结论 → 门户每日学习摘要（learning digest）生成器。

输入（均可通过 CLI 覆盖，缺失/损坏时容错降级）：
  portal/static/kb_learning_cards.json    学习卡（tools/kb_corpus/learning_cards.py 产出）
  portal/static/kb_daily_briefing.json    研报简报（tools/kb_corpus/daily_briefing.py 产出）
  knowledge_docs/选股策略研究结论_20261101.md   本轮策略研究结论要点

输出：
  portal/static/kb_learning_digest.json   {date, cards(每日5张/主题去重/质量排序), briefing, strategy_notes, meta}

CLI::

    python tools/kb_corpus/learning_digest.py run          # 生成摘要（默认）
    python tools/kb_corpus/learning_digest.py --check      # 校验已生成的摘要 JSON（schema）
    python tools/kb_corpus/learning_digest.py check --json # 同上，JSON 输出

退出码：0=成功；1=--check 校验失败 / 输出写入失败。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEFAULT_CARDS = REPO / "portal" / "static" / "kb_learning_cards.json"
DEFAULT_BRIEFING = REPO / "portal" / "static" / "kb_daily_briefing.json"
DEFAULT_STRATEGY_NOTES = REPO / "knowledge_docs" / "选股策略研究结论_20261101.md"
DEFAULT_OUT = REPO / "portal" / "static" / "kb_learning_digest.json"

DAILY_CARDS = 5
BRIEFING_HIGHLIGHTS = 5
MAX_STRATEGY_POINTS = 10
GENERATOR = "tools/kb_corpus/learning_digest.py"
SCHEMA_VERSION = 1

REQUIRED_TOP_KEYS = ("date", "cards", "briefing", "strategy_notes", "meta")
REQUIRED_CARD_KEYS = ("rank", "topic_key", "q", "a", "concept", "source", "quality_score")
DATE_RE = re.compile(r"^\d{8}$")


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------
def read_json_tolerant(path: str | Path) -> tuple[dict | None, str | None]:
    """读取 JSON；不存在/损坏时返回 (None, 原因)，绝不抛异常。"""
    p = Path(path)
    try:
        if not p.is_file():
            return None, f"文件不存在: {p}"
        return json.loads(p.read_text(encoding="utf-8")), None
    except Exception as exc:  # noqa: BLE001 - 输入容错的边界
        return None, f"读取/解析失败: {p}: {exc}"


def read_text_tolerant(path: str | Path) -> tuple[str, str | None]:
    p = Path(path)
    try:
        if not p.is_file():
            return "", f"文件不存在: {p}"
        return p.read_text(encoding="utf-8"), None
    except Exception as exc:  # noqa: BLE001
        return "", f"读取失败: {p}: {exc}"


def _clean_text(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


# ---------------------------------------------------------------------------
# 主题去重 / 质量排序（纯函数，供测试）
# ---------------------------------------------------------------------------
_TOPIC_NOISE = re.compile(r"[\s\u3000（）()【】\[\]《》「」『』“”\"'’·、，,。.：:；;？?！!—-]+")


def normalize_topic(text) -> str:
    """主题归一化：小写、去空白与常见标点，用于去重。"""
    return _TOPIC_NOISE.sub("", str(text or "")).lower()


def _is_near_duplicate(key: str, seen: list[str]) -> bool:
    """精确重复或中文长度≥6的包含关系（如「AI原生安全」vs「AI原生安全体系」）。"""
    for other in seen:
        if key == other:
            return True
        if len(key) >= 6 and len(other) >= 6 and (key in other or other in key):
            return True
    return False


def quality_score(card: dict) -> float:
    """学习卡质量分（0–100，确定性）：答案信息量/证据充分性/主题命名/出处可读性/数据密度。"""
    answer = _clean_text(card.get("a"))
    evidence = _clean_text(card.get("evidence"))
    concept = _clean_text(card.get("concept"))
    source = _clean_text(card.get("source"))
    score = 50.0

    n = len(answer)
    if 40 <= n <= 220:
        score += 15
    elif n >= 20:
        score += 8
    else:
        score -= 15
    if n > 400:
        score -= 5
    if "..." in answer or "…" in answer:
        score -= 6  # 截断答案降权

    m = len(evidence)
    score += min(12.0, m / 20.0)
    if m < 30:
        score -= 10

    if 4 <= len(concept) <= 30:
        score += 6
    if concept and concept.isascii():
        score -= 2

    if "《" in source:
        score += 5
    if re.search(r"p\d+", source):
        score += 4
    if re.search(r"rp_[A-Za-z0-9]+", source):
        score -= 1  # 研报流水号可读性略低

    digits = len(re.findall(r"\d", answer))
    score += min(5.0, digits * 0.5)
    return round(max(0.0, min(100.0, score)), 2)


def select_daily_cards(cards: list, n: int = DAILY_CARDS) -> list[dict]:
    """主题去重 + 质量排序，取每日 n 张。纯函数、确定性（同分按输入顺序）。"""
    scored: list[dict] = []
    for idx, card in enumerate(cards or []):
        if not isinstance(card, dict):
            continue
        answer = _clean_text(card.get("a"))
        question = _clean_text(card.get("q"))
        if not answer:
            continue
        concept = _clean_text(card.get("concept"))
        topic_key = normalize_topic(concept or question)
        if not topic_key:
            continue
        scored.append(
            {
                "q": question or f"什么是「{concept}」？",
                "a": answer,
                "concept": concept,
                "source": _clean_text(card.get("source")),
                "evidence": _clean_text(card.get("evidence"))[:240],
                "topic_key": topic_key,
                "quality_score": quality_score(card),
                "_order": idx,
            }
        )
    scored.sort(key=lambda item: (-item["quality_score"], item["_order"]))

    picked: list[dict] = []
    seen_keys: list[str] = []
    for item in scored:
        if _is_near_duplicate(item["topic_key"], seen_keys):
            continue
        seen_keys.append(item["topic_key"])
        picked.append(item)
        if len(picked) >= n:
            break

    for rank, item in enumerate(picked, 1):
        item["rank"] = rank
        item.pop("_order", None)
    return picked


# ---------------------------------------------------------------------------
# 简报摘要 / 策略结论解析
# ---------------------------------------------------------------------------
def build_briefing(briefing: dict | None) -> dict:
    """从 kb_daily_briefing.json 提炼简报摘要（标题/篇数/要点 highlights）。"""
    briefing = briefing if isinstance(briefing, dict) else {}
    docs = [d for d in (briefing.get("docs") or []) if isinstance(d, dict)]
    units_total = sum(len([u for u in (d.get("units") or []) if isinstance(u, dict)]) for d in docs)

    highlights: list[dict] = []
    seen_keys: list[str] = []
    for doc in docs:
        for unit in doc.get("units") or []:
            if not isinstance(unit, dict):
                continue
            concept = _clean_text(unit.get("concept") or unit.get("chapter"))
            key = normalize_topic(concept)
            if not key or _is_near_duplicate(key, seen_keys):
                continue
            seen_keys.append(key)
            highlights.append(
                {
                    "doc": _clean_text(doc.get("doc")),
                    "pages": doc.get("pages"),
                    "chapter": _clean_text(unit.get("chapter")),
                    "concept": concept,
                    "claim": _clean_text(unit.get("claim")),
                }
            )
            if len(highlights) >= BRIEFING_HIGHLIGHTS:
                break
        if len(highlights) >= BRIEFING_HIGHLIGHTS:
            break

    date = _clean_text(briefing.get("date"))
    if len(highlights) == 0:
        headline = "今日暂无新简报（简报文件缺失或为空）"
    else:
        headline = f"研报简报 {date or '—'}：{len(docs)} 篇研报 · {units_total} 条知识单元"
    return {
        "date": date,
        "generated_at": briefing.get("generated_at"),
        "docs_count": len(docs),
        "unit_count": units_total,
        "headline": headline,
        "highlights": highlights,
        "note": _clean_text(briefing.get("note")) or "研报摘要仅为内部研究输出，不构成投资建议",
    }


_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_MD_LIST = re.compile(r"^\s*(?:\d+[.、]|[-*])\s+(.+?)\s*$")
_MD_TABLE_ROW = re.compile(r"^\s*\|(.+?)\|\s*$")
_MD_TABLE_SEP = re.compile(r"^\s*\|[\s:\-|]+\|\s*$")


def parse_markdown(md_text: str) -> dict:
    """轻量 Markdown 解析：标题/分节/表格/列表项。纯函数，空文本安全。"""
    title = ""
    sections: list[dict] = []
    current = {"heading": "", "bullets": [], "tables": []}
    current_table: dict | None = None
    for raw in (md_text or "").splitlines():
        line = raw.rstrip()
        heading = _MD_HEADING.match(line)
        if heading:
            if not title and len(heading.group(1)) == 1:
                title = heading.group(2).strip()
            current = {"heading": heading.group(2).strip(), "bullets": [], "tables": []}
            sections.append(current)
            current_table = None
            continue
        row = _MD_TABLE_ROW.match(line)
        if row:
            if _MD_TABLE_SEP.match(line):  # 表头分隔行：跳过但不重置当前表格
                continue
            cells = [c.strip() for c in row.group(1).split("|")]
            if current_table is None:
                current_table = {"headers": cells, "rows": []}
                current["tables"].append(current_table)
            else:
                current_table["rows"].append(cells)
            continue
        current_table = None
        bullet = _MD_LIST.match(line)
        if bullet:
            current["bullets"].append(_clean_text(bullet.group(1)))
    return {"title": title, "sections": sections}


def _strip_md(text: str) -> str:
    return _clean_text(re.sub(r"\*\*|`", "", text or ""))


def parse_strategy_notes(md_text: str, path: str = "") -> dict:
    """从策略研究结论 Markdown 提炼要点（关键结论 / 教训 / 裁决 / 策略状态表）。"""
    parsed = parse_markdown(md_text)
    title = parsed["title"]
    points: list[str] = []
    status_table: list[dict] = []
    key_findings: list[dict] = []
    takeaway = ""

    for section in parsed["sections"]:
        heading = section["heading"]
        for table in section["tables"]:
            headers = [_strip_md(h) for h in table["headers"]]
            if "实验" in headers and "结论" in headers:
                for rows in table["rows"]:
                    row = [_strip_md(c) for c in rows]
                    if len(row) < 4:
                        continue
                    item = {"experiment": row[0], "window": row[1], "result": row[2], "conclusion": row[3]}
                    key_findings.append(item)
                    points.append(f"{row[0]}：{row[2]} → {row[3]}")
            if "ID" in headers and ("状态" in headers or "用途" in headers):
                for rows in table["rows"]:
                    row = [_strip_md(c) for c in rows]
                    if len(row) < 4:
                        continue
                    status_table.append({"id": row[0], "strategy": row[1], "status": row[2], "usage": row[3]})
        if "教训" in heading:
            for bullet in section["bullets"]:
                points.append(_strip_md(bullet))
        if "裁决" in heading:
            for bullet in section["bullets"]:
                points.append(_strip_md(bullet))
            if section["bullets"]:
                takeaway = _strip_md(section["bullets"][0])

    if not title:
        title = "策略研究结论"
    if not takeaway:
        takeaway = "策略线暂停新因子投入，保留事件提醒；研报知识库/学习卡产品线继续。"
    updates = []
    match = re.search(r"(\d{8})", Path(path).name)
    if match:
        d = match.group(1)
        updates.append(f"{d[:4]}-{d[4:6]}-{d[6:]}")
    return {
        "title": title,
        "path": str(path),
        "updated_at": updates[0] if updates else "",
        "points": points[:MAX_STRATEGY_POINTS],
        "takeaway": takeaway,
        "key_findings": key_findings[:5],
        "status_table": status_table,
    }


# ---------------------------------------------------------------------------
# 组装 / 生成 / 校验
# ---------------------------------------------------------------------------
def _cards_from_briefing_units(briefing: dict | None) -> list[dict]:
    """学习卡文件缺失时的兜底：把简报里的知识单元转成学习卡。"""
    cards: list[dict] = []
    for doc in (briefing or {}).get("docs") or []:
        if not isinstance(doc, dict):
            continue
        for unit in doc.get("units") or []:
            if not isinstance(unit, dict) or not _clean_text(unit.get("claim")):
                continue
            concept = _clean_text(unit.get("concept") or unit.get("chapter"))
            cards.append(
                {
                    "q": f"什么是「{concept}」？" if concept else "这条知识的核心结论是什么？",
                    "a": _clean_text(unit.get("claim")),
                    "concept": concept,
                    "source": f"《{_clean_text(unit.get('book'))}》 {_clean_text(unit.get('chapter'))} "
                    f"p{unit.get('page_start')}-{unit.get('page_end')}",
                    "evidence": _clean_text(unit.get("evidence")),
                }
            )
    return cards


def build_digest(
    cards_json: dict | None,
    briefing_json: dict | None,
    strategy_md: str,
    *,
    date: str | None = None,
    strategy_path: str = "",
    now: datetime | None = None,
) -> dict:
    """把三路输入组装成 digest 结构（纯函数，不读写磁盘）。"""
    now = now or datetime.now().astimezone()
    stamp = date or now.strftime("%Y%m%d")

    input_cards = []
    if isinstance(cards_json, dict):
        input_cards = [c for c in (cards_json.get("cards") or []) if isinstance(c, dict)]
        daily = cards_json.get("daily")
        if isinstance(daily, dict):
            input_cards = [daily] + input_cards
    fallbacks: list[str] = []
    if not input_cards:
        input_cards = _cards_from_briefing_units(briefing_json)
        if input_cards:
            fallbacks.append("学习卡文件缺失/为空，已改用简报知识单元兜底")

    picked = select_daily_cards(input_cards, DAILY_CARDS)
    if len(picked) < DAILY_CARDS:
        fallbacks.append(f"学习卡去重/过滤后仅 {len(picked)} 张（目标 {DAILY_CARDS} 张）")

    briefing = build_briefing(briefing_json)
    if not isinstance(briefing_json, dict):
        fallbacks.append("简报文件缺失/损坏，briefing 已降级为空摘要")

    strategy_notes = parse_strategy_notes(strategy_md, strategy_path)
    if not strategy_notes["points"]:
        fallbacks.append("策略研究结论文件缺失/无可解析要点，strategy_notes 已降级")

    return {
        "date": stamp,
        "cards": picked,
        "briefing": briefing,
        "strategy_notes": strategy_notes,
        "meta": {
            "generator": GENERATOR,
            "version": SCHEMA_VERSION,
            "generated_at": now.isoformat(timespec="seconds"),
            "cards_input": len(input_cards),
            "cards_selected": len(picked),
            "fallbacks": fallbacks,
            "note": "学习卡/简报/策略结论均为内部研究材料，不构成投资建议",
        },
    }


def run(
    *,
    cards_path: str | Path = DEFAULT_CARDS,
    briefing_path: str | Path = DEFAULT_BRIEFING,
    strategy_notes_path: str | Path = DEFAULT_STRATEGY_NOTES,
    out_path: str | Path = DEFAULT_OUT,
    date: str | None = None,
) -> dict:
    """读取输入→生成→写盘，返回 digest dict（输入缺失时降级，不抛异常）。"""
    cards_json, cards_err = read_json_tolerant(cards_path)
    briefing_json, briefing_err = read_json_tolerant(briefing_path)
    strategy_md, strategy_err = read_text_tolerant(strategy_notes_path)

    digest = build_digest(
        cards_json,
        briefing_json,
        strategy_md,
        date=date,
        strategy_path=str(strategy_notes_path),
    )
    for err in (cards_err, briefing_err, strategy_err):
        if err:
            digest["meta"]["fallbacks"].append(err)
    digest["meta"]["inputs"] = {
        "cards": str(cards_path),
        "briefing": str(briefing_path),
        "strategy_notes": str(strategy_notes_path),
    }
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(digest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    digest["meta"]["out"] = str(out)
    return digest


def check_digest(payload) -> list[str]:
    """校验 digest JSON 的 schema，返回错误列表（空列表=通过）。"""
    errors: list[str] = []
    if not isinstance(payload, dict):
        return [f"顶层必须是 object，实际 {type(payload).__name__}"]
    for key in REQUIRED_TOP_KEYS:
        if key not in payload:
            errors.append(f"缺少顶层字段 '{key}'")
    date = payload.get("date")
    if not isinstance(date, str) or not DATE_RE.match(date):
        errors.append(f"date 应为 YYYYMMDD，实际 {date!r}")

    cards = payload.get("cards")
    if not isinstance(cards, list):
        errors.append("cards 必须是数组")
    else:
        if not cards:
            errors.append("cards 不能为空（至少 1 张）")
        if len(cards) > DAILY_CARDS:
            errors.append(f"cards 最多 {DAILY_CARDS} 张，实际 {len(cards)}")
        seen_ranks, seen_topics = set(), set()
        for i, card in enumerate(cards):
            if not isinstance(card, dict):
                errors.append(f"cards[{i}] 必须是 object")
                continue
            for key in REQUIRED_CARD_KEYS:
                if key not in card:
                    errors.append(f"cards[{i}] 缺少字段 '{key}'")
            if not _clean_text(card.get("a")):
                errors.append(f"cards[{i}] 答案 a 为空")
            rank = card.get("rank")
            if rank in seen_ranks:
                errors.append(f"cards[{i}] rank 重复: {rank!r}")
            seen_ranks.add(rank)
            topic = card.get("topic_key")
            if topic in seen_topics:
                errors.append(f"cards[{i}] topic_key 重复: {topic!r}")
            seen_topics.add(topic)
            score = card.get("quality_score")
            if not isinstance(score, (int, float)) or isinstance(score, bool):
                errors.append(f"cards[{i}] quality_score 应为数值")
        ranks = [c.get("rank") for c in cards if isinstance(c, dict)]
        if ranks and sorted(ranks) != list(range(1, len(ranks) + 1)):
            errors.append(f"cards rank 应从 1 连续编号，实际 {ranks}")

    briefing = payload.get("briefing")
    if not isinstance(briefing, dict):
        errors.append("briefing 必须是 object")
    else:
        if not isinstance(briefing.get("headline"), str):
            errors.append("briefing.headline 必须是字符串")
        if not isinstance(briefing.get("highlights"), list):
            errors.append("briefing.highlights 必须是数组")

    notes = payload.get("strategy_notes")
    if not isinstance(notes, dict):
        errors.append("strategy_notes 必须是 object")
    else:
        if not isinstance(notes.get("title"), str) or not notes["title"]:
            errors.append("strategy_notes.title 必须是非空字符串")
        if not isinstance(notes.get("points"), list):
            errors.append("strategy_notes.points 必须是数组")

    meta = payload.get("meta")
    if not isinstance(meta, dict):
        errors.append("meta 必须是 object")
    else:
        if meta.get("generator") != GENERATOR:
            errors.append(f"meta.generator 应为 {GENERATOR}")
        if not isinstance(meta.get("fallbacks"), list):
            errors.append("meta.fallbacks 必须是数组")
    return errors


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
    parser = argparse.ArgumentParser(description="学习卡/简报/策略结论 → 门户每日学习摘要")
    parser.add_argument("command", nargs="?", default="run", choices=["run", "check"], help="run=生成（默认），check=校验")
    parser.add_argument("--check", action="store_true", dest="check_flag", help="等价于 check 子命令")
    parser.add_argument("--cards", default=str(DEFAULT_CARDS), help="学习卡 JSON 路径")
    parser.add_argument("--briefing", default=str(DEFAULT_BRIEFING), help="简报 JSON 路径")
    parser.add_argument("--strategy-notes", default=str(DEFAULT_STRATEGY_NOTES), help="策略研究结论 Markdown 路径")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="输出 digest JSON 路径")
    parser.add_argument("--date", default=None, help="覆盖日期 YYYYMMDD（默认当天）")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    return parser


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_stdout()
    args = build_parser().parse_args(argv)
    command = "check" if args.check_flag else args.command

    if command == "run":
        try:
            digest = run(
                cards_path=args.cards,
                briefing_path=args.briefing,
                strategy_notes_path=args.strategy_notes,
                out_path=args.out,
                date=args.date,
            )
        except Exception as exc:  # noqa: BLE001 - CLI 边界：写盘失败才退出非 0
            print(f"错误：生成失败: {exc}", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(digest, ensure_ascii=False, indent=2))
        else:
            print(
                f"生成成功：{digest['meta']['out']} | date={digest['date']} | "
                f"cards={len(digest['cards'])} | highlights={len(digest['briefing']['highlights'])} | "
                f"points={len(digest['strategy_notes']['points'])}"
            )
            for msg in digest["meta"]["fallbacks"]:
                print(f"降级提示：{msg}", file=sys.stderr)
        return 0

    payload, err = read_json_tolerant(args.out)
    if payload is None:
        print(f"校验失败：{err}", file=sys.stderr)
        return 1
    errors = check_digest(payload)
    if args.json:
        print(json.dumps({"ok": not errors, "errors": errors, "path": str(args.out)}, ensure_ascii=False, indent=2))
    elif errors:
        print(f"校验失败：{len(errors)} 个错误（{args.out}）", file=sys.stderr)
        for item in errors:
            print(f"  - {item}", file=sys.stderr)
    else:
        print(
            f"校验通过：{args.out} | date={payload.get('date')} | cards={len(payload.get('cards') or [])} | "
            f"highlights={len((payload.get('briefing') or {}).get('highlights') or [])} | "
            f"points={len((payload.get('strategy_notes') or {}).get('points') or [])}"
        )
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
