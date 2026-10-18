# ASSEMBLY_DRAFT.md — 经典四策略 S2 Agent 装配条目（草稿，未注册）

RC-1 第一批 S2（s2-classic 工位）。按
`agent_platform/engine/strategy_discovery.py::load_manifest` 装配清单口径
（`module` / `class` / `strategy_id` 三件套）起草；身份属性按
`MANIFEST_GUIDE.md` §4。

**状态红线：本文件只是草稿。四条目须待 S3 回放对账过 + S4 影子期满
（replay_reconcile 一致率达标、shadow_collect 校准完成）才可进正式装配。
正式注册进 agent_platform 装配清单属 S5 授权步骤，本工位不执行。**

## 1. 装配清单三件套（strategy_discovery 口径）

```json
[
  {
    "module": "strategy_layer.onboarding.agents.classic_agents",
    "class": "MaCrossAgent",
    "strategy_id": "ma_cross"
  },
  {
    "module": "strategy_layer.onboarding.agents.classic_agents",
    "class": "RsiReversalAgent",
    "strategy_id": "rsi_reversal"
  },
  {
    "module": "strategy_layer.onboarding.agents.classic_agents",
    "class": "BollBreakoutAgent",
    "strategy_id": "boll_breakout"
  },
  {
    "module": "strategy_layer.onboarding.agents.classic_agents",
    "class": "TrendFollowAgent",
    "strategy_id": "trend_follow"
  }
]
```

- `strategy_id` 与 `strategies.REGISTRY` 键名一致（MANIFEST_GUIDE §1：
  经典策略不取双身份）；manifests/ 侧 `<strategy_id>.manifest.json` 的
  strategy_id 同名一一对应（见 §3 对照表）。
- `class` 均为 `ClassicStrategyAgent` 的非抽象子类（无参构造可实例化，
  满足 `load_manifest` 的 `agent_class()` 实例化 + 实例 ID 与清单声明
  一致校验）。`ClassicStrategyAgent` 基类本身**不进**装配清单（无独立
  strategy_id）。
- `module` 导入前提：quant_backtest 根在 sys.path（S5 装配时由清单所在
  目录/宿主进程决定，`load_manifest` 会把清单目录插 sys.path[0]，若清单
  落在仓库根可直接解析）；`classic_agents.py` 对 agent_platform 契约
  （strategy_registry）有路径兜底加载，不依赖 engine 入 sys.path。

## 2. 身份属性（StrategyRegistry._validate_manifest 校验面）

| strategy_id | class | version | family | capability_manifest | lifecycle_state | stance |
|---|---|---|---|---|---|---|
| ma_cross | MaCrossAgent | 1.0.0 | classic | (daily_bars, long_only, frame_position) | experimental | bull |
| rsi_reversal | RsiReversalAgent | 1.0.0 | classic | 同上 | experimental | bull |
| boll_breakout | BollBreakoutAgent | 1.0.0 | classic | 同上 | experimental | bull |
| trend_follow | TrendFollowAgent | 1.0.0 | classic | 同上 | experimental | bull |

- lifecycle_state 收编影子期取 "experimental"（scheduler 白名单
  experimental/active 可跑影子），S4 期满转正后升 "active" 并升 version。
- 改本体逻辑（strategies.py 侧或窗口纪律）必须升 version——
  `bind_authorization` 校验注册/授权 version 一致。

## 3. 与 strategies/manifests/ 运行清单的对应关系

| strategy_id（两套清单同名） | 装配条目（本文件 §1） | 运行条目（manifests/） |
|---|---|---|
| ma_cross | module/class 如上 | `ma_cross.manifest.json`：6 字段白名单（version 1.0.0 / allowed_data bars.daily / budget 5s·1·256KiB / modes BACKTEST·REPLAY·SHADOW / permissions propose·read_context），`_provenance` 聚合时剥离 |
| rsi_reversal | 同上形态 | `rsi_reversal.manifest.json`，同口径 |
| boll_breakout | 同上形态 | `boll_breakout.manifest.json`，同口径 |
| trend_follow | 同上形态 | `trend_follow.manifest.json`，同口径 |

注意（BATCH1_DOSIER 已列）：`strategy_discovery` 装配清单与
`strategy_config.load_config` 运行清单一**是两套清单**，S5 装配时两条目
都要落、strategy_id 必须对齐（`shadow_strategy_runner._bind_config` 校验
registry/config ID 集合一致）。本批 manifests/ 只覆盖运行清单口径。

## 4. S5 装配前置检查单（本工位移交，未执行）

1. S3 回放对账：`replay_reconcile` 用与回测**等长预热窗**切片——rsi/boll
   的窗口重算预热差是主要一致率风险点（各 Agent 的 min_window 公式见
   `classic_agents.py` 模块 docstring；窗口不足 Agent fail-closed 出中性
   Verdict，对账脚本须把 fail-closed 帧单独归类统计，不算"信号不一致"）。
2. S4 影子期满：shadow_collect 收齐前向收益，回填 extras.s4_calibrate
   校准分数/置信（当前模板基准 buy=70/sell=30/hold=0~70、conf 0.5/0.3）。
3. rsi/boll 残余风险核实：max_hold_upper=60 的持有期上界是契约声明，
   S3 对账需确认实盘回放窗口内无超上界的遗留持有（有则调大上界并升
   version 重跑对账）。
4. max_context_bytes 下限：bars 逐根 JSON 序列化，rsi/boll 需 ≥ min_window
   （75/80 根）+ 日期字段，manifests 现 256KiB 预算充裕；若改分钟线需重估。
