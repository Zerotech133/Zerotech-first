# SDD ledger — plan: docs/superpowers/plans/2026-10-11-source-evidence-snapshot.md

Pre-flight: Task 1 produces candidate-source-snapshot-v0.3.json and candidate-eval-manifest-v0.3.json; Task 2 consumes them to generate E-drive artifacts; Task 3 consumes the snapshot for the RAG baseline. No conflicting interfaces found.

Ruling: The exact docs/plans/2026-10-08_量子双刀策略研究报告.md is absent from E:\老田的AI助手 and available indexes; retain an explicit blocker rather than substituting the 2026-10-09 report, because the source date and provenance differ.

Task 1: complete — tools/test_rebuild_academy_source_snapshot.py 8 passed; builder validates stable source_id, URI-formatted evidence refs, evidence status preservation, hash drift and missing evidence. Invalid evidence refs are rejected from the candidate source list.
Task 2: complete — E-drive snapshot v0.3 contains 13 readable sources; K-STOCK-099 and K-STOCK-103 have source IDs E-f65e3d180fb3260f and E-73a1a64f2c668c81; exact 2026-10-08 source and nine_dim hash drift remain explicit blockers.
Task 3: complete — RAG baseline rebuilt from the new snapshot: positive 3/3, refusal 2/2, source_recall@5=1.0, temporal evidence incomplete, decision=blocked_snapshot, model_calls=0, training_steps=0.

Final review: independent reviewer dispatched; no files changed by reviewer.

Implementation follow-up: evidence URI validation and `complete_but_not_substitute` preservation were added with regression tests. Fresh verification: builder tests 8 passed, isolated E-drive RAG tests 12 passed, and `py_compile` passed. `_mixin_integrity` and `unify_nav` guards pass with UTF-8 Python; the registered task remains `review` because its native detached worktree ref cannot be merged by name. No source, training, simulation or trading gate was enabled.

2026-10-12 follow-up: rebuilt the full E-drive index to `work/e-drive-inventory-refresh-2026-10-12` (267,412 files). `quant_backtest/nine_dim.py` now matches the refreshed index (`93721e...`, 61,671 bytes), so `hash_drift` is cleared. The exact 2026-10-08 report is still absent and seven sources still lack provable dates; the candidate snapshot and RAG baseline remain blocked.

Ruling: the first `--force --anchor 20261008` run exposed that the replay writer discarded all prior anchors; fixed `_load_replay_rows` so force replaces only requested anchors, with a regression test. The original ignored replay file was not recoverable from the workspace; restored the checked-in `v2arch` archive (51 anchors through 20260924) and merged the verified 20261008 snapshot result, preserving an explicit 52-anchor research set. A full historical rebuild from the 218-code snapshot was rejected because the historical candidate union is 4,374 codes and the snapshot is not qualified for that scope.
Verification: replay contains 5,502 rows across 52 anchors (`20240902`–`20261008`); `by_anchor` cache merged for all 52; related tests 27 passed, 7 skipped; `py_compile` passed; `global_activation=false`, `production_write=false`.

Qualification follow-up: generated `quant_backtest/data/tushare_staging/nine_dim_historical_source_gap_20261012.json`. The 51-anchor candidate union is 4,374 codes; the 20261008 snapshot covers 212 (4.8468%) and leaves 4,162 without as-of receipts. Full historical replay remains blocked until those codes have `forecast/report_rc/block_trade` receipts; no production or score write was attempted.

Batch 001: queried the next 50 missing codes for `forecast` (20260610..20261008), `report_rc` (20260908..20261008), and `block_trade` (20261008). All 150 requests returned `source_code=0`; payload and hashes are in `nine_dim_source_payload_20261012_batch001.json`. Merged snapshot coverage is now 262/4,374 (5.9899%), with 4,112 codes still missing; updated gate is `blocked_insufficient_historical_snapshot`, and production flags remain false.

Batch 002: queried the following 50 codes with the same windows; all 150 requests returned `source_code=0`. Merged snapshot coverage is now 312/4,374 (7.1331%), with 4,062 codes still missing. Payload: `nine_dim_source_payload_20261012_batch002.json`; gate remains blocked and no production write occurred.
