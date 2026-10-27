# -*- coding: utf-8 -*-
"""短视频端到端入口：素材 → 分镜脚本 → TTS → 成片 → 飞书审阅 → 门户留档。

用法: python video_make.py [--dry] [--from-script script.json]
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import script_gen
import pipeline
import logging

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(HERE, "config.json")
NEWS_DIR = os.path.abspath(os.path.join(HERE, "..", "news_collector"))
CST = timezone(timedelta(hours=8))


def log(msg):
    os.makedirs(os.path.join(HERE, "logs"), exist_ok=True)
    with open(os.path.join(HERE, "logs", "video.log"), "a", encoding="utf-8") as f:
        f.write("[%s] %s\n" % (datetime.now(CST).strftime("%m-%d %H:%M:%S"), msg))


def push_message(text):
    tmp = os.path.join(NEWS_DIR, "outbox", "pending_video.txt")
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    r = subprocess.run([sys.executable, os.path.join(NEWS_DIR, "send_feishu.py"), tmp],
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    out = (r.stdout or "").strip()
    log("push -> " + out)
    return out.startswith("SEND_OK")


def main():
    dry = "--dry" in sys.argv
    custom = None
    if "--from-script" in sys.argv:
        custom = sys.argv[sys.argv.index("--from-script") + 1]

    if custom:
        script = json.load(open(custom, encoding="utf-8"))
    else:
        log("script generating...")
        script = script_gen.generate()
        log("script ok: %s (%d scenes)" % (script["title"], len(script["scenes"])))

    info = pipeline.build(script)

    items = []
    idx_path = os.path.join(HERE, "output", "index.json")
    try:
        items = json.load(open(idx_path, encoding="utf-8"))
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "video/video_make.py", exc)
    rec = {"id": datetime.now(CST).strftime("%Y%m%d-%H%M"),
           "title": script["title"], "ts": datetime.now(CST).strftime("%m-%d %H:%M"),
           "duration": info["duration"], "scenes": info["scenes"],
           "size_mb": info["size_mb"], "dir": os.path.basename(info["dir"]),
           "status": "draft"}
    items.insert(0, rec)
    os.makedirs(os.path.dirname(idx_path), exist_ok=True)
    tmp = idx_path + ".tmp"
    json.dump(items[:30], open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, idx_path)

    msg = ["🎬 短视频样片已生成 · %s" % rec["ts"], "",
           "《%s》" % script["title"],
           "时长 %ss · %d 个分镜 · %sMB" % (info["duration"], info["scenes"], info["size_mb"]),
           "", "分镜脚本："]
    for s in script["scenes"]:
        msg.append("%d.【%s】%s" % (s["id"], s["big"], s["say"]))
    msg += ["", "成片: video\\output\\%s\\video.mp4" % rec["dir"],
            "字幕: 同目录 subs.srt（可导入剪映精剪）",
            "—— 短视频运营域 · 本地 TTS+ffmpeg 全自动"]
    sent = dry or push_message("\n".join(msg))
    log("done %s sent=%s" % (rec["id"], sent))
    print("VIDEO_OK %s dur=%ss scenes=%d sent=%s" % (
        rec["id"], info["duration"], info["scenes"], sent))


if __name__ == "__main__":
    main()
