# -*- coding: utf-8 -*-
"""长尾概念 LLM 语义归并 v1.0（W1-2c，2026-11-03）。

背景：W1-2b 后多源确认 23/30，剩余 1006 个单源概念；规则同义表只覆盖明显别名，
长尾需语义归并。本模块**只读** `research/reports/knowledge_multisource_20261103.json`
的概念清单（含 sources[source_key]），在 W1-2 规则归并（1029 簇）之上做：

  1. **向量候选**：本地 ollama `bge-m3`（POST /api/embeddings，批量优先 /api/embed）对每个概念簇
     的展示名 + 归一键算向量，余弦相似度 **≥0.85** 直接合并为候选簇；
  2. **边界判定**：余弦 **0.75~0.85** 的边界对，用本地 `qwen3.5:9b`（think=false, format=json）
     判定「是否同一概念」，只接受 {"same": true}；
  3. **合并口径**：多源确认数按 `concept_synonyms` 口径重算（独立来源=(source_type, source_key)，
     合并簇已知来源去重后 ≥2 即确认多源）；
  4. 输出**可回滚**映射（每个原簇 → 目标簇，含方法/相似度/LLM 理由）：忽略 mapping 即回到 1029 簇基线；
  5. 输出误并风险清单（边界合并 / 低字符重合 / 跨领域来源）供人工抽检。

降级策略（2026-11-03 本机 ollama 与其它会话共用 GPU，bge-m3 首次加载可能排队超时）：
  - 嵌入失败超过全局截止时间 → 自动回退到**确定性字符 n-gram TF-IDF 向量**（`embedding_source`
    标记 `fallback_char_ngram`），保证流水线可复现产出，但语义召回弱于 bge-m3；
  - LLM 判定连续失败 ≥3 次 → 熔断，边界对一律不合并（保守，宁少并不多）。
  - 报告如实记录 ollama 状态/耗时/失败原因，不伪造模型结果。

只读声明：只读输入 JSON（前后 sha256 校验），只写 --out/--md 两个输出；不改学习卡/units/治理。

用法：
  python tools/kb_corpus/concept_merge_llm.py                # 真实 ollama（失败自动降级）
  python tools/kb_corpus/concept_merge_llm.py --no-llm       # 只做向量自动合并（跳过边界 LLM）
  python tools/kb_corpus/concept_merge_llm.py --lexical      # 强制字符 n-gram 向量（无网络，单测/离线）
  python tools/kb_corpus/concept_merge_llm.py --json-stdout
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
if str(REPO / "tools" / "kb_corpus") not in sys.path:
    sys.path.insert(0, str(REPO / "tools" / "kb_corpus"))

import concept_synonyms as cs  # noqa: E402  复用规则归并 + 多源确认口径

DEFAULT_INPUT = REPO / "research" / "reports" / "knowledge_multisource_20261103.json"
OUT_JSON = REPO / "research" / "reports" / "concept_merge_llm_20261103.json"
OUT_MD = REPO / "research" / "reports" / "concept_merge_llm_20261103.md"

VERSION = "1.0.0"
DEFAULT_BASE_URL = "http://127.0.0.1:11434"
EMBED_MODEL = "bge-m3:latest"
LLM_MODEL = "qwen3.5:9b"
AUTO_COS = 0.85                 # 余弦 ≥ 该值 → 直接合并
BOUNDARY_LO = 0.75              # 边界区间 [0.75, 0.85) → LLM 判定
MAX_LLM_PAIRS = 40             # 边界判定最多调用次数（控制 LLM 耗时）
MAX_LLM_FAILURES = 3           # 连续失败熔断阈值
EMBED_BATCH = 64               # /api/embed 批量大小
HTTP_TIMEOUT = 60              # 单次 HTTP 超时（秒）
EMBED_DEADLINE = 300           # 嵌入阶段全局截止（秒），超时回退词法向量
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
STOP = frozenset({"的", "了", "呢", "吗", "啊", "吧", "与", "和", "及"})


# ---------------------------------------------------------------- HTTP 客户端
class OllamaError(RuntimeError):
    pass


def _post_json(url: str, payload: dict, timeout: float = HTTP_TIMEOUT) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise OllamaError(f"{type(exc).__name__}: {exc}") from exc


class OllamaEmbedder:
    """bge-m3 向量：优先 /api/embed 批量，404/失败回退 /api/embeddings 单条。"""

    def __init__(self, base_url: str = DEFAULT_BASE_URL, model: str = EMBED_MODEL,
                 timeout: float = HTTP_TIMEOUT, deadline: float = EMBED_DEADLINE,
                 num_gpu: int | None = None):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.deadline = deadline
        self.calls = 0
        self.sec = 0.0
        self.errors: list[str] = []
        self.mode = "batch"
        # 显式 num_gpu=0 可绕过「GPU 被其它会话独占」时的排队（速度慢但可用）；
        # 默认从环境变量 KB_EMBED_NUM_GPU 读取，None=不传（由 ollama 自行调度）。
        env_gpu = os.environ.get("KB_EMBED_NUM_GPU")
        self.num_gpu = num_gpu if num_gpu is not None else (
            int(env_gpu) if env_gpu not in (None, "") else None)
        self.options = {} if self.num_gpu is None else {"num_gpu": int(self.num_gpu)}

    def _embed_batch(self, texts: list[str]) -> np.ndarray:
        out = _post_json(f"{self.base_url}/api/embed",
                         {"model": self.model, "input": texts, "options": self.options},
                         self.timeout)
        embs = out.get("embeddings")
        if not isinstance(embs, list) or len(embs) != len(texts):
            raise OllamaError("批量 /api/embed 返回格式不符")
        return np.asarray(embs, dtype=np.float32)

    def _embed_single(self, text: str) -> list[float]:
        out = _post_json(f"{self.base_url}/api/embeddings",
                         {"model": self.model, "prompt": text, "options": self.options},
                         self.timeout)
        emb = out.get("embedding")
        if not isinstance(emb, list) or not emb:
            raise OllamaError("单条 /api/embeddings 返回空")
        return emb

    def embed_many(self, texts: list[str]) -> np.ndarray:
        t0 = time.time()
        self.calls = len(texts)
        chunks = [texts[i:i + EMBED_BATCH] for i in range(0, len(texts), EMBED_BATCH)]
        # 1) 批量端点（一次 64 条，显著减少请求数）
        try:
            vecs = [self._embed_batch(chunk) for chunk in chunks]
            self.sec = round(time.time() - t0, 2)
            self.mode = "batch"
            return np.vstack(vecs)
        except OllamaError as exc:
            self.errors.append(f"batch: {exc}")
        # 2) 单条回退（受全局截止时间约束）
        rows: list[list[float]] = []
        try:
            for i, text in enumerate(texts):
                if time.time() - t0 > self.deadline:
                    raise OllamaError(f"嵌入截止时间 {self.deadline}s 超时（{i}/{len(texts)}）")
                rows.append(self._embed_single(text))
        except OllamaError as exc:
            self.errors.append(f"single: {exc}")
            raise OllamaError("; ".join(self.errors)) from exc
        finally:
            self.sec = round(time.time() - t0, 2)
        self.mode = "single"
        return np.asarray(rows, dtype=np.float32)


class OllamaJudge:
    """qwen3.5:9b（think=false, JSON）边界判定：同一概念返回 {"same": true/false, "reason": ...}。"""

    def __init__(self, base_url: str = DEFAULT_BASE_URL, model: str = LLM_MODEL,
                 timeout: float = HTTP_TIMEOUT):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.calls = 0
        self.sec = 0.0
        self.failures = 0
        self.errors: list[str] = []

    @staticmethod
    def _parse(text: str) -> dict | None:
        text = str(text or "").strip()
        text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
        try:
            doc = json.loads(text)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", text, flags=re.S)
            if not m:
                return None
            try:
                doc = json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
        if not isinstance(doc, dict) or "same" not in doc:
            return None
        return {"same": bool(doc.get("same")), "reason": str(doc.get("reason") or "")[:200]}

    def judge_same(self, concept_a: str, concept_b: str) -> dict | None:
        prompt = (
            "你是知识库概念去重助手。判断下面两个概念名是否指同一个概念（同义/别名/繁简/错别字/"
            "同一指标的不同叫法），只输出 JSON：{\"same\": true 或 false, \"reason\": \"一句话理由\"}。\n"
            f"概念A：{concept_a}\n概念B：{concept_b}"
        )
        payload = {"model": self.model, "prompt": prompt, "stream": False, "think": False,
                   "format": "json", "options": {"temperature": 0, "num_predict": 80}}
        t0 = time.time()
        self.calls += 1
        try:
            out = _post_json(f"{self.base_url}/api/generate", payload, self.timeout)
        except OllamaError as exc:
            self.failures += 1
            self.errors.append(str(exc))
            self.sec = round(time.time() - t0, 2)
            return None
        self.sec = round(time.time() - t0, 2)
        parsed = self._parse(out.get("response") or "")
        if parsed is None:
            self.failures += 1
            self.errors.append("无法解析 JSON 响应")
        else:
            self.failures = 0
        return parsed


# ---------------------------------------------------------------- 词法回退向量
def lexical_vectors(texts: list[str], n: int = 2) -> np.ndarray:
    """确定性字符 n-gram（2+3 gram）TF-IDF 向量（L2 归一化）。用于 ollama 不可用/单测，不联网。

    保留中文与字母数字（如 520 均线、VCP 等区分性 token），避免把「520均线战法」与「均线战法」
    误当成同一文本。
    """
    def grams(t: str) -> list[str]:
        s = re.sub(r"[^\w\u4e00-\u9fff]+", "", str(t or "")).lower()
        out: list[str] = []
        for size in (2, 3):
            if len(s) >= size:
                out.extend(s[i:i + size] for i in range(len(s) - size + 1))
        return out or ([s] if s else [])

    docs = [grams(t) for t in texts]
    vocab: dict[str, int] = {}
    for doc in docs:
        for g in doc:
            if g not in vocab:
                vocab[g] = len(vocab)
    df = Counter()
    for doc in docs:
        df.update(set(doc))
    n_docs = max(1, len(docs))
    arr = np.zeros((len(docs), max(1, len(vocab))), dtype=np.float32)
    for i, doc in enumerate(docs):
        tf = Counter(doc)
        total = max(1, sum(tf.values()))
        for g, cnt in tf.items():
            idf = math.log((1.0 + n_docs) / (1.0 + df[g])) + 1.0
            arr[i, vocab[g]] = (1.0 + math.log(cnt)) / total * idf
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return arr / norms


# ---------------------------------------------------------------- 相似度 / 合并
def cosine_matrix(vectors: np.ndarray) -> np.ndarray:
    v = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(v, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    normed = v / norms
    return normed @ normed.T


def build_single_source_clusters(entries: list[dict], groups=None) -> list[dict]:
    """规则归并后的「单源」簇（长尾）列表；每簇带 sources 集合与排序 id。"""
    result = cs.merge_concepts(entries, groups)
    singles = []
    for idx, cluster in enumerate(result["clusters"]):
        sources = {(s["source_type"], s["source_key"]) for s in cluster["known_sources"]}
        if len(sources) >= 2:
            continue
        singles.append({**cluster, "_idx": idx, "_sources": sources})
    return singles


def _union_find(items: list[str]) -> tuple[dict[str, str], callable]:
    parent: dict[str, str] = {x: x for x in items}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    return parent, union


def merge_long_tail(clusters: list[dict], embedder, judge=None, auto_cos: float = AUTO_COS,
                    boundary_lo: float = BOUNDARY_LO, max_llm_pairs: int = MAX_LLM_PAIRS,
                    max_llm_failures: int = MAX_LLM_FAILURES) -> dict:
    """核心：嵌入 → 候选对 → 自动/边界 LLM 合并。返回合并结果 dict（不写文件）。

    embedder 需有 embed_many(texts) -> np.ndarray；
    judge 需有 judge_same(a, b) -> dict|None（None=失败/熔断；judge=None 跳过边界）。
    """
    texts = [" ".join([c["display"]] + list(c.get("merged_keys") or [])[:6]) for c in clusters]
    vectors = embedder.embed_many(texts)
    if vectors.shape[0] != len(texts):
        raise ValueError(f"嵌入行数 {vectors.shape[0]} 与概念数 {len(texts)} 不一致")
    sim = cosine_matrix(vectors)
    n = len(clusters)
    auto_pairs: list[tuple[float, int, int]] = []
    boundary_pairs: list[tuple[float, int, int]] = []
    for i in range(n):
        for j in range(i + 1, n):
            s = float(sim[i, j])
            if s >= auto_cos:
                auto_pairs.append((s, i, j))
            elif s >= boundary_lo:
                boundary_pairs.append((s, i, j))
    auto_pairs.sort(reverse=True)
    boundary_pairs.sort(reverse=True)

    ids = [str(c["_idx"]) for c in clusters]
    parent, union = _union_find(ids)
    methods: dict[tuple[str, str], dict] = {}
    for s, i, j in auto_pairs:
        union(ids[i], ids[j])
        methods[(ids[i], ids[j])] = {"method": "embedding", "similarity": round(s, 4),
                                     "reason": f"余弦 {s:.3f}≥{auto_cos}"}

    llm_accept = llm_reject = llm_failed = 0
    llm_used = 0
    llm_failure_streak = 0
    if judge is not None:
        for s, i, j in boundary_pairs:
            if llm_used >= max_llm_pairs or llm_failure_streak >= max_llm_failures:
                break
            llm_used += 1
            a_text = clusters[i]["display"]
            b_text = clusters[j]["display"]
            verdict = judge.judge_same(a_text, b_text)
            if verdict is None:
                llm_failed += 1
                llm_failure_streak += 1
                continue
            llm_failure_streak = 0
            if verdict.get("same"):
                union(ids[i], ids[j])
                llm_accept += 1
                methods[(ids[i], ids[j])] = {"method": "llm_boundary",
                                             "similarity": round(s, 4),
                                             "reason": f"LLM: {verdict.get('reason', '')[:120]}"}
            else:
                llm_reject += 1

    # 合并组
    groups: dict[str, list[int]] = {}
    for i in range(n):
        groups.setdefault(parent[ids[i]], []).append(i)
    merged_groups = []
    mapping = []
    for root, idxs in groups.items():
        if len(idxs) < 2:
            continue
        idxs = sorted(idxs, key=lambda i: -len(clusters[i].get("merged_keys") or []))
        target = clusters[idxs[0]]
        merged_groups.append({"root": root, "idxs": idxs, "target": target})
        for i in idxs[1:]:
            src = clusters[i]
            methods_key = (ids[idxs[0]], ids[i])
            info = methods.get(methods_key) or {}
            mapping.append({
                "from_cluster_id": f"CS-{src['_idx']:04d}",
                "from_concept": src["display"],
                "to_cluster_id": f"CS-{target['_idx']:04d}",
                "to_concept": target["display"],
                "method": info.get("method", "transitive"),
                "similarity": info.get("similarity"),
                "reason": info.get("reason", "并查集传递合并"),
            })
    return {"clusters": clusters, "sim": sim, "parent": parent,
            "groups": merged_groups, "mapping": mapping,
            "stats": {"auto_pairs": len(auto_pairs), "boundary_pairs": len(boundary_pairs),
                      "auto_merged": len({root for root, idxs in groups.items() if len(idxs) > 1}),
                      "llm_calls": llm_used, "llm_accept": llm_accept,
                      "llm_reject": llm_reject, "llm_failed": llm_failed}}


# ---------------------------------------------------------------- 报告组装
def build_payload(input_path: str | Path = DEFAULT_INPUT, base_url: str = DEFAULT_BASE_URL,
                  embed_model: str = EMBED_MODEL, llm_model: str = LLM_MODEL,
                  use_llm: bool = True, force_lexical: bool = False,
                  auto_cos: float = AUTO_COS, boundary_lo: float = BOUNDARY_LO,
                  max_llm_pairs: int = MAX_LLM_PAIRS, embeder=None, judge=None,
                  embed_timeout: float = HTTP_TIMEOUT,
                  embed_deadline: float = EMBED_DEADLINE) -> dict:
    """主流程（纯内存，不写文件）；embedder/judge 可注入（单测 mock，不真实调 LLM）。"""
    t0 = time.time()
    path = Path(input_path)
    if not path.is_file():
        raise FileNotFoundError(f"输入不存在: {path}")
    raw = path.read_bytes()
    sha_before = hashlib.sha256(raw).hexdigest()
    doc = json.loads(raw.decode("utf-8"))
    entries, _ = cs.plan_from_input(doc)
    baseline = cs.merge_concepts(entries)
    baseline_multi = sum(1 for c in baseline["clusters"] if c["status"] == "多源-确认")
    singles = build_single_source_clusters(entries)
    singles = [c for c in singles]
    # 长尾单源簇（保留全局排序 idx 用于回滚映射）
    if not singles:
        raise ValueError("没有可归并的单源概念簇")

    embed_info: dict = {"source": "lexical_char_ngram", "model": None,
                        "calls": 0, "sec": 0.0, "errors": []}
    texts = [" ".join([c["display"]] + list(c.get("merged_keys") or [])[:6]) for c in singles]
    if not force_lexical:
        if embeder is None:
            embeder = OllamaEmbedder(base_url, embed_model, timeout=embed_timeout,
                                     deadline=embed_deadline)
        try:
            vectors = embeder.embed_many(texts)
            embed_info.update({"source": "ollama_bge_m3", "model": embed_model,
                               "calls": getattr(embeder, "calls", len(singles)),
                               "sec": getattr(embeder, "sec", None),
                               "mode": getattr(embeder, "mode", None),
                               "errors": list(getattr(embeder, "errors", []))})
        except Exception as exc:  # noqa: BLE001 - 降级路径需兜底
            embed_info.update({"source": "fallback_lexical", "model": None,
                               "errors": [f"{type(exc).__name__}: {exc}"][:3] + list(
                                   getattr(embeder, "errors", []))[:3]})
            vectors = lexical_vectors(texts)
    else:
        vectors = lexical_vectors(texts)

    judge_obj = judge
    if use_llm and judge_obj is None:
        judge_obj = OllamaJudge(base_url, llm_model)
    result = merge_long_tail(singles, _VectorEmbedder(vectors), judge=judge_obj,
                             auto_cos=auto_cos, boundary_lo=boundary_lo,
                             max_llm_pairs=max_llm_pairs)
    llm_info = {"enabled": bool(use_llm), "model": llm_model if use_llm else None,
                "calls": getattr(judge_obj, "calls", 0), "sec": getattr(judge_obj, "sec", 0.0),
                "errors": list(getattr(judge_obj, "errors", []))[:5],
                "failed": getattr(judge_obj, "failures", 0)}

    # 最终簇：union-find 结果 → 多源确认数（concept_synonyms 口径：独立 (type,key) ≥2）
    final_groups: dict[str, list[dict]] = {}
    for i, c in enumerate(singles):
        final_groups.setdefault(result["parent"][str(c["_idx"])], []).append(c)
    final_clusters = []
    for root, members in final_groups.items():
        sources: dict[tuple, dict] = {}
        for m in members:
            for s in m["known_sources"]:
                sources.setdefault((s["source_type"], s["source_key"]), s)
        final_clusters.append({
            "root": root, "members": members, "n_members": len(members),
            "sources": list(sources.values()),
            "n_sources": len(sources),
            "multi_confirmed": len(sources) >= 2,
        })
    final_multi = [c for c in final_clusters if c["multi_confirmed"]]
    before_single = len(singles)
    new_multi = max(0, len(final_multi) - 0)  # 单源簇池内新确认多源数
    after_clusters = len(baseline["clusters"]) - sum(len(g["idxs"]) - 1 for g in result["groups"])
    stats = {
        "concepts_before": len(entries),
        "clusters_before": len(baseline["clusters"]),
        "multi_before": baseline_multi,
        "single_pool": before_single,
        "merged_groups": len(result["groups"]),
        "merged_members": sum(len(g["idxs"]) - 1 for g in result["groups"]),
        "clusters_after": after_clusters,
        "multi_after_confirmed": baseline_multi + new_multi,
        "new_multi_from_llm": new_multi,
        **result["stats"],
    }

    # 误并风险清单：边界合并 / 字符重合低 / 跨来源类型
    risky = []
    for g in result["groups"]:
        idxs = g["idxs"]
        texts = [c["display"] for c in (singles[i] for i in idxs)]
        if len(set(texts)) < 2:
            continue
        risk_score = 0.0
        pair_info = [mapping for mapping in result["mapping"]
                     if mapping["to_cluster_id"] == f"CS-{g['target']['_idx']:04d}"]
        if any("llm_boundary" in (p.get("method") or "") for p in pair_info):
            risk_score += 0.5                                    # 边界经 LLM，风险 +0.5
        char_overlaps = [cs._char_jaccard(texts[i], texts[j])
                         for i in range(len(texts)) for j in range(i + 1, len(texts))]
        if char_overlaps and min(char_overlaps) < 0.30:
            risk_score += 0.3
        if len({c["display"] for i in idxs for c in [singles[i]]}) <= 1:
            risk_score += 0.2
        if risk_score >= 0.3:
            risky.append({"group": g["target"]["display"], "members": texts,
                          "risk_score": round(risk_score, 2),
                          "note": "边界/低字符重合合并，建议人工抽检"})
    risky.sort(key=lambda x: -x["risk_score"])

    mapping = result["mapping"]
    payload = {
        "meta": {
            "report": "concept_merge_llm",
            "version": VERSION,
            "task": "W1-2c 长尾概念 LLM 语义归并",
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "read_only": True,
            "read_only_verified": sha_before == hashlib.sha256(path.read_bytes()).hexdigest(),
            "input": {"path": str(path), "sha256_before": sha_before,
                      "sha256_after": hashlib.sha256(path.read_bytes()).hexdigest()},
            "ollama": {"base_url": base_url, "embed_model": embed_model, "llm_model": llm_model,
                       "embedding": embed_info, "llm": llm_info},
            "params": {"auto_cos": auto_cos, "boundary_lo": boundary_lo,
                       "max_llm_pairs": max_llm_pairs, "use_llm": bool(use_llm),
                       "force_lexical": bool(force_lexical),
                       "merge_scope": "仅单源长尾簇（规则归并后 ≥2 源的簇不参与二次合并）"},
            "runtime_sec": round(time.time() - t0, 2),
            "limitations": [
                "语义合并仅在单源长尾簇内进行；多源簇不参与二次合并（避免稀释既有证据）。",
                "bge-m3 不可用时回退字符 n-gram 向量（embedding.source=fallback_lexical），召回弱、需复核；"
                "LLM 连续失败会熔断，边界对一律不合并（保守）。",
                "多源确认数按 concept_synonyms 口径（独立 (source_type,source_key) ≥2）重算；"
                "合并后概念本身的答案一致性/冲突未重算，置信仍为命名层面。",
                "映射表可回滚：忽略 mapping 记录即恢复 W1-2 的 1029 簇基线。",
                "只读输入，不改学习卡/units/治理；本次未真实调 LLM 的部分以 mock/降级路径标注。",
            ],
        },
        "stats": stats,
        "baseline": {"clusters": len(baseline["clusters"]), "multi_confirmed": baseline_multi},
        "mapping": mapping,
        "merged_groups": [
            {"group_id": f"MG-{k + 1:03d}", "display": g["target"]["display"],
             "members": [singles[i]["display"] for i in g["idxs"]],
             "n_members": len(g["idxs"]),
             "multi_confirmed": len({(s["source_type"], s["source_key"])
                                     for i in g["idxs"]
                                     for s in singles[i]["known_sources"]}) >= 2}
            for k, g in enumerate(sorted(result["groups"], key=lambda x: -len(x["idxs"])))],
        "false_merge_risk": risky,
        "rollback": {"strategy": "忽略 mapping（原概念→目标簇），即回到 W1-2 的 1029 簇基线",
                     "mapping_entries": len(mapping)},
    }
    return payload


class _VectorEmbedder:
    """把已算好的向量包装成 embedder 接口（供 merge_long_tail 复用）。"""

    def __init__(self, vectors: np.ndarray):
        self._vectors = vectors
        self.calls = len(vectors)
        self.sec = 0.0
        self.mode = "precomputed"

    def embed_many(self, texts):
        if len(texts) != len(self._vectors):
            raise ValueError("文本数与预计算向量数不一致")
        return self._vectors


def render_markdown(payload: dict) -> str:
    m = payload["meta"]
    s = payload["stats"]
    lines: list[str] = []
    add = lines.append
    add("# 长尾概念 LLM 语义归并（W1-2c，2026-11-03）")
    add("")
    add(f"- 生成：{m['generated_at']} ｜ v{m['version']} ｜ 只读输入：**{m['read_only_verified']}**")
    add(f"- 输入：`{m['input']['path']}`")
    oll = m["ollama"]
    embed = oll["embedding"]
    add(f"- 向量：{embed['source']}（模型 {embed.get('model')}，{embed.get('calls')} 条，"
        f"{embed.get('sec')}s" + (f"，降级原因：{embed['errors'][:1]}" if embed.get("errors") else "") + "）")
    llm = oll["llm"]
    add(f"- 边界判定：{'qwen3.5:9b（think=false, JSON）' if llm['enabled'] else '已跳过'}"
        f"· 调用 {llm['calls']} 次，失败 {llm['failed']}")
    add(f"- 阈值：余弦 ≥{m['params']['auto_cos']} 直接合并；{m['params']['boundary_lo']}~"
        f"{m['params']['auto_cos']} 交 LLM 判定（最多 {m['params']['max_llm_pairs']} 对）")
    add("")
    add("## 一、核心结果（是否达标）")
    add("")
    add("| 指标 | W1-2b 基线 | W1-2c 后 |")
    add("|---|---:|---:|")
    add(f"| 概念簇数 | {s['clusters_before']} | **{s['clusters_after']}** |")
    add(f"| 多源确认数（concept_synonyms 口径） | {s['multi_before']} | **{s['multi_after_confirmed']}** |")
    add(f"| 新增确认多源 | — | {s['new_multi_from_llm']} |")
    add(f"| 合并组 / 合并成员 | — | {s['merged_groups']} / {s['merged_members']} |")
    add(f"| 向量自动合并 / LLM 接受 | — | {s['auto_pairs']} 对候选 / {s['llm_accept']} 接收 |")
    add("")
    ok = s["multi_after_confirmed"] >= 30
    add(f"> **结论：多源确认 {s['multi_after_confirmed']} {'≥' if ok else '<'} 30，"
        f"{'✅ 达标' if ok else '❌ 未达标'}。**")
    if not ok:
        add("> 未达标根因：单源长尾簇的**来源高度分散且语义重复有限**——多数簇的语义近邻来自同一来源"
            "（合并后仍是单源），或向量在字符层面差异大（尤其 fallback 向量）；要再提升需扩大输入源"
            "（书/研报/更多博主）或人工标注种子对。")
    add("")
    add("## 二、合并组清单（按成员数）")
    add("")
    groups = payload["merged_groups"]
    if groups:
        add("| # | 合并后概念 | 成员数 | 多源确认 | 成员 |")
        add("|---:|---|---:|---|---|")
        for i, g in enumerate(groups[:30], 1):
            add(f"| {i} | {g['display']} | {g['n_members']} | "
                f"{'✅' if g['multi_confirmed'] else '单源'} | {'；'.join(g['members'])[:120]} |")
    else:
        add("（无 LLM/向量合并组）")
    add("")
    add("## 三、合并映射（可回滚）")
    add("")
    add(f"- 共 **{len(payload['mapping'])}** 条映射（原簇 → 目标簇，含方法/相似度/理由）；")
    add("- 回滚方式：忽略 `mapping` 即恢复 W1-2 的 1029 簇基线（原 concept_synonyms 输出不变）。")
    if payload["mapping"]:
        add("")
        add("| 原概念 | 目标概念 | 方法 | 相似度 | 理由 |")
        add("|---|---|---|---:|---|")
        for mp in payload["mapping"][:25]:
            add(f"| {mp['from_concept']} | {mp['to_concept']} | {mp['method']} | "
                f"{mp['similarity']} | {str(mp['reason'])[:60]} |")
    add("")
    add("## 四、误并风险清单")
    add("")
    risky = payload["false_merge_risk"]
    if risky:
        add("| 合并后概念 | 风险分 | 成员 | 说明 |")
        add("|---|---:|---|---|")
        for r in risky[:15]:
            add(f"| {r['group']} | {r['risk_score']} | {'；'.join(r['members'])[:80]} | {r['note']} |")
    else:
        add("（无高风险合并组）")
    add("")
    add("## 五、口径与局限")
    add("")
    for i, item in enumerate(m["limitations"], 1):
        add(f"{i}. {item}")
    add("")
    add("## 六、复现")
    add("")
    add("```powershell")
    add("C:\\项目\\py312\\python.exe -X utf8 tools/kb_corpus/concept_merge_llm.py")
    add("C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_concept_merge_llm.py")
    add("```")
    return "\n".join(lines) + "\n"


def run(input_path: str | Path = DEFAULT_INPUT, out_json: str | Path | None = None,
        out_md: str | Path | None = None, **kwargs) -> dict:
    payload = build_payload(input_path, **kwargs)
    jp = Path(out_json) if out_json else OUT_JSON
    mp = Path(out_md) if out_md else OUT_MD
    jp.parent.mkdir(parents=True, exist_ok=True)
    mp.parent.mkdir(parents=True, exist_ok=True)
    jp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    mp.write_text(render_markdown(payload), encoding="utf-8")
    return payload


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="concept_merge_llm",
        description="长尾概念 LLM 语义归并（W1-2c）：bge-m3 向量 + qwen3.5:9b 边界判定 → 可回滚映射")
    ap.add_argument("--input", default=str(DEFAULT_INPUT), help="W1-1 多源 JSON（只读）")
    ap.add_argument("--out", default=str(OUT_JSON), help="输出 JSON")
    ap.add_argument("--md", default=str(OUT_MD), help="输出 Markdown")
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL, help="ollama 地址")
    ap.add_argument("--embed-model", default=EMBED_MODEL)
    ap.add_argument("--llm-model", default=LLM_MODEL)
    ap.add_argument("--auto-cos", type=float, default=AUTO_COS)
    ap.add_argument("--boundary-lo", type=float, default=BOUNDARY_LO)
    ap.add_argument("--max-llm-pairs", type=int, default=MAX_LLM_PAIRS)
    ap.add_argument("--no-llm", action="store_true", help="跳过边界 LLM 判定")
    ap.add_argument("--lexical", action="store_true", help="强制字符 n-gram 向量（无网络/离线）")
    ap.add_argument("--embed-timeout", type=float, default=HTTP_TIMEOUT,
                    help="单次 ollama HTTP 超时（秒）")
    ap.add_argument("--embed-deadline", type=float, default=EMBED_DEADLINE,
                    help="嵌入阶段全局截止（秒），超时回退词法向量")
    ap.add_argument("--json-stdout", action="store_true")
    args = ap.parse_args(argv)
    try:
        payload = run(args.input, args.out, args.md, base_url=args.base_url,
                      embed_model=args.embed_model, llm_model=args.llm_model,
                      use_llm=not args.no_llm, force_lexical=args.lexical,
                      auto_cos=args.auto_cos, boundary_lo=args.boundary_lo,
                      max_llm_pairs=args.max_llm_pairs,
                      embed_timeout=args.embed_timeout,
                      embed_deadline=args.embed_deadline)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[concept_merge_llm] 运行失败: {exc}", file=sys.stderr)
        return 2
    s = payload["stats"]
    print(f"# LLM 语义归并 v{payload['meta']['version']}：簇 {s['clusters_before']} → {s['clusters_after']}，"
          f"多源确认 {s['multi_before']} → {s['multi_after_confirmed']}"
          f"（{'达标' if s['multi_after_confirmed'] >= 30 else '未达 30'}）；"
          f"合并组 {s['merged_groups']}，LLM {s['llm_accept']} 接受 / {s['llm_reject']} 拒绝")
    print(f"# 向量源: {payload['meta']['ollama']['embedding']['source']}")
    print(f"# 输出: {Path(args.out).resolve()}")
    print(f"# 输出: {Path(args.md).resolve()}")
    if args.json_stdout:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
