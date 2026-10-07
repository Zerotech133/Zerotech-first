# 第三批独立审查报告

审查日期：2026-10-07。结论：冻结版未发现 P0、P1、P2 或 P3 级可行动缺陷；在本批显式工程契约范围内无阻断项。此结论不代表真实历史行情来源已认证、模型效果成立或可执行交易。

## 范围与最终版本

先完整阅读 `plan.md`，随后审查 `ai_operator/outcomes.py`、`ai_operator/dataset.py`、`ai_operator/cli.py`、对应三份测试与 `acceptance.py`，并比较 E 盘既有 `quant_backtest/ai_operator/cli.py` 基线。仅写本报告，未修改实现、测试或 E 盘；未调用网络、模型、交易或 Git，未创建子代理。

冻结版 SHA256（小写）：

| 文件 | SHA256 |
| --- | --- |
| ai_operator/outcomes.py | 529cd85685c00e2f9204c3eff3ea505c8180efbebb1304ed68ee42bb990b2c04 |
| ai_operator/dataset.py | e34d3347722e4f8a9a3360eed77a277775a1bd23ddff572c2e3c40aaf5c7929f |
| ai_operator/cli.py | 2982986a80d7aeb9ed5cf36104e8f859962af6ec7c87d33bf0aa99a719f1e722 |
| tests_ai_operator/test_outcomes.py | 311100b13bc3454f31a75a5dc926ed1ed38806692d5b1837eb54cd4c3516e810 |
| tests_ai_operator/test_dataset.py | 6c15b480e1693e645e4485b7028a380d5a9dffe6cf047819056c96d9fe586562 |
| tests_ai_operator/test_phase3_cli.py | 89620de4b7961795c7c677420c16c465955c120eaf6f3697a09fe805f6a42047 |
| acceptance.py | aa68fa590ff55d3114c37dfd982c5e54f814b4dc56f4726ed2d12e8242fc31f7 |

## 检查结果

| 重点 | 源码位置 | 核验结果 |
| --- | --- | --- |
| 显式日历与严格未来参考点 | outcomes.py:45、134、139 | 参考收盘严格晚于决策时刻；终点是参考点后 H 个日历 session；周末、休市由调用者日历确定，缺行情不会跳到下一根 bar。 |
| 未知来源与未到达价格 | outcomes.py:143、148、150、156、166 | 非 matured 的 reference_close、end_close、target、label_available_at 全部保持 null；unknown 和未知 available_at 优先 audit_only；完整窗口所有 bar 到达后才成熟。 |
| 决策契约 | outcomes.py:110、116、118 | 输入冻结；重建完整影子决策并按规范 JSON 比较，字段缺失、类型混淆、非影子环境和重哈希后的不一致字段均拒绝。 |
| 同批延迟到达 | outcomes.py:90、156、167 | 多根 bar 可共享 available_at，成熟时间取整个窗口最大可用时间。 |
| 成熟、purge 与 embargo | dataset.py:77、119、126 | 先排除未成熟行；训练和验证标签结束与可用时刻必须严格早于对应边界；embargo 使用半开区间；测试标签可晚于 test_end，但必须不晚于 as_of。 |
| 传递分组 | dataset.py:147、183 | family 与同证券同 UTC 日建立候选行连通组，只保留最晚折；purge 等已剔除行不作为分组桥，符合计划。 |
| 全输入绑定 | dataset.py:94、195、196 | input_hash 绑定所有规范化排序的输入行，包括剔除行特征与 target；split_hash 同时绑定 input_hash；改变剔除行内容会改变两个哈希，换输入顺序不改变结果。 |
| 摘要语义 | dataset.py:200、228、231、243、252 | 完整字段和哈希检查后重跑保留行切分；重哈希无法绕过保留行成熟度、特征时间、折归属、embargo 或跨折分组约束；无 excluded 时重算 input_hash。 |
| CLI 只读与 JSON 错误 | cli.py:75、98、132、140、148、202 | 三条新增命令在数据库分支前返回；复用 8 MiB、重复键、非有限数、编码和嵌套校验；失败输出结构化 JSON、退出码 2。既有写入命令仍由原显式子命令触发。 |

## 验证证据与复现

执行环境：`C:/Program Files/Python311/python.exe`，所有 Python 调用使用 `-B`。

全量回归命令：

