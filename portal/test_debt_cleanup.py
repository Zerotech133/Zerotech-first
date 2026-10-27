# -*- coding: utf-8 -*-
"""P2.5 FD-C · 遗留债清尾 · 离线契约测试（2026-10-20）。

全部离线（不起服务、不发网络请求），覆盖：
1. /api/public/ops 无密钥只读端点存在（位于 /api/state 之前，无鉴权前置）；
2. 端点响应结构：ok/generated_at/dead_letters_24h/services/note；
3. 响应不含 open_id/target/kind 明文键（绝不暴露收件人/内容明细）；
4. 复用 dead_letter.count_since(24)（60s 缓存语义不重造）；
5. ops_board.html 含 renderDeadLetterPublic 且保留原 /api/state 降级文案；
6. ops_board.html 零写操作（无 POST / 无表单）；
7. 品牌收尾：console.html / index.html / manifest.json 含「邓芝」；
8. start-hermes.ps1 banner 已改但原路径逻辑行仍在；
9. 8097 其他端点锚点抽验（零破坏）；
10. notify_command.html 页脚含死信计数源图例行。
"""

import sys
import types
from pathlib import Path


PORTAL = Path(__file__).resolve().parent
REPO = PORTAL.parent
sys.path.insert(0, str(PORTAL))

import push_admin_server as admin  # noqa: E402

ADMIN_SRC = (PORTAL / "push_admin_server.py").read_text(encoding="utf-8")
OPS_BOARD = (PORTAL / "static" / "ops_board.html").read_text(encoding="utf-8")
NOTIFY_HTML = (PORTAL / "static" / "notify_command.html").read_text(encoding="utf-8")
CONSOLE_HTML = (
    REPO / "Hermes" / "hermes-webui-next" / "static" / "console.html"
).read_text(encoding="utf-8")
INDEX_HTML = (
    REPO / "Hermes" / "hermes-webui-next" / "static" / "index.html"
).read_text(encoding="utf-8")
MANIFEST = (
    REPO / "Hermes" / "hermes-webui-next" / "static" / "manifest.json"
).read_text(encoding="utf-8")
START_PS1 = (REPO / "Hermes" / "start-hermes.ps1").read_text(encoding="utf-8")


class _FakeHandler:
    """只够跑 do_GET 的 /api/public/ops 分支的最小桩。"""

    def __init__(self):
        self.path = "/api/public/ops"
        self.payload = None
        self.code = None

    def _json(self, obj, code=200):
        self.payload = obj
        self.code = code


def _call_public_ops(monkeypatch, count=3):
    """以桩替 dead_letter，直呼 do_GET 的公开摘要分支，返回捕获的载荷。"""
    calls = []
    fake_dl = types.ModuleType("dead_letter")

    def _count_since(hours=24):
        calls.append(hours)
        return count

    fake_dl.count_since = _count_since
    monkeypatch.setitem(sys.modules, "dead_letter", fake_dl)
    handler = _FakeHandler()
    admin.Handler.do_GET(handler)
    return handler, calls


# ── 1. 端点存在且无鉴权前置 ──


def test_public_ops_endpoint_exists_before_state_branch():
    """/api/public/ops branch 存在，且位于 /api/state（需密钥）branch 之前。"""
    assert ADMIN_SRC.count('p == "/api/public/ops"') == 1
    assert ADMIN_SRC.index('p == "/api/public/ops"') < ADMIN_SRC.index(
        'if p == "/api/state":'
    )
    # 该 branch 自身不引任何密钥判断（无 X-Push-Admin-Key / authorized 前置）
    branch = ADMIN_SRC[
        ADMIN_SRC.index('p == "/api/public/ops"') : ADMIN_SRC.index(
            'if p == "/api/state":'
        )
    ]
    assert "X-Push-Admin-Key" not in branch
    assert "authorized" not in branch


# ── 2/3/4. 响应结构与隐私边界 ──


def test_public_ops_payload_shape(monkeypatch):
    handler, _ = _call_public_ops(monkeypatch, count=7)
    assert handler.code == 200
    body = handler.payload
    assert body["ok"] is True
    assert body["dead_letters_24h"] == 7
    assert isinstance(body["generated_at"], str) and body["generated_at"]
    assert "无鉴权" in body["note"]
    ports = sorted(s["port"] for s in body["services"])
    assert ports == [8091, 8096, 8097, 8099, 8787]


