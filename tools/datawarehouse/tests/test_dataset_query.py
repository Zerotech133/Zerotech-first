# -*- coding: utf-8 -*-
"""S3-06 P1 通用 query 单测（2026-11-02 task-50）。

覆盖：sqlite 过滤/分页/排序/投影/since/注入防护、parquet 过滤投影分页、
jsonl 基础行 + not_supported、空目录/混合目录、上限/白名单、HTTP handler
（GET/POST mock）、MCP stdio、只读幂等、审计日志、覆盖率脚本判定。
"""
from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "tools" / "datawarehouse"))

import pyarrow as pa  # noqa: E402
import pyarrow.parquet as pq  # noqa: E402
from dataset_registry import (  # noqa: E402
    DatasetRegistry,
    QueryNotSupported,
    QueryParamError,
)


def _make_env(tmp_path: Path) -> Path:
    """临时环境：sqlite 表 + parquet 目录 + jsonl 目录 + 空目录 + 混合目录 + inventory。"""
    db = tmp_path / "t.db"
    con = sqlite3.connect(db)
    con.execute("create table trades (trade_date text, code text, px real)")
    con.executemany("insert into trades values (?,?,?)",
                    [("2026010%d" % i, "00000%d" % (i % 3), 1.0 * i) for i in range(1, 8)])
    con.commit()
    con.close()
    pq_dir = tmp_path / "pq"
    pq_dir.mkdir()
    for i in range(3):
        pq.write_table(
            pa.table({"trade_date": pa.array(["2026010%d" % (i + 1)] * 5),
                      "code": pa.array(["00000%d" % j for j in range(5)]),
                      "px": pa.array([float(i * 10 + j) for j in range(5)])}),
            pq_dir / ("part_%04d.parquet" % i))
    jl = tmp_path / "jl"
    jl.mkdir()
    (jl / "data.jsonl").write_text(
        "\n".join(json.dumps({"ts": "2026010%d" % (i + 1), "a": i, "b": "x%d" % i})
                  for i in range(5)), encoding="utf-8")
    os.utime(jl / "data.jsonl", (1577836800, 1577836800))  # 2020-01-01（file mtime 回退用）
    (jl / "more.jsonl").write_text(
        "\n".join(json.dumps({"ts": "2026010%d" % (i + 1), "a": i, "b": "x%d" % i})
                  for i in range(5, 8)), encoding="utf-8")
    jl_plain = tmp_path / "jl_plain"
    jl_plain.mkdir()
    (jl_plain / "old.jsonl").write_text(
        "\n".join(json.dumps({"k": i}) for i in range(3)), encoding="utf-8")
    os.utime(jl_plain / "old.jsonl", (1577836800, 1577836800))
    jd = tmp_path / "json_docs"
    jd.mkdir()
    (jd / "docs.json").write_text(
        json.dumps([{"id": i, "name": "d%d" % i} for i in range(3)]), encoding="utf-8")
    (tmp_path / "empty").mkdir()
    mixed = tmp_path / "mixed"
    mixed.mkdir()
    (mixed / "readme.pdf").write_bytes(b"%PDF-1.4")
    (mixed / "notes.md").write_text("# notes", encoding="utf-8")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "sub1").mkdir()
    (nested / "sub1" / "x.json").write_text(
        json.dumps([{"id": 1}, {"id": 2}]), encoding="utf-8")
    (nested / "sub2").mkdir()
    (nested / "sub2" / "r.pdf").write_bytes(b"%PDF-1.4")
    (nested / "top.md").write_text("top", encoding="utf-8")
    inv = tmp_path / "inv.json"
    inv.write_text(json.dumps({"datasets": [
        {"domain": "测试", "label": "t.trades", "kind": "sqlite_table",
         "path": str(db), "registered": True, "exposed": True, "suggested_id": "trades"},
        {"domain": "测试", "label": "pq", "kind": "dir",
         "path": str(pq_dir), "registered": True, "exposed": True, "suggested_id": "pq_ds"},
        {"domain": "测试", "label": "jl", "kind": "corpus_layer",
         "path": str(jl), "registered": True, "exposed": True, "suggested_id": "jl_ds"},
        {"domain": "测试", "label": "jl_plain", "kind": "corpus_layer",
         "path": str(jl_plain), "registered": True, "exposed": True,
         "suggested_id": "jl_plain_ds"},
        {"domain": "测试", "label": "json_docs", "kind": "dir",
         "path": str(jd), "registered": True, "exposed": True, "suggested_id": "jd_ds"},
        {"domain": "测试", "label": "empty", "kind": "dir",
         "path": str(tmp_path / "empty"), "registered": False, "exposed": False,
         "suggested_id": "empty_ds"},
        {"domain": "测试", "label": "mixed", "kind": "dir",
         "path": str(mixed), "registered": False, "exposed": False, "suggested_id": "mixed_ds"},
        {"domain": "测试", "label": "nested", "kind": "dir",
         "path": str(nested), "registered": False, "exposed": False,
         "suggested_id": "nested_ds"},
        {"domain": "测试", "label": "pg", "kind": "pg_table",
         "path": "pg://fake", "registered": True, "exposed": True, "suggested_id": "pg_ds"},
    ]}, ensure_ascii=False), encoding="utf-8")
    return inv


