# 任务 B：委员会旧 JSONL 只读增量导入

状态：实现完成，任务 B 的 16 项行为测试通过；已按冻结 MemoryStore API 联调。未修改 E 盘、其他代理源码或交易流程。

## 交付

- `ai_operator/legacy.py`：`ingest_legacy(store, reports_dir) -> dict`。
- `tests_ai_operator/test_legacy.py`：16 项标准库 unittest 行为测试。
- 本报告。

## 行为与公共接口

固定读取五个文件：`council_proposals.jsonl`、`council_watch.jsonl`、`council_reviews.jsonl`、`council_actions.jsonl`、`council_sim_proposals.jsonl`。kind 对应 `council.proposal/watch/review/action/sim_proposal`；sim 环境为 `paper`，其余为 `shadow`。source 为 `legacy/<原文件名>`。

来源进度键使用 `os.path.normcase(str(path.resolve()))`，即规范绝对路径；Windows 大小写别名不会产生独立进度。checkpoint 保存 `offset`、`generation`、`prefix_sha256` 和 `file_identity`。初始 generation 为 0；文件缩短、已消费前缀改写或文件身份改变时 generation 加一，从头重扫，事件仍通过 MemoryStore 的规范内容去重。

以二进制打开源文件，捕获当前文件长度，只处理这个快照中的完整换行行。末尾半行留给下一次，不推进 offset；读取期间追加的内容留给下一次。UTF-8 字节错误、JSON 错误、非对象、非法日期和非法事件等完整坏行保存 source/offset/generation/type/message，并随 checkpoint 一起提交，下一次正常增量不重复报告。消费前缀在提交前再核对哈希；读取期间缩短或改写明确失败，可重试。

source_id 优先使用 `event_id/id/proposal_id/review_id/action_id/watch_id` 中非空字符串或整数；否则使用原 payload 的规范 JSON SHA256。`sid` 保持原策略标识，不用于事件 ID。payload 完整保留，格式不同但内容相同的 JSON 幂等；同一显式 ID 内容修订可以共存。

occurred_at 按 `ts/occurred_at/d/date` 顺序获取第一个非空时间；无时区日期按 Asia/Shanghai 解释，再经公共 `normalize_time` 转 UTC。无时间或选中时间非法的记录报错，不编造时间。只有原记录显式的 `available_at` 字段送公共时间验证，须自带时区；缺省始终为 null，绝不从 ts 或 d 推断。复盘 lessons 保留在 payload，本导入器不调用 `put_lesson`，不自动产生或审核经验。

每个已存在文件独立调用一次 `commit_import`，同时提交事件、错误和 checkpoint，并传入完整 `expected_checkpoint`。数据库失败或进度竞争向调用方抛出错误；其他已完成文件的事务不回滚，下次调用幂等恢复。目录不存在抛 `NotADirectoryError`，不创建来源目录；缺少的固定文件列入 missing，不创建文件。

返回字段为 `inserted/duplicates/errors/pending_bytes/files/missing`；files 为已存在文件的明细列表，含 source、source_key、offset、generation、三类计数与 pending_bytes；missing 为缺失文件规范绝对路径列表。

## 测试先行记录

执行目录：`C:/Users/Administrator/Documents/Codex/2026-10-07/new-chat-2/work/ai_operator_phase1`。

```powershell
& 'C:/Program Files/Python311/python.exe' -m unittest discover -s tests_ai_operator -p test_legacy.py -v
```

1. RED：先写 10 项行为测试，执行退出 1，`ModuleNotFoundError: No module named 'ai_operator.legacy'`。这是方案允许的模块缺失 RED，非功能通过。
2. GREEN：实现后执行同命令，10 项通过，退出 0。
3. 新增 Windows 来源键规范测试，执行 16 项，出现 1 failure + 5 error：大小写未规范导致审计来源不符、checkpoint 取不到。
4. 修正 `source_key` 使用 `os.path.normcase`，再执行 16 项，全部通过，退出 0。此轮测试耗时 0.622 秒。

16 项覆盖：原文 payload 与源哈希不变、重复导入、半行与补换行、坏行仅报告一次、中文字节偏移与错误详情、缩短轮转、同长度前缀改写、相同内容文件替换、五个来源及环境、日期回退与带时区时间、缺日期/坏日期/无时区 available_at、显式 ID 修订、规范内容身份、缺目录无副作用、显式审核历史经验仍不可召回、事务失败全部回滚、双连接 checkpoint 竞争、读取期间与提交期间追加。

全量命令：

```powershell
& 'C:/Program Files/Python311/python.exe' -m unittest discover -s tests_ai_operator -v
```

本轮运行 74 项，任务 B/A/CLI 通过；退出 1，包核查任务有 3 failure、2 skip（其他代理仍在并行修改，此处只记录本轮事实）：

- `test_invalid_manifest_fields_and_duplicate_json_keys`：`schema_version=1e309` 对非有限数未拒绝。
- `test_ragged_csv_rows_are_errors`：两个子用例，CSV 多列和少列尚未报错。
- `test_directory_symlink_is_rejected`、`test_root_symlink_is_rejected`：Windows 无创建符号链接权限而跳过；junction 测试通过。

已通知主代理，不越权修改任务 C 的文件。真实 E 盘 JSONL 连续导入验收由主代理统一执行；本任务只读抽查了现有文件的记录结构，未声称完成该统一验收。

## 限制与疑虑

Windows 标准库可能没有 IANA tzdata。优先使用 `ZoneInfo('Asia/Shanghai')`；不可用时，对 1992 年及以后的无时区日期使用上海现代固定 UTC+8，1992 年前无时区日期报错，避免把历史夏令时转换错误。明确带时区的历史日期仍可导入。未联网或安装 tzdata。

原文显式 `available_at` 被视为来源自行声明的可用时间；本批不评估上游声明的业务可信度。真实旧委员会记录抽查未见该字段，保持 null；源系统将来提供该字段时仍需人工确认其语义。

前缀哈希校验需重新读取已消费前缀，单文件批量事件也暂存在内存中。当前固定委员会日志规模很小；大规模档案可后续在不改变公共事务语义的前提下优化。外部无协作原地写入无法提供严格文件系统事务快照；本实现验证消费前缀并在下一次导入识别轮转或改写。
