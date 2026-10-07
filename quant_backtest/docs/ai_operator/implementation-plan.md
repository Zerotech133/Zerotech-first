# 多 AI 第一批并行开发实施计划

目标：交付 M0 的离线发布包核查工具、M1 统一记忆与审计、旧委员会记录导入与 CLI。规格来源：`outputs/2026-10-07-云端语料与本地多AI最优研发方案.md` 与此前闭环设计第 5 节。

本批是后续结构、训练与交易模块的前置工程。M2 算法接入依赖尚未取得的开发包；M3–M6 不在这一批假装完成。

技术：Python 3.11 标准库、SQLite WAL、unittest；不安装依赖，不启动模型，不连接网络，不改变交易流程。

## 全局约束

- 简体中文文档、注释、错误说明；代码标识使用英文。
- 隔离开发目录为本文件所在目录；三个任务不共享写入文件。
- 项目最终只新增 `quant_backtest/ai_operator/`、`tests_ai_operator/`、`docs/ai_operator/`。保留所有已有改动。
- 导入无副作用；所有写入必须由用户显式调用 CLI 或 API。
- 历史资料缺可信 `available_at` 时只供审计；不能根据旧记录的 `ts` 自动推断决策可用时间。
- 使用测试先行，记录失败与通过命令。各代理不创建子代理、不修改别人的文件、不提交 Git。
- 用户已明确要求并行开发，按冻结接口并行实现，替代技能默认串行派发；接口变化由主代理协调。
- 本线程为项目外的文档工作区，采用新文件隔离暂存与审查后复制接入，避免在脏主仓库创建/切换分支。

## 目录与归属

| 任务 | 文件所有权 | 接口 |
|---|---|---|
| A 记忆底座 | `ai_operator/contracts.py`、`memory.py`；`tests_ai_operator/test_memory.py` | 下述 MemoryStore API |
| B 旧数据导入 | `ai_operator/legacy.py`；`tests_ai_operator/test_legacy.py` | `ingest_legacy(store, reports_dir) -> dict` |
| C 包核查 | `ai_operator/bundles.py`；`tests_ai_operator/test_bundles.py` | `inspect_bundle(path) -> dict` |
| 主代理 联调 | `ai_operator/__init__.py`、`__main__.py`、`cli.py`；CLI 测试、说明与验收报告 | 命令调用 A/B/C |

## 冻结公共接口

`contracts.py` 提供 `canonical_json(value)->str`（排序、无 NaN）、`normalize_time(value)->str`（必须有时区，转 UTC ISO）、`utc_now()->str`、`validate_event(event)->dict`。验证并补齐：

```json
{"schema_version":1,"kind":"council.proposal","environment":"shadow","source":"legacy/council_proposals.jsonl","source_id":"stable-key","occurred_at":"2026-10-03T10:38:50+00:00","available_at":null,"payload":{"code":"600663.SH","sid":"9dim"}}
```

允许环境 `shadow/paper/advisory/legacy`；`occurred_at` 必填，`available_at` 可空；`recorded_at` 由库写入。ID 与 payload_hash 由规范内容生成，来源逻辑 ID 相同但内容修订可以共存。事件必须 JSON 对象，payload 必须对象，时间非法抛 ValueError，不接受调用方伪造计算字段。

`memory.py` 提供 `open_store(path, readonly=False)->MemoryStore`；只读模式不初始化缺失库、不迁移、不写业务，允许 SQLite 读取活跃 WAL 所需的协调附属文件。可写模式在任何写入型 PRAGMA 之前识别库，仅初始化真正空库，拒绝无关对象或不兼容的已有库。实例支持 with、close 和：

```python
append_event(event: dict) -> str
get_event(event_id: str) -> dict | None
get_checkpoint(source_key: str) -> dict | None
commit_import(source_key: str, checkpoint: dict, events: list[dict], errors: list[dict], expected_checkpoint: dict | None = None) -> dict
put_lesson(event_id: str, text: str, tags: list[str], status: str = "draft", available_at: str | None = None, expires_at: str | None = None) -> str
recall(query: dict, as_of: str, limit: int = 5) -> list[dict]
snapshot(as_of: str | None = None) -> dict
export_pending(output_dir: str) -> dict
```

`commit_import` 单事务保存 events、错误和 checkpoint。比对 expected_checkpoint 防止两个导入者覆盖进度；结果字段 `inserted/duplicates/errors`。checkpoint 是 JSON 对象，由导入方管理。`put_lesson` 幂等并支持状态修改；有效状态 `draft/reviewed/rejected`。新建或首次审核在未显式给出可信历史 available_at 时默认当前时点，重复 reviewed 保留时间和原 expiry。经验可用时间不得早于事件可用时间；来源事件 available_at 为空时无论传入何值都不可召回。`recall` 必须指定 environment，支持 code、strategy_id 与 tags 过滤，只返回当前 reviewed、双方时点可用且未过期的经验，包含 event_id 和 lesson_id。本批不实现审批状态完整历史回放。`snapshot` 返回事件数、按 kind/environment 计数、经验数、可召回经验数、导入错误数、待导出事件数。快照只读，不启动推理。

`export_pending` 用 outbox 导出完整且可重建的 `events.jsonl`，原子替换后才确认相应事件；重试不重复数据。导出失败保留 pending，导出器并发不得把新快照覆盖成旧快照。数据库提交和文件输出分开恢复。

