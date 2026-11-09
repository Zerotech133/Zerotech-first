#!/usr/bin/env bash
set -euo pipefail
MODEL_DIR=${MODEL_DIR7:-/root/autodl-tmp/models/Qwen2.5-7B-Instruct}
llamafactory-cli train configs/sft_7b_4090_v2.yaml --model_name_or_path "$MODEL_DIR" --dataset_dir . --output_dir saves/smoke-7b-4090
