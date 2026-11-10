# 验收判据 01：验收会证据接入（WO-HQ-ACCEPT-20261028）
> STATUS: frozen（黄忠验收夹具/样例文件，非计划；2026-10-31 W2 收口）



> 工单：WO-HQ-ACCEPT-20261028「黄忠验收会接入：acceptance evidence 路由 + 纪要 acceptance 字段 + 验收面板」
> 任务提交：d746b3418 · 合并提交：38fd8fc29（merge task/WO-HQ-ACCEPT-20261028）· 2026-10-29 05:03 入 master
> 核对：`git cat-file -t 38fd8fc29` → commit；`git merge-base --is-ancestor 38fd8fc29 HEAD` → 退出码 0（master 祖先，2026-10-30 实测）

## 一、判据表（每条含证据路径/命令 + 通过线）

| # | 判据 | 证据路径/命令 | 通过线（阈值） |
|---|---|---|---|
| 1 | 证据聚合函数在位且只读 | `agent_platform/legion_command/hq_acceptance.py:28`（`def gather(baseline_file, redteam_dir, acceptance_dir)`） | 函数可 import 且 `gather(...)` 返回 dict；不写盘（只 `_read_json`/`_latest`） |
| 2 | HTTP 端点已注册 | `agent_platform/legion_command/serve.py:491` `if path == "/api/hq/meeting/acceptance/evidence":` → 调用 `hq_acceptance.gather` | `GET /api/hq/meeting/acceptance/evidence` 返回 200 且 `ok=true`；`acceptance.verdict` 非空时 ∈ {PASS, REJECT, OBSERVE} |
| 3 | 缺件 fail-visible（不编造） | 同判据 1 的 `warnings` 分支：baseline/redteam/acceptance 缺任一 → 对应 `ok=false` + `warnings` 追加中文原因 | 缺件场景 `ok=true`（外层）且 `warnings` 长度 ≥ 1；不得用默认值伪造通过 |
| 4 | 验收结论入纪要 | `agent_platform/legion_command/hq_meeting.py:16`（`TYPES` 含 `acceptance`）、`:134-155`（解析 `req["acceptance"]` 最多 5 条写入文档 `acceptance` 字段） | 保存 acceptance 类型会议后，落盘 json 的 `acceptance[0].verdict` 等于请求值；`decisions` 归属人保留 |
| 5 | 前端验收面板已接 | `agent_platform/legion_command/static/meeting.html:184`（`hqVerdict` 下拉）、`:424`（`kind == 'acceptance'` 时 `loadHqEvidence()`）、`:497-499`（`fetch('/api/hq/meeting/acceptance/evidence')`） | 页面源码同时含 `hqVerdict`、`hqEvidence`、`loadHqEvidence`、端点路径四标记（字符串匹配，缺一失败） |
| 6 | 单测通过 | `C:\项目\py312\python.exe -X utf8 -m pytest -q -p no:cacheprovider -o addopts= agent_platform/tests/test_hq_acceptance.py` | **5 passed / 0 failed**，rc=0（2026-10-30 实测：5 passed in 1.08s） |

## 二、通过线汇总（可计数）

- 判据 1–5：静态证据逐条 `grep`/源码核对，全部命中=通过；缺任一=退回。
- 判据 6：pytest `passed=5`、`failed=0`、`rc=0`。
- 端到端：`ok=true` 且 `warnings == []`（证据齐备时）；缺件时 `warnings` 非空且 `acceptance.ok=false`（诚实缺件）。
- 阈值口径：测试数 `>=5` 且失败数 `<=0`；源码标记数 `ok=4/4`。

## 三、回归项（修复后不误伤）

| 回归项 | 命令 | 通过线 |
|---|---|---|
| 会议看板/绑定不受影响 | `pytest -q agent_platform/tests/test_hq_p2.py` | 5 passed（2026-10-30 实测） |
| 三套件合计回归 | `pytest -q agent_platform/tests/test_hq_acceptance.py agent_platform/tests/test_academy_role_attrs.py tools/tests/test_task_board_audit.py` | 12 passed（2026-10-30 实测） |
| 证据端点不写生产数据 | 代码审查 `hq_acceptance.py` 全文无 `write_text`、无 `open(..., "w")` | 写操作数 = 0 |

## 四、不可逆点与边界

- 本判据只验读侧证据与纪要写入（`HQ_MEETINGS_DIR` 下会议 json），不改 registry/不改绑定。
- 端点依赖 `work/p1/redteam/` 与 `work/huangzhong/` 产物目录；跨环境复跑时须先备齐被聚合目录，否则只能验缺件分支。
- 回归不含线上 8796 探活（属 E2E 范围，不在本判据内）。
