# W8 · tools/kb_corpus ruff 遗留清理（F/E9 告警清零）

- 任务：task-166（W8-ruff 遗留清理）
- 执行时间：2026-11-04 03:39 ~ 03:56（本地，py312 / ruff 0.16.4）
- 范围：仅 `tools/kb_corpus/` 下 F/E9 告警；**不 commit**；不触碰其他线正在改的文件
- 结论：**范围内 F/E9 告警 48 → 0**；全目录剩余 1 条位于排除文件（他线 WIP，非本单范围）；语法门 PASS；相关单测无语义回归

## 一、前/后计数

| 指标 | 前（03:39:58 快照） | 后（03:56 复核） | 变化 |
|---|---:|---:|---:|
| `ruff check --select F,E9 tools/kb_corpus` 总告警 | **48** | **1**（全在排除文件） | −47 |
| 其中本单范围内（29 个文件） | 48 | **0** | **−48** |
| 其中排除文件（他线 WIP） | 0 | 1 | +1（非本单引入/不处理） |
| 涉及文件数 | 29 | 0（范围内） | — |
| F401 unused-import | 19 | 0 | −19 |
| F541 f-string-without-placeholders | 12 | 0 | −12 |
| F841 unused-variable | 17 | 0 | −17 |
| F821 undefined-name | 0 | 0 | 0 |

命令与证据：
```powershell
# 前
C:\项目\py312\python.exe -X utf8 -m ruff check --select F,E9 tools/kb_corpus
# → Found 48 errors. (F841 17 / F541 12 / F401 19)

# 后
C:\项目\py312\python.exe -X utf8 -m ruff check --select F,E9 tools/kb_corpus
# → Found 1 error.
# tools\kb_corpus\export_governed_data.py:20:25: F401 [*] `collections.defaultdict` imported but unused
```

> 剩余 1 条 `export_governed_data.py:20 F401`：该文件属任务书明确的**排除清单**（其他线正在改，实测 mtime 2026-11-04 03:43:29，本会话观察期内被其他线改动 122 行）；本单未触碰该文件，**不代改**。范围内计数 = **0**。

## 二、修改清单（29 文件，+23/−47 行）

