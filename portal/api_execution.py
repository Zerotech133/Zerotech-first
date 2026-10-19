# -*- coding: utf-8 -*-
"""R1 执行健康总线（联动层一期 T1，K-ORG-112 三层只读投影）。

数据面(Windows Scheduler + 日志 mtime) → 只读总线(8091 /api/execution/health)
→ 转调消费(Hermes 8787 console 卡)。本模块设计为**纯 stdlib、零 portal 内部
依赖**：R3 巡检脚本(tools/execution_watchdog.py)可直接
``sys.path.insert(0, portal); import api_execution`` 复用同一份轮询逻辑
（force=True 绕缓存拿新鲜值），一份逻辑两个消费方，不复制粘贴。

红线：全部只读投影 + manifest 白名单；fail-visible（总线挂 ≠ 执行面挂——
PowerShell 轮询失败输出 ps_ok:false 而非编造数据，HTTP 仍 200）；零新增依赖
（纯 stdlib + OS 自带 powershell.exe）。

Windows 编码判例（§8.4）：
  ① Python subprocess 捕获 PS 输出：bytes 先 utf-8 后 gbk 回退解码
    （PS 命令里已强制 [Console]::OutputEncoding=UTF8，gbk 只是回退保险）；
  ② 任务名保持 ASCII 与中文混合，PS 命令行参数走 CreateProcessW Unicode 传递；
  ③ 读既有日志用 errors="replace"（api_capability _read_jsonl 判例），
    fail_scan 匹配同样逐行 replace 后再 regex。
"""

import copy
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

# ── 常量与模块级状态 ──────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parent.parent

#: manifest 白名单路径（portal/execution_manifest.json，加任务=加一行 JSON）
MANIFEST_PATH = _REPO_ROOT / "portal" / "execution_manifest.json"

#: 8091 侧数据新鲜度 TTL（秒）：console 30s 自动刷新 + 人工连点不叠加 PS 开销
CACHE_TTL = 30

#: 良性返回码表（沿 ops/health_check.py L101 判例）：
#: 0=成功 267009=0x41301 运行中 267011=0x41303 尚未跑 267014=0x41306 被终止。
#: 4294967295(0xFFFFFFFF) 等不在表内，必报 bad。
BENIGN_RC = {0, 267009, 267011, 267014}

#: 0x41303 尚未跑：配合 1999-11-30 LastRunTime 哨兵判定 never（从未运行）
_RC_NOT_RUN = 267011
#: 1999 哨兵年（PS LastRunTime 从未运行时为 1999-11-30 本地投影）
_SENTINEL_YEAR = 1999

_PS = "powershell.exe"

#: 30s TTL 缓存（Lock 保护；R3 force=True 显式绕过）
_CACHE = {"ts": 0.0, "data": None}
_LOCK = threading.Lock()


# ── 后台预热线程（D1 处置 2026-10-18）────────────────────────────────────
#
# 缺陷：冷缓存首个请求同步调 poll_tasks（PS Get-ScheduledTask 冷启实测
# 48.7s），console.html 30s 轮询 timeout=60s 逼近超时。
# 处置：懒启动 daemon 线程按 CACHE_TTL-5s 周期锁内刷新 _CACHE——稳态下
# 所有前台请求命中温缓存（毫秒级），仅进程冷启动的首个请求慢一次。
# 红线：前台 build_execution_health 对外签名/返回契约/深拷贝语义零变化；
# 后台刷新异常吞掉记 stderr（fail-open：后台失败不影响前台同步兜底）。

#: 预热提前量：后台每 CACHE_TTL-_WARM_SLACK 秒刷一次，保证 _CACHE 的 ts
#: 恒在 TTL 内。下限 0.05s 供测试 monkeypatch CACHE_TTL 小值时仍可运行。
_WARM_SLACK = 5.0

#: 环境变量闸：置 "0" 禁用预热线程（pytest / R3 脚本 import 场景防误启）。
#: 未设置或非 "0" = 启用（生产 portal_api 默认启用，本文件不依赖 portal_api）。
_WARMER_ENV = "PORTAL_HEALTH_WARMER"

