# -*- coding: utf-8 -*-
"""飞书收件人可达性审计（W3 清债工具，2026-10-24）。

背景：open_id / chat_id 是 **app 维度**的；push_targets 是一张全局表，
若某条目标属于另一个 app 域，旧 app 凭证发送会得到
`code=230001 invalid receive_id`（HTTP 400）——这类目标必须在收口前审计出来。

能力（只读，不发消息）：
  · chat_id → GET /im/v1/chats/{chat_id}（机器人须在群内）
  · open_id → GET /contact/v3/users/{id}?user_id_type=open_id（需 contact 读权限）
  · 输出表格 / JSON；存在不可达目标时退出码 1（可接 push_liveness）

用法：
    python feishu_target_audit.py
    python feishu_target_audit.py --json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import logging

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
NEWS = os.path.join(ROOT, "news_collector")
for p in (HERE, NEWS):
    if p not in sys.path:
        sys.path.insert(0, p)

OPEN = "https://open.feishu.cn/open-apis"


def classify_target(target):
    """→ 'chat_id' / 'open_id' / 'unknown'。"""
    tid = str((target or {}).get("id") or "").strip()
    if tid.startswith("oc_"):
        return "chat_id"
    if tid.startswith("ou_"):
        return "open_id"
    return "unknown"


def audit_target(target, token, opener=None, canonical_app=""):
    """单个目标只读校验 → dict(name,id,type,app,ok,reason)。"""
    name = target.get("name") or "?"
    tid = str(target.get("id") or "")
    app = str(target.get("app") or "")
    ttype = classify_target(target)
    rec = {
        "name": name,
        "id": tid,
        "type": ttype,
        "app": app,
        "ok": False,
        "reason": "",
    }
    if ttype == "unknown":
        rec["reason"] = "id 前缀不认识（非 ou_/oc_）"
        return rec
    if canonical_app and app and app != canonical_app:
        rec["reason"] = (
            "app mismatch：目标登记 %s ≠ 当前凭据 app %s"
            "（跨应用域必得 230001）" % (app, canonical_app)
        )
        return rec
    if not token:
        rec["reason"] = "无 tenant token（凭证缺失）"
        return rec
    if ttype == "chat_id":
        url = OPEN + "/im/v1/chats/" + urllib.parse.quote(tid, safe="")
    else:
        url = (
            OPEN
            + "/contact/v3/users/"
            + urllib.parse.quote(tid, safe="")
            + "?user_id_type=open_id"
        )
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
    try:
        o = opener or urllib.request.urlopen
        with o(req, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8"))
        code = data.get("code")
        if code == 0:
            rec["ok"] = True
            rec["reason"] = "ok"
        else:
            rec["reason"] = "code=%s %s" % (code, str(data.get("msg"))[:100])
    except urllib.error.HTTPError as e:
        raw = ""
        try:
            raw = e.read().decode("utf-8", "replace")[:160]
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "portal/feishu_target_audit.py", exc)
        rec["reason"] = "HTTP %d %s" % (e.code, raw)
    except Exception as e:  # noqa: BLE001
        rec["reason"] = "ERR " + str(e)[:120]
    return rec


def audit(token, targets=None, opener=None, canonical_app=""):
    if targets is None:
        import push_targets

        targets = push_targets.targets()
    rows = [
        audit_target(t, token, opener=opener, canonical_app=canonical_app)
        for t in targets
    ]
    summary = {
        "total": len(rows),
        "ok": sum(1 for r in rows if r["ok"]),
        "bad": sum(1 for r in rows if not r["ok"]),
        "canonical_app": canonical_app,
        "rows": rows,
    }
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    import feishu_client

    token = feishu_client.get_tat()
    canonical = ""
    try:
        canonical = feishu_client._canonical_app_id()
        if not canonical:
            canonical, _ = feishu_client.load_app_creds()
    except Exception:  # noqa: BLE001
        canonical = ""
    s = audit(token, canonical_app=canonical or "")
    if args.json:
        print(json.dumps(s, ensure_ascii=False, indent=1))
    else:
        print(
            "收件人可达性审计：total=%d ok=%d bad=%d canonical_app=%s"
            % (s["total"], s["ok"], s["bad"], s.get("canonical_app") or "-")
        )
        for r in s["rows"]:
            print(
                "  %s %-12s %-8s app=%-14s %s  %s"
                % (
                    "✅" if r["ok"] else "❌",
                    r["name"],
                    r["type"],
                    (r.get("app") or "-")[:14],
                    r["id"][:20] + ("…" if len(r["id"]) > 20 else ""),
                    r["reason"][:90],
                )
            )
    return 0 if s["bad"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
