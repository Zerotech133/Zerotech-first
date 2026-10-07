# 任务 A：统一记忆与审计交付报告

## 完成状态

任务 A 在隔离目录实现并验证。仅编辑归属文件：`ai_operator/contracts.py`、`ai_operator/memory.py`、`tests_ai_operator/test_memory.py`、本报告。未修改原 E 盘项目，未安装依赖、启动模型、联网、推送、交易或提交 Git。

冻结 API 已实现：`canonical_json`、`normalize_time`、`utc_now`、`validate_event`、`open_store(path, readonly=False)` 以及 MemoryStore 的上下文管理、close、append_event、get_event、get_checkpoint、commit_import、put_lesson、recall、snapshot、export_pending。

## 数据与身份约束

- 规范 JSON 使用排序键、紧凑分隔符和 UTF-8 中文；拒绝 NaN、Infinity、非字符串对象键、非 JSON 类型及循环引用。
- 时间必须带时区；规范为 UTC ISO，固定六位微秒，例如 `2026-10-03T10:38:50.000000+00:00`，保证存储文本的时点比较一致。
- 事件 schema_version 默认 1；available_at 默认 null；kind、environment、source、source_id、occurred_at、payload 必填。payload 必须对象。
- 环境限 shadow、paper、advisory、legacy。未知事件字段及 event_id、payload_hash、recorded_at 等调用方计算字段均拒绝。
- event_id 为全部规范事件内容的 SHA256，payload_hash 为规范 payload 的 SHA256。recorded_at 由库生成并排除在内容身份之外。
- 相同内容、等价时区表示及不同 JSON 键顺序得到同一身份；相同 source_id 的内容修订得到独立事件；调用方后续修改输入对象不影响已存事件。
- 导入模块无副作用；只有显式调用 API 才打开或写入数据库。

## SQLite 与事务

- 写连接启用 WAL、foreign_keys，busy_timeout 为 10 秒。
- 事件与唯一 event_id 的 outbox 项在同一 BEGIN IMMEDIATE 事务中写入。
- commit_import 同一事务保存事件、去重错误和 checkpoint；expected_checkpoint 按规范 JSON 与当前进度比较。冲突抛中文 ValueError，全部写入回滚。
- expected_checkpoint 默认 None 只允许首次提交；已有进度的导入必须传入之前读取的 checkpoint。
- 同一来源的相同错误内容只保存一次；返回 inserted、duplicates、errors，errors 为本次实际新增审计错误数。
- 非法事件、错误对象或数据库异常均不能留下部分事件、错误、outbox 或 checkpoint。
- 独立连接并发追加相同事件只落一条；并发首次提交同一来源进度只允许一个成功。
- MemoryStore 单实例遵循 sqlite3 默认线程归属。跨线程使用独立连接；本批不提供同一连接的跨线程共享。

## 经验与时点安全

- 经验身份取 event_id、text、排序去重后的 tags；状态可更新为 draft、reviewed、rejected。
- 新经验未明确传入 available_at 时，默认取当前时刻与来源事件可用时刻的最大值。draft 或 rejected 转 reviewed 时未指定时间，至少取本次审核时刻。
- 相同 reviewed 经验重复调用保持既有可用时间；省略 expires_at 时保留已有过期时间，避免重复审核意外取消过期限制。
- 显式 available_at 必须不早于来源事件 available_at。调用方负责证明显式历史时间可信；系统不会从旧事件 occurred_at 推断经验可用时间。
- 来源事件 available_at 为 null 时，即便显式审核并赋予经验时间，也永远不可召回。
- recall 必须指定 environment 和带时区的 as_of。仅返回 reviewed、事件与经验双方可用时间均不晚于 as_of、且 as_of 严格早于 expires_at 的记录。
- code、strategy_id、tags 可组合过滤；tags 要求全部包含。策略兼容 payload.strategy_id 与旧 payload.sid。
- 返回 lesson_id、event_id、text、tags、status、environment、code、strategy_id、available_at、expires_at。按经验可用时间降序及 lesson_id 升序稳定排序，最后限制返回数量。
- 本批检索的是当前审核集合中在指定时点可用的经验，并不提供审核状态完整历史回放。未来撤销或重新审核会改变该集合；后续升级若要求重现当时审批状态，需要独立审批版本历史。

## snapshot 的准确语义

返回字段固定为 events、events_by_kind、events_by_environment、lessons、recallable_lessons、import_errors、pending_events。

事件、经验、错误、待导出数量及分类均为当前数据库的全量审计统计。as_of 只决定 recallable_lessons 的可用时点；省略时采用当前 UTC 时间。统计在单一只读事务中读取一致快照，不启动模型或推理。

