# 20261014 backtest-dev 变更记录（W1-2 两项）

执行域：`E:\老田的AI助手\quant_backtest`
解释器：`C:/Program Files/Python311/python.exe`
纪律遵守：未推送飞书、未注册计划任务、未跑全市场扫描、未动 `data/` 与 `positions.json`；改前均已备份。

---

## 任务A：季度调参接入 walk_forward 纪律门（P0）

### 改动文件
| 文件 | 改动 |
|---|---|
| `opt_defaults.py` | 新增 `_gate_folds()` + `discipline_gate()`；`main()` 在 `--apply` 前过门；飞书摘要逐策略附纪律门结果 |
| `config.json` | 新增 `"opt_discipline_gate": true`（开关，默认开） |
| `test_opt_discipline_gate.py` | 新增，5 条用例 |

### 关键逻辑（opt_defaults.py）
- **口径**：与 `walk_forward.py` 对齐——train 252d / test 63d 滚动推进、至少 4 折；IS 夏普=各折训练窗普通口径夏普均值，OOS 夏普=各折测试窗净收益/持仓拼接后统一 `metrics.evaluate`。
- **判据**：`OOS ≥ 0.6×IS` → PASS 放行写入；`OOS < 0.6×IS` → VETO，写回默认参数（`strategies.PARAM_DEFAULTS` 为底、原 config 值补缺键），摘要/飞书明示 `纪律门否决：<策略> <参数组> OOS=x.xx < 0.6×IS=y.yy`。
- **样本不足**：折数 < 4 → SKIP，写最优参数但在摘要/飞书标注 `SKIP 样本不足未校验（n 折 < 4）`——不静默放行也不炸任务。
- **成本**：门内评估用 CN 保守口径（12+3=15bps）。
- **开关/回退**：`config.opt_discipline_gate: false` → 完整保留旧行为（对照模式）。

### 单测覆盖（test_opt_discipline_gate.py）
- `test_gate_pass`：mock 折窗 (IS=1.0, OOS=0.8) → PASS
- `test_gate_veto`：mock (IS=1.5, OOS=0.5) → VETO，文案含 `纪律门否决：trend_follow … OOS=0.50 < 0.6×IS=0.90`
- `test_gate_skip_insufficient_bars`：合成 100 bar → SKIP「样本不足未校验」
- `test_gate_skip_zero_folds`：折窗全挂 → SKIP 不静默放行
- `test_gate_pass_enough_folds_synthetic`：合成 600 bar 真实折窗 ≥4 折

---

## 任务B：成本分层与 CRYPTO T+0

### 改动文件
| 文件 | 改动 |
|---|---|
| `backtest.py` | 新增 `load_trade_config(market=None, cfg=None)`；`run()` 加 `t_plus_one=True` 参数 |
| `config.json` | `trade` 节改分层结构（by_market：CN 12+3 / US 5+1 / HK 5+1 / CRYPTO 8+2 / T_plus_one_markets=[CN,US,HK]） |
| `runner.py` | 全部 `backtest.run` 调用点改传 `**load_trade_config(市场)`；`compare_strategies`/`optimize_run`/`_page_section`/`report_markdown`/`fmt_report` 加 `market` 参数；报告/扫描输出加成本口径标注行；新增 `cost_note()` |
| `opt_defaults.py` | `tune()` 逐标的按 frames 键 `(market, symbol)` 分层取成本 |
| `test_backtest_trade_layers.py` | 新增，4 条用例 |

### 关键逻辑
- **读法**（backtest.py:20 `load_trade_config`）：有 `by_market` → 按市场取；无（旧扁平 config）→ 回退旧行为（统一成本 + T+1）；market 拿不到/未知 → 默认 CN 保守口径。返回 dict 可直接 `**` 展开进 `run()`（init_cash/cost_bps/slippage_bps/t_plus_one）。
- **T+0**（backtest.py:63）：`t_plus_one=False` 时 `pos=signal` 不 shift（当日生效）；CRYPTO 不在 `T_plus_one_markets` → 自动 T+0。
- **口径标注**：单股报告尾行 `成本口径: 成本假设 CN=15bps（成本12+滑点3）· T+1`；扫描摘要附各市场口径行；卡片 markdown 末尾灰字同款。
- **向后兼容**：`positions.json` 账本历史未动；旧扁平 config 结构读法已验证（见下）。

### 单测覆盖（test_backtest_trade_layers.py）
- `test_load_by_market_layered`：CN/CRYPTO/US 分层读取 + 未知市场回退 CN + 返回键精确
- `test_load_legacy_flat_fallback`：旧扁平 config → 3bps + T+1（旧行为）
- `test_crypto_t0_shift_difference`：T+0 当日持仓、涨市收益高于 T+1、CRYPTO 链路等价显式 `t_plus_one=False`
- `test_real_config_loads`：真实 config.json 冒烟（CN=15bps T+1 / CRYPTO=10bps T+0）

---

## 验证结果

```
test_opt_discipline_gate.py   5 passed
test_backtest_trade_layers.py 4 passed
test_qpzm_backtest.py        12 passed   （既有回测域测试回归）
py_compile 全部通过；runner/opt_defaults/backtest import OK
```

### 向后兼容验证（假想旧扁平 config 实跑读法）
```
旧 config {"init_cash":500000,"cost_bps":3,"slippage_bps":1}：
  CN/US/HK/CRYPTO/None -> {'cost_bps':3,'slippage_bps':1,'t_plus_one':True}  # 旧行为原样
新分层 config（真实 config.json）：
  CN     -> {'cost_bps':12,'slippage_bps':3,'t_plus_one':True}
  US/HK  -> {'cost_bps':5, 'slippage_bps':1,'t_plus_one':True}
  CRYPTO -> {'cost_bps':8, 'slippage_bps':2,'t_plus_one':False}
  None   -> CN 保守口径
成本口径标注行实测：
  成本假设 CN=15bps（成本12+滑点3）· T+1
  成本假设 CRYPTO=10bps（成本8+滑点2）· T+0
runner.optimize_run / compare_strategies 带 market=CRYPTO 合成数据冒烟通过
```

### 测试中踩到的判例（已写进测试注释）
`pd.DataFrame({"close": px}, index=日期)` 当 px 是默认 RangeIndex 的 Series 时会 reindex 成全 NaN 列——合成测试数据必须用 `list(px)` 构造；另外 day0 的 `pct_change` 恒为 0，shift 差异要用 day1 翻多的信号才能体现。

---

## 回退方法

1. **整体回退**（回到今日前状态）：
   ```
   cd E:\老田的AI助手\quant_backtest
   copy /Y config.json.bak_20261014 config.json
   copy /Y backtest.py.bak_20261014 backtest.py
   copy /Y runner.py.bak_20261014 runner.py
   copy /Y opt_defaults.py.bak_20261014 opt_defaults.py
   ```
   （bash 下用 `cp <name>.bak_20261014 <name>`；删除两个新测试文件即可）
2. **只关纪律门（对照模式）**：config.json 中 `"opt_discipline_gate": false`——季度调参恢复旧的"样本内寻优直接 --apply"行为。
3. **只回退成本口径**：把 config.json `trade` 节还原为旧扁平 `{"init_cash":1000000,"cost_bps":3,"slippage_bps":1}`——`load_trade_config` 检测到无 `by_market` 自动走旧行为（新代码可读旧结构，无需改代码）。
4. 注意：本次 `tune()`/runner 调用点已按新读法改造，若**只**还原 `backtest.py` 而保留新 config.json 会因 `by_market` 键展开报错——回退请按上面第 1 条整体还原，或第 2/3 条只动开关与 config。
