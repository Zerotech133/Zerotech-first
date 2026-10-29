# -*- coding: utf-8 -*-
"""阚泽部 · 出向智能摘要（K5.2，WO-FS-148-8）。

一句话：把「模板堆字段」的日报/预警卡，变成人话说的一段。

设计
----
- 输入：结构化事实（标题 + 若干条目/字段），输出：人话摘要。
- 模型：默认本地 qwen3.5:9b（零成本），走 engine/llm.py（自动档位+降级三态）。
- **模板兜底**：模型不可用/超时/输出不合格 → 返回原文（截断到上限）并
  标 llm_ok=False；调用方据此在卡片上标注「模板」而不是假装 AI 生成。
- 长度：输出上限 max_chars（默认 220），超长按句截断。
- 缓存：同 body 指纹 + 同 prompt 版本在进程内短期缓存（默认 10 分钟），
  避免日报/预警重复渲染时反复打模型。

不做
----
- 不改账单/额度/交易口径数字（数字一律来自结构化输入，模型只做语言组织）；
- 不做「模型自己算」的事——摘要文本里的数字必须能在输入里找到，
  否则丢弃该句（防模型编造，见 _numbers_ok）。
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from pathlib import Path

PROMPT_VERSION = "v1"
DEFAULT_MAX_CHARS = 220
CACHE_TTL_SEC = 600

_CACHE = {}  # {(body_md5, kind): (ts, result)}

_SYSTEM = (
    "你是飞书推送卡片的中文编辑。把结构化事实写成 1~3 句人话摘要，"
    "只陈述输入里出现的事实与数字，不推测、不评价、不加建议。"
    "不要 Markdown 标题，不要列表符号，不要前后缀客套。"
    "数字必须与输入完全一致，禁止四舍五入出新数字。"
)


def enabled():
    """摘要开关：env 优先，comm_flags.json 兜底（WO-FS-152，默认开）。"""
    try:
        import comm_flags

        return comm_flags.get("summary")
    except Exception:  # noqa: BLE001
        return str(os.environ.get("COMM_SUMMARY_ENABLED", "1")).strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )


def _default_llm():
    try:
        root = Path(__file__).resolve().parent.parent
        ap = root / "agent_platform"
        if str(ap) not in os.sys.path:
            os.sys.path.insert(0, str(ap))
        from engine.llm import OllamaLLM  # noqa: E402

        return OllamaLLM(
            timeout=float(os.environ.get("COMM_SUMMARY_TIMEOUT", "20"))
        ), None
    except Exception as ex:  # noqa: BLE001
        return None, "%s: %s" % (type(ex).__name__, ex)


def _md5(s):
    return hashlib.md5((s or "").encode("utf-8", "replace"), usedforsecurity=False).hexdigest()


def _nums(s):
    return set(re.findall(r"\d+(?:\.\d+)?", s or ""))


def _numbers_ok(out_text, source_text):
    """摘要里出现的数字必须都在输入里（防模型编造）。"""
    return _nums(out_text) <= _nums(source_text)


def _trim(text, max_chars):
    t = (text or "").strip().replace("\n", " ")
    if len(t) <= max_chars:
        return t
    cut = t[:max_chars]
    # 尽量在句末断开
    for sep in ("。", "；", ".", "，", " "):
        i = cut.rfind(sep)
        if i >= max_chars * 0.6:
            return cut[: i + (1 if sep in "。；." else 0)]
    return cut


def _build_user(title, items, kind):
    lines = ["类型：%s" % kind, "标题：%s" % (title or "")]
    for it in items or []:
        lines.append("- %s" % (it,))
    return "\n".join(lines)


def humanize(title, items, kind="daily", llm=None, max_chars=None, use_cache=True):
    """→ {text, llm_ok, tier, error, cached}

    llm_ok=False 时 text 为模板原文（调用方在卡片标注「模板」）。
    """
    mc = int(max_chars or os.environ.get("COMM_SUMMARY_MAX_CHARS", DEFAULT_MAX_CHARS))
    body = _build_user(title, items, kind)
    fallback = _trim(
        "%s：%s" % (title or kind, "；".join(str(i) for i in (items or []))), mc
    )

    if not enabled():
        return {
            "text": fallback,
            "llm_ok": False,
            "tier": "template",
            "error": "disabled",
            "cached": False,
        }

    key = (_md5(body + PROMPT_VERSION), kind)
    if use_cache:
        hit = _CACHE.get(key)
        if hit and time.time() - hit[0] < CACHE_TTL_SEC:
            r = dict(hit[1])
            r["cached"] = True
            return r

    if llm is None:
        llm, err = _default_llm()
        if llm is None:
            return {
                "text": fallback,
                "llm_ok": False,
                "tier": "template",
                "error": "llm_unavailable: %s" % err,
                "cached": False,
            }
    try:
        raw, status = llm.chat(_SYSTEM, body, model="qwen3.5:9b")
    except Exception as ex:  # noqa: BLE001
        return {
            "text": fallback,
            "llm_ok": False,
            "tier": "template",
            "error": "%s: %s" % (type(ex).__name__, str(ex)[:100]),
            "cached": False,
        }

    out = _trim(raw, mc)
    if not out or len(out) < 8:
        return {
            "text": fallback,
            "llm_ok": False,
            "tier": "template",
            "error": "output_too_short",
            "cached": False,
        }
    if not _numbers_ok(out, body):
        # 出现输入里没有的数字 → 判定编造，回模板（不冒险展示）
        return {
            "text": fallback,
            "llm_ok": False,
            "tier": "template",
            "error": "number_fabrication_guard",
            "cached": False,
        }

    res = {
        "text": out,
        "llm_ok": True,
        "tier": "fast" if status == "normal" else "degraded",
        "error": None if status == "normal" else "llm_status=%s" % status,
        "cached": False,
    }
    if use_cache:
        _CACHE[key] = (time.time(), dict(res))
    return res


def clear_cache():
    _CACHE.clear()
