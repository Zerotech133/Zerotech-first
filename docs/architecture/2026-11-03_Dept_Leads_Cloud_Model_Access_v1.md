# 部门负责人云端模型访问 v1

- 日期：2026-11-03｜触发：A0「所有部门负责人可以调取云端模型」
- 真源：`agent_platform/registry/org.json` 的 `llm_binding`（8796 `/api/chat` 按此解析，页面文案不生效）

## 一、策略

| 项 | 设定 |
|---|---|
| 范围 | 13 位部门负责人（不含指挥席 A0/AI1/ZHAOYUN/GUANYU/HUANGZHONG） |
| 主绑 | **`glm/glm-5.3`（云端）** |
| 降级 | `deepseek/deepseek-flash` → `ollama/qwen3.5:9b`（云端失败按席位链；不伪造云端成功） |
| 会议 | `/api/chat?purpose=meeting` 同样走云端主绑 |
| 密钥 | 不落仓；由模型网关/环境提供 |
| 成本 | 仍受调用闸/预算门约束；不因“可调云端”放开无限并发 |

## 二、13 位负责人

D1 鲁肃（量化）、ARCH1 曹操（研发）、DATA1 张松（数据资源）、PM1 吕蒙（产品）、QA1 于禁（质量）、DEV3 蒋济（回测）、MKT1 简雍（市场）、LEG1 诸葛瑾（合规）、A0A 董允（运行体）、ACAD1 司马徽（研究）、INTEL0 马超（情报）、LUSU1 阚泽（客户）、R3 陆逊（风控）。

## 三、验证

```powershell
python -X utf8 -c "import sys; sys.path.insert(0,'agent_platform'); from legion_command import serve; print(serve.resolve_binding('PM1'))"
# 期望 glm/glm-5.3
```

- 会议页五席/负责人池发言同一解析器；
- 云端不可用时返回可诊断错误/降级标记，不伪装为云端回执。

## 四、边界

- 只给“部门负责人会议/工作使用”，不等于授予生产写权限、审批权、L2；
- 不改变 ZHAOYUN 会议本地覆盖（治理席专项规则另议）；
- 预算/配额仍由既有预算门约束。
