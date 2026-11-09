#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""缠论池 · 收盘复盘（一条命令：重算 → 溯源 → 推飞书）
============================================================

盘中有 15 分钟一次的卡片，但 15:00 之后什么都没有：A/B/E 定格在 14:45，
一天下来「说了什么、后来怎么样」没有人替你说完。这个脚本补的就是那一环。

做三件事：
  1. 用收盘价重跑一次 judge（结构还是昨收的，价是今天的收盘价）
  2. 用 pool_trace 把一天十几份快照串起来，算各档从首现到收盘的表现
  3. 组一张卡推到飞书——名单是证据，表现是后果，两个一起给

口径纪律（与 pool_trace 同源，不另算一套）：
  · 末刻才首现的样本剔除（没有可观测区间，ret 恒 0）
  · 各档语义不同，分列不合计
  · 没有 market 基准时超额列留空，不拿涨跌家数比冒充收益

用法：
  python pool_close_review.py --dry          # 只打印，不推送
  python pool_close_review.py               # 15:00 后跑，重算并推送
  python pool_close_review.py --no-judge    # 沿用已有快照（调试用）
  python pool_close_review.py --force       # 跳过时段门控
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "news_collector")))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "portal")))

import pool_trace as T                                       # noqa: E402

REPORTS = T.REPORTS
LOG_PATH = os.path.join(REPORTS, "close_review.log")
H5_PATH = os.path.join(REPORTS, "pool_report_h5.html")
SEND_PY = os.path.join(HERE, "..", "news_collector", "send_feishu.py")


