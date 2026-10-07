# 第二批：时点结构回放与影子决策审计实施计划

目标：在首批事件库上交付可逐根回放的本地算法适配器、严格时点事实包与可追溯影子建议。规格来源为 outputs/2026-10-07-云端语料与本地多AI最优研发方案.md 第 5、6 节。

架构：新增纯函数模块，通过现有 MemoryStore.append_event 保存事实与决策；不增加 SQLite 表、不改已有策略。所有开发先在本目录隔离暂存，测试与审查后仅接入 ai_operator、tests_ai_operator、docs/ai_operator。

技术：Python 3.11 标准库、unittest；沿用已有 chanlun_bi.analyze，无网络行情拉取。

## 约束与审查重点

- 简体中文文档、注释与错误；英文接口名。保留已有用户修改，不提交、不启动交易、模型或定时任务。
- 输入为一个证券一个周期的升序已收盘 K 线；每根必须显式 close_at、available_at。可用时间未知的历史数据不自动转成可决策行情。
- 不宣称现有算法是标准缠论；末端笔和简化线段会修订，confirmed_at 只表示首次观测该版本，不是永不重绘承诺。
- 测试重点：未来后缀不改已有回放、延迟可用与乱序拒绝、输入不被算法修改、撤销历史保留、篡改事实包和时点不一致拒绝。
- 不实施 M3 训练、M4 完整组合风控、M5 模拟成交；缺原始 ZIP 的规则 Schema、四组结构算法对照仍待核验。

## 冻结接口和文件归属

### A 逐根回放（代理）

文件：ai_operator/replay.py；tests_ai_operator/test_replay.py；reports/replay.md。

`replay_bars(bars: list[dict], analyzer=None, algorithm_id: str = 'local-chanlun-bi-v1') -> dict`

bar 字段固定为 dt/o/h/l/c/v/close_at/available_at；dt 为非空唯一字符串，close_at/available_at 为带时区 ISO，available_at >= close_at；两种时间均严格递增，OHLC 有限正数且 l <= min(o,c) <= max(o,c) <= h，v 有限非负。未知字段拒绝以免前向标签混入。最多 2000 根，超限显式报错。每次只调用 analyzer(prefix)，输入深拷贝；默认延迟导入 chanlun_bi.analyze 并传当时 closes。

结果包含 schema_version=1、algorithm_id、bar_count、snapshots；每个快照为 as_of、bar_dt、visible_count、structures（算法完整 JSON 输出）、changes。对 fractals/bis/segments/pivots/signals 跟踪规范内容哈希，输出 added/withdrawn（包含 kind、version_id、pivot_at、confirmed_at、observed_at、status、data）。同一版本首次观测时间保留，消失后再现为新出现记录；撤销不回写旧快照。status='observed'，末端/所有未证明稳定性结构不冒充最终确认。确认时间取本根 available_at，pivot_at 取结构 dt/e_dt/d。不支持未知输出形状时必须报错。

- [x] 写行为测试，运行缺模块 RED。
- [x] 实现严格输入、前缀隔离、版本变化与默认适配器；默认算法另校验 dt 字典序严格递增。
- [x] 运行 GREEN，报告方法、复杂度 O(N²) 和确认语义。

### B 事实与影子审计（代理）

文件：ai_operator/shadow.py；tests_ai_operator/test_shadow.py；reports/shadow.md。

`build_fact_pack(code, period, as_of, facts: list[dict], versions: dict) -> dict`

facts 每项仅 id/source/occurred_at/available_at/data，其中 data 为对象，ID 唯一、source 非空、时间显式且 occurred_at <= available_at <= as_of；拒绝未来事实而不是悄悄滤除。versions 固定且必须非空字符串 data/algorithm/knowledge/rules。证券/周期非空。冻结 JSON 深拷贝，计算 fact_hash（规范完整包除 fact_hash）。包包含 schema_version=1/environment='shadow'/code/period/as_of/facts/versions/fact_hash，不允许嵌入后验标签字段 post/forward_return/label/outcome（递归检查）。

`make_shadow_decision(pack: dict, action: str, evidence_ids: list[str], rationale: str, valid_until: str) -> dict`

先严格重验完整包和哈希；动作仅 observe/candidate_buy/candidate_sell/abstain；证据必须指向包内且唯一，候选买卖至少一项；理由非空；valid_until > as_of；输出 schema_version/environment/code/period/as_of/fact_hash/versions/action/evidence_ids/rationale/valid_until/confidence=null/execution_allowed=false/decision_id（除 ID 的内容哈希）。不聚合投票、不声称风控通过。

`record_shadow_decision(store, pack, decision) -> dict`

重建合法决策并核对所有字段，拒绝篡改；先通过 append_event 保存 shadow.fact_pack，再保存 shadow.decision（payload 包含 fact_event_id），事件 occurred_at=available_at=as_of，source='ai_operator.shadow'，source_id 为事实哈希/决策 ID。两次调用返回 fact_event_id/decision_event_id；幂等，崩溃时可能仅有事实，重试可恢复。不声称跨事件原子事务。

- [x] 写行为测试，运行 RED。
- [x] 实现时点/证据/版本/哈希校验及幂等记忆写入。
- [x] 运行 GREEN，报告失败恢复与明确非执行能力。
- [x] 按本地样本实际字段补充递归拒绝 r5/r10/r20/r60、ret5/ret10/ret20/ret60、mature5/mature20、post10/post_hm10。此字段检查防止已知误入，不能替代来源时点审核。

### C 联调与交付（主代理）

文件：ai_operator/cli.py；tests_ai_operator/test_phase2_cli.py；验收脚本、outputs 交付文档。

- [x] 增加 replay --input（JSON bar 列表）只读 JSON 输出；shadow-decision --input（pack/action/evidence_ids/rationale/valid_until）--db（已存在库）显式落审计；fact-pack --input（code/period/as_of/facts/versions）只读生成。
- [x] 先写 CLI RED，完成 GREEN，全量首批与本批首轮回归。
- [x] 独立审查代码、修复并复审；本地算法与存量样本离线前缀回放、审计重跑去重、源哈希核对。空库隐式初始化的审查问题经 RED/GREEN 修复，复审无阻断项。
- [x] 接入 E 盘限定目录并重跑测试；交付命令、验收结果、剩余门槛。目标目录 139 项，137 通过、2 权限跳过；120 根本地存量样本工程验收通过，6 个源码/测试文件哈希与审查版一致。

用户已授权按计划并行实施，本计划作为执行台账，不再重复索取设计确认。
