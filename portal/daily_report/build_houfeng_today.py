# -*- coding: utf-8 -*-
"""候风 · 今日状态页构建器（D1 日更第一件）。

为什么要这一页：
    候风从 2026-11-12 起，活因子池为空（唯一候选 IDEA_004 被 20 日批级净额否决），
    auto 池 `[FATAL-POOL]` 直接 exit 4 —— **main 池根本没有执行**。
    结果：前端 9 个页面停在 11-10，界面上看不出来这几天发生了什么。

    这是「日更」的实质：**日更不是每天有票，是每天都有一份写着"今天为什么没票"的当日页。**
    有票时它说票；没票时它说原因、说停在哪一步、说什么时候该重新看。

四条纪律（与 build_houfeng.py 一致）：
  1. 数据缺失不是错误 —— 页面显式写「未采集」，绝不沿用旧数字；
  2. 模板与代码里不写死任何会换日变化的数字，全部现读；
  3. 判定只认文件内容里的 asof / 交易日期，**不看 mtime**（mtime 不参与判定）；
  4. 退出码只答「跑完了吗」—— 业务判定另写 stdout 的 `TODAY: ...` 一行，
     不拿非 0 退出码表达业务结论。

本页是内部页：含因子代号与 OOS 数值，不进对外发布集（见 PUBLISH_SET.json 的 excluded）。

用法： "/c/Program Files/Python311/python.exe" build_houfeng_today.py
"""
from __future__ import annotations

import glob
import io
import json
import os
import re
import sys
from datetime import date, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))          # 主仓根
PROJ = os.path.abspath(os.path.join(ROOT, ".."))                 # 项目目录
FP = os.path.join(PROJ, "zerobound-kg", "factor_proto")
DAILY = os.path.join(ROOT, "quant_backtest", "data", "tushare", "daily")
COHORT = os.path.join(HERE, "cohorts")
LOG = r"C:\Temp\houfeng_daily_task.log"
OUT = os.path.join(ROOT, "portal", "houfeng", "HOUFENG_TODAY.html")

TODAY = date.today().strftime("%Y%m%d")


# ------------------------------------------------------------------ 读取
def trading_days() -> list:
    """交易日序列：取 daily 分片文件名里含的日期（文件名即日期，不是 mtime）。"""
    out = []
    for f in glob.glob(os.path.join(DAILY, "d_*.parquet")):
        m = re.search(r"d_(\d{8})\.parquet$", os.path.basename(f))
        if m:
            out.append(m.group(1))
    return sorted(set(out))


def read_json(path: str) -> dict:
    try:
        return json.load(io.open(path, encoding="utf-8"))
    except Exception:                                   # noqa: BLE001
        return {}


def last_selections() -> dict:
    """{asof: [{"name","n_union","n_conc"}, ...]}：只认 selection_YYYYMMDD.json 与 _auto 后缀。

    文件存在不等于有票：select_asof 只要有因子命中就落盘，单因子命中 109 只也会
    写出文件，但 confluence_multi=0 —— 台账不登记、不构成批次。所以这里必须把
    共识数读出来由调用方判断；只看文件名会把候选当成买票。
    """
    got = {}
    for f in glob.glob(os.path.join(FP, "selection_*.json")):
        base = os.path.basename(f)
        m = re.search(r"selection_(\d{8})(_auto)?\.json$", base)
        if not m:
            continue                                    # selection_20261106_wf.json 之类不算日更产物
        j = read_json(f)
        got.setdefault(m.group(1), []).append({
            "name": base,
            "n_union": len(j.get("union") or []),
            "n_conc": len(j.get("confluence_multi") or []),
        })
    return got


