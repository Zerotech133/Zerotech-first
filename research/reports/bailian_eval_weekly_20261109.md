# 百炼部署后评测（W17 · 2026-11-04）

> 模式：**api**（真实 API 复测）
> 模型：`qwen2.5-14b-instruct-a57a3521e4e0` · base_url：`https://llm-4n3lsps262hujtx3.cn-beijing.maas.aliyuncs.com/compatible-mode/v1` · key_source：C:\项目\_vault\secrets\dashscope_api_key.txt
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
| 引用正确率 | 0.725 | 0.875 | +0.15 | ≥0.9 | ❌ |
| groundedness | 0.4776 | 0.6634 | +0.1858 | ≥0.6 | ✅ |
| 不可答拒答率 | 0.8 | 1.0 | +0.2 | ≥0.8 | ✅ |
| gold 相似度 | 0.3845 | 0.5192 | +0.1347 | ≥0.3 | ✅ |
| 过度声明率 | 0.0 | 0.0 | 0.0 | ≤0.05 | ✅ |
| 长度异常率 | 0.0 | 0.0 | 0.0 | ≤0.1 | ✅ |

> 本次 api 模式：mock 模式结果仅用于验证代码路径，不代表真实模型指标。

## 三、请求计划（dry-run）

- 计划题数：40 / 40；has_api_key=True
- URL：`https://llm-4n3lsps262hujtx3.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions`
- 样例 prompt（前 400 字）：

```text
你是量化知识库助手。规则：
1) 只依据下面编号资料回答，不得编造；
2) 每个事实句句末必须标注来源编号，如[1]；只能引用确实支撑该句的资料编号；
3) 若资料不足以回答，明确说「资料不足」，不要猜测；
4) 用 3-5 句话回答，不要投资建议。

资料：
[1] 什么是「AI 产业投资传导链条」？ 当前 AI 产业的投资线索正沿着“模型降价→应用放量→芯片需求→制造扩产”的链条自上而下传导。 我们认为，当前 AI 产业的投资线索正沿着“模型降价→应用放量→芯片需求→制造扩产”的链条自上而下传导，景气验证不断下沉：模型端价格战降低应用门槛，端侧与 AIoT 场景加速打开；芯片端新品周期临近，渗透率与单机价值量双升。
[2] A股开盘前，你不可能看完全球108个信息源，但这个开源项目想先替你筛出真正影响板块的领先信号。Investment News专为A股投资者设计，AI半导体、机器人、
```

