# -*- coding: utf-8 -*-
"""E2E-09 mock 回环：私聊命令 → feishu_bot_ws.handle_cmd → 捕获 send_text 回执。

不发真实飞书消息、不连 WS、不调 LLM：仅验证"命令解析→回执生成"路径。
输出 JSON（stdout）：{"ok": bool, "reply": str, "captured": int}
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))  # tests/e2e -> tests -> repo
sys.path.insert(0, os.path.join(REPO, "portal"))
os.environ.setdefault("VOICE_INGEST_ENABLED", "0")

import feishu_bot_ws as bot  # noqa: E402

captured = []
bot.send_text = lambda text, message_id=None: (captured.append(text), True)[1]  # noqa: E731

try:
    bot.handle_cmd("机器", "om_e2e_mock", "ou_e2e_mock", "p2p")
    reply = captured[-1] if captured else ""
    ok = bool(reply) and ("迁移" in reply or "入口" in reply)
    print(json.dumps({"ok": ok, "reply": reply, "captured": len(captured)},
                     ensure_ascii=False))
    raise SystemExit(0 if ok else 1)
except SystemExit:
    raise
except Exception as e:  # noqa: BLE001
    print(json.dumps({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200])},
                     ensure_ascii=False))
    raise SystemExit(1)
