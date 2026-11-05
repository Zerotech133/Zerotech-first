# LX03（将军柱/卧底搭梯）独立复核审计

> 日期：2026-11-04｜任务：task-84｜审计方：ACAD1（独立验证）
> 输入：`code/scripts/research_lx03_general_pillar.py`、`research/reports/W2_LX03_prestudy_20261104.md`、`research/reports/LX03_prestudy_20261104.json`；独立复算脚本：`C:\Temp\lx03_confirm_recompute.py`、`C:\Temp\lx03_breakout_recompute.py`
> 约束：只读；不改实现者文件；不写 DB；不改策略状态。仅新增本 audit 报告。

## 0. 一句话结论

**confirm 与 wodi_breakout 两个代理事件的样本量、IC、事件超额、OOS 与实现者 JSON 逐项一致；独立按代码级截断的 PIT 测试也通过（mismatch=0）。代理事件初筛 = reject；原书完整战法 = insufficient。**

| 对象 | 独立复算 | 实现者结论 | 审计 verdict |
|---|---|---|---|
| lx03_general_confirm（3日确认代理） | 完全一致（n_events 51,468；IC -0.006095/t -3.358；超额 -0.6699%/t -4.354） | 不通过 | **proxy reject** |
| lx03_wodi_breakout（卧底突破代理） | 完全一致（n_events 2,136；超额 -0.4137%/t -0.813） | 不通过 | **proxy reject** |
| 原书完整将军柱/搭梯战法 | 未实现（画线/人工辨柱/搭梯双柱/退出规则） | insufficient | **full strategy insufficient** |

> `reject` 指代理事件未通过预注册初筛；不是“代码不可复现”。实现复现性 = pass。

## 1. 方法：独立读 parquet，不 import 实现者代码

```powershell
C:\项目\py312\python.exe C:\Temp\lx03_confirm_recompute.py   # 独立实现 confirm 事件 + IC/事件超额
C:\项目\py312\python.exe C:\Temp\lx03_breakout_recompute.py  # 独立实现 breakout 事件 + 事件超额
```

- 直接读 `quant_backtest/data/tushare/daily/d_*.parquet`、`adj_factor`、`stock_basic`；独立构建 483 日 × 5,662 只面板；未 import `factor_lib`，也未 import 实现者脚本；公式按预注册/报告独立重写。
- CAR 口径：T+1 收盘入场、T+21 收盘卖出、等权市场日收益复利、单边 30bp；tradable = close≥2、amount×1000≥5000 万、上市≥250 日；IC = 按日横截面 Spearman；事件超额 = 每日信号组等权 CAR − 非信号组等权 CAR。
- 输出：`C:\Temp\lx03_confirm_recompute.json`、`C:\Temp\lx03_breakout_recompute.json`。

## 2. 代理事件独立复算与实现者数字对比

### 2.1 事件样本量

| 诊断 | 实现者 JSON | 独立复算 | 一致 |
|---|---:|---:|---|
| 基柱候选 base_candidates | 233458 | 233458 | ✅ |
| confirm_obs（全面板） | 72191 | 72191 | ✅ |
| confirm_days（全面板有事件日） | 479 | 479 | ✅ |
| confirm 有效事件（信号窗内 eligible+finite） | 51468 | 51468 | ✅ |
| confirm 信号日 | 422 | 422 | ✅ |
| breakout_obs（全面板） | 2427 | 2427 | ✅ |
| breakout_days（全面板有事件日） | 359 | 359 | ✅ |
| breakout 有效事件（信号窗内 eligible+finite） | 2136 | 2136 | ✅ |
| breakout 信号日 | 327 | 327 | ✅ |

> **两个计数的关系**：`confirm_obs=72,191`/`breakout_obs=2,427` 是**全面板**（含 warmup、未过滤 eligible、未要求 CAR finite）的事件数；`n_events=51,468`/`2,136` 是**信号窗内 + tradable + CAR 可算**的有效样本。两者不是矛盾；独立复算两者均复现。

### 2.2 IC / 事件超额 / OOS

**confirm（lx03_general_confirm）**：

| 指标 | 实现者 | 独立复算 | 差值 |
|---|---:|---:|---:|
| 全样本 IC mean | -0.006095 | -0.006095 | 0.0 |
| IC t | -3.358 | -3.358 | 0.0 |
| IC n_days | 422 | 422 | 0 |
| 事件超额 mean | -0.6699% | -0.6699% | 0.0 |
| 事件超额 t | -4.354 | -4.354 | 0.0 |

