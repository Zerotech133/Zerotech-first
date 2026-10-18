# -*- coding: utf-8 -*-
"""P0-4 行情替代源探针测试（tools/xq_quote_alt_probe.py）。

覆盖：.day 二进制合成解析（OHLC/日期/limit）、文件缺失容错、残片容错、
akshare 缺库优雅 N/A、tushare token 缺失 N/A、verdict 优先级、报告落盘。
"""
import json
import struct
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

import xq_quote_alt_probe as P  # noqa: E402


# ── 合成 .day 字节流 ─────────────────────────────────────────

def make_day_records(n=20, base_date=20261001, base_close=150000):
    """生成 n 条合成记录（未打包 dict，单位：分）。"""
    recs = []
    for i in range(n):
        c = base_close + i * 100          # 收盘逐日 +1 元
        recs.append((base_date + i, c - 200, c + 300, c - 400, c,
                     1.23e8 + i, 5_000_000 + i * 1000, 0))
    return recs


def pack_day(recs):
    return b"".join(P._TDX_DAY.pack(*r) for r in recs)


@pytest.fixture()
def tdx_tmp(tmp_path):
    """搭一个迷你 vipdoc：sh600519(20根) + sz000001(10根)。"""
    sh = tmp_path / "vipdoc" / "sh" / "lday"
    sz = tmp_path / "vipdoc" / "sz" / "lday"
    sh.mkdir(parents=True)
    sz.mkdir(parents=True)
    (sh / "sh600519.day").write_bytes(
        pack_day(make_day_records(20, base_close=1_450_000)))   # 1.45万元
    (sz / "sz000001.day").write_bytes(
        pack_day(make_day_records(10, base_close=1_100)))  # 11 元
    return tmp_path


# 1) 合成字节流解析：OHLC / 日期正确（分→元换算）
def test_parse_tdx_day_synthetic_ohlc_and_date():
    recs = make_day_records(5)
    f = Path(__import__("tempfile").gettempdir()) / "_t_probe_a.day"
    f.write_bytes(pack_day(recs))
    rows = P.parse_tdx_day(f)
    assert len(rows) == 5
    last = rows[-1]
    assert last["date"] == "20261005"
    assert last["open"] == pytest.approx(recs[-1][1] / 100.0)
    assert last["high"] == pytest.approx(recs[-1][2] / 100.0)
    assert last["low"] == pytest.approx(recs[-1][3] / 100.0)
    assert last["close"] == pytest.approx(recs[-1][4] / 100.0)
    assert last["amount"] == pytest.approx(recs[-1][5])
    assert last["vol"] == recs[-1][6]
    f.unlink()


# 2) limit=20 只取最近 N 根且保持升序
def test_parse_tdx_day_limit_returns_tail_ascending():
    recs = make_day_records(30)
    f = Path(__import__("tempfile").gettempdir()) / "_t_probe_b.day"
    f.write_bytes(pack_day(recs))
    rows = P.parse_tdx_day(f, limit=20)
    assert len(rows) == 20
    assert [r["date"] for r in rows] == ["%08d" % d for d, *_ in recs[10:]]
    f.unlink()


# 3) 文件缺失容错：明确抛 FileNotFoundError（探针层捕获降级）
def test_parse_tdx_day_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        P.parse_tdx_day(tmp_path / "nope" / "sh600519.day")


# 4) 尾部残片容错：31 字节残尾被忽略，不影响有效记录
def test_parse_tdx_day_tolerates_trailing_partial_record():
    recs = make_day_records(4)
    f = Path(__import__("tempfile").gettempdir()) / "_t_probe_c.day"
    f.write_bytes(pack_day(recs) + b"\x01\x02\x03")   # 3 字节残片
    rows = P.parse_tdx_day(f)
    assert len(rows) == 4
    f.unlink()


# 5) 探针：安装目录不存在 → ok=False 优雅降级
def test_probe_tdx_missing_install_dir(tmp_path):
    r = P.probe_tdx(base_dir=tmp_path / "not_installed", limit=20)
    assert r["source"] == "tdx_local" and r["ok"] is False
    assert "未找到通达信安装目录" in r["err"]


