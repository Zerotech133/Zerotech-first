# -*- coding: utf-8 -*-
"""DeepSeek 云端推理客户端（工具级 llm-deepseek · WO-LEGION-LLMDEEPSEEK-20261026）。

定位：给治理工具/引擎提供**可调用、可测、不泄密钥**的 DeepSeek 客户端；默认模型
``deepseek-flash``（快/省，适合扫描归纳），重任务可显式传 ``deepseek-v4-pro``。
**不改 Hermes profile、不改任何常驻服务配置**（工具级接入，可逆）。

密钥纪律（与项目凭据红线一致）：
  · 优先 ``DEEPSEEK_API_KEY`` 环境变量；否则读 ``DEEPSEEK_API_KEY_FILE``；
    再否则读默认私有文件 ``<workspace>/_vault/secrets/deepseek_api_key.txt``；
  · 私钥/密钥**永不入库、不进日志、不回显**；异常信息统一脱敏（``sk-****``）；
  · 本模块只读密钥，不写密钥、不生成密钥。

网络：仅标准库 urllib（无第三方依赖）；可用 ``transport`` 注入离线桩做单测。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional
from tools.safe_urlopen import safe_urlopen

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"
KNOWN_MODELS = ("deepseek-flash", "deepseek-v4-pro")

KEY_ENV = "DEEPSEEK_API_KEY"
KEY_FILE_ENV = "DEEPSEEK_API_KEY_FILE"

_REPO = Path(__file__).resolve().parents[2]  # <repo>/agent_platform/engine -> <repo>


def _find_default_key_file() -> Path:
    """向上搜索 `<workspace>/_vault/secrets/deepseek_api_key.txt`。

    生产布局：repo=<workspace>/老田的AI助手，workspace 即 repo 的父目录；
    worktree 布局：repo=<main repo>/.worktrees/<TID>，工作区根在更上层。
    二者统一按"最近的含 _vault 的祖先"解析，找不到退回 repo 父目录约定路径。
    """
    for base in (_REPO, *_REPO.parents):
        cand = base / "_vault" / "secrets" / "deepseek_api_key.txt"
        if cand.is_file():
            return cand
    return _REPO.parent / "_vault" / "secrets" / "deepseek_api_key.txt"


DEFAULT_KEY_FILE = _find_default_key_file()

_SK_RE = re.compile(r"sk-[A-Za-z0-9_\-]{6,}")


class DeepSeekError(RuntimeError):
    """DeepSeek 调用失败（消息已脱敏，不含密钥）。"""


def _sanitize(text: Any, limit: int = 300) -> str:
    """错误信息脱敏：去掉可能的密钥片段并截断。"""
    s = _SK_RE.sub("sk-****", str(text or ""))
    return s[:limit]


def resolve_api_key(
    env: Optional[dict] = None, explicit_file: Optional[str] = None
) -> str:
    """按 env → 显式文件 → KEY_FILE_ENV → 默认私有文件 解析密钥。"""
    env = os.environ if env is None else env
    key = (env.get(KEY_ENV) or "").strip()
    if key:
        return key
    path = Path(explicit_file or env.get(KEY_FILE_ENV) or DEFAULT_KEY_FILE)
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        raise DeepSeekError(
            "缺少 DeepSeek key：%s 未设置，且私有文件不可读（%s）。"
            "请把 key 放到私有区或设置环境变量。" % (KEY_ENV, path)
        ) from None
    if not raw:
        raise DeepSeekError("DeepSeek key 文件为空：%s" % path)
    return raw


def _http_request(
    method: str, url: str, headers: dict, data: Optional[bytes], timeout: float
) -> tuple[int, str]:
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with safe_urlopen(req, timeout=timeout) as r:
            return int(getattr(r, "status", 200)), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read().decode("utf-8", "replace")


class DeepSeekClient:
    """最小可用 DeepSeek 客户端（OpenAI 兼容）；transport 可注入做离线测试。"""

    def __init__(
        self,
        api_key=None,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 60.0,
        retries: int = 2,
        transport: Optional[Callable[..., tuple[int, str]]] = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ):
        self.api_key = api_key if api_key is not None else resolve_api_key()
        if not self.api_key:
            raise DeepSeekError("DeepSeek key 为空")
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = float(timeout)
        self.retries = max(0, int(retries))
        self._transport = transport or _http_request
        self._sleep = sleep_fn

    # ---------------- 内部 ----------------

    def _request(self, method: str, path: str, payload: Optional[dict] = None) -> dict:
        url = self.base_url + path
        data = (
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if payload is not None
            else None
        )
        headers = {
            "Authorization": "Bearer " + self.api_key,
            "Content-Type": "application/json",
        }
        last = ""
        for attempt in range(self.retries + 1):
            try:
                status, body = self._transport(method, url, headers, data, self.timeout)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last = "%s: %s" % (type(exc).__name__, exc)
                status, body = 0, ""
            if 200 <= status < 300:
                try:
                    return json.loads(body)
                except ValueError:
                    raise DeepSeekError(
                        "DeepSeek 响应非 JSON（status=%s）" % status
                    ) from None
            last = "HTTP %s: %s" % (status, _sanitize(body, 200))
            if status in (429, 500, 502, 503, 504) and attempt < self.retries:
                self._sleep(0.5 * (2**attempt))
                continue
            raise DeepSeekError("DeepSeek 调用失败（%s）" % last)
        raise DeepSeekError("DeepSeek 调用失败（重试耗尽：%s）" % last)

    # ---------------- 公共 API ----------------

    def chat_full(
        self,
        messages: list[dict],
        model: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 800,
        response_format: Optional[dict] = None,
    ) -> dict:
        """发一轮对话，返回 ``{content, usage, model, finish_reason}``。

        注意（LLMDEEPSEEKFIX2 实证）：deepseek 系列为**推理模型**，响应含
        ``reasoning_content``，其 token 与正文共用 ``max_tokens`` 预算；短回复场景
        预算过小会被推理吃光而正文为空。一句话级 ≥64，结构化抽取 ≥512。
        """
        payload: dict = {
            "model": model or self.model,
            "messages": list(messages),
            "temperature": float(temperature),
            "max_tokens": int(max_tokens),
            "stream": False,
        }
        if response_format:
            payload["response_format"] = response_format
        data = self._request("POST", "/chat/completions", payload)
        try:
            choice = data["choices"][0]
            content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise DeepSeekError(
                "DeepSeek 响应结构异常（缺 choices[0].message.content）"
            ) from None
        return {
            "content": str(content),
            "usage": data.get("usage") or {},
            "model": data.get("model") or (model or self.model),
            "finish_reason": choice.get("finish_reason"),
        }

    def chat(
        self,
        messages: list[dict],
        model: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 800,
        response_format: Optional[dict] = None,
    ) -> str:
        """发一轮对话，返回 assistant 正文（需要 usage 用 chat_full）。"""
        return self.chat_full(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
        )["content"]

    def models(self) -> list[str]:
        data = self._request("GET", "/models")
        rows = data.get("data") if isinstance(data, dict) else None
        return [
            str(r.get("id"))
            for r in (rows or [])
            if isinstance(r, dict) and r.get("id")
        ]

    def ping(self, max_tokens: int = 64) -> dict:
        """最小连通性自检：/models + 一次短 chat（成本可忽略）。

        判例（假成功）：HTTP 200 但正文为空 ≠ 可用——推理模型预算不足时正文会空。
        故默认 64 token，且空正文判 ``ok=False``（fail-visible，不冒充可用）。
        """
        ids = self.models()
        reply = self.chat(
            [{"role": "user", "content": "只回复 OK 两个字母"}],
            max_tokens=max(64, int(max_tokens)),
            temperature=0.0,
        )
        text = reply.strip()
        return {
            "ok": bool(text),
            "models": ids,
            "model": self.model,
            "reply": text[:20],
            "reason": ""
            if text
            else "empty_content（推理 token 可能占满预算；可调大 max_tokens）",
        }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="DeepSeek 工具级客户端（不打印密钥）")
    ap.add_argument(
        "--selftest", action="store_true", help="/models + 极短 chat 连通性自检"
    )
    ap.add_argument("--models", action="store_true", help="仅列模型")
    ap.add_argument("--chat", default="", help="发一条用户消息并打印回复")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        client = DeepSeekClient(model=args.model)
        rc = 0
        if args.models:
            out: Any = {"ok": True, "models": client.models()}
        elif args.selftest:
            out = client.ping()
            if not out.get("ok"):  # 空正文 = 不可用（假成功判例）
                rc = 1
        elif args.chat:
            out = {
                "ok": True,
                "reply": client.chat([{"role": "user", "content": args.chat}]),
            }
        else:
            ap.print_help()
            return 2
    except DeepSeekError as exc:
        print("[FAIL] %s" % _sanitize(exc), file=sys.stderr)
        return 1
    print(
        json.dumps(out, ensure_ascii=False, indent=2)
        if args.json
        else json.dumps(out, ensure_ascii=False)
    )
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
