# -*- coding: utf-8 -*-
"""K69 模拟投委会（预演层）：真实投委会（夜链 23:50）开会前先行预演。

用户愿景 2026-10-04：在真实投委会之前先跑一遍「模拟投委会」预演辩论，
提前看到可能的辩论结论；真实会开完后自动对照打分，积累
「预演 vs 真实」一致性统计——预演稳了，才敢让辩论链路往前走。

管线（辩论引擎全部复用真实投委会，零口径漂移）：
  候选票（与真实立案会同源：账本当日新建仓 / --code 单票）
  → _ctx_of → _recall → 6×_view_run 并发 → _host 主持
  → _vote_verdict 加权投票 → _directive 指令+K66 系数 → _style_of 分桶
  → 独立留痕 reports/council_sim_proposals.jsonl（rec 带 sim=True）
真实会后 compare() 按 (sid, code, d) 全量对齐打分
  → reports/council_sim_score.json（verdict 一致率 / Δwsum / Δ系数 /
     六视角逐角色一致率）

铁律：预演留痕绝不进真实 proposals / pending / 六本账，只对照不打扰；
不推飞书（预演是给用户自己看的，夜链真会才推卡）。

CLI：
  python council_sim.py                     # 预演当日候选票（幂等防重）
  python council_sim.py --code 600663.SH    # 单票预演
  python council_sim.py --force             # 同日同票重演（默认跳过）
  python council_sim.py --compare-only      # 只重算对照打分
"""

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from stock_council import (
    COUNCIL,
    PROPOSALS,
    REPORTS,
    SID_DESC,
    _alive,
    _ctx_of,
    _directive,
    _host,
    _llm,
    _recall,
    _STANCE_RE,
    _style_of,
    _today_new,
    _view_run,
    _vote_verdict,
)
import logging
import atomic_io  # noqa: E402  (P2-c 统一原子写)

SIM_PROPOSALS = os.path.join(REPORTS, "council_sim_proposals.jsonl")
SCORE = os.path.join(REPORTS, "council_sim_score.json")
ROLES = ("bull", "bear", "aggr", "cons", "tech", "senti")

# K72 操盘手质询人设（执行视角，对应六本账三种个性）
TRADER_SPECS = {
    "aggr": {
        "name": "激进操盘手",
        "system": (
            "你是激进操盘手（短线进攻型），列席模拟投委会的操盘"
            "手质询环节。分析师团队刚完成辩论，你从执行视角审视其"
            "结论：动能与量能是否支持更快更重的进场？回调是不是"
            "上车点？你的账本风格：满基数建仓、止盈半出后追踪、"
            "最长持有 40 日。用 3-4 句中文直说操作主张（是否接单、"
            "仓位轻重、入场节奏、止损容忍），可以质疑分析师的保守"
            "或迟疑。最后单独一行 STANCE:n（-2..+2，正=支持按分析"
            "师结论进场做多，负=反对）。"
        ),
    },
    "cons": {
        "name": "保守操盘手",
        "system": (
            "你是保守操盘手（风控优先型），列席模拟投委会的操盘"
            "手质询环节。分析师团队刚完成辩论，你从风控视角审视其"
            "结论：止损距离是否太近？盈亏比是否划算？流动性够不"
            "够撤退？你的账本风格：半基数建仓、止盈全出落袋、最"
            "长持有 20 日、满仓时宁可放弃新票也不挤仓。用 3-4 句"
            "中文直说操作主张，可以质疑分析师的乐观或冒进。最后"
            "单独一行 STANCE:n（-2..+2，同上口径）。"
        ),
    },
    "neut": {
        "name": "中立操盘手",
        "system": (
            "你是中立操盘手（纪律执行型），列席模拟投委会的操盘"
            "手质询环节。你不带情绪，只按信号与纪律办事：分析师"
            "结论是否明确？价位线是否有效？时间窗是否合适？你的"
            "账本风格：按系数执行、只挤过半窗且浮亏的持仓让位。"
            "用 3-4 句中文直说执行判断（接单/观望/等回踩，依据哪条"
            "纪律）。最后单独一行 STANCE:n（-2..+2，同上口径）。"
        ),
    },
}


def _books_brief(trader_prefix):
    """读六本账（trader_sim.json）该操盘手两本账的持仓概况——辩论时
    知道自己的仓位处境，质询才有执行语境。"""
    try:
        import trader_sim as _ts

        with open(_ts.SIM, encoding="utf-8") as f:
            sim = json.load(f)
        n = used = 0
        for bk, book in (sim.get("books") or {}).items():
            if not bk.startswith(trader_prefix + "|"):
                continue
            pos = book.get("pos") or {}
            n += len(pos)
            used = max(used, round(sum(p.get("size_eff", 0) for p in pos.values()), 2))
        return "你的两本账当前 %d 笔在仓，单账仓位占用约 %.0f%%（上限 100%%）" % (
            n,
            used * 100,
        )
    except Exception:
        return "六本账暂无数据（首笔建仓前）"


