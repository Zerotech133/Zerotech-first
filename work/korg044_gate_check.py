# -*- coding: utf-8 -*-
"""K-ORG-044 冒烟：_integrity_gate 三 case 行为断言（子进程执行）。"""
import sys

sys.path.insert(0, "tools")
import agent_coder as ac  # noqa: E402

ORIG = "\n".join(["def f%d():\n    pass\n" % i for i in range(20)])  # ~220c·20 def

# T1 正常 whole 重写（长度相近·定义数不减）→ None
ok_body = ORIG + "\n# added comment line for the new feature implementation\n"
assert ac._integrity_gate(ok_body, ORIG.encode(), "x.py") is None, "T1 FAIL"

# T2 截水（0.3x）→ FAIL
stub = ORIG[:len(ORIG) * 3 // 10]
err = ac._integrity_gate(stub, ORIG.encode(), "x.py")
assert err and "截水" in err, "T2 FAIL: %r" % err

# T3 丢函数（破坏 def 关键字→顶层 def 数 20→10·长度比≈1.0 过长度门）→ FAIL
few = ORIG
for i in range(10, 20):
    few = few.replace("def f%d():" % i, "XEF f%d():" % i)
err = ac._integrity_gate(few, ORIG.encode(), "x.py")
assert err and "函数丢失" in err, "T3 FAIL: %r" % err

# T4 新文件（orig 空）→ None
assert ac._integrity_gate("print(1)", None, "x.py") is None, "T4 FAIL"
# T5 非 py 不查定义数（长度相近文本）→ None
assert ac._integrity_gate(ok_body, ORIG.encode(), "x.md") is None, "T5 FAIL"

# T6 还原缺口修复：run() 的 smoke 最后一轮 FAIL 分支（attempt>=smoke_retries）
# 必须含 _restore() 调用（AST 结构级验证·044 首跑实录残留缺陷）
import ast as _ast
src = open("tools/agent_coder.py", encoding="utf-8").read()
tree = _ast.parse(src)
run_fn = next(n for n in _ast.walk(tree)
              if isinstance(n, _ast.FunctionDef) and n.name == "run")
found = False
for node in _ast.walk(run_fn):
    if (isinstance(node, _ast.If) and
            "smoke_retries" in _ast.unparse(node.test)):
        calls = [n.func.id for n in _ast.walk(node)
                 if isinstance(n, _ast.Call)
                 and isinstance(n.func, _ast.Name)]
        if "_restore" in calls:
            found = True
            break
assert found, "T6 FAIL: 最后一轮 smoke FAIL 分支未见 _restore() 还原调用"

print("PASS: T1正常None/T2截水拦/T3丢函数拦/T4新文件None/T5非py跳过/T6还原缺口已修")
