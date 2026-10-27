# 测试指南（docs/testing.md）

> 目标：让"测试可当回归门"。当前为分套件基线；CI 门禁在 B3/B4 计划中（工单 `WO-DEV-B3-*`/`WO-DEV-B4-*`）。
> 基线来源：`deliverables/2026-10-27_测试基线_v1.md` + 2026-10-27 全量实测。

## 一、套件与基线（2026-10-27）

| 套件 | 基线 | 说明 |
|---|---|---|
| `portal/`（根级 test_*.py） | 289 passed（1 xfail） | 门户/移动端/飞书卡片；`portal/test_m_links.py` 修复后 7/7 |
| `k_stock/tests` | 791 passed / **2 既存失败** | `test_report_builder::test_no_external_sdk_imported`、`test_riskguard_topics::test_markdown_renders_conclusion_and_groups`（master 同败） |
| `agent_platform/tests` | 历史 1,833 passed / 10 failed（8 环境/顺序、2 flaky） | 全量较慢；ENVTEST 已修 6 个环境依赖 |
| `quant_backtest/tests` | **2,379 collected / 0 collection error** | 全量 >20 分钟，建议夜间窗口 |
| `tests/e2e` | **13 passed**（2026-10-27） | 冒烟 E2E（服务/工具/mock 回执/失败路径）；依赖 8091/8734/8796/bot 存活，已入夜跑默认套件 |
| 全量（带 per-test timeout） | 2,095 passed / 1 xfailed / 0 failed（298.9s，2026-10-27 实测） | 裸跑可能因外部子进程挂起；见 §四 |

## 二、常用命令

```powershell
# 定向（推荐日常）
python -m pytest portal/test_m_links.py -q -p no:cacheprovider
python -m pytest k_stock/tests -q --timeout=120 -p no:cacheprovider
python -m pytest agent_platform/tests -q --timeout=120 -p no:cacheprovider
python -m pytest quant_backtest/tests --collect-only -q -p no:cacheprovider   # 收集自检
python -m pytest tests/e2e -q --timeout=120 -p no:cacheprovider                # E2E 冒烟（需服务在线）
python -X utf8 tools/e2e_run.py                                                # 带 artifact 跑 E2E
```

## 三、失败分类口径（不许糊）

1. **真 bug**：同环境主仓/干净 master 可复现且不符合预期 → 开单修 + 留回归；
2. **测试漂移**：产品口径变了测试没跟上（如 m_links H5 按钮文案）→ 更新测试并注明判例；
3. **环境依赖**：worktree 缺数据根/端口占用/全局状态 → 主仓复跑定性；修法=skipif/fixture 隔离；
4. **假红**：并行改动的脏工作树 → 干净 worktree 复跑，不算失败。

## 四、已知环境坑

- worktree 无 `quant_backtest/data`、`portal/data` 未跟踪文件 → 相关用例假红，主仓复跑确认；
- 8795 端口被 legion-gateway 占用时，探针测试假设"端口空闲"会红 → 应加 `port_in_use` skip（P0-3）；
- `test_sign_tools` 全量跑顺序依赖（单跑必过）→ 加 fixture 清理（P0-3）；
- 外部子进程（`run_evals`）曾挂起 54min → 必须加 timeout/trace（X3/`WO-DEV-X3-*`）。

## 五、门禁与夜跑（计划）

- merge 门已含：端口注册、Mixin、导航、明文凭据、L2 危险操作（自动）。
- 待落地（B3/B4）：`tools/test_gate.ps1` 快速门（portal+k_stock）、agent_platform 分片、quant 夜间跑、baseline diff（连续 3 晚无新增失败）。
- 原则：**新失败不许进基线**；基线只允许显式登记并注明归因。
