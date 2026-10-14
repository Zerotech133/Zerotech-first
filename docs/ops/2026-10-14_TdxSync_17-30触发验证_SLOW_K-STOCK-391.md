# TdxSync 计划任务 10-14 17:30 触发验证报告（K-STOCK-331 事故遗留）

- **验证时间**：2026-10-14 18:40 ~ 18:47
- **验证人**：主控自动化（老田指令 K-STOCK-331 遗留验证）
- **判定**：**SLOW**（触发链已恢复、代理注入生效，但大表经 7890 上游不稳，`ts_daily` 湖未到 20261014）
- **提交号**：K-STOCK-391

## 一、验证证据

### 1. 计划任务触发（schtasks 权威数据）

```
任务名:     \TdxSync
上次运行时间: 2026/10/14 17:30:01   ✅ 触发恢复
上次结果:   1                       ❌ 当轮最终退出码非 0
下次运行时间: 2026/10/15 17:30:00   ✅ 调度仍每 1 天启用
要运行的任务: powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\aios\quant_backtest\start_tdx_sync.ps1
停止条件:   运行超过 02:00:00 停止
```

注：`_331_task_state.py` 读到的 DynamicInfo 原始 FILETIME 与 schtasks 口径不一致（解出 09:30:01，疑结构偏移解析误差），以 schtasks 为权威。

### 2. 任务日志时间线（C:\Temp\tdx_sync_task.log）

| 时间 | 事件 | 解读 |
|---|---|---|
| 17:07:33 / 17:10:02 | exit=1（旧格式行） | 17:30 前已有外部链路在跑并失败 |
| 17:10 后 | ERR: out.log 正由另一进程使用 | **并发写日志冲突**——多链路同时抢 out.log |
| 17:35:35 | exit=0（旧格式行） | ⚠️ 来源不明（非 v2 ps1 的 `attempt=N` 格式），在并发冲突期写入，不可作为 PASS 依据 |
| 18:13:10 | LOCK-CLEAR dead pid=56992 | 陈锁自清正常 |
| 18:13:22 / 18:25:34 | attempt=1 exit=1 | v2 启动器首步探测即失败（见 err 日志） |
| 18:27:34 | PID 66396 手动拉起 | 父进程为代理 PS 会话（`& E:\老田的AI助手\quant_backtest\start_tdx_sync.ps1`），**非计划任务链** |

### 3. 失败根因（C:\Temp\tdx_sync_err.log）

```
RuntimeError: GET /api/data/status failed: Expecting value: line 1 column 1 (char 0)
  File "tdx_sync.py", line 78, in _remote_tables
```

远端 `http://rookie.xtcloud-tech.com:48088`（纯 HTTP）返回非 JSON——典型代理/上游返回 50x HTML 或空体。18:13/18:25 两次会话均死于启动探测。

### 4. 代理注入是否失效？——**注入生效，上游不稳**

- `start_tdx_sync.ps1` v2 显式设置 `HTTP_PROXY/HTTPS_PROXY=http://127.0.0.1:7890`（6-8 行）
- `tdx_sync.py:47 SESS = requests.Session()`，trust_env 默认 True → **环境变量代理会被采用**，注入机制本身没有失效
- 7890 端口实测监听中（Test-NetConnection TCP=True）
- 旁证：18:27 会话小表 `[ts_index_weight]`/`[ts_daily_info]`/`[ts_ggt_top10]` 等全部 0~2s DONE——HTTP 链路走得通
- **真正的故障点**：大表翻页链路。`ts_daily` 增量 811 万行 ÷ PAGE 5000 = 1624 页逐页经代理请求，中途返回非 JSON 即整表 ERROR（进度已存，续传机制兜底）。本轮实测：

```
[ts_daily_basic] incremental from 20261013 rows=8070629
[ts_daily_basic] ERROR GET /api/data/table/ts_daily_basic failed: Expecting value... (进度已存，重跑续传)
[ts_daily]       incremental from 20261013 rows=8119198
[ts_daily]       ERROR GET /api/data/table/ts_daily failed: Expecting value... (进度已存，重跑续传)
```

### 5. 进程活性取证（PEB + CPU-delta）

- PID 66396，启动 18:27:34，命令行 `"C:\Program Files\Python311\python.exe" -X utf8 C:\aios\quant_backtest\tdx_sync.py --all`
- CPU-delta（5s 采样）= **0s**，累计 CPU 仅 20s，内存 211MB → 纯网络等待
- out 日志 18:38:51 后停更（卡在 `[ts_trade_cal] full rows=22287` 无 DONE 行 >8 分钟）→ **当前表疑似挂在代理慢响应/无响应等待中**

### 6. 湖水位（quant_backtest/data/tdx/ts_daily/）

- 布局为 `part_NNNN.parquet` 分片（45 片），**非 d1= 日期分区**（任务书口径与实际布局不符，特此更正）
- 最新 part_0044.parquet（10-13 20:56）`max trade_date = 20261013` → **20261014 未入库**
- manifest：ts_daily rows=4,175,758（断点已存，续传成本低）

## 二、三态判定

| 判据 | PASS 要求 | 实测 | 结论 |
|---|---|---|---|
| 17:30 触发 | 有记录 | schtasks 17:30:01 | ✅ |
| LastTaskResult | 0 | **1** | ❌ |
| task log exit=0 | 可信 exit=0 | 17:35:35 行来源不明（并发冲突期旧格式） | ⚠️ 不采信 |
| 湖最新日期 | 20261014 | 20261013 | ❌ |

→ **SLOW**：触发链恢复，但代理通道对大表间歇故障（注入未失效，是上游经 7890 不稳 + 大表 1624 页翻页脆弱性放大），且当前进程已挂起无进展。

## 三、处置建议（待老田确认后手动执行，本轮未杀进程）

1. **杀掉挂起进程**：PID 66396（及其父 PS 会话 58148 若仍在）；确认锁文件 `C:\Temp\tdx_sync.lock` 清理。
2. **显式确认 7890 后重跑**：先 `curl -x http://127.0.0.1:7890 http://rookie.xtcloud-tech.com:48088/api/data/status` 确认返回 JSON，再前台重跑 `start_tdx_sync.ps1`（断点续传，ts_daily 从 20261013 之后续拉）。
3. **根因加固建议**（后续工单）：
   - 大表翻页失败粒度过粗：单页连续 3 败即弃整表，建议页级失败计入进度（offset 前移容错）或降低单页依赖；
   - `_get` 增加对非 JSON 响应体的特征识别（502/504 HTML → 立即切重试退避，别等 60s 超时×3）；
   - 排查 7890 上游节点对该 HTTP 目标的稳定性（大响应中断）；
   - 17:07/17:10 旧格式日志写入者身份待查——今日存在**两条以上链路并发抢 out.log**，建议 v2 锁文件机制对旧链路同样生效。

## 四、遗留疑点

- 17:35:35 `exit=0` 旧格式行的写入者不明（v2 格式应为 `attempt=N exit=`），不排除仍有旧版启动器副本在别处被调用。
- DynamicInfo FILETIME 解析与 schtasks 口径差 8 小时（09:30 vs 17:30），量级恰为时区差，`_331_task_state.py` 解析逻辑建议后续校准。

——本报告为 K-STOCK-331 事故的次日验证件，**事故尚未闭环**，待手动重跑成功且湖到 20261014 后再回填 PASS。
