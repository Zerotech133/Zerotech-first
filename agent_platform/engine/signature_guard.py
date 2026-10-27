# -*- coding: utf-8 -*-
"""进化链签名守卫（WO-LEGION-P0FIX-20261026 · P0-3，渐进式）。

背景（2026-10-26 三路审计实证）：Z2 `decide()` 与 Z4 `write_entry()` 的签字门
此前只校验 `signed_by.strip()` 非空——**任何字符串都能当签字**，"无签字不结案"
是语法约束而非身份约束。本模块把签字升级为可验证的 Ed25519 签名：

  · 复用仓内既有签准语义（approval_gate.PrincipalVerifier 同源：Ed25519 +
    `-v1` 方案号 + 规范化 payload），但独立成 engine 层模块，避免 engine 反向
    依赖军团骨架仓；
  · **渐进上线**：未配置公钥、未传签名时保留旧行为，但把 `verified=False`
    如实写进结果（不冒充已验签）；`EVOLVE_SIGNATURE_STRICT=1` 时无签名/无公钥
    一律拒绝；
  · 只验签、不签名、不生成密钥（私钥永远在本人侧，与 approval_gate 同纪律）。

环境变量（全部缺省关闭，不破坏既有调用）：
  EVOLVE_PRINCIPAL_PUBKEY_HEX   32 字节 Ed25519 公钥的 hex
  EVOLVE_PRINCIPAL_PUBKEY_FILE  公钥 hex 文件路径（与上者二选一，HEX 优先）
  EVOLVE_SIGNATURE_STRICT       "1" 时强制验签（无签名/无公钥即拒）

payload 规范化口径（签名方须逐字节一致）：
  json.dumps({scheme, kind, item_id, fingerprint, action, ts, nonce},
             ensure_ascii=False, sort_keys=True, separators=(",", ":"))
  以 UTF-8 编码后签名。`ts` 为 Unix 秒（float 保留 6 位小数），`nonce` 为调用方
  生成的一次性字符串（本模块不做重放缓存；重放防护由签名有效期 + 调用方单据号
  承担，见 approval_gate 的 nonce 账本先例）。
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Optional, Tuple

SIG_SCHEME = "ed25519-v1"

ENV_PUBKEY_HEX = "EVOLVE_PRINCIPAL_PUBKEY_HEX"
ENV_PUBKEY_FILE = "EVOLVE_PRINCIPAL_PUBKEY_FILE"
ENV_STRICT = "EVOLVE_SIGNATURE_STRICT"

# 签名有效期（秒）：默认 24h。经验入库可能滞后于实验结案，故放宽于 approval_gate 的
# 短时效（签准类动作是即时授权，经验入库是归档动作）。
DEFAULT_MAX_AGE_S = 86400.0


class SignatureError(ValueError):
    """签名存在但不可信（格式/算法/时效/验签失败）。"""


class SignatureRequired(RuntimeError):
    """严格模式下无可用签名/无公钥 —— 拒绝继续。"""


def canonical_payload(
    *, kind: str, item_id: str, fingerprint: str, action: str, ts: float, nonce: str
) -> bytes:
    """生成待签名 payload 的规范字节串（签名/验签双方唯一口径）。"""
    payload = {
        "scheme": SIG_SCHEME,
        "kind": str(kind),
        "item_id": str(item_id),
        "fingerprint": str(fingerprint),
        "action": str(action),
        "ts": round(float(ts), 6),
        "nonce": str(nonce),
    }
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def fingerprint_of(obj: Any) -> str:
    """内容指纹：sha256(规范 JSON)。用于把签名绑定到具体卡片/经验内容。"""
    raw = json.dumps(
        obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class Verifier:
    """Ed25519 公钥验签器（无私钥、不签名、不生成密钥）。"""

    def __init__(self, public_key_hex: str) -> None:
        if not public_key_hex or not public_key_hex.strip():
            raise SignatureError("公钥为空")
        try:
            public_bytes = bytes.fromhex(public_key_hex.strip())
        except ValueError as exc:
            raise SignatureError("公钥不是合法 hex: %s" % exc) from exc
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import (
                Ed25519PublicKey,
            )

            self._key = Ed25519PublicKey.from_public_bytes(public_bytes)
        except ImportError as exc:  # pragma: no cover - 环境缺 cryptography
            raise SignatureError("缺少 cryptography 依赖，无法验签") from exc
        except Exception as exc:  # 公钥长度/格式非法
            raise SignatureError("公钥非法: %s" % exc) from exc

    def verify(
        self,
        *,
        kind: str,
        item_id: str,
        fingerprint: str,
        action: str,
        ts: float,
        nonce: str,
        sig_hex: str,
        max_age_s: float = DEFAULT_MAX_AGE_S,
        now: Optional[float] = None,
    ) -> Tuple[bool, str]:
        """验签并校验时效。返回 (ok, reason)；异常输入不抛，返回 False+原因。"""
        if not sig_hex:
            return False, "empty signature"
        try:
            sig = bytes.fromhex(str(sig_hex).strip())
        except ValueError:
            return False, "bad signature encoding"
        try:
            ts_f = float(ts)
        except (TypeError, ValueError):
            return False, "bad ts"
        import time

        now_f = float(now) if now is not None else time.time()
        age = abs(now_f - ts_f)
        if age > float(max_age_s):
            return False, "signature expired (age=%.0fs > %.0fs)" % (age, max_age_s)
        payload = canonical_payload(
            kind=kind,
            item_id=item_id,
            fingerprint=fingerprint,
            action=action,
            ts=ts_f,
            nonce=nonce,
        )
        try:
            from cryptography.exceptions import InvalidSignature

            self._key.verify(sig, payload)
        except InvalidSignature:
            return False, "signature invalid"
        except Exception as exc:  # 长度非法等
            return False, "signature invalid (%s)" % type(exc).__name__
        return True, "ok"


def _public_key_hex_from_env() -> Optional[str]:
    raw = (os.environ.get(ENV_PUBKEY_HEX) or "").strip()
    if raw:
        return raw
    path = (os.environ.get(ENV_PUBKEY_FILE) or "").strip()
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


def verifier_from_env() -> Optional[Verifier]:
    """从环境变量装配验签器；未配置返回 None（调用方按渐进模式处理）。"""
    hexed = _public_key_hex_from_env()
    if not hexed:
        return None
    return Verifier(hexed)


def strict_enabled() -> bool:
    return (os.environ.get(ENV_STRICT) or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def check(
    *,
    kind: str,
    item_id: str,
    fingerprint: str,
    action: str,
    signed_by: str,
    signature: Optional[dict] = None,
    now: Optional[float] = None,
) -> dict:
    """统一验签入口。

    Returns:
        {"signed_by": str, "verified": bool, "reason": str}

    Raises:
        SignatureRequired: 严格模式下无签名或公钥未配置；
        SignatureError:    签名存在但不可信（格式/算法/时效/验签失败）。
    """
    who = (signed_by or "").strip()
    sig = signature or None
    if not sig:
        if strict_enabled():
            raise SignatureRequired(
                "签名校验已开启（%s=1）：缺少 signature，拒绝继续" % ENV_STRICT
            )
        return {
            "signed_by": who,
            "verified": False,
            "reason": "legacy mode: no signature provided "
            "(EVOLVE_SIGNATURE_STRICT not enabled)",
        }

    if not isinstance(sig, dict):
        raise SignatureError("signature 必须是对象（scheme/sig/ts/nonce）")
    if sig.get("scheme") != SIG_SCHEME:
        raise SignatureError(
            "unsupported scheme: %r（期望 %s）" % (sig.get("scheme"), SIG_SCHEME)
        )
    verifier = verifier_from_env()
    if verifier is None:
        if strict_enabled():
            raise SignatureRequired(
                "签名校验已开启但未配置公钥（%s / %s）"
                % (ENV_PUBKEY_HEX, ENV_PUBKEY_FILE)
            )
        return {
            "signed_by": who,
            "verified": False,
            "reason": "signature present but verifier not configured "
            "(EVOLVE_PRINCIPAL_PUBKEY_HEX/FILE unset)",
        }
    ok, reason = verifier.verify(
        kind=kind,
        item_id=item_id,
        fingerprint=fingerprint,
        action=action,
        ts=sig.get("ts"),
        nonce=sig.get("nonce", ""),
        sig_hex=sig.get("sig", ""),
        now=now,
    )
    if not ok:
        raise SignatureError("签名校验失败：%s" % reason)
    return {"signed_by": who, "verified": True, "reason": reason}