#: 预热线程句柄与停机旗标（懒启动一次；测试夹具复位）
_WARMER_THREAD = None
_WARMER_STOP = threading.Event()


def _warm_interval():
    """后台预热间隔（秒）：CACHE_TTL-5，下限 0.05s 兜底。"""
    return max(CACHE_TTL - _WARM_SLACK, 0.05)


def _warmer_enabled():
    """env 闸判定（_WARMER_ENV 置 "0" 时禁用）。"""
    return os.environ.get(_WARMER_ENV, "1") != "0"


def _build_payload():
    """构建端点载荷（纯函数：不持锁、不碰 _CACHE）。

    manifest 坏/PS 挂走 fail-visible 字段，不抛不编造。
    T-P2（2026-10-19）：由 _refresh_locked 拆出——后台刷新（预热线程/SWR
    触发）全程不持 _LOCK 构建，仅在换缓存瞬间短暂持锁，杜绝 PS 慢放大成
    前台请求阻塞（T-P1 诊断 A3）。
    """
    now = datetime.now()
    tasks_cfg, manifest_ok, manifest_err = load_manifest()
    names = [c["name"] for c in tasks_cfg]
    if manifest_ok and names:
        rows, ps_ok, ps_error, elapsed_ms = poll_tasks(names)
    else:
        # manifest 缺失/坏 JSON：没有白名单就没有轮询面，宁少勿假
        rows, ps_ok, ps_error, elapsed_ms = {}, True, None, 0

    entries = []
    for cfg in tasks_cfg:
        try:
            entries.append(classify(cfg, rows.get(cfg["name"]), now))
        except Exception as e:  # noqa: BLE001  单行坏不拖垮整卡
            entries.append({
                "name": cfg.get("name", "?"),
                "expected_cadence": "?", "last_run": None,
                "last_result": None, "result_ok": None,
                "log_mtime": None, "stale_min": None, "in_system": None,
                "fail_scan_hit": False,
                "status": "never",
                "error": "classify 异常: %s" % e,
            })

    return {
        "read_only": True,
        "generated_at": now.isoformat(timespec="seconds"),
        "manifest_ok": manifest_ok,
        "manifest_path": "portal/execution_manifest.json",
        "manifest_count": len(entries),
        "manifest_error": manifest_err,
        "ps_ok": ps_ok,
        "ps_error": ps_error,
        "ps_elapsed_ms": elapsed_ms,
        "tasks": entries,
        "summary": _summary_of(entries),
    }


def _refresh_locked():
    """锁内全量刷新并写 _CACHE（前台冷启同步路径与 force 专用）。

    调用方必须已持有 _LOCK（单飞语义：并发冷启前台请求串行化，
    一次冷启只付一次 PS 开销）。T-P2 起后台刷新不再走本函数（改
    _refresh_background 不持锁构建）。
    """
    payload = _build_payload()
    _CACHE["ts"] = time.monotonic()
    _CACHE["data"] = payload


#: 后台刷新单飞闸：同一时刻最多一个线程在构建载荷（非 _LOCK，构建不阻塞前台）
_REFRESHING = threading.Lock()


def _refresh_background():
    """后台单飞刷新（SWR 兜底，T-P2）。

    构建全程不持 _LOCK（PS 轮询再慢也不阻塞前台）；完成后短暂持 _LOCK
    原子换 _CACHE。fail-open 判例：异常吞掉记 stderr，缓存保持旧值。
    已有线程在刷时本调用静默让位（单飞，省一次 PS 开销）。
    """
    if not _REFRESHING.acquire(blocking=False):
        return
    try:
        payload = _build_payload()
        with _LOCK:
            _CACHE["ts"] = time.monotonic()
            _CACHE["data"] = payload
    except Exception as e:  # noqa: BLE001  fail-open：后台失败不冒泡
        sys.stderr.write(
            "[api_execution] 后台刷新失败(前台返回旧缓存不受影响): "
            "%s: %s\n" % (type(e).__name__, e))
        sys.stderr.flush()
    finally:
        _REFRESHING.release()


