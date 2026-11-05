# 2026-11-05 · 三件批准项收口：K260 漂移 / CainiaoPull 重写 / CryptoSync 恢复

> 触发：老田「执行图谱 2 条漂移修、CainiaoPull 重写、CryptoSync 恢复」。
> 起点：上一轮在网络中断处停住；后台全量同步 `TLis9a` 返回 **3/9 失败**，
> `\CryptoSync` 未见启用结果。本轮从「只读核验现状」接起，未重复执行已完成的动作。

## 一、结论速览

| # | 批准项 | 结果 | 验收证据 |
|---|---|---|---|
| 1 | 图谱 2 条 tag 漂移 | **已修** | 五断言 A~E 全 PASS；`\K260RegressionDaily` 13:14 触发 `last_result=0` |
| 2 | CainiaoPull 重写 | **已重写并全量跑通** | 九表 9/9 `rc=0`，补 62393 行，源/镜像行数 ALL_MATCH，水位 9 条 |
| 3 | CryptoSync 恢复 | **已恢复（改接统一供给层）** | `State=Ready`、触发 `last_result=0`、下次 11-06 09:00、`Missed=0` |

定性修正：**`\CryptoSync` 不是「任务被停用，启用即可」**。`crypto_sync.py` 有主动停用开关，
直接启用会制造每天 `rc=0` 但什么都没干的假绿空转。详见 §四。

## 二、K260 图谱 2 条 tag 漂移

- 漂移节点：`ev:nt:AN202610291831173502:300555.SZ`、
  `ev:nt:AN202610291831173938:600678.SH`，标签 `退市风险`。
  修复前图谱 347 / `退市风险` 194，jsonl 349 / `退市风险` 196。
- 处置：`k260_fix_tag_drift.py --apply`，备份
  `quant_backtest/data/k260_tag_drift_backup_20261105-124726.json`。
- 复跑 `_k260_regression.py`：A/B/C/D/E 全 PASS，三错类
  **349 = 业绩预亏 1 + 监管处罚 152 + 退市风险 196**，图谱与 jsonl 逐类一致。
- 13:14:20 触发 `\K260RegressionDaily` → `LastTaskResult=0`（12:29 的 `rc=1`
  是断言 D 正确报出漂移，非配置错误）。下次 11-06 06:40。

## 三、CainiaoPull 重写（含首轮 3/9 的 P1 根因）

九表口径：`fund_anomaly` / `position_daily` / `account_daily` /
`realtime_monitor_record` / `member_instruction` / `member_real_order` /
`strategy` / `strategy_instruction` / `strategy_pool`；源 `thunder_model`
只读账号，目标本机 `quant-cainiao-mysql:3307`，水位表 `_cainiao_pull_state`，
收据 `data/cainiao_pull/receipt.jsonl`。

### 首轮失败根因（不是网络抖动，别按重试处理）

```
stock_realtime_monitor_record  FAIL OperationalError (2013, WinError 10053)
后 5 张表                      FAIL InterfaceError (0, '')
```

pymysql **默认缓冲游标**在 `execute()` 阶段就把整表结果集读进内存，
`fetchmany(BATCH)` 只是本地切片。`stock_realtime_monitor_record` 有
**27252 行 × 32 列**（含 `score_detail` / `signal_reason` 长文本），单条大查询
在传输中被中间链路掐断；源连接是九表共用的，一断之后后续全部连锁 `InterfaceError`。
实测按主键分段 `LIMIT 500` 只需 **0.06s/段**，证明不是数据量不可行，是读取方式不对。

### 修法（`quant_backtest/cainiao_pull.py`）

1. **主键 keyset 分段**：`WHERE (窗口) AND id > last_id ORDER BY id LIMIT 500`，
   读一段写一段；无 `id` 时退回目标主键，都没有才退回一次性读取。
2. 源表缺 `update_time` 时自动把水位清空退全量（upsert 幂等，不漏数）。
3. **单表失败自动重连重跑 1 次**：连接类异常关源连接、下一轮惰性重建，
   目标连接 `ping(reconnect=True)`，整表已 `rollback`，杜绝连锁失败。
4. `finally` 兼容 `src is None`。

### 验收

| 表 | 源 | 镜像 | |
|---|---|---|---|
| fund_anomaly | 42 | 42 | OK |
| position_daily | 16995 | 16995 | OK |
| account_daily | 6733 | 6733 | OK |
| realtime_monitor_record | 27252 | 27252 | OK |
| member_instruction | 24715 | 24715 | OK |
| member_real_order | 7002 | 7002 | OK |
| strategy | 19 | 19 | OK |
| strategy_instruction | 2243 | 2243 | OK |
| strategy_pool | 1181 | 1181 | OK |

`--apply` 9/9 `rc=0`，本轮补 **62393** 行；水位 9 条，源时钟 13:07:34。
`dryrun` 9/9 `rc=0`（增量时前三表 0 行，符合预期）。

**遗留（未擅自处理）**：`\CainiaoPull`(19:00) 任务仍 `NO_TASK`。本轮批准范围是
「重写脚本」，排班未批；脚本已就绪，需排班时另批。

## 四、CryptoSync 恢复：定性修正与落地

### 为什么不直接 `Enable-ScheduledTask`

