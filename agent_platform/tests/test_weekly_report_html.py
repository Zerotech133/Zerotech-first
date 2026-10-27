# -*- coding: utf-8 -*-
"""tools/weekly_report_html.py 测试（WO-DRILL2-WEEKLYHTML）。

覆盖：
1. 轻量 Markdown 渲染：标题/表格/列表/段落/引用/行内 code/bold/链接；
2. HTML 转义与危险协议拦截；
3. 页面骨架含 tokens.css、nav.js，且样式引用 tokens 变量；
4. `--src/--out` 生成、`--check` 通过/缺产物/缺标记三类退出码；
5. 全部落盘均走 tmp_path，不写生产产物。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_WT = Path(__file__).resolve().parents[2]
_TOOL = _WT / "tools" / "weekly_report_html.py"
_spec = importlib.util.spec_from_file_location("weekly_report_html", str(_TOOL))
WRH = importlib.util.module_from_spec(_spec)
sys.modules["weekly_report_html"] = WRH
_spec.loader.exec_module(WRH)

SAMPLE_MD = """# 研发部周报（测试样例）

- as-of：2026-10-27
- 数据源：`tasks.json`

## 一、八项指标

| 指标 | 当前值 | 判读 |
|---|---:|---|
| E3 触发率 | 4/4 | **达标** |
| 采集份额 | 1.40% | 观察 |

### 子项

段落含 `a < b`、**加粗**、[链接](https://example.com/a?x=1&y=2) 与 <script>alert('x')</script>。

> 本文件由生成器覆盖写。
"""


def test_render_markdown_blocks():
    """标题/表格/列表/段落/引用按块级标签渲染，表头对齐带出来。"""
    out = WRH.render_markdown(SAMPLE_MD)
    assert "<h1>研发部周报（测试样例）</h1>" in out
    assert "<h2>一、八项指标</h2>" in out
    assert "<h3>子项</h3>" in out
    assert '<div class="table-wrap">' in out
    assert "<thead>" in out and "<tbody>" in out
    assert "<th>指标</th>" in out
    assert '<th style="text-align:right">当前值</th>' in out
    assert '<td style="text-align:right">4/4</td>' in out
    assert "<strong>达标</strong>" in out
    assert "<ul>" in out
    assert "<li>as-of：2026-10-27</li>" in out
    assert "<blockquote><p>本文件由生成器覆盖写。</p></blockquote>" in out


def test_inline_code_bold_link_and_escape():
    """行内三件套生效，正文/代码里的 HTML 必须转义。"""
    out = WRH.render_markdown(SAMPLE_MD)
    assert "<code>a &lt; b</code>" in out
    assert "<strong>加粗</strong>" in out
    assert (
        '<a href="https://example.com/a?x=1&amp;y=2" rel="noopener noreferrer">链接</a>'
        in out
    )
    assert "&lt;script&gt;alert('x')&lt;/script&gt;" in out
    assert "<script>" not in out


def test_code_span_does_not_trigger_bold():
    """反引号内是原样文本，`**x**` 不得被当成粗体。"""
    out = WRH.render_markdown("行内 `**not bold**` 与 **真加粗**。")
    assert "<code>**not bold**</code>" in out
    assert "<strong>真加粗</strong>" in out
    assert "<strong>not bold</strong>" not in out


def test_unsafe_link_scheme_neutralized():
    """javascript:/data:/vbscript: 链接统一降为 #，不把危险协议写进 href。"""
    out = WRH.render_markdown("[x](javascript:alert(1))")
    assert 'href="#"' in out
    assert "javascript:" not in out


def test_table_cell_html_escaped():
    """表格单元格里的 HTML 与行内标记同样走转义/白名单渲染。"""
    out = WRH.render_markdown("| 列 |\n|---|\n| <b>x</b> |\n")
    assert "&lt;b&gt;x&lt;/b&gt;" in out
    assert "<b>x</b>" not in out


def test_build_page_frontend_triplet():
    """页面骨架必须引 tokens.css + nav.js，且样式用 tokens 变量。"""
    page = WRH.build_page(
        "<p>正文</p>",
        title="研发部周报",
        source_label="agent_platform/data/dev/weekly_report.md",
    )
    assert page.startswith("<!DOCTYPE html>")
    assert '<meta charset="UTF-8">' in page
    assert 'href="/static/tokens.css"' in page
    assert 'src="/static/nav.js"' in page
    assert "<title>研发部周报 · 零界</title>" in page
    assert "研发部周报" in page
    for token in ("var(--bg-page)", "var(--text)", "var(--border)", "var(--link)"):
        assert token in page


def test_cli_generates_and_check_passes(tmp_path):
    """--src/--out 生成到临时目录，随后 --check 返回 0。"""
    src = tmp_path / "weekly_report.md"
    src.write_text(SAMPLE_MD, encoding="utf-8")
    out = tmp_path / "docs" / "weekly_report.html"

    assert WRH.main(["--src", str(src), "--out", str(out)]) == 0
    assert out.is_file()
    text = out.read_text(encoding="utf-8")
    for marker in WRH.REQUIRED_MARKERS:
        assert marker in text
    assert "&lt;script&gt;" in text
    assert WRH.main(["--out", str(out), "--check"]) == 0


def test_check_rejects_missing_output(tmp_path):
    """产物不存在时 --check 返回 1。"""
    assert WRH.main(["--out", str(tmp_path / "missing.html"), "--check"]) == 1


def test_check_rejects_missing_markers(tmp_path):
    """产物存在但缺 tokens.css/nav.js 时 --check 返回 1，补齐后返回 0。"""
    out = tmp_path / "weekly_report.html"
    out.write_text(
        "<!DOCTYPE html><html><head><title>研发部周报</title></head>"
        "<body><table></table></body></html>",
        encoding="utf-8",
    )
    assert WRH.main(["--out", str(out), "--check"]) == 1

    out.write_text(
        WRH.build_page("<table><tr><td>研发部周报</td></tr></table>", title="研发部周报"),
        encoding="utf-8",
    )
    assert WRH.main(["--out", str(out), "--check"]) == 0


def test_missing_source_returns_1_and_writes_nothing(tmp_path):
    """源文件缺失时返回 1，且不产生空产物。"""
    out = tmp_path / "out.html"
    assert WRH.main(["--src", str(tmp_path / "no.md"), "--out", str(out)]) == 1
    assert not out.exists()
