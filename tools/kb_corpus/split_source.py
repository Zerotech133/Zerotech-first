#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""split_source.py — 把"拼接式"学习源大 md 按视频切成一视频一文件。

背景
----
某个学习项目的转写源被拼成了单个 md（114 条视频 / 约 50 万字节）。每天追加新视频
都会改变整文件 sha256，下游判定"源已变更"从而全量重算。本工具把大文件按视频切开，
使下游可以按 video_key 做 per-video 幂等。

输入结构（实测）
----------------
    # 北斗股术1688

    > 共 114 条 · 由 faster-whisper 转写，可能有个别同音字错误

    ## 20260823_2151 8月23日晚：解封了，来一篇。赚钱是结果，不是目标！

    - 文件：`20260823_2151_....mp4`
    - 时长：9:02
    - 字数：2894

    <正文，一整段，无换行>

    ---

用法
----
    python tools/kb_corpus/split_source.py --project 视频号学习_北斗股术1688
    python tools/kb_corpus/split_source.py --project 视频号学习_北斗股术1688 --write
    python tools/kb_corpus/split_source.py --project 视频号学习_北斗股术1688 --json

安全约束
--------
* 默认 dry-run，只有显式 --write 才落盘；
* 输出目录固定为 <项目目录>/source_split/，绝不写入、覆盖或删除既有 source/；
* 不执行任何删除操作，不做清库/清理；只新增或重写 source_split/ 下的产物；
* 仅使用 Python 标准库。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = "source_split.v1"

# 学习源件根目录（--source-root 可覆盖）
DEFAULT_SOURCE_ROOT = Path("C:/项目/知识库/05_学习源件")

SOURCE_DIRNAME = "source"
OUT_DIRNAME = "source_split"
MANIFEST_NAME = "manifest.json"

CST = timezone(timedelta(hours=8))

# 切分锚点：## YYYYMMDD_HHMM 标题
ANCHOR_RE = re.compile(r"^##[ \t]+(\d{8})_(\d{4})[ \t]+(.+?)[ \t]*$")

# 需剥离的元数据行（下游 top_terms 污染源）
META_PREFIXES: tuple[tuple[str, str], ...] = (
    ("file", "- 文件："),
    ("duration", "- 时长："),
    ("chars", "- 字数："),
)
META_LABELS = {"file": "文件", "duration": "时长", "chars": "字数"}

SEP_RE = re.compile(r"^-{3,}[ \t]*$")
TITLE_PREVIEW_CHARS = 30


class SplitError(RuntimeError):
    """可预期的使用错误（退出码 1）。"""


@dataclass
class Segment:
    """单条视频切分结果。"""

    video_key: str
    title: str
    body: str
    declared_chars: Optional[int] = None
    duration: Optional[str] = None
    source_file: Optional[str] = None
    found_meta: Dict[str, int] = field(default_factory=dict)
    pit: Optional[str] = None
    sha256: str = ""
    issues: List[str] = field(default_factory=list)

    @property
    def chars(self) -> int:
        return len(self.body)

    @property
    def filename(self) -> str:
        return f"{self.video_key}.md"

    @property
    def title_preview(self) -> str:
        title = self.title.strip()
        if len(title) <= TITLE_PREVIEW_CHARS:
            return title
        return title[:TITLE_PREVIEW_CHARS] + "…"


