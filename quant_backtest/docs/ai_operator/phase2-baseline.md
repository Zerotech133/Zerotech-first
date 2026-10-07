# 第二批只读基线盘点与验收资料

盘点日期：2026-10-07。约束：仅只读本地源文件和数据，不调用 `load_kline`、生产数据加载、网络、数据库、模型或交易；仅写本报告。本报告不宣称已完成主代理的逐根回放联调。

## 1. 实际模块与算法语义

原任务中的 `fractal.py`、`signal.py` 实际不存在，对应文件是 `chanlun_fractal.py`、`chanlun_signal.py`；数据根为 `E:\老田的AI助手\quant_backtest\data`，不是项目顶层 `data`。

- `chanlun_fractal.py:21` 的 `_merge_inclusion` 只消费高低点，首段默认向上，包含时修改处理后末根高低点，保留组内首根 `dt`。`find_fractals`（47、50 行）需要处理后三根，以中根为定位，使用严格高低点比较；因此输出 `dt` 不是观测/确认时刻。
- `chanlun_bi.py:26` 的 `find_bis` 以处理后索引差至少 4 连接异性分型；同型更极值会修改最近笔终点（40–42 行），跨度不足的分型弃用。笔末端明确可能延伸修订。
- `find_segments`（62 行）从最早前三笔区间共同重叠开始；反向笔破前反向极值形成候选，后续同向笔不创新极值才结束于历史极值。末段在数据截断时仍输出当前极值（112 行后的分支），不是稳定终结保证。
- `find_pivots`（127 行）用三笔重叠确定 `[zd,zg]`，须 `zg > zd`；后笔仍与区间接触则延伸，完全离开才结束。中枢 `e_dt/n_bi` 会随前缀变化。
- 默认 `chanlun_bi.analyze(bars, closes=None)`（252 行）输出 `merged_n/fractals/top_n/bottom_n/bis/segments/pivots/signals`。传等长 `closes` 后使用 MACD 面积衰减或笔幅衰减判据，不传则退化为笔幅衰减。
- 同一项目还有不同信号入口：`chanlun_signal.analyze`（222–226 行）调用 `chanlun_bi` 后**覆盖** `signals`；其一类基于线段终结和同向笔幅衰减，二/三类要求回调笔非末笔（151、179 行），并附影子指标。其信号为 `type/dt/price/...`；默认 `chanlun_bi` 信号为 `d/p/t/sub`。两者不能共用一个未区分的算法版本名。

上述为该项目实现口径，不据代码注释的“标准”措辞宣称完整标准缠论认证。

## 2. 首次观测与稳定确认的界限

结构点 `dt/s_dt/e_dt/d` 指向价格结构位置。分型需要右邻 K 线；笔需要分型形成；线段终结、回踩信号又依赖后续笔。中间位置时间不能冒充当时已知时间。

本批 `confirmed_at` 仅能表示：给定算法版本和当时可见输入前缀，某个完整内容版本首次出现的观测时间。`status='observed'` 才符合代码证据。不能据“其后存在一笔/一段”推论所有未来后缀下永不修订。应保存版本内容哈希、首次出现、撤销及再出现记录，保留旧快照；不可用当前全量分析结果回写过去的“已确认”结构。

包含处理会改变当前末根处理后 K 线，末端分型也可能改变；同性极值明确能修订最近笔，中枢长度和末段也能变化。稳定确认需要另外明确、可证明的算法契约，本次没有这类证据。

## 3. 现有回测的风险边界

- `backtest.py:18–23` 用 `close.pct_change()` 和 `signal.shift(1)`；一根延迟只处理仓位时序，不能清除上游信号由全量未来结构产生的前视。该接口没有 `available_at`，不会检查信号是否当时可知。
- `backtest.py:42–49` 的逐笔清单按仓位变化当日收盘记录开仓，但收益计算已计入从前日收盘至该日收盘的收益。清单成交基准和净值区间口径需区别，不能直接作为真实可成交证据。
- 进一步只读检查 `chanlun_backtest.py`：头部明确“理论价非可成交价、仅作纸面信号质量验证”；39 行 `run_backtest` 在结构点日期/理论极值价成交，并按其后第 5/10/20/60 根收盘计算标签。
- `chanlun_backtest.py:146` 的 `eval_samples` 使用截断重放寻找最早检出，较理论点回测更接近可观察口径，但会调用 `chanlun_data.load_kline`（171 行左右）。本次**未执行**此函数。它用检出窗末根收盘当成交价仍不构成实时供应商到达和真实成交保证。
- `chanlun_scan.py` 头部声称检出日收盘可成交。晚于收盘的数据到达/分析若仍按同收盘价入场，须额外验证成交假设；这不是本次已验证的收益结论。

