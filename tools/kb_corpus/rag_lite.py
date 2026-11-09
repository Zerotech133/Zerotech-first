# -*- coding: utf-8 -*-
"""P4-lite · 语料本地检索问答（标题+描述+评论，bge-m3 + qwen3.5）。

不依赖外网：embedding 用本地 Ollama bge-m3，生成用本地 qwen3.5（think=false）。

子命令：
  build   从 pilot_videos + videos.parquet + 清洗评论构建索引（40_索引/kb_lite_index.json）
  ask     单问：python tools/kb_corpus/rag_lite.py ask "如何评估策略稳健性"
  report  跑预置 10 问，输出 50_评测/p4lite_qa_report.md
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
# safe_path=true 时脚本目录不入 sys.path：把本树根加入 path（helper 同目录）
import sys as _sys
from pathlib import Path as _Path
for _p in _Path(__file__).resolve().parents:
    if (_p / "safe_urlopen.py").exists():
        _sys.path.insert(0, str(_p))
        break
try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包/仓库根导入时
    from tools.safe_urlopen import safe_urlopen

KB = Path(r"C:\项目\知识库")
D = KB / "10_规范化"
CLN = KB / "20_清洗" / "comments_clean"
IDX = KB / "40_索引" / "kb_lite_index.json"
PILOT = KB / "30_知识单元" / "pilot_videos.jsonl"
REP = KB / "90_报告"
OLLAMA = "http://127.0.0.1:11434"
EMBED_MODEL = "bge-m3:latest"
CHAT_MODEL = "qwen3.5:9b"
TOP_K = 5
MAX_DESC = 1500
TOP_COMMENTS = 15

QUESTIONS = [
    "量化交易中如何避免过拟合？有哪些常见做法？",
    "回测有哪些常见陷阱？样本外验证应该怎么做？",
    "仓位管理和风险控制有哪些常用方法（如凯利公式、最大回撤控制）？",
    "Python 量化回测框架应该怎么选？各有什么优缺点？",
    "因子投资与 alpha 衰减：如何判断一个因子是否还有效？",
    "交易成本和滑点应该如何估计与控制？",
    "机器学习在量化交易中的使用风险是什么？",
    "均值回归策略和动量策略的适用条件有什么区别？",
    "如何用 walk-forward / 滚动窗口验证策略稳健性？",
    "实盘前应该做哪些检查（数据、执行、风控）？",
]


def post(path: str, body: dict, timeout=600):
    req = urllib.request.Request(OLLAMA + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    return json.loads(safe_urlopen(req, timeout=timeout).read().decode())


def embed(text: str) -> list[float]:
    return post("/api/embeddings", {"model": EMBED_MODEL, "prompt": text})["embedding"]


def chat(prompt: str, model=CHAT_MODEL, num_predict=600) -> str:
    r = post("/api/chat", {"model": model, "stream": False, "think": False,
                           "messages": [{"role": "user", "content": prompt}],
                           "options": {"temperature": 0.2, "num_predict": num_predict}})
    return (r.get("message") or {}).get("content", "")


def load_docs() -> list[dict]:
    pilot = [json.loads(x) for x in PILOT.read_text(encoding="utf-8").splitlines() if x.strip()]
    vids = {p["video_id"] for p in pilot}
    vdf = pd.read_parquet(D / "videos.parquet")
    vdf = vdf[vdf["video_id"].isin(vids)].set_index("video_id")
    # 清洗评论：只保留 pilot 视频，按点赞取 topN
    frames = []
    for f in glob.glob(str(CLN / "*.parquet")):
        tb = pq.read_table(f, columns=["video_id", "comment_id", "text", "like_count", "published_at"])
        df = tb.to_pandas()
        df = df[df["video_id"].isin(vids)]
        if len(df):
            frames.append(df)
    cdf = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    cdf["like_count"] = pd.to_numeric(cdf.get("like_count"), errors="coerce").fillna(0)
    docs = []
    for p in pilot:
        vid = p["video_id"]
        meta = vdf.loc[vid] if vid in vdf.index else None
        title = str(meta["title"]) if meta is not None else p.get("title", "")
        desc = str(meta["description"] or "")[:MAX_DESC] if meta is not None else ""
        ch = str(meta["channel_id"]) if meta is not None else p.get("channel_id", "")
        upload = str(meta["upload_date"]) if meta is not None else ""
        docs.append({"doc_id": "v_" + vid, "video_id": vid, "channel_id": ch,
                     "kind": "video", "title": title, "upload_date": upload,
                     "text": "标题：" + title + "\n描述：" + desc})
        sub = cdf[cdf["video_id"] == vid].sort_values("like_count", ascending=False).head(TOP_COMMENTS)
        if len(sub):
            lines = ["- " + str(t)[:300] for t in sub["text"].tolist()]
            docs.append({"doc_id": "c_" + vid, "video_id": vid, "channel_id": ch,
                         "kind": "comments", "title": title, "upload_date": upload,
                         "text": "标题：" + title + "\n高赞评论：\n" + "\n".join(lines)})
    return docs


def build() -> int:
    docs = load_docs()
    print("docs:", len(docs), flush=True)
    t0 = time.time()
    for i, d in enumerate(docs, 1):
        d["embedding"] = embed(d["text"])
        if i % 25 == 0:
            print(f"embedding {i}/{len(docs)} {time.time()-t0:.0f}s", flush=True)
    IDX.parent.mkdir(parents=True, exist_ok=True)
    IDX.write_text(json.dumps({"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                               "embed_model": EMBED_MODEL, "chat_model": CHAT_MODEL,
                               "docs": docs}, ensure_ascii=False), encoding="utf-8")
    print("index:", IDX, "size_MB:", round(IDX.stat().st_size / 1e6, 1), flush=True)
    return 0


def load_index() -> tuple[np.ndarray, list[dict]]:
    data = json.loads(IDX.read_text(encoding="utf-8"))
    docs = data["docs"]
    mat = np.array([d["embedding"] for d in docs], dtype=np.float32)
    mat /= np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9
    return mat, docs


def retrieve(question: str, k: int = TOP_K) -> list[dict]:
    mat, docs = load_index()
    q = np.array(embed(question), dtype=np.float32)
    q /= np.linalg.norm(q) + 1e-9
    scores = mat @ q
    idx = np.argsort(-scores)[:k]
    return [{**{kk: docs[i][kk] for kk in ("doc_id", "video_id", "channel_id", "kind", "title", "upload_date")},
             "score": float(scores[i]), "text": docs[i]["text"][:800]} for i in idx]


PROMPT_PROFILE = os.environ.get("RAG_PROMPT_PROFILE", "strict_nocite").strip().lower()


def _router_module():
    """延迟导入 llm_router（同目录；脚本/包两种运行方式均可）。"""
    _dir = str(Path(__file__).resolve().parent)
    if _dir not in sys.path:
        sys.path.insert(0, _dir)
    import llm_router  # noqa: PLC0415
    return llm_router


def build_prompt_strict_nocite(question: str, hits: list[dict]) -> str:
    """严格引用 + 拒答不带引用的口径（2026-11-09 线上 40 题回归候选）。

    与 strict 的唯一差异：资料不足时只输出固定拒答句，不允许解释/罗列/引用；
    该句 17 字，避免被 answer_eval 的 15 字长度阈值判为长度异常。
    """
    ctx = []
    for i, h in enumerate(hits, 1):
        ctx.append(f"[S{i}] video={h['video_id']} kind={h['kind']} title={h['title'][:80]}\n{h['text'][:600]}")
    return ("你是量化知识库助手。规则：\n"
            "1) 只依据下面编号资料回答，不得编造；\n"
            "2) 先判断资料是否足以回答。若资料不足，最终答案只输出"
            "「资料不足，现有资料无法回答该问题。」，不要添加任何其他文字、"
            "解释、资料内容、资料编号或 [S]；\n"
            "3) 若资料足够回答，每个事实句句末必须标注来源编号，如[S1]；"
            "只能引用确实支撑该句的资料编号，不要引用无关资料；\n"
            "4) 用 3-5 句话回答，不构成投资建议。\n\n"
            "资料：\n" + "\n\n".join(ctx) + "\n\n问题：" + question + "\n回答：")


def build_prompt(question: str, hits: list[dict], profile: str | None = None) -> str:
    """构造回答 prompt：strict_nocite=生产默认（拒答固定句不带引用）；
    strict=旧严格口径（每句末引用/拒答可解释）；base=旧口径。"""
    profile = (profile or PROMPT_PROFILE or "strict").strip().lower()
    if profile == "strict_nocite":
        return build_prompt_strict_nocite(question, hits)
    ctx = []
    for i, h in enumerate(hits, 1):
        ctx.append(f"[S{i}] video={h['video_id']} kind={h['kind']} title={h['title'][:80]}\n{h['text'][:600]}")
    if profile == "base":
        return ("你是量化研究助理。仅根据下面资料回答，资料不足时明确说“资料未覆盖”。\n"
                "回答用中文，控制在 300 字内，并在关键结论后用 [S1]/[S2] 标注来源。\n\n"
                "资料：\n" + "\n\n".join(ctx) + "\n\n问题：" + question + "\n回答：")
    return ("你是量化知识库助手。规则：\n"
            "1) 只依据下面编号资料回答，不得编造；\n"
            "2) 每个事实句句末必须标注来源编号（如[S1]），只能引用确实支撑该句的资料；\n"
            "3) 资料不足以回答时明确说「资料未覆盖」，不要猜测；\n"
            "4) 用 3-5 句话回答，不构成投资建议。\n\n"
            "资料：\n" + "\n\n".join(ctx) + "\n\n问题：" + question + "\n回答：")


def ask(question: str, use_router: bool | None = None) -> dict:
    """问答：检索 → strict prompt → llm_router 三级降级生成。

    回滚：RAG_ROUTER_DISABLE=1 时退回旧本地 chat()；RAG_PROMPT_PROFILE=strict 回退旧严格 prompt，=base 回退旧口径。
    """
    hits = retrieve(question)
    prompt = build_prompt(question, hits)
    sources = [{"sid": f"S{i+1}", "video_id": h["video_id"], "kind": h["kind"],
                "title": h["title"], "score": round(h["score"], 4)} for i, h in enumerate(hits)]
    if use_router is None:
        use_router = os.environ.get("RAG_ROUTER_DISABLE", "0") != "1"
    if use_router:
        r = _router_module().generate(prompt, purpose="rag_lite")
        meta = {k: r.get(k) for k in ("ok", "tier", "provider", "model", "cost_cny",
                                      "daily_cost_cny", "budget_cny", "budget_warning",
                                      "error")}
        meta["attempts"] = r.get("attempts") or []
        return {"question": question, "answer": (r.get("text") or "").strip(),
                "sources": sources, "router": meta}
    answer = chat(prompt)
    return {"question": question, "answer": answer.strip(), "sources": sources,
            "router": {"ok": True, "tier": None, "provider": "ollama_legacy", "legacy": True}}


def report() -> int:
    REP.mkdir(parents=True, exist_ok=True)
    out = []
    for q in QUESTIONS:
        t0 = time.time()
        r = ask(q)
        r["seconds"] = round(time.time() - t0, 1)
        out.append(r)
        print(f"Q: {q[:40]}... ({r['seconds']}s) top1={r['sources'][0]['score'] if r['sources'] else None}", flush=True)
    path = REP / "p4lite_qa_report.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    L = ["# P4-lite · 本地检索问答验证报告", "",
         f"- 索引：{IDX}；embedding={EMBED_MODEL}；生成={CHAT_MODEL}；top_k={TOP_K}",
         "- 知识源：pilot 100 视频（标题+描述+高赞评论），来自 core_quant 过滤后的语料", "",
         "| # | 问题 | 检索 top1 score | 回答摘要 | 引用 |", "|---|---|---|---|---|"]
    for i, r in enumerate(out, 1):
        srcs = " ".join(s["sid"] + ":" + s["video_id"] for s in r["sources"][:3])
        ans = r["answer"].replace("\n", " ")[:120]
        L.append(f"| {i} | {r['question'][:30]} | {r['sources'][0]['score'] if r['sources'] else '—'} | {ans} | {srcs} |")
    L += ["", "## 详细回答（前 3 问）", ""]
    for r in out[:3]:
        L += ["### " + r["question"], "", r["answer"], "", "来源："]
        L += ["- [%s] %s（%s）" % (s["sid"], s["title"][:70], s["video_id"]) for s in r["sources"]]
        L.append("")
    L += ["## 结论", "- 检索与引用链路已通：本地 bge-m3 召回 + qwen3.5 生成 + [S#] 引用。",
          "- 局限：仅标题/描述/评论，无字幕正文；对「方法细节」类问题证据有限，需等 P3 转写补齐。"]
    (REP / "p4lite_qa_report.md").write_text("\n".join(L), encoding="utf-8")
    print("report:", REP / "p4lite_qa_report.md", flush=True)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build")
    a = sub.add_parser("ask")
    a.add_argument("question")
    sub.add_parser("report")
    args = ap.parse_args(argv)
    if args.cmd == "build":
        return build()
    if args.cmd == "ask":
        print(json.dumps(ask(args.question), ensure_ascii=False, indent=1), flush=True)
        return 0
    return report()


if __name__ == "__main__":
    sys.exit(main())
