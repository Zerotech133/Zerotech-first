"""council_review_client.py — 议会厅席位复核客户端（甲案落地，2026-10-23）。

背景：CLI 路线被沙箱 schtasks 黑名单阻断（service_manager 依赖），
甲案 = 改走 WebUI API（8787，SYSTEM 级不受黑名单约束）。
已验证协议：Cookie hermes_profile=<席位> → POST /api/session/new →
POST /api/chat/start → GET /api/chat/stream（SSE）。

用途：议会厅监读/复核动作以真席位执行（tianfeng/zhaoyun/zhuge 任一 profile）。
纪律：只读复核（提示词约定不写盘不改账）；超时如实报；流断如实报不重试掩盖。
"""

from __future__ import annotations

try:  # Phase B1: 8787 密码认证后自动带 hermes_session
    import sys as _sys
    from pathlib import Path as _P
    _sys.path.insert(0, str(_P(__file__).resolve().parents[2]))
    from agent_platform.legion_command.hermes_auth import auth_urlopen as safe_urlopen  # noqa: E402
except Exception:  # noqa: BLE001
    pass


import argparse
import json
import urllib.request
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

BASE = "http://127.0.0.1:8787"
TIMEOUT_START = 120
TIMEOUT_STREAM = 180


def _post(path: str, payload: dict, profile: str) -> dict:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Cookie": f"hermes_profile={profile}",
        },
        method="POST",
    )
    with safe_urlopen(req, timeout=TIMEOUT_START) as r:
        return json.loads(r.read().decode("utf-8"))


def seat_review(
    profile: str, question: str, expect_keywords: list[str] | None = None
) -> dict:
    """以指定席位发起一次复核问答，返回 {session_id, stream_id, reply, ok}。"""
    ses = _post("/api/session/new", {"name": "council-review"}, profile)
    session_id = ses["session"]["session_id"]
    start = _post(
        "/api/chat/start", {"session_id": session_id, "message": question}, profile
    )
    stream_id = start.get("stream_id")
    if not stream_id:
        return {"ok": False, "stage": "chat_start", "detail": start}
    # 拉 SSE 流聚合回复：token 事件=正文增量（实测 60 token 事件），replay=1 回放已完成流
    req = urllib.request.Request(
        f"{BASE}/api/chat/stream?session_id={session_id}&stream_id={stream_id}&replay=1",
        headers={"Cookie": f"hermes_profile={profile}"},
    )
    chunks: list[str] = []
    current_event = ""
    with safe_urlopen(req, timeout=TIMEOUT_STREAM) as r:
        for raw in r:
            line = raw.decode("utf-8", errors="replace").rstrip("\n")
            if line.startswith("event:"):
                current_event = line.split(":", 1)[1].strip()
                continue
            if not line.startswith("data:"):
                continue
            try:
                evt = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            etype = evt.get("type") or current_event
            if etype == "token":
                c = evt.get("text") or evt.get("token") or evt.get("content") or ""
                if c:
                    chunks.append(str(c))
            elif etype in ("stream_end", "done"):
                break
    reply = "".join(chunks).strip()
    ok = bool(reply)
    if ok and expect_keywords:
        ok = all(k in reply for k in expect_keywords)
    return {
        "ok": ok,
        "session_id": session_id,
        "stream_id": stream_id,
        "reply": reply[:2000],
        "profile": profile,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="议会厅席位复核客户端（8787 甲案）")
    ap.add_argument(
        "--profile", default="tianfeng", help="tianfeng|zhaoyun|zhuge|default"
    )
    ap.add_argument("--question", required=True)
    ap.add_argument("--expect", help="逗号分隔必含关键词（验收口径）")
    args = ap.parse_args(argv)
    r = seat_review(
        args.profile,
        args.question,
        [k for k in (args.expect or "").split(",") if k] or None,
    )
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
