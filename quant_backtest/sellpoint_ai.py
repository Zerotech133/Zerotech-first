# -*- coding: utf-8 -*-
r"""SellPoint AI 卖点研判域 · 影子信息层（K208 落地 K207-C 四件套①②）。

口径：缠论标准卖点（1卖=趋势背驰 / 2卖=次高点 / 3卖=中枢破坏，引擎
chanlun_bi 已产出）。研判卡只做信息层——不拦截、不改信号/推送/入池口径
（project_memory 多智能体铁律）。

链路（一次 --once）：
  触发（纯代码）——候选池卖出侧 fresh 行（type∈1卖/2卖/3卖）+ 1卖 tbe
    影子流，(code,type,dt) 幂等去重
  组装——信号/结构位置(npv)/大盘(reg)/可成交(tradable)/模拟持仓关联
    （chanlun_paper.json open[]，浮盈与持有天数入卡）
  研判——本地 Ollama qwen3.5:9b 一卡三行（强度/建议/风险），白名单
    动词校验（沿用 research_agent 范式：缺动词重试 1 次，仍缺标注降级）
  落盘——data\sellpoint_cards.jsonl 原子写（影子：只记不推）
  推送——config sellpoint.push（默认 false）+ feishu.push 才发；幂等
    data\sellpoint_pushed.json（(code,dt) 键）；多条合并单条消息

范围（影子期，K207-C「1卖 tbe 优先，2卖/3卖影子扩容」）：
  1卖 全量 fresh；2卖/3卖 仅模拟持仓命中——config
  sellpoint.include_all_types=true 扩容全类型。每次运行上限 --limit
  （默认 12），超限次日续跑；超 FRESH_DAYS 天未处理自然过期（候选池
  本身已全量记账，研判卡只是增量信息层）。

用法：
  python sellpoint_ai.py --once            # 默认：研判 fresh 卖出信号
  python sellpoint_ai.py --limit 40        # 提高本次上限（首播回填用）
  python sellpoint_ai.py --status          # 只看影子统计（不动 LLM）
"""

import argparse
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timedelta, timezone
import logging
# safe_path=true 时脚本目录不入 sys.path：把本树根加入 path（helper 同目录）
import sys as _sys
from pathlib import Path as _Path
for _p in _Path(__file__).resolve().parents:
    if (_p / "safe_urlopen.py").exists():
        _sys.path.insert(0, str(_p))
        break
try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包/仓库根导入时
    from tools.safe_urlopen import safe_urlopen

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
NEWS_DIR = os.path.abspath(os.path.join(HERE, "..", "news_collector"))

POOL = os.path.join(DATA, "chanlun_pool.jsonl")
SHADOW = os.path.join(DATA, "chanlun_shadow_1sell_tbe.jsonl")
PAPER = os.path.join(DATA, "chanlun_paper.json")
CARDS = os.path.join(DATA, "sellpoint_cards.jsonl")
SEEN = os.path.join(DATA, "sellpoint_seen.json")
PUSHED = os.path.join(DATA, "sellpoint_pushed.json")
CONFIG = os.path.join(HERE, "config.json")

OLLAMA = "http://127.0.0.1:11434/api/chat"
MODEL = "qwen3.5:9b"  # 本地 Ollama（与 research_agent/news_ai 同源）
TIMEOUT = 120  # 9b 单卡 20-60s，留余量
FRESH_DAYS = 7  # fresh 窗口（自然日，跨周末/假期）
MAX_CARDS = 12  # 单次运行默认上限（防 LLM 排队过久）
LLM_FAIL_ABORT = 3  # 连续 N 次 LLM 失败则中止本轮（防白烧）

SELL_TYPES = ("1卖", "2卖", "3卖")
TYPE_NOTE = {"1卖": "趋势背驰", "2卖": "次高点", "3卖": "中枢破坏"}
# 卖出侧白名单（research_agent ACTIONS 的卖出子集 + 中性项）
ACTIONS = ["卖出", "减仓", "清仓", "止盈", "离场", "回避", "观望", "持有"]
DISCLAIMER = "影子信息层·不构成交易依据"

