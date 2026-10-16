# workflow_candidates 候选区（K-ORG-120 · MASS 内环）

MASS 喂养体系的「新工作流候选区」：OPT1（流程优化师）提出的每一次工作流修改，
先落本目录进影子，成对判读赢了且老田拍板后，才允许替换生产 `prompts/*` 冠军。
**本目录与生产注册表之间隔着一道墙：候选永远不直接上线。**

## 宪法（三条，不可绕过）

1. **唯一写手 OPT1**：本目录 JSON 的 `workflow`/`proposal` 块只允许 OPT1 写入。
   A0 判读结果只追加 `judgement` 块，老田终审只追加 `review` 块——评估与优化不同手
   （同构循环红线：自己改的东西自己打分 = 盲区）。
2. **冠军保留制**：生产 `prompts/*` 即当前冠军。候选只在影子模式跑
   （`a0_agent.py --compare`），判读 `winner=candidate` 且 `confidence` 不低于 medium、
   老田拍板后，候选内容方可晋升为新冠军；旧冠军内容在候选 JSON 的
   `workflow.champion_snapshot` 留档，可回滚。
3. **换血走 8093 工单**：每次 status 流转（draft→in_shadow→promoted|rejected）
   必须挂一个 K- 开头工单（`.worktrees/tasks.json`），proposal.ticket 字段即指针。
   无工单的候选一律视为草稿废纸。

## 命名与状态

- 文件名：`cand_<workflow>_<yyyymmdd>_<n>.json`
  - `<workflow>`：目标工作流标识（如 `a0_daily_review`）
  - `<n>`：同日同工作流第几份（01 起）
- status 流转：`draft` →（挂工单+进影子）→ `in_shadow` →（判读+拍板）→ `promoted | rejected`
- schema 见本目录 `_schema.json`

## 首个目标工作流

`a0_daily_review`（A0 日报点评，prompts/a0_chief.md + a0_agent.py）——
菜鸟总管喂养的第一块试验田：OPT1 改的是「读日报出判读」这条工作流的四组件，
不是改 A0 的人设本身（信息进契约不进人设）。

## 参与角色

| 角色 | 席位 | 在本目录的权限 |
|---|---|---|
| OPT1 流程优化师 | 优化者 | 写 workflow/proposal 块（唯一写手） |
| A0 田丰 | 评估者 | 追加 judgement 块（成对判读，`--compare` 产出指针） |
| 老田 | 终审 | 追加 review 块（拍板 promoted/rejected） |
| 生产注册表 | — | OPT1 只读（danger=deny，见 hr/OPT1/appointment.yaml） |
