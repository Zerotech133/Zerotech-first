# -*- coding: utf-8 -*-
"""研发部周报 Markdown→HTML 渲染器（WO-DRILL2-WEEKLYHTML）。

把 `agent_platform/data/dev/weekly_report.md` 渲染成 `docs/dev/weekly_report.html`：
- 轻量 Markdown 子集：标题（#/##/###）、表格（| 行 + 分隔行）、无序列表（-）、
  段落、引用（>），以及行内 `code`、**bold**、[text](url)；
- 所有文本先做 HTML 转义，再拼装白名单标签，不执行 Markdown 之外的任何内容；
- 产物复用前端门禁三件套里的 `/static/tokens.css` 与 `/static/nav.js`，
  页内配色/字号/间距/圆角全部引用 tokens 变量。

用法：
  python -X utf8 tools/weekly_report_html.py
  python -X utf8 tools/weekly_report_html.py --src <md> --out <html>
  python -X utf8 tools/weekly_report_html.py --check

`--check` 只校验产物：存在且含「研发部周报」「<table」「tokens.css」「nav.js」，
任一不满足返回 1。
"""
from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SRC = ROOT / "agent_platform" / "data" / "dev" / "weekly_report.md"
DEFAULT_OUT = ROOT / "docs" / "dev" / "weekly_report.html"
DEFAULT_SOURCE_LABEL = "agent_platform/data/dev/weekly_report.md"
REQUIRED_MARKERS = ("研发部周报", "<table", "tokens.css", "nav.js")

_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*$")
_LIST_RE = re.compile(r"^\s*-\s+(.+?)\s*$")
_LINK_RE = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)\)")
_H1_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


def _flush_text(buffer: list[str]) -> str:
    """把纯文本缓冲转义后输出，并清空缓冲。"""
    if not buffer:
        return ""
    text = html.escape("".join(buffer), quote=False)
    buffer.clear()
    return text


def _safe_url(url: str) -> str:
    """只拦危险协议；链接地址仍由调用方做属性转义。"""
    if url.lower().startswith(("javascript:", "data:", "vbscript:")):
        return "#"
    return url


def render_inline(text: str) -> str:
    """渲染行内 `code`、**bold**、[text](url)，其余字符按 HTML 文本转义。"""
    output: list[str] = []
    buffer: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char == "`":
            end = text.find("`", index + 1)
            if end > index + 1:
                output.append(_flush_text(buffer))
                output.append("<code>" + html.escape(text[index + 1 : end], quote=False) + "</code>")
                index = end + 1
                continue
        if text.startswith("**", index):
            end = text.find("**", index + 2)
            if end > index + 2:
                output.append(_flush_text(buffer))
                output.append("<strong>" + render_inline(text[index + 2 : end]) + "</strong>")
                index = end + 2
                continue
        if char == "[":
            match = _LINK_RE.match(text, index)
            if match:
                output.append(_flush_text(buffer))
                label, url = match.group(1), match.group(2)
                output.append(
                    '<a href="{}" rel="noopener noreferrer">{}</a>'.format(
                        html.escape(_safe_url(url), quote=True),
                        render_inline(label),
                    )
                )
                index = match.end()
                continue
        buffer.append(char)
        index += 1
    output.append(_flush_text(buffer))
    return "".join(output)


def _split_table_row(line: str) -> list[str]:
    """按 Markdown 表格语法拆单元格，容忍首尾竖线。"""
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|"):
        text = text[:-1]
    return [cell.strip() for cell in text.split("|")]


def _is_table_start(lines: list[str], index: int) -> bool:
    """当前行是表头、下一行是同列数的分隔行时，判定为表格起点。"""
    if index + 1 >= len(lines) or not lines[index].lstrip().startswith("|"):
        return False
    header = _split_table_row(lines[index])
    separator = _split_table_row(lines[index + 1])
    if len(header) != len(separator):
        return False
    return all(re.fullmatch(r":?-{3,}:?", cell) for cell in separator)


def _table_align(cell: str) -> str:
    """从分隔行单元格提取对齐方式：:---: 居中、---: 右、:--- 左。"""
    left = cell.startswith(":")
    right = cell.endswith(":")
    if left and right:
        return "center"
    if right:
        return "right"
    if left:
        return "left"
    return ""


def _cell_tag(tag: str, align: str) -> str:
    if align:
        return f'<{tag} style="text-align:{align}">'
    return f"<{tag}>"


