# A0 每日跑板 v1（07:50 自动）

- 日期：2026-11-03｜触发：A0「未来汇报的 A0 应该每天把整个板块的数据真的跑一遍」
- 结论：**已从“按钮/提醒”升级为真实每日跑板**：13 位负责人云端汇报 → 田丰简报 → 08:00 早会消费。

## 一、每日节奏

| 时间 | 动作 | 产物 |
|---|---|---|
| **07:50** | Windows 计划任务 `AI-Workstation-A0-Daily-Board` 自动执行 | `deliverables/daily_briefs/<date>/rollup.json` + `a0_briefing.md` |
| 08:00 | A0 早会读取简报；13 部门已提交昨天完成/正在做/当前卡点 | `/briefing` 页面 |
| 08:20 | 老田决策清单 | 会议纪要/派单 |

## 二、实现

- 工具：`tools/hq_daily_run.py`
  - 读取 `org.json` 13 位部门负责人（不含 A0）；
  - 3 并发调用 `/api/chat`（云端 `glm/glm-5.3`，失败按绑定链降级并标记）；
  - 严格 JSON 解析；坏输出不伪造，记 amber/无内容；
  - 13/13 成功 rc=0；部分失败 rc=2；真源错误 rc=1；
  - 原子写 rollup / Markdown。
- 包装：`ops/run_hq_daily.ps1`，日志 `C:\Temp\hq_daily_run.log`。
- 任务：`AI-Workstation-A0-Daily-Board`，每天 07:50，Interactive（登录状态有效）。
- 已注册并查询：Next Run 2026-11-04 07:50，Status Ready。

## 三、命令

```powershell
# 手动跑一次（会真实调用 13 部门云端模型）
python -X utf8 tools/hq_daily_run.py

# 查看任务
schtasks /Query /TN "AI-Workstation-A0-Daily-Board" /FO LIST

# 立即测试任务（注意会真实跑 13 部门）
schtasks /Run /TN "AI-Workstation-A0-Daily-Board"

# 临时停用/启用
schtasks /Change /TN "AI-Workstation-A0-Daily-Board" /DISABLE
schtasks /Change /TN "AI-Workstation-A0-Daily-Board" /ENABLE
```

## 四、失败语义

- 某部门模型超时/失败：`failed[]` 记录，其他部门照常完成；不会用模板补造；
- 13/13 才 rc=0；部分成功 rc=2；
- 页面「立即总结（13部门运行）」仍保留为手动即时跑；
- 每日任务只写日报文件，不自动派单/不代批 L2。

## 五、验证

- `tools/tests/test_hq_daily_run.py`：全部成功、部分失败、no-write、坏输出不伪造；
- 页面/对话证据：真实 `binding`/延迟显示；
- 次日 08:00 检查 `rollup.json` 与 `/briefing` 是否更新。
