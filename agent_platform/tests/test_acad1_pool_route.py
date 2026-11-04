# -*- coding: utf-8 -*-
"""ACAD1 全池页路由契约（2026-11-03 WO-ACAD1-POOLROUTE-20261103）。

背景：`/acad1/pool` 公开只读页需要 nginx 精确路由，否则被前缀规则劫持/404。
本测试只做静态契约校验（不 reload/不重启；语法门由合并后
`docker exec ai-portal nginx -t` 在 L2 窗口执行）：
1) nginx.conf 存在且仅有一处 `location = /acad1/pool`（精确匹配，不重复）；
2) 块内含 `try_files /acad1_pool.html =404;` 与 `add_header Cache-Control "no-cache";`；
3) 路由位于 `/academy` 相关路由附近；
4) 目标静态文件 `portal/acad1_pool.html` 存在（try_files 有物可吐）。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NGINX = ROOT / "portal" / "nginx.conf"
TARGET = ROOT / "portal" / "acad1_pool.html"

LOCATION = "location = /acad1/pool"
TRY_FILES = "try_files /acad1_pool.html =404;"
NO_CACHE = 'add_header Cache-Control "no-cache";'


def _conf():
    return NGINX.read_text(encoding="utf-8")


def _block(conf):
    """截取精确 location 块（从 location 行到匹配的 }），返回块文本。"""
    start = conf.index(LOCATION)
    # 精确路由都是一行头 + 块；按花括号配对找到块结束
    depth = 0
    i = start
    while i < len(conf):
        ch = conf[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return conf[start:i + 1]
        i += 1
    raise AssertionError("location 块未闭合")


def test_exact_route_unique():
    conf = _conf()
    assert conf.count(LOCATION) == 1, "location = /acad1/pool 必须唯一（重复会导致 nginx -t 失败）"


def test_route_block_try_files_and_cache():
    block = _block(_conf())
    assert TRY_FILES in block, "缺少 try_files /acad1_pool.html =404;"
    assert NO_CACHE in block, "缺少 Cache-Control no-cache"


def test_route_near_academy():
    conf = _conf()
    idx_pool = conf.index(LOCATION)
    idx_academy = conf.index("location = /academy")
    # 与 /academy 路由同区（前后 2000 字符窗口内）
    assert abs(idx_pool - idx_academy) < 2000, "/acad1/pool 路由应靠近 /academy 路由区"
    assert idx_pool > idx_academy


def test_target_page_exists():
    assert TARGET.is_file(), "目标文件 portal/acad1_pool.html 不存在，try_files 会 404"
    html = TARGET.read_text(encoding="utf-8")
    assert len(html) > 500, "acad1_pool.html 内容异常（过短）"
    # 只读公开页：不应出现写操作表单/自动交易开关
    assert "只读" in html or "研究" in html
    assert not re.search(r"<form[^>]+method=[\"']post", html, re.I)
