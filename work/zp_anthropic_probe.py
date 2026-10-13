# -*- coding: utf-8 -*-
"""K-ORG-038: 智谱包年(Coding Plan)Anthropic 兼容端点探测。
背景: 老田 10-13 11:54 指令「我的智谱是包年的你去研究一下」。
调研: 套餐额度与 API 钱包分开计费(1113≠套餐耗尽); Coding Plan 走
      https://open.bigmodel.cn/api/anthropic (Anthropic 兼容), API Key 复用。
探测: /v1/messages 最小请求 × 两种鉴权头 × 双模型档。"""
import json
import os
import urllib.request

KEY = os.environ.get("GLM_API_KEY") or os.environ.get("ZHIPUAI_API_KEY") or ""
BASE = "https://open.bigmodel.cn/api/anthropic"
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 直连(判例)

def probe(model, auth_style):
    body = json.dumps({
        "model": model, "max_tokens": 2048,
        "messages": [{"role": "user", "content": "回复两个字：在岗"}],
    }).encode("utf-8")
    headers = {"Content-Type": "application/json",
               "anthropic-version": "2023-06-01"}
    if auth_style == "x-api-key":
        headers["x-api-key"] = KEY
    else:
        headers["Authorization"] = "Bearer " + KEY
    req = urllib.request.Request(BASE + "/v1/messages", data=body,
                                 headers=headers, method="POST")
    try:
        with opener.open(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        blocks = data.get("content", [])
        kinds = [(b.get("type"), b.get("thinking", "")[:30] if b.get("type") == "thinking"
                  else b.get("text", "")[:40]) for b in blocks]
        return True, "stop=%s blocks=%s" % (data.get("stop_reason"), kinds)
    except urllib.error.HTTPError as e:
        return False, "HTTP %d %s" % (e.code, e.read().decode("utf-8", "replace")[:180])
    except Exception as e:
        return False, type(e).__name__ + " " + str(e)[:120]

for model in ("glm-4.6", "glm-4.5"):
    for style in ("x-api-key", "bearer"):
        ok, detail = probe(model, style)
        print("[%s/%s] %s %s" % (model, style, "OK " if ok else "FAIL", detail))
