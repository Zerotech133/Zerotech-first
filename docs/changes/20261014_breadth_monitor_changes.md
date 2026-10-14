# 20261014 宽度穿越监测器落地（breadth-mon）

## 一、出处与定位
《历史数据全景复盘与当前正确路径_2026-10-14》正确路径执行面第一条可动作：
当前宽度 32.64%（regime=range、信号枯竭），**不抄底；等宽度上穿 45% 穿越确认
再评估入场；假穿越防护 = 穿越后 5 个交易日内跌回 40% 以下撤回**。本改动把该
规则落成日频监测（只告警不拦任何策略行为，阈值定档仍留 W8 评审）。

## 二、新增文件
- `quant_backtest/breadth_crossing_monitor.py`：状态机监测器。
  - 数据源 `reports/qd_breadth_series.json`（与 gate_shadow_collect 同口径；
    **历史段存在 null 宽度锚，load_series 跳过非法锚**——首跑即踩到）；
  - 三类事件：CROSS_UP（上穿 45%，提醒评估入场）、FAKE_EXIT（5 交易日内
    <40%，提醒撤回）、CONFIRM_HOLD（满 5 日未跌破 40%，提醒确认保持）；
  - `armed` 武装位：同 episode 只告一轮——CROSS_UP 后解除（观察窗内回落
    再上穿、确认后 40~45 区间震荡不再重复告警）；FAKE_EXIT 撤回后重新武装
    （翻案可再触发）；无观察态时深度回落 <40%（新下跌-修复周期）重新武装；
  - 状态 `data/breadth_cross_state.json`（原子写）+ 事件留痕
    `data/breadth_cross_events.jsonl`（O_APPEND+fsync）；
  - 首跑播种模式：不回放 912 天历史不告警，last_date 置最新锚（实测播种
    last_date=20261013）；告警经 guard_alert.alert_once（键含事件日期，
    天然去重；推失败落日志不反噬）；
  - CLI：`--dry`（推演不落盘不推送）/`--seed`（强制播种）。
- `quant_backtest/test_breadth_crossing_monitor.py`：8 用例（上穿/假穿越/
  满窗确认/播种幂等/增量落档/dry 不写/序列缺失 fail-open/翻案再触发/
  告警文本自解释），**8/8 过**。

## 三、挂载
`qpzm_daily_batch.archive_daily_batch()` 尾部（gate_shadow_collect 同位，
每日链 14:40），注记式 try/except，monitor_run 自身不抛，失败不影响产线。
无新计划任务（复用现有日批）。

## 四、开发过程修正（自记）
1. 状态机 v1 缺陷：观察窗内回落再上穿会重复 CROSS_UP 并重置假穿越时钟 →
   引入 armed 位修复；
2. `advance` 跨调用 prev 缺失（首新锚恰为穿越日会漏判）→ 增加 prev_br
   续接参数，monitor_run 从序列 last_date 锚取；
3. 真实序列 null 锚致 float(None) 崩 → load_series 过滤；
4. 测试两处预期写错（无穿越在先的跌破 40% 不是假穿越；播种起点选错）→
   修正测试而非产品。

## 五、回退
- 移除 qpzm_daily_batch 尾部 BREADTH_MONITOR 段（或整段注释）即回退；
- 监测器为纯新增文件，可直接删除；状态/事件 jsonl 可直接删除。

## 六、遗留
- 阈值 45/40/5 日为复盘观察口径，**W8 评审定档**（与三策略统一观察带
  45~50% 议题合并评审）；
- 交叉验证：宽度序列锚为 T-1 语义（qd 口径），告警文本已按此理解撰写。

## 七、周报联动（shadow_weekly_report 市况·正确路径节，2026-10-14 16:50）
- 新增 `market_path_status()` + `render_market_path_md()`：读宽度序列
  （跳过 null 锚）+ 穿越监测状态，产出第六节——最新宽度/距 45% 距离/
  连续 <45% 天数/近 20 日最低/监测器状态/复盘口径指引与历史参照
  （同款状态后 20 日 +3.9%、穿越后 +6.4%，静态出处已标注）；
- 原"触发评审的条件提醒"顺延为第七节；payload 增 market_path 字段；
- 实跑验证：宽度 32.64%（锚 20261013）、距穿越线 12.36pp、连续 8 日
  <45%、近 20 日最低 20.57%、指引=深度弱势不抄底；回归 31 测试全绿
  （含 shadow 三件+qpzm percentile）；首跑踩到 guide 非格式化串 `%%`
  原样显示，已修；
- 周五 18:00 影子巡检自动化零改动即带出新节（周报脚本内部变化）。

## 八、代码审查遗留件修复（2026-10-14 17:15）
- kg_client.py:31 缺 neo4j 驱动 SystemExit→ImportError（W5d 交决策建议采纳
  落地；SystemExit 绕过 except Exception 炸批量流程；回归 test_ui_drilldown
  + test_kg_schema_smoke 94 测试全绿；备份 .bak_20261014_kgfix）。
- 3 卖分支 zg/zd 不对称、linkage ①超额口径：仍留拍板（见
  十二策略对照复盘报告第二节）。
