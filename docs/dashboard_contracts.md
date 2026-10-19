# 驾驶舱数据契约总账（dashboard_contracts v1.0）

> 2026-10-19 07:55 · E1 工位产物。松耦合定案（见 E 批计划）：视图层只认本契约，不认文件路径/库表。
> 每个端点：用途 / 权威形状（07:53 实测钉死）/ SLA / 陈旧行为 / owner。
> 改端点先改本账 + E9 快照测试，再动代码。

## 0. 总则

- 单一数据源：一个业务事实一个权威端点；跨页展示必须同源。
- 陈旧行为：超 SLA 的数据前端降灰态（不假绿），横幅类进断链琥珀条。
- 跨源取数标准降级链：同源代理 → 直连 8791（CORS）→ 内嵌快照（显著标注「静态兜底」+ as_of）。
- 时间格式：8091 系 `YYYY-MM-DD HH:MM:SS`，8791 系 ISO `YYYY-MM-DDTHH:MM:SS`，交易日 `YYYYMMDD`。

## 1. 策源（regime）—— 市场环境事实唯一权威

**GET 8791 `/api/regime`** · owner=quant_backtest/regime_center.py · SLA=快照日≤3 交易日
```json
{ "ok": true, "ts": "…", 
  "regime": { "available": true, "stale": false, "detail": "risk_off · 上证 3716.28 …",
    "source": "data/regime_center/snapshot_20261016.json", "elapsed_ms": 0, "gaps": [],
    "value": { "trade_date": "20261016", "regime": "risk_off|risk_on|neutral",
      "gates": {"ma60_above":false,"slope_up":false,"dif_positive":false},
      "risk_off_reasons": ["close_below_ma60_2d(streak=11)","ma60_slope_down","dif_and_dea_both_negative"],
      "close": 3716.2842, "chg_pct": 0.24, "ma60": 3882.2469, "macd_dif": -46.8847,
      "up_ratio": 0.608, "amount_total_yi": 15690.6 } },
  "snapshot": { …完整快照（指数/广度/criteria/version）… } }
```
- available=false 时 value=null、detail 说明原因；前端倾向格降灰。
- 语义权威：risk_off/risk_on/neutral 判据=watching-standard-capability-v1.0 §5.1。
- **注意**：8091 `/api/brief` 的 `fin.verdict` 是另一口径（fin 裁决），见 §6 双轨条款。

## 2. 六环（sixring）—— 收编流水线运营态势

**GET 8791 `/api/sixring`** · owner=quant_backtest/strategy_layer/dashboard_api.py · SLA=ts 当日
```json
{ "ok": true, "ts": "…", "rings": { "<ring>": { "available": true|false,
  "value": {}, "detail": "… · NNms", "source": "文件路径", "notes": "可选", "elapsed_ms": 0 } } }
```
- 六环固定键：`pool`(缠论池) `watch`(投委会在盯) `auth`(授权账本) `risk`(RiskGuard) `trade`(模拟成交) `review`(复盘)。
- 产出节奏差异大（pool=每日扫描、trade=每日 mark、review=不定期）——`detail` 必须带批次日期，前端如实展示不判陈旧（陈旧判定只对"环整体 available=false"）。

## 3. 信号聚合（signals）—— 当日信号计划权威

**GET 8791 `/api/signals?date=YYYYMMDD`** · owner=SignalAggregator · SLA=交易日 08:30 后当日可查
```json
{ "ok": true, "trade_date": "20261019", "count": 234,
  "signals": [ { "trade_date": "…", "symbol": "603057.SH", "action": "buy|sell|watch",
    "score": 100.0, "confidence": 0.8, "contributions": {"kg_boost": {…}},
    "divergence": false, "rationale": "[kg_boost] …" } ] }
```

## 4. 风控（risk）—— RiskGuard 账本

**GET 8791 `/api/risk`** · owner=risk_bridge.py → riskguard_ledger · SLA=规则 ts≤7 日
```json
{ "ok": true, "risk": { "available": true, "ledger": "C:\\…\\ledger.jsonl",
  "rules": { "R1": { "pass": 682, "block": 8646, "ts": "…", "last_score": 0.2246,
    "market_gate": { "score": 0.2246, "action": "shadow_block|pass", "reason": "…" } },
    "R2": {…}, "R3": {…}, "R4": {…} } } }
```
- market_gate.action=shadow_block → 允许与 regime(risk_off) 并列为"双判据同向"横幅措辞。

## 5. 健康探针（两套，语义不同不得混用）

