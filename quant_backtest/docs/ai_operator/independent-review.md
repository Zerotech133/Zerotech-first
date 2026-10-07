# 第一批并行开发独立代码审查

审查日期：2026-10-07。审查依据为 `plan.md`、`review-brief.md` 与 README 已声明的边界。审查对象为 A 的 contracts/memory、B 的 legacy、C 的 bundles、主代理 CLI/入口及对应测试。未修改实现，未访问网络、运行推理、部署、提交或修改 E 盘项目。

## 结论

暂不建议接入，先修复下面两个 P2 问题。现有测试覆盖主要事务、去重、时点、只读、轮转、导出恢复和文件安全路径，整体结构简洁，模块边界符合冻结接口。发现的问题均有本机 Python 3.11 可复现结果；未发现需要扩大到未实现交易模块的理由。CLI 参数解析输出另有一项 P3 规格差异。

## 必须修复的问题

### P2：将其他应用的 SQLite 数据库当作未初始化记忆库修改

位置：`ai_operator/memory.py:291`（读取 user_version）至 `:300`（写 journal_mode）及后续初始化事务。`open_store` 只用 user_version 是否属于 0/1 判断兼容性；SQLite 常见的外部数据库默认 user_version 就是 0。向一个已有业务表的外部数据库调用该 API，会成功添加本工具的五张表、切换 WAL，并把外部库的 user_version 改成 1。CLI `init`、`import-legacy`、`export` 均能进入这个分支。显式指定数据库路径并不能证明该文件是本工具拥有的数据库，路径误选会修改其他应用的持久化元数据，可能破坏其迁移和连接约定。

复现：用 sqlite3 创建 `foreign.db`，执行 `CREATE TABLE business(id INTEGER)` 并关闭连接；记录文件字节；调用 `with open_store(path): pass`；重新查询 user_version、sqlite_master。实际结果为 `version=1 changed=True`，表清单新增 events/checkpoints/import_errors/lessons/outbox，没有拒绝输入。

建议：在任何写入型 PRAGMA 或初始化之前识别库所有权/完整受支持结构；仅允许真正空库初始化，对已有无关对象、错误版本或不兼容结构明确拒绝。验证应覆盖外部 version 0、外部 version 1、同名不兼容表，并断言主文件字节/原有版本/表及 journal_mode 保持不变。version 1 也不能单独作为所有权依据。

### P2：损坏的压缩数据令包核查 API 和 CLI 直接抛出 zlib.error

位置：`ai_operator/bundles.py:370` 的压缩成员读取，以及 `:393` 的异常捕获列表；CLI 的通用捕获也不包含该异常。

`zipfile` 解析到结构完整、压缩流损坏的 DEFLATE 成员时可能抛出 `zlib.error`，它不是当前列表中任何受捕获异常的子类。现有 CRC 损坏测试触发的是 BadZipFile，未覆盖此路径。真实损坏 ZIP 是核查器正常应处理的输入，不能破坏 `inspect_bundle -> dict` 契约。

复现步骤：

```python
with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
    archive.writestr("x.txt", b"hello world " * 100)
data = bytearray(path.read_bytes())
offset = 30 + struct.unpack_from("<H", data, 26)[0] + struct.unpack_from("<H", data, 28)[0]
data[offset] = 7  # 非法 DEFLATE block type
path.write_bytes(data)
inspect_bundle(path)
```

实际结果：API 抛出 `zlib.error: Error -3 while decompressing data: invalid block type`；子进程 `python -B -m ai_operator inspect-bundle --path ...` 的退出码为 1，stdout 为空，stderr 含 traceback。期望为 API 返回 `ok=false/status=invalid` 及错误数组，CLI 返回 JSON 并退出 2。

建议：在核查器边界捕获受支持压缩后端的具体损坏异常，至少 zlib.error，并检查 LZMAError 等其他后端；补充真实压缩流损坏的 API 与 CLI 回归测试，而非只 mock 异常或破坏 CRC。

