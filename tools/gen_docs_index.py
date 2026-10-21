#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DOCS_INDEX.md 的生成与校验工具。

用法（在任意目录运行均可）::

    python -B tools/gen_docs_index.py            # 重新生成 C:\\项目\\DOCS_INDEX.md
    python -B tools/gen_docs_index.py --check    # 只比对差异，不写文件（有差异退出码 1）

扫描范围（五处，共 5 节）
------------------------
* 二、老田的AI助手/docs/plans      （递归）
* 三、老田的AI助手/docs/audit      （递归，含 _archive）
* 四、老田的AI助手/docs/ops        （递归）
* 五、工作区根 *.md                （仅顶层，不递归）
* 六、C:\\项目\\deliverables        （递归）

字段口径
--------
* 标题：文件首个 ``# `` 一级标题；无标题时退回文件名；超过 100 字硬截断。
* 日期：文件 mtime 的 ``MM-DD``（不是文件名里的日期）。
* 文件：相对工作区根的 posix 路径。
* 排序：mtime 倒序，同一时间按路径升序。
* 特例：本索引自身（§五 的 ``DOCS_INDEX.md``）按「本次运行时刻」计，
  否则每次写盘都会改自己的 mtime，导致自己那一行在 §五 里来回跳。

人工维护区
----------
「一、现行权威」「七、已知的取代/重复关系」「八、人工备注」三段**不生成**：
脚本从旧索引里原样解析出来再逐行写回，内容不做任何改写；旧索引缺失时才写入空模板。

其他
----
* 输出 UTF-8 无 BOM、LF 换行、末行带换行。
* ``--check`` 比对：新增 / 删除 / 标题变化 / 日期变化 / 节标题变化 / 节说明变化。
  「生成时间」行每次运行都会变，不参与比对，因此生成后立刻 ``--check`` 应为 0。
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

PROJECT_DIRNAME = "老田的AI助手"
OUTPUT_FILENAME = "DOCS_INDEX.md"
TITLE_LIMIT = 100
TIME_PREFIX = "- 生成时间："
NOTE_TEMPLATE_DELIVERABLES = "> 与 `老田的AI助手/deliverables` 有 **{dup} 篇同名**；当前位置待裁决。"

SECTIONS = (
    {
        "num": "二",
        "display": "二、拍板与设计",
        "dir_parts": (PROJECT_DIRNAME, "docs", "plans"),
        "prefix": "docs/plans/",
        "recursive": True,
        "heading": "## 二、拍板与设计 · `老田的AI助手/docs/plans`（{count} 份）",
        "note_template": None,
    },
    {
        "num": "三",
        "display": "三、审计与判例",
        "dir_parts": (PROJECT_DIRNAME, "docs", "audit"),
        "prefix": "docs/audit/",
        "recursive": True,
        "heading": "## 三、审计与判例 · `老田的AI助手/docs/audit`（{count} 份）",
        "note_template": None,
    },
    {
        "num": "四",
        "display": "四、运维操作单",
        "dir_parts": (PROJECT_DIRNAME, "docs", "ops"),
        "prefix": "docs/ops/",
        "recursive": True,
        "heading": "## 四、运维操作单 · `老田的AI助手/docs/ops`（{count} 份）",
        "note_template": None,
    },
    {
        "num": "五",
        "display": "五、根目录文档",
        "dir_parts": (),
        "prefix": "",
        "recursive": False,
        "heading": "## 五、根目录文档（{count} 份）",
        "note_template": None,
    },
    {
        "num": "六",
        "display": "六、交付与评审产物",
        "dir_parts": ("deliverables",),
        "prefix": "deliverables/",
        "recursive": True,
        "heading": "## 六、交付与评审产物 · `deliverables`（{count} 份）",
        "note_template": NOTE_TEMPLATE_DELIVERABLES,
    },
)

DEFAULT_PREAMBLE = (
    "# 文档索引 DOCS_INDEX",
    "",
    "- 生成时间：{timestamp}（脚本自动生成，手工编辑请写在「人工备注」段）",
    "- 用途：**回答「哪个文档是现行的」**。新增文档请在此登记；"
    "被取代的文档在原文头部加 `> STATUS: superseded by <新文档>`。",
    "- 口径：`plans` 是拍板与设计，`audit` 是审计与判例，`ops` 是运维操作单；"
    "同名不同日期者，**以状态为「现行」者为准**。",
)

DEFAULT_SECTION_ONE = (
    "## 一、现行权威（人工维护）",
    "",
    "| 文档 | 领域 | 状态 |",
    "|---|---|---|",
    "| _（手工维护区，脚本不覆盖本段）_ | — | — |",
)