## 4. 可复用本地存量 OHLC 样本

候选文件：`E:\老田的AI助手\quant_backtest\data\ashare\000001_SH.parquet`。

只读解析 runtime 为 `C:\Program Files\Python311\python.exe`，Python `3.11.9`、64 位 AMD64；实际用 `python -B -c` 和 `pyarrow.parquet.read_table`，`pyarrow==20.0.0`。没有通过项目加载器解析，也没有写 parquet、fixture 或缓存。

- 300 行；`ts_code=000001.SH`；首日 `20250102`，末日 `20260401`。
- 字段：`ts_code/trade_date/close/open/high/low/volume/amount/pre_close/pct_chg`。Parquet `created_by=parquet-cpp-arrow version 20.0.0`，元数据键只有 `pandas/ARROW:schema`；没有供应商签名或历史可用时刻。
- 映射：`dt=trade_date, o=open, h=high, l=low, c=close, v=volume`；保留源序且日期唯一严格递增。
- 300 行 OHLCV 检查均通过：四价有限正数，`l <= min(o,c) <= max(o,c) <= h`，量有限非负；无 `close_at/available_at`。
- 首根：`o=3347.9392, h=3351.722, l=3242.0865, c=3262.5607, v=561375199.0`。
- 末根：`o=3939.5658, h=3955.943, l=3929.9213, c=3948.5521, v=564611598.0`。
- 文件 SHA256：`2AEDD9B3C2922058831932ACE36393BD405EC86948B49EE8A6D081828E660D48`。
- 规范映射后 300 根 JSON SHA256：`da2745466e85965e0b8744e363ba9ae3e8d816024ed69582843886f92b2f08a7`。序列化为 UTF-8，`json.dumps(bars, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)`。

### 来源和日期核查的实际证据

300 日无周末。自然日相邻间隔分布：1 日 235 次，3 日 57 次，9 日 2 次，4 日 2 次，6/5/11 日各 1 次。不是每个自然日连续生成的序列。

针对明确列出的 2025/2026 元旦、春节、2025 清明/劳动节/端午/国庆休市日期集合，命中 0。该抽查不是完整交易所日历认证；尚不能证明每个日期均真实交易日或行情值与官方逐日一致。

源线索：`chanlun_scan.py:4–5` 注释称 ashare 由 `rps_resonance` 增量同步，`tushare daily` 不复权；`rps_resonance.py:331` 附近请求 daily，350–357 行去重排序并写 `code.replace('.', '_') + '.parquet'`。这证明项目存在对应写入路径，不能证明该特定 300 行文件就是该函数产物。未查到该文件专属采集日志/manifest，也未联网逐日核验。应一直称为**本地存量样本**，可验证工程结构行为，暂不能作真实市场来源或策略收益证据。

## 5. pre/post 与样本标签盘点

已对 `quant_backtest/data` 下 JSON/JSONL 按 `"pre"\s*:|"post"\s*:` 搜索，唯一命中为 `data/distill/distill_stats.json:109–110` 中 `mean_ret5_neg` 的两个数值（`post=0.14, pre=-0.47`），不是 K 线数组。这一搜索不涵盖所有 parquet 嵌套列和 ZIP 内容，不能据此宣称全盘不存在 pre/post K 线。

逐项解析样本证据：

