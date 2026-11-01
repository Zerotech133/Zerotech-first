# WP-I3：数据仓统一取数面（HTTP + MCP）· 回执

## 一、交付
| 项 | 内容 |
|---|---|
| HTTP 入口 | `portal/api_dw_registry.py`：`/api/dw/stats`、`/api/dw/list`、`/api/dw/search`、`/api/dw/get`、`/api/dw/sample` |
| 接线 | `portal/portal_api.py` GET 分发新增 `/api/dw/*`（仅 +6 行，portal_api 3615/5200 预算内） |
| 路由基线 | `tools/portal_route_baseline.json`：281→**287 路由**，`--check` 零 diff |
| MCP 服务器 | `agent_platform/tools/dw_governance_mcp.py`：5 个只读工具（dw_stats/dw_list/dw_search/dw_get/dw_sample），JSON-RPC stdio |
| 测试 | `tools/datawarehouse/tests/test_dw_wp_i3.py`：**4/4**（HTTP handler 直调 + MCP stdio 子进程） |

## 二、验证结果
| 检查 | 结果 |
|---|---|
| HTTP handler 直调（stats/list/get/404/400） | ✅ 4 passed |
| MCP stdio（initialize/tools/list/tools/call） | ✅ 返回 dw-governance + 5 工具 + 真实 stats |
| MCP selftest | ✅ tools=5，total=314 数据集 |
| 语法门 | ✅ PASS |
| Mixin 完整性 | ✅ 187 methods/526 funcs 零 diff |
| 导入边界 | ✅ PASS |
| portal 拆分预算 | ✅ 3615/5200（70%） |
| 元数据一致性 | ✅ PASS |
| 路由清单 | ✅ 287 条零 diff |
| datawarehouse 全测试 | ✅ **119 passed**（原 112 + 3 registry + 4 WP-I3） |

## 三、生效条件（重要）
1. **HTTP 路由需重启 8091** 才对外生效——重启属 L2，需老田五要素批准；当前已通过 handler 直调测试；
2. **MCP 需注册到 `~/.hermes/config.yaml`** 并 `/reload-mcp` 或重启 8787（L2）：
```yaml
  dw-governance:
    command: "C:\\Program Files\\Python311\\python.exe"
    args:
      - "C:\\项目\\老田的AI助手\\agent_platform\\tools\\dw_governance_mcp.py"
```
   注：Hermes 用 Python311；sqlite/json/目录样本可用；若 py311 无 pyarrow，parquet 样本会 fail-visible（可换 py312 venv 或补 pyarrow）。

## 四、用法（生效后）
```powershell
curl http://127.0.0.1:8734/api/dw/stats
curl "http://127.0.0.1:8734/api/dw/list?domain=%E5%AD%A6%E4%B9%A0%E6%B4%BE%E7%94%9F&limit=20"
curl "http://127.0.0.1:8734/api/dw/sample?id=yt_dim_video&n=2"
python tools/datawarehouse/dataset_registry_cli.py stats
```

## 五、待办
1. 老田/L2：批准 8091 重启（HTTP 生效）+ Hermes config 注册/重启 8787（MCP 生效）；
2. DATA1：把 `dataset_registry_v1.yaml` 草稿并入 data_catalog v7；
3. 取数入口覆盖：当前 72/314（23%）→ Sprint3 ≥80%（补充 YouTube 语料层与自动化产物取数）。