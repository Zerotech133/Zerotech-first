# -*- coding: utf-8 -*-
"""仓库根 pytest 配置（2026-10-27 WO-AUDIT4-FLAKY）。

Windows 中文 locale 下，子进程 stdout 默认按 cp936 编码；pytest 用例若用
`subprocess.run(..., encoding="utf-8")` 捕获 CLI 输出，会因解码失败得到
`stdout=None`（TypeError: JSON object must be str...）。全量套件曾因此 12 红。

这里在测试进程最早期统一给子进程设置 UTF-8 输出（仅测试进程，不影响生产）：
  * PYTHONIOENCODING=utf-8 —— 子进程 stdout/stderr 用 UTF-8；
  * 用 setdefault 不覆盖用户显式设置。
"""
from __future__ import annotations

import os

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
