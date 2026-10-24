# -*- coding: utf-8 -*-
"""词云别名表巡检（周跑）：发现疑似同义变体 → 本地 LLM 核准 → 飞书推送。

背景：TAG_ALIAS（K266 文档词云）与 WORD_ALIAS（K263 股吧词云）靠人工按
全量数据核定（2026-10-08 首次核定 9+4 组）。新变体随数据漂移出现，本脚本
周扫词频自动发现候选。

两级过滤（实测教训：纯子串规则 24 组候选误报 >90%，如"子公司⊃公司"、
"市场情绪⊃市场"均为不可并的组合词/上下位词）：
1. 子串宽召回：候选词（频≥3、len≥3）包含某基准词（top300 高频）
2. 本地 qwen3.5:9b 判同义（同义才并，组合词/上下位不并）

报告只列 LLM 核准组，最终仍需人工核定补录——不自动改表。

用法：python alias_audit.py [--push] [--no-llm]（--no-llm 只看子串召回）
调度：计划任务 K266-AliasAudit 每周一 09:30；持 ollama_lock（批处理）
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SHADOW = os.path.join(HERE, "data", "k266_docs", "structure_shadow.jsonl")
GUBA_DIR = os.path.join(HERE, "data", "guba")
SEND_FEISHU = os.path.join(ROOT, "news_collector", "send_feishu.py")
OLLAMA = "http://127.0.0.1:11434/api/chat"

TOP_BASE = 300     # 基准词取词频 top N（len≥2）
MIN_CAND = 3       # 候选词最低词频
MAX_CAND = 40      # 送 LLM 核准的候选上限（周跑控时）
MAX_REPORT = 10    # 每域最多报告组数
_WORD_RE = re.compile(r"^[\dA-Za-z%.:：,，！!？?]+$")

LLM_PROMPT = ("判断两个词在金融资讯词云场景是否为同一主题词（应归并计数）。\n"
              "词A: %s  词B: %s\n"
              "仅当 A 是 B 的同义变体才归并（例：AI算力/算力、国庆节/国庆、"
              "触及涨停/涨停）；组合词、上下位词、不同概念不归并"
              "（例：子公司/公司、市场情绪/市场、光芯片/芯片、半年报/年报）。\n"
              "只输出 YES 或 NO。")


def ask_llm(cand, base, timeout=30):
    """本地 Ollama 判同义：YES/NO/ERR。"""
    import requests
    # num_ctx 统一 16384（2026-10-24 收口）：本机所有调用方必须共用一个 ctx——
    # Ollama 每次 num_ctx 变化都会**整模型重载**（实测 15~46s），10-24 一天重载
    # 99 次。此处原先钉 4096，依据是 2026-10-08 的"2048 挂死 45s+/8192·16384
    # 病态"对照实验；那是 **CPU 推理 + f16 KV cache** 时代的结论。iGPU/Vulkan +
    # KV q8_0 之后复测：16384 与 4096 生成吞吐相同（33.5 vs 33.8 tok/s），
    # 加载代价 4096=0s（已驻留）/16384≈15~46s（只在换 ctx 时付一次）。故统一到
    # 16384，换掉"唯一健康值 4096"的旧口径。
    # think:False 抑制 qwen3.5 思考链（否则 num_predict 全被 thinking 吃掉，
    # content 恒空）。
    body = {"model": "qwen3.5:9b", "think": False, "stream": False,
            "options": {"num_ctx": 16384, "num_predict": 8},
            "messages": [{"role": "user",
                          "content": LLM_PROMPT % (cand, base)}]}
    try:
        r = requests.post(OLLAMA, json=body, timeout=timeout)
        out = r.json()["message"]["content"].strip().upper()
        return "YES" if "YES" in out else "NO"
    except Exception:
        return "ERR"


def k266_freq():
    """影子 raw tags → 词频（ok 记录；未登记变体原样留存）。"""
    wf = Counter()
    with open(SHADOW, encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("raw_ok"):
                wf.update(t for t in rec.get("tags", []) if t and not _WORD_RE.match(t))
    return wf


def guba_freq():
    """guba/*.jsonl 股民帖标题 → jieba 词频（展示端同款分词+停用）。"""
    from guba_sentiment import _tokens
    wf = Counter()
    for name in os.listdir(GUBA_DIR):
        if not name.endswith(".jsonl"):
            continue
        with open(os.path.join(GUBA_DIR, name), encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("news"):
                    continue
                wf.update(_tokens(r.get("title") or ""))
    return wf


def find_candidates(wf, alias):
    """子串宽召回 → [(候选, 候选频, 基准, 基准频)] 按候选频降序。"""
    known = set(alias) | set(alias.values())
    bases = [w for w, _ in wf.most_common(TOP_BASE)
             if len(w) >= 2 and w not in known and not _WORD_RE.match(w)]
    out = []
    for w, n in wf.most_common():
        if n < MIN_CAND or len(w) < 3 or w in known or _WORD_RE.match(w):
            continue
        for b in bases:
            if b != w and b in w:
                out.append((w, n, b, wf[b]))
                break
    out.sort(key=lambda x: -x[1])
    return out[:MAX_CAND]


def audit_domain(tag, wf, alias, use_llm, lock_ok=False):
    """一个域的巡检：召回 →（可选）LLM 核准 → 报告行。"""
    cands = find_candidates(wf, alias)
    lines = ["■ %s（%d 词，已登记 %d 组，子串召回 %d 组）"
             % (tag, len(wf), len(alias), len(cands))]
    top = " ".join("%s(%d)" % (w, n) for w, n in wf.most_common(10))
    lines.append("  高频 top10：%s" % top)
    if not cands:
        lines.append("  无候选，词表健康")
        return lines
    if not use_llm:
        lines += ["    · %s(%d) ⊃ %s(%d)" % (w, n, b, bn)
                  for w, n, b, bn in cands[:MAX_REPORT]]
        return lines
    import ollama_lock
    ok, err = [], 0
    for i, (w, n, b, bn) in enumerate(cands):
        v = ask_llm(w, b)
        if v == "YES":
            ok.append((w, n, b, bn))
        elif v == "ERR":
            err += 1
        time.sleep(0.3)
        # 教训（2026-10-08 实跑）：判定循环漏心跳 → 锁 mtime 停在 acquire 时刻
        # → 1200s 被 M3 回收抢入 → 两批处理并发挤 Ollama，判定吃满超时。判定
        # 循环必须与批处理同待遇逐组续命。heartbeat 无 pid 校验，仅持锁时调。
        if lock_ok:
            ollama_lock.heartbeat()
        if (i + 1) % 10 == 0:
            print("  [%s] LLM 判定进度 %d/%d" % (tag, i + 1, len(cands)), flush=True)
    if ok:
        lines.append("  LLM 核准同义 %d 组（人工终审后补录）：" % len(ok))
        lines += ["    · %s(%d) → %s(%d)" % (w, n, b, bn) for w, n, b, bn in ok[:MAX_REPORT]]
    else:
        lines.append("  LLM 核准 0 组（召回 %d 组均判非同义），词表健康" % len(cands))
    if err:
        lines.append("  ⚠ LLM 判定失败 %d 组（Ollama 异常，下轮复检）" % err)
    return lines


def build_report(use_llm=True):
    lock_ok = False
    want_llm = use_llm          # 区分"主动 --no-llm"与"想用但降级"
    if use_llm:
        sys.path.insert(0, ROOT)
        import ollama_lock
        lock_ok = ollama_lock.acquire("alias_audit", wait_s=600)
        if not lock_ok:
            use_llm = False       # 抢不到锁降级纯召回（下轮 LLM 复检）

    sys.path.insert(0, HERE)
    from k266_m3_structure import TAG_ALIAS
    from guba_sentiment import WORD_ALIAS
    try:
        lines = ["【别名巡检周报】%s%s"
                 % (datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "（降级：未获 ollama 锁，仅子串召回）"
                    if want_llm and not use_llm else "")]
        lines += audit_domain("K266 文档词云", k266_freq(), TAG_ALIAS,
                              use_llm, lock_ok)
        lines += audit_domain("K263 股吧词云", guba_freq(), WORD_ALIAS,
                              use_llm, lock_ok)
        lines.append("处置：人工终审（LLM 有误判可能）→ "
                     "股吧 WORD_ALIAS 在 portal /kg/lexicon 页在线补录；"
                     "文档 TAG_ALIAS 改 k266_m3_structure.py")
        return "\n".join(lines)
    finally:
        if lock_ok:
            import ollama_lock
            ollama_lock.release()


def push(text):
    tmp = os.path.join(HERE, "logs", "alias_audit_msg.txt")
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    r = subprocess.run([sys.executable, SEND_FEISHU, tmp],
                       capture_output=True, text=True, encoding="utf-8", timeout=90)
    print("push ->", (r.stdout or r.stderr or "").strip()[:120])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--push", action="store_true", help="飞书推送（默认只打印）")
    ap.add_argument("--no-llm", action="store_true", help="跳过 LLM 核准（纯子串召回）")
    args = ap.parse_args()
    report = build_report(use_llm=not args.no_llm)
    print(report)
    if args.push:
        push(report)


if __name__ == "__main__":
    main()
