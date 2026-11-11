# JEV · S0 + S1 情报成品输出口径规格 v1

> **状态：规格稿（纯新增，未实施）**。本文定义「输出该长什么样」与「命中该怎么解释」，
> **不改代码、不改既有规格、不进判定闸、不改 v2 真相源**。任何落地实施须老田另批。
> **上位**：`docs/JEV_情报学方法论深度研究.md` §二（S0）、§三·二（S1）、§七（能力账本）。
> **并列**：`docs/jev_pattern_spec.md`（P4 口径）、`docs/jev_gate_a2_spec.md`（条件②唯一执行口径）、
> `docs/jev_deltamin_spec.md`、`docs/JEV_P5_2_单日可行性验证.md`、`docs/JEV_研究手册.md`。
> **日期**：2026-11-11
> **口径纪律**：本规格中的数字与标识符均已回查本仓原文并做码点复核；引用学理数值一律标注「非本仓校准值」；
> 规格不改变任何历史读数，P5.2 的 34 / 63、54.0% 等既有结论**无需重算**。

---

## 〇、范围与定位

情报学深度研究给出的最高优先级结论是：JEV 已把「信息 → 态势描述」做扎实（结构同构、SHA256 无损、
append-only 留痕、只读实证），但**停在 OSIF + JDL L2**，缺「态势描述 → 情报成品」这一段。
本规格把其中两条（S0、S1）从研判结论写成**可机械验收的字段契约与推理口径**。

| 项 | 本规格交付 | 本规格**不**交付 |
| --- | --- | --- |
| **S0 产品定义** | `requirement_id` / `product_kind` / `fact_vs_judgement` 三字段的类型、取值域、挂载点、缺省、验收标准 | 字段写入代码、PIR 需求登记簿实体、概率词表的本仓校准 |
| **S1 推理口径** | 把 `≥2 维度命中即记` 改述为**竞争假设排除**口径；H1–H4 矩阵、诊断性判据、ACH 输出契约、「不吠的狗」硬闸门 | 改 `MIN_DIMS` 运行时行为、改命中集合、改阈值、重跑案例库、新增闸位条件 |
| 共同 | 与三旗标/`unpriced`/采集渲染隔离的兼容矩阵；14 条文档级验收判据 | 任何生产文件写入、任何判定链变更 |

**一句话**：S0 让受众知道「这份输出回答什么问题、是哪类产品、哪些是事实哪些是判断」；
S1 让 matcher 的命中从「凑够两个维度」降格为「可供排除的证据行」，结论改由**反证最少**决定。

---

## 一、依据与术语

### 1.1 学理坐标（只列规格要用到的，展开见深度研究稿）

| 概念 | 出处 | 本规格的用法 |
| --- | --- | --- |
| Intelligence ≠ Information | 《中国情报学论纲》；DoD/ODNI PL 109-163；Steele OSD→OSIF→OSINT→OSINT-V | 情报须由**特定情报需求（PIR）**牵引、面向特定受众、经加工核验 → 导出 `requirement_id` |
| 产品分类四型 | Sherman Kent (1949)；CIA《Curing Analytic Pathologies》 | basic descriptive / current reportorial / current evaluative / estimative → 合并为三值 `product_kind` |
| ICD 203 | 美国情报界分析标准 | 区分「情报信息」与「分析人员假设/判断」；概率词表固定；**likelihood 与 confidence 分开** → 导出 `fact_vs_judgement` |
| ACH / 诊断性 | Heuer《Psychology of Intelligence Analysis》(CIA, 1999)；IARPA | 与所有假设一致的证据诊断价值为零；找反证排除，不找确认项累加 → 导出 S1 |
| 「不吠的狗」 | IARPA；Heuer | 缺失指标 ≠ 事件不存在的证据，除非先证明采集姿态稳健 → 导出硬闸门 |
| Kent 概率词 | 《Words of Estimative Probability》(CIA CSI, 1964) | 固定词表，**参考锚点非本仓校准值**，不得用于判定 |

