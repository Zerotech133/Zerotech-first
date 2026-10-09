# 假期后首个交易日核验报告 · 20261008

- 核验时点：2026-10-09 08:30:02（PostHolidayCheck 自动执行）
- 前一快照基线：nine_dim trade_date=20260930 · 依据 post_holiday_check_template.md

## 1. ChanlunScan（05:50，mls 首批自然数据）❌

| 核验项 | 实际 |
|---|---|
| 任务 Last Run / Result | 查询失败:'NoneType' object has no attribute 'spli / — |
| pool 最新 detect_date | 2026-10-08 |
| 新信号数 / mls 覆盖 | 93 / 100% |
| 门户 mls 列 | 命中（页面 123655 字节）|

## 2. NineDimScan（06:40，K33 新代码首跑）❌

| 核验项 | 实际 |
|---|---|
| 任务 Last Run / Result | 查询失败:'NoneType' object has no attribute 'spli / — |
| 快照 trade_date | 20261008（已刷新）|
| band 覆盖 | 218/218 行 |
| n_alert / alert_th | 0 / 75 |
| 门户警报头部 | 命中（页面 42690 字节）|

## 3. K33ShadowCheck（08:00 周例行）❌（推送应已触达，按 checklist §A 处置）

| 任务 Last Run / Result | 查询失败:'NoneType' object has no attribute 'spli / — |

## 4. weak 产线（WeakTrackCheck 15:30 / WeakScan 周一 15:35）❌

| 核验项 | 实际 |
|---|---|
| WeakTrackCheck Run / Result | 查询失败:'NoneType' object has no attribute 'spli / — |
| WeakScan Run / Result | 查询失败:'NoneType' object has no attribute 'spli / — |
| 在案状态流 | 301216.SZ（数据日 2026-09-30） |
| 最新扫描档 | weak_scan_20260930.csv（focus 列有） |
| 门户案例库页 | 命中（页面 18756 字节）|
| 当日产出 | 15:30 后自动运行，不在 08:30 核验范围 |

## 5. 结论

- 判定汇总：存在异常（ChanlunScan(rc=—, mls 93/93)；NineDimScan(rc=—, band 218/218, n_alert=0)；K33ShadowCheck rc=—（应已有推送）；weak(rc=—/—, 状态流OK, 扫描OK, 门户OK)）
- 警报池首批入池：否（n_alert=0 属正常）
- mls 首批数据：是，93 条（覆盖 100%）
- weak 产线：见上表 ❌ 项
- 异常处置：见模板 §4
