# bundle Phase2（去 tracked）· 离线方案 + dry-run v1（2026-10-31）

> **工单**：`K-ORG-20261031-BUNDLEP2`（task-32）｜**分支**：`task/K-ORG-20261031-BUNDLEP2`｜**worktree**：`.worktrees/K-ORG-20261031-BUNDLEP2`
> **性质**：Phase2 **离线方案与只读 dry-run**。本单**不执行 L2、不 `git rm`、不改 nginx/容器、不切生产、不删历史**。
> **依据**：W5 质量还债 §2（`docs/audit/2026-10-31_W5质量还债_v1.md`）、W5 落地回执（`docs/audit/2026-10-31_W5落地回执_v1.md`）、`tools/build_agent_ide_bundle.py`、`ops/rebake_ide_bundle.ps1`、`portal/nginx.conf`、`tools/devtask.py`（Phase1）、`agent_platform/legion_ide/serve.py`、`ops/start_legion_ide.ps1`。
> **工具**：`tools/bundle_phase2_dryrun.py`（本次新增，只读）｜**测试**：`tools/tests/test_bundle_phase2_dryrun.py`（12 用例）

> **落地状态（2026-10-31 更新）**：Phase2-B 已在工单 `K-ORG-20261031-BUNDLEP2B` 分支实现并通过测试，
> 见 `docs/ops/2026-10-31_bundle_Phase2B_落地回执_v1.md`；本文档保留为方案基线（B1-B7 步骤与回滚清单不变）。

## 0. 一页结论

1. **W5 原文 Phase2-A（nginx alias 指向 `_vault/legion_ide/agent_ide_bundle.json`）在本机不可行**：
   ai-portal 容器只挂载了 `portal/`（→ `/usr/share/nginx/html`）与 `portal/nginx.conf`，**`_vault` 在容器内不可见** → `alias` 会 404；补齐需重建容器（8734 入口重启），比本目标更重的 **L2**。
2. **推荐替代：Phase2-B「原地去 tracked」**——生成物继续落在 `portal/`（容器已挂载、nginx 零改动），
   `git rm --cached` + `.gitignore` 后 **bundle 提交 0 增长**，历史提交不删，`git show` 仍可追溯；**不需要 nginx reload/服务重启**。
3. dry-run 实测（2026-10-31 11:50，只读）：bundle `tracked=True / ignored=False`，size 751,809B，8 日窗口
   **bundle 提交 900/3928 = 22.91%**（近 8 条 bundle 提交全为 `auto rebuild`），部署副本 `_vault` 版本比 repo tracked 版更新（745,112B，sha 不同）——与 W5 判断一致。
4. **L2 门禁**：dry-run 默认 `execution.status=blocked`；`--l2-approved` 仅标记、不执行；`--require-approved` 未批准时 exit 2。**本单未执行任何 Phase2 变更**（清单见 §7）。

## 1. 现状审计（dry-run 实测，只读）

### 1.1 bundle tracked 状态

| 项 | 值 |
|---|---|
| 路径 | `portal/agent_ide_bundle.json` |
| tracked / ignored | **True / False**（`git ls-files` 命中；`git check-ignore` 无规则） |
| 工作树状态 | clean（`git status --porcelain` 无该文件行；tracked 版由 rebake 的 `git checkout --` 还原） |
| 尺寸 / sha256 | 751,809B / `2bc4b5176b34b68d3e488281abc2be5c52d42435ce79d7ebc6be1af7f9ffe3e6` |
| 最后触碰提交 | `4f18d101e 2026-10-31 11:08:15 chore(ide): auto rebuild agent_ide_bundle after merge WO-L4-M4-HARDEN-20261031` |

### 1.2 提交噪声（8 日窗口，`git log --since="8 days ago"`）

| 指标 | 实测 | W5 报告（时点） |
|---|---|---|
| bundle 提交 | **900** | 852 |
| 总提交 | **3,928** | 3,896 |
| 占比 | **22.91%** | 21.87% |
| merge 提交 | 1,362 | 1,355 |
| 近 8 条 bundle 提交中 `auto rebuild` | **8/8** | — |
| 生成器易变字段 | `tools/build_agent_ide_bundle.py:595 "generated": time.strftime(...)` | 同 |

→ Phase1（源变才重建+提交）已把"每次 merge 都提交"收敛为"agent 源变更才提交"；Phase2 目标为 **0 增长**。

### 1.3 部署副本（8103/独立 IDE 数据源）

| 项 | 值 |
|---|---|
| 路径 | `C:\项目\_vault\legion_ide\agent_ide_bundle.json` |
| 尺寸 / sha256 | 745,112B / `7859fd2d0c99dec9f32e46eae382e4797ad5a89a70e0dd6fd4870e0a9397ce63` |
| 与 repo tracked 版相同？ | **否**（post-merge 钩子每 merge 刷新副本；repo 版被 `git checkout --` 还原 → 两者天然分叉） |

