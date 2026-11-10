# JEV P5.1 · pattern matcher 实现说明（2026-11-10）

> 配套：`docs/jev_pattern_spec.md`（P4 八 pattern 定义）、`docs/jev_deltamin_spec.md`（P1.4 Δmin 定义）、
> `docs/plans/2026-11-10_统一真相源_能力账本+执行计划_v2.md`（§阶段 P / §六 判定闸）。
> 实证台账（可复现）：`tmp/p51_evidence.json`，生成脚本 `tmp/p51_evidence.py`。

---

## 一、交付物

| 文件 | 作用 |
|---|---|
| `portal/jev_pattern.py` | 匹配引擎主体：纯函数判定层 + 离线装配层 + Δmin 计算 + 覆盖率统计 + CLI |
| `portal/test_jev_pattern.py` | 27 项回归（全部通过），含 2 个本轮实测踩中的坑 |
| `docs/JEV_P5_1_实现说明.md` | 本文 |

CLI：`python portal/jev_pattern.py --day YYYYMMDD [--limit N] [--code CODE] [--json]`

契约按 P4 §3 落地：`match(code, day) -> list[Hit]`，`Hit = {pattern, name, dims, values, confidence, hypothesis, fork}`。

---

## 二、分层（严格守住 JEV 职责边界）

```
判定层（纯函数，不读盘不触网）
  Signal → match_signal(signal, th) → list[Hit]
  8 个 pattern 的三维软判定，返回 True / False / None
        ↑
装配层（只读本地，不联网 / 不写盘 / 不采集）
  DayContext(day).signal(code) → Signal
  资金  _load_flow_offline(day)        读 _jev_snap/flow_{day}.json
  社交  jev_social.load_social(day)   读 portal/social_{day}.json
  时间  compute_delta_min(...)        读 stk_mins / daily parquet + news.db 只读连接
```

**关键取舍**：资金面**没有**调 `jev_flow.load_flow()`。后者在无新鲜快照时会去 xtcloud 拉数，
离线装配绝不能意外触网；历史研究的收盘终值也不会变，所以本模块直接读已落盘快照并忽略 TTL。
采集是 `tools/*_fetch.py` 的事，本模块只回答「拿到了说明什么」。

`DayContext` 一次读盘建全天上下文，供 P5.2 批量跑 5000+ 样本复用。

> **社交快照时序口径标注（2026-11-10，A 方案，仅标注、不改结论）**
> 上面装配层的 `jev_social.load_social(day)` 是页面展示口径：按文件名取数、零时序校验，
> 而快照的 `day` 是【选票日】、`fetched_at` 才是【观测时刻】。因此本文所有 social
> 覆盖读数与 `summarize()` 输出，描述的是「探索性页面/研究口径」，**不是当日社交状态的可信度量**，
> 按 §八 本就不得引用进判定闸 A 条件②。**本轮未改任何数字与结论。**
> P2.3 覆盖率表与 P5.3/P5.4 校准链路须改用
> `jev_snapshot_gate.load_compliant_social()` / `require_compliant_social()`；
> 口径全文见 `docs/JEV_P2_3_快照可信度与覆盖率口径.md`。

---

## 三、8 个 pattern 的落地口径

三维度 = **资金 + 社交 + Δmin**（P3 已拍板放弃结构维度）。全部阈值集中在
`Thresholds` dataclass，**是 P5.3/P5.4 案例库校准的起点，不是结论**：

| 组 | 阈值 | 用途 |
|---|---|---|
| Δmin | `th_lead` / `th_drive` = ±5min | 价格先行 / 消息驱动判定带（P1.4 建议起点） |
| Δmin | `th_lead_strong` / `th_drive_strong` = 30min | P2 / P4 / P6 / P7 的强形态 |
| 资金 | `flow_eps` = 50 万元 | 中性带；`flow_mild` = 300 万元 |
| 社交 | `xq_top_n` / `guba_top_n` = 20 | 机构圈关注 / 散户追捧 |
| 社交 | `hot_posts` / `hot_reads` | 帖子热度线 |

- **≥2 个维度命中才记入 pattern**（P4 §3），`confidence` = 命中维度权重 / 总权重（默认等权）。
- 每个 pattern 的 `hypothesis`（预期演化）与 `fork`（分化节点假设）原样保留为**待验证假设**，
  未经 P5.2/P5.3 案例库检验不得当结论。
