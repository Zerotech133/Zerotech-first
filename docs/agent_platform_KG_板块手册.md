# agent_platform + zerobound-kg 板块研究手册（2026-11-10）

> 从全仓审计抽出 agent_platform + zerobound-kg 单板块，格式与 `docs/JEV_研究手册.md` 一致。

## 一、定位（先钉死）
- `agent_platform`：Agent 平台（含 OSLINK 网关 8795、服务 8796）。
- `zerobound-kg`：知识图谱（KG API + 日级管线）。
- **不是**：对外产品入口；它是能力层。

## 二、本轮审计结论（2026-11-10）
**P0：无。** 主要债：

| 严重度 | 问题 | 位置 | 状态 |
|---|---|---|---|
| P1 | **OSLINK 8795 CH2 契约未闭环**：缺 `answered_by/answer_ref` 致 422，HTTP 通道不可用 | `agent_platform/gateway/osleg_http_api.py:44` | 未修，需契约 spec 变更 |
| P1 | `KG_CLOSEOUT.md` 标"零挂起/终态"但实际仍在迭代 | `zerobound-kg/KG_CLOSEOUT.md:5` | **已重标**"建设期完成，常态运维+迭代" |
| P1 | KG API **13+ 处裸 except 吞错**，静默返回空/200 | `kg_graph_portal.py` | 未修 |
| P2 | gateway docstring 端口 8795/8796 串错 | gateway 文档 | 未修 |
| P2 | trigger 路径硬编码 `C:\aios` | trigger 相关 | 未修（迁移即崩） |
| P2 | KG 日级管线硬编码运行日 | 管线脚本 | 未修 |

## 三、待办
- [ ] OSLINK CH2 契约闭环：接线单补 `answered_by/answer_ref` 或归口方给证据（阶段 A2）
- [ ] KG API 吞错 → 至少 `logging.exception` + 返回 5xx、收窄异常类型（阶段 A4）
- [ ] 路径硬编码统一走 `AIOS_ROOT`
- [ ] v2-K 挂起项进 `zerobound-kg/INTEL_RD_PLAN` 跟踪

## 四、一句话给研究者
这个板块的病是**"声明与现实的落差"**：收口文档说终态实际在迭代、API 说 200 实际吞了异常、契约说闭环实际 422。
研究它先对台账：**凡"已收口/已闭环"的字样，都要拿运行证据反验一遍。**
