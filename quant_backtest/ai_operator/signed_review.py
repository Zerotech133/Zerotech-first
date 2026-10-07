"""可信应用策略内的 Ed25519 审核验证，不自行证明真人登记或来源资格。"""

import hashlib
from types import MappingProxyType

from .contracts import canonical_json, utc_now
from .human_review import _hex, _json_object, _statement, _time


_DOMAIN = "ai_operator.human_review.ed25519.v1"
_BUNDLE_FIELDS = frozenset({"schema_version", "authority_id", "policies"})
_POLICY_FIELDS = frozenset({"policy_id", "effective_at", "principals"})
_ENROLLMENT_FIELDS = frozenset({"principal_id", "key_id", "public_key_hex", "role", "valid_from",
                               "valid_until", "revoked_at", "enrollment_reference"})
_PROOF_FIELDS = frozenset({"schema_version", "algorithm", "authority_id", "policy_id", "key_id", "signature_hex"})


def _text(value, name):
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} 必须是非空字符串")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise ValueError(f"{name} 必须是有效 UTF8 字符串") from None
    return value


def _canonical_time(value, name):
    normalized = _time(value)
    if value != normalized:
        raise ValueError(f"{name} 必须是规范 UTC 时间")
    return normalized


def _ed25519_public_key():
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError:
        raise ValueError("离线审核认证需要 cryptography 的 Ed25519 实现") from None
    return Ed25519PublicKey


def signing_message(statement, authority_id, policy_id) -> bytes:
    """固定签名域绑定可信 authority/policy 和完整规范声明。"""
    body = {"domain": _DOMAIN, "authority_id": _text(authority_id, "authority_id"),
            "policy_id": _text(policy_id, "policy_id"), "statement": _statement(statement)}
    return canonical_json(body).encode("utf-8")


class SignedReviewVerifier:
    """由可信应用注入完整归档策略和请求外固定摘要，按原接收时点验权。"""

    def __init__(self, bundle, expected_bundle_sha256):
        pinned = _hex(expected_bundle_sha256, 64, "expected_bundle_sha256")
        archived = _json_object(bundle, "bundle", 262144)
        actual = hashlib.sha256(canonical_json(archived).encode("utf-8")).hexdigest()
        if actual != pinned:
            raise ValueError("可信审核策略摘要不匹配")
        if set(archived) != _BUNDLE_FIELDS or type(archived["schema_version"]) is not int or archived["schema_version"] != 1:
            raise ValueError("bundle 字段不符或版本不受支持")
        authority = _text(archived["authority_id"], "authority_id")
        policies = archived["policies"]
        if type(policies) is not list or not 1 <= len(policies) <= 64:
            raise ValueError("bundle 必须包含 1 至 64 份完整策略")
        public_key_class = _ed25519_public_key()
        seen_policies, prepared, previous = set(), [], None
        for policy in policies:
            if type(policy) is not dict or set(policy) != _POLICY_FIELDS:
                raise ValueError("审核策略字段不符")
            policy_id = _text(policy["policy_id"], "policy_id")
            effective = _canonical_time(policy["effective_at"], "effective_at")
            if policy_id in seen_policies or (previous is not None and effective <= previous):
                raise ValueError("policy_id 必须唯一且 effective_at 严格递增")
            seen_policies.add(policy_id)
            previous = effective
            principals = policy["principals"]
            if type(principals) is not list or not 1 <= len(principals) <= 64:
                raise ValueError("每份策略必须包含 1 至 64 个主体")
            enrollments, seen_keys = {}, set()
            for principal in principals:
                if type(principal) is not dict or set(principal) != _ENROLLMENT_FIELDS:
                    raise ValueError("公钥主体登记字段不符")
                _text(principal["principal_id"], "principal_id")
                _text(principal["enrollment_reference"], "enrollment_reference")
                if type(principal["role"]) is not str or principal["role"] not in ("source_reviewer", "source_admin"):
                    raise ValueError("登记主体没有来源审核权限")
                key_id = _hex(principal["key_id"], 64, "key_id")
                public_hex = _hex(principal["public_key_hex"], 64, "public_key_hex")
                public_bytes = bytes.fromhex(public_hex)
                if key_id != hashlib.sha256(public_bytes).hexdigest():
                    raise ValueError("key_id 与公钥原始字节指纹不一致")
                if key_id in enrollments or public_hex in seen_keys:
                    raise ValueError("同一策略不得重复登记公钥或 key_id")
                valid_from = _canonical_time(principal["valid_from"], "valid_from")
                valid_until = _canonical_time(principal["valid_until"], "valid_until")
                if valid_until <= valid_from:
                    raise ValueError("valid_until 必须晚于 valid_from")
                if principal["revoked_at"] is not None:
                    _canonical_time(principal["revoked_at"], "revoked_at")
                enrollment = dict(principal)
                enrollment["public_key"] = public_key_class.from_public_bytes(public_bytes)
                enrollments[key_id] = MappingProxyType(enrollment)
                seen_keys.add(public_hex)
            prepared.append((policy_id, effective, MappingProxyType(enrollments)))
        # 仅保留深拷贝后的不可变权限索引，调用方原 bundle 突变不能改写认证。
        self._authority_id = authority
        self._policies = tuple(prepared)

    def verify(self, statement, proof, *, received_at) -> dict:
        """校验签名与系统接收时点权限；历史重验沿用事件原始 received_at。"""
        normalized = _statement(statement)
        assertion = _json_object(proof, "proof", 65536)
        if set(assertion) != _PROOF_FIELDS or type(assertion["schema_version"]) is not int or assertion["schema_version"] != 1:
            raise ValueError("离线审核 proof 字段不符或版本不受支持")
        if type(assertion["algorithm"]) is not str or assertion["algorithm"] != "Ed25519":
            raise ValueError("离线审核仅支持 Ed25519")
        authority = _text(assertion["authority_id"], "authority_id")
        policy_id = _text(assertion["policy_id"], "policy_id")
        key_id = _hex(assertion["key_id"], 64, "key_id")
        signature = bytes.fromhex(_hex(assertion["signature_hex"], 128, "signature_hex"))
        received = _time(received_at)
        if received > _time(utc_now()):
            raise ValueError("系统接收时点不得在未来")
        selected = None
        for policy in self._policies:
            if policy[1] > received:
                break
            selected = policy
        if selected is None:
            raise ValueError("系统接收时点尚无可用的可信审核策略")
        if authority != self._authority_id or policy_id != selected[0]:
            raise ValueError("proof 与系统接收时点最新有效策略不符")
        principal = selected[2].get(key_id)
        if principal is None:
            raise ValueError("公钥未在最新有效策略中登记")
        if not principal["valid_from"] <= received < principal["valid_until"]:
            raise ValueError("主体权限在系统接收时点尚未生效或已到期")
        if principal["revoked_at"] is not None and principal["revoked_at"] <= received:
            raise ValueError("主体权限在系统接收时点已撤销")
        message = signing_message(normalized, authority, policy_id)
        try:
            principal["public_key"].verify(signature, message)
        except Exception:
            raise ValueError("Ed25519 审核签名无效") from None
        return {"principal_id": principal["principal_id"], "authority_id": authority,
                "policy_id": policy_id, "role": principal["role"]}
