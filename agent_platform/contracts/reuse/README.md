# 三系统统一契约 v1

日期：2026-10-30  
工单：`WO-LEGION-TRIPART-BFF-20261030`

## 文件

| 契约 | Schema | 示例 |
|---|---|---|
| 身份上下文 | `identity_context.schema.json` | `examples/identity_context.example.json` |
| 对话会话 | `conversation.schema.json` | `examples/conversation.example.json` |
| 选股输出 | `selection_contract.schema.json` | `examples/selection_contract.example.json` |

## 边界

- 三份 Schema 只描述**数据结构**，不包含鉴权、下单、实盘指令；
- `IdentityContext` 只描述调用者，不替代服务端 RBAC；
- `Conversation` 禁止放 token/密码/密钥；
- `SelectionContract` 的 `pool` 只是候选结果，不是交易指令；
- 任何写操作仍回智策/菜鸟各自授权链路。

## 来源映射

| 系统 | 身份 | 对话 | 选股 |
|---|---|---|---|
| 零界 | `actor_type=agent/member` | 诸葛亮/军团 | QPZM/quant_backtest |
| 智策 | 6 角色 RBAC + 权益 | 无 | StrategySpec/候选池/回测 |
| 菜鸟 | admin/member/QMT/strategy_collaborator + tenant | AiChat* + TeamTask | SelectionModel/rookie/score |

## 校验

```bash
python -m pytest portal/tests/test_zhice_bff_contracts.py -q
```
