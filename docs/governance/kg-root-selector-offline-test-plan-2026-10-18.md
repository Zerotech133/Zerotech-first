# GOV-MIN-002 数据根选择器离线测试设计（阻断状态）

> **SOURCE_ACCESS_BLOCKED · NOT_EXECUTED · 不可断言实现通过**
>
> 2026-10-18。本文件只是一份场景与验收矩阵，不是可运行测试或测试通过证据。读取 `quant_backtest/start_kg_sync.ps1` 时访问审批超时，受限内容未获取；不重试读取、不推断脚本实现。只有在用户授权提供选择器契约或受限代码片段后，才能把下述待确认预期固化为实现耦合测试。生产启动器从未执行。

## 边界与前提

- 依据：`docs/governance/next-minimal-workorders-2026-10-17.md` 第 32–39 行的 GOV-MIN-002 工单。要求覆盖“新根有效、fallback 有效、新根缺失、catalog 损坏、tables 空壳”，在临时目录验证选择结果；不得运行生产启动器、写 `quant_backtest/data`、创建 `KGDataRoot` 或修改启动脚本。
- 现状：在 `quant_backtest/tests/` 搜索相关关键字，未发现现成的 KG 根选择器测试；现有测试可见 `pytest` 的 `tmp_path` 隔离用法。当前 `python --version && python -m pytest --version` 探测到 Python 3.13.14，但 `No module named pytest`（退出码 1）。未安装依赖，未运行测试。
- 本设计不规定候选路径、catalog 格式、有效 tables 的内容、fallback 优先级、退出码或诊断文字；这些均须由获准的契约确认。矩阵中的“应”是**建议待确认验收条件**，不是关于现有实现的事实。
- 若将来获准实施，只新建或修改 `quant_backtest/tests/` 的专属离线测试文件，以测试框架临时目录构造根；通过纯函数或经授权抽取的选择器逻辑注入临时路径，不调用生产启动器，也不在生产路径建立文件。若选择器无法在不执行启动器的前提下调用，则保持阻断并申请进一步授权，而非绕过。

## 五类场景验收矩阵（全部未执行）

| 场景 | 隔离临时目录布置 | 待契约确认的选择预期 | 安全断言重点 | 当前状态 |
|---|---|---|---|---|
| 1. 新根有效 | 临时 `candidate` 放置**契约确认**的有效 catalog 与有效 tables；另置有效 `fallback`，避免只因无备选而误判 | 优先选择 candidate（前提：契约确立新根优先） | 选择结果是 candidate，且不在源树创建/修改文件 | SOURCE_ACCESS_BLOCKED；NOT_EXECUTED |
| 2. fallback 有效 | 临时 candidate 布置为**契约确认**的不合格根，fallback 完全有效；与场景 3 区分为“候选存在但校验失败” | 选择 fallback（前提：契约允许校验失败后回退） | 不把不合格 candidate 误选为有效；不写入任何根 | SOURCE_ACCESS_BLOCKED；NOT_EXECUTED |
| 3. 新根缺失 | 临时 candidate 路径不存在；fallback 完全有效 | 选择 fallback（前提：契约允许候选缺失时回退） | 不因选择动作隐式创建缺失的 candidate | SOURCE_ACCESS_BLOCKED；NOT_EXECUTED |
| 4. catalog 损坏 | candidate 的 catalog 为**契约确认**的非法内容或不可解析结构，tables 则保持有效；fallback 有效 | 拒绝 candidate；回退或显式失败取决于契约 | 损坏 catalog 不可产生成功选择；确切选择/失败结果待确认 | SOURCE_ACCESS_BLOCKED；NOT_EXECUTED |
| 5. tables 空壳 | candidate 的 catalog 有效，但 tables 仅为存在的空目录/空壳；fallback 有效 | 拒绝 candidate；回退或显式失败取决于契约 | 不能用“目录存在”代替有效内容校验；确切选择/失败结果待确认 | SOURCE_ACCESS_BLOCKED；NOT_EXECUTED |

建议补充无有效根时的失败封闭边界案例，但不计入工单所要求的五项，不在契约缺失时虚构诊断结果。实施后每一项应同时记录选择值或失败状态、临时目录的创建/清理证据、命令与真实输出；这些证据目前均不存在。

## 已知文案风险与放行条件

- “fallback 有效”可能指新根缺失后回退，也可能指新根存在但无效后回退；这里拆成场景 2 和 3 作为设计假设，不能据此声称脚本现有语义。
- “catalog 损坏”和“tables 空壳”的具体有效性判据未取得，不能直接以任意 JSON 或空目录构造后宣称回归通过。错误输出文案也未知，测试不应对猜测文本断言。
- 必须先取得用户授权的选择器契约或受限代码片段，明确根参数化入口、优先级、fallback 条件、catalog/tables 最小有效结构及失败返回；再写隔离离线测试。具备合适 Python/pytest 环境并获准运行时，才可执行测试并记录结果。在此之前 GOV-MIN-002 验收状态为**阻断，未验证**。

## 本次执行记录与回滚

- 只读查看工单第 32–39 行和现有测试惯例；受限启动器读取审批超时，未取得内容。`python --version && python -m pytest --version`：Python 3.13.14；pytest 缺失，退出码 1。
- 未运行生产启动器或测试；未写生产数据、未创建任何数据根、未修改启动脚本或 `quant_backtest/tests/`。运行态变更：无。
- 本报告唯一新增文件为 `docs/governance/kg-root-selector-offline-test-plan-2026-10-18.md`。回滚仅删除该文件，不触碰其他资源。
