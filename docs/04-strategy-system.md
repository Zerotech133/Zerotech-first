# 04 · 策略系统

## 1. 目标

让所有策略（内置策略、外部研究引擎策略、未来自研策略）走同一条：

```
研究/回测 → StrategySpec → 注册 → 审批 → 版本化 → 执行 → 复盘 → 学习 → 晋升/降级
```

## 2. StrategySpec

```python
class StrategySpec(BaseModel):
    spec_version: int
    market: str
    universe: Universe
    factors: list[FactorSpec]
    rules: list[RuleSpec]
    params: dict
    risk: RiskSpec
    execution: ExecutionSpec
    backtest_perf: dict
    research_ref: str | None
```

字段说明：

- `universe`：标的域，支持 codes / index / industry / filter
- `factors`：因子定义
- `rules`：进出场规则，支持 enter/exit
- `risk`：止损、止盈、仓位、暴露上限
- `execution`：频率、方向、滑点模型、交易时段
- `backtest_perf`：回测绩效快照
- `research_ref`：研究溯源

## 3. StrategyExecutor

```python
class StrategyExecutor(Protocol):
    def generate_candidates(
        self,
        ctx: MarketContext,
        spec: StrategySpec,
        trade_date: date,
    ) -> list[CandidateSignal]: ...
```

实现：

- `ExternalResearchExecutor`：基于外部研究引擎产出的 StrategySpec 执行
- `BuiltinExecutor`：把 CN 内置策略包成统一接口
- `CustomPythonExecutor`：允许受控 Python 插件
- `MLExecutor`：未来模型推理

## 4. 算子注册表

能力门从固定白名单升级为插件注册表：

```python
OPERATOR_REGISTRY = {
    "momentum": MomentumFactor,
    "ma_cross": MaCrossFactor,
    "rsi": RsiFactor,
    "fundamental_score": FundamentalFactor,
    "custom_alpha": CustomAlphaFactor,
}
```

注册时校验：

- 因子是否已注册
- 参数是否合法
- 市场是否支持
- 回测绩效是否达标

## 5. 策略生命周期

```
DRAFT → PENDING_REVIEW → STAGING → ACTIVE → DISABLED
              ↓
          REJECTED
```

- 回测不达标 → REJECTED
- 人工审批 → STAGING
- 模拟盘观察 → ACTIVE
- 绩效衰减 → DISABLED

## 6. 与外部研究引擎的关系

- 外部研究引擎仍是研究/回测能力提供方。
- 研究成果通过 `StrategySpec` 物化。
- 日常运行不依赖外部研究引擎。
- 数据回流按需触发。
- 对外统一表现为“智策研究引擎”，不暴露外部系统品牌。

## 7. 统一策略注册接口

```python
class StrategyRegistrar:
    def submit(self, spec: StrategySpec, perf: BacktestPerf) -> Registration: ...
    def approve(self, registration_id: int, approver: User) -> StrategyVersion: ...
    def reject(self, registration_id: int, reason: str) -> None: ...
    def promote(self, version_id: int) -> None: ...
    def demote(self, version_id: int, reason: str) -> None: ...
```
