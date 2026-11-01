# -*- coding: utf-8 -*-
"""评测报告头工具：统一从「冻结评测集资产」生成报告头部（set_id/content_hash/annotation_policy）。

背景
----
wave14 已把 89 条冻结评测集资产化（`research/reports/eval_sets/eval_set_20261101_v2.json`，
sha256 内容寻址）。为避免各评测报告手写版本/哈希导致口径漂移，本工具提供统一入口：

    from eval_report_header import make_header, header_markdown
    header = make_header(2)          # dict：set_id/content_hash/annotation_policy…
    md = header_markdown(2)          # 报告头 markdown 两行

`make_header(set_version)` 会经 `eval_registry.load` 加载并**校验 hash**（默认开启），
返回内容可直接嵌入评测报告 JSON/MD，保证「报告 ↔ 评测集版本」可追溯。

CLI
---
C:\\项目\\py312\\python.exe -X utf8 tools/kb_corpus/eval_report_header.py header --version 2
C:\\项目\\py312\\python.exe -X utf8 tools/kb_corpus/eval_report_header.py header --version 2 --format markdown
C:\\项目\\py312\\python.exe -X utf8 tools/kb_corpus/eval_report_header.py list
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "kb_corpus"))

import eval_registry as er  # noqa: E402

OUT_DIR = er.OUT_DIR
SCHEMA_VERSION = "eval_report_header.v1"


def make_header(set_version: int = 2, out_dir: Path | str | None = None,
                verify: bool = True) -> dict:
    """返回评测报告头 dict（默认校验 content_hash）。

    字段：schema_version/set_id/set_version/n_queries/content_hash/frozen_at/
    annotation_policy/policy_ref/type_distribution/source/hash_verified。
    资产缺失/版本不存在/校验失败会抛异常（FileNotFoundError/ValueError），不做静默降级。
    """
    asset = er.load(version=int(set_version),
                    out_dir=Path(out_dir) if out_dir is not None else OUT_DIR,
                    verify_hash=bool(verify))
    policy = asset.get("annotation_policy") or {}
    return {
        "schema_version": SCHEMA_VERSION,
        "set_id": asset.get("set_id"),
        "set_version": asset.get("version"),
        "n_queries": asset.get("n_queries"),
        "content_hash": asset.get("content_hash"),
        "frozen_at": asset.get("frozen_at"),
        "annotation_policy": policy,
        "policy_ref": policy.get("policy_ref"),
        "type_distribution": asset.get("type_distribution"),
        "source": asset.get("source"),
        "hash_verified": bool(verify),
    }


def header_markdown(set_version: int = 2, out_dir: Path | str | None = None,
                    verify: bool = True) -> str:
    """报告头 markdown（两行，供评测报告顶部统一引用）。"""
    h = make_header(set_version, out_dir=out_dir, verify=verify)
    return "\n".join([
        f"- 评测集：`{h['set_id']}`（v{h['set_version']}，n={h['n_queries']}，"
        f"content_hash=`{h['content_hash']}`，hash_verified={h['hash_verified']}）",
        f"- 标注规范：{h['policy_ref']}（相关度分级 2/1/0；冻结后不可改，变更须新建版本）",
    ])


def header_json(set_version: int = 2, out_dir: Path | str | None = None,
                verify: bool = True, indent: int = 1) -> str:
    """报告头 JSON 字符串（供评测报告 JSON 的 meta.eval_set 字段嵌入）。"""
    return json.dumps(make_header(set_version, out_dir=out_dir, verify=verify),
                      ensure_ascii=False, indent=indent)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def cmd_header(args) -> int:
    if args.format == "markdown":
        print(header_markdown(args.version, out_dir=args.out_dir,
                              verify=not args.no_verify))
    else:
        print(header_json(args.version, out_dir=args.out_dir,
                          verify=not args.no_verify, indent=args.indent))
    return 0


def cmd_list(args) -> int:
    print(json.dumps(er.list_sets(args.out_dir), ensure_ascii=False, indent=1))
    return 0


def _parse_args(argv):
    p = argparse.ArgumentParser(prog="eval_report_header",
                                description="评测报告头（从冻结评测集资产生成 set_id/content_hash）")
    p.add_argument("--out-dir", default=str(OUT_DIR))
    sub = p.add_subparsers(dest="cmd", required=True)
    ph = sub.add_parser("header", help="生成报告头")
    ph.add_argument("--version", type=int, default=er.DEFAULT_VERSION)
    ph.add_argument("--format", choices=("json", "markdown"), default="json")
    ph.add_argument("--no-verify", action="store_true")
    ph.add_argument("--indent", type=int, default=1)
    ph.set_defaults(func=cmd_header)
    pl = sub.add_parser("list", help="列出可用评测集版本")
    pl.set_defaults(func=cmd_list)
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
