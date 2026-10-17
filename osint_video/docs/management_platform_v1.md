# 知识资源管理平台 V1 架构补充

> 面向 `osint_video` 已完成的 PostgreSQL 知识链路和链接队列。本文只描述管理平台（不改采集、抽取代码），重点覆盖首屏、管理闭环、查询边界、状态卡片、投递刷新与证据引用。

## 1. 目标与非目标

### 目标

- 让运营人员在一个高密度、深色中文工作台中完成“投递链接 → 查看处理状态 → 审核知识 → 追溯证据 → 绑定策略”。
- PostgreSQL 是唯一事实源；Qdrant/图谱只能用于候选召回，前端展示前必须回 PG 校验状态与证据权限。
- 所有操作可重试、可审计，不因刷新页面或重复点击产生重复链接、知识版本或证据绑定。

### 非目标（V1 不做）

- 不在浏览器展示或下载明文视频、Cookie、Token、原始媒体路径。
- 不允许前端直接查询 PostgreSQL、Qdrant 或图数据库。
- 不在首版引入实时 WebSocket 基础设施；首版用短轮询 + 增量游标实现稳定刷新。
- 不把 Qdrant 相似度当作事实置信度；事实置信度来自知识单元和 Evidence。

## 2. 页面信息架构与首屏

### 2.1 全局壳层

- 左侧固定导航：`总览`、`链接收件箱`、`博主与兴趣`、`知识单元`、`时间线`、`策略证据`、`任务运维`。
- 顶部：当前数据时间（UTC 原始值 + 本地展示）、PG/Qdrant/Worker 健康、全局搜索框、刷新按钮。
- 内容区采用策略/数据工作台同款高密度布局：紧凑表格、少留白、固定列宽、状态色优先于装饰。
- 主题：深色背景、灰蓝面板、细边框；中文优先，数字使用等宽字体。
- 颜色约定必须沿用现有平台：红色表示上涨、正向、支持（`supports`）；绿色表示下跌、负向、反对（`contradicts`）；中性状态使用蓝/灰/黄。禁止使用“红色=错误、绿色=成功”的通用覆盖，错误用橙/紫并配文字。

### 2.2 总览首屏（Dashboard）

首屏是一张“管线驾驶舱”，不展示长文本，点击卡片下钻到列表。

**第一排状态卡（可点击，卡片数字必须带 `as_of`）**

1. **待处理链接**：`pending + parsed + downloaded + decrypted`，辅助显示队列最老年龄、P95 等待时间。
2. **处理中**：`extracted` 与进行中的 processing run，显示 ASR/OCR 通道覆盖率。
3. **待审核知识**：`candidate/unreviewed` 知识单元，显示今日新增和 24h 增长。
4. **证据完整率**：已发布知识单元中具备有效 Evidence 的比例；低于阈值显示橙色告警。
5. **索引延迟**：PG 已写入但 Qdrant/KG 未成功的资源数及最老延迟。
6. **死信/挂起**：`dead + waiting_cookie + quarantine`，按原因拆分，点击进入任务运维。

**第二排工作区**

- 左：`最近链接` 表（URL 短 hash、作者、路由、状态、阶段耗时、更新时间、重试）。
- 中：`待审核知识` 表（标题、类型、博主、置信度、Evidence 数、质量、来源时间）。
- 右：`告警与动作`（Cookie 失效、死信阈值、索引失败、媒体将过期），每条提供安全动作：重试、隔离、更新凭证；不提供媒体路径。

**第三排溯源摘要**

- `热门博主/兴趣信号`：按“频次 × 时间衰减 × Evidence 可靠性”排序，仅展示候选/已确认标记。
- `策略证据变更`：最近版本、支持/反对数量、冲突项、操作者与时间。

## 3. 功能页面与交互闭环

### 3.1 链接收件箱（LinkInbox）

