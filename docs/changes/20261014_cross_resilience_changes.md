# 20261014 cross 韧性改造（W3-4 泳道 · cross-resilience-dev）

泳道目标：任一信号源断供时 cross 降级可用且有人知道，而不是静默出错或整体炸掉。
涉及文件：`quant_backtest/cross_poll.py`、`quant_backtest/config.json`、
新增 `quant_backtest/test_cross_resilience.py`。market_scan.py 经核实为缠论
全市场扫描器（chanlun 源上游），本次零改动。

## 一、四源依赖现状（改前盘点）

| 源 | 加载方式 | 产物/数据锚 | 改前单源异常行为 |
|----|---------|------------|----------------|
| qpzm | 读 `reports/rps_latest.json` 的 `res[].action` | rps_latest.json `ts`（rps_resonance.run_daily 日批） | try/except 静默空集 |
| sig4 | **与 qpzm 共读同一 rps_latest.json**，取 `res[].longs` | 同上 | 同上（与 qpzm 一损俱损） |
| chanlun | SQLite `db.pool_query_recent` → 失败回退 `data/chanlun_pool.jsonl` | 池内最新 `detect_date`/`dt` | 同上 |
| 9dim | 读 `data/nine_dim_scores.json` `rows[].score≥80` | `trade_date`（日批） | 同上（文件缺失静默空集） |

关键缺陷：`build_cross` 每源 try/except 吞错 → 静默 `{}`，产物无源状态、
无降级标注；k=3 共振（W3-4 网格硬门槛）只存在于离线网格
cross_grid_backtest.py，产线聚合侧无门槛感知——任一源缺失时 k=3 数学上
不可达却无任何人知道。

## 二、改动点

1. **sig4 解耦**（cross_poll.py `_sig4_count`）：`longs` 字段缺失时从
   `res[].sigs`（四策略 0/1）现算持仓数，字段级互备，不再与 qpzm 完全
   共命运；两路皆缺该票跳过（不装作 neutral）。
2. **源状态显式化**（`build_cross` 新增 `src_state`）：每源
   ok / missing / error 三态。ok=有数据（9dim 为阈值过滤型源，空集合法，
   文件缺失才显式抛错）；missing=产物缺/空集；error=loader 抛异常
   （带 detail 截断 120 字符）。单源异常按"该源缺席"继续聚合，不崩。
3. **degraded 标注**：任一源非 ok → 产物 `degraded=True` +
   中文 `degrade_note`。
4. **k 门槛降档**（产物 `k_gate` + `topn`，K_RES=3 不自动降）：
   - normal：四源齐整，k=3；
   - degraded_k3：可用源=3，仍保持 k=3（不降门槛）；
   - blocked：可用源<3 → `topn` 产空 + note 醒目 DEGRADED
     （防降门槛放水，对应 W3-4 结论）。
5. **源健康检查**（`source_health()`）：四源 ok/stale/missing 三态 +
   数据锚日期 + 数据龄。
6. **每日汇总健康行**（`health_summary_line()`）：
   `源健康: qpzm ok(2026-10-13) | ... | 可用源 4/4`；cross_poll CLI
   `__main__` 汇总输出末尾追加健康行与 k 门槛状态行。
7. **告警推送**（`push_source_health_alert()`）：复用 cross 现有推送通道
   `runner.push_feishu`；开关 `config.json cross.source_health_alert`
   （默认关，显式开启）；`send=False` 为 dry 测试路径（不 import runner、
   不写 outbox、不真推）；全源 ok 不告警。
8. **config.json 最小增量**：新增 `"cross": {"source_health_alert": true}`。

## 三、降级行为矩阵（源状态 × 行为）

