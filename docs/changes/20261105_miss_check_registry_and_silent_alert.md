# 2026-11-05 · miss_check 底册校准 + 告警静默链修复

> 触发：老田 11:48「先找问题，进行修复」。起点是 `JevFeed-Daily` / `JevBackfill`
> 仍是 S4U 的疑点，往下查出四个真问题，其中一个比 S4U 本身严重得多。

## 一、结论速览

| # | 问题 | 严重度 | 状态 |
|---|---|---|---|
| 1 | `miss_check` 底册全是换机前的旧任务名，每晚固定误报 10 条 | **P1（掩盖真故障）** | 已修（改名 + 退役 2 条） |
| 2 | `CryptoSync` 自 10-21 起 `Disabled`，crypto 数据冻在 10-21 09:00 | **P1（真断供）** | 已定位，**未擅自启用**，待裁定 |
| 3 | 失败告警凭据在无 profile 环境下静默失效（`expanduser("~")` 返回 `'~'`） | **P1（故障不可见）** | 已修（两处） |
| 4 | 判定窗口 `expect` 未清 microsecond → 整分整秒跑完判成 MISS | P2（假告警） | 已修 + 回归测试 |
| 5 | `CainiaoPull` / `K260RegressionDaily` / `ChanlunRefill` 无任务、无替代 | P2（真断供） | 保留在册继续报，待裁定 |

## 二、逐条证据与处置

### 1. 底册（REGISTRY）过时

`data/miss_check_last.json`（2026-11-04 21:30）报 `MISS×10`，逐条比对实际计划任务：

| 底册名 | 现实 | 处置 |
|---|---|---|
| `KG-TushareSync` 00:10 | 不存在；实际 `TushareSync` 23:30（底册另有一条，重复） | 退役旧条目 |
| `AIColAudit` 06:10 | 现名 `\ColAudit`，同 06:10，rc=0 | 改名 |
| `CryptoSync` 08:10 | 任务真实触发 **09:00**；且当前 Disabled | 改时刻 |
| `KG-TdxSync` 17:40 | 旧名；11-02 起拆 4 分片 | 退役旧条目（见下） |
| `KG-Cl1Sync` 18:10 | 现名 `KgCl1Sync` | 改名 |
| `AI工作站每日备份` 03:00 | 现名 `\BackupDaily`，07:45 起跑 | 改名 + 改时刻 |
| `JevReviewBackfill` 20:30 | 现名 `\JevBackfill`（整点轮） | 改名 |

**关键澄清（差点改错）**：`\TdxSync` 确实 `Disabled` 且 `last_result=267014`，但它**不是故障**——
11-02 起被主动拆成 `\ZeroBound\TdxSync_A_高频(17:35)`、`_B_财务(17:45)`、`_C_低频(17:55)`、
`_D_缺口(18:05)` 四片 + `TdxSyncProbe`，11-04 四个分片全部 rc=0。
**如果按"停用即故障"直接启用，会和四个分片重复写库。**
（遗留：四个分片目前**没有任何哨兵监控**，任一片静默死掉无人知——已列入待裁定。）

### 2. CryptoSync 真断供

```
state=Disabled  enabled=False  missed=15
last_run=10/21/2026 09:00:01  last_result=0
C:\Temp\crypto_sync_task.log 末条：crypto_sync exit=0 2026-10-21T09:00:38
data\crypto\*_USDT.parquet → 全部停在 Oct 21 09:00
```

末次是 **exit=0 成功**，随后被停用 → 人为操作，非崩溃。
全库 `start_crypto_sync.ps1` 引用数 = 1（仅 `\CryptoSync`），**无第二个入口**。
所以这不是"改名漏挂"，是 crypto 这条链整体停了 15 天。
未擅自启用：可能业务侧已退役（有 SOL Meme bot 另一条线），需老田裁定。

### 3. 告警静默链（本次最有价值的修复）

```
清空 USERPROFILE/HOMEDRIVE/HOMEPATH 后：
  os.path.expanduser("~")  →  '~'          （原样返回，不是路径）
  send_feishu._load_app_creds() → (None, None)
```

旧实现 `os.path.join(os.path.expanduser("~"), ".lark-cli", "config.json")` 会拼出
`~/.lark-cli/config.json` 去打开，必然 `FileNotFoundError`；
接着回退到 `lark-cli` 通道，而 `lark-cli` 同样依赖用户 profile →
**任务失败 → 告警发不出 → 完全没人知道**。S4U 任务正是"无 profile"的典型场景，
`JevFeed-Daily` 就是 S4U。

