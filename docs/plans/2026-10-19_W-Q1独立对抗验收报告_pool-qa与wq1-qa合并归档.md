# W-Q1 独立对抗验收报告（wq1-qa-1019）
> STATUS: unspecified（owner: TBD；2026-11-07 前补状态或标 superseded）



验收对象：6301a239e52c9924fe51c61cbec07e26277d7ac2（7 files / 1356 insertions）
验收人：独立 QA 工位 wq1-qa-1019（新鲜视角，未复用实施方测试代码）

## 总结论：验收通过（无产品缺陷，2 个观察项）

⚠️ 验证路径说明：本会话中对 upstream_inbox.py / dispatch_ledger.py / 三个 test_wq1_*.py 的逐行内容读取多次触发 SENSITIVE_APPROVAL 超时（Read / cat / Grep 均被拦），按纪律未重试未绕行。B 项"逐行代码审读"改用替代验证路径完成：运行时内省（inspect 签名 / dataclass 字段 / 常量）+ 行为探针 + 自写 23 项对抗测试实证。行为契约验证强度不受影响；逐行级审读（注释意图、未触发分支）未覆盖，见观察项 2。

## A. 提交完整性 — PASS
- git show --stat：恰 7 文件、1356 insertions，与自报一致。
- 7 文件 sha256 逐一核对：全部与实施方指纹一致；quant_sector 4 文件 working tree 实测 sha256 与提交完全相同（无漂移）。
- watch_dispatch 包：git diff 6301a239^ 6301a239 -- quant_backtest/watch_dispatch/ 为空 = 零改动。
- 注：watch_dispatch 实为 10 个 .py（非任务书的 9），signal_pool.py 由前一提交 4f367ee6 引入，与本切片无关。

## B. 契约对照（行为实证）— PASS
- QuantUpstreamReport：frozen（FrozenInstanceError 实证）；non_executable=False 直接 raise EXECUTION_FORBIDDEN（比"恒定 True"更严格的 fail-closed）；report_kind 仅 ANOMALY|HEALTH（SPAM→REPORT_KIND_INVALID）；hash 64hex 校验（短 hash→ENVELOPE_HASH_INVALID）。
- accept 幂等：同 (report_id, envelope) 重受理返回完全相同 receipt+ACK（== 实证）；同 id 异 envelope→RECEIPT_CONFLICT；naive now→ACCEPTED_AT_INVALID；非零 offset（UTC+8 实测）拒绝；verification_source 恒 "upstream-inbox-v1"；acked_at≥accepted_at 成立。
- revoke：只进不退、重复撤销→RECEIPT_ALREADY_REVOKED；已撤销重受理→RECEIPT_REVOKED（其他新 report_id 仍可受理）。
- DispatchLedger：路由校验在构造期即拒（SPAM_ROUTE→ROUTE_INVALID）；intent.route 与 ledger 不符→ROUTE_MISMATCH（比契约更细的独立错误码）；inbox_lookup 未注入 fail-closed→UPSTREAM_NOT_ACCEPTED；lookup=False→同；intent.receipt_id 与凭证不符→同；已撤销（is_active）→同。UPSTREAM_NOT_ACCEPTED 四分支全部实证拒绝。
- plan 幂等（重 plan 返回相同对象，status READY）；claim：lease_until≤now→LEASE_WINDOW_INVALID；naive→CLAIM_NOW_INVALID；活跃租约内并发 claim→INTENT_NOT_CLAIMABLE；attempt_no 递增 1/2/3，第 4 次→ATTEMPTS_EXHAUSTED。
- finish：UNKNOWN 后 claim 被拒（无重试路径可走）；凭证缺失（无 external_message_id/observed_at 配对）强制 UNKNOWN 且 id 被清空；DEFINITELY_REJECTED 双凭证齐全→intent 回 READY、可受控重试、attempt_no 递增；ledger revoke 显式 epoch 须严格递增（=当前→EPOCH_NOT_ADVANCED，+3→OK）。
- projections：snapshot 对错误类型 fail-closed（ProjectionError）；CONNECTION_STATUS="NOT_CONNECTED"。
- 反向依赖：quant_sector 4 源文件中 "watch_dispatch" 仅 3 处注释（dispatch_ledger.py:9 / upstream_inbox.py:88 / __init__.py:10），0 处 import 实证。
- trading_permitted：4 模块及全部导出类型 not hasattr 实证通过。

## C. 独立对抗重放 — PASS（自写 23/23 全绿，跑完已删除）
覆盖任务清单全部 13 项：同 id 异 hash 冲突、重受理幂等、撤销后重受理、撤销后 plan 拒绝、claim 窗口/租约、3 次上限、UNKNOWN 无重试路径、DR 受控重试 attempt_no 递增、epoch 显式回退拒绝、naive 与非零 offset、script+引号注入转义、not hasattr(trading_permitted)，另加 frozen/hash 校验、凭证配对强制 UNKNOWN、投影 fail-closed、跨路由互不引用。
开发期 6 次失败 triage 全部为我方测试 bug（kwargs 笔误、property 误当方法、frozen intent 误改、对更严错误码 ROUTE_MISMATCH / EXECUTION_FORBIDDEN 的预期偏差），修正后全绿；无一项归因被验收代码。

## D. 回归 — PASS（实跑数）
- test_watch_dispatch_*.py（含 signal_pool）：78 passed。
- test_wq1_*（3 套件）：22 passed。
- signal_pool + wq1 合跑：42 passed。0 failed。
- 全仓收集错误实为 18 个（任务书预估 10）：原因实证为缺 pandas、缺 tzdata、tools.xq_ticker_normalize 非 package 等环境性问题；相关测试文件与 tools 目录在 6301a239^ 时点状态一致（test_xq_ticker_normalize.py 始于更早提交 659884b7），非本切片引入。

## E. 离线与性质 — PASS
- sys.addaudithook 实证：完整 accept→revoke→plan→claim→finish→snapshot→render 生命周期中 socket.connect / getaddrinfo / urllib.Request / subprocess.Popen 事件 = 0，文件写事件 = 0。
- render_readonly_html：<script>（标题+单元格双路）与引号注入均转义（&lt; &quot;），无 form/button；CONNECTION_STATUS 恒 NOT_CONNECTED 并呈现于 HTML。

## 缺陷清单
- 产品缺陷：0
- 测试缺陷：0（实施方 22/22 绿；我方临时测试缺陷已自行修正）
- 观察项（不阻塞验收）：
  1. 租约过期未 finish 的 CLAIMED intent 无自动回收路径（claim 恒 INTENT_NOT_CLAIMABLE，需人工 finish UNKNOWN）。与"UNKNOWN 终态不自动重试"的保守设计一致，但长期运行可能积压悬挂 intent，建议后续切片提供 lease-expiry 显式回收语义。
  2. 逐行源码审读因 SENSITIVE_APPROVAL 拦截未执行；B 项结论基于运行时行为实证。如需源码级复审，请安排有读取权限的会话。

临时测试文件（test_wq1_qa_adversarial_tmp.py / probe2_tmp.py）已按纪律删除，无残留。
