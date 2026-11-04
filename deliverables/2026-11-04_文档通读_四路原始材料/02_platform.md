# 02 平台层（工程/平台/架构/契约/门禁）通读报告

> 阅读者：doc-platform（四人组之一）｜只读：未改仓内文件、未做 git 写、未启停服务
> 仓库快照（实测 `git rev-parse`，2026-11-04 07:54:08）：HEAD=`08fac205f`、branch=master、dirty=253；tracked md=2675（docs 1216 / deliverables 713 / adr 23 / plans 390 / compositions manifest 7 / *test*.py 2565）
> 图例：**〔文档称〕**=只来自文档；**〔实测〕**=本次命令输出；行号对应当前 master 文件。
> 覆盖：精读 36 份（根 3 + architecture 3 + ADR 11 + specs 3 + standards 4 + plans 6 + compositions/kernel/MCP 6）+ 工具/配置实测 12 项；大文件 STATUS.md(4218 行) 用 grep/tail 定位；docs/ 与 deliverables/ 未全覆盖（见 H 方法）。

## A. 现行 / 历史 / 退役分级

| 级别 | 对象 | 权威文件（证据） | 判据 |
|---|---|---|---|
| **现行·框架总纲** | ZBF v1（一宿主+K0–K8+C1–C8+黄金路径+Q1–Q4） | `docs/architecture/PLATFORM_FRAMEWORK_v1.md:11-19`；ADR-015/016/017 | **状态为 Proposed 却已被当唯一框架执行**（`PLATFORM_FRAMEWORK_v1.md:3`，`docs/adr/ADR-017-framework-golden-path.md:3`） |
| **现行·宿主** | 唯一宿主=零界，唯一入口 8734 | `docs/adr/ADR-016-single-host-integration.md:1-8,19-37` Accepted；`AGENTS.md:18` | 实测 8734 在听（见 B） |
| **现行·内核** | K0–K8 冻结清单 v0.9 **draft** | `platform/kernel_v1.yaml:1-19,21-256` | status=draft（:6），未生成哈希、不拦截（:259） |
| **现行·组合层** | `compositions/` 7 清单 + registry（draft-shadow） | `compositions/registry.json:4-131`；`compositions/README.md:20-31,66-76` | 实测 7 manifest/7 注册、全 lifecycle=draft、validated=false |
| **现行·治理** | ADGS v1.1 试行（11-03~11-16） | `docs/standards/2026-11-02_敏捷开发与治理标准_v1.md:3,94-127` | Accepted 试行 |
| **现行·战略/排期** | ROADMAP v4 唯一战略源 | `docs/plans/2026-11-02_ROADMAP_v4_唯一战略与主排期.md:5-9,35-43` | v3 头部已标 superseded（`docs/plans/2026-10-26_PRODUCT_ROADMAP_v3.md:2-3`） |
| **现行·执行层** | 整体研发方案 v2（11-03，L2 执行）、11-04 研发计划 v2（日报级） | `docs/plans/2026-11-03_整体研发方案_v2_最优执行版.md:373-384`；`docs/plans/2026-11-04_研发计划_v2.md:3-4` | 自述"服从 ROADMAP v4" |
| **现行·契约** | IFS v1 + Envelope/Error v1 + ID_CHAIN v1 + OUTCOME/TRAIN + OWNER_CONSUMER_SLO + INTERFACE_REGISTRY v1.2.0 | `docs/standards/INTERFACE_REGISTRY_v1.yaml:1-20`（1666 行，draft） | 11-03 定稿 |
| **现行·运行契约** | Hermes 控制面 / MCP registry / 6 军团 Q2 探针 | `platform/interfaces/hermes-control.yaml:1-36`；`platform/mcp/registry.yaml:1-103`（13 server）；`platform/q2/legions.yaml:1-44` | status active |
| **历史·被取代** | 10-30 联邦架构三方案（不合并代码/DB） | ADR-016 supersedes（`docs/adr/ADR-016…:6`） | 已被 11-02 唯一宿主推翻 |
| **历史·过期控制面** | `docs/governance/` 2026-10-17 控制面（REBASELINE-001 BLOCKED） | `docs/governance/README.md:5-7,39` | 仍写"当前门户实际入口为 8080"，与现行冲突 |
| **历史·索引** | `docs/README.md` 战略源/ADR 区间 | `docs/README.md:20,36` | 指向 v3 与 ADR-001~020（均已过期） |
| **退役** | 5175（智策前端，retired 2026-11-02 L1）、8102（智策旧 API，代码仍默认它）、8795（军团网关，默认不启）、8798（议会厅桥，收编入 8796）、8788（Hermes v2 原型，已 down）、8799 过渡网关；8797 临时网关退役后复用 M4 签署页；KGDataRoot 空壳、`C:\项目\work` 根 | `tools/check_ports.py:48,50,102-105,117,131-138`；`docs/audit/2026-10-30_Hermes二开合理性评估_v1.md:207`；`docs/adr/ADR-011-data-roots.md:15`；`AGENTS.md:101` | **实测全部 no-listen**（见 B） |