def _trader_one(rk, t, memo, vm, vote, directive):
    """单操盘手质询：看分析师辩论全貌 + 自己账本处境 → 执行视角发言。"""
    spec = TRADER_SPECS[rk]
    prefix = {"aggr": "激进", "cons": "保守", "neut": "中立"}[rk]
    try:
        user = (
            "【标的】%s %s（策略来源 %s）\n【分析师辩论纪要】%s\n"
            "【六视角立场】%s\n【投票】%s（加权 %+.1f）→ 指令 %s，"
            "仓位系数 k=%.2f\n【你的账本处境】%s\n"
            "请给出你的执行视角质询（可同意、可质疑、可提操作替代案）。"
            % (
                t.get("code"),
                t.get("name") or "",
                SID_DESC.get(t.get("sid"), t.get("sid") or "?"),
                "；".join(
                    "%s：%s" % (lab, memo.get(kk))
                    for kk, lab in (
                        ("thesis", "论点"),
                        ("risks", "风险"),
                        ("if_wrong", "错了怎么办"),
                    )
                    if memo.get(kk)
                )[:600],
                " / ".join("%s:%+d" % (k, vm.get(k, 0)) for k in ROLES),
                vote.get("verdict"),
                vote.get("wsum") or 0,
                directive.get("act") or "—",
                (directive.get("sizing") or {}).get("k", 1.0),
                _books_brief(prefix),
            )
        )
        ans = (
            _llm(
                [
                    {"role": "system", "content": spec["system"]},
                    {"role": "user", "content": user},
                ]
            ).get("content")
            or ""
        ).strip()
    except Exception as ex:
        ans, ex0 = "", str(ex)[:80]
        return {
            "role": rk,
            "name": spec["name"],
            "stance": 0,
            "answer": "（质询失败：%s）" % ex0,
        }
    m = _STANCE_RE.search(ans)
    stance = max(-2, min(2, int(m.group(1)))) if m else 0
    return {"role": rk, "name": spec["name"], "stance": stance, "answer": ans}


def _trader_views(t, memo, vm, vote, directive):
    """K72 操盘手轮：三位操盘手并发质询（分析师 vs 操盘手的辩论第二幕）。"""
    with ThreadPoolExecutor(max_workers=3) as ex:
        return list(
            ex.map(
                lambda rk: _trader_one(rk, t, memo, vm, vote, directive),
                list(TRADER_SPECS),
            )
        )


def _analyst_rebuttal(t, memo, vm, vote, directive, tviews):
    """K73 交锋轮一：主持人代分析师团回应操盘手三方质疑——
    分析师不是被质询完就沉默，逐点回应才有真辩论。"""
    try:
        user = (
            "【标的】%s %s\n【你的分析师团队结论】票%s（加权 %+.1f），"
            "指令 %s，系数 k=%.2f；六视角立场 %s\n【纪要】%s\n"
            "【操盘手三方质询】%s\n"
            "请逐点回应三位操盘手的质疑：哪些成立需要吸收进结论？"
            "哪些基于数据可以反驳？4-6 句中文，可调整结论但须说明依据。"
            % (
                t.get("code"),
                t.get("name") or "",
                vote.get("verdict"),
                vote.get("wsum") or 0,
                directive.get("act") or "—",
                (directive.get("sizing") or {}).get("k", 1.0),
                " / ".join("%s:%+d" % (k, vm.get(k, 0)) for k in ROLES),
                "；".join(
                    "%s：%s" % (lab, memo.get(kk))
                    for kk, lab in (
                        ("thesis", "论点"),
                        ("risks", "风险"),
                        ("if_wrong", "错了怎么办"),
                    )
                    if memo.get(kk)
                )[:400],
                "\n".join(
                    "· %s（%+d）：%s"
                    % (v["name"], v["stance"], v["answer"].replace("\n", " ")[:280])
                    for v in tviews
                ),
            )
        )
        ans = (
            _llm(
                [
                    {
                        "role": "system",
                        "content": (
                            "你是模拟投委会主持人，此刻代表分析师"
                            "团队接受操盘手质询。你熟悉六视角全部"
                            "论据，可以承认合理质疑、修正结论，也可"
                            "以用数据反驳——立场要诚实，不为面子"
                            "辩护。"
                        ),
                    },
                    {"role": "user", "content": user},
                ]
            ).get("content")
            or ""
        ).strip()
    except Exception as ex:
        return {"answer": "（回应失败：%s）" % str(ex)[:80]}
    return {"answer": ans}


