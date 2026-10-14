# -*- coding: utf-8 -*-
"""K-ORG-056 驱动脚本：研发部 DEV1 按方案条目产码（主控代跑产线接口）。"""
import sys
sys.path.insert(0, "tools")
import agent_coder

PLAN = "docs/plans/2026-10-14_0210_晨会三件套生成器v0_K-ORG-056.md"
PROMPT = """你是研发部 DEV1（20 年 Python 后端）。按研发方案实施工单，先读方案文件：
%s

交付：tools/morning_brief.py 单文件（全新文件），满足方案条目 P-1~P-5：
- P-1 统计 agent_platform/data/dev_events.jsonl 中指定日期（--date 缺省今天）五类事件
  （approval_request/approval_verdict/deviation_report/task_start/commit）计数与明细。
- P-2 读 agent_platform/registry/org_baseline.json 输出 A~G 批状态表（批号/名称/状态/pending）。
- P-3 扫 agent_platform/data/sec2/ 取最近 1 份报告判定统计（正则宽容匹配 完善\\s*(\\d+)·?走形\\s*(\\d+)·?缺失\\s*(\\d+) 或 完善N走形N缺失N 变体；读不到输出"无近期走查"不报错）。
- P-4 产出 agent_platform/data/morning_brief/YYYYMMDD.md，三个二级标题：
  ## 昨日事件汇总 / ## 今日分发清单 / ## 风险与走形跟踪；--out 可覆盖路径；目录不存在自动创建。
- P-5 CLI argparse：--date YYYY-MM-DD / --out PATH；只读零网络；任何单点异常
  （文件缺失/json 解析失败）降级为占位文本不中断整体；文件头注明 K-ORG-056 与方案路径。

编码纪律：标准库 only；函数小而直；事件明细行截断 120 字符；main 返回 0。
直接输出完整文件内容。
""" % PLAN

ok, detail = agent_coder.run(
    label="morning_brief_v0",
    agent_id="DEV1",
    out_path="tools/morning_brief.py",
    prompt=PROMPT,
    k_id="K-ORG-056",
    smoke=_smoke if (_smoke := None) else None,
)
print("RUN:", ok, "|", str(detail)[:200])
sys.exit(0 if ok else 1)
