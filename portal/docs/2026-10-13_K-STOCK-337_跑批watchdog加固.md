# K-STOCK-337 · 回测跑批子进程 watchdog 加固（335 号观察项收口）

日期：2026-10-13　|　状态：已落地　|　前置：K-STOCK-335

## 一、问题回顾

335 号任务实锤：`_327_paired.py` 跑三臂时，chan_base 子进程在「全市场数据装载 3%」处**无声中断**——stdout 无 traceback、无错误输出、进程退出且 rc=0（疑似 Python 解释器级异常终止），引擎未写产物 json。单独重跑 34s 成功，中断未复现——**偶发、机制未知**。旧产物误判 DONE 的洞已由 335 修复（--force 语义前移先删旧产物），但"子进程挂死不退出也不吐日志"这一形态当时的 `subprocess.run` 完全无防御，10-16 开闸七臂跑批不能赌不出事。

## 二、方案

新增通用 watchdog 模块 `quant_backtest/_run_guard.py`，核心 `run_guarded()`：

- **Popen + 尾随活性监测**：子进程 stdout/stderr 落同一日志文件（与调用方既有 log 句柄共用），守护循环每 2s 轮询 `poll()` + `os.fstat` 读共享句柄文件尾字节数；
- **静默超时杀树**：`quiet_secs` 秒内日志无新增字节且进程未退出 → 判静默挂死 → `taskkill /F /T /PID` 杀整棵树（**经 python subprocess 调**，bash 会吞 `/` 开关）；
- **自动重试一次**：杀灭后自动重跑（attempts 默认 2）；重试仍被杀 → 返回哨兵 `KILLED_RC=-137`，交上层按原 FAIL 判定（rc!=0 或产物缺失）收口；
- **语义保持**：子进程正常退出 rc!=0 时**透传不重试**（真实失败重跑只会浪费且可能非幂等），上层 FAIL/幂等/守卫逻辑零改动；`quiet_secs` 默认 300s 可参数化（335 实测全市场装载远短于此）。

接入（底座最小化改动，仅换调用点）：

- `_327_paired.py`：三臂 `subprocess.run` → `run_guarded(..., label=jid)`；
- `open1016_run.py`：七臂 + `ensure_cache` 的 market_scan 重扫，两处 `subprocess.run` → `run_guarded`。

## 三、注入自测证据

负向注入脚本 `_337_inject_test.py`（跑完已删）注入"stdout 滴流两行后无限挂死"假子进程（复刻 335 形态），`quiet_secs=4` 加速验证。**6 项检查全 PASS**：

```
PASS A1 watchdog 触发两次杀树 -- WATCHDOG 触发行数=2 (期望2=首跑+重试各一次)
PASS A2 返回 KILLED_RC       -- rc=-137 (期望-137)
PASS A3 尝试次数=2           -- used_attempts=2
PASS A4 超时及时             -- elapsed=14.4s (期望约8~28s=两次quiet_secs4s+杀树开销)
PASS B 正常直通              -- rc=0 used=1
PASS C rc!=0 透传不重试      -- rc=3 used=1
```

关键触发日志行（守护日志与子进程输出同流落盘）：

```
[A_hang run_guard] WATCHDOG 触发: attempt 1 静默 4s(阈值 4s) 无日志增长
  -> taskkill /F /T pid=66420 tk_rc=0 成功: 已终止 PID 68300 (属于 PID 66420 子进程)的进程。
     成功: 已终止 PID 66420 (属于 PID 56740 子进程)的进程。 (elapsed 7s)
[A_hang run_guard] attempt 1/2 FAIL rc=-137 (7s) -> 自动重试
[A_hang run_guard] WATCHDOG 触发: attempt 2 静默 4s(阈值 4s) 无日志增长
  -> taskkill /F /T pid=55828 tk_rc=0 ... (elapsed 7s)
[A_hang run_guard] attempt 2/2 FAIL rc=-137 (7s)
```

即：杀树成功（tk_rc=0、子进程树全灭）、重试再杀、两次后返回 KILLED_RC——**watchdog 有牙齿**。对照场景 B/C 证明正常退出零干扰。

## 四、遗留

1. **335 无声中断根因未破**：rc=0 + 无 traceback 的解释器级异常终止机制仍未知（疑与全市场 parquet 装载内存峰值/杀软扫描相关）。watchdog 只保"挂死可杀可重试"，若是"快速退出 rc=0"形态（如本次 335 实录），产物缺失判定兜底（335 已修）+ 人工重跑仍是最后防线。值得挂账：给引擎加关键阶段心跳行（装载进度每 N% 打一行），可同时把静默监测粒度从"整跑批"缩到"阶段内"。
2. **open1016 无 335 式"先删旧产物"前移**：本次按纪律未动其幂等语义（本任务只换 watchdog 包装），首次正式开闸建议 `--force` 跑以规避同类误判。
3. **taskkill 输出 GBK**：已按 bytes 捕获+容错解码（text=True 的 utf-8 reader 线程会炸，自测中发现并修复），守护日志里该段可能显示乱码但不影响判定。
4. 未动 `tdx_sync.py`（改进项待用户拍板）；未跑真实回测（遵守纪律），验证全部走注入测试 + `py_compile` + 三模块 import 冒烟。
