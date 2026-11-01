# 100 卡建议 decisions · 二轮 dry-run 预览（2026-11-01）

> **本文件为 dry-run 预览：未 apply，未修改清单/学习卡**。
> decisions：`research/reports/card_review_decisions_suggested_round2_20261101.json`（sha256 `2fe07b688f252669…`）
> 数据源：`portal/static/card_review_data.json`（100 张；precheck pass 53 / revise 45 / reject 2）

## 一、建议 decisions 分布

| 判定 | 数量 | 说明 |
|---|---|---|
| pass → 通过 | 53 | 首轮 36 + 二轮 17 |
| revise → 需修改 | 45 | 首轮 24 + 二轮 21 |
| reject → 驳回 | 2 | 二轮 2 |
| **合计** | **100** | 100 张全部解析 |

## 二、decide dry-run 结果

| 项 | 值 |
|---|---|
| decisions 解析 | **100/100**（100/100） |
| 决策总数 decided | 100 |
| approved（可合并） | **53** |
| 现有学习卡 | 88 |
| **预计合并新增** | **17** |
| 跳过·concept 已存在 | 36 |
| 跳过·批内重复 | 0 |
| 合并后总卡数 | 105 |
| 合并后校验 | 通过 |
| 冲突（existing_wins，已跳过） | 36 |

## 三、轮次对账

| 轮次 | 总数 | pass | revise | reject |
|---|---|---|---|---|
| first | 60 | 36 | 24 | 0 |
| second | 40 | 17 | 21 | 2 |

## 四、预期合并影响与对账

- 二轮 pass **17** 为新增候选（本轮 dry-run 预计新增 17，其余见跳过项）；
- 首轮 revise **24** 落为「需修改」、二轮 revise **21** 同样待修改；二轮 reject **2** 驳回；
- 当前学习卡基线 88 张（与预期「首轮36已合并」存在差异时以实际 dry-run 为准，见 JSON reconciliation）。

## 五、安全说明

| 文件 | SHA256 前后一致 |
|---|---|
| checklist | ✅ 一致 |
| learning_cards | ✅ 一致 |
| card_review_data | ✅ 一致 |
| proposals_v3 | ✅ 一致 |

## 六、下一步（人工确认后执行）

1. 人工复核建议 decisions（重点：45 张 revise / 2 张 reject；二轮 CP3 条目注意轮次标注）
2. 确认后执行 card_review.py decide --decisions <本文件> --apply（需要与 100 条兼容的清单；本轮 dry-run 使用临时合并清单，真实清单仍为 60 条未改）
3. 如需合并进学习卡再追加 --merge（写前备份、写后校验）
4. 本文件与 decisions 文件均未触发真实写盘（清单/学习卡 SHA256 不变）

> 建议 decisions / 预览为机器辅助产物，最终由人工确认；不构成投资建议。
