# -*- coding: utf-8 -*-
"""模拟市场端3：统一评判 Agent（K-STOCK-100 M1，077u）。

职责：每日评审模拟过程的问题——信号质量/执行偏差/风控行为/账本纪律。
- 规则评审（7 项，确定性可复算）是主体，评分只由规则产生；
- --llm 可选叙述分析（Ollama qwen3.5:9b，think:False），仅增强可读性，失败降级；
- 输出 k_stock/data/judge_reviews/YYYYMMDD.json，零写入 paper.db（只读）。

用法：
  python k_stock/paper/judge.py --date 20261012          # 评审某日（缺省=库内最新活动日）
  python k_stock/paper/judge.py --date 20261012 --llm    # 加 LLM 叙述
  python k_stock/paper/judge.py --date 20261012 --json   # 机器可读

评审项（R1-R7）与扣分：
  R1 陈旧产物拦截      —— 每单 -2（>0 说明上游产物新鲜度有问题）
  R2 replan 撤销       —— 每单 -1（>0 说明策略源换意图频繁）
  R3 拒单分布          —— 每单 -1（涨停拦截属正常风控，单列不扣）
  R4 同票重复 pending  —— 每组 -8（077q 事故级问题）
  R5 过期挂单          —— 每单 -5（plan_date 已过仍未撮/未撤）
  R6 撮合健康          —— rejected 率 >30% 再 -10
  R7 信号同质化        —— 同 stock 同方向多 strategy 落单，每组 -3
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
import logging
from tools.safe_urlopen import safe_urlopen

DB = Path(__file__).resolve().parent.parent / "data" / "paper.db"
OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "judge_reviews"
OLLAMA = "http://127.0.0.1:11434/api/chat"
LLM_MODEL = "qwen3.5:9b"

START_SCORE = 100.0


def _connect():
    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _pick_date(con, date):
    if date:
        return date
    row = con.execute("SELECT MAX(substr(ts,1,10)) d FROM orders").fetchone()
    return (row["d"] or "").replace("-", "") or datetime.now().strftime("%Y%m%d")


def review(date: str | None = None, llm: bool = False) -> dict:
    con = _connect()
    try:
        date = _pick_date(con, date)
        iso = f"{date[:4]}-{date[4:6]}-{date[6:]}"
        # 当日全部订单动作：ts 落单日 或 fill_date 成交日
        rows = [
            dict(r)
            for r in con.execute(
                "SELECT * FROM orders WHERE substr(ts,1,10)=? OR fill_date=? ORDER BY id",
                (iso, iso),
            )
        ]
        findings: list[dict] = []
        score = START_SCORE

        def flag(rule, sev, msg, n=1, deduct=0.0):
            nonlocal score
            findings.append({"rule": rule, "severity": sev, "msg": msg, "n": n})
            score -= deduct * n

        # R1 陈旧产物拦截（exec 守卫2 拒单）
        stale = [r for r in rows if "陈旧产物" in (r["reason"] or "")]
        if stale:
            flag(
                "STALE_ARTIFACT",
                "high",
                f"{len(stale)} 单被陈旧产物守卫拦截（asof 早于湖最新）——上游产物新鲜度问题",
                len(stale),
                2.0,
            )
        # R2 replan 撤销（守卫1）
        sup = [r for r in rows if "superseded" in (r["reason"] or "")]
        if sup:
            flag(
                "REPLAN_CANCELLED",
                "medium",
                f"{len(sup)} 单被新批次 replan 撤销——策略源换意图频繁（周末堆叠史留痕）",
                len(sup),
                1.0,
            )
        # R3 拒单分布（涨停拦截单列）
        rej = [r for r in rows if r["status"] == "rejected"]
        rej_limit = [r for r in rej if "涨停" in (r["reason"] or "")]
        rej_other = [r for r in rej if r not in rej_limit]
        if rej_limit:
            findings.append(
                {
                    "rule": "REJECT_LIMIT_UP",
                    "severity": "info",
                    "msg": f"{len(rej_limit)} 单涨停拦截（正常风控，不扣分）",
                    "n": len(rej_limit),
                }
            )
        if rej_other:
            flag(
                "REJECT_OTHER",
                "medium",
                "非涨停拒单："
                + "；".join(sorted({(r["reason"] or "?")[:40] for r in rej_other})),
                len(rej_other),
                1.0,
            )
        # R4 同票重复 pending（账本内；077q 事故形态）。跨账本同票是合法语义另列。
        pend = [
            dict(r)
            for r in con.execute(
                "SELECT account_id,stock,side,COUNT(*) n FROM orders WHERE status='pending' "
                "GROUP BY account_id,stock,side HAVING n>1"
            )
        ]
        for p in pend:
            flag(
                "DUP_PENDING",
                "critical",
                f"{p['account_id']} 账本 {p['stock']} {p['side']} 挂单 {p['n']} 批并存（077q 事故形态）",
                p["n"],
                8.0,
            )
        # R4b 跨账本同票同向重叠：合法（各账本独立目标仓位）但属信号同质化，info 级
        cross = [
            dict(r)
            for r in con.execute(
                "SELECT stock,side,COUNT(DISTINCT account_id) n FROM orders WHERE status='pending' "
                "GROUP BY stock,side HAVING n>1"
            )
        ]
        for c in cross:
            findings.append(
                {
                    "rule": "CROSS_ACCOUNT_OVERLAP",
                    "severity": "info",
                    "msg": f"{c['stock']} {c['side']} 被 {c['n']} 个账本同时持仓意图（同质化集中度，不扣分）",
                    "n": c["n"],
                }
            )
        # R5 过期挂单
        stale_pend = [
            dict(r)
            for r in con.execute(
                "SELECT stock,side,qty,plan_date FROM orders WHERE status='pending' "
                "AND plan_date IS NOT NULL AND plan_date < ?",
                (date,),
            )
        ]
        for sp in stale_pend:
            flag(
                "OVERDUE_PENDING",
                "high",
                f"{sp['stock']} 挂单 plan_date={sp['plan_date']} 已过未撮未撤",
                1,
                5.0,
            )
        # R6 撮合健康
        filled = [r for r in rows if r["status"] == "filled"]
        if rows:
            rej_rate = len(rej) / len(rows)
            findings.append(
                {
                    "rule": "FILL_STATS",
                    "severity": "info",
                    "msg": f"当日订单 {len(rows)}：成交 {len(filled)} / 拒 {len(rej)} / 其他 {len(rows) - len(filled) - len(rej)}",
                    "n": len(rows),
                }
            )
            if rej_rate > 0.30:
                flag(
                    "REJECT_RATE_HIGH",
                    "medium",
                    f"拒单率 {rej_rate:.0%} 超过 30% 警戒",
                    1,
                    10.0,
                )
        # R7 信号同质化：同 stock 同方向多 strategy
        by_stock = Counter(
            (r["stock"], r["side"])
            for r in rows
            if r["status"] in ("pending", "filled")
        )
        dup_strat = [(s, n) for s, n in by_stock.items() if n > 1]
        for s, n in dup_strat:
            strats = sorted(
                {r["strategy"] for r in rows if (r["stock"], r["side"]) == s}
            )
            if len(strats) > 1:
                flag(
                    "SIGNAL_OVERLAP",
                    "medium",
                    f"{s[0]} {s[1]} 被 {len(strats)} 源同时命中（{','.join(strats)}）——同质化加仓风险",
                    1,
                    3.0,
                )

        score = max(0.0, round(score, 1))
        out = {
            "ok": True,
            "date": iso,
            "score": score,
            "grade": "A"
            if score >= 90
            else "B"
            if score >= 75
            else "C"
            if score >= 60
            else "D",
            "findings": findings,
            "summary": {
                "orders": len(rows),
                "filled": len(filled),
                "rejected": len(rej),
                "pending_alive": con.execute(
                    "SELECT COUNT(*) FROM orders WHERE status='pending'"
                ).fetchone()[0],
            },
            "narrative": None,
        }
        if llm:
            out["narrative"] = _narrate(out)
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        fp = OUT_DIR / f"{date}.json"
        fp.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        out["_path"] = str(fp)
        return out
    finally:
        con.close()


def _narrate(review: dict) -> str | None:
    """LLM 叙述分析（可选增强，失败返回 None 不影响评分）。"""
    try:
        import urllib.request

        lines = (
            "\n".join(
                f"- [{f['severity']}] {f['rule']}: {f['msg']}"
                for f in review["findings"]
            )
            or "- 无发现"
        )
        prompt = (
            "你是模拟盘评审员。以下是当日规则评审结果（评分由规则产生，不要改分）。"
            "用不超过 200 字指出最值得老田关注的 1-2 个问题与下一步动作：\n"
            f"日期 {review['date']}，评分 {review['score']}\n{lines}"
        )
        body = json.dumps(
            {
                "model": LLM_MODEL,
                "stream": False,
                "think": False,
                "messages": [{"role": "user", "content": prompt}],
            }
        ).encode()
        req = urllib.request.Request(
            OLLAMA, data=body, headers={"Content-Type": "application/json"}
        )
        raw = safe_safe_urlopen(req, timeout=120).read().decode("utf-8")
        d = json.loads(raw)
        # 077w M2 后半：成本记账（本地 Ollama 单价 0，仍记调用量；失败不阻塞）
        try:
            try:
                from llm_cost import record_cost
            except ImportError:
                from k_stock.paper.llm_cost import record_cost
            u = d.get("prompt_eval_count"), d.get("eval_count")
            if all(u):
                record_cost("judge", f"ollama/{LLM_MODEL}", u[0], u[1])
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "k_stock/paper/judge.py", exc)
        msg = d.get("message", {}).get("content", "").strip()
        # 旧版 Ollama 忽略 think:False → 剥 <think> 段兜底
        if "</think>" in msg:
            msg = msg.split("</think>", 1)[1].strip()
        return msg or None
    except Exception as e:  # noqa: BLE001
        return f"(LLM 叙述失败降级: {type(e).__name__}: {e})"


def meeting(date: str | None = None, llm: bool = False) -> dict:
    """K-STOCK-122 M5 投研经理每日复盘会（judge 升级为会议主持人）。

    三方输入：① 投研经理=当日 review() 评审；② 分析师=strategist_latest.json
    （策略源建议）；③ 操盘手=当日盯盘动作（stop/tp 触发单+最新净值）。
    产出 review_minutes/YYYY-MM-DD.json：inputs + 分歧点 + 决议 + action_items，
    全量留存=蒸馏语料增量（K-STOCK-100 M4）。llm=True 时经理叙述由 LLM 生成
    （失败降级不阻塞）。异常整体不抛（routine 尾挂点约定）。"""
    try:
        out: dict = {
            "ok": True,
            "date": None,
            "attendees": ["analyst", "trader", "manager"],
        }
        rev = review(date, llm=False)
        out["date"] = rev["date"]
        out["manager"] = {
            "score": rev.get("score"),
            "grade": rev.get("grade"),
            "findings": rev.get("findings"),
            "narrative": rev.get("narrative"),
        }
        # ② 分析师：策略源建议
        ana_path = OUT_DIR.parent / "strategist_latest.json"
        try:
            ana = json.loads(ana_path.read_text(encoding="utf-8"))
            out["analyst"] = {
                k: ana.get(k)
                for k in ("date", "suggestions", "summary", "note")
                if ana.get(k) is not None
            }
        except Exception as e:  # noqa: BLE001
            out["analyst"] = {"ok": False, "error": repr(e)}
        # ③ 操盘手：当日盯盘动作 + 最新净值
        con = _connect()
        try:
            iso = rev["date"]
            acts = [
                dict(r)
                for r in con.execute(
                    "SELECT account_id,stock,side,order_kind,status,reason FROM orders "
                    "WHERE (order_kind IN ('stop','tp') OR reason LIKE '%%触发%%') "
                    "AND (substr(ts,1,10)=? OR fill_date=?) ORDER BY id",
                    (iso, iso),
                )
            ]
            eq = con.execute(
                "SELECT date,total FROM equity_marks WHERE account_id='main' "
                "ORDER BY date DESC LIMIT 2"
            ).fetchall()
            out["trader"] = {"actions": acts, "equity_recent": [dict(r) for r in eq]}
        finally:
            con.close()
        # 博弈分歧点：分析师建议与经理 findings 相冲时记录（首版规则法）
        out["disagreements"] = _disagreements(out)
        out["resolutions"] = [
            {"on": d["on"], "by": "manager", "decision": d["suggestion"]}
            for d in out["disagreements"]
        ] or [{"on": "daily", "by": "manager", "decision": "按既有纪律继续执行"}]
        out["action_items"] = [
            f["msg"][:80]
            for f in rev.get("findings", [])
            if f.get("severity") in ("critical", "high")
        ]
        if llm:
            out["minutes_narrative"] = _meeting_narrate(out)
        mdir = OUT_DIR.parent / "review_minutes"
        mdir.mkdir(parents=True, exist_ok=True)
        fp = mdir / f"{rev['date']}.json"
        fp.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        out["_path"] = str(fp)
        return out
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": repr(e)}


def _disagreements(out: dict) -> list:
    """首版规则法：经理 findings 批评的域 与 分析师建议同域 → 记分歧。"""
    dis = []
    ana = out.get("analyst") or {}
    sugg = json.dumps(
        ana.get("suggestions") or ana.get("summary") or "", ensure_ascii=False
    )
    for f in out["manager"].get("findings") or []:
        if f.get("severity") in ("critical", "high") and len(sugg) > 4:
            dis.append(
                {
                    "on": f["rule"],
                    "manager": f["msg"][:100],
                    "analyst": sugg[:100],
                    "suggestion": f"以经理评审为准：{f['msg'][:60]}；"
                    f"分析师建议降权观察",
                }
            )
    return dis


def _meeting_narrate(out: dict) -> str | None:
    """经理 LLM 纪要叙述（复用 Ollama 链路，account=manager 计量）。"""
    try:
        import urllib.request

        brief = json.dumps(
            {k: out.get(k) for k in ("manager", "analyst", "trader", "disagreements")},
            ensure_ascii=False,
        )[:1500]
        prompt = (
            "你是投研经理，主持模拟盘每日复盘会。基于三方输入写不超过"
            "250 字会议纪要：一句话总评 + 分歧裁决 + 明日 1-2 个动作：\n"
            f"日期 {out['date']}\n{brief}"
        )
        body = json.dumps(
            {
                "model": LLM_MODEL,
                "stream": False,
                "think": False,
                "messages": [{"role": "user", "content": prompt}],
            }
        ).encode()
        req = urllib.request.Request(
            OLLAMA, data=body, headers={"Content-Type": "application/json"}
        )
        raw = safe_safe_urlopen(req, timeout=120).read().decode("utf-8")
        d = json.loads(raw)
        try:
            try:
                from llm_cost import record_cost
            except ImportError:
                from k_stock.paper.llm_cost import record_cost
            u = d.get("prompt_eval_count"), d.get("eval_count")
            if all(u):
                record_cost("manager", f"ollama/{LLM_MODEL}", u[0], u[1])
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "k_stock/paper/judge.py", exc)
        msg = d.get("message", {}).get("content", "").strip()
        if "</think>" in msg:
            msg = msg.split("</think>", 1)[1].strip()
        return msg or None
    except Exception as e:  # noqa: BLE001
        return f"(LLM 纪要失败降级: {type(e).__name__}: {e})"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="模拟仓日评审（端3 judge）")
    ap.add_argument("--date", default=None, help="YYYYMMDD（缺省=库内最新活动日）")
    ap.add_argument("--llm", action="store_true", help="追加 Ollama 叙述分析")
    ap.add_argument("--json", action="store_true")
    ap.add_argument(
        "--meeting",
        action="store_true",
        help="K-STOCK-122 M5：开投研经理复盘会（产出会议纪要）",
    )
    a = ap.parse_args(argv)
    if a.meeting:
        r = meeting(a.date, llm=a.llm)
        if a.json:
            print(json.dumps(r, ensure_ascii=False))
        else:
            print(
                ("✅ 复盘会完成 " if r.get("ok") else "❌ 复盘会失败 ")
                + json.dumps(
                    {k: r.get(k) for k in ("date", "disagreements", "_path")},
                    ensure_ascii=False,
                )
            )
        return 0 if r.get("ok") else 1
    r = review(a.date, llm=a.llm)
    if a.json:
        print(json.dumps(r, ensure_ascii=False))
    else:
        print(f"📋 {r['date']} 评审：{r['score']} 分（{r['grade']}）→ {r['_path']}")
        for f in r["findings"]:
            print(f"  [{f['severity']}] {f['rule']}: {f['msg']}")
        if r["narrative"]:
            print(f"  🗣 {r['narrative']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