## B. 架构一页图 + 端口/服务表（实测）

```text
L3 驾驶舱   /hq /preview/hq /intel /briefing /case …（8734 同源，55 处 nginx auth_request）
L2 组合层   7 manifests + registry(draft-shadow)  C1军团/C2产品/C3策略/C4页面/C5数据/C6工作流/C7通道/C8平台
L1 内核     K0组合 K1组织席位 K2任务 K3运行观测 K4接口信封 K5数据证据 K6呈现 K7扩展 K8治理（draft 0.9.0）
L0 宿主     8734（svchost portproxy）→ 8080（Docker nginx：/quant/→静态包 /quant-api/→8105 /hermes/→8787
            /api/...→8091 /api/hq/meeting/→8796 /lde/→8103）；PG 5446(Docker) + 数据根 quant_backtest/data
            旁路：agent_platform 引擎/8796 legion_command、Hermes+MCP13、A2A 6 卡、_vault 凭据、77 席/17 部门注册表
```

| 端口 | 角色 | 真源 | 实测（2026-11-04 07:51/07:54） | 分级 |
|---:|---|---|---|---|
| 8734 | 人类唯一入口（portproxy→8080） | `AGENTS.md:18`；`check_ports.py:45` | LISTEN pid=7044 `svchost`；8080 侧 pid=14656 `com.docker.backend` | 现行 |
| 8091 | 零界门户 API（stdlib 控制面） | `ARCHITECTURE.md:16`；`check_ports.py:42` | LISTEN pid=42848 python（`STATUS.md:4202`：start 06:04:40 > 代码 mtime） | 现行 |
| 8105 | 智策 v2 API（FastAPI，内部） | `ARCHITECTURE.md:66`；`check_ports.py:63` | LISTEN pid=34396 python start 07:22:35 | 现行 |
| 8787 | Hermes WebUI（交互/控制面） | `ARCHITECTURE.md:19`；`check_ports.py:52` | LISTEN pid=10796 python | 现行 |
| 8796 | 军团指挥台/legion_command（MCP 客户端） | `ARCHITECTURE.md:17`；`check_ports.py:100-101` | LISTEN pid=32308 python start 05:05:30 | 现行 |
| 8103 | 军团独立 IDE | `check_ports.py:123-127` | LISTEN pid=17284 python | 现行(按需) |
| 8791 | 图谱数据看板 | `check_ports.py:106` | LISTEN pid=32696 python start 05:20:56 | 现行 |
| 8096/8097/8098/8099/8100 | 军团大屏/推送总览/模型台/KM 联动/datamart 门面 | `check_ports.py:68-72,99` | 5 口全 LISTEN | 现行 |
| 8093 | 组织架构注册 | `check_ports.py:68` | LISTEN pid=3128 | 现行 |
| 5446 | 唯一业务库 PG（Docker） | ADR-016:31；`kernel_v1.yaml:12` | LISTEN `wslrelay`（Docker 端口转发） | 现行 |
| 8080 | 容器 nginx 内部链路（非入口） | `check_ports.py:43`；`AGENTS.md:18` | LISTEN com.docker.backend | 现行(内部) |
| 8795 | 军团网关 | `check_ports.py:102-105` | **no-listen**（默认不启动，可选） | 可选/未启用 |
| 8788 | Hermes v2 | `check_ports.py:94` | **no-listen**（原型已 down，`docs/audit/…Hermes二开…:207`） | 退役 |
| 5175 | 智策前端 | `check_ports.py:48`（retired 2026-11-02 L1） | **no-listen** | 退役 |
| 8102 | 智策旧 API | `check_ports.py:50`；ADR-016:55-60 | **no-listen**（代码默认值仍待清） | 退役(待引用清零) |
| 8798 | 议会厅桥 | `check_ports.py:114-117`（收编 8796） | **no-listen** | 退役 |
| 8101/8770 | 情报评审实例/OpenHands 引擎 | `check_ports.py:118-130` | **no-listen** | 可选 |
| 8771 | OpenHands Docker Sandbox | `check_ports.py:134-136` | **LISTEN**（可选服务在跑） | 可选 |

