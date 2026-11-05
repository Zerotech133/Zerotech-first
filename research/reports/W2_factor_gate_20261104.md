# W2-6 四关前筛质量门（2026-11-04）

> STATUS: gate 汇总（只读）｜任务：task-79｜生成：2026-11-04T03:01:01+08:00
> 输入：`code/config/w2_factor_screen_results.json` + `research/reports/W2_4_independent_audit_20261104.md`（+ config 指向的组 stats 仅用于补充分层数字）
> 边界：只汇总，不重跑因子、不写 DB、不改策略状态、不生成选股；缺失证据一律 `not_evaluated`/`insufficient`，**不把 reject 改写成 pass**。

## 一、结论摘要

- 因子数：**8**；verdict：**pass_to_next_gate 0 / reject 5 / insufficient 3**。
- **无因子进入下一关（pass_to_next_gate=0）**；5 个 reject、3 个 insufficient。
- PIT：仅 `chip_concentration`、`moneyflow_strength` 有独立审计截断证据（pass）；其余 6 个未独立评估 → `not_evaluated`（不推断）。
- 成本：所有因子只有 30bp 单边，无 50/80bp/冲击敏感性 → `not_evaluated`；容量：均无容量/涨跌停证据 → `not_evaluated`。
- 与独立审计数字一致性：chip_concentration=✅ 关键数字命中；moneyflow_strength=✅ 关键数字命中

## 二、8 因子 gate 矩阵

| 因子 | implemented | PIT | IC方向 | OOS | 分层 | 成本 | 容量 | verdict |
|---|---|---|---|---|---|---|---|---|
| ma_convergence | pass | not_evaluated | pass | fail | fail | not_evaluated | not_evaluated | **insufficient** |
| ma_bull_alignment | pass | not_evaluated | fail | fail | fail | not_evaluated | not_evaluated | **reject** |
| volume_breakout_strength | pass | not_evaluated | fail | fail | insufficient | not_evaluated | not_evaluated | **reject** |
| macd_momentum | pass | not_evaluated | fail | fail | fail | not_evaluated | not_evaluated | **reject** |
| platform_tightness | pass | not_evaluated | pass | pass | insufficient | not_evaluated | not_evaluated | **insufficient** |
| post_limitup_strength | pass | not_evaluated | pass | insufficient | pass | not_evaluated | not_evaluated | **insufficient** |
| chip_concentration | pass | pass | fail | fail | fail | not_evaluated | not_evaluated | **reject** |
| moneyflow_strength | pass | pass | fail | fail | insufficient | not_evaluated | not_evaluated | **reject** |

## 三、逐因子证据与判定

### ma_convergence（来源 status：inconclusive）

- 方向：+1；IC：0.017652（t=2.172）
- implemented：pass —— 初筛已执行/有 stats 产物；未独立审计
- PIT：not_evaluated —— 组报告未提供独立 PIT 截断测试；不推断
- IC方向：pass —— IC 方向为正且 |t|≥2（先验 +1 方向成立）
- OOS：fail —— OOS 仅 1/3 为正，显著为正 1（需 ≥2/3 正且≥1 显著）
- 分层：fail（Q5-Q1=-1.0312%, t=-3.875） —— Q5-Q1=-1.0312%, t=-3.875（负且显著；IC 与五分位方向矛盾，配置判 inconclusive）
- 成本：not_evaluated —— 仅 30bp 单边；无 50/80bp 与冲击成本敏感性
- 容量：not_evaluated —— 初筛未提供容量/成交额/涨跌停可执行性证据
- **verdict：insufficient**；只有一个切分显著为正；IC与五分位方向矛盾；先复核实现/口径，不采用
- next_action：先补齐/复核实现与口径（PIT/分层/成本/容量），不做方向判断前不采用
- 证据：`research/reports/W2_MA_factors_20261104.json`

### ma_bull_alignment（来源 status：rejected_as_buy_factor）

- 方向：+1；IC：-0.092389（t=-11.26）
- implemented：pass —— 初筛已执行/有 stats 产物；未独立审计
- PIT：not_evaluated —— 组报告未提供独立 PIT 截断测试；不推断
- IC方向：fail —— IC 方向为负且 |t|≥2（先验 +1 被显著否定）
- OOS：fail —— OOS 仅 0/3 为正，显著为正 0（需 ≥2/3 正且≥1 显著）
- 分层：fail（Q5-Q1=-1.0551%, t=-2.81） —— Q5-Q1=-1.0551%/20日，t=-2.81（方向负且显著）
- 成本：not_evaluated —— 仅 30bp 单边；无 50/80bp 与冲击成本敏感性
- 容量：not_evaluated —— 初筛未提供容量/成交额/涨跌停可执行性证据
- **verdict：reject**；多头排列越强未来收益越低，方向与假设相反；不能作为买入因子
- next_action：不进入下一关；不得作为买入因子；如要反向使用必须另立预注册+独立OOS
- 证据：`research/reports/W2_MA_factors_20261104.json`

