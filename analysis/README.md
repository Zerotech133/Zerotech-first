# analysis · 个股全维分析流水线（P0）

> 目标：把"个股分析"从手工作坊变成一条可复用流水线：**一键产出 报告 + H5 + 图 + analysis.json + 归档**，并带质量门。
> 归属：`零界` 主仓（quant 线）｜工单：K-STOCK-ANALYSIS-P0-20261026

## 一、快速开始

```bash
# 全链路（含公告 PDF 抓取与规则分类，约 15-40s/只）
python -m analysis.run --code 600337.SH

# 跳过 LLM / 事件（最快，~10s）
python -m analysis.run --code 688023.SH --no-llm --skip-events

# 带军团（慢，Ollama 单卡排队时不要用）
python -m analysis.run --code 600337.SH --with-legion

# 推送到飞书（默认关闭）
python -m analysis.run --code 600337.SH --push
```

产物目录：
- 运行目录：`C:\项目\analysis_work\runs\<run_id>\`（report.md / h5.html / charts / analysis.json / manifest.json）
- 归档目录：`C:\项目\deliverables\<as-of>_<code>_<name>\`
- 索引：`C:\项目\deliverables\ANALYSIS_INDEX.md`

## 二、流水线

| 阶段 | 模块 | 输出 | 说明 |
|---|---|---|---|
| S0 触发 | `run.py` | run_id | 用户指令/池/事件/定时 |
| S1 快照 | `snapshot.py` | domains + 12 域数据 | 本地 curated parquet + 少量在线（disclosure_date） |
| S2 事件 | `events.py` | 事件库（SQLite） | anns_d → 巨潮 PDF → pypdf 文本 → 规则分类 → 去重 |
| S3 特征 | `features.py` | 技术/估值（含申万同业）/财务/资金/筹码 | 缠论/联动/筹码走门户 8091（失败降级） |
| S4 研判 | `views.py` | AI 通道 + 事实校验 | stock_analyze / research（可选 legion） |
| S5 结论 | `verdict.py` | 评级/情景/关键位/风险/观察 | 规则引擎；事件覆盖（停牌/重整/控制权）优先 |
| S6 交付 | `render.py` + `publish.py` | 报告/H5/图/manifest/归档 | 单文件 H5（模板来自协和/美克实践） |
| G1-G4 | `gates.py` | 质量门 | 数据/事实/一致性/交付 |

## 三、产物口径

- **评级**：研究口径 5 档（买入/观察偏多/观察/事件观察/回避），只给情景与触发/失效条件，不构成交易建议。
- **数据域**：quote/valuation/financial/moneyflow/chips/margin/announcement/news/research/holders/industry/calendar；缺项在 `domains` 里显式标注原因。
- **溯源**：`analysis.json.sources` 记录每个数据域与事件（URL/日期）；报告每个数字来自 features/snapshot。
- **质量门**：G1 覆盖率≥80% 且必需域不缺；G2 事件有 URL、结论有论点；G3 评级合法、情景一致、LLM 事实校验；G4 交付物齐全 + 免责声明。

## 四、数据层（sync.py，P0B 新增）

补 tushare 22 张扩展表到本地缓存（**不写 curated 仓库**）：

```bash
# 按股票：一致预期/预告快报/增减持/解禁/质押/回购/大宗/分红/主营/调研/互动易
python -m analysis.sync --codes 600337.SH,688023.SH,605258.SH
# 按交易日（全市场）：龙虎榜/机构席位/涨跌停/涨跌停价/北向
python -m analysis.sync --daily-only --days 30
```

缓存：`C:\项目\analysis_work\cache\tables\`（`by_code/<table>/<code>.parquet` + `daily/<table>.parquet`），
`_meta.json` 记录最后同步时间与行数。分析流水线自动读取：一致预期 → 估值段/信号；龙虎榜 → 资金段；
增减持/质押/解禁/回购 → 风险段；主营构成 → 公司画像；互动易/调研 → 事件段。

## 五、门户入口 + 盘后批（P0C 新增）

### 门户 API（`portal/api_analysis.py`，独立文件；`portal_api.py` 仅加早分派 3 段）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/analysis/health` | 流水线健康（db/runs/queue/最近一次运行） |
| GET | `/api/analysis/list?code=&limit=` | 运行台账（SQLite `analysis.db`） |
| GET | `/api/analysis/latest?code=600337.SH` | 最近一次运行 + analysis.json 摘要 |
| GET | `/api/analysis/report?run_id=` | 返回 report.md 文本 |
| GET | `/api/analysis/queue` | 队列计数/最近文件 |
| POST | `/api/analysis/submit` | 入队 `{code, llm, push}`（已加入 `_MUTATION_POSTS`，需 X-Portal-Token） |

