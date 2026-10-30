# -*- coding: utf-8 -*-
"""Phase 5 · Golden 扩到 50 + 生成式引用正确率抽检。

- 复用 rag_eval 的检索/BM25/RRF 逻辑；GOLD 50 条（25 原有 + 25 新增主题）
- 生成式抽检：12 条 query 调本地 qwen3.5（think=false）带 [S#] 引用生成；
  统计：引用覆盖率（有引用）、引用精确率（被引来源含关键词比例）、非法引用号（>5）
产出 90_报告/phase5_full_eval.{json,md}
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

KB = Path(r"C:/项目/知识库")
REP = KB / "90_报告"
sys.path.insert(0, str(Path(r"C:/项目/老田的AI助手/tools/kb_corpus")))
import rag_eval as RE  # noqa: E402

EXTRA = [
    ("价值投资与基本面", ["value investing", "基本面", "估值"]),
    ("技术指标 RSI/MACD", ["rsi", "macd", "indicator"]),
    ("布林带策略", ["bollinger", "布林"]),
    ("市场微观结构与订单流", ["order flow", "微观结构", "liquidity"]),
    ("高频交易", ["high frequency", "高频"]),
    ("统计套利", ["statistical arbitrage", "统计套利"]),
    ("波动率指数 VIX", ["vix", "volatility index"]),
    ("债券收益率与久期", ["bond", "yield", "久期"]),
    ("外汇交易", ["forex", "currency", "外汇"]),
    ("商品期货套期保值", ["hedging", "套期保值", "commodity"]),
    ("交易系统开发", ["trading system", "交易系统"]),
    ("仓位规模计算", ["position sizing", "仓位"]),
    ("胜率与盈亏比", ["win rate", "盈亏比", "risk reward"]),
    ("回撤恢复", ["drawdown recovery", "回撤恢复"]),
    ("策略容量", ["capacity", "容量"]),
    ("滑点建模", ["slippage model", "滑点建模"]),
    ("数据供应商对比", ["data provider", "数据源"]),
    ("量化平台对比", ["platform", "平台", "quantconnect"]),
    ("Python 量化库", ["python", "pandas", "numpy"]),
    ("时间序列分析", ["time series", "时间序列"]),
    ("协整与配对交易", ["cointegration", "pairs trading", "配对"]),
    ("机器学习特征工程", ["feature engineering", "特征"]),
    ("强化学习交易", ["reinforcement learning", "强化学习"]),
    ("情绪与行为金融", ["sentiment", "behavior", "情绪"]),
    ("税务与交易成本", ["tax", "fee", "税费"]),
]


def embed(text):
    return RE.embed(text)


def main() -> int:
    RE.REP = REP
    GOLD = RE.GOLD + EXTRA
    docs = json.loads((RE.PACKED / "docs.json").read_text(encoding="utf-8"))
    mat = np.load(RE.PACKED / "emb_norm.f32.npy")
    bm25 = RE.BM25([d.get("title", "") + " " + d.get("text", "") for d in docs])
    print(f"docs={len(docs)} golden={len(GOLD)}", flush=True)
    rows, avg_h, avg_v = RE.evaluate(docs, mat, bm25, GOLD)
    print("retrieval:", avg_h, avg_v, flush=True)

    # 生成式引用抽检（取前 12 条）
    sample_qs = GOLD[:12]
    gen = []
    for q, keys in sample_qs:
        vec = embed(q + " " + " ".join(keys))
        vs = mat @ vec
        bs = bm25.score(q + " " + " ".join(keys))
        vr = np.argsort(-vs); br = np.argsort(-bs)
        vrank = np.empty(len(vs), dtype=np.float32); brank = np.empty(len(bs), dtype=np.float32)
        vrank[vr] = np.arange(len(vs)); brank[br] = np.arange(len(bs))
        fused = -(0.7 / (60.0 + vrank) + 0.3 / (60.0 + brank))
        order = np.argsort(fused)[:5]
        ctx = []
        for i, di in enumerate(order, 1):
            d = docs[int(di)]
            ctx.append(f"[S{i}] {d.get('title','')}\n{d.get('text','')[:500]}")
        prompt = ("仅根据资料回答，300 字内，用中文；关键结论后标注 [S1]-[S5]；资料不足说“资料未覆盖”。\n\n资料：\n"
                  + "\n\n".join(ctx) + "\n\n问题：" + q + "\n回答：")
        body = {"model": "qwen3.5:9b", "stream": False, "think": False,
                "messages": [{"role": "user", "content": prompt}],
                "options": {"temperature": 0.2, "num_predict": 400}}
        import urllib.request
        req = urllib.request.Request("http://127.0.0.1:11434/api/chat",
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                ans = ((json.loads(r.read().decode()).get("message") or {}).get("content") or "").strip()
        except Exception as e:  # noqa: BLE001
            ans = f"[ERROR {type(e).__name__}]"
        cites = [int(x) for x in re.findall(r"\[S(\d+)\]", ans)]
        valid = [c for c in cites if 1 <= c <= 5]
        invalid = [c for c in cites if c < 1 or c > 5]
        hit_cites = 0
        for c in valid:
            d = docs[int(order[c - 1])]
            txt = (d.get("title", "") + " " + d.get("text", "")).lower()
            if any(k.lower() in txt for k in keys):
                hit_cites += 1
        gen.append({"query": q, "answer_len": len(ans), "cites": len(cites),
                    "valid_cites": len(valid), "invalid_cites": len(invalid),
                    "cite_hit": hit_cites, "answer_head": ans[:120]})
        print("gen:", q, "cites", len(cites), "hit", hit_cites, flush=True)

    coverage = sum(1 for g in gen if g["cites"] > 0) / len(gen)
    cite_precision = (sum(g["cite_hit"] for g in gen) / max(1, sum(g["valid_cites"] for g in gen)))
    invalid_rate = sum(g["invalid_cites"] for g in gen) / max(1, sum(g["cites"] for g in gen))
    rep = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
           "docs": len(docs), "golden": len(GOLD),
           "hybrid_recall5": round(avg_h, 3), "vector_recall5": round(avg_v, 3),
           "pass_recall5": avg_h >= 0.6, "rows": rows,
           "gen_sample": len(gen), "citation_coverage": round(coverage, 3),
           "citation_precision": round(cite_precision, 3), "invalid_citation_rate": round(invalid_rate, 3),
           "pass_citation": cite_precision >= 0.9, "gen": gen}
    (REP / "phase5_full_eval.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    L = ["# Phase 5 · Golden 50 + 生成式引用抽检", "",
         f"- 文档 {len(docs):,}；golden **{len(GOLD)}** 条",
         f"- 检索 Recall@5：**混合 {avg_h:.3f}** / 纯向量 {avg_v:.3f}（≥0.6 → {'PASS' if avg_h >= 0.6 else 'FAIL'}）",
         f"- 生成抽检 {len(gen)} 条：引用覆盖 **{coverage:.1%}**，引用精确率 **{cite_precision:.1%}**（目标 ≥90% → {'PASS' if cite_precision >= 0.9 else 'FAIL'}），非法引用率 {invalid_rate:.1%}", "",
         "| 查询 | 回答长度 | 引用数 | 有效 | 命中 |", "|---|---|---|---|---|"]
    for g in gen:
        L.append(f"| {g['query'][:18]} | {g['answer_len']} | {g['cites']} | {g['valid_cites']} | {g['cite_hit']} |")
    L += ["", "## 说明", "- 引用精确率=被引来源中文本含查询关键词的比例；未做人工逐句核对（生成式引用正确率需人工抽检复核）。",
          "- Phase 3 转写仍阻塞（YouTube 网络不可达），知识单元级引用（video_id+时间戳）待网络恢复。"]
    (REP / "phase5_full_eval.md").write_text("\n".join(L), encoding="utf-8")
    print(json.dumps({"hybrid_recall5": round(avg_h, 3), "vector_recall5": round(avg_v, 3),
                      "citation_coverage": round(coverage, 3), "citation_precision": round(cite_precision, 3),
                      "invalid_rate": round(invalid_rate, 3)}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