### 1.2 本仓已核对的落点（只读核对，精确到位置）

| 事实 | 位置 |
| --- | --- |
| 「≥2 维度命中即记」的口径原文 | `docs/jev_pattern_spec.md` §2 题注、§3 匹配器契约 |
| 「下列『预期演化』均为待验证假设……不得当作结论」 | `docs/jev_pattern_spec.md` §2 题注 |
| `MIN_DIMS = 2`；`DIM_WEIGHTS` 三维度等权 | `portal/jev_pattern.py` 常量段 |
| `n_true < MIN_DIMS: continue`；`confidence` = 命中维度权重和 / 全部权重和 | `portal/jev_pattern.py::match_signal` |
| `Hit` = `pattern/name/dims/values/confidence/hypothesis/fork` | `portal/jev_pattern.py::Hit` |
| `summarize` 恒带 `calibrated: False` | `portal/jev_pattern.py::summarize` |
| `validate()` 恒带 `feasibility_only=True / gate_eligible=False / calibrated=False` | `portal/jev_casebook.py::validate` |
| `AVAILABILITY_PROXY_SIGNALS` + `countable=False` 伪分化节点判例 | `docs/JEV_P5_2_单日可行性验证.md` §5.1 |
| `unpriced`「绝不硬算」、`echo_ratio` 先验 | `docs/jev_deltamin_spec.md` §〇、§二 |
| 信号可达性天花板 **51.8%**、**48.2%** 问题域外 | `docs/jev_gate_a2_spec.md` §五 |
| 可达率 **69.9%**、**99.7%（1,495/1,500）** 抽样归因 | `docs/jev_gate_a2_spec.md` §十一 |
| 图结果 **17** 个顶层键；`chain` 是顶层键不在 `focus` 下 | `tmp/jev_page_schema_brief.json`、`tmp/jev_8091_compare.md` |
| `_bridge` 独立命名空间、payload 逐字节无损 | `skills/jev-graph-sqlite-prototype/` 原型验收回执 |

### 1.3 术语

- **PIR**（Priority Intelligence Requirement）：本次输出要回答的**单一特定问题**。
- **事实**：可由 `source_sig` / 快照 / `minute_read` / `ingest_audit` 回溯的观测值。
- **判断**：分析人员对事实的解释、归类或投影；不是事实，必须显式标注。
- **诊断性证据**：能把至少两个竞争假设区分开的证据；与全部假设一致的证据诊断价值为零。
- **证据行**：一条证据 + 它对假设集中每个假设的一致性标注（ACH 按行扫描的最小单位）。

---

## 二、通用挂载契约（S0 / S1 共用）

### 2.1 命名空间

三字段与 ACH 契约一律挂在**新增独立命名空间 `_product`** 下，与 payload 原有 **17** 个顶层键并列：

```
{
  ...原有 17 个顶层键，一字不改...,
  "_product": {
     "requirement_id": ..., "product_kind": ..., "fact_vs_judgement": {...},
     "ach": {...}
  }
}
```

**理由**：原型已用 `_bridge` 独立命名空间验证过「不覆盖、不改名、payload 可逐字节复现」。
沿用同一纪律，可保证既有 SHA256 端到端无损验收不因 S0 失效。
**禁止**把新字段塞进 `universe` / `focus` / `network` / `chain` 等既有业务键。

### 2.2 挂载点（实施时统一，不每层各起名字）

| 层 | 出处 | 挂载形态 |
| --- | --- | --- |
| 图结果 | `api_jev_graph.compute()` 直出 | 顶层 `_product` |
| sqlite 原型 | `graph_json.payload` | 随 payload 落库，字段名同上 |
| pattern 层 | `Hit` / `summarize` 输出 | `_product`（`Hit` 内为引用，不复制 `values`） |
| 案例库 | `validate()` 输出 | `_product` + 三旗标原样并列 |

### 2.3 三条不变式（违反即规格失效）

