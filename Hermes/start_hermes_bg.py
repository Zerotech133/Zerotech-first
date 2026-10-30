# -*- coding: utf-8 -*-
"""Hermes WebUI 幂等守护启动器（K271 终版配方）。

被计划任务每 30 分钟触发 + 登录触发：
  - 幂等：8787 已监听则零副作用退出（socket 探测）
  - 驻留配方（死亡实验定稿）：DETACHED_PROCESS|CREATE_NEW_PROCESS_GROUP
    + stdin/stdout/stderr 全 DEVNULL。
    ⚠️ 实测：DETACHED + 文件句柄重定向 → server 启动后 ~1-2 分钟静默死
    （前台同句柄活、DETACHED+DEVNULL 活——继承句柄在 launcher 退出后
    失效，缓冲 flush 炸解释器且 stderr 同坏=零 traceback）。勿改回重定向。
  - env 与 start-hermes.ps1 逐项对齐；.cred_set 在位不触发交互
  - 计划任务侧须 AllowStartIfOnBatteries/DontStopIfGoingOnBatteries（K268）
"""
import os

# 换血 marker（2026-10-26）：SYSTEM Guard 任务消费；touch 后下一跳杀 8787 并重拉，
# 避免依赖 UAC 提权（同 8097 push_admin_restart.marker 判例）。
_MARKER = r"C:\Temp\hermes_restart.marker"
import socket
import subprocess
import sys
import time

# 轻量运行日志（排查守护分支用；S4U 令牌可写）
_LOG = r"C:\aios\Hermes\start_hermes_bg.log"


def _consume_marker():
    if not os.path.exists(_MARKER):
        return
    try:
        os.remove(_MARKER)
    except OSError:
        pass
    import subprocess
    ns = subprocess.run(["netstat", "-ano", "-p", "TCP"],
                        capture_output=True).stdout.decode("gbk", "replace")
    for ln in ns.splitlines():
        if ":8787" in ln and "LISTENING" in ln:
            pid = ln.split()[-1]
            if pid.isdigit():
                subprocess.run(["taskkill", "/F", "/PID", pid],
                               capture_output=True)
                _log("[restart] killed 8787 pid %s (marker consumed)" % pid)
                # 等端口释放，避免随后的幂等探测误判 already listening
                import socket as _sk
                import time as _tm
                for _ in range(30):
                    _s = _sk.socket()
                    _s.settimeout(0.5)
                    try:
                        _s.connect(("127.0.0.1", 8787))
                        _s.close()
                    except OSError:
                        break
                    finally:
                        try:
                            _s.close()
                        except Exception:
                            pass
                    _tm.sleep(0.5)
                _log("[restart] port released, relaunching")