> 实测方法：`Get-NetTCPConnection -State Listen` + `Get-Process`（07:51:36 全量快照；07:54:47 定向复测）；python 进程 StartTime 部分因权限为空，已用 STATUS 记录补证。

## C. 内核 / 组合 / 黄金路径 / 质量分级现状

| 层 | 冻结物 | 文档状态 | 实测/差额 |
|---|---|---|---|
| K0 组合 | composition schema、registry、composition.py、kernel_guard.py、组合门规格 | draft（`kernel_v1.yaml:22-50`） | `validate --all` 7/7 PASS errors=0 warnings=2；`plan --strict` findings=0；`validate --strict` **rc=1 errors=2** |
| K1 组织席位 | org.json(17 部门/77 席)、team_*、hr、skill_registry、model_tiers、tool_matrix | partial（:53-88） | seat.schema/model_tier.schema=planned；tool_matrix 29 席声明无执行点 |
| K2 任务 | TaskStore 7 态+RBAC、task schema | partial（:90-105） | task.schema planned；DEPTS 硬编码 4 |
| K3 运行观测 | ADR-014 Run/Event/Conversation、trace.py | partial（:107-125） | run.schema planned；P2 仅部分落地（ADR-014:3） |
| K4 接口信封 | reuse 6 schema、objects.py、S21/I5 | partial（:127-146） | envelope/error schema 已在 `docs/standards/schemas/`（16 个），但 kernel 清单仍标 planned→**清单滞后** |
| K5 数据证据 | dataset_registry、ODCS 契约、dataset.schema v0.1 | partial（:148-170） | pg wave2 83 表/registry v2 待实现；evidence schema planned |
| K6 呈现 | tokens/nav/page_shell/shortscreen、路由基线 | partial（:172-199） | page.schema planned；页面 manifest 0（W3 包 §2.1） |
| K7 扩展 | extension manifest schema + lint | partial（:201-215） | 扩类型/转真源未做（`docs/specs/2026-10-27_扩展契约_v1.md:71-76`） |
| K8 治理 | 9 门/merge_train/merge_preflight/devtask/test_gate… | existing（:217-256） | kernel_guard 保护 **40/51 artifacts**（`kernel_guard.py --list` 实测 40 行；W4 框架:86-89） |
| **总量** | 51 artifacts：39 existing/10 planned/1 accepted/1 proposed | `docs/plans/2026-11-02_W4组合化收口框架_v1.md:86` | kernel 0.9.0 draft，计划 P2/P3 清偿 |
| **组合** | C1–C8；7 清单：acad1-legion/quant-domain/lusu-d1-flow/quant-strategy-line/data-warehouse/huangzhong-acceptance/dev-legion | `compositions/README.md:20-31,66-76` | 全 draft/shadow；Q2=0 |
| **黄金路径** | 六步：分类→开单→写清单→validate→接线→merge/观测；模板矩阵 7 tier | `PLATFORM_FRAMEWORK_v1.md:152-176` | 模板仅 4 个（`compositions/_templates/` 实测 data/legion/product/workflow）；模板矩阵未补齐 |
| **质量分级** | Q1 已登记/Q2 可运行/Q3 可治理/Q4 可进化；active≥Q2、对外≥Q3、核心≥Q4 | `PLATFORM_FRAMEWORK_v1.md:120-129` | **Q1=7 / Q2=0 / Q3=0 / Q4=0**（registry 7 条全 validated=false；W4:42 旧口径 Q1=6/Q2=0） |

