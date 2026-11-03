#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A0 每日跑板：13 部门负责人真实云端汇报 → 田丰简报（可被计划任务调用）。

- 每天 07:50 执行；13 位负责人按 org.json 逐一调用云端模型；
- 3 并发；失败显式记录，不伪造；
- 产物：deliverables/daily_briefs/<date>/rollup.json + a0_briefing.md
- 退出码：0=13/13；2=部分失败；1=无真源/参数错误
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORG = ROOT / "agent_platform" / "registry" / "org.json"
DEFAULT_BASE = "http://127.0.0.1:8796"
sys.path.insert(0, str(ROOT / "tools"))

SYSTEM_TMPL = (
    "你是{name}（{lead}），零界{dept}负责人。只从你的职能面做部门早报；"
    "不编造具体数字，缺证据标需查证；不代替田丰派单、不代替老田拍板。"
    "只输出JSON：{\"status\":\"green|amber|red\","
    "\"yesterday_done\":[\"昨天实际完成、有证据的事\"],"
    "\"in_progress\":[\"今天正在做/未完成的事\"],"
    "\"blockers\":[\"当前卡点/正在卡点\"],"
    "\"decision_requests\":[\"需A0/老田决策事项\"],"
    "\"research_topics\":[{\"title\":\"研究题\",\"question\":\"研究问题\"}]}"
)


# 中军帐常任指挥席：每天也必须报“昨天完成/正在做/当前卡点”
COMMAND_LEADS = [
    {"lead": "AI1", "dept": "zhong", "name": "中军帐·诸葛亮", "role": "军师"},
    {"lead": "ZHAOYUN", "dept": "zhong", "name": "中军帐·赵云治理", "role": "治理"},
    {"lead": "GUANYU", "dept": "zhong", "name": "中军帐·关羽整治", "role": "整治"},
    {"lead": "HUANGZHONG", "dept": "zhong", "name": "中军帐·黄忠验收", "role": "验收"},
]


def _leads(include_command: bool = True) -> list[dict]:
    d = json.loads(ORG.read_text(encoding="utf-8-sig"))
    out, seen = [], set()
    for dept in d.get("departments", []):
        lead = dept.get("lead")
        if not lead or lead == "A0" or lead in seen:
            continue
        seen.add(lead)
        out.append({"lead": lead, "dept": dept.get("dept"), "name": dept.get("name")})
    if include_command:
        for lead in COMMAND_LEADS:
            if lead["lead"] not in seen:
                seen.add(lead["lead"])
                out.append(dict(lead))
    return out


def _context() -> str:
    try:
        import hq_directives
        ds = hq_directives.build_directives().get("directives") or []
        return "\n".join((x.get("kind") or "") + ":" + (x.get("topic") or "")
                         for x in ds if x.get("status") == "open")[:1500]
    except Exception:  # noqa: BLE001
        return ""


