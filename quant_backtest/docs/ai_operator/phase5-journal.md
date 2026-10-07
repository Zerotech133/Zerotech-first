# 第五批 A：持久接收链验收记录

工作目录：`C:/Users/Administrator/Documents/Codex/2026-10-07/new-chat-2/work/ai_operator_phase5`。

所有实现和测试仅涉及 `ai_operator/capture_journal.py`、`tests_ai_operator/test_capture_journal.py` 和本报告。未修改 MemoryStore、DDL、CLI，未提交，未写 E 盘，未联网；每个测试均建立独立 TemporaryDirectory 和新 SQLite 审计库。

## 冻结接口与行为

- `capture_to_store(store, source_request, calendar_request, previous_event_id=None)` 严格接收第四批当前采集请求；format 可省。禁止 received_at、available_at、snapshot、calendar_capture、observation_id 等回填字段。先检查可写，再现场调用 capture_snapshot/capture_calendar，双快照验证和 snapshot_for_outcomes 对齐完成后只调用一次 append_event。
- 行情和日历作为同一个 source.capture/shadow 事件 payload 保存，source 固定 ai_operator.capture_journal。沿用 MemoryStore 单事务写事件与 outbox，未建立新表。
- 独立审查 P2 修订：payload 固定字段新增 observation_id，仅由本次调用 uuid.uuid4().hex 生成，必须为 32 位小写十六进制。它绑定事件和 payload 哈希，不改变 receipt 顶层或 stream_id。即使两次现场读取的源内容和系统时间完全相同，也各自保存一个新观察事件；这是跨调用非幂等观察，不是历史可用性或来源认证标识。
- stream_id 绑定 source_id/code/period/price_basis/exchange/calendar_source_id；revision 改变允许继续同观察链。前驱不存在或身份不一致在写前拒绝。
- occurred_at/available_at 为 max(双received_at)，recorded_available_at 为 max(occurred_at,recorded_at)。已与主代理确认，前驱约束收紧为 min(双received_at)>=前驱recorded_available_at，防止后读日历恢复的时钟掩盖先读行情时钟倒退。
- `validate_receipt(receipt)` 不访问数据库或源文件，重算完整事件 ID 和 payload_hash、双快照 ID 与语义、stream、固定身份、recorded_at 范围、回执逐项一致性，返回深拷贝。逐项一致使用 canonical_json，拒绝 Python True==1 导致的类型替换。
- `load_capture(store, capture_event_id, as_of)` 重验 get_event 返回的完整事件；按持久可用时点而不是快照接收时间判断 as_of。未来 as_of 可查询当前已持久存在的记录，不生成未来证据。
- `capture_history(store, capture_event_id, as_of, limit=20)` 沿显式前驱链读取最近 1..100 项，旧到新；检查读取范围内循环、来源错配、缺失前驱和时间倒退。limit 为正整数且非 bool。不声称发现库内所有来源或验证限额以外的前驱。
- 所有 receipt.production_eligible=false，底层 supplier_verification=unverified，来源历史可用性保持 unknown。

## 测试记录

RED 命令：

```powershell
python -m unittest discover -s tests_ai_operator -p test_capture_journal.py -v
```

首轮 13 个测试，模块尚未实现，因 ModuleNotFoundError 产生 30 个错误（含子测试），退出码 1。实现后相同命令 13 个测试通过，退出码 0。

第二轮新增类型替换、时钟和重算哈希链测试，共 18 项；外层 snapshot/calendar_capture.schema_version=True 替换因 Python 字典相等而误通过，产生 2 个失败，退出码 1。改为 canonical_json 比较后，同命令 18 项通过。

首次冻结同命令 22 项通过，退出码 0。覆盖现场真实 JSON 采集、重开库、完整 outbox 导出、只读连接与并行可见性、单事务失败回滚、日历解析和休市对齐失败零写入、禁填时间/未知字段、前驱身份/修订/缺失、as_of 持久边界、限额、重算全部哈希后非法 OHLC 拒绝、管理员重造合法回执的认证限制、源文件删除后的离线读取。

独立审查 P2 定向 RED：26 项，3 个失败，退出码 1。固定 contracts.utc_now 和 memory.utc_now 为同一时点，两次无前驱采集只留下 1 个事件，复现内容寻址去重造成的第二次伪成功；另外缺失 nonce 和未生成 nonce 断言失败。加入系统 observation_id 及严格校验后，新测试通过。首轮回跑暴露原 durable 测试依赖真实时钟严格递增而偶发 received==recorded，修正为可控两阶段时钟：现场真实采集在 T 接收，memory 在 T+1 秒录入，稳定核验落库前读取拒绝。

最终同命令 26 项全部通过，退出码 0。新增覆盖相同内容/时间的两个独立观察各写 1 个事件及 outbox、等时点显式前驱链允许、重算哈希后 nonce 缺失/类型/长度/大小写/非十六进制拒绝，和行情及日历请求禁止调用者指定 observation_id。已有手工事件夹具复制真实 capture 的 payload，自然包含新字段，无需改其他模块。

前四批回归命令：

```powershell
python -m unittest tests_ai_operator.test_bundles tests_ai_operator.test_cli tests_ai_operator.test_dataset tests_ai_operator.test_legacy tests_ai_operator.test_memory tests_ai_operator.test_outcomes tests_ai_operator.test_phase2_cli tests_ai_operator.test_phase3_cli tests_ai_operator.test_phase4_cli tests_ai_operator.test_provenance tests_ai_operator.test_replay tests_ai_operator.test_shadow tests_ai_operator.test_source_inputs tests_ai_operator.test_trading_calendar
```

结果：256 项，OK，4 项既有跳过，退出码 0。此记录不对同时开发中的 B/C 模块作全量完成声明，第五批最终全量回归由主代理执行。

## 明确限制

所有输入、日历对齐和前驱错误均在 append_event 前拒绝；事务内部失败由既有 MemoryStore 回滚事件和 outbox。若 append_event 内独立 utc_now 恰逢系统时钟回退，则事务可能已提交一条完整双快照事件及 outbox，之后 receipt 验证拒绝返回。定向测试证实这种记录的 load_capture 和 validate_receipt 都拒绝。未修改 memory 接口，不能声称每种失败都零写入，也不移除已存在的完整审计记录。

哈希证明内容完整性和可重算语义，不能认证供应商、官方日历下载、接收时间来源，不能阻止有权限的管理员重造整个合法事件/数据库。测试显式重造合法 revision 后重算全部哈希，validate_receipt 接受其完整性，但生产资格仍为 false，供应商仍为 unverified。正式来源审核、真实下载/签名证明和人审不属于 A 模块。

## 冻结 SHA256

```text
ai_operator/capture_journal.py
098b163cdcccb994781e7de8cd989800375cc81bbee4fa0c3c051712c08f1873

tests_ai_operator/test_capture_journal.py
8acccfc2dd5f673958f55bbec2add148c7a0ea73b5f79bb9997bd613e180a42a
```