> W1 引用解析现状〔实测〕：`tools/resolver.py --all --strict --json` = 7 manifests / **153 refs** / errors=0 / **warnings=2** / ok=false rc=1（11-04 03:45 文档称 6 组合/131 refs/0/0，`docs/plans/2026-11-04_研发计划_v2.md:102-106`）。

## D. 门禁清单现状（已实现 / 纸面 / 缺口）

| 门 | 文档声称 | 实现证据〔实测〕 | 判定 |
|---|---|---|---|
| devtask 固定门 | "九道门" | `tools/devtask.py:433-444` GATES=**8 条**：Mixin/导航/端口/UI-UX 审计/明文凭据/语法/导入边界/元数据 | 已实现（口径差 8 vs 九） |
| 质量闸/测试门 | fast 七套件+budget+route | `tools/test_gate.ps1:77` 7 目录套件；budget strict `:445-479`；route 阻断 `:509-539`；wtcheck `:541-575`；org 一致性 warn-only `:20-24`；artifact guard `:309` | 已实现 |
| 组合门 | composition validate+plan | `tools/devtask.py:2436-2448`；`tools/merge_preflight.py:458-470`；规格 `docs/specs/2026-11-02_composition_gate_devtask_v1.md:281-286`（P1.2 已接线） | 已实现（fail-visible） |
| 内核门 | kernel_guard，无 ADR 拒收 | `tools/kernel_guard.py --list` 40 条；devtask:2436-2448 + hook:460-470；K8 artifacts 含 devtask/test_gate 自身 | 已实现 |
| merge train | 两窗口+禁并期+文档旁路 | `tools/merge_train.py status`：07:51 `allowed=false/mode=outside_window`；`devtask.py:2411-2435`、`merge_preflight.py:474-486` 已接线 | 已实现（规格文档说"未接线"已过时） |
| 门锁 H5-a | test_gate/baseline master 钉扎 | `tools/merge_gated.py:109-144`；`devtask.py:2032-2040` 调用 `gate_pin_check` | 已实现 |
| 门锁 H5-b | 一次性 preflight token（TID+HEAD、TTL900s、用后删） | `tools/merge_preflight.py:53-55,79-96`；devtask 质量闸通过后写 token `:2071-2080`；hook 命中即跳过重复测试 `:445-451` | 已实现（见缺口 G-2） |
| L2 宿主机门 | 五要素+人工豁免留痕 | devtask `cmd_merge` L2 段（约 :2259-2395）；ADR-012:12-15 | 已实现（人工通道） |
| 安全 warn 门 | security/gitleaks/OCR/视觉 | 规格 `…composition_gate…:44`（warn 不阻断） | 已实现（warn） |
| Q1–Q4 分级门 | active 必须 Q2 | 无自动判定工具；`tools/composition_audit.py`=planned（`kernel_v1.yaml:43-46`）；W4:110 "无 Q 计算器" | **纸面** |
| G4 发布门 | 健康探针+SLO+provenance | ADGS:102 列出；未发现 merge/发布链路执行点 | **纸面** |
| 实现/验收分离 | 验收人≠实现者 | `docs/standards/2026-10-27_实现与验收分离_v1.md:5,26-30` 明示"暂不阻断" | **warn 观察**（非门） |
| 扩展 lint 门 | extension_lint 接 merge | `docs/specs/2026-10-27_扩展契约_v1.md:71-76` "下一步（P2，未做）" | **纸面** |
| 文档漂移门 | DOCS_INDEX --check rc=0（P9） | 工具存在 `tools/gen_docs_index.py:3-8`，但 devtask/test_gate 无调用点（grep 仅工具自身） | **纸面**（手动） |

