# stock-analyst profile（Hermes 身份面，H1）

个股分析助手身份：SOUL + skills，配合 `stock-analysis` MCP 使用。

## 文件

- `SOUL.md`：角色/边界/工具纪律/输出格式；
- `skills/sa-analyze/SKILL.md`：单股分析流程；
- `skills/sa-data/SKILL.md`：数据面问答（观察池/时效/覆盖/事件/任务健康）。

## 安装（L2，需先批准）

本 profile 属 Hermes 配置变更（L2）：把本目录复制到 `%USERPROFILE%\.hermes\profiles\stock-analyst\`，
并在 Hermes 会话中切换 profile 或按其文档加载。**未经 A0 批准不得直接写入。**

```powershell
# 批准后执行（示例）
Copy-Item -Recurse -Force "C:\项目\老田的AI助手\agent_platform\profiles\stock-analyst" `
  "$env:USERPROFILE\.hermes\profiles\stock-analyst"
```

## 依赖

- MCP server `stock-analysis`（见 `agent_platform/contracts/stock_analysis.v1.yaml`）；
- 网关：`http://127.0.0.1:8734/api/analysis/internal`（token 文件
  `C:\项目\commercial\analysis_internal.token`，本机 loopback）。

## 验收（H1-3）

- 9 个 sa_* 工具在 Hermes 可见；
- 10 个真实问答全部 tool-first、0 编造；
- 回答附数据 as-of/覆盖率与"研究分析，不构成投资建议"声明。