| OOS 切分 | 实现者 mean / t | 独立 mean / t | 一致 |
|---|---:|---:|---|
| 20250430 | -0.6077% / -3.667 | -0.6077% / -3.667 | ✅ |
| 20251031 | -0.9139% / -3.975 | -0.9139% / -3.975 | ✅ |
| 20260430 | -1.0974% / -2.745 | -1.0974% / -2.745 | ✅ |

**breakout（lx03_wodi_breakout）**：

| 指标 | 实现者 | 独立复算 | 差值 |
|---|---:|---:|---:|
| 全样本事件超额 mean | -0.4137% | -0.4137% | 0.0 |
| 全样本 t | -0.813 | -0.813 | 0.0 |
| OOS 20250430 | -0.9702% / t=-1.772 | -0.9702% / t=-1.772 | ✅ |
| OOS 20251031 | -2.241% / t=-3.43 | -2.241% / t=-3.43 | ✅ |
| OOS 20260430 | -2.1701% / t=-1.733 | -2.1701% / t=-1.733 | ✅ |

**差值解释**：两事件的全样本/OOS 数字与实现者逐项一致（四舍五入 0.0）；独立实现未使用实现者因子函数，说明因子/事件定义与评测口径可被独立重写复现，非结果拷贝。

## 3. PIT 检查（T0+3 / 突破日 B / 截断测试）

- **confirm**：事件在 T=T0+3 行；条件只用 `close/open/vol` 的 T0..T0+3 数据；信号在 T 收盘，T+1 入场。独立按代码级截断（只用 ≤T 数据重算，与全量矩阵 T 行逐代码比较）：

| 日期 | 全量代码级 mismatch | 实现者 date-level any | 结论 |
|---|---:|---|---|
| 20251208 | 0 | full=True/sub=True | PASS |
| 20260716 | 0 | full=True/sub=True | PASS |

- **breakout**：事件在 B 行；只用 ≤B 数据（B 条件、窗口、量比）；独立代码级截断：

| 日期 | 全量代码级 mismatch | 结论 |
|---|---:|---|
| 20251208 | 0 | PASS |
| 20260716 | 0 | PASS |

- 结论：**PIT pass**。实现者 `pit_check` 只比较当日 `.any()`（date-level，较弱）；本审计按代码级逐列比较，confirm/breakout 均 mismatch=0，未发现未来数据泄漏。

## 4. 样本日期 / 覆盖率 / 缺失处理

- 面板：**483 日 × 5,662 代码**（20241106–20261103）；有效信号窗 **20250102–20260928（422 日）**；信号末日 20260928 距数据末日 21 个交易日，满足 T+1→T+21，**无提前截断**。
- 有效样本：confirm 51,468 事件/422 信号日；breakout 2,136 事件/327 信号日；均 > 预注册最小值（事件≥100、信号日≥30），统计量不是小样本噪声。
- 覆盖率：tradable 池日均 eligible≈4,194（约 74.1%）；confirm 全面板 72,191 事件中有 51,468 进入有效窗口；breakout 2,427→2,136；减少主要来自未过滤/无 CAR 的 warmup 与不可交易样本，报告数字同口径可复现。
- 缺失/停牌：事件条件要求 close/open/low/high/vol finite；缺失即不生成事件；无 ffill/插值；CAR 缺失在事件组/非事件组统计中被剔除；价格<2/成交额<5000 万/上市<250 日的股票被 tradable 排除。这与报告口径一致，未见缺失被当作 0 或前填充。

## 5. 参数 / YAML / 预注册一致性

| YAML 参数 | YAML 值 | 代理实现 | 一致 |
|---|---:|---:|---|
| confirm_days | 3 | 3 | ✅ |
| bottom_hold_ratio_min | 0.99 | 0.99 | ✅ |
| flat_bottom_days_min/max | 3 / 8 | 3 / 8 | ✅ |
| flat_bottom_tol_pct | 0.01 | 0.01（相对窗口最低 low 的极差） | ✅（口径见下） |
| breakout_vol_ratio_min | 1.9 | 1.9 | ✅ |

**代理补充参数（不在 YAML，但已在预注册/报告披露）**：

- `base_bull=True`、`base_vol_ratio_min=1.5`（YAML 只写“基柱”，未给基柱量比阈值；代理用 1.5 小倍阳）
- `breakout_within_days=3`（YAML “3 日内”）；`horizon_days=20`、`cost_bp=30`（评测口径）

**未覆盖的 YAML 项（重要）**：

