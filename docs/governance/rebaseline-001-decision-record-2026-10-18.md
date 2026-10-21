# REBASELINE-001 人工决策记录

> 决策日期：2026-10-18 00:59（中国标准时间）  
> 决策人：老田  
> 执行人：WorkBuddy  
> 观察人：老田  
> 决策状态：已登记，未授权正式 rebaseline

## 一、决策结论

### 1. 路由范围

选择：**保持 72 条正式基线**。

- 历史决策口径中的 23 条新增路由继续作为待审阅差异隔离保存；封存包现场检查另记录为 72→106，即 34 条差异；两种口径尚未对账，均不得作为批准依据；
- 不生成正式新基线；
- 不生成 `CANDIDATE`；
- 不修改 `portal/route_baseline.json`、路由扫描脚本、导航生成物或 nginx；
- `route_baseline.py --check` 的退出码不得单独解释为无漂移或批准。

### 2. 账本范围

选择：**继续保全**。

- 不生成账本外部投影；
- 不生成 current recheck；
- 不回写、修复、删除、归档、重排或补字段；
- 不修改 `.worktrees/tasks.json` 或 Agent Platform JSONL；
- 不将任何一次观测的 `raw=219` 或 `raw=226` 自动认定为当前稳定事实源。

## 二、依据与已知状态

REBASELINE-001 审查包：

```text
C:/项目/老田的AI助手/docs/governance/rebaseline-001-20261018T002306+0800
```

审查包在一次性只读封存时记录了账本继续漂移：

- raw objects：226；
- status：`done=180`、`aborted=45`、`doing=1`；
- size：151,201 bytes；
- SHA-256：`08760a2602e064aadd45dfe36415143aa1e4ba9b99f96ca7b0ddb5ab846f712c`；
- mtime：2026-10-18 00:07:51.062043（中国标准时间）。

该观测仅是带时间戳的封存事实，不构成稳定 current baseline。

既有索引：

```text
C:/项目/老田的AI助手/docs/governance/evidence-index-2026-10-17.md
```

仍作为历史时间切片保留，不覆盖、不删除、不原地编辑。

## 三、当前状态

```text
DRIFT_DETECTED
HUMAN_REVIEW_REQUIRED
CONTINUOUS_LEDGER_DRIFT
BLOCKED
```

## 四、后续放行条件

只有在以下条件全部满足后，才可重新讨论 current recheck 或正式 rebaseline：

1. 账本停止漂移并完成一次性稳定快照；
2. 明确账本 owner、写入来源和变更窗口；
3. 路由新增项完成逐条来源、用途、owner、审批和回滚审阅；
4. 账本与路由审批保持独立；
5. 新增证据使用带时间戳的新文件，不覆盖历史证据；
6. 独立 QA 完成验证；
7. 任何正式基线切换均取得单独人工批准。

## 五、禁止动作

在上述条件满足前，禁止：

- 读取或解析账本以建立新结论；
- 生成 current manifest/recheck 或账本投影；
- 重建或切换路由基线；
- 修改、覆盖或删除旧 `evidence-index`；
- 回滚、修复或归因账本变化；
- 修改服务、端口、nginx、计划任务或数据根。

本记录只登记人工选择，不授予任何生产写入或基线切换权限。
