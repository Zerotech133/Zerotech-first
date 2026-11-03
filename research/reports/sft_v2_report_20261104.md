# SFT v2 数据构建报告（2026-11-04）

> v1 2545+133｜refusal +182｜citation +219｜**合计 3079（train 2905 / val 174）**

| 类 | 条数 |
|---|---|
| v1_existing | 2678 |
| Q_citation | 219 |
| R_refusal | 182 |

## 校验
- 重复丢弃 0；格式不合格 0
- 新增样本 90/10: train 360 / val 41
- 输出: `research/finetune/sft/quant_kb_sft_v2_{train,val}.jsonl`
- held-out 泄漏过滤丢弃 0 条（概念级保守）
- 注册: `dataset_info.json` → `quant_kb_sft_v2`；配置: `configs/*_v2.yaml`

> 训练仍需 GPU（V100/4090），执行包见 `deliverables/2026-11-04_微调D1D2执行包.md`。