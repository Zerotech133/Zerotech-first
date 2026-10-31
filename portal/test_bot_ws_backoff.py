# -*- coding: utf-8 -*-
"""P2.7 bot WS 内部重连退避单测（G-A 工位）。

离线纪律：零网络、不真发飞书、不启任何服务、不重启在役 bot。
覆盖：
  · next_backoff 纯函数三态：初始 1s / 指数倍增 / 上限 60s / 存活
    ≥60s 重置（含恰好 60s 边界）；
  · 源码静态断言：main() 重连环存在、cleanly 出口保留、watchdog
    兜底注释存在、closed on failure 措辞、traceback 尾 ≤200、
    Client 重建在环内（AST 契约，格式无关）；
  · _patch_ws_card_route 幂等动态验证：重复调用后 CARD 帧路由
    （MessageType.CARD 同路派发）语义不破。
"""

import ast
import inspect
import os
import sys

import pytest  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import feishu_bot_ws  # noqa: E402

_SRC = inspect.getsource(feishu_bot_ws)


# ── next_backoff 三态 ────────────────────────────────────────


def test_backoff_initial_is_1s():
    """首次断连（delay=0）退避初始 1s。"""
    assert feishu_bot_ws.next_backoff(0.0, 5.0) == 1.0
    assert feishu_bot_ws.next_backoff(0.0, 0.0) == 1.0


def test_backoff_doubles():
    """未达稳定阈值时指数倍增：1→2→4→8。"""
    assert feishu_bot_ws.next_backoff(1.0, 10.0) == 2.0
    assert feishu_bot_ws.next_backoff(2.0, 10.0) == 4.0
    assert feishu_bot_ws.next_backoff(4.0, 10.0) == 8.0


def test_backoff_caps_at_60():
    """倍增上限 60s（32×2=64 被压到 60；60 保持 60）。"""
    assert feishu_bot_ws.next_backoff(32.0, 3.0) == 60.0
    assert feishu_bot_ws.next_backoff(60.0, 3.0) == 60.0


def test_backoff_resets_after_stable_alive():
    """存活≥60s 后异常视为新断连周期，退避重置 1s。"""
    assert feishu_bot_ws.next_backoff(45.0, 61.0) == 1.0
    assert feishu_bot_ws.next_backoff(60.0, 3600.0) == 1.0


def test_backoff_reset_boundary_exactly_60s():
    """边界：存活恰好 60s 即视为稳定，重置 1s；59.9s 不重置。"""
    assert feishu_bot_ws.next_backoff(20.0, 60.0) == 1.0
    assert feishu_bot_ws.next_backoff(20.0, 59.9) == 40.0


# ── 源码静态断言 ─────────────────────────────────────────────


def test_src_reconnect_loop_exists():
    """main() 内部重连环存在：while True + reconnect 日志 + 退避接线。"""
    assert "while True" in _SRC
    assert "bot ws reconnect #%d in %.1fs" in _SRC
    assert "delay = next_backoff(delay, alive_secs)" in _SRC
    assert "time.sleep(delay)" in _SRC


def test_src_clean_exit_and_watchdog_kept():
    """start() 正常返回的 cleanly 出口保留；watchdog 兜底语义注释在。"""
    assert "bot ws exited cleanly" in _SRC
    assert "watchdog" in _SRC and "兜底" in _SRC
    # 断线原因日志：closed on failure 措辞 + traceback 尾 ≤200 字符
    assert "bot ws closed on failure" in _SRC
    assert "traceback.format_exc()[-200:]" in _SRC


