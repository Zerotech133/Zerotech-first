# 数据仓代码审计与健康扫描修复报告（2026-10-12）

## 当前仓库基线

- TDX/Tushare 物理资产：93 个表，约 12,348.2 MB。
- TDX 同步：28/32；Tushare 同步：24/31。
- 新鲜度：9 个 OK、1 个 WARN、2 个 SNAP。
- 完整度：6 个 FAIL、1 个 WARN。
- 真实扫描入口：`scan_warehouse()`；页面消费 8091 的数据仓健康接口。

## 当前真实缺口

复扫结果已落盘到 `work/warehouse_health_after_fix.json`，当前 9 项：

- A 股样本关键字段缺失：`market_cap`、`pe`、`turnover`。
- 盘前产物超过 26 小时未刷新：本次约 88.5 小时。
- `tdx_sector_daily_daily_coverage`：51。
- `ts_margin_detail_required_nulls`：2。
- `ts_margin_detail_daily_coverage`：12,858。
- `sector_definition_key_reconciliation`：51。
- `listed_stock_latest_coverage`：WARN 14。
- `sector_minute_daily_coverage`：14。
- `sector_minute_required_nulls`：14,771。

上述行情、板块、融资和分钟缺口仍属于来源/断面问题；本次没有补零、复制前一交易日或写入生产。

## 本次确认并修复的代码 BUG

1. `_build_gaps()` 只处理市场和新闻缺口，忽略 A 股样本的 `missing_fields`，导致关键列缺失时可能显示为空缺口。
2. `scan_warehouse()` 没有执行 A 股样本字段检查，页面入口与 `scan_data_health()` 口径不一致。
3. 策略产物只展示 `age_hours`，过期文件不进入 `gaps`；已统一按 26 小时阈值告警。
4. `_scan_news()` 的可选 `created_at` schema 探针失败会吞掉已读到的总量、来源、日期和 JEV 统计；现在降级为空的 `source_times`，保留核心统计。

实现位置：`quant_backtest/data_health.py`；回归用例：`quant_backtest/test_data_health_regressions.py`。

## 验证

- 健康、完整度、分钟、读取和仓库日验证：116 passed、1 skipped。
- `python -m py_compile quant_backtest/data_health.py quant_backtest/test_data_health_regressions.py`：通过。
- 端口门禁 `python tools/check_ports.py --strict`：通过，注册表外监听 0；3 个已登记可离线服务未启动。
- Mixin 门禁：`INTEGRITY OK`。
- 导航门禁：通过。
- 全量 `quant_backtest`：1366 passed、12 skipped、46 failed；失败集中在既有门户链接/API 路径和数据仓页面旧 fixture 口径（例如 `/bt/stock` 与 `/kg/stock`、旧 TDX note 文案），与本次数据健康改动无关，已保留原现场并单独记录在 `work/audit_quant_backtest_tests.log`。

## 后续建议

1. 先为 `market_cap/pe/turnover` 指定实际来源字段与回填批次，不把健康告警当作可直接补值授权。
2. 对 51/14 板块缺键、融资 12,858 键和分钟空值继续执行逐键来源回执；`global_activation=false`、`production_write=false` 保持不变。
3. 另开 UI 兼容任务，统一 `/bt/stock` 与 `/kg/stock` 测试口径并更新数据仓页面旧 fixture，避免把页面回归与数据质量告警混在一批处理。