1. **payload 无损**：除 `_product` 外，原有键集合、类型、数值不变；不得以「产品化」为名清洗、补值或改口径。
2. **三旗标不失守**：`coverage_only=True / gate_eligible=False / calibrated=False` 原样保留，
   且 `validate()` 的 `feasibility_only=True` 不得删除。S0 是把这份「不够」写清楚，**不是让它变绿**。
3. **不进闸**：`_product` 及 `ach` 的任何字段**不得**进入判定闸 A/B 的条件、分子、分母或阈值。
   `docs/jev_gate_a2_spec.md` 仍是条件②**唯一**执行口径。

### 2.4 缺省与向后兼容

- 未实施前，所有现有输出**不含** `_product`，行为与今天完全一致；本规格不要求也不暗示回填。
- 实施时若某层无法提供 `requirement_id`，填 `UNSPECIFIED`，并**强制** `product_kind=descriptive`
  且 `fact_vs_judgement.judgements=[]`；**同时禁止 `estimative`**（无 PIR 不得出推演产品）（§3.1）。
- 缺字段按「未评估」处理，**绝不推断、绝不补默认值充当结论**；与 `unpriced` 纪律同构。

---

## 三、S0 · 输出产品契约

### 3.1 `requirement_id`

| 属性 | 定义 |
| --- | --- |
| 类型 | `str`，非空 |
| 格式 | `PIR-<域>-<三位序号>`，例 `PIR-JEV-001`（**仅为格式示例，非已登记需求**） |
| 语义 | 本次输出回答的**单一**特定问题；一个输出只挂一个 |
| 必填 | 是（缺省 `UNSPECIFIED`，见 §2.4） |
| 登记簿 | **本规格不新建、不定义 PIR 内容与文件路径**。PIR 内容定义权归上级；登记表结构与覆盖状态由采集工位（OSINT1 / C1）维护，本规格只在输出侧**引用** `requirement_id`。实施前须与 C1 对齐单一路径，禁止两份同名登记表 |
| 约束 | 不得用「跑一次图」「看看今天数据」这类无判别力的问题充当 PIR；问题须能被某条证据支持或削弱 |

**示例问题**（取自本仓真实判例，非新增需求）：「新型电力的涨停是消息驱动还是价格先行？」
—— 它可被 `Δmin` 带符号、`echo_ratio`、`ts_kind` 支持或削弱，符合 PIR 要求。

### 3.2 `product_kind`

三值闭合枚举，**缺省 `descriptive`**：

| 值 | 对应 Kent | 学理要求 | JEV 对应物 | **验收标准（各自独立，不共用「准不准」）** |
| --- | --- | --- | --- | --- |
| `descriptive` | basic descriptive + current reportorial | evidence-based、可复核 | 整图、`boards`、`network`、`minute_read` 三档台账、三旗标 | 结构同构、字段无损、来源可追溯、同输入可复现（SHA256） |
| `evaluative` | current evaluative | 事实与判断分栏、概率与置信分开 | `Hit`、`summarize`、11-06 判例 | 判断项逐条标注、来源与假设显式、**命中不得写成结论** |
| `estimative` | estimative / warning | 允许以置信度换证据硬度；须列备选、证伪条件与里程碑 | P1–P8「预期演化 / 分化节点假设」 | 竞争假设集、诊断性证据行、证伪条件、敏感性分析、后验里程碑 |

**硬约束**：

- `product_kind` **只决定验收标准，不改变任何数值**，不得用它绕过 `calibrated=False`。
- `coverage_only=True` 时**禁止**产出 `estimative`；`gate_eligible=False` 时 `evaluative` 的判断项
  必须带「不得进闸」标记。
- 三档产品可同时存在于一次输出的不同区块，但每个区块只能标一个值，禁止「既是描述又是推演」。

### 3.3 `fact_vs_judgement`

```
_product.fact_vs_judgement = {
  "facts":      [ {"field": str, "value": any, "source_ref": str}, ... ],
  "judgements": [ {"statement": str, "likelihood": str, "confidence": str,
                   "basis": [str], "falsified_by": [str]}, ... ]
}
```

