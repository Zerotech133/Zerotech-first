# -*- coding: utf-8 -*-
"""prompt v4 + guard 分段解析（segment）A/B：治「事实行未标【事实】」根因。

wave27/28 结论：v3 prompt 失败根因是模型把内容组织成「推断段」，事实行不逐行标【事实】；
35b 反而更糟。v4 方案（**整包在独立实验文件内，不改现有模块默认**）：

  ① prompt v4：
     - 每个事实行必须以【事实】开头、引用紧随（[unit_id] p.页码）；
     - 推断行必须以【推断】开头且最多 2 条、每条注明依据线索；
     - 给 1 个**完整示例**（事实段 2 行 + 推断段 1 行）；
  ② guard 分段解析模式 `segment`（本文件本地实现，不改 kb_qa_guard 默认）：
     识别「事实段/推断段」标题块——事实段内所有要点按事实计、推断段内按推断计，
     不依赖逐行标记；标题行本身不计；few-shot/说明/代码块跳过；
     实现方式：本地统计 direct/infer 后注入 result 的 marker 字段，
     再走 guard 的 legacy 口径（legacy 会采用已提供的 marker 数）完成引用/集中度/比例判定；
  ③ A/B：v3 问题库 12 条，v1（现 multi prompt，复用 wave25 结果）vs v4（现场生成），
     对比可答率 / guard 通过率 / 推断比例 / 引用数 / 耗时；
  ④ 输出 `research/reports/kb_qa_prompt_v4_20261101.{json,md}`，结论：v4 是否优于 v1/是否值得合入。

CLI：
  python tools/kb_corpus/kb_qa_prompt_v4.py run --n 12
  python tools/kb_corpus/kb_qa_prompt_v4.py run --n 10 --deadline 360
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
V3_BANK = REPO / "research" / "reports" / "kb_qa_bank_v3_20261101.json"
V1_BASELINE = REPO / "research" / "reports" / "kb_qa_prompt_v2_20261101.json"
OUT_JSON = REPO / "research" / "reports" / "kb_qa_prompt_v4_20261101.json"
OUT_MD = REPO / "research" / "reports" / "kb_qa_prompt_v4_20261101.md"
DEFAULT_N = 12
DEFAULT_K = 8
DEFAULT_MODEL = "qwen3.5:9b"
DEFAULT_TIMEOUT = 300
DEFAULT_DEADLINE = 480

# 分段解析：标题行/跳过词（与 guard strict 口径保持一致思路）
_FACT_HEAD_RE = re.compile(r"^\s*(?:#{1,6}\s*)?(?:【事实摘录】|【事实段】|事实摘录|事实段)\s*[：:如下]*\s*$")
_INFER_HEAD_RE = re.compile(r"^\s*(?:#{1,6}\s*)?(?:【推断】\s*段?|【推断段】|推断段|##\s*推断)\s*[：:如下]*\s*$")
_FACT_LINE_RE = re.compile(r"^\s*(?:[-*•]\s*|\d+[.、)]\s*)?【(?:直接事实|事实)】")
_INFER_LINE_RE = re.compile(r"^\s*(?:[-*•]\s*|\d+[.、)]\s*)?【推断】")
_SKIP_WORDS = ("示例", "few-shot", "few shot", "规则", "输出结构", "格式说明", "如下", "说明", "要求", "注意")

FULL_EXAMPLE = (
    "【事实摘录】\n"
    "【事实】M9 级覆铜板支持 224Gbps 与 78 层 PCB 量产（[unit_A] p.2）。\n"
    "【事实】单颗 GPU 配套 PCB 价值量约为传统方案三倍（[unit_B] p.5）。\n"
    "【推断】\n"
    "【推断】综合两条材料，高速材料升级将带动单机柜 PCB 价值量提升（依据：[unit_A] p.2、[unit_B] p.5）。\n"
    "（示例完）"
)


class PromptV4Error(RuntimeError):
    """prompt v4 A/B 异常。"""


def _load_module(name: str, filename: str):
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise PromptV4Error(f"无法加载 {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_cache: dict = {}


def mod(name: str):
    if name not in _cache:
        _cache[name] = _load_module(f"{name}_for_v4", f"{name}.py")
    return _cache[name]


# ---------------------------------------------------------------------------
# ① prompt v4
# ---------------------------------------------------------------------------
def build_prompt_v4(question: str, clusters: list[dict]) -> str:
    materials = mod("kb_qa_multi").format_clusters(clusters)
    return (
        "你是研报知识库问答助手。请**逐行严格遵守**以下格式（v4）：\n\n"
        "【格式要求（必须照做）】\n"
        "1) 先写一行标题「【事实摘录】」，随后每一条事实**单独一行、以【事实】开头**，"
        "且**引用紧跟在该句末尾**（[unit_id] p.页码），直接摘录材料原文、不改写关键数据；\n"
        "2) 再写一行标题「【推断】」，随后**最多 2 条**推断，每条**单独一行、以【推断】开头**，"
        "并注明依据线索（依据：[unit_id] …）；\n"
        "3) 事实行数必须多于推断行数（推断占比 ≤40%）；引用覆盖 **至少 2 个不同单元**；\n"
        "4) 材料不足时只回答「知识库中没有足够依据回答该问题。」，不要编造推断；\n"
        "5) 中文、≤400 字。\n\n"
        f"【完整示例】\n{FULL_EXAMPLE}\n\n"
        f"【问题】{question}\n\n【检索材料（按主题/章节聚类）】\n{materials}\n\n"
        "【回答（按【事实摘录】段 → 【推断】段的格式逐行输出）】"
    )


# ---------------------------------------------------------------------------
# ② guard 分段解析（本地实现，不改现有模块默认）
# ---------------------------------------------------------------------------
def segment_markers(answer: str) -> tuple[int, int]:
    """分段解析：事实段内要点按事实计、推断段内按推断计；标题行不计。

    返回 (direct_facts, inferences)。无任何段标题时退化为「仅统计行首显式标记」。
    """
    text = str(answer or "")
    direct = infer = 0
    section: str | None = None   # "fact" / "infer" / None
    in_code = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("```"):
            in_code = not in_code
            continue
        if in_code or not line:
            continue
        low = line.lower()
        if any(w in low for w in _SKIP_WORDS):
            continue
        if _FACT_HEAD_RE.match(line):
            section = "fact"
            continue
        if _INFER_HEAD_RE.match(line):
            section = "infer"
            continue
        # 行首显式标记优先
        if _FACT_LINE_RE.match(line):
            direct += 1
            continue
        if _INFER_LINE_RE.match(line):
            rest = _INFER_LINE_RE.sub("", line, count=1).strip()
            if rest.startswith(("如下", "：", ":", "（示例", "(示例")):
                continue
            infer += 1
            continue
        # 分段无标记：按当前段归属计数
        if section == "fact":
            direct += 1
        elif section == "infer":
            infer += 1
    return direct, infer


def _raw_with_segment_markers(raw: dict) -> tuple[dict, int, int]:
    direct, infer = segment_markers(str(raw.get("answer") or ""))
    out = dict(raw)
    out["direct_fact_markers"] = direct
    out["inference_markers"] = infer
    return out, direct, infer


def guard_metrics_segment(result: dict, unit_ids: set[str], book_ids: set[str] | None = None) -> dict:
    """seg 模式指标：把分段计数注入 marker 字段，再走 guard legacy 口径（采用已提供计数）。"""
    guard_mod = mod("kb_qa_guard")
    raw, _d, _i = _raw_with_segment_markers(result)
    return guard_mod.compute_guard_metrics(raw, unit_ids, book_ids, marker_mode="legacy")


def apply_guard_segment(result: dict, unit_ids: set[str], book_ids: set[str] | None = None,
                        on_fail: str = "fallback") -> dict:
    """seg 模式守卫：同 guard.apply_guard，但推断比例基于分段解析计数。"""
    guard_mod = mod("kb_qa_guard")
    raw, _d, _i = _raw_with_segment_markers(result)
    return guard_mod.apply_guard(raw, unit_ids, book_ids, on_fail=on_fail, marker_mode="legacy")


# ---------------------------------------------------------------------------
# ③ v4 arm 生成
# ---------------------------------------------------------------------------
def answer_v4(question: str, k: int = DEFAULT_K, chat_fn=None, search_fn=None,
              model: str = DEFAULT_MODEL, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """v4 arm：prompt v4 → LLM → guard segment（分段解析）。"""
    started = time.monotonic()
    v2 = mod("kb_qa_prompt_v2")
    multi = mod("kb_qa_multi")
    guard_mod = mod("kb_qa_guard")
    try:
        clusters = multi.retrieve_clusters(question, k=k, search_fn=search_fn)
        prompt = build_prompt_v4(question, clusters)
        chat = chat_fn or multi.call_ollama
        try:
            answer = chat(prompt, model=model, timeout=timeout)
            raw = v2._raw_from_answer(question, answer, clusters, multi)
            error = None
        except Exception as exc:  # noqa: BLE001
            raw = v2._raw_from_answer(question, multi.rule_based_multi(clusters), clusters, multi)
            raw.update({"fallback": True})
            error = f"{type(exc).__name__}: {exc}"
        unit_ids, book_ids = guard_mod.load_kb_ids()
        guarded = apply_guard_segment(raw, unit_ids, book_ids, on_fail="fallback")
    except Exception as exc:  # noqa: BLE001
        guarded = {"answer": None, "citations": [], "confidence": {"level": "none"},
                   "fallback": True, "refusal": False}
        error = f"{type(exc).__name__}: {exc}"
    out = v2._classify(guarded)
    if error:
        out["error"] = error
    out["arm"] = "v4"
    out["elapsed_seconds"] = round(time.monotonic() - started, 2)
    out["segment_direct_facts"] = int(guarded.get("direct_fact_markers") or 0)
    out["segment_inferences"] = int(guarded.get("inference_markers") or 0)
    out["guard_marker_mode"] = "segment"
    return out


# ---------------------------------------------------------------------------
# ④ 基线 / 统计 / 结论
# ---------------------------------------------------------------------------
def load_v1_rows(ids: set[str], path: Path | str = V1_BASELINE) -> dict:
    path = Path(path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    out = {}
    for row in (data.get("v1") or {}).get("rows") or []:
        if row.get("id") in ids:
            out[row["id"]] = {
                "status": row.get("status"), "success": row.get("status") == "success",
                "no_evidence": row.get("status") == "no_evidence",
                "fallback": row.get("status") == "fallback",
                "guard_passed": row.get("guard_passed"),
                "citations": int(row.get("citations") or 0),
                "inference_ratio": row.get("inference_ratio"),
                "elapsed_seconds": row.get("elapsed_seconds"),
                "answer_length": int(row.get("answer_length") or 0),
                "answer_preview": row.get("answer_preview"),
                "error": None, "reused": True}
    return out


def load_questions(path: Path | str = V3_BANK, n: int = DEFAULT_N) -> list[dict]:
    path = Path(path)
    if not path.exists():
        raise PromptV4Error(f"v3 问题库不存在：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PromptV4Error(f"v3 问题库读取失败：{path}（{exc}）") from exc
    qs = [q for q in data.get("questions") or [] if isinstance(q, dict) and q.get("question")]
    if not qs:
        raise PromptV4Error(f"v3 问题库 questions 为空：{path}")
    return qs[:max(1, int(n))]


def build_conclusion(v1: dict, v4: dict) -> dict:
    d_ans = round((v4.get("answerable_rate") or 0) - (v1.get("answerable_rate") or 0), 4)
    d_guard = round((v4.get("guard_pass_rate") or 0) - (v1.get("guard_pass_rate") or 0), 4)
    d_infer = round((v4.get("avg_inference_ratio") or 0) - (v1.get("avg_inference_ratio") or 0), 4)
    if v4.get("errors"):
        verdict, rec = "v4 存在调用/解析错误，先修稳定性再评估合入", "hold"
    elif d_ans >= -0.05 and d_guard >= 0.0:
        verdict, rec = "v4（分段解析）达到/超过 v1 可答率与 guard 通过率，建议合入（灰度）", "merge"
    elif (v4.get("guard_pass_rate") or 0) >= 0.5 and d_guard > 0:
        verdict, rec = "v4 显著提升 guard 通过率（推断比例可控），建议扩样后合入", "expand"
    elif d_guard > 0:
        verdict, rec = "v4 有小幅改善但未达 v1 水平，建议继续调优/扩样", "expand"
    else:
        verdict, rec = "v4 未优于 v1，不建议合入（保留现 multi prompt）", "keep"
    reasons = [
        f"可答率：v1 {v1.get('answerable_rate', 0):.0%}（{v1.get('success', 0)}/{v1.get('total', 0)}）→ "
        f"v4 {v4.get('answerable_rate', 0):.0%}（{v4.get('success', 0)}/{v4.get('total', 0)}），Δ{d_ans:+.0%}。",
        f"guard 通过率：v1 {v1.get('guard_pass_rate', 0):.0%} → v4 {v4.get('guard_pass_rate', 0):.0%}，"
        f"Δ{d_guard:+.0%}；fallback 率：{v1.get('fallback_rate', 0):.0%} → {v4.get('fallback_rate', 0):.0%}。",
        f"平均推断比例：{v1.get('avg_inference_ratio')} → {v4.get('avg_inference_ratio')}（Δ{d_infer:+.2f}）；"
        f"平均引用：{v1.get('avg_citations')} → {v4.get('avg_citations')}；"
        f"平均耗时：{v1.get('avg_elapsed_seconds')}s → {v4.get('avg_elapsed_seconds')}s。",
    ]
    risks = [
        "样本 12 条（LLM 慢时可能更少），为 effective 子集；结论需扩样 30–50 条复核。",
        "v1 基线复用 wave25 结果（不同时间点），耗时/波动存在时间差。",
        "segment 解析依赖模型输出段标题；若模型不写标题则退化为行首标记统计，可能漏计事实。",
        "完整示例为结构示范（未用真实 unit_id），9b 可能模仿格式但引用质量仍需人工抽查。",
        "若合入：需在 kb_qa_multi 增加 prompt 版本开关（v1/v4）+ guard segment 标记模式，灰度并保留回退。",
    ]
    return {"merge_recommendation": rec, "verdict": verdict, "reasons": reasons, "risks": risks,
            "delta": {"answerable_rate": d_ans, "guard_pass_rate": d_guard,
                      "avg_inference_ratio": d_infer,
                      "avg_elapsed_seconds": round((v4.get("avg_elapsed_seconds") or 0)
                                                   - (v1.get("avg_elapsed_seconds") or 0), 2)}}


# ---------------------------------------------------------------------------
# ⑤ 运行 + 输出
# ---------------------------------------------------------------------------
def run_ab(n: int = DEFAULT_N, k: int = DEFAULT_K, model: str = DEFAULT_MODEL,
           timeout: float = DEFAULT_TIMEOUT, deadline_seconds: float = DEFAULT_DEADLINE,
           chat_fn=None, search_fn=None, v3_bank: Path | str = V3_BANK,
           v1_baseline: Path | str = V1_BASELINE, clock=time.monotonic) -> dict:
    started = clock()
    questions = load_questions(v3_bank, n)
    ids = {q.get("id") for q in questions}
    v1_base = load_v1_rows(ids, v1_baseline)
    v1_rows, v4_rows = [], []
    live = skipped = 0
    for q in questions:
        qid = q.get("id")
        if qid in v1_base:
            v1_rows.append({**v1_base[qid], "id": qid, "question": q["question"],
                            "category": q.get("category")})
        remaining = deadline_seconds - (clock() - started)
        if remaining <= 1:
            skipped += 1
            continue
        row = answer_v4(q["question"], k=k, chat_fn=chat_fn, search_fn=search_fn,
                        model=model, timeout=min(timeout, remaining))
        row.update({"id": qid, "question": q["question"], "category": q.get("category")})
        v4_rows.append(row)
        live += 1
    v2 = mod("kb_qa_prompt_v2")
    v1_sum, v4_sum = v2.arm_stats(v1_rows), v2.arm_stats(v4_rows)
    conclusion = build_conclusion(v1_sum, v4_sum)
    return {"schema_version": 1, "kind": "kb_qa_prompt_v4_ab",
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "config": {"n_requested": int(n), "n_actual": live, "skipped": skipped,
                       "k": k, "model": model, "timeout_seconds": timeout,
                       "deadline_seconds": deadline_seconds,
                       "guard_marker_mode": "segment",
                       "prompt_v4_rules": ["事实行以【事实】开头+引用紧随", "推断行以【推断】开头且 ≤2 条",
                                           "完整示例（2 事实 + 1 推断）", "事实行多于推断行（≤40%）",
                                           "≥2 个不同单元", "不确定拒答"]},
            "full_example": FULL_EXAMPLE,
            "sources": {"v3_bank": str(v3_bank), "v1_baseline": str(v1_baseline)},
            "note": ("v4 arm 现场生成（prompt v4 + guard segment 分段解析）；v1 复用 wave25 结果；"
                     "LLM 慢时减少条数并注明；不改现有模块默认"),
            "v1": {"summary": v1_sum, "rows": v1_rows},
            "v4": {"summary": v4_sum, "rows": v4_rows},
            "comparison": conclusion,
            "elapsed_seconds": round(clock() - started, 2),
            "disclaimer": "prompt A/B 结果仅供研究参考，不构成投资建议；答案需人工复核。"}


def render_markdown(payload: dict) -> str:
    v1, v4 = payload["v1"]["summary"], payload["v4"]["summary"]
    cmp = payload["comparison"]
    cfg = payload["config"]
    lines = [f"# prompt v4 + guard 分段解析 A/B（{payload['generated_at'][:10]}）", "",
             f"> v3 问题库前 **{cfg['n_actual']}** 条（申请 {cfg['n_requested']}，跳过 {cfg['skipped']}）· "
             f"v1 = 现 multi prompt（复用 wave25 结果 {v1['total'] - 0} 条）· "
             f"v4 = 每行事实【事实】开头+引用紧随 / 推断【推断】≤2 条 + 完整示例 + guard **segment** 分段解析 · "
             f"qwen3.5:9b 串行。", "",
             "## 结论", "",
             f"**{cmp['verdict']}** —— 建议：`{cmp['merge_recommendation']}`"
             f"（merge=合入 / expand=扩样/继续调优 / keep=保留 v1 / hold=先修）", ""]
    lines += [f"- {r}" for r in cmp["reasons"]]
    lines += ["", "## A/B 对比", "",
              "| 指标 | v1（现 multi prompt） | v4（+segment 解析） | Δ |", "|---|---|---|---|",
              f"| 可答率 | {v1['answerable_rate']:.0%}（{v1['success']}/{v1['total']}） | "
              f"{v4['answerable_rate']:.0%}（{v4['success']}/{v4['total']}） | {cmp['delta']['answerable_rate']:+.0%} |",
              f"| guard 通过率 | {v1['guard_pass_rate']:.0%} | {v4['guard_pass_rate']:.0%} | "
              f"{cmp['delta']['guard_pass_rate']:+.0%} |",
              f"| fallback 率 | {v1['fallback_rate']:.0%} | {v4['fallback_rate']:.0%} | — |",
              f"| 拒答数 | {v1['no_evidence']} | {v4['no_evidence']} | — |",
              f"| 平均引用数 | {v1['avg_citations']} | {v4['avg_citations']} | — |",
              f"| 平均推断比例 | {v1['avg_inference_ratio']} | {v4['avg_inference_ratio']} | "
              f"{cmp['delta']['avg_inference_ratio']:+.2f} |",
              f"| 平均耗时(s) | {v1['avg_elapsed_seconds']} | {v4['avg_elapsed_seconds']} | "
              f"{cmp['delta']['avg_elapsed_seconds']:+.1f}s |", "",
              "## 逐条对比", "",
              "| # | 类别 | 问题 | v1 状态/guard/引用/推断 | v4 状态/guard/引用/推断 |",
              "|---|---|---|---|---|"]
    for idx, (r1, r4) in enumerate(zip(payload["v1"]["rows"], payload["v4"]["rows"]), 1):
        lines.append(f"| {idx} | {r1.get('category')} | {str(r1.get('question'))[:34]} | "
                     f"{r1.get('status')}/{r1.get('guard_passed')}/{r1.get('citations')}/{r1.get('inference_ratio')} | "
                     f"{r4.get('status')}/{r4.get('guard_passed')}/{r4.get('citations')}/{r4.get('inference_ratio')} |")
    lines += ["", "## prompt v4 规则", ""]
    lines += [f"- {x}" for x in cfg["prompt_v4_rules"]]
    lines += ["", "## 完整示例（few-shot）", "", "```", payload["full_example"], "```"]
    lines += ["", "## 风险与说明", ""]
    lines += [f"- 风险：{r}" for r in cmp["risks"]]
    lines += ["", f"- {payload['disclaimer']}", f"- 说明：{payload['note']}",
              f"- 总耗时 {payload['elapsed_seconds']}s"]
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
    ap = argparse.ArgumentParser(description="prompt v4 + guard 分段解析 A/B")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="跑 v1 vs v4 A/B 并输出 JSON+MD")
    run.add_argument("--n", type=int, default=DEFAULT_N, help=f"样本条数（默认 {DEFAULT_N}）")
    run.add_argument("--k", type=int, default=DEFAULT_K)
    run.add_argument("--model", default=DEFAULT_MODEL)
    run.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    run.add_argument("--deadline", type=float, default=DEFAULT_DEADLINE)
    run.add_argument("--v3-bank", default=str(V3_BANK))
    run.add_argument("--v1-baseline", default=str(V1_BASELINE))
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
                         v1_baseline=args.v1_baseline)
        paths = write_outputs(payload, args.out_json, args.out_md)
    except PromptV4Error as exc:
        print(f"[kb_qa_prompt_v4] FAILED: {exc}", file=sys.stderr)
        return 1
    v1, v4, cmp = payload["v1"]["summary"], payload["v4"]["summary"], payload["comparison"]
    print(f"[kb_qa_prompt_v4] n={payload['config']['n_actual']} skipped={payload['config']['skipped']}")
    print(f"[kb_qa_prompt_v4] v1: answerable={v1['answerable_rate']:.0%} guard={v1['guard_pass_rate']:.0%} "
          f"infer={v1['avg_inference_ratio']} cites={v1['avg_citations']} elapsed={v1['avg_elapsed_seconds']}s")
    print(f"[kb_qa_prompt_v4] v4: answerable={v4['answerable_rate']:.0%} guard={v4['guard_pass_rate']:.0%} "
          f"infer={v4['avg_inference_ratio']} cites={v4['avg_citations']} elapsed={v4['avg_elapsed_seconds']}s")
    print(f"[kb_qa_prompt_v4] verdict={cmp['verdict']} recommendation={cmp['merge_recommendation']}")
    print(f"[kb_qa_prompt_v4] json={paths[0]}")
    print(f"[kb_qa_prompt_v4] md={paths[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
