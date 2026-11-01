# 静态产物写入脚本审计（门户静态 JSON 覆盖风险）

- 生成：2026-11-01T14:31:47+08:00；扫描 ['tools', 'portal']（26 个静态 JSON）
- 结果：**写盘脚本 15 个**（高风险 **3** / 中 9 / 低 3）；只读引用 34 个；涉及静态目标 13 个
- 高危目标：h18_watch.json、kb_learning_cards.json、report_panel.json、selection_daily.json

## 评级规则（启发式，需人工复核）

- **高**：人工/长期资产 + 无备份 + 无原子写（直接整文件覆盖）
- **中**：生成数据可重算；或人工资产缺备份/原子写之一；或缺合并旧数据逻辑
- **低**：只读引用 / 生成数据且有备份+原子写
- 人工资产名单：kb_learning_cards.json、kb_qa_samples.json、kb_units_all.json、report_panel.json、selection_daily.json

## 高风险清单（优先整改）

| 脚本 | 目标 | 缺控制 | 原因 |
|---|---|---|---|
| `tools\kb_corpus\learning_cards.py` | kb_learning_cards.json | backup、atomic_write | 写人工/长期资产（kb_learning_cards.json）；无备份迹象 |
| `tools\kb_corpus\morning_brief_push.py` | h18_watch.json、selection_daily.json | backup、atomic_write | 写人工/长期资产（selection_daily.json）；无备份迹象 |
| `tools\research\panel_export.py` | report_panel.json | backup、atomic_write | 写人工/长期资产（report_panel.json）；无备份迹象 |

### 最小修复建议（不直接改代码）

**`tools\kb_corpus\learning_cards.py`**（kb_learning_cards.json）
- 写前备份：写前 copy2 到 research/reports/backups/<name>.<时间戳>.bak（保留最近 N 份）；
- 读-合并-写：先读旧文件，按 id/主键合并新数据（新增/更新而不是整文件替换），保留旧记录与未知字段；
- 原子写：写临时文件（同目录 .tmp）后 os.replace 到目标，避免半截文件；
- 变更确认：写前记录 sha256/条数，写后校验条数与差异 count；提供 --dry-run / 二次确认；
- 串行约束：加锁文件或检查其他会话是否在跑，禁止并发写同一静态资产。
- 证据：L140 `OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")`

**`tools\kb_corpus\morning_brief_push.py`**（h18_watch.json, selection_daily.json）
- 写前备份：写前 copy2 到 research/reports/backups/<name>.<时间戳>.bak（保留最近 N 份）；
- 读-合并-写：先读旧文件，按 id/主键合并新数据（新增/更新而不是整文件替换），保留旧记录与未知字段；
- 原子写：写临时文件（同目录 .tmp）后 os.replace 到目标，避免半截文件；
- 变更确认：写前记录 sha256/条数，写后校验条数与差异 count；提供 --dry-run / 二次确认；
- 串行约束：加锁文件或检查其他会话是否在跑，禁止并发写同一静态资产。
- 证据：L31 `WATCH.write_text(json.dumps({"generated_at": d.get("generated_at"), "date": d.get("date"),`

**`tools\research\panel_export.py`**（report_panel.json）
- 写前备份：写前 copy2 到 research/reports/backups/<name>.<时间戳>.bak（保留最近 N 份）；
- 读-合并-写：先读旧文件，按 id/主键合并新数据（新增/更新而不是整文件替换），保留旧记录与未知字段；
- 原子写：写临时文件（同目录 .tmp）后 os.replace 到目标，避免半截文件；
- 变更确认：写前记录 sha256/条数，写后校验条数与差异 count；提供 --dry-run / 二次确认；
- 串行约束：加锁文件或检查其他会话是否在跑，禁止并发写同一静态资产。
- 证据：L238 `out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")`

## 全部写盘脚本清单

