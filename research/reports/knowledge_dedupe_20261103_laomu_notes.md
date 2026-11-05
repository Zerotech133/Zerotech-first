# 知识单元重复 cohort 合并报告（W0-2 · 2026-11-03）

> 输入：`C:\项目\知识库\30_知识单元\laomu_notes_units_draft.jsonl` · 单元 **288** · dry_run=False
> 规则：按归一化 claim 前 14 个 CJK 字符分组；保留首条 + 合并 evidence；跳过抽取元数据=True
> 结果：重复组 **1** · 重复行 **2** · 合并后 **286**（合并 2）· 跳过元数据 0

## 重复组样例（前 15）

| key | 组大小 | 保留 unit_id | 合并条数 | evidence 数 |
|---|---|---|---|---|
| 我是老木感谢关注 | 3 | learn_laomu_notes_0065 | 2 | 3 |

## 说明

- 预计变化：288 → 286（-2）
- 备份：C:\项目\老田的AI助手\research\reports\backups\laomu_notes_units_draft.jsonl.20261104_200004.v1bak
- 默认 dry-run；--apply 才写回并先备份；重复为 0 时 apply 不写（幂等）。
- 本工具不修改知识卡/其他项目；仅作用于输入 units 文件。

> W0-2 重复单元合并：默认 dry-run；不修改知识卡/其他项目；不构成投资建议。