- `crypto_sync.py::_enabled()` 默认 `0`，判例 **WO-QT-20261021-094**：
  CRYPTO 已并入统一供给层 `zhice-db v2_bar_daily`，直连 Binance 属重复搬运，
  与 WO-085「量化所有 Agent 一律在情报部门读数据」冲突。
- 直接启用旧 wrapper 的后果：每天打印 `CRYPTO SYNC DISABLED` 后 `return 0`
  → **任务 rc=0、调度器全绿、数据一行不动**。与 S4U 空转同类，是最难发现的假绿。
- `docs/audit/2026-11-02_manifest缺失4表诊断.md` §2.2 早已定性：
  「全任务扫描未发现任何任务调用 `export_us_hk.py --market CRYPTO`」
  → 供给层替代**只改了代码路径、没接线任务**，数据自 10-21 冻结。

### 本轮动作

1. 供给层补采：`export_us_hk.py --market CRYPTO` → 37 对 parquet 已到
   **20261105**（145 根/只），`_active.json` `active=37/37`，
   `new=0 redo=0 upd=37 skip=0 fail=0`。
2. wrapper 改接：`start_crypto_sync.ps1` 备份为
   `.bak-20261105-crypto-export`，python 调用改为
   `export_us_hk.py --market CRYPTO --batch 200`，stdout/err 改
   `C:\Temp\crypto_export_*.log`，失败告警沿用 `crypto_sync` 名以免底册失配。
   试跑 `wrapper_rc=0`、`crypto_export(CRYPTO) exit=0 2026-11-05T13:11:56`。
3. 任务 XML 备份：`C:\aios\state\CryptoSync_task_backup_20261105.xml`（UTF-16）。
   **任务定义（Action / Trigger / Principal）未改**，只改状态。
4. `Enable-ScheduledTask` + `Start-ScheduledTask` 验证：
   `State=Ready`、`LastTaskResult=0`、日志 `crypto_export(CRYPTO) exit=0 13:12:36`、
   `NextRunTime=2026/11/6 09:00`、`Missed=0`。
5. 登记转正：按 `agent_platform/catalogs/data_lifecycle.yaml` 原注释约定
   「供给层接线完成后删除本条恢复 active」，删除 `crypto_daily` deprecated 条目；
   重跑 `build_catalog.py` + `registry_rebuild.py --apply`。
   最终 `catalog.json` 中 `crypto_daily.lifecycle=None`（active）、`freshness=20261105`、
   `files=37`、`verification_status=verified`；catalog 与 registry 的
   `catalog_sha256` 均为 `fe885e8a…fd2`，`MATCH=True`。

### 环境坑（附带记录）

`C:\项目\py312\python.exe` 的 `sys.flags.safe_path=True`，**忽略脚本目录与
PYTHONPATH**，跑 `build_catalog.py` 必报 `ModuleNotFoundError: linreg`。
`registry_rebuild.py` 不受影响（无同目录 import）。本仓 `build_catalog.py`
要用 `C:\Program Files\Python311\python.exe` 跑。第一次 13:15 组合命令里
build 失败但 registry 照常写盘，已于 13:19 补跑并校验一致。

### L2 五要素留痕（K-STOCK-416 / AGENTS §7）

- **命令全文**：`Enable-ScheduledTask -TaskName 'CryptoSync'`；
  验证性触发 `Start-ScheduledTask -TaskName 'CryptoSync'` 与
  `Start-ScheduledTask -TaskName 'K260RegressionDaily'`。
  任务 XML 未修改，仅状态 + 同目录 wrapper 文件内容。
- **影响面**：每天 09:00 增加一次 `export_us_hk --market CRYPTO`，只写
  `data/crypto/*.parquet` 与 `_active.json`；不碰 Neo4j / PostgreSQL / 生产库；
  失败走飞书 `alert_fail_wrap.py crypto_sync`。`miss_check` 底册中
  `CryptoSync` 期望时刻已于上一轮校准为 09:00，故不新增误报。
- **不可逆点**：无。任务 XML、wrapper、catalog.json 均有备份；
  registry 自带 `.bak-20261105-131947`。
- **替代方案与已尝试**：① 直接启用旧 wrapper → 实测会假绿空转，排除；
  ② 新建独立导出任务 → 改动面更大且任务数增加，排除；
  ③ 并入 `\ChanlunScanUS` 链 → CRYPTO 与 US 扫描耦合，排除；
  ④ 保持 Disabled 只手动补采 → 数据会再次冻结，排除。
- **回滚预案**：
  `Disable-ScheduledTask -TaskName 'CryptoSync'`；
  `copy start_crypto_sync.ps1.bak-20261105-crypto-export start_crypto_sync.ps1`；
  必要时 `Register-ScheduledTask -Xml (Get-Content C:\aios\state\CryptoSync_task_backup_20261105.xml -Raw) -TaskName 'CryptoSync' -Force`；
  yaml 恢复原条目后用 Python311 重跑 `build_catalog.py` + `registry_rebuild.py --apply`。

## 五、遗留 / 待裁定

1. `\CainiaoPull`(19:00) 任务不存在 —— 脚本就绪，排班需另批。
2. `_k260_regression_task.log` 编码混杂（PS 5.1 `*>>` 写 UTF-16LE、
   Python/`Out-File -Encoding utf8` 写 UTF-8），不影响判定、影响人读，未修。
3. `registry_rebuild` 输出中「catalog 中已不存在的旧条目（保守保留）」约 48 条，
   是既有保守策略，本轮未动。
