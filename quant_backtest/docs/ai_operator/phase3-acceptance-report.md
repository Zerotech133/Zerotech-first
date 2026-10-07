# 第三批并行开发验收报告

2026-10-07。本批已经完成开发、独立审查、E 盘接入及目标目录验收，交付成熟结果标签、特征白名单、时间切分与描述性评估工具。不代表正式训练或完整 M3/M5 已完成。

## 交付

| 并行任务 | 成果 | 状态 |
| --- | --- | --- |
| A 成熟标签 | `ai_operator/outcomes.py`；完整影子决策校验、显式日历 H5/H20、未来参考收盘、成熟/待成熟/审计状态 | 已接入 |
| B 时间外数据集 | `ai_operator/dataset.py`；白名单、标签结束与可用时间 purge、embargo、传递分组、输入与结果双哈希、摘要重验 | 已接入 |
| 主代理联调 | `outcome-label/dataset-split/dataset-summary` 三个只读 CLI；可重复端到端夹具和文档 | 已接入 |
| 来源核验和独立审查 | 两份 Parquet Schema/null/时点缺口、云端获取状态、审查和定向探针 | 已归档 |

目标为 `E:\老田的AI助手\quant_backtest`。本批共 6 个源码/测试变更文件，不改 SQLite DDL；接入前检查既有 CLI 仍为第二批版本，新文件若已有不同内容则停止覆盖。接入后 6 文件 SHA256 与独立审查冻结版本一致。其他工作区改动未覆盖，也未提交 Git。

## 测试与审查

在实际项目目录执行：

```powershell
& 'C:\Program Files\Python311\python.exe' -B -m unittest discover -s tests_ai_operator
```

结果：**192 项，190 通过、2 跳过、0 失败**，21.592 秒。第三批新增 **53 项全部通过**（标签 23、数据集 24、CLI 6）。两项跳过为首批目录/根符号链接创建测试，Windows `WinError 1314`；junction 和 ZIP 链接拒绝测试通过。

所有新模块和 CLI 均有缺功能 RED→GREEN 记录。联调期间发现只哈希切分结果未覆盖被剔除行内容，补测 RED 后新增 `input_hash`，现在被剔除行的特征或目标变化也改变输入及结果哈希。

冻结版独立审查未发现阻断项；另验证了 24 项定向断言。重点包含未到达未来价格不曝光、未知来源不生成目标、完整包/决策篡改拒绝、边界时区、purge/embargo、传递分组，以及重新计算哈希不能绕过保留行语义。极端日期、巨大数值、畸形 JSON、编码、重复键、深嵌套和超过 8 MiB 输入按契约返回结构化错误。新 CLI 分支不调用数据库写入。

## 端到端验收

执行部署后的 `docs/ai_operator/phase3_acceptance.py`，使用明确标识的 **工程夹具**，不把旧样本补造成可信历史数据。

| 核验 | 结果 |
| --- | --- |
| 日历/行情 | 90 个夹具 session；显式收盘及接收时点 |
| 成熟 H5/H20 标签 | 20 项 |
| 未到达、未知来源、缺窗口行情 | 各 2 项，目标均为 null；未知来源为 audit_only |
| 数据集 | 11 行，保留训练 1、验证 1、测试 2；其余 7 行剔除 |
| 剔除原因 | embargo 2；group_overlap、immature、outside_window、purge_train、purge_validation 各 1 |
| 可重复性 | 相同请求标签一致，输入反序切分与双哈希一致，摘要重验通过 |
| 真实存量文件 | 两份 Parquet 原文件 SHA256 在验收前后不变 |

完整夹具请求和产物保留在本输出目录 `第三批工程夹具/`：`label-request.json`、`label-result.json`、`dataset-request.json`、`split-result.json`、`summary.json`。这些数字和夹具价格不是真实市场收益证据。

机器报告 `2026-10-07-第三批研发验收.json` 明确 `model_trained=false`、`historical_profitability_verified=false`、`production_eligible=false`。目标是证明程序链路能正确处理明确输入，不是证明模型、策略或交易盈利。

## 数据前置核验

- `kg_distill.parquet`：9708 行，SHA256 `0572d79010820906d53518679912c150d487db8e0ea631f91dae88a2968b6d69`。
- `kg_distill_neg.parquet`：19080 行，SHA256 `110ce5ddcbea3784744311a490897685af5ac9b7aa31b912a4f410efeb68550e`。
- 两份样本都缺可信 `feature_available_at/label_end_at/label_available_at`、成熟证据、内容家族及价格口径；现有日期主要为无时区 `YYYYMMDD`。已有 ret 值非空不能证明当时标签已成熟，不能直接进入正式时间外切分。
- 未找到按文件名识别的本地日历文件；这不等于不存在任何日历数据。Downloads 目录读取受权限限制，列为未核验。
- 云端开发包正文仍声明 v1.1/30 规则；本轮查看可见版本历史和 22 条目的父目录，未出现可下载 ZIP 入口，原包仍未取得。不据 UI 断言附件绝对不存在，未访问未展示的后台端点。

## 口径与边界

标签观察的是决策后第一个严格更晚收盘至后 H 个交易日收盘的变化，不是按历史拐点或理论极值成交，也不含手续费、滑点、停牌/涨跌停可成交性和账户约束。卖出观察不转换成做空利润。日历/证券对应/价格口径/来源哈希及 trusted 状态均需上游认证。

切分按标签结束和到达时间做 purge；同内容家族和同票同 UTC 日期的候选行传递关联成组，只保留最晚折。统计为描述性均值和正目标比例，未训练模型、调参或计算统计显著性。完整模型测试集冻结、注册和晋升尚未实施。

有剔除行时结果只保留 ID 与原因，摘要不能独立重建原输入、剔除原因或原输入哈希。必须保留原始请求，重跑并核对双哈希；摘要会重验保留折的时点、成熟、分组与归属。哈希提供一致性校验，不提供来源签名认证。

下一阶段优先接入可信交易日历与当前时点行情快照，保存来源/可用时间/复权版本；核验原始规则包后开展结构对照和正式时间外评估。现有旧样本继续留作审计，等待可核验来源证据。独立 paper、组合硬风控、教师限流、训练与模型晋升继续按后续阶段实施。

本批没有启动模型、训练、调度、推送、模拟成交或实盘交易，没有编辑或发布云端资料。

## 文件哈希

| 文件（相对 quant_backtest） | SHA256 |
| --- | --- |
| `ai_operator/cli.py` | `2982986A80D7AEB9ED5CF36104E8F859962AF6EC7C87D33BF0AA99A719F1E722` |
| `ai_operator/outcomes.py` | `529CD85685C00E2F9204C3EFF3EA505C8180EFBEBB1304ED68EE42BB990B2C04` |
| `ai_operator/dataset.py` | `E34D3347722E4F8A9A3360EED77A277775A1BD23DDFF572C2E3C40AAF5C7929F` |
| `tests_ai_operator/test_outcomes.py` | `311100B13BC3454F31A75A5DC926ED1ED38806692D5B1837EB54CD4C3516E810` |
| `tests_ai_operator/test_dataset.py` | `6C15B480E1693E645E4485B7028A380D5A9DFFE6CF047819056C96D9FE586562` |
| `tests_ai_operator/test_phase3_cli.py` | `89620DE4B7961795C7C677420C16C465955C120EAF6F3697A09FE805F6A42047` |

项目内文档和脚本统一归档到 `docs/ai_operator/phase3-*`、`phase3_acceptance.py`。
