# 2026-10-14 卫生行动留痕（计划 v1.2 §14.6）

执行人：hygiene-dev。纪律：解释器只用 `C:/Program Files/Python311/python.exe`；只移动不删除；产线文件零改动；不推飞书。

## 一、归档统计

| 类别 | 数量 | 目的地 |
|---|---|---|
| 根目录一次性研究脚本 | 12 个 .py | `quant_backtest/tools/research/` |
| 根目录 _v7 数据文件 | 0 个（见"未动清单"） | — |
| C:/Temp 一次性研究日志 | 31 个（30 log/txt/json + 1 task.xml） | `C:/Temp/research_logs_20261014/` |
| 新增文档 | 2 个 | `tools/research/README.md`、本文件 |
| 删除 | 0 | — |

## 二、任务A：根目录脚本归档（quant_backtest → tools/research/）

### 移动清单（12 个，均通过引用核查）

| 原路径 | 新路径 | 引用核查结果 |
|---|---|---|
| qd_backtest_1445.py | tools/research/qd_backtest_1445.py | 产线零 import；仅根目录 test_qd_backtest_1445.py（研究测试，非产线）importlib 引用 |
| qd_variants.py | tools/research/qd_variants.py | 无外部 import（自引用说明文字） |
| qd_gate_variants.py | tools/research/qd_gate_variants.py | 无外部 import |
| chanlun_rotation_v7.py | tools/research/chanlun_rotation_v7.py | 无外部 import |
| chanlun_beichi_grid.py | tools/research/chanlun_beichi_grid.py | 无外部 import；shadow_weekly_report.py 仅引用其**产物** reports/chanlun_beichi_grid.json（产物未动，不受影响） |
| cross_grid_backtest.py | tools/research/cross_grid_backtest.py | 产线 cross_poll.py / gate_shadow_collect.py 仅在注释中对齐其口径，无 import |
| linkage_hitrate_backtest.py | tools/research/linkage_hitrate_backtest.py | 产线 linkage_collector.py 仅注释提及，无 import |
| linkage_resonance_audit.py | tools/research/linkage_resonance_audit.py | 无产线引用 |
| linkage_timing_audit.py | tools/research/linkage_timing_audit.py | 无产线引用 |
| nine_dim_industry_pct.py | tools/research/nine_dim_industry_pct.py | 无外部 import |
| strong_threshold_calib.py | tools/research/strong_threshold_calib.py | 无外部 import |
| qpzm_jev_weight_audit.py | tools/research/qpzm_jev_weight_audit.py | 无外部 import |

research 脚本内部相互引用（linkage_resonance_audit / linkage_timing_audit import linkage_hitrate_backtest）随同目录整体迁移，同目录运行不受影响。

### 未动清单与原因

| 文件 | 原因 |
|---|---|
| shadow_weekly_report.py | 产线四件，纪律不动 |
| gate_shadow_collect.py | 产线四件，纪律不动 |
| linkage_collector.py | 产线四件，纪律不动 |
| linkage_collect_daily.py | 产线四件，纪律不动 |
| _v7_stream_cache.json | **预检不通过，留在根目录**：产线 `shadow_weekly_report.py:行内 P_STREAM = os.path.join(QT, "_v7_stream_cache.json")` 以根目录路径读取 buy_ev 做周报；chanlun_rotation_v7.py / chanlun_beichi_grid.py（本次归档脚本）也以根目录路径引用。按"产线零改动"纪律，移动需改产线路径，故不移动。后续若要迁移，需同步改 shadow_weekly_report.py 的 P_STREAM 与两个归档脚本的 STREAM_CACHE，并在周报跑完后窗口执行 |
| test_qd_backtest_1445.py | 不在归档清单；它 importlib 引用 qd_backtest_1445，qd 脚本归档后该测试原地运行会失败。同为研究产物，建议下次卫生行动与 tools/research 合并或删除 |

## 三、任务B：C:/Temp 日志治理

### 移动清单（31 个 → C:/Temp/research_logs_20261014/，全部今日/近期一次性研究产物）

qd_backtest_1445.log、qd_backtest_1445_hfq.log、qd_gate.log、qd_variants.log、chanlun_beichi.log、chanlun_v7.log、chanlun_v7_w3first.log、cross_grid.log、cross_grid_console.log、linkage_backtest_full.log、linkage_timing.log、nine_dim_err.log、nine_dim_out.log、nine_dim_task.log、nine_dim_task.xml、nine_ind_pct.log、strong_calib.log、strong_calib_run.log、strong_full_timing.log、strong_review_dryrun.json、jev_feed_test.log、jev_find.txt、jev_find2.txt、jev_find3.txt、jev_find4.txt、jev_fix.txt、jev_manual.txt、jev_manual311.txt、jev_reg_update.txt、jev_task_probe.txt、jev_v2_verify.txt

（原路径均为 `C:/Temp/<文件名>`，移动即前缀替换为 `C:/Temp/research_logs_20261014/`，无重命名、无删除。nine_dim_task.xml 为任务定义副本，注册时已拷贝进任务库，移动不影响已注册任务。）

### 未动清单与原因

| 文件 | 原因 |
|---|---|
| jev_oos_eval.log / jev_oos_eval.start / jev_oos_v2_eval.log | **正在被写**：run_oos_eval.py（PID 46000）运行中，v2 日志 mtime 与当前时刻同步刷新；待该轮 OOS 评估结束后可移入同一归档目录 |
| linkage_collect_daily.log / _err / _out、linkage_collector.log | 产线四件（linkage_collect_daily.py / linkage_collector.py）的常态日志 |
| chanlun_scan_*.log、chanlun_export_*.log、chanlun_plot_*.log、chanlun_backfill*.log、chanlun_sample_*.log | 常态产线任务日志（扫描/导出/绘图/回填/抽样） |
| db_direct_err.log | 见下方轮转建议 |
| linkage_page.png | 非日志，用途未明，保守不动 |

### 轮转建议：db_direct_err.log（6.3MB）

- **写入方**：产线 `quant_backtest/data_loader.py`（`_query_direct` 失败时的诊断落盘，`open(r"C:\Temp\db_direct_err.log", "a")`）。它是 jev/review 数据链路的伴生产物：常驻进程 `jev_qd_bridge.py --loop`（PID 56616）及当前 `run_oos_eval.py`（PID 46000）等任何调用 `load_bars` 直连失败都会追加。
- **何时可轮转**：文件按事件打开-追加-关闭，**不持长句柄**，理论上任意时刻 `copy + truncate` 都不丢句柄；但当前 6.3MB 高频增长说明直连持续在失败（回退 docker 查询在工作），建议 ① 等 run_oos_eval.py（一次性长跑）结束后轮转；② 若要根治，需排查 `_query_direct` 失败原因（这才是日志暴涨的根因）。
- **怎么轮转**：PowerShell 两步，不停任何进程：
  1. `Copy-Item C:\Temp\db_direct_err.log C:\Temp\db_direct_err_20261014.log`
  2. `Clear-Content C:\Temp\db_direct_err.log`（或 `Set-Content -Value $null`）
  因写入方每次都是新 open("a")，截断后下一条错误自然从空文件续写，无句柄失效风险。**本次未动该文件**，仅留建议。
