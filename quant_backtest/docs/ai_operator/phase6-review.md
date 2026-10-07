# 第六批独立复审

复审者负责过A公开材料归档，未参与B/C/CLI/acceptance实现。依照主代理后续授权只读审查上述模块与测试，未改实现、memory、DDL、测试或E盘文件；本报告为新增审查交付。

最终复审状态：两项P2均已由实现代理修复并通过本代理针对性复验，无未关闭的确定代码缺陷。以下保留初审事实及复现证据，文末记录修复后行为、测试结果和最终hash。真实身份认证适配器、供应商来源/历史到达/复权认证和455天官方日历逐日覆盖仍未完成；没有批准任何真实来源或开启生产资格。

## 发现

### [P2，已修复] 真实认证接线前须明确认证有效时点与历史重验语义

位置：`ai_operator/human_review.py:92`、`:96`、`:188`、`:205`、`:208`；契约认证器目前只有 `verify(statement, proof)`。

记录时已计算系统 `received_at`，但认证器只能收到客户端规范声明和证明，读取时也只收到同一参数。认证器没有系统认证上下文来判断授权应在 `issued_at`、实际接收或当前查询时点成立。具体场景：声明10:00签发，人审权限10:01到期/撤销，10:02才提交，声明本身expires_at为次日。按签发时权限作稳定重验的适配器会接收此迟到提交；改用当前权限查询又会在今天权限失效时拒绝过去原本有效的批准，与稳定历史回放要求冲突。规范声明的expires_at是批准有效期，不能代表认证授权有效期。

当前正式源码没有成功适配器，CLI `human-review` 在 `ai_operator/cli.py:205` 至`:213` 只读打开库后固定拒绝，5类最终资格仍false。因此这里是**真实认证部署前置接口缺口**，不是已开启的CLI绕过，也不否认本批默认拒绝的工程状态机通过。应在正式适配器接线前冻结一个受系统控制、持久绑定的认证有效时点/模式上下文，并定义历史授权策略与撤销证明的可重验版本；记录时验证该接收时点，历史重验针对同一个过去认证时点，不向过去带入后来授权变化。若业务明确选择“以签发时点授权为准”，也应明示迟到提交的有效期与防重放策略，不默认为接收时授权。

测试缺口：已有HMAC夹具绑定完整声明和身份，但没有验证“授权过期/撤销后迟到提交拒绝，同时过去有效批准仍能回放”。这两项应成为真实适配器契约验收，工程HMAC签名成功不能代替它们。本次没有编造或部署真实身份适配器，也未改接口。

### [P2，已修复] 不相关或历史不可见证明会错误阻断所选审核历史

位置：`ai_operator/review_history.py:78`、`:81`（初审源码哈希见文末）。有verifier时，所有候选事件先通过 `load_human_decision(..., validation_now, verifier=verifier)` 做身份认证，之后才按所选pair和durable<=as_of过滤。当前适配器只认可其授权域时，别的capture/review合法批准会抛认证失败；晚于as_of、本应不可见的证明不能重验时，也会阻断更早历史。这与合法其他pair不参与当前状态、后到记录不可见的查询要求不符。

独立定向实测两例（使用已有工程夹具和临时库，无真实凭据）：1. 保存其他pair的合法签名批准，仅认可所选capture授权的适配器查询所选pair，实际 `OTHER_PAIR_RAISED`，应pending；2. 所选pair在第10秒批准，查询第5秒历史，适配器拒绝该不可见后到证明，实际 `INVISIBLE_RECORD_RAISED`，应pending。两个错误均为固定的“人工审核认证失败”，临时夹具清理正常，命令退出码0代表复现脚本执行完成，不代表接口行为符合预期。

建议先借B纯结构helper核验全局事件完整性和时点，再过滤pair/实际可用时点，最后只对可见相关事件调用认证适配器。这样仍拒绝结构篡改/非法时点/可见证明无效，当前认证器未覆盖的其他授权域及后到不可见凭据则不影响所选历史。

除上述两项初审发现，未发现默认关闭的B/C/CLI行为中可确定的新增高优先级缺陷。全库完整重造、外部可信时间、真实身份与供应商认证仍属明确能力边界。两项修复复审证据见文末。

## 初审逐项审查结果

