# 独立范围复审报告

日期：2026-10-07。依据 `plan.md`、`reports/independent-review.md` 与最终 `reports/fix-review.md`。本次只复审原 2 项 P2、1 项 P3，以及修复直接影响的兼容性、只读、副作用和错误退出路径；未扩大审查未修改模块。

## 接入结论

**原 3 项发现均已关闭，本次修复范围内未发现新的接入阻断问题，可以继续按既定计划接入本批独立模块。** 接入后仍应在实际项目目录重跑本批测试。该结论不表示真实云端 ZIP、上游 Schema、算法、推理或交易流程已获验收。

复审只读取实现、测试和报告，运行隔离测试与临时复现；仅新增本报告，未修改实现或 E 盘项目，未联网、推理、部署或提交。

## 原发现处理结果

| 原发现 | 修复检查 | 独立验证与结论 |
|---|---|---|
| P2：外部 SQLite 被误初始化 | `memory.py:276` 的 `_validate_database` 对版本 0 要求无任何 schema 对象；版本 1 必须匹配冻结完整结构。已有文件在 `memory.py:311` 先用 `mode=ro` 预检；正式连接在持久化 WAL PRAGMA 前复验，初始化事务内再次复验。 | business/events 同名不兼容表 × version 0/1 均被拒绝。SQL trace 与 connect 观察证明只建立只读目标连接，未执行修改 journal_mode/user_version 或业务对象的 SQL；主文件和已存在日志内容不变。关闭。 |
| 同一 P2 的遗留 WAL 边界 | 只读预检避免先建立可写连接、拒绝后 close 自动 checkpoint 的副作用。最终测试辅助状态查询也使用 `mode=ro`，没有由测试自身造成 checkpoint。 | 真实子进程写入 WAL 后 `os._exit(0)`，针对 version 0/1、调用方 readonly=False/True 均拒绝。独立比较主文件、`-wal`、`-journal` 存在性与字节，保持不变。关闭。 |
| P2：坏压缩流泄漏异常 | `bundles.py:396` 增加捕获 `zlib.error` 与 `lzma.LZMAError`，保持原有 invalid/source_error 返回约定。 | 重新构造非法 DEFLATE block type、损坏 ZIP LZMA 流正文；两类均 API 返回 invalid dict，实际 CLI 子进程输出 JSON、退出 2、stderr 为空，输入 ZIP 字节不变。关闭。 |
| P3：参数错误不是 JSON | `cli.py:10` 覆盖 ArgumentParser.error；`cli.py:62` 在统一 try 内 parse_args，子命令继承同一解析器。 | 缺 --db、未知命令、limit 为非法字符串/0/-1 均 JSON 退出 2；全量测试另覆盖无命令、未知选项和非法环境。根 --help 与 init --help 仍为文本、退出 0。关闭。 |

## 规格符合性与代码质量

- 修改局限于 memory/bundles/cli 和对应测试，未改变事件、经验、导入、导出公共接口及其时点语义；原有事务/并发/可恢复导出/活跃 WAL 只读测试继续通过。
- 数据库识别使用 SQLite 解析同一份 `_SCHEMA` 生成参考 schema，避免另维护一份容易漂移的字段约定；连接均有异常关闭路径。三处验证分别处理已有库预检、正式连接写 PRAGMA 前检查和初始化事务内检查，职责明确。
- 版本 1 采用完整冻结 DDL 比较，属于保守的封闭格式：额外表/索引/触发器、手工改写 DDL 会被拒绝，不自动迁移或补表。`fix-review.md` 已明确此边界，当前完整工具库读写重开测试通过；这不构成本批规格回归。
- 核查器捕获具体压缩后端异常，未用无差别异常吞噬掩盖所有编程错误。新增 fixture 是真实坏压缩流，并先验证标准库确实抛预期后端异常，测试具有针对性。
- CLI 重用已有 JSON 错误通道；帮助文本仍由 argparse 正常处理，未改变成功命令输出或写入授权方式。
- 新测试检查外部库版本、模式、完整对象清单、主文件及日志字节，覆盖“报错但已改库”的关键副作用；原测试不是仅靠退出码判断修复成功。

## 最终验证

稳定实现上由独立审查者执行：

```powershell
& 'C:/Program Files/Python311/python.exe' -B -m unittest discover -s tests_ai_operator -v
```

实际结果：退出码 **0**，`Ran 90 tests in 10.088s`，`OK (skipped=2)`。其中 memory/contracts 31、legacy 16、bundles 30、CLI 13。两个 skipped 为当前 Windows 缺少符号链接创建权限；目录 junction 和根 junction 检查通过。修复过程中的 89 项结果未用作最终结论。

稳定版额外运行内联复现脚本，使用 Python 3.11、TemporaryDirectory、真实 SQLite/ZIP/CLI 子进程与仅用于观察的 connect/SQL trace。脚本退出 0，输出：

```text
business/v0: rejected in readonly connection; main/WAL/journal unchanged
events/v0: rejected in readonly connection; main/WAL/journal unchanged
crashed_wal/v0: rejected in readonly connection; main/WAL/journal unchanged
business/v1: rejected in readonly connection; main/WAL/journal unchanged
events/v1: rejected in readonly connection; main/WAL/journal unchanged
crashed_wal/v1: rejected in readonly connection; main/WAL/journal unchanged
existing empty file: initializes and reopens readonly
DEFLATE: API dict + CLI JSON2 + unchanged file
LZMA: API dict + CLI JSON2 + unchanged file
arguments: JSON2; root/subcommand help: text0
```

已阅读修复者最终报告并与代码、上述实测核对。修复者报告既有 163 事件验收库仍能只读打开且主文件不变；主代理另有真实源第二轮验收。本次范围复审没有重新读取真实来源或将其他执行者的验收结果记作自己的实测。

## 保留边界

本复审沿用首次报告已接受的边界：SQLite 只读访问可能协调 `-shm` 等附属文件；审批状态不是完整历史版本回放；真实上游 ZIP 与后续算法/交易验收尚未完成。这些边界没有被本次修复改变。最终没有剩余未解决的本轮 P2/P3 发现。
