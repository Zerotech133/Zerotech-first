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

## 六、一键自动化（到方案）

`/case` 顶部「⚡ 一键自动化」：

- 一次点击：建 Case → 田丰汇总 → 五角色并行 → 失败自动重试 → 保存会议 → 生成方案；
- 全流程进度条 + 阶段状态；失败先自动重试一次，仍失败才停下并显示 x/5；
- **人类审批仍是最后一道门**：自动化只到方案，不自动批准/下发；
- 审批后可用「审批并下发」一次完成审批+任务申请。

## 七、即时拆解（虚拟临战会，不强制写实）

超级个体随时可能面对任务拆解，不必每次落正式会议：

- `/case` 顶部「⚡ 即时拆解」：输入一句话任务；
- 田丰拆解 → 5角色快速意见（并行）→ 田丰合成；
- 7 次真实云端调用；**不保存会议纪要、不进入审批/下发**；
- 需要留痕时点「升级为正式 Case」，再走七步流程；
- Playwright 实测：7 次 chat、0 次 meeting save、升级后会议按钮可用。

## 八、前端入口（已实现）

`/case`（需登录）：

1. 建 Case（标题 + 粘贴材料）；
2. 田丰汇总；
3. 五角色会诊；
4. 保存作战会纪要（HQ Meeting v2）；
5. 田丰方案；
6. 人类审批（审批人输入/记录）；
7. 审批后才可下发任务申请。

- 页面只把流程状态存本机 localStorage；会议纪要与任务申请落服务端；
- 顶部整体 7 步进度条；即时拆解有阶段进度（拆解/5角色/合成）和 120s 超时；旧会话显示「重新登录刷新身份」；
- 五角色真正并行（原来代码串行）；失败角色显示 `x/5` 并可「重试失败角色」；5/5 才允许保存会议/生成方案；
- 审批按钮绑定登录身份：ADMIN/OWNER/APPROVER 可审批；VIEWER 不可审批下发；审批人自动取 session 身份（2026-11-03 老田授权 tf=APPROVER）；
- `下发` 按钮在审批前 `disabled`；Playwright 实测 7 步走通、审批前不可下发；
- CLI 仍是强校验入口（`hq_case.py` 状态机硬拦）。

## 七、下一步

1. `/case` 前端（新建、五角色意见、会议、方案、审批按钮）；
2. 审批接飞书/本地 admin 身份，不只是 CLI 字符串；
3. 下发后把任务回执写回 case（DISPATCHED→VERIFYING→DONE）；
4. 周报统计 case 闭环率。
