# -*- coding: utf-8 -*-
"""统一原子写：唯一临时名 + os.replace + 退避重试（P2-c）。

为什么需要：仓库里有 170+ 处「path + ".tmp" 再 os.replace」。单写者时无害，一旦
多进程/多线程写同一产物（网格回测、并行抓取、日更与手工重跑撞车），临时名相同就会
互相覆盖或删除，表现为 PermissionError 或静默写出半截文件。

实证（2026-11-07，portfolio_sim._atomic_write，8 线程 × 60 次写同一文件）：
    固定名 path+".tmp"      成功  26/480   失败 454
    path.<pid>.tmp          成功 262/480   失败 218   ← 只隔离了进程
    mkstemp 唯一名          成功 480/480   失败   0

用法：
    with atomic_io.replacing(path) as f:   # 文本模式，正常退出即原子替换
        json.dump(obj, f)
    atomic_io.write_text(path, text)

注意：退出时若发生异常，临时文件会被删除，目标文件保持原样（不写半截）。
"""
import contextlib
import errno
import os
import tempfile
import time


def _ensure_dir(path):
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
    return d


@contextlib.contextmanager
def replacing(path, encoding="utf-8", newline="", retries=6, mode="w"):
    """在唯一临时文件上写，退出时 os.replace 到目标；异常则清理临时文件。

    retries：Windows 上目标被扫描/占用时 os.replace 会短暂失败，退避重试。
    """
    d = _ensure_dir(path)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=os.path.basename(path) + ".",
                               suffix=".tmp")
    os.close(fd)
    try:
        with open(tmp, mode, encoding=encoding, newline=newline) as f:
            yield f
        last = None
        for k in range(retries):
            try:
                os.replace(tmp, path)
                return
            except OSError as e:                      # PermissionError 是 OSError 子类
                last = e
                if e.errno not in (errno.EACCES, errno.EPERM, errno.EBUSY):
                    raise
                time.sleep(0.3 * (k + 1))
        raise last
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass


def write_text(path, text, **kw):
    with replacing(path, **kw) as f:
        f.write(text)
    return path


def write_bytes(path, data):
    with replacing(path, mode="wb", encoding=None, newline=None) as f:
        f.write(data)
    return path
