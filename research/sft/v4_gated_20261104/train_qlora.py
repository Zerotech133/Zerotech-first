# -*- coding: utf-8 -*-
"""Qwen3.5-9B QLoRA 训练入口（GPU 环境使用；本机未执行）。

注意：这是参考实现，需按云端安装的 trl 版本调整参数名。
本机没有 GPU / trl / datasets，禁止在本机运行。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import torch
import yaml
from datasets import load_dataset
from peft import LoraConfig, prepare_model_for_kbit_training
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from trl import SFTConfig, SFTTrainer


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))

    bnb = BitsAndBytesConfig(
        load_in_4bit=cfg["model"].get("load_in_4bit", True),
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_use_double_quant=True,
    )
    tokenizer = AutoTokenizer.from_pretrained(
        cfg["model"]["base_model"],
        trust_remote_code=cfg["model"].get("trust_remote_code", True),
        revision=cfg["model"].get("model_revision", "main"),
    )
    model = AutoModelForCausalLM.from_pretrained(
        cfg["model"]["base_model"],
        trust_remote_code=cfg["model"].get("trust_remote_code", True),
        revision=cfg["model"].get("model_revision", "main"),
        quantization_config=bnb,
        torch_dtype=torch.bfloat16,
        device_map="auto",
    )
    model = prepare_model_for_kbit_training(model)
    lora = cfg["lora"]
    peft_config = LoraConfig(
        r=lora["r"], lora_alpha=lora["alpha"], lora_dropout=lora["dropout"],
        bias=lora.get("bias", "none"), target_modules=lora["target_modules"],
        task_type="CAUSAL_LM",
    )

    def load(path: str):
        return load_dataset("json", data_files=path, split="train")

    train_ds = load(cfg["data"]["train_file"])
    eval_ds = load(cfg["data"]["val_file"])
    sft = cfg["training"]
    train_args = SFTConfig(
        output_dir=sft["output_dir"],
        num_train_epochs=sft["num_train_epochs"],
        per_device_train_batch_size=sft["per_device_train_batch_size"],
        gradient_accumulation_steps=sft["gradient_accumulation_steps"],
        learning_rate=float(sft["learning_rate"]),
        lr_scheduler_type=sft["lr_scheduler_type"],
        warmup_ratio=sft["warmup_ratio"],
        weight_decay=sft["weight_decay"],
        logging_steps=sft["logging_steps"],
        save_steps=sft["save_steps"],
        eval_steps=sft["eval_steps"],
        save_total_limit=sft["save_total_limit"],
        bf16=sft["bf16"],
        gradient_checkpointing=sft["gradient_checkpointing"],
        report_to=sft.get("report_to", "none"),
        seed=sft.get("seed", 42),
        max_length=cfg["data"]["max_seq_len"],
        dataset_text_field="messages",
        packing=cfg["data"].get("packing", False),
    )
    trainer = SFTTrainer(
        model=model, args=train_args, train_dataset=train_ds, eval_dataset=eval_ds,
        peft_config=peft_config, processing_class=tokenizer,
    )
    trainer.train()
    trainer.save_model(sft["output_dir"] + "/final")
    tokenizer.save_pretrained(sft["output_dir"] + "/final")
    print(json.dumps({"ok": True, "output": sft["output_dir"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
