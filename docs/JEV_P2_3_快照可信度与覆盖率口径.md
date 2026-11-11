# JEV P2.3 前置 · 社交快照可信度与覆盖率口径（等待期护栏）

> 配套：`tools/jev_social_coverage.py`（护栏）、`portal/test_jev_social_coverage.py`（回归）、
> `tmp/p23_snapshot_audit.json`（实测台账）、`docs/JEV_P5_1_实现说明.md` §八、
> `docs/JEV_P5_2_单日可行性验证.md`、`tools/run_social_fetch_daily.py`。
> 本轮**未改** `docs/plans/2026-11-10_统一真相源_能力账本+执行计划_v2.md`
> （真相源本轮受敏感保护拦截，按纪律不重试、不绕过、不凭记忆回写）。

## 一、结论先行（三条硬结论）

| # | 结论 | 状态 |
|---|---|---|
| 1 | 盘上 3 份 `social_*.json` **合规 0 份**，覆盖率表必须为空 | ✅ 实测，护栏已锁死 |
| 2 | `JEVSocialDaily` 设 18:30，而 daily 分片基本 20:30 落盘 → **前向采集结构性空转**，不是今日特例 | ✅ 实测，待批修复 |
| 3 | `jev_social.py::load_social(day)` 不校验观测时刻 → 页面与研究链路默认「文件名日期 = 观测日」 | ✅ 读码确认，待拍板 |

**因此：P2.3「覆盖率表」当前不能产出任何覆盖率数字，P5.3 / P5.4 校准也不具备合规社交输入。**
本轮交付的是**尺子和护栏**，不是覆盖率结论。

## 二、数据契约：`day` ≠ 观测日

`jev_social_fetch.py` 的三个源（雪球四榜 / 股吧人气榜 / 股吧帖子）**都不接收日期参数**：
`--day` 只决定「给哪批票拉帖子」，榜单与帖子数值永远是**查询时刻**的实时值。

| 字段 | 真实含义 |
|---|---|
| `day` | 【选票日】用了哪天的涨停 / 焦点板块清单 |
| `fetched_at` | 【观测时刻】榜单 / 帖子真正反映的是这一刻 |

P2.2 文档已经定性「历史回补物理不可行」，但产物 JSON 里没有任何机器可判的校验位，
消费者一律按文件名取数，定性没有落成护栏。本模块把它落成可执行规则。

**合规快照判定**（优先级递减，任一命中即剔除）：

1. `missing_meta`：缺 `day` / `fetched_at` 或格式不可解析；
2. `cross_day`：观测日 ≠ 选票日（记 `lag_days`，含负值）；
3. `intraday`：同日但观测时刻早于盘后下界（缺省 15:00，可 `--after-close` 覆盖）；
4. `ok` → `compliant=True`。

`errors` 不否决（三源独立降级是设计内行为），但留痕；文件名日期与内容 `day` 不一致单独标 `file_day_mismatch`。

## 三、实测台账（本轮基线）

`python tools/jev_social_coverage.py` 输出，完整 JSON 见 `tmp/p23_snapshot_audit.json`。

| 文件 | `day` | `fetched_at` | 分类 | lag_days |
|---|---|---|---|---|
| `social_20261106.json` | 20261106 | 2026-11-08 18:51:37 | `cross_day` | +2 |
| `social_20261109.json` | 20261109 | 2026-11-10 10:36:48 | `cross_day` | +1 |
| `social_20261110.json` | 20261110 | 2026-11-10 13:15:49 | `intraday` | 0 |

汇总：快照 3 份 / 合规 **0** / 剔除 **3**；`coverage_table=[]`、两个均值 `None`、
`coverage_only=True / gate_eligible=False / calibrated=False`。

补充事实：

- `social_20261110.json`（13:15）**不在** `tools/social_fetch_daily.log` 里，该日志最后一次写盘动作是
  10:19:40「20261110 非交易日或 daily 未就绪，跳过（不写盘）」→ 这份是绕过触发器直跑的产物，
  `focus_board` 为盘中的「影视音像」，属盘中实时值。
- `social_20261109.json` 同样是次日盘中直跑采集，却标成 11-09 的快照。
- 三份都不删、不改名：保留作诊断样本（删除属破坏性动作，需另批）；11-10 那份会由
  未来同日合规快照按 `os.replace` 自愈覆盖，11-06 / 11-09 两份**不会**被覆盖，
  故下游必须一律经护栏判定，不能靠「跑一次就洗白」。

## 四、两个阻塞

### 4.1 时序错配（护栏已解决「会不会被误用」）

把 11-08 采的榜单当 11-06 的社交状态，特征里混入了事件日之后的信息。
需要精确表述：11-08 是周末、仍早于后验交易日 11-09，**不构成对后验收益的直接窥视**，
但作为「T 日当日社交状态」的解释变量是错的，会高估当日社交信号的可用性与强度。

影响面（读码确认）：

