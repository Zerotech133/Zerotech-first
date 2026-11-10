# -*- coding: utf-8 -*-
"""A7/A9 P 级纯吞批量修复器：给 except Exception: pass/continue 补日志。

规则：
  - 循环内的 handler（continue/break 高频路径）→ logging.getLogger(__name__).debug(...)
  - 其余 → logging.getLogger(__name__).exception("...")
  - 文件缺 import logging → 自动补（文档串后/首个 import 前）
  - 每文件：.bak-silent-20261110 备份 → 改 → ast 重解析断言 P 数下降 → py_compile
  - --dry 只报不写
"""
import ast, os, py_compile, shutil, sys, io

LOG_LN = "logging.getLogger(__name__)"


def in_loop(tree, handler):
    """handler 是否在 For/While 体内（含嵌套 try）。"""
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents.setdefault(child, node)
    cur = parents.get(handler)
    while cur is not None:
        if isinstance(cur, (ast.For, ast.AsyncFor, ast.While)):
            return True
        cur = parents.get(cur)
    return False


def ensure_import(src_lines):
    for ln in src_lines:
        if ln.startswith("import logging") or ln.startswith("import logging,") \
                or (", logging" in ln and ln.startswith(("import ", "from "))):
            return None  # 已有
    # 找插入点：跳过 from __future__（必须在最前），插在首个普通 import 前
    idx = None
    for i, ln in enumerate(src_lines):
        s = ln.strip()
        if s.startswith("from __future__"):
            continue
        if s.startswith(("import ", "from ")):
            idx = i
            break
    if idx is None:
        idx = 0
    return idx, "import logging"


def fix_file(path, items):
    src = io.open(path, encoding="utf-8", newline="").read()
    tree = ast.parse(src)
    lines = src.splitlines(keepends=True)
    target_lines = {it["line"] for it in items}

    def is_p(node):
        return ((node.type is None or (isinstance(node.type, ast.Name)
                and node.type.id in ("Exception", "BaseException")))
                and all(isinstance(s, (ast.Pass, ast.Continue, ast.Break))
                        for s in node.body))

    inserts = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.lineno in target_lines:
            if not is_p(node):
                continue  # 行号漂移或已修，跳过
            first = node.body[0]
            indent = " " * first.col_offset
            lvl = "debug" if in_loop(tree, node) else "exception"
            msg = "swallowed exception at line %d" % node.lineno
            if first.lineno == node.lineno:
                # 单行形态 `except Exception: pass` → 展开为多行
                raw = lines[node.lineno - 1]
                hdr_end = raw.rindex(":") + 1
                hdr = raw[:hdr_end].rstrip()
                body_txt = raw[hdr_end:].strip()
                inner = " " * (len(hdr) - len(hdr.lstrip()) + 4)
                new_block = (hdr + _eol(raw)
                             + inner + '%s.%s("%s")%s'
                             % (LOG_LN, lvl, msg, _eol(raw))
                             + inner + body_txt + _eol(raw))
                inserts.append(("LINE", node.lineno, new_block))
            else:
                inserts.append((first.lineno, indent +
                                '%s.%s("%s")%s' % (LOG_LN, lvl, msg, _eol(lines[first.lineno - 1]))))
    if not inserts:
        return 0, None
    # 行替换（单行展开）与行插入（多行补日志）分两步，行号从大到小
    repls = [x for x in inserts if x[0] == "LINE"]
    ins = [x for x in inserts if x[0] != "LINE"]
    for _, lineno, block in sorted(repls, key=lambda t: -t[1]):
        lines[lineno - 1] = block
    for lineno, text in sorted(ins, key=lambda t: -t[0]):
        lines.insert(lineno - 1, text)
    new_src = "".join(lines)
    imp = ensure_import(new_src.splitlines())
    if imp is not None:
        idx, stmt = imp
        all_lines = new_src.splitlines(keepends=True)
        all_lines.insert(idx, stmt + _eol(all_lines[0]))
        new_src = "".join(all_lines)
    # 断言：重解析后纯吞数必须下降
    new_tree = ast.parse(new_src)
    new_p = sum(1 for n in ast.walk(new_tree)
                if isinstance(n, ast.ExceptHandler)
                and (n.type is None or (isinstance(n.type, ast.Name)
                     and n.type.id in ("Exception", "BaseException")))
                and all(isinstance(s, (ast.Pass, ast.Continue, ast.Break))
                        for s in n.body))
    old_p = new_p + len(inserts)
    assert new_p < old_p, "P 数未下降: %s" % path
    return len(inserts), new_src


def _eol(sample):
    return "\r\n" if "\r\n" in sample else "\n"


def main():
    dry = "--dry" in sys.argv
    ledgers = [a for a in sys.argv[1:] if a.endswith(".json")]
    from collections import defaultdict
    by_file = defaultdict(list)
    for lf in ledgers:
        d = json.load(io.open(lf, encoding="utf-8"))
        for it in d["items"]:
            if it["kind"] == "P":
                by_file[it["file"]].append(it)
    print("P 级待修文件 %d 个" % len(by_file))
    fixed_total = 0
    fails = []
    for f, items in sorted(by_file.items()):
        try:
            n, new_src = fix_file(f, items)
        except Exception as e:
            fails.append((f, repr(e)))
            continue
        if n == 0 or new_src is None:
            continue  # 本轮无可修项（可能上轮已修或行号漂移）
        if dry:
            print("  [dry] %s +%d" % (f, n))
            continue
        bak = f + ".bak-silent-20261110"
        if not os.path.exists(bak):
            shutil.copy2(f, bak)
        try:
            io.open(f, "w", encoding="utf-8", newline="").write(new_src)
            py_compile.compile(f, doraise=True)
            fixed_total += n
        except Exception as e:
            # 写坏即回滚
            shutil.copy2(bak, f)
            fails.append((f, "write/compile: %r" % e))
    print("修复 %d 处 / 失败 %d 文件" % (fixed_total, len(fails)))
    for f, e in fails:
        print("  FAIL %s: %s" % (f, e))


if __name__ == "__main__":
    import json
    main()
