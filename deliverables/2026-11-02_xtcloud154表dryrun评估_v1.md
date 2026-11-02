# xtcloud 154 表资格批量 dry-run 评估 v1（S3-01）

> 任务：`task-26` / 《数据仓研发计划 v3 执行版》S3-01（只评估，不写回）｜执行：wa-quant（车道3）
> 口径 **as-of 2026-11-02 08:29–08:59 +08:00**｜范围：`warehouse_registry.json` 中 `qualification_status=unknown` 且 `channel=xtcloud` 的 **154 张**表
> 边界遵守：**未 apply、未改 registry/catalog/数据文件、未 commit、未重启服务**；本单只新增两产物（本 MD + `research/reports/qualification_xtcloud_dryrun_20261102.json`）。
> 机器可读明细（逐表 verdict/批次/原因/证据摘要）：`research/reports/qualification_xtcloud_dryrun_20261102.json`（195 KB，154 条 records）。

---

## 0. 结论先行

| 项 | 数 | 说明 |
|---|---|---|
| 评估覆盖 | **154 / 154** | 8 批（≤20/批，20×7 + 14），无缺表/重复；catalog source 核对 xtcloud 154/154 |
| `evidence_ok` | **85** | = 干净可升 **58** + 静态/无日期列豁免 **27** |
| `evidence_partial` | **63** | 真 stale 53 / 抽样待复核 7 / manifest 1 / 无最新日期 1 / 未来日期口径例外 1 |
| `evidence_bad` | **6** | sqlite 探针缺口 3 / 空目录无数据 3 |
| 自洽校验 | ✅ | 总数 154 = ok 85 + partial 63 + bad 6（与 8 批 summary 加总一致） |
| 零写回（本席） | ✅ | 8 次调用均 `--dry-run`；代码写路径仅存在 `--apply` 分支，本席未传 |
| **并发告警** | ⚠️ | 评估窗口内**并行车道**对同一 registry 连续 apply（08:24–08:34 ≥4 次 .bak），我们 154 张中 **12 张已被外部改判**（5 ok / 7 partial，与本评估 r2 判定一致） |

**一句话**：154 张中 **58 张可直接升**（证据齐、lag≤4d）、**27 张属"无日期列豁免"需先校准口径**、**53 张真 stale 要先补数据**、**6 张不可升（3 张是探针缺口、3 张是空目录）**；且本轮评估与并行车道的 apply 窗口重叠，正式 apply 前必须串行化协调。

---

## 1. 方法与范围

