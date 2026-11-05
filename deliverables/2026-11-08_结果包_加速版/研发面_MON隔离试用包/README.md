# 研发面 · MON 隔离试用包（P6 §三 口径）

> 口径：一个可试用的软件成果（隔离环境）+ 版本迭代 + 测试与返工；证据 = 分支/commit + 测试 rc + 回滚步骤 + 验收回执；**禁生产发布**。
> 状态：可试用（只读 GET，本机 8091）｜生成 2026-11-06 05:20

## 一、成果是什么

MON（监控/告警模块）已随 `K-STOCK-ANALYSIS-MONPAGE-20261104` 合入 master（2026-11-05 21:55:44，merge `d551207e3`）。本包提供**隔离环境只读试用**：本机 8091 门户 API 的 `/api/monitor/*` GET 端点，不触碰生产、不触发写库。

- 引擎：`analysis/monitor.py`（master 版，PIT as_of 口径）
- API：`portal/api_monitor.py`
- 运行实例：127.0.0.1:8091（portal_api.py，进程启动 2026-11-05 21:58:18，晚于 MONPAGE 合入）
- 数据：本地 watchlist（3 只）+ curated parquet + 事件/runs 库（均为本地开发数据）

## 二、老田怎么试（3 条只读命令）

```powershell
# 1) 股票池与汇总
Invoke-RestMethod http://127.0.0.1:8091/api/monitor/pools
Invoke-RestMethod http://127.0.0.1:8091/api/monitor/summary

# 2) 最近告警（watchlist 前 3 条）
Invoke-RestMethod 'http://127.0.0.1:8091/api/monitor/alerts?pool=watchlist&limit=3'

# 3) 一键复现冒烟（只读探针，PASS 则 rc=0）
& 'C:\Program Files\Python311\python.exe' -X utf8 C:\Temp\smoke_monitor.py http://127.0.0.1:8091
```

预期：四个端点均 200；`/api/monitor/pools` 返回 `{ok,pools,summary}`；`summary` 含 `alerts_total,by_severity,last_run,pools`；`alerts` 返回 `{alerts,count,ok}`。

## 三、测试与返工记录（版本迭代）

| 迭代 | 动作 | 结果 | 证据 |
|---|---|---|---|
| 迭代 0 | 首试 8734 端口（旧实例，启动 11-05 20:15，早于 MONPAGE 合入） | 未取到 monitor 响应（非目标实例） | 排查记录见下 |
| 迭代 1 | 改为 8091（portal_api.py `PORT = 8091`，实例启动晚于合入） | **4/4 端点 200，SMOKE PASS，rc=0** | `证据/monitor_smoke_20261106_051557.json` |
| 迭代 2 | 探针脚本改为独立 .py 文件（避免 PowerShell here-string 内联 `python -c` 的语法错误） | 可重复执行 | `证据/smoke_monitor.py` |

返工说明：迭代 0 的失败不是 MON 缺陷，是端口/进程版本判断错误；已固化为「先确认实例启动时间晚于目标合入」的检查步骤。

## 四、回滚步骤

本包**只读**，未写任何库/配置/生产文件，无需回滚。若需停止试用：不调用上述 GET 即可；不重启、不改 8091 进程。

## 五、边界与未覆盖

- 未验证写端点：`POST /api/monitor/run`（dry_run 需要 `X-Portal-Token`）、`POST /api/monitor/pool`（增删股票池）——不在本轮试用范围；如需，另行申请 token 与窗口。
- 禁生产发布；不接真实交易；不构成投资建议。
- 试用验收口径（P6 §三）：老田打开/调用上述端点成功 + 可复现冒烟 rc=0，即视为研发面最小成果验收通过。
