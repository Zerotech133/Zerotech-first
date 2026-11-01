# -*- coding: utf-8 -*-
"""kb_reports.html 问答示例（静态）区块契约测试 —— 只读页面/静态 JSON，不改数据、不调服务。

覆盖：
  1. 新区块挂载点（容器/标题/qa-body/qa-meta/qa-note）与 fetch 路径；
  2. 既有区块共存（检索 v2 / 学习卡 / 简报 / H18 / 搜索框 / results）；
  3. 引用字段渲染（c.unit_id + c.page）与置信标注、fallback 标记；
  4. 降级文案（fetch 失败 / 无数据 / HTTP 待审批说明）；
  5. 静态 JSON（kb_qa_samples.json）字段契约（只读校验，不修改）；
  6. 内联 JS 语法 node --check。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PAGE = REPO / "portal" / "kb_reports.html"
SAMPLES = REPO / "portal" / "static" / "kb_qa_samples.json"


def _html() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_mount_points_and_fetch_path():
    html = _html()
    for anchor in ('id="qa-sample-panel"', "💬 问答示例（静态）",
                   'id="qa-body"', 'id="qa-meta"', 'id="qa-note"'):
        assert anchor in html, anchor
    # 只从新 JSON 取数，且为静态文件路径
    assert "fetch('/static/kb_qa_samples.json?ts='+Date.now())" in html
    # 区块只出现一次（新增而非复制）
    assert html.count('id="qa-sample-panel"') == 1
    assert html.count("/static/kb_qa_samples.json") >= 1


def test_existing_blocks_preserved():
    html = _html()
    # 检索 v2 / 学习卡 / 简报 / H18 / 搜索结果容器 / 搜索输入与引擎切换均保留
    for anchor in ('id="q"', 'id="engine"', 'value="v2"', 'id="learning-cards"',
                   'id="daily-briefing"', 'id="results"', 'id="h18-body"',
                   "📡 H18 业绩预告修订观察", "📚 学习卡", "📰 今日简报",
                   "检索引擎切换", "/static/kb_units_all.json"):
        assert anchor in html, anchor
    # 原页面结构：nav 容器与页脚仍在
    assert 'id="nav"' in html and "/static/nav.js" in html and "_ingest*" in html


def test_citation_and_confidence_rendering_fields():
    html = _html()
    # 引用必须渲染 unit_id + 页码
    assert "c.unit_id" in html and "c.page" in html
    assert "引用：" in html
    # 置信标注与 fallback / 演示标记
    for label in ("高置信", "中置信", "低置信", "拒答/无依据", "置信说明"):
        assert label in html, label
    assert "规则降级" in html and "演示" in html
    # 答案摘要截断（≤180 字）
    assert "slice(0,180)" in html


def test_degradation_texts_and_http_note():
    html = _html()
    # fetch 失败 & 无数据两条降级路径
    assert "问答示例暂不可用" in html
    assert "暂无问答示例数据" in html
    assert "加载失败（降级）" in html and "无数据（降级）" in html
    # 必须注明 HTTP 接口待审批、当前为静态示例
    assert "HTTP 问答接口待 8091 审批，当前为静态示例" in html
    # 只允许该区块读 kb_qa_samples，不得出现写操作
    assert "POST" not in html.split("qa-sample-panel")[1][:6000]


def test_static_samples_contract_readonly():
    data = json.loads(SAMPLES.read_text(encoding="utf-8"))
    assert data.get("kind") == "kb_qa_samples"
    assert data.get("disclaimer") and "不构成投资建议" in data["disclaimer"]
    samples = data.get("samples") or []
    assert len(samples) == 5
    for s in samples:
        assert s.get("question") and "answer" in s
        assert (s.get("confidence") or {}).get("level") in ("high", "medium", "low", "none", "n/a")
        for c in s.get("citations") or []:
            assert c.get("unit_id") and c.get("text") is not None
            assert "page" in c
    # 覆盖无依据/失败演示两类
    categories = [s.get("category") for s in samples]
    assert any("no_evidence" in str(c) for c in categories)
    assert any("failure" in str(c) for c in categories)


def test_inline_js_syntax_node_check(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用，跳过内联 JS 语法检查")
    html = _html()
    scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
    assert scripts, "页面应含内联 JS"
    qa_script = next((s for s in scripts if "kb_qa_samples" in s), None)
    assert qa_script, "应存在问答示例内联脚本"
    js_file = tmp_path / "qa_inline.js"
    js_file.write_text(qa_script, encoding="utf-8")
    proc = subprocess.run([node, "--check", str(js_file)], capture_output=True, text=True,
                          timeout=60, encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stderr
