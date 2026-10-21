# GOV-MIN-003｜E 盘旧路径逐文件迁移矩阵（2026-10-18）

> 工单依据：`C:\项目\老田的AI助手\docs\governance\next-minimal-workorders-2026-10-17.md:41-48`。本文件为**静态代码搜索与人工审阅**，未运行任何脚本、未读取账本内容、未移动/删除/替换路径，也未变更服务或运行态。原工单约定交付文件名为 `...2026-10-17.md`，本次派单指定 `...2026-10-18.md`，故采用派单文件名，不冒充 10 月 17 日现场快照。
>
> 检索范围：`C:\项目\老田的AI助手` 内业务 `.py/.ps1` 源码；检索大小写不敏感的 `E:\`、`E:/`，并人工剔除非路径字符串命中。下表采用**审阅条目**口径：每条定位到绝对源码文件和具体行号；连续两行使用同一路径的判断与插入合并为一个逻辑位点（例如 `:1740-1741`）；archive 的多个独立行号亦暂合并展示，不能把条目数视为逐字面量出现次数。`E:\老田的AI助手` 的可观察仓库现址为 `C:\项目\老田的AI助手`；只有源代码已明确使用 `C:\aios` 或相对本文件推导时，才在替代栏记录为“已有代码候选”，绝不据此宣称目标文件、junction 或服务已验证。未审阅运行时任务注册表/调用链，凡需此证据均为 `BLOCKED`。
>
> 本矩阵列出 **124 条需审阅路径条目**（A–E 120 条执行路径/候选盘引用；F 4 条应保留的历史前缀兼容常量），另在 A 表列 **2 条非执行示例** 以避免误判。分段可复算：A 18 + B 30 + C 29 + D 31 + E 12 = 120 条执行路径表行，F 4 条兼容常量表行；合计 124 条**表格审阅条目**，并非逐个源码字面量出现次数。连续成员检查/插入两行、archive 多行定位在表中合并展示；**本表是非穷尽样本，不代表全仓硬编码总数**。未读取账本内容、parquet、历史证据大文件、vendored JS。风险：`H`=生产写入/删除/部署/核心服务；`M`=功能降级或读取缺失；`L`=实验/诊断脚本。结论 `迁移` 仅表示**建议后续走批准流程**，不是批准执行；`保留` 表示历史输入兼容语义；`待证据 BLOCKED` 表示替代位置或调用域尚不能证明。所有迁移建议目前实施状态一律 `BLOCKED`，须先确认 owner、调用方、只读/写边界、离线测试、生产探测与人工批准。

## 统一回滚与禁止动作

- 回滚代号 `R`：未来若获批准逐文件迁移，保留原文件/配置版本及变更前证据；故障恢复原文件或原环境变量，重新核验调用方读写，**不删旧路径或历史证据**。涉及数据写者须先隔离写入窗口、备份目标、验证回放。当前矩阵唯一回滚为删除本新增文档。
- `R+`：高危写路径还必须记录变更前目录/文件哈希及写者归属并针对写入侧设计独立回滚；绝不可用“恢复源代码”替代对已写数据的回滚。任何删除、数据库变更、任务注册、服务重启均不在本工单范围。
- 替代栏 `—` 意味着**无可证明替代路径**，禁止机械拼接新盘符。`候选` 不等于路径存在性、同一数据集或已获生产许可。

## A. 生产入口、门户、运维与共享源

| 唯一定位：绝对源码文件:行 | 旧路径及语义 | 调用方 / 运行域（仅静态可见） | 替代路径证据 | 风险 / 回滚 | 结论 |
|---|---|---|---|---|---|
| `C:\项目\老田的AI助手\portal\api_backtest.py:1740-1741` | `e:\老田的AI助手\quant_backtest`，一次 `sys.path` 成员检查+一次插入后导入 `chanlun_market`，读代码 | `/bt/m/*` 详情页面服务进程 | `—` | M/R | 迁移，实施 BLOCKED |
| `C:\项目\老田的AI助手\portal\api_backtest.py:1759` | `e:\老田的AI助手\quant_backtest\data`，读 `chanlun_pool*.jsonl` | 同上，页面池历史；异常被捕获可能静默缺失 | `—` | M/R | 待证据 BLOCKED：数据集与源根 |
| `C:\项目\老田的AI助手\portal\api_chanlun_pages.py:2155-2156` | `e:\老田的AI助手\quant_backtest`，`sys.path` 成员检查+插入导入路径 | 门户缠论页面 | `—` | M/R | 迁移，实施 BLOCKED |
| `C:\项目\老田的AI助手\portal\api_chanlun_pages.py:2235-2236` | 同上，`sys.path` 成员检查+插入导入路径 | 门户缠论页面另一分支 | `—` | M/R | 迁移，实施 BLOCKED |
| `C:\项目\老田的AI助手\portal\api_chanlun_pages.py:2377-2378` | 同上，`sys.path` 成员检查+插入导入路径 | 门户缠论页面第三分支 | `—` | M/R | 迁移，实施 BLOCKED |
| `C:\项目\老田的AI助手\portal\portal_report.py:145` | `e:\老田的AI助手\quant_backtest`，导入 `chanlun_card` | 门户报告/止盈止损展示 | `—` | M/R | 迁移，实施 BLOCKED |
| `C:\项目\老田的AI助手\portal\portal_report.py:357` | 同上，导入 `chanlun_card` | 门户报告拒绝买入解释 | `—` | M/R | 迁移，实施 BLOCKED |
| `C:\项目\老田的AI助手\portal\portal_report.py:925` | 同上，导入 `report_card_img` | 门户飞书图证卡；失败降级无图 | `—` | M/R | 迁移，实施 BLOCKED |
| `C:\项目\老田的AI助手\portal\portal_report.py:945` | 同上，导入 `subscribe_action` | 门户订阅按钮 | `—` | M/R | 迁移，实施 BLOCKED |
| `C:\项目\老田的AI助手\portal\api_misc.py:2046` | `E:\老田的AI助手\k_stock\data\plan_review`，glob 读取审批单 | 门户模拟交易 AI 值班经理 | `—` | M/R | 待证据 BLOCKED：审批单事实根 |
| `C:\项目\老田的AI助手\portal\api_misc.py:2068` | `E:\老田的AI助手\k_stock`，`sys.path` 导入 `paper.portal_status` | 门户模拟交易 AI 值班经理 | `—` | M/R | 迁移，实施 BLOCKED |
| `C:\项目\老田的AI助手\portal\api_portfolio.py:1748` | `E:\老田的AI助手\k_stock\data`，glob 读双刀产物 | 门户交易台动态块；同方法另读 SQLite，但**本审查不读账本** | `—` | M/R | 待证据 BLOCKED：数据同源性 |
| `C:\项目\老田的AI助手\portal\qd_replay_verify.py:17` | `E:\老田的AI助手\agent_platform\engine`，导入搜索路径 | 独立回放核验脚本 | `—` | M/R | 待证据 BLOCKED：Agent Platform 所属独立树 |
| `C:\项目\老田的AI助手\portal\qd_replay_verify.py:20` | `E:\老田的AI助手\quant_backtest\data\ashare`，读行情 | 同一回放核验脚本 | `—` | M/R | 待证据 BLOCKED：数据根 |
| `C:\项目\老田的AI助手\portal\qd_replay_verify.py:109` | `E:\老田的AI助手\portal\qd_replay_verify.json`，写核验报告 | 同一回放核验脚本 | `—` | M/R+ | 迁移，实施 BLOCKED |
| `C:\项目\老田的AI助手\portal\agent_auto_commit.py:29` | `E:/老田的AI助手`，Git 工作树根；`git add -A`/commit、日志/状态写入 | 定时 Agent 代码入库作业；**不得执行** | `—` | H/R+ | 待证据 BLOCKED：调度、仓库归属与防误提交 |
| `C:\项目\老田的AI助手\tools\disk_ttl_cleanup.py:29` | `E:/老田的AI助手`，清理目标仓根；`--execute` 可删除过期文件、写日志 | 人工 CLI 磁盘清理 | `—` | H/R+ | 待证据 BLOCKED：删除目标与安全边界 |
| `C:\项目\老田的AI助手\tools\rebuild_academy_source_snapshot.py:296` | `E:\老田的AI助手`，`--root` 默认值，读取源并重建快照 | 人工 CLI 快照重建 | `—` | H/R+ | 待证据 BLOCKED：源/目标与写入窗口 |
| `C:\项目\老田的AI助手\portal\start_portal_nginx.ps1:1` | `E:\老田的AI助手\portal` 只存在注释，**非执行硬编码** | Docker nginx 启动器 | 源码 `:8` 已用 `C:\项目\老田的AI助手\portal` 挂载 | L/R | 保留历史注记；不纳入 124 条审阅路径 |
| `C:\项目\老田的AI助手\portal\km_feishu_hub.py:614` | UI placeholder 展示旧 E 路径，不是运行硬编码 | 门户用户输入提示 | `—` | L/R | 待产品文案确认；不纳入 124 条审阅路径 |

## B. KG/行情、回测与研究脚本

| 唯一定位：绝对源码文件:行 | 旧路径及语义 | 调用方 / 运行域（仅静态可见） | 替代路径证据 | 风险 / 回滚 | 结论 |
|---|---|---|---|---|---|
| `C:\项目\老田的AI助手\quant_backtest\backtest_engine_v2.py:28` | `E:/老田的AI助手`，派生行情、池、逐日账本、回测输出 `:29-33`，**读+写** | 回测引擎；门户调用方可能 `bt2_tune_compare.py` | `bt2_tune_compare.py:32-39` 存在从脚本位置推导站根的运行时自愈，**仅此调用路径** | H/R+ | 迁移，实施 BLOCKED：其他入口不受自愈保护 |
| `C:\项目\老田的AI助手\quant_backtest\build_bt2_prototype.py:5` | `E:/老田的AI助手`，派生回测数据/输出根 | 原型脚本 CLI | `—` | M/R+ | 待证据 BLOCKED：所有派生读写 |
| `C:\项目\老田的AI助手\quant_backtest\combo_e2e_run.py:20` | 同上，端到端输入与输出根 | 回测组合实验 CLI | `—` | M/R+ | 待证据 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\chanlun_daily_ledger.py:19` | 同上，缠论逐日账本**路径变量**（未读账本内容） | 缠论日历/回测研究 | `—` | H/R+ | 待证据 BLOCKED：写入边界 |
| `C:\项目\老田的AI助手\quant_backtest\chanlun_pit_select.py:16` | 同上，研究输入/输出根 | 缠论 PIT 研究 CLI | `—` | M/R+ | 待证据 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\chanlun_month_replay.py:13` | 同上，月回放根 | 回测研究 CLI | `—` | M/R+ | 待证据 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\chanlun_month_replay_pit.py:9` | 同上，PIT 月回放根 | 回测研究 CLI | `—` | M/R+ | 待证据 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\chanlun_topk_backtest.py:18` | 同上，TopK 回测根 | 回测研究 CLI | `—` | M/R+ | 待证据 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\chanlun_rotation_backtest.py:17` | 同上，轮动回测根 | 回测研究 CLI | `—` | M/R+ | 待证据 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\chanlun_rotation_v2.py:17` | 同上，轮动 v2 根 | 回测研究 CLI | `—` | M/R+ | 待证据 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\chanlun_rotation_v3.py:16` | 同上，轮动 v3 根 | 回测研究 CLI | `—` | M/R+ | 待证据 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\chanlun_rotation_v4.py:16` | 同上，轮动 v4 根 | 回测研究 CLI | `—` | M/R+ | 待证据 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\chanlun_rotation_v5.py:18` | 同上，轮动 v5 根 | 回测研究 CLI | `—` | M/R+ | 待证据 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\chanlun_rotation_v6.py:27` | 同上，轮动 v6 根 | 回测研究 CLI | `—` | M/R+ | 待证据 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\chanlun_rotation_v6c.py:11` | 同上，轮动 v6c 根 | 回测研究 CLI | `—` | M/R+ | 待证据 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\tools\research\chanlun_rotation_v7.py:17` | 同上，轮动 v7 根 | research 实验 CLI | `—` | L/R+ | 待证据 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\tools\research\chanlun_beichi_grid.py:33` | 同上，背驰网格输入输出根 | research 实验 CLI | `—` | L/R+ | 待证据 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\ops_signal_today.py:15` | 同上，今日信号根 | 量化运营 CLI/调度未知 | `—` | M/R+ | 待证据 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\shadow_weekly_report.py:46` | 同上，门户/量化/Jev 输入与 reports 输出；`:15-16` 另有**文档示例命令** | 影子周报 CLI，尚未证明计划任务实际接入 | `—` | M/R+ | 待证据 BLOCKED：实际挂载 |
| `C:\项目\老田的AI助手\quant_backtest\regime_timeline.py:31` | `E:/老田的AI助手/agent_platform/engine`，导入路径 | regime 时间轴研究 CLI | `—` | M/R | 待证据 BLOCKED：独立平台树归属 |
| `C:\项目\老田的AI助手\quant_backtest\build_history_backfill_plan.py:9` | `E:\老田的AI助手\quant_backtest\data\tushare\trade_cal`，交易日历读 | 回填计划构造器 | `—` | M/R | 待证据 BLOCKED：日历事实根 |
| `C:\项目\老田的AI助手\quant_backtest\run_annual_history_backfill.py:117` | 同上，交易日历读 | 年度回填作业 | `—` | H/R+ | 待证据 BLOCKED：写者+日历 |
| `C:\项目\老田的AI助手\quant_backtest\run_recent_history_backfill.py:7` | 同上，交易日历读 | 近期回填作业 | `—` | H/R+ | 待证据 BLOCKED：写者+日历 |
| `C:\项目\老田的AI助手\quant_backtest\corpus_incr_sync.py:69` | `E:\老田的AI助手\config\tushare_token.txt`，令牌读取候选 | 语料增量同步 CLI | 同一代码另有 `C:\Temp\tushare_token.txt` fallback，非已批准替代 | H/R | 待证据 BLOCKED：凭据 owner/密钥权限；不展示内容 |
| `C:\项目\老田的AI助手\quant_backtest\limit_list_fix_sync.py:60` | 同上，令牌读取候选 | 涨停数据同步 CLI | 同上 | H/R | 待证据 BLOCKED：凭据 owner |
| `C:\项目\老田的AI助手\quant_backtest\market_scan.py:20` | `E:/老田的AI助手/quant_backtest`，只有无 `__file__` 时的 fallback | 市场扫描脚本 | 同行优先 `os.path.dirname(os.path.abspath(__file__))` | L/R | 保留备用兼容口径，未来移除须先验证交互环境 |
| `C:\项目\老田的AI助手\quant_backtest\test_datamart_date_regressions.py:11` | `E:/老田的AI助手/quant_backtest`，测试工程根读 | 日期回归测试 | `—` | L/R | 迁移测试夹具，实施 BLOCKED |
| `C:\项目\老田的AI助手\k_stock\kstock\scoring.py:64` | `E:\老田的AI助手\菜鸟\thunder_model\thunder_model.sql`，SQL 因子配置读 | 股票评分模块 | `—` | M/R | 待证据 BLOCKED：菜鸟目录所属仓 |
| `C:\项目\老田的AI助手\Hermes\cowbridge\smoke_webauth.py:25` | `E:\老田的AI助手\CowAgent\config.json`，配置读 | CowAgent WebAuth smoke 脚本 | `—` | M/R | 待证据 BLOCKED：CowAgent 属主/配置位置 |
| `C:\项目\老田的AI助手\quant_backtest\tools\d1_s1_precheck.py:42` | `E:\老田的AI助手`，预检仓根（可能读/生成报告） | D1/S1 独立预检 CLI | `—` | M/R+ | 待证据 BLOCKED：写入范围 |

## C. 单次诊断、修复、采集与历史 archive（**不得因“脚本”二字直接运行**）

| 唯一定位：绝对源码文件:行 | 旧路径及语义 | 调用方 / 运行域（仅静态可见） | 替代路径证据 | 风险 / 回滚 | 结论 |
|---|---|---|---|---|---|
| `C:\项目\老田的AI助手\quant_backtest\_317_link_summary.py:5` | `E:/老田的AI助手/quant_backtest/data/bt2/`，读历史回测 | 317 诊断 CLI | `—` | L/R | 待证据 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\_321_probe.py:6` | `E:/老田的AI助手/quant_backtest/data/tdx/_manifest.json`，读 manifest | 321 诊断 CLI | `—` | L/R | 待证据 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\_323_probe_caliber.py:8` | `E:/老田的AI助手/quant_backtest/data/tdx/ts_rt_min/*.parquet`，glob 读 | 323 探针 CLI | `—` | L/R | 待证据 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\_323_diag.py:8` | `E:\老田的AI助手`，`sys.path` 导入 | 323 诊断 CLI | `—` | L/R | 迁移，实施 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\_323_diag.py:11` | `E:\老田的AI助手\quant_backtest\data\tdx\dknife_snap\dknife_snap.parquet`，读快照 | 323 诊断 CLI | `—` | L/R | 待证据 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\_323_sig_verify.py:6` | `E:\老田的AI助手\quant_backtest`，导入路径 | 323 验证 CLI | `—` | L/R | 迁移，实施 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\_331b_findsync.py:39` | 同上，导入路径 | 331b 诊断 CLI | `—` | L/R | 迁移，实施 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\_audit_datasource.py:16` | `E:/老田的AI助手/quant_backtest/data`，数据源检查 | 审计 CLI | `—` | L/R | 待证据 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\_audit_probe2.py:6` | 同上，诊断数据读 | 审计探针 CLI | `—` | L/R | 待证据 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\_audit_probe3.py:6` | 同上，诊断数据读 | 审计探针 CLI | `—` | L/R | 待证据 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\_audit_probe3.py:46` | `E:/老田的AI助手/k_stock/data/poc_adapter_chanlun_20261012.json`，池读 | 审计探针 CLI | `—` | L/R | 待证据 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\_k260_recon.py:6` | `E:\老田的AI助手\quant_backtest\data\semantica\notice_llm.parquet`，读候选 | K260 对账 CLI | `—` | L/R | 待证据 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\_k260_recon.py:7` | `E:\老田的AI助手\quant_backtest\data\semantica\events.jsonl`，读事件 | K260 对账 CLI | `—` | L/R | 待证据 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\_patch_bt2view_buyfilter.py:5` | `E:/老田的AI助手/portal/bt2_view.html`，读+**改写门户 HTML** | 一次性页面补丁 CLI | `—` | H/R+ | 待证据 BLOCKED：页面 owner 与版本 |
| `C:\项目\老田的AI助手\quant_backtest\_patch_bt2view_default3.py:3` | 同上，读+改写门户 HTML | 一次性页面补丁 CLI | `—` | H/R+ | 待证据 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\_patch_bt2view_daydetail.py:3` | 同上，读+改写门户 HTML | 一次性页面补丁 CLI | `—` | H/R+ | 待证据 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\_patch_bt2view_universe.py:4` | 同上，读+改写门户 HTML | 一次性页面补丁 CLI | `—` | H/R+ | 待证据 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\_probe3_sig_vs_exec.py:12` | `E:/老田的AI助手`，信号/执行诊断输入根 | 回测探针 CLI | `—` | L/R | 待证据 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\_probe4_runup_filter.py:11` | 同上，runup 诊断根 | 回测探针 CLI | `—` | L/R | 待证据 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\_probe5_ew_rebalance.py:12` | 同上，调仓诊断根 | 回测探针 CLI | `—` | L/R | 待证据 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\_probe6_1y_window.py:13` | 同上，一年窗诊断根 | 回测探针 CLI | `—` | L/R | 待证据 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\_probe6b_pool_hold.py:6` | 同上，池持仓诊断根 | 回测探针 CLI | `—` | L/R | 待证据 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\_probe_lag.py:6` | 同上，滞后诊断根 | 回测探针 CLI | `—` | L/R | 待证据 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\_probe_lag2.py:6` | 同上，滞后诊断根 v2 | 回测探针 CLI | `—` | L/R | 待证据 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\_verify_buyfilter.py:8` | 同上，买入过滤校验根 | 回测验证 CLI | `—` | L/R | 待证据 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\_test_buffer.py:4` | `E:/老田的AI助手/quant_backtest`，测试导入路径 | 临时实验测试 CLI | `—` | L/R | 迁移，实施 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\_test_buffer2.py:4` | 同上，测试导入路径 | 临时实验测试 CLI | `—` | L/R | 迁移，实施 BLOCKED |
| `C:\项目\老田的AI助手\portal\_smoke_algo_market.py:5` | `E:/老田的AI助手/portal`，测试导入路径 | 门户算法市场 smoke | `—` | L/R | 迁移，实施 BLOCKED |
| `C:\项目\老田的AI助手\portal\_smoke_algo_market.py:6` | 同上，进程 `chdir` | 同一 smoke；会改变后续相对路径 | `—` | M/R | 迁移，实施 BLOCKED |

## D. Semantica/K266/知识图谱专项及 archive

| 唯一定位：绝对源码文件:行 | 旧路径及语义 | 调用方 / 运行域（仅静态可见） | 替代路径证据 | 风险 / 回滚 | 结论 |
|---|---|---|---|---|---|
| `C:\项目\老田的AI助手\quant_backtest\k265_phase0.py:17` | `e:\老田的AI助手\quant_backtest`，模块导入 | K265 阶段 0 CLI | `—` | M/R | 迁移，实施 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\k265_phase0.py:20` | `...\data\semantica\k265_phase0_shadow.jsonl`，**写影子** | 同上 | `—` | H/R+ | 待证据 BLOCKED：影子数据 owner |
| `C:\项目\老田的AI助手\quant_backtest\k265_phase0.py:110` | `...\data\semantica\notice_llm.parquet`，读候选 | 同上 | `—` | M/R | 待证据 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\k265_phase1.py:17` | `e:\老田的AI助手\quant_backtest`，模块导入 | K265 阶段 1 CLI | `—` | M/R | 迁移，实施 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\k265_phase1.py:20` | `...\data\semantica\k265_phase1_shadow.jsonl`，**写影子** | 同上 | `—` | H/R+ | 待证据 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\k265_phase1_commit.py:16` | `e:\老田的AI助手\quant_backtest`，模块导入 | K265 commit CLI；后续可写 KG | `—` | H/R+ | 待证据 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\k265_phase1_commit.py:19` | `...\data\semantica\k265_phase1_shadow.jsonl`，读影子 | 同上 | `—` | H/R+ | 待证据 BLOCKED：KG 写入须独立批准 |
| `C:\项目\老田的AI助手\quant_backtest\k265_dir_audit.py:16` | 同一 phase1 影子，读目录审计 | K265 审计 CLI | `—` | L/R | 待证据 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\k265_band_rejudge.py:11` | `e:\老田的AI助手\quant_backtest`，导入路径 | K265 重判 CLI | `—` | M/R+ | 迁移，实施 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\k265_rj_cleanup.py:7` | 同上，导入路径 | K265 rj 清理 CLI；`--go` 可**删 KG 图关系** | `—` | H/R+ | 待证据 BLOCKED：绝不可试跑 |
| `C:\项目\老田的AI助手\quant_backtest\k265_rj_cleanup.py:11` | `...\data\semantica\k265_phase1_shadow.jsonl`，读白名单候选 | 同上 | `—` | H/R+ | 待证据 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\k265_rj_cleanup.py:12` | `...\data\semantica\k265_rj_exclude.txt`，读剔除表 | 同上 | `—` | H/R+ | 待证据 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\k265_rj_close.py:6` | `e:\老田的AI助手\quant_backtest`，导入路径 | K265 收口 CLI | `—` | H/R+ | 待证据 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\k265_rj_close.py:10` | `...\data\semantica\k265_phase1_shadow.jsonl`，读影子 | 同上 | `—` | H/R+ | 待证据 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\k265_rj_close.py:58` | `...\data\semantica\k265_ingest_pending.txt`，写待入图清单 | 同上 | `—` | H/R+ | 待证据 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\archive\k265_phase0.py:15,18,110` | 三个独立出现位置：`quant_backtest` 导入、phase0 影子写、`notice_llm.parquet` 读；**archive 版本** | 历史脚本，是否仍有调度未证 | `—` | H/R+ | 待证据 BLOCKED：勿当成现行可删 |
| `C:\项目\老田的AI助手\quant_backtest\archive\k265_phase1.py:15,18` | 两位置：模块导入、phase1 影子写 | 历史脚本 | `—` | H/R+ | 待证据 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\archive\k265_phase1_commit.py:15,18` | 两位置：模块导入、phase1 影子读；潜在 KG 写 | 历史脚本 | `—` | H/R+ | 待证据 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\k266_clip_watch.py:12` | `e:\老田的AI助手\inbox\docs\pending`，**写剪贴板截图**（`:5` 仅说明） | K266 剪贴板守护 | `—` | H/R+ | 待证据 BLOCKED：真实收件箱 |
| `C:\项目\老田的AI助手\quant_backtest\k266_doc_ingest.py:25` | `e:\老田的AI助手\inbox\docs`，读待摄取、移动 done/reject | K266 OCR/PDF 守护；用户上传/新闻采集可能投递 | `—` | H/R+ | 待证据 BLOCKED：生产入口与移动语义 |
| `C:\项目\老田的AI助手\quant_backtest\k266_doc_ingest.py:26` | `...\quant_backtest\data\k266_docs`，写 registry/doc_texts 等 | 同上 | `—` | H/R+ | 待证据 BLOCKED：登记一致性 |
| `C:\项目\老田的AI助手\quant_backtest\k266_m1_prep.py:18` | `e:\老田的AI助手\quant_backtest\data\k266_m1`，M1 OCR 预处理根 | K266 OCR CLI | `—` | M/R+ | 待证据 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\k266_m1_ocr.py:13` | 同上，M1 OCR 读写 | K266 OCR CLI | `—` | M/R+ | 待证据 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\k266_seed_reports.py:10` | `e:\老田的AI助手\inbox\docs\pending`，读/写种子报告 | K266 报告生成 CLI | `—` | M/R+ | 待证据 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\kg_ths_concepts.py:24` | `e:\老田的AI助手\quant_backtest`，导入路径 | 知识图谱概念同步 CLI | `—` | H/R+ | 待证据 BLOCKED：KG 写者归属 |
| `C:\项目\老田的AI助手\data\korg059_l3_batch1.py:11` | `E:\老田的AI助手\data\korg059_learn.log`，append 写日志 | K-ORG 059 批处理 | `—` | M/R+ | 待证据 BLOCKED |
| `C:\项目\老田的AI助手\data\korg060_attrs_batch.py:11` | `E:\老田的AI助手\data\korg060_attrs.log`，append 写日志 | K-ORG 060 批处理 | `—` | M/R+ | 待证据 BLOCKED |
| `C:\项目\老田的AI助手\data\korg061_learn_batch.py:11` | `E:\老田的AI助手\data\korg061_learn.log`，append 写日志 | K-ORG 061 批处理 | `—` | M/R+ | 待证据 BLOCKED |
| `C:\项目\老田的AI助手\data\korg061b_f6a_probe.py:11` | `E:\老田的AI助手\tools`，导入路径 | K-ORG 061b 探针 | `—` | L/R | 迁移，实施 BLOCKED |
| `C:\项目\老田的AI助手\data\korg061b_f6a_probe.py:12` | `E:\老田的AI助手\data\korg061b_f6a_probe.log`，append 写日志 | 同上 | `—` | M/R+ | 待证据 BLOCKED |
| `C:\项目\老田的AI助手\data\korg061b_f6a_probe.py:44` | `E:\老田的AI助手\agent_platform\registry\claude_agents.json`，读注册配置 | 同上；Agent Platform 实际代码根据治理路线图在独立树 | `—` | M/R | 待证据 BLOCKED：注册表归属 |

## E. PowerShell 启动器、任务部署、Jev

| 唯一定位：绝对源码文件:行 | 旧路径及语义 | 调用方 / 运行域（仅静态可见） | 替代路径证据 | 风险 / 回滚 | 结论 |
|---|---|---|---|---|---|
| `C:\项目\老田的AI助手\quant_backtest\deploy_tushare_sync.ps1:36` | `E:\` 枚举找仓，**注册任务动作内部**回写 launcher | 管理员/SYSTEM 定时任务部署；不可执行 | 同项目 `start_tushare_sync.ps1:8-13` 已先尝试 `C:\aios`，但**部署脚本这行未改** | H/R+ | 待证据 BLOCKED：计划任务 XML 与 SYSTEM 权限 |
| `C:\项目\老田的AI助手\quant_backtest\deploy_tushare_sync.ps1:108` | `E:\` 枚举找仓，部署期 ACL 自愈目标 | 同上，可改 ACL | 同上（仅 launcher 候选） | H/R+ | 待证据 BLOCKED：权限边界 |
| `C:\项目\老田的AI助手\quant_backtest\start_tushare_sync.ps1:11` | `E:\` 旧机扫描 fallback，选择 root | TushareSync 启动器 | `:8-9` 优先已写 `C:\aios` | M/R | 保留回退（是否仍需旧机需 owner 确认） |
| `C:\项目\老田的AI助手\quant_backtest\start_adj_factor_sync.ps1:12` | `E:\` 旧机扫描 fallback | 复权因子定时启动器 | 同文件 `:9` 优先 `C:\aios`（静态） | M/R | 保留回退，待 owner |
| `C:\项目\老田的AI助手\jev-eval\alert_forward.ps1:15` | `E:\` 旧机目录扫描 fallback | Jev 告警转发任务 | 文件注释记录 `C:\aios` 优先 | M/R | 保留回退，待 owner |
| `C:\项目\老田的AI助手\jev-eval\n1_check.ps1:48` | `E:\` 旧机目录扫描 fallback | Jev N1 巡检任务 | 文件注释记录 `C:\aios` 优先 | M/R | 保留回退，待 owner |
| `C:\项目\老田的AI助手\jev-eval\jev_backfill_task.ps1:19` | 候选盘枚举 `C:\,E:\,D:\`，定位 news_collector | Jev 补全任务 | 同行 `C:\` 优先；**不是唯一 E 根** | M/R | 保留跨机器发现逻辑；调用来源待证据 |
| `C:\项目\老田的AI助手\jev-eval\auto_verify_jobs.ps1:41` | `e:\老田的AI助手\jev-eval\backup_daily.ps1`，`Get-Item` 查备份脚本 | Jev 任务核验 | `—` | M/R | 迁移，实施 BLOCKED |
| `C:\项目\老田的AI助手\jev-eval\kg_time_audit.py:7` | `E:\老田的AI助手\quant_backtest`，导入路径 | Jev/KG 时序审计 CLI | `—` | L/R | 迁移，实施 BLOCKED |
| `C:\项目\老田的AI助手\jev-eval\kg_time_audit2.py:4` | 同上，导入路径 | Jev/KG 时序审计 CLI v2 | `—` | L/R | 迁移，实施 BLOCKED |
| `C:\项目\老田的AI助手\jev-eval\k12_p1_smoke.py:6` | `e:\老田的AI助手\quant_backtest`，导入路径 | Jev smoke | `—` | L/R | 迁移，实施 BLOCKED |
| `C:\项目\老田的AI助手\jev-eval\webhook_probe.py:9` | `E:\老田的AI助手\jev-eval`，导入路径 | Webhook 探针 | `—` | L/R | 迁移，实施 BLOCKED |

## F. 明确不应机械迁移的旧路径兼容常量

| 唯一定位：绝对源码文件:行 | 旧路径及语义 | 调用方 / 运行域（仅静态可见） | 替代路径证据 | 风险 / 回滚 | 结论 |
|---|---|---|---|---|---|
| `C:\项目\老田的AI助手\quant_backtest\staging_materialize_dryrun.py:44` | `_LEGACY_PREFIX=E:\老田的AI助手`，**读取旧 manifest 的前缀识别，不是旧机 I/O** | staging dry-run，只读对照并写自己的报告 | 同文件 `:45,52-55` 映射 `C:\aios` | M/R | **保留**历史输入兼容；不可替换常量 |
| `C:\项目\老田的AI助手\quant_backtest\staging_materialize_v2.py:34` | 同上，映射旧 manifest 内 staged_path | `tushare_v2` 独立物化器 | 同文件 `:35,42-45` 已映射 `C:\aios` | H/R+ | **保留**历史输入兼容；不批准执行物化 |
| `C:\项目\老田的AI助手\quant_backtest\staging_materialize_prod.py:36` | 同上，生产物化器旧输入兼容 | 生产 `data/tushare` 写者 | 同文件 `:37,44-47` 已映射 `C:\aios` | H/R+ | **保留**旧输入兼容；生产写操作仍 BLOCKED |
| `C:\项目\老田的AI助手\quant_backtest\staging_materialize_full_history.py:35` | 同上，旧 manifest staged_path 前缀 | 全历史物化写者 | 同文件 `_LOCAL_PREFIX` 后继映射，未核运行态 | H/R+ | **保留**历史输入兼容；运行仍 BLOCKED |

## 待补证据、验收边界

1. **绝对优先的未决项**：`portal/agent_auto_commit.py` 的定时任务归属与 `git add -A` 误收风险；`tools/disk_ttl_cleanup.py` 的删除目标；`deploy_tushare_sync.ps1` 的 SYSTEM 任务动作与 ACL；K265 KG 图删写、K266 inbox move/write；`backtest_engine_v2.py` 各入口能否继承 `bt2_tune_compare.py` 自愈。这些均不得凭本矩阵放行。
2. **路径/数据未证明**：旧 E 仓与新 C 仓的等价性必须逐目录证实；`C:\aios` junction 已在多个脚本中使用/注释，但本工单没有文件存在性、junction 指向、运行健康或输出一致性证据；Agent Platform 路径尤其不得将老田仓 registry/engine 与独立 `C:\项目\zhice-os-v2-windows-20260920\code` 混同。
3. **未纳入执行迁移计数**：纯注释/示例，包括 `ops/*`、`zhice.ps1`、`start_kg_sync.ps1`、`start_triples_backfill.ps1`、`start_portal_nginx.ps1:1`、`shadow_weekly_report.py:15-16`、`k266_clip_watch.py:5`、`tools/riskguard_postmortem.py`；三处 `k_stock/*` 的旧路径注释。`portal/km_feishu_hub.py` 的旧路径 UI 输入示例应单独改文案，经产品 owner 确认；JS 文本中 `type:` 等伪匹配不可算 Windows 路径。历史证据、日志、jsonl/JSON 快照、vendor 与 parquet 未读取内容。
4. 下一阶段需 owner 提供：每条实际入口/计划任务引用、运行域、读写者清单、数据目录/配置/凭据有效性、受影响文件哈希、离线回归、生产只读探测、变更窗口、人工审批与逐项回滚演练。若无上述证据维持 `BLOCKED`；本报告不认定 GOV-FIX-002 已完成。
