# 接口 JSON Schema 索引（IFS F1）

> 权威说明：`docs/standards/2026-11-03_F1接口Schema接线说明_v1.md` · `docs/standards/2026-11-03_接口对接规范_IFS_v1.md`
> 登记表：`docs/standards/INTERFACE_REGISTRY_v1.yaml`（`interfaces[*].schema_file` 指向本目录）
> 全部为 **draft-07 JSON Schema**，描述单接口 `data` 资源本体；外层统一信封见 `interface_envelope_v1.schema.json` / `interface_error_v1.schema.json`。

## 单接口 Schema（8 份，F1 交付）

| # | schema_version | 文件 | 登记接口（interface_id） | 关键约束 |
|---|---|---|---|---|
| 1 | `acad1_pool/v2` | `acad1_pool_v2.schema.json` | `acad1.pool.get` | `source.source_mode`（pg_official/projection）、`pool_version`、`published_at` 显式；禁止执行字段 |
| 2 | `d1_research/v1` | `d1_research_v1.schema.json` | `d1.research.get` | `readonly=true`、`source` 真源/新鲜度、items 只读 |
| 3 | `d1_decision/v1` | `d1_decision_v1.schema.json` | `d1.decision.create` / `d1.decision.list` | `decision_id` 双格式 anyOf、`paper_only=true`、禁止执行字段 |
| 4 | `d1_handoff/v2` | `d1_handoff_v2.schema.json` | `d1.handoff.create` / `d1.handoff.list` | `decision_id` 必填、`watch_plans[]` 必填、`not_a_trade=true`、**执行字段一律拒绝** |
| 5 | `watch_plan/v1` | `watch_plan_v1.schema.json` | `watch.plan.create` / `d1.watch_plans.list` | armed/triggered 状态、conditions；`paper_only=true` |
| 5b | `trigger_event/v1` | `watch_plan_v1.schema.json#/$defs/trigger_event`（合并定义） | `watch.trigger.create` | `trigger_id`、`watch_plan_id`→`trigger_id` 链、只产生待确认 |
| 6 | `trading_bot_confirmation/v2` | `trading_bot_confirmation_v2.schema.json` | `confirm.create` / `confirm.list` | **四 ID 必填**（decision/handoff/watch_plan/trigger）、`not_a_trade=true` |
| 7 | `paper_receipt/v1` | `paper_receipt_v1.schema.json` | `paper.execution.record` | `trade_id` + 四 ID、hash 链（prev_hash/hash）、`trading_permitted=false`、`mode=paper` |
| 8 | `outcome/v1` | `outcome_v1.schema.json` | `outcome.get` | `outcome_id`、`trade_id` 可空但必须显式、行情缺失 null（不伪造） |

## 统一信封 / 错误（F0，已存在）

| schema_version | 文件 | 说明 |
|---|---|---|
| `interface_envelope.v1` | `interface_envelope_v1.schema.json` | 成功信封（ok/schema_version/request_id/source/data/warnings） |
| `interface_error.v1` | `interface_error_v1.schema.json` | 错误信封（error.code/message/retryable/details，21 错误码） |

## 校验与使用

```powershell
# 契约测试（离线，不依赖真实服务）
python -X utf8 -m pytest tools/tests/test_interface_schema_contract.py -q

# 单份 schema 语法 + 内嵌 examples 自校验
python -X utf8 -c "import json;from jsonschema import Draft7Validator as V;s=json.load(open('docs/standards/schemas/d1_handoff_v2.schema.json',encoding='utf-8'));V.check_schema(s);[V(s).validate(e) for e in s.get('examples',[])];print('OK')"
```

```python
from agent_platform.contracts import ifs

payload = ifs.wrap_ok(
    "d1.research.get", "d1_research/v1", data,
    source_of_truth="agent_platform/data/pool_review/",
    source_mode="aggregate",
)
assert ifs.validate_envelope(payload) == []
```

- 每份 schema 的 `examples[0]` 为合法样例（契约测试逐份校验）；非法反例由测试构造（缺字段/错枚举/执行字段）。
- 版本变更规则：破坏性变更 → v2 + 90 天双版本（IFS-09）；`schema_version` 与 registry `interfaces[*].schema` 同批更新。
