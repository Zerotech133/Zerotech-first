# 2026-11-05 S4U 守卫告警出口 + b4 演练 P1 结论 + 卡片真机核验

## 1. s4u_guard 宿主定位：一直是计划任务 `\AIOS-S4U-Guard`，只是普通权限枚举不到

### 结论

宿主就是 **`\AIOS-S4U-Guard`**（SYSTEM 身份、时间触发器、每 5 分钟）。此前"487 个任务里没有一个引用 s4u_guard.ps1 / 任务不存在"的判断是**权限造成的假象**：该任务的安全描述符不允许普通用户读取，`Get-ScheduledTask` 枚举时会**静默跳过**它，`Get-ScheduledTask -TaskName 'AIOS-S4U-Guard'` 也直接返回空。

### 证据链（任务计划器操作日志，11:23 那一轮）

```
11:23:13  EID 107  由于存在时间触发器条件，任务计划程序已启动任务 "\AIOS-S4U-Guard" 的实例
11:23:13  EID 100  任务计划程序已为用户 "NT AUTHORITY\SYSTEM" 启动 "\AIOS-S4U-Guard" 任务的实例
11:23:13  EID 129  任务计划程序启动进程 ID 为 "47612" 的任务 "\AIOS-S4U-Guard"，实例 "powershell.exe"
11:23:20  EID 201  成功完成任务 "\AIOS-S4U-Guard"、操作 "powershell.exe"，返回代码 0
```

脚本侧父进程链同时印证（`s4u_guard.log` 当行）：

```
parent=47612:powershell.exe:"...powershell.exe" -NoProfile -ExecutionPolicy By <- 3424:svchost.exe:C:\WINDOWS\system32\svchost.exe -k netsvcs -p -s Schedule
```

`svchost.exe -k netsvcs -s Schedule` = 任务计划器服务本身。

### 为什么以前查不到

- 全量任务 XML 搜 `s4u_guard` 无结果：`C:\Windows\System32\Tasks` 目录对非管理员**拒绝访问**（`rg` 报 os error 5），不是文件里真没有。
- 本机沙箱把 `schtasks.exe` 列入程序黑名单，无法用它交叉验证。
- 现有进程快照里看不到：守卫是每 5 分钟一次的短命进程，跑完即退。
- 结论：**不要再用"任务列表里搜不到"推断宿主不存在**，要查 `Microsoft-Windows-TaskScheduler/Operational` 的 EID 129/200，那条事件直接给出任务名 + PID。

### 脚本改动

`C:\aios\s4u_guard.ps1`（备份 `s4u_guard.ps1.bak-20261105-1125`）：

- 新增 `Get-ParentChain`，每轮把父进程链（深度 4）写进日志行尾 `parent=`，并按 180 字符截断。宿主已定位，但这行留着成本很低，下次被谁拉起仍然看得见。
- 版本注释 v2 → v3，"every 15 min" 改为实际的 "every 5 min"（旧注释与事实不符，会误导排障）。

## 2. 失败告警出口：58 个任务不再静默空转

### 问题

守卫每 5 分钟匹配到 164 个任务、其中 58 个转换失败，旧版只写一行
`noop matched=164 failed=58 exempt=8`。日志到 100KB 会被删，等于失败被周期性抹掉。

### 实现

`C:\aios\tools\s4u_alert.py`（新增）+ `s4u_guard.ps1` 调用：

1. 守卫每轮把 `stamp/matched/exempt/converted/failed/parent/host` 落盘到
   `C:\aios\state\s4u_guard_state.json`（原子写，UTF-8 无 BOM）。
2. 守卫随后调用 `python -X utf8 C:\aios\tools\s4u_alert.py`，rc 写回日志行 `alert_rc=`。
3. 告警模块按"失败集合签名变化 **或** 距上次告警 ≥ 6 小时"发送；失败清零且有历史失败时发一条"已恢复"。发送结果落 `s4u_guard_alert.jsonl` 审计。

### 为什么告警能跑在 SYSTEM 下

守卫是 SYSTEM（`s4u_guard.log` 属主 = `NT AUTHORITY\SYSTEM`），解不了用户级 DPAPI 的
`jev-gate.env.enc`。但 `C:\Users\t\.lark-cli\config.json` 是**明文静态 app 凭证**，
ACL 里 `NT AUTHORITY\SYSTEM = FullControl`。告警模块把 `USERPROFILE`/`HOME` 指向
`C:\Users\t` 后再导入 `send_feishu`，走 OpenAPI 直连（不依赖 lark-cli 的 profile、
也不占用飞书事件总线，与 portal 机器人的 WS 长连接不冲突）。

