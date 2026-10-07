# 第三批 B：数据集与时间切分实施报告

## 接口与用途

新增 `ai_operator.dataset.split_dataset` 与 `summarize_split` 两个标准库纯函数。模块不写数据库、不调用网络或模型、不训练模型、不选择参数、不执行交易，也不授予生产使用资格；结果与摘要均固定 `production_eligible=false`。

样本目标口径固定为 `forward_close_return`，全部输入必须具有相同 `horizon` 与非空 `label_policy`。目标描述未来收盘价格变化，不代表策略净值、实际成交收益、卖出做空收益或收益保证。模块校验调用者提供的时间契约，不认证特征来源、标签来源或标签政策真实性。

## 输入与隔离政策

样本字段必须完整、无未知字段，`sample_id` 唯一。特征白名单必须为非空、唯一的非空字符串列表；与 `shadow._POSTERIOR_FIELDS` 共用已知后验禁用字段集合。每行特征完整覆盖白名单且不能多出键，特征与目标仅允许有限 `int/float`，拒绝布尔值、NaN、无穷数及非标准 JSON。

所有时间必须显式包含时区并规范化为 UTC，满足 `feature_available_at <= decision_at <= label_end_at <= label_available_at`。策略边界满足 `train_end < validation_end < test_end <= as_of`；`embargo_seconds` 是非负、非布尔整数，无法表示的时间偏移报 `ValueError`。

标签可用时间超过 `as_of` 的行优先记为 `immature`，目标不会以零值代替未成熟标签。训练折要求决策、标签结束和标签可用时间均严格早于 `train_end`；验证折从 `train_end + embargo` 开始，标签结束与可用时间严格早于 `validation_end`；测试折从 `validation_end + embargo` 开始至 `test_end` 之前，标签只需在 `as_of` 前到达。测试标签可在测试决策窗口结束后成熟。

边界采用半开区间。标签时间恰好等于训练或验证边界也剔除；决策时间恰好等于 embargo 结束时间可进入下一折，恰好等于 `test_end` 则记为 `outside_window`。剔除原因固定为 `immature`、`purge_train`、`purge_validation`、`embargo`、`outside_window`、`group_overlap`。

## 连通分组与确定性

仅对通过时间与成熟度筛选的候选行建立连通组。同一非空 `content_family_id`，或同一 `(code, UTC decision 日期)`，均连接；家族与证券同日关系可以传递。跨折组保留该组最晚一折的全部行，较早折行记为 `group_overlap`，不移动样本到另一时间折。已 purge、未成熟等被剔除行不作为候选组的连接桥。

所有折与剔除列表按规范化 `(decision_at, sample_id)` 排序，白名单排序，输入对象冻结复制，调用者输入不被修改。同内容输入行顺序不同或等价时区表示不同，结果及哈希相同。

## 哈希与摘要校验

顶层固定字段为 `schema_version`、`policy`、`feature_allowlist`、`train`、`validation`、`test`、`excluded`、`input_hash`、`split_hash`、`production_eligible`。`input_hash` 是规范化、排序后的全部输入行的规范 JSON SHA256，覆盖被剔除行的特征与目标。`split_hash` 覆盖除自身外的完整结果，并包含 `input_hash`。

摘要先校验严格字段、类型、政策、完整哈希，再将保留的三折样本重跑 `split_dataset`，要求各折、排序、特征白名单、规范政策一致。即使重新计算哈希，保留行的时间越界、未成熟、特征后验字段、跨折家族或证券 UTC 日泄漏、错误折归属仍会被拒绝。数字与布尔值不会混为一谈。

剔除项只保存 `sample_id/reason`。摘要校验原因集合、ID 唯一且不与保留 ID 重叠，不能从此列表重建原始剔除决定。无剔除行时重算并核对 `input_hash`；有剔除行时只能校验其小写 64 位 SHA256 格式和结果哈希覆盖关系，不能独立恢复被剔除行原内容。因此调用者必须另外保留原始输入，以便完整复核输入哈希和原始剔除决定。普通 SHA256 也不构成来源认证或数字签名。

描述性摘要返回各折数量、目标均值、`target>0` 比例与按原因计数；空折均值及比例为 null。均值使用标准库 `statistics.mean` 避免普通浮点求和溢出；极大数值不能生成有限统计时抛出中文 `ValueError`，不输出 NaN 或无穷数。

## 测试证据

初始 RED：`python.exe -B -m unittest tests_ai_operator.test_dataset -v` 因缺少 `ai_operator.dataset` 报 `ModuleNotFoundError`，退出码 1。随后最小实现 GREEN。

哈希契约修订 RED：对 purge 与 immature 行分别改变目标、特征，4 个子用例均观察到旧 `split_hash` 不变而失败，退出码 1；新增全部输入哈希后转 GREEN。

当前 B 专项：`C:\Program Files\Python311\python.exe -B -m unittest tests_ai_operator.test_dataset -v`，24 项通过，退出码 0。覆盖双时间 purge、未成熟优先级、半开 embargo、测试窗口之后成熟、UTC 同日直接跨折及传递分组、候选分组隔离、输入乱序与等价时区、空集与全剔除、黑白名单、严格 JSON、重新哈希后的泄漏拒绝、原输入哈希与极大有限数统计。

首次全量运行发生在 A 模块落地之前，168 项中因缺 `ai_operator.outcomes` 出现 1 个导入错误及 7 个 outcome CLI 失败，另有 2 个 Windows 符号链接权限跳过；B 测试与数据集 CLI 当时通过。A 落地后的全量复跑命令为 `python.exe -B -m unittest discover -s tests_ai_operator -q`，192 项通过、2 项符号链接权限跳过，退出码 0。后续集成及独立复审结果由主代理记录。
