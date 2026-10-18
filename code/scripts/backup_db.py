"""V2 数据库备份脚本（使用 pg_dump）。"""
from __future__ import annotations

import os
import subprocess
from datetime import datetime

DB_URL = os.getenv("DB_MAIN_URL", "postgresql://zhice:zhice_pass@localhost:5432/zhice_os_v2")


def main() -> None:
    out = f"backup/zhice_os_v2_{datetime.now().strftime('%Y%m%d_%H%M%S')}.dump"
    os.makedirs("backup", exist_ok=True)
    subprocess.run(["pg_dump", DB_URL, "-Fc", "-f", out], check=True)
    print(f"backup written: {out}")


if __name__ == "__main__":
    main()
