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
        self._json(registry)
