# -*- coding: utf-8 -*-
"""JEV 知识图谱「只读快照」采集工位 —— `_jev_kg/`（2026-11-09，Step 5 / 计划 A）。

## 职责边界

JEV 是数据的**使用方**，采集是下属工位的事（DEVELOPER_GUIDE §9 判例 #26/#30）：

    tools/jev_kg_fetch.py   输入 day，输出快照文件，无状态、可整体替换
    portal/jev_kg.py        纯编译器，零网络 import，只读快照
    portal/api_jev_kg.py    只读接口，src=auto 复用 graph 缓存 / src=snapshot 读盘

本脚本落盘的是 **原始 `/api/jev/graph` 响应**（不是编译后的 KG）——
`jev_kg.read_graph_snapshot()` 读盘后再现编译，因此快照与线上 graph 同源、
可逐字段对齐，也能被采集频率/数据源整体替换而不动渲染层。

## 文件格式

    quant_backtest/data/tushare/_jev_kg/<day>_<board>_<kind>.json

`board=auto` 表示不带 board 参数的默认焦点板块。`_kg_meta` 只放来源信息，
**故意不叫 `day`**：`jev_kg.scan_foreign_days()` 会递归找所有 `day` 键，
多写一个会被当成跨日脏数据。编译器只读固定键（`focus/network/chain/social/...`），
额外键不参与关系生成，端到端以 `src=snapshot` 的 clean / n_rel 对齐为准。

## 纪律（都是踩过的坑）

- **日期纯净性用码点复核**：本机输出通道会把数字串串味（判例 2026-11-04），
  `d["day"]` 与请求日必须 `[ord(c)]` 逐位相等，不靠打印结果判断。
- **口径不许静默回落**：`board_kind` 与请求 kind 不符、或 `focus.name` 与请求
  board 不符，一律拒写（判例 #28：换了分析对象还不自知）。
- **拒绝 200 假成功**：响应带 `schema_version`（`/api/jev/` 的 jev.v2 兜底）
  或 `ok:false` 一律拒写（判例 #30）。
- **直连不走系统代理**：`ProxyHandler({})`，否则 7890 会把本机请求拖到 18s（判例 #26）。
- **幂等 + 原子写 + 写前备份**：内容未变则跳过；`tmp + os.replace`；
  覆盖前 `copy2` 到 `.bak`（只留一份，不删任何文件）。
  ⚠ **内容哈希幂等 ≠ 上游幂等**：graph 的分钟层是「本地优先→远端兜底→写回
  本地 parquet」，60s 缓存过期后重建会拿到更多分钟（`minute_read 59→60`、
  `focus.timeline` 整条换股）——真实数据变化必须如实覆盖。只有
  `minute_coverage`（本地 parquet 目录规模遥测 1026→1029，与当日结论无关）
  按 `VOLATILE_KEYS` 白名单摘出哈希；`minute_read` 等真实台账一律保留，
  绝不为了凑 SKIP 去冻结数据。收敛后连续重跑自然全 SKIP。
- 不写 datamart、不写 parquet、不碰 8091、不重启、不改任何渲染层文件。

## 用法

    python tools/jev_kg_fetch.py --day 20261106 --all-boards
    python tools/jev_kg_fetch.py --day 20261106 --kind industry --board 种植业
    python tools/jev_kg_fetch.py --day 20261106 --all-boards --dry
    python tools/jev_kg_fetch.py --day 20261106 --all-boards --force
    python tools/jev_kg_fetch.py --list
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import os
import re
import shutil
import sys
import urllib.parse
import urllib.request

STATION = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAP_DIR = os.path.join(STATION, "quant_backtest", "data", "tushare", "_jev_kg")
DEF_BASE = "http://127.0.0.1:8080"
TIMEOUT_S = 45
MIN_TOP, MAX_TOP, DEF_TOP = 3, 20, 12


def log(msg):
    print(msg, flush=True)


def _opener():
    """禁系统代理：7890 对本机/内网是纯拖慢（判例 #26）。"""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def get_json(url, timeout=TIMEOUT_S):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with _opener().open(req, timeout=timeout) as r:
        raw = r.read()
    if not raw:
        raise ValueError("空响应")
    return json.loads(raw.decode("utf-8"))


def ord_eq(a, b) -> bool:
    """码点逐位比对，绕开本机输出通道的数字串串味。"""
    sa, sb = str(a or ""), str(b or "")
    return len(sa) == len(sb) and [ord(c) for c in sa] == [ord(c) for c in sb]


def norm_day(x) -> str:
    s = str(x or "").strip()
    if not re.fullmatch(r"[0-9]{8}", s):
        raise ValueError("day 须为 8 位数字 YYYYMMDD，收到 %r" % (s,))
    return s


