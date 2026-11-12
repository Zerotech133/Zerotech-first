# -*- coding: utf-8 -*-
"""事情化（event_log）：把「一次盘前运行」变成「一件可复盘的事」。

为什么不能只留日志
------------------
`preopen_run_latest.json` 是**状态快照**：它说「今天 warn，2 条告警」。
但这句话回答不了三个问题：
  1. 我事前预期是什么？（没有预期，就没有「差」可言）
  2. 哪一处是我没料到的？（意外才是学习的入口）
  3. 三个月后遇到同样情境，我该记得什么？（这是 cue → lesson）

日志记「发生了什么」，事件记「我当时怎么想的、结果如何、差在哪」。
后者才能累积成知识线，前者只能累积成文件。

与赵云经验库的关系（重要，勿另造一套）
--------------------------------------
赵云（进化官，`~/.hermes/profiles/zhaoyun/`）已有四字段经验体系：
    cue / lesson / evidence / confidence，且 `signed_by` 必须为老田才生效。
本模块**只产 candidate（候选）**，不产生效经验：
    - 落盘位置：本项目 `var/preopen/events/`（后峰自己的账）
    - 升级路径：`evolve.py` 聚合 → 假设单 → 赵云对照实验 → 老田签字 → 写入
      `~/.hermes/profiles/zhaoyun/experience/stock_screen/EXPERIENCE.md`
红线：**本模块不得写赵云的 experience/ 目录**。签字权不在我。

lesson 从哪来
-------------
不调 LLM 生成教训。自动生成的「教训」绝大多数是正确但无用的鸡汤
（"要注意数据质量"），写进经验库只会稀释真正的知识。
本模块的 lesson 全部来自 `PATTERNS` 规则表 —— 每条规则由一个**可观测的
触发条件**加一条**人工写死的教训**构成。没有匹配规则就留空，
空着比编一句强（与赵云「无证据标 needs_backtest，绝不编造」同源）。

用法：
    python tools/preopen/event_log.py --from-run          # 从最近一次运行生成今日事件
    python tools/preopen/event_log.py --show 20261112     # 看某天的事件
    python tools/preopen/event_log.py --amend 20261112 --note "事后发现..." 
    python tools/preopen/event_log.py --list              # 列出所有事件日
"""
from __future__ import annotations

import argparse
import glob
import io
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
EV_DIR = os.path.join(ROOT, "var", "preopen", "events")
DELIV = r"C:\项目\deliverables"
RUN_JSON = os.path.join(DELIV, "preopen_run_latest.json")


