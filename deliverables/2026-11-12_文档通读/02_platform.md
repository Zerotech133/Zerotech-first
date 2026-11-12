# 02 · 平台 / 架构 / 契约 / 门禁层通读（只读）

> 实测窗 2026-11-12 08:25–08:32 Asia/Shanghai；只读（未改仓/git 写/启停服务）。
> 快照：HEAD `044328307`（08:25，dirty 216）；STATUS 08:04 记 `4ec8c8f96`（今日 22 提交，worktree 79/doing 31/review 28/parked 9）。并行提交，数字点测。
> 〔实测〕=命令输出；〔文档称〕=仅文档称；证据 `path:line`。

## 一、当前真实架构（进程 / 端口 / 数据根 / 模块边界）

```
人类浏览器
  └─ 8734 (svchost 的 netsh portproxy, 〔实测〕)
       └─ 8080 (Docker nginx "ai-portal", 〔实测〕)          ← 同源反代层
            ├─ /             → 静态页（portal/）
            ├─ /api/*        → 8091 portal_api.py（851 路由前缀分派）
            ├─ /_auth_guard  → 唯一鉴权开关（auth_request，fail-closed）
            ├─ /legion/*     → 8796 legion_command（MCP 258 tools）
            ├─ /oslink/*     → 8795 OSLINK 网关（W4 隔离通道，P2c 暂缓）
            └─ /hermes/*     → 8787 Hermes WebUI（旧代运行中，双代收口待批）
旁路：8103 / 8105 / 8791 / 5446（zhice-db PG，Docker）——详见端口表
数据面：quant_backtest/data（KG_DATA_ROOT；quant.db 183MB / news.db 155MB〔文档称〕）、
        C:\项目\analysis_work（投研流水线）、PG 5446（文档称单业务库）、C:\项目\_vault\secrets（凭据，不入 git）
组合层：compositions/（7 manifest + _schema + registry.json）；内核：platform/kernel_v1.yaml（K0–K8，v0.9 draft）
宿主唯一：ADR-016（人类入口唯一 8734 / 单认证 / 单库 / 单调度 / 单账本，PLATFORM_FRAMEWORK_v1.md:97-118）
```

### 端口 / 服务表（实测 08:29，`Get-NetTCPConnection` + 进程归属）

| 端口 | 实测监听 | 归属（实测） | 文档定位 | 分级 |
|---|---|---|---|---|
| 8734 | 是 | svchost（portproxy） | 人类唯一入口（ARCHITECTURE.md:17） | 现行 |
| 8080 | 是 | com.docker.backend | 容器/LAN 反代链路，仅 8734 可对外写（AGENTS.md §1） | 现行（不写入口） |
| 8091 | 是 | python（portal_api.py） | 门户 API（ARCHITECTURE.md:18） | 现行 |
| 8103 | 是 | python | 未入架构端口表 | 现行旁路 |
| 8105 | 是 | python（智策 API） | 未入架构端口表；ADR-016 称收敛为量化域资产 | 现行旁路 |
| 8787 | 是 | python（Hermes WebUI） | Hermes（ARCHITECTURE.md:21） | 现行（旧代） |
| 8791 | 是 | python（kg_data_dashboard） | 未入架构端口表 | 现行旁路 |
| 8795 | 是 | python（OSLINK 网关） | W4 隔离通道（ARCHITECTURE.md:20） | 现行 |
| 8796 | 是 | python（legion_command） | 军团命令面（ARCHITECTURE.md:19） | 现行 |
| 5446 | 是 | docker（zhice-db） | 单业务库（PLATFORM_FRAMEWORK_v1.md:100） | 现行 |
| 5175 | **否** | — | 智策前端；REGISTRY 标 "retired 2026-11-02 L1" | **已退役** |
| 8102 | **否** | — | 智策 API；REGISTRY 仍登记 "智策OS API(zhice)" | **已停未删（待收尾）** |
| 8788 / 8101 / 8898 / 18096 | **否** | — | 旧隔离通道 / 旧服务（8898 文档称 res-downloader 活服务，实测无监听） | **已停/待核实** |

