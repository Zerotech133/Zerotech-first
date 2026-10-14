【硬约束·先读】全部代码中字符串字面量之外禁止出现任何全角/中文标点（：，、；（）「」等）；中文只允许出现在 # 注释和字符串内；写每一行保持半角。

新建单文件只读工具 tools/e2e_report.py，汇总 e2e 彩排轨迹（真实需求：11-08 正验报告需要彩排轮次/关卡/结果一览）。功能条目：

P-1 扫描 data/e2e/ 下所有 K-*_trace_*.md 文件（主仓根定位：优先 git rev-parse --git-common-dir 锚定，非 git 环境降级 __file__ 上溯两级），文件名解析出 TID 与时间。
P-2 每个轨迹文件提取：终态（首行标题里括号内的 DONE/ABORT/SEC2_FAIL/QA1_FAIL）、步骤数（以 "- [" 开头的行数）、最后一步内容。
P-3 唯一 CLI 契约：`python tools/e2e_report.py --json` → stdout 只输出一个 JSON 对象 {"runs": N, "items": [{"tid": ..., "time": ..., "final": ..., "steps": N, "last_step": ...}, ...]（按 time 倒序）}，退出码 0；JSON 之外不打印任何其他内容到 stdout。
P-4 只读边界：不写文件、无网络；文件头部 # 注释标注 K-ORG-066 与用途一句话。
