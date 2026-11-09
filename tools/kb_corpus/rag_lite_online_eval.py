# -*- coding: utf-8 -*-
"""rag_lite prompt 线上 40 题回归（2026-11-09）。

目的
----
固定 W13 40 题（30 可答 + 10 不可答）与 W13 top5 上下文，只切换 `rag_lite` 的
prompt profile（`strict` vs `strict_nocite`），调用百炼线上模型，复用
`answer_eval.judge_one()` 的确定性指标，验证“拒答不带引用”规则在 rag_lite
口径下是否达到 citation >= 0.90 且不退化。

说明
----
- 这是 prompt 回归：检索上下文固定为 W13 ctx_ids，不重新跑 rag_lite 检索；
- `rag_lite` 使用 `[S1]` 引用，评分前转为 `[1]` 以复用 W13 judge；
- 不写生产库、不部署；只有 `--write` 才写 `research/reports/rag_lite_prompt_online_20261109.*`。

用法
----
    # 离线自检（不需要 key）
    python tools/kb_corpus/rag_lite_online_eval.py --dry-run

    # 线上 40 题（需要 DASHSCOPE_API_KEY 或本地 key 文件）
    python tools/kb_corpus/rag_lite_online_eval.py \
      --model qwen2.5-14b-instruct-a57a3521e4e0 \
      --base-url https://llm-4n3lsps262hujtx3.cn-beijing.maas.aliyuncs.com/compatible-mode/v1 \
      --write
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "kb_corpus"))
sys.path.insert(0, str(ROOT / "tools" / "finetune"))

import rag_lite  # noqa: E402
import bailian_eval as be  # noqa: E402

DATE = "20261109"
OUT_JSON = ROOT / "research" / "reports" / f"rag_lite_prompt_online_{DATE}.json"
OUT_MD = ROOT / "research" / "reports" / f"rag_lite_prompt_online_{DATE}.md"
PROFILES = ("strict", "strict_nocite")
TARGETS = {"citation_accuracy": 0.90, "groundedness": 0.60,
           "correct_refusal_rate": 0.80, "gold_similarity": 0.30,
           "overclaim_rate": 0.05, "length_anomaly_rate": 0.10}
HIGHER = ("citation_accuracy", "groundedness", "correct_refusal_rate", "gold_similarity")
S_REF = re.compile(r"\[\s*S\s*(\d+)\s*\]", re.IGNORECASE)


def normalize_s_refs(answer: str) -> str:
    """把 rag_lite 的 [S1] 归一成 W13 judge 可解析的 [1]。"""
    return S_REF.sub(r"[\1]", answer or "")


def to_hits(ctx: list[dict]) -> list[dict]:
    """W13 文档对象 -> rag_lite.build_prompt 需要的 hits（固定上下文）。"""
    return [{"video_id": str(c.get("doc_id") or ""),
             "kind": "w13",
             "title": str(c.get("doc_id") or ""),
             "text": str(c.get("text") or "")} for c in ctx]


def load_dataset() -> list[dict]:
    payload = be.load_w13()
    return be.build_dataset(payload)


def evaluate_profile(dataset: list[dict], profile: str, *, client, model: str,
                     limit: int = 0) -> dict:
    w13 = be._load_w13()
    items = dataset[:limit] if limit and limit > 0 else dataset
    results = []
    t0 = time.monotonic()
    for item in items:
        prompt = rag_lite.build_prompt(item["question"], to_hits(item["ctx"]), profile=profile)
        answer, error = "", None
        try:
            answer = be.call_model(client, model, prompt, temperature=0.0, max_tokens=180)
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
        judged = w13.judge_one(item, item["ctx"], normalize_s_refs(answer))
        judged["answer_raw"] = answer
        judged["answer_normalized"] = normalize_s_refs(answer)
        if error:
            # 错误行不得计分；空答案不能因 len<8 被当成合规拒答。
            judged["citation_ok"] = False
            judged["citation_valid"] = False
            judged["refused"] = False
            judged["error"] = error
        results.append(judged)
    agg = be.aggregate_results(results)["aggregate"]
    failures = [r["qid"] for r in results if not r.get("citation_ok")]
    return {"profile": profile, "results": results, "aggregate": agg,
            "failures": failures, "elapsed_s": round(time.monotonic() - t0, 2)}


def compare_profiles(strict: dict, nocite: dict) -> dict:
    base_agg = strict["aggregate"]
    new_agg = nocite["aggregate"]
    comparison = {}
    for key, target in TARGETS.items():
        cur = new_agg.get(key)
        base = base_agg.get(key)
        delta = round(cur - base, 4) if (cur is not None and base is not None) else None
        passed = bool(cur is not None and (
            cur >= target if key in HIGHER else cur <= target))
        comparison[key] = {"strict": base, "strict_nocite": cur,
                           "delta": delta, "target": target, "pass": passed}
    errors = int(new_agg.get("errors") or 0)
    gate = {
        "citation_accuracy": bool(comparison["citation_accuracy"]["pass"]),
        "groundedness": bool(comparison["groundedness"]["pass"]),
        "correct_refusal_rate": bool(comparison["correct_refusal_rate"]["pass"]),
        "gold_similarity": bool(comparison["gold_similarity"]["pass"]),
        "overclaim_rate": bool(comparison["overclaim_rate"]["pass"]),
        "length_anomaly_rate": bool(comparison["length_anomaly_rate"]["pass"]),
        "no_errors": errors == 0,
        "citation_not_worse": bool(comparison["citation_accuracy"]["delta"] is not None
                                   and comparison["citation_accuracy"]["delta"] >= 0),
    }
    gate["passed"] = all(gate.values())
    return {"comparison": comparison, "gate": gate, "errors": errors}


def dry_run(dataset: list[dict]) -> int:
    print(f"[rag_lite_online_eval] dry-run: {len(dataset)} 题（固定 W13 ctx）")
    for profile in PROFILES:
        lens = []
        for item in dataset:
            p = rag_lite.build_prompt(item["question"], to_hits(item["ctx"]), profile=profile)
            lens.append(len(p))
        print(f"  {profile}: prompts={len(lens)} avg_chars={sum(lens)/max(1,len(lens)):.0f} "
              f"has_refusal_guard={'资料不足，现有资料无法回答该问题。' in rag_lite.build_prompt('x', to_hits(dataset[0]['ctx']), profile=profile)}")
    print("  未调用 API；真实运行需 --model/--key-file 或 DASHSCOPE_API_KEY")
    return 0


def render_md(payload: dict) -> str:
    lines = [
        "# rag_lite 拒答不带引用 · 线上 40 题回归", "",
        f"- 生成时间：{payload['generated_at']}",
        f"- 模型：`{payload['api']['model']}`",
        f"- base_url：`{payload['api']['base_url']}`",
        f"- 数据集：{payload['dataset']['total']} 题（可答 {payload['dataset']['answerable']} + 不可答 {payload['dataset']['unanswerable']}）",
        "- 口径：固定 W13 top5 上下文，仅切换 rag_lite prompt；`[S1]` 评分前归一为 `[1]`。", "",
        "## 一、对比", "",
        "| 指标 | strict | strict_nocite | Δ | 目标 | 通过 |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for k, row in payload["comparison"].items():
        lines.append(f"| {k} | {row['strict']} | {row['strict_nocite']} | "
                     f"{row['delta']} | {row['target']} | {'✅' if row['pass'] else '❌'} |")
    lines += ["", f"- 综合门禁：**{'PASS' if payload['gate']['passed'] else 'FAIL'}**；"
                  f"winner=`{payload['winner'] or '无'}`；errors={payload.get('errors', 0)}", ""]
    lines += ["## 二、未通过题", ""]
    for qid in payload["profiles"]["strict_nocite"]["failures"]:
        row = next((r for r in payload["profiles"]["strict_nocite"]["results"] if r["qid"] == qid), {})
        lines.append(f"- `{qid}` answerable={row.get('answerable')} cited={row.get('cited')} "
                     f"answer={str(row.get('answer_raw') or '')[:160]}")
    lines += ["", "## 三、结论", "", payload["conclusion"], "",
              "> 本报告仅做 prompt 回归；检索上下文固定，未把结果外推到线上检索质量。"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:  # pragma: no cover
            pass
    ap = argparse.ArgumentParser(description="rag_lite prompt 线上 W13 40 题回归")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--model", default="")
    ap.add_argument("--base-url", default="")
    ap.add_argument("--key-file", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--out-json", default="")
    ap.add_argument("--out-md", default="")
    args = ap.parse_args(argv)

    dataset = load_dataset()
    if args.dry_run:
        return dry_run(dataset)

    key_file = Path(args.key_file) if args.key_file else None
    key = be.api_key(key_file)
    if not key:
        print("[rag_lite_online_eval] 缺少 DASHSCOPE_API_KEY 或 key 文件", file=sys.stderr)
        return 2
    if not args.model:
        print("[rag_lite_online_eval] 真实运行必须传 --model（部署实例 ID）", file=sys.stderr)
        return 2
    base_url = args.base_url or be.DEFAULT_BASE
    key_source = ("env:DASHSCOPE_API_KEY" if (not key_file and (__import__("os").environ.get("DASHSCOPE_API_KEY") or "").strip())
                  else str(key_file or be.KEY_FILE))
    client = be.build_client(key, base_url)
    print(f"[rag_lite_online_eval] model={args.model} base={base_url} key={key_source} "
          f"questions={len(dataset)} profiles={PROFILES}", flush=True)

    started = time.monotonic()
    profiles = {}
    for profile in PROFILES:
        print(f"[rag_lite_online_eval] running {profile} ...", flush=True)
        profiles[profile] = evaluate_profile(dataset, profile, client=client,
                                             model=args.model, limit=args.limit)
        a = profiles[profile]["aggregate"]
        print(f"  {profile}: citation={a['citation_accuracy']} groundedness={a['groundedness']} "
              f"refusal={a['correct_refusal_rate']} gold={a['gold_similarity']} "
              f"errors={a['errors']} ({profiles[profile]['elapsed_s']}s)", flush=True)

    cmp = compare_profiles(profiles["strict"], profiles["strict_nocite"])
    passed = bool(cmp["gate"]["passed"])
    winner = "strict_nocite" if passed else ""
    conclusion = (
        "strict_nocite 通过线上 40 题门禁，可作为 rag_lite 部署候选。" if passed else
        "strict_nocite 未通过全部门禁，不部署；保留 strict 现状，先修失败项。"
    )
    payload = {
        "schema_version": "rag_lite_online_eval.v1",
        "date": DATE,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "api": {"model": args.model, "base_url": base_url, "key_source": key_source},
        "dataset": {"total": len(dataset),
                    "answerable": sum(1 for x in dataset if x["answerable"]),
                    "unanswerable": sum(1 for x in dataset if not x["answerable"]),
                    "source": str(be.W13_JSON)},
        "profiles": profiles,
        "comparison": cmp["comparison"],
        "gate": cmp["gate"],
        "errors": cmp["errors"],
        "winner": winner,
        "passed": passed,
        "conclusion": conclusion,
        "elapsed_s": round(time.monotonic() - started, 2),
    }
    if args.write:
        out_json = Path(args.out_json) if args.out_json else OUT_JSON
        out_md = Path(args.out_md) if args.out_md else OUT_MD
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        out_md.write_text(render_md(payload), encoding="utf-8")
        print(f"[rag_lite_online_eval] wrote {out_json}")
        print(f"[rag_lite_online_eval] wrote {out_md}")
    print(f"[rag_lite_online_eval] {conclusion}")
    return 0 if passed else 3


if __name__ == "__main__":
    raise SystemExit(main())
