#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""部门主管调度云端模型思考（CloudThink v1）。

- 限定 13 位部门负责人；
- 统一云端主绑 glm/glm-5.3（由 8796 org 真源解析）；
- 输出结构化思考：结论/依据/风险/建议/需查证；
- 只读调用，不自动派单/不写生产/不代签。
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLOUD_LEADS = {
    "D1": ("鲁肃", "量化交易部"),
    "ARCH1": ("曹操", "研发部"),
    "DATA1": ("张松", "数据资源主官"),
    "PM1": ("吕蒙", "产品管理部"),
    "QA1": ("于禁", "质量工程部"),
    "DEV3": ("蒋济", "回测开发部"),
    "MKT1": ("简雍", "市场部"),
    "LEG1": ("诸葛瑾", "合规法务部"),
    "A0A": ("董允", "运行体部"),
    "ACAD1": ("司马徽", "研究院"),
    "INTEL0": ("马超", "情报部"),
    "LUSU1": ("阚泽", "通信与客户服务部"),
    "R3": ("陆逊", "风控部"),
}
DEFAULT_BASE = "http://127.0.0.1:8796"


def build_prompt(lead: str, question: str, context: str = "") -> dict:
    name, dept = CLOUD_LEADS[lead]
    system = (
        f"你是{name}（{lead}），零界{dept}负责人。只从你的职能面思考。"
        "输出必须区分事实/推断/需查证，不得编造具体数字或来源。"
        "不要代替田丰派单，不要代替老田拍板。"
        "只输出一个JSON对象："
        '{"conclusion":"一句话结论","evidence":["依据或需查证"],'
        '"risks":["风险"],"recommendation":"建议","questions":["还需查证什么"]}'
    )
    user = "问题：" + question.strip()[:2000]
    if context.strip():
        user += "\n已知上下文（可能不完整）：" + context.strip()[:4000]
    return {"agent": lead, "system": system, "purpose": "dept_think", "user": user}


def call_cloud(payload: dict, base: str = DEFAULT_BASE, timeout: int = 120) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        base.rstrip("/") + "/api/chat", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        env = json.loads(r.read().decode("utf-8"))
    if not env.get("ok"):
        raise RuntimeError("cloud_think_failed: %s" % str(env.get("error") or env)[:300])
    data = env.get("data") or {}
    text = str(data.get("text") or "").strip()
    if not text:
        raise RuntimeError("cloud_think_empty")
    return {
        "schema": "hq/cloud_think/v1",
        "lead": payload["agent"], "name": CLOUD_LEADS[payload["agent"]][0],
        "dept": CLOUD_LEADS[payload["agent"]][1],
        "question": payload["user"],
        "text": text, "binding": env.get("binding") or data.get("binding_resolved") or "",
        "degraded": bool(env.get("degraded")), "latency_ms": env.get("latency_ms"),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="部门主管调度云端模型思考")
    ap.add_argument("--lead", required=True)
    ap.add_argument("--question", required=True)
    ap.add_argument("--context", default="")
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--out", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    lead = args.lead.strip()
    if lead not in CLOUD_LEADS:
        print("unknown_or_not_cloud_lead: " + lead, file=sys.stderr)
        return 2
    payload = build_prompt(lead, args.question, args.context)
    try:
        res = call_cloud(payload, args.base, args.timeout)
    except Exception as exc:  # noqa: BLE001
        print("cloud_think_error: %s" % str(exc)[:300], file=sys.stderr)
        return 2
    text = json.dumps(res, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
