# 系统问题体检与优化方案 v1

- 日期：2026-11-03 06:02｜范围：零界 / HQ / W3 前置 / 8093 调度 / Q2 证据
- 体检结论：**功能门禁全绿，但运维稳定性与 Q2 验收链存在系统性脆弱；建议先优化稳定性/身份/范围，再进 W3。**
- 本次只读检查，未改服务/代码

## 一、体检事实

| 项 | 现状 | 判定 |
|---|---|---|
| L1 | 18/18 PASS | 功能正常 |
| HQ Q2 探针 | 12/12 PASS，p50 63.2ms / p95 417.8ms | 正常 |
| route / composition | 292 零 diff；6 组合 0/0 | 正常 |
| 8734 / 8091 / 8093 / 8096 / 8105 | 均在听；但 1 小时内多次重启 | 稳定性风险 |
| 8091 supervisor | `AI工作站门户API`：`RunLevel=Limited`、`ExecutionTimeLimit=PT10M` | **无法管理提权 8091** |
| 会话 | `portal/auth_gateway.py:79` SessionStore 为**内存**，TTL 1h | 8091 重启即全员掉线 |
| Q2 auto 证据 | 6/6 `AUTO-EVIDENCE-COLLECTED`，但仅 acad1 Q2 PASS | **自动证据≠Q2 PASS** |
| data-warehouse | API 200、页面 302；登录态 `PENDING-LOGIN` | 缺测试身份/会话 |
| 4 个 Q1 军团 | 人工门未完成（11-04/评审/回滚/SLO） | Q2 全量未达 |
| A2A | discovery 已上线；task send/stream 未实现 | 不能当可执行 Agent |
| 审批 | 40/40 approved、无 pending 源 | 形式审批风险 |
| W3 | 12-03 才启动，但前置功能已提前加 | 范围蔓延风险 |

## 二、问题清单（按优先级）

### P0：8091 单点稳定性

**证据**
- 05:47 重启：旧 pid 33508 被 kill，新进程延迟到 05:47:18 才起，期间门户不可用；
- supervisor 任务 `RunLevel=Limited`，不能杀提权进程；
- 启动脚本 `start_portal_api.ps1` 前台运行，依赖 admin 人工；
- 会话内存存储，重启后 `/hq`、`/dw` 全部 302，Q2/验收需重新登录。

**影响**：门户/Q2 验收/自动化被重启打断；管理员不在就无法恢复；会话不可复用。

**优化**
1. 8091 改为**受监督服务**（NSSM/WinSW 或 Highest 任务 + detached wrapper），health watchdog + 自动重启；
2. 去掉 10 分钟 `ExecutionTimeLimit` 对服务父进程的误杀风险；
3. 会话持久化：SQLite/Redis session，或改用 OIDC 无状态签名 cookie；
4. 重启增加 **blue-green/健康门**：新实例 healthy 后再切流量；至少保留回滚旧进程；
5. 重启/失败事件入 `dev_events` + 告警。

### P1：Q2 验收身份不可用

**证据**
- data-warehouse 页面 302，登录态验收 `PENDING-LOGIN`；
- 当前只能人工手工填 cookie，不能作为长期验收链；
- `tools/q2_evidence.py` 6/6 自动证据，但 4 个 Q1 仍无 owner 签字。

**优化**
1. 建立**只读验收身份** `q2-auditor`（OIDC/本地 admin 均可），仅 Q2 只读 scope；
2. Q2 证据包增加“自动证据 / 人工签署”双状态；禁止 `auto_ready` 当成 PASS；
3. 4 军团指定 owner + 截止日（huangzhong 11-04，其余 W3 前）；
4. 用测试身份跑 `/dw`、`/hq`、9 API，结果写入证据包；
5. 验收完成后销毁临时会话/权限。

### P1：Q2 语义风险

**证据**
- `q2_evidence_matrix` 的 `summary.auto_ready=6` 容易被误读为 6 个 Q2 PASS；
- 实际矩阵仍 1 PASS + 1 条件 + 4 Q1。

**优化**
1. 字段改名：`auto_collected` / `q2_passed` 分离；
2. 报告标题强制“自动证据（非验收）”；
3. W3 出口只认 owner 签字的 `q2_passed=6`。

### P1：W3 范围蔓延

**证据**
- W3 12-03 才启动，HQ-01~06、A2A discovery、MCP 对账等已提前落地；
- 同时还有 Q2 全量、A2A task runtime、页面 manifest/契约强制未完成。

**优化**
1. **冻结新增功能**，只做稳定性/验收/DOD；
2. 已提前完成的列为 W3 前置，不再扩 scope；
3. A2A task runtime、Q2 全量、契约运行时强制放 12-03 W3 批次；
4. 每项工作必须挂 W3 workstream，不再临时插入。

### P2：配置/注册表手工漂移

**证据**
- nginx H4 默认拒绝 + 手工白名单；新增 `/api/hq`、`/api/a2a` 需手工登记；
- route baseline 292 条需手工更新；
- MCP registry 与 `~/.hermes/config.yaml` 只做一次性对账。

**优化**
1. 页面/API manifest 生成 nginx 白名单与 route baseline；
2. MCP registry 在 8091/Hermes 启动时做 reconcile gate（失败 fail-visible）；
3. 变更必须同提交测试，禁止“先上线后补白名单”。

### P2：审批与可观测

**证据**
- 40/40 approved、无 rejected、pending 无源；
- Q2/SLO 靠人工跑探针，无定时告警。

**优化**
1. 飞书待办 ↔ 本地审批库只读对账；
2. 每周抽样审计（已有 `tools/approval_audit.py`）；
3. `hq_q2_probe` 定时 5 分钟 + 失败告警，SLO 趋势入看板。

## 三、优化路线（建议）

| 阶段 | 时间 | 动作 | 验收 |
|---|---|---|---|
| **P0 稳定化** | 11-03~11-10 | 8091 受监督服务、会话持久化、重启健康门 | 连续 7 天无人工重启；重启后 session 不丢 |
| **P1 验收身份** | 11-03~11-10 | `q2-auditor` 只读身份；Q2 双状态字段 | data-warehouse 登录页 PASS；4 Q1 owner/日期 |
| **P1 范围冻结** | 立即 | 停止新增功能；只做 Q2/W3 前置 DOD | 新任务必须挂 W3 workstream |
| **P2 自动化** | 11-10~11-30 | manifest→nginx/route、MCP reconcile、Q2 定时探针 | 门禁自动、漂移可发现 |
| **W3** | 12-03~01-15 | Q2 全量、A2A task runtime、页面 manifest/契约强制 | W3 出口 E1–E8 |

## 四、立即执行建议（3 项）

1. **批准 8091 受监督服务改造（L2）**：服务管理器 + 健康 watchdog + 会话持久化；
2. **建立 q2-auditor 只读身份**：不再人工传 cookie；data-warehouse 登录页验收当天完成；
3. **冻结新增功能**：Q2 全量与 A2A task runtime 收进 W3 批次，避免继续“提前加功能、验收跟不上”。

## 五、结论

当前系统**功能可用、门禁绿**，但**运维上单点、验收上缺身份、Q2 语义易假绿、W3 范围在蔓延**。  
**最优先不是继续写功能，而是：稳定 8091 + 建立只读验收身份 + 冻结范围。** 这三项完成后，Q2 全量和 W3 才有可靠底座。
