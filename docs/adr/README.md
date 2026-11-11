# ADR 索引

本目录 ADR 清单（按编号排序）。状态取自各文件「状态」行；链接为相对本目录的文件链接。
新增 ADR 后请同步本索引（`docs/adr/README.md`）。

| 编号 | 标题 | 状态 | 文件 |
|---|---|---|---|
| ADR-001 | ADR-001 · 仓库地图自研(Aider RepoMap范式吸收) | — | [ADR-001-repomap-selfbuilt.md](./ADR-001-repomap-selfbuilt.md) |
| ADR-002 | ADR-002 · 27岗子代理导出以bundle为单一事实源 | — | [ADR-002-agents-export-single-source.md](./ADR-002-agents-export-single-source.md) |
| ADR-003 | ADR-003 · 研发单路由与 ARCH1 工具矩阵授权（A0 L1 批） | — | [ADR-003-dev-routing-tool-matrix.md](./ADR-003-dev-routing-tool-matrix.md) |
| ADR-004 | ADR-004：文档体系采用 Diátaxis 分类 + ADR 记录架构决策 | — | [ADR-004-docs-taxonomy-diataxis.md](./ADR-004-docs-taxonomy-diataxis.md) |
| ADR-005 | ADR-005：Agent 可观测采用 OpenTelemetry GenAI 语义约定（分阶段） | — | [ADR-005-agent-observability-otel-genai.md](./ADR-005-agent-observability-otel-genai.md) |
| ADR-006 | ADR-006：一单一事 + git worktree 隔离 | — | [ADR-006-worktree-devtask-isolation.md](./ADR-006-worktree-devtask-isolation.md) |
| ADR-007 | ADR-007：工具矩阵（tool_matrix）作为工具授权单一源 | — | [ADR-007-tool-matrix-single-source.md](./ADR-007-tool-matrix-single-source.md) |
| ADR-008 | ADR-008：席位契约（SOUL / SEAT_CARD / CAPABILITIES / 记忆） | — | [ADR-008-seat-contract.md](./ADR-008-seat-contract.md) |
| ADR-009 | ADR-009：端口注册表为唯一真源，一切绑 127.0.0.1 | — | [ADR-009-port-registry-entry.md](./ADR-009-port-registry-entry.md) |
| ADR-010 | ADR-010：凭据集中到 `_vault`，密文副本 + token 仅环境注入 | — | [ADR-010-credentials-vault.md](./ADR-010-credentials-vault.md) |
| ADR-011 | ADR-011：唯一数据根 + 运行态根分离，`work` 根退役 | — | [ADR-011-data-roots.md](./ADR-011-data-roots.md) |
| ADR-012 | ADR-012：L2 动作五要素审批，沉默≠同意 | — | [ADR-012-l2-five-elements.md](./ADR-012-l2-five-elements.md) |
| ADR-013 | ADR-013：单机个人 OS 的范围边界与文档纪律 | — | [ADR-013-scope-and-docs-discipline.md](./ADR-013-scope-and-docs-discipline.md) |
| ADR-014 | ADR-014：Agent 运行契约 v1（run / event / conversation） | — | [ADR-014-agent-run-contract.md](./ADR-014-agent-run-contract.md) |
| ADR-015 | ADR-015：平台内核契约与组合模型 v1 | — | [ADR-015-platform-kernel-composition-model.md](./ADR-015-platform-kernel-composition-model.md) |
| ADR-016 | ADR-016：唯一宿主整合（零界承接零界 + 智策） | — | [ADR-016-single-host-integration.md](./ADR-016-single-host-integration.md) |
| ADR-017 | ADR-017：唯一开发框架与黄金路径（ZBF v1） | — | [ADR-017-framework-golden-path.md](./ADR-017-framework-golden-path.md) |
| ADR-020 | ADR-020 研发军团作战屏 v1 数据契约（只读） | — | [ADR-020-dev-legion-screen-contract.md](./ADR-020-dev-legion-screen-contract.md) |
| ADR-021 | ADR-021：多 AI 军团控制面选型——原方案为主，Paperclip 仅作参考/隔离 PoC | **Accepted** | [ADR-021-control-plane-selection.md](./ADR-021-control-plane-selection.md) |
| ADR-025 | ADR-025 · 治理嵌入开发流（影子→告警→强制，不停工） | **Accepted | [ADR-025-governance-embedding-shadow-to-enforce_v1.md](./ADR-025-governance-embedding-shadow-to-enforce_v1.md) |
| ADR-026 | ADR-026 · PII W2/W3 窗口外紧急合并（质量闸绕过 + merge train force） | **Accepted | [ADR-026-emergency-off-window-merge-pii.md](./ADR-026-emergency-off-window-merge-pii.md) |
| ADR-027 | ADR-027 · GATEHEALTH 分支结案归档（不申请 H5 钉扎例外） | **Accepted | [ADR-027-gatehealth-branch-closure_v1.md](./ADR-027-gatehealth-branch-closure_v1.md) |
| ADR-20261103 | ADR-20261103 · 分钟K线数据核查与缠论战法可实现性修正 | 已接受 | [ADR-20261103-分钟K线数据核查与缠论可实现性_v1.md](./ADR-20261103-分钟K线数据核查与缠论可实现性_v1.md) |