```powershell
& 'C:/Program Files/Python311/python.exe' -B -m unittest discover -s tests_ai_operator -v
```

结果：总计 192 项，190 项通过，2 项跳过，无失败。跳过的是既有 bundle 的目录/根符号链接测试，原因均为 Windows `WinError 1314` 权限不足；junction 测试通过。第三批 53 项全部通过，分为 outcomes 23、dataset 24、CLI 6，无跳过。冻结后没有无理由重复全量回归，针对最终版本另执行 24 项断言，全部通过。

定向探针可从对应测试夹具复现，以下例子均不写文件：

```python
from tests_ai_operator.test_outcomes import fixture
from ai_operator.outcomes import label_decision

value = fixture()
value["evaluated_at"] = "2026-10-09T07:00:00Z"
value["bars"][2]["available_at"] = "2026-10-09T08:00:00Z"
label = label_decision(**value, horizons=[5])["labels"][0]
assert label["reason"] == "not_available"
assert all(label[key] is None for key in
           ["reference_close", "end_close", "target", "label_available_at"])
```

摘要重哈希探针：以 `test_dataset.row/split/rehash` 构造一条 `purge_train` 剔除行及一条测试保留行；把保留行 `label_available_at` 改成 `2027-01-01T00:00:00Z` 后调用 `summarize_split(rehash(value))`，得到 `ValueError`。把保留行 `feature_available_at` 改到决策之后也得到 `ValueError`。对应源码为 `dataset.py:228` 与 `dataset.py:231`。

CLI 定向探针通过 `unittest.mock.patch('ai_operator.cli.Path.open', return_value=io.BytesIO(raw))` 保留真实 `_read_input` 逻辑；同时把 `ai_operator.memory.open_store` 设为调用即失败。对三条命令分别输入 1100 层数组、`1e9999`、5000 位整数、重复键、非法 UTF-8 和超过 8 MiB 的字节串，18 种组合全部返回 2、stdout 为 `ok:false` 的 JSON、错误类型 `ValueError`、stderr 为空，数据库守卫未触发。

极端时间探针：`0001-01-01T00:00:00+14:00`、`9999-12-31T23:59:59-14:00`、年份 0000 和 10000，分别进入 dataset 边界及 outcome evaluated_at，均为 `ValueError`。巨大 embargo、非整数 embargo 和深嵌套非数值特征均为 `ValueError`；价格计算溢出已有 outcomes 测试覆盖。任意精度整数在数学上仍有限，单个巨大整数摘要可以保留整数；混合统计无法得到有限值时按契约转为 `ValueError`。

独立端到端探针执行 `acceptance.main()`，仅替换 `Path.mkdir/write_text` 阻止产物写入：90 session 工程夹具产生 20 个成熟标签、2 个未到达标签、2 个 unknown 审计标签、2 个缺 bar 待成熟标签，输入反序结果一致，`production_eligible=false`。主代理另执行真实产物验收，并报告 11 行保留 train 1 / validation 1 / test 2、7 行剔除覆盖六种原因，以及存量源 SHA256 不变；该存量源核对由主代理负责，本次独立探针未读取真实市场数据。

## 已确认边界

- `trusted`、日历版本、source_hash 和复权口径均为调用者声明，模块不认证官方来源、真实可用时间或来源哈希与行情的一致性；真实资料进入训练前仍需上游核验。
- 输出 target 是未来收盘价格观察值，买卖动作没有方向翻转，也没有成交、手续费、净值或卖出做空收益假设；描述性均值与正值比例不能解释为策略成交收益。
- 有 excluded 时 split 只保留 ID 与原因，未保存被剔除行的原特征、目标或时点。摘要能验证哈希形式和保留折语义，不能独立重建原 input_hash、原剔除原因或已剔除桥接关系。复现：修改合法格式的 input_hash 或一个合法 excluded 原因并重新计算 split_hash，摘要可以接受；这是 `plan.md` 明示的可恢复性边界，不列为缺陷。必须外部保留完整原始请求，重跑 split_dataset 并核对 input_hash 与 split_hash。
- SHA256 与规范 JSON 校验用于内容完整性和可重复性，不提供来源签名认证；能生成一致新输入及对应哈希的调用者仍由上游治理负责。
- 本次验证没有训练、参数选择、模型晋升或交易执行；未取得原规则 ZIP 与缺真实历史可用时间的资料仍只能用于审计。
