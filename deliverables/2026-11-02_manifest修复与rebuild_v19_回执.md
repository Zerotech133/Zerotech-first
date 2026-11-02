# manifest 语义修复 + report_rc 修复 + rebuild v19 + xtcloud 停更排查 · 回执（2026-11-02 09:00）

> 车道：dwagent-impl / dwgov-impl / wa-govern / wa-quant + Lead 串行 rebuild｜提交：`a0be55395`（manifest 语义）+ 本批

## 一、完成项

| 项 | 结果 | 证据 |
|---|---|---|
| **manifest not_applicable 语义** | ✅ 3/4 表不再误判 missing | `qualify_probe` +4 测试（41→45）；us_legacy 变 evidence_ok；crypto/global_fut 只剩真实 stale |
| **report_rc manifest 修复** | ✅ manifest 补键 + 资质升 ok | 94 files/rows=1,729,477；tushare manifest `report_rc{done,repaired_by}`；qualify apply；unknown→ok |
| **lifecycle 标注** | ✅ us_legacy/crypto_daily → deprecated | `data_lifecycle.yaml` v2（10 条）；rebuild 后 registry 两表 deprecated |
| **build_catalog ts_global_fut manifest 声明** | ✅ catalog v19 | `manifest=data/tdx/_manifest.json`、status=missing（待补表级键） |
| **串行 rebuild** | ✅ catalog v18→**v19**、registry 对齐新 sha | dry-run→apply；备份 `.bak-20261102-085625`；metadata **PASS**、snapshot **code=0** |
| **xtcloud 53 stale 根因** | ✅ 定位 `\TdxSync` 任务整体失效 | DNS errno 11001 + HTTP200 零行软失败 + 全量重跑超 2h 被终止（Last Result=**267014/0x41306**）；33/53 表目录 mtime 停 10-13 |

## 二、资质分布（当前）

| 分级 | 起点 | 现在 |
|---|---|---|
| unknown | 223 | **110** |
| evidence_ok | 0 | **102** |
| evidence_partial | 25 | **34** |
| deprecated | 1 | **3**（+us_legacy/crypto_daily） |
| planned | 7 | 7 |

**K8 路径**：223 → 191 → 172 → 113 → **110**（rebuild 把 2 表转 deprecated、report_rc 升 ok）→ 目标 <40。

## 三、xtcloud 停更根因（task-31，只读诊断）

- **根因**：`\TdxSync`（每日 17:30）整体失效：DNS 解析失败（`errno=11001 getaddrinfo failed`）→ 远端 HTTP 200 零行「软失败」→ 全量重跑超过 2h 被调度器终止（**Last Result 267014 = 0x41306 SCHED_S_TASK_TERMINATED**；日志最后写入 19:29:49≈2h 上限）；
- **33/53 表目录 mtime 停在 10-13**（当天六连跑后中断）；另 ~10 张「文件在写但业务日不前进」；
- `\KgDailySync` 06:20 正常但 22 步**不含 xtcloud 采集**，不能掩盖；
- **修复建议**：① DNS/hosts 修复（需真实 IP + L2）② 拆 2-3 批 `--tables` 防 2h 终止 ③ 恢复后 `--status` 核对 182 键再分批补采 33 张断点 ④ manifest 增 `last_attempt_at/last_success_at/zero_streak` ⑤ 任务级监控（终止场景 wrapper 不告警）⑥ 20 张 freq 标签修正（另立治理单）。

## 四、本批工具/配置改动

1. `qualify_probe.py`：not_applicable 语义（不读 manifest/不回退/不判 missing）+ 4 测试；
2. `data_lifecycle.yaml`：+us_legacy/crypto_daily deprecated（v2）；
3. `build_catalog.py`：ts_global_fut manifest 声明；
4. 数据根：tushare/xueqiu manifest 各 +1 备份；registry 多次备份（.bak-20261102-085625 等）。

## 五、待 L2 / 数据侧

| 项 | 类型 | 说明 |
|---|---|---|
| TdxSync 修复（hosts/DNS + 分批 + 监控） | L2 | 需 DATA1 给远端真实 IP；任务参数调整；补采窗口 |
| 8091 重启（S1-03/S1-04 生效） | L2 | 前批申请单仍待批 |
| dw_snapshot 06:40 / nginx 入口页 | L2 | 待批 |
| 24 张未声明豁免口径 | 治理决策 | 定白名单/补 date_col |
| sqlite 探针扩展（3 表） | 工具开发 | stock_news/notice/report |
| 7 张抽样复核 + 20 张 freq 修正 | 治理 | 另立单 |

## 六、下一步

1. 等 L2 批：TdxSync 修复（补采 33 张断点）+ 8091 重启 + dw_snapshot/nginx；
2. L1：us_legacy 已 deprecated（不再计 unknown）；ts_global_fut 待补表级 manifest 键后复判；
3. 治理单：24 张豁免口径、sqlite 探针、7 张抽样、20 张 freq 修正。