def _kick_background_refresh():
    """触发一次后台单飞刷新（立即返回，不阻塞调用方）。"""
    threading.Thread(target=_refresh_background,
                     name="execution-health-swr", daemon=True).start()


def _warmer_loop(stop):
    """后台预热主循环（daemon）：睡 _warm_interval() → 后台单飞刷新。

    fail-open 判例：任何一轮刷新异常在 _refresh_background 内吞掉记
    stderr，绝不冒泡终止线程——前台遇冷缓存仍走原有同步 poll 兜底，
    后台失败只是退回旧行为。
    stop 置位即退出（pytest 夹具清理 / 预留优雅停机）。
    """
    while not stop.wait(_warm_interval()):
        _refresh_background()


def _ensure_warmer_started():
    """懒启动防重入：首个 build_execution_health 调用者触发，仅 spawn 一次。

    调用方持有 _LOCK（本函数只做句柄检查与 Thread.start，无阻塞操作）；
    env 闸关闭时静默跳过（测试/巡检脚本 import 场景防误启真线程）。
    """
    global _WARMER_THREAD
    if not _warmer_enabled():
        return
    if _WARMER_THREAD is not None and _WARMER_THREAD.is_alive():
        return
    _WARMER_STOP.clear()
    _WARMER_THREAD = threading.Thread(
        target=_warmer_loop, args=(_WARMER_STOP,),
        name="execution-health-warmer", daemon=True)
    _WARMER_THREAD.start()


# ── manifest 加载 ─────────────────────────────────────────────────────────

def load_manifest():
    """加载白名单 manifest。

    Returns:
        tuple[list[dict], bool, str | None]: (任务配置列表, manifest_ok, 错误说明)。
        manifest 缺失/坏 JSON 时 manifest_ok=False、列表为空——宁少勿假，
        不编造任务行。
    """
    try:
        with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return [], False, "manifest 不存在: %s" % MANIFEST_PATH
    except (json.JSONDecodeError, OSError) as e:
        return [], False, "manifest 读取/解析失败: %s" % e
    tasks = data.get("tasks")
    if not isinstance(tasks, list):
        return [], False, "manifest 缺少 tasks 数组"
    # 逐行容错：缺 name 的坏行跳过（宁少勿假），不拖垮整份清单
    out = []
    for t in tasks:
        if isinstance(t, dict) and t.get("name"):
            out.append(t)
    return out, True, None


# ── PowerShell 批量轮询 ───────────────────────────────────────────────────

