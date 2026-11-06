# 微调云训练上传包 v1（2026-11-04T04:45:18+08:00）

## 内容
- `sft/quant_kb_sft_v2_train.jsonl`：SFT v2 训练 2905 条（v1 2678 + 拒答 182 + 引用 219，已过滤 held-out）
- `sft/quant_kb_sft_v2_val.jsonl`：验证集
- `sft/smoke_7b.jsonl`：7B 冒烟 300 条
- `dpo/quant_kb_dpo_v2.jsonl`：DPO 42 对（引用/拒答偏好）
- `dataset_info.json`：LLaMA-Factory 注册（sft_v2/smoke/dpo_v2）
- `configs/`：14B QLoRA / 7B 冒烟 / 14B DPO 配置
- `run_*.sh`：一键脚本

## 快速开始（AutoDL 4090）
```bash
unzip 2026-11-04_微调云训练上传包_v1.zip && cd 微调云训练上传包
pip install -U "llamafactory==0.9.*" modelscope
modelscope download --model Qwen/Qwen2.5-14B-Instruct --local_dir /root/autodl-tmp/models/Qwen2.5-14B-Instruct
bash run_smoke_7b.sh        # 可选，先验流程（需 7B 模型）
bash run_sft_14b.sh         # D2 正式 SFT
bash run_dpo_14b.sh         # DPO（SFT 验收后）
```

## 验收（训练后本地/云端）
`tools/kb_corpus/answer_eval.py` 的 40 题：引用正确率≥0.90、groundedness≥0.60、不可答拒答率≥0.80。

> 数据为公开学习资料与合成样本，不含个人敏感信息；训练完立即关机。
