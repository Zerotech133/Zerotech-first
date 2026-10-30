# -*- coding: utf-8 -*-
"""P1 · 语料规范化导出：video_channel_map / videos / comments。

- 流式读取 948 个 ZIP 内 observations/*.jsonl.gz；
- video_metadata：按 video_id 保留**最新 collected_at** 的元信息；
- video_comments：按 video_id 保留最新一次观察的评论（扁平化为评论行）；
- 生成：
    10_规范化/video_channel_map.jsonl    (video_id, channel_id)
    10_规范化/videos.jsonl               (latest metadata)
    10_规范化/comments.jsonl             (评论+回复，author 哈希脱敏)
    10_规范化/*.parquet                  (转换)
    90_报告/p1_export_report.{json,md}
- 不改动原始 ZIP；PII：作者仅存 sha256 前 16 位。

用法：python tools/kb_corpus/export.py [--limit-zips N]
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path

KB = Path(r"C:\项目\知识库")
SUB = KB / "01_数据分包"
OUTD = KB / "10_规范化"
REP = KB / "90_报告"


def author_hash(x) -> str:
    return hashlib.sha256(str(x or "").encode("utf-8")).hexdigest()[:16]


def zips() -> list[Path]:
    z = sorted(SUB.glob("*.zip")) + sorted((SUB / "补充更正_v2").glob("*.zip"))
    return z


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit-zips", type=int, default=0)
    args = ap.parse_args(argv)
    OUTD.mkdir(parents=True, exist_ok=True)
    REP.mkdir(parents=True, exist_ok=True)
    all_zips = zips()
    if args.limit_zips:
        all_zips = all_zips[: args.limit_zips]
    meta: dict[str, dict] = {}       # video_id -> latest metadata row
    comments: dict[str, dict] = {}   # video_id -> latest comments snapshot {collected_at, rows}
    t0 = time.time()
    obs_files = 0
    line_total = 0

    for zi, z in enumerate(all_zips, 1):
        with zipfile.ZipFile(z) as zf:
            names = [n for n in zf.namelist() if n.endswith(".jsonl.gz")]
            for n in names:
                obs_files += 1
                with zf.open(n) as f:
                    data = gzip.decompress(f.read())
                for raw in data.splitlines():
                    if not raw.strip():
                        continue
                    line_total += 1
                    try:
                        row = json.loads(raw)
                    except ValueError:
                        continue
                    et = row.get("entity_type")
                    payload = row.get("payload") or {}
                    vid = payload.get("video_id")
                    if not vid:
                        continue
                    ca = str(row.get("collected_at") or "")
                    if et == "video_metadata":
                        info = payload.get("info") or {}
                        prev = meta.get(vid)
                        if prev is None or ca >= prev.get("collected_at", ""):
                            meta[vid] = {
                                "video_id": vid,
                                "channel_id": info.get("channel_id") or "",
                                "title": info.get("title") or "",
                                "description": (info.get("description") or "")[:2000],
                                "upload_date": str(info.get("upload_date") or ""),
                                "duration": info.get("duration"),
                                "view_count": info.get("view_count"),
                                "like_count": info.get("like_count"),
                                "comment_count": info.get("comment_count"),
                                "tags": (info.get("tags") or [])[:30],
                                "language": info.get("language") or "",
                                "collected_at": ca,
                                "snapshot_id": row.get("snapshot_id") or "",
                            }
                    elif et == "video_comments":
                        prev = comments.get(vid)
                        if prev is None or ca >= prev.get("collected_at", ""):
                            rows = []
                            for c in (payload.get("comments") or []):
                                rows.append({
                                    "video_id": vid,
                                    "comment_id": str(c.get("id") or ""),
                                    "parent": str(c.get("parent") or "root"),
                                    "text": (c.get("text") or "")[:4000],
                                    "author_hash": author_hash(c.get("author_id") or c.get("author") or ""),
                                    "like_count": c.get("like_count"),
                                    "published_at": str(c.get("published_at") or c.get("time_text") or ""),
                                    "collected_at": ca,
                                    "snapshot_id": row.get("snapshot_id") or "",
                                })
                            comments[vid] = {"collected_at": ca, "rows": rows}
        if zi % 50 == 0:
            print(f"zips {zi}/{len(all_zips)} obs_files={obs_files} lines={line_total} "
                  f"videos={len(meta)} comments_videos={len(comments)} {time.time()-t0:.0f}s", flush=True)

    # 写出
    mp = OUTD / "video_channel_map.jsonl"
    with mp.open("w", encoding="utf-8") as f:
        for vid in sorted(meta):
            ch = meta[vid].get("channel_id") or ""
            if ch:
                f.write(json.dumps({"video_id": vid, "channel_id": ch}, ensure_ascii=False) + "\n")
    vp = OUTD / "videos.jsonl"
    with vp.open("w", encoding="utf-8") as f:
        for vid in sorted(meta):
            f.write(json.dumps(meta[vid], ensure_ascii=False) + "\n")
    cp = OUTD / "comments.jsonl"
    n_comments = 0
    with cp.open("w", encoding="utf-8") as f:
        for vid in sorted(comments):
            for r in comments[vid]["rows"]:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
                n_comments += 1
    print(f"written map={sum(1 for _ in mp.open(encoding='utf-8'))} videos={len(meta)} comments={n_comments}", flush=True)

    # 质量检查
    chans = {r["channel_id"] for r in meta.values() if r.get("channel_id")}
    report = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
              "zips": len(all_zips), "obs_files": obs_files, "lines": line_total,
              "videos": len(meta), "videos_with_channel": sum(1 for r in meta.values() if r.get("channel_id")),
              "channels": len(chans), "comments": n_comments,
              "seconds": round(time.time() - t0, 1),
              "outputs": {"map": str(mp), "videos": str(vp), "comments": str(cp)}}
    (REP / "p1_export_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    L = ["# P1 · 语料规范化导出报告", "",
         f"- 时间：{report['generated_at']}；ZIP {report['zips']}；观察文件 {report['obs_files']}；行 {report['lines']}",
         f"- 视频 **{report['videos']}**（含频道 {report['videos_with_channel']}）；频道 **{report['channels']}**；评论/回复 **{report['comments']}**",
         f"- 耗时 {report['seconds']}s；输出目录 `C:\\项目\\知识库\\10_规范化\\`",
         "- 评论作者字段仅存 sha256 前 16 位（PII 最小化），原文保留；后续 PII 深度过滤在 Phase 2。",
         "- 说明：视频/评论按 video_id 取最新 collected_at 观察；原始 ZIP 未改动。"]
    (REP / "p1_export_report.md").write_text("\n".join(L), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