修法（不赌环境，直接让路径可靠）：按 环境变量 `LARK_CLI_CONFIG` > `~` >
`C:\Users\<USERNAME>` > 扫 `C:\Users\*\.lark-cli\config.json` 顺序取第一个可用。
两处同改：

- `news_collector/send_feishu.py` → `_cred_candidates()` + `_load_app_creds()`
- `jev-eval/backup_alert.py` → 同款（备份告警走这条）

验证：同一"无 profile"环境下，`creds → True True`，appId 尾号 `f85cdd`（= `cli_aa3e4f1b58f85cdd`，发送方 app）。

### 4. 秒级边界假 MISS

```python
# 修前
expect = now.replace(hour=exp_h, minute=exp_m, second=0)   # microsecond 未清
# now=21:30:00.654321 → expect = 16:05:00.654321
# 任务恰在 16:05:00.000000 跑完 → last < expect → 假 MISS
```

`RpsDaily` 连续误报即此因（实际 11-04 16:05:00 已成功，rc=0）。
修后 `second=0, microsecond=0`。

## 三、改动文件

| 文件 | 改动 |
|---|---|
| `quant_backtest/miss_check.py` | 底册 7 条改名/退役；`expect` 清 microsecond；三处"真断供"加核实注释 |
| `quant_backtest/tests/test_miss_check_expected_boundary.py` | **新增** 3 例：整分整秒不误报 / 昨日跑过仍报 / never 必报 |
| `news_collector/send_feishu.py` | `_cred_candidates()` + 多路径加载 |
| `jev-eval/backup_alert.py` | 同上 |

验证：`quant_backtest` 相关单测 **11 passed**；`jev-eval/tests` **94 passed**；
`send_feishu` 在剥离 profile 环境下实测能取到凭据。

## 四、踩坑与纪律

- **沙箱拦 `schtasks.exe`**（Bash 与 PowerShell 两条通道都拦），`miss_check` 在助手会话里
  跑必得 `never`（因为它靠 `schtasks /v` 取 Last Run）。我第一次的"校验跑"因此产出了
  15 条全 `never` 的假台账，已把该文件隔离为
  `data/miss_check_last.json.invalid-20261105-harness`，等 21:30 真实排班重建。
  → 纪律：**这类依赖 schtasks 的哨兵，不能在助手沙箱里“试跑”**，只能用注入 `_task_last_run` 的单测验证。
- 注册 S4U 临时探针任务被拒（权限不足），所以 S4U 下 `~` 是否可解析**没有实测**，
  改走"让路径不再依赖 profile"的确定性修法。
- 本次没有启用/停用任何计划任务，没有修改任何任务定义。

## 五、待老田裁定

1. **CryptoSync 恢复还是退役**（停了 15 天，唯一 crypto 入口）
2. **三个真断供重建还是退役**：`CainiaoPull`(19:00)、`K260RegressionDaily`(06:40)、`ChanlunRefill`(06:30)
3. **TdxSync 四个分片要不要纳入哨兵**（当前无人监控，任一片死掉无人知）
4. `JevFeed-Daily` / `JevBackfill` 仍是 S4U：**功能实测正常**（feed 11-04 真实入库 554 条、
   backfill 11:00 rc=0），告警风险已被第 3 条修复兜住；是否改回 Interactive 见仁见智
   （S4U 能在无人登录时跑，Interactive 不能）。

## 六、12:07 追加：三条「断供」逐条定性（恢复 ≠ 一个动作）

老田问「恢复这三条是干什么的」。逐条查证后，三条**性质完全不同**，不能一律重建任务：

