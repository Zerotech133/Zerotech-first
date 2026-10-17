# AGENTS.md — 老田的AI助手 · Agent 开发宪法（v1）

> 本文件是所有进入本仓库的 AI Agent（CodeBuddy / Claude Code / Cursor /
> WorkBuddy / 人类协作者）的**强制规范**。开工前必读，违反=评审门拒绝合并。

## 0. 平台身份

- 平台名：**零界**（老田的AI助手 / 个人 OS）· 口号「零界 · 一个人的 AI 公司」
- 主语言：中文注释与文档 · Windows 环境（PowerShell/Python，勿写含中文路径的 .ps1/.bat）
- 你（Agent）在本仓库的身份：**研发部 worker**。你的每项工作必须挂着一张任务单（见 §4）。

## 1. 端口铁律（不许互抢）

- **唯一权威注册表**：`tools/check_ports.py` 的 `REGISTRY` dict（24 口在册，K-STOCK-115 收编后）。
- 起新服务前：① 先查 REGISTRY——被占用即换；② 用 `python tools/devtask.py port <服务名>` 申请分配；③ 分配到后**必须**把端口+服务名写回 check_ports.py REGISTRY（同一提交内）。
- 一律绑 `127.0.0.1`，对外暴露走 nginx 反代（052 收敛既定）。
- 违规自查：`python tools/check_ports.py`（--strict 供 CI）。
- **入口唯一（K-STOCK-113 立宪，杜绝双口混乱）**：人类入口**只有一个 = 8734**（portproxy→nginx 同源合一）；8080 仅为容器/局域网反代链路，**文档、话术、页脚一律只写 8734，禁止再出现「8080/8734 同步生效」这类双口表述**。页内相对路径一律不带端口（走同源反代）。
- **开发侧全量回流（同上立宪）**：一切 IDE Agent 的开发动作必须自动进平台事件层 `portal/data/dev_events.jsonl`（tools/events.py 四事件：task_start/commit/blocked/merge_done）；post-commit 钩子已自动上报 commit——**禁止任何 Agent 关闭/绕过该钩子**；工单页 `http://127.0.0.1:8734/devops`。
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
- 完工合并必须过评审门：`python tools/devtask.py merge <TID>`（内置三守卫，门不过不合并）。

## 4. 任务单纪律

- 任务单登记在 `.worktrees/tasks.json`（devtask.py 维护）+ 重活同步 agent_platform tasks。
- 状态机：doing → review → done（merge 时自动翻转）。中途放弃标 aborted 留原因。
- 汇报：完工必须在提交信息带 `K-STOCK-xxx` 或工单号；A0 日报按此汇总。

## 5. 评审门（merge 自动执行，全绿才放行）

| 守卫 | 命令 | 防 |
|---|---|---|
| Mixin 方法集 | `python tools/_mixin_integrity.py --check` | portal_api 拆分丢方法 |
| 导航统一 | `python tools/unify_nav.py --check` | 手写导航/数据漂移 |
| 端口注册 | `python tools/check_ports.py --strict` | 抢端口/私起服务 |

## 6. 受训与记忆（怎么让 Agent 越用越准）

- **本文件是你的底层宪法**：每次会话先读，冲突时以本文件+工作区记忆为准。
- 项目长期记忆：`.workbuddy/memory/MEMORY.md`（工作区级）——踩坑后必须回写一句「教训」，防止下一个 Agent 重踩。
- 用户级铁律（回收站删除/长跑落盘/补丁断言等）见用户记忆 MEMORY.md，跨仓库有效。
- 判例文档：`docs/audit/`（审计）与 `docs/plans/`（拍板）——做设计决策前先 grep 有没有先例，**有判例循判例，无判例立新档**。
- 飞书侧新能力选路必循：`docs/plans/2026-10-14_飞书自建底座与Lark插件分工边界_K-STOCK-395.md`（按「谁在场+是否计量」划界；插件禁生产推送/禁常驻）。

## 7. 红线（历史踩过、代价高的）

- Windows 删除一律进回收站（Python SHFileOperationW），禁 rm/del；
- 长跑命令输出必须落文件；
- portal_api.py 是 23k 行巨石——**只改不扩**，新 handler 一律新文件（P4 批 0 后启用拒收闸）；
- 替换补丁前断言锚点 count==1，替换后断言新串存在；
- 杀进程前先辨认是不是线上服务（按启动时间+端口归属）；
- **修复写盘 ≠ 生效（2026-10-16 P0 实锤）**：改常驻服务代码后必须杀旧进程换血——「缺则拉」型守护器（start_jev_stack.ps1 类）不认 mtime，旧进程会带旧码跑到天荒地老；验证=新进程出生时间 > 文件 mtime + 日志出现新分支特征行；
- **S4U/Session0 进程只信端口判活（2026-10-16 双实证）**：CIM/Win32_Process 枚举漏列+CommandLine 全 null；找 PID 用 `Get-NetTCPConnection -LocalPort`，杀进程要 UAC taskkill（`Start-Process cmd -Verb RunAs`，降权 token Access denied）。