### 实测（2026-11-05 11:23）

| 轮次 | 触发者 | 日志行 | 结果 |
|---|---|---|---|
| 11:23:20 | 计划任务（SYSTEM） | `noop matched=164 failed=58 exempt=8 alert_rc=0` | **真实身份下发送成功** |
| 11:23:23 | 手动补跑（用户态） | 同上，`alert_rc=3` | 去重生效，未重复发送 |

落盘确认：`s4u_guard_alert.json` → `"last_result": true, "last_reason": "openapi"`；
审计行 `{"event":"alert","ok":true,"failed_count":58,"changed":true}`。

### 58 个失败任务的实际构成

失败集合稳定（签名未变），绝大多数是**与 JEV 无关的 AIOS 侧任务**：
`A0-Daily-Board-Verify`、`Academy*`、`ZeroBound_*`、`Zhuge*`、`Zhaoyun*`、`Huangzhong*`、
`HermesWebUI-Guard30`、`BackupDaily`、`JevFeed-Verify` 等。
即"守卫想把这些任务转 S4U 但转不动"，不等于这些任务必须转。下一步要么按域补白名单
（`JevFeed-Verify` 明显属于 JEV 域漏名单），要么认领"为什么 RegisterTaskDefinition
在 SYSTEM 侧对这批任务返回失败"。**本次只保证它不再静默。**

## 3. b4_restore_drill 的两个 P1

### P1-gzip-regression：已在生产脚本层修掉，可用证据闭环

- 旧备份 `backups\db\2026-10-29\quant_pg_alldb.sql.gz` 头 4 字节 = `2d 2d 0a 2d`（`--\n-`），不是 gzip。
  **真根因**（演练回执 §三 P1-a）：`with gzip.open(dst,"wb") as f: subprocess.run(cmd, stdout=f)` ——
  Windows 下子进程直接写原始 fd，**明文数据先落盘、空 gzip 头在 `close()` 时才追加到尾部**（尾部那 21–30 字节
  `1f 8b 08 08 …` 就是它）。`dump_latest.log` 只判 `rc==0 and size>200`，所以一路报 OK。
- 演练那侧也错了一次：把 `.gz` 直接喂 `psql -f`（psql 不解压），于是读到尾部 `0x8b` 报
  `invalid byte sequence for encoding "UTF8"`。**两个错误叠在一起**，才表现为"备份坏了但数据其实是全的"。
- `ops/db_dump_daily.py` 已按 **K-STOCK-414** 改为"容器内纯 dump、宿主侧 gzip 收流"
  （`gzip.open(dst,'wb')` + 判 `rc==0 and size>200`）。
- 2026-11-05 实测：`gzip -t quant_pg_alldb.sql.gz` 通过，头字节 `1f 8b 08 08`；
  恢复验证（`b4_dumpfix_verify`，10:35，`rc=0`）跑到 `CREATE INDEX/TRIGGER` 无编码错误。
- 结论：**这条 P1 的修复落在生产 dump 脚本里，不是临时补丁**。剩下的只是演练没重跑出新的结论行。

### P1-no-schedule：已排班（老田 11:29 批准后落地）

原先演练靠 `C:\Temp\b4_*.py` 一堆临时脚本拼出来，`C:\Temp` 一清就等于没演练过。三件套已落地：

| # | 项 | 状态 |
|---|---|---|
| 1 | 固化脚本 `C:\aios\tools\b4_restore_drill.py` | 完成 |
| 2 | 计划任务 `\JevRestoreDrill` | 已注册，Ready，next=2026-12-01 03:00 |
| 3 | 失败 rc=1 进 `aux` | 完成（`b4_restore_drill` 命中 `AUX_JOB_PATTERN`） |

脚本相比旧演练修了两处**方法错误**：

- 旧演练把 `.gz` 直接喂 `psql -f`（psql 不解压），所以必然撞 `0x8b`。新版在宿主侧用 `gzip.open` 流式解压后喂 `psql` stdin（`ON_ERROR_STOP=1`）；
- 旧演练只看"能不能跑完"，没有前置判定。新版六步：选备份 → 时效检查（超 `--max-age-days 8` 判 `P1-stale-backup`）→ gzip 魔数 + 真解压前 8MB → 起一次性容器 → 恢复 → 行数 + md5 + `pg_dump` 快照（记 sha256）。