# ── 多 Agent 卖点研判平台（K2xx MVP，学 stock_council 注册表） ──
# 每 agent = 独立投研机构：不同 persona / 卖点侧重 / 动作偏置 / 择时规则。
# 卖法差异本期载体=prompt+侧重+白名单偏置+择时，不改缠论引擎参数（二期再引入
# BEICHI_RATIO/BI_MIN_GAP 真引擎分歧）。所有 agent 共享同一持仓池（chanlun_paper），
# 各自独立研判同一批卖出信号 → 锦标赛对照找最优。
AGENTS = {
    "cons_1sell": {
        "method": "保守·趋势背驰派",
        "focus": "1卖",  # 主抓趋势背驰（tbe 确认）
        "actions": ["卖出", "清仓", "止盈", "观望", "持有"],  # 偏保守动作集
        "timing": "检出日收盘一次性清仓",  # 择时规则快照
        "persona": (
            "你是保守派缠论卖点研判。风格：宁可错过不追卖，只有确认趋势"
            "背驰(tbe)且结构充分(中枢≥2)才给强信号；未确认前降到观望。"
        ),
    },
    "aggr_3sell": {
        "method": "激进·盘整/次高派",
        "focus": "2卖/3卖",  # 主抓次高点与中枢破坏
        "actions": ["卖出", "减仓", "止盈", "离场", "回避", "观望", "持有"],
        "timing": "持有至信号确认+分批止盈",
        "persona": (
            "你是激进派缠论卖点研判。风格：次高点(2卖)/中枢破坏(3卖)只要"
            "结构出现就给偏强信号，倾向分批止盈锁利而非一次性清仓；"
            "看重大盘环境与浮盈。"
        ),
    },
}
DEFAULT_AGENT = "cons_1sell"
LEGACY_AGENT = "legacy_cons"  # 历史 67 卡标注用（schema 兼容）


def _agent(an):
    return (AGENTS.get(an or DEFAULT_AGENT) or AGENTS[DEFAULT_AGENT]) | {
        "name": an or DEFAULT_AGENT
    }


def _agent_prompt(agent):
    """按 agent 生成的研判 prompt（学原 PROMPT，注入 persona + focus）。"""
    return """{persona}

你是缠论 {focus} 卖点研判。只基于以下事实研判，不得编造任何数字。

{facts}

输出（中文 ≤200 字，三行，行首固定标签）:
强度: 强/中/弱 —— 一句归因（引用结构位置或背驰类型）
建议: <动作>。一句理由，必须引用上面事实中的具体数字
风险: 一句风险提示（确认滞后/分批/大盘环境）
要求：建议动作必须且只能从 {actions} 中选一个；未持仓时倾向 观望。
""".format(
        persona=agent["persona"],
        focus=agent["focus"],
        facts="{facts}",
        actions="{actions}",
    )


# ── 基础工具 ──────────────────────────────────────────────
def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today():
    return datetime.now().strftime("%Y-%m-%d")


def _load_jsonl(path):
    rows = []
    if os.path.exists(path):
        with io.open(path, encoding="utf-8") as f:
            for ln in f:
                try:
                    rows.append(json.loads(ln))
                except Exception:
                    continue
    return rows


def _atomic_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    os.replace(tmp, path)


def _load_json(path, default):
    if os.path.exists(path):
        try:
            with io.open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/sellpoint_ai.py", exc)
    return default


def _atomic_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


def _cfg():
    return _load_json(CONFIG, {})


def _active_agents():
    """启用的投研 agent：config sellpoint.agents 白名单（缺省=全部 AGENTS 的多
    agent 平台模式；配置 -> only 收窄该名单）。
    多 Agent 平台定位：未显式配置时运行全部 agent（cons_1sell + aggr_3sell），
    各自独立研判 → 锦标赛对照。空名单输入视为不指定 → 全量。"""
    names = (_cfg().get("sellpoint") or {}).get("agents")
    if names and isinstance(names, list):
        on = [n for n in names if n in AGENTS]
        if on:
            return on
    return list(AGENTS)


# ── 触发：fresh 卖出信号候选 ─────────────────────────────
def _paper_index():
    """模拟持仓 code → open 行（卖点研判的消费主场景）。"""
    paper = _load_json(PAPER, {})
    idx = {}
    for p in paper.get("open") or []:
        c = p.get("code")
        if c and c not in idx:
            idx[c] = p
    return idx


