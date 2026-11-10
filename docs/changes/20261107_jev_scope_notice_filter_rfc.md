# RFC 20261107 · JEV 公告类（xd_notice / announcement）判定范围治理

- 状态：**已实施（2026-11-07，老田批准 A+C）** —— 补丁 + 19 项单测落地，
  核心回归 84 passed / tools 29 passed；生产 `decide()` 验收复算 812 skip / 0 误杀。
  实施明细见 §11；原始评估阶段结论保留在下文，便于对照。
- 提出：2026-11-07
- 关联：K-ORG-20261026-JEV2STAGE（两段式预筛）、PREFILTEREVAL（2026-10-30，旧全量 skip 误杀率 58%）、RFC-20261106（M3 锁竞争积压出口）、JEV 积压告警（`alert-threshold=100`）
- 候选影响组件（**需批准才动**）：`jev_prefilter.py`、`jev_backfill.py`、`jev_feed.py`、`jev_night.py` / `tools/night_batch.py`、告警口径

---

## 0. 结论先行（给决策者）

**按字面「把两类公告排除出 JEV 判定队列」不可行。** 近 30 日 18,685 条已判公告里，有 **2,306 条（12.34%）被判为 bull/bear**；按 source 全排除等于把这些方向性判定全部丢掉，方向召回率 0%。公告的 neutral 率高（87–88%），不等于「无价值」。

同时，巡检暴露的真问题有两层，比「算力」更值得先修：

1. **告警/竞争口径把低价值公告算进 `left`**：`_backlog_left()` 是一条不带 source 条件的裸 COUNT，既驱动飞书告警（`alert-threshold=100`），也驱动 RFC-20261106 新加的「积压 ≥150 就参与锁竞争」。500 条例行公告足以让两者长期处于触发态。
2. **每日 500 条是自家采集上限，不是外部真实量**：`jev_feed.py --cap 500` 拉满即截断，且夜窗（21:45–07:05）`judge_skipped_night=true` 不判，全部留给夜批 → 每天 00:00 前后集中灌一批。

**推荐 A + C 组合**：扩 safe 白名单模板（零误杀）+ 告警/竞争阈值按高价值口径分离。不动采集层、不删数据、不丢任何判定、一键可退。若你坚持「每天实省 500–700 条算力」，则必须批准 B，并显式接受方向性判定损失或 6–8% 误杀。

---

## 1. 现象与数据证据

### 1.1 积压构成（2026-11-06 23:45 → 11-07 00:37 实测，库持续增长，均为滚动快照）

| 时刻 | `left` | 说明 |
|---|---|---|
| 11-06 16:50 | 716 | 告警 `alerted=1` |
| 11-06 23:45 | 883 | 夜批启动前 |
| 11-07 00:13 | 1270 | 夜批 ROUND 1 判 300 条后仍被入库冲高 |
| 11-07 00:19 | 1182 | ROUND 2 在跑 |
| 11-07 00:31 | 1019 | ROUND 2 持续消化中 |
| 11-07 00:37 | 1032 | 复核时点，ROUND 2 仍在跑 |

`xd_notice` 每天 00:00 固定灌 500 条（11-04 / 11-05 / 11-06 各 500，`ts` 均为当日 00:00），今日 500 条全未判；历史共 7,241 条而 `left` 仅 500 → 属每日批量待判，不是历史漏判堆积。

### 1.2 公告类的判定价值（近 30 日，`ts >= 2026-10-08`）

| source | 已判 | neutral | neutral 率 |
|---|---|---|---|
| `xd_notice` | 6,711 | 5,908 | **88.0%** |
| `announcement` | 11,974 | 10,471 | **87.4%** |
| `xd_news`（对照） | 2,931 | 801 | 27.3% |
| `sina`（对照） | 43,190 | 15,313 | 35.5% |

两类公告合计 19,213 条，占近 30 日全库 114,763 条的 **16.7%**。信息密度确实远低于新闻源，但 12% 的方向性条目是真实存在的。
（以上为 2026-11-07 00:37 只读快照，原始复核见 §10；夜批运行中，个位数会滚动变化。）

### 1.3 关键更正：500 条/日 = 自家 cap，不是外部量

`news_collector/jev_feed.py:20,262` 默认 `--cap 500`（每源单轮拉取/判定上限）。11-06 23:49 的 `runs` 记录：

```json
{"wm":{"news":"20261106","notice":"20261106"},
 "src":{"news":{"inserted":150,"fetched":500,"judge_skipped_night":true},
        "notice":{"inserted":500,"fetched":500,"judge_skipped_night":true}},
 "errors":[]}
```

`fetched=500` 拉满 cap、`inserted=500` 全新增、`judge_skipped_night=true`（夜窗跳判）。水位在截断轮仍推进（L290-303「排水/活锁破除」），**cap 之外的公告可能被跳过去**，这一点本 RFC 不处理，见 §6 附注 P1。

### 1.4 三个判定入口（改动范围必须覆盖全部，否则会漏网）

| 入口 | 文件 | 行为 |
|---|---|---|
| 日间 feed 顺带判 | `jev_feed.py:196 judge_pending()` | `WHERE source=? AND (jev IS NULL OR jev='')`，cap=500；夜窗直接 SKIP |
| 整点回填 | `jev_backfill.py:398` | `WHERE (jev IS NULL OR jev='') ORDER BY ts DESC LIMIT ?` |
| 夜批 | `tools/jev_night.py` / `tools/night_batch.py` | 循环调回填，21:50–07:00 |