任务定义要点：

- 主体 `DESKTOP-ARF447U\t`、`InteractiveToken`、`LeastPrivilege`（与 Jev* 同口径：docker desktop 是用户级，S4U 下拉不起容器）；
- 触发器 `ScheduleByMonth`，每月 1 日 03:00，`StartWhenAvailable=true`（错过补跑）、`StopIfGoingOnBatteries=false`（掉电不杀，历史判例 K268）、`ExecutionTimeLimit=PT2H`、`MultipleInstances=StopExisting`；
- **参数里不能写 shell 重定向**：第一次注册时写了 `--json >> xxx.log 2>&1`，`>>` 被当参数喂给 python，任务 `last_result=2`（ERROR_FILE_NOT_FOUND）且日志根本没生成。改为脚本内部写 `C:\Temp\b4_restore_drill_task.log`。

实测（任务真实触发，不是手动跑脚本）：

```
last_result=0  last_run=2026/11/5 11:35:00
[2026-11-05T11:35:00+08:00] rc=0 findings=none
rows=25/25  md5=0de97aec5191829f6c8290f74ce73494  magic=1f8b  snapshot 113,619,834B
```

闸门确认：`find_kg_log_state()` → `ok=true`，`verdict` 仍是 10:27 那条 `registry_align exit=0`，
`skipped=3`（三条 b4 行被跳过），`aux` 里暂留 10:06 那条历史失败（`rc=1`），等它滑出尾部 20 行窗口自然消失。

### 追加发现：db dump 的每日调度在换机后丢失（2026-11-05 12:00 更正）

> **更正记录（老田 11:40 指出，已撤回原结论）**
> 本节初稿写的是「"db dump 每周至少 1 次成功"从未真正成立」，**该结论错误，已删除**。
> 错误原因：只看了 487 个计划任务里没有引用 `db_dump_daily.py`，就推断"从没跑过"，
> 没有去查 `backups\db` 目录内的**文件 mtime**。产物自己会说话。

**事实一：它曾连续 7 天日跑。**

```
backups\db\2026-10-09\cainiao_mysql_alldb.sql.gz   mtime = Oct  9 23:09
backups\db\2026-10-15\cainiao_mysql_alldb.sql.gz   mtime = Oct 15 03:31
（共 2026-10-09 ~ 2026-10-15 七个连续日目录，每个目录内文件 mtime 各对应当天）
```

**事实二：断点是换机，不是"从未排班"。** 这七个目录的**目录 mtime 全是 `Oct 15 23:13`**
（迁移拷贝时刻），与 `backup_daily.ps1` v11 注释里记录的换机时间一致。即：旧机上有日排班，
换到本机（`C:\aios` junction）后**任务没有重建**。之后只剩两次人工跑：10-29 11:54、11-05 10:08。

**事实三：`\BackupDaily` 是健康的，且确实在备份数据库。** 复核 11-05 07:56 那轮产物：

```
zhice_db.sql.gz.enc  845,330,752 B   （pg_dump zhice_os_v2，AES-256 加密）
quant_code.tar.gz    614,809,408 B
quant_data.tar.gz    507,823,782 B
neo4j_kg.tar.gz      501,134,393 B
jev_eval.tar.gz        4,308,859 B
offsite_remote_receipt.txt
```

所以「数据库每天有备份」对 `zhice_os_v2` 是成立的。**缺口精确表述为**：`quant-pg`（quant_meta 等）、
菜鸟 MySQL、两套 neo4j 没有每日归档落 `backups\db`，而 `db_dump_daily.py` 覆盖的正是这部分。
`\BackupDaily` **不应删除**——四条 tar 归档链与 `C:\backups_mirror` 镜像只有它在维护。

**事实四（新发现，P1）：`zerobound-neo4j-independent` 的数据卷没有任何备份。**

先纠正我自己的一个误判：`db_dump_daily.py` 的 rc=0 **不是 bug，是设计**——

```python
# Neo4j community 在线 dump 不可用（需停库）——先尝试，失败记 PENDING 不算硬失败
{"key": "kg_neo4j",       ... "must": False},
{"key": "zerobound_neo4j", ... "must": False},
```

`must: False` 让汇总 `rc=0` 是有意的。但两条 PENDING 后面的实际覆盖情况完全不同：