def _priority(r):
    """排序键：持仓 1卖 tbe 最优先 → 1卖 tbe → 1卖 → 持仓 2卖 → 持仓 3卖。
    （K207-C：1卖 tbe 优先；2卖/3卖仅持仓命中入范围。）"""
    held = 1 if r.get("_held") else 0
    tbe = 1 if (r.get("type") == "1卖" and r.get("tbe") is True) else 0
    trank = {"1卖": 0, "2卖": 1, "3卖": 2}.get(r.get("type"), 9)
    return (
        0 if (held and tbe) else (1 if tbe else (2 if held else 3)),
        trank,
        0 if held else 0,
        -int(str(r.get("detect_date") or "0").replace("-", "") or 0),
    )


def candidates(limit=MAX_CARDS, include_all=False):
    """fresh 卖出信号 → 排序去重后的候选列表。

    fresh 口径：detect_date（池行）/ first_seen（影子行）在 FRESH_DAYS
    自然日内。幂等键 (agent, code, type, dt)：某信号仅当其**所有启用 agent**
    均已研判才跳过；否则附 `_pending_agents`（该信号尚未覆盖的 agent），
    供 run_once 逐 agent 补卡。历史 SEEN 短 key 自动视为 legacy 维度已含。
    """
    cutoff = (datetime.now() - timedelta(days=FRESH_DAYS)).strftime("%Y-%m-%d")
    seen = {tuple(x) for x in _load_json(SEEN, [])}
    agents = tuple(_active_agents())
    paper = _paper_index()
    merged = {}
    for r in _load_jsonl(POOL):
        if r.get("type") not in SELL_TYPES:
            continue
        if str(r.get("detect_date") or "") < cutoff:
            continue
        key = (r.get("code"), r.get("type"), r.get("dt"))
        r["_held"] = r.get("code") in paper
        merged[key] = r
    for r in _load_jsonl(SHADOW):
        # 影子流全为 1卖 tbe；池行已有的只补 entry_px/lag 缺口
        key = (r.get("code"), "1卖", r.get("dt"))
        base = merged.get(key)
        if base is None:
            if str(r.get("first_seen") or "")[:10] < cutoff:
                continue
            base = {
                "market": r.get("market", "CN"),
                "code": r.get("code"),
                "name": r.get("name", ""),
                "type": "1卖",
                "dt": r.get("dt"),
                "price": r.get("price"),
                "detect_date": str(r.get("first_seen") or "")[:10],
                "lag": r.get("lag"),
                "entry_px": r.get("entry_px"),
                "tbe": r.get("tbe"),
                "npv": r.get("npv"),
                "reg": None,
                "tradable": None,
                "_held": r.get("code") in paper,
            }
            merged[key] = base
        else:
            if base.get("entry_px") is None and r.get("entry_px") is not None:
                base["entry_px"] = r.get("entry_px")
    out = []
    for r in merged.values():
        if not include_all and r["type"] in ("2卖", "3卖") and not r["_held"]:
            continue  # 影子期范围：2卖/3卖仅持仓命中（include_all 扩容）
        base = (r.get("code"), r.get("type"), r.get("dt"))
        pend = [a for a in agents if (a,) + base not in seen]
        if not pend:
            continue  # 该信号所有启用 agent 均已研判
        r["_pending_agents"] = pend
        out.append(r)
    out.sort(key=_priority)
    return out[:limit]


# ── 组装：事实快照（纯代码，不编造） ─────────────────────
def _fmt_reg(reg):
    if reg in ("up", "down", "flat"):
        return {"up": "up（顺风）", "down": "down（逆风）", "flat": "flat（震荡）"}[reg]
    return "未知"


