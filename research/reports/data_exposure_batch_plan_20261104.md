# 数据暴露 Top50 审批包 v2（task-181 · W21 · 未执行任何暴露动作）

- 生成：2026-11-04T06:14:27+08:00；依据 `research/reports/datawarehouse_coverage_p1_20261103.json`（未暴露 242 项） + `research/reports/data_asset_metadata_draft_20261104.json`（4 维评分 Top50）；as_of 2026-11-04
- 4 维权重：价值 0.4 / 大小 0.2 / 时效 0.2 / 复用 0.2（复用=仓内 py 引用扫描，本次只读扫描 3839 个 py 文件）
- **边界：本包只做审批准备，不执行暴露动作；不改 catalog/registry、不发网络请求、不 commit。**
- 明细审批表（5 批×10 = 50 项）：`deliverables/2026-11-04_数据暴露Top50审批表_v2.csv`

## 一、总览（5 批 × 10 项）

| 批 | 主题 | 项数 | 风险分布 | 平均总分 | 主要消费方 | 建议启动条件 |
|---|---|---|---|---|---|---|
| B1 | 快赢批 | 10 | 高0/中0/低10 | 0.929 | 数据仓治理, 投研/因子/回测, 门户/API 页面 | 产品/门户评审 + DATA4 复核后即可 |
| B2 | 价值批 | 10 | 高0/中0/低10 | 0.845 | 其他/脚本, 分析/研究, 投研/因子/回测 | 批1 链路验证通过后 |
| B3 | 结构批 | 10 | 高1/中3/低6 | 0.802 | 数据仓治理, 投研/因子/回测, 分析/研究 | 批1 链路验证通过后 |
| B4 | 情报/长尾批 | 10 | 高2/中0/低8 | 0.765 | 数据仓治理, 门户/API 页面, 投研/因子/回测 | 批1 链路验证通过后 |
| B5 | 审慎批 | 10 | 高0/中0/低10 | 0.732 | 其他/脚本, 分析/研究, 数据仓治理 | 批1 链路验证通过后 |

## 二、5 批清单（Top50）

### 批次 B1（快赢批：小体量、接口数据、引用面广（reuse 高）、合规低风险；先打通门户/contract_export 映射链路，验证审批与回滚流程）

| rank | label | catalog id | 域 | 体量MB | latest | 总分 | 风险 | 消费方建议 |
|---|---|---|---|---|---|---|---|---|
| 1 | tushare.express | ts_express | 接口数据 | 0.3 | 2026-10-28 | 0.9715 | 低 | 门户/API 页面；分析/研究（仓内 py 代码引用聚合，已排除 tests/outputs） |
| 2 | tushare.index_weight | ts_index_weight | 接口数据 | 1.5 | 2026-11-01 | 0.9605 | 低 | 门户/API 页面；数据仓治理（仓内 py 代码引用聚合，已排除 tests/outputs） |
| 3 | tushare.fx_daily | ts_fx_daily | 接口数据 | 0.1 | 2026-10-30 | 0.9594 | 低 | 投研/因子/回测；数据仓治理（仓内 py 代码引用聚合，已排除 tests/outputs） |
| 4 | tushare.index_global | ts_index_global | 接口数据 | 2.6 | 2026-11-01 | 0.9469 | 低 | 数据仓治理；投研/因子/回测（仓内 py 代码引用聚合，已排除 tests/outputs） |
| 5 | tushare.moneyflow_hsgt | ts_moneyflow_hsgt | 接口数据 | 0.1 | 2026-10-30 | 0.9404 | 低 | 投研/因子/回测；分析/研究（仓内 py 代码引用聚合，已排除 tests/outputs） |
| 6 | tushare.major_news | ts_major_news | 接口数据 | 2.1 | 2026-11-01 | 0.9292 | 低 | 数据仓治理（仓内 py 代码引用聚合，已排除 tests/outputs） |
| 7 | tushare.hk_basic | ts_hk_basic | 接口数据 | 8.4 | 2026-11-01 | 0.8997 | 低 | 投研/因子/回测（仓内 py 代码引用聚合，已排除 tests/outputs） |
| 8 | tushare.index_member_all | ts_index_member_all | 接口数据 | 0.1 | 2026-10-29 | 0.8985 | 低 | 投研/因子/回测；分析/研究（仓内 py 代码引用聚合，已排除 tests/outputs） |
| 9 | tushare.shibor | ts_shibor | 接口数据 | 0.2 | 2026-11-01 | 0.8926 | 低 | 其他/脚本（仓内 py 代码引用聚合，已排除 tests/outputs） |
| 10 | tdx.ts_margin | tdx_ts_margin | 接口数据 | 0.3 | 2026-10-31 | 0.8915 | 低 | 门户/API 页面；数据仓治理（仓内 py 代码引用聚合，已排除 tests/outputs） |

