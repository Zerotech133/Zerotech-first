# -*- coding: utf-8 -*-
"""K-ORG-036 F1 冒烟：claude_glm 通道连通性（只读，不动文件）。
执行位：worker/调度侧（主控沙箱拦 claude 内部 spawn）。
"""
import os
import sys

sys.path.insert(0, "tools")
os.environ["CODER_BACKEND"] = "claude_glm"
import agent_coder  # noqa: E402

out = agent_coder.chat_claude("DEV2", {
    "label": "smoke",
    "prompt": "只回答一个数字，不要使用任何工具：1+1=?",
    "allowed_tools": "Read",
    "max_turns": 2,
})
print("SMOKE_OUT:", out[:100])
assert "2" in out, "回包不含期望数字: %r" % out[:100]
print("F1 CLAUDE_GLM SMOKE PASS")
