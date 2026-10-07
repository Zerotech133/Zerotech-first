# 第四批运行说明

本批新增当前来源快照与交易日历校验，依托已有 `ai_operator`。命令只读源文件，将 JSON 写到标准输出。调用者负责保存输出供复验；快照会记录本次实际读取时间，每次重新接收产生不同快照 ID。

## 命令

在 `E:\老田的AI助手\quant_backtest` 中运行：

```powershell
& 'C:\Program Files\Python311\python.exe' -B -m ai_operator capture-source --input '<行情请求.json>'
& 'C:\Program Files\Python311\python.exe' -B -m ai_operator capture-calendar --input '<日历请求.json>'
& 'C:\Program Files\Python311\python.exe' -B -m ai_operator source-inputs --input '<双快照请求.json>'
& 'C:\Program Files\Python311\python.exe' -B -m ai_operator captured-outcome --input '<快照与决策请求.json>'
```

真实验收的完整请求与结果已保存在本交付目录的 `第四批真实文件验收/`，文件名对应上述命令。保存的接收时刻表示那次读取，而非原始历史行情的到达时刻。请求文件大小上限8MiB，源文件单个32MiB；成功退出0，契约或文件错误退出2并输出JSON错误。

### 行情请求

```json
{
  "path": "E:/老田的AI助手/quant_backtest/data/ashare/000001_SH.parquet",
  "source_id": "local-ashare-index-file",
  "code": "000001.SH",
  "price_basis": "raw",
  "revision": "existing-file-current-capture-unverified",
  "format": "ashare_parquet"
}
```

`format` 缺省为json，JSON源结构为 `{bars:[{session,open,high,low,close,volume}]}`，日期 YYYY-MM-DD。Parquet匹配完整ts_code，读取trade_date/OHLC/volume或vol，额外字段不进入特征，但仍被源文件字节哈希绑定。保留零成交量，不通过删停牌日简化标签窗口。

`price_basis` 仅 raw/qfq_snapshot/hfq_snapshot，`revision` 是显式声明。系统不会通过文件名推断价格口径，也不核验复权因子或供应商修订档案。

### 日历请求

```json
{
  "paths": ["E:/老田的AI助手/quant_backtest/data/tushare/trade_cal/part_0000.parquet"],
  "exchange": "SSE",
  "source_id": "local-tushare-calendar-files",
  "revision": "existing-file-current-capture-unverified",
  "coverage_start": "2025-01-02",
  "coverage_end": "2026-04-01",
  "format": "tushare_parquet"
}
```

接受1至32个唯一文件。JSON缺省源为 `{rows:[{exchange,cal_date,is_open}]}`；cal_date可YYYY-MM-DD或YYYYMMDD，is_open为整数0/1。覆盖区间每个自然日必须明确记录开闭市；相同重复合并，冲突或缺日拒绝。当前11个存量分片哈希相同，这次覆盖区间455天、300开市日；不能据此认为已核验官方日历。

日历语义版本绑定交易所、覆盖范围、逐日开闭状态；来源修订、文件哈希和实际接收时间另由capture_id绑定。全部日线收盘契约固定15:00 +08:00。

### 组合请求

`source-inputs` 接受 `{snapshot:<行情结果>,calendar_capture:<日历结果>}`，输出code/period/bars/calendar/source。它会校验证券交易所与日历、行情日期与开市日期，以及完整内容哈希。

推荐通过 `captured-outcome` 接入第三批标签：请求为 `{snapshot,calendar_capture,pack,decision,evaluated_at,horizons}`，horizons可省略（默认H5/H20）。pack和decision沿用第二批完整契约，证券代码和周期必须与来源完全相同，评估时刻不得早于双方接收。该入口还会重建决策验证ID，避免把上证指数000001.SH价格用于000001.SZ股票。

决策的中国本地日期不得早于日历覆盖起点；超过覆盖末尾的决策不猜参考日，参考日/标签终点为null。按未来交易日观察的窗口仍需完整日历和行情，而未知来源始终保持audit_only。

来源供应商与历史可用时间尚未审核，因此适配后的 `availability_status` 固定unknown，标签为audit_only，价格目标为null；没有人工修改trusted的命令开关。不可把输出当作训练标签、成交收益或正式回测结果。

## 可验证范围

- 接收时刻由系统生成，用户请求不能回填received_at/available_at；所有日线共享本批接收时刻。
- 原始字节一次读取后解析并计算SHA256；捕获代码校验文件为普通本地文件、路径链接、大小与数据字段。
- 离线validator校验快照完整性和语义，不再次访问源文件，因此源文件后续变化不会影响已保存快照复验。
- SHA256不是签名；外部能重造整包并计算新哈希。本批不认证供应商真实性、历史可用性、复权版本或过去已知的日历版本。
- 真实来源验收覆盖300根存量日线；当前接收的历史数据可以用于接收之后的审计决策，不能作为接收之前的可用事实。

## 下一步门槛

补齐明确的数据源审核记录、官方日历对照、复权因子/修订证据及持久接收记录后，才能设计受控的审核放行入口与真实成熟标签。原始缠论开发包和视频库ZIP仍待取得核验，标准结构对照和生产规则激活仍受此限制。

本批按需运行，不新增常驻服务或共享数据库写入，不执行训练或交易。
