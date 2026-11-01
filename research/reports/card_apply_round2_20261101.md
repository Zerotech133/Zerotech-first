# 100 卡 apply+merge 执行报告（2026-11-01）

> 任务：task-99（用户已授权执行）· 执行时间 2026-11-01T22:43:32+08:00

## 一、执行结果（真实数字）

| 项 | 值 |
|---|---|
| 清单 v1 | 60 条（未改，sha 一致=True） |
| 清单 v2 | **100 条**（AE20+AV40+CP3 40） |
| decisions | 100（pass53/revise45/reject2） |
| dry-run 解析 | **100/100**，approved 53 |
| 学习卡 | **88 → 105（+17）** |
| 合并后校验 | 通过 |
| 重复 concept | 后：[('涨停密码', 2)]；新增重复：无 |
| learning_digest | rc=0 |

## 二、HTTP 验证

| 目标 | 状态 |
|---|---|
| kb_learning_cards | 200 |
| kb_learning_digest | 200 |
| kb_reports_page | 200 |

## 三、操作清单

1. 小改 card_review.py：validate_checklist source 允许 ("AE","AV","CP3")（仅扩展，旧路径 AE/AV 不变）
2. 备份学习卡（88 张）→ `kb_learning_cards.20261101_224332.pre_round2.bak.json`
3. 备份清单 v1（60 条）→ `card_review_checklist_20261101_v1.20261101_224332.bak.json`
4. 生成清单 v2（100 条，source 支持 CP3）
5. dry-run：decide --checklist v2（100/100 解析，未写盘）
6. apply：更新 v2 清单状态（pass 53/revise 45/reject 2）
7. apply --merge：approved 53 → 现有 88 新增 17 → 105，写前备份+写后校验
8. 刷新 learning_digest，HTTP 200 验证

## 四、备份与回滚

- 学习卡备份：`C:\项目\老田的AI助手\research\reports\backups\kb_learning_cards.20261101_224332.pre_round2.bak.json`
- 清单 v1 备份：`C:\项目\老田的AI助手\research\reports\backups\card_review_checklist_20261101_v1.20261101_224332.bak.json`
- merge 备份：`C:\项目\老田的AI助手\research\reports\backups\kb_learning_cards.20261101_224332.bak.json`
- 回滚：copy /Y "C:\项目\老田的AI助手\research\reports\backups\kb_learning_cards.20261101_224332.pre_round2.bak.json" "C:\项目\老田的AI助手\portal\static\kb_learning_cards.json" 然后重跑 learning_digest.py run

> 本次为用户授权执行的学习卡合并操作；回滚路径见 backups/rollback；不构成投资建议。