---

## 2. 现状机制：为什么今晚 `prefilter ON: skip=0`

`JEV_PREFILTER=safe` 已在生产常开（`jev-eval/jev_backfill_task.ps1:18`、`jev-eval/start_jev_night.ps1:11`、`tools/night_batch.py:404`），且 `tools/jev_night_preflight.py:136` 强制校验其存在。

`jev_prefilter.prefilter_jev()` 的 skip 不是丢弃：写回一条与 LLM 判定同 schema 的 neutral，带 `src=prefilter` 和 `pref.{reason,matched,source}` 证据，可被 `is_prefilter_judgment()` 识别并批量回退。

`decide()` 四道守门（L136-187）：

```python
d = directional_hit(text)
if d:  return keep("方向性词")            # 宁可多判
...
has_stock_code = bool(re.search(r"(?<!\d)\d{6}(?!\d)", text))
n = safe_neutral_hit(text)                # 仅 9 条 routine 治理模板
if n and has_stock_code: return keep("含个股代码")
if n:  return skip("例行/行情")
return keep("未命中中性模式")
```

**`skip=0` 的三个原因**（离线复算当前 1,019 条未判）：

| 分类 | 条数 | 占比 |
|---|---|---|
| keep_其他（例行公告但模板未覆盖） | 724 | 71.1% |
| keep_方向词 | 221 | 21.7% |
| **skip_safe 模板命中** | **44** | **4.3%** |
| keep_含代码 | 30 | 2.9% |

其中 `xd_notice` 500 条：safe 命中仅 **43 条（8.6%）**、方向词 96 条、其余 361 条是「投资者关系活动记录表 / 委托理财进展 / 持续督导意见 / 关于控股股东股权质押的公告」这类例行公告——**模板没覆盖，但方向词又误命中**（`质押`、`回购` 都在方向词表里）。`announcement` 标题形如 `[603284 林平发展] ...`，6 位代码直接触发保守保留。

所以：**不能靠「扩方向词/放宽守门」来提覆盖率**，那正是 2026-10-30 PREFILTEREVAL 判死刑的 58% 误杀路径。

---

## 3. 离线评估（18,685 条已判公告，纯函数复算）

Ground truth = 现有 9B 模型写入的 `jev.bias`。评估的是**「新策略与现状判定的一致性」**，不是业务正确性；模型自身判错的部分不在本评估范围内。

| 策略 | skip 数 | skip 率 | 误杀数 | 误杀率 | 方向召回 |
|---|---|---|---|---|---|
| P1 按 source 全排除 | 18,685 | 100.0% | 2,306 | 12.34% | **0.0%** |
| P2 source + 无方向词 | 15,321 | 82.0% | 952 | 6.21% | 58.7% |
| P3 P2 + 无 6 位代码 | 5,691 | 30.5% | 407 | 7.15% | 82.4% |
| P4 P3 + 无高价值例外词 | 5,258 | 28.1% | 403 | 7.66% | 82.5% |
| P5 仅 safe 模板命中（不含代码守门） | 2,573 | 13.8% | 1 | 0.04% | 100.0% |
| P6 P4 ∪ safe | 6,785 | 36.3% | 404 | 5.95% | 82.5% |
| **P5′ 生产等价**（无方向词 + safe 命中 + 无 6 位代码） | **1,046** | **5.6%** | **0** | **0.0%** | **100.0%** |

> 口径说明：`decide()` 在 safe 命中后还会因「标题含 6 位个股代码」而 keep（L160-172），
> `announcement` 标题形如 `[603284 林平发展] ...` 大多命中，所以**生产实际覆盖是 P5′ 的 5.6%**，
> 而不是 P5 的 13.8%。P5 只用于衡量模板本身的召回上限。两者误杀都≈0。

**读法**：只有 P5 / P5′ 达到可接受的精度（1/18,685 与 0/18,685）。任何把「source」当作独立 skip 依据的策略，误杀率都在 6% 以上——因为公告的方向性常藏在正文/语义里，标题规则捕捉不到。这直接否定了「按 source 排除」的实现路径。

---

## 4. 方案对比

### A｜扩 safe 白名单模板（推荐，零误杀）

落地实现（`jev_prefilter.py`）：**独立 `notice_hit()` + 按 source 限定**，不改 `_SAFE_RE`
（它是模块加载时编译的列表，直接 `+=` 不生效；独立函数同时让 `reason` 可区分）：

```python
NOTICE_SOURCES = ("xd_notice", "announcement")
NOTICE_ENV = "JEV_PREFILTER_NOTICE"
NOTICE_REASON = "例行公告"
ROUTINE_NOTICE_PATTERNS = (
    r"投资者关系活动记录表",
    r"(业绩说明会|投资者说明会).{0,8}(公告|通知|召开)",
    r"(内部控制|内控|审计).{0,10}(制度|报告|评价)",
    r"关于召开.{0,24}(股东会|股东大会).{0,6}(通知|提示)",
)

def notice_enabled(source="", mode="safe"):
    return mode == "safe" and source in NOTICE_SOURCES and _env_on(NOTICE_ENV)

# decide() 中：旧 safe 模板未命中才试 notice_hit，命中写 reason="例行公告"，
# 方向词 / 6 位代码两道守门对两者一视同仁；旧模板命中保持 reason="例行/行情"。
```

