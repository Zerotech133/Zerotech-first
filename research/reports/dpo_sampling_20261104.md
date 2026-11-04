# DPO 拒绝采样偏好对生成（W20 · 2026-11-04）

> 模式：**mock**（未调用真实 API：仅计划/管道验证）
> 模型端：`mock` · model=`mock-synthetic` · base_url=`https://dashscope.aliyuncs.com/compatible-mode/v1` · key_source=未找到 key
> 问题集：W13 answer_eval 30 道可答题（C:\项目\老田的AI助手\research\reports\answer_eval_20261104.json）

## 一、方法

1. 每题采样 k=8 个候选（temperature=0.7）；
2. 用 W13 确定性指标逐候选打分：引用正确/groundedness/gold F1/无过度声明，组合分 = 0.45*引用+0.30*grounded+0.15*gold+0.10*无过度声明；
3. 选 top1 为 chosen、bottom1 为 rejected；过滤：chosen 组合分≥0.55 且引用正确/不拒答/无过度声明；rejected 组合分<0.5；分差≥0.05；
4. 输出 v2 兼容 JSONL（prompt/chosen/rejected/source/category/concept/meta+metrics）。

## 二、条数与分布

| 项 | 值 |
|---|---:|
| 可用偏好对 | **28** / 30 题（覆盖 93.33%） |
| 候选总数 / 失败采样 | 240 / 0 （成功率 1.0） |
| chosen 组合分均值 | 0.9312 |
| rejected 组合分均值 | 0.0191 |
| 分差 mean/min/max | 0.9121 / 0.8131 / 0.9636 |
| chosen 引用正确率 / 过度声明率 | 1.0 / 0.0 |
| rejected 过度声明率 | 1.0 |
| 跳过原因 | {"chosen_not_eligible": 2} |

## 三、部署后命令

```powershell
# dry_run
python tools/finetune/gen_dpo_pairs.py --dry-run
# mock
python tools/finetune/gen_dpo_pairs.py --mock --write
# bailian_after_deploy
python tools/finetune/gen_dpo_pairs.py --model <百炼部署实例ID> --k 8 --write
# local_ollama
python tools/finetune/gen_dpo_pairs.py --local --k 8 --write
# limit_test
python tools/finetune/gen_dpo_pairs.py --dry-run --limit 3 --k 4
```

## 四、阈值

- k=8、temperature=0.7；chosen 组合分≥0.55、rejected<0.5、分差≥0.05；
- 组合分权重：引用 0.45 / groundedness 0.30 / gold F1 0.15 / 无过度声明 0.10；
- 候选文件：`research/finetune/dpo/quant_kb_dpo_v3_candidates.jsonl`（字段与 DPO v2 兼容）。

## 五、局限

1. 问题集=W13 answer_eval 的 30 道可答题（问题/gold/上下文 ctx_ids 复用），可选 --heldout 扩概念题；
2. 评分=W13 确定性指标组合分 0.45*引用正确+0.30*groundedness+0.15*gold F1+0.10*无过度声明；
3. 阈值：chosen 组合分≥0.55 且引用正确/不拒答/无过度声明；rejected 组合分<0.5；top-bottom 分差≥0.05；k=8，temperature=0.7；
4. 只读知识库/报告，不写生产库；不 commit（仅写 candidates.jsonl 与报告 2 个交付文件）。
5. 当前为 mock：候选为合成答案，仅验证管道与筛选逻辑；候选文件不得直接用于训练。

> 研究输出，不构成投资建议。
