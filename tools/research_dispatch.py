# -*- coding: utf-8 -*-
"""投研派活器：池内标的 → T1/T2/N1/F1 四席位 →（对立时）辩论 → 结构化结果

2026-11-16 WO-RESEARCH-DISPATCH v2：按「菜鸟系统」的 Agent 团队契约补齐三项——
- **C 执行三态**：`normal / debate / degraded`（`no_json`/超时不再混成"席位缺席"）
- **A 结果契约**：`recommendation_level` 五档 + `confidence_score` 0-100 +
  `risk_level` + `time_horizon`（`target_price` 无可靠源时留 null，不编造）
- **B 辩论**：T1↔T2 立场相反时互辩 2 轮，落 `debate_log`，用辩论后立场汇总

参考：`C:\\项目\\_archive\\2026-10-27_code-cleanup\\菜鸟\\菜鸟系统深度研究_选股与情报体系.md`
（`ai_team_role` 的 is_debate_node/debate_partner_key/max_debate_rounds、
 `ai_team_node_execute` 的 execution_mode/debate_log、
 `ai_team_result_summary` 的五档+confidence_score+risk_level+target_price+time_horizon）

边界：不写 PG；不许编造；产出落 `data/research/<date>/<code>.json`；索引按 (code, pool_date) 去重。
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
DEBATE_PAIR = ("T1", "T2")          # 多头 ↔ 空头
FINAL_SEAT = "FD"                   # 决胜席（对齐菜鸟 final_decision 节点）
FINAL_SCHEMA = ("{\"decision_level\":\"STRONG_BUY|BUY|HOLD|SELL|STRONG_SELL\","
                "\"confidence\":0-100,\"decisive_evidence\":[\"...\"],"
                "\"rejected_arguments\":[\"...\"],\"rationale\":\"一句话\"}")
SEAT_SPEC = {
    "T1": ("多头研究员", "只找**支持买入**的证据：结构/量价/资金/催化。没有就直说没有。"),
    "T2": ("空头研究员", "只找**反对买入**的证据：破位/背离/估值/筹码/利空。没有就直说没有。"),
    "N1": ("舆情研究员", "只看情绪面与消息面：热度、一致性、突发消息、监管风险；无数据就说无数据。"),
    "F1": ("基本面研究员", "只看财务与行业：盈利能力、成长、现金流、行业景气；无数据就说无数据。"),
}
SCHEMA = ("{\"stance\":\"bull|bear|neutral|unknown\",\"points\":[\"...\"],"
          "\"risks\":[\"...\"],\"evidence_missing\":[\"...\"],\"verdict\":\"一句话\","
          "\"confidence\":0.0}")
MAX_POINTS = 5
MAX_CHARS_PER_POINT = 80

# 执行三态（对齐菜鸟 ai_team_node_execute.execution_mode）
MODE_NORMAL, MODE_DEBATE, MODE_DEGRADED = "normal", "debate", "degraded"

SEAT_CARD_FILE = {"T1": "t1_bull.md", "T2": "t2_bear.md",
                  "N1": "n1_sentiment.md", "F1": "f1_fundamental.md"}


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


def load_cards() -> dict:
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


def finance_profile(code: str, timeout: int = 90) -> dict:
    """调 tools/finance_profile.py 取基本面画像（本地 tushare，无需联网）。失败返回 {}。"""
    import subprocess
    try:
        r = subprocess.run([sys.executable, "-X", "utf8",
                            str(REPO / "tools" / "finance_profile.py"), code, "--json"],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL,
                           encoding="utf-8", errors="replace", timeout=timeout, cwd=str(REPO))
        if not (r.stdout or "").strip().startswith("{"):
            return {}
        doc = json.loads(r.stdout)
        return doc if doc.get("available") else {}
    except Exception:  # noqa: BLE001
        return {}


def sentiment_profile(code: str, timeout: int = 90) -> dict:
    """调 tools/sentiment_profile.py 取舆情/关注度信号（本地互动易/户数/资金流）。"""
    import subprocess
    try:
        r = subprocess.run([sys.executable, "-X", "utf8",
                            str(REPO / "tools" / "sentiment_profile.py"), code, "--json"],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL,
                           encoding="utf-8", errors="replace", timeout=timeout, cwd=str(REPO))
        if not (r.stdout or "").strip().startswith("{"):
            return {}
        doc = json.loads(r.stdout)
        return doc if doc.get("available") else {}
    except Exception:  # noqa: BLE001
        return {}


def slim_sent(p: dict) -> dict:
    if not p:
        return {}
    keep = ("qa", "holders", "moneyflow", "warnings")
    return {k: p[k] for k in keep if p.get(k)}


def events_profile(code: str, timeout: int = 90) -> dict:
    """调 tools/events_profile.py 取近期公告归类（本地 anns_d）。"""
    import subprocess
    try:
        r = subprocess.run([sys.executable, "-X", "utf8",
                            str(REPO / "tools" / "events_profile.py"), code, "--json"],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL,
                           encoding="utf-8", errors="replace", timeout=timeout, cwd=str(REPO))
        if not (r.stdout or "").strip().startswith("{"):
            return {}
        doc = json.loads(r.stdout)
        return doc if doc.get("available") else {}
    except Exception:  # noqa: BLE001
        return {}


def slim_events(p: dict) -> dict:
    if not p:
        return {}
    keep = ("window_days", "window_count", "last_date", "last_title",
            "by_category", "attention_categories", "recent", "warnings")
    return {k: p[k] for k in keep if p.get(k)}


def slim_fin(p: dict) -> dict:
    """F1/T2 用得上的字段；不含推断值。"""
    if not p:
        return {}
    keep = ("report", "profitability", "growth", "solvency", "cash", "valuation", "basic", "warnings")
    out = {k: p[k] for k in keep if p.get(k)}
    if p.get("trend"):
        out["trend"] = p["trend"][-4:]
    return out


def build_prompt(seat: str, item: dict, card: dict, xsec: dict | None = None,
                 fin: dict | None = None, sent: dict | None = None,
                 events: dict | None = None) -> tuple[str, str]:
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
    if fin and seat in ("F1", "T2"):     # 基本面给 F1；T2 需要估值/财报做反证
        material["finance_profile"] = fin
    if sent and seat in ("N1", "T2"):    # 舆情给 N1；T2 也要看资金/关注度
        material["sentiment_profile"] = sent
    if events:                            # 公告对所有席位都相关（事件核验是共同前提）
        material["events_profile"] = events
    user = ("标的材料（ACAD1 池条目 + xsec 漏斗报告，均为原始字段）：\n%s\n\n"
            "请按此 JSON 结构输出你的结论：%s\n"
            "篇幅硬约束：points ≤ %d 条、risks ≤ %d 条、evidence_missing ≤ %d 条，"
            "每条 ≤ %d 字；超出会导致解析失败。"
            % (json.dumps(material, ensure_ascii=False), SCHEMA,
               MAX_POINTS, MAX_POINTS, MAX_POINTS, MAX_CHARS_PER_POINT))
    return system, user


def build_debate_prompt(seat: str, item: dict, card: dict, mine: dict, theirs: dict,
                        opponent: str, round_no: int, rounds: int, xsec: dict | None) -> tuple[str, str]:
    """辩论轮：把对方论据摆给对方，要求针对性反驳并可修正立场（对齐菜鸟 debate 节点）。"""
    role, focus = SEAT_SPEC[seat]
    system = (
        "你是数字军团投研席位 %s（%s），岗位：%s。%s\n"
        "现在是第 %d/%d 轮辩论：对手 %s 已给出如下结论。你必须**逐条针对性回应**——"
        "承认对方成立的点、指出你不认同的点及依据；允许据实修改自己的 stance，但不得为了『和稀泥』而改。\n"
        "硬规则：只用给定材料与对方论据；不许编造；只输出 JSON。"
        % (seat, card.get("cn_name") or seat, card.get("role_cn") or role, focus,
           round_no, rounds, opponent)
    )
    material = {
        "code": item.get("code"),
        "my_previous": mine,
        "opponent_previous": theirs,
        "xsec_profile": xsec or {},
    }
    user = ("辩论材料：\n%s\n\n请按此 JSON 结构输出本轮结论：%s\n"
            "篇幅硬约束：points ≤ %d 条、每条 ≤ %d 字。"
            % (json.dumps(material, ensure_ascii=False), SCHEMA, MAX_POINTS, MAX_CHARS_PER_POINT))
    return system, user


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


def _call(client, system: str, user: str, model: str, max_tokens: int) -> dict:
    """统一调用 + 三态判定：normal 有 JSON；degraded 有输出但解析失败/异常；超时也算 degraded。"""
    t0 = time.time()
    raw = ""
    err = ""
    for _ in range(2):
        try:
            full = getattr(client, "chat_full", None)
            msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
            if callable(full):
                data = full(msgs, model=model, temperature=0.2, max_tokens=max_tokens) or {}
                raw = str(data.get("content") or "").strip()
            else:
                raw = str(client.chat(msgs, model=model, temperature=0.2,
                                      max_tokens=max_tokens) or "").strip()
        except Exception as exc:  # noqa: BLE001
            err = "%s: %s" % (type(exc).__name__, str(exc)[:160])
            raw = ""
        if raw:
            break
    parsed = _parse_json(raw)
    if parsed:
        mode = MODE_NORMAL
    else:
        mode = MODE_DEGRADED          # 有输出但解析不了、或彻底失败，都算降级
    return {"ok": bool(parsed), "mode": mode, "elapsed_ms": int((time.time() - t0) * 1000),
            "raw_head": raw[:200], "result": parsed,
            "error": err or ("" if parsed else ("no_json" if raw else "empty_reply"))}


def ask_seat(client, seat: str, item: dict, card: dict, model: str, max_tokens: int,
             xsec: dict | None = None, fin: dict | None = None,
             sent: dict | None = None, events: dict | None = None) -> dict:
    system, user = build_prompt(seat, item, card, xsec, fin, sent, events)
    out = _call(client, system, user, model, max_tokens)
    out["seat"] = seat
    out["phase"] = "open"
    return out


def should_debate(t1_stance: str, t2_stance: str, rounds: int) -> bool:
    """辩论触发条件：轮数>0 且 T1/T2 立场正好相反（bull vs bear）。

    同向（如都 bull）或任一方 unknown/neutral 时不辩——避免无意义成本（每轮每只多 2 次调用）。
    """
    return rounds > 0 and {str(t1_stance), str(t2_stance)} == {"bull", "bear"}


def should_finalize(bull: int, bear: int) -> bool:
    """何时启用决胜席：四席里同时存在看多与看空（真对立），否则规则已能定。

    背景：T2 恒 bear 使 `bear>=2 → SELL` 实际由 F1 一票决定，`BUY` 永不成立；
    对齐菜鸟的 final_decision 节点，在多空对立时由决胜席拍板，而不是靠票数门槛。
    """
    return bull >= 1 and bear >= 1


def _seat_brief_for_final(seats: dict) -> dict:
    """给决胜席看：每席立场 + 论据 + 缺失。不塞原始长文，只给可判断的要点。"""
    out = {}
    for k in SEATS:
        r = (seats.get(k) or {}).get("result") or {}
        out[k] = {"stance": r.get("stance"), "confidence": r.get("confidence"),
                  "points": (r.get("points") or [])[:4],
                  "risks": (r.get("risks") or [])[:3],
                  "evidence_missing": (r.get("evidence_missing") or [])[:3],
                  "verdict": str(r.get("verdict") or "")[:160]}
    return out


def run_final_decision(client, item: dict, seats: dict, debate: dict | None,
                       xsec: dict | None, fin: dict | None, sent: dict | None,
                       model: str, max_tokens: int) -> dict:
    """决胜席：在多空对立时给出最终五档，必须指出决定性证据与被否定的论据。"""
    system = (
        "你是数字军团**最终决策席（FD）**。四位研究员已给出结论，现在由你拍板。\n"
        "硬规则：\n"
        "1) 必须选出一个档位（STRONG_BUY/BUY/HOLD/SELL/STRONG_SELL），**不允许 unknown**；\n"
        "2) 必须指出「决定性证据」——是哪一方哪条材料真正左右了结论；\n"
        "3) 必须指出「被否定的论据」——你为什么不采纳对方的核心论点；\n"
        "4) 只用给定材料，不许编造；只输出 JSON。"
    )
    material = {
        "code": item.get("code"), "name": item.get("name"),
        "seats": _seat_brief_for_final(seats),
        "debate": (debate or {}).get("rounds") or [],
        "xsec_profile": xsec or {},
        "finance_profile": fin or {},
        "sentiment_profile": sent or {},
    }
    user = ("四席结论与辩论纪要：\n%s\n\n请按此 JSON 输出最终决策：%s\n"
            "篇幅硬约束：decisive_evidence ≤ 3 条、rejected_arguments ≤ 3 条，每条 ≤ 80 字。"
            % (json.dumps(material, ensure_ascii=False), FINAL_SCHEMA))
    out = _call(client, system, user, model, max_tokens)
    out["seat"] = FINAL_SEAT
    out["phase"] = "final"
    return out


def _stance(res: dict) -> str:
    r = (res or {}).get("result") or {}
    return str(r.get("stance") or "unknown")


def run_debate(client, item: dict, cards: dict, seats: dict, xsec: dict | None,
               model: str, max_tokens: int, rounds: int = 2) -> dict:
    """T1↔T2 互辩。返回 {rounds:[{round, a, b}], final:{T1,T2}, changed:bool}。"""
    a, b = DEBATE_PAIR
    log_rounds = []
    changed = False
    cur = {a: seats[a], b: seats[b]}
    for r in range(1, rounds + 1):
        new = {}
        for me, opp in ((a, b), (b, a)):
            mine = (cur[me].get("result") or {})
            theirs = (cur[opp].get("result") or {})
            system, user = build_debate_prompt(me, item, cards.get(me) or {}, mine, theirs,
                                               opp, r, rounds, xsec)
            got = _call(client, system, user, model, max_tokens)
            got["seat"] = me
            got["phase"] = "debate_r%d" % r
            new[me] = got
            log("    %s %s 轮%d stance=%s ok=%s" % (item.get("code"), me, r,
                                                    _stance(got), got["ok"]))
        if _stance(new[a]) != _stance(cur[a]) or _stance(new[b]) != _stance(cur[b]):
            changed = True
        log_rounds.append({"round": r,
                           a: {"stance": _stance(new[a]), "verdict": ((new[a].get("result") or {}).get("verdict") or "")[:200],
                               "ok": new[a]["ok"], "mode": new[a]["mode"]},
                           b: {"stance": _stance(new[b]), "verdict": ((new[b].get("result") or {}).get("verdict") or "")[:200],
                               "ok": new[b]["ok"], "mode": new[b]["mode"]}})
        cur = {a: new[a] if new[a]["ok"] else cur[a],
               b: new[b] if new[b]["ok"] else cur[b]}
    return {"rounds": log_rounds, "final": cur, "changed": changed}


def _risk_level(bull: int, bear: int, unk: int, degraded: int) -> str:
    """风险等级：空头占优或席位大面积降级 → HIGH；分歧/部分未知 → MEDIUM；其余 LOW。"""
    if bear >= 2 or degraded >= 2:
        return "HIGH"
    if bear >= 1 or unk >= 2 or degraded == 1:
        return "MEDIUM"
    return "LOW"


def summarize(code: str, seats: dict, debate: dict | None = None,
              final: dict | None = None) -> dict:
    """汇总为菜鸟式契约：五档 + 0-100 + risk_level + time_horizon。

    target_price 无可靠来源 → 一律 null 并注明（不编造）。
    """
    got = {k: v for k, v in seats.items() if v.get("ok") and isinstance(v.get("result"), dict)}
    degraded = [k for k, v in seats.items() if v.get("mode") == MODE_DEGRADED]
    stances = {k: _stance(v) for k, v in got.items()}
    confs = []
    for v in got.values():
        try:
            c = float((v.get("result") or {}).get("confidence") or 0)
        except (TypeError, ValueError):
            c = 0.0
        confs.append(c if c > 1 else c * 100)      # 席位给 0-1，统一到 0-100
    bull = sum(1 for s in stances.values() if s == "bull")
    bear = sum(1 for s in stances.values() if s == "bear")
    neu = sum(1 for s in stances.values() if s == "neutral")
    unk = sum(1 for s in stances.values() if s == "unknown")
    avg = round(sum(confs) / len(confs), 1) if confs else 0.0

    if not got:
        level, conf, why = "UNKNOWN", 0.0, "四席位均未产出可用结论"
    elif bear >= 2:
        level = "STRONG_SELL" if (bear >= 3 or avg >= 60) else "SELL"
        conf, why = round(avg * 0.8, 1), "空头证据占多数（%d 席看空）" % bear
    elif bull >= 2 and bear == 0:
        level = "STRONG_BUY" if (bull >= 3 and avg >= 60) else "BUY"
        conf, why = avg, "多头证据成立且无空头反对（%d 席看多）" % bull
    else:
        level, conf = "HOLD", round(avg * 0.9, 1)
        if bull and bear:
            why = "多空对立（多%d/空%d）" % (bull, bear)
        elif bull or bear:
            why = "单边有立场但反方无证据（多%d/空%d/中%d/未%d）" % (bull, bear, neu, unk)
        elif neu:
            why = "四席均中性，无方向（中%d/未%d）" % (neu, unk)
        else:
            why = "四席均无立场（证据不足；降级 %d 席）" % len(degraded)

    horizon = "SHORT" if level in ("BUY", "STRONG_BUY", "SELL", "STRONG_SELL") else "MEDIUM"
    out = {
        "recommendation_level": level,
        "confidence_score": conf,                      # 0-100
        "risk_level": _risk_level(bull, bear, unk, len(degraded)),
        "time_horizon": horizon,
        "target_price_min": None,                      # 无可靠来源，留空不编造
        "target_price_max": None,
        "target_price_note": "缺行情/估值模型输出，按规则留空",
        "rationale": why,
        "stances": stances,
        "stance_counts": {"bull": bull, "bear": bear, "neutral": neu, "unknown": unk},
        "seats_ok": len(got), "seats_degraded": len(degraded), "seats_total": len(SEATS),
        "debated": bool(debate and debate.get("rounds")),
        "debate_changed": bool(debate and debate.get("changed")),
        "decided_by": "rule",
    }
    # 决胜席有效时覆盖规则结论（保留 rule_* 以便随时对比）
    fr = (final or {}).get("result") or {}
    if (final or {}).get("ok") and fr.get("decision_level"):
        lvl = str(fr["decision_level"])
        if lvl in ("STRONG_BUY", "BUY", "HOLD", "SELL", "STRONG_SELL"):
            out["rule_level"] = level
            out["rule_confidence"] = conf
            out["recommendation_level"] = lvl
            try:
                f_conf = float(fr.get("confidence") or 0)
                out["confidence_score"] = round(f_conf if f_conf > 1 else f_conf * 100, 1)
            except (TypeError, ValueError):
                pass
            out["rationale"] = "决胜席：%s" % str(fr.get("rationale") or "")[:200]
            out["decided_by"] = "final_seat"
            out["decisive_evidence"] = (fr.get("decisive_evidence") or [])[:3]
            out["rejected_arguments"] = (fr.get("rejected_arguments") or [])[:3]
            if lvl in ("SELL", "STRONG_SELL") and out["risk_level"] == "LOW":
                out["risk_level"] = "MEDIUM"
    elif final:
        out["final_seat_status"] = (final or {}).get("mode") or "degraded"
    return out


def upsert_index(row: dict) -> None:
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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="投研派活：池内标的 → 四席位 → 辩论 → 结构化结果")
    ap.add_argument("--pool-date", default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--model", default="deepseek-flash")
    ap.add_argument("--max-tokens", type=int, default=4000)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--no-xsec", action="store_true")
    ap.add_argument("--no-fin", action="store_true", help="不附带财务画像")
    ap.add_argument("--no-sent", action="store_true", help="不附带舆情画像")
    ap.add_argument("--no-final", action="store_true", help="不启用决胜席（只用票数规则）")
    ap.add_argument("--no-events", action="store_true", help="不附带公告事件")
    ap.add_argument("--debate-rounds", type=int, default=2, help="T1↔T2 互辩轮数（0=不辩）")
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
    log("POOL day=%s CN=%d source_mode=%s debate_rounds=%d"
        % (day, len(items), pool.get("source_mode"), args.debate_rounds))
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
        fin = {} if args.no_fin else slim_fin(finance_profile(code))
        sent = {} if args.no_sent else slim_sent(sentiment_profile(code))
        ev = {} if args.no_events else slim_events(events_profile(code))
        if ev:
            log("  %s events 近%d天%d条 last=%s" % (code, ev.get("window_days"),
                ev.get("window_count") or 0, ev.get("last_date")))
        if sent:
            log("  %s sentiment qa=%s holders=%s" % (code,
                (sent.get("qa") or {}).get("window_qa"),
                (sent.get("holders") or {}).get("holder_num")))
        if fin:
            log("  %s finance roe=%s margin=%s" % (code,
                (fin.get("profitability") or {}).get("roe"),
                (fin.get("profitability") or {}).get("net_margin")))
        if xs:
            log("  %s xsec primary=%s ops_hit=%s" % (code, xs.get("primary"),
                {k: v for k, v in (xs.get("ops_hit") or {}).items() if v}))
        seats = {}
        for seat in SEATS:
            r = ask_seat(client, seat, item, cards.get(seat) or {}, args.model, args.max_tokens, xs, fin, sent, ev)
            seats[seat] = r
            log("  %s %s ok=%s mode=%s %sms %s" % (code, seat, r["ok"], r["mode"], r["elapsed_ms"],
                                                   (r["result"] or {}).get("verdict", "")[:50]))

        debate = None
        sa, sb = _stance(seats.get("T1")), _stance(seats.get("T2"))
        if should_debate(sa, sb, args.debate_rounds):
            log("  %s 立场对立(T1=%s/T2=%s) → 辩论 %d 轮" % (code, sa, sb, args.debate_rounds))
            debate = run_debate(client, item, cards, seats, xs, args.model,
                                args.max_tokens, args.debate_rounds)
            for k, v in debate["final"].items():
                seats[k] = v
            log("  %s 辩论后 T1=%s T2=%s 改变=%s"
                % (code, _stance(debate["final"].get("T1")), _stance(debate["final"].get("T2")),
                   debate["changed"]))

        final = None
        cnt = {"bull": sum(1 for v in seats.values() if _stance(v) == "bull"),
               "bear": sum(1 for v in seats.values() if _stance(v) == "bear")}
        if not args.no_final and should_finalize(cnt["bull"], cnt["bear"]):
            log("  %s 多空对立(多%d/空%d) → 决胜席拍板" % (code, cnt["bull"], cnt["bear"]))
            final = run_final_decision(client, item, seats, debate, xs, fin, sent,
                                       args.model, args.max_tokens)
            fr = (final.get("result") or {})
            log("  %s 决胜席 -> %s conf=%s ok=%s | %s" % (code, fr.get("decision_level"),
                fr.get("confidence"), final.get("ok"), str(fr.get("rationale"))[:70]))

        summary = summarize(code, seats, debate, final)
        modes = {"normal": 0, "degraded": 0, "debate": 0}
        for v in seats.values():
            modes[v.get("mode") or "normal"] = modes.get(v.get("mode"), 0) + 1
        doc = {"schema": "research_dispatch/v2", "code": code, "name": item.get("name"),
               "pool_date": day, "data_date": item.get("data_date"),
               "seats": seats, "debate_log": (debate or {}).get("rounds"),
               "final_decision": (final or {}).get("result"),
               "summary": summary, "modes": modes,
               "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
               "model": args.model, "paper_only": True, "not_a_trade": True,
               "source_note": "ACAD1 池条目 + xsec 漏斗 + 四席位（含辩论）；不写 PG、不构成交易指令"}
        path = out_dir / ("%s.json" % code)
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        upsert_index({"code": code, "pool_date": day,
                      "recommendation": summary["recommendation_level"],
                      "confidence": summary["confidence_score"],
                      "risk_level": summary["risk_level"],
                      "seats_ok": summary["seats_ok"],
                      "seats_degraded": summary["seats_degraded"],
                      "file": str(path.relative_to(REPO))})
        log("  -> %s %s conf=%.1f risk=%s ok=%d/4 degraded=%d%s"
            % (code, summary["recommendation_level"], summary["confidence_score"],
               summary["risk_level"], summary["seats_ok"], summary["seats_degraded"],
               " 辩论过" if summary["debated"] else ""))
        ok_n += 1 if summary["seats_ok"] else 0

    log("SUMMARY processed=%d seats_productive=%d out=%s" % (len(items), ok_n, out_dir))
    return 0 if ok_n == len(items) else 1


if __name__ == "__main__":
    raise SystemExit(main())
