# 2026-11-07 R7 `verify_fix` 改写 diff（候选，未写生产图）
> 数据源：`_merged_all_20261106_v12_draft.jsonl`（2642 条）。  
> 候选：`C:\项目\知识库\95_KG\_assertions\_merged_all_20261106_v12_r7_draft.jsonl`（2642 条，7 条中 5 条有字段变化，2 条已满足；sha256 `aa8d4eff...a4c75`）。  
> 只改 assertion 内容字段；未改生产图、未改 `verify.verdict`、未将 R 标记 applied。
| # | assertion_id | issue | verify_fix | 字段变化 |
|---|---|---|---|---:|
| 01 | `asrt_00599b9018b4` | not_supported | 条件=向下跌破CD / 动作=关注CD的1.38和1.68 | 2 |
| 02 | `asrt_1261f7186e0f` | not_supported | 理由='日线不在背驰段时看三十分钟买点足够' | 0 |
| 03 | `asrt_1a9596d551d7` | not_supported | 删除动作，改为'识别净收权利金策略' | 1 |
| 04 | `asrt_281277238074` | not_supported | 考虑减仓 | 0 |
| 05 | `asrt_cf4f096d74ec` | vacuous | 卖点应基于上涨中的背驰而非恐慌下跌 | 2 |
| 06 | `asrt_69c6179e25e7` | vacuous | 把动作改为'把每一笔交易当作独立事件，并记住错误、改正并避免再犯' | 1 |
| 07 | `asrt_bee96217775f` | vacuous | 观察反弹能否突破3953点及后续是否再次向下 | 1 |

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
- **无字段变化**：当前 assertion 已满足该 verify_fix；不需要改。
- verify_fix 已满足，无字段变化

## 03 `asrt_1a9596d551d7`
- issue：`not_supported`
- verify_fix：删除动作，改为'识别净收权利金策略'
```diff
--- asrt_1a9596d551d7.before.json
+++ asrt_1a9596d551d7.after.json
@@ -6,7 +6,7 @@
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
- **无字段变化**：当前 assertion 已满足该 verify_fix；不需要改。
- verify_fix 已满足，无字段变化

## 05 `asrt_cf4f096d74ec`
- issue：`vacuous`
- verify_fix：卖点应基于上涨中的背驰而非恐慌下跌
```diff
--- asrt_cf4f096d74ec.before.json
+++ asrt_cf4f096d74ec.after.json
@@ -6,14 +6,14 @@
   },
   "action": {
     "verb": "prohibit",
-    "text": "卖晚"
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
@@ -6,7 +6,7 @@
   },
   "action": {
     "verb": "avoid",
-    "text": "背着沉重包袱"
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
@@ -6,7 +6,7 @@
   },
   "action": {
     "verb": "identify",
-    "text": "识别新的五分钟下跌中枢"
+    "text": "观察反弹能否突破3953点及后续是否再次向下"
   },
   "target": {
     "scope": "any",
```

## 待你确认的语义问题（未擅自改）
- **08 `asrt_1261f7186e0f`**：`invalidator=日线在背驰段`、`rationale=日线不在背驰段时看三十分钟买点足够`，与 verify_fix 完全一致；当前无需改。
- **17 `asrt_281277238074`**：`action.text=考虑减仓` 已满足 verify_fix；但 `rationale=放量破位说明主力出货` 仍是原文未提的额外因果，`signals` 还带 `成交量`（原文未提成交量）。要不要我顺手把 rationale 改成“股价创新高但MACD红柱面积缩小，形成量价背离，是顶部信号”、并去掉 `成交量`？
- **11 `asrt_1a9596d551d7`**：verify_fix 只说改动作；`condition=买入Put并卖出更高行权价Put` 仍含原文未说的“卖出更高行权价Put”。要不要把 condition 收窄为 `买入Put`？
- **22 `asrt_cf4f096d74ec`**：action 已按语义改成“基于上涨中的背驰卖出，而非恐慌下跌时卖出”，但 verb 仍是 `prohibit`、target 仍是 `卖晚`；严格 verb 语义要不要改成 `sell` / `卖点`？
- **29 `asrt_69c6179e25e7`**：action.text 已换，verb 仍是 `avoid`；该动作是正向行为准则，`avoid` 语义不贴，建议改 `other` 或 `hold`，你定。
- **37 `asrt_bee96217775f`**：action.text 改成“观察…”，verb 仍是 `identify`；若严格按动作白名单，`观察` 没有对应 verb，建议 `other`/`wait`，你定。
- 以上均**未写 graph_cache.db**；确认后再生成最终候选/重建。
