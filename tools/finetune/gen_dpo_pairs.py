# -*- coding: utf-8 -*-
"""W20 · DPO 拒绝采样生成偏好对（task-180）：k=8 采样 + W13 规则评分选 top/bottom。

用途
----
为 DPO（D4/D5）准备「拒绝采样」偏好对自动化脚本，待百炼 SFT 模型部署后直接跑；也支持本地 ollama 与
mock 模式先验证管道。

流程
----
1. 问题集：`research/reports/answer_eval_20261104.json` 的 **30 道可答题**（W13 口径：问题 + gold +
   当次 hybrid top5 上下文 ctx_ids；上下文从 W6 文档库重建）；可 `--heldout` 追加 held-out 概念题（预留）。
2. 模型端（二选一）：
   - OpenAI 兼容（百炼部署实例）：`--model <实例ID>`；key 用 `DASHSCOPE_API_KEY` 或
     `C:\\项目\\_vault\\secrets\\dashscope_api_key.txt`；base_url 默认北京（`--base-url` 可切新加坡）；
   - 本地 ollama：`--local [--local-model qwen3.5:9b]`（`/api/chat`，不联网）。
3. 每问采样 **k=8**（temperature 0.7），用 W13 `answer_eval.py` 的确定性指标给每个候选打分：
   引用正确/groundedness/gold bigram F1/无过度声明 → 组合分：
   `0.45*citation_ok + 0.30*groundedness + 0.15*gold_f1 + 0.10*(1-overclaim)`。
4. 选 top1 为 chosen、bottom1 为 rejected；过滤：chosen 非拒答/无过度声明、组合分差 ≥0.05、
   bottom 组合分 < 拒绝阈值（默认 0.50）、chosen 引用正确。
5. 输出 `research/finetune/dpo/quant_kb_dpo_v3_candidates.jsonl`（与 v2 兼容：prompt/chosen/rejected/
   source/category/concept/meta metrics）+ 报告 `research/reports/dpo_sampling_20261104.md`。

安全 / 约束
-----------
- 测试与本次交付**不调用真实 API**：`--dry-run` 只打印采样计划（无 key 也可用）；`--mock` 用合成候选
  跑通全流程（结果标注 mock，非真实模型）；真实调用仅在部署后显式运行。
- 只读知识库/报告；不写生产库；不 commit；只写 2 个交付文件（candidates.jsonl + 报告）。

CLI 示例
--------
    # 部署前：打印采样计划（30 题 × k=8 = 240 请求，无需 key）
    python tools/finetune/gen_dpo_pairs.py --dry-run
    python tools/finetune/gen_dpo_pairs.py --dry-run --limit 3 --k 4

    # 管道自检（mock，不联网；可写候选/报告）
    python tools/finetune/gen_dpo_pairs.py --mock --write

    # 部署后：百炼实例拒绝采样（真实调用）
    python tools/finetune/gen_dpo_pairs.py --model qwen3-14b-quantkb-xxxx --k 8 --write
    # 本地 ollama（真实调用本机模型）
    python tools/finetune/gen_dpo_pairs.py --local --write
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import bailian_eval as be  # noqa: E402  复用 W13 指标/数据集/client 辅助（不修改）

W13_JSON = REPO / "research" / "reports" / "answer_eval_20261104.json"
OUT_JSONL = REPO / "research" / "finetune" / "dpo" / "quant_kb_dpo_v3_candidates.jsonl"
OUT_MD = REPO / "research" / "reports" / "dpo_sampling_20261104.md"
HELDOUT = REPO / "research" / "finetune" / "heldout" / "quant_kb_heldout_105.jsonl"
DATE = "20261104"
K = 8
TEMPERATURE = 0.7
MAX_TOKENS = 180
GAP_MIN = 0.05
CHOSEN_MIN = 0.55
REJECT_MAX = 0.50
LOCAL_URL = "http://127.0.0.1:11434"
LOCAL_MODEL = "qwen3.5:9b"
CITE = re.compile(r"\[([0-9]{1,2})\]")


# ================================================================ 评分
def judge_candidate(item: dict, answer: str) -> dict:
    """用 W13 judge_one 评一个候选，并计算组合分与资格。"""
    w13 = be._load_w13()
    judged = w13.judge_one(item, item["ctx"], answer)
    cite = 1.0 if judged["citation_ok"] else 0.0
    ground = judged["groundedness"] or 0.0
    gold = judged["gold_similarity"] or 0.0
    over = 0.0 if judged["overclaim"] else 1.0
    judged["composite"] = round(0.45 * cite + 0.30 * ground + 0.15 * gold + 0.10 * over, 4)
    judged["eligible_chosen"] = bool(judged["citation_ok"] and not judged["refused"]
                                     and not judged["overclaim"] and judged["composite"] >= CHOSEN_MIN)
    judged["eligible_rejected"] = bool(judged["composite"] < REJECT_MAX)
    return judged


def score_candidates(item: dict, answers: list[str]) -> list[dict]:
    scored = []
    for idx, ans in enumerate(answers):
        if ans is None:
            continue
        judged = judge_candidate(item, ans)
        judged.update({"sample_idx": idx, "answer": ans})
        scored.append(judged)
    return scored


def select_pair(scored: list[dict]) -> tuple[dict | None, dict | None, str]:
    """选 top1/bottom1 并做质量过滤；返回 (chosen, rejected, reason)。"""
    if len(scored) < 2:
        return None, None, "candidates<2"
    ordered = sorted(scored, key=lambda x: -x["composite"])
    chosen, rejected = ordered[0], ordered[-1]
    if not chosen.get("eligible_chosen"):
        return None, None, "chosen_not_eligible"
    if not rejected.get("eligible_rejected"):
        return None, None, "rejected_not_below_threshold"
    if chosen["composite"] - rejected["composite"] < GAP_MIN:
        return None, None, "gap_too_small"
    return chosen, rejected, "ok"


# ================================================================ 候选生成
def _post_json(url: str, payload: dict, timeout: float = 120):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


class LocalOllamaBackend:
    """本地 ollama /api/chat（不联网）。"""

    name = "local_ollama"

    def __init__(self, model: str = LOCAL_MODEL, url: str = LOCAL_URL, timeout: float = 120.0):
        self.model = model
        self.url = url.rstrip("/")
        self.timeout = timeout

    def generate(self, prompt: str, *, temperature: float = TEMPERATURE,
                 max_tokens: int = MAX_TOKENS) -> str:
        payload = {"model": self.model, "stream": False, "think": False,
                   "messages": [{"role": "user", "content": prompt}],
                   "options": {"temperature": temperature, "num_predict": max_tokens}}
        data = _post_json(f"{self.url}/api/chat", payload, timeout=self.timeout)
        return str(((data or {}).get("message") or {}).get("content") or "").strip()


class OpenAICompatBackend:
    """百炼/OpenAI 兼容端点。"""

    name = "openai_compat"

    def __init__(self, api_key: str, base_url: str, model: str, timeout: float = 60.0):
        self.client = be.build_client(api_key, base_url, timeout=timeout)
        self.model = model
        self.base_url = base_url

    def generate(self, prompt: str, *, temperature: float = TEMPERATURE,
                 max_tokens: int = MAX_TOKENS) -> str:
        resp = self.client.chat.completions.create(
            model=self.model, messages=[{"role": "user", "content": prompt}],
            temperature=temperature, max_tokens=max_tokens)
        return be.parse_answer(resp)


class MockBackend:
    """离线合成候选：按采样序号构造高/中/低质量答案，保证可产出偏好对（仅管道验证）。"""

    name = "mock"

    def __init__(self, seed: int = 42):
        self.rng = random.Random(seed)

    def mock_answers(self, item: dict, k: int) -> list[str]:
        ctx = item.get("ctx") or []
        gold_ids = set(item.get("gold_doc_ids") or [])
        cite_idx, gold_doc = 1, (ctx[0] if ctx else {"text": ""})
        for i, c in enumerate(ctx):
            if c.get("doc_id") in gold_ids:
                cite_idx, gold_doc = i + 1, c
                break
        good = f"根据资料，{str(gold_doc.get('text') or '')[:110]}（要点转述）[{cite_idx}]。"
        base_text = str(gold_doc.get("text") or "")
        mids = [
            f"{base_text[:90]}。",                                  # 无引用
            f"{base_text[:80]}。[9]",                               # 引用越界
            f"这个问题可以这样理解：{base_text[:70]}。",             # 口语/弱引用
            f"资料显示相关要点如下：{base_text[:60]}。[{cite_idx}]",  # 有正确引用但更短
            f"从资料看，{base_text[:50]}；另外还需结合实践判断。[{cite_idx}]",
        ]
        out = [good]
        while len(out) < k - 1:
            out.append(mids[(len(out) - 1) % len(mids)])
        out.append("必涨！建议满仓买入，目标价翻倍，稳赚不赔[9]。")    # 低质量：过度声明+错误引用
        return out[:k]

    def generate(self, prompt: str, *, temperature: float = TEMPERATURE,
                 max_tokens: int = MAX_TOKENS) -> str:  # pragma: no cover - mock 走 mock_answers
        raise NotImplementedError("MockBackend 使用 mock_answers()")


def generate_candidates(backend, item: dict, k: int, temperature: float = TEMPERATURE) -> list[str | None]:
    """对单题采样 k 个候选；单次失败记 None，不中断。"""
    if isinstance(backend, MockBackend):
        return list(backend.mock_answers(item, k))
    prompt = be.build_prompt(item["question"], item["ctx"])
    out: list[str | None] = []
    for _ in range(k):
        try:
            out.append(backend.generate(prompt, temperature=temperature, max_tokens=MAX_TOKENS))
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] {item['qid']} sample err {type(exc).__name__}: {exc}", flush=True)
            out.append(None)
    return out


# ================================================================ 计划 / 主流程
def build_plan(dataset: list[dict], *, backend_name: str, model: str, k: int,
               limit: int = 0, has_key: bool = False, base_url: str = "") -> dict:
    items = dataset[:limit] if limit and limit > 0 else dataset
    return {
        "mode": "dry-run", "backend": backend_name, "model": model, "base_url": base_url,
        "has_api_key": has_key, "k": k, "temperature": TEMPERATURE,
        "questions_total": len(dataset), "planned_questions": len(items),
        "planned_requests": len(items) * k,
        "sample_prompt_preview": (be.build_prompt(items[0]["question"], items[0]["ctx"])[:500]
                                  if items else ""),
        "items": [{"qid": q["qid"], "type": q["type"],
                   "question": q["question"], "ctx_ids": q.get("w13_ctx_ids")} for q in items],
    }


def build_qa_dataset(*, include_heldout: bool = False) -> list[dict]:
    w13_payload = be.load_w13(W13_JSON)
    dataset = [d for d in be.build_dataset(w13_payload) if d["answerable"]]
    if include_heldout:
        dataset += load_heldout_concepts()
    return dataset


def load_heldout_concepts(limit: int = 0) -> list[dict]:
    """可选：把 held-out 概念题转成同结构（gold=对应知识单元；上下文=该单元文本）。"""
    if not HELDOUT.exists():
        return []
    rows = [json.loads(x) for x in HELDOUT.read_text(encoding="utf-8").splitlines() if x.strip()]
    concepts = [r for r in rows if str((r.get("meta") or {}).get("category")) != "C_heldout_note"]
    if limit:
        concepts = concepts[:limit]
    out = []
    for i, r in enumerate(concepts):
        meta = r.get("meta") or {}
        user = ""
        for m in r.get("messages") or []:
            if m.get("role") == "user":
                user = str(m.get("content") or "")
        out.append({"qid": f"H{i+1:02d}", "type": "heldout_concept", "answerable": True,
                    "question": user or f"「{meta.get('concept')}」的核心要点是什么？",
                    "gold_doc_ids": [], "gold_text": str(meta.get("concept") or ""),
                    "ctx": [], "w13_ctx_ids": [], "heldout_meta": meta})
    return out


def build_pairs(dataset: list[dict], backend, *, k: int, limit: int = 0,
                temperature: float = TEMPERATURE) -> dict:
    items = dataset[:limit] if limit and limit > 0 else dataset
    pairs, skipped = [], {}
    stats = {"n_candidates": 0, "n_failed_samples": 0}
    for item in items:
        if not item.get("ctx"):
            skipped["no_ctx"] = skipped.get("no_ctx", 0) + 1
            continue
        answers = generate_candidates(backend, item, k, temperature)
        stats["n_candidates"] += len(answers)
        stats["n_failed_samples"] += sum(1 for a in answers if a is None)
        scored = score_candidates(item, answers)
        chosen, rejected, reason = select_pair(scored)
        if chosen is None:
            skipped[reason] = skipped.get(reason, 0) + 1
            continue
        pairs.append({"item": item, "chosen": chosen, "rejected": rejected,
                      "n_candidates": len(scored), "gap": round(chosen["composite"] - rejected["composite"], 4)})
    return {"pairs": pairs, "skipped": skipped, "stats": stats}


def pair_record(pair: dict, *, backend_name: str, model: str, k: int) -> dict:
    item = pair["item"]

    def m(j: dict) -> dict:
        return {kk: j.get(kk) for kk in ("citation_ok", "groundedness", "gold_similarity",
                                         "refused", "overclaim", "length", "length_anomaly", "composite")}

    return {
        "prompt": item["question"],
        "chosen": pair["chosen"]["answer"],
        "rejected": pair["rejected"]["answer"],
        "source": f"{W13_JSON.stem}_rejection_sampling" if item["type"] != "heldout_concept"
                  else "heldout_concept_rejection_sampling",
        "category": "E1_rejection_sampling",
        "concept": item["type"],
        "meta": {
            "qid": item["qid"], "type": item["type"], "ctx_ids": item.get("w13_ctx_ids"),
            "citation_gold": item.get("gold_doc_ids"),
            "backend": backend_name, "model": model, "k": k, "temperature": TEMPERATURE,
            "n_candidates": pair["n_candidates"], "composite_gap": pair["gap"],
            "chosen_metrics": m(pair["chosen"]), "rejected_metrics": m(pair["rejected"]),
            "chosen_sample_idx": pair["chosen"].get("sample_idx"),
            "rejected_sample_idx": pair["rejected"].get("sample_idx"),
            "quality_ok": True, "origin": "w20_rejection_sampling",
        },
    }


def summarize(records: list[dict], skipped: dict, stats: dict, *, dataset_n: int, k: int,
              backend_name: str) -> dict:
    chosen_c = [r["meta"]["chosen_metrics"]["composite"] for r in records]
    rejected_c = [r["meta"]["rejected_metrics"]["composite"] for r in records]
    gaps = [r["meta"]["composite_gap"] for r in records]
    cand = stats.get("n_candidates", 0)
    return {
        "pairs": len(records), "questions_total": dataset_n,
        "coverage_pct": round(100.0 * len(records) / max(1, dataset_n), 2),
        "skipped": skipped, "k": k, "backend": backend_name,
        "candidates_generated": cand, "failed_samples": stats.get("n_failed_samples", 0),
        "candidate_success_rate": round(1.0 - stats.get("n_failed_samples", 0) / max(1, cand), 4),
        "chosen_composite_mean": round(sum(chosen_c) / max(1, len(chosen_c)), 4),
        "rejected_composite_mean": round(sum(rejected_c) / max(1, len(rejected_c)), 4),
        "gap_mean": round(sum(gaps) / max(1, len(gaps)), 4),
        "gap_min": round(min(gaps), 4) if gaps else None,
        "gap_max": round(max(gaps), 4) if gaps else None,
        "chosen_citation_ok_rate": round(sum(1 for r in records if r["meta"]["chosen_metrics"]["citation_ok"])
                                          / max(1, len(records)), 4),
        "chosen_overclaim_rate": round(sum(1 for r in records if r["meta"]["chosen_metrics"]["overclaim"])
                                        / max(1, len(records)), 4),
        "rejected_overclaim_rate": round(sum(1 for r in records if r["meta"]["rejected_metrics"]["overclaim"])
                                          / max(1, len(records)), 4),
        "thresholds": {"gap_min": GAP_MIN, "chosen_min": CHOSEN_MIN, "reject_max": REJECT_MAX,
                       "k": k, "temperature": TEMPERATURE},
    }


def run(*, dry_run: bool = False, mock: bool = False, local: bool = False,
        limit: int = 0, k: int = K, model: str = "", base_url: str = "",
        key_file: str | Path | None = None, write: bool = False,
        include_heldout: bool = False) -> dict:
    dataset = build_qa_dataset(include_heldout=include_heldout)
    key = be.api_key(key_file)
    key_source = ("env:DASHSCOPE_API_KEY" if (os.environ.get("DASHSCOPE_API_KEY") or "").strip()
                  else (str(Path(key_file) if key_file else be.KEY_FILE) if key else "未找到 key"))
    base_url = base_url or os.environ.get("DASHSCOPE_COMPAT_BASE") or be.DEFAULT_BASE
    if mock:
        backend = MockBackend()
        backend_name, use_model = "mock", "mock-synthetic"
    elif local:
        backend = LocalOllamaBackend(model=model or LOCAL_MODEL)
        backend_name, use_model = backend.name, backend.model
    else:
        use_model = model or os.environ.get("DASHSCOPE_MODEL") or be.DEFAULT_MODEL
        backend_name = "openai_compat"
        backend = None

    if dry_run or (not mock and not local and not key):
        plan = build_plan(dataset, backend_name=backend_name, model=use_model, k=k, limit=limit,
                          has_key=bool(key), base_url=base_url)
        payload = {"schema_version": "dpo_sampling.v1", "date": DATE, "mode": "dry-run",
                   "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                   "task": "W20-DPO 拒绝采样生成偏好对（k=8 + W13 确定性指标 top/bottom）",
                   "backend": {"name": backend_name, "model": use_model, "base_url": base_url,
                               "key_source": key_source, "real_call": False},
                   "dataset": {"source": str(W13_JSON), "answerable": len(dataset)},
                   "plan": plan, "summary": {}, "pairs": [],
                   "commands": _commands(), "notes": _notes(mode="dry-run")}
        if not key:
            payload["notes"].append("未找到 DASHSCOPE_API_KEY/key 文件：自动降级为 dry-run（未调用 API）。")
        if write:
            _write_outputs(payload)
        return payload

    if backend is None:
        backend = OpenAICompatBackend(key, base_url, use_model)
    t0 = time.monotonic()
    built = build_pairs(dataset, backend, k=k, limit=limit)
    records = [pair_record(p, backend_name=backend_name, model=use_model, k=k) for p in built["pairs"]]
    summary = summarize(records, built["skipped"], built["stats"],
                        dataset_n=len(dataset[:limit] if limit and limit > 0 else dataset),
                        k=k, backend_name=backend_name)
    mode = "mock" if mock else ("local" if local else "api")
    payload = {
        "schema_version": "dpo_sampling.v1", "date": DATE,
        "mode": mode,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "task": "W20-DPO 拒绝采样生成偏好对（k=8 + W13 确定性指标 top/bottom）",
        "backend": {"name": backend_name, "model": use_model, "base_url": base_url,
                    "key_source": key_source, "real_call": not (mock)},
        "dataset": {"source": str(W13_JSON),
                    "sha256": hashlib.sha256(W13_JSON.read_bytes()).hexdigest(),
                    "answerable": len(dataset),
                    "heldout_included": bool(include_heldout)},
        "elapsed_s": round(time.monotonic() - t0, 1),
        "summary": summary, "pairs": records,
        "commands": _commands(), "notes": _notes(mode=mode),
    }
    if write:
        _write_outputs(payload)
    return payload


def _commands() -> dict:
    return {
        "dry_run": "python tools/finetune/gen_dpo_pairs.py --dry-run",
        "mock": "python tools/finetune/gen_dpo_pairs.py --mock --write",
        "bailian_after_deploy": "python tools/finetune/gen_dpo_pairs.py --model <百炼部署实例ID> --k 8 --write",
        "local_ollama": "python tools/finetune/gen_dpo_pairs.py --local --k 8 --write",
        "limit_test": "python tools/finetune/gen_dpo_pairs.py --dry-run --limit 3 --k 4",
    }


def _notes(mode: str) -> list[str]:
    notes = [
        "问题集=W13 answer_eval 的 30 道可答题（问题/gold/上下文 ctx_ids 复用），可选 --heldout 扩概念题；",
        "评分=W13 确定性指标组合分 0.45*引用正确+0.30*groundedness+0.15*gold F1+0.10*无过度声明；",
        f"阈值：chosen 组合分≥{CHOSEN_MIN} 且引用正确/不拒答/无过度声明；rejected 组合分<{REJECT_MAX}；"
        f"top-bottom 分差≥{GAP_MIN}；k={K}，temperature={TEMPERATURE}；",
        "只读知识库/报告，不写生产库；不 commit（仅写 candidates.jsonl 与报告 2 个交付文件）。",
    ]
    if mode == "dry-run":
        notes.append("当前为 dry-run：未调用任何模型 API；部署后按 commands 执行真实采样。")
    elif mode == "mock":
        notes.append("当前为 mock：候选为合成答案，仅验证管道与筛选逻辑；候选文件不得直接用于训练。")
    return notes


def render_markdown(payload: dict) -> str:
    mode = payload["mode"]
    backend = payload["backend"]
    s = payload.get("summary") or {}
    plan = payload.get("plan") or {}
    lines = ["# DPO 拒绝采样偏好对生成（W20 · 2026-11-04）", "",
             f"> 模式：**{mode}**"
             + ("（未调用真实 API：仅计划/管道验证）" if mode in ("dry-run", "mock") else "（真实模型采样）"),
             f"> 模型端：`{backend['name']}` · model=`{backend['model']}` · base_url=`{backend['base_url']}` · "
             f"key_source={backend['key_source']}",
             f"> 问题集：W13 answer_eval 30 道可答题（{payload['dataset']['source']}）"
             + ("；已含 held-out 概念" if payload['dataset'].get('heldout_included') else ""),
             "", "## 一、方法", "",
             f"1. 每题采样 k={s.get('k', K)} 个候选（temperature={TEMPERATURE}）；",
             "2. 用 W13 确定性指标逐候选打分：引用正确/groundedness/gold F1/无过度声明，"
             "组合分 = 0.45*引用+0.30*grounded+0.15*gold+0.10*无过度声明；",
             f"3. 选 top1 为 chosen、bottom1 为 rejected；过滤：chosen 组合分≥{CHOSEN_MIN} 且引用正确/不拒答/"
             f"无过度声明；rejected 组合分<{REJECT_MAX}；分差≥{GAP_MIN}；",
             "4. 输出 v2 兼容 JSONL（prompt/chosen/rejected/source/category/concept/meta+metrics）。",
             "", "## 二、条数与分布", ""]
    if mode == "dry-run":
        lines += [f"- 计划：{plan.get('planned_questions')} 题 × k={plan.get('k')} = "
                  f"**{plan.get('planned_requests')} 次请求**（has_api_key={plan.get('has_api_key')}）；",
                  "- 样例 prompt 前 300 字：", "", "```text",
                  (plan.get("sample_prompt_preview") or "")[:300], "```", ""]
    else:
        lines += ["| 项 | 值 |", "|---|---:|",
                  f"| 可用偏好对 | **{s.get('pairs')}** / {s.get('questions_total')} 题（覆盖 {s.get('coverage_pct')}%） |",
                  f"| 候选总数 / 失败采样 | {s.get('candidates_generated')} / {s.get('failed_samples')} "
                  f"（成功率 {s.get('candidate_success_rate')}） |",
                  f"| chosen 组合分均值 | {s.get('chosen_composite_mean')} |",
                  f"| rejected 组合分均值 | {s.get('rejected_composite_mean')} |",
                  f"| 分差 mean/min/max | {s.get('gap_mean')} / {s.get('gap_min')} / {s.get('gap_max')} |",
                  f"| chosen 引用正确率 / 过度声明率 | {s.get('chosen_citation_ok_rate')} / {s.get('chosen_overclaim_rate')} |",
                  f"| rejected 过度声明率 | {s.get('rejected_overclaim_rate')} |",
                  f"| 跳过原因 | {json.dumps(s.get('skipped') or {}, ensure_ascii=False)} |", ""]
    lines += ["## 三、部署后命令", "", "```powershell"]
    for k, v in (payload.get("commands") or {}).items():
        lines.append(f"# {k}")
        lines.append(v)
    lines += ["```", "",
              "## 四、阈值", "",
              f"- k={K}、temperature={TEMPERATURE}；chosen 组合分≥{CHOSEN_MIN}、rejected<{REJECT_MAX}、"
              f"分差≥{GAP_MIN}；",
              "- 组合分权重：引用 0.45 / groundedness 0.30 / gold F1 0.15 / 无过度声明 0.10；",
              "- 候选文件：`research/finetune/dpo/quant_kb_dpo_v3_candidates.jsonl`（字段与 DPO v2 兼容）。", "",
              "## 五、局限", ""]
    for i, n in enumerate(payload.get("notes") or [], 1):
        lines.append(f"{i}. {n}")
    lines += ["", "> 研究输出，不构成投资建议。", ""]
    return "\n".join(lines)


def _write_outputs(payload: dict) -> tuple[str, str]:
    OUT_JSONL.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSONL.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in payload.get("pairs") or []) + "\n",
        encoding="utf-8")
    OUT_MD.write_text(render_markdown(payload), encoding="utf-8")
    payload["_outputs"] = {"jsonl": str(OUT_JSONL), "md": str(OUT_MD)}
    return str(OUT_JSONL), str(OUT_MD)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover
            pass
    ap = argparse.ArgumentParser(description="DPO 拒绝采样生成偏好对（k=8 + W13 确定性指标）")
    ap.add_argument("--dry-run", action="store_true", help="只打印采样计划，不调用模型")
    ap.add_argument("--mock", action="store_true", help="合成候选跑通管道（不联网）")
    ap.add_argument("--local", action="store_true", help="使用本地 ollama /api/chat")
    ap.add_argument("--local-model", default="", help=f"本地模型（默认 {LOCAL_MODEL}）")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 题（0=全部 30）")
    ap.add_argument("--k", type=int, default=K, help=f"每题采样数（默认 {K}）")
    ap.add_argument("--model", default="", help="百炼部署实例 ID/模型名")
    ap.add_argument("--base-url", default="", help=f"OpenAI 兼容 base_url（默认 {be.DEFAULT_BASE}）")
    ap.add_argument("--key-file", default="", help="DASHSCOPE_API_KEY 本地文件路径")
    ap.add_argument("--heldout", action="store_true", help="追加 held-out 概念题")
    ap.add_argument("--write", action="store_true",
                    help="写 candidates.jsonl 与 research/reports/dpo_sampling_20261104.md")
    args = ap.parse_args(argv)
    model = args.model or args.local_model
    payload = run(dry_run=args.dry_run, mock=args.mock, local=args.local, limit=args.limit,
                  k=args.k, model=model, base_url=args.base_url,
                  key_file=Path(args.key_file) if args.key_file else None,
                  write=args.write, include_heldout=args.heldout)
    s = payload.get("summary") or {}
    print(f"[gen_dpo_pairs] mode={payload['mode']} backend={payload['backend']['name']} "
          f"model={payload['backend']['model']}")
    if payload["mode"] == "dry-run":
        print(f"  计划：{payload['plan']['planned_questions']} 题 × k={payload['plan']['k']} = "
              f"{payload['plan']['planned_requests']} 请求（未调用 API）")
    else:
        print(f"  可用偏好对：{s.get('pairs')}/{s.get('questions_total')} 题；候选 {s.get('candidates_generated')}；"
              f"chosen 均值={s.get('chosen_composite_mean')} rejected 均值={s.get('rejected_composite_mean')}；"
              f"跳过={s.get('skipped')}")
    for key, val in (payload.get("_outputs") or {}).items():
        print(f"  输出({key})：{val}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
