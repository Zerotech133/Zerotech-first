#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""rotate_logs.py — 日志轮转 + 回收站保留（默认 dry-run，T7 轨道）

为什么要有它
------------
2026-10-22 实测：权威 ``logs/`` 只有 5 个文件 14.6 KB，而真实日志散落各处——
``jev-eval/logs/gate.log`` 已 19.5 MB 且每 10 分钟 +1.35 KB、**无轮转**；
``card.log``、``news_collector/logs/ai.log``、仓库根 ``s4u_guard.log`` 同样只增不减。
病灶是「日志散 + 无轮转」，不是「日志太大」。本工具负责轮转与保留，
不做跨目录归并（归并另案）。

规则
----
1. 目标清单 = 配置驱动：内置 ``DEFAULT_CONFIG``（jev-eval/logs、
   news_collector/logs、quant_backtest/logs、logs/），可用 ``--config`` 外部
   JSON 覆盖，也可用 ``--target`` 临时追加。目标不存在 → 跳过，不算失败。
2. 命中任一条件即轮转：
   - 单文件 > ``--max-mb``（默认 20 MiB）；
   - 文件日期跨天：mtime 的本地日期早于今天（等价 logrotate 的 daily 语义）。
3. 轮转方式 = 同目录重命名 ``<stem>.<yyyyMMdd-HHMMSS>.<原扩展名>``（``.log``
   文件即 ``<name>.<yyyyMMdd-HHMMSS>.log``）。**默认不压缩**，压缩会掩盖可读性；
   需要压缩时显式 ``--gzip``（压缩件 ``.gz``，明文副本随后进回收站）。
4. 保留：轮转件文件名里的时间戳早于 ``--keep-days``（默认 14）→ 进回收站；
   窗口内的轮转件保留（KEEP），不重复轮转。
5. 默认 dry-run，只有显式 ``--execute`` 才动手；``--json`` 输出机器可读结果。
6. **三级轮转（2026-11-05 JEV-LOGROT）**：
   - A 快路 ``mode=rename``：同目录 rename 成功（原行为）；
   - B 占用降级 ``mode=copy_truncate``：rename 抛 ``PermissionError``
     （Windows WinError 32 / errno 13 EACCES）——常驻服务用
     ``Start-Process -RedirectStandardError`` 拉起，``.err/.out`` 句柄被子进程
     持有到进程退出，rename 必然失败。此时改为「复制内容到归档件
     （沿用 ``--gzip`` / ``--keep-days`` 策略）+ 原地截断源文件」
     （``open(path, "r+b")`` 后 ``truncate(0)``），截断成功即视为轮转成功；
   - C 兜底 ``mode=deferred``：复制或截断都失败 → 记入
     ``<root>/jev-eval/logs/rotate_deferred.json``，**exit 0 + WARN**；
     下次运行**开头先重试**该清单，同一文件累计失败 3 次后转入
     ``rotate_deferred_dead.json`` 且只 WARN 一次（防无限重试刷屏）。

安全护栏
--------
- 目标目录与文件必须 realpath 落在 ``--root``（默认仓库根）之下；``--root``
  只允许指向工作区 ``C:\\项目`` 内、且不能是工作区根本身；
- 跳过 mtime 在 ``--active-guard-min``（默认 10）分钟内的文件（可能正在写）；
- 跳过 ``.git`` / ``node_modules`` / ``.venv`` 目录（不递归进去）；
- ACL 拒绝的目录/文件：记 FAIL 并继续，不崩；
- **回收站不可用时拒绝删除**：进程若运行在 AppContainer / 低完整性级别，
  或回收站被 ``NukeOnDelete`` 策略关闭，``SHFileOperationW`` 会「静默降级为
  永久删除」却仍返回 0（本机已实证，见报告）。此时一律记 FAIL、保留文件，
  绝不把「已删」误报成「已进回收站」。

退出码
------
0 = 无需处理或全部成功 / 降级成功 / 延后（deferred 不再让整条链变红）；
1 = 有实质失败（gzip 回收失败等，不含占用延后）；2 = 参数/环境错误。

用法
----
::

    # 预览（文本）
    python -B tools/rotate_logs.py
    # 预览（JSON）
    python -B tools/rotate_logs.py --json
    # 真轮转
    python -B tools/rotate_logs.py --execute
    # 真轮转 + 压缩
    python -B tools/rotate_logs.py --execute --gzip
    # 诊断回收站能力（不可用 → exit 2）
    python -B tools/rotate_logs.py --recycle-check
    # 夹具/演练：以另一个目录当「仓库根」
    python -B tools/rotate_logs.py --execute --root C:/项目/_tmp_T7
    # 外部配置
    python -B tools/rotate_logs.py --config tools/rotate_logs.example.json

配置 JSON 形态::

    {
      "targets": [
        {"path": "jev-eval/logs", "suffixes": [".log", ".out", ".err"]},
        {"path": "logs"}
      ],
      "suffixes": [".log", ".out", ".err"],
      "skip_dirs": [".git", "node_modules", ".venv"]
    }
"""
from __future__ import annotations

import argparse
import ctypes
import errno
import gzip
import json
import os
import re
import shutil
import stat as statmod
import sys
import time
from collections import Counter
from ctypes import wintypes
from datetime import datetime, timedelta
from pathlib import Path
import logging

try:  # Windows 控制台/管道统一按 UTF-8 输出
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError) as exc:
    logging.getLogger(__name__).warning("silent-except %s: %s", "tools/rotate_logs.py", exc)

TOOL_NAME = "rotate_logs"
SCHEMA = "rotate_logs/v1"
TOOL_VERSION = 2

# ---- 三级轮转模式（JEV-LOGROT-20261105）----
MODE_RENAME = "rename"              # A 快路
MODE_COPY_TRUNCATE = "copy_truncate"  # B 占用降级
MODE_DEFERRED = "deferred"          # C 兜底延后

DEFERRED_SCHEMA = "rotate_deferred/v1"
DEFERRED_REL_DIR = "jev-eval/logs"
DEFERRED_NAME = "rotate_deferred.json"
DEFERRED_DEAD_NAME = "rotate_deferred_dead.json"
DEFERRED_MAX_ATTEMPTS = 3

DELETE_ACCESS = 0x00010000         # 探测句柄是否允许 rename/删除
WIN_SHARING_VIOLATION = 32

REPO_ROOT = Path(r"C:/项目/老田的AI助手")
WORKSPACE_ROOT = Path(r"C:/项目")

# 内置目标清单（仓库内相对路径）。只处理存在且是目录的项。
DEFAULT_CONFIG = {
    "targets": [
        {"path": "jev-eval/logs"},
        {"path": "news_collector/logs"},
        {"path": "quant_backtest/logs"},
        {"path": "logs"},
    ],
    "suffixes": [".log", ".out", ".err"],
    "skip_dirs": [".git", "node_modules", ".venv"],
}

# 轮转件命名：<stem>.<yyyyMMdd-HHMMSS>[-dup].<ext>[.gz]
ROTATED_RE = re.compile(
    r"^(?P<stem>.+?)\.(?P<ts>\d{8}-\d{6})(?:-(?P<dup>\d+))?"
    r"\.(?P<ext>log|out|err)(?P<gz>\.gz)?$",
    re.IGNORECASE,
)
TS_FMT = "%Y%m%d-%H%M%S"

# ---- Windows 删除（SHFileOperationW）常量 ----
FO_DELETE = 3
FOF_ALLOWUNDO = 0x0040
FOF_NOCONFIRMATION = 0x0010
FOF_SILENT = 0x0004
FOF_NOERRORUI = 0x0400
FOF_NOCONFIRMMKDIR = 0x0200

TOKEN_QUERY = 0x0008
INFO_TOKEN_USER = 1
INFO_TOKEN_INTEGRITY_LEVEL = 25
INFO_TOKEN_IS_APPCONTAINER = 29
GENERIC_WRITE = 0x40000000
FILE_SHARE_ALL = 0x00000007
OPEN_EXISTING = 3
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

INTEGRITY_NAMES = {
    0x0000: "Untrusted",
    0x1000: "Low",
    0x2000: "Medium",
    0x3000: "High",
    0x4000: "System",
}


class Fatal(Exception):
    """参数/环境错误 → exit 2。"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class _Parser(argparse.ArgumentParser):
    """让 argparse 的错误也走统一的 Fatal/JSON 通道。"""

    def error(self, message):  # noqa: D102
        raise Fatal("参数错误：" + message)


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------
def human_size(n: int) -> str:
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"  # pragma: no cover


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def parse_rotation_ts(text: str):
    try:
        return datetime.strptime(text, TS_FMT)
    except (TypeError, ValueError):
        return None


