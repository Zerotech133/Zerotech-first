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

K-ORG-035/036 F1 通道合流（蓝图 docs/plans/2026-10-13_105000_）：
  backend = "claude_glm"  Claude Code CLI headless × 智谱 Anthropic 兼容端点
          (open.bigmodel.cn/api/anthropic + GLM_API_KEY → glm-4.6)
          工具型 Agent（Read/Edit/Bash/Grep 工具环），与通道A纯补全本质差异；
          主控沙箱拦 spawn → 执行位 worker/计划任务侧。
  路由：claude_glm → cloud → ollama 逐级降级。
"""
import json
import os
import shutil
import subprocess
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


ANTHROPIC_BASE = "https://open.bigmodel.cn/api/anthropic"


def _claude_bin():
    """定位 claude 可执行体。Windows npm 装=claude.cmd 垫片，裸名 subprocess
    CreateProcess 找不到(WinError 2)——必须 which 出带扩展名全路径。"""
    bin_ = os.environ.get("CLAUDE_BIN") or shutil.which("claude")
    if not bin_:
        raise RuntimeError("claude CLI 未找到(PATH 无 claude/CLAUDE_BIN 未设)")
    return bin_


def chat_claude(agent_id, task, timeout=600):
    """通道B：Claude Code CLI headless × 智谱 GLM（Anthropic 兼容端点）。

    与 chat_cloud(纯补全) 的本质差异：模型带工具环（Read/Edit/Bash/Grep/Glob），
    能自己看仓库、改文件、跑验证——SWE-agent 判例「接口>模型」的落地形态。
    人设经 --append-system-prompt 注入；max_turns 限步数防失控。
    """
    key = os.environ.get("GLM_API_KEY") or os.environ.get("ZHIPUAI_API_KEY")
    if not key:
        raise RuntimeError("GLM_API_KEY/ZHIPUAI_API_KEY 未设置")
    sys_prompt = BUNDLE["agents"][agent_id]["prompt"]
    model = os.environ.get("CODER_CLAUDE_MODEL", "glm-4.6")
    env = dict(os.environ)
    env.update(ANTHROPIC_BASE_URL=ANTHROPIC_BASE,
               ANTHROPIC_AUTH_TOKEN=key,
               ANTHROPIC_MODEL=model,
               ANTHROPIC_SMALL_FAST_MODEL=model)
    cmd = [_claude_bin(), "-p", task["prompt"],
           "--output-format", "json",
           "--append-system-prompt", sys_prompt,
           "--allowedTools", task.get("allowed_tools", "Read,Edit,Grep,Glob,Bash"),
           "--max-turns", str(task.get("max_turns", 40))]
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace",
                          timeout=timeout, env=env)
    if proc.returncode != 0:
        raise RuntimeError("claude rc=%d %s" % (proc.returncode,
                          (proc.stderr or proc.stdout or "")[:200]))
    d = json.loads(proc.stdout)
    out = (d.get("result") or "").strip()
    print("[claude_glm:%s %s %.0fs turns=%s cost=%s] %d chars"
          % (agent_id, task.get("label", ""), time.time() - t0,
             d.get("num_turns"), d.get("total_cost_usd"), len(out)))
    return out


def chat_auto(agent_id, task, timeout_cloud=120, timeout_local=420, timeout_claude=600):
    """按 CODER_BACKEND 路由；逐级降级 claude_glm → cloud → ollama。"""
    bk = backend()
    if bk == "claude_glm":
        try:
            return chat_claude(agent_id, task, timeout_claude)
        except Exception as e:
            print("[claude_glm FAIL -> fallback cloud]", str(e)[:120])
        try:
            return chat_cloud(agent_id, task, timeout_cloud)
        except Exception as e:
            print("[cloud FAIL -> fallback ollama]", str(e)[:120])
    elif bk == "cloud":
        try:
            return chat_cloud(agent_id, task, timeout_cloud)
        except Exception as e:
            print("[cloud FAIL -> fallback ollama]", str(e)[:120])
    return chat(agent_id, task, timeout_local)


def run(label, agent_id, out_path, prompt, validator=None, json_out=False):
    """生成→落盘→校验。返回 (ok, detail)。"""
    task = {"label": label, "prompt": prompt, "json_out": json_out}
    raw = chat_auto(agent_id, task)
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
