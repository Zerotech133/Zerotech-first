# -*- coding: utf-8 -*-
"""JSON 写侧前置校验工具（M3-P5；tasks.json 损坏事故判例落地）。

背景（2026-10-17 判例）：devtask 写入方把 note 字段里的未转义引号
直接 json.dumps 前拼进字符串，tasks.json loads 失败、239 checks 断供
——事后锚点机械转义才救回。本工具把「写前必 loads」从纪律变成
一行命令：写入方保存真源前先跑本工具，loads 失败即退出码 1，
门禁/CI/巡检可直接拦截。

用法：
    python tools/validate_json.py <file.json> [more.json ...]
    python tools/validate_json.py --stdin        # 管道喂内容（写入方自检）
    python tools/validate_json.py --stdin --jsonl  # 管道内容按 JSONL 逐行
    python tools/validate_json.py -r <dir>       # 递归校验目录下 *.json

.jsonl 文件自动按行独立校验（devtask 真源 tasks_YYYYMM.jsonl 主格式；
空行容忍，报错带行号列号）。

输出：每文件一行 PASS/FAIL；FAIL 给出精确行列 + 错误点前后文
（json.JSONDecodeError.pos 定位，±40 字符窗口），复制即可肉眼定位
未转义引号 / 尾逗号 / NaN 等典型损坏。

退出码：0=全部 PASS；1=存在 FAIL；2=参数/文件缺失。
只读工具：不修改任何文件（修复属写入方职责，工具不代写真源）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_WINDOW = 40  # 错误点前后文窗口字符数


def _snippet(text: str, pos: int) -> str:
    lo = max(0, pos - _WINDOW)
    hi = min(len(text), pos + _WINDOW)
    return ("…%s[HERE→]%s…" % (text[lo:pos], text[pos:hi])) \
        if hi > pos else ("…%s[EOF]" % text[lo:])


def validate_file(path: Path) -> bool:
    """校验单个文件；.jsonl 按行独立 loads（devtask 真源主格式）。"""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        print("FAIL %s · 不可读：%s" % (path, exc))
        return False
    if path.suffix.lower() == ".jsonl":
        return _validate_jsonl(raw, str(path))
    return _validate_text(raw, str(path))


def _validate_jsonl(raw: str, label: str) -> bool:
    ok = True
    for lineno, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue  # 空行容忍（append-only 账本尾行惯例）
        try:
            json.loads(line)
        except json.JSONDecodeError as exc:
            print("FAIL %s · 第 %d 行 列 %s：%s" % (
                label, lineno, exc.colno, exc.msg))
            print("     上下文：%s" % _snippet(line, exc.pos))
            ok = False
        except ValueError as exc:
            print("FAIL %s · 第 %d 行：%s" % (label, lineno, exc))
            ok = False
    if ok:
        print("PASS %s · %d 行" % (label, len(
            [l for l in raw.splitlines() if l.strip()])))
    return ok


def _validate_text(raw: str, label: str) -> bool:
    try:
        json.loads(raw)
    except json.JSONDecodeError as exc:
        print("FAIL %s · 行 %s 列 %s：%s" % (
            label, exc.lineno, exc.colno, exc.msg))
        print("     上下文：%s" % _snippet(raw, exc.pos))
        return False
    except ValueError as exc:  # NaN/Infinity 等 loads 阶段异常
        print("FAIL %s · %s" % (label, exc))
        return False
    print("PASS %s" % label)
    return True


def _iter_json_files(root: Path):
    for p in sorted(root.rglob("*.json")):
        if "__pycache__" in p.parts or p.name.endswith(".pyc"):
            continue
        yield p


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="JSON 写侧前置校验（只读；loads 失败=退出码 1）")
    ap.add_argument("files", nargs="*", help="待校验的 .json 文件")
    ap.add_argument("-r", "--recursive", metavar="DIR",
                    help="递归校验目录下全部 *.json")
    ap.add_argument("--stdin", action="store_true",
                    help="从 stdin 读内容校验（写入方保存前自检管道）")
    ap.add_argument("--jsonl", action="store_true",
                    help="stdin/无后缀文件按 JSONL 逐行校验（.jsonl 后缀自动识别）")
    args = ap.parse_args(argv)

    ok = True
    checked = 0
    if args.stdin:
        raw = sys.stdin.read()
        if args.jsonl:
            ok = _validate_jsonl(raw, "<stdin>")
        else:
            ok = _validate_text(raw, "<stdin>")
        checked = 1
    if args.recursive:
        root = Path(args.recursive)
        if not root.is_dir():
            print("FAIL 目录不存在：%s" % root)
            return 2
        for p in _iter_json_files(root):
            checked += 1
            ok = validate_file(p) and ok
    for f in args.files:
        p = Path(f)
        if not p.is_file():
            print("FAIL %s · 文件不存在" % p)
            ok = False
            checked += 1
            continue
        checked += 1
        ok = validate_file(p) and ok
    if checked == 0:
        ap.print_help()
        return 2
    print("----\n%d 个文件校验完毕：%s" % (
        checked, "全部 PASS" if ok else "存在 FAIL（写侧必须先修再落盘）"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
