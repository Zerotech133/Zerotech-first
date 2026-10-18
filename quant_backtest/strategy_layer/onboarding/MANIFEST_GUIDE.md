# Manifest 填写指南（S1 → 收编注册模板）

- 工位：pipeline-fw（RC-1 模板四件套 · S1 部分）
- 日期：2026-10-18
- 模板文件：`quant_backtest/strategy_layer/onboarding/manifest_template.json`
- 字段口径权威来源（**不另造平行机制**）：
  - `agent_platform/engine/strategy_config.py`——运行清单严格 6 字段校验
    （`load_config` / `_manifest`：多一字段、少一字段都拒）；
  - `agent_platform/engine/strategy_registry.py`——策略身份/版本/能力/生命周期
    （`StrategyAgent` 类属性 + `StrategyRegistry._validate_manifest`）；
  - `agent_platform/engine/strategy_discovery.py`——装配清单三件套
    （`module` / `class` / `strategy_id`）。

## 0. 三张注册面的关系（收编一个策略要登记什么）

| 注册面 | 文件/机制 | 本模板覆盖方式 |
|---|---|---|
| 运行清单（数据/预算/模式/权限） | `strategy_config.py::load_config`，聚合文件 `strategy_config.json` | `manifest_template.json` 直接可过校验器 |
| 装配清单（module/class/strategy_id） | `strategy_discovery.py::load_manifest` | S2 Agent 类落位后按 §3 填 |
| 身份/能力/生命周期 | `StrategyAgent` 类属性（version/family/capability_manifest/lifecycle_state/stance） | S2 Agent 类按 §4 填 |

## 1. 严格 6 字段逐一说明（填写规则 + 经典四策略示例值）

模板顶层只允许 `{"strategies": [...]}`；每个数组元素只允许以下 6 个字段。

### strategy_id
- 规则：非空字符串，全局唯一（注册表重复注册=冲突拒绝；配置清单重复声明=拒绝）。
- 建议：与 `strategies.REGISTRY` 键名一致（经典策略）或与既有 strategy_layer 侧 id 一致（已收编策略），避免同一策略双身份。
- 示例：`ma_cross` / `rsi_reversal` / `boll_breakout` / `trend_follow`。

### version
- 规则：非空字符串。注册身份与运行授权绑定校验 version 一致（`bind_authorization`），改本体逻辑必须升版本。
- 示例：四策略均为 `1.0.0`（首批收编，本体未改动）。

### allowed_data
- 规则：非空、无重复的字符串数组。校验器不限定枚举，本项目约定小写命名空间令牌，按**最小授权**填。
- 示例（四策略只用日线 close）：`["bars.daily"]`；
  多级别策略（如 chanlun_li122）参考 batch1 口径：`["bars.1m","bars.5m","bars.30m","bars.daily"]`。

### resource_budget
- 规则：恰好三键，多键少键都拒：
  - `timeout_s`：有限正数（int/float）。经典逐帧纯函数很轻，填 `5.0`；
  - `max_concurrency`：正整数。单进程逐帧计算填 `1`；
  - `max_context_bytes`：正整数。**事件帧语义策略（rsi_reversal / boll_breakout 有 ffill 状态）**
    需要 context 覆盖最近一次反向触发，预算给大些（示例 `524288`）；
    无状态策略（ma_cross / trend_follow）给 `262144` 即可。
- 示例见模板内四策略条目。

### allowed_modes
- 规则：字符串数组，取值域 `ALLOWED_MODES = {BACKTEST, REPLAY, SHADOW, SIM_MARKET}`。
- 收编纪律：**未过 G3 门不得申请 SIM_MARKET**。首批一律 `["BACKTEST","REPLAY","SHADOW"]`：
  - BACKTEST：S3 对账期使用；REPLAY：对账通过后回放验证；SHADOW：S4 影子期；
  - 转正评估（三态判据，见 shadow_report_template.md）通过后再议升级。

### permissions
- 规则：必须且只能为 `["propose", "read_context"]`（校验器硬编码，禁止
  `order` / `account` / `ledger_write`）。无裁量空间，照抄即可。
- 红线：策略只产建议（Verdict/Signal），不持有任何执行能力。

## 2. `_provenance` 登记字段（可选，聚合时剥离）

batch1-manifest 已落 6 份 `<strategy_id>.manifest.json`（含 `_provenance` 溯源块）。
约定：`_provenance` 是 S1 收编档案字段，**不在 6 字段白名单内**，聚合进
`strategy_config.json` 供 `load_config` 消费前必须剥离。本模板的
`manifest_template.json` 是"剥离态"（纯 6 字段），可直接整体作为聚合清单片段。

## 3. 装配清单三件套（S2 后填）

`strategy_discovery.load_manifest` 的清单项格式：

```json
{
  "module": "strategy_layer.onboarding.classic_adapter",
  "class": "ClassicStrategyAdapter",
  "strategy_id": "ma_cross"
}
```

- `module`：Agent 类可导入路径（收编零主链 diff → 适配类一律放
  `strategy_layer/onboarding/` 或各策略 adapter 文件，不改既有模块）；
- `class`：必须是非抽象 `StrategyAgent` 子类，实例 `strategy_id` 与清单声明一致；
- 经典四策略共用 `ClassicStrategyAdapter` 一个类、按 strategy_name 参数化，
  装配时每个策略一条清单项（class 同名、strategy_id 不同，配合 §4 的子类化或
  实例化工厂——以 S2 落位时的实际形态为准）。

## 4. StrategyAgent 身份属性（S2 后填）

`StrategyRegistry._validate_manifest` 校验的类属性：

| 属性 | 规则 | 经典四策略示例值 |
|---|---|---|
| `strategy_id` | 非空、全局唯一 | 同 §1 |
| `version` | 非空字符串 | `"1.0.0"` |
| `family` | 非空字符串 | `"classic"`（默认 legacy，收编批显式标注） |
| `capability_manifest` | 字符串序列 | `("daily_bars", "long_only", "frame_position")` |
| `lifecycle_state` | 枚举 experimental/active/deprecated/retired | 收编影子期建议 `"experimental"`，转正后升 `"active"` |
| `stance` | 枚举 bull/bear/neutral | `"bull"`（四策略仅做多） |

## 5. 自检命令

```bash
# 模板过既有校验器（真调 load_config，非复制粘贴式校验）
"C:/Program Files/Python311/python.exe" -m pytest \
  quant_backtest/strategy_layer/tests/test_onboarding.py -q -k manifest
```
