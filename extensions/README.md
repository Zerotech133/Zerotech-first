# 扩展（extensions/）— 声明契约，不是插件市场

> 本目录是 O3 扩展契约的落地：**每个扩展一个目录 + 一份 `manifest.json`**。
> 规则：`docs/specs/2026-10-27_扩展契约_v1.md`；JSON Schema：`extensions/_schema/manifest.schema.json`；校验：`tools/extension_lint.py`。

## 边界（ADR-013）

- 当前**不拆仓、不做插件市场/多租户**；`extensions/` 只是本仓内的声明目录。
- 运行配置的真源仍在原位置（`agent_platform/legion_mcp/servers.json`、`tool_matrix`）；
  manifest 是**契约镜像**，等 lint 稳定后再评估"从 manifest 生成运行配置"（O3 P2）。
- 新增工具/MCP/skill：先加 `extensions/<name>/manifest.json` → 跑 lint → 再写代码/配置。

## 现有扩展

| 目录 | 类型 | 风险 | 说明 |
|---|---|---|---|
| `ro-filesystem/` | mcp | read-only | MCP 只读文件 |
| `ro-git/` | mcp | read-only | MCP 只读 Git |
| `tushare-http/` | mcp | read-only | 外部 Tushare MCP（token_env；scope 待白名单化） |
| `seat-evidence-packet/` | tool | read-only | 田丰证据包 CLI |

## 校验

```powershell
python -X utf8 tools/extension_lint.py            # 人读
python -X utf8 tools/extension_lint.py --json     # 机器读
python -m pytest tools/test_extension_lint.py -q  # 单测
```