| 规则 | 内容 |
| --- | --- |
| `facts` 准入 | 只放可回溯观测；`source_ref` 指向 `source_sig` 文件项、`minute_read` 台账、`ingest_audit` 行或快照路径 |
| `facts` 禁令 | 禁止补值；禁止把 `unpriced` 写成 `0` / 「无消息」/ 「无事件」；禁止把 `absent` 写成 `False` |
| `judgements` 准入 | 每条必须同时带 `likelihood` 与 `confidence`，两字段**独立并列**，不得合并成一句 |
| `basis` | 至少一条 `fact.field` 或 `ach.evidence_rows[].evidence_id`；空 `basis` 的判断项不合法 |
| `falsified_by` | 每条判断须写出「什么观测出现就算它被推翻」；写不出则该条只能降级为 `facts` 的描述 |

**`likelihood` 词表**（Kent 固定词；参考锚点引自深度研究稿 §3.2，**非本仓校准值、不得用于判定**）：

| 词 | 参考锚点 |
| --- | --- |
| `almost certain` | ≈93% |
| `probable` | ≈75% |
| `chances about even` | ≈50% |
| `improbable` | ≈30% |
| `almost impossible` | ≈7% |
| `unassessed` | 未评估（不是「不可能」） |

**`confidence` 取值**：`high / moderate / low / unassessed`，与本仓三旗标**同侧**：

| 既有旗标状态 | 允许的 `confidence` 上限 |
| --- | --- |
| `calibrated=False` 或 `gate_eligible=False` | **`low`** |
| `coverage_only=True` | `unassessed`，且不得产出 `estimative` |
| 未来 `calibrated=True`（需另批） | 由届时校准口径决定，本规格不预设 |

**禁止混写示例**：「高置信度认为很可能回落」❌ —— 把概率与置信揉在一句，ICD 203 明令禁止。
正确写法：`likelihood=probable; confidence=low`，并分栏展示。

### 3.4 S0 验收判据

| 编号 | 判据 |
| --- | --- |
| V1 | 三字段均有明确类型、闭合取值域与缺省行为；`product_kind` 恰好三值、`confidence` 恰好四值 |
| V2 | `_product` 为独立命名空间，且明令禁止改动既有 **17** 个顶层键 |
| V3 | 挂载点覆盖图结果 / sqlite / pattern / 案例库四层，四处同名 |
| V4 | `UNSPECIFIED` 时强制 `descriptive` 且 `judgements=[]` |
| V5 | `likelihood` 与 `confidence` 分栏、禁止混写，且数值锚点标注「非本仓校准值」 |
| V6 | 三旗标与 `product_kind` / `confidence` 的耦合约束写成可执行硬表，不得留模糊表述 |

---

## 四、S1 · 竞争假设排除口径

### 4.1 现行实现（逐字复述，不评价、不改动）

- `docs/jev_pattern_spec.md` §2：「匹配器（P5.1）采用『≥2 维度命中即记』的宽松策略，不要求全中」；
  §3：「命中 ≥2 维度即记入该 pattern」。
- `portal/jev_pattern.py`：`MIN_DIMS = 2`；`match_signal()` 计算 `n_true`，`n_true < MIN_DIMS` 则跳过；
  `confidence` = 命中维度权重和 / 全部权重和（三维度等权）。
- 同文件 `Hit` 已带 `hypothesis`（预期演化）与 `fork`（分化节点假设）；
  `jev_pattern_spec.md` §2 明写这些「均为**待验证假设**，未经 P5 案例库检验前不得当作结论」。

**即：本仓的假设登记早就显式存在，且比多数系统自觉。问题不在有没有假设，在推理方向。**

### 4.2 语义改述（规格层，命中集合不变）

