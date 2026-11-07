#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""跨平台静态门禁（Windows 开发 / Linux 生产）。

为什么存在
----------
K-RELEASE-20261106 验收实证：本机 Windows 全绿的用例，到 CentOS 7 生产机上
成批崩。根因不是个别 BUG，而是「提交前没有任何门禁模拟 Linux」：

  * tools/devtask.py 无条件 ``import msvcrt``            -> Linux ModuleNotFoundError
  * portal/portal_api.py 无条件传 ``creationflags=``     -> Linux ValueError, /qd 502
  * agent_platform/tests/test_ide_worktree.py ``git -C`` -> git 1.8.3.1 不认，静默非 0
  * agent_platform/legion_command/openhands_engine.py 常量定义顺序 -> import 期 NameError

前两条用正则能扫到；第三条写成 ``["git", "-C", root]`` 列表形式时，正则扫描
漏掉了，导致上一轮错误地得出「生产代码没有 git -C」的结论。因此本门禁一律
走 AST，不靠文本匹配。

设计
----
* 按 **增量基线** 判定：存量已登记缺陷不阻塞开发，但**任何新增**一律 exit 1。
  避免「一次扫出 1500 处 → 全员停摆」，同时立刻冻结新增。
* BLOCK = 在 Linux 上必然炸的写法（Windows-only 顶层导入、无保护的
  creationflags、git -C、os.startfile）；WARN = 盘符绝对路径（多为可覆盖默认值）。
* ``--tgz`` 可直接扫最终发布包 —— 部署面才是真正需要治理的面，全仓 2000+ 处
  里绝大多数是服务器从不执行的本机运维脚本，一刀切全修既浪费又制造回归。

用法
----
  python tools/check_crossplatform.py                       # 增量门禁（CI / 预提交）
  python tools/check_crossplatform.py --no-baseline        # 全量体检
  python tools/check_crossplatform.py --tgz <pkg.tgz>      # 扫发布包部署面
  python tools/check_crossplatform.py --refresh-baseline --owner DATA1
  python tools/check_crossplatform.py --json               # 机器可读

退出码：0 = 无新增 BLOCK；1 = 有新增 BLOCK（或 --strict 下有 WARN）；2 = 用法错误。
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import os
import re
import sys
import tarfile
from datetime import date
from pathlib import Path

WIN_ONLY_MODULES = {
    "msvcrt", "_msvcrt", "winreg", "_winreg", "winsound", "msvcrt",
    "win32api", "win32com", "win32con", "win32file", "win32process",
    "pythoncom", "pywintypes", "win32security", "win32evtlog", "ntsecuritycon",
}

RULES = {
    "R1_win_only_import": "BLOCK",
    "R2_creationflags": "BLOCK",
    "R3_git_dash_C": "BLOCK",
    "R4_os_startfile": "BLOCK",
    "R5_drive_letter_path": "WARN",
}

RULE_DESC = {
    "R1_win_only_import": "Windows 专用模块导入，Linux 无此模块",
    "R2_creationflags": "subprocess.Popen 的 creationflags 仅 Windows 支持",
    "R3_git_dash_C": "git -C 在服务器 git 1.8.3.1 不存在（且不报错，只打 usage）",
    "R4_os_startfile": "os.startfile 仅 Windows 存在",
    "R5_drive_letter_path": "盘符绝对路径字面量（应走 env/配置覆盖）",
}

EXCLUDE_DIRS = {
    ".git", "__pycache__", "node_modules", ".models", ".venv_czsc", ".venv",
    "pylibs", "deliverables", "research", "_archive", ".worktrees", "site-packages",
}

DEFAULT_ROOTS = (
    "portal", "agent_platform", "tools", "quant_backtest", "k_stock",
    "common", "data_sources", "news_collector", "ops", "config",
)

DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")


