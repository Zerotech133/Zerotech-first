# -*- coding: utf-8 -*-
"""开盘啦题材成分快照采集 —— 板块口径的第二条腿（2026-11-08）。

## 为什么要这个脚本

jev_graph 一直用 `stock_basic.industry` 划板块。11-06 那次核查发现行业名
「新型电力」（32 只）被当成市场说的「新型电力板块」在用，实际上是两回事：

    行业「新型电力」 = tushare 的「新能源发电运营商」桶
                       川能动力/三峡能源/浙江新能/中国核电… 还混进珠海港(港口)、
                       蓝丰生化(农药) 两只与电力无关的票
    概念「电力」     = 219 只，长江电力/华能国际/国电电力/国电南瑞/中国电建
                       这几家电力绝对主力**一只都不在行业里**

两者只重合 26 只：行业 ∩ 概念 = 26，概念独有 193 只。也就是说拿行业当板块，
等于只看了电力概念的 12%，且恰好漏掉全部龙头。

**名称不可直接映射**：行业名 ≠ 题材名，同名不同物、异物不同名两种情况都有
（见 DEVELOPER_GUIDE 判例 #28）。所以需要把概念成分单独采下来，双轨并存。

## 三张表的现状（2026-11-08 实测）

    ts_kpl_concept        题材统计(z_t_num/up_num)   **停在 2024-11-17**，5000 行，废
    ts_kpl_concept_cons   题材成分                   有 20261106，13711 行/日，可用
    ts_kpl_list           个股当日题材标签+涨停时刻   只到 20261105，滞后一日

所以题材「强度」不能读 z_t_num，得自己按成分 + 当日行情算。

## 纪律

- **只写快照、无状态**：输入 day，输出 `_jev_concept/<day>.json`，不改库、不改
  datamart、不碰 stock_basic。渲染层（portal/jev_*.py）只读这个文件，不联网。
- **日期纯净性校验**：这个服务的 start_date/end_date 在部分表上静默失效（坑 2），
  所以落盘前必须断言「返回的 trade_date 全部等于请求日」，否则拒写。
  不校验的话，会把别的日子的成分盖在今天的文件名上——判例 #27 同一类错误。
- **total 不可信**（坑 3）：翻页只认「本页返回的行数」，本页不满即止。
- **幂等**：文件已存在且日期/D日成员与本次一致则跳过；`--force` 才覆盖。
- **原子写**：tmp + os.replace。

用法：
    python tools/jev_concept_fetch.py --day 20261106
    python tools/jev_concept_fetch.py --day 20261106 --dry
    python tools/jev_concept_fetch.py --day 20261106 --list
    python tools/jev_concept_fetch.py --day 20261106 --force
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from xtcloud_client import XtCloud, XtCloudError, _d8  # noqa: E402

STATION = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAP_DIR = os.path.join(STATION, "quant_backtest", "data", "tushare",
                        "_jev_concept")
PAGE = 5000
MAX_PAGE = 20          # 13711 行/日 ≈ 3 页；20 页是防御性上限


def log(msg):
    print(msg, flush=True)


def snap_path(day):
    return os.path.join(SNAP_DIR, "%s.json" % day)


def load(day):
    """读快照 → {"concepts": {名: {...}}, ...}；不存在或坏文件返回 None。"""
    p = snap_path(day)
    try:
        if not os.path.exists(p):
            return None
        with open(p, encoding="utf-8") as f:
            obj = json.load(f)
        if str(obj.get("day")) != str(day):
            return None          # 文件名与内容不符，当没有
        return obj
    except Exception:            # noqa: BLE001
        return None


def fetch(day):
    """拉当日题材成分。返回 (concepts, n_cons, warn)。"""
    c = XtCloud()
    rows, off = [], 0
    for _ in range(MAX_PAGE):
        j = c.table("ts_kpl_concept_cons", start_date=day, end_date=day,
                    limit=PAGE, offset=off)
        cols = j.get("columns") or []
        batch = [dict(zip(cols, r)) for r in (j.get("rows") or [])]
        if not batch:
            break
        rows += batch
        off += PAGE
        if len(batch) < PAGE:     # 本页不满即止，不看 total
            break
    if not rows:
        raise XtCloudError("%s 题材成分为空（该日无数据？）" % day)

    # 日期纯净性：一条外来日期都不许进
    bad = sorted({str(r.get("trade_date")) for r in rows
                  if str(r.get("trade_date")) != day})
    if bad:
        raise XtCloudError("日期过滤失效：请求 %s 却返回 %s（共 %d 行），拒写"
                           % (day, bad[:5], len(rows)))

    concepts = {}
    for r in rows:
        cn = (r.get("name") or "").strip()
        cc = (r.get("ts_code") or "").strip()
        sc = (r.get("con_code") or "").strip()
        if not cn or not sc:
            continue
        d = concepts.setdefault(cn, {"code": cc, "members": {}})
        if not d["code"]:
            d["code"] = cc
        # 同一概念同一票可能多行（不同 desc），保留首个非空 desc 与最大热度
        old = d["members"].get(sc)
        hot = r.get("hot_num")
        hot = float(hot) if hot == hot and hot is not None else None
        if old is None:
            d["members"][sc] = {"name": (r.get("con_name") or "").strip(),
                                "desc": (r.get("desc") or "").strip(),
                                "hot": hot}
        else:
            if not old["desc"] and r.get("desc"):
                old["desc"] = str(r["desc"]).strip()
            if hot is not None and (old["hot"] is None or hot > old["hot"]):
                old["hot"] = hot
    warn = []
    if off >= PAGE * MAX_PAGE:
        warn.append("翻页触顶 %d 页，可能截断" % MAX_PAGE)
    return concepts, len(rows), warn


def write(day, concepts, n_cons):
    os.makedirs(SNAP_DIR, exist_ok=True)
    obj = {"day": day,
           "fetched_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "source": "xtcloud:ts_kpl_concept_cons",
           "n_concept": len(concepts),
           "n_cons": n_cons,
           "concepts": concepts}
    p = snap_path(day)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, p)
    return p


def main(argv=None):
    ap = argparse.ArgumentParser(description="开盘啦题材成分快照")
    ap.add_argument("--day", default="", help="YYYYMMDD，缺省取今天")
    ap.add_argument("--dry", action="store_true", help="只看行数，不落盘")
    ap.add_argument("--force", action="store_true", help="已有快照也重拉")
    ap.add_argument("--list", action="store_true", help="列出当日题材名")
    a = ap.parse_args(argv)

    # _d8 返回的是带横杠的 2026-11-06，而表里 trade_date 是 20261106，
    # 不剥横杠会被下面的日期纯净性校验当场拦下（它拦对了）。
    day = _d8(a.day).replace("-", "") if a.day else dt.date.today().strftime("%Y%m%d")

    if a.list:
        obj = load(day)
        if not obj:
            log("%s 无快照，先不带 --list 跑一次" % day)
            return 2
        for cn, d in sorted(obj["concepts"].items(),
                            key=lambda z: -len(z[1]["members"])):
            log("  %-14s %-12s %3d 只" % (cn, d["code"], len(d["members"])))
        return 0

    old = load(day)
    if old and not a.force:
        log("%s 已有快照：%d 个题材 / %d 行（--force 可覆盖）"
            % (day, old["n_concept"], old["n_cons"]))
        return 0

    try:
        concepts, n_cons, warn = fetch(day)
    except XtCloudError as exc:
        log("!! %s" % exc)
        return 1

    log("%s 拉取 %d 行 → %d 个题材，覆盖 %d 只股票"
        % (day, n_cons, len(concepts),
           len({s for d in concepts.values() for s in d["members"]})))
    for w in warn:
        log("!! %s" % w)
    size = sorted(((len(d["members"]), n) for n, d in concepts.items()),
                  reverse=True)[:12]
    log("  最大题材: " + "、".join("%s(%d)" % (n, k) for k, n in size))
    if a.dry:
        log("  --dry：未落盘")
        return 0
    p = write(day, concepts, n_cons)
    log("  落盘 %s（%.1f MB）" % (p, os.path.getsize(p) / 1048576.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
