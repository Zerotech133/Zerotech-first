# -*- coding: utf-8 -*-
# WO-OS-09 契约测试：/api/zhuge/brief 只读路由挂载（先红后绿）。
# 路由挂载验证不依赖真实 8091 进程（沿 test_api_tickets / test_portal_compat_regressions
# 判例：stub 子类捕获 _json / object.__new__(portal_api.Handler) 直调方法）。
# 运行：python -m pytest portal/test_api_zhuge_route.py -q
import importlib
import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

ROUTE_PATH = "/api/zhuge/brief"


def _load_module(name):
    return importlib.import_module(name)


def _make_handler():
    """构造 portal_api.Handler 实例（不起服务器）+ 捕获 _json 输出。

    沿 test_portal_compat_regressions 判例：object.__new__ 绕过
    BaseHTTPRequestHandler.__init__（其需要真实 socket）。
    """
    portal_api = _load_module("portal_api")
    seen = {}

    class _Stub(portal_api.Handler):
        def _json(self, obj, code=200):
            seen["obj"] = obj
            seen["code"] = code

    return object.__new__(_Stub), seen, portal_api


class _Resp:  # 简易响应记录（send_error 等裸协议方法捕获）
    pass


def _h_get(handler, path=ROUTE_PATH, qs=""):
    handler.path = path + ("?" + qs if qs else "")
    handler.do_GET()


# ── ① 路由已注册：GET 返回 200 + 引擎契约字段 ─────────────────────────
def test_get_returns_200_engine_contract():
    h, seen, _ = _make_handler()
    _h_get(h)
    assert seen.get("code") == 200, "GET /api/zhuge/brief 应 200"
    obj = seen["obj"]
    assert obj["engine"] == "rule-based"
    assert obj["model_invoked"] is False
    assert obj["dispatched"] is False
    assert isinstance(obj["suggestions"], list)
    assert isinstance(obj["gaps"], list)


# ── ② 六源全缺（stub 注入）→ gaps 覆盖六源且 note 以「待核」开头 ──────
def test_all_sources_missing_gaps_cover_six():
    import api_zhuge_route as zr
    out = zr.build_route_payload({k: (lambda: None) for k in zr.SOURCES})
    gaps = {g["source"] for g in out["gaps"]}
    assert gaps == {"os", "home", "alerts", "notify", "tasks", "capability"}
    for g in out["gaps"]:
        assert g["note"].startswith("待核")


# ── ③ 六源获取函数抛异常 → 按 None 降级进 gaps（不 500 不静默）───────
def test_fetcher_exception_degrades_to_gap():
    import api_zhuge_route as zr

    def _boom():
        raise RuntimeError("boom")

    out = zr.build_route_payload({
        "os": _boom, "home": lambda: None, "alerts": lambda: None,
        "notify": lambda: None, "tasks": lambda: None, "capability": lambda: None,
    })
    assert out["engine"] == "rule-based"  # 单源坏不拖垮整体
    assert any(g["source"] == "os" for g in out["gaps"])


# ── ④ 坏时钟参数（?clock=垃圾）→ 4xx + 错误 JSON（拒绝而非静默错）────
def test_bad_clock_param_rejected_4xx():
    h, seen, _ = _make_handler()
    _h_get(h, qs="clock=not-a-date")
    assert 400 <= seen.get("code", 0) < 500, "坏 clock 应 4xx"
    assert "error" in seen["obj"] or "ok" in seen["obj"]


# ── ⑤ 好时钟参数被接受（可回放口径：ISO 字符串）───────────────────────
def test_good_clock_param_accepted():
    h, seen, _ = _make_handler()
    _h_get(h, qs="clock=2026-10-21T09:00:00+08:00")
    assert seen.get("code") == 200
    assert seen["obj"]["business_day"] == "2026-10-21"


# ── ⑥ 非 GET 方法不被允许（POST/OPTIONS/PUT/DELETE → 405）───────────
def test_post_method_not_allowed_405():
    portal_api = _load_module("portal_api")
    seen = {}

    class _Stub(portal_api.Handler):
        def _json(self, obj, code=200):
            seen["obj"] = obj
            seen["code"] = code

        def send_error(self, code, message=None, explain=None):
            seen["code"] = code

    import io
    for verb in ("do_POST", "do_OPTIONS", "do_PUT", "do_DELETE"):
        h = object.__new__(_Stub)
        h.path = ROUTE_PATH
        h.headers = {}
        h.rfile = io.BytesIO(b"")
        getattr(h, verb)()
        assert seen.get("code") == 405, f"{verb} /api/zhuge/brief 应 405，得 {seen.get('code')}"


# ── ⑦ 路由源码零写操作纪律 ────────────────────────────────────────────
def test_route_source_has_no_write_ops():
    src = (HERE / "api_zhuge_route.py").read_text(encoding="utf-8")
    for banned in ("requests.post", "urllib.request", "subprocess",
                   "sqlite3.connect"):
        assert banned not in src, f"路由源码不得出现 {banned}"
    # open( 仅允许只读形态（路由层本应零文件 IO，六源函数各司其职）
    assert 'open(' not in src.replace('mode=ro', '')


# ── ⑧ 引擎 04 的 9 例不回退（引用守卫：模块同名加载）──────────────────
def test_engine_module_still_importable_and_pure():
    m = _load_module("api_zhuge_brief")
    out = m.build_brief({}, datetime(2026, 10, 21, 9, 0,
                                     tzinfo=m.BEIJING))
    assert out["engine"] == "rule-based" and out["suggestions"] == []