def _trader_second(rk, t, reb):
    """K73 交锋轮二：单操盘手看分析师回应后再表态（立场可演化）。"""
    spec = TRADER_SPECS[rk]
    try:
        user = (
            "【标的】%s %s\n【你此前的质询】%s\n【分析师团队回应】%s\n"
            "请再表态：你是否被说服？坚持还是修正此前立场？1-2 句"
            "中文说明，最后单独一行 STANCE:n（-2..+2，你的最新立场）。"
            % (
                t.get("code"),
                t.get("name") or "",
                (spec.get("last_answer") or "")[:280],
                (reb.get("answer") or "")[:600],
            )
        )
        ans = (
            _llm(
                [
                    {"role": "system", "content": spec["system"]},
                    {"role": "user", "content": user},
                ]
            ).get("content")
            or ""
        ).strip()
    except Exception as ex:
        return {"stance2": None, "answer2": "（再表态失败：%s）" % str(ex)[:80]}
    m = _STANCE_RE.search(ans)
    st2 = max(-2, min(2, int(m.group(1)))) if m else None
    return {"stance2": st2, "answer2": ans}


def _trader_seconds(t, tviews, reb):
    """K73 交锋轮二：三位操盘手并发再表态（质询观点写回 spec 传语境）。"""
    for v in tviews:
        TRADER_SPECS[v["role"]]["last_answer"] = v["answer"]
    try:
        with ThreadPoolExecutor(max_workers=3) as ex:
            secs = list(
                ex.map(lambda rk: _trader_second(rk, t, reb), list(TRADER_SPECS))
            )
    finally:
        for rk in TRADER_SPECS:
            TRADER_SPECS[rk].pop("last_answer", None)
    for v, s in zip(tviews, secs):
        v.update(s)
    return tviews


def _final_round(t, vote, directive, tviews):
    """K72/K73 终裁轮：主持人综合分析师结论、交锋与立场演化 → 终裁。"""
    try:
        user = (
            "【标的】%s %s\n【分析师结论】票%s（加权 %+.1f），指令 %s，"
            "系数 k=%.2f\n【操盘手质询】%s\n"
            "请终裁：3-5 句中文总结分歧焦点与裁决，说明是否维持分析师"
            "原结论。最后单独一行 STANCE:n（-2..+2，终裁立场）。"
            % (
                t.get("code"),
                t.get("name") or "",
                vote.get("verdict"),
                vote.get("wsum") or 0,
                directive.get("act") or "—",
                (directive.get("sizing") or {}).get("k", 1.0),
                "\n".join(
                    "· %s（%+d）：%s"
                    % (v["name"], v["stance"], v["answer"].replace("\n", " ")[:300])
                    for v in tviews
                ),
            )
        )
        ans = (
            _llm(
                [
                    {
                        "role": "system",
                        "content": (
                            "你是模拟投委会终裁主持人。分析师团队与"
                            "三位操盘手（激进/保守/中立）已完成两轮"
                            "辩论，你综合双方交锋给出终裁立场，重点"
                            "评估执行层的质疑是否成立。"
                        ),
                    },
                    {"role": "user", "content": user},
                ]
            ).get("content")
            or ""
        ).strip()
    except Exception as ex:
        return {"stance": None, "verdict": "终裁失败", "note": str(ex)[:80]}
    m = _STANCE_RE.search(ans)
    stance = max(-2, min(2, int(m.group(1)))) if m else None
    verdict = (
        "支持" if (stance or 0) >= 1 else "反对" if (stance or 0) <= -1 else "中性"
    )
    return {"stance": stance, "verdict": verdict, "note": ans}


