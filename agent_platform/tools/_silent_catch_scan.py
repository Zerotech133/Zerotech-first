# -*- coding: utf-8 -*-
"""A7/A9 吞错扫描器：精确分类一方代码的 except 块，产出处置台账。

分类规则（AST 级，不是正则猜）：
  R  rollback+raise / log+raise  → 豁免（正确模式）
  L  块内已有 logging            → 豁免（已可见）
  P  pass/continue 纯吞         → 必修（P 级，补 logging.exception）
  V  赋默认值 continue（尽力而为）→ 判级处理（L1 补 debug 日志 / L2+L3 批量）
用法：
  python _silent_catch_scan.py agent_platform --out _a7_ledger.json
  python _silent_catch_scan.py CowAgent --exclude venv --out _a9_ledger.json
"""
import ast, json, os, sys, io


def classify_block(node):
    """对 except handler 分类，返回 (kind, detail)。"""
    body = node.body
    names = []
    only_silent = True  # 全部语句均为 pass/continue/break 才算纯吞
    for st in body:
        if isinstance(st, ast.Raise):
            return ("R", "raise")
        if isinstance(st, (ast.Pass, ast.Continue, ast.Break)):
            names.append(type(st).__name__.lower())
        else:
            only_silent = False
    # 检查块内是否已有日志调用
    has_log = False
    for st in ast.walk(ast.Module(body=body, type_ignores=[])):
        if isinstance(st, ast.Call):
            f = st.func
            nm = ""
            if isinstance(f, ast.Attribute):
                nm = f.attr
            elif isinstance(f, ast.Name):
                nm = f.id
            if nm in ("exception", "error", "warning", "warn", "info", "debug",
                      "critical", "print", "log"):
                has_log = True
    if has_log:
        return ("L", "has-logging")
    if names and only_silent:
        return ("P", "+".join(names))
    # 其余：赋默认值/错误标记等软处理
    return ("V", "soft-default")


def scan_root(root, excludes=()):
    ledger = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in ("__pycache__",) and
                       not any(x in os.path.join(dirpath, d).replace("\\", "/") for x in excludes)]
        for fn in filenames:
            if not fn.endswith(".py") or "test" in fn.lower():
                continue
            p = os.path.join(dirpath, fn)
            try:
                src = io.open(p, encoding="utf-8", errors="replace").read()
                tree = ast.parse(src)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ExceptHandler):
                    # 宽捕获 = Exception/BaseException/裸
                    wide = (node.type is None or
                            (isinstance(node.type, ast.Name) and node.type.id in
                             ("Exception", "BaseException")))
                    if not wide:
                        continue
                    kind, detail = classify_block(node)
                    ledger.append({
                        "file": p.replace("\\", "/"),
                        "line": node.lineno,
                        "kind": kind, "detail": detail,
                    })
    return ledger


def main():
    root = sys.argv[1]
    out = "ledger.json"
    excludes = []
    args = sys.argv[2:]
    i = 0
    while i < len(args):
        if args[i] == "--out":
            out = args[i + 1]; i += 2
        elif args[i] == "--exclude":
            excludes.append(args[i + 1]); i += 2
        else:
            i += 1
    led = scan_root(root, excludes)
    from collections import Counter
    kinds = Counter(x["kind"] for x in led)
    json.dump({"root": root, "excludes": excludes, "items": led},
              io.open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("总计 %d 处宽捕获：" % len(led), dict(kinds))
    hot = Counter(x["file"] for x in led if x["kind"] == "P")
    print("\nP 级（纯吞，必修）热点：")
    for f, c in hot.most_common(15):
        print("  %4d  %s" % (c, f))
    print("台账 ->", out)


if __name__ == "__main__":
    main()
