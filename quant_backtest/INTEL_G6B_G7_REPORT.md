# G6b + G7 集成收口详细总结报告

> 知识图谱量化情报系统 · G 系治理收官
> 日期：2026-09-30 · 工作目录：`e:\老田的AI助手\quant_backtest`
> 前置档案：INTEL_RD_PLAN.md 第八节（G1-G7 全记录）

---

## 〇、一句话总结

notice LLM 影子分类正式接入（方案① id 回填）+ Admiralty 来源评级体系上线，每日链从十步扩为十二步并整链验证全绿——事件情报的**覆盖面**（+38.7% 实质主题）与**可信度**（双维评级 + 断流可见）同时补齐，G 系（G1-G7）全部交付并产线运转。

---

## 一、交付概览

| 项 | G6b notice LLM 影子正式接入 | G7 来源可靠性评级（Admiralty） |
|---|---|---|
| 决策来源 | 用户拍板方案①（按 id 回填 events.jsonl） | 计划立项（G1-4 jev 断流教训驱动） |
| 核心改动 | kg_notice_llm.py（+--backfill）、start_kg_sync.ps1（+步骤） | kg_source_rating.py（新建）、kg_check.py（+第⑦项）、kg_portal.py（+sources）、portal_api.py（+端点/面板）、start_kg_sync.ps1（+首步） |
| 终端效果 | H1_event 样本 **96 → 144（+50%）** | 断流/catalog 滞后**可见化**（check 项 + 门户红点） |
| 产线状态 | 每日链第 3 步，Ollama 故障三重防护不阻断 | 每日链首步 catalog_build + graph_check 第⑦项 + 门户 `/kg` 面板 |

---

## 二、G6b：notice LLM 影子正式接入

### 2.1 背景与问题（G6 全量影子结论）

- 30 天窗 notice 全量 ~1.8 万条，规则词表命中 2,750 条，**未命中 15,199 条全部静默**
- 缺席主题域：正向资产重组（规则只有终止/bear 侧）、对外投资、股权转让、"限制性股票"类股权激励、"行政监管措施决定书"类处罚、"终止挂牌"类退市
- 全量影子分类（qwen3 本地，2.8h，fail 0.3% 兜底中性）捞出实质主题 **1,063 条**（bull 1,032 / bear 31，conf 均值 0.95）
- 一致性互检（499 条样本）：方向反转仅 0.2%；修正规则误命中 7%（"质押延期购回"≠回购——购回≠回购，prompt 补一句判别 + 删行重判即修复 8→1）

### 2.2 接入设计（单一 backfill 机制）

选择**每日回填**而非改 `_seed_notice`（源头改）：一个机制同时覆盖存量与增量，event_graph 规则零改动（克制原则）。

```
每日链：evt_build（新公告先入 events.jsonl）
   → kg_notice_llm --backfill（判定新行 + 按 id 回填）
   → kg_evt_sync（图同步时 bias 已带上）
```

**回填规则（四条铁律）**：

1. 只补 `kind=notice 且 bias ∉ (bull, bear)` 的行——**规则命中优先，永不覆盖**
2. LLM conf ≥ 0.7 且非中性才回填（tag / bias / conf / reason="llm:model"）
3. text 不变 → 向量矩阵无需重建；幂等（已有 bias 的行永远不碰）
4. 回填后行落 parquet 与 events.jsonl 双写一致

**Ollama 故障三重防护（不阻断每日链）**：

| 防护 | 行为 |
|---|---|
| 健康预检 | `/api/tags` 不通 → 跳过判定，仅回填已判行，exit 0 |
| 连续失败熔断 | 连续 10 条判定失败即停（已判部分已落盘） |
| 断点续跑 | 每 200 条落盘，次日自动补判缺口 |

### 2.3 传导链零改动验证

