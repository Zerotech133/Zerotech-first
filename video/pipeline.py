# -*- coding: utf-8 -*-
"""视频合成管线：分镜脚本 → 逐镜 TTS → 音频拼接 + SRT → ffmpeg 竖屏成片。

所有中间文件走 ASCII 临时目录（规避 ffmpeg filter 中文路径转义），
成片完成后移动到 output/。
"""
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))
TOOLS = os.path.abspath(os.path.join(HERE, "..", "tools"))
FFMPEG = os.path.join(TOOLS, "ffmpeg-9.0.2-essentials_build", "bin", "ffmpeg.exe")
FFPROBE = FFMPEG.replace("ffmpeg.exe", "ffprobe.exe")
WORK = r"C:\Temp\vidwork"
CST = timezone(timedelta(hours=8))
FONT = "C\\:/Windows/Fonts/simhei.ttf"  # ffmpeg filter 内路径转义


def log(msg):
    os.makedirs(os.path.join(HERE, "logs"), exist_ok=True)
    with open(os.path.join(HERE, "logs", "video.log"), "a", encoding="utf-8") as f:
        f.write("[%s] %s\n" % (datetime.now(CST).strftime("%m-%d %H:%M:%S"), msg))


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=kw.get("timeout", 300),
                       cwd=kw.get("cwd"), encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError("cmd fail %s: %s" % (cmd[0], (r.stderr or "")[-400:]))
    return r


def probe_dur(path):
    r = run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path])
    return float(r.stdout.strip())


def wrap_cn(text, width=15):
    """中文按字数硬换行。"""
    text = text.strip()
    return "\n".join(text[i:i + width] for i in range(0, len(text), width))


def wrap_units(text, max_units=16):
    """按显示宽度换行：中文/全角占 2，ASCII 占 1（用于屏幕大字）。"""
    lines, cur, units = [], "", 0
    for ch in text.strip():
        u = 2 if ord(ch) > 0x2E7F else 1
        if units + u > max_units and cur:
            lines.append(cur)
            cur, units = "", 0
        cur += ch
        units += u
    if cur:
        lines.append(cur)
    return "\n".join(lines)


def srt_time(t):
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return "%02d:%02d:%02d,%03d" % (h, m, s, ms)


