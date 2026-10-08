# 自动化 28344874（影子运行数据采集）执行记忆

## 2026-10-08（周四）17:10 触发
- 首次执行。脚本退出码 0；发现今日 14:57:58 已有一轮完整采集（5 票 done 入账），本次重跑 5 票全部 [skip]，幂等判据验证通过，零新增行。
- 今日决策摘要（账本 data/shadow/shadow_ledger.jsonl）：
  - 600519 贵州茅台 HOLD 0.55 run_id=e1d80bc78cfb degraded=false 价 1255.0
  - 000858 五粮液 HOLD 0.60 run_id=d585d3f68977 degraded=false 价 70.14
  - 601318 中国平安 HOLD 0.60 run_id=4ff2a7f8d373 degraded=false 价 52.67
  - 300750 宁德时代 HOLD 0.45 run_id=1642fb3d7ea9 degraded=false 价 286.75
  - 600036 招商银行 HOLD 0.55 run_id=fd3db6e43972 degraded=false 价 41.71
- 无 failed 票。执行命令模式：`python run_shadow.py >> data/shadow/last_run.log 2>&1`，基线快照存 /tmp/shadow_ledger_before.jsonl 后 diff 取新增行。
- 后续执行可沿用此流程；若当日已有 5 票 done 则预期全部 skip，直接摘要既有行即可。