| # | 现行语义 | **改述后语义** |
| --- | --- | --- |
| 1 | `≥2 维度命中` → 记为一次 pattern 命中 | → 记为一批**待排除的证据行**（`candidate_evidence`），不是结论 |
| 2 | `confidence` = 命中维度权重 | → **字段值不变**，只改解释：仅作**线索权重**；禁止读作「结论置信度」，与 `fact_vs_judgement.confidence` 不是同一个量 |
| 3 | 判定 = 数确认项够不够 | → 判定 = **在竞争假设集中，哪些假设被诊断性证据排除**；结论是**反证最少**的假设 |
| 4 | 三维度等权累加 | → 与全部假设都一致的证据（如「当日有涨停」「该票在样本池内」）**诊断价值 = 0，不得计分** |
| 5 | 按 pattern 逐个看命中 | → 按**证据逐行**扫假设矩阵（ACH 逐行而非逐列），避免锚定首选假设 |
| 6 | `hypothesis` / `fork` 是附注 | → 升级为**必参与竞争**的假设行；每条须带证伪条件与里程碑 |

**关键保护**：本规格**不改变 `MIN_DIMS` 的运行时行为、不改变命中集合、不重跑案例库**。
因此 P5.2 的 34 / 63、54.0% 及八个 pattern 的样本分布**全部原样有效、无需重算**。
改的是「命中之后怎么解释」，不是「什么算命中」。

### 4.3 竞争假设矩阵 H1–H4

| 假设 | 预期诊断性证据 | JEV 已有字段 | 不一致 / 反证证据 | 证伪条件（里程碑） |
| --- | --- | --- | --- | --- |
| **H1 消息驱动** | 新闻在前、封板在后；`echo_ratio` 高 | `delta_kind`、`echo_ratio`、`T_news_first` | Δmin 显著为正（消息追认）；`echo_ratio` 低 → 「消息驱动」只是噪声 | 价格封板时刻早于最早新闻时刻，且排除采集延迟 |
| **H2 价格先行 / 消息追认** | Δmin 正且大；消息 `ts_kind` 落盘后或日级追认 | `Δmin` 带符号、`ts_kind` | 存在盘前/盘中的分钟级对应新闻 | 找到与板块同步的盘中首发新闻，且 `ts_kind=intraday` |
| **H3 散户接盘** | `divergence`（主力净买但全档净流出）+ 股吧热、雪球机构榜冷 | `divergence`、`guba_rank`、`xq` 四榜 | 机构榜同步在榜且全档净流入 → 转 H3 的反面 | 后续多日出现机构资金持续净流入且社交口径同步转热 |
| **H4 采集覆盖伪影** | 锚点缺失、`minute_read=fail`、快照不合规、`unpriced` | `unpriced`、`minute_read` 三档、P2.3 三旗标、`AVAILABILITY_PROXY_SIGNALS` | 三源齐备、合规快照、且诊断性证据仍成立 | 采集充分性证明成立（§4.5），且三维度均有可回溯观测 |

**H4 必须进矩阵，且优先于业务假设评估。** 依据 `docs/JEV_P5_2_单日可行性验证.md` §5.1：
`time_ready` 实为「该票有本地分钟数据」，分出来的是大/小盘覆盖偏倚而非信号分化，
已用 `AVAILABILITY_PROXY_SIGNALS` + `countable=False` 拦下 —— 这正是「把数据没采到」
当作与业务假设**平权**的竞争解释。H4 若缺席，同类伪分化节点会再次出现。

### 4.4 ACH 输出契约

```
_product.ach = {
  "hypothesis_set": ["H1","H2","H3","H4"],
  "evidence_rows": [
    {"evidence_id": str, "source_ref": str, "is_absence": bool,
     "diagnosticity": {"H1": "consistent|inconsistent|not_applicable|unassessed", ...}}
  ],
  "excluded":       [ {"hypothesis": str, "reason": str, "by": [str]} ],
  "sensitivity":    [ {"evidence_id": str, "if_wrong": str, "conclusion_change": str} ],
  "falsification_conditions": [ {"hypothesis": str, "condition": str} ],
  "milestones":     [ {"at": "T+1|T+3|T+5", "observe": str} ],
  "absence_guard":  { "coverage_proof": [str], "verdict": str }
}
```

