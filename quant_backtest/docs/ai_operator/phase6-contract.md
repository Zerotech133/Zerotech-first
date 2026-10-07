# 第六批实施契约：人工审核与历史回放

目标：落实第六批任务清单的A/B/C/D。复用第五批事件、接收和文档匹配。实际人工认证通道尚未存在，本批实现可信代码注入的认证适配器接口，默认拒绝，CLI不提供任意名称、布尔值或配置文件认证后门。测试适配器只放tests，正式源码不自带可接受凭证的适配器。

## 隔离与所有权

非Git项目沿用隔离副本 `work/ai_operator_phase6/`，基线为第五批317项（313通过、4权限跳过）。B只写human_review.py、test_human_review.py、reports/human_review.md。C只写review_history.py、test_review_history.py、reports/review_history.md。A只写evidence/及reports/evidence.md。主代理写CLI、CLI测试、acceptance.py、交付文件。没有生产库接线、DDL、memory修改、端口、服务、训练或交易。

## B 认证审核事件接口

`record_human_decision(store, statement, proof, *, verifier=None) -> dict`

`load_human_decision(store, decision_event_id, as_of, *, verifier=None) -> dict`

认证适配器为可信应用代码对象，提供 `verify(statement, proof, *, received_at) -> dict`。返回严格 `{principal_id, authority_id, policy_id, role}`，三ID非空字符串，role为source_reviewer或source_admin。它应验证签名/凭证、人工主体与授权策略，并按给定系统接收时点核验授权有效期和撤销状态；不能仅相信输入中的身份字段或客户端issued_at。记录时传本次系统received_at，读取时传原事件保存的received_at，借助归档策略/授权断言稳定重验原时点；今天授权到期/撤销不能抹掉过去有效批准，接收时已到期/撤销的迟到声明必须拒绝。没有适配器抛ValueError。应用依赖注入不是网络/JSON请求字段；CLI不加载用户指定Python模块、验证函数、策略或名单。适配器仍未部署，不能宣称实现了真实身份认证。

statement严格字段：schema_version=1、request_id（32位小写hex）、action（approve/reject/revoke）、capture_event_id、review_event_id、target_decision_event_id、reason（非空）、issued_at、expires_at。三个事件ID为64位小写hex，target只在revoke非null。时间必须带时区并规范化；issued_at不得早于review落库时刻，也不得晚于系统received_at。approve必须有未来expires_at；reject/revoke必须expires_at=null。proof为严格JSON对象，规范UTF8至多64KiB，不接受任意运行对象。规范化statement后传给适配器，持久保存同一份规范声明和proof。

approve只允许第五批assess_review_gate状态blocked_pending_human_review（文档全部匹配），缺失或不一致时拒绝；reject可登记缺口材料。revoke必须绑定真实approve事件和同capture/review；原批准主体或source_admin才能撤销。load重验完整event/payload哈希、kind/source/environment、capture/review和规范声明、适配器身份与保存principal相等、received_at及recorded_at，按实际落库时点可用。批准是否过期由C判断，重验不能因为今天过期而抹掉过去批准。

事件kind=source.human_decision、source=ai_operator.human_review、environment=shadow。payload严格 `{schema_version, observation_id, statement, proof, principal, received_at}`；observation_id系统uuid4.hex，接收每次独立观察；source_id为statement规范对象SHA256，occurred_at/available_at=received_at。record和load返回 `{decision_event_id, statement, principal, recorded_available_at, event, eligible:false, production_eligible:false}`。不输出认证秘密或将proof当作可执行内容。客户端重放记录新观察，不宣称请求幂等；无法确定相同有效时点多观察顺序时C阻断。

实现收紧：记录使用既有store._transaction与store._insert_event，把绑定、认证、撤销目标检查、单事件/outbox插入及重验放在同一事务内。这样避免校验后目标并发变化，并在落库时钟异常或二次认证失败时回滚。不改memory接口或DDL；后续memory内部API变化需复验本模块。

## C 历史资格接口

`assess_human_review_history(store, capture_event_id, review_event_id, as_of, *, verifier=None) -> dict`

先使用第五批assess_review_gate核验接收、具体审核报告与时点。只读事务形成一致查询快照。利用既有store._conn查询source.human_decision的event_id，先用B的纯结构helper在validation_now重验所有候选的事件完整性/语义/时点，再按所选capture/review与durable<=as_of过滤，仅对可见相关事件调用B的load做认证。这样其他authority的合法记录或尚不可见的证明不会影响当前查询；全局结构篡改与可见相关签名错误仍拒绝。超过10000条候选事件拒绝查询，不截断成伪完整历史。未来as_of拒绝；实际recorded_at>as_of的合法未来落库记录不可见。相关篡改、不合法时点必须拒绝，不能静默跳过。与其他capture/review相关的合法记录不参与当前状态。

可见事件按recorded_available_at升序；同一时点有多个相关事件时状态conflict，不用event_id字典序认定人工先后。approve/reject为新的主决定；revoke只影响其明确目标，撤销旧批准不影响新批准。有效批准过期后不得回退旧批准；拒绝同样不能被旧批准覆盖。输出human_status为pending/approved/rejected/revoked/expired/conflict/unverified；decision_event_ids为本次可见相关ID、active_decision_event_id、checks与review_gate_status、as_of、status。status在文档缺失/不一致时保留第五批阻断优先；文档匹配时使用blocked_pending_human_review/blocked_authentication_unconfigured/blocked_human_rejected/blocked_human_revoked/blocked_human_expired/blocked_human_conflict或blocked_provenance_gaps。

缺verifier时不能读出已批准结果；若有可见相关审核事件，返回unverified/blocked_authentication_unconfigured（文档缺口仍优先）；无事件则pending。源码没有默认认证成功实现。本批即使人工文档批准，仍source_verified=false、historical_availability_verified=false、training_eligible=false、eligible=false、production_eligible=false；不改变journal-outcome和unknown标签。remaining_gaps明确真实下载链/真实性、历史到达及复权因子等尚未认证。

## CLI和验收

新增human-review --db --input，输入严格statement/proof；当前默认无verifier，合法输入返回2且零写入，说明未配置认证适配器。新增review-history --db --input，输入capture_event_id/review_event_id/as_of，readonly查询。沿用8MiB请求、重复键拒绝、已有独立库预检及JSON错误契约；不动第五批review-gate返回格式。

A独立核实官方公开资料，实际访问失败明确记录，不将手工匹配的规范JSON标成官方原始证据。D实际存量行情接收和缺口审核、拒绝伪身份批准、重开历史pending/缺口、outbox完整导出，验证源文件SHA不变。工程夹具测试approve→reject/新批准→revoke/expiry及时间边界，不混入真实验收库。

## 执行台账

- [x] 核对第五批接口、真实缺口与既有三目录边界。
- [x] 冻结本实施契约，用户已授权团队按第六批计划实施。
- [x] A公开材料核查与原始证据归档：21次获取、18份正文，455天逐日对照仍未完成。
- [x] B审核记录先RED再GREEN，28项通过，认证时点P2修复并独立复审通过。
- [x] C历史查询先RED再GREEN，24项通过，候选认证顺序P2修复并独立复审通过。
- [x] CLI联调、真实缺口验收、全量回归及全批复审：修订后375项、371通过/4权限跳过；真实缺口仍阻断。
- [x] 限定目录接入、目标复验与交付：6个接入文件哈希一致，目标375项中371通过/4既有权限跳过，44.074秒退出0；真实验收保持缺口阻断。
