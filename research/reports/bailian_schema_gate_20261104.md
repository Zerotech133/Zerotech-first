# 百炼数据集 schema 校验门禁（W26 · 2026-11-04）

> 任务：task-186（W26 百炼数据 schema 校验门禁）；交付：`tools/finetune/validate_bailian_schema.py` +
> `tools/tests/test_validate_bailian_schema.py`（8 passed）+ 本报告。只读数据、不联网、不 commit。
>
> **结论先行**：门禁实现并验证通过——**raw v4 报错**（顶层 `category`：train 1224/3769 行、val 137/270 行），
> **v4d 通过**（train 3769 行、val 270 行，0 错误）。该类顶层多余字段正是百炼 SFT 导入失败的已知原因，
> 上传前跑本脚本即可拦截。

## 一、校验规则

| 层 | 规则 | 错误码 |
|---|---|---|
| 文件 | UTF-8 编码、**无 BOM** | `utf8_decode` / `bom` |
| 行 | 每行（忽略空行）为 JSON object；单行字符数 ≤ `--max-line`（默认 4000） | `invalid_json` / `not_object` / `line_too_long` |
| SFT 顶层 | **仅允许 `messages`、`meta`**；额外顶层字段（如 `category`）报错；`meta` 若存在必须是 object | `extra_top_level_keys` / `missing_top_level_keys` / `meta_not_object` |
| DPO 顶层 | **仅允许 `messages`、`chosen`、`rejected`**；`prompt`/`source` 等额外字段报错 | `extra_top_level_keys` / `missing_top_level_keys` |
| messages | list；每条仅 `role`/`content`；role ∈ {system,user,assistant}；system 最多一条且在首位；其后 user/assistant **交替**；content 非空 | `messages_not_list` / `message_role` / `message_extra_keys` / `system_position` / `message_alternation` / `message_content` |
| SFT 结尾 | messages 必须以 **assistant** 结尾（有监督目标） | `missing_assistant` |
| DPO 专项 | messages 必须以 **user** 结尾；`chosen`/`rejected` 必须是 object 且 `role=assistant`、content 非空 | `dpo_prompt_tail` / `chosen_role` / `rejected_role` / `chosen_not_object` 等 |
| 输出 | 条数/空行/行长度 max/avg/角色计数/顶层 key 计数/错误列表（前 200 条，invalid_lines 为全量）；退出码 **0=通过 / 1=有错误** | — |

## 二、用法（上传百炼前必须执行）

```powershell
# 1) SFT 清洗后文件（期望 PASS，rc=0）
C:\项目\py312\python.exe -X utf8 tools/finetune/validate_bailian_schema.py `
  --path research/finetune/sft/quant_kb_sft_v4d_train.jsonl --type sft

# 2) raw v4（期望 FAIL，rc=1：顶层 category）
C:\项目\py312\python.exe -X utf8 tools/finetune/validate_bailian_schema.py `
  --path research/finetune/sft/quant_kb_sft_v4_train.jsonl --type sft

# 3) DPO 文件 + 机器可读 JSON（接 CI / 报告）
C:\项目\py312\python.exe -X utf8 tools/finetune/validate_bailian_schema.py `
  --path <dpo.jsonl> --type dpo --json

# 4) 自定义行长度上限 + 测试
... --max-line 8000
C:\项目\py312\python.exe -X utf8 -m pytest -q tools/tests/test_validate_bailian_schema.py   # 8 passed
```

可作为上传前门禁：`if rc != 0: 拒绝上传`（本脚本不联网、不写任何数据）。

## 三、在真实文件上的检测结果（2026-11-04 实测）

| 文件 | 类型 | 结果 | 非空行 | 有效/无效 | max 行字符 | 顶层 keys | 错误 |
|---|---|---|---:|---:|---:|---|---|
| `quant_kb_sft_v4d_train.jsonl` | SFT | **✅ PASS** | 3769 | 3769 / 0 | 3124 | `messages`×3769 | 无 |
| `quant_kb_sft_v4_train.jsonl` | SFT | **❌ FAIL** | 3769 | 2545 / **1224** | 3150 | `messages`×3769、`meta`×2545、`category`×1224 | `extra_top_level_keys`（category） |
| `quant_kb_sft_v4d_val.jsonl` | SFT | **✅ PASS** | 270 | 270 / 0 | 3034 | `messages`×270 | 无 |
| `quant_kb_sft_v4_val.jsonl` | SFT | **❌ FAIL** | 270 | 133 / **137** | 3060 | `messages`×270、`meta`×133、`category`×137 | `extra_top_level_keys`（category） |
| `quant_kb_dpo_v3_candidates.jsonl` | DPO（百炼口径） | ❌ FAIL | 28 | 0 / 28 | 1174 | `prompt/chosen/rejected/source/category/concept/meta` | `extra_top_level_keys` + `missing_top_level_keys` + `chosen/rejected_not_object`（见补充观察） |

**根因**：v4 raw 有 1224/3769 行（val 137/270）在**顶层**带 `category` 字段；百炼 SFT 数据集只接受
`messages`（及 `meta`），顶层 `category` 会触发导入失败。v4d 已完成清洗（顶层仅 `messages`，`category`
与 `meta` 一并移除）→ 门禁 PASS，可直接上传。

## 四、补充观察（非本单修改，供后续线参考）

- 现网 DPO 候选文件（v1/v2/v3）采用 `prompt/chosen/rejected(+source/category/concept/meta)` 结构，
  与**百炼 DPO 口径**（`messages` + `chosen/rejected` 为 role=assistant 的 object）不一致；按本门禁
  `--type dpo` 会报 `extra_top_level_keys`/`missing_top_level_keys`/`*_not_object`。
  若这些文件要直接用于百炼 DPO，需补一次结构转换（本门禁不代改数据文件）；若用于 LLaMA-Factory 等
  其他框架则以其 schema 为准（门禁可加 `--type` 扩展）。
- v4d 把 `meta` 一并移除后仍 PASS；若希望保留溯源信息，需确认百炼对 SFT `meta` 字段的实际接受度
  （本门禁按任务口径允许 `meta`，不阻止）。

## 五、局限

1. 只做**结构/schema**校验，不查语义正确性、重复/泄漏、训练/验证集分布；
2. 错误列表最多展示 200 条，`invalid_lines` 为全量计数（v4 train 实际 1224 行无效）；
3. 规则依据任务口径与百炼 SFT/DPO 导入约束整理；百炼控制台若调整 schema 需同步更新本门禁；
4. 本次未修改任何数据文件（只读校验），未 commit。

> 研究/工程输出；门禁通过≠数据质量合格，训练前仍需数据质量审计。