### 盘后批（`analysis/batch.py`）

```bash
python -m analysis.batch                       # 读 watchlist.txt（默认不跑 LLM）
python -m analysis.batch --codes 600337.SH --llm --push
python -m analysis.batch --queue               # 消费一次门户队列
```

回执落盘：`C:\项目\analysis_work\batch\<date>\receipt.json` + `receipt.md`。
每只股票独立子进程（异常隔离），默认 `--no-llm` 避免与生产 Ollama 抢卡。

### 部署（L2，需人工批准后执行）

1. 重启 8091（`portal/start_portal_api.ps1`，内置 import 冒烟预检）；
2. 注册计划任务（每周一至周五 15:40）：`python -m analysis.batch --queue` 或 `--max 5`；
3. 验证：`curl http://127.0.0.1:8091/api/analysis/health` 返回 `ok:true`；任务日志落 `analysis_work/batch/`。

## 六、事件人工复核队列（P0E 新增）

公告事件由规则自动分类（`reviewed=0`），用 CLI 逐条人工复核：

```bash
python -m analysis.review --code 600337.SH --list --limit 20      # 查看（✓通过/✗驳回/·待复核）
python -m analysis.review --code 600337.SH --interactive           # 逐条：y通过 / n驳回 / c改类别 / s跳过 / q退出
python -m analysis.review --approve <event_id> --category 重整 --note "确认"
python -m analysis.review --reject  <event_id> --note "误分类"
python -m analysis.review --stats --code 600337.SH
```

- 状态：`reviewed=1` 通过 / `-1` 驳回 / `0` 待复核；`category_final` 为人工修正类别；
- 复核状态持久化在 `analysis.db.events`，**重复跑分析不会覆盖**（upsert 保留复核字段）；
- 报告 §六 增加「复核」列与复核统计；H5 时间线类别带 ✓/✗；批回执含每只标的待复核数（`pending_review`）。

### 事件 AI 预审清单（P0J 新增，只出建议、不代替人审）

```bash
python -m analysis.prereview --all                     # 三只 watchlist，输出 CSV+MD 到 deliverables
python -m analysis.prereview --code 600337.SH --out C:\项目\deliverables\2026-10-26_事件AI预审清单
```

- 每条 pending 事件给出：`approve`（类别一致）/ `reclassify`（建议改类别）/ `reject`（程序性文件，如实施细则/审计委员会/章程/股东大会通知）；
- **不写复核状态**；人工确认后按 CSV 里的 `event_id` 用 `review --approve/--reject` 或门户 `/sa` 页点选入库；
- 规则补全（同一提交）：`MATERIAL` 去掉 `审计`（避免审计委员会类程序文件混入）；`EVENT_RULES` 增加「治理/程序」类别；重整优先于退市/ST。

### 非经常性损益识别（P0L，2026-10-30）

- 特征层：`fundamental.nonrecurring = {profit_dedt_yi, extra_item_yi, ratio, dt_yoy, dedt_ratio}`（来自 `fina_indicator.profit_dedt/extra_item/dt_netprofit_yoy`，仅当 fina 与 income 最新期一致才采信）；
- 评级层：`ratio ≥ 0.5` 且 `|extra_item| ≥ 0.5 亿` → 判定"利润含大额非经常性损益"：抵消盈利加分（-1.0）、抑制业绩预告预增加分（不再 +0.3）、风险提示扣非与主场，`verdict.nonrecurring` 随报告输出；
- 报告/H5：财务表新增「扣非归母净利 / 非经常性损益（占归母比） / 扣非净利同比」三行。

