# 2026-11-05 kg 日志闸门改「只看数据操作行」+ s4u_guard 加 JEV 白名单

## 1. kg_sync_task.log 前置闸门（`tools/datawarehouse/registry_rebuild.py`）

### 病灶

`find_kg_log_state()` 旧口径：**最后一条非空行必须带 `OK` 或 `exit=0`**，否则拒跑。

日志里除了数据操作行，还会落别的行：

```
registry_align date=2026-11-05T09:51:13+08:00 action=apply ... exit=0
b4_restore_drill date=2026-11-05T10:06:30+08:00 ... findings=P1-gzip-regression,P1-no-schedule rc=1
```

尾行是备份演练，于是 `registry_align --apply` 直接 `exit_code=2 拒跑：日志尾部不是成功收尾`。
后果不是"少跑一次"，而是**永久堵死**：除非下一次 kg_sync 成功，否则谁都 apply 不了；
人工只能在日志里补一行带 `OK` 的备注解锁（09:52 DSH-Lead 的 `manual-precheck OK` 就是这么来的）。

### 改法（v2）

尾部 20 行内**从后往前找第一条带结论的行**，只看它成败：

- 带结论的行 = 有 `exit=N` / `rc=N`，或有独立的 `OK` / `FAIL` / `WARN` 标记，
  或虽无退出码但带 `error=` / `ERROR` / `Traceback`（旧 `bad_marks` 的等价物，**不能因为改版丢掉**）；
- 不带任何上述标记的行（`b4_*` 演练的纯信息行、`fx_fill ... dry=False` 信息行）→ **跳过并计数**；
- 辅助作业（`AUX_JOB_PATTERN = ^(b4_[a-z_]+|restore_drill)`）→ 不参与数据根互斥判定，
  但其**失败会放进 `aux` 字段带出去**，由 `kg_log_aux_warnings` 上报。

`aux` 是**扫描整个 20 行窗口**收集的，不因判定提前返回而截断。这一点是踩出来的：
第一次实现边倒序扫边收集，结果 10:27 那次 apply 在失败演练之后追加了一条 `exit=0`，
倒序一撞到它即返回，演练的 P1 就不见了 —— 等于用"修好的闸门"又把问题盖回去。

保守性未退让：尾部 3 行出现进行中标记一律拒跑；整段存量找不到结论行也拒跑。

新增返回字段：`verdict`（据以判定的那行）、`skipped`（跳过条数）、`aux`（辅助作业失败行）。
`last` 语义不变，仍是**物理最后一行**（供 `kg_log_last_before` 审计留痕）。

### 验收（真实日志，非模拟）

```
kg_log ok = True | skipped= 3 | aux= 1
kg verdict= registry_align ... 2026-11-05T09:51:13 ... exit=0
--apply → exit_code=0, rebuild.performed=true
backup  = quant_backtest/data/warehouse_registry.json.bak-20261105-102749
verify  = {json_valid:true, catalog_sha256_match:true, physical_count_match:true, display_count_match:true}
match_after = True
```

漂移（`catalog_sha256` `c03865c5` → `7e96bbf5`）随之消掉，`portal/tests/test_dw_catalog_alignment.py`
从 failed 转 **passed**，`registry_align --check` rc=0。

### 附带发现

`b4_restore_drill` 自己是失败的：`findings=P1-gzip-regression,P1-no-schedule rc=1`。
即**备份恢复演练存在 2 个 P1 且未排班**。闸门不再因此堵死，但只要它还在日志尾部 20 行窗口内，
每次 apply 都会把它带进 `kg_log_aux_warnings`——不让一个长期失败被闸门"顺便"掩盖。
（这条 P1 本身要另案处理，本次只保证它可见。）

## 2. s4u_guard.ps1 白名单

### 病灶

守卫每 5 分钟把动作含 `aios` 的非 S4U 任务转成 S4U（`TASK_LOGON_S4U`，不加载用户 profile）。
JEV 四件套的动作路径就是 `C:\aios\jev-eval\...`，**全部命中**。

现在它转不动（每轮 `catch` 吞掉，日志还写 `noop (all S4U)` 骗人，实测 66 个任务全在失败）。
但哪天真转成功：`jev-gate.env.enc` 走**用户级 DPAPI** 解密、`lark-cli` 依赖用户 profile(`~`)、
下钻机器人同一份凭据 —— gate 起不来、公告源降级、飞书发不出消息。

### 改法

```powershell
$exempt = '^(Jev[A-Za-z0-9]*|DSHBot[A-Za-z0-9]*)$'
$_.TaskName -notmatch $exempt -and
```

豁免 8 个：`JevStack` / `JevRestart` / `JevMonthlyEval` / `JevCoverageDaily` / `JevDriftDaily` /
`JevBackfill` / `ZeroBound\JevNight` / `DSHBotWatchdog`。可被转换的从 66 降到 58。

同时把日志从 `noop (all S4U)` 改成 `noop matched=N failed=N exempt=N` —— 之前那句是假的
（匹配到却全转换失败），新格式能让"改了没生效"一眼可见。

### 未解

**找不到谁在拉起 `s4u_guard.ps1`**：全量任务 XML 无 `s4u_guard` 引用、无 `AIOS-S4U-Guard` 任务、
WMI 永久订阅只有系统默认 `SCM Event Log Filter`、仓库内无脚本引用（`rotate_logs.py` 只是注释提到）。
但日志严格每 5 分钟追加。

不影响本改动生效：脚本每轮从磁盘读取，改完即生效。风险在于**没人监控它**——
哪天它转换成功也不会有人被告知。

## 3. 回归

```
pytest tools/datawarehouse/tests/test_kg_log_gate.py              12 passed（新增）
pytest test_kg_log_gate + test_registry_align + test_registry_rebuild_review   25 passed
pytest portal/tests/test_dw_catalog_alignment.py                  1 passed（原 failed）
```

## 4. 回滚

- 闸门：`git revert <commit>`；registry 数据回滚用 `--apply` 自动留的
  `warehouse_registry.json.bak-20261105-102749`。
- 白名单：删掉 `-notmatch $exempt` 那一行即可恢复旧行为（但仍建议保留）。