| 风险 | 脚本 | 目标 | 备份 | 合并旧数据 | 原子写 | 页面消费 |
|---|---|---|---|---|---|---|
| 🔴 高 | `tools\kb_corpus\learning_cards.py` | kb_learning_cards.json | ❌ | ✅ | ❌ | portal\kb_cards.html、portal\kb_reports.html |
| 🔴 高 | `tools\kb_corpus\morning_brief_push.py` | h18_watch.json、selection_daily.json | ❌ | ✅ | ❌ | portal\kb_reports.html、portal\selection.html |
| 🔴 高 | `tools\research\panel_export.py` | report_panel.json | ❌ | ✅ | ❌ | portal\selection.html |
| 🟡 中 | `tools\kb_corpus\card_revision_apply.py` | kb_learning_cards.json | ✅ | ✅ | ❌ | portal\kb_cards.html、portal\kb_reports.html |
| 🟡 中 | `tools\kb_corpus\daily_briefing.py` | kb_daily_briefing.json | ❌ | ✅ | ❌ | portal\kb_reports.html |
| 🟡 中 | `tools\kb_corpus\kb_ingest_status.py` | kb_ingest.json | ❌ | ✅ | ❌ | portal\kb_ingest.html |
| 🟡 中 | `tools\kb_corpus\kb_qa_bank_v2.py` | kb_qa_questions_v2.json | ❌ | ❌ | ❌ | — |
| 🟡 中 | `tools\kb_corpus\kb_qa_bank_v3.py` | kb_qa_questions_v3.json | ❌ | ✅ | ❌ | — |
| 🟡 中 | `tools\kb_corpus\kb_qa_expand.py` | kb_qa_samples.json | ✅ | ✅ | ❌ | portal\kb_reports.html |
| 🟡 中 | `tools\kb_corpus\kb_reports_gov.py` | kb_reports_gov.json | ❌ | ✅ | ❌ | portal\dw.html |
| 🟡 中 | `tools\research\h18_watch_push.py` | h18_watch.json | ❌ | ✅ | ❌ | portal\kb_reports.html |
| 🟡 中 | `tools\research\panel_daily.py` | report_panel.json | ❌ | ✅ | ✅ | portal\selection.html |
| 🟢 低 | `tools\dw_dashboard.py` | dw_snapshot.json | ✅ | ✅ | ✅ | portal\dw.html |
| 🟢 低 | `tools\kb_corpus\gov_status.py` | kb_corpus_gov.json | ✅ | ✅ | ❌ | portal\dw.html、portal\kb_corpus.html |
| 🟢 低 | `tools\research\h14_shadow_status.py` | h14_shadow.json | ❌ | ✅ | ✅ | portal\h14_shadow.html |

## 只读引用（低风险，仅提示）

- `portal\api_kb_read.py` → kb_corpus_gov.json、kb_daily_briefing.json、kb_ingest.json、kb_reports_gov.json、kb_units_all.json
- `portal\portal_api.py` → china_geo.json
- `tools\gen_bff_score_distribution.py` → bff_score_distribution.json
- `tools\kb_corpus\card_merge.py` → kb_learning_cards.json
- `tools\kb_corpus\card_precheck.py` → kb_learning_cards.json
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
| h18_watch.json | 🔴 高 | `tools\kb_corpus\morning_brief_push.py`、`tools\research\h18_watch_push.py` | portal\kb_reports.html |
| kb_learning_cards.json | 🔴 高 | `tools\kb_corpus\card_revision_apply.py`、`tools\kb_corpus\learning_cards.py` | portal\kb_cards.html、portal\kb_reports.html |
| report_panel.json | 🔴 高 | `tools\research\panel_daily.py`、`tools\research\panel_export.py` | portal\selection.html |
| selection_daily.json | 🔴 高 | `tools\kb_corpus\morning_brief_push.py` | portal\selection.html |
| kb_daily_briefing.json | 🟡 中 | `tools\kb_corpus\daily_briefing.py` | portal\kb_reports.html |
| kb_ingest.json | 🟡 中 | `tools\kb_corpus\kb_ingest_status.py` | portal\kb_ingest.html |
| kb_qa_questions_v2.json | 🟡 中 | `tools\kb_corpus\kb_qa_bank_v2.py` | — |
| kb_qa_questions_v3.json | 🟡 中 | `tools\kb_corpus\kb_qa_bank_v3.py` | — |
| kb_qa_samples.json | 🟡 中 | `tools\kb_corpus\kb_qa_expand.py` | portal\kb_reports.html |
| kb_reports_gov.json | 🟡 中 | `tools\kb_corpus\kb_reports_gov.py` | portal\dw.html |
| dw_snapshot.json | 🟢 低 | `tools\dw_dashboard.py` | portal\dw.html |
| h14_shadow.json | 🟢 低 | `tools\research\h14_shadow_status.py` | portal\h14_shadow.html |
| kb_corpus_gov.json | 🟢 低 | `tools\kb_corpus\gov_status.py` | portal\dw.html、portal\kb_corpus.html |

## 口径与局限

- 启发式扫描：写盘目标由变量赋值 + 写操作行匹配推断，**可能漏报/误报**；高风险项建议人工确认源码；
- 未执行任何写盘脚本、未修改代码；本报告只做静态审计；
- 备份/合并/原子写仅检测**迹象**（关键词/函数名），不保证运行时一定生效；
- 建议把本审计纳入发布前检查：高风险脚本改动需 review + 备份/原子写/合并三件套。
