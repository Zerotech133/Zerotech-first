# -*- coding: utf-8 -*-
"""hermes-webui-next (8788) 幂等启动器 —— B 方案并行验证环境（2026-10-17）。

照抄 start_hermes_bg.py K271 终版配方（DETACHED_PROCESS|CREATE_NEW_PROCESS_GROUP
+ stdio 全 DEVNULL，勿改回重定向），仅改四参数：
  端口 8788 / 目录 hermes-webui-next / state 独立目录 webui-state-next /
  无 RESTART_FLAG 哨兵（验证环境手动杀进程即可）。

2026-10-17 判例（PM venv shim 转发谜团，b0 排障定稿）：
  PM 特制 venv 的 python.exe（241KB 重定向器）直跑 server.py 时，若环境缺
  HERMES_DISABLE_LAZY_INSTALLS=1 + HERMES_WEBUI_PYTHON + PATH 重建，会触发
  PM lazy install 机制 → 转发到裸 tools python（-S 无 site-packages，import
  yaml/ruamel 全灭）。生产 8787 靠 start_hermes_bg.py 的 env 全套活；
  next 裸起必炸。两者唯一差异就是这套 env。
"""
import os
import socket
import subprocess
import sys
import time

_LOG = r"C:\aios\Hermes\start_hermes_next.log"


def _log(msg):
    try:
        with open(_LOG, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except OSError:
        pass


_log("[boot] next launcher entered (pid=%s)" % os.getpid())

VENV_PY = r"C:\Users\t\.hermes\installs\4cc1a9b84685bce9\environments\da53f63c8bf44f54a7dfc492507ed00d\venv\Scripts\python.exe"
SERVER = r"C:\aios\Hermes\hermes-webui-next\server.py"
STATE_DIR = r"C:\aios\Hermes\webui-state-next"

# ── 幂等探测：8788 已监听则退出 ──
s = socket.socket()
s.settimeout(1.5)
try:
    s.connect(("127.0.0.1", 8788))
    s.close()
    print("[skip] 8788 already listening")
    _log("[skip] 8788 already listening")
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
    "HERMES_DISABLE_LAZY_INSTALLS": "1",  # 防 PM shim 转发（见文件头判例）
    "HERMES_WEBUI_PYTHON": VENV_PY,
    "HERMES_WEBUI_HOST": "127.0.0.1",
    "HERMES_WEBUI_PORT": "8788",
    "HERMES_WEBUI_STATE_DIR": STATE_DIR,
    # 军团 IDE 扩展（8788 验证环境）
    "HERMES_WEBUI_EXTENSION_DIR": r"C:\项目\老田的AI助手\Hermes\extensions\legion-ide",
    "HERMES_WEBUI_EXTENSION_SCRIPT_URLS": "/extensions/app.js",
    "HERMES_WEBUI_EXTENSION_STYLESHEET_URLS": "/extensions/app.css",
    "NO_PROXY": "localhost,127.0.0.1",
})
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
for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
          "ALL_PROXY", "all_proxy"):
    env.pop(k, None)

flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
subprocess.Popen(
    [VENV_PY, "-X", "utf8", SERVER],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    env=env, creationflags=flags, close_fds=True,
    cwd=r"C:\aios\Hermes\hermes-webui-next",
)
print("[launched] next server.py detached on 8788")
_log("[launched] next server.py detached on 8788")
