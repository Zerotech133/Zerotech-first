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
import ast
import json
from dataclasses import dataclass
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
# K-ORG-038: 包年 Coding Plan 走 Anthropic 兼容端点——额度与 API 钱包分开计费
# （判例：chat/completions 报 1113 余额不足 ≠ 套餐耗尽），纯 HTTP 无 spawn。
ANTHROPIC_URL = "https://open.bigmodel.cn/api/anthropic/v1/messages"
ANTHROPIC_MODEL = os.environ.get("CODER_ANTHROPIC_MODEL", "glm-4.6")
# K-ORG-039: GPT-6.0（老田自备密钥·OpenAI 兼容端点）——密钥存
# config/model_keys.local.json（gitignored）或环境变量 GPT6_API_KEY。
GPT6_URL = os.environ.get("GPT6_URL", "https://itokengate.com/v1/chat/completions")
GPT6_MODEL = os.environ.get("GPT6_MODEL", "gpt-6-sol")


def _gpt6_key():
    key = os.environ.get("GPT6_API_KEY", "")
    if key:
        return key
    try:
        conf = json.load(open("config/model_keys.local.json", encoding="utf-8"))
        return conf.get("gpt6", {}).get("api_key", "")
    except (OSError, ValueError):
        return ""


def chat_openai(agent_id, task, timeout=180):
    """GPT-6.0 自定义 OpenAI 兼容端点（K-ORG-039·实测 10~19s/轮）。
    探测判据：tool_call 正确出参、编码探针一次写对；
    content 兼容字符串与块数组两种回形。"""
    key = _gpt6_key()
    if not key:
        raise RuntimeError("GPT6 key 未配置(env GPT6_API_KEY 或 config/model_keys.local.json)")
    sys_prompt = BUNDLE["agents"][agent_id]["prompt"]
    payload = {
        "model": GPT6_MODEL,
        "max_tokens": task.get("max_tokens", 8192),
        "messages": [{"role": "system", "content": sys_prompt},
                     {"role": "user", "content": task["prompt"]}],
    }
    req = urllib.request.Request(GPT6_URL, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + key})
    t0 = time.time()
    d = json.load(OP.open(req, timeout=timeout))
    msg = (d.get("choices") or [{}])[0].get("message", {})
    c = msg.get("content")
    if isinstance(c, list):  # 块数组变体（reasoning 网关常见）
        out = "".join(b.get("text", "") for b in c
                      if isinstance(b, dict) and b.get("type") == "text").strip()
    else:
        out = (c or "").strip()
    if not out:
        raise RuntimeError("gpt6 空回(finish=%s)"
                           % (d.get("choices") or [{}])[0].get("finish_reason"))
    print("[gpt6:%s %s %.1fs] %d chars"
          % (agent_id, task.get("label", ""), time.time() - t0, len(out)))
    return out


def chat_anthropic(agent_id, task, timeout=240):
    """智谱包年(Coding Plan) Anthropic 兼容端点生成——GLM-4.6 档。
    ⚠️ 判例：glm-4.6 是思考型，content 为 [thinking, text] 块数组——
    正文只拼 type=text 块；max_tokens 须给足（64 级会被 thinking 吞成空回，
    stop=max_tokens 表面成功）。"""
    key = os.environ.get("GLM_API_KEY", "")
    if not key:
        raise RuntimeError("GLM_API_KEY 未设置")
    sys_prompt = BUNDLE["agents"][agent_id]["prompt"]
    payload = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": task.get("max_tokens", 32768),  # 038判例: thinking耗额·8192级被截断(stop=max_tokens)
        "system": sys_prompt,
        "messages": [{"role": "user", "content": task["prompt"]}],
    }
    req = urllib.request.Request(ANTHROPIC_URL, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json",
                                          "x-api-key": key,
                                          "anthropic-version": "2023-06-01"})
    t0 = time.time()
    d = json.load(OP.open(req, timeout=timeout))
    out = "".join(b.get("text", "") for b in d.get("content", [])
                  if b.get("type") == "text").strip()
    if not out:
        raise RuntimeError("anthropic 空回(thinking 吞 content? stop=%s)"
                           % d.get("stop_reason"))
    print("[anthropic:%s %s %.1fs] %d chars (stop=%s)"
          % (agent_id, task.get("label", ""), time.time() - t0, len(out),
             d.get("stop_reason")))
    return out


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