# ── 教训规则表 ───────────────────────────────────────────────────────────
# 每条：id / 触发条件（对事件的观测）/ cue（什么情境）/ lesson（教训本体）
# 只写「踩过才知道」的话，不写「要重视质量」这类正确的废话。
PATTERNS = [
    {
        "id": "REPEAT_WARN",
        "kind": "event",
        "cue": "同一条告警连续多日出现，未消解也未处置",
        "lesson": "同一告警连续 3 个交易日不消解，就应从「告警」降级为「题单」："
                  "告警的语义是「今天不一样」，长期存在的东西不是异常而是常态。"
                  "继续按告警刷，会把红条训练成背景色，真正的异常来时就看不见了。",
        "test": lambda e: e.get("repeated_warns"),
    },
    {
        "id": "FALLBACK_MASK",
        "kind": "event",
        "cue": "本地缺当日数据，脚本当场从源端补拉后继续",
        "lesson": "补拉成功不等于链路正常。它证明的是「源端有数据」，恰恰反证了"
                  "「本地采集漏了」。若把补拉当成功，采集缺口会被永久掩盖 —— "
                  "必须把 source_fallback 计数进采集健康度，而不是当容错亮点。",
        "test": lambda e: e.get("fallbacks"),
    },
    {
        "id": "VERDICT_FLIP",
        "kind": "event",
        "cue": "判定结果从前一日的 ok 翻成 warn/fail（或反向）",
        "lesson": "日间翻转要归因到具体一张表、一个字段，不能只记「今天变差了」。"
                  "翻转而找不到单一责任源，通常说明判定阈值本身在被数据密度推着走。",
        "test": lambda e: e.get("verdict_flip"),
    },
    {
        "id": "DATA_STALE_SKIP",
        "kind": "event",
        "cue": "数据日未前进（仍停在前一交易日），但报告照出",
        "lesson": "数据日不动时出的报告，与昨天的报告是同一份东西。"
                  "必须显式标注「本报告数据日 = X，非最新」，否则接收方会当成今日新增信息。",
        "test": lambda e: e.get("data_day_stale"),
    },
    {
        "id": "POOL_AGING",
        "kind": "event",
        # 触发条件是 lag > 1 而不是 > 0：盘前用 T-1 收盘的池子是对的（T 日池子
        # 收盘后才有），lag=1 属设计内。只有 >1 才说明池子真的过期了。
        "cue": "共识池 asof 落后行情超过 1 个交易日（用旧包选新票）",
        "lesson": "共识池是上游给的候选集，asof 落后行情超过一日时，选出来的是"
                  "「按过期池子挑今天的强势股」，与策略假设（池子本身有效）不是一回事。"
                  "这种错配不会报错，只会让超额归因失真；lag=1 则是正常设计，不可一并告警。",
        "test": lambda e: (e.get("pool_lag_days") or 0) > 1,
    },
    {
        "id": "PARAM_DRIFT",
        "kind": "event",
        # 这条规则是「不断调校」这个动作本身的账。
        # 注意它只**记录与质询**，不发起调参 —— 见 evolve.py 的方向分流（direction=param）。
        "cue": "选股打分口径（score_mode / 权重）与上一份产物不同",
        "lesson": "口径变更必须记账，否则历史超额无法归因：同一本台账里混着两套权重时，"
                  "超额变化到底是市场变了还是配方变了，永远说不清。变更前应有 precheck 对照，"
                  "变更后台账须按口径分段，不可跨口径累计。",
        "test": lambda e: e.get("param_drift"),
    },
    {
        "id": "SAMPLE_TOO_SMALL",
        "kind": "state",
        # 这是「持续态」不是「当日事件」：台账样本一直不足，每天都成立。
        # 分 kind 的目的就是让聚合时它只报一次，不刷成每日噪音。
        "cue": "台账批次样本不足却已给出超额数字",
        "lesson": "样本 <10 只、跟踪 <5 日的超额，方向性都不稳，只能记数不能解读。"
                  "报告里给出数字又不加样本标记，等于把噪声当业绩。",
        "test": lambda e: e.get("small_sample_batches"),
    },
]


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _p(path: str) -> str:
    return os.path.join(EV_DIR, "%s.json" % path)


def load(day: str) -> dict | None:
    p = _p(day)
    if not os.path.exists(p):
        return None
    return json.load(io.open(p, encoding="utf-8"))


def list_days() -> list[str]:
    return sorted(os.path.basename(f)[:-5]
                  for f in glob.glob(os.path.join(EV_DIR, "*.json")))


def _prev_event(day: str) -> dict | None:
    days = [d for d in list_days() if d < day]
    return load(days[-1]) if days else None


def _prev_pick(day: str) -> dict | None:
    """前一份选票产物，用来比对「今天和昨天是不是同一份东西」。"""
    fs = sorted(glob.glob(os.path.join(DELIV, "preopen_pick15_v2_adj_check_*.json")))
    fs = [f for f in fs if os.path.basename(f)[-13:-5] < day]
    if not fs:
        return None
    try:
        return json.load(io.open(fs[-1], encoding="utf-8"))
    except Exception:
        return None


