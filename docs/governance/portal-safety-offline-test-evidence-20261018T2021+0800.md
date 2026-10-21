# Portal 安全变更离线测试增量证据｜2026-10-18 20:21 +08:00

> 性质：本次定向离线测试的独立时间切片；非生产修复、发布验收、路由批准或 REBASELINE-001 放行。旧版 `portal-safety-change-requests-2026-10-18.md` 和 `evidence-index-increment-2026-10-18.md` 中的 `6 passed / 8 xfailed`、`2 passed / 3 xfailed` 是先前测试版本的历史结果，不覆盖、不原位改写。

## 1. 本次复验

- 工作树：`portal/test_model_endpoint_guard_offline.py`、`quant_backtest/tests/test_ops_today_isolated.py` 和待批安全草案均为 `??`（未跟踪）；限定路径状态未显示 `portal/portal_api.py`、`quant_backtest/ops_signal_today.py` 的本批改动。工作树可能同时存在其他会话的变更；这里没有声称全仓冻结。
- 解释器：`C:/Users/t/.workbuddy/binaries/python/envs/default/Scripts/python.exe`，禁用 pytest 插件自动加载、字节码写入及 pytest cache。两条命令只指定两份离线测试，没有运行生产服务或 HTTP 探针：

```text
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "C:/Users/t/.workbuddy/binaries/python/envs/default/Scripts/python.exe" -m pytest -q -p no:cacheprovider "C:/项目/老田的AI助手/portal/test_model_endpoint_guard_offline.py" "C:/项目/老田的AI助手/quant_backtest/tests/test_ops_today_isolated.py"
结果：24 passed, 14 xfailed in 4.70s；exit 0。

PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "C:/Users/t/.workbuddy/binaries/python/envs/default/Scripts/python.exe" -O -m pytest -q -p no:cacheprovider "C:/项目/老田的AI助手/portal/test_model_endpoint_guard_offline.py" "C:/项目/老田的AI助手/quant_backtest/tests/test_ops_today_isolated.py"
结果：24 passed, 14 xfailed, 1 warning in 5.66s；exit 0。
```

- 优化模式警告为 pytest 的 `PytestConfigWarning`：Python `-O` 下普通 `assert` 不执行。因此 `-O` 结果只辅助检查隔离器使用的显式执行前拒绝门禁；不能凭它证明所有普通测试断言仍有效，更不能声称 AST 指纹是通用安全沙箱。
- 先前独立 QA 对这两份离线测试的稳定快照给出**限定范围 PASS**，正常与 `-O` 均为 `24 passed / 14 xfailed`；本条是主会话 20:20 +08:00 的再次定向复验，不冒充一次新的独立 QA 签字。

## 2. 按变更单解释，而非把 xfail 算成修复

| 待批项 | 离线证据的边界 | 未完成的产品/审批工作 |
|---|---|---|
| SEC-PORTAL-01 | 模型 POST 分派的 AST 隔离回归和合成负面输入；strict xfail 限定已知保护缺口，其他异常须失败。生产门户模块未作为本测试入口导入，未调用真实模型。 | `/api/audit_chat`、`/api/ai_duty` 的目标鉴权、独立频控、读取 body 前字节上限等仍未修复；上游实际保护及调用方契约待 owner 核实。 |
| SEC-PORTAL-02 | `ops_today` 计算与 GET 分派的 AST 隔离回归，文件操作限制在测试临时根；当前 GET 可触发写入的事实哨兵与目标无写/缓存失效判据分开。 | GET 写入语义与同名池内容更新后的缓存新鲜度契约仍未选定/修复；状态与缓存 owner、A/B 方案、并发及兼容性待批准。 |

**14 个 xfailed 是尚未达到目标契约的预期失败，不是 14 个修复通过。** 联合数字不是按单文件的新统计口径，避免将旧文档的两项历史结果与现时联合结果相加。

## 3. 继续阻断与下一门槛

1. 两项安全变更继续 `PROPOSED / BLOCKED / NOT_IMPLEMENTED`；安全草案原状态 `NOT_TESTED` 指产品修复/隔离集成与生产验收尚未进行，不否认本文件所记录的**离线测试本身**已运行。
2. SEC-PORTAL-01 先取得 Portal/API、安全、模型服务/费用 owner 对认证方式、可信代理边界、限额和 body 阈值、调用方兼容、带时区窗口及回滚的书面签认；SEC-PORTAL-02 先由 Portal、量化 state、运维/安全 owner 选定 A/B 契约和写入归属。随后才可在独立隔离环境实施与验收单项产品修复，生产阶段另批。
3. REBASELINE-001 保持正式路由基线 **72 条**、账本继续保全，状态 `DRIFT_DETECTED / HUMAN_REVIEW_REQUIRED / CONTINUOUS_LEDGER_DRIFT / BLOCKED`。不读取/解析/投影或改写两个冻结事实源，不重算 current，不用旧工单代替审批；历史 23 与封存 34 条差异仍未对账。
4. 本记录不修改旧证据索引、待批草案、生产代码、数据库、服务或计划任务；如后续证据变化，再建新的带时间戳增量，不把历史切片改写为当前事实。
