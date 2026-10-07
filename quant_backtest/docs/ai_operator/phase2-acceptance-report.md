# 第二批并行开发验收报告

2026-10-07，验收已完成。本批交付时点结构回放、事实包和影子建议审计，已经接入 `E:\老田的AI助手\quant_backtest`。这是原研发方案中 M2 时点工程和影子事实链路的增量，不代表 M2–M6 全部完成。

## 交付内容

| 并行任务 | 交付 | 状态 |
| --- | --- | --- |
| A 逐根回放 | `ai_operator/replay.py`；只向原算法提供当前前缀，保留首次观测、修订撤销和再现记录 | 已接入 |
| B 事实与影子审计 | `ai_operator/shadow.py`；时点、完整包哈希、版本、证据和决策重验，沿用首批事件库 | 已接入 |
| C 联调 | `replay/fact-pack/shadow-decision` 三个 CLI 命令；严格 JSON 输入和错误输出 | 已接入 |
| 独立盘点与审查 | 原算法与存量数据核查、审查修复、复审及隔离验收脚本 | 已归档 |

源码与测试共 6 个变更文件；没有修改首批数据库 DDL。实施前校验原 CLI 与首批版本相同，新增文件遇已有不同内容时停止覆盖；部署后 6 文件 SHA256 与独立审查版本全部一致。仓库中其他现有和期间产生的改动未被覆盖或提交。

## 测试证据

在实际 `E:\老田的AI助手\quant_backtest` 目录执行：

```powershell
& 'C:\Program Files\Python311\python.exe' -B -m unittest discover -s tests_ai_operator
```

结果：**139 项，137 通过、2 跳过、0 失败**，16.484 秒。其中本批新增 49 项全部通过。两项跳过来自首批符号链接创建测试，原因是 Windows `WinError 1314` 权限不足；目录 junction 和 ZIP 链接拒绝测试仍通过。

测试覆盖前缀隔离、算法修改输入/复用输出、显式延迟、日期顺序、结构撤销再现、未来/未知定位拒绝、完整包和决策篡改、嵌套后验字段、事件幂等和第二事件中断恢复、CLI 失败无业务写入。

独立审查发现并修复：影子命令曾把已有 0 字节空文件隐式初始化。新增行为测试先失败，改为只读预检完整数据库后再写入，复测成功且空文件字节不变、无 WAL。复审最终未发现阻断项。审查另用真实 SQLite 触发器验证第二事件失败后补齐重试，并验证外部数据库拒绝时源哈希保持不变。

## 本地算法工程验收

```powershell
& 'C:\Program Files\Python311\python.exe' -B 'E:\老田的AI助手\quant_backtest\docs\ai_operator\phase2_acceptance.py' --project-root 'E:\老田的AI助手\quant_backtest' --operator-root 'E:\老田的AI助手\quant_backtest' --output 'C:\研究目录\验收.json'
```

使用现存 `data\ashare\000001_SH.parquet` 前 120 根，调用实际 `chanlun_bi.analyze`；不调用行情加载、网络或生产数据库。

- 120 根全部匹配各自前缀的直接算法输出，前 60 根快照与独立 60 根回放完全一致。
- 保留 74 次结构版本新增、17 次撤销；末快照为分型 43、笔 8、简化线段 1、笔中枢 2、简化信号 3。
- 隔离审计库重复登记后仍为事实 1、建议 1，共 2 条事件；经验 0；完整导出 2 条，重试事件 ID 一致。
- 样本及 `chanlun_bi/chanlun_fractal/chanlun_data` 来源哈希在验收前后相同。
- 120 根回放实测 0.148822 秒，包含全部前缀重算；不是单票决策 P95 或 2000 根上限性能验证。

**时间与来源边界**：样本缺供应商历史 `available_at`，来源也无专属 manifest。本次逐条实测当前接收时间；日线收盘时间按 CN 15:00 推导并标记。所有回放 `as_of` 属于本次接收时期，没有把这些数据伪装成历史当时可知。这只验工程一致性，不证明真实市场来源、收益或可成交性。机器报告明确 `historical_profitability_verified=false`、`production_eligible=false`。验收使用的临时数据库与导出文件已随临时目录清理，保留报告与可复现脚本。

## 使用与下一阶段门槛

运行命令、JSON 输入格式和恢复方法见同目录的《第二批运行说明》。`confirmed_at` 仅表示内容版本首次观测，状态为 `observed`，结构仍可重绘；影子建议固定 `execution_allowed=false`。

下阶段沿原方案推进：核验两个原始 ZIP 的 Schema、规则 ID 与 evidence；补齐可信行情可用时间与来源快照；随后才做四组结构对照、成本后时间外评估、成熟标签与时间切分，再接硬风控与独立 paper 账户。原始 ZIP 仍未取得，现有本地简化算法不被当成已认证的标准缠论算法。

本批未启动定时任务、模型、训练、推送、模拟成交或实盘交易，未修改生产行情、账本或交易配置，也未发布云端包。

## 文件清单与哈希

| 文件（相对 quant_backtest） | SHA256 |
| --- | --- |
| `ai_operator/cli.py` | `586C97A5F42AD3694C3EBE4686996520E48C6050FFEAB813CC975B76EFA5A43F` |
| `ai_operator/replay.py` | `0B2F7ACDF8927FA0F214304B741C44B138C6A022167A0E7224F859A0AE859E7F` |
| `ai_operator/shadow.py` | `07FCD2EF87310B3CCBFA0A30D9749AC38BF82A63A4AE04EF2CCDFFAA7BD98AC6` |
| `tests_ai_operator/test_replay.py` | `D55B68EF81D710DE4F9C7A14EDDD1342F309129A6678C50D20D0516CDEC88FFE` |
| `tests_ai_operator/test_shadow.py` | `E84E7BE5112D5E73B41A20559BB9271E6BDDB5F6C176A65CDA596AC788E3266E` |
| `tests_ai_operator/test_phase2_cli.py` | `11F8E35247B1E7961FD36D34CD760319A03B06E0AE5EEC0583769FF641394187` |

项目内归档：`docs/ai_operator/phase2-plan.md`、`phase2-README.md`、`phase2-review.md`、`phase2-baseline.md`、`phase2-replay.md`、`phase2-shadow.md`、`phase2_acceptance.py`、`phase2-acceptance-report.md`。