## 任务 A：记忆与审计

- [x] 先写行为测试并运行，记录缺失功能导致的失败。
- [x] 实现时间/JSON/身份验证与 SQLite 初始化、事件去重、修订共存、WAL、busy timeout。
- [x] 实现同事务导入进度、错误报告、expected_checkpoint 对比。
- [x] 实现经验审核状态、环境隔离、可用时间与过期过滤；不使用未来经验。
- [x] 实现可恢复导出与只读统计。
- [x] 测试并发独立连接、不合法 JSON/时间、重复导入、事务中断、导出失败重试、历史时点召回。
- [x] 写 `reports/task-a.md`，说明红绿测试命令与结果。

测试命令：`& 'C:/Program Files/Python311/python.exe' -m unittest discover -s tests_ai_operator -p test_memory.py -v`。

## 任务 B：旧委员会记录只读导入

- [x] 先写增量/轮转/坏行/半行/时点行为测试并运行。
- [x] 仅处理固定五个文件：council_proposals、council_watch、council_reviews、council_actions、council_sim_proposals 的 JSONL。
- [x] 二进制按完整换行读取；末尾无换行不推进偏移；坏完整行记录 source/offset/type/message，下一次不重复报告。
- [x] 来源键为规范绝对路径；记录 offset、generation、已处理前缀 SHA256；前缀改变或文件缩短识别新一代，重扫但事件幂等。
- [x] source_id 来自原有显式 ID 或内容规范哈希，不能只靠行号；原始 payload 完整保存。sim 为 paper，其余 shadow，来源明确。
- [x] ts/日期按 Asia/Shanghai 转 occurred_at；原文无可信 available_at 则 null。缺任何事件日期的记录作为错误，不编造历史时间。
- [x] 导入复盘中的 lessons 仅保存在 payload，本批不自动创建已审核经验；用户通过 put_lesson 显式审核。
- [x] 缺文件列入 missing，不创建原目录/文件。原文件并发追加只读到当前快照长度；进度与事件同事务提交，发生并发进度冲突明确失败可重试。
- [x] 返回 inserted、duplicates、errors、pending_bytes、files、missing；记录测试与报告 `reports/task-b.md`。

测试命令与 A 相同但模式为 test_legacy.py。A 尚未落盘时可先运行得到依赖缺失的 RED，随后按 API 调试，不改 A。

## 任务 C：离线发布包核查

- [x] 先写 ZIP 安全与一致性测试并运行。
- [x] 支持 ZIP 或已解压目录，只读查看，不解压/执行脚本，不写云端。
- [x] ZIP 拒绝绝对路径、盘符、..、反斜线绕过、重复大小写路径、符号链接；目录拒绝符号链接/重解析目录逃逸。
- [x] 限额：最多 2,000 文件，单文件 8 MiB，累计 64 MiB；实际读取也受限。
- [x] 计算文件 SHA256，发现可选 manifest.json 时核对 files 字典（相对路径→哈希）和 bundle_version/schema_version，未清单化文件报告不一致；无清单状态 unverified。
- [x] 若识别 `rules/rules.json`，读 list 或 {rules:list}，检查 id/rule_id 唯一；未知结构报 unverified，不能假称 Schema 验证通过。可检查 data 下 CSV 中 V011/V012 的课号，只有列名可识别时核查，未知列名返回警告。
- [x] 这是本地离线证据清单，返回 ok/status/errors/warnings/files/stats/source_hash；无论包检查是否通过，production_eligible 始终 false，等待独立 Schema、规则审核和回测门槛。
- [x] 不捏造原 ZIP 的实测结果，报告 `reports/task-c.md`。

## 联调与验收

- [x] 写 __init__/CLI 测试并运行 RED；命令为 init、import-legacy、snapshot、recall、export、inspect-bundle。
- [x] 所有 DB 操作必须显式 --db；snapshot/recall 使用只读连接，不存在 DB 必须错误退出且不创建文件；包检查不需要 DB。
- [x] JSON 输出，失败非零退出；recall 明确 environment 与 as-of。
- [x] 全量运行本批 unittest；独立审查规格与代码质量，修复重要问题。
- [x] 使用隔离 DB 对真实委员会 JSONL 连续导入两次，核对第二次新增为零、源文件哈希不变、经验为零、历史召回为空。
- [x] 文件复制接入授权 E 盘三个新目录；在真实项目目录重跑本批测试；不启动定时任务、模型、推送、训练或交易。
- [x] 写用户交付验收报告及剩余 M0/M2–M6 依赖。

## 前置审查

| 接口/任务 | 核查结果 |
|---|---|
| A→B | commit_import 事务与 checkpoint JSON 已冻结；B 不直接 SQL 写库 |
| A→CLI | 只读 CLI 调用 `open_store(path, readonly=False)` 的 readonly 参数；只读不初始化/迁移/写业务，保留活跃 WAL 可见性 |
| C→CLI | 返回纯 dict，包核查独立于 DB |
| A 内部 | event.available_at 为空时经验不可用；outbox 使用唯一 event_id |
| B 内部 | 无换行半行不消费；缺文件不创建；轮转重扫去重 |
| C 内部 | 未知上游 Schema 显式 unverified；哈希核查不代替生产放行 |