def is_under(path: Path, parent: Path) -> bool:
    try:
        return path == parent or path.is_relative_to(parent)
    except (OSError, ValueError):  # pragma: no cover
        return False


def rel_display(path: Path, root: Path) -> str:
    try:
        return os.path.relpath(str(path), str(root))
    except (OSError, ValueError):  # pragma: no cover
        return str(path)


# --------------------------------------------------------------------------
# 三级轮转：占用判定 / 复制归档 / 原地截断 / dry-run 预判
# --------------------------------------------------------------------------
def is_lock_error(exc: OSError) -> bool:
    """是否为「文件被别的进程占用」类错误（WinError 32/33、EACCES/EBUSY）。"""
    winerror = getattr(exc, "winerror", None)
    if winerror in (WIN_SHARING_VIOLATION, 33):
        return True
    return getattr(exc, "errno", None) in (errno.EACCES, errno.EBUSY, errno.EPERM)


def probe_rename_blocked(path: Path) -> tuple[bool, str]:
    """dry-run 用：只探测「rename 会不会被占用拦住」，不动文件内容。

    Windows 下 rename/删除需要 DELETE 访问；若另一进程以「不含
    FILE_SHARE_DELETE」的句柄打开着该文件，CreateFileW(DELETE) 会直接返回
    WinError 32 —— 与随后 rename 失败同因，可据此预览 B 路。
    """
    if os.name != "nt" or not hasattr(ctypes, "WinDLL"):
        return False, ""
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                    ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                    wintypes.HANDLE]
        k32.CreateFileW.restype = wintypes.HANDLE
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.CloseHandle.restype = wintypes.BOOL
        handle = k32.CreateFileW(str(path), DELETE_ACCESS, FILE_SHARE_ALL, None,
                                 OPEN_EXISTING, 0, None)
        if handle in (INVALID_HANDLE_VALUE, None):
            err = ctypes.get_last_error()
            if err in (WIN_SHARING_VIOLATION, 33):
                return True, f"WinError {err}（句柄被占用，rename 会失败）"
            return False, f"probe WinError {err}"
        k32.CloseHandle(handle)
        return False, "probe ok"
    except OSError as exc:  # pragma: no cover - 探测失败不影响主流程
        return False, f"probe 异常：{exc}"


def copy_to_archive(src: Path, dest: Path, gzip_on: bool) -> Path:
    """B 路第一步：把源内容复制成归档件（沿用 --gzip 策略），返回归档路径。

    gzip 时直接写 ``<dest>.gz``，不落明文中间件——避免复用回收站依赖。
    """
    if not gzip_on:
        shutil.copy2(src, dest)
        return dest
    gz_path = dest.with_name(dest.name + ".gz")
    with open(src, "rb") as f_in, gzip.open(gz_path, "wb", compresslevel=6) as f_out:
        shutil.copyfileobj(f_in, f_out)
    return gz_path


def truncate_in_place(src: Path) -> None:
    """B 路第二步：原地截断源文件（保留 inode/句柄，服务可继续写）。"""
    with open(src, "r+b") as fh:
        fh.truncate(0)
        fh.flush()
        try:
            os.fsync(fh.fileno())
        except OSError as exc:  # pragma: no cover - 某些卷不支持 fsync
            logging.getLogger(__name__).warning("silent-except %s: %s",
                                                "tools/rotate_logs.py", exc)


# --------------------------------------------------------------------------
# 参数 / 配置 / 目标解析
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    ap = _Parser(
        prog="rotate_logs.py",
        description="日志轮转 + 回收站保留（默认 dry-run，--execute 才动手）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  python -B tools/rotate_logs.py --json\n"
            "  python -B tools/rotate_logs.py --execute\n"
            "  python -B tools/rotate_logs.py --execute --gzip --keep-days 7\n"
            "  python -B tools/rotate_logs.py --recycle-check\n"
            "  python -B tools/rotate_logs.py --execute --root C:/项目/_tmp_T7\n"
        ),
    )
    ap.add_argument(
        "--root", default=str(REPO_ROOT),
        help="仓库根（安全边界与目标解析基准；默认 %(default)s）",
    )
    ap.add_argument(
        "--target", action="append", metavar="REL",
        help="相对 --root 的目标目录，可重复；默认用配置里的 target 清单",
    )
    ap.add_argument("--config", metavar="JSON", help="外部 JSON 配置（覆盖内置目标清单）")
    ap.add_argument("--max-mb", type=float, default=20.0, help="单文件轮转阈值 MB（默认 20）")
    ap.add_argument("--keep-days", type=int, default=14, help="轮转件保留天数（默认 14）")
    ap.add_argument(
        "--active-guard-min", type=float, default=10.0,
        help="mtime 在 N 分钟内视为正在写、跳过（默认 10）",
    )
    ap.add_argument("--gzip", action="store_true", help="轮转后压缩（默认不压缩，保可读性）")
    ap.add_argument("--execute", action="store_true", help="真的动手（默认 dry-run）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--verbose", action="store_true", help="文本模式下列出所有 skip/keep")
    ap.add_argument("--recycle-check", action="store_true", help="只诊断回收站能力并退出")
    return ap


def load_config(path_text: str | None) -> dict:
    cfg = {
        "targets": [dict(t) for t in DEFAULT_CONFIG["targets"]],
        "suffixes": list(DEFAULT_CONFIG["suffixes"]),
        "skip_dirs": list(DEFAULT_CONFIG["skip_dirs"]),
    }
    if not path_text:
        return cfg
    p = Path(path_text)
    try:
        raw = json.loads(p.read_text(encoding="utf-8-sig"))  # 容忍 Windows 编辑器加的 BOM
    except OSError as exc:
        raise Fatal(f"--config 读不到：{p}（{exc}）") from exc
    except json.JSONDecodeError as exc:
        raise Fatal(f"--config 不是合法 JSON：{p}（{exc}）") from exc
    if not isinstance(raw, dict):
        raise Fatal("--config 顶层必须是对象")

    suffixes = raw.get("suffixes")
    if suffixes is not None:
        if not isinstance(suffixes, list) or not suffixes:
            raise Fatal("--config.suffixes 必须是非空数组")
        cfg["suffixes"] = [str(s).lower() for s in suffixes]

    skip_dirs = raw.get("skip_dirs")
    if skip_dirs is not None:
        if not isinstance(skip_dirs, list):
            raise Fatal("--config.skip_dirs 必须是数组")
        cfg["skip_dirs"] = [str(s) for s in skip_dirs]

    targets = raw.get("targets")
    if targets is None:
        return cfg
    if not isinstance(targets, list) or not targets:
        raise Fatal("--config.targets 必须是非空数组")
    parsed = []
    for item in targets:
        if isinstance(item, str):
            parsed.append({"path": item})
        elif isinstance(item, dict) and item.get("path"):
            entry = {"path": str(item["path"])}
            item_suffixes = item.get("suffixes")
            if item_suffixes is not None:
                if not isinstance(item_suffixes, list) or not item_suffixes:
                    raise Fatal("--config.targets[].suffixes 必须是非空数组")
                entry["suffixes"] = [str(s).lower() for s in item_suffixes]
            parsed.append(entry)
        else:
            raise Fatal("--config.targets[] 每项必须是字符串或含 path 的对象")
    cfg["targets"] = parsed
    return cfg


