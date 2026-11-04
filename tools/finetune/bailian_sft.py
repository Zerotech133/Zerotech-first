# -*- coding: utf-8 -*-
"""阿里云百炼（Model Studio）SFT 微调自动化脚本（file 上传 → 建任务 → 轮询）。

前置：DASHSCOPE_API_KEY（新加坡区 API；北京区请在控制台建任务），可放在
  C:/项目/_vault/secrets/dashscope_api_key.txt 或环境变量。

用法：
  python tools/finetune/bailian_sft.py prepare                     # 从 SFT v2 生成 ChatML jsonl
  python tools/finetune/bailian_sft.py upload --file <jsonl>      # 上传文件 → file_id
  python tools/finetune/bailian_sft.py create --file-id <id> --model qwen3-14b \
        --epochs 3 --batch 16 --max-length 2048 [--dry-run]       # 创建微调任务
  python tools/finetune/bailian_sft.py status --job-id <id>
  python tools/finetune/bailian_sft.py list
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding="utf-8")
REPO = Path(__file__).resolve().parents[2]
FT = REPO / "research/finetune"
OUT = REPO / "deliverables/bailian"
KEY_FILE = Path(r"C:/项目/_vault/secrets/dashscope_api_key.txt")
# 新加坡区（API 建任务文档要求）；北京区控制台建任务请用控制台
DEFAULT_BASE = "https://dashscope-intl.aliyuncs.com/api/v1"
DEFAULT_COMPAT = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"


def api_key() -> str:
    k = os.environ.get("DASHSCOPE_API_KEY") or ""
    if not k and KEY_FILE.exists():
        k = KEY_FILE.read_text(encoding="utf-8").strip()
    return k


def prepare() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for src, dst in ((FT / "sft/quant_kb_sft_v2_train.jsonl", "sft_v2_train_chatml.jsonl"),
                     (FT / "sft/quant_kb_sft_v2_val.jsonl", "sft_v2_val_chatml.jsonl")):
        rows = [json.loads(x) for x in src.read_text(encoding="utf-8").splitlines() if x.strip()]
        with (OUT / dst).open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps({"messages": r["messages"]}, ensure_ascii=False) + "\n")
        print(dst, len(rows))
    return 0


def _headers() -> dict:
    k = api_key()
    if not k:
        raise SystemExit("缺少 DASHSCOPE_API_KEY（环境变量或 C:/项目/_vault/secrets/dashscope_api_key.txt）")
    return {"Authorization": f"Bearer {k}", "Content-Type": "application/json"}


def upload(file: Path) -> int:
    k = api_key()
    if not k:
        raise SystemExit("缺少 DASHSCOPE_API_KEY")
    base = os.environ.get("DASHSCOPE_COMPAT_BASE", DEFAULT_COMPAT)
    try:
        from openai import OpenAI
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"需要 openai 包: {exc}")
    client = OpenAI(api_key=k, base_url=base)
    fo = client.files.create(file=file, purpose="fine-tune")
    print(json.dumps({"file_id": getattr(fo, "id", ""), "bytes": getattr(fo, "bytes", None),
                      "filename": getattr(fo, "filename", None)}, ensure_ascii=False))
    return 0


def create(file_id: str, model: str, epochs: int, batch: int, max_length: int, dry_run: bool) -> int:
    base = os.environ.get("DASHSCOPE_API_BASE", DEFAULT_BASE)
    body = {
        "model": model,
        "training_datasets": [{"data_source_type": "file_id", "file_id": file_id}],
        "hyper_parameters": {"n_epochs": epochs, "batch_size": batch, "max_length": max_length,
                             "split": 0.9, "learning_rate": "1.6e-5"},
        "training_type": "efficient_sft",
        "finetuned_output_suffix": "quantkb",
    }
    print("[request]", json.dumps(body, ensure_ascii=False))
    if dry_run:
        print("dry-run：未调用 API")
        return 0
    r = requests.post(f"{base}/fine-tunes", headers=_headers(), json=body, timeout=120)
    print("HTTP", r.status_code, r.text[:1000])
    return 0 if r.ok else 1


def status(job_id: str) -> int:
    base = os.environ.get("DASHSCOPE_API_BASE", DEFAULT_BASE)
    r = requests.get(f"{base}/fine-tunes/{job_id}", headers=_headers(), timeout=60)
    print("HTTP", r.status_code, r.text[:1500])
    return 0 if r.ok else 1


def list_jobs() -> int:
    base = os.environ.get("DASHSCOPE_API_BASE", DEFAULT_BASE)
    r = requests.get(f"{base}/fine-tunes", headers=_headers(), timeout=60)
    print("HTTP", r.status_code, r.text[:2000])
    return 0 if r.ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prepare")
    up = sub.add_parser("upload"); up.add_argument("--file", required=True)
    cr = sub.add_parser("create"); cr.add_argument("--file-id", required=True)
    cr.add_argument("--model", default="qwen3-14b"); cr.add_argument("--epochs", type=int, default=3)
    cr.add_argument("--batch", type=int, default=16); cr.add_argument("--max-length", type=int, default=2048)
    cr.add_argument("--dry-run", action="store_true")
    st = sub.add_parser("status"); st.add_argument("--job-id", required=True)
    sub.add_parser("list")
    a = ap.parse_args()
    if a.cmd == "prepare":
        return prepare()
    if a.cmd == "upload":
        return upload(Path(a.file))
    if a.cmd == "create":
        return create(a.file_id, a.model, a.epochs, a.batch, a.max_length, a.dry_run)
    if a.cmd == "status":
        return status(a.job_id)
    return list_jobs()


if __name__ == "__main__":
    sys.exit(main())
