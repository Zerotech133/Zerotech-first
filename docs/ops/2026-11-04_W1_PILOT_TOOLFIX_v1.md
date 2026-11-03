# W1-PILOT-TOOLFIX v1（G5/G6/G7）

- 时间：2026-11-04 04:05｜性质：计划 v2 W1 工具缺口修复，**未合并 master**
- 来源：W1 双周试点报告 G5/G6/G7

## 一、修复内容

| 缺口 | 现象 | 修复 |
|---|---|---|
| G5 | `composition.py validate acad1-legion` 裸 id → rc=2 “没有找到 manifest” | `_manifests()` 增加 `COMP/<id>/manifest.yaml` 回退；裸 id 可 validate |
| G6 | `resolver.py --manifest <file> --strict` 的 `--strict` 不生效 | 单 manifest 模式若 strict 且 warnings>0，`ok=false`/rc=1；非 strict 不变 |
| G7 | `test_resolver` 只断言 `refs>=100`，不能防扩/缩分母 | 改为 **refs==131** 分母锁 |

## 二、交付物

| 项 | 值 |
|---|---|
| worktree | `.worktrees/W1-PILOT-TOOLFIX-20261104` |
| branch | `task/W1-PILOT-TOOLFIX-20261104` |
| commit | `1f679071f` |
| 变更 | `tools/composition.py`、`tools/resolver.py`、`tools/test_resolver.py`、`tools/tests/test_platform_composition.py`（+28/−2） |

## 三、验证

```text
composition.py validate acad1-legion --strict --json  → rc=0, id=acad1-legion
resolver.py --all --strict --json                    → 131 refs / 0 error / 0 warning
pytest tools/test_resolver.py tools/tests/test_platform_composition.py → 30 passed
```

worktree 基线为最新 master；merge 前必须 rebase + 复跑。

## 四、worktree 测试环境前置（不写入分支）

W1 测试在 worktree 需 live 数据根，否则会出现“7 warnings”假红：

- `quant_backtest/data`：用 junction 指向主仓（只读，测试用）
- `research/reports/dataset_inventory_*.json`：复制主仓最新 inventory
- `agent_platform/data/{recommendation_pool,pool_review,team_exec,shadow}`：junction 到主仓

这些均为测试环境，不进 git；**merge 门禁前由 `w0_worktree_bootstrap` 或等价步骤准备**。

## 五、merge 队列

- 优先级低于 UI-MANIFEST / HZ-FINAL-DATE；可在 16:30 窗口第三位；
- 合并前：rebase 最新 master → 数据引导 → `pytest` 30 passed → fast 门；
- 若窗口容量不足，顺延 11-05，不夹带。

## 六、结论

G5/G6/G7 已修复并有回归测试；分支未合并、master 未受影响。11-16 W1 复审前把该分支合入即可；若不合，W1 的 131 分母锁/裸 id 兼容仍缺失，但不阻塞 11-04/11-10。
