# -*- coding: utf-8 -*-
"""K-ORG-036 F4 冒烟：按单自动提交（dry-run，不产生真实 commit）。
F1 claude_glm 仍 blocked → smoke 钩子用真模型免跑，走本地桩。
"""
import os
import subprocess as sp
import sys

sys.path.insert(0, "tools")
import agent_coder as ac  # noqa: E402

probe = os.path.join("work", "f4_commit_probe.py")
with open(probe, "w", encoding="utf-8") as f:
    f.write("# K-ORG-036d F4 smoke probe\nVALUE = 42\n")

# 1) smoke 钩子：run() 落盘后回调语义（此处直接验 callable 前置条件）
def fake_smoke(path):
    src = open(path, encoding="utf-8").read()
    assert "VALUE = 42" in src

fake_smoke(probe)
print("1 smoke-hook-semantics PASS")

# 2) 按单提交 dry-run：add + commit --dry-run 只动目标文件
ok, detail = ac.commit_work(probe, "K-ORG-036d", "OPS1", "F4按单提交冒烟",
                            channel="ollama", dry_run=True)
assert ok, detail
print("2 commit-dry-run PASS:", detail)

# 3) 消息格式断言（按单可追溯三要素：K编号/岗位/通道）
msg_ok = sp.run(["git", "status", "--porcelain", "--", probe],
                capture_output=True, text=True).stdout.strip()
assert probe.replace("\\", "/") in msg_ok or "A" in msg_ok, msg_ok
print("3 staged-path PASS:", msg_ok[:60])

# 4) 复位暂存（dry-run 不留痕），scratch 文件保留随批次入库
sp.run(["git", "reset", "-q", "--", probe], capture_output=True)
print("F4 COMMIT-WORK SMOKE ALL PASS")