def _pick_of(day: str) -> dict | None:
    p = os.path.join(DELIV, "preopen_pick15_v2_adj_check_%s.json" % day)
    if not os.path.exists(p):
        return None
    try:
        return json.load(io.open(p, encoding="utf-8"))
    except Exception:
        return None


def build_from_run(run: dict, note: str = "") -> dict:
    """把一次 run_preopen 的产物，转成一件「事」。

    关键在于补上 run 产物里没有的三样：**预期、意外、教训**。
    """
    day = run.get("data_day") or ""
    au = run.get("audit") or {}
    pick = _pick_of(day) or {}
    ppick = _prev_pick(day)
    prev = _prev_event(day)

    verdict = au.get("verdict") or "unknown"
    warns = au.get("warns") or []
    problems = au.get("problems") or []
    steps = {s.get("step"): s for s in (run.get("steps") or [])}

    # 预期：盘前正常应有的样子。写成可判定的句子，不是愿望。
    expect = {
        "verdict": "ok",
        "warns_max": 0,
        "data_day_eq_latest": True,
        "pool_lag_days_max": 1,
    }

    # 实际
    actual = {
        "verdict": verdict,
        "warns": warns,
        "problems": problems,
        "rc": run.get("rc"),
        "picks_n": len(pick.get("top15_adj") or []),
        "pool_n": pick.get("pool_n"),
        "pkg_asof": pick.get("package_asof"),
    }

    # ── 观测项（供规则表判定）────────────────────────────────────────
    obs: dict = {}

    # ① 告警是否重复：与上一件事件比
    if prev:
        pw = set((prev.get("actual") or {}).get("warns") or [])
        obs["repeated_warns"] = sorted(set(warns) & pw)
        obs["verdict_flip"] = (prev.get("actual", {}).get("verdict") != verdict)
    else:
        obs["repeated_warns"] = []
        obs["verdict_flip"] = False

    # ② 补拉：从选票产物的 toplist_filter 取
    tl = pick.get("toplist_filter") or {}
    obs["fallbacks"] = 1 if tl.get("source") == "source_fallback" else 0

    # ③ 数据日是否前进
    if prev:
        obs["data_day_stale"] = (prev.get("data_day") == day)
    else:
        obs["data_day_stale"] = False

    # ④ 共识池滞后
    pkg_asof = pick.get("package_asof")
    if pkg_asof and day:
        try:
            obs["pool_lag_days"] = _trading_lag(str(pkg_asof), str(day))
        except Exception:
            obs["pool_lag_days"] = None
    else:
        obs["pool_lag_days"] = None

    # ⑤ 选股口径是否被调过：score_mode / score_weights 是「配方」的可观测指纹。
    #    「不断调校」这个动作如果没人记账，每次超额变化都会变成无头案。
    cur_mode = pick.get("score_mode")
    cur_w = pick.get("score_weights")
    obs["param_drift"] = None
    obs["score_mode"] = cur_mode
    obs["score_weights"] = cur_w
    if ppick:
        pm, pw = ppick.get("score_mode"), ppick.get("score_weights")
        mode_chg = (pm and cur_mode and pm != cur_mode)
        w_chg = (pw and cur_w and pw != cur_w)
        if mode_chg or w_chg:
            obs["param_drift"] = {"mode_from": pm, "mode_to": cur_mode,
                                  "w_from": pw, "w_to": cur_w}

    # ⑥ 小样本批次：读台账
    obs["small_sample_batches"] = _small_samples()

    # ── 意外：事前没预料到的具体项 ────────────────────────────────────
    surprise = []
    if obs.get("repeated_warns"):
        surprise.append("同一告警重复：%s" % "；".join(obs["repeated_warns"]))
    if obs.get("fallbacks"):
        surprise.append("本地缺当日数据，靠脚本当场补拉才继续")
    if obs.get("pool_lag_days") and obs["pool_lag_days"] > 1:
        surprise.append("共识池落后行情 %d 个交易日" % obs["pool_lag_days"])
    if obs.get("data_day_stale"):
        surprise.append("数据日未前进，本报告与上一份同源")
    if obs.get("param_drift"):
        _p = obs["param_drift"]
        surprise.append("选股口径被调整：mode %s→%s，权重 %s → %s"
                        % (_p.get("mode_from"), _p.get("mode_to"),
                           _p.get("w_from"), _p.get("w_to")))
    # 名单是否与昨日完全一致（同源的最强证据）
    if ppick:
        a = [x.get("ts_code") for x in (ppick.get("top15_adj") or [])]
        b = [x.get("ts_code") for x in (pick.get("top15_adj") or [])]
        if a and a == b:
            surprise.append("精选名单与上一份逐只相同（%d 只）——数据未更新的直接后果" % len(b))
        elif a and b:
            obs["turnover_names"] = len(set(a) ^ set(b)) // 2

    # ── 匹配教训规则 ────────────────────────────────────────────────
    hits = [p for p in PATTERNS if p["test"](obs)]

    ev = {
        "event_id": "EV-HF-%s" % day,
        "date": datetime.now().strftime("%Y%m%d"),
        "data_day": day,
        "scene": "盘前：数据校对 → 选票 → 简报",
        "expect": expect,
        "actual": actual,
        "obs": obs,
        "verdict": verdict,
        "surprise": surprise,
        "action": ("出报告" if steps.get("report", {}).get("rc") == 0
                   else ("阻断未出报告" if run.get("rc") == 2 else "未知")),
        "candidates": [
            {"pattern": h["id"], "kind": h.get("kind", "event"),
             "cue": h["cue"], "lesson": h["lesson"],
             "confidence": round(_conf(h, day), 2),
             "domain": "stock_screen", "status": "candidate"}
            for h in hits
        ],
        "candidates_all_empty": (len(hits) == 0),
        "note": note,
        "written_at": _now(),
        "amended": [],
        "signed_by": None,
        "written_by": "houfeng/preopen",
    }
    if not hits:
        ev["empty_reason"] = ("今日观测未匹配任何既有教训规则。"
                             "不生成候选 —— 无规则支撑的「教训」是编的。")

    # 关系级发现（跨次/跨池）。单点绿而关系坏，是最容易漏的失效形态，
    # 所以它必须进同一本账，不能另开一处存放。
    try:
        sys.path.insert(0, HERE)
        import detect_drift
        ev["drift"] = detect_drift.detect(day)
    except Exception as e:                                  # noqa: BLE001
        ev["drift"] = []
        ev["drift_error"] = "%s: %s" % (type(e).__name__, e)
    return ev


