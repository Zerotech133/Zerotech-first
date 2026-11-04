# L2 审批单：TodaysPicks 盘后时刻调整（2026-11-04）

## 1. 事由（What/Why）
`\TodaysPicks` 每日 **15:40** 运行，但行情湖 `ts_daily` 的当日收盘由
`\ZeroBound\TdxSync_A_高频` **17:35** 才同步；`\BtScan` 16:00 才刷新
`rps_latest`。因此 15:40 生成的 `todays_picks.json` 基准恒为 **D-1**，
11-04 当日实测：15:40 产物 basis=2026-11-03；17:35 同步后 `tdx_read.latest_date("ts_daily")=20261104`。

目标：让「盘后选股」使用 **当日收盘**（Basis=D），并保留 fail-closed 闸门，
避免上游同步失败时旧基准清单冒充当日清单。

## 2. 命令全文（拟执行）
```powershell
schtasks /change /tn "\TodaysPicks" /st 18:20
```
- 仅改开始时刻，任务动作/程序/参数不变：
  `powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\aios\portal\start_todays_picks.ps1"`
- 不新增任务、不改触发器类型（daily）、不改运行账户/权限。
- 选 18:20 的原因：晚于 TdxSync_A 17:35、B_财务 17:45、C_低频 17:55、
  D_缺口 18:05 全部结束，且不落入 merge 禁并期（17:10–18:40 只约束合并，
  本任务不参与代码合并）。

## 3. 影响面（Impact）
- 每日盘后清单由 15:40 生成改为 18:20 生成（页面当日清单在 18:25 前刷新）。
- 15:10 的 `PickMonitor`、16:00 的 `BtScan` 不受影响；不改服务/端口/数据库。
- 数据写入路径不变：`portal/data/todays_picks.json`、
  `portal/data/picks_history/todays_<D>.json`（首写锁定，18:20 首写 D 基准）。
- 下游 `build_pick_details --tier T1`、`build_pick_verdicts` 仍由
  `start_todays_picks.ps1` 串联；T1 当前=0，详情/后验为空跑。
- 若 17:35 同步失败：新增 `_basis_guard` 在写盘前 fail-closed，
  退出码 **4**，`alert_fail_wrap` 会告警且不写旧基准清单（已实测 rc=0 正常路径）。

## 4. 不可逆点
- 计划任务开始时刻持久化变更（可回滚）。
- 15:40–18:20 窗口内页面看到的是前一交易日基准清单（数据本身仍正确，
  只是非当日盘后）；18:20 后归档 `todays_<D>.json` 一旦首写即锁定，
  重跑不覆写（既有设计，非本次引入）。

## 5. 替代方案与已尝试
1. **保留 15:40 + 新增 18:20 第二任务**：两次运行、双份告警/详情/后验，
   运维面与噪音翻倍；未采用。
2. **wrapper 内轮询等待 17:35 数据**：任务会连续占用调度窗口约 2.7h，
   失败语义被长等待掩盖；未采用。
3. **builder 内置 ashare 全市场回填**：需遍历 5,600 个 parquet 或改批量
   导出链路，今日只需修时序；未采用（本次仅加 ashare 单票哨兵闸门）。
4. 已尝试/完成（非 L2）：基准新鲜度闸门 + 6 项单测（PASS）、原生 builder
   在 20:19 跑通 rc=0、basis=2026-11-04、回填 404 只；未改任何计划任务。

## 6. 回滚预案
```powershell
schtasks /change /tn "\TodaysPicks" /st 15:40
schtasks /query /tn "\TodaysPicks" /v /fo LIST   # 验收 Next Run=次日 15:40
```
代码回滚：`git revert <本次提交>`；`PICKS_BASIS_GUARD=0` 可临时关闭闸门
（不建议生产长期使用）。

## 7. 验收与证据
- [ ] `schtasks /query /tn "\TodaysPicks" /v`：Next Run = **2026-11-05 18:20**
- [ ] 手动 `schtasks /run /tn "\TodaysPicks"` 后：`todays_picks.json`
      `trade_basis` 含当日日期、`generated_at` 为 18:2x、rc=0（或 T1=0 的 rc=0）
- [ ] `tdx_read.latest_date("ts_daily")` == ashare 最新交易日
- [ ] 归档 `picks_history/todays_20261105.json` 首写存在且 basis=11-05
- [ ] 告警通道：故意停 TdxSync_A 后手动跑，builder rc=4 且不写文件（可选演练）

## 8. 审批
- 审批人：老田
- 审批时间：待填
- 结论：待批
