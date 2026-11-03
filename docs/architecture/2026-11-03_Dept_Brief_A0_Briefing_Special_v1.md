# 部门汇报 → 田丰简报 → 人专项 v1

- 日期：2026-11-03｜触发：A0「部门主管汇总工作汇报田丰，田丰形成简报；感兴趣的研究点击进人专项」
- 入口：`/briefing`（简报）→ 点击研究题 → `/special`（人专项工作台）

## 一、流程

```
07:50 前 13 位部门负责人提交早报
   ↓ tools/hq_dept_brief.py --collect ... --briefing
田丰（A0）生成早间简报（部门状态/工作要点/阻塞/研究题/决策请求）
   ↓ /briefing
老田浏览简报，点击感兴趣的研究题
   ↓ /special?topic=...&title=...
人专项工作台：定义为什么关心/期望产出 → 生成专项卡
   ↓
交给田丰派单/立项（不自动执行、不代批 L2）
```

## 二、命令

```powershell
# 1) 生成 13 位负责人早报模板
python -X utf8 tools/hq_dept_brief.py --template --out deliverables/daily_briefs/2026-11-04/template.json

# 2) 各部门填好各自 brief 后，田丰汇总
python -X utf8 tools/hq_dept_brief.py --collect deliverables/daily_briefs/2026-11-04 --briefing --out deliverables/daily_briefs/2026-11-04/a0_briefing.json

# 3) （可选）完整 rollup 供页面消费
python -X utf8 tools/hq_dept_brief.py --collect deliverables/daily_briefs/2026-11-04 --out deliverables/daily_briefs/2026-11-04/rollup.json
```

- `hq_snapshot.py` 会自动读取最新 `daily_briefs/*/rollup.json` 的 `briefing` 字段；
- `briefing` 页面优先显示部门汇报；没有汇总时，用当前未派单 Directive 生成研究题，页面不空。

## 三、页面

- 简报：http://127.0.0.1:8734/briefing（需登录）
- 简报页提供「立即总结」「汇报工作」按钮和部门筛选；人类汇报保存在本页（localStorage），可复制/下载给田丰
- 人专项：点击研究题进入 `/special`，可填“为什么关心/期望产出”，生成/复制/下载专项卡
- 入口：`/hq` 页头「早间简报 →」

## 四、边界

- 专项卡是**本地草稿**，不自动建任务、不自动派单、不代签；
- 研究题来自真实早报/未派单 Directive，不编造；
- 部门早报未提交时，简报会明确“尚未汇总”，不假装有数据；
- 页面/工具变更需 8091 重启后才把最新 `briefing` 字段带进 snapshot；页面已有 Directive 兜底。

## 五、验证

- `hq_dept_brief`：模板/采集/研究题/简报测试；
- `portal/test_briefing_special.py`：路由鉴权、链接、专项卡；
- 登录后 `/briefing`、`/special` 200。