def resolve_root(root_text: str) -> tuple[Path, bool]:
    raw = Path(root_text).expanduser()
    try:
        root = raw.resolve(strict=True)
    except OSError as exc:
        raise Fatal(f"--root 不可用：{root_text}（{exc}）") from exc
    if not root.is_dir():
        raise Fatal(f"--root 不是目录：{root}")
    if root == WORKSPACE_ROOT:
        raise Fatal(f"--root 不能是工作区根本身：{root}")
    if not is_under(root, WORKSPACE_ROOT):
        raise Fatal(f"--root 必须位于工作区 {WORKSPACE_ROOT} 之下：{root}")
    return root, root != REPO_ROOT


def resolve_targets(root: Path, cfg: dict, cli_targets: list[str] | None,
                    drill: bool) -> list[dict]:
    skip_lower = {s.lower() for s in cfg["skip_dirs"]}
    entries = []
    if cli_targets:
        for rel in cli_targets:
            entries.append({"path": rel})
    else:
        entries = [dict(t) for t in cfg["targets"]]

    resolved = []
    for entry in entries:
        rel_text = entry["path"].strip()
        if not rel_text:
            raise Fatal("目标路径为空")
        rel_path = Path(rel_text)
        if rel_path.is_absolute():
            raise Fatal(f"目标必须是相对 --root 的路径：{rel_text}")
        if any(part.lower() in skip_lower for part in rel_path.parts):
            raise Fatal(f"目标落在禁扫目录（.git/node_modules/.venv）里：{rel_text}")
        try:
            abs_dir = (root / rel_path).resolve(strict=False)
        except OSError as exc:
            raise Fatal(f"目标无法解析：{rel_text}（{exc}）") from exc
        if not is_under(abs_dir, root):
            raise Fatal(f"目标越界（realpath 不在 root 之下）：{rel_text} -> {abs_dir}")
        if rel_text in (".", "") and abs_dir == root:
            if not drill:
                raise Fatal("不允许把仓库根本身当作轮转目标（演练请用 --root 指定别处）")
        if abs_dir.exists() and not abs_dir.is_dir():
            raise Fatal(f"目标不是目录：{rel_text}")
        resolved.append({
            "path": rel_text if rel_text not in (".", "") else ".",
            "abs": abs_dir,
            "suffixes": [s.lower() for s in (entry.get("suffixes") or cfg["suffixes"])],
        })
    return resolved


def validate_args(args) -> None:
    if args.max_mb <= 0:
        raise Fatal("--max-mb 必须 > 0")
    if args.keep_days < 0:
        raise Fatal("--keep-days 必须 >= 0")
    if args.active_guard_min < 0:
        raise Fatal("--active-guard-min 必须 >= 0")


# --------------------------------------------------------------------------
# 回收站能力探测（关键安全闸）
# --------------------------------------------------------------------------
def _win_structs():
    class SID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]

    class TokenUserStruct(ctypes.Structure):
        _fields_ = [("User", SID_AND_ATTRIBUTES)]

    class TokenMandatoryLabelStruct(ctypes.Structure):
        _fields_ = [("Label", SID_AND_ATTRIBUTES)]

    return TokenUserStruct, TokenMandatoryLabelStruct


def _kernel32_advapi():
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    a32 = ctypes.WinDLL("advapi32", use_last_error=True)
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                     ctypes.POINTER(wintypes.HANDLE)]
    k32.OpenProcessToken.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.restype = wintypes.BOOL
    k32.LocalFree.argtypes = [ctypes.c_void_p]
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                wintypes.HANDLE]
    k32.CreateFileW.restype = wintypes.HANDLE
    a32.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                        wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    a32.GetTokenInformation.restype = wintypes.BOOL
    a32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p,
                                           ctypes.POINTER(wintypes.LPWSTR)]
    a32.ConvertSidToStringSidW.restype = wintypes.BOOL
    a32.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
    a32.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
    a32.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    a32.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)
    return k32, a32


def _current_user_sid() -> str | None:
    k32, a32 = _kernel32_advapi()
    user_cls, _ = _win_structs()
    token = wintypes.HANDLE()
    if not k32.OpenProcessToken(k32.GetCurrentProcess(), TOKEN_QUERY,
                                ctypes.byref(token)):
        return None
    try:
        size = wintypes.DWORD(0)
        a32.GetTokenInformation(token, INFO_TOKEN_USER, None, 0, ctypes.byref(size))
        if not size.value:
            return None
        buf = ctypes.create_string_buffer(size.value)
        if not a32.GetTokenInformation(token, INFO_TOKEN_USER, buf, size,
                                       ctypes.byref(size)):
            return None
        token_user = ctypes.cast(buf, ctypes.POINTER(user_cls)).contents
        sid_ptr = wintypes.LPWSTR()
        if not a32.ConvertSidToStringSidW(token_user.User.Sid,
                                          ctypes.byref(sid_ptr)):
            return None
        try:
            return sid_ptr.value
        finally:
            k32.LocalFree(sid_ptr)
    finally:
        k32.CloseHandle(token)


def _integrity_rid() -> int | None:
    k32, a32 = _kernel32_advapi()
    _, label_cls = _win_structs()
    token = wintypes.HANDLE()
    if not k32.OpenProcessToken(k32.GetCurrentProcess(), TOKEN_QUERY,
                                ctypes.byref(token)):
        return None
    try:
        size = wintypes.DWORD(0)
        a32.GetTokenInformation(token, INFO_TOKEN_INTEGRITY_LEVEL, None, 0,
                                ctypes.byref(size))
        if not size.value:
            return None
        buf = ctypes.create_string_buffer(size.value)
        if not a32.GetTokenInformation(token, INFO_TOKEN_INTEGRITY_LEVEL, buf, size,
                                       ctypes.byref(size)):
            return None
        label = ctypes.cast(buf, ctypes.POINTER(label_cls)).contents
        count_ptr = a32.GetSidSubAuthorityCount(label.Label.Sid)
        if not count_ptr:
            return None
        count = count_ptr.contents.value
        rid_ptr = a32.GetSidSubAuthority(label.Label.Sid, count - 1)
        if not rid_ptr:
            return None
        return int(rid_ptr.contents.value)
    finally:
        k32.CloseHandle(token)


