# 数据仓覆盖缺口清单（P1，20261103）

- 生成：2026-11-03T16:43:09+08:00；模式：**readonly（只写报告/草案；不改数据根）**；数据仓 catalog v35（256 表）
- 依据：`research\reports\dataset_inventory_20261102.json` + manifest_check/metadata_consistency/freshness_report
- 质量背景：W3-2 复检 8/8 PASS、metadata_consistency PASS（本次不重复修复数据）

## 一、总览

| 指标 | 数值 |
|---|---|
| 数据集（inventory） | **314** |
| 已注册 | 265（84.4%） |
| 已暴露 | 72（22.9%） |
| 未注册 / 未暴露 | **49** / **242** |
| manifest 真实缺口（按源） | **9**（tdx 4 / tushare 2 / xueqiu 3 / cnopendata 0） |
| manifest extra 键 | 22 |
| 陈旧 >30 天 | **17** |
| 无 latest 字段 | **59** |

### 按域

| 域 | 数据集 | 已注册 | 已暴露 | MB |
|---|---|---|---|---|
| 接口数据 | 241 | 240 | 38 | 48245.2 |
| 开源情报 | 19 | 14 | 7 | 5997.8 |
| 学习派生 | 29 | 5 | 7 | 1749.2 |
| 平台运行 | 25 | 6 | 20 | 886.7 |

## 二、缺口分类与建议

### 1) manifest 真实缺口（按源归属，非跨源虚数）

- tdx：**4** 个，样例：crypto_daily, guba_posts, us_legacy, v2_bar_daily
- tushare：**2** 个，样例：index_global, quarantine_prod_overwrite
- xueqiu：**3** 个，样例：xq_earningforecast, xq_hot_deal, xq_inner_trade
- cnopendata：**0** 个，样例：—

| 缺口 | 源 | 数据在盘 | 建议动作 | Owner |
|---|---|---|---|---|
| crypto_daily | tdx | ✅ | manifest 补键（数据已在盘，仓内可安全修复） | DATA3（补键复核） |
| guba_posts | tdx | ✅ | manifest 补键（数据已在盘，仓内可安全修复） | DATA3（补键复核） |
| us_legacy | tdx | ✅ | manifest 补键（数据已在盘，仓内可安全修复） | DATA3（补键复核） |
| v2_bar_daily | tdx | ❌ | 外部抓取后补 manifest（需 token/ETL） | DATA2（抓取）→DATA3 |
| index_global | tushare | ✅ | manifest 补键（数据已在盘，仓内可安全修复） | DATA3（补键复核） |
| quarantine_prod_overwrite | tushare | ✅ | manifest 补键（数据已在盘，仓内可安全修复） | DATA3（补键复核） |
| xq_earningforecast | xueqiu | ❌ | 外部抓取后补 manifest（需 token/ETL） | DATA2（抓取）→DATA3 |
| xq_hot_deal | xueqiu | ❌ | 外部抓取后补 manifest（需 token/ETL） | DATA2（抓取）→DATA3 |
| xq_inner_trade | xueqiu | ❌ | 外部抓取后补 manifest（需 token/ETL） | DATA2（抓取）→DATA3 |

> 注：manifest_check 的 missing 86/192 为“该 manifest 相对全 catalog”的跨源差，不能直接当缺口；上表已按 catalog.source/store 归属到对应源。

### 2) 未注册数据集（49）

| 样例 | 域 | 类型 | suggested_id | 建议 |
|---|---|---|---|---|
| tushare..git | 接口数据 | dir | ts_.git | 评估登记/忽略 |
| xueqiu_info.xq_status_classified | 开源情报 | sqlite_table | xq_xq_status_classified | 评估登记/忽略 |
| hypotheses.hypotheses | 学习派生 | sqlite_table | research_hypotheses | 评估登记/忽略 |
| research_shadow | 学习派生 | dir | research_shadow | 评估登记/忽略 |
| paper.orders | 平台运行 | sqlite_table | paper_orders | 评估登记/忽略 |
| paper.ledger | 平台运行 | sqlite_table | paper_ledger | 评估登记/忽略 |
| paper.positions | 平台运行 | sqlite_table | paper_positions | 评估登记/忽略 |
| paper.equity_marks | 平台运行 | sqlite_table | paper_equity_marks | 评估登记/忽略 |
| paper.positions_snapshot | 平台运行 | sqlite_table | paper_positions_snapshot | 评估登记/忽略 |
| paper.decision_snapshots | 平台运行 | sqlite_table | paper_decision_snapshots | 评估登记/忽略 |
| quant_db.chanlun_pool | 平台运行 | sqlite_table | quant_chanlun_pool | 评估登记/忽略 |
| quant_db.nine_dim_rc | 平台运行 | sqlite_table | quant_nine_dim_rc | 评估登记/忽略 |

