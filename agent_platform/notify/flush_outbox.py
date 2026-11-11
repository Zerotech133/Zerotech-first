# -*- coding: utf-8 -*-
"""outbox 补发 runner：把 data/outbox 内 status=queued 的消息经飞书 webhook 重发。

路由纪律（老田 2026-11-10 裁定）下，queued 只剩人类审批类（approval_card）；
AI↔AI 消息 enqueue 时已自动转 bridge_inbox 内部闭环（delivered_internal），
不经本 runner。

用法：
    python notify/flush_outbox.py            # 补发全部 queued
    python notify/flush_outbox.py MSG-xxx    # 只补发指定 id

边界：
  · webhook 凭据缺失时 rc=2 退出（不假装成功）；
  · 凭据来源与 outbox.send() 一致（env FEISHU_WEBHOOK_URL / vault 文件），不打印回显；
  · 每条结果留痕（sent / failed），index.jsonl 不动（审计以 MSG-*.json status 为准）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import outbox  # noqa: E402


def main(argv: list[str]) -> int:
    only = set(argv)
    queued = []
    for f in sorted(outbox.OUTBOX.glob("MSG-*.json")):
        try:
            rec = json.loads(f.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            print("SKIP unreadable:", f.name)
            continue
        if rec.get("status") == "queued" and (not only or rec.get("id") in only):
            queued.append(rec.get("id"))
    if not queued:
        print("no queued messages" + (" (filter=%s)" % ",".join(sorted(only)) if only else ""))
        return 0
    if not outbox._webhook():
        print("webhook_not_configured：凭据缺失（env FEISHU_WEBHOOK_URL 或 vault 文件），rc=2 不假装发送")
        return 2
    rc = 0
    for mid in queued:
        r = outbox.send(mid)
        ok = bool(r.get("ok"))
        print(("SENT " if ok else "FAIL ") + str(mid) + (" resp=" + str(r.get("response"))[:120] if ok else " err=" + str(r.get("error"))))
        if not ok:
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
