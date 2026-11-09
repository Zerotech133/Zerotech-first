# -*- coding: utf-8 -*-
"""api_jev_evidence_item 单测（四级溯源）+ interpret 云端降级链单测。

跑法：cd portal && python -m pytest test_jev_evidence_item.py -q
"""
from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import api_jev_evidence_item as ei  # noqa: E402


# ── 数据面探测：挑一条真有四环样本（有 dec_audit 后验的公告） ──
def _pick_sample() -> str | None:
    import sqlite3
    try:
        adb = sqlite3.connect(ei.AUDIT_DB, timeout=5)
        row = adb.execute(
            "SELECT news_id FROM dec_audit WHERE ret IS NOT NULL "
            "AND news_id LIKE 'ann:%' ORDER BY rowid DESC LIMIT 1").fetchone()
        adb.close()
        if row and row[0]:
            return str(row[0])
    except Exception:  # noqa: BLE001
        pass
    return None


SAMPLE = _pick_sample()


def test_trace_full_chain():
    """四环齐全：原文/判定/后验/归组都能查到，且给引用串。"""
    if not SAMPLE:
        pytest.skip("后验库无可用样本")
    r = ei.trace(SAMPLE)
    assert r["schema"] == "jev_evidence_item.v1"
    assert r["complete"] >= 3, r["complete_txt"]
    rings = {x["ring"][1:]: x for x in r["rings"]}
    assert set(rings) == {"原文", "判定", "后验", "归组"}
    assert rings["原文"]["data"]["id"] == SAMPLE
    assert rings["判定"]["data"]["bias"] in ("bull", "bear", "neutral")
    assert rings["后验"]["data"]["caliber"] in (
        "dec_audit T+1 官方后验", "行情次日涨跌兜底（非 dec_audit 官方 T+1）")
    assert SAMPLE in r["permalink"]
    # 引用串给中文渠道名（老田令：渠道说中文），id 仍保留在原文环 data 里对日志
    assert r["citation"].startswith(rings["原文"]["data"]["source_cn"])
    assert rings["原文"]["data"]["source_cn"] != ""
    # 后验那一环的 ret 是数字（证明真取到行情，不是空壳）
    row0 = rings["后验"]["data"]["rows"][0]
    assert isinstance(row0["ret"], (int, float))


def test_trace_rejects_bad_id():
    for bad in ("", "  ", "a b", "x" * 100):
        with pytest.raises(ValueError):
            ei.trace(bad)


def test_trace_missing_id_raises_keyerror():
    with pytest.raises(KeyError):
        ei.trace("no_such_source:999999999")


def test_trace_code_hint_used_for_posterior():
    """显式 code 参与后验：给了 6 位码就有标的，不给则按判定 companies。"""
    if not SAMPLE:
        pytest.skip("后验库无可用样本")
    r = ei.trace(SAMPLE, "002923")
    ring = [x for x in r["rings"] if x["ring"].endswith("后验")][0]
    assert ring["data"]["code"] == "002923"


class _MockOpenAI(BaseHTTPRequestHandler):
    ok = True

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("content-length") or 0)
        self.rfile.read(n)
        if not self.ok:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"boom")
            return
        body = json.dumps({"choices": [{"message": {"content":
                "【形态】震荡偏弱，累计 -3.2%。\n【量价】放量下跌，量价背离。\n"
                "【消息与验证】利多 40 / 利空 10，证实率 38%。\n"
                "【结论】利好出尽，规避追高。"}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # noqa: A003
        pass


@pytest.fixture()
def mock_cloud():
    srv = HTTPServer(("127.0.0.1", 0), _MockOpenAI)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d/v1" % port
    srv.shutdown()


def _interpret_mod():
    import api_jev_interpret
    return api_jev_interpret


def test_cloud_fallback_when_local_fails(mock_cloud, monkeypatch):
    """本地算力不足 → 云端接手，engine=cloud 且标注模型来源。"""
    it = _interpret_mod()
    monkeypatch.setenv("JEV_CLOUD_LLM_URL", mock_cloud)
    monkeypatch.setenv("JEV_CLOUD_LLM_KEY", "test-key")
    monkeypatch.setenv("JEV_CLOUD_LLM_MODEL", "mock-model")
    monkeypatch.setattr(it, "_ollama_conclude",
                        lambda facts: (_ for _ in ()).throw(
                            RuntimeError("本地算力不足")))
    it._RESULT_CACHE.clear()
    r = it.interpret("evidence", q="回购", days=7)
    assert r["engine"] == "cloud"
    assert r["engine_label"] == "云端模型"
    assert "mock-model" in (r["model"] or "")
    assert "已切云端" in (r["degrade_note"] or "")
    assert "【结论】" in r["conclusion"]


def test_rule_when_cloud_also_fails(mock_cloud, monkeypatch):
    """云端也挂 → 规则兜底，结论仍可读且标注原因。"""
    it = _interpret_mod()
    monkeypatch.setenv("JEV_CLOUD_LLM_URL", "http://127.0.0.1:9/v1")  # 必失败
    monkeypatch.setenv("JEV_CLOUD_LLM_KEY", "k")
    monkeypatch.setenv("JEV_CLOUD_LLM_MODEL", "m")
    monkeypatch.setenv("JEV_CLOUD_LLM_TIMEOUT", "2")
    monkeypatch.setattr(it, "_ollama_conclude",
                        lambda facts: (_ for _ in ()).throw(
                            RuntimeError("本地超时")))
    it._RESULT_CACHE.clear()
    r = it.interpret("evidence", q="回购", days=7)
    assert r["engine"] == "rule"
    assert r["engine_label"] == "规则引擎"
    assert "云端" in (r["degrade_note"] or "")
    assert "【" in r["conclusion"] and len(r["conclusion"]) > 20


def test_cloud_config_from_env_shape(monkeypatch):
    it = _interpret_mod()
    monkeypatch.setenv("JEV_CLOUD_LLM_URL", "https://api.x.com/v1/")
    monkeypatch.setenv("JEV_CLOUD_LLM_KEY", "k")
    monkeypatch.setenv("JEV_CLOUD_LLM_MODEL", "m1")
    monkeypatch.delenv("JEV_CLOUD_LLM_KIND", raising=False)
    p = it._cloud_providers()
    assert len(p) == 1 and p[0]["kind"] == "openai"
    assert p[0]["base_url"] == "https://api.x.com/v1"