def _facts(r, paper_row, agent):
    """按 agent 组装事实快照（纯代码，不编造）。加 focus 侧重行提示。"""
    npv = r.get("npv")
    npv_note = (
        "未知"
        if npv is None
        else "%s（%s）"
        % (npv, "0-1 尚早" if npv < 2 else "2 中段" if npv == 2 else "3+ 后段")
    )
    tbe = r.get("tbe")
    type_note = TYPE_NOTE.get(r["type"], "")
    if r["type"] == "1卖":
        type_note += "(tbe=%s)" % (
            "True 趋势背驰确认"
            if tbe is True
            else "False 盘整背驰"
            if tbe is False
            else "未知"
        )
    lines = [
        "代码: %s %s" % (r.get("code"), r.get("name") or ""),
        "卖点类型: %s · %s" % (r["type"], type_note),
        "信号日: %s（检出 %s，确认滞后 %s 日）"
        % (
            r.get("dt"),
            r.get("detect_date") or "—",
            r.get("lag") if r.get("lag") is not None else "—",
        ),
        "价格: 信号收盘 %s / 可成交参考 %s" % (r.get("price"), r.get("entry_px")),
        "结构: 已完成中枢数 %s" % npv_note,
        "大盘环境: %s" % _fmt_reg(r.get("reg")),
        # 按 agent 侧重提示（信息层，不改变事实）
        "研判侧重: %s 派关注 %s" % (agent["method"], agent["focus"]),
    ]
    tr = r.get("tradable")
    lines.append(
        "可成交: %s"
        % ("True" if tr else "False（检出日涨停不可成交）" if tr is False else "未知")
    )
    if paper_row:
        pnl = paper_row.get("pnl")
        lines.append(
            "模拟持仓: 持有中 · 模拟入场 %s · 已持 %s 日 · 浮动 %s"
            % (
                paper_row.get("entry_px"),
                paper_row.get("days_held"),
                ("%+.1f%%" % (pnl * 100)) if isinstance(pnl, (int, float)) else "—",
            )
        )
    else:
        lines.append("模拟持仓: 无（仅观察）")
    return "\n".join(lines)


# ── 研判：Ollama 一卡三行（专辑台独立；同源信号多 agent 并行） ──
def _llm(prompt):
    body = {
        "model": MODEL,
        "stream": False,
        "think": False,
        # think:False 对齐 news_ai 范式——qwen3.5 思考模型不关思考时
        # token 全进 thinking，content 恒空（077h 实测坑）
        "messages": [{"role": "user", "content": prompt}],
        "options": {"temperature": 0},
    }
    req = urllib.request.Request(
        OLLAMA,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with safe_urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read()).get("message", {}).get("content", "")


def _parse(out, actions=None):
    """三行标签解析 + 白名单动词校验。返回 (强度, 动作, 理由, 风险, ok)。
    actions 传入 agent 白名单（缺省用全集 ACTIONS）。"""
    actions = actions or ACTIONS
    strength = action = risk = ""
    for ln in (out or "").splitlines():
        s = ln.strip()
        if s.startswith("强度:"):
            strength = s[3:].strip()
        elif s.startswith("建议:"):
            action = s[3:].strip()
        elif s.startswith("风险:"):
            risk = s[3:].strip()
    found = [a for a in actions if a in action]
    if strength not in ("强", "中", "弱"):
        strength = "中"
    if found:
        # 动作取最早出现的白名单词，理由保留整句
        first = min(found, key=action.find)
        return strength, first, action, risk, True
    return strength, "", action, risk, False


