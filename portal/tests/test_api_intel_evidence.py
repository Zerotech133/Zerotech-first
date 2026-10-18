import json
import runpy
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import api_intel_evidence as evidence


def test_default_database_path_uses_registered_junction(monkeypatch, tmp_path):
    monkeypatch.delenv("AIOS_ROOT", raising=False)
    module = runpy.run_path(str(Path(evidence.__file__)))
    assert module["_DB"] == Path("C:/aios/news_collector/news.db")
    assert ".worktrees" not in str(module["_DB"])

    monkeypatch.setenv("AIOS_ROOT", str(tmp_path))
    module = runpy.run_path(str(Path(evidence.__file__)))
    assert module["_DB"] == tmp_path / "news_collector" / "news.db"


def _db(path):
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE news (id TEXT, source TEXT, ts TEXT, created_at TEXT, title TEXT, content TEXT, stocks TEXT, jev TEXT)")


def _add(path, nid, stocks, jev=None, days=0):
    ts = (datetime.now(timezone(timedelta(hours=8))) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M")
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO news VALUES (?,?,?,?,?,?,?,?)",
                   (nid, "xd_news", ts, ts, "标题", "原文", stocks, jev))


def test_exact_match_list_and_counts(tmp_path):
    path = tmp_path / "news.db"
    _db(path)
    _add(path, "xd:news:1", '["600000.SH", "000001.SZ", "平安银行", "600000.SH"]',
         json.dumps({"bias": "bull", "conf": .8}))
    _add(path, "xd:news:2", '["600000.SHISH", "600000", "600000.sh", "名称600000.SH"]')
    _add(path, "xd:news:3", '["600000.SH"]', days=10)
    _add(path, "xd:news:4", '["688001.SH", "430001.BJ"]')
    _add(path, "xd:news:5", 'not json')
    _add(path, "xd:news:6", '["600000.SH"]', '{bad')
    body, status = evidence.load_evidence_targets("code=600000.SH&limit=1", path)
    assert status == 200
    assert body["count"] == 2 and len(body["items"]) == 1
    assert body["excluded_count"] == 2 and body["bad_rows"] == 2
    item = body["items"][0]
    assert item["evidence_id"] == item["source_ref"] == "xd:news:6"
    assert item["match_basis"] == "news.stocks:exact_code" and item["jev"] is None
    assert item["source"] == "xd_news" and item["title"] == "标题" and item["content"] == "原文"
    all_body, status = evidence.load_evidence_targets("limit=10", path)
    assert status == 200 and all_body["count"] == 5 and len(all_body["items"]) == 5
    assert {it["code"] for it in all_body["items"]} == {"600000.SH", "000001.SZ", "688001.SH", "430001.BJ"}
    assert {it["evidence_id"] for it in all_body["items"]} == {"xd:news:1", "xd:news:4", "xd:news:6"}
    assert next(it for it in all_body["items"] if it["evidence_id"] == "xd:news:1")["jev"] == {"bias": "bull", "conf": .8, "kind": "model_judgement"}


def test_empty_missing_and_broken_database(tmp_path):
    path = tmp_path / "news.db"
    body, status = evidence.load_evidence_targets("", path)
    assert status == 503 and body["error"]["code"] == "database_missing" and not path.exists()
    _db(path)
    body, status = evidence.load_evidence_targets("", path)
    assert status == 200 and body == {"endpoint": evidence._ENDPOINT, "items": [], "count": 0, "excluded_count": 0, "bad_rows": 0}
    path.write_text("not sqlite", encoding="utf-8")
    body, status = evidence.load_evidence_targets("", path)
    assert status == 503 and body["error"]["code"] == "database_error"


def test_query_whitelist_and_bounds(tmp_path):
    path = tmp_path / "news.db"
    for qs in ("code=600000", "code=000001.SH", "code=600000.SZ", "code=300001.SH",
               "code=123456.BJ", "code=600000.sh", "code=600000.SH%20", "code=",
               "code=600000.SH&code=000001.SZ", "days=0", "days=31", "days=-1",
               "days=abc", "limit=0", "limit=101", "limit=1.0", "extra=x", "days=7&days=8"):
        body, status = evidence.load_evidence_targets(qs, path)
        assert status == 400 and body["error"]["code"] == "invalid_query", qs
        assert not path.exists()
    for qs in ("code=688001.SH", "code=300001.SZ", "code=830001.BJ", "days=30&limit=100"):
        body, status = evidence.load_evidence_targets(qs, path)
        assert status == 503 and body["error"]["code"] == "database_missing", qs


def test_bad_stocks_and_jev_are_visible(tmp_path):
    path = tmp_path / "news.db"
    _db(path)
    _add(path, "names", '["平安银行"]')
    _add(path, "object", '{"code":"600000.SH"}')
    _add(path, "wrong_conf", '["600000.SH", "000001.SZ"]', '{"bias":"bull","conf":1.5}')
    _add(path, "wrong_bias", '["600000.SH"]', '{"bias":"unknown","conf":0.8}')
    _add(path, "bad_json", '["600000.SH"]', '{bad')
    _add(path, "good", '["600000.SH"]', json.dumps({"bias": "bear", "conf": .5}))
    body, status = evidence.load_evidence_targets("code=600000.SH", path)
    assert status == 200 and body["count"] == 4 and len(body["items"]) == 4
    assert body["excluded_count"] == 1 and body["bad_rows"] == 4
    assert {item["evidence_id"] for item in body["items"]} == {"wrong_conf", "wrong_bias", "bad_json", "good"}
    assert all(item["jev"] is None for item in body["items"] if item["evidence_id"] != "good")
    assert next(item for item in body["items"] if item["evidence_id"] == "good")["jev"] == {"bias": "bear", "conf": .5, "kind": "model_judgement"}
    all_body, status = evidence.load_evidence_targets("", path)
    assert status == 200 and all_body["count"] == 5 and len(all_body["items"]) == 5
    assert all_body["bad_rows"] == 4
    assert {(item["evidence_id"], item["code"]) for item in all_body["items"] if item["evidence_id"] == "wrong_conf"} == {("wrong_conf", "600000.SH"), ("wrong_conf", "000001.SZ")}


def test_handler_contract(monkeypatch):
    monkeypatch.setattr(evidence, "load_evidence_targets", lambda qs: ({"endpoint": evidence._ENDPOINT}, 200))

    class Stub(evidence.Handler):
        def _json(self, payload, status):
            self.payload, self.status = payload, status

    handler = Stub()
    handler._intel_evidence_targets("code=600000.SH")
    assert handler.status == 200 and handler.payload["endpoint"] == evidence._ENDPOINT
