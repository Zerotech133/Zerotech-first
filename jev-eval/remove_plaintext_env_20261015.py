# -*- coding: utf-8 -*-
"""jev 明文凭据清除（密钥收口日 2026-10-15，用户拍板"明文清除"）。

删两个明文文件（jev-gate.env.plain.ps1 / jev-gate.env.ps1.bak_20261014）：
1) 先整体备份到 %TEMP%（可逆第一层）；
2) 走回收站（SHFileOperationW FOF_ALLOWUNDO，可逆第二层）；
3) os.path.exists 逐个复核（返回值不可信判例）。
前提已实证：DPAPI .enc 解密 OK（len=107）、env_fallback.flag 不存在。
"""
import ctypes
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TARGETS = [os.path.join(HERE, "jev-gate.env.plain.ps1"),
           os.path.join(HERE, "jev-gate.env.ps1.bak_20261014")]
BACKUP_DIR = os.path.join(tempfile.gettempdir(),
                          "jev_plaintext_removal_20261015")


def main():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    for fp in TARGETS:
        if os.path.exists(fp):
            dst = os.path.join(BACKUP_DIR, os.path.basename(fp))
            shutil.copy2(fp, dst)
            print("backup -> %s" % dst)

    buf = ctypes.create_unicode_buffer("\0".join(TARGETS) + "\0\0")
    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", ctypes.c_void_p),
                    ("wFunc", ctypes.c_uint),
                    ("pFrom", ctypes.c_wchar_p),
                    ("pTo", ctypes.c_wchar_p),
                    ("fFlags", ctypes.c_ushort),
                    ("fAnyOperationsAborted", ctypes.c_int),
                    ("hNameMappings", ctypes.c_void_p),
                    ("lpszProgressTitle", ctypes.c_wchar_p)]
    op = SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = 3                      # FO_DELETE
    op.pFrom = "\0".join(TARGETS) + "\0"
    op.pTo = None
    op.fFlags = 0x0040 | 0x0010 | 0x0004   # ALLOWUNDO|NOCONFIRMATION|SILENT
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    print("SHFileOperationW rc=%d aborted=%s（返回值不可信，以下复核为准）"
          % (rc, bool(op.fAnyOperationsAborted)))

    gone = True
    for fp in TARGETS:
        still = os.path.exists(fp)
        print("exists after: %s -> %s" % (os.path.basename(fp), still))
        gone = gone and not still
    print("RESULT:", "ALL RECYCLED" if gone else "SOME REMAIN — CHECK")
    return 0 if gone else 1


if __name__ == "__main__":
    sys.exit(main())