| 实例 | 数据卷 | 有备份吗 |
|---|---|---|
| `neo4j-kg` | `docker volume neo4j_kg_data` | ✅ `backup_daily.ps1` 的 `Backup-TarDir` → `neo4j_kg.tar.gz`（11-05 实测 501MB） |
| `zerobound-neo4j-independent` | `docker volume zerobound_neo4j_data` | ❌ 无 |

取证：`backup_daily.ps1` 里 `grep zerobound` **零命中**；`backups\` 与 `C:\backups_mirror\` 全库
`find -iname "*zerobound*"` **零命中**；`backups\db\*\manifest.json` 里它只以 FAIL 条目出现。
即：这套 neo4j 的图数据（ZeroBound 域）**目前零备份**，一旦卷损坏不可恢复。

修法不是改 rc（那是设计），而是照 `neo4j_kg` 的路子给它补一条卷归档：
`Backup-TarDir "zerobound-neo4j-independent" "/data" "zerobound_neo4j.tar.gz"`。
另建议把日志里那两个 `FAIL ... rc=1` 的措辞改成 `PENDING`，与代码注释口径一致（现在日志说 FAIL，
汇总说 rc=0，读日志的人会以为自相矛盾）。

`b4_restore_drill.py` 的 `P1-stale-backup` 只能发现"备份太旧"，发现不到"这套实例压根没进备份链"。

## 4. 飞书卡片真机核验（10:06 测试卡）

### 已实证

- 服务端 `om_x100b618a5671bca8dc1aebc59e29c7b`：`updated=true`，
  `update_time=1793847434890`（= 2026-11-05 10:57:14，与本地日志 10:57:15 对齐）。
- `body.content` 末段已是：
  `[{"tag":"text","text":"✓ 已处理"},{"tag":"text","text":" · by ou_50a95455c7dffec1090ade400886d0d4"}]`
- 即：**「✓ 已处理」确实落到飞书服务端，不是只写了本地日志。**

### 颜色无法由 API 判定（已做对照实验）

同一 app 新发一张**原生 2.0 格式卡**（`header.template=green` + `lark_md` + `markdown`），
正确发出（`code=0`）后回读，`body.content` 同样是
`{"title":..., "elements":[[{"tag":"text",...}]]}`，**`header` 根本不在回读结果里**，
`markdown` 也被拆成两个 `text`。

结论：`im/v1/messages` 对 interactive 消息返回的是**v1 渲染摘要**，不承载 header/颜色。
因此"老卡 PATCH 后是否变绿"**只能用肉眼看**，不能用接口断言。
代码路径本身没问题：`lark_card_handler.py:421` ack → `status_text="✓ 已处理", template="green"`，
`_legacy_to_new` 为旧卡补 `header`，`_mutate_card` 再把 `header.template` 覆盖成 `green`，
PATCH 载荷确实带绿。

> 已在内部测试群留一张绿头基准卡（`om_x100b618b385074b4c1f01ca7ff0697a`）供对照：
> 若那张是绿头、10:06 那张不是，说明旧消息被飞书降级保留，需要在发卡侧直接发 2.0 格式卡，
> 而不是"发旧卡再转格式 PATCH"。

## 5. 副作用与遗留

- `C:\aios\state\` 新建（守卫状态 + 告警审计）。守卫日志行变长（多了 `alert_rc=` 与 `parent=`）。
- 11:23:23 手动补跑把 `s4u_guard_state.json` 的 `parent` 覆盖成用户态进程链；后续系统轮会写回 `svchost -s Schedule`，不影响告警判定（签名一致 → rc=3 去重）。
- **白名单已补**（老田 11:29 批准）：`^(Jev[A-Za-z0-9]*|DSHBot[A-Za-z0-9]*)$`
  → `^(Jev[A-Za-z0-9-]*|DSHBot[A-Za-z0-9-]*)$`。连字符进字符类后实测
  `matched 164→162 / exempt 8→10 / failed 58→57` —— 漏网的不止 `JevFeed-Verify`，
  `JevFeed-Daily` 同样因为连字符一直没被豁免。
- 待批：给 `ops/db_dump_daily.py` **重建**每日任务（换机后丢失，非从未排班，见 §3 更正）。
- 待批：给 `zerobound-neo4j-independent` 补卷归档（见 §3 事实四，当前零备份）。
- `\BackupDaily` **保持原样，不删**（11:40 老田提出删除，经核为四条归档链唯一日备份，未执行，待其明示）。
