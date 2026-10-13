"""本地 Ollama 与 8899 聊天服务的只读 smoke 检查。"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def _content_type(response: Any) -> str:
    headers = getattr(response, "headers", None)
    if headers is None:
        return ""
    getter = getattr(headers, "get", None)
    return str(getter("Content-Type", "") if getter else "")


def _fetch(url: str, payload: dict[str, Any] | None = None, timeout: float = 8.0) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = Request(
        url,
        data=data,
        method="POST" if payload is not None else "GET",
        headers={"Content-Type": "application/json"} if payload is not None else {},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return {
                "ok": int(getattr(response, "status", 200)) < 400,
                "status": int(getattr(response, "status", 200)),
                "content_type": _content_type(response),
                "body": response.read(),
            }
    except HTTPError as exc:
        return {"ok": False, "status": int(exc.code), "error": f"http_{exc.code}", "body": b""}
    except (OSError, URLError, TimeoutError) as exc:
        return {"ok": False, "status": None, "error": type(exc).__name__, "body": b""}


def _failure(fetch_result: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": False,
        "status": fetch_result.get("status"),
        "error": fetch_result.get("error", "http_error"),
    }


def _ndjson_summary(body: bytes) -> dict[str, Any]:
    first_token = False
    sources = False
    done = False
    malformed = 0
    for raw in body.splitlines():
        if not raw.strip():
            continue
        try:
            record = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            malformed += 1
            continue
        if isinstance(record, dict):
            message = record.get("message")
            if isinstance(message, dict) and isinstance(message.get("content"), str) and message["content"]:
                first_token = True
            if isinstance(record.get("response"), str) and record["response"]:
                first_token = True
            sources = sources or "sources" in record
            done = done or bool(record.get("done"))
    return {"first_token": first_token, "sources": sources, "done": done, "malformed_lines": malformed}


def run_smoke(
    base_url: str = "http://127.0.0.1:11434",
    web_url: str = "http://127.0.0.1:8899",
    timeout: float = 8.0,
) -> dict[str, Any]:
    """执行五项本地检查，返回不含回答正文的机器可读结果。"""
    base_url, web_url = base_url.rstrip("/"), web_url.rstrip("/")
    checks: dict[str, Any] = {}

    tags = _fetch(f"{base_url}/api/tags", timeout=timeout)
    if not tags.get("ok"):
        checks["ollama_tags"] = _failure(tags)
    else:
        try:
            payload = json.loads(tags["body"].decode("utf-8"))
            models = payload.get("models", []) if isinstance(payload, dict) else []
            names = [item.get("name") for item in models if isinstance(item, dict) and item.get("name")]
            checks["ollama_tags"] = {"ok": True, "status": tags.get("status"), "model_count": len(names), "models": names}
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError, TypeError):
            checks["ollama_tags"] = {"ok": False, "status": tags.get("status"), "error": "invalid_json"}

    page = _fetch(f"{web_url}/", timeout=timeout)
    checks["web_page"] = (
        {"ok": True, "status": page.get("status"), "content_type": page.get("content_type")}
        if page.get("ok") else _failure(page)
    )

    assets: dict[str, Any] = {}
    for asset in ("tokens.css", "nav.js"):
        result = _fetch(f"{web_url}/static/{asset}", timeout=timeout)
        assets[asset] = (
            {"ok": True, "status": result.get("status"), "content_type": result.get("content_type")}
            if result.get("ok") else _failure(result)
        )
    checks["static_assets"] = {"ok": all(item["ok"] for item in assets.values()), "assets": assets}

    chat = _fetch(
        f"{web_url}/api/chat",
        {"model": "qwen3.5:9b", "message": "smoke", "history": [], "think": False, "keep_alive": "30m"},
        timeout=timeout,
    )
    chat_summary = _ndjson_summary(chat.get("body", b"")) if chat.get("ok") else _failure(chat)
    if chat.get("ok"):
        chat_summary["ok"] = bool(chat_summary.get("first_token") and chat_summary.get("done"))
        chat_summary["status"] = chat.get("status")
    checks["chat"] = chat_summary

    rag = _fetch(f"{web_url}/api/rag", {"message": "smoke", "top_k": 1}, timeout=timeout)
    rag_summary = _ndjson_summary(rag.get("body", b"")) if rag.get("ok") else _failure(rag)
    if rag.get("ok"):
        rag_summary["ok"] = bool(rag_summary.get("sources") and rag_summary.get("done"))
        rag_summary["status"] = rag.get("status")
    checks["rag"] = rag_summary
    return {"ok": all(bool(item.get("ok")) for item in checks.values()), "checks": checks}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="检查本地 Ollama 与 8899 AI 聊天服务")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--web-url", default="http://127.0.0.1:8899")
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    result = run_smoke(args.base_url, args.web_url, args.timeout)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
