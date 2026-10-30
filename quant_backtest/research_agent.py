# -*- coding: utf-8 -*-
"""P-B2 个股按需深调：事实包(agent_tools) → 本地 LLM 研判 → 白名单校验 → 原子缓存。

口径（2026-09-28 定稿）：
  · 事实：pool_rows / signal_precedents / tgraph_left_tail / events_for_stock
  · LLM：本地 Ollama qwen3.5:9b（与 sellpoint_ai 同源），≤220 字中文
  · 建议：白名单动作（买入/卖出/清仓/建仓/加仓/减仓/做空/止盈/止损/
    回避/入场/离场 + 中性 持有/观望），须引用事实依据
  · 校验（反向）：出参必含 ≥1 白名单动词；缺→重试 1 次→仍缺退拼接版
    并标注"建议段生成失败"（个人决策辅助，保留免责行）
  · 缓存：data/semantica/research/<code6>_<yyyymmdd>.json 原子写，当日复用

用法：
  python research_agent.py 688411 [--force]   # 全链路
  python research_agent.py 688411 --facts     # 只看事实包（不跑 LLM）
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
sys.path.insert(0, HERE)

RESEARCH_DIR = os.path.join(HERE, "data", "semantica", "research")
OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
MODEL = "qwen3.5:9b"  # 本地 Ollama（077h 实测 qwen3:8b-8k 已不在
# 本地模型清单，静默降级拼接版；改指 9b 恢复）
TIMEOUT = 90  # 研判段 ~220 字，9b CPU 生成 20-60s 留余量
# 白名单：用户二改定稿——动词是"建议动作" vocabulary（非禁用表）
ACTIONS = [
    "买入",
    "卖出",
    "清仓",
    "建仓",
    "加仓",
    "减仓",
    "做空",
    "止盈",
    "止损",
    "回避",
    "入场",
    "离场",
    "持有",
    "观望",
]
DISCLAIMER = "个人决策辅助·非投资建议"

PROMPT = """你是个人量化研究系统的个股研判助手。只基于以下事实研判，不得编造任何数字。

{facts}

