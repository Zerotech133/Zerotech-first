# C3 采纳 beacon：decision_id / card_id 字段接口变更单（待前端）
> STATUS: unspecified（owner: TBD；2026-11-07 前补状态或标 superseded）



- 工单：`WO-LEGION-C3DID-20261027`
- 日期：2026-10-27
- 性质：**只加关联字段，不加执行面**；旧报文完全兼容；前端接入另行排期。

## 1. 现状

`POST /api/zhuge/adoption` 接收 `{items:[{at,domain,ua}]}`，追加到
`portal/data/zhuge_adoption.jsonl`。只有「建议域」，无法精确关联某条决策。

## 2. 变更（已实现，待 8091 重启生效）

`items[]` 新增两个**可选**字段：

| 字段 | 格式 | 说明 |
|---|---|---|
| `decision_id` | `DR-YYYYMMDD-NN`（如 `DR-20261027-01`） | 该建议对应哪条决策记录（首选） |
| `card_id` | `ZQ-YYYYMMDD-NN`（如 `ZQ-20261027-01`） | 对应 C1 质询卡（可选备选） |

- 缺省/空串 = 旧行为（按 domain 聚合）；
- 非法格式 → 400，整批不落盘（与现有一致）；
- 服务端补 `received_at`；不记录任何客户原文/PII。

请求示例：

```json
{"items": [
  {"at": "2026-10-27T10:00:00+08:00", "domain": "notify:deadline:us:postclose",
   "decision_id": "DR-20261027-01"}
]}
```

## 3. 服务端行为

1. `api_zhuge_adoption.append_items` 白名单与校验（已改）：见 `portal/api_zhuge_adoption.py`；
2. C3 桥 `tools/zhuge_c3_decisions.py`：**优先按 `decision_id` 精确回填**
   （`adoption_id="C3:decision:<id>"`、outcome 标「精确匹配」）；
   无 `decision_id` 时仍走 `adoption_domains` 匹配；
   未匹配的 decision_id 在 `unmatched_decision_ids` 报告，不猜测；
3. C1↔决策、每日同步逻辑不变（`ZHUGE_C3_DECISION_SYNC=1`）。

## 4. 前端接入（另单）

- 页面生成建议卡时，把服务端返回的 `decision_id` 放进 beacon item；
- sendBeacon 不能带自定义头，保持匿名内网写面；只传 ID，不传问题/用户正文；
- 兼容期：前端可先只传旧三字段，后端不会拒绝；
- 上线顺序：后端起效（重启 8091）→ 前端灰度传 `decision_id` → 观察未匹配数。

## 5. 验收

- `portal/test_api_zhuge_adoption.py`：18 passed（含 decision_id/card_id 合法落盘、非法 400）；
- `tools/test_zhuge_c3_decisions.py`：7 passed（含精确匹配/未匹配报告/domain 兜底）；
- 旧报文回归：原有用例全过（兼容）。

## 6. 边界

- 不新增 L2/计划任务；8091 已于 2026-10-27 14:38 受控重启（`98742bcf3` 生效）；
- 不猜测、不自动补 decision_id；未匹配只报告；
- decision_id 只用于采纳统计与决策复盘，不触发任何执行动作。

## 7. 更优路径：链接 query（2026-10-27 追加，推荐）

C3 v3 本身就是**服务器侧 access-log 归集**（页面禁 POST/第二 fetch）：
建议卡下钻时跳 `/dev`、`/intel` 等领域路径，nginx 日志即可当采纳信号。
因此**不需要新增 sendBeacon**，只需在建议卡链接上带参数：

```text
/dev?decision_id=DR-20261027-01&card_id=ZQ-20261027-01
```

- `portal/harvest_adoption.py` 已解析 GET query 的 `decision_id/card_id`（格式校验，非法忽略）；
- 归集器按 5 分钟窗口去重，去重键含 decision_id（不同决策不互相吞）；
- 页面侧改动极小：`linkFor` 拼接可选参数，无 JS、无 POST、无 storage，契约不变；
- 灰度：只给部分建议卡加参数 → 观察 `zhuge_c3_decisions.py --report` 的精确匹配/未匹配数。
