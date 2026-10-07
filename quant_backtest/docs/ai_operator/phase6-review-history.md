# 第六批 C：人工审核历史自审报告

冻结接口：`assess_human_review_history(store, capture_event_id, review_event_id, as_of, *, verifier=None)`。

仅修改 `ai_operator/review_history.py`、`tests_ai_operator/test_review_history.py` 和本报告；未修改 B、CLI、memory、DDL、E 盘或既有 unknown 标签逻辑，未提交。全量回归和真实验收由主代理统一执行，本代理未重跑全量。

## 实现与自审结论

先校验带时区查询时间且拒绝未来 `as_of`。在 `store._transaction(write=False)` 的一个一致只读快照中调用第五批 `assess_review_gate`，重验真实 capture/review 的完整性、绑定与实际落库可用时间。

通过既有 `events` 表枚举 `source.human_decision` 候选，取最多 10001 项，超过 10000 项直接拒绝，不以截断结果伪装完整历史。候选重验覆盖所有枚举事件；合法其他 capture/review 的事件不参与所选 pair 状态。相关事件的内容/哈希/身份/声明/时点篡改不会被静默跳过。

全部候选先复用 B 的 `_decision_event` 执行完整结构、哈希、绑定、接收/落库时点及撤销目标结构校验，再筛选所选 capture/review 和 `durable <= as_of`。只有可见相关事件且存在可信代码适配器时，才复用 B 的 `load_human_decision` 重验签名/证明及保存主体；其他来源或历史不可见事件不要求当前适配器为其认证。无适配器时不产生认证成功结论：无可见相关事件为 `pending`，有可见相关事件为 `unverified`。

候选读取后取得当前系统重验时点，再按查询 `as_of` 筛选实际可用事件。这样既拒绝实际系统未来落库时间，也允许查询开始后、快照建立前到达的合法事件仅对较早查询不可见。

可见相关事件仅按 `recorded_available_at` 排序；出现重复有效时点直接 `conflict`，不使用 event_id 字典序制造人工顺序。任何可见历史同刻冲突均保守阻断，不因后续事件自行消除。approve/reject 更新主决定，revoke 仅撤销其明确目标；撤销旧批准不影响新批准。过期最新批准不会回退旧批准，最新拒绝也不会被旧批准覆盖。批准在 `as_of == expires_at` 时已经过期。

文档缺失/不匹配始终保留第五批阻断优先。即使认证人工批准有效，整体仍为 `blocked_provenance_gaps`，`source_verified`、`historical_availability_verified`、`training_eligible`、`eligible`、`production_eligible` 均为 false。raw 始终保留真实下载链/真实性与历史到达/可用时间缺口，qfq/hfq 额外保留复权算法/因子认证缺口。

输出只包含相关决策事件 ID 和当前主决定 ID，不返回 proof 或完整人工审核事件，避免在历史查询中输出认证秘密。

## RED 与 GREEN

首次 RED 命令在隔离目录执行：

```powershell
python -m unittest tests_ai_operator.test_review_history.ReviewHistoryTests.test_pending_no_adapter_has_no_write_or_source_qualification tests_ai_operator.test_review_history.ReviewHistoryTests.test_missing_and_mismatch_documents_have_priority tests_ai_operator.test_review_history.ReviewHistoryTests.test_future_naive_and_invalid_asof_rejected tests_ai_operator.test_review_history.ReviewHistoryTests.test_query_over_ten_thousand_candidates_refuses_partial_history -v
```

结果：4 项、17.837 秒，`FAILED (errors=7)`；均因新 `ai_operator.review_history` 模块尚不存在，预先写好的真实第五批工程夹具未取得实现。

首次联调 GREEN：`python -m unittest tests_ai_operator.test_review_history -v`，19 项、26.612 秒，`OK`。随后自审识别查询开始与快照建立间的合法后到边界，先补复现测试：

```powershell
python -m unittest tests_ai_operator.test_review_history.ReviewHistoryTests.test_record_arriving_between_query_start_and_snapshot_is_invisible -v
```

结果：1 项、0.279 秒，`FAILED (errors=1)`；候选事件合法落库于查询启动时点之后，被旧实现误报尚未落库可用。修复为读取候选之后取得重验当前时点。

最终 GREEN：`python -m unittest tests_ai_operator.test_review_history -v`，21 项、6.701 秒，`OK`，0 跳过。候选上限夹具改用一个真实数据库事务批量写入，避免 10001 次单行自动提交的测试开销。

