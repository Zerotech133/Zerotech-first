# -*- coding: utf-8 -*-
"""P3b · 字幕 → 知识单元蒸馏（本地 qwen3.5，think=false）。

输入：20_转写/subs/<video_id>*.vtt + pilot_videos.jsonl 元数据
输出：20_转写/transcripts.jsonl（纯文本，含 video_id/channel/长度）
      30_知识单元/knowledge_units.jsonl（概念/论点/证据/时间戳/来源/可信度）
      90_报告/p3_distill_report.json

用法：python tools/kb_corpus/p3_distill.py [--limit 100] [--chunk 1800]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

KB = Path(r"C:\项目\知识库")
SUB = KB / "20_转写" / "subs"
TRAN = KB / "20_转写" / "transcripts.jsonl"
KU = KB / "30_知识单元" / "knowledge_units.jsonl"
PILOT = KB / "30_知识单元" / "pilot_videos.jsonl"
REP = KB / "90_报告"
OLLAMA = "http://127.0.0.1:11434"
CHAT = "qwen3.5:9b"

TS_RE = re.compile(r"^\d{2}:\d{2}:\d{2}\.\d{3}\s*-->")


def parse_vtt(path: Path) -> str:
    lines = []
    for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = ln.strip()
        if not s or s.upper() == "WEBVTT" or TS_RE.match(s) or s.startswith("NOTE"):
            continue
        s = re.sub(r"<[^>]+>", "", s)
        if s and (not lines or lines[-1] != s):
            lines.append(s)
    return "\n".join(lines)


def chunks(text: str, size: int) -> list[str]:
    return [text[i:i + size] for i in range(0, len(text), size) if text[i:i + size].strip()]


def chat(prompt: str) -> str:
    body = {"model": CHAT, "stream": False, "think": False,
            "messages": [{"role": "user", "content": prompt}],
            "options": {"temperature": 0.1, "num_predict": 1200}}
    req = urllib.request.Request(OLLAMA + "/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=300) as r:
        return ((json.loads(r.read().decode()).get("message") or {}).get("content") or "").strip()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--chunk", type=int, default=1800)
    args = ap.parse_args(argv)
    REP.mkdir(parents=True, exist_ok=True)
    videos = {}
    if PILOT.exists():
        for x in PILOT.read_text(encoding="utf-8").splitlines():
            if x.strip():
                d = json.loads(x)
                videos[d["video_id"]] = d
    vtts = sorted(SUB.glob("*.vtt"))[: args.limit]
    if not vtts:
        print("no vtt files (P3 network blocked?)", flush=True)
        return 3
    units, transcripts, seen = [], [], set()
    for i, f in enumerate(vtts, 1):
        vid = f.name.split(".")[0]
        text = parse_vtt(f)
        meta = videos.get(vid, {})
        transcripts.append({"video_id": vid, "channel_id": meta.get("channel_id", ""),
                            "title": meta.get("title", ""), "chars": len(text)})
        for ci, ch in enumerate(chunks(text, args.chunk)):
            prompt = ("从以下视频字幕片段提取量化/交易知识单元。只输出 JSON 数组，元素字段："
                      '{"concept":"概念","claim":"可验证的论点","evidence":"原文依据(<=120字)"}；'
                      "最多 3 条；无明确知识则输出 []。\n\n字幕：\n" + ch)
            try:
                raw = chat(prompt)
                m = re.search(r"\[.*\]", raw, re.S)
                items = json.loads(m.group(0)) if m else []
            except Exception:  # noqa: BLE001
                items = []
            for it in items[:3]:
                claim = str(it.get("claim", "")).strip()
                if not claim:
                    continue
                h = hashlib.sha1(claim.encode("utf-8")).hexdigest()[:16]
                if h in seen:
                    continue
                seen.add(h)
                units.append({"video_id": vid, "channel_id": meta.get("channel_id", ""),
                              "title": meta.get("title", ""), "chunk": ci,
                              "concept": str(it.get("concept", ""))[:80],
                              "claim": claim[:400], "evidence": str(it.get("evidence", ""))[:300],
                              "confidence": "medium", "source": "youtube_subtitle",
                              "sha1": h})
            time.sleep(0.05)
        if i % 10 == 0:
            print(f"{i}/{len(vtts)} units={len(units)}", flush=True)
    TRAN.parent.mkdir(parents=True, exist_ok=True)
    TRAN.write_text("\n".join(json.dumps(t, ensure_ascii=False) for t in transcripts), encoding="utf-8")
    KU.parent.mkdir(parents=True, exist_ok=True)
    KU.write_text("\n".join(json.dumps(u, ensure_ascii=False) for u in units), encoding="utf-8")
    rep = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
           "videos": len(vtts), "transcripts": len(transcripts), "units": len(units)}
    (REP / "p3_distill_report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
