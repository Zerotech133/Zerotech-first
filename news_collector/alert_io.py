# -*- coding: utf-8 -*-
r"""告警链路 IO 原语（E1 alert_fail 可观测化）。

BOM 嗅探范式：读前 4 字节判定编码，杜绝 UTF-16 日志按 utf-8 读出乱码
（实伤判例：PS 5.1 `2>` 重定向产物 C:\Temp\*_err.log 为 UTF-16LE）。
ASCII 状态日志范式：单行管道分隔、写前 ascii 消毒，对齐 K208/K220 惯例。
原子写范式：tempfile 同目录落盘 + os.replace（alert_watch._save_state 判例）。
"""

import json
import os
import tempfile
import logging


def sniff_encoding(path):
    """读前 4 字节判定编码：utf-16 / utf-8-sig / utf-8。"""
    with open(path, "rb") as f:
        head = f.read(4)
    if head[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return "utf-16"
    if head[:3] == b"\xef\xbb\xbf":
        return "utf-8-sig"
    return "utf-8"


def sniff_read_text(path, errors="replace"):
    """BOM 嗅探后解码全文（utf-16 由 BOM 自适配大小端）。"""
    enc = sniff_encoding(path)
    with open(path, "r", encoding=enc, errors=errors) as f:
        return f.read()


def append_ascii_log(path, line):
    """ASCII 状态日志瞬时追加：单行 + 换行；非 ASCII 字符降级为 '?'。"""
    safe = line.encode("ascii", "replace").decode("ascii")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="ascii", errors="replace") as f:
        f.write(safe.rstrip("\r\n") + "\n")


def atomic_write_json(path, obj, indent=None):
    """JSON 原子写：同目录 tempfile + os.replace，杜绝半截 JSON。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=os.path.dirname(path), prefix=os.path.basename(path) + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=indent)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "news_collector/alert_io.py", exc)
        raise
