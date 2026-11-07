# -*- coding: utf-8 -*-
"""tools/tests/test_kb_search_frontend.py — 研报检索 v2 前端灰度（portal/kb_reports.html）。

覆盖（任务验收 ≥4，全部只读页面/静态检查，不依赖浏览器）：
  1. v2 挂载点：页面内联 JS 含 SEARCH_V2_WEIGHTS / SYNONYMS_V2 / v2Parse / v2UnitScore /
     v2GroupResults / renderV2 等新版函数；
  2. 权重一致性：页面内 SEARCH_V2_WEIGHTS 与 tools/kb_corpus/search_v2.py 的 DEFAULT_WEIGHTS 一致；
  3. 切换控件：`#engine` 下拉默认 v2、含 v1 回退项、onchange 绑定；render() 按 ENGINE 分发；
  4. 同义词：内联 SYNONYMS_V2 与 tools/kb_corpus/synonyms_v2.json 完全一致（≥20 组）；
  5. 降级/保留：旧逻辑 tok/score 与学习卡/简报/H18 区块仍在；v2 异常自动回退 v1 文案；
  6. 语法校验：inline `<script>` 用 `node --check` 通过（node 缺失则 skip）；
  7. HTTP 200：门户服务在线时 GET /kb_reports.html 返回 200 且含 v2 标记（离线则 skip）。

运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_kb_search_frontend.py
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
# 本文件只打本机自启服务：回环请求禁代理（safe_urlopen 内含 127.0.0.1 豁免）
from safe_urlopen import safe_urlopen  # noqa: E402
PAGE = ROOT / "portal" / "kb_reports.html"
SYN_JSON = ROOT / "tools" / "kb_corpus" / "synonyms_v2.json"


def _html() -> str:
    return PAGE.read_text(encoding="utf-8")


def _inline_scripts() -> list[str]:
    return re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", _html(), re.S | re.I)


def _js() -> str:
    return "\n;\n".join(_inline_scripts())


def _inline_synonym_groups() -> list[list[str]]:
    md = _html()
    m = re.search(r"/\* === SEARCH_V2_SYNONYMS_BEGIN.*?\*/(.*?)/\* === SEARCH_V2_SYNONYMS_END === \*/",
                  md, re.S)
    assert m, "缺少 SEARCH_V2_SYNONYMS_BEGIN/END 标记块"
    arr = re.search(r"const\s+SYNONYMS_V2\s*=\s*(\[.*?\]);", m.group(1), re.S)
    assert arr, "标记块内缺少 const SYNONYMS_V2 = [...]"
    return json.loads(arr.group(1))


# ------------------------------------------------------------------ 1. v2 挂载点
def test_page_has_v2_mount_points():
    page, js = _html(), _js()
    for token in ("SEARCH_V2_WEIGHTS", "SYNONYMS_V2", "v2Parse", "v2UnitScore",
                  "v2SearchUnits", "v2GroupResults", "renderV2", "V2_FIELDS"):
        assert token in js, f"页面 JS 缺少 v2 挂载点: {token}"
    # 页面元素与数据源保持
    assert 'id="q"' in page and 'id="results"' in page
    assert "/static/kb_units_all.json" in js
    # v2 检索入口会被 render() 调用
    assert re.search(r"renderV2\(LASTQ\.trim\(\)\)", js)


# ------------------------------------------------------------------ 2. 权重一致
def test_weights_match_python_v2():
    sys.path.insert(0, str(ROOT / "tools" / "kb_corpus"))
    import search_v2 as sv  # noqa: E402
    m = re.search(r"const\s+SEARCH_V2_WEIGHTS\s*=\s*(\{[^}]+\})", _js())
    assert m, "页面缺少 SEARCH_V2_WEIGHTS 常量"
    js_weights = json.loads(re.sub(r"(\w+)\s*:", r'"\1":', m.group(1)))
    assert js_weights == sv.DEFAULT_WEIGHTS, (
        f"前端权重 {js_weights} 与 Python v2 默认权重 {sv.DEFAULT_WEIGHTS} 不一致")
    assert set(js_weights) == {"concept", "claim", "evidence", "chapter", "book"}


# ------------------------------------------------------------------ 3. v1/v2 切换
def test_engine_switch_default_v2():
    page, js = _html(), _js()
    assert 'id="engine"' in page, "缺少检索引擎切换控件"
    sel = re.search(r'<select[^>]*id="engine".*?</select>', page, re.S)
    assert sel, "未找到 #engine select"
    block = sel.group(0)
    assert 'value="v2"' in block and 'value="v1"' in block
    assert re.search(r'value="v2"\s+selected', block), "默认引擎不是 v2"
    # 初始 JS 状态 + 切换绑定 + 分发
    assert re.search(r"ENGINE\s*=\s*'v2'", js)
    assert re.search(r"\$\('engine'\)\.onchange", js)
    assert re.search(r"ENGINE\s*===\s*'v2'", js)
    assert "function renderV1()" in js


# ------------------------------------------------------------------ 3b. 软 AND 同步
def test_soft_and_sync_defaults():
    """wave13：前端 v2 与后端一致——默认 soft（覆盖率≥0.5）+ 同义词权重 0.6 + strict 回退。"""
    page, js = _html(), _js()
    sys.path.insert(0, str(ROOT / "tools" / "kb_corpus"))
    import search_v2 as sv  # noqa: E402
    # 1) JS 常量与 Python 默认一致（前后端同源）
    assert re.search(r"const\s+SEARCH_V2_DEFAULT_AND_MODE\s*=\s*'soft'", js)
    assert sv.DEFAULT_AND_MODE == "soft"
    m_cov = re.search(r"const\s+SEARCH_V2_SOFT_COV\s*=\s*([0-9.]+)", js)
    m_syn = re.search(r"const\s+SEARCH_V2_SYNONYM_WEIGHT\s*=\s*([0-9.]+)", js)
    assert m_cov and m_syn, "缺少前端软 AND/同义词权重常量"
    assert float(m_cov.group(1)) == sv.DEFAULT_SOFT_COV == 0.5
    assert float(m_syn.group(1)) == sv.DEFAULT_SYNONYM_WEIGHT == 0.6
    # 2) 软 AND 逻辑：覆盖率阈值判定 + strict/any 分支 + 同义词命中乘权重
    assert "coverage>=SEARCH_V2_SOFT_COV" in js
    assert "AND_MODE==='strict'" in js and "AND_MODE==='any'" in js
    assert "SEARCH_V2_SYNONYM_WEIGHT" in js
    # 3) 初始状态默认 soft + 控件渲染 + 切换绑定
    assert re.search(r"AND_MODE\s*=\s*SEARCH_V2_DEFAULT_AND_MODE", js)
    sel = re.search(r'<select[^>]*id="and-mode".*?</select>', page, re.S)
    assert sel, "缺少 AND 模式控件 #and-mode（strict 回退入口）"
    block = sel.group(0)
    for v in ("soft", "strict", "any"):
        assert f'value="{v}"' in block
    assert re.search(r'value="soft"\s+selected', block), "AND 模式默认不是 soft"
    assert re.search(r"\$\('and-mode'\)\.onchange", js)
    # 4) 结果栏暴露当前 AND 模式（可核对前后端一致）
    assert "AND:${AND_MODE}" in js


# ------------------------------------------------------------------ 4. 同义词一致
def test_synonyms_parity_with_json():
    groups = _inline_synonym_groups()
    assert len(groups) >= 20, f"内联同义词组仅 {len(groups)} 组，要求 ≥20"
    data = json.loads(SYN_JSON.read_text(encoding="utf-8"))
    assert groups == data["groups"], "页面内联同义词与 tools/kb_corpus/synonyms_v2.json 不一致"
    # 页面注释里标注了同义词源路径（灰度可追溯）
    assert "tools/kb_corpus/synonyms_v2.json" in _html()
    # 覆盖 wave7 评测里的关键别名
    flat = {w for g in groups for w in g}
    for kw in ("太阳能", "光伏", "IDC", "数据中心", "钠离子电池", "量柱", "量线"):
        assert kw in flat, f"同义词表缺少关键别名: {kw}"


# ------------------------------------------------------------------ 5. 降级与保留
def test_legacy_fallback_and_blocks_preserved():
    page, js = _html(), _js()
    # 旧逻辑保留（回退）
    assert "function tok(s)" in js and "function score(qt,u)" in js
    # 异常降级：v2 抛错时回退 v1 并提示
    assert re.search(r"catch\s*\(e\)", js) and "回退 v1" in js
    assert "renderV1()" in js
    # 既有区块保留
    for el in ("learning-cards", "learning-cards-list", "daily-briefing",
               "daily-briefing-list", "h18-body", "strategy-note"):
        assert f'id="{el}"' in page, f"既有区块丢失: #{el}"
    # 学习卡/简报/H18 的加载逻辑仍在
    assert "loadDigest()" in js and "loadH18()" in js
    assert "/static/kb_learning_digest.json" in js
    # v2 语法点：AND/OR/短语/排除
    assert "v2Parse" in js and re.search(r"split\(/\\s\*\\\|", js) or "or_branches" in js
    assert "startsWith('-')" in js and "exclude" in js
    assert "phrase" in js


# ------------------------------------------------------------------ 6. node --check
def test_inline_js_node_check(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用，跳过 JS 语法校验")
    blocks = _inline_scripts()
    assert blocks, "页面没有任何内联 <script> 块"
    js_file = tmp_path / "kb_reports_inline.js"
    js_file.write_text("\n;\n".join(blocks), encoding="utf-8")
    proc = subprocess.run([node, "--check", str(js_file)], capture_output=True, text=True,
                          timeout=30)
    assert proc.returncode == 0, f"node --check 失败：{proc.stderr[:800]}"


# ------------------------------------------------------------------ 7. HTTP 200
def test_portal_serves_page_http_200():
    url = "http://127.0.0.1:8734/kb_reports.html?ts=unittest"
    try:
        with safe_urlopen(url, timeout=5) as r:
            body = r.read().decode("utf-8", errors="replace")
            assert r.status == 200
    except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
        pytest.skip(f"门户 8734 未在线，跳过 HTTP 校验：{exc}")
    assert "SEARCH_V2_SYNONYMS_BEGIN" in body, "HTTP 返回的页面未包含 v2 标记（可能缓存旧文件）"
    assert 'id="engine"' in body
    # wave13 软 AND 同步：HTTP 返回的页面也必须带软 AND 标记与控件
    assert "SEARCH_V2_SOFT_COV" in body and "SEARCH_V2_SYNONYM_WEIGHT" in body
    assert 'id="and-mode"' in body and 'value="soft"' in body
