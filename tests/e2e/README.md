# E2E 场景集（首批）

> 场景目录：`docs/specs/2026-10-27_E2E场景集_v1.md`
> 运行器：`tools/e2e_run.py`

```powershell
python -X utf8 tools/e2e_run.py            # 跑首批冒烟 + artifact
python -X utf8 tools/e2e_run.py --list     # 列出场景
python -m pytest tests/e2e -q              # 直接跑
```

## 首批覆盖

| 文件 | 场景 |
|---|---|
| `test_smoke_services.py` | 8734 / 8091 / 8796 MCP |
| `test_smoke_tools.py` | C6 KPI / 质量分级 / 扩展 lint / 扩展对账 / MCP 安全检查 |

## 说明

- 只读：GET 或工具 `--json/--print-only`；不写生产数据、不发飞书消息、不调 LLM。
- 服务离线 → 测试失败（E2E 预期），不要用 skip 掩盖。
- artifact：`agent_platform/data/dev/e2e/<date>-<ts>.json`。