### 批次 B2（价值批：中体量、行情/基础数据补充（指数权重/期货/SHIBOR/涨跌停/更名等），复用面较高；在批1链路稳定后跟进）

| rank | label | catalog id | 域 | 体量MB | latest | 总分 | 风险 | 消费方建议 |
|---|---|---|---|---|---|---|---|---|
| 11 | tushare.shibor_lpr | ts_shibor_lpr | 接口数据 | 0.4 | 2026-11-01 | 0.8904 | 低 | 其他/脚本（仓内 py 代码引用聚合，已排除 tests/outputs） |
| 12 | tushare.cb_basic | ts_cb_basic | 接口数据 | 7.3 | 2026-11-01 | 0.8843 | 低 | 其他/脚本（仓内 py 代码引用聚合，已排除 tests/outputs） |
| 13 | tdx.ts_namechange | tdx_ts_namechange | 接口数据 | 0.1 | 2026-10-29 | 0.8571 | 低 | 其他/脚本（仓内 py 代码引用聚合，已排除 tests/outputs） |
| 14 | tushare.limit_list_d | ts_limit_list_d | 接口数据 | 5.4 | 2026-11-01 | 0.8568 | 低 | 投研/因子/回测；分析/研究（仓内 py 代码引用聚合，已排除 tests/outputs） |
| 15 | tushare.sw_classify_l1 | ts_sw_classify_l1 | 接口数据 | 0.6 | 2026-11-01 | 0.8517 | 低 | 分析/研究；数据仓治理（仓内 py 代码引用聚合，已排除 tests/outputs） |
| 16 | tushare.stk_holdernumber | ts_stk_holdernumber | 接口数据 | 24.3 | 2026-10-30 | 0.8376 | 低 | 数据仓治理；分析/研究（仓内 py 代码引用聚合，已排除 tests/outputs） |
| 17 | tushare.sf_month | ts_sf_month | 接口数据 | 0.3 | 2026-11-01 | 0.8206 | 低 | 其他/脚本（仓内 py 代码引用聚合，已排除 tests/outputs） |
| 18 | tdx.ts_adj_factor | tdx_ts_adj_factor | 接口数据 | 15.1 | 2026-10-30 | 0.8197 | 低 | 投研/因子/回测；门户/API 页面（仓内 py 代码引用聚合，已排除 tests/outputs） |
| 19 | tushare.us_basic | ts_us_basic | 接口数据 | 33.1 | 2026-11-01 | 0.8173 | 低 | 其他/脚本（仓内 py 代码引用聚合，已排除 tests/outputs） |
| 20 | tushare.stk_managers | ts_stk_managers | 接口数据 | 7.2 | 2026-09-30 | 0.814 | 低 | 其他/脚本（仓内 py 代码引用聚合，已排除 tests/outputs） |

### 批次 B3（结构批：中高风险/中大体量（财务、研报、筹码、复权因子、知识文档），需先做口径与脱敏评估，建议小范围投研试用后暴露）

