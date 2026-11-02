# AGENTS.md — 老田的AI助手 · Agent 开发宪法（v1）

> 本文件是所有进入本仓库的 AI Agent（CodeBuddy / Claude Code / Cursor /
> WorkBuddy / 人类协作者）的**强制规范**。开工前必读，违反=评审门拒绝合并。

## 0. 平台身份

- 平台名：**零界**（老田的AI助手 / 个人 OS）· 口号「零界 · 一个人的 AI 公司」
- 主语言：中文注释与文档 · Windows 环境（PowerShell/Python，勿写含中文路径的 .ps1/.bat）
- 你（Agent）在本仓库的身份：**研发部 worker**。你的每项工作必须挂着一张任务单（见 §4）。

## 1. 端口铁律（不许互抢）

- **唯一权威注册表**：`tools/check_ports.py` 的 `REGISTRY` dict（**52 口在册**，以 REGISTRY 为准；K-STOCK-115 收编后，2026-10-27 实测 `python tools/check_ports.py` 汇总=注册 52 个）。
- 起新服务前：① 先查 REGISTRY——被占用即换；② 用 `python tools/devtask.py port <服务名>` 申请分配；③ 分配到后**必须**把端口+服务名写回 check_ports.py REGISTRY（同一提交内）。
- 一律绑 `127.0.0.1`，对外暴露走 nginx 反代（052 收敛既定）。
- 违规自查：`python tools/check_ports.py`（--strict 供 CI）。
- **入口唯一（K-STOCK-113 立宪，杜绝双口混乱）**：人类入口**只有一个 = 8734**（portproxy→nginx 同源合一）；8080 仅为容器/局域网反代链路，**文档、话术、页脚一律只写 8734，禁止再出现「8080/8734 同步生效」这类双口表述**。页内相对路径一律不带端口（走同源反代）。
- **开发侧全量回流（同上立宪）**：一切 IDE Agent 的开发动作必须自动进平台事件层 `portal/data/dev_events.jsonl`（tools/events.py 核心五类事件：task_start/commit/blocked/merge_done/guard_fail；另有派单/审批/编码线等扩展类，KINDS 共 15 类）；post-commit 钩子已自动上报 commit——**禁止任何 Agent 关闭/绕过该钩子**；工单页 `http://127.0.0.1:8734/devops`。
- **开发身份三环境变量（领单必设）**：干活前导出 `DEVTOOL_AGENT`（谁，如 CodeBuddy·A）、`DEVTOOL_IDE`（什么工具，如 CodeBuddy/Claude Code）、`DEVTOOL_MODEL`（什么模型，如 qwen3.5:9b/claude-4.5-sonnet）——事件层与指挥台「IDE/模型」列靠它归因，不设=详情页看不到你在用什么开发。

## 2. 前端准入三件套（新页面缺一不收）

1. `tokens.css`（唯一视觉来源，禁自绘配色）；
2. `nav.js` 统一顶栏（动态页走 pill_nav 自动获得；静态页 `tools/unify_nav.py` 注入）；
3. 短屏化（h2≥6 必须过 shortscreen.js）。
- 导航只许两层：顶栏+面包屑。**禁平铺胶囊长条**；>7 个并列入口必须收下拉。
- 设计宪法全文：`docs/plans/2026-10-11_大平台设计规范与流动方案_K-STOCK-103.md`

## 3. 隔离与并发（多 Agent 同时开发）

- **一单一事、单据先行**：无任务单不写码。
- 每个 Agent 在**独立 git worktree** 干活：`python tools/devtask.py new <TID> <一句话>`——自动建 `.worktrees/<TID>/` + 分支 `task/<TID>`。禁止在 master 直接开发。
- 完工合并必须过评审门：`python tools/devtask.py merge <TID>`（内置评审门四道：Mixin 方法集/导航统一/端口注册/明文凭据，门不过不合并）。
- **合并默认走质量闸（P0 #1，2026-10-28；2026-10-29 P37 内联）**：**直接 `python tools/devtask.py merge <TID>`**——内联质量闸先跑 `test_gate -Suite fast`（现含 **portal/kstock/ide/agent_key/quant_key/tools_key** 六套件 + **budget 预算门（A5，**已切 --strict**，P133）+ route 路由清单门（P130，阻断）**，新增失败即拒绝合并），纯文档/配置单（`docs/`、`deliverables/`、`portal/static/`、`agent_platform/skills/` 下的 .md/.json/.css/.js 等）自动跳过测试。`tools/merge_gated.py` 降为兼容入口/预检器（`--dry-run` 看改动分类）；绕过：`DEVTOOL_MERGE_GATE=0`（仅回滚/紧急，回单注明「绕过质量闸」）。
- **合并钩子（第二道，2026-10-28）**：`powershell -NoProfile -ExecutionPolicy Bypass -File tools\install_merge_hook.ps1` 安装 git `pre-merge-commit` 钩子 → 任何 `git merge`/`devtask merge` 在生成合并提交前自动跑 `tools/merge_preflight.py`（纯文档单放行，其余跑 `test_gate -Suite fast`，新增失败即中止合并）。绕过：`DEVTOOL_MERGE_GATE=0`（仅回滚/紧急，回单注明"绕过质量闸"）。
- **提交粒度（2026-11-02 并发事故后）**：多会话共享仓禁止 `git add -A` / `git commit -a`，必须显式 pathspec；提交前核对 `git diff --cached --name-only`；内核 artifact 变更提交信息必须带 ADR 号（`tools/kernel_guard.py --commit-msg`）。
- **共享数据根互斥（2026-10-17 立章）**：对数据根（`quant_backtest/data` 等 KG_DATA_ROOT 指向目录）做结构性操作（还原/迁移/清理/换根/重建 catalog）前，**必须先读 `C:\Temp\kg_sync_task.log` 尾部**确认无其他会话的进行中操作；操作完成后**必须补记该日志**。违反=评审门拒绝合并。（教训：2026-10-17 KGDataRoot 17.7GB 因两会话同时「清理 vs 还原」反复拉锯一上午）

