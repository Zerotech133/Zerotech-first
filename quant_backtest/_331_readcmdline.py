# -*- coding: utf-8 -*-
"""331：用 psutil 式只读方式取目标 PID 命令行（纯 ctypes NtQueryInformationProcess）。"""

import ctypes

ntdll = ctypes.WinDLL("ntdll")
k32 = ctypes.windll.kernel32

TARGETS = [20424, 46620, 65096, 74232, 46396]


class PBI(ctypes.Structure):
    _fields_ = [
        ("ExitStatus", ctypes.c_void_p),
        ("PebBaseAddress", ctypes.c_void_p),
        ("AffinityMask", ctypes.c_void_p),
        ("BasePriority", ctypes.c_void_p),
        ("UniqueProcessId", ctypes.c_void_p),
        ("InheritedFromUniqueProcessId", ctypes.c_void_p),
    ]


def read_mem(h, addr, size):
    buf = ctypes.create_string_buffer(size)
    got = ctypes.c_size_t()
    if not k32.ReadProcessMemory(
        h, ctypes.c_void_p(addr), buf, size, ctypes.byref(got)
    ):
        return None
    return buf.raw[: got.value]


def cmdline_of(pid):
    h = k32.OpenProcess(0x1FFF, False, pid)  # QUERY | READ VM
    if not h:
        return None
    try:
        pbi = PBI()
        rl = ctypes.c_ulong()
        st = ntdll.NtQueryInformationProcess(
            h, 0, ctypes.byref(pbi), ctypes.sizeof(pbi), ctypes.byref(rl)
        )
        if st:
            return None
        peb = pbi.PebBaseAddress
        ctypes.c_void_p()
        if not read_mem(h, peb + 0x20, 8):
            return None
        raw = read_mem(h, peb + 0x20, 8)
        uptr = int.from_bytes(raw, "little")
        raw = read_mem(
            h, uptr + 0x70, 16
        )  # UNICODE_STRING CmdLine at 0x70 (x64 RTL_USER_PROCESS_PARAMETERS)
        if not raw:
            return None
        ulen, umax = (
            int.from_bytes(raw[0:2], "little"),
            int.from_bytes(raw[2:4], "little"),
        )
        uptr2 = int.from_bytes(raw[8:16], "little")
        raw = read_mem(h, uptr2, ulen if ulen else umax * 2)
        if not raw:
            return None
        return raw.decode("utf-16-le", errors="replace")
    finally:
        k32.CloseHandle(h)


for pid in TARGETS:
    try:
        cl = cmdline_of(pid)
    except Exception as e:
        cl = "ERR %s" % e
    print(pid, "->", (cl[:150] if cl else cl))
