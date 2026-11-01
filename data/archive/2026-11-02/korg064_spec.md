【硬约束·先读】全部代码中字符串字面量之外禁止出现任何全角/中文标点（：，、；（）「」等）；中文只允许出现在 # 注释和字符串内；前几轮产码反复栽在全角标点进代码区，写每一行时保持半角。

新建单文件只读工具 tools/devtask_audit.py（目标 ≤90 行，功能做小不做大）：

P-1 读 .worktrees/tasks.json（dict，键=工单号，值含 status 字段），取 status=='review' 的 TID 列表。
P-2 对每个 TID 运行 git log --all --grep=<TID>，存在提交消息含 "merge task/<TID>" 即归入 already_merged，否则归入 still_reviewing。
P-3 唯一 CLI 契约：`python tools/devtask_audit.py --json` → stdout 只输出一个 JSON 对象 {"total": N, "already_merged": [...], "still_reviewing": [...]}，退出码 0；JSON 之外不打印任何其他内容到 stdout。
P-4 只读边界：不写文件、无网络、git 只用 log；文件头部 # 注释标注 K-ORG-064。
