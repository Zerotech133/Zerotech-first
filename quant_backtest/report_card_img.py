# -*- coding: utf-8 -*-
"""K249 · 飞书报告卡图片模块（P0：把缠论图嵌进飞书卡）。

职责单一：
  1. card_png(code, period) —— 生成/复用「飞书卡专用缩略缠论图」
     （复用 chanlun_plot.plot_level 出全图，再用 PIL 等比缩到卡宽，
      加白底/留白，落到 reports/card_png/cardview/ 子目录）；
  2. upload_image(png) —— 上传飞书拿 image_key（泛化 card_assets 逻辑，
     带缓存，失败返回 None——调用方降级为无图卡）。

设计原则：任何一步失败都返回 None，绝不让「图挂了」导致整卡推不出去。
"""
import hashlib
import json
import os
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
QBT = HERE
CARD_PNG = os.path.normpath(os.path.join(HERE, "..", "reports", "card_png"))
CARD_VIEW = os.path.join(CARD_PNG, "cardview")
ASSET_DIR = os.path.join(HERE, "assets")
CACHE = os.path.join(ASSET_DIR, "img_cache.json")
LARK_ROOT = (r"C:\Users\Administrator\.trae-cn\plugins"
             r"\trae-remote-official\lark")

# 飞书卡内图片：目标宽度（px）。飞书 interactive 卡在手机/桌面渲染时
# 约 600~700 css px 宽，2x 图 = 1200~1400 足够清晰且体积可控。
CARD_W = 1280

# K249 关键：门户是 ThreadingHTTPServer（多线程），而 matplotlib pyplot
# 的全局 figure 注册表**非线程安全**——两个 HTTP 工作线程同时调
# plt.figure()/plt.close() 会让进程**原生崩溃**（无 Python traceback、
# 退出码 -1、连接被强行关闭 10054）。实测「推送带图报告」把门户打崩
# 就是这个原因。用模块级锁把「出图」整段串行化，是代价最小、
# 不动出图代码的修法。
_PLOT_LOCK = threading.Lock()


def _find_cli():
    # K-STOCK-396 exe 解耦：仓库 bin/ 固定副本优先，插件目录扫描回退。
    _bin = os.path.join(os.path.dirname(HERE), "bin", "lark-cli.exe")
    if os.path.isfile(_bin):
        return _bin
    if not os.path.isdir(LARK_ROOT):
        return None
    for d in sorted(os.listdir(LARK_ROOT), reverse=True):
        p = os.path.join(LARK_ROOT, d, "bin", "lark-cli.exe")
        if os.path.isfile(p):
            return p
    return None


