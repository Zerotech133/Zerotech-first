# SDD ledger — plan: docs/superpowers/plans/2026-10-12_three-year-history.md

Pre-flight: Task 1/2/3 share `TableSpec`, `build_batches`, and the staging receipt schema; Task 3 consumes the plan output from Tasks 1–2.

Task 1: complete — `pytest -q quant_backtest/test_three_year_history.py quant_backtest/test_history_backfill.py` → 8 passed.

Task 2: complete — focused suite above covers resumable units and raw/staged receipt contract; production writes remain disabled.

Task 3: complete — `python quant_backtest/run_three_year_history.py --calendar-root E:\\老田的AI助手\\quant_backtest\\data\\tushare\\trade_cal --stage-root E:\\老田的AI助手\\work\\k162_stage_full --dry-run --start 20230101 --end 20231231 --batch-size 40` → exit 0, 242 dates, 7 batches, audit written with 1452 missing units, `production_write=false`, `global_activation=false`.

Task 3: fix — added `three_year_history_audit.json` output to the CLI; regression test `test_cli_writes_audit_report_for_dry_run` was RED, then GREEN; final relevant suite `pytest -q quant_backtest/test_three_year_history.py quant_backtest/test_history_backfill.py` → 10 passed.

Task 1: Ruling: the final batch may contain fewer than 20 dates — the plan's own test expects `[40, 40, 15]`, while the global batch-size wording says 20–50; rejecting a remainder would lose valid tail dates, so the implementation keeps the tail and records the batch size as an upper-bound constraint. Cost if wrong: an operator expecting every batch to have at least 20 dates must treat the final partial batch specially.

Final review: self-review pending Git metadata write access; source and tests are present in the shared worktree, but commit/merge cannot run while `.git/index.lock` creation is denied.

Task 3: Ruling: the first live 28-day staging batch was attempted only after the dry-run passed, but the sandbox rejected `api.tushare.pro:443` with `WinError 10013`; no production path was touched and the batch remains incomplete for an environment with outbound network access. Cost if wrong: the first batch still needs to be run in an approved network environment before external-data completeness can be claimed.