# 6) 探针：文件缺失但目录存在 → ok=False 带文件缺失原因
def test_probe_tdx_day_files_missing(tmp_path):
    (tmp_path / "vipdoc" / "sh" / "lday").mkdir(parents=True)
    r = P.probe_tdx(base_dir=tmp_path, limit=20)
    assert r["ok"] is False and "文件缺失" in r["err"]


# 7) 探针：完整合成 TDX 目录 → ok=True，600519 千元级校验通过
def test_probe_tdx_ok_and_sanity(tdx_tmp):
    r = P.probe_tdx(base_dir=tdx_tmp, limit=20)
    assert r["ok"] is True, r["err"]
    assert r["sample_ohlc_last3"][-1]["close"] > 1000   # 千元级
    assert r["all_samples"]["sz000001"][-1]["close"] == 20.0  # 末根 20 元


# 8) akshare 缺库：优雅 N/A（monkeypatch import 失败）
def test_probe_akshare_graceful_na(monkeypatch):
    def boom():
        raise ImportError("No module named 'akshare'")
    monkeypatch.setattr(P, "_import_akshare", boom)
    r = P.probe_akshare()
    assert r["ok"] is False
    assert "N/A" in r["err"] and "akshare" in r["err"]


# 9) tushare token 不可得：优雅 N/A
def test_probe_tushare_token_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    r = P.probe_tushare(repo_root=tmp_path)   # 空 tmp 目录：无 .env.local
    assert r["ok"] is False
    assert "N/A" in r["err"] and r.get("token_found") is False


# 10) verdict：tdx_local 优先于 akshare/tushare
def test_verdict_prefers_tdx():
    results = [
        {"source": "tdx_local", "ok": True, "latency_ms": 1,
         "sample_ohlc_last3": [], "err": ""},
        {"source": "akshare", "ok": True, "latency_ms": 500,
         "sample_ohlc_last3": [], "err": ""},
        {"source": "tushare", "ok": True, "latency_ms": 300,
         "sample_ohlc_last3": [], "err": ""},
    ]
    v = P.build_verdict(results)
    assert v["primary"] == "tdx_local"
    assert v["fallback"] == ["akshare", "tushare"]


# 11) verdict：主源失败时落到备选
def test_verdict_falls_back():
    results = [
        {"source": "tdx_local", "ok": False, "latency_ms": 0,
         "sample_ohlc_last3": [], "err": "x"},
        {"source": "akshare", "ok": False, "latency_ms": 0,
         "sample_ohlc_last3": [], "err": "x"},
        {"source": "tushare", "ok": True, "latency_ms": 300,
         "sample_ohlc_last3": [], "err": ""},
    ]
    assert P.build_verdict(results)["primary"] == "tushare"


# 12) 报告落盘：含三源对比表 / verdict / 字段映射
def test_write_report(tmp_path, tdx_tmp):
    results = [P.probe_tdx(base_dir=tdx_tmp, limit=20)]
    results.append({"source": "akshare", "ok": False, "latency_ms": 0,
                    "sample_ohlc_last3": [], "err": "N/A"})
    results.append({"source": "tushare", "ok": False, "latency_ms": 0,
                    "sample_ohlc_last3": [], "err": "N/A"})
    v = P.build_verdict(results)
    out = tmp_path / "report.md"
    p = P.write_report(results, v, path=out)
    assert Path(p).exists()
    txt = out.read_text(encoding="utf-8")
    for key in ("三源对比", "tdx_local", "verdict", "xq_quote_snap 字段",
                "parse_tdx_day"):
        assert key in txt
    assert "| tdx_local | ✅" in txt
    # 附录 JSON 可反序列化
    js = txt.split("```json")[1].split("```")[0]
    assert json.loads(js)["verdict"]["primary"] == "tdx_local"


# 13) 代理清理：清空代理变量并设 NO_PROXY
def test_clear_proxy_env(monkeypatch):
    for k in ("HTTP_PROXY", "https_proxy"):
        monkeypatch.setenv(k, "http://127.0.0.1:7890")
    removed = P.clear_proxy_env()
    import os
    assert "HTTP_PROXY" in removed
    assert os.environ.get("HTTP_PROXY") is None
    assert os.environ.get("NO_PROXY") == "*"