### G1 覆盖率目标（P0J，诸葛亮裁示）

- `config.COVERAGE_MIN=80`（硬门槛，低于即不通过）、`config.COVERAGE_TARGET=90`（目标，80-90 区间标黄告警但不否决）；
- 报告/analysis.json 的 `checks.G1.issues` 会写明"覆盖率 x% < 目标 90%（告警，不否决）"。

## 七、三股回放（2026-10-23 口径，P0B 复跑）

| 标的 | 覆盖率 | 评级（流水线） | 人工版对照 | 用时 |
|---|---|---|---|---|
| 600337.SH ST美克 | 91.7% | 事件观察 | 事件观察（一致） | ~14s |
| 605258.SH 协和电子 | 83.3% | 事件观察（停牌/控制权事件） | 事件观察（一致） | ~8s |
| 688023.SH 安恒信息 | 91.7% | **观察**（财报日历门禁：三季报 2 天内 → 由"观察偏多"降档） | 观察（偏积极，一致） | ~13s |

> 财报日历门禁：预约披露日 ≤7 天时，买入→观察偏多；≤2 天且原为观察偏多→观察；并写入风险段。

## 六、已知限制（P1 待补）

1. **龙虎榜/北向只有近 N 天**：sync 默认 30 天，历史深度需按需回填；席位画像未接。
2. **事件分类**：规则版，正文不参与分类（避免模板句误判）；OCR 未接；人工复核队列未建。
3. **一致预期**：`report_rc` 覆盖随券商披露，部分标的 180 天内可为 0；目标价字段未用（口径不稳）。
4. **LLM 通道**：默认跑 stock_analyze + research；军团受单卡排队影响，默认关闭。
5. **数据时效**：`cyq_perf/limit_list_d/margin_detail` 等本地表有滞后（已在 G1 报警），依赖仓库同步修复（L2）。
6. **门户入口/定时**：`/api/analysis` 与盘后批未接（涉及生产调度，L2 审批后进行）。

## 六、目录

```
analysis/
├── run.py          # CLI 编排
├── snapshot.py     # S1
├── events.py       # S2
├── features.py     # S3
├── views.py        # S4
├── verdict.py      # S5
├── render.py       # S6
├── publish.py      # 归档/推送
├── gates.py        # G1-G4
├── db.py           # SQLite（runs/events）
├── config.py       # 配置
├── schema/stock_analysis.schema.json  # 产物 schema
└── templates/      # H5 CSS/JS 模板
```

### 分析方法优化（P0M，2026-10-30）

- **行业映射纠偏**：优先 `stock_basic.industry` 名称匹配申万分类（L2/L3 命中后按 `industry_code/index_code` 上溯 L1），回退 `sw_l1_member`；`snapshot.industry.match` 标注来源（stock_basic / sw_l1_member）。
- **估值历史分位**：`valuation.ps_pct_hist / pe_pct_hist / pb_pct_hist / hist_temp`（近 5 年，样本≥120）；报告/H5 增加「PS 历史分位（近5年）」「估值历史温度」。
- **数据时效门禁**：`snapshot.data_lag = {last_trade_date, today, calendar_days, trade_days}`；G1 对 `trade_days>=1` 告警（不否决），评级层 `trade_days>=3` 置信度上限 0.5。
- 评级层：PS 历史分位 ≤25 加 0.3、≥85 减 0.4，并进 key_signals。

### 评级打分 v2（P0N，2026-10-30）

- 五维 100 分制：`quality 30 / growth 20 / valuation 20 / trend 15 / event 15`（`config.SCORE_WEIGHTS`）；
- 质量=ROE/毛利率/OCF÷净利/负债 + 非经常性惩罚（占比≥50% 扣 20 分、亏损封顶 40）；
- 成长=营收同比+净利（有非经常性时用扣非同比）+预告；估值=同业分位+自身 5 年分位+历史温度；趋势=均线/250日分位/20日动量/主力资金；事件=ST/退市/重整/问询诉讼/减持质押 + 回购预增；
- 评级由总分映射（`config.SCORE_CUTS`，P0O 回测后校准）：≥82 买入 / ≥68 观察偏多 / ≥50 观察 / ≥32 观察（偏弱）/ <32 回避；ST/退市/重整仍走事件观察覆盖；
- `verdict.score` 改为 0-100（原规则分退役）；报告/H5 增加「五维分解」；P0O 回测将按分档校准权重与阈值。