def _is_appcontainer() -> bool | None:
    k32, a32 = _kernel32_advapi()
    token = wintypes.HANDLE()
    if not k32.OpenProcessToken(k32.GetCurrentProcess(), TOKEN_QUERY,
                                ctypes.byref(token)):
        return None
    try:
        flag = wintypes.DWORD(0)
        ret = wintypes.DWORD(0)
        ok = a32.GetTokenInformation(token, INFO_TOKEN_IS_APPCONTAINER,
                                     ctypes.byref(flag), ctypes.sizeof(flag),
                                     ctypes.byref(ret))
        if not ok:
            return None
        return bool(flag.value)
    finally:
        k32.CloseHandle(token)


def _volume_guid() -> str | None:
    k32, _ = _kernel32_advapi()
    buf = ctypes.create_unicode_buffer(1024)
    if not k32.GetVolumeNameForVolumeMountPointW("C:\\", buf, len(buf)):
        return None
    match = re.search(r"Volume(\{[0-9a-fA-F-]+\})", buf.value)
    return match.group(1) if match else None


def _nuke_on_delete() -> bool | None:
    """回收站「直接删除不走回收站」策略（本卷/全局）。None = 查不到。"""
    try:
        import winreg
    except ImportError:  # pragma: no cover
        return None
    result = None
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Microsoft\Windows\CurrentVersion"
                            r"\Explorer\BitBucket") as key:
            value, _ = winreg.QueryValueEx(key, "NukeOnDelete")
            result = bool(value)
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "tools/rotate_logs.py", exc)
    guid = _volume_guid()
    if guid:
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer"
                rf"\BitBucket\Volume\{guid}",
            ) as key:
                value, _ = winreg.QueryValueEx(key, "NukeOnDelete")
                if value:
                    result = True
        except OSError as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "tools/rotate_logs.py", exc)
    return result


def _dir_writable(path: Path) -> tuple[bool | None, str]:
    """用 CreateFileW(GENERIC_WRITE, 目录) 探测真实写权限（AppContainer 会在这里露馅）。"""
    if os.name != "nt":
        return None, "not_windows"
    k32, _ = _kernel32_advapi()
    handle = k32.CreateFileW(
        str(path), GENERIC_WRITE, FILE_SHARE_ALL, None, OPEN_EXISTING,
        FILE_FLAG_BACKUP_SEMANTICS, None,
    )
    if handle in (INVALID_HANDLE_VALUE, None):
        err = ctypes.get_last_error()
        return False, f"WinError {err}"
    k32.CloseHandle(handle)
    return True, "ok"


def recycle_capability() -> dict:
    """探测本进程是否可能把文件真正放进回收站。

    不可用的三种典型原因：
    - AppContainer / 低完整性级别：写不进 C:\\$Recycle.Bin，SHFileOperationW
      会**静默降级为永久删除**；
    - 回收站目录不可写（ACL/策略）；
    - NukeOnDelete=1（删除即永久删除）。
    """
    info = {
        "platform": sys.platform,
        "available": False,
        "reasons": [],
        "detail": "",
        "user_sid": None,
        "bin_dir": None,
        "integrity": None,
        "integrity_rid": None,
        "appcontainer": None,
        "nuke_on_delete": None,
        "bin_dir_writable": None,
        "probe_error": None,
    }
    if os.name != "nt":
        info["reasons"].append("not_windows")
        info["detail"] = "非 Windows，回收站不可用"
        return info

    rid = _integrity_rid()
    info["integrity_rid"] = rid
    info["integrity"] = INTEGRITY_NAMES.get(rid, f"0x{rid:04x}" if rid else None)
    if rid is not None and rid < 0x2000:
        info["reasons"].append("low_integrity")

    appc = _is_appcontainer()
    info["appcontainer"] = appc
    if appc:
        info["reasons"].append("appcontainer")

    sid = _current_user_sid()
    info["user_sid"] = sid
    if sid:
        bin_dir = Path(os.environ.get("SystemDrive", "C:") + "\\$Recycle.Bin") / sid
        info["bin_dir"] = str(bin_dir)
        if not bin_dir.is_dir():
            info["reasons"].append("bin_dir_missing")
        else:
            writable, err = _dir_writable(bin_dir)
            info["bin_dir_writable"] = writable
            info["probe_error"] = err
            if writable is False:
                info["reasons"].append("bin_dir_not_writable")
    else:
        info["reasons"].append("user_sid_unknown")

    nuke = _nuke_on_delete()
    info["nuke_on_delete"] = nuke
    if nuke:
        info["reasons"].append("nuke_on_delete")

    info["available"] = not info["reasons"]
    if info["available"]:
        info["detail"] = "回收站可用：SHFileOperationW + FOF_ALLOWUNDO 会真正入站"
    else:
        info["detail"] = (
            "回收站不可用（" + ", ".join(info["reasons"]) + "）："
            "SHFileOperationW 在回收失败时会静默降级为永久删除，"
            "本工具拒绝执行保留动作、原文件保留"
        )
    return info


def _bin_entries(bin_dir: Path) -> set[str] | None:
    try:
        return {p.name for p in bin_dir.iterdir() if p.name.startswith("$I")}
    except OSError:
        return None


def _shfileop_delete(path: Path) -> tuple[bool, str]:
    """SHFileOperationW + FOF_ALLOWUNDO 删除单个文件（调用前必须确认回收站可用）。"""
    if os.name != "nt":
        return False, "not_windows"
    abs_path = str(path.resolve())
    buf = ctypes.create_unicode_buffer(abs_path + "\0")  # 双 NUL 结尾

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", ctypes.c_uint16),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    op = SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = FO_DELETE
    op.pFrom = ctypes.cast(buf, wintypes.LPCWSTR)
    op.pTo = None
    op.fFlags = (FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT
                 | FOF_NOERRORUI | FOF_NOCONFIRMMKDIR)
    op.fAnyOperationsAborted = False
    op.hNameMappings = None
    op.lpszProgressTitle = None
    try:
        rc = int(ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)))
    except OSError as exc:  # pragma: no cover
        return False, f"SHFileOperationW 异常：{exc}"
    if rc != 0:
        return False, f"SHFileOperationW rc=0x{rc:04X}"
    if op.fAnyOperationsAborted:
        return False, "操作被中止（fAnyOperationsAborted=True）"
    for _ in range(15):  # 最多等 ~3s
        if not path.exists():
            return True, ""
        time.sleep(0.2)
    return False, "调用返回成功但文件仍存在"


def recycle_one(path: Path, cap: dict) -> tuple[bool, str]:
    """把单个文件送进回收站，并复核「回收站确实多了一条 $I 记录」。"""
    if not cap.get("available"):
        return False, "回收站不可用：" + ",".join(cap.get("reasons") or [])
    bin_dir = Path(cap["bin_dir"]) if cap.get("bin_dir") else None
    before = _bin_entries(bin_dir) if bin_dir else None

    ok, detail = _shfileop_delete(path)
    if not ok:
        return False, detail
    if before is None:
        return True, "已删除（回收站目录不可读，未能复核 $I 条目）"

    new_entries: set[str] = set()
    for _ in range(5):
        after = _bin_entries(bin_dir)
        if after is not None:
            new_entries = after - before
            if new_entries:
                break
        time.sleep(0.2)
    if new_entries:
        return True, f"已入回收站（新增 $I 条目 {len(new_entries)} 条）"
    return False, ("文件已消失但回收站没有新增 $I 条目——"
                   "可能被永久删除，请人工核查（工具不会再动这个路径）")