- 多行粘贴（每行一个链接），路由 D/B/AUTO、优先级、操作者；提交前显示规范化链接数和重复数预览。
- 提交返回 `accepted/duplicated/tasks`，不等待采集完成；成功后把新 task ID 写入页面本地状态。
- 表格按阶段过滤，行内展示状态、错误码、重试次数、下一次重试时间、作者/视频 ID（可用时）。
- “重试”只允许 `failed`；“恢复”只允许 `dead`/`waiting_cookie` 且需要操作理由；Cookie 更新入口不回显旧值。

### 3.2 博主与兴趣（Creators）

- 列表支持昵称、finder_id、主题、实体、启停、监控级别、来源类型过滤。
- 详情页三列：画像与监控设置、兴趣信号时间序列、证据知识单元列表。
- 兴趣信号必须显示形成原因（频次、时间衰减、可靠性）及至少一个 `unit_id/evidence_id`；单条视频不得直接显示为高置信度兴趣。

### 3.3 知识单元（KnowledgeUnits）

- 支持关键词/语义检索与 topic/entity/status/unit_type/时间过滤；语义结果必须回 PG 过滤权限、版本和质量。
- 编辑采用“生成新版本”模式，禁止原地覆盖：显示 supersedes 链、操作者、抽取器/模型版本、修改差异。
- 发布前置条件：至少一个 `valid` Evidence，来源 URL 可访问/已留档，时间定位合法，PII 已脱敏。

### 3.4 时间线（Timeline）

- 按 target、topic、entity、事件类型、发生时间和采集时间筛选。
- 事件卡点击打开知识单元抽屉，抽屉内继续打开 EvidenceChain；不能从时间线直接编辑原始来源。

### 3.5 策略证据（Strategies）

- 策略列表展示版本、状态、支持/反对/上下文证据计数、最新变更。
- 证据绑定需选择关系 `supports/contradicts/context`、权重、理由；支持批量解除但保留审计。
- 证据链按策略版本聚合，显示冲突项和质量警告，不允许静默覆盖旧版本。

### 3.6 任务运维（Jobs）

- 三层视图：链接采集、抽取处理、知识/索引；统一展示 state、attempt、error、run、开始/结束时间。
- 人工动作必须带 reason，并记录 operator；批量重试需二次确认和上限。

## 4. 后端 API 契约

所有 API 统一 `{code: 0, data: ..., message: "ok"}`；错误响应使用非零 code，敏感字段永不回显。列表统一返回 `items,next_cursor,as_of`，避免无上限分页。时间均为 ISO 8601 UTC。

### 4.1 首屏和健康

- `GET /api/v1/dashboard/summary`
  - 查询参数：`window=24h|7d`。
  - 返回：`cards[]`（`key,label,value,delta,level,as_of,drilldown`）、`health`、`alerts[]`。
  - 数据边界：仅聚合 PG 统计、processing_run、index_task；不在此调用 Qdrant 搜索。
- `GET /api/v1/healthz`
  - 返回 API/PG/Qdrant/worker 状态；Qdrant 不可用时不影响 PG 列表和投递。
- `GET /api/v1/changes?cursor=&scope=dashboard|links|knowledge|jobs`
  - 返回 `cursor,changed_ids,as_of`，仅用于短轮询增量刷新。

### 4.2 链接与任务

- `POST /api/v1/links`：`{urls[],created_by,route,priority,client_request_id?}`；返回 `accepted,duplicated,tasks[]`。
- `GET /api/v1/links?status=&route=&cursor=&size=`：只读队列列表。
- `GET /api/v1/links/{task_id}`：任务详情与阶段事件（脱敏）。
- `POST /api/v1/links/{task_id}/retry`：仅 failed/dead；幂等键 + reason。
- `POST /api/v1/credentials/yuanbao_cookie`：加密更新并返回 `resumed_waiting` 数量，不返回 Cookie。
- `GET /api/v1/jobs?stage=&state=&cursor=&size=`、`POST /api/v1/jobs/{id}/retry`、`POST /api/v1/jobs/{id}/quarantine`。
- `GET /api/v1/stats`：兼容现有接口；扩展 `queue_lag,index_lag,evidence_completeness,media_expiry`。

