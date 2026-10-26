# -*- coding: utf-8 -*-
"""GOVAI-3：AI 判例草稿 → Z4 待签 payload → 验签入库（人类签字门）。

链路（与设计文档 §4.3 / G3 一致）：

  gov_ai.py --action case_draft --bundle <证据包>
        │  产出 draft JSON（cue/lesson/evidence/confidence，未签字）
        ▼
  gov_ai_case_bridge.py --emit            # 本模块：生成待签 payload（不签名）
        │  每条 item → EXP-SUPERVISION-NNN.payload.json（含 canonical hex/sha256）
        ▼
  本人执行 sign_approval.py --kind experience_write --i-am-human
        │  产出 EXP-SUPERVISION-NNN.signature.json
        ▼
  gov_ai_case_bridge.py --apply           # 验签（需公钥 env）→ experience_store 入库
                                          # 入库条目 verified=True；严格模式失败即拒

纪律：
  · 本模块**不签名、不生成密钥、不持有私钥**；payload 与签名由人完成；
  · `--apply` 必须配置公钥（EVOLVE_PRINCIPAL_PUBKEY_HEX/FILE），否则拒绝
    （不落"unverified"的 AI 草稿，防止静默越权）；
  · 只写 supervision 域（风控 riskguard 只读；域外需显式 --domain 且仍过白名单）；
  · 证据原文来自 AI 草稿（已过 gov_ai 校验器数字接地/Z5）；本模块再做一次
    lookahead_guard 路径检查，防 future 路径混入。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

_TOOLS = Path(__file__).resolve().parent
_REPO = _TOOLS.parent
_PLATFORM = _REPO / "agent_platform"
if str(_PLATFORM) not in sys.path:
    sys.path.insert(0, str(_PLATFORM))

from engine import signature_guard as SG  # noqa: E402
from engine import lookahead_guard as LG  # noqa: E402
from engine import experience_store as S  # noqa: E402

KIND = "experience_write"
ACTION = "write"
DEFAULT_DOMAIN = "supervision"
SIGNED_BY = "老田"
DEFAULT_KEY = _REPO.parent / "_vault" / "keys" / "principal_private.pem"
DRAFT_ROOT = _PLATFORM / "data" / "governance_ai" / "advice"


class BridgeError(RuntimeError):
    """payload/签名/入库链路错误（消息可展示给人）。"""


def _read_json(path: Path) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BridgeError("读取失败 %s：%s" % (path, exc)) from None


def _write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def latest_case_draft() -> Path:
    if not DRAFT_ROOT.exists():
        raise BridgeError("草稿目录不存在：%s" % DRAFT_ROOT)
    cands = sorted(DRAFT_ROOT.glob("*/case_draft.json"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    if not cands:
        raise BridgeError("未找到 case_draft.json（先跑 gov_ai --action case_draft）")
    return cands[0]


def load_draft(path: Path) -> dict:
    data = _read_json(Path(path))
    if not isinstance(data, dict) or data.get("action") != "case_draft":
        raise BridgeError("不是 case_draft 产物：%s" % path)
    if str(data.get("status") or "") != "draft":
        raise BridgeError("草稿 status=%r，仅 draft 可送签" % data.get("status"))
    items = data.get("items") or []
    if not isinstance(items, list) or not items:
        raise BridgeError("草稿没有可送签 items")
    return data


def _entry_content(item: dict) -> dict:
    cue = str(item.get("cue") or "").strip()
    lesson = str(item.get("lesson") or "").strip()
    evidence = item.get("evidence")
    if isinstance(evidence, (list, tuple)):
        evidence_text = "; ".join(str(e).strip() for e in evidence if str(e).strip())
    else:
        evidence_text = str(evidence or "").strip()
    if not cue or not lesson or not evidence_text:
        raise BridgeError("item 缺 cue/lesson/evidence：%r" % (item.get("cue") or item))
    try:
        confidence = float(item.get("confidence"))
    except (TypeError, ValueError):
        raise BridgeError("item confidence 非数值：%r" % (item.get("confidence"),)) from None
    if not 0.0 <= confidence <= 1.0:
        raise BridgeError("item confidence 越界：%r" % (confidence,))
    # 防穿越：证据里的路径再过一次 Z5（写库时 experience_store 也会再查）
    paths = evidence_text.split("; ")
    try:
        LG.assert_clean(paths=paths, stage="evolve")
    except LG.LeakageDetected as exc:
        raise BridgeError("证据被 Z5 拦截：%s" % exc) from None
    return {"domain": DEFAULT_DOMAIN, "cue": cue, "lesson": lesson,
            "evidence": evidence_text, "confidence": confidence}


def _next_entry_index(experience_root: Optional[Path], domain: str) -> int:
    rows = S._load_index(S._root(experience_root))
    return sum(1 for r in rows if r.get("domain") == domain)


def emit(draft_path: Path, out_dir: Path, *, ts: Optional[float] = None,
         nonce_prefix: str = "govai-case",
         experience_root: Optional[Path] = None,
         domain: str = DEFAULT_DOMAIN,
         offset: int = 0) -> list[dict]:
    """把 draft items 转成待签 payload（不签名）。

    offset：预留给"尚未入库但已预分配 id"的批次（如 7 张判例卡占用
    001..007 时，case 批次用 --offset 7 从 008 起），避免 item_id 撞车。
    """
    draft = load_draft(Path(draft_path))
    if domain not in S.WRITABLE_DOMAINS:
        raise BridgeError("域 %s 不可写（只读或未知；可写=%s）"
                          % (domain, ",".join(S.WRITABLE_DOMAINS)))
    out_dir = Path(out_dir)
    base = _next_entry_index(experience_root, domain) + max(0, int(offset))
    ts_f = float(time.time() if ts is None else ts)
    payloads: list[dict] = []
    for i, item in enumerate(draft["items"], 1):
        content = _entry_content(item)
        content["domain"] = domain
        item_id = "EXP-%s-%03d" % (domain.upper(), base + i)
        fingerprint = SG.fingerprint_of(content)
        nonce = "%s-%02d" % (nonce_prefix, i)
        canonical = SG.canonical_payload(kind=KIND, item_id=item_id,
                                         fingerprint=fingerprint, action=ACTION,
                                         ts=ts_f, nonce=nonce)
        payload = {
            "kind": KIND, "action": ACTION,
            "item_id": item_id, "fingerprint": fingerprint,
            "ts": ts_f, "nonce": nonce,
            **content,
            "source_draft": str(Path(draft_path)),
            "canonical_payload_hex": canonical.hex(),
            "canonical_payload_sha256": hashlib.sha256(canonical).hexdigest(),
        }
        _write_json(out_dir / (item_id + ".payload.json"), payload)
        payloads.append(payload)
    _write_readme(out_dir, payloads)
    return payloads


def _write_readme(out_dir: Path, payloads: list[dict]) -> None:
    lines = [
        "# GOVAI-3 判例草稿 · 待签 payload（%s）" % time.strftime("%Y-%m-%d %H:%M"),
        "",
        "> 产物**未签名**。签名必须由本人执行；私钥不经过 Agent。",
        "> 公钥文件：`C:\\项目\\_vault\\keys\\principal_pub.hex`（验签用）",
        "",
        "## 1) 本人签名（逐条，输一次口令）",
        "",
        "```powershell",
        "$env:DSH_PRINCIPAL_PASSPHRASE = '<口令>'   # 或改用 --passphrase-file",
        "cd C:\\项目\\老田的AI助手",
    ]
    for p in payloads:
        lines.append(
            "python tools\\sign_approval.py --kind experience_write "
            "--payload-file \"%s\" --private-key \"%s\" --i-am-human"
            % (out_dir / (p["item_id"] + ".payload.json"), DEFAULT_KEY))
    lines += [
        "```",
        "",
        "## 2) 批量签名（推荐：一条命令签完本目录全部 payload，一次口令）",
        "",
        "```powershell",
        "cd C:\\项目\\老田的AI助手",
        "powershell -ExecutionPolicy Bypass -File tools\\sign_govai_batch.ps1 -Dirs \"%s\"" % out_dir,
        "```",
        "",
        "## 3) 验签入库（签名齐备后执行；需公钥 env）",
        "",
        "```powershell",
        "$env:EVOLVE_PRINCIPAL_PUBKEY_FILE = \"C:\\项目\\_vault\\keys\\principal_pub.hex\"",
        "$env:EVOLVE_SIGNATURE_STRICT = \"1\"",
        "python tools\\gov_ai_case_bridge.py --apply --signature-dir \"%s\"" % out_dir,
        "```",
        "",
        "## 清单",
        "",
    ]
    for p in payloads:
        lines.append("| %s | %s | conf %.2f | `%s…` |"
                     % (p["item_id"], p["cue"][:40], p["confidence"],
                        p["canonical_payload_sha256"][:16]))
    lines.append("")
    (Path(out_dir) / "README.md").write_text("\n".join(lines), encoding="utf-8")


def _require_verifier() -> SG.Verifier:
    verifier = SG.verifier_from_env()
    if verifier is None:
        raise BridgeError(
            "未配置 principal 公钥：请设 EVOLVE_PRINCIPAL_PUBKEY_FILE（或 HEX），"
            "否则拒绝把未验签内容写入 Z4")
    return verifier


def apply(signature_dir: Path, *, experience_root: Optional[Path] = None,
          draft_path: Optional[Path] = None) -> list[dict]:
    """验签并入 Z4（supervision）；签名缺失/不匹配/未配置公钥一律拒绝。"""
    _require_verifier()
    sig_dir = Path(signature_dir)
    payload_files = sorted(sig_dir.glob("EXP-*.payload.json"))
    if not payload_files:
        raise BridgeError("签名目录无 payload：%s（先 --emit）" % sig_dir)
    if draft_path is not None:
        load_draft(Path(draft_path))  # 存在性/结构自检

    # 预检 id 漂移（域内新增条目会让 write_entry 重算 id，签名就对不上了）
    first = _read_json(payload_files[0])
    domain = str(first.get("domain") or DEFAULT_DOMAIN)
    base = _next_entry_index(experience_root, domain)
    for i, payload_path in enumerate(payload_files, 1):
        payload = _read_json(payload_path)
        expected = "EXP-%s-%03d" % (domain.upper(), base + i)
        if payload.get("item_id") != expected:
            raise BridgeError(
                "entry_id 漂移：payload=%s 期望=%s（域内新增条目后需重新 --emit）"
                % (payload.get("item_id"), expected))

    results: list[dict] = []
    for payload_path in payload_files:
        payload = _read_json(payload_path)
        item_id = payload.get("item_id") or payload_path.name.split(".")[0]
        # 兼容两种签名命名：<item>.signature.json（本模块约定）与
        # <item>.payload.json.signature.json（sign_approval.py 默认输出，
        # 即 "<payload 文件>.signature.json"）——GOVAI3FIX2 实证踩到。
        sig_candidates = [sig_dir / (item_id + ".signature.json"),
                          sig_dir / (item_id + ".payload.json.signature.json")]
        sig_path = next((c for c in sig_candidates if c.is_file()), None)
        if sig_path is None:
            raise BridgeError("缺少签名：%s（期望 %s 或 %s）"
                              % (item_id, sig_candidates[0].name,
                                 sig_candidates[1].name))
        signature = _read_json(sig_path)
        entry = S.write_entry(
            domain=payload["domain"], cue=payload["cue"], lesson=payload["lesson"],
            evidence=payload["evidence"], confidence=payload["confidence"],
            signed_by=SIGNED_BY, root=experience_root, signature=signature,
            now=payload.get("ts"))
        if entry.entry_id != item_id:
            raise BridgeError(
                "entry_id 漂移：payload=%s 实际=%s（域内新增条目后需重新 --emit）"
                % (item_id, entry.entry_id))
        if not entry.verified:
            raise BridgeError("%s 写入后 verified=False（%s）" % (item_id, entry.signature_reason))
        results.append(entry.as_dict())
    return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="GOVAI-3：AI 判例草稿 → 待签 payload → 验签入库")
    ap.add_argument("--draft", default="", help="case_draft.json（缺省取最新）")
    ap.add_argument("--emit", action="store_true", help="生成待签 payload")
    ap.add_argument("--apply", action="store_true", help="验签并写入 Z4")
    ap.add_argument("--out", default="", help="payload/签名目录（--emit 输出）")
    ap.add_argument("--signature-dir", default="", help="签名目录（--apply 输入）")
    ap.add_argument("--experience-root", default="", help="Z4 根（默认私有区）")
    ap.add_argument("--domain", default=DEFAULT_DOMAIN)
    ap.add_argument("--offset", type=int, default=0,
                    help="预分配 id 偏移（如 7 卡占 001-007 时传 7，case 批次从 008 起）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    draft = Path(args.draft) if args.draft else latest_case_draft()
    out_dir = Path(args.out) if args.out else \
        _REPO / "_vault" / "sign_requests" / ("govai_case_" + time.strftime("%Y%m%d"))
    sig_dir = Path(args.signature_dir) if args.signature_dir else out_dir
    root = Path(args.experience_root) if args.experience_root else None
    try:
        if args.emit and args.apply:
            ap.error("--emit 与 --apply 不能同时使用")
        if args.emit:
            payloads = emit(draft, out_dir, experience_root=root, domain=args.domain,
                            offset=args.offset)
            out: dict = {"ok": True, "emit": len(payloads), "out": str(out_dir),
                         "items": [{"item_id": p["item_id"], "cue": p["cue"]} for p in payloads]}
            rc = 0
        elif args.apply:
            entries = apply(sig_dir, experience_root=root, draft_path=draft)
            out = {"ok": True, "applied": len(entries),
                   "entries": [e.get("entry_id") for e in entries]}
            rc = 0
        else:
            ap.print_help()
            return 2
    except (BridgeError, S.StoreError, SG.SignatureError, SG.SignatureRequired) as exc:
        out = {"ok": False, "error": str(exc)}
        rc = 1
    print(json.dumps(out, ensure_ascii=False, indent=2) if args.json
          else json.dumps(out, ensure_ascii=False))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
