# KG 更新通知台账（KG-notify v1.1 · 郑玄 KGMAP1 维护）

> 协议：docs/standards/2026-11-08_知识图谱更新通知协议_v1.md
> 每条通知：时间、级别、范围、触达席位、回执状态

| # | 时间 | 级别 | 更新范围 | 影响查询 | 触达席位 | 回执 |
|---|---|---|---|---|---|---|
| 1 | 2026-11-08 21:50 | **L2** | 新增节点标签 `IntelAuthor`（19,317：xueqiu 5,098 uid 身份 + guba 14,219 name_only 身份）+ 新增关系类型 `AUTHORED_BY`（54,421 实连，链接率 85.2%，缺源帖部分等 intel_link 增量补齐）+ `IntelAuthor.author_key` 索引。guba 身份为昵称（无 uid），重名风险已用 identity_strength 标注 | 现有 query_recipe 四类查询**不受影响**（纯增量）；新增博主级聚合配方：`MATCH (a:IntelAuthor)<-[:AUTHORED_BY]-(i:IntelItem)-[:MENTIONS]->(s:Security) RETURN a.author_key, count(DISTINCT s)` 供声量/情绪聚合 | FACTOR1 蒋琬（声量/分歧度因子新输入）、D1（invest 域接口）、QA1、JEV（dump schema 向后兼容）、满宠（MDC 不受影响）、INTEL0（归口） | 已结算：六席默认接受（2026-11-13 17:00 到期条款，2026-11-08 无异议结算，WO-INTEL-KG-201 已关闭） |
| 2 | 2026-11-08 22:05 | **L2** | 新增节点标签 `KnowledgeUnit` 481（research_reports_kb 463 + strategy_classics_kb 18）+ `KnowledgeDoc` 100 + 关系 `DERIVED_FROM` 481（孤儿 0）+ 双索引。**ABOUT→Security 留 P2**：单元文本 0% 显式代码，AP→ts_code 映射需外部来源（提请 DATA1 张松线评估映射表） | 现有查询**不受影响**（纯增量）；新增研报复查配方：`MATCH (u:KnowledgeUnit)-[:DERIVED_FROM]->(d:KnowledgeDoc {kb:'research_reports_kb'}) RETURN u.title, u.claim, u.doc_uri` | 同 L2-1 六席 + DSH-Lead（其 steward 两个 KB 已入图） | 已结算：同 #1（六席默认接受，WO-INTEL-KG-201 已关闭） |
| 3 | 2026-11-08 22:58 | **L2** | P2 落地（WO-KG-202）：新增关系 `ABOUT` 94 条（KnowledgeUnit→Security，覆盖 32 单元 / 83 标的，空端点 0，全部 name 通道）。**不干等 DATA1**：复用正图 Security 三级索引词典 + intel_link 全套护栏做文本映射；试算 105 条经逐条上下文复核剔除 11 条误配实锤。残余缺口：463+18 单元中 449 个单元无标的命中（宏观/方法论类语料属正常）；降级候选 22 条（常用词/通用后缀/2 字短名/同名多码）随报告 JSON 供 DATA1 到位后复核补边 | 纯增量新关系；新增配方：`MATCH (u:KnowledgeUnit)-[:ABOUT]->(s:Security) RETURN u.concept, s.name`；GIN 风险提示：ABOUT 为词典扫描产物（via=name），因子侧使用建议叠加 confidence 过滤 | 同 L2-1 六席 | 已结算：同 #1（六席默认接受，WO-INTEL-KG-201 已关闭） |

**P1（2026-11-08 22:10）**：在役链 `quant_backtest/start_kg_sync.ps1` 插入 `intel_author` 步（intel_xq_guba 之后），每日新帖作者边自动挂接；PS1 语法校验通过；回滚点 `C:\项目\backups\start_kg_sync.ps1.bak-kgmapP1-20261108`。

**P2（2026-11-08 22:58，WO-KG-202）**：`zerobound-kg/kg_knowledge_ap_mapping.py` 两段式（--plan 纯读试算 / --live 写门放行）；ABOUT 94 条正图写入并终验。副产品（影响 intel_link 在役行为，随本条知会 QA1/FACTOR1）：
- 护栏热修词表 `quant_backtest/data/graph_check/common_word_terms.txt` 新建，增补 6 词（中国建筑/国新能源/太阳能/海量数据/新产业/中国稀土）——研报语料逐条上下文复核实锤，新闻链接同步受益；明日链首跑起生效，intel_link 误配实锤沉淀流程照旧
- 判例两则：①act_name 通道在研报语料 5/5 全误配（"向平台化"内嵌"向平"），P2 裁剪该通道，Security.act_name 字段质量已提请 DATA1 复核；②.env.local 缓存的 KG_DATA_ROOT 会把状态目录带偏（护栏词表静默失效），脚本 import 前剔除两键

**回执回流（2026-11-08 22:40 启动）**：L2-1/L2-2/L2-3 三事件合并走 WO-INTEL-KG-201，截止 **2026-11-13（周五）17:00**，未回执默认接受（INTEL0 可否决）；11-12 上午催办一次。

**P2 复核收口（2026-11-08 23:40，老田 23:16 令）**：DATA1 三件复核完成（报告：`deliverables/2026-11-08_WO-KG-202_DATA1三件复核报告.md`）。
- **候选复核**：22 条降级候选 → 19 条维持（护栏行为正确）+ 3 条真误杀救回（同花顺×2/东方财富×1，via=name_manual_review，reviewed_by 郑玄）。**ABOUT 终态 97 = 词典 94 + 人工 3**。
- **无命中核验**：449 无命中单元（rp 431 + 米勒维尼 18）抽样证实为海外标的/宏观/方法论语料，正常非缺陷。
- **⚠ act_name 字段语义污染（升级 P1，提请 DATA1 张松线 + 老田裁定）**：Security.act_name 5,572 条实为「实控人/董事长名」（与 stock_company.chairman 逐案一致，608 条 ≤2 字人名；中国建筑 act_name=国资委机关名）。根因：本地数仓 join 误标 chairman→act_name（上游脚本待追认）。**影响面含在役链**：intel_link 新闻 act_name 通道对 608 人名无护栏。修复建议：①intel_link build_index 加 act_name len<3 gate（需老田批准）；②parquet 列重建；③图侧清洗/重命名 controller_name。P2 已裁剪该通道自保。

**act_name gate 落地（2026-11-08 23:45，老田 23:38 批「按计划实施」）**：`kg_intel_link.py build_index` act_name 通道加 `len<3` 闸门（与 cnspell 同款，边=命中铁律工程化）。验证：单测 17 passed；gate 单元断言（2 字不入典/3 字保留/name+cnspell 不动）；正图词典 act_name **4066→3510、2 字残留 0**。提交 zerobound-kg；回滚点 `C:\项目\backups\kg_intel_link.py.bak-actgate-20261108`。明晨链首跑起生效——QA1/FACTOR1 知会：新闻 MENTIONS 的 act_name 通道误配面即刻收敛；真实曾用简称后续由 DATA1 走 tushare namechange 重建（≥3 字不受 gate 影响）。**→ 2026-11-08 23:10 结算完成**：六席均未注明异议，按默认接受条款（2026-11-13 17:00 到期）落定，WO-INTEL-KG-201 已关闭；INTEL0 否决权保留至 2026-11-13 17:00。

## 维护说明

- 台账随 git 留痕；L1 例行通知可按日合并一行（周报时归档）
- 回执：L2/L3 需订阅席显式回执；本表「回执」列记录状态
