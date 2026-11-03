# 学习结论 API 接线验收报告（W3-3b，2026-11-03）

## 路由
- `GET /api/learning/conclusions`（8091；同源 8734 反代可达）
- 入口：`portal/portal_api.py` do_GET 早分支 **+5 行**（含注释），逻辑全部在独立模块 `portal/api_learning_conclusions.py`（portal_api 只改不扩）。
- 路由基线：`python tools/portal_route_inventory.py --write-baseline` → 293 条（新增 1 条），`--check` 零 diff。

## 参数与响应
| 参数 | 取值 | 默认 | 校验 |
|---|---|---|---|
| confidence | 高/中/低 | 全部 | 非法 → 400 INVALID_ARGUMENT |
| executability | A/B/C（level） | 全部 | 非法 → 400 |
| tactic | 缠论/均线/指标/量价/形态/风控/心理/量化/未分类 | 全部 | 非法 → 400 |
| limit | 1..100 | 100 | 越界/非整数 → 400 |
| offset | ≥0 | 0 | 负数/非整数 → 400 |

成功响应：`{ok:true, total:<过滤后总数>, items:[Top100 条目], query:{...规范化回显}, schema_version, generated_at}`；
数据源 `portal/static/learning_conclusions.json`（只读，mtime 缓存）；文件缺失 → 503 NOT_READY；内部异常 → 500 INTERNAL。

## HTTP 冒烟（重启后 8091 实测）
- 重启方式：写 `C:\Temp\PORTAL_API_RESTART.flag` + 触发计划任务「AI工作站门户API」；旧 PID 45552 → 新 PID 46968，cold-start guard OK，flag 已消费（未手动 kill）。
- `limit=5` → **200** `{ok:true,total:100,items:5,query:{limit:5,offset:0,...}}`
- `confidence=高&limit=3` → **200** `{total:5,items:3,query.confidence:"高"}`
- `tactic=缠论&limit=2` → **200** `{total:6,items:2}`
- `executability=A&limit=2` → **200** `{total:84,items:2}`
- `limit=0` / `confidence=超高` / `offset=-1` → **400 INVALID_ARGUMENT**（错误码与 message 正确）

## 测试
- `tools/tests/test_api_learning_conclusions.py`：**6 passed**（过滤 3×3+组合/分页/400 参数 8 例/schema+query 回显/503 降级/mtime 缓存重载）；
- ruff：`All checks passed!`；py_compile：OK；
- route inventory：293 条与基线零 diff。

## 回滚
1. 从 `portal/portal_api.py` 移除 `if path == '/api/learning/conclusions':` 5 行分支；
2. 删除 `portal/api_learning_conclusions.py`（或保留不用）；
3. 重跑 `python tools/portal_route_inventory.py --write-baseline`（293→292）；
4. 写 `C:\Temp\PORTAL_API_RESTART.flag` + 触发计划任务重启 8091 生效；
5. 前端如需静态兜底：继续用 `/static/learning_conclusions.json`（契约不变）。

## 说明
- 数据只读（Top100 静态导出），未改 `export_conclusions.py`/`learning_conclusions_lib.py`；
- 中文参数需 URL 编码（HTTP 客户端自然处理）；服务端 UTF-8 解析正常。