**缺口（带证据）**：G-1 组合 `validate --all --strict` 当前 rc=1（2 条 `dataset/pg` soft→strict 升级，data-warehouse/quant-domain）→ W1 的"strict 6/6 绿"已不成立；G-2 devtask 路径下 token 命中的 hook 会跳过组合/内核/merge-train 复检（`merge_preflight.py:445-451`），第二道仅在 token 缺失时生效；G-3 route 基线漂移：`/dw.html` 已 200→302、11-03 G1 基准过期（`STATUS.md:4203`）；G-4 仓库 `portal/nginx.conf` 有 +16 行安全段未提交（`STATUS.md:4203,4208`）；G-5 merge_preflight 组合/内核复检在 `docs_only` 之前（设计如此，文档 `…composition_gate…:284` 已注记）。

## E. W1–W4 完成度与未完成（以 ROADMAP v4 + 11-02/11-03/11-04 计划为准）

| 波次 | 出口判据〔文档〕 | 当前完成度 | 关键未完成 |
|---|---|---|---|
| **W0 收编** 11-02~11-04 | 台账 v0；doing≤8/worktree≤20/parked≤8；merge train 生效；试点 10 项开跑（`ROADMAP_v4:39`） | **未关闭**：doing=41/review=8/worktree=60/parked=8（实测 tasks.json+目录）；11-04 文档口径 doing48/worktree64（`2026-11-04_研发计划_v2.md:15`）；WIP 告警见 `STATUS.md:3953` | WIP 超限 3~7 倍；安全回收与 18 parked 定性；W0 计划已顺延 11-04~11-08 且"≤8/≤20 条件化"（`2026-11-04_研发计划_v2.md:67-88`） |
| **W1 组合纳管** 11-03~11-16 | 新增组合内核 diff=0；引用 100% 可解析；Q2 门槛；11-16 复审（`ROADMAP_v4:40`） | **部分/口径打架**：P1 工具 finish（validate/plan/diff/retire/kernel_guard/resolver）已交付并接线；resolver 11-03 报 131refs 0/0（`ROADMAP_v4:150-151`）；`ROADMAP_v4:222` 称"W1 条件关闭"、`:224` 同文件又称"不关闭" | 实测 153refs/2 warnings rc=1；Q2=0（7 组合全 draft/validated=false）；7 tier 模板仅 4；W1 正式签字 11-16 未到 |
| **W2 单栈(L1)** 11-10~12-02 | ADR-016 L1 全绿；运维只剩 8734；5175/8102 off（`ROADMAP_v4:41`；ADR-016:90-100） | **主体达成但未签字**：实测 5175/8102 no-listen；`/quant/`、`/quant-api/` nginx location 已在（`portal/nginx.conf:1156,1161`）；L1 17/18，8091 待管理员换血（`2026-11-04_研发计划_v2.md:16,129-134`） | 8091 换血（L1 18/18）；5175 引用 14 处清零/启动器默认值（UF-14）；单认证三态；ACAD1 池单口径；nginx +16 未提交 |
| **W3 解耦** 12-03~01-15 | E1–E8：问题表 P0/P1 清零过半、active≥Q2、页面/数据/任务可声明、契约强制门绿（`2026-11-02_W3解耦启动包_v1.md:238-251`） | **未启动**（12-03 起）；启动包已完成基线盘点 | 页面 manifest 0/104、分派表化 32/290(11%)、seat/workflow/job schema=0、job manifest 0、契约强制点 3 处未接（W3 包 §2.1-2.5） |
| **W4 组合化** 01-16~03-31 | 组合账本 100%；T1–T5≥4/5；Kernel v1.0；替换演练（`2026-11-02_W4组合化收口框架_v1.md:12-23`） | **未启动**；框架文档就绪（3 批 B1/B2/B3） | 10 planned artifacts 清偿；Q2→Q3 冲刺；替换/退役演练；季度 Kernel Review（W4:277-304） |

> 硬节点现状：11-04 黄忠"全链路验收"已改**中期复核**（4/6 NO_GO，`2026-11-04_研发计划_v2.md:12,121-127`），最终验收最早 11-10 22:15；11-08 ACAD1 冻结**当前 NO_GO**（0/13 复核、20 blockers，`:86,111-119`）；12-02 L1 不变（ADR-016:55）。

