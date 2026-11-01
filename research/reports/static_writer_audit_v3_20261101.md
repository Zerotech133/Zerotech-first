# 静态产物写入脚本审计 v3（人工资产批次加固后 · 2026-11-01）

- 生成：2026-11-01T15:45:51+08:00；扫描 ['tools', 'portal']（28 个静态 JSON）
- 结果：**写盘脚本 17 个**（高风险 **0** / 中 1 / 低 16）；只读引用 35 个；涉及静态目标 14 个
- 高危目标：无

## 评级规则（启发式，需人工复核）

- **高**：人工/长期资产 + 无备份 + 无原子写 + 无合并旧数据（直接整文件覆盖）
- **中**：人工资产缺备份/原子写/合并之一（部分保护）；或生成数据无备份/原子写
- **低**：只读引用；或生成数据+三件套；或人工资产三件套齐全（含 safe_write 辅助函数识别）
- 人工资产名单：kb_learning_cards.json、kb_qa_samples.json、kb_units_all.json、report_panel.json、selection_daily.json

## v2 → v3 复审对比（人工资产批次：learning_cards / kb_units_snapshot 加固）

- v2（wave28 报告，复审基线）：写盘 17 个（高 0 / 中 8 / 低 9）
- v3（本次）：写盘 17 个（高 0 / 中 1 / 低 16）；辅助函数写入识别 13 个；降级 7、升级 0

| 脚本 | 目标 | v1 | v2 | 依据 |
|---|---|---|---|---|
| `tools\kb_corpus\card_revision_apply.py` | kb_learning_cards.json | 🟡 中 | 🟢 低 | 写人工资产（kb_learning_cards.json）但三件套齐全（safe_write 辅助函数：backup_then_atomic_write） |
| `tools\kb_corpus\kb_qa_bank_v2.py` | kb_qa_questions_v2.json | 🟡 中 | 🟢 低 | 生成数据可重算，且有备份/原子写保护 |
| `tools\kb_corpus\kb_qa_bank_v3.py` | kb_qa_questions_v3.json | 🟡 中 | 🟢 低 | 生成数据 + 三件套齐全（safe_write 辅助函数：backup_then_atomic_write） |
| `tools\kb_corpus\kb_qa_expand.py` | kb_qa_samples.json | 🟡 中 | 🟢 低 | 写人工资产（kb_qa_samples.json）但三件套齐全（safe_write 辅助函数：backup_then_atomic_write） |
| `tools\kb_corpus\kb_units_snapshot.py` | kb_units_all.json | 🟡 中 | 🟢 低 | 写人工资产（kb_units_all.json）但三件套齐全（safe_write 辅助函数：backup_then_atomic_write） |
| `tools\kb_corpus\learning_cards.py` | kb_learning_cards.json | 🟡 中 | 🟢 低 | 写人工资产（kb_learning_cards.json）但三件套齐全（safe_write 辅助函数：backup_then_atomic_write） |
| `tools\research\h18_watch_push.py` | h18_watch.json | 🟡 中 | 🟢 低 | 生成数据 + 三件套齐全（safe_write 辅助函数：backup_then_atomic_write） |

**降级确认**：`tools\kb_corpus\card_revision_apply.py`、`tools\kb_corpus\kb_qa_bank_v2.py`、`tools\kb_corpus\kb_qa_bank_v3.py`、`tools\kb_corpus\kb_qa_expand.py`、`tools\kb_corpus\kb_units_snapshot.py`、`tools\kb_corpus\learning_cards.py`、`tools\research\h18_watch_push.py`

## 高风险清单（优先整改）

- 无高风险脚本 ✅

## 全部写盘脚本清单

