# 第四批：来源快照与交易日历适配实施计划

目标：接入真实本地日线和交易日历，记录系统实际接收时刻、源文件字节哈希、证券、价格口径及修订声明，形成可验证的当前快照和第三批标签输入。承接总体研发方案 M2/M5。用户已授权按计划并行实施，本计划作为执行台账。

架构：隔离目录 `work/ai_operator_phase4` 复制 E 盘现有模块与测试；两个代理各负责独立模块，主代理负责 CLI、联调与交付。复用严格 JSON/UTC 契约，Python 3.11、unittest；Parquet 读取仅按需导入既有 pandas。不修改 SQLite DDL，不启动同步器、服务或训练。

## 设计与边界

- 接收时间由 `contracts.utc_now()` 在读完文件后生成；公开接口没有 received_at/available_at 参数。既有文件 mtime、同步完成标记均不能作为历史可用时间。
- 文件先读字节，再从同一字节解析并计算 SHA256，避免两次读取导致哈希与内容错配；拒绝符号链接和非普通文件，单文件上限 32 MiB。
- 本批的时间证明仅为本进程当前读取；内容哈希不是签名，也不认证供应商。价格口径与修订为调用者声明，输出 `supplier_verification='unverified'`、`production_eligible=false`。标签适配源状态固定 unknown，保持 audit_only；不提供调用者把 unknown 改 trusted 的开关。
- 支持日线，收盘契约固定中国沪深交易所 15:00 +08:00；不适配分钟线、盘中 bars、港股或不同收盘制度。
- 日历必须包含覆盖区间每个自然日的开闭市状态；不从缺失行情推导休市，不按周一至周五生成日历。相同重复行合并，冲突重复拒绝；SSE/SZSE 显式选取。
- 既有三目录是 Codex 增量接入边界；不占端口、不注册共享表/图标签、不写生产行情或账本。接入前在多会话正式协定追加本会话注册，仅记既有范围，不重新分配别人的所有权。

## 冻结接口

### A：行情来源快照

文件所有权：`ai_operator/provenance.py`、`tests_ai_operator/test_provenance.py`、`reports/provenance.md`。

`capture_snapshot(path, *, source_id, code, price_basis, revision, format='json') -> dict`。

- source_id/revision 为非空字符串；code 为六位代码加 `.SH`/`.SZ`，price_basis 为 raw/qfq_snapshot/hfq_snapshot。format 仅 json/ashare_parquet。
- JSON 原文严格对象 `{bars:[{session,open,high,low,close,volume}]}`，日期 YYYY-MM-DD；拒绝重复键、未知字段、非有限数、布尔数值。Parquet 使用实际存量字段 ts_code/trade_date/open/high/low/close/volume（或 vol），选择证券；额外列保留在原始字节哈希中，但不进特征。不能混淆不同证券，空选取拒绝。
- 最多 5000 根；唯一日期升序，允许源乱序后排序，重复日期拒绝；OHLC 有限正数并满足 low<=open/close<=high，volume 有限非负；停牌零量不删除。未来收盘晚于当前读取时间拒绝。
- 输出固定字段 `{schema_version:1,kind:'daily_local_capture',code,period:'1d',source_id,price_basis,revision,received_at,source_file:{path,sha256,size_bytes,format},bars:[{session,open,high,low,close,volume,close_at,available_at}],historical_availability:'unknown',supplier_verification:'unverified',production_eligible:false,snapshot_id}`。available_at 全部等于 received_at；snapshot_id 哈希完整对象除自身。
- `validate_snapshot(snapshot) -> dict`：严格重验完整字段、语义和内容哈希；深拷贝规范化，拒绝篡改/后退可用时间。注意这只是完整性验证，不能阻止外部重新伪造整个文件并计算新哈希。
- 测试先 RED 后 GREEN：实际接收、不允许回填、OHLC/零量、未来数据、同批时间、Parquet证券、输入冻结、篡改及非法 JSON。报告命令和结果。

### B：交易日历快照

文件所有权：`ai_operator/trading_calendar.py`、`tests_ai_operator/test_trading_calendar.py`、`reports/trading_calendar.md`。

`capture_calendar(paths, *, exchange, source_id, revision, coverage_start, coverage_end, format='json') -> dict`。