## F. 红线与不能碰的边界

1. **单入口/单认证/单库/单调度/单组合账本**：8734 唯一入口（`AGENTS.md:18`、ADR-009:15）；"一套"7 维（ADR-016:27-37）；禁止第二入口/认证/DB/调度（ZBF:142-146、ADR-017:16-18）。
2. **开发只允许三种形态**：组合变更/实现替换/内核变更(ADR)；临时脚本/无 manifest 上线非法（ZBF:131-138,177-185）。
3. **L2 五要素+人类批准，沉默≠同意**：重启/停服/计划任务/凭据区/nginx/宿主机（ADR-012:12-15；`AGENTS.md:80`；principles P6）。
4. **禁 `git clean -fdx` / `reset --hard`**（共享仓；11-02 两次清掉未跟踪文件）（`AGENTS.md:112`；ADGS:132）。
5. **数据根互斥**：结构性操作前读 `C:\Temp\kg_sync_task.log`，完成后补记；唯一数据根 `quant_backtest/data`（`AGENTS.md:38`；ADR-011:11-16；实测 Test-Path=True）。
6. **凭据只进 `_vault`**：不入 git/聊天/日志（`ARCHITECTURE.md:31`；ADR-010）；MCP 默认只读+token_env（`platform/mcp/registry.yaml:8-11`）。
7. **portal_api.py 只改不扩**（预算 5,200 行，`AGENTS.md:74`）；8091 冷启动 import 预检不得绕过（`:79`）。
8. **`C:\项目\work` 根退役**：新代码禁止依赖（ADR-011:15；`ARCHITECTURE.md:34`；principles P2）。
9. **删除走回收站、长跑输出落盘、改常量服务须换血验证**（`AGENTS.md:72-78`）；实现/验收分离（标准 v1）。
10. **共享文件单 owner + write_scope**（`AGENTS.md:111`；ADGS:129-133）；绕过门禁仅 `DEVTOOL_MERGE_GATE=0`（回滚/紧急+留痕，`AGENTS.md:35-36`）。

## G. 冲突与风险清单（14 条，均带证据）

