# scanner-dev W1-2 改动留痕（2026-10-14）

执行者：scanner-dev。解释器：`"C:/Program Files/Python311/python.exe"`。
范围纪律：只动下述文件；未推送飞书、未注册/修改计划任务、未跑全市场扫描、未动 data/ 生产数据（仅只读）。

---

## 任务A：chanlun 后复权成笔

### 背景与目标
`chanlun_scan.py` 吃 `data/ashare/*.parquet`（tushare daily 不复权），除权缺口产生假分型污染笔/中枢/三类买卖点。本次在**加载层**加后复权开关，信号层（chanlun_signal/chanlun_bi/chanlun_fractal）零改动——它们只吃加载后的 bars。

### 改动文件清单

| 文件 | 改动 | 备份 |
|---|---|---|
| `quant_backtest/chanlun_scan.py` | 新增后复权层（开关/因子缓存/缩放/链接续/降级留痕） | `chanlun_scan.py.bak_20261014` |
| `quant_backtest/config.json` | chanlun 节新增 `"use_hfq": true` | `config.json.bak_20261014` |
| `quant_backtest/test_chanlun_hfq.py` | 新增回归测试（26 项断言） | 新文件 |

### 改动点（chanlun_scan.py）

1. **开关 `_use_hfq()`**：读 `config.json chanlun.use_hfq`，键缺失/读取失败默认 `False`（旧行为，过渡期零变化）；config 已显式置 `true`。**一键回退 = config 该键改 false 或删除，无需动代码**。惰性读一次（`_HFQ_CFG` 全局），测试可显式覆盖。
2. **因子源 `_adj_map()`**：湖 `data/tdx/ts_adj_factor/`（adj_factor_sync 每日 18:10 增量，后复权累积因子，历史值固定；实测 done=true 420 万行/5705 票，加载 2.5s，per-code ndarray 内存 ~百MB 级）。表未就绪/读取失败 → `{}` 全局降级。
3. **核心 `_hfq_scale(bars, code)`**：OHLC 与 pre_close 按因子归一，**基准=全序列最后一根因子（最新日=1）**——与 tushare hfq 等比（比值口径一致），最新根价格=不复权真实价。头部/尾部/内部无因子日以 **pre_close 链接续（K254 口径：f(t)=f(t-1)×c(t-1)/pre_close(t)，头部逆向回补）**，比率出界 0.05~20 或 pc 缺失 → 继承前根因子（不修代错修）。
4. **挂载点**：
   - `_bars_from_parquet`（chanlun_scan.py:127 区域）：CN 市场 + 开关开启时缩放；US/HK 不动（无因子源且文件名非 ts_code 形态）。
   - `_tdx_topup`：TDX 兜底补尾 bar 以正向链接续因子（归一基准=原末根），接缝不撕开。
5. **降级留痕**：该票因子整段缺失 → 原样不复权 + 记入 `HFQ_FALLBACK`；`scan_all` 末尾落盘 `data/chanlun_hfq_fallback.json`（原子写）并打印。
6. **评估口径标注**：`pool_eval` note 随开关动态生成——开启时「后复权口径（ts_adj_factor·最新日=1基准）[·降级不复权N只]」，关闭时保持原文「不复权口径（跨除权剔除）」。后复权开启时 `_exdiv_between` 对已复权序列自然不触发（pc 同步缩放，除权缺口消失），跨除权样本由复权收益**修正**而非剔除——与 adj_factor_sync.py 设计意图（"除权窗口从剔除升级为修正"）一致；降级票仍是不复权，adj_flag 照旧剔除。

### 价格语义选择（要求 4 说明）
**选"最新日=1 基准"方案**：
- 每日扫描/手动推池的 `price`/`entry_px` = 检出日收盘 = **不复权真实价**，飞书推送与池内价格语义零漂移，推送代码零改动。
- `backfill` 历史回放的 `entry_px` 为归一后复权价（历史根缩放，跨除权可比），与旧行为不同——回填行价格不再等于当日不复权收盘，属预期变更（见"遗留风险"）。
- 图表（plot_day_signals）走 chanlun_data.load_kline（zhice-db 不复权）独立链路，本次未动。

