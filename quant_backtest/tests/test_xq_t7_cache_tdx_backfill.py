# -*- coding: utf-8 -*-
"""T7 缓存 TDX 离线补齐测试（tools/xq_t7_cache_tdx_backfill.py）。

覆盖：正常 .day 解析、坏日期整股剔除、重复日期整股剔除、缺文件记报告、
复权口径标注、合并不覆盖已有条目 + 备份 + 原子写回、审计 INCONCLUSIVE 语义。
全部使用临时夹具（tmp 缓存 / tmp vipdoc / tmp sqlite），零网络、
不触碰真实缓存与真库。
"""
import json
import os
import sqlite3
import struct
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

import xq_t7_cache_tdx_backfill as B  # noqa: E402
import xq_factor_v1 as factor         # noqa: E402

_TDX_DAY = struct.Struct("<IIIIIfII")


# ── 合成夹具 ─────────────────────────────────────────────────

def pack_day(date_int, o, h, low, c, amount=1.2e8, vol=3_000_000):
    """一条记录 → 32B（价格为分）。"""
    return _TDX_DAY.pack(date_int, o, h, low, c, amount, vol, 0)


def write_day(path: Path, records, trailing=b""):
    """records 为 (date_int, o_fen, h_fen, l_fen, c_fen) 元组列表。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = b"".join(pack_day(*r) for r in records) + trailing
    path.write_bytes(data)
    return path


def make_records(n=5, base_date=20260901, base_o=1000, base_c=1050):
    """n 条合成记录（分）：逐日 +1 日期、收盘 +10 分。"""
    return [(base_date + i, base_o, base_c + 20, base_o - 20, base_c + i * 10)
            for i in range(n)]


def day_path_for(tdx_root: Path, symbol: str) -> Path:
    return tdx_root / symbol[:2].lower() / "lday" / (symbol.lower() + ".day")


@pytest.fixture()
def env(tmp_path):
    """tmp 三件套：缓存 JSON、vipdoc 根、只读 sqlite 库。"""
    cache_path = tmp_path / "daily_cache.json"
    tdx_root = tmp_path / "vipdoc"
    db_path = tmp_path / "xueqiu_info.db"
    con = sqlite3.connect(str(db_path))
    try:
        con.execute("CREATE TABLE xq_status (status_id INTEGER PRIMARY KEY,"
                    " created_at TEXT)")
        con.execute("CREATE TABLE xq_status_classified (status_id INTEGER,"
                    " sentiment TEXT, prompt_version TEXT, norm_tickers TEXT)")
        con.execute("INSERT INTO xq_status VALUES (1, '2026-09-01 10:00:00')")
        con.execute("INSERT INTO xq_status_classified VALUES (1, 'bullish',"
                    " 'v1.3', ?)", (json.dumps([
                        {"kind": "stock_a", "canonical": "SH600000"},
                        {"kind": "stock_a", "canonical": "SZ000001"},
                        {"kind": "index", "canonical": "SH000001"},
                    ]),))
        con.commit()
    finally:
        con.close()
    return {"cache_path": str(cache_path), "tdx_root": str(tdx_root),
            "db_path": str(db_path), "report_dir": str(tmp_path / "reports"),
            "data_root": str(tmp_path / "nodata")}


def write_cache(path, daily, meta=None, missing=None, errs=None):
    payload = {"_meta": meta or {"days": 10, "saved_at": "2026-10-19T00:00:00"},
               "daily": daily,
               "_missing": missing or [], "_errs": errs or []}
    Path(path).write_text(json.dumps(payload, ensure_ascii=False),
                          encoding="utf-8")
    return payload


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ── .day 解析 ────────────────────────────────────────────────

def test_parse_tdx_day_ok(tmp_path):
    """正常 .day：日期/价格（分→元）/升序；尾部残片忽略。"""
    p = write_day(tmp_path / "a.day", make_records(5), trailing=b"\x01\x02")
    rows = B.parse_tdx_day_file(p)
    assert len(rows) == 5
    assert rows[0]["trade_date"] == "20260901"
    assert rows[-1]["trade_date"] == "20260905"
    assert rows[0]["open"] == pytest.approx(10.00)
    assert rows[0]["close"] == pytest.approx(10.50)
    assert rows[0]["volume"] == 3_000_000
    assert [r["trade_date"] for r in rows] == sorted(
        r["trade_date"] for r in rows)


def test_parse_tdx_day_empty_raises(tmp_path):
    p = tmp_path / "empty.day"
    p.write_bytes(b"")
    with pytest.raises(ValueError):
        B.parse_tdx_day_file(p)


def test_symbol_to_day_path_mapping():
    """SH600000 → sh/lday/sh600000.day；SZ000001 → sz/lday/sz000001.day。"""
    assert str(B.symbol_to_day_path("SH600000", r"X:/vipdoc")).replace(
        "\\", "/") == "X:/vipdoc/sh/lday/sh600000.day"
    assert str(B.symbol_to_day_path("SZ000001", r"X:/vipdoc")).replace(
        "\\", "/") == "X:/vipdoc/sz/lday/sz000001.day"
    with pytest.raises(ValueError):
        B.symbol_to_day_path("BJ430047")


# ── 整股校验（与 _audit_daily_cache 同标准）─────────────────

def test_bad_date_excludes_whole_symbol(tmp_path):
    """坏日期（13 月）→ 整股不入缓存，只入报告。"""
    recs = make_records(3)
    bad = (20261399, 1000, 1050, 980, 1050)          # 13 月 39 日，strptime 必败
    p = write_day(tmp_path / "a.day", recs + [bad])
    rows = B.parse_tdx_day_file(p)
    ok, details, counts = B.validate_symbol_rows(rows)
    assert not ok
    assert any(d["reason"] == "invalid_trade_date" for d in details)
    assert counts["invalid_trade_date"] >= 1


def test_duplicate_date_excludes_whole_symbol(tmp_path):
    """重复日期 → 整股剔除。"""
    recs = make_records(3)
    p = write_day(tmp_path / "a.day", recs + [recs[-1]])
    rows = B.parse_tdx_day_file(p)
    ok, details, counts = B.validate_symbol_rows(rows)
    assert not ok
    assert counts["duplicate_trade_date"] >= 1


def test_nonpositive_price_excludes_symbol(tmp_path):
    """open<=0 → invalid_open，整股剔除。"""
    recs = make_records(2)
    bad = (20260910, 0, 1050, 980, 1050)             # open=0
    p = write_day(tmp_path / "a.day", recs + [bad])
    ok, details, _ = B.validate_symbol_rows(B.parse_tdx_day_file(p))
    assert not ok
    assert any(d["reason"] == "invalid_open" for d in details)


def test_clean_rows_pass(tmp_path):
    p = write_day(tmp_path / "a.day", make_records(4))
    ok, details, _ = B.validate_symbol_rows(B.parse_tdx_day_file(p))
    assert ok
    assert details == []


# ── plan_backfill：缺失发现 / 缺文件 / 坏行入报告 ────────────

def test_plan_missing_file_recorded_not_raised(tmp_path):
    """缺 .day 文件 → 记入报告（missing_file），不抛错、不入缓存。"""
    cache = write_cache(tmp_path / "c.json",
                        {"SH600000": [{"trade_date": "20260901",
                                       "open": 10.0, "close": 10.5}]})
    posts = [{"status_id": 1, "date": "2026-09-01", "sentiment": "bullish",
              "version": "v1.3", "stocks": ["SH600000", "SZ000001"]}]
    missing, per_symbol, additions = B.plan_backfill(
        posts, cache, str(tmp_path / "vipdoc"))
    assert missing == ["SZ000001"]
    assert per_symbol["SZ000001"]["status"] == "missing_file"
    assert "SZ000001" not in additions


def test_plan_bad_rows_symbol_excluded(tmp_path):
    """坏行整股剔除：该股 status=invalid_rows 且不入 additions。"""
    tdx_root = tmp_path / "vipdoc"
    good = write_day(day_path_for(tdx_root, "SH600000"), make_records(3))
    assert good.exists()
    bad = write_day(day_path_for(tdx_root, "SZ000001"),
                    make_records(2) + [(20261399, 1000, 1050, 980, 1050)])
    assert bad.exists()
    cache = write_cache(tmp_path / "c.json", {})
    posts = [{"status_id": 1, "date": "2026-09-01", "sentiment": "neutral",
              "version": "v1.3", "stocks": ["SH600000", "SZ000001"]}]
    missing, per_symbol, additions = B.plan_backfill(
        posts, cache, str(tdx_root))
    assert missing == ["SH600000", "SZ000001"]
    assert per_symbol["SH600000"]["status"] == "backfilled"
    assert per_symbol["SZ000001"]["status"] == "invalid_rows"
    assert "SZ000001" not in additions
    assert set(additions) == {"SH600000"}


def test_plan_additions_row_schema(tmp_path):
    """缓存行 schema 与既有缓存同构（ts_code/timestamp/chg/percent）。"""
    tdx_root = tmp_path / "vipdoc"
    write_day(day_path_for(tdx_root, "SH600000"), make_records(3))
    cache = write_cache(tmp_path / "c.json", {})
    posts = [{"status_id": 1, "date": "2026-09-01", "sentiment": "neutral",
              "version": "v1.3", "stocks": ["SH600000"]}]
    _, _, additions = B.plan_backfill(posts, cache, str(tdx_root))
    rows = additions["SH600000"]
    assert len(rows) == 3
    r0 = rows[0]
    assert r0["ts_code"] == "600000.SH"
    assert r0["timestamp"] == "2026-09-01 00:00:00"
    assert r0["trade_date"] == "20260901"
    assert r0["open"] == pytest.approx(10.0)
    assert r0["close"] == pytest.approx(10.5)
    assert r0["chg"] is None and r0["percent"] is None   # 首行无前收盘
    assert r0["turnoverrate"] is None
    assert r0["source"] == "tdx_lday_gui"
    assert rows[1]["chg"] == pytest.approx(0.1)          # 10.6-10.5


# ── merge_and_write：备份 / 不覆盖 / 原子写回 / 口径标注 ─────

def test_merge_preserves_existing_and_backs_up(tmp_path):
    """合并只增缺失标的；已有条目逐字节不变；备份含原始内容；
    _meta.source 注明 tdx_lday_gui 与复权口径警示。"""
    cache_path = str(tmp_path / "daily_cache.json")
    existing_rows = [{"trade_date": "20260901", "open": 7.93, "close": 7.89}]
    write_cache(cache_path, {"SH600000": existing_rows})
    before = Path(cache_path).read_bytes()
    additions = {"SH600000": [{"trade_date": "20260902"}],   # 撞键→跳过
                 "SZ000001": [{"trade_date": "20260902", "open": 1.0,
                               "close": 1.1}]}
    info = B.merge_and_write(cache_path, additions, "20261019T0000000800")
    assert info["cache_updated"] is True
    assert info["added"] == {"SZ000001": 1}
    assert info["skipped_existing"] == ["SH600000"]
    # 备份存在且为原始内容
    bak = Path(info["backup_path"])
    assert bak.name == "daily_cache.json.bak-20261019T0000000800"
    assert bak.read_bytes() == before
    # 已有条目逐字节不变
    after = read_json(cache_path)
    assert after["daily"]["SH600000"] == existing_rows
    assert after["daily"]["SZ000001"] == additions["SZ000001"]
    assert after["_missing"] == [] and after["_errs"] == []
    # 口径标注
    src = after["_meta"]["source"]
    assert "tdx_lday_gui" in src
    assert "tushare qfq" in src["tdx_lday_gui"]["adjustment_note"] \
        or B.ADJUSTMENT_NOTE == src["tdx_lday_gui"]["adjustment_note"]


def test_merge_empty_additions_touches_nothing(tmp_path):
    """零可并入标的 → 不改缓存、不产生备份（缺文件场景全量走此分支）。"""
    cache_path = str(tmp_path / "daily_cache.json")
    write_cache(cache_path, {"SH600000": []})
    before = Path(cache_path).read_bytes()
    info = B.merge_and_write(cache_path, {}, "20261019T0000000800")
    assert info["cache_updated"] is False
    assert info["backup_path"] is None
    assert Path(cache_path).read_bytes() == before
    assert not list(tmp_path.glob("*.bak-*"))


def test_merge_atomic_no_tmp_leftover(tmp_path):
    cache_path = str(tmp_path / "daily_cache.json")
    write_cache(cache_path, {})
    B.merge_and_write(cache_path, {"SZ000001": [{"trade_date": "20260902",
                                                 "open": 1.0, "close": 1.1}]},
                      "20261019T0000000800")
    assert not Path(cache_path + ".tmp").exists()
    assert read_json(cache_path)["daily"]["SZ000001"]


# ── run 端到端：报告 / 审计语义 / 零网络 ─────────────────────

def test_run_end_to_end_report_and_inconclusive(env, tmp_path):
    """端到端：补齐 1 只、缺文件 1 只 → 报告落盘，前后审计均
    INCONCLUSIVE 且 effectiveness_conclusion=None（纪律）。"""
    write_day(day_path_for(Path(env["tdx_root"]), "SH600000"),
              make_records(30))                          # 覆盖若干周
    # SZ000001 有帖但无 .day 文件 → missing_file
    write_cache(env["cache_path"], {})
    report = B.run(cache_path=env["cache_path"], db_path=env["db_path"],
                   tdx_root=env["tdx_root"], report_dir=env["report_dir"],
                   data_root=env["data_root"])
    assert report["counts"]["missing"] == 2
    assert report["counts"]["backfilled"] == 1
    assert report["counts"]["missing_file"] == 1
    assert "SH600000" in report["missing_symbols"]
    assert "SZ000001" in report["missing_symbols"]
    assert report["per_symbol"]["SZ000001"]["status"] == "missing_file"
    # 纪律：INCONCLUSIVE，无有效性结论
    for side in ("audit_before", "audit_after"):
        assert report[side]["audit_decision"] == "INCONCLUSIVE"
        assert report[side]["effectiveness_conclusion"] is None
    # 报告落盘
    rp = Path(report["report_path"])
    assert rp.exists() and rp.parent.name == "reports"
    assert json.loads(rp.read_text(encoding="utf-8"))["tool"] == \
        "xq_t7_cache_tdx_backfill"
    # 合并后缓存新增 SH600000
    assert "SH600000" in read_json(env["cache_path"])["daily"]


def test_run_missing_file_leaves_cache_untouched(env):
    """vipdoc 全空 → 缓存文件逐字节不变、无备份、cache_updated=False。"""
    cache_path = env["cache_path"]
    write_cache(cache_path, {"SH600000": [{"trade_date": "20260901",
                                           "open": 10.0, "close": 10.5}]})
    before = Path(cache_path).read_bytes()
    report = B.run(cache_path=cache_path, db_path=env["db_path"],
                   tdx_root=env["tdx_root"], report_dir=env["report_dir"])
    assert report["cache_updated"] is False
    assert report["backup_path"] is None
    assert Path(cache_path).read_bytes() == before


def test_run_backfill_improves_coverage(env):
    """补齐后 cached_candidate_symbols 上升、missing_symbol 缺口下降。"""
    write_day(day_path_for(Path(env["tdx_root"]), "SH600000"),
              make_records(30))
    write_cache(env["cache_path"], {})
    report = B.run(cache_path=env["cache_path"], db_path=env["db_path"],
                   tdx_root=env["tdx_root"], report_dir=env["report_dir"],
                   data_root=env["data_root"])
    b, a = report["audit_before"], report["audit_after"]
    assert a["cached_candidate_symbols"] > b["cached_candidate_symbols"]
    assert len(a["unique_missing_symbols"]) < \
        len(b["unique_missing_symbols"])
    # 即便补齐，audit_status 仍不声称 complete（SZ000001 仍缺文件）
    assert a["audit_decision"] == "INCONCLUSIVE"


def test_run_creates_backup_and_source_note(env):
    write_day(day_path_for(Path(env["tdx_root"]), "SH600000"),
              make_records(5))
    write_cache(env["cache_path"], {})
    report = B.run(cache_path=env["cache_path"], db_path=env["db_path"],
                   tdx_root=env["tdx_root"], report_dir=env["report_dir"],
                   data_root=env["data_root"])
    assert report["cache_updated"] is True
    bak = Path(report["backup_path"])
    assert bak.exists()
    assert "daily_cache.json.bak-" in bak.name
    meta = read_json(env["cache_path"])["_meta"]
    assert B.ADJUSTMENT_NOTE == meta["source"]["tdx_lday_gui"]["adjustment_note"]


def test_offline_audit_pure_in_memory(env, monkeypatch):
    """offline_audit 全程不发网络：即便有人误用网络库也立刻显形。"""
    def _boom(*a, **k):                              # noqa: ANN002, ANN003
        raise AssertionError("审计流程禁止任何网络调用")
    import socket
    monkeypatch.setattr(socket.socket, "connect", _boom)
    posts = factor.load_posts(env["db_path"])
    write_cache(env["cache_path"], {})
    cache = read_json(env["cache_path"])
    result = factor.offline_audit(posts, cache, env["db_path"],
                                  env["cache_path"])
    assert result["audit_decision"] == "INCONCLUSIVE"
    assert result["effectiveness_conclusion"] is None


# ── 本地行情源只读普查 ───────────────────────────────────────

def test_symbol_to_ts_code():
    assert B.symbol_to_ts_code("SH600938") == "600938.SH"
    assert B.symbol_to_ts_code("SZ000015") == "000015.SZ"
    with pytest.raises(ValueError):
        B.symbol_to_ts_code("BJ430047")


class _FakeTable:
    """模拟 pandas DataFrame 的最小接口（list(df[col]) 可用）。"""

    def __init__(self, cols):
        self._cols = cols
        self.columns = list(cols.keys())

    def __getitem__(self, name):
        return list(self._cols[name])


def test_census_per_date_parts_with_fake_reader(tmp_path):
    """按日部件普查：窗口过滤、per-symbol 行数/首末日、adj_factor 同扫。"""
    parts = tmp_path / "daily"
    adj = tmp_path / "adj_factor"
    parts.mkdir(parents=True)
    adj.mkdir(parents=True)
    for d in ("20180102", "20190605", "20231229", "20240102"):  # 最后一个出窗
        for root in (parts, adj):
            (root / ("d_%s.parquet" % d)).write_bytes(b"stub")
    mapping = {
        "20180102": {"600000.SH", "000015.SZ"},
        "20190605": {"600000.SH", "000015.SZ", "601288.SH"},
        "20231229": {"600000.SH"},
        "20240102": {"600000.SH"},                  # 出窗，不计数
    }

    def reader(path, columns):                      # noqa: ANN001
        td = os.path.basename(str(path))[2:10]
        return _FakeTable({"ts_code": mapping[td]})

    syms = ["SH600000", "SH601288", "SZ000015"]
    r = B.census_per_date_parts(str(parts), syms,
                                ("20180101", "20231231"),
                                reader=reader, adj_dir=str(adj),
                                note="测试口径说明")
    assert r["parts_in_window"] == 3 and r["parts_total"] == 4
    assert r["span_in_window"] == ["20180102", "20231229"]
    assert r["covered_symbols"] == ["SH600000", "SH601288", "SZ000015"]
    assert r["per_symbol"]["SH600000"] == {"rows": 3,
                                           "first": "20180102",
                                           "last": "20231229"}
    assert r["per_symbol"]["SH601288"]["rows"] == 1
    assert r["adj_factor"]["parts_in_window"] == 3
    assert "adjustment_note" in r


def test_census_per_date_parts_dir_missing(tmp_path):
    r = B.census_per_date_parts(str(tmp_path / "nope"), ["SH600000"],
                                ("20180101", "20231231"),
                                reader=lambda p, c: _FakeTable({"ts_code": []}))
    assert r["status"] == "dir_missing"


def test_census_ashare_per_symbol(tmp_path):
    """单票 parquet 普查：缺文件记 file=None，窗口外日期不计。"""
    d = tmp_path / "ashare"
    d.mkdir()
    (d / "600000_SH.parquet").write_bytes(b"stub")
    (d / "300104_SZ.parquet").write_bytes(b"stub")

    def reader(path, columns):                      # noqa: ANN001
        name = os.path.basename(str(path))
        if name.startswith("600000"):
            dates = ["20180102", "20191231", "20230103"]
        else:                                       # 300104 全在窗外
            dates = ["20240102", "20250601"]
        return _FakeTable({"trade_date": dates})

    r = B.census_ashare_per_symbol(str(d), ["SH600000", "SZ300104",
                                            "SZ000015"],
                                   ("20180101", "20231231"), reader=reader)
    assert r["covered_symbols"] == ["SH600000"]
    assert r["per_symbol"]["SH600000"]["rows_in_window"] == 3
    assert r["per_symbol"]["SZ300104"]["rows_in_window"] == 0
    assert r["per_symbol"]["SZ000015"]["file"] is None


def test_local_source_census_engine_missing():
    """引擎缺失 → fail-visible 跳过，不抛错、sources 为空。"""
    r = B.local_source_census(["SH600000"], data_root="X:/nope",
                              engine_probe=lambda: None)
    assert r["engine"] is None
    assert "普查跳过" in r["note"]
    assert r["sources"] == {}


def test_local_source_census_with_fake_reader(tmp_path):
    """多源普查：单源目录缺失被隔离，union 覆盖正确汇总。"""
    daily = tmp_path / "tushare" / "daily"
    daily.mkdir(parents=True)
    (daily / "d_20200106.parquet").write_bytes(b"stub")
    (daily / "d_20200107.parquet").write_bytes(b"stub")

    def reader(path, columns):                      # noqa: ANN001
        return _FakeTable({"ts_code": ["600000.SH", "601288.SH"]})

    r = B.local_source_census(["SH600000", "SH601288", "SZ000015"],
                              data_root=str(tmp_path),
                              reader=reader,
                              engine_probe=lambda: "pandas-test")
    assert r["engine"] == "pandas-test"
    tw = r["sources"]["tushare_warehouse"]
    assert tw["covered_count"] == 2
    assert tw["covered_symbols"] == ["SH600000", "SH601288"]
    # 其余源目录不存在 → dir_missing 被隔离，不拖垮
    assert r["sources"]["ashare_per_symbol"]["status"] == "dir_missing"
    assert r["sources"]["kg_ts_daily_snapshot"]["status"] == "dir_missing"
    assert r["covered_union_count"] == 2
    assert r["uncovered_union_symbols"] == ["SZ000015"]
    assert "未并入缓存" in r["discipline"]


def test_local_source_census_single_source_error_isolated(tmp_path):
    """单源 reader 抛错 → 记 error，其余源继续（隔离不扩散）。"""
    daily = tmp_path / "tushare" / "daily"
    daily.mkdir(parents=True)
    (daily / "d_20200106.parquet").write_bytes(b"stub")

    def bad_reader(path, columns):                  # noqa: ANN001
        raise OSError("boom")

    r = B.local_source_census(["SH600000"], data_root=str(tmp_path),
                              reader=bad_reader,
                              engine_probe=lambda: "pandas-test")
    assert r["sources"]["tushare_warehouse"]["status"] == "error"
    assert r["covered_union_count"] == 0


def test_run_includes_census_section(env):
    """run() 报告必含 local_source_census 段（引擎无关，结构完整）。"""
    write_cache(env["cache_path"], {})
    report = B.run(cache_path=env["cache_path"], db_path=env["db_path"],
                   tdx_root=env["tdx_root"], report_dir=env["report_dir"],
                   data_root=env["data_root"])
    c = report["local_source_census"]
    assert c["window"] == ["20180101", "20231231"]
    assert c["covered_union_count"] == 0
    assert c["uncovered_union_symbols"] == sorted(report["missing_symbols"])
    assert "未并入缓存" in c["discipline"]
    if c["engine"] is not None:                     # 有引擎时逐源结构齐全
        assert set(c["sources"]) == {"tushare_warehouse", "tushare_v2",
                                     "ashare_per_symbol",
                                     "kg_ts_daily_snapshot"}
    else:                                           # 缺引擎 fail-visible
        assert "普查跳过" in c["note"]
        assert c["sources"] == {}
