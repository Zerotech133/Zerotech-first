# ADR-004：文档体系采用 Diátaxis 分类 + ADR 记录架构决策

- **状态**：Accepted（2026-10-27）
- **决定者**：DSH-Lead（老田 2026-10-27 指令：学习开源实战项目文档优化本项目）
- **关联**：`docs/README.md`、`ARCHITECTURE.md`、`CONTRIBUTING.md`

## 背景

仓库已有 **1,000+ 份文档**（`docs/plans` 237、`docs/audit` 103、`docs/ops` 122、`deliverables` 数百），
按"事件/日期"堆积，缺少面向读者的分类与入口：新成员（或新 Agent 会话）不知道先读哪份、哪份是权威。
开源项目（Cloudflare、Gatsby 等）采用 [Diátaxis](https://diataxis.fr/) 把文档分为
**Tutorials / How-to / Reference / Explanation** 四类；架构决策采用
[ADR](https://adr.github.io/) 一决策一文件、可追溯。

## 决定

1. 新增 `docs/README.md` 作为文档首页，给出四类映射与目录职责（不改历史目录名）；
2. 新文档按四类归位：操作/排障→How-to（`docs/ops`、`docs/runbooks`），
   契约/标准/决策→Reference（`docs/specs`、`docs/standards`、`docs/adr`），
   设计/计划/审计→Explanation（`docs/design`、`docs/plans`、`docs/audit`）；
3. 架构级决定必须写 ADR（本目录，编号连续）；
4. 根级补 `ARCHITECTURE.md`（当前架构权威简图）、`CONTRIBUTING.md`（贡献流程）、
   `SECURITY.md`（凭据/漏洞/事故）、`CHANGELOG.md`（变更记录）；
5. 文档索引继续由 `tools/gen_docs_index.py` 维护（`--check` rc=0 为门禁）。

## 后果

- 正面：新会话有明确入口；决策可追溯；文档不再只看日期找。
- 代价：需要持续维护（每次架构/流程变化同步更新 4 个根文档）。
- 不做：不迁移历史文档、不建静态站点（mkdocs 待后续评估）。
