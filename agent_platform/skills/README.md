# Agent Skills · 席位技能包（零界）

> 2026-10-27 关羽线引入。依据：Anthropic **Agent Skills** 标准已捐给 vendor-neutral 的 Agentic AI Foundation，Claude Code / Codex / Cursor / Kimi Code / OpenCode 等 harness 均按同一格式加载技能。
> 目标：把 61 个席位的"能力"从散落的 prompt/config/tools 变成**可移植、可评测、可版本化**的技能包。

## 目录约定

```
agent_platform/skills/
  README.md              ← 本文件（规范）
  _template/SKILL.md     ← 新建席位技能包时复制
  <seat>/SKILL.md        ← 席位技能（seats 名用 org.json 的 agent_id，小写目录）
  <seat>/references/     ← 可选：该技能引用的知识文件/清单
```

与 `agent_platform/registry/org.json` 的关系：
- **org.json 是权威注册表**（席位档级/model/tools/memory grants）；
- `SKILL.md` 是**能力说明书**（何时用、怎么干、什么不能干、输出契约），不得与 org.json 的授权矛盾；
- 冲突时以 org.json + AGENTS.md 为准。

## SKILL.md 格式

```markdown
---
name: <seat-lower-kebab>            # 技能名（可被 harness 调用）
description: <一句话：能力+触发场景>
seat: <AGENT_ID>                    # 对应 org.json agent_id
model: <provider/model>
allowed-tools: [<tool>, ...]        # 必须 ⊆ org.json tools + tools_allowed
danger: deny|ask|allow              # 必须与 org.json 一致（关羽=deny）
version: <x.y.z>
---

## 何时使用（触发条件）
## 输入（需要什么）
## 工作流（步骤化，可审计）
## 硬约束（红线/授权边界）
## 输出契约（格式固定，机读）
## 自检清单（完成前逐项）
## 升级/人工确认路径（L2 等）
## 评测挂钩（promptfoo 用例）
```

## 纪律

1. **不新增授权**：`allowed-tools` 只能收窄、不能扩大 org.json 的 grants；
2. **危险动作**：`danger: deny` 的席位不得写需要 L2 确认的动作（宿主机重启/防火墙/计划任务/生产服务器变更），只能出"待批工单"；
3. **能力变更**走工单：改 SKILL.md 需评审门 + 事件层留痕，和改代码同等级；
4. **评测**：每个 SKILL.md 必须在 `agent_platform/evals/` 有对应用例（至少覆盖：正常路径、越权拒绝、输出契约）；
5. **跨 harness**：技能包不得依赖某个 IDE 私有 API；脚本一律用仓库内相对路径。
