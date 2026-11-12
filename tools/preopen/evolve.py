# -*- coding: utf-8 -*-
"""自进化（evolve）：从事件账里长出建议，并且记得上次的建议结果。

「自进化」最容易做成两件错事
----------------------------
1. **每期重新提同一批建议** —— 没人有义务记着三个月前提过什么，
   但如果机器自己也不记，它就永远在提第 1 条建议。
2. **建议直接等于改参数** —— 参数层已被证伪六次（P63/P64/P66/P68/P69），
   再往那个方向进化，是在同一个 β 上做第七次实验。

所以本模块的三条硬约束：

  Ａ. **建议有状态机**：proposed → accepted / rejected → done → verified / failed。
     新一期生成建议时先读旧建议：还挂着的（accepted 未 done）不重复提；
     已 failed 的同 pattern 不再自动提（要人显式 --force）。
  Ｂ. **方向分流**：涉及「选股参数」的假设默认**拒提**，只允许「时点/闸门」类。
     依据：六次参数实验全否，唯一能过预判门禁的是「何时不交易」。
     这不是偏好，是已被验证过的事实，所以写进代码而不是写在文档里。
  Ｃ. **不自动跑实验**：产出 precheck.yaml 骨架 + TODO，跑不跑、填不填由人决定。
     填不满的 precheck 会被 experiment_precheck.py 判 incomplete，跑不了。

与赵云（进化官）的分工
----------------------
赵云做「探·试·裁·记」里的试与裁（对照实验、签字），本模块只做「探」的整理：
把每日观察聚成带证据的假设，交给他。**不写赵云的 experience/，不替他签字。**

用法：
    python tools/preopen/evolve.py --propose        # 生成/更新建议
    python tools/preopen/evolve.py --list           # 看建议状态
    python tools/preopen/evolve.py --set HF01 accepted --note "同意，下周做"
    python tools/preopen/evolve.py --set HF01 done --note "已改采集调度"
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
PP_DIR = os.path.join(ROOT, "var", "preopen", "proposals")
EXP_DIR = os.path.join(ROOT, "quant_backtest", "experiments")

# 够格立假设的门槛：偶发不算，重复且跨日才算
QUORUM = {"event": 3, "state": 5}
SPAN_DAYS_MIN = 3

# 方向分流。param = 选股参数，默认拒绝 —— 理由见模块头 Ｂ。
# 方向分流。param = 选股参数/口径，默认拒绝发起建议 —— 理由见模块头 Ｂ。
# 注意：param 类不是「不记账」，是「记录 + 质询，但不自动发起调参」。
DIRECTION = {
    "PARAM_DRIFT":     ("param", "选股口径"),
    "FALLBACK_MASK":   ("pipeline", "采集链路"),
    "REPEAT_WARN":     ("pipeline", "告警治理"),
    "VERDICT_FLIP":    ("pipeline", "判据阈值"),
    "DATA_STALE_SKIP": ("gate", "时点闸门"),
    "POOL_AGING":      ("pipeline", "上游供给"),
    "SAMPLE_TOO_SMALL": ("gate", "记账口径"),
    "POOL_SYSTEMATIC": ("gate", "时点/池层"),
    "POOL_INCOMPARABLE": ("report", "表达口径"),
    "BOUNDARY_FUZZY":  ("gate", "名单长度"),
    "NAME_STALE":      ("gate", "时点闸门"),
    "UNIVERSE_SHRINK": ("pipeline", "供给完整性"),
    "NO_DATA":         ("pipeline", "供给完整性"),
    # 盘后复盘观测：评分与实现收益的秩相关。归 param 而非新开一类 ——
    # 「排序方向反了」的诱人结论必然指向改权重，而六次参数实验已否决该方向。
    # 它只该记账 + 质询，绝不能自动发起调参建议。
    "SCORE_RANK_INVERTED": ("param", "选股参数"),
}
PARAM_BLOCKED = "param"          # 若将来有 pattern 归到此类，默认拒提
# 未登记的 pattern 一律按参数类处理。原先默认 "unknown" 会绕过上面那道
# 拒提：新加一个 detector、忘了登记方向，第 3 次出现就会自动立一个调参
# 假设 —— 门禁的精神是「没定性的方向先别动」，不是「没登记就放行」。
UNKNOWN_DIRECTION = (PARAM_BLOCKED, "未分类（按参数类处理）")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


# ── 读事件，聚合候选 ────────────────────────────────────────────────────
def load_events() -> list[dict]:
    out = []
    for f in sorted(glob.glob(os.path.join(EV_DIR, "*.json"))):
        if "_index" in f:
            continue
        try:
            out.append(json.load(io.open(f, encoding="utf-8")))
        except Exception:
            continue
    return out


def aggregate() -> dict[str, dict]:
    """按 pattern 聚合。返回 {pattern: {cue, lesson, days[], conf, kind}}"""
    agg: dict[str, dict] = {}
    for ev in load_events():
        day = ev.get("data_day")
        for c in ev.get("candidates") or []:
            p = c.get("pattern")
            if not p:
                continue
            r = agg.setdefault(p, {
                "pattern": p, "cue": c.get("cue"), "lesson": c.get("lesson"),
                "kind": c.get("kind", "event"), "days": [], "conf": []})
            if day not in r["days"]:
                r["days"].append(day)
            r["conf"].append(c.get("confidence") or 0)
        # 检测器发现的问题也要能进候选（它们是关系级证据，不在事件规则表里）
        for d in ev.get("drift") or []:
            p = d.get("id")
            r = agg.setdefault(p, {
                "pattern": p, "cue": d.get("title"), "lesson": d.get("so_what"),
                "kind": "event", "days": [], "conf": []})
            if day not in r["days"]:
                r["days"].append(day)
            r["conf"].append(0.5)
    for r in agg.values():
        r["days"].sort()
        r["n"] = len(r["days"])
        r["conf_avg"] = round(sum(r["conf"]) / len(r["conf"]), 2) if r["conf"] else 0
    return agg


# ── 建议账 ──────────────────────────────────────────────────────────────
def load_proposals() -> dict[str, dict]:
    os.makedirs(PP_DIR, exist_ok=True)
    out = {}
    for f in sorted(glob.glob(os.path.join(PP_DIR, "*.json"))):
        try:
            d = json.load(io.open(f, encoding="utf-8"))
            out[d["pid"]] = d
        except Exception:
            continue
    return out


def save_proposal(p: dict) -> None:
    os.makedirs(PP_DIR, exist_ok=True)
    io.open(os.path.join(PP_DIR, "%s.json" % p["pid"]), "w",
            encoding="utf-8").write(json.dumps(p, ensure_ascii=False, indent=1))


def _next_pid(existing: dict) -> str:
    n = 1
    while "HF%02d" % n in existing:
        n += 1
    return "HF%02d" % n


def _open_same_pattern(props: dict, pattern: str) -> dict | None:
    """同 pattern 还挂着的建议：不做完不再提新的。"""
    for p in props.values():
        if p.get("pattern") == pattern and p.get("status") in ("proposed", "accepted"):
            return p
    return None


def _was_failed(props: dict, pattern: str) -> dict | None:
    for p in props.values():
        if p.get("pattern") == pattern and p.get("status") == "failed":
            return p
    return None


# ── 生成 precheck 骨架 ──────────────────────────────────────────────────
def write_precheck(pid: str, hyp: dict) -> str:
    """产出一份 precheck.yaml **骨架**，全部关键字段留 TODO。

    故意不填：填了就等于我替老田做了「上限估计」这个判断。
    experiment_precheck.py 会把 TODO 判为 incomplete（rc=1），跑不起来 —— 这是设计。
    """
    d = os.path.join(EXP_DIR, pid)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "precheck.yaml")
    if os.path.exists(p):
        return p
    txt = """meta:
  exp_id: {pid}
  layer: {layer}
  hypothesis: {hyp}
  created: '{day}'
  status: draft
  verdict: null
  proposed_by: houfeng/preopen（自动整理，非结论）
