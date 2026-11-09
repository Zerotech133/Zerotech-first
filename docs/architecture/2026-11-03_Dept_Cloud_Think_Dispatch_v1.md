# 部门主管调度云端模型思考 v1

- 日期：2026-11-03｜触发：A0「部门主管可以调度云端模型思考问题」
- 能力：13 位部门负责人按各自职能，把问题调度给**云端模型**思考，返回结构化判断并留证。
- 入口：`tools/hq_cloud_think.py`（CLI，不新增页面/不新增端口）

## 一、谁能调度

D1 鲁肃、ARCH1 曹操、DATA1 张松、PM1 吕蒙、QA1 于禁、DEV3 蒋济、MKT1 简雍、LEG1 诸葛瑾、A0A 董允、ACAD1 司马徽、INTEL0 马超、LUSU1 阚泽、R3 陆逊。

- 主绑：`glm/glm-5.3` 云端；
- 降级：`deepseek/deepseek-flash` → `ollama/qwen3.5:9b`；
- 非上述 13 席（含 A0/指挥席）不允许用本入口，避免越权替指挥席思考。

## 二、调用

```powershell
python -X utf8 tools/hq_cloud_think.py `
  --lead DATA1 `
  --question "数据仓血缘缺口如何在一个迭代内补齐？" `
  --context "已知 data-warehouse 100% 完成；314 数据集/256 表" `
  --out deliverables/think_logs/2026-11-03_DATA1.json
```

输出：

```json
{
  "schema": "hq/cloud_think/v1",
  "lead": "DATA1", "name": "张松", "dept": "数据资源主官",
  "text": "{\"conclusion\":\"...\",\"evidence\":[...],\"risks\":[...],\"recommendation\":\"...\",\"questions\":[...]}",
  "binding": "glm/glm-5.3", "degraded": false, "latency_ms": 0
}
```

## 三、思考契约

- 只从部门职能面回答；
- 区分事实/推断/需查证，不给具体假数字；
- 不代替田丰派单、不代替老田拍板；
- 云端失败时 fail-visible（rc=2），不伪装成功；降级时 `degraded=true` 且 `binding` 是实际绑定。

## 四、边界

- 只读思考，不自动执行、不写任务/审批/生产数据；
- 仍受预算/调用闸约束；
- 思考结果不等于决策，进决策需 A0/老田确认；
- 不保存密钥；`--out` 只写指定文件。
