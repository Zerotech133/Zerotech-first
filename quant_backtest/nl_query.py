# -*- coding: utf-8 -*-
"""P-C 自然语言查数：中文问句 → 本地 qwen3:8b-8k FC 循环(≤3轮) → 工具执行 → 汇总答案。

口径（2026-09-28 AskUserQuestion 批准）：
  · 触发面：仅 CLI——python nl_query.py "688411最近30天有什么事件"
  · 工具白名单：agent_tools 全部只读信息层工具（不改信号/推送/拦截口径）
  · 循环：function calling ≤3 轮（一轮可多工具并行）；轮尽仍要工具则无工具
    强汇总一次，再失败退降级拼接版并标注
  · 纠偏（系统层，不改工具层）：
      - args 只留注册表声明的键（防 TypeError）
      - sig_type 枚举校验非法丢弃（实测模型会传"缠论"）
      - 整数/布尔按 schema 强转
      - tbe 意图守卫：问句不含 趋✓/趋势背驰/tbe 时强制丢弃（实测 8b 会过传
        tbe=false 导致漏掉池内 tbe=true 信号）
  · 缓存：data/nlquery/<md5(q)前12>_<yyyymmdd>.json 当日复用，--force 跳过，
    7 天滚动清理
  · LLM：本地 Ollama qwen3:8b-8k，think=false + temp 0（实测 0.5~5s/轮，
    思考预算吞噬问题规避）

用法：
  python nl_query.py "688411最近30天有什么事件"
  python nl_query.py "1卖趋势背驰的左尾统计" --json
  python nl_query.py "预警池最新10条信号" --force
"""

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
import logging

HERE = os.path.dirname(os.path.abspath(__file__))
# 2026-10-07 A/B 全量合并为单一权威版：kg_paths 仅 zerobound-kg（旧名 KGStockAnalysis）侧存在，
# quant_backtest 产线无此模块 → ImportError 回落 HERE/data（两侧行为均不变）
try:
    import kg_paths  # noqa: E402

    _data_root = kg_paths.get_data_root()
except ImportError:
    _data_root = os.path.join(HERE, "data")
sys.path.insert(0, HERE)

CACHE_DIR = os.path.join(_data_root, "nlquery")
OLLAMA_URL = os.environ.get(
    "OLLAMA_URL", "http://127.0.0.1:11434/api/chat"
)  # 2026-10-07 env 化；注意值需含 /api/chat 路径
MODEL = "qwen3.5:9b"  # 2026-10-07 修正：qwen3:8b-8k 已不在本机 Ollama（已升级 qwen3.5 系）致 /api/chat 404；9b 含 tools 能力，与 kg_* 系列同源实测可用
TIMEOUT = 180  # 单请求秒数（含上一轮工具执行后的下一轮生成）
MAX_ROUNDS = 3  # 带 tools 的 FC 轮数上限
TOOL_MSG_MAX = 1200  # 回喂模型的单工具结果截断字符数（8k 上下文保护）

INT_ARGS = {"days", "limit", "min_n", "k", "n", "topn"}
BOOL_ARGS = {"tbe"}
SIG_ENUM = {"1买", "1卖", "2买", "2卖", "3买", "3卖"}
TBE_WORDS = ("趋✓", "趋势背驰", "tbe")  # 问句含这些才允许 tbe 参数通过

HINTS = {  # schema 参数描述覆盖（放参数旁边比 system prompt 更被 8b 看见）
    "tbe": "布尔；仅当用户明确说趋势背驰/趋✓才传；省略=不过滤（更全）",
    "sig_type": "信号类型(1买/1卖/2买/2卖/3买/3卖)；不确定先调 pool_rows 确认",
    "code": "6位股票代码即可，系统自动补交易所后缀",
    "codes": "代码列表，传6位裸码即可，系统自动补后缀（不要自己加.SH/.SZ）",
    "market": "市场 SH/SZ/BJ（也认 沪/深/北交所）；问市场/大盘整体成交额时传，查个股不要传",
}

SYSTEM = """你是个人量化系统的数据查询助手，用提供的只读工具查数回答，不得编造任何数字。
规则：
· 优先精确选工具，可在同一轮选多个工具并行查
· 问句是 6 位纯数字（股票代码）时：先调 stock_info(code) 拿名称+最新行情+
  池内信号，再用 pool_rows(code=...) 等深入；股票名称以工具返回为准，绝不编造
· 问句给的是股票名称时：先调 stock_info(code=名称) 反查代码（全市场映射，
  含曾用名/改名前旧称，name_note 会标注现名）；code=null 就明说无法确认，
  绝不硬猜代码
· 用户没提趋势背驰/趋✓时，绝对不要传 tbe 参数（false 也不要传，传了会漏数据）
· sig_type 不确定时先查 pool_rows 确认该股信号类型，不要猜
· 数字参数按用户口径传，用户没说就不传
· 拿到足够事实后直接给最终中文答案（≤300字，引用具体数字），不要再调用工具；查不到就直说"""

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception as exc:
    logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/nl_query.py", exc)


