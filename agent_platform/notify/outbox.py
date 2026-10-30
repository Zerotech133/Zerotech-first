# -*- coding: utf-8 -*-
"""通知 outbox + 飞书审批卡片生成（默认只落 outbox，不假装发送）。

边界：
  · 无 webhook 凭据时 send() 返回 webhook_not_configured，消息状态保持 queued；
  · webhook 从 env `FEISHU_WEBHOOK_URL` 或私密文件读取（不落仓、不打印）；
  · 审批卡片 = 飞书 interactive card JSON（approve/reject 带 request_id value）；
  · 所有消息留痕：data/outbox/*.json + index.jsonl，可审计。
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
import uuid
from pathlib import Path
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

PLATFORM = Path(__file__).resolve().parents[1]
REPO = PLATFORM.parent
OUTBOX = PLATFORM / "data" / "outbox"
WEBHOOK_CANDIDATES = [
    Path(os.environ.get("FEISHU_WEBHOOK_FILE") or ""),
    REPO.parent / "_vault" / "secrets" / "feishu_webhook.txt",
    REPO / "_vault" / "secrets" / "feishu_webhook.txt",
]


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _webhook() -> str:
    env = os.environ.get("FEISHU_WEBHOOK_URL")
    if env:
        return env.strip()
    for p in WEBHOOK_CANDIDATES:
        try:
            if p and p.is_file():
                return p.read_text(encoding="utf-8").strip()
        except OSError:
            continue
    return ""


def enqueue(
    title: str, body: str, kind: str = "notify", ref: str = "", card: dict | None = None
) -> dict:
    OUTBOX.mkdir(parents=True, exist_ok=True)
    mid = "MSG-%s-%s" % (time.strftime("%Y%m%d%H%M%S"), uuid.uuid4().hex[:6])
    rec = {
        "schema": "zhaoyun_outbox/v1",
        "id": mid,
        "kind": kind,
        "channel": "feishu",
        "title": str(title)[:200],
        "body": str(body)[:4000],
        "ref": str(ref)[:120],
        "status": "queued",
        "created_at": _now(),
        "sent_at": None,
        "card": card,
    }
    (OUTBOX / (mid + ".json")).write_text(
        json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    with open(OUTBOX / "index.jsonl", "a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "id": mid,
                    "kind": kind,
                    "title": rec["title"],
                    "status": "queued",
                    "created_at": rec["created_at"],
                },
                ensure_ascii=False,
            )
            + "\n"
        )
    return {"ok": True, "message": rec, "webhook_configured": bool(_webhook())}


def send(mid: str, timeout: float = 10.0) -> dict:
    p = OUTBOX / (str(mid) + ".json")
    try:
        rec = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {"ok": False, "error": "message_not_found", "id": mid}
    hook = _webhook()
    if not hook:
        return {
            "ok": False,
            "error": "webhook_not_configured",
            "id": mid,
            "status": rec.get("status"),
            "note": "已落 outbox，未发送（不假装成功）",
        }
    payload = rec.get("card") or {
        "msg_type": "text",
        "content": {"text": "%s\n%s" % (rec.get("title"), rec.get("body"))},
    }
    try:
        req = urllib.request.Request(
            hook,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with safe_urlopen(req, timeout=max(1.0, min(timeout, 30.0))) as r:
            body = r.read(2000).decode("utf-8", errors="replace")
        rec.update({"status": "sent", "sent_at": _now(), "response": body[:500]})
        p.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        return {"ok": True, "id": mid, "status": "sent", "response": body[:200]}
    except Exception as e:  # noqa: BLE001
        rec.update(
            {"status": "failed", "error": "%s: %s" % (type(e).__name__, str(e)[:160])}
        )
        p.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        return {"ok": False, "error": "send_failed", "detail": str(e)[:160], "id": mid}


def approval_card(request: dict) -> dict:
    rid = str(request.get("id") or "")
    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "orange",
                "title": {"tag": "plain_text", "content": "赵云审批请求 · %s" % rid},
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": "**类型**：%s\n**状态**：%s\n**理由**：%s"
                        % (
                            request.get("kind"),
                            request.get("status"),
                            request.get("reason") or "—",
                        ),
                    },
                },
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "批准"},
                            "type": "primary",
                            "value": {"action": "approve", "request_id": rid},
                        },
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "拒绝"},
                            "type": "danger",
                            "value": {"action": "reject", "request_id": rid},
                        },
                    ],
                },
                {
                    "tag": "note",
                    "elements": [
                        {
                            "tag": "plain_text",
                            "content": "人工审批=控制权；Agent 不代批",
                        }
                    ],
                },
            ],
        },
    }


def enqueue_approval_card(request: dict) -> dict:
    rid = str(request.get("id") or "")
    return enqueue(
        "审批请求 " + rid,
        json.dumps(
            {"kind": request.get("kind"), "reason": request.get("reason")},
            ensure_ascii=False,
        ),
        kind="approval_card",
        ref=rid,
        card=approval_card(request),
    )


def recent(limit: int = 20) -> dict:
    rows = []
    OUTBOX.mkdir(parents=True, exist_ok=True)
    files = sorted(
        OUTBOX.glob("MSG-*.json"), key=lambda p: p.stat().st_mtime, reverse=True
    )[: max(1, min(int(limit), 100))]
    for f in files:
        try:
            r = json.loads(f.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        rows.append(
            {
                k: r.get(k)
                for k in (
                    "id",
                    "kind",
                    "title",
                    "status",
                    "created_at",
                    "sent_at",
                    "ref",
                )
            }
        )
    return {
        "ok": True,
        "count": len(rows),
        "webhook_configured": bool(_webhook()),
        "items": rows,
        "note": "无 webhook 时消息只排队，不发送",
    }