@dataclass
class ParseResult:
    preamble: List[str]
    segments: List[Segment]
    meta_counts: Dict[str, int]
    separator_lines: int
    blank_lines_dropped: int


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #
def read_source_text(path: Path) -> str:
    if not path.is_file():
        raise SplitError(f"源文件不存在：{path}")
    raw = path.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    if text.startswith("\ufeff"):
        text = text[1:]
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _strip_block(
    lines: List[str],
) -> tuple[str, Dict[str, int], Optional[int], Optional[str], Optional[str]]:
    """剥离元数据行 / --- 分隔线，返回 (正文, 命中计数, 声明字数, 时长, 源文件名)。"""
    hit = {key: 0 for key, _ in META_PREFIXES}
    declared: Optional[int] = None
    duration: Optional[str] = None
    src_file: Optional[str] = None
    kept: List[str] = []

    for line in lines:
        stripped = line.strip()
        matched = False
        for key, prefix in META_PREFIXES:
            if stripped.startswith(prefix):
                hit[key] += 1
                value = stripped[len(prefix):].strip()
                if key == "chars":
                    try:
                        declared = int(re.sub(r"[^0-9]", "", value) or 0)
                    except ValueError:
                        declared = None
                elif key == "duration":
                    duration = value or None
                else:
                    src_file = value or None
                matched = True
                break
        if matched:
            continue
        if SEP_RE.match(stripped):
            continue
        kept.append(line.rstrip())

    while kept and not kept[0].strip():
        kept.pop(0)
    while kept and not kept[-1].strip():
        kept.pop()

    return "\n".join(kept), hit, declared, duration, src_file


def compute_pit(video_key: str) -> str:
    """20260823_2151 -> 2026-08-23T21:51:00+08:00"""
    year, month, day = int(video_key[0:4]), int(video_key[4:6]), int(video_key[6:8])
    hour, minute = int(video_key[9:11]), int(video_key[11:13])
    return datetime(year, month, day, hour, minute, tzinfo=CST).isoformat()


