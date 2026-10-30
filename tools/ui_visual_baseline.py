# -*- coding: utf-8 -*-
r"""ui_visual_baseline.py — 通过 Chrome DevTools Protocol 采集视觉基线。

前置：本机 Edge/Chrome 以 CDP 启动，例如：
  msedge --headless=new --disable-gpu --no-sandbox \
    --remote-debugging-port=9335 --user-data-dir=C:\Temp\edge_visual \
    http://127.0.0.1:8734/os-entry

用法：
  python tools/ui_visual_baseline.py --port 9335 --url http://127.0.0.1:8734/os-entry \
    --out-dir docs/visual_baseline --label os_entry_20261027

产物：<label>_desktop_1440.png / <label>_mobile_375.png / manifest.json
只读截图，不改页面、不写入页面 storage。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import websocket

try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包导入时
    from tools.safe_urlopen import safe_urlopen

VIEWPORTS = (("desktop", 1440, 1200, False), ("mobile", 375, 900, True))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="CDP visual baseline")
    ap.add_argument("--port", type=int, default=9335)
    ap.add_argument("--url", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--label", default="baseline")
    args = ap.parse_args(argv)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    tabs = json.load(safe_urlopen(
        "http://127.0.0.1:%d/json" % args.port))
    tab = next((t for t in tabs if t.get("type") == "page"
                and t.get("url", "").startswith(args.url.split("?")[0])), None)
    if tab is None:
        raise SystemExit("CDP 找不到目标页：%s" % args.url)

    ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=15)
    seq = 0

    def send(method, params=None):
        nonlocal seq
        seq += 1
        ws.send(json.dumps({"id": seq, "method": method, "params": params or {}}))
        while True:
            m = json.loads(ws.recv())
            if m.get("id") == seq:
                return m

    def ev(expr):
        r = send("Runtime.evaluate", {"expression": expr, "returnByValue": True})
        return r.get("result", {}).get("result", {}).get("value")

    send("Page.enable")
    send("Runtime.enable")
    send("Page.navigate", {"url": args.url})
    for _ in range(30):
        if ev("document.readyState") == "complete":
            break
        time.sleep(0.2)
    time.sleep(1.0)

    manifest = {"url": args.url, "captured_at": datetime.now(timezone.utc).isoformat(),
                "views": []}
    for name, width, height, mobile in VIEWPORTS:
        send("Emulation.setDeviceMetricsOverride", {
            "width": width, "height": height,
            "deviceScaleFactor": 1, "mobile": mobile})
        time.sleep(0.5)
        shot = send("Page.captureScreenshot",
                    {"format": "png", "captureBeyondViewport": True})
        raw = base64.b64decode(shot["result"]["data"])
        path = out / ("%s_%s_%d.png" % (args.label, name, width))
        path.write_bytes(raw)
        manifest["views"].append({
            "name": name, "width": width, "height": height,
            "file": path.name, "sha256": sha256_bytes(raw), "bytes": len(raw),
        })
        print("saved %s %d bytes sha256=%s" % (path, len(raw), sha256_bytes(raw)[:16]))
    send("Emulation.clearDeviceMetricsOverride")
    ws.close()
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print("manifest:", out / "manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
