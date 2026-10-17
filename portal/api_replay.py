# -*- coding: utf-8 -*-
"""M1-③ 事件回放查询只读 mixin（挂 portal_api.Handler 继承链）。

GET /api/platform/replay?source=&kind=&actor=&object_id=&object_type=
                    &trace_id=&causation_id=&since=&until=&limit=&order=
    → agent_platform/adapters/event_replay.build_replay()
    全部只读；参数白名单化，非法参数 400（不静默忽略）；无缓存
    （SQLite/JSONL 百毫秒级现算，查询面每次现读保证新鲜）。
"""

from __future__ import annotations

from pathlib import Path

import urllib.parse as _urlparse

_REPO_ROOT = Path(__file__).resolve().parents[1]

# 查询参数白名单（与 event_replay.build_replay 形参一一对应）
_QUERY_FIELDS = ("source", "kind", "actor", "object_id", "object_type",
                 "trace_id", "causation_id", "since", "until", "limit", "order")


class Handler:
    """事件回放查询 mixin（模块名绑定：portal_api 里 import api_replay）。"""

    def _platform_replay(self, qs: str):
        try:
            from agent_platform.adapters.event_replay import build_replay
        except ImportError:
            # portal 目录直跑时 agent_platform 不在 sys.path 的兜底
            import sys as _sys
            _p = str(_REPO_ROOT / "agent_platform")
            if _p not in _sys.path:
                _sys.path.insert(0, _p)
            from adapters.event_replay import build_replay

        params = _urlparse.parse_qs(qs or "", keep_blank_values=True)
        kwargs = {}
        for name in _QUERY_FIELDS:
            value = (params.get(name) or [None])[0]
            if value not in (None, ""):
                kwargs[name] = value
        try:
            replay = build_replay(_REPO_ROOT, **kwargs)
        except ValueError as exc:
            self._json({"error": "invalid_query", "message": str(exc)}, 400)
            return
        except Exception as exc:  # fail loud：数据层异常如实暴露
            self._json({"error": "replay_failed", "message": repr(exc)}, 500)
            return
        self._json(replay)
