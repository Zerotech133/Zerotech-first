# -*- coding: utf-8 -*-
"""D1 日终批量裁决 + 交棒（2026-11-16 WO-D1-DAILY-BATCH）。

每天开盘前（计划任务 09:05）跑一轮：
1. 取最近一场投研会审（`pool_v2_sessions.jsonl` 最后一条）的精选池；
2. 按聚合分取 Top N，走 **真实 HTTP** 落 D1 裁决（`POST /api/d1/decision`）；
3. 有收盘价的情况下，再走 `POST /api/d1/handoff` 交棒 WA1（带止损/止盈 watch_plan）；
4. 全部幂等：Key 绑定「池日 + 代码」，重跑返回原记录，账本不新增。

为什么走 API 而不是直写文件：Q2 的 3 个写接口此前 POST 数为 0（业务绕开 API），
SLO 只能判 NOT_MEASURED。本任务让 D1 的日常动作天然产生真实样本。

用法::

    python -X utf8 tools/d1_daily_batch.py --dry-run
    python -X utf8 tools/d1_daily_batch.py --top 5 --json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TOKEN_FILE = Path(r"C:\项目\_vault\secrets\portal_api_token")
SESSION_LEDGER = REPO / "k_stock" / "data" / "invest_committee" / "pool_v2_sessions.jsonl"
DECISIONS = REPO / "agent_platform" / "data" / "d1_decisions.jsonl"
LOG = REPO / "logs" / "d1_daily_batch.log"
BASE = "http://127.0.0.1:8091"
TIER_BY_SCORE = [(0.85, "STRONG_BUY"), (0.60, "BUY"), (0.40, "HOLD")]
QUOTE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=%s,day,,,5,qfq"


def log(msg: str) -> None:
    line = "%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def tier_of(score: float) -> str:
    for floor, tier in TIER_BY_SCORE:
        if score >= floor:
            return tier
    return "HOLD"


def gid(code: str) -> str:
    c = str(code)
    return ("sh" if c.endswith(".SH") else "sz") + c.split(".")[0]


def last_close(code: str) -> float | None:
    """腾讯日线最近一根收盘；取不到返回 None（不造价）。"""
    try:
        url = QUOTE_URL % gid(code)
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url, timeout=12) as r:
            doc = json.loads(r.read().decode("utf-8"))
        node = (doc.get("data") or {}).get(gid(code)) or {}
        bars = node.get("qfqday") or node.get("day") or []
        if not bars:
            return None
        return float(bars[-1][2])
    except Exception:  # noqa: BLE001
        return None


def token() -> str:
    return TOKEN_FILE.read_text(encoding="utf-8").strip()


#: 写端点有「每 IP 每分钟 ≤12 次」的限流；一轮最多 2×top 次写，按此节流。
DEFAULT_INTERVAL_SEC = 6.0
_last_post_at = [0.0]


def _throttle(interval: float) -> None:
    if interval <= 0:
        return
    gap = time.time() - _last_post_at[0]
    if _last_post_at[0] and gap < interval:
        time.sleep(interval - gap)
    _last_post_at[0] = time.time()


def post(path: str, payload: dict, key: str, bearer: str, interval: float = DEFAULT_INTERVAL_SEC) -> tuple[int, dict, float]:
    _throttle(interval)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=body, method="POST", headers={
        "Content-Type": "application/json; charset=utf-8",
        "X-Portal-Token": bearer,
        "Idempotent-Key": key,
    })
    t0 = time.perf_counter()
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=20) as resp:
            return int(resp.status), json.loads(resp.read().decode("utf-8")), round((time.perf_counter() - t0) * 1000, 1)
    except urllib.error.HTTPError as exc:
        return int(exc.code), {"error": exc.read().decode("utf-8", "replace")[:300]}, round(
            (time.perf_counter() - t0) * 1000, 1)


def judged_codes(session_id: str) -> set[str]:
    """已对该 pool session 出过裁决的代码（evidence 里带 session_id）。"""
    out: set[str] = set()
    if not DECISIONS.is_file():
        return out
    for line in DECISIONS.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if session_id in str(row.get("evidence") or ""):
            out.add(str(row.get("code")))
    return out


def latest_session() -> dict | None:
    if not SESSION_LEDGER.is_file():
        return None
    rows = [json.loads(l) for l in SESSION_LEDGER.read_text(encoding="utf-8").splitlines() if l.strip()]
    return rows[-1] if rows else None


def arm_plans(dry_run: bool) -> dict:
    """把本轮交棒里的 watch_plan 消费成 armed（失败不影响裁决结果）。"""
    if dry_run:
        return {"skipped": "dry_run"}
    cmd = [sys.executable, "-X", "utf8", "-m", "agent_platform.watch_plan_autoarm", "--once",
           "--ledger", str(REPO / "agent_platform" / "data" / "d1_handoffs.jsonl"),
           "--plans", str(REPO / "agent_platform" / "data" / "watch_plans.jsonl"),
           "--status", str(REPO / "agent_platform" / "data" / "watch_engine" / "autoarm_status.json"),
           "--json"]
    try:
        r = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=180)
        # autoarm 的 --json 是 indent 输出的多行 JSON：整段解析，不能取最后一行。
        doc = json.loads((r.stdout or "").strip())
        return {"created": doc.get("created"), "armed": doc.get("armed"),
                "expired": doc.get("expired"), "errors": doc.get("errors"),
                "rc": r.returncode}
    except Exception as exc:  # noqa: BLE001
        log("ARM_WARN autoarm 失败（不影响裁决）：%s" % type(exc).__name__)
        return {"error": type(exc).__name__}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="D1 日终批量裁决 + 交棒（走真实 HTTP）")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_SEC,
                    help="每次写请求的最小间隔秒（写端点限流 12 次/分钟）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-handoff", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    session = latest_session()
    if not session:
        log("NO_SESSION pool_v2_sessions.jsonl 缺失或为空 -> 本轮无事可做")
        return 0
    sid = str(session.get("session_id") or "")
    scan_day = str(session.get("scan_run_date") or "")
    reviews = session.get("reviews") or []
    selected = [r for r in reviews if (r.get("aggregate") or {}).get("selected")]
    selected.sort(key=lambda r: float((r.get("aggregate") or {}).get("score") or 0.0), reverse=True)
    done = judged_codes(sid)
    todo = [r for r in selected if str(r.get("code")) not in done][: args.top]
    log("session=%s scan_run_date=%s selected=%d already_judged=%d todo=%d%s"
        % (sid, scan_day, len(selected), len(done), len(todo), " (dry-run)" if args.dry_run else ""))

    results = []
    bearer = "" if args.dry_run else token()
    for rec in todo:
        code = str(rec.get("code"))
        agg = rec.get("aggregate") or {}
        score = float(agg.get("score") or 0.0)
        tier = tier_of(score)
        opinions = rec.get("opinions") or []
        seat_bits = "；".join(
            "%s %s %.2f（%s）" % (o.get("name"), o.get("rating"), float(o.get("score") or 0),
                                  "、".join((o.get("reasons") or [])[:2]))
            for o in opinions)
        row = {"code": code, "name": rec.get("name"), "tier": tier, "score": score}
        if args.dry_run:
            log("  [dry] %-10s %-11s conf=%.3f -> would POST decision" % (code, tier, score))
            results.append(dict(row, action="dry_run"))
            continue

        payload = {
            "code": code, "name": rec.get("name"), "tier": tier,
            "confidence": round(min(0.95, max(0.05, score)), 3),
            "rationale": "投研会审 %s 精选（聚合分 %.2f）：%s。池内 paper 观察，不建单。"
                         % (sid, score, seat_bits)[:1800],
            "decided_by": "D1", "paper_only": True, "not_a_trade": True,
            "source": "invest_committee_v2",
            "evidence": [{"kind": "pool_v2_session", "value": sid}],
        }
        key_dec = "d1dec-%s-%s-v2" % (scan_day.replace("-", ""), code.replace(".", ""))
        http, data, ms = post("/api/d1/decision", payload, key_dec, bearer, args.interval)
        did = ((data.get("data") or {}).get("decision") or {}).get("decision_id") \
            or (data.get("decision") or {}).get("decision_id")
        log("  decision %-10s %-11s http=%s %6.1fms id=%s %s"
            % (code, tier, http, ms, did, (data or {}).get("error") or ""))
        row.update({"action": "decision", "http": http, "decision_id": did, "ms": ms})
        results.append(row)
        if http != 200 or args.no_handoff:
            continue

        close = last_close(code)
        if not close:
            log("  handoff  %-10s SKIP 无收盘价（不造数）" % code)
            row["handoff"] = "skipped_no_quote"
            continue
        ho = {
            "schema": "d1_handoff/v2", "code": code, "name": rec.get("name"),
            "target": "WA1", "action": "watch",
            "rationale": "D1 裁决 %s（聚合分 %.2f）：交 WA1 盯盘，止损/止盈两档触发；paper-only，不建单。"
                         % (tier, score),
            "buy_zone": [round(close * 0.98, 2), round(close * 1.01, 2)],
            "stop_loss": round(close * 0.95, 2),
            "trigger": "跌破%.2f止损 / 站上%.2f止盈复核" % (round(close * 0.95, 2), round(close * 1.08, 2)),
            "evidence": [{"kind": "pool_v2_session", "value": sid}],
            "paper_only": True, "not_a_trade": True,
            "watch_plans": [
                {"plan_id": "wp-%s-%s-stop" % (scan_day.replace("-", ""), code.split(".")[0]),
                 "seat": "WA1", "method": "price_structure",
                 "conditions": {"symbol": code, "price": round(close * 0.95, 2), "operator": "<=",
                                "action": "WATCH", "reason": "止损位复核"},
                 "expires_at": (dt.date.fromisoformat(scan_day) + dt.timedelta(days=7)).isoformat()
                               + "T15:00:00+08:00", "status": "planned"},
                {"plan_id": "wp-%s-%s-take" % (scan_day.replace("-", ""), code.split(".")[0]),
                 "seat": "WA1", "method": "price_structure",
                 "conditions": {"symbol": code, "price": round(close * 1.08, 2), "operator": ">=",
                                "action": "WATCH", "reason": "止盈位复核"},
                 "expires_at": (dt.date.fromisoformat(scan_day) + dt.timedelta(days=7)).isoformat()
                               + "T15:00:00+08:00", "status": "planned"},
            ],
        }
        key_ho = "d1ho-%s-%s-v2" % (scan_day.replace("-", ""), code.replace(".", ""))
        h2, d2, ms2 = post("/api/d1/handoff", ho, key_ho, bearer, args.interval)
        rid = (d2.get("handoff") or {}).get("receipt_id") if isinstance(d2, dict) else None
        log("  handoff  %-10s close=%.2f http=%s %6.1fms receipt=%s %s"
            % (code, close, h2, ms2, rid, (d2 or {}).get("error") or ""))
        row.update({"handoff": "created" if h2 == 200 else "failed", "handoff_http": h2,
                    "receipt_id": rid})

    armed = arm_plans(args.dry_run)
    log("ARM %s" % json.dumps(armed, ensure_ascii=False))
    payload_out = {"schema": "d1_daily_batch/v1",
                   "at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                   "session_id": sid, "scan_run_date": scan_day, "top": args.top,
                   "selected": len(selected), "already_judged": len(done),
                   "processed": len(todo), "results": results, "arm": armed,
                   "paper_only": True, "not_a_trade": True}
    if args.json:
        print(json.dumps(payload_out, ensure_ascii=False, indent=1))
    log("SUMMARY session=%s processed=%d decisions=%d handoffs=%d armed=%s bad=%d"
        % (sid, len(results),
           sum(1 for r in results if r.get("action") == "decision"),
           sum(1 for r in results if r.get("handoff") == "created"),
           armed.get("armed"), sum(1 for r in results if r.get("http") not in (None, 200))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
