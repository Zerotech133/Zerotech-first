# ADR-025 · 治理嵌入开发流（影子→告警→强制，不停工）

- 状态：**Accepted（老田 2026-11-06 批准执行路线；A0 / ARCH1 于 2026-11-07 经 Gate 回签生效，批复件 lg-704d1cbdb94d / lg-6191e2f59a20；P1 待 11-08 17:10 启动）**
- 决策人：老田（批准）；A0、ARCH1（2026-11-07 回签，田峰代签）；执行：DSH-Lead
- 日期：2026-11-06
- 关联：ADR-006（worktree 隔离）、ADR-009（端口登记）、ADR-013（范围与文档纪律）、ADR-015（内核/组合）、ADR-016（单宿主）、ADR-020（开发军团）、ADR-021（控制面选型）；ADGS v1.1 §七；ROADMAP v4 子计划
- 依据：`deliverables/2026-11-06_零界完整复盘与治理成型方案_v1.md`（诊断卷）、`deliverables/2026-11-06_治理嵌入开发流_v2_不停工方案.md`（执行卷）、`deliverables/2026-11-06_治理方案审阅与修正建议_v1.md`（审阅）、`deliverables/2026-11-06_治理落地实施方案_不影响并行开发_v1.md`（实施方案 v1）

## 背景

1. 六个断点串联：无仲裁、无进门队列、契约空、治理无执行体、状态是日志、知识不回流；11-05 出现两会话同 worktree 双 fast 门、staged 文件混入他人 merge commit 等事故。
2. ADGS §七 的 WIP 目标（试点 doing≤6 / worktree≤15 / parked≤5）与代码现状（`tools/devtask.py:1095 MAX_WORKTREES_DEFAULT=65`，实测 worktree=42）相差数倍；"新开 worktree 前先回收一个"的令牌制在代码中未落地。
3. v2 提出"只改一个常量 + 一道门 + 只读观测"的不停工路径，但对 D0 参数存在三处硬伤（详见审阅建议 §二.2）：
   - 棘轮公式 `cap(d)=max(target,current(d-1)-step)` 在首日（42→40）会触发 `count>=cap` 硬拒，等于立刻停工；
   - §七 声称影子期只记录，§八 D0 却直接改 devtask 读 config，没有 `GOV_MODE` 分支，会变成隐性 enforce；
   - 第 9 道门 `_lease_gate` 依赖尚不存在的租约后端，且 explore 通道若仍走 `devtask new` 会被 worktree cap 拦截。
4. 当前处于 11-08 交付冲刺（结果包 12:00 / W1 16:30 窗），任何治理改动不得影响并行开发。

## 决策

1. **文档定位**：v1 降为诊断卷 + 目标态参考（其 §八 S0–S4 执行路线不再执行）；v2 作为执行卷，但 D0 参数按本 ADR 修正。
2. **执行路线（P0–P5，见实施方案 §一）**：
   - P0（11-06 → 11-08 17:10）：治理零代码改动，只读基线采集（脚本在 `C:\Temp`，不入仓、不建计划任务、不写仓库）；
   - P1（11-08 17:10 → 11-09 16:30 窗）：分支 `task/GOV-D0-20261108` 写修正版 D0；合入时 `GOV_MODE=off`，行为与现状等价；
   - P2（11-09 → 11-15）：`shadow`，只记录 would-block/warn，不拦；
   - P3（11-16 → 11-22）：`warn`，只告警，不拦；
   - P4（11-23 起）：`enforce`，仅 ship 通道；explore 豁免；第 9 道门先有 acquire 端点（8796，复用现有 ThreadingHTTPServer）再接门，11-27 warn 试运行、11-30 生效；
   - P5（12-02）：对齐 L1/P6；ADR-025 定稿；GovGraph/GovKernel 去留评审。