def _ask(lead: dict, date: str, base: str, timeout: int) -> dict:
    y = (datetime.date.fromisoformat(date) - datetime.timedelta(days=1)).isoformat()
    text = ("报告日期 " + date + "｜统计昨天 " + y + "。\n"
            "请汇报：1) 昨天实际完成了什么（有证据，没做就写无）；"
            "2) 今天正在做什么；3) 当前卡点/正在卡点；4) 需 A0/老田决策；5) 研究题。\n"
            "当前未派单议题：\n" + (_context() or "（无）") + "\n请立即生成部门早报，供田丰汇总。")
    system = (SYSTEM_TMPL.replace("{name}", str(lead.get("name") or ""))
              .replace("{lead}", str(lead.get("lead") or ""))
              .replace("{dept}", str(lead.get("dept") or "")))
    body = json.dumps({"agent": lead["lead"],
                       "system": system,
                       "purpose": "dept_report", "user": text}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(base.rstrip("/") + "/api/chat", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    t = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        env = json.loads(r.read().decode("utf-8"))
    if not env.get("ok"):
        raise RuntimeError(str(env.get("error") or "model_failed")[:200])
    txt = str((env.get("data") or {}).get("text") or "").strip()
    if not txt:
        raise RuntimeError("empty_model_text")
    return {"lead": lead, "text": txt, "binding": env.get("binding") or "",
            "latency_ms": env.get("latency_ms") or int((time.time() - t) * 1000),
            "degraded": bool(env.get("degraded"))}


def _parse(lead: dict, text: str) -> dict:
    d = None
    t = str(text).strip().replace("```json", "").replace("```", "").strip()
    a, b = t.find("{"), t.rfind("}")
    if a >= 0 and b > a:
        try:
            d = json.loads(t[a:b + 1])
        except ValueError:
            d = None
    arr = lambda v: [str(x) for x in (v if isinstance(v, list) else [])][:6]  # noqa: E731
    st = (d or {}).get("status") or "amber"
    if st not in ("green", "amber", "red"):
        st = "amber"
    topics = []
    for x in (d or {}).get("research_topics") or []:
        if isinstance(x, str):
            topics.append({"title": x, "question": ""})
        elif isinstance(x, dict) and x.get("title"):
            topics.append({"title": str(x["title"]), "question": str(x.get("question") or "")})
    yesterday = arr((d or {}).get("yesterday_done") or (d or {}).get("highlights"))
    return {"lead": lead["lead"], "dept": lead["dept"], "dept_name": lead["name"],
            "status": st, "yesterday_done": yesterday,
            "in_progress": arr((d or {}).get("in_progress")),
            "highlights": yesterday,  # 兼容旧消费方
            "blockers": arr((d or {}).get("blockers")),
            "decision_requests": arr((d or {}).get("decision_requests")),
            "research_topics": topics[:6]}


def run(date: str | None = None, base: str = DEFAULT_BASE, concurrency: int = 3,
        timeout: int = 120, write: bool = True, out_dir: Path | None = None,
        include_command: bool = True) -> dict:
    date = date or datetime.date.today().isoformat()
    leads = _leads(include_command)
    reports, failed = [], []
    with cf.ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
        futs = {ex.submit(_ask, l, date, base, timeout): l for l in leads}
        for f in cf.as_completed(futs):
            l = futs[f]
            try:
                r = f.result()
                rep = _parse(l, r["text"])
                rep.update({"_binding": r.get("binding", ""), "_latency_ms": r.get("latency_ms"),
                            "_degraded": bool(r.get("degraded"))})
                reports.append(rep)
            except Exception as e:  # noqa: BLE001
                failed.append({"lead": l["lead"], "dept": l["dept"], "error": type(e).__name__ + ":" + str(e)[:160]})
    reports.sort(key=lambda x: [l["lead"] for l in leads].index(x["lead"]))
    topics = []
    for r in reports:
        for t in (r.get("research_topics") or []):
            topics.append({"id": "RT-%s-%02d" % (date.replace("-", ""), len(topics) + 1),
                           "title": t["title"], "question": t.get("question", ""),
                           "source_lead": r["lead"], "source_dept": r.get("dept_name")})
    briefing = {"schema": "hq/a0_briefing/v1", "date": date, "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                "headline": "部门早报：%d/%d 提交，%d 失败" % (len(reports), len(leads), len(failed)),
                "submitted": len(reports), "expected": len(leads), "failed": failed,
                "departments": reports, "research_topics": topics}
    result = {"schema": "hq/dept_daily_run/v1", "date": date, "ok": len(reports) == len(leads),
              "submitted": len(reports), "expected": len(leads), "failed": failed,
              "reports": reports, "briefing": briefing,
              "generated_at": briefing["generated_at"]}
    if write:
        out = Path(out_dir) if out_dir else (ROOT / "deliverables" / "daily_briefs" / date)
        out.mkdir(parents=True, exist_ok=True)
        tmp = out / "rollup.json.tmp"
        tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, out / "rollup.json")
        (out / "a0_briefing.md").write_text(_markdown(result), encoding="utf-8")
        result["out_dir"] = str(out)
    return result


def _markdown(r: dict) -> str:
    b = r["briefing"]
    lines = ["# 田丰早间简报 · " + r["date"], "",
             "- 部门汇报：%d/%d｜失败 %d" % (r["submitted"], r["expected"], len(r["failed"])), ""]
    for d in b["departments"]:
        lines += ["## %s（%s）· %s" % (d.get("dept_name") or d["dept"], d["lead"], d["status"]),
                  "- 昨天完成：" + ("；".join(d.get("yesterday_done") or d.get("highlights") or []) or "未填"),
                  "- 正在做：" + ("；".join(d.get("in_progress") or []) or "无"),
                  "- 当前卡点：" + ("；".join(d.get("blockers") or []) or "无"),
                  "- 需决策：" + ("；".join(d["decision_requests"]) or "无"), ""]
    if b["research_topics"]:
        lines += ["## 研究题"] + ["- %s（%s）" % (t["title"], t["source_lead"]) for t in b["research_topics"]]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="A0 每日跑板")
    ap.add_argument("--date", default="")
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--out-dir", default="")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-command", action="store_true", help="只跑13部门，不含四席")
    a = ap.parse_args(argv)
    try:
        r = run(a.date or None, a.base, a.concurrency, a.timeout,
                write=not a.no_write, out_dir=Path(a.out_dir) if a.out_dir else None,
                include_command=not a.no_command)
    except Exception as e:  # noqa: BLE001
        print("daily_run_error: %s" % str(e)[:300], file=sys.stderr)
        return 1
    print(json.dumps({k: r.get(k) for k in ("schema", "date", "ok", "submitted", "expected", "failed", "out_dir")},
                     ensure_ascii=False, indent=2))
    return 0 if r["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
