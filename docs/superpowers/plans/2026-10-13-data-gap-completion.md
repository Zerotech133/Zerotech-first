# 数据缺口完善实施计划

## 目标

在保持 `production_write=false`、`global_activation=false` 的前提下，把健康报告中的缺口变成可复核的逐键证据：统一扫描窗口口径，明确整日来源缺失，接入估值字段只读覆盖，并对 JEV staging 与新闻库 pending 做对账。

## 实施步骤

- [x] 为覆盖指标记录 `window_start`、`window_end`、`reference_date` 和稳定 `gap_keys`，消除 46/51 键因窗口不同造成的歧义。
- [x] 将 0 行整日断面标记为 `source_empty_pending`，同时保留缺键统计，避免把远端未供给误报为普通缺失。
- [x] 将 `turnover`、`market_cap`、`pe` 的只读拼接覆盖、来源匹配行数和待分类空值接入健康报告与看板数据模型。
- [x] 新增 JEV staging/news pending 对账指标，区分已决策、队列待判断、新闻库待判断和孤儿回执。
- [x] 生成稳定逐键缺口摘要，所有无来源资格的键继续 `blocked`，不补零、不复制邻日、不写生产。

## 验证

先运行新增回归测试，再运行完整性、估值、健康页测试与 `compileall`。生产目录只读扫描结果写入 `work/`，不执行发布。
