# Hermes MCP 重载成功 · 回执（dw-governance 已生效）

> 2026-11-02 03:42 ｜ 执行：DSH-Lead ｜ 结论：**/reload-mcp 成功，数据仓 5 个只读工具已在 Hermes 生效**

## 一、执行方式（无需重启）

发现 8787 已由 `start_hermes_bg.py` 升级为 **hermes-webui-next（v2）**，其 `/api/commands/exec` 支持 agent 命令。用本地 trusted-header（`X-Portal-User`）认证后调用：

```powershell
POST http://127.0.0.1:8787/api/commands/exec
Header: X-Portal-User: laotian
Body:   {"command": "/reload-mcp"}
```

**返回（62s）**：
```json
{"output": "Reloaded MCP servers from configuration.\nAdded: d1-research, dw-governance, huangzhong, jev-news, playwright, stock-data, stock-intel-readonly\n77 tool(s) available across 7 server(s)"}
```

## 二、证据

1. **MCP 启动日志**（`~/.hermes/logs/mcp-stderr.log`，2026-11-02 03:41）：
   ```
   ===== starting MCP server 'stock-data' =====
   ===== starting MCP server 'jev-news' =====
   ...
   ===== starting MCP server 'dw-governance' =====   ← 新服务器已加载
   ```
2. **agent 真实调用验证**：新会话直接问「调用 dw_stats」→ 回答：
   - 总条目 **314**、已注册 **265**、已暴露 **72**
   - 领域：应用数据 241 / 资源画像 19 / 学习数据 29 / 平台数据 25
   - 与 `/api/dw/stats` 返回值完全一致 → **工具真实生效，非模型编造**
3. 8787 现运行 pid 46356（03:36:52 由守卫脚本换血，加载新配置 config_version 52）。

## 三、数据仓 5 个只读工具（现在 Hermes 可直接调用）

| 工具 | 用途 |
|---|---|
| `dw_stats` | 数据集统计（314/265/72，分域/类型） |
| `dw_list` | 按域/关键词/登记/入口过滤列出数据集 |
| `dw_search` | 关键词检索数据集 |
| `dw_get` | 按 dataset_id 取元数据 |
| `dw_sample` | 按 dataset_id 取样（sqlite 行/parquet/json/目录） |

## 四、顺带发现（待观察，不阻塞）

- `zhice-selection` MCP 在重载后短时间内出现 **多次重启**（03:41:17/20/24）→ 疑似该 server 崩溃重连循环，建议 DATA1/DEV1 排查；
- `om-metadata` 已启动，但此前 OpenMetadata API 401（token 可能失效），需另行修复；
- `dw-etl` 写版按批准暂缓（当前只有 dry-run 版，未注册）。

## 五、验证命令（可复现）

```powershell
# 在 Hermes WebUI 里问：数据仓有多少数据集？→ 应回答 314
# 或直接看日志
Get-Content "$env:USERPROFILE\.hermes\logs\mcp-stderr.log" -Tail 20 | Select-String 'dw-governance'
# HTTP 侧（已生效）
curl http://127.0.0.1:8091/api/dw/stats
```
