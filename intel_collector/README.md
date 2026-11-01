# 采集即服务（Collection-as-a-Platform）· 原型设计说明

> 情报板块「开源情报」这一足的供给底座。把散落的爬虫/采集器整合为一个被管理的内部数据产品：
> 任何数字军团 agent、知识图谱、情报分析、量化策略，都通过统一契约"订阅一个源"，
> 拿到**带血缘、不可变、权利清零（或权利标注）的原始证据流**，而不关心底下怎么抓。

---

## 一、为什么做这个原型（现状病灶）

读完整个项目后，关于"爬虫层"的核心判断：**今天根本不存在一个"爬虫层"，只有四个互不相通的纵向单体，底下没有统一底座。**

| 现有资产 | 实际形态 | 问题 |
|---|---|---|
| `news_collector/` | 采集+JEV分析+告警+飞书投递+看门狗的纵向单体 | 采集与加工/消费耦合；100+ 扁平脚本、`.bak_*` 历史文件 |
| `quant_backtest/xueqiu_*` | 嵌在回测产品里的脚本 | 不是独立服务；`.ps1` 拉起、无编排/健康/封禁检测 |
| `quant_backtest/guba_*` | `guba_crawl.py`（合规采集）+ `guba_sentiment.py`（分析） | 采集逻辑只在这处，没有契约 |
| `res-downloader-py/` | 最接近平台的采集器（MITM+FastAPI+台账） | 游离 app，未被登记为"一个源"，入库打 `[UNOWNED]` |
| `C:/项目/知识库`（YouTube） | 948 ZIP 系统外，未接入 | **血缘/权利范本最好，却没被系统吸收** |

全仓 grep 不到任何 `SourceRegistry / BaseAdapter / register_source` —— **没有任何统一源注册、适配器契约、权利门、血缘回放**。每接一个新源都是重写一遍世界。

---

## 二、目标形态

```
开放网络与授权源 ──► 平台内核（一源一档·统一契约）──► 受管源供给 L0 ──► 订阅消费
  雪球·东财·News·        Source Registry             不可变·带血缘·         数字军团 agents
  YouTube·微博·X·        Adapter SDK                 权利清零的证据流       知识图谱·情报分析·量化
  监管·论坛 …            Orchestrator                                     
                         Provenance + Rights Gate
```

内核四件必须有的东西（本原型均已落地最小版）：

1. **Source Registry（一源一档）** —— 每个源是第一类公民：权属、schema、权利范围、SLA、健康度。接入入口即登记；**禁止 `UNOWNED` owner**。
2. **Adapter SDK（统一契约）** —— `collect() -> Observation[]`；每条 Observation 带 `source_id / snapshot_id / entity_id / collected_at / raw_sha256 / status / completeness / rights_scope`。加新源 = 写适配器 + 登记权利。
3. **Orchestrator** —— 统一调度 + 自适应退避 + 封禁/掉线检测；采集时把允许用途绑定到每条观察。
4. **Provenance + Rights Gate** —— 采集即受权利闸门约束（"公开可访问 ≠ 授权"）；出域即带血缘、权利清零（或标注）的不可变证据。

---

## 三、与现有资产的整合映射（本原型如何"整合"）

| 现有资产 | 在本平台中的角色 | 整合方式 |
|---|---|---|
| `res-downloader-py/` | 采集内核蓝本 | 其 MITM/调度/FastAPI/台账理念吸收进 Orchestrator；**需登记为"一个源"并消 `[UNOWNED]`** |
| `guba_crawl.py` | 合规采集适配器样板 | → `adapters/guba_adapter.py`，保留只读不登录、post_id 去重、原子写、限速 |
| `news_collector` 采集子集 | 新闻适配器 | → `adapters/news_adapter.py`（仅采集职责）；JEV分析/告警/投递剥离到下游 |
| `xueqiu_*` | 雪球公开舆情适配器（**已迁参考版**） | → `adapters/xueqiu_adapter.py`：迁 `_row/_parse_feed_item` 嵌套 data 解析 + `manifest` 状态分账（ip_blocked→unavailable 失败可见）；默认仅 internal_retention/cleaning/controlled_query，不含训练 |
| `C:/项目/知识库` YouTube envelope | 血缘标准 + 系统外接入标杆 | → `adapters/youtube_envelope_adapter.py`，证明契约与 envelope 完全兼容 |

**整合原则（来自老田对 YouTube 语料的裁定）：**
- 采集必须"又笨又诚实"：交付原始 + 血缘 + 失败可见；绝不伪造、绝不解释、绝不静默丢弃。
- 原始观察不覆盖、不累加；评论身份 `(video_id, comment.id)` 由 clean 层处理。
- 时间分开：平台发布时间 / `collected_at` / `available_at` / `first_seen_at`；不得把采集时间倒填为可用时间。
- 最新失败快照不回退伪装为旧成功；`disabled/unavailable` 不等于已删除。
- 文本共现、评论观点不自动升级为已核事实或因果。

---

## 四、核心数据模型（contracts.py）

