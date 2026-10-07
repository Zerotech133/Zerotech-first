# 第二批 A：逐根结构回放

## 交付范围

仅新增 `ai_operator/replay.py`、`tests_ai_operator/test_replay.py` 和本报告。复用 `contracts.canonical_json`、`contracts.normalize_time`；未写入 E 盘、未提交 Git、未启动交易、网络行情或模型任务。

接口为 `replay_bars(bars: list[dict], analyzer=None, algorithm_id='local-chanlun-bi-v1') -> dict`。结果包含 `schema_version=1`、`algorithm_id`、`bar_count`、`snapshots`。每个快照包含 `as_of`、`bar_dt`、`visible_count`、算法完整 `structures` 和 `changes={added: [], withdrawn: []}`。

## 输入与算法边界

- K 线只能包含 `dt/o/h/l/c/v/close_at/available_at`，全部字段必填；最多 2000 根。只接受标准 JSON 类型，拒绝非有限浮点数、布尔价格/成交量、未知字段和缺少显式可用时间的数据。
- `dt` 为非空唯一字符串；两种时间均必须带时区，规范成 UTC 微秒 ISO 字符串后分别严格递增，且 `available_at >= close_at`。OHLC 为有限正数，满足价格区间关系，成交量非负。先验证完整输入，非法输入不会触发任何算法调用。
- 每根仅调用 `analyzer(prefix)`，前缀为深拷贝。分析器修改参数或反复复用可变输出都不会修改原始输入或旧快照。`as_of` 取当前根的 `available_at`，而非 `close_at`；延迟可用不会被自动提前。
- 默认适配器在非空回放时延迟导入 `chanlun_bi.analyze`，传入该前缀对应的 `closes`。缺依赖产生中文 `RuntimeError`，空输入无需依赖。隔离工作区应通过部署后的模块路径或 `PYTHONPATH` 加入 `E:\老田的AI助手\quant_backtest`。
- 既有默认算法使用 `dt` 字典序计算 MACD 区间，因此额外要求默认调用的 `dt` 严格字典序递增；自定义分析器仅要求 `dt` 唯一和两种时间递增。此检查在默认算法首次调用前完成。
- 输出必须是标准 JSON 对象，包含 `fractals/bis/segments/pivots/signals` 中至少一种；未出现的集合按空集合跟踪，`structures` 保留原输出。仅额外接受本地算法的非负整数元数据 `merged_n/top_n/bottom_n`。集合必须是对象列表，每个对象必须包含对应定位字段：分型 `dt`、笔/线段/中枢 `e_dt`、信号 `d`。定位及出现的 `dt/s_dt/e_dt/d` 均须引用当前可见 K 线；未知形状、重复结构版本和未来定位显式报错。

## 版本与确认语义

`version_id = SHA256(canonical_json({kind, data}))`，与对象字段顺序无关，不同结构种类不会共用版本身份。新增、撤销记录均包含 `kind/version_id/pivot_at/confirmed_at/observed_at/status/data`。撤销在当前快照增加新记录，保留旧快照及原 `confirmed_at`；不回写历史。

`status` 始终为 `observed`。`confirmed_at` 仅表示这一内容版本在本次连续存在期间的首次观测时点，取该根 `available_at`。消失后再现属于新的出现记录，保留内容哈希，重新赋予 `confirmed_at`。结构原始定位保留在 `pivot_at`，不能拿它冒充可用时间。持续存在的同版本不重复生成新增记录。

默认算法是既有简化笔、简化线段、笔中枢及简化买卖点口径，不宣称标准缠论一致性，也不承诺末端笔或任何结构永不重绘。只追加合法未来后缀不会改变已有回放快照；自定义分析器需自行遵守确定性，不能通过外部状态或闭包访问未来数据。

逐根完整前缀复制及线性分析时的基础回放工作量为 `1+2+...+N`，即 `O(N²)`。一般总耗时应表示为 `O(N² + Σ T_analyzer(k) + 全部输出编码成本)`，非线性分析器不能获得无条件的 `O(N²)` 保证。保存每根完整结构快照的空间取决于所有快照总大小，结构数随前缀线性增长时为 `O(N²)`。2000 根是显式上限，不代表已完成性能基准。

## RED/GREEN 证据

PowerShell，在 `work\ai_operator_phase2` 执行：

```powershell
& 'C:\Program Files\Python311\python.exe' -B -m unittest discover -s tests_ai_operator -p test_replay.py -v
& 'C:\Program Files\Python311\python.exe' -B -m unittest discover -s tests_ai_operator
```

首次 RED：`ModuleNotFoundError: No module named 'ai_operator.replay'`，对应尚未实现的模块。实现后 17 项测试 GREEN。补充默认算法乱序 `dt` 测试先 RED，结果为 `ValueError not raised`，添加检查后 18 项 GREEN（0.019 秒）。

最后全量回归：135 项，`OK (skipped=2)`，11.951 秒。跳过为既有 `test_directory_symlink_is_rejected`、`test_root_symlink_is_rejected`，原因是 Windows 当前账户缺少创建符号链接权限；无失败。共享目录中的 B、C 也包含在本次回归内，但本报告只交付 A。真实 E 盘算法的离线验收由主代理统一执行。

覆盖重点：合法未来后缀不改历史；深拷贝和输出冻结；延迟可用、乱序、时区等价时间；修订/撤销/再现；规范哈希；五类定位；未知输出、非 JSON 数据、未来引用及重复版本；默认延迟依赖和收盘价传递。
