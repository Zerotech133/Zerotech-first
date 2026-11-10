# 2026-11-07 R7 `verify_fix` 最终定稿 diff（候选，未写生产图）
> 数据源：`_merged_all_20261106_v12_draft.jsonl`（2642 条）。  
> 定稿候选：`C:\项目\知识库\95_KG\_assertions\_merged_all_20261106_v12_r7_final.jsonl`（2642 条）。  
> 状态：R7 已按老田批复定稿；生产图仍是 2639 条；K6 仍 pending，等回复后再进 v12 final。
| # | assertion_id | issue | 定稿改动 | 字段数 |
|---|---|---|---|---:|
| 01 | `asrt_00599b9018b4` | not_supported | `condition.text`；`action.text` | 2 |
| 02 | `asrt_1261f7186e0f` | not_supported | 无（已满足） | 0 |
| 03 | `asrt_1a9596d551d7` | not_supported | `condition.text`；`action.text` | 2 |
| 04 | `asrt_281277238074` | not_supported | `signals`；`rationale` | 2 |
| 05 | `asrt_cf4f096d74ec` | vacuous | `action.verb`；`action.text`；`rationale` | 3 |
| 06 | `asrt_69c6179e25e7` | vacuous | `action.verb`；`action.text` | 2 |
| 07 | `asrt_bee96217775f` | vacuous | `action.verb`；`action.text` | 2 |

## 01 `asrt_00599b9018b4`
- issue：`not_supported`
- verify_fix：条件=向下跌破CD / 动作=关注CD的1.38和1.68
```diff
--- asrt_00599b9018b4.before.json
+++ asrt_00599b9018b4.after.json
@@ -2,11 +2,11 @@
   "assertion_id": "asrt_00599b9018b4",
   "kind": "RULE",
   "condition": {
-    "text": "向下跌破CD且放量"
+    "text": "向下跌破CD"
   },
   "action": {
     "verb": "identify",
-    "text": "CD的1.38和1.68"
+    "text": "关注CD的1.38和1.68"
   },
   "target": {
     "scope": "any",
```

## 02 `asrt_1261f7186e0f`
- issue：`not_supported`
- verify_fix：理由='日线不在背驰段时看三十分钟买点足够'
- **无字段变化**：当前 assertion 已满足该 verify_fix。

## 03 `asrt_1a9596d551d7`
- issue：`not_supported`
- verify_fix：删除动作，改为'识别净收权利金策略'
```diff
--- asrt_1a9596d551d7.before.json
+++ asrt_1a9596d551d7.after.json
@@ -2,11 +2,11 @@
   "assertion_id": "asrt_1a9596d551d7",
   "kind": "RULE",
   "condition": {
-    "text": "买入Put并卖出更高行权价Put"
+    "text": "买入Put"
   },
   "action": {
     "verb": "identify",
-    "text": "牛市看跌价差"
+    "text": "识别净收权利金策略"
   },
   "target": {
     "scope": "any",
```

## 04 `asrt_281277238074`
- issue：`not_supported`
- verify_fix：考虑减仓
```diff
--- asrt_281277238074.before.json
+++ asrt_281277238074.after.json
@@ -13,10 +13,9 @@
     "text": "当前走势"
   },
   "invalidator": null,
-  "rationale": "放量破位说明主力出货",
+  "rationale": "股价创出新高但MACD红柱面积比之前小，是顶部的重要信号",
   "signals": [
-    "MACD红柱面积",
-    "成交量"
+    "MACD红柱面积"
   ],
   "confidence": 0.6,
   "expire_at": null,
```

## 05 `asrt_cf4f096d74ec`
- issue：`vacuous`
- verify_fix：卖点应基于上涨中的背驰而非恐慌下跌
```diff
--- asrt_cf4f096d74ec.before.json
+++ asrt_cf4f096d74ec.after.json
@@ -5,15 +5,15 @@
     "text": "卖点是在涨的时候出现的，不是沙爹割出来的"
   },
   "action": {
-    "verb": "prohibit",
-    "text": "卖晚"
+    "verb": "sell",
+    "text": "基于上涨中的背驰卖出，而非恐慌下跌时卖出"
   },
   "target": {
     "scope": "any",
     "text": "卖晚"
   },
   "invalidator": null,
-  "rationale": "卖点是在涨的时候出现的，不是沙爹割出来的",
+  "rationale": "卖点应基于上涨中的背驰而非恐慌下跌",
   "signals": [],
   "confidence": 0.6,
   "expire_at": null,
```

## 06 `asrt_69c6179e25e7`
- issue：`vacuous`
- verify_fix：把动作改为'把每一笔交易当作独立事件，并记住错误、改正并避免再犯'
```diff
--- asrt_69c6179e25e7.before.json
+++ asrt_69c6179e25e7.after.json
@@ -5,8 +5,8 @@
     "text": "亏损后没有忘记亏损金额"
   },
   "action": {
-    "verb": "avoid",
-    "text": "背着沉重包袱"
+    "verb": "other",
+    "text": "把每一笔交易当作独立事件，并记住错误、改正并避免再犯"
   },
   "target": {
     "scope": "any",
```

## 07 `asrt_bee96217775f`
- issue：`vacuous`
- verify_fix：观察反弹能否突破3953点及后续是否再次向下
```diff
--- asrt_bee96217775f.before.json
+++ asrt_bee96217775f.after.json
@@ -5,8 +5,8 @@
     "text": "反弹不过3953点"
   },
   "action": {
-    "verb": "identify",
-    "text": "识别新的五分钟下跌中枢"
+    "verb": "wait",
+    "text": "观察反弹能否突破3953点及后续是否再次向下"
   },
   "target": {
     "scope": "any",
```

## 定稿说明
- **01**：条件去掉未支持的“且放量”，动作改为“关注CD的1.38和1.68”。
- **02**：当前 `invalidator`/`rationale` 已与 verify_fix 一致，不改。
- **03**：condition 收窄为“买入Put”；动作改为“识别净收权利金策略”。
- **04**：按你的要求清掉 `signals` 与 `rationale` 中的“成交量/放量”表述，rationale 改为原文可支撑的“股价创新高但MACD红柱面积缩小是顶部重要信号”。
- **05**：动作 verb 改 `sell`，text 改为“基于上涨中的背驰卖出，而非恐慌下跌时卖出”；rationale 同步。target 仍是“卖晚”，未擅自改。
- **06**：verb 改 `other`（正向行为准则，`avoid` 不贴），text 按 verify_fix。
- **07**：verb 改 `wait`（等待/观察后续确认），text 按 verify_fix。
- 以上均**未写 graph_cache.db**；K6 未并入；R7 已标记 applied（candidate 层，production=false）。
