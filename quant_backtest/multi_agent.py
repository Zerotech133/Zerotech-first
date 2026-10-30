# -*- coding: utf-8 -*-
"""P-D 多 agent 编排：LLM router → 3 specialists 串行 → synthesizer 汇总。

口径（2026-09-28 用户批准 A 方案）：
  · info-layer only：不改信号/推送触发/拦截口径
  · LLM 全本地：Ollama qwen3:8b-8k，think=false + temp 0（复用 P-C 范式）
  · 零框架：纯 python，复用 nl_query._llm / _coerce / _schema
  · specialists（各持工具子集 + FC 循环 ≤3 轮）：
      - quant_judge  缠论结构槽位/信号先例/预警池/模拟账本
      - event_face   个股事件流/相关事件/事件摘要/近期强事件
      - data_lookup  数据中台读层/新鲜度/可用性/全仓健康
  · router：无工具，LLM 输出 JSON {plan:[{to,sub_q}]}；解析失败降级全跑
  · synthesizer：无工具，收 router 原问句 + 各 specialist 答案 → 中文汇总
  · specialists 串行（本地 Ollama 单卡，并发无收益且 OOM 风险）
  · 缓存：data/nlmulti/<md5(q)前12>_<yyyymmdd>.json，--force 跳过，7天清理

用法：
  python multi_agent.py "688411最近怎么样"
  python multi_agent.py "1卖趋势背驰的左尾与688411近期事件" --json
  python multi_agent.py "预警池最新10条信号" --force
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# 复用 P-C nl_query 的本地 FC 范式（_llm 已含 think=false+temp0+剥思考块）
from nl_query import _llm, _coerce, _atomic_json, _today8, TOOL_MSG_MAX, MAX_ROUNDS
from agent_tools import call as T
import logging

CACHE_DIR = os.path.join(HERE, "data", "nlmulti")

SPECIALISTS = {
    "quant_judge": {
        "desc": "缠论结构槽位/信号先例/预警池/模拟账本",
        "tools": [
            "stock_info",
            "tgraph_slots",
            "tgraph_eval",
            "tgraph_left_tail",
            "tgraph_fps",
            "signal_precedents",
            "signal_enrich",
            "pool_rows",
            "paper_positions",
        ],
        "system": "你是缠论量化研判 specialist，只用提供的工具查结构/先例/账本数据，"
        "不得编造任何数字。\n"
        "规则：\n"
        "· 用户没提趋势背驰/趋✓时，绝对不要传 tbe 参数\n"
        "· sub_q 含 6 位股票代码时先调 stock_info(code) 拿名称与池内"
        "信号概况；查该股信号明细用 pool_rows(code=...)，不要全池扫描\n"
        "· sub_q 是股票名称（非代码）时：先调 stock_info(code=名称)"
        "反查代码（全市场映射，支持曾用名/改名前旧称）；拿不到代码就"
        "明说无法确认，绝不自己猜代码\n"
        "· sig_type 不确定时先查 pool_rows 确认该股信号类型\n"
        "· 工具没返回股票名称时答案只写代码，绝不猜名称\n"
        "· 拿到足够事实后直接给中文答案（≤200字，引用具体数字），"
        "不要再调用工具",
    },
    "event_face": {
        "desc": "个股事件流/相关事件语义检索/事件摘要/近期强事件",
        "tools": [
            "events_for_stock",
            "related_events",
            "event_summary",
            "recent_events",
        ],
        "system": "你是事件面 specialist，只用提供的工具查事件，不得编造。\n"
        "规则：\n"
        "· 工具选择：查某只股票的事件用 events_for_stock(code)；"
        "查全市场近期强事件用 recent_events；语义检索相关事件用 "
        "related_events(query)；个股事件多空摘要用 event_summary(code)\n"
        "· sub_q 含 6 位股票代码时，绝对用 events_for_stock(code) 查该股"
        "事件，不要用 recent_events（那是全市场事件，不含个股）\n"
        "· 工具没返回股票名称时答案只写代码，绝不编造名称\n"
        "· recent_events 是全市场新闻：新闻里出现的公司名与所查代码"
        "无关（如问 300419 时新闻里出现'新思科技'不代表 300419 是"
        "新思科技）；绝不把全市场新闻里的公司名当作所查股票名称\n"
        "· 拿到足够事实后直接给中文答案（≤200字，引用具体事件标题"
        "与偏性），不要再调用工具",
    },
    "data_lookup": {
        "desc": "数据中台读层/表新鲜度/可用性/全仓健康/市场总量",
        "tools": [
            "stock_info",
            "market_snapshot",
            "datamart_read",
            "datamart_latest",
            "datamart_ready",
            "data_health",
        ],
        "system": "你是数据查数 specialist，只用提供的工具读数据中台，不得编造。\n"
        "规则：\n"
        "· 市场总量问题（大盘/交易所整体成交额、量能、涨跌家数，"
        "如'北交所今天成交额'）：用 market_snapshot(market=..., days=...)，"
        "不要用 stock_info/datamart_read 试\n"
        "· table 参数是表名（如 ts_daily/news/notice/chanlun_pool），"
        "不是股票代码\n"
        "· 查个股名称/最新行情/池内信号：先调 stock_info(code)，"
        "股票名称以它返回为准，绝不编造\n"
        "· 用户给股票名称时：先调 stock_info(code=名称) 反查代码"
        "（全市场映射，含曾用名/改名前旧称，name_note 会标注现名）；"
        "code=null 就明说无法确认，绝不硬猜代码\n"
        "· datamart_read 的 codes 传 6 位裸码，"
        "系统自动补后缀；不要自己加 .SH/.SZ\n"
        "· fields 只传真实存在的列（trade_date/close/pct_chg/vol/"
        "amount 等）；price/name/turnover_rate 不存在，不要传\n"
        "· 用户没指定时间区间就不要猜年份，不传 start/end 即查最近\n"
        "· datamart_ready/datamart_latest 需具体表名，不支持通配符 *\n"
        "· 用户问'有哪些表'或'表可用'时，用 data_health 看全仓状态\n"
        "· 拿到足够事实后直接给中文答案（≤200字，引用具体表名/日期/"
        "行数），不要再调用工具",
    },
}

ROUTER_SYSTEM = """你是任务分发 agent。把用户问句拆成子任务，每个子任务指派给一个 specialist。