**逐模板抽检结果（近 30 日，生产守门条件：无方向词 / 无 6 位代码 / 未命中旧 safe 模板）**：

| 模板 | 命中 n | 误杀 | 判定 |
|---|---|---|---|
| 业绩说明会通知 | 389 | 0 | ✅ 采用 |
| 投资者关系活动记录表 | 219 | 0 | ✅ 采用 |
| 会议通知 | 183 | 0 | ✅ 采用 |
| 制度内控报告 | 21 | 0 | ✅ 采用 |
| 现金管理进展 | 44 | 14（31.8%） | ❌ 剔除 |
| 闲置募集资金现金管理 | 76 | 22（28.9%） | ❌ 剔除 |
| 进展公告宽匹配 | 54 | 17（31.5%） | ❌ 剔除 |
| 募集资金存放使用 | 50 | 14（28.0%） | ❌ 剔除 |
| 更正补充说明 | 37 | 5（13.5%） | ❌ 剔除 |
| 持续督导/核查意见 | 126 | 12（9.5%） | ❌ 剔除 |
| 工商变更完成 | 75 | 3（4.0%） | ❌ 剔除 |
| 委托理财进展 / 权益变动报告书 | 8 / 15 | 0 | ⚠️ n<20 样本不足，不入列 |

**实施中新增的关键发现（已写进代码注释）**：模板必须按 source 限定。
「投资者关系活动记录表」在其他源 23 命中 **13 误杀（56.5%）**、「制度内控报告」4 命中
2 误杀（50%）——新闻源里这类标题多为调研纪要，模型会判出方向性。限定后其他源 skip=0。

**实测收益（修正预估）**：生产 `decide()` 全量复算 = 公告类 **812 skip / 0 误杀**、
其他源 0 skip；当前 881 条未判中可安全 skip **109 条（12.4%）**。
按每日 500 条 `xd_notice` 的命中率外推，**日省算力约 100–150 条**——低于立项时预估的
250–350 条，更达不到 500–700 条。原因是「零误杀」约束下只有 4 条模板合格，
大量例行公告（委托理财/现金管理/募集资金）标题与方向性事件混杂，规则无法安全区分。
- 误杀：**实测 0.00%**（不是「目标 <0.1%」）
- 不动采集层写入（`INSERT`/`source`/`--cap`/水位）、不动 `news.db` schema、不删历史 jev

### B｜source 分层排除 + 影子抽样（能达到用户原目标，但要接受损失）

在 `jev_backfill.py` 取批前按 source 分层，公告类走「规则判定 + 写带证据 neutral」，并抽 10% 仍走 LLM 做漂移哨兵。等价于 P1/P2 上线：

- 每日省算力 **500–700 条**（达标）
- 代价：P1 丢 12.34% 方向性判定 / P2 误杀 6.21%
- 需要业务上明确裁定「公告类方向性不纳入 JEV 口径」，且**会改变 JEV 评测/复盘报表的历史可比性**（下游消费方见 §5）
- 必须同步改三个入口（§1.4），否则 feed 仍会判

### C｜判定降权 + 告警口径分离（零数据损失，治「告警被淹没」）

不动任何排除逻辑，只改两处：

1. 取批排序按 source 分层，高价值源优先：
   ```sql
   ORDER BY CASE WHEN source IN ('xd_notice','announcement') THEN 1 ELSE 0 END,
            ts DESC LIMIT ?
   ```
2. 新增 `_backlog_left_high()`（排除公告类）单独供告警与竞争判定使用，原 `_backlog_left()` 保持不变供 `remaining` 统计。

- 每日省算力 **0 条**，但告警与 RFC-20261106 的 150 阈值不再被 500 条例行公告顶死
- 零误杀、零口径变化（判定照旧），只是「先判重要的」

> **耦合警告**：`_backlog_left()` 目前同时被 `alert_backlog()` 和 RFC-20261106 的 `_decide_skip()` 使用。C 若改口径，两处语义会一起变；必须新增独立函数而非原地改写，否则方案 B（锁竞争）的 150 阈值失去原义。

### 对比表

| 维度 | A 扩模板 | B source 排除 | C 降权+分口径 | **A+C（推荐）** |
|---|---|---|---|---|
| 每日省算力（实测修正） | 100–150 | 500–700 | 0 | 100–150 |
| 方向性损失 | ~0 | 6–12% | 0 | ~0 |
| 告警不再被淹没 | ✗ | ✓ | ✓ | ✓ |
| 改动文件数 | 1 | 3–4 | 1–2 | 2–3 |
| 是否动采集层 | 否 | 否（但需改 3 入口） | 否 | 否 |
| 一键回滚 | `JEV_PREFILTER=0` | 需 SQL 还原 | env 关 | env 关 |
| 评测报表历史可比性 | 保持 | **破坏** | 保持 | 保持 |

---

## 5. 影响面与边界（**待批范围勾选清单**）

**明确不动（除非你另行批准）**

- ☐ 采集层写入：`jev_feed.py` 的 `INSERT`、`source` 取值、`--cap`、水位推进策略
- ☐ 删除任何历史 `jev` 数据；`news.db` 表结构（不改 schema、不加列）
- ☐ `news_ai.jev_judge_batch()` 与本地模型调用链
- ☐ 生产启动脚本（ps1）中的 `JEV_PREFILTER=safe` 常开设置
- ☐ RFC-20261106 已落地的 `_decide_skip()` / 150 阈值逻辑本身

