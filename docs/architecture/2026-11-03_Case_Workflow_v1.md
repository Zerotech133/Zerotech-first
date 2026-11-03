# Case 流程 v1：田丰汇总 → 五角色意见 → 会议 → 方案 → 人类审批 → 下发

- 日期：2026-11-03｜触发：A0「田丰汇总、5角色读材料给意见、田丰组织开会总结解决办法、交人类审批后执行下发」
- 状态机：`DRAFT → AGGREGATED → REVIEWED → MEETING → PROPOSED → APPROVED → DISPATCHED`
- 硬门：**未 APPROVED 不得 dispatch**；五角色不齐不得进会议；方案只记录，不自动执行。

## 一、流程

```
CASE 新建（标题 + 材料）
 → 田丰汇总（A0 云模型；事实/证据缺口/关键矛盾/建议议程）
 → 五角色读材料给意见（田丰/诸葛亮/赵云/黄忠/关羽，5 席）
 → 田丰组织会议（HQ Meeting v2，保存纪要）
 → 田丰总结解决办法（推荐/备选/风险/回滚/验收/待批点）
 → 人类审批（--approver 老田；记录审批人/时间/备注）
 → 下发（创建任务申请；执行仍由军团）
```

## 二、命令

```powershell
# 1) 新建
python -X utf8 tools/hq_case.py new `
  --title "数据资源台账缺口" --materials docs/xxx.md notes/yyy.md

# 2) 田丰汇总
python -X utf8 tools/hq_case.py aggregate --case CASE-20261103-abc123

# 3) 五角色意见（5/5 才进 REVIEWED）
python -X utf8 tools/hq_case.py review --case CASE-...

# 4) 组织会议
python -X utf8 tools/hq_case.py meeting --case CASE-...

# 5) 方案
python -X utf8 tools/hq_case.py propose --case CASE-...

# 6) 人类审批（必须显式）
python -X utf8 tools/hq_case.py approve --case CASE-... --approver 老田 --note "同意"

# 7) 审批后才可下发
python -X utf8 tools/hq_case.py dispatch --case CASE-... --owner DATA1
```

## 三、数据落点

- Case：`deliverables/cases/<case_id>/case.json`
- 会议：`agent_platform/data/meetings/hq/hq-YYYYMMDD-HHMMSS.json`
- 下发：`hq_meeting.dispatch_meeting` 生成任务申请（不自动执行）
- 历史：`case.history[]` 记录每步时间/动作

## 四、硬约束

1. 五角色必须 5/5 有文本，否则 `REVIEWED` 不成立；
2. `APPROVED` 必须由 `approve --approver` 显式写入；
3. 未审批调用 dispatch → `human_approval_required`，不创建任务；
4. 审批后只创建任务申请，不直接改代码/不自动执行；
5. 模型失败显式记录，不伪造角色意见/方案；
6. 人类审批仍是本地 CLI 显式动作，后续接 UI/审批系统再加强身份认证。

## 五、验证

- 3 tests：完整链路 + 审批前拦截、五角色不全停留 AGGREGATED、未审批/无会议拒绝；
- 审批门是状态机强校验，不依赖前端。

## 六、下一步

1. `/case` 前端（新建、五角色意见、会议、方案、审批按钮）；
2. 审批接飞书/本地 admin 身份，不只是 CLI 字符串；
3. 下发后把任务回执写回 case（DISPATCHED→VERIFYING→DONE）；
4. 周报统计 case 闭环率。
