"""WO-S12-03 接线收尾：LEGION_SEAT_TOKENS 凭证注入 + message_bus 的 --serve。

覆盖两层此前的"永不生效"缺口：
1. entrypoint 裸 `MessageBus()` 不传 seat_credentials → 鉴权恒关闭；
2. `--serve` 对 message_bus 无效（只实例化、不监听）→ 没有调用方，
   鉴权配没配都不会被触发。

不启动真实服务、不占端口：uvicorn.run 一律打桩。
环境变量一律走 monkeypatch（含 delenv 清掉外部残留），不污染同进程其他用例。
"""
from __future__ import annotations

import json

import pytest

import roles.message_bus as message_bus_module
from entrypoint import (
    SEAT_TOKENS_ENV,
    SeatCredentialsError,
    _load_seat_credentials,
    _resolve_bind_host,
    _run_message_bus,
    main,
)

# 特征 token：用于断言"打印/异常里不得出现 token"（模拟数据）。
SEKRET = "SEKRET-TOKEN-XYZ"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """清掉外部可能存在的 LEGION_SEAT_TOKENS，避免宿主环境污染本文件用例。

    raising=False：变量本来不存在时 delenv 不报错。
    """
    monkeypatch.delenv(SEAT_TOKENS_ENV, raising=False)
    yield


def _capture_bus(monkeypatch):
    """打桩 MessageBus.__init__，把构造出的实例与 kwargs 收进 dict。

    直接 patch 真实类的 __init__（而不是换成子类），保证被测路径用的仍是
    生产类，鉴权属性、净化逻辑都是真的。
    """
    captured: dict = {}
    orig_init = message_bus_module.MessageBus.__init__

    def spy_init(self, *args, **kwargs):
        orig_init(self, *args, **kwargs)
        captured["bus"] = self
        captured["init_kwargs"] = kwargs

    monkeypatch.setattr(message_bus_module.MessageBus, "__init__", spy_init)
    return captured


def _capture_app(monkeypatch, captured):
    """打桩工厂，记录"谁构造了 app、app 属于哪个 bus"。

    说明：MessageBus 本体不持有 .app（应用由模块级 create_app(bus) 工厂产出），
    故用工厂作为接线校验点——断言 uvicorn 拿到的 app 正是由本次构造的 bus 产出的。
    """
    orig_create_app = message_bus_module.create_app

    def spy_create_app(bus):
        app = orig_create_app(bus)
        captured["app"] = app
        captured["app_bus"] = bus
        return app

    monkeypatch.setattr(message_bus_module, "create_app", spy_create_app)
    return captured


# ---------------- 纯函数：未配置 → None（鉴权关闭，零破坏） ----------------

def test_load_none_returns_none():
    """变量未设置 → None（鉴权关闭，既有行为）。"""
    assert _load_seat_credentials(None) is None


def test_load_empty_string_returns_none():
    """空串视为未配置 → None。"""
    assert _load_seat_credentials("") is None


def test_load_whitespace_returns_none():
    """纯空白（含换行/制表符）视为未配置 → None。"""
    assert _load_seat_credentials("   \n\t  ") is None


# ---------------- 纯函数：合法配置 ----------------

def test_load_valid_multi_seat():
    """合法 JSON 对象 → 返回等价 dict（多席位）。"""
    raw = json.dumps({"tian_feng": "token-a", "zhao_yun": "token-b"})
    assert _load_seat_credentials(raw) == {
        "tian_feng": "token-a",
        "zhao_yun": "token-b",
    }


def test_load_valid_token_with_json_special_chars():
    """token 含 = , : 空白等字符也能原样保留（JSON 有明确转义，无解析歧义）。"""
    tricky = "a=b,c:d \"quoted\" 中文"
    raw = json.dumps({"tian_feng": tricky}, ensure_ascii=False)
    assert _load_seat_credentials(raw) == {"tian_feng": tricky}


# ---------------- 纯函数：五种坏法（逐条单独断言，不合并） ----------------

def test_load_non_json_raises():
    """坏法1：不是 JSON。"""
    with pytest.raises(SeatCredentialsError):
        _load_seat_credentials("tian_feng=token-a")


def test_load_json_array_raises():
    """坏法2：JSON 合法但不是对象（数组）。"""
    with pytest.raises(SeatCredentialsError):
        _load_seat_credentials('["tian_feng", "token-a"]')