| 文件 | 前告警 | 处理方式 |
|---|---|---|
| card_from_units.py | F841×1 | `pid = cards_pid = None` → `pid = None`（`cards_pid` 无后续引用） |
| card_precheck.py | F841×1 | 去掉 walrus 绑定，`any(not f.startswith("TRACE_MISSING:") for f in flags)`（真值语义等价） |
| card_precheck_v3.py | F541×1 | 无占位 f-string 去 `f` 前缀（字面量不变） |
| card_proposal_v4.py | F841×1 | 删除未使用的 `tier2` 列表推导（纯计算，无副作用） |
| card_reextract.py | F841×1 | 删除未使用的 `existing_answers` 列表（纯计算） |
| card_review.py | F401×1 / F541×1 | 删除 `import re`；无占位 f-string 去前缀 |
| card_revision_apply.py | F401×2 | 删除 `importlib.util`、`safe_json.SafeJsonError`（均未被引用） |
| concept_dedup.py | F541×1 | 无占位 f-string 去前缀 |
| eval_search_v2_ext2.py | F401×1 | 删除 `import random` |
| eval_set_v3.py | F841×1 | **保留 `freeze(write=True)` 写盘副作用**，重写为 `if not V3_PATH.exists(): freeze(write=True)` |
| eval_v3_baseline.py | F401×2 | 删除 `collections.Counter`、`eval_registry` 导入（均未引用） |
| export_conclusions.py | F401×1 | 删除 `datetime.datetime` 导入 |
| governance_today_audit.py | F541×7 | 7 条无占位 f-string 去 `f` 前缀（输出文本不变） |
| kb_qa_bank_v2.py | F401×1 | 删除 `SafeJsonError`（保留 `backup_then_atomic_write`） |
| kb_qa_bank_v3.py | F401×1 | 同上 |
| kb_qa_expand.py | F401×1 | 同上 |
| kb_qa_multi_eval.py | F841×1 | 删除未使用的 `graded` 列表（纯计算） |
| knowledge_multisource.py | F841×2 | `(ka, sa), (kb, sb)` → `(_, sa), (_, sb)`（仅未使用的 key 变量） |
| learning_analysis.py | F401×1 | `from collections import Counter, defaultdict` → `Counter` |
| learning_capability_map.py | F401×1 | 删除 `import re` |
| learning_ops.py | F401×1 / F841×1 | 删除 `import shutil`；删除未使用的 `src = Path(...)` |
| merge_sim.py | F541×1 | `f"### 风险"` → `"### 风险"`（内容不变） |
| merge_sim_89.py | F841×2 / F541×1 | 删除 `metric()` 内未使用的 `total`、`k`；f-string 去前缀 |
| pdf_ingest.py | F841×5 | 删除 5 处未使用的 `pdf_dir = Path(args.pdf_dir)`（cmd_ocr/structure/distill/verify/index；`iter_pdfs(args)` 不依赖该变量，cmd_scan 中的同名变量仍在使用、未动） |
| search_v2.py | F401×1 | 删除 `import math` |
| search_v2b.py | F401×1 | 删除 `import math` |
| shipinhao_cards_routine.py | F401×2 | 删除未使用的 `re`、`sys` 导入 |
| shipinhao_methodology.py | F401×1 | 删除 `collections.Counter` 导入 |
| tool_intel_extract.py | F401×1 / F841×1 | 删除 `collections.defaultdict`；删除未使用的 `unit_text` |

**保留项/noqa**：范围内 **0 条 noqa**。17 条 F841 全部以「不改语义」方式处理：
- 13 处纯 dead-store 直接删除（RHS 为纯表达式）；
- 1 处 `eval_set_v3` 保留函数调用副作用（改写为条件语句，不绑定变量）；
- 2 处 tuple-unpack 的未使用 key 改为 `_`；
- 1 处 `card_precheck` 的 walrus 真值判断改为等价的 `any(...)`。

改动均为未使用导入/未使用赋值/无占位 f-string 前缀，**逐文件 `git diff` 复核无逻辑分支、数值、输出文本变化**（f-string 去前缀后字符串内容逐字节相同）。

## 三、并发与排除项（不触碰）

任务书排除清单（他线正在改，本单未改）：`multi_source_canonical.py`（新文件，03:42 创建）、`rag_baseline_eval.py`（新文件，03:46 创建）、`knowledge_dedupe.py`、`knowledge_governance*.py`、`export_governed_data.py`（03:43 改动 122 行）、`training_data_matrix.py`、`data_governance_overview.py`。
- 前快照（03:39:58）中这 7 个文件 **均无 F/E9 告警**，故前后计数不受其并发编辑影响；
- 后复核时 `export_governed_data.py` 出现 1 条 F401，系他线 WIP 引入，**按范围约定不代改**；
- 任务单 write_scopes 与 task-164 提示重叠：本单 diff 严格限制在前快照命中的 29 个文件（`git diff --numstat` 实测 29 文件 +23/−47），未触碰排除清单。

## 四、验证证据

### 1) 语法门（评审门口径）
```powershell
C:\项目\py312\python.exe -X utf8 tools/check_syntax.py --fail-on-warning
# → 语法门：4342 文件 / 错误 0 / 告警 0 / 0.11s
# → result: PASS
```

### 2) 相关单测
环境已确认：本机 py312 为 embed 发行版（`C:\项目\py312\python312._pth` 存在，PYTHONPATH 不生效）；`tools/tests` 中 4 个用例用 `importlib` 直接加载 `tools/kb_corpus/*.py`，依赖 `tools/kb_corpus` 在 `sys.path`（HEAD 上同样如此，属**既有环境/测试隔离问题**：`ModuleNotFoundError: No module named 'safe_json'`，非本单改动引入）。因此以「显式插入 sys.path」方式跑全量相关用例：

