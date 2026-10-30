# 脏文件巡检任务 StartWhenAvailable · L2 五要素批准单 v1（2026-10-30 · 关羽）

> 类型：**计划任务设置修改（宿主机级，L2 需人工批准）**
> 现状：`ZeroBound\CheckDirty2H` 的 `StartWhenAvailable=False`（实测），导致 10-29 19:22/23:22 两次计划因休眠/未登录错过且无补跑。
> 当前状态：任务健康（Last Result=0，Next Run 11:22），本单只改**错过补跑**开关。

## 一、命令全文（批准后照此执行）

```powershell
$t = Get-ScheduledTask -TaskName 'CheckDirty2H' -TaskPath '\ZeroBound\'
$t.Settings.StartWhenAvailable = $true
Set-ScheduledTask -TaskName 'CheckDirty2H' -TaskPath '\ZeroBound\' -Settings $t.Settings
# 验证
(Get-ScheduledTask -TaskName 'CheckDirty2H' -TaskPath '\ZeroBound\').Settings.StartWhenAvailable  # 期望 True
```

## 二、影响面

- 仅影响该计划任务的**错过补跑**行为：机器休眠/未登录错过计划点后，恢复时补跑一次（不并发、不补跑多次）；
- 巡检本身只读（`check_dirty` + 日志/事件层追加），不写数据根、不改 git、不重启服务；
- 无其他任务/服务受影响。

## 三、不可逆点

- **无**（设置可随时改回 False）；不改脚本、不改权限、不改触发器频率。

## 四、替代方案与已尝试

| 方案 | 取舍 |
|---|---|
| 保持现状（不补跑） | 已实证漏 2 次/24h（19:22/23:22），巡检覆盖有洞；不采纳 |
| 提高频率（每 1h） | 增加运行次数/日志噪声，且休眠时同样会漏；不如补跑精准 |
| `WakeToRun=True`（唤醒机器跑） | 会唤醒休眠主机，影响面更大（L2+能耗/打扰）；不采纳 |
| **StartWhenAvailable=True（本方案）** | 只补跑不唤醒，最小改动 |

## 五、回滚预案

```powershell
$t = Get-ScheduledTask -TaskName 'CheckDirty2H' -TaskPath '\ZeroBound\'
$t.Settings.StartWhenAvailable = $false
Set-ScheduledTask -TaskName 'CheckDirty2H' -TaskPath '\ZeroBound\' -Settings $t.Settings
```

## 六、验收

1. `Settings.StartWhenAvailable` 读出 True；
2. 下一次机器休眠→恢复后，日志出现补跑记录（或 Last Run Time 非计划点）；
3. 任务 Last Result 仍为 0，无并发实例（MultipleInstances 保持 IgnoreNew）。

## 七、批准栏（老田填写）

- [ ] 批准修改 `ZeroBound\CheckDirty2H` 的 StartWhenAvailable=True
- 批准人：________  日期：________
