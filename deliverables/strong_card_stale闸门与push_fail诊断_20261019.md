# strong_card stale 闸门 + push fail 诊断备忘（2026-10-19）

工位：WS-CARD（strong 名单守卫）｜改动文件：`quant_backtest/strong_card.py`、
`quant_backtest/test_strong_card_stale.py`（新增）、备份
`quant_backtest/strong_card.py.bak-20261019`（勿 git add）。

## 一、事故复现路径（一句话）

StrongEod 的 scan 步骤被 freshness_guard 拦截（exit 3，10-13 起板块仓停）
→ `reports/strong_latest.json` 停留 10-13 旧数据 → card 步骤
`strong_card --send` 照常读到旧名单（`--latest` 默认路径、`date` 字段取自
JSON 顶层，strong_card.py 原 156 行）且无任何时效校验 → 119 只「板块领涨」
全量静默重推，`strong_state.json` 全条目 ts 被刷成 2026-10-19 15:46。

## 二、闸门实现要点

- 判据同源：待发名单 `date`（strong_latest.json 顶层）== 今日（ISO）。
  该字段与 strong_scan.py 熔断判据（strong_scan.py:231）同源同字段——
  scan 当日落盘时写 `date=today_str()`（strong_scan.py:325）。scan 被拦
  即停留旧 date → 闸门必拦。
- closed on failure（strong_card.py `stale_check`）：
  - date 空（裸数组名单 `load_latest` 返回 ""）→ 拦；
  - 非 `YYYY-MM-DD` 严格格式（含 20261019 紧凑式——fromisoformat 3.11+
    也吃它，已显式挡掉）→ 拦；
  - date != 今日 → 拦。
- 位置与顺序：闸门在 `--send` 分支最前、先于每日熔断与推送执行；
  dry 路径不设闸（不推不发，仅落盘留档）。
- 告警复用原语：拦截即调 `guard_alert("StrongCard", ...)`（四要素卡、
  12h 去重、原子写、不反噬主流程），不新造第二套；告警链自身故障
  （guard_alert 抛错）只打点不改变拦截结果——告警缺席不开闸
  （`stale_alert` 内 try/except，对齐 strong_scan.py:245-259 惯例）。
- 退出码：拦截 exit 3（对齐 strong_scan freshness_guard 拦截语义）。

## 三、逃生口（--force）设计取舍

- 保留 `--force`（ argparse 新增）：完全不给逃生口会把「旧名单人工确认
  后确需补推」的合理场景逼向改代码/改状态文件，风险更高。
- 强推必留痕：`--force` 路径仍先走 guard_alert 告警（reason 注记
  「--force 强推放行，留痕」），推送失败也不阻断强推（告警不反噬），
  stdout 同步打 `[stale 闸门] … --force 强推放行` 行。
- 测试覆盖：`test_force_stale_list_passes_with_alert` /
  `test_force_today_list_no_alert`。

## 四、测试数字

- 新增 `test_strong_card_stale.py`：**8 passed**（0.78s），覆盖
  旧名单拒发/当日放行/告警留痕/逃生口/closed on failure/推送失败不写
  熔断状态；零真实推送（send_card、guard_alert 双打桩），state/outbox
  全部指 tmp_path，未跑 --send 实弹。
- 密钥扫描：`PY tools/scan_secrets.py quant_backtest/strong_card.py
  quant_backtest/test_strong_card_stale.py` → 未豁免命中 **0**，退出码 0。
- 基线 `PY -m pytest strategy_layer/tests/ -q`：**680 passed, 1 failed,
  1 skipped, 1 xfailed**。passed ≥ 基线 659，skip/xfailed 持平；唯一
  失败 `test_combined_verdicts.py::test_load_rows_pg_tuple_and_dict_paths[True]`
  属**另一在途工位**：`strategy_layer/combined_verdicts.py` 与其测试
  在工作区已被他人修改（git status M，本工位未碰），失败点
  combined_verdicts.py:133 KeyError: 0，且该测试不 import strong_card，
  与本改动无关联。本工位测试文件在 quant_backtest 根下（对齐
  test_guard_alert.py 平铺惯例），故单独跑绿并在此说明。

## 五、guard_alert push fail 诊断（只读，未改告警链）

现象：`quant_backtest/logs/guard_alert.log`（2026-10-14 15:18 起）累计
**17 次 push fail**，全部 key=`guard_1331630b87d9314b`（10-14 06:30
StrongEod 拦截告警的去重键），时间跨 10-14 15:18 → 10-19 04:34。

证据链：
1. 告警链：guard_alert._push（guard_alert.py:49-52）→ runner.push_feishu
   （runner.py:48-60）→ news_collector/send_feishu.py 双通道（通道1
   OpenAPI 静态凭证 / 通道2 lark-cli 回退）。push fail = stdout 非
   SEND_OK 开头。
2. **可观测性断点（确定的链上缺陷）**：guard_alert.py:105 只记
   `push fail key=…`，`_push` 返回 bool，send_feishu 的
   `SEND_FAIL <原因>` 全程被丢弃——17 次失败无一留下原因。这是首要
   修复建议（下批）：把 send_feishu 的失败原因带进 guard_alert 日志。
3. 通道侧根因（高置信）：失败窗口 10-14~10-19 恰为 **20261016 换机**
   期。send_feishu.py:57-72 注释实证该失败模式：「appSecret 非 str
   （1.0.5+ 外置引用 dict）即不可用 → (None,None) 通道1 干净失败」；
   通道2 lark-cli「计划任务等独立进程无凭证可用（token_missing）」
   （send_feishu.py:12-13）——两通道同断即 push fail，且计划任务
   无人值守场景无人工授权兜底。
4. 通道已恢复的证据：`data/guard_alert_state.json` 现存三次成功写状
   态记录：10-15 06:30:03、10-18 15:45:08、10-19 15:45:29（ StrongEod
   拦截告警推送成功）；当前 `~/.lark-cli/config.json` appSecret 为
   str（本次实测，未打印值）→ 通道1 健康。
5. 未决异常（如实报，建议下批查）：同秒出现 `dedup skip (sent
   2026-10-14 06:30:00)` + `push fail` 成对日志，且远超 12h 去重窗后
   （如 10-14 19:01）仍 dedup skip——与当前 guard_alert.py 代码语义
   不符；同一窗口内 `cross_src_9dim` 键推送成功而 guard 键失败。
   疑点指向：多进程并发写读状态竞争 / 存在第二份 checkout 的
   guard_alert 在跑（旧代码语义）。且该 key 现已不在状态文件中
   （疑被某次全量覆写或人工清理）。建议用 strong_guard_precheck.py
   + 一次 guard_alert dry 人工复现核实。

## 六、遵守的互斥约束

未改 strong_scan.py / strong_guard_precheck.py / strong_state.json /
outbox/ / 告警链任何文件；未 commit；未真发飞书；未连真实 PG；
strong_card.py.bak-20261019 未 git add。