| qid | type | answerable | prompt字符 | ctx_ids |
|---|---|---|---:|---|
| A01 | card_concept | True | 1244 | C:009,U:learn_视频号学习_第二期_1099,U:learn_视频号学习_第二期_0008,U:learn_ |
| A02 | card_concept | True | 986 | C:278,U:learn_视频号学习_8博主_0842,U:learn_视频号学习_8博主_0079,U:learn_ |
| A03 | card_concept | True | 1278 | C:124,C:103,C:090,C:057,C:122 |
| A04 | card_concept | True | 1238 | C:038,C:079,C:075,C:140,U:learn_视频号学习_第二期_1150 |
| A05 | card_concept | True | 1162 | C:073,C:259,U:learn_视频号学习_8博主_0476,U:learn_视频号学习_8博主_0027,C: |
| A06 | card_concept | True | 1001 | C:171,U:learn_laomu_notes_0259,C:251,U:learn_视频号学习_8博主_0703, |
| A07 | card_concept | True | 1184 | C:079,C:038,C:267,C:061,C:013 |
| A08 | card_concept | True | 1171 | C:236,U:learn_视频号学习_8博主_0445,U:learn_视频号学习_8博主_1000,U:learn_ |
| A09 | card_evidence | True | 1019 | U:learn_视频号学习_8博主_0350,C:274,U:learn_视频号学习_8博主_0505,U:learn_ |
| A10 | card_evidence | True | 1043 | C:288,C:287,C:388,U:learn_视频号学习_8博主_1005,U:learn_视频号学习_8博主_0 |
| A11 | card_evidence | True | 989 | U:learn_视频号学习_8博主_0661,C:299,U:learn_视频号学习_8博主_0778,U:learn_ |
| A12 | card_evidence | True | 830 | C:304,C:328,C:207,U:learn_视频号学习_8博主_0263,C:326 |
| A13 | card_evidence | True | 1175 | C:308,U:learn_视频号学习_8博主_0363,U:learn_视频号学习_8博主_0604,U:learn_ |
| A14 | card_evidence | True | 851 | C:164,U:learn_laomu_notes_0214,C:173,U:learn_视频号学习_8博主_0630, |
| A15 | card_evidence | True | 1226 | C:093,C:029,C:063,C:035,C:071 |
| A16 | card_evidence | True | 1000 | C:329,U:learn_视频号学习_8博主_0609,U:learn_视频号学习_8博主_1005,U:learn_ |
| A17 | unit_howto | True | 847 | U:learn_laomu_notes_0001,U:learn_视频号学习_8博主_0701,U:learn_视频号学 |
| A18 | unit_howto | True | 1287 | U:learn_视频号学习_第二期_0001,U:learn_视频号学习_第二期_0022,U:learn_视频号学习_ |
| A19 | unit_howto | True | 1157 | U:learn_视频号学习_8博主_0002,U:learn_laomu_notes_0269,U:learn_视频号学 |
| A20 | unit_howto | True | 1009 | U:learn_laomu_notes_0003,U:learn_laomu_notes_0009,U:learn_视频 |
| A21 | unit_howto | True | 1132 | U:learn_视频号学习_8博主_0259,U:learn_视频号学习_8博主_0279,U:learn_视频号学习_ |
| A22 | unit_howto | True | 763 | U:learn_视频号学习_8博主_0004,C:152,U:learn_laomu_notes_0164,U:lear |
| A23 | unit_howto | True | 1128 | U:learn_视频号学习_第二期_1284,U:learn_视频号学习_第二期_1151,U:learn_视频号学习_ |
| A24 | unit_fact | True | 1148 | U:learn_视频号学习_8博主_0001,U:learn_视频号学习_8博主_0013,U:learn_视频号学习_ |
| A25 | unit_fact | True | 761 | U:learn_laomu_notes_0002,U:learn_laomu_notes_0008,U:learn_视频 |
| A26 | unit_fact | True | 1296 | U:learn_视频号学习_第二期_0002,U:learn_视频号学习_第二期_1097,C:032,U:learn_ |
| A27 | unit_fact | True | 1279 | U:learn_视频号学习_8博主_0003,U:learn_视频号学习_8博主_1014,U:learn_视频号学习_ |
| A28 | unit_fact | True | 465 | U:learn_laomu_notes_0004,U:learn_laomu_notes_0010,C:144,U:le |
| A29 | unit_fact | True | 1285 | U:learn_视频号学习_8博主_1014,U:learn_视频号学习_8博主_1009,U:learn_视频号学习_ |
| A30 | unit_fact | True | 868 | U:learn_laomu_notes_0153,U:learn_视频号学习_8博主_0005,C:148,U:lear |
| U01 | unanswerable | False | 944 | C:055,U:learn_视频号学习_第二期_1164,U:learn_视频号学习_第二期_1092,U:learn_ |
| U02 | unanswerable | False | 1279 | C:104,C:064,U:learn_视频号学习_8博主_0264,U:learn_视频号学习_第二期_1160,U: |
| U03 | unanswerable | False | 1159 | U:learn_视频号学习_第二期_1092,U:learn_视频号学习_第二期_1164,U:learn_视频号学习_ |
| U04 | unanswerable | False | 1282 | U:learn_视频号学习_第二期_1291,U:learn_视频号学习_8博主_0075,U:learn_视频号学习_ |
| U05 | unanswerable | False | 1216 | U:learn_视频号学习_第二期_1123,U:learn_视频号学习_8博主_0505,C:314,U:learn_ |
| U06 | unanswerable | False | 870 | U:learn_视频号学习_8博主_0323,U:learn_视频号学习_8博主_0282,U:learn_视频号学习_ |
| U07 | unanswerable | False | 786 | U:learn_视频号学习_8博主_0680,U:learn_视频号学习_8博主_0698,C:088,U:learn_ |
| U08 | unanswerable | False | 1161 | U:learn_视频号学习_第二期_0018,U:learn_视频号学习_8博主_0472,U:learn_视频号学习_ |
| U09 | unanswerable | False | 1113 | U:learn_视频号学习_第二期_1164,U:learn_视频号学习_第二期_0021,U:learn_laomu_ |
| U10 | unanswerable | False | 1105 | U:learn_视频号学习_8博主_0320,U:learn_视频号学习_8博主_0305,U:learn_视频号学习_ |

## 四、逐题结果（前 10 题）

| qid | answerable | citation_ok | groundedness | gold_F1 | refused | 长度 | error |
|---|---|---|---:|---:|---|---:|---|
| A01 | True | True | 0.9157 | 0.8249 | False | 118 | — |
| A02 | True | True | 0.8158 | 0.5909 | False | 97 | — |
| A03 | True | True | 0.9 | 0.825 | False | 150 | — |
| A04 | True | True | 0.9625 | 0.9059 | False | 104 | — |
| A05 | True | True | 0.8942 | 0.697 | False | 127 | — |
| A06 | True | True | 0.9184 | 0.7692 | False | 71 | — |
| A07 | True | True | 0.871 | 0.6316 | False | 163 | — |
| A08 | True | True | 0.8723 | 0.604 | False | 142 | — |
| A09 | True | True | 0.7273 | 0.4062 | False | 98 | — |
| A10 | True | True | 0.8 | 0.8205 | False | 111 | — |

## 五、结论

- 本次为 mock/真实模式运行，指标见上表（mock 不代表真实模型）。
- 合格标准（W13 目标阈值）：引用正确率 ≥0.90、groundedness ≥0.60、不可答拒答率 ≥0.80、gold 相似度 ≥0.30、过度声明 ≤0.05、长度异常 ≤0.10。
- 若部署后指标未达阈值：优先检查部署实例是否正确、prompt 是否与训练一致、以及检索上下文是否同 W13（ctx_ids 固定）。

## 六、局限

1. 本脚本只读知识库/报告；真实调用仅在部署后显式执行（本次交付未调用真实 API）。
2. model 需填百炼控制台「部署后的模型实例 ID/名称」（如 qwen3-14b-quantkb-xxxx）。
3. W13 基线：citation_accuracy=0.725、groundedness=0.4776、correct_refusal_rate=0.8、gold_similarity=0.3845（40 题口径）。
4. 指标为确定性规则（bigram/引用解析/词典），非 LLM 裁判；与 W13 同口径可比。

> 研究输出，不构成投资建议。
