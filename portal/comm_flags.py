# -*- coding: utf-8 -*-
"""阚泽部 · 开关读取统一入口（WO-FS-152）。

为什么需要这个模块
----------------
开闸前发现一个架构陷阱：bot 由 8097 守护进程 spawn（PID 29396 ← 父 28328），
守护进程 8:03 启动后**不会重读环境变量**，它 spawn 出的新 bot 继承的是
守护进程的旧环境块。所以「设环境变量 + 重启 bot」在多层守护架构下**不生效**
——bot 起来了但依然读不到新开关，形成「以为开了其实没开」的假成功。

本模块把开关读取统一为两级：
  1. 环境变量（显式设置时优先，适合单进程/裸跑的调试场景）
  2. 本机标志文件 `portal/work/comm_flags.json`（默认兜底，改动即时、
     不依赖进程环境继承——守护链多深都读同一份文件）
  3. 都没有 → 默认值（关）

文件格式（全部可选，缺键即用默认）::

    {"intent": true, "dispatch": true, "patrol": true, "zhuge": false,
     "updated_at": "2026-10-21 16:10:00", "note": "谁在何时开的"}

纪律
----
- 读失败（文件损坏/无权限）→ 一律按「关」处理并记 last_error，**绝不
  因为读不到就当开**（fail-closed，与回调验签同判例）。
- 打开还需重启对应服务才生效（进程内已缓存的不重读）：本模块只解决
  「新进程能否读到」，不解决「旧进程热切换」。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

FLAGS_PATH = Path(__file__).resolve().parent / "work" / "comm_flags.json"

DEFAULTS = {
    "intent": False,  # COMM_INTENT_ENABLED
    "dispatch": False,  # COMM_DISPATCH_ENABLED
    "patrol": True,  # COMM_DISPATCH_PATROL
    "zhuge": False,  # COMM_ZHUGE_ENABLED
    "summary": True,  # COMM_SUMMARY_ENABLED
}

ENV_MAP = {
    "intent": "COMM_INTENT_ENABLED",
    "dispatch": "COMM_DISPATCH_ENABLED",
    "patrol": "COMM_DISPATCH_PATROL",
    "zhuge": "COMM_ZHUGE_ENABLED",
    "summary": "COMM_SUMMARY_ENABLED",
}

_TRUTHY = ("1", "true", "yes", "on")
_last_error = None


def flags_path():
    p = os.environ.get("COMM_FLAGS_PATH")
    return Path(p) if p else FLAGS_PATH


def read_flags():
    """→ (dict, error)。读失败返回 ({}, 原因)，调用方按默认（关）处理。"""
    global _last_error
    p = flags_path()
    if not p.exists():
        _last_error = None
        return {}, None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("flags 不是对象")
        _last_error = None
        return data, None
    except (OSError, ValueError) as ex:
        _last_error = "%s: %s" % (type(ex).__name__, ex)
        return {}, _last_error


def get(name):
    """→ bool。env 显式设置优先，其次文件，最后默认。"""
    env_key = ENV_MAP.get(name)
    if env_key:
        raw = os.environ.get(env_key)
        if raw is not None and str(raw).strip() != "":
            return str(raw).strip().lower() in _TRUTHY
    data, _err = read_flags()
    if name in data:
        return bool(data[name])
    return bool(DEFAULTS.get(name, False))


def status():
    """→ 全量状态快照（供核对工具与排障）。"""
    data, err = read_flags()
    return {
        "path": str(flags_path()),
        "exists": flags_path().exists(),
        "file": data,
        "read_error": err,
        "effective": {k: get(k) for k in DEFAULTS},
        "source": {
            k: (
                "env"
                if (
                    ENV_MAP.get(k) and str(os.environ.get(ENV_MAP[k], "")).strip() != ""
                )
                else ("file" if k in data else "default")
            )
            for k in DEFAULTS
        },
    }


def write_flags(updates, note=""):
    """合并写入（保留未提及的键）。→ (ok, error)。"""
    data, _err = read_flags()
    if not isinstance(data, dict):
        data = {}
    data.update({k: bool(v) for k, v in updates.items()})
    import time

    data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    if note:
        data["note"] = note
    p = flags_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(p)  # 原子替换，避免读到半截文件
        return True, None
    except OSError as ex:
        return False, "%s: %s" % (type(ex).__name__, ex)
