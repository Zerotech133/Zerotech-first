# -*- coding: utf-8 -*-
"""Phoenix 幂等自启：健康检查→(在跑则退出0/不在跑则拉起并等健康)。

用于计划任务 PhoenixAutostart；避免端口已被占用时 phoenix.exe 异常退出（0xC000013A）。
只读检查 + 子进程拉起；不杀进程、不改配置。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
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
except ImportError:  # 作为包导入时
    try:
        from safe_urlopen import safe_urlopen
    except ImportError:  # 作为包/仓库根导入时
        from tools.safe_urlopen import safe_urlopen

ROOT = Path(__file__).resolve().parents[1]
PHOENIX = ROOT / ".venvs" / "phoenix" / "Scripts" / "phoenix.exe"
HEALTH_URL = "http://127.0.0.1:6006/health"
LOG = Path(r"C:\Temp\phoenix_autostart.log")


def health_ok(url: str = HEALTH_URL, timeout: float = 3.0) -> bool:
    try:
        with safe_urlopen(url, timeout=timeout) as r:
            return 200 <= int(getattr(r, "status", 0)) < 300
    except Exception:  # noqa: BLE001
        return False


def _spawn(cmd: list[str], log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    f = log_path.open("a", encoding="utf-8", errors="replace")
    creationflags = 0
    if sys.platform == "win32":
        creationflags = (subprocess.DETACHED_PROCESS
                         | subprocess.CREATE_NEW_PROCESS_GROUP)
    subprocess.Popen(cmd, cwd=str(ROOT), stdout=f, stderr=f,
                     stdin=subprocess.DEVNULL, creationflags=creationflags)


def ensure_running(health=None, spawner=None, wait_s: int = 60,
                   log_path: Path | None = None) -> dict:
    health = health or health_ok
    spawner = spawner or (lambda cmd, lp: _spawn(cmd, lp))
    lp = log_path or LOG
    if health():
        return {"ok": True, "action": "already_running", "health": True}
    if not PHOENIX.is_file():
        return {"ok": False, "action": "missing_exe", "path": str(PHOENIX)}
    try:
        spawner([str(PHOENIX), "serve", "--host", "127.0.0.1", "--port", "6006"], lp)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "action": "spawn_failed",
                "error": "%s: %s" % (type(e).__name__, e)}
    deadline = time.time() + max(1, wait_s)
    while time.time() < deadline:
        if health():
            return {"ok": True, "action": "started", "health": True}
        time.sleep(2)
    return {"ok": False, "action": "started_but_unhealthy", "health": False}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Phoenix 幂等自启")
    ap.add_argument("--wait", type=int, default=60)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    rep = ensure_running(wait_s=args.wait)
    print(json.dumps(rep, ensure_ascii=False) if args.json
          else "phoenix: %s (ok=%s)" % (rep["action"], rep["ok"]))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