def _render_table(header: list[str], aligns: list[str], rows: list[list[str]]) -> str:
    """渲染表格；数据行按表头列数补齐/截断，单元格内容走行内渲染。"""
    parts = ['<div class="table-wrap">', "<table>", "<thead>", "<tr>"]
    for index, cell in enumerate(header):
        align = aligns[index] if index < len(aligns) else ""
        parts.append(_cell_tag("th", align) + render_inline(cell) + "</th>")
    parts.extend(["</tr>", "</thead>", "<tbody>"])
    for row in rows:
        parts.append("<tr>")
        for index in range(len(header)):
            cell = row[index] if index < len(row) else ""
            align = aligns[index] if index < len(aligns) else ""
            parts.append(_cell_tag("td", align) + render_inline(cell) + "</td>")
        parts.append("</tr>")
    parts.extend(["</tbody>", "</table>", "</div>"])
    return "\n".join(parts)


def _is_block_start(lines: list[str], index: int) -> bool:
    """段落扫描时遇到这些行即结束当前段落。"""
    line = lines[index]
    stripped = line.strip()
    if not stripped:
        return True
    if _HEADING_RE.match(stripped):
        return True
    if _LIST_RE.match(line):
        return True
    if stripped.startswith(">"):
        return True
    if _is_table_start(lines, index):
        return True
    return False


def render_markdown(markdown: str) -> str:
    """把轻量 Markdown 子集渲染为 HTML 片段（不含页面骨架）。"""
    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[str] = []
    index = 0
    total = len(lines)
    while index < total:
        line = lines[index]
        if not line.strip():
            index += 1
            continue

        heading = _HEADING_RE.match(line.strip())
        if heading:
            level = len(heading.group(1))
            blocks.append(f"<h{level}>{render_inline(heading.group(2))}</h{level}>")
            index += 1
            continue

        if _is_table_start(lines, index):
            header = _split_table_row(lines[index])
            aligns = [_table_align(cell) for cell in _split_table_row(lines[index + 1])]
            index += 2
            rows: list[list[str]] = []
            while index < total and lines[index].lstrip().startswith("|"):
                rows.append(_split_table_row(lines[index]))
                index += 1
            blocks.append(_render_table(header, aligns, rows))
            continue

        if _LIST_RE.match(line):
            items: list[str] = []
            while index < total:
                item = _LIST_RE.match(lines[index])
                if not item:
                    break
                content = [item.group(1).strip()]
                index += 1
                # 缩进续行并入当前列表项，避免轻量渲染器把长条目拆成段落。
                while (
                    index < total
                    and lines[index].strip()
                    and lines[index].startswith("  ")
                    and not _LIST_RE.match(lines[index])
                ):
                    content.append(lines[index].strip())
                    index += 1
                items.append("<li>" + render_inline(" ".join(content)) + "</li>")
            blocks.append("<ul>\n" + "\n".join(items) + "\n</ul>")
            continue

        if line.lstrip().startswith(">"):
            quoted: list[str] = []
            while index < total and lines[index].lstrip().startswith(">"):
                quoted.append(lines[index].lstrip()[1:].lstrip())
                index += 1
            blocks.append("<blockquote><p>" + render_inline(" ".join(quoted)) + "</p></blockquote>")
            continue

        paragraph = [line.strip()]
        index += 1
        while index < total and not _is_block_start(lines, index):
            paragraph.append(lines[index].strip())
            index += 1
        blocks.append("<p>" + render_inline(" ".join(paragraph)) + "</p>")
    return "\n".join(blocks)


def _extract_title(markdown: str) -> str:
    """取首个一级标题作为页面标题；剥掉行内标记，缺失时回退固定标题。"""
    match = _H1_RE.search(markdown)
    if not match:
        return "研发部周报"
    title = match.group(1)
    title = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", title)
    title = title.replace("`", "").replace("*", "").strip()
    return title or "研发部周报"


