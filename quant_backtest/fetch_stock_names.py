# -*- coding: utf-8 -*-
"""生成全市场股票名称映射 data/stock_names.json（供 agent_tools 名称反查）。

背景：飞书机器人「名称→代码」靠 8b 模型记忆不可靠（纳思达=002180 现名奔图
科技，模型曾断言 002183=怡亚通）。根治：工具层全市场映射，彻底脱离模型记忆。

口径：
· 现用名：datamart stock_basic（本地 tushare 同步仓，~5900 行含退市，
  随 tushare_sync 每日刷新，不调 API）；
· 曾用名：tushare namechange 分页全量（慢变，手动重跑即可；
  裸调 HTTP 复用 tushare_sync.ts_call 模式——tushare 包 client 报
  「token不对」是包侧坑，裸调同 token 实测通）；
· 冲突处理：现用名先占坑；曾用名若与任何现用名撞车则跳过（防误导）；
· 落地结构：
    {"generated_at", "n_current", "n_alias", "n_conflict",
     "code2name": {code: 现用名},
     "name2code": {名称: code（现用名+不撞车的曾用名）},
     "alias_of":  {曾用名: 现用名}}

用法：python fetch_stock_names.py [--no-alias]（--no-alias 跳过 API 只刷现用名）
"""
import json
import os
import sys
import tempfile
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

OUT = os.path.join(HERE, "data", "stock_names.json")


def _current_names():
    """datamart stock_basic → {ts_code: name}（全市场现用名，本地）。"""
    import datamart as dm
    df = dm.read("stock_basic")
    if "ts_code" not in df.columns or "name" not in df.columns:
        raise RuntimeError("stock_basic 缺 ts_code/name 列: %s" % list(df.columns))
    out = {}
    for c, n in zip(df["ts_code"].astype(str).str.upper(),
                    df["name"].astype(str).str.strip()):
        if c and n and n.lower() != "nan":
            out[c] = n
    return out


def _alias_names():
    """tushare namechange 分页全量 → {ts_code: [曾用名...]}。

    裸调 HTTP（tushare 包 pro_api 同 token 报「token不对」，包侧坑，
    tushare_sync.ts_call 同款模式实测通）。顶层 fields 控制列序。
    """
    import time
    import requests
    TOKEN = ""
    for ln in open(os.path.join(HERE, ".env.local"), encoding="utf-8"):
        if ln.startswith("XT_DATA_KEY="):
            TOKEN = ln.split("=", 1)[1].strip()
    if not TOKEN:
        raise RuntimeError("缺 token（.env.local XT_DATA_KEY）")
    fields = "ts_code,name,start_date,end_date"
    frames, offset = [], 0
    while True:
        r = requests.post("https://api.tushare.pro", json={
            "api_name": "namechange", "token": TOKEN,
            "params": {"offset": offset}, "fields": fields}, timeout=60)
        d = r.json()
        if d.get("code") != 0:
            raise RuntimeError("namechange %s: %s" % (
                d.get("code"), str(d.get("msg"))[:100]))
        data = d.get("data") or {}
        cols, items = data.get("fields") or fields.split(","), \
            data.get("items") or []
        if not items:
            break
        import pandas as pd
        frames.append(pd.DataFrame(items, columns=cols))
        if len(items) < 10000:
            break
        offset += len(items)
        time.sleep(0.4)
    import pandas as pd
    df = pd.concat(frames, ignore_index=True)
    df["ts_code"] = df["ts_code"].astype(str).str.upper()
    hist = {}
    for tc, g in df.groupby("ts_code"):
        names = sorted({str(n).strip() for n in g["name"]})
        hist[tc] = names
    return hist, len(df)


def run(with_alias=True):
    code2name = _current_names()
    print("现用名 %d（datamart stock_basic）" % len(code2name))
    name2code = {}
    for c, n in code2name.items():
        name2code[n] = c          # 现用名先占坑
    alias_of, n_conflict = {}, 0
    if with_alias:
        hist, rows = _alias_names()
        print("namechange %d 行 / %d 标的有历史名" % (rows, len(hist)))
        for tc, names in hist.items():
            cur = code2name.get(tc)
            for n in names:
                if not n or n == cur:
                    continue
                if n in name2code:            # 与现用名（或先到的曾用名）撞车
                    n_conflict += 1
                    continue
                name2code[n] = tc
                alias_of[n] = cur or tc       # 现名未知时回落自身代码
        print("曾用名并入 %d / 撞车跳过 %d" % (len(alias_of), n_conflict))
    res = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "n_current": len(code2name),
        "n_alias": len(alias_of),
        "n_conflict": n_conflict,
        "code2name": code2name,
        "name2code": name2code,
        "alias_of": alias_of,
    }
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(OUT), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False)
    os.replace(tmp, OUT)
    print("落盘: %s（name2code %d 条）" % (OUT, len(name2code)))
    # 抽查
    for probe in ("纳思达", "奔图科技", "ST浩丰", "浩物股份", "浦发银行"):
        print("  %s -> %s (现名 %s)" % (
            probe, name2code.get(probe),
            alias_of.get(probe) or probe))


def main():
    run(with_alias="--no-alias" not in sys.argv)


if __name__ == "__main__":
    main()
