# -*- coding: utf-8 -*-
"""卡片素材：门户二维码生成 + 飞书 image_key 上传（带缓存复用）。
任何一步失败都返回 None，卡片自动降级为无二维码（不影响推送）。"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ASSET_DIR = os.path.join(HERE, "assets")
CACHE = os.path.join(ASSET_DIR, "qr_cache.json")
LARK_ROOT = r"C:\Users\Administrator\.trae-cn\plugins\trae-remote-official\lark"
PYLIBS = os.path.abspath(os.path.join(HERE, "..", "pylibs"))


def _cfg():
    try:
        with open(os.path.join(HERE, "config.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _find_cli():
    if not os.path.isdir(LARK_ROOT):
        return None
    for d in sorted(os.listdir(LARK_ROOT), reverse=True):
        p = os.path.join(LARK_ROOT, d, "bin", "lark-cli.exe")
        if os.path.isfile(p):
            return p
    return None


def ensure_qr_image_key(url=None):
    """生成二维码并上传飞书，返回 image_key（同 URL 走缓存）。失败返回 None。"""
    url = url or _cfg().get("report", {}).get("portal_url")
    if not url:
        return None
    try:
        with open(CACHE, encoding="utf-8") as f:
            cache = json.load(f)
        if cache.get("url") == url and cache.get("image_key"):
            return cache["image_key"]
    except Exception:
        pass
    os.makedirs(ASSET_DIR, exist_ok=True)
    png = os.path.join(ASSET_DIR, "portal_qr.png")
    try:
        if PYLIBS not in sys.path:
            sys.path.insert(0, PYLIBS)
        import qrcode
        qrcode.make(url, box_size=10, border=2).save(png)
    except Exception:
        return None
    exe = _find_cli()
    if not exe:
        return None
    try:
        r = subprocess.run(
            [exe, "im", "images", "create", "--as", "bot",
             "--data", json.dumps({"image_type": "message"}), "--file", png],
            capture_output=True, text=True, encoding="utf-8", timeout=60)
        data = json.loads(r.stdout or "{}")
        key = (data.get("data") or {}).get("image_key") or data.get("image_key")
        if not key:
            return None
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump({"url": url, "image_key": key}, f, ensure_ascii=False)
        return key
    except Exception:
        return None


if __name__ == "__main__":
    print("QR_KEY=" + str(ensure_qr_image_key()))