关键发现：`kg_build_m2` 的 group = `coalesce(e.tag, e.kind)`、基线组硬编码 `["notice","news","jev"]`——回填 tag 后新组**自动**落入实质组进入 DRIVE 打分，`kg_build_m2` / `kg_distill` 一行未动。接入只动了数据层与调度层。

### 2.4 全链刷新实证（backfill → p3 → m2 → features → distill → check）

| 指标 | 接入前 | 接入后 |
|---|---|---|
| Event 已判 bull/bear | 7,271 | 8,334（+1,063） |
| DRIVE 实质组 | 14 组 | 17 组（+对外投资/股权转让/资产重组） |
| DRIVE 边（Event→MarketEvent） | — | 4,823（notice 基线 3,916 + 实质组 907） |
| **H1_event 样本** | **96** | **144（+50%）** |
| H1 溢价方向 | pre -7.31 / post -2.05 | 不变（口径一致性 ✓） |
| kg_check | 全绿 | 全绿 |

图内效果实查（Neo4j）：

- 新组 tag 节点：对外投资 344 / 股权转让 164 / 资产重组 162 / 监管处罚 137 / 资产收购 133 / 退市风险 73（监管处罚/资产收购含原有规则命中）
- 新组驱动信号 216 个：对外投资 76 / 股权转让 42 / 资产重组 38 / 监管处罚 38 / 资产收购 17 / 退市风险 5
- 实质组驱动信号 804 / 全部驱动 2,939
- 实例（0924）：000679.SZ vol 信号 ← DRIVE:对外投资；002574.SZ / 600892.SH ← DRIVE:股权转让；300237.SZ ← DRIVE:资产重组——这些标的的缠论/量能信号此前只有 notice 基线佐证，现在有实质组佐证进入 H1 计数

---

## 三、G7：来源可靠性评级（Admiralty 双维）

### 3.1 设计：静态定档 + 动态监测分离

**源级 reliability（A-F，静态人工定档，10 源族）**：

| 档 | 源族 | 依据 |
|---|---|---|
| A | human_curation | 人工审核词表（可控性最高） |
| B | tushare_pro / xtcloud_local / rule_keyword / chanlun_engine / candle_local | 确定性管道或官方主通道（含各 G 系实证缺陷记录） |
| C | llm_local / cooccur_stat / crypto_feed | LLM conf 自报非校准 / 只算共现不判因果 / 低质量 feed |
| D | jev_feed | 远端断流教训（G1-4）、无 SLA |

**实例级 credibility（1-6，不回写，需要时现场推导）**：`judge_credibility(reason, conf)`——规则=2、LLM ≥0.9=2、0.7~0.9=3、未判=6。

**设计原则**：断流是状态非属性——**告警不降档，恢复即消；降档须新实证改依据**（防止告警风暴把整个体系搅乱）。

### 3.2 动态 staleness（断流可见化）

- catalog freshness 按**表名语义分档**预期节奏（FREQ_OVERRIDE：日频 5 天 / 月频 45 / 季频 130）+ 三类豁免（快照/事实表、探针、未消费链）
- 落盘 `data/kg_source_rating.json`：评级表 + staleness + 元检查

### 3.3 首跑三连发现（31→14→6→2→0 收敛史）

| 轮 | 超期表数 | 发现 | 修复 |
|---|---|---|---|
| 1 | 31 | catalog freq 字段全标 'D' 不可靠——季报财务表（fresh=20260630 正常节奏）被日频口径误报 | 按表名语义分档（FREQ_OVERRIDE） |
| 2 | 14 | **抓到真问题**：catalog freshness 停 0924，把实到 0928 的 ts_top_inst（G4b 输入）误报断流——根因 build_catalog 从无自动调用（数据在流 catalog 不知） | rating 加**元检查**（catalog_age>1 天告警"freshness 全表失真"）+ **每日链首步加 catalog_build** 根治 |
| 3 | 6→2→0 | 剩余表按语义归位（moneyflow=月度策略、report=低频研报） | 终态全绿 |