def _jl(fp):
    out = []
    try:
        with open(fp, encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/council_sim.py", exc)
    return out


def _sim_done(day):
    """当日已预演的 code 集合（幂等防重演）。"""
    return {
        r.get("code") for r in _jl(SIM_PROPOSALS) if r.get("d") == day and r.get("code")
    }


def run_one_sim(t):
    """复刻真实 run_one 管线，差异只有三处：写 SIM 留痕 / sim=True /
    不推飞书。返回 rec。"""
    t0 = time.time()
    ctx = _ctx_of(t)
    mem = _recall(t)
    if mem:
        ctx += "\n\n历史复盘经验（同类标的复盘沉淀，供参考不强制）：\n%s" % "\n".join(
            "- " + m["text"] for m in mem
        )
    with ThreadPoolExecutor(max_workers=3) as ex:
        views = list(ex.map(lambda k: _view_run(k, ctx), list(COUNCIL)))
    memo = _host(ctx, views)
    stances = [v["stance"] for v in views]
    dissent = round(100 * (max(stances) - min(stances)) / 4.0)
    vm = {v["role"]: v["stance"] for v in views}
    vote = _vote_verdict(vm)
    directive = _directive(memo, vm, dissent, vote)
    # K72/K73 模拟辩论：操盘手质询 → 分析师回应 → 再表态 → 终裁
    # （真实会没有，预演独有；质询-回应-再表态=真交锋，立场可演化）
    tviews = _trader_views(t, memo, vm, vote, directive)
    reb = _analyst_rebuttal(t, memo, vm, vote, directive, tviews)
    tviews = _trader_seconds(t, tviews, reb)
    fin = _final_round(t, vote, directive, tviews)
    style, style_score = _style_of(vm)
    params = {
        v["param_key"]: v["param"]
        for v in views
        if v.get("param_key") and v.get("param")
    }
    rec = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "d": t.get("entry_date") or time.strftime("%Y-%m-%d"),
        "code": t.get("code"),
        "name": t.get("name"),
        "sid": t.get("sid"),
        "sid_label": SID_DESC.get(t.get("sid"), t.get("sid") or "?"),  # K70 来源自包含
        "market": t.get("market"),
        "entry_px": t.get("entry_px"),
        "qty": t.get("qty"),
        "dissent": dissent,
        "views": {
            v["role"]: {
                "stance": v["stance"],
                "stance_ok": v["stance_ok"],
                "param": v["param"],
                "answer": v["answer"],
            }
            for v in views
        },
        "memo": memo,
        "mem_used": mem,
        "shadow": True,
        "sim": True,
        "vote": vote,
        "directive": directive,
        "style": style,
        "style_score": style_score,
        "params": params,
        "traders": tviews,
        "rebuttal": reb,
        "final": fin,
        "elapsed_s": round(time.time() - t0, 1),
    }
    os.makedirs(REPORTS, exist_ok=True)
    # K118 append 加锁：与 portal _debate_say（锁内重读合并全量重写）
    # 共用同一独立锁文件，防「插话写回吞掉并发 append 新行」交错
    import msvcrt

    lk = open(SIM_PROPOSALS + ".lock", "a+b")
    try:
        lk.seek(0)
        msvcrt.locking(lk.fileno(), msvcrt.LK_LOCK, 1)
        try:
            with open(SIM_PROPOSALS, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        finally:
            lk.seek(0)
            msvcrt.locking(lk.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        lk.close()
    print(
        "[COUNCIL-SIM] %s %s dissent=%d 票%s(w%+.1f) 主%s → %s（%s桶·k%.1f）"
        "(%.0fs)"
        % (
            t.get("code"),
            t.get("name") or "",
            dissent,
            vote["verdict"],
            vote["wsum"],
            memo.get("verdict"),
            directive["act"],
            style,
            (directive.get("sizing") or {}).get("k", 1.0),
            rec["elapsed_s"],
        )
    )
    tv = " / ".join(
        "%s%+d%s"
        % (
            v["name"][:2],
            v["stance"],
            ("→%+d" % v["stance2"]) if v.get("stance2") is not None else "",
        )
        for v in tviews
    )
    print("[COUNCIL-SIM]   操盘手质询：%s → 终裁 %s" % (tv, fin.get("verdict")))
    return rec


def _targets(led, day, codes=None):
    """候选票：--code 单票（从 open 撸上下文）/ 否则与真实立案会同源。"""
    if codes:
        out = []
        for sid, ac in (led.get("accounts") or {}).items():
            for p in ac.get("open") or []:
                if p.get("code") in codes:
                    out.append(
                        {
                            "sid": sid,
                            "code": p.get("code"),
                            "name": p.get("name"),
                            "market": p.get("market"),
                            "entry_px": p.get("entry_px"),
                            "qty": p.get("qty"),
                            "entry_date": day,
                            "pos_pct": None,
                        }
                    )
        return out or [
            {
                "sid": "manual",
                "code": c,
                "name": "",
                "market": None,
                "entry_px": None,
                "qty": None,
                "entry_date": day,
                "pos_pct": None,
            }
            for c in codes
        ]
    return _today_new(led, day)


def run(codes=None, force=False):
    """预演当日候选票 → 留痕 → 对照打分。返回 recs。"""
    if not _alive():
        print("[COUNCIL-SIM] OLLAMA_DOWN skip（预演层降级）")
        return []
    from portfolio import load_ledger

    led, _cfg = load_ledger()
    day = (led.get("ts") or time.strftime("%Y-%m-%d"))[:10]
    done = set() if force else _sim_done(day)
    targets = [t for t in _targets(led, day, codes) if t.get("code") not in done]
    if not targets:
        print("[COUNCIL-SIM] 无待预演票（day=%s，--force 重演）" % day)
        compare()
        return []
    print("[COUNCIL-SIM] day=%s 预演 %d 票" % (day, len(targets)))
    recs = [run_one_sim(t) for t in targets]
    compare()
    return recs


def _k_of(rec):
    return ((rec.get("directive") or {}).get("sizing") or {}).get("k")


def compare():
    """预演 vs 真实全量对齐打分（票量小，每次覆盖重算幂等）。"""
    sim = {
        "%s|%s|%s" % (r.get("sid"), r.get("code"), r.get("d")): r
        for r in _jl(SIM_PROPOSALS)
        if r.get("code")
    }
    real = {
        "%s|%s|%s" % (r.get("sid"), r.get("code"), r.get("d")): r
        for r in _jl(PROPOSALS)
        if r.get("code")
    }
    rows = []
    for key in sorted(set(sim) & set(real), reverse=True):
        s, r = sim[key], real[key]
        sv, rv = (s.get("vote") or {}), (r.get("vote") or {})
        svm = {k: ((s.get("views") or {}).get(k) or {}).get("stance") for k in ROLES}
        rvm = {k: ((r.get("views") or {}).get(k) or {}).get("stance") for k in ROLES}
        ks, kr = _k_of(s), _k_of(r)
        ks = 1.0 if ks is None else ks
        kr = 1.0 if kr is None else kr
        rows.append(
            {
                "key": key,
                "d": r.get("d"),
                "sid": r.get("sid"),
                "code": r.get("code"),
                "name": r.get("name"),
                "sim_verdict": sv.get("verdict"),
                "real_verdict": rv.get("verdict"),
                "verdict_agree": sv.get("verdict") == rv.get("verdict"),
                "sim_wsum": sv.get("wsum"),
                "real_wsum": rv.get("wsum"),
                "sim_net": (s.get("directive") or {}).get("net_stance"),
                "real_net": (r.get("directive") or {}).get("net_stance"),
                "sim_k": ks,
                "real_k": kr,
                "d_k": round(ks - kr, 2),
                "role_agree": {k: svm[k] == rvm[k] for k in ROLES},
            }
        )
    n = len(rows)
    dw = [
        abs(x["sim_wsum"] - x["real_wsum"])
        for x in rows
        if x["sim_wsum"] is not None and x["real_wsum"] is not None
    ]
    dk = [abs(x["d_k"]) for x in rows]
    role_rate = {
        k: round(100.0 * sum(1 for x in rows if x["role_agree"][k]) / n, 1)
        if n
        else None
        for k in ROLES
    }
    score = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "n_sim": len(sim),
        "n_real": len(real),
        "n_pairs": n,
        "verdict_agree_rate": round(
            100.0 * sum(1 for x in rows if x["verdict_agree"]) / n, 1
        )
        if n
        else None,
        "avg_abs_dwsum": round(sum(dw) / len(dw), 2) if dw else None,
        "avg_abs_dk": round(sum(dk) / len(dk), 2) if dk else None,
        "role_agree_rate": role_rate,
        "rows": rows,
    }
    with atomic_io.replacing(SCORE) as f:
        json.dump(score, f, ensure_ascii=False, indent=1)
    print(
        "[COUNCIL-SIM] 对照：sim=%d real=%d 对齐=%d verdict一致率=%s%%"
        " 平均|Δk|=%s → %s"
        % (
            len(sim),
            len(real),
            n,
            score["verdict_agree_rate"] if n else "-",
            score["avg_abs_dk"] if dk else "-",
            os.path.basename(SCORE),
        )
    )
    return score


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/council_sim.py", exc)
    ap = argparse.ArgumentParser(description="K69 模拟投委会（预演层）")
    ap.add_argument("--code", help="单票预演（可逗号分隔多票）")
    ap.add_argument("--force", action="store_true", help="同日同票重演")
    ap.add_argument("--compare-only", action="store_true", help="只重算对照打分不辩论")
    a = ap.parse_args()
    if a.compare_only:
        compare()
    else:
        run(
            codes=[c.strip() for c in a.code.split(",")] if a.code else None,
            force=a.force,
        )
