# -*- coding: utf-8 -*-
"""构建微调云训练上传包 v4（SFT v4 修正引用上下文 + 4090/A100 配置 + 一键脚本）。

用途：绕开百炼数据集导入异常，在 AutoDL/PAI 等 GPU 云上直接训练，
训练后导出 adapter，再用 40 题评测复核对齐。
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
REPO = Path(__file__).resolve().parents[2]
FT = REPO / "research" / "finetune"
OUT = REPO / "deliverables"
BUNDLE = OUT / "cloud_bundle_v4"
ZIP = OUT / "2026-11-04_微调云训练上传包_v4.zip"
NOW = datetime.now().astimezone().isoformat(timespec="seconds")

CONFIG_4090 = """### Qwen2.5-14B-Instruct QLoRA SFT v4 · 单卡 4090 24G（4bit/bf16）
### 用途：引用上下文修正数据（quant_kb_sft_v4）
stage: sft
do_train: true
finetuning_type: lora
lora_rank: 64
lora_alpha: 128
lora_dropout: 0.05
lora_target: all

dataset: quant_kb_sft_v4
dataset_dir: .
template: qwen
cutoff_len: 2048
max_samples: 100000
overwrite_cache: true
preprocessing_num_workers: 8

per_device_train_batch_size: 1
gradient_accumulation_steps: 16
learning_rate: 1.0e-4
num_train_epochs: 3.0
lr_scheduler_type: cosine
warmup_ratio: 0.05
logging_steps: 10
save_steps: 200
save_total_limit: 2
bf16: true
gradient_checkpointing: true
flash_attn: fa2
quantization_bit: 4
quantization_method: bnb
"""

CONFIG_A100 = """### Qwen2.5-14B-Instruct QLoRA SFT v4 · A100 40G/80G
stage: sft
do_train: true
finetuning_type: lora
lora_rank: 64
lora_alpha: 128
lora_dropout: 0.05
lora_target: all

dataset: quant_kb_sft_v4
dataset_dir: .
template: qwen
cutoff_len: 2048
max_samples: 100000
overwrite_cache: true
preprocessing_num_workers: 8

per_device_train_batch_size: 4
gradient_accumulation_steps: 4
learning_rate: 1.0e-4
num_train_epochs: 3.0
lr_scheduler_type: cosine
warmup_ratio: 0.05
logging_steps: 10
save_steps: 200
save_total_limit: 2
bf16: true
gradient_checkpointing: true
flash_attn: fa2
quantization_bit: 4
quantization_method: bnb
"""

README = """# 微调云训练上传包 v4（SFT v4：引用上下文修正）

> 原因：百炼数据集导入异常（v2 已知可用数据也失败），改用 GPU 云直接训练。
> 数据：quant_kb_sft_v4（**4,039 条**：v1 2,678 + 拒答 182 + 引用 219【已注入上下文】+ 通用 960）
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
2. 复制回本机 `C:\\项目\\老田的AI助手\\research\\finetune\\adapters\\sft_v4\\`（或直接留在云上推理）；
3. 本地/云上起 vLLM（adapter 可动态加载）后用 `tools/finetune/bailian_eval.py` 的 prompt 口径或 `answer_eval` 40 题复测：
   - 引用正确率（目标 ≥0.90）
   - groundedness（≥0.60）
   - 不可答拒答率（≥0.80）
4. 结果写 `research/reports/sft_v4_eval_20261104.md`。

## 五、数据完整性
- 引用样本 user 含 `资料：[1..5] + 问题`，assistant 含 `[n]` 引用；
- 与百炼 v2 同源，仅修复引用上下文丢失问题；
- 校验：4,039 条；citation 219 / refusal 182 / F 通用 960 / v1 2,678。