> 这解释了"8734 `/ide` 数据 vs 8103 `/ide` 数据可能不一致"的现状：Phase2-B 后由同一生成动作同时更新两份，可收敛该分叉（见 §4-B6）。

### 1.4 消费方清单（Phase2 破坏任一项都必须显式处理）

| id | 位置 | 行 | 作用 | Phase2-B 影响 |
|---|---|---|---|---|
| `nginx_location` | `portal/nginx.conf` | 501 | `location = /agent_ide_bundle.json`：auth_request + `try_files $uri`（从容器 html 根=portal 目录取文件） | 无（文件仍在 portal/ 且被挂载） |
| `frontend_fetch` | `portal/ide.html` | 2084 | IDE 页同源 `fetch("/agent_ide_bundle.json")` | 无（8734 仍 200） |
| `devtask_autorebuild` | `tools/devtask.py` | 1394/1395/1411/1413/1647/2013 | Phase1：源变才重建 + paths 提交 | **需改**（B4：重建但不提交） |
| `rebake_script` | `ops/rebake_ide_bundle.ps1` | 52 | post-merge：build→复制 vault 副本→`git checkout --` 恢复 tracked | **需改**（B3：去掉恢复动作） |
| `generator` | `tools/build_agent_ide_bundle.py` | 34 | `OUT = portal/agent_ide_bundle.json`（含 generated 时间戳） | 不变（输出仍在 portal/） |
| `ide_serve_8103` | `agent_platform/legion_ide/serve.py` | 72/123-125/305-313 | 8103 优先读 `_vault` 副本（repo 文件兜底） | 无（早已解耦） |
| `start_ide_script` | `ops/start_legion_ide.ps1` | 48-50 | `Resolve-BundlePath` 优先 `_vault` 副本 | 无 |
| `static_nginx_test` | `portal/tests/test_nginx_auth_request.py` | 56 | 静态断言 nginx location + auth_request 存在 | 无（B 不改 nginx）；若选 A 需同步改测试 |
| `vault_deploy_copy` | `C:\项目\_vault\legion_ide\agent_ide_bundle.json` | — | 独立 IDE 部署副本 | 无（钩子继续刷新） |
| `nginx_container_mount` | `docker inspect ai-portal` | — | portal 目录 ro bind mount；**`_vault` 未挂载** | B 无需挂载；A 被此阻断 |

### 1.5 关键阻塞事实（Phase2-A）

```text
docker inspect ai-portal --format '{{json .Mounts}}' →
  C:\项目\老田的AI助手\portal        → /usr/share/nginx/html (ro)
  C:\项目\老田的AI助手\portal\nginx.conf → /etc/nginx/conf.d/default.conf (ro)
  （无 C:\项目\_vault\legion_ide 挂载）
```

→ W5 原文 `alias C:/项目/_vault/legion_ide/agent_ide_bundle.json;` 在容器内解析不到 → **404**。
补齐需给 ai-portal 增加 bind mount（`docker compose` 配置变更 + 容器重建 = 8734 入口重启）= **L2**，且比 Phase2 目标更重。

## 2. 方案对比

| 维度 | **Phase2-B（推荐）** | Phase2-A（W5 原文） |
|---|---|---|
| 做法 | 生成物留在 `portal/`，`git rm --cached` + `.gitignore`；改 rebake/devtask/bootstrap | nginx alias 到 `_vault` + 容器挂载 + reload |
| nginx 改动 | **0** | 改 location（alias）+ reload |
| 容器/挂载改动 | **0** | 需新增 `_vault/legion_ide` bind mount → **容器重建** |
| 是否需要 L2 | **否**（仓库/生成物变更；不重启服务、不 reload） | **是**（改生产入口配置 + 容器重启） |
| bundle 提交增长 | **0** | 0 |
| 工作树 | 生成文件仍在 `portal/`（ignored、不随 git 分发） | 生成物只在 `_vault`（repo 目录干净） |
| 干净 clone/worktree | 需 bootstrap 兜底（B5） | 需 bootstrap 兜底 |
| 回滚 | 单 commit revert；`git add -f` 恢复 tracked | nginx 回退 + 容器回滚 + 恢复 tracked（多面） |
| 风险 | `git clean -fdx` 会删生成物（bootstrap 兜底）；两处数据源需同刷 | 容器重建窗口/入口重启；静态测试需同步 |
| 结论 | **采纳** | **不采纳**（挂载阻断 + L2 过度） |

## 3. 推荐执行（Phase2-B）：B1–B7（**本单全部未执行**）

