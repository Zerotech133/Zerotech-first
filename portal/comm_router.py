# -*- coding: utf-8 -*-
"""阚泽部 · 通信入口智能路由核心（K1.1/K1.2/K1.3，WO-FS-148-2/3/4）。

一体口径（老田 12:31 定调）：飞书功能 × 智能功能 = 一个功能面。
本模块是消息处理链里的智能段，四步串联：

    意图识别（正则前置 → LLM 兜底）
      → 域路由（八意图 → 席位/直答/引导）
      → 模型调度（cost_aware 三档：fast / deep / cloud / template）
      → 留痕（jsonl，只存统计不存原文）

设计纪律
--------
1. **正则前置**：能正则判定的不过 LLM（成本与延迟纪律，判例同
   inbound_gateway.classify）。只有正则覆盖不到的才调模型。
2. **底座复用**：模型一律走 agent_platform/engine/llm.py 的
   make_llm(binding)（MODEL_PROFILES 自动档位 + 降级三态），
   本模块不 import 任何厂商 SDK，不建第二调用层。
3. **降级三态**：LLM 不可用 / 超时 / 返回非法 → 回落 unclear（澄清卡），
   链路不断、不臆造意图。
4. **留痕不存原文**：只落 intent/confidence/entities 摘要/text_len/
   text_md5/trace_id —— 与 customer_interest 隐私红线同口径
   （画像层不存聊天原文）。

补充决策（设计未覆盖处，理由随行标注）
------------------------------------
- 意图层超时默认 12s（COMM_INTENT_TIMEOUT 可覆盖）。设计稿写"3s 超时档"，
  但实测 9b 冷加载 34s（llm.py:70 注释同判例），3s 会让冷态永远降级、
  等于意图层形同虚设；12s 覆盖 warm 快路径，冷态如实降级并在留痕标
  degraded，不假装成功。
- 6 位代码 + 盯/提醒词同时出现 → 判 watch_setup 而非 stock_consult
  （"帮我盯一下 300750" 的动作是建触发器，不是查行情）。
- billing 与 subscription_qa 分开：前者涉资金动作（扣款/发票/退款），
  必须走人工兜底不自动答；后者是查账本直答。

对外接口
--------
    route_message(text, llm=None, log=True) -> dict
    classify_intent(text, llm) -> dict      # K1.1
    decide_route(intent) -> dict            # K1.2
    cost_aware_route(intent, confidence)    # K1.3
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from pathlib import Path

# ── 八意图闭集（与 v2 方案 §2.1 一致，新增意图必须同时补 ROUTE_TABLE）──
INTENTS = (
    "stock_consult",  # 个股咨询
    "watch_setup",  # 盯票/预警设置
    "subscription_qa",  # 我的订阅/额度（查账本直答）
    "billing",  # 账单/发票/退款（涉资金，人工兜底）
    "complaint",  # 投诉（永远受理，最高优先级）
    "feed",  # 长文/链接投喂
    "smalltalk",  # 闲聊
    "unclear",  # 判不出来 → 澄清卡
)

FAST_MODEL = "ollama/qwen3.5:9b"
DEEP_MODEL = "ollama/qwen3.6:35b-a3b"
CLOUD_MODEL = "glm/glm-4.5-flash"
TEMPLATE_TIER = "template"

# 置信度低于此值 → 升云档（歧义需要更强模型裁决）
CLOUD_CONF_THRESHOLD = 0.7
# 意图分类置信度低于此值 → 直接判 unclear（宁澄清不瞎猜）
UNCLEAR_CONF_THRESHOLD = 0.5

# 正则命中时的置信度（规则判定确定性高但非绝对，不虚报 1.0）
REGEX_CONFIDENCE = 0.9

TEXT_FEED_MIN = 30  # 与 inbound_gateway.TEXT_FEED_MIN 同口径

_CODE_RE = re.compile(r"\b(\d{6})\b")
_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)

_COMPLAINT_RE = re.compile(
    r"(投诉|失望|没人回|没人理|差评|退钱|骗|垃圾|太差|什么破|态度)"
)
_WATCH_RE = re.compile(r"(盯|提醒|预警|监控)")
_STOCK_RE = re.compile(
    r"(买|卖|能进|能上|走势|分析|看看|看一下|怎么样|支撑|压力|止盈|止损|仓位)"
)
_SUBSCRIPTION_RE = re.compile(r"(订阅|额度|剩几天|还剩|剩余|套餐|到期|档位)")
_BILLING_RE = re.compile(r"(账单|发票|退款|支付|扣款|续费|付款|对账|多少钱)")

# ── 域路由表（v2 方案 §2.2）：target=接活的席位；gate=订阅闸口径；
#    priority 越小越优先（投诉=1 永远最前）；action=回执形态
ROUTE_TABLE = {
    "stock_consult": {
        "target": "simayi",
        "domain": "quant",
        "gate": "subscription",
        "action": "dispatch_result_card",
        "priority": 3,
        "desc": "个股咨询 → 司马懿席（盯盘官转派）",
    },
    "watch_setup": {
        "target": "xuchu_g1",
        "domain": "quant",
        "gate": "subscription",
        "action": "dispatch_confirm_card",
        "priority": 3,
        "desc": "盯票设置 → 许褚营 G1 触发器",
    },
    "subscription_qa": {
        "target": "self",
        "domain": "business",
        "gate": "no_gate",
        "action": "summary_card",
        "priority": 2,
        "desc": "订阅/额度查询 → 中台查账本直答（零 Agent）",
    },
    "billing": {
        "target": "self_human",
        "domain": "business",
        "gate": "no_gate",
        "action": "human_fallback_card",
        "priority": 2,
        "desc": "账单/发票/退款 → 直答摘要 + 人工兜底（涉资金不自动答）",
    },
    "complaint": {
        "target": "tianfeng",
        "domain": "governance",
        "gate": "no_gate",
        "action": "accept_card_with_ticket",
        "priority": 1,
        "desc": "投诉 → 田丰归口（最高优先级，永不受闸限）",
    },
    "feed": {
        "target": "knowledge",
        "domain": "knowledge",
        "gate": "no_gate",
        "action": "ingested_card",
        "priority": 5,
        "desc": "长文/链接 → 知识库既有链路",
    },
    "smalltalk": {
        "target": "clarify",
        "domain": "comm",
        "gate": "no_gate",
        "action": "clarify_card",
        "priority": 4,
        "desc": "闲聊 → 澄清卡（带建议指令）",
    },
    "unclear": {
        "target": "clarify",
        "domain": "comm",
        "gate": "no_gate",
        "action": "clarify_card",
        "priority": 4,
        "desc": "判不出来 → 澄清卡（不猜）",
    },
}

# 意图分类 prompt（约束模型只输出 JSON，不给自由发挥空间）
_CLASSIFY_SYSTEM = (
    "你是飞书客户入口的意图分类器。只输出一个 JSON 对象，不要任何额外文字。\n"
    "字段：intent（八选一：stock_consult, watch_setup, subscription_qa, "
    "billing, complaint, feed, smalltalk, unclear）、confidence（0~1 浮点）、"
    "entities（对象，可含 code=6位股票代码 / topic=主题词，无则空对象）。\n"
    "判定口径：\n"
    "- stock_consult：问某只票能不能买/卖/走势/分析\n"
    "- watch_setup：要设盯票、提醒、预警\n"
    "- subscription_qa：问自己的订阅、剩余天数、额度用量\n"
    "- billing：账单、发票、退款、扣款、续费等涉及钱的问题\n"
    "- complaint：表达不满、投诉、指责服务\n"
    "- feed：投喂长文、文章、链接\n"
    "- smalltalk：寒暄、闲聊、试探是不是真人\n"
    "- unclear：看不懂或信息不足\n"
    "拿不准就降低 confidence，不要硬猜。"
)


# ── 内部：默认 LLM 获取（延迟导入，失败返回 (None, 原因)）────────────
def _default_llm():
    """取 engine/llm.py 的 OllamaLLM（复用底座，不新建调用层）。

    失败原因原样返回给调用方进留痕，不静默（禁 except: pass 吞异常）。
    """
    try:
        root = Path(__file__).resolve().parent.parent
        ap = root / "agent_platform"
        if str(ap) not in os.sys.path:
            os.sys.path.insert(0, str(ap))
        from engine.llm import OllamaLLM  # noqa: E402

        timeout = float(os.environ.get("COMM_INTENT_TIMEOUT", "12"))
        return OllamaLLM(timeout=timeout), None
    except Exception as ex:  # noqa: BLE001
        return None, "%s: %s" % (type(ex).__name__, ex)


# ── K1.1 正则前置 ────────────────────────────────
def _regex_classify(text):
    """命中返回 (intent, entities)，未覆盖返回 None（交 LLM）。"""
    t = (text or "").strip()
    if not t:
        return ("unclear", {})
    code_m = _CODE_RE.search(t)
    code = code_m.group(1) if code_m else None

    if _COMPLAINT_RE.search(t):
        return ("complaint", {})
    # 盯票优先于个股咨询：动作是"设触发器"不是"查行情"
    if code and _WATCH_RE.search(t):
        return ("watch_setup", {"code": code})
    if code and _STOCK_RE.search(t):
        return ("stock_consult", {"code": code})
    if _BILLING_RE.search(t):
        return ("billing", {})
    if _SUBSCRIPTION_RE.search(t):
        return ("subscription_qa", {})
    if _URL_RE.search(t) or len(t) >= TEXT_FEED_MIN:
        return ("feed", {"len": len(t)})
    return None  # 正则覆盖不到 → LLM


def _parse_llm_json(raw):
    """容错解析：剥 ```json 围栏 → json.loads → 校验 intent 闭集。"""
    s = (raw or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s).strip()
    # 模型偶尔带前后缀说明文字：截首个 { 到末个 }
    l, r = s.find("{"), s.rfind("}")
    if l >= 0 and r > l:
        s = s[l : r + 1]
    obj = json.loads(s)
    if not isinstance(obj, dict):
        raise ValueError("llm output not object")
    intent = str(obj.get("intent") or "").strip()
    if intent not in INTENTS:
        raise ValueError("illegal intent: %s" % intent)
    conf = obj.get("confidence", 0)
    try:
        conf = float(conf)
    except (TypeError, ValueError):
        raise ValueError("bad confidence: %r" % (conf,)) from None
    conf = max(0.0, min(1.0, conf))
    ents = obj.get("entities") or {}
    if not isinstance(ents, dict):
        ents = {}
    return intent, conf, ents


# ── K1.1 意图识别总入口 ───────────────────────────
def classify_intent(text, llm=None, status_sink=None):
    """→ {intent, confidence, entities, channel, llm_ok, error}

    channel 三态：regex（正则命中）/ llm（模型判定）/ fallback（模型不可用）。
    """
    hit = _regex_classify(text)
    if hit is not None:
        intent, ents = hit
        return {
            "intent": intent,
            "confidence": REGEX_CONFIDENCE,
            "entities": ents,
            "channel": "regex",
            "llm_ok": False,
            "error": None,
        }

    if llm is None:
        llm, err = _default_llm()
        if llm is None:
            return {
                "intent": "unclear",
                "confidence": 0.0,
                "entities": {},
                "channel": "fallback",
                "llm_ok": False,
                "error": "llm_unavailable: %s" % err,
            }
    try:
        raw, status = llm.chat(
            _CLASSIFY_SYSTEM, (text or "")[:2000], model="qwen3.5:9b"
        )
        if status_sink is not None:
            status_sink.append(status)
        if status != "normal":
            # degraded 也接受（本地兜底已产出合理文本），但如实标记
            intent, conf, ents = _parse_llm_json(raw)
            return {
                "intent": intent,
                "confidence": conf,
                "entities": ents,
                "channel": "llm",
                "llm_ok": False,
                "error": "llm_status=%s" % status,
            }
        intent, conf, ents = _parse_llm_json(raw)
    except Exception as ex:  # noqa: BLE001
        return {
            "intent": "unclear",
            "confidence": 0.0,
            "entities": {},
            "channel": "fallback",
            "llm_ok": False,
            "error": "%s: %s" % (type(ex).__name__, str(ex)[:120]),
        }

    if conf < UNCLEAR_CONF_THRESHOLD:
        # 宁澄清不瞎猜：保留原判在 entities_hint 供排障
        return {
            "intent": "unclear",
            "confidence": conf,
            "entities": ents,
            "channel": "llm",
            "llm_ok": True,
            "error": None,
            "hint": intent,
        }
    return {
        "intent": intent,
        "confidence": conf,
        "entities": ents,
        "channel": "llm",
        "llm_ok": True,
        "error": None,
    }


# ── K1.2 域路由 ──────────────────────────────────
def decide_route(intent):
    """意图 → 路由决策（深拷贝防调用方改表）。"""
    row = ROUTE_TABLE.get(intent) or ROUTE_TABLE["unclear"]
    return dict(row)


# ── K1.3 模型调度（cost_aware 三档 + 模板档）────────
def cost_aware_route(intent, confidence):
    """→ {tier, binding, reason, degraded_chain}

    tier 四值：fast（本地 9b）/ deep（本地 35b 研究类）/ cloud（云端 GLM
    歧义裁决）/ template（模板生成，不经模型——投诉与涉资金场景）。
    """
    if intent == "complaint":
        return {
            "tier": TEMPLATE_TIER,
            "binding": "template://complaint",
            "reason": "投诉走模板+人工，模型不得代答",
            "degraded_chain": ["template"],
        }
    if intent == "billing":
        return {
            "tier": TEMPLATE_TIER,
            "binding": "template://billing",
            "reason": "涉资金口径走模板+人工兜底，不自动答",
            "degraded_chain": ["template"],
        }
    if confidence < CLOUD_CONF_THRESHOLD:
        return {
            "tier": "cloud",
            "binding": CLOUD_MODEL,
            "reason": "置信度 %.2f < %.1f，升云裁决"
            % (confidence, CLOUD_CONF_THRESHOLD),
            "degraded_chain": ["glm", FAST_MODEL, "template"],
        }
    if intent == "stock_consult":
        return {
            "tier": "deep",
            "binding": DEEP_MODEL,
            "reason": "研究推理类，本地 35b 深答",
            "degraded_chain": [DEEP_MODEL, FAST_MODEL, "template"],
        }
    return {
        "tier": "fast",
        "binding": FAST_MODEL,
        "reason": "常规意图，本地 9b 零成本",
        "degraded_chain": [FAST_MODEL, "template"],
    }


# ── 留痕（不存原文）───────────────────────────────
def _log_path():
    p = os.environ.get("COMM_ROUTE_LOG")
    if p:
        return Path(p)
    return Path(__file__).resolve().parent / "work" / "comm_route_log.jsonl"


def _write_trace(entry):
    """→ (ok, error)。写失败如实返回，不阻断路由。"""
    p = _log_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return True, None
    except OSError as ex:
        return False, "%s: %s" % (type(ex).__name__, ex)


def _md5(s):
    return hashlib.md5((s or "").encode("utf-8", "replace"), usedforsecurity=False).hexdigest()


# ── 总入口：一条消息走完全链 ──────────────────────
def route_message(text, llm=None, log=True):
    """意图 → 路由 → 调度 → 留痕。返回决策 dict（附 trace_id）。

    任何一环降级都如实反映在 channel/llm_ok/error/degraded 字段，
    不伪造成功（禁假绿判例）。
    """
    t0 = time.time()
    trace_id = uuid.uuid4().hex[:16]
    status_sink = []
    cl = classify_intent(text, llm=llm, status_sink=status_sink)
    rt = decide_route(cl["intent"])
    md = cost_aware_route(cl["intent"], cl["confidence"])
    elapsed_ms = int((time.time() - t0) * 1000)

    result = {
        "trace_id": trace_id,
        "intent": cl["intent"],
        "confidence": round(cl["confidence"], 3),
        "entities": cl["entities"],
        "channel": cl["channel"],
        "llm_ok": cl["llm_ok"],
        "error": cl["error"],
        "route": rt,
        "model": md,
        "elapsed_ms": elapsed_ms,
    }
    if cl.get("hint"):
        result["hint"] = cl["hint"]

    if log:
        entry = {
            "trace_id": trace_id,
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "intent": result["intent"],
            "confidence": result["confidence"],
            "channel": cl["channel"],
            "llm_ok": cl["llm_ok"],
            "llm_status": status_sink[-1] if status_sink else None,
            "error": cl["error"],
            "route_target": rt["target"],
            "model_tier": md["tier"],
            "text_len": len(text or ""),
            "text_md5": _md5(text),  # 只存指纹，不存原文
            "elapsed_ms": elapsed_ms,
        }
        ok, err = _write_trace(entry)
        result["logged"] = ok
        if not ok:
            result["log_error"] = err  # 留痕失败如实上报，不静默
    return result
