# -*- coding: utf-8 -*-
"""K55 逐票多Agent投委会 · 立案/盯盘/平仓复盘（影子模式）

铁律（INTEL_RD_PLAN K55，调研三原则落地）：
  · LLM never touches the trade —— 只观点留痕，不拦截/不改仓/不动钱
  · 留痕 append-only —— reports/council_proposals.jsonl 只增不改
  · 影子模式 —— 跑批主链永不为观点层让路（Ollama 不可达 → 留 skip 记录退出）

六视角（本地 Ollama qwen3:8b-8k，think=false temp0，复用 nl_query FC 范式）：
  多头/空头/激进/保守（无工具·观点角色）
  技术面（datamart_read 读 K 线 / pool_rows / signal_enrich 先例）
  情绪面（events_for_stock / event_summary 个股新闻事件）
  → 主持人汇总投资备忘录（verdict/conviction/thesis/risks/if_wrong）
  → 分歧度 dissent = 100×(六方 stance 极差)/4（代码算，机器可读）

K62/K64 影子管理逻辑（代码规则，非 LLM）：
  · 加权投票（K64a）：保守×1.2/激进×0.8，wsum≥+3 支持/≤-3 反对
  · 管理指令（K62-A）：投票结论×信心×分歧 → 五级指令
  · 风格分桶（K62-B）：激进/稳健桶分纪律盯盘
  · 仓位系数（K64b/K66）：净多空打底→×0.8~1.2；net≠0 即触发角色
    个性修正——激进同向顺净方向加码（净多放大/净空砍狠）、保守同向
    一律缩仓一档
  · 观点参数（K64c）：TP目标/SL风险/ML动量/RL风控价位线→盯盘个性化纪律线
  · 待确认队列（K64d/K67）：预警票+系数建议票（k≥1.1 加/k≤0.9 减，
    pct=|k-1|×100）入 council_pending.json，/council 人工确认后执行

触发：
  python stock_council.py                      # 账本当日新建仓逐票辩论（跑批后接线）
  python stock_council.py --code 600519.SH     # 单票手动辩论（测试/补跑）
  python stock_council.py --force              # 同日同票重辩（默认跳过已留痕）
  python stock_council.py --no-push            # 只留痕不推飞书
  python stock_council.py --watch-only         # 只跑盯盘会（不立案）
  python stock_council.py --review-only        # 只跑平仓/复盘会（K55-C3）
"""
import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from nl_query import _llm, _coerce, _schema, TOOL_MSG_MAX, MAX_ROUNDS
from agent_tools import call as T

REPORTS = os.path.join(HERE, "reports")
PROPOSALS = os.path.join(REPORTS, "council_proposals.jsonl")
OLLAMA_TAGS = "http://127.0.0.1:11434/api/tags"
MKT_TABLE = {"CN": "ts_daily", "US": "us_daily", "HK": "hk_daily",
             "CRYPTO": "crypto_daily"}

SID_DESC = {
    "qpzm": "青萍之末·全市场动量捕捉，风格偏灵活",
    "9dim": "九维评分·A股基本面+技术面综合评分，风格偏均衡",
    "sig4": "四策略信号·多策略信号驱动，风格偏量化",
    "chanlun": "缠论结构·缠论买卖点结构驱动，风格偏技术",
}

# ── 六视角注册表 ─────────────────────────────────────────────
COUNCIL = {
    "bull": {
        "name": "多头", "tools": [],
        "param": {"key": "TP", "label": "目标价"},
        "system": "你是模拟盘投委会的多头研究员。必须站在看多立场为该建仓辩护："
                  "找催化剂、上行逻辑、有利证据。实事求是引用背景信息，不编数字。"
                  "观点陈述≤150字，然后单独一行输出 TP:数字（12个月目标价，元，"
                  "基于背景信息合理估计），最后单独一行输出 STANCE:2（强烈看多）"
                  "或 STANCE:1（看多）。",
    },
    "bear": {
        "name": "空头", "tools": [],
        "param": {"key": "SL", "label": "风险位"},
        "system": "你是模拟盘投委会的空头研究员（魔鬼代言人）。必须站在看空立场挑刺："
                  "风险点、反面证据、这笔建仓可能错在哪。实事求是，不编数字。"
                  "观点陈述≤150字，然后单独一行输出 SL:数字（跌破该价位空头论点"
                  "大幅兑现，元），最后单独一行输出 STANCE:-2（强烈看空）"
                  "或 STANCE:-1（看空）。",
    },
    "aggr": {
        "name": "激进", "tools": [],
        "param": {"key": "ML", "label": "动量失效线"},
        "system": "你是模拟盘投委会的激进交易员。从短线动量视角评估：近期强势度、"
                  "事件催化节奏、快进快出的机会与风险。可多可空，实事求是。"
                  "观点陈述≤150字，然后单独一行输出 ML:数字（动量失效价，跌破即"
                  "离场，元），最后单独一行输出 STANCE:n（-2强烈看空~2强烈看多）。",
    },
    "cons": {
        "name": "保守", "tools": [],
        "param": {"key": "RL", "label": "风控线"},
        "system": "你是模拟盘投委会的保守风控官。从回撤风险/估值安全边际/仓位合理性"
                  "视角评估这笔建仓，默认怀疑态度，可多可空。实事求是，不编数字。"
                  "观点陈述≤150字，然后单独一行输出 RL:数字（建议止损价，元，"
                  "控制单笔最大回撤），最后单独一行输出 STANCE:n"
                  "（-2强烈看空~2强烈看多）。",
    },
    "tech": {
        "name": "技术面", "tools": ["datamart_read", "pool_rows",
                                    "signal_enrich", "signal_precedents"],
        "system": "你是投委会的技术面研究员，只用提供的工具查数据，不得编造任何数字。\n"
                  "规则：\n"
                  "· 查 K 线用 datamart_read(table=用户给的表名, codes=[该股代码], "
                  "limit=30) 看近 30 根的量价形态\n"
                  "· 该股若是缠论信号触发，用 signal_enrich 或 pool_rows 查信号先例战绩\n"
                  "· 拿到足够事实后直接给中文观点（≤150字，引用具体数字）"
                  "，最后单独一行输出 STANCE:n（-2~2）",
    },
    "senti": {
        "name": "情绪面", "tools": ["events_for_stock", "event_summary",
                                    "related_events"],
        "system": "你是投委会的情绪面研究员，只用提供的工具查事件，不得编造。\n"
                  "规则：\n"
                  "· 用 events_for_stock(code=该股代码, days=30) 查近期公告/新闻事件\n"
                  "· 事件多可再调 event_summary 看多空统计\n"
                  "· 拿到足够事实后直接给中文观点（≤150字，引用具体事件标题与偏性）"
                  "，最后单独一行输出 STANCE:n（-2~2）",
    },
}

