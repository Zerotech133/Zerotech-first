# -*- coding: utf-8 -*-
"""问答样本 schema v2（wave17 支线 AX）：字段定义、从 v1 样本推断迁移、备份与校验。

背景（wave16 AU 发现）
----------------------
1. 有 3 条「无引用」样本与「有引用答案」未区分标注 → 下游无法判断「无引用是否预期」；
2. 幻觉启发式对小数字/列表序号（"1. **"、"2."）误报 → 需要 v2 规则（见 kb_qa_eval_v2.py）。

schema v2 新增字段（每个 sample）
--------------------------------
- `expected_refusal` (bool)：该样本**预期**是拒答/无依据回答（无引用 + 拒答语义 / category 无证据模拟）；
- `citations_required` (bool)：该样本是否要求引用（非拒答样本=True；拒答样本=False）；
- `answer_type`：`fact` | `inference` | `refusal`；
- `schema_version`：2（样本级）。

迁移规则（按旧样本推断，不改 answer/citations 原文）
--------------------------------------------------
- 无 citations 且（no_evidence=True 或 category in {no_evidence_simulated,…} 或拒答语义/confidence=none）
  → expected_refusal=True、citations_required=False、answer_type=refusal；
- 有 citations：
  - 含推断性措辞（预计/推断/意味着/可能/或将/有望/测算/假设/综合…）→ inference；
  - 否则 → fact；
  - expected_refusal=False、citations_required=True；
- 顶层 `schema_version` 由 1 → 2，并记录迁移规则/时间（可追溯）。

先备份：research/reports/kb_qa_samples_backup2_20261101.json（字节级复制）。

运行
----
C:\\项目\\py312\\python.exe -X utf8 tools/kb_corpus/kb_qa_schema_v2.py migrate
C:\\项目\\py312\\python.exe -X utf8 tools/kb_corpus/kb_qa_schema_v2.py validate
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "portal" / "static" / "kb_qa_samples.json"
BACKUP = ROOT / "research" / "reports" / "kb_qa_samples_backup2_20261101.json"
SCHEMA_VERSION = 2
ANSWER_TYPES = ("fact", "inference", "refusal")
REFUSAL_MARKERS = ("没有足够依据", "无法回答", "不足以回答", "未包含", "没有找到",
                   "知识库中没有", "无法从", "拒答", "材料不足", "无相关材料")
INFERENCE_MARKERS = ("预计", "推断", "推测", "意味着", "可能", "或将", "有望", "测算",
                     "假设", "综合来看", "因此", "判断", "认为", "预期", "趋势上")
REFUSAL_CATEGORIES = ("no_evidence_simulated", "no_evidence", "refusal")

MIGRATION_RULES = {
    "refusal": "无 citations 且(no_evidence=True 或 category∈无证据模拟 或 拒答语义/confidence=none)",
    "inference": "有 citations 且答案含推断措辞（预计/推断/意味着/可能/或将/有望/测算/假设/综合等）",
    "fact": "有 citations 且不含推断措辞",
    "citations_required": "非拒答样本=True；拒答样本=False",
    "untouched": "answer/citations/pre-existing fields 原样保留（仅新增字段与顶层 schema_version）",
}


def _has_any(text: str, markers) -> bool:
    t = str(text or "")
    return any(m in t for m in markers)


def infer_sample(sample: dict) -> dict:
    """按旧样本推断 v2 字段（纯函数，可单测）。"""
    citations = sample.get("citations") or []
    answer = str(sample.get("answer") or "")
    category = str(sample.get("category") or "")
    no_evidence = sample.get("no_evidence")
    confidence = sample.get("confidence") or {}
    level = str(confidence.get("level") or "")
    refusal_semantics = _has_any(answer, REFUSAL_MARKERS)
    expected_refusal = bool(
        not citations and (
            no_evidence is True
            or category in REFUSAL_CATEGORIES
            or refusal_semantics
            or level == "none"
        )
    ) or bool(refusal_semantics and not citations)
    citations_required = not expected_refusal
    if expected_refusal:
        answer_type = "refusal"
    elif citations and _has_any(answer, INFERENCE_MARKERS):
        answer_type = "inference"
    elif citations:
        answer_type = "fact"
    else:
        # 有答案但无引用且未被判拒答 → 保守按 fact（缺失引用由评估标记）
        answer_type = "fact"
    out = dict(sample)
    out.update({"expected_refusal": expected_refusal,
                "citations_required": citations_required,
                "answer_type": answer_type,
                "schema_version": SCHEMA_VERSION})
    return out


def migrate_payload(payload: dict) -> dict:
    """对整份样本文件做 v2 迁移（返回新 dict，不落盘）。"""
    data = dict(payload)
    data["samples"] = [infer_sample(s) for s in payload.get("samples") or []]
    data["schema_version"] = SCHEMA_VERSION
    data["schema_migration"] = {
        "from": 1, "to": SCHEMA_VERSION,
        "migrated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "rules": MIGRATION_RULES,
        "backup": str(BACKUP),
    }
    return data


def validate_samples(samples: list[dict]) -> list[str]:
    """校验 v2 必填字段/取值/一致性；返回错误列表（空=通过）。"""
    errors: list[str] = []
    seen: set[str] = set()
    for s in samples:
        sid = str(s.get("id"))
        if sid in seen:
            errors.append(f"样本 id 重复: {sid}")
        seen.add(sid)
        for field in ("expected_refusal", "citations_required", "answer_type", "schema_version"):
            if field not in s:
                errors.append(f"{sid}: 缺少字段 {field}")
        if not isinstance(s.get("expected_refusal"), bool):
            errors.append(f"{sid}: expected_refusal 必须为 bool")
        if not isinstance(s.get("citations_required"), bool):
            errors.append(f"{sid}: citations_required 必须为 bool")
        if s.get("answer_type") not in ANSWER_TYPES:
            errors.append(f"{sid}: answer_type 非法: {s.get('answer_type')!r}")
        if int(s.get("schema_version") or 0) != SCHEMA_VERSION:
            errors.append(f"{sid}: schema_version 应为 {SCHEMA_VERSION}")
        # 一致性：拒答 ↔ 不需要引用；拒答样本应有拒答语义或无引用
        if s.get("expected_refusal") is True and s.get("citations_required") is not False:
            errors.append(f"{sid}: expected_refusal=True 时 citations_required 应为 False")
        if s.get("expected_refusal") is False and s.get("citations_required") is not True:
            errors.append(f"{sid}: expected_refusal=False 时 citations_required 应为 True")
        if s.get("answer_type") == "refusal" and s.get("expected_refusal") is not True:
            errors.append(f"{sid}: answer_type=refusal 时 expected_refusal 应为 True")
        if s.get("expected_refusal") is True and not _has_any(str(s.get("answer") or ""),
                                                             REFUSAL_MARKERS):
            errors.append(f"{sid}: 拒答样本答案缺少拒答语义（需人工确认）")
        if s.get("answer_type") == "fact" and not (s.get("citations") or []):
            errors.append(f"{sid}: answer_type=fact 但无 citations")
    return errors


def migrate(samples_path: Path | str = SAMPLES, backup_path: Path | str = BACKUP,
            out_path: Path | str | None = None) -> dict:
    """备份旧文件 → 迁移 → 校验 → 写回；返回统计。"""
    src = Path(samples_path)
    payload = json.loads(src.read_text(encoding="utf-8"))
    backup = Path(backup_path)
    backup.parent.mkdir(parents=True, exist_ok=True)
    if not backup.exists():
        shutil.copy2(src, backup)          # 字节级备份（先备份，防不可逆）
    new_payload = migrate_payload(payload)
    errors = validate_samples(new_payload["samples"])
    out = Path(out_path) if out_path else src
    if errors:
        return {"ok": False, "errors": errors, "backup": str(backup), "written": None}
    out.write_text(json.dumps(new_payload, ensure_ascii=False, indent=1), encoding="utf-8")
    counts = {}
    for s in new_payload["samples"]:
        counts[s["answer_type"]] = counts.get(s["answer_type"], 0) + 1
    return {"ok": True, "errors": [], "backup": str(backup), "written": str(out),
            "n_samples": len(new_payload["samples"]),
            "answer_type_counts": counts,
            "expected_refusal": sum(1 for s in new_payload["samples"]
                                    if s["expected_refusal"]),
            "citations_required": sum(1 for s in new_payload["samples"]
                                      if s["citations_required"]),
            "schema_version": new_payload["schema_version"]}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def cmd_migrate(args) -> int:
    rep = migrate(args.samples, args.backup, args.out)
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    return 0 if rep["ok"] else 1


def cmd_validate(args) -> int:
    payload = json.loads(Path(args.samples).read_text(encoding="utf-8"))
    errors = validate_samples(payload.get("samples") or [])
    print(json.dumps({"ok": not errors, "n_samples": len(payload.get("samples") or []),
                      "schema_version": payload.get("schema_version"),
                      "errors": errors}, ensure_ascii=False, indent=1))
    return 0 if not errors else 1


def cmd_infer(args) -> int:
    payload = json.loads(Path(args.samples).read_text(encoding="utf-8"))
    out = [infer_sample(s) for s in payload.get("samples") or []]
    print(json.dumps(out if not args.summary else
                     [{k: s[k] for k in ("id", "expected_refusal", "citations_required",
                                         "answer_type")} for s in out],
                     ensure_ascii=False, indent=1))
    return 0


def _parse_args(argv):
    p = argparse.ArgumentParser(prog="kb_qa_schema_v2",
                                description="问答样本 schema v2：迁移/校验/推断")
    sub = p.add_subparsers(dest="cmd", required=True)
    pm = sub.add_parser("migrate", help="备份 v1 后迁移到 schema v2")
    pm.add_argument("--samples", default=str(SAMPLES))
    pm.add_argument("--backup", default=str(BACKUP))
    pm.add_argument("--out", default=None)
    pm.set_defaults(func=cmd_migrate)
    pv = sub.add_parser("validate", help="校验 schema v2 字段")
    pv.add_argument("--samples", default=str(SAMPLES))
    pv.set_defaults(func=cmd_validate)
    pi = sub.add_parser("infer", help="只推断字段不写盘")
    pi.add_argument("--samples", default=str(SAMPLES))
    pi.add_argument("--summary", action="store_true")
    pi.set_defaults(func=cmd_infer)
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
