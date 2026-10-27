# -*- coding: utf-8 -*-
"""数据日报公网快照构建器（K-ORG-186 M2，2026-10-18）。

链路：每晚 daily_report.py 推送前调用本脚本 →
  ① 拉 127.0.0.1:8091/api/daily/data 聚合 JSON（本机活服务，fail 快退出 1）
  ② 读 portal/data_daily.html 源（唯一 UI 来源，禁复制双维护）
  ③ 公网快照变换：剥 nav.js / shortscreen.js 引用（公网静态站无对应文件、
     无 API 顶栏；tokens.css 已在白名单发布）；注入 window.__EMBED__
     （页面 embed 分支直接渲染，不发 fetch）
  ④ 原子写 portal/picks_public/data_daily.html（mkstemp→os.replace，
     网关不读半文件）；deploy（CloudStudio Gateway 整目录重发布，agent 侧
     动作）后公网生效。

敏感面自查（沿 K-STOCK-411 白名单精神，ledger 停发判例）：快照内容为聚合
统计 + 分布 + 漂移告警标题，无持仓/止损位/口令/逐条新闻明细。

纪律：锚点断言 count==1 先拆后装（红线：替换补丁断言）；退出码 0 成功 /
1 失败，调用方（daily_report）降级不阻断日报主链。
"""

import json
import os
import sys
import tempfile
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "data_daily.html")
DST = os.path.join(HERE, "picks_public", "data_daily.html")
API = "http://127.0.0.1:8091/api/daily/data"

NAV_TAG = '<script src="/static/nav.js" defer></script>'
SS_TAG = '<script src="/static/shortscreen.js" defer></script>'
EMBED_ANCHOR = "<script>\nfunction el(id)"


def main():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    raw = opener.open(
        urllib.request.Request(
            API, headers={"User-Agent": "build-daily-data-public/1.0"}
        ),
        timeout=20,
    ).read()
    data = json.loads(raw.decode("utf-8"))
    with open(SRC, encoding="utf-8") as f:
        html = f.read()
    for tag in (NAV_TAG, SS_TAG):
        if html.count(tag) != 1:
            print("BUILD_FAIL anchor count!=1: %s" % tag[:48])
            return 1
        name = tag.split('src="')[1].split('"')[0]
        html = html.replace(
            tag, "<!-- %s 公网快照剥除（build_daily_data_public） -->" % name
        )
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    if html.count(EMBED_ANCHOR) != 1:
        print("BUILD_FAIL embed anchor count!=1")
        return 1
    html = html.replace(
        EMBED_ANCHOR, "<script>window.__EMBED__=%s</script>\n" % payload + EMBED_ANCHOR
    )
    if "window.__EMBED__" not in html:
        print("BUILD_FAIL post-assert embed missing")
        return 1
    os.makedirs(os.path.dirname(DST), exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=os.path.dirname(DST), prefix="data_daily.", suffix=".tmp"
    )
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(html)
    os.replace(tmp, DST)
    print(
        "BUILD_OK -> %s (%d bytes, api generated_at=%s)"
        % (DST, len(html), data.get("generated_at"))
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