cap:
  method: spread
  upper_bound_pct: TODO
  cost_pct: TODO
  basis: TODO 哪张表 / 哪段窗口 / 多少只票
  inputs:
    quantiles: 10
    holding_days: TODO
    spread_gross_pct: TODO
    cost_note: 双边佣金+印花+冲击，A股默认 0.30pct
  note: 完美分层毛收益法：先算不扣成本的最优分位多空差，再减 cost_pct。净上限 <= 0 就该弃。
abandon:
  threshold_pct: TODO
  rationale: TODO
falsify:
  if_true:
  - TODO 带数值
  - TODO 带数值
  if_false:
  - TODO 带数值
  monotonicity_rho_min: TODO
  t_min: TODO
  is_oos_sign_agree: true
  sample_note: TODO 至少多少笔 / 多少个截面才算有效样本
attempts:
  family: {layer}
  total_trials: 1
  same_sample: true
  correction:
    applied: false
    method: none
    note: 本族第 1 组。若在同一段样本上继续调同层，从 2 起累加。
cost_sensitivity:
  mode: derived
  grid_pct: [0.20, 0.30, 0.50]
  net_upper_pct: {{}}
  reason: ''
  note: 填完 cap.inputs.spread_gross_pct 后自动出三档净上限。
selfcheck:
  reference: TODO 拿哪个已知结果反证
  tolerance_pct: 5
  note: 没有参照物的新实现，大概率带着口径错误产出「看起来有结论」的结果。