def _conf(h: dict, exclude_day: str | None = None) -> float:
    """候选经验的置信度。刻意压低 —— 这是**候选**，不是结论。

    分档依据是证据强度，不是「看着有多对」：
      - event（当日发生）：有明确的判前/判后对比，起点 0.45，重复命中累加，上限 0.75
      - state（持续态）：没有「变化」就没有对照，只能算观察，起点 0.30，上限 0.50
    与赵云「无对照组只能写疑似」同源：持续态最多证到「值得查」，证不到「是问题」。
    """
    n = _times_seen(h["id"], exclude_day)
    if h.get("kind", "event") == "state":
        return min(0.50, 0.30 + 0.05 * n)
    return min(0.75, 0.45 + 0.10 * n)


def _times_seen(pattern_id: str, exclude_day: str | None = None) -> int:
    """这个规则在历史上命中过几次。当日重跑时必须排除当日，否则自己给自己加分。"""
    n = 0
    for d in list_days():
        if exclude_day and d == exclude_day:
            continue
        e = load(d) or {}
        for c in e.get("candidates") or []:
            if c.get("pattern") == pattern_id:
                n += 1
    return n


def _small_samples(min_n: int = 10) -> int:
    p = os.path.join(ROOT, "portal", "daily_report", "cohorts", "status_latest.json")
    if not os.path.exists(p):
        return 0
    try:
        d = json.load(io.open(p, encoding="utf-8"))
    except Exception:
        return 0
    return sum(1 for c in (d.get("cohorts") or []) if (c.get("n") or 0) < min_n)