### 验证方式
- `python test_chanlun_hfq.py` → **26/26 PASS**：10送10 合成缺口，hfq 开→单日涨跌幅连续(<5%)、无假分型（分型顶→底落差<15%）、最新根=真实价、头尾链接续、降级记名、parquet 集成、topup 接缝、守卫边界；关→复现旧行为（缺口≈-50%、假分型落差>35%）。
- 真实数据冒烟：600519 全程连续（pc 残差 <2% 属分红税口径噪声）；抽样真除权票除权日收益修正：001299 -27.0%→-2.4%、001210 -28.9%→+0.3%、301053 -29.4%→-1.0%。
- 反向验证：600696 20260601 -58% 为 pre_close 无跳变的退市整理真暴跌，因子不动、hfq 正确保留。
- 既有 `test_chanlun_tdx.py` 11/11 OK 无回归。

### 回退方法
1. config.json `chanlun.use_hfq` 改 `false`（或删键）→ 行为完全回到旧版，无需回滚代码。
2. 彻底回滚：`copy quant_backtest/chanlun_scan.py.bak_20261014 quant_backtest/chanlun_scan.py`（config 同理）；测试文件直接删。

---

## 任务B：双刀尾盘复核量纲修复

### 背景与目标
`portal/qd_scanner.py` `intraday_recheck` 原对 20 日累计成交额全序列用 `vol×close/1000` 近似（阈值 3e6 千元），临界票 ~5% 误判。湖 `data/ashare/*.parquet` 的 amount 是准确千元口径，历史 19 根应优先用湖值。

### 改动文件清单

| 文件 | 改动 | 备份 |
|---|---|---|
| `portal/qd_scanner.py` | 新增 `lake_amounts`/`merge_amount`；`intraday_recheck` 接湖口径 | `qd_scanner.py.bak_20261014` |
| `portal/test_qd_amount_fix.py` | 新增单测（15 项断言） | 新文件 |

### 改动点（qd_scanner.py）
1. **`lake_amounts(code)`**：读湖 parquet `[trade_date, amount]` → `{td8: 千元}`，进程内缓存；缺失/坏行 → None。保持顶部不 import pandas 的惯例（函数内 lazy import，`--help` 不依赖 venv 已验证）。
2. **`merge_amount(d, lake)`**：历史 19 根逐日查湖（准确千元），仅当日实时 bar 用 `vol×close/1000` 估算增量（占比 ~1/20，临界票误判 ~5% → ~0.3%）。**降级三条件**：湖缺失 / 湖末根 ≠ 实时序列前一日（断更错位）/ 历史日期未全覆盖 → 全序列退回旧近似（旧行为）。
3. **`intraday_recheck`**：调用上述两函数；降级票 signal 行 note 追加 `"amount_approx（湖缺失/末根错日，20日均额为近似口径）"`。
4. **输出格式零变化**：tail_signals 键集仍为 `{code, close, regime, note}`（note 本为自由文本），扫描节奏、cache 结构、下游 qd_view/qd_adapter 依赖均不动。

### 验证方式
- `python portal/test_qd_amount_fix.py` → **15/15 PASS**：湖口径优先（历史=湖值、当日=估算增量）、湖缺失/空映射退化近似、错日 skip（断更 1 日、历史覆盖不全两条）、intraday_recheck 端到端（note 标注三态 + 输出键集不变），全程 mock 网络不联网。
- 真实湖冒烟：000001.SZ 湖 853 日（最新 20261013），merge 后历史值与湖逐日相等。
- `python qd_scanner.py --help` 正常（延迟 import 惯例保持）。

### 回退方法
- `copy portal/qd_scanner.py.bak_20261014 portal/qd_scanner.py`；测试文件直接删。降级路径天然兜底，即使湖断更也自动退回旧行为并标注，无需紧急回滚。

---

## 遗留风险
1. **hfq 尾部因子滞后**：因子表 18:10 同步 vs 扫描 15:35——当日因子缺失由 pre_close 链自动接续（守卫内）；若 TDX 兜底行 pre_close 也缺失则该日不修（继承前根），概率低，次日自愈。
2. **backfill 历史价格语义变更**：hfq 开启后回填行 `entry_px` 为归一复权价，与历史池行（不复权）不可直接混比；pool_eval note 已标注口径。
3. **内存**：`_adj_map` 全表常驻 ~百MB 级，常驻进程（portal 手动推池）与扫描进程各持一份；若机器吃紧可改为按需加载（未做，最小改动优先）。
4. **US/HK 未复权**：本次仅 CN；US/HK 无 adj_factor 源，维持现状。
5. **当日估算增量口径**：腾讯 vol 单位若为手而非股，当日增量仍偏（历史 19 根已准确）——沿用原近似公式未改单位换算，属存量问题，影响 ~1/20 权重。
