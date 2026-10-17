# osint_video 项目代码审计、BUG 修复与复盘

日期：2026-10-16

## 一、审计结论

项目已从视频号链接 PoC 演进为“链接驱动博主知识资源中台”雏形。核心数据链路、管理平台 V1、EvidenceChain、敏感字段边界和测试基线均已建立。

当前质量结论：**核心回归通过，可进入下一阶段真实采集与 worker 化；生产上线前仍需补齐真实媒体获取、历史回溯 worker、每日扫描 lease/recovery 和 creator 查询 API。**

## 二、当前架构

```text
公开 sph 链接
  → 链接队列 / 幂等
  → 公开元数据与博主发现
  → osint_target 博主档案
  → 历史回溯 / 每日扫描运行记录
  → 临时媒体处理
  → ASR / OCR / 字幕
  → KnowledgeResource
  → KnowledgeUnit(version)
  → Evidence
  → IndexTask
  → Qdrant / 知识图谱派生索引
  → 策略证据链
```

事实源：PostgreSQL。Qdrant 和知识图谱是可重建派生索引，不得阻断 PG 主链路。

## 三、已完成能力

### 1. 链接与博主发现

- sph 链接规范化与幂等入队；
- 公开元数据解析；
- 从价值链接识别博主；
- 新博主默认观察级；
- `source_type/source_link` 保留来源溯源；
- LinkTask 完成门禁：Resource、KnowledgeUnit、Evidence、IndexTask 缺一不可。

### 2. 知识资产链路

- Resource → KnowledgeUnit → Evidence → IndexTask；
- KnowledgeUnit 版本不可变；
- Evidence 支持来源通道、时间码、quote、snapshot_hash、模型版本；
- EvidenceChain 支持版本匹配、时间合法性和质量告警；
- 媒体清理不级联删除知识与证据。

### 3. 管理平台

- 深色高密度中文工作台；
- 总览、链接收件箱、博主与兴趣、知识单元、时间线、策略证据、任务运维导航；
- dashboard、health、changes、knowledge、EvidenceChain 等 API；
- 前端 typecheck/build 已通过；
- Qdrant 不可用时 PG 查询仍可用。

### 4. 博主运营运行层

- `osint_target.operation_state`；
- `osint_creator_run`；
- `osint_creator_summary`；
- 历史回溯、总结、每日扫描 run 类型；
- active run 幂等；
- summary snapshot 幂等；
- cursor 回退校验；
- `FOR UPDATE SKIP LOCKED` 到期目标认领。

## 四、本轮发现与修复的 BUG

### P1：retryable 状态可被不完整恢复

**问题**：原实现只在传入 `previous_state` 时校验；未传时可能接受任意恢复目标，违反状态门禁。

**修复**：

- `retryable` 必须显式提供 `previous_state`；
- 目标必须属于可恢复状态集合；
- 目标必须严格等于 `previous_state`；
- 否则返回 `CREATOR_STATE_TRANSITION_INVALID`，API 返回 409，不写数据库。

修改文件：

```text
osint_video/knowledge/creator_ops.py
osint_video/knowledge/api.py
```

### P1：新建博主运营状态默认值与正式状态机不一致

**问题**：schema 原默认值为 `creator_resolved`，但正式流程从 `link_received` 开始；首次建档时可能出现状态无法推进或语义跳步。

**修复**：

```sql
DEFAULT 'link_received'
```

并通过 `ALTER COLUMN ... SET DEFAULT` 兼容既有表。

修改文件：

```text
osint_video/store/schema.sql
```

### P1：公开 API DTO 曾存在前后端漂移

已完成对齐：

- LinkTask 使用 `task_id/state/attempt`；
- 公开 DTO 不返回 `raw_path/parse_result`；
- Health 使用 `healthy/degraded/unavailable/unknown`；
- Knowledge 详情使用 `unit_key + version`；
- EvidenceChain 路由单一挂载；
- list units 补充版本、Evidence 数量、索引状态和更新时间。

### P2：测试运行环境污染

从项目外层运行 pytest 会误收集其他项目测试；已确认必须在：

```text
C:/项目/老田的AI助手/osint_video
```

目录内运行。

## 五、验证结果

### Python

```text
py_compile：通过
pytest -q -W error：128 passed, 15 skipped
```

### 前端

```text
npm run typecheck：通过
npm run build：通过
```

### 已知测试环境提示

pytest 退出清理阶段曾被 WorkBuddy 的安全删除保护拦截临时目录清理，但测试主体全部通过；未影响代码测试结果。

## 六、当前风险与缺口

### P0/P1 上线前必须完成

1. **真实媒体获取**：公开元数据已可解析，但媒体二次链路仍可能返回“内容暂时无法播放”或 permission verification failed，需要合法登录/客户端环境验证。
2. **历史回溯 worker**：当前已有运行记录和状态接口，尚未完整连接博主历史内容分页抓取与结构化入库。
3. **每日扫描 lease/recovery**：当前 claim 使用 next_scan_at 预留窗口；worker 崩溃恢复语义仍应升级为 claim_token/lease_until，避免任务在窗口内丢失。
4. **Creator 查询 API**：详情、runs 列表、summary 列表、timeline 仍需补齐并接入真实 UI。
5. **summary 证据质量**：summary 的 evidence_count 必须由已持久化有效 Evidence 聚合，不应信任调用方计数。
6. **持久化 changes log**：生产环境应使用持久化 change_seq/outbox，不能用临时时间或内存游标。

### P2 改进

- 前端自动化测试；
- 真实 worker 运行指标；
- Qdrant 重试与索引重放；
- 知识图谱实体关系映射；
- 依赖漏洞治理；
- 归档和数据保留策略；
- 多租户与权限模型。

## 七、复盘：做对的事情

1. 先把媒体定位为临时输入，而不是长期资产；
2. 用 PostgreSQL 作为事实源，避免向量库成为事实源；
3. 通过 EvidenceChain 建立策略结论到原始来源的追溯链；
4. 对版本、时间码、quote、snapshot_hash 做硬约束；
5. 将 Qdrant/KG 设计为可降级派生索引；
6. 真实 PG 联调发现并修复了 SQLAlchemy 事务、复合外键和索引状态持久化问题；
7. 在前端正式接入前完成 DTO 契约审查，修复多个字段漂移。

## 八、复盘：需要改进的地方

1. 代码实现早于完整 canonical DTO，导致前后端字段反复对齐；
2. 博主运营状态机先有 schema 后补正式转移门禁，初始默认值曾不一致；
3. 运行层测试不足，creator_ops 缺少专门状态/幂等/并发测试文件；
4. 部分代理协作频繁空输出，关键验收应优先由主会话本地命令兜底；
5. 真实媒体链路依赖微信登录态，应该更早建立合法客户端环境验收样本；
6. 预览静态服务器根目录错误导致前端资源 404，部署/预览脚本应固定从 dist 根目录启动。

## 九、建议下一阶段顺序

```text
1. 修复 claim lease/recovery
2. 补 creator 详情、runs、summaries、timeline API
3. 编写 creator_ops 专项测试
4. 接入历史回溯 worker
5. 接入每日扫描 worker
6. 在合法登录环境验证真实媒体抽取
7. 完成 Qdrant/KG 异步索引重放
8. 再进行生产部署验收
```