## 低优先级规格差异

### P3：参数解析失败仍输出 argparse 文本而非 JSON

位置：`ai_operator/cli.py:56`。parse_args 位于 try 外，缺失 --db、非法选项或错误参数类型直接走 argparse 的 SystemExit。实际运行 `python -B -m ai_operator init`：退出 2、stdout 为空、stderr 为 usage 文本。plan.md 的“JSON 输出，失败非零退出”未明确排除使用错误；README 也未说明此例外。建议统一参数错误为结构化 JSON，保留 --help 的文本行为；或明确限定并记录 CLI 输出契约。现有测试只检查退出码，未检查参数错误 JSON。

## 分模块符合性与质量

| 模块 | 结论与证据 |
|---|---|
| A contracts/memory | 事件规范化、去重与修订共存、计算字段防伪、WAL/busy timeout、导入 CAS 与事务回滚、经验环境/双方 available_at/expiry 过滤均符合规格。无可信事件时间的经验不可召回；默认首次审核用当前时间，重复审核保持时间。导出在 DB 写锁内生成全量快照，原子替换后确认，失败重试和独立连接并发测试通过。须修复外部库识别。 |
| B legacy | 固定五文件、sim=paper、其他=shadow、日期转换、字节偏移、完整坏行审计、半行保留、前缀哈希和代次、source_key Windows normcase、只读源与同事务 checkpoint 均符合本批要求。未自动生成经验。另用读取中同长度改写复现验证，正确抛 RuntimeError，未发现进度提交。 |
| C bundles | ZIP/目录只读、危险路径/重解析点/特殊文件/重复名拒绝、元数据及实际读取限额、manifest 哈希与未知结构降级、规则 ID 和 CSV 核查符合当前本地约定。source_hash 明确为内容清单指纹，schema_verified/production_eligible 永远 false。须修复压缩后端异常遗漏。 |
| 主代理 CLI/入口 | 六个子命令、显式 DB、只读 snapshot/recall、缺库不建库、坏行非零退出、来源变化 RuntimeError 结构化处理已具备。入口导入不启动业务。参数错误 JSON 为上述 P3 差异。 |

## 验证记录

在隔离暂存目录执行：

```powershell
& 'C:/Program Files/Python311/python.exe' -B -m unittest discover -s tests_ai_operator -v
```

结果：退出码 0，`Ran 81 tests in 7.128s`，`OK (skipped=2)`。两个 skipped 均为 Windows 符号链接创建权限不足；目录 junction/根 junction 测试通过。测试数量：A 26、B 16、C 28、CLI 11。

额外临时样例的复现脚本最终退出码 0，打印：

```text
foreign_database: version=1 changed=True
corrupt_deflate_cli: exit=1 stdout='' traceback=True
missing_db_cli: exit=2 stdout='' json_error=False
```

现有红绿开发记录在任务报告中；本次独立审查只重新运行最终测试，不将历史记录当作亲自执行的证据。

## 剩余限制与建议测试

- 缺失真实云端 ZIP，不能推导上游 Schema、规则语义、算法或生产资格已通过；合成包测试只验证工具自身。
- 审核状态是当前状态过滤，不提供完整审批历史回放；README 已明确，这是本批接受边界。
- 只读 WAL 允许 SQLite 协调附属文件，测试证明读到活跃 WAL，不能把“只读”描述成目录中绝无任何附属文件变化。
- 来源导入是逐文件事务，后续来源失败前已提交的其他来源不回滚；API 文档已有此说明，重试依靠 checkpoint 幂等恢复。
- 导出同步 fsync 文件与原子替换、DB 确认覆盖进程故障恢复；未以断电故障注入证明所有文件系统的持久化行为，不应把当前测试扩张成此保证。
- 待修复后应新增上述外部库、压缩流损坏回归，并重跑全量测试及本报告的两个复现，再作接入判断。