### 4.3 博主、知识与时间线

- `GET /api/v1/creators?nickname=&topic=&entity=&enabled=&monitor_level=&cursor=`。
- `GET /api/v1/creators/{creator_id}`、`PATCH /api/v1/creators/{creator_id}`（昵称、备注、监控级别、启停）。
- `GET /api/v1/creators/{creator_id}/interests?from=&to=&cursor=`：信号、分数、形成因素、Evidence 引用。
- `GET /api/v1/knowledge/units?q=&topic=&entity=&target_id=&status=&unit_type=&from=&to=&cursor=`。
- `GET /api/v1/knowledge/units/{unit_id}`：当前版本、supersedes、Evidence、索引状态。
- `POST /api/v1/knowledge/units`、`PATCH /api/v1/knowledge/units/{unit_id}`：人工创建/修订；每次修订生成新版本。
- `GET /api/v1/knowledge/search?q=&filters=&expand_graph=`：Qdrant 候选 → PG 回填；响应标记 `retrieval_source` 和 `index_staleness`。
- `GET /api/v1/timeline?target_id=&topic=&entity=&event_type=&from=&to=&cursor=`。

### 4.4 策略与证据

- `GET /api/v1/strategies?status=&cursor=`、`POST /api/v1/strategies`、`PATCH /api/v1/strategies/{id}`。
- `GET /api/v1/strategies/{id}/evidence-chain?version=`：版本、证据关系、冲突、质量告警、来源链接。
- `POST /api/v1/strategies/{id}/evidence`：`{evidence_id,relation,rationale,weight,expected_version}`。
- `DELETE /api/v1/strategies/{id}/evidence/{evidence_id}`：软解除并写审计。

## 5. 数据查询边界

1. **前端 → API only**：浏览器只接触 DTO；任何 SQL、内部路径、Cookie、Qdrant payload 禁止下发。
2. **PG 为事实源**：队列、资源、知识单元、Evidence、策略、审计和状态卡都从 PG 读取；分页优先 keyset/cursor。
3. **派生索引只做召回**：Qdrant 仅返回 `unit_id,score,index_version`；API 必须批量回 PG，并过滤 `status=published/reviewed`、PII、权限和 tombstone。
4. **图谱仅做扩展**：KG 邻居必须带 `evidence_id/confidence/valid_from/to`；缺 Evidence 的边不得展示为事实。
5. **查询上限**：默认 `size=20`，最大 100；语义召回 top-k ≤ 50；图扩展深度 ≤ 2；时间线单次 ≤ 500 条。
6. **敏感数据**：`raw_path`、`value_enc`、Cookie、Token、完整作者标识不返回；日志只写 hash/脱敏 ID。
7. **一致性标记**：每个列表响应带 `as_of`；知识详情带 `pg_updated_at/index_status/index_error`，允许前端提示“索引稍后可用”。

## 6. 链接投递后的刷新机制

1. 提交 `POST /links` 成功后，将返回 task ID 合并到前端 query cache；立即刷新收件箱和首屏卡片。
2. 对存在未终态任务的页面，以 3 秒间隔调用 `/changes`；仅在 cursor 有变化时按 changed IDs 请求详情，避免整表刷新。
3. 页面不可见时暂停轮询，恢复可见时先调用一次 summary/changes；连续 3 次无变化退避至 10 秒，最多 30 秒。
4. 任务进入 `done` 后停止该 task 轮询，刷新知识计数、证据完整率和索引延迟卡片。
5. `failed/dead/waiting_cookie` 变化立即显示错误码和可行动作；不得自动重试死信。
6. 若 `/changes` 不可用，降级为 10 秒 `GET /links`；若 PG 返回 503，保留本地已提交结果并显示“数据源暂不可用”，不伪造成功。
7. 未来可将 `/changes` 替换 SSE/WebSocket，前端订阅接口不变，保留 cursor 语义避免丢事件。