def last_log_verdict() -> dict:
    """从任务日志取「本次运行」的业务判定。日志是「为什么」的权威来源。

    先切出最后一次 start 之后的段：跨天的旧判定不能当今天用。
    新日志末尾有 VERDICT 行（cand/conc/watch 三个数字都在里面），优先读它；
    读不到才退回旧格式的关键字扫描 —— 否则旧日志会让页面按旧语义解释新一天。
    """
    out = {"verdict": None, "at": None, "lines": [], "cand": None, "conc": None, "watch_n": None}
    if not os.path.exists(LOG):
        return out
    try:
        with io.open(LOG, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()[-600:]
    except OSError:
        return out

    seg = lines
    for i in range(len(lines) - 1, -1, -1):
        if "houfeng_daily start" in lines[i]:
            seg = lines[i:]
            break

    for ln in reversed(seg):
        s = ln.rstrip()
        if "VERDICT:" in s:
            tail = s.split("VERDICT:", 1)[1].strip()
            out["verdict"] = tail.split()[0] if tail.split() else None
            out["at"] = s[:19]
            for tok in tail.split():
                for pre, key in (("cand=", "cand"), ("conc=", "conc"), ("watch=", "watch_n")):
                    if tok.startswith(pre):
                        try:
                            out[key] = int(tok[len(pre):])
                        except ValueError:
                            pass
            break

    if out["verdict"] is None:                          # 旧格式兜底
        for ln in reversed(seg):
            s = ln.rstrip()
            if "POOL-EMPTY" in s:
                out.update(verdict="POOL-EMPTY", at=s[:19]); break
            if "RETRY-LATER" in s:
                out.update(verdict="RETRY-LATER", at=s[:19]); break
            if "houfeng_daily done" in s:
                out.update(verdict="DONE", at=s[:19]); break
            if "ABORT" in s or "EXCEPTION" in s:
                out.update(verdict="ABORT", at=s[:19]); break
    out["lines"] = [l.rstrip() for l in seg[-40:] if l.strip()]
    return out


def read_done_mark(asof):
    """读当日完成标记 data/.houfeng_done_<asof>（ps1 与页面之间唯一的交接凭证）。

    为什么必须有它：主池产物缺失有两个完全不同的原因 ——
      ① 三池全空：select_asof 池空时不落文件，缺失就是**结论本身**（verdict=NO-PICK/WATCH-ONLY）；
      ② 产物丢失/写失败：异常（标记若称 PICK/CANDIDATES-ONLY，缺失就不是池空）。
    两者在文件系统上长得一模一样（都是「文件不在」），只能靠标记里的 verdict 分开。
    没有标记时不得假设任何一方，一律按「未采集」处理。
    """
    if not asof:
        return None
    p = os.path.join(FP, "data", ".houfeng_done_" + str(asof))
    try:
        raw = io.open(p, encoding="ascii", errors="replace").read().strip()
    except OSError:
        return None
    out = {"verdict": None, "empty": [], "at": None, "raw": raw}
    m = re.search(r"verdict=([^\s]+)", raw)
    if m:
        out["verdict"] = m.group(1)
    m = re.search(r"empty=([^\s]*)", raw)
    if m and m.group(1):
        out["empty"] = m.group(1).split(",")
    m = re.search(r"at=([0-9: \-]+)$", raw)
    if m:
        out["at"] = m.group(1).strip()
    return out if out["verdict"] else None


# ------------------------------------------------------------------ 判定
def build() -> dict:
    d = {"asof": None, "today": TODAY, "lag_completed": None,
         "verdict": None, "verdict_at": None,
         "promoted": None, "robust": [], "vetoed": [], "oos": None, "oos_rule": "",
         "last_pick_date": None, "gap_days": [], "main_ran": None,
         "cohort_n": None, "cohort_day": None, "price_ok": None,
         "promoted_at": None, "ok": True, "miss": [], "watch": None, "cand": None,
         "mark": None, "artifacts": None}

    days = trading_days()
    if days:
        d["asof"] = days[-1]
    else:
        d["miss"].append("交易日历（daily 分片）")

    # ---- 晋升链：为什么没有票 ----
    pp = read_json(os.path.join(FP, "pool_promoted.json"))
    if pp:
        d["promoted"] = len(pp.get("promoted") or [])
        d["robust"] = pp.get("robust_set") or []
        d["vetoed"] = pp.get("vetoed_20d") or []
        d["promoted_at"] = pp.get("generated_at")
        d["oos_rule"] = str((pp.get("rule") or ""))[:160]
        pf = pp.get("per_factor") or {}
        # 取被否决的那个因子的组合层 OOS（开仓口径），它是「否决依据」本身的数字
        for code in d["vetoed"] or d["robust"]:
            v = (pf.get(code) or {}).get("port_oos_open")
            if v is not None:
                d["oos"] = v
                break
    else:
        d["miss"].append("晋升池 pool_promoted.json")

    # ---- 今日状态：executed 到哪一步 ----
    lg = last_log_verdict()
    d["verdict"], d["verdict_at"] = lg["verdict"], lg["at"]
    # 读不到判定行时必须记为「未采集」并点名缺的是什么。
    # 只把标题改成「今日状态未采集」而不说缺哪一项，等于让人自己去猜 —— 空态要指名道姓。
    if lg["verdict"] is None:
        d["miss"].append("当日运行日志（houfeng_daily_task.log 无判定行）")
    # 语义变更后：池空不再中断，main 池必然执行；只有「等数据」的 RETRY-LATER 仍会提前退出。
    if lg["verdict"] == "RETRY-LATER":
        d["main_ran"] = False
    elif lg["verdict"] in ("PICK", "CANDIDATES-ONLY", "WATCH-ONLY", "NO-PICK", "DONE"):
        d["main_ran"] = True

    # ---- 连续无票：最后一次真的产出「批次」是哪天（按多因子共识判，不按文件名）----
    sels = last_selections()
    pick_days = sorted(a for a, fs in sels.items() if any(x["n_conc"] > 0 for x in fs))
    if pick_days:
        d["last_pick_date"] = pick_days[-1]
        if d["asof"]:
            d["gap_days"] = [x for x in days if x > d["last_pick_date"]]

    # ---- 今日候选篮：单因子命中、未成共识 -> 能看，但不构成批次 ----
    cur = sels.get(str(d["asof"] or ""), [])
    if cur:
        d["cand"] = {
            "asof": d["asof"],
            "files": [x["name"] for x in cur],
            "n": sum(x["n_union"] for x in cur),
            "conc": sum(x["n_conc"] for x in cur),
        }

    # ---- 观察池（wf 池）：可买为 0 时，今天仍然能看到什么 ----
    # 事实：ZKG_POOL=wf 的产物 selection_YYYYMMDD_wf.json 是「walk-forward 稳健集」的
    # 命中列表，它**没有**过组合层样本外闸门 —— 所以它是「能看」，不是「能买」。
    # 这一层必须单独存在：可买与可观察是两件事，不能因为前者为 0 就让后者一起消失。
    wfs = sorted(glob.glob(os.path.join(FP, "selection_*_wf.json")))
    if wfs:
        j = read_json(wfs[-1])
        if j:
            u = j.get("union") or []
            d["watch"] = {
                "file": os.path.basename(wfs[-1]),
                "asof": str(j.get("asof") or ""),
                "n": len(u),
                "pool": j.get("pool") or [],
                "conf": len(j.get("confluence") or []),
                "ex": {k: sum(1 for c in u if c.endswith("." + k))
                       for k in ("SH", "SZ", "BJ")},
            }

    # ---- 产物状态：主池产物缺失是「池空」还是「丢了」----
    # 这两件事在文件系统上完全一样（文件不在），只能靠 ps1 写的标记 verdict 分开。
    # 结论影响的是页面敢不敢说「今日无票」：池空可以说；产物丢了只能说「不知道」。
    d["mark"] = read_done_mark(d["asof"])
    mv = (d["mark"] or {}).get("verdict")
    if d["cand"]:
        d["artifacts"] = "present"
    elif mv in ("NO-PICK", "WATCH-ONLY"):
        d["artifacts"] = "empty-by-design"          # 池空：缺失是结论本身
    elif mv in ("PICK", "CANDIDATES-ONLY"):
        d["artifacts"] = "missing"                   # 标记称有候选，产物却不在 -> 丢失
        d["miss"].append("当日选股产物（标记称 %s，但 selection_%s.json 不在）" % (mv, d["asof"]))
    elif d["mark"]:
        d["artifacts"] = "unknown"
    else:
        d["artifacts"] = "unrun"                     # 无标记：当天没跑完（或被中断）

    # ---- 台账现状 ----
    st = read_json(os.path.join(COHORT, "status_latest.json"))
    if st:
        d["cohort_n"] = len(st.get("cohorts") or [])
        d["cohort_day"] = st.get("today")
        d["price_ok"] = st.get("price_ok")
    else:
        d["miss"].append("台账 status_latest.json")

    # ---- 数据日落后几个「已完成交易日」 ----
    if d["asof"]:
        d["lag_completed"] = len([x for x in days if x > d["asof"] and x < TODAY])
    d["ok"] = not d["miss"]
    return d


# ------------------------------------------------------------------ 渲染
def esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def fmt_d(s: str) -> str:
    return f"{s[:4]}-{s[4:6]}-{s[6:]}" if s and len(s) == 8 else "未采集"


def pct(v) -> str:
    return f"{v * 100:+.2f}%" if isinstance(v, (int, float)) else "未采集"


def render(d: dict) -> str:
    # 三级陈述，不是一个开关：可买批次 / 候选篮 / 观察池。
    # 「不能出票」不等于「什么都没有」—— 候选词与观察词本来就该在，只是不等于买票。
    cand = d.get("cand") or {"n": 0, "conc": 0, "files": []}
    has_pick = bool(d["last_pick_date"]) and d["last_pick_date"] == d["asof"]
    if has_pick:
        head, tone, kicker = "今日出票", "ok", "多因子共识已形成批次，见下方台账"
    elif cand["n"] > 0:
        head, tone, kicker = ("今日不出票 · 有候选 %d 只" % cand["n"]), "warn", \
            "单因子命中、未形成多因子共识；候选可以看，不构成买票依据"
    elif d["artifacts"] == "missing":
        # 必须排在 cand 判断之后、其余 verdict 之前：标记称有候选却找不到产物，
        # 此时任何数字（包括 0）都不可信，页面只能承认「不知道」。
        head, tone, kicker = "今日结论不可信 · 产物丢失", "bad", \
            "标记称当日有候选，但选股产物不在；数字不可用，需重跑"
    elif d["verdict"] == "WATCH-ONLY":
        head, tone, kicker = ("今日无票 · 仅观察池 %d 只" % ((d["watch"] or {}).get("n") or 0)), "warn", \
            "可买与候选都为空，只有观察池有命中；观察不等于买"
    elif d["verdict"] == "NO-PICK":
        head, tone, kicker = "今日无票", "warn", \
            "三个池全空：活因子池 0 个，无候选、无观察；这是治理结论，不是故障"
    elif d["verdict"] == "POOL-EMPTY":
        head, tone, kicker = "今日无票", "warn", "活因子池为空，治理拒绝；重跑不会改变结果"
    elif d["verdict"] == "RETRY-LATER":
        head, tone, kicker = "今日未出票", "warn", "闸门拒绝，等下一个触发点"
    elif d["verdict"] == "ABORT":
        head, tone, kicker = "今日未出票", "bad", "管线异常退出，需人工介入"
    else:
        head, tone, kicker = "今日状态未采集", "bad", "没有读到本次运行留下的判定"

    lag = d["lag_completed"]
    lag_txt = ("落后 %d 个交易日" % lag) if isinstance(lag, int) and lag > 0 else "已是最新已收盘交易日"
    lag_tone = "bad" if isinstance(lag, int) and lag > 1 else ("warn" if lag else "ok")

    gap = d["gap_days"] or []
    gap_txt = ("%s 共 %d 个交易日无新批次" % ("、".join(fmt_d(x)[5:] for x in gap[:6]), len(gap))
               if gap else "无空档")

    rows = [
        ("活因子池（promoted）", f'{d["promoted"]} 个' if d["promoted"] is not None else "未采集",
         "晋升闸门全部通过才算；0 个即无法选股"),
        ("WF 稳健集", f'{len(d["robust"])} 个' + (f'（{"、".join(d["robust"])}）' if d["robust"] else ""),
         "walk-forward 阶段稳健"),
        ("被 20 日批准否决", f'{len(d["vetoed"])} 个' + (f'（{"、".join(d["vetoed"])}）' if d["vetoed"] else ""),
         "批级净额 ≤0 即否决"),
        ("组合层 OOS（开仓口径）", pct(d["oos"]), "需 > 0 且可交易化，才进 promoted"),
    ]
    trs = "".join(
        f'<tr><td>{esc(k)}</td><td class="v">{esc(v)}</td><td class="n">{esc(n)}</td></tr>'
        for k, v, n in rows)

    miss = ("".join(f'<li>{esc(m)}</li>' for m in d["miss"])) if d["miss"] else ""
    missblk = (f'<section class="warnbox"><h2>以下数据不可用（未采集或已丢失），本页不对这些项作任何判断</h2>'
               f'<ul>{miss}</ul></section>') if miss else ""

    # 「为什么没有票」这一段只在无票日显示。
    # 有票日还留着这段解释，等于页面上同时写着「今日有票」和一段「今日无票不等于无信号」，
    # 读的人第一眼会以为结论自相矛盾 —— 变异检验（有票分支）抓出来的。
    if not has_pick and cand["n"] > 0:
        readblk = f"""<h2 class="sec">这一页怎么读</h2>
<div class="lede">
  <b>今天不出票，但不是什么都没发生 —— 这是三件事，必须分开读：</b>
  <br><br>
  <b>可买批次 0 只</b>：晋升链的「组合层样本外」这道闸没有候选通过
  （被否决因子组合层 OOS {pct(d["oos"])}），所以没有批次进台账。
  <br>
  <b>候选篮 {cand["n"]} 只</b>：单因子命中，只说明「这个因子今天点了它」，
  <b>没有经过多因子共识</b>。它是候选词，可以看、可以研究，<b>不构成买票依据</b>。
  <br>
  <b>观察池 {(d["watch"] or {}).get("n", 0)} 只</b>：这是<b>另一个池、另一只因子</b>
  （walk-forward 稳健集）在同一天点出来的，跟上面的候选篮不是一个池子的两半，
  <b>不要相加</b>。它只过了稳健闸，没到样本外闸。
  <br><br>
  「候选有效」和「马上买票」是两件事。要变成批次，只有两条路：
  <b>因子通过全部晋升闸门</b>，或<b>多因子在同一标的上形成共识</b>。
  <br>
  （另有一个方向：候选篮与观察池在同一个代码上重叠 —— 该口径<b>尚未验证，不作判据</b>，
  写在这里只为说明它没被漏掉，不是一条可用的路。）
  放宽闸门把候选凑成批次，不是选项。
</div>"""
    elif d["artifacts"] == "missing":
        readblk = f"""<h2 class="sec">这一页怎么读</h2>
<div class="lede">
  <b>这一页现在不能用来判断今天有没有票。</b>完成标记写着当日结论是
  <code>{esc(str(d["verdict"]))}</code>（即「有候选/有票」），但
  <code>selection_{esc(str(d["asof"]))}.json</code> 不在 —— <b>产物丢了</b>。
  <br><br>
  这不是「池空」。池空时 <code>select_asof</code> 本来就不落文件，那是结论；
  而标记说有候选却没有文件，只能是产物被删、写失败或被清理。
  两者在文件系统上长得一样，所以这一页<b>不猜</b>：所有数字按「不可用」处理。
  <br><br>
  处置：重跑一次当日任务（会重建产物并重写这页）。若重跑后仍然缺失，
  问题在选股器写盘环节，不在本页。
</div>"""
    elif not has_pick:
        readblk = f"""<h2 class="sec">这一页怎么读</h2>
<div class="lede">
  「今日无票」不等于「今日无信号」，也不等于故障。它是<b>晋升流水线主动拒绝</b>：
  候选因子过完 walk-forward、20 日批级净额、组合层样本外三道闸之后一个不剩。
  所以重跑 <b>promote_auto --recompute</b> 不会改变结果，放宽闸门不是选项。
  <br><br>
  要改变这一页的结论，只有一条路：<b>有新的因子真的通过全部闸门</b>。
  核查入口是晋升台账的拒绝原因与 walk-forward 明细，不是重跑当天任务。
</div>"""
    else:
        readblk = f"""<h2 class="sec">这一页怎么读</h2>
<div class="lede">
  今日活因子池非空（promoted {d["promoted"]} 个），已选出批次。
  本页只回答「今天有没有票、票从哪来」，<b>不给买卖建议</b>；明细见候风信号台账。
</div>"""

    # 表标题也要随状态变：有票日还写「为什么没有票」，等于标题和结论打架。
    chain_title = ("票从哪来 · 晋升链逐闸" if has_pick
                   else ("为什么不出票 · 晋升链逐闸" if cand["n"] > 0
                         else ("产物缺失 · 定位不到是哪一闸" if d["artifacts"] == "missing"
                               else "为什么没有票 · 晋升链逐闸")))

    # 观察池区块：可买与可观察分开陈述，绝不合并成一个数字
    # 默认空串：没有观察池产物时必须渲染成空，而不是让整页抛 UnboundLocalError
    # （变异检验抓到过：有票日 / 无 wf 文件时 watch 为 None，页面直接崩）。
    watchblk = ""
    w = d.get("watch")
    if w:
        drift = (w["asof"] != d["asof"])
        drift_tag = ('<span class="tag" style="background:#fdf6e6;color:#8a6a1a">观察池数据日与今日不同</span>'
                     if drift else "")
        watchblk = f"""<h2 class="sec">观察池明细 · {w["n"]} 只 {drift_tag}</h2>
<div class="lede">
  <b>可买批次 {cand["conc"]} 只 · 候选篮 {cand["n"]} 只 · 观察池 {w["n"]} 只 —— 三个数字，三件事。</b><br>
  可买为空，是因为晋升链的「组合层样本外」这道闸没有候选通过；
  观察池这一层只过了 walk-forward 稳健闸，<b>没有过样本外闸</b>，
  所以它只能看、不能买，也不构成任何买入依据。
  <br><br>
  数据日 {fmt_d(w["asof"])} · 命中因子 {"、".join(w["pool"]) or "未采集"} ·
  沪 {w["ex"].get("SH", 0)} / 深 {w["ex"].get("SZ", 0)} / 北 {w["ex"].get("BJ", 0)} ·
  池内多因子共识 {w["conf"]} 只 · 产物 <code>{esc(w["file"])}</code>
  <br><br>
  <b>为什么它只能是「清单」而不是「名单」—— 已实测，不是推断：</b>
  布尔谓词只有命中/不命中，没有强弱维度。2026-11-16 用「距 MA20 幅度 + 换手率」池内分位
  双键，在 242 个交易日上做了分位检验：四组方向的毛 spread 全部落在 ±0.5pct 内，
  最好一组扣掉 0.30pct 成本只剩 <b>+0.07pct</b>，与随机排列（0.0009pct）不可区分，
  按预设放弃线判为放弃（<code>deliverables/experiments/EXP_20261116_watch_rank</code>）。
  所以这一页不再写「下一步会排序」—— 这条路已经用数据否掉，不是待办事项。
</div>"""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#f5f7fc">
<title>候风 · 今日状态 · {fmt_d(d["asof"])}</title>
<link rel="stylesheet" href="hf_tokens.css">
<style>
body{{background:var(--bg);color:var(--tx,#1b2233);font:15px/1.6 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif;margin:0}}
.wrap{{max-width:860px;margin:0 auto;padding:18px 20px 60px}}
header{{border-bottom:1px solid rgba(0,0,0,.08);padding-bottom:12px;margin-bottom:18px}}
.kicker{{font-size:11px;letter-spacing:1.4px;color:#6b7488}}
h1{{font-size:21px;margin:4px 0 0;font-weight:600}}
.meta{{font-size:12px;color:#6b7488;margin-top:6px}}
.meta b{{color:#1b2233;font-variant-numeric:tabular-nums}}
.verdict{{border-radius:12px;padding:18px 20px;margin:16px 0 22px;border:1px solid}}
.verdict.ok{{background:#eef7ee;border-color:#bfe0bf}}
.verdict.warn{{background:#fdf6e6;border-color:#edd9a3}}
.verdict.bad{{background:#fdeeee;border-color:#eebfbf}}
.verdict h2{{font-size:26px;margin:0 0 6px;font-weight:600}}
.verdict p{{margin:0;font-size:13.5px;color:#3d4557}}
.nums{{display:flex;gap:10px;flex-wrap:wrap;margin-top:14px}}
.num{{flex:1 1 150px;background:#fff;border:1px solid rgba(0,0,0,.07);border-radius:9px;padding:10px 12px}}
.num .k{{font-size:11.5px;color:#6b7488}}
.num .v{{font-size:17px;font-weight:600;font-variant-numeric:tabular-nums;margin-top:2px}}
.num .r{{font-size:11px;color:#8a92a3;margin-top:2px}}
h2.sec{{font-size:14px;font-weight:600;margin:24px 0 8px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th,td{{text-align:left;padding:9px 10px;border-bottom:1px solid rgba(0,0,0,.06)}}
th{{font-size:11.5px;color:#6b7488;font-weight:500}}
td.v{{font-variant-numeric:tabular-nums;font-weight:500;white-space:nowrap}}
td.n{{color:#6b7488;font-size:12px}}
.lede{{font-size:13.5px;color:#3d4557;background:#fff;border:1px solid rgba(0,0,0,.07);border-radius:10px;padding:14px 16px}}
.lede b{{color:#1b2233}}
.warnbox{{margin:18px 0;background:#fdeeee;border:1px solid #eebfbf;border-radius:10px;padding:12px 16px}}
.warnbox h2{{font-size:13px;margin:0 0 6px;font-weight:600}}
.warnbox ul{{margin:0;padding-left:18px;font-size:12.5px;color:#3d4557}}
footer{{margin-top:34px;padding-top:14px;border-top:1px solid rgba(0,0,0,.08);font-size:11.5px;color:#8a92a3;line-height:1.8}}
.tag{{display:inline-block;font-size:11px;padding:1px 7px;border-radius:20px;background:#eef1f8;color:#5a6478;margin-left:6px}}
</style>
</head>
<body>
<div class="wrap">
<header>
  <div class="kicker">候风 HOUFENG · 每日状态</div>
  <h1>今日状态<span class="tag">内部页</span></h1>
  <div class="meta">数据日 <b>{fmt_d(d["asof"])}</b> · 今日 <b>{fmt_d(d["today"])}</b> ·
    <span class="tag">{esc(lag_txt)}</span> · 生成 <b>{datetime.now().strftime("%Y-%m-%d %H:%M")}</b></div>
</header>

<div class="verdict {tone}">
  <h2>{head}</h2>
  <p>{esc(kicker)}</p>
  <div class="nums">
    <div class="num"><div class="k">可买批次</div>
      <div class="v">{cand["conc"]} 只</div>
      <div class="r">台账只认多因子共识</div></div>
    <div class="num"><div class="k">候选篮</div>
      <div class="v">{cand["n"]} 只</div>
      <div class="r">单因子命中，不成批次</div></div>
    <div class="num"><div class="k">观察池</div>
      <div class="v">{(d["watch"] or {}).get("n", 0)} 只</div>
      <div class="r">仅过稳健闸，未过样本外闸</div></div>
    <div class="num"><div class="k">最后批次</div>
      <div class="v">{fmt_d(d["last_pick_date"]) if d["last_pick_date"] else "无记录"}</div>
      <div class="r">{esc(gap_txt)}</div></div>
  </div>
</div>

{missblk}

<h2 class="sec">{chain_title}</h2>
<table>
  <thead><tr><th>环节</th><th>当前值</th><th>判据</th></tr></thead>
  <tbody>{trs}</tbody>
</table>

{readblk}

{watchblk}

<h2 class="sec">台账现状</h2>
<table>
  <thead><tr><th>项</th><th>值</th><th>说明</th></tr></thead>
  <tbody>
    <tr><td>在跑批次</td><td class="v">{d["cohort_n"] if d["cohort_n"] is not None else "未采集"}</td>
        <td class="n">已登记、未到期的候选篮子</td></tr>
    <tr><td>台账数据日</td><td class="v">{fmt_d(d["cohort_day"]) if d["cohort_day"] else "未采集"}</td>
        <td class="n">台账自身的 asof，与本页数据日可能不同</td></tr>
    <tr><td>价格守卫</td><td class="v">{"通过" if d["price_ok"] else ("未通过" if d["price_ok"] is False else "未采集")}</td>
        <td class="n">收盘后同日两快照差 &gt;1pp 判坏价</td></tr>
    <tr><td>晋升台账生成于</td><td class="v">{esc(d["promoted_at"] or "未采集")}</td>
        <td class="n">pool_promoted.json 的 generated_at</td></tr>
  </tbody>
</table>

<footer>
本页由 <code>portal/daily_report/build_houfeng_today.py</code> 生成，每次运行整页重写，不沿用旧数字。<br>
判定来源：<code>pool_promoted.json</code>（晋升链）+ <code>C:\\Temp\\houfeng_daily_task.log</code> 的 <code>VERDICT</code> 行（cand/conc/watch 三个计数）+ <code>cohorts/status_latest.json</code>（台账） + <code>factor_proto/data/.houfeng_done_&lt;asof&gt;</code>（当日完成标记：区分「池空无产物」与「产物丢失」）。<br>
「可买批次」按 <code>confluence_multi</code> 计数，<b>不按 selection 文件是否存在</b>：单因子命中也会落盘，但共识为 0 就不进台账。<br>
内部页：含因子代号与样本外数值，<b>不纳入对外发布集</b>。<br>
涨红跌绿为中国股市约定；本页不表达任何买卖建议。
</footer>
</div>
</body>
</html>
"""


def main() -> int:
    d = build()
    html = render(d)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    io.open(OUT, "w", encoding="utf-8", newline="\n").write(html)

    # 自检：业务判定必须真的出现在页面上，且数据日的两种写法都要能对上
    assert d["asof"] is None or fmt_d(d["asof"]) in html, "自检失败：数据日未进页面"
    # 自检必须是「结构 + 结论词」判据，不能写死某一种文案：
    # 原先是 assert "今日状态" in html，NO-PICK 日标题变成「今日无票」后整页就生成失败。
    # 变异检验：把 head 置空 -> 本断言必须 FAIL。
    assert "<h1" in html and "</html>" in html, "自检失败：整页结构未渲染"
    assert any(k in html for k in ("今日出票", "今日不出票", "今日无票", "今日未出票",
                                   "今日状态未采集", "今日结论不可信")), \
        "自检失败：页面没有任何结论词"
    if d["promoted"] is not None:
        assert f'{d["promoted"]} 个' in html, "自检失败：promoted 计数未进页面"

    print("WROTE %s (%d bytes)" % (OUT, os.path.getsize(OUT)))
    print("TODAY: verdict=%s asof=%s lag=%s promoted=%s gap=%d miss=%d"
          % (d["verdict"], d["asof"], d["lag_completed"], d["promoted"],
             len(d["gap_days"] or []), len(d["miss"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