输出（中文 ≤220 字，三行，行首固定标签）：
解读: 归因解读上述数字组合的含义
建议: <动作>。<一句理由，必须引用上面事实中的具体数字>
风险: 样本量/左尾/事件面风险
要求：建议动作必须且只能从 {actions} 中选一个。"""


def _norm_code(code):
    """代码归一（2026-10-16 章程 §4.3 收编）：判定委托 codes.normalize_code
    唯一权威；非 A 股形态原样返回。"""
    from codes import normalize_code

    n = normalize_code(code)
    if n is not None:
        return n
    return str(code or "").strip().upper()


def _today8():
    return datetime.now(timezone.utc).astimezone().strftime("%Y%m%d")


def _cache_path(code):
    return os.path.join(
        RESEARCH_DIR, "%s_%s.json" % (str(code).split(".")[0], _today8())
    )


def _atomic_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _fp(facts):
    """事实指纹：事实包规范化序列化 → md5。指纹一致=事实未变=可复用研判。"""
    core = {
        k: facts.get(k)
        for k in (
            "code",
            "pool",
            "prec",
            "npv_group",
            "left_tail",
            "events_n",
            "events_net",
            "events_recent",
        )
    }
    return hashlib.md5(
        json.dumps(core, sort_keys=True, ensure_ascii=False).encode("utf-8")
    , usedforsecurity=False).hexdigest()[:12]


def _cleanup(keep_days=7):
    """缓存滚动清理：mtime 超过 keep_days 的旧文件删除（防堆积）。"""
    try:
        cutoff = time.time() - keep_days * 86400
        for fn in os.listdir(RESEARCH_DIR):
            p = os.path.join(RESEARCH_DIR, fn)
            if fn.endswith(".json") and os.path.getmtime(p) < cutoff:
                os.remove(p)
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/research_agent.py", exc)


# ── 事实包（全部走 agent_tools，异常兜底空） ─────────────────
def _facts(code):
    code = _norm_code(code)
    from agent_tools import call as T

    f = {"code": code, "ts": _today8()}
    # ① 池内信号（该股行）
    try:
        pr = T("pool_rows", limit=5000)
        rows = [
            r
            for r in ((pr.get("data") or {}).get("rows") or [])
            if r.get("code") == code
        ]
        f["pool"] = [
            {k: r.get(k) for k in ("type", "dt", "price", "tbe", "npv", "detect_date")}
            for r in rows[:5]
        ]
    except Exception:
        f["pool"] = []
    # ② 先例战绩（首个池信号有类型才查）
    if f["pool"]:
        sig = f["pool"][0]
        try:
            p = T(
                "signal_precedents",
                code=code,
                sig_type=sig.get("type"),
                tbe=sig.get("tbe"),
                k=5,
            )
            pd = p.get("data") or {}
            f["prec"] = {
                k: pd.get(k) for k in ("n", "win_rate", "avg_return", "median_return")
            }
        except Exception:
            f["prec"] = None
        # ③ npv 归组左尾（池行带 npv 才查）
        npv = sig.get("npv")
        if npv is not None:
            f["npv_group"] = "≤2枢" if npv <= 2 else "3+枢"
            try:
                lt = T(
                    "tgraph_left_tail",
                    sig_type=sig.get("type"),
                    tbe=bool(sig.get("tbe")),
                )
                gd = ((lt.get("data") or {}).get("le2" if npv <= 2 else "3p")) or {}
                f["left_tail"] = {
                    k: gd.get(k) for k in ("n", "win", "avg", "p10", "worst", "lt30")
                }
            except Exception:
                f["left_tail"] = None
    # ④ 事件面（近30天）
    try:
        ev = T("events_for_stock", code=code, days=30)
        ed = ev.get("data") or {}
        f["events_n"] = ed.get("n")
        f["events_net"] = (ed.get("bias_stat") or {}).get("net")
        f["events_recent"] = [
            {
                "ts": e.get("ts"),
                "bias": e.get("bias"),
                "text": (e.get("text") or "")[:60],
            }
            for e in (ed.get("events") or [])[:5]
        ]
    except Exception:
        f["events_n"] = f["events_net"] = None
    return f


def _facts_text(f):
    """事实包 → 紧凑文本（喂 LLM）。"""
    parts = ["个股: %s" % f.get("code")]
    if f.get("pool"):
        parts.append(
            "池内信号: "
            + "; ".join(
                "%s@%s 价%s tbe=%s npv=%s"
                % (
                    r.get("type"),
                    r.get("dt"),
                    r.get("price"),
                    r.get("tbe"),
                    r.get("npv"),
                )
                for r in f["pool"]
            )
        )
    else:
        parts.append("池内信号: 无（当前不在缠论预警池）")
    p = f.get("prec")
    if p and p.get("n"):
        parts.append(
            "同型先例: n=%s 胜率%.0f%% 均值%+.2f%% 中位%+.2f%%"
            % (
                p["n"],
                (p.get("win_rate") or 0) * 100,
                (p.get("avg_return") or 0) * 100,
                (p.get("median_return") or 0) * 100,
            )
        )
    if f.get("npv_group"):
        lt = f.get("left_tail") or {}
        parts.append(
            "npv归组: %s（组内 n=%s 胜率%s%% 均值%+.2f%% "
            "p10=%s%% worst=%s%% <-30%%:%s例）"
            % (
                f["npv_group"],
                lt.get("n"),
                lt.get("win"),
                lt.get("avg"),
                lt.get("p10"),
                lt.get("worst"),
                lt.get("lt30"),
            )
        )
    parts.append(
        "近30天事件: %s 条 多空净分 %s" % (f.get("events_n"), f.get("events_net"))
    )
    for e in f.get("events_recent") or []:
        parts.append("  · %s [%s] %s" % (e.get("ts"), e.get("bias"), e.get("text")))
    return "\n".join(parts)


# ── LLM（本地 Ollama） ───────────────────────────────────────
def _llm(prompt):
    try:
        import requests

        r = requests.post(
            OLLAMA_URL,
            json={
                "model": MODEL,
                "stream": False,
                "think": False,
                # think:False 对齐 news_ai/alias_audit 范式——qwen3.5 思考模型
                # 不关思考时 num_predict 全被 thinking 吃掉，content 恒空
                "messages": [{"role": "user", "content": prompt}],
                "options": {"temperature": 0.3, "num_predict": 600},
            },
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        t = ((r.json() or {}).get("message") or {}).get("content") or ""
        # qwen3 思考模式会先输出 <think>…</think> 吃掉 token 预算，剥除
        while "<think>" in t and "</think>" in t:
            t = t.split("</think>", 1)[1]
        return t.strip() or None
    except Exception as e:
        print("llm fail: %s" % str(e)[:120])
        return None


def _validate(text):
    """反向校验：必含 ≥1 白名单动词 + 完整性（建议/风险行）。"""
    if not text:
        return False
    if not any(a in text for a in ACTIONS):
        return False
    return "建议" in text and "风险" in text


def _fallback(facts):
    """拼接版降级（LLM 失败/校验不过）。"""
    return "（建议段生成失败·降级拼接版）\n" + _facts_text(facts) + "\n" + DISCLAIMER


# ── 主入口 ───────────────────────────────────────────────────
def _latest_cache(code):
    """该码最新缓存文件（跨日指纹复用，不限于今天）。"""
    c6 = str(code).split(".")[0]
    try:
        fs = [
            os.path.join(RESEARCH_DIR, f)
            for f in os.listdir(RESEARCH_DIR)
            if f.startswith(c6 + "_") and f.endswith(".json")
        ]
        return max(fs, key=os.path.getmtime) if fs else None
    except Exception:
        return None


def research_for_stock(code, force=False, read_only=False):
    """个股深度研判：事实指纹复用——先算事实包(1-3s)，指纹与缓存一致
    直接复用（跳过 10-90s LLM），事实变化自动重算；
    force 跳过指纹强制重算；read_only 只读缓存不触发 LLM/事实包。"""
    latest = _latest_cache(code)
    if read_only:
        if latest:
            try:
                with open(latest, encoding="utf-8") as f:
                    d = json.load(f)
                d["cached"] = True
                return d
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/research_agent.py", exc)
        return {"ok": True, "empty": True, "code": _norm_code(code)}
    facts = _facts(code)
    fp = _fp(facts)
    if not force and latest:
        try:
            with open(latest, encoding="utf-8") as f:
                d = json.load(f)
            if d.get("fp") == fp and d.get("text"):
                d["cached"] = True
                d["fp_match"] = True
                return d
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/research_agent.py", exc)
    text, src = None, None
    for attempt in (1, 2):
        t = _llm(PROMPT.format(facts=_facts_text(facts), actions="/".join(ACTIONS)))
        if _validate(t):
            text = t.strip()
            src = "llm" if attempt == 1 else "llm_retry"
            break
    if not text:
        text = _fallback(facts)
        src = "fallback"
    out = {
        "ok": True,
        "code": _norm_code(code),
        "date": _today8(),
        "fp": fp,
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": src,
        "model": MODEL if src != "fallback" else None,
        "text": text,
        "facts": facts,
        "disclaimer": DISCLAIMER,
    }
    _atomic_json(_cache_path(code), out)
    _cleanup()
    out["cached"] = False
    return out


def main():
    ap = argparse.ArgumentParser(description="P-B2 个股按需深调")
    ap.add_argument("code")
    ap.add_argument("--force", action="store_true", help="跳缓存重算")
    ap.add_argument("--facts", action="store_true", help="只看事实包")
    a = ap.parse_args()
    if a.facts:
        print(json.dumps(_facts(a.code), ensure_ascii=False, indent=1))
        return
    t0 = time.time()
    r = research_for_stock(a.code, force=a.force)
    tag = (
        "（缓存·指纹复用）"
        if r.get("fp_match")
        else ("（缓存）" if r.get("cached") else "")
    )
    print("来源: %s%s 耗时%.1fs" % (r.get("source"), tag, time.time() - t0))
    print(r.get("text"))


if __name__ == "__main__":
    main()
