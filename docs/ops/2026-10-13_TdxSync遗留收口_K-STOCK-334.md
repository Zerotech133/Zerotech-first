# K-STOCK-334 · TdxSync 遗留三件收口（2026-10-13 晚·老田双拍板）

> 331 事故处置（portal/docs/2026-10-13_K-STOCK-331_TdxSync增量缺勤事故处置.md）§7 三条遗留，本包全收。

## 一、代理注入落地（拍板 A·根因修复）

- **拍板**：AskUserQuestion 老田选 **A**——ps1 头部注入代理（env 级最小改·不动 tdx_sync.py）。
- **改动**：`quant_backtest/start_tdx_sync.ps1`（= `C:\aios\quant_backtest\` junction 同体·已核另一侧生效）`$env:PYTHONUTF8` 后注入三行：

```powershell
$env:HTTP_PROXY = 'http://127.0.0.1:7890'
$env:HTTPS_PROXY = 'http://127.0.0.1:7890'
$env:NO_PROXY = 'localhost,127.0.0.1'
```

- **机理**：requests 自动吃 HTTP(S)_PROXY env；321/331 两轮实测 rookie 只认 7890（64639 会话代理返回非 JSON、直连超时慢爬）。预期 17:30 触发后 ~40 分钟跑完 155 表（原直连 2h15m 跑不完）。
- **验证**：automation `9e64a2ce`（10-14 18:40 once）三态判据——PASS(d1=20261014 入库) / SLOW(慢爬=代理未生效) / MISS(未触发)。验证结果回填 331 文档并出 K-STOCK-335。
- **未动项（挂账观察）**：ts_share_float 表级硬超时 + 关键大表前置排序（方案 B 范畴）——先观察代理修复后的实际跑批时长，若 40 分钟级稳定则表排序饿死链风险大幅下降，B 降为低优先候选。

## 二、KG-TdxSync 双实例移交知会（拍板 A·不越仓库边界）

**现状取证（10-13 22:35）**：

| 项 | PID 20424 | PID 46620 |
|---|---|---|
| 命令行（PEB 实锤） | `...\envs\default\Scripts\python.exe -X utf8 E:\KGStockAnalysis\tdx_sync.py --all` | 同左 |
| 启动 | 10-13 17:43（与主仓 TdxSync 同批触发） | 同左 |
| CPU-delta（8s 采样×2） | **0.00s** | **0.00s** |
| 状态定性 | 5h+ 零 CPU 增量，大概率卡死在网络等待（ts_share_float 同款：连接不断数据不来·read-timeout 失效） | 同左 |

**风险**：双实例并发写同仓 manifest/表文件=331 事故同款数据竞争；且占上游 rookie 配额。

**移交事项（KG 项目线·E:\KGStockAnalysis 自行处置）**：
1. 杀其一保单例（建议杀 46620；杀前按 331 双铁律先三件套取证）
2. 单例复跑须走显式 7890（env 注入同 §一，本仓 ps1 配方可直接抄）
3. 参考主仓修复：`quant_backtest/start_tdx_sync.ps1` 代理三行 + 331 文档 §5 工具箱四件套可直接复用
4. 若 KG 线无人在管，建议直接停用其 TdxSync 计划任务入口，改每日手动/托管 automation

## 三、状态

- [x] ps1 代理注入（根因修复）
- [x] KG 移交知会（本文件）
- [x] 明晚验证 automation `9e64a2ce`（10-14 18:40）
- [ ] 10-14 晚三态验证回填 → K-STOCK-335
