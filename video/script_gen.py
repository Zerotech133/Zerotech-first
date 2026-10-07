# -*- coding: utf-8 -*-
"""短视频分镜脚本生成 — 本地 Ollama

基于 news.db 近 Nh 小时命中素材，生成竖屏口播视频脚本：
  {title, duration_hint, scenes: [{id, big(屏幕大字≤12字), say(口播40-70字)}]}
结构：钩子 → 3 个要点 → 总结 → 互动引导。
"""
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))
NEWS_DIR = os.path.abspath(os.path.join(HERE, "..", "news_collector"))
CST = timezone(timedelta(hours=8))
OLLAMA = "http://127.0.0.1:11434/api/chat"

SYS = (
    "你是一名爆款短视频财经口播编导。规则：①只依据给定素材，禁止编造数据/事件；"
    "②口语化、短句、有节奏感；③全部简体中文；④严格按用户要求的格式输出。"
)

PROMPT = (
    "以下是近 {h} 小时财经素材（⭐为高关注）：\n{mat}\n\n"
    "请挑选最值得讲的 1 个主题，写一条 {n} 个分镜的竖屏口播脚本。\n"
    "结构：镜头1=3秒钩子（制造悬念/直击痛点）；镜头2~{m}=核心要点（按素材事实讲）；"
    "倒数第2镜=总结观点；最后1镜=互动引导（提问求评论）。\n"
    "严格输出 JSON（不要 markdown 代码块、不要多余文字）：\n"
    "{{\"title\":\"视频标题15字内\",\"scenes\":["
    "{{\"id\":1,\"big\":\"屏幕大字(4-8字,名词短语,宁短勿长)\",\"say\":\"口播文案(35-70字,口语,一个镜头只讲一件事)\"}}"
    ", ...]}}\n"
    "要求：big 与 say 内容呼应但不照抄；say 中数据须来自素材并可含来源；全片约 50-70 秒。"
)


def fetch_material(hours, limit):
    db = sqlite3.connect(os.path.join(NEWS_DIR, "news.db"))
    since = (datetime.now(CST) - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M")
    rows = db.execute(
        "SELECT source, title, hit FROM news WHERE created_at>=? "
        "ORDER BY hit DESC, created_at DESC LIMIT ?", (since, limit)).fetchall()
    db.close()
    src = {"wallstreetcn": "见闻", "sina": "新浪", "eastmoney": "东财"}
    return ["%s[%s] %s" % ("⭐" if r[2] else "  ", src.get(r[0], r[0]),
                           (r[1] or "")[:70]) for r in rows]


def generate():
    n = int(CFG["scene_count"])
    mat = fetch_material(int(CFG["material_hours"]), int(CFG["material_limit"]))
    if len(mat) < 8:
        raise RuntimeError("素材不足 %d 条" % len(mat))
    r = requests.post(OLLAMA, json={
        "model": CFG["model"],
        "messages": [{"role": "system", "content": SYS},
                     {"role": "user", "content": PROMPT.format(
                         h=CFG["material_hours"], mat="\n".join(mat),
                         n=n, m=n - 2)}],
        "stream": False, "think": False,
        "options": {"temperature": 0.7, "num_predict": 1600},
    }, timeout=600)
    r.raise_for_status()
    text = r.json()["message"]["content"].strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("LLM 未输出 JSON: " + text[:200])
    script = json.loads(m.group(0))
    scenes = script.get("scenes", [])
    if not scenes or not script.get("title"):
        raise ValueError("脚本结构不完整")
    # 规范化
    out_scenes = []
    for i, s in enumerate(scenes[:n], 1):
        big = re.sub(r"\s+", "", str(s.get("big", "")))[:14]
        say = str(s.get("say", "")).strip()
        if say:
            out_scenes.append({"id": i, "big": big or ("要点%d" % i), "say": say})
    script["scenes"] = out_scenes
    script["gen_ts"] = datetime.now(CST).strftime("%Y-%m-%d %H:%M")
    return script


if __name__ == "__main__":
    sc = generate()
    print(json.dumps(sc, ensure_ascii=False, indent=1))
