# -*- coding: utf-8 -*-
"""K-ORG-039: GPT-6.0 密钥通道探测（老田 10-13 12:10 提供 itokengate 端点）。
验四项：基础对话 / reasoning 声明 / tool_call 支持 / 编码小活。
Key 从 config/model_keys.local.json 读（gitignored，不进版本库）。"""
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
KEYS = json.load(open("config/model_keys.local.json", encoding="utf-8"))
URL = KEYS["gpt6"]["url"]
KEY = KEYS["gpt6"]["api_key"]
OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(messages, extra=None, timeout=180):
    body = {"model": "gpt-6-sol", "messages": messages,
            "max_tokens": 4096, **(extra or {})}
    req = urllib.request.Request(URL, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + KEY})
    t0 = time.time()
    resp = OP.open(req, timeout=timeout)
    d = json.loads(resp.read().decode("utf-8"))
    dt = time.time() - t0
    return d, dt


def pick_text(d):
    """兼容 OpenAI 标准与 reasoning 变体：取 message.content，空则拼块。"""
    msg = (d.get("choices") or [{}])[0].get("message", {})
    c = msg.get("content")
    if isinstance(c, list):
        return "".join(b.get("text", "") for b in c if isinstance(b, dict)), msg
    return (c or ""), msg


print("=== 1) 基础对话 ===")
try:
    d, dt = call([{"role": "user", "content": "回复四个字：通道畅通"}])
    text, msg = pick_text(d)
    print("OK %.1fs model=%s stop=%s text=%r"
          % (dt, d.get("model"), (d.get("choices") or [{}])[0].get("finish_reason"),
             text[:30]))
    print("    msg keys:", sorted(msg.keys()),
          "| reasoning present:", bool(msg.get("reasoning_content") or msg.get("reasoning")))
except Exception as e:
    print("FAIL", type(e).__name__, str(e)[:200])
    sys.exit(1)

print("=== 2) tool_call 支持（claims supportsToolCall=true）===")
try:
    d, dt = call(
        [{"role": "user", "content": "北京今天天气怎么样？用 get_weather 工具查"}],
        {"tools": [{"type": "function", "function": {
            "name": "get_weather",
            "parameters": {"type": "object",
                           "properties": {"city": {"type": "string"}},
                           "required": ["city"]}}}]})
    msg = (d.get("choices") or [{}])[0].get("message", {})
    tcs = msg.get("tool_calls") or []
    print("OK %.1fs tool_calls=%d" % (dt, len(tcs)))
    if tcs:
        print("    first:", tcs[0].get("function", {}).get("name"),
              tcs[0].get("function", {}).get("arguments", "")[:80])
except Exception as e:
    print("FAIL", type(e).__name__, str(e)[:200])

print("=== 3) 编码小活（产出质量探针）===")
try:
    d, dt = call([{"role": "user", "content":
        "只输出 python 代码（```python 围栏内），实现 top_k_words(text, k)："
        "统计英文单词频次返回前 k 个 (word, count) 列表，并列按字母序。"
        "不要解释。"}])
    text, _ = pick_text(d)
    print("OK %.1fs %d chars" % (dt, len(text)))
    print(text[:400])
except Exception as e:
    print("FAIL", type(e).__name__, str(e)[:200])
