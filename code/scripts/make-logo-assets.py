#!/usr/bin/env python3
"""从 WhoTalk logo 源图生成前端品牌资产。

输入：横版黑底 logo（左火焰图形 + 右 "WhoTalk / AI ACADEMY" 文字）
输出（frontend/public/）：
  - logo.png              宽 1200 横版，透明底（un-screen 抠黑）
  - logo-icon.png         512x512 火焰方形，透明底（un-screen）
  - apple-touch-icon.png  180x180 火焰方形，纯黑底
  - favicon-32.png        32x32 火焰方形，纯黑底

可重复执行：换新源图后重跑即可覆盖。依赖 Pillow + numpy（后端 venv 自带）。
用法：.venv/bin/python scripts/make-logo-assets.py [源图路径]
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

REPO = Path(__file__).resolve().parent.parent
SRC_DEFAULT = Path('/Users/join/Desktop/系统logo.jpg')
OUT_DIR = REPO / 'frontend' / 'public'

# max(R,G,B) 低于此值视为纯黑（压掉 JPEG 噪点）
BLACK_THRESHOLD = 24
# 火焰方形图的黑底 padding 比例
PAD_RATIO = 0.06
# 火焰/文字分界检测：最小全黑间隙列数 与 搜索右界（比例）
GAP_MIN_RUN = 8
GAP_SEARCH_RIGHT = 0.7


def unscreen(img: Image.Image) -> Image.Image:
    """把黑底图反解成透明底。

    黑底图等效于彩色内容以 screen 叠加在纯黑上：
    alpha = max(R,G,B)，RGB 按 alpha 反预乘还原。
    深色背景上的观感与 CSS mix-blend-mode: screen 一致，
    且不受层叠上下文隔离影响。
    """
    arr = np.asarray(img.convert('RGB')).astype(np.float32)
    alpha = arr.max(axis=2)
    alpha[alpha < BLACK_THRESHOLD] = 0
    safe = np.where(alpha > 0, alpha, 1.0)
    rgb = (arr / safe[..., None] * 255.0).clip(0, 255)
    rgb[alpha == 0] = 0
    out = np.dstack([rgb, alpha]).astype(np.uint8)
    return Image.fromarray(out, 'RGBA')


def flame_square(src: Image.Image) -> Image.Image:
    """检测火焰边界（火焰与文字间的全黑间隙为界），裁黑底正方形（含 6% padding）。

    源图火焰并不只占"左半区"：实测火焰右缘 x≈679、文字 "WhoTalk" 左缘
    x≈690，二者之间有一段全黑空隙；简单按 w//2 裁会把文字前几个字母也
    框进方形图标。因此从首个密集列起，找第一段 >=GAP_MIN_RUN 列的全黑
    间隙作为火焰/文字分界，再对间隙左侧内容取 bbox。

    限制：假设火焰横向连续、火焰与文字间的全黑间隙是沿途首段
    >=GAP_MIN_RUN 列的 run。
    """
    w, h = src.size
    arr = np.asarray(src.convert('RGB'))
    col = (arr.max(axis=2) > BLACK_THRESHOLD).sum(axis=0)
    dense = np.nonzero(col >= h * 0.1)[0]
    if len(dense) == 0:
        sys.exit(f'错误：整幅图未检测到内容（阈值 {BLACK_THRESHOLD}）')
    first = int(dense[0])
    # 间隙判定容差：单列最多 3 个噪点像素
    empty = col <= 3
    run, gap_x = 0, None
    for x in range(first, int(w * GAP_SEARCH_RIGHT)):
        if empty[x]:
            run += 1
            if run >= GAP_MIN_RUN:
                gap_x = x - run + 1
                break
        else:
            run = 0
    if gap_x is None:
        sys.exit(f'错误：火焰与文字之间未检测到全黑间隙（run>={GAP_MIN_RUN}）')
    left = src.crop((0, 0, gap_x, h)).convert('RGB')
    mask = np.asarray(left).max(axis=2) > BLACK_THRESHOLD
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        sys.exit(f'错误：间隙左侧未检测到火焰图形（阈值 {BLACK_THRESHOLD}）')
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())
    bw, bh = x1 - x0 + 1, y1 - y0 + 1
    side = int(max(bw, bh) * (1 + 2 * PAD_RATIO))
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    half = side // 2
    square = left.crop((cx - half, cy - half, cx + half, cy + half))
    # 裁剪越界部分 PIL 自动填黑；pad 再兜底一次尺寸
    return ImageOps.pad(square, (side, side), method=Image.LANCZOS, color=(0, 0, 0))


def main() -> None:
    src_path = Path(sys.argv[1]) if len(sys.argv) > 1 else SRC_DEFAULT
    src = Image.open(src_path)
    print(f'源图：{src_path} {src.size[0]}x{src.size[1]}')

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 1) 横版：透明底，压到宽 1200
    banner = unscreen(src)
    if banner.width > 1200:
        h = round(banner.height * 1200 / banner.width)
        banner = banner.resize((1200, h), Image.LANCZOS)
    banner.save(OUT_DIR / 'logo.png', optimize=True)

    # 2) 火焰方形：页面内用透明底，浏览器图标用黑底
    square = flame_square(src)
    unscreen(square).resize((512, 512), Image.LANCZOS).save(OUT_DIR / 'logo-icon.png', optimize=True)
    square.resize((180, 180), Image.LANCZOS).save(OUT_DIR / 'apple-touch-icon.png', optimize=True)
    square.resize((32, 32), Image.LANCZOS).save(OUT_DIR / 'favicon-32.png', optimize=True)

    for name in ('logo.png', 'logo-icon.png', 'apple-touch-icon.png', 'favicon-32.png'):
        p = OUT_DIR / name
        print(f'{name}: {p.stat().st_size // 1024} KB')


if __name__ == '__main__':
    main()
