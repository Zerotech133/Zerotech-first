# -*- coding: utf-8 -*-
"""P0-05 T+1 检查（2026-11-16 09:40 一次性任务用）。

两件事：
1. **验证**：paper_ledger 里是否出现了带 `trade_id` 的账行（P0-05 从 PARTIAL→PASS 的唯一条件）；
2. **补齐**：若存在 `audience=internal` 的 pending confirmation（盯盘触发后停在人工门前），
   按自主权契约 L0 的「内部 auto 路径」执行它（`auto_execute`）——paper-only，不接实盘。

没有成交就如实报 `no_new_fill`，不造交易、不把 0 当达成。

用法::

    python -X utf8 tools/d1_t1_check.py --json
    python -X utf8 tools/d1_t1_check.py --dry-run
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

LEDGER = REPO / "agent_platform" / "data" / "paper" / "paper_ledger.jsonl"
STATE = REPO / "agent_platform" / "data" / "paper" / "state.json"
# 与 Q2 其他证据同目录（C:\项目\deliverables\d1_q2），按日期分子目录。
OUT_DIR = REPO.parent / "deliverables" / "d1_q2"
LOG = REPO / "logs" / "d1_t1_check.log"


def log(msg: str) -> None:
    line = "%s %s" % (dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def ledger_stats() -> dict:
    rows = []
    if LEDGER.is_file():
        for line in LEDGER.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    with_tid = [r for r in rows if r.get("trade_id")]
    today = dt.date.today().isoformat()
    today_rows = [r for r in rows if str(r.get("date")) == today]
    return {"rows": len(rows), "rows_with_trade_id": len(with_tid),
            "today_rows": len(today_rows),
            "today_with_trade_id": len([r for r in today_rows if r.get("trade_id")]),
            "last_trade_ids": [str(r.get("trade_id")) for r in with_tid[-5:]],
            "last_date": str(rows[-1].get("date")) if rows else None}


def pending_internal() -> list[dict]:
    try:
        from agent_platform import trading_bot_flow as flow
    except Exception as exc:  # noqa: BLE001
        log("WARN 无法导入 trading_bot_flow：%s" % type(exc).__name__)
        return []
    try:
        rows = flow.list_confirmations("pending")
    except Exception as exc:  # noqa: BLE001
        log("WARN list_confirmations 失败：%s" % type(exc).__name__)
        return []
    return [r for r in rows if str(r.get("audience")) == "internal"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="P0-05 T+1 检查（验证 + 内部 auto 补齐）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-report", action="store_true",
                    help="只打印不写 report 文件（验收自检/反复 dry-run 时用，避免刷出重复报告）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    before = ledger_stats()
    log("BEFORE rows=%d with_trade_id=%d today=%d today_with_tid=%d"
        % (before["rows"], before["rows_with_trade_id"], before["today_rows"], before["today_with_trade_id"]))

    executed = []
    pend = pending_internal()
    log("pending_internal=%d" % len(pend))
    if pend and not args.dry_run:
        from agent_platform import trading_bot_flow as flow
        for rec in pend:
            cid = rec.get("confirmation_id")
            try:
                out = flow.auto_execute(cid, by="SYSTEM:TR1",
                                        note="2026-11-16 自主权契约 L0：内部 auto 路径，T+1 检查补齐")
                executed.append({"confirmation_id": cid, "symbol": out.get("symbol"),
                                 "status": out.get("status"), "trade_id": out.get("trade_id")})
                log("EXECUTED %s %s -> %s tid=%s" % (cid, out.get("symbol"),
                                                     out.get("status"), out.get("trade_id")))
            except Exception as exc:  # noqa: BLE001
                executed.append({"confirmation_id": cid, "error": type(exc).__name__ + ": " + str(exc)[:160]})
                log("EXECUTE_FAIL %s %s" % (cid, type(exc).__name__))

    after = ledger_stats()
    ready = None
    try:
        sys.path.insert(0, str(REPO / "tools"))
        import d1_runtime_ledger_check as chk
        res = chk.check(probe_api=True)
        ready = {"ok": res.get("ok"), "ready": res.get("ready"),
                 "incomplete_books": res.get("incomplete_books"),
                 "paper_positions": (res.get("chain") or {}).get("paper_positions"),
                 "paper_trade_ids": (res.get("chain") or {}).get("paper_trade_ids")}
    except Exception as exc:  # noqa: BLE001
        ready = {"error": type(exc).__name__}

    verdict = "PASS" if after["rows_with_trade_id"] > 0 else "NO_FILL_YET"
    payload = {
        "schema": "d1_p0_05_t1_check/v1",
        "checked_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "dry_run": args.dry_run,
        "before": before, "after": after,
        "pending_internal": len(pend), "executed": executed,
        "readiness": ready,
        "p0_05_trade_id_condition": verdict,
        "note": ("账行已带 trade_id → P0-05 的成交可追条件成立；仍需 training episode 链完整"
                 if verdict == "PASS" else
                 "当日无新成交：不造交易，等盯盘触发或下一次成交后复跑本检查"),
        "paper_only": True, "not_a_trade": True,
    }
    out = OUT_DIR / dt.date.today().strftime("%Y%m%d") / ("P0-05_t1_check_%s.json"
                                                          % dt.datetime.now().strftime("%H%M%S"))
    if args.no_report:
        log("VERDICT %s (--no-report：未写报告文件)" % verdict)
    else:
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            payload["written_to"] = str(out)
        except OSError as exc:
            payload["write_error"] = type(exc).__name__
        log("VERDICT %s %s" % (verdict, out))
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
