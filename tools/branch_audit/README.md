# 悬空分支审计工具

配套交付物：`deliverables/2026-11-06_悬空分支可合并候选清单_v1.md`

这三个脚本回答同一个问题——「堆在分支上的活，哪些能合、按什么顺序合、哪些其实已经落地了」。
全部只读：不 checkout、不改 ref、不动工作区，只往 `.git/wb-branch-audit/` 写分析用 JSON。

## 跑法（在仓库根执行）

```bash
# 1) 逐文件三方比对，算每个分支的净贡献
python tools/branch_audit/merge_candidates_v2.py

# 2) 按「线」分组，组内依次叠加演练
python tools/branch_audit/merge_plan_v3.py

# 3) 把可合的分支按低耦合优先排序，一次性叠加验证
python tools/branch_audit/global_merge_drill.py

# 4) 重新生成清单文档（会覆盖 deliverables 下同名文件）
python tools/branch_audit/gen_candidates_doc.py
```

换仓库跑：`WB_REPO=/path/to/repo python tools/branch_audit/merge_candidates_v2.py`

## 三个判据的含义

| 判据 | 命令本质 | 回答什么 |
|---|---|---|
| 净贡献 | 逐文件比 base / 分支 / master 三个版本 | 这条分支的改动到底有没有落地 |
| 冲突 | `git merge-tree --write-tree` | 合并会不会打架（无副作用） |
| 顺序 | 把上一步的 tree 当下一次合并的左侧 | 一批合起来是否仍然不打架 |

## 为什么不能用 `git cherry` 单独判

`git cherry` 比对的是 patch-id。它说「等价」只代表**某几条提交**在 master 上有等价补丁，
不代表分支里每个文件都落了地。实测有一例：某分支 6 条提交中 5 条等价，
剩下 1 条含一个在 master 上**完全不存在**的模块 + 测试 + 交付文档。
按 cherry 结论删分支，这份工作就只剩 reflog 能找。

## 已知边界

- 只判文本层可行性，**不跑测试**。合完必须跑。
- 只看无 worktree 的分支；被 worktree 占用的属于在途工作，不在范围内。
- 「有冲突」不等于「该删」——可能是最重要的成果，只是需要作者回来处理。
