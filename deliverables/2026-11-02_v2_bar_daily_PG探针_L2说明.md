# v2_bar_daily（PG 外部库）只读探针 · L2 说明（2026-11-02 15:50）

## 一、现状

- registry 256 表唯一剩余 unknown 的 PG 表；`store=null/files=0/date_col=null`（catalog desc：zhice-db 日线，多市场）；
- qualify_probe 对 pg 载体返回 `store_missing`（无只读 DSN，无法做行数/日期探针）；
- 其余 14 张 unknown 为 xtcloud 动态表（今晚四批覆盖 12 张，预计转 ok/partial）。

## 二、五要素（申请只读 DSN 探针）

1) **命令全文**：为 qualify_probe 增加 pg 只读探针（`--pg-dsn-env ZB_V2_RO_DSN`），执行 `SELECT count(*), min(trade_date), max(trade_date) FROM v2_bar_daily`（仅 read-only 会话）；不写库；
2) **影响面**：只读查询 zhice-db（localhost:5446）单表统计；不影响产线写入；耗时 <2s；
3) **不可逆点**：无（只读）；DSN 仅经环境变量注入，不落盘/不入库/不打印；
4) **替代方案**：保持 unknown（K8 已 15<40，不影响验收）；或标 planned（不推荐，掩盖真实数据）；
5) **回滚**：删除环境变量注入 + 移除 pg 分支即可（无数据变更）。

## 三、待批

- 需 DATA1/A0 确认 zhice-db 只读账号（或复用现有只读凭据）；批准后 0.5d 实现 + 测试 + 一次探针；
- 在批准前，v2_bar_daily 保留 unknown，K8 达标不受影响。