# -*- coding: utf-8 -*-
"""R5-A 单测：P0-E 数据治理四端点 /api/data/governance/*（只读）。

用 FakeHandler 记录 _json 写出，不起真实服务、不碰 8091、不发 POST；
数据源一律 monkeypatch 到临时目录 / 假 loader，断言不依赖真实报告快照。
覆盖 impl_round5_contract_20261106.md §2.3 / §2.5 的全部验收点：
  4 端点正常信封 / 文件缺失与损坏 → 503 / 参数非法 → 400 /
  无绝对路径与 store|manifest 外泄 / portal_api.py 接线恰好 1 处 /
  模块源码无写盘·subprocess·requests / 缓存命中不刷新 generated_at。
"""
from __future__ import annotations

import io
import json
import re
import sys
from pathlib import Path

import pytest

PORTAL = Path(__file__).resolve().parents[1]
if str(PORTAL) not in sys.path:
    sys.path.insert(0, str(PORTAL))
import api_data_governance as mod  # noqa: E402

PORTAL_API_PY = PORTAL / "portal_api.py"
MODULE_PY = Path(mod.__file__)

ENVELOPE_KEYS = ("ok", "endpoint", "generated_at", "source", "total", "items", "cached")
WIN_ABS_RE = re.compile(r"[A-Za-z]:\\")
POSIX_ABS = "/c/aios"


# ── BaseHTTPRequestHandler 替身：只记录 _json 写出，不碰 socket ──
class _RecordingWFile:
    def __init__(self):
        self._buf = io.BytesIO()

    def write(self, data):
        return self._buf.write(data)

    def getvalue(self):
        return self._buf.getvalue()


