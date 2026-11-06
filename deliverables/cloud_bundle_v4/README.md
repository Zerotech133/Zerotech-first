# 微调云训练上传包 v4（SFT v4：引用上下文修正）

> 原因：百炼数据集导入异常（v2 已知可用数据也失败），改用 GPU 云直接训练。
> 数据：quant_kb_sft_v4（**3,769 条**：v1 2,678 + 拒答 182 + 引用 219【已注入上下文】+ 通用 960）
> 训练：Qwen2.5-14B-Instruct QLoRA（LoRA rank 64），3 epoch

## 一、AutoDL 快速开始（推荐 4090 24G）
1. 注册/登录 AutoDL → 租用 **RTX 4090 24G**（镜像选 PyTorch 2.x / CUDA 12.x）；
2. 上传本包并解压：`unzip 2026-11-04_微调云训练上传包_v4.zip -d /root/autodl-tmp/quantkb`
3. 安装 LLaMA-Factory（若镜像未带）：
   `git clone https://github.com/hiyouga/LLaMA-Factory.git && cd LLaMA-Factory && pip install -e ".[torch,metrics,bitsandbytes]"`
4. 下模型（ModelScope 国内快）：
   `modelscope download --model Qwen/Qwen2.5-14B-Instruct --local_dir /root/autodl-tmp/models/Qwen2.5-14B-Instruct`
5. 训练：
   ```bash
   cd /root/autodl-tmp/quantkb
   bash run_sft_4090.sh /root/autodl-tmp/models/Qwen2.5-14B-Instruct
   ```
   （脚本内已含 `--dataset_dir .`、输出 `saves/qwen25-14b-qlora-sft-v4`）

## 二、A100 40G/80G（更快）
```bash
bash run_sft_a100.sh /root/autodl-tmp/models/Qwen2.5-14B-Instruct
```

## 三、成本与时间（参考）
| 卡型 | 单价（约） | 14B QLoRA 3 epoch | 费用 |
|---|---:|---:|---:|
| 4090 24G | ¥1.5–2.7/h（spot/按量） | 4–6h | **¥10–25** |
| A100 40G | ¥2.5–3.5/h | 2–3h | ¥8–15 |
| 百炼托管（对照） | 按 token | 51min | ¥22–43 |

## 四、训练后：导出 adapter 并评测
1. adapter 在 `saves/qwen25-14b-qlora-sft-v4/`（LoRA 权重，几百 MB）；
2. 复制回本机 `C:\项目\老田的AI助手\research\finetune\adapters\sft_v4\`（或直接留在云上推理）；
3. 本地/云上起 vLLM（adapter 可动态加载）后用 `tools/finetune/bailian_eval.py` 的 prompt 口径或 `answer_eval` 40 题复测：
   - 引用正确率（目标 ≥0.90）
   - groundedness（≥0.60）
   - 不可答拒答率（≥0.80）
4. 结果写 `research/reports/sft_v4_eval_20261104.md`。

## 五、数据完整性
- 引用样本 user 含 `资料：[1..5] + 问题`，assistant 含 `[n]` 引用；
- 与百炼 v2 同源，仅修复引用上下文丢失问题；
- 校验：3,769 条；citation 219 / refusal 182 / F 通用 960 / v1 2,678。

## 六、文件清单
- `sft/quant_kb_sft_v4_{train,val}.jsonl`
- `sft/smoke_14b_v4.jsonl`（30 条冒烟）
- `configs/sft_14b_4090_v4.yaml` / `configs/sft_14b_a100_v4.yaml`
- `dataset_info.json`
- `run_sft_4090.sh` / `run_sft_a100.sh`
- `README.md` / `MANIFEST.json`
