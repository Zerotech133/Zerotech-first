# bundle Phase2-B（原地去 tracked）· 落地回执 v1（2026-10-31）

> **工单**：`K-ORG-20261031-BUNDLEP2B`（task-35）｜**分支**：`task/K-ORG-20261031-BUNDLEP2B`｜**worktree**：`.worktrees/K-ORG-20261031-BUNDLEP2B`
> **性质**：Phase2-B 的可合并候选实现（**未 merge、未动主工作树、未动生产**）。
> **依据**：`docs/ops/2026-10-31_bundle_Phase2_离线方案_v1.md`、`tools/bundle_phase2_dryrun.py`、W5 质量还债 §2。

## 0. 一句话

按推荐 **Phase2-B** 落地：`portal/agent_ide_bundle.json` 已 `.gitignore` + `git rm --cached`（工作树生成物保留）；
`ops/rebake_ide_bundle.ps1` 与 `tools/devtask.py` 不再产生 bundle 提交，改为「重建 + 刷新 `_vault` 部署副本」；
dry-run 复核 `tracked=False / ignored=True / implementation done=True`，33 个 bundle 相关测试 + 27 个关联测试全绿。

## 1. 改动清单（worktree 内）

| # | 文件 | 改动 | 对应 |
|---|---|---|---|
| 1 | `.gitignore` | 追加 `/portal/agent_ide_bundle.json`（附定义段注释） | B1 |
| 2 | `portal/agent_ide_bundle.json` | `git rm --cached`（**仅出索引，不删工作树文件**；751,809B 仍在磁盘） | B2 |
| 3 | `ops/rebake_ide_bundle.ps1` | 删除末尾 `git -C $Repo checkout -- …` 还原；日志改为 `bundle generated (untracked/ignored; no git commit)`；保留 build + 复制 `_vault` 副本 | B3 |
| 4 | `tools/devtask.py` | `_auto_rebuild_ide_bundle`：Phase1 源判定保留；重建后只调 `_refresh_bundle_deploy_copy()` 刷 `_vault`，**不 git add/commit**；删除 `_ide_bundle_commit_args`（pathspec 收尾提交）与相关分支 | B4 |
| 5 | `tools/bundle_phase2_dryrun.py` | 新增 `implementation`（B1–B4 状态）、步骤 `landed` 标记、blockers 按实现状态收敛、人读输出加"Phase2-B 实现"行 | 验收 |
| 6 | `tools/tests/test_bundle_phase2b.py` | **新增**：B1–B4 落地状态 + 消费方不变 + 幂等（8 用例） | 测试 |
| 7 | `tools/tests/test_auto_rebuild_ide_bundle.py` | 移除 pathspec 提交用例；新增"重建→刷副本、零提交""构建失败不刷副本""部署副本幂等" | 测试 |
| 8 | `tools/tests/test_bundle_phase2_dryrun.py` | 适配去 tracked 后状态（tracked=False/ignored=True）+ 新增 implementation 断言 | 测试 |

## 2. 实测（worktree 内）

### 2.1 dry-run 复核（去 tracked 后）

```text
bundle: tracked=False ignored=True size=751809 sha=2bc4b517…（16位截断）
churn(8d): bundle提交=900 / 总提交=3987 = 22.57%；近期 auto rebuild 占比=8/8
消费方: nginx_location/frontend_fetch/devtask_autorebuild/rebake_script/generator/
        ide_serve_8103/start_ide_script/static_nginx_test/vault_deploy_copy/nginx_container_mount 全 OK
Phase2-B 实现: B1-B4 全为 True（明细见代码/测试，本文脱敏）
自我校验零写入: True
blockers（仅剩与 Phase2-B 无关的两项）:
  - PHASE2-A 不可行：ai-portal 容器未挂载 _vault/legion_ide（备选方案的门，不影响 B）
  - L2 未批准：生产窗口标记（本单不执行）
```

### 2.2 `git rm --cached` 后状态

```text
git ls-files --error-unmatch portal/agent_ide_bundle.json → 失败（不再 tracked）
git check-ignore -v → .gitignore:329:/portal/agent_ide_bundle.json
工作树文件仍在：751,809B（未被删除）
git status（提交前）：D  portal/agent_ide_bundle.json / M .gitignore / M ops/rebake_ide_bundle.ps1 / M tools/devtask.py
```

### 2.3 测试/门禁