HOST_SYSTEM = """你是模拟盘投委会主持人。下面是六位研究员对刚建仓标的的观点纪要。
请汇总成投资备忘录，只输出 JSON（不要 markdown 代码块、不要任何其它文字）：
{"verdict":"支持|反对|中性","conviction":<0-100 整数,结论信心>,
 "dissent_hat":<0-100 整数,你判断的委员会分歧度>,
 "thesis":"≤80字核心多空逻辑",
 "risks":["风险1","风险2","风险3"],
 "if_wrong":"反方最强论点≤60字"}
评分口径：六方立场一致→dissent_hat 低；多空尖锐对立→高。"""

_JSON_RE = re.compile(r"\{[\s\S]*\}")
_STANCE_RE = re.compile(r"STANCE\s*[:：]\s*([+-]?\d)")
_PARAM_RE = re.compile(r"\b(TP|SL|ML|RL)\s*[:：]\s*([+-]?\d+(?:\.\d+)?)")


# ── Ollama 探活 ─────────────────────────────────────────────
def _alive():
    try:
        import requests
        return requests.get(OLLAMA_TAGS, timeout=4).ok
    except Exception:
        return False


# ── 单视角运行（FC 循环，仿 multi_agent._specialist_run）──────
def _view_run(rk, ctx):
    """返回 {"role","name","stance","stance_ok","answer","calls"}。
    任意故障兜底为 stance=0 的失败视角，全链不断。"""
    t0 = time.time()
    spec = COUNCIL[rk]
    try:
        tools = None
        if spec["tools"]:
            full = {t["function"]["name"]: t for t in _schema()}
            tools = [full[n] for n in spec["tools"] if n in full]
        messages = [{"role": "system", "content": spec["system"]},
                    {"role": "user", "content": ctx}]
        calls, answer = [], None
        for _ in range(MAX_ROUNDS):
            m = _llm(messages, tools)
            tcs = m.get("tool_calls") or []
            msg = {"role": "assistant", "content": m.get("content") or ""}
            if tcs:
                msg["tool_calls"] = tcs
            messages.append(msg)
            if not tcs:
                answer = m.get("content") or ""
                break
            for tc in tcs:
                f = tc.get("function") or {}
                args = _coerce(f.get("name"), f.get("arguments") or {}, ctx)
                if args is None:
                    res = {"ok": False, "err": "unknown tool"}
                else:
                    res = T(f.get("name"), **args)
                c = json.dumps(res, ensure_ascii=False)
                if len(c) > TOOL_MSG_MAX:
                    c = c[:TOOL_MSG_MAX] + "…[截断]"
                calls.append({"tool": f.get("name"), "args": args})
                messages.append({"role": "tool", "content": c,
                                 "tool_name": f.get("name")})
        if not answer:      # 轮尽仍要工具 → 无工具强汇总
            messages.append({"role": "user",
                             "content": "直接给最终中文观点，不要再调用工具，"
                                        "最后单独一行 STANCE:n。"})
            answer = (_llm(messages).get("content") or "")
        m2 = _STANCE_RE.search(answer)
        stance = int(m2.group(1)) if m2 else 0
        stance = max(-2, min(2, stance))
        param = None                          # K64c 观点参数（价位线）
        pk = (spec.get("param") or {}).get("key")
        if pk:
            for pm in _PARAM_RE.finditer(answer):
                if pm.group(1) == pk:         # 只认本角色的键，防串线
                    try:
                        param = float(pm.group(2))
                    except Exception:
                        param = None
        return {"role": rk, "name": spec["name"], "stance": stance,
                "stance_ok": bool(m2), "param_key": pk, "param": param,
                "answer": answer.strip()[:800],
                "calls": calls, "elapsed_s": round(time.time() - t0, 1)}
    except Exception as e:
        return {"role": rk, "name": spec["name"], "stance": 0,
                "stance_ok": False, "param_key": None, "param": None,
                "answer": "（%s 视角调用失败：%s）" % (spec["name"], str(e)[:120]),
                "calls": [], "elapsed_s": round(time.time() - t0, 1)}


# ── 主持人 → 备忘录 ──────────────────────────────────────────
def _host(ctx, views):
    parts = ["标的背景：\n%s\n" % ctx, "六方观点纪要："]
    for v in views:
        parts.append("【%s·stance=%d】%s" % (v["name"], v["stance"], v["answer"]))
    parts.append("请输出备忘录 JSON。")
    try:
        m = _llm([{"role": "system", "content": HOST_SYSTEM},
                  {"role": "user", "content": "\n".join(parts)}])
        match = _JSON_RE.search(m.get("content") or "")
        memo = json.loads(match.group(0)) if match else {}
    except Exception:
        memo = {}
    if not memo.get("verdict") in ("支持", "反对", "中性"):
        memo = {"verdict": "中性", "degraded": True}
    memo.setdefault("conviction", 50)
    memo.setdefault("dissent_hat", None)
    memo.setdefault("thesis", "")
    memo.setdefault("risks", [])
    memo.setdefault("if_wrong", "")
    try:
        memo["conviction"] = max(0, min(100, int(memo["conviction"])))
    except Exception:
        memo["conviction"] = 50
    return memo


# ── 单票全流程 ───────────────────────────────────────────────
def _ctx_of(t):
    table = MKT_TABLE.get(t.get("market") or "", "ts_daily")
    pct = ""
    if t.get("pos_pct") is not None:
        pct = "，仓位占账户净值约 %.1f%%" % t["pos_pct"]
    return ("标的：%s %s（%s）\n账户：%s 刚建仓%s\n建仓价：%s\n"
            "K线表名：%s（技术面用 datamart_read(table='%s', codes=['%s'], "
            "limit=30) 查近30根）\n请给出你的立场。"
            % (t.get("code"), t.get("name") or "", t.get("market") or "?",
               SID_DESC.get(t.get("sid"), t.get("sid") or "?"),
               pct, t.get("entry_px") or "?", table, table, t.get("code")))


# ── K62 影子管理逻辑：观点→指令映射 + 风格分桶（代码规则，非 LLM）──
def _directive(memo, vm, dissent, vote=None):
    """K62-A 看多看空→管理指令（影子建议链）：
    verdict×conviction×分歧度 代码映射，可解释可统计；铁律不变——
    只产建议留痕+提醒，永不直接动仓，影子转正后即插即用。
    net_stance = 四观点角色（多/空/激进/保守）STANCE 之和，-8~8。
    K64a：vd 优先投票结论（六方加权票），主持人 verdict 留 rec.memo 对照。"""
    vd = (vote or {}).get("verdict") \
        or (memo or {}).get("verdict") or "中性"
    conv = int((memo or {}).get("conviction") or 0)
    dis = (memo or {}).get("dissent_hat")
    if dis is None:
        dis = dissent
    net = sum(vm.get(k) or 0 for k in ("bull", "bear", "aggr", "cons"))
    if vd == "反对":
        act = "平仓预警" if conv >= 60 else "减仓预警"
    elif vd == "中性" and (dis >= 60 or net <= -3):
        act = "收紧盯盘"
    else:
        act = "持有观察"
    sizing = _sizing_hint(net, vm)               # K65 仓位系数建议
    rule = "%s·信%s·歧%s%s" % (vd, conv, dis,
                               ("·票%+.1f" % vote["wsum"]) if vote else "")
    return {"act": act, "net_stance": net, "sizing": sizing,
            "rule": rule}


