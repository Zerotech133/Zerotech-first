# JEV · Δmin 时间维度定义文档（P1.4）

> 配套：`docs/JEV_DeltaMin_可用性扫描.md`（P1.1–P1.3 实证）、`docs/plans/2026-11-10_统一真相源_能力账本+执行计划_v2.md`（§六 判定闸、阶段 P）。
> 本文 = Δmin 的**精确定义 + 计算逻辑 + 边界处理**。只定义、不实现推演权重（权重在 P5 新验证器校准，不在 P1 定死）。
> 目的：钉死"价格先行 / 消息追认"两个核心概念，绕开判例 #25③"拿不存在的时间戳算领先分钟"的坑。

---

## 〇、一句话定义

**Δmin = T_news − T_limit（带符号，单位分钟）**：新闻事件时间与价格封板时间的差。
- **Δmin > 0（价格先行 / 消息追认）**：价格先封板，新闻后发布 → 新闻是"事后解释/追认"。
- **Δmin < 0（消息驱动）**：新闻先出，价格后封板 → 消息是"事前催化"。
- **Δmin ≈ 0（同步）**：无明确因果方向信号，Δmin 维度对该样本不提供区分度。

> **关键纪律**（判例 #25③）：Δmin 只在**两个时间锚点都存在且真实**时计算。任一锚点缺失（无分钟戳 / 无 stk_mins / 无关联股票）→ 该样本 Δmin = None，标记为 `unpriced`，**绝不拿默认时间或邻近时间硬算**。

---

## 一、两个时间锚点的精确定义

### 1.1 T_limit（价格侧，来自 stk_mins 活源）

**数据契约（钉死，禁止绕开）**：
- 价格源 = `quant_backtest/data/tushare/stk_mins/`（活源，覆盖到 2026-11，每日 15:39 更新）。
- **禁止**用 `tushare_curated/stk_mins.parquet`（只到 2026-10-23，含不了近期案例）。
- 文件定位：`{code 去点}_{ex}_{YYYYMM}.parquet`（例 `689009_SH_202611.parquet`）。
- 轻量读：`pyarrow.read_table(path, filters=[("ts_code","=",code), ("trade_time",">=",day+" 09:00:00"), ("trade_time","<=",day+" 15:00:00")])`，避免读 1.47 亿行全量。

**计算依赖**：
- 列：`trade_time, close, high, low, vol, amount`。
- 涨停价 `limit_price` 需要 `pre_close`：取 `quant_backtest/data/tushare/daily/d_{day}.parquet` 的 `pre_close`（= 昨收，即 D 日涨跌幅基准）。
- 档位与封板判定**复用** `portal/jev_graph.py::limit_state(close, high, pre_close, code6, name, pct=None)`：
  - 返回 `(limit_up, touch_only, ratio, limit_price)`。
  - `limit_up = |close − limit_price| ≤ SEAL_TOL`（收盘封板）。
  - `touch_only = high ≥ limit_price − SEAL_TOL 且 close < limit_price`（盘中触板、收盘炸板）。
  - `limit_price = round(pre_close × (1 + ratio), 2)`，`ratio` 由 `limit_ratios(code6, name, pct)` 按规则定（主板 10% / 双创 20% / ST 5% / 北交所 30%）。
  - `SEAL_TOL = 0.011`（一分钱容差）。

**两个派生锚点**（用途不同，都保留）：
| 锚点 | 定义 | 计算 | 语义角色 |
|---|---|---|---|
| `T_limit_touch` | 首次触板时刻 | 当日分钟序中，第一根 `high ≥ limit_price − SEAL_TOL` 的 `trade_time` | 价格"开始动"的最早点（试探信号） |
| `T_limit_seal` | 首次封板时刻 | 当日分钟序中，第一根 `limit_state(...).limit_up == True` 的 `trade_time` | 价格"锁死涨停"的点（主 Δmin 锚点） |

> **主锚点 = `T_limit_seal`**（封板）。理由：封板后价格已锁死涨停，此时发布的新闻才是明确的"追认"；仅触板又炸板的 `touch_only` 状态模糊，不单独用作主锚点，仅作为辅助信号记录 `T_limit_touch`。

**边界**：若当日该票无一根 `limit_up` 分钟（即全天未封板）→ `T_limit_seal = None`（即便触及过也只记 `T_limit_touch`，主锚点缺失）。若 `pre_close` 缺失或 ≤0 → `limit_state` 直接返回 `(False,False,None,None)`，`T_limit_* = None`（unpriced）。

