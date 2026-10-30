# -*- coding: utf-8 -*-
"""P3a · 试点选题（内容相关性过滤版）。

交付报告已注明频道 content_verification 未系统采样 → 先从 45,827 视频中按
标题/描述做量化-交易相关性打分，再按热度（评论×2+播放/1000+点赞/10）选 TOP N，
每频道最多 3 个，保证多样性。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

KB = Path(r"C:\项目\知识库")
D = KB / "10_规范化"
OUT = KB / "30_知识单元"
REP = KB / "90_报告"

POS = re.compile(
    r"(?i)量化|策略|回测|因子|交易|投资|股票|股市|期权|期货|对冲|套利|组合|收益|风险|"
    r"portfolio|trading|trader|quant|backtest|factor|alpha|beta|hedge|option|future|"
    r"invest|stock|market|financial|finance|crypto|bitcoin|macro|economy|volatility|"
    r"mean.reversion|momentum|arbitrage|valuation|roi|sharpe|drawdown|risk|asset|equity|"
    r"bond|forex|commodity|statistical|algorithmic")
NEG = re.compile(r"(?i)minecraft|gameplay|speedrun|python tutorial|leetcode|unboxing|vlog|"
                 r"cooking|football|basketball|movie|anime|music|cover|reaction|prank|"
                 r"roblox|fortnite|gta|valorant|fifa|nba|nfl|celebrity|epstein|pirate software")


def core_quant_channels() -> set[str]:
    """从有效频道清单读取 topic_tags 含 core_quant 的频道 ID。"""
    import json as _json
    out = set()
    fp = KB / "02_索引与读取说明" / "目标频道有效清单_100_v2.jsonl"
    with fp.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            d = _json.loads(line)
            tags = (d.get("candidate") or {}).get("topic_tags") or []
            if "core_quant" in tags and d.get("channel_id"):
                out.add(d["channel_id"])
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=100)
    args = ap.parse_args(argv)
    df = pd.read_parquet(D / "videos.parquet")
    for c in ("comment_count", "view_count", "like_count"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    df["text"] = (df["title"].fillna("") + " " + df["description"].fillna("")).astype(str)
    df["pos"] = df["text"].map(lambda x: len(POS.findall(x)))
    df["neg"] = df["text"].map(lambda x: len(NEG.findall(x)))
    cq = core_quant_channels()
    df = df[df["channel_id"].isin(cq)]
    df = df[(df["pos"] > 0) & (df["neg"] == 0)].copy()
    df["score"] = df["comment_count"] * 2 + df["view_count"] / 1000 + df["like_count"] / 10 + df["pos"] * 5
    df = df.sort_values("score", ascending=False)
    picked, per = [], {}
    for r in df.itertuples():
        if per.get(r.channel_id, 0) >= 3:
            continue
        picked.append({"video_id": r.video_id, "channel_id": r.channel_id, "title": r.title,
                       "upload_date": r.upload_date, "duration": r.duration,
                       "view_count": r.view_count, "comment_count": r.comment_count,
                       "like_count": r.like_count, "language": r.language,
                       "relevance_hits": int(r.pos), "score": round(float(r.score), 1)})
        per[r.channel_id] = per.get(r.channel_id, 0) + 1
        if len(picked) >= args.top:
            break
    OUT.mkdir(parents=True, exist_ok=True)
    REP.mkdir(parents=True, exist_ok=True)
    p = OUT / "pilot_videos.jsonl"
    p.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in picked), encoding="utf-8")
    rep = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
           "videos_total": int(len(pd.read_parquet(D / "videos.parquet"))),
           "relevant_after_filter": int(len(df)), "picked": len(picked),
           "channels_picked": len(per), "file": str(p),
           "top10": [{"video_id": x["video_id"], "title": x["title"][:60], "lang": x["language"]} for x in picked[:10]]}
    (REP / "p3_select_report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=1)[:1600], flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
