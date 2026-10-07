# 第四批 B：交易日历快照

实现文件为 `ai_operator/trading_calendar.py`，测试为 `tests_ai_operator/test_trading_calendar.py`。只修改这两个文件和本报告；未修改 CLI、其他模块或 E 盘源文件，未提交，未联网，未调用同步脚本。

## 冻结接口

```python
capture_calendar(paths, *, exchange, source_id, revision,
                 coverage_start, coverage_end, format="json") -> dict
validate_calendar_capture(capture) -> dict
```

- 显式选择 SSE/SZSE，覆盖 1 至 5000 个自然日；1 至 32 个唯一普通文件，每文件最多 32 MiB。
- JSON 固定 `rows/exchange/cal_date/is_open` 字段，拒绝重复键、未知字段、非有限常量和 bool 开市状态。Parquet 按需使用本地已有 pandas，从同一读取字节解析 exchange/cal_date/is_open，忽略 pretrade_date 等额外列的推导用途，额外列仍由源字节哈希绑定。
- 所有自然日必须覆盖；相同重复合并、冲突重复拒绝。全闭市覆盖合法，返回空 sessions，不从证券缺失行情或工作日规则推导开闭市。
- 源路径先拒绝符号链接、父目录链接和 Windows 重解析点，再解析规范绝对路径、去重和排序；拒绝 UNC/设备命名空间。打开后的普通文件身份与原检查一致，并限制实际读取字节数。
- 所有文件读完、解析后由 `contracts.utc_now()` 记录接收时刻，调用者没有时间注入参数。解析与 SHA256 使用同一读取字节，源文件随后被替换也不改变这次已冻结内容。
- `calendar.version` 是 exchange、覆盖区间、完整 days 的 SHA256 语义哈希；文件顺序、相同分片重复和源行顺序不改变版本。`capture_id` 绑定除自身外完整对象，包括源文件信息及接收时刻。
- `close_at` 固定保留本地 `YYYY-MM-DDT15:00:00+08:00`，与第三批本地日期关联一致。
- 重验严格检查完整字段、标准 JSON 类型、逐日覆盖、days/sessions 对应、语义版本和完整对象哈希，要求规范微秒 UTC received_at 且不晚于校验时刻；返回深拷贝，不重新读取源文件。

## RED / GREEN 证据

首次 RED：在模块不存在时，先新增测试，再执行：

```powershell
python -m unittest discover -s tests_ai_operator -p test_trading_calendar.py -v
```

结果：17 项、17 个失败，退出码 1；明确失败原因为交易日历模块未实现。随后实现并修复 Windows `ADMINI~1` 短路径未展开问题。

最终模块 GREEN：

```powershell
python -X utf8 -m unittest discover -s tests_ai_operator -p test_trading_calendar.py -v
```

结果：24 项，`OK (skipped=1)`，退出码 0。真实 symlink 创建因 Windows 1314 权限限制跳过；真实 `_winapi.CreateJunction` 父目录链接拒绝测试以及文件/父目录重解析点保护测试均通过。

覆盖国庆和春节显式闭市、全闭市合法、缺自然日和空选取、交易所分离、缺行情不推导停牌、重复/冲突、多源排序与语义版本、严格 JSON、Parquet 实际字段与 selected 行校验、源文件读取后替换、实际当前接收/禁止注入、篡改和重算哈希后的非法语义、非 JSON 对象/bool/float、空文件、32 MiB 正好接受和超一字节拒绝、UNC/设备命名空间、无网络连接，以及输入深拷贝。

最新完整回归命令：

```powershell
python -X utf8 -m unittest discover -s tests_ai_operator
```

结果：256 项、18.487 秒，`OK (skipped=4)`，退出码 0；无失败和错误。四个跳过均为当前 Windows 会话不允许创建真实 symlink：bundles 的目录/root 两项、provenance 一项、trading_calendar 一项。真实 Windows junction 测试通过。

## 真实源只读验收

源文件：`E:/老田的AI助手/quant_backtest/data/tushare/trade_cal/part_0000.parquet`。

通过 `python -X utf8 -c` 调用上述两个 API，分别在 capture 前后直接计算源文件 SHA256；无写操作。参数：exchange=SSE、source_id=local_tushare、revision=observed_current、覆盖 2026-09-01 至 2026-10-07、format=tushare_parquet。

- 源大小：131937 字节。
- 读取前后 SHA256 均为 `8cea0f82b3273004cbf80438a550f4d01c66e0a08583ca1f549cb7b3fd8b2f20`。
- 系统接收时刻：`2026-10-07T09:41:47.584876+00:00`。
- 完整覆盖 37 个自然日，21 个开市日；2026-10-01 至 2026-10-07 七天全部由源日历显式标记 `is_open=0`。
- 语义版本：`2ecaaaa8d7e6df98ad76042b00eab3c5fa3aa07cb64063462e35be6c96231a62`。
- capture_id：`042854187d79c5c598091b6721c499ce23480949a0fe453c3a57e4f3b210f072`。
- validate 成功，`supplier_verification=unverified`、`production_eligible=false`。

## 证据限制

这是当前本进程读取和本地内容完整性证据，不能证明过去决策时已经知道此日历版本，也不认证供应商或交易所真实性。外部可以重造整个对象并重算哈希；哈希不是签名。未根据分片数量默认认证完整性，只针对指定覆盖区间逐日校验。单文件读取受 32 MiB 限额约束，Parquet 解压后的引擎内存没有额外硬上限。全闭市快照可存档，但来源标签适配需要至少一个开市日。