| 用例组 | 结果 | 说明 |
|---|---|---|
| 21 个相关测试文件（card/concept/kb_qa/learning/merge_sim/pdf/search） | **114 passed / 6 failed** | 6 个失败均为**既有数据漂移/子进程环境**问题，见下 |
| 另 4 个相关文件（eval_v3_merge / eval_v3_baseline / export_conclusions / api_learning_tool_intel） | **16 passed / 2 failed** | 2 个失败同为数据漂移 |

8 个失败的归属（均与本单改动无关）：
1. **子进程无法导入 safe_json（3 个）**：`test_kb_qa_bank_v2/v3::test_build_payload_schema_and_writes`、`test_kb_qa_expand::test_cli_help_and_dry_run` —— CLI 用 `subprocess` 调 `python kb_qa_bank_v2.py`，子进程 sys.path 不含 `tools/kb_corpus` → `ModuleNotFoundError: safe_json`（HEAD 导入行相同，既有问题）。
2. **共享数据漂移（5 个）**：`test_card_precheck_v3::test_no_mutation_and_idempotent`（report 记录 sha vs 当前 proposals 文件已变）、`test_card_revision::test_real_outputs_and_source_unchanged`（记录 sha vs 当前 `portal/static/kb_learning_cards.json`，mtime 11-03）、`test_merge_sim_89::test_run_limited_schema_and_outputs`（`units_before` 期望 498、当前 516）、`test_eval_v3_merge::test_real_data_unchanged`、`test_eval_v3_baseline::test_reference_consistency_and_significance`（期望 0.0989、当前 0.1012）——均为「历史报告快照 vs 当前共享数据」比对，其他并行波次改动共享数据导致，与本单仅删未使用导入/赋值的 diff 无因果关系。

### 3) 差异复核
```powershell
git diff --numstat -- tools/kb_corpus/card_from_units.py ... (29 个前快照文件)
# → 29 files changed, 23 insertions(+), 47 deletions(-)
```
未 commit（`git log` 未新增，改动仅存工作区）。

## 五、复现

```powershell
cd C:\项目\老田的AI助手
C:\项目\py312\python.exe -X utf8 -m ruff check --select F,E9 tools/kb_corpus
C:\项目\py312\python.exe -X utf8 tools/check_syntax.py --fail-on-warning
# 相关单测（embed py312 需先注入 tools/kb_corpus 到 sys.path）：
C:\项目\py312\python.exe -X utf8 -c "import sys; sys.path.insert(0,'C:/aios/tools/kb_corpus'); import pytest; raise SystemExit(pytest.main(['-q','tools/tests/test_card_from_units.py','tools/tests/test_card_precheck.py','tools/tests/test_card_review.py','tools/tests/test_merge_sim_89.py','tools/tests/test_pdf_ingest_contract.py']))"
```

## 六、遗留与建议
1. 本单范围内 F/E9 **已清零（0 条）**；全目录仅剩 `export_governed_data.py:20 F401`，属排除清单（他线 WIP），建议由该线随其改动一并清理（`ruff --fix` 可自动处理）。
2. 既有环境问题（非本单引入，建议另立单）：embed py312 下 `tools/tests` 中 4 个用 `importlib` 加载 `tools/kb_corpus` 模块的用例必须先注入 `sys.path`；3 个 CLI 子进程用例同理。修复方向：测试内 `sys.path.insert(0, str(REPO/"tools"/"kb_corpus"))` 或模块改为相对导入。
3. 数据漂移类失败（5 个）建议由数据 owner 在共享数据更新后同步刷新报告快照/参考值，否则会持续干扰测试门。