DEFAULT_SECTION_SEVEN = (
    "## 七、已知的取代/重复关系",
    "",
    "| 关系 | 涉及文档 | 处置 |",
    "|---|---|---|",
    "| _（手工维护区，脚本不覆盖本段）_ | — | — |",
)

DEFAULT_SECTION_EIGHT = (
    "## 八、人工备注",
    "",
    "- 维护规则：新增 → 登记到最近的一节；作废 → 原文加 `STATUS: superseded` 并在第七节登记。",
    "- 本文件由 `老田的AI助手/tools/gen_docs_index.py` 生成；「一、七、八」为人工维护区，脚本原样保留。",
)

HEADING_RE = re.compile(r"^## ([一二三四五六七八九十])、")
ROW_RE = re.compile(r"^\|\s*(\d{2}-\d{2})\s*\|\s*(.*?)\s*\|\s*`(.*?)`\s*\|$")


# ---------------------------------------------------------------- 基础工具


def _say(text: str = "") -> None:
    """打印一行；控制台编码不支持时降级，不因输出中断整轮生成。"""
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", "replace").decode("ascii"))


def _read_text(path: Path) -> str:
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        return fh.read()


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _fmt_mtime(path: Path) -> str:
    try:
        return dt.datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    except OSError:
        return "（不可读）"


def read_title(path: Path) -> str:
    """取文件首个 ``# `` 一级标题；没有则返回空串。"""
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            for raw in fh:
                line = raw.strip()
                if line.startswith("# "):
                    title = line[2:].strip()
                    if title:
                        return title
    except OSError:
        pass
    return ""


def format_title(title: str, fallback: str) -> str:
    """标题落表前的清洗：单行化、100 字截断、转义竖线。"""
    text = title or fallback
    for ch in ("\r", "\n", "\t"):
        text = text.replace(ch, " ")
    text = text.strip()
    if len(text) > TITLE_LIMIT:
        text = text[:TITLE_LIMIT]
    return text.replace("|", r"\|")


@dataclass(frozen=True)
class Doc:
    path: str  # 相对工作区根，posix
    rel: str  # 相对所在节目录，posix
    title: str
    mtime: float

    @property
    def date(self) -> str:
        return dt.datetime.fromtimestamp(self.mtime).strftime("%m-%d")


# ---------------------------------------------------------------- 扫描


def iter_markdown(base: Path, recursive: bool) -> list[Path]:
    if not base.is_dir():
        return []
    pattern = "**/*.md" if recursive else "*.md"
    found = []
    for path in base.glob(pattern):
        if path.is_file() and not path.name.startswith("."):
            found.append(path)
    return found


def collect(spec: dict, workspace: Path, self_path: Path | None = None) -> list[Doc]:
    base = workspace.joinpath(*spec["dir_parts"])
    docs: list[Doc] = []
    for path in iter_markdown(base, spec["recursive"]):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if self_path is not None and path == self_path:
            mtime = time.time()  # 本索引自身：按本次运行时刻计，恒排 §五 首位
        rel = path.relative_to(base).as_posix()
        docs.append(
            Doc(
                path=spec["prefix"] + rel,
                rel=rel,
                title=format_title(read_title(path), path.stem),
                mtime=mtime,
            )
        )
    docs.sort(key=lambda item: (-item.mtime, item.path))
    return docs


def count_same_name(workspace: Path) -> int:
    """根 deliverables 与 老田的AI助手/deliverables 的相对路径同名篇数。"""

    def rels(base: Path) -> set[str]:
        return {p.relative_to(base).as_posix() for p in iter_markdown(base, True)}

    return len(rels(workspace / "deliverables") & rels(workspace / PROJECT_DIRNAME / "deliverables"))


# ---------------------------------------------------------------- 渲染


def render_block(spec: dict, docs: list[Doc], note: str | None) -> list[str]:
    lines = [spec["heading"].format(count=len(docs)), ""]
    if note:
        lines.extend([note, ""])
    lines.append("| 日期 | 标题 | 文件 |")
    lines.append("|---|---|---|")
    lines.extend(f"| {doc.date} | {doc.title} | `{doc.path}` |" for doc in docs)
    return lines


def split_sections(text: str) -> tuple[list[str], dict[str, list[str]]]:
    """按 ``## 一、`` 式标题切块；块内含标题行，去掉尾部空行。"""
    preamble: list[str] = []
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in _normalize_newlines(text).split("\n"):
        match = HEADING_RE.match(line)
        if match:
            current = match.group(1)
            sections[current] = [line]
            continue
        if current is None:
            preamble.append(line)
        else:
            sections[current].append(line)
    for block in sections.values():
        while block and not block[-1].strip():
            block.pop()
    while preamble and not preamble[-1].strip():
        preamble.pop()
    return preamble, sections