| 规则 | 内容 |
| --- | --- |
| `evidence_rows` | 每条证据必须标 `diagnosticity`；全为 `consistent` 的行诊断价值为零，须显式标出而非删除 |
| `excluded` | **必填**；无排除项时写 `[]` 并说明「证据不足以排除任何假设」，禁止省略字段 |
| `sensitivity` | 至少覆盖一条关键证据：若其错、被误导或可另解，结论如何变（ACH 第七步） |
| `falsification_conditions` | 四条假设各一条；写不出即该假设尚未成形，只能列为观察项 |
| `milestones` | 未来观测点，与 S3 后验回流台账对接（**本规格不实现 S3**） |
| `is_absence=true` | 必须带 `absence_guard` 引用；未附采集充分性证明的缺失证据**不得用于排除任何假设** |

### 4.5 「不吠的狗」硬闸门（规格级，非判定闸条件）

> **规格条文**：`absence of evidence` 升格为 `evidence of absence` 之前，必须出示
> **采集覆盖充分性证明**；未能出示时，该观测只能记为「未评估」，不得记为「未发生」。

**本仓可作为证明引用的既有量化边界**（随结论附报，不得省略）：

| 证据 | 出处 | 作用 |
| --- | --- | --- |
| 信号可达性天花板 **51.8%** | `jev_gate_a2_spec.md` §五 | 界定「若发生必被检出」的现实上界 |
| **48.2%** 涨停票三天内零新闻 → 问题域外 | 同上 §五 | 无 OSINT 信号不等于无事件 |
| 可达率 **69.9%**、**99.7%（1,495/1,500）** 空票码新闻本就不含 A 股简称 | 同上 §十一 | 目的性筛选；「采集越差覆盖率越好看」反向激励的排除 |
| 锚点缺失 → `unpriced`，绝不硬算 | `jev_deltamin_spec.md` §〇 | 缺失不补值 |
| `minute_read` 三档（local / remote / fail）、P2.3 三旗标、合规快照 `fetched_at` | 覆盖率与护栏规格 | 采集姿态可追溯 |
| `source_sig`（含 `absent` 记账）、`ingest_audit` | 原型规格 | 输入逐次留痕 |

**三条派生规则**：

1. 贝叶斯增量更新中，**缺失证据恒为「不更新」**（深度研究稿 §3.3）；弱证据微调、强证据显著调整，
   幅度与诊断性成比例。
2. `unpriced` / `absent` / `fail` 是**采集状态**，不是业务取值；禁止参与任何假设的一致性判定。
3. **本闸门只约束证据解释，不新增、不修改判定闸 A/B 的任何条件、分子、分母或阈值**。
   `jev_gate_a2_spec.md` 仍是条件②唯一执行口径；本规格「不进闸」的不变式（§2.3·3）在此再次适用。

### 4.6 与 P5.2 判例的对接（不重算、不改结论）

- **P1 / P7 主路径与假设相反** → 规格层记为「**未证伪**，四种可能并列」：T+1 太短 / 市场 beta /
  阈值未校准 / 假设方向写反。这是 ACH `sensitivity` 的现成样例，也印证「单日单期不下结论」。
- **`time_ready` 伪分化节点** → H4 的已实现先例，做法照搬，不重复造概念。
- **三旗标与 `feasibility_only=True`** → 原样保留；本规格不改变 P5.2 任何读数与结论。

### 4.7 S1 验收判据