| 任务 | 原职责 | 脚本 / 数据现状 | 定性 |
|---|---|---|---|
| `ChanlunRefill` 06:30 | 缠论全市场扫描**早批**，在 15:10 TodaysPicks 聚合前落盘、参与当日 T1 消费（15:35 晚批不参与） | `start_chanlun_scan.ps1` 由 `\ChanlunScan` 自 10-26 起改到 **07:20** 每日跑（11-05 07:20 触发、07:48:33 完成、rc=0） | **已被接管，不是断供**。重建 = 一天两次全市场扫描 |
| `K260RegressionDaily` 06:40 | K260 规则修复（10-08 三十条 audit）后的**只读数据质量守卫**，五断言守 events.jsonl 与 Neo4j 图谱一致性 | `k260_regression_task.ps1` + `_k260_regression.py` 完好；`data/semantica/events.jsonl` 56MB、11-05 07:48 仍在写 | **脚本在、任务在换机时丢了**。日志末次 10-15 06:40 全绿，与 10-15 23:13 换机断点吻合 |
| `CainiaoPull` 19:00 | 从菜鸟远端 `thunder_model` 每日增量拉六表（`fund_anomaly`/`position_daily`/`account_daily`/`realtime_monitor_record`/`member_instruction`/`real_order`），供 R4/OOS、指令质量档案、绩效档案、QMT 实盘监控 | 全库 `.py`/`.ps1` **无任何 wrapper 残留**（含 `fund_anomaly`/`ENDPOINTS` 的执行脚本零命中）；`C:\Temp\cainiao_import.log` 只是 mysql 导入日志；`zerobound-kg/cainiao_bridge/` 是反方向写影子库 demo | **脚本也丢了，无法「恢复」**，只能找回或重写 |

补充证据：

- `\XmapSync`（`quant_backtest/xmap_sync.py`，`CAINIAO_MYSQL_DSN` 在 `quant_backtest/.env.local`）是**另一条更窄的拉取链**：X2 实体 ID 映射的 watermark 增量（stock_basic / stock_trade_strategy 等三张核心表），不覆盖 CainiaoPull 的六表，不能当替代者。
- 10-08/10-13 文档里的 wrapper 改进项（六表→九表→十表）引用旧机路径 `E:/老田的AI助手/菜鸟/...`，换机后该盘符不存在。
- 10-08 审计底册只有 `ChanlunRefill` 一条（另两条 since=10-09，当晚才建），故不能用它恢复另两条的原始定义。

**K260 只读试跑（`_k260_regression.py`，不写任何数据）现在 rc=1**：

```
[PASS] A jsonl 行数          [PASS] B 字段自洽残留=0
[PASS] C 三错类>=119（当前 349）
[FAIL] D 图谱三错类==jsonl(349) 且逐类一致
        图谱 347 {'监管处罚': 152, '退市风险': 194, '业绩预亏': 1}
   vs  jsonl 349 {'监管处罚': 152, '退市风险': 196, '业绩预亏': 1}
[PASS] E 图谱 reason/conf 残留=0
```

数字已按原始字节复核（`DIGIT_CODES [51,52,57]=349 / [51,52,55]=347 / [49,57,52]=194 / [49,57,54]=196`），
不是输出通道串味。今晨 `KgDailySync` 06:20、`NineDimScan` 06:40、`KgExtractShadow` 06:50 全部 rc=0，
故不是「晨链没跑」，而是**图谱同步漏了 2 条「退市风险」**——恰是断言 D 设计要抓的失效模式。
守卫失联 20 天后，漂移已经发生。**直接重建任务会首日即报红并推飞书告警**，需老田裁定顺序。

**本次已做的改动（纯代码，未动任何计划任务）**：

- `quant_backtest/miss_check.py`：底册 `ChanlunRefill 06:30` → `ChanlunScan 07:20`（since 10-26），
  注释写明「不重建 06:30，会一天两次全市场扫描」。19 条底册不变。
- `ops/health_check.py`：`_DAILY_TASKS` 键 `ChanlunRefill` → `ChanlunScan`（真实任务名）。
- `quant_backtest/start_tbe_stats.ps1`：过时注释改准（它只读 A 股 parquet，与缠论扫描无时序依赖，
  06:30→07:20 不影响 06:45 的 TbeStats）。
- `quant_backtest/tests/test_miss_check_expected_boundary.py`：+2 例（底册键名/时刻、07:20 整分不误报）。
- 验证：`py_compile` 3 文件通过；`pytest test_miss_check_expected_boundary.py + test_miss_check_wake_grace.py` = **13 passed**。

**仍待裁定（涉及新建计划任务，须五要素报批）**：

1. `\K260RegressionDaily` 重建：user t / Interactive、每日 06:40、动作 `powershell -File k260_regression_task.ps1`。
   影响面：只读、失败推飞书；不可逆点：无（`-Disable`/`Unregister` 即可回滚）；
   替代方案：先补图谱 2 条漂移再排班；回滚：禁用任务即可，脚本与数据都不动。
   **前置决定**：接受首日告警，还是先修同步再排班。
