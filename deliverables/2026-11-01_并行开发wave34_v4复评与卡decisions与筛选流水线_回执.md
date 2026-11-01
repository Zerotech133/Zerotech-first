# 并行开发 wave34 · v4 默认复评 / 100卡decisions / 每日筛选流水线 · 回执

> 时间：2026-11-01 22:20–22:45；三条支线并行，Lead 集成+提交。

## 一、v4 默认灰度复评（稳定）
| 指标 | 扩样基线（v4，30条） | **本次复评（v4 默认，20条）** |
|---|---|---|
| 可答率 | 83.3% | **85.0%** |
| guard 通过率 | 86.7% | **85.0%** |
| fallback | 13.3% | 15.0% |
| 引用可解析/支撑 | 100%/100% | 100%/100% |
| 平均耗时 | 23.92s | 29.16s（同机负载略高） |
- 灰度生效校验：20/20 payload 均为 `prompt_version=v4`、`guard_marker_mode=segment`；
- 结论：**默认稳定（stable，risk_flagged=false）**，两项≥80% 阈值；新题 8/8 全可答；复测 12 条 75%（3 条 fallback 观察项）；
- 回滚：`KB_QA_PROMPT_VERSION=v1` + `KB_QA_MARKER_MODE=legacy`（CLI 即时生效）。

## 二、100 卡建议 decisions 二轮 dry-run（待你确认）
| 项 | 结果 |
|---|---|
| 解析 | 100/100，errors=[] |
| 分布 | pass 53 / revise 45 / reject 2 / pending 0 |
| 合并影响 | 现有 88 张 → approved 53 → 新增 **17** → **105 张**；冲突 36 条 existing_wins |
| 校验 | 合并后校验通过；未 apply（清单/学习卡 SHA256 不变） |
| 边界 | 清单仍 60 条；实际 apply 需先扩清单 v2 或让 decide 接受 CP3 |

## 三、每日筛选流水线（dry-run 就绪，未注册定时）
| 项 | 结果 |
|---|---|
| 链路 | 最新交易日 → S01 过滤 → S02 研究排序 → S03 H18 → 量学四因子信号 → 可选 RS overlay |
| 实测（20261030，--rs-mode C） | 5571→2583 合格、S02 top30、H18 20 条、量学 4437 只、RS_C 35 只、**量学∩RS 18 只** |
| 用法 | `python tools/research/daily_screening.py [--dry-run] [--date] [--rs-mode off/A/B/C]` |
| 定时 | **未注册**；审批后在行情同步（17:30）后调用，建议先每日 dry-run |

## 四、集成验收
| 项 | 结果 |
|---|---|
| 测试 | v4 复评 5 + 卡 decisions 5 + 筛选 6 = **16 passed** |
| registry | +3 摘要 |
| skill_registry | +2 → **110** |
| 门禁 | 语法/导航/路由/元数据（见执行记录） |

## 五、下一步 / 待你
1. **学习卡 100 张**：建议 decisions 已备（pass53/revise45/reject2）→ 你确认后我扩清单 v2 并 apply/merge（预计 105 张）；
2. **三件审批**：8091 问答接线（v4/segment 已可灰度）、每日自动化定时任务（流水线已就绪）、飞书推送；
3. v4 默认继续灰度观察，3 条 fallback 复测样本列入抽查。

## 六、交付清单
- v4 复评：`tools/kb_corpus/kb_qa_v4_default_recheck.py`、测试、`research/reports/kb_qa_v4_default_recheck_20261101.{json,md}`
- 100 卡：`research/reports/card_review_decisions_suggested_round2_20261101.json`、`card_review_decisions_round2_preview_20261101.{json,md}`、`tools/tests/test_card_decisions_round2.py`
- 流水线：`tools/research/daily_screening.py`、`tools/tests/test_daily_screening.py`、`docs/plans/2026-11-01_每日筛选流水线_v1.md`