# --------------------------------------------------------------------------
# 扫描 / 分类
# --------------------------------------------------------------------------
def iter_log_files(top: Path, suffixes: set[str], skip_lower: set[str],
                   failures: list[dict]):
    """遍历目录，只产出后缀命中的普通文件；ACL 拒绝记 FAIL 后继续。"""

    def onerror(err: OSError):
        failures.append({
            "action": "fail",
            "path": getattr(err, "filename", None) or str(top),
            "reason": "acl_denied" if isinstance(err, PermissionError) else "walk_error",
            "detail": str(err),
            "result": "failed",
        })

    for dirpath, dirnames, filenames in os.walk(
            top, topdown=True, onerror=onerror, followlinks=False):
        kept = []
        for name in sorted(dirnames):
            if name.lower() in skip_lower:
                continue
            sub = Path(dirpath) / name
            try:
                if sub.is_symlink():
                    continue
            except OSError:  # pragma: no cover
                continue
            kept.append(name)
        dirnames[:] = kept
        for name in sorted(filenames):
            path = Path(dirpath) / name
            suffix = path.suffix.lower()
            # 普通日志按扩展名收；<ts>.log.gz 这类压缩轮转件也要收（否则保留规则扫不到）
            if suffix in suffixes or (suffix == ".gz" and ROTATED_RE.match(name)):
                yield path


def rotation_dest(path: Path, now: datetime) -> Path:
    ext = path.suffix.lower()
    stem = path.name[: -len(ext)] if ext else path.name
    base = f"{stem}.{now.strftime(TS_FMT)}"
    cand = path.with_name(base + ext)
    dup = 2
    while cand.exists():
        cand = path.with_name(f"{base}-{dup}{ext}")
        dup += 1
    return cand


def classify_file(path: Path, params: dict, now: datetime) -> dict:
    record = {
        "action": "skip",
        "path": str(path),
        "size": 0,
        "mtime": None,
        "reason": None,
        "detail": "",
        "result": "skipped",
    }
    try:
        st = path.stat()
    except OSError as exc:
        record.update(action="fail", reason="stat_failed", detail=str(exc),
                      result="failed")
        return record
    record["size"] = st.st_size
    record["mtime"] = datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")

    try:
        if path.is_symlink():
            record.update(reason="symlink", detail="不跟随符号链接")
            return record
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "tools/rotate_logs.py", exc)
    if not statmod.S_ISREG(st.st_mode):
        record.update(reason="not_regular", detail="不是普通文件")
        return record

    age_sec = now.timestamp() - st.st_mtime
    active_sec = params["active_guard_min"] * 60

    # 轮转件：只做保留判定，绝不二次轮转
    match = ROTATED_RE.match(path.name)
    if match:
        ts = parse_rotation_ts(match.group("ts"))
        if ts is None:
            record.update(reason="bad_rotation_timestamp",
                          detail=f"文件名时间戳非法：{match.group('ts')}")
            return record
        age_days = (now - ts).total_seconds() / 86400.0
        if age_sec < active_sec:
            record.update(reason="active",
                          detail=f"mtime 距今 {age_sec:.0f}s < {active_sec:.0f}s，疑似正在写")
            return record
        record["age_days"] = round(age_days, 2)
        if ts < params["keep_cutoff"]:
            record.update(action="recycle", reason="stale_rotated", result="planned",
                          detail=f"轮转件已 {age_days:.1f} 天 > 保留 {params['keep_days']} 天")
            return record
        record.update(action="keep", reason="within_keep_days", result="kept",
                      detail=f"轮转件 {age_days:.1f} 天 ≤ 保留 {params['keep_days']} 天")
        return record

    if age_sec < active_sec:
        record.update(reason="active",
                      detail=f"mtime 距今 {age_sec:.0f}s < {active_sec:.0f}s，疑似正在写")
        return record
    if st.st_size == 0:
        record.update(reason="empty", detail="0 字节，无内容可保留")
        return record
    if st.st_size > params["max_bytes"]:
        dest = rotation_dest(path, now)
        record.update(action="rotate", reason="size", result="planned",
                      dest=str(dest),
                      detail=f"{human_size(st.st_size)} > {params['max_mb']:g} MB")
        return record
    mdate = datetime.fromtimestamp(st.st_mtime).date()
    if mdate < now.date():
        dest = rotation_dest(path, now)
        record.update(action="rotate", reason="cross_day", result="planned",
                      dest=str(dest),
                      detail=f"mtime {mdate.isoformat()} 早于今天 {now.date().isoformat()}")
        return record
    record.update(reason="under_threshold",
                  detail="未超阈值且 mtime 是今天")
    return record


def build_plan(root: Path, targets: list[dict], cfg: dict,
               params: dict, now: datetime) -> tuple[list[dict], list[dict], list[dict]]:
    skip_lower = {s.lower() for s in cfg["skip_dirs"]}
    actions: list[dict] = []
    target_rows: list[dict] = []
    failures: list[dict] = []
    for target in targets:
        top = target["abs"]
        row = {
            "path": target["path"],
            "abs": str(top),
            "status": "missing" if not top.exists() else "scanned",
            "files": 0,
            "suffixes": target["suffixes"],
        }
        if not top.exists():
            target_rows.append(row)
            continue
        suffixes = set(target["suffixes"])
        for path in iter_log_files(top, suffixes, skip_lower, failures):
            row["files"] += 1
            try:
                resolved = path.resolve(strict=True)
            except OSError as exc:
                failures.append({
                    "action": "fail", "path": str(path), "reason": "resolve_failed",
                    "detail": str(exc), "result": "failed",
                })
                continue
            if not is_under(resolved, root):
                failures.append({
                    "action": "fail", "path": str(path), "reason": "path_escape",
                    "detail": f"realpath 越界：{resolved}", "result": "failed",
                })
                continue
            record = classify_file(path, params, now)
            dead_set = params.get("dead_paths") or set()
            retry_set = params.get("deferred_paths") or set()
            if record["action"] == "rotate" and norm_key(path) in dead_set:
                # 已满 3 次重试转入 dead：不再自动轮转（避免每天白复制一遍大文件）
                record.update(action="skip", result="skipped", reason="dead_deferred",
                              detail=(f"已在 {DEFERRED_DEAD_NAME} 中，需人工处理"
                                      "（释放句柄后手工重试）"))
                record["target"] = target["path"]
                record["rel"] = rel_display(path, root)
                actions.append(record)
                continue
            if record["action"] == "rotate" and norm_key(path) in retry_set:
                # 本轮开头已重试过（仍占用）：不重复复制归档，等下一轮
                record.update(action="skip", result="skipped", reason="retried_this_run",
                              detail="本轮已重试仍被占用，保留 deferred 至下一轮")
                record["target"] = target["path"]
                record["rel"] = rel_display(path, root)
                actions.append(record)
                continue
            if not params.get("execute") and record["action"] == "rotate":
                # dry-run 也要能预览走哪一级：占用探测 → 预览 B 路
                blocked, why = probe_rename_blocked(path)
                record["mode"] = MODE_COPY_TRUNCATE if blocked else MODE_RENAME
                if blocked:
                    record["detail"] = f"{record['detail']}；预览 mode=copy_truncate（{why}）"
            record["target"] = target["path"]
            record["rel"] = rel_display(path, root)
            actions.append(record)
        target_rows.append(row)
    return actions, target_rows, failures


# --------------------------------------------------------------------------
# deferred 清单（C 路兜底）
# --------------------------------------------------------------------------
def deferred_paths(root: Path) -> tuple[Path, Path]:
    base = root / DEFERRED_REL_DIR
    return base / DEFERRED_NAME, base / DEFERRED_DEAD_NAME


