# 第五批：持久接收与来源证据审核实施计划

目标：把第四批当前快照写入既有独立审计库，支持连续接收链、按时点读取、来源证据逐项对照与可追溯的放行缺口报告。承接总体进度的下一项。用户已授权持续并行实施，本计划为执行台账。

架构：`work/ai_operator_phase5` 复制E盘第四批源码和测试，A负责持久接收模块，B负责证据对照模块，主代理负责登记、查询、标签衔接和CLI。标准库/Python3.11/unittest，复用memory.append_event和既有DDL；新增数据仅使用本次outputs专用验收库，生产库不接管。

## 约束与判定

- 当前接收必须调用第四批capture函数现场读取，不能导入任意JSON伪造历史接收。每次命令是新的一次观察；重跑产生新记录，不声称跨调用请求幂等。
- 快照和日历作为一个事件payload整体提交，避免半对行情/日历。recorded_available_at为接收时间与数据库recorded_at的最大值；重开库后可按as_of读取。
- previous_event_id可组成连续观察链；绑定source_id、code、period、price_basis、exchange、calendar.source_id，前后必须同stream_id且时间不倒退。新修订可沿同来源链登记，但不能覆写旧事件。
- 四类审核文档只是匹配证据，声明URL不是签名或下载证明；通过匹配的状态为ready_for_human_review，不能自动批准真实来源。所有放行输出eligible=false、production_eligible=false；未开发任何修改trusted开关。当前没有真实供应商/复权/官方日历完整证据，因此真实验收应报告缺口。
- 只读调查现有采集脚本和数据，避免输出配置/凭据；不执行同步器，不新增服务/端口/常驻任务，不训练、不交易。E盘接入仍限定已登记三个目录，本轮需技术写权限。
- 审查重点：事务单事件、并发只读与链前驱错配、篡改重哈希的语义、后到证据/落库时点、凭证不足和官方参考与同一数据自证的混淆。

## A：持久接收链

所有权：`ai_operator/capture_journal.py`、`tests_ai_operator/test_capture_journal.py`、`reports/capture_journal.md`。

`capture_to_store(store, source_request, calendar_request, previous_event_id=None) -> dict`：请求严格字段等于第四批capture-source/capture-calendar请求（format可省），先调用capture_snapshot/capture_calendar并用snapshot_for_outcomes对齐，再单次append_event。拒绝只读store；写入前所有校验完成。前驱若提供，load_capture以当前时点读取，确认同stream并严格后接收（>=前驱recorded_available_at；真实系统时钟倒退拒绝）。source/calendar请求不能含received_at/available_at或已造snapshot。

事件固定基础契约：schema_version=1,kind='source.capture',environment='shadow',source='ai_operator.capture_journal',source_id=stream_id,occurred_at=max(snapshot.received_at,calendar.received_at),available_at同occurred_at,payload固定 `{schema_version:1,observation_id,stream_id,previous_event_id,snapshot,calendar_capture}`。observation_id由本次现场采集系统生成uuid4.hex（32位小写hex），调用者不能传入；用于区分相同源字节/相同系统时钟分辨率下的独立观察，不制造新时间。stream_id为规范对象 `{source_id,code,period,price_basis,exchange,calendar_source_id}` SHA256。append返回ID后get_event读取recorded_at，组成receipt。

实施收紧：新接收的min(行情received_at,日历received_at)>=前驱recorded_available_at，避免日历时钟恢复掩盖行情接收时间倒退；event occurred仍为双时间max。既有memory在提交期间独立读取时钟，如该时刻回退，可能保存一个完整事件和outbox后才被receipt拒绝；保持失败关闭，后续load/gate/标签拒绝，不宣称所有失败零写入，不改共享memory。

独立初审修订：原payload仅靠received时间区分，固定同一系统时钟时第二次capture可能复用旧event而伪成功。增加系统observation_id，避免仅事前查存在留下并发竞态；每次成功采集保留独立记录。第五批尚未接入E盘，不需要迁移此前正式运行的source.capture事件；隔离验收修复前的旧库保留作中间证据，最终新库按新契约再验收。

receipt固定 `{schema_version:1,capture_event_id,stream_id,previous_event_id,recorded_available_at,snapshot,calendar_capture,event,production_eligible:false}`；event为store.get_event完整返回（基础8字段+event_id,payload_hash,recorded_at）。

`validate_receipt(receipt) -> dict`：严格JSON/字段、完整事件和payload哈希重算、ID和快照/日历逐项一致、stream重算、recorded_at>=occurred/available且不晚于当前、recorded_available_at=max；使用第四批validator和对齐；深拷贝，不访问DB/源。哈希只能证明完整性，不阻止管理员重造数据库。

`load_capture(store, capture_event_id, as_of) -> dict`：get_event并类型/hash完整重验；不存在/错误kind拒绝，recorded_available_at>as_of拒绝。

`capture_history(store, capture_event_id, as_of, limit=20) -> list`：沿前驱读取，最多100项、检测循环/身份不一致/时间倒退，结果旧到新；limit正整数非bool。只返回所追踪的显式链，不宣称发现整个库的所有来源。

测试先RED再GREEN：真实JSONcapture落库/重开/导出、单事务失败不落半记录、只读/前驱不存在/跨证券与价格口径、未来asof、链/限额、篡改含重算哈希语义、调用方禁填时间。不改memory/DDL。

## B：来源证据对照

所有权：`ai_operator/source_review.py`、`tests_ai_operator/test_source_review.py`、`reports/source_review.md`。