def make_card(r, paper_row, agent):
    """单卡研判（某 agent 视角）：组装 → LLM → 校验（缺动词重试 1 次）→ 卡片 dict。
    卡片带 agent_name/method/focus/timing，供锦标赛对照。"""
    facts = _facts(r, paper_row, agent)
    prompt = _agent_prompt(agent).format(
        facts=facts, actions="/".join(agent["actions"])
    )
    t0 = time.time()
    try:
        out = _llm(prompt)
    except Exception as e:
        raise RuntimeError("LLM_FAIL: %s" % str(e)[:120])
    gen_ms = int((time.time() - t0) * 1000)
    strength, action, reason, risk, ok = _parse(out, agent["actions"])
    if not ok:
        try:  # 重试一次（缺白名单动词）；仍失败保留首轮解析降级
            out = _llm(
                prompt + "\n注意：必须以「建议: <动作>。」开头，动作从白名单选。"
            )
            gen_ms += int((time.time() - t0) * 1000)
            strength, action, reason, risk, ok = _parse(out, agent["actions"])
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/sellpoint_ai.py", exc)
    card = {
        "market": r.get("market", "CN"),
        "code": r.get("code"),
        "name": r.get("name", ""),
        "type": r["type"],
        "tbe": r.get("tbe"),
        "dt": r.get("dt"),
        "detect_date": r.get("detect_date"),
        "lag": r.get("lag"),
        "price": r.get("price"),
        "entry_px": r.get("entry_px"),
        "npv": r.get("npv"),
        "reg": r.get("reg"),
        "trend": r.get("trend"),
        "tradable": r.get("tradable"),
        "held": bool(paper_row),
        "paper_pnl": (paper_row or {}).get("pnl"),
        "paper_days": (paper_row or {}).get("days_held"),
        # 多 agent 维度（schema 扩展，向上兼容历史 67 卡缺省 legacy_cons）
        "agent_name": agent["name"],
        "method": agent["method"],
        "focus": agent["focus"],
        "timing": agent["timing"],
        "strength": strength,
        "action": action or "观望",
        "reason": reason,
        "risk": risk,
        "parse_ok": ok,
        "model": MODEL,
        "gen_ms": gen_ms,
        "source": "sellpoint_ai",
        "ts": _now_iso(),
    }
    if not ok:
        card["raw"] = (out or "")[:400]  # 降级留存原文（research_agent 惯例）
    return card


# ── 落盘 / 幂等（key 含 agent_name：(agent, code, type, dt)） ──
def _card_key(c):
    return [
        c.get("agent_name") or "legacy_cons",
        c.get("code"),
        c.get("type"),
        c.get("dt"),
    ]


def append_cards(new_cards):
    rows = _load_jsonl(CARDS)
    have = {(_k(r)) for r in rows}
    added = 0
    for c in new_cards:
        k = _k(c)
        if k in have:
            continue
        rows.append(c)
        have.add(k)
        added += 1
    if added:
        _atomic_jsonl(CARDS, rows)
    return added


def mark_seen(cards):
    seen = _load_json(SEEN, [])
    s = {tuple(x) for x in seen}
    for c in cards:
        s.add(
            (
                c.get("agent_name") or "legacy_cons",
                c.get("code"),
                c.get("type"),
                c.get("dt"),
            )
        )
    _atomic_json(SEEN, sorted([list(x) for x in s]))


def _k(c):
    """状态字段缺失时兜底 agent 名（历史行 legacy_cons）。"""
    return (
        c.get("agent_name") or "legacy_cons",
        c.get("code"),
        c.get("type"),
        c.get("dt"),
    )


# ── 推送（影子默认关；config sellpoint.push 才发） ────────
def _pushed_keys():
    return {tuple(x) for x in _load_json(PUSHED, [])}