def norm_key(path: str | Path) -> str:
    """路径归一化（Windows 大小写不敏感），用于 deferred/dead 去重。"""
    try:
        return os.path.normcase(os.path.abspath(str(path)))
    except (OSError, ValueError):  # pragma: no cover
        return str(path)


def dead_path_set(dead_file: Path) -> set[str]:
    return {norm_key(item.get("path", "")) for item in load_deferred(dead_file)}


def load_deferred(path: Path) -> list[dict]:
    """读 deferred 清单；坏文件不阻塞主流程（记 WARN 后当空清单）。"""
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[WARN] deferred 清单不可读，本次按空清单处理：{path}（{exc}）")
        return []
    items = raw.get("items") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return []
    return [dict(it) for it in items if isinstance(it, dict) and it.get("path")]


def save_deferred(path: Path, items: list[dict]) -> None:
    """写 deferred 清单（先写 .tmp 再 os.replace，避免半截文件）。"""
    items = list(items or [])
    if not items and not path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": DEFERRED_SCHEMA,
        "updated_at": now_iso(),
        "max_attempts": DEFERRED_MAX_ATTEMPTS,
        "count": len(items),
        "items": items,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(text + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        # 临时文件不可替换时直接覆盖（沙箱下 unlink 可能被劫持，不做删除）
        path.write_text(text + "\n", encoding="utf-8")


def append_dead(path: Path, items: list[dict]) -> None:
    if not items:
        return
    try:
        old = json.loads(path.read_text(encoding="utf-8-sig")).get("items", [])
        if not isinstance(old, list):
            old = []
    except (OSError, json.JSONDecodeError):
        old = []
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": DEFERRED_SCHEMA,
        "kind": "dead",
        "updated_at": now_iso(),
        "count": len(old) + len(items),
        "items": list(old) + list(items),
    }
    try:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    except OSError as exc:  # pragma: no cover - 极端只读场景
        print(f"[WARN] rotate_deferred_dead.json 写入失败：{exc}")


def _deferred_entry(record: dict, now: datetime) -> dict:
    rel = record.get("rel") or record.get("path")
    return {
        "path": record["path"],
        "rel": rel,
        "dest_hint": record.get("dest"),
        "reason": record.get("defer_reason") or "rotate_failed",
        "detail": record.get("detail", ""),
        "first_seen": now.isoformat(timespec="seconds"),
        "last_attempt": now.isoformat(timespec="seconds"),
        "attempts": 1,
        "next_attempt": 2,
        "max_attempts": DEFERRED_MAX_ATTEMPTS,
        "archive": record.get("archive"),
    }


def _merge_queue(old: list[dict], new: list[dict]) -> list[dict]:
    """同名路径合并：老条目保留累计次数，只刷新明细；新条目直接入队。"""
    merged: dict[str, dict] = {str(item.get("path")): item for item in old}
    for item in new:
        key = str(item.get("path"))
        prev = merged.get(key)
        if prev is None:
            merged[key] = item
            continue
        prev.update({
            "detail": item.get("detail", prev.get("detail", "")),
            "last_attempt": item.get("last_attempt", now_iso()),
            "archive": item.get("archive") or prev.get("archive"),
            "reason": item.get("reason", prev.get("reason")),
        })
        prev["next_attempt"] = int(prev.get("attempts") or 1) + 1
    return list(merged.values())


# --------------------------------------------------------------------------
# 三级轮转核心
# --------------------------------------------------------------------------
def _gzip_and_recycle(src: Path, gz_path: Path, cap: dict) -> tuple[bool, bool, str]:
    """返回 (整体成功, 压缩是否完成, 说明)。"""
    try:
        with open(src, "rb") as f_in, gzip.open(gz_path, "wb", compresslevel=6) as f_out:
            shutil.copyfileobj(f_in, f_out)
    except OSError as exc:
        return False, False, f"gzip 失败（明文轮转件保留）：{exc}"
    ok, detail = recycle_one(src, cap)
    if not ok:
        return False, True, (f"压缩完成（{gz_path.name}），但明文副本未回收：{detail}")
    return True, True, f"压缩到 {gz_path.name}，明文副本已入回收站"


def rotate_one(src: Path, dest: Path, params: dict, cap: dict,
               record: dict, existing_archive: str | None = None) -> tuple[str, str, bool]:
    """执行单个文件的三级轮转，返回 (mode, detail, ok)。

    - A ``rename``：同目录 rename 成功；
    - B ``copy_truncate``：rename 失败 → 复制归档 + 原地截断；
    - C ``deferred``：复制/截断都失败（ok=False，交由上层登记 deferred）。
    """
    record.pop("defer_reason", None)
    prefix = ""
    try:
        os.rename(src, dest)  # 同目录、同卷，原子改名
    except OSError as exc:
        record["rename_error"] = f"{type(exc).__name__}: {exc}"
        record["defer_reason"] = "locked" if is_lock_error(exc) else "rename_error"
        prefix = ("rename 被占用（%s）" % exc) if is_lock_error(exc) else ("rename 失败：%s" % exc)
    else:
        if not params["gzip"]:
            return MODE_RENAME, f"已重命名为 {dest.name}", True
        gz_path = dest.with_name(dest.name + ".gz")
        ok, compressed, detail = _gzip_and_recycle(dest, gz_path, cap)
        record["gz_path"] = str(gz_path)
        record["gzip_ok"] = compressed
        return MODE_RENAME, f"已重命名为 {dest.name}；{detail}", ok

    # ---- B 路：复制归档 + 原地截断 ----
    if existing_archive:
        archive = Path(existing_archive)
        try:
            reuse = archive.exists() and archive.stat().st_size > 0
        except OSError:  # pragma: no cover
            reuse = False
        if reuse:
            try:
                truncate_in_place(src)
            except OSError as exc:
                record["defer_reason"] = "truncate_failed"
                return (MODE_DEFERRED,
                        f"{prefix}；归档已存在 {archive.name}，但截断仍失败：{exc}", False)
            return (MODE_COPY_TRUNCATE,
                    f"{prefix}；归档已存在 {archive.name}，已原地截断", True)

    try:
        archive = copy_to_archive(src, dest, params["gzip"])
    except OSError as exc:
        record["defer_reason"] = "copy_failed"
        return MODE_DEFERRED, f"{prefix}；复制归档失败：{exc}", False
    record["archive"] = str(archive)
    try:
        truncate_in_place(src)
    except OSError as exc:
        record["defer_reason"] = "truncate_failed"
        return (MODE_DEFERRED,
                f"{prefix}；已归档 {archive.name}，但原地截断失败：{exc}", False)
    return (MODE_COPY_TRUNCATE,
            f"{prefix}；已复制归档 {archive.name} 并原地截断为 0 字节", True)


