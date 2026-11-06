#!/usr/bin/env bash
set -euo pipefail
MODEL_DIR=${MODEL_DIR:-/root/autodl-tmp/models/Qwen2.5-14B-Instruct}
ADAPTER_DIR=${ADAPTER_DIR:-saves/qwen25-14b-qlora-sft-v2}
llamafactory-cli train configs/dpo_14b_v2.yaml --model_name_or_path "$MODEL_DIR" --adapter_name_or_path "$ADAPTER_DIR" --dataset_dir . --output_dir saves/qwen25-14b-qlora-dpo-v2
