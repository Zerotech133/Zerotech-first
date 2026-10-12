# -*- coding: utf-8 -*-
"""图谱 QA 自检（每日链第 9 步，G1-8）。

六项检查（全部轻量 Cypher count；不阻断链条，exit 0 恒，
但 ok=False 时直报飞书 alert_fail——OPS-0930c）：
  inverted   validStart > validEnd 倒挂边（时序铁律违例）
  orphan_evt MarketEvent 无任何入边（挂空事件）
  bad_date   MarketEvent.date 非法（非 8 位数字）
  constraint evt_id 唯一约束缺失
  drift      边型×端点组合 vs 上次快照 diff（新增组合=潜在越轨写）
  coverage   六域边最大日期（简报/红点复用）
  source_fresh 数据源断流（G7：catalog freshness 按语义档超期表）

落盘 data/graph_check/latest.json（snapshot 供次日 drift 对比）。

用法：
  python kg_check.py            # 跑检查并落盘
  python kg_check.py --json     # 仅打印 JSON
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from kg_client import KgClient  # noqa: E402

STATE_DIR = os.path.join(HERE, "data", "graph_check")
LATEST = os.path.join(STATE_DIR, "latest.json")

# 覆盖域：边型 -> 日期取值（边日期在 validStart，事件日期在 m.date）
COVERAGE_EDGES = [
    "HAS_VOL_EVENT", "HAS_STRUCTURE", "HAS_CANDLE",
    "DRIVE", "TRADED_ON", "INVOLVES", "SERVES_AS",
]

INTRADAY_NOTE = {
    "HAS_VOL_EVENT": "盘面量能", "HAS_STRUCTURE": "缠论结构",
    "HAS_CANDLE": "盘口事件", "DRIVE": "DRIVE 传导",
    "TRADED_ON": "龙虎榜席位", "INVOLVES": "公告事件",
    "SERVES_AS": "高管任职",
}


def _num8(v):
    return bool(v) and len(str(v)) == 8 and str(v).isdigit()


def run():
    cli = KgClient(retry=1)
    issues = {}
    with cli._session() as s:
        # ① 倒挂边：任何带双时戳的边
        r = s.run(
            "MATCH ()-[r]->() WHERE r.validStart IS NOT NULL "
            "AND r.validEnd IS NOT NULL AND r.validStart > r.validEnd "
            "AND r.validEnd <> '9999-12-31' "
            "RETURN type(r) AS t, count(*) AS n").data()
        bad = [x for x in r if x["n"]]
        issues["inverted"] = {
            "ok": not bad,
            "detail": ["%s: %d" % (x["t"], x["n"]) for x in bad]}

        # ② 挂空事件节点（指数信号豁免：MarketIndex 缠论信号无出边属
        #    架构已知残留，保留节点价值；evt_id 前缀=指数 ts_code）
        idx = set(x["c"] for x in s.run(
            "MATCH (i:MarketIndex) RETURN i.code AS c").data())
        orph = s.run(
            "MATCH (m:MarketEvent) WHERE NOT ( ()-->(m) ) "
            "RETURN m.evt_id AS eid").data()
        real = [o["eid"] for o in orph
                if (o["eid"] or "?").split(":")[0] not in idx]
        issues["orphan_evt"] = {
            "ok": not real,
            "detail": {"real": real,
                       "idx_exempt": len(orph) - len(real)}}

        # ③ 事件日期格式
        n = s.run(
            "MATCH (m:MarketEvent) WHERE m.date IS NOT NULL "
            "AND (size(toString(m.date)) <> 8 OR NOT toString(m.date) =~ "
            "'[0-9]{8}') RETURN count(*) AS n").data()[0]["n"]
        issues["bad_date"] = {"ok": n == 0, "detail": n}

        # ④ evt_id 唯一约束存在
        cons = s.run("SHOW CONSTRAINTS YIELD name RETURN name").data()
        issues["constraint"] = {
            "ok": any("evt_id" in (c.get("name") or "") for c in cons),
            "detail": [c.get("name") for c in cons]}

        # ⑤ 边型×端点组合快照 + drift
        snap = {}
        for x in s.run(
                "MATCH (a)-[r]->(b) "
                "RETURN type(r) AS rt, labels(a)[0] AS la, "
                "labels(b)[0] AS lb, count(*) AS n").data():
            snap["%s|%s>%s" % (x["rt"], x["la"], x["lb"])] = x["n"]
        prev = {}
        if os.path.isfile(LATEST):
            try:
                with open(LATEST, encoding="utf-8-sig") as f:
                    prev = json.load(f).get("snapshot") or {}
            except Exception:
                prev = {}
        new_keys = sorted(set(snap) - set(prev))
        issues["drift"] = {
            "ok": not new_keys or not prev,  # 首跑基线不算
            "detail": new_keys}

        # ⑥ 覆盖域最大日期
        cov = {}
        for t in COVERAGE_EDGES:
            d = s.run("MATCH ()-[r:%s]->() RETURN max(r.validStart) AS d"
                      % t).data()[0]["d"]
            cov[t] = {"max": d, "cn": INTRADAY_NOTE.get(t, t)}

        # ⑥b 滞后告警（OPS-0930b：HAS_STRUCTURE 断 13 天而检查器
        #    始终 ok=True 的教训——coverage 日期必须升级为失败条件）。
        #    分档：每日链高频边 >4 交易日即断流；HAS_STRUCTURE 信号
        #    确认滞后设计上限 9 交易日，放宽到 14；SERVES_AS
        #    为任职区间语义（max 可为未来日期），不适用。
        #    K268 补遗（2026-10-08）：lag 按交易日度量（trade_cal 口径，
        #    国庆 8 自然日仅涨 1-2 交易日不再误报；日历不可用退自然日，
        #    budget 数字不变，方向仅放宽）。
        LAG_BUDGET = {"HAS_STRUCTURE": 14}
        lag_bad = []
        try:
            from kg_source_rating import trade_days_lag as _tdlag
        except Exception:
            _tdlag = None
        for t, c in cov.items():
            if t == "SERVES_AS" or not c["max"]:
                continue
            try:
                nat = (datetime.now()
                       - datetime.strptime(c["max"], "%Y%m%d")).days
            except ValueError:
                continue          # 非常规日期格式（如 ISO）跳过
            lag = _tdlag(c["max"]) if _tdlag else None
            basis = "tradeday" if lag is not None else "natural"
            if lag is None:
                lag = nat
            if lag > LAG_BUDGET.get(t, 4):
                lag_bad.append("%s %s 距今 %dd(%s)"
                               % (c["cn"], c["max"], lag, basis))
        issues["coverage_lag"] = {"ok": not lag_bad, "detail": lag_bad}
    cli.close()

    # ⑦ 数据源断流（G7 Admiralty 动态面：catalog freshness 超语义档 +
    #    catalog 自身过期元检查；纯本地计算，顺带刷新 rating JSON）
    from kg_source_rating import build as rating_build, save as rating_save
    rt = rating_build()
    rating_save(rt)
    sf_detail = ["%s: lag=%sd budget=%sd (%s)" % (
        r["table"], r["lag_days"], r["budget"], r["source"])
        for r in rt["staleness"]["stale_tables"]]
    if rt["staleness"]["catalog_stale"]:
        sf_detail.insert(0, "catalog 自身过期 %sd（须跑 build_catalog.py，"
                            "否则 freshness 全表失真）"
                         % rt["staleness"]["catalog_age_days"])
    issues["source_fresh"] = {"ok": rt["ok"], "detail": sf_detail}

    out = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "ok": all(v["ok"] for k, v in issues.items() if k != "drift")
              and issues["drift"]["ok"],
        "issues": issues,
        "coverage": cov,
        "snapshot": snap,
    }
    return out


def save(out):
    os.makedirs(STATE_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=STATE_DIR, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        # snapshot 可重生成，latest 只留必要字段供对比与引用
        slim = dict(out)
        slim["snapshot"] = out["snapshot"]
        json.dump(slim, f, ensure_ascii=False, indent=1)
    os.replace(tmp, LATEST)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="print JSON only")
    a = ap.parse_args()
    out = run()
    if a.json:
        slim = {k: v for k, v in out.items() if k != "snapshot"}
        print(json.dumps(slim, ensure_ascii=False, indent=1))
    else:
        print("graph_check ok=%s ts=%s" % (out["ok"], out["ts"]))
        for k, v in out["issues"].items():
            print("  %-11s ok=%-5s %s" % (k, v["ok"], v["detail"]))
        for t, c in out["coverage"].items():
            print("  coverage %-14s max=%s (%s)" % (t, c["max"], c["cn"]))
    save(out)
    # OPS-0930c：warn-only 指对链条不阻断（kg_brief 日报照发，恒 return 0），
    # 但检出问题必须升级到飞书——HAS_STRUCTURE 断 13 天而无人知晓的教训：
    # 检查器"有判定、无告警"等于采集不判定。
    if not out["ok"]:
        try:
            bad = ["%s: %s" % (k, v["detail"]) for k, v
                   in out["issues"].items() if not v["ok"]]
            fd, tp = tempfile.mkstemp(suffix=".txt")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write("\n".join(bad)[:500])
            wrapper = os.path.abspath(os.path.join(
                HERE, "..", "news_collector", "alert_fail_wrap.py"))
            subprocess.run([sys.executable, wrapper, "kg_check", "1", tp],
                           timeout=120, check=False)
            os.remove(tp)
        except Exception:
            pass
    return 0  # 告警不阻断（日报仍发），升级走上面的飞书直报


if __name__ == "__main__":
    sys.exit(main())