- 完整 49 条见 JSON；probe/.git/临时目录建议列入忽略清单，不纳入注册目标；Owner **DATA4**。

### 3) 未暴露（242）

- 暴露率 22.9%；建议按价值分批（先接口数据/学习派生高价值表），Owner **DATA4**，需门户/产品评审。

### 4) 陈旧 >30 天（17）

| 数据集 | 域 | latest | 天数 |
|---|---|---|---|
| tushare.irm_qa_sh_probe | 接口数据 | 2026-09-27 | 37 |
| tushare.irm_qa_sz_probe | 接口数据 | 2026-09-27 | 37 |
| tdx.tdx_sector_def | 接口数据 | 2026-09-27 | 37 |
| tdx.ts_etf_basic | 接口数据 | 2026-09-27 | 37 |
| tdx.ts_ths_index | 接口数据 | 2026-09-27 | 37 |
| tushare.hk_balancesheet | 接口数据 | 2026-09-28 | 36 |
| tushare.hk_cashflow | 接口数据 | 2026-09-28 | 36 |
| tushare.hk_fina_indicator | 接口数据 | 2026-09-28 | 36 |
| tushare.hk_income | 接口数据 | 2026-09-28 | 36 |
| tushare.sw_l1_member | 接口数据 | 2026-09-28 | 36 |
| tushare.us_balancesheet | 接口数据 | 2026-09-28 | 36 |
| tushare.us_cashflow | 接口数据 | 2026-09-28 | 36 |

建议：先排查采集任务/源端是否停更；probe 类可降级或退役；Owner **DATA2**。

### 5) 无 latest 字段（59）

- 属 inventory 元数据不完整（非数据缺失）；建议修 inventory 生成器（只写报告/元数据），Owner **DATA4**，2 周。

## 三、仓内可安全修复（dry-run 草案，本次不改数据根）

- **manifest 补键草案**：9 项按源分组（见 JSON `dry_run_drafts.manifest`）；先跑 `python tools/datawarehouse/manifest_repair.py --json`（默认 dry-run）核对；apply 需 DATA3 复核。
- **inventory latest 字段**：修 `tools/.../dataset_inventory` 生成逻辑补 latest（不写数据根），Owner DATA4。
- **registry 登记映射草案**：未注册 → suggested_id 映射表（见 JSON `dry_run_drafts.registry`）；registry_align 有 apply 入口，但需 DATA4 审批与留痕。

## 四、必须外部抓取 / 需审批

- **外部抓取（需 token/配额）**：26 项命中清单（manifest 缺口 9 + 陈旧数据）；Owner **DATA2**，需采集排期（DATA1 批）。
- **需审批**：
  - manifest 补键 apply（DATA3）：DATA3 复核后执行（写 _manifest.json，不改数据）
  - 未注册数据集登记/忽略清单（DATA4）：DATA1 审批（registry 变更）
  - 暴露面扩展（72/314）（DATA4）：需产品/门户评审（contract_export/nexus 映射）
  - catalog/registry 重建（DATA4）：registry_align apply 需变更审批+留痕

## 五、结论

- 真实 manifest 缺口（按源归属计算）：9 项（tdx 4 / tushare 2 / xueqiu 3 / cnopendata 0）
- 未注册 49、未暴露 242；陈旧 >30 天 17、无 latest 59
- 仓内可安全修复：manifest 补键（dry-run 草案）、inventory latest 字段生成器修复（DATA4）、registry 登记映射草案；均不改数据根
- 需外部抓取：manifest 缺口对应数据集与陈旧数据集（token/配额）；需审批：registry/catalog 变更与暴露面扩展