| # | 步骤 | 命令（可复制） | 验证 | 回滚 | L2 |
|---|---|---|---|---|---|
| B1 | 先加 ignore | `Add-Content .gitignore "`nportal/agent_ide_bundle.json"` | `git check-ignore -v portal/agent_ide_bundle.json` 命中 | 删除该行 | 否 |
| B2 | 去索引（不删工作树文件） | `git rm --cached portal/agent_ide_bundle.json` | `git ls-files` 不再包含；文件仍在 `portal/` | `git add -f … && git commit` | 否 |
| B3 | rebake 去掉 tracked 恢复 | 删除 `ops/rebake_ide_bundle.ps1` 末尾 `git -C $Repo checkout -- portal/agent_ide_bundle.json`（保留 build + 复制 vault 副本） | 手动 rebake 后 `portal/` 文件 mtime 更新、`git status` 干净 | `git checkout -- ops/rebake_ide_bundle.ps1` | 否 |
| B4 | devtask 改为"重建+刷新副本、不提交" | 改 `tools/devtask.py::_auto_rebuild_ide_bundle`：保留源判定与 `DEVTOOL_IDE_BUNDLE_DRY_RUN`，删除 `git add/commit` 段，改调 build + 复制 `_vault` 副本；同步更新 `tools/tests/test_auto_rebuild_ide_bundle.py` | 单测全绿；merge 后 `git status` 无 bundle 行、0 bundle 提交 | `git checkout -- tools/devtask.py tools/tests/test_auto_rebuild_ide_bundle.py` | 否 |
| B5 | bootstrap 幂等兜底 | `tools/test_gate.ps1` 开头、`ops/start_legion_ide.ps1` 增加：若 `portal/agent_ide_bundle.json` 缺失 → `python tools/build_agent_ide_bundle.py` | 临时移走文件后跑 gate/start → 自动重建 | `git checkout --` 两个脚本 | 否 |
| B6 | 首次生成态 + 双面验证 | `python -X utf8 tools/build_agent_ide_bundle.py`；随后复制到 `_vault`（或跑 `ops/rebake_ide_bundle.ps1`） | 8734（登录态）`/agent_ide_bundle.json` 200 且 sha==生成文件；8103 `/agent_ide_bundle.json` sha==vault；`git status` 干净、`git ls-files` 无该路径 | `git add -f` 恢复 tracked | 否 |
| B7 | 观测验收（2 日） | `git log --since='2 days ago' --oneline -- portal/agent_ide_bundle.json` → 期望空；`--grep='auto rebuild agent_ide_bundle'` → 期望空 | 0 提交增长；8734/8103 数据一致且为最新 | 按 B2 回滚恢复 tracked | 否 |

## 4. L2 五要素（仅在**未来选择 Phase2-A** 时才需要；本单未批准、未执行）

> B 方案不需要 L2。以下为 A 方案备案（若后续坚持"产物彻底离开 repo"）。

1. **命令全文**：
   ```powershell
   # 1) 容器挂载变更（docker-compose 文件：ai-portal volumes 增加）
   #    - C:\项目\_vault\legion_ide:/usr/share/nginx/html/_vault_legion_ide:ro
   docker compose -f <compose> up -d ai-portal     # 会重建容器=8734 入口短暂中断
   # 2) nginx 改为 alias（portal/nginx.conf:501-507）
   #    location = /agent_ide_bundle.json { auth_request /_auth_guard; error_page 401 = @login_required;
   #      alias /usr/share/nginx/html/_vault_legion_ide/agent_ide_bundle.json; add_header Cache-Control "no-cache";
   #      types { } default_type application/json; charset utf-8; }
   docker exec ai-portal nginx -t
   docker exec ai-portal nginx -s reload
   # 3) 去 tracked
   git rm --cached portal/agent_ide_bundle.json
   # 4) 验证：8734 /ide 200；/agent_ide_bundle.json 200 且 sha==_vault 副本；8103 同
   ```
2. **影响面**：8734 入口在容器重建期间中断（≈秒级~10s）；`/agent_ide_bundle.json` 数据源切到 `_vault`；静态测试 `test_nginx_auth_request.py` 需同步；其余页面不受影响。
3. **不可逆点**：无数据不可逆；nginx conf / compose 文件在 git 可回退；历史提交不删。
4. **替代方案与已尝试**：**B（本方案，推荐、非 L2）**；A 因容器未挂载 `_vault` 实测不可行（`docker inspect` 证据）；隔离演练等价物见 `tools/bundle_phase2_dryrun.py`。
5. **回滚预案**：`git checkout -- portal/nginx.conf` + compose 回滚挂载 + `docker compose up -d ai-portal`（L2）→ `docker exec … nginx -t && -s reload` → `git add -f portal/agent_ide_bundle.json` 恢复 tracked。