def test_load_json_string_scalar_raises():
    """坏法3：JSON 标量（字符串）。"""
    with pytest.raises(SeatCredentialsError):
        _load_seat_credentials('"tian_feng=token-a"')


def test_load_json_number_scalar_raises():
    """坏法3 补充：JSON 标量（数字）同样拒绝。"""
    with pytest.raises(SeatCredentialsError):
        _load_seat_credentials("42")


def test_load_value_not_string_raises():
    """坏法4：值不是字符串（数字）。"""
    with pytest.raises(SeatCredentialsError):
        _load_seat_credentials('{"tian_feng": 123}')


def test_load_value_null_raises():
    """坏法4 补充：值为 null 同样拒绝。"""
    with pytest.raises(SeatCredentialsError):
        _load_seat_credentials('{"tian_feng": null}')


def test_load_empty_key_raises():
    """坏法5：键为空串。"""
    with pytest.raises(SeatCredentialsError):
        _load_seat_credentials('{"": "token-a"}')


def test_load_whitespace_key_raises():
    """坏法5 补充：键为纯空白。"""
    with pytest.raises(SeatCredentialsError):
        _load_seat_credentials('{"   ": "token-a"}')


def test_load_empty_value_raises():
    """坏法6：值为空串（空 token 会变成免凭证通道）。"""
    with pytest.raises(SeatCredentialsError):
        _load_seat_credentials('{"tian_feng": ""}')


def test_load_whitespace_value_raises():
    """坏法6 补充：值为纯空白。"""
    with pytest.raises(SeatCredentialsError):
        _load_seat_credentials('{"tian_feng": "   "}')


def test_load_empty_object_raises():
    """{} 合法但零席位 → 启动失败（明显的配置遗漏，见 entrypoint 注释）。"""
    with pytest.raises(SeatCredentialsError):
        _load_seat_credentials("{}")


def test_load_error_message_does_not_leak_token():
    """坏配置的异常消息不得回显任何 token（含同时存在的好 token）。"""
    raw = json.dumps({"tian_feng": SEKRET, "zhao_yun": 1})
    with pytest.raises(SeatCredentialsError) as excinfo:
        _load_seat_credentials(raw)
    assert SEKRET not in str(excinfo.value)
    # 席位名可以回显（部署清单里公开），便于定位是哪个条目配坏
    assert "zhao_yun" in str(excinfo.value)


def test_seat_credentials_error_is_value_error():
    """异常类型可辨识：SeatCredentialsError 是 ValueError 子类。"""
    assert issubclass(SeatCredentialsError, ValueError)


# ---------------- 端到端：env → 总线构造 ----------------

def test_run_message_bus_injects_credentials(monkeypatch, capsys):
    """env 配好 → bus.auth_enabled 为 True，凭证表内容正确。"""
    monkeypatch.setenv(
        SEAT_TOKENS_ENV, json.dumps({"tian_feng": "tok-test", "zhao_yun": "tok-b"})
    )
    captured = _capture_bus(monkeypatch)

    rc = _run_message_bus(serve=False, port=9400)

    assert rc == 0
    bus = captured["bus"]
    assert bus.auth_enabled is True
    # 用公开校验接口证明凭证表内容（不读私有字段）
    assert bus.check_seat_credential("tian_feng", "tok-test") is None
    assert bus.check_seat_credential("zhao_yun", "tok-b") is None
    assert bus.check_seat_credential("tian_feng", "wrong") == "auth_token_invalid"
    assert bus.check_seat_credential("ghost", "tok-test") == "auth_seat_unknown"
    out = capsys.readouterr().out
    assert "auth=enabled seats=2" in out


def test_run_message_bus_without_env_disables_auth(monkeypatch, capsys):
    """env 未设置 → auth_enabled 为 False（零破坏：既有部署行为不变）。"""
    captured = _capture_bus(monkeypatch)

    rc = _run_message_bus(serve=False, port=9400)

    assert rc == 0
    assert captured["bus"].auth_enabled is False
    assert "auth=disabled" in capsys.readouterr().out


def test_run_message_bus_invalid_env_raises_before_constructing(monkeypatch):
    """env 非法 → 直接抛异常，且不构造出"关闭鉴权"的 bus 照常返回 0。"""
    monkeypatch.setenv(SEAT_TOKENS_ENV, "not-json-at-all")
    captured = _capture_bus(monkeypatch)

    with pytest.raises(SeatCredentialsError):
        _run_message_bus(serve=False, port=9400)

    # 关键：坏配置下不得留下任何总线实例（否则就是 fail-open 的半启动状态）
    assert captured == {}


