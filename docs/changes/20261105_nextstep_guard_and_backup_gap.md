# 2026-11-05 13:24 · 「下一步」推进：哨兵补盲 / 日志编码 / db 备份断链

> 起点：三件批准项已收口（见 `20261105_k260_cainiao_crypto_closure.md`），
> 遗留 `\CainiaoPull` 19:00 未排班、K260 日志编码混杂。
> 本轮原则：**不把「下一步」当成对任何 L2 排班的批准**，只做代码层可验证的修复，
> 把需要裁定的排班整理成五要素报批卡。

## 一、本轮做完（均不需要新 L2 批准）

| # | 事项 | 状态 | 验收 |
|---|---|---|---|
| 1 | K260 回归日志 UTF-16LE/UTF-8 混杂 | **已修** | 历史 96 行规范化为纯 UTF-8，12 条 `exit=` / 60 条判定行不丢；任务触发后新写入零 NUL、`rc=0` |
| 2 | `miss_check` 底册三条结论过时 | **已校准** | `CryptoSync` / `K260RegressionDaily` / `CainiaoPull` 注释改为今日事实 |
| 3 | \ZeroBound\TdxSync 四分片无哨兵 | **已补** | 4 条入册 + 完整路径查询 + 短名回退；18 例单测全过 |
| 4 | 两处误导性注释 | **已纠错** | `cainiao_pull.py`、`db_dump_daily.py` |

## 二、逐条证据

### 1. K260 日志编码

根因：`k260_regression_task.ps1` 用 PS 5.1 的 `*>>` 追 Python 输出（**UTF-16LE**），
下一行 `Out-File -Append -Encoding utf8` 又写 UTF-8 → 同一文件两种编码。
字节实测：开头 `\xff\xfe[\x00P\x00A\x00S\x00S\x00]`，尾部却是 ASCII `k260_regression exit=0`。

- 修法：`$pyout = & python ... 2>&1` → `ForEach-Object { $_.ToString() }` →
  `Out-File $log -Append -Encoding utf8`，全程单一编码；`$LASTEXITCODE` 仍取 Python 的 rc。
- 历史规范化：`C:\Temp\normalize_k260_log.py`，按 `\r\x00\n\x00` / `\r\n` 切行、
  逐行按 NUL 占比判编码，备份 `C:\Temp\k260_regression_task.log.mixed-20261105.bak`。
  结果 84 行 UTF-16LE + 12 行 UTF-8 → 纯 UTF-8，`exit=` 12→12、判定行 60→60，
  `INVARIANT_OK=True`，6438B → 4642B。
- 13:27:21 触发 `\K260RegressionDaily`：`LastTaskResult=0`，日志新段为标准
  `\r\n`、NUL=0、`utf8_decode_ok=True`。

### 2. miss_check 底册校准 + 四分片补盲

**过时结论（会误导下一次排障）**：

| 条目 | 旧注释 | 今日事实 |
|---|---|---|
| `CryptoSync` | "自 10-21 起 Disabled、数据冻在 10-21、MISS 即真断供" | 13:12 已恢复，wrapper 改接 `export_us_hk --market CRYPTO`，`State=Ready`、触发 rc=0 |
| `K260RegressionDaily` | "无此任务、无替代者 → 真断供" | 12:29 已重建，13:14 触发 rc=0 |
| `CainiaoPull` | "无任何任务 action 引用 cainiao 脚本" | 九表脚本已重写并 9/9 跑通，但 19:00 任务仍无 → **调度层**仍真断供 |