**实际落地文件（4 个，均在 `C:/aios`）**：
- `news_collector/jev_prefilter.py`：4 条模板 + `notice_enabled/notice_hit` + `decide()` reason 分流
- `news_collector/jev_feed.py`：`judge_pending()` 接 `prefilter_jev()`、items 带 `source`、`runs.detail` 记 `pref_skipped`（三入口一致性）
- `news_collector/jev_backfill.py`：`_backlog_left_high()`、`_batch_order_clause()`、告警双口径
- `news_collector/test_jev_notice_scope.py`：19 项单测（含 feed 入口实测、跨源误杀回归）

**方案 C 要动**：`jev_backfill.py`（取批 ORDER BY、新增 `_backlog_left_high()`、`alert_backlog` 入参）、对应单测

**方案 B 要动**（不推荐）：上述 + `jev_feed.py:judge_pending()` + 夜批 env 透传 + 影子抽样模块 + 报表口径说明

**`jev` 下游消费方**（改口径会影响）：`daily_report.py`、`jev_review_queue.py`、`news_digest.py`、`news_jev_chart.py`、`sentiment_backtest.py`、`jev-eval/jev_drift.py`、`portal/api_jev.py` 等 20+ 文件。

---

## 6. 风险与缓解

| 风险 | 严重度 | 缓解 |
|---|---|---|
| 新模板误杀方向性公告 | P1 | 逐模板 n≥20 人工抽检，误杀 >1% 的模板不入列；上线后影子抽样 10% 持续监测 |
| 模板漂移（公告标题改版） | P2 | 每月用新增已判数据重跑 §3 评估，`skip` 率突降即告警 |
| 双通道漏网（feed 判了 / 回填排除了） | P1 | 三入口统一调 `prefilter_jev()`；验收用例覆盖 feed 路径 |
| 告警口径变化导致该响不响 | P1 | C 保留原 `_backlog_left()`，双口径同时打印到日志，灰度期并跑对照 |
| 与 RFC-20261106 的 150 阈值语义漂移 | P1 | 见 §4 耦合警告，独立函数不原地改写 |
| **附注 P1**：`--cap 500` 截断轮仍推进水位 → **已确认数据丢失** | **P0** | 2026-11-07 只读审计完成，证据与修复方向见 §12；**修复需单独批准** |

---

## 7. 回滚

1. **总开关**：`JEV_PREFILTER=0`（off）立即关闭全部规则粗筛，条目回到 LLM 判定队列；三个 ps1 启动器改一行即可。
2. **新增模板独开关**：`JEV_PREFILTER_NOTICE=0` 只关本次新增模板，保留原有 9 条 safe 模板。
3. **数据级还原**（skip 行带证据，可精确回退）：
   ```sql
   UPDATE news
      SET jev = NULL
    WHERE jev IS NOT NULL
      AND json_extract(jev, '$.src') = 'prefilter'
      AND json_extract(jev, '$.pref.reason') = '例行公告';
   ```
   还原后下一轮回填/夜批会自动重判。
4. **方案 C**：新增 env `JEV_SCOPE_ALERT=0` 恢复原单口径；ORDER BY 改动用 `JEV_PRIORITY_BATCH=0` 关闭。

---

## 8. 验收用例与实施结果

**已通过（2026-11-07）**：用例 1（逐模板抽检，见表）、用例 2（核心回归 84 passed + tools 29 passed）、
用例 4（feed 单测实测公告不进 LLM、写 `src=prefilter`）、用例 5（双口径日志 + 告警消息）、
用例 6 的 SQL 标记（单测断言 `pref.reason=例行公告`）。
**仍待观察**：用例 3（7 天影子抽样一致率）、用例 7（上线前后 3 天收益对照）——需运行一段时间后取数。
**既存问题**：全量 `news_collector/test_*.py` 有 23 项失败（feishu / news_time / alert_delivery），
单独复跑同样失败且这些文件不引用本次改动的 3 个模块，判定为既存收集期/环境问题，非本次引入。

原验收清单：

1. 每条新增模板在近 30 日已判样本上 n≥20 抽检，误杀数 ≤1（>1% 即剔除该模板）。
2. 全量回归：`test_jev_prefilter*.py`、`test_jev_backfill_lock_order.py`、`test_jev_p1_fixes.py`、`test_jev_feed_runs_ok.py`、`tools/tests/test_prefilter_on.py` 全绿。
3. 精度基线：上线后 7 天影子抽样 10% 的 skip 条目，与 LLM 判定一致率 ≥99%。
4. 三入口一致性：构造一条例行公告，验证 feed / 整点回填 / 夜批三路都写 `src=prefilter` 而非各自判。
5. 告警双口径：同时打印 `left` 与 `left_high`，灰度期对照，确认无「该响不响」。
6. 回退演练：执行 §7 的 SQL + `JEV_PREFILTER=0`，验证条目重新进入待判队列并能被判回。
7. 收益复核：上线前后各取 3 天，对比日均 `judged`、日均 LLM 调用数、告警次数。

---

## 9. 待批决策点