PAGE_CSS = """
  * { box-sizing: border-box; }
  html { background: var(--bg-page); }
  body {
    margin: 0;
    background: var(--bg-page);
    color: var(--text);
    font-family: var(--font-ui);
    font-size: var(--fs-m);
    line-height: 1.75;
  }
  main {
    max-width: var(--page-w);
    margin: 0 auto;
    padding: var(--sp-5) var(--sp-5) 64px;
  }
  .source {
    margin: 0 0 var(--sp-5);
    padding: var(--sp-3) var(--sp-4);
    border: 1px solid var(--border-weak);
    border-radius: var(--r-m);
    background: var(--bg-surface);
    color: var(--text-dim);
    font-size: var(--fs-s);
  }
  .source code { padding: 0; border: 0; background: transparent; }
  h1 {
    margin: 0 0 var(--sp-4);
    font-size: var(--fs-xl);
    line-height: 1.4;
    letter-spacing: .02em;
  }
  h2 {
    margin: var(--sp-5) 0 var(--sp-3);
    padding-bottom: var(--sp-2);
    border-bottom: 1px solid var(--border);
    font-size: var(--fs-l);
  }
  h3 {
    margin: var(--sp-4) 0 var(--sp-2);
    font-size: var(--fs-m);
  }
  p { margin: var(--sp-3) 0; }
  a { color: var(--link); text-decoration: none; }
  a:hover { text-decoration: underline; }
  code {
    padding: 1px 5px;
    border: 1px solid var(--border-weak);
    border-radius: var(--r-s);
    background: var(--bg-raised);
    font-size: .92em;
  }
  ul { margin: var(--sp-3) 0; padding-left: var(--sp-5); }
  li { margin: var(--sp-1) 0; }
  blockquote {
    margin: var(--sp-4) 0;
    padding: var(--sp-2) var(--sp-4);
    border-left: 3px solid var(--accent);
    border-radius: 0 var(--r-s) var(--r-s) 0;
    background: var(--bg-surface);
    color: var(--text-dim);
  }
  blockquote p { margin: 0; }
  .table-wrap { margin: var(--sp-4) 0; overflow-x: auto; }
  table {
    width: 100%;
    border-collapse: collapse;
    border: 1px solid var(--border);
    background: var(--bg-surface);
    font-size: var(--fs-s);
  }
  th, td {
    padding: var(--sp-2) var(--sp-3);
    border: 1px solid var(--border);
    text-align: left;
    vertical-align: top;
  }
  th {
    background: var(--bg-raised);
    color: var(--text-dim);
    font-weight: 600;
    white-space: nowrap;
  }
  tbody tr:hover td { background: var(--bg-raised); }
  @media (max-width: 720px) {
    main { padding: var(--sp-4) var(--sp-3) 40px; }
    th, td { padding: var(--sp-1) var(--sp-2); }
  }
"""


def build_page(
    body: str,
    title: str = "研发部周报",
    source_label: str = DEFAULT_SOURCE_LABEL,
) -> str:
    """套上页面骨架：tokens.css + nav.js + 全部使用 tokens 变量的内联样式。"""
    safe_title = html.escape(title, quote=False)
    page_title = safe_title if "零界" in safe_title else safe_title + " · 零界"
    safe_label = html.escape(source_label, quote=False)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{page_title}</title>
<link rel="stylesheet" href="/static/tokens.css">
<style>{PAGE_CSS}</style>
<script src="/static/nav.js" defer></script>
</head>
<body>
<main>
<div class="source">来源：<code>{safe_label}</code> · 渲染器：<code>tools/weekly_report_html.py</code></div>
<article>
{body}
</article>
</main>
</body>
</html>
"""


def render_document(markdown: str, source_label: str = DEFAULT_SOURCE_LABEL) -> str:
    """Markdown 全文 → 完整 HTML 页面。"""
    return build_page(render_markdown(markdown), title=_extract_title(markdown), source_label=source_label)


def check_output(out: Path) -> tuple[bool, list[str]]:
    """校验产物存在且含验收要求的四个标记；返回 (是否通过, 问题列表)。"""
    path = Path(out)
    if not path.is_file():
        return False, [f"产物不存在：{path}"]
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:  # pragma: no cover - 权限/占用等环境异常
        return False, [f"产物读取失败：{exc}"]
    missing = [marker for marker in REQUIRED_MARKERS if marker not in text]
    if missing:
        return False, ["产物缺少必需标记：" + "、".join(missing)]
    return True, []


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：默认生成，`--check` 只校验；退出码 0 通过、1 失败。"""
    parser = argparse.ArgumentParser(description="研发部周报 Markdown→HTML 渲染器（tokens.css + nav.js）")
    parser.add_argument("--src", default=str(DEFAULT_SRC), help="Markdown 源文件（默认 agent_platform/data/dev/weekly_report.md）")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="HTML 产物（默认 docs/dev/weekly_report.html）")
    parser.add_argument("--check", action="store_true", help="只校验产物必需标记，不重新生成")
    args = parser.parse_args(argv)

    out = Path(args.out)
    if args.check:
        ok, problems = check_output(out)
        if ok:
            print(f"PASS：{out} 含研发部周报 / <table / tokens.css / nav.js")
            return 0
        print(f"FAIL：{out}", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    src = Path(args.src)
    if not src.is_file():
        print(f"FAIL：源文件不存在：{src}", file=sys.stderr)
        return 1
    try:
        markdown = src.read_text(encoding="utf-8-sig")
    except OSError as exc:
        print(f"FAIL：源文件读取失败：{exc}", file=sys.stderr)
        return 1

    try:
        source_label = src.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        source_label = str(src)
    page = render_document(markdown, source_label=source_label)

    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(page, encoding="utf-8", newline="\n")
    except OSError as exc:
        print(f"FAIL：产物写入失败：{exc}", file=sys.stderr)
        return 1
    print(f"OK：{src} → {out}（{len(page)} 字符）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
