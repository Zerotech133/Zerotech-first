# -*- coding: utf-8 -*-
"""研报知识库 RAG 问答（检索融合 + 本地 LLM 生成 + 引用/置信说明）。

能力：
  ① 检索融合：复用 kb_read_api.py 的 `search`（知识单元，498 条含研报+书籍）与
     `corpus-search`（RAG chunks，C:/项目/知识库/50_RAG/rag_index_corpus.json，4926 条），
     两路 top-k 归一化融合打分、按 unit_id 与原文去重、排序取 topk；
  ② 生成：本地 Ollama qwen3.5:9b（/api/chat，stream=false、think=false、timeout=300s），
     prompt 强制「只依据给定材料回答；每个结论引用 [unit_id] 与页码；无依据必须说不知道」；
  ③ 输出：answer + citations（unit_id/书名/章节/页码/原文片段）+ confidence 说明；
     并对 LLM 引用做校验（未知 unit_id 记 hallucinated、未引用材料记 citation_missing）；
     LLM 失败/超时 → 规则式摘录降级（fallback=true，不编造）。
  ④ CLI：`ask "问题" --topk 8 --json`；`ask --retrieval-only` 仅检索（离线冒烟/测试）；
     `samples` 生成静态示范样本（含无依据/失败降级示例）。

边界：只回答知识库材料覆盖的问题；引用页码来自知识单元 page_start/page_end；
     本工具是**研究辅助**，不构成投资建议；HTTP 接口待门户 8091 接线（见文档）。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT_DIR = REPO / "research" / "reports"
PORTAL_STATIC = REPO / "portal" / "static"
OLLAMA_ENDPOINT = "http://127.0.0.1:11434/api/chat"
DEFAULT_MODEL = "qwen3.5:9b"
DEFAULT_TOPK = 8
DEFAULT_TIMEOUT = 300
DEFAULT_MODE = "multi"          # 默认多单元聚合（A/B：可答率 33%→80%，回退 0）
VALID_MODES = ("multi", "single")
PROMPT_VERSIONS = ("v1", "v4")          # 聚合 prompt 版本（默认 v4 灰度；KB_QA_PROMPT_VERSION=v1 回滚）
DEFAULT_PROMPT_VERSION = os.environ.get("KB_QA_PROMPT_VERSION", "v4").strip().lower()
MARKER_MODES = ("strict_line", "segment", "legacy")   # guard marker 模式（默认 segment 灰度；KB_QA_MARKER_MODE=strict_line 回滚）
DEFAULT_MARKER_MODE = os.environ.get("KB_QA_MARKER_MODE", "segment").strip().lower()
UNIT_WEIGHT = 1.0
CHUNK_WEIGHT = 0.35      # 语料 chunk 噪声较大（含视频评论等），只作补充证据
MIN_CHUNK_OVERLAP = 2    # chunk 至少命中 2 个问题词才入选（降噪）
MAX_TEXT_CHARS = 300
REFUSAL_ANSWER = "知识库中没有足够依据回答该问题。"

DEFAULT_QUESTIONS = [
    {"id": "q1", "question": "M9 级覆铜板对 AI 服务器 PCB 的价值量有什么影响？",
     "category": "answerable"},
    {"id": "q2", "question": "高端球形硅微粉的竞争格局与国产替代空间如何？",
     "category": "answerable"},
    {"id": "q3", "question": "固态电池的量产时间表与锂电产业链排产情况？",
     "category": "answerable"},
    {"id": "q4", "question": "2027 年火星殖民计划对 A 股半导体板块的影响？",
     "category": "no_evidence_simulated"},
    {"id": "q5", "question": "研报里对白酒行业 2026 年双节动销的判断是什么？",
     "category": "llm_failure_simulated"},
]


class KbQaError(RuntimeError):
    """问答流程异常（检索接口不可用/输入非法等）。"""


def _load_kb_api():
    path = Path(__file__).with_name("kb_read_api.py")
    spec = importlib.util.spec_from_file_location("kb_read_api_for_qa", path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise KbQaError(f"无法加载 kb_read_api.py：{path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_kb = None


def kb_api():
    global _kb
    if _kb is None:
        _kb = _load_kb_api()
    return _kb


# ---------------------------------------------------------------------------
# ① 检索融合
# ---------------------------------------------------------------------------
def _clip(text, limit: int = MAX_TEXT_CHARS) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _norm_key(text: str) -> str:
    return re.sub(r"\s+", "", str(text or ""))[:60]


def normalize_unit_hit(item: dict, rank: int) -> dict:
    return {"kind": "unit", "unit_id": str(item.get("id") or ""),
            "book": item.get("book") or "", "chapter": item.get("chapter") or "",
            "page_start": item.get("page_start"), "page_end": item.get("page_end"),
            "concept": item.get("concept") or "",
            "claim": item.get("claim") or "", "evidence": item.get("evidence") or "",
            "text": _clip((item.get("claim") or "") + " " + (item.get("evidence") or "")),
            "score": float(item.get("score") or 0.0), "origin": "units", "rank": rank}


def normalize_chunk_hit(item: dict, rank: int) -> dict:
    return {"kind": "chunk", "unit_id": str(item.get("id") or ""),
            "book": item.get("video_id") or item.get("source") or "语料",
            "chapter": "", "page_start": item.get("chunk_index"), "page_end": None,
            "concept": "", "claim": "", "evidence": "",
            "text": _clip(item.get("text") or item.get("snippet") or ""),
            "score": float(item.get("score") or 0.0), "origin": "corpus", "rank": rank}


def _question_tokens(question: str) -> list[str]:
    """问题切词：中文 2-gram + 英文/数字词（与 kb_read_api.tokenize 口径一致，失败则本地兜底）。"""
    q = (question or "").lower()
    tokens = re.findall(r"[\u4e00-\u9fff]{2}", q) + re.findall(r"[a-z0-9]{2,}", q)
    return list(dict.fromkeys(tokens))


def _token_overlap_count(question: str, text: str) -> int:
    q = (question or "").lower()
    t = (text or "").lower()
    if not q or not t:
        return 0
    return sum(1 for tok in _question_tokens(q) if tok in t)


def fuse_hits(unit_items: list[dict], chunk_items: list[dict], topk: int = DEFAULT_TOPK,
              unit_weight: float = UNIT_WEIGHT, chunk_weight: float = CHUNK_WEIGHT,
              question: str = "") -> list[dict]:
    """两路检索归一化融合：去重（unit_id / 原文前缀）后按融合分排序取 topk。

    单位命中权重高于语料 chunk；chunk 增加「至少命中 2 个问题词」降噪门槛
    （语料含视频评论等噪声，避免无关 chunk 挤占 top-k）。
    """
    hits: list[dict] = []
    for i, item in enumerate(unit_items or []):
        if isinstance(item, dict) and item.get("claim"):
            hits.append(normalize_unit_hit(item, i))
    for i, item in enumerate(chunk_items or []):
        if not isinstance(item, dict) or not (item.get("text") or item.get("snippet")):
            continue
        text = item.get("text") or item.get("snippet") or ""
        if question and _token_overlap_count(question, text) < MIN_CHUNK_OVERLAP:
            continue
        hits.append(normalize_chunk_hit(item, i))
    if not hits:
        return []
    max_unit = max([h["score"] for h in hits if h["origin"] == "units"] or [0.0]) or 1.0
    max_chunk = max([h["score"] for h in hits if h["origin"] == "corpus"] or [0.0]) or 1.0
    for h in hits:
        base = h["score"] / (max_unit if h["origin"] == "units" else max_chunk)
        weight = unit_weight if h["origin"] == "units" else chunk_weight
        h["fused_score"] = round(base * weight + 1.0 / (h["rank"] + 10), 4)
        h["key"] = _norm_key(h["text"])
    # 去重：同 unit_id / 同原文前缀，保留融合分更高者
    deduped: dict[str, dict] = {}
    for h in sorted(hits, key=lambda x: -x["fused_score"]):
        key = f"id:{h['unit_id']}" if h["unit_id"] else f"text:{h['key']}"
        if h["key"] and len(h["key"]) >= 8:
            key = f"{key}|{h['key']}"
        if key not in deduped:
            deduped[key] = h
    ranked = sorted(deduped.values(), key=lambda x: -x["fused_score"])[:max(1, int(topk))]
    for i, h in enumerate(ranked, 1):
        h["rank"] = i
    return ranked


def _run_kb_query(cmd: str, question: str, top: int) -> list[dict]:
    """调用 kb_read_api 的 search / corpus-search（统一信封），失败返回空列表。"""
    api = kb_api()
    try:
        args = argparse.Namespace(q=question, top=top)
        result = api.cmd_search(args) if cmd == "units" else api.cmd_corpus_search(args)
    except Exception:  # noqa: BLE001 - 检索失败按无命中处理（问答降级拒答）
        return []
    if not isinstance(result, dict) or not result.get("ok"):
        return []
    return list((result.get("data") or {}).get("items") or [])


def search(question: str, topk: int = DEFAULT_TOPK,
           unit_search_fn=None, corpus_search_fn=None) -> list[dict]:
    """两路检索 + 融合。unit_search_fn/corpus_search_fn 可注入（测试 mock）。"""
    question = (question or "").strip()
    if not question:
        return []
    per_side = max(3, int(topk))
    units = (unit_search_fn or (lambda q, n: _run_kb_query("units", q, n)))(question, per_side)
    chunks = (corpus_search_fn or (lambda q, n: _run_kb_query("corpus", q, n)))(question, per_side)
    return fuse_hits(units, chunks, topk=topk, question=question)


# ---------------------------------------------------------------------------
# ② 生成
# ---------------------------------------------------------------------------
def format_materials(hits: list[dict]) -> str:
    if not hits:
        return "（无检索材料）"
    lines = []
    for h in hits:
        page = h.get("page_start")
        page_s = f"p.{page}" if page is not None else "页码缺失"
        if h.get("page_end") and h.get("page_end") != page:
            page_s += f"-{h['page_end']}"
        origin = "知识单元" if h["origin"] == "units" else "语料"
        lines.append(f"{h['rank']}. [{h['unit_id']}]（{origin}）《{h.get('book') or '未知'}》"
                     f"{('·' + h['chapter']) if h.get('chapter') else ''} {page_s}\n"
                     f"   原文：{_clip(h.get('text'), MAX_TEXT_CHARS)}")
    return "\n".join(lines)


def build_prompt(question: str, hits: list[dict]) -> str:
    """构造强制引用/拒答的问答 prompt（确定性，便于测试）。"""
    return (
        "你是研报知识库问答助手。必须严格遵守以下规则：\n"
        "1) 只依据【检索材料】回答，不得使用材料之外的知识，不得编造事实；\n"
        "2) 每个结论后必须用 [unit_id] 标注出处并写明页码（如 [3ade35288309] p.2-12）；\n"
        "3) 材料不足以回答时，必须直接回答「知识库中没有足够依据回答该问题。」，不要猜测；\n"
        "4) 回答用中文，先给结论、再列依据，不超过 300 字。\n\n"
        f"【问题】{question}\n\n【检索材料】\n{format_materials(hits)}\n\n"
        "【回答】"
    )


def call_ollama(prompt: str, model: str = DEFAULT_MODEL, timeout: float = DEFAULT_TIMEOUT,
                num_predict: int = 800) -> str:
    body = {"model": model, "stream": False, "think": False,
            "messages": [{"role": "user", "content": prompt}],
            "options": {"temperature": 0.1, "num_predict": int(num_predict)}}
    req = urllib.request.Request(OLLAMA_ENDPOINT, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise KbQaError(f"Ollama 调用失败：{exc}") from exc
    content = ((payload.get("message") or {}).get("content") or "").strip()
    if not content:
        raise KbQaError("Ollama 返回空内容")
    return content


# ---------------------------------------------------------------------------
# ③ 引用校验 + 置信说明
# ---------------------------------------------------------------------------
_CITE_RE = re.compile(r"[\[【]([0-9a-fA-F]{8,12})[\]】]")
_REFUSAL_RE = re.compile(r"(没有足够依据|不知道|无法回答|未找到|无法确定|没有相关|不足以回答)")


def extract_citation_ids(answer: str) -> list[str]:
    seen, out = set(), []
    for cid in _CITE_RE.findall(answer or ""):
        cid = cid.lower()
        if cid not in seen:
            seen.add(cid)
            out.append(cid)
    return out


def build_citations(answer: str, hits: list[dict]) -> dict:
    """按 answer 中的 [unit_id] 生成引用列表；检测未知引用与未引用材料。"""
    available = {h["unit_id"]: h for h in hits if h.get("unit_id")}
    cited = extract_citation_ids(answer)
    citations = []
    for cid in cited:
        h = available.get(cid)
        if not h:
            continue
        citations.append({"unit_id": cid, "book": h.get("book") or "",
                          "chapter": h.get("chapter") or "",
                          "page": h.get("page_start"), "page_end": h.get("page_end"),
                          "origin": h.get("origin"),
                          "text": _clip(h.get("text"), MAX_TEXT_CHARS)})
    return {"citations": citations,
            "hallucinated_citations": [c for c in cited if c not in available],
            "available_ids": list(available),
            "citation_missing": bool(hits) and not cited}


def assess_confidence(answer: str, cite_info: dict, hits: list[dict], fallback: bool) -> dict:
    reasons: list[str] = []
    n = len(cite_info["citations"])
    if not hits:
        return {"level": "none", "reasons": ["检索无命中材料，按拒答处理"]}
    if _REFUSAL_RE.search(answer or ""):
        return {"level": "none", "reasons": ["模型判断材料不足以回答（拒答）"]}
    if cite_info["hallucinated_citations"]:
        reasons.append(f"存在未知引用 {len(cite_info['hallucinated_citations'])} 个（已剔除）")
    if cite_info["citation_missing"]:
        reasons.append("回答未标注任何 [unit_id] 引用")
    if fallback:
        return {"level": "low", "reasons": reasons + ["LLM 失败，规则式摘录降级（未做语义论证）"]}
    if n >= 2 and not cite_info["hallucinated_citations"]:
        return {"level": "high", "reasons": reasons + [f"{n} 条可核验引用（材料内命中）"]}
    if n == 1:
        return {"level": "medium", "reasons": reasons + ["仅 1 条可核验引用"]}
    return {"level": "low", "reasons": reasons + ["无可核验引用，建议人工复核"]}


# ---------------------------------------------------------------------------
# 问答主流程
# ---------------------------------------------------------------------------
def rule_based_answer(hits: list[dict]) -> str:
    top = hits[:2]
    if not top:
        return REFUSAL_ANSWER
    parts = ["（规则降级：LLM 不可用，仅摘录知识库原文）"]
    for h in top:
        page = h.get("page_start")
        parts.append(f"《{h.get('book') or '未知'}》{('·' + h['chapter']) if h.get('chapter') else ''}"
                     f"（p.{page if page is not None else '?'}）：{_clip(h.get('text'), 160)} [{h['unit_id']}]")
    return "\n".join(parts)


def _load_kb_qa_multi():
    """懒加载 kb_qa_multi（避免循环 import；multi 模块内部也按需加载本模块）。"""
    path = Path(__file__).with_name("kb_qa_multi.py")
    if not path.exists():
        raise KbQaError(f"kb_qa_multi.py 不存在：{path}")
    spec = importlib.util.spec_from_file_location("kb_qa_multi_for_kb_qa", path)
    if spec is None or spec.loader is None:
        raise KbQaError(f"无法加载 kb_qa_multi.py：{path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _ask_single(question: str, topk: int, model: str, timeout: float, search_fn, chat_fn,
                retrieval_only: bool = False,
                prompt_version: str = DEFAULT_PROMPT_VERSION,
                marker_mode: str = DEFAULT_MARKER_MODE) -> dict:
    """单单元标准问答（旧行为，mode="single" / retrieval-only 走这里）。"""
    started = time.monotonic()
    hits = (search_fn or search)(question, topk)
    retrieved = [{"rank": h["rank"], "unit_id": h["unit_id"], "kind": h["kind"],
                  "book": h.get("book"), "chapter": h.get("chapter"),
                  "page": h.get("page_start"), "page_end": h.get("page_end"),
                  "fused_score": h.get("fused_score"), "preview": _clip(h.get("text"), 120)}
                 for h in hits]
    base = {"schema_version": 1, "question": question, "topk": topk,
            "mode": "retrieval" if retrieval_only else "single",
            "prompt_version": (prompt_version or DEFAULT_PROMPT_VERSION).strip().lower(),
            "guard_marker_mode": (marker_mode or DEFAULT_MARKER_MODE).strip().lower(),
            "k": topk, "clusters": [],
            "retrieved": retrieved, "llm": {"model": model, "endpoint": OLLAMA_ENDPOINT,
                                            "think": False, "timeout_seconds": timeout,
                                            "fallback": False, "error": None},
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    if retrieval_only:
        base.update({"answer": None, "citations": [], "confidence": {"level": "n/a",
                                                                      "reasons": ["仅检索模式"]},
                     "fallback": False})
        base["elapsed_seconds"] = round(time.monotonic() - started, 2)
        return base
    if not hits:
        base.update({"answer": REFUSAL_ANSWER, "citations": [],
                     "confidence": {"level": "none", "reasons": ["检索无命中材料，按拒答处理"]},
                     "fallback": False,
                     "citation_check": {"hallucinated_citations": [], "citation_missing": False}})
        base["elapsed_seconds"] = round(time.monotonic() - started, 2)
        return base
    prompt = build_prompt(question, hits)
    fallback = False
    error = None
    try:
        answer = (chat_fn or call_ollama)(prompt, model=model, timeout=timeout)
    except (KbQaError, TimeoutError, OSError, ValueError) as exc:
        fallback = True
        error = _clip(exc, 160)
        answer = rule_based_answer(hits)
    cite_info = build_citations(answer, hits)
    confidence = assess_confidence(answer, cite_info, hits, fallback)
    base.update({"answer": answer, "citations": cite_info["citations"],
                 "confidence": confidence, "fallback": fallback,
                 "citation_check": {"hallucinated_citations": cite_info["hallucinated_citations"],
                                    "citation_missing": cite_info["citation_missing"]}})
    base["llm"].update({"fallback": fallback, "error": error})
    base["elapsed_seconds"] = round(time.monotonic() - started, 2)
    return base


def _ask_multi(question: str, topk: int, model: str, timeout: float, search_fn, chat_fn,
               prompt_version: str = DEFAULT_PROMPT_VERSION) -> dict:
    """多单元聚合问答（默认模式）：检索 top-k → 聚类 → 聚合 prompt → 引用校验。"""
    started = time.monotonic()
    multi = _load_kb_qa_multi()
    k = max(5, min(8, int(topk or DEFAULT_TOPK)))
    clusters = multi.retrieve_clusters(question, k=k, search_fn=search_fn)
    multi_kwargs = {"k": k, "clusters": clusters, "search_fn": search_fn,
                    "chat_fn": chat_fn, "model": model, "timeout": timeout}
    if (prompt_version or DEFAULT_PROMPT_VERSION).strip().lower() != DEFAULT_PROMPT_VERSION:
        multi_kwargs["prompt_version"] = prompt_version
    result = multi.answer_multi(question, **multi_kwargs)
    retrieved = []
    for cluster in clusters:
        for unit in cluster.get("units") or []:
            retrieved.append({"rank": len(retrieved) + 1, "unit_id": unit.get("unit_id"),
                              "kind": "units", "book": unit.get("book"),
                              "chapter": unit.get("chapter"), "page": unit.get("page"),
                              "page_end": None, "fused_score": unit.get("fused_score"),
                              "preview": _clip(unit.get("text"), 120)})
    check = result.get("citation_check") or {}
    return {"schema_version": 1, "question": question, "topk": topk, "k": k,
            "mode": "multi", "prompt_version": (prompt_version or DEFAULT_PROMPT_VERSION).strip().lower(),
            "clusters": [{"label": c.get("label"), "book": c.get("book"),
                          "chapter": c.get("chapter"), "units": len(c.get("units") or []),
                          "max_score": c.get("max_score")} for c in clusters],
            "retrieved": retrieved,
            "answer": result.get("answer"),
            "citations": result.get("citations") or [],
            "confidence": result.get("confidence") or {},
            "fallback": bool(result.get("fallback")),
            "citation_check": {"hallucinated_citations": check.get("hallucinated_ids") or [],
                               "citation_missing": check.get("citation_missing", False)},
            "llm": result.get("llm") or {"model": model, "endpoint": OLLAMA_ENDPOINT,
                                         "think": False, "timeout_seconds": timeout,
                                         "fallback": bool(result.get("fallback")), "error": None},
            "direct_fact_markers": result.get("direct_fact_markers"),
            "inference_markers": result.get("inference_markers"),
            "generated_at": result.get("generated_at") or datetime.now().astimezone().isoformat(timespec="seconds"),
            "elapsed_seconds": result.get("elapsed_seconds", round(time.monotonic() - started, 2))}


GUARD_ACTIONS = ("mark", "fallback", "refuse")
_guard_module = None
_guard_kb_ids = None


def _load_kb_qa_guard():
    """懒加载 kb_qa_guard（multi 出口质量守卫；避免循环 import）。"""
    global _guard_module
    if _guard_module is not None:
        return _guard_module
    path = Path(__file__).with_name("kb_qa_guard.py")
    if not path.exists():
        raise KbQaError(f"kb_qa_guard.py 不存在：{path}")
    spec = importlib.util.spec_from_file_location("kb_qa_guard_for_kb_qa", path)
    if spec is None or spec.loader is None:
        raise KbQaError(f"无法加载 kb_qa_guard.py：{path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    _guard_module = module
    return module


def _apply_guard_payload(result: dict, on_guard_fail: str = "fallback",
                         marker_mode: str = DEFAULT_MARKER_MODE) -> dict:
    """multi 出口质量守卫（引用硬门/推断比例/集中度）；guard 不可用时保持原结果并标 guard_error。"""
    global _guard_kb_ids
    try:
        guard = _load_kb_qa_guard()
        if _guard_kb_ids is None:
            _guard_kb_ids = guard.load_kb_ids()
        unit_ids, book_ids = _guard_kb_ids
        guard_kwargs = {"on_fail": on_guard_fail}
        if marker_mode and marker_mode != DEFAULT_MARKER_MODE:
            guard_kwargs["marker_mode"] = marker_mode
        guarded = guard.apply_guard(result, unit_ids, book_ids, **guard_kwargs)
        guarded.setdefault("guard_marker_mode", marker_mode or DEFAULT_MARKER_MODE)
        guarded["guard_enabled"] = True
        return guarded
    except Exception as exc:  # noqa: BLE001 - 守卫失败不阻断问答，保持原答案并标注
        out = dict(result)
        out["guard_enabled"] = False
        out["guard_passed"] = None
        out["guard_action"] = "guard_unavailable"
        out["guard_error"] = _clip(f"{type(exc).__name__}: {exc}", 160)
        return out


def ask(question: str, topk: int = DEFAULT_TOPK, model: str = DEFAULT_MODEL,
        timeout: float = DEFAULT_TIMEOUT, search_fn=None, chat_fn=None,
        retrieval_only: bool = False, mode: str = DEFAULT_MODE,
        guard: bool = True, on_guard_fail: str = "fallback",
        prompt_version: str = DEFAULT_PROMPT_VERSION,
        marker_mode: str = DEFAULT_MARKER_MODE) -> dict:
    """问答主流程。

    mode="multi"（默认）：检索 top-k（5–8）单元 → 聚类 → 多单元聚合 prompt（允许综合推断，
    区分直接事实/推断，引用 [unit_id]）；multi 异常时**自动回退 single**（payload 标 multi_fallback）。
    返回前默认经质量守卫（kb_qa_guard：引用可解析硬门/推断比例/被引单元集中度），
    on_guard_fail="fallback"（默认，另有 mark/refuse），guard=False 可绕过（灰度对比）。
    mode="single"：旧单单元行为（向后兼容）；retrieval_only=True：仅检索（与 mode 无关）。
    """
    started = time.monotonic()
    question = (question or "").strip()
    if not question:
        raise KbQaError("问题不能为空")
    mode = (mode or DEFAULT_MODE).strip().lower()
    if mode not in VALID_MODES:
        raise KbQaError(f"mode 只能是 {'/'.join(VALID_MODES)}（收到 {mode!r}）")
    on_guard_fail = (on_guard_fail or "fallback").strip().lower()
    if on_guard_fail not in GUARD_ACTIONS:
        raise KbQaError(f"on_guard_fail 只能是 {'/'.join(GUARD_ACTIONS)}（收到 {on_guard_fail!r}）")
    prompt_version = (prompt_version or DEFAULT_PROMPT_VERSION).strip().lower()
    if prompt_version not in PROMPT_VERSIONS:
        raise KbQaError(f"prompt_version 只能是 {'/'.join(PROMPT_VERSIONS)}（收到 {prompt_version!r}）")
    marker_mode = (marker_mode or DEFAULT_MARKER_MODE).strip().lower()
    if marker_mode not in MARKER_MODES:
        raise KbQaError(f"marker_mode 只能是 {'/'.join(MARKER_MODES)}（收到 {marker_mode!r}）")
    if retrieval_only or mode == "single":
        return _ask_single(question, topk, model, timeout, search_fn, chat_fn, retrieval_only,
                           prompt_version=prompt_version, marker_mode=marker_mode)
    try:
        result = _ask_multi(question, topk, model, timeout, search_fn, chat_fn,
                            prompt_version=prompt_version)
    except Exception as exc:  # noqa: BLE001 - multi 不可用/失败 → 回退 single（灰度安全）
        fallback = _ask_single(question, topk, model, timeout, search_fn, chat_fn, False,
                               prompt_version=prompt_version, marker_mode=marker_mode)
        fallback["mode"] = "single"
        fallback["multi_fallback"] = True
        fallback["multi_fallback_error"] = _clip(f"{type(exc).__name__}: {exc}", 160)
        fallback["guard_enabled"] = False
        fallback["guard_passed"] = None
        fallback["guard_action"] = "multi_fallback_single"
        fallback["elapsed_seconds"] = round(time.monotonic() - started, 2)
        return fallback
    if guard:
        result = _apply_guard_payload(result, on_guard_fail, marker_mode=marker_mode)
    else:
        result = dict(result)
        result.update({"guard_enabled": False, "guard_passed": None,
                       "guard_action": "disabled", "guard_issues": [],
                       "citation_valid": [], "citation_invalid": [],
                       "citation_resolvable_rate": None, "inference_ratio": None,
                       "inference_heavy": None, "distinct_cited_units": None})
    result["elapsed_seconds"] = round(time.monotonic() - started, 2)
    return result



# ---------------------------------------------------------------------------
# ⑤ 静态样本
# ---------------------------------------------------------------------------
def build_samples(questions: list[dict] | None = None, model: str = DEFAULT_MODEL,
                  timeout: float = DEFAULT_TIMEOUT, topk: int = DEFAULT_TOPK,
                  limit: int = 5, ask_fn=None) -> dict:
    """跑示范问题生成静态样本；无依据/失败示例用「模拟检索空/模拟 LLM 异常」明确标注。"""
    questions = (questions or DEFAULT_QUESTIONS)[:max(1, int(limit))]
    samples = []
    for idx, item in enumerate(questions):
        category = item.get("category") or "answerable"
        question = item.get("question") or ""
        started = time.monotonic()
        if category == "no_evidence_simulated":
            result = (ask_fn or ask)(question, topk=topk, model=model, timeout=timeout,
                                     search_fn=_empty_search_fn)
            note = "无依据示例：模拟检索无命中，验证拒答路径"
        elif category == "llm_failure_simulated":
            result = (ask_fn or ask)(question, topk=topk, model=model, timeout=timeout,
                                     chat_fn=_failing_chat_fn)
            note = "失败降级示例：模拟 LLM 调用异常，验证规则摘录 + fallback 标记"
        else:
            result = (ask_fn or ask)(question, topk=topk, model=model, timeout=timeout)
            note = None
        samples.append({"id": item.get("id") or f"q{idx + 1}", "question": question,
                        "category": category, "note": note,
                        "answer": result.get("answer"), "citations": result.get("citations") or [],
                        "confidence": result.get("confidence") or {},
                        "fallback": bool(result.get("fallback")),
                        "retrieved_count": len(result.get("retrieved") or []),
                        "citation_check": result.get("citation_check") or {},
                        "elapsed_seconds": round(time.monotonic() - started, 2)})
    return {"schema_version": 1, "kind": "kb_qa_samples",
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "model": model, "endpoint": OLLAMA_ENDPOINT, "topk": topk,
            "disclaimer": "仅供研究，不构成投资建议；回答依据知识库检索材料，引用页码以知识单元 page_start 为准。",
            "kb": {"units": len(kb_api().load_units()) if _kb_available() else None,
                   "corpus_chunks": len(kb_api().load_corpus()) if _kb_available() else None},
            "samples": samples}


def _kb_available() -> bool:
    try:
        return kb_api() is not None
    except KbQaError:
        return False


def _empty_search_fn(question: str, topk: int) -> list[dict]:  # pragma: no cover - 样本演示
    return []


def _failing_chat_fn(prompt: str, model: str | None = None, timeout: float | None = None) -> str:
    raise KbQaError("模拟 LLM 调用失败（样本演示用）")


def write_samples(payload: dict, out_path: Path | str, portal_path: Path | str | None = None
                  ) -> tuple[Path, Path | None]:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    written_portal = None
    if portal_path:
        portal_path = Path(portal_path)
        portal_path.parent.mkdir(parents=True, exist_ok=True)
        portal_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
                               encoding="utf-8")
        written_portal = portal_path
    return out_path, written_portal


# ---------------------------------------------------------------------------
# ④ CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="研报知识库 RAG 问答（检索融合 + 本地 LLM + 引用校验）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ask_p = sub.add_parser("ask", help="对知识库提问（默认多单元聚合 multi）")
    ask_p.add_argument("question", help="问题文本")
    ask_p.add_argument("--mode", choices=list(VALID_MODES), default=DEFAULT_MODE,
                       help="问答模式：multi（默认，多单元聚合）| single（旧单单元）")
    ask_p.add_argument("--k", type=int, default=DEFAULT_TOPK, help=f"检索单元数（默认 {DEFAULT_TOPK}，multi 钳制 5–8）")
    ask_p.add_argument("--topk", type=int, default=None, help="兼容旧参数，等价于 --k")
    ask_p.add_argument("--single", action="store_true", help="便捷开关：等价于 --mode single")
    ask_p.add_argument("--json", action="store_true", help="输出完整 JSON（含 citations/confidence）")
    ask_p.add_argument("--model", default=DEFAULT_MODEL, help="Ollama 模型")
    ask_p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="LLM 超时秒数")
    ask_p.add_argument("--retrieval-only", action="store_true", help="仅检索不生成（离线冒烟）")
    ask_p.add_argument("--no-guard", action="store_true", help="multi 输出不经质量守卫（灰度对比）")
    ask_p.add_argument("--on-guard-fail", choices=list(GUARD_ACTIONS), default="fallback",
                       help="质量守卫不通过时策略：mark（仅标记）/ fallback（默认，回退可核验摘录）/ refuse（拒答）")
    smp = sub.add_parser("samples", help="生成静态示范样本 JSON")
    smp.add_argument("--limit", type=int, default=5, help="样本数（默认 5）")
    smp.add_argument("--topk", type=int, default=DEFAULT_TOPK)
    smp.add_argument("--model", default=DEFAULT_MODEL)
    smp.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    smp.add_argument("--out", default=str(OUT_DIR / "kb_qa_samples_20261101.json"))
    smp.add_argument("--portal-out", default=str(PORTAL_STATIC / "kb_qa_samples.json"))
    return ap


def _print_human(result: dict) -> None:
    print(f"问题：{result['question']}")
    mode = result.get("mode") or DEFAULT_MODE
    extra = ""
    if mode == "multi":
        extra = f" · k={result.get('k')} · 聚类 {len(result.get('clusters') or [])} 个"
    elif result.get("multi_fallback"):
        extra = f" · multi 回退 single（{result.get('multi_fallback_error')}）"
    print(f"模式：{mode}{extra}")
    print(f"作答：{result.get('answer')}")
    print(f"置信：{(result.get('confidence') or {}).get('level')} — "
          f"{'；'.join((result.get('confidence') or {}).get('reasons') or [])}")
    print(f"检索命中：{len(result.get('retrieved') or [])} 条")
    if mode == "multi":
        print(f"质量守卫：passed={result.get('guard_passed')} · action={result.get('guard_action')}"
              f" · issues={result.get('guard_issues')}")
    for c in result.get("citations") or []:
        page = c.get("page")
        print(f"  [{c.get('unit_id')}] 《{c.get('book')}》{c.get('chapter') or ''} "
              f"p.{page if page is not None else '?'} · {_clip(c.get('text'), 100)}")
    if (result.get("llm") or {}).get("fallback"):
        print(f"⚠ fallback：{(result.get('llm') or {}).get('error')}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "ask":
        try:
            mode = "single" if getattr(args, "single", False) else args.mode
            k = args.k if getattr(args, "topk", None) is None else args.topk
            result = ask(args.question, topk=k, model=args.model, timeout=args.timeout,
                         retrieval_only=args.retrieval_only, mode=mode,
                         guard=not getattr(args, "no_guard", False),
                         on_guard_fail=getattr(args, "on_guard_fail", "fallback"))
        except KbQaError as exc:
            print(f"[kb_qa] FAILED: {exc}", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=1))
        else:
            _print_human(result)
        return 0
    payload = build_samples(limit=args.limit, model=args.model, timeout=args.timeout,
                            topk=args.topk)
    jp, pp = write_samples(payload, args.out, args.portal_out)
    n_fallback = sum(1 for s in payload["samples"] if s.get("fallback"))
    n_cites = sum(len(s.get("citations") or []) for s in payload["samples"])
    print(f"[kb_qa] samples={len(payload['samples'])} fallback={n_fallback} citations={n_cites}")
    print(f"[kb_qa] out={jp}")
    print(f"[kb_qa] portal={pp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