def _const_str(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _is_nt_test(test: ast.AST) -> bool:
    """识别 if os.name == 'nt' / sys.platform == 'win32' / platform.system() == 'Windows'。"""
    if not isinstance(test, (ast.Compare, ast.UnaryOp, ast.BoolOp, ast.Call)):
        return False
    # 直接比较
    if isinstance(test, ast.Compare):
        for op, right in zip(test.ops, test.comparators):
            if not isinstance(op, (ast.Eq, ast.Is)):
                continue
            val = _const_str(right) or _const_str(test.left)
            if val in ("nt", "win32", "Windows"):
                return True
    # platform.system() == "Windows" 已被上面覆盖；os.name in ("nt",) 之类
    for n in ast.walk(test):
        v = _const_str(n)
        if v in ("nt", "win32", "Windows"):
            return True
    return False


def _parent_map(tree):
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def _is_guarded(node, parents) -> bool:
    """节点是否位于 nt 条件分支的 body 内（orelse 不算）。"""
    cur = node
    while True:
        parent = parents.get(cur)
        if parent is None:
            return False
        if isinstance(parent, ast.If):
            try:
                in_body = any(cur is b for b in parent.body)
            except TypeError:
                in_body = False
            if in_body and _is_nt_test(parent.test):
                return True
        # 不因遇到 FunctionDef 就中断：按平台定义不同实现
        # （if os.name == 'nt': def f(): ... else: def f(): ...）是合法写法，
        # 继续向上传才能看到外层 guard。
        cur = parent


def _in_function(node, parents) -> bool:
    cur = node
    while True:
        parent = parents.get(cur)
        if parent is None:
            return False
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return True
        cur = parent


def _fingerprint(path, rule, code, occurrence=0):
    """不含行号（插入代码不导致基线漂移），但带出现序号（重复代码行不互相掩盖）。"""
    raw = "%s|%s|%s|#%d" % (path, rule, code.strip(), occurrence)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def scan_source(path: str, src: str):
    """返回 findings 列表。"""
    try:
        tree = ast.parse(src, filename=path)
    except SyntaxError as exc:
        return [{"path": path, "line": exc.lineno or 0, "rule": "R0_syntax",
                 "level": "BLOCK", "code": "SyntaxError: %s" % exc.msg,
                 "fp": _fingerprint(path, "R0_syntax", str(exc))}]
    parents = _parent_map(tree)
    lines = src.splitlines()
    out = []
    seen = {}

    def add(node, rule, level, code=None):
        ln = getattr(node, "lineno", 0)
        text = lines[ln - 1].strip() if 0 < ln <= len(lines) else (code or "")
        key = (rule, text)
        occurrence = seen.get(key, 0)
        seen[key] = occurrence + 1
        out.append({
            "path": path, "line": ln, "rule": rule, "level": level,
            "code": text[:160], "fp": _fingerprint(path, rule, text, occurrence),
        })

    # R1: Windows-only import（未受 nt 保护）
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module.split(".")[0]]
        if any(n in WIN_ONLY_MODULES for n in names):
            if not _is_guarded(node, parents):
                add(node, "R1_win_only_import", "BLOCK")

    # R2 / R3 / R4: 调用点
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        guarded = _is_guarded(node, parents)

        for kw in node.keywords:
            if kw.arg == "creationflags" and not guarded:
                add(node, "R2_creationflags", "BLOCK")
                break

        # git -C：列表/元组形式 ["git", "-C", ...]
        first = node.args[0] if node.args else None
        if isinstance(first, (ast.List, ast.Tuple)) and len(first.elts) >= 2:
            a, b = _const_str(first.elts[0]), _const_str(first.elts[1])
            if a and a.rstrip(".exe") == "git" and b == "-C" and not guarded:
                add(node, "R3_git_dash_C", "BLOCK")
        # git -C：shell 字符串形式
        if first is not None:
            s = _const_str(first)
            if s and re.search(r"\bgit\s+-C\s", s) and not guarded:
                add(node, "R3_git_dash_C", "BLOCK")

        fn = node.func
        if (isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name)
                and fn.value.id == "os" and fn.attr == "startfile" and not guarded):
            add(node, "R4_os_startfile", "BLOCK")

    # R5: 盘符路径字面量（WARN；函数内默认值同样提示）
    for node in ast.walk(tree):
        s = _const_str(node)
        if s and DRIVE_RE.match(s) and not _is_guarded(node, parents):
            add(node, "R5_drive_letter_path", "WARN")
    return out