def body_digest(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def parse_source(text: str) -> ParseResult:
    lines = text.split("\n")

    preamble: List[str] = []
    blocks: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None

    for line in lines:
        match = ANCHOR_RE.match(line)
        if match:
            if current is not None:
                blocks.append(current)
            current = {"key": match.group(1), "hm": match.group(2), "title": match.group(3), "lines": []}
            continue
        if current is None:
            preamble.append(line)
        else:
            current["lines"].append(line)
    if current is not None:
        blocks.append(current)

    meta_counts = {key: 0 for key, _ in META_PREFIXES}
    segments: List[Segment] = []
    separator_lines = 0

    for block in blocks:
        video_key = f"{block['key']}_{block['hm']}"
        block_lines: List[str] = block["lines"]
        separator_lines += sum(1 for ln in block_lines if SEP_RE.match(ln.strip()))

        body, hit, declared, duration, src_file = _strip_block(block_lines)
        for key in meta_counts:
            meta_counts[key] += hit[key]

        seg = Segment(
            video_key=video_key,
            title=block["title"].strip(),
            body=body,
            declared_chars=declared,
            duration=duration,
            source_file=src_file,
            found_meta=hit,
        )

        if not seg.title:
            seg.issues.append("标题为空")
        if not body.strip():
            seg.issues.append("正文为空")
        leftover_heads = [ln for ln in body.split("\n") if ln.startswith("## ")]
        if leftover_heads:
            seg.issues.append(f"正文含未识别标题行 {len(leftover_heads)} 行（锚点可能不匹配）")
        for key, label in META_LABELS.items():
            if hit[key] == 0:
                seg.issues.append(f"缺少元数据行：{label}")
            elif hit[key] > 1:
                seg.issues.append(f"元数据行重复 {hit[key]} 次：{label}")
        if declared is None:
            if hit["chars"]:
                seg.issues.append("字数无法解析")
        elif declared != len(body):
            seg.issues.append(f"字数字段 {declared} ≠ 正文长度 {len(body)}")

        try:
            seg.pit = compute_pit(video_key)
        except ValueError as exc:
            seg.issues.append(f"video_key 时间非法：{exc}")

        seg.sha256 = body_digest(body)
        segments.append(seg)

    seen: Dict[str, int] = {}
    for seg in segments:
        seen[seg.video_key] = seen.get(seg.video_key, 0) + 1
    for seg in segments:
        if seen[seg.video_key] > 1:
            seg.issues.append(f"video_key 重复 {seen[seg.video_key]} 次")

    blank_dropped = sum(1 for ln in lines if not ln.strip())
    return ParseResult(
        preamble=[ln for ln in preamble if ln.strip()],
        segments=segments,
        meta_counts=meta_counts,
        separator_lines=separator_lines,
        blank_lines_dropped=blank_dropped,
    )


# --------------------------------------------------------------------------- #
# 路径与安全护栏
# --------------------------------------------------------------------------- #
def project_dir(project: str, source_root: Path) -> Path:
    return source_root / project


def resolve_source_file(project: str, source_root: Path) -> Path:
    pdir = project_dir(project, source_root)
    src_dir = pdir / SOURCE_DIRNAME
    expected = src_dir / f"{project}.md"
    if expected.is_file():
        return expected
    if src_dir.is_dir():
        candidates = sorted(src_dir.glob("*.md"))
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            names = ", ".join(c.name for c in candidates)
            raise SplitError(f"source/ 下有多个 md，无法确定主源：{names}")
    raise SplitError(f"未找到源文件：{expected}")


def default_out_dir(project: str, source_root: Path) -> Path:
    return project_dir(project, source_root) / OUT_DIRNAME


def resolve_authority(out_dir: Path, project: str, source_root: Path) -> tuple[bool, str]:
    """判定本次产物是否为"权威产物"。

    只有同时满足两条才算权威，否则一律 is_authoritative=false（下游必须拒读）：
      1. 输出目录 == 默认识别根下的官方 source_split/；
      2. --source-root 就是默认识别根（没有指向测试副本树）。

    这条规则针对的真实事故：临时目录名长得像项目目录
    （_tmp_split_append/视频号学习_北斗股术1688/source_split），下游误把它当权威源。
    """
    official_out = default_out_dir(project, DEFAULT_SOURCE_ROOT).expanduser().resolve()
    official_root = DEFAULT_SOURCE_ROOT.expanduser().resolve()
    out_resolved = out_dir.expanduser().resolve()
    root_resolved = source_root.expanduser().resolve()

    if out_resolved != official_out:
        return False, f"输出目录不是官方路径（期望 {official_out}）"
    if root_resolved != official_root:
        return False, f"--source-root 不是默认识别根（{root_resolved}）"
    return True, "官方路径 + 默认识别根"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()



def guard_out_dir(out_dir: Path, project: str, source_root: Path, src_file: Path) -> Path:
    """拒绝任何可能覆盖 source/ 或项目根目录的输出路径。"""
    out_resolved = out_dir.expanduser().resolve()
    src_dir = (project_dir(project, source_root) / SOURCE_DIRNAME).resolve()
    pdir = project_dir(project, source_root).resolve()

    if out_resolved == src_dir or src_dir in out_resolved.parents:
        raise SplitError(f"拒绝写入：输出目录位于 source/ 内（{out_resolved}）")
    if out_resolved == pdir:
        raise SplitError(f"拒绝写入：输出目录等于项目根目录（{out_resolved}）")
    if out_resolved == src_file.resolve() or src_file.resolve().parent == out_resolved:
        raise SplitError(f"拒绝写入：输出目录与源文件同目录（{out_resolved}）")
    if out_resolved.exists() and not out_resolved.is_dir():
        raise SplitError(f"输出路径已存在且不是目录：{out_resolved}")
    return out_resolved


# --------------------------------------------------------------------------- #
# 渲染 / 落盘
# --------------------------------------------------------------------------- #
def render_doc(seg: Segment) -> str:
    if seg.body:
        return f"# {seg.title}\n\n{seg.body}\n"
    return f"# {seg.title}\n"


def build_manifest(
    project: str,
    segments: List[Segment],
    generated_at: datetime,
    source_path: Path,
    source_sha256: str,
    source_bytes: int,
    is_authoritative: bool,
) -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "project_id": project,
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "is_authoritative": is_authoritative,
        "source_path": str(source_path),
        "source_sha256": source_sha256,
        "source_bytes": source_bytes,
        "count": len(segments),
        "videos": [
            {
                "video_key": seg.video_key,
                "title": seg.title,
                "pit": seg.pit,
                "chars": seg.chars,
                "sha256": seg.sha256,
                "file": seg.filename,
            }
            for seg in segments
        ],
    }


