# -*- coding: utf-8 -*-
"""K-PUSH-011 盘后兜底批：扫描 tune_store 所有待对比单（stage∈verified/pending_manual、
intent 有规则模板、非 running），串行跑自动回测对比。

触发：计划任务「AI工作站调优回测批次」每日 20:00（schedule.json tune_bt 联动）。
幂等：draft.bt2_result.status in (running, done) 跳过；失败单（inconclusive）次日
批次重跑兜底。
"""

import os
import sys
import json
import glob

_HERE = os.path.dirname(os.path.abspath(__file__))
_STATION = os.path.dirname(_HERE)  # 站根 c:\项目\老田的AI助手
_PORTAL = os.path.join(_STATION, "portal")
for _p in (_STATION, _PORTAL, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import feedback_flow
import tune_gate
from quant_backtest import bt2_tune_compare as _btc

STORE = os.path.join(_PORTAL, "work", "tune_store")
TEMPLATE_INTENTS = _btc.TUNE_BT_INTENTS


def _candidates():
    out = []
    try:
        vip = tune_gate.vip_overrides()
    except Exception:
        vip = {}
    view = feedback_flow.requests_view()
    for p in glob.glob(os.path.join(STORE, "*.json")):
        try:
            with open(p, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except Exception:
            continue
        rid = os.path.splitext(os.path.basename(p))[0]
        if doc.get("intent") not in TEMPLATE_INTENTS:
            continue
        st = (view.get(rid) or {}).get("stage")
        if st not in ("verified", "pending_manual"):
            continue
        bt2r = (doc.get("draft") or {}).get("bt2_result") or {}
        if bt2r.get("status") == "done":
            continue
        if bt2r.get("status") == "running":
            # K-PUSH-011 兜底：subprocess 意外死亡会留下永久 running（deploy 被卡），
            # 超过 30 分钟未完成的 running 视为 stale，批次重跑接管
            started = bt2r.get("started_at") or ""
            try:
                from datetime import datetime

                stale = (
                    datetime.now() - datetime.fromisoformat(started)
                ).total_seconds() > 1800
            except Exception:
                stale = True
            if not stale:
                continue
        out.append((rid, doc))
    return out, vip


def main():
    cands, vip = _candidates()
    print("candidates: %d" % len(cands), flush=True)
    for rid, doc in cands:
        r = _btc.run_compare(rid, doc, vip)
        print(
            "  %s -> %s (%s, %ss)"
            % (rid, r.get("verdict"), r.get("reason", ""), r.get("elapsed", "?")),
            flush=True,
        )
        # 回写 store + 账本（与 _auto_compare 同语义）
        from datetime import datetime

        doc2 = tune_gate._load_store(rid) or {}
        draft = doc2.setdefault("draft", {})
        draft["bt2_result"] = dict(
            r, status="done", finished_at=datetime.now().isoformat(timespec="seconds")
        )
        tune_gate._save_store(doc2)
        intent = doc2.get("intent", "opinion")
        scope = doc2.get("scope", "private")
        note = "批次自动回测%s：%s（%ss）" % (
            r.get("verdict", "?"),
            r.get("reason", ""),
            r.get("elapsed", "?"),
        )
        if r.get("verdict") == "pass":
            feedback_flow.record(
                rid, "pending_manual", note=note[:200], intent=intent, scope=scope
            )
        elif r.get("verdict") == "fail":
            feedback_flow.record(
                rid,
                "rejected",
                operator="auto-bt2-batch",
                note=note[:200],
                intent=intent,
                scope=scope,
            )
        else:
            feedback_flow.record(
                rid,
                "pending_manual",
                note=(note + "（可重跑）")[:200],
                intent=intent,
                scope=scope,
            )
    print("batch done", flush=True)


if __name__ == "__main__":
    main()
