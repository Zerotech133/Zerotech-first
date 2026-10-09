"""受管读的同根嵌套复用与跨仓有序发布保护；不访问配置或远端。"""
from contextlib import contextmanager, ExitStack
from functools import wraps
import os
from pathlib import Path
import threading

from warehouse_commit import PublicationPending, publication_guard


_READ_STATE = threading.local()


def resource_paths(resources):
    """规范实际仓根；不存在仓不创建锁文件，保持旧读层空仓语义。"""
    paths = {}
    for value in resources:
        if not value:
            continue
        path = Path(value).resolve()
        if path.is_dir():
            paths[os.path.normcase(str(path))] = path
    return [paths[key] for key in sorted(paths)]


@contextmanager
def read_resources(resources):
    """一次锁住所有候选仓，嵌套同根共享原锁；禁止倒序追加锁。"""
    paths = resource_paths(resources)
    held = getattr(_READ_STATE, 'held', None)
    if held is None:
        held = _READ_STATE.held = {}
    new = [p for p in paths if os.path.normcase(str(p)) not in held]
    if held and new and os.path.normcase(str(new[0])) < max(held):
        raise RuntimeError('嵌套读取必须在外层按顺序锁定全部候选仓')
    acquired = []
    with ExitStack() as stack:
        try:
            for path in new:
                stack.enter_context(publication_guard(path, mode='read'))
                key = os.path.normcase(str(path))
                held[key] = path
                acquired.append(key)
            yield paths
        finally:
            for key in acquired:
                del held[key]


def guarded_read(resources):
    """动态仓根在每次调用时解析，整个返回值必须在保护范围内物化。"""
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            with read_resources(resources(*args, **kwargs)):
                return function(*args, **kwargs)
        return wrapped
    return decorate


def publication_stamp(resource):
    """锁内读取轻量版本：原子替换会改变表目录或 manifest/db 的元数据。

    仅枚举仓根直接成员，不扫描逐码文件；未接管的原地写入不在保证范围。
    """
    root = Path(resource)
    values = []
    with os.scandir(root) as entries:
        for entry in entries:
            if entry.name.startswith('.warehouse-publication'):
                continue
            try:
                stat = entry.stat()
                values.append((entry.name, stat.st_mtime_ns, stat.st_size))
            except FileNotFoundError:
                values.append((entry.name, None, None))
    return tuple(sorted(values))