3. **棘轮公式（修正版，永不冻结）**：`cap_new = max(target, min(cap_prev, current + 1))`；cap 恒 ≥ 当前 worktree 数 + 1，只有实际回收使 current 下降时 cap 才跟随下降；下降由 merge train 回收驱动，不另起清账战役。
4. **安全不变量（硬性）**：
   - 默认零行为：`config/gov_state.json` 缺失或 `mode=off`（或 env `GOV_MODE=off`）时，`devtask.py` 原逻辑逐字节等价；
   - `off`/`shadow`/`warn` 三档绝不 `SystemExit`；只有 `enforce` 档才拦，且只拦 ship；
   - 单写者：`tools/devtask.py` 仅 DSH-Lead 在 `task/GOV-D0-20261108` 分支修改，声明 write_scope，编辑前检查他人未提交修改；
   - 不碰现有 8 道门、`test_gate.ps1`、`merge_preflight.py`、`merge_train`、`check_ports.py`、`AGENTS.md`；
   - 不删除任何分支/worktree/任务，不执行 `prune --apply`、`git clean -fdx`、`reset --hard`；
   - 不新增服务进程/端口/MQ/DB；`/gov/observe/status` 复用现有 8796；
   - `GOV_MODE=off` 熔断：任何治理导致阻塞 >30 分钟即降级并复盘。
5. **运行纪律（不依赖治理服务的即时防线）**：窗口单执行器（开窗前检查 `devtask.py merge` 进程）、pre-merge staged 检查（`git status --porcelain --untracked-files=no` 必须为空）、token/timing 证据、`DSH-Merge-*`/`DSH-Prep-*` 计划任务保持 Disabled、每窗一页回执并入 STATUS。
6. **度量**：只报进门时长（创建→合入）、WIP（doing+review）、worktree、未合分支、双执行器/staged 混入事故数；不把 commit 数、分支数、席位数、页面数当治理目标（v2 §九）。
7. **范围外**：GovKernel 六服务（8801–8806）与 GovGraph 入图不在本次实施范围；12-02 凭影子数据评审。
8. **审核与换挡**：L0 治理改动需 A0+ARCH1 双签；P2/P3/P4 每次换挡由老田确认，DSH-Lead 执行并留痕。

## 后果

- 正面：治理以最小可逆方式嵌入，11-08 前零干扰；影子期积累真实数据再换挡；`GOV_MODE=off` 随时熔断；存量随 merge train 自然下降。
- 风险与缓解：多会话并发改 devtask（单写者 + 分支写域）；治理改动被 fast 门判红（分支先行 + full fast 门 + off 默认路径不变）；影子期误判（不拦 + 每日复核）；治理本身变成新"文档治理"（每阶段出口判据，不达标不升级）。
- 回滚：删除 `config/gov_state.json` 或设 `GOV_MODE=off` 即恢复原行为；必要时 revert P1 单提交，无残留。

## 实施与验证

- 改动清单：`tools/gov_ratchet.py`（新建）、`tools/gov_observe.py`（P0 脚本入库版）、`config/gov_state.json`（默认 mode=off）、`tools/devtask.py`（~12 行：GOV_MODE 读取、`_worktree_cap`/`_enforce_worktree_cap` 分档、`--explore` 跳过 cap）、新增两个测试文件；不碰门禁本体。
- 验收：新增测试 + `test_devtask_worktree_mech.py`/`test_devtask_gates.py` 全 PASS；full fast 门 GATE PASS；PIN/merge-tree OK；16:30 窗口单执行器合入；合入后 `GOV_MODE=off` 复核行为不变。
- 证据：P0 基线 `%TEMP%\gov_observe\gov_baseline_*.json`；P1 起每阶段留 `%TEMP%\gov_ratchet.log` 与换挡记录；窗口回执入 STATUS。

## 审批签名

| 角色 | 姓名/席位 | 结论 | 日期 |
|---|---|---|---|
| 产品/最终决策 | 老田 | 批准执行路线（P0–P5、5 项拍板） | 2026-11-06 |
| A0 裁定 | A0（田峰代签） | 同意 §决策 1–8 作为治理执行依据；P4 前不 enforce，cap 恒 ≥ 当前 worktree+1（Gate 回签 lg-704d1cbdb94d） | 2026-11-07 |
| ARCH1 架构 | ARCH1（田峰代签） | 同意决策 4/6/7（安全不变量/度量/范围外）；第 9 道门仅影子→warn，不换门禁本体（Gate 回签 lg-6191e2f59a20） | 2026-11-07 |
| 执行 | DSH-Lead | 按本 ADR 实施 | 2026-11-06 |
