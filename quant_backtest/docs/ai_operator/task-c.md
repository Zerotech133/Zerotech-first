# 任务 C：离线发布包核查报告

## 范围与状态

已实现 `ai_operator/bundles.py` 的 `inspect_bundle(path) -> dict` 及 `tests_ai_operator/test_bundles.py`。仅编辑这两个文件与本报告，不修改原 E 盘、不安装依赖、不联网、不执行包内内容、不解压、不提交 Git。

实际验证来源全部为 `TemporaryDirectory` 内制作的安全 fixture，没有取得或核查真实开发 ZIP。不能据此声称原发布包验证通过，M0 的真实包验收仍未完成。

## 返回约定

- `ok`：未发现安全、读取或一致性硬错误；`unverified` 的 `ok` 仍为 `true`。
- `status=invalid`：发现错误；CLI 对应退出码 2。
- `status=unverified`：缺少清单、本地版本未知、规则结构未知、CSV 列无法识别等；CLI 对应退出码 3。
- `status=verified`：支持的本地清单完整性检查完成且无错误或警告；CLI 对应退出码 0。
- `production_eligible=false`、`schema_verified=false`：始终固定，不代表上游规则 Schema 审核、规则安全审查或回测门槛已完成。
- `errors/warnings` 为包含英文 `code`、中文 `message` 与可选 `path` 的对象列表；`files` 为按路径排序的 `path/size/sha256` 对象列表。
- `stats` 包括 `file_count/total_bytes/rule_count/course_codes`。
- `source_hash_kind=sha256-content-inventory-v1`；`source_hash` 是排序文件证据列表的规范 JSON 的 SHA256。同内容 ZIP 与目录指纹相同，它不是 ZIP 原始容器字节哈希。不完整读取时为 `null`。

## 已实现的核查

1. ZIP 拒绝绝对路径、盘符、`..`、反斜线、空路径分量、`.`、Windows 流名称、保留设备名及末尾空格或点。拒绝 Unicode NFC/大小写等价的重复路径及文件与目录冲突。
2. 拒绝 ZIP 符号链接、FIFO 等特殊类型、加密成员、目录成员携带内容及类型歧义。CRC 损坏返回结构化失败。
3. 目录与来源根、全部祖先路径均核查符号链接/重解析点；Windows junction 的真实 fixture 验证拒绝行为。普通文件在打开前后及读取后核对身份、大小与时间戳；目录末次扫描再比较全体文件快照，识别同长度改写。
4. 最多 2000 文件、单文件 8 MiB、累计 64 MiB；预检查与实际流式读取均限额。另加 ZIP 容器 128 MiB、总路径条目 10000 的资源保护，ZIP 中央目录 seek/read 也受容器限额约束。
5. `manifest.json` 必须是 UTF-8 JSON 对象，`files` 是相对路径到 64 位 SHA256 的字典。核对遗漏文件、虚构文件、哈希错误及清单路径冲突。清单本身不列入 `files`，防止不可能实现的自身哈希依赖。
6. `bundle_version` 要求非空字符串；整数 `1` 或字符串 `"1"` 是本工具支持的本地清单 envelope 版本。其他 `schema_version` 明确返回 `unverified`。JSON 重复键、NaN、Infinity、指数溢出均拒绝。
7. `rules/rules.json` 支持 list 或 `{rules:list}`，核查 `id/rule_id` 唯一及别名冲突。未知结构或缺少可识别 ID 时只报告 `unverified`，不声称上游 Schema 已验证。
8. `data/` 下 CSV 只在可唯一识别 `course_id/course_code/lesson_id/课号/课程编号` 时扫描 `V011/V012`；未知列名给警告，缺少任一代码给警告，数据行列数不一致给错误。不更改全局 CSV 参数。

## 红绿验证记录

工作目录：本报告上级目录 `work/ai_operator_phase1`。

任务 C 命令：

```powershell
& 'C:/Program Files/Python311/python.exe' -m unittest discover -s tests_ai_operator -p test_bundles.py -v
```

- 首轮 RED：22 项测试均因缺失 `inspect_bundle` 失败，退出码 1。
- 首轮 GREEN：22 项，20 通过、2 跳过，退出码 0。
- 第二轮 RED：新增数值溢出与 CSV 行结构案例复现 3 个失败；补充真实 junction、CRC 损坏、目录大小上限与 ZIP 大小边界测试同时通过。
- 第二轮 GREEN：26 项，24 通过、2 跳过，退出码 0。
- 第三轮 RED：同长度文件改写、打开后 ZIP 增长复现 2 个失败。
- 第三轮 GREEN：28 项，26 通过、2 跳过，退出码 0。
- 最后修正实际读取限额测试的 fixture 注入位置，确保实际流读取触发 `limit_exceeded`，而非仅被快照变化阻挡。

全量命令：

```powershell
& 'C:/Program Files/Python311/python.exe' -m unittest discover -s tests_ai_operator -v
```

首次全量 64 项时，`test_help_has_no_filesystem_side_effect` 因 CLI 帮助输出 GBK/UTF-8 解码冲突失败，已通知主代理修复；不修改该文件。随后全量 80 项无失败，2 跳过，退出码 0。

跳过的两项为目录符号链接与来源根符号链接真实创建测试，系统返回 `WinError 1314`。并未隐瞒该缺口；ZIP 符号链接和真实 Windows junction 拒绝测试已通过。

## 剩余限制

- 尚未取得真实 ZIP、可信完整上游 Schema、规则审核结论或回测证据；生产资格固定为 `false`。
- 目录核查采用读取前后快照与身份比较，适用于静态本地包；它不提供操作系统级原子目录快照。针对恶意进程并发替换祖先目录的严格隔离，应使用不可变快照或额外操作系统隔离。
- CRC、SHA256 与自带 manifest 只能证明内容一致性，不能证明发行者真实性；本批没有签名或可信发行公钥。
- `source_hash` 为内容证据指纹；需要原 ZIP 原始字节证据时应由独立只读证据采集另存容器哈希。