class FakeHandler:
    def __init__(self):
        self.wfile = _RecordingWFile()
        self.payload = None
        self.code = None

    def _json(self, obj, code=200):
        # allow_nan=False：任何 NaN/Inf 都会在这里炸出来，不允许漏到生产响应。
        body = json.dumps(obj, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.payload = obj
        self.code = code
        self.wfile.write(body)

    @property
    def text(self):
        return self.wfile.getvalue().decode("utf-8")


def _get(path, qs=""):
    h = FakeHandler()
    mod.handle_get(h, path, qs)
    return h


@pytest.fixture(autouse=True)
def _clear_cache():
    """每个用例前清进程内缓存，避免 TTL 缓存串味。"""
    mod._CACHE.clear()
    yield
    mod._CACHE.clear()


# ── 假数据源 ──────────────────────────────────────────────────
FAKE_SLA = {
    "source": "tools/data_hub/govern.py::audit()",
    "today": "2026-11-06",
    "scope": "stale_only",
    "by_grade": {"stopped": 0, "broken": 1, "lagging": 1},
    "grade_counts": {"stopped": 0, "broken": 1, "lagging": 1},
    "scale": {"sources": 31, "catalog_tables": 258,
              "physical_tables": 306, "undated_tables": 25},
    "skipped_by_lifecycle": 22,
    "note": mod.SLA_NOTE,
    "batch_profile_note": mod.BATCH_NOTE,
    "items": [
        {"table": "ts_delisted", "source": "xtcloud", "freq": "D",
         "freshness": "2026-08-01", "lag_days": 97, "sla_days": 3, "grade": "broken"},
        {"table": "ts_fut_weekly", "source": "xtcloud", "freq": "W",
         "freshness": "2026-10-20", "lag_days": 17, "sla_days": 7, "grade": "lagging"},
    ],
}

FAKE_SNAPSHOT = {
    "generated_at": "2026-11-05T13:17:56+08:00",
    "today": "2026-11-05",
    "probed": 0,
    "probe_hits": 22,
    "errors": [],
    "rows": [
        {"table": "adj_factor", "source": "tushare", "freq": "D", "verdict": "OK",
         "lag_days": 1, "sla_days": 3, "local": "2026-11-04", "remote_latest": None,
         "sampled": False, "present": True, "reason": "", "remote_rows": 120,
         "store": "C:/SECRET/ABS/store", "manifest": "C:/SECRET/ABS/m.json"},
        {"table": "ts_delisted", "source": "xtcloud", "freq": "D", "verdict": "GONE",
         "lag_days": 97, "sla_days": 3, "local": "2026-08-01", "remote_latest": None,
         "sampled": True, "present": False, "reason": "远端下架", "remote_rows": None},
        {"table": "shibor", "source": "xtcloud", "freq": "D", "verdict": "CALIBER",
         "lag_days": 2, "sla_days": 3, "local": "2026-11-03", "remote_latest": "2026-11-04",
         "sampled": False, "present": True, "reason": "口径差", "remote_rows": 900},
    ],
    "summary": {
        "by_verdict": {"OK": 1, "GONE": 1, "CALIBER": 1},
        "per_source": {
            "tushare": {"OK": 1, "_name": "Tushare Pro", "_status": "verified",
                        "_total": 1, "_conclusion": "可用"},
            "xtcloud": {"GONE": 1, "CALIBER": 1, "_name": "通达信行情云",
                        "_status": "verified", "_total": 2, "_conclusion": "部分失效"},
        },
        "tables": 3,
        "sources": 2,
    },
}

FAKE_CATALOG = {
    "version": 43,
    "updated": "2026-11-05T13:19:31",
    "tables": {
        "dknife_snap": {
            "source": "xtcloud", "freq": "D", "lifecycle": "deprecated",
            "lifecycle_reason": "上游下架，恢复需远端复挂。",
            "store": "C:/SECRET/ABS/store", "manifest": "C:/SECRET/ABS/manifest.json"},
        "hk_option_plan": {
            "source": "xtcloud", "freq": "D", "lifecycle": "planned",
            "lifecycle_reason": "待接采集。",
            "store": "C:/SECRET/ABS/store2", "manifest": "C:/SECRET/ABS/manifest2.json"},
        "ts_daily": {"source": "xtcloud", "freq": "D"},   # 无 lifecycle → 不进 items
    },
}

FAKE_SOURCES = {
    "source": "tools/data_hub/sources.yaml + registry.summary()",
    "total_exact_note": "total = sources.yaml 登记源数（registry.summary().total）。",
    "by_domain": {"quote": 8, "alt": 3},
    "by_status": {"verified": 2, "planned": 1},
    "validate_problems": 0,
    "summary_total": 3,
    "items": [
        {"id": "xtcloud", "name": "通达信行情云", "domain": "quote", "license": "commercial",
         "freq": "D", "proxy_policy": "auto", "status": "verified",
         "adapter": "parquet", "covers": ["行情"], "linked_tables": ["ts_daily"],
         "note": "主行情源"},
        {"id": "arxiv", "name": "arXiv", "domain": "alt", "license": "open_license",
         "freq": "D", "proxy_policy": "auto", "status": "verified",
         "adapter": "http_json", "covers": ["论文元数据"], "linked_tables": [], "note": None},
        {"id": "cnopendata", "name": "CnOpenData", "domain": "alt",
         "license": "commercial", "freq": "Q", "proxy_policy": "none",
         "status": "planned", "adapter": "http_json", "covers": [], "linked_tables": [],
         "note": "待接"},
    ],
}


@pytest.fixture
def upstream_dir(tmp_path, monkeypatch):
    """临时快照目录 + 假 upstream_health_*.json（含需剔除的 store/manifest）。"""
    d = tmp_path / "data_hub"
    d.mkdir()
    p = d / "upstream_health_20261105_1317.json"
    p.write_text(json.dumps(FAKE_SNAPSHOT, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(mod, "UPSTREAM_DIRS", (d,))
    return d


@pytest.fixture
def catalog_file(tmp_path, monkeypatch):
    p = tmp_path / "catalog.json"
    p.write_text(json.dumps(FAKE_CATALOG, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(mod, "CATALOG_PATH", p)
    return p


@pytest.fixture
def fake_sla(monkeypatch):
    mod._CACHE.clear()
    monkeypatch.setattr(mod, "_sla_loader", lambda: json.loads(json.dumps(FAKE_SLA)))


@pytest.fixture
def fake_sources(monkeypatch):
    mod._CACHE.clear()
    monkeypatch.setattr(mod, "_sources_loader", lambda: json.loads(json.dumps(FAKE_SOURCES)))


# ── 1. 四端点正常路径 ─────────────────────────────────────────
def test_sla_normal(fake_sla):
    h = _get("/api/data/governance/sla")
    assert h.code == 200
    p = h.payload
    for key in ENVELOPE_KEYS:
        assert key in p, key
    assert p["ok"] is True
    assert p["endpoint"] == "/api/data/governance/sla"
    assert p["total"] == 2
    assert p["cached"] is False
    assert p["scope"] == "stale_only"
    assert p["today"] == "2026-11-06"
    assert p["by_grade"] == {"stopped": 0, "broken": 1, "lagging": 1}
    assert p["scale"]["catalog_tables"] == 258
    assert p["skipped_by_lifecycle"] == 22
    assert set(p["items"][0]) == {"table", "source", "freq", "freshness",
                                  "lag_days", "sla_days", "grade"}


def test_sla_grade_filter_and_paging(fake_sla):
    h = _get("/api/data/governance/sla", "grade=broken")
    assert h.code == 200
    assert h.payload["total"] == 1
    assert h.payload["items"][0]["grade"] == "broken"
    h2 = _get("/api/data/governance/sla", "limit=1")
    assert h2.code == 200
    assert h2.payload["total"] == 2 and h2.payload["returned"] == 1


def test_upstream_normal(upstream_dir):
    h = _get("/api/data/governance/upstream-health")
    assert h.code == 200
    p = h.payload
    for key in ENVELOPE_KEYS:
        assert key in p, key
    assert p["endpoint"] == "/api/data/governance/upstream-health"
    assert p["source"] == "upstream_health_20261105_1317.json"   # 只放文件名
    assert p["source_generated_at"] == "2026-11-05T13:17:56+08:00"
    assert p["snapshot_today"] == "2026-11-05"
    assert p["total"] == 3
    assert p["by_verdict"] == {"OK": 1, "GONE": 1, "CALIBER": 1}
    assert p["by_state"] == {"ACTIVE": 1, "STALLED": 0, "GONE": 1, "UNDECIDABLE": 1}
    assert "total_exact_note" in p and "估算下限" in p["total_exact_note"]
    states = {it["table"]: it["state"] for it in p["items"]}
    assert states == {"adj_factor": "ACTIVE", "ts_delisted": "GONE", "shibor": "UNDECIDABLE"}


def test_upstream_state_and_verdict_filter(upstream_dir):
    h = _get("/api/data/governance/upstream-health", "state=GONE")
    assert h.code == 200 and h.payload["total"] == 1
    h2 = _get("/api/data/governance/upstream-health", "verdict=CALIBER")
    assert h2.code == 200 and h2.payload["items"][0]["table"] == "shibor"
    h3 = _get("/api/data/governance/upstream-health", "source=xtcloud")
    assert h3.code == 200 and h3.payload["total"] == 2


def test_lifecycle_normal(catalog_file):
    h = _get("/api/data/governance/lifecycle")
    assert h.code == 200
    p = h.payload
    for key in ENVELOPE_KEYS:
        assert key in p, key
    assert p["endpoint"] == "/api/data/governance/lifecycle"
    assert p["source"] == "quant_backtest/data/catalog.json"
    assert p["total"] == 2
    assert p["catalog_version"] == 43
    assert p["catalog_tables"] == 3
    assert p["by_lifecycle"] == {"deprecated": 1, "planned": 1}
    for it in p["items"]:
        assert set(it) == {"table", "name", "lifecycle", "lifecycle_reason"}


def test_lifecycle_status_filter(catalog_file):
    h = _get("/api/data/governance/lifecycle", "status=planned")
    assert h.code == 200
    assert h.payload["total"] == 1
    assert h.payload["items"][0]["lifecycle"] == "planned"


def test_sources_normal(fake_sources):
    h = _get("/api/data/governance/sources")
    assert h.code == 200
    p = h.payload
    for key in ENVELOPE_KEYS:
        assert key in p, key
    assert p["endpoint"] == "/api/data/governance/sources"
    assert p["total"] == 3 and p["validate_problems"] == 0
    assert p["by_domain"] == {"quote": 8, "alt": 3}
    for it in p["items"]:
        assert {"id", "name", "domain", "status", "note"} <= set(it)
        assert "probe" not in it and "home" not in it and "update_window" not in it


def test_sources_domain_filter(fake_sources):
    h = _get("/api/data/governance/sources", "domain=alt")
    assert h.code == 200 and h.payload["total"] == 2


def test_unknown_endpoint_404(fake_sla):
    h = _get("/api/data/governance/nope")
    assert h.code == 404
    assert h.payload["ok"] is False and h.payload["error"] == "not_found"


# ── 2. 失败路径：503 / 400 ────────────────────────────────────
def test_upstream_missing_file_503(tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(mod, "UPSTREAM_DIRS", (empty,))
    h = _get("/api/data/governance/upstream-health")
    assert h.code == 503
    p = h.payload
    assert p["ok"] is False
    assert p["error"] == "governance_unavailable"
    assert p["endpoint"] == "/api/data/governance/upstream-health"
    assert "detail" in p
    assert "total" not in p          # 严禁假装 0 条


def test_lifecycle_missing_file_503(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "CATALOG_PATH", tmp_path / "nope" / "catalog.json")
    h = _get("/api/data/governance/lifecycle")
    assert h.code == 503
    assert h.payload["error"] == "governance_unavailable"
    assert "total" not in h.payload


def test_lifecycle_corrupt_json_503(tmp_path, monkeypatch):
    p = tmp_path / "catalog.json"
    p.write_text("{ this is not json", encoding="utf-8")
    monkeypatch.setattr(mod, "CATALOG_PATH", p)
    h = _get("/api/data/governance/lifecycle")
    assert h.code == 503
    assert h.payload["error"] == "governance_unavailable"


def test_upstream_corrupt_json_503(tmp_path, monkeypatch):
    d = tmp_path / "data_hub"
    d.mkdir()
    (d / "upstream_health_20260101_0000.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(mod, "UPSTREAM_DIRS", (d,))
    h = _get("/api/data/governance/upstream-health")
    assert h.code == 503
    assert h.payload["error"] == "governance_unavailable"


def test_sla_loader_failure_503(monkeypatch):
    def boom():
        raise mod._Unavailable("audit 调用失败")
    monkeypatch.setattr(mod, "_sla_loader", boom)
    h = _get("/api/data/governance/sla")
    assert h.code == 503
    assert h.payload["error"] == "governance_unavailable"


def test_bad_param_limit_not_number(fake_sla):
    h = _get("/api/data/governance/sla", "limit=abc")
    assert h.code == 400
    p = h.payload
    assert p["ok"] is False and p["error"] == "bad_param"
    assert "abc" not in json.dumps(p, ensure_ascii=False)   # 不回显用户输入


def test_bad_param_limit_over_max(fake_sla):
    h = _get("/api/data/governance/sla", "limit=501")
    assert h.code == 400 and h.payload["error"] == "bad_param"
    assert _get("/api/data/governance/sla", "limit=500").code == 200


def test_bad_param_negative_offset(fake_sla):
    h = _get("/api/data/governance/sla", "offset=-1")
    assert h.code == 400 and h.payload["error"] == "bad_param"


def test_bad_param_enum(fake_sla):
    h = _get("/api/data/governance/sla", "grade=critical")
    assert h.code == 400 and h.payload["error"] == "bad_param"


def test_bad_param_path_like_value(fake_sla):
    """不接路径：把路径塞进枚举位必须 400，且不回显该值。"""
    h = _get("/api/data/governance/sla", "grade=C:%2Faios%2Fsecret")
    assert h.code == 400
    assert "C:" not in json.dumps(h.payload, ensure_ascii=False)


# ── 3. 安全边界：无绝对路径、无 store/manifest ─────────────────
def _strings(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k)
            yield from _strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _strings(v)
    else:
        yield str(obj)


def _assert_clean(payload):
    for s in _strings(payload):
        assert not WIN_ABS_RE.search(s), s
        assert POSIX_ABS not in s, s
    assert "store" not in payload
    assert "manifest" not in payload


def test_no_abs_path_sla(fake_sla):
    _assert_clean(_get("/api/data/governance/sla").payload)


def test_no_abs_path_upstream(upstream_dir):
    h = _get("/api/data/governance/upstream-health")
    _assert_clean(h.payload)
    for it in h.payload["items"]:
        assert "store" not in it and "manifest" not in it


def test_no_abs_path_lifecycle(catalog_file):
    h = _get("/api/data/governance/lifecycle")
    _assert_clean(h.payload)
    for it in h.payload["items"]:
        assert "store" not in it and "manifest" not in it


def test_no_abs_path_sources(fake_sources):
    _assert_clean(_get("/api/data/governance/sources").payload)


def test_503_detail_scrubbed_of_abs_path(tmp_path, monkeypatch):
    """OSError 原文带绝对路径时，detail 必须被抹掉。"""
    monkeypatch.setattr(mod, "CATALOG_PATH", tmp_path / "missing" / "catalog.json")
    h = _get("/api/data/governance/lifecycle")
    assert h.code == 503
    _assert_clean(h.payload)


# ── 4. 缓存语义：命中不刷新 generated_at ───────────────────────
def test_cache_hit_keeps_generated_at(monkeypatch):
    calls = {"n": 0}

    def loader():
        calls["n"] += 1
        return json.loads(json.dumps(FAKE_SLA))

    monkeypatch.setattr(mod, "_sla_loader", loader)
    h1 = _get("/api/data/governance/sla")
    h2 = _get("/api/data/governance/sla")
    assert calls["n"] == 1                      # 第二次不再跑重操作
    assert h1.payload["cached"] is False
    assert h2.payload["cached"] is True
    assert h1.payload["generated_at"] == h2.payload["generated_at"]


def test_cache_ttl_expiry_recomputes(monkeypatch):
    calls = {"n": 0}

    def loader():
        calls["n"] += 1
        return json.loads(json.dumps(FAKE_SLA))

    monkeypatch.setattr(mod, "_sla_loader", loader)
    _get("/api/data/governance/sla")
    ent = mod._CACHE["sla"]
    ent["ts"] = ent["ts"] - (mod.CACHE_TTL + 1)     # 手动过期
    h = _get("/api/data/governance/sla")
    assert calls["n"] == 2
    assert h.payload["cached"] is False


# ── 5. 接线与源码静态契约 ─────────────────────────────────────
def test_portal_api_wired_exactly_once():
    src = PORTAL_API_PY.read_text(encoding="utf-8")
    assert src.count('startswith("/api/data/governance/")') == 1
    assert "_gov_get(self, path, qs)" in src


def test_module_has_no_write_subprocess_or_requests():
    src = MODULE_PY.read_text(encoding="utf-8")
    assert "subprocess" not in src
    assert "requests" not in src
    # 真实校验：所有 open() 调用只允许读模式，禁写盘/追加
    for m in re.finditer(r"open\(([^)]*)\)", src):
        args = m.group(1)
        assert '"w"' not in args and "'w'" not in args, args
        assert '"a"' not in args and "'a'" not in args, args
        assert '"r"' in args, args