def _style_of(vm):
    """K62-B 激进保守→风格分桶：动量视角分（激进+技术面 stance，-4~4）
    ≥2 → 激进桶（紧纪律盯盘），否则稳健桶（宽纪律）。
    只影响盯盘强度与提醒频次，不动仓位。"""
    sc = (vm.get("aggr") or 0) + (vm.get("tech") or 0)
    return ("激进", sc) if sc >= 2 else ("稳健", sc)


# ── K64a 投票制结论：六方加权投票（代码规则，可解释可统计）─────
VOTE_W = {"bull": 1.0, "bear": 1.0, "aggr": 0.8, "cons": 1.2,
          "tech": 1.0, "senti": 1.0}


def _vote_verdict(vm):
    """K64a 六视角加权投票：保守×1.2（风控话语权放大）/激进×0.8（动量
    噪音降权）/其余×1.0；理论域 [-12, 12]（多空对冲后常态 [-8, 8]）。
    ≥+3 支持 / ≤-3 反对 / 其间中性。主持人 LLM 结论留 rec.memo 对照，
    host↔vote 一致率进影子统计（转正素材）。"""
    wsum = round(sum((vm.get(k) or 0) * w for k, w in VOTE_W.items()), 2)
    verdict = "支持" if wsum >= 3 else ("反对" if wsum <= -3 else "中性")
    return {"verdict": verdict, "wsum": wsum}


# ── K64b/K66 仓位系数建议：净多空打底 + 激进/保守角色个性修正 ──
def _sizing_hint(net, vm):
    """K66 净多空打底：≥+4→1.2 / ≥+2→1.1 / ≤-4→0.8 / ≤-2→0.9 / 其余 1.0。
    角色个性修正（net≠0 有方向即生效，用户 2026-10-04 定口径「混合版」）：
      · 激进同向 → 顺净方向加码：净多→加重仓 +0.1（放大）/
        净空→砍更狠 -0.1（砍狠）
      · 保守同向 → 一律缩仓一档 -0.1（缩小）：净多轻一点 / 净空也缩
    （任何角色同向看空都压仓位；保守=点头就打九折）
    clamp [0.8, 1.2]。只入 directive.sizing 留痕建议，永不直接改仓。"""
    if net >= 4:
        k, why = 1.2, "净多空≥+4 强共识"
    elif net >= 2:
        k, why = 1.1, "净多空≥+2 偏多"
    elif net <= -4:
        k, why = 0.8, "净多空≤-4 强分歧"
    elif net <= -2:
        k, why = 0.9, "净多空≤-2 偏空"
    else:
        k, why = 1.0, "中性区"
    vm = vm or {}
    if net != 0:                            # K66：有方向即生效（去 |net|≥2 门控）
        aggr, cons = (vm.get("aggr") or 0), (vm.get("cons") or 0)
        if (net > 0 and aggr > 0) or (net < 0 and aggr < 0):
            k = round(k + (0.1 if net > 0 else -0.1), 2)
            why += "·激进同向放大" if net > 0 else "·激进同向砍狠"
        if (net > 0 and cons > 0) or (net < 0 and cons < 0):
            k = round(k - 0.1, 2)
            why += "·保守同向缩小"
    k = max(0.8, min(1.2, k))
    return {"k": k, "rule": why}


def run_one(t, no_push=False, force=False):
    """六视角（3 并发 × 2 波，multi_agent 判例）→ 主持人 → 留痕。"""
    t0 = time.time()
    ctx = _ctx_of(t)
    mem = _recall(t)                      # K55-C3 记忆检索注入（纯检索不微调）
    if mem:
        ctx += ("\n\n历史复盘经验（同类标的复盘沉淀，供参考不强制）：\n%s"
                % "\n".join("- " + m["text"] for m in mem))
    keys = list(COUNCIL)
    with ThreadPoolExecutor(max_workers=3) as ex:
        views = list(ex.map(lambda k: _view_run(k, ctx), keys))
    memo = _host(ctx, views)
    stances = [v["stance"] for v in views]
    dissent = round(100 * (max(stances) - min(stances)) / 4.0)
    vm = {v["role"]: v["stance"] for v in views}
    vote = _vote_verdict(vm)                     # K64a 六方加权投票
    directive = _directive(memo, vm, dissent, vote)   # K62-A（vd 优先投票）
    style, style_score = _style_of(vm)           # K62-B 风格分桶
    params = {v["param_key"]: v["param"]         # K64c 观点价位线
              for v in views if v.get("param_key") and v.get("param")}
    rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
           "d": t.get("entry_date") or time.strftime("%Y-%m-%d"),
           "code": t.get("code"), "name": t.get("name"),
           "sid": t.get("sid"),
           "sid_label": SID_DESC.get(t.get("sid"),
                                     t.get("sid") or "?"),  # K70 来源自包含
           "market": t.get("market"),
           "entry_px": t.get("entry_px"), "qty": t.get("qty"),
           "dissent": dissent,
           "views": {v["role"]: {"stance": v["stance"],
                                 "stance_ok": v["stance_ok"],
                                 "param": v["param"],
                                 "answer": v["answer"],
                                 "calls": v["calls"]} for v in views},
           "memo": memo, "mem_used": mem, "shadow": True,
           "vote": vote, "directive": directive, "style": style,
           "style_score": style_score, "params": params,
           "elapsed_s": round(time.time() - t0, 1)}
    os.makedirs(REPORTS, exist_ok=True)
    if not _append_proposal(rec, force=force):
        print("[COUNCIL] %s %s dup-skip（并发会已完成留痕）"
              % (t.get("code"), t.get("name") or ""))
        return None              # K94a 并发竞态下重复会静默丢弃
    print("[COUNCIL] %s %s dissent=%d 票%s(w%+.1f) 主%s → %s（%s桶·k%.1f）"
          "(%.0fs)"
          % (t.get("code"), t.get("name") or "", dissent,
             vote["verdict"], vote["wsum"], memo.get("verdict"),
             directive["act"], style,
             (directive.get("sizing") or {}).get("k", 1.0),
             rec["elapsed_s"]))
    return rec


