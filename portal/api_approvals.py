# -*- coding: utf-8 -*-
"""M1-阶段二 Approval 注册表只读 mixin（挂 portal_api.Handler 继承链）。

GET /api/platform/approvals?target_type=&target_id=&decided_by=&limit=&order=
    → agent_platform/adapters/approvals_registry.build_registry()
    只读 ro 直读账本；参数白名单，非法参数 400；无缓存（低频直读）。
"""

from __future__ import annotations

from pathlib import Path

import urllib.parse as _urlparse

_REPO_ROOT = Path(__file__).resolve().parents[1]

_QUERY_FIELDS = ("target_type", "target_id", "decided_by", "limit", "order")


def _scrub_repo_paths(obj, root_str: str):
    """P1 安全口径（2026-10-17 端点验收实测）：出口脱敏——响应中的仓库
    绝对路径（root/source.path 等）替换为相对路径，防本机目录结构外泄。
    递归覆盖 dict/list/str；空结果回退 "."。"""
    if isinstance(obj, dict):
        return {k: _scrub_repo_paths(v, root_str) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_scrub_repo_paths(v, root_str) for v in obj]
    if isinstance(obj, str) and root_str and root_str in obj:
        rel = obj.replace(root_str, "").lstrip("\\/").strip()
        return rel or "."
    return obj


class Handler:
    """Approval 注册表 mixin（模块名绑定：portal_api 里 import api_approvals）。"""

    def _platform_approvals(self, qs: str):
        try:
            from agent_platform.adapters.approvals_registry import build_registry
        except ImportError:
            import sys as _sys
            _p = str(_REPO_ROOT / "agent_platform")
            if _p not in _sys.path:
                _sys.path.insert(0, _p)
            from adapters.approvals_registry import build_registry

        params = _urlparse.parse_qs(qs or "", keep_blank_values=True)
        kwargs = {}
        for name in _QUERY_FIELDS:
            value = (params.get(name) or [None])[0]
            if value not in (None, ""):
                kwargs[name] = value
        try:
            registry = build_registry(_REPO_ROOT, **kwargs)
        except ValueError as exc:
            self._json({"error": "invalid_query", "message": str(exc)}, 400)
            return
        except Exception as exc:  # fail loud
            self._json({"error": "registry_failed", "message": repr(exc)}, 500)
            return
        self._json(_scrub_repo_paths(registry, str(_REPO_ROOT)))