- 注册表规模〔实测〕：`tools/check_ports.py` REGISTRY=72、OPTIONAL_OFFLINE=40；STATUS 08:04 记 "端口注册72/离线28/未登记1(rustdesk 33213)/冲突0"。
- 模块边界：门户 portal/（页+API 同源）｜军团 agent_platform/（8796 命令面/8795 网关/进程内引擎）｜Hermes/（旧代）｜产线 analysis/、quant_backtest/、飞书 bot｜组合层 compositions/｜内核 kernel_v1.yaml｜公共库 common/、pylibs/、tools/。规则见 ARCHITECTURE.md:28-43。

## 二、门禁与合并流程现状

### 2.1 merge 实际链路（源码实证，非纸面）

`devtask merge <TID>` → ①纯文档单跳过测试 → ②H5-a 钉扎 `gate_pin_check`（`devtask.py:2287`）→ ③worktree 跑 `test_gate fast`（9 套件+budget+route，~400–500s；`devtask.py:2275-2301`）→ ④写一次性 preflight token（TID+HEAD、TTL 900s；`devtask.py:2331`、`merge_preflight.py:54-64`）→ ⑤git pre-merge-commit 钩子验 token（`.git/hooks/pre-merge-commit:9`〔实测〕）→ ⑥compositions/** 或内核 artifact 触发组合/内核门（`devtask.py:2339-2401`）→ ⑦merge train 窗口（主 16:30–17:00/次 21:40–22:10；`merge_train.py status`〔实测〕NO/outside_window，下一主窗 11-12 16:30）→ 合并。
绕过开关只为回滚/紧急：`DEVTOOL_MERGE_GATE=0`（ADR-026 唯一先例：PII 窗口外合并，且 ARCH1 复核仍待回签）。

### 2.2 门禁清单（实现状态）

| # | 门 | 实现证据 | 状态 |
|---|---|---|---|
| 1 | Mixin 方法集 | `tools/devtask.py:440-451` GATES[0] | 已实现·每 merge |
| 2 | 导航统一 | `tools/devtask.py:440-451`；`test_gate.ps1:586-608`（缺工具仅 warn） | 已实现 |
| 3 | 端口注册 | `tools/check_ports.py --strict`（REGISTRY 唯一源） | 已实现 |
| 4 | UI/UX 审计 | `devtask.py:440-451` GATES 含该项 | 已实现（AGENTS §5 表未列，口径缺口） |
| 5 | 明文凭据 | `_secret_scan_gate`→`tools/scan_secrets.py`；pre-commit 亦扫 | 已实现 |
| 6 | 语法门 | `tools/check_syntax.py --fail-on-warning`（缓存 ~0.4s） | 已实现 |
| 7 | 导入边界 | `tools/check_import_boundaries.py` | 已实现 |
| 8 | 元数据一致性 | `tools/datawarehouse/metadata_consistency.py`（P77 起全阻断） | 已实现 |
| 9 | 路由清单 | `tools/portal_route_inventory.py --check`，基线同提交 `--write-baseline`；`test_gate.ps1:614-640` | 已实现·阻断 |
| 10 | budget 预算 | `tools/check_portal_split.py --strict`；`test_gate.ps1:553-577` fail-closed | 已实现·阻断 |
| 11 | 测试门（9 套件） | `tools/test_gate.ps1:125-126`：portal,kstock,ide,agent_key,quant_key,tools_key,dwh,analysis,commercial | 已实现·fast ~465–533s |
| 12 | H5-a 钉扎 | `tools/merge_gated.py:12-13,35-37,75,109-119`：test_gate.ps1 + test_baseline.json 须与 master 完全一致 | 已实现（ADR-027 不申请例外） |
| 13 | H5-b preflight token | `merge_preflight.py:54-64` + hook | 已实现·一次性 |
| 14 | 组合门 | `devtask.py:2339-2401`：`composition.py plan --strict` 漂移即拒 + manifest errors 即拒；`validate` 7/7 PASS errors=0 warnings=0〔实测〕 | 已实现·条件触发 |
| 15 | 内核门 | 同上 + `kernel_guard.py --files ... --commit-msg`；`platform/kernel_v1.yaml:5-6` v0.9.0 **status: draft**；L15 规则=冻结后改 contract/schema/gate 须带 ADR 号 | **已接线，基准未冻结**（ADR-015 仍 Proposed） |
| 16 | merge train | `tools/merge_train.py`；窗口与禁并期见 AGENTS §9.8 / ADGS v1.1 §8 | 已实现（force 仅 L2） |
| 17 | GOV_MODE 治理嵌入 | `devtask.py:1141-1144`（off/shadow/warn/enforce，state `tools/gov_ratchet.py`）；`config/gov_state.json`〔实测〕mode=off；P1 已合入（11-11 2d1689971），P2 shadow 11-08 决策暂不启动 | 已实现·默认关 |

**评审门计数三口径（重要冲突）**：`devtask.py:440-451` GATES=**8**；ADR-015:12 与平台话术称"九道门"；`AGENTS.md:50-61` §5 表列 **10 行**（+路由门/budget 门/测试门）；`PLATFORM_FRAMEWORK_v1.md:41` 列 10 个名字。实现真相=8 基础门 + fast 门内 route/budget + merge_gated 测试门，即阻断点 ≈11，无单一权威数字。

## 三、ADR 决策清单与缺口

`docs/adr/` 实存 22 份（001–017、020、021、025–027 + 20261103 + 018/019 异名文件）。

| 编号 | 标题（简） | 状态 | 一句话结论 |
|---|---|---|---|
| 001–003 | repomap 自建 / agents bundle 单源 / 研发单路由 | accepted（复核期已过） | 仓库地图自研；27 岗导出单一源；ARCH1 优先 |
| 004 | 文档体系 Diátaxis+ADR | Accepted 10-27 | 文档分类与 ADR 纪律 |
| 005 | Agent 可观测 OTel GenAI | **Proposed** | 分阶段，未决 |
| 006–008 | worktree 隔离 / 工具矩阵单源 / 席位契约 | Accepted | 一单一事；授权单源；SOUL 等四件套 |
| 009–011 | 端口注册 / 凭据 vault / 数据根 | Accepted | 127.0.0.1 唯一真源；密文+token env；唯一数据根+work 退役 |
| 012–013 | L2 五要素 / 单机范围与文档纪律 | Accepted | 沉默≠同意；个人 OS 边界 |
| 014 | Agent 运行契约 v1 | Accepted；P2 部分 | run/event/conversation；trace.run_scope 等已落，余项 W3 |
| 015 | 平台内核契约与组合模型 v1 | **Proposed** | K0–K8 + C1–C8 + 黄金路径；内核冻结待 A0 |
| 016 | 唯一宿主整合 | Accepted 11-02 | 智策收敛为量化域资产；L1 截止 12-02 关 5175/8102 |
| 017 | 唯一开发框架与黄金路径（ZBF v1） | **Proposed** | 三种合法形态；禁第二入口/认证/DB/调度；例外 ADR+90 天 |
| 020 | 研发军团作战屏 v1 数据契约 | Accepted/M0 冻结 | 只读契约；自述"AI 代确认，非人类签字" |
| 021 | 多 AI 军团控制面选型 | Accepted 11-02 | 原方案+Hermes+薄 HQ；Paperclip 仅隔离 PoC；W3 12-03 起 |
| 025 | 治理嵌入开发流（影子→告警→强制） | Accepted 11-06 | GOV_MODE 四档；P1 已合入；P2 shadow 暂不启动；mode=off |
| 026 | PII W2/W3 窗口外紧急合并 | Accepted 紧急 | `DEVTOOL_MERGE_GATE=0`+`--train-force` 先例；24h 复盘；**ARCH1 复核待回签** |
| 027 | GATEHEALTH 分支结案归档 | Accepted 11-10 | fast 7→9（master 5bf84007f）；不申请 H5 钉扎例外 |
| 20261103 | 分钟 K 线数据核查与缠论可实现性 | 已接受 | 数据核查结论修正 |

**索引缺口（证据）**：`docs/adr/README.md`：001–020 状态列全部为 "—"（L8-25 未回填）；025–027 状态单元格破损为 `**Accepted |`（L27-29，Markdown 未闭合）；无 ADR-018/019 行（实为 `2026-11-02_ACL-103…` 自述 ADR-018、`2026-11-02_DWH-103…` 自述 ADR-019、另 `2026-11-05_ADR-018_接受状态修正说明`）；**ADR-022/023/024 无文件无登记**。`ARCHITECTURE.md:49-60` ADR 索引止于 ADR-020，缺 021/025/026/027。ADR-020 自述非人类签字、ADR-026 复核未回签 = 两项治理留痕缺口。

## 四、文档与代码/运行态不一致清单

| # | 冲突 | 证据 |
|---|---|---|
| 1 | 端口数 52 vs 72 | `AGENTS.md` §1 "52 口在册（2026-10-27 实测）" vs `tools/check_ports.py` REGISTRY=72〔实测〕+ STATUS 08:04 |
| 2 | fast 套件 6/7 vs 9 | `AGENTS.md` §3 "六套件"、`DEVELOPER_GUIDE.md` §7.2 "7 套件"、`docs/testing.md:43-46` "待落地" vs `tools/test_gate.ps1:125-126`=9〔实测〕、ADR-027 |
| 3 | 评审门 8/9/10 三说 | `devtask.py:440-451`=8；ADR-015:12"九道门"；`AGENTS.md:50-61`=10 行；`PLATFORM_FRAMEWORK_v1.md:41`=10 名 |
| 4 | portal_api 行数三说 | 实测 3,519 行；`DEVELOPER_GUIDE.md:190`=3,895；`AGENTS.md:75`=5,052（预算 5,200） |
| 5 | 战略唯一源悬挂 | `docs/README.md:20` 与 AGENTS §9 指 `docs/plans/2026-11-02_ROADMAP_v4_唯一战略与主排期.md`，实测该文件已移入 `docs/plans/_archive_20261110/`，`docs/plans/` 已无 ROADMAP |
| 6 | 系统设计仍以 8080 为现行入口 | `docs/system_design.md:39`"8734 无 portproxy"、`:224`"当前事实入口是 8080；8734 不得写成已生效" vs 现行 8734 唯一（`ARCHITECTURE.md:17`）；该文件实为 2026-10-17 治理增量设计，无归档头 |
| 7 | 测试指南过期 | `docs/testing.md`（10-27）旧基线 portal 289 / k_stock 791 / agent_platform 1833 / quant 2379 / e2e 13；称 fast 门待落地 vs 现行 9 套件+三把尺子（`DEVELOPER_GUIDE.md` §7.3） |
| 8 | 架构端口表不全 | `ARCHITECTURE.md:15-26` 仅 5 端口；实测另有 8103/8105/8791/5446/8080（REGISTRY 72）无文档位 |
| 9 | 8734 链路描述不精确 | `ARCHITECTURE.md:17` 称 "8734（nginx 同源反代）"；实测 8734=svchost portproxy→8080 Docker nginx→8091 |
| 10 | ADR 索引/状态缺口 | 见 §三：001–020 状态 "—"、025–027 表格破损、无 018/019 行、022–024 空号、ARCHITECTURE 止于 020 |
| 11 | 退役进度与注册表不同步 | 5175 已标 retired 且无监听；8102 无监听但 REGISTRY 仍登记"智策OS API(zhice)"；ADR-016 的 L1 截止 12-02 尚未走完 |
| 12 | res-downloader 服务状态 | `AGENTS.md` §8 称 8898/18899 活服务+自启计划任务；实测 8898 无监听 |
| 13 | 治理文档时效 | `docs/governance/` 15+ 文件均 2026-10-17/18 证据包，无归档标记；现行治理=ADR-025/027+GOV_MODE |
| 14 | 门禁话术无单一权威 | ADR-015:12、`PLATFORM_FRAMEWORK_v1.md:41`、`AGENTS.md:50-61` 互相矛盾；无单一门禁清单文件（`docs/specs/2026-11-02_composition_gate_devtask_v1.md` 只管组合门） |

## 五、平台层下一步建议（3–5 条）

1. **口径校正（1 张单、当天可完成）**：AGENTS/DEVELOPER_GUIDE/docs/testing 的端口数（52→72）、fast 套件数（6/7→9）、评审门计数（统一为"8 基础门+fast 内含 route/budget+测试门"）改正；`ARCHITECTURE.md` 端口表补 8103/8105/8791/5446/8080 或标"旁路/容器"；REGISTRY 删/标 8102。建议用脚本从二者生成 docs 数字摘要防漂移。
2. **ADR 索引收口**：回填 001–020 状态、修 025–027 表格、为 ACL-103/DWH-103 补 ADR-018/019 索引行、明确 022–024 空号原因、`ARCHITECTURE.md:49-60` 补 021/025/026/027；催 ARCH1 回签 ADR-026；请 A0 拍 ADR-005/015/017（否则内核无 frozen 基准）。
3. **修战略源悬挂与历史文档头**：ROADMAP v4 已归档但仍是 4 处文档的"唯一源"——要么恢复 live 路径，要么重定唯一战略源并同步 `docs/README.md:20`/AGENTS §9/plans 索引；`docs/system_design.md`、`docs/testing.md`、`docs/governance/` 加"历史/归档"头或迁 `_archive`。
4. **组合/内核门从"接线"到"可信"**：当前 `composition validate` 7/7 PASS、`plan findings=0`〔实测〕，但 kernel_v1.yaml status=draft、ADR-015 Proposed；建议冻结 K0–K8 artifact 哈希→升 Kernel v1.0，把 `plan --strict` 从条件触发改为 merge train 常规步骤，并按 ADGS §9 落"每月组合账本审计"。
5. **治理转段**：P1 已合入（2d1689971）、GOV_MODE=off；按 ADR-025 选 P2 shadow 窗口（先 1 周 shadow 看误报），把 qgate/merge 耗时（STATUS timing n=8：total 532.7s、qgate 465.7s）纳入日报，避免"实现但常关"。

## 六、方法与覆盖（自证）

- 精读：AGENTS.md(118 行)/ARCHITECTURE.md(75 行全文)/DEVELOPER_GUIDE.md(622 行：§0–§8 全文+§9–§11 大纲)/docs/README、docs/adr/README+22 份 ADR、PLATFORM_FRAMEWORK_v1、docs/specs 18 份清单、ADGS v1.1、dashboard_contracts、testing、system_design、5 份板块手册、统一真相源 v2(465 行大纲+阶段 G)、kernel_v1.yaml、compositions/、devtask/test_gate/merge_gated/merge_preflight/composition/kernel_guard/merge_train 关键段。
- 实测：Get-NetTCPConnection 归属、check_ports、composition validate/plan、kernel_guard --list、merge_train status、tasks.json、hook 存在性、gov_state.json。
- 限制：未读 ROADMAP v4 归档全文及其他历史长文；8080 nginx 上游、18899 未验证；数字为 08:25–08:32 点测。覆盖：退役分级/架构图/内核组合 Q 分级/门禁合并/ADR 缺口/红线冲突。
