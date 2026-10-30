"""AST隔离新路由，禁止整体导入门户的认证初始化与外发功能。"""

import ast
from io import BytesIO
from pathlib import Path


def handler(methods):
    path = Path(__file__).with_name("portal_api.py")
    tree = ast.parse(path.read_text(encoding="utf8"))
    cls = next(
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Handler"
    )
    selected = [
        n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in methods
    ]
    ns = {
        "_MUTATION_POSTS": frozenset({"/api/data/update-plan", "/api/data/update"}),
        "_ACTION_GETS": frozenset(),
        # K-STOCK-185b：频控符号随隔离 exec 一并打桩（恒放行，测派发不测限流）
        "_rate_hit": lambda key, max_per_min: False,
        "_WRITE_MAX_PER_MIN": 12,
    }
    exec(  # nosec B102 - 测试隔离加载 portal_api（无外部输入）
        compile(
            ast.fix_missing_locations(
                ast.Module(
                    body=[
                        ast.ClassDef(
                            name="Probe",
                            bases=[],
                            keywords=[],
                            body=selected,
                            decorator_list=[],
                        )
                    ],
                    type_ignores=[],
                )
            ),
            str(path),
            "exec",
        ),
        ns,
    )
    obj = ns["Probe"]()
    obj.responses = []
    obj._json = lambda value, code=200: obj.responses.append((code, value))
    obj._check_portal_token = lambda: True
    return obj, ns


def test_post_route_dispatches_only_bounded_json_body():
    obj, _ = handler({"do_POST"})
    obj.path = "/api/data/update-plan"
    obj.headers = {"Content-Length": "2", "Content-Type": "application/json"}
    obj.rfile = BytesIO(b"{}")
    seen = []
    obj._warehouse_update_request = lambda method, path, qs="", raw=b"": seen.append(
        (method, path, raw)
    )
    obj.do_POST()
    assert seen == [("POST", "/api/data/update-plan", b"{}")]


def test_oversize_body_rejected_before_read_or_launch():
    obj, _ = handler({"do_POST"})
    obj.path = "/api/data/update"
    obj.headers = {"Content-Length": "65537", "Content-Type": "application/json"}

    class NoRead:
        def read(self, *args):
            raise AssertionError("超过上限不能读取正文")

    obj.rfile = NoRead()
    obj.do_POST()
    assert obj.responses[0][0] == 413


def test_get_daily_routes_without_calling_old_sync_actions():
    obj, _ = handler({"do_GET"})
    obj.path = "/api/data/daily?date=2026-10-09"
    calls = []
    obj._warehouse_update_request = lambda method, path, qs="", raw=b"": calls.append(
        (method, path, qs)
    )
    obj.do_GET()
    assert calls == [("GET", "/api/data/daily", "date=2026-10-09")]


def test_mutation_endpoints_are_in_existing_auth_set():
    tree = ast.parse(
        Path(__file__).with_name("portal_api.py").read_text(encoding="utf8")
    )
    value = next(
        n.value
        for n in tree.body
        if isinstance(n, ast.Assign)
        and any(
            isinstance(t, ast.Name) and t.id == "_MUTATION_POSTS" for t in n.targets
        )
    )
    strings = {
        n.value
        for n in ast.walk(value)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }
    assert {"/api/data/update-plan", "/api/data/update"} <= strings
