# -*- coding: utf-8 -*-
"""probe_llm_capability.py — 本地推理能力探针（健康卡第 22 项候选）。

为什么要这个：2026-10-23 的 AI 底层审计发现，健康卡 21 项全在管任务/备份/数据/
仓库，**产品本体（AI 推理）零监控**。而推理故障全是静默的——
上下文截断不报错、空响应返回空字符串、通道死了只有调用时才 500、变慢只是"很慢"。

对 4b / 9b / 35b 各发一次 `think:false` 的短请求，记录四项硬指标：

    content 非空？    空 = 红（思考模型未关思考时 response 恒空）
    tok/s             生成速率；低于阈值 = 黄
    实际 ctx          从 `ollama ps` 读；小于阈值 = 黄（长提示会被静默截断）
    冷加载耗时        模型不在显存时的一次性代价

跑完会把临时加载的模型卸载（`ollama stop`），不留占用。

用法：
    python -B tools/probe_llm_capability.py
    python -B tools/probe_llm_capability.py --json
退出码：全绿=0 / 有黄=0 / 有红=1
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import logging

HOST = "http://127.0.0.1:11434"
MODELS = ["qwen3.5:4b", "qwen3.5:9b", "qwen3.6:35b-a3b"]
MIN_TOKS = 5.0       # 低于此吞吐判黄
MIN_CTX = 8192       # 上下文小于此判黄（9b 默认只有 4096）
PROMPT = "只回答一个字：好"
NUM_PREDICT = 32


def _post_chat(model: str, timeout: int) -> dict:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": PROMPT}],
        "stream": False,
        "think": False,          # 关键：思考模型不关思考，content 恒空
        "options": {"num_predict": NUM_PREDICT, "temperature": 0.0},
    }
    req = urllib.request.Request(
        HOST + "/api/chat",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as resp:
        return json.load(resp)


def _loaded_ctx() -> dict[str, int]:
    """从 `ollama ps` 读每个已加载模型的实际上下文长度。"""
    try:
        proc = subprocess.run(["ollama", "ps"], capture_output=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    text = proc.stdout.decode("utf-8", "replace")
    out: dict[str, int] = {}
    for line in text.splitlines()[1:]:
        cells = line.split()
        if len(cells) < 5:
            continue
        name = cells[0]
        for cell in cells[1:]:
            if cell.isdigit() and int(cell) >= 512:
                out[name] = int(cell)
                break
    return out


def _unload(model: str) -> None:
    try:
        subprocess.run(["ollama", "stop", model], capture_output=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "tools/probe_llm_capability.py", exc)


def probe(model: str, timeout: int) -> dict:
    item = {"model": model, "level": "green", "reason": []}
    started = time.monotonic()
    try:
        data = _post_chat(model, timeout)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        item.update(level="red", reachable=False,
                    reason=[f"调用失败: {type(exc).__name__}: {str(exc)[:80]}"])
        item["elapsed_s"] = round(time.monotonic() - started, 1)
        return item

    item["reachable"] = True
    item["elapsed_s"] = round(time.monotonic() - started, 1)
    item["load_s"] = round((data.get("load_duration") or 0) / 1e9, 1)
    content = (data.get("message") or {}).get("content") or ""
    thinking = (data.get("message") or {}).get("thinking") or ""
    item["content_len"] = len(content)
    item["thinking_len"] = len(thinking)
    item["done_reason"] = data.get("done_reason")

    eval_count = data.get("eval_count") or 0
    eval_duration = (data.get("eval_duration") or 0) / 1e9
    toks = round(eval_count / eval_duration, 1) if eval_duration else 0.0
    item["toks_per_s"] = toks
    item["ctx"] = _loaded_ctx().get(model)

    if not content.strip():
        item["level"] = "red"
        item["reason"].append(
            "content 为空（思考模型未关思考？thinking=%d 字符）" % len(thinking))
    if toks and toks < MIN_TOKS:
        if item["level"] == "green":
            item["level"] = "yellow"
        item["reason"].append("吞吐 %.1f tok/s < %.0f" % (toks, MIN_TOKS))
    ctx = item.get("ctx")
    if ctx and ctx < MIN_CTX:
        if item["level"] == "green":
            item["level"] = "yellow"
        item["reason"].append("上下文 %d < %d（长提示会被静默截断）" % (ctx, MIN_CTX))
    if not item["reason"]:
        item["reason"].append("正常")
    return item


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="本地推理能力探针（内容/吞吐/上下文/冷加载）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--timeout", type=int, default=420)
    ap.add_argument("--keep-loaded", action="store_true", help="跑完不卸载模型")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    order = {"red": 2, "yellow": 1, "green": 0}
    items = []
    for model in MODELS:
        item = probe(model, a.timeout)
        items.append(item)
        if not a.keep_loaded:
            _unload(model)

    worst = max(items, key=lambda x: order[x["level"]])["level"]
    payload = {"verdict": worst, "models": items,
               "thresholds": {"min_toks": MIN_TOKS, "min_ctx": MIN_CTX}}

    if a.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for it in items:
            print("[%-6s] %-18s content=%-4s tok/s=%-6s ctx=%-7s load=%ss"
                  % (it["level"].upper(), it["model"], it.get("content_len"),
                     it.get("toks_per_s"), it.get("ctx"), it.get("load_s")))
            print("          " + "；".join(it["reason"]))
        print()
        print("verdict: %s（%d 个模型，%d 红 %d 黄）" % (
            worst.upper(), len(items),
            sum(1 for x in items if x["level"] == "red"),
            sum(1 for x in items if x["level"] == "yellow")))
    return 1 if worst == "red" else 0


if __name__ == "__main__":
    sys.exit(main())
