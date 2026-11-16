# -*- coding: utf-8 -*-
"""每日情绪快照串联入口（计划任务 SentimentSnapshot）。

sentiment_card.py（六源情绪卡）→ market_sentiment.py（市场级指数），
顺序执行，日志落 logs/sentiment_snapshot.log（append）。
设计约束：pythonw 无控制台（print 落 None 安全 no-op），全程落文件。
"""
import json
import subprocess
import sys
import os
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_F = os.path.join(HERE, "logs", "sentiment_snapshot.log")
SCRIPTS = ("sentiment_card.py", "market_sentiment.py", "qianping_collector.py")

# 工程解释器锚定（pandas 在 Python311；SentimentBT 判例同款），
# 不信任调度环境 PATH——pythonw 下 sys.executable 也是 pythonw 自身。
_PY311 = r"C:\Program Files\Python311\python.exe"
PYTHON = _PY311 if os.path.exists(_PY311) else sys.executable


def _clean_env():
    """剔除代理环境变量（判例 #26 直连；T-187 2026-11-11）。

    qianping_collector 等子进程走 requests，挂代理的会话会让其被本地
    服务代理拦截。生产计划任务无代理变量时此清理幂等。
    """
    env = dict(os.environ)
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
              "all_proxy", "ALL_PROXY"):
        env.pop(k, None)
    env["NO_PROXY"] = "*"
    env["no_proxy"] = "*"
    return env


# ── 雪球当日覆盖完整性（CR-20261114-02 选项 C，老田 2026-11-16 10:22 配准）
# 背景：SentimentSnapshot 固定 08:00 读当日雪球落盘。正常档 37 分钟（07:37 完成）有余量；
# 降速档 89 分钟（08:29 完成）会跨过 08:00，快照读到半截数据而**无从察觉**。
# 本判据只做「显式标注」，不改任何调度时间、不改 sentiment_card 既有 schema——
# 完整性以独立文件承载，避免动到下游门户正在读的结构。
XQ_MIN_RATIO = 0.95   # DEFAULT 经验值，非裁定；改阈值归上级
COMPLETE_F = os.path.join(HERE, "data", "sentiment_completeness.json")


def xq_completeness(now=None):
    """读「雪球全队列」与「尝试账本」，判断当日 08:00 时是否已跑完。

    返回 xq_complete：True 跑完 / False 未跑完 / None 无法判定（队列或账本不可读）。
    None 不等于 False——「不知道」不能冒充「不完整」，否则天天报假警。
    """
    now = now or datetime.now()
    info = {"asof": now.strftime("%Y-%m-%d %H:%M:%S"),
            "xq_queue": None, "xq_attempted_today": 0, "xq_coverage": None,
            "threshold": XQ_MIN_RATIO, "xq_complete": None, "reason": ""}
    try:
        if HERE not in sys.path:
            sys.path.insert(0, HERE)
        import xq_stock_timeline as X   # 必须复用采集器口径，避免两套分母
        queue = X.load_queue("all")
    except Exception as exc:  # noqa: BLE001
        info["reason"] = "队列读取失败 %s: %s" % (type(exc).__name__, exc)
        return info
    n = len(queue)
    info["xq_queue"] = n
    if not n:
        info["reason"] = "队列为空"
        return info

    ledger = os.path.join(HERE, "data", "xq_timeline", "_attempt.json")
    if not os.path.exists(ledger):
        info["reason"] = "尝试账本不存在（雪球当日可能尚未运行）"
        return info
    try:
        d = json.load(open(ledger, encoding="utf-8"))
        box = d.get("stocks") or d.get("codes") or d
        today = now.date().isoformat()
        tried = 0
        for k in queue:
            v = box.get(k)
            if v is None:
                continue
            try:
                dt = datetime.fromtimestamp(float(v))
            except (TypeError, ValueError):
                continue
            if dt.date().isoformat() == today:
                tried += 1
    except Exception as exc:  # noqa: BLE001
        info["reason"] = "账本读取失败 %s: %s" % (type(exc).__name__, exc)
        return info

    info["xq_attempted_today"] = tried
    info["xq_coverage"] = round(1.0 * tried / n, 4)
    info["xq_complete"] = info["xq_coverage"] >= XQ_MIN_RATIO
    if not info["xq_complete"]:
        info["reason"] = ("当日覆盖 %d/%d = %.1f%%，未达阈值 %.0f%%，"
                          "快照可能读到半截数据" % (tried, n, 100.0 * tried / n,
                                                    XQ_MIN_RATIO * 100))
    return info


def _save_completeness(info):
    """原子写；失败只吞掉，绝不因「标注失败」阻断快照本身。"""
    try:
        os.makedirs(os.path.dirname(COMPLETE_F), exist_ok=True)
        tmp = COMPLETE_F + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, COMPLETE_F)
    except Exception:  # noqa: BLE001
        pass


def _fmt_comp(info):
    if info.get("xq_complete") is None:
        return "unknown（%s）" % (info.get("reason") or "-")
    return "%d/%d = %.1f%% → %s" % (
        info["xq_attempted_today"], info["xq_queue"], info["xq_coverage"] * 100,
        "complete" if info["xq_complete"] else "INCOMPLETE")


def main():
    os.makedirs(os.path.dirname(LOG_F), exist_ok=True)
    lines = ["== run %s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S")]
    comp = xq_completeness()
    _save_completeness(comp)
    lines.append("== xq 完整性 " + _fmt_comp(comp))
    rcs = []
    for script in SCRIPTS:
        try:
            r = subprocess.run(
                [PYTHON, "-X", "utf8", os.path.join(HERE, script)],
                cwd=HERE, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=1800,
                env=_clean_env())
            rc = r.returncode
            out = (r.stdout or "").strip()[-500:]
            err = (r.stderr or "").strip()[-800:]
        except Exception as exc:  # 子进程起不来也要留痕
            rc, out, err = -1, "", "%s: %s" % (type(exc).__name__, exc)
        rcs.append(rc)
        lines.append("%s rc=%d" % (script, rc))
        if out:
            lines.append(out)
        if err:
            lines.append("STDERR: " + err)
    ok = all(rc == 0 for rc in rcs)
    lines.append("== result %s" % ("OK" if ok else "FAIL"))
    with open(LOG_F, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