## 4. 任务单纪律

- 任务单登记在 `.worktrees/tasks.json`（devtask.py 维护）+ 重活同步 agent_platform tasks。
- 状态机：doing → review → done（merge 时自动翻转）。中途放弃标 aborted 留原因。
- 汇报：完工必须在提交信息带 `K-STOCK-xxx` 或工单号；A0 日报按此汇总。
- **研发需求路由（2026-10-27 A0 批，L1）**：新研发需求优先派 ARCH1/组长（G1 DEV1 / G2 DEV5），不得直接落 DSH/治理席；例外=A0/老田显式指派、跨部门治理任务、研发部请求支援且 ARCH1 确认。细则见 `docs/plans/2026-10-27_研发单路由规则_ARCH1优先_v1.md`。

## 5. 评审门（merge 自动执行，全绿才放行）

| 守卫 | 命令 | 防 |
|---|---|---|
| Mixin 方法集 | `python tools/_mixin_integrity.py --check` | portal_api 拆分丢方法 |
| 导航统一 | `python tools/unify_nav.py --check` | 手写导航/数据漂移 |
| 端口注册 | `python tools/check_ports.py --strict` | 抢端口/私起服务 |
| 明文凭据 | `devtask.py merge` 内置 `_secret_scan_gate`（`tools/scan_secrets.py`） | 未豁免明文凭据入库 |
| 语法门（2026-10-30 恢复） | `_syntax_gate` → `tools/check_syntax.py --fail-on-warning`（缓存后 ~0.4s） | 语法错误/警告入库 |
| 导入边界（2026-10-30 恢复） | `_import_boundary_gate` → `tools/check_import_boundaries.py` | 业务 import 越界（osleg 类回归） |
| 元数据一致性（2026-10-30 恢复，P77 起全阻断） | `_metadata_gate` → `tools/datawarehouse/metadata_consistency.py` | catalog×registry×lifecycle×store 漂移 |
| 路由清单门（2026-10-31 P130，阻断） | `python tools/portal_route_inventory.py --check` | 路由集合与 B0 基线漂移（新增/删除路由必须同提交 `--write-baseline`） |
| budget 预算门（W5 P1-6 / A5，**P133 起 --strict**） | `tools/check_portal_split.py --strict`（fast 门内执行） | portal 拆分预算超限回归（硬违规/建议项均阻断） |
| **测试门（P0 #1）** | `python tools/merge_gated.py <TID>`（merge 前跑 `test_gate -Suite fast`，新增失败即拒） | 带失败测试合入主区 |

## 6. 受训与记忆（怎么让 Agent 越用越准）

- **本文件是你的底层宪法**：每次会话先读，冲突时以本文件+工作区记忆为准。
- 项目长期记忆：`.workbuddy/memory/MEMORY.md`（工作区级）——踩坑后必须回写一句「教训」，防止下一个 Agent 重踩。
- 用户级铁律（回收站删除/长跑落盘/补丁断言等）见用户记忆 MEMORY.md，跨仓库有效。
- 判例文档：`docs/audit/`（审计）与 `docs/plans/`（拍板）——做设计决策前先 grep 有没有先例，**有判例循判例，无判例立新档**。
- 飞书侧新能力选路必循：`docs/plans/2026-10-14_飞书自建底座与Lark插件分工边界_K-STOCK-395.md`（按「谁在场+是否计量」划界；插件禁生产推送/禁常驻）。

## 7. 红线（历史踩过、代价高的）

