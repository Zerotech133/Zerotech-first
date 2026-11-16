# -*- coding: utf-8 -*-
"""投研派活器：把池内标的真正送进 T1/T2/N1/F1 四席位（2026-11-16 WO-RESEARCH-DISPATCH）。

## 背景

诊断结论（2026-11-16）：
- 12 席**在岗**（09:20 心跳全绿）；
- 但池内 30 只全是 `action=pending_analysis`、`analyses={}`、`recommendation=null`
  —— **有工人、有工位、没有工单**。

本工具补的就是"工单通道"：读池 → 生成四席位工单 → 调席位 → 汇总 → 落可追溯文件。

## 边界（重要）

- **不写 PG**：`v2_acad1_pool` 是 ACAD1 的只读真源（projection 里写明"不写库"）。
  `action=analyzed` 只能由 ACAD1 侧改；本工具把结果落到
  `agent_platform/data/research/<date>/<code>.json` 与 `research_index.jsonl`，供下游消费。
- 每个席位**只出结构化结论**（JSON），不写长文、不调工具；
- 缺数据时明确说"缺"，不许编；
- 一次一只、可 `--limit` 控制，失败不影响其他。

    python -X utf8 tools/research_dispatch.py --limit 1 --dry-run   # 看 prompt
    python -X utf8 tools/research_dispatch.py --limit 1             # 真跑一只
    python -X utf8 tools/research_dispatch.py                       # 跑池内全部 CN
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLATFORM = REPO / "agent_platform"
DATA = PLATFORM / "data"
OUT_ROOT = DATA / "research"
INDEX = DATA / "research_index.jsonl"
POOL_API = "http://127.0.0.1:8091/api/acad1/pool"
SEATS = ("T1", "T2", "N1", "F1")
SEAT_SPEC = {
    "T1": ("多头研究员", "只找**支持买入**的证据：结构/量价/资金/催化。没有就直说没有。"),
    "T2": ("空头研究员", "只找**反对买入**的证据：破位/背离/估值/筹码/利空。没有就直说没有。"),
    "N1": ("舆情研究员", "只看情绪面与消息面：热度、一致性、突发消息、监管风险；无数据就说无数据。"),
    "F1": ("基本面研究员", "只看财务与行业：盈利能力、成长、现金流、行业景气；无数据就说无数据。"),
}
SCHEMA = ("{\"stance\":\"bull|bear|neutral|unknown\",\"points\":[\"...\"],"
          "\"risks\":[\"...\"],\"evidence_missing\":[\"...\"],\"verdict\":\"一句话\","
          "\"confidence\":0.0}")
# 输出长度硬约束：实测 T1 接 xsec 后会写超 3000 token 被截断 → JSON 解析失败（no_json），
# 席位在汇总里"消失"。既提高 token 预算，也在 prompt 里限条数与字数。
MAX_POINTS = 5
MAX_CHARS_PER_POINT = 80


def log(msg: str) -> None:
    print("%s %s" % (dt.datetime.now().strftime("%H:%M:%S"), msg))


def fetch_pool() -> dict:
    import urllib.request
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(POOL_API, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def _client():
    sys.path.insert(0, str(PLATFORM))
    from engine import deepseek  # noqa: WPS433
    return deepseek.DeepSeekClient()


SEAT_CARD_FILE = {"T1": "t1_bull.md", "T2": "t2_bear.md",
                  "N1": "n1_sentiment.md", "F1": "f1_fundamental.md"}


def load_cards() -> dict:
    """席位卡真源 = `agent_platform/prompts/<seat>.md`（心跳配置只有名单，没有职责正文）。"""
    out = {}
    for seat, fn in SEAT_CARD_FILE.items():
        p = PLATFORM / "prompts" / fn
        try:
            txt = p.read_text(encoding="utf-8")
        except OSError:
            continue
        head = next((l.strip() for l in txt.splitlines() if l.strip()), "")
        out[seat] = {"cn_name": seat, "role_cn": head[:80], "charter": txt[:900]}
    return {"agents": out}


def xsec_profile(code: str, timeout: int = 90) -> dict:
    """调 tools/xsec_profile.py 取漏斗报告（图形假设 + 事件/资金佐证）。

    失败返回 {}——材料不足时席位会自己写进 evidence_missing，不阻塞整轮。
    """
    import subprocess
    try:
        r = subprocess.run([sys.executable, "-X", "utf8",
                            str(REPO / "tools" / "xsec_profile.py"), code, "--json"],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL,
                           encoding="utf-8", errors="replace", timeout=timeout, cwd=str(REPO))
        if r.returncode != 0 or not (r.stdout or "").strip().startswith("{"):
            return {}
        return json.loads(r.stdout)
    except Exception:  # noqa: BLE001
        return {}


def slim_xsec(p: dict) -> dict:
    """只保留席位用得上的部分，避免 prompt 过长。"""
    if not p:
        return {}
    f = p.get("funnel") or {}
    l2 = p.get("line2_kline") or {}
    return {
        "primary": (f.get("primary_hypothesis") or {}).get("primary"),
        "primary_name": (f.get("primary_hypothesis") or {}).get("name"),
        "next_verification": (f.get("primary_hypothesis") or {}).get("next_verification"),
        "ops_hit": {k: (v.get("hit") if isinstance(v, dict) else None)
                    for k, v in (f.get("ops") or {}).items() if isinstance(v, dict)},
        "context": (f.get("ops") or {}).get("context"),
        "kline_last": {k: l2.get(k) for k in
                       ("close", "pos250", "vol20_vs_prev20", "ma20", "ma60", "trend")
                       if l2.get(k) is not None},
    }


def build_prompt(seat: str, item: dict, card: dict, xsec: dict | None = None) -> tuple[str, str]:
    role, focus = SEAT_SPEC[seat]
    system = (
        "你是数字军团投研席位 %s（%s），岗位：%s。%s\n"
        "硬规则：只用给定材料；材料不足时把缺的东西写进 evidence_missing，**不许编造数字或消息**；"
        "结论必须能被我复核。只输出 JSON，不要代码块、不要解释。"
        % (seat, card.get("cn_name") or seat, card.get("role_cn") or role, focus)
    )
    material = {
        "code": item.get("code"), "name": item.get("name"), "market": item.get("market"),
        "strategy_id": item.get("strategy_id"), "signal": item.get("signal"),
        "score": item.get("score"), "selection_reason": item.get("selection_reason"),
        "data_date": item.get("data_date"), "pool_date": item.get("pool_date"),
        "risk_flags": item.get("risk_flags"),
    }
    if xsec:
        material["xsec_profile"] = xsec
    user = ("标的材料（ACAD1 池条目 + xsec 漏斗报告，均为原始字段）：\n%s\n\n"
            "请按此 JSON 结构输出你的结论：%s\n"
            "篇幅硬约束：points ≤ %d 条、risks ≤ %d 条、evidence_missing ≤ %d 条，"
            "每条 ≤ %d 字；超出会导致解析失败。"
            % (json.dumps(material, ensure_ascii=False), SCHEMA,
               MAX_POINTS, MAX_POINTS, MAX_POINTS, MAX_CHARS_PER_POINT))
    return system, user


def ask_seat(client, seat: str, item: dict, card: dict, model: str, max_tokens: int,
             xsec: dict | None = None) -> dict:
    system, user = build_prompt(seat, item, card, xsec)
    t0 = time.time()
    err = ""
    text = ""
    for _ in range(2):
        try:
            full = getattr(client, "chat_full", None)
            if callable(full):
                data = full([{"role": "system", "content": system},
                             {"role": "user", "content": user}],
                            model=model, temperature=0.2, max_tokens=max_tokens) or {}
                text = str(data.get("content") or "").strip()
            else:
                text = str(client.chat([{"role": "system", "content": system},
                                        {"role": "user", "content": user}],
                                       model=model, temperature=0.2,
                                       max_tokens=max_tokens) or "").strip()
        except Exception as exc:  # noqa: BLE001
            err = "%s: %s" % (type(exc).__name__, str(exc)[:160])
            text = ""
        if text:
            break
    payload = _parse_json(text)
    return {"seat": seat, "ok": bool(payload), "elapsed_ms": int((time.time() - t0) * 1000),
            "raw_head": text[:200], "result": payload, "error": err or ("" if payload else "no_json")}


def _parse_json(text: str) -> dict | None:
    s = text.strip()
    if s.startswith("```"):
        s = s.split("```")[1] if "```" in s[3:] else s
        s = s.lstrip("json").strip()
    try:
        return json.loads(s)
    except ValueError:
        i, j = s.find("{"), s.rfind("}")
        if i >= 0 and j > i:
            try:
                return json.loads(s[i:j + 1])
            except ValueError:
                return None
        return None


def upsert_index(row: dict) -> None:
    """按 (code, pool_date) 去重后重写——重跑同一只不产生重复行。"""
    key = (row["code"], row["pool_date"])
    rows = {}
    if INDEX.is_file():
        for line in INDEX.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            rows[(r.get("code"), r.get("pool_date"))] = r
    rows[key] = row
    INDEX.parent.mkdir(parents=True, exist_ok=True)
    with INDEX.open("w", encoding="utf-8") as fh:
        for k in sorted(rows):
            fh.write(json.dumps(rows[k], ensure_ascii=False) + "\n")
def summarize(code: str, seats: dict) -> dict:
    """汇总四席位 → 一句合计结论。规则透明、可复核。"""
    got = {k: v for k, v in seats.items() if v.get("ok") and isinstance(v.get("result"), dict)}
    stances = {k: str(v["result"].get("stance") or "unknown") for k, v in got.items()}
    confs = []
    for v in got.values():
        try:
            confs.append(float(v["result"].get("confidence") or 0))
        except (TypeError, ValueError):
            pass
    bull = sum(1 for s in stances.values() if s == "bull")
    bear = sum(1 for s in stances.values() if s == "bear")
    neu = sum(1 for s in stances.values() if s == "neutral")
    unk = sum(1 for s in stances.values() if s == "unknown")
    missing_seats = len(SEATS) - len(got)
    if not got:
        rec, conf = "UNKNOWN", 0.0
        why = "四席位均未产出可用结论"
    elif bear >= 2:
        rec, conf = "AVOID", round(sum(confs) / max(1, len(confs)) * 0.8, 3)
        why = "空头证据占多数（%d 席看空）" % bear
    elif bull >= 2 and bear == 0:
        rec, conf = "BUY", round(sum(confs) / max(1, len(confs)), 3)
        why = "多头证据成立且无空头反对（%d 席看多）" % bull
    else:
        rec, conf = "HOLD", round(sum(confs) / max(1, len(confs)) * 0.9, 3)
        # 文案按实际构成分开写：全未知 ≠ 分歧 ≠ 全中性
        if bull and bear:
            why = "多空对立（多%d/空%d）" % (bull, bear)
        elif bull or bear:
            why = "单边有立场但反方无证据（多%d/空%d/中%d/未%d）" % (bull, bear, neu, unk)
        elif neu:
            why = "四席均中性，无方向（中%d/未%d）" % (neu, unk)
        else:
            why = "四席均无立场（证据不足；未产出 %d 席）" % missing_seats
    return {"recommendation": rec, "confidence": conf, "rationale": why,
            "stances": stances, "seats_ok": len(got), "seats_total": len(SEATS),
            "stance_counts": {"bull": bull, "bear": bear, "neutral": neu, "unknown": unk}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="投研派活：池内标的 → T1/T2/N1/F1")
    ap.add_argument("--pool-date", default=None, help="缺省取池的 pool_date")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 只（0=全部 CN）")
    ap.add_argument("--model", default="deepseek-flash")
    ap.add_argument("--max-tokens", type=int, default=3000)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", default="", help="只处理指定代码（逗号分隔）")
    ap.add_argument("--no-xsec", action="store_true", help="不附带 xsec 漏斗报告")
    args = ap.parse_args(argv)

    pool = fetch_pool()
    day = args.pool_date or str(pool.get("pool_date") or "")
    items = [x for x in (pool.get("items") or [])
             if str(x.get("market") or "").upper() == "CN"]
    if args.only:
        want = {c.strip() for c in args.only.split(",") if c.strip()}
        items = [x for x in items if str(x.get("code")) in want]
    if args.limit:
        items = items[: args.limit]
    log("POOL day=%s CN=%d source_mode=%s" % (day, len(items), pool.get("source_mode")))
    if not items:
        log("NO_ITEMS 无可处理标的")
        return 0

    cards = (load_cards().get("agents") or {})
    if args.dry_run:
        system, user = build_prompt("T1", items[0], cards.get("T1") or {})
        log("DRY_RUN 首个工单（%s）\n--- system ---\n%s\n--- user ---\n%s" % (items[0]["code"], system, user))
        return 0

    client = _client()
    out_dir = OUT_ROOT / day.replace("-", "")
    out_dir.mkdir(parents=True, exist_ok=True)
    ok_n = 0
    for item in items:
        code = str(item.get("code"))
        xs = {} if args.no_xsec else slim_xsec(xsec_profile(code))
        if xs:
            log("  %s xsec primary=%s ops_hit=%s" % (code, xs.get("primary"),
                {k: v for k, v in (xs.get("ops_hit") or {}).items() if v}))
        seats = {}
        for seat in SEATS:
            r = ask_seat(client, seat, item, cards.get(seat) or {}, args.model, args.max_tokens, xs)
            seats[seat] = r
            log("  %s %s ok=%s %sms %s" % (code, seat, r["ok"], r["elapsed_ms"],
                                           (r["result"] or {}).get("verdict", "")[:60]))
        summary = summarize(code, seats)
        doc = {"schema": "research_dispatch/v1", "code": code, "name": item.get("name"),
               "pool_date": day, "data_date": item.get("data_date"),
               "seats": seats, "summary": summary,
               "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
               "model": args.model, "paper_only": True, "not_a_trade": True,
               "source_note": "ACAD1 池条目 + 四席位结论；不写 PG、不构成交易指令"}
        path = out_dir / ("%s.json" % code)
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        upsert_index({"code": code, "pool_date": day,
                      "recommendation": summary["recommendation"],
                      "confidence": summary["confidence"],
                      "seats_ok": summary["seats_ok"],
                      "file": str(path.relative_to(REPO))})
        log("  -> %s %s conf=%.2f (%s)" % (code, summary["recommendation"],
                                           summary["confidence"], path.name))
        ok_n += 1 if summary["seats_ok"] else 0

    log("SUMMARY processed=%d seats_productive=%d out=%s" % (len(items), ok_n, out_dir))
    return 0 if ok_n == len(items) else 1


if __name__ == "__main__":
    raise SystemExit(main())
