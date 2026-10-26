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
- 运行目录：`C:\项目\work\analysis\runs\<run_id>\`（report.md / h5.html / charts / analysis.json / manifest.json）
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

缓存：`C:\项目\work\analysis\cache\tables\`（`by_code/<table>/<code>.parquet` + `daily/<table>.parquet`），
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

回执落盘：`C:\项目\work\analysis\batch\<date>\receipt.json` + `receipt.md`。
每只股票独立子进程（异常隔离），默认 `--no-llm` 避免与生产 Ollama 抢卡。

### 部署（L2，需人工批准后执行）

1. 重启 8091（`portal/start_portal_api.ps1`，内置 import 冒烟预检）；
2. 注册计划任务（每周一至周五 15:40）：`python -m analysis.batch --queue` 或 `--max 5`；
3. 验证：`curl http://127.0.0.1:8091/api/analysis/health` 返回 `ok:true`；任务日志落 `work/analysis/batch/`。

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