def _trading_lag(a: str, b: str) -> int:
    """两个 YYYYMMDD 之间的交易日数。用本地交易日历；取不到才退回自然日估算。"""
    try:
        sys.path.insert(0, HERE)
        import audit_preopen as audit
        cal = audit.trade_cal()
        if cal and a in cal and b in cal:
            return abs(cal.index(b) - cal.index(a))
    except Exception:
        pass
    try:
        da = datetime.strptime(a, "%Y%m%d")
        db = datetime.strptime(b, "%Y%m%d")
        return round(abs((db - da).days) * 5 / 7)
    except Exception:
        return 0


# ── 写入 ────────────────────────────────────────────────────────────────
def save(ev: dict) -> str:
    os.makedirs(EV_DIR, exist_ok=True)
    p = _p(ev["data_day"])
    old = load(ev["data_day"])
    if old:
        # 同日重跑：保留人工补充的 note / amended，不把人的话冲掉
        ev["note"] = ev.get("note") or old.get("note", "")
        ev["amended"] = old.get("amended") or []
        ev["first_written_at"] = old.get("first_written_at") or old.get("written_at")
        ev["rerun_n"] = (old.get("rerun_n") or 1) + 1
        # drift 是「事后拿实现结果评价这次选票」，不是盘前重算的产物。
        # 盘前重跑把它冲掉，等于重跑一次就删掉一次事后结论 —— 2026-11-12 实测：
        # 16:27 重跑后 SCORE_RANK_INVERTED 整条消失（当时只能 git checkout 手工捞回）。
        # 幂等键同 attach_drift：(id, exec_day)，不重复累加。
        new_keys = {(x.get("id"), x.get("exec_day"))
                    for x in (ev.get("drift") or []) if isinstance(x, dict)}
        keep = [x for x in (old.get("drift") or [])
                if isinstance(x, dict) and (x.get("id"), x.get("exec_day")) not in new_keys]
        if keep:
            ev["drift"] = (ev.get("drift") or []) + keep
            ev["drift_carried_at"] = _now()
            ev["drift_carried_n"] = len(keep)
            print("[INFO] 保留 %d 条事后 drift（盘前重跑不覆盖）：%s"
                  % (len(keep), ",".join(str(x.get("id")) for x in keep)))
        if old.get("drift_appended_at") and not ev.get("drift_appended_at"):
            ev["drift_appended_at"] = old["drift_appended_at"]
        # 上一版留一份（单份覆盖式，不按次数堆积）：万一还有别的字段被冲掉，至少有据可查
        try:
            io.open(p + ".bak", "w", encoding="utf-8").write(
                json.dumps(old, ensure_ascii=False, indent=1))
            ev["prev_backup"] = os.path.basename(p + ".bak")
        except OSError:
            pass
    else:
        ev["first_written_at"] = ev["written_at"]
        ev["rerun_n"] = 1
    io.open(p, "w", encoding="utf-8").write(json.dumps(ev, ensure_ascii=False, indent=1))
    _rebuild_index()
    return p


