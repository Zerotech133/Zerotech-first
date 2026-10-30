# -*- coding: utf-8 -*-
"""P3a · 试点视频字幕抓取（yt-dlp，网络可用时执行）。

输入：30_知识单元/pilot_videos.jsonl
输出：20_转写/subs/<video_id>.<lang>.vtt + 90_报告/p3_fetch_report.json

用法：python tools/kb_corpus/p3_fetch_subs.py [--limit 100] [--proxy URL]
说明：YouTube 被网络限制时逐条失败并记录 error，不中断整批；脚本本身不下载视频文件。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

KB = Path(r"C:\项目\知识库")
PILOT = KB / "30_知识单元" / "pilot_videos.jsonl"
SUB = KB / "20_转写" / "subs"
REP = KB / "90_报告"
YT = Path(r"C:\aios\CowAgent\venv\Scripts\python.exe")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--proxy", default="")
    args = ap.parse_args(argv)
    SUB.mkdir(parents=True, exist_ok=True)
    REP.mkdir(parents=True, exist_ok=True)
    videos = [json.loads(x) for x in PILOT.read_text(encoding="utf-8").splitlines() if x.strip()][: args.limit]
    results = []
    ok = 0
    for i, v in enumerate(videos, 1):
        vid = v["video_id"]
        out_tpl = str(SUB / (vid + ".%(ext)s"))
        cmd = [str(YT), "-m", "yt_dlp", "--skip-download", "--write-auto-subs", "--write-subs",
               "--sub-langs", "zh.*,en.*,en", "--sub-format", "vtt",
               "--no-warnings", "--socket-timeout", "20", "-o", out_tpl,
               "https://www.youtube.com/watch?v=" + vid]
        if args.proxy:
            cmd += ["--proxy", args.proxy]
        t0 = time.time()
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace")
            err = (p.stderr or "").strip().splitlines()
            got = list(SUB.glob(vid + "*.vtt"))
            results.append({"video_id": vid, "rc": p.returncode, "vtt": [g.name for g in got],
                            "seconds": round(time.time() - t0, 1), "error": (err[-1] if err else "")[:200]})
            ok += 1 if got else 0
        except subprocess.TimeoutExpired:
            results.append({"video_id": vid, "rc": None, "vtt": [], "error": "timeout"})
        except Exception as e:  # noqa: BLE001
            results.append({"video_id": vid, "rc": None, "vtt": [], "error": f"{type(e).__name__}: {e}"[:200]})
        if i % 10 == 0:
            print(f"{i}/{len(videos)} subs_ok={ok}", flush=True)
    rep = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
           "requested": len(videos), "subs_ok": ok, "results": results}
    (REP / "p3_fetch_report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"requested": len(videos), "subs_ok": ok}, ensure_ascii=False), flush=True)
    return 0 if ok else 3


if __name__ == "__main__":
    sys.exit(main())
