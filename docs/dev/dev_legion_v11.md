# 研发军团作战屏 v1.1 · 真实量化数据接入（使用/口径）

- **状态**：已实现（2026-10-29，`WO-V11-IMPL-20261029`）；2026-10-29 08:35 live 验证通过；后续重启以 OPS 为准
- **原则**：只读聚合、字段白名单、k≥5 掩码；**不出**标的/名称/价格/目标价/理由/owner 明细
- **依据**：`deliverables/2026-10-29_作战屏v1.1_立项书_v1.md` + 白名单/兼容方案

## 一、API 可选字段（只增不改）
`quant_cards[i].real`：
```json
{"as_of":"...","source":"aggregate(strategy_runs)","window_days":7,
 "metrics":{"strategies":4,"runs_window":26,"last_run_at":"...",
            "stance":{"buy":9,"sell":7,"hold":10}}}
```
`risk_cards[i].real`：
```json
{"as_of":"...","source":"aggregate(shadow_ledger,backtests)",
 "metrics":{"shadow":{"last_date":"...","rows":60,"status":{...},
   "degraded_rate":0.05,"latency_p50_s":106.0},
  "backtests":{"total":23,"status":{...},"go_no_go":{...},"last_created":"..."}}}
```
顶层 `extensions:["v1.1_quant_real","v1.1_risk_real"]`；无真源时不出现 `real`/extensions，页面显示"未接"。

## 二、页面
量化卡/风控卡底部显示：`真实 · <as_of> · 策略N · 近7天M · 多X/空Y/中Z`（或影子/回测摘要）；无数据不显示该行。

## 三、掩码与边界
- 任一桶计数 `0<n<5` → 显示 `"<5"`；
- 代码不读取 `symbol/name/price_at_decision/target_range/rationale/owner`；
- 缓存/限额：读末尾 ≤5000 行；单请求只读；
- 缺文件/坏 JSON → `None`（API 不加字段），不 500。

## 四、验收命令
```powershell
python -X utf8 -m pytest agent_platform/tests/test_dev_legion_real.py `
  agent_platform/tests/test_dev_legion_api.py `
  agent_platform/tests/test_dev_legion_ui.py -q
python -X utf8 tools/dev_legion_smoke.py
```
重启后 live 验证：`/api/dev/legion` 含 `extensions`/`real`，页面卡出现"真实 ·"行；v1 字段不变。

## 五、回滚
删除/禁用 `dev_legion_quant_real.py`/`dev_legion_risk_real.py` 的接入即退回 v1；无 DB/端口/迁移。