```text
pytest tools/tests/test_bundle_phase2b.py tools/tests/test_bundle_phase2_dryrun.py \
       tools/tests/test_auto_rebuild_ide_bundle.py -q
.................................                                        [100%]
33 passed in 14.49s
# = 8（phase2b）+ 13（dry-run）+ 12（auto rebuild）

pytest tools/tests/test_test_gate_b1.py portal/tests/test_nginx_auth_request.py \
       portal/tests/test_login_ui.py -q          → 27 passed in 0.71s
py_compile（5 个改动 py 文件）→ rc=0
tools/check_syntax.py --fail-on-warning → 5 文件 / 错误 0 / 告警 0 / PASS
dry-run --json --require-approved → exit 2（L2 未批准门禁不变）
```

## 3. 未执行项（明确没做）

1. ❌ `git merge` / `devtask merge`（按 Lead 要求不合并）
2. ❌ 主工作树任何改动（全部改动在 worktree 分支内）
3. ❌ 生产 nginx/容器/服务变更、`docker exec`/reload/restart、切 current junction
4. ❌ 删除历史提交（`git show <旧commit>:portal/agent_ide_bundle.json` 仍可追溯）
5. ❌ 发布/推送（仅本地 commit）

## 4. 合并（Lead）注意事项

1. **可能遇到 modify/delete 冲突**：本分支删除 tracked bundle；若 master 期间又有
   `chore(ide): auto rebuild agent_ide_bundle` 提交（Phase1 仍在 master 生效），git merge 会报
   "deleted in … / modified in …"。解决（保留工作树生成物、只接受删除索引）：
   ```powershell
   git rm --cached portal/agent_ide_bundle.json       # 触发冲突后：接受删除（不删磁盘文件）
   git add .gitignore ops/rebake_ide_bundle.ps1 tools/devtask.py tools/bundle_phase2_dryrun.py `
           tools/tests/test_bundle_phase2b.py tools/tests/test_bundle_phase2_dryrun.py `
           tools/tests/test_auto_rebuild_ide_bundle.py docs/ops/2026-10-31_bundle_Phase2B_落地回执_v1.md
   git commit
   ```
2. **合并后自动生效面**：`.git/hooks/post-merge` 调 `C:/项目/老田的AI助手/ops/rebake_ide_bundle.ps1`（绝对路径，
   已实测存在）→ 合并后自动走 Phase2 版（不再 checkout 还原/不提交）。
3. **master 后续 merge**：`tools/devtask.py` 已是 Phase2 版 → `_auto_rebuild_ide_bundle` 不再 add/commit，
   bundle 提交应为 0；post-merge 钩子仅刷 `_vault` 副本。
4. **干净 clone bootstrap 尚未做（B5，未在本单范围）**：干净 clone 无生成物文件，`/ide` 数据需先跑
   `python tools/build_agent_ide_bundle.py`（或 `ops/rebake_ide_bundle.ps1`）；生产机与当前工作树文件在位，不受影响。
   建议 Lead 把 B5 排为后续小单（test_gate.ps1 / start_legion_ide.ps1 幂等生成）。
5. **回滚（单 commit）**：`git revert <本单commit>` 会把 bundle 恢复为 tracked（工作树文件已在，无需再生成）；
   `.gitignore`/hooks 一并未动生产。

## 5. 验收矩阵（合并后逐条核）

| # | 检查 | 期望 |
|---|---|---|
| 1 | `git ls-files --error-unmatch portal/agent_ide_bundle.json`（master） | 失败（已去 tracked） |
| 2 | `git check-ignore -v portal/agent_ide_bundle.json` | 命中 `.gitignore` |
| 3 | `git status --porcelain`（含 bundle 路径） | 空 |
| 4 | 连续 2 日 `git log --oneline -- portal/agent_ide_bundle.json` | 空（0 增长） |
| 5 | `git log --since='2 days ago' --grep='auto rebuild agent_ide_bundle'` | 空 |
| 6 | 8734（登录态）`GET /agent_ide_bundle.json` | 200（文件仍在 portal/，服务无需变更） |
| 7 | 8103 `GET /agent_ide_bundle.json` | 200（读 `_vault` 副本，post-merge 刷新） |
| 8 | `git show HEAD~N:portal/agent_ide_bundle.json` | 可追溯（历史未删） |

## 6. L2 / 评审需求

- **Phase2-B 非 L2**：全部改动为仓库文件/索引/生成逻辑，**不需要** nginx reload、容器重建、服务重启、生产 current 切换。
- **评审门**：建议走 `python tools/devtask.py merge K-ORG-20261031-BUNDLEP2B`（质量闸 fast=portal+kstock+ide；
  本单测试不在 fast 默认列表，但已在分支内单独全绿；纯文档/测试/工具改动）。
- 需 Lead 决策：① 是否接受"bundle 文件留在 repo 工作树但 ignored"的 B 方案（vs A 方案的容器挂载 L2）；
  ② 合并时 modify/delete 冲突的处置按 §4-1；③ 是否排 B5 bootstrap 小单。
