# S2-08 datawarehouse 测试入 fast 门 · 回执

> 2026-11-02 04:35–04:58 ｜ 工作目录 `C:\项目\老田的AI助手`（master 工作树，未 commit）｜ 任务：共享 task-12（W1/W2 并行开发·车道3）
> 结论：**fast 门配置已接入 datawarehouse（第 7 套件），该套件实跑 163/163 全绿、门禁汇总 New=0**；fast 门整体 rc=1，唯一残留新增失败是**外部回归**（portal 1 项，他线 commit 7fa50baa2 引入，见 §五）。

## 一、改动文件（7 个，只改门禁配置/冻结守卫，0 业务代码）

| # | 文件 | 改动 |
|---|---|---|
| 1 | `tools/test_gate.ps1` | ①`-Suite` ValidateSet 加 `datawarehouse`；②switch 加 `'datawarehouse' { $suites = @('datawarehouse') }`，`fast` 由 6→7 套件；③`$paths` 加 `'datawarehouse' = 'tools/datawarehouse/tests'`（目录套件，独立 pytest）；④头部注释/版本说明；⑤失败解析 `-match` → **`-cmatch`**（见 §四） |
| 2 | `tools/tests/test_test_gate_b1.py` | fast 期望清单加 datawarehouse；加「套件路径 + 目录存在」断言 |
| 3 | `tools/tests/test_test_gate_a5strict.py` | fast 期望清单加 datawarehouse；顺带修既存漂移 `tools_key 13→14`（HEAD 实际 14） |
| 4 | `tools/tests/test_test_gate_org_gate.py` | 同上（fast 清单 + tools_key 13→14） |
| 5 | `tools/tests/test_test_gate_wtcheck_gate.py` | fast 期望清单加 datawarehouse |
| 6 | `tools/tests/test_test_gate_artifact_guard.py` | fast 套件字面量断言加 datawarehouse |
| 7 | `agent_platform/tests/test_gate_coverage_w5.py` | `FAST_SUITES` 加 datawarehouse + 注释（该守卫在 fast/ide 内运行，必须同批同步） |

> 说明：2–7 是「冻结清单」同步（它们精确断言 fast 套件组合，不同步则守卫恒红）；其中 3/4 的 `tools_key 13→14` 经 `git show HEAD:tools/test_gate.ps1` 证实为**既存漂移**（HEAD tools_key 已 14 文件、b1 守卫已按 14/89 断言，a5strict/org_gate 未同步），非本单引入。
> `all` 套件保持不变（夜间全量口径不动）；`weekly_kpi_board.py` 的 FAST_SUITES 属下游 KPI 清单、非门禁冻结，本次**未改**（守写域，建议后续同步）。

## 二、如何加入

- 套件形态：`datawarehouse` = **目录套件** `tools/datawarehouse/tests`（与 kstock/tools 同型），gate 循环里单独一次 `python -m pytest tools/datawarehouse/tests`，不与其他套件混跑（防 sys.path 冲突）。
- fast 组合：`portal + kstock + ide + agent_key + quant_key + tools_key + datawarehouse`。
- 独立可切换：`-Suite datawarehouse` 可单独跑。

## 三、实跑证据（命令 + rc + 通过数）

| # | 命令 | 结果 |
|---|---|---|
| E1 | `python -m pytest tools/datawarehouse/tests -q --timeout=120 -p no:cacheprovider`（改动前基线，04:2x） | **rc=0；158 passed in 10.22s**（当时 HEAD 未含 S1-06 新增 5 用例） |
| E2 | `powershell -NoProfile -ExecutionPolicy Bypass -File tools\test_gate.ps1 -Suite fast`（run1 04:38:31，含 datawarehouse） | rc=1，297.1s；datawarehouse pytest **163 passed in 9.41s**，但被 gate 误判 New=1（见 §四） |
| E3 | 同 E2（run2 04:44:21，修复 -cmatch 后） | rc=1，281.8s；**datawarehouse 行 `0 0 0 0 20.5s`（New=0）**，pytest **163 passed in 9.29s**；其余 7 套件 New=0；唯一新增失败=portal 外部回归（§五）。汇总：`GATE FAIL: 新增失败 1 项` |
| E4 | 6 个门禁冻结守卫：`python -m pytest tools/tests/test_test_gate_b1.py tools/tests/test_test_gate_a5strict.py tools/tests/test_test_gate_org_gate.py tools/tests/test_test_gate_wtcheck_gate.py tools/tests/test_test_gate_artifact_guard.py agent_platform/tests/test_gate_coverage_w5.py -q` | **rc=0；50 passed in 2.31s** |
| E5 | `python tools/check_syntax.py --fail-on-warning`（完成前必跑） | **rc=0；4103 文件 / 错误 0 / 警告 0 / 0.11s；result: PASS** |
| E6 | PowerShell Parser 解析 `tools/test_gate.ps1` + BOM 校验 | `PARSE-OK`；BOM=`EF-BB-BF`（PS5.1 中文兼容保留） |
| E7 | `python tools/merge_gated.py --help` / `--dry-run S2-08-DW-FASTGATE` | `--dry-run` 存在（兼容入口/预检器）；实跑 rc=1 → `H5 钉扎：无法读取 tools/test_gate.ps1（master/分支缺失）→ 拒绝（fail-closed）`。原因：本单为 master 工作树配置改动、未建 `task/<TID>` 分支 |

