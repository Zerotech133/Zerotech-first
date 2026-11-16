# -*- coding: utf-8 -*-
"""ACAD1（司马徽）官方池 → D1（鲁肃）裁决/交棒 桥（2026-11-16 WO-ACAD1-D1-BRIDGE）。

## 为什么需要

ACAD1 每天在 PG 产**官方推荐池**（`/api/acad1/pool`，`review_status=pool_reviewed`，
今天 11-16 共 29 只：CN 12 / HK 10 / US 6 / CRYPTO 1），而 D1 侧的会审输入
`todays_picks.json` 仍停在数据日 11-13 —— 中间缺一条"ACAD1 池 → D1 裁决"的桥。

## 边界（重要）

- ACAD1 只**选池/复核**（`recommendation/confidence/tier` 均为 null），**D1 才裁决**；
- 本桥不重算选股、不改池，只把池内标的按 D1 五档落裁决（`POST /api/d1/decision`）；
- tier 映射是**显式规则**（下方 `decide()`），且写成 rationale 可追溯；
- paper-only、不建单、不接实盘；幂等键绑定「池日 + 代码 + 来源」，重放返回原记录。

## 用法

    python -X utf8 tools/acad1_pool_bridge.py --dry-run
    python -X utf8 tools/acad1_pool_bridge.py --limit 5
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
DECISIONS = REPO / "agent_platform" / "data" / "d1_decisions.jsonl"
LOG = REPO / "logs" / "acad1_d1_bridge.log"
BASE = "http://127.0.0.1:8091"
POOL_API = BASE + "/api/acad1/pool"
INTERVAL = 6.0  # 写端点 12 次/分钟


def log(msg: str) -> None:
    line = "%s %s" % (dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def token() -> str:
    return TOKEN_FILE.read_text(encoding="utf-8").strip()


def fetch_pool(timeout: int = 20) -> dict:
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(POOL_API, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


_last = [0.0]


def post(path: str, payload: dict, key: str, bearer: str, interval: float = INTERVAL) -> tuple[int, dict, float]:
    gap = time.time() - _last[0]
    if _last[0] and gap < interval:
        time.sleep(interval - gap)
    _last[0] = time.time()
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=body, method="POST", headers={
        "Content-Type": "application/json; charset=utf-8",
        "X-Portal-Token": bearer,
        "Idempotent-Key": key,
    })
    t0 = time.perf_counter()
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=25) as resp:
            return int(resp.status), json.loads(resp.read().decode("utf-8")), round((time.perf_counter() - t0) * 1000, 1)
    except urllib.error.HTTPError as exc:
        return int(exc.code), {"error": exc.read().decode("utf-8", "replace")[:300]}, round(
            (time.perf_counter() - t0) * 1000, 1)


def decide(item: dict) -> dict:
    """ACAD1 池条目 → D1 五档（**显式规则，依据 ACAD1 既有证据，可追溯**）。

    事实依据（2026-11-16 实测 29 只）：全部 `risk_flags=[]`，`selection_reason` 均以
    「基础信号：买点」开头，`status=selected`，`selected_by=ACAD1`，`score` 82.5–100。
    即：**池内标的本就是 ACAD1 的买点候选**，D1 的职责是复核采纳，而非重新选股。

    规则（按优先级）：
    1. `risk_flags` 非空 → HOLD（ACAD1 已标风险，D1 不硬顶）；
    2. `signal` 含卖点（`卖`/`SELL`）→ SELL；
    3. `selection_reason` 含「买点」或 `signal` 含「买」→ **BUY**；
    4. 其余 → HOLD（无可采信买点依据，观察）。

    confidence 来自 ACAD1 `score` 归一：`0.45 + 0.25 × score/100`，clamp [0.45, 0.70]；
    带 risk_flags 再降 0.10，无买点依据降 0.05。不另造数字。
    """
    sig = str(item.get("signal") or "")
    reason = str(item.get("selection_reason") or "")
    flags = item.get("risk_flags") or []
    score = float(item.get("score") or 0)
    conf = round(max(0.45, min(0.70, 0.45 + 0.25 * (score / 100.0))), 3)
    if flags:
        return {"tier": "HOLD", "confidence": round(max(0.30, conf - 0.10), 3),
                "why": "ACAD1 风险标记 %s → 观望" % flags}
    if ("卖" in sig) or ("SELL" in sig.upper()):
        return {"tier": "SELL", "confidence": conf, "why": "信号含卖点 %r" % sig}
    if ("买" in reason) or ("买" in sig):
        return {"tier": "BUY", "confidence": conf,
                "why": "ACAD1 基础信号=买点（score %.1f，信号 %r）" % (score, sig)}
    return {"tier": "HOLD", "confidence": round(max(0.30, conf - 0.05), 3),
            "why": "无可采信买点依据（信号 %r）→ 观察" % sig}


QUOTE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=%s,day,,,5,qfq"


def _gid(code: str) -> str:
    c = str(code)
    return ("sh" if c.endswith(".SH") else "sz") + c.split(".")[0]


def last_close(code: str) -> float | None:
    """腾讯日线最近一根收盘；取不到返回 None（不造价）。"""
    try:
        url = QUOTE_URL % _gid(code)
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url, timeout=12) as r:
            doc = json.loads(r.read().decode("utf-8"))
        bars = ((doc.get("data") or {}).get(_gid(code)) or {})
        bars = bars.get("qfqday") or bars.get("day") or []
        return float(bars[-1][2]) if bars else None
    except Exception:  # noqa: BLE001
        return None


def judged(pool_date: str) -> set[str]:
    out: set[str] = set()
    if not DECISIONS.is_file():
        return out
    for line in DECISIONS.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if "acad1_pool" in str(row.get("source") or "") and pool_date in str(row.get("evidence") or ""):
            out.add(str(row.get("code")))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="ACAD1 官方池 → D1 裁决/交棒")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 只（0=全部）")
    ap.add_argument("--market", default="CN",
                    help="只处理该市场（默认 CN；HK/US/CRYPTO 暂无行情源，交棒会跳过）")
    ap.add_argument("--no-handoff", action="store_true", help="只写裁决不交棒")
    ap.add_argument("--max-age-days", type=int, default=3,
                    help="池日距今超过该天数则拒绝裁决（默认 3，防用过期池）")
    ap.add_argument("--handoff-only", action="store_true",
                    help="补交棒模式：跳过「已判」过滤，对池内 BUY 全部走一次（裁决 POST 幂等返回原记录）")
    ap.add_argument("--interval", type=float, default=INTERVAL)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    pool = fetch_pool()
    pool_date = str(pool.get("pool_date") or "")
    mode = str(pool.get("source_mode") or "")
    items = [x for x in (pool.get("items") or [])
             if str(x.get("market") or "").upper() == str(args.market).upper()]
    log("POOL pool_date=%s count=%d review_status=%s source_mode=%s"
        % (pool_date, len(items), pool.get("review_status"), mode))
    # 退化守卫：shadow = 非官方回退源（实测会返回旧日期且 market 全 None），
    # 拿它去裁决等于用错数据，必须拒绝而不是静默处理 0 条。
    if mode == "shadow" or str(pool.get("review_status") or "") == "shadow_only":
        log("REFUSED 池来源已退化（source_mode=%s, degraded_reason=%s）→ 不拿退化数据裁决；"
            "先修 PG 或其投影。" % (mode, pool.get("degraded_reason")))
        return 2
    # 陈旧守卫（时间维度）：池日距今过久、或上游自标 stale → 不裁过期数据。
    # 注意：池 API 的 `stale` 只算「数据日 vs 池日」的滞后，抓不到「projection 文件本身停在几天前」，
    # 所以这里额外按 pool_date 距今的天数兜一层。
    age = 999
    try:
        age = (dt.date.today() - dt.date.fromisoformat(pool_date)).days
    except ValueError:
        pass
    if age > args.max_age_days:
        log("REFUSED 池日 %s 距今 %d 天 > 阈值 %d 天 → 不用过期池裁决（修上游后重跑）"
            % (pool_date, age, args.max_age_days))
        return 2
    if pool.get("stale") is True:
        # `stale` 是「池内数据日 vs 池日」的滞后提示（实测 2026-11-16 的 CN 用的还是 11-13 行情，
        # freshness_days=3 → stale=True），周末/盘前属正常，**不能据此硬拒**，只留痕。
        log("WARN 上游自标 stale=true（freshness_days=%s, data_date=%s）→ 数据滞后属常态，继续但留痕"
            % (pool.get("freshness_days"), pool.get("data_date")))
    if not pool_date or not items:
        # projection 是**单日视图**（实测 2026-11-16 的单日只有 HK10/CRYPTO1，CN 为 0），
        # 而 CN 标的只在 PG 的聚合视图里。此时"0 条"不是"今天没活"，是"通道缺数据"。
        if mode == "projection":
            log("REFUSED 回退视图 projection 内没有 %s 标的（单日视图通常只含 HK/CRYPTO；"
                "CN 需 PG 聚合视图）→ 修 PG 后重跑，勿按'无事可做'处理。" % args.market)
            return 2
        log("NO_POOL 池为空或缺少 pool_date → 本轮无事可做")
        return 0

    done = judged(pool_date)
    if args.handoff_only:
        todo = list(items)  # 不看已判，全部重走（裁决幂等、交棒缺失才新建）
    else:
        todo = [x for x in items if str(x.get("code")) not in done]
    if args.limit:
        todo = todo[: args.limit]
    log("already_judged=%d todo=%d%s" % (len(done), len(todo), " (dry-run)" if args.dry_run else ""))

    bearer = "" if args.dry_run else token()
    results = []
    for item in todo:
        code = str(item.get("code"))
        d = decide(item)
        row = {"code": code, "market": item.get("market"), "tier": d["tier"],
               "confidence": d["confidence"], "why": d["why"]}
        if args.dry_run:
            log("  [dry] %-10s %-4s %-5s conf=%.2f %s"
                % (code, item.get("market"), d["tier"], d["confidence"], d["why"]))
            results.append(dict(row, action="dry_run"))
            continue
        payload = {
            "code": code, "name": item.get("name"), "tier": d["tier"],
            "confidence": d["confidence"],
            "rationale": ("ACAD1 官方池 %s 复核产物（%s/%s，score=%s，%s）→ D1 按规则裁 %s：%s。"
                          "池内 paper 观察，不建单。"
                          % (pool_date, item.get("market"), item.get("strategy_id"),
                             item.get("score"), str(item.get("selection_reason"))[:200],
                             d["tier"], d["why"]))[:1800],
            "decided_by": "D1", "paper_only": True, "not_a_trade": True,
            "source": "acad1_pool_bridge",
            "evidence": [{"kind": "acad1_pool", "value": "%s/%s" % (pool_date, item.get("strategy_id"))}],
        }
        key = "d1dec-acad1-%s-%s" % (pool_date.replace("-", ""), code.replace(".", ""))
        http, data, ms = post("/api/d1/decision", payload, key, bearer, args.interval)
        did = ((data.get("data") or {}).get("decision") or {}).get("decision_id") \
            or (data.get("decision") or {}).get("decision_id")
        log("  decision %-10s %-4s %-5s http=%s %6.1fms id=%s %s"
            % (code, item.get("market"), d["tier"], http, ms, did, (data or {}).get("error") or ""))
        results.append(dict(row, action="decision", http=http, decision_id=did, ms=ms))
        if http != 200 or args.no_handoff or d["tier"] not in ("BUY", "STRONG_BUY"):
            continue

        # 交棒：有收盘价才建（无价不造数）
        close = last_close(code)
        if not close:
            log("  handoff  %-10s SKIP 无收盘价（不造数）" % code)
            row["handoff"] = "skipped_no_quote"
            continue
        expire = (dt.date.fromisoformat(pool_date) + dt.timedelta(days=7)).isoformat() + "T15:00:00+08:00"
        ho = {
            "schema": "d1_handoff/v2", "code": code, "name": item.get("name"),
            "target": "WA1", "action": "watch",
            "rationale": "ACAD1 官方池 %s（%s/%s，score %s）→ D1 裁 %s，交 WA1 盯盘；paper-only。"
                         % (pool_date, item.get("market"), item.get("strategy_id"),
                            item.get("score"), d["tier"]),
            "buy_zone": [round(close * 0.98, 2), round(close * 1.01, 2)],
            "stop_loss": round(close * 0.95, 2),
            "trigger": "跌破%.2f止损 / 站上%.2f止盈复核" % (round(close * 0.95, 2), round(close * 1.08, 2)),
            "evidence": [{"kind": "acad1_pool", "value": "%s/%s" % (pool_date, item.get("strategy_id"))}],
            "paper_only": True, "not_a_trade": True,
            "watch_plans": [
                {"plan_id": "wp-acad1-%s-%s-stop" % (pool_date.replace("-", ""), code.split(".")[0]),
                 "seat": "WA1", "method": "price_structure",
                 "conditions": {"symbol": code, "price": round(close * 0.95, 2), "operator": "<=",
                                "action": "WATCH", "reason": "止损位复核"},
                 "expires_at": expire, "status": "planned"},
                {"plan_id": "wp-acad1-%s-%s-take" % (pool_date.replace("-", ""), code.split(".")[0]),
                 "seat": "WA1", "method": "price_structure",
                 "conditions": {"symbol": code, "price": round(close * 1.08, 2), "operator": ">=",
                                "action": "WATCH", "reason": "止盈位复核"},
                 "expires_at": expire, "status": "planned"},
            ],
        }
        h2, d2, ms2 = post("/api/d1/handoff", ho,
                           "d1ho-acad1-%s-%s" % (pool_date.replace("-", ""), code.replace(".", "")),
                           bearer, args.interval)
        rid = (d2.get("handoff") or {}).get("receipt_id") if isinstance(d2, dict) else None
        log("  handoff  %-10s close=%.2f http=%s %6.1fms receipt=%s %s"
            % (code, close, h2, ms2, rid, (d2 or {}).get("error") or ""))
        row.update({"handoff": "created" if h2 == 200 else "failed", "handoff_http": h2, "receipt_id": rid})

    ok = sum(1 for r in results if r.get("action") == "dry_run" or r.get("http") == 200)
    payload_out = {"schema": "acad1_d1_bridge/v1",
                   "at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                   "pool_date": pool_date, "pool_size": len(items),
                   "already_judged": len(done), "processed": len(results),
                   "ok": ok, "results": results,
                   "paper_only": True, "not_a_trade": True}
    if args.json:
        print(json.dumps(payload_out, ensure_ascii=False, indent=1))
    log("SUMMARY pool_date=%s processed=%d ok=%d bad=%d"
        % (pool_date, len(results), ok, len(results) - ok))
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