| 可用源数 | k_gate.mode | topn | degraded | 告警 | 语义 |
|---------|-------------|------|----------|------|------|
| 4（全 ok） | normal | n_hit≥3 正常出 | False | 不发 | 正常运行 |
| 3（一源 missing/error） | degraded_k3 | 仍 n_hit≥3（可用源内共振） | True+note | 该源 stale/missing 时发 | k 不降档 |
| <3（两源及以上缺） | **blocked** | **空列表** | True+DEGRADED | 发（含各源锚日期） | k=3 不可达，宁空不放水 |
| 单源 error | 同 missing 档位 | 同上 | True（state=error 带 detail） | 按 health 状态发 | 不崩、显式留痕 |

注意：`src_state`（聚合侧，ok/missing/error）与 `source_health`（巡检侧，
ok/stale/missing）为两组口径——聚合侧判"此刻有没有票"，巡检侧判"数据新不新"。

## 四、健康检查规则（source_health，日历日近似）

| 源 | 锚 | stale 阈值 | missing 判定 |
|----|----|-----------|-------------|
| qpzm | rps_latest.json `ts`（res 有 action 才算有锚） | >3 日历日（容一个周末） | 文件缺/res 空无 action |
| sig4 | 同文件 `ts`（res 有 longs 或 sigs 才算有锚） | >5 日历日（≈W3-4 口径 3 交易日） | 文件缺/无 longs 且无 sigs |
| chanlun | 池内最新 detect_date/dt | >4 日历日（扫描断档） | 池空/加载异常 |
| 9dim | nine_dim_scores.json `trade_date` | >8 日历日（周频锚口径） | 文件缺 |

## 五、回退方法

```bash
cd /e/老田的AI助手/quant_backtest
cp cross_poll.py.bak_20261014 cross_poll.py
cp config.json.bak_20261014_crosres config.json
rm test_cross_resilience.py   # 可选：新单测一并移除
```

两份备份均为改前原样字节拷贝；无数据库/计划任务/positions.json 变更。

## 六、测试结果

- `pytest test_cross_resilience.py`：**13 passed**（五条路径全覆盖：
  单源 error 降级不崩 / degraded 标注 + sig4 sigs 现算 / k 门槛不自动放水
  （degraded_k3 与 blocked 两档）/ 健康检查三态 / 开关关闭+dry 不推）。
  全合成数据，零真实源、零网络、零推送。
- `pytest test_sqlite_migration.py -k cross`：2 passed（chanlun SQLite
  路径与 JSONL 一致性回归无损）。
- `pytest test_ui_drilldown.py`：41 failed / 46 passed，与改前备份对照
  **完全一致**（失败为存量环境问题，非本次引入）。
- 生产 dry 演示：四源当前全绿 → 不告警（正确）；合成 sig4 stale +
  chanlun missing → 告警文本正确（含各源锚日期与"可用源 2<3，k=3 共振
  不可达"），send=False 未触碰 outbox。

## 七、遗留风险

1. **过期阈值用日历日近似**（qpzm 3 / sig4 5 / chanlun 4 / 9dim 8 天），
   非精确交易日判定——长假（春节 8 天+）可能误报 stale；如需精确可接
   000001_SZ 日历做交易日差。
2. **告警触发时机**：`push_source_health_alert` 是函数路径，未注册任何
   计划任务（纪律约束）；要无人值守告警需在每日 16:00 批（rps_resonance
   或门户请求）挂一行调用，本次留待产线侧决定挂载点。
3. **chanlun 锚格式混用**：SQLite 与 JSONL 路径的 dt 字段格式（带时间/
   纯日期/8 位数字）不完全一致，锚比较按字符串字典序，极端情况锚选取
   可能偏差一天，不影响三态判定主路径。
4. **qpzm 与 sig4 仍共文件**：字段级互备解决了 longs 缺失场景，但
   rps_latest.json 整文件损坏时两源齐灭（此时 blocked 机制兜底 + 告警
   显式可见，符合"降级可用且有人知道"目标；彻底分源需 sig4 独立产物，
   属 rps_resonance 侧改造）。