| # | 决策 | 选项 |
|---|---|---|
| 1 | **路线** | **A+C（推荐）** / B / 仅 A / 仅 C / 暂缓 |
| 2 | **误杀容忍度** | 0.1%（A 的口径，推荐） / 接受 6–8%（可上 B-P2/P4） / 接受 12.34%（纯 source 全排除） |
| 3 | **是否允许改告警口径** | 允许新增 `left_high` 并跑（推荐） / 保持单口径不动 |
| 4 | **灰度周期与影子抽样比例** | 7 天 × 10%（推荐） / 3 天 × 20% / 直接全量 |
| 5 | **附注 P1 是否另立任务** | ✅ 审计已完成（§12，确认 P0 丢数）；待批：是否修采集层 `--cap` / 暂缓 |

**范围确认**：§5 的勾选清单请一并确认「不动」项是否需要放宽（尤其「是否允许动采集层 `--cap`」——若允许，B 的收益可以更高且更治本）。

---

## 10. 原始复核证据（防数字串味）

关键统计已落盘至 `C:/Temp/notice_scope_eval_facts.json`（文件回读，非 stdout 打印）：
`sample=18685`、`non_neutral=2306 (12.34%)`、`total_30d=114763`、`ann_30d=19213 (16.7%)`、
`P1 false_skip=2306 (12.34%)`、`P2 false_skip=952 (6.21%)`、`P5' skip=1046 (5.6%), false_skip=0`、
`current_left=1032`（00:37 快照）。

## 11. 实施记录（2026-11-07）

| 项 | 内容 |
|---|---|
| 批准 | 老田批复「A+C 补丁 + 单测」 |
| 改动文件 | `jev_prefilter.py`、`jev_feed.py`、`jev_backfill.py`、`test_jev_notice_scope.py`（新增） |
| 测试 | 新增 19 passed；核心回归 84 passed / 6 subtests；tools 相关 29 passed；三个改动模块 `py_compile` OK |
| 生产代码验收 | `C:/Temp/notice_scope_acceptance.json`：公告 812 skip / **0 误杀**，其他源 0 skip，当前 881 未判可 skip 109（12.4%） |
| 开关 | `JEV_PREFILTER_NOTICE=0`、`JEV_SCOPE_ALERT=0`、`JEV_PRIORITY_BATCH=0` 各自一键回退；总开关 `JEV_PREFILTER=0` |
| 生效方式 | 三个启动器（整点 ps1 / 夜批 ps1 / night_batch.py）无需改动，新起的进程自动加载新代码；**已在运行的进程仍持旧代码**，按各自轮次自然替换 |
| 未提交 | 4 个文件 + 本 RFC 均未 git commit |

**注意（2026-11-07 审计后修订）**：A 的实际收益（100–150 条/日）显著低于立项预估。
§12 的只读审计证明 `--cap 500` 正在造成**确凿的公告丢数**，且上游真实日量为 600–3,400 条。
因此「改从采集侧 `--cap` 入手」的性质与立项时的假设相反：**修 cap 是补数据完整性（必做），
但会把每日入队公告量从 500 抬到 600–3,400 条，算力压力上升 1.2–6.8 倍**。
届时过滤策略不是「省算力优化」，而是「判读链路能否存活」的前置条件 —— 方案 B 的
6–12% 方向性损失需要在那个新规模下重新评估，不能沿用本 RFC 的 18,685 条样本结论。

## 附录：评估口径（可复现）

```python
# 只读，连 news.db（mode=ro），不写库、不占 ollama_lock
rows = con.execute("""
    SELECT title, source, jev FROM news
     WHERE source IN ('xd_notice','announcement')
       AND jev IS NOT NULL AND jev<>'' AND ts>='2026-10-08'
""").fetchall()
gt = lambda j: json.loads(j).get('bias')       # ground truth = 现有 9B 判定
# 依次调用 jev_prefilter.directional_hit / safe_neutral_hit，
# 按策略组合判定 skip，统计 skip 中 gt!='neutral' 的比例
```


---

## 12. 附注 P1 审计结论：`--cap` 截断造成确凿丢数（2026-11-07，纯只读）

用户批复「接受 100–150 条/日 → 观察一周」后，按 RFC 附注 P1 做了只读审计。
**结论从「潜在丢数」升级为「已确认丢数，P0 数据完整性」**。所有关键数字均写入
`C:/Temp/*.json` 后文件回读复核，未凭 stdout 打印判断。

### 12.1 机制缺陷（代码级）

| 位置 | 行为 |
|---|---|
| `quant_backtest/datamart.py:616-618 / 675-678` | `sort_values(dcol, kind="stable")` 后 `df.head(limit)` —— **按日期升序取最早 N 条**，后续全部丢弃 |
| `news_collector/jev_feed.py:137` | `dm.read(alias, start=start, limit=cap)`，cap 缺省 500 |
| `jev_feed.py:144-147` | 拉满 cap 只打 `WARN`，不改变取数 |
| `jev_feed.py:163-169` | `max_date` 取**窗口内可解析行的最大日期**（含重复行），不是「已连续处理到哪天」 |
| `jev_feed.py:328-334` | **截断轮仍把水位推进到 `max_date`**（注释称「排水 / 活锁破除」） |

单纯截断本可靠「1 天重叠回看」自愈；但 `max_date` 用「窗口最大值」而非「连续水位」，
且 `dm.read` 的 `end` 参数在本场景未生效（见 12.3），返回的 500 条里**混有多个日期**，
于是水位一次性跳到最晚那天，中间日期的数据被永久跳过。

