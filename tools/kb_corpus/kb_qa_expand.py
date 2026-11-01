# -*- coding: utf-8 -*-
"""问答样本扩量：32 问题库 → 10–15 条新样本（逐条 kb_qa.ask，串行+降级），合并原 5 条成 15–20 条。

流程：
  ① 读 `research/reports/kb_qa_questions_20261101.json`（32 条候选问题），按主题轮转挑选 10–15 条；
  ② 串行调用 `kb_qa.ask`（生成模式，默认 qwen3.5:9b，逐条 timeout 300s；整体带 deadline，
     超时/失败由 kb_qa 自身降级为规则摘录，本模块标注 fallback；deadline 用尽的余下条目记 skipped）；
  ③ 输出 `research/reports/kb_qa_samples_expanded_20261101.json`（问题/答案/引用/置信/是否降级 + 汇总：
     成功数/降级数/无依据数/平均引用数/耗时）；
  ④ **先备份** `portal/static/kb_qa_samples.json` 到 `research/reports/kb_qa_samples_backup_20261101.json`，
     再把原 5 条 + 新样本去重合并（合计 15–20 条）写回 `portal/static/kb_qa_samples.json`（保持原 schema）；
  ⑤ CLI：`run [--limit 12] [--dry-run]`；不做 git 操作。

成本：每个新样本 1 次本地 LLM 调用（12 条约 4–8 分钟，取决于 Ollama 负载）；`--dry-run` 只做选题/备份，不调 LLM。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEFAULT_QUESTIONS = REPO / "research" / "reports" / "kb_qa_questions_20261101.json"
OUT_EXPANDED = REPO / "research" / "reports" / "kb_qa_samples_expanded_20261101.json"
OUT_BACKUP = REPO / "research" / "reports" / "kb_qa_samples_backup_20261101.json"
PORTAL_SAMPLES = REPO / "portal" / "static" / "kb_qa_samples.json"
DEFAULT_LIMIT = 12
MIN_LIMIT = 10
MAX_LIMIT = 15
DEFAULT_MODEL = "qwen3.5:9b"
DEFAULT_TIMEOUT = 300
DEFAULT_TOPK = 8
DEFAULT_DEADLINE = 600
REFUSAL_MARK = "没有足够依据"


class ExpandError(RuntimeError):
    """扩量流程异常（问题库不可读等）。"""


def _load_kb_qa():
    path = Path(__file__).with_name("kb_qa.py")
    spec = importlib.util.spec_from_file_location("kb_qa_for_expand", path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise ExpandError(f"无法加载 kb_qa.py：{path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def norm_question(text: str) -> str:
    return re.sub(r"[\s，。；：、（）()【】\[\]“”\"'’‘·…—\-?？!！]+", "", str(text or ""))


# ---------------------------------------------------------------------------
# ① 选题：主题轮转
# ---------------------------------------------------------------------------
def load_questions(path: Path | str = DEFAULT_QUESTIONS) -> list[dict]:
    path = Path(path)
    if not path.exists():
        raise ExpandError(f"问题库文件不存在：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExpandError(f"问题库读取/解析失败：{path}（{exc}）") from exc
    questions = data.get("questions") if isinstance(data, dict) else None
    if not isinstance(questions, list):
        raise ExpandError(f"问题库结构不合法（缺少 questions[]）：{path}")
    return [q for q in questions if isinstance(q, dict) and q.get("question")]


def select_by_theme_rotation(questions: list[dict], limit: int = DEFAULT_LIMIT) -> list[dict]:
    """按主题轮转挑选（每轮每主题取 1 条，均衡覆盖），数量钳制 10..15。"""
    limit = max(MIN_LIMIT, min(MAX_LIMIT, int(limit)))
    themes: list[str] = []
    grouped: dict[str, list[dict]] = {}
    for q in questions:
        theme = q.get("theme") or "其他/综合"
        if theme not in grouped:
            grouped[theme] = []
            themes.append(theme)
        grouped[theme].append(q)
    selected: list[dict] = []
    idx = 0
    while len(selected) < limit:
        moved = False
        for theme in themes:
            pool = grouped.get(theme) or []
            if idx < len(pool):
                selected.append(pool[idx])
                moved = True
                if len(selected) >= limit:
                    break
        if not moved:
            break
        idx += 1
    return selected


# ---------------------------------------------------------------------------
# ② 逐条生成（串行 + 降级 + deadline）
# ---------------------------------------------------------------------------
def _classify(result: dict) -> dict:
    answer = str(result.get("answer") or "")
    confidence = result.get("confidence") or {}
    fallback = bool(result.get("fallback"))
    no_evidence = (confidence.get("level") == "none") or (REFUSAL_MARK in answer)
    return {"fallback": fallback, "no_evidence": bool(no_evidence),
            "success": bool(answer) and not fallback and not no_evidence}


def run_expand(questions: list[dict], ask_fn=None, model: str = DEFAULT_MODEL,
               timeout: float = DEFAULT_TIMEOUT, topk: int = DEFAULT_TOPK,
               deadline_seconds: float = DEFAULT_DEADLINE, clock=time.monotonic) -> dict:
    """串行对新样本调用 kb_qa.ask；返回 expanded payload（不写盘）。"""
    if ask_fn is None:
        qa = _load_kb_qa()
        ask_fn = qa.ask
    started = clock()
    samples: list[dict] = []
    stats = {"requested": len(questions), "success": 0, "fallback": 0,
             "no_evidence": 0, "skipped": 0, "errors": []}
    citations_total = 0
    for q in questions:
        remaining = deadline_seconds - (clock() - started)
        if remaining <= 1:
            stats["skipped"] += 1
            continue
        item_started = clock()
        try:
            result = ask_fn(q["question"], topk=topk, model=model,
                            timeout=max(5.0, min(float(timeout), remaining)))
        except Exception as exc:  # noqa: BLE001 - 失败降级记录，不中断批量
            result = {"question": q["question"], "answer": None, "citations": [],
                      "confidence": {"level": "none", "reasons": ["调用失败"]},
                      "fallback": True, "error": f"{type(exc).__name__}: {exc}"}
        flags = _classify(result)
        citations = result.get("citations") or []
        citations_total += len(citations)
        if flags["fallback"]:
            stats["fallback"] += 1
        elif flags["no_evidence"]:
            stats["no_evidence"] += 1
        else:
            stats["success"] += 1
        if result.get("error"):
            stats["errors"].append(f"{q.get('id')}: {str(result['error'])[:100]}")
        samples.append({
            "id": "expanded_" + str(q.get("id") or f"q{len(samples) + 1}")[:32],
            "bank_id": q.get("id"), "question": q.get("question"),
            "theme": q.get("theme"), "type": q.get("type"),
            "source_unit_id": q.get("source_unit_id"),
            "answer": result.get("answer"),
            "citations": citations,
            "confidence": result.get("confidence") or {},
            "fallback": flags["fallback"],
            "fallback_reason": result.get("fallback_reason") or result.get("error"),
            "no_evidence": flags["no_evidence"],
            "success": flags["success"],
            "model": model,
            "elapsed_seconds": round(clock() - item_started, 2),
        })
    elapsed = round(clock() - started, 2)
    answered = [s for s in samples if s["answer"]]
    stats["answered"] = len(answered)
    stats["avg_citations"] = round(citations_total / len(answered), 2) if answered else 0.0
    stats["elapsed_seconds"] = elapsed
    stats["deadline_seconds"] = deadline_seconds
    return {
        "schema_version": 1, "kind": "kb_qa_samples_expanded",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "model": model, "topk": topk, "timeout_seconds": timeout,
        "selection": {"requested": len(questions),
                      "themes": list(dict.fromkeys(s.get("theme") for s in samples)),
                      "rule": "按主题轮转挑选 10–15 条（每轮每主题 1 条，覆盖 7 主题）"},
        "run": {"serial": True, "deadline_seconds": deadline_seconds,
                "requested": stats["requested"], "success": stats["success"],
                "fallback": stats["fallback"], "no_evidence": stats["no_evidence"],
                "skipped": stats["skipped"], "answered": stats["answered"],
                "avg_citations": stats["avg_citations"], "elapsed_seconds": elapsed,
                "errors": stats["errors"][:5]},
        "samples": samples,
        "disclaimer": "样本由本地 LLM 依据知识库材料生成，仅供研究参考，不构成投资建议；引用需人工复核。",
    }


# ---------------------------------------------------------------------------
# ③④ 备份 + 合并
# ---------------------------------------------------------------------------
def backup_original(original: Path | str = PORTAL_SAMPLES,
                    backup: Path | str = OUT_BACKUP) -> tuple[Path, bool]:
    """备份门户原样本（仅在备份不存在时执行，避免覆盖首次备份）；返回 (路径, 是否新建)。"""
    original, backup = Path(original), Path(backup)
    if backup.exists():
        return backup, False
    if not original.exists():
        return backup, False
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(original, backup)
    return backup, True


def load_original_samples(path: Path | str = PORTAL_SAMPLES) -> dict:
    path = Path(path)
    if not path.exists():
        return {"kind": "kb_qa_samples", "model": DEFAULT_MODEL, "samples": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"kind": "kb_qa_samples", "model": DEFAULT_MODEL, "samples": []}
    if not isinstance(data, dict) or not isinstance(data.get("samples"), list):
        return {"kind": "kb_qa_samples", "model": DEFAULT_MODEL, "samples": []}
    return data


def merge_samples(original: dict, expanded: dict) -> dict:
    """原样本 + 新样本按问题文本去重合并（原样本优先），保持原 schema 兼容。"""
    orig_samples = [s for s in original.get("samples") or [] if isinstance(s, dict)]
    seen = {norm_question(s.get("question")) for s in orig_samples if s.get("question")}
    merged = list(orig_samples)
    added = 0
    for s in expanded.get("samples") or []:
        if not isinstance(s, dict) or not s.get("question") or not s.get("answer"):
            continue
        key = norm_question(s.get("question"))
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append({**s, "category": s.get("category") or "expanded"})
        added += 1
    payload = {**original,
               "schema_version": original.get("schema_version", 1),
               "kind": original.get("kind", "kb_qa_samples"),
               "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
               "model": expanded.get("model") or original.get("model"),
               "expanded_from": expanded.get("generated_at"),
               "samples": merged}
    return {"payload": payload, "original_count": len(orig_samples),
            "added_count": added, "merged_count": len(merged)}


# ---------------------------------------------------------------------------
# 主流程 / CLI
# ---------------------------------------------------------------------------
def run_pipeline(questions_path: Path | str = DEFAULT_QUESTIONS,
                 limit: int = DEFAULT_LIMIT, model: str = DEFAULT_MODEL,
                 timeout: float = DEFAULT_TIMEOUT, topk: int = DEFAULT_TOPK,
                 deadline_seconds: float = DEFAULT_DEADLINE, ask_fn=None,
                 dry_run: bool = False, out_path: Path | str = OUT_EXPANDED,
                 backup_path: Path | str = OUT_BACKUP,
                 portal_path: Path | str = PORTAL_SAMPLES) -> dict:
    """选题 → （dry_run 跳过生成）→ 备份 → 合并写回门户；返回落盘结果与汇总。"""
    questions = load_questions(questions_path)
    selected = select_by_theme_rotation(questions, limit)
    backup_file, backup_created = backup_original(portal_path, backup_path)
    original = load_original_samples(portal_path)
    if dry_run:
        expanded = {
            "schema_version": 1, "kind": "kb_qa_samples_expanded",
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "model": model, "topk": topk, "timeout_seconds": timeout,
            "selection": {"requested": len(selected),
                          "themes": list(dict.fromkeys(q.get("theme") for q in selected)),
                          "rule": "按主题轮转挑选 10–15 条"},
            "run": {"serial": True, "requested": len(selected), "success": 0, "fallback": 0,
                    "no_evidence": 0, "skipped": 0, "answered": 0, "avg_citations": 0.0,
                    "elapsed_seconds": 0.0, "dry_run": True, "errors": []},
            "samples": [],
            "disclaimer": "dry-run：仅选题与备份，未调用 LLM。",
        }
    else:
        expanded = run_expand(selected, ask_fn=ask_fn, model=model, timeout=timeout,
                              topk=topk, deadline_seconds=deadline_seconds)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(expanded, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    merged = merge_samples(original, expanded)
    portal_path = Path(portal_path)
    portal_path.parent.mkdir(parents=True, exist_ok=True)
    portal_path.write_text(json.dumps(merged["payload"], ensure_ascii=False, indent=1) + "\n",
                           encoding="utf-8")
    return {"expanded": expanded, "expanded_path": str(out_path),
            "backup_path": str(backup_file), "backup_created": backup_created,
            "portal_path": str(portal_path), **merged}


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="问答样本扩量：问题库 → 新样本并合并门户样本（串行 LLM）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="扩量新样本并合并写回门户")
    run.add_argument("--questions", default=str(DEFAULT_QUESTIONS))
    run.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                     help=f"新样本条数 {MIN_LIMIT}..{MAX_LIMIT}（默认 {DEFAULT_LIMIT}）")
    run.add_argument("--model", default=DEFAULT_MODEL)
    run.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="单条 LLM 超时秒数")
    run.add_argument("--deadline", type=float, default=DEFAULT_DEADLINE, help="整体 deadline 秒数")
    run.add_argument("--topk", type=int, default=DEFAULT_TOPK)
    run.add_argument("--dry-run", action="store_true", help="只选题/备份，不调 LLM")
    run.add_argument("--out", default=str(OUT_EXPANDED))
    run.add_argument("--backup", default=str(OUT_BACKUP))
    run.add_argument("--portal", default=str(PORTAL_SAMPLES))
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd != "run":  # pragma: no cover
        return 2
    try:
        result = run_pipeline(args.questions, limit=args.limit, model=args.model,
                              timeout=args.timeout, topk=args.topk,
                              deadline_seconds=args.deadline, dry_run=args.dry_run,
                              out_path=args.out, backup_path=args.backup,
                              portal_path=args.portal)
    except ExpandError as exc:
        print(f"[kb_qa_expand] FAILED: {exc}", file=sys.stderr)
        return 1
    run = result["expanded"]["run"]
    print(f"[kb_qa_expand] requested={run['requested']} success={run['success']} "
          f"fallback={run['fallback']} no_evidence={run['no_evidence']} skipped={run['skipped']} "
          f"avg_citations={run['avg_citations']} elapsed={run['elapsed_seconds']}s dry_run={args.dry_run}")
    print(f"[kb_qa_expand] merged: original={result['original_count']} + new={result['added_count']} "
          f"= {result['merged_count']} samples")
    print(f"[kb_qa_expand] expanded={result['expanded_path']}")
    print(f"[kb_qa_expand] backup={result['backup_path']} (created={result['backup_created']})")
    print(f"[kb_qa_expand] portal={result['portal_path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
