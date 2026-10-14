# W1-2 变更记录：九维警报池命中提醒上卡（dashboard-dev，2026-10-14）

## 任务
持仓/待买票命中九维警报池（反指实证：score≥75 前向收益显著为负，≥85 段 r20 -71.5%）
→ 盘前作战卡与账本持仓页同步提醒。此前警报池只记录无动作。

## 改动文件
| 文件 | 改动 |
|---|---|
| `quant_backtest/report_page.py` | 新增 `nine_dim_cn_code()` / `nine_dim_alert_check()` / `_ledger_alert_hits()` / `_alert_deco()` / `_cfg_flag()`；`write_positions_html()` 命中行加暗红背景+⚠️警报池徽标，页脚标注数据源口径 |
| `quant_backtest/runner.py` | `morning_push()` 集成：命中时卡片新增"⚠️ 警报池命中（九维反指）"块，逐行 code+名称+命中日期+分数+band，附提示语 |
| `quant_backtest/config.json` | 新增顶层开关 `"nine_dim_alert_card": true`（默认开） |
| `quant_backtest/test_nine_dim_alert_card.py` | 新增单测 7 条 |

备份：`runner.py.bak_20261014`、`report_page.py.bak_20261014`、`config.json.bak_20261014`。

## 关键设计
- **函数位置**：`nine_dim_alert_check` 放 report_page.py 而非 runner/nine_dim——runner 已
  import report_page（反向会成环），nine_dim.py 拖 tushare/pandas 全家桶，读一个 jsonl
  不值得加载。
- **流式读取**：按行读 jsonl + code 集合过滤，不整读；坏行单行跳过。
- **"最近 20 个池内交易日"**：取池内出现过的 dt（YYYYMMDD）降序前 20 作窗口，
  免依赖交易日历；过期命中不提醒；同码取最近 dt。
- **代码对齐**：池内 code 一律带后缀（600519.SH，见 nine_dim.py 入池段）；
  账本 CN 也是全码直接对齐，裸 6 位码兜底按首位推断（6→SH，4/8/9→BJ，其余→SZ）；
  非 CN 市场（HK/US/CRYPTO）返回 None 跳过——九维只覆盖 A 股。
- **fail-open（K98 同款纪律）**：池文件缺失/损坏 = 无提醒，不炸卡不炸页（已实测）。
- **口径过滤**：只认 `pool_type=="alert"`（回放文件无该字段 → 命中 0，对照验证通过）。
- **closed 不标**：已平仓是历史，无操作意义。

## 判例/纪律遵守
- 未调用 push_feishu 发真消息（验证走纯函数级路径，未触发 morning_push 推送分支）
- 未注册计划任务、未动 positions.json 生产数据（只读）
- 原子写沿用（报告页 .tmp + os.replace 原有模式未改）
- 开关：`config.json → nine_dim_alert_card`，关 = 卡片无此块、页面无标记

## 测试结果
`test_nine_dim_alert_card.py`：**7 passed**（命中含过期过滤 / 未命中 / 非CN跳过与
代码对齐 / 文件缺失fail-open / 持仓页命中标记 / 持仓页fail-open / 开关注入）。
runner.py、report_page.py、test 文件 py_compile 通过；config.json JSON 校验通过。

## 真实警报池命中统计（只读）
- 账本 62 条（待买 19 + 持仓 43），CN 全码 7 个：
  `300684.SZ / 600641.SH / 603175.SH / 603580.SH / 603995.SH / 688112.SH / 920028.BJ`
- `data/nine_dim_history.jsonl` **尚不存在**（9DIM 日常任务首次入池前为空）→
  fail-open 返回 {}，即上线首日无提醒属预期；9DIM 首次跑完入池后自动生效。
- 对照 `nine_dim_replay_v2arch.jsonl`（code 同为 "000045.SZ" 式全码但无
  pool_type/dt 字段）→ 命中 0，证明 pool_type 口径过滤生效，不会误报。
