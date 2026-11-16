# -*- coding: utf-8 -*-
"""审查 95e9f5dfa 时发现的可疑点验证（2026-11-16）。

只验两个，不重复原提交已有的三条断言（路径一致 / 目录缺 -> 抛错 / 无文件 -> None）：
  V1  json.load(open(p)) 未关闭文件句柄 -> ResourceWarning / 句柄累积
  V2  day 为空或非法时静默返回 None（而不是抛错）
"""
import importlib.util
import json
import os
import sys
import tempfile
import warnings

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TARGET = os.path.join(ROOT, "quant_backtest", "paper_trade.py")


def _load(deliver):
    os.environ["ZB_DELIVERABLES"] = deliver
    name = "_pt_audit_%d" % len(sys.modules)
    spec = importlib.util.spec_from_file_location(name, TARGET)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


def _write_sup(d, day, action="suppress"):
    j = {
        "schema": "preopen_suppressed/v1",
        "action": action,
        "exec_day": day,
        "consumers": ["quant_backtest/paper_trade.py"],
    }
    p = os.path.join(d, "preopen_suppressed_%s.json" % day)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(j, fh)
    return p


def test_v1_file_handle_leak():
    """V1：load_suppression 用 json.load(open(...)) —— 文件对象未关闭。

    ResourceWarning 需要 -W error::ResourceWarning 或 gc 才会暴露；
    这里直接检查「读完后该文件不在 gc 的待关闭列表里」不好做，
    改用更直接的办法：读取时把 open 打桩，记录是否被 close。
    """
    d = tempfile.mkdtemp()
    day = "20261116"
    _write_sup(d, day)
    m = _load(d)

    import builtins
    real_open = builtins.open
    opened = []

    class _Tracked:
        def __init__(self, fh):
            self._fh = fh
            self.closed = False

        def __getattr__(self, n):
            return getattr(self._fh, n)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.closed = True
            self._fh.close()
            return False

        def close(self):
            self.closed = True
            self._fh.close()

    def _patched(path, *a, **k):
        fh = real_open(path, *a, **k)
        if str(path).endswith(".json"):
            t = _Tracked(fh)
            opened.append(t)
            return t
        return fh

    builtins.open = _patched
    try:
        m.load_suppression(day)
    finally:
        builtins.open = real_open

    leaked = [t for t in opened if not t.closed]
    assert not leaked, (
        "load_suppression 读取抑制令后未关闭文件句柄（%d 个未关）："
        "应改成 with open(...) as fh: json.load(fh)" % len(leaked))


def test_v2_empty_day_raises_not_silent_none():
    """V2：day 为空/非法时应抛错，而不是拼出 preopen_suppressed_None.json 后返回 None。

    修复前：目录存在、day=None ⇒ p=preopen_suppressed_None.json ⇒ isfile False
            ⇒ isdir(DELIVERABLES) True ⇒ return None（与「当日无抑制」不可区分）
    修复后：入口校验 day 为 8 位日期串，非法即 RuntimeError。
    """
    import pytest

    d = tempfile.mkdtemp()
    m = _load(d)

    for bad in (None, "", "2026-11-16", "2026111", "abcdefgh"):
        with pytest.raises(RuntimeError, match="day 必须是 8 位日期串"):
            m.load_suppression(bad)


def test_v2b_valid_day_still_works():
    """V2 的反面：合法 day 不能被新校验误伤（含 int 形态，历史调用点可能传 int）。"""
    d = tempfile.mkdtemp()
    day = "20261116"
    _write_sup(d, day)
    m = _load(d)

    assert m.load_suppression(day) is not None          # 字符串
    assert m.load_suppression(int(day)) is not None     # int（str() 后仍是 8 位数字）

def test_v3_corrupt_json_raises_runtime_error():
    """V3：抑制令文件存在但内容损坏时，应抛 RuntimeError 并指出是哪个文件。

    修复前：json.JSONDecodeError 直接传播 —— 方向对（失败关闭），
            但报错只说「decode 失败」，看不出是哪份抑制令坏了。
    """
    import pytest

    d = tempfile.mkdtemp()
    day = "20261116"
    p2 = os.path.join(d, "preopen_suppressed_%s.json" % day)
    with open(p2, "w", encoding="utf-8") as fh:
        fh.write('{"schema": "preopen_suppressed/v1", "action": ')   # 截断

    m = _load(d)
    with pytest.raises(RuntimeError, match="抑制令文件损坏"):
        m.load_suppression(day)


def test_v3b_non_utf8_file_raises_runtime_error():
    """V3b：文件不是 UTF-8（UnicodeDecodeError）同样被包装。"""
    import pytest

    d = tempfile.mkdtemp()
    day = "20261116"
    p2 = os.path.join(d, "preopen_suppressed_%s.json" % day)
    with open(p2, "wb") as fh:
        fh.write(b"\xff\xfe\x00\x00 not utf-8")

    m = _load(d)
    with pytest.raises(RuntimeError, match="抑制令文件损坏"):
        m.load_suppression(day)


def test_v3c_os_error_not_wrapped():
    """V3c：权限/占用问题（OSError）不应被包装成「文件损坏」，语义不同。"""
    import pytest

    d = tempfile.mkdtemp()
    day = "20261116"
    _write_sup(d, day)
    m = _load(d)

    import builtins
    real_open = builtins.open

    def _deny(path, *a, **k):
        if str(path).endswith(".json"):
            raise PermissionError(13, "denied")
        return real_open(path, *a, **k)

    builtins.open = _deny
    try:
        with pytest.raises(PermissionError):
            m.load_suppression(day)
    finally:
        builtins.open = real_open
