# -*- coding: utf-8 -*-
"""WO-QT-20261020-056：R1 真实数据试运行（待批清单事项一，R0 M3 材料）。

与 r0_m3_drill（纯合成演练）的唯一区别：源为真实信号池的点时冻结字节、
签发/接收密钥为运行时 os.urandom 生成的一次性真实密钥（非演练固定种子）。
链路结构、验签、回执、重放账本、对账全部复用已验收的 R0 模块。

边界（必须原样保留）：
- 仅本地目录写入（work_dir 与公钥导出目录）；零 PG、零网络外发、零调度。
- 终报恒带 RESEARCH_ONLY_NOT_PRODUCTION / BLOCKED_REAL_DATA / BLOCK_CUTOVER，
  永不输出 READY；试运行不构成生产切流依据（切流属 R2 另批）。
- 私钥仅落 work_dir/keys/（trial-grade 托管）；公钥另路导出 pubkey_out_dir
  （与源/凭证分离的可信渠道副本）。演练种子密钥严禁在此复用。
- 试运行口径偏差（接收方由本席代收执行、密钥托管非生产级）必须写入终报
  disclosures，不得静默。
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .r0_evidence_reconciliation import reconcile_evidence
from .r0_historical_pool_probe import inspect_historical_pool
from .r0_m3_drill import (
    canonical_json_bytes,
    issue_source_attestation,
    raw_public_key,
    sign_drill_receipt,
)
from .r0_receipt_attestation import verify_receipt_attestation
from .r0_receipt_replay_guard import ReplayLedger
from .r0_source_attestation import verify_source_attestation
import logging

TRIAL_ISSUER_ID = "r0-m3-trial-issuer"
TRIAL_RECEIVER_IDENTITY = "tianfeng-guikou-receiver"
_LEDGER_FILENAME = "replay_ledger.json"
_SOURCE_COPY = "source_batch.jsonl"
_MANIFEST = "manifest.json"
_REPORT = "trial_report.json"

# 试运行口径披露（随终报落盘，逐字保留）
_DISCLOSURES = [
    "接收方=田丰归口席（老田 2026-10-21 01:30 指定）；回签由实施席按接收方"
    "身份在本机代为执行（密钥独立生成），真实外部通道回签属 R2 另批",
    "密钥托管为 trial-grade：私钥仅落 work_dir/keys/ 本地目录，非生产级"
    "密钥 ceremony；签发/接收密钥均 os.urandom 一次性生成，与演练固定种子"
    "严格分离，用后不复用",
    "冻结口径：整池点时冻结（源文件 mtime 为证），非本地多日池按日切片；"
    "业务日当日零检出行为合法终态，零匹配不等于可信零事件（探针自动标注）",
]


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _write_private_key(key: Ed25519PrivateKey, path: Path) -> None:
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, pem)
    finally:
        os.close(fd)
    # 预存宽权限文件不会被 O_CREAT 的 mode 收紧，显式 chmod 兜底
    os.chmod(path, 0o600)


def _export_public_key(key: Ed25519PrivateKey, path: Path) -> None:
    pem = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pem)


def freeze_pool_point_in_time(
    source_path: str | Path,
    work_dir: str | Path,
    business_day: str,
    *,
    now: datetime | None = None,
    source_bytes: bytes | None = None,
) -> dict:
    """段 1 因子化（WO-QT-20261020-056 机制复用；N7 独立冻结入口共用）。

    信号池点时冻结：唯一一次读取源字节，冻结副本落
    work_dir/source_batch.jsonl，manifest 落 work_dir/manifest.json
    （freeze_basis=point_in_time_freeze_of_live_pool_file）。

    Parameters
    ----------
    source_path : 真实信号池 JSONL 路径（只读；mtime 为冻结时点证据）。
    work_dir : 冻结产物目录（不存在则创建）。
    business_day : 业务日 YYYYMMDD（仅记入 manifest；行级业务日一致性
        不由本函数强制——该守护在验签层显式 surfaced，见
        r0_source_attestation 全行 detect_date 校验与 r0_readonly_e2e
        的 WRONG_DAY 反例；调用方需要 fail-visible 时应先自行校验）。
    now : 冻结时钟（None 取当前 UTC）；observed_date 取其日期。
    source_bytes : 调用方已读取的源字节。提供时以之为唯一字节源
        （mtime 仍实时取），使对齐校验与冻结共用同一次读取、消除
        TOCTOU 窗口；不提供则本函数自行读取（唯一一次）。

    Returns
    -------
    dict manifest（与人工试运行段 1 逐字节一致）：
    source_path/business_date/observed_date/source_mtime_utc/
    source_sha256/event_count/freeze_basis。
    """
    clock = _now_utc() if now is None else now
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    source = Path(source_path)
    source_mtime_utc = os.path.getmtime(source)
    raw = source.read_bytes() if source_bytes is None else source_bytes
    (work / _SOURCE_COPY).write_bytes(raw)
    lines = raw.decode("utf-8").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    manifest = {
        "source_path": str(work / _SOURCE_COPY),
        "business_date": business_day,
        "observed_date": clock.date().isoformat(),
        "source_mtime_utc": source_mtime_utc,
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "event_count": len(lines),
        "freeze_basis": "point_in_time_freeze_of_live_pool_file",
    }
    (work / _MANIFEST).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def run_real_trial(
    source_path: str | Path,
    work_dir: str | Path,
    business_day: str,
    *,
    pubkey_out_dir: str | Path,
    issuer_id: str = TRIAL_ISSUER_ID,
    receiver_identity: str = TRIAL_RECEIVER_IDENTITY,
    now: datetime | None = None,
) -> dict:
    """R1 六段试运行：冻结→签发→验签→探针→简报→回执→重放→对账。

    Parameters
    ----------
    source_path : 真实信号池 JSONL 路径（只读取一次字节）。
    work_dir : 试运行工作目录（源冻结副本/manifest/账本/私钥/终报落此）。
    business_day : 业务日 YYYYMMDD。
    pubkey_out_dir : 签发/接收公钥的独立导出目录（可信渠道副本）。

    Returns
    -------
    dict 终报：恒 RESEARCH_ONLY_NOT_PRODUCTION，status ∈
    {BLOCKED_NOT_READY, FAILED, NO_MATCH_UNVERIFIED, ...}（按探针实态），
    绝不 READY。
    """
    clock = _now_utc() if now is None else now
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    pub_dir = Path(pubkey_out_dir)
    report: dict = {
        "scope": "RESEARCH_ONLY_NOT_PRODUCTION",
        "trial": "r0_m3_real_trial_v1",
        "trade_date": business_day,
        "trial_clock": clock.isoformat(),
        "issuer_id": issuer_id,
        "receiver_identity": receiver_identity,
        "disclosures": list(_DISCLOSURES),
        "status": "FAILED",
        "stopped_at": None,
        "real_source_verdict": "BLOCKED_REAL_DATA",
        "cutover_verdict": "BLOCK_CUTOVER",
        "segments": {},
        "final": None,
    }

    def fail(stage: str, status: str, reason: str) -> dict:
        report["status"] = "FAILED"
        report["stopped_at"] = stage
        report["segments"][stage] = {"status": status, "reason": reason}
        report["final"] = {
            "status": "FAILED",
            "real_source_verdict": "BLOCKED_REAL_DATA",
            "cutover_verdict": "BLOCK_CUTOVER",
        }
        return report

    try:
        # 段 1：点时冻结（唯一读取源字节；冻结副本与 manifest 落 work_dir）
        source = Path(source_path)
        raw = source.read_bytes()
        manifest = freeze_pool_point_in_time(
            source_path, work, business_day, now=clock, source_bytes=raw
        )
        report["segments"]["freeze"] = {
            "status": "FROZEN",
            "event_count": manifest["event_count"],
            "sha256": manifest["source_sha256"],
        }

        # 段 2：真实签发（os.urandom 一次性密钥）+ 独立验签
        issuer_key = Ed25519PrivateKey.from_private_bytes(os.urandom(32))
        receiver_key = Ed25519PrivateKey.from_private_bytes(os.urandom(32))
        _write_private_key(issuer_key, work / "keys" / "issuer_private.pem")
        _write_private_key(receiver_key, work / "keys" / "receiver_private.pem")
        _export_public_key(issuer_key, pub_dir / "issuer_public.pem")
        _export_public_key(receiver_key, pub_dir / "receiver_public.pem")
        attestation = issue_source_attestation(
            issuer_key,
            business_day,
            raw,
            issuer_id=issuer_id,
            issued_at=clock.strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        source_result = verify_source_attestation(
            business_day,
            raw,
            attestation,
            trusted_public_keys={issuer_id: raw_public_key(issuer_key)},
            trusted_as_of=clock,
        )
        if source_result["status"] != "VERIFIED":
            return fail(
                "source_attestation", source_result["status"], source_result["reason"]
            )
        report["segments"]["source_attestation"] = {
            "status": "VERIFIED",
            "issuer_id": issuer_id,
            "source_sha256": source_result["source_sha256"],
        }

        # 段 3：真实探针（内存链；真实行校验+当日匹配）
        probe = inspect_historical_pool(business_day, work / _SOURCE_COPY)
        report["segments"]["probe"] = {
            "status": probe["status"],
            "chain_status": probe["chain_status"],
            "counts": dict(probe["counts"]),
            "failures": [dict(f) for f in probe["failures"]],
        }
        if probe["status"] not in ("UNVERIFIED", "NO_MATCH_UNVERIFIED"):
            return fail(
                "probe",
                probe["status"],
                f"chain_status={probe['chain_status']}; failures={probe['failures']}",
            )

        # 段 4：研究简报 + 模拟通道字节 + 接收方回签 + 独立验签
        brief = probe["brief"]
        if (
            brief.get("scope") != "RESEARCH_ONLY_NOT_PRODUCTION"
            or brief.get("cutover_verdict") != "BLOCK_CUTOVER"
        ):
            return fail("brief", "INVALID_BRIEF", "简报研究边界不一致")
        brief_bytes = canonical_json_bytes(brief)
        message_id = f"r0-m3-trial-{business_day}-receipt-0001"
        receipt = sign_drill_receipt(
            receiver_key,
            brief_bytes,
            batch_id=business_day,
            source_identity=brief["source"]["sha256"],
            received_at=clock.isoformat(),
            message_id=message_id,
            receiver_identity=receiver_identity,
        )
        receipt_result = verify_receipt_attestation(
            brief_bytes,
            receipt,
            trusted_receiver_public_key=raw_public_key(receiver_key),
            expected_batch_id=business_day,
            expected_source_identity=brief["source"]["sha256"],
            expected_receiver_identity=receiver_identity,
            seen_message_ids=set(),
            now=clock,
        )
        if receipt_result["status"] != "VERIFIED_RECEIPT_ONLY":
            return fail("receipt", receipt_result["status"], "回执未通过独立接收方验签")
        report["segments"]["receipt"] = {
            "status": "VERIFIED_RECEIPT_ONLY",
            "message_id": message_id,
            "receiver_identity": receiver_identity,
        }

        # 段 5：重放账本（同 message_id 先 CLAIMED 后 DUPLICATE）
        ledger = ReplayLedger(str(work / _LEDGER_FILENAME))
        first = ledger.claim(message_id)
        second = ledger.claim(message_id)
        first_status = first.get("status") if type(first) is dict else None
        second_status = second.get("status") if type(second) is dict else None
        if first_status != "CLAIMED" or second_status != "DUPLICATE":
            return fail(
                "replay_guard",
                "REPLAY_NOT_ENFORCED",
                f"claim 结果为 {first!r}/{second!r}",
            )
        report["segments"]["replay_guard"] = {
            "status": "ENFORCED",
            "claims": [first_status, second_status],
        }

        # 段 6：对账终态（如实呈现，不提升为 READY）
        reconciliation = reconcile_evidence(
            business_day,
            probe=probe,
            source_attestation=source_result,
            brief=brief,
            receipt_attestation=receipt_result,
        )
        if reconciliation["status"] == "REJECTED":
            fr = fail(
                "reconciliation",
                "RECONCILIATION_REJECTED",
                "对账层判定 REJECTED，终报不得洗白为合法终态",
            )
            fr["segments"]["reconciliation"]["counts"] = dict(reconciliation["counts"])
            return fr
        report["segments"]["reconciliation"] = {
            "status": reconciliation["status"],
            "counts": dict(reconciliation["counts"]),
        }
        report["final"] = {
            "status": reconciliation["status"],
            "real_source_verdict": reconciliation["real_source_verdict"],
            "cutover_verdict": reconciliation["cutover_verdict"],
        }
        if (
            report["final"]["real_source_verdict"] != "BLOCKED_REAL_DATA"
            or report["final"]["cutover_verdict"] != "BLOCK_CUTOVER"
        ):
            return fail("reconciliation", "FINAL_GUARD_FAILED", "终态门位被破坏")
        report["status"] = "BLOCKED_NOT_READY"
        report["stopped_at"] = None
        report["trial_outcome"] = {
            "probe_status": probe["status"],
            "matched_events": probe["counts"].get("matched_events"),
            "note": (
                "业务日当日零检出事件（冻结态如此），零匹配不等于可信"
                "零事件；有数路径另跑补充业务日"
                if probe["status"] == "NO_MATCH_UNVERIFIED"
                else "当日有匹配事件，全链贯通"
            ),
        }
        return report
    except Exception as exc:  # 失败关闭：异常也结构化，不裸抛
        return fail("trial", "UNEXPECTED_EXCEPTION", f"{type(exc).__name__}: {exc}")
    finally:
        # 一次性私钥生命周期终结（机制约束，非仅披露文案）：
        # 无论成功/失败路径，链尾销毁私钥文件并在终报留痕。
        destroyed = []
        for kp in (
            work / "keys" / "issuer_private.pem",
            work / "keys" / "receiver_private.pem",
        ):
            try:
                if kp.exists():
                    kp.unlink()
                    destroyed.append(kp.name)
            except OSError as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/strategy_layer/r0_m3_real_trial.py", exc)
        if destroyed:
            report["segments"]["key_hygiene"] = {
                "status": "DESTROYED",
                "files": destroyed,
                "note": "一次性私钥用后销毁；用后不复用为机制约束",
            }


def save_report(report: dict, work_dir: str | Path) -> Path:
    """终报落盘（work_dir/trial_report.json）。"""
    path = Path(work_dir) / _REPORT
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


__all__ = [
    "run_real_trial",
    "save_report",
    "TRIAL_ISSUER_ID",
    "TRIAL_RECEIVER_IDENTITY",
]
