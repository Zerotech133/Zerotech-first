# W5 影子积累期：环境闸门三档虚拟对照采集（gate-shadow-dev）— 2026-10-14

v1.1 计划第十三节落地：qpzm/cross/双刀三方互证认定「40% 宽度闸门不够紧」，
统一观察带 **45%（下限）~ 50%（候选）**。W5 影子期做**三档虚拟对照，只记录
不改行为**，W8 末统一评审是否切换。本变更只新增采集与落档，零策略行为变更、
零计划任务注册、零飞书推送。

## 一、交付物

| 文件 | 说明 |
|---|---|
| `quant_backtest/gate_shadow_collect.py` | 新增：三档虚拟对照日采集器（θ∈{40,45,50}） |
| `quant_backtest/test_gate_shadow_collect.py` | 新增：单测 10 例全绿（合成数据、离线） |
| `quant_backtest/qpzm_daily_batch.py` | 挂载（+.bak_20261014 备份） |
| `quant_backtest/market_scan.py` | 挂载（+.bak_20261014 备份） |
| `quant_backtest/data/gate_shadow_3tier.jsonl` | 影子档（首采 9 行） |
| `portal/qd_scanner.py` | **未改动**（双刀侧直接读 qd_scan_cache 产物） |

## 二、采集格式（data/gate_shadow_3tier.jsonl，每行一 JSON）

```json
{
  "ts": "2026-10-14 12:16:45",
  "date": "20261013",            // 信号日，策略各自产物自然日期
  "strategy": "qd|qpzm|cross",
  "theta": 40,                    // 40 / 45 / 50（宽度档，百分比）
  "n_total": 25,                  // 当日信号集数量（闸门前口径）
  "n_blocked": 25,                // θ 档虚拟拦截数量
  "blocked_codes": ["..."],       // 被拦代码清单（升序）
  "breadth": 0.326,               // 当日宽度 0-1（如可得）
  "med20": -0.0431,               // 20 日涨幅中位（仅 qpzm 快照可得）
  "upfrac": null,                 // 占位：产线快照未落 upfrac，恒 null
  "source_status": "ok|missing",  // 源产物缺失标 missing，不阻塞
  "src": "产物路径（missing 时为缺失原因摘要）",
  "fwd": null                     // 前向收益占位，由周报脚本回填
}
```

### 三策略拦截口径（只虚拟宽度档，其余条件与产线一致）

- **qd（双刀）**：`breadth < θ/100` 拦（与 qd_scanner.gate_would_block 同式）；
  信号集 = qd_scan_cache.signals_now；宽度优先取单笔注记（qd_scanner 已
  落 breadth/gate_would_block=40 档），注记缺失回退宽度序列最新锚
  （qd 锚语义 T-1，与 lake_breadth breadth_ref 一致）。
- **qpzm（青萍）**：`breadth < θ/100 或 med20 < -3%` 拦（med20 条件即产线
  env 闸门第二腿，保持不动）；信号集 = rps_latest.json res 中 CN 买入/
  观察/回避（环境闸门）——**闸门前口径**，闸门否决单也在 n_total 内。
- **cross（缠论）**：`breadth < θ/100` 拦（与 cross_grid envgate 同式）；
  信号集 = 最新 sig_events_market_v3_*.json 的 meta.d1 当日事件。

### 幂等与写入

- 幂等键 `date|strategy|theta|source_status`：同日同策略同档同状态不重复；
  missing 行先落、源转 ok 后允许补落 ok 行（W8 评审以 ok 行为准）。
- 原子追加：O_APPEND 单次 write + fsync + .lock 互斥 + 锁内全文件复核
  （判例：linkage_collector 同式）。

## 三、挂载点（注记式 try/except，采集失败不影响产线）

| 挂载点 | 采集范围 | 触发链 |
|---|---|---|
| `qpzm_daily_batch.archive_daily_batch()` 尾部 | qd + qpzm | rps_resonance.run_daily 每日链尾部（此时 14:40 双刀产物已落盘） |
| `market_scan.scan_market()` 产物落盘后 | cross | 全市场扫描产出口（历史扫描挂日批时顺带刷新） |

不注册计划任务、不改 qd_scanner、不碰 positions.json 与生产数据。
挂载处双重防护：调用方 try/except + collect_run 自身吞异常只落
missing/日志（`C:/Temp/gate_shadow_collect.log`）。

## 四、当日首采数字（2026-10-14，9 行）

市场背景：宽度 32.6%（20261013 锚）、med20=-4.31%，三档全在观察带下方，
故当日三档全部「全拦」——低宽度日三档无区分度，区分度出现在宽度
40%~50% 区间的交易日，属预期现象，影子期逐日积累。

| 策略 | 信号日 | n_total | θ40 拦 | θ45 拦 | θ50 拦 | breadth | med20 |
|---|---|---|---|---|---|---|---|
| qd | 20261012 | 3 | 3 | 3 | 3 | 0.3264（序列锚回退） | - |
| qpzm | 20261013 | 25（含闸门否决单） | 25 | 25 | 25 | 0.326 | -0.0431 |
| cross | 20261013 | 45 | 45 | 45 | 45 | 0.3264（序列锚） | - |

被拦清单示例：qd = 300890.SZ / 301015.SZ / 600605.SH（详见 jsonl）。

验证：单测 10/10 OK；真实采集二跑 +0（幂等）；qpzm 挂载链用临时
batches 路径冒烟通过（产线归档文件零改动）。

## 五、回退方法

1. 删影子档与锁：`quant_backtest/data/gate_shadow_3tier.jsonl`（+ `.lock` 若残留）
2. 移除挂载（两处注记块，搜 `W5 影子采集`）：
   - `qpzm_daily_batch.py` archive_daily_batch 尾部 try 块
   - `market_scan.py` scan_market 落盘后 try 块
   - 或直接还原备份：`qpzm_daily_batch.py.bak_20261014`、`market_scan.py.bak_20261014`
3. 采集器与单测可留可删（无挂载即不运行）。

## 六、遗留风险

1. **qd 宽度锚错位**：本次 qd 缓存滞后（ts=2026-10-12）而序列锚已到
   20261013，出现「锚晚于信号日」的一次性倒挂；正常日批下锚=T-1 ≤ 信号日。
   评审读数时按 src 字段的锚日期对齐。
2. **低宽度日零区分度**：宽度 <40% 的日子三档全拦，影子数字对「45 vs 50」
   的判别力集中在宽度 40%~50% 区间的交易日；W8 样本量取决于该区间天数。
3. **upfrac 恒空**：rps_latest 快照未落 upfrac（仅参与 env 打分），如 W8
   评审需要，需先在 market_env 落快照（另行变更）。
4. **cross 时效依赖 market_scan 触发**：cross 行只在全市场扫描产物刷新时
   更新，若日批长期不跑 market_scan，cross 会连续 missing（有 missing 行
   留痕，不静默）。
5. **前向收益 fwd 为占位**：由周报脚本回填（另行变更），影子期 jsonl 中
   恒为 null。