@pytest.fixture()
def reg(tmp_path):
    return DatasetRegistry(_make_env(tmp_path), allowed_roots=[tmp_path])


# ---------- sqlite ----------
def test_sqlite_list_and_pagination(reg):
    page1 = reg.query("trades", limit=3)
    assert page1["ok"] is True and page1["state"] == "ok"
    assert page1["carrier"] == "sqlite_table"
    assert len(page1["rows"]) == 3 and page1["has_more"] is True
    assert page1["next_offset"] == 3
    page2 = reg.query("trades", limit=3, offset=page1["next_offset"])
    assert page2["rows"] and page2["rows"] != page1["rows"]
    last = reg.query("trades", limit=3, offset=6)
    assert len(last["rows"]) == 1 and last["has_more"] is False
    assert last["next_offset"] is None


def test_sqlite_where_filter_and_count(reg):
    total = reg.query("trades", limit=1, count=True)["count"]
    assert total == 7
    hit = reg.query("trades", where="code=000001", limit=100, count=True)
    assert hit["count"] == 3
    assert hit["count"] < total
    assert {r[1] for r in hit["rows"]} == {"000001"}
    # 结构化 JSON where + in 算子
    hit2 = reg.query(
        "trades",
        where=[{"field": "code", "op": "in", "value": ["000000"]}])
    assert hit2["count"] is None and hit2["rows"]
    assert all(r[1] == "000000" for r2 in hit2["rows"] for r in [r2])


def test_sqlite_order_and_projection(reg):
    out = reg.query("trades", columns=["px", "code"], order="px:desc", limit=3)
    assert out["columns"] == ["px", "code"]
    assert [r[0] for r in out["rows"]] == [7.0, 6.0, 5.0]
    out2 = reg.query("trades", order=[{"field": "code"}, {"field": "px", "desc": True}],
                     limit=2)
    assert len(out2["rows"]) == 2


def test_sqlite_since(reg):
    out = reg.query("trades", since="2026-01-05", limit=100, count=True)
    assert out["evidence"]["since_applied"] is True
    assert out["evidence"]["since_column"] == "trade_date"
    assert out["count"] == 3
    assert all(r[0] >= "20260105" for r in out["rows"])


def test_sqlite_sql_injection_is_parameterized(reg):
    total = reg.query("trades", limit=1, count=True)["count"]
    # 值注入：整体作为参数，返回 0 行（不是全表，也不报 SQL 错）
    out = reg.query("trades", where="code=000001' OR '1'='1", limit=100)
    assert out["state"] == "no_data"
    assert len(out["rows"]) == 0 and total > 0
    out2 = reg.query("trades", where={"code": "x'; DROP TABLE trades; --"})
    assert out2["state"] == "no_data"
    assert reg.query("trades", limit=1, count=True)["count"] == total  # 表未被破坏
    # 字段名注入：白名单正则拒绝
    with pytest.raises(QueryParamError):
        reg.query("trades", where=[{"field": "code); DROP TABLE trades; --",
                                   "op": "=", "value": 1}])
    with pytest.raises(QueryParamError):
        reg.query("trades", where=[{"field": "code", "op": "=", "value": {"x": 1}}])