### volume_breakout_strength（来源 status：rejected_as_buy_factor）

- 方向：+1；IC：-0.039735（t=-9.383）
- implemented：pass —— 初筛已执行/有 stats 产物；未独立审计
- PIT：not_evaluated —— 组报告未提供独立 PIT 截断测试；不推断
- IC方向：fail —— IC 方向为负且 |t|≥2（先验 +1 被显著否定）
- OOS：fail —— OOS 仅 0/3 为正，显著为正 0（需 ≥2/3 正且≥1 显著）
- 分层：insufficient（Q5-Q1=0.3801%, t=1.566） —— Q5-Q1=0.3801%/20日，t=1.566（不显著/方向矛盾）
- 成本：not_evaluated —— 仅 30bp 单边；无 50/80bp 与冲击成本敏感性
- 容量：not_evaluated —— 初筛未提供容量/成交额/涨跌停可执行性证据
- **verdict：reject**；方向与放量突破假设相反，OOS 稳定为负；不得当买入因子
- next_action：不进入下一关；不得作为买入因子；如要反向使用必须另立预注册+独立OOS
- 证据：`research/reports/W2_breakout_macd_20261104.json`

### macd_momentum（来源 status：rejected_as_buy_factor）

- 方向：+1；IC：-0.03933（t=-5.259）
- implemented：pass —— 初筛已执行/有 stats 产物；未独立审计
- PIT：not_evaluated —— 组报告未提供独立 PIT 截断测试；不推断
- IC方向：fail —— IC 方向为负且 |t|≥2（先验 +1 被显著否定）
- OOS：fail —— OOS 仅 0/3 为正，显著为正 0（需 ≥2/3 正且≥1 显著）
- 分层：fail（Q5-Q1=-0.6405%, t=-2.371） —— Q5-Q1=-0.6405%/20日，t=-2.371（方向负且显著）
- 成本：not_evaluated —— 仅 30bp 单边；无 50/80bp 与冲击成本敏感性
- 容量：not_evaluated —— 初筛未提供容量/成交额/涨跌停可执行性证据
- **verdict：reject**；方向与 MACD 动量假设相反，OOS 稳定为负；不得当买入因子
- next_action：不进入下一关；不得作为买入因子；如要反向使用必须另立预注册+独立OOS
- 证据：`research/reports/W2_breakout_macd_20261104.json`

### platform_tightness（来源 status：inconclusive）

- 方向：+1；IC：0.051249（t=4.943）
- implemented：pass —— 初筛已执行/有 stats 产物；未独立审计
- PIT：not_evaluated —— 组报告未提供独立 PIT 截断测试；不推断
- IC方向：pass —— IC 方向为正且 |t|≥2（先验 +1 方向成立）
- OOS：pass —— OOS 3/3 为正，显著为正 3
- 分层：insufficient（Q5-Q1=-0.4305%, t=-1.133） —— Q5-Q1=-0.4305%/20日，t=-1.133（不显著/方向矛盾）
- 成本：not_evaluated —— 仅 30bp 单边；无 50/80bp 与冲击成本敏感性
- 容量：not_evaluated —— 初筛未提供容量/成交额/涨跌停可执行性证据
- **verdict：insufficient**；IC正但Q5-Q1为负且不单调；独立复核非重叠t=0.36，不成立
- next_action：先补齐/复核实现与口径（PIT/分层/成本/容量），不做方向判断前不采用
- 证据：`research/reports/W2_platform_limitup_20261104.json`

### post_limitup_strength（来源 status：inconclusive）

- 方向：+1；IC：0.018571（t=6.585）
- implemented：pass —— 初筛已执行/有 stats 产物；未独立审计
- PIT：not_evaluated —— 组报告未提供独立 PIT 截断测试；不推断
- IC方向：pass —— IC 方向为正且 |t|≥2（先验 +1 方向成立）
- OOS：insufficient —— 3/3 切分 IC 为正，但第三切分 t=0.068 接近 0；配置判 inconclusive、第三切分不稳定
- 分层：pass（Q5-Q1=0.9991%, t=6.399） —— Q5-Q1=0.9991%/20日，t=6.399（方向正且显著）
- 成本：not_evaluated —— 仅 30bp 单边；无 50/80bp 与冲击成本敏感性
- 容量：not_evaluated —— 初筛未提供容量/成交额/涨跌停可执行性证据
- **verdict：insufficient**；前两个切分为正，第三个接近0；不采用
- next_action：先补齐/复核实现与口径（PIT/分层/成本/容量），不做方向判断前不采用
- 证据：`research/reports/W2_platform_limitup_20261104.json`

