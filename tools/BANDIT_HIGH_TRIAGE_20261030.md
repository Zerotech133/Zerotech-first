# bandit HIGH 18 分诊（2026-10-30）

## 结论
- B324×17：均为**非安全用途**的 ID/缓存键/校验摘要（md5/sha1 前 12-16 位），不是口令/签名/完整性安全边界。
- B602×1：`quant_backtest/tools/d1_s1_precheck.py` 的 `shell=True` 固定命令 `grep -c`，无外部输入拼接，属预检工具误报。
- 处置：加入 `tools/bandit_baseline.json` 基线（仅这 18 条），**未来新增 HIGH 仍会报**；不修改业务逻辑。
- 后续：新代码用 `hashlib.md5(..., usedforsecurity=False)` 或 `sha1(..., usedforsecurity=False)`；禁止用弱哈希做安全用途。

## 18 条清单
- B324 news_collector\jev_feed.py:75 Use of weak MD5 hash for security. Consider usedforsecurity=False
- B324 news_collector\news_collector.py:379 Use of weak MD5 hash for security. Consider usedforsecurity=False
- B324 news_collector\news_collector.py:491 Use of weak MD5 hash for security. Consider usedforsecurity=False
- B324 news_collector\send_wecom.py:87 Use of weak MD5 hash for security. Consider usedforsecurity=False
- B324 quant_backtest\_build_spatial.py:42 Use of weak MD5 hash for security. Consider usedforsecurity=False
- B324 quant_backtest\chanlun_learn.py:171 Use of weak SHA1 hash for security. Consider usedforsecurity=False
- B324 quant_backtest\chanlun_learn.py:796 Use of weak SHA1 hash for security. Consider usedforsecurity=False
- B324 quant_backtest\distill_pool.py:60 Use of weak MD5 hash for security. Consider usedforsecurity=False
- B324 quant_backtest\event_graph.py:313 Use of weak MD5 hash for security. Consider usedforsecurity=False
- B324 quant_backtest\guard_alert.py:216 Use of weak SHA1 hash for security. Consider usedforsecurity=False
- B324 quant_backtest\k266_m3a_events.py:110 Use of weak MD5 hash for security. Consider usedforsecurity=False
- B324 quant_backtest\linreg.py:211 Use of weak SHA1 hash for security. Consider usedforsecurity=False
- B324 quant_backtest\multi_agent.py:282 Use of weak MD5 hash for security. Consider usedforsecurity=False
- B324 quant_backtest\nl_query.py:244 Use of weak MD5 hash for security. Consider usedforsecurity=False
- B324 quant_backtest\report_ai.py:243 Use of weak SHA1 hash for security. Consider usedforsecurity=False
- B324 quant_backtest\research_agent.py:110 Use of weak MD5 hash for security. Consider usedforsecurity=False
- B602 quant_backtest\tools\d1_s1_precheck.py:63 subprocess call with shell=True identified, security issue.
- B324 quant_backtest\trial_ledger.py:171 Use of weak SHA1 hash for security. Consider usedforsecurity=False