def retry_deferred(queue: list[dict], params: dict, cap: dict, now: datetime
                   ) -> tuple[list[dict], list[dict], list[dict], list[str]]:
    """运行开头重试 deferred 清单。

    返回 (剩余队列, 本轮完成的动作, 转 dead 的条目, WARN 文案)。
    """
    remaining: list[dict] = []
    done: list[dict] = []
    dead: list[dict] = []
    warns: list[str] = []
    for item in queue:
        src = Path(str(item.get("path", "")))
        rel = item.get("rel") or str(src)
        if not src.is_file():
            warns.append(f"deferred 源文件已不存在，移出队列：{rel}")
            continue
        try:
            size = src.stat().st_size
        except OSError as exc:  # pragma: no cover
            warns.append(f"deferred 源文件 stat 失败，移出队列：{rel}（{exc}）")
            continue
        if size == 0:
            continue
        dest = rotation_dest(src, now)
        record = {
            "action": "rotate",
            "path": str(src),
            "rel": rel,
            "size": size,
            "dest": str(dest),
            "reason": "deferred_retry",
            "retried": True,
            "detail": f"重试第 {int(item.get('attempts') or 1) + 1} 次",
            "result": "ok",
        }
        mode, detail, ok = rotate_one(src, dest, params, cap, record,
                                      existing_archive=item.get("archive"))
        record["mode"] = mode
        record["detail"] = f"{record['detail']}；{detail}"
        attempts = int(item.get("attempts") or 1) + 1
        if mode == MODE_DEFERRED:
            record["result"] = "deferred"
            item.update(
                attempts=attempts,
                last_attempt=now.isoformat(timespec="seconds"),
                detail=record["detail"],
                reason=record.get("defer_reason") or item.get("reason"),
                archive=record.get("archive") or item.get("archive"),
            )
            if attempts >= DEFERRED_MAX_ATTEMPTS:
                item.update(dead_at=now.isoformat(timespec="seconds"),
                            result="dead",
                            detail=f"{record['detail']}（累计失败 {attempts} 次，转 dead）")
                dead.append(item)
                warns.append(f"deferred 重试 {attempts} 次仍失败，转入 "
                             f"{DEFERRED_DEAD_NAME}（不再自动重试）：{rel}")
            else:
                item["next_attempt"] = attempts + 1
                remaining.append(item)
        elif not ok:
            record["result"] = "failed"
        done.append(record)
    return remaining, done, dead, warns


def execute_actions(actions: list[dict], params: dict, cap: dict) -> list[dict]:
    """执行计划；返回本轮新增的 deferred 条目。"""
    newly_deferred: list[dict] = []
    now = datetime.now()
    for action in actions:
        kind = action["action"]
        if kind == "rotate":
            src = Path(action["path"])
            dest = Path(action["dest"])
            mode, detail, ok = rotate_one(src, dest, params, cap, action)
            action["mode"] = mode
            action["detail"] = f"{action['detail']}；{detail}"
            if mode == MODE_DEFERRED:
                action["result"] = "deferred"
                newly_deferred.append(_deferred_entry(action, now))
                continue
            action["result"] = "ok" if ok else "failed"
        elif kind == "recycle":
            if not cap["available"]:
                action.update(
                    result="failed",
                    detail=(f"回收站不可用（{','.join(cap['reasons'])}），"
                            "拒绝删除，文件已保留"),
                )
                continue
            ok, detail = recycle_one(Path(action["path"]), cap)
            action["detail"] = detail
            action["result"] = "ok" if ok else "failed"
        # keep / skip：不动
    return newly_deferred


# --------------------------------------------------------------------------
# 输出
# --------------------------------------------------------------------------
def summarize(actions: list[dict], target_rows: list[dict], failures: list[dict],
              scanned: int) -> dict:
    counts = Counter(a["action"] for a in actions)
    skip_reasons = Counter(a.get("reason") for a in actions if a["action"] == "skip")
    mode_counts = Counter(a.get("mode") for a in actions if a.get("mode"))
    failed = [a for a in actions if a.get("result") == "failed"] + failures
    summary = {
        "files_scanned": scanned,
        "rotate": counts.get("rotate", 0),
        "recycle": counts.get("recycle", 0),
        "keep": counts.get("keep", 0),
        "skip": counts.get("skip", 0),
        "fail": len(failed),
        "deferred": sum(1 for a in actions if a.get("result") == "deferred"),
        "modes": dict(sorted(mode_counts.items())),
        "rotate_bytes": sum(a.get("size") or 0 for a in actions
                            if a["action"] == "rotate"),
        "recycle_bytes": sum(a.get("size") or 0 for a in actions
                             if a["action"] == "recycle"),
        "skip_reasons": dict(sorted(skip_reasons.items(),
                                    key=lambda kv: (-kv[1], str(kv[0])))),
        "targets_scanned": sum(1 for t in target_rows if t["status"] == "scanned"),
        "targets_missing": sum(1 for t in target_rows if t["status"] == "missing"),
    }
    return summary


def mode_line(summary: dict, write: bool) -> str:
    """输出末尾的 mode= 汇总行——P5 wrapper 只截最后两行，靠它判读三级结果。"""
    modes = summary.get("modes") or {}
    parts = [f"mode={m}={modes.get(m, 0)}"
             for m in (MODE_RENAME, MODE_COPY_TRUNCATE, MODE_DEFERRED)]
    tag = "模式" if write else "模式预览（dry-run）"
    return f"  {tag}：" + " ".join(parts)


def render_text(result: dict, verbose: bool) -> None:
    params = result["params"]
    cap = result["recycle"]
    mode = "execute" if result["write"] else "dry-run"
    print(f"{TOOL_NAME} v{TOOL_VERSION} · {mode} · root={result['root']}")
    if result.get("drill"):
        print("  注意：--root 非仓库根，本次为演练/夹具模式")
    print(
        "  参数：max_mb={max_mb:g}  keep_days={keep_days}  "
        "active_guard={active_guard_min:g}min  gzip={gzip}".format(
            max_mb=params["max_mb"], keep_days=params["keep_days"],
            active_guard_min=params["active_guard_min"],
            gzip="on" if params["gzip"] else "off",
        )
    )
    if cap["available"]:
        print("  回收站：可用（SHFileOperationW + FOF_ALLOWUNDO）")
    else:
        print(f"  回收站：不可用（{','.join(cap['reasons'])}）——"
              "保留动作会记 FAIL 并保留文件，绝不静默永久删除")
    print("  目标：")
    for row in result["targets"]:
        if row["status"] == "scanned":
            print(f"    [扫描] {row['path']}  ({row['files']} 个日志文件)")
        else:
            print(f"    [缺失] {row['path']}  （不存在，跳过）")

    print("  动作：")
    for action in result["actions"]:
        if action["action"] in ("skip", "keep"):
            # 默认只展示「正在写」这类安全相关跳过；--verbose 才全量列
            if not verbose and action.get("reason") not in ("active", "dead_deferred"):
                continue
        rel = action.get("rel") or action["path"]
        size = human_size(action.get("size") or 0)
        if action["action"] == "rotate":
            extra = ""
            if action.get("result") == "failed":
                extra = "  [FAILED]"
            elif action.get("result") == "planned":
                extra = "  (dry-run)"
            elif action.get("result") == "deferred":
                extra = "  [DEFERRED]"
            print(f"    [ROTATE ] {rel}  {size}  mode={action.get('mode') or '?'}  "
                  f"{action['detail']}{extra}")
            if action.get("dest"):
                print(f"              -> {Path(action['dest']).name}")
        elif action["action"] == "recycle":
            status = {"planned": " (dry-run)", "ok": " (done)",
                      "failed": " [FAILED]"}.get(action.get("result"), "")
            print(f"    [RECYCLE] {rel}  {size}  {action['detail']}{status}")
        elif action["action"] == "fail":
            print(f"    [FAIL   ] {rel}  {action['detail']}")
        elif action["action"] == "keep":
            if verbose:
                print(f"    [KEEP   ] {rel}  {size}  {action['detail']}")
        elif action["action"] == "skip":
            if verbose or action.get("reason") in ("active", "dead_deferred"):
                print(f"    [SKIP   ] {rel}  {size}  {action['detail']}")

    summary = result["summary"]
    reasons = ", ".join(f"{k}={v}" for k, v in summary["skip_reasons"].items())
    print(
        "  汇总：scanned={scanned} rotate={rotate} recycle={recycle} "
        "keep={keep} skip={skip} fail={fail} deferred={deferred}".format(
            scanned=summary["files_scanned"], rotate=summary["rotate"],
            recycle=summary["recycle"], keep=summary["keep"],
            skip=summary["skip"], fail=summary["fail"],
            deferred=summary.get("deferred", 0),
        )
    )
    if reasons:
        print(f"        跳过原因：{reasons}")
    deferred = result.get("deferred") or {}
    if deferred.get("count"):
        verb = "待重试" if not result["write"] else "已登记"
        print(f"  deferred {verb}（{deferred['count']} 条；清单 {deferred['file']}）：")
        for item in deferred.get("items") or []:
            print(f"    [DEFER  ] {item.get('rel')}  mode=deferred  "
                  f"attempts={item.get('attempts')}/{item.get('max_attempts')}  "
                  f"next={item.get('next_attempt')}  {item.get('detail', '')}")
    failures = [a for a in result["actions"] if a.get("result") == "failed"] + \
        result.get("failures", [])
    if failures:
        print("  失败：")
        for item in failures:
            print(f"    - {item.get('path')}  [{item.get('reason')}] {item.get('detail','')}")
    for msg in result.get("warnings") or []:
        # 走 stdout：P5 wrapper 用 2>&1 抓取且只留最后两行，stderr 会被缓冲打乱顺序
        print(f"[WARN] {msg}")
    summary = result["summary"]
    modes = summary.get("modes") or {}
    brief = " ".join(f"mode={m}:{modes.get(m, 0)}"
                     for m in (MODE_RENAME, MODE_COPY_TRUNCATE, MODE_DEFERRED))
    print(mode_line(summary, result["write"]))
    if not result["write"]:
        print(f"  dry-run：未改动任何文件；确认后加 --execute（{brief}）。")
    else:
        print(f"  执行完毕；退出码 {result['exit_code']}（{brief}）。")