## 六、文件清单
- `sft/quant_kb_sft_v4_{train,val}.jsonl`
- `sft/smoke_14b_v4.jsonl`（30 条冒烟）
- `configs/sft_14b_4090_v4.yaml` / `configs/sft_14b_a100_v4.yaml`
- `dataset_info.json`
- `run_sft_4090.sh` / `run_sft_a100.sh`
- `README.md` / `MANIFEST.json`
"""


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    if BUNDLE.exists():
        shutil.rmtree(BUNDLE)
    for d in ("sft", "configs"):
        (BUNDLE / d).mkdir(parents=True, exist_ok=True)
    srcs = (("sft/quant_kb_sft_v4_train.jsonl", "sft/quant_kb_sft_v4_train.jsonl"),
            ("sft/quant_kb_sft_v4_val.jsonl", "sft/quant_kb_sft_v4_val.jsonl"),
            ("dpo/quant_kb_dpo_v2.jsonl", "dpo/quant_kb_dpo_v2.jsonl"))
    for src, dst in srcs:
        sp = FT / src
        if sp.exists():
            dp = BUNDLE / dst
            dp.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(sp, dp)
    rows = (BUNDLE / "sft/quant_kb_sft_v4_train.jsonl").read_text(encoding="utf-8").splitlines()[:30]
    (BUNDLE / "sft/smoke_14b_v4.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")
    info = {"quant_kb_sft_v4": {"file_name": "sft/quant_kb_sft_v4_train.jsonl", "formatting": "sharegpt",
                                "columns": {"messages": "messages"}},
            "quant_kb_sft_smoke14b": {"file_name": "sft/smoke_14b_v4.jsonl", "formatting": "sharegpt",
                                      "columns": {"messages": "messages"}}}
    if (FT / "dpo/quant_kb_dpo_v2.jsonl").exists():
        info["quant_kb_dpo_v2"] = {"file_name": "dpo/quant_kb_dpo_v2.jsonl", "formatting": "sharegpt",
                                   "ranking": True, "columns": {"messages": "messages", "chosen": "chosen", "rejected": "rejected"}}
    (BUNDLE / "dataset_info.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    (BUNDLE / "configs/sft_14b_4090_v4.yaml").write_text(CONFIG_4090, encoding="utf-8")
    (BUNDLE / "configs/sft_14b_a100_v4.yaml").write_text(CONFIG_A100, encoding="utf-8")
    (BUNDLE / "README.md").write_text(README, encoding="utf-8")
    (BUNDLE / "run_sft_4090.sh").write_text(
        "#!/usr/bin/env bash\nset -e\nMODEL_DIR=${1:-/root/autodl-tmp/models/Qwen2.5-14B-Instruct}\n"
        "llamafactory-cli train configs/sft_14b_4090_v4.yaml --model_name_or_path \"$MODEL_DIR\" "
        "--dataset_dir . --output_dir saves/qwen25-14b-qlora-sft-v4\n", encoding="utf-8")
    (BUNDLE / "run_sft_a100.sh").write_text(
        "#!/usr/bin/env bash\nset -e\nMODEL_DIR=${1:-/root/autodl-tmp/models/Qwen2.5-14B-Instruct}\n"
        "llamafactory-cli train configs/sft_14b_a100_v4.yaml --model_name_or_path \"$MODEL_DIR\" "
        "--dataset_dir . --output_dir saves/qwen25-14b-qlora-sft-v4\n", encoding="utf-8")
    files = []
    for p in sorted(BUNDLE.rglob("*")):
        if p.is_file() and p.name != "MANIFEST.json":
            files.append({"path": str(p.relative_to(BUNDLE)), "size": p.stat().st_size, "sha256": sha256(p)})
    manifest = {"schema": "cloud_bundle.v4", "generated_at": NOW, "dataset": "quant_kb_sft_v4",
                "total": 4039, "files": files}
    (BUNDLE / "MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(BUNDLE.rglob("*")):
            if p.is_file():
                z.write(p, arcname=f"cloud_bundle_v4/{p.relative_to(BUNDLE)}")
    zsha = sha256(ZIP)
    print(json.dumps({"zip": str(ZIP), "size": ZIP.stat().st_size, "sha256": zsha,
                      "files": len(files), "total": 4039}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
