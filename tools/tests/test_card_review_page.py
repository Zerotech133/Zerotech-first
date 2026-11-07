# -*- coding: utf-8 -*-
"""学习卡审核页（wave21 CD）回归测试。

覆盖：
  1) 静态数据 `card_review_data.json` schema：60 条、字段齐全、统计与预审分布（AE20/AV40、预审 36/24/0）；
  2) 页面 `card_review.html`：挂载点/数据源 fetch/筛选控件/localStorage/导出文件名/单选四态/降级文案/审核页三件套（tokens.css+nav.js）；
  3) 导航集成：nav.js 含学习卡审核入口，unify_nav.py 有 EXTRA_NAV_LINKS 且 `--check` 通过（零漂移），route inventory `--check` 零 diff；
  4) 内联 JS `node --check` 语法通过；
  5) HTTP 200（若 8734 服务在线）：/card_review.html 与 /static/card_review_data.json。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))
# 本文件只打本机自启服务：回环请求禁代理（safe_urlopen 内含 127.0.0.1 豁免）
from safe_urlopen import safe_urlopen  # noqa: E402
PAGE = REPO / "portal" / "card_review.html"
DATA = REPO / "portal" / "static" / "card_review_data.json"
NAVJS = REPO / "portal" / "static" / "nav.js"
UNIFY = REPO / "tools" / "unify_nav.py"
BASE_URL = "http://127.0.0.1:8734"


def load_data() -> dict:
    return json.loads(DATA.read_text(encoding="utf-8-sig"))


# ---------------------------------------------------------------------------
# 1) 数据 schema
# ---------------------------------------------------------------------------
def test_data_json_schema():
    data = load_data()
    assert data["schema_version"] == 2
    assert data["date"] == "20261101"
    items = data["items"]
    assert len(items) == 100, "第一轮 60 + 第二轮 40"
    ids = [it["review_id"] for it in items]
    assert len(set(ids)) == 100
    assert ids[0].startswith("REV-20261101-")
    assert any(rid.startswith("CP3-20261101-") for rid in ids)
    for it in items:
        for key in ("review_id", "source", "theme", "question", "answer_summary", "answer",
                    "source_ref", "evidence_summary", "unit_id", "precheck", "checkboxes", "notes"):
            assert key in it, f"{it.get('review_id')} 缺 {key}"
        assert it["source"] in ("AE", "AV", "CP3")
        assert it["question"] and it["answer_summary"] and it["evidence_summary"] and it["source_ref"]
        assert it["checkboxes"] == {"pass": False, "modify": False, "reject": False}
        assert it.get("round") in ("first", "second")
        assert "precheck" in it
        if it["round"] == "first":
            assert (it["precheck"] or {}).get("suggested_verdict") in ("pass", "revise", "reject")
            assert it["review_id"].startswith("REV-")
        else:
            assert it["review_id"].startswith("CP3-")
            # wave27 CU：第二轮已补机器预审建议
            assert (it["precheck"] or {}).get("suggested_verdict") in ("pass", "revise", "reject")
    stats = data["stats"]
    assert stats["total"] == 100
    assert stats["by_round"] == {"first": 60, "second": 40}
    assert stats["by_source"] == {"AE": 20, "AV": 40, "CP3": 40}
    assert stats["precheck"] == {"pass": 53, "revise": 45, "reject": 2, "none": 0}
    opts = data["options"]
    assert [v["label"] for v in opts["verdicts"]] == ["通过", "需修改", "驳回", "未决"]
    assert opts["localStorage_key"] == "card_review_decisions_20261101"
    assert opts["export_filename"] == "card_review_decisions_20261101.json"
    # 源数据 sha 记录（可追溯）
    assert len(data["source"]["round1_sha256"]) == 64
    assert len(data["source"]["round2_sha256"]) == 64
    assert len(opts.get("rounds") or []) == 3


# ---------------------------------------------------------------------------
# 2) 页面挂载点/数据源/交互与降级
# ---------------------------------------------------------------------------
def test_page_mounts_data_source_and_controls():
    html = PAGE.read_text(encoding="utf-8")
    assert "/static/card_review_data.json" in html, "缺少数据源 fetch 路径"
    assert "fetch(" in html and "r.json()" in html
    for mount in ('id="list"', 'id="stats"', 'id="f-round"', 'id="f-source"', 'id="f-theme"',
                  'id="f-precheck"', 'id="f-status"', 'id="f-q"',
                  'id="btn-export"', 'id="btn-reset"', 'id="progress"'):
        assert mount in html, f"缺少挂载点 {mount}"
    # 三件套：tokens.css + nav.js
    assert "/static/tokens.css" in html
    assert "/static/nav.js" in html
    # localStorage 草稿
    assert "localStorage" in html and "card_review_decisions_" in html
    # 导出 decisions（与 card_review.py decide 兼容：{review_id: {verdict, notes}}）
    assert "buildDecisionsPayload" in html or "decisions" in html
    assert "review_id" in html and "verdict" in html and "notes" in html
    assert "card_review_decisions_20261101.json" in html or "export_filename" in html
    assert "JSON.stringify" in html and "Blob" in html
    # 四态单选
    for label in ("通过", "需修改", "驳回", "未决", "第二轮（40）"):
        assert label in html
    assert "100 候选" in html and "第二轮" in html
    # 筛选与统计/进度
    assert "renderStats" in html and "filtered" in html and "progress" in html
    # 降级文案
    assert "数据加载失败" in html and "请稍后刷新" in html
    # 页面不改数据声明
    assert "页面不改清单/学习卡数据" in html


# ---------------------------------------------------------------------------
# 3) 导航集成（nav.js 入口 + unify_nav 零漂移 + route inventory 零 diff）
# ---------------------------------------------------------------------------
def test_nav_integration():
    navjs = NAVJS.read_text(encoding="utf-8")
    assert "/card_review.html" in navjs, "nav.js 缺学习卡审核入口"
    assert "cardreview" in navjs
    # 导航源已正式加入 portal/api_common.py（不再依赖 unify_nav 临时补链）
    api = (REPO / "portal" / "api_common.py").read_text(encoding="utf-8")
    assert "cardreview" in api and "/card_review.html" in api

    def run(*args):
        return subprocess.run([sys.executable, "-X", "utf8", *args], capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=180, cwd=str(REPO))

    proc = run("tools/unify_nav.py", "--check")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "守卫 PASS" in proc.stdout
    proc2 = run("tools/portal_route_inventory.py", "--check")
    assert proc2.returncode == 0, proc2.stdout + proc2.stderr
    assert "OK" in proc2.stdout or "OK" in proc2.stderr


# ---------------------------------------------------------------------------
# 4) 内联 JS 语法（node --check）
# ---------------------------------------------------------------------------
def test_inline_js_syntax(tmp_path):
    html = PAGE.read_text(encoding="utf-8")
    scripts = re.findall(r"<script(?![^>]*src=)[^>]*>(.*?)</script>", html, re.S)
    assert scripts, "页面应有内联 JS"
    js = "\n".join(scripts)
    p = tmp_path / "card_review_inline.js"
    p.write_text(js, encoding="utf-8")
    proc = subprocess.run(["node", "--check", str(p)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=60)
    assert proc.returncode == 0, proc.stderr


# ---------------------------------------------------------------------------
# 5) HTTP 200（服务在线时）
# ---------------------------------------------------------------------------
def test_http_served():
    try:
        r = safe_urlopen(f"{BASE_URL}/card_review.html", timeout=5)
        assert r.status == 200
        body = r.read().decode("utf-8", "replace")
        assert "学习卡审核" in body
        r2 = safe_urlopen(f"{BASE_URL}/static/card_review_data.json", timeout=5)
        assert r2.status == 200
        payload = json.loads(r2.read().decode("utf-8"))
        assert len(payload["items"]) == 100
        assert payload["stats"]["by_round"] == {"first": 60, "second": 40}
    except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:  # noqa: F821
        pytest.skip(f"8734 门户服务不在线: {exc}")

# ---------------------------------------------------------------------------
# 6) 一键预填机器预审建议（wave22 CG）
# ---------------------------------------------------------------------------
def test_adopt_precheck_controls_and_logic():
    html = PAGE.read_text(encoding="utf-8")
    # 按钮与统计挂载点
    for mount in ('id="btn-adopt"', 'id="btn-reset"', 'id="adopt-stats"'):
        assert mount in html, mount
    for text in ("一键采用机器预审建议", "一键清空", "仅预填未决", "与预审建议一致"):
        assert text in html, text
    # JS：预填函数、建议映射、仅未决不覆盖、本地保存、预填统计
    for fn in ("function adoptPrecheck", "function mappedVerdict", "function consistency",
               "function updateAdoptStats"):
        assert fn in html, fn
    assert "suggested_verdict" in html
    assert "verdictOf(it.review_id)==='pending'" in html, "仅预填未决卡"
    assert "已决不覆盖" in html or "已决卡不覆盖" in html
    assert "localStorage.setItem" in html
    assert "已选" in html and "与预审建议一致" in html
    assert "第二轮" in html and "无预审建议" in html and "保持未决" in html
    assert "f-round" in html
    # 导出 schema 保持不变（仍用 buildDecisionsPayload + {review_id:{verdict,notes}}）
    assert "buildDecisionsPayload" in html and "JSON.stringify" in html
    assert "'pass':'pass'" in html or "pass'?'pass'" in html
    # 一键清空带确认、不清真实数据
    assert "一键清空本地审核草稿" in html