### 评级回测（P0O，2026-10-30）

```bash
python -m analysis.backtest --universe 300 --out "C:\项目\deliverables\2026-10-30_评级回测"
```

- 月频：市值前 N（默认 300，剔除 BJ、要求 2018 前有行情）× 92 个月（2019-01~2026-08），五维打分 v2（事件维度无历史→中性 50），财报按 `ann_date` 截止防未来函数；
- 输出：总分分位、固定分档/当月分位映射的 20/60 日超额与胜率、Spearman IC、分因子 IC、`SCORE_CUTS` 校准建议；
- 2026-10-30 首跑结论：IC 0.011（弱）、分档无单调性、quality/growth/trend 因子 IC≈0，仅 valuation +0.024 → **暂不按建议分档上线**，待 P0P 做权重/频率/股票池实验后再校准。

### 行业数据地基（P0Q，2026-10-30）

- 数据源：tushare `index_classify`（L1/L2/L3，511 条）+ `index_member_all`（逐 L1 拉当前成分，5,922 只，is_new=Y）；
- 缓存：`work/analysis/cache/sw/{classify,member}.parquet`（不改 curated 数据仓）；
- 刷新/自检：`python -m analysis.industry --refresh` / `--check`；
- `snapshot.industry` 现返回 **L1/L2/L3 + tushare_sw 标注**；同业改成**申万二级（L2）同行业**（比一级更可比），回退一级/旧表；
- 效果（300308）：行业=通信/通信设备（原"电力设备"），同业=新易盛/长飞/天孚，PS 同业分位 **95.1 → 25.0**。

### 策略实验网格（P0P，2026-10-30）

- `python -m analysis.backtest --universe 300` 默认在基线之外跑 6 个变体：`base / val_heavy / val_only / equal / qg_only / trend_rev`；
- 评估：2019-2023 训练、2024-2026 验证的 IC / 验证 IC 胜率 / Top-Bottom 分位差 / 季频（3/6/9/12 月末 60 日）IC；
- 判定：`val IC>0.02 且 top_bottom>0 且 买入/观察偏多 val 超额>0` 才算通过；
- 2026-10-30 首跑结论：**6 个变体全部未通过**（最好 trend_rev val IC 0.0232 但 top_bottom -0.43；val_heavy 季频 IC 0.0312 但验证段 0.0182；val_only 训练 0.0723→验证 0.0061 过拟合）→ 评分仅作解释框架，不作选股信号。

### 事件驱动研究（P0R，2026-10-30）

```bash
python -m analysis.event_backtest --universe 500 --out "C:\项目\deliverables\2026-10-30_事件驱动组合"
```

- 数据：curated `anns_d`（2019-2026，去重后按标题规则分类）+ 行情 + 000300 基准；事件后下一交易日收盘买入，持有 20/60 日；
- 关键修正：同时输出 **vs 基准**与 **vs 同期全样本等权（rel20）** 两口径——当前市值前 N 样本存在幸存者漂移，不扣除会把漂移算成事件超额；
- 2026-10-30 首跑（537 只 × 2040 交易日，12.8 万条 material 事件）：
  - 验证段（2024-2026）相对全样本：**股权激励 +2.18%（t=6.93）**、经营/订单 +1.47%（t=1.75）、重组并购 +1.32%（t=3.32）、减持/增持 +1.03%（t=2.71）；分红 -0.85%（t=-3.44）、业绩 -0.08%、回购 -0.13%；
  - 组合层面：event_only 月均超额 0.99% ≡ baseline 0.99%（无增量），event_cheap 0.54% 更差 → 宽基"事件+便宜"组合无 alpha；
- 结论：**事件类型内部分化明显**，下一步应做"事件类型精选组合"（股权激励/重组并购/经营订单），而不是宽基事件暴露。
