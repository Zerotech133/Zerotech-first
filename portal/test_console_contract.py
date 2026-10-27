"""K-ORG-182：/console 离线入口、API 归属与执行健康契约。"""

import os
import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest


ROOT = Path(__file__).resolve().parents[1]
NGINX = ROOT / "portal" / "nginx.conf"
# 仅在显式指定本 worktree 内的源码/fixture 时核查浏览器逻辑；跨工单验证请复制到临时目录。
CONSOLE_SOURCE_ENV = "HERMES_CONSOLE_SOURCE"
PORTAL_API = "/api/flow/rerun"
HERMES_API = (
    "/console-api/api/purify/overview",
    "/console-api/api/pulse/state",
    "/console-api/api/pulse/act",
    "/console-api/api/alerts/queue",
    "/console-api/api/alerts/act",
    "/console-api/api/observe/state",
    "/console-api/api/execution/health",
    "/console-api/api/tickets/open",
    "/console-api/api/evolution/summary",
    "/console-api/api/gate/checkpoint",
    "/console-api/api/brief/latest",
    "/console-api/api/brief/list",
    "/console-api/api/event/agent_shadow_daily",
)


def _locations():
    text = re.sub(r"(?m)^\s*#.*$", "", NGINX.read_text(encoding="utf-8"))
    return [
        (kind or "", uri, body)
        for kind, uri, body in re.findall(
            r"location\s+(?:(=|\^~|~\*?|@)\s+)?(\S+)\s*\{([^{}]*)\}", text
        )
    ]


def _route(path):
    locations = _locations()
    exact = [loc for loc in locations if loc[0] == "=" and loc[1] == path]
    if exact:
        return exact[0]
    prefixes = sorted(
        (loc for loc in locations if loc[0] in ("", "^~") and path.startswith(loc[1])),
        key=lambda loc: len(loc[1]),
        reverse=True,
    )
    if prefixes and prefixes[0][0] == "^~":
        return prefixes[0]
    for loc in locations:
        if loc[0].startswith("~") and re.search(loc[1], path):
            return loc
    return prefixes[0] if prefixes else None


def _upstream(path):
    loc = _route(path)
    assert loc is not None, f"无 nginx 路由：{path}"
    match = re.search(r"\bproxy_pass\s+https?://[^;]*?:(\d+)", loc[2])
    assert match, f"{path} 命中 {loc[1]}，却没有显式上游端口"
    return int(match.group(1))


@pytest.fixture
def console_source():
    source = os.environ.get(CONSOLE_SOURCE_ENV)
    if not source:
        pytest.skip(
            f"未指定 {CONSOLE_SOURCE_ENV}：跳过浏览器源码契约检查；如需验证，请将该变量设为本 worktree 内的源码文件路径"
        )
    path = Path(source).resolve()
    assert path.is_relative_to(ROOT), f"只允许本 worktree 内的显式源码：{path}"
    assert path.is_file(), f"Hermes console 源码缺失：{path}"
    return path.read_text(encoding="utf-8")


def _function(source, name):
    match = re.search(
        r"(?:async\s+)?function\s+" + re.escape(name) + r"\s*\([^)]*\)\s*\{", source
    )
    assert match, f"缺少 {name} 浏览器逻辑"
    # 页面各顶层函数之间以空行分隔；不将相邻函数的 token/判断算进来。
    end = source.find("\n\n", match.end())
    assert end >= 0, f"无法确认 {name} 的函数边界"
    return source[match.start() : end]


def test_portal_api_stays_on_8091():
    for path in (
        "/api/health",
        "/api/execution/health",
        "/api/platform/observe",
        PORTAL_API,
        "/api/audit_chat",
    ):
        assert _upstream(path) == 8091, path


def test_console_and_connector_paths_are_explicitly_routed_to_8787():
    assert _upstream("/console") == 8787
    for path in HERMES_API:
        loc = _route(path)
        assert loc is not None, f"缺少独立 Hermes connector 路由：{path}"
        assert loc[0] == "~" and loc[1].startswith("^/console-api(/api/"), path
        assert re.fullmatch(loc[1], path), f"连接器必须匹配白名单：{path}"
        assert _upstream(path) == 8787, path
        assert re.search(
            r"\brewrite\s+\^/console-api\(/api/\.\*\)\$\s+\$1\s+break\s*;", loc[2]
        ), "Hermes 后端只接收 /api/*，/console-api/api/* 必须剥离 /console-api 前缀"
        assert re.search(r'\bproxy_set_header\s+X-Portal-Token\s+""\s*;', loc[2]), (
            "连接器不得向 Hermes 透传 Portal token"
        )
    for path in (
        "/console-api/api/flow/rerun",
        "/console-api/api/unknown",
        "/console-api/unknown",
    ):
        fallback = _route(path)
        assert fallback is not None and fallback[1] == "/console-api/", (
            f"非白名单路径未被隔离：{path}"
        )
        assert re.search(r"\breturn\s+404\s*;", fallback[2]), (
            f"非白名单路径必须拒绝：{path}"
        )


