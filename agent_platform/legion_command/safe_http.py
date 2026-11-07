# -*- coding: utf-8 -*-
"""安全 urlopen（legion_command 树入口）：scheme allowlist + 回环禁代理。

2026-11-07 修：本文件是原先唯一一份没同步回环豁免的实现，导致 legion_command 全树
12 处打本机服务（11434 Ollama / 8770-8771 OpenHands / 8787 Hermes / 8796 模型网关）
的请求在 HTTP_PROXY 存在时仍被交给代理 —— 代理一严格，探活全部静默失效（显示成
服务宕机，日志里看不出是代理的锅）。

现改为委托载入共享实现 `agent_platform/safe_urlopen.py`（与
`hermes_auth._load_safe_urlopen` 同款 spec 载入方式），实现只保留一份，以后
打补丁不会再漏。

文件名**保持不变**：legion_command 有「包入口 / 脚本入口」双形态，脚本形态下本目录
在 sys.path[0]，若这里也叫 safe_urlopen.py 会遮蔽其它树的同名副本 —— 出现
「改了根目录那份、这个入口不生效」。故沿用 safe_http 这个名字，只做委派。
"""
from __future__ import annotations

import importlib.util as _ilu
import sys as _sys
from pathlib import Path as _Path

ALLOWED_SCHEMES = ("http", "https")


def _load_shared():
    here = _Path(__file__).resolve()
    cands = [
        here.parents[1] / "safe_urlopen.py",              # agent_platform/safe_urlopen.py
        here.parents[2] / "portal" / "safe_urlopen.py",   # 仓库 portal 副本
        here.parents[2] / "tools" / "safe_urlopen.py",    # 仓库 tools 副本
    ]
    for cand in cands:
        if not cand.exists():
            continue
        spec = _ilu.spec_from_file_location("_zb_safe_urlopen", cand)
        mod = _ilu.module_from_spec(spec)
        _sys.modules.setdefault("_zb_safe_urlopen", mod)
        spec.loader.exec_module(mod)
        return mod
    return None


_shared = _load_shared()

if _shared is not None:
    safe_urlopen = _shared.safe_urlopen
else:  # 兜底：至少保留 scheme 校验（fail-visible，绝不静默放行）
    import urllib.request
    from urllib.parse import urlsplit

    def safe_urlopen(url, *args, **kwargs):
        target = getattr(url, "full_url", None) or str(url)
        scheme = urlsplit(target).scheme.lower()
        if scheme not in ALLOWED_SCHEMES:
            raise ValueError("safe_urlopen 拒绝非 http(s) scheme: %r（url=%r）"
                             % (scheme or "<none>", target[:120]))
        return urllib.request.urlopen(url, *args, **kwargs)  # nosec B310