| 文件 | 读到的内容与边界 | SHA256 |
| --- | --- | --- |
| `data/chanlun_samples.jsonl` | 完整解析 495 行；首项字段 `market/code/name/period/type/dt/price/r5/r10/r20/r60/first_seen/mt/mt_hit/vv/reg`；全文件无 `o` 或 `bars` 字段、无 `available_at`。首项 `first_seen=2026-09-26T08:39:36Z` 是样本首次登记信息，不能证明每根行情的历史到达。 | `AE5CF19462F5372071FFF641C328280C6C9A21D6977ABD29B30AFACD9BC7F4D0` |
| `data/chanlun_pool.jsonl` | 首项字段含 `dt/detect_date/lag/entry_px/first_seen/trend` 和影子指标，没有 pre/post K 线。仅解析首项，未声称全量行字段一致。 | `AFD28F22D040460F99C6C5DE33768C64FD9DEE76C6F4B94B3DE0571F1F35DF33` |
| `data/chanlun_samples_pool_import.jsonl` | 首项字段包含 `r5/r10/r20/r60/first_seen` 等标签，没有 pre/post。 | `C1AD429AFF171D76B099322A4A1FE5DABA189B73B8C4688C50823D369FC44846` |
| `data/nine_dim_replay.jsonl`、`data/nine_dim_replay_v2arch.jsonl` | 首项是 `anchor/code/entry_px/score/cov/v/r5/r20/mature5/mature20/adj/bench5/bench20` 等摘要，没有 pre/post。 | 未计算 |
| `data/chanlun_gray_backfill.jsonl`、`data/chanlun_trend_backfill.jsonl` | 首项是结构指标/趋势摘要，没有 pre/post。 | 未计算 |

`r5/r10/r20/r60`、收益、成熟度等是后验标签，不得直接送入当时的事实包或 replay bar。上述样本库可用于独立后验评估/索引线索，不能代替已收盘 K 线输入。

## 6. 推荐工程验收口径

主代理明确的 harness 方案可采用上述文件的前 60/120 根 OHLC，记录源文件哈希、规范输入哈希与算法版本。在完全离线内存处理中，逐条以真实 `utc_now` 标记**本次 harness 收到这根存量 K 线的时刻**作为 `available_at`；必须实测，若钟表值重复/倒退应等待或拒绝，不能递增一微秒补造。所有 snapshot `as_of` 因而属于当前采集/处理时期。

`close_at` 可以按 CN 日线 `trade_date + 15:00:00+08:00` 推导，但须明确记录其为交易时段约定的推导值，不宣称来自供应商历史原始时间戳。真实供应商到达时间未知，不得设成旧日收盘或按固定滞后补造。

这样验收的是“当前收到一段存量数据后的前缀隔离、结构观测/撤销、哈希和 shadow 审计”，不是历史决策可得性，不用于历史收益、可成交率或投资结论。也可将完整源快照作为当前一项 shadow 事实，`available_at` 标记当前采集时刻，关联路径/哈希/推导时间口径；不得混入收益标签。

默认 lazy 导入路径为 `chanlun_bi → chanlun_fractal → chanlun_data`。`chanlun_data` 顶层会只读配置，`load_kline` 可访问远程 API/数据库。主代理已允许仅只读导入且不输出配置，以 `-B` 禁止源码目录字节码写入；验收不得调用加载函数。本盘点只读其代码，未导入或执行该加载器。

## 7. 算法文件基线哈希

以下 SHA256 在盘点时读取：

| 文件（相对 quant_backtest） | SHA256 |
| --- | --- |
| `chanlun_bi.py` | `540942001CEADA27AD5EAE3392EED047CF5C6D0C2062A6979DE8D2EBEA8AC2A3` |
| `chanlun_fractal.py` | `7D71CE9DF9BC5492FEF497B80E1918F1AC39DD8051E2601CBD3078F40565EB04` |
| `chanlun_signal.py` | `762072CB9CA060378D216AEA1E9000AB25A2044FC05AA2775A1107418FD98901` |
| `backtest.py` | `FEF9C34C405A5E8372AC2E28A8FBDE9203AB1BED1BE04F9684D64BCB501EF6FB` |
| `chanlun_backtest.py` | `3C35B812A00A40AE0D20CAB009873DBF3F27F5BD68395D45FF9AB9180CD04EC6` |

parquet 文件哈希在日期/数值检查后重读仍一致。未修改 E 盘任何文件，未输出配置内容或 token；未检验样本真实市场来源、稳定结构证明或收益有效性。
