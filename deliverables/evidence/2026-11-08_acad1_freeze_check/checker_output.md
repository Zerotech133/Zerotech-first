# ACAD1 11-08 冻结就绪检查（只读）

- schema: `acad1_freeze_ready/v1`
- generated_at: 2026-11-08T08:32:00+08:00
- 冻结判据：13/13 review 卡 reconciled + 池数一致（配额 30 vs 官方池 30）+ 无 blockers
- **ready=false**（reconciled 0/13）
- 只读：不联网、不写库/盘、不改 `compositions/acad1-legion/**`/registry、不代签

## 13 张 review 卡（逐张）

| # | 市场 | 代码 | 名称 | 状态 | diff% | 卡源 close | 数据仓 close | asof |
|---|---|---|---|---|---:|---:|---:|---|
| 1 | HK | 00268 | 00268 | unresolved | -1.1163 | 8.51 | 8.415 | 2026-10-30 |
| 2 | HK | 00700 | 00700 | unresolved | -1.5641 | 383.6 | 377.6 | 2026-10-30 |
| 3 | HK | 00857 | 00857 | unresolved | -5.3604 | 10.82 | 10.24 | 2026-10-30 |
| 4 | HK | 01024 | 01024 | unresolved | -1.1312 | 26.52 | 26.22 | 2026-10-30 |
| 5 | HK | 01211 | 01211 | unresolved | 0.5861 | 68.25 | 68.65 | 2026-10-30 |
| 6 | HK | 02583 | 02583 | unresolved | -15.1429 | 56.0 | 47.52 | 2026-10-30 |
| 7 | HK | 06681 | 06681 | unresolved | -6.7485 | 1.63 | 1.52 | 2026-10-30 |
| 8 | HK | 06810 | 06810 | unresolved | 14.2857 | 66.5 | 76.0 | 2026-10-30 |
| 9 | HK | 09987 | 09987 | unresolved | -3.9517 | 364.4 | 350.0 | 2026-10-30 |
| 10 | US | CART | CART | unresolved | -1.1557 | 57.11 | 56.45 | 2026-10-29 |
| 11 | US | CRL | CRL | unresolved | -4.4376 | 376.1 | 359.41 | 2026-10-29 |
| 12 | US | EFX | EFX | unresolved | 2.9043 | 179.39 | 184.6 | 2026-10-29 |
| 13 | US | RRC | RRC | unresolved | 1.3732 | 53.89 | 54.63 | 2026-10-29 |

## 汇总

| 项 | 值 |
|---|---|
| 预期 review 卡 | 13 |
| 登记卡总数 | 30 |
| review scope | 13（reconciled 0 / unresolved 13 / missing 0） |
| 其他卡 | ok 14 / missing 1 / unsupported 2 |
| blockers / errors / warnings | 20 / 0 / 1 |

## 池口径

| 项 | 值 |
|---|---|
| 冻结配额 | 30（{'CN': 12, 'HK': 10, 'US': 6, 'CRYPTO': 2}） |
| 卡登记 | 30 张（{'CN': 12, 'HK': 10, 'US': 6, 'CRYPTO': 2}）match_quota=True |
| 离线 shadow 池 | 45（official=false）match_quota=False |
| 官方单日视图 | 12 |
| 官方聚合视图 | 30 |
| 一致 | False |

## blockers（fail-visible，20 条）

- `review_signoff_registry_missing`  ：无 D1/ACAD1 逐张复核登记文件；本次不建第二真源、不代签，13 卡按未 reconciled 计
- `review_card_unreconciled` HK 00268：未 reconciled（登记 reconciliation_status=review）；卡源 close=8.51 vs 数据仓 close=8.415（diff -1.1163%，asof 2026-10-30）
- `review_card_unreconciled` HK 00700：未 reconciled（登记 reconciliation_status=review）；卡源 close=383.6 vs 数据仓 close=377.6（diff -1.5641%，asof 2026-10-30）
- `review_card_unreconciled` HK 00857：未 reconciled（登记 reconciliation_status=review）；卡源 close=10.82 vs 数据仓 close=10.24（diff -5.3604%，asof 2026-10-30）
- `review_card_unreconciled` HK 01024：未 reconciled（登记 reconciliation_status=review）；卡源 close=26.52 vs 数据仓 close=26.22（diff -1.1312%，asof 2026-10-30）
- `review_card_unreconciled` HK 01211：未 reconciled（登记 reconciliation_status=review）；卡源 close=68.25 vs 数据仓 close=68.65（diff 0.5861%，asof 2026-10-30）
- `review_card_unreconciled` HK 02583：未 reconciled（登记 reconciliation_status=review）；卡源 close=56.0 vs 数据仓 close=47.52（diff -15.1429%，asof 2026-10-30）
- `review_card_unreconciled` HK 06681：未 reconciled（登记 reconciliation_status=review）；卡源 close=1.63 vs 数据仓 close=1.52（diff -6.7485%，asof 2026-10-30）
- `review_card_unreconciled` HK 06810：未 reconciled（登记 reconciliation_status=review）；卡源 close=66.5 vs 数据仓 close=76.0（diff 14.2857%，asof 2026-10-30）
- `review_card_unreconciled` HK 09987：未 reconciled（登记 reconciliation_status=review）；卡源 close=364.4 vs 数据仓 close=350.0（diff -3.9517%，asof 2026-10-30）
- `review_card_unreconciled` US CART：未 reconciled（登记 reconciliation_status=review）；卡源 close=57.11 vs 数据仓 close=56.45（diff -1.1557%，asof 2026-10-29）
- `review_card_unreconciled` US CRL：未 reconciled（登记 reconciliation_status=review）；卡源 close=376.1 vs 数据仓 close=359.41（diff -4.4376%，asof 2026-10-29）
- `review_card_unreconciled` US EFX：未 reconciled（登记 reconciliation_status=review）；卡源 close=179.39 vs 数据仓 close=184.6（diff 2.9043%，asof 2026-10-29）
- `review_card_unreconciled` US RRC：未 reconciled（登记 reconciliation_status=review）；卡源 close=53.89 vs 数据仓 close=54.63（diff 1.3732%，asof 2026-10-29）
- `evidence_gap` CRYPTO ETH/USDT：市场不支持（unsupported，如 CRYPTO 无本仓表）
- `evidence_gap` CRYPTO PROM/USDT：市场不支持（unsupported，如 CRYPTO 无本仓表）
- `evidence_gap` HK 07552：数据缺失（missing_data），不可分析/不可冻结
- `pool_shadow_mismatch`  ：离线 shadow 池 total=45 ≠ 冻结配额 30（官方真源=PG，JSON 池为 shadow 非官方）
- `pool_single_view_mismatch`  ：官方池单日视图 count=12 ≠ 冻结配额 30（PG 按 max(pool_date) 单日读；每市场最新聚合=30，跨 4 个 pool_date）
- `manifest_not_validated`  ：manifest status.validated=False（lifecycle=draft）

## 输入错误 / warnings

- errors: 无
- WARN review_signoff_registry_missing:未提供人工复核登记文件（--reviewed 可注入）；13 张 review 卡按登记 status=review 计为未 reconciled，不代签

## 未做 / 边界

- 本工具只读盘点，不改 compositions/acad1-legion、不改 registry、不写库/盘/联网、不代签；
- 人工复核结论以 D1/ACAD1 正式登记为准（`--reviewed` 注入）；11-08 冻结执行由 Lead/ACAD1 决策。
- 池官方真源=PG（本工具不连 PG，只读现存审计文档/离线 shadow 登记）；PG 只读实测见审计文档。

rc=2