def _rebuild_index() -> None:
    rows = []
    for d in list_days():
        e = load(d) or {}
        rows.append({
            "event_id": e.get("event_id"), "data_day": d,
            "verdict": e.get("verdict"), "n_surprise": len(e.get("surprise") or []),
            "n_cand": len(e.get("candidates") or []),
            # drift 不在候选规则表里，但它是聚合器的输入；不记这一列，
            # 索引看上去就像「什么都没追加」，正是容易被误读成没跑成的那种输出。
            "n_drift": len(e.get("drift") or []),
            "patterns": [c.get("pattern") for c in (e.get("candidates") or [])]
                        + [d.get("id") for d in (e.get("drift") or [])],
            "signed": bool(e.get("signed_by")),
        })
    p = os.path.join(EV_DIR, "_index.jsonl")
    with io.open(p, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def amend(day: str, note: str) -> dict:
    """事后补记 —— 「自评 vs 事后」能力的入口。

    今天说「正常」，明天发现昨天漏了一张表，这句补记就是唯一能纠正它的东西。
    """
    ev = load(day)
    if not ev:
        raise SystemExit("没有 %s 的事件，先 --from-run" % day)
    ev.setdefault("amended", []).append({"at": _now(), "note": note, "by": "houfeng/preopen"})
    io.open(_p(day), "w", encoding="utf-8").write(
        json.dumps(ev, ensure_ascii=False, indent=1))
    _rebuild_index()
    return ev


def attach_drift(day: str, finding: dict) -> tuple[dict, bool]:
    """把盘后/关系级发现挂到**既有**事件上。返回 (事件, 是否新增)。

    与 build_from_run 的区别：盘前那件事已经记完了，盘后才拿到的实现结果
    是「对那次选票的事后评价」，不是新的一次盘前流程。另开一天事件会把
    流程账的天数虚增 —— 那本账数的是「跑了几次流程」，不是「看了几次结果」。

    幂等键是 (id, exec_day)：同一天同一发现重复跑不累加，否则刷一次复盘
    就多一条「独立观测」，quorum 会被人为凑够。
    """
    ev = load(day)
    if not ev:
        raise SystemExit("没有 %s 的事件，先 --from-run；事后观测不能凭空开一天流程账" % day)
    key = (finding.get("id"), finding.get("exec_day"))
    drift = ev.setdefault("drift", [])
    for d in drift:
        if (d.get("id"), d.get("exec_day")) == key:
            return ev, False
    drift.append(finding)
    ev["drift_appended_at"] = _now()
    io.open(_p(day), "w", encoding="utf-8").write(
        json.dumps(ev, ensure_ascii=False, indent=1))
    _rebuild_index()
    return ev, True


# ── CLI ─────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="盘前事件账（事情化）")
    ap.add_argument("--from-run", action="store_true", help="从 preopen_run_latest.json 生成事件")
    ap.add_argument("--run-json", default=RUN_JSON)
    ap.add_argument("--note", default="", help="人工补一句（为什么今天这样）")
    ap.add_argument("--show", default=None, metavar="YYYYMMDD")
    ap.add_argument("--amend", default=None, metavar="YYYYMMDD")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    if a.list:
        for d in list_days():
            e = load(d) or {}
            print("%s  %-6s 意外%d 候选%d 关系%d %s" % (
                d, e.get("verdict"), len(e.get("surprise") or []),
                len(e.get("candidates") or []), len(e.get("drift") or []),
                "已签" if e.get("signed_by") else ""))
        return 0

    if a.show:
        print(json.dumps(load(a.show), ensure_ascii=False, indent=1))
        return 0

    if a.amend:
        amend(a.amend, a.note)
        print("已补记 %s" % a.amend)
        return 0

    if a.from_run:
        if not os.path.exists(a.run_json):
            raise SystemExit("找不到运行产物：%s（先跑 run_preopen.py）" % a.run_json)
        run = json.load(io.open(a.run_json, encoding="utf-8"))
        ev = build_from_run(run, a.note)
        p = save(ev)
        print("事件 %s → %s" % (ev["event_id"], p))
        print("  判定 %s｜意外 %d 条｜候选经验 %d 条"
              % (ev["verdict"], len(ev["surprise"]), len(ev["candidates"])))
        for s in ev["surprise"]:
            print("    · %s" % s)
        for c in ev["candidates"]:
            print("    [%s] conf=%.2f  %s" % (c["pattern"], c["confidence"], c["cue"]))
        if not ev["candidates"]:
            print("    （未匹配教训规则：空着，不编）")
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
