# K-STOCK-331 · TdxSync 每日增量缺勤事故处置 + 进程判别工具箱

日期：2026-10-13 ｜ 性质：运维事故发现-诊断-处置 ｜ 状态：处置完成·待验证回填

## 1. 事故时间线

| 时间 | 事件 |
|---|---|
| 17:30 | TdxSync 计划任务**未按时触发**（StartWhenAvailable 兜底） |
| 17:43:43 | 任务引擎补触发（DynamicInfo LastRunTime 实锤·LastTaskResult=0） |
| 17:43:50 | 官方实例启动（Python311+C:\aios 路径·PID 65096），**任务环境无代理 → 直连 rookie 每请求超时重试 → 极慢爬行** |
| 18:45 | 本会话巡检发现湖 d1 停在 20261012、tasklog 无今日行 → 误判"未触发" |
| 19:28 | 手动补跑（venv·清代理 env）——**实际吃了会话内其他代理通道，18 分钟跑完小表** |
| 19:36 | PEB 命令行复核发现 65096 仍活着 → **两实例并发写 manifest/表文件 20 分钟（数据竞争）** |
| 19:42 | CPU-delta 判别：65096 活跃（非卡死）→ 立即杀掉本会话补跑实例止损 |
| 19:50 | 复核 out 日志 30 分钟零增长+2h15m 未完成 → 实锤 65096 直连模式低效 |
| 19:52 | 杀 65096（ExecutionTimeLimit PT2H 未按期生效）；改用**显式 7890 代理**重跑（run3·断点续传无损失） |
| ~20:15 | run3 跑完全表 → 验证（见 §4） |

## 2. 根因链

1. **直接根因**：rookie API（rookie.xtcloud-tech.com:48088）**直连超时/极慢，必须走 7890 代理**（321 结论正向复证实锤）。TdxSync 计划任务环境无代理变量 → 官方实例每请求在超时边缘重试 → 155 表 2h15m 跑不完。
2. **放大器**：tdx_sync.py 无代理自配、无快速失败；stdout 块缓冲导致 out 日志长时零增长（假死观感）；ps1 只在结束时写 tasklog 行（运行中零痕迹）。
3. **隐患未爆**：ExecutionTimeLimit=PT2H 在 19:43 未实际生效（引擎对慢进程强杀失灵）——若放任，IgnoreNew 策略下明日 17:30 触发也可能被跳过。

## 3. 网络通道矩阵（321 结论修正版）

| 通道 | rookie API | 结果 |
|---|---|---|
| 7890（Clash 系常驻） | ✅ 秒回 JSON | **tdx_sync 正确通道** |
| 64639（WorkBuddy 会话代理） | ❌ 返回非 JSON | 不可用于 rookie |
| 直连（任务环境） | ⚠️ 超时边缘极慢爬 | 官方实例 2h15m 未完成 |
| 裸跑清 env（requests 吃不到代理） | 实测仍连 7890（通道来源待查） | 快（18min 小表队列） |

**修正 321 措辞**："必须走代理"精确化为"必须走 7890"；会话默认代理（64639）对 rookie 无效。

## 4. 处置结果与验证（run6 后终态）

- **湖 ts_daily d1 = 20261013 PASS**（10-13 日线 5561 行入库·ts_daily 4175758/adj_factor 4200838/daily_basic 增量 DONE）
- manifest JSON 可解析（155 表）· 关键三表 done=True
- 抽查 parquet 可读：ts_daily(44→45 parts)/ts_adj_factor(21→22)/ts_dividend_vip(1) OK
- **上游表级故障挂账（不影响开闸依赖链）**：
  - `ts_share_float`（212 万行解禁数据）：四个独立实例（65096/run1/run3/run4）全部卡死此表 → **远端对该表查询挂起**（连接不断数据不来·requests read-timeout 对慢速滴流失效）——已跳过；
  - `ts_limit_list_d/ts_tdx_daily/ts_hibor/ts_libor/ts_shibor/ts_shibor_quote`：远端 SQL 生成 bug（near "on"/"limit"/"1w_b"/"3day"）——官方实例历史上同样 ERROR，常态非本次事故。
- **顺带收官（325 遗留）**：stk_mins 回补 4233/4233 完成 + 补拉清理轮 fail=72→**0**（done=20741 日期段全量）——终版快照由 01:30 自动化接力。

## 5. 工具沉淀（quant_backtest/_331_*.py）

| 工具 | 用途 |
|---|---|
| `_331_task_state.py` | 注册表读计划任务缓存（GUID/LastRunTime/LastTaskResult）——schtasks 被安全策略硬拦时的替代 |
| `_331_di_parse.py` | DynamicInfo 时间戳解析（滑窗 FILETIME） |
| `_331_readcmdline.py` | **PEB 命令行读取**（纯 ctypes·无需 wmic/PowerShell）——进程身份辨认 |
| `_331_liveness.py` | CPU-delta 20s 采样 + netstat 连接 → 判"卡死 vs 慢跑" |
| `_331_watch.py` | 等 PID 退出 → 自动验证 manifest/d1/parquet 抽查 |

## 6. 教训（双条铁律级）

1. 🔴 **工具静默失败会污染关键判断**：本次 PowerShell 工具 exit 0 零输出（输出通道损坏），导致"无实例在跑"的**错误结论**→ 补跑与官方实例并发 20 分钟。**"不存在"类结论必须双方法复核**（本次靠 ctypes/PEB 命令行复核才暴露）。
2. 🔴 **补跑共享资源前先取证生命周期**：manifest/日志/湖文件是全局单写者资源，动手前用注册表 DynamicInfo + CPU-delta + 命令行读取三件套确认没有活实例。

## 7. 遗留观察项

- 明早验证 TdxSync 10-14 17:30 定时触发是否恢复（挂起实例已全清，IgnoreNew 阻塞解除）。
- **表排序陷阱**：sync 按行数升序"先小后大"，关键大表（ts_daily/adj_factor/daily_basic）排在 ts_share_float 之后——上游一张表挂起会**饿死全部更大表**。改进候选：大表前置/表级 watchdog（每表硬超时）/失败快速跳过。
- tdx_sync.py 改进候选：SESS 显式 proxies=7890 或启动探测；ts_share_float 拉取加整表硬超时——**待拍板**（涉及计划任务链路底座，不擅自改）。
- KG-TdxSync（E:\KGStockAnalysis）两个实例 17:43 同样启动且长跑——属 KG 项目仓库，未处置，移交知会。
