# 来源证据补齐与候选快照重建实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将 E 盘两份新增审阅文档纳入只读候选来源，登记可复核的来源证据和稳定 `source_id`，重建候选快照及 RAG 基线，并保留无法证明的原件阻断。

**Architecture:** 在候选快照构建工具中加入显式证据台账输入；台账只接受来源相对路径、文档声明日期、观察时间、证据引用和哈希，不从文件名或 mtime 推断日期。快照继续以 `read_only`、`training_eligible=false`、`production_eligible=false` 输出；缺失的 2026-10-08 原文和未认证的时序字段进入 blockers。

**Tech Stack:** Python 3.11 标准库、JSON/JSONL、pytest、SHA256。

**Spec:** `AGENTS.md` 与现有候选构建器 `build_e_drive_candidate_snapshot.py` 的只读门禁约束。

## Global Constraints

- 训练、LoRA、模拟成交、真实交易和收费上架保持关闭。
- 不把 2026-10-09 量子双刀文档替代缺失的 2026-10-08 原文。
- 日期只能来自文档正文或可复核回执；禁止用文件名、mtime 或报告生成时间推断。
- 不读取、接收或代管私钥和口令。

## Review Focus

- 两份新增文档必须获得稳定 `source_id`，且源哈希与当前 E 盘文件一致。
- 证据台账缺字段时必须阻断快照，而不是默认通过。
- 精确缺失原件必须在快照 blockers 中保留，不能被相关文档吞掉。
- RAG 基线应报告来源召回、拒答和时序证据状态，且模型调用/训练步数为 0。
- 重建后所有输出必须落在 E 盘 `work/academy-source-evidence-v0.1`，不改原始来源。

### Task 1: 扩展候选快照构建器

**Files:**
- Create: `tools/rebuild_academy_source_snapshot.py`
- Test: `tools/test_rebuild_academy_source_snapshot.py`

**Interfaces:**
- `build_snapshot(index_path, root, evidence_path, output_dir) -> dict`
- 输入证据台账按相对路径映射，输出 `candidate-source-snapshot-v0.3.json` 与 `candidate-eval-manifest-v0.3.json`。

- [ ] 加入两份新增文档 allowlist，并用相对路径哈希生成 `source_id`。
- [ ] 校验索引 bytes/SHA256 与当前文件；校验台账 evidence_ref、source_date、observed_at 格式。
- [ ] 缺失/不完整证据进入 blockers；输出始终 candidate-only。
- [ ] 用临时目录测试哈希漂移、证据缺失、路径越界与稳定 source_id。

### Task 2: 生成 E 盘证据台账和候选快照

**Files:**
- Create: `work/academy-source-evidence-v0.1/source-evidence-ledger-v0.1.json`
- Create: `work/academy-source-evidence-v0.1/candidate-source-snapshot-v0.3.json`
- Create: `work/academy-source-evidence-v0.1/candidate-eval-manifest-v0.3.json`
- Create: `work/academy-source-evidence-v0.1/README.md`

- [ ] 从 E 盘全量索引读取两份新增文档的当前哈希。
- [ ] 记录正文声明的 `source_date=2026-10-11`、当前只读观察时间和本地证据引用。
- [ ] 对缺失的 2026-10-08 原文建立明确 unresolved 记录，不创建替代源。
- [ ] 运行构建器并确认两份新增文档有 source_id、training_eligible 仍为 false。

### Task 3: 重跑只读 RAG 基线并验收

**Files:**
- Create: `work/academy-source-evidence-v0.1/rag-baseline-v0.3.json`
- Create: `work/academy-source-evidence-v0.1/evaluation-cases-v0.3.jsonl`

- [ ] 使用更新快照运行 E 盘只读 RAG 基线。
- [ ] 检查正向来源召回、无证据拒答、时序证据缺口和零模型/训练副作用。
- [ ] 记录最终门禁结论：候选快照可评估，但训练、模拟成交和交易继续关闭。

