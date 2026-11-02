# -*- coding: utf-8 -*-
"""guba 采集器 P0 修复测试（2026-11-02 task-61）。

覆盖：fetch 状态可分辨（ok/empty/no_marker/http/exception）、空结果不落盘、
增量合并去重、无新增不重写、退避重试 ≤3、分批上限（pool 模式）、失败率 >20% rc≠0、
全成功/全空 rc=0、告警链直调、旧 CLI 显式清单兼容。
**全部离线**：mock requests / fetch / crawl，不触网、不写生产 data/guba。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "quant_backtest"))

import guba_crawl as gc  # noqa: E402


def _resp(text="", status=200):
    class R:
        status_code = status

        def __init__(self):
            self.text = text

    return R()


MARK = 'var article_list=%s;\nvar other=1</script>'


def _rows(n=2, prefix="p"):
    return [{"post_id": "%s%d" % (prefix, i),
             "title": "t%d" % i,
             "ts": "2026-11-01 10:0%d" % i,
             "click": i, "comment": 0, "user": "u", "bb": None, "news": 0}
            for i in range(1, n + 1)]


# ---------- fetch 状态可分辨 ----------
def test_fetch_page_status_ok_empty(monkeypatch):
    payload = {"re": [{"post_id": "1", "post_title": "标题",
                       "post_publish_time": "2026-11-01 09:00"}]}
    monkeypatch.setattr(gc.requests, "get",
                        lambda *a, **k: _resp(MARK % json.dumps(payload)))
    rows, status = gc.fetch_page("000001", 1)
    assert status == "ok" and len(rows) == 1
    assert rows[0]["post_id"] == "1" and rows[0]["title"] == "标题"

    monkeypatch.setattr(gc.requests, "get",
                        lambda *a, **k: _resp(MARK % json.dumps({"re": []})))
    rows, status = gc.fetch_page("000001", 1)
    assert status == "empty" and rows == []


def test_fetch_page_status_no_marker_http_exception(monkeypatch):
    # 限流/验证页：无 article_list → no_marker（绝不能当"该股无帖"）
    monkeypatch.setattr(gc.requests, "get", lambda *a, **k: _resp("<html>验证页</html>"))
    assert gc.fetch_page("000001", 1) == ([], "no_marker")
    # HTTP 429 → http_429
    monkeypatch.setattr(gc.requests, "get", lambda *a, **k: _resp("", 429))
    assert gc.fetch_page("000001", 1) == ([], "http_429")
    # HTTP 500 → http_5xx
    monkeypatch.setattr(gc.requests, "get", lambda *a, **k: _resp("", 500))
    assert gc.fetch_page("000001", 1) == ([], "http_5xx")

    def _boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(gc.requests, "get", _boom)
    assert gc.fetch_page("000001", 1) == ([], "exception")
    # 兼容旧语义：fetch_page_rows 只返回 rows
    assert gc.fetch_page_rows("000001", 1) == []


# ---------- 退避重试 ≤3 ----------
def test_retry_backoff_max_attempts(monkeypatch):
    calls, sleeps = [], []

    def _fake_fetch(c6, page, timeout=12):
        calls.append((c6, page))
        return [], "no_marker"

    monkeypatch.setattr(gc, "fetch_page", _fake_fetch)
    rows, status, attempts = gc.fetch_page_with_retry(
        "000001", 1, max_attempts=3, backoff_base=2.0, sleep=sleeps.append)
    assert (rows, status, attempts) == ([], "no_marker", 3)
    assert len(calls) == 3 and len(sleeps) == 2
    assert sleeps == [2.0, 4.0]  # 指数退避
    # 不可重试状态（404）不重试
    monkeypatch.setattr(gc, "fetch_page",
                        lambda *a, **k: ([], "http_404"))
    sleeps2 = []
    rows, status, attempts = gc.fetch_page_with_retry(
        "000001", 1, sleep=sleeps2.append)
    assert status == "http_404" and attempts == 1 and sleeps2 == []
    # 成功立即返回，不重试
    monkeypatch.setattr(gc, "fetch_page", lambda *a, **k: (_rows(1), "ok"))
    assert gc.fetch_page_with_retry("000001", 1, sleep=sleeps.append)[2] == 1


# ---------- 空结果不落盘 ----------
def test_crawl_empty_result_does_not_write(tmp_path, monkeypatch):
    monkeypatch.setattr(gc, "OUT_DIR", str(tmp_path))
    monkeypatch.setattr(gc, "fetch_page_with_retry",
                        lambda *a, **k: ([], "empty", 1))
    res = gc.crawl("000001.SZ", pages=1, sleep=lambda *_: None)
    assert res["status"] == "empty" and res["n_new"] == 0
    assert not (tmp_path / "000001.jsonl").exists()  # 不产生 0 字节空占位
    # 错误（限流/异常）同样不落盘
    monkeypatch.setattr(gc, "fetch_page_with_retry",
                        lambda *a, **k: ([], "no_marker", 3))
    res = gc.crawl("000002.SZ", pages=1, sleep=lambda *_: None)
    assert res["status"] == "error" and not (tmp_path / "000002.jsonl").exists()


# ---------- 成功落盘 + 增量合并 + 无新增不重写 ----------
def test_crawl_ok_incremental_merge(tmp_path, monkeypatch):
    monkeypatch.setattr(gc, "OUT_DIR", str(tmp_path))
    rows = _rows(2)
    monkeypatch.setattr(gc, "fetch_page_with_retry",
                        lambda *a, **k: (rows, "ok", 1))
    res = gc.crawl("000003.SZ", pages=1, sleep=lambda *_: None)
    assert res["status"] == "ok" and res["n_new"] == 2 and res["n_total"] == 2
    path = tmp_path / "000003.jsonl"
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2
    # 再跑：同数据 → n_new=0 且**不重写**（mtime 不变）
    mtime = path.stat().st_mtime_ns
    res2 = gc.crawl("000003.SZ", pages=1, sleep=lambda *_: None)
    assert res2["n_new"] == 0 and path.stat().st_mtime_ns == mtime
    # 新增一条 → 合并去重
    rows2 = rows + [{"post_id": "p9", "title": "new", "ts": "2026-11-02 09:00",
                     "click": 0, "comment": 0, "user": "", "bb": None, "news": 0}]
    monkeypatch.setattr(gc, "fetch_page_with_retry",
                        lambda *a, **k: (rows2, "ok", 1))
    res3 = gc.crawl("000003.SZ", pages=1, sleep=lambda *_: None)
    assert res3["n_new"] == 1 and res3["n_total"] == 3


# ---------- 分批上限 / 随机顺序 ----------
def test_pool_batch_cap_and_index(tmp_path, monkeypatch):
    monkeypatch.setattr(gc, "OUT_DIR", str(tmp_path))
    codes = ["%06d.SZ" % i for i in range(1, 41)]
    monkeypatch.setattr(gc, "pool_codes", lambda *a, **k: list(codes))
    calls = []

    def _fake_crawl(tc, **kw):
        calls.append(tc)
        return {"code": gc.code6(tc), "status": "ok", "n_new": 0,
                "n_total": 0, "n_errors": 0, "page_statuses": [],
                "max_ts": "", "pages": 1}

    monkeypatch.setattr(gc, "crawl", _fake_crawl)
    rc = gc.main(["--pool", "--no-shuffle", "--batch-size", "15",
                  "--no-alert"])
    assert rc == 0 and len(calls) == 15  # 每轮 ≤15 码
    calls.clear()
    rc = gc.main(["--pool", "--no-shuffle", "--batch-size", "15",
                  "--batch-index", "1", "--no-alert"])
    assert rc == 0 and len(calls) == 15
    calls.clear()
    rc = gc.main(["--pool", "--no-shuffle", "--batch-size", "15",
                  "--batch-index", "2", "--no-alert"])
    assert rc == 0 and len(calls) == 10  # 第 3 批 10 只
    assert len(set(calls)) == 10


def test_explicit_codes_compat_all_processed(tmp_path, monkeypatch):
    monkeypatch.setattr(gc, "OUT_DIR", str(tmp_path))
    calls = []
    monkeypatch.setattr(gc, "crawl",
                        lambda tc, **kw: (calls.append(tc) or {
                            "code": gc.code6(tc), "status": "ok", "n_new": 0,
                            "n_total": 0, "page_statuses": [], "max_ts": "",
                            "pages": 1}))
    codes = ",".join("%06d.SZ" % i for i in range(1, 21))
    rc = gc.main([codes, "--no-shuffle", "--batch-size", "15", "--no-alert"])
    assert rc == 0 and len(calls) == 20  # 显式清单兼容：全部处理（分批执行）


# ---------- 失败率 / rc / 告警 ----------
def test_fail_rate_rc_and_alert(tmp_path, monkeypatch):
    monkeypatch.setattr(gc, "OUT_DIR", str(tmp_path))
    monkeypatch.setattr(gc, "pool_codes", lambda *a, **k: [])

    def _mk(status):
        def _fake_crawl(tc, **kw):
            return {"code": gc.code6(tc), "status": status,
                    "n_new": 0, "n_total": 0, "page_statuses": [],
                    "max_ts": "", "pages": 1}
        return _fake_crawl

    # 5 码中 2 失败 = 40% > 20% → rc=2
    seq = [_mk("ok"), _mk("ok"), _mk("ok"), _mk("error"), _mk("error")]
    monkeypatch.setattr(gc, "crawl", lambda tc, **kw: seq.pop(0)(tc, **kw))
    alert = []
    monkeypatch.setattr(gc, "_alert_fail",
                        lambda rc, log, enabled=True: alert.append((rc, log, enabled)))
    rc = gc.main(["000001.SZ,000002.SZ,000003.SZ,000004.SZ,000005.SZ",
                  "--no-shuffle", "--alert-direct", "--no-alert"])
    assert rc == 2 and alert == []  # --no-alert 时即使 rc≠0 也不告警

    # 同一失败率，不 --no-alert + --alert-direct → 调用告警链
    seq = [_mk("ok"), _mk("ok"), _mk("ok"), _mk("error"), _mk("error")]
    monkeypatch.setattr(gc, "crawl", lambda tc, **kw: seq.pop(0)(tc, **kw))
    alert.clear()
    rc = gc.main(["000001.SZ,000002.SZ,000003.SZ,000004.SZ,000005.SZ",
                  "--no-shuffle", "--alert-direct"])
    assert rc == 2 and alert and alert[0][0] == 2

    # 全成功 → rc=0 不告警
    monkeypatch.setattr(gc, "crawl", _mk("ok"))
    alert.clear()
    rc = gc.main(["000001.SZ,000002.SZ", "--no-shuffle", "--alert-direct"])
    assert rc == 0 and alert == []
    # 全空（empty）不算失败 → rc=0（页面可访问但无帖/无新增）
    monkeypatch.setattr(gc, "crawl", _mk("empty"))
    rc = gc.main(["000001.SZ,000002.SZ", "--no-shuffle", "--alert-direct"])
    assert rc == 0


def test_alert_fail_invokes_wrapper(monkeypatch):
    calls = []

    class _R:
        returncode = 0

    monkeypatch.setattr(gc.subprocess, "run",
                        lambda *a, **k: (calls.append(a) or _R()))
    assert gc._alert_fail(2, "x.err", enabled=True) is True
    argv = calls[0][0]
    assert argv[3] == gc.ALERT_WRAP and argv[4] == "guba_crawl" and argv[5] == "2"
    calls.clear()
    assert gc._alert_fail(2, "x.err", enabled=False) is False
    assert calls == []


def test_code6_and_empty_pool_rc0(tmp_path, monkeypatch):
    assert gc.code6("000338.SZ") == "000338"
    assert gc.code6("sz000338") == "000338"
    assert gc.code6("000338") == "000338"
    assert gc.code6("INVALID") == ""
    monkeypatch.setattr(gc, "OUT_DIR", str(tmp_path))
    monkeypatch.setattr(gc, "pool_codes", lambda *a, **k: [])
    assert gc.main(["--pool", "--no-alert"]) == 0  # 池空正常退出

def test_auto_batch_index_rotates_by_day():
    import datetime as dt

    assert gc._auto_batch_index(4, dt.date(2026, 1, 1)) == 1  # yday=1 %4
    assert gc._auto_batch_index(4, dt.date(2026, 1, 2)) == 2
    assert gc._auto_batch_index(4, dt.date(2026, 1, 3)) == 3
    assert gc._auto_batch_index(4, dt.date(2026, 1, 4)) == 0
    assert gc._auto_batch_index(0, dt.date(2026, 1, 1)) == 0


def test_pool_auto_rotation_picks_one_batch(monkeypatch):
    import types

    codes = ["%06d" % i for i in range(1000, 1060)]
    monkeypatch.setattr(gc, "pool_codes", lambda *a, **k: list(codes))
    args = types.SimpleNamespace(pool=True, pool_days=30, limit=60, batch_size=15,
                                 batch_index=None, no_shuffle=False, codes=None)
    picked, mode = gc._processed_codes(args)
    assert len(picked) == 15
    assert "auto-rotate" in mode
    # 显式 batch-index + no_shuffle：4 批 * 15 = 60，稳定分区不重复
    args.no_shuffle = True
    seen = set()
    for idx in range(4):
        args.batch_index = idx
        part, _ = gc._processed_codes(args)
        seen.update(part)
    assert seen == set(codes)
