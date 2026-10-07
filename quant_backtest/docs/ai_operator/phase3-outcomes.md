# 结果标签实施报告

## 接口与标签口径

新增 `ai_operator.outcomes.label_decision(pack, decision, bars, calendar, source, evaluated_at, horizons=None)`。默认窗口 `[5,20]`；显式窗口为 1 至 10 个唯一整数，每个处于 1 至 250。使用标准库，严格冻结 JSON，不修改输入，不写数据库，不调用模型、不训练、不执行交易。

复用 `contracts.canonical_json`、`contracts.normalize_time` 与 `shadow.make_shadow_decision`。完整影子决策经事实包重建后进行规范 JSON 比较，包括 `decision_id`；布尔值不能冒充数字。事实包和决策必须通过既有影子契约。输出时间统一为 UTC，`outcome_id` 覆盖除自身之外的完整结果；`production_eligible` 恒为 `false`。

参考日是显式日历中第一个收盘瞬间严格晚于 `decision.as_of` 的交易日。第 H 日终点是在参考日位置再向后移动 H 个日历项，窗口包含参考日至终点的 H+1 根行情。周末和休市只由调用者提供的日历定义；缺行情不会缩短窗口或改取后续行情。参考日缺失、终点超出日历时，不能用已有行情根数或自然日补足。

成熟目标是 `(end_close/reference_close)-1`，`target_kind='forward_close_return'`。四种动作均观察同一价格变化；`candidate_sell` 不翻转符号。该结果不代表委托成交、做空收益、策略净值或收益保证，没有手续费与滑点假设。

## 成熟状态与可用时间

状态按以下优先级判定：

1. `audit_only/unknown_source`：来源声明为 `unknown`。
2. `audit_only/unknown_availability`：已存在的任一窗口行情没有可用时间；日历不完整时仍检查已提供的窗口部分。
3. `pending/calendar_incomplete`：没有参考日，或日历没有终点。
4. `pending/missing_bar`：窗口任一期行情缺失，包括参考日、终点和中间交易日。
5. `pending/not_available`：窗口任一期行情可用时间晚于 `evaluated_at`。
6. `matured`：窗口完整，来源声明可信，所有行情均已到达；`reason=null`。

同批多个行情允许相同的 `available_at`，不要求可用时间随交易日严格递增。成熟结果的 `label_available_at` 是整个 H+1 日窗口可用时间的最大值。任意非成熟状态都将 `reference_close`、`end_close`、`target`、`label_available_at` 置为 `null`，即使输入已包含未来价格也不曝光。日历有终点时可以返回其日期与收盘时点。

## 严格验证与边界

日历最多 5000 项、行情最多 5000 根，拒绝未知或缺失字段。日历日期要求 `YYYY-MM-DD`、唯一且升序，并等于其收盘时区的本地日期；收盘瞬间另行要求严格递增。行情日期必须在日历内、唯一升序，其收盘瞬间必须等于日历值；非空可用时间不得早于收盘。价格要求非布尔有限正数，目标计算溢出或非有限值通过中文 `ValueError` 报错。

`source_id`、小写 SHA256、统一 `price_basis` 和 `availability_status` 均由调用者声明。本模块不认证官方交易日历，不联网核验来源哈希，不推断真实历史数据到达时间，也不能从价格数值判断复权口径是否实际混用。调用者须保证全批行情遵循同一 `raw/qfq_snapshot/hfq_snapshot` 口径；历史到达时间无法证明的数据应声明 `unknown` 并仅供审计。夹具交易日不代表真实交易所休市安排。

内容 ID 用于一致性验证，不是身份签名或第三方可信来源证明；持有全部输入的调用者可构造一份新的合法建议。既有事实契约只拦截已知后验字段，不认证任意事实数据。

## 实际验证

先创建测试，执行 `C:/Program Files/Python311/python.exe -B -m unittest tests_ai_operator.test_outcomes -v`：RED 为 `ModuleNotFoundError: No module named 'ai_operator.outcomes'`。随后实现并执行同命令：23 项测试通过，退出码 0。

覆盖周末与显式休市、严格晚于决策的参考收盘、H5/H20、参考/终点/中间缺行情、未知来源、未知到达时间优先级、未来价格隐藏、批量同时间到达、整段窗口最大可用时间、日历不足、窗口外未知行情、买卖不翻转、时区规范化、完整字段与哈希、重算哈希后的非法决策、输入冻结、非法 JSON、日期/顺序/数值/来源限制及溢出。

并行实施期间曾执行全量 `unittest discover -s tests_ai_operator -v` 快照：192 项，5 个失败、2 个 Windows 符号链接权限跳过。失败为数据集任务正在修改的 `test_excluded_features_and_targets_are_bound_by_input_hash` 的 4 个子场景和 `test_half_open_partitions_policy_and_hash`；本模块 23 项均通过。该快照不作为第三批整体验收；冻结后的全量回归由主代理执行，本任务未修改其他模块。
