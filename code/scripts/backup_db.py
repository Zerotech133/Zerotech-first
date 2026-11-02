"""V2 数据库备份脚本（使用 pg_dump）。"""
from __future__ import annotations

import os
import subprocess
from datetime import datetime

DB_URL = os.getenv("DB_MAIN_URL") or __import__("zhice_os_v2.config.settings", fromlist=["get_settings"]).get_settings().db_main_url


def main() -> None:
    out = f"backup/zhice_os_v2_{datetime.now().strftime('%Y%m%d_%H%M%S')}.dump"
    os.makedirs("backup", exist_ok=True)
    subprocess.run(["pg_dump", DB_URL, "-Fc", "-f", out], check=True)
    print(f"backup written: {out}")


if __name__ == "__main__":
    main()