| 事项 | 核查依据与结论 |
| --- | --- |
| 身份输入与默认拒绝 | human_review.py:86要求可信代码适配器；:74重验严格principal；cli.py:208拒绝顶层注入字段，:213固定拒绝，无用户模块或名单加载入口 |
| 规范声明/签名重验 | human_review.py:92复制声明与proof，拒绝适配器修改输入；:144核对事件/载荷hash；:188重验原证明且:189比较保存principal；重hash不能免除签名有效性 |
| approve文档绑定 | human_review.py:105绑定具体capture/review和签发时点；:110要求未来批准有效期；:114只接受文档全部匹配状态，文档缺失/不一致无法批准 |
| 撤销目标与权限 | human_review.py:118限定目标approve、同pair；:124同时比较authority_id与principal_id；:192重验目标证明；source_admin或原批准主体才可撤销 |
| 事务与时点 | human_review.py:206在既有BEGIN IMMEDIATE事务内完成绑定/认证/目标检查/事件与outbox插入/重验；:164拒绝记录早于接收或系统未来，异常回滚；:169实际落库晚于as_of不可见 |
| 过期和新决定 | review_history.py:30新approve/reject替换主决定；revoke仅针对其ID；:42先检查主批准被撤销，:44按历史as_of判过期，不回退旧批准 |
| 后到材料与回放 | review_history.py:57一致只读快照，先第五批capture/review门禁；:69以当前时点重验候选后，:81只纳入实际可用时点不晚于as_of且匹配pair的事件；后到审核不倒填过去 |
| 同刻冲突 | review_history.py:25任何可见相关同有效时点多观察均conflict，未用hash排序制造人工先后 |
| 未配置认证查询 | review_history.py:73只做结构重验，:23有可见记录则unverified；无记录pending，未输出已批准；文档缺口在:85优先阻断 |
| 资格与公开证据边界 | review_history.py:93五项固定false；:86保留来源/历史缺口，qfq/hfq追加因子缺口。A原件未被手工规范JSON填入gate，455天未核验缺口继续阻断 |
| CLI只读与错误 | cli.py:218严格输入、已有独立库预检和readonly；未初始化库不创建。新入口沿用已有8MiB输入/重复键拒绝与JSON错误契约 |
| 真实缺口验收 | acceptance.py:75缺口审核，:90伪身份预期退出2，:94验证零新增事件；:98 pending/missing，:100检查5资格false；:117 audit_only/null，:123完整3事件导出，:128源hash前后比较；验收没有把夹具认证当真实认证 |

候选上限为全库10000条，超限拒绝且不截断。全库纯结构损坏或超过上限保守拒绝符合当前契约；全库身份认证在pair/可见性过滤前则存在第二项P2的功能缺陷，不能混同为结构完整性限制。

## 独立定向验证

只运行8项风险对应测试，没有重复全量回归，工作目录 `work/ai_operator_phase6`：

```powershell
python -B -m unittest tests_ai_operator.test_human_review.HumanReviewTests.test_rehashed_statement_still_requires_original_signature tests_ai_operator.test_human_review.HumanReviewTests.test_revoke_load_reauthenticates_target_approval tests_ai_operator.test_human_review.HumanReviewTests.test_record_clock_anomaly_rolls_back_event_and_outbox tests_ai_operator.test_review_history.ReviewHistoryTests.test_legal_later_event_is_invisible_to_earlier_asof tests_ai_operator.test_review_history.ReviewHistoryTests.test_same_effective_time_is_conflict_without_hash_order tests_ai_operator.test_review_history.ReviewHistoryTests.test_readonly_transaction_snapshot_excludes_concurrent_append tests_ai_operator.test_phase6_cli.Phase6CLITests.test_no_authenticator_rejects_claimed_human_without_writing tests_ai_operator.test_phase6_cli.Phase6CLITests.test_history_missing_evidence_remains_pending_and_readonly -v
```

实测：8项、1.300秒、OK、退出码0、无跳过。对应签名绑定、目标重新认证、事务回滚、后到不可见、同刻冲突、SQLite只读快照并发追加隔离、CLI未认证零写入、缺口pending与5资格false。

