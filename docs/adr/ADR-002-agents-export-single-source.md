# ADR-002 · 27岗子代理导出以bundle为单一事实源

- **日期**：2026-10-13
- **登记岗**：ARCH1
- **关联工单**：K-ORG-036f
- **状态**：accepted（复核期 2026-10-20 到期回访，QA1 按此复核）

## 背景
Claude Code --agents需四字段JSON,27岗人设双维护会漂移

## 决策
export_claude_agents.py从portal/agent_ide_bundle.json推导,禁止手改产物claude_agents.json

## 代价（明确接受的坏处/限制）
bundle结构变更须重跑导出,模型分档映射表需随岗级调整同步

## 复核期
2026-10-20——到期由 QA1 按本 ADR 回访；若推翻，新 ADR 引用本条编号并置本条 status=superseded。