## 7. 证据引用流（EvidenceChain）

1. 采集/抽取阶段分别保存 ASR、OCR、subtitle、manual 片段及时间范围，生成 `processing_run`。
2. KnowledgeWorker 从 StructuredDoc 生成知识单元，同时写 `osint_evidence`：`source_url/source_ref/source_channel/start_sec/end_sec/quote/quote_hash/reliability/model_version`。
3. API 返回知识单元时只携带 `evidence_refs` 摘要；点击 EvidenceChain 才加载完整 quote、定位、抽取版本和来源链接。
4. EvidenceChain UI 每条证据显示：关系、来源频道、时间码（可复制）、引用文本、可靠性、质量、有效/过期状态；时间码越界或 quote 缺失显示告警。
5. 策略绑定只接受 `valid` Evidence，保存 `supports/contradicts/context`、rationale、weight、操作者和策略版本；绑定快照 hash 防止来源被静默改变。
6. 纠错不覆盖旧证据：人工修订产生新知识单元版本和新 Evidence，旧链标记 `superseded`，策略链显示新旧关系。
7. 媒体清理只删除 MediaAsset；Evidence 的文本、hash、来源和定位长期保留。若来源不可访问，显示“来源失效”而不删除已审计证据。

## 8. 文件清单（首版平台）

### 后端新增/修改

- `knowledge/api.py`：知识、博主、时间线、策略、证据、dashboard DTO 与权限边界。
- `queue_link/api.py`：保留链接投递、队列、stats、healthz；增加 task detail/retry 和 changes。
- `knowledge/repository.py`：PG 只读查询、cursor 分页、dashboard 聚合、证据链查询。
- `knowledge/service.py`：发布前 Evidence 校验、版本化编辑、策略证据绑定。
- `knowledge/indexer.py`：Qdrant 召回封装，不向前端暴露原始 payload。
- `store/schema.sql`：索引/视图（如 `dashboard_queue_summary`、`knowledge_evidence_completeness`）和审计字段。
- `common/logging_setup.py`：管理操作审计（operator/action/object/reason）。

### 前端新增

- `admin_ui/src/App.tsx`、`admin_ui/src/main.tsx`：路由、QueryClient、主题与壳层。
- `admin_ui/src/api/client.ts`：统一 `{code,data,message}` 解包、错误、cursor、轮询 changes。
- `admin_ui/src/types.ts`：LinkTask、DashboardCard、KnowledgeUnit、Evidence、Strategy、Job DTO。
- `admin_ui/src/pages/Dashboard.tsx`、`LinkInbox.tsx`、`Creators.tsx`、`KnowledgeUnits.tsx`、`Timeline.tsx`、`Strategies.tsx`、`Jobs.tsx`。
- `admin_ui/src/components/StatusCard.tsx`、`StatusBadge.tsx`、`EvidenceChain.tsx`、`KnowledgeEditor.tsx`、`CreatorInterestPanel.tsx`、`DataTable.tsx`、`RefreshController.tsx`。
- `admin_ui/src/styles/theme.ts`、`admin_ui/src/styles/global.css`：深色高密度主题、红涨绿跌 token、中文字体与响应式断点。

### 测试与文档

- `tests/test_dashboard_api.py`、`tests/test_query_boundaries.py`、`tests/test_evidence_chain_api.py`、`tests/test_link_refresh.py`。
- `admin_ui/src/**/*.test.tsx`：首屏卡片、投递刷新、知识审核、证据链、错误降级。
- `docs/management_platform_v1.md`：本设计；与 `docs/system_design.md` 的领域模型/API 保持一致。

## 9. 实现顺序（不改采集代码）