- 允许一只票同时命中多个 pattern（软标签），P5.3 用演化路径分布检验谁有区分度。

### 缺失语义（本轮最容易造假数据的一处）

单维判定返回 `True / False / **None**`，`None` = 该维度对本票无数据。
社交快照只覆盖榜单 Top100 + 选票 60 只；全市场 5000+ 只里绝大多数**未被覆盖**。
把它们判成「雪球机构沉默」会凭空白送 11-06 判例的结论，因此：

- 快照整体不在位 → `social_has=False` → 维度 None
- 快照在位但该票既不在榜、也无帖子 → `social_covered()=False` → 维度 None
- 只有「快照覆盖了该票、四榜确实无其名」才算 `xq_silent=True`

Δmin 侧同理：`delta_kind=unpriced` 或 `delta_min is None` → 时间维 None，**绝不硬算**（判例 #25③）。

---

## 四、数据契约勘误（相对 P1.4 §1.2）

| 项 | P1.4 文档写法 | 实测事实 |
|---|---|---|
| `news.stocks` 列 | 逗号分隔的 `ts_code` 列表 | **中文名称 JSON 数组**（`["中国平安"]`、`["英伟达"]`），且混有非 A 股名称。按 `LIKE '%000001%'` 查会零命中 |
| 新闻→票映射 | 上述 `stocks` 列 | 真实通道是 **`news.jev` JSON 的 `companies` / `stocks` 数组里的 6 位数字代码**（与既有 `jev_graph.load_events` 同口径），本模块复用该口径 |
| 日级源 | 白名单外不进分钟 Δmin | 实测成立：`announcement` / `xd_notice` / `report` / `survey` 的 ts 批量落 00:00，`_ts_kind=batch` |

`_load_news_anchors` 四道过滤：jev 能解析出 6 位代码 → source ∈ 分钟白名单（9 源）
→ `_ts_kind == intraday`（排除批量 / 午休 / 盘后）→ `parse_news_time(ts, created_at, source)`
解析成功（事件时间，不是入库时间）。

**符号口径差异（别混）**：本模块 `Δmin = T_news − T_limit_seal`，**正 = 价格先行/消息追认**；
`jev_graph.first_move_after` 的 `lag_min` 是「价格 − 消息」且锚点为首次异动，**符号相反、锚点不同**，两者不通用。

---

## 五、本轮实测踩中并已修掉的 3 个坑

1. **`stk_mins.ts_code` 是完整代码 `'300750.SZ'`，不是 6 位**。
   用 `code6` 做 pyarrow 等值过滤会静默返回 0 行 → 全样本假 `unpriced`。
   已改为「时间条件下推 + Python 侧按前 6 位归票」，并写回归测试锁死。
   P1.4 伪代码里的 `code` 指完整代码，本模块对外接口是 6 位，这层转换不能漏。
2. **分钟缓存键必须带 day**。同一 parquet 是「单票整月」，只按 path 缓存会把 A 日切片
   喂给 B 日，算出完全错位的封板时刻。已改 `(path, day)` 并加回归。
3. **资金快照 12h TTL 会误杀历史样本**。`jev_flow._snap_read` 的 TTL 是给当日在线请求
   用的；11-06 的 `flow_20261106.json` 早已过期，按 TTL 读会让历史样本资金维度全灭。
   已改 `_load_flow_offline` 忽略 TTL（收盘终值不会再变），且仍不联网。

另加固：`_mins_index` 一次 `listdir` 建 `{c6: [文件]}` 索引。目录有 3.4 万个文件，
逐票 listdir 实测 132ms/次，P5.2 跑 5000+ 样本会白烧十余分钟。

---

## 六、实证台账（两个真实同日快照）

数据来自 `tmp/p51_evidence.json`，日期从快照文件名提取、码点复核，非手写字面量。

| 指标 | 11-06 | 11-09 |
|---|---|---|
| 本地分钟覆盖票 / 日线全市场 | 1029 / 5573 | 1029 / 5573 |
| 资金快照票数 | 5579 | 见台账 |
| 社交快照 `by_code` | 305 | 297 |
| 新闻锚点票数（白名单分钟源 + 6 位代码） | 155 | **0** |
| 双锚点交集（新闻 + 本地分钟） | 58 | 0 |
| 真正算出 Δmin | **10**（price_lead 7 / sync 2 / news_drive 1） | 0 |
| 未算出主因 | `no_limit_touch` 48（当日未封板，合理） | 上游无锚点 |
| 社交榜样本池（30 只）三维度可用 | 资金 30 / 社交 30 / **时间 8** | 资金 30 / 社交 30 / **时间 0** |
| 样本池 ≥1 pattern 覆盖率 | 0.7667 | 0.6667 |