# ---------- 上限/白名单 ----------
def test_query_limits_and_whitelist(reg):
    with pytest.raises(QueryParamError):
        reg.query("trades", limit=1001)
    with pytest.raises(QueryParamError):
        reg.query("trades", offset=100001)
    with pytest.raises(QueryParamError):
        reg.query("trades", limit=0)
    with pytest.raises(QueryParamError) as ei:
        reg.query("trades", where="nope=1")
    assert "allowed_fields" in ei.value.detail
    with pytest.raises(QueryParamError):
        reg.query("trades", order="code;drop")
    with pytest.raises(QueryParamError):
        reg.query("trades", where={"code": {"bad": "op"}})
    with pytest.raises(QueryParamError):
        reg.query("trades", since="not-a-date")


# ---------- parquet ----------
def test_parquet_filter_projection_pagination(reg):
    out = reg.query("pq_ds", limit=4, columns=["code", "px"], count=True)
    assert out["carrier"] == "dir_parquet"
    assert out["columns"] == ["code", "px"]
    assert out["count"] == 15 and len(out["rows"]) == 4 and out["has_more"] is True
    p2 = reg.query("pq_ds", limit=4, offset=4, columns=["code"])
    assert p2["rows"] and p2["rows"] != out["rows"][:1]
    hit = reg.query("pq_ds", where={"code": "000003"}, columns=["code"], limit=100, count=True)
    assert hit["count"] == 3 and all(r[0] == "000003" for r in hit["rows"])
    with pytest.raises(QueryParamError) as ei:
        reg.query("pq_ds", columns=["nope"])
    assert "allowed_fields" in ei.value.detail


def test_parquet_since_and_order_small(reg):
    out = reg.query("pq_ds", since="2026-01-03", limit=100, count=True)
    assert out["count"] == 5  # 仅第三片 trade_date=20260103 满足 >=20260103
    ordered = reg.query("pq_ds", order="px:desc", limit=3, columns=["px"])
    assert [r[0] for r in ordered["rows"]] == [24.0, 23.0, 22.0]


# ---------- jsonl / json（P2：where/order/count/since） ----------
def test_jsonl_rows_limit_and_count(reg):
    out = reg.query("jl_ds", limit=2, count=True)
    assert out["carrier"] == "dir_jsonl"
    assert out["columns"] == ["ts", "a", "b"]
    assert len(out["rows"]) == 2 and out["count"] == 8 and out["has_more"] is True
    assert out["evidence"]["engine"] == "jsonl"


def test_jsonl_where_filter_and_count(reg):
    total = reg.query("jl_ds", limit=1, count=True)["count"]
    hit = reg.query("jl_ds", where={"b": "x1"}, limit=100, count=True)
    assert hit["count"] == 1 and hit["rows"] and hit["rows"][0][2] == "x1"
    assert hit["count"] < total
    # 数值比较 + in 算子
    gt = reg.query("jl_ds", where=[{"field": "a", "op": ">=", "value": 6}], count=True)
    assert gt["count"] == 2
    inn = reg.query("jl_ds", where=[{"field": "a", "op": "in", "value": [0, 7]}], count=True)
    assert inn["count"] == 2


def test_jsonl_order_and_pagination(reg):
    out = reg.query("jl_ds", order="a:desc", limit=3)
    assert [r[1] for r in out["rows"]] == [7, 6, 5]
    p1 = reg.query("jl_ds", order="a", limit=3, offset=0)
    p2 = reg.query("jl_ds", order="a", limit=3, offset=3)
    assert [r[1] for r in p1["rows"]] == [0, 1, 2]
    assert [r[1] for r in p2["rows"]] == [3, 4, 5]
    assert p1["next_offset"] == 3