| rank | label | catalog id | 域 | 体量MB | latest | 总分 | 风险 | 消费方建议 |
|---|---|---|---|---|---|---|---|---|
| 21 | tushare.cyq_perf | ts_cyq_perf | 接口数据 | 44.7 | 2026-11-01 | 0.8105 | 低 | 分析/研究；数据仓治理（仓内 py 代码引用聚合，已排除 tests/outputs） |
| 22 | knowledge_docs | knowledge_docs | 学习派生 | 12.3 | 2026-11-01 | 0.8088 | 高 | 投研/因子/回测；数据仓治理（仓内 py 代码引用聚合，已排除 tests/outputs） |
| 23 | tushare.research_report | ts_research_report | 接口数据 | 19.3 | 2026-11-01 | 0.8074 | 低 | 投研/因子/回测；Agent/学习/调度（仓内 py 代码引用聚合，已排除 tests/outputs） |
| 24 | tushare.adj_factor | ts_adj_factor | 接口数据 | 234.6 | 2026-10-30 | 0.8 | 中 | 投研/因子/回测；门户/API 页面（仓内 py 代码引用聚合，已排除 tests/outputs） |
| 25 | tushare.anns_d | ts_anns_d | 接口数据 | 279.3 | 2026-11-01 | 0.8 | 中 | 投研/因子/回测；分析/研究（仓内 py 代码引用聚合，已排除 tests/outputs） |
| 26 | tushare.margin_detail | ts_margin_detail | 接口数据 | 195.9 | 2026-10-30 | 0.8 | 低 | 投研/因子/回测；数据仓治理（仓内 py 代码引用聚合，已排除 tests/outputs） |
| 27 | tushare.report_rc | ts_report_rc | 接口数据 | 85.5 | 2026-10-31 | 0.8 | 低 | 投研/因子/回测；数据仓治理（仓内 py 代码引用聚合，已排除 tests/outputs） |
| 28 | tushare.stock_company | ts_stock_company | 接口数据 | 299.7 | 2026-11-01 | 0.8 | 中 | 数据仓治理（仓内 py 代码引用聚合，已排除 tests/outputs） |
| 29 | cnopendata | cnopendata | 接口数据 | 9.4 | 2026-10-21 | 0.7994 | 低 | 数据仓治理；Agent/学习/调度（仓内 py 代码引用聚合，已排除 tests/outputs） |
| 30 | tdx.ts_rt_min | tdx_ts_rt_min | 接口数据 | 11.4 | 2026-10-23 | 0.792 | 低 | 其他/脚本（仓内 py 代码引用聚合，已排除 tests/outputs） |

### 批次 B4（情报/长尾批：含资讯/股东/港股/期货等体量较大或口径复杂资产；需容量门与内容合规门双过）

| rank | label | catalog id | 域 | 体量MB | latest | 总分 | 风险 | 消费方建议 |
|---|---|---|---|---|---|---|---|---|
| 31 | major_news | intel_major_news | 开源情报 | 2.1 | 2026-11-01 | 0.7893 | 高 | 数据仓治理（仓内 py 代码引用聚合，已排除 tests/outputs） |
| 32 | tushare.top10_floatholders | ts_top10_floatholders | 接口数据 | 15.7 | 2026-09-30 | 0.7804 | 低 | 数据仓治理；门户/API 页面（仓内 py 代码引用聚合，已排除 tests/outputs） |
| 33 | tushare.hk_hold | ts_hk_hold | 接口数据 | 66.6 | 2026-10-30 | 0.7732 | 低 | 投研/因子/回测（仓内 py 代码引用聚合，已排除 tests/outputs） |
| 34 | tdx.ts_index_weight | tdx_ts_index_weight | 接口数据 | 0.0 | 2026-10-13 | 0.7656 | 低 | 门户/API 页面（仓内 py 代码引用聚合，已排除 tests/outputs） |
| 35 | tdx.ts_trade_cal | tdx_ts_trade_cal | 接口数据 | 0.1 | 2026-10-13 | 0.7643 | 低 | 数据仓治理（仓内 py 代码引用聚合，已排除 tests/outputs） |
| 36 | tushare.quarantine_prod_overwrite | ts_quarantine_prod_overwrite | 接口数据 | 0.7 | 2026-10-17 | 0.7629 | 低 | 数据仓治理（仓内 py 代码引用聚合，已排除 tests/outputs） |
| 37 | research_report | intel_research_report | 开源情报 | 19.3 | 2026-11-01 | 0.7574 | 高 | 投研/因子/回测；Agent/学习/调度（仓内 py 代码引用聚合，已排除 tests/outputs） |
| 38 | tdx.ts_ths_member | tdx_ts_ths_member | 接口数据 | 1.7 | 2026-10-13 | 0.7544 | 低 | Agent/学习/调度（仓内 py 代码引用聚合，已排除 tests/outputs） |
| 39 | tushare.stk_pledge | ts_stk_pledge | 接口数据 | 0.1 | 2026-10-19 | 0.7538 | 低 | 数据仓治理（仓内 py 代码引用聚合，已排除 tests/outputs） |
| 40 | tdx.ts_emotion_daily | tdx_ts_emotion_daily | 接口数据 | 0.0 | 2026-10-15 | 0.7493 | 低 | 其他/脚本（仓内 py 代码引用聚合，已排除 tests/outputs） |

