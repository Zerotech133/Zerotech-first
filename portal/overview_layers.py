# -*- coding: utf-8 -*-
"""overview 分层取数（2026-11-08 K-ORG-20261108-OVWSPLIT）。

**为什么单独成文件**：这段机器原先写在 ``portal/api_misc.py`` 里，而该文件在
``tools/check_portal_split.py`` 的冻结名单内（基线 2710 行，守则"只减不增"，
``--strict`` 下增长即判失败 —— 门禁 [budget] 就是这么红的）。原样搬出 +
api_misc 顶部 import 回流保持命名空间，与 2026-10-31 ``api_misc_home.py`` 同一先例。

**为什么不 import api_misc**：getter（get_containers / get_tasks / tcp_ok ...）都
还住在 api_misc 里，顶层 import 会与之循环。这里改成调用时 ``_m()`` 现取：
  * 无循环导入（api_misc → 本模块单向）；
  * **保住测试 seam** —— ``monkeypatch.setattr(api_misc, "_overview_heavy", ...)``
    依旧生效。若在本模块顶部 ``from api_misc import _overview_heavy``，绑定就死了，
    替身会静默失效（这正是 2026-11-08 那批 xq 假红的同一个坑）。

实测剖面（_audit_20261108/probe_overview_parts.py）：
  get_containers 8.108s（docker ps -a，run timeout=25s）
  get_tasks      4.479s（powershell schtasks，run timeout=45s）
  其余 10 项合计 0.09s（文件读取 + 4 次 TCP 探测）
即两个 shell-out 占 99%。它们原先跑在 portal_api 的**跨模块共享锁 _lock** 里，
冷启一次就把 /api/health、限流计数一起堵住，最坏 25+45=70s（不是测到的 12s）。
拆法：轻项同步返回（<0.1s）；重项只读缓存，过期由**后台线程**刷新，请求永不阻塞。
"""

from __future__ import annotations

import threading
import time


def _m():
    """取宿主模块（api_misc），运行时现取，绝不顶层 import（见模块 docstring）。"""
    import api_misc

    return api_misc


# ── 重项：容器矩阵 + 计划任务（两个外部进程，12s 量级）────────────────────
_OVERVIEW_HEAVY_TTL_S = 60.0
# 重项失败后的最小重试间隔：docker/schtasks 持续不可用时，若每个请求都触发
# 一次刷新，会变成"请求量 = 线程数"的线程风暴。5s 足够吸收突发。
_OVERVIEW_HEAVY_RETRY_S = 5.0
_OV_HEAVY = {"t": 0.0, "data": None, "building": False, "err": None, "last_try": 0.0}
_OV_HEAVY_LOCK = threading.Lock()

# ── 轻项：文件读取 + TCP 探测，实测 <0.1s ─────────────────────────────────
_OVERVIEW_LIGHT_TTL_S = 20.0
_OV_LIGHT = {"t": 0.0, "data": None}
_OV_LIGHT_LOCK = threading.Lock()


def _overview_light():
    """轻量骨架：文件读取 + TCP 探测，实测 <0.1s，20s 缓存即可同步重建。"""
    m = _m()
    services = [
        {"name": "智策页面(/quant/)", "port": 8734},
        {"name": "智策 API", "port": 8105},
        {"name": "卡片回调", "port": 9104},
        {"name": "Ollama", "port": 11434},
    ]
    for s in services:
        s["ok"] = m.tcp_ok(s["port"])
    return {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "backups": m.get_backups(),
        "alerts": m.get_alerts(),
        "disks": m.get_disks(),
        "services": services,
        "news": m.get_news(),
        "quotes": m.get_quotes(),
        "backtest": m.get_backtest(),
        "gzh": m.get_gzh(),
        "video": m.get_video(),
    }


def _overview_heavy():
    """重项：容器矩阵 + 计划任务（两个外部进程，12s 量级）。"""
    m = _m()
    return {"containers": m.get_containers(), "tasks": m.get_tasks()}


def _heavy_builder():
    """现取重项构建器 —— 让 api_misc._overview_heavy 上的替身/注入生效。"""
    return _m()._overview_heavy


def _refresh_overview_heavy():
    try:
        data = _heavy_builder()()
    except Exception as exc:  # noqa: BLE001  —— 后台线程绝不能死得无声无息
        with _OV_HEAVY_LOCK:
            _OV_HEAVY["err"] = "%s: %s" % (type(exc).__name__, str(exc)[:120])
            _OV_HEAVY["building"] = False
        return
    with _OV_HEAVY_LOCK:
        _OV_HEAVY["data"] = data
        _OV_HEAVY["t"] = time.time()
        _OV_HEAVY["err"] = None
        _OV_HEAVY["building"] = False


def _kick_overview_heavy_refresh():
    """single-flight：同时刻最多一个刷新线程，失败后 5s 内不再重试。"""
    now = time.time()
    with _OV_HEAVY_LOCK:
        if _OV_HEAVY["building"]:
            return False
        if now - _OV_HEAVY["last_try"] < _OVERVIEW_HEAVY_RETRY_S and _OV_HEAVY["err"]:
            return False
        _OV_HEAVY["building"] = True
        _OV_HEAVY["last_try"] = now
    threading.Thread(target=_refresh_overview_heavy,
                     name="overview-heavy", daemon=True).start()
    return True


def build_overview():
    """全量同步构建（原语义保留：容器/任务也同步跑，供非请求路径/测试用）。"""
    out = _overview_light()
    out.update(_heavy_builder()())
    return out


def overview_cached(now=None):
    """请求路径专用：轻项同步（带 20s 缓存）+ 重项只读缓存并后台刷新。

    返回 (payload, meta)。meta 明示重项年龄与刷新态，前端可据此显示"数据陈旧"，
    而不是把空数组当真实状态。
    """
    now = time.time() if now is None else now
    with _OV_LIGHT_LOCK:
        light, t_light = _OV_LIGHT["data"], _OV_LIGHT["t"]
        if light is not None and now - t_light <= _OVERVIEW_LIGHT_TTL_S:
            out = dict(light)
            light_age = now - t_light
        else:
            out = None
    if out is None:
        out = _overview_light()
        with _OV_LIGHT_LOCK:
            # 缓存"纯轻项"（dict 拷贝）：下面 out 会被 update 进重项，
            # 若直接引用同一个 dict，轻项缓存就会被掺入重项快照。
            _OV_LIGHT["data"] = dict(out)
            _OV_LIGHT["t"] = now
        light_age = 0.0

    with _OV_HEAVY_LOCK:
        heavy, t_heavy, err = (_OV_HEAVY["data"], _OV_HEAVY["t"],
                               _OV_HEAVY["err"])
        building = _OV_HEAVY["building"]
    fresh = heavy is not None and (now - t_heavy) <= _OVERVIEW_HEAVY_TTL_S
    if not fresh:
        building = _kick_overview_heavy_refresh() or building
    out.update(heavy if heavy is not None else {"containers": [], "tasks": []})
    meta = {
        "light_age_s": round(light_age, 1),
        "heavy_age_s": (round(now - t_heavy, 1) if heavy is not None else None),
        "heavy_ok": err is None,
        "heavy_error": err,
        "heavy_refreshing": bool(building),
        "heavy_ttl_s": _OVERVIEW_HEAVY_TTL_S,
    }
    return out, meta
