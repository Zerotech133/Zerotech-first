# -*- coding: utf-8 -*-
"""CLI 入口：``python -m zhice_os_v2.infrastructure.data_quality``（只读 dry-run）。"""
from __future__ import annotations

from zhice_os_v2.infrastructure.data_quality.age_gate import _main

if __name__ == "__main__":
    raise SystemExit(_main())