def iter_sources(root: Path, tgz: str | None, excludes):
    if tgz:
        with tarfile.open(tgz, "r:gz") as tf:
            for m in tf.getmembers():
                if not m.isfile() or not m.name.endswith(".py"):
                    continue
                name = m.name.lstrip("./")
                if any(name.startswith(x) or ("/" + x + "/") in "/" + name
                       for x in excludes):
                    continue
                f = tf.extractfile(m)
                if f is None:
                    continue
                yield name, f.read().decode("utf-8", "ignore")
        return
    bases = [root / d for d in DEFAULT_ROOTS if (root / d).is_dir()]
    if not bases:
        bases = [root]
    for base in bases:
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS
                           and d not in excludes]
            for fn in filenames:
                if not fn.endswith(".py"):
                    continue
                p = Path(dirpath) / fn
                rel = p.relative_to(root).as_posix()
                try:
                    yield rel, p.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue


def baseline_path(root: Path) -> Path:
    return root / "tools" / "crossplatform_baseline.json"


def load_baseline(root: Path):
    p = baseline_path(root)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("entries", {})
    except (OSError, ValueError):
        return {}


def main(argv=None):
    ap = argparse.ArgumentParser(description="跨平台静态门禁（基线增量拦截）")
    ap.add_argument("--root", default=".", help="仓库根（默认当前目录）")
    ap.add_argument("--tgz", help="改为扫描最终发布包 .tgz（部署面）")
    ap.add_argument("--no-baseline", action="store_true", help="全量体检，忽略基线")
    ap.add_argument("--strict", action="store_true", help="WARN 也计入失败")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument("--refresh-baseline", action="store_true",
                    help="把当前全量 findings 写入基线（冻结存量）")
    ap.add_argument("--owner", default="unassigned", help="基线登记责任人")
    ap.add_argument("--exclude", action="append", default=[],
                    help="额外排除的目录名（可重复）")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()
    excludes = set(EXCLUDE_DIRS) | set(args.exclude)
    findings = []
    nfiles = 0
    for path, src in iter_sources(root, args.tgz, excludes):
        nfiles += 1
        findings.extend(scan_source(path, src))

    blocks = [f for f in findings if f["level"] == "BLOCK"]
    warns = [f for f in findings if f["level"] == "WARN"]
    baseline = load_baseline(root)
    known_fps = set(baseline)

    if args.refresh_baseline:
        entries = {}
        for f in blocks + warns:
            entries[f["fp"]] = {
                "path": f["path"], "rule": f["rule"], "level": f["level"],
                "code": f["code"], "owner": args.owner,
                "registered_at": date.today().isoformat(),
                "reason": "存量冻结，待分批清理；新增一律拦截",
            }
        p = baseline_path(root)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({
            "version": 1,
            "generated_at": date.today().isoformat(),
            "note": "增量门禁基线：已登记项不阻塞，未登记的新增项 exit 1。"
                    "清理存量后请同步删除对应条目并跑 --refresh-baseline 复核。",
            "entries": entries,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print("[baseline] 已写入 %d 条 -> %s" % (len(entries), p))
        return 0

    if args.no_baseline:
        new_blocks = blocks
    else:
        new_blocks = [f for f in blocks if f["fp"] not in known_fps]
    known = [f for f in blocks if f["fp"] in known_fps]
    new_warns = [f for f in warns if f["fp"] not in known_fps] if not args.no_baseline else warns

    if args.json:
        print(json.dumps({
            "files_scanned": nfiles,
            "block_total": len(blocks), "block_known": len(known),
            "block_new": len(new_blocks),
            "warn_total": len(warns), "warn_new": len(new_warns),
            "new_blocks": new_blocks,
        }, ensure_ascii=False, indent=2))
    else:
        src_desc = args.tgz or str(root)
        print("[xplatform] 扫描面=%s  .py=%d" % (src_desc, nfiles))
        print("[xplatform] BLOCK 合计=%d（基线内 %d / 新增 %d）  WARN=%d"
              % (len(blocks), len(known), len(new_blocks), len(warns)))
        for f in new_blocks[:30]:
            print("  NEW  %-28s %s:%d  %s"
                  % (f["rule"], f["path"], f["line"], f["code"][:90]))
        if len(new_blocks) > 30:
            print("  ... 另有 %d 条，用 --json 查看全量" % (len(new_blocks) - 30))
        if not new_blocks:
            print("[xplatform] OK：无新增跨平台阻断项")
        else:
            print("[xplatform] FAIL：新增 %d 条跨平台阻断项，禁止提交/发布"
                  % len(new_blocks))
            print("  规则说明：")
            for r, d in RULE_DESC.items():
                print("    %-24s %s" % (r, d))

    if new_blocks:
        return 1
    if args.strict and new_warns:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
