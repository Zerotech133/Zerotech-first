# -*- coding: utf-8 -*-
"""browser-verification：Playwright 真浏览器验收（goto/click/fill/expect/screenshot）。

用法：
  python -X utf8 tools/browser_verify.py probe [--json]
  python -X utf8 tools/browser_verify.py run --url http://127.0.0.1:8796/tianfeng --steps steps.json
  python -X utf8 tools/browser_verify.py run --url URL --click "button#refresh" --expect-text "运行状态"
steps.json: [{"action":"goto","url":"..."},{"action":"click","selector":"#x"},
             {"action":"fill","selector":"#q","value":"abc"},
             {"action":"expect_text","text":"..."},
             {"action":"expect_selector","selector":"#ok"},
             {"action":"screenshot","path":"x.png"}]
证据：work/browser/browser-<ts>.json；失败退出码 1。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
OUT = REPO / "work" / "browser"


def probe() -> dict:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            page = b.new_page()
            page.set_content("<h1 id='ok'>ok</h1>")
            text = page.inner_text("#ok")
            b.close()
        return {"ok": True, "backend": "playwright-python", "text": text}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:300])}


def verify(url: str, steps=None, timeout: int = 30, shot_dir: str = "") -> dict:
    steps = steps or [{"action": "goto", "url": url},
                      {"action": "expect_selector", "selector": "body"}]
    outdir = Path(shot_dir) if shot_dir else OUT / "screens"
    outdir.mkdir(parents=True, exist_ok=True)
    ev = {"schema": "browser_verify/v1", "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
          "url": url, "steps": [], "console_errors": [], "ok": False}
    t0 = time.time()
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            page = b.new_page(viewport={"width": 1440, "height": 900})
            page.on("console", lambda m: ev["console_errors"].append(m.text)
                    if m.type == "error" else None)
            page.on("pageerror", lambda e: ev["console_errors"].append(str(e)[:300]))
            for i, st in enumerate(steps):
                act = str(st.get("action") or "").strip()
                r = {"i": i, "action": act, "ok": False}
                try:
                    if act == "goto":
                        page.goto(st.get("url") or url, timeout=timeout * 1000,
                                  wait_until="domcontentloaded")
                        r["url"] = page.url
                    elif act == "wait":
                        page.wait_for_timeout(int(st.get("ms") or 500))
                    elif act == "click":
                        page.click(st["selector"], timeout=timeout * 1000)
                    elif act == "fill":
                        page.fill(st["selector"], str(st.get("value") or ""),
                                  timeout=timeout * 1000)
                    elif act == "press":
                        page.press(st["selector"], str(st.get("key") or "Enter"))
                    elif act == "expect_selector":
                        page.wait_for_selector(st["selector"], timeout=timeout * 1000)
                        r["selector"] = st["selector"]
                    elif act == "expect_text":
                        text = page.inner_text("body")
                        if str(st.get("text") or "") not in text:
                            raise AssertionError("text not found: %s" % st.get("text"))
                        r["text"] = st.get("text")
                    elif act == "screenshot":
                        path = Path(st.get("path") or ("shot-%d.png" % i))
                        if not path.is_absolute():
                            path = outdir / path
                        page.screenshot(path=str(path), full_page=True)
                        r["screenshot"] = str(path)
                    else:
                        raise ValueError("unknown action: %s" % act)
                    r["ok"] = True
                except Exception as e:  # noqa: BLE001
                    r["error"] = "%s: %s" % (type(e).__name__, str(e)[:300])
                    ev["steps"].append(r)
                    break
                ev["steps"].append(r)
            ev["title"] = page.title()
            ev["final_url"] = page.url
            if page.screenshot:
                pass
            b.close()
        ev["ok"] = all(x.get("ok") for x in ev["steps"]) and \
            len([x for x in ev["steps"] if not x.get("ok")]) == 0
    except Exception as e:  # noqa: BLE001
        ev["error"] = "%s: %s" % (type(e).__name__, str(e)[:300])
    ev["elapsed_ms"] = int((time.time() - t0) * 1000)
    OUT.mkdir(parents=True, exist_ok=True)
    f = OUT / ("browser-%s.json" % time.strftime("%Y%m%d-%H%M%S"))
    f.write_text(json.dumps(ev, ensure_ascii=False, indent=1), encoding="utf-8")
    ev["evidence_path"] = str(f)
    return ev


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="田丰 browser-verification（Playwright）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("probe")
    p.add_argument("--json", action="store_true")
    r = sub.add_parser("run")
    r.add_argument("--url", required=True)
    r.add_argument("--steps", default="")
    r.add_argument("--click", default="")
    r.add_argument("--expect-text", default="")
    r.add_argument("--screenshot-dir", default="")
    r.add_argument("--timeout", type=int, default=30)
    r.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "probe":
        res = probe()
        print(json.dumps(res, ensure_ascii=False, indent=1) if args.json else
              ("ok" if res["ok"] else "FAIL: " + res.get("error", "")))
        return 0 if res["ok"] else 1
    steps = []
    if args.steps:
        steps = json.loads(Path(args.steps).read_text(encoding="utf-8"))
    else:
        steps = [{"action": "goto", "url": args.url}]
        if args.click:
            steps.append({"action": "click", "selector": args.click})
        steps.append({"action": "expect_selector", "selector": "body"})
        if args.expect_text:
            steps.append({"action": "expect_text", "text": args.expect_text})
    res = verify(args.url, steps, args.timeout, args.screenshot_dir)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print("ok=%s steps=%d" % (res["ok"], len(res["steps"])))
        for s in res["steps"]:
            print("  %s %s %s" % (s["action"], "PASS" if s["ok"] else "FAIL",
                                  s.get("error", "")))
        print("evidence", res["evidence_path"])
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