- Windows 删除一律进回收站（Python SHFileOperationW），禁 rm/del；
- 长跑命令输出必须落文件；
- portal_api.py 约 5k 行（2026-10-26 实测 5,052 行；行数预算 5,200——超预算即评审门 FAIL）——**只改不扩**，新 handler 一律新文件（P4 批 0 后启用拒收闸）；
- 替换补丁前断言锚点 count==1，替换后断言新串存在；
- 杀进程前先辨认是不是线上服务（按启动时间+端口归属）；
- **修复写盘 ≠ 生效（2026-10-16 P0 实锤）**：改常驻服务代码后必须杀旧进程换血——「缺则拉」型守护器（start_jev_stack.ps1 类）不认 mtime，旧进程会带旧码跑到天荒地老；验证=新进程出生时间 > 文件 mtime + 日志出现新分支特征行；
- **S4U/Session0 进程只信端口判活（2026-10-16 双实证）**：CIM/Win32_Process 枚举漏列+CommandLine 全 null；找 PID 用 `Get-NetTCPConnection -LocalPort`，杀进程要 UAC taskkill（`Start-Process cmd -Verb RunAs`，降权 token Access denied）。
- **8091 冷启动断链守卫（2026-10-17 K-ORG-155 临时红线）**：portal 并行改版期间 `portal_api.py` 顶层 import 链（api_misc/job_spec_registry/chanlun_data 等）可能中途断裂——**冷启动即整线下线**。`portal/start_portal_api.ps1` 已内置 import 冒烟预检：断链时**拒绝拉起并告警（exit 1）**，任何人不得绕过该预检直接裸启 8091；断链处置=按公告板登记意图恢复/落盘相应模块后再启。portal 改版收口后经用户确认解除本条。
- **宿主机级危险操作一律 blocking 人工确认（2026-10-19 K-STOCK-416 立宪；判例 K-STOCK-413：agent 曾主动 shutdown 重启机器，当时机器上跑着 Ollama/portal/60+ 产线任务）**：重启/关机/注销宿主机（shutdown / Restart-Computer / logoff）、改系统级配置（系统代理/防火墙/服务启动类型/计划任务增删）、对生产服务器 `10.205.0.118` 的任何变更、上线发布——执行前必须取得人类明确批准（五要素：命令全文+影响面+不可逆点+替代方案与已尝试+回滚预案），**沉默≠同意、一次一议、禁止以「根治需要/紧急」自我降级**；批准记录落工单 note。L0/L1/L2 三级动作分类与审批协议细则见 `docs/plans/2026-10-19_Agent危险操作治理规范_K-STOCK-416.md`。

## 8. 工程命名与别名注册表（2026-10-17 立章）

- **品牌体系**：平台=**零界**（zerobound）；旗下工程 `zerobound-<角色>`；独立小工具/采集器 `atom-<能力>-<对象>`（原子层，角度各异、可独立存在）。
- **唯一权威映射表 = 本节**（同端口铁律的 REGISTRY 先例：改它必须与工程改名同一提交）：

| 物理目录 | 角色 | ASCII 别名（junction） | 状态 |
|---|---|---|---|
| `C:\项目\老田的AI助手` | 零界主站（个人 OS，本仓） | `C:\aios` | 在用 |
| `C:\项目\zerobound-kg` | 图谱引擎（KG） | `C:\项目\老田的AI助手\zerobound-kg`（→物理目录，链式成 `C:\aios\zerobound-kg`） | 2026-10-17 由 `KGStockAnalysis` 物理改名（判例：引用链排查→junction 过渡→48 处归一同一批提交） |
| `C:\项目\portal` | 零界门户（8734 唯一入口） | — | 在用 |
| `C:\项目\zerobound-kg-snapshots` | KG 施工快照归档（KGStockAnalysis 时代 2026-10-02~05 审计证据：S0-S5/W/C/G3 批次 snapshot+manifest+GateA 签收，159 文件 12.5MB，保留） | — | 2026-10-17 由 `知识图谱` 改名（原名名不副实；零引用实证后改名） |
| `C:\项目\zhice-os-v2-windows-20260920` | 智策 OS v2 活部署（快照包无 git：5175 前端 + zhice-db 5446 + zhice-redis 6380 在跑） | — | **2026-10-17 拍板不动**：活系统+py312 editable 安装指向此路径+zhice.ps1 引用，改名断链风险>收益；目录名保留快照语义（含日期）以示其出身；引用此工程一律走现名或先立项改名（停服→改名→修引用→重装 editable→验证） |
| `C:\项目\new_tdx64` | **第三方外部软件**（通达信终端 TdxW.exe 完整安装，自 D 盘迁入；非自研工程，atom 体系不适用） | — | **不动**（2026-10-17 拍板）：data_catalog.yaml 白名单活引用 + 通达信升级器/配置记忆安装路径；手动启动的行情终端，按需开关 |
| `C:\项目\res-downloader-py` | 自研资源嗅探下载器 + 知识采集提炼（FastAPI/uvicorn；**归属 OSINT 产线采集层**，见 `osint_video/README.md`） | — | **2026-10-18 拍板归属 osint_video 开源情报产线（采集层）**：物理路径不动（8898/18899 活服务+自启计划任务+config save_dir/corpus_dir 自引用），主站集成走纯 HTTP（`POST /api/collect` 批量链接、`GET /api/corpus` 语料台账，对接 osint_link_task 状态机=T02 范围之一）；站点规则 378 条（金融全域+课程/论文/知识）；知识提炼落 `corpus/` 分桶+manifest.jsonl。**改名执行清单（开发收尾后）不变**：停 8898/18899 → Rename → 改 config.json save_dir/corpus_dir 自引用 → 重启验证 |
| `C:\项目\deliverables` | 平台级交付物集散地（投研报告/架构设计/原型/审计，跨工程产出，60 文件活跃） | — | 不动（通用语义名，活跃使用） |
| `C:\项目\py312` | 全局 Python 3.12.10 运行时（智策 OS editable 安装所在） | — | **环境基础设施，禁改名禁移动** |