def _today8():
    return datetime.now(timezone.utc).astimezone().strftime("%Y%m%d")


# ── 工具 schema（agent_tools 注册表 → OpenAI tools 格式） ────
def _schema():
    from agent_tools import TOOLS

    ts = []
    for name, (_fn, desc, args) in TOOLS.items():
        props = {}
        for k, hint in (args or {}).items():
            t = (
                "integer"
                if k in INT_ARGS
                else "boolean"
                if k in BOOL_ARGS
                else "string"
            )
            props[k] = {"type": t, "description": HINTS.get(k, hint)}
        ts.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": desc,
                    "parameters": {
                        "type": "object",
                        "properties": props,
                        "required": [],
                    },
                },
            }
        )
    return ts


def _coerce(name, args, q=""):
    """系统层参数纠偏：白名单键过滤 + 类型强转 + 枚举校验 + tbe 意图守卫。
    未知工具返回 None。"""
    from agent_tools import TOOLS

    reg = TOOLS.get(name)
    if not reg:
        return None
    ok_keys = set((reg[2] or {}).keys())
    tbe_ok = any(w in (q or "") for w in TBE_WORDS)
    out = {}
    for k, v in (args or {}).items():
        if k not in ok_keys:
            continue  # 注册表未声明 → 丢弃（防 TypeError）
        if k == "tbe" and not tbe_ok:
            continue  # 问句未提趋✓ → 强制丢弃（防漏数据）
        try:
            if k in INT_ARGS:
                out[k] = int(v)
            elif k in BOOL_ARGS:
                if isinstance(v, bool):
                    out[k] = v
                elif str(v).lower() in ("true", "1", "是"):
                    out[k] = True
                elif str(v).lower() in ("false", "0", "否"):
                    out[k] = False
                # 其它取值丢弃
            else:
                if k == "sig_type" and str(v) not in SIG_ENUM:
                    continue  # 非法枚举丢弃
                out[k] = str(v)
        except (TypeError, ValueError):
            continue
    return out


# ── LLM（本地 Ollama，think=false 规避思考预算吞噬） ─────────
def _llm(messages, tools=None):
    import requests

    body = {
        "model": MODEL,
        "stream": False,
        "think": False,
        "messages": messages,
        "options": {"temperature": 0.0, "num_predict": 800},
    }
    if tools:
        body["tools"] = tools
    r = requests.post(OLLAMA_URL, json=body, timeout=TIMEOUT)
    r.raise_for_status()
    m = (r.json() or {}).get("message") or {}
    t = m.get("content") or ""
    while "<think>" in t and "</think>" in t:
        t = t.split("</think>", 1)[1]
    m["content"] = t.strip()
    return m


def _run(q):
    """FC 循环：≤3 轮带工具；轮尽仍要工具则无工具强汇总一次。
    返回 (answer|None, calls[{tool,args,res}])。"""
    from agent_tools import call as T

    tools = _schema()
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": q}]
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
            args = _coerce(name, f.get("arguments") or {}, q)
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


def _fallback(calls):
    """降级拼接版（LLM 汇总失败时）。"""
    if not calls:
        return "（未能选出工具·查询失败）"
    lines = ["（最终答案生成失败·降级拼接版）"]
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


# ── 缓存（问句 md5 + 当日日期，--force 跳过，7 天滚动清理） ──
def _cache_path(q):
    h = hashlib.md5(q.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]
    return os.path.join(CACHE_DIR, "%s_%s.json" % (h, _today8()))


def _atomic_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _cleanup(keep_days=7):
    try:
        cutoff = time.time() - keep_days * 86400
        for fn in os.listdir(CACHE_DIR):
            p = os.path.join(CACHE_DIR, fn)
            if fn.endswith(".json") and os.path.getmtime(p) < cutoff:
                os.remove(p)
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/nl_query.py", exc)


# ── 主入口 ───────────────────────────────────────────────────
def answer(q, force=False):
    """自然语言查数：当日缓存命中直接复用，否则 FC 循环现算并落盘。"""
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
            logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/nl_query.py", exc)
    ans, calls = _run(q)
    degraded = not ans
    if degraded:
        ans = _fallback(calls)
    out = {
        "ok": True,
        "q": q,
        "answer": ans,
        "calls": calls,
        "degraded": degraded,
        "elapsed_s": round(time.time() - t0, 1),
    }
    _atomic_json(cp, out)
    _cleanup()
    return out


def main():
    ap = argparse.ArgumentParser(
        description="P-C 自然语言查数（本地 LLM + 只读工具白名单）"
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
