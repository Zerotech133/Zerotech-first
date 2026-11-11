# ADR-026 · PII W2/W3 窗口外紧急合并（质量闸绕过 + merge train force）

- 状态：**Accepted（紧急例外；老田 2026-11-07 23:44 特批 m08274；ARCH1 复核待回签）**
- 决策人：老田（特批）；执行：DSH-Lead；架构复核：ARCH1（L2 知会单 `docs/ops/2026-11-07_L2_PII审计与8110变更_知会_v1.md`，待回签）
- 日期：2026-11-07（合并）/ 2026-11-08（24h 复盘补录）
- 关联：ADR-006（worktree 隔离）、ADR-012（L2 五要素）、ADR-013（范围与文档纪律）、ADR-025（治理嵌入）；ADGS v1.1 §5.3/§9.10（紧急例外，24h 内补 ADR/复盘）
- 依据：`deliverables/2026-11-07_PII_W2W3合并回执_v1.md`、`deliverables/2026-11-08_PII_W2W3特批合并_24h复盘_v1.md`、`docs/ops/2026-11-07_L2_PII审计与8110变更_知会_v1.md`

## 背景

1. PII 清洗固化 W2（`K-PII-W2-20261106`，export.py 导出前清洗）与 W3（`K-PII-W3-20261106`，learning_intake 入仓前清洗）于 2026-11-06 完成，等待平台合并窗口。
2. 2026-11-06 16:30 主窗口：只读预检全绿（W2/W3 worktree clean、merge-tree rc=0、overlap 0、W2 3 passed、W3 6 passed），但 `devtask merge` 内联 `test_gate fast` 出现 **16 项新增失败，全部在其他子系统**（portal 6、tools_key 1、datawarehouse 9）；门禁单次约 14 分钟，重跑必越过 17:00 窗口，故**未绕过门禁硬合**，改排 21:40 次窗口。
3. 21:40 次窗口：21:35 预检提醒实际于 **23:42** 才送达（延迟 6 小时 07 分），窗口已闭，未执行合并。
4. 2026-11-07 23:44 老田明确特批（m08274）：「现在就窗口外合并 W2→W3，用 DEVTOOL_MERGE_GATE=0 加五要素留痕，先跑只读预检再合，合并后验证并回报」。
5. L2 知会（L2-3）需要已合并的 master 证据；W3 的 learning_intake 清洗是入仓链路 DOD 的一部分，悬空分支会阻塞后续入仓验收。

## 决策

1. **范围**：仅合并 `K-PII-W2-20261106`（HEAD `2ec3428c0`）与 `K-PII-W3-20261106`（HEAD `177b2a8dd`），不合其他分支。
2. **例外手段**（两项同时使用，均留痕）：
   - `DEVTOOL_MERGE_GATE=0`：跳过内联质量闸（16 项失败与本变更文件不相交，证据见复盘 §3）；
   - `--train-force`：merge train 因窗口外拒绝，按特批强制进车。
3. **前置**：只读预检（master 状态、worktree clean、overlap 0、merge-tree rc=0、W2 3 passed / W3 6 passed）全部通过后才执行。
4. **执行**：W2 → `fa9080aad`，W3 → `19bf1e92d`；评审门/导航/端口/语法/导入/元数据/安全/gitleaks 均正常通过，仅质量闸按授权绕过。
5. **回滚**：`git revert -m 1 19bf1e92d`（W3 合并提交）与 `git revert -m 1 fa9080aad`（W2 合并提交）；两笔均为独立合并提交，回滚不影响其他变更。W2/W3 分支与 worktree 在合并后由 devtask 回收，如需重做可从回执中的提交重建。

## 五要素（L2 口径）

| 要素 | 内容 |
|---|---|
| 命令全文 | `DEVTOOL_MERGE_GATE=0 python tools/devtask.py merge K-PII-W2-20261106 --train-force --l2-ack --l2-reason "<五要素理由>"`；W3 同法（日志 `work/pii_w4/merge_w2_2357_force.log`、`merge_w3_0002_force.log`） |
| 影响面 | 平台 master 两笔合并提交；W2 改 `tools/kb_corpus/export.py` + 测试；W3 改 `tools/kb_corpus/learning_intake.py` + 测试；不触内核/schema/端口/凭据/计划任务 |
| 不可逆点 | 合并提交进入 master 历史；已用 revert 预案兜底；worktree/分支回收后需按提交 SHA 重建（SHA 已留档） |
| 替代方案与已尝试 | ① 等 11-07 16:30 主窗口（可行但延后 ≥16h，L2 知会与入仓验收被阻塞）；② 16:30 直接绕过（未采纳，避免在窗口尾硬合）；③ 仅带 `--train-force` 不绕质量闸（仍会被 16 项无关失败拒绝）；④ 修复 16 项无关失败（跨 portal/datawarehouse/工具席，非本单范围） |
| 回滚预案 | `git revert -m 1 19bf1e92d` + `git revert -m 1 fa9080aad`；回滚后重跑 W2 3 / W3 6 测试确认；分支可按 `2ec3428c0`、`177b2a8dd` 重建 |

## 后果

- 正面：W2/W3 进入 master（`19bf1e92d`），L2 知会取得合并证据；入仓链路 PII 清洗可用；24h 后验证代码仍在、测试仍过。
- 风险与缓解：
  - 质量闸被绕过 → 以“变更文件与失败面不相交”+ 24h 定向复跑 + 复盘登记兜底；
  - 窗口外合并破坏 merge train 秩序 → 仅此一次、用户特批、ADR + 复盘 + L2 知会三处留痕；
  - 提醒延迟导致再次错过窗口 → 改进措施见复盘 §5（双通道提醒、15:50 预检、窗口前人工确认）。
- 后续：ARCH1 复核并回签；16 项无关失败按复盘 §5 登记责任方；24h 复盘已完成（2026-11-08）。

## 审批签名

| 角色 | 姓名/席位 | 结论 | 日期 |
|---|---|---|---|
| 产品/最终决策 | 老田 | 特批窗口外合并 W2→W3，要求 `DEVTOOL_MERGE_GATE=0` + 五要素留痕、先预检后合并、合并后验证回报（m08274） | 2026-11-07 23:44 |
| 执行 | DSH-Lead | 预检全绿后执行；W2 `fa9080aad`、W3 `19bf1e92d`；合并后 3+6 passed | 2026-11-07 23:56–2026-11-08 00:04 |
| 架构复核 | ARCH1 | 待回签（L2 知会 `lg-5c9100440223`，截至 2026-11-08 13:10 仍 inbox） | — |