- `Observation`：一条不可变观察。`(source_id, snapshot_id)` 幂等键；`raw_sha256` 由 payload 规范 JSON 计算。
- `SourceClock`：四时间语义，PIT 查询必须排除 `available_at` 未知记录。
- `UseCase`：8 种用途枚举，**默认拒绝未显式授权**；`training / redistribution / commercial` 属高风险，须 `explicit_approval`。
- `Status`：`complete / empty / disabled / unavailable / incomplete`，失败可见。

---

## 五、权利门（rights_gate.py）—— 你最在意的维度

- 按"源 × 内容类型 × 用途"登记，缺省即拒绝。
- 高风险用途即便 `rights[use_case]=true`，也必须有 `rights_meta[use_case].explicit_approval=true`，否则闸门拒绝。
- 采集时 Orchestrator 把"通过闸门 + 源已授权"的用途快照到每条 Observation 的 `allowed_use_cases`；下游查询只能取被绑定的用途。
- **训练/外发/商用不随入湖自动开放**；YouTube 适配器默认不绑定训练用途。

---

## 六、跨源归并与消费契约（core/canonical.py）

补齐了原型审计反复点名的三个缺口：**schema 归一化、跨源身份归并、消费契约**。

- `normalize_symbol(raw)`：A 股代码归一为 `{BOARD}{CODE6}`（60/68/90→SH，00/30→SZ，8/4→BJ），自动补板前缀，使 `SH600519` 与 `600519` 归并到同一实体。
- `extract_symbols(text)`：从任意文本抽取并去重 A 股代码（新闻标题提及也能抓出实体）。
- `CanonicalEntity(domain, key)`：冻结实体键，**domain 隔离命名空间**——A 股归 `astock`，YouTube 等系统外归 `yt`/`generic`，杜绝跨域误并。
- `resolve_entities(obs)`：按 `source_id` 分派，把一条 Observation 解析为其涉及的 0..N 个规范实体（只读投影，不改原快照）。
- `rollup_by_entity(observations)`：把多条 Observation 映射到 `(domain,key)` 的稳定聚合视图 `EntityRollup`（覆盖源/计数/状态分账/时间窗/样例文本）——这是平台对下游的**消费契约**，下游只认聚合键，不关心原始快照形态。

```bash
python examples/cross_source_rollup.py
```

输出示例：同一标的 `SH600519` 的舆情从 xueqiu/guba/news 三源归并到 `astock:SH600519`（跨源覆盖 + 状态分账），YouTube 独立落在 `yt` 域，不误并。

## 七、运行（原型，合成数据，不触网）

```bash
cd C:/项目/老田的AI助手/intel_collector
python -m pytest -q                       # 全量 40 项
python examples/demo_run.py               # 合成演示（不触网）
python examples/cross_source_rollup.py    # 跨源归并 / 消费契约演示
python examples/run_scheduler_demo.py     # 调度编排四态演示（不触网）
python examples/run_quality_sla_demo.py   # 质量 SLA 四态 + PII 脱敏演示（不触网）
python examples/smoke_xueqiu_live.py      # 真实雪球冒烟（需 XQ_COOKIE + XQ_LIVE_ARMED=1）
```

测试覆盖（40 项）：Observation 哈希、权利默认拒绝、高风险显式批准、Registry 拒绝 UNOWNED、
latest 最新失败不回退 vs as-of 取旧成功、股吧合成页快照、YouTube envelope 血缘保留且不含训练、
雪球嵌套 data 归一化、雪球扁平兼容、雪球 ip_blocked→unavailable 状态映射、存储往返保真 source_field/rights；
**canonical 7 项**：A股代码归一化（多板/无板/非法）、文本抽码去重、雪球/股吧同票归并、新闻文本抽码+端点实体、
YouTube 走 yt 域、rollup 跨三源聚合、消费契约结构化可序列化；
**闸内守卫 4 项**：真实 fetcher 默认禁用/缺 cookie/arming 开关/403×3 熔断；
**调度 5 项**：到期触发/指数退避增长/熔断 open/禁用跳过/成功重置 + ledger；
**质量 SLA 6 项 + PII 6 项**：no_data/ok/stale/breach/empty_ratio/circuit 注入；email/手机/身份证 脱敏与递归、store 统计。

---

## 八、缺失维度与后续路线图（本原型未覆盖，按严重度）

> 已落地：跨源身份归并 + schema 归一化 + 消费契约（见第六节 `core/canonical.py`）；多源调度编排（见第九节 `core/scheduler.py`）；源质量 SLA + PII 合规脱敏（见第十节 `core/quality_sla.py` / `core/pii.py`）；**PIT 时间旅行查询**（见第十一节 `core/pit.py`）；源 owner 登记（5 源已登记，owner=`intel(军情司·INTEL0)`，零 UNOWNED）；**分析→采集反馈环**（`core/feedback.py`：SLA 违约/PII 命中回流调度层）；**统一 cron 实体**（`core/cron_runner.py` 收敛分散调度，虚拟时钟可单测、生产 `run_forever` 部署）。

