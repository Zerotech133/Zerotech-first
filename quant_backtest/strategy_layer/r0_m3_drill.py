"""WO-QT-20261020-046：R0 六模块纯合成全流程演练（M3 前置，R1 预演）。

把已验收的六个模块串成一条可重复、失败关闭的演练链：

  1. 演练签发者对合成源字节签发 Ed25519 来源凭证，并独立验签（VERIFIED）。
  2. inspect_historical_pool 在内存完成 行→Signal→融合→股票池（UNVERIFIED）。
  3. build_research_brief 生成研究简报，经"模拟通道"规范化为字节。
  4. 演练接收方对简报字节签发回执并独立验签（VERIFIED_RECEIPT_ONLY）。
  5. ReplayLedger 对同一 message_id 强制先 CLAIMED 后 DUPLICATE。
  6. reconcile_evidence 逐段对账（NOT_VERIFIED）。

边界（必须原样保留）：
- 本模块与其报告仅为合成演练，水印 scope=SYNTHETIC_DRILL_NOT_M3_EVIDENCE；
  不构成 M3 证据，不接触任何真实数据、PG、网络或真实通道。
- 全程终态 real_source_verdict=BLOCKED_REAL_DATA、cutover_verdict=BLOCK_CUTOVER、
  status 永不为 READY；任何一段非预期结果立即停止并结构化上报（失败关闭），
  不以裸异常代替结构化拒绝。
- 本模块使用的 Ed25519 密钥均为固定种子的【演练密钥，严禁复用于任何真实签发】。
- "模拟通道"仅指简报规范化字节的内存约定（canonical_json_bytes）；
  真实通道契约属 R1。
"""
from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .r0_evidence_reconciliation import reconcile_evidence
from .r0_historical_pool_probe import inspect_historical_pool
from .r0_receipt_attestation import verify_receipt_attestation
from .r0_receipt_replay_guard import ReplayLedger
from .r0_research_brief import build_research_brief
from .r0_source_attestation import verify_source_attestation

DRILL_SCOPE = "SYNTHETIC_DRILL_NOT_M3_EVIDENCE"
DEFAULT_BUSINESS_DAY = "20261020"
DEFAULT_NOW = datetime(2026, 10, 20, 16, 0, 0, tzinfo=timezone.utc)
SOURCE_ISSUER_ID = "r0-m3-drill-source-issuer"
RECEIVER_IDENTITY = "r0-m3-drill-receiver"
_RECEIPT_DOMAIN = b"r0-research-brief-receipt-v1\x00"
_LEDGER_FILENAME = "replay_ledger.json"
_SEGMENT_ORDER = ("source_attestation", "probe", "brief", "receipt",
                  "replay_guard", "reconciliation")
_STAGE_ORDER = ("source", "signal", "combined", "pool", "brief", "receipt")

# 固定种子演练密钥材料：只为保证演练可重复；【演练密钥，严禁复用于任何真实签发】。
_ISSUER_SEED = bytes(range(32))
_RECEIVER_SEED = bytes(range(32, 64))


def issuer_private_key() -> Ed25519PrivateKey:
    """演练源签发者密钥（固定种子）。演练密钥，严禁复用于任何真实签发。"""
    return Ed25519PrivateKey.from_private_bytes(_ISSUER_SEED)


def receiver_private_key() -> Ed25519PrivateKey:
    """演练接收方密钥（固定种子）。演练密钥，严禁复用于任何真实签发。"""
    return Ed25519PrivateKey.from_private_bytes(_RECEIVER_SEED)


