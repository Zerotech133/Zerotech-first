# 20261014 试验台账 + Deflated Sharpe Ratio（trial-ledger，G2 闸门加固）

## 一、出处与定位
开源借鉴 #1（purgedcv/ml4t-diagnostic 思想，**纯 stdlib+math 自实现零新依赖**，
《十二策略对照复盘与开源借鉴报告》优先级第 1）。动机：本会话各参数网格都是
"事后从 N 次试验挑最优"——幸存者偏差使报告 Sharpe 系统性偏高；DSR
（Bailey & López de Prado 2014）按试验次数 N 折减后判断显著性。

## 二、新增文件
- `quant_backtest/trial_ledger.py`：
  - DSR 数值核（纯 math）：`_norm_ppf`（Acklam 逆正态）、
    `expected_max_sharpe`（N 试验最优 SR 期望基准，**N=1 → SR0=0 退化 PSR**）、
    `var_sharpe`、`deflated_sharpe_ratio`、`sharpe_from_returns`；
  - 台账 `data/trial_ledger.jsonl`：幂等键 trial_id=sha1(策略|配置指纹)、
    O_APPEND+fsync+锁（判例 gate_shadow_collect）、坏行容忍；
  - CLI：record（含 dsr/sr0 自动计算与 ≥0.95 参考线判定，--path 可覆盖）/
    dsr / stats。
- `quant_backtest/test_trial_ledger.py`：**9/9 过**（N=1 退化、N 单调惩罚、
  强弱 SR 过线双向 sanity、逆正态精度、幂等/坏行/CLI）。
- `quant_backtest/tools/backfill_trial_ledger_20261014.py`（一次性）+ 台账
  **11 条真实回填**（cross 36 组合网格/chanlun 9 档/双刀 3 组/qpzm 门控/
  ninedim 行业分位/strong 自校准/linkage 择时/9dim 反向——**含全部被否决
  试验**，sharpe 未知处留 None 不造值）。示范：chanlun 否决档 DSR=0.0347
  正确判"不显著"。

## 三、G2 闸门对接（约定，不改代码）
W9 起任何参数网格/回测试验落台账（record 一行）；G2 评审材料增列
"DSR 读数"——DSR<0.95 的"最优"参数视为网格幸存者，不得凭其过 G2。
影子周报可在后续迭代挂 stats。

## 四、开发过程修正
1. N=1 时 `_norm_ppf(1-1/1)=ppf(0)` 越界 → expected_max_sharpe 提前返回 0；
2. record 子命令缺 --path（测试曾写进真台账——该行恰为真实试验记录，保留）；
3. 回填脚本 sys.path 指向错层（tools/ 下需取 dirname）。

## 五、回退
- 删 trial_ledger.py / test_trial_ledger.py / tools/backfill_*.py 即回退；
- data/trial_ledger.jsonl（+ .lock）可直接删，纯记录无产线引用。

## 六、局限
- var_sr 缺省用最优试验自身估计（保守常用口径），跨试验方差实测后可替换；
- DSR 只治"挑出来的 Sharpe"，不治数据窥探/口径漂移（那些靠 OOS 纪律门）。
