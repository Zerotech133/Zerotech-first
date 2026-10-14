# -*- coding: utf-8 -*-
"""统一发送流水（商业化计费依据）——所有渠道的每一次真实发送落一行 JSONL。

写入方：quant_backtest/subscribe.py（订阅推送，_push_card 后）
        portal/km_feishu_hub.py（知识库摘要卡，push_lib 内）
读  方：portal/push_admin_server.py（今日用量 + 近 7 天趋势 → 推送中台总览）
行格式：{"ts","date","kind","channel","target","count","ok","tenant"}
原则：fail-safe——记录失败只吞掉，绝不影响推送主链路；只记"真实发送"，
      跳过（无变更/熔断/停用）不记。
"""
import json
import os
import threading

FLOW_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "portal", "work", "push_flow.jsonl")
_lock = threading.Lock()


def record(kind, channel, target, count=1, ok=True, tenant="default"):
    """追加一行流水。任何异常吞掉（计费依据丢失可容忍，推送中断不可容忍）。"""
    try:
        from datetime import datetime
        row = {"ts": datetime.now().isoformat(timespec="seconds"),
               "date": datetime.now().strftime("%Y-%m-%d"),
               "kind": kind, "channel": channel, "target": str(target)[:120],
               "count": int(count), "ok": bool(ok), "tenant": str(tenant)[:40]}
        data = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
        with _lock:
            # 跨进程原子追加：O_APPEND + 单次 os.write 整行（本文件被 4 个独立
            # 进程写入，threading.Lock 只保护同进程；半行交错=计费账目损坏）
            fd = os.open(FLOW_PATH, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
            try:
                os.write(fd, data)
            finally:
                os.close(fd)
    except Exception:
        pass


def stats(days=7):
    """近 N 天统计：逐日成功发送条数 + 今日按 kind 汇总（读侧容错降级为空）。"""
    import datetime as dt
    today = dt.date.today()
    start = (today - dt.timedelta(days=days - 1)).isoformat()
    by_day, today_by_kind = {}, {}
    try:
        with open(FLOW_PATH, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                d = r.get("date", "")
                if not (start <= d <= today.isoformat()):
                    continue
                if not r.get("ok", True):
                    continue
                c = int(r.get("count", 1))
                by_day[d] = by_day.get(d, 0) + c
                if d == today.isoformat():
                    k = r.get("kind", "?")
                    today_by_kind[k] = today_by_kind.get(k, 0) + c
    except OSError:
        pass
    seq = [(today - dt.timedelta(days=days - 1 - i)).isoformat()
           for i in range(days)]
    return {"by_day": [{"date": d, "count": by_day.get(d, 0)} for d in seq],
            "today_by_kind": today_by_kind}


def quota_check():
    """返回 (plan, quota, sent_today, blocked)。
    internal（或缺配置）→ 永不拦；其他套餐 quota>0 且今日已发 ≥ quota → 拦。
    blocked=True 时调用方应跳过真实发送（风险提示类日报例外，见设计文档）。"""
    try:
        cfg = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "portal", "work", "commercial.json")
        with open(cfg, encoding="utf-8") as f:
            biz = json.load(f)
    except Exception:
        return ("internal", 0, 0, False)
    plan = biz.get("plan", "internal")
    quota = int(biz.get("quota_daily", 0) or 0)
    sent = stats(1)["by_day"][-1]["count"]
    blocked = (plan != "internal" and quota > 0 and sent >= quota)
    return (plan, quota, sent, blocked)


if __name__ == "__main__":
    print(json.dumps(stats(7), ensure_ascii=False, indent=1))