| 消费者 | 行为 | 判断 |
|---|---|---|
| `portal/jev_social.py::load_social(day)` | 仅按文件名取数，`has=True` 只看文件存在；`fetched_at` 透传但**零校验** | ⚠️ 无护栏 |
| `jev_pattern.py` `summarize()` / `Signal.social_has` | 继承上层 `has` | ⚠️ 间接继承 |
| `jev_casebook.py` `social_covered` | 同上，P5.2 记「社交覆盖 42/63」 | ⚠️ 间接继承 |
| `jev_graph.py` 社交面板 | 同上 | ⚠️ 页面展示 |

**对已完成结论的影响：P5.2 已恒带 `feasibility_only=True / gate_eligible=False / calibrated=False`，
任何数字不得进闸，v2 判定闸状态未被污染，无需回滚或改结论。**
但 P5.3 / P5.4 校准若沿用这批快照，会把错配特征当成基准，必须在切换合规快照后重算。

### 4.2 门控时点早于数据落盘（待批，属系统配置）

`run_social_fetch_daily.py::is_trading_day()` 要求 `quant_backtest/data/tushare/daily/d_{day}.parquet`
已存在，否则「跳过（不写盘）」。实测近 10 个分片的 mtime：

| 分片 | 落盘时刻 |
|---|---|
| `d_20261027` / `d_20261028` / `d_20261030` | 20:30 前后 |
| `d_20261103` / `d_20261104` / `d_20261105` | 20:30 前后 |
| `d_20261109` | 15:57（提前的特例） |
| `d_20261106` | 次日 13:09 补落 |
| `d_20261102` | 23:19 |

计划任务 `JEVSocialDaily` 触发时点为 **18:30**，早于常规落盘 20:30 约两小时。
**结论：绝大多数交易日 18:30 门控必然判「未就绪」而跳过，任务从注册起就基本空转。**
今天 `d_20261110.parquet` 此刻不存在，18:30 同样会跳过——**「首份前向快照」今天不会到来**。

且这里有个更深的结构矛盾：不能用「次日补跑」绕过，因为补跑会让 `fetched_at` 与 `day`
不同日，被 4.1 的护栏判为 `cross_day`，等于把空转变成了「天天造不合规快照」。
正路只有**把触发时点挪到落盘之后**，不是放松门控。

归责：`daily` 分片落盘属采集工位，计划任务配置属系统级，二者都**不在 JEV 渲染层职责内**
（见 `DEVELOPER_GUIDE` / 工作区长期记忆「JEV 职责边界」）。本轮只上报，不动配置。

## 五、护栏实现与覆盖率口径

`tools/jev_social_coverage.py`：只读 `portal/social_*.json`，不联网、不采集、不改采集器、
不改计划任务；默认不写盘，仅显式 `--out` 才落 JSON。`build_table()` 是纯读装配，
`classify()` / `coverage_row()` 可注入合成 dict 做测试。

**覆盖率两档必须同时给出，禁止挑好看的报**：

| 项 | 定义 |
|---|---|
| 分母 | 快照 `codes` 选票池（6 位代码去重）——**不是** `by_code` 全量（后者混入雪球四榜 + 股吧榜约 300 只非目标票） |
| 分子 A `listed` | 分母中出现在任一榜单的票数（被动热榜收录） |
| 分子 B `posts` | 分母中真正拉到帖子的票数（**独立一档**，主动拉帖；覆盖率可高于 `listed`） |
| 分母为 0 | 记 `None`，不硬算 0% |

**两档并列独立、不构成包含关系**：`listed` 来自雪球四榜 + 股吧人气榜的被动收录；
`posts` 来自采集器对 `codes` 选票池每只票的主动拉帖（实测每只非零，覆盖率可达 1.0）。
因此只能要求 `n_listed ≤ n_targets` 与 `n_posts ≤ n_targets` 两条**独立上界**，
两档覆盖率独立解读；**撤销**原「更严一档 / `posts ≤ listed`」判据，
不得以任何方式截断 `posts` 去迎合包含关系（老田裁定，覆盖率实现零改动）。

非合规快照**只进 `excluded` 诊断清单，绝不进分子 / 分母**；文件名不符、坏 JSON 各自归类。
输出恒带 `coverage_only / gate_eligible=False / calibrated=False` 与 4 条 `disclaimers`，
明确「这是数据可用性指标，不是 pattern 覆盖率，不得引用进判定闸 A 条件②」
（P5.1 §八 三硬伤：样本偏倚、阈值未校准、时间维缺失，一条未解）。

回归：`portal/test_jev_social_coverage.py`，**34 passed**。锁死四类分类、可配置盘后下界、
分母口径（非目标票不得混进分子）、两档各自 ≤ 分母且互不比较、空分母、坏 JSON、
全部剔除时不产出任何覆盖率数字。锁死 `posts > listed` 为合法形态，禁止截断任一档。
日期与编号字面量一律 `chr()` 码点构造，并由 `ord()` 双向复核（源码零手写日历字面量、
零反斜杠转义字面量），防通道串味。

