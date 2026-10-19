# AgentSpec.skills 播种报告（K-ORG-190）

> 2026-10-19 ｜ 执行工位：spec-seeder ｜ 方案：docs/plans/2026-10-19_Agent指挥平台迭代方案_K-ORG-190.md 二节 AgentSpec v1
> 性质：注册表侧"Agent 挂载技能"落地——对 14 个 team_*.json 的 agent 条目**只增不改**，新增 `skills` 数组（引用不内嵌，仅 `{id, kind, enabled}`）。

## 一、播种统计

| 项 | 数值 |
|---|---|
| 修改文件数 | 14 / 14（team_academy/chief/data/dev/invest/legal/marketing/product/proj_backtest/proj_data/proj_next/quality/quant_strategy/trading） |
| 播种 agent 数 | 44 / 44（全部 agent 均新增 skills 字段） |
| 新增条目合计 | **94 条**（tool 86 + algo 8） |
| 条目形态 | `{"id": <skill_registry 在册 id>, "kind": <registry 同款 kind>, "enabled": true}`——零内嵌实体，零 ref 冗余（ref 由 skill_registry.json 唯一真源持有） |
| 已有 skills 合并 | 无（播种前 44 agent 均无 skills 字段，全部为新增，无覆盖风险） |

### 各文件明细

| 文件 | agent | 新增 skills |
|---|---|---|
| team_academy.json | ACAD1/STRAT1/BACK1/COMM1/INTEL1/AI1/PROD1 | 各 1 条（events）= 7 |
| team_chief.json | A0A | 2（devtask/events） |
| team_data.json | DATA1 | 2（devtask/events） |
| team_dev.json | DEV1~DEV8/ARCH1/OPS1/DOC1 | 各 2（DEV2 为 ollama/events，余 devtask/events）= 22 |
| team_invest.json | T1/T2/N1/F1/D1 | 纯 algo：T1=quant_dual+tdx_backtest，T2=tdx_backtest+riskguard，N1=jev_sentiment，F1=score_ranking，D1=quant_dual+riskguard = 8 |
| team_legal.json | LEG1 | 1（events） |
| team_marketing.json | MKT1 | 1（events） |
| team_product.json | PM1/DSGN1/DSGN2 | 各 2 = 6 |
| team_proj_backtest.json | DEV3/DEV4/QA1 | 各 2 = 6 |
| team_proj_data.json | DATA1/DEV1 | 各 2 = 4 |
| team_proj_next.json | PM1/ARCH1/DEV2 | 各 2 = 6 |
| team_quality.json | QA1/SEC1/QA2/SEC2 | 各 2 = 8 |
| team_quant_strategy.json | ST1 | 19（tgraph_slots/tgraph_eval/tgraph_left_tail/tgraph_fps/signal_precedents/signal_enrich/events_for_stock/related_events/event_summary/recent_events/datamart_read/datamart_latest/datamart_ready/market_snapshot/data_health/pool_rows/stock_info/paper_positions/events） |
| team_trading.json | TR1 | 2（paper_bridge/events） |

## 二、映射规则与实证依据

1. **tools → skill 引用**：agent 现有 tools 数组逐个映射，**逐一核对 skill_registry.json 在册且 enabled=true** 才写入（v1 普查后 22 条 tool 全 enabled，无未 enabled 情形）。
2. **algo 映射（ai_market groups → team agent）**：只映射能实证对应的岗——
   - **invest 五岗（T1/T2/N1/F1/D1）**：ai_market id 与 team_invest.json agent_id **完全一致**，实证成立，algos 全部经 skill_registry 核对（6 款 algo 商品全在册）后写入。
   - **A0（algos=quant_dual/score_ranking）未播种**：实锤 org.json chief 段 `agent_id=A0`（参谋长田丰本人，`assistant: A0A`），而 team_chief.json 在编岗是 **A0A（参谋长助理）**——两者非同一岗（org.json: K-ORG-052 注"A0A 参谋长助理只调度不决策"）。A0 本体无 team_*.json 条目、chief 段不在本工位可写范围 → 按"宁缺勿错"不动。
3. **合并去重**：本批播种前无 agent 带 skills 字段；脚本内置 `(id, kind)` 去重与已有条目跳过逻辑，后续批次可重入。

## 三、跳过清单（查无在册，不写入）

| 文件 | agent | 工具码 | 原因 |
|---|---|---|---|
| team_invest.json | T1 | tdx_kline | 不在 skill_registry（技能普查报告已实锤：tdx_kline/news_feed/stock_fundamentals 三工具码全仓零实现，不登记待定夺） |
| team_invest.json | T2 | tdx_kline | 同上 |
| team_invest.json | N1 | news_feed | 同上 |
| team_invest.json | F1 | stock_fundamentals | 同上 |

