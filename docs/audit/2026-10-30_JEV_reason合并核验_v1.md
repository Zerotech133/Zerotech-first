# JEV reason 合并核验与收口

- 日期：2026-10-30
- 工单：`K-ORG-20261030-BATCH4`
- 结论：**已在主线落地，无需重复修改**；本地判读不再每条多调一次模型

## 1. 代码现状

提交：

```text
8748c9a8a K-ORG-20261030-BATCH4
5f9c6bf6a merge task/K-ORG-20261030-BATCH4
```

`news_collector/news_ai.py:jev_judge_one_local`：

- prompt 的 JSON 已包含：

```json
"reason":"30字内判断依据（只写依据本身）"
```

- 单次调用预算：

```text
num_predict = 260
```

- 返回处理：

```python
_reason = str(j.get("reason") or "").strip()[:60]
d["reason"] = _reason or jev_reason(...)
```

即：**reason 随判定同一次返回**；只有模型漏给时才回落单独调用。

## 2. 回归测试

`tools/tests/test_jev_reason_merge.py` 显式断言：

```python
monkeypatch.setattr(news_ai, "jev_reason",
    lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应再单独调用")))
...
assert r["reason"] == "监管处罚直接利空"
assert calls["n"] == 1
```

实测：

```text
pytest tools/tests/test_jev_reason_merge.py
       tools/tests/test_jev_backfill_loop.py
       tools/tests/test_jev_night_wrapper.py
→ 31 passed
```

提交已包含在当前 master。

## 3. 顺带核验的两项排期

审计 A2/A3 已不是缺口：

```text
JevFeed-Verify
  trigger 22:30（start=2026-10-26T22:30）
  next=2026-10-30 22:30
  last result=0

BackupDaily
  trigger 07:45（start=2026-10-31T07:45）
  last=2026-10-30 03:00
  next=2026-10-31 07:45
  last result=0
```

两项当前均为单触发器，未发现审计当时的 21:30/EndBoundary 过期问题。

## 4. 边界说明

- 本地路径：已合并 reason，省一次本地模型调用；
- 云 typesafe 路径：仍是“云判定 + 本地 reason”两次调用；
  云端 `questions` 只支持 choice，不支持自由文本 reason；
  当前云路径不是主吞吐瓶颈，后续如切云为主再单独设计。
- `news_collector/jev_backfill.py` 注释“不改变判定逻辑”属于旧描述，
  不影响运行；如需要可单独清注释。

## 5. JEV 剩余 P1（未做）

按审计顺序：

1. `news` 表加 `model/judged_at/attempt/last_error`（P2，静默丢单土壤）；
2. 动态 batch（当前固定 600，deadline_hit 常态）；
3. `xq_classify` 接 `ollama_lock`（跨线，需 xq 配合）；
4. 低置信复核队列（10,516 条，10.3%）。

下一步建议先做 **news 表溯源/失败列**，它能把“静默失败”变成可告警数据。