**A. 工程/平台**：~~统一调度 cron/队列~~ ✅ 已实体化（`core/cron_runner.py` 统一 cron 实体，虚拟时钟可单测、生产 `run_forever` 部署）。
**B. 治理/权利**：源完整生命周期（上线/废弃/被封重采/退役）、血缘作为一等产出可视化。
**C. 产品/覆盖**：微博/X、监管公告 RSS、SEC/备案/处罚 filing、多语言新闻、播客转写等覆盖缺口。（"分析→采集"反馈环 ✅ 已落地，见 `core/feedback.py`）
**D. 组织**：采集与分析/告警/投递分离（news_collector 纵向单体待拆）。

**建议落地顺序：**
1. 田丰归口补 owner / 路径消 `[UNOWNED]`（治理前提）。
2. 以本原型为内核，立项 Source Registry + Adapter SDK 正式化。
3. 把 `news_collector` 采集逻辑生产化、`guba`（参考适配器已就位）打磨；雪球 `xueqiu_adapter` 参考版已迁，待接真实 fetcher 与 rights 审批。
4. 升级为 agent 可订阅的受管源供给 L0（FastAPI 服务 + 消费契约）。

---

## 实时面板（原型态控制台）

把控制台 mockup 做成**真正读取真实数据**的网页（不触网）：
- `seed_demo_store.py`：用合成 fetcher（不触网）把 Observation 写入持久库 `store/observations.db`，含状态分账样本（complete/empty/unavailable）。明确标注 synthetic。
- `dashboard_build.py`：读真实 `registry/sources.json` + `store/observations.db` + 跑 pytest，生成自包含 `dashboard.html`（light 主题 fallback，可离线双击）。
- `dashboard.html`：指标卡 / 源注册表 / **RightsGate 真实 8 用途闸门状态** / 最近采集运行状态分账 / 跨源实体归并（`core/canonical.rollup_by_entity`）/ **调度与熔断**（读 `scheduler_status.json`）/ **质量 SLA + PII 合规**（读真实 store 计算新鲜度四态 + 各源 PII 命中计数）/ **分析→采集反馈环 + 统一 cron 实体**（基于真实 SLA/PII 报告推断闭环动作，并说明 CronRunner 收敛调度）/ **时点回溯 PIT**（合成集演示 `astock:SH600519` 在 10-21 vs 10-22 时点的跨源覆盖差异）。
- `examples/run_scheduler_demo.py`：用内存注册表（不污染真实 sources.json）+ 合成适配器 + 虚拟时钟跑若干 tick，演示到期触发 / 指数退避 / 熔断 open，写 `scheduler_status.json`。
- `examples/run_quality_sla_demo.py`：用虚拟时钟演示 no_data/达标/滞后/违约 四态 + PII 脱敏（email/手机/身份证），证明引擎行为正确。
- `examples/run_pit_demo.py`：用合成跨时点集演示 PIT 时间旅行（10-21→10-22 新增覆盖源 xueqiu，观测 2→4）。

### 质量 SLA 与 PII 合规（第十节）

- `core/quality_sla.py`：`compute_sla(sources, observations, now, circuit_map)` 按 `sources.json` 的 `sla.freshness_minutes` 真实阈值把每个源判为 **无数据 / 达标(≤freshness) / 滞后(≤2×freshness) / 违约(>2×freshness)**，并给出空观测占比与注入的熔断态。now 注入可单测，不依赖真实时间。
- `core/pii.py`：`scan_pii` / `redact_text` / `redact_observation`（递归脱敏 payload 字符串字段、原快照不动）/ `scan_store_pii`（各源命中计数）。入湖即脱敏：email/手机号/身份证打码（保留末位），**仅计数不落明文**。

### PIT 时间旅行查询（第十一节）

- `core/pit.py`：`filter_as_of`（按采集时点过滤）/ `pit_rollup`（时点跨源归并）/ `pit_entity_state`（某实体在 as_of 的覆盖态，不存在则 `exists=False`）。这是对 `obs_store.as_of`（单源单实体）的升级——面向下游的「全局时点快照」。`now/as_of` 用 ISO8601 解析，不靠字符串字典序。

用法：
```bash
cd C:/项目/老田的AI助手/intel_collector
python seed_demo_store.py && python dashboard_build.py
python examples/run_scheduler_demo.py   # 可选：生成调度状态供面板展示
python examples/run_pit_demo.py          # 可选：PIT 时间旅行演示
```
数据均为 synthetic 或登记元数据，未触网、不含真实外部来源内容。重新运行 `dashboard_build.py` 即刷新面板（接真实采集后同理）。

---

## 九、未决项（不冒签、不冒充）

- `lg-97d585b261bb`（YouTube 映射重建与冲突隔离）仍在 `bridge_inbox/inbox`，**待田丰真实签发**；本原型不读取真实 YouTube 包，只用合成 envelope 验证契约兼容。
- 真实数据根（`quant_backtest/data`、`news.db` 现役根）**不在本原型写入范围**；演示产物落在临时目录。
- 统一源注册 / 适配器契约 / 权利门 / 血缘回放 当前仅有本原型最小实现，任何现有代码都替代不了，需正式立项。
