# UI / HZ rebase 催办单 v1（2026-11-04 10:30）

> 催办人：DSH-Lead（守点）｜性质：**催办 + 要求**，不是代做；只读预检已完成，rebase 由分支 owner 执行。
> 依据：`docs/ops/2026-11-04_1630主树与门禁预检_v1.md` §五（10:20 实测）；`merge_gated --dry-run` + `git merge-tree` + ahead/behind。

## 一、事实（10:20 实测）

| 分支 | TID | task 状态 | owner | ahead/behind master | PIN_GATE | merge-tree |
|---|---|---|---|---|---|---|
| `task/UI-UNIFY-MANIFEST-20261103` | UI-UNIFY-MANIFEST-20261103 | review | DSH-Lead | 2 / **325** | ❌ REJECT（H5：分支 `test_gate.ps1` 落后 master） | rc=0（无冲突） |
| `task/HZ-FINAL-DATE-20261104` | HZ-FINAL-DATE-20261104 | doing | ARCH1 | 1 / **203** | ❌ REJECT（同上） | rc=0（无冲突） |
| `task/W1-PILOT-TOOLFIX-20261104` | W1-PILOT-TOOLFIX-20261104 | doing | ARCH1 | 1 / 160 | ✅ OK | rc=0 |

- 当前主树 HEAD `588b4e2e9`；worktree=59 / dirty=270；禁并期至 15:05，下一主窗 **16:30**。
- 结论：**若 16:20 前不 rebase，16:30 只能合 W1 一单**；UI/HZ 顺延 21:40 或下一窗口。

## 二、要求（owner 执行，16:20 前完成）

1. **rebase 到最新 master**（当前 `588b4e2e9`，以执行时最新为准）；
2. **复跑本分支测试**：
   - UI-UNIFY-MANIFEST：其新增 pytest（20 用例）+ 相关门禁自检；
   - HZ-FINAL-DATE：**79 tests** + `huangzhong_1104_finalize --help` 级别冒烟（不改默认 11-04 行为）；
3. **pin 复检**：`python -X utf8 tools/merge_gated.py --dry-run <TID>` → 期望 **PIN_GATE OK**；
4. **worktree 保持 clean**（不把无关脏文件带入合并）；
5. **回执**：在 `STATUS.md` 追加一段 `[rebase回执] <TID> rebase rc=0 / tests N passed / PIN OK / worktree clean`，或将回执文件放 `docs/ops/`。

### 命令参考（owner 自跑；Lead 不代执行）

```powershell
$PY = "C:\Program Files\Python311\python.exe"
cd C:\项目\老田的AI助手

# 1) 前置：worktree 必须 clean；有未提交改动先由 owner 收口（不 stash 他人改动）
git -C .worktrees\UI-UNIFY-MANIFEST-20261103 status --porcelain
git -C .worktrees\UI-UNIFY-MANIFEST-20261103 rebase master
# 2) 测试（按分支既有用例路径；避免裸 python）
& $PY -X utf8 -m pytest <本分支测试路径> -q
# 3) pin 复检（期望 rc=0 / PIN_GATE OK）
& $PY -X utf8 tools\merge_gated.py --dry-run UI-UNIFY-MANIFEST-20261103

# HZ 同法：
git -C .worktrees\HZ-FINAL-DATE-20261104 status --porcelain
git -C .worktrees\HZ-FINAL-DATE-20261104 rebase master
& $PY -X utf8 -m pytest <HZ 79 tests 路径> -q
& $PY -X utf8 tools\merge_gated.py --dry-run HZ-FINAL-DATE-20261104
```

## 三、红线（催办不是放水）

- **禁止手工改 `tools/test_gate.ps1` / `test_baseline.json` 过 pin**（H5 会拒收且留痕）；
- 禁止 `--train-force` / `DEVTOOL_MERGE_GATE=0`；
- 禁止把 rebase 冲突“硬合/覆盖”后不跑测试；
- 16:20 后无论是否 rebase 完成，**以当时实测为准**；未完成不得宣称“可合”。

## 四、影响与替代

- 若 16:20 仍未 rebase：16:30 只合 W1（1 单）；UI/HZ 顺延 21:40 或下一窗口；
- 若 rebase 出现冲突（当前 merge-tree 预演无冲突，理论不应出现）：停手、记录冲突文件、报 owner/老田，不强行解决；
- W0 当日数字仍按 17:10 后实测冻结，不因催办改变口径。

## 五、催办渠道（本次实际动作）

1. **STATUS.md** 追加催办段（共享板，所有会话可见）；
2. **内部 inbox**：`internal_notify.emit` → A0 / ARCH1 / DSH_Lead（`bridge_inbox/inbox/`，幂等键去重）；
3. **派工台账**：`work/tianfeng_dispatch.jsonl` 追加两条 dispatched 记录（UI/HZ rebase）；
4. 本催办单落 `docs/ops/`（可复核）。

## 六、证据

- 10:20 预检：`docs/ops/2026-11-04_1630主树与门禁预检_v1.md` §五；
- `merge_gated --dry-run` 输出：UI/HZ = REJECT（H5 pin）、W1 = OK；
- `git merge-tree --write-tree master <branch>`：三支 rc=0；
- ahead/behind：`git rev-list --count master..<branch>` / `<branch>..master`。
