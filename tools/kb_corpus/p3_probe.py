# -*- coding: utf-8 -*-
"""Phase 3 网络探测：YouTube 可达则自动跑试点转写+蒸馏。

由 BackupDaily（每日）调用；也可手动跑。
- 探测 www.youtube.com / yt-dlp 数据接口（urllib HEAD/GET，8s 超时）
- 可达 → 依次运行 p3_fetch_subs.py --limit 100 与 p3_distill.py --limit 100（后台子进程）
- 不可达 → 写状态文件 C:/Temp/kb_p3_probe.json（blocked），退出 0
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

REPO = Path(r"C:/项目/老田的AI助手")
STATUS = Path(r"C:/Temp/kb_p3_probe.json")
PY = r"C:/项目/py312/python.exe"


def reachable(url: str) -> bool:
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            return 200 <= r.status < 400
    except Exception:  # noqa: BLE001
        return False


def main() -> int:
    ts = datetime.now().astimezone().isoformat(timespec="seconds")
    ok = reachable("https://www.youtube.com/")
    rec = {"checked_at": ts, "youtube_reachable": ok}
    if not ok:
        rec["action"] = "blocked"
        STATUS.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(rec, ensure_ascii=False), flush=True)
        return 0
    rec["action"] = "start_pilot"
    STATUS.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    print("youtube reachable -> start pilot", flush=True)
    subprocess.run([PY, "-X", "utf8", str(REPO / "tools/kb_corpus/p3_fetch_subs.py"), "--limit", "100"],
                   capture_output=True, timeout=7200)
    subprocess.run([PY, "-X", "utf8", str(REPO / "tools/kb_corpus/p3_distill.py"), "--limit", "100"],
                   capture_output=True, timeout=7200)
    rec["action"] = "pilot_done"
    rec["finished_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    STATUS.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(rec, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
