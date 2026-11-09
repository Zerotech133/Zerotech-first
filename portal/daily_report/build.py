# -*- coding: utf-8 -*-
"""每日汇报 H5 构建器：数据 JSON + 模板 → 自包含单文件 index.html。

为什么要自包含：这份页面要发飞书/微信群，对方点开时可能没有本机 API、
没有登录态、甚至不在同一内网。单文件 = 一个链接打开即见，不依赖后端。

用法：
  python build.py                      # 用默认数据包构建
  python build.py --data X.json --out Y.html

设计约束（对本页成立，改版请保留）：
  - **不做长图**：四屏分开，每屏独立滚动，点条目开抽屉看证据；
  - 抽屉用 div 模拟，不用 <dialog>（iOS Safari 老版本不支持）；
  - 纯 ES5 + 字符串拼接，微信内置浏览器可直接跑；
  - 所有数字来自数据包，模板里不写死业务数值。
"""
from __future__ import annotations

import argparse
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
# 数据包按 asof（数据基准交易日）命名，默认取 20261106
DEFAULT_ASOF = "20261106"
DEFAULT_DATA = r"C:\项目\zerobound-kg\factor_proto\data\report_%s.json" % DEFAULT_ASOF
TEMPLATE = os.path.join(HERE, "template.html")
OUT = os.path.join(HERE, "index.html")

PLACEHOLDER = "/*__DATA__*/ null"


def _resolve_default():
    if os.path.exists(DEFAULT_DATA):
        return DEFAULT_DATA
    # 回退：取 data 目录里最新一份 report_*.json（避免硬编码日期过期即失效）
    import glob
    cands = sorted(glob.glob(
        r"C:\项目\zerobound-kg\factor_proto\data\report_*.json"))
    if cands:
        print("默认数据包缺失，回退最新: %s" % os.path.basename(cands[-1]))
        return cands[-1]
    raise SystemExit("找不到数据包，请先跑 factor_proto/_gen_daily_h5.py，"
                     "或用 --data 指定")


def build(data_path, tpl_path, out_path):
    with open(data_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    with open(tpl_path, "r", encoding="utf-8") as f:
        tpl = f.read()

    if PLACEHOLDER not in tpl:
        raise SystemExit("模板缺少数据占位符: %s" % PLACEHOLDER)

    # 数据包必填字段：模板里的首屏、数据屏全部靠这几个渲染，
    # 少一个页面就会抛错（宁可构建失败，也不要渲染出三天前的旧数字）。
    for k in ("counts", "data_health", "margin", "summary", "stocks"):
        if k not in data:
            raise SystemExit(
                "数据包缺少 %r，页面无法渲染。请用新版 _gen_daily_h5.py 重新生成。"
                % k)
    if "sets" not in (data.get("data_health") or {}):
        raise SystemExit("data_health.sets 缺失：数据屏无法逐类渲染")

    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    # 内联进 <script>：转义 </ 防提前闭合，转义 <!-- 防注释开标签
    payload = payload.replace("</", "<\\/").replace("<!--", "<\\!--")

    # 标题里带上当日核心数字，微信/飞书抓取摘要时直接可读。
    # 用正则替换而不是写死字符串：模板标题会随日期改，硬编码锚点迟早失配。
    c = data.get("counts") or {}
    title = "%s 每日汇报 · %s 只三条全中，%s 只只中一两条" % (
        data.get("report_date", ""), c.get("confluence", "?"),
        c.get("union", "?"))
    tpl, n_t = re.subn(r"<title>.*?</title>", "<title>%s</title>" % title,
                       tpl, count=1, flags=re.S)
    tpl, n_o = re.subn(r'(<meta property="og:title" content=")[^"]*(")',
                       r"\g<1>%s\g<2>" % title, tpl, count=1)
    assert n_t == 1, "未找到 <title> 标签"
    assert n_o == 1, "未找到 og:title"

    html = tpl.replace(PLACEHOLDER, payload)
    assert PLACEHOLDER not in html, "占位符未被替换"

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    # 自检：数据必须真的进了页面，且体积合理
    with open(out_path, "r", encoding="utf-8") as f:
        got = f.read()
    n_code = got.count('"code"')
    assert n_code >= len(data.get("stocks") or []), (
        "数据疑似未注入：页面内 code 字段 %d 个 < 股票 %d 只"
        % (n_code, len(data.get("stocks") or [])))
    return out_path, len(got), n_code


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=None)
    ap.add_argument("--tpl", default=TEMPLATE)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--no-houfeng", action="store_true",
                    help="跳过候风独立页重建（漂移守卫用临时输出时不需要它）")
    a = ap.parse_args()
    p, size, n = build(a.data or _resolve_default(), a.tpl, a.out)
    print("WROTE %s (%d bytes, %d stocks embedded)" % (p, size, n))
    # 候风独立页每次一起重建：两条定时复盘自动化都跑 build.py，
    # 候风页（读 cohorts/status_latest.json）借此保持同步，不需要单独指令。
    # 漂移守卫等用 --data/--out 写临时文件的场景用 --no-houfeng 跳过。
    if not a.no_houfeng:
        try:
            import build_houfeng
            build_houfeng.main()
        except SystemExit as e:
            print("houfeng 构建退出: %s" % e)
        except Exception as e:  # noqa: BLE001
            # 候风页失败不阻断日报主线，但必须出声，不许静默
            print("WARNING: 候风页构建失败（不影响 index.html）: %s" % e)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
