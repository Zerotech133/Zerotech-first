# -*- coding: utf-8 -*-
"""P0-3 前端质量门：dw_v2 原型结构 + 可见英文=0 + 前后端对齐契约。

进 fast 门（tools/datawarehouse/tests 整目录已纳入）。
只读检查已生成产物，不在测试中重建，避免污染工作区。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
HTML = REPO / "portal" / "static" / "dw_v2.html"
DATA = REPO / "portal" / "static" / "dw_v2_data.json"
CHECKER = REPO / "tools" / "check_dw_prototype.py"


def _run_checker():
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(CHECKER)],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )


def test_checker_pass():
    r = _run_checker()
    assert r.returncode == 0, "checker rc=%s\nstdout=%s\nstderr=%s" % (
        r.returncode, r.stdout[-1200:], r.stderr[-800:])
    assert "PASS" in (r.stdout or ""), (r.stdout or "")[-800:]


def test_visible_english_zero():
    html = HTML.read_text(encoding="utf-8")
    visible = re.sub(r"<script.*?</script>", "", html, flags=re.S)
    visible = re.sub(r"<style.*?</style>", "", visible, flags=re.S)
    visible = re.sub(r"<[^>]+>", " ", visible)
    eng = sorted(set(re.findall(r"[A-Za-z][A-Za-z0-9_.\-]*", visible)))
    assert eng == [], "可见英文残留: %r" % (eng[:20],)


def test_views_and_alignment_contract():
    html = HTML.read_text(encoding="utf-8")
    for view in ("overview", "map", "query", "manual", "quality", "legion", "governance"):
        assert 'data-view="%s"' % view in html, "缺少视图 %s" % view
    # 前后端对齐：在线快照加载 + 内嵌兜底 + 查询真实 API + 手动入口明示
    assert "/static/dw_v2_data.json" in html
    assert "data-source-note" in html
    assert "/api/dw/query" in html
    assert "提交不会写入数据" in html


def test_data_contract():
    d = json.loads(DATA.read_text(encoding="utf-8"))
    for key in ("meta", "datasets", "kpis", "domains", "query_examples", "manual", "legion", "zhangsong", "hermes_work"):
        assert key in d, "dw_v2_data 缺少 %s" % key
    assert len(d["datasets"]) >= 200
    ds = d["datasets"][0]
    for key in ("id", "label_cn", "domain", "freq_cn", "qual_cn"):
        assert key in ds, "dataset 缺少 %s" % key
    assert isinstance(d["hermes_work"], dict) and "bindings" in d["hermes_work"]
