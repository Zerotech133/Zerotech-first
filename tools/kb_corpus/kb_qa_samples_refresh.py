# -*- coding: utf-8 -*-
"""门户问答样例刷新到 v4 默认（task-112）。

背景：portal/static/kb_qa_samples.json（schema 2，12 条）仍是旧版 multi+guard 答案；
当前 `kb_qa.ask` 默认已切到 **v4 prompt + segment guard + 引用自检重试**（env 可回滚）。

本工具：
  ① 从 v3 问题库选 10-12 条（默认 8 条；LLM 慢时注明），串行调当前默认
     `kb_qa.ask(q, mode="multi", timeout=180)`（真实 LLM，不 mock）；
  ② 生成新样本 schema 2（保留 mode/guard/guard_passed/citation_resolvable_rate/
     inference_ratio/fallback 等；追加 `prompt_version` / `citation_retry_enabled`）；
  ③ **先备份**旧样例到 research/reports/kb_qa_samples_backup4_20261101.json，再更新
     portal/static/kb_qa_samples.json；
  ④ 输出 research/reports/kb_qa_samples_v4_20261101.{json,md}（逐条状态/统计）。

用法：
  python tools/kb_corpus/kb_qa_samples_refresh.py --limit 8 --timeout 180
  python tools/kb_corpus/kb_qa_samples_refresh.py --dry-run        # 只看选题，不调 LLM
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "kb_corpus"))

import kb_qa as qa  # noqa: E402

OUT = ROOT / "research" / "reports"
V3_BANK = OUT / "kb_qa_bank_v3_20261101.json"
SAMPLES_PATH = ROOT / "portal" / "static" / "kb_qa_samples.json"
BACKUP_PATH = OUT / "kb_qa_samples_backup4_20261101.json"
REPORT_JSON = OUT / "kb_qa_samples_v4_20261101.json"
REPORT_MD = OUT / "kb_qa_samples_v4_20261101.md"
DEFAULT_LIMIT = 8
DEFAULT_TIMEOUT = 180
DEFAULT_MODEL = qa.DEFAULT_MODEL
SCHEMA_VERSION = 2


# ---------------------------------------------------------------------------
# 选题 / 样本构造（纯函数，可单测）
# ---------------------------------------------------------------------------
def load_questions(path: Path | str = V3_BANK) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [q for q in (data.get("questions") or [])
            if isinstance(q, dict) and q.get("question")]


def select_questions(questions: list[dict], limit: int = DEFAULT_LIMIT) -> list[dict]:
    """按主题轮转选题，尽量覆盖不同 theme；不足则顺序补齐。"""
    if limit <= 0:
        return []
    by_theme: dict[str, list[dict]] = {}
    for q in questions:
        by_theme.setdefault(str(q.get("theme") or "其他"), []).append(q)
    picked: list[dict] = []
    idx = 0
    while len(picked) < min(limit, len(questions)):
        added = False
        for theme in list(by_theme):
            rows = by_theme[theme]
            if idx < len(rows) and len(picked) < limit:
                picked.append(rows[idx])
                added = True
        if not added:
            break
        idx += 1
    return picked[:limit]


def classify_status(result: dict) -> str:
    if bool(result.get("refusal")):
        return "refusal"
    conf = (result.get("confidence") or {}).get("level")
    if conf == "none" or not result.get("answer"):
        return "refusal"
    if bool(result.get("fallback")):
        return "fallback"
    return "success"


def build_sample(question: dict, result: dict) -> dict:
    """question（v3 库行）+ ask payload → 门户样本 schema 2（字段只增不减）。"""
    citations = result.get("citations") or []
    return {
        "id": question.get("id") or question.get("source_unit_id") or "q",
        "question": question.get("question"),
        "category": question.get("category"),
        "theme": question.get("theme"),
        "source_unit_id": question.get("source_unit_id"),
        "mode": result.get("mode") or "multi",
        "k": result.get("k") or result.get("topk"),
        "prompt_version": result.get("prompt_version") or qa.DEFAULT_PROMPT_VERSION,
        "citation_retry_enabled": bool(result.get("citation_retry", qa.DEFAULT_CITATION_RETRY)),
        "guard_marker_mode": result.get("guard_marker_mode") or qa.DEFAULT_MARKER_MODE,
        "answer": result.get("answer"),
        "citations": citations,
        "confidence": result.get("confidence") or {},
        "fallback": bool(result.get("fallback")),
        "refusal": bool(result.get("refusal")),
        "status": classify_status(result),
        "guard_enabled": result.get("guard_enabled"),
        "guard_passed": result.get("guard_passed"),
        "guard_action": result.get("guard_action"),
        "guard_issues": result.get("guard_issues") or [],
        "citation_valid": result.get("citation_valid") or [],
        "citation_invalid": result.get("citation_invalid") or [],
        "citation_resolvable_rate": result.get("citation_resolvable_rate"),
        "inference_ratio": result.get("inference_ratio"),
        "inference_heavy": result.get("inference_heavy"),
        "distinct_cited_units": result.get("distinct_cited_units"),
        "direct_fact_markers": result.get("direct_fact_markers"),
        "inference_markers": result.get("inference_markers"),
        "clusters": result.get("clusters") or [],
        "elapsed_seconds": result.get("elapsed_seconds"),
        "error": result.get("error"),
    }


# ---------------------------------------------------------------------------
# 刷新 / 写盘
# ---------------------------------------------------------------------------
def build_payload(samples: list[dict], *, model: str, topk: int = 8,
                  started_at: str | None = None) -> dict:
    stats = {"total": len(samples)}
    for key in ("success", "fallback", "refusal"):
        stats[key] = sum(1 for s in samples if s.get("status") == key)
    stats["total_citations"] = sum(len(s.get("citations") or []) for s in samples)
    stats["avg_citations"] = round(stats["total_citations"] / len(samples), 2) if samples else 0
    stats["avg_elapsed_seconds"] = round(
        sum(float(s.get("elapsed_seconds") or 0) for s in samples) / len(samples), 2) if samples else 0
    guarded = [s for s in samples if s.get("guard_passed") is not None]
    stats["guard_pass_rate"] = round(
        sum(1 for s in guarded if s.get("guard_passed")) / len(guarded), 4) if guarded else None
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "kb_qa_samples",
        "mode": "multi",
        "prompt_version": qa.DEFAULT_PROMPT_VERSION,
        "guard_marker_mode": qa.DEFAULT_MARKER_MODE,
        "citation_retry": bool(qa.DEFAULT_CITATION_RETRY),
        "guard": {"enabled": True, "on_fail": "fallback"},
        "generated_at": started_at or datetime.now().astimezone().isoformat(timespec="seconds"),
        "model": model,
        "endpoint": qa.OLLAMA_ENDPOINT,
        "topk": topk,
        "source": "v3 问题库（research/reports/kb_qa_bank_v3_20261101.json）；kb_qa.ask(mode=multi) 真实 LLM",
        "disclaimer": "问答样例由本地 LLM 基于知识库检索生成，仅供研究参考，不构成投资建议；引用页码以知识单元 page_start 为准。",
        "kb": {"units": None},
        "stats": stats,
        "samples": samples,
    }


def refresh(limit: int = DEFAULT_LIMIT, timeout: float = DEFAULT_TIMEOUT,
            model: str = DEFAULT_MODEL, ask_fn=None, *, dry_run: bool = False,
            samples_path: Path | str = SAMPLES_PATH, backup_path: Path | str = BACKUP_PATH,
            report_json: Path | str = REPORT_JSON, report_md: Path | str = REPORT_MD,
            bank_path: Path | str = V3_BANK) -> dict:
    questions = select_questions(load_questions(bank_path), limit)
    if dry_run:
        return {"dry_run": True, "n": len(questions),
                "questions": [{"id": q.get("id"), "question": q.get("question"),
                               "theme": q.get("theme")} for q in questions]}
    ask = ask_fn or (lambda q, **kw: qa.ask(q, **kw))
    samples = []
    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    for i, q in enumerate(questions, 1):
        t0 = time.monotonic()
        try:
            result = ask(q["question"], mode="multi", topk=8, timeout=timeout, model=model)
        except Exception as exc:  # noqa: BLE001 - 单条失败不中断整批
            result = {"answer": None, "citations": [], "confidence": {"level": "none"},
                      "fallback": True, "refusal": True, "error": f"{type(exc).__name__}: {exc}",
                      "mode": "multi", "k": 8, "elapsed_seconds": None}
        sample = build_sample(q, result)
        if sample.get("elapsed_seconds") is None:
            sample["elapsed_seconds"] = round(time.monotonic() - t0, 2)
        samples.append(sample)
        print(f"[refresh] {i}/{len(questions)} {sample['status']} "
              f"cit={len(sample['citations'])} {sample['elapsed_seconds']}s", flush=True)
    payload = build_payload(samples, model=model, topk=8, started_at=started_at)
    # 先备份旧样例（字节级）
    samples_path = Path(samples_path)
    backup_path = Path(backup_path)
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    if samples_path.exists():
        shutil.copy2(samples_path, backup_path)
        payload["backup"] = {"path": str(backup_path),
                             "sha_source": _sha256(samples_path)}
    samples_path.parent.mkdir(parents=True, exist_ok=True)
    samples_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    Path(report_json).write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    Path(report_md).write_text(render_md(payload), encoding="utf-8")
    payload["outputs"] = {"samples": str(samples_path), "backup": str(backup_path),
                          "report_json": str(report_json), "report_md": str(report_md)}
    return payload


def _sha256(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def render_md(payload: dict) -> str:
    stats = payload.get("stats") or {}
    L = ["# 门户问答样例刷新（v4 默认 · 真实 LLM）", ""]
    L.append(f"- 生成：{payload.get('generated_at')}；mode=multi；"
             f"prompt={payload.get('prompt_version')}；guard_marker={payload.get('guard_marker_mode')}；"
             f"citation_retry={payload.get('citation_retry')}；model={payload.get('model')}")
    L.append(f"- 样本数 {stats.get('total')}：success **{stats.get('success')}** / "
             f"fallback {stats.get('fallback')} / refusal {stats.get('refusal')}；"
             f"引用合计 {stats.get('total_citations')}（均 {stats.get('avg_citations')}）；"
             f"均耗时 {stats.get('avg_elapsed_seconds')}s；guard 通过率 {stats.get('guard_pass_rate')}")
    L.append("")
    L += ["| # | id | 问题 | 状态 | 引用数 | 引用可解析 | 推断占比 | guard | 耗时(s) |",
          "|---|---|---|---|---|---|---|---|---|"]
    for i, s in enumerate(payload.get("samples") or [], 1):
        q = str(s.get("question") or "")[:40]
        rr = s.get("citation_resolvable_rate")
        ir = s.get("inference_ratio")
        L.append(f"| {i} | {s.get('id')} | {q} | {s.get('status')} | {len(s.get('citations') or [])} | "
                 f"{'—' if rr is None else str(round(float(rr) * 100, 1)) + '%'} | "
                 f"{'—' if ir is None else str(round(float(ir) * 100, 1)) + '%'} | "
                 f"{s.get('guard_passed')} | {s.get('elapsed_seconds')} |")
    L += ["", "## 字段说明", "",
          "- 保留：mode / guard_enabled / guard_passed / guard_action / citation_resolvable_rate / "
          "inference_ratio / inference_heavy / fallback / refusal / status / citations 等；",
          "- 追加：`prompt_version`（v4）、`citation_retry_enabled`（引用自检重试：勾上）、"
          "`guard_marker_mode`（segment）；",
          "- 备份：旧样例先复制到 research/reports/kb_qa_samples_backup4_20261101.json（字节级）。", ""]
    return "\n".join(L)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="刷新门户问答样例到 v4 默认（真实 LLM）")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="样本数（默认 8，LLM 慢）")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="单条超时秒（默认 180）")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--dry-run", action="store_true", help="只选题、不调 LLM、不写盘")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    rep = refresh(limit=args.limit, timeout=args.timeout, model=args.model,
                  dry_run=args.dry_run)
    if args.dry_run:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
        return 0
    stats = rep["stats"]
    print(json.dumps({"ok": True, "prompt_version": rep["prompt_version"],
                      "stats": stats, "outputs": rep.get("outputs")},
                     ensure_ascii=False, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
