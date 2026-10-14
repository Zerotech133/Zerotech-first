# K-ORG-078 · Codex 时代 review 存量清场档案（2026-10-14·老田拍板方案 B）

## 处置决定
老田 2026-10-14 拍板：13 张 Codex 时代 review 存量单整批废弃（drop 清场，分支+.codex worktree 一并回收）。

## 废弃清单（13 张）
K-STOCK-136（来源证据）· 145（Ollama 健康）· 146（RAG 增量索引）· 147（Web UX）· 152（Ollama 启动器）·
154（RAG 持久化）· 156（Web 状态）· 157（index-cli）· 158（ops 健康集成）· 159（模型路由）·
160（运行手册 smoke）· 162（三年数据 staging）· 209（缺口发布审计）

## 取证依据（K-ORG-077）
- 13 张逐一 `git log --all --grep "merge task/<TID>"` **均无 merge 痕迹**（真待评审非僵尸）
- 产出物经对照已不同程度被后续工作覆盖；归属 Codex 时代（组织化前），无现岗承接复核
- 备份三层后废弃：内容可随时恢复

## 备份层（恢复入口）
1. **分支引用**（防 gc）：`backup/codex-20261014-0` ~ `-6`，对应 7 个 worktree detached HEAD：
   | sha | 原归属 |
   |---|---|
   | 1699be1 | model-routing(159) / ops-health-integration(158) / runbook-smoke(160) |
   | 715ec20 | ollama-launcher(152) |
   | 5682651 | rag-index(146) / web-ux(147) |
   | bf6c739 | rag-index-persistence(154) |
   | 984da82 | rag-web-incremental(153·已 merge 单顺手回收) |
   | 0753776 | source-evidence-receipt(136) |
   | 631fb05 | web-status(156) |
2. **bundle**：`backups/codex_worktrees_backup_20261014.bundle`（7 引用完整历史·verify 通过）
3. **tasks.json 快照**：`backups/tasks_json_backup_20261014_1715.json`

## 物理回收
- 10 个 `.codex/worktrees/*` 挂载全部移除（git worktree remove/prune；2 个坏壳 model-routing/ollama-launcher 经唯一性鉴定=主仓缺失文件 0 后回收站删除，约 175MB）
- **保留**：`.codex/worktrees/ai-roadmap-design`（task/K-STOCK-228·不在废弃范围）
- worktree 注册表终态：主仓 + ai-roadmap-design + 本仓 .worktrees/K-STOCK-374/389（团队线活跃）

## 保留项说明
- K-STOCK-153/155 已 merge（9d6b8fd/9213a7d），K-ORG-077 已补流转 done，不在废弃列