| 风险 | 脚本 | 目标 | 备份 | 合并旧数据 | 原子写 | 辅助函数 | 页面消费 |
|---|---|---|---|---|---|---|---|
| 🟡 中 | `tools\research\trend_template_export.py` | trend_template_latest.json | ❌ | ✅ | ❌ | — | portal\minervini.html |
| 🟢 低 | `tools\dw_dashboard.py` | dw_snapshot.json | ✅ | ✅ | ✅ | — | portal\dw.html |
| 🟢 低 | `tools\kb_corpus\card_revision_apply.py` | kb_learning_cards.json | ✅ | ✅ | ✅ | backup_then_atomic_write | portal\kb_cards.html、portal\kb_reports.html |
| 🟢 低 | `tools\kb_corpus\daily_briefing.py` | kb_daily_briefing.json | ✅ | ✅ | ✅ | backup_then_atomic_write | portal\kb_reports.html |
| 🟢 低 | `tools\kb_corpus\gov_status.py` | kb_corpus_gov.json | ✅ | ✅ | ❌ | — | portal\dw.html、portal\kb_corpus.html |
| 🟢 低 | `tools\kb_corpus\kb_ingest_status.py` | kb_ingest.json | ✅ | ✅ | ✅ | backup_then_atomic_write | portal\kb_ingest.html |
| 🟢 低 | `tools\kb_corpus\kb_qa_bank_v2.py` | kb_qa_questions_v2.json | ✅ | ❌ | ✅ | backup_then_atomic_write | — |
| 🟢 低 | `tools\kb_corpus\kb_qa_bank_v3.py` | kb_qa_questions_v3.json | ✅ | ✅ | ✅ | backup_then_atomic_write | — |
| 🟢 低 | `tools\kb_corpus\kb_qa_expand.py` | kb_qa_samples.json | ✅ | ✅ | ✅ | backup_then_atomic_write | portal\kb_reports.html |
| 🟢 低 | `tools\kb_corpus\kb_reports_gov.py` | kb_reports_gov.json | ✅ | ✅ | ✅ | backup_then_atomic_write | portal\dw.html |
| 🟢 低 | `tools\kb_corpus\kb_units_snapshot.py` | kb_units_all.json | ✅ | ✅ | ✅ | backup_then_atomic_write | portal\kb_reports.html |
| 🟢 低 | `tools\kb_corpus\learning_cards.py` | kb_learning_cards.json | ✅ | ✅ | ✅ | backup_then_atomic_write | portal\kb_cards.html、portal\kb_reports.html |
| 🟢 低 | `tools\kb_corpus\morning_brief_push.py` | h18_watch.json | ✅ | ✅ | ✅ | safe_write_json | portal\kb_reports.html |
| 🟢 低 | `tools\research\h14_shadow_status.py` | h14_shadow.json | ❌ | ✅ | ✅ | — | portal\h14_shadow.html |
| 🟢 低 | `tools\research\h18_watch_push.py` | h18_watch.json | ✅ | ✅ | ✅ | backup_then_atomic_write | portal\kb_reports.html |
| 🟢 低 | `tools\research\panel_daily.py` | report_panel.json | ✅ | ✅ | ✅ | backup_then_atomic_write | portal\selection.html |
| 🟢 低 | `tools\research\panel_export.py` | report_panel.json | ✅ | ✅ | ✅ | safe_write_json | portal\selection.html |

## 只读引用（低风险，仅提示）

