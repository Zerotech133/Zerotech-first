# -*- coding: utf-8 -*-
"""K-ORG-056 迭代轮：冒烟缺陷反馈单回 DEV1（产线 F4 迭代回路）。"""
import sys
sys.path.insert(0, "tools")
import agent_coder

PROMPT = """你是研发部 DEV1。上一轮产出的 tools/morning_brief.py 冒烟失败，缺陷反馈单两处：
1. 事件账本路径写错：agent_platform/data/dev_events.jsonl 不存在——真实账本是
   portal/data/dev_events.jsonl（以仓库根为基准定位；建议用 os.path 相对脚本自身
   或 sys.argv 兼容任意 cwd）。方案条目 P-1 已同步修正。
2. P-5 降级要求未达标：账本缺失时 FileNotFoundError 直接崩——所有单点读取
   （events/org_baseline/sec2 目录）必须 try/except 降级为占位文本，整体必出三件套。

用 SEARCH/REPLACE 块对 tools/morning_brief.py 做最小修改，别整文件重写。
改完自查：路径改成 portal/data/；所有 open/json.load 包在容错里；main 正常返回三件套。
"""

ok, detail = agent_coder.run(
    label="morning_brief_v0_fix1",
    agent_id="DEV1",
    out_path="tools/morning_brief.py",
    prompt=PROMPT,
    k_id="K-ORG-056",
)
print("RUN:", ok, "|", str(detail)[:200])
sys.exit(0 if ok else 1)