可用的 specialist：
- quant_judge：缠论结构槽位/信号先例/预警池/模拟账本
- event_face：个股事件流/相关事件语义检索/事件摘要/近期强事件
- data_lookup：数据中台读层/表新鲜度/可用性/全仓健康

规则：
· 用户问句是 6 位纯数字（股票代码，如 "600000"）= 查该股全景：三个
  specialist 都要派（data_lookup 查名称行情、quant_judge 查池内信号、
  event_face 查事件）
· 市场整体/交易所总量类问句（大盘成交额、量能、涨跌家数，如
  "北交所今天成交额"）= 只派 data_lookup
· 用户问句含股票名称（非代码）时：你若有把握知道对应候选代码，把它写进
  相关 sub_q，格式如"某某股份（候选代码 123456，需用 stock_info 核对现名）"；
  无把握就只传名称，绝不把无把握的码写成确定事实
· 无关 specialist 不要列入
· 一个 specialist 可承接多个子任务（合并成一个 sub_q）
· sub_q 必须是中文完整问句，能独立被 specialist 理解
· 只输出 JSON，不要任何其它文字、不要 markdown 代码块、不要解释
· 输出格式：{"plan":[{"to":"specialist名","sub_q":"中文子任务问句"}]}"""

SYNTH_SYSTEM = """你是综合 agent。下面是用户原问句和各 specialist 的查数结果。
综合成一份连贯中文答案：
· ≤400字
· 引用具体数字/事件标题
· 股票名称只能引用 specialist 结果里工具返回的名称；没有名称就只写代码，
  绝不编造名称，也不要调和两个不同的名称
· 股票代码必须来自工具返回；模型记忆产生的候选码只能写成"可能是 XXX，
  请核实"，绝不写成确定事实（含"正确代码为 X"这类断言）
