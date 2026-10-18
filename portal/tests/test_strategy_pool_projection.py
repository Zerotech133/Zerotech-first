"""Isolated fail-closed strategy-pool projection and real HTTP router tests."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlencode

import pytest


PORTAL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PORTAL))
from strategy_pool_projection import ProjectionQueryError, project_membership


VALID = {"pool": "A", "strategy_name": "approved-only-name", "strategy_version": "v1",
         "as_of": "2026-10-18", "market": "SZ", "code": "000001"}


def test_no_trusted_source_is_always_empty() -> None:
    """Never infer live memberships from a syntactically valid request."""
    result = project_membership(urlencode(VALID))
    assert result["status"] == "SOURCE_UNVERIFIED"
    assert result["reason_code"] == "TRUSTED_SOURCE_UNAVAILABLE"
    assert result["coverage"] == {"verified": False, "reason_code": "TRUSTED_SOURCE_UNAVAILABLE"}
    assert result["verified_memberships"] == result["events"] == result["unverified_observations"] == []
    assert result["market"] == "SZ" and result["code"] == "000001"
    assert project_membership(urlencode({key: value for key, value in VALID.items()
                                         if key not in ("market", "code")}))["code"] is None


@pytest.mark.parametrize("query", [
    "", "pool=B&strategy_name=x&strategy_version=v1&as_of=2026-10-18",
    "pool=A&strategy_name=x&strategy_version=v1&as_of=2026-10-18&market=SZ",
    "pool=A&strategy_name=x&strategy_version=v1&as_of=2026-10-18&code=000001",
    "pool=A&strategy_name=x&strategy_version=v1&as_of=2026-10-18&extra=1",
    "pool=A&pool=A&strategy_name=x&strategy_version=v1&as_of=2026-10-18",
    "pool=A&strategy_name=&strategy_version=v1&as_of=2026-10-18",
    "pool=A&strategy_name=x&strategy_version=v1&as_of=2026-02-30",
    "pool=A&strategy_name=x&strategy_version=v1&as_of=2026-10-18T00%3A00%3A00",
    "pool=A&strategy_name=x&strategy_version=v1&as_of=2026-10-18&market=SZ&code=%3Cimg%3E",
    "pool=A&strategy_name=x&strategy_version=v1&as_of=2026-10-18&market=SZ&code=000001&code=000002",
    "pool=A&strategy_name=x&strategy_version=v1&as_of=2026-10-18&market=SZ&code=%FF",
    "pool=A&strategy_name=x&strategy_version=v1&as_of=2026-10-18&market=SZ&code=000001&",
])
def test_reject_invalid_or_cross_pool_query(query: str) -> None:
    """Unexpected scope or malformed identity fails rather than broadening."""
    with pytest.raises(ProjectionQueryError):
        project_membership(query)


def test_as_of_explicit_instant() -> None:
    """A timezone-qualified timestamp remains an as-of filter, not a receipt."""
    request = {**VALID, "as_of": "2026-10-18T15:00:00+08:00"}
    result = project_membership(urlencode(request))
    assert result["as_of"] == "2026-10-18T15:00:00+08:00"
    assert result["verified_memberships"] == []


_HTTP_PROBE = r'''
import hashlib
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading

repo = Path(sys.argv[1])
sandbox = Path(sys.argv[2]).resolve()
sys.path[:0] = [str(repo / 'portal'), str(repo / 'quant_backtest')]
import api_common
import job_spec_registry
original = job_spec_registry.JobSpecRegistry
catalog = sandbox / 'data' / 'runtime' / 'minute_job_specs.json'
default = repo / 'data' / 'runtime' / 'minute_job_specs.json'
def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
before = fingerprint(default)
class GuardedRegistry(original):
    def __init__(self, catalog_path):
        if Path(catalog_path).resolve() != catalog:
            raise AssertionError('production catalog read attempted')
        super().__init__(catalog_path)
    def _save(self):
        if Path(self.catalog_path).resolve() != catalog:
            raise AssertionError('production catalog write attempted')
        super()._save()
api_common.STATION = str(sandbox)
api_common.REPORTS_DIR = str(sandbox / 'reports')
api_common._ERROR_LOG = str(sandbox / 'errors.log')
job_spec_registry.JobSpecRegistry = GuardedRegistry
import portal_api
portal_api._ACCESS_LOG = str(sandbox / 'access.log')
server = ThreadingHTTPServer(('127.0.0.1', 0), portal_api.Handler)
server.daemon_threads = True
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
try:
    base = '/api/watch-tower/strategy-pool/membership'
    good = '?pool=A&strategy_name=real-name&strategy_version=v1&as_of=2026-10-18&market=SZ&code=000001'
    for method, path, expected in [('GET', base + good, 200),
                                   ('GET', base + '?pool=B&strategy_name=x&strategy_version=v1&as_of=2026-10-18', 400),
                                   ('GET', base + '?pool=A', 400),
                                   ('POST', base + good, 404)]:
        connection = http.client.HTTPConnection('127.0.0.1', server.server_address[1], timeout=10)
        try:
            connection.request(method, path, body=b'' if method == 'POST' else None)
            response = connection.getresponse()
            body = response.read()
            assert response.status == expected, (method, path, response.status, body)
            if method == 'GET':
                assert response.getheader('Cache-Control') == 'no-store'
                assert response.getheader('Content-Type').startswith('application/json')
                obj = json.loads(body)
                if expected == 200:
                    assert obj['coverage']['verified'] is False
                    assert obj['reason_code'] == 'TRUSTED_SOURCE_UNAVAILABLE'
                    assert obj['verified_memberships'] == obj['events'] == obj['unverified_observations'] == []
                else:
                    assert obj['error'] == 'invalid_strategy_pool_query'
        finally:
            connection.close()
    assert fingerprint(default) == before
finally:
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)
    assert not thread.is_alive()
'''


def test_real_http_router_isolated(tmp_path: Path) -> None:
    """Route through actual HTTP while guarding the production JobSpec root."""
    result = subprocess.run(
        [sys.executable, "-c", _HTTP_PROBE, str(PORTAL.parent), str(tmp_path)],
        env={**os.environ, "PORTAL_NO_WARMUP": "1", "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