**Δmin 样本方向**（11-06，与判例一致）：可计算的样本绝大多数是
`price_lead`（价格先行 / 消息追认），典型领先 41 / 89 / 62 / 65 分钟。
这不是结论，只是说明时间通道代码真的跑通了，不是纯合成测试。

### 11-09 新闻锚点为 0 的根因

不是本模块过滤 bug：当日 `news.jev` 有 1646 行、`companies` 键都在，
但**几乎全为空数组**（1646 行里仅 1 行带 6 位代码，且是 `survey` 日级源、
ts 落 00:00 批量，被「分钟白名单 + 盘中 intraday」两道过滤正常排除）；
11-06 则有 1412 行带 6 位代码。且当日 `sina` / `eastmoney` 两源完全没有数据。
属于**判定/采集工位的回填断点**，按 JEV 职责边界不由本模块修。
影响：P5.2 案例库在补齐之前，只能拿 11-06 这类有回填的交易日做时间维度研究。

---

## 七、对判定闸 A 条件①的口径修正建议（需老田拍板）

v2 §六把条件①记为「Δmin 可用性扫描通过（≥30% 样本有分钟级时间戳）→ 已满足」，
依据是 P1.3 的 **news.ts 分钟级覆盖 81%**。但 P1.4 明确定义 Δmin 需要**两个锚点**：
新闻侧 81% 只证明单侧时间戳可用，**价格侧 `T_limit_seal` 才是本轮的瓶颈**。

实测（11-06）：

- 本地分钟只覆盖中证800 一线，1029 / 5573 ≈ 两成；
- 有新闻锚点的 155 只票里只有 58 只有本地分钟；
- 双锚点齐备且当日真封板、最终算出 Δmin 的只有 **10 只**。

也就是说 **「双锚点可计算率」远低于 30%**，且 11-09 因上游回填断点直接为 0。
建议把条件①重定义为：

> **条件①（修订）**：在目标样本池上，Δmin **双锚点可计算率** ≥ 30%（而非单侧新闻时间戳覆盖率）。

按此口径，**当前条件①未满足**，需两项前置（都不属于 JEV 渲染层）：

1. 价格侧：采集工位按 `tools/jev_mins_fetch.py --day ... --codes ...` 补拉目标票分钟数据
   （P1.4 §3.2 已写明的补救路径；`jev_graph` 已有「本地优先→远端兜底写回」机制）。
2. 新闻侧：判定工位修复 `jev.companies` 6 位代码回填（11-09 断点）。

**建议先不急着改 v2 的「已满足」结论**——本轮只把实测与口径差异记录在此，
等老田拍板是否按修订口径重判。若维持原口径，也需注明「仅新闻侧可用」以免被误读。

---

## 八、覆盖率为何不能进判定闸 A 条件②

样本池 0.7667 / 0.6667 是**探索性数字**，三个硬伤：

1. **样本偏倚**：候选池取自社交榜（三维度最齐的一批票），不是 5000+ 涨停全样本；
2. **阈值未校准**：`Thresholds` 全是拍脑袋起点，未经 P5.3/P5.4 案例库校准，
   `summarize()` 输出已带 `calibrated: False` 标记；
3. **时间维度大面积缺失**（11-06 只有 8/30，11-09 为 0），覆盖率实际是
   「资金 + 社交」两维撑起来的，不能代表三维度组合 pattern。

条件②（pattern 覆盖率 ≥60%）须等 P5.2 全样本案例库 + P5.4 校准后再评估。

---

## 九、下一步（阶段 P 剩余）

- **P5.2 案例库**：批量跑 matcher → 每 pattern 样本集 → 查后续 1/3/5/10 日演化。
  **前置阻塞**即 §七 的两项数据补齐，否则案例库的时间维度基本为空。
- **P2.3 社交覆盖率表**：`JEVSocialDaily` 每日 18:30 前向累积快照，随天数增加覆盖面。
- **P5.3 / P5.4**：演化路径统计 + 新验证器 CLI（校准 `Thresholds`、输出分化度）。
- **P6**：判定闸评估，条件①按 §七 结论重判。
