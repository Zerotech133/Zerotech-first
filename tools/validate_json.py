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
    python tools/validate_json.py --profile tasks .worktrees/tasks.json
    python tools/validate_json.py --profile tasks --strict .worktrees/tasks.json

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
import re
import sys
from pathlib import Path

_WINDOW = 40  # 错误点前后文窗口字符数


def _snippet(text: str, pos: int) -> str:
    lo = max(0, pos - _WINDOW)
    hi = min(len(text), pos + _WINDOW)
    return ("…%s[HERE→]%s…" % (text[lo:pos], text[pos:hi])) \
        if hi > pos else ("…%s[EOF]" % text[lo:])


def validate_file(path: Path, profile: str | None = None,
                  strict: bool = False) -> bool:
    """校验单个文件；.jsonl 按行独立 loads（devtask 真源主格式）。
    profile="shadow"：语法 PASS 后再做影子账本行级语义校验（M5 刀 1）。
    profile="tasks"：devtask 真源 tasks.json 文档级语义校验（ERROR 阻断 /
    WARN 降级放行，见 _validate_tasks_doc）。"""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        print("FAIL %s · 不可读：%s" % (path, exc))
        return False
    if path.suffix.lower() == ".jsonl":
        return _validate_jsonl(raw, str(path), profile=profile)
    return _validate_text(raw, str(path), profile=profile, strict=strict)


def _validate_shadow_row(obj, lineno: int, label: str) -> bool:
    """影子账本行级语义规则（M5 刀 1）。两代并存：仅 schema_version=="m5.v1"
    新行做语义校验；无标记=v0 旧行冻结不重写（K-ORG-129），跳过语义只做语法。"""
    errs = []
    if obj.get("schema_version") is None:
        return True  # v0 旧行：不重写原则，语义豁免
    if obj.get("schema_version") != "m5.v1":
        errs.append("未知 schema_version: %r（合法值：m5.v1）" % (obj.get("schema_version"),))
    po = obj.get("parse_ok")
    if obj.get("status") == "done" and po is None:
        errs.append("done 行缺 parse_ok（m5.v1 起必填）")
    if po is True and not obj.get("recommendation"):
        errs.append("parse_ok=true 但 recommendation 空（一致性破坏）")
    if po is False and any(obj.get(k) is not None
                           for k in ("recommendation", "confidence", "score")):
        errs.append("parse_ok=false 但 recommendation/confidence/score 非空"
                    "（解析失败行不得残留决策字段）")
    if "degraded" not in obj:
        errs.append("m5.v1 行缺 degraded（扩义=数据源异常∪解析失败）")
    for e in errs:
        print("FAIL %s · 第 %d 行 语义：%s" % (label, lineno, e))
    return not errs


def _validate_jsonl(raw: str, label: str, profile: str | None = None) -> bool:
    ok = True
    for lineno, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue  # 空行容忍（append-only 账本尾行惯例）
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            print("FAIL %s · 第 %d 行 列 %s：%s" % (
                label, lineno, exc.colno, exc.msg))
            print("     上下文：%s" % _snippet(line, exc.pos))
            ok = False
        except ValueError as exc:
            print("FAIL %s · 第 %d 行：%s" % (label, lineno, exc))
            ok = False
        else:
            if profile == "shadow" and isinstance(obj, dict):
                ok = _validate_shadow_row(obj, lineno, label) and ok
    if ok:
        print("PASS %s · %d 行%s" % (label, len(
            [l for l in raw.splitlines() if l.strip()]),
            "（profile=shadow）" if profile == "shadow" else ""))
    return ok


# ----------------------------------------------------------------------
# profile=tasks：devtask 真源 tasks.json 文档级语义校验
# ----------------------------------------------------------------------
# 分级设计（M6 观察季，「降级」的准确含义）：
#   ERROR —— **结构性**损坏，写侧必须拒写：顶层非 dict / 值非 dict /
#            status 缺失或不在枚举 / created 缺失。
#   WARN  —— **内容不精确**但合法存在，只记录不阻断。典型：
#            · 空壳完工单（done 但 desc/branch/owner 全空）——推送域
#              直接落生产文件的工作本就没有 branch（见 devtask_wiring
#              模块 docstring 对 K-PUSH-005~008 的说明），判 ERROR 属
#              范畴错误，必须降级；
#            · 工单 ID 形态可疑（以 "-" 开头 = CLI 参数被当工单 ID）；
#            · created/merged 不可解析——真源存在手写近似时间
#              （K-ORG-156~159 的 "2026-10-17 20:1x"，同批会话未记准
#              分钟用 x 占位）。它不破坏状态机，只是不可排序，故告警。
# 默认 WARN 不影响退出码（降级放行）；--strict 时 WARN 一并计入失败，
# 供「清理周」批量排查用。