def test_jsonl_since_row_column(reg):
    out = reg.query("jl_ds", since="2026-01-06", count=True)
    assert out["evidence"]["since_column"] == "ts"
    assert out["evidence"]["since_mode"] == "row_column"
    assert out["count"] == 3


def test_jsonl_since_file_mtime_fallback(reg):
    # 无行内时间字段 → 按文件 mtime；fixture 文件 mtime=2020，since 2026 应无命中
    old = reg.query("jl_plain_ds", since="2026-01-01", count=True)
    assert old["state"] == "no_data" and old["count"] == 0
    assert old["evidence"]["since_mode"] == "file_mtime"
    none = reg.query("jl_plain_ds", count=True)
    assert none["count"] == 3


def test_jsonl_unknown_field_and_order_cap(reg, monkeypatch):
    with pytest.raises(QueryParamError) as ei:
        reg.query("jl_ds", where={"nope": 1})
    assert "allowed_fields" in ei.value.detail
    import dataset_registry
    monkeypatch.setattr(dataset_registry, "QUERY_JSONL_ORDER_ROWS", 2)
    with pytest.raises(QueryNotSupported):
        reg.query("jl_ds", order="a:desc")


def test_json_docs_file_rows(reg):
    out = reg.query("jd_ds", limit=5, count=True)
    assert out["carrier"] == "dir_jsonl"
    assert out["columns"] == ["id", "name"]
    assert out["count"] == 3 and len(out["rows"]) == 3


# ---------- mixed / nested 文件级（P2） ----------
def test_mixed_files_query(reg):
    out = reg.query("mixed_ds", limit=5, count=True)
    assert out["carrier"] == "dir_files"
    assert out["columns"] == ["path", "name", "ext", "size", "mtime", "depth"]
    assert out["count"] == 2 and len(out["rows"]) == 2
    pdf = reg.query("mixed_ds", where={"ext": ".pdf"}, count=True)
    assert pdf["count"] == 1 and pdf["rows"][0][1] == "readme.pdf"
    ordered = reg.query("mixed_ds", order="name:desc", limit=1)
    assert ordered["rows"][0][1] == "readme.pdf"
    p1 = reg.query("mixed_ds", limit=1, offset=0)
    p2 = reg.query("mixed_ds", limit=1, offset=1)
    assert p1["rows"] and p2["rows"] and p1["rows"] != p2["rows"]


def test_nested_files_query(reg):
    out = reg.query("nested_ds", limit=10, count=True)
    assert out["carrier"] == "dir_files"
    assert out["count"] == 3  # sub1/x.json + sub2/r.pdf + top.md
    assert out["evidence"]["max_depth"] >= 1
    json_only = reg.query("nested_ds", where={"ext": ".json"}, count=True)
    assert json_only["count"] == 1
    top = reg.query("nested_ds", where={"name": "top.md"}, count=True)
    assert top["count"] == 1
    with pytest.raises(QueryParamError):
        reg.query("nested_ds", where={"nope": 1})


def test_empty_and_unknown_and_not_supported(reg):
    empty = reg.query("empty_ds")
    assert empty["state"] == "no_data"
    with pytest.raises(KeyError):
        reg.query("no_such_dataset")
    # not_supported 边界保留：pg_table 载体 P2 仍不支持行级 query（fail-visible）
    with pytest.raises(QueryNotSupported):
        reg.query("pg_ds")


# ---------- 只读/审计 ----------
def test_query_is_read_only_and_audited(reg, tmp_path, monkeypatch):
    db = Path(reg.get("trades")["path"])
    before = (db.stat().st_size, db.stat().st_mtime_ns)
    audit = tmp_path / "audit.jsonl"
    monkeypatch.setenv("DW_QUERY_AUDIT", str(audit))
    reg.query("trades", where="code=000001", limit=2)
    assert (db.stat().st_size, db.stat().st_mtime_ns) == before
    lines = [json.loads(x) for x in audit.read_text(encoding="utf-8").splitlines() if x]
    assert lines and lines[-1]["dataset_id"] == "trades"
    dumped = json.dumps(lines[-1], ensure_ascii=False)
    assert "rows_data" not in dumped and "000001" not in dumped


