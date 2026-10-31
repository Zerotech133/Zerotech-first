# 拆分首批回执：api_screen → api_screen_kline（2026-10-31 · P129）

## 结果

| 项 | 变化 |
|---|---|
| `portal/api_screen.py` | **1,387 → 1,062 行（−325）**，低于新文件上限 1,200 → WARN 消除 |
| 新 `portal/api_screen_kline.py` | **350 行**（`_kline_qy` 97 + `_kline_data` 204 + `_ts_daily_prepend` 25 + imports/header） |
| 兼容性 | api_screen 保留 `from api_screen_kline import ...` re-export，旧调用点零改动 |
| 其他 | ruff 清理 4 处未用导入；api_common 仍是唯一 >1,200 WARN（1,741） |

## 验证

- `py_compile` ✅；`ruff F821/F401/F811` ✅；
- **portal 全量测试：526 passed / 1 xfailed**（0 failed，43.5s）；
- `_mixin_integrity --check --root`：187 methods / 489 funcs 零 diff ✅；
- `portal_route_inventory --check`：281 条零 diff ✅；
- `check_portal_split`：portal_api 3,552/5,200 PASS；api_screen WARN 消除（仅剩 api_common 1,741）。

## 回滚

单提交 revert（纯移动，函数体零改动）。
