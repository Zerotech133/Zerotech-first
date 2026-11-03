# 百炼部署后评测（W17 · 2026-11-04）

> 模式：**dry-run**（未调用真实 API：仅请求计划/管道校验）
> 模型：`qwen3-14b-quantkb-<替换为部署实例ID>` · base_url：`https://dashscope.aliyuncs.com/compatible-mode/v1` · key_source：未找到 key
> 数据：40 题（可答 30 + 不可答 10）· 来源 `C:\项目\老田的AI助手\research\reports\answer_eval_20261104.json`

## 一、部署后如何运行（命令）

```powershell
# dry_run
python tools/finetune/bailian_eval.py --dry-run --limit 5 --write
# mock
python tools/finetune/bailian_eval.py --mock --limit 5
# real
python tools/finetune/bailian_eval.py --model <部署实例ID> --write
# real_intl
python tools/finetune/bailian_eval.py --model <部署实例ID> --base-url https://dashscope-intl.aliyuncs.com/compatible-mode/v1 --write
```

**如何填模型实例 ID**：百炼控制台 → 模型部署/我的模型 → 部署实例 → 复制实例 ID 或模型名称，替换 `--model` 参数（默认占位 `qwen3-14b-quantkb-<替换为部署实例ID>`）。Key 放 `C:/项目/_vault/secrets/dashscope_api_key.txt` 或环境变量 `DASHSCOPE_API_KEY`。

## 二、指标对比（W13 基线 vs 部署后）

| 指标 | W13 基线 | 部署后 | Δ | 目标 | 达标 |
|---|---:|---:|---:|---:|---|
| 引用正确率 | 0.725 | —（待真实复测） | — | ≥0.9 | — |
| groundedness | 0.4776 | —（待真实复测） | — | ≥0.6 | — |
| 不可答拒答率 | 0.8 | —（待真实复测） | — | ≥0.8 | — |
| gold 相似度 | 0.3845 | —（待真实复测） | — | ≥0.3 | — |
| 过度声明率 | 0.0 | —（待真实复测） | — | ≤0.05 | — |
| 长度异常率 | 0.0 | —（待真实复测） | — | ≤0.1 | — |

> 本次 dry-run 模式：`results` 为空，指标待部署后真实运行填充；上表「部署后」列为真实运行时的对比位。

## 三、请求计划（dry-run）

- 计划题数：40 / 40；has_api_key=False
- URL：`https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions`
- 样例 prompt（前 400 字）：

```text
你是投研知识助手。仅依据下列资料回答，不要编造；用 2-4 句话回答，并在末尾用 [1][2] 标注所依据资料编号；若资料不足以回答，请明确说「资料不足/资料未提及」。

[1] (C:009) 什么是「AI 产业投资传导链条」？ 当前 AI 产业的投资线索正沿着“模型降价→应用放量→芯片需求→制造扩产”的链条自上而下传导。 我们认为，当前 AI 产业的投资线索正沿着“模型降价→应用放量→芯片需求→制造扩产”的链条自上而下传导，景气验证不断下沉：模型端价格战降低应用门槛，端侧与 AIoT 场景加速打开；芯片端新品周期临近，渗透率与单机价值量双升。
[2] (U:learn_视频号学习_第二期_1099) A股开盘前，你不可能看完全球108个信息源，但这个开源项目想先替你筛出真正影响板块的领先信号。Investment News专为A股投资者设计，AI半导体、机器人、新能源车等12大赛道，直
```

