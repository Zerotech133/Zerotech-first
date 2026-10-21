# -*- coding: utf-8 -*-
"""阚泽部 · 诸葛亮咨询位（K6，WO-FS-148-10）。

一句话：客户问「为什么/该怎么办」这类管理咨询问题，转诸葛亮（军师）作答。

通道三级（priority 从高到低）
---------------------------
1. **openclaw_gpt**：OpenClaw CLI + GPT6.0（老田 10-21 10:51 裁决的异族编制）。
   前置：OPENAI_API_KEY 已注入 + openclaw 已配置（`~/.openclaw/config.json`）。
   调用方式：子进程 `openclaw --print "<prompt>"`，超时上限 DEFAULT_TIMEOUT。
2. **local_soul**：本地 qwen3.5:9b + 诸葛亮 SOUL prompt（军师人格，非真 GPT6.0）。
   **诚实标注**：返回 mode=local_soul 且 reply meta 会标「本地军师代理」，
   不冒充 OpenAI 通道。
3. 都没戏 → 返回 text=None，由调用方回落澄清卡（不猜、不硬编答案）。

开关
----
`COMM_ZHUGE_ENABLED` 默认 0：关时 consult() 直接返 disabled，零副作用。

不做
----
- 不把客户原文写盘（本模块不落任何文件；留痕仅在上层 route_log 存指纹）。
- 不在 key 缺失时尝试真通道（省掉一次必败的子进程与可能的错误计费）。
- 不让咨询位处理投诉/涉资金问题（那是田丰归口与人工兜底的事）。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

DEFAULT_TIMEOUT = 30
DEFAULT_MAX_CHARS = 600
OPENCLAW_TIMEOUT = 45

# 诸葛亮 SOUL（精简自 deliverables/s20-zhuge-workspace-20261021/SOUL.md）
ZHUGE_SOUL = (
    "你是诸葛亮，零界数字军团的军师（AI1）。面对咨询时：\n"
    "1. 结论先行：第一句给判断，再给依据；\n"
    "2. 只基于用户给出的事实与常识推理，不编造数字、不虚构数据；\n"
    "3. 涉及交易执行、资金、签约等实际动作时，明确说「此事须走审批，我不代决」；\n"
    "4. 不承诺收益，不给具体买卖指令，只做判断与路径分析；\n"
    "5. 语言简练，3~6 句内说清，不写客套话。"
)


def enabled():
    """咨询位开关：env 优先，comm_flags.json 兜底（WO-FS-152）。"""
    try:
        import comm_flags
        return comm_flags.get("zhuge")
    except Exception:  # noqa: BLE001
        return str(os.environ.get("COMM_ZHUGE_ENABLED", "0")).strip().lower() \
            in ("1", "true", "yes", "on")


def _openclaw_bin():
    """→ 可执行路径或 None（不猜路径，靠 PATH 查找）。"""
    for name in ("openclaw", "openclaw.cmd"):
        p = shutil.which(name)
        if p:
            return p
    return None


def _openclaw_configured():
    return (Path.home() / ".openclaw" / "config.json").exists()


def _openclaw_available():
    """→ (ready, reason)。三条件全满足才走真通道。"""
    if not _openclaw_bin():
        return False, "openclaw_bin_missing"
    if not os.environ.get("OPENAI_API_KEY"):
        return False, "openai_key_missing"
    if not _openclaw_configured():
        return False, "openclaw_config_missing"
    return True, None


def channel_status():
    b = _openclaw_bin()
    ready, reason = _openclaw_available()
    return {"openclaw_bin": b, "openclaw_ready": ready,
            "reason": reason,
            "key_set": bool(os.environ.get("OPENAI_API_KEY")),
            "configured": _openclaw_configured()}


def _run_openclaw(question, timeout=OPENCLAW_TIMEOUT):
    """子进程调 CLI。→ (text, error)。绝不抛（调用方靠 error 判降级）。"""
    exe = _openclaw_bin()
    if not exe:
        return None, "bin_missing"
    try:
        cp = subprocess.run(
            [exe, "--print", question],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout,
            env={**os.environ})
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except OSError as ex:
        return None, "%s: %s" % (type(ex).__name__, ex)
    if cp.returncode != 0:
        return None, "exit=%d: %s" % (cp.returncode,
                                      (cp.stderr or "")[-160:])
    return (cp.stdout or "").strip(), None


def _default_llm():
    try:
        root = Path(__file__).resolve().parent.parent
        ap = root / "agent_platform"
        if str(ap) not in os.sys.path:
            os.sys.path.insert(0, str(ap))
        from engine.llm import OllamaLLM  # noqa: E402
        return OllamaLLM(timeout=float(os.environ.get(
            "COMM_ZHUGE_LOCAL_TIMEOUT", "25"))), None
    except Exception as ex:  # noqa: BLE001
        return None, "%s: %s" % (type(ex).__name__, ex)


def _trim(text, max_chars):
    t = (text or "").strip()
    t = re.sub(r"^#+\s*", "", t)
    if len(t) <= max_chars:
        return t
    cut = t[:max_chars]
    for sep in ("。", "；", ".", "\n"):
        i = cut.rfind(sep)
        if i >= max_chars * 0.6:
            return cut[:i + 1]
    return cut


def consult(question, open_id="", llm=None, timeout=DEFAULT_TIMEOUT,
            max_chars=DEFAULT_MAX_CHARS):
    """→ {text, mode, llm_ok, error}。text=None 表示两条通道都不可用。"""
    if not enabled():
        return {"text": None, "mode": None, "llm_ok": False,
                "error": "disabled"}
    q = (question or "").strip()
    if not q:
        return {"text": None, "mode": None, "llm_ok": False,
                "error": "empty_question"}

    t0 = time.time()
    # ① 真通道
    ready, why = _openclaw_available()
    oc_err = None
    if ready:
        raw, oc_err = _run_openclaw(q, timeout=min(timeout, OPENCLAW_TIMEOUT))
        if raw:
            return {"text": _trim(raw, max_chars), "mode": "openclaw_gpt",
                    "llm_ok": True, "error": None,
                    "elapsed_ms": int((time.time() - t0) * 1000)}
        # 真通道失败 → 继续降级（错误进 error 字段，不吞）
    # ② 本地军师代理
    if llm is None:
        llm, lerr = _default_llm()
        if llm is None:
            return {"text": None, "mode": None, "llm_ok": False,
                    "error": "all_channels_down: %s" % (
                        oc_err or why or lerr),
                    "elapsed_ms": int((time.time() - t0) * 1000)}
    try:
        raw, status = llm.chat(ZHUGE_SOUL, q, model="qwen3.5:9b")
    except Exception as ex:  # noqa: BLE001 —— 降级必须留因
        return {"text": None, "mode": None, "llm_ok": False,
                "error": "%s: %s" % (type(ex).__name__, str(ex)[:100]),
                "elapsed_ms": int((time.time() - t0) * 1000)}
    out = _trim(raw, max_chars)
    if not out:
        return {"text": None, "mode": None, "llm_ok": False,
                "error": "empty_output",
                "elapsed_ms": int((time.time() - t0) * 1000)}
    err = None
    if oc_err:
        err = "openclaw_fail: %s" % oc_err
    elif not ready:
        err = "local_fallback: %s" % why
    return {"text": out, "mode": "local_soul",
            "llm_ok": status == "normal", "error": err,
            "llm_status": status,
            "elapsed_ms": int((time.time() - t0) * 1000)}