def write_outputs(out_dir: Path, segments: List[Segment], manifest: Dict[str, Any]) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    for seg in segments:
        # newline="\n"：保证 Windows 下不写入 CRLF，两次运行字节一致
        with open(out_dir / seg.filename, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(render_doc(seg))

    manifest_path = out_dir / MANIFEST_NAME
    with open(manifest_path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    expected = {seg.filename for seg in segments} | {MANIFEST_NAME}
    existing = {p.name for p in out_dir.iterdir() if p.is_file()}
    stale = sorted(existing - expected)
    return {
        "out_dir": str(out_dir),
        "manifest_path": str(manifest_path),
        "written_files": len(segments) + 1,
        "stale_files_not_deleted": stale,
    }


# --------------------------------------------------------------------------- #
# 报告
# --------------------------------------------------------------------------- #
def summarize(
    project: str,
    src_file: Path,
    result: ParseResult,
    source_sha256: str,
    is_authoritative: bool,
    authority_reason: str,
) -> Dict[str, Any]:
    segments = result.segments
    issues = [{"video_key": seg.video_key, "issues": seg.issues} for seg in segments if seg.issues]
    return {
        "project_id": project,
        "source_file": str(src_file),
        "source_path": str(src_file.resolve()),
        "source_sha256": source_sha256,
        "source_bytes": src_file.stat().st_size if src_file.is_file() else 0,
        "is_authoritative": is_authoritative,
        "authority_reason": authority_reason,
        "count": len(segments),
        "total_chars": sum(seg.chars for seg in segments),
        "declared_total_chars": sum(seg.declared_chars or 0 for seg in segments),
        "meta_stripped": {
            "file": result.meta_counts["file"],
            "duration": result.meta_counts["duration"],
            "chars": result.meta_counts["chars"],
        },
        "separator_lines_stripped": result.separator_lines,
        "first_video_key": segments[0].video_key if segments else None,
        "last_video_key": segments[-1].video_key if segments else None,
        "issues": issues,
        "videos": [
            {
                "video_key": seg.video_key,
                "title": seg.title,
                "title_preview": seg.title_preview,
                "chars": seg.chars,
                "sha256": seg.sha256,
                "pit": seg.pit,
            }
            for seg in segments
        ],
    }


def print_dry_run(report: Dict[str, Any], out_dir: Path, write: bool) -> None:
    print("== split_source.py ==")
    print(f"project_id : {report['project_id']}")
    print(f"source     : {report['source_file']}  ({report['source_bytes']} bytes)")
    print(f"source sha : {report['source_sha256']}")
    print(f"out_dir    : {out_dir}  ({'已写入' if write else 'DRY-RUN，未落盘'})")
    print(f"mode       : {'WRITE' if write else 'DRY-RUN（加 --write 才落盘）'}")
    auth_flag = "true（权威产物）" if report["is_authoritative"] else f"false（{report['authority_reason']}）"
    print(f"is_authoritative: {auth_flag}")
    print()
    print(f"切出条数   : {report['count']}")
    print(f"正文总字数 : {report['total_chars']}（元数据字数字段合计 {report['declared_total_chars']}）")
    meta = report["meta_stripped"]
    print(f"元数据剥离 : 文件 {meta['file']} · 时长 {meta['duration']} · 字数 {meta['chars']}")
    print(f"分隔线剥离 : {report['separator_lines_stripped']}")
    print(f"首/末条    : {report['first_video_key']} → {report['last_video_key']}")
    print()

    if not report["videos"]:
        print("（无视频段）")
        return

    print(f"--- 逐条摘要（{len(report['videos'])} 条）---")
    width = max(len(v["video_key"]) for v in report["videos"])
    for index, video in enumerate(report["videos"], start=1):
        print(f"{index:>3}. {video['video_key']:<{width}}  {video['chars']:>6}字  {video['title_preview']}")

    if report["issues"]:
        print()
        print(f"--- 异常 {len(report['issues'])} 条 ---")
        for item in report["issues"]:
            print(f"  {item['video_key']}: {'；'.join(item['issues'])}")
    else:
        print()
        print("--- 异常 0 条 ---")

    summary = report_digest_summary(report["videos"])
    print(f"全量正文 sha256 合并摘要: {summary}")


def report_digest_summary(videos: List[Dict[str, Any]]) -> str:
    joined = "\n".join(f"{video['video_key']}:{video['sha256']}" for video in videos)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="把拼接式学习源大 md 按视频拆成一视频一文件（默认 dry-run）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：\n"
        "  python tools/kb_corpus/split_source.py --project 视频号学习_北斗股术1688\n"
        "  python tools/kb_corpus/split_source.py --project 视频号学习_北斗股术1688 --write\n",
    )
    parser.add_argument("--project", required=True, help="学习项目 id（= 05_学习源件 下的目录名）")
    parser.add_argument("--write", action="store_true", help="真正落盘到 source_split/（默认 dry-run）")
    parser.add_argument("--out", default=None, help=f"输出目录，默认 <项目目录>/{OUT_DIRNAME}/")
    parser.add_argument("--source-root", default=str(DEFAULT_SOURCE_ROOT), help="学习源件根目录")
    parser.add_argument("--json", action="store_true", dest="as_json", help="以 JSON 输出报告")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    source_root = Path(args.source_root)

    try:
        src_file = resolve_source_file(args.project, source_root)
        out_dir = guard_out_dir(
            Path(args.out) if args.out else default_out_dir(args.project, source_root),
            args.project,
            source_root,
            src_file,
        )
        text = read_source_text(src_file)
        source_sha256 = file_sha256(src_file)
        result = parse_source(text)

        if not result.segments:
            raise SplitError(f"未切出任何视频段，锚点正则 {ANCHOR_RE.pattern} 未命中：{src_file}")

        is_authoritative, authority_reason = resolve_authority(out_dir, args.project, source_root)
        report = summarize(
            args.project, src_file, result, source_sha256, is_authoritative, authority_reason
        )
        write_info: Dict[str, Any] = {}
        if args.write:
            generated_at = datetime.now(CST)
            manifest = build_manifest(
                args.project,
                result.segments,
                generated_at,
                src_file.resolve(),
                source_sha256,
                report["source_bytes"],
                is_authoritative,
            )
            write_info = write_outputs(out_dir, result.segments, manifest)
            write_info["generated_at"] = manifest["generated_at"]
            write_info["is_authoritative"] = is_authoritative
    except SplitError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    if args.as_json:
        payload = dict(report)
        payload["mode"] = "write" if args.write else "dry-run"
        payload["out_dir"] = str(out_dir)
        payload["write"] = write_info or None
        payload["digest_summary"] = report_digest_summary(report["videos"])
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print_dry_run(report, out_dir, args.write)
    if write_info:
        print()
        print("--- 落盘结果 ---")
        print(f"目录      : {write_info['out_dir']}")
        print(f"manifest  : {write_info['manifest_path']}")
        print(f"写文件数  : {write_info['written_files']}（{report['count']} 个视频文件 + 1 个 manifest）")
        print(f"generated_at: {write_info['generated_at']}")
        print(f"is_authoritative: {'true' if write_info['is_authoritative'] else 'false'}")
        if not write_info["is_authoritative"]:
            print(
                "[WARN] 非权威产物：manifest 已标记 is_authoritative=false，"
                "下游必须拒读，仅供测试/演练使用。"
            )
        if write_info["stale_files_not_deleted"]:
            print(f"遗留文件（未删除，需人工确认）: {', '.join(write_info['stale_files_not_deleted'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
