# ADR-011：唯一数据根 + 运行态根分离，`work` 根退役

- **状态**：Accepted（2026-10-27 WORKPATH Phase 1-3 执行后固化）
- **关联**：P2/P7；`analysis/config.py`；`portal/api_analysis.py`；`AGENTS.md` §7

## 背景

历史上有 `C:\项目\work` 承载 analysis/运行态/会话，硬编码散落，搬移后出现断链（models junction、bot `_dsh_remote`、Edge profile 复活旧根）。

## 决定

1. **唯一 live 数据根**：`quant_backtest/data`（KG_DATA_ROOT）；
2. **分析根**：`C:\项目\analysis_work`（env `ANALYSIS_WORK` 覆盖）；
3. **飞书运行态**：`C:\项目\dsh_runtime`（env `DSH_REMOTE_DIR` 覆盖）；
4. **`C:\项目\work` 退役**：新代码禁止引用；测试 tmp 改 `%LOCALAPPDATA%`；搬移只做 Move+manifest；
5. 数据根结构性操作前读 `C:\Temp\kg_sync_task.log`，完成后补记。

## 后果

- 正面：断链面收敛；搬移不反复；测试/运行态可重建。
- 代价：历史文档/脚本仍可能引用旧路径（按 P2 审计分批清）。
- 不做：不搞多数据根/多租户路径。
