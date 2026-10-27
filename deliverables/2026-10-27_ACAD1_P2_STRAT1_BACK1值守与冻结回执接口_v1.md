# STRAT1/BACK1 值守触发与 DATA1 冻结回执接口 v1（ACAD1 P2）

> 日期：2026-10-27 ｜ 工单：WO-LEGION-ACAD1-P2-20261027 ｜ 执行：DSH session（Lead）
> 上游：ACAD1 P0（三任务已装、5/5）、P1（两席能力卡、LCS-1 数据冻结跟踪）
> 目标：把 STRAT1/BACK1 从 3/5 推到 **5/5 真成员**（补触发+审计），并把「DATA1 出回执 → BACK1 开跑」写成机器可判的接口。

## 一、新增触发（3 个计划任务，L2 待批）

| 任务 | 时点 | 执行体 | 作用 |
|---|---|---|---|
| STRAT1 周一假设简报任务 | 周一 09:40 | `research_seat_ops.py strat1` | 策略假设值守：待补假设/八字段缺项/Spec 责任提醒；emit `research_report`（agent=STRAT1） |
| BACK1 周三冻结值守任务 | 周三 15:10 | `research_seat_ops.py back1` | 冻结/回测值守：四字段 + DATA1 回执检查；就绪报 **READY**，否则 **WAITING_DATA1**；emit `research_report`（agent=BACK1） |
| 双席周五周报任务 | 周五 16:10 | `research_seat_ops.py weekly` | 双席合并周报；两席各 emit 一条 `research_report` |

- 安装器：[tools/install_research_seat_ops.ps1](../../tools/install_research_seat_ops.ps1)（`-DryRun` 预演 / `-Uninstall` 回滚；ASCII 路径 `C:\aios`）。
- 输出落 `agent_platform/data/academy/ops/{strat1,back1,weekly}/`，只读台账、不写数据根、不回测、不交易。

## 二、DATA1 冻结回执接口（机器可判）

**路径**：`agent_platform/data/academy/freeze_receipts/<topic_id>.json`

```json
{
  "topic_id": "AC-20261017-LCS1",
  "data_version": "<数据源/版本/快照日期/指纹 sha256>",
  "sample_in": "<起止日期/池口径/样本数 n>",
  "sample_out": "<起止日期/池口径/样本数 n，与 in 不重叠>",
  "cost_model": "<佣金/印花税/滑点/冲击参数与版本>",
  "sha256": "<数据快照指纹>",
  "frozen_at": "YYYY-MM-DDTHH:MM:SS+08:00",
  "by": "DATA1"
}
```

**BACK1 判定规则（`research_seat_ops.py back1` 已实现）**：
1. 回测登记 `backtests.jsonl` 四字段（data_version/sample_in/sample_out/cost_model）齐且非 `待*`；
2. 回执文件存在且四字段齐；
3. 两者同时满足 → `READY（可开跑回测）`；否则 `WAITING_DATA1`（拒跑）。
> 判定以**登记真源 + 回执文件**为准；`rc=0` 或有回执但登记未更新都不算就绪。

## 三、RACI（LCS-1 冻结→回测→评审）

| 事项 | R | A | C | I |
|---|---|---|---|---|
| 数据冻结回执（四字段+sha256） | DATA1 | ACAD1 | BACK1、STRAT1 | A0 |
| 策略 Spec（图形算子/阈值/否定条件） | STRAT1 | ACAD1 | BACK1 | A0 |
| 回测执行与登记 | BACK1 | ACAD1 | DATA1 | A0 |
| 评审与下一轮 Go/No-Go | ACAD1 | ACAD1（裁决老田签） | STRAT1、BACK1、D1 | A0 |

## 四、验收判据

1. `pytest agent_platform/tests/test_research_seat_ops.py` 全过；
2. `research_seat_ops.py back1` 在无回执时明确输出 `WAITING_DATA1`，有回执且四字段齐时输出 `READY`（单测覆盖两态）；
3. 三任务安装后：`seat_realness --seat STRAT1 --task <安装器列表中的 STRAT1 任务名>` = **5/5**，`--seat BACK1 --task <安装器列表中的 BACK1 任务名>` = **5/5**；任务名以 `tools/install_research_seat_ops.ps1` 的 `$Tasks` 数组为准；
4. 能力卡 `legion_agents/STRAT1|BACK1/workspace/skills/CAPABILITIES.json` 的 `trigger-ops` 动态项翻 `ready`，`capability_self_audit` drift=0。

## 五、L2 说明与回滚

- 计划任务增删属 L2 宿主机级动作（K-STOCK-416）：安装依据老田 2026-10-27 09:50 指令（本批“给它们装与研究院同款的触发任务”）；五要素见工单 note + approval 账本。
- 回滚：`powershell -File C:\aios\tools\install_research_seat_ops.ps1 -Uninstall`（卸三任务）；`git revert <merge-commit>`；能力卡重注入后 trigger-ops 自动回 `pending_wiring`。

## 六、边界声明

- 本批不碰数据根（`quant_backtest/data`）、不回测、不交易、不改生产服务/端口；
- 不代 DATA1 填冻结回执、不代老田签字；`READY` 仅表示“可开跑回测”，不等于 Go。