**GET 8791 `/api/health`**（量化业务链探针）
```json
{ "ok": true, "ts": "…", "checks": [ { "port": 8091, "name": "portal", "ok": true,
  "latency_ms": 2, "err": null }, … ] }   // 8091/8098/8001/11434/8791
```
**GET 8091 `/api/health`**（门户基础设施聚合）
```json
{ "ok": true, "ts": "…", "services": [ { "name": "portal api", "port": 8091, "ok": true,
  "ms": 2, "pid": 31644 }, … ] }
```
- 断链横幅取 8791 checks（业务链）；os_entry 系基础设施灯取 8091 services。ok=false 即断链候选。

## 6. 门户聚合（8091 系）

**GET `/api/brief`** · owner=portal api_unified · SLA=ts 当日
```json
{ "ts": "…", "fin": { "verdict": "中性（按个股信号执行）", "prob": 44, "buy": 1, "latest": "20261016" },
  "intel": { "today": 322, "hit": 52, "bull": 65, "bear": 82, "neutral": 84 },
  "kg": { "stale": 18, "catalog_stale": false, "catalog_age_days": 0, "ts": "…" } }
```
- **双轨条款（E4）**：`fin.verdict`（fin 裁决，基于最近收盘 latest）与 §1 regime（策源三态）语义不同——前者"怎么执行"，后者"能不能做多"。前端同屏展示时必须各带来源标签；倾向格以 regime 为先（判据可溯源），verdict 兜底。禁止把两者互相改写。
- intel.today 与 8791 signals.count 语义不同（情报条数 vs 策略信号条数），禁混用一格。

**GET `/api/strategies`** · owner=portal/strategies.json（策略资产注册表） · SLA=ts 当日
```json
{ "ts": "…", "meta": { "what": "策略资产注册表——单一数据源", "status_def": {…}, "promotion": {…} },
  "strategies": [ { "id": "chanlun", "name": "…", "tagline": "…", "status": "live|shadow|tool|paused",
    "status_note": "…", "task": "计划任务名|空", "href": "/bt-…", "badge": "信号|评分|…",
    "since": "YYYY-MM-DD", "metrics": [ {"k": "…", "v": "…"} ], "ext": "可选外链",
    "pipeline": { "stage": "S1..S5", "stage_cn": "…", "as_of": "YYYYMMDD", "note": "…" } } ] }
```
- `pipeline` 为 **E5 新增可选字段**：由收编侧 sync 工具回写，人工不手填；缺省=未入收编流水线，前端不渲染该徽标。

**GET `/api/alerts`** · owner=portal 各产线快照 · SLA=ts 当日（盘后批次）
```json
{ "ts": "…", "alerts": [ { "name": "盘前倾向", "value": "中性", "sub": "…", "href": "/premarket",
  "delta": -2|null, "six": [ {"k":"外围","v":-2}, … ], "overall": -2 } ] }
```
- **E8 预留**：行级 `level: "market|eng|strategy"` 字段（缺省=eng）。

**GET `/api/paper_status`** · owner=k_stock paper · SLA=交易日盘后
```json
{ "ok": true, "as_of": "2026-10-15", "account_id": "main",
  "latest": { "date": "…", "total": 1007837.1, "cash": 140457.1, "market_value": 867380.0,
    "pos_pct": 0.8606, "pending": 279, "verify_ok": true, "cum_pct": 0.78 }, "curve": [ … ] }
```

**GET `/api/overview`** · owner=portal · SLA=30s 缓存
```json
{ "ts": "…", "containers": {…}, "tasks": [ {"name":"ChanlunScan","state":"Disabled|Ready","last":…,"lastrun":"…","next":"…"} ],
  "backups": {…}, "quotes": { "ok": true, "items": [ { "name","code","price","pct","dec" } ], "updated": "…", "alert_threshold": 5 },
  "backtest": { "mode": "scan|single", … } }
```
- tasks[].state=Disabled → 前端基建面板红标（断链自愈信号源）。

## 7. nginx 代理路由（同源面）

| 路由 | 上游 | 状态 |
|---|---|---|
| /probe/quant /probe/zhice /probe/quantdinger | 外部工具 | ✅ 在产 |
| /probe/dash | 8791/health | ✅ 在产 |
| /api/regime /api/sixring | 8791 同名 | **E2 待补**（现 404，前端走降级链） |

## 8. SLA 速查

| 端点 | SLA | 超时行为 |
|---|---|---|
| /api/regime | 快照≤3 交易日 | stale=true → 倾向格灰 |
| /api/sixring | ts 当日 | 环灰 |
| /api/signals | 交易日当日 | 日期标注陈旧 |
| /api/risk | 规则 ts≤7 日 | 风控格灰 |
| /api/health ×2 | 实时 | 横幅断链条 |
| /api/brief · alerts · strategies | 当日 | 格灰+「快照未更新」 |
| /api/paper_status | 交易日盘后 | 模拟仓格保留最后值+as_of |
