# -*- coding: utf-8 -*-
"""盘中池推送：一次扫描 → 判据分层 → 刷新 H5 → 飞书卡片。

给计划任务用（`PoolJudgeIntraday`，每个交易日 09:30/13:00 起每 15 分钟）。

为什么单独一个入口，而不是在 pool_state.py 里加 `--push`：
  · pool_state.py 是结构引擎，只回答「结构是什么」，不该知道飞书长什么样；
  · 推送要管「该不该推」（时段/交易日/去重/心跳），那是调度层的事；
  · 计划任务只认一条命令，出问题只看一个日志，入口越窄越好定位。

门控（三重，任一不过即静默退出，不写日志噪声）：
  1. 工作日（周一~周五）
  2. 交易时段 09:30~11:30 / 13:00~15:00（午休不扫，扫了也是同一批价）
  3. 行情时点必须是今天——非交易日新浪返回的是上一交易日的 ts，
     用它推「实时」就是撒谎。这一条同时兜住了节假日（没有日历也能判）。

推送节流（`--always` 关掉）：
  · A/B/E 名单或各档计数变了 → 推
  · 距上次推送 ≥60 分钟 → 推（心跳，防止「一上午没动静」被当成挂了）
  · 否则只落盘、刷新 H5，不发消息

用法:
  python pool_intraday_push.py                 # 正常盘中一次
  python pool_intraday_push.py --dry           # 只打印卡片文本，不发送
  python pool_intraday_push.py --always        # 忽略节流，每次都推
  python pool_intraday_push.py --force         # 跳过时段/交易日门控（调试）
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
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "news_collector")))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "portal")))

REPORTS = os.path.join(HERE, "reports")
STATE_PATH = os.path.join(REPORTS, "pool_push_state.json")
LOG_PATH = os.path.join(REPORTS, "intraday_push.log")
H5_PATH = os.path.join(REPORTS, "pool_report_h5.html")
SEND_PY = os.path.join(HERE, "..", "news_collector", "send_feishu.py")

HEARTBEAT_MIN = 60          # 无变化时的最长静默（分钟）
LAYERS = ("A", "B", "C", "D", "E")


def log(msg):
    line = "%s %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    try:
        os.makedirs(REPORTS, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:                             # noqa: BLE001
        pass


def in_session(now):
    """交易时段（本地时间）。午休返回 False——11:30~13:00 价格不动。"""
    if now.weekday() > 4:
        return False
    t = now.hour * 60 + now.minute
    return (9 * 60 + 30 <= t <= 11 * 60 + 30) or (13 * 60 <= t <= 15 * 60)


def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:                             # noqa: BLE001
        return {}


def save_state(st):
    try:
        os.makedirs(REPORTS, exist_ok=True)
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=1)
        os.replace(tmp, STATE_PATH)
    except Exception as e:                        # noqa: BLE001
        log("[warn] state 写入失败: %s" % e)


def _names(items, n=5):
    if not items:
        return "—"
    s = "、".join("%s(%s)" % (it.get("name", ""), it["code"][:6]) for it in items[:n])
    return s + ("　等 %d 只" % len(items) if len(items) > n else "")


def _one_lines(items, n=3, guard=False):
    out = []
    for it in items[:n]:
        base = it.get("guard") if guard else it.get("zg")
        d = it["lpx"] / base - 1 if base else 0
        out.append("%s %s %.2f (%+.2f%%) 距%s %+.1f%% 量比 %s" % (
            it["code"], it.get("name", ""), it["lpx"], it.get("lpct") or 0,
            "守位" if guard else "ZG", d * 100,
            ("%.2f" % it["volr"]) if it.get("volr") is not None else "-"))
    return out


def build_sections(j):
    """judge JSON → 卡片分节文本（首节是结论，被 from_sections 抽成 summary）。"""
    c = j["counts"]
    up, dn, lim = j.get("up", 0), j.get("down", 0), j.get("limit_up", 0)
    a, b, e = j.get("A", []), j.get("B", []), j.get("E", [])

    if b:
        concl = "可介入 %d 只（B 三买守位在手边）" % len(b)
        level = "ok"
    elif a or e:
        concl = "无三买；贴沿/突破 %d 只，等回踩" % (len(a) + len(e))
        level = "warn"
    else:
        concl = "五层全空，今天没有可操作位置"
        level = "info"

    secs = ["%s · 上涨 %d / 下跌 %d / 涨停 %d" % (concl, up, dn, lim)]

    secs.append("**A 今日突破**（等回踩，不追）%d 只\n%s"
                % (len(a), "\n".join(_one_lines(a)) or "—"))
    secs.append("**B 可介入**（三买守位 ≤8%%）%d 只\n%s"
                % (len(b), "\n".join(_one_lines(b, guard=True)) or "—"))
    secs.append("**E 贴沿未过**（临近爆发位，埋伏不是买）%d 只\n%s"
                % (len(e), "\n".join(_one_lines(e)) or "—"))
    secs.append("**C 持有跟踪** %d 只 · **D 衰竭预警** %d 只 · 被否 %d 只"
                % (c.get("C", 0), c.get("D", 0), c.get("drop", 0)))
    return secs, level


def push_card(card):
    """走 send_feishu.py CLI（计划任务无 keychain，OpenAPI 通道才是能用的那条）。"""
    fd, tmp = tempfile.mkstemp(suffix=".json", prefix="poolpush_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(card, f, ensure_ascii=False)
        r = subprocess.run(
            [sys.executable, SEND_PY, tmp, "interactive"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=90,
        )
        out = (r.stdout or "").strip()
        if out.startswith("SEND_OK"):
            return True, ""
        return False, (out or (r.stderr or "").strip() or "send_feishu 无输出")[:200]
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def newest(pattern):
    import glob
    fs = sorted(glob.glob(os.path.join(REPORTS, pattern)))
    return fs[-1] if fs else None


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:                             # noqa: BLE001
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只打印，不发送")
    ap.add_argument("--always", action="store_true", help="忽略节流，每次都推")
    ap.add_argument("--force", action="store_true", help="跳过时段/交易日门控")
    args = ap.parse_args()

    now = datetime.now()
    if not args.force and not in_session(now):
        return 0                                   # 静默：非交易时段

    import pool_state as P
    rc = P.judge(0.5, 30, save=True)
    if rc != 0:
        log("[fail] judge rc=%d（取价失败或无可判标的），本轮不推送" % rc)
        return 3

    fp = newest("pool_judge_*.json")
    if not fp:
        log("[fail] 找不到 pool_judge_*.json")
        return 4
    with open(fp, encoding="utf-8") as f:
        j = json.load(f)

    # 门控 3：行情必须是今天的。非交易日新浪返回上一交易日 ts，
    # 拿它推「实时 HH:MM」就是撒谎。
    today = now.strftime("%Y-%m-%d")
    if not args.force and (j.get("live_at") or "")[:10] != today:
        log("[skip] 行情时点 %s 不是今天，判为非交易日/未开盘"
            % (j.get("live_at") or "?"))
        return 0

    # 顺手刷新 H5（页面读 pool_state + 最新 pool_live，刚才 judge 已落盘）
    try:
        r = subprocess.run(
            [sys.executable, os.path.join(HERE, "pool_report_h5.py"),
             "--out", H5_PATH],
            cwd=HERE, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=300,
        )
        if r.returncode != 0:
            log("[warn] H5 刷新失败 rc=%d %s"
                % (r.returncode, (r.stderr or "")[-160:]))
    except Exception as e:                        # noqa: BLE001
        log("[warn] H5 刷新异常: %s" % e)

    secs, level = build_sections(j)
    sig = {
        "A": sorted(it["code"] for it in j.get("A", [])),
        "B": sorted(it["code"] for it in j.get("B", [])),
        "E": sorted(it["code"] for it in j.get("E", [])),
        "counts": j["counts"],
    }
    st = load_state()
    changed = (json.dumps(sig, sort_keys=True, ensure_ascii=False)
               != json.dumps(st.get("sig") or {}, sort_keys=True, ensure_ascii=False))
    last_ts = st.get("pushed_at") or ""
    try:
        idle_min = (now - datetime.strptime(last_ts, "%Y-%m-%d %H:%M:%S")).total_seconds() / 60
    except Exception:                             # noqa: BLE001
        idle_min = 1e9

    from feishu_push import from_sections
    card = from_sections(
        "pool_intraday", "chan",
        "盘中池 %s" % now.strftime("%H:%M"), secs,
        ts=j.get("live_at") or now.strftime("%Y-%m-%d %H:%M"),
        source="缠论结构池 · 日线结构 %s" % j.get("struct_asof", ""),
        footer="结构基准 %s，盘中价实时。位置清单，不是买入信号。"
               % j.get("struct_asof", ""),
        level=level,
    )

    if args.dry:
        print("\n--- 卡片文本 ---")
        for s in secs:
            print(s)
        print("--- level=%s changed=%s idle=%.0fmin ---" % (level, changed, idle_min))
        return 0

    if not args.always and not changed and idle_min < HEARTBEAT_MIN:
        log("[skip] 无变化（距上次推送 %.0f 分钟），落盘已更新，不推送" % idle_min)
        save_state({"sig": sig, "pushed_at": last_ts,
                    "checked_at": now.strftime("%Y-%m-%d %H:%M:%S")})
        return 0

    ok, why = push_card(card)
    if ok:
        log("[push] OK A%d/B%d/E%d C%d D%d up%d dn%d lim%d"
            % (j["counts"]["A"], j["counts"]["B"], j["counts"]["E"],
               j["counts"]["C"], j["counts"]["D"],
               j.get("up", 0), j.get("down", 0), j.get("limit_up", 0)))
        save_state({"sig": sig, "pushed_at": now.strftime("%Y-%m-%d %H:%M:%S"),
                    "checked_at": now.strftime("%Y-%m-%d %H:%M:%S")})
        return 0
    log("[push] FAIL %s" % why)
    return 5


if __name__ == "__main__":
    sys.exit(main() or 0)