def test_public_ops_no_plaintext_fields(monkeypatch):
    """响应绝不携带 open_id/target/kind 明文键（含嵌套 services）。"""
    handler, _ = _call_public_ops(monkeypatch)
    banned = {"open_id", "target", "kind", "receivers"}
    for key in handler.payload:
        assert key not in banned, f"公开摘要泄露敏感键: {key}"
    assert isinstance(handler.payload["services"], list)
    for svc in handler.payload["services"]:
        assert set(svc) <= {"port", "label", "note"}


def test_public_ops_reuses_count_since_24h(monkeypatch):
    """复用 N6 count_since，且固定 24h 窗口，不重造计数。"""
    _, calls = _call_public_ops(monkeypatch)
    assert calls == [24]


def test_public_ops_count_minus_one_honest(monkeypatch):
    """count_since 异常语义 -1 原样透传（不猜数、不假绿）。"""
    handler, _ = _call_public_ops(monkeypatch, count=-1)
    assert handler.payload["dead_letters_24h"] == -1


# ── 5/6. ops_board 页面契约 ──


def test_ops_board_has_render_dead_letter_public_and_degraded_fallback():
    assert "function renderDeadLetterPublic" in OPS_BOARD
    assert "/push/api/public/ops" in OPS_BOARD
    # 原 /api/state 降级文案保留（公开摘要失败时回退，未删）
    assert "/api/state 读取失败" in OPS_BOARD
    assert "本卡不显示兜底数字" in OPS_BOARD
    # 公开摘要成功时不得再走 renderDeadLetter(null,null) 覆盖刚渲染的卡
    assert "renderDeadLetter(null,null)" not in OPS_BOARD


def test_ops_board_no_write_operations():
    assert "POST" not in OPS_BOARD
    assert "<form" not in OPS_BOARD
    assert "method:'GET'" in OPS_BOARD  # 既有 fetch 判例：显式 GET


# ── 7. 品牌收尾 ──


def test_branding_dengzhi_in_entry_files():
    assert "邓芝 8787" in CONSOLE_HTML
    assert "由邓芝转调持有" in CONSOLE_HTML
    assert "邓芝" in INDEX_HTML
    assert '"name": "邓芝"' in MANIFEST
    assert '"short_name": "邓芝"' in MANIFEST


def test_branding_identifiers_untouched():
    """代码逻辑/变量名/接口路径不动：CSRF 头、CONFIG 占位、启动脚本路径仍在。"""
    assert "X-Hermes-CSRF-Token" in INDEX_HTML
    assert "__HERMES_CONFIG__" in INDEX_HTML
    assert "HERMES_WEBUI_PASSWORD" in START_PS1  # 环境变量名不改


# ── 8. start-hermes.ps1 banner 与路径逻辑 ──


def test_start_hermes_banner_changed_but_paths_intact():
    assert "邓芝 WebUI Launcher（生产基=hermes-webui-next）" in START_PS1
    assert "start_hermes_bg.py" in START_PS1  # 守护链注释行
    # 原路径逻辑行仍在（一行不改）
    assert 'Join-Path $PSScriptRoot "hermes-webui\\server.py"' in START_PS1
    assert 'Join-Path $resolvedHermesHome "hermes-agent"' in START_PS1
    assert "else { 8787 }" in START_PS1


# ── 9. 8097 其他端点锚点抽验（零破坏） ──


def test_8097_other_endpoints_unchanged():
    anchors = (
        'p.startswith("/api/quant/")',
        'p == "/api/state" and not admin_authorized',
        'p in ("/api/decisions", "/api/decision-groups")',
        '"dead_letters": _dead_letter_stats()',
        "def do_POST",
        "_dead_letter_alert_if_needed()",
    )
    for a in anchors:
        assert a in ADMIN_SRC, f"8097 锚点丢失: {a}"


# ── 10. notify_command 图例 ──


def test_notify_command_legend_mentions_public_ops():
    assert "死信计数源：/api/public/ops（无鉴权摘要" in NOTIFY_HTML