`build_source_review(receipt, reviewer, evidence) -> dict`：先validate_receipt；reviewer非空。evidence严格JSON对象，固定四键supplier_identity/price_basis/revision/official_calendar，每值null或固定 `{path,url}`；url显式https合法URL，官方日历只允许sse.com.cn/szse.cn及其真实子域，交易所需匹配；其余URL仅为声明。源证据按第四批普通本地文件边界读取，单文件<=1MiB，严格UTF8 JSON且重复键/未知字段拒绝。

四类文档固定结构：

```json
{"source_id":"来源声明ID","provider":"供应商名称"}
{"code":"000001.SH","price_basis":"raw","adjustment_reference":null}
{"revision":"修订声明","source_sha256":"对应行情原始字节SHA256"}
{"exchange":"SSE","rows":[{"cal_date":"2026-01-05","is_open":1}]}
```

supplier对source_id、price_basis对code/basis、revision对revision/sourcehash、calendar对全部覆盖day（重复同值合并冲突拒绝，缺日missing，多余日忽略；声明URL域与exchange相符）。价格口径raw要求adjustment_reference=null；qfq/hfq的adjustment_reference为null或非空httpsURL，但即使声明一致也添加`adjustment_factors` missing项（本批不认证复权算法/因子）。所有文档字段格式正确但值不匹配时check=fail，缺文档check=missing，通过check=pass；pass表示声明/参考一致，不代表来源真实性。

输出固定 `{schema_version:1,capture_event_id,stream_id,reviewer,compiled_at,evidence:[{kind,path,url,sha256,size_bytes,raw_utf8}],checks:[{name,status,reason}],status:'needs_evidence'|'mismatch'|'ready_for_human_review',eligible:false,production_eligible:false,review_id}`；checks前四项顺序固定，复权额外项最后。status fail优先mismatch，再missing为needs_evidence，其余ready_for_human_review。compiled_at系统当前且>=receipt.recorded_available_at；review_id哈希除自身全部对象。raw_utf8完整绑定原始字节，可在源文档更改后离线重算对照，不是仅靠调用者自填checks。

`validate_source_review(review, receipt) -> dict`：严格字段、id/receipt绑定、文档SHA/大小/原始JSON、时间/域与证据语义重算checks/status；拒绝篡改后重hash的虚假pass/eligible；不重读源。报告强调凭据文档可能人为重造，尚需人审及真实下载/签名证明。

测试先RED再GREEN：空证据缺口、全匹配仅待人审、价格证券/修订不匹配、官方日历缺日/休市冲突/伪官方子域、日期时区、复权因子额外缺口、原始字节哈希、字段/类型/无穷/重复键、来源重造限制。

## C：联调、调查与CLI

主代理所有权：`ai_operator/review_gate.py`、`ai_operator/cli.py`、`tests_ai_operator/test_review_gate.py`、`tests_ai_operator/test_phase5_cli.py`、acceptance.py、outputs交付文档。另派只读调查代理追踪现有行情采集/复权口径与日历来源，随后作为独立审查，不修改实现文件。

`record_source_review(store,capture_event_id,reviewer,evidence) -> dict`：从库读取真实receipt（now），B构建review，append_event(kind='source.review',source='ai_operator.review_gate',source_id=review_id,occurred_at/available_at=compiled_at,payload=review)，返回review_event_id/capture_event_id/review_id/status/recorded_available_at/eligible:false/production_eligible:false。

`assess_review_gate(store,capture_event_id,review_event_id,as_of) -> dict`：从库加载并重验两事件hash与recorded_at、检查kind/environment/source身份与两者绑定、review记录asof可用；B重新计算结果。返回 `{capture_event_id,review_event_id,status:'blocked_missing_evidence'|'blocked_mismatch'|'blocked_pending_human_review',checks,recorded_available_at,eligible:false,production_eligible:false}`，最后一状态仍不可放行。

`label_journal_decision(store,capture_event_id,pack,decision,evaluated_at,horizons=None) -> dict`：从库读取真实receipt，调用第四批label_captured_decision核验身份/覆盖，再以snapshot_for_outcomes生成四字段bars，available_at抬到receipt.recorded_available_at，source_hash绑定capture_event_id和双方snapshotID，source状态仍unknown；第三批label_decision输出审计标签。不能抬成历史trusted来源。

CLI新增：`capture-journal --db --input`（source_request/calendar_request/可选previous_event_id），`capture-history --db --input`（capture_event_id/as_of/可选limit），`source-review --db --input`（capture_event_id/reviewer/evidence），`review-gate --db --input`（capture_event_id/review_event_id/as_of），`journal-outcome --db --input`（capture_event_id/pack/decision/evaluated_at/可选horizons）。写命令要求已初始化的独立库，先只读预检；请求字段先校验，读取命令使用readonly库。保持原8MiB JSON/退出码/错误输出契约。

验收：outputs专用新建SQLite库，真实300根存量源重复接收形成2条观察链；reopen、history、待证据review、gate拒绝、unknown标签、outbox导出完整性；源SHA前后不变。另工程证据夹具全匹配仍待人审、不进入训练。先独立复审和隔离全量回归，再申请E盘目录写权限接入，目标回归和真实验收复跑。

## 执行台账

- [x] 核对第四批契约、既有event API和数据目录，冻结第五批范围。
- [x] A：持久接收/时间链/完整性先测后实现；26项通过，同时间观察ID碰撞修复。
- [x] B：来源文档/官方日历对照/人审缺口先测后实现；20项通过，全匹配仍待人审。
- [x] C：CLI/审查门槛/持久标签衔接/真实只读调查与验收；2次接收形成链、3个事件完整导出，真实来源缺四类凭据，放行关闭。
- [x] 独立审查、修复及全量回归；新增61项通过，修订后全量317项、313通过/4既有权限跳过，复审无阻断。
- [x] E盘限定目录接入与目标复验：8个源码/测试哈希一致，317项中313通过/4既有权限跳过；真实验收通过，进度和运行说明已更新。