阅读了B自审25项和C自审21项最终GREEN报告。主代理另通报全量369项、365通过、4项既有权限跳过，以及独立真实缺口库10个命令验收；本复审未自行重跑这些全量/真实验收，明确区分证据来源。

## 初审源码哈希

- human_review.py：`80e10f57a1f83329bbbd259d477965f3126fd63ef172126b75e92320a957429a`。
- review_history.py：`d42b7cf804735caf639ba7e7af27021205fab2d3ce579bdd6d085a38851883a5`。
- cli.py：`49dcce4e824c1e0dfeb55ea77808e77200857e64584d1f10698b126c287798f5`。
- acceptance.py：`cfec78018bf28aa525e584c06f822fcf7028278a51ec3f079f8eb6871786d223`。

后续实现或认证接口有改动时，上述定向结果只对这些哈希对应版本成立，需要对相关改动重新复审。

## 修复后针对性复审

P2认证时点：现行 `human_review.py:92` 的 `_verify` 接收系统context，`:95`规范化，`:97`调用 `verify(statement, proof, *, received_at)`。记录路径`:209`传本次系统接收时点，读取路径`:189`传保存payload.received_at；此前`:162`至`:170`重验保存时点规范、事件时间一致及实际可用。契约明确使用归档策略/授权断言核验原系统时点，客户端issued_at不能代替context；今天授权失效不抹掉过去批准，接收时已经失效的迟到声明拒绝。正式源码仍无成功认证器。

独立运行B新增的 `test_authorization_expired_after_issue_before_receive_zero_write`、`test_authorization_revoked_after_issue_before_receive_zero_write`、`test_past_authorization_replays_after_current_revocation` 和C新增 `test_historical_approval_uses_original_authorized_receipt_time`：4项、0.433秒、OK、退出码0。检查工程授权窗口及撤销时点参与签名，不是自填身份。真实适配器仍需可信归档授权/撤销策略，这些夹具只证明context契约能表达并校验相关时点。

P2候选过滤：现行 `review_history.py:73` 先对所有候选做纯结构重验，`:74`至`:75`过滤pair/历史可见性，`:80`仅认证可见相关记录。全库结构损坏仍可拒绝；其他authority或不可见证明不需要当前认证器认可。可见撤销继续由B认证其自身及明确目标，目标不通过不能产生撤销成功。

独立运行以下6项：

```powershell
python -B -m unittest tests_ai_operator.test_review_history.ReviewHistoryTests.test_other_pair_authority_outside_current_adapter_remains_pending tests_ai_operator.test_review_history.ReviewHistoryTests.test_invisible_related_proof_outside_adapter_scope_remains_pending tests_ai_operator.test_review_history.ReviewHistoryTests.test_historical_approval_uses_original_authorized_receipt_time tests_ai_operator.test_review_history.ReviewHistoryTests.test_tampered_related_event_is_rejected_with_or_without_adapter tests_ai_operator.test_review_history.ReviewHistoryTests.test_rehashed_changed_statement_cannot_reuse_signature tests_ai_operator.test_review_history.ReviewHistoryTests.test_illegal_recorded_time_cannot_be_silently_skipped -v
```

实测：6项、1.074秒、OK、退出码0。新增两项使用真实不同工程HMAC key/authority；其他pair查询及后到记录不可见查询返回pending，后到记录到达其可见时点后同一不匹配认证器仍拒绝。相关结构篡改、重hash但原签名失效、非法recorded时点拒绝保持有效。

本代理未修改实现或重复全量。B修复报告28项GREEN、C修复报告24项GREEN由对应实现代理提供，最终全量/目标接入复验由主代理负责。

最终修复版本SHA256：

- human_review.py：`20d0d02cbb05edb00b309277220ca1e6cc865569468ef20b43c32a9499001712`。
- review_history.py：`7be02fcc52fc2d25e1c6acc6af495cd7dae28e2fa7ebc7e73faaf958df81eac2`。
- test_human_review.py：`86f6d276001745ee14e9a5881c8b7b21affd31b0926c3a1f9a37ee1f5003fcc6`。
- test_review_history.py：`4dcc61e02e202642c27a4a6825edd2310a0ba2931da6f3a65d0c624a4093875d`。
- CLI与acceptance未因上述修复改动，审查hash沿用初审记录；若主代理后续改动，须以其最终接入复验为准。