# ---------- HTTP handler（GET/POST mock） ----------
class _FakeHandler:
    def __init__(self):
        self.calls = []

    def _json(self, payload, code=200):
        self.calls.append((payload, code))


@pytest.fixture()
def api(reg):
    path = REPO / "portal" / "api_dw_registry.py"
    spec = importlib.util.spec_from_file_location("api_dw_registry_t50", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    mod._REG = reg
    return mod


def test_http_get_query(api):
    h = _FakeHandler()
    api.handle_get(h, "/api/dw/query", "id=trades&limit=2&where=code%3D000001")
    payload, code = h.calls[-1]
    assert code == 200 and payload["ok"] is True
    assert payload["query_state"] == "ok" and len(payload["rows"]) == 2
    assert "data_date" in payload
    # 参数错误 → 400
    h2 = _FakeHandler()
    api.handle_get(h2, "/api/dw/query", "id=trades&limit=99999")
    assert h2.calls[-1][1] == 400
    # 未知 dataset → 404
    h3 = _FakeHandler()
    api.handle_get(h3, "/api/dw/query", "id=nope")
    assert h3.calls[-1][1] == 404
    # 不支持载体 → 409（pg_table 仍保留 not_supported 边界）
    h4 = _FakeHandler()
    api.handle_get(h4, "/api/dw/query", "id=pg_ds")
    assert h4.calls[-1][1] == 409


def test_http_post_query(api):
    h = _FakeHandler()
    api.handle_post(h, "/api/dw/query",
                    json.dumps({"id": "trades", "where": [{"field": "code",
                                                           "op": "=", "value": "000002"}],
                                "limit": 2}).encode("utf-8"))
    payload, code = h.calls[-1]
    assert code == 200 and payload["ok"] is True
    assert all(r[1] == "000002" for r in payload["rows"])
    # form 编码 body
    h2 = _FakeHandler()
    api.handle_post(h2, "/api/dw/query", b"id=trades&limit=1&count=1")
    assert h2.calls[-1][1] == 200 and h2.calls[-1][0]["count"] == 7
    # 坏 body → 400；未知路径 → 404
    h3 = _FakeHandler()
    api.handle_post(h3, "/api/dw/query", b"{bad json")
    assert h3.calls[-1][1] == 400
    h4 = _FakeHandler()
    api.handle_post(h4, "/api/other", b"{}")
    assert h4.calls[-1][1] == 404


# ---------- MCP ----------
@pytest.fixture()
def mcp(reg):
    path = REPO / "agent_platform" / "tools" / "dw_governance_mcp.py"
    spec = importlib.util.spec_from_file_location("dw_governance_mcp_t50", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    mod._REG = reg
    return mod


def test_mcp_dw_query(api, mcp):
    listing = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = [t["name"] for t in listing["result"]["tools"]]
    assert "dw_query" in names and len(names) == 6
    call = mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                       "params": {"name": "dw_query",
                                  "arguments": {"id": "trades", "limit": 2,
                                                "where": [{"field": "code", "op": "=",
                                                           "value": "000001"}]}}})
    assert call["result"]["isError"] is False
    obj = json.loads(call["result"]["content"][0]["text"])
    assert obj["rows"] and obj["state"] == "ok"
    bad = mcp.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                      "params": {"name": "dw_query", "arguments": {"id": "trades",
                                                                   "limit": 9999}}})
    assert bad["result"]["isError"] is True