备注：四条工具码仍保留在各 agent 的 `tools` 数组中（零改动）；若 DEV/ARCH 定夺后改声明映射（普查报告建议改 events_for_stock/datamart_read），届时重跑本播种即可。

## 四、未播种清单（对应关系拿不准，宁缺勿错）

| ai_market 岗 | algos | 原因 |
|---|---|---|
| A0（决策中枢） | quant_dual, score_ranking | ai_market id=A0 对应 org.json chief 段（agent_id=A0），非 team_chief.json 的 A0A（助理）；chief 段不在本工位可写范围 |
| SA | chanlun_9dim | 无对应 team_*.json 条目（工具台岗，无编制注册） |
| SP | chanlun_9dim | 同上 |
| KG | （空） | 同上 |
| CARD | chanlun_9dim | 同上 |
| BRIEF | score_ranking, jev_sentiment | 同上 |

## 五、安全链（判例平移）

1. **改前备份**：14 个 team 文件整份拷贝至
   `agent_platform/registry/binding_backups/20261019_091826_agentspec_seed/`（14/14 份）。
2. **改后校验脚本输出**（对比改前备份 vs 改后）：
   ```
   校验总结：14 文件全部 json 可解析、keys 只增不改、skills 仅含在册条目（共 94 条）、除 skills 外零改动
   RESULT: PASS
   ```
   校验内容：① json.load 全通过；② 顶层/agent 级 keys 集合**只增不改**（新增字段仅 `skills`）；③ skills 条目形态严格 `{id,kind,enabled}` 且全部指向 skill_registry 在册 enabled 条目；④ (id,kind) 无重复；⑤ 删除 skills 后与备份深比对**逐字节语义一致**（原有字段零丢失、零改动）。
3. **边界**：未触碰 org.json / skill_registry.json / model_tiers.json / portal 下任何文件；未 git add/commit；未重启服务。

## 六、interpreter 兼容性核验

1. **静态核验**（grep engine/interpreter.py）：`Team.validate()`（interpreter.py:86-111）消费字段为 `node_key`（by_node 索引）、`parent_node_keys`、`level`、`is_debate_node`、`debate_partner_key`，另有控辩审契约的 `role`/`role_profile`（K-ORG-166，interpreter.py:106-107）；全文件无任何 `skills` 字样。
2. **动态实测**：播种后用 `engine.interpreter.Team` 逐个加载 14 个 team 文件（构造即触发 validate()）：
   ```
   [OK] registry\team_academy.json: team=academy 7 agent 校验通过
   [OK] registry\team_chief.json: team=chiefoffice 1 agent 校验通过
   [OK] registry\team_data.json: team=datacore 1 agent 校验通过
   [OK] registry\team_dev.json: team=devcore 11 agent 校验通过
   [OK] registry\team_invest.json: team=alpha 5 agent 校验通过
   [OK] registry\team_legal.json: team=legalcore 1 agent 校验通过
   [OK] registry\team_marketing.json: team=mktcore 1 agent 校验通过
   [OK] registry\team_product.json: team=prodcore 3 agent 校验通过
   [OK] registry\team_proj_backtest.json: team=proj-backtest 3 agent 校验通过
   [OK] registry\team_proj_data.json: team=proj-datagov 2 agent 校验通过
   [OK] registry\team_proj_next.json: team=proj-nextver 3 agent 校验通过
   [OK] registry\team_quality.json: team=qualcore 4 agent 校验通过
   [OK] registry\team_quant_strategy.json: team=strategy 1 agent 校验通过
   [OK] registry\team_trading.json: team=tradeexec 1 agent 校验通过
   interpreter Team.validate() 实测：14 文件 / 44 agent 全通过，新增 skills 字段零影响
   ```

## 七、遗留

1. **零实现工具码定夺**（不在本工位权限）：tdx_kline/news_feed/stock_fundamentals 待 DEV/ARCH 拍板后，可改声明映射既有工具并重跑播种补齐 T1/T2/N1/F1 的 skills。
2. **A0 本体挂载**：ai_market A0 的 algos 若需落到注册表，需在 org.json chief 段加 skills（超出本工位可写范围，建议下批由有 org.json 写权限的工位执行）。
3. **SA/SP/KG/CARD/BRIEF**：工具台五岗无 team 编制条目，其 algo 挂载需先补编制注册（加 Agent 表单流程）或明确"无编制不挂载"口径。
4. **生效**：纯注册表数据增量，engine 侧零代码改动；无需重启即被下次加载读取（若 portal 指挥台需展示，随既定 ai-portal 重启批生效）。
