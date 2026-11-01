# -*- coding: utf-8 -*-
"""学习卡修订落地（wave24 CL）：把 wave23 CJ 的 merge_fix 14 / drop 10 执行到学习卡。

用户已授权执行；安全流程：
  dry-run（默认）：预览 merge_fix 的 before→after 替换/新增效果、drop 的移除/合并目标、前后数量；
  --apply：备份 kb_learning_cards.json → merge_fix（按 concept 匹配：存在则替换、不存在则新增）→
           drop（若概念已存在于学习卡则仅记录合并说明、**不删除他卡**；不存在则为 no-op，不再新增）；
           写后校验（schema/数量/顺序/无新增重复 concept）→ 刷新 learning_digest → 输出操作清单+回滚说明。

输入：
  research/reports/card_revisions_20261101.json（24 条修订建议：final_action=merge_fix/drop）
  portal/static/kb_learning_cards.json（现有 56 张）
输出：
  portal/static/kb_learning_cards.json（--apply 时写）
  research/reports/kb_learning_cards.20261101_bak2.json（--apply 写前备份，写范围指定名）
  research/reports/card_revision_apply_20261101.{json,md}
  portal/static/kb_learning_digest.json（刷新）
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
R = REPO / "research" / "reports"
STATIC = REPO / "portal" / "static"
REVISIONS = R / "card_revisions_20261101.json"
CARDS = STATIC / "kb_learning_cards.json"
DIGEST = STATIC / "kb_learning_digest.json"
DIGEST_SCRIPT = REPO / "tools" / "kb_corpus" / "learning_digest.py"
BAK2 = R / "kb_learning_cards.20261101_bak2.json"
OUT_JSON = R / "card_revision_apply_20261101.json"
OUT_MD = R / "card_revision_apply_20261101.md"
DATE = "20261101"
SCHEMA_VERSION = 1
GENERATOR = "tools/kb_corpus/card_revision_apply.py"
CARD_FIELDS = ("q", "a", "concept", "source", "topic", "evidence")
DISCLAIMER = "本操作按用户授权执行学习卡修订落地；备份与回滚路径见报告，不构成投资建议。"

_NOISE = re.compile(r"[\s\u3000（）()【】\[\]《》「」『』“”\"'’·、，,。.：:；;？?！!—-]+")

# 学习卡 topic 词表（与 card_merge.THEME_TO_TOPIC 保持一致；映射不到时沿用主题名）
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


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8-sig"))


def sha(p: Path) -> str | None:
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        return None


def normalize(text) -> str:
    return _NOISE.sub("", str(text or "")).lower()


def clip(text, limit: int) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= limit else s[:limit].rstrip() + "…"


# ---------------------------------------------------------------------------
# 纯函数：修订卡构建 / 计划 / 校验
# ---------------------------------------------------------------------------
def revised_card(revision: dict) -> dict:
    """把一条 merge_fix 建议合成学习卡（q/a/source 用建议 after，其余保留原值）。"""
    o = revision.get("original") or {}
    q = o.get("question") or ""
    a = o.get("answer") or ""
    source = o.get("source_ref") or ""
    for action in revision.get("actions") or []:
        after = action.get("after")
        if action.get("action") != "merge_fix":
            continue
        field = action.get("field")
        if isinstance(after, dict):
            q = after.get("q") or q
            a = after.get("a") or a
        elif field == "source_ref" and isinstance(after, str):
            source = after
        elif field == "a" and isinstance(after, str):
            a = after
    theme = str(revision.get("theme") or "")
    return {
        "q": str(q).strip(),
        "a": str(a).strip(),
        "concept": str(revision.get("concept") or "").strip(),
        "source": str(source).strip(),
        "topic": THEME_TO_TOPIC.get(theme, theme or "其他"),
        "evidence": str(o.get("evidence") or "").strip(),
    }


def build_plan(revisions: dict | None, existing_cards: list[dict]) -> dict:
    """生成落地计划（纯函数）：merge_fix→replace/add，drop→skip_merge/noop。"""
    revisions = revisions if isinstance(revisions, dict) else {}
    existing = [c for c in (existing_cards or []) if isinstance(c, dict)]
    by_concept = {}
    for i, card in enumerate(existing):
        by_concept.setdefault(normalize(card.get("concept")), i)
    resulting = [dict(c) for c in existing]
    ops = []
    added = replaced = drop_skipped = drop_noop = 0
    for revision in revisions.get("revisions") or []:
        if not isinstance(revision, dict):
            continue
        action = revision.get("final_action")
        concept = revision.get("concept")
        key = normalize(concept)
        if action == "merge_fix":
            card = revised_card(revision)
            if key in by_concept:
                idx = by_concept[key]
                before = dict(resulting[idx])
                resulting[idx] = card
                replaced += 1
                ops.append({"op": "replace", "revision_id": revision.get("revision_id"),
                            "review_id": revision.get("review_id"), "concept": concept,
                            "index": idx, "before": before, "after": card})
            else:
                by_concept[key] = len(resulting)
                resulting.append(card)
                added += 1
                ops.append({"op": "add", "revision_id": revision.get("revision_id"),
                            "review_id": revision.get("review_id"), "concept": concept,
                            "index": len(resulting) - 1, "after": card})
        elif action == "drop":
            idx = by_concept.get(key)
            if idx is not None and idx < len(existing):
                target = existing[idx]
                drop_skipped += 1
                ops.append({"op": "drop_merge_existing", "revision_id": revision.get("revision_id"),
                            "review_id": revision.get("review_id"), "concept": concept,
                            "kept_card_concept": target.get("concept"),
                            "note": "概念已存在于学习卡，按「不删他卡」保留已有卡，不新增本候选"})
            elif idx is not None:
                drop_noop += 1
                ops.append({"op": "drop_skip_absent", "revision_id": revision.get("revision_id"),
                            "review_id": revision.get("review_id"), "concept": concept,
                            "note": "同批主卡已通过 merge_fix 写入，本重复候选不新增（no-op）"})
            else:
                drop_noop += 1
                ops.append({"op": "drop_skip_absent", "revision_id": revision.get("revision_id"),
                            "review_id": revision.get("review_id"), "concept": concept,
                            "note": "候选未合并进学习卡（批内重复非主卡），无需移除，仅记录"})
        else:
            ops.append({"op": "keep_revise", "revision_id": revision.get("revision_id"),
                        "review_id": revision.get("review_id"), "concept": concept,
                        "note": "keep_revise 不在本次落地范围"})
    return {
        "ops": ops,
        "resulting_cards": resulting,
        "stats": {
            "before": len(existing),
            "after": len(resulting),
            "added": added,
            "replaced": replaced,
            "drop_merge_existing": drop_skipped,
            "drop_skip_absent": drop_noop,
            "delta": len(resulting) - len(existing),
        },
    }


def validate_result(resulting_cards: list[dict], before_cards: list[dict]) -> list[str]:
    """写后校验：schema/数量/顺序/无新增重复 concept。"""
    errors: list[str] = []
    before = [c for c in before_cards if isinstance(c, dict)]
    after = [c for c in resulting_cards if isinstance(c, dict)]
    if len(after) != len(resulting_cards):
        errors.append("存在非 object 卡片")
    for i, card in enumerate(after):
        for field in CARD_FIELDS:
            if not str(card.get(field) or "").strip():
                errors.append(f"cards[{i}] 缺字段 '{field}'")
    before_keys = {normalize(c.get("concept")) for c in before}
    # 顺序/不删他卡：现有卡（按 concept 归一化）必须仍在，数量不减少
    if len(after) < len(before):
        errors.append(f"卡片数减少：{len(before)} → {len(after)}（不应删除现有卡）")
    after_keys = {normalize(c.get("concept")) for c in after}
    missing = before_keys - after_keys
    if missing:
        errors.append(f"现有卡 concept 丢失：{sorted(missing)[:5]}")
    # 无新增重复 concept（允许现有重复如「涨停密码」保持原样）
    before_counts = Counter(normalize(c.get("concept")) for c in before)
    after_counts = Counter(normalize(c.get("concept")) for c in after)
    for key, count in after_counts.items():
        base = before_counts.get(key, 0)
        if base == 0 and count > 1:
            errors.append(f"新增 concept 重复：{key} x{count}")
        elif base > 0 and count > base:
            errors.append(f"concept 重复数增加：{key} {base}→{count}")
    return errors


# ---------------------------------------------------------------------------
# 执行 / 刷新 digest
# ---------------------------------------------------------------------------
def refresh_digest(runner=None) -> dict:
    """刷新 learning_digest（调用 learning_digest.py run）。"""
    cmd = [sys.executable, "-X", "utf8", str(DIGEST_SCRIPT), "run"]
    try:
        fn = runner or subprocess.run
        proc = fn(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                  timeout=300, cwd=str(REPO))
        return {"ok": int(getattr(proc, "returncode", 1)) == 0, "rc": int(proc.returncode),
                "stdout_tail": (getattr(proc, "stdout", "") or "")[-400:],
                "stderr_tail": (getattr(proc, "stderr", "") or "")[-300:]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "rc": -1, "error": f"{type(exc).__name__}: {exc}"}


def build(*, revisions_path: str | Path = REVISIONS, cards_path: str | Path = CARDS,
          backup_path: str | Path = BAK2, out_dir: str | Path = R,
          apply: bool = False, generated_at: str | None = None,
          refresh_runner=None) -> dict:
    """执行/预览修订落地。apply=False 时只读；apply=True 时备份+写+校验+刷新 digest。"""
    revisions = load(Path(revisions_path))
    cards_payload = load(Path(cards_path))
    before_cards = [c for c in (cards_payload.get("cards") or []) if isinstance(c, dict)]
    plan = build_plan(revisions, before_cards)
    validation = validate_result(plan["resulting_cards"], before_cards)
    report = {
        "schema_version": SCHEMA_VERSION,
        "date": DATE,
        "generated_at": generated_at or datetime.now().astimezone().isoformat(timespec="seconds"),
        "generator": GENERATOR,
        "mode": "apply" if apply else "dry-run",
        "apply": bool(apply),
        "source": {
            "revisions": str(revisions_path), "revisions_sha256": sha(Path(revisions_path)),
            "cards": str(cards_path), "cards_sha256_before": sha(Path(cards_path)),
            "backup_path": str(backup_path),
        },
        "plan": {"stats": plan["stats"], "ops": plan["ops"]},
        "validation": {"ok": not validation, "errors": validation},
        "digest": {"refreshed": False},
        "disclaimer": DISCLAIMER,
    }
    if apply:
        if validation:
            report["ok"] = False
            report["error"] = "预校验失败，拒绝写盘"
        else:
            # 1) 备份
            bak = Path(backup_path)
            bak.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(Path(cards_path), bak)
            report["source"]["backup_sha256"] = sha(bak)
            # 2) 写盘点（保留原顶层字段，更新 cards/generated_at/merge_info）
            payload = dict(cards_payload)
            payload["cards"] = plan["resulting_cards"]
            payload["generated_at"] = report["generated_at"]
            payload["revision_apply"] = {
                "applied_at": report["generated_at"],
                "source": str(revisions_path),
                "added": plan["stats"]["added"], "replaced": plan["stats"]["replaced"],
                "drop_merge_existing": plan["stats"]["drop_merge_existing"],
                "drop_skip_absent": plan["stats"]["drop_skip_absent"],
                "backup": str(bak),
            }
            Path(cards_path).write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
                                        encoding="utf-8")
            # 3) 写后校验（重新读取）
            written = load(Path(cards_path))
            post_errors = validate_result([c for c in (written.get("cards") or []) if isinstance(c, dict)],
                                          before_cards)
            report["validation"] = {"ok": not post_errors, "errors": post_errors,
                                    "post_write": True,
                                    "count_before": plan["stats"]["before"],
                                    "count_after": len(written.get("cards") or [])}
            # 4) 刷新 learning_digest
            report["digest"] = refresh_digest(refresh_runner)
            report["digest"]["digest_path"] = str(DIGEST)
            report["ok"] = not post_errors and report["digest"].get("ok", False)
            report["rollback"] = {
                "backup": str(bak),
                "instructions": f"如需回滚：copy /Y \"{bak}\" \"{cards_path}\"，再重跑 learning_digest.py run 刷新摘要",
            }
            report["source"]["cards_sha256_after"] = sha(Path(cards_path))
            report["source"]["bytes_after"] = Path(cards_path).stat().st_size
    # 写报告（写入 out_dir，默认即 research/reports；测试用 tmp 时不污染真实报告）
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    report_json = out / f"card_revision_apply_{DATE}.json"
    report_md = out / f"card_revision_apply_{DATE}.md"
    report_json.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    report_md.write_text(render_md(report), encoding="utf-8")
    report["_outputs"] = {"json": str(report_json), "md": str(report_md)}
    return report


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------
def render_md(report: dict) -> str:
    s = report["plan"]["stats"]
    mode = "APPLY（已执行）" if report["apply"] else "DRY-RUN（未写盘）"
    lines = [
        "# 学习卡修订落地报告（2026-11-01）",
        "",
        f"> 模式：**{mode}** · 生成于 {report['generated_at']}",
        f"> 数据源：`{report['source']['revisions']}`（sha256 `{str(report['source']['revisions_sha256'])[:16]}…`）",
        f"> 学习卡：`{report['source']['cards']}`（sha256 `{str(report['source']['cards_sha256_before'])[:16]}…`）",
        "",
        "## 一、前后数量",
        "",
        "| 项 | 数量 |",
        "|---|---|",
        f"| 修订前学习卡 | {s['before']} |",
        f"| merge_fix 新增 | {s['added']} |",
        f"| merge_fix 替换 | {s['replaced']} |",
        f"| drop·概念已存在（保留已有卡，不删） | {s['drop_merge_existing']} |",
        f"| drop·候选未合并（no-op） | {s['drop_skip_absent']} |",
        f"| **修订后学习卡** | **{s['after']}**（Δ{s['delta']:+d}） |",
        "| 预校验 | " + ("通过" if report["validation"]["ok"] else "失败") + " |",
        "",
        "## 二、操作清单（14 merge_fix + 10 drop）",
        "",
        "| # | 操作 | revision_id | 学习卡 review_id | concept | 说明 |",
        "|---|---|---|---|---|---|",
    ]
    for i, op in enumerate(report["plan"]["ops"], 1):
        note = op.get("note") or ""
        if op["op"] in ("add", "replace"):
            after = op.get("after") or {}
            note = f"{'新增' if op['op']=='add' else '替换'}：{clip(after.get('a'), 90)}"
        lines.append(f"| {i} | {op['op']} | `{op.get('revision_id')}` | `{op.get('review_id')}` | "
                     f"{op.get('concept')} | {note} |")
    if report["apply"]:
        lines += ["", "## 三、写后校验与回滚", "",
                  f"- 写后校验：**{'通过' if report['validation']['ok'] else '失败'}**"
                  + (f"（错误：{report['validation']['errors']}）" if report['validation']['errors'] else ""),
                  f"- 数量：{report['validation'].get('count_before')} → {report['validation'].get('count_after')}",
                  f"- 备份：`{report['source'].get('backup_path')}`"
                  f"（sha256 `{str(report['source'].get('backup_sha256'))[:16]}…`）",
                  f"- 回滚：{report['rollback']['instructions']}",
                  "", "## 四、learning_digest 刷新", "",
                  f"- rc={report['digest'].get('rc')} · {'成功' if report['digest'].get('ok') else '失败'}"
                  f" · `{report['digest'].get('digest_path')}`",
                  f"- stdout：{clip(report['digest'].get('stdout_tail'), 200)}"]
    else:
        lines += ["", "## 三、（dry-run）未写盘", "",
                  "- 本次为 dry-run：未备份/未写学习卡/未刷新 digest；确认后加 `--apply` 执行。"]
    lines += ["", "## 五、说明", "",
              "1. merge_fix 按 concept 匹配：现有学习卡不存在则新增（本次 14 条均为新增）；存在则原位替换。",
              "2. drop 按任务约束「不删他卡」：7 条概念已在现有卡→仅记录合并目标、保留已有卡；3 条为批内重复非主卡、未合并过→no-op。",
              "3. 校验规则：6 字段 schema / 数量不减少 / 现有 concept 不丢失 / 不新增重复 concept（现有「涨停密码」重复保持原样）。",
              "4. 写完刷新 `learning_digest.py run`（学习卡每日摘要随更新）。",
              "", f"> {report['disclaimer']}", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _ensure_utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover
            pass


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_stdout()
    parser = argparse.ArgumentParser(description="学习卡修订落地（merge_fix/drop；默认 dry-run）")
    parser.add_argument("command", nargs="?", default="build", choices=["build"])
    parser.add_argument("--revisions", default=str(REVISIONS))
    parser.add_argument("--cards", default=str(CARDS))
    parser.add_argument("--backup", default=str(BAK2))
    parser.add_argument("--out-dir", default=str(R))
    parser.add_argument("--apply", action="store_true", help="执行写盘（备份+校验+刷新 digest）")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = build(revisions_path=args.revisions, cards_path=args.cards, backup_path=args.backup,
                   out_dir=args.out_dir, apply=args.apply)
    s = report["plan"]["stats"]
    if args.json:
        print(json.dumps({k: v for k, v in report.items() if k != "_outputs"}, ensure_ascii=False, indent=2))
    else:
        print(f"[card_revision_apply] {report['mode']} | 学习卡 {s['before']} → {s['after']}"
              f"（新增 {s['added']} / 替换 {s['replaced']} / drop 保留 {s['drop_merge_existing']} / drop no-op {s['drop_skip_absent']}）")
        print(f"  预校验: {'通过' if report['validation']['ok'] else report['validation']['errors']}")
        if report.get("apply"):
            print(f"  写后校验: {'通过' if report['validation']['ok'] else report['validation']['errors']}")
            print(f"  备份: {report['source'].get('backup_path')} | digest rc={report['digest'].get('rc')}")
            print(f"  回滚: {report['rollback']['instructions']}")
        for name, path in (report.get("_outputs") or {}).items():
            print(f"  输出({name})：{path}")
    return 0 if report.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main())
