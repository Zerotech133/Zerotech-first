# -*- coding: utf-8 -*-
"""原生 GDI 窗口盘口模拟（与通达信同机制），验证 PrintWindow 在 Win32 GDI 程序上是否实时。

通达信/同花顺是传统 Win32 GDI 程序：内容直接画到窗口 DC，PrintWindow 触发 WM_PRINT
即得到当前绘制结果，没有 Chromium 的 GPU 合成/遮挡停渲染问题。
本脚本用同样机制造窗口，用于对照。
"""
import ctypes, ctypes.wintypes as wt, threading, time, random

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32

CS_HREDRAW, CS_VREDRAW = 0x0002, 0x0001
WS_OVERLAPPEDWINDOW = 0x00CF0000
WS_VISIBLE = 0x10000000
SW_SHOW = 5
WM_PAINT, WM_DESTROY, WM_ERASEBKGND = 0x000F, 0x0002, 0x0014
DT_LEFT, DT_RIGHT, DT_SINGLELINE = 0x0000, 0x0002, 0x0020

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

# WPARAM/LPARAM 是 64 位，不声明 argtypes 时部分消息(如 WM_SETCURSOR)会把高位当 int 溢出
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_ssize_t

TAGS = ['卖' + '一二三四五六七八九十'[i - 1] for i in range(10, 0, -1)] + \
       ['买' + '一二三四五六七八九十'[i - 1] for i in range(1, 11)]


class WNDCLASSEX(ctypes.Structure):
    _fields_ = [('cbSize', wt.UINT), ('style', wt.UINT), ('lpfnWndProc', WNDPROC),
                ('cbClsExtra', ctypes.c_int), ('cbWndExtra', ctypes.c_int),
                ('hInstance', wt.HINSTANCE), ('hIcon', wt.HICON), ('hCursor', wt.HANDLE),
                ('hbrBackground', wt.HBRUSH), ('lpszMenuName', wt.LPCWSTR),
                ('lpszClassName', wt.LPCWSTR), ('hIconSm', wt.HICON)]


class RECT(ctypes.Structure):
    _fields_ = [('left', wt.LONG), ('top', wt.LONG), ('right', wt.LONG), ('bottom', wt.LONG)]


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [('hdc', wt.HDC), ('fErase', wt.BOOL), ('rcPaint', RECT),
                ('fRestore', wt.BOOL), ('fIncUpdate', wt.BOOL),
                ('rgbReserved', ctypes.c_byte * 32)]


ROW_TOP, ROW_H = 34, 27


class GdiBookWindow:
    def __init__(self, title='FAKE_TDX_000001', w=420, h=790):
        self.title, self.w, self.h = title, w, h
        self.book = {t: ('0.00', '0') for t in TAGS}
        self.base = 1680.00
        self._hwnd = None
        self._ready = threading.Event()
        self._proc = WNDPROC(self._wndproc)
        self._cls = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(10)

    # ---------- GDI 绘制
    def _wndproc(self, hwnd, msg, wp, lp):
        if msg == WM_PAINT:
            ps = PAINTSTRUCT()
            hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
            rc = RECT()
            user32.GetClientRect(hwnd, ctypes.byref(rc))
            brush = gdi32.CreateSolidBrush(0x000000)
            user32.FillRect(hdc, ctypes.byref(rc), brush)
            gdi32.DeleteObject(brush)

            gdi32.SetBkMode(hdc, 1)                     # TRANSPARENT
            font = gdi32.CreateFontW(17, 0, 0, 0, 400, 0, 0, 0, 134, 0, 0, 0, 0, 'SimSun')
            old = gdi32.SelectObject(hdc, font)
            for i, tag in enumerate(TAGS):
                y = ROW_TOP + i * ROW_H
                px, vol = self.book[tag]
                is_ask = tag.startswith('卖')
                r = RECT(6, y, 70, y + ROW_H)
                gdi32.SetTextColor(hdc, 0x00FFFF)       # 黄 (BGR)
                user32.DrawTextW(hdc, tag, -1, ctypes.byref(r), DT_LEFT | DT_SINGLELINE)
                r2 = RECT(80, y, 170, y + ROW_H)
                gdi32.SetTextColor(hdc, 0x3C3CFF if is_ask else 0x78DC00)   # 红/绿
                user32.DrawTextW(hdc, px, -1, ctypes.byref(r2), DT_RIGHT | DT_SINGLELINE)
                r3 = RECT(190, y, 290, y + ROW_H)
                gdi32.SetTextColor(hdc, 0xEBEBEB)
                user32.DrawTextW(hdc, vol, -1, ctypes.byref(r3), DT_RIGHT | DT_SINGLELINE)
            gdi32.SelectObject(hdc, old)
            gdi32.DeleteObject(font)
            user32.EndPaint(hwnd, ctypes.byref(ps))
            return 0
        if msg == WM_ERASEBKGND:
            return 1
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wp, lp)

    def _run(self):
        hinst = kernel32.GetModuleHandleW(None)
        name = f'GdiBook{random.randint(1000, 9999)}'
        wc = WNDCLASSEX()
        wc.cbSize = ctypes.sizeof(WNDCLASSEX)
        wc.style = CS_HREDRAW | CS_VREDRAW
        wc.lpfnWndProc = self._proc
        wc.hInstance = hinst
        wc.hbrBackground = gdi32.CreateSolidBrush(0)
        wc.lpszClassName = name
        if not user32.RegisterClassExW(ctypes.byref(wc)):
            print('RegisterClassEx 失败', kernel32.GetLastError())
            self._ready.set()
            return
        self._cls = wc
        self._hwnd = user32.CreateWindowExW(0, name, self.title, WS_OVERLAPPEDWINDOW,
                                            100, 60, self.w, self.h, None, None, hinst, None)
        if not self._hwnd:
            print('CreateWindowEx 失败', kernel32.GetLastError())
            self._ready.set()
            return
        user32.ShowWindow(self._hwnd, SW_SHOW)
        user32.UpdateWindow(self._hwnd)
        self._ready.set()
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    @property
    def hwnd(self):
        return self._hwnd

    def set_book(self):
        """刷新数字并请求重绘（不依赖任何定时器/合成器，立即可见）"""
        b = {}
        for i, tag in enumerate(TAGS):
            n = 10 - i if tag.startswith('卖') else (i - 10 + 1)
            px = self.base + 0.01 * n if tag.startswith('卖') else self.base - 0.01 * n
            px = round(px + random.choice([0, 0, 0, 0, 0.01, -0.01]), 2)
            vol = random.randint(1, 99999)
            b[tag] = (f'{px:.2f}', str(vol))
        self.book = b
        user32.InvalidateRect(self._hwnd, None, True)
        user32.UpdateWindow(self._hwnd)
        return b

    def close(self):
        if self._hwnd:
            user32.PostMessageW(self._hwnd, 0x0010, 0, 0)   # WM_CLOSE
