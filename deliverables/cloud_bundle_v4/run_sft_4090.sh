#!/usr/bin/env bash
set -e
MODEL_DIR=${1:-/root/autodl-tmp/models/Qwen2.5-14B-Instruct}
llamafactory-cli train configs/sft_14b_4090_v4.yaml --model_name_or_path "$MODEL_DIR" --dataset_dir . --output_dir saves/qwen25-14b-qlora-sft-v4
