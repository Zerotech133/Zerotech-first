# 验收判据：测试收集修复（WO-QUANT-COLLECT）
> STATUS: frozen（黄忠验收夹具/样例文件，非计划；2026-10-31 W2 收口）



| # | 判据 | 证据路径/命令 | 通过线 |
|---|---|---|---|
| 1 | quant 可收集 | `pytest quant_backtest/tests --collect-only -q` | rc=0；collected=2390 |
| 2 | 根目录白名单生效 | `pytest --collect-only -q` | rc=0；无 .worktrees nodeid |
| 3 | 缺依赖不炸收集 | 3 个 xq_* 测试文件头 | 含 pytest.importorskip("jieba") |
| 4 | 原错误测试可跑 | 直跑 3 个 xq_* 测试 | 19 passed |
| 5 | 白名单行正确 | `pytest.ini` testpaths | 含 quant_backtest/tests |

## 回归项（修复后不误伤）

- `pytest agent_platform/tests/test_ide_enrich.py`：passed。
- bare collect 数量 5124、rc=0；无 .worktrees 节点。
