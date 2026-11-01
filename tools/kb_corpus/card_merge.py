# -*- coding: utf-8 -*-
"""学习卡合并工具（默认 dry-run；--apply 需人工审核勾选后使用）。

输入：
  research/reports/learning_card_proposals_20261101.json  wave11 审核稿（pending_review + review.checkboxes）
  portal/static/kb_learning_cards.json                    现有学习卡（只读；apply 时才写）

规则：
  - 仅 `review.checkboxes.pass=true` 的卡进入合并（revise/reject/未勾选 → 跳过）；
  - 按 concept 归一化去重：**现有卡优先**（同名跳过并记冲突）；同一批内重复只取第一张；
  - 保留现有卡顺序；新卡按审核稿主题分组顺序追加（主题内按原顺序）；
  - 新卡写成与现有卡一致的 schema：q/a/concept/source/topic/evidence（topic 按主题映射到现有词表）。

输出：
  research/reports/card_merge_preview_20261101.json   合并预览（统计/新增/跳过/冲突/merged_preview/validation）
  research/reports/card_merge_preview_20261101.md     人读预览
  research/reports/kb_learning_cards.<ts>.bak.json    仅 --apply 时生成（写前备份）

CLI::

    python tools/kb_corpus/card_merge.py merge --proposals ... --out-dir ...
    python tools/kb_corpus/card_merge.py merge --apply      # 真实写盘（需人工审核 + 审批；本任务未执行）

安全：默认 dry-run，不写 portal/static/kb_learning_cards.json；--apply 才备份+写+校验。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
STATIC = REPO / "portal" / "static"
PROPOSALS = REPO / "research" / "reports" / "learning_card_proposals_20261101.json"
BASE = STATIC / "kb_learning_cards.json"
OUT_DIR = REPO / "research" / "reports"
DATE_TAG = "20261101"
SCHEMA_VERSION = 1
GENERATOR = "tools/kb_corpus/card_merge.py"

DISCLAIMER = "合并工具仅做本地文件合并；卡片内容为研究/学习候选，不构成投资建议。"

_NOISE = re.compile(r"[\s\u3000（）()【】\[\]《》「」『』“”\"'’·、，,。.：:；;？?！!—-]+")

# 审核稿主题 → 现有学习卡 topic 词表（映射不到时沿用主题名）
THEME_TO_TOPIC = {
    "AI算力/硬件": "AI/科技",
    "半导体/电子": "AI/科技",
    "通信/互联网": "AI/科技",
    "新能源/电力": "新能源/电力",
    "金融/地产": "金融/资本市场",
    "宏观/政策": "制造/宏观",
    "机械/制造": "制造/宏观",
    "量学/交易": "量学/涨停",
}
CARD_FIELDS = ("q", "a", "concept", "source", "topic", "evidence")


# ---------------------------------------------------------------------------
# 纯函数
# ---------------------------------------------------------------------------
def normalize(text) -> str:
    return _NOISE.sub("", str(text or "")).lower()


def is_passed(card: dict) -> bool:
    review = card.get("review") if isinstance(card, dict) else None
    boxes = (review or {}).get("checkboxes") or {}
    return bool(boxes.get("pass"))


def skip_reason(card: dict) -> str:
    """未通过卡片的原因（用于统计）。"""
    review = card.get("review") if isinstance(card, dict) else None
    boxes = (review or {}).get("checkboxes") or {}
    if boxes.get("reject"):
        return "rejected"
    if boxes.get("revise"):
        return "needs_revision"
    if boxes.get("pass"):
        return "passed"
    return "not_reviewed"


def to_base_card(card: dict) -> dict:
    """审核稿卡片 → 现有学习卡 schema（q/a/concept/source/topic/evidence）。"""
    theme = str(card.get("theme") or "")
    topic = THEME_TO_TOPIC.get(theme, theme or "其他")
    return {
        "q": str(card.get("q") or "").strip(),
        "a": str(card.get("a") or "").strip(),
        "concept": str(card.get("concept") or "").strip(),
        "source": str(card.get("source") or "").strip(),
        "topic": topic,
        "evidence": str(card.get("evidence") or "").strip(),
    }


def plan_merge(base_cards: list[dict], proposal_cards: list[dict]) -> dict:
    """计算合并计划（纯函数，不写盘）：仅 pass、现有优先、批内去重、保留顺序。"""
    base_cards = [c for c in base_cards if isinstance(c, dict)]
    proposal_cards = [c for c in proposal_cards if isinstance(c, dict)]
    existing_keys = {normalize(c.get("concept")) for c in base_cards if normalize(c.get("concept"))}
    seen_new: set[str] = set()
    inserted: list[dict] = []
    skipped: list[dict] = []
    conflicts: list[dict] = []
    for card in proposal_cards:
        cid = card.get("id")
        concept = str(card.get("concept") or "")
        key = normalize(concept)
        if not is_passed(card):
            skipped.append({"proposal_id": cid, "concept": concept, "reason": skip_reason(card)})
            continue
        if not key:
            skipped.append({"proposal_id": cid, "concept": concept, "reason": "empty_concept"})
            continue
        if key in existing_keys:
            skipped.append({"proposal_id": cid, "concept": concept, "reason": "concept_exists"})
            conflicts.append({"proposal_id": cid, "concept": concept,
                              "resolution": "existing_wins",
                              "note": "现有学习卡已有同主题，保留现有卡"})
            continue
        if key in seen_new:
            skipped.append({"proposal_id": cid, "concept": concept, "reason": "duplicate_in_batch"})
            conflicts.append({"proposal_id": cid, "concept": concept,
                              "resolution": "first_wins", "note": "同批审核稿重复，保留先出现的一张"})
            continue
        seen_new.add(key)
        inserted.append({
            "theme": str(card.get("theme") or ""),
            "proposal_id": cid,
            "topic": THEME_TO_TOPIC.get(str(card.get("theme") or ""), str(card.get("theme") or "其他")),
            "card": to_base_card(card),
        })
    merged_cards = list(base_cards) + [item["card"] for item in inserted]
    reason_counts = Counter(s["reason"] for s in skipped)
    return {
        "inserted": inserted,
        "skipped": skipped,
        "conflicts": conflicts,
        "merged_cards": merged_cards,
        "stats": {
            "base_cards": len(base_cards),
            "proposal_cards": len(proposal_cards),
            "approved": sum(1 for c in proposal_cards if is_passed(c)),
            "would_add": len(inserted),
            "skipped_total": len(skipped),
            "skipped_not_reviewed": reason_counts.get("not_reviewed", 0),
            "skipped_rejected": reason_counts.get("rejected", 0),
            "skipped_needs_revision": reason_counts.get("needs_revision", 0),
            "skipped_concept_exists": reason_counts.get("concept_exists", 0),
            "skipped_duplicate_in_batch": reason_counts.get("duplicate_in_batch", 0),
            "resulting_cards": len(merged_cards),
        },
    }


def build_merged_payload(base: dict, merged_cards: list[dict], inserted: list[dict],
                         *, generated_at: str | None = None) -> dict:
    """生成写盘的合并后 payload（保留 base 其他字段；topics 计数更新）。"""
    generated_at = generated_at or datetime.now().astimezone().isoformat(timespec="seconds")
    payload = dict(base or {})
    topics = dict(payload.get("topics") or {})
    for item in inserted:
        topic = item.get("topic") or "其他"
        topics[topic] = topics.get(topic, 0) + 1
    payload.update({
        "generated_at": generated_at,
        "cards": merged_cards,
        "topics": topics,
        "merge_info": {
            "merged_at": generated_at,
            "generator": GENERATOR,
            "inserted": len(inserted),
            "inserted_proposal_ids": [item["proposal_id"] for item in inserted],
            "note": "由 card_merge.py 合并人工审核通过的 pending_review 卡；重新生成 learning_cards 时需保留本批卡。",
        },
    })
    return payload


def validate_merged(payload: dict, base_cards: list[dict], expected_count: int | None = None) -> list[str]:
    """写后校验：schema/数量/顺序（现有卡前缀不变）/concept 唯一/新卡字段完整。"""
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["合并后 payload 必须是 object"]
    cards = payload.get("cards")
    if not isinstance(cards, list) or not cards:
        return ["cards 必须是非空数组"]
    if expected_count is not None and len(cards) != expected_count:
        errors.append(f"卡片数量不符：期望 {expected_count}，实际 {len(cards)}")
    if len(cards) < len(base_cards):
        errors.append("合并后卡片数少于现有卡数（不应删除现有卡）")
    for i, card in enumerate(cards[:len(base_cards)]):
        if card != base_cards[i]:
            errors.append(f"现有卡顺序/内容被改动：index {i}")
            break
    seen: Counter = Counter()
    base_key_counts: Counter = Counter(normalize(c.get("concept")) for c in base_cards if isinstance(c, dict))
    for i, card in enumerate(cards):
        if not isinstance(card, dict):
            errors.append(f"cards[{i}] 不是 object")
            continue
        for key in CARD_FIELDS:
            if not str(card.get(key) or "").strip():
                errors.append(f"cards[{i}] 缺少字段 '{key}'")
        key = normalize(card.get("concept"))
        seen[key] += 1
        # 允许现有卡里已存在的重复（本工具不删现有卡），但不得新增超出基线数量的重复
        if seen[key] > 1 and seen[key] > base_key_counts.get(key, 0):
            errors.append(f"cards[{i}] 新增了重复 concept: {card.get('concept')}")
    return errors


def _sha1_file(path: Path) -> str | None:
    try:
        return hashlib.sha1(path.read_bytes()).hexdigest()
    except OSError:
        return None


# ---------------------------------------------------------------------------
# 预览渲染
# ---------------------------------------------------------------------------
def render_markdown(preview: dict) -> str:
    stats = preview["stats"]
    lines = [
        "# 学习卡合并预览（2026-11-01）",
        "",
        f"> 模式：**{'APPLY' if preview.get('apply') else 'DRY-RUN'}** · "
        f"生成于 {preview['generated_at']}",
        f"> 现有卡 **{stats['base_cards']}** 张 + 审核稿 **{stats['proposal_cards']}** 张"
        f"（已勾选通过 **{stats['approved']}** 张）→ 预计新增 **{stats['would_add']}** 张，"
        f"合并后 **{stats['resulting_cards']}** 张。",
        "",
        "## 一、合并策略",
        "",
        "| 规则 | 值 |",
        "|---|---|",
        "| 准入 | 仅 `review.checkboxes.pass=true` |",
        "| 去重 | concept 归一化相等（现有卡优先，批内先到先得） |",
        "| 顺序 | 保留现有卡原顺序；新卡按审核稿主题分组顺序追加 |",
        "| 写盘 | 默认 dry-run；`--apply` 才写 `portal/static/kb_learning_cards.json`（写前备份） |",
        "",
        "## 二、统计",
        "",
        "| 指标 | 数量 |",
        "|---|---|",
        f"| 现有卡 | {stats['base_cards']} |",
        f"| 审核稿卡 | {stats['proposal_cards']} |",
        f"| 已通过（pass=true） | {stats['approved']} |",
        f"| **预计新增** | **{stats['would_add']}** |",
        f"| 跳过·未审核 | {stats['skipped_not_reviewed']} |",
        f"| 跳过·需修改 | {stats['skipped_needs_revision']} |",
        f"| 跳过·驳回 | {stats['skipped_rejected']} |",
        f"| 跳过·concept 已存在（现有优先） | {stats['skipped_concept_exists']} |",
        f"| 跳过·批内重复 | {stats['skipped_duplicate_in_batch']} |",
        f"| 合并后总数 | {stats['resulting_cards']} |",
        "",
        "## 三、新增卡预览（按主题分组顺序）",
        "",
    ]
    if preview["inserted"]:
        lines += ["| # | 主题 | 审核稿ID | concept | Q | topic |", "|---|---|---|---|---|---|"]
        for i, item in enumerate(preview["inserted"], 1):
            card = item["card"]
            lines.append(f"| {i} | {item['theme']} | `{item['proposal_id']}` | {card['concept']} | "
                         f"{card['q']} | {card['topic']} |")
    else:
        lines.append("- 无新增卡（当前审核稿没有 pass=true 的卡片；人工审核勾选后再运行本工具）")
    lines += ["", "## 四、跳过清单", ""]
    if preview["skipped"]:
        lines += ["| 审核稿ID | concept | 原因 |", "|---|---|---|"]
        reason_cn = {"not_reviewed": "未审核/未勾选", "needs_revision": "需修改", "rejected": "驳回",
                     "concept_exists": "concept 已存在（现有优先）", "duplicate_in_batch": "批内重复",
                     "empty_concept": "concept 为空"}
        for s in preview["skipped"]:
            lines.append(f"| `{s['proposal_id']}` | {s['concept']} | {reason_cn.get(s['reason'], s['reason'])} |")
    else:
        lines.append("- 无")
    if preview["conflicts"]:
        lines += ["", "## 五、冲突与裁决", "", "| 审核稿ID | concept | 裁决 | 说明 |", "|---|---|---|---|"]
        for c in preview["conflicts"]:
            lines.append(f"| `{c['proposal_id']}` | {c['concept']} | {c['resolution']} | {c['note']} |")
    lines += ["", "## 六、合并后预览（前 5 张 / 尾部新增）", "",]
    merged = preview.get("merged_preview") or []
    for card in merged[:5]:
        lines.append(f"- {card.get('concept')}（{card.get('topic')}）")
    if len(merged) > 5:
        lines.append(f"- … 共 {len(merged)} 张")
    lines += ["", "## 七、使用步骤与风险提示", "",
              "1. 人工审核 `research/reports/learning_card_proposals_20261101.json`，在 `review.checkboxes` 勾选 pass/revise/reject；",
              "2. 先跑 dry-run 预览本文件，确认 新增/跳过/冲突 统计与插入顺序；",
              "3. 人工确认后执行 `--apply`：写前备份到 `research/reports/*.bak.json`，写后自动校验 schema/数量/顺序；",
              "4. ⚠ **learning_cards.py 重跑会覆盖 `kb_learning_cards.json`**：合并卡需在其后运行本工具，或后续给生成器加「保留人工合并卡」机制（本任务不改生成器）。"]
    if preview["validation"]["errors"]:
        lines += ["", "## ⚠ 校验错误", ""] + [f"- {e}" for e in preview["validation"]["errors"]]
    lines += ["", f"> {preview.get('disclaimer')}", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 读取/预览/apply
# ---------------------------------------------------------------------------
def read_json_tolerant(path: str | Path) -> tuple[dict | None, str | None]:
    p = Path(path)
    try:
        if not p.is_file():
            return None, f"文件不存在: {p}"
        return json.loads(p.read_text(encoding="utf-8")), None
    except Exception as exc:  # noqa: BLE001
        return None, f"读取/解析失败: {p}: {exc}"


def describe_file(path: str | Path) -> dict:
    p = Path(path)
    info = {"path": str(p), "exists": False, "mtime": None, "size": None, "sha1": None}
    try:
        if p.is_file():
            st = p.stat()
            info.update(exists=True, size=int(st.st_size), sha1=_sha1_file(p),
                        mtime=datetime.fromtimestamp(st.st_mtime).astimezone().isoformat(timespec="seconds"))
    except OSError:
        pass
    return info


def build_preview(
    proposals: dict | None,
    base: dict | None,
    *,
    proposals_path: str | Path = PROPOSALS,
    base_path: str | Path = BASE,
    date_tag: str = DATE_TAG,
    apply: bool = False,
    generated_at: str | None = None,
    errors: list[str] | None = None,
) -> dict:
    """组装预览（不写盘）。"""
    proposals = proposals if isinstance(proposals, dict) else {}
    base = base if isinstance(base, dict) else {}
    base_cards = [c for c in (base.get("cards") or []) if isinstance(c, dict)]
    proposal_cards = [c for c in (proposals.get("cards") or []) if isinstance(c, dict)]
    plan = plan_merge(base_cards, proposal_cards)
    merged_payload = build_merged_payload(base, plan["merged_cards"], plan["inserted"], generated_at=generated_at)
    validation = {"ok": True, "errors": []}
    validation_errors = list(errors or [])
    validation_errors.extend(validate_merged(merged_payload, base_cards, plan["stats"]["resulting_cards"]))
    validation = {"ok": not validation_errors, "errors": validation_errors}
    base_dup = {k: n for k, n in Counter(normalize(c.get("concept")) for c in base_cards).items() if n > 1}
    warnings = [
        "learning_cards.py 重跑会覆盖 kb_learning_cards.json；合并卡需在其后运行本工具或改造生成器保留人工卡。",
    ]
    if base_dup:
        warnings.append(f"现有学习卡已存在重复 concept：{base_dup}（本工具不删除现有卡，仅避免新增重复）")
    preview = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at or datetime.now().astimezone().isoformat(timespec="seconds"),
        "date": date_tag,
        "dry_run": not apply,
        "apply": bool(apply),
        "generator": GENERATOR,
        "sources": {"proposals": describe_file(proposals_path), "base": describe_file(base_path)},
        "policy": {
            "only_pass_true": True,
            "dedupe_by": "concept_normalized",
            "existing_wins": True,
            "preserve_existing_order": True,
            "insert_new_grouped_by_theme": True,
            "write_target": str(base_path),
            "backup_dir": str(OUT_DIR),
        },
        "stats": plan["stats"],
        "inserted": plan["inserted"],
        "skipped": plan["skipped"],
        "conflicts": plan["conflicts"],
        "merged_preview": plan["merged_cards"],
        "merged_payload": merged_payload,
        "validation": validation,
        "warnings": warnings,
        "disclaimer": DISCLAIMER,
    }
    return preview


def _write_preview(preview: dict, out_dir: Path, date_tag: str) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    jp = out_dir / f"card_merge_preview_{date_tag}.json"
    mp = out_dir / f"card_merge_preview_{date_tag}.md"
    jp.write_text(json.dumps(preview, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    mp.write_text(render_markdown(preview), encoding="utf-8")
    preview.setdefault("_outputs", {}).update({"json": str(jp), "md": str(mp)})
    return preview


def merge(
    proposals_path: str | Path = PROPOSALS,
    base_path: str | Path = BASE,
    out_dir: str | Path = OUT_DIR,
    date_tag: str = DATE_TAG,
    apply: bool = False,
    write_preview: bool = True,
) -> dict:
    """执行合并（默认 dry-run；apply=True 时才写 base）。"""
    proposals, p_err = read_json_tolerant(proposals_path)
    base, b_err = read_json_tolerant(base_path)
    errors = [e for e in (p_err, b_err) if e]
    preview = build_preview(
        proposals, base, proposals_path=proposals_path, base_path=base_path,
        date_tag=date_tag, apply=apply, errors=errors,
    )
    if write_preview:
        _write_preview(preview, Path(out_dir), date_tag)
    if apply:
        if not preview["validation"]["ok"]:
            preview["applied"] = False
            preview["apply_error"] = "校验未通过，拒绝写盘"
            return preview
        base_p = Path(base_path)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = Path(out_dir) / f"kb_learning_cards.{stamp}.bak.json"
        backup.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(base_p, backup)
            base_p.write_text(json.dumps(preview["merged_payload"], ensure_ascii=False, indent=1) + "\n",
                              encoding="utf-8")
        except OSError as exc:
            preview["applied"] = False
            preview["apply_error"] = f"写盘失败: {exc}"
            return preview
        written, w_err = read_json_tolerant(base_p)
        post_errors = validate_merged(written or {}, [c for c in (base or {}).get("cards") or [] if isinstance(c, dict)],
                                      preview["stats"]["resulting_cards"]) if not w_err else [w_err]
        preview["applied"] = not post_errors
        preview["apply_error"] = None if not post_errors else "; ".join(post_errors)
        preview["backup"] = str(backup)
        preview["post_write_validation"] = {"ok": not post_errors, "errors": post_errors}
    return preview


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
    parser = argparse.ArgumentParser(description="学习卡合并工具（默认 dry-run，--apply 才写盘）")
    parser.add_argument("command", nargs="?", default="merge", choices=["merge"], help="merge=合并（默认 dry-run）")
    parser.add_argument("--proposals", default=str(PROPOSALS), help="审核稿 JSON")
    parser.add_argument("--base", default=str(BASE), help="现有学习卡 JSON")
    parser.add_argument("--out-dir", default=str(OUT_DIR), help="预览/备份输出目录")
    parser.add_argument("--date", default=DATE_TAG, help="日期标签 YYYYMMDD")
    parser.add_argument("--apply", action="store_true", help="真实写盘（需人工审核；默认 dry-run）")
    parser.add_argument("--no-preview", action="store_true", help="不写预览文件（仅打印）")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    return parser


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_stdout()
    args = build_parser().parse_args(argv)
    preview = merge(
        proposals_path=args.proposals,
        base_path=args.base,
        out_dir=args.out_dir,
        date_tag=args.date,
        apply=args.apply,
        write_preview=not args.no_preview,
    )
    stats = preview["stats"]
    if args.json:
        out = {k: v for k, v in preview.items() if k not in ("merged_payload", "merged_preview")}
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        mode = "APPLY" if preview["apply"] else "DRY-RUN"
        print(f"[card_merge] {mode} | 现有 {stats['base_cards']} + 审核稿 {stats['proposal_cards']}"
              f"（通过 {stats['approved']}）→ 新增 {stats['would_add']} | "
              f"跳过 {stats['skipped_total']}（未审 {stats['skipped_not_reviewed']} / 需改 {stats['skipped_needs_revision']}"
              f" / 驳回 {stats['skipped_rejected']} / 已存在 {stats['skipped_concept_exists']}"
              f" / 批内重复 {stats['skipped_duplicate_in_batch']}）| 合并后 {stats['resulting_cards']}")
        for name, path in (preview.get("_outputs") or {}).items():
            print(f"  输出({name})：{path}")
        if preview["apply"]:
            print(f"  写盘：{'成功' if preview.get('applied') else '失败'} | 备份：{preview.get('backup')}"
                  f" | 校验：{(preview.get('post_write_validation') or {}).get('ok')}")
        else:
            print("  （dry-run，未写 portal/static/kb_learning_cards.json；人工审核后加 --apply）")
        for err in preview["validation"]["errors"]:
            print(f"  校验错误：{err}", file=sys.stderr)
    if args.apply and not preview.get("applied"):
        return 1
    return 0 if preview["validation"]["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