### 12.2 实测证据

**`runs` 表 24 轮 `jev_feed`（`C:/Temp/jev_cap_audit_facts.json`）**：
- `notice_fetched` 分布：`500` × 22 轮、`30` × 1、`5` × 1 —— **91.7% 的轮次拉满 cap**。
- `fetched=500 且 inserted=0` 的空转轮 **6 轮**（10-16 21:24 / 10-16 23:55 / 10-17 00:44 /
  10-27 10:38 / 11-02 23:18 / 11-03 21:00）。
- `xd_notice` 按日入库几乎恒为 **500**（11-04/05/06、10-22/23/24/26/27/28/29/30/31），
  且 **1,511 条的 `ts` 时分全是 `00:00`** —— 同日内没有亚日排序键，stable sort 保持物理顺序，
  每轮极可能取到同一批。
- **水位卡死序列（`C:/Temp/jev_feed_night_and_cap_facts.json`）**：
  `11-01 21:26 wm=20261101 ins=11` → `11-02 23:18 wm=20261101 ins=0` →
  `11-03 21:00 wm=20261101 ins=0` → `11-04 23:22 wm=20261104 ins=500`。
  **11-02、11-03 两天公告零入库，水位直接跳到 11-04。**

### 12.3 上游真实量（决定问题性质）

`dm.read('notice', start='20261102', end='20261102', limit=100000)` 返回 8,370 行，
但按 `notice_date` 分组显示 **end 过滤未生效**，返回的是 `start>=20261102` 的全量；
分组结果即真实单日量（`C:/Temp/jev_notice_upstream_verify.json`）：

| 日期 | 上游 `notice` 行数 | `news.db` 实际入库 | 差额 |
|---|---|---|---|
| 11-02 | 618 | **0** | 全丢 |
| 11-03 | 1,516 | **0** | 全丢 |
| 11-04 | 3,411 | 500 | −2,911 |
| 11-05 | 1,866 | 500 | −1,366 |
| 11-06 | 959 | 500 | −459 |

（11-06 当日采集可能尚未完结；`limit=None` 会被 `datamart` 退化成默认 200，
必须显式给大值，否则误判为「每天 200 条」。）

**所以「每天 500 条公告」不是业务量，是自家从 600–3,400 条里截出的最早 500 条。**

### 12.4 对决策的影响（推翻立项假设）

1. 方案 B 的「日省 500–700 条算力」是在给**一个丢数 90% 左右的管道**打补丁。
2. 修 cap 属于数据完整性必做项，但会把入队量抬到 **当前 1.2–6.8 倍**，
   JEV 判读压力不降反升；届时过滤策略从「可选优化」变成「链路存活前提」。
3. 本 RFC §3 的 18,685 条样本来自**截断后的 500 条/日**，代表性有偏；
   若真修 cap，A/B 的误杀率与收益**必须用全量样本重算**，不能沿用现有结论。
4. **建议顺序**：先立独立 RFC 修采集水位（`max_date` → 连续水位 / 分页拉取 / 去重游标），
   再在新规模下重新评估 A/B。本 RFC 不擅自改采集层（§5「明确不动」仍有效）。

### 12.5 修复方向（仅供批准用，未落地）

- 水位改为「已连续处理到的日期 + 稳定游标（如 `_fetched_at` / url hash）」，不用 `max_date`；
- 或 `dm.read` 分页（`limit=cap` + `offset`/游标）拉满当日再推进；
- 截断轮 `inserted=0` 不得推进水位（现「活锁破除」逻辑会把重复窗口当成成功）。
- 影响面：采集层、水位格式、`runs.detail`；需历史补数方案。**须老田单独批准。**
  **（2026-11-07 已立项独立 RFC：`20261107_jev_cap_data_loss_p0_rfc.md`，
  含方案①止血/②连续水位游标/③cap 语义重定义与待批决策点 D1–D4。）**

---

## 13. 「观察一周」观测机制（2026-11-07 已落地，纯只读）

### 13.1 工具

`C:/aios/tools/jev_scope_observe.py`：只读连 `news.db`、不调 Ollama、不抢
`ollama_batch.lock`、不改生产配置。每次产出
`C:/aios/jev-eval/review_data/jev_scope_observe_YYYYMMDD.json`，与最近一份相减得当日增量。

采集项：`left_total / left_high / left_low`、prefilter 落库按 `pref.reason` 分组
（`例行公告` vs `例行/行情`）、当日 9B 判定量、当日入库量、**新代码是否已在真实轮次生效**
（`notice_skip>0`）、历史同模板 9B neutral 率作为**离线代理**影子基线。

> 口径警示：这里的一致率是**离线代理指标**，不是 LLM 复判一致率。真实影子复判
> （抽 10% 重新调 judge 对比）会占用 Ollama 单槽与锁，须显式单独批准后才可执行。

### 13.2 基线快照（2026-11-07 00:59）

| 指标 | 值 |
|---|---|
| `left_total` | 827 |
| `left_high` | 299 |
| `left_low` | 528 |
| `notice_skip`（新模板） | **0** |
| `legacy_skip`（旧模板） | 214 |
| `new_code_effective_in_prod` | **False** |