def test_mcp_stdio_subprocess():
    """真实 stdio 管道：initialize + tools/list + tools/call 全链路（只读）。"""
    import subprocess
    mcp_path = REPO / "agent_platform" / "tools" / "dw_governance_mcp.py"
    env = dict(os.environ)
    env["DW_QUERY_AUDIT"] = "off"
    msgs = "\n".join([
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
        json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                    "params": {"name": "dw_query",
                               "arguments": {"id": "no_such_dataset", "limit": 1}}}),
    ]) + "\n"
    proc = subprocess.run([sys.executable, "-X", "utf8", str(mcp_path)],
                          input=msgs, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=180, env=env)
    assert proc.returncode == 0, proc.stderr[-500:]
    replies = {}
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line:
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            replies[obj.get("id")] = obj
    tools = [t["name"] for t in replies[2]["result"]["tools"]]
    assert "dw_query" in tools and len(tools) == 6
    # 不存在的 dataset → isError=True（fail-visible，不假绿）
    assert replies[3]["result"]["isError"] is True


# ---------- 覆盖率脚本判定函数 ----------
def test_coverage_judge_dims(reg):
    path = REPO / "tools" / "datawarehouse" / "query_coverage.py"
    spec = importlib.util.spec_from_file_location("query_coverage_t50", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    sqlite_row = mod.judge_dataset(reg, "trades")
    assert sqlite_row["L"] and sqlite_row["P"] and sqlite_row["F"] and sqlite_row["A"]
    assert sqlite_row["carrier"] == "sqlite_table"
    pq_row = mod.judge_dataset(reg, "pq_ds")
    assert pq_row["L"] and pq_row["P"] and pq_row["F"] and pq_row["A"]
    # P2：jsonl 行级、mixed/nested 文件级均达四维
    jl_row = mod.judge_dataset(reg, "jl_ds")
    assert jl_row["carrier"] == "dir_jsonl"
    assert jl_row["L"] and jl_row["P"] and jl_row["F"] and jl_row["A"]
    mixed_row = mod.judge_dataset(reg, "mixed_ds")
    assert mixed_row["carrier"] == "dir_files"
    assert mixed_row["L"] and mixed_row["P"] and mixed_row["F"] and mixed_row["A"]
    nested_row = mod.judge_dataset(reg, "nested_ds")
    assert nested_row["carrier"] == "dir_files" and nested_row["L"]
    empty_row = mod.judge_dataset(reg, "empty_ds")
    assert not empty_row["ready"] and empty_row.get("excluded") is True
    pg_row = mod.judge_dataset(reg, "pg_ds")
    assert not pg_row["ready"] and "not_supported" in (pg_row["reason"] or "")

# ---------- 2026-11-02 历史数据验收发现的 3 个 P1 回归 ----------
def test_sqlite_since_iso_text_matches_direct_sql():
    """sqlite since 对 ISO 文本列不假绿：与直连 SQL 计数一致。"""
    import sqlite3
    from dataset_registry import DatasetRegistry

    r = DatasetRegistry()
    q = r.query("xq_xq_status", since="2026-10-01", limit=1, count=True)
    assert q.get("state") == "ok"
    row = [d for d in r.datasets if d.get("dataset_id") == "xq_xq_status"][0]
    p = Path(row["path"])
    if not p.is_absolute():
        p = Path(__file__).resolve().parents[3] / p
    con = sqlite3.connect("file:%s?mode=ro" % p.as_posix(), uri=True)
    try:
        expect = con.execute(
            "SELECT COUNT(*) FROM xq_status WHERE created_at >= '2026-10-01'").fetchone()[0]
    finally:
        con.close()
    assert q.get("count") == expect and expect > 0


def test_files_since_works_and_counts():
    """files 载体 since 不再 AttributeError。"""
    from dataset_registry import DatasetRegistry

    r = DatasetRegistry()
    q = r.query("research_reports", since="2026-10-01", limit=3, count=True)
    assert q.get("state") == "ok"
    assert q.get("count") is not None and q["count"] > 0


def test_files_order_size_numeric_desc():
    """files 载体 order size 必须按数值而非字符串排序。"""
    from dataset_registry import DatasetRegistry

    r = DatasetRegistry()
    q = r.query("research_reports", order=[{"field": "size", "desc": True}], limit=5)
    ci = q["columns"].index("size")
    sizes = [row[ci] for row in q.get("rows", [])]
    assert len(sizes) >= 2
    assert sizes == sorted(sizes, reverse=True)
    assert all(isinstance(s, (int, float)) for s in sizes)
