# ADR-016：唯一宿主整合（零界承接零界 + 智策）

- **状态**：**Accepted（2026-11-02 老田批准）**
- **批准事项**：① 唯一宿主 = 零界；② L1 双跑截止日 30 天内（2026-12-02 前关掉 5175/8102）；③ 智策 SaaS-only 面归档；④ 本 ADR 批准，**supersedes 2026-10-30 联邦方案**。
- **日期**：2026-11-02
- **关联**：ADR-015（平台内核契约与组合模型）、ADR-009（端口注册）、ADR-012（L2 五要素）、`docs/audit/2026-10-30_零界智策整合架构方案_v1.md`（superseded）、`..._智策可复用能力盘点与零界融合方案_v1.md`（superseded）、`..._零界智策菜鸟三系统融合方案_v1.md`（superseded）
- **性质**：架构决策；L1/L2 落地需按阶段另开工单，涉及 nginx/认证/停服的步骤走 L2 五要素

## 背景

2026-10-30 的三份方案确立了"联邦架构"：不合并代码、不合并数据库，两个模块化单体 + 边界 BFF + 同源入口 + 可选 OIDC；零界保留个人 OS/AI/军团/运维，智策保留行情/策略/交易/复盘/权益。

该方案造成长期双跑：5175（Vite dev/静态）、8102（旧 API，已停但代码仍默认它）、8105（现役 zhice API）、8091（零界门户 API）同时存在；PG 5446 独立；两套账号（零界 admin key / 智策 auth，含 autoSession 自动注册）；两套调度（Windows 计划任务 + APScheduler）；两套前端（vanilla portal / React antd）。运维与数据口径（ACAD1 池 19 vs 30）都因此分裂。

2026-11-02 老田新拍板：**零界/智策不是并行关系，是整合关系，只能在一套上运行。**

## 决定

### 1. 唯一宿主 = 零界（Lingjie）

- 人类唯一入口：**http://127.0.0.1:8734**（nginx）；
- 平台内核与组合层、军团编制、任务/审批、凭据 `_vault`、治理门、门户页面均在零界；
- 智策不再是独立运行的系统/独立入口/独立认证，而是零界宿主上的**量化域资产**。

### 2. "一套"的 7 个验收定义

| 维度 | 单一标准 | L1 目标 | L2 目标 |
|---|---|---|---|
| 部署 | 一个 nginx + 一个 Python 3.11 运行时家庭 + 一个 PG | 一个入口链路 | 同一部署/进程组 |
| 入口 | 唯一 8734 | 5175/8102 关停 | 仅 8734 对外 |
| 数据库 | 唯一业务库 PG 5446 + 唯一数据根 | 双池对账、PG 为真源 | 零界数据全迁 PG 或降投影 |
| 认证 | 唯一身份（PocketID/OIDC + portal token） | 智策 autoSession/默认口令下线 | 智策 JWT/权益映射或退役 |
| 调度 | 一个 job manifest + 一个 runner | 单调度台账登记 | 全部日管线进 job manifest |
| 配置/凭据 | 一套 `_vault` + 一套端口注册表 | 智策密钥纳入 vault | 单一配置面 |
| 组合 | 一个 composition registry | 影子清单 | 全部组合在册 |

**"一套" ≠ 单进程/单框架**：允许同一 Python 运行时内"stdlib 控制面（8091）+ FastAPI 业务面（8105）"两个进程；但必须同部署、同入口、同认证、同 DB、同调度、同账本。验收判据是**运维上只操作一套**。

### 3. 智策收敛为 6 个资产

| 资产 | 处理 | 落位 |
|---|---|---|
| ① 量化业务 API（FastAPI 8105） | 保留为**内部服务**（仅 127.0.0.1，不直接对外） | 8734 同源反代 `/quant-api/` |
| ② React 前端（静态 build） | 作为**页面包**纳入零界页面 manifest（不重写 React） | `/quant/*` |
| ③ PG 5446 | **唯一业务库**；`v2_*` 为量化域 schema | dataset registry 登记 `pg_table` |
| ④ 日管线脚本（16 步 + APScheduler） | 改为 job manifest（Windows 计划任务仅作触发器） | `jobs/quant-*.yaml` |
| ⑤ 身份/权限（6 角色） | 映射到零界身份；autoSession、默认口令、通配 CORS 下线；写白名单进组合声明 | 单一认证 |
| ⑥ 安全债（默认 secret/admin、无鉴权 BFF） | **先修后用（P0）** | L2 审批 |

