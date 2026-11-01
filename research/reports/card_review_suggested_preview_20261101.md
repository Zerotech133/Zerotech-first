# 学习卡建议 decisions · dry-run 预览（2026-11-01）

> **本文件仅为预览：未 apply，未修改 checklist / proposals / 学习卡**。
> 建议 decisions：`research/reports/card_review_decisions_suggested_20261101.json`（sha256 `a188ab46c1d9da71…`）
> 依据：wave18 BC 机器预审（pass 36 / revise 24 / reject 0）
> 生成于 2026-11-01T10:58:49+08:00 · 模式 = **dry-run**（`--apply` 未执行）

## 一、建议 decisions 分布

| 维度 | 数量 |
|---|---|
| 决策总数 | 60 / 60 |
| pass（预审 pass → 建议通过） | 36 |
| revise（预审 revise → 建议修改后再审） | 24 |
| reject（不自动 reject） | 0 |
| pending（未决策） | 0 |

## 二、dry-run：若按建议执行（仅预览）

| 项 | 值 |
|---|---|
| 将更新的卡片状态数 | **60**（pass 36 / revise 24） |
| approved（可合并）卡 | 36 |
| 预计合并新增卡 | **36** |
| 跳过·concept 已存在（现有优先） | 0 |
| 跳过·批内重复 | 0 |
| 合并后总卡数 | 56（现有 20 + 新增） |
| 合并后校验 | 通过 |
| 冲突数 | 0 |

## 三、将新增卡片（前 15）

| review_id | 主题 | 概念 |
|---|---|---|
| `REV-20261101-001` | AI算力/硬件 | Akamai 与 Anthropic 合作协议 |
| `REV-20261101-002` | AI算力/硬件 | Meta Muse 产品安全漏洞 |
| `REV-20261101-003` | 其他 | 景嘉微股权激励计划 |
| `REV-20261101-004` | 其他 | DeepSeek DSec 系统架构 |
| `REV-20261101-005` | 新能源/电力 | 钙钛矿光伏电池测试标准 |
| `REV-20261101-006` | 新能源/电力 | 电力设备板块周度表现 |
| `REV-20261101-007` | 金融/地产 | 购房贷款贴息政策 |
| `REV-20261101-010` | 消费/零售 | 北京零售市场现状 |
| `REV-20261101-013` | 量学/交易 | 十月怀胎一朝分娩 |
| `REV-20261101-014` | 量学/交易 | 倍量伸缩 |
| `REV-20261101-015` | 机械/制造 | 进口产品排名 |
| `REV-20261101-018` | 军工/航天 | 国防军工行业周度表现 |
| `REV-20261101-021` | 宏观/政策 | 数字贸易增长预测 |
| `REV-20261101-022` | 宏观/政策 | 大模型商业化核心指标 |
| `REV-20261101-025` | 消费/零售 | 上海零售市场动态 |
| … | 共 36 张 | |

## 四、下一步（人工确认后执行）

1. 人工复核建议 decisions（重点看 24 张 revise；可直接编辑 decisions JSON 的 verdict/notes）
2. 复核通过后执行：python tools/kb_corpus/card_review.py decide --decisions <该文件> --apply（先备份清单）
3. 如需合并进学习卡，再追加 --merge（写前备份 kb_learning_cards.json，写后校验）
4. 本文件与 decisions 文件均未触发任何真实写盘（checklist/proposals/学习卡 SHA256 不变）

## 五、安全自证

| 文件 | SHA256 前后一致 |
|---|---|
| checklist | ✅ 一致 |
| existing_cards | ✅ 一致 |
| ae_proposals | ✅ 一致 |
| av_proposals | ✅ 一致 |

> 建议 decisions / 预览为机器辅助产物，最终由人工确认；不构成投资建议。