def test_run_message_bus_empty_object_env_raises(monkeypatch):
    """`{}` 同样启动失败（不放行成"全员 401"的总线）。"""
    monkeypatch.setenv(SEAT_TOKENS_ENV, "{}")
    captured = _capture_bus(monkeypatch)

    with pytest.raises(SeatCredentialsError):
        _run_message_bus(serve=False, port=9400)

    assert captured == {}


def test_selfcheck_print_contains_no_token(monkeypatch, capsys):
    """自检打印不得出现 token 明文。"""
    monkeypatch.setenv(SEAT_TOKENS_ENV, json.dumps({"tian_feng": SEKRET}))

    rc = _run_message_bus(serve=False, port=9400)

    out = capsys.readouterr().out
    assert rc == 0
    assert SEKRET not in out
    assert "auth=enabled seats=1" in out


def test_serve_print_contains_no_token(monkeypatch, capsys):
    """--serve 启动打印同样不得出现 token 明文，且带实际 bind 地址。"""
    monkeypatch.setenv(SEAT_TOKENS_ENV, json.dumps({"tian_feng": SEKRET}))
    monkeypatch.setattr("uvicorn.run", lambda *a, **kw: None)

    _run_message_bus(serve=True, port=9400)

    out = capsys.readouterr().out
    assert SEKRET not in out
    assert "bind=0.0.0.0:9400" in out
    assert "auth=enabled seats=1" in out


# ---------------- 端到端：--serve 真正启动（uvicorn 打桩） ----------------

def test_serve_calls_uvicorn_with_bus_app(monkeypatch):
    """--serve → uvicorn.run 被调用一次，app 由本次构造的 bus 产出，host/port 正确。"""
    monkeypatch.setenv(SEAT_TOKENS_ENV, json.dumps({"tian_feng": "tok-test"}))
    captured = _capture_bus(monkeypatch)
    _capture_app(monkeypatch, captured)

    calls: list = []
    monkeypatch.setattr("uvicorn.run", lambda *a, **kw: calls.append((a, kw)))

    rc = _run_message_bus(serve=True, port=9500)

    assert rc == 0
    assert len(calls) == 1
    args, kwargs = calls[0]
    app = args[0]
    # 1) 传给 uvicorn 的就是工厂为本次 bus 构造的应用（同一对象）
    assert app is captured["app"]
    # 2) 该应用属于本次构造的总线，且鉴权已启用
    assert captured["app_bus"] is captured["bus"]
    assert captured["bus"].auth_enabled is True
    # 3) 应用真的带上了总线路由与鉴权中间件（不是空壳）
    paths = {getattr(r, "path", None) for r in app.routes}
    assert {"/route", "/status", "/board", "/health"} <= paths
    # 4) 已启用鉴权 → 允许对外监听；端口仍是传入值
    assert kwargs["host"] == "0.0.0.0"
    assert kwargs["port"] == 9500


def test_serve_without_env_still_serves_but_auth_disabled(monkeypatch):
    """未配 env 时 --serve 仍可启动（既有行为），但打印必须如实写 auth=disabled。"""
    captured = _capture_bus(monkeypatch)
    _capture_app(monkeypatch, captured)
    calls: list = []
    monkeypatch.setattr("uvicorn.run", lambda *a, **kw: calls.append((a, kw)))

    rc = _run_message_bus(serve=True, port=9400)

    assert rc == 0
    assert len(calls) == 1
    assert captured["bus"].auth_enabled is False


# ---------------- bind 与鉴权状态联动（不得对外裸奔） ----------------

def test_resolve_bind_host_links_auth_state():
    """纯函数口径：启用鉴权→0.0.0.0；关闭鉴权→仅本机。"""
    assert _resolve_bind_host(True) == "0.0.0.0"
    assert _resolve_bind_host(False) == "127.0.0.1"


def test_serve_without_auth_binds_loopback_only(monkeypatch):
    """guard：鉴权关闭 + serve → 只绑 127.0.0.1（不允许对外监听）。"""
    calls: list = []
    monkeypatch.setattr("uvicorn.run", lambda *a, **kw: calls.append((a, kw)))

    rc = _run_message_bus(serve=True, port=9400)

    assert rc == 0
    assert len(calls) == 1
    assert calls[0][1]["host"] == "127.0.0.1"
    assert calls[0][1]["port"] == 9400