**SaaS-only 面归档**：billing / billing_webhook / brokers / trade / trades / my_trading / my_accounts / mirror_accounts / accounts / accounts_ext / admin_billing / admin_entitlements / plans / integrations_telegram / integrations_wxpusher / my_notification_* / subscription_observation 等（候选清单与证据见 `deliverables/2026-11-02_智策SaaS-only面归档清单_v1.md`）。
归档规则：L1 内逐项确认"无在用入口、无活跃数据依赖、无外部凭据"后归档；不确定的一律先保留只读，不删代码；归档走 ADR-015 的组合退役语义（retired，保留 manifest 与决策痕迹）。

### 4. 两档整合

**L1 运行态单栈（截止 2026-12-02，即本 ADR 批准后 30 天）**：
1. 8734 同源反代 `/quant/`（静态 build）与 `/quant-api/`（8105，仅服务端/受控）；
2. 单认证接入（PocketID/OIDC + portal token）；智策 autoSession/默认口令/通配 CORS 下线；
3. **关停 5175/8102**（停监听、改默认配置、停相关计划任务），保留回滚步骤；
4. 单调度台账登记（现有 Windows 任务先登记，不立即合并）；
5. 验收：运维上只剩 8734 可操作；5175/8102 无监听、无系统引用默认值；ACAD1 池单口径可解释。

**L2 代码/数据单栈（预计 2–3 个月）**：
1. 单库 PG 5446；零界 JSON/SQLite 数据迁 PG 或降为投影；
2. 单 UI 壳（页面 manifest）；智策 React build 作为页面包，不重写；
3. 单调度（job manifest + runner）；
4. 智策不再独立部署，降为 `services/quant` + 页面包 + `v2_*` schema；
5. 发布 Platform Kernel v1.0，宣布"框架固定、一套运行"。

### 5. 安全前置（先修后用）

在开同源反代/对外暴露之前必须完成：
1. 强 `SECRET_KEY`/`ADMIN_PASSWORD`，轮换默认 admin；
2. 关闭 autoSession，恢复登录；禁止密码/token 明文 localStorage；
3. `INTERNAL_API_KEY` 启用范围与豁免写清（否则 `selection-public`/`hermes/*` 会整体 401 或裸读）；
4. 5175/8102 仅 127.0.0.1；nginx 变更走 L2 五要素。

### 6. 回滚

- L1 每步保留回滚：nginx location 回退、端口恢复、计划任务启停；
- 数据迁移前备份 PG、保留双写窗口；
- 触发回滚条件：入口 401/404 面扩大、ACAD1 池数不可解释、写操作越权、页面白屏 >10 分钟。

## 后果

- **正面**：运维一套、入口一套、身份一套、数据口径一套；新能力以组合方式挂到唯一宿主；智策资产不再需要独立运维。
- **代价**：需要前端同源/认证改造、数据迁移、归档盘点；短期双跑有截止日与关停成本。
- **风险**：双跑失控（R11）、先暴露安全债（R12）、智策默认值/autoSession 遗留、React 页面包与 portal 壳的样式冲突。
- **不做**：不重写智策 React 为零界页面；不做微前端框架；不合并代码为一次性大爆炸；不把 5175/8102 长期保留为第二入口。

## 验收清单

### L1

- [ ] `Get-NetTCPConnection` 5175/8102 无监听（关停且无默认引用）
- [ ] 8734 `/quant/` 200、`/quant-api/` 健康 200、未登录按门户鉴权
- [ ] 智策 autoSession 已关；默认 admin 口令轮换；`INTERNAL_API_KEY` 行为明确
- [ ] 单调度台账可查（job 清单 + 触发源）
- [ ] ACAD1 池单口径（PG 或 JSON 只有一个是真源，另一投影）
- [ ] 回滚步骤演练过（nginx/端口/任务）

### L2

- [ ] 唯一 PG 业务库，零界数据迁移完成或有投影说明
- [ ] 页面 manifest 覆盖 `/quant/*` 与 ACAD1/D1 页面
- [ ] job manifest 覆盖全部日管线；Windows 任务仅触发器
- [ ] 智策独立部署下线（无独立进程/独立入口/独立认证）
- [ ] 组合账本 100% 覆盖（ADR-015）
- [ ] Platform Kernel v1.0 发布

## 相关

- 研究报告：`deliverables/2026-11-02_松耦合技术框架平台_固化可行性研究_v1.md`（§四 整合关系、§七 路线）
- 平台内核：`docs/adr/ADR-015-platform-kernel-composition-model.md`
- 组合层：`compositions/README.md`
- 旧方案（superseded）：`docs/audit/2026-10-30_零界智策整合架构方案_v1.md` 等三份