## 5. 验收矩阵（Phase2-B 执行后）

| # | 检查 | 期望 |
|---|---|---|
| 1 | `git ls-files --error-unmatch portal/agent_ide_bundle.json` | 失败（已去 tracked） |
| 2 | `git check-ignore -v portal/agent_ide_bundle.json` | 命中 `.gitignore` 规则 |
| 3 | `git status --porcelain`（bundle 路径） | 空（ignored） |
| 4 | `git log --since='2 days ago' --oneline -- portal/agent_ide_bundle.json` | 空（0 增长） |
| 5 | 8734（登录态）`GET /agent_ide_bundle.json` | 200，sha == 生成文件 |
| 6 | 8103 `GET /agent_ide_bundle.json` | 200，sha == `_vault` 副本 |
| 7 | 干净 clone/worktree（无 bundle）跑 `test_gate.ps1`/`start_legion_ide.ps1` | 自动生成，不失败 |
| 8 | `git show HEAD~1:portal/agent_ide_bundle.json` 等历史 | 仍可追溯（历史未删） |

## 6. 风险与缓解

| 风险 | 缓解 |
|---|---|
| `git clean -fdx` 删除生成物 | B5 bootstrap 兜底；`_vault` 副本仍在 |
| IDE 页数据滞后（Phase1 已引入） | post-merge 钩子仍每 merge 刷新 `_vault`（B6 同刷 repo 副本）；需要时手动 `ops\rebake_ide_bundle.ps1` |
| 两处数据源（repo 生成物 / `_vault` 副本）分叉 | B6 一次生成同时更新两处；B7 观测 2 日 |
| 去 tracked 后 IDE 页在 8734 404（文件被误删） | B5 兜底 + 健康卡/静态测试 `test_nginx_auth_request.py` 仍在 |
| 干净 clone 首次 clone 后 `portal/agent_ide_bundle.json` 缺失 | B5（test_gate/start_legion_ide 幂等生成）；产物可由生成器确定性重建 |
| Phase2-A 误执行（无挂载）导致 404 | dry-run 明确 `feasible=False` 并列入 blockers；本单禁止执行 A |

## 7. 未执行项清单（本单明确没做）

1. ❌ `git rm --cached` / 任何索引改动（dry-run 只输出命令文本；测试断言 `git ls-files` 不变）
2. ❌ 修改 `.gitignore` / `ops/rebake_ide_bundle.ps1` / `tools/devtask.py` / `tools/test_gate.ps1` / `ops/start_legion_ide.ps1`
3. ❌ 修改 `portal/nginx.conf` / 容器挂载 / `docker restart|reload` / 切生产
4. ❌ 发布/删除任何历史提交、删除 bundle 文件
5. ❌ L2 执行（dry-run 默认 `execution.status=blocked`）

## 8. 复现与测试（本单已跑）

```powershell
# dry-run（只读；唯一稳定契约 = --json）
python -X utf8 tools/bundle_phase2_dryrun.py
python -X utf8 tools/bundle_phase2_dryrun.py --json
python -X utf8 tools/bundle_phase2_dryrun.py --json --l2-approved        # 仅标记，不执行
python -X utf8 tools/bundle_phase2_dryrun.py --json --require-approved   # 未批准 → exit 2

# 测试（12 用例：JSON 契约/零写入/消费方/L2 门禁/A 可行性/降级）
python -X utf8 -m pytest tools/tests/test_bundle_phase2_dryrun.py -q -p no:cacheprovider --timeout=120
# 回归（Phase1 既有 8 用例）
python -X utf8 -m pytest tools/tests/test_auto_rebuild_ide_bundle.py -q -p no:cacheprovider --timeout=120
# 语法
python -X utf8 -m py_compile tools/bundle_phase2_dryrun.py tools/tests/test_bundle_phase2_dryrun.py
```

**本单实测**：dry-run 脚本 12/12 通过；Phase1 回归 8/8 通过；py_compile 通过。

## 9. 后续（不在本单执行）

1. Lead 排期 Phase2-B（建议 11-07 前；非 L2，但涉及"去 tracked"需评审门 + 首次生成态验证）。
2. 若坚持 Phase2-A：需先补容器挂载（L2 五要素单）并同步静态测试。
3. W5 指标看板回填：bundle 提交/8d 从 900（22.91%）→ 观测 2 日 0 增长。
4. 顺带优化（可选）：`tools/build_agent_ide_bundle.py:595` 的 `generated` 时间戳可改为 `SOURCE_DATE_EPOCH`/内容哈希派生，进一步降低无意义 diff（Phase2 后已不提交，优先级低）。
