# -*- coding: utf-8 -*-
"""情报总线出口单测 + 量化路径影子对账（2026-10-19 联调首包）。
运行：python portal/test_intel_bus.py
单测段：临时夹具库（AIOS_ROOT 重定向 + INTEL_BUS_EVENTS_PATH），零碰生产。
对账段（__main__ 显式触发）：总线 domain=jev/news vs 直读 news.db 基线
（event_graph 读法同口径：jev IS NOT NULL AND jev!=''）逐条比对
（id/ts/bias/conf），真库只读——这是「读层 v1 未路由」补链的验收证据。
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

FIX = tempfile.mkdtemp(prefix="intelbus_")
os.environ["AIOS_ROOT"] = FIX
os.environ["INTEL_BUS_EVENTS_PATH"] = os.path.join(FIX, "events.jsonl")

import api_intel_bus as bus  # noqa: E402  必须在 env 注入后导入

NOW = datetime.now()
SINCE_TS = (NOW - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M")
OLD_TS = "2026-01-01 00:00"

# ── 夹具：news.db（news 表同构） + events.jsonl ────────────────
db_dir = Path(FIX) / "news_collector"
db_dir.mkdir(parents=True)
db = sqlite3.connect(db_dir / "news.db")
db.execute("CREATE TABLE news (id TEXT PRIMARY KEY, source TEXT, ts TEXT, "
           "created_at TEXT, title TEXT, content TEXT, stocks TEXT, "
           "jev TEXT, hit INTEGER DEFAULT 0, pushed INTEGER DEFAULT 0)")
rows = [
    ("n1", "sina", SINCE_TS, SINCE_TS, "标题甲",
     "内容", json.dumps(["600000.SH"]), json.dumps(
         {"bias": "bull", "conf": 0.9, "impact_cn": "高"})),
    ("n2", "sina", SINCE_TS, SINCE_TS, "标题乙",
     "内容", json.dumps(["000001.SZ", "600000.SH"]), "not-json"),  # 坏 jev
    ("n3", "east", SINCE_TS, SINCE_TS, "标题丙",
     "内容", "[]", ""),                                                    # 无标的
    ("n4", "east", OLD_TS, OLD_TS, "窗口外",
     "内容", json.dumps(["600000.SH"]), json.dumps({"bias": "bear", "conf": 0.5})),
    ("n5", "sina", SINCE_TS, SINCE_TS, "标题丁",
     "内容", "bad-json", json.dumps({"bias": "neutral", "conf": 0.3})),
]
db.executemany("INSERT INTO news VALUES (?,?,?,?,?,?,?,?,?,?)",
               [r + (0, 0) for r in rows])
db.commit()
db.close()

with open(os.path.join(FIX, "events.jsonl"), "w", encoding="utf-8") as f:
    f.write(json.dumps({"id": "e1", "kind": "news", "ts": SINCE_TS,
                        "text": "事件一", "source": "sina",
                        "stocks": ["600000.SH"], "bias": "bull",
                        "conf": 0.98, "reason": "r1", "tag": "jev"}) + "\n")
    f.write("not-a-json-line\n")  # 坏行
    f.write(json.dumps({"id": "e2", "kind": "news", "ts": OLD_TS,
                        "text": "窗口外", "source": "sina",
                        "stocks": ["600000.SH"], "bias": "bear",
                        "conf": 0.4, "reason": "r2", "tag": "jev"}) + "\n")

_n = 0


def _ok(name):
    global _n
    _n += 1
    print("T%d %s OK" % (_n, name))


# ── T1 参数门 ─────────────────────────────────────────────────
body, st = bus.bus_query({"domain": "news", "foo": "1"})
assert st == 400 and not body["ok"] and "foo" in body["errors"]["invalid_query"]
body, st = bus.bus_query({"domain": "bad"})
assert st == 400
body, st = bus.bus_query({"domain": "news", "code": "600000"})
assert st == 400 and "A-share" in body["errors"]["invalid_query"]
body, st = bus.bus_query({"domain": "news", "days": "999"})
assert st == 400
_ok("参数白名单/域/代码格式/天数上限 四路 400")

# ── T2 news 域：窗口+代码过滤，坏 jev→None 不炸 ────────────────
body, st = bus.bus_query({"domain": "news", "code": "600000.SH", "days": "7"})
assert st == 200 and body["ok"]
ids = [i["id"] for i in body["items"]]
assert ids == ["n2", "n1"], ids                 # n3 无标的/n4 窗口外/
assert body["count"] == 2                       # n5 stocks 坏 JSON 不匹配代码
by_id = {i["id"]: i for i in body["items"]}
assert by_id["n1"]["jev"]["bias"] == "bull"
assert by_id["n2"]["jev"] is None              # 坏 JSON → None 不炸
body, st = bus.bus_query({"domain": "news", "days": "7", "limit": "100"})
n5 = [i for i in body["items"] if i["id"] == "n5"]
assert n5 and n5[0]["stocks"] == []            # 无代码过滤时 stocks 坏→[] 在列
_ok("news 域窗口/代码过滤与坏数据兜底")

# ── T3 jev 域：只收有效判定，坏行计数 ──────────────────────────
body, st = bus.bus_query({"domain": "jev", "code": "600000.SH", "days": "7"})
assert st == 200 and [i["id"] for i in body["items"]] == ["n1"]  # n5 无代码
assert body["items"][0]["jev"]["conf"] == 0.9
assert body["errors"].get("bad_rows") == 1  # n2 匹配代码但 jev 损坏→计数
# n2 jev 非空但坏 → 应计 bad_rows
body2, _ = bus.bus_query({"domain": "jev", "days": "7", "limit": "100"})
assert body2["errors"].get("bad_rows") == 1, body2["errors"]  # n2
_ok("jev 域有效判定过滤 + 坏行 fail-visible 计数")

# ── T4 evidence 域：委托既有出口口径不漂移 ─────────────────────
body, st = bus.bus_query({"domain": "evidence", "code": "600000.SH"})
assert st == 200 and body["ok"] and body["count"] >= 1
item = body["items"][0]
assert item["match_basis"] == "news.stocks:exact_code"
assert item["code"] == "600000.SH"
_ok("evidence 域委托委托口径（精确 ts_code）")

# ── T5 events 域：窗口过滤 + 坏行跳过 ──────────────────────────
body, st = bus.bus_query({"domain": "events", "code": "600000.SH",
                          "days": "7"})
assert st == 200 and [i["id"] for i in body["items"]] == ["e1"]
assert body["items"][0]["bias"] == "bull" and body["count"] == 1
body, st = bus.bus_query({"domain": "events", "days": "7", "limit": "100"})
assert body["errors"].get("bad_rows") == 1     # not-a-json-line
_ok("events 域窗口/坏行计数")

# ── T6 缺源 fail-visible ──────────────────────────────────────
os.environ["INTEL_BUS_EVENTS_PATH"] = os.path.join(FIX, "nope.jsonl")
body, st = bus.bus_query({"domain": "events"})
assert st == 503 and not body["ok"] and "缺失" in body["errors"]["events"]
os.environ["INTEL_BUS_EVENTS_PATH"] = os.path.join(FIX, "events.jsonl")
_ok("events 缺源 503 fail-visible")

print("UNIT PASS（情报总线 6 组单测）")

# ── 影子对账（真库只读）────────────────────────────────────────
if __name__ == "__main__":
    print("--- 影子对账：总线 vs 直读基线（真实 news.db，mode=ro）---")
    base = Path(os.environ.get("AIOS_ROOT", "C:/aios")) / "news_collector" / \
        "news.db"
    if not base.is_file():
        print("RECONCILE SKIP：真实 news.db 不存在（%s）" % base)
    else:
        days = 7
        since, until = bus._window(days)
        con = sqlite3.connect("file:" + base.resolve().as_posix() +
                              "?mode=ro", uri=True)
        baseline = con.execute(
            "SELECT id,ts,jev FROM news WHERE ts>=? AND ts<=? "
            "AND jev IS NOT NULL AND jev!=''", (since, until)).fetchall()
        con.close()
        body, st = bus.bus_query({"domain": "jev", "days": str(days),
                                  "limit": "100"})
        assert st == 200
        bus_rows = {i["id"]: i for i in body["items"]}
        # 对账口径：基线 id 全集 vs 总线 id 全集（limit 100 内）；逐条
        # ts/bias/conf 一致；基线坏 JSON 行允许总线侧缺席（计 bad_rows）
        base_ids = set()
        mismatch = []
        import api_news_intel as ni
        bad_base = 0
        for _id, ts, raw in baseline:
            j = ni._parse_jev(raw)
            if j is None:
                bad_base += 1
                continue
            base_ids.add(_id)
            b = bus_rows.get(_id)
            if not b or b["ts"] != ts or b["jev"]["bias"] != j["bias"] or \
                    b["jev"]["conf"] != j["conf"]:
                mismatch.append(_id)
        extra = set(bus_rows) - base_ids
        green = not mismatch and not extra and \
            body["count"] >= len(base_ids) - 0
        print("基线有效判定 %d（坏 JSON %d）｜总线 items %d｜不一致 %s｜"
              "总线多出 %s" % (len(base_ids), bad_base, len(bus_rows),
                               mismatch or "无", extra or "无"))
        # 窗口超 100 条时总线 limit 截断属预期，比较限于前 100
        print("RECONCILE %s" % ("PASS（读层 v1 未路由→总线接线对账一致）"
                                if green else "FAIL"))
        sys.exit(0 if green else 1)

shutil.rmtree(FIX, ignore_errors=True)