2. `CainiaoPull`：先从旧机备份/git 找 wrapper，找不到则按 10-13 九表口径重写（属开发任务，不是恢复）。
3. `ChanlunRefill`：**不重建**，已按上表改底册。

## 七、12:29 落地：`\K260RegressionDaily` 重建（老田批准）

**注册定义**（XML / UTF-16 声明，`Register-ScheduledTask -Force`）：

| 项 | 值 |
|---|---|
| 名称 / 状态 | `\K260RegressionDaily` / Ready |
| 主体 | `DESKTOP-ARF447U\t`，`InteractiveToken`，`LeastPrivilege` |
| 触发 | 每日 06:40，`StartBoundary=2026-11-06T06:40:00`，`StartWhenAvailable=true` |
| 动作 | `powershell.exe -NoProfile -File "C:\aios\quant_backtest\k260_regression_task.ps1"` |
| 工作目录 | `C:\aios\quant_backtest` |
| 设置 | `IgnoreNew`、不因电池停、`ExecutionTimeLimit=PT1H`、允许按需触发 |
| 下次运行 | 2026-11-06 06:40 |

按批准的命令全文注册，**未加** `-ExecutionPolicy Bypass`（本机 `CurrentUser=RemoteSigned`，
本地脚本可直接执行，实测通过）。参数里不放 shell 重定向（K260 脚本内部自己写日志）。

**顺序裁定**：采用「**接受首日告警当哨兵**」。理由：先补齐图谱 2 条漂移属于**改业务数据**，
不在本次批准的动作范围内，不顺手做；守卫先上岗，漂移作为可见告警存在，修不修另批。

**手动触发端到端验证**（12:29:25）：

```
last_run=2026-11-05 12:29:25  last_result=1  state=Ready
_k260_regression_task.log  5224B -> 5922B（新结论行 exit=1 @12:29:13）
alert_send.log  2026-11-05T12:29:33 | task=k260_regression | channel=feishu
                 | result=OK | reason=SEND_OK | task_rc=1
```

`last_result=1` 是断言 D 失败的正确传递，不是任务配置错误。全链路（任务 → 只读断言 → rc →
飞书告警）实测闭环。**老田飞书里会多一条 12:29 的 K260 告警，属预期。**

**附带发现（未修，待批）**：`_k260_regression_task.log` 编码混杂——PS 5.1 的 `*>> $log`
写 UTF-16LE，Python 输出与 `Out-File -Encoding utf8` 写 UTF-8，同一文件里两种编码，
用 UTF-8 读会看到 `㉫〶牟来敲獳潩⁮硥⋯` 乱码。不影响判定与告警，但影响人读日志。
修法是把 `*>>` 改成 `2>&1 | Out-File -Encoding utf8 -Append`（需改 .ps1，不在本次批准范围）。

**CainiaoPull wrapper 找回结论：判定随旧机 E 盘丢失，只能重写。**

| 查找位置 | 结果 |
|---|---|
| `git log --all -- "*cainiao*"`（含已删 `--diff-filter=D`） | 只有 `portal/cainiao_auth.py` 等 4 个登录鉴权文件，**从未有拉取 wrapper**，也无删除记录 |
| `backups\2026-11-05_0756\*.tar.gz` 四份归档 | 同上，仅 4 个 portal 文件 |
| `C:\backups_mirror\2026-11-02..04_0745` 四份归档 | 无任何 pull/sync/wrapper 条目 |
| 项目根「菜鸟」源码目录 | 不存在 |
| `C:\Temp\cainiao_7z_list.txt`（216MB） | UTF-16LE（BOM `fffe`），内容是 `thunder-model-admin` 后端 Java/uniapp 源码归档清单，命中的是 `node_modules` 里的 `gensync` 之类单词，与本机 Python wrapper 无关（ASCII grep 曾给假阴性，已按 UTF-16 解码复核） |

**重写材料是齐的**：`quant_backtest\.env.local` 仍含 `CAINIAO_MYSQL_DSN` 键；
`quant_backtest\xmap_sync.py` 有可复用的连接 + watermark 增量代码；
10-08/10-13 文档给了六表→九表→十表的表名与 `/page` 端点口径。

**CryptoSync 本轮仍未动**（停 15 天，恢复/退役方向未裁定）。