| # | 冲突/风险 | 证据 |
|---|---|---|
| 1 | **组合数 6 vs 7**：框架/技术方案仍写"6 影子/6 组合"，实际 7（dev-legion 11-03 入账） | `PLATFORM_FRAMEWORK_v1.md:44`；`2026-11-02_技术框架统一方案_v1.md:44`；W4:18；vs `compositions/registry.json:117-130` + 实测 7 |
| 2 | **"九道门"与代码不一致**：文档并列 10 项/称九门，GATES 实测 8 条，route/budget 在 test_gate 内而非 GATES | `PLATFORM_FRAMEWORK_v1.md:41`；ADGS:99；`tools/devtask.py:433-444`；`tools/test_gate.ps1:445-539` |
| 3 | **ADR-018/019 编号无对应 ADR-0xx 文件**：实际文件名为 `2026-11-02_ACL-103…`/`DWH-103…`，且 docs/README 索引停在 ADR-020（ADR-021 已存在） | `platform/kernel_v1.yaml:156-167`；`docs/adr/2026-11-02_ACL-103…:3-4`；`docs/adr/ADR-021…:1`；`docs/README.md:36` |
| 4 | **战略源索引过期**：docs/README 仍把 PRODUCT_ROADMAP v3 当"唯一战略源" | `docs/README.md:20` vs `AGENTS.md:108`、`ROADMAP_v4:5-9`、v3 头部 superseded |
| 5 | **历史治理页与现行入口冲突**：治理 README 写"当前门户实际入口为 8080；8734 未生效" | `docs/governance/README.md:39` vs `AGENTS.md:18` + 实测 8734 LISTEN |
| 6 | **端口在册数过期**：AGENTS/ADR-009 称"52 口在册"，实测 REGISTRY=62 | `AGENTS.md:14`、`docs/adr/ADR-009-port-registry-entry.md:4,12`；实测 `len(REGISTRY)=62` |
| 7 | **merge train 规格文档过期**：称"工具已交付、未接 devtask/钩子"，实际已接线 | `docs/specs/2026-11-02_merge_train_v1.md:5,135-141` vs `tools/devtask.py:2159,2411-2435`、`tools/merge_preflight.py:352,474-486` |
| 8 | **W1 关闭口径三说**：同文件"条件关闭/不关闭"，11-04 计划"PASS"，实测 strict 红 | `ROADMAP_v4:222` vs `:224`；`2026-11-04_研发计划_v2.md:102-106`；实测 resolver rc=1(153refs/2warn)、validate strict rc=1 |
| 9 | **"质量分级"三套标准并存**：ZBF Q1–Q4（组合）、2026-10-27 Bronze/Silver（席位/工具/集成）、kernel draft/partial/existing | `PLATFORM_FRAMEWORK_v1.md:120-129`；`docs/standards/2026-10-27_质量分级_v1.md:15-28`；`kernel_v1.yaml` 各层 status |
| 10 | **计划日期滑移未回写 ROADMAP**：11-04 HZ 验收→中期复核、最终 11-10；W0 出口 11-04→11-08 条件化；但 ROADMAP 仍列 11-04/11-08 硬节点 | `ROADMAP_v4:14,246` vs `2026-11-04_研发计划_v2.md:12,67-88,121-127`；`STATUS.md:4212-4218`（11-04 07:47 六条 6/6） |
| 11 | **运行态基线漂移**：ROADMAP 基线称 5175/8102 在跑；实测已 off（文档未回写） | `ROADMAP_v4:22` vs 实测 no-listen；`ARCHITECTURE.md:19` "8787 旧代运行中/双代收口待批"vs 8788 down |
| 12 | **ACAD1 11-08 冻结硬闸 NO_GO**：13 卡 0 复核/20 blockers/四套卡口径/池 12≠30，S1–S5 未定则只能报 NO_GO | `2026-11-04_研发计划_v2.md:86,111-119`；`docs/audit/2026-11-04_ACAD1冻结准备_v1.md:47` |
| 13 | **门禁例外已发生**：11-04 06:25 记录"P1 门禁例外 24h 复盘"，说明存在绕门事件 | `STATUS.md:4182` |
| 14 | **Q2 术语过载**：Q2 既指组合质量等级、又指 q2-auditor/Q2 验收轮次 | `PLATFORM_FRAMEWORK_v1.md:122-129` vs `2026-11-03_整体研发方案_v2…:218,236` |

## H. Top 20 现行工程必读（按理解价值排序）

