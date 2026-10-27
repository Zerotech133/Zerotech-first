# -*- coding: utf-8 -*-
"""绩效账本（M4-P4）：eval_shadow J2 后验收益沉淀 + intel 阵容校准反馈。

职责（对齐 M4 总规划 P4 刀）：
1. J2 逐行明细（交易日×票 的后验收益）沉淀为 append-only 账本
   data/perf_ledger.jsonl —— perf_key = sha1(date|symbol|run_id)[:16]，
   同键同内容重放零副作用、同键异内容 ValueError（M0 契约平移）。
2. 可选挂 trace_link perf 槽位：shadow(run_id)+perf(perf_key) 两环链，
   链挂接失败留 finding 不阻断入账（账本先行判例）。
3. by_profile 聚合：按 P3 dk_* 阵容字段分组统计后验——纯观测，无门。
4. auto_draft_roster：组样本达标且后验均值越阈 → intel 裁决池自动起草
   阵容校准建议（**draft 无凭据建议权**，adopt 仍人工凭据；同统计指纹
   幂等跳过，不产生重复裁决噪音）。

红线：本模块只读决策账本与 K 线后验（由调用方喂行），不产生任何交易
指令；建议≠凭据；fail-open（单行损坏不拖垮整账）。
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_AGENT_PLATFORM = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------- 常量

SCHEMA_VERSION = 1

LEDGER_PATH_NAME = ("data", "perf_ledger.jsonl")

# perf 行必填锚点（缺失行跳过并留 finding——无源 ID 不伪造）
_ROW_REQUIRED = ("date", "symbol", "run_id", "ret_pct")

# 阵容校准起草阈值：组样本下限（低于此不起草——小样本不足为凭）
MIN_SAMPLES = 5
# 组后验均值告警阈（百分点）：均值低于此值才起草（正收益组不动阵容）
MEAN_RET_FLOOR = -0.5

# 阵容分组取值域（P3 REGIME_ROSTER profile；none=行无 dk 字段）
KNOWN_PROFILES = ("strict", "relaxed", "none")


# ---------------------------------------------------------------- 锚点


def perf_key_for(date: str, symbol: str, run_id: str) -> str:
    """perf 幂等键：sha1(date|symbol|run_id) 前 16 位。

    run_id 是决策行的稳定指纹（影子账本 done 行必有）——同一决策的
    后验随 K 线推移会变化，但 perf_key 不变：同键异值（后验更新）走
    「重放拒绝覆盖」语义，后验修正须显式修订（#r 轮次化留给 P5）。
    """
    payload = "|".join((str(date).strip(), str(symbol).strip(), str(run_id).strip()))
    if payload == "||":
        raise ValueError("perf_key 三要素全空")
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def ledger_path(root: Path = None) -> Path:
    base = Path(root) if root else _AGENT_PLATFORM
    return base.joinpath(*LEDGER_PATH_NAME)


# ---------------------------------------------------------------- 记账


def record_perf_rows(
    rows: List[Dict],
    root: Path = None,
    link_store=None,
    source: str = "eval-shadow",
    declared_at: str = "",
) -> Dict:
    """批量沉淀 J2 明细行。返回 {recorded, replayed, skipped, findings}。

    - 缺必填锚点行 → skipped + finding（不伪造）；
    - 同键同内容 → replayed（零副作用）；
    - 同键异内容 → ValueError 冒泡（后验漂移=数据异常，须人工判读，
      静默覆盖会污染阵容校准样本）；
    - link_store 提供时挂 shadow+perf 两环链，失败留 finding。
    """
    p = ledger_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    index = _load_index(p)

    recorded = replayed = skipped = 0
    findings: List[str] = []
    for r in rows or []:
        missing = [f for f in _ROW_REQUIRED if r.get(f) in (None, "", "pending")]
        if missing:
            skipped += 1
            findings.append(
                "perf 行缺锚点 %s: %s/%s" % (missing, r.get("date"), r.get("symbol"))
            )
            continue
        key = perf_key_for(r["date"], r["symbol"], r["run_id"])
        row = {
            "schema_version": SCHEMA_VERSION,
            "perf_key": key,
            "date": str(r["date"]),
            "symbol": str(r["symbol"]),
            "run_id": str(r["run_id"]),
            "score": r.get("score"),
            "confidence": r.get("confidence"),
            "ret_pct": _num(r.get("ret_pct")),
            "horizon_days": r.get("horizon"),
            "base_price": _num(r.get("base")),
            "exit_price": _num(r.get("exit")),
            "recommendation": str(r.get("recommendation", "?")),
            "stance": str(r.get("stance", "?")),
            # P3 增量字段（旧行无此组——by_profile 归 none）
            "dk_regime": r.get("dk_regime"),
            "dk_profile": r.get("dk_profile"),
            "dk_blocked": r.get("dk_blocked"),
            "source": source,
            "declared_at": declared_at,
        }
        existing = index.get(key)
        if existing is not None:
            if existing.get("ret_pct") != row["ret_pct"]:
                raise ValueError(
                    "perf 冲突：perf_key=%s 已存在但后验不一致 "
                    "(existing=%s new=%s；M0 契约：同键异值拒绝，"
                    "后验漂移须人工判读)"
                    % (key, existing.get("ret_pct"), row["ret_pct"])
                )
            replayed += 1
        else:
            index[key] = row
            with p.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            recorded += 1
        # 链挂接（账本先行：行已入账，链失败只留痕）
        if link_store is not None:
            chain = {"perf": {"perf_key": key}}
            if str(r.get("run_id", "")).strip():
                chain["shadow"] = {
                    "run_id": str(r["run_id"]),
                    "symbol": str(r["symbol"]),
                    "trade_date": str(r["date"])[:10],
                }
            try:
                link_store.record(chain, source=source, declared_at=declared_at)
            except ValueError as exc:
                findings.append("trace 链挂接失败（perf 已入账）: %s" % exc)
    return {
        "recorded": recorded,
        "replayed": replayed,
        "skipped": skipped,
        "findings": findings,
    }


def _num(v):
    try:
        return round(float(v), 6) if v is not None else None
    except (TypeError, ValueError):
        return None


def _load_index(p: Path) -> Dict[str, Dict]:
    index: Dict[str, Dict] = {}
    if not p.exists():
        return index
    with p.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                if row.get("perf_key"):
                    index[row["perf_key"]] = row
            except json.JSONDecodeError:
                continue  # 坏行容忍（fail-open，P5 回放器负责亮牌）
    return index


def load_perf_rows(root: Path = None) -> List[Dict]:
    return list(_load_index(ledger_path(root)).values())


# ---------------------------------------------------------------- 聚合


def by_profile(rows: List[Dict] = None, root: Path = None) -> Dict[str, Dict]:
    """按阵容分组统计后验：profile -> {n, mean_ret, win_rate, neg_streak_ok}。

    纯观测聚合，无门无建议权；行无 dk_profile 字段归 "none"。
    """
    if rows is None:
        rows = load_perf_rows(root)
    groups: Dict[str, List[float]] = {}
    for r in rows:
        prof = r.get("dk_profile") or "none"
        groups.setdefault(prof, []).append(r.get("ret_pct") or 0.0)
    out = {}
    for prof, rets in sorted(groups.items()):
        n = len(rets)
        out[prof] = {
            "n": n,
            "mean_ret": round(sum(rets) / n, 4),
            "win_rate": round(sum(1 for x in rets if x > 0) / n, 4),
        }
    return out


# ---------------------------------------------------------------- intel 反馈


def _roster_job_key(stats: Dict[str, Dict]) -> str:
    """阵容校准作业指纹：统计态变化即新作业（同态重跑幂等跳过）。"""
    payload = json.dumps(stats, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]


def auto_draft_roster(
    root: Path = None,
    by: str = "agent",
    min_samples: int = MIN_SAMPLES,
    mean_floor: float = MEAN_RET_FLOOR,
) -> Tuple[list, list]:
    """阵容校准建议自动起草（intel draft 无凭据建议权）。

    触发规则（阈值显式、可回放）：
    - relaxed 组 n>=min 且 mean_ret<floor → degrade（放宽阵容在后验
      亏损 → 建议收回放宽、回落 strict）；
    - strict 组 n>=min 且 mean_ret<floor → watch（稳健阵容亏损多为
      市场系统性回落，只观察不动阵容——阵容表自身无恙不轻动）。

    返回 (drafted_entries, findings)；同统计指纹已起草过则空列表返回
    （幂等，不产生重复裁决）。intel_wiring 缺席时降级留 finding。
    """
    findings: List[str] = []
    try:
        from domains.intel_wiring import draft, verdict_id_for, load_pool
    except ImportError as exc:
        return [], ["intel_wiring 不可达，起草降级: %s" % exc]

    stats = by_profile(root=root)
    jkey = _roster_job_key(stats)
    drafted: List[Dict] = []
    pool_ids = {e.get("verdict_id") for e in load_pool(root) if "_error" not in e}

    for prof, s in stats.items():
        if prof not in ("strict", "relaxed"):
            continue
        if s["n"] < min_samples or s["mean_ret"] >= mean_floor:
            continue
        decision = "degrade" if prof == "relaxed" else "watch"
        vid = verdict_id_for("agent_platform/perf_ledger", jkey + "|" + prof)
        if vid in pool_ids:
            continue  # 同作业同组已起草——幂等跳过
        entry = {
            "verdict_id": vid,
            "table_urn": "agent_platform/perf_ledger",
            "job_run_id": jkey + "|" + prof,
            "break_kind": "stale",
            "decision": decision,
            "impact": ["roster_profile:%s" % prof],
            "rationale": (
                "阵容=%s 样本 n=%d 后验均值 %.2f%%（阈值 %.2f%%，"
                "win_rate=%.0f%%）——%s。来源 M4-P4 perf_ledger "
                "自动起草（draft 建议权，adopt 须人工凭据）。"
                % (
                    prof,
                    s["n"],
                    s["mean_ret"],
                    mean_floor,
                    s["win_rate"] * 100,
                    "建议收回放宽回落 strict"
                    if decision == "degrade"
                    else "稳健阵容亏损多为系统性回落，仅观察不动阵容",
                )
            ),
        }
        try:
            drafted.append(draft(root, entry, by=by))
        except Exception as exc:  # noqa: BLE001
            findings.append("roster 起草失败(%s): %s" % (prof, exc))
    return drafted, findings


# ---------------------------------------------------------------- 全链回放


def perf_chain_view(store, target: str, root: Path = None) -> Dict:
    """四环全链回放（M4-P5 总验收句的 perf 版）。

    target = run_id / trace_id / order_id / perf_key 任一锚点值 →
    trace_link 命中链聚合 + perf 行明细 + 阵容分组快照。
    verdict: OK / NOT_FOUND；perf 行按命中的 perf_key 关联（无行留
    perf_rows=[]，不伪造——链上锚点是登记态，明细以账本为准）。
    """
    from adapters.trace_link import replay_trace

    rep = replay_trace(store, target)
    if rep.get("verdict") != "OK":
        return {
            "verdict": rep["verdict"],
            "matches": [],
            "perf_rows": [],
            "profile_stats": {},
            "findings": rep.get("findings", []),
        }
    hit_keys = set()
    for m in rep["matches"]:
        ref = m["chain"].get("perf") or {}
        if ref.get("perf_key"):
            hit_keys.add(ref["perf_key"])
    all_rows = load_perf_rows(root)
    perf_rows = [r for r in all_rows if r["perf_key"] in hit_keys]
    return {
        "verdict": "OK",
        "matches": rep["matches"],
        "perf_rows": perf_rows,
        "profile_stats": by_profile(perf_rows),
        "findings": [],
    }


# ---------------------------------------------------------------- CLI


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="M4-P4 绩效账本工具")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("stats", help="账本规模 + 阵容分组后验")
    p_ls = sub.add_parser("ls", help="最近明细")
    p_ls.add_argument("--limit", type=int, default=20)
    ap.add_argument("--root", default="")
    args, rest = ap.parse_known_args(argv)
    if not args.root and "--root" in rest:  # 子命令后位置的 --root 兼容
        i = rest.index("--root")
        if i + 1 < len(rest):
            args.root = rest[i + 1]
    root = Path(args.root) if args.root else None
    if args.cmd == "stats":
        # M5-P2 演示隔离安全阀：stats 含写侧起草（auto_draft_roster 会落
        # intel_verdicts + trace_links），无显式 --root 拒绝执行，防止验收
        # 演示污染真仓（复发两次的判例：演示永不落真仓红线）。
        if not args.root:
            print(
                "[安全阀 M5-P2] stats 含写侧起草（auto_draft_roster），"
                "无显式 --root 拒绝落真仓。生产判读：--root "
                "<agent_platform 根>；演示：--root <tmp 目录>",
                file=sys.stderr,
            )
            return 2
        rows = load_perf_rows(root)
        print("perf_ledger rows=%d" % len(rows))
        for prof, s in by_profile(rows).items():
            print(
                "  %-8s n=%-4d mean_ret=%+7.2f%% win=%.0f%%"
                % (prof, s["n"], s["mean_ret"], s["win_rate"] * 100)
            )
        drafted, findings = auto_draft_roster(root)
        for d in drafted:
            print(
                "  [draft] %s %s (%s)" % (d["decision"], d["verdict_id"], d["impact"])
            )
        for f in findings:
            print("  [finding] %s" % f)
        return 0
    rows = load_perf_rows(root)
    for r in rows[-args.limit :]:
        print(
            r["date"],
            r["symbol"],
            "ret=%+.2f%%" % (r["ret_pct"] or 0),
            "prof=%s" % (r.get("dk_profile") or "none"),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
