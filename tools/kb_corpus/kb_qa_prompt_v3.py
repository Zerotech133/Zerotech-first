# -*- coding: utf-8 -*-
# DEPRECATED（2026-11-02）：实验失败，禁止在生产路径引用；证据见 docs/plans/2026-11-02_提示词清单_活跃与废弃_v1.md
"""prompt v3 调优 A/B：先「事实摘录」后「推断（≤2 条且引用线索）」+ few-shot 示例。

背景：wave25 prompt v2 失败根因——严格指令让 9b 模型把几乎全部内容标为【推断】
（guard 推断比例均值 0.92）→ inference_heavy → fallback 92% / 可答率 0%。
v3 改为**分段落结构**：先输出【事实摘录】段（每条直接摘录材料原文 + 紧跟 [unit_id] 页码），
再输出【推断】段（最多 2 条、每条必须注明依据线索 [unit_id]），并给 1 个 few-shot 结构示例。

A/B（真实 LLM 串行，12 条 v3 问题）：
  - v1 arm：现 multi prompt 结果（复用 wave25 prompt_v2 A/B 的 v1 行，0 新增调用）；
  - v3 arm：同聚类材料 + prompt v3 → LLM → 同一 kb_qa_guard 后处理；
  - 对比：可答率 / guard 通过率 / fallback / 推断比例 / 引用数 / 平均耗时；
输出 `research/reports/kb_qa_prompt_v3_20261101.{json,md}`，结论：v3 是否可用/是否合入。

CLI：
  python tools/kb_corpus/kb_qa_prompt_v3.py run --n 12
  python tools/kb_corpus/kb_qa_prompt_v3.py run --n 10 --deadline 360   # LLM 慢时减条
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
V3_BANK = REPO / "research" / "reports" / "kb_qa_bank_v3_20261101.json"
V2_AB_JSON = REPO / "research" / "reports" / "kb_qa_prompt_v2_20261101.json"
OUT_JSON = REPO / "research" / "reports" / "kb_qa_prompt_v3_20261101.json"
OUT_MD = REPO / "research" / "reports" / "kb_qa_prompt_v3_20261101.md"
DEFAULT_N = 12
DEFAULT_K = 8
DEFAULT_MODEL = "qwen3.5:9b"
DEFAULT_TIMEOUT = 300
DEFAULT_DEADLINE = 420

FEWSHOT_EXAMPLE = (
    "【示例（few-shot，结构示范）】\n"
    "材料：[unit_A] p.2 「M9 级覆铜板支持 224Gbps 与 78 层 PCB 量产。」；"
    "[unit_B] p.5 「单颗 GPU 配套 PCB 价值量约为传统方案三倍。」\n"
    "回答：\n"
    "【事实】M9 级覆铜板支持 224Gbps 与 78 层 PCB 量产（[unit_A] p.2）。\n"
    "【事实】单颗 GPU 配套 PCB 价值量约为传统方案三倍（[unit_B] p.5）。\n"
    "【推断】综合两条材料，高速材料升级将带动单机柜 PCB 价值量提升"
    "（依据：[unit_A] p.2、[unit_B] p.5）。"
)


class PromptV3Error(RuntimeError):
    """prompt v3 A/B 异常。"""


def _load_module(name: str, filename: str):
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise PromptV3Error(f"无法加载 {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_mod_cache: dict = {}


def mod(name: str):
    """复用 wave25 的 prompt_v2 模块（含 kb_qa/kb_qa_multi/kb_qa_guard 工具函数）。"""
    if name not in _mod_cache:
        _mod_cache[name] = _load_module(f"{name}_for_prompt_v3", f"{name}.py")
    return _mod_cache[name]


# ---------------------------------------------------------------------------
# ① prompt v3
# ---------------------------------------------------------------------------
def build_prompt_v3(question: str, clusters: list[dict]) -> str:
    """v3：事实摘录段在前（每条带引用）→ 推断段（≤2 条且必须引用线索）+ few-shot。"""
    materials = mod("kb_qa_multi").format_clusters(clusters)
    return (
        "你是研报知识库问答助手。严格按以下**分段结构**作答（v3）：\n\n"
        "【输出结构（必须照做）】\n"
        "1) 先写「【事实摘录】」段：2–4 条，**每条直接摘录材料原文**（原文优先、不得改写关键数据），"
        "并在句末紧跟 [unit_id] 与页码（如 [3ade35288309] p.2）；\n"
        "2) 再写「【推断】」段（可选）：**最多 2 条**，每条必须以「（依据：[unit_id] …）」注明线索，"
        "不得引入材料之外的信息；\n"
        "3) 事实段必须占多数：推断条数 ≤2 且推断占比 ≤40%；\n"
        "4) 引用必须覆盖 **至少 2 个不同单元**（unit_id）；\n"
        "5) 材料不足时只回答「知识库中没有足够依据回答该问题。」，不要给推断；\n"
        "6) 中文、不超过 400 字。\n\n"
        f"{FEWSHOT_EXAMPLE}\n\n"
        f"【问题】{question}\n\n【检索材料（按主题/章节聚类）】\n{materials}\n\n"
        "【回答（先【事实摘录】段，再【推断】段）】"
    )


# ---------------------------------------------------------------------------
# ② v3 arm 生成
# ---------------------------------------------------------------------------
def answer_v3(question: str, k: int = DEFAULT_K, chat_fn=None, search_fn=None,
              model: str = DEFAULT_MODEL, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """v3 arm：同聚类材料 + prompt v3 → LLM → 同一 guard 后处理（复用 v2 工具函数）。"""
    started = time.monotonic()
    v2 = mod("kb_qa_prompt_v2")
    multi = mod("kb_qa_multi")
    guard_mod = mod("kb_qa_guard")
    try:
        clusters = multi.retrieve_clusters(question, k=k, search_fn=search_fn)
        prompt = build_prompt_v3(question, clusters)
        chat = chat_fn or multi.call_ollama
        try:
            answer = chat(prompt, model=model, timeout=timeout)
            raw = v2._raw_from_answer(question, answer, clusters, multi)
        except Exception as exc:  # noqa: BLE001 - LLM 失败 → 规则摘录降级
            raw = v2._raw_from_answer(question, multi.rule_based_multi(clusters), clusters, multi)
            raw.update({"fallback": True, "error": f"{type(exc).__name__}: {exc}"})
        unit_ids, book_ids = guard_mod.load_kb_ids()
        guarded = guard_mod.apply_guard(raw, unit_ids, book_ids, on_fail="fallback")
    except Exception as exc:  # noqa: BLE001
        guarded = {"answer": None, "citations": [], "confidence": {"level": "none"},
                   "fallback": True, "refusal": False,
                   "error": f"{type(exc).__name__}: {exc}"}
    out = v2._classify(guarded)
    if guarded.get("error"):
        out["error"] = guarded["error"]
    out["elapsed_seconds"] = round(time.monotonic() - started, 2)
    out["arm"] = "v3"
    out["cluster_count"] = len(guarded.get("clusters") or [])
    return out


# ---------------------------------------------------------------------------
# ③ v1 复用 + 统计 + 结论
# ---------------------------------------------------------------------------
def load_v1_rows(path: Path | str = V2_AB_JSON) -> dict:
    """复用 wave25 A/B 的 v1 行（同 12 条问题），避免重复 LLM 调用。"""
    path = Path(path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    rows = {}
    for row in (data.get("v1") or {}).get("rows") or []:
        if isinstance(row, dict) and row.get("id"):
            rows[row["id"]] = {**row, "reused": True, "arm": "v1"}
    return rows


def build_conclusion_v3(v1: dict, v3: dict) -> dict:
    d_success = round(v3["answerable_rate"] - v1["answerable_rate"], 4)
    d_guard = round(v3["guard_pass_rate"] - v1["guard_pass_rate"], 4)
    d_infer = round(v3["avg_inference_ratio"] - v1["avg_inference_ratio"], 4)
    if v3["errors"]:
        verdict, merge = "v3 存在调用错误，先修稳定性再评估", "hold"
    elif v3["success"] == 0:
        verdict, merge = "v3 仍未能产出可答样本（推断标记问题未解决），不可用，继续调优", "keep"
    elif d_success >= 0.05 and d_guard >= 0.0:
        verdict, merge = "v3 显著优于 v1（可答率提升且 guard 不降），建议合入（灰度）", "merge"
    elif d_success >= -0.05 and (d_guard > 0 or d_infer < 0):
        verdict, merge = "v3 可用且有质量增益（guard/推断比例改善），建议扩样后合入", "expand"
    else:
        verdict, merge = "v3 可用但未优于 v1，继续调优 prompt（事实段/引用跟随）", "keep"
    reasons = [
        f"可答率：v1 {v1['answerable_rate']:.0%}（{v1['success']}/{v1['total']}）→ "
        f"v3 {v3['answerable_rate']:.0%}（{v3['success']}/{v3['total']}），Δ{d_success:+.0%}。",
        f"guard 通过率：v1 {v1['guard_pass_rate']:.0%} → v3 {v3['guard_pass_rate']:.0%}，Δ{d_guard:+.0%}；"
        f"fallback 率：{v1['fallback_rate']:.0%} → {v3['fallback_rate']:.0%}。",
        f"平均推断比例：{v1['avg_inference_ratio']} → {v3['avg_inference_ratio']}（Δ{d_infer:+.2f}）；"
        f"平均引用：{v1['avg_citations']} → {v3['avg_citations']}；"
        f"平均耗时：{v1['avg_elapsed_seconds']}s → {v3['avg_elapsed_seconds']}s。",
    ]
    risks = [
        "样本 12 条（LLM 慢时更少），为 effective 子集，结论需扩样 30–50 条复核。",
        "v1 结果复用 wave25（不同时间点），耗时/波动存在时间差。",
        "few-shot 示例为结构示范（未用真实 unit_id），9b 模型可能模仿结构但引用质量仍需人工抽查。",
        "若合入：在 kb_qa_multi 增加 prompt 版本开关 v1/v3，灰度并保留回退。",
    ]
    return {"merge_recommendation": merge, "verdict": verdict, "reasons": reasons, "risks": risks,
            "delta": {"success_rate": d_success, "guard_pass_rate": d_guard,
                      "avg_inference_ratio": d_infer,
                      "avg_elapsed_seconds": round(v3["avg_elapsed_seconds"] - v1["avg_elapsed_seconds"], 2)}}


def _load_questions(path: Path | str, n: int) -> list[dict]:
    path = Path(path)
    if not path.exists():
        raise PromptV3Error(f"v3 问题库不存在：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PromptV3Error(f"v3 问题库读取失败：{path}（{exc}）") from exc
    qs = [q for q in data.get("questions") or [] if isinstance(q, dict) and q.get("question")]
    if not qs:
        raise PromptV3Error(f"v3 问题库 questions 为空：{path}")
    return qs[:max(1, int(n))]


# ---------------------------------------------------------------------------
# ④ 运行 + 输出
# ---------------------------------------------------------------------------
def run_ab(n: int = DEFAULT_N, k: int = DEFAULT_K, model: str = DEFAULT_MODEL,
           timeout: float = DEFAULT_TIMEOUT, deadline_seconds: float = DEFAULT_DEADLINE,
           chat_fn=None, search_fn=None, v3_bank: Path | str = V3_BANK,
           v1_json: Path | str = V2_AB_JSON, clock=time.monotonic) -> dict:
    started = clock()
    questions = _load_questions(v3_bank, n)
    v1_reuse = load_v1_rows(v1_json)
    v1_rows, v3_rows = [], []
    live_v1 = live_v3 = skipped = 0
    for q in questions:
        qid = q.get("id")
        if qid in v1_reuse:
            v1_rows.append({**v1_reuse[qid], "question": q["question"], "category": q.get("category")})
        else:
            remaining = deadline_seconds - (clock() - started)
            if remaining <= 1:
                skipped += 1
                continue
            row = mod("kb_qa_prompt_v2").answer_v1(q["question"], k=k, model=model,
                                                  timeout=min(timeout, remaining))
            row.update({"id": qid, "question": q["question"], "category": q.get("category")})
            v1_rows.append(row)
            live_v1 += 1
        remaining = deadline_seconds - (clock() - started)
        if remaining <= 1:
            skipped += 1
            continue
        row3 = answer_v3(q["question"], k=k, chat_fn=chat_fn, search_fn=search_fn,
                         model=model, timeout=min(timeout, remaining))
        row3.update({"id": qid, "question": q["question"], "category": q.get("category")})
        v3_rows.append(row3)
        live_v3 += 1
    v2mod = mod("kb_qa_prompt_v2")
    v1_sum, v3_sum = v2mod.arm_stats(v1_rows), v2mod.arm_stats(v3_rows)
    conclusion = build_conclusion_v3(v1_sum, v3_sum)
    return {"schema_version": 1, "kind": "kb_qa_prompt_v3_ab",
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "config": {"n_requested": int(n), "n_actual": len(v3_rows), "k": k, "model": model,
                       "timeout_seconds": timeout, "deadline_seconds": deadline_seconds,
                       "reuse_v1": True, "live_v1": live_v1, "live_v3": live_v3,
                       "skipped": skipped,
                       "prompt_v3_changes": ["先事实摘录段（每条带引用）", "再推断段（≤2 条且引用线索）",
                                             "事实段优先/推断占比≤40%", "≥2 个不同单元", "1 个 few-shot 结构示例",
                                             "不确定拒答"]},
            "fewshot_example": FEWSHOT_EXAMPLE,
            "v1": {"summary": v1_sum, "rows": v1_rows},
            "v3": {"summary": v3_sum, "rows": v3_rows},
            "comparison": conclusion,
            "disclaimer": "prompt A/B 结果仅供研究参考，不构成投资建议；答案需人工复核。",
            "elapsed_seconds": round(clock() - started, 2)}


def render_markdown(payload: dict) -> str:
    v1, v3 = payload["v1"]["summary"], payload["v3"]["summary"]
    cmp = payload["comparison"]
    cfg = payload["config"]
    lines = [f"# multi prompt v3 A/B（先事实后推断 + few-shot，{payload['generated_at'][:10]}）", "",
             f"> v3 问题库前 **{cfg['n_actual']}** 条 · v1 = 现 multi prompt（复用 wave25 结果 {v1['total'] - cfg['live_v1']} 条）"
             f" · v3 = 先【事实摘录】再【推断（≤2 条+引用线索）】+ few-shot，同聚类材料 + 同 guard · qwen3.5:9b 串行。", "",
             "## 结论", "",
             f"**{cmp['verdict']}** —— 建议：`{cmp['merge_recommendation']}`"
             f"（merge=合入 / expand=扩样 / keep=继续调优 / hold=先修）", ""]
    lines += [f"- {r}" for r in cmp["reasons"]]
    lines += ["", "## A/B 对比", "",
              "| 指标 | v1（现 multi prompt） | v3（先事实后推断 + few-shot） | Δ |", "|---|---|---|---|",
              f"| 可答率 | {v1['answerable_rate']:.0%}（{v1['success']}/{v1['total']}） | "
              f"{v3['answerable_rate']:.0%}（{v3['success']}/{v3['total']}） | {cmp['delta']['success_rate']:+.0%} |",
              f"| guard 通过率 | {v1['guard_pass_rate']:.0%} | {v3['guard_pass_rate']:.0%} | "
              f"{cmp['delta']['guard_pass_rate']:+.0%} |",
              f"| fallback 率 | {v1['fallback_rate']:.0%} | {v3['fallback_rate']:.0%} | — |",
              f"| 拒答数 | {v1['no_evidence']} | {v3['no_evidence']} | — |",
              f"| 平均引用数 | {v1['avg_citations']} | {v3['avg_citations']} | — |",
              f"| 平均推断比例 | {v1['avg_inference_ratio']} | {v3['avg_inference_ratio']} | "
              f"{cmp['delta']['avg_inference_ratio']:+.2f} |",
              f"| 平均耗时(s) | {v1['avg_elapsed_seconds']} | {v3['avg_elapsed_seconds']} | "
              f"{cmp['delta']['avg_elapsed_seconds']:+.1f}s |", "",
              "## 逐条对比", "",
              "| # | 类别 | 问题 | v1 状态/guard/引用/推断 | v3 状态/guard/引用/推断 |",
              "|---|---|---|---|---|"]
    for idx, (r1, r3) in enumerate(zip(payload["v1"]["rows"], payload["v3"]["rows"]), 1):
        lines.append(f"| {idx} | {r1.get('category')} | {str(r1.get('question'))[:34]} | "
                     f"{r1.get('status')}/{r1.get('guard_passed')}/{r1.get('citations')}/{r1.get('inference_ratio')} | "
                     f"{r3.get('status')}/{r3.get('guard_passed')}/{r3.get('citations')}/{r3.get('inference_ratio')} |")
    lines += ["", "## prompt v3 改动点", ""]
    lines += [f"- {x}" for x in cfg["prompt_v3_changes"]]
    lines += ["", "## few-shot 示例", "", "```", payload["fewshot_example"], "```"]
    lines += ["", "## 风险与说明", ""]
    lines += [f"- 风险：{r}" for r in cmp["risks"]]
    lines += ["", f"- {payload['disclaimer']}", f"- 总耗时 {payload['elapsed_seconds']}s（含 LLM）"]
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
    ap = argparse.ArgumentParser(description="prompt v3 A/B（先事实后推断 + few-shot）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="跑 v1 vs v3 A/B 并输出 JSON+MD")
    run.add_argument("--n", type=int, default=DEFAULT_N, help=f"样本条数（默认 {DEFAULT_N}）")
    run.add_argument("--k", type=int, default=DEFAULT_K)
    run.add_argument("--model", default=DEFAULT_MODEL)
    run.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    run.add_argument("--deadline", type=float, default=DEFAULT_DEADLINE)
    run.add_argument("--v3-bank", default=str(V3_BANK))
    run.add_argument("--v1-json", default=str(V2_AB_JSON))
    run.add_argument("--out-json", default=str(OUT_JSON))
    run.add_argument("--out-md", default=str(OUT_MD))
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd != "run":  # pragma: no cover
        return 2
    try:
        payload = run_ab(args.n, k=args.k, model=args.model, timeout=args.timeout,
                         deadline_seconds=args.deadline, v3_bank=args.v3_bank,
                         v1_json=args.v1_json)
        paths = write_outputs(payload, args.out_json, args.out_md)
    except PromptV3Error as exc:
        print(f"[kb_qa_prompt_v3] FAILED: {exc}", file=sys.stderr)
        return 1
    v1, v3, cmp = payload["v1"]["summary"], payload["v3"]["summary"], payload["comparison"]
    print(f"[kb_qa_prompt_v3] n={payload['config']['n_actual']} live_v1={payload['config']['live_v1']} "
          f"live_v3={payload['config']['live_v3']} skipped={payload['config']['skipped']}")
    print(f"[kb_qa_prompt_v3] v1: answerable={v1['answerable_rate']:.0%} guard={v1['guard_pass_rate']:.0%} "
          f"fallback={v1['fallback_rate']:.0%} cites={v1['avg_citations']} infer={v1['avg_inference_ratio']} "
          f"elapsed={v1['avg_elapsed_seconds']}s")
    print(f"[kb_qa_prompt_v3] v3: answerable={v3['answerable_rate']:.0%} guard={v3['guard_pass_rate']:.0%} "
          f"fallback={v3['fallback_rate']:.0%} cites={v3['avg_citations']} infer={v3['avg_inference_ratio']} "
          f"elapsed={v3['avg_elapsed_seconds']}s")
    print(f"[kb_qa_prompt_v3] verdict={cmp['verdict']} recommendation={cmp['merge_recommendation']}")
    print(f"[kb_qa_prompt_v3] json={paths[0]}")
    print(f"[kb_qa_prompt_v3] md={paths[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
