# 自评（黄忠 W1 正式提交）

- 自评分数：**94/100**
- 完成物：9 文件（README 1 + 判据 3 + 七检解读 2 + 回滚点 2 + 自评 1）
- 数据来源：2026-10-30 01:00–01:15 现场实测（本仓真实 commit/测试数/扫描数），无编造

## 一、分项自评

| 维度 | 分值 | 自评 | 依据 |
|---|---|---|---|
| 可测性 | 25 | 24 | 3 份判据共 18 条，每条均有命令/路径与通过线；扣 1 分：判据 03 的「不扩能力」仅验 events 保留，未做能力全集 diff |
| 证据链 | 25 | 24 | 全部 SHA 已用 `git merge-base --is-ancestor` 核对；原始输出落盘到交付证据目录；扣 1 分：语法门 2893 为历史记录（时点 2026-10-28），与现场 3059 不同时点，仅能以复现方式交叉印证 |
| 七检解读 | 25 | 24 | 全绿解读为现场实跑（7/7，含逐项耗时与计数）；语法门拦截案例含历史原始行 + 现场复现（修复前 2 告警 → 修复后 0）；扣 1 分：现场语法门因 2 个既有 BOM 文件仍 FAIL，无法给出「当前全绿」结论 |
| 回滚完整 | 25 | 22 | 2 个回滚点均含「备份或 git revert + 重启/生效 + 验证」三步；回滚 02 已做真实演练并复位；扣 3 分：回滚 01 的线上重启属 L2，只做本地打桩预演、未获批准未真执行 |
| 自评加分 | 5 | — | 自评数字与问题清单齐备 |

## 二、遇到的问题（5 条）

- 问题1：七检首跑 `capabilities` 失败（子进程 gbk 解码线程 `UnicodeDecodeError`，rc=1），根因是会话未设 UTF-8 环境；设 `PYTHONUTF8=1` + `PYTHONIOENCODING=utf-8` 后复跑 7 项全绿。已把环境前置写进 `gate_review_01.md`，避免下一位同学把环境坑误判成代码缺陷。
- 问题2：ZHAOYUN 不在 `org.json` 的 62 席 `agent_profiles` 内，`resolve_binding("ZHAOYUN")` 回落到平台默认 `ollama/qwen3.5:9b`，与 `serve.py:118` 注释「正式绑定=DSH 会话载体 deepseek-v4-pro」存在口径落差；回滚 01 验证因此不能只看 binding 值，须看 `meeting_override` 标记字段消失 + 单测口径。
- 问题3：语法门现场扫描 3059 文件、2 个既有语法错误（`tools/research/h10_quality_sensitivity.py`、`tools/s2_sign_pilot.py` 文件头 BOM U+FEFF），与本单无关但会导致语法门当前非全绿；若后续把语法门接进 merge 前置，建议另立工单清 BOM，不宜在本 docs-only 单里扩范围。
- 问题4：worktree 内无 `.worktrees/tasks.json`（工作树隔离），`tools/task_board_audit.py` 在 worktree 内只能走「不可读」兜底分支（ok=false，error 明确）；有效对账必须在 master 工作区跑。已在判据 02 与回滚 02 里写明，防止误判工具坏。
- 问题5：`ruff_ratchet` 基线存在历史余量（baseline_total=48、current_total=7），回滚单个 F401 时棘轮不会报 regression（实测 7→8 仍 ok=true）；说明「棘轮绿」不等于「本文件干净」，判据须落到文件级 ruff + 单测，不能只看棘轮汇总。

## 三、下轮改进

1. 判据 03 增加能力全集 diff（读取 registry 能力项集合，断言「只增不减或零变更」）。
2. 语法门案例补一条「定向文件门禁」命令输出（`--files quant_backtest/kg_build_p2.py`）落盘，减少对历史记录的依赖。
3. 回滚 01 待批准窗口做一次真实 8796 重启验证：记录新旧 pid 与重启前后 `/api/chat` 响应字段对比，补齐「线上实测」证据。
