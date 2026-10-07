# -*- coding: utf-8 -*-
"""scan_notify: ChanlunScan 完成哨兵（K179 建，10-05 拍板改每日 15:41）。

触发: 计划任务 ChanlunScanNotify（每日 15:41）→ 本脚本轮询等待
15:35 主跑（--scan --plot，出图耗时不确定）收尾标志=
data/chanlun_pool_eval_cache.json 的 generated_at ≥ 当日 15:30
（scan_all 尾部 _pool_eval 重写缓存，06:30 补齐跑生成的时间戳更早，
不会误触发）。

完成后汇总当日入池信号（detect_date=今日）:
  · 总数 / 类型分布
  · pt 形态共振数（K175 首日积累）+ 明细（≤10 条，附形态名）
  · 1卖·趋✓ 强预警
经 news_collector/send_feishu.py 推送飞书（outbox 临时文件 + 子进程，
SEND_OK 口径，与 chanlun_scan._push_feishu_text 同通道）。

幂等: ①状态文件 data/scan_notify_state.json（当日已推送/超时即退）
②每日触发靠状态文件当日即退防重发 ③SEND_OK 才写状态。
超时（45 分钟未见收尾标志）也推送一条未完成告警（OPS-0930b 精神：
失败要可见），状态记 timeout 防重发。

退出码: 0=已推送或当日已推送；3=轮询超时（此时仍会推超时告警）。
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
DATA = os.path.join(HERE, "data")
POOL = os.path.join(DATA, "chanlun_pool.jsonl")
EVAL = os.path.join(DATA, "chanlun_pool_eval_cache.json")
STATE = os.path.join(DATA, "scan_notify_state.json")
NEWS_DIR = os.path.abspath(os.path.join(HERE, "..", "news_collector"))

POLL_SEC = 60          # 轮询间隔
POLL_MAX = 45          # 最多 45 分钟
CUTOFF = "15:30"       # 收尾标志时间下限（cache generated_at）

TYPES = ("1买", "2买", "3买", "1卖", "2卖", "3卖")


def _now():
    return datetime.now()


def _today_dashed():
    return _now().strftime("%Y-%m-%d")


def _write_json(path, obj):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


def _scan_done():
    """15:35 主跑收尾标志：eval 缓存 generated_at ≥ 今日 15:30。"""
    try:
        with open(EVAL, encoding="utf-8") as f:
            gen = (json.load(f).get("generated_at") or "")
        today = _today_dashed()
        return gen.startswith(today) and gen[11:16] >= CUTOFF, gen
    except Exception:
        return False, ""


def _today_rows():
    rows = []
    try:
        with open(POOL, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                dd = str(r.get("detect_date") or "")
                if dd.startswith(_today_dashed()) or \
                        dd.replace("-", "") == _today_dashed().replace("-", ""):
                    rows.append(r)
    except OSError:
        pass
    return rows


def _summary(rows):
    n = len(rows)
    if not n:
        return ("📊 缠论扫描哨兵 · %s\n15:35 主跑已收尾（eval 缓存 %s）。\n"
                "今日新入池 0 条（节假日数据未更新属正常，管线在位）。"
                % (_today_dashed(), _now().strftime("%H:%M")))
    dist = {}
    for r in rows:
        dist[r.get("type") or "?"] = dist.get(r.get("type") or "?", 0) + 1
    pt_rows = [r for r in rows if r.get("pt") is True]
    strong = [r for r in rows if r.get("type") == "1卖"
              and r.get("tbe") is True]
    lines = ["📊 缠论扫描哨兵 · %s（收尾 %s）" % (_today_dashed(),
                                                _now().strftime("%H:%M")),
             "今日新入池 %d 条：%s" % (
                 n, " / ".join("%s×%d" % (t, dist[t]) for t in TYPES
                               if t in dist) or "—")]
    if pt_rows:
        lines.append("形✓ 形态共振 %d 条（K175 首日积累）：" % len(pt_rows))
        for r in pt_rows[:10]:
            lines.append("  · %s %s %s〔%s〕" % (
                r.get("code"), (r.get("name") or "")[:8], r.get("type"),
                r.get("pt_name") or "?"))
        if len(pt_rows) > 10:
            lines.append("  · …等 %d 条" % len(pt_rows))
    else:
        lines.append("形✓ 形态共振 0 条")
    if strong:
        lines.append("🚨 1卖·趋✓ 强预警 %d 条：%s" % (
            len(strong), " · ".join(
                "%s %s" % (r.get("code"), (r.get("name") or "")[:8])
                for r in strong[:8])))
    lines.append("详情 → /bt-chanlun-pool")
    return "\n".join(lines)


def _push(text):
    """outbox 临时文件 → send_feishu.py（SEND_OK 口径）。"""
    os.makedirs(os.path.join(NEWS_DIR, "outbox"), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.join(NEWS_DIR, "outbox"),
                               suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    try:
        r = subprocess.run(
            [sys.executable, os.path.join(NEWS_DIR, "send_feishu.py"), tmp],
            capture_output=True, text=True, encoding="utf-8", timeout=60)
        ok = "SEND_OK" in (r.stdout or "")
        print((r.stdout or r.stderr or "").strip()[:100])
        return ok
    except Exception as e:
        print("notify push error: %s" % str(e)[:80])
        return False


def main():
    try:
        with open(STATE, encoding="utf-8") as f:
            st = json.load(f)
        if st.get("date") == _today_dashed():
            print("already handled today: %s" % st.get("status"))
            return
    except Exception:
        st = None

    done, gen = False, ""
    for _ in range(POLL_MAX):
        done, gen = _scan_done()
        if done:
            break
        time.sleep(POLL_SEC)

    if done:
        ok = _push(_summary(_today_rows()))
        status = "pushed" if ok else "push_failed"
    else:
        ok = _push("⚠️ 缠论扫描哨兵 · %s\n%d 分钟未见 15:35 主跑收尾标志"
                   "（eval 缓存未重生）——请查任务 ChanlunScan 状态与日志。"
                   % (_today_dashed(), POLL_MAX))
        status = "timeout"
        ok = True   # 超时告警已发即视为已处理（发失败下次重试）
    if ok:
        _write_json(STATE, {"date": _today_dashed(), "status": status,
                            "ts": _now().isoformat(timespec="seconds"),
                            "eval_generated_at": gen})
        print("state=%s" % status)
        sys.exit(0 if status != "timeout" else 3)
    sys.exit(4)


if __name__ == "__main__":
    main()
