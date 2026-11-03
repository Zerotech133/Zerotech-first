# -*- coding: utf-8 -*-
"""W17 百炼部署后评测（task-177）：W13 40 题 × 百炼 OpenAI 兼容接口复测。

用途
----
百炼 SFT 完成并部署后，用**同一批 W13 评测集**（`research/reports/answer_eval_20261104.json`
的 40 题：30 可答 + 10 不可答，含 W13 检索的 top5 上下文 ctx_ids）调用部署模型，复用 W13
确定性指标，输出与 W13 基线（引用正确率 0.725 / groundedness 0.4776 / 不可答拒答率 0.8 等）
的对比报告。

复用
----
- 指标：`tools/kb_corpus/answer_eval.py`（W13）的 deterministic 指标与 `judge_one()`：
  引用正确率 / groundedness / gold bigram F1 / 拒答 / 过度声明 / 长度异常；
- 上下文：W13 结果中的 ctx_ids → 从 W6 文档库（effective units + 400 卡）重建 top5 文本；
- prompt：与 W13 `answer_prompt()` 同格式（资料 [1..5] + 引用/拒答要求 + 问题）。

API 约定（阿里云百炼 Model Studio，OpenAI 兼容模式）
--------------------------------------------------
- base_url：默认北京 `https://dashscope.aliyuncs.com/compatible-mode/v1`
  （新加坡区：`https://dashscope-intl.aliyuncs.com/compatible-mode/v1`，用 `--base-url` 覆盖）；
- key：环境变量 `DASHSCOPE_API_KEY`，或本地文件 `C:\\项目\\_vault\\secrets\\dashscope_api_key.txt`；
- model：部署后的**模型实例 ID/名称**（如 `qwen3-14b-quantkb-xxxx`），用 `--model` 传入；
  未填时默认占位 `qwen3-14b-quantkb-<替换为部署实例ID>` 并在报告中标注。

安全/约束
--------
- **测试与本次交付不调用真实 API**：`--dry-run` 只打印请求计划（无 key 也可用）；`--mock` 用内置
  MockClient 走通全流程（结果标注 mock，非真实模型）；真实调用仅在部署后显式运行（无 `--dry-run`）。
- 只读：不写生产库；只写 `research/reports/bailian_eval_20261104.{json,md}`（`--write` 时）。

CLI 示例
--------
    # 部署前自检：打印 40 题请求计划（无需 key，可写报告）
    python tools/finetune/bailian_eval.py --dry-run --write
    python tools/finetune/bailian_eval.py --dry-run --limit 5

    # 管道自检（mock，不联网）
    python tools/finetune/bailian_eval.py --mock --limit 5

    # 部署后真实复测（需要 DASHSCOPE_API_KEY 或 key 文件）
    python tools/finetune/bailian_eval.py --model qwen3-14b-quantkb-xxxx --write
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
W13_PATH = REPO / "tools" / "kb_corpus" / "answer_eval.py"
W13_JSON = REPO / "research" / "reports" / "answer_eval_20261104.json"
OUT_JSON = REPO / "research" / "reports" / "bailian_eval_20261104.json"
OUT_MD = REPO / "research" / "reports" / "bailian_eval_20261104.md"
KEY_FILE = Path(r"C:/项目/_vault/secrets/dashscope_api_key.txt")
DEFAULT_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
INTL_BASE = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen3-14b-quantkb-<替换为部署实例ID>"
DATE = "20261104"
TARGETS = {"citation_accuracy": 0.90, "groundedness": 0.60, "correct_refusal_rate": 0.80,
           "gold_similarity": 0.30, "overclaim_rate": 0.05, "length_anomaly_rate": 0.10}
HIGHER_BETTER = ("citation_accuracy", "groundedness", "correct_refusal_rate", "gold_similarity")
W13_BASELINE_FOR_MD = {"citation_accuracy": 0.725, "groundedness": 0.4776,
                       "correct_refusal_rate": 0.8, "gold_similarity": 0.3845,
                       "overclaim_rate": 0.0, "length_anomaly_rate": 0.0}


# ================================================================ W13 复用
def _load_w13():
    """加载 W13 answer_eval 模块（确定性指标/judge_one/prompt；其内部只读加载 W6/W10 模块，无网络）。"""
    if "w13_answer_eval" in sys.modules:
        return sys.modules["w13_answer_eval"]
    spec = importlib.util.spec_from_file_location("w13_answer_eval", W13_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["w13_answer_eval"] = mod
    spec.loader.exec_module(mod)
    return mod


def api_key(key_file: str | Path | None = None) -> str:
    """优先环境变量 DASHSCOPE_API_KEY，其次本地 key 文件。"""
    k = (os.environ.get("DASHSCOPE_API_KEY") or "").strip()
    if k:
        return k
    p = Path(key_file) if key_file else KEY_FILE
    if p.exists():
        return p.read_text(encoding="utf-8").strip()
    return ""


def load_w13(path: str | Path = W13_JSON) -> dict:
    """读 W13 评测结果 JSON（40 题 + 聚合基线）。"""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return payload


def build_dataset(payload: dict, *, docs: list[dict] | None = None,
                  questions: list[dict] | None = None) -> list[dict]:
    """把 W13 结果拼成评测集：问题 + gold + 当次 top5 上下文（文档对象）。

    docs/questions 可注入（测试/离线）；默认从 W6 文档库 + W10/W13 题库重建。
    """
    w13 = _load_w13()
    if docs is None:
        docs = w13.W6MOD.load_docs()
    if questions is None:
        questions = w13.build_questions(w13.W10MOD.load_questions())
    doc_map = {d["doc_id"]: d for d in docs}
    qmap = {q["qid"]: q for q in questions}
    dataset = []
    for r in payload.get("results") or []:
        q = qmap.get(r["qid"])
        if not q:
            continue
        ctx = [doc_map[i] for i in (r.get("ctx_ids") or []) if i in doc_map]
        if not ctx:
            continue
        gold_text = q.get("gold_text") or ""
        if not gold_text and q.get("gold_doc_ids"):
            gold_text = str(doc_map.get(q["gold_doc_ids"][0], {}).get("text") or "")
        dataset.append({**q, "gold_text": gold_text, "ctx": ctx,
                        "w13_answer": r.get("answer"), "w13_ctx_ids": r.get("ctx_ids"),
                        "w13_metrics": {k: r.get(k) for k in (
                            "citation_ok", "groundedness", "gold_similarity", "refused",
                            "overclaim", "length", "length_anomaly")}})
    return dataset


def build_prompt(question: str, ctx: list[dict]) -> str:
    """与 W13 answer_prompt 同格式：资料 [1..5] + 引用/拒答要求 + 问题。"""
    return ("你是投研知识助手。仅依据下列资料回答，不要编造；用 2-4 句话回答，"
            "并在末尾用 [1][2] 标注所依据资料编号；若资料不足以回答，请明确说「资料不足/资料未提及」。\n\n"
            + "\n".join(f"[{i+1}] ({c.get('doc_id', '')}) {str(c.get('text') or '')[:200]}"
                        for i, c in enumerate(ctx))
            + f"\n\n问题：{question}\n回答：")


# ================================================================ API 调用
def build_client(api_key_value: str, base_url: str, timeout: float = 60.0):
    """构造 OpenAI 兼容客户端（百炼/OpenAI-兼容端点）。延迟 import，dry-run 不触发。"""
    from openai import OpenAI
    return OpenAI(api_key=api_key_value, base_url=base_url, timeout=timeout)


def parse_answer(resp) -> str:
    """从 OpenAI ChatCompletion 响应（对象或 dict）提取文本。"""
    if resp is None:
        return ""
    choices = getattr(resp, "choices", None)
    if choices is None and isinstance(resp, dict):
        choices = resp.get("choices")
    if not choices:
        return ""
    first = choices[0]
    msg = getattr(first, "message", None)
    if msg is None and isinstance(first, dict):
        msg = first.get("message")
    if msg is None:
        return ""
    content = getattr(msg, "content", None)
    if content is None and isinstance(msg, dict):
        content = msg.get("content")
    return str(content or "").strip()


def call_model(client, model: str, prompt: str, *, temperature: float = 0.0,
               max_tokens: int = 180, retries: int = 1) -> str:
    """调用一次 chat.completions；仅对非鉴权类瞬时错误做有限重试。"""
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature, max_tokens=max_tokens)
            return parse_answer(resp)
        except Exception as exc:  # noqa: BLE001 - 由调用方决定是否重试
            last = exc
            name = type(exc).__name__.lower()
            if "auth" in name or "permission" in name or "invalid" in name:
                break
            if attempt < retries:
                time.sleep(min(2.0, 0.5 * (attempt + 1)))
    raise last  # type: ignore[misc]


class MockClient:
    """离线 mock：确定性回答（可答→引用上下文；不可答→拒答；另 1 题模拟超时/错误）。"""

    def __init__(self, fail_qids: tuple[str, ...] = ("U05",), timeout_qids: tuple[str, ...] = ()):
        self.calls = 0
        self.fail_qids = set(fail_qids)
        self.timeout_qids = set(timeout_qids)

    def _create(self, *, model, messages, temperature, max_tokens):
        self.calls += 1
        prompt = messages[0]["content"]
        m = re.search(r"问题：(.+?)\n回答：", prompt, re.S)
        question = (m.group(1).strip() if m else "")
        qid_guess = ""
        for line in prompt.splitlines():
            if line.startswith("问题："):
                qid_guess = line
                break
        if qid_guess and any(q in prompt for q in self.fail_qids):
            raise RuntimeError("mock api error")
        if any(q in prompt for q in self.timeout_qids):
            raise TimeoutError("mock timeout")
        if "如何申请申根旅游签证" in question or "火星表面" in question or "手冲咖啡" in question:
            answer = "资料不足：知识库未涵盖该问题。"
        else:
            ctx = re.findall(r"\[(\d+)\] \(([^)]+)\) (.{0,80})", prompt)
            src = ctx[0][1] if ctx else ""
            answer = f"根据资料[{1}]，要点与原文相关表述一致（{src[:20]}）。[1]"
        return {"choices": [{"message": {"content": answer}}]}

    @property
    def chat(self):
        client = self

        class _Completions:
            def create(self, **kwargs):
                return client._create(**kwargs)

        class _Chat:
            completions = _Completions()

        return _Chat()


# ================================================================ 评测
def evaluate(dataset: list[dict], *, baseline_agg: dict, client, model: str,
             limit: int = 0, mock: bool = False) -> dict:
    """逐题调用 + W13 judge_one 评分 + 聚合（复用 W13 公式）。"""
    w13 = _load_w13()
    items = dataset[:limit] if limit and limit > 0 else dataset
    results = []
    t0 = time.monotonic()
    for item in items:
        prompt = build_prompt(item["question"], item["ctx"])
        answer, error = "", None
        try:
            answer = call_model(client, model, prompt)
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
        judged = w13.judge_one(item, item["ctx"], answer)
        judged["error"] = error
        results.append(judged)
    agg_bundle = aggregate_results(results, baseline_agg)
    return {
        "mode": "mock" if mock else "api",
        "model": model,
        "elapsed_s": round(time.monotonic() - t0, 2),
        "results": results,
        "aggregate": agg_bundle["aggregate"],
        "comparison": agg_bundle["comparison"],
    }


def aggregate_results(results: list[dict], baseline_agg: dict | None = None) -> dict:
    """与 W13 同口径聚合 + 与 W13 基线对比（delta/达标）。"""
    n = len(results)
    ans = [r for r in results if r["answerable"]]
    unans = [r for r in results if not r["answerable"]]
    agg = {
        "questions_total": n, "evaluated": n, "generation_evaluated": n,
        "errors": sum(1 for r in results if r.get("error")),
        "citation_accuracy": round(sum(1 for r in results if r["citation_ok"]) / max(1, n), 4),
        "citation_valid_rate": round(sum(1 for r in results if r["citation_valid"]) / max(1, n), 4),
        "groundedness": round(sum(r["groundedness"] for r in results) / max(1, n), 4),
        "gold_similarity": round(sum(r["gold_similarity"] for r in ans
                                     if r["gold_similarity"] is not None) / max(1, len(ans)), 4),
        "refusal_rate": round(sum(1 for r in results if r["refused"]) / max(1, n), 4),
        "correct_refusal_rate": round(sum(1 for r in unans if r["refused"]) / max(1, len(unans)), 4),
        "over_refusal_rate": round(sum(1 for r in ans if r["refused"]) / max(1, len(ans)), 4),
        "overclaim_rate": round(sum(1 for r in results if r["overclaim"]) / max(1, n), 4),
        "length_anomaly_rate": round(sum(1 for r in results if r["length_anomaly"]) / max(1, n), 4),
        "avg_answer_len": round(sum(r["length"] for r in results) / max(1, n), 1),
        "answerable_n": len(ans), "unanswerable_n": len(unans),
    }
    comparison = {}
    if baseline_agg:
        for k in ("citation_accuracy", "groundedness", "correct_refusal_rate", "gold_similarity",
                  "overclaim_rate", "length_anomaly_rate"):
            cur, base = agg.get(k), baseline_agg.get(k)
            delta = round(cur - base, 4) if (cur is not None and base is not None) else None
            target = TARGETS.get(k)
            if target is None:
                passed = None
            elif k in HIGHER_BETTER:
                passed = bool(cur is not None and cur >= target)
            else:
                passed = bool(cur is not None and cur <= target)
            comparison[k] = {"current": cur, "w13_baseline": base, "delta": delta,
                             "target": target, "pass": passed}
    return {"aggregate": agg, "comparison": comparison}


# ================================================================ 交付输出
def build_plan(dataset: list[dict], *, model: str, base_url: str, limit: int = 0,
               has_key: bool = False) -> dict:
    items = dataset[:limit] if limit and limit > 0 else dataset
    return {
        "mode": "dry-run", "model": model, "base_url": base_url,
        "has_api_key": has_key, "questions_total": len(dataset), "planned": len(items),
        "request_preview": {
            "url": f"{base_url.rstrip('/')}/chat/completions",
            "messages": [{"role": "user", "content": build_prompt(items[0]["question"], items[0]["ctx"])[:600]}]
            if items else [],
            "params": {"model": model, "temperature": 0.0, "max_tokens": 180},
        },
        "items": [{"qid": q["qid"], "type": q["type"], "answerable": q["answerable"],
                   "question": q["question"], "ctx_ids": q.get("w13_ctx_ids"),
                   "prompt_chars": len(build_prompt(q["question"], q["ctx"]))} for q in items],
    }


def build_payload(*, payload_mode: str, model: str, base_url: str, dataset: list[dict],
                  baseline_agg: dict, evaluation: dict | None = None,
                  plan: dict | None = None, key_source: str = "") -> dict:
    dataset_meta = {
        "source": str(W13_JSON),
        "sha256": hashlib.sha256(W13_JSON.read_bytes()).hexdigest() if W13_JSON.is_file() else None,
        "total": len(dataset),
        "answerable": sum(1 for q in dataset if q["answerable"]),
        "unanswerable": sum(1 for q in dataset if not q["answerable"]),
        "note": "W13 40 题（30 可答 + 10 不可答）；上下文为 W13 hybrid top5 的 ctx_ids 重建文本",
    }
    ctx = {
        "schema_version": "bailian_eval.v1", "date": DATE,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "task": "W17-百炼部署后评测（W13 40 题 × 百炼 OpenAI 兼容接口）",
        "mode": payload_mode,
        "api": {"base_url": base_url, "model": model, "key_source": key_source,
                "dry_run": payload_mode == "dry-run", "mock": payload_mode == "mock"},
        "dataset": dataset_meta,
        "w13_baseline": {"aggregate": baseline_agg,
                         "targets": TARGETS,
                         "source": "research/reports/answer_eval_20261104.json"},
        "plan": plan or {},
    }
    if evaluation:
        ctx.update({"results": evaluation["results"], "aggregate": evaluation["aggregate"],
                    "comparison": evaluation.get("comparison") or {},
                    "elapsed_s": evaluation.get("elapsed_s"), "model": evaluation.get("model")})
    else:
        ctx.update({"results": [], "aggregate": {}, "comparison": {}})
    ctx["commands"] = {
        "dry_run": "python tools/finetune/bailian_eval.py --dry-run --limit 5 --write",
        "mock": "python tools/finetune/bailian_eval.py --mock --limit 5",
        "real": "python tools/finetune/bailian_eval.py --model <部署实例ID> --write",
        "real_intl": "python tools/finetune/bailian_eval.py --model <部署实例ID> "
                     f"--base-url {INTL_BASE} --write",
    }
    ctx["notes"] = [
        "本脚本只读知识库/报告；真实调用仅在部署后显式执行（本次交付未调用真实 API）。",
        "model 需填百炼控制台「部署后的模型实例 ID/名称」（如 qwen3-14b-quantkb-xxxx）。",
        "W13 基线：citation_accuracy=0.725、groundedness=0.4776、correct_refusal_rate=0.8、"
        "gold_similarity=0.3845（40 题口径）。",
        "指标为确定性规则（bigram/引用解析/词典），非 LLM 裁判；与 W13 同口径可比。",
    ]
    return ctx


def render_markdown(payload: dict) -> str:
    a = payload["aggregate"] or {}
    base = payload["w13_baseline"]["aggregate"]
    mode = payload["mode"]
    lines = [f"# 百炼部署后评测（W17 · 2026-11-04）", "",
             f"> 模式：**{mode}**"
             + ("（未调用真实 API：仅请求计划/管道校验）" if mode in ("dry-run", "mock") else "（真实 API 复测）"),
             f"> 模型：`{payload['api']['model']}` · base_url：`{payload['api']['base_url']}` · "
             f"key_source：{payload['api']['key_source'] or '未找到'}",
             f"> 数据：{payload['dataset']['total']} 题（可答 {payload['dataset']['answerable']} + "
             f"不可答 {payload['dataset']['unanswerable']}）· 来源 `{payload['dataset']['source']}`",
             "", "## 一、部署后如何运行（命令）", "", "```powershell"]
    for k, v in payload["commands"].items():
        lines.append(f"# {k}")
        lines.append(v)
    lines += ["```", "",
              "**如何填模型实例 ID**：百炼控制台 → 模型部署/我的模型 → 部署实例 → 复制实例 ID 或模型名称，"
              "替换 `--model` 参数（默认占位 `qwen3-14b-quantkb-<替换为部署实例ID>`）。"
              "Key 放 `C:/项目/_vault/secrets/dashscope_api_key.txt` 或环境变量 `DASHSCOPE_API_KEY`。",
              "", "## 二、指标对比（W13 基线 vs 部署后）", "",
              "| 指标 | W13 基线 | 部署后 | Δ | 目标 | 达标 |", "|---|---:|---:|---:|---:|---|"]
    order = ("citation_accuracy", "groundedness", "correct_refusal_rate", "gold_similarity",
             "overclaim_rate", "length_anomaly_rate")
    labels = {"citation_accuracy": "引用正确率", "groundedness": "groundedness",
              "correct_refusal_rate": "不可答拒答率", "gold_similarity": "gold 相似度",
              "overclaim_rate": "过度声明率", "length_anomaly_rate": "长度异常率"}
    for k in order:
        cur = a.get(k)
        b = base.get(k)
        d = round(cur - b, 4) if (cur is not None and b is not None) else None
        target = TARGETS.get(k)
        ok = payload["comparison"].get(k, {}).get("pass")
        lines.append(f"| {labels[k]} | {b} | {cur if cur is not None else '—（待真实复测）'} | "
                     f"{('+' if (d or 0) > 0 else '') + str(d) if d is not None else '—'} | "
                     f"{'≥' if k in HIGHER_BETTER else '≤'}{target} | "
                     f"{'✅' if ok else ('❌' if ok is False else '—')} |")
    lines += ["", f"> 本次 {payload['mode']} 模式："
              + ("`results` 为空，指标待部署后真实运行填充；上表「部署后」列为真实运行时的对比位。"
                 if mode == "dry-run" else
                 "mock 模式结果仅用于验证代码路径，不代表真实模型指标。"), "",
              "## 三、请求计划（dry-run）", ""]
    plan = payload.get("plan") or {}
    if plan:
        lines += [f"- 计划题数：{plan.get('planned')} / {plan.get('questions_total')}；"
                  f"has_api_key={plan.get('has_api_key')}",
                  f"- URL：`{(plan.get('request_preview') or {}).get('url', '')}`",
                  f"- 样例 prompt（前 400 字）：", "", "```text",
                  ((plan.get("request_preview") or {}).get("messages") or [{}])[0].get("content", "")[:400],
                  "```", ""]
        lines += ["| qid | type | answerable | prompt字符 | ctx_ids |", "|---|---|---|---:|---|"]
        for it in plan.get("items", [])[:50]:
            lines.append(f"| {it['qid']} | {it['type']} | {it['answerable']} | {it['prompt_chars']} | "
                         f"{','.join(it.get('ctx_ids') or [])[:60]} |")
        lines.append("")
    if payload.get("mock_sanity"):
        ms = payload["mock_sanity"]
        magg = ms.get("aggregate") or {}
        mcmp = ms.get("comparison") or {}
        lines += ["## 三-B、评测管道自检（mock，非真实模型结果）", "",
                  f"> {ms.get('note', '')}（{magg.get('evaluated')} 题；errors={magg.get('errors')}）", "",
                  "| 指标 | W13 基线 | mock 管道 | 目标 |", "|---|---:|---:|---:|"]
        for k in ("citation_accuracy", "groundedness", "correct_refusal_rate", "gold_similarity"):
            lines.append(f"| {labels.get(k, k)} | {W13_BASELINE_FOR_MD.get(k, '—')} | "
                         f"{magg.get(k)} | {'≥' if k in HIGHER_BETTER else '≤'}{TARGETS.get(k)} |")
        lines.append("")
    if payload.get("results"):
        lines += ["## 四、逐题结果（前 10 题）", "",
                  "| qid | answerable | citation_ok | groundedness | gold_F1 | refused | 长度 | error |",
                  "|---|---|---|---:|---:|---|---:|---|"]
        for r in payload["results"][:10]:
            lines.append(f"| {r['qid']} | {r['answerable']} | {r['citation_ok']} | {r['groundedness']} | "
                         f"{r['gold_similarity']} | {r['refused']} | {r['length']} | {r.get('error') or '—'} |")
        lines.append("")
    lines += ["## 五、结论", "",
              "- 脚本已就绪：`--dry-run` 可离线生成 40 题请求计划；部署后填实例 ID 即可一键复测并与 W13 基线同口径对比。"
              if mode == "dry-run" else
              "- 本次为 mock/真实模式运行，指标见上表（mock 不代表真实模型）。",
              "- 合格标准（W13 目标阈值）：引用正确率 ≥0.90、groundedness ≥0.60、不可答拒答率 ≥0.80、"
              "gold 相似度 ≥0.30、过度声明 ≤0.05、长度异常 ≤0.10。",
              "- 若部署后指标未达阈值：优先检查部署实例是否正确、prompt 是否与训练一致、以及检索上下文是否同 W13（ctx_ids 固定）。",
              "", "## 六、局限", ""]
    for i, note in enumerate(payload["notes"], 1):
        lines.append(f"{i}. {note}")
    lines += ["", "> 研究输出，不构成投资建议。", ""]
    return "\n".join(lines)


def write_outputs(payload: dict, json_path: str | Path | None = None,
                  md_path: str | Path | None = None) -> tuple[str, str]:
    jp = Path(json_path) if json_path else OUT_JSON
    mp = Path(md_path) if md_path else OUT_MD
    jp.parent.mkdir(parents=True, exist_ok=True)
    jp.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    mp.write_text(render_markdown(payload), encoding="utf-8")
    return str(jp), str(mp)


# ================================================================ CLI
def run(*, dry_run: bool = False, mock: bool = False, limit: int = 0, model: str = "",
        base_url: str = "", write: bool = False, key_file: str | Path | None = None,
        w13_path: str | Path = W13_JSON) -> dict:
    payload_w13 = load_w13(w13_path)
    baseline_agg = payload_w13.get("aggregate") or {}
    dataset = build_dataset(payload_w13)
    key = api_key(key_file)
    key_source = ("env:DASHSCOPE_API_KEY" if (os.environ.get("DASHSCOPE_API_KEY") or "").strip()
                  else (str(Path(key_file) if key_file else KEY_FILE) if key else "未找到 key"))
    model = model or os.environ.get("DASHSCOPE_MODEL") or DEFAULT_MODEL
    base_url = base_url or os.environ.get("DASHSCOPE_COMPAT_BASE") or DEFAULT_BASE
    if dry_run or (not mock and not key):
        plan = build_plan(dataset, model=model, base_url=base_url, limit=limit, has_key=bool(key))
        # 管道自检（mock，不联网）：验证 40 题走通 judge/aggregate，结果仅作代码路径证据
        mock_eval = evaluate(dataset, baseline_agg=baseline_agg, client=MockClient(),
                             model=model, limit=limit, mock=True)
        payload = build_payload(payload_mode="dry-run", model=model, base_url=base_url,
                                dataset=dataset, baseline_agg=baseline_agg, plan=plan,
                                key_source=key_source)
        payload["mock_sanity"] = {"mode": "mock", "note": "非真实模型结果，仅验证评测管道",
                                  "aggregate": mock_eval["aggregate"],
                                  "comparison": mock_eval["comparison"]}
        payload["notes"].append("未找到 DASHSCOPE_API_KEY/本地 key 文件：自动降级为 dry-run（未调用 API）。"
                                if not key else "显式 --dry-run：仅生成请求计划。")
    else:
        client = MockClient() if mock else build_client(key, base_url)
        evaluation = evaluate(dataset, baseline_agg=baseline_agg, client=client, model=model,
                              limit=limit, mock=mock)
        payload = build_payload(payload_mode="mock" if mock else "api", model=model,
                                base_url=base_url, dataset=dataset, baseline_agg=baseline_agg,
                                evaluation=evaluation,
                                plan=build_plan(dataset, model=model, base_url=base_url, limit=limit,
                                                has_key=bool(key)),
                                key_source=key_source)
    if write:
        jp, mp = write_outputs(payload)
        payload["_outputs"] = {"json": jp, "md": mp}
    return payload


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover
            pass
    ap = argparse.ArgumentParser(description="百炼部署后评测（W13 40 题，OpenAI 兼容接口）")
    ap.add_argument("--dry-run", action="store_true", help="只打印请求计划，不调用 API")
    ap.add_argument("--mock", action="store_true", help="用内置 MockClient 走通全流程（不联网）")
    ap.add_argument("--limit", type=int, default=0, help="只评测前 N 题（0=全部 40）")
    ap.add_argument("--model", default="", help="百炼部署实例 ID/模型名")
    ap.add_argument("--base-url", default="", help=f"OpenAI 兼容 base_url（默认北京 {DEFAULT_BASE}）")
    ap.add_argument("--key-file", default="", help="DASHSCOPE_API_KEY 本地文件路径")
    ap.add_argument("--write", action="store_true", help="写 research/reports/bailian_eval_20261104.{json,md}")
    args = ap.parse_args(argv)
    payload = run(dry_run=args.dry_run, mock=args.mock, limit=args.limit, model=args.model,
                  base_url=args.base_url, write=args.write,
                  key_file=Path(args.key_file) if args.key_file else None)
    agg = payload.get("aggregate") or {}
    print(f"[bailian_eval] mode={payload['mode']} model={payload['api']['model']} "
          f"base={payload['api']['base_url']} key={payload['api']['key_source']}")
    if payload["mode"] == "dry-run":
        print(f"  请求计划：{payload['plan'].get('planned')}/{payload['plan'].get('questions_total')} 题；"
              f"未调用 API（dry-run）")
    else:
        print(f"  评测 {agg.get('evaluated')} 题：citation_accuracy={agg.get('citation_accuracy')} "
              f"groundedness={agg.get('groundedness')} correct_refusal_rate={agg.get('correct_refusal_rate')} "
              f"gold_similarity={agg.get('gold_similarity')}")
    for k, v in (payload.get("_outputs") or {}).items():
        print(f"  输出({k})：{v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