### T01 基础设施与 API 契约（P0）

- 文件：`admin_ui/package.json`、`admin_ui/vite.config.ts`、`admin_ui/tsconfig.json`、`admin_ui/src/main.tsx`、`admin_ui/src/App.tsx`、`admin_ui/src/api/client.ts`、`admin_ui/src/types.ts`。
- 依赖：无。
- 产出：深色主题骨架、统一响应解包、路由占位、cursor/changes 类型。

### T02 PG 查询层与状态聚合（P0）

- 文件：`knowledge/repository.py`、`knowledge/api.py`、`queue_link/api.py`、`store/schema.sql`、`common/logging_setup.py`、`tests/test_dashboard_api.py`、`tests/test_query_boundaries.py`。
- 依赖：T01；依赖现有 PG 表，不引入前端直连。
- 产出：dashboard/links/creators/units/timeline/jobs/changes API，统一边界与审计。

### T03 首屏、链接收件箱与刷新控制（P0）

- 文件：`admin_ui/src/pages/Dashboard.tsx`、`admin_ui/src/pages/LinkInbox.tsx`、`admin_ui/src/components/StatusCard.tsx`、`admin_ui/src/components/StatusBadge.tsx`、`admin_ui/src/components/RefreshController.tsx`、`admin_ui/src/styles/theme.ts`、`admin_ui/src/styles/global.css`、`admin_ui/src/**/*.test.tsx`。
- 依赖：T01、T02。
- 产出：状态卡片、投递、3 秒增量轮询、失败降级、深色高密度风格和红涨绿跌约定。

### T04 知识审核、博主/时间线与证据流（P1）

- 文件：`knowledge/service.py`、`knowledge/indexer.py`、`admin_ui/src/pages/Creators.tsx`、`KnowledgeUnits.tsx`、`Timeline.tsx`、`admin_ui/src/components/KnowledgeEditor.tsx`、`CreatorInterestPanel.tsx`、`EvidenceChain.tsx`、`tests/test_evidence_chain_api.py`。
- 依赖：T02、T03；Qdrant 不可用时必须保持 PG 降级。
- 产出：版本化编辑、发布前 Evidence 校验、语义召回回 PG、兴趣信号和时间线下钻。

### T05 策略证据、任务运维与验收（P1）

- 文件：`admin_ui/src/pages/Strategies.tsx`、`Jobs.tsx`、`knowledge/api.py`、`queue_link/api.py`、`tests/test_link_refresh.py`、`admin_ui/src/**/*.test.tsx`、`docs/system_design.md`、`docs/class-diagram.mermaid`、`docs/sequence-diagram.mermaid`。
- 依赖：T02、T03、T04。
- 产出：策略证据链、任务人工处置、权限/审计、PG/Qdrant 故障演练和 Go/No-Go 报告。

## 10. 链接驱动博主运营标准（正式口径）

平台的主运行标准不是“单条视频下载”，而是以用户投递链接为兴趣信号，围绕博主建立持续更新的知识档案：

```text
收到链接
  → 解析公开元数据与博主
  → 建立/更新博主档案
  → 创建历史回溯任务
  → 抓取并结构化历史内容
  → 生成博主阶段性总结
  → 每日扫描增量内容
  → 更新档案、主题、观点、时间线与策略证据
```

### 10.1 阶段一：收到链接并确认兴趣

- 输入：用户投递一个或多个公开 sph 链接。
- 系统动作：规范化链接、计算幂等键、解析作者、记录来源链接和投递人。
- 输出：`osint_link_task`、`osint_target`；新博主默认进入观察级监控，不因单条视频直接判定高置信度兴趣。
- 门禁：解析失败进入 `failed/dead`，不得伪造博主档案；重复链接只返回 `duplicated`，不重复创建任务。

### 10.2 阶段二：形成博主档案

博主档案至少维护：

