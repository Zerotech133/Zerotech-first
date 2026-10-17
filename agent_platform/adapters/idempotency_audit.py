# -*- coding: utf-8 -*-
"""M0 P3 幂等基线只读审计器。

按 docs/audit/2026-10-17_统一幂等语义盘点.md 实施顺序第 1 步：

- 扫描 Paper 旧库 orders 表：总订单、空 ref、重复 ref、跨账户同 ref 冲突；
- 扫描 agent_platform/data/shadow/shadow_ledger.jsonl：状态分布、
  parse_ok=false 的 done、date+symbol 重复 done；
- 只读：SQLite mode=ro；缺失数据源显式报告 missing_source；
- 输出 JSON 报告，绝不修改任何事实源。

用法:
  python agent_platform/adapters/idempotency_audit.py \
      --root <项目根> [--paper-db <paper.db>] --output reports/m0_idempotency_baseline.json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

SCHEMA_VERSION = "1.0"
READ_ONLY = True


def _audit_paper_db(db_path: Path) -> dict:
    out = {
        "source": "paper_db",
        "path": str(db_path),
        "exists": db_path.exists(),
        "read_only": True,
    }
    if not db_path.exists():
        out["status"] = "missing_source"
        out["orders_total"] = 0
        return out
    try:
        con = sqlite3.connect("file:%s?mode=ro" % db_path.as_posix(), uri=True)
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(
                "SELECT ref, account_id FROM orders"
            ).fetchall()
        finally:
            con.close()
    except sqlite3.Error as exc:
        out["status"] = "read_error"
        out["error"] = str(exc)
        out["orders_total"] = 0
        return out

    total = len(rows)
    empty_refs = [r["account_id"] or "" for r in rows if not (r["ref"] or "").strip()]
    ref_counter = Counter(r["ref"] for r in rows if (r["ref"] or "").strip())
    dup_refs = {ref: n for ref, n in ref_counter.items() if n > 1}
    # 跨账户同 ref：同一 ref 出现且涉及多个 account_id
    ref_accounts = {}
    for r in rows:
        ref = (r["ref"] or "").strip()
        if not ref:
            continue
        ref_accounts.setdefault(ref, set()).add(r["account_id"] or "")
    cross_account = {
        ref: sorted(accs) for ref, accs in ref_accounts.items() if len(accs) > 1
    }
    out.update(
        {
            "status": "ok",
            "orders_total": total,
            "empty_ref_count": len(empty_refs),
            "duplicate_ref_count": len(dup_refs),
            "duplicate_refs_sample": dict(sorted(dup_refs.items())[:20]),
            "cross_account_ref_count": len(cross_account),
            "cross_account_refs_sample": dict(sorted(cross_account.items())[:20]),
        }
    )
    return out


def _audit_shadow_ledger(ledger_path: Path) -> dict:
    out = {
        "source": "shadow_ledger",
        "path": str(ledger_path),
        "exists": ledger_path.exists(),
        "read_only": True,
    }
    if not ledger_path.exists():
        out["status"] = "missing_source"
        out["total"] = 0
        return out
    status_counter = Counter()
    done_pairs = Counter()
    dup_done_pairs = {}
    parse_fail_done = 0
    bad_lines = 0
    total = 0
    try:
        text = ledger_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        out["status"] = "read_error"
        out["error"] = str(exc)
        out["total"] = 0
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            bad_lines += 1
            continue
        if not isinstance(rec, dict):
            bad_lines += 1
            continue
        total += 1
        st = str(rec.get("status", ""))
        status_counter[st] += 1
        pair = (str(rec.get("date", "")), str(rec.get("symbol", "")))
        if st == "done":
            done_pairs[pair] += 1
            if rec.get("parse_ok") is False:
                parse_fail_done += 1
    for pair, n in done_pairs.items():
        if n > 1:
            dup_done_pairs["%s|%s" % pair] = n
    out.update(
        {
            "status": "ok",
            "total": total,
            "by_status": dict(status_counter),
            "bad_lines": bad_lines,
            "parse_fail_done_count": parse_fail_done,
            # T3 产品决策（2026-10-17 拍板）：done 且 parse_ok=false 语义 =
            # 「降级完成」（done_degraded），保留 done 状态零迁移，审计不再
            # 阻塞——只计数供评估观测。见 docs/audit/2026-10-17_T3产品决策_parse_ok语义.md
            "parse_fail_policy": "done_degraded",
            # T3 产品决策（2026-10-17 拍板）：done 且 parse_ok=false 语义 =
            # 「降级完成」（done_degraded），保留 done 状态零迁移，审计不再
            # 阻塞——只计数供评估观测。见 docs/audit/2026-10-17_T3产品决策_parse_ok语义.md
            "parse_fail_policy": "done_degraded",
            "duplicate_done_pair_count": len(dup_done_pairs),
            "duplicate_done_pairs_sample": dict(sorted(dup_done_pairs.items())[:20]),
        }
    )
    return out


def _audit_credential_trace(root: Path) -> dict:
    """M1 审查收官 + M3-P4：凭据轮次 vs 真源痕迹交叉校验（只读）。

    覆盖 last_wiring_key 消费标记派三域（gzh/invest/devtask）：
    - trace_key_missing：真源条目引用的 last_wiring_key 在账本中不存在
      （FAIL——真源引用了不存在的凭据：痕迹伪造或账本损坏）；
    - credential_ahead：账本中该 target 最新凭据与真源 last_wiring_key
      不一致且最新凭据 to_status ≠ 真源 status（WARN——凭据领先真源，
      崩溃窗口未 catch-up 或真源被外部改动，需人工确认）；
    - credential_without_entry：账本有凭据但真源条目已消失（FAIL）。

    trace 痕迹派（academy/pitch/dev/skill）由各域 audit_report() 内部
    走 adapters.credential_trace.cross_check（M3-P4a 收编：原声明
    pending 的 academy/pitch 已由域内实现），本函数只读聚合其
    cross_check 结果——root 下不存在 agent_platform/domains（tmp 隔离
    仓）时声明 not_applicable 跳过，保持测试封闭。
    """
    out = {
        "source": "credential_trace",
        "status": "ok",
        "covered_domains": ["gzh", "invest", "devtask"],
        "trace_style_domains": {},
        "pending_domains": [],
        "checks": 0,
        "violations": [],
        "warnings": [],
    }

    def _viol(kind, target, detail):
        out["violations"].append(
            {"kind": kind, "target": target, "detail": detail})

    def _warn(kind, target, detail):
        out["warnings"].append(
            {"kind": kind, "target": target, "detail": detail})

    db = root / "agent_platform" / "data" / "approvals" / "approvals.db"
    by_target = {}
    if not db.exists():
        # 账本缺失不提前 return：真源侧校验照走（真源声明消费痕迹而
        # 账本不存在 = 更严重违规，全部走 credential_without_entry）
        out["status"] = "missing_source"
    else:
        conn = sqlite3.connect("file:%s?mode=ro" % db.as_posix(), uri=True)
        try:
            rows = conn.execute(
                "SELECT target_id, idempotency_key, to_status, created_at,"
                " rowid FROM approvals ORDER BY rowid").fetchall()
        finally:
            conn.close()
        for tid, key, to_st, cat, rid in rows:
            by_target.setdefault(tid, []).append(
                {"key": key, "to_status": to_st, "created_at": cat,
                 "rowid": rid})

    # --- 真源快照（单次读取，写并发下解析失败如实报告） ---
    snapshots = []   # (domain, target_id, status, last_wiring_key)
    gzh_path = root / "gzh" / "drafts" / "index.json"
    if gzh_path.exists():
        try:
            for it in json.loads(gzh_path.read_text(encoding="utf-8")):
                snapshots.append(("gzh", "gzh:%s" % it.get("id"),
                                  it.get("status"),
                                  it.get("last_wiring_key")))
        except (json.JSONDecodeError, OSError) as exc:
            out["status"] = "source_error"
            _viol("source_unreadable", str(gzh_path), str(exc)[:120])
    inv_path = root / "agent_platform" / "data" / "invest" / "runs_index.json"
    if inv_path.exists():
        try:
            for e in json.loads(inv_path.read_text(encoding="utf-8")):
                snapshots.append(("invest", "invest:%s" % e.get("run_id"),
                                  e.get("status"),
                                  e.get("last_wiring_key")))
        except (json.JSONDecodeError, OSError) as exc:
            out["status"] = "source_error"
            _viol("source_unreadable", str(inv_path), str(exc)[:120])
    dt_path = root / ".worktrees" / "tasks.json"
    if dt_path.exists():
        try:
            for tid, t in json.loads(
                    dt_path.read_text(encoding="utf-8")).items():
                snapshots.append(("devtask", "devtask:%s" % tid,
                                  t.get("status"),
                                  t.get("last_wiring_key")))
        except (json.JSONDecodeError, OSError) as exc:
            out["status"] = "source_error"
            _viol("source_unreadable", str(dt_path), str(exc)[:120])

    for domain, target, status, last_key in snapshots:
        out["checks"] += 1
        creds = by_target.get(target)
        if last_key:
            hit = any(c["key"] == last_key for c in (creds or []))
            if not hit:
                _viol("trace_key_missing", target,
                      "真源 last_wiring_key=%s 在账本中不存在" % last_key)
        if not creds:
            # 无凭据：仅当真源声明过消费痕迹才算异常（存量条目可无凭据）
            if last_key:
                _viol("credential_without_entry", target,
                      "真源声明 last_wiring_key 但账本无该 target 任何凭据")
            continue
        latest = creds[-1]
        if last_key and latest["key"] != last_key:
            # 最新凭据与消费标记不一致：看状态是否对齐
            if latest["to_status"] != status:
                _warn("credential_ahead", target,
                      "账本最新凭据 %s(to=%s) 未被真源消费(last=%s,"
                      "status=%s)——崩溃窗口或外部改动" %
                      (latest["key"], latest["to_status"], last_key, status))

    if out["violations"]:
        out["status"] = ("source_error" if out["status"] == "source_error"
                         else "violations_found")
    elif out["status"] == "missing_source":
        # 账本缺失但真源也无消费声明 = 双侧一致（空对空），不判异常；
        # missing_source 保留为声明性字段供上层知晓数据覆盖面
        out["ledger_missing"] = True
        out["status"] = "ok"

    # --- trace 痕迹派聚合（M3-P4a）：域内 cross_check 结果只读汇集 ---
    # root 无 agent_platform/domains（tmp 隔离仓）→ not_applicable，
    # 不碰域模块默认路径，保证测试封闭。
    if not (root / "agent_platform" / "domains").is_dir():
        out["trace_style_domains"] = "not_applicable"
    else:
        import importlib
        for dname in ("academy", "pitch", "dev", "skill", "intel"):
            try:
                import inspect
                mod = importlib.import_module("domains.%s_wiring" % dname)
                if "root" in inspect.signature(
                        mod.audit_report).parameters:
                    rep = mod.audit_report(root)
                else:
                    rep = mod.audit_report()
                cross = rep.get("cross_check") or []
                out["trace_style_domains"][dname] = {
                    "verdict": rep.get("verdict"),
                    "cross_violations": len(cross),
                }
                for v in cross:
                    bucket = (out["violations"]
                              if v.get("severity", "FAIL") == "FAIL"
                              else out["warnings"])
                    bucket.append({
                        "kind": v.get("kind", "cross_check_violation"),
                        "target": v.get("target_id"),
                        "detail": "[%s] %s" % (dname, v.get("detail", "")),
                    })
                if (any(v.get("severity", "FAIL") == "FAIL" for v in cross)
                        and out["status"] == "ok"):
                    out["status"] = "violations_found"
            except Exception as exc:   # 域缺失/接口变化如实声明，不吞
                out["trace_style_domains"][dname] = {
                    "status": "unavailable", "error": str(exc)[:120]}
    return out


def _audit_credential_trace(root: Path) -> dict:
    """M1 审查收官 + M3-P4：凭据轮次 vs 真源痕迹交叉校验（只读）。

    覆盖 last_wiring_key 消费标记派三域（gzh/invest/devtask）：
    - trace_key_missing：真源条目引用的 last_wiring_key 在账本中不存在
      （FAIL——真源引用了不存在的凭据：痕迹伪造或账本损坏）；
    - credential_ahead：账本中该 target 最新凭据与真源 last_wiring_key
      不一致且最新凭据 to_status ≠ 真源 status（WARN——凭据领先真源，
      崩溃窗口未 catch-up 或真源被外部改动，需人工确认）；
    - credential_without_entry：账本有凭据但真源条目已消失（FAIL）。

    trace 痕迹派（academy/pitch/dev/skill）由各域 audit_report() 内部
    走 adapters.credential_trace.cross_check（M3-P4a 收编：原声明
    pending 的 academy/pitch 已由域内实现），本函数只读聚合其
    cross_check 结果——root 下不存在 agent_platform/domains（tmp 隔离
    仓）时声明 not_applicable 跳过，保持测试封闭。
    """
    out = {
        "source": "credential_trace",
        "status": "ok",
        "covered_domains": ["gzh", "invest", "devtask"],
        "trace_style_domains": {},
        "pending_domains": [],
        "checks": 0,
        "violations": [],
        "warnings": [],
    }

    def _viol(kind, target, detail):
        out["violations"].append(
            {"kind": kind, "target": target, "detail": detail})

    def _warn(kind, target, detail):
        out["warnings"].append(
            {"kind": kind, "target": target, "detail": detail})

    db = root / "agent_platform" / "data" / "approvals" / "approvals.db"
    by_target = {}
    if not db.exists():
        # 账本缺失不提前 return：真源侧校验照走（真源声明消费痕迹而
        # 账本不存在 = 更严重违规，全部走 credential_without_entry）
        out["status"] = "missing_source"
    else:
        conn = sqlite3.connect("file:%s?mode=ro" % db.as_posix(), uri=True)
        try:
            rows = conn.execute(
                "SELECT target_id, idempotency_key, to_status, created_at,"
                " rowid FROM approvals ORDER BY rowid").fetchall()
        finally:
            conn.close()
        for tid, key, to_st, cat, rid in rows:
            by_target.setdefault(tid, []).append(
                {"key": key, "to_status": to_st, "created_at": cat,
                 "rowid": rid})

    # --- 真源快照（单次读取，写并发下解析失败如实报告） ---
    snapshots = []   # (domain, target_id, status, last_wiring_key)
    gzh_path = root / "gzh" / "drafts" / "index.json"
    if gzh_path.exists():
        try:
            for it in json.loads(gzh_path.read_text(encoding="utf-8")):
                snapshots.append(("gzh", "gzh:%s" % it.get("id"),
                                  it.get("status"),
                                  it.get("last_wiring_key")))
        except (json.JSONDecodeError, OSError) as exc:
            out["status"] = "source_error"
            _viol("source_unreadable", str(gzh_path), str(exc)[:120])
    inv_path = root / "agent_platform" / "data" / "invest" / "runs_index.json"
    if inv_path.exists():
        try:
            for e in json.loads(inv_path.read_text(encoding="utf-8")):
                snapshots.append(("invest", "invest:%s" % e.get("run_id"),
                                  e.get("status"),
                                  e.get("last_wiring_key")))
        except (json.JSONDecodeError, OSError) as exc:
            out["status"] = "source_error"
            _viol("source_unreadable", str(inv_path), str(exc)[:120])
    dt_path = root / ".worktrees" / "tasks.json"
    if dt_path.exists():
        try:
            for tid, t in json.loads(
                    dt_path.read_text(encoding="utf-8")).items():
                snapshots.append(("devtask", "devtask:%s" % tid,
                                  t.get("status"),
                                  t.get("last_wiring_key")))
        except (json.JSONDecodeError, OSError) as exc:
            out["status"] = "source_error"
            _viol("source_unreadable", str(dt_path), str(exc)[:120])

    for domain, target, status, last_key in snapshots:
        out["checks"] += 1
        creds = by_target.get(target)
        if last_key:
            hit = any(c["key"] == last_key for c in (creds or []))
            if not hit:
                _viol("trace_key_missing", target,
                      "真源 last_wiring_key=%s 在账本中不存在" % last_key)
        if not creds:
            # 无凭据：仅当真源声明过消费痕迹才算异常（存量条目可无凭据）
            if last_key:
                _viol("credential_without_entry", target,
                      "真源声明 last_wiring_key 但账本无该 target 任何凭据")
            continue
        latest = creds[-1]
        if last_key and latest["key"] != last_key:
            # 最新凭据与消费标记不一致：看状态是否对齐
            if latest["to_status"] != status:
                _warn("credential_ahead", target,
                      "账本最新凭据 %s(to=%s) 未被真源消费(last=%s,"
                      "status=%s)——崩溃窗口或外部改动" %
                      (latest["key"], latest["to_status"], last_key, status))

    if out["violations"]:
        out["status"] = ("source_error" if out["status"] == "source_error"
                         else "violations_found")
    elif out["status"] == "missing_source":
        # 账本缺失但真源也无消费声明 = 双侧一致（空对空），不判异常；
        # missing_source 保留为声明性字段供上层知晓数据覆盖面
        out["ledger_missing"] = True
        out["status"] = "ok"

    # --- trace 痕迹派聚合（M3-P4a）：域内 cross_check 结果只读汇集 ---
    # root 无 agent_platform/domains（tmp 隔离仓）→ not_applicable，
    # 不碰域模块默认路径，保证测试封闭。
    if not (root / "agent_platform" / "domains").is_dir():
        out["trace_style_domains"] = "not_applicable"
    else:
        import importlib
        for dname in ("academy", "pitch", "dev", "skill", "intel"):
            try:
                import inspect
                mod = importlib.import_module("domains.%s_wiring" % dname)
                if "root" in inspect.signature(
                        mod.audit_report).parameters:
                    rep = mod.audit_report(root)
                else:
                    rep = mod.audit_report()
                cross = rep.get("cross_check") or []
                out["trace_style_domains"][dname] = {
                    "verdict": rep.get("verdict"),
                    "cross_violations": len(cross),
                }
                for v in cross:
                    bucket = (out["violations"]
                              if v.get("severity", "FAIL") == "FAIL"
                              else out["warnings"])
                    bucket.append({
                        "kind": v.get("kind", "cross_check_violation"),
                        "target": v.get("target_id"),
                        "detail": "[%s] %s" % (dname, v.get("detail", "")),
                    })
                if (any(v.get("severity", "FAIL") == "FAIL" for v in cross)
                        and out["status"] == "ok"):
                    out["status"] = "violations_found"
            except Exception as exc:   # 域缺失/接口变化如实声明，不吞
                out["trace_style_domains"][dname] = {
                    "status": "unavailable", "error": str(exc)[:120]}
    return out


def build_report(root: Path, paper_db: Path | None = None) -> dict:
    root = Path(root)
    paper_path = paper_db or (root / "k_stock" / "data" / "paper.db")
    ledger_path = root / "agent_platform" / "data" / "shadow" / "shadow_ledger.jsonl"
    paper = _audit_paper_db(paper_path)
    shadow = _audit_shadow_ledger(ledger_path)
    blockers = []
    if paper["status"] == "ok" and paper.get("duplicate_ref_count"):
        blockers.append("paper_db has duplicate refs (must migrate via mapping, not reuse)")
    if paper["status"] == "ok" and paper.get("empty_ref_count"):
        blockers.append(
            "paper_db has empty-ref orders (manual orders; cannot be used as idempotency key)"
        )
    if shadow["status"] == "ok" and shadow.get("duplicate_done_pair_count"):
        blockers.append(
            "shadow ledger has duplicate date+symbol done rows (concurrency risk confirmed)"
        )
    # T3 摘牌（2026-10-17）：parse_ok=false 的 done 不再阻塞，语义=done_degraded，
    # 仅计数（parse_fail_done_count）供评估观测；见 docs/audit T3 产品决策。
        blockers.append(
            "shadow ledger has duplicate date+symbol done rows (concurrency risk confirmed)"
        )
    # T3 摘牌（2026-10-17）：parse_ok=false 的 done 不再阻塞，语义=done_degraded，
    # 仅计数（parse_fail_done_count）供评估观测；见 docs/audit T3 产品决策。
    trace = _audit_credential_trace(root)
    if trace["status"] == "violations_found":
        blockers.append(
            "credential trace cross-check failed: %d violation(s)" %
            len(trace["violations"]))
    return {
        "schema_version": SCHEMA_VERSION,
        "read_only": READ_ONLY,
        "root": str(root),
        "sources": {"paper_db": paper, "shadow_ledger": shadow,
                    "credential_trace": trace},
        "blockers": blockers,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="M0 幂等基线只读审计")
    parser.add_argument("--root", default=".", help="项目根目录")
    parser.add_argument("--paper-db", default="", help="Paper orders DB 路径（可选）")
    parser.add_argument("--output", default="", help="报告输出路径（可选，默认仅打印）")
    args = parser.parse_args(argv)

    report = build_report(
        Path(args.root), Path(args.paper_db) if args.paper_db else None
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