| qid | type | answerable | prompt字符 | ctx_ids |
|---|---|---|---:|---|
| A01 | card_concept | True | 1227 | C:009,U:learn_视频号学习_第二期_1099,U:learn_视频号学习_第二期_0008,U:learn_ |
| A02 | card_concept | True | 989 | C:278,U:learn_视频号学习_8博主_0842,U:learn_视频号学习_8博主_0079,U:learn_ |
| A03 | card_concept | True | 1180 | C:124,C:103,C:090,C:057,C:122 |
| A04 | card_concept | True | 1189 | C:038,C:079,C:075,C:140,U:learn_视频号学习_第二期_1150 |
| A05 | card_concept | True | 1143 | C:073,C:259,U:learn_视频号学习_8博主_0476,U:learn_视频号学习_8博主_0027,C: |
| A06 | card_concept | True | 1009 | C:171,U:learn_laomu_notes_0259,C:251,U:learn_视频号学习_8博主_0703, |
| A07 | card_concept | True | 1131 | C:079,C:038,C:267,C:061,C:013 |
| A08 | card_concept | True | 1154 | C:236,U:learn_视频号学习_8博主_0445,U:learn_视频号学习_8博主_1000,U:learn_ |
| A09 | card_evidence | True | 1022 | U:learn_视频号学习_8博主_0350,C:274,U:learn_视频号学习_8博主_0505,U:learn_ |
| A10 | card_evidence | True | 1032 | C:288,C:287,C:388,U:learn_视频号学习_8博主_1005,U:learn_视频号学习_8博主_0 |
| A11 | card_evidence | True | 1012 | U:learn_视频号学习_8博主_0661,C:299,U:learn_视频号学习_8博主_0778,U:learn_ |
| A12 | card_evidence | True | 822 | C:304,C:328,C:207,U:learn_视频号学习_8博主_0263,C:326 |
| A13 | card_evidence | True | 1158 | C:308,U:learn_视频号学习_8博主_0363,U:learn_视频号学习_8博主_0604,U:learn_ |
| A14 | card_evidence | True | 879 | C:164,U:learn_laomu_notes_0214,C:173,U:learn_视频号学习_8博主_0630, |
| A15 | card_evidence | True | 1152 | C:093,C:029,C:063,C:035,C:071 |
| A16 | card_evidence | True | 986 | C:329,U:learn_视频号学习_8博主_0609,U:learn_视频号学习_8博主_1005,U:learn_ |
| A17 | unit_howto | True | 910 | U:learn_laomu_notes_0001,U:learn_视频号学习_8博主_0701,U:learn_视频号学 |
| A18 | unit_howto | True | 1267 | U:learn_视频号学习_第二期_0001,U:learn_视频号学习_第二期_0022,U:learn_视频号学习_ |
| A19 | unit_howto | True | 1159 | U:learn_视频号学习_8博主_0002,U:learn_laomu_notes_0269,U:learn_视频号学 |
| A20 | unit_howto | True | 1033 | U:learn_laomu_notes_0003,U:learn_laomu_notes_0009,U:learn_视频 |
| A21 | unit_howto | True | 1134 | U:learn_视频号学习_8博主_0259,U:learn_视频号学习_8博主_0279,U:learn_视频号学习_ |
| A22 | unit_howto | True | 808 | U:learn_视频号学习_8博主_0004,C:152,U:learn_laomu_notes_0164,U:lear |
| A23 | unit_howto | True | 1130 | U:learn_视频号学习_第二期_1284,U:learn_视频号学习_第二期_1151,U:learn_视频号学习_ |
| A24 | unit_fact | True | 1131 | U:learn_视频号学习_8博主_0001,U:learn_视频号学习_8博主_0013,U:learn_视频号学习_ |
| A25 | unit_fact | True | 810 | U:learn_laomu_notes_0002,U:learn_laomu_notes_0008,U:learn_视频 |
| A26 | unit_fact | True | 1259 | U:learn_视频号学习_第二期_0002,U:learn_视频号学习_第二期_1097,C:032,U:learn_ |
| A27 | unit_fact | True | 1259 | U:learn_视频号学习_8博主_0003,U:learn_视频号学习_8博主_1014,U:learn_视频号学习_ |
| A28 | unit_fact | True | 536 | U:learn_laomu_notes_0004,U:learn_laomu_notes_0010,C:144,U:le |
| A29 | unit_fact | True | 1265 | U:learn_视频号学习_8博主_1014,U:learn_视频号学习_8博主_1009,U:learn_视频号学习_ |
| A30 | unit_fact | True | 895 | U:learn_laomu_notes_0153,U:learn_视频号学习_8博主_0005,C:148,U:lear |
| U01 | unanswerable | False | 947 | C:055,U:learn_视频号学习_第二期_1164,U:learn_视频号学习_第二期_1092,U:learn_ |
| U02 | unanswerable | False | 1225 | C:104,C:064,U:learn_视频号学习_8博主_0264,U:learn_视频号学习_第二期_1160,U: |
| U03 | unanswerable | False | 1162 | U:learn_视频号学习_第二期_1092,U:learn_视频号学习_第二期_1164,U:learn_视频号学习_ |
| U04 | unanswerable | False | 1262 | U:learn_视频号学习_第二期_1291,U:learn_视频号学习_8博主_0075,U:learn_视频号学习_ |
| U05 | unanswerable | False | 1199 | U:learn_视频号学习_第二期_1123,U:learn_视频号学习_8博主_0505,C:314,U:learn_ |
| U06 | unanswerable | False | 890 | U:learn_视频号学习_8博主_0323,U:learn_视频号学习_8博主_0282,U:learn_视频号学习_ |
| U07 | unanswerable | False | 829 | U:learn_视频号学习_8博主_0680,U:learn_视频号学习_8博主_0698,C:088,U:learn_ |
| U08 | unanswerable | False | 1144 | U:learn_视频号学习_第二期_0018,U:learn_视频号学习_8博主_0472,U:learn_视频号学习_ |
| U09 | unanswerable | False | 1115 | U:learn_视频号学习_第二期_1164,U:learn_视频号学习_第二期_0021,U:learn_laomu_ |
| U10 | unanswerable | False | 1107 | U:learn_视频号学习_8博主_0320,U:learn_视频号学习_8博主_0305,U:learn_视频号学习_ |