## 六、待拍板与落地状态

> **2026-11-10 更新**：决策 A 已获老田批准并落地（见下「A 方案落地」）；
> 决策 B（计划任务时点 18:30 → 20:45，系统级变更）**仍未批准、未执行**。

### 决策 A · 护栏接入范围（已批准执行 A）

| 方案 | 做法 | 收益 | 代价 | 状态 |
|---|---|---|---|---|
| **A（已执行）** | 护栏只作用于 P2.3 覆盖率表与 P5.3/P5.4 校准链路；现有 `jev_graph` 页面与 P5.1/P5.2 历史结论不动，仅标注时序口径 | 不破坏现有视图与已交付研究 | 页面短期仍显示错配社交数据 | ✅ 已落地 |
| B（未执行） | 把 `load_social()` 接上护栏，非合规快照返回 `has=False` + `note` | 全链路不再有错配数据 | **破坏性**：当前 3 天社交面板立刻全空，P5.2 案例库社交维度需重算 | ⏸ 待合规快照累积后再切 |

#### A 方案落地内容

1. 护栏纯函数抽到 `portal/jev_snapshot_gate.py`，页面侧与研究侧**共用同一套判定**，
   不另起炉灶；`tools/jev_social_coverage.py` 改为从它导入并重新导出（既有回归零改动通过）。
2. **P2.3 覆盖率表**：以 `tools/jev_social_coverage.py` 为唯一入口，
   非合规快照只进 `excluded` 诊断清单，绝不进分子 / 分母。
3. **P5.3/P5.4 校准链路**：唯一取数口
   `jev_snapshot_gate.load_compliant_social(day)` / `require_compliant_social(day)`；
   不合规时 `has=False` 且 `by_code={}`、`posts=[]`，`require_` 直接抛
   `SocialSnapshotNotCompliant` fail-fast，绝不静默降级。
   CLI 可执行准入检查：`python tools/jev_social_coverage.py --require-day YYYYMMDD`（不合规 rc=3）。
4. **页面侧零逻辑变更**：`jev_social.py` 只在 docstring 加时序口径标注，
   `load_social()` / `jev_graph` 行为原样；P5.1 / P5.2 文档各加一段口径标注，
   **结论与数字一个字未改**。
5. **验收点**：`load_social()` 对现有 3 份快照仍返回 `has=True`（页面不受影响），
   而 `load_compliant_social()` 全部返回 `compliant=False`（校准链路被拦住）——
   两条同时成立才算 A 落地，已写成回归测试锁死。

> 注意：A 只解决了「尺子与准入」，**没有解决水源**。计划任务时点仍是 18:30，
> 今晚仍不会产出合规快照；须另批决策 B 后，覆盖率表才可能从 0 变成 1。

### 决策 B · 计划任务时点（系统级变更，需五要素批准）

| 要素 | 内容 |
|---|---|
| 命令全文 | 先用 `Export-ScheduledTask -TaskName JEVSocialDaily` 导出备份，再以 `Set-ScheduledTask` 把每日触发器由 18:30 改为 **20:45**（其余 Action / 用户 / 权限不变） |
| 影响面 | 仅每日社交快照采集时点后移约 2h15m；不碰 8091 服务、不碰行情数据、不改采集代码；采集仍落在同一自然日盘后 |
| 不可逆点 | 计划任务触发器被覆盖；可由备份 XML 原样恢复，无数据删除 |
| 替代方案 | ① 维持 18:30，改由采集工位在落盘后另建触发机制（需新机制，成本高）；② 放宽门控为「工作日即跑」（会走盘中降级、重造盘中假快照，**不推荐**） |
| 回滚预案 | 保留导出的原触发器 XML，一条 `Register-ScheduledTask -Xml` 恢复 18:30；已落盘快照不受影响 |

按纪律，改计划任务须老田明示批准（沉默 ≠ 同意），本轮**未执行**。

## 七、纪律与交付物

- 未改 v2 真相源、未改 `jev_social_fetch.py` / `run_social_fetch_daily.py` / `jev_social.py`；
- 未删任何文件、未联网、未采集、未重启服务、未动计划任务；
- 所有数字由脚本读盘动态生成，日期 / 编号经 `ord()` 原始字节复核；
- Python 统一 `C:\Program Files\Python311\python.exe`。

| 文件 | 作用 |
|---|---|
| `tools/jev_social_coverage.py` | 快照可信度分类 + 两档覆盖率表（只读 / 离线） |
| `portal/test_jev_social_coverage.py` | 34 项回归，锁死护栏 |
| `tmp/p23_snapshot_audit.json` | 本轮实测台账（合规 0 / 剔除 3） |
| `docs/JEV_P2_3_快照可信度与覆盖率口径.md` | 本文 |

复跑：

```bash
cd C:/aios
"C:/Program Files/Python311/python.exe" tools/jev_social_coverage.py
"C:/Program Files/Python311/python.exe" -m pytest portal/test_jev_social_coverage.py -q
```