- YAML `exit_rule`：`close < base_low*0.99` 止损，持有 5/10 个交易日；本代理固定 20 日、未实现止损。因此本 prestudy 评测的是“事件后 20 日 CAR 的代理信号”，**不是 YAML 的完整退出规则**；报告已把完整战法标 insufficient，但 `yaml_spec.params_consistent` 只检查 5 个 YAML params，不检查 exit_rule/horizon，这是审计发现的**口径覆盖缺口**（不是代理数字错误）。
- YAML 的“实顶/实底线画线、人工辨柱、搭梯双柱关系、主庄/精庄/狡庄”未实现；报告 `not_full_strategy=true` 且 `full_strategy_verdict=insufficient`，**没有把代理说成完整战法**（见 6）。

## 6. 代理 vs 完整战法 / 结论与数字一致性

- 报告标题、正文与 JSON 均明确：`scope=prestudy_proxy_only_no_strategy_promotion`、`not_full_strategy=true`、`full_strategy_verdict=insufficient`、手动项列出画线/辨柱/搭梯/庄家上下文；**未把代理包装成完整战法**。
- 报告代理结论：`不通过`（confirm 超额 -0.6699%/t -4.354；breakout -0.4137%/t -0.813；OOS 多数负）；独立复算数字与之一致；结论与数字一致，**没有淡化负向**。
- 报告 `full_strategy=insufficient` 是合理边界：B 级原战法依赖人工画线/辨柱/搭梯/退出规则，代理未实现。但审计需指出：`yaml_spec.params_consistent` 只验证 5 个 YAML params，未验证 exit_rule / 持有期 / 止损；因此“params consistent”不能理解为“代理已完整对齐 YAML”。

## 7. 发现的限制/缺口（非阻断，按严重度）

1. **P1 参数覆盖缺口**：`params_consistent` 只覆盖 confirm_days / bottom_hold / flat days / flat tol / breakout vol；未覆盖 YAML exit_rule（5/10 日+止损）与 horizon；代理用固定 20 日无止损。建议后续 prestudy 的 consistency 检查加入 exit_rule/horizon/止损，或将“入口事件代理”与“完整战法”分开登记。
2. **P2 PIT 测试强度**：实现者 `pit_check` 只比较当日 `any()`；本次独立按代码级 mismatch=0 通过，实际无问题，但建议实现者把 PIT 测试升级为代码级比对（避免单只事件掩盖差异）。
3. **P2 计数口径**：诊断 `confirm_obs=72,191/breakout_obs=2,427`（全面板）与 `n_events=51,468/2,136`（窗口+可交易）不同；报告正文混用两套数字，易误读；建议统一加注（本次审计已复算两种口径）。
4. **P2 简化披露**：基柱/实底用 open 代理、平底上沿用窗口最高 high 代理、flat_tol 用相对窗口最低 low 的 1%；报告已在 manual_not_programmatic 披露，但 `params_consistent` 不能表达这些近似；建议 prestudy 文档继续保留。

## 8. 最终 verdict

| 对象 | verdict | 依据 |
|---|---|---|
| lx03_general_confirm（confirm 代理事件） | **proxy reject** | 独立复算完全一致；事件超额 -0.6699%/t -4.354，OOS 0/3 正；PIT pass；参数一致 |
| lx03_wodi_breakout（breakout 代理事件） | **proxy reject** | 独立复算完全一致；事件超额 -0.4137%/t -0.813，OOS 0/3 正；PIT pass；参数一致 |
| 完整将军柱/搭梯战法 | **full strategy insufficient** | 未实现实顶/实底线、人工辨柱、搭梯双柱、YAML exit_rule（5/10 日+止损）；代理不能代表完整战法 |

**总体**：代理事件初筛 **reject**（两个代理均未通过预注册正向/OOS 稳定性）；完整战法 **insufficient**；不同意把 proxy 结论外推为完整战法有效/无效。

## 9. 复现与只读声明

```powershell
C:\项目\py312\python.exe C:\Temp\lx03_confirm_recompute.py
C:\项目\py312\python.exe C:\Temp\lx03_breakout_recompute.py
```

- 独立输出：`C:\Temp\lx03_confirm_recompute.json`、`C:\Temp\lx03_breakout_recompute.json`。
- 实现者输入文件（脚本/JSON/MD）在审计前后未改动；本审计只新增本文件。
- 只读：不写 DB、不改实现者文件、不改策略状态、不生成选股；未调用任何写接口/任务触发。