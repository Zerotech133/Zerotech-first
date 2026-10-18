"""门户模块拆分后的兼容回归测试。"""

import importlib
import sys
from pathlib import Path


PORTAL_DIR = Path(__file__).resolve().parent
if str(PORTAL_DIR) not in sys.path:
    sys.path.insert(0, str(PORTAL_DIR))


def test_handler_back_bar_uses_bound_instance_method():
    portal_api = importlib.import_module("portal_api")
    handler = object.__new__(portal_api.Handler)

    html = handler._back_bar_html()

    assert "href='/portal'" in html
    assert "← 返回" in html


def test_portal_api_exports_sa_history_reader_for_fin_page_injection():
    portal_api = importlib.import_module("portal_api")

    assert callable(portal_api._sa_hist_read)


def test_console_connector_proxy_does_not_capture_portal_api():
    import re

    config = (PORTAL_DIR / "nginx.conf").read_text(encoding="utf-8")
    location = re.search(r"location ~ (\^/console-api[^\n]+) \{(.*?)\n    \}", config, re.S)
    assert location is not None
    pattern, proxy = location.groups()
    assert "proxy_pass http://$h:8787;" in proxy
    assert 'proxy_set_header X-Portal-Token "";' in proxy
    assert "proxy_set_header Host $http_host;" in proxy
    assert "rewrite ^/console-api(/api/.*)$ $1 break;" in proxy
    for path in ("purify/overview", "pulse/state", "pulse/act", "alerts/queue",
                 "alerts/act", "observe/state", "execution/health", "tickets/open",
                 "evolution/summary", "gate/checkpoint", "brief/latest", "brief/list",
                 "event/watchdog"):
        assert re.fullmatch(pattern, "/console-api/api/" + path)
    for path in ("flow/rerun", "health", "auth/status", "pulse/act/other"):
        assert not re.fullmatch(pattern, "/console-api/api/" + path)
    assert "location /console-api/ { return 404; }" in config
    assert re.search(r"location /api/ \{[^}]*proxy_pass http://\$h:8091;", config)


def test_console_page_only_sends_hermes_connector_calls_through_console_api():
    page = (PORTAL_DIR.parent / "Hermes/hermes-webui-next/static/console.html").read_text(encoding="utf-8")
    assert 'const hc=path=>"/console-api/api/"+path' in page
    assert 'const pc=path=>"/api/"+path' in page
    assert 'api(pc("flow/rerun")' in page
    assert 'api(hc("pulse/act")' in page
    assert 'api(hc("alerts/act")' in page
    assert 'opt.headers["X-Hermes-CSRF-Token"]=csrfToken' in page
    assert 'if(body&&connector)' in page
    assert 'cache:"no-store",credentials:"same-origin"' in page
    assert 'if(r.status===403&&body&&connector)csrfToken=""' in page
    assert 'd.ps_ok===true&&d.manifest_ok===true' in page
    assert 'tasks.length===d.manifest_count' in page
    assert 'counts.reduce((n,k)=>n+s[k],0)===tasks.length' in page
    assert 'counts.every(k=>tasks.filter(t=>t.status===k).length===s[k])' in page
    assert 'if(!complete)' in page
    assert 'if(healthy){badge($("lt-ex"),"ok","全绿")' in page
    assert 'api("/api/' not in page
    for path in ("purify/overview", "pulse/state", "pulse/act", "alerts/queue",
                 "alerts/act", "observe/state", "execution/health", "tickets/open",
                 "evolution/summary", "gate/checkpoint", "brief/latest", "brief/list"):
        assert f'api(hc("{path}")' in page
    assert 'api(hc("event/"+s))' in page
    assert page.count('<section id="tab') == 5


def test_console_execution_lamp_fails_visible_for_incomplete_data():
    import pytest

    playwright = pytest.importorskip("playwright.sync_api")
    try:
        with playwright.sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.route("**/console-api/api/**", lambda route: route.fulfill(
                    status=200, content_type="application/json", body="{}"))
                html = (PORTAL_DIR.parent / "Hermes/hermes-webui-next/static/console.html").read_text(encoding="utf-8")
                page.route("http://console.test/console", lambda route: route.fulfill(
                    status=200, content_type="text/html", body=html))
                page.goto("http://console.test/console")
                payload = {
                    "ps_ok": True, "manifest_ok": True, "manifest_count": 1,
                    "tasks": [{"name": "TodaysPicks", "status": "ok"}],
                    "summary": {"ok": 1, "fail": 0, "stale": 0, "never": 0,
                                "disabled": 0, "worst": None},
                }
                for change, expected in (
                    ({}, "ok"), ({"ps_ok": False}, "bad"),
                    ({"manifest_ok": False}, "bad"),
                    ({"summary": {}}, "bad"),
                    ({"manifest_count": 0}, "bad"),
                    ({"summary": {**payload["summary"], "ok": 0}}, "bad"),
                ):
                    response = {**payload, **change}
                    page.evaluate("data => { window.fetch = async () => ({status:200, json:async () => data}); }", response)
                    page.evaluate("async () => { await loadExecution(); }")
                    assert expected in page.locator("#lt-ex").get_attribute("class")
                    assert expected in page.locator("#exb").get_attribute("class")
                    if expected == "bad":
                        assert "全绿" not in page.locator("#lt-ex-body").inner_text()
            finally:
                browser.close()
    except playwright.Error as exc:
        pytest.skip(f"Chromium unavailable: {exc}")


def test_console_csrf_only_accompanies_connector_posts():
    import pytest

    playwright = pytest.importorskip("playwright.sync_api")
    try:
        with playwright.sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                html = (PORTAL_DIR.parent / "Hermes/hermes-webui-next/static/console.html").read_text(encoding="utf-8")
                token = "a" * 64
                requests = []

                def respond(route):
                    request = route.request
                    if request.method == "POST":
                        requests.append((request.url, request.headers))
                    body = ('<script>window.__HERMES_CONFIG__={csrfToken:"' + token + '"}</script>'
                            if request.url.endswith("/hermes/") else "{}")
                    route.fulfill(status=200, content_type="text/html" if request.url.endswith("/hermes/")
                                  else "application/json", body=body)

                page.route("http://console.test/**", respond)
                page.route("http://console.test/console", lambda route: route.fulfill(
                    status=200, content_type="text/html", body=html))
                page.goto("http://console.test/console")
                page.evaluate('async () => { await api(hc("pulse/act"), {action:"run_task"}); '
                              'await api(hc("alerts/act"), {action:"ack_all"}); '
                              'await api(pc("flow/rerun"), {task:"TodaysPicks"}); }')
                assert len(requests) == 3
                for url, headers in requests[:2]:
                    assert "/console-api/api/" in url
                    assert headers.get("x-hermes-csrf-token") == token
                assert requests[2][0].endswith("/api/flow/rerun")
                assert "x-hermes-csrf-token" not in requests[2][1]
                assert all("x-portal-token" not in headers for _, headers in requests)
                page.route("http://console.test/hermes/", lambda route: route.fulfill(
                    status=200, content_type="text/html", body="<html>login</html>"))
                page.evaluate('() => { csrfToken=""; }')
                assert page.evaluate('async () => { try { await api(hc("pulse/act"), {}); '
                                     'return false; } catch(e) { return true; } }')
                assert len(requests) == 3
            finally:
                browser.close()
    except playwright.Error as exc:
        pytest.skip(f"Chromium unavailable: {exc}")
