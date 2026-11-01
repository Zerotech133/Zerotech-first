# team_exec_archive — T1/T2 A/B trace 受控归档（2026-11-01 WO-T1T2-CAPTURE-20261031）

目的：`agent_platform/data/team_exec` 被 `.gitignore` 忽略，实验 worktree 回收后
trace 随之丢失（W1-3/b/c 六个变体 run 教训）。本目录随单提交、可审计，存放
**真实** run 数据，禁止人工编造数字。

采集工具：`python -X utf8 tools/t1t2_capture.py capture --all --json`
（源优先 team_exec 完整 trace；缺失时用 `C:\Temp\run_invest_*.log` 做 log_salvage）

保真度约定：
- `<arm>/<run_id>.jsonl` + `_summary.json`：**full_trace** 原件（structured/duration 完整，可算 JSON 完整率与延迟）；
- `<arm>/<run_id>.salvage.json`：**log_salvage**，console 日志被截断 → `structured`/`duration_ms` 为 null，仅用于状态/样本级审计，**不得**用于 JSON 完整率/延迟。
- `manifest.json`：来源、sha256、采集时间、保真度，采集/审计唯一账本。

消费方：`tools/t1t2_metrics.py`（team_exec 缺数据时自动回落到本归档，仅读 full_trace）。
