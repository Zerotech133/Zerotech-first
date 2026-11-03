# W1 测试债收口：curation 回归 + 7 个陈旧卡测试（2026-11-04）

> 范围：学习线测试债。目标：把「pipeline → curation → effective」变成可回归契约，并修复 3 个陈旧卡测试文件的 7 个失败（硬编码 104/124/130 卡与旧 demo/报告 sha）。未修改任何业务判决口径、未改生产数据根/知识库实文件、未 commit。

## 一、结论
| 项 | 结果 |
|---|---|
| 原失败 | **7 failed**（3 文件：`test_card_from_units.py` 1、`test_card_dedupe.py` 3、`test_card_proposal_v4.py` 3） |
| 修复后（3 文件） | **13 passed** |
| 新增 curation 回归 | **5 passed**（`test_learning_curation.py` 4 + `test_learning_pipeline_curation.py` 1） |
| 合计（5 文件） | **18 passed，0 failed（0.89s）** |
| 是否建议入 fast 套件 | 新增 2 个 curation 测试：**建议入**；3 个修好的卡测试：先入夜间 `tools` 套件观察 1 周，稳定后再入 `tools_key`（见第四节） |

## 二、7 个失败的原因与修复
共同根因：测试把**历史产物快照**当成实时约束硬编码——学习卡已从 124→130→400 张（扩卡），演示/去重报告是当时快照（旧 sha、旧卡数），数据演进后断言必然红。

| 文件 | 失败测试 | 失败原因 | 修复（动态断言 / 归档旧报告口径） |
|---|---|---|---|
| `test_card_from_units.py` | `test_demo_artifacts` | demo 记录 `sources.cards.sha256` 为生成时 130 卡 sha；`len(cards)==130` 硬编码 | 记录 sha 长度校验 + 与当前 sha 不一致时按「历史 demo（归档）」校验（`cards_unchanged=true`、记录卡数≥130）；当前卡数断言改为「只增不减 ≥130」 |
| `test_card_dedupe.py` | `test_cards_after_dedupe` | 硬编码 `len(cards)==104 == report.after.cards`；卡已扩至 400 | 新增 `fresh()`：报告 after sha/卡数 == 当前才做严格断言；否则按历史快照校验（当前卡数 > 报告快照、`validation.dup_concepts_after==[]`） |
| 同上 | `test_others_unchanged_and_backup` | 硬编码 `len(remaining)==len(cards)==104` 且与当前卡逐张比对 | 备份/105→104 快照自洽断言保留；仅 `fresh()` 时才与当前卡逐张比对 |
| 同上 | `test_report_rollback_digest_http` | `report.after.sha256 == sha(CARDS)`、digest sha == 当前 digest | `fresh()` 时才做当前文件 sha 校验；历史报告只校验记录完整/回滚路径存在/记录的 HTTP 200 |
| `test_card_proposal_v4.py` | `test_theme_depth_and_gaps` | `summary.cards_total == len(cards) == 124` | 报告自身快照自洽（`cards_total == preview.base_cards == sum(theme_depth.cards)`）；`archived()` 才跳过与当前卡数比较 |
| 同上 | `test_candidates_quality` | 候选去重基准取**当前** 400 卡 → 与生成时 124 卡下合法的候选重复 | 历史报告跳过「与当前卡重复」检查（改用报告生成时快照口径），保留批内去重/三角度/可追溯等断言 |
| 同上 | `test_dry_run_preview_and_no_mutation` | `preview.base_cards == 124 == len(cards)`、`resulting == 148`、sources sha 硬编码 | `base_cards == summary.cards_total == 124`、`resulting == base + 24`；sha 断言仅在未归档（fresh）时执行 |

原则：**不改业务判决口径**；报告内部一致性（前后卡数、sha 记录、决策/校验字段、MD 标记）全部保留严格断言，只把「与当前实时文件相等」的耦合改成「fresh 才严格 / 否则按历史快照归档校验」。

## 三、新增 curation 回归（契约：pipeline→curation→effective 幂等）

### `tools/tests/test_learning_curation.py`（4 tests）
- `test_helpers_lines_and_projects`：`lines()` 空/非空计数、`projects()` 读注册表返回非归档项目、`EXCLUDE_PAT=="指标说明书"`；
- `test_dry_run_counts_without_writing`：dry-run 不改 units、不写 effective、报告 mode=dry-run、计数自洽（4 行 → 排除 1 → effective 3）；
- `test_apply_dedupes_and_writes_effective`：apply 后精确重复行 4→3、effective 3→2 且不含「指标说明书」来源、报告计数/rc=0；
- `test_apply_idempotent`：连续两次 apply：units 不变、effective 不变、`units_after/effective/excluded/rc` 全相等（第二次 `units_before`=第一次 `units_after`）。

### `tools/tests/test_learning_pipeline_curation.py`（1 E2E test）
- 模拟真实 BUG：pipeline 重建写入「1 重复行 + 2 指标说明书 + 2 正常行」→ `learning_curation --apply` → 断言去重计数 `(5,4,2,2,0)`、effective 只留 `{u1,u4}`；
- **再次 pipeline 重建（覆盖治理结果）后再 curation**：计数/effective 与第 1 轮完全一致（治理可重复收敛、幂等），报告 md 覆盖生成。

隔离保证：全部跑在 pytest `tmp_path`（C:\Temp 下），monkeypatch `KB`/`R`/`projects`；`knowledge_dedupe` 子进程用 fake 替换，不写生产数据根、不写真实 research/reports、不联网。

## 四、fast 套件纳入建议（读 `tools/test_gate.ps1`）
现状：`fast = portal+kstock+ide+agent_key+quant_key+tools_key+datawarehouse`；`tools_key` 清单 14 个文件（release/merge/guard/academy/huangzhong 等），**不含学习卡/curation 测试**；`tools` 套件为夜间/手动全量。

| 测试文件 | 建议 | 理由 |
|---|---|---|
| `tools/tests/test_learning_curation.py` | **入 `tools_key`（fast）** | 纯 tmp 目录 + monkeypatch KB/R/projects + fake 子进程；无网络/无生产写；0.3s 级；直接守护 curation 契约 |
| `tools/tests/test_learning_pipeline_curation.py` | **入 `tools_key`（fast）** | E2E 幂等契约；同样完全沙箱化；0.3s 级 |
| `test_card_from_units.py` / `test_card_dedupe.py` / `test_card_proposal_v4.py` | 先入 `tools` 夜间套件，绿 1 周后评估入 `tools_key` | 只读 `portal/static` 历史快照，仍与真实数据演进耦合（本次 7 红即此因）；动态化后已稳，但保留观察期 |
| 长期建议 | 历史产物测试统一加「fresh/archived 双口径」模板 + 在 `tools/test_baseline.json` 记录已知历史断言 | 防同类数据演进再次制造批量假红 |

## 五、验证与复现
```powershell
# 5 个目标测试文件
C:\项目\py312\python.exe -X utf8 -m pytest -q `
  tools/tests/test_learning_curation.py tools/tests/test_learning_pipeline_curation.py `
  tools/tests/test_card_from_units.py tools/tests/test_card_dedupe.py tools/tests/test_card_proposal_v4.py
# → 18 passed in 0.89s
```
- 写入文件：本报告 + 上述 5 个测试文件（新增 2 / 修改 3）；未 commit；
- 未触碰：`C:\项目\知识库`、`portal/static` 生产文件、`research/reports` 历史报告（只读）、业务判决口径与生产数据根；
- 临时文件：pytest `tmp_path`（`C:\Temp` 下），测试结束自动清理。
