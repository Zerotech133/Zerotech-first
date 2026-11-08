# -*- coding: utf-8 -*-
"""AIDE 研判卡预生成 —— 把「打开页面才现算」改成「浏览前已梳理好」。

为什么要有这个
--------------
旧链路：用户点开 → 现算 facts（11~13s）→ 本地模型推理（>100s）→ 才出结论。
770 只关注池全量现算等于 21 小时，用户不可能等。

本脚本在**盘后**把研判卡先算好写进库，前端打开即读（0 秒）。分层调度：

    L1  T1+T2（共振 / 高价值，约 32 只）—— 盘后必跑
    L2  T3（单策略，约 738 只）—— 夜间慢跑，或等云端配置好后并发跑
    L3  长尾 —— 不预生成，点开时在线算

算力现实（2026-11-08 实测）
--------------------------
本机无独显（AMD 8060S 核显），Ollama qwen3.5:4b 单条 >100s。故 770 只本地
串行不可行（约 21 小时）。云端通道已在 api_jev_interpret 里备好，但
config/cloud_llm.json 目前是模板占位（enabled=false），配好 key 后 L2 才可并发。

产出
----
intel_facts.db 的 intel_briefs 表（与五要素同库，研判域内自洽）：
    (code, trade_date, kind) 主键；payload/facts/conclusion/engine/model/datasig

用法
----
    python -X utf8 tools/intel_brief_pregen.py --dry-run          # 只列计划
    python -X utf8 tools/intel_brief_pregen.py                    # L1
    python -X utf8 tools/intel_brief_pregen.py --tier L2 --limit 50
    python -X utf8 tools/intel_brief_pregen.py --force            # 强制重算
    python -X utf8 tools/intel_brief_pregen.py --codes 300750,600519

长跑纪律：进度与结论全部落 logs/intel_brief_pregen.log，不回传管道。
退出码：0 成功（含部分失败）；2 参数/数据源错误。
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import os
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORTAL = os.path.join(ROOT, "portal")
PICKS = os.path.join(PORTAL, "data", "todays_picks.json")
FACTS_DB = os.path.join(ROOT, "agent_platform", "data", "intel",
                        "intel_facts.db")
LOG_PATH = os.path.join(ROOT, "logs", "intel_brief_pregen.log")

sys.path.insert(0, PORTAL)

DDL = """
CREATE TABLE IF NOT EXISTS intel_briefs (
    code         TEXT NOT NULL,
    trade_date   TEXT NOT NULL,
    kind         TEXT NOT NULL,
    name         TEXT,
    tier         TEXT,
    facts        TEXT,
    conclusion   TEXT,
    engine       TEXT,
    engine_label TEXT,
    model        TEXT,
    degrade_note TEXT,
    datasig      TEXT,
    built_at     TEXT,
    PRIMARY KEY (code, trade_date, kind)
);
CREATE INDEX IF NOT EXISTS idx_ib_date ON intel_briefs(trade_date, tier);
"""

_LOG_FP = None


def log(msg: str) -> None:
    global _LOG_FP
    line = "[%s] %s" % (dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    if _LOG_FP is None:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        _LOG_FP = io.open(LOG_PATH, "a", encoding="utf-8")
    _LOG_FP.write(line + "\n")
    _LOG_FP.flush()


def load_pool(tiers: set) -> list:
    """关注池：todays_picks.json 的 stocks[]，带 tier / name / industry。"""
    try:
        with io.open(PICKS, encoding="utf-8") as f:
            d = json.load(f) or {}
    except Exception as exc:  # noqa: BLE001
        raise SystemExit("关注池读取失败：%s（%s）" % (PICKS, exc))
    out = []
    for s in d.get("stocks") or []:
        tier = str(s.get("tier") or "T3").upper()
        tag = "L2" if tier.startswith("T3") else "L1"
        if tiers and tag not in tiers:
            continue
        code = str(s.get("code") or "").strip()
        if not code:
            continue
        out.append({"code": code, "name": s.get("name"),
                    "tier": tier, "layer": tag})
    return out


def _datasig(facts: str) -> str:
    return hashlib.sha256((facts or "").encode("utf-8")).hexdigest()[:16]


def pregen(tiers: set, trade_date: str, limit: int = 0, force: bool = False,
           codes: list = None, dry: bool = False) -> dict:
    import api_jev_interpret as AI

    if codes:
        pool = [{"code": c, "name": None, "tier": "MANUAL", "layer": "MANUAL"}
                for c in codes]
    else:
        pool = load_pool(tiers)
    if limit:
        pool = pool[:limit]
    log("计划 %d 只（tiers=%s, date=%s, force=%s, dry=%s）"
        % (len(pool), ",".join(sorted(tiers)) or "all", trade_date, force, dry))
    if dry:
        for p in pool[:20]:
            log("  DRY %-12s %-10s %s" % (p["code"], p["layer"], p.get("name") or ""))
        if len(pool) > 20:
            log("  ... 其余 %d 只略" % (len(pool) - 20))
        return {"planned": len(pool), "dry": True}

    os.makedirs(os.path.dirname(FACTS_DB), exist_ok=True)
    db = sqlite3.connect(FACTS_DB)
    db.executescript(DDL)
    db.commit()

    done = skipped = failed = 0
    stat = {"local": 0, "cloud": 0, "rule": 0}
    t0 = time.time()
    for i, p in enumerate(pool, 1):
        code = p["code"]
        old = db.execute(
            "SELECT datasig FROM intel_briefs WHERE code=? AND trade_date=?"
            " AND kind='story'", (code, trade_date)).fetchone()
        try:
            # 先只算 facts（0.7s），指纹一致就跳过推理——省下 100s/只
            facts = AI._facts("story", code, 30)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            log("  [%d/%d] %s facts 失败：%s" % (i, len(pool), code,
                                                 str(exc)[:70]))
            continue
        sig = _datasig(facts)
        if old and old[0] == sig and not force:
            skipped += 1
            continue
        try:
            # force=True：绕过沉淀卡与指纹，真正重新研判——否则会读回自己
            # 上次写的那张旧卡（指纹判断已在此之前完成，此处必须强制重算）
            r = AI.interpret("story", code, "", 30, force=True)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            log("  [%d/%d] %s 推理失败：%s" % (i, len(pool), code, str(exc)[:70]))
            continue
        eng = r.get("engine") or "rule"
        stat[eng] = stat.get(eng, 0) + 1
        db.execute(
            "INSERT INTO intel_briefs (code, trade_date, kind, name, tier,"
            " facts, conclusion, engine, engine_label, model, degrade_note,"
            " datasig, built_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(code, trade_date, kind) DO UPDATE SET"
            " name=excluded.name, tier=excluded.tier, facts=excluded.facts,"
            " conclusion=excluded.conclusion, engine=excluded.engine,"
            " engine_label=excluded.engine_label, model=excluded.model,"
            " degrade_note=excluded.degrade_note, datasig=excluded.datasig,"
            " built_at=excluded.built_at",
            (code, trade_date, "story", p.get("name"), p.get("tier"),
             r.get("facts"), r.get("conclusion"), eng, r.get("engine_label"),
             r.get("model"), r.get("degrade_note"), sig, r.get("generated_at")))
        db.commit()
        done += 1
        el = time.time() - t0
        eta = el / i * (len(pool) - i) if i else 0
        log("  [%d/%d] %-12s %-8s %s  %.1fs  剩余约 %.0f 分钟"
            % (i, len(pool), code, eng, (p.get("name") or "")[:6], el / i,
               eta / 60))

    total = db.execute("SELECT count(*) FROM intel_briefs WHERE trade_date=?",
                       (trade_date,)).fetchone()[0]
    # by_engine 只统计「本次运行」的产出；库里当日可能还有别的批次（手工
    # 试跑、L2 夜间补跑），混在一起会把本轮成功率算错。
    db_by_eng = dict(db.execute(
        "SELECT engine, count(*) FROM intel_briefs WHERE trade_date=?"
        " GROUP BY engine", (trade_date,)).fetchall())
    db.close()
    res = {"planned": len(pool), "done": done, "skipped": skipped,
           "failed": failed, "by_engine": stat, "db_today": total,
           "db_today_by_engine": db_by_eng,
           "seconds": round(time.time() - t0, 1), "trade_date": trade_date}
    log("完成 %s" % json.dumps(res, ensure_ascii=False))
    return res


def _default_trade_date() -> str:
    """默认交易日 = 最近一个「已收盘」A 股交易日。

    不能用 today：周末/节假日/盘中跑会把卡标成自然日（实测出现过
    trade_date=周日、数据实为周五的卡），页面读起来像"周日也有行情"，
    按交易日做的时效判断也会跟着错。日历取不到才退回 today。
    """
    try:
        p = os.path.join(ROOT, "quant_backtest")
        if p not in sys.path:
            sys.path.insert(0, p)
        import datamart as dm
        e = str(dm._expected_trade_day(dm._recent_trade_days()) or "")[:8]
        if len(e) == 8 and e.isdigit():
            return "%s-%s-%s" % (e[:4], e[4:6], e[6:])
    except Exception:  # noqa: BLE001 —— 日历故障退回自然日，不阻断预生成
        pass
    return dt.date.today().strftime("%Y-%m-%d")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="AIDE 研判卡预生成")
    p.add_argument("--tier", default="L1", help="L1 / L2 / L1,L2 / all")
    p.add_argument("--date", default="",
                   help="交易日，默认最近一个已收盘交易日")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--codes", default="", help="指定代码，逗号分隔")
    p.add_argument("--force", action="store_true", help="忽略指纹强制重算")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args(argv)
    raw = (a.tier or "L1").strip()
    tiers = set() if raw.lower() in ("all", "*", "") else {
        t.strip().upper() for t in raw.split(",") if t.strip()}
    codes = [c.strip() for c in (a.codes or "").split(",") if c.strip()]
    date = a.date or _default_trade_date()
    res = pregen(tiers, date, limit=a.limit, force=a.force,
                 codes=codes, dry=a.dry_run)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
