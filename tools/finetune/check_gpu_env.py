# -*- coding: utf-8 -*-
"""训练环境自检与推荐配置（D1/D2 前置）。

用法：
  python tools/finetune/check_gpu_env.py            # 人类可读
  python tools/finetune/check_gpu_env.py --json     # JSON

判据：
  - 有 NVIDIA GPU 且显存 ≥24G → 推荐 Qwen2.5-14B QLoRA（双卡可 DDP）
  - 单卡 16-23G → 推荐 Qwen2.5-7B QLoRA（14B 需极限参数）
  - 无 NVIDIA GPU / CPU-only → 建议云 4090 或 V100；本机仅数据/推理
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
REPO = Path(__file__).resolve().parents[2]


def nvidia_smi() -> list[dict]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    try:
        out = subprocess.run([exe, "--query-gpu=name,memory.total,driver_version",
                              "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=20)
        gpus = []
        for line in out.stdout.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 3:
                gpus.append({"name": parts[0], "memory_gb": round(float(parts[1]) / 1024, 1),
                             "driver": parts[2]})
        return gpus
    except Exception:  # noqa: BLE001
        return []


def torch_status() -> dict:
    try:
        import torch  # noqa: F401
        return {"installed": True, "version": torch.__version__,
                "cuda": bool(torch.cuda.is_available()),
                "device_count": int(torch.cuda.device_count()) if torch.cuda.is_available() else 0}
    except Exception as exc:  # noqa: BLE001
        return {"installed": False, "error": type(exc).__name__}


def recommend(gpus: list[dict]) -> dict:
    if not gpus:
        return {"model": "云端 GPU（4090/V100）", "method": "QLoRA（需 CUDA）",
                "reason": "无 NVIDIA GPU：QLoRA 需 CUDA，建议云 4090/V100 或本机只做数据/推理",
                "configs": ["research/finetune/configs/sft_14b_v100.yaml",
                            "research/finetune/configs/sft_14b_2x4090.yaml",
                            "research/finetune/configs/sft_7b_v100.yaml"]}
    total = sum(g["memory_gb"] for g in gpus)
    n = len(gpus)
    if total >= 40:
        return {"model": "Qwen2.5-14B-Instruct", "method": "QLoRA (4bit) + DDP/ZeRO-2",
                "configs": ["research/finetune/configs/sft_14b_2x4090.yaml",
                            "research/finetune/configs/dpo_14b_2x4090.yaml"],
                "reason": f"{n} 卡共 {total:.0f}GB，14B 双卡 DDP 最快；可选 32B QLoRA(stretch)"}
    if min(g["memory_gb"] for g in gpus) >= 30:
        return {"model": "Qwen2.5-14B-Instruct", "method": "QLoRA (4bit, V100 FP16)",
                "configs": ["research/finetune/configs/sft_14b_v100.yaml",
                            "research/finetune/configs/dpo_14b_v100.yaml"],
                "reason": "单卡 ≥30GB（V100 32G），14B QLoRA 可跑（FP16/sdpa）"}
    if min(g["memory_gb"] for g in gpus) >= 20:
        return {"model": "Qwen2.5-14B-Instruct", "method": "QLoRA (4bit, 单 4090)",
                "configs": ["research/finetune/configs/sft_14b.yaml",
                            "research/finetune/configs/dpo_14b.yaml"],
                "reason": "单卡 24G，14B QLoRA（seq 2048 batch1×ga16）"}
    return {"model": "Qwen2.5-7B-Instruct", "method": "QLoRA (4bit)",
            "configs": ["research/finetune/configs/sft_7b_v100.yaml"],
            "reason": "显存 16-23G：7B QLoRA 稳妥；14B 需 seq1024 极限"}


def main() -> int:
    gpus = nvidia_smi()
    rec = recommend(gpus)
    payload = {"generated_at": __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds"),
               "gpus": gpus, "torch": torch_status(), "recommendation": rec,
               "datasets": {
                   "sft": "research/finetune/sft/quant_kb_sft_v1_train.jsonl",
                   "val": "research/finetune/sft/quant_kb_sft_v1_val.jsonl",
                   "heldout": "research/finetune/heldout/quant_kb_heldout_105.jsonl",
                   "dpo": "research/finetune/dpo/quant_kb_dpo_v1.jsonl"},
               "dataset_info": "research/finetune/dataset_info.json"}
    if "--json" in sys.argv:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
        return 0
    print(f"GPU: {gpus or '未检测到 NVIDIA GPU'}")
    print(f"PyTorch: {payload['torch']}")
    print(f"推荐: {rec.get('model')} / {rec.get('method')}")
    print(f"理由: {rec.get('reason')}")
    print("配置: " + (", ".join(rec.get("configs") or []) or "—"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