def push_cards(cards):
    cfg = _cfg()
    if not (cfg.get("sellpoint") or {}).get("push"):
        print("sellpoint push: disabled by config (sellpoint.push)")
        return 0
    if not (cfg.get("feishu") or {}).get("push", True):
        print("sellpoint push: master feishu.push off")
        return 0
    if not os.path.isdir(NEWS_DIR):
        print("sellpoint push: news_collector missing")
        return 0
    pushed = _pushed_keys()
    fresh = [
        c
        for c in cards
        if c.get("parse_ok") and (c.get("code"), c.get("dt")) not in pushed
    ]
    if not fresh:
        return 0
    lines = ["🎯 SellPoint 研判卡 · %d 条（%s）" % (len(fresh), _today()), ""]
    for c in fresh:
        lines.append(
            "· %s %s %s%s"
            % (
                c.get("code"),
                c.get("name") or "—",
                c.get("type"),
                "(tbe)" if c.get("tbe") is True else "",
            )
        )
        lines.append(
            "  强度 %s ｜ 建议 %s ｜ %s"
            % (c.get("strength"), c.get("action"), (c.get("risk") or "")[:40])
        )
    portal = (cfg.get("report") or {}).get("portal_url") or ""
    lines.append("")
    lines.append(DISCLAIMER)
    if portal:
        lines.append("详情：%ssellpoint" % portal)
    tmp = os.path.join(NEWS_DIR, "outbox", "pending_sellpoint_ai.txt")
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    with io.open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    try:
        r = subprocess.run(
            [sys.executable, os.path.join(NEWS_DIR, "send_feishu.py"), tmp],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        if (r.stdout or "").strip().startswith("SEND_OK"):
            for c in fresh:
                pushed.add((c.get("code"), c.get("dt")))
            _atomic_json(PUSHED, sorted([list(x) for x in pushed]))
            print("sellpoint push: PUSHED %d cards" % len(fresh))
            return len(fresh)
    except Exception as e:
        print("sellpoint push error: %s" % str(e)[:120])
    print("sellpoint push: SEND_FAILED")
    return 0


# ── 主流程 ───────────────────────────────────────────────
def run_once(limit=MAX_CARDS):
    cfg = _cfg()
    include_all = bool((cfg.get("sellpoint") or {}).get("include_all_types"))
    agents = {a: _agent(a) for a in _active_agents()}
    cands = candidates(limit=limit, include_all=include_all)
    if not cands:
        print("sellpoint_ai: no fresh candidates")
        return []
    print(
        "sellpoint_ai: %d signals, agents=%s (limit=%d, include_all=%s)"
        % (len(cands), list(agents), limit, include_all)
    )
    paper = _paper_index()
    ok_cards, fails = [], 0
    total = sum(len(r["_pending_agents"]) for r in cands)
    idx = 0
    for r in cands:
        for an in r["_pending_agents"]:
            idx += 1
            agent = agents[an]
            print(
                "  [%d/%d] %s %s %s  ⇒ %s(%s) tbe=%s held=%s ..."
                % (
                    idx,
                    total,
                    r.get("code"),
                    r.get("name"),
                    r["type"],
                    an,
                    agent["method"],
                    r.get("tbe"),
                    r.get("_held"),
                )
            )
            try:
                card = make_card(r, paper.get(r.get("code")), agent)
                ok_cards.append(card)
                print(
                    "      强度=%s 建议=%s (%.1fs)"
                    % (card["strength"], card["action"], card["gen_ms"] / 1000)
                )
            except RuntimeError as e:
                fails += 1
                print("      SKIP %s" % e)
                if fails >= LLM_FAIL_ABORT:
                    print("sellpoint_ai: %d consecutive LLM fails, abort run" % fails)
                    return ok_cards
    if ok_cards:
        n = append_cards(ok_cards)
        mark_seen(ok_cards)  # 失败行不记 seen → 次日自动重试
        print("sellpoint_ai: %d cards appended (file +%d)" % (len(ok_cards), n))
        push_cards(ok_cards)
    return ok_cards


def status():
    cards = _load_jsonl(CARDS)
    if not cards:
        print("sellpoint_cards: 0（先跑 python sellpoint_ai.py --once）")
        return
    from collections import Counter

    by_agent = Counter(c.get("agent_name") or "legacy_cons" for c in cards)
    print("cards=%d  by_agent=%s" % (len(cards), dict(by_agent)))
    print(
        "       by_type=%s  by_strength=%s"
        % (
            dict(Counter(c.get("type") for c in cards)),
            dict(Counter(c.get("strength") for c in cards)),
        )
    )
    print(
        "held=%d  parse_ok=%d  last=%s"
        % (
            sum(1 for c in cards if c.get("held")),
            sum(1 for c in cards if c.get("parse_ok")),
            cards[-1].get("ts"),
        )
    )
    for c in cards[-6:]:
        print(
            "  %s %s %s[%s] %s%s → %s/%s  %s"
            % (
                c.get("code"),
                c.get("name"),
                c.get("type"),
                c.get("agent_name") or "legacy_cons",
                c.get("dt"),
                "(tbe)" if c.get("tbe") is True else "",
                c.get("strength"),
                c.get("action"),
                c.get("ts"),
            )
        )


def main():
    ap = argparse.ArgumentParser(description="SellPoint AI 卖点研判（影子）")
    ap.add_argument("--once", action="store_true", help="研判 fresh 卖出信号")
    ap.add_argument("--limit", type=int, default=MAX_CARDS)
    ap.add_argument("--status", action="store_true", help="只看统计")
    a = ap.parse_args()
    if a.status:
        status()
    else:
        run_once(limit=max(1, a.limit))


if __name__ == "__main__":
    main()
