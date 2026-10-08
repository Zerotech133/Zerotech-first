# quant_system 主库 23KB 疑点定性：新系统预部署空库（非数据丢失）

- 日期：2026-10-08 10:2x
- 定性：**误警闭环** —— 上午融合巡检上报「quant_system 主库 dump 仅 23KB（34 表结构完整、数据近乎空、库总 9.8MB），待确认是轻量元库还是数据丢失」
- 结论：**新系统预部署空库**，无数据丢失，无需告警

## 证据链（四条独立证据）

1. **库水位**：`pg_database_size = 9.8MB`；41 张表（pg_stat_user_tables）中 **39 张业务表 n_live_tup=0**——kline_data(264KB 结构)、stock_minute_bars、orders、positions、trade_signals、chan_buy_point、daily_nav_history 等全空
2. **仅有的数据全是今天的**：lineage_edge 21 行 + lineage.job_run 7 行（kg_daily_sync 作业记录，时间=10-08 06:24-06:27 北京，即今晨 KgDailySync 跑批，全部 ok）——正是今天 X3 血缘落地（ed619c2）后 lineage 三表开始工作写入的
3. **时间纵深**：10-07 11:14 备份的 quant_db.sql.gz = 23156 字节，10-08 06:47 = 25824 字节（.enc，+49 字节封装 ≈ 明文微增=新增 lineage schema+21 行）——**两天前就是空库**，不存在近期数据损失事件
4. **对照组**：同库主机的 zhice_db 同期 dump = 423MB（生产真库量级）——备份链路对大数据正常，23KB 是库本身空，不是 dump 缺陷

## 背景佐证

quant_system v2（量子双刀 Scanner→Screener→Signal）处于 Sprint 2 开发期（T11-T16 并行），schema 先行、业务数据灌入未开始——符合「预部署空库」形态。

## 处置

- 备份保留现状（schema 是资产；灌数后 dump 自动增长，水位正常化以 kline_data 出现数据为标志）
- 后续观察：若 Sprint 2 结束后业务表仍全 0 行，再升级为疑点
- health_check 不需为此加规则（dump 大小不入检项，避免误报源增加）