证据日志：`C:\Temp\s208_fast_gate.log`（run1）、`C:\Temp\s208_fast_gate_run2.log`（run2）、`%TEMP%\test_gate_datawarehouse_20261102_043831.log` / `..._20261102_044421.log`、`C:\Temp\s208_gate_change.diff`（23,404 B，本单 7 文件完整 diff，供审批/回滚）。

## 四、顺带修复：gate 失败解析被 OTel stderr 假红（S2-08 连带暴露）

- 现象：run1 中 datawarehouse pytest 实为 163 passed，但 gate 汇总 New=1，失败项文本是 `to export spans batch due to timeout...`。
- 根因：`tools/datawarehouse/tests/test_otel_tracing.py` 触发 OTel exporter，向 stderr 打印 `Failed to export spans batch due to timeout...`；`test_gate.ps1` 解析用 `-match '^(FAILED|ERROR) '`，PowerShell `-match` **默认大小写不敏感**，`Failed ...` 被误判为失败。
- 修复：改为 `-cmatch`（pytest 短摘要固定大写），并在脚本内留一行注释说明。修复后 run2 datawarehouse New=0。
- 影响面：仅收紧到「大写 FAILED/ERROR 行」，其余解析逻辑不变；50 条冻结守卫测试全绿。

## 五、残留问题（非本单改动，未处置）

| 项 | 证据 | 定性 |
|---|---|---|
| `portal/tests/test_os_entry_ia.py::test_zhice_entry_card` 失败 | 断言 HTML 含 `127.0.0.1:5175`；实际已无该串。独立单跑 `pytest ...::test_zhice_entry_card` **1 failed**（与门禁无关） | **外部回归**：commit `7fa50baa2`（WO-L1-EXEC-20261102，04:40:37）把 `portal/static/nav.js` 智策入口 `http://127.0.0.1:5175` 改为 `/quant/`，但未同步该测试。属他线写域，本单未改 |
| HEAD 在任务期间前进 | `f402cdad1`（04:38:59）新增 `tools/datawarehouse/tests/test_dw_snapshot_daily.py`（+157 行/5 用例）→ 计划书「158 项」在实跑时为 **163 项**；`7fa50baa2`、`ef35cdec5` 等继续推进 | 说明计数差异来源；本回执数字按 run2（04:44:21）时点 |

> 因此「fast 门整体全绿」被上述外部 portal 回归阻塞；**datawarehouse 套件自身与所有门禁守卫均全绿**。建议 Lead 将 portal 测试同步指派给 WO-L1-EXEC 车道（更新断言 `127.0.0.1:5175` → `/quant/` 或按新入口口径改），该修复后 fast 门应整体 rc=0。

## 六、H5 钉扎提示（重要）

`tools/merge_gated.py` H5-a 规则：**任务分支不得修改 `tools/test_gate.ps1`**（与 master 不一致即拒绝合并；质量闸脚本变更需独立审批后由 Lead 手动合入 master）。本单按任务要求只做 master 工作树改动、**未 commit**；落地路径应为：Lead 审批后把本单 7 文件改动直接提交 master（或按治理道例外流程），其余分支 rebase 后生效。

## 七、约束与回滚

- 未 commit、未重启服务、未触发生产 merge/发布；未改业务代码（改动仅 7 个门禁/守卫文件）。
- 回滚：`C:\Temp\s208_gate_change.diff` 为完整可审 diff；批准后 `git restore -- tools/test_gate.ps1 tools/tests/test_test_gate_b1.py tools/tests/test_test_gate_a5strict.py tools/tests/test_test_gate_org_gate.py tools/tests/test_test_gate_wtcheck_gate.py tools/tests/test_test_gate_artifact_guard.py agent_platform/tests/test_gate_coverage_w5.py`（注意共享仓其他车道未提交改动，勿全仓回滚）。

## 八、验收勾选

- [x] fast 门配置包含 datawarehouse（7 套件；`$paths` 目录套件）
- [x] datawarehouse 实跑全绿：**163 passed**，gate 行 New=0
- [x] 冻结守卫全绿：50 passed
- [x] `check_syntax.py --fail-on-warning` PASS（rc=0，0 错 0 警）
- [x] 只改门禁配置/冻结清单，未改业务代码；未 commit/未重启/未触发 merge
- [ ] fast 门整体全绿：被**外部** portal 回归阻塞（§五，非本单）
