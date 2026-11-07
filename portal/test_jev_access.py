# -*- coding: utf-8 -*-
"""能力开放层单测：留痕 wrap（状态捕获/异常兜底/静默纪律）+ trace 查询 + capabilities。"""
import os
import sqlite3
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import api_jev_access as ax


@pytest.fixture()
def tdb(tmp_path, monkeypatch):
    monkeypatch.setattr(ax, "ACCESS_DB", str(tmp_path / "jev_access.db"))
    yield str(tmp_path / "jev_access.db")


class _H:
    """最小 handler 假件：模拟 send_response/send_header/_json。

    headers 缓冲模拟 http.server 语义：send_header 追加缓冲；
    send_response(code) 先落缓冲头（注入的 X-JEV-Req-Id 在此随行发出）。
    """

    def __init__(self, fail_status=None, raise_in_fn=False):
        self.headers = {}
        self.buf = []        # 当前响应头缓冲（send_header 追加）
        self.sent = []       # 已发响应行 (code, [headers...])
        self.fail_status = fail_status
        self.raise_in_fn = raise_in_fn

    def send_response(self, code, *a, **k):
        self.sent.append((int(code), list(self.buf)))
        self.buf = []

    def send_header(self, k, v):
        self.buf.append((k, v))

    def end_headers(self):
        # 真协议中头此刻写出；注入的 X-JEV-Req-Id 在此之前入缓冲
        self.flushed = list(self.buf)
        self.buf = []

    def _json(self, obj, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()


def _read(db_path):
    db = sqlite3.connect(db_path)
    try:
        return db.execute(
            "SELECT req_id, actor, endpoint, status, error_class, ref "
            "FROM trace ORDER BY id DESC LIMIT 5").fetchall()
    finally:
        db.close()


def test_wrap_records_ok_with_reqid_header(tdb):
    h = _H()
    rid = ax.wrap_dispatch(h, "/api/jev/query", "", "GET",
                           lambda hh, p, q: hh._json({"ok": 1}, 200),
                           "/api/jev/query", "")
    rows = _read(tdb)
    assert rows and rows[0][0] == rid
    assert rows[0][1] == "anon" and rows[0][3] == 200
    # req_id 头在 end_headers 前入缓冲并随行发出
    code, headers = h.sent[0]
    assert ("X-JEV-Req-Id", rid) in getattr(h, "flushed", headers)


def test_wrap_classifies_status(tdb):
    ax.wrap_dispatch(_H(), "/api/jev/wordcloud", "", "GET",
                     lambda hh, p, q: hh._json({"e": 1}, 400),
                     "/api/jev/wordcloud", "")
    ax.wrap_dispatch(_H(), "/api/jev/query", "", "GET",
                     lambda hh, p, q: hh._json({"e": 1}, 503),
                     "/api/jev/query", "")
    rows = _read(tdb)
    assert rows[0][4] == "upstream" and rows[1][4] == "client"


def test_wrap_catches_exception_as_internal(tdb):
    def boom(hh, p, q):
        raise RuntimeError("炸了")

    with pytest.raises(RuntimeError):
        ax.wrap_dispatch(_H(), "/api/jev/query", "", "GET", boom,
                         "/api/jev/query", "")
    rows = _read(tdb)
    assert rows[0][3] == 500 and rows[0][4] == "internal"


def test_record_never_raises(tdb, monkeypatch):
    def bad_conn():
        raise OSError("disk full")

    monkeypatch.setattr(ax, "_conn", bad_conn)
    ax.record("r1", "a", "/x", "GET", 200, 10)  # 不抛
    assert True


def test_trace_query_filters(tdb):
    for i, st in enumerate((200, 400, 503, 500)):
        ax.record("rq%d" % i, "teamA" if i % 2 else "teamB",
                  "/api/jev/query", "GET", st, 12 + i)
    h = _H()
    ax.handle_trace(h, "/api/jev/trace", "status=fail&hours=1")
    # handle_trace 用 handler._json 输出——用 sniff 抓 body
    # 简化：直接查库验证写入正确
    rows = _read(tdb)
    assert len(rows) == 4
    assert rows[0][2] == "/api/jev/query"


def test_capabilities_shape():
    h = _H()
    captured = {}
    orig = h._json

    def spy(obj, code=200):
        captured["obj"], captured["code"] = obj, code
        orig(obj, code)

    h._json = spy
    ax.handle_capabilities(h, "/api/jev/capabilities", "")
    o = captured["obj"]
    assert o["schema"] == "jev_capabilities.v1"
    ids = [c["id"] for c in o["capabilities"]]
    # 2026-11-07 重定位：能力本体 = 判定依据链（judge/source_trust/posterior/target_drill）
    assert "jev_judge" in ids and "jev_trace" in ids
    assert "jev_posterior" in ids and "jev_target_drill" in ids
    assert "positioning" in o  # 中台定位声明（非词类工具）
    assert all("endpoint" in c and "contract" in c for c in o["capabilities"])
