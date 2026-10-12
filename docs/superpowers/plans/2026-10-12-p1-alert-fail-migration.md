# K-STOCK-180 P1 告警入口迁移实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将受控源码中的告警直调用统一迁移到 `news_collector/alert_fail_wrap.py`，保留任务名、退出码和日志路径语义，并让 P1 审计器能准确证明直调用为零。

**Architecture:** 包装器继续作为唯一观测入口，内部相对调用现有 `quant_backtest/alert_fail.py`；启动脚本只负责把任务结果和日志路径传给包装器。审计器通过匹配实际 Python 调用而非注释/文档字符串，避免把说明文字误报为直调用。所有改动在独立 worktree 中完成，生产告警器本体不改。

**Tech Stack:** PowerShell、Python 3.11、pytest、现有 `news_collector.alert_fail_wrap` 与 `tools.audit_p1_observability`。

## Global Constraints

- 不修改 `quant_backtest/alert_fail.py` 的业务行为。
- 不运行调度、训练、模拟成交或交易；只读审计和离线测试。
- 保留原 task、退出码、日志路径和失败分支语义；包装器异常不得反噬调用方。
- 所有长输出落到 `work/`；源码改动必须带 `K-STOCK-180` 提交信息。
- 合并前必须通过 Mixin、导航、端口三道评审门。

## Review Focus

- PowerShell 中硬编码路径、`Join-Path` 相对路径和 Python `subprocess` 路径都必须指向包装器。
- 组合退出码（如 `fut_daily`）必须原样传递给包装器。
- 仅出现注释或说明文本的文件不应被审计器计为直调用。
- 包装器缺失、日志不存在或发送失败时仍应留下可复核字段，且不改变调用方返回码。

### Task 1: 告警直调用迁移与审计回归

**Files:**
- Modify: `tools/audit_p1_observability.py`
- Modify: `news_collector/test_alert_fail.py`
- Modify: 所有受控源码中实际调用 `alert_fail.py` 的 `.ps1/.py/.cmd/.bat/.xml` 文件（由审计器列出的 44 个文件）
- Test: `tools/test_audit_p1_observability.py`
- Test: `news_collector/test_alert_fail.py`

**Interfaces:**
- `audit(root: str | Path) -> dict` 继续返回 `alert_fail.direct_file_count`、`direct_files`、`wrapper_file_count` 和 `coverage_gap`。
- 新增内部匹配函数只识别 Python 可执行调用（路径后跟 task 参数），忽略注释、文档字符串和说明文本。
- 所有迁移入口调用 `news_collector/alert_fail_wrap.py`，参数顺序保持 `<task> <exit_code> [log_path]`。

- [x] **Step 1: 写失败测试**
  - 在 `tools/test_audit_p1_observability.py` 加入临时项目样例：注释中的 `alert_fail.py` 不计数；实际 `python alert_fail.py task 1 log` 计数；`python alert_fail_wrap.py task 1 log` 不计数。
  - 在 `news_collector/test_alert_fail.py` 加入启动器扫描断言：`gzh/start_gzh_writer.ps1`、`portal/start_portal_api.ps1`、`quant_backtest/start_fut_daily.ps1` 均包含 `alert_fail_wrap.py` 且不包含实际 `quant_backtest/alert_fail.py` 调用。
- [x] **Step 2: 运行失败测试**
  - `python -m pytest tools/test_audit_p1_observability.py news_collector/test_alert_fail.py -q`
  - 预期新增断言失败，当前审计仍报告 44 个直调用。
- [x] **Step 3: 实现最小修复**
  - 在审计器中用逐行规则匹配命令调用，先去掉 PowerShell 注释和 Python 注释/字符串后再识别 `alert_fail.py`，并排除 `alert_fail_wrap.py`。
  - 将所有实际调用路径迁移到 `news_collector/alert_fail_wrap.py`；相对路径使用脚本根目录拼接，硬编码部署路径保持 `C:\aios\news_collector\alert_fail_wrap.py` 结构。
  - 把代码注释/文档中的旧文件名改成“告警包装器”或 `alert_fail_wrap.py`，避免审计结果混淆。
- [x] **Step 4: 运行聚焦测试并核对审计**
  - `python -m pytest tools/test_audit_p1_observability.py news_collector/test_alert_fail.py -q`
  - `python tools/audit_p1_observability.py --root . --output work/p1_observability_kstock180.json`
  - 预期 `alert_direct=0`、`alert_wrapper` 大于等于 44、`coverage_gap=false`；market 和三源证据字段仍按真实状态返回，不能人为改绿。
- [ ] **Step 5: 运行相关全量测试并提交**
  - `python -m pytest news_collector/test_alert_fail.py tools/test_audit_p1_observability.py -q`
  - `git add tools/audit_p1_observability.py tools/test_audit_p1_observability.py news_collector/test_alert_fail.py gzh portal quant_backtest`
  - `git commit -m "fix(K-STOCK-180): migrate alert failure calls to observable wrapper"`

### Task 2: 评审门与可复核报告

**Files:**
- Output: `work/p1_observability_kstock180.json`
- Modify: `.worktrees/tasks.json`（由 `devtask.py merge` 更新）

- [ ] **Step 1: 运行三道评审门**
  - `python tools/_mixin_integrity.py --check`
  - `python tools/unify_nav.py --check`
  - `python tools/check_ports.py --strict`
- [ ] **Step 2: 运行 P1 审计报告**
  - `python tools/audit_p1_observability.py --root . --output work/p1_observability_kstock180.json`
  - 保存真实结果；若 market、调度或三源证据仍阻断，报告保持阻断，不伪造证据。
- [ ] **Step 3: 合并**
  - 在主仓执行 `python tools/devtask.py merge K-STOCK-180`，仅在三道评审门全绿后合并。

