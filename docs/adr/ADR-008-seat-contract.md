# ADR-008：席位契约（SOUL / SEAT_CARD / CAPABILITIES / 记忆）

- **状态**：Accepted（历史回填，2026-10-27；2026-10 席位真成员化改造）
- **关联**：P4/P8；`tools/seat_system_audit.py`；`prompts/`、`agent_platform/registry/org.json`

## 背景

"席位"若只是 prompt 名字，会出现能力声明与实际工具/路径不符、审计无法判真。

## 决定

1. 每个席位必须有：SOUL（使命/边界）、SEAT_CARD（对外名片/底座口径）、CAPABILITIES（能力→方法路径）、记忆（经验库/工作记忆）；
2. `seat_system_audit --strict` 校验文件齐全与路径存在（9/0/0 PASS 是当前基线）；
3. 底座/模型口径必须真实（禁止写"未注入的 key 可用"）；
4. 席位变更走工单，重生成 SEAT_CARD 并留回执。

## 后果

- 正面：能力声明可验证；对外协作口径一致；审计有据。
- 代价：每席位多套文档，新席位入编有固定工序。
- 不做：不把席位做成独立微服务（单进程/本机调度足够）。