### 3.4 消费闭环（数据流）

```
每日链 catalog_build（G7 新首步，根治元问题）
  → ... → graph_check 第⑦项 source_fresh（warn-only，刷新 rating JSON）
  → 门户 /kg/sources（JSON 端点）
  → /kg 首页折叠面板（档徽章 A绿/B蓝/C黄/D红 + 依据摘要 + 断流表数红点 + catalog 滞后提示）
```

---

## 四、集成收口：每日链十二步整链验证

```
catalog_build(G7新) → evt_build → notice_llm(G6b新) → pull_global_idx
→ kg_evt_sync → cl1_sync → m2_drive_sync → candle_sync
→ kg_features → distill_build → graph_check(含⑦source_fresh) → kg_brief
```

**整链实跑全绿**（11:37:46 → 11:40:55，约 3 分钟）：12 步全部 exit=0；notice_llm 空转 2 秒（待判 0——断点续跑语义正确，昨晚已全判）；kg_brief 飞书推送成功。日志顺带确认 0929 08:52 链曾在 evt_build 中止（G5 期间 Neo4j 挂掉），已被计划任务自愈。

运维口径：每日链新增本地 Ollama 依赖（~600 条/日 ≈ 7 分钟），步骤日志 `C:\Temp\kg_notice_llm_out.log`。

---

## 五、关键数字汇总

| 维度 | 数值 |
|---|---|
| LLM 影子判定 | 15,199 条全判（fail 0.3% 兜底中性） |
| 实质主题捞回 | 1,063 条（对外投资 344 / 股权激励 315 / 股权转让 164 / 资产重组 162 / 合作协议 26 / 监管处罚 21 / 增持 9 / 退市风险 8…） |
| 实质主题事件池 | 2,750 → 3,813（**+38.7%**） |
| H1_event 样本 | 96 → 144（**+50%**） |
| Admiralty 评级 | 10 源族（A×1 / B×5 / C×3 / D×1） |
| staleness 监控 | 71 catalog 表按语义分档，终态 0 超期 |
| 每日链 | 10 步 → 12 步，全部幂等 |
| 门户 | /kg 首页新增评级面板 + /kg/sources 端点 |

---

## 六、经验教训（均已归档 INTEL_RD_PLAN.md）

1. **Cypher 列表必须方括号**：`IN ['a','b']`（圆括号=语法错）；图内信号载体是 `MarketEvent`，DRIVE 边为 `Event -[DRIVE]-> MarketEvent`（不是 sec→sec）
2. **改复合路由条件须逐路径回归**：门户路由加 `/kg/sources` 时误删 startswith 分支，/kg/stock/ 等四路径失联——立即补回
3. **告警判据自身会过期**：catalog freshness 误报证明"监测者也要被监测"——加元检查（catalog 年龄 > 1 天即告警 freshness 全表失真）
4. **PowerShell 引号陷阱**：内联 python 含 `\"` 与 `<`/`>` 保留字会解析错误——复杂查询一律写临时脚本文件
5. **影子纪律有效**：G6 全量影子 + 互检先行，才让"方案①回填"成为低风险决策（规则优先/幂等/防护三原则在影子期已验证）

---

## 七、G4b-WL：watchlist 人工审核闭环（同日追加，G 系最后遗留项）

### 7.1 审核方法
图内无 watchlist 席位数据（影子层不写图的克制设计），证据走 **parquet 直算**（复用 kg_communities._load）：同日同标的共现（按注册主体聚合）+ 交易风格（净买方向占比 / 单日均额）。

**净买方向是马甲判别决定性证据**：游资 = 大额净买入打板；机构 = 大额净卖出调仓上榜。

### 7.2 判读与拍板（三项，均已落地）