### 1.2 T_news（新闻侧，来自 news.ts）

**数据契约（钉死）**：
- 库：`news_collector/news.db` → 表 `news(id, source, ts, title, content, created_at)`。
- 事件时间取自 `ts` 列，经 `news_collector/news_time.py::parse_news_time(ts, reference=created_at, source=source)` 解析：
  - 完整 ISO 时间（含日期）→ 直接解析为 CST datetime。
  - 短格式 `"MM-DD HH:MM"` → 用 `created_at` 年份做邻近推断（ref±183 天），且普通快讯事件时间不得晚于入库时间 >5 分钟（announcement/survey 容许未来日）。
  - 解析失败 → 返回 `None`（该条无可用分钟戳）。
- **新闻源白名单（真实分钟级，Δmin 可用）**：`sina / eastmoney / wallstreetcn / chinanews / world / xd_news / wechat_kol / govpolicy / xq_kol`（扫描实测 99–100% 带分钟戳）。
- **日级追认源（不进分钟 Δmin）**：`announcement / xd_notice / report / survey`（天然只有日期，属"追认"信号，见 §三）。

**锚点派生**（同一票当日可能多条新闻）：
| 锚点 | 定义 | 用途 |
|---|---|---|
| `T_news_first` | 该票当日相关新闻中，`parse_news_time` 成功且最早的 `ts` | 判定"消息是否先于价格" |
| `T_news_last` | 该票当日相关新闻中，最晚的 `ts` | 判定"消息是否全在封板后追认" |
| `T_news_set` | 全部成功解析的新闻 `ts` 集合（含 source 标注） | pattern 匹配时的完整证据 |

**股票关联**：新闻→票的映射以 `news.stocks` 字段为准（逗号分隔的 `ts_code` 列表）。`stocks` 为空的条 → 无法对应到票 → 该条不参与 Δmin（但可作为板块级/无指向信号另用，不在本文范围）。

---

## 二、Δmin 计算与符号约定

对单只票、单交易日，给定 `T_limit_seal`（主锚点）与 `T_news_first`/`T_news_last`：

```
Δmin_seal_first = (T_news_first − T_limit_seal)   # 单位：分钟，带符号
Δmin_seal_last  = (T_news_last  − T_limit_seal)
```

**符号 → 因果方向判定**：
| 条件 | 判定 | 含义 |
|---|---|---|
| `T_limit_seal is None` | `unpriced` | 该票当日未封板或无分钟数据 → Δmin 不可用，剔除 |
| `T_news_first is None`（白名单源全无分钟戳） | `unpriced` | 无可用新闻时间 → Δmin 不可用 |
| `Δmin_seal_first > +TH_LEAD`（如 +5 分钟） | **价格先行 / 消息追认** | 封板后 ≥5 分钟才有新闻 → 新闻是事后解释 |
| `Δmin_seal_last  < −TH_DRIVE`（如 −5 分钟） | **消息驱动** | 新闻最晚一条也比封板早 ≥5 分钟 → 消息是事前催化 |
| 介于 `[-TH_DRIVE, +TH_LEAD]` | **同步 / 无区分** | 新闻与封板时间纠缠，Δmin 不提供方向信号 |

> **阈值 `TH_LEAD` / `TH_DRIVE`**：建议默认值 ±5 分钟（过滤新闻采集延迟造成的 ±1~2 分钟噪声）。**具体阈值须经 P5 新验证器在案例库上校准**，不在 P1 定死；此处只给建议起点。

**辅助信号（不进主 Δmin，供 pattern 交叉）**：
- `touch_lead_min = T_limit_touch − T_news_first`：若价格先"触板试探"再炸板、新闻其后才出，亦算价格先行的一种弱形态。
- `echo_ratio`（判例 #25 要求"消息驱动必须先用 echo/hit 比例验证"）：消息驱动型样本里，新闻发布后价格**实际封板**的比例。若 echo_ratio 低，则"消息驱动"只是噪声，不算真信号。

---

## 三、边界处理（5 类，缺一不可）

### 3.1 新闻无分钟戳（announcement / xd_notice / report / survey）
- 这些源 `ts` 天然只有日期（扫描实测 0% 分钟级），`parse_news_time` 对它们通常返回 `None` 或仅日期。
- **处理**：不进分钟级 Δmin。降级为**日级追认** co-signal：若 `T_limit_seal` 存在且新闻 `created_at` 日期 == 涨停日（盘后公告）或次日（T+1 追认）→ 标记为 `confirm_daily`（日级追认），参与"价格先行 + 盘后/次日公告追认"的粗粒度 pattern。
- **绝不**用 `created_at`（入库时间）冒充事件时间算 Δmin。

