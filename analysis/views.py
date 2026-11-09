# -*- coding: utf-8 -*-
"""S4 AI 研判接入（可选）：stock_analyze / /api/research / legion，并做事实校验。

默认只跑前两路（快）；legion 需要 --with-legion。
"""
import json
import re
import sys

from . import config
# 回环禁代理（2026-11-07 批 5）：本机服务的请求不走系统代理，实现见 tools/safe_urlopen.py
import sys as _sys
from pathlib import Path as _Path

for _base in _Path(__file__).resolve().parents:
    for _cand in (_base, _base / "tools"):
        if (_cand / "safe_urlopen.py").exists():
            _sys.path.insert(0, str(_cand))
            break
    else:
        continue
    break
try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包/仓库根导入时
    from tools.safe_urlopen import safe_urlopen

SEMI_WORDS = re.compile(r"半导体|芯片|晶圆|封测")


def _research(code):
    try:
        with safe_urlopen("http://127.0.0.1:8091/api/research?c=%s&force=1" % code, timeout=180) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"error": str(e)[:150]}


def _stock_analyze(code):
    try:
        sys.path.insert(0, config.MAIN_ROOT)
        sys.path.insert(0, config.MAIN_ROOT + r"\news_collector")
        import stock_analyze as SA
        d = SA.analyze_detail(code.split(".")[0])
        return d
    except Exception as e:
        return {"error": str(e)[:150]}


def _legion(code):
    try:
        sys.path.insert(0, config.MAIN_ROOT + r"\tools")
        import legion_debate as L
        L.MODEL_CHAIN[0] = "qwen3.5:9b"
        r = L.run(code.split(".")[0], save=False)
        return {"funnel": r.get("funnel"), "acts": r.get("acts")}
    except Exception as e:
        return {"error": str(e)[:150]}


def fact_check(views, snap, events):
    """LLM 事实校验：行业错配、信号滞后、事件真空误判。"""
    issues = []
    industry = (snap.get("industry") or {}).get("l1_name") or snap.get("basic", {}).get("industry") or ""
    for ch in views.get("channels", []):
        text = ch.get("output") or ""
        if SEMI_WORDS.search(text) and industry and not SEMI_WORDS.search(industry) and "半导体" not in industry:
            issues.append("%s 提到半导体，但公司行业为 %s（疑似幻觉）" % (ch["channel"], industry))
        if ch["channel"] == "research":
            facts = (views.get("research_facts") or {})
            pool = facts.get("pool") or []
            if pool:
                dt = str(pool[0].get("dt") or "")[:10]
                if dt and dt < snap["as_of"]:
                    issues.append("research 依据的结构信号日期为 %s，早于当前 %s" % (dt, snap["as_of"]))
            if facts.get("events_n") == 0 and events:
                issues.append("research 称事件面真空，但事件库有 %d 条近期事件" % len(events))
    return issues


def run_views(code, snap, events, with_legion=False):
    views = {"channels": [], "issues": [], "ran_at": None}
    sa = _stock_analyze(code)
    if "error" not in sa:
        views["channels"].append({"channel": "stock_analyze", "model": "qwen3.5:9b",
                                  "output": (sa.get("outlook") or "") + " | bias=%s strength=%s px=%s" % (
                                      sa.get("bias"), sa.get("strength"), sa.get("px")),
                                  "bias": {"看多": "看多", "偏多": "看多"}.get(sa.get("bias"), "中性"),
                                  "is_model_generated": True, "raw": sa})
    else:
        views["issues"].append("stock_analyze 失败：" + sa["error"])
    rs = _research(code)
    if "error" not in rs:
        views["channels"].append({"channel": "research", "model": rs.get("model", "llm"),
                                  "output": rs.get("text") or "", "bias": "看空" if "卖出" in (rs.get("text") or "") else "中性",
                                  "is_model_generated": True})
        views["research_facts"] = rs.get("facts") or {}
    else:
        views["issues"].append("research 失败：" + rs["error"])
    if with_legion:
        lg = _legion(code)
        if "error" not in lg:
            acts = lg.get("acts") or {}
            views["channels"].append({"channel": "legion", "model": "qwen3.5:9b",
                                      "output": "D1: %s" % str(acts.get("D1", ""))[:400],
                                      "bias": "中性", "is_model_generated": True})
        else:
            views["issues"].append("legion 失败：" + lg["error"])
    views["issues"] += fact_check(views, snap, events)
    return views