- **不入册目录**：`.pytest_cache` / `.trae` / `.workbuddy` / `__pycache__` / `node_modules` 等运行态缓存，非工程。

- **数据根不在此表**：唯一 live 数据根 = `C:/项目/老田的AI助手/quant_backtest/data`（zerobound-kg/AGENTS.md §1 立章，KGDataRoot 空壳已废）。
- **改名纪律**：①先查引用链（计划任务/junction/硬编码路径，含 E:\ 历史死路径）；②物理改名 + 引用归一**同一批提交**；③可选旧名兼容 junction，必须在本表标注过期日期；④新代码一律引用物理名或注册表内别名，禁私建未注册 junction。

## 9. 敏捷开发与治理标准（ADGS v1.1 试行，2026-11-02 立章）

> 标准全文：`docs/standards/2026-11-02_敏捷开发与治理标准_v1.md`。本节只列**强条款**；与全文冲突时以全文为准。

1. **单一战略源**：战略/排期唯一权威 = `docs/plans/ROADMAP_v4_*.md`（发布后生效）；其余计划为子计划或 superseded，不得再自称"唯一"。
2. **工作项必带车道**：快速道（文档/测试/低风险组合）/ 标准道（组合/页面/通道/数据包）/ 治理道（内核/schema/口径/认证/外部依赖）/ L2 道（服务/计划任务/凭据/nginx/宿主机）/ 紧急道（先止血，24h 补证）。缺车道/DOR/DOD 不接单。
3. **WIP 上限（分阶段，见 ADGS v1.1 §七）**：（W0 口径 2026-11-02 A0：验收=安全回收+C/D 40 全登记+parked 18 全登记；worktree 数值目标顺延试点末≤15）W0 末 doing≤8/worktree≤20/parked≤8/寿命≤14d；试点末 ≤6/≤15/≤5/≤10d；季度目标 ≤5/≤10/≤5/≤7d。单人/单 Agent 并行写 ≤2；超限先收口，禁止新开。
4. **写域互斥**：任务单必须声明 `write_scope`；共享文件（AGENTS/ROADMAP/ADR/registry）同一时段只允许一个 owner 写。
5. **禁止共享仓清理**：**禁止在共享仓执行 `git clean -fdx` / `reset --hard`**（2026-11-02 两次清掉未跟踪 P0 文件的教训）；未跟踪交付物先提交或放 `deliverables/`。
6. **门禁不绕过**：快速道自动门、标准道组合门、治理道 `kernel_guard`+ADR 双签、L2 五要素；`DEVTOOL_MERGE_GATE=0` 仅回滚/紧急并留痕。
7. **无证据不算完成**：DOD 必须含证据包（命令+rc+产物+验收勾选+回滚+无残留+owner 签字）；`rc=0` 不等于有产出。
8. **merge train**：主窗口 16:30–17:00、次窗口 21:40–22:10；禁并期 09:15–15:05、17:10–18:40、20:00–20:20、21:20–21:40；文档/组合声明随时合并；窗口外只提交不合（紧急事故按 L2 处理）。
9. **度量**：每周报 lead time/WIP/失败率/逃逸缺陷/组合 Q 分布；每月组合账本审计；每季度 Kernel Review。
10. **例外分级（ADGS v1.1 §5.3）**：小例外（≤2 周、不触内核/安全/数据口径）ARCH1 单签 + 24h 报备；大例外（内核旁路/永久/跨系统接口/外部依赖）A0+ARCH1 双签；框架不适用走 ADR-017 例外（ADR+A0+临时 adapter+90 天回归）；紧急 hotfix 24h 内补 ADR/复盘。