**四分片**：`\TdxSync` 自 11-02 起拆为 `\ZeroBound\` 下
`TdxSync_A_高频 17:35` / `B_财务 17:45` / `C_低频 17:55` / `D_缺口 18:05`，
旧任务 `Disabled` 且 `last_result=267014` 是设计内（启用会重复写库），
但四分片一直不在册，任一片静默死掉无人知。

- 新增 4 条，带 `"path": r"\ZeroBound\..."`：任务在子路径且名称含中文，
  `schtasks /tn` 需完整路径。
- `run()` 查询改为 `cfg.get("path") or name`，路径查不到再回退短名，
  避免"查不到"被误判成"没跑"。`_task_last_run` 签名不变，既有单测不受影响。
- 新增 `tests/test_miss_check_tdx_shards.py`（5 例：登记/路径优先/短名回退/
  单点静默/四点全静默）。连同原有两文件共 **18 passed**。
- **纪律与观察点**：沙箱把 `schtasks.exe` 列入黑名单且不允许等价绕过，
  端到端**未试跑**，全部注入验证。今晚 **21:30** 首轮若四分片齐报 `never`，
  说明路径查询在真实任务环境不通 → 立刻回滚这 4 条（不改其他条目）。

### 3. 两处注释纠错（防止把"设计意图"当"运行事实"）

- `quant_backtest/cainiao_pull.py` 原写"该库由 `db_dump_daily.py` 每日备份"
  → 改为：脚本存在但 2026-11-05 复核无任何计划任务调用，排班仍需 L2 批准。
- `ops/db_dump_daily.py` docstring 原写 `[--keep-days 14]` → **该参数从未实现**：
  `main()` 不解析 `sys.argv`，`KEEP_DAYS=14` 是源码常量，传了会被静默忽略。
  11-05 人工那次日志却写「无超过 60 天的旧目录」→ **轮转口径未收敛**。
  若照旧文档直接排班，首次运行就会把 10-09~10-15 / 10-29 这批换机前后留痕
  轮转掉（走回收站、可逆，但证据被移走）。

## 三、待批（P0）：db 日备份排班 —— L2 五要素报批卡

**现状证据**：全库 **492** 个计划任务，动作/参数全扫，**没有任何一个引用
`db_dump_daily.py`**。`backups/db` 仅 9 个目录：10-09~10-15（换机前旧机日排班，
`dump_latest.log` 10-15 03:32 收尾）、10-29、11-05（人工）。
11-05 10:08 人工跑 `rc=0`：必保 3/3 全绿
（cainiao_mysql 147M / zhice_os_v2 764M / quant_pg_alldb 108M，gzip + sha256 + manifest），
两个 Neo4j 因 community 在线 dump 不可用而 `FAIL`，`must: False` 属设计内
（**不是 bug**，10-30 曾误判已撤回）。即：**脚本可用，缺的纯粹是排班**。

- **命令全文（批准后才执行，本轮未执行）**：
  ```powershell
  $a = New-ScheduledTaskAction -Execute 'powershell.exe' `
       -Argument '-NoProfile -ExecutionPolicy Bypass -File "C:\aios\ops\start_db_dump_daily.ps1"' `
       -WorkingDirectory 'C:\aios'
  $t = New-ScheduledTaskTrigger -Daily -At 03:30
  $p = New-ScheduledTaskPrincipal -UserId 'DESKTOP-ARF447U\t' `
       -LogonType Interactive -RunLevel LeastPrivilege
  Register-ScheduledTask -TaskName 'DbDumpDaily' -Action $a -Trigger $t -Principal $p
  ```
  wrapper `ops/start_db_dump_daily.ps1` 已备好（**未排班、不生效**），带日志与
  `alert_fail_wrap.py db_dump_daily` 失败告警。另需把 `DbDumpDaily` 加入
  `miss_check.REGISTRY`（`expect 03:30`）才有"应跑未跑"兜底。
- **影响面**：每天 03:30 起跑约 17 分钟（11-05 实测 10:08:43→10:25:05），
  写 `backups/db/YYYY-MM-DD/` 约 1GB gzip；只读 dump，不碰线上库。
  **前置条件**：Docker Desktop 及 `quant-cainiao-mysql` / `zhice-db` / `quant-pg`
  三个容器在 03:30 必须已运行，否则必保库全失败并推飞书。
  **磁盘**：按 14 天轮转约 14GB，按 60 天约 60GB。
- **不可逆点**：轮转删除走 `SHFileOperationW FOF_ALLOWUNDO` 进回收站（可恢复），
  但 10-09~10-15 等历史目录会被移走；新增任务本身可禁用/删除。
- **替代方案与已尝试**：① 挂进现有 `\BackupDaily`（07:56）——它跑
  `jev-eval/backup_daily.ps1` 产 `zhice_db.sql.gz.enc` + tar，与本脚本口径不同，
  合并会让单任务变成长跑且失败域变大，不推荐；② WorkBuddy automation——
  依赖会话在线，不如计划任务稳；③ 维持人工——已导致 21 天里只有 2 次备份，不推荐；
  ④ 只补 `miss_check` 哨兵不排班——会每晚报 MISS 但不产生数据，不解决根因。
- **回滚预案**：`Disable-ScheduledTask -TaskName 'DbDumpDaily'` 或
  `Unregister-ScheduledTask -TaskName 'DbDumpDaily' -Confirm:$false`；
  从回收站恢复被轮转目录；`miss_check.REGISTRY` 删对应条目；
  wrapper 文件可留（不生效）。
- **批准时需一并裁定**：`KEEP_DAYS` 取 14 还是 60（当前源码常量 14，
  与 11-05 日志的 60 天表述冲突）。

## 四、待批 / 待观察清单

1. `\CainiaoPull`(19:00) 仍 `NO_TASK` —— 脚本已就绪，排班需另批（本轮未动）。
2. `DbDumpDaily` 排班 —— 见 §三，本轮未执行，仅备好 wrapper。
3. 四分片哨兵今晚 21:30 首轮观察点（若齐报 `never` 立即回滚 4 条）。
4. `_k260_regression_task.log` 编码问题已修，**移出遗留清单**。
