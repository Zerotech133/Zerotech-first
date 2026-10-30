# -*- coding: utf-8 -*-
"""研报 AI 语义结构化：本地 Ollama(qwen3:8b-8k) 抽取结构化 JSON + 置信度。

设计（与 kg_notice_llm.py 同范式，JEV 体系融合）：
- report_texts.jsonl 每条 rec 增加 "ai" 字段：
  {event_type, bias(bull/bear/neutral), conf, reason, target_price,
   earnings, eps_forecast[], core_points[], risks[], model, ts}
- 抽取结果同步写 data/semantica/report_events.jsonl（与 events.jsonl
  行格式同构：id/kind/ts/text/source/stocks/bias/conf/reason/tag），
  下游向量/图谱可统一消费。刻意不直接追加 events.jsonl——该文件与
  events_vec.npy 按行对齐，外部增行会错位向量矩阵。
- 幂等：无 ai 字段才抽（--force 全量重抽）；Ollama 探活失败直接跳过；
  连续失败熔断防雪崩。

用法：
  python report_ai.py            # 增量抽取
  python report_ai.py --force    # 全量重抽
  python report_ai.py --sync     # 只重建 report_events.jsonl（不调 LLM）
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from report_fetch import TEXTS_PATH, load_texts  # noqa: E402

SNAP_DIR = os.path.join(HERE, "data", "semantica")
EVT_OUT = os.path.join(SNAP_DIR, "report_events.jsonl")
OLLAMA = "http://127.0.0.1:11434/api/chat"
MODEL = os.environ.get("OLLAMA_MODEL", "qwen3:8b-8k")
TIMEOUT = 120  # 单篇超时（4k 字输入 + JSON 输出较慢）
INPUT_CHARS = 4000  # 送入正文上限（浓缩后观点流，qwen3:8k 覆盖全篇）
FAIL_STREAK_MAX = 6  # 连续失败熔断
EVENT_TYPES = (
    "业绩点评",
    "首次覆盖",
    "行业景气",
    "产能扩张",
    "并购重组",
    "政策利好",
    "技术突破",
    "订单中标",
    "风险提示",
    "点评更新",
)

PROMPT = (
    "你是券商研报结构化助手。通读研报正文，输出严格 JSON"
    "（不要任何其他文字、不要markdown代码块）：\n"
    '{"event_type":"...","bias":"...","conf":0.95,"reason":"...",'
    '"target_price":"...","earnings":"...","eps_forecast":["..."],'
    '"core_points":["..."],"risks":["..."]}\n'
    "字段要求：\n"
    "- event_type 枚举选一：" + " ".join(EVENT_TYPES) + "\n"
    "- bias：研报观点对个股的方向 bull=看多 bear=看空 neutral=neutral\n"
    "- conf：你对整体判断的置信度(0到1的小数)：证据充分给0.9以上，"
    "观点模糊只能给0.6以下\n"
    "- reason：概括这篇研报的具体逻辑，必须反映该股具体业务或数据，"
    "禁止通用套话\n"
    "- target_price：文中给出的目标价数字加单位，没有则空串\n"
    "- earnings：概括业绩数据亮点（必须有具体数字或增速），"
    "无业绩数据则空串\n"
    "- eps_forecast：每条形如「2026E归母净利3.2亿元」，"
    "报告没有盈利预测则给空数组\n"
    "- core_points：2-4条，每条必须是完整句子（主谓齐全，20-45字），"
    "严禁2-6字的短语\n"
    "- risks：1-3条，每条必须是完整句子（15-30字），严禁短语\n"
    "研报正文（可能截断）：\n%s"
)

_ANCHORS = (
    "事件：",
    "投资要点",
    "核心观点",
    "投资建议",
    "业绩点评",
    "公司点评",
    "盈利预测",
)


def _trim_text(text):
    """跳过研报页头噪音（股价表现/相关研究表格），从观点锚点起送入。"""
    t = text or ""
    best = -1
    for a in _ANCHORS:
        i = t.find(a)
        if i != -1 and (best == -1 or i < best):
            best = i
    return t[best:] if best > 0 else t


def digest_text(text):
    """全篇观点浓缩：过滤表格/数字密集行（研报正文 6-7k 字中表格占
    一半以上，观点句浓缩后 ~3-4k 字，一次送入可覆盖全篇）。"""
    t = _trim_text(text)
    out = []
    for p in re.split(r"[\n\r]+", t):
        s = p.strip()
        if not s:
            continue
        han = len(re.findall(r"[\u4e00-\u9fff]", s))
        if len(s) > 4 and (han < 8 or (len(s) > 20 and han / len(s) < 0.35)):
            continue  # 表格行 / 财务数据行
        out.append(s)
    return "\n".join(out)


def ollama_ok():
    """探活：Ollama 服务与模型在位才开跑。"""
    try:
        r = requests.get(OLLAMA.replace("/api/chat", "/api/tags"), timeout=5)
        names = [m.get("name", "") for m in (r.json().get("models") or [])]
        return MODEL in names or any(n.startswith(MODEL.split(":")[0]) for n in names)
    except Exception:
        return False


def _balance_json(s):
    """从模型输出提取首个括号平衡的 JSON 对象（含嵌套数组）。"""
    start = s.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(s)):
            if s[i] == "{":
                depth += 1
            elif s[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(s[start : i + 1])
                    except ValueError:
                        break
        start = s.find("{", start + 1)
    return None


def extract(text, timeout=TIMEOUT):
    """单篇正文 → 结构化 dict 或 None（两次解析机会）。"""
    # num_ctx 统一 16384（2026-10-24 收口）：全仓共用一个 ctx，避免 Ollama 因
    # num_ctx 变化整模型重载（实测 15~46s/次）。原值 8192 属"各调用方各设各的"
    # 历史遗留，iGPU/Vulkan + KV q8_0 后 16384 与 4096 生成吞吐相同。
    body = {
        "model": MODEL,
        "stream": False,
        "think": False,
        "messages": [
            {"role": "user", "content": PROMPT % digest_text(text)[:INPUT_CHARS]}
        ],
        "options": {"temperature": 0.1, "num_predict": 600, "num_ctx": 16384},
    }
    for _ in range(2):
        try:
            r = requests.post(OLLAMA, json=body, timeout=timeout)
            r.raise_for_status()
            d = _balance_json(r.json()["message"]["content"] or "")
            if not d:
                continue
            bias = str(d.get("bias", "")).strip()
            if bias not in ("bull", "bear", "neutral"):
                bias = "neutral"
            et = str(d.get("event_type", "")).strip()
            if et not in EVENT_TYPES:
                et = "点评更新"
            conf = min(1.0, max(0.0, float(d.get("conf", 0) or 0)))

            def strs(k, n, ln):
                """取前 n 条、每条限 ln 字（两个维度独立）。"""
                return [str(x)[:ln] for x in (d.get(k) or []) if str(x).strip()][:n]

            return {
                "event_type": et,
                "bias": bias,
                "conf": round(conf, 2),
                "reason": str(d.get("reason", ""))[:80],
                "target_price": str(d.get("target_price", ""))[:20],
                "earnings": str(d.get("earnings", ""))[:90],
                "eps_forecast": strs("eps_forecast", 4, 40),
                "core_points": strs("core_points", 4, 60),
                "risks": strs("risks", 3, 45),
                "model": MODEL,
                "ts": datetime.now().strftime("%Y-%m-%d %H:%M"),
            }
        except Exception:
            time.sleep(1.5)
    return None


def run_all(force=False):
    """增量抽取：无 ai 字段（或 --force）的记录跑 LLM，写回 JSONL。"""
    texts = load_texts()
    if not texts:
        print("AI抽取: 正文库为空，跳过")
        return 0
    if not ollama_ok():
        print("AI抽取: Ollama/模型不可用，跳过（熔断）")
        return 0
    todo = [u for u, r in texts.items() if force or "ai" not in r or not r["ai"]]
    print("AI抽取: 待处理 %d / %d 篇" % (len(todo), len(texts)))
    done, fails, streak = 0, 0, 0
    for u in todo:
        if streak >= FAIL_STREAK_MAX:
            print("AI抽取: 连续失败 %d 次熔断退出" % streak)
            break
        ai = extract(texts[u].get("text") or "")
        if ai:
            texts[u]["ai"] = ai
            done += 1
            streak = 0
            print(
                "  ✓ %s %s %s conf=%.2f"
                % (
                    (texts[u].get("code") or "?")[:6],
                    ai["event_type"],
                    ai["bias"],
                    ai["conf"],
                )
            )
            with open(TEXTS_PATH, "w", encoding="utf-8") as f:
                for _, rec in texts.items():
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        else:
            fails += 1
            streak += 1
            print("  ✗ %s 解析失败" % (texts[u].get("code") or u)[:40])
        time.sleep(0.3)
    sync_events()
    print("AI抽取: 完成 %d，失败 %d" % (done, fails))
    return done


def _evt_id(url):
    return "rpt:" + hashlib.sha1((url or "").encode("utf-8"), usedforsecurity=False).hexdigest()[:10]


def sync_events():
    """抽取结果 → report_events.jsonl（与 events.jsonl 同构，幂等重建）。"""
    texts = load_texts()
    rows = []
    for u, r in texts.items():
        ai = r.get("ai")
        if not ai:
            continue
        rows.append(
            {
                "id": _evt_id(u),
                "kind": "report",
                "ts": (r.get("pub_date") or r.get("date") or "") + " 08:00",
                "text": ((r.get("title") or "") + "｜" + ai.get("reason", ""))[:200],
                "source": "report:" + (r.get("org") or ""),
                "stocks": [r["code"]] if r.get("code") else [],
                "bias": ai["bias"],
                "conf": ai["conf"],
                "reason": ai.get("reason", ""),
                "tag": ai["event_type"],
            }
        )
    if rows:
        os.makedirs(SNAP_DIR, exist_ok=True)
        with open(EVT_OUT, "w", encoding="utf-8") as f:
            for e in rows:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
    print("事件融合: report_events.jsonl %d 行" % len(rows))
    return len(rows)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="全量重抽")
    ap.add_argument("--sync", action="store_true", help="只重建 report_events.jsonl")
    a = ap.parse_args()
    if a.sync:
        sync_events()
    else:
        run_all(force=a.force)