- paths 为 1 至 32 个唯一普通文件，单文件上限32MiB；format 仅 json/tushare_parquet；SSE/SZSE 显式，覆盖日期为 YYYY-MM-DD，1<=覆盖天数<=5000。
- JSON 固定 `{rows:[{exchange,cal_date,is_open}]}`；cal_date YYYY-MM-DD 或 YYYYMMDD；is_open 为整数0/1，不能bool。Parquet 实际字段 exchange/cal_date/is_open，额外 pretrade_date 不用于推导。
- 只选 exchange 和覆盖区间，严格校验匹配行；相同重复行合并，冲突拒绝；覆盖每一天，缺任一天拒绝。允许全部休市区间，输出空 sessions，但第三批标签适配需至少一个开市日。
- 输出固定 `{schema_version:1,kind:'exchange_calendar_capture',exchange,source_id,revision,coverage_start,coverage_end,received_at,source_files:[{path,sha256,size_bytes,format}],days:[{session,is_open}],calendar:{version,sessions:[{session,close_at}]},supplier_verification:'unverified',production_eligible:false,capture_id}`。calendar.version 由 exchange、覆盖、days 的语义哈希组成，不受文件顺序/相同分片重复影响；capture_id 哈希完整对象除自身。close_at 保留 +08:00 本地日期（与 outcomes 日期契约一致）。source_files 按规范绝对路径排序。
- `validate_calendar_capture(capture) -> dict`：完整字段和哈希、逐日覆盖、sessions/day对应、版本重算、来源信息/时间严格校验；复制而不修改输入。当前日历快照不证明过去决策时已知该日历版本。
- 测试先 RED 后 GREEN：春节/国庆显式闭市、缺日、停牌与交易所日历分离、重复/冲突、时区、哈希、全闭市、无网络读取。

### C：CLI 与真实只读验收（主代理）

文件所有权：`ai_operator/cli.py`、`ai_operator/source_inputs.py`、`tests_ai_operator/test_phase4_cli.py`、`tests_ai_operator/test_source_inputs.py`、`acceptance.py`、outputs 文档。

`snapshot_for_outcomes(snapshot, calendar_capture) -> dict`：调用 A/B 验证，证券交易所匹配且全部行情在日历开市日中，收盘瞬间匹配；返回 `{code,period,bars,calendar,source}`，bars为第三批固定四字段，source.source_hash 绑定双方 capture ID，source_id包含来源/修订，price_basis沿用，availability_status固定unknown。源供应商/历史接收没有审核，不产生可信成熟标签。

CLI `capture-source --input`（A 参数 path及关键字）；`capture-calendar --input`（B参数）；`source-inputs --input`（snapshot/calendar_capture）。沿用8MiB请求限额和JSON错误，拒绝用户注入时间字段；均只读源文件，只输出JSON，不需要DB。

预检补充：`label_captured_decision(snapshot, calendar_capture, pack, decision, evaluated_at, horizons=None)` 和 CLI `captured-outcome --input` 组合入口。先校验双方快照，再核对 pack.code/period 与来源完全一致（不得混用000001.SH和000001.SZ），evaluated_at>=双方实际received_at，最后调用既有label_decision核验完整决策，返回第三批同型结果；source固定unknown。来源ID采用规范JSON，哈希采用带键对象，避免分隔符拼接碰撞。当前日历只证明当前读取，不证明过去决策时已知该日历。

独立审查补充：组合入口拒绝中国本地日期早于日历覆盖起点的决策，避免把区间首日猜成跨年决策的下一交易日；晚于覆盖末尾时不猜参考日期，未知来源仍只返回audit_only。行情捕获和validator在文件访问前拒绝UNC/设备命名空间路径，不发起共享网络读取。

真实验收：上证指数300根 `data/ashare/000001_SH.parquet` 与现存 `data/tushare/trade_cal/part_*.parquet`；记录前后SHA，当前接收时间、覆盖结果、衔接 outcomes 的 audit_only。闭市缺口和缺行情分开；若真实日期与日历冲突则报告，不能删数据让验收通过。

## 执行清单

- [x] 只读盘点实际接口/Parquet字段，建立隔离副本和冻结契约。
- [x] A：先测后实现，模块独立回归；UNC和父目录junction补测修复。
- [x] B：先测后实现，模块独立回归；真实国庆闭市区间读取验证。
- [x] C：CLI/来源标签适配、真实文件只读端到端验收；早于日历覆盖的决策补测修复。
- [x] 独立代码审查、问题修复、全量回归；冻结版256项，252通过、4权限跳过，复审无阻断。
- [x] 具体改动可审阅后取得E盘三个目录和正式协定单文件写权限，18:18追加登记后接入；目标目录256项回归及真实四命令复验通过，交付进度已更新。