| 项 | 值 | 证据 |
|---|---|---|
| 选表规则 | `registry.physical_tables[qualification_status=='unknown' and channel=='xtcloud']` | 脚本见 §7；registry sha `df85465d…`（08:28:28 状态） |
| registry 总体 | physical **256** / unknown **191**（xtcloud 154、tushare 24、cnopendata 6、xueqiu 3、legacy/crypto/guba/pg 各1） | 同上 |
| catalog | **v18**、256 表、sha `d50f510f…`（registry.catalog_sha256 与之匹配，探针硬闸通过） | `quant_backtest/data/catalog.json` |
| 分批 | 8 批：**20,20,20,20,20,20,20,14** | 每批一次 `--tables <逗号清单>` |
| 命令模板 | `python -X utf8 tools/datawarehouse/qualify_probe.py --tables <批> --dry-run --json` | 8 次全部执行（rc 见 §2） |
| 探针版本 | `qualify_probe.py` sha256 `7044688624d2e78f…`、39,583 B、mtime **2026-11-02T08:34:04** | 工具在评估窗口内被并行车道改过（见 §5.6） |
| 日期口径 | `date_policy`=`{D:7, W:14, M:40, Q:100, 默认:7}`；静态/无日期列豁免（`date_check=skipped_static_no_date`） | 批次 payload `date_policy` |
| 只读验证 | registry/catalog 的写路径仅 `--apply`；本席 8 次调用后先复核：catalog sha 未变（→后由外部车道改写 registry，见 §5.6） | `C:\Temp\qualify_dryrun_20261102\` |

**执行通道**：原始批次输出落 `C:\Temp\qualify_dryrun_20261102\batch_r2_01..08.json`（权威复跑，08:38–08:39）；首轮 `batch_01..08.json`（08:33，旧版工具）仅作版本对比（§5.6）。

---

## 2. 批次结果（rc 逐批记录）

| 批 | 表数 | `rc` | `code` | ok | partial | bad | 耗时 |
|---|---|---|---|---|---|---|---|
| 1 | 20 | **1** | 1 | 6 | 11 | 3 | 0.6s |
| 2 | 20 | 0 | 0 | 16 | 4 | 0 | 0.6s |
| 3 | 20 | **1** | 1 | 2 | 16 | 2 | 0.7s |
| 4 | 20 | 0 | 0 | 15 | 5 | 0 | 0.7s |
| 5 | 20 | 0 | 0 | 13 | 7 | 0 | 0.9s |
| 6 | 20 | 0 | 0 | 14 | 6 | 0 | 0.8s |
| 7 | 20 | **1** | 1 | 11 | 8 | 1 | 0.9s |
| 8 | 14 | 0 | 0 | 8 | 6 | 0 | 0.8s |
| **合计** | **154** | — | — | **85** | **63** | **6** | 6.2s |

> `rc=1` 的批（1/3/7）均仅因含 `evidence_bad`（工具退出码约定：0=无 bad，1=有 bad，2=致命）；**无 rc=2**，即无 registry/catalog sha 冲突、无探针崩溃。

---

## 3. 原因分布（154 表，可多因）

| reason | 计数 | 含义 | 归属 |
|---|---|---|---|
| `stale_gt_7d` | **61** | 最新业务日滞后 > 7 天（本轮全部 freq=D/1min → 阈值 7d） | 53 真 stale + 7 抽样待复核 + 1 manifest 复合 |
| `no_parquet` | **6** | store 在但 0 个 parquet | 3 sqlite 探针缺口 + 3 空目录 |
| `manifest_missing` | 1 | 表不在所属 manifest 键集合 | `ts_global_fut` |
| `manifest_fallback_tushare` | 1 | catalog 未指定 manifest → 回退 tushare | `ts_global_fut` |
| `no_latest_date` | 1 | 有 date_col 但 footer 取不到最新日 | `ts_sge_basic` |
| `future_date` | 1 | 最新业务日 > 今天 | `ts_trade_cal`（交易日历，未来日期属正常，缺口径例外） |

> `no_date_column`（r1 旧版有 27 张）在当前版已被 `date_check=skipped_static_no_date` **豁免**，不再计入 reasons——见 §5.3 的口径问题。

---

## 4. 三分结论与建议 apply 清单

### 4.1 可升级（85）

#### A1｜干净可升 58 张（证据齐、lag≤4d、freq=D、layout=part；建议 apply 拆 3 子批 20/20/18）
```
ts_adj_factor ts_bak_basic ts_block_trade ts_cb_daily ts_cyq_perf ts_daily ts_daily_basic
ts_daily_info ts_dc_daily ts_dc_index ts_dc_member ts_fund_adj ts_fund_daily ts_fund_share
ts_fut_daily ts_fut_holding ts_fut_mapping ts_fut_settle ts_fut_weekly_monthly ts_fut_wsr
ts_fx_daily ts_ggt_top10 ts_hk_daily ts_hk_daily_adj ts_hk_hold ts_hk_tradecal ts_index_daily
ts_index_dailybasic ts_kpl_concept_cons ts_limit_cpt_list ts_limit_step ts_margin ts_margin_detail
ts_margin_secs ts_moneyflow ts_moneyflow_hsgt ts_moneyflow_ind_dc ts_moneyflow_ind_ths
ts_moneyflow_mkt_dc ts_monthly ts_namechange ts_opt_daily ts_repo_daily ts_repurchase ts_sge_daily
ts_stk_ah_comparison ts_stk_auction_c ts_stk_limit ts_stk_premarket ts_stk_week_month_adj
ts_stk_weekly_monthly ts_stock_hsgt ts_suspend_d ts_sz_daily_info ts_tdx_member ts_ths_daily
ts_us_daily ts_us_tradecal
```

#### A2｜静态/无日期列豁免 27 张（工具判 ok，但口径需先确认）
- **已在 `date_policy.static_exempt_tables` 声明（3 张，可直接升）**：`tdx_sector_def`、`tdx_sector_member`、`ts_broker_recommend`
- **未在策略名单内却被豁免（24 张，建议先补 date_col 或显式入白名单再升）**：
```
ts_cb_price_chg ts_cb_rate ts_cctv_news ts_cn_cpi ts_cn_gdp ts_cn_m ts_cn_pmi ts_cn_ppi
ts_fut_weekly_detail ts_fx_obasic ts_hm_list ts_hs_const ts_index_classify ts_index_member_all
ts_major_news ts_new_share ts_news ts_report_rc ts_stk_surv ts_ths_member ts_us_tbr ts_us_trycr
ts_us_tycr ts_wz_index
```
> 其中 `ts_news/ts_major_news/ts_report_rc/ts_cctv_news` 为**新闻/研报流**、`ts_us_tbr/trycr/tycr` 为**美债收益率**、`ts_cn_*` 为**宏观月度**——均属动态数据，被"无日期列豁免"后**没有新鲜度证据**，二选一：① 补 catalog `date_col`（news 类可用 `created_at/pub_time`）；② 在 `date_policy` 显式声明并标注 `date_status=not_applicable`。

### 4.2 需补证据（63，不建议本轮 apply）

| 子类 | 数 | 表 | 处置建议 |
|---|---|---|---|
| **真 stale**（probe 最新日 = catalog freshness，确无新数据） | **53** | 见 JSON `class=需补证据-真stale`；典型：`ts_fina_mainbz(125d)`、`ts_income(33d)`、`ts_forecast(20d)`、`ts_stock_basic(39d)` | **属数据更新问题，不是资格问题**：先行补数/查采集管线，再评 |
| **抽样待复核**（probe 最新日 **早于** catalog freshness → 探针只抽 5 个 part 可能漏最新片） | **7** | `ts_anns_d`(probe 20240718 vs cat 20261016)、`ts_fund_portfolio`(20230831/20260831)、`ts_index_weekly`、`ts_slb_sec`、`ts_top10_floatholders`、`ts_top10_holders`、`tdx_sector_min` | 复核 footer（全量或按 mtime 最新 N 片）后再定，**不能据此判 stale** |
| manifest 缺失/回退 | 1 | `ts_global_fut` | 把表补进 `tdx/_manifest.json`（或 catalog `manifest` 指向正确文件）后重跑 |
| 无最新日期 | 1 | `ts_sge_basic` | 检查 footer 日期统计（文件是否空壳/date_col 是否错列） |
| 未来日期口径例外 | 1 | `ts_trade_cal`（lag=−424） | 交易日历含未来日期属正常 → 建议探针为 calendar 类加例外（口径修，非数据） |

### 4.3 不可升（6）

| 子类 | 数 | 表 | 事实与处置 |
|---|---|---|---|
| **sqlite 探针缺口** | 3 | `stock_news`、`stock_notice`、`stock_report` | store=`tdx/tdx_info.db`（**269,934,592 B**，mtime 11-01 19:25，catalog freshness=20261101、verified）；探针 `_iter_parquet` 不支持 sqlite 布局 → 0 parquet → 误判 bad。**属探针能力缺口，不是无数据**：扩展 sqlite 证据（表行数+日期列 max）后重跑 |
| **空目录/无数据** | 3 | `ts_eco_cal`、`ts_fund_company`、`ts_stock_company` | store 目录**存在但 0 个 parquet**；catalog 却标 `files=0 + verification_status=verified + freshness=null` → **登记失真**。先修 catalog 登记（降 planned/无数据/补采集），**不可升** |

### 4.4 数据侧待办（D，不属资格评估结论）

- 真 stale 53 张中：**lag 19–21 天 25 张**、**lag ≥ 90 天 13 张** —— 多处为同一批 freshness=20261013 前后的存量（与 10-13 xtcloud 同步批次高度重合），**疑似 10-13 后 xtcloud 同步管线未覆盖这批表**，建议 DATA1 优先查采集管线（附：`ts_slb_sec 1545d`、`ts_fund_portfolio 1159d`、`ts_bse_mapping 938d`、`ts_anns_d 837d` 为最久未更新）。
- `ts_fina_mainbz/ts_fina_mainbz_vip(125d)`、`ts_forecast*`、`ts_express*`、`ts_income*` 等为**低频内容表但 catalog freq=D** → 阈值口径失真也是"真 stale"结论的一部分：建议对财报/预测类按实际发布节律改 freq（Q/M）或单表设 lag 阈值，否则永远无法升。

---

## 5. 关键发现（按重要性）

### 5.1 【P0·协调】并行车道在评估窗口内写 registry（apply 竞争）
- 证据：`warehouse_registry.json` 今日出现 4 个 apply 备份（`warehouse_registry.json.bak-20261102-082432/-082437/-082828/-083457`，mtime 07:07:30 / 08:24:32 / 08:24:37 / 08:28:28），且 **08:34:57 registry 再次被写**（sha `df85465d…`→`53f8cfed…`，mtime 08:34:57）。
- 状态变化：unknown 191→**172**（evidence_ok 32→42、partial 25→34）；**我们清单 154 张中 12 张已被外部改判**（5 `evidence_ok`：`tdx_sector_def/tdx_sector_member/ts_cctv_news/ts_major_news/ts_news`；7 `evidence_partial`：`dknife_snap/tdx_sector_daily/tdx_sector_min/tdx_sector_min_tail/ts_emotion_daily/ts_emotion_ladder/ts_index_weight`）。
- **正面交叉验证**：外部已 apply 的 12 张判定与本评估 r2 **完全一致**（5+7 分类相同）→ 说明本轮 dry-run 结论与基线实现口径一致。
- **风险**：后续 apply 必须与并行车道**串行/划分表集合**；否则后写覆盖、`validate_registry` 的 before/after diff 口径混乱。

### 5.2 工具版本在评估窗口内被改动（可复现性风险）
- 08:29 取数时工具 36,787 B（mtime 08:27:56）；**08:34:04 被并行车道改为 39,583 B（sha `70446886…`）**，新增 `date_policy`（freq 阈值）+ `static_no_date` 豁免（task-24）。
- 影响：**首轮（r1，旧版）与复跑（r2，当前版）逐表判定差 27 张**——恰好就是 27 张无日期列表由 `evidence_partial(no_date_column)` → `evidence_ok`。
- **本报告以 r2（sha `70446886…`）为权威**；若工具再被改，结论需按新 sha 复跑。

### 5.3 【P1】静态豁免"名单 3 张 vs 实现 27 张"口径不一致
- `date_policy.static_exempt_tables` 只声明 3 张，但实现 `static_exempt = (static_no_date or not date_col or latest_source=='no_date_column')` 实际豁免 **27 张**（`date_check=skipped_static_no_date`）。
- 其中 24 张为动态数据（新闻/宏观/汇率/期权期货元数据），无新鲜度证据即判 ok → **资格证据链变薄**。建议：收窄实现（仅 `static_no_date=true` 或 `date_status=not_applicable` 才豁免）或把 27 张全部显式登记进 `date_policy.static_exempt_tables`（当前 fail-visible 字段有 `date_check`，但名单不完整）。

### 5.4 【P1】抽样策略导致 7 张假 stale
- `select_samples` 每表最多抽 5 个 parquet（无日期名→mtime 最新 4+最大 1），多 part 表可能漏最新片；7 张 probe latest **早于** catalog freshness（最大差：`ts_slb_sec` 2022-08-10 vs catalog 2024-09-30；`ts_anns_d` 2024-07-18 vs 2026-10-16）。
- 建议：对 `layout=part` 且无日期文件名的表，抽样按「最大 mtime 且非空壳」+ 可选全量 footer 上限（如 ≤200 文件全扫）后再下结论；或至少把 `latest_source`/抽样策略写进资格证据摘要（当前 `_summary` 已带源，但人类报告易误读）。

### 5.5 【P1】catalog 登记失真（3 空目录 + 6 sqlite files=0）
- 3 张空目录表 catalog 标 `verified`（files=0、freshness=null）→ 资格台账会继续读到一个"已核验但无数据"的矛盾态。
- 6 张 sqlite 表（本次 3 张 + xueqiu 3 张）catalog `files` 恒 0（task-1 报告已记录口径缺陷），与之伴随的是探针对 sqlite 无证据能力。

### 5.6 【P2】rc/code 语义与退出码
- 8 批 rc 与 code 完全一致（1/0/1/0/0/0/1/0）；**rc=1 仅由 bad 触发**，不代表探针错误；无 rc=2（无 sha 冲突/崩溃）。后续 CI 若以 rc 判定，应区分「有 bad」与「致命」。

---

## 6. 风险与前置条件（apply 前必读）

| # | 风险 | 影响 | 对策 |
|---|---|---|---|
| R1 | 并行车道正在 apply 同一 registry（§5.1） | 写回竞争/覆盖、diff 口径混乱 | apply 前 Lead 协调串行；先 `registry_rebuild` 得干净基线；按表集合切分（本单 A1 58 张与外部已 apply 12 张去重） |
| R2 | 工具版本不稳定（§5.2） | 结论不可复现 | apply 前锁定工具 sha `70446886…`；若变则重跑 8 批 |
| R3 | registry↔catalog sha 硬闸 | catalog/registry 每日重建（DW-Daily 06:30；今日 registry rebuild 07:07）；跨窗口 apply 会 rc=2 拒写 | dry-run 与 apply 同一窗口完成；apply 前重跑一次确认 `catalog_sha256` 匹配 |
| R4 | 静态豁免口径过宽（§5.3） | 27 张证据变薄 | 先定口径（收窄 or 全量白名单），再决定 A2 是否 apply |
| R5 | 抽样假 stale（§5.4） | 7 张误判 partial | 复核后再归组；不进入 apply |
| R6 | 真 stale 53 张（§4.4） | 资格长期无法升 | 归数据侧补数（DATA1），不靠资格口径解决 |
| R7 | 本轮**未验证 `--apply` 写回路径** | 备份/原子写/kg 互斥日志/校验均未实跑 | apply 单须另立任务 + L2 审批，按脚本既有流程执行 |
| R8 | 3 张空目录 + 6 张 sqlite 登记/探针缺口（§5.5） | 资格台账失真 | 另立 catalog 修复 + 探针扩展任务 |

---

## 7. 复现与证据路径

```powershell
# 1) 选表（registry unknown + channel=xtcloud）→ 154 张；catalog source 核对 xtcloud 154/154
#    注册表快照 sha256 = df85465d6843ecf0…（catalog v18 sha d50f510f8297dd59…，两者匹配）
# 2) 8 批 dry-run（≤20/批）
python -X utf8 tools/datawarehouse/qualify_probe.py --tables <20 张逗号清单> --dry-run --json
# 3) 原始输出（权威复跑 r2）
C:\Temp\qualify_dryrun_20261102\batch_r2_01.json … batch_r2_08.json
C:\Temp\qualify_dryrun_20261102\batch_meta_r2.json   # 每批 rc/code/summary/耗时
C:\Temp\qualify_dryrun_20261102\tables_input.json    # 154 表清单 + 取数时 registry/catalog sha
C:\Temp\qualify_dryrun_20261102\version_diff.json    # r1(旧版) vs r2(当前版) 27 表差异
# 4) 汇总产物
research/reports/qualification_xtcloud_dryrun_20261102.json   # 逐表 verdict/原因/分类/批次/apply 建议
```

| 证据 | 路径 |
|---|---|
| 机器可读明细 | `research/reports/qualification_xtcloud_dryrun_20261102.json` |
| 批次原始 JSON（8 个） | `C:\Temp\qualify_dryrun_20261102\batch_r2_*.json` |
| 批次元数据（rc/code/summary） | `C:\Temp\qualify_dryrun_20261102\batch_meta_r2.json` |
| 首轮/复跑版本差异 | `C:\Temp\qualify_dryrun_20261102\version_diff.json`（27 表） |
| registry 基线快照 | 取数时 sha `df85465d…`（registry `generated_at=2026-11-02T07:07:02`）；今日 apply 备份见 §5.1 |
| 探针实现 | `tools/datawarehouse/qualify_probe.py`（sha256 `7044688624d2e78f…`，39,583 B） |
| 计划依据 | `docs/plans/2026-11-02_数据仓研发计划_v3_执行版.md` S3-01 |

---

## 8. 未验证 / 存疑（不要当结论引用）

1. **`--apply` 写回路径未验证**：备份、原子写、`registry.catalog_sha256` 校验、kg 互斥日志（`C:\Temp\kg_sync_task.log`）、写后重读校验——本轮全未执行（禁 apply）。
2. **并行车道具体 apply 了哪些批/依据什么证据**：仅从 registry 差异与 .bak 推断 12 张；其来源任务单未取证。
3. **7 张抽样偏差表的真实最新日期未复核**（未跑全量 footer 扫描）；"假 stale"是推断（依据 probe latest < catalog freshness）。
4. **53 张真 stale 的成因未逐表归因**（采集停更/接口变更/表本身低频），仅给出 lag 分布与批次相关性假设。
5. **首次 r1 所用工具版本 sha 不可得**（文件已被覆盖、无 git 提交记录）；r1 结论只用于版本差异性对照，不作为评估依据。
6. **registry 中被外部改判的 12 张**是否与本次建议的 apply 集合重叠：Q2 建议按**当前** registry 重新差集后再 apply（本报告 apply 清单基于 08:29 基线）。
7. `ts_sge_basic` 无最新日期的原因（空壳文件/date_col 错列）未打开 footer 逐片核对。