| 编号 | 判据 |
| --- | --- |
| V7 | 现行 `≥2 维度命中` 口径与 `MIN_DIMS` 位置被逐字复述，且明写「不改运行时行为、不重算历史读数」 |
| V8 | 语义改述覆盖六条：证据行降格、`confidence` 非结论置信度、反证最少、零诊断价值、逐行扫描、假设必参与 |
| V9 | H1–H4 四条假设齐全，**H4 采集覆盖伪影在内且优先**；每条带字段、反证与证伪条件 |
| V10 | ACH 输出契约字段齐全；`excluded` 必填（空也要显式）；`is_absence` 与 `absence_guard` 绑定 |
| V11 | 「不吠的狗」条文写成硬闸门，且**明确声明不是判定闸 A/B 的新条件** |
| V12 | 采集充分性证明清单引用 **51.8% / 48.2% / 69.9% / 99.7%** 与 `unpriced`，且可回查到原文行 |

---

## 五、与既有口径的兼容矩阵

| 既有口径 | 出处 | 本规格的关系 | 状态 |
| --- | --- | --- | --- |
| 三旗标 `feasibility_only / gate_eligible / calibrated` | `jev_casebook.py::validate` | 原样保留；S0 的 `confidence` 上限由它约束 | 【不冲突】 |
| `coverage_only=True / gate_eligible=False / calibrated=False` | P2.3 护栏 | 原样保留；`coverage_only` 时禁 `estimative` | 【不冲突】 |
| 分母恒为 `snapshot.codes`；`posts > listed` 合法；两档独立上界 | P2.3 覆盖率口径 | 本规格不触碰任何覆盖率计算 | 【无关】 |
| `unpriced` 绝不硬算；`echo_ratio` 先验 | `jev_deltamin_spec.md` | 升格为「不吠的狗」硬闸门的一等证据 | 【加强】 |
| 采集层与渲染层物理隔离 | 老田 2026-11-08 裁定 | 本规格全是**研判侧口径**，不新增采集职责、不拆隔离 | 【不冲突】 |
| 结论必须多维度交叉才算利用 | 同上 | ACH 把「交叉」从累加细化为**诊断性排除**；不降低要求 | 【精化】 |
| 判定闸 A/B 条件②③、条件②唯一执行口径 | `jev_gate_a2_spec.md` | 一字不改；`_product` / `ach` 永不入闸 | 【不改】 |
| `≥2 维度命中即记` 的 P4 原文 | `jev_pattern_spec.md` §2/§3 | **保留原文不动**；本规格另立解释层，差异登记在 §六 | 【待批订正】 |

---

## 六、明确不做的事与待批单点订正

### 6.1 本规格不做的（逐条写死）

1. **不改 `portal/jev_pattern.py`**：`MIN_DIMS`、`DIM_WEIGHTS`、`match_signal`、`Hit`、`summarize` 一行不动。
2. **不改 `docs/jev_pattern_spec.md`**：它是 P4 交付物，§2/§3 的 `≥2 维度命中` 原文保留。
3. **不改 `docs/jev_gate_a2_spec.md`**：条件②唯一执行口径，分子分母规则四条全部保留。
4. **不改 v2 真相源**、不加判定闸条件、不改阈值、不做校准、不重跑案例库。
5. **不写任何生产代码 / 配置 / 计划任务 / 服务**，不导入租户、不挂 Agent、不请求 8091、不联网、不删文件。
6. **不回填历史输出**：已有 JSON / sqlite 行不含 `_product`，按不造假数据铁律**不伪造回填**。

### 6.2 口径差异登记（实施前需老田单点裁定）

| # | 差异 | 现状 | 建议处置 |
| --- | --- | --- | --- |
| D1 | `docs/jev_pattern_spec.md` §2/§3 写「≥2 维度命中即记」，本规格把它降格为「待排除的证据行」 | 两文并存、语义不同层 | 实施时在 P4 规格加一条**指向本规格的注记**，不删原文；或由老田裁定合并，二者择一 |
| D2 | `Hit.confidence` 与 `_product.fact_vs_judgement.confidence` 同名不同义 | 同名易混 | 实施时把 **`Hit.confidence`** 的显示名改为 `clue_weight`（**仅展示层**），字段本身不改 |
| D3 | PIR 需求登记簿、概率词表的本仓校准尚无实体 | 采集工位 C1 已声明「可先建空表结构，PIR 内容归上级定义」 | 另批；实施前与 C1 对齐单一路径，**禁止两份同名登记表**；校准前 `likelihood` 只允许 `unassessed` |

