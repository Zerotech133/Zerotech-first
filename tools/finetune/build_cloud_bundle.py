# -*- coding: utf-8 -*-
"""构建微调云训练上传包（SFT v2 / DPO v2 / 配置 / 一键脚本）。"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
REPO = Path(r"C:/项目/老田的AI助手")
FT = REPO / "research/finetune"
OUT = REPO / "deliverables"
BUNDLE = OUT / "cloud_bundle"
ZIP = OUT / "2026-11-04_微调云训练上传包_v1.zip"

if BUNDLE.exists():
    shutil.rmtree(BUNDLE)
for d in ("sft", "dpo", "configs"):
    (BUNDLE / d).mkdir(parents=True, exist_ok=True)

# 数据
for src, dst in ((FT / "sft/quant_kb_sft_v2_train.jsonl", "sft/quant_kb_sft_v2_train.jsonl"),
                 (FT / "sft/quant_kb_sft_v2_val.jsonl", "sft/quant_kb_sft_v2_val.jsonl"),
                 (FT / "dpo/quant_kb_dpo_v2.jsonl", "dpo/quant_kb_dpo_v2.jsonl")):
    shutil.copy2(src, BUNDLE / dst)
# 7B 冒烟子集（300 条）
rows = (BUNDLE / "sft/quant_kb_sft_v2_train.jsonl").read_text(encoding="utf-8").splitlines()[:300]
(BUNDLE / "sft/smoke_7b.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")
# dataset_info
info = json.loads((FT / "dataset_info.json").read_text(encoding="utf-8"))
info["quant_kb_sft_smoke"] = {"file_name": "sft/smoke_7b.jsonl", "formatting": "sharegpt",
                               "columns": {"messages": "messages"}}
(BUNDLE / "dataset_info.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
# 配置
shutil.copy2(FT / "configs/sft_14b_v2.yaml", BUNDLE / "configs/sft_14b_v2.yaml")
shutil.copy2(FT / "configs/dpo_14b_v2.yaml", BUNDLE / "configs/dpo_14b_v2.yaml")
(BUNDLE / "configs/sft_7b_4090_v2.yaml").write_text("""### Qwen2.5-7B-Instruct QLoRA SFT · 4090 24G（bf16/fa2）· 冒烟
model_name_or_path: <Qwen2.5-7B-Instruct 本地路径>
quantization_bit: 4
stage: sft
finetuning_type: lora
lora_target: all
lora_rank: 32
lora_alpha: 64
lora_dropout: 0.05
dataset: quant_kb_sft_smoke
template: qwen
cutoff_len: 1024
train_on_prompt: false
per_device_train_batch_size: 4
gradient_accumulation_steps: 4
learning_rate: 8.0e-5
num_train_epochs: 1.0
lr_scheduler_type: cosine
warmup_steps: 10
bf16: true
fp16: false
gradient_checkpointing: true
flash_attn: fa2
weight_decay: 0.0
max_grad_norm: 1.0
packing: false
logging_steps: 5
output_dir: saves/smoke-7b-4090
seed: 42
""", encoding="utf-8")
# 脚本
shutil.copy2(OUT / "cloud_bundle/run_sft_14b.sh", BUNDLE / "run_sft_14b.sh") if (OUT / "cloud_bundle/run_sft_14b.sh").exists() else None
if not (BUNDLE / "run_sft_14b.sh").exists():
    (BUNDLE / "run_sft_14b.sh").write_text("""#!/usr/bin/env bash
set -euo pipefail
MODEL_DIR=${MODEL_DIR:-/root/autodl-tmp/models/Qwen2.5-14B-Instruct}
llamafactory-cli train configs/sft_14b_v2.yaml --model_name_or_path "$MODEL_DIR" --dataset_dir . --output_dir saves/qwen25-14b-qlora-sft-v2
""", encoding="utf-8")
(BUNDLE / "run_smoke_7b.sh").write_text("""#!/usr/bin/env bash
set -euo pipefail
MODEL_DIR=${MODEL_DIR7:-/root/autodl-tmp/models/Qwen2.5-7B-Instruct}
llamafactory-cli train configs/sft_7b_4090_v2.yaml --model_name_or_path "$MODEL_DIR" --dataset_dir . --output_dir saves/smoke-7b-4090
""", encoding="utf-8")
(BUNDLE / "run_dpo_14b.sh").write_text("""#!/usr/bin/env bash
set -euo pipefail
MODEL_DIR=${MODEL_DIR:-/root/autodl-tmp/models/Qwen2.5-14B-Instruct}
ADAPTER_DIR=${ADAPTER_DIR:-saves/qwen25-14b-qlora-sft-v2}
llamafactory-cli train configs/dpo_14b_v2.yaml --model_name_or_path "$MODEL_DIR" --adapter_name_or_path "$ADAPTER_DIR" --dataset_dir . --output_dir saves/qwen25-14b-qlora-dpo-v2
""", encoding="utf-8")
BD = __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds")
(BUNDLE / "README.md").write_text(f"""# 微调云训练上传包 v1（{BD}）

## 内容
- `sft/quant_kb_sft_v2_train.jsonl`：SFT v2 训练 {sum(1 for _ in (BUNDLE/'sft/quant_kb_sft_v2_train.jsonl').open(encoding='utf-8'))} 条（v1 2678 + 拒答 182 + 引用 219，已过滤 held-out）
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
""", encoding="utf-8")
# zip + manifest
files = sorted(p for p in BUNDLE.rglob("*") if p.is_file())
manifest = {str(p.relative_to(BUNDLE)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
(BUNDLE / "MANIFEST.json").write_text(json.dumps({"generated_at": BD, "files": manifest}, ensure_ascii=False, indent=1), encoding="utf-8")
files = sorted(p for p in BUNDLE.rglob("*") if p.is_file())
with zipfile.ZipFile(ZIP, "w", compression=zipfile.ZIP_DEFLATED) as z:
    for p in files:
        z.write(p, arcname=str(Path("微调云训练上传包") / p.relative_to(BUNDLE)).replace("\\", "/"))
print("zip", ZIP, ZIP.stat().st_size, "files", len(files))
print("sha256", hashlib.sha256(ZIP.read_bytes()).hexdigest())
