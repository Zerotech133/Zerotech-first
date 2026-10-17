# 工单治理交付模板

> 状态：[CURRENT] + [TEMPLATE]
> 适用：所有新增或修改代码、配置、页面、数据、计划任务和服务的工单。

## A. 工单元数据

- ticket：
- title：
- owner / agent_id：
- domain：
- risk_level：`low` / `medium` / `high` / `critical`
- rollout_mode：`read-only` / `experimental` / `shadow` / `canary` / `production`
- parent_decision / ADR：

## B. 边界

- objective：
- allowed_paths：
- forbidden_paths：
- touched_services / ports：
- touched_routes / APIs：
- touched_tables / data roots：
- shared_resources：
- explicit_non_goals：

## C. 设计与风险

- input_source / fact_source：
- contract/schema/version：
- permission / approval rule：
- idempotency key：
- audit / provenance fields：
- concurrency / restart behavior：
- threat and failure modes：

## D. 实施证据

- changed_files：
- commit：
- test_commands：
- test_result：
- runtime_smoke：
- evidence_path：
- logs / artifact paths：
- docs_updated：

## E. 发布与回滚

- pre_snapshot / backup：
- rollout steps：
- observation window：
- rollback trigger：
- rollback command/files：
- rollback_tested：
- operator approval：

## F. 结论

- status：`done` / `blocked` / `rejected` / `aborted`
- residual_risks：
- follow_up_ticket：
- reviewer：
- review_date：

## 关闭标准

工单只有同时满足以下条件才可标记 done：

1. 代码/配置已明确列出；
2. 测试命令和结果可复现；
3. 运行证据或明确说明无需运行态变更；
4. 文档已更新或记录 docs_updated=no 及理由；
5. 回滚方式可执行；
6. 无越界文件和未登记共享资源写入；
7. 高风险工单有人工作批准。