# ---------- F3: 编辑协议 + lint 门（K-ORG-035 批F·借 Aider/SWE-agent 范式） ----------

import re

SR_RE = re.compile(
    r"<<<<<<< SEARCH\r?\n(.*?)\r?\n?=======\r?\n(.*?)\r?\n?>>>>>>> REPLACE\r?\n?",
    re.S)
FENCE_RE = re.compile(r"^```[a-zA-Z0-9_]*\r?\n|\r?\n```\s*$")


def strip_fences(text):
    """剥模型输出的 Markdown 代码围栏。
    🔴 K-ORG-038 判例：glm-4.6 爱先写说明文字再上围栏——原「仅剥首尾围栏标记」
    会把围栏前的散文一并留给 lint 门（line 1 全角字符）。
    有围栏时直接提取第一个开栏到最后一个闭栏之间的内容。"""
    m = re.search(r"```[a-zA-Z0-9_]*[ \t]*\r?\n", text)
    if m:
        start = m.end()
        end = text.rfind("```")
        if end > start:
            return text[start:end].strip("\n").strip()
    return FENCE_RE.sub("", text.strip())


def lint_py_text(content, name="<target>"):
    """语法门：纯 compile 不落盘。返回 None=过 / 错误摘要。"""
    try:
        compile(content, name, "exec")
        return None
    except SyntaxError as e:
        return "line %d: %s" % (e.lineno, e.msg)


def _integrity_gate(content, orig_bytes, out_path):
    """K-ORG-044 完整性闸门：拦截截水特征/函数丢失。
    043 判例：whole 模式整文件截水 4.5KB→845c 丢 main，冒烟只测
    目标行为 → 残缺文件假 PASS。返回错误串；None=过；
    新文件（orig_bytes 空）不查。"""
    if orig_bytes is None:
        return None
    orig = orig_bytes.decode("utf-8", errors="replace")
    if len(content) < len(orig) * 0.8:
        return ("完整性FAIL: 产出%d字 < 原文%d字*0.8(截水特征)"
                % (len(content), len(orig)))
    if out_path.endswith(".py"):
        def _top_defs(text):
            # 优先 ast 顶层节点计数，解析失败按工单规格正则兜底
            try:
                return sum(1 for n in ast.parse(text).body
                           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                                             ast.ClassDef)))
            except SyntaxError:
                return len(re.findall(r"(?m)^(?:async\s+def|def|class)\s", text))
        n_new, n_old = _top_defs(content), _top_defs(orig)
        if n_new < n_old:
            return ("完整性FAIL: 顶层定义%d个 < 原文%d个(函数丢失)"
                    % (n_new, n_old))
    return None


TRACE_DIR = os.path.join("shadow", "coder_traces")