TASK_STATUSES = ("doing", "review", "done", "aborted")
# 时间戳三代并存：早期只有日期 / 手写 HH:MM / cmd_merge 写 "%Y-%m-%d %H:%M:%S"
_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}( \d{2}:\d{2}(:\d{2})?)?$")
_DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def check_tasks_doc(obj) -> tuple[list, list]:
    """tasks.json 语义校验**纯函数**（无 IO、无 print）——写侧复用入口。

    返回 (errors, warns)：errors 非空=结构性损坏须拒写；warns 只记录。
    分级理由见上方 TASK_STATUSES 处的注释块。
    """
    errors, warns = [], []
    if not isinstance(obj, dict):
        return (["顶层必须是 dict（键=工单 ID），实际 %s" % type(obj).__name__], [])
    for tid, t in obj.items():
        if not isinstance(t, dict):
            errors.append("%r 的值不是 dict（实际 %s）——读侧会静默丢弃该工单"
                          % (tid, type(t).__name__))
            continue
        if not isinstance(tid, str):
            errors.append("工单 ID 非字符串：%r" % (tid,))
        elif tid.startswith("-"):
            warns.append("%r ID 以 '-' 开头——疑似 CLI 参数被当工单 ID 落入真源"
                         % (tid,))
        status = t.get("status")
        if status is None:
            errors.append("%s 缺 status" % tid)
        elif status not in TASK_STATUSES:
            errors.append("%s status=%r 不在枚举 %s"
                          % (tid, status, "/".join(TASK_STATUSES)))
        created = t.get("created")
        if created is None:
            errors.append("%s 缺 created" % tid)
        elif not (isinstance(created, str) and _TS_RE.match(created)):
            warns.append("%s created=%r 不可解析（须 YYYY-MM-DD[ HH:MM[:SS]]）"
                         "——手写近似时间，状态机不依赖但不可排序"
                         % (tid, created))
        merged = t.get("merged")
        # merged 是「时间戳 + 可选说明」的自由文本（如
        # "2026-10-13 10:05 (QA1四批质检完毕:19单全出结论)"），
        # 只要以日期开头即可——空串才是真异常（说明写了字段却没给时间）。
        if merged is not None and not (isinstance(merged, str)
                                       and _DATE_PREFIX_RE.match(merged)):
            warns.append("%s merged=%r 不可识别（须以 YYYY-MM-DD 开头，"
                         "允许后接时间与说明）" % (tid, merged))
        if status == "done" and not any(t.get(k) for k in ("desc", "branch", "owner")):
            warns.append("%s 空壳完工单（done 但 desc/branch/owner 全空）——"
                         "需人工确认非洗白" % tid)
    return errors, warns


def _validate_tasks_doc(obj, label: str, strict: bool = False) -> bool:
    """tasks.json 语义校验。返回 True=通过（无 ERROR，且 strict 时无 WARN）。"""
    errors, warns = check_tasks_doc(obj)
    for e in errors:
        print("FAIL %s · 语义：%s" % (label, e))
    for w in warns:
        print("%s %s · 语义：%s" % ("FAIL" if strict else "WARN", label, w))
    if errors or (strict and warns):
        return False
    print("PASS %s · %d 工单（profile=tasks%s）"
          % (label, len(obj), "" if not warns else
             "，%d 条 WARN 降级放行" % len(warns)))
    return True


def _validate_text(raw: str, label: str, profile: str | None = None,
                   strict: bool = False) -> bool:
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        print("FAIL %s · 行 %s 列 %s：%s" % (
            label, exc.lineno, exc.colno, exc.msg))
        print("     上下文：%s" % _snippet(raw, exc.pos))
        return False
    except ValueError as exc:  # NaN/Infinity 等 loads 阶段异常
        print("FAIL %s · %s" % (label, exc))
        return False
    if profile == "tasks":
        return _validate_tasks_doc(obj, label, strict=strict)
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
    ap.add_argument("--profile", choices=["shadow", "tasks"],
                    help="语义校验档案：shadow=影子账本（仅 m5.v1 新行，"
                         "v0 旧行语义豁免——不重写原则）；tasks=devtask 真源"
                         " tasks.json（ERROR 阻断 / WARN 降级放行）")
    ap.add_argument("--strict", action="store_true",
                    help="profile=tasks 时把 WARN 也计入失败（清理周批量排查用）")
    args = ap.parse_args(argv)

    ok = True
    checked = 0
    if args.stdin:
        raw = sys.stdin.read()
        if args.jsonl:
            ok = _validate_jsonl(raw, "<stdin>", profile=args.profile)
        else:
            ok = _validate_text(raw, "<stdin>", profile=args.profile,
                                strict=args.strict)
        checked = 1
    if args.recursive:
        root = Path(args.recursive)
        if not root.is_dir():
            print("FAIL 目录不存在：%s" % root)
            return 2
        for p in _iter_json_files(root):
            checked += 1
            ok = validate_file(p, profile=args.profile, strict=args.strict) and ok
    for f in args.files:
        p = Path(f)
        if not p.is_file():
            print("FAIL %s · 文件不存在" % p)
            ok = False
            checked += 1
            continue
        checked += 1
        ok = validate_file(p, profile=args.profile, strict=args.strict) and ok
    if checked == 0:
        ap.print_help()
        return 2
    print("----\n%d 个文件校验完毕：%s" % (
        checked, "全部 PASS" if ok else "存在 FAIL（写侧必须先修再落盘）"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
