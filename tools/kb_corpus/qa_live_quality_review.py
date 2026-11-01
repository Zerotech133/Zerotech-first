# -*- coding: utf-8 -*-
"""8091 线上 multi 质量抽检（真实 HTTP，非离线）：3 条 + 人工可审清单。

流程：
  ① 经 `http://127.0.0.1:8091/api/kb/qa?mode=multi&topk=8&cache=0` 真实调用 3 条（≤4 次 LLM）；
  ② 逐条：问题/答案/引用列表/引用可解析性/推断比例/guard 结果/fallback/耗时/答案完整度评分(1-5)；
     其中可解析性/推断比例/guard 通过由响应数据 + 本地 KB unit_id 集合 + guard 口径离线重算；
  ③ 与离线 v4 扩样基线（30 条：可答率 83.3% / guard 86.7% / 引用 2.27 / 推断 0.41）对比；
  ④ 输出 `research/reports/qa_live_quality_20261102.{json,md}`（逐条 + 汇总 + ☐通过/☐需修改）。

CLI：
  python tools/kb_corpus/qa_live_quality_review.py run --n 3
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
V3_BANK = REPO / "research" / "reports" / "kb_qa_bank_v3_20261101.json"
V4_SCALE_JSON = REPO / "research" / "reports" / "kb_qa_prompt_v4_scale_20261101.json"
OUT_JSON = REPO / "research" / "reports" / "qa_live_quality_20261102.json"
OUT_MD = REPO / "research" / "reports" / "qa_live_quality_20261102.md"
BASE_URL = "http://127.0.0.1:8091"
DEFAULT_N = 3
TOP_K = 8
TIMEOUT = 180
FALLBACK_PREFIX = "（质量守卫回退"


class ReviewError(RuntimeError):
    """线上质量抽检异常。"""


def _load_module(name: str, filename: str):
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise ReviewError(f"无法加载 {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def mod(name: str):
    key = f"{name}_for_live_quality"
    if key not in sys.modules:
        sys.modules[key] = _load_module(key, f"{name}.py")
    return sys.modules[key]


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def http_get(url: str, timeout: float = TIMEOUT) -> tuple[int, dict | str, float]:
    started = time.monotonic()
    req = urllib.request.Request(url, headers={"User-Agent": "qa-live-quality/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            status = resp.status
    except urllib.error.HTTPError as exc:
        body = exc.read()
        status = exc.code
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return 0, f"{type(exc).__name__}: {exc}", round(time.monotonic() - started, 2)
    elapsed = round(time.monotonic() - started, 2)
    try:
        payload = json.loads(body.decode("utf-8", errors="replace"))
    except (ValueError, UnicodeDecodeError):
        payload = body.decode("utf-8", errors="replace")[:500]
    return status, payload, elapsed


# ---------------------------------------------------------------------------
# 离线重算（引用可解析/推断比例/guard）
# ---------------------------------------------------------------------------
def load_unit_ids() -> set[str]:
    api = mod("kb_read_api")
    return {str(u["id"]).lower() for u in api.load_units() if u.get("id")}


def load_v4_baseline(path: Path | str = V4_SCALE_JSON) -> dict:
    path = Path(path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    v4 = ((data.get("comparison") or {}).get("v4")) or {}
    return {k: v4.get(k) for k in
            ("total", "answerable_rate", "guard_pass_rate", "fallback_rate",
             "avg_citations", "avg_citation_resolvable_rate", "avg_support_rate",
             "avg_inference_ratio", "avg_elapsed_seconds")}


def completeness_score(answer: str, citations: int, resolvable_rate: float,
                       guard_passed: bool, inference_ratio: float) -> int:
    """答案完整度 1-5 启发式（人工位保留在 human_score 字段）：

    1 基础分；+1 答案 ≥80 字；+1 可解析引用 ≥2；+1 guard 通过；+1 含 ≥2 条【事实】且推断比 ≤0.7。
    """
    score = 1
    if len(str(answer or "")) >= 80:
        score += 1
    if citations >= 2 and (resolvable_rate or 0) >= 1.0:
        score += 1
    if guard_passed:
        score += 1
    if str(answer or "").count("【事实】") >= 2 and (inference_ratio or 0) <= 0.7:
        score += 1
    return max(1, min(5, score))


# ---------------------------------------------------------------------------
# 单条构建
# ---------------------------------------------------------------------------
def build_sample(question: dict, status: int, payload: dict | str, elapsed: float,
                 unit_ids: set[str] | None = None) -> dict:
    env = payload if isinstance(payload, dict) else {}
    meta = env.get("meta") or {}
    data = env.get("data") or {}
    answer = str(data.get("answer") or "")
    citations = data.get("citations") or []
    guard_meta = meta.get("guard") or {}
    refusal = bool(data.get("refusal")) or "没有足够依据" in answer
    fallback = answer.startswith(FALLBACK_PREFIX) or bool(data.get("fallback"))
    guard = mod("kb_qa_guard")
    ids = unit_ids if unit_ids is not None else load_unit_ids()
    raw = {"answer": answer, "citations": citations,
           "retrieved": data.get("retrieved") or [], "fallback": fallback,
           "refusal": refusal}
    try:
        metrics = guard.compute_guard_metrics(raw, ids, None, marker_mode="segment")
    except Exception:  # noqa: BLE001 - 极端答案解析失败时给出空指标（不阻断抽检）
        metrics = {"guard_passed": False, "guard_issues": ["metrics_error"],
                   "citation_resolvable_rate": 0.0, "support_rate": 0.0,
                   "inference_ratio": 0.0, "distinct_cited_units": 0,
                   "citation_total": len(citations)}
    citation_resolvable = float(metrics.get("citation_resolvable_rate") or 0.0)
    infer = float(metrics.get("inference_ratio") or 0.0)
    guard_passed = bool(metrics.get("guard_passed")) and not fallback and not refusal
    if fallback:
        status_text = "fallback"
    elif refusal:
        status_text = "no_evidence"
    else:
        status_text = "success" if answer else "no_evidence"
    return {
        "id": question.get("id"), "category": question.get("category"), "theme": question.get("theme"),
        "question": question.get("question"),
        "http_status": status, "elapsed_seconds": elapsed,
        "meta": {k: meta.get(k) for k in ("mode", "k", "clusters", "topk", "llm_called",
                                          "prompt_version", "guard_marker", "citation_retry",
                                          "citation_retry_info", "cache_hit")},
        "guard_meta": guard_meta,
        "answer": answer, "answer_length": len(answer),
        "citations": citations, "citation_count": len(citations),
        "citation_resolvable_rate": citation_resolvable,
        "distinct_cited_units": metrics.get("distinct_cited_units"),
        "support_rate": metrics.get("support_rate"),
        "inference_ratio": infer,
        "guard_passed_recomputed": guard_passed,
        "guard_issues": metrics.get("guard_issues") or [],
        "fallback": fallback, "refusal": refusal, "status": status_text,
        "completeness_score": completeness_score(answer, len(citations),
                                                 citation_resolvable, guard_passed, infer),
        "human_score": None,
        "verdict": ("☐通过 ☐需修改" if status == 200 else "☐通过 ☐需修改（HTTP 异常）"),
    }


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------
def summarize(samples: list[dict], baseline: dict) -> dict:
    total = len(samples)
    ok = [s for s in samples if s.get("http_status") == 200]
    success = [s for s in ok if s.get("status") == "success"]
    fallback = [s for s in ok if s.get("status") == "fallback"]
    no_ev = [s for s in ok if s.get("status") == "no_evidence"]
    guard_pass = [s for s in ok if s.get("guard_passed_recomputed")]
    avg_cit = round(sum(s.get("citation_count") or 0 for s in ok) / len(ok), 2) if ok else 0.0
    avg_resolvable = round(sum(float(s.get("citation_resolvable_rate") or 0) for s in ok) / len(ok), 4) if ok else 0.0
    avg_infer = round(sum(float(s.get("inference_ratio") or 0) for s in ok) / len(ok), 4) if ok else 0.0
    avg_elapsed = round(sum(float(s.get("elapsed_seconds") or 0) for s in ok) / len(ok), 2) if ok else 0.0
    avg_score = round(sum(s.get("completeness_score") or 0 for s in ok) / len(ok), 2) if ok else 0.0
    live = {"total": total, "http_200": len(ok), "success": len(success),
            "fallback": len(fallback), "no_evidence": len(no_ev),
            "answerable_rate": round(len(success) / total, 4) if total else 0.0,
            "guard_pass_rate": round(len(guard_pass) / total, 4) if total else 0.0,
            "fallback_rate": round(len(fallback) / total, 4) if total else 0.0,
            "avg_citations": avg_cit, "avg_citation_resolvable_rate": avg_resolvable,
            "avg_inference_ratio": avg_infer, "avg_elapsed_seconds": avg_elapsed,
            "avg_completeness_score": avg_score}
    compare = {}
    for key, bkey in (("answerable_rate", "answerable_rate"),
                      ("guard_pass_rate", "guard_pass_rate"),
                      ("fallback_rate", "fallback_rate"),
                      ("avg_citations", "avg_citations"),
                      ("avg_citation_resolvable_rate", "avg_citation_resolvable_rate"),
                      ("avg_inference_ratio", "avg_inference_ratio"),
                      ("avg_elapsed_seconds", "avg_elapsed_seconds")):
        if baseline.get(bkey) is not None:
            compare[key] = {"live": live[key], "offline_v4": baseline.get(bkey),
                            "delta": round(float(live[key]) - float(baseline.get(bkey)), 4)}
    fixes = []
    if len(ok) < total:
        fixes.append(f"存在 HTTP 非 200 响应（{total - len(ok)} 条），需查 8091 稳定性。")
    if live["guard_pass_rate"] < 0.5:
        fixes.append(f"线上 guard 通过率 {live['guard_pass_rate']:.0%} 偏低（离线 v4 基线 {baseline.get('guard_pass_rate', 0):.0%}），需抽答案人工复核/调 prompt。")
    if live["avg_citation_resolvable_rate"] < 0.9:
        fixes.append(f"引用可解析率 {live['avg_citation_resolvable_rate']:.0%} <90%，需检查引用生成。")
    if live["avg_inference_ratio"] > 0.7:
        fixes.append(f"推断比例 {live['avg_inference_ratio']} 高于 guard 阈值 0.7，需强化事实摘录。")
    live_ok = (live["http_200"] == total and live["guard_pass_rate"] >= 0.5
               and live["avg_citation_resolvable_rate"] >= 0.9
               and live["avg_inference_ratio"] <= 0.7)
    verdict = "线上 multi 质量可接受（与离线 v4 基线方向一致，抽样 n=3）" if live_ok else "线上 multi 质量需修改（见需修项）"
    risks = [
        "抽样仅 3 条（LLM 成本约束），结论方向性参考；人工 1-5 评分位（human_score）待人工填写。",
        "引用可解析/推断比例由响应 + 本地 KB 口径离线重算，与 8091 内部 guard 口径可能有细微差异。",
        "线上 multi 单次 8–25s（真实 LLM），不适合高频抽检；建议按需/每日 1-3 条。",
    ]
    return {"live": live, "baseline_v4_offline": baseline, "compare": compare,
            "contains_human_review_slot": True,
            "verdict": verdict, "fixes_needed": fixes, "risks": risks,
            "live_ok": bool(live_ok)}


# ---------------------------------------------------------------------------
# 主流程 / 输出
# ---------------------------------------------------------------------------
def pick_questions(n: int = DEFAULT_N, path: Path | str = V3_BANK) -> list[dict]:
    path = Path(path)
    if not path.exists():
        raise ReviewError(f"v3 问题库不存在：{path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    qs = [q for q in data.get("questions") or [] if isinstance(q, dict) and q.get("question")]
    if not qs:
        raise ReviewError("v3 问题库 questions 为空")
    # 主题轮转取 n 条（不重复）
    by_theme: dict[str, list[dict]] = {}
    for q in qs:
        by_theme.setdefault(str(q.get("theme") or "其他"), []).append(q)
    picked, i = [], 0
    themes = list(by_theme)
    while len(picked) < n and themes:
        pool = by_theme[themes[i % len(themes)]]
        if pool:
            picked.append(pool.pop(0))
        i += 1
        if not any(by_theme.values()):
            break
    return picked[:n]


def run_review(n: int = DEFAULT_N, base_url: str = BASE_URL, fetch_fn=None,
               unit_ids: set[str] | None = None, questions: list[dict] | None = None) -> dict:
    started = time.monotonic()
    qs = questions if questions is not None else pick_questions(n)
    fetch = fetch_fn or http_get
    ids = unit_ids if unit_ids is not None else load_unit_ids()
    samples = []
    for q in qs:
        url = f"{base_url}/api/kb/qa?" + urllib.parse.urlencode(
            {"q": q["question"], "mode": "multi", "topk": str(TOP_K), "cache": "0"})
        status, payload, elapsed = fetch(url, TIMEOUT)
        samples.append(build_sample(q, status, payload, elapsed, ids))
    baseline = load_v4_baseline()
    summary = summarize(samples, baseline)
    return {"schema_version": 1, "kind": "qa_live_quality_review",
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "config": {"base_url": base_url, "n": len(samples), "mode": "multi",
                       "topk": TOP_K, "cache": "0", "llm_calls": len(samples),
                       "offline_baseline": "kb_qa_prompt_v4_scale_20261101（v4 扩样 30 条）"},
            "samples": samples, "summary": summary,
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "disclaimer": "线上质量抽检结果仅供评审参考，不构成投资建议；人工评分位待填。"}


def render_markdown(payload: dict) -> str:
    s = payload["summary"]
    live = s["live"]
    cfg = payload["config"]
    lines = [f"# 8091 线上 multi 质量抽检（{payload['generated_at'][:10]}）", "",
             f"> 真实 HTTP `{cfg['base_url']}/api/kb/qa?mode=multi&topk=8&cache=0` · {cfg['n']} 条 · "
             f"真实 LLM {cfg['llm_calls']} 次 · 与离线 v4 扩样基线对比。", "",
             "## 结论", "", f"**{s['verdict']}**", ""]
    if s["fixes_needed"]:
        lines += ["### 需修项", ""] + [f"- {x}" for x in s["fixes_needed"]] + [""]
    lines += ["## 汇总", "",
              "| 指标 | 线上（本次） | 离线 v4 基线（30 条） | Δ |", "|---|---|---|---|",
              f"| 可答率 | {live['answerable_rate']:.0%} | {s['baseline_v4_offline'].get('answerable_rate', 0):.0%} | "
              f"{s['compare'].get('answerable_rate', {}).get('delta')} |",
              f"| guard 通过率 | {live['guard_pass_rate']:.0%} | {s['baseline_v4_offline'].get('guard_pass_rate', 0):.0%} | "
              f"{s['compare'].get('guard_pass_rate', {}).get('delta')} |",
              f"| fallback 率 | {live['fallback_rate']:.0%} | {s['baseline_v4_offline'].get('fallback_rate', 0):.0%} | — |",
              f"| 平均引用数 | {live['avg_citations']} | {s['baseline_v4_offline'].get('avg_citations')} | — |",
              f"| 引用可解析率 | {live['avg_citation_resolvable_rate']:.0%} | 100% | — |",
              f"| 平均推断比例 | {live['avg_inference_ratio']} | {s['baseline_v4_offline'].get('avg_inference_ratio')} | — |",
              f"| 平均耗时 | {live['avg_elapsed_seconds']}s | {s['baseline_v4_offline'].get('avg_elapsed_seconds')}s | — |",
              f"| 完整度评分（启发式） | {live['avg_completeness_score']}/5 | — | — |", "",
              "## 逐条人工可审清单", ""]
    for i, sample in enumerate(payload["samples"], 1):
        lines += [f"### {i}. {sample['question']}", "",
                  f"- **HTTP/耗时**：{sample['http_status']} / {sample['elapsed_seconds']}s；"
                  f"prompt `{(sample['meta'] or {}).get('prompt_version')}`、"
                  f"guard `{(sample['meta'] or {}).get('guard_marker')}`、"
                  f"retry `{(sample['meta'] or {}).get('citation_retry')}`",
                  f"- **答案**（{sample['answer_length']} 字）：{str(sample['answer'])[:400]}",
                  f"- **引用**：{sample['citation_count']} 条（可解析率 {sample['citation_resolvable_rate']:.0%}，"
                  f"去重单元 {sample['distinct_cited_units']}）",
                  f"- **推断/guard**：推断比例 {sample['inference_ratio']}；"
                  f"guard 重算通过={sample['guard_passed_recomputed']}，issues={sample['guard_issues']}",
                  f"- **fallback/拒答**：{sample['fallback']} / {sample['refusal']}（状态 {sample['status']}）",
                  f"- **完整度评分**：{sample['completeness_score']}/5（人工 1-5 位：{sample['human_score']}）",
                  f"- **人工结论**：{sample['verdict']}", ""]
    lines += ["## 风险与说明", ""]
    lines += [f"- 风险：{r}" for r in s["risks"]]
    lines += ["", f"- {payload['disclaimer']}", f"- 总耗时 {payload['elapsed_seconds']}s"]
    return "\n".join(lines).rstrip() + "\n"


def write_outputs(payload: dict, out_json: Path | str = OUT_JSON,
                  out_md: Path | str = OUT_MD) -> tuple[Path, Path]:
    out_json, out_md = Path(out_json), Path(out_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    out_md.write_text(render_markdown(payload), encoding="utf-8")
    return out_json, out_md


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="8091 线上 multi 质量抽检")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="真实 HTTP 抽检 3 条并输出 JSON+MD")
    run.add_argument("--n", type=int, default=DEFAULT_N)
    run.add_argument("--base-url", default=BASE_URL)
    run.add_argument("--out-json", default=str(OUT_JSON))
    run.add_argument("--out-md", default=str(OUT_MD))
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd != "run":  # pragma: no cover
        return 2
    try:
        payload = run_review(args.n, args.base_url)
        paths = write_outputs(payload, args.out_json, args.out_md)
    except ReviewError as exc:
        print(f"[qa-live-quality] FAILED: {exc}", file=sys.stderr)
        return 1
    s = payload["summary"]
    print(f"[qa-live-quality] n={payload['config']['n']} http_200={s['live']['http_200']} "
          f"answerable={s['live']['answerable_rate']:.0%} guard={s['live']['guard_pass_rate']:.0%} "
          f"cites={s['live']['avg_citations']} infer={s['live']['avg_inference_ratio']}")
    print(f"[qa-live-quality] verdict={s['verdict']}")
    print(f"[qa-live-quality] json={paths[0]}")
    print(f"[qa-live-quality] md={paths[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