### 3.2 价格侧无 stk_mins（票不在活源目录 / 当日无分钟文件）
- `covered(code, day)` 返回 False，或文件存在但目标日行数 < 200。
- **处理**：`T_limit_seal = None` → Δmin = `unpriced`，该样本退出 Δmin 维度计算（但资金/社交维度仍可用，只是三维度组合里 Δmin 缺失）。
- **补救**：走 `tools/jev_mins_fetch.py --day YYYYMMDD --codes <code>` 按需补拉（幂等、写前备份、原子替换），补完重算。

### 3.3 多新闻对应一票
- 同一 `ts_code` 当日可能有多条白名单源新闻（不同 source / 不同时间）。
- **处理**：用 `T_news_first` 与 `T_news_last` 双锚点，而非单条：
  - 若 `T_news_last < T_limit_seal − TH_DRIVE` → 全部新闻都在封板前 → **消息驱动**强。
  - 若 `T_news_first > T_limit_seal + TH_LEAD` → 全部新闻都在封板后 → **价格先行/追认**强。
  - 若新闻时间横跨封板时刻 → 标记为 `mixed`，需结合 echo_ratio 与资金维度判断，不单独给方向。

### 3.4 盘前 / 盘后新闻
- `ts` 落在 `09:00` 前或 `15:00` 后（如 08:30 早间快讯、16:00 收盘后公告）。
- **处理**：仍按真实时间参加 Δmin 计算（可正可负，跨盘口），但额外标注 `is_session`：
  - 盘前新闻（`is_session=False, pre_market=True`）：若 `< T_limit_seal` → 计入"消息驱动"但权重打折（盘前消息催化常见，区分度低）。
  - 盘后新闻（`is_session=False, post_market=True`）：几乎必然 `> T_limit_seal` → 天然是"追认"，不提供领先信息，仅作 confirm 旁证。

### 3.5 新闻无关联股票（stocks 为空）
- `news.stocks` 为 NULL/空串 → 无法映射到具体票。
- **处理**：该条不进任何个股 Δmin。可保留作板块级/全市场情绪信号（不在本文 Δmin 口径内）。

---

## 四、与三维度交叉的语义（Δmin 如何进入 pattern）

Δmin 不是孤立指标，它在组合 pattern 里提供**因果方向**维度。与另两维度交叉：

| 维度 | 信号 | 与 Δmin 的交叉语义 |
|---|---|---|
| 资金 | `main_net>0 & net_mf<0`（主力接而全档逃，divergence） | Δmin 价格先行 + 资金背离 = "有人提前知道、拉板后散户接盘"的强 pattern（散户接盘型） |
| 社交 | 股吧热度飙升 + 雪球机构沉默 | Δmin 价格先行 + 社交过热 = 价格已封板、舆情才追涨 → 散户接盘确认 |
| 结构 | （P3 决策：建议放弃，用 Δmin 替代） | — |
| Δmin | 价格先行 / 消息追认 / 消息驱动 | 提供"谁先动"的因果方向，是推演引擎区分路径分化节点的核心 |

**pattern 命中示例**（与 P4 枚举对齐）：
- **散户接盘型**：Δmin 价格先行（封板后新闻追认） + 资金背离 + 社交过热 → 预期演化：短期回落。
- **真突破型**：Δmin 价格先行（先有资金动、后有研报/公告追认） + 主力接且全档流入 + 机构热榜 → 预期演化：继续涨。
- **消息驱动型**：Δmin 消息驱动（新闻先出 ≥5 分钟、且 echo_ratio 高） + 资金中性 → 预期演化：消息落地后延续一段。

---

## 五、计算伪代码（引用真实函数，供 P5 实现）

