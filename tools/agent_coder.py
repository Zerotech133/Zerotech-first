# -*- coding: utf-8 -*-
"""
K-ORG-032: Agent 编码执行器——在编岗以人设真模型产出，直接写仓库工作区。
链路：读人设(bundle) → 生成 → 写目标文件 → 校验(json/关键词)。
产出文件由主控验证后 git 提交，提交信息注明产出 Agent。

K-ORG-033 云端化（老田 10-13 10:28 指令：写代码的全部调用云端模型）：
  backend = "cloud"  智谱开放平台 API（GLM_API_KEY，免费档 glm-4-flash 实测 0.4s；
                     付费旗舰 4.5/4.6/4.7 余额不足 code 1113，充值后改 MODEL 即用）
           = "ollama" 本地 9b（60s 级，作离线兜底）
  CODER_BACKEND 环境变量可切，默认 cloud。
  Claude Code CLI(2.1.83 已装) 留给 worker/调度侧——本会话沙箱拦其内部 spawn(reg.exe)。
"""
import json
import os
import sys
import time
import urllib.request

OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
BUNDLE = json.load(open("portal/agent_ide_bundle.json", encoding="utf-8"))

ZHIPU_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
CLOUD_MODEL = os.environ.get("CODER_CLOUD_MODEL", "glm-4-flash")


def chat_cloud(agent_id, task, timeout=120):
    """智谱官方 API 生成（免费档 flash，人设 system 照用）。"""
    key = os.environ.get("GLM_API_KEY", "")
    if not key:
        raise RuntimeError("GLM_API_KEY 未设置")
    sys_prompt = BUNDLE["agents"][agent_id]["prompt"]
    payload = {
        "model": CLOUD_MODEL,
        "max_tokens": task.get("max_tokens", 4096),
        "temperature": 0.3,
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": task["prompt"]},
        ],
    }
    req = urllib.request.Request(ZHIPU_URL, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + key})
    t0 = time.time()
    d = json.load(OP.open(req, timeout=timeout))
    out = (d["choices"][0]["message"]["content"] or "").strip()
    u = d.get("usage", {})
    print("[cloud:%s %s %.1fs] %d chars (tok in=%s out=%s)"
          % (agent_id, task.get("label", ""), time.time() - t0, len(out),
             u.get("prompt_tokens"), u.get("completion_tokens")))
    return out


def chat(agent_id, task, timeout=420, num_ctx=6144):
    """本地 Ollama 生成（兜底通道，逻辑保持 032 原样）。"""
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


def backend():
    return os.environ.get("CODER_BACKEND", "cloud")


def chat_auto(agent_id, task, timeout_cloud=120, timeout_local=420):
    """按 CODER_BACKEND 路由；cloud 失败自动降级 ollama。"""
    if backend() == "cloud":
        try:
            return chat_cloud(agent_id, task, timeout_cloud)
        except Exception as e:
            print("[cloud FAIL -> fallback ollama]", str(e)[:120])
    return chat(agent_id, task, timeout_local)


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
