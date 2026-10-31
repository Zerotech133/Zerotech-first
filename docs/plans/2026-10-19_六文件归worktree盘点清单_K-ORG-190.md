# 六文件归 worktree 盘点清单（K-ORG-190 · C 路 c-inventory）
> STATUS: unspecified（owner: TBD；2026-11-07 前补状态或标 superseded）



- 盘点时间：2026-10-19
- 盘点人：c-inventory（C 路工位）
- 纪律：**全程只读**，零移动、零重命名、零删除、零 git add/commit
- 背景：早期会话（dev-inventory，2026-10-18）曾产出《6文件归worktree盘点清单》（今存于 `agent_platform/docs/audit/2026-10-18_6文件归worktree盘点清单.md`）。**注意：该早期清单的"6文件"实指 `C:\项目\` 根目录 6 个散件（迁移包zip等），并非 worktree 归属问题**。本轮按新指令重新盘点，两条线都覆盖：①C:\项目 根 6 文件现状复核；②主仓内疑似"应归某 worktree"的散落文件盘点。

---

## 一、worktree 全录（git worktree list，共 12 个注册 worktree）

| # | worktree | 分支 | 领先 master | 最近提交主题 | 用途判读 |
|---|---|---|---|---|---|
| 1 | 主仓（.） | master | — | 5b66301a ops(escort) 周一护航操作单 | 主工作区 |
| 2 | .worktrees/K-INTEL-20261018-KG-SOURCES | task/K-INTEL-20261018-KG-SOURCES | 0 | RC-1 S1: 第一批 6 策略 Manifest + 收编档案（batch1-manifest 工位） | 情报·KG源收编 |
| 3 | .worktrees/K-INTEL-20261018-PORTS | task/K-INTEL-20261018-PORTS | 0 | 护航准备：周一门3观察点只读巡检+操作单 | 情报·港口护航 |
| 4 | .worktrees/K-INTEL-20261018-RO | task/K-INTEL-20261018-RO | 0 | docs(audit): 网关 192.168.5.155 不可达周一预案（HH 路） | 情报·只读预案 |
| 5 | .worktrees/K-INTEL-20261018-SOURCES | task/K-INTEL-20261018-SOURCES | 0 | docs(xq_report): 日报 v1.6 提取器实弹前只读预演走查 | 情报·雪球日报源 |
| 6 | .worktrees/K-INTEL-20261018-WORKBENCH | task/K-INTEL-20261018-WORKBENCH | 0 | add evidence-to-target drill-down | 情报·工作台 |
| 7 | .worktrees/K-OPS-002 | task/K-OPS-002 | 0 | M5 规划定稿：数据治理与稳定观察线 | 运维·M5规划 |
| 8 | .worktrees/K-ORG-180 | task/K-ORG-180 | 1 | K-ORG-180: 修复 Hermes 总控入口、鉴权及健康状态 | 组织·Hermes修复 |
| 9 | .worktrees/K-ORG-203 | task/K-ORG-203 | 0 | K-ORG-203 文档引用视图：决策附带飞书文档链接 | 组织·文档引用视图 |
| 10 | .worktrees/K-ORG-204 | task/K-ORG-204 | 0 | K-ORG-204 飞书 Agent 中台出口改造 | 组织·飞书出口 |
| 11 | .worktrees/K-QUANT-001 | task/K-QUANT-001 | 0 | feat(tools): 8001 策略预警影子服务实体恢复 K-DASH-002 | 量化·预警影子服务 |
| 12-13 | .worktrees/legion-1019093011 / legion-1019093110 | task/legion-1019093011 / -3110 | 0（两者同 HEAD a1f2b618） | K-ORG-190 第二批次状态回填（aim-proj / tool-memo 工位） | 军团·K-ORG-190 双工位（内容相同，疑似双开冗余） |

**附带发现（重要）**：`.worktrees/` 目录下实际存在 **17 个子目录**，其中 5 个**未注册**为 git worktree（`git worktree list` 无此条目，其分支均停在 master、ahead=0）：
`K-ORG-068/`、`K-ORG-134/`、`K-ORG-184/`、`K-ORG-186/`、`K-PUSH-012/`。
判读：历史任务的残留空壳目录（内容与 master 一致）。**待裁决**：确认无在途工作后 `git worktree prune` + 目录清理（本轮不动作）。

---

## 二、C:\项目 根目录"原 6 文件"现状复核

| # | 文件 | 2026-10-18 建议 | 本轮现状（2026-10-19） | 本轮口径 |
|---|---|---|---|---|
| 1 | 老田的AI助手_迁移包_20261015.zip（43.94GB） | 归档冷存储 | **已不在根目录**（已移走，去向待确认登记） | 关闭候选，建议台账登记去向 |
| 2 | 存量收编盘点清单_2026-10-17.md（32KB） | 副本入 audit/ | **仍在根目录** | 待裁决：副本入库+原件归档 |
| 3 | 复盘与代码审计_2026-10-17.md（8.8KB） | 副本入 audit/ | **仍在根目录** | 同上 |
| 4 | 研发方案与研发计划_2026-10-17.md（4.1KB） | 作历史版本归档 | **仍在根目录** | 同上（已被 10-18 版取代） |
| 5 | P0-4判读席位资源包调查报告_2026-10-17.md（4.8KB） | 副本入 audit/ | **仍在根目录**（方案A/B仍未拍板） | 同上 |
| 6 | llm_client.py（3.6KB） | 保留/收编/删除三选一 | **已不在根目录**（备份存 `C:\项目\backups\2026-10-18_llm_client\`） | 处置已发生，建议台账补登记裁决结果 |

另：根目录 10-18 起新增散件仍在持续产生（`老田的AI助手最优解决方案与研发计划_2026-10-18.md` 10-19 03:58、`情报工厂子项目规划_20261018.md` 10-19 04:18、`_s2_recon_ref.md` 10-19 05:09 等，另有多枚 .html 原型），已转《散落文档盘点与直落新规执行草案》处理（见姊妹文档）。

---

## 三、主仓内"应归 worktree"散落文件候选（本轮重点）

### 3.1 主题定位

主仓工作区有一组 untracked 新文件，围绕两个主题：
- **A4 对比主题**（选股 Agent M0 计划 A4 并轨：只读探针的可比性判定）；
- **Agent→量化桥接 / 模拟市场主题**（Agent 平台策略产出到 quant_backtest 标准信号的适配器）。

关键事实：**已注册的 12 个 worktree 内均不存在这些同名文件或同主题实现**（逐一 grep 验证）；即它们不是"从某 worktree 掉进主仓的"，而是**未开 worktree、直接落在主仓的进行中工作**。
关键佐证：`docs/plans/2026-10-19_选股策略独立化与选股Agent_M0计划.md` §八（L189-190）已将 `a4_comparison_contract.py`、`a4_comparison_cli.py` 载为该计划的正式产出；同仓已跟踪文件 `quant_backtest/strategy_layer/a4_readonly_probe.py`（本轮还在被修改）是同主题先例——**主仓直落是既有惯例，M0 计划本身未要求 worktree**。

### 3.2 六个主候选逐文件明细

| # | 文件（均 untracked） | 主题 | 依赖关系 | 归属建议（仅列项，不下裁决） |
|---|---|---|---|---|
| 1 | `quant_backtest/strategy_layer/a4_comparison_cli.py`（1.7KB，10-19 10:44） | A4 对比·CLI 入口 | import a4_readonly_probe（已跟踪）+ a4_comparison_contract（#2） | **倾向留主仓收编**：与已跟踪的 a4_readonly_probe.py 同区同主题；备选：若 M0-A4 并轨要走 worktree 流程，则开 task/K-QUANT-A4 类 worktree 整组搬入。待裁决 |
| 2 | `quant_backtest/strategy_layer/a4_comparison_contract.py`（8.7KB，10-19 11:33） | A4 对比·可比性判定合约 | 纯内存判定，无外依赖 | 同 #1，与 #1/#5/#6 同进退 |
| 3 | `quant_backtest/strategy_layer/adapters/agent_bridge.py` | Agent→量化信号桥接 | 只调 Agent 只读 propose/Registry.run；依赖 agent_platform/contracts/sim_market.py 体系 | **待裁决**：跨 agent_platform 与 quant_backtest 两域，无现成 worktree 主题匹配（K-QUANT-001 是预警影子服务，非本主题）。建议与 #4/#7/#8 及 agent_platform 侧 sim_market 散件整组统一裁决 |
| 4 | `quant_backtest/strategy_layer/adapters/agent_sim_market.py` | Verdict→模拟订单适配器 | 依赖 agent_platform.contracts.sim_market（untracked） | 同 #3 |
| 5 | `quant_backtest/strategy_layer/tests/test_a4_comparison_cli.py` | #1 的测试 | — | 随 #1 |
| 6 | `quant_backtest/strategy_layer/tests/test_a4_comparison_contract.py` | #2 的测试 | — | 随 #2 |

### 3.3 同主题连带散件（6 文件之外的发现，建议一并纳入裁决）

| 文件（均 untracked） | 说明 |
|---|---|
| `quant_backtest/strategy_layer/tests/test_agent_bridge.py` | #3 的测试 |
| `quant_backtest/strategy_layer/tests/test_agent_sim_market.py` | #4 的测试 |
| `agent_platform/contracts/sim_market.py` | SIM_MARKET 只读数据契约，#4 的依赖 |
| `agent_platform/adapters/sim_market_readonly.py` | SIM_MARKET 只读策略适配器 |
| `agent_platform/engine/shadow_strategy_runner.py`、`engine/strategy_*.py`（7枚）、`tests/test_shadow_strategy_runner.py` 等 | 引擎侧策略独立化主题群（M0 计划关联），量大约 20+ 枚 untracked |

> 提示：这一整片 untracked 区是**同一个进行中的大主题（选股策略独立化 / M0）**，如要走 worktree 化，宜整组定去留，不宜 6 件单独裁。

### 3.4 判定矩阵速览

| 组 | 文件数 | 主题 | 现成 worktree 匹配 | 建议 |
|---|---|---|---|---|
| A4 对比组 | 4（#1#2#5#6） | M0-A4 并轨 | 无 | 留主仓收编（有 a4_readonly_probe 先例）或整组开新 worktree——**待裁决** |
| 桥接/模拟市场组 | 2 主候选（#3#4）+连带 2 tests | Agent→量化桥接 | 无 | 与 agent_platform 侧 sim_market 散件**整组待裁决** |
| 独立化引擎群 | 20+ untracked | M0 策略独立化 | 无 | 本轮只登记，范围超出 6 文件，建议另立专项盘点 |

---

## 四、待老田裁决表

| # | 事项 | 代理建议 | 裁决 |
|---|---|---|---|
| 1 | A4 对比 4 件去留 | 倾向主仓收编（与 a4_readonly_probe 同区）；否决理由需明确是否强制 worktree 流程 | ＿＿ |
| 2 | 桥接/模拟市场组（含 agent_platform 侧连带）去留 | 整组一次裁决；先明确该主题是否立 worktree | ＿＿ |
| 3 | .worktrees/ 下 5 个残留目录（K-ORG-068/134/184/186、K-PUSH-012） | 确认无在途工作后 prune+清理 | ＿＿ |
| 4 | legion-1019093011/3110 双开冗余 worktree | 确认两工位是否均已收官，收官后合并清理 | ＿＿ |
| 5 | 原 6 文件中仍在根目录的 4 个 md（C:\项目 根） | 延续 10-18 建议：副本入库+原件归档；llm_client.py 已移走、zip 已移走请补台账 | ＿＿ |

---

*盘点完，零改动零移动零删除。仅新建本清单。*
