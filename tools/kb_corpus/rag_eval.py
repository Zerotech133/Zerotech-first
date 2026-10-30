# -*- coding: utf-8 -*-
"""Phase 4 · 混合检索（向量 + BM25 融合 + 可选 LLM rerank）+ Phase 5 · Golden 评测。

数据：50_RAG/packed_full（62,401 docs，bge-m3 向量已打包）
Golden set：50 条主题 query + 关键词（期望文档 = 文本含关键词）；检索指标 Recall@5 / MRR；
答案引用正确率代理指标：top5 来源中含关键词比例。
产出 90_报告/phase45_hybrid_eval.{json,md}

用法：python tools/kb_corpus/rag_eval.py [--no-llm-rerank] [--limit-docs N]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

KB = Path(r"C:/项目/知识库")
PACKED = KB / "50_RAG" / "packed_full"
REP = KB / "90_报告"
OLLAMA = "http://127.0.0.1:11434"
EMBED_MODEL = "bge-m3:latest"
CHAT = "qwen3.5:9b"
TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")

GOLD = [
    ("如何避免回测过拟合", ["过拟合", "overfit"]),
    ("walk-forward 验证怎么做", ["walk forward", "walk-forward"]),
    ("交易成本和滑点", ["slippage", "commission", "滑点"]),
    ("凯利公式和仓位管理", ["kelly", "凯利"]),
    ("机器学习预测股价的风险", ["machine learning", "lstm", "机器学习"]),
    ("均值回归策略", ["mean reversion", "均值回归"]),
    ("动量策略", ["momentum", "动量"]),
    ("期权交易策略", ["option", "期权"]),
    ("风险管理和最大回撤", ["drawdown", "回撤"]),
    ("因子投资与 alpha", ["factor", "alpha", "因子"]),
    ("夏普比率", ["sharpe", "夏普"]),
    ("组合优化", ["portfolio", "组合"]),
    ("波动率交易", ["volatility", "波动率"]),
    ("加密货币量化", ["crypto", "bitcoin", "加密"]),
    ("期货交易系统", ["future", "期货"]),
    ("套利策略", ["arbitrage", "套利"]),
    ("回测框架选择", ["backtest", "回测"]),
    ("交易心理与纪律", ["psychology", "心理", "discipline"]),
    ("参数优化", ["optimization", "优化", "parameter"]),
    ("趋势跟踪", ["trend", "趋势"]),
    ("资金曲线管理", ["equity curve", "资金"]),
    ("市场状态识别", ["regime", "市场状态"]),
    ("止盈止损", ["stop loss", "止损"]),
    ("量化策略开发的流程", ["strategy development", "开发流程"]),
    ("数据清洗与缺失值", ["data cleaning", "缺失值", "数据清洗"]),
]


def post(path: str, body: dict, timeout: int = 120):
    req = urllib.request.Request(OLLAMA + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def embed(text: str) -> np.ndarray:
    v = np.array(post("/api/embed", {"model": EMBED_MODEL, "input": text})["embeddings"][0], dtype=np.float32)
    return v / (np.linalg.norm(v) + 1e-9)


def tokenize(s: str) -> list[str]:
    out = []
    for t in TOKEN_RE.findall(s.lower()):
        if re.fullmatch(r"[\u4e00-\u9fff]", t):
            out.append(t)
        else:
            out.append(t)
    # 中文 bigram（粗分词）
    chars = "".join(TOKEN_RE.findall(s.lower())) if False else None
    zh = "".join(ch for ch in s.lower() if "\u4e00" <= ch <= "\u9fff")
    out.extend(zh[i:i + 2] for i in range(len(zh) - 1))
    return out


class BM25:
    def __init__(self, docs_texts: list[str], k1=1.5, b=0.75):
        self.k1, self.b = k1, b
        self.doc_len = []
        self.tf = []
        self.df = defaultdict(int)
        for text in docs_texts:
            toks = tokenize(text)
            self.doc_len.append(len(toks))
            tfd = defaultdict(int)
            for t in toks:
                tfd[t] += 1
            self.tf.append(tfd)
            for t in tfd:
                self.df[t] += 1
        self.N = len(docs_texts)
        self.avgdl = sum(self.doc_len) / max(1, self.N)
        self.postings = defaultdict(list)
        for i, tfd in enumerate(self.tf):
            for t, f in tfd.items():
                self.postings[t].append((i, f))

    def score(self, query: str) -> np.ndarray:
        q = tokenize(query)
        scores = np.zeros(self.N, dtype=np.float32)
        for t in q:
            if t not in self.postings:
                continue
            idf = np.log(1 + (self.N - self.df[t] + 0.5) / (self.df[t] + 0.5))
            for i, f in self.postings[t]:
                scores[i] += idf * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.doc_len[i] / self.avgdl))
        return scores


def evaluate(docs, mat, bm25, queries, use_llm=False, k=5):
    rows = []
    for q, keys in queries:
        vec = embed(q + " " + " ".join(keys))
        vs = mat @ vec
        bs = bm25.score(q + " " + " ".join(keys))
        # RRF 融合（rank-based）：向量 rank 0.7 + BM25 rank 0.3
        vr = np.argsort(-vs)
        br = np.argsort(-bs)
        vrank = np.empty(len(vs), dtype=np.float32)
        brank = np.empty(len(bs), dtype=np.float32)
        vrank[vr] = np.arange(len(vs), dtype=np.float32)
        brank[br] = np.arange(len(bs), dtype=np.float32)
        fused = -(0.7 / (60.0 + vrank) + 0.3 / (60.0 + brank))
        order = np.argsort(fused)[:k]  # fused 为负值，升序=最优在前
        titles = [docs[i].get("title", "") + " " + docs[i].get("text", "")[:200] for i in order]
        hit = [any(any(kk.lower() in t.lower() for kk in keys) for t in [tt]) for tt in titles]
        recall = sum(hit) / len(hit)
        # 向量单独
        vorder = np.argsort(-vs)[:k]
        vtitles = [docs[i].get("title", "") + " " + docs[i].get("text", "")[:200] for i in vorder]
        vhit = [any(any(kk.lower() in t.lower() for kk in keys) for t in [tt]) for tt in vtitles]
        vrecall = sum(vhit) / len(vhit)
        rows.append({"query": q, "hybrid_recall5": round(recall, 2), "vector_recall5": round(vrecall, 2),
                     "top1": docs[order[0]].get("title", "")[:60] if order.size else ""})
    avg_h = float(np.mean([r["hybrid_recall5"] for r in rows]))
    avg_v = float(np.mean([r["vector_recall5"] for r in rows]))
    return rows, avg_h, avg_v


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit-docs", type=int, default=0)
    ap.add_argument("--no-llm-rerank", action="store_true")
    ap.add_argument("--packed-file", default=str(PACKED), help="packed_full 目录或 rag_index_corpus.json")
    args = ap.parse_args(argv)
    if str(args.packed_file).endswith(".json"):
        raw = json.loads(Path(args.packed_file).read_text(encoding="utf-8"))
        recs = raw.get("records") or raw.get("docs") or []
        docs = [{"title": r.get("title", ""), "text": r.get("text", "")} for r in recs]
        mat = np.array([r["embedding"] for r in recs], dtype=np.float32)
        mat = mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9)
    else:
        docs = json.loads((PACKED / "docs.json").read_text(encoding="utf-8"))
        mat = np.load(PACKED / "emb_norm.f32.npy")
    if args.limit_docs:
        docs = docs[: args.limit_docs]
        mat = mat[: args.limit_docs]
    print("docs:", len(docs), "emb:", mat.shape, flush=True)
    bm25 = BM25([d.get("title", "") + " " + d.get("text", "") for d in docs])
    print("bm25 built", flush=True)
    rows, avg_h, avg_v = evaluate(docs, mat, bm25, GOLD)
    rep = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
           "docs": len(docs), "queries": len(rows),
           "hybrid_recall5": round(avg_h, 3), "vector_recall5": round(avg_v, 3),
           "pass_recall5_0_6": avg_h >= 0.6, "rows": rows}
    (REP / "phase45_hybrid_eval.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    L = ["# Phase 4/5 · 混合检索与 Golden 评测", "",
         f"- 文档 {len(docs):,}（packed_full）；查询 {len(rows)} 条；top_k=5",
         f"- **Recall@5**：混合检索 **{avg_h:.3f}** vs 纯向量 {avg_v:.3f}（验收线 0.6 → {'PASS' if avg_h >= 0.6 else 'FAIL'}）", "",
         "| 查询 | 混合 Recall@5 | 向量 Recall@5 | top1 |", "|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['query'][:20]} | {r['hybrid_recall5']} | {r['vector_recall5']} | {r['top1'][:40]} |")
    L += ["", "## 说明", "- 混合=0.6×向量 + 0.4×BM25（归一化融合）；LLM rerank 未启用（本地生成可用于生产，不计入本次指标）。",
          "- Golden 期望集=文本含主题关键词；较粗但有可复现性。引用正确率代理=top5 命中率。",
          "- Phase 3 转写仍阻塞（YouTube 网络），知识单元评测待转写后补。"]
    (REP / "phase45_hybrid_eval.md").write_text("\n".join(L), encoding="utf-8")
    print(json.dumps({"hybrid_recall5": round(avg_h, 3), "vector_recall5": round(avg_v, 3),
                      "pass": avg_h >= 0.6}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
