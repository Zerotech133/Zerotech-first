# P1 尾巴三小件改动留痕（20261014_p1_tail）

执行计划 v1.2 P1 尾巴三小件。纪律遵守：未注册/未修改任何计划任务；未推真飞书；
未读写生产 positions.json（单测路径参数化走 tmp）；解释器统一
`C:/Program Files/Python311/python.exe`；改前备份统一后缀 `.bak_20261014_t`。

## 小件1：双刀多源降级（腾讯→新浪→东财）

### 背景与判例
qd_scanner（portal/）实时行情单依赖腾讯源，W3-4 曾因腾讯源末根错日整段不可信。

### 改动点
- `portal/qd_scanner.py`：
  - 新增多源降级层：`_kline_tencent/_kline_sina/_kline_east`（日K，统一映射为
    腾讯行格式 `[date, open, close, high, low, vol]`；新浪 volume 股→手 ÷100
    对齐量纲；东财 fqt=1 前复权、f51..f56 字段序）与
    `_quote_tencent/_quote_sina/_quote_east`（快照统一字段：价格/涨跌幅/
    成交量/时间戳；新浪 hq 需 Referer 头，东财 fltt=2 免手工缩放）。
  - `kline_multisource()` / `quote_multisource()`：腾讯→新浪→东财顺序降级；
    **每源末根时间戳校验，错日=该源失败**（判例 W3-4）；全源失败返回
    `(None, "all_failed", 降级链留痕)`，调用方显式跳过，不假绿。
  - `intraday_recheck()` 接入：候选票全源失败/错日 → 跳过且 checked 不计入、
    note 落「全源失败/错日（…），候选 xxx 跳过（不假绿）」；命中时 note 落
    `source=tencent / sina_fallback / east_fallback`（含降级链明细）。
  - 主流程信号票快照补全接入：备选源命中时 `info["quote_source"]` 留痕；
    备选源缺换手/市值字段则不写该键（不造默认值，下游 .get 容错）。
- `portal/qd_scanner_config.json`：新增开关 `qd_multi_source: true`
  （**缺省 true**——代码缺 key 亦为开；显式 false 回退仅腾讯旧行为）。

### 开关
| 开关 | 位置 | 缺省 | 关闭效果 |
|---|---|---|---|
| `qd_multi_source` | portal/qd_scanner_config.json | true | 仅腾讯，失败即败（与改动前一致） |

### 测试结果
`portal/test_qd_multisource.py`（mock HTTP 层，不真拉）：**14/14 PASS**
- T1 源切换顺序（腾讯败→新浪；双败→东财）
- T2 错日判定（错日=源失败自动切下家）
- T3/T3b 全源失败显式报错不假绿（返回 None+all_failed；端到端候选跳过）
- T4 开关关闭（仅腾讯零备选调用；config 缺省开/显式关两种读法）
- T5 降级命中端到端（真引擎重算，note 落 source=sina_fallback）
- T6 新浪快照字段映射

回归：`test_qd_amount_fix.py` 15/15 PASS、`test_qd_breadth_note.py` 8/8 OK。

## 小件2：runner.py 账本去重 + max_holding_days

### 改动点
- `quant_backtest/runner.py`：
  - `update_ledger(refresh_signals=True, ledger_path=None)`：路径参数化
    （默认 positions.json，单测传 tmp），写入走既有 `_atomic` 原子写。
  - 入账幂等：`_sig_key()` = code+signal_date+strategy，对 pending/open/closed
    全节查重；同键已入账不再入、重复跳过并计数——落日志
    `LEDGER dedup_skip=N（同信号重复入账已跳过）`。既有「同 code+strategy
    在册不加」的持仓语义原样保留（两层并存）。
  - `max_holding_days()`：config `backtest.max_holding_days`（**缺省 60**）。
    open 持有超 N 个交易日强制平仓入 closed 并标注 `forced_close: true`
    （区别于翻空正常平仓，正常平仓主动清除该标注防语义串）。持有交易日数
    用 `bars_held` 增量计数（建仓日=1，每日新 bar +1，与 marks 打点同
    ffill 语义、同日重跑不胀）；旧档无 bars_held 用 marks 长度回填，再无
    按自然日回填（保守偏高，宁可早平不强拿）。
  - 日志新增：`LEDGER forced_close=N（超 max_holding_days=60 强平）`。
