# 撮合验收口径 v1.1（acceptance_criteria_v1 · K-STOCK-191 版本化 / 209 修订）

> 版本：**v1.1**（v1 的 10-12 当日修订，未及正式验收故原位修订不另立 v2）· 生效：2026-10-13 16:00 首验 · 拍板：老田 2026-10-12（C1 三选一选 B）
> 判据器：`k_stock/paper/acceptance_check.py`（本文件为口径唯一权威源，改口径必升版本并在验收报告标注所用版本）

## 一、五判据口径表（v1.1 定版）

| 判据 | v1.1 口径 | 判定 | 拍板/依据 |
|---|---|---|---|
| C1 CN filled | **批次口径**：`plan_date=--plan-day`（默认 20261012）批次内 `filled >= 4`（main/chaser，fill 字段齐全；**成交日允许跨验收日**） | PASS/FAIL | **B（≥ 判据）**：超额成交是好消息不判 FAIL；批次内残留 pending 降为观察项 `pending_watch` 不阻塞 |
| C2 US filled | **批次口径**：同批次内 `filled == 6` 且批次内 pending == 0（us_qingping） | PASS/FAIL | 硬等式：US 无超额单源；数据源延迟致 pending 保持=如实 FAIL（归因清晰） |
| C3 止损自动挂 | US 持仓 stop_price 全非空（核心）+ chaser 同断言；main 只展示不并判据；**US 无持仓=SKIP（判据未激活，192）** | PASS/FAIL/SKIP | 分层断言（069c 口径）；接线已核实（G2=cli settle US 段 + trainee auto_stops） |
| C4 快照首战 | positions_snapshot **验收日(--date)** 行数 > 0（双日期形态兼容） | PASS/FAIL | K-STOCK-189 修复 |
| C5 幂等 | 同日同 ref（非空）至多一条 filled | PASS/FAIL | K-STOCK-189 修复（空 ref 剔除） |

### v1.1 修订记录（K-STOCK-209，2026-10-12 21:50）

- **C1/C2 从「成交按 fill_date=验收日」改为批次口径**。根因：验收清单制定时（10-10）假设 CN+US 都在 10-13 成交，实况 CN 10-12 已成交、US 10-13 才成交——**跨两日**，v1 口径在 `--date 20261013` 下 C1 必假 FAIL（实测 filled=0）。
- 判据原文「把 4 笔 CN pending 转 filled」的主语本就是**批次**（plan=20261012 落的单），v1 的 fill_date 过滤系口径带偏。
- 批次隔离（plan_date=固定日）天然免疫历史/未来批次混入——201 的防混入诉求由此继承（201 的 fill_date==day 过滤在跨日场景反而成了假 FAIL 根源，已由批次口径取代）。
- `--date` 语义收窄为验收日（管 C4/C5/总判语境）；新增 `--plan-day`（默认 20261012）管 C1/C2 批次圈定。
- 三日期实测：20261013/20261012 下 C1 均 PASS（filled=5≥4，批次内 pending_watch=281 观察）；C4 随 --date 正确变化。

## 二、账户覆盖分层（B-02 多组合口径 v1）

| 层级 | 账户 | 处理 |
|---|---|---|
| 硬判据 | main / chaser（CN）· us_qingping（US） | 进五判据与总判 |
| **observe（观察级）** | **pf_002**（组合跟单 07:20） | **不进总判**，验收报告单列观察段落；转 enforce 需升 v2 并老田拍板 |

## 三、总判与退出码

- 总判 = 五判据硬判定全 AND（SKIP=判据未激活不算 FAIL）
- exit 0 = PASS / exit 1 = FAIL / exit 2 = db 缺失
- pending_watch、observe 层、main 止损展示均**不影响总判**，只进明细

## 四、演进规则

1. 改任何判定语义（阈值/等式/账户分层）→ 新建 acceptance_criteria_v2.md，判据器 docstring 同步指向
2. 判据器 bug 修复（如 189 两处）不改口径，无需升版本，commit 记录即可
3. 每次正式验收报告头部标注「口径版本：v1.1」+ 拍板/修订日期

## 五、拍板记录

- 2026-10-12：C1 三选一 → **老田拍 B（≥ 判据）**；同日判据器两 bug 修复（K-STOCK-189）、C2 settle 链路预检闭环（K-STOCK-190）
