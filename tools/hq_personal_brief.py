#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""云端 A0 个人版早报：只消费 LocalBrief 的授权摘要/证据。

输入必须是 local_library.cloud_payload 产物（hq/local_brief_cloud/v1）；
不得包含原文/本地路径/root。按 user_id 输出个人目录，不跨用户。
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE = "http://127.0.0.1:8796"
SCHEMA = "hq/personal_brief/v1"
CLOUD_SCHEMA = "hq/local_brief_cloud/v1"
USER_RE = re.compile(r"^[A-Za-z0-9._@-]{1,80}$")


def _safe_user(u: str) -> str:
    if not u or not USER_RE.fullmatch(u) or u in (".", ".."):
        raise ValueError("bad_user")
    return u


def build(cloud: dict, user: str, base: str = DEFAULT_BASE, agent: str = "A0",
          timeout: int = 120) -> dict:
    user = _safe_user(user)
    if not isinstance(cloud, dict) or cloud.get("schema") != CLOUD_SCHEMA:
        raise ValueError("bad_cloud_schema")
    if str(cloud.get("user_id") or "") != user:
        raise ValueError("cloud_user_mismatch")
    if (cloud.get("privacy") or {}).get("raw_uploaded") is not False:
        raise ValueError("raw_upload_not_allowed")
    summary = str(cloud.get("summary") or "")[:4000]
    evidence = cloud.get("evidence") or []
    ev = "\n".join("- %s（%s）" % (str(x.get("title") or "")[:120], str(x.get("sha256") or "")[:12])
                   for x in evidence[:20] if isinstance(x, dict))
    system = ("你是田丰（A0），零界参谋长。为个人用户生成个人版早报；只使用授权摘要/证据，"
              "不得臆测本地原文、不得编造具体数字；输出结论、今日重点、需决策、研究题。")
    user_msg = ("用户：" + user + "\n授权摘要：\n" + (summary or "（无）") +
                "\n证据标题：\n" + (ev or "（无）") + "\n请生成不超过 400 字的个人版早报。")
    body = json.dumps({"agent": agent, "system": system, "purpose": "personal_brief",
                       "user": user_msg}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(base.rstrip("/") + "/api/chat", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        env = json.loads(r.read().decode("utf-8"))
    if not env.get("ok"):
        raise RuntimeError(str(env.get("error") or "model_failed")[:200])
    text = str((env.get("data") or {}).get("text") or "").strip()
    if not text:
        raise RuntimeError("empty_model_text")
    return {
        "schema": SCHEMA, "user_id": user,
        "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "agent": agent, "binding": env.get("binding") or (env.get("data") or {}).get("binding_resolved") or "",
        "degraded": bool(env.get("degraded")), "text": text[:4000],
        "source": {"cloud_schema": CLOUD_SCHEMA, "summary": summary,
                   "evidence": [{"sha256": x.get("sha256"), "title": x.get("title")}
                                for x in evidence[:20] if isinstance(x, dict)]},
        "privacy": {"raw_uploaded": False, "cloud_input": "summary_only"},
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="A0 个人版早报")
    ap.add_argument("--cloud-brief", required=True)
    ap.add_argument("--user", required=True)
    ap.add_argument("--out", default="")
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--agent", default="A0")
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    try:
        cloud = json.loads(Path(a.cloud_brief).read_text(encoding="utf-8-sig"))
        out = build(cloud, a.user, a.base, a.agent, a.timeout)
    except Exception as e:  # noqa: BLE001
        print("personal_brief_error: %s" % str(e)[:300], file=sys.stderr)
        return 2
    if a.dry_run:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    target = Path(a.out) if a.out else (ROOT / "deliverables" / "personal_briefs" / a.user /
                                        (datetime.date.today().isoformat() + ".json"))
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, target)
    print(str(target))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
