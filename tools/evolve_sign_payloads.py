# -*- coding: utf-8 -*-
"""Z4 判例卡待签 payload 生成器（只生成 payload，不签名、不落库）。

背景（2026-10-26）：首批 7 张判例卡仍是 draft（`Z4判例卡_六条_草稿_2026-10-23.md`），
签字链从未运行过。本工具把 7 张卡映射为 Z4 supervision 域的**待签 payload**：

  · item_id 固定 EXP-SUPERVISION-001..007（要求 supervision 域为空，顺序写入）；
  · fingerprint 与 experience_store 入库时使用的口径**完全一致**
    （{"domain","cue","lesson","evidence","confidence"} 规范 JSON 的 sha256）；
  · 输出 canonical_payload_hex / sha256，供 `tools/sign_approval.py --kind experience_write`
    离线签名；本工具**不接触私钥、不执行签名**；
  · `--apply --signature-dir` 只在签名齐备时把经验写入 Z4（verified 由 signature_guard 判定），
    默认不执行。

纪律：payload 内容全部来自判例卡原文，不编造证据；confidence 取卡内自评区间中值。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

_TOOLS = Path(__file__).resolve().parent
_REPO = _TOOLS.parent
for _p in (str(_REPO / "agent_platform"), str(_TOOLS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from engine import experience_store as S  # noqa: E402
from engine import signature_guard as SG  # noqa: E402

DOMAIN = "supervision"
KIND = "experience_write"
DEFAULT_OUT = _REPO / "_vault" / "sign_requests" / "20261026"

# 7 张判例卡 → 四字段经验（cue/lesson/evidence/confidence）。
# 来源：Z4判例卡_六条_草稿_2026-10-23.md（卡 01-06 + 判例 07）。
CASES = [
    {
        "card": "Z4-01 假成功",
        "cue": "执行型脚本报成功但目标对象未被修改（假成功）",
        "lesson": "验收必须落在被改对象的可观测属性（mtime/哈希/返回值/计数），不得以命令返回 0 作验收",
        "evidence": "Z4卡01；2026-10-22 17:33 disable_zombie_tasks 报已执行而 Tasks 定义 mtime 停在 10-16/17/19；提交 1e0d3b21",
        "confidence": 0.9,
    },
    {
        "card": "Z4-02 快照假绿",
        "cue": "LastTaskResult 快照把每次失败拍成正常运行（快照假绿）",
        "lesson": "判定失败必须用窗口内计数，并区分仍活跃与已恢复；单点快照不可作门禁",
        "evidence": "Z4卡02；AlertWatch 停摆 4 天账本照写 rc=1；QuantWatchdog 712 次/天被记成正在运行；提交 820b99d3/5e76d564",
        "confidence": 0.9,
    },
    {
        "card": "Z4-03 无对照组不算统计",
        "cue": "用失败组比例归因配置却无对照组（无对照不算统计）",
        "lesson": "任何因果判断必须同时给出对照组同一比例；无对照组只能写疑似",
        "evidence": "Z4卡03；2026-10-22 junction 假说失败组 75% vs 正常组 67% 不成立已撤回；提交 57fb3b1a",
        "confidence": 0.85,
    },
    {
        "card": "Z4-04 检测器自证",
        "cue": "采信检测器读数前未用已知为真样本自证（检测器自证）",
        "lesson": "检测器必须能给出预期正例与反例；给不出反例时结论记未知、不得记正常",
        "evidence": "Z4卡04；同一晚六种形态（schtasks 假阴性/wevtutil 过滤前截断/UTC 二次转换/裸名当路径等）；提交 820b99d3/633189c7",
        "confidence": 0.9,
    },
    {
        "card": "Z4-05 修A漏B",
        "cue": "一处修复存在两个入口只改了一个（修A漏B）",
        "lesson": "跨仓/跨入口修复必须成对提交，验收覆盖另一入口（提交信息带 PAIR 标记）",
        "evidence": "Z4卡05；hermes_push 预算修复而 chain_watchdog 调用点未传预算导致 267014；kg 提交 6ee6b4d",
        "confidence": 0.85,
    },
    {
        "card": "Z4-06 窗口对比的种子时序",
        "cue": "窗口对比测试种子用 now 相对偏移导致跨 cutoff 偶发失败（种子时序）",
        "lesson": "时间敏感测试必须从 cutoff 反推时间点，并在断言前打印实际窗口边界",
        "evidence": "Z4卡06；test_window_diff_classifies 2026-10-23 22:20 偶发失败且单跑通过",
        "confidence": 0.8,
    },
    {
        "card": "Z4-07 零引用必须带对照组",
        "cue": "用零引用当删除依据而搜索方法本身静默失效（零引用必须带对照组）",
        "lesson": "零结果须用已知存在词做对照；删除依据还要看自称用途/默认参数/配套文档；恢复动作先确认写目标",
        "evidence": "Z4判例07；tushare_v2 搜索 0 引用实有 13 处，0.69GB 与 1744 文件永久丢失",
        "confidence": 0.95,
    },
]


def entry_id_for(index: int) -> str:
    return "EXP-SUPERVISION-%03d" % index


def fingerprint_for(case: dict) -> str:
    return SG.fingerprint_of({
        "domain": DOMAIN, "cue": case["cue"], "lesson": case["lesson"],
        "evidence": case["evidence"], "confidence": float(case["confidence"]),
    })


def build_payloads(now: float | None = None,
                   nonce_prefix: str = "z4-20261026") -> list[dict]:
    """构造 7 条待签 payload（含 canonical 字节与 sha256）。"""
    ts = float(time.time() if now is None else now)
    out = []
    for i, case in enumerate(CASES, 1):
        item_id = entry_id_for(i)
        fingerprint = fingerprint_for(case)
        nonce = "%s-%02d" % (nonce_prefix, i)
        canonical = SG.canonical_payload(
            kind=KIND, item_id=item_id, fingerprint=fingerprint,
            action="write", ts=ts, nonce=nonce)
        out.append({
            "card": case["card"],
            "kind": KIND,
            "item_id": item_id,
            "fingerprint": fingerprint,
            "action": "write",
            "ts": ts,
            "nonce": nonce,
            "domain": DOMAIN,
            "cue": case["cue"],
            "lesson": case["lesson"],
            "evidence": case["evidence"],
            "confidence": float(case["confidence"]),
            "canonical_payload_hex": canonical.hex(),
            "canonical_payload_sha256": hashlib.sha256(canonical).hexdigest(),
        })
    return out


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _write_readme(out: Path, payloads: list[dict]) -> None:
    lines = [
        "# 7 张判例卡 · 待签 payload 清单（%s）" % time.strftime("%Y-%m-%d"),
        "",
        "> 生成物：**未签名**。签名由本人在自己的终端执行，私钥不经过 Agent。",
        "",
        "## 签名命令（逐条）",
        "",
        "```powershell",
        "$env:DSH_PRINCIPAL_PASSPHRASE = '<口令，或改用 --passphrase-file>'",
    ]
    for p in payloads:
        lines.append(
            "python tools\\sign_approval.py --kind experience_write "
            "--payload-file \"%s\" --private-key _vault\\keys\\principal_private.pem "
            "--i-am-human" % (out / (p["item_id"] + ".payload.json")))
    lines += ["```", "", "## 入库（签名齐备后，人工执行）", "",
              "```powershell",
              "python tools\\evolve_sign_payloads.py --apply --signature-dir \"%s\"" % out,
              "```",
              "",
              "## 清单", ""]
    for p in payloads:
        lines.append("| %s | %s | conf %.2f | `%s` |"
                     % (p["item_id"], p["card"], p["confidence"],
                        p["canonical_payload_sha256"][:16] + "…"))
    lines.append("")
    (out / "README.md").write_text("\n".join(lines), encoding="utf-8")


def _check_empty(experience_root: Path | None) -> list[str]:
    rows = S._load_index(S._root(experience_root))
    return [r.get("entry_id") for r in rows if r.get("domain") == DOMAIN]


def apply_payloads(payloads: list[dict], signature_dir: Path,
                   experience_root: Path | None,
                   require_empty: bool = True) -> list[dict]:
    existing = _check_empty(experience_root)
    if require_empty and existing:
        raise ValueError("supervision 域已有条目（%s）；item_id 会漂移，拒绝按预分配 id 入库"
                         % ", ".join(str(x) for x in existing[:5]))
    results = []
    for p in payloads:
        cand = [signature_dir / (p["item_id"] + ".signature.json"),
                signature_dir / (p["item_id"] + ".payload.json.signature.json")]
        sig_file = next((c for c in cand if c.is_file()), None)
        if sig_file is None:
            raise ValueError("缺少签名：%s（期望 %s）"
                             % (p["item_id"], cand[0].name))
        signature = json.loads(sig_file.read_text(encoding="utf-8"))
        entry = S.write_entry(
            domain=DOMAIN, cue=p["cue"], lesson=p["lesson"],
            evidence=p["evidence"], confidence=p["confidence"],
            signed_by="老田", root=experience_root,
            signature=signature, now=p["ts"])
        results.append(entry.as_dict())
    return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Z4 判例卡待签 payload 生成器")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--now", type=float, default=0.0, help="签名时间戳（默认当前）")
    ap.add_argument("--nonce-prefix", default="z4-20261026")
    ap.add_argument("--apply", action="store_true", help="签名齐备后写入 Z4（人工执行）")
    ap.add_argument("--signature-dir", default="")
    ap.add_argument("--experience-root", default="")
    ap.add_argument("--allow-non-empty", action="store_true")
    args = ap.parse_args(argv)

    now = args.now or None
    payloads = build_payloads(now=now, nonce_prefix=args.nonce_prefix)
    out = Path(args.out)
    for p in payloads:
        _write_json(out / (p["item_id"] + ".payload.json"), p)
    _write_readme(out, payloads)
    print(json.dumps({"ok": True, "out": str(out), "count": len(payloads),
                      "ids": [p["item_id"] for p in payloads],
                      "note": "未签名；签名后用 --apply --signature-dir 入库"},
                     ensure_ascii=False))
    if not args.apply:
        return 0
    if not args.signature_dir:
        print("[FAIL] --apply 需要 --signature-dir", file=sys.stderr)
        return 2
    try:
        results = apply_payloads(
            payloads, Path(args.signature_dir),
            Path(args.experience_root) if args.experience_root else None,
            require_empty=not args.allow_non_empty)
    except (ValueError, OSError, RuntimeError) as exc:
        print("[FAIL] %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "applied": results}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