```python
# 依赖（均已存在，禁止重写）：
#   portal/jev_graph.py :: limit_state, limit_ratios, SEAL_TOL, resolve_day
#   news_collector/news_time.py :: parse_news_time
#   quant_backtest/data/tushare/stk_mins/{code}_{ex}_{YYYYMM}.parquet
#   quant_backtest/data/tushare/daily/d_{day}.parquet  (pre_close)

TH_LEAD, TH_DRIVE = 5, 5   # 建议起点，P5 校准

def t_limit_seal(code, day, pre_close, name):
    """返回该票当日首次封板 trade_time（datetime）或 None。"""
    path = f"{STK_MINS}/{code.replace('.','_')}_{day[:6]}.parquet"
    if not os.path.exists(path):
        return None
    t = pq.read_table(path, filters=[("ts_code","=",code),
                                     ("trade_time",">=",f"{day[:4]}-{day[4:6]}-{day[6:]} 09:00:00"),
                                     ("trade_time","<=",f"{day[:4]}-{day[4:6]}-{day[6:]} 15:00:00")])
    df = t.to_pandas()
    if pre_close is None or pre_close <= 0:
        return None
    c6 = code[:6]
    for row in df.itertuples():
        lu, _touch, _r, _lp = limit_state(row.close, row.high, pre_close, c6, name)
        if lu:
            return parse_full_time(row.trade_time)   # 首次封板
    return None

def t_news_anchors(code, day):
    """返回 (T_news_first, T_news_last, T_news_set) 或 (None,None,[]) 。"""
    rows = db.execute(
        "SELECT ts, source, created_at FROM news WHERE stocks LIKE ? "
        "AND source IN (白名单) AND created_at >= ?",
        (f"%{code}%", f"{day[:4]}-{day[4:6]}-{day[6:]}"))
    times = []
    for ts, source, created_at in rows:
        dt = parse_news_time(ts, reference=created_at, source=source)
        if dt is not None:
            times.append(dt)
    if not times:
        return None, None, []
    return min(times), max(times), times

def compute_delta_min(code, day, name, pre_close):
    t_lim = t_limit_seal(code, day, pre_close, name)
    t_first, t_last, _set = t_news_anchors(code, day)
    if t_lim is None or t_first is None:
        return None, "unpriced"          # 判例 #25③：锚点缺失即退出，绝不硬算
    d_first = (t_first - t_lim).total_seconds() / 60.0
    d_last  = (t_last  - t_lim).total_seconds() / 60.0
    if d_first > TH_LEAD:
        return d_first, "price_lead"     # 价格先行 / 消息追认
    if d_last < -TH_DRIVE:
        return d_last, "news_drive"      # 消息驱动
    return d_first, "sync"               # 同步 / 无区分
```

---

## 六、正确性约束（绕开判例 #25③）

1. **任何锚点缺失 → 返回 `unpriced`，不填充、不邻近、不默认**。这是硬纪律，违反即复现旧坑。
2. **价格源只用活源 `stk_mins/`**，禁止 `tushare_curated/stk_mins.parquet`（缺 2026-11）。
3. **新闻时间只用 `parse_news_time(ts, ...)` 的解析结果**，禁止用 `created_at` 冒充事件时间。
4. **白名单外源（announcement/report/xd_notice/survey）不进分钟 Δmin**，只走日级追认通道。
5. **社交维度不参与分钟 Δmin**（social_*.json 无逐条时间戳），只做日级 co-signal（P2 范畴）。
6. **轻量读**：pyarrow `filters` 按票+日切片，禁止读 1.47 亿行全量。

---

## 七、与阶段 P 其他任务的接口

| 下游任务 | 本文提供的接口 |
|---|---|
| **P2 社交历史回补** | Δmin 不依赖社交；P2 完成后社交走日级 co-signal，与本文件 §三/§四 的社交维度并列 |
| **P3 结构维度决策** | 建议放弃结构维度、用 Δmin（时间维度）替代；本文件 §四 已把 Δmin 列为三维度之一 |
| **P4 pattern 枚举** | §四 给出 3 个 pattern 的 Δmin 命中条件示例；P4 据此扩展 5–10 个并写明触发条件 |
| **P5 matcher + 新验证器** | §五 伪代码可直接落地；`TH_LEAD/TH_DRIVE` 与 Δmin 权重在 P5 用案例库校准 |
| **P6 判定闸** | 本文支撑判定闸 A 条件①（已满足）的"可用性"细化为"可计算性"；条件②③在 P4/P5 后评估 |

---

## 八、待 P5 校准、不在 P1 定死的项

- `TH_LEAD` / `TH_DRIVE` 阈值（建议 ±5 分钟起点）。
- Δmin 在组合 pattern 中的权重（价格先行 vs 消息驱动 vs 同步，如何与资金/社交维度加权）。
- `echo_ratio` 的合格线（消息驱动型要求多高比例的"发消息后真封板"才认）。
- 盘前/盘后新闻的权重折让系数。
