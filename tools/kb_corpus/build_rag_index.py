# -*- coding: utf-8 -*-
"""接入 AI/111111 RAG：从语料生成 markdown 文档 → KnowledgeBase 建索引 → 验证问答。

产物：
  50_RAG/docs/*.md                （每视频一篇：标题/描述/高赞评论）
  50_RAG/rag_index_corpus.json    （KnowledgeBase 原生 schema）
  90_报告/rag_integration_report.md
用法：python tools/kb_corpus/build_rag_index.py [--top-videos 800]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

REPO = Path(r"C:\项目\老田的AI助手")
KB = Path(r"C:\项目\知识库")
D = KB / "10_规范化"
CLN = KB / "20_清洗" / "comments_clean"
OUT = KB / "50_RAG"
REP = KB / "90_报告"
AI = REPO / "AI" / "111111"
POS = re.compile(r"(?i)量化|策略|回测|因子|交易|投资|股票|期权|期货|对冲|套利|组合|风险|"
                 r"portfolio|trading|quant|backtest|factor|alpha|beta|hedge|option|future|"
                 r"invest|stock|market|financial|finance|volatility|momentum|arbitrage|sharpe")


def core_quant_channels() -> set[str]:
    out = set()
    fp = KB / "02_索引与读取说明" / "目标频道有效清单_100_v2.jsonl"
    with fp.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            d = json.loads(line)
            tags = (d.get("candidate") or {}).get("topic_tags") or []
            if "core_quant" in tags and d.get("channel_id"):
                out.add(d["channel_id"])
    return out


def ensure_ai_path():
    p = str(AI)
    if p not in sys.path:
        sys.path.insert(0, p)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top-videos", type=int, default=800)
    args = ap.parse_args(argv)
    ensure_ai_path()
    from knowledge_base import KnowledgeBase

    vdf = pd.read_parquet(D / "videos.parquet")
    cq = core_quant_channels()
    vdf = vdf[vdf["channel_id"].isin(cq)].copy()
    vdf["text"] = (vdf["title"].fillna("") + " " + vdf["description"].fillna("")).astype(str)
    vdf = vdf[vdf["text"].map(lambda x: bool(POS.search(x)))]
    for c in ("comment_count", "view_count", "like_count"):
        vdf[c] = pd.to_numeric(vdf[c], errors="coerce").fillna(0)
    vdf["score"] = vdf["comment_count"] * 2 + vdf["view_count"] / 1000 + vdf["like_count"] / 10
    vdf = vdf.sort_values("score", ascending=False).head(args.top_videos)
    vids = set(vdf["video_id"])
    print("selected videos:", len(vdf), flush=True)

    # 评论 top10
    frames = []
    for f in sorted(CLN.glob("*.parquet")):
        tb = pq.read_table(f, columns=["video_id", "text", "like_count"])
        df = tb.to_pandas()
        df = df[df["video_id"].isin(vids)]
        if len(df):
            frames.append(df)
    cdf = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["video_id", "text", "like_count"])
    cdf["like_count"] = pd.to_numeric(cdf["like_count"], errors="coerce").fillna(0)
    cdf = cdf.sort_values("like_count", ascending=False).groupby("video_id").head(10)
    cgroup = {k: v["text"].astype(str).tolist() for k, v in cdf.groupby("video_id")}

    docs_dir = OUT / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    documents = {}
    for r in vdf.itertuples():
        desc = (r.description or "").strip()
        cs = cgroup.get(r.video_id, [])
        md = ("# " + str(r.title) + "\n\n"
              "- video_id: " + r.video_id + "\n"
              "- channel_id: " + str(r.channel_id) + "\n"
              "- upload_date: " + str(r.upload_date) + "\n\n"
              "## 描述\n" + desc[:3000] + "\n\n"
              "## 高赞评论\n" + "\n".join("- " + str(t)[:300] for t in cs))
        p = docs_dir / (r.video_id + ".md")
        p.write_text(md, encoding="utf-8")
        documents[str(p)] = md
    print("docs written:", len(documents), flush=True)

    kb = KnowledgeBase(top_k=5)
    t0 = time.time()
    kb.ingest(documents)
    idx = OUT / "rag_index_corpus.json"
    kb.save(str(idx))
    print("index saved:", idx, round(idx.stat().st_size / 1e6, 1), "MB, chunks:", len(getattr(kb, "_records", [])),
          "in", round(time.time() - t0, 1), "s", flush=True)

    # 验证问答
    qs = ["如何避免回测过拟合？", "walk-forward 验证怎么做？", "交易成本与滑点如何影响策略？"]
    answers = []
    for q in qs:
        t1 = time.time()
        try:
            r = kb.answer(q)
        except Exception as e:  # noqa: BLE001
            r = {"error": f"{type(e).__name__}: {e}"}
        answers.append({"question": q, "result": r, "seconds": round(time.time() - t1, 1)})
        print("Q:", q, "=>", str(r)[:160], flush=True)

    rep = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
           "videos": len(vdf), "docs": len(documents), "chunks": len(getattr(kb, "_records", [])),
           "index": str(idx), "index_mb": round(idx.stat().st_size / 1e6, 1), "answers": answers}
    (REP / "rag_integration_report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    L = ["# RAG 接入验证（AI/111111 KnowledgeBase）", "",
         f"- 文档：{len(documents)} 篇（TOP {len(vdf)} core_quant 视频）；chunk {len(getattr(kb, '_records', []))}；索引 {idx.stat().st_size/1e6:.1f} MB",
         "- 模型：bge-m3 embedding + qwen3.5:9b（AI/111111 原生契约）", "",
         "## 验证问答"]
    for a in answers:
        L += ["### " + a["question"], "", "```json", json.dumps(a["result"], ensure_ascii=False)[:800], "```", ""]
    (REP / "rag_integration_report.md").write_text("\n".join(L), encoding="utf-8")
    print("report:", REP / "rag_integration_report.md", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
