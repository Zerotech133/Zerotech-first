# -*- coding: utf-8 -*-
"""离线签名器（人类专用；Agent 不得调用）。

背景（2026-10-26 研究实证）：principal 私钥从未生成、approval_gate 生产零装配，
治理链签字从未跑过；approval_gate 的 payload（6 字段、TTL 300s）与本仓
signature_guard 的 payload（7 字段含 kind、TTL 86400s、无 nonce 账本）**口径不同**，
直接把旧签名叫 Z 链签名会验不过。本工具只做一件事：按 signature_guard 的规范
payload 口径，用**本人私钥**离线签名，产出 {scheme,sig,ts,nonce} JSON。

纪律（与 approval_gate 同源）：
  · 只签名、不验签、不生成密钥、不落库、不打印私钥/口令；
  · 必须显式 `--i-am-human`；检测到 Agent 环境变量（AGENT_NAME/DEVTOOL_AGENT）直接拒绝；
  · 签名人自行保管私钥；本工具只接收私钥路径与口令（文件或环境变量）。

用法：
  python tools/sign_approval.py --kind experience_write \
      --payload-file _vault/sign_requests/20261026/EXP-SUPERVISION-001.payload.json \
      --private-key _vault/keys/principal_private.pem \
      --passphrase-env DSH_PRINCIPAL_PASSPHRASE --i-am-human

  （或 --passphrase-file C:\\secure\\pass.txt；--out 指定输出，默认 <payload>.signature.json）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

_TOOLS = Path(__file__).resolve().parent
_REPO = _TOOLS.parent
if str(_REPO / "agent_platform") not in sys.path:
    sys.path.insert(0, str(_REPO / "agent_platform"))

from engine import signature_guard as SG  # noqa: E402

KINDS = ("experience_write", "experiment_card")
PAYLOAD_FIELDS = ("kind", "item_id", "fingerprint", "action", "ts", "nonce")


def _load_private_key(path: str, passphrase: bytes):
    from cryptography.hazmat.primitives import serialization
    data = Path(path).read_bytes()
    return serialization.load_pem_private_key(data, passphrase or None)


def _read_passphrase(args) -> bytes:
    if args.passphrase_file:
        raw = Path(args.passphrase_file).read_bytes()
        return raw.rstrip(b"\r\n")
    env_name = args.passphrase_env
    if env_name and os.environ.get(env_name):
        return os.environ[env_name].encode("utf-8")
    return b""


def sign_payload(payload: dict, private_key) -> dict:
    """按 signature_guard 规范口径签名一条 payload，返回签名对象。"""
    missing = [k for k in PAYLOAD_FIELDS if k not in payload]
    if missing:
        raise ValueError("payload 缺字段: %s" % ", ".join(missing))
    canonical = SG.canonical_payload(
        kind=payload["kind"], item_id=payload["item_id"],
        fingerprint=payload["fingerprint"], action=payload["action"],
        ts=payload["ts"], nonce=payload["nonce"])
    return {
        "scheme": SG.SIG_SCHEME,
        "sig": private_key.sign(canonical).hex(),
        "ts": float(payload["ts"]),
        "nonce": str(payload["nonce"]),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="离线签名器（人类专用；Agent 不得调用）")
    ap.add_argument("--kind", required=True, choices=KINDS)
    ap.add_argument("--payload-file", required=True)
    ap.add_argument("--private-key", required=True)
    ap.add_argument("--passphrase-file", default="")
    ap.add_argument("--passphrase-env", default="DSH_PRINCIPAL_PASSPHRASE")
    ap.add_argument("--out", default="", help="输出签名 JSON（默认 <payload>.signature.json）")
    ap.add_argument("--public-key", default="",
                    help="可选：签名后本地自检用的公钥 hex 文件")
    ap.add_argument("--i-am-human", action="store_true",
                    help="人类确认（必须显式给出；Agent 会话不得给出）")
    args = ap.parse_args(argv)

    if os.environ.get("AGENT_NAME") or os.environ.get("DEVTOOL_AGENT"):
        print("[REFUSE] 检测到 Agent 环境变量（AGENT_NAME/DEVTOOL_AGENT）。"
              "本工具仅限本人执行。", file=sys.stderr)
        return 2
    if not args.i_am_human:
        print("[REFUSE] 需显式 --i-am-human 确认；签名动作只能由本人执行。",
              file=sys.stderr)
        return 2

    payload = json.loads(Path(args.payload_file).read_text(encoding="utf-8"))
    if payload.get("kind") != args.kind:
        print("[FAIL] payload.kind=%r 与 --kind=%r 不一致"
              % (payload.get("kind"), args.kind), file=sys.stderr)
        return 1

    try:
        key = _load_private_key(args.private_key, _read_passphrase(args))
        sig = sign_payload(payload, key)
    except (OSError, ValueError, TypeError) as exc:
        print("[FAIL] 签名失败：%s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 1

    if args.public_key:
        try:
            verifier = SG.Verifier(Path(args.public_key).read_text(encoding="utf-8").strip())
            ok, reason = verifier.verify(
                kind=payload["kind"], item_id=payload["item_id"],
                fingerprint=payload["fingerprint"], action=payload["action"],
                ts=sig["ts"], nonce=sig["nonce"], sig_hex=sig["sig"],
                now=time.time())
            if not ok:
                print("[FAIL] 自检失败：%s" % reason, file=sys.stderr)
                return 1
            print("[OK] 自检通过：signature verified")
        except (OSError, ValueError) as exc:
            print("[FAIL] 自检失败：%s" % exc, file=sys.stderr)
            return 1

    out = Path(args.out) if args.out else Path(str(args.payload_file) + ".signature.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(json.dumps(sig, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, out)
    print(json.dumps({"ok": True, "signature_file": str(out),
                      "kind": payload["kind"], "item_id": payload["item_id"],
                      "nonce": sig["nonce"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