**新代码尚未在真实轮次生效**：夜批 ROUND 3（00:35 起）与整点回填进程都启动于补丁落盘之前，
仍持旧代码；日志里没有 `BACKFILL scope ...`，`pref.reason` 全是 `例行/行情`。
按各自轮次自然替换后才会出现 `notice_skip>0`。这不是补丁失败，是进程生命周期问题。

### 13.3 观测期发现的两个实施期缺陷（须批准后修）

1. **方案 C 的「取批降权」在当前积压结构下是 no-op**。只读取批模拟：
   旧 `ORDER BY ts DESC LIMIT 389` → 公告 98 / 其他 291；
   方案 C `ORDER BY CASE ... , ts DESC LIMIT 389` → 公告 98 / 其他 291，**完全相同**。
   原因：高价值积压 291 < batch 389，排序差异被「补足」吃掉。降权只在
   `left_high >= batch_n` 时才产生差异，属**条件性保护**而非即时收益。
   C 真正即时生效的是告警 / 150 竞争阈值的双口径（827 → 299）。
2. **feed 入口的 prefilter 被夜窗守卫挡在前面**。`jev_feed.py:204-210` 的
   `_in_night_window()` 在取批与粗筛（L235+）之前 `return`；24 轮里按时间落在
   21:45–07:05 的有 **16 轮**。规则粗筛是纯 CPU 正则、不占 Ollama，
   **本不应受夜批独占窗口约束** —— 这是我接三入口时的位置失误，会显著压缩 A 的生效窗口。
   最小修法：把 prefilter 段提到夜窗守卫之前，让夜窗轮也做粗筛（skip 写带证据 neutral，
   夜批按 `jev IS NULL` 取全量，语义不变、可回退）。**2026-11-07 已获批准并实施，见 §14。**

### 13.4 观测计划

每日 09:00 自动跑一次，连续 7 天；结束汇总 `left_high` 净趋势、`notice_skip` 增量、
新代码生效时点、离线代理误杀率。若 `notice_skip` 持续为 0，先按 13.3(2) 与 **§14.3** 排查
（位置 / 总开关 / 进程生命周期三重），而非判定 A 无效。

## 14. feed 粗筛位置修复（2026-11-07 获批准，已实施）

### 14.1 批复范围与实现选择

批准的是 §13.3(2) 的「修 feed 粗筛位置」，目标写得很明确：**让方案 A 真能生效，
否则这一周影子观测拿不到样本**。实施时没有采用 §13.3(2) 给出的字面修法
（把 235–262 行整段 prefilter 提到守卫之前），而是换成更小的等价改动：

| | §13.3(2) 原建议 | 实际落地（更小、更安全） |
|---|---|---|
| 正式判读路径 | 需改守卫控制流，夜窗会走进 `for ... 8` 主循环 | **逐字不动** |
| 夜窗取批 | 会走完整取批（含 `stocks` JSON 解析、分批） | 只 `SELECT id,title`，零 JSON 解析 |
| Ollama | 需额外 flag 拦住 `jev_judge_batch` | 结构上根本到不了判读调用 |
| 锁争用 | 无兜底，写库异常会让 feed 轮次变红 | `except` 静默回退给夜批 |

理由：`OS-FIX-JEVFEEDNIGHT` 的契约是「夜窗内 feed 不碰 Ollama、判读全归 JevNight」。
把主循环放进来，后续迭代容易改坏契约；守卫分支里调一个只做纯 CPU 正则的函数更稳。

### 14.2 代码改动（2 个文件，均未 git commit）

- `news_collector/jev_feed.py`
  - 新增模块常量 `_PREFILTER_ON_VALUES`；正式路径的 `_mode in (...)` 改为引用它
    （单点定义，防两处白名单漂移；行为等价）。
  - 新增 `_night_prefilter(kind, cap)`：`JEV_PREFILTER` 未开时直接返回 0（零连库）；
    开启时只读本源未判行、跑 `prefilter_jev(..., mode="safe")`、命中写
    `src=prefilter / pref.reason` 的 neutral；`UPDATE ... AND (jev IS NULL OR jev='')`
    双守卫不覆盖夜批已判结果；任何异常（含 `database is locked`）静默回退，
    不写 `res["error"]`、不新增告警噪声。
  - 夜窗守卫分支在 `return` 前加一行
    `res["pref_skipped"] += _night_prefilter(kind, cap)`；`skipped_night=True` 语义、
    打印、`runs.detail` 两个字段（`judge_skipped_night` / `pref_skipped`）全部保留。
  - `judge_pending()` docstring 补一行说明。
- `news_collector/test_jev_notice_scope.py`：新增 6 项夜窗回归。

**验收**：5 模块 `py_compile` OK；
`pytest test_jev_notice_scope.py test_jev_backfill_contend.py -q` = **36 passed**
（原 19 + 11，新增 6）。新增用例覆盖：夜窗命中公告 skip 且
`news_ai.jev_judge_batch` **零调用**、开关关闭零连库、与夜批争锁静默回退、
不覆盖已判行、其他源不误杀、`--cap` 截断仍生效。

### 14.3 修复后仍存在的两重阻断（**关键，须另行批准**）

位置修复只解除了其中一重。只读核查（未改任何生产配置）发现：

