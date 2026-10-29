# 曹操军团实战就绪审计

- as-of：2026-10-29T08:45:01｜席位 11｜在制 dev 11｜新单真席位执行 0/7

## 一、角色到位

- 后端：3 席 ✅
- 前端/UI：2 席 ✅
- AI/模型：1 席 ✅
- 量化：2 席 ✅
- 运维/SRE：1 席 ✅
- 文档：1 席 ✅
- 架构：1 席 ✅

## 二、汇报链/分组

- G1 · 组长 DEV1 · 成员 DEV6、DEV7、DEV2、OPS1
- G2 · 组长 DEV5 · 成员 DEV8、DEV3、DEV4、DOC1

## 三、能力卡

| 席位 | 角色 | 域 | 职级 | 组 | 专长 | 工具写域 | 学习 |
|---|---|---|---|---|---|---|---|
| DEV1  | 后端工程师 · G1 组长 | backend | E3 | G1 | 4 | 4 | 未建学习计划 |
| DEV6  | 后端工程师 | backend | E2 | G1 | 3 | 4 | 未建学习计划 |
| DEV7  | 后端工程师（标准化交付） | backend | E1 | G1 | 3 | 4 | learning:[{'id': 'L1', 'title': '岗位与契约（角色域/边界/验收）', 'source': 'agent_platform/prompts/code_engineer.md', 'status': 'learning', 'evidence': ''}, {'id': 'L2', 'title': '代码定位与影响面（repo map/search/refs）', 'source': 'tools/codebase_intel.py', 'status': 'learning', 'evidence': ''}, {'id': 'L3', 'title': '测试与回归（分片/失败定位/证据）', 'source': 'agent_platform/tests', 'status': 'planned', 'evidence': ''}, {'id': 'L4', 'title': '质量门与路由规则（谁能派/怎么验收）', 'source': 'docs/plans/2026-10-27_研发单路由规则_ARCH1优先_v1.md', 'status': 'planned', 'evidence': ''}] |
| DEV2  | AI/模型工程师 | ai | E2 | G1 | 4 | 4 | 未建学习计划 |
| DEV3  | 量化策略工程师 | quant | E2 | G2 | 4 | 4 | 未建学习计划 |
| DEV4  | 回测/风控工程师 | quant | E2 | G2 | 4 | 4 | 未建学习计划 |
| DEV5  | 前端工程师 · G2 组长 | frontend | E3 | G2 | 4 | 4 | 未建学习计划 |
| DEV8  | 前端工程师 | frontend | E2 | G2 | 3 | 4 | 未建学习计划 |
| ARCH1 曹操 | 研发主官 · 架构师 | architecture | E3 |  | 4 | 2 | 未建学习计划 |
| OPS1  | 运维/SRE 工程师 | devops | E3 | G1 | 4 | 3 | 未建学习计划 |
| DOC1  | 技术文档工程师 | docs | E2 | G2 | 4 | 2 | 未建学习计划 |

## 四、任务分派机制

- tools/devtask.py：✅
- tools/tianfeng_dispatch.py：✅
- tools/dev_orchestrator.py：✅

## 五、判定

- role_ok：✅
- chain_ok：✅
- cards_ok：✅
- mechanism_ok：✅
- true_exec_ok：⚠️
- ready_for_combat：✅

> 能力卡可生成 ≠ 已认证；学习计划仅 DEV7 起步。真席位执行率按 block 后新单 owner 统计。
