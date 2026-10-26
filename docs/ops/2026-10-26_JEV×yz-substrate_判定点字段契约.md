# JEV × yz-substrate · 判定点字段契约（P0 定稿）

> 工单：K-DATA-20261026-JEVCAL ｜ 2026-10-26
> 目的：把 JEV（舆情判读）接入 yz-substrate 的判定账本/后验回流/动态基线体系前，先锁定**字段与口径**。
> 约定：本文件是契约，任何一侧改字段先改这里；P0 只读，不改生产。

---

## 一、判定点注册表（yzpoint ↔ JEV 字段）

| point_id | type | predictive | 候选集（choice） | posterior 定义 |
|---|---|---|---|---|
| `jev.bias` | choice | **是** | `bull` / `bear` / `neutral` | T+1 超额收益方向是否与 bias 一致（见 §四） |
| `jev.event_type` | choice | 否 | 14 类（earnings/policy/rate/...） | 无（分类型，不预测收益） |
| `jev.scope` | choice | 否 | `cn`/`hk`/`us`/`commodity`/`crypto`/`fx`/`macro` | 无 |
| `jev.impact` | choice | 否 | `low`/`medium`/`high` | 无 |

> 当前 JEV 只产 `bias`（预测）与 `event_type/scope/impact`（分类）。V14 基线只对 `predictive=true` 的点生效 → **只有 `jev.bias` 会进 baseline_report**。

## 二、Answer 映射

JEV 判读 JSON（`news.db.news.jev`）→ yz-substrate `Answer`：

| yz Answer 字段 | JEV 来源 | 现状 | 要求 |
|---|---|---|---|
| `question` | `jev.bias` | — | 按 point 命名 |
| `type` | `choice` | — | — |
| `answer` | `bias` | ✅ 有 | 必须 ∈ {bull,bear,neutral}，越界即 `JudgeUnavailable` |
| `probabilities` | **无** | ❌ 缺 | 建议下一步让本地判读输出 `{bull:p,bear:p,neutral:p}`；P0 期间用 `{answer:conf, 其余均分}` 兜底 |
| `confidence` | `conf` | ✅ 有（未校准） | **必须 clip 到 [0.5,0.9]**（B 型规则，V5 高置信直采永不触发） |
| `judge` | `llm:qwen3.5:9b` | 模型名 | 落账时必须带 `model` |

## 三、账本（decision_audit ↔ JEV 缺口）

yz-substrate 的 `decision_audit` 期望（reflow.py 消费）：

```
seq, point_id, answers, state_snapshot, model_verdict, posterior
```

JEV 现状（news.db）只有 `id/ts/source/stocks/jev(JSON)`，**缺**：

| 缺口 | 影响 | P1 方案 |
|---|---|---|
| 无 `seq` / append-only 链 | 无法审计判读历史/回滚 | 新建 `jev_audit` 表（seq 自增 + hash_prev） |
| 无 `model` / `prompt_version` | 无法归因"哪版 prompt/模型有效" | 判读时写入列 |
| 无 `state_snapshot` | 回填时无法复原当时上下文 | 存 title/source/stocks 快照 |
| 无 `posterior` | **无法校准** | P1 refill 回填（见 §四） |

## 四、posterior 定义（v1 口径，P0 已验证可算）

```
信号日     = news.ts 的日期
持有日     = 信号日之后第一个 SSE 交易日（T+1）        ← trade_cal.parquet
标的收益   = 关联 A 股（companies ∪ stocks，6 位码归一为 ts_code）T+1 pct_chg 等权均值
基准收益   = 000300.SH 同日 pct_chg                    ← index_daily.parquet
超额       = 标的收益 − 基准收益                        ← daily.parquet
命中(posterior)：
  bias=bull    → excess > 0
  bias=bear    → excess < 0
  bias=neutral → |excess| ≤ 1.0（中性带，可配）
```

连续后验（供 V14 用）：`excess`（百分点）与 `hit`（0/1）都保留。

## 五、refill / ctx_provider 契约（给 yz-substrate 对接）

```python
# ctx_provider(point_id, row) -> dict
{
  "trade_day": "20261023",      # T+1 交易日
  "codes": ["600519.SH"],       # 归一后的标的
  "ret": 1.23,                  # 标的等权收益 %
  "bench_pct": 0.45,            # 基准收益 %
  "excess": 0.78,               # 超额 %
  "hit": 1,                     # 0/1（按 §四口径）
}
```

松耦合落地方案：JEV 每日追加 `agent_platform/data/jev_audit/posterior_<date>.jsonl`（一行一条），
yz-substrate `refill(posterior_map={"jev.bias": fn})` 直接读取，**双方不改对方代码**。

## 六、baseline_report 口径（JEV 版）

| 字段 | JEV 定义 |
|---|---|
| `model_hit_rate` | **方向类准确率**（bull/bear 的方向命中率），不算中性 |
| `baselines` | 全判 bull / 全判 bear（同分母方向类）；全判 neutral（中性带命中率） |
| `min_n` | 建议 **200**（方向类样本；当前 P0 样本 1,997 达标，conf 分桶单桶不足） |
| `status` | `insufficient_data` / `ready_for_edge_test`——与 reflow.py 语义一致 |

## 七、P0 实测结论（2026-10-26，7 个交易日样本）

| 指标 | 值 |
|---|---|
| 已判 / 带 A 股标的 / 可评估 | 67,354 / 7,955（11.8%） / 3,819（方向类 1,997 + 中性 1,822） |
| **方向准确率** | **55.9%**（Wilson 95%: ~53.7–58.1%） |
| 全判 bull 基线 | 55.7%（53.5–57.9%） |
| 结论 | **JEV 方向判读相对"全判 bull"无统计显著增量** |
| conf 校准曲线（方向类） | 0.55–0.65: 50.0% / 0.65–0.75: 48.4% / 0.75–0.85: 57.8% / 0.85+: 55.0% → **非单调，conf 无校准度** |
| 平均超额 | bull +0.715 / neutral +0.361 / bear +0.131（排序对，但 bear 均值仍为正） |

> 注：样本仅 7 个交易日（多数为近 2 周判读），结论是**方向性证据**而非最终定论；
> 但它已足以说明"直接用 JEV bias/conf 当预测信号"目前不成立。

## 八、未决问题（需拍板）

1. **后验窗口**：T+1 收盘（当前）vs T+3/T+5（是否更适合情绪类事件）？
2. **是否行业中性**：当前减沪深300；是否再减行业指数（避免"板块 beta 冒充方向"）？
3. **中性带宽度**：当前 ±1%，neutral 命中率仅 32.8%——是否放宽到 ±2%？
4. **命名歧义**：judges.py 注释里 "Jev" 作为 wire 端点之一——是**决策服务人格**还是**本仓舆情 JEV**？影响 wire 适配器是"新增"还是"补口"。
5. **conf 语义**：yz B 型强制 clip [0.5,0.9]；JEV 是否接受"永不 V5 直采"？若必须支持高置信直采，需要 P1 校准后按分桶重映射。

## 九、P1/P2 待办（P0 已定契约）

- P1：`jev_audit` 表 + `--only` 式回填 + 每日 17:30 生成 `posterior_<date>.jsonl` + 健康卡 `jev.calibration`
- P1：JEV wire 适配器 `POST /v1/systemone`（A 型出口）
- P2：V14 门控结果反哺 `JEV_GATE` 阈值（0.8 改为 min_edge 驱动，经变更单）