> D1–D3 **均未执行**，本规格只登记，不构成授权。

### 6.3 与并行工位（采集侧）的边界

本轮同工作区另有采集工位并行交付 `docs/OSINT收集方法学习_2026-11-11.md`（OSINT1 / C1），
其 C1 条讲 PIR↔源↔指示器映射、登记表结构与覆盖状态，并写明「PIR 内容归上级定义（角色边界）」。
两文是**上下游、不是竞争口径**，按互斥协议钉死三条：

1. **职责**：C1 管「怎么拿到 / 登记表结构 / 覆盖状态」；本规格管「拿到了说明什么 / 输出侧引用」。
   与老田 2026-11-08「采集层与渲染层物理隔离」裁定一致。
2. **文件**：本规格**不新建** PIR 登记簿实体、**不修改** C1 那份文档；实施时双方对齐单一路径。
3. **概念不互相实现**：C1 的 Feedback 闭环 ↔ 深度研究稿 S3 后验回流；C1 的 Admiralty 双维 ↔ S2；
   本规格只实现 S0 / S1，**不代做 S2 / S3 / S4**，也不把「不吠的狗」硬闸门外溢成采集侧任务。

---

## 七、文档级验收判据汇总

V1–V6（§3.4）、V7–V12（§4.7），外加两条全局判据：

| 编号 | 判据 |
| --- | --- |
| V13 | §2.3 三条不变式（payload 无损 / 三旗标不失守 / 不进闸）与 §6.1 六条「不做」齐全 |
| V14 | 全文日期、数字、标识符经码点复核无串味；引用的 **51.8% / 48.2% / 69.9% / 99.7% / 17 / MIN_DIMS / AVAILABILITY_PROXY_SIGNALS** 可回查到本仓原文 |

复核脚本：`tmp/verify_jev_s0s1_spec.py`；回执：`tmp/jev_s0s1_spec_verify.md` / `.json`。
**该复核只验文档本身，不验任何代码实现** —— 本规格没有代码可验。

---

## 八、实施路径（全部需另批，本规格不授权）

| 阶段 | 动作 | 前置批准 |
| --- | --- | --- |
| T0（已完成） | 本规格稿 + 文档级复核 | 老田本轮批准 A |
| T1 | 裁定 D1–D3；建 PIR 需求登记簿 | 另批 |
| T2 | 在**原型技能包**（非 8091）加 `_product` 字段，本地自测 payload 仍逐字节无损 | 另批 |
| T3 | ACH 矩阵以**只读分析产物**形式产出，不改 matcher 命中集合 | 另批 |
| T4 | 才谈 8091 输出契约与页面分栏展示 | 另批（且需 3100 位置或其它挂载路径） |

零界终端 3100 位置、8091 默认 `fund=1` 完整档比对、服务器只读核查五要素批准书、
并行工位改写 `stk_mins` 的验收时间窗对齐 —— 四件仍按红线挂着，与本规格无依赖关系。

---

## 九、边界声明

- 本文是**口径规格稿**，不是实施记录、不是验收通过证明，更不构成判定闸口径变更。
- 学理方法可迁移，**场景与授权逻辑不可迁移**：JEV 对象是公开市场板块与证券，
  不涉及个人信息、定位或行动环节；关联不等于因果，结构位置不等于意图。
- 对外材料统一表述为「公开市场信号的 OSINT 化推演」，避免与执法 / 国安语境的「情报」混用。
- 新建本文 1 份 + `tmp/` 复核脚本与回执；**未改 v2 真相源、判定闸 A/B、任何生产代码 / 配置 /
  计划任务 / 服务；未请求 8091、未连生产服务器、未导入租户、未删文件、未联网**。