def graph_url(base, day, board, kind, top):
    q = {"day": day, "kind": kind, "top": str(top)}
    if board and board != "auto":
        q["board"] = board
    return base.rstrip("/") + "/api/jev/graph?" + urllib.parse.urlencode(q)


def kg_url(base, day, board, kind, top):
    q = {"day": day, "kind": kind, "top": str(top), "src": "snapshot"}
    if board and board != "auto":
        q["board"] = board
    return base.rstrip("/") + "/api/jev/kg?" + urllib.parse.urlencode(q)


def validate(d, day, board, kind):
    """落盘前的四道闸：日期 / 口径 / 焦点 / 非空。任何一条不过都拒写。"""
    if not isinstance(d, dict):
        raise ValueError("响应不是 JSON 对象")
    if d.get("ok") is False or d.get("error"):
        raise ValueError("接口报错: %s / %s" % (d.get("error"), d.get("message")))
    if d.get("schema_version"):
        raise ValueError("拿到 /api/jev/ 兜底（%s），不是 graph 响应"
                         % d.get("schema_version"))
    if not ord_eq(d.get("day"), day):
        raise ValueError("跨日: 响应 day=%r ≠ 请求 %r" % (d.get("day"), day))
    bk = str(d.get("board_kind") or "")
    if bk and bk != kind:
        raise ValueError("口径回落: 请求 %s，响应 %s" % (kind, bk))
    if not (d.get("network") or {}).get("nodes"):
        raise ValueError("network.nodes 为空")
    if not d.get("boards"):
        raise ValueError("板块榜为空")
    fn = str((d.get("focus") or {}).get("name") or "")
    if board != "auto" and not ord_eq(fn, board):
        raise ValueError("焦点板块不符: 请求 %r，响应 %r" % (board, fn))
    return d


def snap_path(day, board, kind) -> str:
    """与 jev_kg.snap_path() 完全同口径，避免采集/渲染读到两份文件。"""
    safe = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]", "_", str(board or "auto"))
    return os.path.join(SNAP_DIR, "%s_%s_%s.json" % (norm_day(day), safe, kind))


# 只摘两类「非结论」字段：采集时间、以及本地 parquet 目录规模遥测。
# minute_coverage = 本月本地分钟文件数（远端写回会从 1026 涨到 1029），
# 是全局运维状态，不是该板块当日数据；它每次重建都变，留着会让采集永远 WRITE。
# **minute_read 必须留在哈希里**：它记录本次实际读到多少只票的分钟，
# 会直接改变 focus.timeline 的首次异动条目，属于真实数据变化。
VOLATILE_KEYS = ("_kg_meta", "minute_coverage")


def payload_hash(obj) -> str:
    """只比结论数据，保证幂等；易变遥测按白名单摘除（不摘真实台账）。"""
    core = {k: v for k, v in obj.items() if k not in VOLATILE_KEYS}
    s = json.dumps(core, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def write_snapshot(obj, path, force=False):
    os.makedirs(SNAP_DIR, exist_ok=True)
    if os.path.exists(path):
        try:
            with io.open(path, encoding="utf-8") as f:
                old = json.load(f)
            if not force and payload_hash(old) == payload_hash(obj):
                return "SKIP", path
        except Exception:      # noqa: BLE001
            pass                # 旧文件坏了就正常覆盖
        shutil.copy2(path, path + ".bak")
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False))
    os.replace(tmp, path)
    return "WRITE", path


def fetch_one(base, day, board, kind, top, force=False, dry=False,
              verify=False):
    url = graph_url(base, day, board, kind, top)
    d = get_json(url)
    validate(d, day, board, kind)
    obj = dict(d)
    obj["_kg_meta"] = {
        "producer": "tools/jev_kg_fetch.py",
        "fetched_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "query": urllib.parse.urlparse(url).query,
    }
    p = snap_path(day, board, kind)
    if dry:
        return {"board": board, "kind": kind, "status": "DRY",
                "bytes": len(json.dumps(obj, ensure_ascii=False).encode("utf-8")),
                "path": p}
    st, path = write_snapshot(obj, p, force=force)
    rec = {"board": board, "kind": kind, "status": st,
           "path": path, "bytes": os.path.getsize(path),
           "n_nodes": len((d.get("network") or {}).get("nodes") or [])}
    if verify:
        v = get_json(kg_url(base, day, board, kind, top))
        rec["verify"] = {
            "source": v.get("source"), "ok": v.get("ok"),
            "n_rel": v.get("n_rel"), "clean": v.get("clean"),
            "miss": v.get("total_miss"),
        }
    return rec