def raw_public_key(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def drill_issued_at(business_day: str = DEFAULT_BUSINESS_DAY) -> str:
    """演练签发时刻：业务日 08:00:00Z（晚于演练时钟即被验签拒绝）。"""
    return (f"{business_day[:4]}-{business_day[4:6]}-{business_day[6:]}"
            "T08:00:00Z")


def canonical_json_bytes(payload: dict) -> bytes:
    """"模拟通道"的简报规范化字节；真实通道契约属 R1。

    发送前把 source.path 归一为纯文件名：探针内嵌的是本机绝对路径，
    不属于研究产物内容；归一后简报字节与 work_dir 无关，演练可逐字节
    重复。归一只发生在发送字节副本上，不改调用方原 dict（对账仍用原值）。
    """
    normalized = deepcopy(payload)
    source = normalized.get("source")
    if type(source) is dict and type(source.get("path")) is str:
        source["path"] = Path(source["path"]).name
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def synthetic_drill_rows(business_day: str = DEFAULT_BUSINESS_DAY) -> list[dict]:
    """两笔不同标的的合成当日事件；仅供演练，不代表任何真实采集。"""
    day = f"{business_day[:4]}-{business_day[4:6]}-{business_day[6:]}"
    return [
        {"market": "CN", "code": "002633.SZ", "type": "2买", "dt": day,
         "detect_date": day, "price": 10.0, "mbei": None, "bei": None,
         "bei2": None, "vv": True},
        {"market": "CN", "code": "002634.SZ", "type": "2买", "dt": day,
         "detect_date": day, "price": 11.0, "mbei": None, "bei": None,
         "bei2": None, "vv": True},
    ]


def write_drill_source(source_rows, work_dir, *,
                       business_day: str = DEFAULT_BUSINESS_DAY,
                       filename: str = "drill_source.jsonl") -> tuple[Path, bytes]:
    """把调用方给定的源行写为 UTF-8 JSONL（仅写入调用方指定的 work_dir）。"""
    directory = Path(work_dir)
    directory.mkdir(parents=True, exist_ok=True)
    raw = "".join(json.dumps(row, ensure_ascii=False) + "\n"
                  for row in source_rows).encode("utf-8")
    path = directory / filename
    path.write_bytes(raw)
    return path, raw


def issue_source_attestation(
    signing_key: Ed25519PrivateKey, business_day: str, source_bytes: bytes, *,
    issuer_id: str = SOURCE_ISSUER_ID, issued_at: str | None = None,
) -> dict:
    """演练签发者对源字节签发来源凭证（与真实凭证契约同构）。"""
    claims = {
        "schema_version": 1,
        "issuer_id": issuer_id,
        "business_date": business_day,
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "event_count": None,
        "issued_at": issued_at or drill_issued_at(business_day),
    }
    lines = source_bytes.decode("utf-8").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    claims["event_count"] = len(lines)
    payload = json.dumps(claims, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    return {"claims": claims,
            "signature": base64.b64encode(signing_key.sign(payload)).decode("ascii")}


def sign_drill_receipt(
    signing_key: Ed25519PrivateKey, brief_bytes: bytes, *,
    batch_id: str, source_identity: str, received_at: str, message_id: str,
    receiver_identity: str = RECEIVER_IDENTITY,
) -> dict:
    """演练接收方对简报字节签发回执（与真实回执契约同构）。"""
    fields = {
        "brief_sha256": hashlib.sha256(brief_bytes).hexdigest(),
        "batch_id": batch_id,
        "source_identity": source_identity,
        "receiver_identity": receiver_identity,
        "received_at": received_at,
        "message_id": message_id,
    }
    message = _RECEIPT_DOMAIN + json.dumps(
        fields, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")
    receipt = dict(fields)
    receipt["signature"] = base64.b64encode(
        signing_key.sign(message)).decode("ascii")
    return receipt


def _blank_report(business_day: str, clock: datetime) -> dict:
    return {
        "scope": DRILL_SCOPE,
        "drill": "r0_m3_full_drill_v1",
        "trade_date": business_day,
        "drill_clock": clock.isoformat(),
        "key_note": "演练密钥（固定种子），严禁复用于任何真实签发",
        "channel_note": "简报经模拟通道规范化为字节；真实通道契约属 R1",
        "status": "FAILED",
        "stopped_at": None,
        "segments": {name: {"status": "NOT_RUN", "reason": None}
                     for name in _SEGMENT_ORDER},
        "final": {"status": "FAILED",
                  "real_source_verdict": "BLOCKED_REAL_DATA",
                  "cutover_verdict": "BLOCK_CUTOVER"},
    }


def _contains_ready(value) -> bool:
    """递归检查报告中是否出现字面 READY 值（演练全程通过也不得出现）。"""
    if isinstance(value, str):
        return value == "READY"
    if isinstance(value, dict):
        return any(_contains_ready(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_ready(item) for item in value)
    return False


def run_full_drill(source_rows, work_dir, *, now: datetime | None = None,
                   business_day: str = DEFAULT_BUSINESS_DAY) -> dict:
    """逐段执行合成演练链；任何一段非预期即停止，返回结构化失败报告。

    - source_rows：调用方给定的源行列表（dict，含 detect_date/code/type 等）。
    - work_dir：演练工作目录（如 pytest tmp_path）；仅写入该目录，源 JSONL
      与重放账本均落在此处，绝不写死任何真实路径。
    - now：注入的演练时钟（默认 DEFAULT_NOW）；密钥固定种子 + 时钟注入，
      保证同参数重复运行结果一致。
    """
    clock = DEFAULT_NOW if now is None else now
    report = _blank_report(business_day, clock)
    segments = report["segments"]
    stage = "source_attestation"

    def fail(name: str, status: str, reason: str) -> dict:
        report["status"] = "FAILED"
        report["stopped_at"] = name
        segments[name] = {"status": status, "reason": reason}
        report["final"]["status"] = "FAILED"
        return report

    try:
        # 段 1：写源 + 演练签发者签发 + 独立验签
        source_path, source_bytes = write_drill_source(
            source_rows, work_dir, business_day=business_day)
        attestation = issue_source_attestation(
            issuer_private_key(), business_day, source_bytes)
        source_result = verify_source_attestation(
            business_day, source_bytes, attestation,
            trusted_public_keys={SOURCE_ISSUER_ID: raw_public_key(issuer_private_key())},
            trusted_as_of=clock)
        if source_result["status"] != "VERIFIED":
            return fail("source_attestation", source_result["status"],
                        source_result["reason"])
        segments["source_attestation"] = {
            "status": "VERIFIED", "reason": source_result["reason"],
            "issuer_id": source_result["issuer_id"],
            "source_sha256": source_result["source_sha256"],
            "event_count": source_result["event_count"],
            "issued_at_claim": source_result["issued_at_claim"],
        }

        # 段 2：历史池探针（内存链；只读段 1 写入的同一份字节）
        stage = "probe"
        probe = inspect_historical_pool(business_day, source_path)
        if (probe["status"] != "UNVERIFIED"
                or probe["chain_status"] != "VERIFIED_IN_MEMORY"):
            return fail("probe", probe["status"],
                        f"chain_status={probe['chain_status']}; "
                        f"failures={probe['failures']}")
        segments["probe"] = {
            "status": probe["status"], "chain_status": probe["chain_status"],
            "counts": dict(probe["counts"]),
            "reason": "内存链完成但源身份未验证；仅研究用途",
        }

        # 段 3：研究简报 + 模拟通道规范化字节
        stage = "brief"
        brief = build_research_brief(probe)
        brief_bytes = canonical_json_bytes(brief)
        if (brief.get("scope") != "RESEARCH_ONLY_NOT_PRODUCTION"
                or brief.get("cutover_verdict") != "BLOCK_CUTOVER"
                or brief.get("trade_date") != business_day
                or brief.get("source", {}).get("sha256")
                != source_result["source_sha256"]):
            return fail("brief", "INVALID_BRIEF", "简报研究边界或源身份不一致")
        segments["brief"] = {
            "status": "BUILT_IN_MEMORY",
            "brief_sha256": hashlib.sha256(brief_bytes).hexdigest(),
            "reason": "内存简报经模拟通道规范化；真实通道契约属 R1",
        }

        # 段 4：演练接收方签发回执 + 独立验签
        stage = "receipt"
        message_id = f"r0-m3-drill-{business_day}-receipt-0001"
        receipt = sign_drill_receipt(
            receiver_private_key(), brief_bytes, batch_id=business_day,
            source_identity=brief["source"]["sha256"],
            received_at=clock.isoformat(), message_id=message_id)
        seen_message_ids: set[str] = set()
        receipt_result = verify_receipt_attestation(
            brief_bytes, receipt,
            trusted_receiver_public_key=raw_public_key(receiver_private_key()),
            expected_batch_id=business_day,
            expected_source_identity=brief["source"]["sha256"],
            expected_receiver_identity=RECEIVER_IDENTITY,
            seen_message_ids=seen_message_ids, now=clock)
        if receipt_result["status"] != "VERIFIED_RECEIPT_ONLY":
            return fail("receipt", receipt_result["status"],
                        "回执未通过独立接收方验签")
        segments["receipt"] = {
            "status": "VERIFIED_RECEIPT_ONLY", "message_id": message_id,
            "reason": "回执独立验签通过；仅证明此回执，不代表真实接收",
        }

        # 段 5：重放账本（同 message_id 先 CLAIMED 后 DUPLICATE）
        stage = "replay_guard"
        ledger = ReplayLedger(str(Path(work_dir) / _LEDGER_FILENAME))
        first_claim = ledger.claim(message_id)
        second_claim = ledger.claim(message_id)
        # claim 返回 dict（含真实时钟 claimed_at），只取 status 入报告以保可重复
        first_status = first_claim.get("status") if type(first_claim) is dict else None
        second_status = second_claim.get("status") if type(second_claim) is dict else None
        if first_status != "CLAIMED" or second_status != "DUPLICATE":
            return fail("replay_guard", "REPLAY_NOT_ENFORCED",
                        f"claim 结果为 {first_claim!r}/{second_claim!r}")
        segments["replay_guard"] = {
            "status": "ENFORCED", "message_id": message_id,
            "claims": [first_status, second_status],
            "reason": "同 message_id 二次领取被拒绝",
        }

        # 段 6：逐段对账
        stage = "reconciliation"
        reconciliation = reconcile_evidence(
            business_day, probe=probe, source_attestation=source_result,
            brief=brief, receipt_attestation=receipt_result)
        actual_stages = [reconciliation["stages"][name]["status"]
                         for name in _STAGE_ORDER]
        expected_stages = ["CALLER_ATTESTATION_CLAIM", "IN_MEMORY_ONLY",
                           "IN_MEMORY_ONLY", "IN_MEMORY_ONLY",
                           "IN_MEMORY_ONLY", "CALLER_RECEIPT_CLAIM"]
        if (reconciliation["status"] != "NOT_VERIFIED"
                or actual_stages != expected_stages):
            return fail("reconciliation", reconciliation["status"],
                        f"stages={actual_stages}; "
                        f"reasons={reconciliation['reasons']}")
        segments["reconciliation"] = {
            "status": reconciliation["status"],
            "stages": {name: dict(reconciliation["stages"][name])
                       for name in reconciliation["stages"]},
            "counts": dict(reconciliation["counts"]),
            "reason": "六段对账一致但仍 NOT_VERIFIED；不构成 READY",
        }

        # 终态护栏：全程通过也绝不 READY
        report["status"] = "BLOCKED_NOT_READY"
        report["stopped_at"] = None
        report["final"] = {
            "status": "BLOCKED_NOT_READY",
            "real_source_verdict": reconciliation["real_source_verdict"],
            "cutover_verdict": reconciliation["cutover_verdict"],
        }
        if (report["final"]["real_source_verdict"] != "BLOCKED_REAL_DATA"
                or report["final"]["cutover_verdict"] != "BLOCK_CUTOVER"
                or _contains_ready(report)):
            return fail("reconciliation", "FINAL_GUARD_FAILED", "终态护栏被破坏")
        return report
    except Exception as exc:  # 失败关闭：异常也结构化，不裸抛
        return fail(stage, "UNEXPECTED_EXCEPTION",
                    f"{type(exc).__name__}: {exc}")
