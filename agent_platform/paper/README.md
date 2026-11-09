# Agent Platform Paper 账本

只做 paper（模拟）记账，不接真实交易所、不下真实订单。

## 止损规则

- `paper_config.json` 的 `stop_loss.positions.<symbol>.stop_price` 为该持仓的止损价；
  未配置时默认 `entry_price * (1 - stop_loss.default_pct)`。
- 行情 `last_price <= stop_price` 时，按当前 `last_price` 全部平仓（paper close）；
  只生成 paper ledger/marks，不触发实盘。
- 实际建仓时，止损价以建仓确认单为准；确认单优先于默认规则。

## 平仓与每日 mark

- 平仓：`apply_decision(..., action="close")` 移除持仓，`cash_weight` 记录平仓后的现金价值，
  `nav` 保持按平仓价计算后的组合净值，并写 `last_close` / ledger 的 `realized_pnl_pct`、
  `realized_pnl_nav`。
- 每日 mark：`mark_to_market(state, prices)` 只更新仍存在的持仓；空仓时直接 no-op，
  保留已实现盈亏后的 `nav`，不会把现金再按权重计一次。
- 同一天先 mark 再 close 时，close 行是事件行（`event=close`），不是第二份估值；
  daily mark 的读取方应使用 state 的最新 `nav`，不要累加 marks 行。
