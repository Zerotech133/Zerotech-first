# 第一批独立审查修复记录

日期：2026-10-07。依据 `reports/independent-review.md` 的两个 P2 和一个 P3，以及 `plan.md` 的冻结接口与全局约束。本轮三个问题均已修复并完成回归验证。

## 修改范围

仅修改 `ai_operator/memory.py`、`ai_operator/bundles.py`、`ai_operator/cli.py`、对应三个测试文件，并新增本报告。未修改其他模块、E 盘项目或原验收库；未联网、安装依赖、启动推理、部署或提交 Git。使用 `apply_patch` 编辑。

## 修复与证据

### P2：外部 SQLite 数据库不得初始化或修改

- `user_version=0` 只接受 `sqlite_master` 没有任何对象的空库；非空库明确抛 `ValueError`。
- `user_version=1` 必须与本工具冻结 `_SCHEMA` 经 SQLite 规范化后的完整结构一致。核对对象类型、名称、所属表和完整 DDL，覆盖字段、主键、外键、CHECK 约束、显式索引与隐式索引，拒绝缺失、不兼容和额外对象。不会仅凭版本号接收，也不会写 `application_id` 或迁移外部库。
- 对已有数据库先以 `mode=ro` 预检；随后在正式连接写 `journal_mode=WAL` 前复验，并在初始化写事务内再次验证。只有空库执行初始化，完整版本 1 库无需补表或重写版本。
- 回归覆盖外部版本 0/1、同名不兼容表、缺表、缺索引、改字段、额外业务表；拒绝后比对主文件字节、`user_version`、`journal_mode` 和完整对象清单，并检查 WAL/journal 不新增或原有字节不变。辅助断言自身也只用 `mode=ro` 查询。
- 补查发现可写连接关闭时会对遗留 WAL 自动检查点。新增真实子进程提交后 `os._exit(0)` 的版本 0/1 外部库回归，证明单纯在可写连接中先验证仍会改变主文件；只读预检修复该边界，现有 WAL 字节亦保持不变。
- 完整同工具数据库的读写重开测试通过。旧 `acceptance-run-1/memory.sqlite3` 在最终实现上以只读连接成功打开，快照仍为 163 条事件、0 条经验、0 条导入错误、0 个待导出事件，主文件字节不变。

### P2：真实压缩流损坏返回结构化核查结果

- `inspect_bundle` 边界增加捕获 `zlib.error` 与 `lzma.LZMAError`，返回纯 `dict`，`ok=false`、`status=invalid`、错误码 `source_error`。
- 真实 DEFLATE fixture 把首个压缩块改为非法块类型；真实 ZIP LZMA fixture 损坏压缩流正文。测试先确认 Python 标准库分别抛对应后端异常，随后验证核查器返回 JSON 可序列化的错误结果。
- CLI 使用真实坏 DEFLATE 文件运行子进程，断言退出码 2、stdout 为 invalid JSON、stderr 为空且来源字节不变。不是 mock 或单纯 CRC 错误。

### P3：argparse 错误统一 JSON

- 自定义 `ArgumentParser.error` 抛带中文说明的 `ValueError`；参数解析移入现有统一错误处理，输出 `ok=false/error` JSON 并返回 2。
- 子命令自动继承同一个解析器。缺 `--db`、无命令、未知命令、未知选项、非法环境和非法整数均有真实 CLI 子进程回归，stderr 为空且无文件系统业务写入。
- `--help` 保留正常文本输出，UTF-8 中文可读，退出码 0，无文件系统副作用。

## RED / GREEN 记录

先新增回归，再运行旧实现：

```powershell
& 'C:/Program Files/Python311/python.exe' -B -m unittest discover -s tests_ai_operator -p test_memory.py -v
& 'C:/Program Files/Python311/python.exe' -B -m unittest discover -s tests_ai_operator -p test_bundles.py -v
& 'C:/Program Files/Python311/python.exe' -B -m unittest discover -s tests_ai_operator -p test_cli.py -v
```

- memory：30 项，退出 1，12 个失败和 2 个错误子用例；外部库接受/被补表，或在修改 WAL 后才报 SQLite 结构错误。
- bundles：30 项，退出 1，2 个错误，2 项跳过；实际抛 `zlib.error` 和 `LZMAError`。
- CLI：13 项，退出 1，1 个失败和 6 个错误子用例；参数错误 stdout 为空，坏 DEFLATE 退出 1 且含 traceback。

首轮修复后全量 89 项通过。随后新增遗留 WAL 边界回归并先运行 RED：

```powershell
& 'C:/Program Files/Python311/python.exe' -B -m unittest discover -s tests_ai_operator -p test_memory.py -k foreign_uncheckpointed -v
```

结果：退出 1，1 个测试的 4 个子用例失败，主库字节被自动检查点改变。增加已有库只读预检后运行最终完整套件：

```powershell
& 'C:/Program Files/Python311/python.exe' -B -m unittest discover -s tests_ai_operator -v
```

最终结果：退出 0，`Ran 90 tests in 9.621s`，`OK (skipped=2)`。模块数量：memory/contracts 31、legacy 16、bundles 30、CLI 13。原有测试持续通过。

两项 skipped 仍为当前 Windows 账户缺少创建符号链接权限；junction 与根 junction 测试通过。隔离目录没有 Git 仓库，`git diff` 无法执行；本轮不创建仓库或提交。

## 边界与剩余疑虑

- 版本 1 识别采用冻结 DDL 的完整清单，手工改写结构、额外添加表/触发器或生成不同 DDL 的数据库会被明确拒绝；不会尝试修复或迁移。当前工具既有完整库已验证兼容。
- SQLite 只读 WAL 连接仍可能协调 `-shm` 等附属文件；本回归保证主数据库、业务结构、版本、日志模式和既有 WAL/journal 内容不被核查改写，不扩张为目录中绝无任何附属协调变化。
- 未取得真实云端发布包；合成坏压缩流回归只证明本地错误处理，不证明上游 Schema、规则语义或生产资格。
- 本轮未发现其他未解决的实现问题；仍需主代理独立复审后决定接入。
