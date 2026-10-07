# 第三批运行说明：成熟标签和时间外切分

目标源码目录：`E:\老田的AI助手\quant_backtest`。新增 `outcome-label`、`dataset-split`、`dataset-summary` 三个只读命令，输出 JSON，不写数据库、不训练模型。标准库 Python 3.11 即可运行。

## 1. 标签生成

```powershell
Set-Location -LiteralPath 'E:\老田的AI助手\quant_backtest'
& 'C:\Program Files\Python311\python.exe' -B -m ai_operator outcome-label --input 'C:\研究目录\label-request.json'
```

请求固定包含 `pack/decision/bars/calendar/source/evaluated_at`，可选 `horizons` 默认 `[5,20]`。pack 和 decision 为第二批完整事实包、完整影子建议；事件导出的 decision payload 若含 `fact_event_id`，应取完整原决策字段作为输入。模块会重建建议核对所有字段、事实包和决策哈希，不能只提供一个决策 ID。

```json
{
  "calendar":{"version":"已核验日历版本","sessions":[
    {"session":"2026-10-08","close_at":"2026-10-08T15:00:00+08:00"}
  ]},
  "bars":[{"session":"2026-10-08","close":10.5,
    "close_at":"2026-10-08T15:00:00+08:00","available_at":"2026-10-08T15:01:00+08:00"}],
  "source":{"source_id":"来源快照ID","source_hash":"64位小写SHA256",
    "price_basis":"raw","availability_status":"trusted"},
  "evaluated_at":"2026-10-09T16:00:00+08:00","horizons":[5,20]
}
```

上面只展示行情/日历/来源部分格式，完整请求还需 pack、decision；示例不是可验证的真实行情。`horizons` 为 1–10 个唯一整数，单项 1–250。日历和行情各最多 5000 项；日期唯一升序、明确收盘时区。行情必须属于日历且收盘时间是同一瞬间；允许多根行情同批到达。

参考点是日历中第一个收盘瞬间严格晚于建议 `as_of` 的交易日，终点再向后 H 个交易日。目标为 `end_close/reference_close - 1`，固定 `forward_close_return`。不按过去拐点成交，不把缺行情跳过后当下一交易日，也不把卖出观察翻成做空利润；这不是含交易成本的策略收益。

状态与目标：

| 状态 | 原因 | 行为 |
| --- | --- | --- |
| `matured` | 完整窗口行情均已到达评估时点 | 输出两端收盘价、目标和整个窗口最晚可用时间 |
| `pending` | `calendar_incomplete/missing_bar/not_available` | 价格、目标、标签可用时间为 null |
| `audit_only` | `unknown_source/unknown_availability` | 仅审计，不产生训练目标 |

`availability_status=trusted` 是调用方声明，source_hash 为声明的快照身份，日历版本也由调用方提供。模块只验证格式和相对时间，不认证供应商历史到达、官方日历、证券对应关系或价格复权。`raw/qfq_snapshot/hfq_snapshot` 必须固定口径；后复权/前复权快照的历史可用性仍须审核。缺历史可用时间的旧资料不能改填旧日收盘或当前文件修改时间。

## 2. 特征白名单与时间切分

```powershell
& 'C:\Program Files\Python311\python.exe' -B -m ai_operator dataset-split --input 'C:\研究目录\dataset-request.json'
```

请求包含 `rows/feature_allowlist/train_end/validation_end/test_end/as_of`，可选 `embargo_seconds`，默认 0。白名单必须显式非空；特征完整覆盖白名单，值为有限数值，不接受 bool、缺失值或额外字段；已知后验字段不能列入白名单。

每行固定字段：

```json
{
  "sample_id":"唯一样本ID","code":"600000.SH","content_family_id":null,
  "decision_at":"2026-01-02T08:00:00Z","feature_available_at":"2026-01-02T07:59:00Z",
  "label_end_at":"2026-01-09T07:00:00Z","label_available_at":"2026-01-09T07:01:00Z",
  "features":{"pivot_count":2,"bi_count":8},"target":0.03,
  "target_kind":"forward_close_return","horizon":5,"label_policy":"审核后的固定标签政策版本"
}
```

全数据集同一 horizon、label_policy 和 target_kind；买点、卖点、公告文本或不同标签任务由调用方分别组装。时间满足 `feature_available_at <= decision_at <= label_end_at <= label_available_at`。未知时点、未成熟 null 目标的结果不能补成 0 后送入数据集。特征白名单只限定字段，值是否来自正确历史快照及内容家族身份仍需来源审核。

边界满足 `train_end < validation_end < test_end <= as_of`。训练与验证采用半开区间，标签结束时间和到达时间必须严格早于该折结束；正好压线也剔除。验证和测试开始前应用指定秒数的 embargo。测试标签必须在全局 `as_of` 已可用。

剔除原因：`immature/purge_train/purge_validation/embargo/outside_window/group_overlap`。相同非空内容家族，或同证券同 UTC 决策日，会连成同组；传递关联也生效。同组跨折只保留最晚折的行，较早折剔除，不把行迁移到别的时间折。UTC 日期分组是本批明确口径，不自动等同市场交易日。

结果包含 `input_hash` 和 `split_hash`：前者绑定全部规范化输入行，包括被剔除行的特征与目标；后者绑定完整切分结果。输入行顺序改变不影响结果。**必须同时保留原始 dataset-request.json**：结果只保存剔除 ID 与原因，有剔除行时仅凭 split 无法恢复原输入或独立复核原剔除决定。

## 3. 描述性评估

```powershell
& 'C:\Program Files\Python311\python.exe' -B -m ai_operator dataset-summary --input 'C:\研究目录\split-result.json'
```

输入为完整 dataset-split 返回值。摘要会验证 Schema、哈希、保留样本的时间、成熟度、分组和折归属，防止仅重算哈希后混入非法样本；无剔除行时可重算核对 input_hash。有剔除行时 input_hash 只能检查格式和完整结果覆盖关系，原输入须另行核对。

输出各折样本数、目标均值、正目标比例及剔除原因计数；空折统计为 null。没有训练、调参、统计显著性或聚类置信区间，也没有冻结模型测试集注册、模型晋升和账户净值。所有产物 `production_eligible=false`。

## 4. 重复工程验收

项目内 `docs/ai_operator/phase3_acceptance.py` 会生成明确标注的夹具并核对影子事实→标签→白名单行→切分→摘要；它不是正式市场数据适配器。可在目录已存在且可写的研究目录输出：

```powershell
& 'C:\Program Files\Python311\python.exe' -B 'E:\老田的AI助手\quant_backtest\docs\ai_operator\phase3_acceptance.py' --operator-root 'E:\老田的AI助手\quant_backtest' --output 'C:\研究目录\验收.json' --artifact-dir 'C:\研究目录\fixtures'
& 'C:\Program Files\Python311\python.exe' -B -m unittest discover -s tests_ai_operator
```

本轮生成的夹具日历、行情、来源和版本均带 engineering-fixture 标记；`trusted` 仅在夹具内表示人为已知的接收过程，不是正式供应商认证。夹具描述性数字不能作为策略收益或训练质量证据。