### 批次 B5（审慎批：参考数据/低引用/大体量（dknife_snap、世华、申万成员等），仅在有明确消费方申请时按需暴露，默认不排期）

| rank | label | catalog id | 域 | 体量MB | latest | 总分 | 风险 | 消费方建议 |
|---|---|---|---|---|---|---|---|---|
| 41 | tdx.ts_global_fut | tdx_ts_global_fut | 接口数据 | 0.7 | 2026-10-15 | 0.7474 | 低 | 其他/脚本（仓内 py 代码引用聚合，已排除 tests/outputs） |
| 42 | tdx.ts_sge_basic | tdx_ts_sge_basic | 接口数据 | 0.0 | 2026-10-13 | 0.7432 | 低 | 其他/脚本（仓内 py 代码引用聚合，已排除 tests/outputs） |
| 43 | tdx.dknife_snap | tdx_dknife_snap | 接口数据 | 12.9 | 2026-10-13 | 0.7396 | 低 | 其他/脚本（仓内 py 代码引用聚合，已排除 tests/outputs） |
| 44 | tushare.sw_l1_member | ts_sw_l1_member | 接口数据 | 0.2 | 2026-09-28 | 0.7301 | 低 | 分析/研究；数据仓治理（仓内 py 代码引用聚合，已排除 tests/outputs） |
| 45 | tdx.ts_emotion_ladder | tdx_ts_emotion_ladder | 接口数据 | 0.1 | 2026-10-15 | 0.7283 | 低 | 其他/脚本（仓内 py 代码引用聚合，已排除 tests/outputs） |
| 46 | tdx.ts_cb_call | tdx_ts_cb_call | 接口数据 | 0.1 | 2026-10-31 | 0.7271 | 低 | 其他/脚本（仓内 py 代码引用聚合，已排除 tests/outputs） |
| 47 | tdx.ts_hk_tradecal | tdx_ts_hk_tradecal | 接口数据 | 0.1 | 2026-10-31 | 0.7271 | 低 | 其他/脚本（仓内 py 代码引用聚合，已排除 tests/outputs） |
| 48 | tdx.ts_moneyflow_hsgt | tdx_ts_moneyflow_hsgt | 接口数据 | 0.1 | 2026-10-31 | 0.7271 | 低 | 其他/脚本（仓内 py 代码引用聚合，已排除 tests/outputs） |
| 49 | tdx.ts_us_tbr | tdx_ts_us_tbr | 接口数据 | 0.1 | 2026-10-31 | 0.7271 | 低 | 其他/脚本（仓内 py 代码引用聚合，已排除 tests/outputs） |
| 50 | tdx.ts_us_tradecal | tdx_ts_us_tradecal | 接口数据 | 0.1 | 2026-10-31 | 0.7271 | 低 | 其他/脚本（仓内 py 代码引用聚合，已排除 tests/outputs） |