| # | 文档 | 一句话理由 |
|---:|---|---|
| 1 | `AGENTS.md` | 开发宪法：端口/隔离/门禁/红线/ADGS 强条款全在此，冲突时最高优先 |
| 2 | `ARCHITECTURE.md` | 现行架构权威简图：组件端口/数据边界/ADR 索引（73 行可全读） |
| 3 | `docs/architecture/PLATFORM_FRAMEWORK_v1.md` | ZBF v1 总纲：K0–K8/C1–C8/黄金路径/Q1–Q4/问题表（框架唯一出处） |
| 4 | `docs/adr/ADR-016-single-host-integration.md` | 唯一宿主决策与"一套"7 维验收、L1/L2 清单——所有整合工作的准绳 |
| 5 | `docs/adr/ADR-015-platform-kernel-composition-model.md` | 内核/组合/T1–T5 判据与变更纪律，K0–K8 分层定义源头 |
| 6 | `platform/kernel_v1.yaml` | 内核冻结清单（draft 0.9.0）：哪些文件变更必须带 ADR，逐 artifact 可查 |
| 7 | `compositions/README.md` + `compositions/registry.json` | C1–C8 模型、校验规则、7 组合账本与每组合 evidence/health 现状 |
| 8 | `docs/adr/ADR-017-framework-golden-path.md` | 唯一开发路径（三种合法形态/六步法/例外流程）的制度来源 |
| 9 | `docs/standards/2026-11-02_敏捷开发与治理标准_v1.md` | ADGS v1.1：车间/车道/DOR-DOD/G0–G4/merge train/WIP 分阶段数值 |
| 10 | `docs/plans/2026-11-02_ROADMAP_v4_唯一战略与主排期.md` | 唯一战略源 + W0–W4 出口 + 11-02~11-04 执行状态流水（含最新事实） |
| 11 | `docs/specs/2026-11-02_composition_gate_devtask_v1.md` | 组合门/内核守卫接线规格与实现注记（门顺序/失败语义/绕过面） |
| 12 | `docs/specs/2026-11-02_merge_train_v1.md` | merge train 规则与 JSON 契约（工具真实行为以 `tools/merge_train.py` 为准） |
| 13 | `tools/devtask.py`（重点 :2009-2448） | 合并门唯一执行点：质量闸→merge train→组合/内核门→锁→git merge 的真实顺序 |
| 14 | `tools/check_ports.py`（REGISTRY :41-141） | 端口唯一真源（62 条）+ OPTIONAL_OFFLINE 语义，判现行/退役第一步 |
| 15 | `docs/plans/2026-11-02_技术框架统一方案_v1.md` | 唯一技术选型+禁止清单+30 个 WBS（UF-01~30）+已定决策 15 条 |
| 16 | `docs/plans/2026-11-04_研发计划_v2.md` | 当前最诚实的基线（W0 48/64、W1 resolver、L1 17/18、HZ 日期、ACAD1 闸门） |
| 17 | `docs/plans/2026-11-03_整体研发方案_v2_最优执行版.md`（§五/§六/§九/§十五） | 执行层单一方案：L0–L3 目标架构、5 作战单元、单一真源层级与 RND v1→v2 映射 |
| 18 | `docs/plans/2026-11-02_W3解耦启动包_v1.md` | W3 基线数字与 E1–E8 出口清单（页面/Seat/Workflow/job/契约五工作流） |
| 19 | `docs/plans/2026-11-02_W4组合化收口框架_v1.md` | W4 三件套/Kernel v1.0 冻结标准/T1–T5 测量方法/A1–A10 验收 |
| 20 | `platform/mcp/registry.yaml` + `platform/interfaces/hermes-control.yaml` | 运行面契约：13 MCP server 白名单与 Hermes 控制面 8787 单点登录边界 |

> 备选（第 20 名之后）：ADR-014（Run/Event）、ADR-020（作战屏契约，含 v2 修订）、ADR-021（控制面选型）、`docs/standards/2026-11-03_接口对接规范_IFS_v1.md`（IFS 唯一权威条款）、`docs/standards/2026-10-27_质量分级_v1.md`（Bronze/Silver）。

## 方法与覆盖（可复核）

- **精读 36 份**：AGENTS/ARCHITECTURE/README(根)、PLATFORM_FRAMEWORK/principles/BusinessOS、ADR-008/009/011/012/013/014/015/016/017/020/021、composition_gate/merge_train 规格、ADGS/质量分级/实现验收/版本发布/扩展契约、ROADMAP v4/技术框架统一方案/W3/W4/11-03 v2/11-04 v2、kernel_v1.yaml/registry/README/q2 legions/MCP registry/hermes-control、STATUS 定位段。
- **实测命令**（只读）：`git rev-parse/ls-files/status`；`Get-NetTCPConnection/Get-Process`；`python tools/composition.py validate --all [--strict]`、`plan --strict --json`、`tools/resolver.py --all --strict --json`、`tools/kernel_guard.py --list`、`tools/merge_train.py status --json`、`len(check_ports.REGISTRY)`、`.worktrees/tasks.json` 统计、`Test-Path`（数据根/运行态）。
- **口径差异说明**：本报告 md 统计（2675 tracked）与任务书 2,669 存在差异，因并行会话在提交；`dirty=253` 说明工作区有未提交改动，见 `STATUS.md:4203`（nginx.conf +16 行未提交）。
- **局限**：①部分进程 StartTime 因权限/进程更替为空，已以 STATUS 记录补证；②未逐一实测 8096-8100 的业务语义（仅按 REGISTRY 归因）；③ADR-018/019 以文件内自述编号认定；④**未全覆盖**：docs/specs 其余 8 份、docs/governance 30 份、docs/legion 60+ 份、deliverables 713 份仅索引/抽样未精读（超出平台层范围）。