def log(msg):
    line = "%s %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    try:
        os.makedirs(REPORTS, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:                                       # noqa: BLE001
        pass


def stats(snaps):
    """各档「首现 → 收盘」的配对收益。返回 layer -> {rows, st, ex, stall}。"""
    first = T.collect(snaps)
    lastq = T.last_quotes(snaps)
    tail_t = (snaps[-1].get("live_at") or "")[11:16]
    out = {}
    for L in T.LAYERS:
        rows, stall = [], 0
        for c, rec in first[L].items():
            if rec.get("first_t") == tail_t:
                stall += 1
                continue
            q = lastq.get(c)
            if not q or not rec.get("first_px") or not q.get("lpx"):
                continue
            rows.append((q["lpx"] / rec["first_px"] - 1, c, rec, q,
                         T.mkt_ret(snaps, rec.get("_i", 0), len(snaps) - 1)))
        ex = [r[0] - r[4] for r in rows if r[4] is not None]
        out[L] = {"rows": rows, "stall": stall,
                  "st": T._stat(rows, reverse=(L in T.REVERSE)),
                  "ex": (sum(ex) / len(ex)) if ex else None}
    return out


def _pct(x, nd=2):
    return "—" if x is None else ("%+.*f%%" % (nd, x * 100))


def build_card(j, snaps, st, date):
    """judge JSON + 快照 → 卡片分节。首节即结论，会被抽成 summary。"""
    c = j.get("counts") or {}
    a, b, e = j.get("A") or [], j.get("B") or [], j.get("E") or []
    up, dn, lim = j.get("up") or 0, j.get("down") or 0, j.get("limit_up") or 0

    # ── 结论：只用已经算出来的数说话，不写形容不出来的话 ──
    # 措辞要点：样本是「当天在档内出现过的」，不是「收盘还在档内的」。
    # 这两者不是一回事——E 档收盘常常清零，说「E 档 +1.89%」会让读者以为
    # 现在还挂着 4 只 E。
    hits = [(L, st[L]["st"]) for L in ("E", "B", "A")
            if st[L]["st"] and st[L]["st"]["n"] >= 2]
    bg = [(s.get("up"), s.get("down")) for s in snaps
          if s.get("up") and s.get("down")]
    mkt = ""
    if len(bg) >= 2:
        (u0, d0), (u1, d1) = bg[0], bg[-1]
        w0 = (u0 - d0) / (u0 + d0) * 100
        w1 = (u1 - d1) / (u1 + d1) * 100
        mkt = "；市场净宽 %+.0f%%→%+.0f%%" % (w0, w1)
    if hits:
        L, s = max(hits, key=lambda kv: kv[1]["mean"])
        head = "%s 当日在档 %d 只，平均 %s（%d 只收红）%s" % (
            T.LNAME[L], s["n"], _pct(s["mean"]), s["up"], mkt)
        level = "ok" if s["mean"] > 0 else "warn"
    else:
        head = "今日可介入档样本过少，不做当日结论%s" % mkt
        level = "info"
    secs = ["收盘 · " + head]

    # ── 名单：证据 ──
    qt = (j.get("live_at") or "")[11:16]        # 价格时点：收盘后跑就是 15:00
    def _fmt(items, guard=False, n=4):
        out = []
        for it in items[:n]:
            g = it.get("guard") or it.get("sig_zg") or it.get("zg") or 0
            d = (it["lpx"] / g - 1) if g else 0
            out.append("%s %s %.2f（%s，距守位 %+.1f%%）"
                       % (it["code"], it.get("name", ""), it["lpx"],
                          _pct((it.get("lpct") or 0) / 100.0), d * 100))
        if len(items) > n:
            out.append("…另有 %d 只" % (len(items) - n))
        return "\n".join(out) or "—"

    secs.append("**B 可介入（收盘仍在）** %d 只 · %s 价\n%s"
                % (len(b), qt, _fmt(b, True)))
    secs.append("**A 突破 / E 贴沿** %d + %d 只 · %s 价\n%s"
                % (len(a), len(e), qt, _fmt((a + e)[:4]) if (a or e) else "—"))

    # ── 表现：后果 ──
    lines = []
    for L in T.LAYERS:
        s = st[L]["st"]
        if not s:
            extra = "（仅末刻 %d 只）" % st[L]["stall"] if st[L]["stall"] else ""
            lines.append("%s　—%s" % (T.LNAME[L], extra))
            continue
        lines.append("%s　%s　%d涨/%d平/%d跌　胜率 %.0f%%%s%s"
                     % (T.LNAME[L], _pct(s["mean"]), s["up"], s["flat"], s["dn"],
                        s["win"] * 100,
                        "　超额 %s" % _pct(st[L]["ex"]) if st[L]["ex"] is not None else "",
                        "　←反向" if L in T.REVERSE else ""))
    _tail = (snaps[-1].get("live_at") or "")[11:16]
    _to = "收盘" if _tail >= "14:55" else "%s（未收盘）" % _tail
    secs.append("**各档当日表现**（首现价 → %s，未计滑点）\n%s"
                % (_to, "\n".join(lines)))

    # ── 市场背景 ──
    if len(bg) >= 2:
        (u0, d0), (u1, d1) = bg[0], bg[-1]
        w0 = (u0 - d0) / (u0 + d0) * 100
        w1 = (u1 - d1) / (u1 + d1) * 100
        secs.append("**市场背景**　开盘后净宽 %+.0f%% → 收盘 %+.0f%%（%+.0f 个百分点）"
                    "　收盘 %d 涨 / %d 跌 / %d 停"
                    % (w0, w1, w1 - w0, up, dn, lim))

    # 超额算不出来时必须点明，否则「-1%」会被读成判据失效
    no_ex = all(st[L]["ex"] is None for L in T.LAYERS)
    secs.append("**C 持有 %d · D 衰竭 %d · 被否 %d**\n"
                "结构基准 %s（今日日线未同步，价格已收盘，结构仍是上一交易日）。%s"
                "位置清单，不是买入建议。"
                % (c.get("C", 0), c.get("D", 0), c.get("drop", 0),
                   j.get("struct_asof", ""),
                   "快照未存同期基准，超额无法计算——各档涨跌里混着大盘，"
                   "别直接当判据成绩。" if no_ex else ""))
    return secs, level


def _deep(layer, h5):
    return (h5 + ("&" if "?" in h5 else "?") + "layer=" + layer) if h5 else None


def push_card(card):
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix=".json", prefix="poolclose_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(card, f, ensure_ascii=False)
        r = subprocess.run([sys.executable, SEND_PY, tmp, "interactive"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=90)
        out = (r.stdout or "").strip()
        if out.startswith("SEND_OK"):
            return True, ""
        return False, (out or (r.stderr or "").strip() or "send_feishu 无输出")[:200]
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:                                       # noqa: BLE001
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只打印，不发送")
    ap.add_argument("--no-judge", action="store_true", help="沿用已有快照，不重跑")
    ap.add_argument("--force", action="store_true", help="跳过时段门控")
    a = ap.parse_args()

    now = datetime.now()
    today = now.strftime("%Y%m%d")
    t = now.hour * 60 + now.minute
    if not a.force:
        if now.weekday() > 4:
            log("[skip] 周末，不复盘")
            return 0
        if not (15 * 60 <= t <= 17 * 60):
            log("[skip] 当前 %s 不在收盘窗口 15:00~17:00（--force 可越过）"
                % now.strftime("%H:%M"))
            return 0

    if not a.no_judge:
        import pool_state as P
        rc = P.judge(0.5, 30, save=True)
        if rc != 0:
            log("[fail] judge rc=%d，无法复盘" % rc)
            return 3

    snaps, raw = T.load_snaps(today)
    if not snaps:
        log("[fail] %s 无快照" % today)
        return 4
    last = snaps[-1]
    if not a.force and (last.get("live_at") or "")[:10] != now.strftime("%Y-%m-%d"):
        log("[skip] 行情时点 %s 不是今天，判为非交易日" % (last.get("live_at") or "?"))
        return 0
    if not a.force and (last.get("live_at") or "")[11:16] < "14:55":
        log("[skip] 最后快照 %s 还没到收盘，复盘会少算尾盘"
            % (last.get("live_at") or "")[11:16])
        return 0

    st = stats(snaps)
    card_secs, level = build_card(last, snaps, st, today)

    # 顺手刷新 H5（页面读 pool_state + 最新 pool_live）
    h5 = os.environ.get("POOL_H5_URL", "").strip()
    if not a.dry:
        try:
            r = subprocess.run([sys.executable, os.path.join(HERE, "pool_report_h5.py"),
                                "--out", H5_PATH], cwd=HERE,
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=300)
            if r.returncode != 0:
                log("[warn] H5 刷新失败 rc=%d" % r.returncode)
        except Exception as e:                              # noqa: BLE001
            log("[warn] H5 刷新异常: %s" % e)

    if a.dry:
        print("\n--- 卡片 ---")
        for s in card_secs:
            print(s)
        print("--- level=%s ---" % level)
        return 0

    from feishu_push import from_sections
    btns = []
    for L, lab in (("B", "B 可介入"), ("E", "E 贴沿")):
        n = len(last.get(L) or [])
        if n and h5:
            btns.append(("%s %d 只" % (lab, n), _deep(L, h5), "primary"))
    if h5:
        btns.append(("看完整证据", h5, "default"))

    card = from_sections(
        "pool_close_review", "chan",
        "收盘复盘 %s" % now.strftime("%m-%d"), card_secs,
        ts=last.get("live_at") or now.strftime("%Y-%m-%d %H:%M"),
        source="缠论结构池 · 日线结构 %s" % last.get("struct_asof", ""),
        footer="各档语义不同不可加总；胜率按方向算，平盘不算输。",
        level=level, buttons=btns or None,
    )
    ok, why = push_card(card)
    if ok:
        log("[push] OK 收盘复盘 %s A%d/B%d/E%d C%d D%d"
            % (today, len(last.get("A") or []), len(last.get("B") or []),
               len(last.get("E") or []),
               (last.get("counts") or {}).get("C", 0),
               (last.get("counts") or {}).get("D", 0)))
        return 0
    log("[push] FAIL %s" % why)
    return 5


if __name__ == "__main__":
    sys.exit(main() or 0)