- `portal\api_kb_read.py` → kb_corpus_gov.json、kb_daily_briefing.json、kb_ingest.json、kb_reports_gov.json、kb_units_all.json
- `portal\portal_api.py` → china_geo.json
- `tools\gen_bff_score_distribution.py` → bff_score_distribution.json
- `tools\kb_corpus\card_merge.py` → kb_learning_cards.json
- `tools\kb_corpus\card_precheck.py` → kb_learning_cards.json
- `tools\kb_corpus\card_precheck_v3.py` → card_proposals_v3.json、card_review_data.json、kb_learning_cards.json
- `tools\kb_corpus\card_proposal.py` → kb_learning_cards.json、kb_units_all.json
- `tools\kb_corpus\card_proposal_v2.py` → kb_units_all.json
- `tools\kb_corpus\card_proposal_v3.py` → card_proposals_v3.json、kb_learning_cards.json、kb_units_all.json
- `tools\kb_corpus\card_review.py` → kb_learning_cards.json
- `tools\kb_corpus\card_revision.py` → kb_learning_cards.json
- `tools\kb_corpus\concept_dedup.py` → kb_units_all.json
- `tools\kb_corpus\daily_kb.py` → kb_ingest.json、kb_learning_digest.json、kb_qa_samples.json、report_panel.json
- `tools\kb_corpus\eval_set_v3.py` → kb_units_all.json
- `tools\kb_corpus\eval_v3_baseline.py` → kb_units_all.json
- `tools\kb_corpus\kb_daily_push.py` → kb_daily_briefing.json、kb_daily_push.json、kb_learning_digest.json、report_panel.json
- `tools\kb_corpus\kb_qa.py` → kb_qa_samples.json
- `tools\kb_corpus\kb_qa_bank.py` → kb_qa_questions.json
- `tools\kb_corpus\kb_qa_eval.py` → kb_qa_samples.json、kb_units_all.json
- `tools\kb_corpus\kb_qa_eval_v2.py` → kb_qa_samples.json
- `tools\kb_corpus\kb_qa_schema_v2.py` → kb_qa_samples.json
- `tools\kb_corpus\kb_read_api.py` → kb_corpus_gov.json、kb_daily_briefing.json
- `tools\kb_corpus\learning_analysis.py` → kb_learning_cards.json、kb_units_all.json
- `tools\kb_corpus\learning_card_push.py` → kb_learning_cards.json
- `tools\kb_corpus\learning_digest.py` → kb_daily_briefing.json、kb_learning_cards.json、kb_learning_digest.json
- `tools\kb_corpus\merge_sim.py` → kb_units_all.json
- `tools\kb_corpus\merge_sim_89.py` → kb_units_all.json
- `tools\kb_corpus\qa_acceptance.py` → kb_qa_samples.json
- `tools\kb_corpus\search_v2.py` → kb_units_all.json
- `tools\kb_corpus\static_writer_audit.py` → kb_learning_cards.json、kb_qa_samples.json、kb_units_all.json、report_panel.json、selection_daily.json
- `tools\research\selection_daily.py` → selection_daily.json
- `tools\research\shadow_ledger.py` → shadow_baseline.json、shadow_selection.json
- `tools\research\shadow_monitor.py` → shadow_monitor.json
- `tools\test_page_shell.py` → page_shell_migration.json
- `tools\test_page_shell_w4.py` → page_shell_migration.json

## 目标视角（谁在写、谁在读）

| 静态目标 | 风险 | 写脚本 | 页面消费 |
|---|---|---|---|
| trend_template_latest.json | 🟡 中 | `tools\research\trend_template_export.py` | portal\minervini.html |
| dw_snapshot.json | 🟢 低 | `tools\dw_dashboard.py` | portal\dw.html |
| h14_shadow.json | 🟢 低 | `tools\research\h14_shadow_status.py` | portal\h14_shadow.html |
| h18_watch.json | 🟢 低 | `tools\kb_corpus\morning_brief_push.py`、`tools\research\h18_watch_push.py` | portal\kb_reports.html |
| kb_corpus_gov.json | 🟢 低 | `tools\kb_corpus\gov_status.py` | portal\dw.html、portal\kb_corpus.html |
| kb_daily_briefing.json | 🟢 低 | `tools\kb_corpus\daily_briefing.py` | portal\kb_reports.html |
| kb_ingest.json | 🟢 低 | `tools\kb_corpus\kb_ingest_status.py` | portal\kb_ingest.html |
| kb_learning_cards.json | 🟢 低 | `tools\kb_corpus\card_revision_apply.py`、`tools\kb_corpus\learning_cards.py` | portal\kb_cards.html、portal\kb_reports.html |
| kb_qa_questions_v2.json | 🟢 低 | `tools\kb_corpus\kb_qa_bank_v2.py` | — |
| kb_qa_questions_v3.json | 🟢 低 | `tools\kb_corpus\kb_qa_bank_v3.py` | — |
| kb_qa_samples.json | 🟢 低 | `tools\kb_corpus\kb_qa_expand.py` | portal\kb_reports.html |
| kb_reports_gov.json | 🟢 低 | `tools\kb_corpus\kb_reports_gov.py` | portal\dw.html |
| kb_units_all.json | 🟢 低 | `tools\kb_corpus\kb_units_snapshot.py` | portal\kb_reports.html |
| report_panel.json | 🟢 低 | `tools\research\panel_daily.py`、`tools\research\panel_export.py` | portal\selection.html |

## 口径与局限

- 启发式扫描：写盘目标由变量赋值 + 写操作行匹配推断，**可能漏报/误报**；高风险项建议人工确认源码；
- 未执行任何写盘脚本、未修改代码；本报告只做静态审计；
- 备份/合并/原子写仅检测**迹象**（关键词/函数名），不保证运行时一定生效；
- 建议把本审计纳入发布前检查：高风险脚本改动需 review + 备份/原子写/合并三件套。
