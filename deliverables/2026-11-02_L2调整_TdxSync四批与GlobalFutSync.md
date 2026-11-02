# L2 调整记录：TdxSync 四批错峰 + GlobalFutSync 每日任务（2026-11-02 10:22）

> 依据：老田「按计划实施」+ TdxSync 三批能力核验（task-43）；执行：DSH-Lead

## 一、调整原因（核验发现）

1. **三批覆盖缺口**：A/B/C 三批 54 张 vs 50 张停滞清单仅覆盖 **30 张**，20 张不在任何批（A 批对停滞清单覆盖 0/17，因 A 批高频表本就新鲜）；
2. **共锁静默跳过**：三批共用 `tdx_sync_core.lock`，失败即 `[lock] SKIP` 且 exit 0；原 5 分钟间隔过窄（A 若超 5 分钟 B/C 静默跳过）；
3. **零行假绿**：增量模式无零行闸，Last Result=0 不代表有产出 → 首夜验收必须看 part mtime + manifest rows 增量。

## 二、执行结果（已生效）

| 任务 | 触发 | 表数 | 状态 |
|---|---|---|---|
| `\ZeroBound\TdxSync_A_高频` | 17:35 | 18 | Ready |
| `\ZeroBound\TdxSync_B_财务` | 17:45 | 18 | Ready |
| `\ZeroBound\TdxSync_C_低频` | 17:55 | 18 | Ready |
| **`\ZeroBound\TdxSync_D_缺口`（新增）** | **18:05** | **19**（排除本地自产 dknife_snap） | Ready |
| **`\ZeroBound\GlobalFutSync`（新增）** | **18:15** | 4 品种全量（**禁止 --quick**，会截断历史） | LastTaskResult=**0**（10:17 实测通过） |

**变更**：A/B/C 由 17:35/17:40/17:45 改为 10 分钟错峰（17:35/17:45/17:55）；D 批补 19 张缺口；GlobalFutSync 由 17:50 改 18:15 避开批次窗口。

## 三、回滚

- TdxSync 批次：`schtasks /Change /TN "\TdxSync" /ENABLE` + `schtasks /Delete /TN "\ZeroBound\TdxSync_A_高频" /F`（B/C/D 同）；
- GlobalFutSync：`schtasks /Delete /TN "\ZeroBound\GlobalFutSync" /F`；
- 旧任务 XML 备份：`C:\Temp\tdx_fix_20261102_100832\TdxSync_before.xml`。

## 四、首夜验收清单（今晚 17:35-18:20）

1. 四批任务 Last Result（允许 0，但不作为唯一判据）；
2. 数据级：`data/tdx/<table>` 最新 part mtime > 今日 17:30、manifest rows 增量 >0；
3. 记录预期零行表（_vip×4、fina_mainbz、stock_basic/hk_basic/index_basic/classify/member_all/ths_member、trade_cal/forecast、index_weekly 等）与 D 批结果；
4. GlobalFutSync 18:15 后 ts_global_fut 最新日=当日。