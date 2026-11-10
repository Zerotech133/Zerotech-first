# -*- coding: utf-8 -*-
"""T1 后端通道组单测：/api/data/catalog/* 只读端点。

用 FakeHandler 记录 send_response / send_header / end_headers / wfile.write，
不起真实服务、不碰 8091、不发 POST；catalog 与 datamart 取数用 monkeypatch 隔离。
覆盖 impl_contract_20261105.md §4.1 / §4.2 / §4.3 的全部验收点。
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

PORTAL = Path(__file__).resolve().parents[1]
if str(PORTAL) not in sys.path:
    sys.path.insert(0, str(PORTAL))
import api_catalog_data as mod  # noqa: E402

PORTAL_API_PY = PORTAL / "portal_api.py"


# ── BaseHTTPRequestHandler 替身：只记录底层写出动作，不碰 socket ──
class _RecordingWFile:
    def __init__(self, owner):
        self._owner = owner
        self._buf = io.BytesIO()

    def write(self, data):
        self._owner.events.append(("wfile.write", bytes(data)))
        return self._buf.write(data)

    def getvalue(self):
        return self._buf.getvalue()


class FakeHandler:
    def __init__(self):
        self.events = []
        self.headers = []
        self.status = None
        self.wfile = _RecordingWFile(self)
        self.payload = None
        self.code = None

    def send_response(self, code):
        self.status = code
        self.events.append(("send_response", code))

    def send_header(self, key, value):
        self.headers.append((key, value))
        self.events.append(("send_header", key, value))

    def end_headers(self):
        self.events.append(("end_headers",))

    def _json(self, obj, code=200):
        # 模拟 portal_api.Handler._json 的写出流程；allow_nan=False 强制暴露
        # 任何漏过 _cell 的 NaN/Inf（JSON 安全硬校验）。
        body = json.dumps(obj, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.payload = obj
        self.code = code
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @property
    def body_json(self):
        return json.loads(self.wfile.getvalue().decode("utf-8"))


def _get(path, qs=""):
    h = FakeHandler()
    mod.handle_get(h, path, qs)
    return h


# ── 隔离用的小 catalog（含绝对路径 store/manifest，专测不外泄）──
def _rec(source, freq, desc, schema):
    return {
        "source": source, "freq": freq, "rows": 100, "freshness": "20261104",
        "date_col": "trade_date", "code_col": "ts_code", "layout": "part",
        "files": 3, "primary_key": ["ts_code", "trade_date"],
        "verification_status": "verified", "deprecated": False,
        "lifecycle": "active", "static_no_date": False, "desc": desc,
        "schema": schema,
        "store": "C:/SECRET/ABS/path/should_never_leak",
        "manifest": "C:/SECRET/ABS/manifest.json",
        "priority": 1, "readiness_basis": "manifest",
    }


FAKE_CATALOG = {
    "version": 43,
    "updated": "2026-11-05T13:19:31",
    "tables": {
        "ts_daily": _rec("xtcloud", "D", "日线行情 daily", ["ts_code", "trade_date", "close"]),
        "ts_stock_basic": _rec("xtcloud", "D", "股票基础信息", ["ts_code", "name"]),
        "us_daily": _rec("tushare", "D", "美股日线 daily", ["ts_code", "trade_date"]),
        "fin_quarter": _rec("cnopendata", "Q", "季度财报", ["ts_code", "end_date"]),
    },
    "aliases": {
        "daily": {"resolves": ["ts_daily", "daily"], "primary": "ts_daily"},
        "stock_basic": {"resolves": ["ts_stock_basic"], "primary": "ts_stock_basic"},
    },
}


@pytest.fixture
def cat(monkeypatch, tmp_path):
    """把 CATALOG_PATH 指向隔离的小 catalog，让断言精确可控、不依赖真实数据。"""
    p = tmp_path / "catalog.json"
    p.write_text(json.dumps(FAKE_CATALOG, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(mod, "CATALOG_PATH", p)
    return p


# ── 1. 正常返回结构 ───────────────────────────────────────────
def test_tables_normal_structure(cat):
    h = _get("/api/data/catalog/tables", "")
    assert h.code == 200
    p = h.payload
    assert p["ok"] is True
    assert p["endpoint"] == "/api/data/catalog/tables"
    assert p["source"] == "catalog.json"
    assert p["version"] == 43
    assert p["updated"] == "2026-11-05T13:19:31"
    assert p["total"] == 4
    assert p["returned"] == 4
    assert p["aliases"] == 2
    assert p["by_source"] == {"xtcloud": 2, "tushare": 1, "cnopendata": 1}
    assert p["by_freq"] == {"D": 3, "Q": 1}
    assert [i["name"] for i in p["items"]] == [
        "ts_daily", "ts_stock_basic", "us_daily", "fin_quarter"]
    # 复用现成 helper 的写出序列：send_response → send_header → end_headers → wfile
    assert h.events[0] == ("send_response", 200)
    assert ("end_headers",) in h.events
    assert any(e[0] == "wfile.write" for e in h.events)
    assert dict(h.headers)["Content-Type"].startswith("application/json")
    assert h.body_json == p


def test_table_normal_structure(cat, monkeypatch):
    import pandas as pd
    seen = {}

    def fake(name, start, end, limit):
        seen.update(name=name, start=start, end=end, limit=limit)
        return pd.DataFrame({
            "ts_code": ["000001.SZ"], "trade_date": ["20261104"], "close": [10.5]})

    monkeypatch.setattr(mod, "_read_frame", fake)
    h = _get("/api/data/catalog/table",
             "name=ts_daily&start=20260101&end=20261105&limit=5")
    assert h.code == 200
    p = h.payload
    assert p["ok"] is True
    assert p["name"] == "ts_daily" and p["resolved"] == "ts_daily"
    assert p["meta"]["name"] == "ts_daily" and isinstance(p["meta"]["schema"], list)
    assert "store" not in p["meta"] and "manifest" not in p["meta"]
    assert p["columns"] == ["ts_code", "trade_date", "close"]
    assert p["rows"] == [["000001.SZ", "20261104", 10.5]]
    assert p["row_count"] == 1
    assert p["truncated"] is False
    assert p["available"] is True and p["reason"] is None
    # 只调 datamart.read(name, start=..., end=..., limit=...)
    assert seen == {"name": "ts_daily", "start": "20260101",
                    "end": "20261105", "limit": 5}
    assert "C:/SECRET" not in h.wfile.getvalue().decode("utf-8")


# ── 2. brief 绝不含 schema / store / manifest ──────────────────
def test_brief_excludes_schema_and_abs_paths(cat):
    h = _get("/api/data/catalog/tables", "detail=brief")
    assert h.code == 200
    items = h.payload["items"]
    assert items
    expected = {"name", "source", "freq", "rows", "freshness", "date_col",
                "code_col", "layout", "files", "primary_key",
                "verification_status", "deprecated", "lifecycle",
                "static_no_date", "desc"}
    for it in items:
        assert set(it) == expected
        for forbidden in ("schema", "store", "manifest"):
            assert forbidden not in it
    raw = h.wfile.getvalue().decode("utf-8")
    assert "C:/SECRET" not in raw
    assert "manifest.json" not in raw


def test_full_includes_schema_but_no_abs_paths(cat):
    h = _get("/api/data/catalog/tables", "detail=full")
    assert h.code == 200
    for it in h.payload["items"]:
        assert "schema" in it and isinstance(it["schema"], list)
        assert "store" not in it and "manifest" not in it
    assert "C:/SECRET" not in h.wfile.getvalue().decode("utf-8")


# ── 3. 过滤与分页（by_source/by_freq 全量统计不受影响）─────────
def test_filters_and_pagination(cat):
    # source 精确
    h = _get("/api/data/catalog/tables", "source=xtcloud")
    assert h.payload["total"] == 2
    assert {i["name"] for i in h.payload["items"]} == {"ts_daily", "ts_stock_basic"}
    # freq 精确
    h = _get("/api/data/catalog/tables", "freq=Q")
    assert h.payload["total"] == 1
    assert h.payload["items"][0]["name"] == "fin_quarter"
    # q 大小写不敏感，命中表名/描述子串
    h = _get("/api/data/catalog/tables", "q=DAILY")
    assert h.payload["total"] == 2
    assert {i["name"] for i in h.payload["items"]} == {"ts_daily", "us_daily"}
    # 分页：total 是过滤后总数，returned 是本页数
    h = _get("/api/data/catalog/tables", "limit=2&offset=1")
    assert h.payload["total"] == 4
    assert h.payload["returned"] == 2
    assert [i["name"] for i in h.payload["items"]] == ["ts_stock_basic", "us_daily"]
    # 过滤+分页下 by_source 仍是全量统计
    h = _get("/api/data/catalog/tables", "source=xtcloud&limit=1&offset=1")
    assert h.payload["total"] == 2 and h.payload["returned"] == 1
    assert h.payload["by_source"] == {"xtcloud": 2, "tushare": 1, "cnopendata": 1}
    assert sum(h.payload["by_freq"].values()) == 4
    # limit=0 = 全部；超上限 clamp 到 1000
    assert _get("/api/data/catalog/tables", "limit=0").payload["returned"] == 4
    assert _get("/api/data/catalog/tables", "limit=9999").payload["returned"] == 4


# ── 4. 参数非法 → 400 结构化 JSON ──────────────────────────────
@pytest.mark.parametrize("qs", [
    "limit=-1", "limit=abc", "offset=-1", "offset=abc", "detail=xxx",
])
def test_tables_bad_param_400(cat, qs):
    h = _get("/api/data/catalog/tables", qs)
    assert h.code == 400
    assert h.payload["ok"] is False
    assert h.payload["error"] == "bad_param"
    assert h.payload["message"]


@pytest.mark.parametrize("qs", [
    "name=ts_daily&start=2026", "name=ts_daily&start=abcd1234",
    "name=ts_daily&end=2026110", "name=ts_daily&limit=abc",
    "name=ts_daily&limit=0", "name=ts_daily&limit=-3",
])
def test_table_bad_param_400(cat, qs):
    h = _get("/api/data/catalog/table", qs)
    assert h.code == 400
    assert h.payload["ok"] is False
    assert h.payload["error"] == "bad_param"


# ── 5. 表不存在 → 404 + 相近候选（最多 8 个）───────────────────
def test_table_not_found_404_with_candidates(cat):
    h = _get("/api/data/catalog/table", "name=ts_dail")
    assert h.code == 404
    assert h.payload["ok"] is False
    assert h.payload["error"] == "table_not_found"
    cand = h.payload["candidates"]
    assert isinstance(cand, list) and len(cand) <= 8
    assert "ts_daily" in cand


@pytest.mark.parametrize("bad", [
    "../etc/passwd", "ts-daily", "ts_daily;DROP", "ts.daily", "",
])
def test_table_name_illegal_chars_404(cat, bad):
    # 自由路径 / 非法字符一律 404，不接任意表名
    h = _get("/api/data/catalog/table", "name=" + bad)
    assert h.code == 404
    assert h.payload["error"] == "table_not_found"


# ── 6. datamart 抛异常 → available=false + reason，不冒泡裸栈 ──
def test_datamart_exception_available_false(cat, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("parquet 损坏 / 源未就绪")

    monkeypatch.setattr(mod, "_read_frame", boom)
    h = _get("/api/data/catalog/table", "name=ts_daily")
    assert h.code == 200 and h.code != 500
    p = h.payload
    assert p["ok"] is True
    assert p["available"] is False
    assert p["reason"] and "RuntimeError" in p["reason"]
    assert p["rows"] == [] and p["columns"] == [] and p["row_count"] == 0
    assert "store" not in p["meta"] and "manifest" not in p["meta"]


# ── 7. DataFrame 含 NaN / Timestamp 的 JSON 安全 ───────────────
def test_cell_type_safety():
    import numpy as np
    import pandas as pd
    assert mod._cell(float("nan")) is None
    assert mod._cell(float("inf")) is None
    assert mod._cell(pd.NaT) is None
    assert mod._cell(pd.Timestamp("2026-11-04")).startswith("2026-11-04")
    assert mod._cell(np.int64(7)) == 7 and type(mod._cell(np.int64(7))) is int
    assert mod._cell(np.bool_(True)) is True
    assert isinstance(mod._cell(b"\x00"), str)


def test_dataframe_nan_timestamp_json_safe(cat, monkeypatch):
    import numpy as np
    import pandas as pd
    df = pd.DataFrame({
        "ts_code": ["000001.SZ", "000002.SZ"],
        "trade_date": [pd.Timestamp("2026-11-04"), pd.NaT],
        "close": [np.float64(10.5), np.nan],
        "vol": [np.int64(1000), np.int64(0)],
        "flag": [np.bool_(True), np.bool_(False)],
        "raw": [b"\x00\x01", b"\xff"],
        "dt": [np.datetime64("2026-11-05"), np.datetime64("NaT")],
    })
    monkeypatch.setattr(mod, "_read_frame", lambda *a, **kw: df)
    h = _get("/api/data/catalog/table", "name=ts_daily&limit=2")
    assert h.code == 200
    p = h.payload
    assert p["available"] is True
    assert p["columns"] == ["ts_code", "trade_date", "close", "vol", "flag", "raw", "dt"]
    assert p["row_count"] == 2
    assert p["truncated"] is True
    rows = p["rows"]
    # NaN / NaT → None
    assert rows[1][2] is None and rows[1][1] is None and rows[1][6] is None
    # numpy 标量 → Python 标量
    assert rows[0][2] == 10.5 and type(rows[0][2]) is float
    assert rows[0][3] == 1000 and type(rows[0][3]) is int
    assert rows[0][4] is True and type(rows[0][4]) is bool
    # Timestamp / datetime64 → ISO 串
    assert rows[0][1].startswith("2026-11-04T")
    assert rows[0][6].startswith("2026-11-05")
    # bytes → repr 字符串
    assert isinstance(rows[0][5], str)
    # 整段响应是严格合法 JSON（FakeHandler._json 已用 allow_nan=False 强制）
    raw = h.wfile.getvalue().decode("utf-8")
    assert "NaN" not in raw and "Infinity" not in raw and "NaT" not in raw
    assert json.loads(raw) == p


# ── 8. catalog 缺失 / 损坏 → 503，不假装 0 表 ──────────────────
def test_catalog_missing_503(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "CATALOG_PATH", tmp_path / "missing.json")
    for path, qs in (("/api/data/catalog/tables", ""),
                     ("/api/data/catalog/table", "name=ts_daily")):
        h = _get(path, qs)
        assert h.code == 503
        assert h.payload["ok"] is False
        assert h.payload["error"] == "catalog_unavailable"
        assert h.payload.get("total") != 0   # 不假装 0 表


def test_catalog_corrupt_503(monkeypatch, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(mod, "CATALOG_PATH", bad)
    h = _get("/api/data/catalog/tables", "")
    assert h.code == 503
    assert h.payload["error"] == "catalog_unavailable"


# ── 附加：别名解析 / limit 硬上限 / 未知路径 ────────────────────
def test_alias_resolved(cat, monkeypatch):
    """集成实测回归：别名必须按 primary 取数。

    首版把别名原名 'daily' 直接传给 datamart.read，触发跨源 concat 全表扫描，
    端到端实测 60s 超时。当时 fake 用 lambda *a 吞掉参数，测试照样通过。
    """
    import pandas as pd
    seen = {}

    def fake(name, start, end, limit):
        seen["name"] = name
        return pd.DataFrame({"a": [1]})

    monkeypatch.setattr(mod, "_read_frame", fake)
    h = _get("/api/data/catalog/table", "name=daily")
    assert h.code == 200
    assert h.payload["name"] == "daily"
    assert h.payload["resolved"] == "ts_daily"
    assert h.payload["meta"]["name"] == "ts_daily"
    assert seen["name"] == "ts_daily", "别名必须按 primary 取数，不能传原名"


def test_limit_hard_cap_200(cat, monkeypatch):
    import pandas as pd
    seen = {}

    def fake(name, start, end, limit):
        seen["limit"] = limit
        return pd.DataFrame({"a": [1]})

    monkeypatch.setattr(mod, "_read_frame", fake)
    h = _get("/api/data/catalog/table", "name=ts_daily&limit=9999")
    assert h.code == 200
    assert seen["limit"] == 200
    assert h.payload["limit_applied"] == 200
    assert h.payload["clamped"] is True, "截断必须显式回传，不能静默"
    assert "sample_note" in h.payload

    h2 = _get("/api/data/catalog/table", "name=ts_daily&limit=20")
    assert h2.payload["clamped"] is False
    assert h2.payload["limit_applied"] == 20


def test_unknown_catalog_path_404(cat):
    h = _get("/api/data/catalog/nope", "")
    assert h.code == 404
    assert h.payload["ok"] is False


def test_real_catalog_consistency(monkeypatch):
    """真实 catalog 集成（2026-11-10 A11 改动态断言）。

    原版钉死 v43/258 表，catalog 升 v44/259 即假红。现只钉结构一致性：
    版本/表数随数据演进（下限=v43/258 首版口径），分布和恒等于 total，
    别名数不缩水。不读样本、不起服务。"""
    real = PORTAL.parent / "quant_backtest" / "data" / "catalog.json"
    monkeypatch.setattr(mod, "CATALOG_PATH", real)
    h = _get("/api/data/catalog/tables", "detail=brief&limit=2")
    assert h.code == 200
    p = h.payload
    assert isinstance(p["version"], int) and p["version"] >= 43
    assert p["total"] >= 258
    assert p["aliases"] >= 14
    assert sum(p["by_source"].values()) == p["total"]
    assert sum(p["by_freq"].values()) == p["total"]
    assert p["returned"] == 2
    assert all("store" not in i and "manifest" not in i and "schema" not in i
               for i in p["items"])


# ── 9. 早分支挂载存在且只出现一次（§4.3）───────────────────────
def test_early_branch_mounted_exactly_once():
    text = PORTAL_API_PY.read_text(encoding="utf-8")
    assert text.count("if path.startswith('/api/data/catalog/'):") == 1
    assert text.count("import api_catalog_data") == 1
    assert text.count("api_catalog_data.handle_get(self, path, qs)") == 1
    # 模块名在文件里只出现于这一处早分支（import + 调用两行）
    assert text.count("api_catalog_data") == 2
    # 位于 do_GET 前缀早分支区：在既有首个 /api/zhuge/review 分支之前
    i_get = text.find("def do_GET(self):")
    i_new = text.find("if path.startswith('/api/data/catalog/'):")
    i_first = text.find("if path == '/api/zhuge/review/status'")
    assert i_get != -1 and i_new != -1 and i_first != -1
    assert i_get < i_new < i_first
    # §4.3 给定的注释落地
    assert "catalog 258 表实时元数据/样本只读通道" in text
    assert "本轮不重启" in text