## 三-B、评测管道自检（mock，非真实模型结果）

> 非真实模型结果，仅验证评测管道（40 题；errors=0）

| 指标 | W13 基线 | mock 管道 | 目标 |
|---|---:|---:|---:|
| 引用正确率 | 0.725 | 0.675 | ≥0.9 |
| groundedness | 0.4776 | 0.1225 | ≥0.6 |
| 不可答拒答率 | 0.8 | 0.3 | ≥0.8 |
| gold 相似度 | 0.3845 | 0.0034 | ≥0.3 |

## 五、结论

- 脚本已就绪：`--dry-run` 可离线生成 40 题请求计划；部署后填实例 ID 即可一键复测并与 W13 基线同口径对比。
- 合格标准（W13 目标阈值）：引用正确率 ≥0.90、groundedness ≥0.60、不可答拒答率 ≥0.80、gold 相似度 ≥0.30、过度声明 ≤0.05、长度异常 ≤0.10。
- 若部署后指标未达阈值：优先检查部署实例是否正确、prompt 是否与训练一致、以及检索上下文是否同 W13（ctx_ids 固定）。

## 六、局限

1. 本脚本只读知识库/报告；真实调用仅在部署后显式执行（本次交付未调用真实 API）。
2. model 需填百炼控制台「部署后的模型实例 ID/名称」（如 qwen3-14b-quantkb-xxxx）。
3. W13 基线：citation_accuracy=0.725、groundedness=0.4776、correct_refusal_rate=0.8、gold_similarity=0.3845（40 题口径）。
4. 指标为确定性规则（bigram/引用解析/词典），非 LLM 裁判；与 W13 同口径可比。
5. 未找到 DASHSCOPE_API_KEY/本地 key 文件：自动降级为 dry-run（未调用 API）。

> 研究输出，不构成投资建议。