""".format(pid=pid, layer=hyp.get("layer", "TODO"), hyp=hyp.get("hypothesis", "TODO"),
           day=datetime.now().strftime("%Y-%m-%d"))
    io.open(p, "w", encoding="utf-8", newline="\n").write(txt)
    return p


def _hypothesis_of(pat: str, agg: dict) -> dict:
    """把一条聚合观察翻译成「可检验的假设」。写不好就直说写不好。"""
    r = agg[pat]
    n, days = r["n"], r["days"]
    span = "%s~%s" % (days[0], days[-1]) if days else "-"
    return {
        "pattern": pat,
        "layer": DIRECTION.get(pat, UNKNOWN_DIRECTION)[1],
        "hypothesis": "TODO 由人补写：在当前观测（%s，近 %d 次出现，%s）的基础上，"
                      "具体要验证的可检验命题是什么" % (r["cue"], n, span),
        "observations": n,
        "span": span,
        "confidence": r["conf_avg"],
    }


# ── 主流程 ──────────────────────────────────────────────────────────────
def propose(force: bool = False) -> tuple[list[dict], dict]:
    """返回 (新增建议, 跳过原因统计)。

    跳过原因必须分开统计 —— 「不够格 / 已有挂起 / 已失败 / 参数方向拒提」
    是四件不同的事。合成一句"无新增"会让人以为没发现问题，
    实际可能只是门檻没到，明天就到了。
    """
    agg = aggregate()
    props = load_proposals()
    made = []
    skip = {"below_quorum": [], "open_exists": [], "failed_before": [], "param_blocked": []}

    for pat, r in sorted(agg.items(), key=lambda kv: -kv[1]["n"]):
        direction, layer = DIRECTION.get(pat, UNKNOWN_DIRECTION)

        # Ｂ. 方向分流：参数类默认拒提
        if direction == PARAM_BLOCKED and not force:
            skip["param_blocked"].append((pat, r["n"]))
            continue

        # 门檻
        need = QUORUM.get(r["kind"], 3)
        if r["n"] < need:
            skip["below_quorum"].append((pat, r["n"], need))
            continue

        # Ａ. 已有同类挂着 → 不重复提
        op = _open_same_pattern(props, pat)
        if op:
            op["last_seen"] = r["days"][-1]
            op["observations"] = r["n"]
            save_proposal(op)
            skip["open_exists"].append((pat, op["pid"], op["status"]))
            continue

        # 已 failed → 不再自动提
        fp = _was_failed(props, pat)
        if fp and not force:
            fp["last_seen"] = r["days"][-1]
            save_proposal(fp)
            skip["failed_before"].append((pat, fp["pid"]))
            continue

        pid = _next_pid(props)
        hyp = _hypothesis_of(pat, agg)
        p = {
            "pid": pid, "pattern": pat, "kind": r["kind"],
            "direction": direction, "layer": layer,
            "title": r["cue"],
            "lesson_candidate": r["lesson"],
            "observations": r["n"], "days": r["days"],
            "span": hyp["span"], "confidence": hyp["confidence"],
            "status": "proposed",
            "proposed_at": _now(),
            "history": [{"at": _now(), "event": "proposed", "note": "首次提出"}],
            "needs": ("人补写假设并填 precheck，再由赵云做对照实验" if direction == "gate"
                      else "派工单给数据/研发侧修复，无需实验"),
        }
        if direction == "gate":
            p["precheck"] = write_precheck(pid, hyp)
        save_proposal(p)
        props[pid] = p
        made.append(p)
    return made, skip


def set_status(pid: str, status: str, note: str = "") -> dict:
    props = load_proposals()
    if pid not in props:
        raise SystemExit("没有建议 %s" % pid)
    p = props[pid]
    p["status"] = status
    p.setdefault("history", []).append({"at": _now(), "event": status, "note": note})
    if status in ("done", "verified", "failed"):
        p["closed"] = True
    save_proposal(p)
    return p


def render_list(props: dict) -> str:
    if not props:
        return "（还没有建议。先用 --propose 生成）"
    order = {"proposed": 0, "accepted": 1, "done": 2, "verified": 3,
             "failed": 4, "rejected": 5}
    lines = []
    for p in sorted(props.values(), key=lambda x: (order.get(x["status"], 9), x["pid"])):
        lines.append("%s [%-9s] %-18s 观测%d次 %s"
                     % (p["pid"], p["status"], p["pattern"], p.get("observations", 0),
                        p.get("title", "")[:34]))
        if p.get("history") and len(p["history"]) > 1:
            last = p["history"][-1]
            lines.append("        └ %s %s" % (last["at"][:16], last.get("note") or last["event"]))
    return "\n".join(lines)


# ── 进化台账（给人看的那一面）────────────────────────────────────────────
#
# 前面全是给机器看的：events/ 是原料、proposals/ 是工单。
# 但「自进化」如果只能靠敲命令行看，它就不会有人看第二眼 ——
# 没人看，就等于没进化。所以必须有一个人能读的出口。
#
# 这份台账最重要的不是列成绩，而是回答一个问题：
# **现在这套东西，到底够不够格下结论？** 不够就明说不够。

MIN_DAYS_FOR_JUDGEMENT = 20   # 少于这么多天的账，任何"规律"都还是噪声


def _batches() -> dict:
    """台账里的批次。这是**另一本账**：cohorts/ 记的是「哪一天选了哪些票、后来怎么样」，
    事件账记的是「哪一天跑流程、有没有出状况」。两者单位不同，不能合并成一句「几天账」。
    """
    d = os.path.join(ROOT, "portal", "daily_report", "cohorts")
    sig, pool_n = set(), {}
    for f in glob.glob(os.path.join(d, "cohort_*.json")):
        try:
            j = json.load(io.open(f, encoding="utf-8"))
        except Exception:
            continue
        a = j.get("asof")
        if a:
            sig.add(a)
            pool_n[j.get("pool")] = pool_n.get(j.get("pool"), 0) + 1
    return {"n_batches": len(sig), "pools": pool_n}


def _events() -> list[dict]:
    out = []
    for f in sorted(glob.glob(os.path.join(EV_DIR, "*.json"))):
        try:
            out.append(json.load(io.open(f, encoding="utf-8")))
        except Exception:
            continue
    return out


def digest(html_out: str | None = None) -> str:
    evs = _events()
    agg = aggregate()
    props = load_proposals()
    B = _batches()

    days = [e.get("data_day") for e in evs if e.get("data_day")]
    days.sort()
    n_days = len(days)
    span = ("%s ~ %s" % (days[0], days[-1])) if days else "无"

    # 判决：不是"好/坏"，是"够不够格说话"
    if n_days == 0:
        verdict, why = "没料", "事件账是空的，无从判断。先跑 run_preopen.py。"
    elif n_days < MIN_DAYS_FOR_JUDGEMENT:
        verdict, why = "在学，还不足以下结论", (
            "只有 %d 天账（门槛 %d 天）。现在看到的任何「规律」，"
            "都可能是这几天的行情特征，不是策略特性。"
            "这个阶段唯一该做的事是把账继续记下去，不是急着改配方。"
            % (n_days, MIN_DAYS_FOR_JUDGEMENT))
    else:
        verdict, why = "可评估", ("已有 %d 天账，跨行情阶段，开始能分辨"
                                 "「策略问题」与「这几天的行情」。" % n_days)

    L = []
    A = L.append
    A("# 后峰盘前 · 进化台账")
    A("")
    A("> 生成 %s｜流程账 %d 天（%s）｜选票账 %d 个信号日"
      % (_now()[:16], n_days, span, B["n_batches"]))
    A("")
    A("## 一、现在够不够格下结论")
    A("")
    A("**%s**" % verdict)
    A("")
    A(why)
    A("")
    A("两本账要分开看，混着说会高估证据：")
    A("")
    A("**流程账（事件账）：%d 天**" % n_days)
    A("")
    A("- 能回答：每天流程有没有出状况、判了什么、该不该发报告")
    A("- 不能回答：这套策略赚不赚钱")
    A("")
    A("**选票账（台账）：%d 个信号日**" % B["n_batches"])
    A("")
    A("- 能回答：选出来的票后来涨跌如何、相对大盘多了多少")
    A("- 不能回答：流程是否健康（它只看结果，看不到过程有没有出岔）")
    A("")
    A("流程账短，说明这套自动化还没跑够久；选票账也短，说明策略好坏"
      "还没到能下结论的时候。**两件事都不靠调参解决，靠时间。**")
    A("")

    # 二、最近一天的观察
    A("## 二、最近一次观察")
    A("")
    if evs:
        last = evs[-1]
        A("数据日 **%s**，判定 **%s**，动作 `%s`" %
          (last.get("data_day"), last.get("verdict"), last.get("action")))
        A("")
        sp = last.get("surprise") or []
        A("**意外（和预期不符的地方）**：")
        if sp:
            for s in sp:
                A("- %s" % s)
        else:
            A("- 无。今天的表现与预期一致 —— 这本身是信息，说明还没遇到边界。")
        A("")
        cands = last.get("candidates") or []
        A("**今天记下的教训候选**：%d 条" % len(cands))
        for c in cands:
            A("- `%s`（置信 %.2f，%s）" % (c.get("pattern"), c.get("confidence") or 0,
                                          "事件型" if c.get("kind") == "event" else "持续态"))
        if not cands:
            A("- 无。今日观测未匹配任何既有教训规则 —— 不生成候选，"
              "无规则支撑的「教训」是编的。")
        A("")
        dr = last.get("drift") or []
        A("**关系级发现**：%d 条" % len(dr))
        for d in dr:
            A("- 「%s」%s" % (d.get("title"), "（严重度 %s）" % d.get("severity", "-")))
        if last.get("drift_error"):
            A("- 检测器异常：%s" % last["drift_error"])
        A("")
    else:
        A("（无事件）")
        A("")

    # 三、累计观测
    A("## 三、累计观测（离「够格提建议」还有多远）")
    A("")
    if agg:
        A("| 模式 | 已见 | 门槛 | 状态 |")
        A("|---|---|---|---|")
        for pat, r in sorted(agg.items(), key=lambda kv: -kv[1]["n"]):
            need = QUORUM.get(r["kind"], 3)
            st = ("够格" if r["n"] >= need
                  else "还差 %d 次" % (need - r["n"]))
            A("| `%s` | %d | %d | %s |" % (pat, r["n"], need, st))
    else:
        A("（还没有任何模式被记到）")
    A("")

    # 四、建议台账
    A("## 四、建议台账")
    A("")
    if props:
        for p in sorted(props.values(), key=lambda x: x["pid"]):
            A("**%s** `%s` — %s" % (p["pid"], p.get("status"), p.get("title")))
            A("")
            A("- 方向：%s / %s｜观测 %d 次（%s）"
              % (p.get("direction"), p.get("layer"), p.get("observations", 0),
                 p.get("span") or "-"))
            A("- 需要：%s" % p.get("needs"))
            if p.get("precheck"):
                A("- 预判单骨架：`%s`" % p["precheck"])
            A("")
    else:
        A("（目前没有建议在挂着。这不等于「系统没问题」—— "
          "也可能是账还不够，见第一节。）")
        A("")

    # 五、边界
    A("## 五、这份台账不做什么")
    A("")
    A("- **不替你判断策略好坏。** 它只记「看到了什么」，"
      "把「这算不算问题」留给你和有对照实验的人。")
    A("- **不自动调参。** 参数方向的观察只记账并质询，不提建议 —— "
      "六个旋钮同一个 β 已被验证六次，第七次只是重复。")
    A("- **不靠 LLM 编教训。** 教训只从规则表取。自动生成的教训多是"
      "正确但无用的鸡汤，读着舒服，改不了任何东西。")
    A("")

    md = "\n".join(L)
    if html_out:
        io.open(html_out, "w", encoding="utf-8", newline="").write(_md2html(md))
    return md


def _md2html(md: str) -> str:
    """极简 md→html。不引外部库、不联网，保证单文件可转发。

    只支持台账真正用到的语法：标题、引用、表格、无序列表、段落、行内 code/b。
    写法是逐行状态机 —— 列表必须一整段包成一个 ul，表格必须整块包成 table。
    顺手拼字符串会在嵌套时错位（第一版就是这么错的：ul 开 1 个闭 3 个）。
    """
    import html as _h
    import re as _re

    def inline(s: str) -> str:
        s = _h.escape(s)
        s = _re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
        s = _re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", s)
        return s

    out: list[str] = []
    buf: list[str] = []          # 当前积累的同类行
    mode = ""                    # "ul" | "table" | ""

    def flush() -> None:
        nonlocal buf, mode
        if not buf:
            mode = ""
            return
        if mode == "ul":
            out.append("<ul>" + "".join("<li>%s</li>" % inline(x) for x in buf) + "</ul>")
        elif mode == "table":
            rows = [r for r in buf if r]
            if rows:
                head = rows[0]
                body = rows[1:]
                h = "<tr>" + "".join("<th>%s</th>" % inline(c) for c in head) + "</tr>"
                bd = "".join(
                    "<tr>" + "".join("<td>%s</td>" % inline(c) for c in r) + "</tr>"
                    for r in body)
                out.append("<table><thead>" + h + "</thead><tbody>" + bd
                           + "</tbody></table>")
        buf, mode = [], ""

    for ln in md.splitlines():
        if ln.startswith("|"):
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            if all(set(c) <= set("-: ") and c for c in cells):
                continue                      # 分隔行 |---|
            if mode != "table":
                flush()
                mode = "table"
            buf.append(cells)
            continue
        if ln.startswith("- "):
            if mode != "ul":
                flush()
                mode = "ul"
            buf.append(ln[2:])
            continue

        flush()
        if ln.startswith("## "):
            out.append("<h2>%s</h2>" % inline(ln[3:]))
        elif ln.startswith("# "):
            out.append("<h1>%s</h1>" % inline(ln[2:]))
        elif ln.startswith("> "):
            out.append("<blockquote>%s</blockquote>" % inline(ln[2:]))
        elif ln.strip() == "":
            continue
        else:
            out.append("<p>%s</p>" % inline(ln))
    flush()

    body = "\n".join(out)
    return ("<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<title>后峰盘前 · 进化台账</title><style>"
            "body{max-width:760px;margin:0 auto;padding:24px 18px 60px;"
            "font:15px/1.75 -apple-system,'PingFang SC','Microsoft YaHei',sans-serif;"
            "color:#1a1a1a;background:#fbfaf8}"
            "h1{font-size:22px;margin:0 0 6px}h2{font-size:17px;margin:28px 0 10px;"
            "padding-left:9px;border-left:3px solid #c0392b}"
            "blockquote{margin:0 0 18px;padding:9px 14px;background:#f3f0eb;"
            "border-left:3px solid #c9c2b6;color:#5a5348;font-size:13.5px}"
            "code{background:#f0ece5;padding:1px 5px;border-radius:3px;"
            "font-size:12.5px;color:#8a4b1e}"
            "table{width:100%;border-collapse:collapse;margin:8px 0 16px;font-size:13.5px}"
            "th,td{border-bottom:1px solid #e6e1d8;padding:6px 8px;text-align:left}"
            "th{background:#f5f1ea;font-weight:600}"
            "ul{padding-left:22px;margin:6px 0 16px}"
            "</style></head><body>" + body + "</body></html>")


def main() -> int:
    ap = argparse.ArgumentParser(description="从事件账长建议（自进化）")
    ap.add_argument("--propose", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--digest", action="store_true", help="产出可读的进化台账")
    ap.add_argument("--md-out", default=None, help="台账 markdown 落盘路径")
    ap.add_argument("--html-out", default=None, help="台账 HTML 落盘路径（可转发）")
    ap.add_argument("--set", nargs=2, metavar=("PID", "STATUS"))
    ap.add_argument("--note", default="")
    ap.add_argument("--force", action="store_true", help="允许重提已 failed 的同类")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.set:
        p = set_status(a.set[0], a.set[1], a.note)
        print("%s → %s" % (p["pid"], p["status"]))
        return 0

    if a.propose:
        made, skip = propose(a.force)
        agg = aggregate()
        if a.json:
            print(json.dumps({"made": made, "skip": skip, "agg": agg},
                             ensure_ascii=False, indent=1))
            return 0
        print("聚合到 %d 个候选模式，本次新增建议 %d 条" % (len(agg), len(made)))
        for p in made:
            print("\n[%s] %-16s 观测 %d 次（%s）"
                  % (p["pid"], p["pattern"], p["observations"], p["span"]))
            print("      观察：%s" % p["title"])
            print("      方向：%s / %s" % (p["direction"], p["layer"]))
            print("      需要：%s" % p["needs"])
            if p.get("precheck"):
                print("      预判单骨架：%s" % p["precheck"])
        # 没新增时，说清楚是哪种"没有"。三种截然不同，不能一句"无新增"糊过去。
        if not made:
            if skip["below_quorum"]:
                print("\n未达门檻（还没到能立假设的程度，继续攒）：")
                for pat, n, need in skip["below_quorum"]:
                    print("    %-18s 已见 %d 次，需 %d 次" % (pat, n, need))
            if skip["open_exists"]:
                print("已有挂起建议，不重复提：")
                for pat, pid, st in skip["open_exists"]:
                    print("    %-18s → %s（%s）" % (pat, pid, st))
            if skip["failed_before"]:
                print("同类曾失败，不自动重提（需 --force）：")
                for pat, pid in skip["failed_before"]:
                    print("    %-18s → %s" % (pat, pid))
            if skip["param_blocked"]:
                print("参数方向，按规矩拒提：")
                for pat, n in skip["param_blocked"]:
                    print("    %-18s 已见 %d 次" % (pat, n))
        return 0

    if a.list:
        print(render_list(load_proposals()))
        return 0

    if a.digest:
        md = digest(html_out=a.html_out)
        if a.md_out:
            os.makedirs(os.path.dirname(os.path.abspath(a.md_out)), exist_ok=True)
            io.open(a.md_out, "w", encoding="utf-8", newline="").write(md)
            print("WROTE %s (%d 字)" % (a.md_out, len(md)))
        if a.html_out:
            print("WROTE %s" % a.html_out)
        if not a.md_out and not a.html_out:
            print(md)
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
