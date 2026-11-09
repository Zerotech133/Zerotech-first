# 2026-11-08 review 冲突单 rebase 轮计划 v1

状态：执行中（老田 2026-11-08 18:12 决策：review 单按冲突单单独排 rebase 轮处置）
口径：只做 rebase/冲突解决/定向验证，不合入；合入仍走 16:30 主窗 / 21:40 次窗。

## 1. 数据快照（2026-11-08 18:26，master d015c0cc3）

| 任务分支 | tip | ahead | behind | merge-tree | 冲突文件 |
|---|---|---|---|---|---|
| task/WO-GUANYU-DATASEED-20261106 | abe301673 | 1 | 0 | rc=0 | 已解决（REPO 解析：env > common.REPO > _repo_root） |
| task/K-AUDIT-20261108-FULL-AUDIT | 5b9f7297f | 1 | 0 | rc=0 | 已解决（清单状态/1-2 项合并 master 事实+分支证据链接） |
| task/K-KG-BRIEF-STYLE-20261105 | ac4be6c38 | 2 | 0 | rc=0 | 已解决（meta 同时保留 last_secs 与 view） |
| task/WO-IFS-STD-20261103 | 68967879b | 1 | 1211 | rc=1（4） | docs/standards/...SLO_MATRIX.md、接口对接规范_IFS_v1.md、INTERFACE_REGISTRY_v1.yaml、tools/tests/test_interface_registry_contract.py |
| task/WO-HERMES-CONTRACT-20261103 | 326f8bb37 | 6 | 858 | rc=1（1） | tools/tests/test_hermes_audit.py |
| task/UI-UNIFY-BATCH-20261103 | 14f5d4b00 | 32 | 214 | rc=1（1） | portal/tests/test_round7_closeout.py |
| task/K-STOCK-ANALYSIS-GATEHEALTH-20261107 | f64af59fa | 9 | 176 | rc=1（6） | DEVELOPER_GUIDE.md、portal/api_chanlun_pages.py、portal/mixin_baseline.json、portal/static/nav.js、tools/portal_route_baseline.json、tools/tests/test_test_gate_org_gate.py |
| task/K-STOCK-ANALYSIS-F2-20261104 | e405a4dc5 | 15 | 200 | rc=1（2） | portal/static/nav.js、tools/portal_route_baseline.json |
| task/K-STOCK-ANALYSIS-GATEHEALTH2-20261108 | 256deefd4 | 13 | 1 | **rc=0（0）** | —（已无冲突） |

说明：GATEHEALTH2 已由并发会话推进至干净，可作 11-09 窗候选（仍需 PIN + fast 门）；其余 8 支按轻→重分三轮。

## 2. 轮次

- **R1（轻，1 文件冲突）2026-11-09 08:30–09:10**：WO-GUANYU-DATASEED、K-AUDIT、K-KG-BRIEF。
- **R2（中）2026-11-09 17:15–18:10**：WO-IFS-STD、WO-HERMES-CONTRACT、UI-UNIFY-BATCH。
- **R3（重，含基线/nav/route 再生成）2026-11-10 08:30–09:15**：GATEHEALTH、F2（若 R2 未消化，UI-UNIFY-BATCH 顺延至此）。
- GATEHEALTH2：排 11-09 16:30 队列尾或 21:40 次窗（需 PIN + fast 门，不得插队 P1/W1）。

## 3. 执行规则

1. 每支先确认其 worktree 存在、clean、无并发 rebase/门进程；否则跳过并记录。
2. `git -C <worktree> rebase master`；冲突解决原则：
   - 文档/登记表：以 master 版本为基线，保留本单新增段落/行（add/add 时合并语义，不整份覆盖）。
   - 代码：保留双方意图，优先 master 的类型/接口，回贴本单修复。
   - 路由/nav/mixin/nginx 基线：解决后按仓库工具再生成，不手改基线数字。
3. 定向测试（该单测试文件）+ `python tools/_mixin_integrity.py --check`（如涉及 portal）。
4. `merge_gated.py --dry-run <TID>` + `git merge-tree --write-tree master <ref>` rc=0 才算该轮完成。
5. 失败立即 `git rebase --abort`，保持原 tip，记录原因，转下一支；不合入、不 force 其他分支。
6. 每轮结束更新 `C:\Temp\review_disposition_1108.md` 与本文件进度表；轮次结果并入 11-09/11-10 回执。

## 4. 风险

- 分支 behind 较大（IFS 1211、HERMES 858、KG 642、UI 214），冲突可能含语义变更；以“能安全重放”为准，不强行合。
- 多会话并发：若分支被他人同时 rebase，以 `git reflog` 最新且 clean 的 tip 为准，不覆盖。
- 时间窗：rebase 不占合并窗，但 16:30–17:00 / 21:40–22:10 期间不启动重 rebase，避免与门/合并争抢。

## 4.1 R1 执行结果（2026-11-09 08:30–08:55）

| 分支 | 新 tip | 定向测试 | PIN/dry-run | merge-tree | 结果 |
|---|---|---|---|---|---|
| WO-GUANYU-DATASEED-20261106 | abe301673 | 37 passed（anchor 4 + seed + dataset_query） | rc=0 OK | rc=0 `7d5bf7b1` | 完成，未合 |
| K-AUDIT-20261108-FULL-AUDIT | 5b9f7297f | 文档单，无需代码测试 | rc=0 OK | rc=0 `5905c199` | 完成，未合 |
| K-KG-BRIEF-STYLE-20261105 | ac4be6c38 | 6 passed（view/card）；mixin 190/741 零 diff | rc=0 OK | rc=0 `c68b0ac5` | 完成，未合 |

轮次结论：三支冲突全部按“master 基线 + 本单新增/修复”语义解决；不合入、不 force；待 16:30/21:40 窗按队列评估。
