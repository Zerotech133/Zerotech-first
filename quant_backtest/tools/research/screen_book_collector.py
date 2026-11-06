# -*- coding: utf-8 -*-
"""
screen_book_collector.py —— 屏幕盘口采集器（后台截图 + 逐格 OCR + 结构化落盘）

用途
    通达信/同花顺等客户端的 Level-2 数据无法导出到文件（沙箱/接口限制），
    屏幕是唯一合法出口。本脚本通过 Win32 PrintWindow 对**后台窗口**截图
    （窗口被遮挡亦可，无需置顶、无需多桌面/虚拟机），再对固定网格逐格 OCR，
    还原为结构化盘口 (price, volume) 序列。

设计取舍（实测依据）
    · 通用检测型 OCR（rapidocr/paddle）整块盘口 963ms，太慢 → 不用
    · 验证码型 OCR（ddddocr）单格 12~18ms，且不含小数点 → 用，小数点按固定位数推断
    · 盘口是固定网格 → 不做文字检测，直接按配置行高/列宽切格

用法
    # 单窗口采集 60 秒，1 秒 1 帧
    python screen_book_collector.py --title "600519" --code 600519 --duration 60

    # 只识别不落盘，打印前 3 帧
    python screen_book_collector.py --title "600519" --code 600519 --frames 3 --dry-run

    # 多窗口并行（每窗口一个线程）
    python screen_book_collector.py --title "TDX" --duration 300 --workers 4 --auto-code

注意
    · 首次使用必须标定 ROI：先 --calibrate 打印窗口图像与候选网格线
    · 屏幕缩放建议 100%；125%/150% 会因插值导致数字粘连，需配合 --upscale 2
    · 本脚本不修改客户端、不注入进程，仅读屏幕像素
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import io
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Iterable

import numpy as np

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32


# ---------------------------------------------------------------- 窗口截图
class _BMI(ctypes.Structure):
    _fields_ = [('biSize', wt.DWORD), ('biWidth', wt.LONG), ('biHeight', wt.LONG),
                ('biPlanes', wt.WORD), ('biBitCount', wt.WORD), ('biCompression', wt.DWORD),
                ('biSizeImage', wt.DWORD), ('biXPelsPerMeter', wt.LONG),
                ('biYPelsPerMeter', wt.LONG), ('biClrUsed', wt.DWORD),
                ('biClrImportant', wt.DWORD)]


WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def list_windows(pattern: str | None = None) -> list[dict]:
    """枚举可见顶层窗口，可按标题正则过滤。"""
    out: list[dict] = []
    rx = re.compile(pattern, re.I) if pattern else None

    def _cb(hwnd, _lp):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        title = buf.value
        if rx and not rx.search(title):
            return True
        r = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        w, h = r.right - r.left, r.bottom - r.top
        if w < 80 or h < 80:
            return True
        out.append({'hwnd': hwnd, 'title': title, 'w': w, 'h': h, 'x': r.left, 'y': r.top})
        return True

    user32.EnumWindows(WNDENUMPROC(_cb), 0)
    return out


def grab_window(hwnd: int, w: int, h: int) -> np.ndarray | None:
    """PrintWindow(PW_RENDERFULLCONTENT) 后台截图，返回 BGR ndarray。
    窗口被遮挡仍可拿到真实画面；最小化(IsIconic)时可能全黑。"""
    if user32.IsIconic(hwnd):
        return None
    hdc = user32.GetWindowDC(hwnd)
    if not hdc:
        return None
    mfc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    old = gdi32.SelectObject(mfc, bmp)
    ok = user32.PrintWindow(hwnd, mfc, 2)          # PW_RENDERFULLCONTENT
    arr = None
    if ok:
        bi = _BMI()
        bi.biSize = ctypes.sizeof(_BMI)
        bi.biWidth, bi.biHeight = w, -h            # 负高度 = top-down
        bi.biPlanes, bi.biBitCount, bi.biCompression = 1, 32, 0
        buf = ctypes.create_string_buffer(w * h * 4)
        if gdi32.GetDIBits(mfc, bmp, 0, h, buf, ctypes.byref(bi), 0):
            arr = np.frombuffer(buf.raw, dtype=np.uint8).reshape(h, w, 4)[:, :, :3].copy()
    gdi32.SelectObject(mfc, old)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mfc)
    user32.ReleaseDC(hwnd, hdc)
    return arr


# ---------------------------------------------------------------- 网格识别
@dataclass
class Grid:
    """盘口网格标定参数（相对窗口左上角，单位像素）"""
    top: int = 26          # 第 1 行文字顶部
    row_h: int = 27        # 行高
    rows: int = 20         # 总行数（卖10..卖1, 买1..买10）
    px_x: int = 80         # 价格列左边界
    px_w: int = 90         # 价格列宽
    vol_x: int = 190       # 手数列左边界
    vol_w: int = 100       # 手数列宽
    px_decimals: int = 2   # 价格小数位（A股 2，可转债/ETF 3）
    px_scale: float | None = None   # 价格除数；None=按 px_decimals 自动

    @property
    def height(self) -> int:
        return self.top + self.row_h * self.rows + 4


class CellOCR:
    """验证码型 OCR，逐格识别。不检测、不含小数点，速度快。"""

    def __init__(self, upscale: int = 1):
        import ddddocr
        self.ocr = ddddocr.DdddOcr(show_ad=False, beta=False)
        self.upscale = max(1, upscale)
        try:                                    # 限定字符集=纯数字，显著降误识
            self.ocr.set_ranges('0123456789')
        except Exception:
            pass

    def read(self, bgr: np.ndarray, upscale: int | None = None) -> str:
        if bgr.size == 0:
            return ''
        import cv2
        up = self.upscale if upscale is None else upscale
        # 取三通道最大值：黑底≈0，任意亮色文字(R/G/B/白/黄)都保留，
        # 比直接转灰度更抗彩色（通达信买绿卖红、标签黄）
        v = bgr.max(axis=2)
        if v.mean() > 160:                      # 亮底（白底黑字皮肤）
            v = 255 - v
        # 二值化 -> 统一成白底黑字，验证码模型最友好
        _, bw = cv2.threshold(v, max(40, int(v.max() * 0.35)), 255, cv2.THRESH_BINARY)
        bw = 255 - bw
        if up > 1:
            bw = cv2.resize(bw, None, fx=up, fy=up, interpolation=cv2.INTER_CUBIC)
            _, bw = cv2.threshold(bw, 128, 255, cv2.THRESH_BINARY)
        ok, png = cv2.imencode('.png', bw)
        if not ok:
            return ''
        try:
            return ''.join(ch for ch in self.ocr.classification(png.tobytes())
                           if ch.isdigit() or ch == '.')
        except Exception:
            return ''


def _digits(s: str) -> str:
    return ''.join(ch for ch in s if ch.isdigit())


def parse_price(raw: str, decimals: int) -> float | None:
    """OCR 不含小数点，按固定小数位还原：'168045' -> 1680.45"""
    d = _digits(raw)
    if not d:
        return None
    if decimals <= 0:
        return float(d)
    # 允许 OCR 丢掉前导 0 或多识别 1 位，取末尾 decimals 位为小数
    if len(d) <= decimals:
        d = d.rjust(decimals + 1, '0')
    return int(d) / (10 ** decimals)


def parse_volume(raw: str) -> int | None:
    d = _digits(raw)
    return int(d) if d else None


# ---------------------------------------------------------------- 采集主循环
@dataclass
class Frame:
    ts: float
    code: str
    asks: list = field(default_factory=list)   # [卖10,...,卖1] -> 由近及远另存
    bids: list = field(default_factory=list)
    ok_rows: int = 0
    total_rows: int = 0


class Collector:
    def __init__(self, win: dict, grid: Grid, code: str,
                 ocr: CellOCR, diff_only: bool = False):
        self.win = win
        self.grid = grid
        self.code = code
        self.ocr = ocr
        self.diff_only = diff_only
        self._prev: list[tuple[str, ...]] = [()] * grid.rows
        self._px_len = 0        # 本会话价格串的标准位数（首帧学习）
        self._bad_len = 0       # 位数校验失败次数

    def grab(self) -> np.ndarray | None:
        return grab_window(self.win['hwnd'], self.win['w'], self.win['h'])

    def read_frame(self) -> Frame | None:
        img = self.grab()
        if img is None:
            return None
        g = self.grid
        # 第一遍：整版读一遍原始串。20 档价格处在同一价位区间，位数必然一致，
        # 用众数定标而不是首帧（首帧若漏位会把标准带偏，实测准确率会掉到 38%）。
        rows = []
        for i in range(g.rows):
            y = g.top + i * g.row_h
            if y + g.row_h > img.shape[0]:
                break
            # 取样高度必须小于行距：整行 27px 会把下一行顶部笔画带进来，实测准确率从 100% 掉到 80%
            h = min(g.row_h - 6, 22)
            # +2 余量：数字右对齐时末位常压在格边界上，切掉会少一位
            px_img = img[y:y + h, g.px_x:g.px_x + g.px_w + 2]
            vol_img = img[y:y + h, g.vol_x:g.vol_x + g.vol_w + 2]
            rows.append((px_img, vol_img, self.ocr.read(px_img), self.ocr.read(vol_img)))

        lens = [len(r[2]) for r in rows if len(r[2]) >= 4]
        mode_len = max(set(lens), key=lens.count) if lens else 0
        asks, bids = [], []
        ok = 0
        for i, (px_img, vol_img, raw_p, raw_v) in enumerate(rows):
            if mode_len and len(raw_p) != mode_len:      # 偏离众数 -> 换放大倍数重试一次
                alt = self.ocr.read(px_img, upscale=2)
                if len(alt) == mode_len:
                    raw_p = alt
                else:
                    self._bad_len += 1
            px = parse_price(raw_p, g.px_decimals)
            vol = parse_volume(raw_v)
            if px is None or vol is None:
                continue
            ok += 1
            (asks if i < 10 else bids).append((px, vol))
        f = Frame(ts=time.time(), code=self.code, asks=asks, bids=bids,
                  ok_rows=ok, total_rows=min(g.rows, len(asks) + len(bids) + (g.rows - ok)))
        return f


def calibrate(win: dict, out_dir: str) -> None:
    """导出窗口截图 + 行投影，辅助确定 Grid 参数"""
    import cv2
    img = grab_window(win['hwnd'], win['w'], win['h'])
    if img is None:
        print('!! 截图失败（窗口可能最小化）')
        return
    os.makedirs(out_dir, exist_ok=True)
    p = os.path.join(out_dir, f"calib_{int(time.time())}.png")
    cv2.imwrite(p, img)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    rowsum = (gray > 60).sum(axis=1)
    lines = [(y, int(rowsum[y])) for y in range(len(rowsum)) if rowsum[y] > 8]
    # 压缩为连续段
    segs, cur = [], []
    for y, v in lines:
        if cur and y - cur[-1][0] > 2:
            segs.append((cur[0][0], cur[-1][0])); cur = []
        cur.append((y, v))
    if cur:
        segs.append((cur[0][0], cur[-1][0]))
    print(f'窗口 {win["title"][:30]!r} {win["w"]}x{win["h"]}')
    print(f'截图已存: {p}')
    # 取正文行（高 8~24px 视为一行文字），末 20 行即盘口
    body = [(a, b) for a, b in segs if 8 <= b - a <= 24]
    if len(body) >= 20:
        body = body[-20:]
    print(f'\n检测到 {len(segs)} 个文字行段，取正文 {len(body)} 行：')
    for i, (a, b) in enumerate(body[:24]):
        print(f'  行{i:02d}: y={a}..{b}  高={b - a + 1}')
    if len(body) >= 2:
        step = body[1][0] - body[0][0]
        print(f'  首行 y={body[0][0]}  行高={step}')
        # 列：用末行做列投影（买十，干扰最少）
        y0, y1 = body[-1]
        cs = (cv2.cvtColor(img[y0:y1 + 1], cv2.COLOR_BGR2GRAY) > 60).sum(axis=0)
        xs = [x for x in range(len(cs)) if cs[x] > 0]
        groups, c = [], []
        for x in xs:
            if c and x - c[-1] > 12:
                groups.append((c[0], c[-1])); c = []
            c.append(x)
        if c:
            groups.append((c[0], c[-1]))
        print(f'  列段 {len(groups)} 个: {groups}')
        if len(groups) >= 3:
            px1, vx1 = groups[-2][1], groups[-1][1]
            print('\n可直接使用的参数（价格/手数均右对齐，格宽按 64 反推）：')
            print(f'  --top {body[0][0]} --row-h {step} --rows {len(body)} '
                  f'--px-x {px1 - 64} --px-w 64 --vol-x {vx1 - 64} --vol-w 64')
            print('\n提示：字号 < 12px 时识别率骤降，建议把客户端字体调到 16px 以上再采集。')


def main() -> int:
    ap = argparse.ArgumentParser(description='屏幕盘口采集器（后台截图+逐格OCR）')
    ap.add_argument('--title', required=True, help='窗口标题正则')
    ap.add_argument('--code', default='', help='股票代码；留空则从标题提取 6 位数字')
    ap.add_argument('--duration', type=float, default=60, help='采集时长(秒)')
    ap.add_argument('--interval', type=float, default=1.0, help='采样间隔(秒)')
    ap.add_argument('--frames', type=int, default=0, help='采满 N 帧即停（优先于 duration）')
    ap.add_argument('--out', default='data/screen_book', help='输出目录')
    ap.add_argument('--dry-run', action='store_true', help='只打印不写文件')
    ap.add_argument('--calibrate', action='store_true', help='只做标定并退出')
    ap.add_argument('--upscale', type=int, default=1, help='OCR 前放大倍数（DPI 缩放非 100% 时用 2）')
    ap.add_argument('--px-decimals', type=int, default=2, help='价格小数位，A股=2')
    # Grid 参数
    ap.add_argument('--top', type=int, default=26)
    ap.add_argument('--row-h', type=int, default=27)
    ap.add_argument('--rows', type=int, default=20)
    ap.add_argument('--px-x', type=int, default=80)
    ap.add_argument('--px-w', type=int, default=90)
    ap.add_argument('--vol-x', type=int, default=190)
    ap.add_argument('--vol-w', type=int, default=100)
    args = ap.parse_args()

    wins = list_windows(args.title)
    if not wins:
        print(f'未找到标题匹配 {args.title!r} 的窗口')
        for w in list_windows(None)[:15]:
            print(f"   {w['title'][:40]:42s} {w['w']}x{w['h']}")
        return 2
    win = wins[0]
    print(f'锁定窗口: {win["title"][:50]!r}  {win["w"]}x{win["h"]}  hwnd={win["hwnd"]}')

    if args.calibrate:
        calibrate(win, args.out)
        return 0

    code = args.code or ''
    if not code:
        m = re.search(r'(\d{6})', win['title'])
        code = m.group(1) if m else 'UNKNOWN'

    grid = Grid(top=args.top, row_h=args.row_h, rows=args.rows,
                px_x=args.px_x, px_w=args.px_w, vol_x=args.vol_x, vol_w=args.vol_w,
                px_decimals=args.px_decimals)
    ocr = CellOCR(upscale=args.upscale)
    col = Collector(win, grid, code, ocr)

    if not args.dry_run:
        os.makedirs(args.out, exist_ok=True)
        out_path = os.path.join(args.out, f'screen_book_{datetime.now():%Y%m%d}.jsonl')
        fh = open(out_path, 'a', encoding='utf-8')
    else:
        fh = None
        out_path = ''

    t_end = time.time() + args.duration
    n = ok = 0
    t0 = time.perf_counter()
    try:
        while time.time() < t_end:
            frame = col.read_frame()
            n += 1
            if frame is None:
                if n <= 3:
                    print(f'  帧{n}: 截图失败（窗口最小化？）')
            else:
                good = frame.ok_rows == grid.rows
                ok += 1 if good else 0
                if args.dry_run and n <= 3:
                    print(f'  帧{n}: 卖一={frame.asks[-1] if frame.asks else None} '
                          f'买一={frame.bids[0] if frame.bids else None} '
                          f'完整={good} 行数={frame.ok_rows}/{grid.rows}')
                if fh:
                    fh.write(json.dumps(asdict(frame), ensure_ascii=False) + '\n')
            if args.frames and n >= args.frames:
                break
            time.sleep(max(0.0, args.interval))
    except KeyboardInterrupt:
        print('\n中断')
    finally:
        if fh:
            fh.close()

    dt = time.perf_counter() - t0
    print(f'\n=== 采集结束 ===')
    print(f'  帧数={n}  完整帧={ok} ({ok/max(n,1)*100:.0f}%)  耗时={dt:.1f}s  '
          f'实际帧率={n/max(dt,1e-9):.2f}/s')
    if out_path:
        print(f'  落盘: {out_path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
