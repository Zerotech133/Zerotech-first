# 多 AI 记忆与资料核查底座 v0.1

本批提供统一事件记忆、时点经验检索、旧委员会 JSONL 增量导入、可恢复 JSONL 导出与离线资料包核查。使用 Python 3.11 标准库，无需安装新依赖。

只新增独立模块和测试。尚未连接产线调度、模型推理、训练、推送、模拟执行或证券网关。

## 运行入口

在 `E:\老田的AI助手\quant_backtest` 中执行。所有数据库路径均由调用方显式指定。下面是用户后续主动运行的示例，不表示研发期间已在生产数据目录运行。

```powershell
& 'C:\Program Files\Python311\python.exe' -B -m ai_operator --help

New-Item -ItemType Directory -Path '.\data\ai_operator' -Force

& 'C:\Program Files\Python311\python.exe' -B -m ai_operator init --db '.\data\ai_operator\memory.sqlite3'

& 'C:\Program Files\Python311\python.exe' -B -m ai_operator import-legacy --db '.\data\ai_operator\memory.sqlite3' --reports-dir '.\reports'

& 'C:\Program Files\Python311\python.exe' -B -m ai_operator snapshot --db '.\data\ai_operator\memory.sqlite3'

& 'C:\Program Files\Python311\python.exe' -B -m ai_operator recall --db '.\data\ai_operator\memory.sqlite3' --environment shadow --as-of '2026-10-07T16:00:00+08:00' --code '600663.SH'

& 'C:\Program Files\Python311\python.exe' -B -m ai_operator export --db '.\data\ai_operator\memory.sqlite3' --output-dir '.\reports\ai_operator'

& 'C:\Program Files\Python311\python.exe' -B -m ai_operator inspect-bundle --path 'D:\downloads\beidou_quant_kit_v1.1.zip'
```

`init` 不会创建缺失的上级目录，第一次调用前由用户明确创建数据目录。`import-legacy` 同样要求数据库父目录存在。模块导入和 `--help` 不会创建业务文件、访问网络或启动推理。

`snapshot` / `recall` 使用 SQLite `mode=ro` 与 `query_only`，缺失或未初始化的数据库会报错，不创建空库、不迁移、不写业务。WAL 模式的只读访问可能由 SQLite 创建协调用 `-wal` / `-shm` 附属文件；它读取活跃 WAL 中的有效提交，不使用可能忽略 WAL 的 immutable 模式。

退出码：成功为 0；输入、IO、数据库或核查失败为 2；包检查没有硬错误但证据不足为 3。导入发现坏行时有效记录仍提交，坏行写入审计，CLI 退出 2，不能把部分成功误报为全成功。缺失的旧来源文件在 `missing` 中报告，不创建原文件。

参数使用错误也返回 JSON 和退出码 2；`--help` 保持正常帮助文本和退出码 0。初始化前先以只读方式检查已有库，再校验完整冻结表结构；外部库、同名但不兼容的表、额外对象会被拒绝，不自动迁移。后续增加表或修改约束须显式升级数据库版本，不能绕过这项检查。

## 记忆与时点约束

SQLite 为唯一写入真源。事件 ID 根据规范化事件内容生成；重跑同内容不重复，修订内容保留为新版本。来源文件 offset 与事件、错误在同一事务提交；并发导入通过 expected checkpoint 对比阻止覆盖进度。

旧文件固定五类：`council_proposals.jsonl`、`council_watch.jsonl`、`council_reviews.jsonl`、`council_actions.jsonl`、`council_sim_proposals.jsonl`。sim 来源标为 `paper`，其他标 `shadow`；这是旧流程的研究环境标记，不证明成交。

旧 `ts` 是事件发生时间，不能直接证明历史决策可用。没有显式可信 `available_at` 的记录只用于审计，不能借新增经验变成可用的历史记忆。原始复盘中的 lessons 只保存于 payload，不自动审核。代码显式调用 `put_lesson` 才创建经验，默认状态为 draft。

经验只有 reviewed、环境匹配、事件与经验双方可用时间不晚于 `as_of`、未过期时才可召回。新经验与首次审核在未显式指定历史可用时间时使用当前时点；显式历史时间仅供有可信来源的迁移数据。审核状态采用当前集合过滤，本版本未提供审批状态变更的完整历史回放；如需严格回放“当时哪些规则获批”，后续需增加审批版本事件。

`snapshot --as-of` 的时点仅用于 `recallable_lessons` 计数；事件总数等审计统计仍是当前全量，不是历史数据库快照。

JSONL 末尾无换行视为尚未写完，保留 pending_bytes 并在后续调用继续读取。完整坏行只报告一次；来源轮转、缩短或前缀改写会重扫，事件继续按内容去重。读取中来源改变会明确失败并可重试。

## 导出与故障恢复

事件提交时同时登记 outbox。`export` 在数据库写锁内生成完整 `events.jsonl` 暂存快照，flush/fsync 后原子替换，最后确认 outbox。文件替换失败时旧文件和 pending 保留；替换成功但数据库确认失败可重复导出，不产生重复行。为了保证并发导出顺序，导出期间短暂阻塞写入；大规模数据后应以实测决定是否升级。

JSONL 是可重建的事件视图，不是包含经验、checkpoint、错误表的完整备份。完整备份使用 SQLite backup API；不得仅复制一个运行中的主 DB 文件。

## 发布包核查

支持 ZIP 和已解压目录，仅读取，不解压或执行脚本。拒绝路径穿越、重复路径、链接/重解析路径、特殊文件；限制文件数量与读取大小。每文件产生 SHA256；`source_hash` 是排序内容清单的指纹，`source_hash_kind` 说明其口径，不是 ZIP 原始容器的 SHA256。

可选本地 `manifest.json` 格式：

```json
{
  "bundle_version": "1.1",
  "schema_version": 1,
  "files": {
    "rules/rules.json": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
  }
}
```

以上 SHA256 对应文件内容恰为 `[]` 的示例；实际包需按其真实文件重新计算。该清单是本地完整性核查约定，不修改云端上游 Schema。清单不包含自身；其他文件都须列入。没有清单或不识别结构返回 unverified。`verified` 仅表示本工具支持的完整性检查完成，`schema_verified=false` 与 `production_eligible=false` 始终保留；真实 Schema、知识审核、evidence 引用及回测另行验收。

工具可识别规则 ID 重复与部分 CSV 登记字段，但未知列名或格式明确返回警告。尚未取得真实云端 ZIP，不能根据合成测试声称开发包已通过验证。

## 测试与下一批

```powershell
& 'C:\Program Files\Python311\python.exe' -B -m unittest discover -s tests_ai_operator -v
```

测试全部使用独立临时 DB、JSONL、ZIP/目录，包含事务中断、并发、时点、只读和文件失败场景，不写旧账本。真实数据验收另在项目外隔离 DB 进行。

下一批依赖真实两个 ZIP 的核查结果：完成上游 Schema 与规则引用检查、结构算法逐根回放、笔/线段中枢与包含处理四组对照，之后接入快速模型、教师、模拟账户和调度。本批没有以骨架代替这些验收。
