# -*- coding: utf-8 -*-
"""Ollama 与本地 Web 聊天服务的健康检查。

该模块只负责探测，不会启动或停止任何服务，适合被启动脚本、晨检和监控复用。
"""

from __future__ import annotations

import argparse
import importlib
import json
import socket
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping


DEFAULT_BASE_URL = "http://127.0.0.1:11434"
try:
    DEFAULT_CHAT_MODEL = importlib.import_module(
        "AI.111111.model_routing"
    ).DEFAULT_CHAT_MODEL
except (ImportError, ModuleNotFoundError, AttributeError):
    # 直接从 ollama_service 目录运行时，仓库根目录可能不在 sys.path。
    DEFAULT_CHAT_MODEL = "qwen3.5:9b"
DEFAULT_EMBED_MODEL = "bge-m3:latest"
DEFAULT_WEB_CHAT_HOST = "127.0.0.1"
DEFAULT_WEB_CHAT_PORT = 8899


@dataclass(frozen=True)
class HealthCheckConfig:
    """健康检查目标配置。"""

    base_url: str = DEFAULT_BASE_URL
    chat_model: str = DEFAULT_CHAT_MODEL
    embed_model: str = DEFAULT_EMBED_MODEL
    web_chat_host: str = DEFAULT_WEB_CHAT_HOST
    web_chat_port: int = DEFAULT_WEB_CHAT_PORT
    timeout: float = 3.0
    port_timeout: float = 0.8

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_url", self.base_url.rstrip("/"))


@dataclass(frozen=True)
class HealthCheckResult:
    """单项检查结果，message 始终面向中文运维提示。"""

    ok: bool
    message: str
    details: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "message": self.message,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class HealthReport:
    """完整健康报告。"""

    ok: bool
    checks: Mapping[str, HealthCheckResult]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checks": {
                name: result.to_dict() for name, result in self.checks.items()
            },
        }


def _model_matches(installed: list[str], target: str) -> bool:
    """判断模型是否已安装；bge-m3 允许省略 ``:latest`` 标签。"""
    target = target.strip().lower()
    if target == "bge-m3" or target == "bge-m3:latest":
        return any(name.lower() in {"bge-m3", "bge-m3:latest"} for name in installed)
    return target in {name.lower() for name in installed}


def _request_models(
    config: HealthCheckConfig,
    opener: Callable[..., Any],
) -> tuple[HealthCheckResult, list[str] | None]:
    url = f"{config.base_url}/api/tags"
    request = urllib.request.Request(url, method="GET")
    try:
        with opener(request, timeout=config.timeout) as response:
            status = getattr(response, "status", 200)
            if status >= 400:
                return (
                    HealthCheckResult(
                        False,
                        f"Ollama HTTP 请求失败（状态码 {status}）",
                        {"url": url, "status": status},
                    ),
                    None,
                )
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, TypeError, urllib.error.URLError) as exc:
        return (
            HealthCheckResult(
                False,
                f"无法连接 Ollama 服务（{config.base_url}），请确认服务已启动",
                {"url": url, "error": str(exc)},
            ),
            None,
        )

    raw_models = payload.get("models", []) if isinstance(payload, dict) else []
    models = [
        item.get("name", "")
        for item in raw_models
        if isinstance(item, dict) and item.get("name")
    ]
    return (
        HealthCheckResult(
            True,
            "Ollama HTTP 服务正常",
            {"url": url, "status": status, "models": models},
        ),
        models,
    )


def _model_result(
    models: list[str] | None,
    target: str,
    kind: str,
) -> HealthCheckResult:
    if models is None:
        label = "目标聊天模型" if kind == "chat" else "嵌入模型"
        return HealthCheckResult(
            False,
            f"无法检查{label} {target}：Ollama HTTP 不可用",
            {"model": target},
        )
    if _model_matches(models, target):
        label = "目标聊天模型" if kind == "chat" else "嵌入模型"
        return HealthCheckResult(True, f"{label} {target} 已安装", {"model": target})
    label = "目标聊天模型" if kind == "chat" else "嵌入模型"
    action = f"ollama pull {target}"
    return HealthCheckResult(
        False,
        f"未安装{label} {target}，请先执行 {action}",
        {"model": target, "available_models": models},
    )


def _check_web_chat_port(
    config: HealthCheckConfig,
    port_checker: Callable[[str, int, float], bool],
) -> HealthCheckResult:
    details = {
        "host": config.web_chat_host,
        "port": config.web_chat_port,
        "transport": "tcp",
    }
    try:
        up = bool(
            port_checker(
                config.web_chat_host, config.web_chat_port, config.port_timeout
            )
        )
    except OSError as exc:
        up = False
        details["error"] = str(exc)
    if up:
        return HealthCheckResult(
            True,
            f"本地 Web 聊天服务端口 {config.web_chat_port} 已开放",
            details,
        )
    return HealthCheckResult(
        False,
        f"本地 Web 聊天服务端口 {config.web_chat_port} 未开放，请确认 {config.web_chat_port} 服务已启动",
        details,
    )


def check_ollama_health(
    config: HealthCheckConfig | None = None,
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
    port_checker: Callable[[str, int, float], bool] | None = None,
) -> HealthReport:
    """检查 Ollama HTTP、聊天模型、bge-m3 与 8899 Web 聊天端口。"""
    config = config or HealthCheckConfig()
    if port_checker is None:
        port_checker = lambda host, port, timeout: _tcp_up(host, port, timeout)

    http_result, models = _request_models(config, opener)
    checks = {
        "ollama_http": http_result,
        "chat_model": _model_result(models, config.chat_model, "chat"),
        "embedding_model": _model_result(models, config.embed_model, "embed"),
        "web_chat_8899": _check_web_chat_port(config, port_checker),
    }
    return HealthReport(ok=all(result.ok for result in checks.values()), checks=checks)


def _tcp_up(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="检查 Ollama 与本地 Web 聊天服务")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--chat-model", default=DEFAULT_CHAT_MODEL)
    parser.add_argument("--embed-model", default=DEFAULT_EMBED_MODEL)
    parser.add_argument("--web-port", type=int, default=DEFAULT_WEB_CHAT_PORT)
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    report = check_ollama_health(
        HealthCheckConfig(
            base_url=args.base_url,
            chat_model=args.chat_model,
            embed_model=args.embed_model,
            web_chat_port=args.web_port,
            timeout=args.timeout,
        )
    )
    if args.as_json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        for name, check in report.checks.items():
            mark = "✓" if check.ok else "✗"
            print(f"{mark} {name}: {check.message}")
        print("健康检查通过" if report.ok else "健康检查未通过")
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