## 三、首批建议（B1 · 快赢批）

- **范围**：见 CSV `batch=1` 十项（小体量接口数据、仓内引用高、合规低风险）
- **审批链**：产品/门户评审（主）→ DATA4 复核（owner）→ 数据口径核对（投研代表）→ 上线留痕；无需合规会签（接口数据）
- **门禁**：①可用性/口径门（catalog 已注册 + 抽样 schema/latest 核对）→ ②产品/门户评审门（用途/展示形态/contract_export 映射）→ ③合规门（tushare 许可/再分发范围）→ ④消费方验收门（接口联调+页面验收）→ ⑤回滚门
- **消费方**：优先接入投研因子/回测（tools/research）与门户数据页（portal），复用面以 CSV `consumer_hint/consumer_evidence` 为准
- **回滚**：禁用 contract_export 映射 + 回退门户入口（保留前一版 commit）；不涉及 catalog/registry 变更
- **建议节奏**：B1 十项按 CSV rank 顺序分 2 次上线（每次 5 项），每次上线后 1 个交易日观察门户与消费方报错率，再放下一组

## 四、每批理由 / 回滚 / 门禁 / 验收

### B1

- **理由**：快赢批：小体量、接口数据、引用面广（reuse 高）、合规低风险；先打通门户/contract_export 映射链路，验证审批与回滚流程
- **回滚**：禁用 ts_express 的 contract_export 映射并回退门户入口（保留前一版 commit）；若已生成静态渲染，回退 portal/static 对应文件；不改 catalog/registry（逐项回滚同口径）
- **门禁**：①可用性/口径门：catalog 已注册 + 抽样 schema/latest 核对；②产品/门户评审门：用途/展示形态/contract_export 映射评审；③合规门：数据许可/再分发范围确认（tushare 条款）；④消费方验收门：接口联调 + 页面验收 + 样例核对；⑤回滚门：映射开关与前端可一键下线
- **验收**：①10 项数据接口返回样例与 catalog schema 一致；②门户页面/接口联调通过且有样例截图；③消费方（投研/门户）确认可用；④回滚演练一次成功；⑤上线记录与审批留痕齐全

### B2

- **理由**：价值批：中体量、行情/基础数据补充（指数权重/期货/SHIBOR/涨跌停/更名等），复用面较高；在批1链路稳定后跟进
- **回滚**：禁用 ts_shibor_lpr 的 contract_export 映射并回退门户入口（保留前一版 commit）；若已生成静态渲染，回退 portal/static 对应文件；不改 catalog/registry（逐项回滚同口径）
- **门禁**：①可用性/口径门：catalog 已注册 + 抽样 schema/latest 核对；②产品/门户评审门：用途/展示形态/contract_export 映射评审；③合规门：数据许可/再分发范围确认（tushare 条款）；④消费方验收门：接口联调 + 页面验收 + 样例核对；⑤回滚门：映射开关与前端可一键下线
- **验收**：①10 项数据接口返回样例与 catalog schema 一致；②门户页面/接口联调通过且有样例截图；③消费方（投研/门户）确认可用；④回滚演练一次成功；⑤上线记录与审批留痕齐全

### B3

- **理由**：结构批：中高风险/中大体量（财务、研报、筹码、复权因子、知识文档），需先做口径与脱敏评估，建议小范围投研试用后暴露
- **回滚**：禁用 ts_cyq_perf 的 contract_export 映射并回退门户入口（保留前一版 commit）；若已生成静态渲染，回退 portal/static 对应文件；不改 catalog/registry（逐项回滚同口径）
- **门禁**：①可用性/口径门：catalog 已注册 + 抽样 schema/latest 核对；②产品/门户评审门：用途/展示形态/contract_export 映射评审；③合规门：数据许可/再分发范围确认（tushare 条款）；④消费方验收门：接口联调 + 页面验收 + 样例核对；⑤回滚门：映射开关与前端可一键下线
- **验收**：①10 项数据接口返回样例与 catalog schema 一致；②门户页面/接口联调通过且有样例截图；③消费方（投研/门户）确认可用；④回滚演练一次成功；⑤上线记录与审批留痕齐全

