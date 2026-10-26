"""entrypoint.py 冒烟测试（S3-E 通闸工位；S6 工位S 增补端口口径断言）。

只测默认（无 --serve）路径与 argparse 行为，不启动真实服务、不监听端口。
"""
from __future__ import annotations

import socket

import pytest

import entrypoint
from entrypoint import main

# 部署侧端口约定（compose/prometheus/test_deploy_config._expected_port）：
# 9400 + 10×角色在 KNOWN_ROLES 中的序号。
EXPECTED_PORTS = {
    "message_bus": 9400,
    "zhuge_liang": 9410,
    "tian_feng": 9420,
    "zhao_yun": 9430,
    "platform_agent": 9440,
    "risk_guard": 9450,
    "approval_gate": 9460,
    "os_commander": 9470,
}


def test_zhuge_liang_selfcheck_returns_zero(capsys):
    """a) main(["--role","zhuge_liang"]) 不抛异常且返回 0（自检路径）。"""
    rc = main(["--role", "zhuge_liang"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "自检" in out
    assert "zhuge_liang" in out
    # 无 --serve 不得启动 uvicorn：自检信息应注明业务待实现
    assert "业务逻辑待实现" in out


def test_message_bus_without_serve_no_listen(capsys):
    """b) --role message_bus 无 --serve 时不监听端口、返回 0。"""
    rc = main(["--role", "message_bus"])
    assert rc == 0
    capsys.readouterr()  # 清空输出，断言不依赖打印内容
    # 9400 端口不应有服务在监听
    probe = socket.socket()
    probe.settimeout(0.5)
    with pytest.raises(OSError):
        probe.connect(("127.0.0.1", 9400))
    probe.close()


def test_invalid_role_rejected(capsys):
    """c) 非法 --role 被 argparse 拒绝（SystemExit）。"""
    with pytest.raises(SystemExit) as excinfo:
        main(["--role", "not_a_role"])
    assert excinfo.value.code != 0


# ---------------- P0-4 回归（WO-LEGION-P0FIX-20261026）----------------
# 三个真实断点：①总线进程不 register → 上报必判“伪署名”进死信；
# ②赵云 report_to=principal（非注册席）→ 只落 delivered=false 并挂起；
# ③entrypoint 从不注入 state_path → 生产启动即无持久化。

def test_zhao_yun_reports_to_tian_feng():
    """赵云问题单归口田丰，且目标必须是已知席位（不是 principal 伪目标）。"""
    assert entrypoint.REPORT_TO["zhao_yun"] == "tian_feng"
    assert entrypoint.REPORT_TO["zhao_yun"] in entrypoint.KNOWN_ROLES


def test_message_bus_registers_known_seats(monkeypatch, capsys):
    import roles.message_bus as mb

    seen = {}
    real_init = mb.MessageBus.__init__

    def spy_init(self, *a, **kw):
        real_init(self, *a, **kw)
        real_register = self.register

        def record(name, level):
            seen[name] = level
            return real_register(name, level)

        self.register = record

    monkeypatch.setattr(mb.MessageBus, "__init__", spy_init)
    rc = entrypoint.main(["--role", "message_bus"])
    assert rc == 0
    assert seen == entrypoint.SEAT_LEVELS, "总线未按 SEAT_LEVELS 注册席位"


def _spy_seat_module(monkeypatch, captured):
    import sys
    import types

    class SpySeat:
        def __init__(self, **kw):
            captured.update(kw)

    fake = types.ModuleType("roles.zhao_yun_spy")
    fake.ZhaoYun = SpySeat
    monkeypatch.setitem(sys.modules, "roles.zhao_yun_spy", fake)
    monkeypatch.setitem(entrypoint.SEAT_CLASSES, "zhao_yun",
                        ("roles.zhao_yun_spy", "ZhaoYun"))


def test_zhao_yun_state_path_env_injected(monkeypatch, capsys):
    captured = {}
    _spy_seat_module(monkeypatch, captured)
    monkeypatch.setenv(entrypoint.ZHAO_YUN_STATE_ENV, r"C:\tmp\zhao_state.json")
    rc = entrypoint.main(["--role", "zhao_yun"])
    assert rc == 0
    assert captured.get("state_path") == r"C:\tmp\zhao_state.json"
    assert captured.get("report_to") == "tian_feng"


def test_zhao_yun_state_path_absent_keeps_memory_mode(monkeypatch, capsys):
    captured = {}
    _spy_seat_module(monkeypatch, captured)
    monkeypatch.delenv(entrypoint.ZHAO_YUN_STATE_ENV, raising=False)
    rc = entrypoint.main(["--role", "zhao_yun"])
    assert rc == 0
    assert "state_path" not in captured  # 缺省零副作用：纯内存
