# JEV P1 四项落地：schema / 动态 batch / xq 锁 / 低置信队列

- 日期：2026-10-30
- 工单：`K-JEV-20261030-P1`
- 范围：news.db 契约、回填产能、跨线锁、低置信复核

## 1. news 表溯源/失败列

新增（idempotent，首次迁移自动备份）：

```text
model      TEXT
judged_at  TEXT
attempt    INTEGER NOT NULL DEFAULT 0
last_error TEXT
```

- `jev_backfill` 启动时自动 `ensure_news_meta`；
- 成功写 model/当前时间、`attempt+1`、清空 `last_error`；
- 失败（含异常/None）写 `attempt+1`、`last_error`，不再静默丢弃；
- 旧行这些列为 NULL/0，属正常；
- 备份目录：`news_collector/backups/`。

## 2. 动态 batch

`news_collector/jev_batch.py`：

- 从最近 20 轮 `logs/jev_backfill_metrics.json` 取单条耗时中位数；
- 无历史默认 3.0s；
- `batch = (deadline - 30s) * 0.85 / item_s`，夹在 8~600；
- `--batch 0` 启用（默认）；`--batch N` 仍保留人工覆盖；
- 每轮落 metrics，供下一轮估算；
- `JevBackfill` 小时脚本与 `jev_night.py` 默认改为 `--batch 0`。

## 3. xq_classify 接 ollama_lock

- `quant_backtest/xq_classify.py` 导入仓库根 `ollama_lock`；
- `run` / `retry_degraded` / `recontent_run` 入口统一拿 `xq_classify` 租约；
- 抢不到（等待上限默认 120s）记 `OLLAMA-LOCK yield` 并本轮让位；
- 每条模型调用后 `heartbeat()`，结束 `release()`；
- 开关：`XQ_OLLAMA_LOCK=0` 可完全回退。

## 4. 低置信复核队列

`news_collector/jev_review_queue.py`：

```sql
CREATE TABLE jev_review_queue (
  id, queued_at, conf, bias, title, stocks, original_jev,
  status, reviewer, reviewed_at, decision, note
)
```

- 口径：已判且 `conf < 0.7`（可用 `JEV_REVIEW_CONF` 覆盖）；
- `--build` 批量入队，`--status` 汇总，`--list` 查看，`--resolve` 裁决；
- 入队用 `INSERT OR IGNORE`，幂等。

## 5. 验证

```text
news_collector/test_jev_p1_fixes.py → 4 passed
既有 JEV 测试（lock order/feed/backfill/night）→ 43 passed
```

## 6. 生产迁移顺序

1. 停/确认无回填进程；
2. `python news_collector/jev_schema.py`（自动备份 + 加列）；
3. `python news_collector/jev_review_queue.py --build --limit 200000`；
4. 下一次 `JevBackfill` 自动使用动态 batch；
5. 回退：`XQ_OLLAMA_LOCK=0`、`--batch 500`、删表/列需人工（列保留不影响旧代码）。
