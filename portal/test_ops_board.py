# -*- coding: utf-8 -*-
"""FB-M 运维对账面 · 离线契约测试（2026-10-20 飞书中台 P1）

全部为读文件断言（不起服务、不发网络请求），覆盖：
1. nginx.conf 有 /ops-board location
2. nginx.conf 有 /api/state 反代（本次新增；加行前 grep count==0 无同名冲突）
3. portal_api.py 有 /ops-board 路由 branch
4. nav.js 有「运维对账」条目
5. 只读纪律：ops_board.html 无写操作（无 POST）
6. 降级诚实：ops_board.html 含 degraded 降级区与只读声明文案
"""

import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NGINX = os.path.join(ROOT, "portal", "nginx.conf")
API = os.path.join(ROOT, "portal", "portal_api.py")
HTML = os.path.join(ROOT, "portal", "static", "ops_board.html")
NAV = os.path.join(ROOT, "portal", "static", "nav.js")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def test_nginx_has_ops_board_location():
    """nginx.conf 照 notify-command 模式有 /ops-board 静态页 location。"""
    conf = _read(NGINX)
    assert conf.count("location = /ops-board") == 1
    # 静态页模式判例：try_files + no-cache
    assert re.search(
        r"location = /ops-board \{ try_files /static/ops_board\.html =404; "
        r'add_header Cache-Control "no-cache"; \}',
        conf,
    )


def test_nginx_has_api_state_proxy():
    """nginx.conf 有 /api/state 只读反代（→8097；新增前确认无同名冲突）。"""
    conf = _read(NGINX)
    assert conf.count("location = /api/state") == 1
    # 反代到 8097（带变量判例，同 /push 块）
    m = re.search(r"location = /api/state \{.*?proxy_pass http://\$h:8097;", conf, re.S)
    assert m, "缺 proxy_pass → 8097"


def test_portal_api_has_ops_board_branch():
    """portal_api.py 照 notify-command branch 模式有 /ops-board 只读路由。"""
    src = _read(API)
    assert 'path == "/ops-board"' in src
    # 路由指向新页面文件
    assert re.search(r'path == "/ops-board".*?ops_board\.html', src, re.S)
    # 判例锚点仍在（零破坏）
    assert 'path == "/notify-command"' in src


def test_nav_has_ops_board_entry():
    """nav.js 有「运维对账」条目，指向 /ops-board。"""
    nav = _read(NAV)
    assert nav.count('"/ops-board"') == 1
    assert "运维对账" in nav


def test_ops_board_html_readonly_no_write_ops():
    """只读纪律：页面无任何写操作（无 POST、无 DELETE/PUT、无发送触发）。"""
    html = _read(HTML)
    # 唯一的 fetch 一律 GET（显式 method:'GET'）
    for m in re.finditer(r"fetch\([^)]*\)", html):
        seg = m.group(0)
        assert "POST" not in seg.upper(), "页面出现 POST 写操作: %s" % seg
    assert "method:'DELETE'" not in html and "method:'PUT'" not in html
    assert "XMLHttpRequest" not in html
    # 无表单提交
    assert "<form" not in html


def test_ops_board_html_degraded_and_readonly_banner():
    """降级诚实：含 degraded 降级区与只读声明文案（fail-visible，不造数）。"""
    html = _read(HTML)
    # degraded 降级区（照 notify_command.html 判例结构）
    assert 'id="degraded"' in html
    assert "已降级" in html
    assert "不显示静态兜底数据" in html or "不显示兜底数字" in html
    # 只读声明文案
    assert "只读投影" in html
    assert "本页没有任何写操作" in html
    # 邓芝统辖页头标注
    assert "邓芝统辖" in html
    # 60s 自动刷新（照判例）
    assert "setInterval(load,60000)" in html
    # 在役表诚实声明：不探测、不假绿
    assert "本页不探测" in html
    # 8s 超时与 same-origin（判例一致）
    assert "credentials:'same-origin'" in html
    assert "8000" in html  # AbortController 8s 超时
