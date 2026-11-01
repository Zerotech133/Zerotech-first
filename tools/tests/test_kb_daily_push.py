# -*- coding: utf-8 -*-
"""知识库每日推送准备（kb_daily_push）回归测试。

覆盖：
  1) payload schema（date/dry_run/network=none/disclaimer/sources/summary/feishu_payload/card_text/meta）；
  2) 飞书卡片字段（msg_type/schema 2.0/header title+subtitle/body elements/学习卡5张/研报候选/门户链接）；
  3) 缺数据容错（digest/panel/briefing 缺失或损坏仍产出合法 payload，进入 fallbacks）；
  4) dry-run 无网络（进程内 monkeypatch socket 后仍可组装；源码无网络库 import；CLI network_calls=0）；
  5) check 模式（合法 0 / 篡改 1）。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "tools" / "kb_corpus" / "kb_daily_push.py"

_spec = importlib.util.spec_from_file_location("kb_daily_push_tool", SCRIPT)
push = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(push)  # type: ignore[union-attr]


def synthetic_digest() -> dict:
    return {
        "date": "20261101",
        "cards": [
            {"rank": i, "q": f"什么是「主题{i}」？", "a": f"主题{i}的答案与数据支撑。", "source": f"《某研报》 p{i}-{i + 2}",
             "quality_score": 90 - i}
            for i in range(1, 6)
        ],
        "briefing": {
            "date": "20261101",
            "headline": "研报简报 20261101：3 篇研报 · 9 条知识单元",
            "highlights": [{"concept": "AI 原生安全", "claim": "围绕 AI 系统重新设计安全体系。"}],
        },
        "strategy_notes": {"title": "选股策略研究结论", "takeaway": "策略线暂停新因子投入；", "points": ["p1"]},
    }


def synthetic_panel() -> dict:
    return {
        "date_tag": "20261101",
        "summary": {"reports": 3, "candidates": 1, "rejected": 2, "page_invalid_facts": 1},
        "candidates": [
            {"report_id": "rp_1", "title": "产业并购重组快速推进", "date": "2026-10-07", "pages": 21,
             "pass_models": ["qwen3.5:9b"], "role_agreement_rate": 1.0}
        ],
        "disclaimer": "仅供研究，不构成投资建议",
    }


# ---------------------------------------------------------------------------
# 1) payload schema
# ---------------------------------------------------------------------------
def test_build_payload_schema():
    payload = push.build_payload(synthetic_digest(), synthetic_panel(), date="20261101")
    assert push.validate_payload(payload) == [], push.validate_payload(payload)
    assert payload["date"] == "20261101"
    assert payload["dry_run"] is True
    assert payload["network"] == "none"
    assert "不构成投资建议" in payload["disclaimer"]
    assert payload["summary"]["cards_count"] == 5
    assert payload["summary"]["candidates_count"] == 1
    urls = [link["url"] for link in payload["summary"]["portal_links"]]
    assert any("/kb_reports.html" in u for u in urls)
    assert any("/selection.html" in u for u in urls)
    assert payload["meta"]["network_calls"] == 0
    assert payload["meta"]["generator"] == push.GENERATOR
    # sources 三路快照
    assert set(payload["sources"]) == {"digest", "panel", "briefing"}
    for snap in payload["sources"].values():
        assert {"path", "exists", "mtime", "size"} <= set(snap)


# ---------------------------------------------------------------------------
# 2) 飞书卡片字段
# ---------------------------------------------------------------------------
def test_feishu_card_fields():
    payload = push.build_payload(synthetic_digest(), synthetic_panel(), date="20261101")
    feishu = payload["feishu_payload"]
    assert feishu["msg_type"] == "interactive"
    card = feishu["card"]
    assert card["schema"] == "2.0"
    assert card["config"]["summary"]["content"]
    header = card["header"]
    assert header["title"]["tag"] == "plain_text"
    assert "知识库" in header["title"]["content"] and "2026-11-01" in header["title"]["content"]
    assert "DRY-RUN" in header["subtitle"]["content"]
    assert header["template"] in ("blue", "green", "orange", "red", "grey", "wathet", "turquoise", "carmine")

    elements = card["body"]["elements"]
    assert elements and all("tag" in el for el in elements)
    contents = "\n".join(el.get("content", "") for el in elements if el.get("tag") == "markdown")
    assert "学习卡" in contents
    for i in range(1, 6):
        assert f"主题{i}" in contents, "学习卡 5 张都应进入卡片"
    assert "研报候选" in contents and "产业并购重组快速推进" in contents
    assert "/kb_reports.html" in contents and "/selection.html" in contents
    assert "不构成投资建议" in contents

    text = payload["card_text"]
    assert "知识库日报" in text and "学习卡" in text and "不构成投资建议" in text


# ---------------------------------------------------------------------------
# 3) 缺数据容错
# ---------------------------------------------------------------------------
def test_missing_or_corrupt_inputs_tolerated(tmp_path):
    missing = tmp_path / "nope.json"
    payload = push.build(write=False, digest_path=missing, panel_path=missing, briefing_path=missing,
                         date="20261101")
    assert push.validate_payload(payload) == [], push.validate_payload(payload)
    assert payload["summary"]["cards_count"] == 0
    assert payload["summary"]["candidates_count"] == 0
    assert payload["meta"]["inputs_ok"] is False
    assert payload["meta"]["fallbacks"], "缺失输入必须记录降级原因"
    # 卡片仍可组装（降级文案）
    contents = "\n".join(el.get("content", "") for el in payload["feishu_payload"]["card"]["body"]["elements"])
    assert "暂无" in contents

    bad1 = tmp_path / "bad_digest.json"
    bad1.write_text("{not-json", encoding="utf-8")
    bad2 = tmp_path / "bad_panel.json"
    bad2.write_text('{"candidates": "not-a-list"}', encoding="utf-8")
    payload2 = push.build(write=False, digest_path=bad1, panel_path=bad2, briefing_path=missing,
                          date="20261101")
    assert push.validate_payload(payload2) == []
    assert payload2["summary"]["candidates_count"] == 0
    assert payload2["meta"]["inputs_ok"] is False


# ---------------------------------------------------------------------------
# 4) dry-run 无网络
# ---------------------------------------------------------------------------
def test_dry_run_no_network(monkeypatch):
    calls = {"n": 0}

    def boom(*args, **kwargs):  # noqa: ANN001
        calls["n"] += 1
        raise AssertionError("dry-run 模式不应发起任何网络请求")

    monkeypatch.setattr("socket.socket", boom)
    monkeypatch.setattr("socket.create_connection", boom)
    monkeypatch.setattr("socket.getaddrinfo", boom)

    payload = push.build(write=False, date="20261101")
    assert payload["meta"]["network_calls"] == 0
    assert payload["dry_run"] is True
    assert calls["n"] == 0
    assert push.validate_payload(payload) == []

    # 源码层面也不允许出现网络库 import
    source = SCRIPT.read_text(encoding="utf-8")
    for forbidden in ("import urllib", "import requests", "import http.client", "import socket"):
        assert forbidden not in source, f"推送准备脚本不应引入网络依赖: {forbidden}"

    # CLI build（--no-write）：退出 0 且 JSON 明确 network_calls=0 / dry_run=true
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(SCRIPT), "build", "--date", "20261101", "--no-write", "--json"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, cwd=str(REPO),
    )
    assert proc.returncode == 0, proc.stderr
    cli_payload = json.loads(proc.stdout)
    assert cli_payload["dry_run"] is True
    assert cli_payload["meta"]["network_calls"] == 0
    assert cli_payload["meta"]["generator"] == push.GENERATOR


# ---------------------------------------------------------------------------
# 5) check 模式：合法 0 / 篡改 1
# ---------------------------------------------------------------------------
def test_check_payload_file(tmp_path):
    out_dir = tmp_path / "reports"
    portal_out = tmp_path / "static" / "kb_daily_push.json"
    payload = push.build(date="20261101", out_dir=out_dir, portal_out=portal_out)
    assert Path(payload["meta"]["out"]).is_file()
    assert portal_out.is_file()

    report = push.check_payload_file(portal_out)
    assert report["ok"] is True, report["errors"]

    # 篡改：dry_run=false → 校验失败
    tampered = json.loads(portal_out.read_text(encoding="utf-8"))
    tampered["dry_run"] = False
    bad_path = tmp_path / "bad.json"
    bad_path.write_text(json.dumps(tampered, ensure_ascii=False), encoding="utf-8")
    report2 = push.check_payload_file(bad_path)
    assert report2["ok"] is False
    assert any("dry_run" in e for e in report2["errors"])

    # CLI check 退出码
    def cli(*args):
        return subprocess.run(
            [sys.executable, "-X", "utf8", str(SCRIPT), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        )

    assert cli("check", "--portal-out", str(portal_out)).returncode == 0
    assert cli("check", "--portal-out", str(bad_path)).returncode == 1
