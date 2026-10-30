# A5 B1b `_portal_page` 解耦拆分 · 执行回执 v1（2026-10-31）

- 工单：`WO-GUANYU-P112-20261031`
- 依据：`docs/plans/2026-10-31_portal_api拆分提案_v1.md` B1b（B1 批因依赖未移出的 `_portal_page`）
- 性质：**纯移动 + 依赖解耦**（方法体零改动；仅 import 与缓存归属调整）

## 一、解耦内容

| 依赖 | 处理 |
|---|---|
| `ROOT / REPORTS_DIR / STATION / _read_json / log_exc / pill_nav` | 从 `api_common` 直接 import（原经 portal_api 中转） |
| `org_command_api` | 新模块直接 `import org_command_api` |
| `_HERMES_UP_CACHE`（K-STOCK-101 探活缓存） | **定义移入** `api_pages_portal.py`；portal_api 已无其他引用，删除原定义 |
| `_COCKPIT_CSS`（Handler class attr）、`_license_state`（api_misc 方法）、`_html`（同模块） | 经 MRO 解析，无需移动 |

## 二、结果

| 项 | 变化 |
|---|---|
| `portal/portal_api.py` | **4,068 → 3,853 行（−215）**；**A5 累计 5,153→3,853（−1,300，−25%）** |
| `portal/api_pages_portal.py` | 459 → **679 行**（B1 的 8 个方法 + `_portal_page` 214 行） |
| 行数预算 | 5,200 → 余量 **1,347 行**（已达 A5 提案第一阶段目标 4,000 以下） |

## 三、验证矩阵

| 检查 | 结果 |
|---|---|
| `py_compile` | ✅ |
| `ruff F401/F821/F811` | ✅ All checks passed |
| `_mixin_integrity --check --root` | ✅ 186 methods / 477 funcs 零 diff |
| `portal_route_inventory --check` | ✅ 279 条路由零 diff |
| `pytest portal/tests` | ✅ **439 passed / 1 xfailed / 1 failed（已知 flaky `test_minute_router_process_restart`，基线内）** |

## 四、A5 进度（更新）

| 批 | 状态 | 行数变化 |
|---|---|---|
| B0 契约/路由基线（279 条） | ✅ | — |
| B1 页面族 | ✅ | −433 |
| B2 健康/观测族 | ✅ | −197 |
| B3 管理/雇佣族 | ✅ | −455 |
| **B1b `_portal_page` 解耦** | ✅ | **−215** |
| **portal_api 现状** | **3,853 / 5,200** | **累计 −1,300（−25%）** |
| B2b `_api_health/_api_jev_stack`（共享锁/缓存） | 待做 | ~−236 |
| B4/B5 路由表化 | 待评审 | ~−800（剩余 do_GET/do_POST 瘦身） |

## 五、回滚

单批 revert 即可（纯移动；`_HERMES_UP_CACHE` 仅本方法使用，回滚同时恢复定义）。