def test_browser_calls_follow_api_ownership(console_source):
    assert re.search(
        r'\bconst\s+hc\s*=\s*path\s*=>\s*["\']/console-api/api/["\']\s*\+\s*path',
        console_source,
    )
    assert re.search(
        r'\bconst\s+pc\s*=\s*path\s*=>\s*["\']/api/["\']\s*\+\s*path', console_source
    )
    calls = re.findall(
        r'\bapi\(\s*(hc|pc)\(\s*["\']([^"\']+)["\']\s*\)', console_source
    )
    assert calls, "console 没有可审计的 API 调用"
    paths = [
        urlsplit(("/console-api/api/" if owner == "hc" else "/api/") + call).path
        for owner, call in calls
    ]
    assert ("pc", "flow/rerun") in calls, "重跑必须经 8091 /api/flow/rerun"
    for (owner, call), path in zip(calls, paths):
        if owner == "pc":
            assert path == PORTAL_API, f"非重跑请求不得走 Portal API：{call}"
            assert _upstream(path) == 8091
        else:
            assert path.startswith("/console-api/api/"), call
            assert _upstream(path) == 8787, f"console 调用 {call} 被错误转发"
    assert any("/pulse/" in p for p in paths)
    assert any("/alerts/" in p for p in paths)
    assert re.search(r'\bapi\(hc\(["\']event/["\']\s*\+\s*\w+\)\)', console_source)
    assert _upstream("/console-api/api/event/agent_shadow_daily") == 8787


@pytest.mark.parametrize("action", ["pulse", "alerts"])
def test_hermes_mutations_require_session_bound_csrf(action, console_source):
    assert re.search(
        r'\bapi\(hc\(["\']' + action + r'/act["\']\)\s*,', console_source
    ), f"{action} 写动作必须走 Hermes connector"
    helper = _function(console_source, "api")
    csrf = _function(console_source, "hermesCsrf")
    assert re.search(r'\bfetch\(["\']/hermes/["\']', csrf)
    assert re.search(r"\bcsrfToken\b", csrf) and "[0-9a-f]{64}" in csrf, (
        "CSRF 必须来自 Hermes 认证会话，而非固定 token"
    )
    assert re.search(
        r'\bconnector\s*=\s*path\.startsWith\(["\']/console-api/api/["\']\)', helper
    )
    assert re.search(
        r"if\s*\(body\s*&&\s*connector\)\s*\{[^{}]*hermesCsrf\(\)[^{}]*"
        r"X-Hermes-CSRF-Token[^{}]*\}",
        helper,
    ), "只给 Hermes 写请求附加会话 CSRF"
    assert re.search(r'\bmethod\s*:\s*["\']POST["\']', helper)
    assert re.search(r"\bfetch\s*\(path\s*,\s*opt\)", helper)
    assert re.search(r'credentials\s*:\s*["\']same-origin["\']', helper)
    assert not re.search(r'credentials\s*:\s*["\'](?:omit|include)["\']', helper)


def test_portal_rerun_posts_without_hermes_csrf(console_source):
    assert re.search(r'\bapi\(pc\(["\']flow/rerun["\']\)\s*,', console_source)
    helper = _function(console_source, "api")
    assert re.search(r"if\s*\(body\s*&&\s*connector\)", helper)
    assert not re.search(r'\bapi\(hc\(["\']flow/rerun["\']', console_source)


def test_execution_overview_rejects_unverified_green(console_source):
    source = _function(console_source, "loadExecution")
    assert re.search(
        r"const\s+complete\s*=\s*d\.ps_ok\s*===\s*true\s*&&\s*"
        r"d\.manifest_ok\s*===\s*true",
        source,
    ), "轮询与 manifest 必须明确为 true"
    for condition in (
        r"Number\.isInteger\(d\.manifest_count\)",
        r"d\.manifest_count\s*>\s*0",
        r"tasks\.length\s*===\s*d\.manifest_count",
        r"counts\.every\(k\s*=>\s*Number\.isInteger\(s\[k\]\)",
        r"counts\.reduce\([^;]+\)\s*===\s*tasks\.length",
    ):
        assert re.search(condition, source), f"执行摘要缺少完整性条件：{condition}"
    assert re.search(
        r"const\s+healthy\s*=\s*complete\s*&&\s*s\.ok\s*>\s*0\s*&&\s*!worst", source
    )
    assert re.search(r"if\s*\(\s*!complete\s*\)\s*\{[\s\S]*?\breturn\s*;", source)
    assert re.search(
        r'if\s*\(healthy\)\s*\{\s*badge\(\$\(["\']exb["\']\),["\']ok["\']', source
    )
    assert re.search(
        r'if\s*\(healthy\)\s*\{\s*badge\(\$\(["\']lt-ex["\']\),["\']ok["\']', source
    )
