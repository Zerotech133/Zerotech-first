# 第六批 B：人工审核记录自审

## 范围与实现

- 仅新增 `ai_operator/human_review.py`、`tests_ai_operator/test_human_review.py` 及本报告。没有修改 memory、DDL、CLI、既有模块或 E 盘；没有创建 Git 提交、工作树、服务或端口。
- 实现 `record_human_decision(store, statement, proof, *, verifier=None)` 与 `load_human_decision(store, decision_event_id, as_of, *, verifier=None)`。没有适配器或适配器没有 `verify` 方法均拒绝；适配器必须由可信应用代码注入。
- 正式模块不提供认证成功实现。工程 HMAC 夹具只存在于测试中，完整规范声明与主体/权限共同签名。真实签名、人工主体和授权策略的认证通道尚未部署，不能把工程测试称为真实人工认证。
- statement 字段、ID、动作、目标、原因与带时区时间严格校验；规范声明和同一份规范 proof 保存到事件。proof 必须是标准 JSON 对象，规范 UTF8 不超过 65536 字节，拒绝循环、非 JSON 类型、非有限数、非法 UTF8 与超限值。
- **proof 仅保存认证断言或签名，调用方不得上传密码、访问令牌、私钥等秘密。** 按契约结果含完整 event，因而会回显持久 proof；认证适配器异常固定为认证失败，不输出原异常及其秘密内容，也不执行 proof。
- principal 严格四字段；三个 ID 非空，role 限定为 `source_reviewer` / `source_admin`。重验结果必须与保存 principal 完全相等；拒绝适配器修改 statement/proof。
- 认证接口收紧为 `verifier.verify(statement, proof, *, received_at)`：record 传系统规范接收时点，load 传该事件保存的原始接收时点，撤销目标独立沿用目标事件接收时点。真实适配器须依据签名授权或归档策略核验系统接收时点的有效权限，不能仅依据调用方 issued_at，也不能以今天已撤销抹掉历史有效权限。
- 复用 `load_capture`、`review_gate._review_event`、`assess_review_gate` 校验 capture/review、时点与文档状态。approve 只允许文档全部匹配，reject 可以登记缺口。
- revoke 目标必须是真实 approve、同 capture/review，且在撤销接收时点已落库；原主体或 source_admin 才可撤销。原主体按 `authority_id + principal_id` 识别，防不同认证域同名。load 会重新认证目标批准，结构 helper 先限制目标动作以避免伪造撤销链递归。
- 接收与实际落库时点分别校验；received_at 不早于 issued_at，issued_at 不早于 review 落库，recorded_at 不早于 received_at，且不晚于系统当前时间。查询不得在未来，实际落库晚于 as_of 时拒绝可见。
- approve 的未来 expires_at 相对原 received_at 检验；load 不用今天的时钟重新判定批准过期，所以过期后仍可回放原批准，历史资格由 C 判定。
- 每次接收生成系统 UUID4 observation_id。同声明同钟记录得到不同事件，source_id 都是规范 statement 的 SHA256。客户端重放不是幂等写入，同有效时点多观察顺序交由历史层阻断。
- 使用既有 `store._transaction()` 与 `_insert_event()` 完成校验、写入、落库后重验；发现时钟异常或认证重验失败会回滚 events/outbox。未改 memory。load 只读、不触发写事务；C 可在其只读快照内调用。
- 返回契约要求的七个字段，`eligible=false`、`production_eligible=false` 始终保留；不提升来源真实性、历史到达或训练资格。

## 与 C 的接口协调

内部 `_decision_event(store, decision_event_id, as_of)` 返回 `(event, statement, principal, recorded_available_at)`。该 helper 重验完整事件/载荷哈希、来源、环境、严格字段、时间、capture/review、文档门槛及撤销结构权限，但不认证身份，不能单独当作批准结果。

C 无适配器时可以借此验证结构并返回 unverified；有适配器时必须使用公开 `load_human_decision`。C 对历史窗口以外合法记录的过滤需先在当前时点结构重验，再判断实际 recorded_available_at 是否超过查询 as_of，不能对相关篡改静默跳过。上述接口已向 C 任务说明。

## RED / GREEN 证据

工作目录：`work/ai_operator_phase6`。

```powershell
python -m unittest discover -s tests_ai_operator -p test_human_review.py -v
```

1. 实现前 RED：20 项，20 个失败；各失败均为“缺少契约要求的人工审核模块”的显式断言，命令退出码 1，未编写正式实现。
2. 首轮 GREEN 尝试：20 项，19 项通过，1 项失败。失败是测试助手将显式 `proof=None` 替换为自动签名，未实际把 null 送给正式接口；改为专用默认 sentinel，保留 null 的拒绝断言。
3. 初版 GREEN：25 项全部通过，12.203 秒，退出码 0。新增的五项覆盖重算事件/来源哈希后声明签名仍须有效、新签名不能绕过时点语义、适配器修改输入/主体漂移、不同 authority 同名不能撤销、撤销加载重新认证目标。

## 独立复审 P2 修复

复审指出原两参数适配器接口缺少系统接收时点，可能按调用方 issued_at 接受授权到期或撤销后的迟到提交，也可能以当前授权状态破坏历史回放。现已增加必需的 keyword-only `received_at`，没有兼容回退。

工程 HMAC 夹具增加完整签名的权限有效窗口和撤销时点，正式源码仍没有成功认证适配器。新增三个案例：签发有效但接收到期零写入、签发有效但接收前撤销零写入、原接收有效但今日到期且撤销仍能 as_of 回放。验证命令保持仅运行自己的文件：

```powershell
python -m unittest discover -s tests_ai_operator -p test_human_review.py -v
```

- 修复前 RED：28 项中 26 项通过，2 项失败，4.394 秒，退出码 1。两个迟到提交测试均实际写入了审核，明确因未按 received_at 核验授权而失败；新增历史回放测试通过。
- 修复后 GREEN：28 项全部通过，4.335 秒，退出码 0。原有 25 项也全部通过，两个迟到提交现拒绝且 events/outbox 无变化，历史批准按原始 received_at 校验并保留可回放性。
- C 自有夹具由 C 任务更新，本任务没有改 C 或执行全量回归。已通知 C 接口完成，可进行定向联调。

测试覆盖缺适配器/伪适配器、严格 principal、approve 缺口/文档错配/错绑定、reject 缺口、revoke 非批准/未知目标/跨 pair/原主体及管理员权限、过期后读取、未来查询/未来落库/时钟倒退、实际落库可见性、事件与 payload 哈希篡改、重 hash 语义拒绝、只读和失败零写入、签名绑定声明、系统 nonce 与 proof 严格 JSON/UTF8/64KiB 边界。

## 剩余验证

本任务按所有权约束只运行自己的测试文件。全量回归、CLI 联调、真实缺口验收和独立复审由主任务执行；上述结果不宣称那些检查已经完成。自审未发现需要更改契约或扩展本任务所有权的问题。真实认证适配器部署、真实下载链及历史到达真实性仍未验证。
