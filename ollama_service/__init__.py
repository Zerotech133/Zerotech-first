"""本地 Ollama 服务工具集。"""

__all__ = [
    "HealthCheckConfig",
    "HealthCheckResult",
    "HealthReport",
    "check_ollama_health",
]


def __getattr__(name):
    """按需导出健康检查 API，避免 ``python -m ollama_service.health`` 重复加载。"""
    if name in __all__:
        from . import health

        return getattr(health, name)
    raise AttributeError(name)