1. **feed 启动器没有开总开关**。`jev_feed.py:212/300` 读 `JEV_PREFILTER`，
   **缺省 `"0"` = 关闭**；而 `news_collector/start_jev_feed.ps1` 全文没有设置它。
   对比：`jev-eval/jev_backfill_task.ps1:18`、`jev-eval/start_jev_night.ps1:11`、
   `tools/night_batch.py:404` 都设了 `JEV_PREFILTER=safe`，
   `tools/jev_night_preflight.py:152` 还会强校验 —— 三个入口里只有 feed 是关的。
   最小补丁：`start_jev_feed.ps1` 加一行 `$env:JEV_PREFILTER = 'safe'`
   （`Start-Process` 继承父进程 env）；回滚把值改 `'0'`，或按 §7 做数据级回退。
   **2026-11-07 07:30 已获老田批准补上（§14.6 选 A），见 §14.6。**
2. **feed 由单轮启动器拉起，轮次时间点多落在夜窗内**。
   `start_jev_feed.ps1` 注释标注每日 23:45（落在 21:45–07:05 内）；
   `jev-eval/start_jev_stack.ps1` 的 JevStack 四组件是 jev-gate / review-gate /
   qd-bridge / card-handler，**并不拉起 feed**。这与 §13.3(2) 的 16/24 轮一致：
   凡落在夜窗的轮次，修复前粗筛一律不执行。
   **【07:50 勘误】** 实查计划任务（`Get-ScheduledTask`）：`JevFeed-Daily` 触发为
   **每日 21:00**（无 Repetition 间隔），早于夜窗起点 21:45 —— 脚本注释「23:45」
   已过期失真。昨晚 LastRun=11-06 23:48:05 属延迟触发（迟了约 2h48m，落进夜窗）。
   两种情形的生效路径（均已打通）：
   - **准点 21:00** → 不落夜窗 → 走正常判读路径，粗筛经正常路径 `prefilter_jev()`
     生效（同读 `JEV_PREFILTER=safe`，§14.6(1) 已设）；
   - **延迟过 21:45** → 落夜窗 → 走 §14.1 新增的 `_night_prefilter()` 路径。
   即无论准点还是延迟，今晚起 A 都能生效；判读日志看
   `C:/Temp/jev_feed_out.log` 的 `pref_skipped` 或 `NIGHT-PREFILTER` 行。
3. 已在运行的 backfill / 夜批长跑进程仍持旧代码（§13.2），按轮次自然替换，
   **未擅自重启任何进程**。只读进程检查确认当前**无常驻 feed 进程** → feed 是单轮，
   下一轮自动加载新代码，**无需重启**。

### 14.4 观测现状复核（纯只读，hex + 码点复核防串味）

07:06 只读快照（落 `C:/Temp/jev_scope_observe_after_fix.json`，未覆盖当日正式基线）：

| 指标 | 00:59 基线 | 07:06 | hex / 码点复核 |
|---|---|---|---|
| `left_total` | 827 | 16 | `0x10` / `[49,54]` |
| `left_high` / `left_low` | 299 / 528 | 16 / 0 | `0x10` / `0x0` |
| `notice_skip`（新模板） | 0 | **0** | `0x0` |
| `legacy_skip` / `pref_total` | 214 / 214 | 214 / 214 | `0xd6` |
| `judged_today_9b` / `inserted_today` | — | 1962 / 677 | `0x7aa` / `0x2a5` |
| `new_code_effective_in_prod` | False | **False** | — |

夜批已把积压清到 16 条、公告类未判为 0，因此此刻没有可 skip 的样本；
`notice_skip` 仍为 0 与 14.3 的阻断一致，**不是模板失效**。
另：`--cap` P0（§12）仍限制公告入库量，即使补齐开关，样本量也受其约束。

### 14.5 回滚

- 代码级：删守卫分支那一行调用即回到修复前行为（`_night_prefilter` 不被调用、无副作用）。
- 开关级：`JEV_PREFILTER=0`（§7）。
- 数据级：§7 的 `json_extract(jev,'$.pref.reason')='例行公告'` SQL 精确回退。

### 14.6 待批 / 下一步

1. ~~（阻塞观测）~~ **已批准并实施（2026-11-07 老田选 A）**：`start_jev_feed.ps1`
   在 `Start-Process` 之前加注释 3 行 + `$env:JEV_PREFILTER = 'safe'`（子进程继承）；
   PowerShell `Parser::ParseFile` 校验 **PARSE OK**。回滚把值改 `'0'`；
   数据级回退走 §7 prefilter SQL。`tools/jev_night_preflight.py` 对启动器含
   `JEV_PREFILTER` 的强校验由两处变三处，只会更严。
2. 11-08 09:00 自动快照看 `notice_skip` 增量；`judge_skipped_night=true` 的轮次
   应同时出现 `pref_skipped>0`。**【07:50 勘误】** 生效路径按 §14.3(2) 勘误理解：
   准点 21:00 轮走正常路径粗筛（`runs.detail.pref_skipped`），延迟落夜窗才走
   `NIGHT-PREFILTER` 路径；两条路径今晚起均有效。另 `JevFeed-Verify`（22:30）
   只跑 `jev_feed_verify.py` 校验上轮结果、不调 `judge_pending`，与粗筛无关。
3. 其余维持既定决策：**不 git commit**（观察一周后再提交）、不实施方案 B、
   不动 `--cap`（P0 另立 RFC）、C4 迁移 dry-run→--apply 仍未动。