def parse_rows(block: list[str]) -> list[tuple[str, str, str]]:
    """从一节里解析出 (日期, 标题, 文件) 行；非表格行自动跳过。"""
    rows = []
    for line in block:
        match = ROW_RE.match(line.rstrip())
        if match:
            rows.append((match.group(1), match.group(2).replace(r"\|", "|"), match.group(3)))
    return rows


def stamp_preamble(preamble: list[str], timestamp: str) -> list[str]:
    if not preamble:
        return [line.replace("{timestamp}", timestamp) for line in DEFAULT_PREAMBLE]
    out = []
    replaced = False
    stamp = f"{TIME_PREFIX}{timestamp}（脚本自动生成，手工编辑请写在「人工备注」段）"
    for line in preamble:
        if line.startswith(TIME_PREFIX):
            out.append(stamp)
            replaced = True
        else:
            out.append(line)
    if not replaced:
        insert_at = 1 if out and out[0].startswith("# ") else 0
        if insert_at == 1 and len(out) > 1 and not out[1].strip():
            insert_at = 2
        out.insert(insert_at, stamp)
    return out


def manual_block(sections: dict[str, list[str]], num: str, default: tuple[str, ...]) -> list[str]:
    """人工维护区：旧索引有就原样返回，没有才用空模板。"""
    block = sections.get(num)
    if block and len(block) > 1:
        return list(block)
    return list(default)


def build_lines(
    workspace: Path,
    old_text: str,
    built: list[tuple[dict, list[Doc]]],
    timestamp: str,
) -> list[str]:
    preamble, sections = split_sections(old_text) if old_text else ([], {})
    lines = stamp_preamble(preamble, timestamp)
    lines.append("")
    lines.extend(manual_block(sections, "一", DEFAULT_SECTION_ONE))
    for spec, docs in built:
        note = None
        if spec["note_template"]:
            note = spec["note_template"].format(dup=count_same_name(workspace))
        lines.extend(render_block(spec, docs, note))
    lines.extend(manual_block(sections, "七", DEFAULT_SECTION_SEVEN))
    lines.extend(manual_block(sections, "八", DEFAULT_SECTION_EIGHT))
    return lines


def write_output(path: Path, lines: list[str]) -> None:
    text = "\n".join(lines).rstrip("\n") + "\n"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


# ---------------------------------------------------------------- 校验


def diff_rows(
    old_rows: list[tuple[str, str, str]], docs: list[Doc]
) -> tuple[list[Doc], list[tuple[str, tuple[str, str]]], list[tuple[Doc, str]], list[tuple[Doc, str]]]:
    old_map: dict[str, tuple[str, str]] = {}
    for date, title, path in old_rows:
        old_map.setdefault(path, (date, title))
    new_paths = {doc.path for doc in docs}
    added = [doc for doc in docs if doc.path not in old_map]
    removed = [(path, value) for path, value in old_map.items() if path not in new_paths]
    changed_title: list[tuple[Doc, str]] = []
    changed_date: list[tuple[Doc, str]] = []
    for doc in docs:
        old = old_map.get(doc.path)
        if old is None:
            continue
        if old[1] != doc.title:
            changed_title.append((doc, old[1]))
        if old[0] != doc.date:
            changed_date.append((doc, old[0]))
    return added, removed, changed_title, changed_date