def build(script):
    if os.path.exists(WORK):
        shutil.rmtree(WORK, ignore_errors=True)
    os.makedirs(WORK, exist_ok=True)
    scenes = script["scenes"]
    gap = float(CFG["gap_sec"])
    w, h = (int(x) for x in CFG["resolution"].split("x"))

    # 1) 逐镜 TTS（独立进程）
    wavs, durs = [], []
    for sc in scenes:
        txt_p = os.path.join(WORK, "say_%02d.txt" % sc["id"])
        wav_p = os.path.join(WORK, "s%02d.wav" % sc["id"])
        with open(txt_p, "w", encoding="utf-8") as f:
            f.write(sc["say"])
        run([sys.executable, os.path.join(HERE, "tts_one.py"), txt_p, wav_p,
             str(CFG["voice_rate"]), CFG["voice_hint"]], timeout=120)
        d = probe_dur(wav_p)
        wavs.append(wav_p)
        durs.append(d)
        log("tts scene %d %.2fs %s" % (sc["id"], d, sc["big"]))

    # 2) 静音间隔 + concat
    sr = run([FFPROBE, "-v", "error", "-select_streams", "a:0",
              "-show_entries", "stream=sample_rate,channels",
              "-of", "csv=p=0", wavs[0]]).stdout.strip().split(",")
    sil = os.path.join(WORK, "sil.wav")
    run([FFMPEG, "-y", "-f", "lavfi", "-i",
         "anullsrc=channel_layout=mono:sample_rate=%s" % sr[0],
         "-t", str(gap), sil], timeout=60)

    # 3) 每镜时间窗 + SRT + concat 列表
    concat_txt, srt_lines, windows = [], [], []
    t0 = 0.0
    for i, (sc, d) in enumerate(zip(scenes, durs), 1):
        start, end = t0, t0 + d
        windows.append((start, end + gap))
        srt_lines += ["%d" % i,
                      "%s --> %s" % (srt_time(start), srt_time(end + 0.1)),
                      wrap_cn(sc["say"], 18), ""]
        concat_txt.append("file '%s'" % wavs[i - 1].replace("\\", "/"))
        concat_txt.append("file '%s'" % sil.replace("\\", "/"))
        t0 += d + gap
    total = t0
    with open(os.path.join(WORK, "list.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(concat_txt))
    with open(os.path.join(WORK, "subs.srt"), "w", encoding="utf-8") as f:
        f.write("\n".join(srt_lines))
    audio = os.path.join(WORK, "audio.wav")
    run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i",
         os.path.join(WORK, "list.txt"), "-c", "copy", audio], timeout=120)

    # 4) 文字文件（大字/字幕）
    with open(os.path.join(WORK, "brand.txt"), "w", encoding="utf-8") as f:
        f.write(CFG["brand"])
    with open(os.path.join(WORK, "title.txt"), "w", encoding="utf-8") as f:
        f.write(script["title"])
    for sc in scenes:
        with open(os.path.join(WORK, "big_%02d.txt" % sc["id"]), "w",
                  encoding="utf-8") as f:
            f.write(wrap_units(sc["big"], 16))
        with open(os.path.join(WORK, "cap_%02d.txt" % sc["id"]), "w",
                  encoding="utf-8") as f:
            f.write(wrap_cn(sc["say"], 15))

    # 5) filter_complex_script（ASCII，中文只在 textfile 内容里）
    acc = CFG["accent_color"]
    chains = [
        "drawbox=x=0:y=0:w=%d:h=8:color=%s:t=fill" % (w, acc),
        "drawtext=expansion=none:fontfile='%s':textfile=brand.txt:fontcolor=0x9db1d4:"
        "fontsize=36:x=(w-text_w)/2:y=64" % FONT,
        "drawtext=expansion=none:fontfile='%s':textfile=title.txt:fontcolor=0xe8edf5:"
        "fontsize=44:line_spacing=10:x=(w-text_w)/2:y=140" % FONT,
    ]
    for sc, (st, en) in zip(scenes, windows):
        enb = "enable='between(t\\,%.2f\\,%.2f)'" % (st, en)
        chains.append(
            "drawtext=expansion=none:fontfile='%s':textfile=big_%02d.txt:fontcolor=white:"
            "fontsize=84:line_spacing=20:x=(w-text_w)/2:y=h*0.36:%s:"
            "borderw=5:bordercolor=black@0.7" % (FONT, sc["id"], enb))
        chains.append(
            "drawtext=expansion=none:fontfile='%s':textfile=cap_%02d.txt:fontcolor=0xf2f5fa:"
            "fontsize=48:line_spacing=16:x=(w-text_w)/2:y=h*0.72:%s:"
            "box=1:boxcolor=black@0.45:boxborderw=20" % (FONT, sc["id"], enb))
    chains.append("format=yuv420p")
    fc = "[0:v]" + ",".join(chains) + "[v]"

    out_mp4 = os.path.join(WORK, "out.mp4")
    run([FFMPEG, "-y",
         "-f", "lavfi", "-i", "color=c=%s:s=%dx%d:r=%d:d=%.2f" % (
             CFG["bg_color"], w, h, CFG["fps"], total + 0.2),
         "-i", audio,
         "-filter_complex", fc,
         "-map", "[v]", "-map", "1:a",
         "-c:v", "libx264", "-preset", "medium", "-crf", "21",
        "-c:a", "aac", "-b:a", "128k", "-shortest",
        "-movflags", "+faststart", out_mp4], timeout=900, cwd=WORK)

    # 6) 归档到 output/
    today = datetime.now(CST).strftime("%Y-%m-%d")
    slug = "".join(c for c in script["title"] if c.isalnum() or "\u4e00" <= c <= "\u9fa5")[:18]
    dest_dir = os.path.join(HERE, "output", "%s-%s" % (today, slug))
    os.makedirs(dest_dir, exist_ok=True)
    shutil.move(out_mp4, os.path.join(dest_dir, "video.mp4"))
    shutil.move(audio, os.path.join(dest_dir, "audio.wav"))
    shutil.move(os.path.join(WORK, "subs.srt"), os.path.join(dest_dir, "subs.srt"))
    with open(os.path.join(dest_dir, "script.json"), "w", encoding="utf-8") as f:
        json.dump(script, f, ensure_ascii=False, indent=1)
    log("video built %.1fs -> %s" % (total, dest_dir))
    return {"dir": dest_dir, "duration": round(total, 1),
            "scenes": len(scenes), "size_mb": round(
                os.path.getsize(os.path.join(dest_dir, "video.mp4")) / 1048576, 1)}