21 项覆盖 pending/缺口/不匹配、前后复权缺口、未来与无时区查询、错配 pair、认证批准仍不放行、无 adapter 有事件 unverified、缺口优先、后到 issued/recorded 不可见、最新拒绝、指定目标撤销、过期不回退、同刻 conflict、合法其他 pair、篡改及重哈希但无有效签名、非法 recorded 时点、超过 10000 候选拒绝，以及真实 SQLite WAL 只读快照中并发追加不改变本次读取结果。

测试通过真实本地 JSON capture/source review 夹具落库；认证工程适配器只存在于 tests，使用 HMAC 绑定完整规范 statement，修改声明不能沿用原签名。正式实现没有默认成功认证、JSON 认证开关或任意模块加载入口。

## 冻结 SHA256

- `ai_operator/review_history.py`（两项独立审查 P2 修复后）：`7be02fcc52fc2d25e1c6acc6af495cd7dae28e2fa7ebc7e73faaf958df81eac2`
- `tests_ai_operator/test_review_history.py`（两项独立审查 P2 修复后）：`4dcc61e02e202642c27a4a6825edd2310a0ba2931da6f3a65d0c624a4093875d`

## 仍未完成的认证

可信适配器是正式应用代码需要部署的依赖注入接口，本批测试签名夹具不代表真实人工身份/授权通道已部署。人工审核和数据库内容哈希无法认证真实下载链、来源真实性、历史到达时间或复权算法和因子，管理员完整重造数据库仍不在本批完整性证明能力内。历史查询也不发现其他未显式登记的人审或来源资料。

## 独立审查 P2 接口修复复验

B 的可信认证接口修订为 `verifier.verify(statement, proof, *, received_at)`：登记传入当前系统接收时点，历史重验传入保存的原始 `payload.received_at`，撤销目标按目标自身原接收时点重验。授权是否有效必须针对系统接收时点，不能仅根据客户端声明的签发时点，也不能用今天的查询时点抹掉过去有效接收的批准。

C 生产逻辑未改变；仅将自有签名测试认证器和并发测试认证器适配上述关键字，并新增历史授权边界测试：批准在接收时点 1 授权有效，权限窗口于时点 10 到期，历史查询时点 50 仍正确回放批准；最终仍 `blocked_provenance_gaps`，资格保持 false。

RED 命令：`python -m unittest tests_ai_operator.test_review_history.ReviewHistoryTests.test_historical_approval_uses_original_authorized_receipt_time -v`。旧 B 接口下 1 项、0.162 秒，`FAILED (errors=1)`；未传系统 `received_at` 导致测试认证器拒绝。

B 完成修复后的定向 GREEN 命令：`python -m unittest tests_ai_operator.test_review_history -v`。22 项、3.843 秒，`OK`，0 跳过。未重跑全量，也未编辑 B 或其他所有权文件。

## 独立审查第二项 P2：认证范围与历史可见性

原实现对全库候选先认证再过滤，导致当前适配器无法识别其他合法来源的授权主体，或无法识别历史尚不可见记录的证明时，错误地阻断本应 pending 的所选来源历史。

先新增两项真实签名复现：另一 capture/review 的独立工程 authority 使用另一签名密钥；当前 capture/review 于时点 10 落库的另一 authority 记录，在 as_of=5 查询时尚不可见。均通过真实 B 认证登记，不伪造接收成功。两项 RED 命令：

```powershell
python -m unittest tests_ai_operator.test_review_history.ReviewHistoryTests.test_other_pair_authority_outside_current_adapter_remains_pending tests_ai_operator.test_review_history.ReviewHistoryTests.test_invisible_related_proof_outside_adapter_scope_remains_pending -v
```

结果：2 项、0.401 秒，`FAILED (errors=2)`；原实现对本次无需认证的记录调用了当前 adapter，均报认证失败。

修复为所有候选先纯结构重验，随后按所选 pair 与持久可见时点过滤，只对可见相关记录调用正式认证 load。全局结构/绑定/时点/内容哈希错误仍拒绝，10000 候选上限保留；已有相关篡改和重新哈希签名拒绝测试保持不变。新增不可见记录测试同时断言：在 as_of=10 可见之后，当前适配器无法认证其证明仍必须抛错。

最终定向 GREEN 命令：`python -m unittest tests_ai_operator.test_review_history -v`。24 项、4.489 秒，`OK`，0 跳过。仅修改 C 的生产、测试和报告三文件，未运行全量。