def check(workspace: Path, out_path: Path, built: list[tuple[dict, list[Doc]]], old_text: str) -> int:
    _preamble, sections = split_sections(old_text)
    totals = {"added": 0, "removed": 0, "title": 0, "date": 0, "heading": 0, "note": 0}

    _say("== DOCS_INDEX 差异检查（--check，不写文件）==")
    _say(f"工作区：{workspace}")
    _say(f"索引：  {out_path}")
    _say(f"        {len(old_text.splitlines())} 行 / {out_path.stat().st_size} 字节 / mtime {_fmt_mtime(out_path)}")
    _say("")

    for spec, docs in built:
        num = spec["num"]
        old_block = sections.get(num, [])
        old_rows = parse_rows(old_block)
        new_heading = spec["heading"].format(count=len(docs))
        old_heading = old_block[0] if old_block else "（缺失）"
        added, removed, changed_title, changed_date = diff_rows(old_rows, docs)
        heading_changed = old_heading != new_heading

        new_note = None
        old_note = ""
        note_changed = False
        if spec["note_template"]:
            new_note = spec["note_template"].format(dup=count_same_name(workspace))
            old_note = next((line for line in old_block if line.startswith("> ")), "")
            note_changed = old_note != new_note

        if not (added or removed or changed_title or changed_date or heading_changed or note_changed):
            _say(f"[{spec['display']}] 无差异（{len(docs)} 份）")
            continue

        _say(f"[{spec['display']}] 现有 {len(old_rows)} 份 → 生成 {len(docs)} 份")
        if heading_changed:
            _say(f"  节标题变化：{old_heading}")
            _say(f"                → {new_heading}")
            totals["heading"] += 1
        if note_changed:
            _say(f"  节说明变化：{old_note or '（缺失）'}")
            _say(f"                → {new_note}")
            totals["note"] += 1
        if added:
            _say(f"  新增 {len(added)} 份：")
            for doc in added:
                _say(f"    + {doc.path}  《{doc.title}》（{doc.date}）")
            totals["added"] += len(added)
        if removed:
            _say(f"  删除 {len(removed)} 份：")
            for path, (date, title) in removed:
                _say(f"    - {path}  《{title}》（{date}）")
            totals["removed"] += len(removed)
        if changed_title:
            _say(f"  标题变化 {len(changed_title)} 份：")
            for doc, old_title in changed_title:
                _say(f"    ~ {doc.path}")
                _say(f"        旧：《{old_title}》")
                _say(f"        新：《{doc.title}》")
            totals["title"] += len(changed_title)
        if changed_date:
            _say(f"  日期变化 {len(changed_date)} 份：")
            for doc, old_date in changed_date:
                _say(f"    ~ {doc.path}  {old_date} → {doc.date}")
            totals["date"] += len(changed_date)
        _say("")

    total = sum(totals.values())
    _say(
        "差异合计：新增 {added} / 删除 {removed} / 标题变化 {title} / "
        "日期变化 {date} / 节标题变化 {heading} / 节说明变化 {note}".format(**totals)
    )
    if total:
        _say("结论：索引与扫描结果不一致 → 运行 `python -B tools/gen_docs_index.py` 更新（退出码 1）")
        return 1
    _say("结论：生成结果与现有索引一致（退出码 0）")
    return 0


# ---------------------------------------------------------------- 入口


def resolve_workspace(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / PROJECT_DIRNAME).is_dir():
            return parent
    return Path.cwd().resolve()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gen_docs_index.py",
        description="生成 / 校验 C:\\项目\\DOCS_INDEX.md（八节结构；一、七、八 为人工维护区）",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="只比对生成结果与现有索引的差异，不写文件；有差异退出码 1，无差异 0",
    )
    parser.add_argument(
        "--workspace",
        default=None,
        help="工作区根目录（默认由脚本位置推导：本脚本位于 老田的AI助手/tools/）",
    )
    args = parser.parse_args(argv)

    workspace = resolve_workspace(args.workspace)
    project = workspace / PROJECT_DIRNAME
    if not project.is_dir():
        _say(f"错误：项目目录不存在：{project}")
        return 2

    out_path = workspace / OUTPUT_FILENAME
    built = [
        (spec, collect(spec, workspace, out_path if spec["num"] == "五" else None))
        for spec in SECTIONS
    ]
    if not any(docs for _spec, docs in built):
        _say(f"错误：五处来源均未扫描到 .md，请确认工作区：{workspace}")
        return 2

    old_text = _read_text(out_path) if out_path.is_file() else ""

    if args.check:
        if not old_text:
            _say(f"现有索引不存在或为空：{out_path}")
            _say("结论：需要生成（退出码 1）")
            return 1
        return check(workspace, out_path, built, old_text)

    timestamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    # 首次从零生成（索引文件还不存在）时，索引自身在 §五 里也是一篇文档；
    # 第一遍写盘后它才存在，所以必须再生成一遍，否则产物缺自己这一行、
    # 紧跟其后的 --check 会报"新增 DOCS_INDEX.md"（退出码 1），自相矛盾。
    # 已有索引时只需一遍。
    passes = 1 if old_text else 2
    lines: list[str] = []
    for _pass in range(passes):
        built = [
            (spec, collect(spec, workspace, out_path if spec["num"] == "五" else None))
            for spec in SECTIONS
        ]
        current = _read_text(out_path) if out_path.is_file() else ""
        lines = build_lines(workspace, current, built, timestamp)
        try:
            write_output(out_path, lines)
        except OSError as exc:
            _say(f"错误：写入失败：{out_path}：{exc}")
            return 2

    _say(f"已写入 {out_path}")
    _say(f"格式：UTF-8 无 BOM / LF 换行 / {len(lines)} 行")
    _say("各节份数：")
    total = 0
    for spec, docs in built:
        total += len(docs)
        _say(f"  {spec['display']}  {len(docs)} 份")
    _say(f"  合计  {total} 份")
    _say("人工维护区「一、现行权威」「七、已知的取代/重复关系」「八、人工备注」已从旧索引原样保留。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
