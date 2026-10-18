# 盘中入场 0 成交 — 根因定位（2026-09-02）

## 结论（推翻旧假设）

旧假设（记忆 `chan-v7-and-scheduler-ops`）：**入场循环某个 `continue` 分支吞了 v7**。
**证据推翻此假设**——入场循环根本没有拿到当日承认可评估。真因在上游 pre_market：

> **过期未约定的 APPROVED 承认永不过期 → 累积 → 撑爆 `sim_max_positions=10` 上限
> → pre_market 建计划前早退（只建候选池，plans=[]/approvals=[]）→ 入场循环当日承认=0 → 0 成交。**

## 证据链

1. **今日 09-02 CN 候选池存在但计划/承认为 0**
   - CandidatePool(09-02, CN): 3 池（chan sv-4 / mean_reversion sv-2 / chan_v7 sv-7），各 20 候选，ACTIVE。
   - trade_plan(09-02, CN) = 0；approval(09-02, CN) = 0（全状态）。
   - → 管线在「候选池 → 计划/承认」之间断裂。

2. **pre_market 在建计划前有 `sim_max_positions` 早退闸**（`workflow_service.py:594-618`）
   ```python
   _total_active = open_positions + active_approvals(PENDING/APPROVED/EXECUTING)
   if _total_active >= sim_max_positions(=10):
       return {..."plans":[], "approvals":[], "position_limit_reached": True}
   ```
   日志实证（CRYPTO）：`sim_max_positions 上限已达: market=CRYPTO active=175/10 (持仓=3 待成交=172), 跳过本轮建计划`。

3. **各市场 active 承认远超上限**
   | 市场 | open | active承认 | 合计/10 | 判定 |
   |---|---|---|---|---|
   | CN | 0 | 78 (APPROVED67 + PENDING11) | 78/10 | **BLOCKED** |
   | US | 8 | 19 (APPROVED3 + EXECUTING2 + PENDING14) | 27/10 | **BLOCKED** |
   | HK | 0 | 9 (APPROVED7 + EXECUTING2) | 9/10 | ok（临界） |
   | CRYPTO | 3 | 172 | 175/10 | **BLOCKED** |

4. **APPROVED 承认无任何自动过期路径**
   - `expire_stale_pending_plans`（`sqlalchemy_db.py:355`）**硬排除 APPROVED**（377 行 `if r.plan_id in approved_intents: continue`），只过期 PENDING（386 行 `status=="PENDING"`）。
   - 全仓搜索：仅 `conflict_resolver_service.py:84` 在竞争裁决时把 loser 的 APPROVED→CANCELLED。**正常「批准但当日未约定」的 APPROVED 无终态收敛，永久滞留。**
   - CN 67 APPROVED 分布在 8 个历史日期（08-19 ~ 09-01），逐日累积，越过 10 即永久锁死后续所有 pre_market。

5. **次生泄漏：EXECUTING 固着**
   - `_auto_execute`（`workflow_service.py:827,865`）：claim 把 APPROVED→EXECUTING，若随后 `apply_fill` 返回 None（未成交）则 `return None` **不回滚 EXECUTING**。US/HK 各 2 笔 EXECUTING 疑似此因固着，同样计入 active 占坑。

## 影响面

- CN / US / CRYPTO 三市场当前被上限锁死，每日 pre_market 只产候选池、零计划零承认；HK 9/10 侥幸未锁但一步之遥。
- 叠加 catch-up 幂等缺陷：`_catch_up_on_start` 以「当日有候选池记录」判定 pre_market 已跑，而 pre_market 可「建池后撞上限早退」，故 catch-up 不会重试补建计划。

## 修复方向（待评审）

1. **EOD 过期滞留非终态承认（日内单语义）**：post-market 扫 `created_at 日期 < 会话日` 且未落 OPEN 持仓的 APPROVED + EXECUTING → EXPIRED，镜像其计划 → EXPIRED。使 active 恒收敛到「今日在途」。
2. **修 EXECUTING 回滚泄漏**：`_auto_execute` fill 失败时把 EXECUTING 复位（→APPROVED 或 →EXPIRED）。
3. **一次性清库**：备份后过期存量积压（CN 78 / US 19 / CRYPTO 172），立即解锁。
4. **（可选）catch-up 幂等改判**：以「当日有计划/承认」而非「有候选池」判定 pre_market 完成。

## 附：CRYPTO 57 笔固着 EXECUTING 调查（2026-09-02）

- 现象：清库时 CRYPTO 除 <今日 的 115 笔外，另有 **57 笔今日 EXECUTING**（已一次性清）。
- 创建时刻分布：03:00×2 / 04:00×2 / **08:00×13 / 09:00×38** / 10:00×2 —— 51/57 集中在 08-09 时段，
  **均在 10:04 调度器重启之前** claim。
- 两次重启（今日 10:04、本会话 12:55）跑的都是**旧码（无 claim 回滚）**；08-09 的 auto_execute
  失败日志在 10:04 重启 `> var/scheduler.log` 覆写时**丢失**，无法逐笔坐实失败原因。
- 成因归类（二者现均已被防御覆盖）：
  1. **静默 fill 失败**（apply_fill 返 None）→ 新 `release_approval_claim` 回滚 APPROVED（当日重试）。
  2. **进程崩溃/重启中断的孤儿 claim** → 每日 EOD `expire_stale_active_approvals` 兜底过期。
- 遗留增强（未做，待评审）：调度器**启动时释放孤儿 EXECUTING**（进程新起时 DB 内任何 EXECUTING
  必为前实例遗留 → 安全复位 APPROVED），可把孤儿的恢复从「次日 EOD」提前到「重启即刻」。