- 昵称、平台标识、主页/来源链接；
- 首次发现时间、最近活跃时间、发现来源；
- 监控级别、启停状态、扫描频率；
- 主题分布、实体分布、观点标签；
- 内容数量、有效知识单元数量、Evidence 完整率；
- 观点稳定性、变化点、争议点；
- 最近一次历史回溯和每日扫描状态。

档案中的每个主题或观点必须能够回溯到至少一个 `unit_id/evidence_id`，不得仅凭昵称、标题或单条视频生成结论。

### 10.3 阶段三：历史数据回溯

新博主入监控后自动创建历史整理任务：

1. 获取博主公开历史内容索引；
2. 按发布时间倒序或分页增量处理；
3. 对每条内容执行元数据、ASR、OCR、字幕和观点结构化；
4. 写入 `KnowledgeResource → KnowledgeUnit → Evidence → IndexTask`；
5. 记录游标、已处理链接、失败原因和下次重试时间；
6. 历史媒体只作为临时抽取输入，抽取完成后按保留策略清理，不作为长期知识资产。

历史回溯必须可暂停、可恢复、可重试，不能因单条内容失败阻断整个博主任务。

### 10.4 阶段四：生成博主总结

历史回溯达到阶段性边界后生成博主总结，至少包括：

- 主要主题及时间分布；
- 高频实体、行业和策略关键词；
- 核心观点与观点首次/最近出现时间；
- 观点延续、修正、反转和冲突；
- 观点的 Evidence 数量、来源通道和可靠性；
- 与现有策略的 supports/contradicts/context 关系；
- 总结生成时间、模型版本、输入范围和快照哈希。

总结是可版本化的派生知识，不覆盖历史总结；每次重新生成都记录输入水位和版本，便于比较博主观点变化。

### 10.5 阶段五：每日扫描与增量更新

每日扫描只处理上次成功水位之后的新内容或发生变化的公开内容：

- 新内容进入链接队列并执行同一结构化管线；
- 更新博主最近活跃时间、主题热度和观点时间线；
- 对新 Evidence 触发策略证据链重算或变更提示；
- 发现观点冲突、来源失效、索引延迟或质量下降时生成告警；
- 扫描成功后推进 `last_success_cursor/last_success_at`；
- 扫描失败保留旧档案和旧总结，不推进成功水位，进入重试或人工处置。

### 10.6 运营状态机

```text
link_received
  → creator_resolved
  → profile_created
  → history_backfill_running
  → profile_summarized
  → daily_monitoring
  → incremental_update
```

异常分支：

```text
resolve_failed / backfill_failed / summarize_failed / scan_failed
  → retryable → retry
  → terminal → dead_letter + manual_review
```

`profile_summarized` 不是终态；只有进入 `daily_monitoring` 后，博主才被纳入持续扫描。任何阶段都必须保留运行记录、输入水位、输出版本和审计信息。

## 11. 共享知识与未决假设

- API 响应固定 `{code,data,message}`；时间固定 ISO 8601 UTC；分页固定 cursor。
- PG 是事实源；Qdrant/KG 可重建、可暂时不可用；所有派生结果回 PG 校验。
- `LinkTask.done` 只表示知识资源、Evidence 已入 PG 且索引任务已登记，不表示 Qdrant 已完成。
- `published` 知识必须有至少一个有效 Evidence；无来源内容永不发布。
- 状态卡的红绿语义继承现有策略平台，具体颜色 token 需与现有 portal 主题变量对齐，避免新建冲突色板。
- 假设 V1 为单租户管理端，操作者由现有登录/反向代理提供；若后续需要多租户，需在所有 API 增加 tenant_id 和 Evidence 权限过滤。
- 未决：是否允许策略页面编辑 Evidence quote；默认只允许生成新版本，不允许原地改 quote。
- 未决：Qdrant 是否已在部署环境可用；默认设计为可选依赖并提供 PG 关键词检索降级。