### chip_concentration（来源 status：rejected_as_buy_factor）

- 方向：+1；IC：-0.045456（t=-8.222）
- implemented：pass —— 独立审计复算通过（见 W2_4_independent_audit）
- PIT：pass —— 审计截断测试 20251208/20260716 max_abs_diff=0；同T日快照、无ffill
- IC方向：fail —— IC 方向为负且 |t|≥2（先验 +1 被显著否定）
- OOS：fail —— OOS 仅 0/3 为正，显著为正 0（需 ≥2/3 正且≥1 显著）
- 分层：fail（Q5-Q1=-1.7157%, t=-7.474） —— Q5-Q1=-1.7157%/20日，t=-7.474（方向负且显著）
- 成本：not_evaluated —— 仅 30bp 单边；无 50/80bp 与冲击成本敏感性
- 容量：not_evaluated —— 初筛未提供容量/成交额/涨跌停可执行性证据
- **verdict：reject**；先验方向+1被数据显著否定；Q5-Q1 -1.716%/20日 t=-7.474；0/3 OOS为正；不得当买入因子
- next_action：不进入下一关；不得作为买入因子；如要反向使用必须另立预注册+独立OOS
- 证据：`research/reports/W2_chips_20261104.md`；`research/reports/W2_chips_20261104.json`；`research/reports/W2_4_independent_audit_20261104.md`

### moneyflow_strength（来源 status：rejected_as_buy_factor）

- 方向：+1；IC：0.001366（t=0.254）
- implemented：pass —— 独立审计复算通过（见 W2_4_independent_audit）
- PIT：pass —— 审计截断测试 20251208/20260716 max_abs_diff=0；trailing rolling
- IC方向：fail —— raw IC=+0.001366(t=0.254) 不显著、5d=-0.001186(t=-0.22)、20d=-0.012553(t=-2.17)；三变体不一致，未达正向显著
- OOS：fail —— OOS 仅 1/3 为正，显著为正 0（需 ≥2/3 正且≥1 显著）
- 分层：insufficient —— raw/5d/20d 三变体 Q5-Q1 分别 0.3557(t1.856)/0.5691(t2.711)/0.3431(t1.328)；与 IC/OOS 矛盾
- 成本：not_evaluated —— 仅 30bp 单边；无 50/80bp 与冲击成本敏感性
- 容量：not_evaluated —— 初筛未提供容量/成交额/涨跌停可执行性证据
- **verdict：reject**；raw/5d/20d 三变体均未同时满足全样本正向显著+OOS≥2/3正向+Q5-Q1正向显著；独立审计复现后 reject；不得采用
- next_action：不进入下一关；不得作为买入因子；如要反向使用必须另立预注册+独立OOS
- 证据：`research/reports/W2_moneyflow_20261104.md`；`research/reports/W2_moneyflow_20261104.json`；`research/reports/W2_4_independent_audit_20261104.md`

## 四、口径与限制

- `implemented` 只表示初筛产物已存在/可运行；除 chip/moneyflow 经独立审计外，其余 6 个未做独立代码复算，故不把 implemented=pass 外推为“实现已被独立验证”。
- `PIT`：6 个非审计因子无独立截断测试证据；组报告虽按 T 日因子设计，但本任务不据此推断 pass。
- `IC方向`：先验 +1；正且 |t|≥2=pass；负且 |t|≥2=fail；|t|<2=insufficient。
- `OOS`：至少 2/3 切分 IC 为正且至少 1 个显著为正=pass；否则 fail；无 OOS=not_evaluated。
- `分层`：Q5-Q1 正且 t≥2=pass；负且 |t|≥2=fail；否则 insufficient；无数据=not_evaluated。moneyflow 三变体互相矛盾 → insufficient（不挑单指标）。
- `成本`/`容量` 未评估：初筛只覆盖 30bp 与“可交易池”粗过滤，不等价于 G3 成本敏感性与 G5 容量/可执行性。

## 五、复现

```powershell
cd C:\项目\zhice-os-v2-windows-20260920\code
.\.venv\Scripts\python.exe scripts\w2_factor_gate.py
# 可选参数：--config --audit --out-json --out-md
```

- JSON：`C:\Temp\w2_factor_gate_matrix.json`；本 MD：`research/reports/W2_factor_gate_20261104.md`。
- 只读声明：未重跑因子、未写 DB、未改策略状态/生产配置；仅读取 config/audit/组 stats 并写上述两份输出。