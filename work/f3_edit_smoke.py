# -*- coding: utf-8 -*-
"""K-ORG-036 F3 冒烟：编辑协议 + lint 门（纯单元级，不调模型）。"""
import os
import shutil
import sys

sys.path.insert(0, "tools")
import agent_coder as ac  # noqa: E402

TMP = os.path.join("shadow", "coder_f3")
shutil.rmtree(TMP, ignore_errors=True)
os.makedirs(TMP, exist_ok=True)
target = os.path.join(TMP, "target.py")
with open(target, "w", encoding="utf-8") as f:
    f.write("def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n")

# 1) sr 块唯一命中 → 块编辑
raw1 = "```python\nfoo.py\n<<<<<<< SEARCH\ndef add(a, b):\n    return a + b\n=======\ndef add(a, b, c=0):\n    return a + b + c\n>>>>>>> REPLACE\n```"
content, mode = ac.apply_edit_protocol(raw1, target)
assert mode == "sr" and "def add(a, b, c=0):" in content and "def sub" in content, (mode, content)
print("1 sr-block-edit PASS")

# 2) SEARCH 不命中 → sr_fail（禁止落盘，由调用方带错重试）
raw2 = raw1.replace("def add(a, b):\n    return a + b", "return x")  # 制造不命中
content, mode = ac.apply_edit_protocol(raw2, target)
assert mode == "sr_fail" and content is None, (mode, content)
print("2 sr-miss-sr_fail PASS")

# 3) 无块 + 围栏剥离 → 整文件
raw3 = "```python\ndef only():\n    return 1\n```"
content, mode = ac.apply_edit_protocol(raw3, target)
assert mode == "whole" and content == "def only():\n    return 1", (mode, repr(content))
print("3 fence-strip PASS")

# 4) lint 门：好内容过 / 坏内容报行号
assert ac.lint_py_text("x = 1\n") is None
err = ac.lint_py_text("def f(:\n    pass\n")
assert err and "line 1" in err, err
print("4 lint-gate PASS")

# 5) 真文件跑一遍 sr 落盘 + 复 lint
content, mode = ac.apply_edit_protocol(raw1, target)
assert ac.lint_py_text(content) is None
with open(target, "w", encoding="utf-8") as f:
    f.write(content)
assert "c=0" in open(target, encoding="utf-8").read()
print("5 sr-apply-roundtrip PASS")

shutil.rmtree(TMP, ignore_errors=True)
print("F3 EDIT-PROTOCOL SMOKE ALL PASS")