· 不要编造、不要重复 specialist 的元叙述（如"工具返回"）
· 如果某 specialist 没有相关信息，不要提它
· 直接给答案，不要前言"""

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception as exc:
    logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/multi_agent.py", exc)


def _schema_subset(tool_names):
    """specialist 子集工具 schema（OpenAI tools 格式）。"""
    from nl_query import _schema

    full = {t["function"]["name"]: t for t in _schema()}
    return [full[n] for n in tool_names if n in full]


# ── specialist：FC 循环（复用 nl_query 范式，tools=子集） ─────
def _specialist_run(sub_q, spec_name, parent_q=""):
    """单 specialist 的 FC 循环 ≤3 轮。返回 (answer|None, calls)。"""
    spec = SPECIALISTS[spec_name]
    tools = _schema_subset(spec["tools"])
    messages = [
        {"role": "system", "content": spec["system"]},
        {"role": "user", "content": sub_q},
    ]
    calls = []
    for _ in range(MAX_ROUNDS):
        m = _llm(messages, tools)
        tcs = m.get("tool_calls") or []
        msg = {"role": "assistant", "content": m.get("content") or ""}
        if tcs:
            msg["tool_calls"] = tcs
        messages.append(msg)
        if not tcs:
            return (m.get("content") or None), calls
        for tc in tcs:
            f = tc.get("function") or {}
            name = f.get("name")
            # tbe 意图守卫用用户原问句（更准），sig_type 等仍按 sub_q
            args = _coerce(name, f.get("arguments") or {}, parent_q or sub_q)
            calls.append({"tool": name, "args": args})
            if args is None:
                res = {"ok": False, "err": "unknown tool"}
            else:
                res = T(name, **args)
            c = json.dumps(res, ensure_ascii=False)
            if len(c) > TOOL_MSG_MAX:
                c = c[:TOOL_MSG_MAX] + "…[截断]"
            calls[-1]["res"] = c[:600]
            messages.append({"role": "tool", "content": c, "tool_name": name})
    messages.append(
        {
            "role": "user",
            "content": "基于以上工具结果直接给出最终中文答案，不要再调用工具。",
        }
    )
    m = _llm(messages)
    return (m.get("content") or None), calls


def _fallback_specialist(spec_name, calls):
    if not calls:
        return "（%s 未能选出工具·查数失败）" % spec_name
    lines = ["（%s 最终答案生成失败·降级拼接版）" % spec_name]
    for c in calls:
        lines.append(
            "· %s %s → %s"
            % (
                c.get("tool"),
                json.dumps(c.get("args"), ensure_ascii=False),
                (c.get("res") or "")[:300],
            )
        )
    return "\n".join(lines)


# ── router：LLM 输出 JSON 任务计划 ─────────────────────────────
_JSON_RE = re.compile(r"\{[\s\S]*\}")


def _router(q):
    """LLM 路由。返回 (plan, raw_text)。plan=[(spec_name, sub_q), ...]。
    解析失败降级：全 specialist 各跑原问句。"""
    messages = [
        {"role": "system", "content": ROUTER_SYSTEM},
        {"role": "user", "content": q},
    ]
    m = _llm(messages)  # 无工具
    t = m.get("content") or ""
    plan = []
    try:
        match = _JSON_RE.search(t)
        if match:
            d = json.loads(match.group(0))
            for item in d.get("plan", []):
                to = item.get("to")
                sub_q = (item.get("sub_q") or "").strip()
                if to in SPECIALISTS and sub_q:
                    plan.append((to, sub_q))
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/multi_agent.py", exc)
    if not plan:
        plan = [(name, q) for name in SPECIALISTS]  # 降级全跑
    return plan, t


# ── synthesizer：无工具汇总 ────────────────────────────────────
def _synth(q, spec_answers):
    """spec_answers = [(spec_name, sub_q, answer), ...]。返回综合答案|None。"""
    parts = ["用户原问句：%s" % q, ""]
    for name, sub_q, ans in spec_answers:
        parts.append("【%s】子任务：%s" % (name, sub_q))
        parts.append("结果：%s" % ans)
        parts.append("")
    parts.append("请综合上述信息给出最终中文答案。")
    messages = [
        {"role": "system", "content": SYNTH_SYSTEM},
        {"role": "user", "content": "\n".join(parts)},
    ]
    m = _llm(messages)
    return m.get("content") or None


# ── 缓存（当日 md5，--force 跳过，7天滚动清理） ────────────────
def _cache_path(q):
    h = hashlib.md5(q.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]
    return os.path.join(CACHE_DIR, "%s_%s.json" % (h, _today8()))


def _cleanup_cache(keep_days=7):
    try:
        cutoff = time.time() - keep_days * 86400
        for fn in os.listdir(CACHE_DIR):
            p = os.path.join(CACHE_DIR, fn)
            if fn.endswith(".json") and os.path.getmtime(p) < cutoff:
                os.remove(p)
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/multi_agent.py", exc)


# ── 主入口 ─────────────────────────────────────────────────────
def answer(q, force=False, read_only=False):
    """多 agent 查数：router→specialists→synth。当日缓存命中直接复用。

    read_only=True：仅读缓存，未命中返回 empty 标记不触发 LLM（门户页面
    加载安全口径，对齐 research_agent）。"""
    t0 = time.time()
    cp = _cache_path(q)
    if not force and os.path.exists(cp):
        try:
            with open(cp, encoding="utf-8") as f:
                d = json.load(f)
            if d.get("answer"):
                d["cached"] = True
                d["elapsed_s"] = round(time.time() - t0, 1)
                return d
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/multi_agent.py", exc)

    # read_only：缓存未命中不触发 LLM（页面加载零算力口径）
    if read_only:
        return {
            "ok": True,
            "empty": True,
            "q": q,
            "msg": "暂无当日缓存，点按钮触发现算（10-30s）",
            "elapsed_s": round(time.time() - t0, 1),
        }

    # 1) router
    plan, router_raw = _router(q)

    # 2) specialists 并行（实测 1.87x 加速：串行 16.6s → 并行 8.9s；
    #    Ollama 单卡可交错处理并发请求（batch scheduling），单请求延迟略增
    #    但总 wall 接近最慢单请求；VRAM 6.2GB 单模型 3 并发未 OOM，
    #    结果 3/3 一致。ex.map 保 plan 顺序供 synth 按序汇总）
    from concurrent.futures import ThreadPoolExecutor

    def _run_spec(args):
        spec_name, sub_q = args
        ans, calls = _specialist_run(sub_q, spec_name, parent_q=q)
        if not ans:
            ans = _fallback_specialist(spec_name, calls)
        return {"specialist": spec_name, "sub_q": sub_q, "answer": ans, "calls": calls}

    with ThreadPoolExecutor(max_workers=len(plan)) as ex:
        spec_results = list(ex.map(_run_spec, plan))

    # 3) synthesizer
    synth_in = [(r["specialist"], r["sub_q"], r["answer"]) for r in spec_results]
    final = _synth(q, synth_in)
    degraded = not final
    if degraded:
        lines = ["（综合失败·降级拼接版）"]
        for r in spec_results:
            lines.append("【%s】%s" % (r["specialist"], r["answer"]))
        final = "\n".join(lines)

    out = {
        "ok": True,
        "q": q,
        "answer": final,
        "router_raw": router_raw,
        "plan": [{"to": n, "sub_q": sq} for n, sq in plan],
        "specialists": spec_results,
        "degraded": degraded,
        "elapsed_s": round(time.time() - t0, 1),
    }
    _atomic_json(cp, out)
    _cleanup_cache()
    return out


def main():
    ap = argparse.ArgumentParser(
        description="P-D 多 agent 编排（router+specialists+synth，本地 LLM）"
    )
    ap.add_argument("query", nargs="+", help="中文问句")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument("--force", action="store_true", help="跳过当日缓存强制现算")
    a = ap.parse_args()
    d = answer(" ".join(a.query), force=a.force)
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=1))
    else:
        print(d["answer"])


if __name__ == "__main__":
    main()
