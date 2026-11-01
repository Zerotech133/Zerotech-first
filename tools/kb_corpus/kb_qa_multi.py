# -*- coding: utf-8 -*-
"""多单元聚合回答 MVP：检索 top-k 单元 → 聚类 → 多单元聚合 prompt（允许综合推断）→ 与 v3 单单元对比。

背景：wave15 v3 证明可答率瓶颈=「单条知识单元信息量不足」（12 条抽验 9 条 LLM 拒答），
并非检索或 claim 覆盖问题。本模块尝试用「多单元聚合」突破：
  ① 检索 top-k（默认 k=8）知识单元，按 concept / 同书章节聚类（簇内按融合分排序）；
  ② 聚合 prompt：同一簇的多条单元（claim+evidence）一起给 qwen3.5:9b，**允许基于多条单元综合推断**，
     但必须区分【直接事实】/【推断】、引用 [unit_id] 与页码；材料无关时仍拒答；
  ③ 对 wave15 v3 的 12 条抽验问题各跑一次多单元回答，与 v3 单单元结果对比：
     可答率 / 平均引用数 / 拒答数 / 耗时，输出结论「多单元是否突破瓶颈」；
  ④ 输出 `research/reports/kb_qa_multi_20261101.{json,md}`（逐条对比+汇总+结论+风险）。

CLI：
  python tools/kb_corpus/kb_qa_multi.py ask --q "M9 覆铜板对 PCB 价值量的影响？" --k 8 --multi
  python tools/kb_corpus/kb_qa_multi.py eval --n 12 --k 8
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from functools import lru_cache
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
V3_BANK = REPO / "research" / "reports" / "kb_qa_bank_v3_20261101.json"
V3_EVAL = REPO / "research" / "reports" / "kb_qa_multi_20261101.json"
OUT_JSON = REPO / "research" / "reports" / "kb_qa_multi_20261101.json"
OUT_MD = REPO / "research" / "reports" / "kb_qa_multi_20261101.md"
DEFAULT_K = 8
MIN_K = 5
MAX_K = 8
DEFAULT_MODEL = "qwen3.5:9b"
DEFAULT_TIMEOUT = 300
DEFAULT_DEADLINE = 600
PROMPT_VERSIONS = ("v1", "v4")
DEFAULT_PROMPT_VERSION = "v1"      # 默认 v1（v4 为可选版本，未经扩样不切默认）
MAX_CLUSTERS = 3
MAX_UNITS_PER_CLUSTER = 3
MAX_ANSWER_CHARS = 400
REFUSAL_MARK = "没有足够依据"


class MultiError(RuntimeError):
    """多单元问答异常（问题库不可读等）。"""


def _load_module(name: str, filename: str):
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise MultiError(f"无法加载 {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_kb_qa = None


def kb_qa():
    global _kb_qa
    if _kb_qa is None:
        _kb_qa = _load_module("kb_qa_for_multi", "kb_qa.py")
    return _kb_qa


def norm_text(text: str) -> str:
    return re.sub(r"[\s，。；：、（）()【】\[\]“”\"'’‘·…—\-?？!！]+", "", str(text or "").lower())


def _clip(text, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


# ---------------------------------------------------------------------------
# ① 检索 + 聚类
# ---------------------------------------------------------------------------
def _cached_search():
    qa = kb_qa()
    try:
        api = qa.kb_api()
        if not getattr(api.load_units, "_cached_for_multi", False):
            api.load_units = lru_cache(maxsize=1)(api.load_units)
            api.load_units._cached_for_multi = True
        if not getattr(api.load_corpus, "_cached_for_multi", False):
            api.load_corpus = lru_cache(maxsize=1)(api.load_corpus)
            api.load_corpus._cached_for_multi = True
    except Exception:  # noqa: BLE001
        pass

    def _search(question: str, topk: int):
        return qa.search(question, topk=topk)

    return _search


def cluster_hits(hits: list[dict], max_clusters: int = MAX_CLUSTERS,
                 max_units: int = MAX_UNITS_PER_CLUSTER) -> list[dict]:
    """把检索 top-k 按「concept / 同书章节」聚类；簇按最高融合分排序，簇内按融合分取前 max_units。"""
    unit_hits = [h for h in (hits or []) if isinstance(h, dict) and h.get("kind") == "units"]
    source = unit_hits or [h for h in (hits or []) if isinstance(h, dict)]
    groups: dict[str, list[dict]] = {}
    meta: dict[str, dict] = {}
    for h in source:
        concept_key = norm_text(h.get("concept"))[:20]
        if concept_key:
            key = "concept:" + concept_key
        else:
            key = f"book:{h.get('book') or ''}|{h.get('chapter') or ''}"
        groups.setdefault(key, []).append(h)
        meta.setdefault(key, {"book": h.get("book"), "chapter": h.get("chapter"),
                              "concept": h.get("concept")})
    clusters = []
    for key, group in groups.items():
        group = sorted(group, key=lambda x: -float(x.get("fused_score") or 0.0))[:max_units]
        clusters.append({"key": key, "label": meta[key].get("concept") or meta[key].get("book") or key,
                         "book": meta[key].get("book"), "chapter": meta[key].get("chapter"),
                         "concept": meta[key].get("concept"),
                         "max_score": max(float(h.get("fused_score") or 0.0) for h in group),
                         "units": [{"unit_id": h.get("unit_id"), "book": h.get("book"),
                                    "chapter": h.get("chapter"), "page": h.get("page_start"),
                                    "concept": h.get("concept"),
                                    "text": _clip(h.get("text"), 220),
                                    "fused_score": h.get("fused_score")} for h in group]})
    clusters.sort(key=lambda c: -c["max_score"])
    return clusters[:max(1, int(max_clusters))]


def retrieve_clusters(question: str, k: int = DEFAULT_K, search_fn=None,
                      max_clusters: int = MAX_CLUSTERS) -> list[dict]:
    k = max(MIN_K, min(MAX_K, int(k)))
    hits = (search_fn or _cached_search())(question, k) or []
    return cluster_hits(hits, max_clusters=max_clusters)


# ---------------------------------------------------------------------------
# ② 多单元聚合 prompt + 生成
# ---------------------------------------------------------------------------
def format_clusters(clusters: list[dict]) -> str:
    if not clusters:
        return "（无检索材料）"
    lines = []
    for i, cluster in enumerate(clusters, 1):
        page = ""
        lines.append(f"【簇{i}】主题：{cluster.get('concept') or '—'} · "
                     f"《{cluster.get('book') or '未知'}》{('·' + cluster['chapter']) if cluster.get('chapter') else ''}")
        for unit in cluster.get("units") or []:
            page = f"p.{unit.get('page')}" if unit.get("page") is not None else "页码缺失"
            lines.append(f"  - [{unit.get('unit_id')}] {page} 原文：{unit.get('text')}")
    return "\n".join(lines)


FULL_EXAMPLE_V4 = (
    "【事实摘录】\n"
    "【事实】M9 级覆铜板支持 224Gbps 与 78 层 PCB 量产（[unit_A] p.2）。\n"
    "【事实】单颗 GPU 配套 PCB 价值量约为传统方案三倍（[unit_B] p.5）。\n"
    "【推断】\n"
    "【推断】综合两条材料，高速材料升级将带动单机柜 PCB 价值量提升"
    "（依据：[unit_A] p.2、[unit_B] p.5）。"
)


def build_multi_prompt_v4(question: str, clusters: list[dict]) -> str:
    """prompt v4（可选）：事实行逐行【事实】+引用、推断行【推断】≤2、附完整示例。"""
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
        f"【完整示例】\n{FULL_EXAMPLE_V4}\n\n"
        f"【问题】{question}\n\n【检索材料（按主题/章节聚类）】\n{format_clusters(clusters)}\n\n"
        "【回答（按【事实摘录】段 → 【推断】段的格式逐行输出）】"
    )


def build_multi_prompt(question: str, clusters: list[dict],
                       prompt_version: str = DEFAULT_PROMPT_VERSION) -> str:
    """多单元聚合 prompt（按版本）：v1 默认；v4 为可选版本（事实行/推断行分段格式）。"""
    version = (prompt_version or DEFAULT_PROMPT_VERSION).strip().lower()
    if version not in PROMPT_VERSIONS:
        raise MultiError(f"prompt_version 只能是 {'/'.join(PROMPT_VERSIONS)}（收到 {prompt_version!r}）")
    if version == "v4":
        return build_multi_prompt_v4(question, clusters)
    return (
        "你是研报知识库问答助手。下面是按主题/章节聚类后的多条知识单元（同一簇代表相关证据）。\n"
        "必须遵守：\n"
        "1) 允许基于多条单元进行综合推断，但每个论点必须标注类型：\n"
        "   - 【直接事实】= 材料原文明确写出的事实（不得改写关键数据）；\n"
        "   - 【推断】= 你基于多条单元综合得出的判断（必须说明依据哪几个单元）。\n"
        "2) 每个论点后必须引用 [unit_id] 与页码（直接事实必须引用；推断建议引用依据单元）。\n"
        "3) 不得编造材料之外的事实；材料完全无法支撑时，回答「知识库中没有足够依据回答该问题。」\n"
        "4) 中文、先结论后依据、不超过 400 字。\n\n"
        f"【问题】{question}\n\n【检索材料（聚类）】\n{format_clusters(clusters)}\n\n"
        "【回答（区分【直接事实】/【推断】）】"
    )


def call_ollama(prompt: str, model: str = DEFAULT_MODEL, timeout: float = DEFAULT_TIMEOUT,
                num_predict: int = 1600) -> str:
    body = {"model": model, "stream": False, "think": False,
            "messages": [{"role": "user", "content": prompt}],
            "options": {"temperature": 0.1, "num_predict": int(num_predict)}}
    req = urllib.request.Request("http://127.0.0.1:11434/api/chat",
                                 data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise MultiError(f"Ollama 调用失败：{exc}") from exc
    content = ((payload.get("message") or {}).get("content") or "").strip()
    if not content:
        raise MultiError("Ollama 返回空内容")
    return content


# ---------------------------------------------------------------------------
# ③ 引用校验 / 置信 / 降级
# ---------------------------------------------------------------------------
_CITE_RE = re.compile(r"[\[【]([0-9a-fA-F]{8,12})[\]】]")
_FACT_RE = re.compile(r"【直接事实】|\[事实\]|直接事实")
_INFER_RE = re.compile(r"【推断】|\[推断\]|推断")


def validate_multi_answer(answer: str, clusters: list[dict]) -> dict:
    """校验回答：引用 ID、直接事实/推断标记计数、拒答判定、幻觉引用。"""
    available = {}
    for cluster in clusters:
        for unit in cluster.get("units") or []:
            if unit.get("unit_id"):
                available[unit["unit_id"]] = unit
    cited = []
    seen = set()
    for cid in _CITE_RE.findall(answer or ""):
        cid = cid.lower()
        if cid not in seen:
            seen.add(cid)
            cited.append(cid)
    valid = [c for c in cited if c in available]
    hallucinated = [c for c in cited if c not in available]
    refusal = REFUSAL_MARK in str(answer or "")
    return {"cited_ids": valid, "hallucinated_ids": hallucinated,
            "citations": [{"unit_id": cid, "book": available[cid].get("book"),
                           "chapter": available[cid].get("chapter"),
                           "page": available[cid].get("page"),
                           "text": available[cid].get("text")} for cid in valid],
            "direct_fact_markers": len(_FACT_RE.findall(answer or "")),
            "inference_markers": len(_INFER_RE.findall(answer or "")),
            "refusal": refusal,
            "citation_missing": not cited and bool(clusters) and not refusal,
            "available_units": len(available)}


def assess_confidence(answer: str, check: dict, clusters: list[dict], fallback: bool) -> dict:
    if not clusters:
        return {"level": "none", "reasons": ["检索无材料，按拒答处理"]}
    if check["refusal"]:
        return {"level": "none", "reasons": ["模型判断材料不足以回答（拒答）"]}
    reasons = []
    if check["hallucinated_ids"]:
        reasons.append(f"存在未知引用 {len(check['hallucinated_ids'])} 个（已剔除）")
    if check["citation_missing"]:
        reasons.append("回答未标注 [unit_id] 引用")
    if fallback:
        return {"level": "low", "reasons": reasons + ["LLM 失败→规则式多单元摘录降级"]}
    if len(check["citations"]) >= 2 and len(clusters) >= 2:
        return {"level": "high", "reasons": reasons + [
            f"{len(check['citations'])} 条可核验引用、{len(clusters)} 个聚类",
            f"直接事实标记 {check['direct_fact_markers']}、推断标记 {check['inference_markers']}"]}
    if len(check["citations"]) >= 1:
        return {"level": "medium", "reasons": reasons + ["1 条可核验引用"]}
    return {"level": "low", "reasons": reasons + ["无引用/推断为主，建议人工复核"]}


def rule_based_multi(clusters: list[dict]) -> str:
    if not clusters:
        return "知识库中没有足够依据回答该问题。"
    parts = ["（规则降级：LLM 不可用，多单元摘录）"]
    for cluster in clusters[:2]:
        parts.append(f"【直接事实】{cluster.get('label') or ''}：")
        for unit in (cluster.get("units") or [])[:2]:
            parts.append(f"  - [{unit.get('unit_id')}] p.{unit.get('page')} {unit.get('text')}")
    return "\n".join(parts)


def answer_multi(question: str, k: int = DEFAULT_K, clusters: list[dict] | None = None,
                 search_fn=None, chat_fn=None, model: str = DEFAULT_MODEL,
                 timeout: float = DEFAULT_TIMEOUT,
                 prompt_version: str = DEFAULT_PROMPT_VERSION) -> dict:
    """多单元聚合回答（单问题）：检索+聚类 → 聚合 prompt → 引用校验/置信。

    prompt_version：v1（默认）/ v4（可选版本，事实行/推断行分段格式；未扩样前不切默认）。
    """
    started = time.monotonic()
    question = (question or "").strip()
    if not question:
        raise MultiError("问题不能为空")
    clusters = clusters if clusters is not None else retrieve_clusters(question, k, search_fn=search_fn)
    provided = sum(len(c.get("units") or []) for c in clusters)
    base = {"question": question, "k": max(MIN_K, min(MAX_K, int(k))),
            "prompt_version": (prompt_version or DEFAULT_PROMPT_VERSION).strip().lower(),
            "clusters": [{"label": c.get("label"), "book": c.get("book"),
                          "chapter": c.get("chapter"), "units": len(c.get("units") or []),
                          "max_score": c.get("max_score")} for c in clusters],
            "cluster_count": len(clusters), "units_provided": provided,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    if not clusters:
        base.update({"answer": "知识库中没有足够依据回答该问题。", "citations": [],
                     "confidence": {"level": "none", "reasons": ["检索无材料，按拒答处理"]},
                     "fallback": False, "refusal": True, "direct_fact_markers": 0,
                     "inference_markers": 0})
        base["elapsed_seconds"] = round(time.monotonic() - started, 2)
        return base
    prompt = build_multi_prompt(question, clusters, prompt_version=prompt_version)
    fallback = False
    error = None
    try:
        answer = (chat_fn or call_ollama)(prompt, model=model, timeout=timeout)
    except (MultiError, TimeoutError, OSError, ValueError) as exc:
        fallback = True
        error = _clip(exc, 160)
        answer = rule_based_multi(clusters)
    check = validate_multi_answer(answer, clusters)
    confidence = assess_confidence(answer, check, clusters, fallback)
    base.update({"answer": answer, "citations": check["citations"],
                 "confidence": confidence, "fallback": fallback,
                 "refusal": check["refusal"],
                 "direct_fact_markers": check["direct_fact_markers"],
                 "inference_markers": check["inference_markers"],
                 "citation_check": {"hallucinated_ids": check["hallucinated_ids"],
                                    "citation_missing": check["citation_missing"]},
                 "llm": {"model": model, "fallback": fallback, "error": error}})
    base["elapsed_seconds"] = round(time.monotonic() - started, 2)
    return base


def classify_status(result: dict) -> dict:
    """success / no_evidence / fallback 判定（供对比统计）。"""
    answer = str(result.get("answer") or "")
    confidence = result.get("confidence") or {}
    fallback = bool(result.get("fallback"))
    no_evidence = bool(result.get("refusal")) or confidence.get("level") == "none" or REFUSAL_MARK in answer
    if fallback:
        status = "fallback"
    elif no_evidence:
        status = "no_evidence"
    else:
        status = "success"
    return {"status": status, "fallback": fallback, "no_evidence": bool(no_evidence),
            "success": status == "success", "citations": len(result.get("citations") or [])}


# ---------------------------------------------------------------------------
# ④ v3 对比评估
# ---------------------------------------------------------------------------
def load_v3_baseline(path: Path | str = V3_BANK, limit: int = 12) -> dict:
    path = Path(path)
    if not path.exists():
        raise MultiError(f"v3 结果文件不存在：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MultiError(f"v3 结果读取失败：{path}（{exc}）") from exc
    av = data.get("answer_validation") or {}
    samples = [s for s in av.get("samples") or [] if isinstance(s, dict) and s.get("question")]
    if not samples:
        raise MultiError(f"v3 answer_validation.samples 为空：{path}")
    return {"path": str(path), "requested": av.get("requested"),
            "success": av.get("success"), "no_evidence": av.get("no_evidence"),
            "fallback": av.get("fallback"), "skipped": av.get("skipped", 0),
            "answerable_rate": av.get("answerable_rate"),
            "avg_citations": av.get("avg_citations"),
            "elapsed_seconds": av.get("elapsed_seconds"),
            "samples": samples[:max(1, int(limit))]}


def _status_of_baseline(sample: dict) -> str:
    return sample.get("status") or ("no_evidence" if (sample.get("confidence") or {}).get("level") == "none"
                                    else "success")


def eval_compare(n: int = 12, k: int = DEFAULT_K, model: str = DEFAULT_MODEL,
                 timeout: float = DEFAULT_TIMEOUT, deadline_seconds: float = DEFAULT_DEADLINE,
                 ask_fn=None, search_fn=None, baseline_path: Path | str = V3_BANK,
                 clock=time.monotonic) -> dict:
    """对 v3 的 N 条抽验问题跑多单元聚合，逐条与单单元（v3）对比。"""
    started = clock()
    baseline = load_v3_baseline(baseline_path)
    samples = baseline["samples"][:max(1, min(int(n), len(baseline["samples"])))]
    rows = []
    success = no_evidence = fallback = skipped = 0
    citations_total = 0
    answered = 0
    direct_total = inference_total = 0
    for sample in samples:
        remaining = deadline_seconds - (clock() - started)
        if remaining <= 1:
            skipped += 1
            rows.append({"id": sample.get("id"), "question": sample["question"],
                         "category": sample.get("category"),
                         "single_status": _status_of_baseline(sample),
                         "single_citations": len(sample.get("citations") or []),
                         "multi_status": "skipped_deadline", "multi_citations": 0,
                         "multi_clusters": 0, "reason": "整体 deadline 用尽"})
            continue
        try:
            result = answer_multi(sample["question"], k=k, search_fn=search_fn, chat_fn=ask_fn,
                                  model=model, timeout=max(5.0, min(timeout, remaining)))
        except Exception as exc:  # noqa: BLE001 - 单条失败按 fallback 记录
            result = {"answer": None, "citations": [], "confidence": {"level": "none"},
                      "fallback": True, "refusal": False, "cluster_count": 0,
                      "direct_fact_markers": 0, "inference_markers": 0,
                      "error": f"{type(exc).__name__}: {exc}", "elapsed_seconds": 0.0}
        status = classify_status(result)
        if status["status"] == "success":
            success += 1
        elif status["status"] == "fallback":
            fallback += 1
        else:
            no_evidence += 1
        if result.get("answer"):
            answered += 1
            citations_total += status["citations"]
        direct_total += int(result.get("direct_fact_markers") or 0)
        inference_total += int(result.get("inference_markers") or 0)
        rows.append({"id": sample.get("id"), "question": sample["question"],
                     "category": sample.get("category"),
                     "single_status": _status_of_baseline(sample),
                     "single_citations": len(sample.get("citations") or []),
                     "multi_status": status["status"],
                     "multi_citations": status["citations"],
                     "multi_clusters": result.get("cluster_count", 0),
                     "multi_units": result.get("units_provided", 0),
                     "multi_direct_facts": result.get("direct_fact_markers", 0),
                     "multi_inferences": result.get("inference_markers", 0),
                     "confidence": (result.get("confidence") or {}).get("level"),
                     "refusal": bool(result.get("refusal")),
                     "fallback": bool(result.get("fallback")),
                     "answer": (result.get("answer") or "")[:400],
                     "elapsed_seconds": result.get("elapsed_seconds")})
    total = len(rows)
    multi_rate = round(success / total, 4) if total else 0.0
    single_rate = baseline.get("answerable_rate")
    multi = {"requested": total, "success": success, "no_evidence": no_evidence,
             "fallback": fallback, "skipped": skipped, "answerable_rate": multi_rate,
             "avg_citations": round(citations_total / answered, 2) if answered else 0.0,
             "avg_clusters": round(sum(r.get("multi_clusters") or 0 for r in rows) / total, 2) if total else 0.0,
             "direct_fact_markers": direct_total, "inference_markers": inference_total,
             "elapsed_seconds": round(clock() - started, 2)}
    single = {"requested": baseline.get("requested"), "success": baseline.get("success"),
              "no_evidence": baseline.get("no_evidence"), "fallback": baseline.get("fallback"),
              "answerable_rate": single_rate, "avg_citations": baseline.get("avg_citations"),
              "elapsed_seconds": baseline.get("elapsed_seconds")}
    delta = {}
    if isinstance(multi_rate, (int, float)) and isinstance(single_rate, (int, float)):
        delta["answerable_rate"] = round(multi_rate - single_rate, 4)
    if isinstance(multi.get("avg_citations"), (int, float)) and isinstance(single.get("avg_citations"), (int, float)):
        delta["avg_citations"] = round(multi["avg_citations"] - single["avg_citations"], 2)
    if isinstance(multi.get("elapsed_seconds"), (int, float)) and isinstance(single.get("elapsed_seconds"), (int, float)):
        delta["elapsed_seconds"] = round(multi["elapsed_seconds"] - single["elapsed_seconds"], 2)
    if isinstance(single.get("no_evidence"), int):
        delta["no_evidence"] = multi["no_evidence"] - single["no_evidence"]
    conclusion = _conclusion(single, multi, delta, rows)
    return {
        "schema_version": 1, "kind": "kb_qa_multi_eval",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "baseline_source": {"path": baseline.get("path"), "mode": "single-unit（wave15 v3 kb_qa.ask 抽验）"},
        "config": {"n": total, "k": max(MIN_K, min(MAX_K, int(k))), "model": model,
                   "timeout_seconds": timeout, "deadline_seconds": deadline_seconds,
                   "max_clusters": MAX_CLUSTERS, "max_units_per_cluster": MAX_UNITS_PER_CLUSTER},
        "single": single, "multi": multi, "delta": delta,
        "comparison": rows, "conclusion": conclusion,
        "disclaimer": "多单元聚合回答仅供研究参考，不构成投资建议；直接事实/推断需人工复核。",
    }


def _conclusion(single: dict, multi: dict, delta: dict, rows: list[dict]) -> dict:
    rate1, rate2 = single.get("answerable_rate"), multi.get("answerable_rate")
    improved = (rate2 or 0) - (rate1 or 0)
    if improved > 0.1:
        verdict = "多单元聚合显著提升可答率，建议作为问答默认模式（MVP 验证通过）"
        met = True
    elif improved > 0:
        verdict = "多单元聚合有小幅提升，建议扩大样本验证后再切换默认模式"
        met = True
    elif improved == 0:
        verdict = "多单元聚合与单单元持平，瓶颈不在材料条数（需改 prompt/模型）"
        met = False
    else:
        verdict = "多单元聚合未提升（甚至下降），不建议直接替换单单元模式"
        met = False
    reasons = []
    if isinstance(rate1, (int, float)) and isinstance(rate2, (int, float)):
        reasons.append(f"可答率单单元 {rate1:.0%}（{single.get('success')}/{single.get('requested')}）→ "
                       f"多单元 {rate2:.0%}（{multi.get('success')}/{multi.get('requested')}），Δ{improved:+.0%}。")
    reasons.append(f"拒答数：{single.get('no_evidence')} → {multi.get('no_evidence')}；"
                   f"成功数：{single.get('success')} → {multi.get('success')}。")
    reasons.append(f"多单元平均聚类 {multi.get('avg_clusters')} 个、平均引用 {multi.get('avg_citations')} 条，"
                   f"直接事实标记 {multi.get('direct_fact_markers')}、推断标记 {multi.get('inference_markers')}。")
    improved_ids = [r["id"] for r in rows if r.get("multi_status") == "success"
                    and r.get("single_status") != "success"]
    if improved_ids:
        reasons.append(f"单单元拒答→多单元成功的样本：{', '.join(improved_ids[:6])}。")
    risks = [
        "多单元聚合 prompt 允许推断，存在「过度综合/跨单元错误拼接」风险，需人工抽查引用与事实边界。",
        "n=12 为小样本，结论需扩到 30+ 条验证；多单元 prompt 更长，单条耗时更高（见 delta.elapsed_seconds）。",
        "引用校验仅校验 unit_id 是否在候选池内，不校验「该引用是否支持该论点」；直接事实仍需人工复核。",
        "若单单元与多单元都拒答，瓶颈可能是模型能力/prompt 严格度，而非材料条数——建议并行试验放宽 prompt 或 35b 模型。",
    ]
    next_actions = [
        "将多单元聚合作为 kb_qa 的可选模式（--multi），默认保持单单元；先在评测集上 A/B。",
        "扩样到 30–50 条（v3 问题库全覆盖）重跑本 eval，观察可答率与引用质量稳定性。",
        "对「都拒答」类问题制作小样本，单独调试 prompt（允许基于 claim 的有限外推）。",
    ]
    return {"multi_unit_breaks_bottleneck": met, "verdict": verdict,
            "reasons": reasons, "risks": risks, "next_actions": next_actions}


# ---------------------------------------------------------------------------
# 输出 + CLI
# ---------------------------------------------------------------------------
def render_markdown(payload: dict) -> str:
    single = payload["single"]
    multi = payload["multi"]
    delta = payload["delta"]
    concl = payload["conclusion"]
    cfg = payload["config"]
    lines = [f"# 多单元聚合回答 MVP 对比（{payload['generated_at'][:10]}）", "",
             f"> 对 wave15 v3 的 {cfg['n']} 条抽验问题：单单元（v3 kb_qa.ask）vs 多单元聚合"
             f"（top-{cfg['k']} 检索 + 聚类 ≤{cfg['max_clusters']} 簇 × ≤{cfg['max_units_per_cluster']} 单元；"
             f"qwen3.5:9b 串行）。", "",
             "## 汇总对比", "",
             "| 指标 | 单单元（v3） | 多单元聚合 | Δ |", "|---|---|---|---|",
             f"| 可答率 | {single.get('answerable_rate') if single.get('answerable_rate') is None else format(single['answerable_rate'], '.0%')}"
             f"（{single.get('success')}/{single.get('requested')}） | "
             f"**{format(multi['answerable_rate'], '.0%')}**（{multi['success']}/{multi['requested']}） | "
             f"{delta.get('answerable_rate', 0):+.0%} |",
             f"| 成功数 | {single.get('success')} | {multi['success']} | — |",
             f"| 拒答数 | {single.get('no_evidence')} | {multi['no_evidence']} | {delta.get('no_evidence', '—')} |",
             f"| 降级数 | {single.get('fallback')} | {multi['fallback']} | — |",
             f"| 平均引用（已答） | {single.get('avg_citations')} | {multi.get('avg_citations')} | {delta.get('avg_citations', '—')} |",
             f"| 平均聚类 | — | {multi.get('avg_clusters')} | — |",
             f"| 耗时(s) | {single.get('elapsed_seconds')} | {multi.get('elapsed_seconds')} | {delta.get('elapsed_seconds', '—')} |",
             "", "## 结论：多单元是否突破瓶颈", "",
             f"**{'是' if concl['multi_unit_breaks_bottleneck'] else '否/未定'}** —— {concl['verdict']}", ""]
    lines += [f"- {r}" for r in concl["reasons"]]
    lines += ["", "风险："] + [f"- {r}" for r in concl["risks"]]
    lines += ["", "下一步："] + [f"- {r}" for r in concl["next_actions"]]
    lines += ["", "## 逐条对比", "",
              "| # | 类别 | 问题 | 单单元状态/引用 | 多单元状态/引用 | 聚类 | 直接事实/推断 | 耗时(s) |",
              "|---|---|---|---|---|---|---|---|"]
    for i, row in enumerate(payload["comparison"], 1):
        lines.append(f"| {i} | {row.get('category')} | {str(row.get('question'))[:42]} | "
                     f"{row.get('single_status')}/{row.get('single_citations')} | "
                     f"{row.get('multi_status')}/{row.get('multi_citations')} | {row.get('multi_clusters')} | "
                     f"{row.get('multi_direct_facts')}/{row.get('multi_inferences')} | {row.get('elapsed_seconds')} |")
    succeeded = [r for r in payload["comparison"] if r.get("multi_status") == "success"]
    if succeeded:
        lines += ["", "## 多单元成功样例", ""]
        for row in succeeded[:3]:
            lines.append(f"- [{row.get('category')}] {row.get('question')} → {str(row.get('answer'))[:200]}")
    lines += ["", "## 说明", "", f"- {payload['disclaimer']}",
              "- 单单元基线取自 wave15 v3 `answer_validation.samples`（同一批 12 条问题），多单元为本模块同题重跑；",
              "- 多单元 prompt 允许基于多条单元综合推断，但要求区分【直接事实】/【推断】并引用 [unit_id]；"
              "引用校验仅保证 unit_id 在候选池内，不保证语义支撑。"]
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
    ap = argparse.ArgumentParser(description="多单元聚合回答 MVP（检索 top-k 聚类 + 聚合 prompt + v3 对比）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ask_p = sub.add_parser("ask", help="单问题多单元聚合回答")
    ask_p.add_argument("--q", required=True, help="问题")
    ask_p.add_argument("--k", type=int, default=DEFAULT_K, help=f"检索单元数 {MIN_K}..{MAX_K}（默认 {DEFAULT_K}）")
    ask_p.add_argument("--multi", action="store_true", help="使用多单元聚合模式（默认单单元 kb_qa.ask）")
    ask_p.add_argument("--json", action="store_true", help="输出 JSON")
    ask_p.add_argument("--model", default=DEFAULT_MODEL)
    ask_p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    ask_p.add_argument("--prompt-version", default=DEFAULT_PROMPT_VERSION, choices=PROMPT_VERSIONS,
                       help="聚合 prompt 版本（默认 v1；v4 为可选版本）")
    ev = sub.add_parser("eval", help="对 v3 抽验问题做单单元 vs 多单元对比")
    ev.add_argument("--n", type=int, default=12, help="对比问题数（默认 12）")
    ev.add_argument("--k", type=int, default=DEFAULT_K)
    ev.add_argument("--model", default=DEFAULT_MODEL)
    ev.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    ev.add_argument("--deadline", type=float, default=DEFAULT_DEADLINE)
    ev.add_argument("--baseline", default=str(V3_BANK), help="v3 基线 JSON 路径")
    ev.add_argument("--out-json", default=str(OUT_JSON))
    ev.add_argument("--out-md", default=str(OUT_MD))
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "ask":
        try:
            if args.multi:
                result = answer_multi(args.q, k=args.k, model=args.model, timeout=args.timeout,
                                      prompt_version=args.prompt_version)
            else:
                result = kb_qa().ask(args.q, topk=max(MIN_K, min(MAX_K, args.k)),
                                     model=args.model, timeout=args.timeout)
        except (MultiError, Exception) as exc:  # noqa: BLE001
            print(f"[kb_qa_multi] FAILED: {exc}", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=1))
        else:
            print(f"问题：{result.get('question')}")
            print(f"模式：{'多单元聚合' if args.multi else '单单元'}")
            print(f"回答：{result.get('answer')}")
            print(f"置信：{(result.get('confidence') or {}).get('level')} · "
                  f"引用 {len(result.get('citations') or [])} 条 · 聚类 {result.get('cluster_count', '—')}")
        return 0
    try:
        payload = eval_compare(n=args.n, k=args.k, model=args.model, timeout=args.timeout,
                               deadline_seconds=args.deadline, baseline_path=args.baseline)
        paths = write_outputs(payload, args.out_json, args.out_md)
    except MultiError as exc:
        print(f"[kb_qa_multi] FAILED: {exc}", file=sys.stderr)
        return 1
    s, m, d = payload["single"], payload["multi"], payload["delta"]
    print(f"[kb_qa_multi] n={payload['config']['n']} k={payload['config']['k']}")
    print(f"[kb_qa_multi] single: rate={s.get('answerable_rate')} success={s.get('success')} "
          f"no_evidence={s.get('no_evidence')} avg_cit={s.get('avg_citations')} elapsed={s.get('elapsed_seconds')}s")
    print(f"[kb_qa_multi] multi:  rate={m.get('answerable_rate')} success={m.get('success')} "
          f"no_evidence={m.get('no_evidence')} avg_cit={m.get('avg_citations')} "
          f"clusters={m.get('avg_clusters')} elapsed={m.get('elapsed_seconds')}s")
    print(f"[kb_qa_multi] delta={json.dumps(d, ensure_ascii=False)} "
          f"breaks_bottleneck={payload['conclusion']['multi_unit_breaks_bottleneck']}")
    print(f"[kb_qa_multi] json={paths[0]}")
    print(f"[kb_qa_multi] md={paths[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