| 席位 | 证据 | 决策 |
|---|---|---|
| 开源·西安西大街（570 日）+ 太华路（229 日） | 净买 63%/61%，单日均额 **+1251万 / +1697万**，与章建平(161·153)/小鳄鱼(89·109)/国泰海通系马甲高频同框 | **入词表为独立主体"开源西安系"**（不预设集团归属） |
| 国信·浙江互联网分公司 | 净买 57% 但单日均额仅 +76万 | 暂不入，留 watchlist 继续观察 |
| 7 个总部/QFII 席位（国泰海通总部/高盛浦东/中信上海/瑞银花园石桥/摩根大通银城/中金上海/华泰总部） | 净买仅 24-45%，单日均额 **-1163万 ~ -2743万 大额净卖**，共现 top1 全是拉萨散户通道 | **UNREG_EXCL 扩排除**（"总部"+QFII 名单+2 精确分公司名） |

### 7.3 意外发现：24 个孤儿 HotMoney
历史"席位级"词表（券商·营业部简称格式）换版为人物级词表后**节点从未清理**，两代词表共存图内污染 OCCUPIES 反查映射（实证：社区 anchor 名曾被带歪为"华泰·上海武定路 集团"等孤儿命名）。已按当前词表 20 主体 DETACH DELETE 清理。教训：**词表换版须清图内孤儿**（upsert 只增不删）。

### 7.4 重建后图内
- 词表主体 20/20、席位 80、TRADED_ON +6,970
- 社区重排干净四集团：**开源西安系 81**（章建平在其中）/ 宁波桑田路 49 / 章建平 43 / 方新侠 14
- watchlist 换血 10 全新候选（机构席位全部退出 ✓，国信浙江互联网留观察 ✓）
- 归因链抽检：开源西安系 → ST金顶 23 次净买 1.44 亿、平潭发展 1.80 亿——典型游资打板画像

### 7.5 下轮审核提示
新 watchlist 出现东财西藏系席位（山南香曲东路/昌都两江大道），疑与拉萨天团同源散户通道——RETAIL_EXCL 目前只匹配"拉萨"子串漏网，届时可扩排除。

---

## 八、遗留事项（未立项）

- ~~watchlist 人工审核入词表~~（✅ 2026-09-30 完成，见第七节 G4b-WL）
- **LLM 影子季度复评**：prompt/模型升级时跑 `--eval` 互检
- **brief 消费评级**：飞书简报可选择性提示 D 档源异常（当前克制不接）
- **下轮 watchlist 审核**：东财西藏系席位排除确认（见 7.5）

---

## 九、涉及文件清单

| 文件 | 变更 |
|---|---|
| quant_backtest/kg_notice_llm.py | +`--backfill`（判定+回填+三重防护） |
| quant_backtest/kg_source_rating.py | 新建（Admiralty 源级+实例级+staleness+元检查） |
| quant_backtest/kg_check.py | +第⑦项 source_fresh |
| quant_backtest/kg_portal.py | +`api("sources")` |
| portal/portal_api.py | +`/kg/sources` 端点 + /kg 首页评级面板 + `.bdg.b` 样式 |
| quant_backtest/start_kg_sync.ps1 | 12 步（+catalog_build、+notice_llm） |
| quant_backtest/kg_build_cl1.py | YOUZI +开源西安系主体（19→20）（G4b-WL） |
| quant_backtest/kg_communities.py | UNREG_EXCL 扩机构通道排除（G4b-WL） |
| quant_backtest/INTEL_RD_PLAN.md | G6/G6b/G7/G4b-WL ✅ 段归档 |
| data/semantica/notice_llm.parquet | 15,199 条影子判定 |
| data/kg_source_rating.json | 每日链刷新的评级快照 |
| data/kg_communities.json | 重建（开源西安系入图后四集团 + 新 watchlist 10） |
| Neo4j event_graph | +24 孤儿 HotMoney 清理（DETACH DELETE） |

---

*G 系（G1-G7）全部交付 + G4b-WL 审核闭环完成。下一步方向见 INTEL_RD_PLAN.md 待办区。*
