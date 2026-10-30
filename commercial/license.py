# -*- coding: utf-8 -*-
"""P2 商业化底座：license 签发/校验、租户、配额、审计（无第三方依赖）。

设计原则：
- 私有化部署：客户现场持有 license（HMAC 签名，绑定租户/套餐/到期/席位/配额）；
- 租户隔离：每个租户独立工作目录 + 独立 usage 计数；
- 配额：按套餐额度（月度报告数 / 日 API 调用）实时扣减，超限拒绝；
- 审计：所有商业动作追加 JSONL，可导出对账。

注意：v1 使用 HMAC（共享密钥），适合私有化交付 + 契约约束；
      后续可升级非对称签名（Ed25519/RSA），不影响上层接口。
"""
import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

VERSION = "1"
PREFIX = "SAC1"
DEFAULT_SECRET = "dev-secret-change-me-in-production"


class LicenseError(Exception):
    pass


def _secret(secret: Optional[str] = None) -> str:
    return secret or os.environ.get("COMMERCIAL_LICENSE_SECRET") or DEFAULT_SECRET


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(payload_b64: str, secret: Optional[str]) -> str:
    return _b64(hmac.new(_secret(secret).encode("utf-8"), payload_b64.encode("ascii"),
                         hashlib.sha256).digest())


@dataclass
class License:
    tenant: str
    plan: str = "pro"
    issued: float = field(default_factory=time.time)
    expires: float = 0.0          # 0 = 永久（仅内部测试用）
    seats: int = 1
    quota: Dict[str, int] = field(default_factory=dict)
    features: List[str] = field(default_factory=list)
    key: str = ""

    def is_expired(self, now: Optional[float] = None) -> bool:
        if not self.expires:
            return False
        return (now or time.time()) > float(self.expires)

    def to_dict(self) -> Dict[str, Any]:
        return {"tenant": self.tenant, "plan": self.plan, "issued": self.issued,
                "expires": self.expires, "seats": self.seats, "quota": self.quota,
                "features": self.features}


def issue(tenant: str, plan: str = "pro", expires: float = 0.0, seats: int = 1,
          quota: Optional[Dict[str, int]] = None, features: Optional[List[str]] = None,
          secret: Optional[str] = None) -> str:
    """签发 license key（payload 签名，v1: SAC1.<b64payload>.<b64sig>）。"""
    if not tenant:
        raise LicenseError("tenant 不能为空")
    lic = License(tenant=tenant, plan=plan, expires=float(expires), seats=int(seats),
                  quota=dict(quota or {}), features=list(features or []))
    payload = json.dumps(lic.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    p = _b64(payload.encode("utf-8"))
    return "%s.%s.%s" % (PREFIX, p, _sign(p, secret))


def verify(key: str, secret: Optional[str] = None, now: Optional[float] = None) -> License:
    """校验 license（格式/签名/到期）。"""
    try:
        prefix, p, sig = key.strip().split(".")
    except ValueError:
        raise LicenseError("license 格式错误（应为 SAC1.payload.sig）")
    if prefix != PREFIX:
        raise LicenseError("license 版本不支持: " + prefix)
    if not hmac.compare_digest(sig, _sign(p, secret)):
        raise LicenseError("license 签名无效")
    try:
        d = json.loads(_unb64(p).decode("utf-8"))
    except Exception as e:
        raise LicenseError("license payload 解析失败: %s" % e)
    lic = License(tenant=d.get("tenant", ""), plan=d.get("plan", "pro"),
                  issued=float(d.get("issued", 0)), expires=float(d.get("expires", 0)),
                  seats=int(d.get("seats", 1)), quota=dict(d.get("quota") or {}),
                  features=list(d.get("features") or []), key=key)
    if lic.is_expired(now):
        raise LicenseError("license 已过期（%s）" % time.strftime("%F", time.localtime(lic.expires)))
    return lic


def mask(key: str) -> str:
    """日志/回执里脱敏展示。"""
    if not key or len(key) < 16:
        return "***"
    return key[:10] + "..." + key[-6:]