def _ps_query(names):
    """单次批量轮询 Windows 计划任务（COM Schedule.Service 单连接，T-P2 治本）。

    历史（≤2026-10-19，T-P1 诊断 A1/A2/A5）：逐任务 foreach
    ``Get-ScheduledTask -TaskName $n | Get-ScheduledTaskInfo``——22 任务
    × 每次独立 CIM/WMI 往返，本机劣化环境实测 51.5s，逼近 subprocess
    timeout=60（余量仅 ~8.5s，轻微劣化即击穿转 ps_ok:false）。

    处置：一次 PowerShell 进程内建 **COM Schedule.Service 单连接**，
    递归枚举任务库全部文件夹（GetTasks(1) 含隐藏任务），直接读
    IRegisteredTask.LastRunTime / LastTaskResult——零 CIM/WMI 往返，
    22 任务由 ~51.5s 压到个位数秒预期。LastTaskResult 是 COM int32
    （0xC000013A 等高位码以负值到达），PS 侧透传 int64，Python 侧统一
    ``& 0xFFFFFFFF`` 归一——注意 PS 字面量 0xFFFFFFFF 会被解析为 Int32
    -1（-band 恒等的坑），掩码必须在 Python 侧做。属性语义与
    Get-ScheduledTaskInfo 同源（LastRunTime 未跑为 1999-11-30 哨兵投影），
    classify 判定不变。

    Returns:
        dict[str, dict]: {任务名: {"found": bool, "last_run": str|None,
        "last_result": int|None}}。任务不存在 → found=False（宁报勿瞒，
        上层 classify 判 never）。

    Raises:
        Exception: subprocess 任何失败（超时/PS 不存在/非零退出/坏 JSON）——
        由 poll_tasks 统一转 ps_ok:false，不编造数据。
    """
    # 任务名单引号转义（PS 字符串字面量）；@{} 条目分隔符是分号
    want = "; ".join("'%s'=$true" % n.replace("'", "''") for n in names)
    ps = (
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
        "$ProgressPreference='SilentlyContinue'; "
        "$want = @{" + want + "}; "
        "$found = @{}; "
        "$svc = New-Object -ComObject Schedule.Service; "
        "$svc.Connect() | Out-Null; "
        "function Walk($f) { "
        "foreach ($t in $f.GetTasks(1)) { "
        "$n = $t.Name; "
        "if ($want.ContainsKey($n) -and -not $found.ContainsKey($n)) "
        "{ $found[$n] = $t } }; "
        "foreach ($sub in $f.GetFolders(0)) { Walk $sub } }; "
        "Walk ($svc.GetFolder('\\')); "
        "$out = foreach ($n in $want.Keys) { "
        "$t = $found[$n]; "
        "if ($null -ne $t) { "
        "$lr = $t.LastRunTime; "
        "$lrs = if ($lr -is [datetime]) "
        "{ $lr.ToString('yyyy-MM-dd HH:mm:ss') } else { '' }; "
        "[pscustomobject]@{ name = $n; found = $true; last_run = $lrs; "
        "result = [int64]$t.LastTaskResult } } "
        "else { [pscustomobject]@{ name = $n; found = $false; "
        "last_run = ''; result = 0 } } }; "
        "$out | ConvertTo-Json -Depth 2"
    )
    proc = subprocess.run(
        [_PS, "-NoProfile", "-NonInteractive", "-Command", ps],
        capture_output=True, timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "powershell exit=%s stderr=%s" % (
                proc.returncode,
                _decode(proc.stderr)[:200],
            ))
    rows = json.loads(_decode(proc.stdout))
    if isinstance(rows, dict):  # 单任务时 ConvertTo-Json 输出对象而非数组
        rows = [rows]
    out = {}
    for r in rows:
        last_run = r.get("last_run") or None
        result = r.get("result")
        # 0xFFFFFFFF 归一（防御纵深）：PS 侧 [uint32] 转换为主，Python 侧
        # 再掩码一次——COM int32 负值（0xFFFFFFFF→-1）即使漏过也不误判
        masked = int(result) & 0xFFFFFFFF if isinstance(result, (int, float)) \
            else None
        out[r.get("name")] = {
            "found": bool(r.get("found")),
            "last_run": last_run,
            "last_result": masked,
        }
    return out


def _decode(b):
    """§8.4①：PS 输出 bytes 先 utf-8 后 gbk 回退解码。"""
    for enc in ("utf-8", "gbk"):
        try:
            return b.decode(enc)
        except (UnicodeDecodeError, AttributeError):
            continue
    return b.decode("utf-8", errors="replace")


def poll_tasks(names, force=False):
    """批量轮询任务状态（R3 消费入口：force=True 绕 8091 缓存拿新鲜值）。

    Returns:
        tuple[dict, bool, str | None, int]:
        (rows, ps_ok, ps_error, elapsed_ms)。ps_ok=False 时 rows 为空 dict，
        绝不编造数据（fail-visible）。
    """
    if not names:
        return {}, True, None, 0
    t0 = time.monotonic()
    try:
        rows = _ps_query(names)
        return rows, True, None, int((time.monotonic() - t0) * 1000)
    except Exception as e:  # noqa: BLE001  fail-visible：失败如实上报
        return {}, False, "%s: %s" % (type(e).__name__, e), int(
            (time.monotonic() - t0) * 1000)


# ── 日志 mtime 与 fail_scan ───────────────────────────────────────────────

def _resolve_log_path(p):
    """仓库相对路径（C:\\aios junction 语义）解析；绝对路径原样。"""
    if not p:
        return None
    pp = Path(p)
    return pp if pp.is_absolute() else _REPO_ROOT / pp