- `quant_backtest/config.json`：最小增量加节点
  `"backtest": {"max_holding_days": 60}`。

### 开关
| 开关 | 位置 | 缺省 | 说明 |
|---|---|---|---|
| `backtest.max_holding_days` | quant_backtest/config.json | 60（键缺失亦 60） | 调大/调小即时生效；设极大值≈关闭强平 |

### 测试结果
`quant_backtest/test_runner_ledger_p1tail.py`（合成数据，update_ledger 路径
参数化写 tmp，不碰生产 positions.json）：**16/16 PASS**
- T1/T1b 重复信号跳过（历史已平仓同键、账内既有重复对）+ dedup_skip 落日志
- T2/T2b 超期强平标注（bars_held=60 强平入 closed + forced_close；59 继续
  持有）
- T3 config 缺省值（空配置/缺键→60，显式值生效，真实 config=60）
- T4/T4b 与 ffill 语义共存（信号连续 1 只建一次仓、同日重跑幂等、持有计数
  递增不误平、翻空正常平仓无 forced_close 标注）

## 小件3：shadow_weekly_report 挂 WeakWeeklyReport 链

### 改动点
- `quant_backtest/weak_track_weekly.py`（WeakWeeklyReport 计划任务所调
  脚本，deploy_weak_weekly.ps1 → weak_track_weekly.py --send）：
  - 新增 `shadow_weekly_tail()`：注记式 subprocess 调
    `shadow_weekly_report.py --json-only`（cwd=quant_backtest，超时 300s，
    rc/stderr 只落日志，任何失败不影响 §15 周报主流程）。
  - main() 三个出口（空转跳过 / 预览 return / 推送完成）尾部均挂该调用，
    与计划任务无任何注册/修改关系。
  - 回退：删 `shadow_weekly_tail()` 函数与三处调用即可，零其他耦合。

### 验证
直接跑入口（预览模式，无 --send 不推送）：
```
SHADOW_TAIL rc=0 已写出: E:/老田的AI助手\quant_backtest\reports\shadow_weekly_report_data.json
```
产物落盘且内容有效：`reports/shadow_weekly_report_data.json`（week_label
2026-W42，5 条观察线）+ `reports/shadow_weekly_report.md`（11KB）。

回归：`test_weak_weekly.py` ALL PASS。

## 测试汇总

| 项 | 结果 |
|---|---|
| portal/test_qd_multisource.py（新增） | 14/14 PASS |
| portal/test_qd_amount_fix.py（回归） | 15/15 PASS |
| portal/test_qd_breadth_note.py（回归） | 8/8 OK |
| quant_backtest/test_runner_ledger_p1tail.py（新增） | 16/16 PASS |
| quant_backtest/test_weak_weekly.py（回归） | ALL PASS |
| 小件3 端到端验证 | SHADOW_TAIL rc=0，产物更新 |

## 回退方法

改前备份（恢复即回退，配置/数据零残留）：
```
copy /y portal\qd_scanner.py.bak_20261014_t            portal\qd_scanner.py
copy /y quant_backtest\runner.py.bak_20261014_t        quant_backtest\runner.py
copy /y quant_backtest\config.json.bak_20261014_t      quant_backtest\config.json
copy /y quant_backtest\weak_track_weekly.py.bak_20261014_t  quant_backtest\weak_track_weekly.py
```
软回退（不还原代码）：
- 小件1：`qd_scanner_config.json` 置 `"qd_multi_source": false` → 仅腾讯旧行为；
- 小件2：`config.json` 的 `backtest.max_holding_days` 调大（如 999999）≈关闭
  强平；去重逻辑无开关（纯安全侧，如需彻底回退用备份还原）；
- 小件3：删除 `weak_track_weekly.py` 中 `shadow_weekly_tail()` 及三处调用。
新增测试文件（test_qd_multisource.py / test_runner_ledger_p1tail.py）可直接删除。