## 只读连接边界

- readonly=True 使用 SQLite URI mode=ro 和 query_only；不存在数据库直接抛错，不创建主库或父目录。
- 不初始化、迁移、不执行 journal_mode 切换、不写业务记录。未初始化或不支持版本的主库明确失败。
- 保留 WAL 正常读取机制，没有使用 immutable；活跃 WAL 中已提交事件必须可见，不能忽略 WAL 返回旧数据。
- 验证显示：只读读取不会修改主库字节，也不会修改已有 WAL 的内容。
- SQLite 在完全关闭写连接且附属文件已消失时，mode=ro 读取 WAL 主库仍可能创建空的 `-wal` 和协调用 `-shm` 文件。这是 SQLite 读协调行为，本 API 不承诺目录完全零字节变化。文件系统禁止协调文件创建时应明确报错，不能切换为忽略 WAL 的读法。

## outbox 导出与恢复

- export_pending 每次输出完整 events.jsonl，包含规范事件、event_id、payload_hash、recorded_at，可重建全部事件内容，不仅输出新增行。
- 导出在 BEGIN IMMEDIATE 写锁内选取一致快照、写同目录临时文件、flush/fsync、os.replace 原子替换，再确认相应 outbox。其他导出器及写入者不能在旧快照导出期间抢先生成更晚快照后再被覆盖。
- 原子替换失败时旧目标文件不变、pending 保留，临时文件清理；重试输出完整快照，不重复追加事件。
- 文件替换成功后，若数据库确认或提交失败，文件可能已经更新，但 pending 仍保留。再次导出同一完整快照即可恢复，数据库确认与文件输出不依赖跨系统原子事务。
- 返回 exported 为文件中的完整事件数，acknowledged 为本次确认的待导出数，pending 为本次提交后的数量，path 为输出绝对路径。
- 此串行化以同一数据库为边界；每个数据库应使用自己的输出目录。不同数据库共用同一 events.jsonl 的所有权冲突不在本批接口内。
- 导出锁持有时间与完整事件量相关。本批优先保证恢复及并发顺序；超大库后续可采用带版本的分块快照与独立导出协议。
- fsync 与原子替换已覆盖进程失败恢复；不宣称跨所有文件系统的物理断电耐久保证。

## 红绿测试记录

工作目录：`C:/Users/Administrator/Documents/Codex/2026-10-07/new-chat-2/work/ai_operator_phase1`。

任务 A 测试命令：

```powershell
& 'C:/Program Files/Python311/python.exe' -m unittest discover -s tests_ai_operator -p test_memory.py -v
```

1. 首次 RED：生产模块尚不存在，ModuleNotFoundError: No module named 'ai_operator'，exit 1。测试文件先于生产实现创建。
2. 首轮实现：22 项功能断言通过，但三个测试清理临时目录时因测试 sqlite3 连接未关闭产生 WinError 32，exit 1。修正测试资源生命周期，使用 contextlib.closing 显式关闭辅助连接。
3. 首次 GREEN：22 tests，0 failures、0 errors，exit 0。
4. 时点回归 RED：新增 4 项测试，总 26 项；新经验默认时间回填历史、首次审核时点与过期时间保留两项失败，exit 1；两项 WAL 只读边界测试通过。
5. 最终任务 A GREEN：26 tests，0 failures、0 errors、0 skipped，exit 0。

覆盖规范 JSON、无时区与非法时间、伪造字段、事件去重、修订共存、输入对象隔离、WAL 与超时、上下文关闭、事务错误回滚、触发器中断、并发独立连接、进度比较冲突、审核幂等、环境与属性隔离、未来时点及过期过滤、审计资料不可召回、默认经验时点、只读缺库/空库/活跃 WAL、完整导出、replace 失败重试、确认失败恢复与并发导出顺序。

全套联调命令：

```powershell
& 'C:/Program Files/Python311/python.exe' -m unittest discover -s tests_ai_operator -v
```

结果：80 tests，0 failures、0 errors，exit 0，2 skipped。跳过来自任务 C 的 test_directory_symlink_is_rejected、test_root_symlink_is_rejected，因为 Windows 缺少创建符号链接的权限；其重解析目录和路径安全的其他测试通过。本报告不把跳过描述为已经实测通过。

## 交接与剩余限制

已通过 B 和 CLI 冻结接口联调。A 没有接触原项目数据或执行旧项目代码；真实委员会 JSONL 只读导入验收、原文件哈希复核及接入复制由主代理按授权执行。

上述 SQLite 只读附属文件、当前审核集合语义、单连接线程归属和导出锁范围是已披露的工程边界；没有未解决的 A 测试失败。
