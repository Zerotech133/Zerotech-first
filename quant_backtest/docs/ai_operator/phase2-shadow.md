# B：事实与影子审计实施报告

## 文件与冻结接口

仅新增本任务归属文件：`ai_operator/shadow.py`、`tests_ai_operator/test_shadow.py`、`reports/shadow.md`。
没有修改 SQLite DDL、其他模块或 E 盘文件，没有提交 Git。

```python
build_fact_pack(code, period, as_of, facts: list[dict], versions: dict) -> dict
make_shadow_decision(pack: dict, action: str, evidence_ids: list[str], rationale: str, valid_until: str) -> dict
record_shadow_decision(store, pack, decision) -> dict
```

## 校验与输出

- 复用 `contracts.canonical_json` 和 `normalize_time`；JSON 严格拒绝非有限数字、非字符串对象键、非 JSON 类型与循环引用。输入冻结为独立 JSON 数据，输出不共享输入的可变对象。
- 新建包的 `as_of`、`occurred_at`、`available_at` 规范为 UTC、六位微秒 ISO。事实字段严格固定为 `id/source/occurred_at/available_at/data`；ID 唯一，来源非空，data 为对象；每项满足 `occurred_at <= available_at <= as_of`。未来事实显式拒绝。
- `versions` 严格固定为 `data/algorithm/knowledge/rules`，全部非空字符串；code、period 非空。允许空事实列表供 observe/abstain 使用。
- 包包含 `schema_version=1/environment='shadow'/code/period/as_of/facts/versions/fact_hash`。递归拒绝 `post/forward_return/label/outcome`，以及样本盘点确认的 `r5/r10/r20/r60`、`ret5/ret10/ret20/ret60`、`mature5/mature20`、`post10/post_hm10` 后验字段；其中 `ret60` 按同类防误入需求一并拒绝。
- 后验字段集合只用于已知字段防误入，不代替任意 `data` 的来源与可用时点审核，不能保证检测所有换名泄漏。`pre_close` 等历史时点字段允许使用，仍需核验其来源、语义与可用时间。
- `fact_hash` 为除该字段外完整包的规范 JSON UTF-8 SHA-256。决策前严格重验整个包 Schema、类型、时点、版本、递归后验字段及哈希；正确重算哈希不能绕过语义验证。对收到的完整 JSON 原表示计算哈希，避免时间表示重写掩盖内容变化。
- 动作严格限定 `observe/candidate_buy/candidate_sell/abstain`；证据 ID 唯一且存在于包，候选买卖至少一项证据；理由非空，`valid_until > as_of`。
- 决策完整字段为 `schema_version/environment/code/period/as_of/fact_hash/versions/action/evidence_ids/rationale/valid_until/confidence/execution_allowed/decision_id`。`confidence=null`，`execution_allowed=false`。`decision_id` 为除 ID 外完整内容的规范 JSON UTF-8 SHA-256。
- 记录前重建合法决策，再用规范 JSON 核对所有字段（严格区别布尔值与数字），拒绝未知、遗漏、篡改字段与时点不一致；全部校验发生在首次事件写入之前。

## 事件、幂等与恢复边界

通过既有 `MemoryStore.append_event` 顺序保存：

1. `shadow.fact_pack`：payload 为完整事实包，source_id 为 fact_hash。
2. `shadow.decision`：payload 为完整决策字段加 `fact_event_id`，source_id 为 decision_id。

两条事件均为 `schema_version=1`、`environment='shadow'`、`source='ai_operator.shadow'`，`occurred_at=available_at=as_of`。
返回仅包含 `fact_event_id/decision_event_id`。

每次 `append_event` 单独提交，**两个事件之间不构成原子事务**。事实写入后、决策写入前发生异常，可仅留下事实事件；以相同输入重试时，事件库根据确定性内容身份复用事实，再补齐决策。决策已经提交但调用方尚未收到返回时，重试也复用两条事件。无需删除孤立事实或回写历史事件。

多条不同决策可引用同一事实事件；相同决策重复调用不会增加事件。恢复依赖输入保持不变和 MemoryStore 原有幂等机制；本模块不提供跨事件事务、自动重试调度或执行功能。

## 测试命令与结果

在隔离暂存目录运行，使用 `C:\Program Files\Python311\python.exe`：

```powershell
& 'C:\Program Files\Python311\python.exe' -m unittest discover -s tests_ai_operator -p test_shadow.py -v
```

- RED：实现前退出码 1，`ModuleNotFoundError: No module named 'ai_operator.shadow'`，符合计划中缺模块阶段。
- 初次 GREEN：实现后退出码 0，`Ran 19 tests in 0.127s`，`OK`。
- 已知样本字段扩展 RED：先增加构建包、重算哈希后的完整包重验两组嵌套测试；退出码 1，`Ran 22 tests in 0.146s`，`FAILED (failures=24)`。12 个新字段各在两组测试中因未抛出 ValueError 失败，`pre_close` 保留测试通过。
- 扩展 GREEN：加入字段后退出码 0，`Ran 22 tests in 0.119s`，`OK`。覆盖嵌套 dict/list 的 12 个已知字段拒绝、正确重算哈希不能绕过重验，以及 `pre_close` 可正常构建包和生成决策。
- 覆盖输入隔离、规范哈希、未来/逆序/无时区时间、固定版本字段、严格 JSON、递归后验字段、完整包重验、证据与候选动作、有效期、决策篡改、写入前拒绝、两事件来源与时点、重复记录、不同决策共享事实，以及真实 MemoryStore 第二次写入中断后的恢复。

```powershell
& 'C:\Program Files\Python311\python.exe' -m unittest discover -s tests_ai_operator -v
```

- 初次全量回归：退出码 0，`Ran 134 tests in 14.587s`，`OK (skipped=2)`。
- 字段扩展后的最新全量回归（同一命令省略 `-v`）：退出码 0，`Ran 138 tests in 13.355s`，`OK (skipped=2)`；共享目录同期增加了其他任务测试。
- 既有 `test_directory_symlink_is_rejected`、`test_root_symlink_is_rejected` 因 Windows `WinError 1314` 无符号链接创建权限跳过；其他测试通过。

本模块仅提供影子建议和来源审计，不聚合投票、不声称风控通过、不下单，不实现 M3/M4/M5。
