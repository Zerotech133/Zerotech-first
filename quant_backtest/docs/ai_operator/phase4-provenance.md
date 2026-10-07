# 行情来源快照模块实施与验证

实现文件仅包含 `ai_operator/provenance.py`、`tests_ai_operator/test_provenance.py`、本报告。接口保持冻结计划：`capture_snapshot(path, *, source_id, code, price_basis, revision, format='json')` 与 `validate_snapshot(snapshot)`。

## 实现契约

- 从普通本地文件一次读取有限字节，读取完成后调用 `contracts.utc_now()`；同一字节缓冲区用于 JSON/Parquet 解析与 SHA256。单文件上限 32 MiB，拒绝空文件、符号链接和 Windows 重解析点（包括父目录 junction）。
- 在文件系统访问前按路径文本拒绝 UNC、`//` 前缀、Windows 设备命名空间以及 NUL；验证快照源路径采用相同规则。没有网络读取接口。
- 严格 JSON 对象和日线字段，拒绝重复键、未知字段、非有限数、布尔数值；Parquet 按证券选择，读取真实存量 `ts_code/trade_date/open/high/low/close/volume`，兼容 `vol`。其他证券不混入，附加列只绑定于原始文件哈希。
- 保留停牌零量日线；OHLC 为有限正数并满足区间，volume 有限非负，最多 5000 根，唯一日期排序；未来收盘拒绝。
- `close_at` 始终输出本地交易日期的 `15:00:00+08:00`，`received_at` 和全部 `available_at` 为同批 UTC 时间。验证可接受等价 UTC 收盘时间，并规范回本地格式。
- 验证严格重验完整字段、语义、时间和对象哈希；不重新读取来源文件，返回深复制规范对象，不修改调用者输入。规范化后重新计算 `snapshot_id`。

## TDD 证据

工作目录：`C:/Users/Administrator/Documents/Codex/2026-10-07/new-chat-2/work/ai_operator_phase4`。

1. 首次 RED：`python -m unittest discover -s tests_ai_operator -p test_provenance.py -v`，20 项均因缺少来源快照模块断言失败，退出码 1。
2. 首次 GREEN：同命令 20 项成功，1 项因 Windows 无创建符号链接权限跳过，退出码 0；Windows 短路径经规范化修复后精确路径断言成功。
3. junction 补测 RED：21 项运行，新增 `test_windows_parent_junction_rejected` 因未抛 `ValueError` 失败，退出码 1。增加重解析点检查后同命令成功，1 项跳过。
4. UNC/设备路径审查补测 RED：23 项运行，10 个子场景失败，退出码 1；以 `lstat/open` 的拒绝访问替身确保没有真实远端访问。加入访问前文本检查后 GREEN。
5. 最终模块 GREEN：上述命令，`Ran 23 tests in 1.337s`，`OK (skipped=1)`，退出码 0。
6. 最终全量回归：`python -m unittest discover -s tests_ai_operator`，`Ran 256 tests in 19.050s`，`OK (skipped=4)`，退出码 0。并行模块还在添加测试，此计数是本次运行的实际结果。

本模块唯一跳过项为真实符号链接创建；Windows junction 的真实创建和拒绝测试已执行成功。全量其余三项也是现有模块的符号链接创建权限限制，无测试失败。

## 真实只读验收

源文件：`E:/老田的AI助手/quant_backtest/data/ashare/000001_SH.parquet`。只读 capture 后 validate 返回相同快照，原文件前后 SHA256 与快照 source_file.sha256 一致；未修改 E 盘文件。

- 证券：`000001.SH`；300 根，2025-01-02 至 2026-04-01。
- 实际接收时间：`2026-10-07T09:44:29.706368+00:00`。
- 前后源文件 SHA256：`2aedd9b3c2922058831932ace36393bd405ec86948b49ee8a6d081828e660d48`。
- `historical_availability='unknown'`、`supplier_verification='unverified'`、`production_eligible=false`。

## 限制

只证明本进程当前读取时刻及内容完整性，不证明任何历史决策时已接收这些价格。SHA256 不是签名，不认证供应商；价格口径和修订仍是调用者声明。外部可以重新伪造整份文件并计算新哈希，验证器不能提供不可伪造的接收证明。支持范围限于中国沪深交易所日线固定 15:00 收盘；JSON 源没有逐行证券字段，证券身份取调用者声明。

Parquet 按需使用已有 pandas 和 Parquet 引擎；本次使用既有依赖，未联网、未安装依赖、未提交代码，也未修改 CLI 或其他模块。