# ── 立案留痕原子写入（K94a）────────────────────────────────
def _append_proposal(rec, force=False):
    """K94a 写入时刻幂等：启动时 _done_today 守卫防不了并发竞态
    （多个进程同时通过检查后各自开 LLM 会数分钟，完成时互不可见，
    判例：10-03 演练连跑跑批 → 600663 同票同日 3 条）。故在文件锁内
    「复查当日同票 + 追加」原子化，重复返回 False；force 重辩除外。"""
    os.makedirs(REPORTS, exist_ok=True)
    import msvcrt
    with open(PROPOSALS, "a+", encoding="utf-8") as f:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)   # 锁首字节互斥
        try:
            if not force:
                dup = False
                for line in f:
                    try:
                        r = json.loads(line)
                    except Exception:
                        continue
                    if r.get("d") == rec.get("d") and \
                            r.get("code") == rec.get("code"):
                        dup = True
                        break
                if dup:
                    return False
            f.seek(0, 2)
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            return True
        finally:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)


# ── 当日新建仓识别（trades act=建仓 + open 上下文）────────────
def _today_new(led, day):
    """返回 [{sid, code, name, market, entry_px, qty, entry_date, pos_pct}]。"""
    out, seen = [], set()
    navs = {}
    for sid, ac in (led.get("accounts") or {}).items():
        curve = ac.get("nav_curve") or []
        navs[sid] = curve[-1].get("nav") if curve else ac.get("capital", 0)
        opens = {p.get("code"): p for p in (ac.get("open") or [])}
        for tr in ac.get("trades") or []:
            if tr.get("d") != day or tr.get("act") != "建仓":
                continue
            key = (sid, tr.get("code"))
            if key in seen:
                continue
            seen.add(key)
            p = opens.get(tr.get("code")) or {}
            pos_pct = None
            held = (p.get("qty") or 0) * (p.get("last_px") or p.get("entry_px") or 0)
            if navs.get(sid):
                pos_pct = round(held * 100.0 / navs[sid], 2)
            out.append({"sid": sid, "code": tr.get("code"),
                        "name": tr.get("name"), "market": p.get("market"),
                        "entry_px": p.get("entry_px"), "qty": p.get("qty"),
                        "entry_date": day, "pos_pct": pos_pct})
    return out[:10]     # 单日上限 10 票（超出的次日用 --code 补）


