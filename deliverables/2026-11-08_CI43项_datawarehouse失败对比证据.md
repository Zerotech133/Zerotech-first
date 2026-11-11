# CI 43 项 datawarehouse 失败名单与 main/PR 0 差异证据

日期：2026-11-08

## 结论

- main 与 PR 的 datawarehouse 失败集合逐项一致：**43/43 差异 = 0**。
- main 失败 run：`42451545713`，job `128641650262`，commit `64f610c68`；PR 失败 run：`42452321003`，job `128643977479`，head `0bedb028e`。
- 两 run 均 `GATE FAIL: 新增失败 44 项`；datawarehouse 均 `37 failed / 407 passed / 6 errors`，suite 表除耗时外一致。
- 43 项完整名称来自诊断 run #26（tag `diag-500` → `0b82659db`，基于 64f，仅把 test_gate 打印上限 20→500；不改测试）：`https://github.com/Zerotech133/Zerotech-first/actions/runs/42467683983`。
- `git diff 64f610c68..0bedb028e -- tools/datawarehouse` 为空；PR 与 main 在 datawarehouse 测试代码上完全一致。

## 43 项名单（逐项）

| # | main | PR | diff |
|---:|---:|---:|---:|
| 1 | tools/datawarehouse/tests/test_acad1_research_datasources.py::TestDeclaredAndManifests::test_composition_validate_strict_no_new_errors[acad1-legion/manifest.yaml] | tools/datawarehouse/tests/test_acad1_research_datasources.py::TestDeclaredAndManifests::test_composition_validate_strict_no_new_errors[acad1-legion/manifest.yaml] | 0 |
| 2 | tools/datawarehouse/tests/test_dataset_query.py::test_sqlite_since_iso_text_matches_direct_sql | tools/datawarehouse/tests/test_dataset_query.py::test_sqlite_since_iso_text_matches_direct_sql | 0 |
| 3 | tools/datawarehouse/tests/test_dataset_query.py::test_files_since_works_and_counts | tools/datawarehouse/tests/test_dataset_query.py::test_files_since_works_and_counts | 0 |
| 4 | tools/datawarehouse/tests/test_dataset_query.py::test_files_order_size_numeric_desc | tools/datawarehouse/tests/test_dataset_query.py::test_files_order_size_numeric_desc | 0 |
| 5 | tools/datawarehouse/tests/test_dataset_refs_declared.py::TestCli::test_cli_resolve_all_six_ok | tools/datawarehouse/tests/test_dataset_refs_declared.py::TestCli::test_cli_resolve_all_six_ok | 0 |
| 6 | tools/datawarehouse/tests/test_dataset_refs_declared.py::TestCli::test_cli_unknown_ref_rc2 | tools/datawarehouse/tests/test_dataset_refs_declared.py::TestCli::test_cli_unknown_ref_rc2 | 0 |
| 7 | tools/datawarehouse/tests/test_dataset_refs_declared.py::TestCli::test_cli_declared_summary | tools/datawarehouse/tests/test_dataset_refs_declared.py::TestCli::test_cli_declared_summary | 0 |
| 8 | tools/datawarehouse/tests/test_dataset_refs_declared.py::TestInvalidDeclarations::test_planned_without_due_rejected | tools/datawarehouse/tests/test_dataset_refs_declared.py::TestInvalidDeclarations::test_planned_without_due_rejected | 0 |
| 9 | tools/datawarehouse/tests/test_dataset_refs_declared.py::TestInvalidDeclarations::test_duplicate_ref_rejected | tools/datawarehouse/tests/test_dataset_refs_declared.py::TestInvalidDeclarations::test_duplicate_ref_rejected | 0 |
| 10 | tools/datawarehouse/tests/test_dataset_refs_declared.py::TestInvalidDeclarations::test_missing_owner_rejected | tools/datawarehouse/tests/test_dataset_refs_declared.py::TestInvalidDeclarations::test_missing_owner_rejected | 0 |
| 11 | tools/datawarehouse/tests/test_dataset_refs_declared.py::TestInvalidDeclarations::test_bad_schema_rejected | tools/datawarehouse/tests/test_dataset_refs_declared.py::TestInvalidDeclarations::test_bad_schema_rejected | 0 |
| 12 | tools/datawarehouse/tests/test_dataset_refs_declared.py::TestInvalidDeclarations::test_missing_file_is_optional | tools/datawarehouse/tests/test_dataset_refs_declared.py::TestInvalidDeclarations::test_missing_file_is_optional | 0 |
| 13 | tools/datawarehouse/tests/test_dataset_refs_declared.py::TestInvalidDeclarations::test_injected_declared_ref_resolves | tools/datawarehouse/tests/test_dataset_refs_declared.py::TestInvalidDeclarations::test_injected_declared_ref_resolves | 0 |
| 14 | tools/datawarehouse/tests/test_dataset_refs_declared.py::TestReadOnlyAndBaseline::test_stats_baseline_unchanged | tools/datawarehouse/tests/test_dataset_refs_declared.py::TestReadOnlyAndBaseline::test_stats_baseline_unchanged | 0 |
| 15 | tools/datawarehouse/tests/test_dataset_refs_declared.py::TestReadOnlyAndBaseline::test_declared_loading_does_not_write | tools/datawarehouse/tests/test_dataset_refs_declared.py::TestReadOnlyAndBaseline::test_declared_loading_does_not_write | 0 |
| 16 | tools/datawarehouse/tests/test_dw_catalog_mcp.py::test_registry_stats_matches_file | tools/datawarehouse/tests/test_dw_catalog_mcp.py::test_registry_stats_matches_file | 0 |
| 17 | tools/datawarehouse/tests/test_dw_catalog_mcp.py::test_qualification_distribution | tools/datawarehouse/tests/test_dw_catalog_mcp.py::test_qualification_distribution | 0 |
| 18 | tools/datawarehouse/tests/test_dw_catalog_mcp.py::test_catalog_snapshot_real_values | tools/datawarehouse/tests/test_dw_catalog_mcp.py::test_catalog_snapshot_real_values | 0 |
| 19 | tools/datawarehouse/tests/test_dw_catalog_mcp.py::test_lifecycle_coverage | tools/datawarehouse/tests/test_dw_catalog_mcp.py::test_lifecycle_coverage | 0 |
| 20 | tools/datawarehouse/tests/test_dw_catalog_mcp.py::test_table_info_known_and_alias | tools/datawarehouse/tests/test_dw_catalog_mcp.py::test_table_info_known_and_alias | 0 |
| 21 | tools/datawarehouse/tests/test_dw_catalog_mcp.py::test_table_info_unknown_is_error | tools/datawarehouse/tests/test_dw_catalog_mcp.py::test_table_info_unknown_is_error | 0 |
| 22 | tools/datawarehouse/tests/test_dw_catalog_mcp.py::test_calls_are_readonly | tools/datawarehouse/tests/test_dw_catalog_mcp.py::test_calls_are_readonly | 0 |
| 23 | tools/datawarehouse/tests/test_dw_catalog_mcp.py::test_selftest | tools/datawarehouse/tests/test_dw_catalog_mcp.py::test_selftest | 0 |
| 24 | tools/datawarehouse/tests/test_dw_etl_dryrun.py::test_plan_is_dry_run_and_no_write | tools/datawarehouse/tests/test_dw_etl_dryrun.py::test_plan_is_dry_run_and_no_write | 0 |
| 25 | tools/datawarehouse/tests/test_dw_quality_mcp.py::test_mcp_call_manifest_and_backup | tools/datawarehouse/tests/test_dw_quality_mcp.py::test_mcp_call_manifest_and_backup | 0 |
| 26 | tools/datawarehouse/tests/test_dw_wp_i3.py::test_api_stats | tools/datawarehouse/tests/test_dw_wp_i3.py::test_api_stats | 0 |
| 27 | tools/datawarehouse/tests/test_dw_wp_i3.py::test_api_list_get_sample | tools/datawarehouse/tests/test_dw_wp_i3.py::test_api_list_get_sample | 0 |
| 28 | tools/datawarehouse/tests/test_dw_wp_i3.py::test_api_404_and_400 | tools/datawarehouse/tests/test_dw_wp_i3.py::test_api_404_and_400 | 0 |
| 29 | tools/datawarehouse/tests/test_dw_wp_i3.py::test_mcp_stdio | tools/datawarehouse/tests/test_dw_wp_i3.py::test_mcp_stdio | 0 |
| 30 | tools/datawarehouse/tests/test_dwh103_pg_temp_registry.py::test_cli_json_contract | tools/datawarehouse/tests/test_dwh103_pg_temp_registry.py::test_cli_json_contract | 0 |
| 31 | tools/datawarehouse/tests/test_kb_corpus_mcp.py::test_kb_status_and_books_via_read_api | tools/datawarehouse/tests/test_kb_corpus_mcp.py::test_kb_status_and_books_via_read_api | 0 |
| 32 | tools/datawarehouse/tests/test_kb_corpus_mcp.py::test_kb_search_returns_hits_and_respects_top | tools/datawarehouse/tests/test_kb_corpus_mcp.py::test_kb_search_returns_hits_and_respects_top | 0 |
| 33 | tools/datawarehouse/tests/test_kb_corpus_mcp.py::test_yt_tables_readonly_inventory | tools/datawarehouse/tests/test_kb_corpus_mcp.py::test_yt_tables_readonly_inventory | 0 |
| 34 | tools/datawarehouse/tests/test_kb_corpus_mcp.py::test_yt_query_whitelist_limit_and_text_cap | tools/datawarehouse/tests/test_kb_corpus_mcp.py::test_yt_query_whitelist_limit_and_text_cap | 0 |
| 35 | tools/datawarehouse/tests/test_kb_corpus_mcp.py::test_calls_do_not_modify_yt_db | tools/datawarehouse/tests/test_kb_corpus_mcp.py::test_calls_do_not_modify_yt_db | 0 |
| 36 | tools/datawarehouse/tests/test_kb_corpus_mcp.py::test_selftest_exit_zero | tools/datawarehouse/tests/test_kb_corpus_mcp.py::test_selftest_exit_zero | 0 |
| 37 | tools/datawarehouse/tests/test_pg_dsn_fallback.py::TestConnectFactory::test_empty_target_rejected | tools/datawarehouse/tests/test_pg_dsn_fallback.py::TestConnectFactory::test_empty_target_rejected | 0 |
| 38 | tools/datawarehouse/tests/test_dataset_refs_declared.py::TestResolveRef::test_all_six_refs_resolve | tools/datawarehouse/tests/test_dataset_refs_declared.py::TestResolveRef::test_all_six_refs_resolve | 0 |
| 39 | tools/datawarehouse/tests/test_dataset_refs_declared.py::TestResolveRef::test_active_and_planned_split | tools/datawarehouse/tests/test_dataset_refs_declared.py::TestResolveRef::test_active_and_planned_split | 0 |
| 40 | tools/datawarehouse/tests/test_dataset_refs_declared.py::TestResolveRef::test_token_alias_and_get | tools/datawarehouse/tests/test_dataset_refs_declared.py::TestResolveRef::test_token_alias_and_get | 0 |
| 41 | tools/datawarehouse/tests/test_dataset_refs_declared.py::TestResolveRef::test_official_pool_ref_registered | tools/datawarehouse/tests/test_dataset_refs_declared.py::TestResolveRef::test_official_pool_ref_registered | 0 |
| 42 | tools/datawarehouse/tests/test_dataset_refs_declared.py::TestResolveRef::test_list_file_dataset | tools/datawarehouse/tests/test_dataset_refs_declared.py::TestResolveRef::test_list_file_dataset | 0 |
| 43 | tools/datawarehouse/tests/test_dataset_refs_declared.py::TestResolveRef::test_declared_summary | tools/datawarehouse/tests/test_dataset_refs_declared.py::TestResolveRef::test_declared_summary | 0 |

## 证据命令

```powershell
git diff --name-only 64f610c68..0bedb028e -- tools/datawarehouse
# 输出为空
```

## 备注

- 诊断 run #26 额外显示 `agent_key` 1 项 new、总计 45，但 datawarehouse 仍 43；该项与本次 43 项无关，属环境侧波动。
- 6 errors 的完整 pytest 明细未由 test_gate 打印；测试节点名单已按 run #26 的 43 行输出逐项列出。
