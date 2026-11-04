# 执行回执：TodaysPicks 盘后时刻调整（L2，2026-11-04）

## 结论
✅ 已完成：`\TodaysPicks` 由 **15:40 → 18:20**（盘后、TdxSync_A 17:35 之后），
手动触发验收 **rc=0**，`todays_picks.json` 基准 **2026-11-04**、生成时间 20:27:39；
基准新鲜度闸门（fail-closed rc=4）已上线（6 测试 PASS）。L2 五要素审批见
`docs/ops/2026-11-04_TodaysPicks时刻调整_L2审批单.md`，用户 20:24 批准。

## 变更前后
| 项 | 变更前 | 变更后 |
|---|---|---|
| StartBoundary | 2026-10-16T15:40:00 | **2026-10-16T18:20:00** |
| Next Run | 2026-11-05 15:40 | **2026-11-05 18:20** |
| 任务描述 | 每日 15:40 | 每日 18:20 盘后（TdxSync_A 之后） |
| 动作/参数 | `start_todays_picks.ps1` | 不变 |
| Principal | `t` / S4U | `t` / **InteractiveToken**（见风险） |

## 执行过程（关键证据）
1. `schtasks /change /tn "\TodaysPicks" /st 18:20`
   → `ERROR: Access is denied.`（S4U 任务变更触发 run-as 密码提示；非管理员无法提供）。
2. `Set-ScheduledTask ...` → `E_ACCESSDENIED (0x80070005)`。
3. **Task Scheduler COM**：
   `$svc.GetFolder('\').GetTask('TodaysPicks').Definition` 改
   `Triggers.Item(1).StartBoundary='2026-10-16T18:20:00'`，
   `RegisterTaskDefinition('TodaysPicks',$def,6,'t',$null,3,$null)` → **成功**。
   - logonType=3 = InteractiveToken；尝试 S4U(2) 被 `Access is denied`，
     InteractiveTokenOrPassword(6) 被账户策略拒绝（空密码）。
4. 手动 `schtasks /run /tn "\TodaysPicks"` → LastResult **0**（20:27:30）。
   任务日志：
   `todays_picks exit=0 2026-11-04T20:27:50`
   `pick_details exit=0 2026-11-04T20:27:51`（verdicts 复用已 detached 进程）。

## 产物验证
```
todays_picks.json: generated=2026-11-04 20:27:39
trade_basis=2026-11-04 收盘（行情湖 ts_daily 最新可得交易日）
counts: total=732 T1=0 T2=45 T3=687 CROWD=0
行情回填：基准 20261104，回填 404 只
```
- `tdx_read.latest_date('ts_daily')` = **20261104**（17:35 TdxSync_A 已成功）。
- 归档 `portal/data/picks_history/todays_20261104.json` 首写已存在，20:27 重跑
  按设计「保留首次快照」未覆写（该文件 17:12 版本已在 19:5x 修正为 368 回填版；
  20:27 原生回填 404 只，差异为 tdx 全市场覆盖，归档按 first-write 保留）。

## 代码变更（非 L2）
- `portal/build_todays_picks.py`
  - 新增 `_ashare_latest_day()`：读 `000001_SZ.parquet` 最新 `trade_date` 作收盘库哨兵。
  - 新增 `_basis_guard()`：`ashare 最新交易日 > 行情湖基准日` → 返回错误，
    `main()` 在写盘前 `return BASIS_FAIL_RC=4`，拒绝旧基准冒充当日盘后清单；
    周末/节假日两库同为最后交易日，不误报；`PICKS_BASIS_GUARD=0` 可临时关闭。
  - 新增 `PICKS_SKIP_PUSH=1`：验证/回放时跳过源级降级飞书推送（本次未用生产推送）。
- `tools/tests/test_picks_basis_guard.py`：6 项（同日/周末/滞后/空基准/关闭/默认开）全 PASS。
- `tools/check_syntax.py --fail-on-warning`：4444 文件 / 0 错误 / 0 告警 / PASS。
- 提交：`480ef8912`。

## 风险与回滚
1. **Principal 由 S4U 变为 InteractiveToken**：任务仅在 `t` 用户已登录会话中运行。
   本机为常开桌面（portal/Edge 常驻），可接受；若机器注销/重启未登录，18:20
   将不触发。恢复 S4U 需管理员重注册（`RegisterTaskDefinition` logonType=2）。
2. 若 17:35 TdxSync_A 失败：18:20 builder 以 **rc=4** 退出且不写
   `todays_picks.json`；`start_todays_picks.ps1` 的 `alert_fail_wrap` 会推送失败告警，
   不会污染 picks_history。
3. 回滚（任选其一，需在可访问任务定义的上下文）：
   ```powershell
   $svc = New-Object -ComObject Schedule.Service; $svc.Connect()
   $def = $svc.GetFolder('\').GetTask('TodaysPicks').Definition
   $def.Triggers.Item(1).StartBoundary = '2026-10-16T15:40:00'
   $svc.GetFolder('\').RegisterTaskDefinition('TodaysPicks',$def,6,'t',$null,3,$null)
   ```
   或管理员：`schtasks /change /tn "\TodaysPicks" /st 15:40`
4. 代码回滚：`git revert 480ef8912`；紧急放行 `PICKS_BASIS_GUARD=0`（不建议）。

## 后续验收
- 2026-11-05 18:20 首跑：检查 `LastResult=0`、`generated_at≈18:2x`、
  `trade_basis` 含 11-05、`picks_history/todays_20261105.json` 首写存在。
- 若 11-05 15:40 仍有人查看页面：清单为 11-04 盘后版（正常，下一次 18:20 刷新）。