def _done_today(day):
    """当日已留痕的 (code) 集合（幂等防重辩）。"""
    done = set()
    try:
        with open(PROPOSALS, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("d") == day and r.get("code"):
                    done.add(r["code"])
    except FileNotFoundError:
        pass
    return done


# ── 盯盘会（K55-C2）：全持仓新事件触发解读，只提醒不动仓 ────
WATCH = os.path.join(REPORTS, "council_watch.jsonl")
WATCH_SYSTEM = """你是模拟盘投委会的盯盘研究员。持仓标的出现新事件，判断对持仓的影响。
只输出 JSON（不要 markdown 代码块、不要任何其它文字）：
{"level":"需注意|无影响","bias":"利好|利空|中性","view":"≤100字影响解读"}
口径：明确利好/利空且可能影响短期走势或触发风控纪律→需注意；
例行公告/传闻性弱/影响有限→无影响。"""


def _efp(e):
    """事件指纹（ts 日期 + 标题前 50，稳定可重放）。"""
    return "%s|%s" % ((e.get("ts") or "")[:10], (e.get("text") or "")[:50])


def _load_seen():
    """从留痕表重建已见指纹 {sid|code: set(fp)}。"""
    seen = {}
    try:
        with open(WATCH, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                k = "%s|%s" % (r.get("sid"), r.get("code"))
                seen.setdefault(k, set()).update(r.get("fps") or [])
    except FileNotFoundError:
        pass
    return seen


def _style_map():
    """code → 最近一次立案的 {style, act, params}（proposals 尾扫，
    K62 分桶锚 + K64c 观点价位线；后写覆盖先写=取最新一夜，
    与 _council_stats 口径一致）。"""
    m = {}
    try:
        with open(PROPOSALS, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("code") and r.get("style"):
                    m[r["code"]] = {"style": r["style"],
                                    "act": (r.get("directive") or {})
                                    .get("act") or "",
                                    "params": r.get("params") or {}}
    except FileNotFoundError:
        pass
    return m


def _watch_targets(led):
    """四账户全部持仓（含浮盈上下文 + K62 立案风格/指令锚）。"""
    sm = _style_map()
    out = []
    for sid, ac in (led.get("accounts") or {}).items():
        for p in ac.get("open") or []:
            pnl = None
            if p.get("entry_px") and p.get("last_px"):
                pnl = round((p["last_px"] / p["entry_px"] - 1) * 100, 1)
            g = sm.get(p.get("code")) or {}
            out.append({"sid": sid, "code": p.get("code"), "name": p.get("name"),
                        "market": p.get("market"),
                        "entry_px": p.get("entry_px"),
                        "last_px": p.get("last_px"), "pnl_pct": pnl,
                        "style": g.get("style") or "稳健",
                        "directive": g.get("act") or "",
                        "params": g.get("params") or {}})
    return out


def _watch_one(t, fps_old):
    """单票：查近14天事件→增量→LLM 解读（无工具单次调用）。
    首扫（fps_old is None，键不存在）建基线：强事件（bias 明确且
    conf≥0.7）≤3 条做体检，弱事件只记指纹；已扫无事件（空 set）不再
    重扫——否则无事件持仓每晚空转重写 baseline（C3 修复）。
    返回 rec|None；rec["type"] = watch|baseline。"""
    code = t["code"]
    style = t.get("style") or "稳健"          # K62-B 分桶（无立案记录默认稳健）
    # K64c 个性化纪律线：立案观点价位线（SL风险位/RL风控线/ML动量失效线）
    # 换算为入场百分比，取最紧一条（最接近 0）；合理域 [-15%,-2%] 外忽略；
    # 无可用观点线 → 桶纪律兜底（激进 -4% · 稳健 -8%）
    params = t.get("params") or {}
    disc, line_src = None, None
    for k, lab in (("SL", "空头风险位"), ("RL", "保守风控线"),
                   ("ML", "激进动量失效线")):
        px = params.get(k)
        if not px or not t.get("entry_px"):
            continue
        try:
            pct = (float(px) / float(t["entry_px"]) - 1) * 100
        except (TypeError, ValueError, ZeroDivisionError):
            continue
        if -15 <= pct <= -2 and (disc is None or pct > disc):
            disc, line_src = pct, "观点线%s:%s（%s）" % (k, px, lab)
    if disc is None:
        disc = -4.0 if style == "激进" else -8.0  # 桶纪律线：激进紧 · 稳健宽
        line_src = "%s桶纪律" % style
    evs = None
    try:
        r = T("events_for_stock", code=code, days=14)
        if r.get("ok"):
            # @tool 包装：无 ok 键的原生返回被包成 {"ok":True,"data":{...}}
            d = r.get("data") if isinstance(r.get("data"), dict) else r
            evs = d.get("events") or []
    except Exception:
        evs = None
    if evs is None:                       # 查询失败：不写基线，下次重试
        return None
    fps_all = [_efp(e) for e in evs]
    fresh = [e for e, fp in zip(evs, fps_all) if fp not in fps_old]
    base = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "d": time.strftime("%Y-%m-%d"), "sid": t["sid"],
            "code": code, "name": t["name"], "pnl_pct": t["pnl_pct"],
            "style": style, "fps": fps_all, "shadow": True}
    if fps_old is None:                   # 首扫：建基线
        strong = [e for e in evs if e.get("bias") in ("bull", "bear")
                  and (e.get("conf") or 0) >= 0.7][:3]
        if not strong:
            base["type"] = "baseline"
            return base                  # 无强事件：只记指纹不 LLM
        fresh = strong
    elif not fresh:
        return None                      # 非首扫且无增量：安静跳过
    if len(fresh) > 5:
        fresh = fresh[:5]
    ev_lines = ["- [%s %.2f] %s %s" % (e.get("bias") or "?",
                                       e.get("conf") or 0,
                                       (e.get("ts") or "")[:10],
                                       (e.get("text") or "")[:60])
                for e in fresh]
    ctx = ("持仓：%s %s（%s）\n成本 %s → 现价 %s（%s）\n"
           "风控纪律：%s桶·%s标准·%s（浮亏触及 %.1f%% 机械需注意；%s标准="
           "影响明确即需注意，宽标准=影响有限则无影响）\n新事件：\n%s\n"
           "请判断影响。"
           % (code, t.get("name") or "", SID_DESC.get(t["sid"], t["sid"]),
              t.get("entry_px") or "?", t.get("last_px") or "?",
              ("浮盈%+.1f%%" % t["pnl_pct"]) if t.get("pnl_pct") is not None
              else "浮动未知",
              style, "紧" if style == "激进" else "宽", line_src, disc,
              "紧" if style == "激进" else "宽",
              "\n".join(ev_lines)))
    judge = {}
    try:
        m = _llm([{"role": "system", "content": WATCH_SYSTEM},
                  {"role": "user", "content": ctx}])
        match = _JSON_RE.search(m.get("content") or "")
        judge = json.loads(match.group(0)) if match else {}
    except Exception:
        judge = {}
    if judge.get("level") not in ("需注意", "无影响"):
        judge = {"level": "无影响", "bias": judge.get("bias") or "中性",
                 "view": judge.get("view") or "（解读降级：LLM 输出未解析）",
                 "degraded": True}
    # K62-B 机械纪律线：浮亏触及纪律线即升级需注意（代码规则，不依赖 LLM）
    if judge.get("level") != "需注意" and t.get("pnl_pct") is not None \
            and t["pnl_pct"] <= disc:
        judge = {"level": "需注意", "bias": judge.get("bias") or "利空",
                 "view": "机械纪律：浮盈%+.1f%% 触%s %.1f%%"
                         % (t["pnl_pct"], line_src, disc), "mech": True}
    base.update({"type": "watch",
                 "disc_line": round(disc, 2), "disc_src": line_src,
                 "events": [{"ts": (e.get("ts") or "")[:10],
                             "text": (e.get("text") or "")[:80],
                             "bias": e.get("bias"),
                             "conf": e.get("conf")} for e in fresh],
                 "judge": judge})
    return base


def run_watch(led, day):
    """全持仓盯盘。返回 (全部 recs, 需注意级 recs)。"""
    targets = _watch_targets(led)
    seen = _load_seen()
    recs, alerts = [], []
    for t in targets:
        if not t.get("code"):
            continue
        try:
            rec = _watch_one(t, seen.get("%s|%s" % (t["sid"], t["code"])))
        except Exception:
            continue
        if rec:
            recs.append(rec)
            if rec.get("type") == "watch" and \
                    (rec.get("judge") or {}).get("level") == "需注意":
                alerts.append(rec)
    if recs:
        with open(WATCH, "a", encoding="utf-8") as f:
            for r in recs:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    n_llm = sum(1 for r in recs if r.get("type") == "watch")
    print("[WATCH] holdings=%d scanned=%d llm=%d alert=%d"
          % (len(targets), len(recs), n_llm, len(alerts)))
    return recs, alerts


# ── 平仓会+复盘会（K55-C3）：观点回看 → 归因反事实 → 记忆条目 ──
REVIEWS = os.path.join(REPORTS, "council_reviews.jsonl")

CLOSE_SYS = """你是模拟盘投委会的平仓会书记。一笔持仓已平仓，请回看立案时六方观点与事后结果。
只输出 JSON（不要 markdown 代码块、不要任何其它文字）：
{"bear_right":"对|错|部分","right_views":["被验证的角色"],"wrong_views":["被证伪的角色"],
 "retrospect":"≤100字观点回看"}
口径：空头对=平仓亏损、或空头风险点事后兑现；角色用英文名 bull/bear/aggr/cons/tech/senti，各≤2个。"""

POST_SYS = """你是模拟盘投委会的复盘会主持人。对这笔已平仓交易做归因复盘。
只输出 JSON（不要 markdown 代码块、不要任何其它文字）：
{"attribution":"实力|运气|混合","skill_vs_luck":"≤60字归因说明",
 "counterfactual":"≤60字反事实推演（须引用建仓→平仓的具体涨跌数字）：如果当初听空头的不建仓会怎样",
 "lessons":[{"text":"≤50字可复用经验","tags":["标签"]}]}
标签只能从：高位拥挤/低位反转/事件驱动/趋势动量/震荡市/高分歧/低分歧/止损纪律/止盈离场/事件利空 中选（≤3个）。"""


def _load_jsonl(fp):
    out = []
    try:
        with open(fp, encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except FileNotFoundError:
        pass
    return out


def _days(a, b):
    try:
        return max(0, int((time.mktime(time.strptime(b, "%Y-%m-%d"))
                           - time.mktime(time.strptime(a, "%Y-%m-%d"))) / 86400))
    except Exception:
        return None


def _review_targets(led):
    """有立案备忘录的已平仓记录（观点回看的前提）+ 最近盯盘需注意。"""
    props = {}
    for p in _load_jsonl(PROPOSALS):
        props.setdefault((p.get("sid"), p.get("code")), []).append(p)
    done = {"%s|%s|%s" % (r.get("sid"), r.get("code"), r.get("d"))
            for r in _load_jsonl(REVIEWS)}
    alerts = {}
    for w in _load_jsonl(WATCH):
        if w.get("type") == "watch" and \
                (w.get("judge") or {}).get("level") == "需注意":
            alerts.setdefault((w.get("sid"), w.get("code")), []).append(w)
    out, n_skip = [], 0
    for sid, ac in (led.get("accounts") or {}).items():
        for c in ac.get("closed") or []:
            if not c.get("exit_date"):
                continue
            if "%s|%s|%s" % (sid, c.get("code"), c["exit_date"]) in done:
                continue
            cand = [p for p in props.get((sid, c.get("code")), [])
                    if p.get("d") <= c["exit_date"]]
            if not cand:
                n_skip += 1          # K94b 无立案备忘录：无从回看（对账可见）
                continue
            out.append({"sid": sid, "closed": c, "memo": cand[-1],
                        "alerts": alerts.get((sid, c.get("code")), [])[-2:]})
    return out[:10], n_skip          # 单日上限 10 票（对齐立案会）


def _review_one(t):
    """平仓会（观点回看）+ 复盘会（归因/反事实/经验条目）→ 留痕。"""
    t0 = time.time()
    sid, c, memo = t["sid"], t["closed"], t["memo"]
    hold = _days(c.get("entry_date"), c.get("exit_date"))
    views = memo.get("views") or {}
    bear = ((views.get("bear") or {}).get("answer") or "")[:200]
    bull = ((views.get("bull") or {}).get("answer") or "")[:200]
    alert_lines = ["- [%s] %s｜%s" % (w.get("d"),
                                      ((w.get("events") or [{}])[0]
                                       .get("text") or "")[:40],
                                      (w.get("judge") or {}).get("view", "")[:40])
                   for w in t["alerts"]]
    m = memo.get("memo") or {}
    ctx = ("标的：%s %s（%s · %s）\n"
           "建仓 %s @%s → 平仓 %s @%s（%+.2f%%，持有 %s 天，原因：%s）\n"
           "立案备忘录：结论%s 信心%s 分歧度%s；逻辑：%s\n反方最强：%s\n"
           "空头当时说：%s\n多头当时说：%s\n盯盘需注意记录：\n%s\n"
           % (c.get("code"), c.get("name") or "", sid, SID_DESC.get(sid, sid),
              c.get("entry_date"), c.get("entry_px"), c.get("exit_date"),
              c.get("exit_px"), c.get("exit_pnl_pct") or 0,
              hold if hold is not None else "?", c.get("reason") or "?",
              m.get("verdict"), m.get("conviction"), memo.get("dissent"),
              m.get("thesis") or "", m.get("if_wrong") or "",
              bear or "（无）", bull or "（无）",
              "\n".join(alert_lines) if alert_lines else "（无）"))
    close_v, post_v = {}, {}
    try:                                   # 平仓会：观点回看
        r = _llm([{"role": "system", "content": CLOSE_SYS},
                  {"role": "user", "content": ctx + "\n请输出平仓会回看 JSON。"}])
        match = _JSON_RE.search(r.get("content") or "")
        close_v = json.loads(match.group(0)) if match else {}
    except Exception:
        close_v = {}
    if close_v.get("bear_right") not in ("对", "错", "部分"):
        close_v = {"bear_right": "部分",
                   "retrospect": "（回看降级：LLM 输出未解析）", "degraded": True}
    try:                                   # 复盘会：归因+反事实+经验条目
        r = _llm([{"role": "system", "content": POST_SYS},
                  {"role": "user", "content": ctx + "\n请输出复盘会归因 JSON。"}])
        match = _JSON_RE.search(r.get("content") or "")
        post_v = json.loads(match.group(0)) if match else {}
    except Exception:
        post_v = {}
    lessons = []
    for l in (post_v.get("lessons") or [])[:3]:
        if isinstance(l, dict) and l.get("text"):
            lessons.append({"text": str(l["text"])[:60],
                            "tags": [str(x)[:12] for x in (l.get("tags")
                                                           or [])[:3]]})
    if post_v.get("attribution") not in ("实力", "运气", "混合"):
        post_v["attribution"] = "混合"
    if not lessons:
        post_v["degraded"] = True
    rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
           "d": c.get("exit_date"), "sid": sid,
           "code": c.get("code"), "name": c.get("name"),
           "market": c.get("market"), "entry_date": c.get("entry_date"),
           "exit_px": c.get("exit_px"), "ret_pct": c.get("exit_pnl_pct"),
           "hold_days": hold, "reason": c.get("reason"),
           "dissent": memo.get("dissent"), "memo_verdict": m.get("verdict"),
           "bear_right": close_v.get("bear_right"),
           "right_views": [str(x) for x in (close_v.get("right_views")
                                            or [])[:2]],
           "wrong_views": [str(x) for x in (close_v.get("wrong_views")
                                            or [])[:2]],
           "retrospect": str(close_v.get("retrospect") or "")[:120],
           "attribution": post_v.get("attribution"),
           "skill_vs_luck": str(post_v.get("skill_vs_luck") or "")[:80],
           "counterfactual": str(post_v.get("counterfactual") or "")[:80],
           "lessons": lessons, "shadow": True,
           "elapsed_s": round(time.time() - t0, 1)}
    with open(REVIEWS, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print("[REVIEW] %s %s ret=%+.1f%% bear=%s attr=%s lessons=%d (%.0fs)"
          % (rec["code"], rec["name"] or "", rec["ret_pct"] or 0,
             rec["bear_right"], rec["attribution"], len(lessons),
             rec["elapsed_s"]))
    return rec


def run_reviews(led):
    """平仓会+复盘会批量（新平仓 × 有备忘录 × 幂等去重）。"""
    targets, n_skip = _review_targets(led)
    recs = []
    for t in targets:
        try:
            recs.append(_review_one(t))
        except Exception as e:
            print("[REVIEW] %s failed: %s"
                  % ((t.get("closed") or {}).get("code"), str(e)[:100]))
    print("[REVIEW] closed_targets=%d done=%d no_memo_skip=%d"
          % (len(targets), len(recs), n_skip))   # K94b 跳过数对账可见
    return recs


# ── 记忆闭环（K55-C3）：复盘经验检索注入（纯检索不微调）────────
def _recall(t, top_n=3):
    """按 sid/market/空头验证度 检索历史经验条目（情形标签供 C4 月度淘汰统计）。"""
    pool = []
    for r in _load_jsonl(REVIEWS):
        if not r.get("lessons"):
            continue
        base = (2 if r.get("sid") == t.get("sid") else 0) \
            + (2 if r.get("market") == t.get("market") else 0)
        if base <= 0:
            continue                   # 主匹配维度（sid/market）必须命中
        score = base
        if r.get("bear_right") == "对":
            score += 1                   # 空头被验证的复盘：经验含金量高
        for l in r["lessons"]:
            pool.append((score, l.get("text") or "", r))
    pool.sort(key=lambda x: -x[0])
    out, seen = [], set()
    for sc, txt, r in pool:
        if not txt or txt in seen:
            continue
        seen.add(txt)
        out.append({"code": r.get("code"), "d": r.get("d"),
                    "text": txt, "score": sc})
        if len(out) >= top_n:
            break
    return out


# ── K64d 半自动执行链：预警票 pending 队列（人工确认→执行）────
PENDING = os.path.join(REPORTS, "council_pending.json")


def _queue_pending(recs):
    """待确认票入 council_pending.json，dict 覆盖式：同键（sid|code）后写
    覆盖=取最新一夜指令。票类两级（K64d 预警 + K67 系数建议）：
      · 平仓预警 → pct=100（全平）
      · 减仓预警 → pct=50（减半）
      · 加仓建议 → act 持有/收紧 且 k≥1.1（激进同向放大），pct=|k-1|×100
      · 减仓建议 → act 持有/收紧 且 k≤0.9（保守同向/偏空压仓），同上
    铁律不变——写入只是排队，执行必须经 /council 页人工确认
    （POST /api/council/confirm → close_manual / adjust_position）。"""
    n_warn = n_add = n_trim = 0
    pend = None
    for r in recs:
        dr = r.get("directive") or {}
        act = dr.get("act")
        k = (dr.get("sizing") or {}).get("k") or 1.0
        q_act = pct = None
        if act in ("平仓预警", "减仓预警"):
            q_act = act
            pct = 100 if act == "平仓预警" else 50
            n_warn += 1
        elif act in ("持有观察", "收紧盯盘"):
            if k >= 1.1:                     # K67 激进同向放大 → 加仓建议
                q_act, pct = "加仓建议", min(20, round((k - 1) * 100))
                n_add += 1
            elif k <= 0.9:                   # K67 保守同向/偏空 → 减仓建议
                q_act, pct = "减仓建议", min(20, round((1 - k) * 100))
                n_trim += 1
        if not q_act:
            continue
        if pend is None:                     # 懒加载：无票不动文件
            try:
                with open(PENDING, encoding="utf-8") as f:
                    pend = json.load(f)
                if not isinstance(pend, dict):
                    pend = {}
            except Exception:
                pend = {}
        pend["%s|%s" % (r.get("sid"), r.get("code"))] = {
            "sid": r.get("sid"), "code": r.get("code"),
            "name": r.get("name"), "act": q_act, "pct": pct, "k": k,
            "d": r.get("d"),
            "sizing": dr.get("sizing"), "vote": r.get("vote"),
            "queued_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "shadow": True}
    if pend is None:
        return {"queued": 0, "warn": 0, "add": 0, "trim": 0}
    with open(PENDING, "w", encoding="utf-8") as f:
        json.dump(pend, f, ensure_ascii=False, indent=1)
    print("[PENDING] queued=%d total=%d（预警 %d · 加 %d · 减 %d，"
          "待 /council 人工确认）"
          % (n_warn + n_add + n_trim, len(pend), n_warn, n_add, n_trim))
    return {"queued": n_warn + n_add + n_trim, "warn": n_warn,
            "add": n_add, "trim": n_trim}


# ── 飞书卡（每日一卡合并，schema 2.0，md 正文）───────────────
def _push_day_card(recs, alerts, day, reviews=None):
    """合一卡：立案备忘录段 + 盯盘需注意段 + 复盘段（无影响只留痕不进卡）。
    K62：卡顶管理预警计数；立案行带指令+风格桶；盯盘行带桶标注。"""
    lines = []
    n_warn = sum(1 for r in recs
                 if (r.get("directive") or {}).get("act")
                 in ("平仓预警", "减仓预警"))
    if n_warn:
        warns = ["%s %s→%s" % (r.get("code"), r.get("name") or "",
                               r["directive"]["act"])
                 for r in recs
                 if (r.get("directive") or {}).get("act")
                 in ("平仓预警", "减仓预警")]
        lines.append("**⚠ 管理预警 %d 条（影子建议·请人工决策）**：%s"
                     % (n_warn, "；".join(warns)[:300]))
        lines.append("待确认执行：门户 /council 页（人工确认→平仓/减仓，"
                     "未确认不动仓）")
        lines.append("")
    # K67 仓位建议票：系数落进执行链（k≥1.1 加 / k≤0.9 减，非预警票）
    szs = []
    for r in recs:
        dr = r.get("directive") or {}
        act, k = dr.get("act"), (dr.get("sizing") or {}).get("k") or 1.0
        if act not in ("持有观察", "收紧盯盘"):
            continue                     # 预警票已另行提醒，不重复
        if k >= 1.1:
            szs.append("%s %s→**+%d%%**（激进同向×%.1f）"
                       % (r.get("code"), r.get("name") or "",
                          min(20, round((k - 1) * 100)), k))
        elif k <= 0.9:
            szs.append("%s %s→**-%d%%**（保守同向×%.1f）"
                       % (r.get("code"), r.get("name") or "",
                          min(20, round((1 - k) * 100)), k))
    if szs:
        lines.append("**📐 仓位建议票 %d 条（K67 系数执行链）**：%s"
                     % (len(szs), "；".join(szs)[:300]))
        lines.append("确认后 adjust_position 真实加减仓（pct=|系数-1|×100，"
                     "/council 人工确认）")
        lines.append("")
    if recs:
        lines.append("**📋 立案会 · 新建仓 %d 票**" % len(recs))
        lines.append("")
        for i, r in enumerate(recs, 1):
            m, v = r.get("memo") or {}, r.get("views") or {}
            dr = r.get("directive") or {}
            vt, sz = r.get("vote") or {}, dr.get("sizing") or {}
            lines.append("**%d. %s %s（%s · %s）**" % (
                i, r.get("code"), r.get("name") or "", r.get("market") or "",
                r.get("sid") or ""))
            lines.append("结论：**票%s**（主%s·信 %s · 歧 %d）→ **%s**（%s桶·"
                         "净多空 %s · 仓位×%s）"
                         % (vt.get("verdict") or m.get("verdict") or "—",
                            m.get("verdict"), m.get("conviction"),
                            r.get("dissent", 0),
                            dr.get("act") or "—",
                            r.get("style") or "稳健",
                            ("%+d" % dr["net_stance"])
                            if dr.get("net_stance") is not None else "?",
                            ("×%.1f" % sz["k"]) if sz.get("k") else "1.0"))
            pm = r.get("params") or {}
            pline = " / ".join("%s %g" % (k, pm[k])
                               for k in ("TP", "SL", "ML", "RL")
                               if isinstance(pm.get(k), (int, float)))
            if pline:
                lines.append("观点线：%s（TP目标/SL风险/ML动量/RL风控）"
                             % pline)
            if m.get("thesis"):
                lines.append("逻辑：%s" % m["thesis"])
            for rk, nm in (("bull", "多方"), ("bear", "空方"),
                           ("tech", "技术"), ("senti", "情绪")):
                a = ((v.get(rk) or {}).get("answer") or "").splitlines()
                if a and a[0]:
                    lines.append("%s：%s" % (nm, a[0][:60]))
            if m.get("risks"):
                lines.append("风险：%s" % " / ".join(str(x)[:30]
                                                    for x in m["risks"][:3]))
            if m.get("if_wrong"):
                lines.append("反方最强：%s" % str(m["if_wrong"])[:60])
            lines.append("")
    if alerts:
        lines.append("**👁 盯盘会 · 需注意 %d 票**" % len(alerts))
        lines.append("")
        for r in alerts:
            j = r.get("judge") or {}
            ev0 = (r.get("events") or [{}])[0]
            lines.append("**%s %s（%s · %s · 浮盈 %s%%）** → %s｜%s"
                         % (r.get("code"), r.get("name") or "",
                            r.get("sid") or "",
                            (r.get("style") or "稳健") + "桶"
                            + ("·机械线" if (j.get("mech")) else ""),
                            ("%+.1f" % r["pnl_pct"])
                            if r.get("pnl_pct") is not None else "?",
                            j.get("bias") or "中性",
                            (j.get("view") or "")[:70]))
            if ev0.get("text"):
                lines.append("事件：[%s] %s" % (ev0.get("bias") or "?",
                                               str(ev0.get("text"))[:50]))
            lines.append("")
    if reviews:
        lines.append("**🧾 复盘会 · 平仓 %d 票**" % len(reviews))
        lines.append("")
        for r in reviews:
            lines.append("**%s %s（%s · %+.1f%% · %s）** 空头%s · 归因%s"
                         % (r.get("code"), r.get("name") or "",
                            r.get("sid") or "", r.get("ret_pct") or 0,
                            r.get("reason") or "", r.get("bear_right"),
                            r.get("attribution")))
            if r.get("retrospect"):
                lines.append("回看：%s" % r["retrospect"][:70])
            if r.get("counterfactual"):
                lines.append("反事实：%s" % r["counterfactual"][:70])
            for l in (r.get("lessons") or [])[:2]:
                tags = ("（%s）" % "/".join(l.get("tags") or [])) \
                    if l.get("tags") else ""
                lines.append("经验：%s%s" % (l.get("text") or "", tags))
            lines.append("")
    lines.append("（影子模式：仅观点留痕，不拦截不动仓；全文见 reports/"
                 "council_proposals.jsonl / council_watch.jsonl / "
                 "council_reviews.jsonl）")
    card = {      # 战报卡同款经典结构（content=卡片对象本身）
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text",
                             "content": "🏛 投委会日报（影子）· %s"
                                        % day},
                   "template": "grey"},
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md",
                                    "content": "\n".join(lines)}},
            {"tag": "note", "elements": [
                {"tag": "plain_text",
                 "content": "影子模式：仅观点留痕，不拦截不动仓 · "
                            "全文 reports/council_proposals.jsonl"}]}]}
    try:
        from portfolio import push_feishu
        ok = push_feishu(json.dumps(card, ensure_ascii=False), card=True)
    except Exception as e:
        print("[COUNCIL] push failed: %s" % str(e)[:120])
        ok = False
    print("[COUNCIL] card %s" % ("sent" if ok else "SKIPPED/FAILED"))
    return ok


