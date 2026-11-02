# 文档中心（docs/README.md）

> 零界 · 老田的AI助手：文档是工程的一部分，不是附属品。
> 本页是**文档导航与写作约定**；实施依据见 [ADR-004](adr/ADR-004-docs-taxonomy-diataxis.md)。
> 文档标准参考 [Diátaxis](https://diataxis.fr/)（Tutorials / How-to / Reference / Explanation）。

## 一、四类文档（Diátaxis 映射）

| 类型 | 读者要什么 | 本仓库位置 |
|---|---|---|
| **Tutorials** | 第一次上手，跟着做就会 | `docs/tutorials/`（待建；当前用 `README.md` 快速开始） |
| **How-to** | 解决具体任务/故障 | `docs/ops/`（操作单）、`docs/runbooks/`（排障）、`docs/migration/` |
| **Reference** | 查准确事实（端口/契约/标准/决策） | `docs/specs/`、`docs/standards/`、`docs/adr/`、`docs/architecture/principles.md`、`ARCHITECTURE.md`、`AGENTS.md` |
| **Explanation** | 理解为什么这样设计 | `docs/design/`、`docs/plans/`、`docs/audit/`、`docs/governance/`、`docs/legion/` |

## 二、目录职责与更新规则

| 目录 | 放什么 | 更新时机 |
|---|---|---|
| `docs/plans/` | 拍板与设计（唯一战略源：`2026-10-26_PRODUCT_ROADMAP_v3.md`） | 决策当天；状态列随进展更新 |
| `docs/audit/` | 审计与判例（历史判例可查） | 每次审计/复盘后 |
| `docs/ops/` | 可执行运维操作单（含 L2 五要素） | 执行前写单、执行后补回执 |
| `docs/runbooks/` | 故障处置步骤 | 每次事故后补一条 |
| `docs/specs/` | 接口/数据契约 | 契约变更与代码同一批提交 |
| `docs/standards/` | 组织/工程标准 | 立宪或修订时 |
| `docs/adr/` | 架构决策记录（ADR） | 任一架构级决策；一决策一文件 |
| `docs/changes/` | 变更记录（按日期） | 每次较大改动 |
| `docs/design/` | 设计与原型 | 方案定稿时 |
| `docs/dev/` | 开发看板/周报等生成物 | 由脚本生成，不手改 |
| `docs/ci/` | CI 草案与本地门禁说明 | 门禁调整时 |

## 三、索引与命名

- 全量索引：`C:\项目\DOCS_INDEX.md`（由 `tools/gen_docs_index.py` 生成，`--check` 必须 rc=0）。
- 命名：`YYYY-MM-DD_主题_vN.md`；历史文档不改名，新增 `superseded-by` 头注。
- 决策类文档：一律进 `docs/adr/`，编号连续（当前 ADR-001 ~ ADR-020（ADR-018/019 于 2026-11-02 由 A0 批准））。
- 审计/回执：交付物落 `C:\项目\deliverables/`，并在 `STATUS.md` 追加一行摘要。

## 四、写作底线

1. 事实给证据（命令、退出码、跑批结果），不写"大概/应该"；
2. 可复现：读者按文档命令能重跑；
3. 时效：文档头写日期与状态（待批/已执行/已废弃）；
4. 不写凭据：token/密码/私钥一律 `<masked>`（见 `SECURITY.md`）；
5. 中文为主，代码/命令/路径用原文。