def _main_client_calls(src=None):
    """在 main() 的 AST 中找 ``Client(...)`` 构造，返回 (环内列表, 环外列表)。

    格式无关：2026-10-27 WO-GUANYU-FMT10 全仓格式化把单行调用拆成多行
    （commit 838a5bdd7，纯格式零行为变化），旧字符串计数断言因此假红；
    改为 AST 断言真实契约。``src`` 仅供阴性对照测试注入合成源码。
    """
    main_fn = next(n for n in ast.walk(ast.parse(src or _SRC))
                   if isinstance(n, ast.FunctionDef) and n.name == "main")
    inside, outside = [], []

    def walk(node, in_while):
        if isinstance(node, ast.While):
            for child in ast.iter_child_nodes(node):
                walk(child, True)
            return
        if isinstance(node, ast.Call):
            fn = node.func
            name = (fn.id if isinstance(fn, ast.Name)
                    else fn.attr if isinstance(fn, ast.Attribute) else "")
            if name == "Client":
                (inside if in_while else outside).append(node)
        for child in ast.iter_child_nodes(node):
            walk(child, in_while)

    for child in ast.iter_child_nodes(main_fn):
        walk(child, False)
    return inside, outside


def test_src_client_rebuild_inside_loop():
    """Client 重建契约（格式无关）：环外初始化 1 处 + 重连环内重建 1 处。

    2026-10-31 WO-QUAL-PORTAL-20261031 根因判定：**非代码回归，是测试契约
    过期**——2026-10-27 WO-GUANYU-FMT10 格式化批次（838a5bdd7）把
    ``cli = Client(app_id, app_secret, event_handler=handler,`` 单行拆成多行，
    行为未变。此处改断言 AST 构造点：恰 1 处 while 环内 + 恰 1 处环外，
    且两处都复用同一 app_id/app_secret/event_handler（重连必须换新 Client）。
    """
    inside, outside = _main_client_calls()
    assert len(inside) == 1, "重连环内必须重建 Client（存活 ≥60s 新断连周期仍要换新连接）"
    assert len(outside) == 1, "环外初始化 Client 恰好 1 处"
    for call in inside + outside:
        pos = [a.id if isinstance(a, ast.Name) else "?" for a in call.args]
        kws = {k.arg: (k.value.id if isinstance(k.value, ast.Name) else "?")
               for k in call.keywords}
        assert pos[:2] == ["app_id", "app_secret"]
        assert kws.get("event_handler") == "handler"


def test_client_rebuild_check_detects_missing_in_loop_rebuild():
    """阴性对照：环内不再重建 Client 时契约必须报红（防空壳断言假绿）。"""
    fake = (
        "def main():\n"
        "    cli = Client(app_id, app_secret, event_handler=handler)\n"
        "    while True:\n"
        "        try:\n"
        "            cli.start()\n"
        "        except Exception:\n"
        "            time.sleep(1)\n"
        "            cli.start()\n"          # 回归形态：环内未重建 Client
    )
    inside, outside = _main_client_calls(fake)
    assert len(outside) == 1
    assert inside == []


# ── _patch_ws_card_route 幂等（动态） ────────────────────────


def test_patch_ws_card_route_idempotent_and_card_routed():
    """重复调用幂等：类级一次性赋值（不读旧值），二次调用后 CARD 帧
    仍与 EVENT 同路派发（路由语义不破）。
    lark_oapi 仅在在役 bot venv 可用，缺失则跳过（静态侧已兜底）。"""
    pytest.importorskip("lark_oapi")
    from lark_oapi.ws import client as wsc

    feishu_bot_ws._patch_ws_card_route()
    feishu_bot_ws._patch_ws_card_route()  # 二次调用（模拟环内重复 patch）
    patched_twice = wsc.Client._handle_data_frame

    assert callable(patched_twice)
    src = inspect.getsource(patched_twice)
    # CARD 与 EVENT 同路派发（原 SDK 此处 CARD 直接 return 丢弃）
    assert "MessageType.EVENT, MessageType.CARD" in src
    # 幂等佐证：函数体对旧 _handle_data_frame 零读取（纯覆盖赋值，
    # 静态侧另有 test_src_patch_is_pure_override 断言）


def test_src_patch_is_pure_override():
    """_patch_ws_card_route 是纯覆盖赋值：代码体（docstring 外）中
    _handle_data_frame 仅出现一次且为赋值左值——重复调用天然幂等。"""
    fn_src = inspect.getsource(feishu_bot_ws._patch_ws_card_route)
    body = fn_src.split('"""')[2]  # docstring 之后代码体
    assert body.count("_handle_data_frame") == 1
    assert "_wsc.Client._handle_data_frame = _fixed" in fn_src
