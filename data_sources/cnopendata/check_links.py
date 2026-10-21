#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""档案链接健康检查：确认 registry 里的 URL 真能打开。

背景：档案里有 1 条记录（工商注册企业全信息）曾被"归一化"成父路径，
实测 404，而原始带 %2F 的链接反而 200。自动收录的条目更是从未人工验证过。
本脚本只做 GET 探测，不下载内容、不登录。

用法：
    python check_links.py                # 只查未分诊的自动收录条目
    python check_links.py --all          # 全量（506 条，约 8 分钟）
    python check_links.py --limit 50
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
REGISTRY = BASE / "registry.json"
OUT = BASE / "_link_health.json"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"


def tz_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def probe(url: str, attempts: int = 3) -> tuple[int | None, str]:
    """探状态码。5xx 与超时按瞬时故障重试。

    实测该站在 0.6s 间隔连续请求下会偶发 525（Cloudflare SSL 握手失败）与超时，
    复测即恢复 200。不重试的话每轮都会报一两个假坏链，报告就没人信了。
    """
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    last: tuple[int | None, str] = (None, "未执行")
    for i in range(attempts):
        req = urllib.request.Request(url, headers={"User-Agent": UA}, method="GET")
        try:
            with opener.open(req, timeout=25) as resp:
                return resp.status, resp.geturl()
        except urllib.error.HTTPError as exc:
            last = (exc.code, url)
            if exc.code < 500:      # 4xx 是确定性结果，不重试
                return last
        except Exception as exc:  # noqa: BLE001
            last = (None, str(exc)[:80])
        if i < attempts - 1:
            time.sleep(2.0 * (i + 1))
    return last


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--delay", type=float, default=0.8, help="每次请求间隔秒，避免打得太密")
    args = ap.parse_args()

    payload = json.loads(REGISTRY.read_text(encoding="utf-8"))
    targets = payload["datasets"]
    if not args.all:
        targets = [d for d in targets if d.get("status") == "new_untriaged"]
    if args.limit:
        targets = targets[: args.limit]

    print(f"待检查 {len(targets)} 条（间隔 {args.delay}s）")
    ok, bad, err = [], [], []
    for i, d in enumerate(targets, 1):
        code, info = probe(d["url"])
        if code == 200:
            ok.append(d["id"])
        elif code is None:
            err.append({"id": d["id"], "name": d["name"], "url": d["url"], "error": info})
        else:
            bad.append({"id": d["id"], "name": d["name"], "url": d["url"], "http": code, "final": info})
        if i % 20 == 0:
            print(f"  {i}/{len(targets)}  200={len(ok)} 非200={len(bad)} 异常={len(err)}")
        time.sleep(args.delay)

    report = {
        "checked_at": tz_now().isoformat(timespec="seconds"),
        "scope": "all" if args.all else "new_untriaged",
        "checked": len(targets),
        "ok": len(ok),
        "non_200": bad,
        "errors": err,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n结果：200 {len(ok)} / 非200 {len(bad)} / 异常 {len(err)}")
    for b in bad:
        print(f"  {b['http']}  {b['name']}  {b['url']}")
    for e in err:
        print(f"  ERR {e['name']}  {e['url']}  ({e['error']})")
    print(f"报告：{OUT}")
    return 0 if not bad and not err else 1


if __name__ == "__main__":
    raise SystemExit(main())