def emit_json(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def emit_recycle_check(cap: dict, as_json: bool, root: Path) -> int:
    code = 0 if cap["available"] else 2
    payload = {
        "tool": TOOL_NAME,
        "schema": SCHEMA,
        "checked_at": now_iso(),
        "root": str(root),
        "recycle": cap,
        "ok": cap["available"],
        "exit_code": code,
    }
    if as_json:
        emit_json(payload)
        return code
    print("回收站能力检查")
    print(f"  平台：{cap['platform']}")
    print(f"  用户 SID：{cap['user_sid']}")
    print(f"  回收站目录：{cap['bin_dir']}")
    print(f"  进程完整性级别：{cap['integrity']}")
    print(f"  AppContainer：{cap['appcontainer']}")
    print(f"  回收站目录可写（CreateFileW GENERIC_WRITE）：{cap['bin_dir_writable']}"
          f"（{cap['probe_error']}）")
    print(f"  NukeOnDelete：{cap['nuke_on_delete']}")
    print(f"  结论：{'可用' if cap['available'] else '不可用'}")
    print(f"  说明：{cap['detail']}")
    return code


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    validate_args(args)
    root, drill = resolve_root(args.root)
    params = {
        "max_mb": args.max_mb,
        "max_bytes": int(args.max_mb * 1024 * 1024),
        "keep_days": args.keep_days,
        "active_guard_min": args.active_guard_min,
        "gzip": bool(args.gzip),
        "execute": bool(args.execute),
        "keep_cutoff": datetime.now() - timedelta(days=args.keep_days),
    }
    cap = recycle_capability()

    if args.recycle_check:
        return emit_recycle_check(cap, args.json, root)

    cfg = load_config(args.config)
    targets = resolve_targets(root, cfg, args.target, drill)

    now = datetime.now()
    deferred_path, dead_path = deferred_paths(root)
    warnings: list[str] = []
    retried_actions: list[dict] = []
    dead_items: list[dict] = []

    # 1) 开头先重试上一轮 deferred 清单（dry-run 只预览，不动文件）
    queue = load_deferred(deferred_path)
    if queue and args.execute:
        queue, retried_actions, dead_items, retry_warns = retry_deferred(
            queue, params, cap, now)
        warnings.extend(retry_warns)
        append_dead(dead_path, dead_items)
    elif queue:
        for item in queue:
            src = Path(str(item.get("path", "")))
            dest_text = item.get("dest_hint") or ""
            if src.is_file():
                dest_text = str(rotation_dest(src, now))
            retried_actions.append({
                "action": "rotate",
                "path": str(src),
                "rel": item.get("rel") or str(src),
                "size": None,
                "dest": dest_text,
                "reason": "deferred_retry",
                "result": "planned",
                "mode": MODE_DEFERRED,
                "retried": True,
                "detail": f"deferred 待重试（第 {int(item.get('attempts') or 1) + 1} 次）",
            })

    # 2) 常规扫描 + 执行
    params["dead_paths"] = dead_path_set(dead_path)
    params["deferred_paths"] = {norm_key(item.get("path", "")) for item in queue}
    actions, target_rows, walk_failures = build_plan(root, targets, cfg, params, now)
    if args.execute:
        new_deferred = execute_actions(actions, params, cap)
        queue = _merge_queue(queue, new_deferred)
        save_deferred(deferred_path, queue)

    all_actions = retried_actions + actions
    failures = [a for a in all_actions if a.get("result") == "failed"] + walk_failures
    summary = summarize(all_actions, target_rows, walk_failures,
                        sum(row["files"] for row in target_rows))
    exit_code = 1 if failures else 0

    result = {
        "tool": TOOL_NAME,
        "schema": SCHEMA,
        "version": TOOL_VERSION,
        "generated_at": now_iso(),
        "mode": "execute" if args.execute else "dry-run",
        "write": bool(args.execute),
        "drill": drill,
        "root": str(root),
        "params": {
            "max_mb": params["max_mb"],
            "max_bytes": params["max_bytes"],
            "keep_days": params["keep_days"],
            "active_guard_min": params["active_guard_min"],
            "gzip": params["gzip"],
        },
        "recycle": cap,
        "targets": target_rows,
        "actions": all_actions,
        "failures": walk_failures,
        "deferred": {
            "file": str(deferred_path),
            "dead_file": str(dead_path),
            "count": len(queue),
            "dead": len(dead_items),
            "max_attempts": DEFERRED_MAX_ATTEMPTS,
            "items": queue,
        },
        "warnings": warnings,
        "summary": summary,
        "ok": not failures,
        "exit_code": exit_code,
    }
    if args.json:
        emit_json(result)
    else:
        render_text(result, args.verbose)
    return exit_code


def main(argv: list[str] | None = None) -> int:
    as_json = "--json" in (argv if argv is not None else sys.argv[1:])
    try:
        return run(argv)
    except Fatal as exc:
        if as_json:
            emit_json({
                "tool": TOOL_NAME,
                "schema": SCHEMA,
                "ok": False,
                "error": exc.message,
                "exit_code": 2,
            })
        else:
            print(f"[错误] {exc.message}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:  # pragma: no cover
        print("[中断] 已停止（已完成的部分不会回滚）", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
