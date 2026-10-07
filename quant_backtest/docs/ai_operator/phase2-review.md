# 第二批独立审查报告

结论：最终暂存实现未发现阻断交付项。审查发现的空文件隐式初始化问题已修复并复核；本结论仅覆盖结构回放与影子审计工程，不证明历史收益、来源时点认证或生产交易资格。

## 已修复发现

### P2：影子命令曾隐式初始化已有空文件

原位置：`ai_operator/cli.py:127`、`:131`。原实现仅检查 `Path.is_file()`，再以可写方式打开数据库。合法影子请求配合一个 0 字节 `empty.sqlite3` 会返回 0，文件变为 53248 字节并新增两条事件，绕过计划要求的显式 `init` / `import-legacy` 初始化。

独立复现使用 `tempfile.TemporaryDirectory()`、空文件和合法 `abstain` 请求，通过 `main(['shadow-decision', '--input', ..., '--db', ...])` 调用；仅替换 `_read_input` 以提供合法请求，其余数据库路径保持真实。修复前结果为 `status=0`、两项事件 ID。

最终实现先以 `open_store(args.db, readonly=True)` 验证已有库，再可写打开。`test_shadow_empty_database_is_not_initialized` 已通过：返回 2、`ok=false`、文件字节仍为空、未产生 WAL。此项已关闭。

## 接口与时点复核

- `replay.py` 在全部输入校验后才调用分析器；每次输入是当前前缀的深拷贝，输出经规范 JSON 冻结。已有前缀测试同时覆盖未来后缀追加、分析器修改输入、复用可变结果，历史快照保持一致。
- `close_at` 与 `available_at` 分别严格递增，且 `available_at >= close_at`；时区统一为 UTC 后再比较。延迟行情的快照以实际声明的 `available_at` 为 `as_of`，没有提前纳入可见集合。
- 最终 `replay.py` 默认适配器在首次分析前拒绝非严格字典序递增的 `dt`；自定义分析器仍允许任意唯一非空 `dt`，符合本批明确的默认算法适配约束。
- `replay.py:113` 仅分析当时前缀；结构的定位字段必须引用当时可见 K 线。`replay.py:124` 的 `confirmed_at` 是连续出现期间首次观测时间，状态始终为 `observed`。撤销形成新记录，重新出现重新记录首次观测时间，没有回写历史。
- `shadow.py:97` 的完整包重验同时核对固定字段、版本、事实 ID、递归后验字段、时间顺序与完整内容哈希。重新计算哈希不能使非法未来事实、非法 Schema 或非法后验字段通过。
- 主代理追加的真实样本已知后验字段 `r5/r10/r20/r60`、`ret5/ret10/ret20/ret60`、`mature5/mature20`、`post10/post_hm10` 已纳入递归拒绝；新增嵌套与重新计算哈希用例通过，历史 `pre_close` 仍被接受。
- `shadow.py:138` 在首次写入前重建并规范比较完整决策，拒绝篡改的执行许可、置信度、版本、证据、时点和 ID。最终建议仍为 `execution_allowed=false`、`confidence=null`。
- CLI 新请求拒绝未知/缺失字段、重复 JSON 键、非有限数；8 MiB 输入上限在解析前执行。独立输入探针验证 8388609 字节、非法 UTF-8、1100 层嵌套、畸形 JSON 均返回 2 和可解析错误 JSON。

## 数据库失败与恢复复现

独立采用真实 SQLite 触发器，而非仅使用测试包装器：

```sql
CREATE TRIGGER fail_shadow BEFORE INSERT ON events
WHEN NEW.kind='shadow.decision'
BEGIN SELECT RAISE(ABORT, 'simulated'); END;
```

`record_shadow_decision` 抛出 `IntegrityError` 后，`events_by_kind` 仅为 `{"shadow.fact_pack": 1}`；删除触发器后重试得到 `{"shadow.decision": 1, "shadow.fact_pack": 1}`，再次重试返回同一组事件 ID。此行为符合两次独立提交、失败可恢复的计划约定，没有把它描述为跨事件原子事务。

独立对含 `business` 表的外部 SQLite 文件提交合法请求，CLI 返回 2；文件 SHA-256 保持不变。缺失库、非法请求和空库边界分别由现有及新增 CLI 用例覆盖。

## 验证证据

使用 `C:\Program Files\Python311\python.exe`，均加 `-B`：

- 首轮 `-m unittest discover -s tests_ai_operator -v`：总计 135 项，133 项通过、2 项跳过、0 失败。跳过来自首批目录/根符号链接用例，原因是 Windows 权限不足。
- 最终修复与后验字段扩展后，`-m unittest tests_ai_operator.test_replay tests_ai_operator.test_shadow tests_ai_operator.test_phase2_cli -v`：总计 49 项，49 项通过、0 跳过、0 失败。
- 本代理未重复执行真实行情算法验收；只读检查 `acceptance.py`，其对历史数据采用本次 `utc_now()` 接收时点、对日线收盘时刻使用 CN 15:00 工程推导，并明确输出 `provenance_status=unverified`、`historical_profitability_verified=false`、`production_eligible=false`。主代理提供的真实算法验收结果应以对应验收输出文件为准。

## 剩余边界

- 显式输入时间只校验格式与相对顺序；其真实性仍由调用者与来源审核负责。已知字段拒绝无法识别所有别名或被重命名的后验信息，事实 `data` 仍需来源审核。
- 简化结构允许修订和撤销，`observed` 不等于不可重绘的最终确认。
- 两事件写入保持可恢复的独立提交；第一条事实事件可能在第二条失败后暂时单独存在。
- 回放上限为 2000 根，前缀分析成本至少为 O(N²) 级别；本次真实本地样本验收规模由主代理限定为 120 根，不能据此宣称已测定上限数据的性能。

本代理只创建本报告，未修改实现、测试、E 盘文件或 Git 状态，未使用网络或交易功能。
