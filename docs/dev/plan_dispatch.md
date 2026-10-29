# 研发计划拆解与分派工具（plan_dispatch）

- **状态**：v1（2026-10-29，`WO-PLAN-DISPATCH-20261029`）
- **用途**：把"研发计划→WBS"变成可校验的 JSON，然后**半自动**生成/执行 `devtask new` 与 `tianfeng_dispatch`，补齐"曹操拿计划→拆解→分到每个人"的工具缺口。
- **不替代**：ARCH1/组长的拆解决策；工具只做**校验与下发**，不自动编 WBS。

## 一、计划格式（schema=plan_dispatch/v1）
```json
{
 "schema": "plan_dispatch/v1",
 "project": "war-screen-v1.1-m1",
 "elements": [
  {"id":"E1","title":"...","owner":"DEV3","discipline":"quant",
   "acceptance":"pytest -q","effort":"M","deps":[]}
 ]
}
```
字段：`id` 唯一；`owner` 必须是 dev 席位；`discipline` 与 owner 工程域一致；`acceptance` 必填（验收命令）；`deps` 不得成环；`effort`=S/M/L。

## 二、校验
```powershell
python -X utf8 tools/plan_dispatch.py --plan docs/dev/plan_dispatch_sample.json --dry-run
```
- 错误：缺 project/id/owner/acceptance、owner 非法、域不匹配、依赖缺失/成环；
- 告警：在制 + 新增 > 12（容量）；
- 输出：每个要素的 `devtask new` 命令 + `tianfeng_dispatch` 命令。

## 三、执行
```powershell
python -X utf8 tools/plan_dispatch.py --plan plan.json --apply
```
- 逐单 `devtask new ... --approver ARCH1 --dept dev`，再逐单派工；
- 任一建单失败即停止（避免半派）；
- 审批门仍生效：缺 approver/验收会被拒。

## 四、边界
- 默认 `--dry-run`；`--apply` 需人工确认计划已批；
- 不自动改 owner/权限/org；不绕过质量闸；
- 拆解质量仍由 ARCH1/组长负责；工具只保证"要素合法、能落单、能派到人"。

## 五、样例
`docs/dev/plan_dispatch_sample.json`（v1.1 M1 两要素：DEV3/DEV4）。