def board_names(d):
    out = []
    for b in d.get("boards") or []:
        n = str(b.get("name") or "").strip()
        if n and n not in out:
            out.append(n)
    return out


def list_snapshots():
    if not os.path.isdir(SNAP_DIR):
        log("无快照目录 %s" % SNAP_DIR)
        return 0
    rows = []
    for fn in sorted(os.listdir(SNAP_DIR)):
        if not fn.endswith(".json"):
            continue
        p = os.path.join(SNAP_DIR, fn)
        rows.append((fn, os.path.getsize(p)))
    log("快照目录 %s：%d 份" % (SNAP_DIR, len(rows)))
    for fn, sz in rows:
        log("  %-52s %8.1f KB" % (fn, sz / 1024.0))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="JEV KG 只读快照采集工位")
    ap.add_argument("--day", default="", help="YYYYMMDD（必填，--list 除外）")
    ap.add_argument("--base", default=DEF_BASE, help="门户基址，默认 127.0.0.1:8080")
    ap.add_argument("--kind", default="both",
                    choices=("industry", "theme", "both"))
    ap.add_argument("--board", action="append", default=[],
                    help="指定板块，可重复；缺省只采 auto")
    ap.add_argument("--all-boards", action="store_true",
                    help="自动采 graph 板块榜里全部板块")
    ap.add_argument("--limit", type=int, default=0,
                    help="--all-boards 时最多采几个板块，0=不限")
    ap.add_argument("--top", type=int, default=DEF_TOP)
    ap.add_argument("--dry", action="store_true", help="只校验不落盘")
    ap.add_argument("--force", action="store_true", help="内容未变也覆盖（仍备份）")
    ap.add_argument("--verify", action="store_true",
                    help="写完后回调 /api/jev/kg?src=snapshot 验证")
    ap.add_argument("--list", action="store_true", help="列出已有快照")
    a = ap.parse_args(argv)

    if a.list:
        return list_snapshots()
    if not a.day:
        log("!! 必须给 --day YYYYMMDD")
        return 2
    day = norm_day(a.day)
    if not MIN_TOP <= a.top <= MAX_TOP:
        log("!! top 须在 %d..%d" % (MIN_TOP, MAX_TOP))
        return 2
    kinds = ("industry", "theme") if a.kind == "both" else (a.kind,)

    jobs, seen = [], set()
    for kind in kinds:
        plan = ["auto"]
        extra = list(a.board)
        if a.all_boards:
            try:
                auto = get_json(graph_url(a.base, day, "auto", kind, a.top))
                validate(auto, day, "auto", kind)
                extra += board_names(auto)
            except Exception as exc:      # noqa: BLE001
                log("!! 拉取 %s 板块榜失败: %s: %s"
                    % (kind, type(exc).__name__, exc))
        for b in extra:
            if a.limit and len(plan) - 1 >= a.limit:
                break
            if b not in plan:
                plan.append(b)
        for b in plan:
            k = (b, kind)
            if k not in seen:
                seen.add(k)
                jobs.append(k)

    log("%s day=%s kinds=%s jobs=%d（auto + %d 板块）"
        % (dt.datetime.now().strftime("%F %T"), day, "/".join(kinds),
           len(jobs), len(jobs) - len(kinds)))
    ok, bad, recs = 0, [], []
    for i, (board, kind) in enumerate(jobs, 1):
        try:
            r = fetch_one(a.base, day, board, kind, a.top,
                          force=a.force, dry=a.dry, verify=a.verify)
            recs.append(r)
            ok += 1
            v = r.get("verify") or {}
            tail = (" verify=source:%s clean:%s n_rel:%s"
                    % (v.get("source"), v.get("clean"), v.get("n_rel"))) if v else ""
            log("  [%2d/%2d] %-8s %-16s %-4s %6.1f KB%s"
                % (i, len(jobs), kind, board, r["status"],
                   r["bytes"] / 1024.0, tail))
            if v and (v.get("source") != "snapshot" or v.get("clean") is not True):
                bad.append((board, kind, "verify 不通过: %s" % v))
        except Exception as exc:          # noqa: BLE001
            bad.append((board, kind, "%s: %s" % (type(exc).__name__, exc)))
            log("  [%2d/%2d] %-8s %-16s FAIL %s: %s"
                % (i, len(jobs), kind, board, type(exc).__name__, exc))

    log("汇总: OK=%d FAIL=%d dry=%s" % (ok, len(bad), a.dry))
    for b, k, why in bad:
        log("  !! %s/%s %s" % (k, b, why))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