def trace_log(k_id, kind, detail):
    """F5: 编码轨迹落盘（OpenHands 事件流范式——Action/Observation 对按单 jsonl，
    出问题可回放定位）。kind: generate/edit/lint/smoke/write/commit/fail。"""
    if not k_id:
        return
    d = os.path.join(TRACE_DIR, time.strftime("%Y-%m-%d"))
    os.makedirs(d, exist_ok=True)
    fn = re.sub(r"[^\w.-]", "_", str(k_id)) + ".jsonl"
    rec = {"ts": time.strftime("%H:%M:%S"), "k_id": k_id,
           "kind": kind, "detail": str(detail)[:300]}
    with open(os.path.join(d, fn), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def apply_edit_protocol(raw, target_path):
    """SEARCH/REPLACE 块协议（Aider 范式）：块内 SEARCH 必须在目标文件唯一命中，
    逐块替换。返回 (content, mode)：
      'whole'   无块 → 整文件重写（剥围栏）
      'sr'      块全命中 → 应用后的完整内容
      'sr_fail' 有块但存在未唯一命中 → content=None（调用方带错重试，禁止落盘）
    ⚠️ 判例：块命中失败时若兜底整文件写回，会把协议标记原文写进目标（垃圾落盘）。"""
    body = strip_fences(raw)
    blocks = SR_RE.findall(body)
    if not blocks:
        return body, "whole"
    try:
        cur = open(target_path, encoding="utf-8").read()
    except FileNotFoundError:
        return body, "whole"
    for i, (old, new) in enumerate(blocks):
        n = cur.count(old)  # 唯一性断言（判例：多命中=伪随机替换事故源）
        if n != 1:
            # SWE-agent「错误结构化」：失败必须可定位——哪个块、命中几次、SEARCH 原文
            diag = ("sr_fail: 第%d/%d块未唯一命中(hit=%d) SEARCH前80字=%r"
                    % (i + 1, len(blocks), n, old[:80]))
            print("  [edit-protocol]", diag)
            return None, diag
        cur = cur.replace(old, new, 1)
    return cur, "sr"


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


# ---------- K-ORG-048: chat_auto 通道元数据（043/045 判例：降级链后实际通道丢失） ----------

@dataclass
class ChatResult:
    content: str
    channel: str      # 实际生成通道（降级后最终档）
    requested: str    # backend() 请求档
    attempts: list    # [(通道, err)] 失败链
    latency_s: float


_LAST_META = None  # 最近一次 chat_auto_meta 结果（非线程安全）


def _chat_auto_impl(agent_id, task, timeout_cloud=240, timeout_local=420, timeout_claude=600):
    """按 CODER_BACKEND 路由；逐级降级。
    gpt6(GPT-6.0·老田密钥) | anthropic(包年GLM-4.6·纯API) | claude_glm(CLI·blocked)
    | cloud | ollama。gpt6/anthropic 失败均降级 cloud→ollama。
    K-ORG-048: 返回 ChatResult——channel=实际成功档，requested=bk，attempts=失败链。"""
    global _LAST_META
    bk = backend()
    _t0 = time.time()
    attempts = []
    if bk == "gpt6":
        try:
            out = chat_openai(agent_id, task)
            _LAST_META = ChatResult(out, "gpt6", bk, attempts,
                                    round(time.time() - _t0, 1))
            return _LAST_META
        except Exception as e:
            print("[gpt6 FAIL -> fallback anthropic]", str(e)[:120])
            attempts.append(("gpt6", str(e)[:120]))
        try:
            out = chat_anthropic(agent_id, task, timeout_cloud)
            _LAST_META = ChatResult(out, "anthropic", bk, attempts,
                                    round(time.time() - _t0, 1))
            return _LAST_META
        except Exception as e:
            print("[anthropic FAIL -> fallback cloud]", str(e)[:120])
            attempts.append(("anthropic", str(e)[:120]))
        try:
            out = chat_cloud(agent_id, task, timeout_cloud)
            _LAST_META = ChatResult(out, "cloud", bk, attempts,
                                    round(time.time() - _t0, 1))
            return _LAST_META
        except Exception as e:
            print("[cloud FAIL -> fallback ollama]", str(e)[:120])
            attempts.append(("cloud", str(e)[:120]))
    elif bk == "anthropic":
        try:
            out = chat_anthropic(agent_id, task, timeout_cloud)
            _LAST_META = ChatResult(out, "anthropic", bk, attempts,
                                    round(time.time() - _t0, 1))
            return _LAST_META
        except Exception as e:
            print("[anthropic FAIL -> fallback cloud]", str(e)[:120])
            attempts.append(("anthropic", str(e)[:120]))
        try:
            out = chat_cloud(agent_id, task, timeout_cloud)
            _LAST_META = ChatResult(out, "cloud", bk, attempts,
                                    round(time.time() - _t0, 1))
            return _LAST_META
        except Exception as e:
            print("[cloud FAIL -> fallback ollama]", str(e)[:120])
            attempts.append(("cloud", str(e)[:120]))
    elif bk == "claude_glm":
        try:
            out = chat_claude(agent_id, task, timeout_claude)
            _LAST_META = ChatResult(out, "claude_glm", bk, attempts,
                                    round(time.time() - _t0, 1))
            return _LAST_META
        except Exception as e:
            print("[claude_glm FAIL -> fallback cloud]", str(e)[:120])
            attempts.append(("claude_glm", str(e)[:120]))
        try:
            out = chat_cloud(agent_id, task, timeout_cloud)
            _LAST_META = ChatResult(out, "cloud", bk, attempts,
                                    round(time.time() - _t0, 1))
            return _LAST_META
        except Exception as e:
            print("[cloud FAIL -> fallback ollama]", str(e)[:120])
            attempts.append(("cloud", str(e)[:120]))
    elif bk == "cloud":
        try:
            out = chat_cloud(agent_id, task, timeout_cloud)
            _LAST_META = ChatResult(out, "cloud", bk, attempts,
                                    round(time.time() - _t0, 1))
            return _LAST_META
        except Exception as e:
            print("[cloud FAIL -> fallback ollama]", str(e)[:120])
            attempts.append(("cloud", str(e)[:120]))
    out = chat(agent_id, task, timeout_local)
    _LAST_META = ChatResult(out, "ollama", bk, attempts,
                            round(time.time() - _t0, 1))
    return _LAST_META


def chat_auto(agent_id, task, timeout_cloud=240, timeout_local=420, timeout_claude=600):
    """K-ORG-048 兼容薄壳：只回 content，旧调用方零改动。"""
    return _chat_auto_impl(agent_id, task, timeout_cloud,
                           timeout_local, timeout_claude).content


def chat_auto_meta(agent_id, task, timeout_cloud=240, timeout_local=420, timeout_claude=600):
    """K-ORG-048: 返回完整 ChatResult（content/channel/requested/attempts/latency_s）。"""
    return _chat_auto_impl(agent_id, task, timeout_cloud,
                           timeout_local, timeout_claude)


def last_channel():
    """K-ORG-048: 最近一次实际生成通道；无记录回退请求档。"""
    return _LAST_META.channel if _LAST_META else backend()


def run(label, agent_id, out_path, prompt, validator=None, json_out=False,
        lint_retries=2, smoke=None, k_id=None, smoke_retries=2):
    """生成→编辑协议→lint门→(带错重试)→落盘→校验→冒烟→轨迹。返回 (ok, detail)。
    smoke: 可选 callable(out_path)，落盘后执行；抛异常=FAIL，会还原原文后
    带错误反馈重新生成（最多 smoke_retries 轮）——F4 迭代回路完整形态。
    k_id: 工单号，轨迹按单归档。"""
    task = {"label": label, "prompt": prompt, "json_out": json_out}
    orig_bytes = None
    if os.path.exists(out_path):
        orig_bytes = open(out_path, "rb").read()

    def _restore():
        if orig_bytes is not None:
            with open(out_path, "wb") as f:
                f.write(orig_bytes)

    meta = chat_auto_meta(agent_id, task)
    raw = meta.content
    trace_log(k_id, "generate", "%s by %s via %s(requested=%s actual=%s)"
              % (label, agent_id, backend(), meta.requested, meta.channel))
    if json_out:
        try:
            data = json.loads(raw)
            content = json.dumps(data, ensure_ascii=False, indent=1)
        except Exception as e:
            return False, "JSON 解析失败: %s | raw[:120]=%r" % (e, raw[:120])
        if validator:
            ok, detail = validator(content)
            if not ok:
                return False, detail
        with open(out_path, "wb") as f:
            f.write(content.replace("\r\n", "\n").encode("utf-8"))
        print("  -> written", out_path, len(content), "chars")
        trace_log(k_id, "write", "%s %d chars" % (out_path, len(content)))
        return True, "ok"

    sr_err = "SEARCH/REPLACE 块未在目标文件唯一命中"
    for attempt in range(smoke_retries + 1):
        content, mode = apply_edit_protocol(raw, out_path)
        print("  [edit-protocol] mode=%s" % mode)
        trace_log(k_id, "edit", "mode=%s" % mode)
        if mode.startswith("sr_fail"):
            err = mode  # 带结构化诊断
        elif out_path.endswith(".py"):
            err = lint_py_text(content, out_path)
        else:
            err = None
        if not err:  # K-ORG-044: 完整性闸在 lint 过后、写盘前，与语法错同路
            err = _integrity_gate(content, orig_bytes, out_path)
        tries = 0
        while err and tries < lint_retries:
            kind = "integrity" if err.startswith("完整性") else "lint"  # K-ORG-044: 按闸门定 trace
            print("  [%s-gate] 第%d轮拦截: %s → 带错重试" % (kind, tries + 1, err))
            trace_log(k_id, kind, "round%d blocked: %s" % (tries + 1, err))
            task["prompt"] = (prompt +
                "\n\n【上一次产出未通过验证门，错误：%s】"
                "\n请重新输出修正后的完整文件内容（或修正的 SEARCH/REPLACE 块）。" % err)
            meta = chat_auto_meta(agent_id, task)
            raw = meta.content
            content, mode = apply_edit_protocol(raw, out_path)
            if mode.startswith("sr_fail"):
                err = mode  # 带结构化诊断（块序号/命中数/SEARCH原文）喂回
            elif content is None:
                err = "产出为空"
            elif out_path.endswith(".py"):
                err = lint_py_text(content, out_path)
            else:
                err = None
            if not err:  # K-ORG-044: 重试产出同样过完整性闸
                err = _integrity_gate(content, orig_bytes, out_path)
            tries += 1
        if err:
            trace_log(k_id, "fail", "gate %d rounds: %s" % (lint_retries, err))
            return False, "验证门 %d 轮未过: %s" % (lint_retries, err)
        print("  [lint-gate] PASS")
        trace_log(k_id, "lint", "PASS")
        if validator:
            ok, detail = validator(content)
            if not ok:
                return False, detail
        with open(out_path, "wb") as f:
            f.write(content.replace("\r\n", "\n").encode("utf-8"))
        print("  -> written", out_path, len(content), "chars")
        trace_log(k_id, "write", "%s %d chars" % (out_path, len(content)))
        if not smoke:
            return True, "ok"
        try:
            smoke(out_path)
            print("  [smoke] PASS")
            trace_log(k_id, "smoke", "PASS")
            return True, "ok"
        except Exception as e:
            sm = str(e)[:150]
            trace_log(k_id, "smoke", "round%d fail: %s" % (attempt + 1, sm))
            if attempt >= smoke_retries:
                trace_log(k_id, "fail",
                          "smoke %d rounds: %s" % (smoke_retries + 1, sm))
                _restore()  # K-ORG-044: 坏产出零残留（044首跑实录修复）
                return False, "smoke FAIL %d 轮: %s" % (smoke_retries + 1, sm)
            print("  [smoke] 第%d轮拦截: %s → 还原原文带错重试" % (attempt + 1, sm))
            _restore()
            task["prompt"] = (prompt +
                "\n\n【上一次产出通过了语法门但未通过行为冒烟，错误：%s】"
                "\n禁止整文件重写式重做——只允许输出 SEARCH/REPLACE 块做最小修改，"
                "\n现有函数实现体一字不动，禁止 pass/... 占位。" % sm)
            meta = chat_auto_meta(agent_id, task)
            raw = meta.content
    return False, "unreachable"


def commit_work(out_path, k_id, agent_id, label, channel=None, dry_run=False):
    """F4: 按单自动提交（借 Aider auto-commit 范式）。
    只 add 目标文件（禁 -A/禁 .），独立 commit 可按单回滚。
    消息格式：K编号: 工单名 — by 岗位 via 通道。返回 (ok, detail)。"""
    import subprocess as sp
    if channel in (None, "auto"):  # K-ORG-048: 缺省=实际通道(last_channel)；显式传值仍覆盖
        channel = last_channel()
    msg = "%s: %s — by %s via %s" % (k_id, label, agent_id, channel)
    r = sp.run(["git", "add", "--", out_path], capture_output=True, text=True,
               encoding="utf-8", errors="replace")
    if r.returncode != 0:
        trace_log(k_id, "fail", "git add: " + (r.stderr or r.stdout or "")[:100])
        return False, "git add: " + (r.stderr or r.stdout or "")[:120]
    trace_log(k_id, "add", out_path)  # 037: add 成功也留痕（回放序列完整）
    cmd = ["git", "commit"]
    if dry_run:
        cmd.append("--dry-run")
    cmd += ["-m", msg, "--", out_path]
    r = sp.run(cmd, capture_output=True, text=True, encoding="utf-8",
               errors="replace")
    if r.returncode != 0:
        trace_log(k_id, "fail", "git commit: " + (r.stderr or r.stdout or "")[:120])
        return False, "git commit: " + (r.stderr or r.stdout or "")[:150]
    if not dry_run:
        sha = sp.run(["git", "rev-parse", "--short", "HEAD"],
                     capture_output=True, text=True).stdout.strip()
        print("  [commit] %s (%s)" % (sha, msg))
        trace_log(k_id, "commit", "%s %s" % (sha, msg))
        return True, sha
    print("  [commit] dry-run ok:", msg)
    return True, "dry-run"


if __name__ == "__main__":
    print("usage: 由 K-ORG-032 主脚本 import 调用")
