# -*- coding: utf-8 -*-
"""
K-ORG-032: Agent 编码执行器——在编岗以人设真模型产出，直接写仓库工作区。
链路：读人设(bundle) → Ollama(9b, think:false) 生成 → 写目标文件 → 校验(json/关键词)。
产出文件由主控验证后 git 提交，提交信息注明产出 Agent。
"""
import json
import sys
import time
import urllib.request

OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
BUNDLE = json.load(open("portal/agent_ide_bundle.json", encoding="utf-8"))


def chat(agent_id, task, timeout=420, num_ctx=6144):
    """以某岗人设真模型生成。返回文本。"""
    sys_prompt = BUNDLE["agents"][agent_id]["prompt"]
    body = json.dumps({
        "model": "qwen3.5:9b", "stream": False, "think": False,
        "format": "json" if task.get("json_out") else None,
        "options": {"num_ctx": num_ctx, "temperature": 0.3},
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": task["prompt"]},
        ],
    }).encode()
    # format None 时去掉键（Ollama 不接受 null format）
    payload = json.loads(body)
    if not task.get("json_out"):
        payload.pop("format", None)
    req = urllib.request.Request("http://127.0.0.1:8080/ollama/api/chat",
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    d = json.load(OP.open(req, timeout=timeout))
    out = (d.get("message") or {}).get("content", "").strip()
    print("[%s %s %.0fs] %d chars" % (agent_id, task.get("label", ""), time.time() - t0, len(out)))
    return out


def run(label, agent_id, out_path, prompt, validator=None, json_out=False):
    """生成→落盘→校验。返回 (ok, detail)。"""
    task = {"label": label, "prompt": prompt, "json_out": json_out}
    raw = chat(agent_id, task)
    if json_out:
        try:
            data = json.loads(raw)
            content = json.dumps(data, ensure_ascii=False, indent=1)
        except Exception as e:
            return False, "JSON 解析失败: %s | raw[:120]=%r" % (e, raw[:120])
    else:
        content = raw
    if validator:
        ok, detail = validator(content)
        if not ok:
            return False, detail
    with open(out_path, "wb") as f:
        f.write(content.replace("\r\n", "\n").encode("utf-8"))
    print("  -> written", out_path, len(content), "chars")
    return True, "ok"


if __name__ == "__main__":
    print("usage: 由 K-ORG-032 主脚本 import 调用")