def _fmt_mtime(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else None


def _log_state(cfg):
    """读 log_path 的 mtime 与 fail_scan 命中情况（读日志 errors=replace 判例）。

    Returns:
        tuple[str | None, bool]: (log_mtime 格式化串或 None, fail_scan_hit)。
    """
    log_mtime = None
    fail_scan_hit = False
    lp = _resolve_log_path(cfg.get("log_path"))
    if lp is not None:
        try:
            if lp.is_file():
                log_mtime = _fmt_mtime(lp.stat().st_mtime)
        except OSError:
            pass
    fs = cfg.get("fail_scan") or {}
    fs_log = fs.get("log")
    pattern = fs.get("pattern")
    if fs_log and pattern:
        fpath = _resolve_log_path(fs_log)
        window_h = float(fs.get("window_hours") or 24)
        try:
            if fpath is not None and fpath.is_file():
                mtime = fpath.stat().st_mtime
                if (time.time() - mtime) <= window_h * 3600:
                    with open(fpath, "r", encoding="utf-8",
                              errors="replace") as f:
                        tail = f.readlines()[-500:]
                    fail_scan_hit = any(pattern in ln for ln in tail)
        except OSError:
            pass
    return log_mtime, fail_scan_hit


# ── 状态机 classify ───────────────────────────────────────────────────────

def classify(cfg, ps_row, now):
    """单任务状态判定（优先级：disabled > fail > never > stale > ok）。

    Args:
        cfg: manifest 条目 dict。
        ps_row: poll_tasks 行（{"found", "last_run", "last_result"}）；
            None = manifest 有行但 PS 未返回（按 never 宁报勿瞒）。
        now: datetime.now() 供测试注入。

    Returns:
        dict: 端点 tasks[] 单条目（契约见设计文档 §3）。
    """
    name = cfg["name"]
    cadence = int(cfg.get("cadence_minutes") or 0)
    grace = int(cfg.get("grace_minutes") or max(cadence // 2, 0))
    enabled = bool(cfg.get("enabled", True))
    log_mtime, fail_scan_hit = _log_state(cfg)

    entry = {
        "name": name,
        "expected_cadence": "每 %d 分钟" % cadence,
        "last_run": None,
        "last_result": None,
        "result_ok": True,
        "log_mtime": log_mtime,
        "stale_min": None,
        "in_system": False,
        "fail_scan_hit": fail_scan_hit,
        "status": "disabled" if not enabled else "never",
    }
    if not enabled:
        return entry

    if not ps_row or not ps_row.get("found"):
        # manifest 内任务在系统中不存在：宁报勿瞒，显示 never 灰态
        return entry

    entry["in_system"] = True
    entry["last_result"] = ps_row.get("last_result")
    entry["result_ok"] = entry["last_result"] in BENIGN_RC

    last_run_dt = None
    raw_last = ps_row.get("last_run")
    if raw_last:
        try:
            last_run_dt = datetime.strptime(str(raw_last), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            last_run_dt = None
    if last_run_dt is not None:
        entry["last_run"] = last_run_dt.strftime("%Y-%m-%d %H:%M:%S")
        if last_run_dt > now:
            last_run_dt = now  # 时钟漂移防御：未来时间不算负陈旧

    # 1) fail：rc 非良性必报（fail 优先级高于 never——267011 良性不会进此支）
    if not entry["result_ok"]:
        entry["status"] = "fail"
        return entry
    # 2) never：267011 + 1999 哨兵 = 从未运行（影子链首跑前真实存在）
    if (entry["last_result"] == _RC_NOT_RUN
            and (last_run_dt is None or last_run_dt.year <= _SENTINEL_YEAR)):
        entry["status"] = "never"
        return entry
    # 3) stale：now - last_run > cadence + grace（恰等于边界不判，+1 判）
    if last_run_dt is not None:
        stale_min = int((now - last_run_dt).total_seconds() // 60)
        entry["stale_min"] = stale_min
        if cadence > 0 and stale_min > cadence + grace:
            entry["status"] = "stale"
            return entry
    # 4) ok
    entry["status"] = "ok"
    return entry


# ── 聚合端点载荷 ──────────────────────────────────────────────────────────

_WORST_ORDER = ("fail", "stale", "never")  # 红 > 黄 > 灰


def _summary_of(entries):
    """汇总 enabled 任务的四态计数与 worst（disabled 不入红绿）。"""
    summary = {"ok": 0, "fail": 0, "stale": 0, "never": 0, "disabled": 0,
               "worst": None}
    for e in entries:
        if e["status"] in summary and e["status"] != "worst":
            summary[e["status"]] += 1
    for w in _WORST_ORDER:
        if summary[w] > 0:
            summary["worst"] = w
            break
    return summary


def build_execution_health(force=False):
    """构建 GET /api/execution/health 只读载荷（30s TTL 缓存 + SWR + 后台预热）。

    fail-visible 契约：manifest 坏 → manifest_ok:false；PS 挂 → ps_ok:false。
    两者都不改 HTTP 状态码（HTTP 500 只留给未捕获异常）。

    D1 处置（2026-10-18）：首次调用懒启动 daemon 预热线程（env 闸可禁），
    周期刷新 _CACHE——稳态下本函数全部温缓存命中，仅进程冷启动的首个
    请求付一次同步 PS 轮询。对外签名/返回契约/深拷贝语义零变化。

    T-P2 处置（2026-10-19，K-PORTAL-20261019-HEALTH，stale-while-revalidate）：
    缓存过期后前台**立即返回旧值**（新增 stale:true / cache_age_sec 标记，
    其余载荷字段契约不变），并触发后台单飞刷新（构建不持 _LOCK）——
    杜绝 T-P1 诊断 A3 的锁放大（预热线程持锁刷 51.5s 阻塞前台）。
    仅冷缓存（无任何旧值）与 force 保留同步轮询兜底。

    Args:
        force: True 绕缓存强制新鲜轮询（R3 巡检专用）。

    Returns:
        dict: 端点契约载荷（read_only 恒为 true；缓存过期命中时附加
        stale:true 与 cache_age_sec）。
    """
    with _LOCK:
        data = _CACHE["data"]
        age = time.monotonic() - _CACHE["ts"]
        if force or data is None:
            # force 显式新鲜 / 冷缓存首请求：同步单飞兜底（原语义，并发
            # 前台请求串行化，一次冷启只付一次 PS 开销）
            _refresh_locked()
            _ensure_warmer_started()
            # 返回深拷贝：调用方（R3 watchdog 的演练注入 _apply_injections）会
            # 原地改 tasks 条目——dict() 浅拷贝与缓存共享嵌套对象，会污染缓存。
            # （2026-10-18 审查收口：浅拷贝缓存污染隐患）
            return copy.deepcopy(_CACHE["data"])
        _ensure_warmer_started()
        if age < CACHE_TTL:
            return copy.deepcopy(data)
        stale_copy = copy.deepcopy(data)
    # SWR（T-P2）：缓存过期前台零等待返回旧值，后台单飞刷新补新鲜值
    _kick_background_refresh()
    stale_copy["stale"] = True
    stale_copy["cache_age_sec"] = int(age)
    return stale_copy


# ── 8091 Handler mixin ────────────────────────────────────────────────────

class Handler:
    """portal_api.Handler 多继承 mixin：GET /api/execution/health 分发。

    只读端点（无任何写路径）；失败走 fail-visible 字段而非 HTTP 错误码
    （§8.1：8091 无统一包装，HTTP 500 只留给未捕获异常）。
    """

    def _execution_health(self, qs):
        # force 参数仅本机诊断/R3 用；caller 归因参数不产生任何副作用
        force = "force=1" in (qs or "").lower() or "force=true" in (qs or "").lower()
        try:
            payload = build_execution_health(force=force)
        except Exception as e:  # noqa: BLE001  兜底 fail-visible
            payload = {
                "read_only": True,
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "manifest_ok": False,
                "manifest_path": "portal/execution_manifest.json",
                "manifest_count": 0,
                "manifest_error": "build_execution_health 未捕获异常: %s" % e,
                "ps_ok": False,
                "ps_error": None,
                "ps_elapsed_ms": 0,
                "tasks": [],
                "summary": {"ok": 0, "fail": 0, "stale": 0, "never": 0,
                            "disabled": 0, "worst": None},
            }
        self._json(payload)