# ── 主入口 ───────────────────────────────────────────────────
def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="K55-C1 逐票投委会（影子）")
    ap.add_argument("--code", help="单票手动辩论（从账本 open 捞上下文）")
    ap.add_argument("--force", action="store_true", help="同日同票重辩")
    ap.add_argument("--no-push", action="store_true", help="只留痕不推卡")
    ap.add_argument("--watch-only", action="store_true",
                    help="只跑盯盘会（不立案）")
    ap.add_argument("--review-only", action="store_true",
                    help="只跑平仓/复盘会（不立案不盯盘）")
    a = ap.parse_args()

    if not _alive():
        print("[COUNCIL] OLLAMA_DOWN skip（影子层降级，跑批不受影响）")
        return

    from portfolio import load_ledger
    led, _cfg = load_ledger()
    day = (led.get("ts") or time.strftime("%Y-%m-%d"))[:10]

    if a.code:
        targets = []
        for sid, ac in (led.get("accounts") or {}).items():
            for p in ac.get("open") or []:
                if p.get("code") == a.code:
                    targets.append({"sid": sid, "code": p.get("code"),
                                    "name": p.get("name"),
                                    "market": p.get("market"),
                                    "entry_px": p.get("entry_px"),
                                    "qty": p.get("qty"),
                                    "entry_date": day, "pos_pct": None})
                    break
            if targets:
                break
        if not targets:
            targets = [{"sid": "manual", "code": a.code, "name": "",
                        "market": None, "entry_px": None, "qty": None,
                        "entry_date": day, "pos_pct": None}]
    else:
        targets = _today_new(led, day)

    recs, alerts, reviews = [], [], []
    if (not a.watch_only and not a.review_only) or a.code:   # 立案会
        if not a.force:
            done = _done_today(day)
            targets = [t for t in targets if t["code"] not in done]
        if targets:
            # K90 预演先行：真会 run_one 之前先跑模拟投委会（K69 愿景
            # 「真实会前预演」）。票源与真会同源、_sim_done 幂等防重演、
            # Ollama 掉线自动降级——预演层故障不阻断真会。仅全量立案会
            # 触发（手动单票/force 重跑不预演）。
            if a.code is None:
                try:
                    import council_sim
                    council_sim.run()
                except Exception as e:
                    print("[COUNCIL-SIM] preset-run failed: %s"
                          % str(e)[:120])
            print("[COUNCIL] day=%s targets=%d" % (day, len(targets)))
            recs = [r for r in (run_one(t, force=a.force) for t in targets)
                    if r]                         # K94a dup 会返回 None
            if recs:
                _queue_pending(recs)             # K64d 预警入待确认队列
        else:
            print("[COUNCIL] no new positions for %s（或已留痕）" % day)

    if a.code is None:                   # 盯盘+复盘（单票手动模式不扫）
        if not a.review_only:
            _recs_w, alerts = run_watch(led, day)
        reviews = run_reviews(led)

    if not a.no_push and (recs or alerts or reviews):
        _push_day_card(recs, alerts, day, reviews)

    try:                                   # K68 三操盘手锦标赛（影子模拟）
        import trader_sim
        trader_sim.run()
    except Exception as e:
        print("[SIM] trader_sim failed: %s" % str(e)[:120])

    try:                                   # K69 模拟投委会对照打分
        import council_sim
        council_sim.compare()
    except Exception as e:
        print("[COUNCIL-SIM] compare failed: %s" % str(e)[:120])

    try:                                   # K88 预演票喂独立六账
        trader_sim.run(preset=True)
    except Exception as e:
        print("[SIM-预演] trader_sim(preset) failed: %s" % str(e)[:120])

    try:                                   # K55-C4 影子统计缓存刷新
        import council_shadow
        council_shadow.refresh()
    except Exception as e:
        print("[SHADOW] refresh failed: %s" % str(e)[:100])


if __name__ == "__main__":
    main()
