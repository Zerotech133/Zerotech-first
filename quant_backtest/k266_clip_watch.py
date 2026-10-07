# -*- coding: utf-8 -*-
"""K266 M2 剪贴板监听：轮询剪贴板位图 → 落 inbox/pending（去重靠 ingest 的 sha256）。

用法：python k266_clip_watch.py [间隔秒，默认3]
退出：Ctrl+C。落在 e:\老田的AI助手\inbox\docs\pending\clip_YYYYmmdd_HHMMSS.png
"""
import io
import time

from PIL import ImageGrab

INBOX = r"e:\老田的AI助手\inbox\docs\pending"
_last = None


def main():
    import os
    os.makedirs(INBOX, exist_ok=True)
    ivl = 3
    import sys
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        ivl = int(sys.argv[1])
    global _last
    print(f"[clip] watching clipboard every {ivl}s -> {INBOX}", flush=True)
    while True:
        try:
            img = ImageGrab.grabclipboard()
            if img is not None:
                buf = io.BytesIO()
                img.convert("RGB").save(buf, format="PNG")
                data = buf.getvalue()
                if data != _last and len(data) > 2048:  # 忽略微小图/重复
                    _last = data
                    fn = time.strftime("clip_%Y%m%d_%H%M%S.png")
                    path = os.path.join(INBOX, fn)
                    with open(path, "wb") as f:
                        f.write(data)
                    print(f"[clip] saved {fn} {len(data)//1024}KB", flush=True)
        except Exception as e:
            # 剪贴板被占用/非位图内容：静默跳过
            if "clipboard" not in str(e).lower() and "OpenClipboard" not in str(e):
                print(f"[clip] {type(e).__name__}: {e}", flush=True)
        time.sleep(ivl)


if __name__ == "__main__":
    main()