def _load_cache():
    try:
        with open(CACHE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(c):
    try:
        os.makedirs(ASSET_DIR, exist_ok=True)
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump(c, f, ensure_ascii=False)
    except Exception:
        pass


def _pivot_label(card_dict):
    """决定高亮中枢的标签：确有 3 买才叫「三买中枢」，否则「当前中枢」。

    判据（与 K247 中枢 state 门禁一致）：日线中枢 state 必须为「上方离开」，
    且近端存在 3 买信号——两者齐备才算「三买中枢」。
    这样「构建中」的中枢（如松发股份）不会被误标成买点中枢。
    """
    try:
        lv = (card_dict or {}).get("levels") or {}
        d1 = lv.get("1d") or {}
        pv = d1.get("pivot") or {}
        state = pv.get("state")
        # 近端 3 买：two_buy 只覆盖 2 买，这里扫 signal 概要
        has3 = False
        for p in ("1d", "30m", "5m"):
            v = lv.get(p) or {}
            for s in (v.get("signals_recent") or []):
                if "3买" in str(s.get("type") or ""):
                    has3 = True
                    break
            if has3:
                break
        if state == "上方离开" and has3:
            return "三买中枢"
    except Exception:
        pass
    return "当前中枢"


def card_png(code, period="1d", show_n=150):
    """生成「飞书卡专用」缩略缠论图；返回 PNG 路径或 None。

    流程：chanlun_plot.plot_level（全图）→ PIL 等比缩到 CARD_W 宽
    → 存 cardview/{code}_{period}.png。同 code+period 且源图未更新则复用。
    """
    os.makedirs(CARD_VIEW, exist_ok=True)
    out = os.path.join(CARD_VIEW, "%s_%s.png"
                       % (code.replace(".", "_"), period))
    src = None
    try:
        sys.path.insert(0, QBT)
        import chanlun_plot as CP
        import chanlun_card as CC
        cd = CC.card(code) or {}
        kp = cd.get("key_point")
        # K249：卡图只高亮最后（当前）中枢，早先中枢淡化，避免客户
        # 看图时混淆「哪个才是买点的中枢」。标签按实情给——
        # 中枢 state=上方离开且确有 3 买 → 「三买中枢」；
        # 否则（构建中/下方离开/无 3 买）→ 「当前中枢」，不虚构买点。
        lab = _pivot_label(cd)
        # 串行化出图（pyplot 全局注册表非线程安全）——RT-1 崩溃修复。
        with _PLOT_LOCK:
            src = CP.plot_level(code, period, show_n, kp,
                                pivot_mode="last", pivot_label=lab)
    except Exception:
        src = None
    if not src or not os.path.exists(src):
        return None
    # 源图更新则重做缩略图
    try:
        if (os.path.exists(out)
                and os.path.getmtime(out) >= os.path.getmtime(src)):
            return out
    except OSError:
        pass
    try:
        from PIL import Image
        im = Image.open(src).convert("RGB")
        w, h = im.size
        if w > CARD_W:
            nh = max(1, int(h * CARD_W / w))
            im = im.resize((CARD_W, nh), Image.LANCZOS)
        im.save(out, "PNG", optimize=True)
        return out
    except Exception:
        # PIL 不可用则退回原图（可能偏大，但胜过没图）
        return src


def upload_image(png, tag=None):
    """上传 PNG 到飞书，返回 image_key；失败返回 None（调用方降级）。

    通道：飞书 OpenAPI `im/v1/images`（multipart/form-data）直连——
    不走 lark-cli：K249 实测 lark-cli `im images create --file` 在本机
    任何路径都报 `cannot open file`（含纯 ASCII 与已验证过的二维码路径），
    该通道已不可用；OpenAPI 只需静态 app 凭证（同 send_feishu）。
    带缓存：以文件 sha256 前 16 位为键，同图不重复上传。
    """
    if not png or not os.path.exists(png):
        return None
    try:
        with open(png, "rb") as f:
            raw = f.read()
        digest = hashlib.sha256(raw).hexdigest()[:16]
    except Exception:
        return None
    key_cache = "img_%s" % digest
    cache = _load_cache()
    if cache.get(key_cache):
        return cache[key_cache]
    key = _upload_openapi(raw, os.path.basename(png))
    if not key:
        return None
    cache[key_cache] = key
    _save_cache(cache)
    return key


def _app_creds():
    """读 ~/.lark-cli/config.json 静态凭证（同 send_feishu 判例）。"""
    cfg = os.path.join(os.path.expanduser("~"), ".lark-cli", "config.json")
    try:
        with open(cfg, encoding="utf-8") as f:
            apps = json.load(f).get("apps") or []
        if apps:
            return apps[0].get("appId"), apps[0].get("appSecret")
    except Exception:
        pass
    return None, None


def _tenant_token():
    app_id, app_secret = _app_creds()
    if not app_id or not app_secret:
        return None
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, os.path.join(os.path.dirname(here), "news_collector"))
    from feishu_client import get_tat  # K-STOCK-228 出口收敛
    return get_tat(app_id, app_secret)


def _upload_openapi(raw, filename):
    """上传图片：K-STOCK-228 起委托 feishu_client（multipart 判例下沉底座）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, os.path.join(os.path.dirname(here), "news_collector"))
    from feishu_client import upload_image
    return upload_image(raw, filename or "img.png")


def card_image_key(code, period="1d", show_n=150):
    """一步到位：出缩略图 → 上传 → 返回 image_key（失败 None）。"""
    p = card_png(code, period, show_n)
    if not p:
        return None
    return upload_image(p)


if __name__ == "__main__":
    code = sys.argv[1] if len(sys.argv) > 1 else "000993.SZ"
    p = card_png(code)
    print("PNG:", p)
    if p:
        print("bytes:", os.path.getsize(p))
        k = upload_image(p)
        print("IMAGE_KEY:", k)
