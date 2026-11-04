# -*- coding: utf-8 -*-
"""Case 服务端编排 v1：田丰汇总 → 5角色并行 → 会议 → 方案。

刷新不中断：由 8796 后台线程执行，状态写回 Case；页面只轮询。
不自动审批/下发。
"""
from __future__ import annotations

import concurrent.futures as cf
import datetime
import json
import os
import threading
import urllib.request
from pathlib import Path

from legion_command import case_api
from legion_command import hq_meeting

BASE = os.environ.get("ZB_CASE_MODEL_BASE", "http://127.0.0.1:8796")
MEETINGS = Path(os.environ.get("ZB_HQ_MEETINGS_DIR", r"C:\项目\老田的AI助手\agent_platform\data\meetings\hq"))
ROLES = [
    ("A0", "田丰", "目标拆解、任务DAG、派单、汇总"),
    ("AI1", "诸葛亮", "反向质询、pre-mortem、找反例"),
    ("ZHAOYUN", "赵云", "证据复核、审计、归档"),
    ("HUANGZHONG", "黄忠", "验收标准、抽样、终检"),
    ("GUANYU", "关羽", "代码审计、技术债、门禁"),
]
_RUNNING = set()
_LOCK = threading.Lock()


def _now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _chat(agent: str, system: str, user: str, timeout: int = 180) -> dict:
    body = json.dumps({"agent": agent, "system": system, "purpose": "case_autorun",
                       "user": user}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE.rstrip("/") + "/api/chat", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        env = json.loads(r.read().decode("utf-8"))
    if not env.get("ok"):
        raise RuntimeError(str(env.get("error") or "model_failed")[:200])
    text = str((env.get("data") or {}).get("text") or "").strip()
    if not text:
        raise RuntimeError("empty_model_text")
    return {"text": text, "binding": env.get("binding") or "",
            "degraded": bool(env.get("degraded"))}


def _update(case_id: str, case: dict, root: Path | None = None) -> dict:
    try:
        return case_api.save_case(case, root)
    except Exception:
        return case


def autorun(case_id: str, case_root: Path | None = None, meetings: Path | None = None) -> dict:
    """同步执行；正常由 serve.py 放后台线程调用。"""
    with _LOCK:
        if case_id in _RUNNING:
            raise RuntimeError("case_already_running")
        _RUNNING.add(case_id)
    case = case_api.get_case(case_id, case_root)
    if not case:
        with _LOCK:
            _RUNNING.discard(case_id)
        raise ValueError("case_not_found")
    try:
        case["orchestration"] = {"state": "running", "phase": "aggregate", "done": 0,
                                 "total": 5, "started_at": _now(), "error": ""}
        _update(case_id, case, case_root)
        if not case.get("a0_summary"):
            case["orchestration"]["phase"] = "aggregate"
            _update(case_id, case, case_root)
            r = _chat("A0", "你是田丰（A0）。汇总材料：事实/证据缺口/关键矛盾/建议议程；不编造数字，不代替老田拍板。",
                      "议题：" + str(case.get("title") or "") + "\n材料：\n" + str(case.get("materials") or "（无）"))
            case["a0_summary"] = r["text"]
            case["summary"] = r["text"]
            case["status"] = "AGGREGATED"
        case["orchestration"].update({"phase": "review", "done": 0})
        _update(case_id, case, case_root)
        prev = {o.get("seat"): o for o in (case.get("opinions") or []) if o.get("text")}
        out = []

        def one(role):
            seat, name, duty = role
            if seat in prev:
                return prev[seat]
            try:
                r = _chat(seat, "你是" + name + "（" + seat + "），零界五角色之一，职责：" + duty +
                          "。只从职能面读材料给意见；不编造数字，缺证据标需查证。输出结论/证据/风险/建议。",
                          "议题：" + str(case.get("title") or "") + "\n田丰汇总：" + str(case.get("a0_summary") or "")[:3000] +
                          "\n材料：" + str(case.get("materials") or "（无）"))
                return {"seat": seat, "name": name, "text": r["text"], "binding": r["binding"]}
            except Exception as e:  # noqa: BLE001
                return {"seat": seat, "name": name, "error": type(e).__name__ + ":" + str(e)[:160]}

        with cf.ThreadPoolExecutor(max_workers=5) as ex:
            futures = [ex.submit(one, r) for r in ROLES]
            for i, f in enumerate(cf.as_completed(futures), 1):
                out.append(f.result())
                case["orchestration"]["done"] = i
                _update(case_id, case, case_root)
        out.sort(key=lambda x: [r[0] for r in ROLES].index(x["seat"]))
        # 一次重试失败角色
        for r in ROLES:
            seat = r[0]
            existing = next((x for x in out if x["seat"] == seat), None)
            if existing and existing.get("text"):
                continue
            try:
                rr = one(r)
                if rr.get("text"):
                    out = [x for x in out if x["seat"] != seat] + [rr]
            except Exception:
                pass
        out.sort(key=lambda x: [r[0] for r in ROLES].index(x["seat"]))
        case["opinions"] = out
        ok = len([x for x in out if x.get("text")]) == 5
        case["status"] = "REVIEWED" if ok else "AGGREGATED"
        if not ok:
            raise RuntimeError("review_incomplete:%d/5" % len([x for x in out if x.get("text")]))
        # meeting
        case["orchestration"]["phase"] = "meeting"
        _update(case_id, case, case_root)
        payload = {"type": "operation", "attendees": [x[0] for x in ROLES],
                   "agenda": [{"item": "田丰汇总", "owner": "A0", "minutes": 5},
                              {"item": "五角色意见", "owner": "ALL", "minutes": 10},
                              {"item": "方案与决策", "owner": "A0", "minutes": 10}],
                   "transcript": [{"by": o["seat"], "text": o.get("text") or o.get("error") or "",
                                   "src": "live" if o.get("text") else "error", "evidence": []} for o in out],
                   "decisions": [], "acceptance": []}
        code, env = hq_meeting.save_meeting(meetings or MEETINGS, payload)
        if code != 200 or not env.get("ok"):
            raise RuntimeError("meeting_save_failed:" + str(env.get("error"))[:160])
        case["meeting_id"] = env["meeting_id"]
        case["status"] = "MEETING"
        # propose
        case["orchestration"]["phase"] = "propose"
        _update(case_id, case, case_root)
        opinions = "\n".join(o["name"] + "：" + (o.get("text") or "")[:1500] for o in out)
        r = _chat("A0", "你是田丰。基于五角色意见给可执行方案：推荐/备选/风险/回滚/验收/需老田批准点；只输出方案，不派单、不执行。",
                  "议题：" + str(case.get("title") or "") + "\n五角色：\n" + opinions)
        case["proposal"] = r["text"]
        case["status"] = "PROPOSED"
        case["orchestration"].update({"state": "done", "phase": "proposed", "done": 5, "finished_at": _now()})
        _update(case_id, case, case_root)
        return case
    except Exception as e:  # noqa: BLE001
        case["orchestration"] = {"state": "error", "phase": case.get("orchestration", {}).get("phase", ""),
                                 "done": case.get("orchestration", {}).get("done", 0), "total": 5,
                                 "error": type(e).__name__ + ":" + str(e)[:240], "finished_at": _now()}
        _update(case_id, case, case_root)
        raise
    finally:
        with _LOCK:
            _RUNNING.discard(case_id)