def _log(msg):
    try:
        with open(_LOG, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except OSError:
        pass


_log("[boot] guard entered (pid=%s)" % os.getpid())

# 2026-10-16 升级：server 切换到 Hermes PM 全量环境 venv（Python 3.14.7，
# extras=all + resolved uv.lock，手工补 pyyaml/httpx-sse）。此前用系统
# Python311 或 pm-runtime 引导壳时 agent 依赖不全 → MCP 0 工具、插件成片
# 加载失败（No module named 'httpx'）。该 venv 与 PM selected 环境身份一致，
# adopt_selected 不会跨版本重写 sys.path（pydantic_core 崩溃判例）。
VENV_PY = r"C:\Users\t\.hermes\installs\4cc1a9b84685bce9\environments\da53f63c8bf44f54a7dfc492507ed00d\venv\Scripts\python.exe"
# 2026-10-17 切推（B 方案验收通过）：server 切到 upstream master 快照
# hermes-webui-next（+50 行定制接线 + 5 api 连接器模块）。旧目录
# hermes-webui 原样保留 = 回滚资产（改回此路径即回滚）。state dir 不变
# （webui-state 保留全部会话数据）。
# ★★★ 生产基=next（2026-10-18 定版）：改码只改 hermes-webui-next；
# hermes-webui=回滚资产勿改（改了不在跑，白改且制造双基漂移）。
# 改码前必读本注释确认 SERVER/_CWD 指向；详见 hermes-webui/README_DUALBASE.md。
SERVER = r"C:\项目\老田的AI助手\Hermes\hermes-webui-next\server.py"
_CWD = r"C:\项目\老田的AI助手\Hermes\hermes-webui-next"

# ── 重启哨兵（2026-10-17）：flag 在位 → 先杀旧 8787 再继续启动 ──
# 守卫注释纠错（2026-10-18）：Guard 任务实测 S4U + LeastPrivilege（并非
# 旧注释所称"提权令牌"）——杀 SYSTEM session-0 webui 进程须管理员，
# 见 restart_hermes_8787.ps1；flag + Start-ScheduledTask HermesWebUI-Guard
# 仍是无 UAC 的标准重启路径。无 flag 时零副作用，幂等语义不变。
# ⚠️ 与 老田的AI助手/Hermes/ 副本须同步。
_RESTART_FLAG = r"C:\aios\Hermes\RESTART_8787.flag"
if os.path.exists(_RESTART_FLAG):
    try:
        os.remove(_RESTART_FLAG)
    except OSError:
        pass
    _ns = subprocess.run(["netstat", "-ano", "-p", "TCP"],
                         capture_output=True, text=True).stdout or ""
    _pids = set()
    for _line in _ns.splitlines():
        _parts = _line.split()
        if len(_parts) >= 5 and _parts[1].endswith(":8787") \
                and _parts[3] == "LISTENING":
            _pids.add(_parts[4])
    _killed_any = False
    for _pid in _pids:
        _rc = subprocess.run(["taskkill", "/F", "/PID", _pid],
                             capture_output=True).returncode
        _log("[restart] taskkill /F /PID %s rc=%d" % (_pid, _rc))
        _killed_any = _killed_any or (_rc == 0)
    # 等端口真正释放（最多 10s）；超时则如实记录不谎报
    _freed = False
    for _ in range(20):
        _s2 = socket.socket()
        _s2.settimeout(0.5)
        try:
            _s2.connect(("127.0.0.1", 8787))
            _s2.close()
            time.sleep(0.5)
        except OSError:
            _s2.close()
            _freed = True
            break
    if _killed_any and _freed:
        print("[restart] old 8787 killed, port released")
        _log("[restart] old 8787 killed, port released")
    else:
        print("[restart] FAILED: killed_any=%s freed=%s (老进程可能是"
              " S4U session-0 高保护，需提权 taskkill)" % (_killed_any, _freed))
        _log("[restart] FAILED: killed_any=%s freed=%s" % (_killed_any, _freed))

_consume_marker()

# ── 幂等探测：已监听即退出 ──────────────────────────────────
s = socket.socket()
s.settimeout(1.5)
try:
    s.connect(("127.0.0.1", 8787))
    s.close()
    print("[skip] 8787 already listening")
    _log("[skip] 8787 already listening")
    sys.exit(0)
except OSError:
    pass
finally:
    try:
        s.close()
    except Exception:
        pass

env = os.environ.copy()
env.update({
    "PYTHONUTF8": "1",
    # 2026-10-17 判例：缺此开关时守护链拉起 40s 必死（受管命令带它则活）——
    # lazy install 在守护重建 PATH 下触发 PM 机制异常。与受管启动命令逐项对齐。
    "HERMES_DISABLE_LAZY_INSTALLS": "1",
    "HERMES_WEBUI_PYTHON": VENV_PY,
    "HERMES_WEBUI_HOST": "127.0.0.1",
    "HERMES_WEBUI_PORT": "8787",
    "HERMES_WEBUI_STATE_DIR": r"C:\项目\老田的AI助手\Hermes\webui-state",
    # 军团 IDE 扩展（官方 /extensions/ 注入面，不改核心 static）
    "HERMES_WEBUI_EXTENSION_DIR": r"C:\项目\_vault\legion_ide\release\legion-ide\current",
    "HERMES_WEBUI_EXTENSION_SCRIPT_URLS": "/extensions/app.js,/extensions/a0-mainchat.js",
    "HERMES_WEBUI_EXTENSION_STYLESHEET_URLS": "/extensions/app.css",
    # L2-A（2026-10-30 老田批准）：允许本页 iframe 内嵌本机 Zhice 研究台（仅 loopback 5175）
    "HERMES_WEBUI_CSP_FRAME_EXTRA": "http://127.0.0.1:5175",
    "NO_PROXY": "localhost,127.0.0.1",
})
# Phase B2：可选密码认证（文件存在才启用；不存在=保持现状）
_PW_FILE = os.environ.get(
    "HERMES_WEBUI_PASSWORD_FILE",
    r"C:\项目\_vault\legion_ide\hermes_webui_password",
)
try:
    _pw = open(_PW_FILE, "r", encoding="utf-8-sig").read().strip()
    if _pw:
        env["HERMES_WEBUI_PASSWORD"] = _pw
except OSError:
    pass
# 2026-10-16 判例：交互会话 PATH 前置了 WorkBuddy 3.13/PortableGit，
# server 启动后 spawn 子进程会拿到坏解释器而静默死。守护链环境必须
# 以系统 PATH 为底重建，剔除注入项。
_sysroot = os.environ.get("SystemRoot", r"C:\Windows")
env["PATH"] = ";".join([
    r"C:\Users\t\.hermes\installs\4cc1a9b84685bce9\environments\da53f63c8bf44f54a7dfc492507ed00d\venv\Scripts",
    r"C:\Users\t\.hermes\installs\4cc1a9b84685bce9\environments\da53f63c8bf44f54a7dfc492507ed00d\venv",
    r"C:\Program Files\Python311\Scripts",
    r"C:\Program Files\Python311",
    _sysroot,
    os.path.join(_sysroot, "System32"),
    os.path.join(_sysroot, "System32", "Wbem"),
    os.path.join(_sysroot, "System32", "WindowsPowerShell", "v1.0"),
    r"C:\Program Files\Docker\Docker\resources\bin",
])
# 剥离会话级代理注入（大小写重复键会让子进程环境块构建炸掉）
for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
          "ALL_PROXY", "all_proxy"):
    env.pop(k, None)

flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
subprocess.Popen(
    [VENV_PY, "-X", "utf8", SERVER],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    env=env, creationflags=flags, close_fds=True,
    cwd=_CWD)
print("[launched] server.py detached (stdio=DEVNULL, handle-safe)")
_log("[launched] server.py detached (stdio=DEVNULL, handle-safe)")