def test_serve_with_auth_binds_all_interfaces(monkeypatch):
    """鉴权启用 + serve → 可对外绑 0.0.0.0。"""
    monkeypatch.setenv(SEAT_TOKENS_ENV, json.dumps({"tian_feng": "tok-test"}))
    calls: list = []
    monkeypatch.setattr("uvicorn.run", lambda *a, **kw: calls.append((a, kw)))

    _run_message_bus(serve=True, port=9400)

    assert calls[0][1]["host"] == "0.0.0.0"


def test_serve_without_auth_never_prints_public_bind(monkeypatch, capsys):
    """防回退守卫：未配凭证时打印里不得出现 0.0.0.0（打印与实际 bind 一致）。

    任何人把它改回无条件 0.0.0.0，这条会红。
    """
    monkeypatch.setattr("uvicorn.run", lambda *a, **kw: None)

    _run_message_bus(serve=True, port=9400)

    out = capsys.readouterr().out
    assert "bind=127.0.0.1:9400" in out
    assert "0.0.0.0" not in out
    assert "auth=disabled" in out


def test_serve_without_auth_starts_normally_not_blocked(monkeypatch, capsys):
    """收紧 bind 不等于拦死启动：未配凭证 + serve 仍返回 0 并真的调 uvicorn。"""
    calls: list = []
    monkeypatch.setattr("uvicorn.run", lambda *a, **kw: calls.append((a, kw)))

    rc = _run_message_bus(serve=True, port=9411)

    assert rc == 0
    assert len(calls) == 1
    assert calls[0][1]["port"] == 9411
    assert "启动服务" in capsys.readouterr().out


def test_serve_goes_through_uvicorn_run(monkeypatch):
    """守卫：--serve 必须经 uvicorn.run 启动（不允许绕过它自行监听端口）。

    用"调用即失败"的桩替换 uvicorn.run：若实现另起监听路径，本用例会因
    没有异常而失败；正常实现下异常被原样抛出，证明唯一启动通道是 uvicorn。
    """

    def _explode(*a, **kw):  # pragma: no cover - 被调用即失败
        raise AssertionError("uvicorn.run 未被正确打桩：禁止在测试中启动真实服务")

    monkeypatch.setattr("uvicorn.run", _explode)
    with pytest.raises(AssertionError, match="uvicorn.run"):
        _run_message_bus(serve=True, port=9400)


# ---------------- 既有行为：main 不 serve 不启动服务 ----------------

def test_main_message_bus_without_serve_returns_zero(monkeypatch, capsys):
    """main(["--role","message_bus"]) 仍返回 0，且不启动 uvicorn。"""
    calls: list = []
    monkeypatch.setattr("uvicorn.run", lambda *a, **kw: calls.append((a, kw)))

    rc = main(["--role", "message_bus"])

    out = capsys.readouterr().out
    assert rc == 0
    assert calls == []
    # 既有文案保留（test_dead_letter.py 依赖"实例化成功"），并追加鉴权状态
    assert "实例化成功" in out
    assert "auth=disabled" in out


def test_main_message_bus_with_serve_passes_port(monkeypatch):
    """main 的 --serve/--port 真的传到 _run_message_bus（不是被忽略）。"""
    monkeypatch.setenv(SEAT_TOKENS_ENV, json.dumps({"tian_feng": "tok-test"}))
    calls: list = []
    monkeypatch.setattr("uvicorn.run", lambda *a, **kw: calls.append((a, kw)))

    rc = main(["--role", "message_bus", "--serve", "--port", "9488"])

    assert rc == 0
    assert len(calls) == 1
    assert calls[0][1]["port"] == 9488


def test_main_message_bus_invalid_env_raises(monkeypatch, capsys):
    """走 CLI 入口时坏配置同样抛异常（不是静默以关闭鉴权的总线启动）。"""
    monkeypatch.setenv(SEAT_TOKENS_ENV, "not-json")

    with pytest.raises(SeatCredentialsError):
        main(["--role", "message_bus"])

    # 坏配置下不得打印"启动成功/自检通过"一类字样
    out = capsys.readouterr().out
    assert "实例化成功" not in out
    assert "auth=" not in out