### B4

- **理由**：情报/长尾批：含资讯/股东/港股/期货等体量较大或口径复杂资产；需容量门与内容合规门双过
- **回滚**：禁用 intel_major_news 的 contract_export 映射并回退门户入口（保留前一版 commit）；若已生成静态渲染，回退 portal/static 对应文件；不改 catalog/registry（逐项回滚同口径）
- **门禁**：①可用性/口径门：catalog 已注册 + 抽样 schema/latest 核对；②产品/门户评审门：用途/展示形态/contract_export 映射评审；③安全/合规门：PII 脱敏核验 + 内容分级 + 访问控制；④消费方验收门：接口联调 + 页面验收 + 样例核对；⑤回滚门：映射开关与前端可一键下线
- **验收**：①10 项数据接口返回样例与 catalog schema 一致；②门户页面/接口联调通过且有样例截图；③消费方（投研/门户）确认可用；④回滚演练一次成功；⑤上线记录与审批留痕齐全

### B5

- **理由**：审慎批：参考数据/低引用/大体量（dknife_snap、世华、申万成员等），仅在有明确消费方申请时按需暴露，默认不排期
- **回滚**：禁用 tdx_ts_global_fut 的 contract_export 映射并回退门户入口（保留前一版 commit）；若已生成静态渲染，回退 portal/static 对应文件；不改 catalog/registry（逐项回滚同口径）
- **门禁**：①可用性/口径门：catalog 已注册 + 抽样 schema/latest 核对；②产品/门户评审门：用途/展示形态/contract_export 映射评审；③合规门：数据许可/再分发范围确认（tushare 条款）；④消费方验收门：接口联调 + 页面验收 + 样例核对；⑤回滚门：映射开关与前端可一键下线
- **验收**：①10 项数据接口返回样例与 catalog schema 一致；②门户页面/接口联调通过且有样例截图；③消费方（投研/门户）确认可用；④回滚演练一次成功；⑤上线记录与审批留痕齐全

## 五、消费方建议（按仓内 py 引用扫描）

- 映射规则：tools/research/quant_backtest → 投研/因子/回测；strategy_layer/pool → 策略平台；tools/datawarehouse → 数据仓治理；portal → 门户/API；agent_platform → Agent/学习；k_stock → 选股/paper；news_collector → 资讯。
- Top50 逐项引用次数与证据文件见 CSV（`reuse_refs`/`n_ref_files`/`consumer_hint`/`consumer_evidence`）。
- 无直接引用的项：建议先内部投研试用（tools/research 脚本或 notebook），验证价值后再申请门户暴露。

## 六、风险与审批说明

- 风险分级：域风险（开源情报/学习派生=高，平台运行=中，接口数据=低）与体量风险（≥2000MB=高、≥200MB=中）取较高者；PII/内容分级见 CSV `risk_notes`。
- 审批人：产品/门户评审（主）+ DATA4（owner）；开源情报/学习派生追加合规（PII）；平台运行追加系统 owner；提示词与数据使用范围需在评审单写明。
- 本包不改变 catalog/registry、不发起网络请求、不 commit；所有动作需审批通过后按批执行并留痕。

## 七、验收清单（DOD）

- [ ] CSV 50 行、5 批各 10 项、批次/风险/审批人/消费方/回滚字段齐全（本包已自检）
- [ ] 每批审批单收集完成（产品/门户评审 + DATA4 + 必要的合规/系统 owner）
- [ ] 每批上线前 contract_export 映射评审通过、回滚演练通过
- [ ] 上线后观察 1 个交易日无报错，消费方确认可用
