# -*- coding: utf-8 -*-
"""统一发送流水（商业化计费依据）——所有渠道的每一次真实发送落一行 JSONL。

写入方：quant_backtest/subscribe.py（订阅推送，_push_card 后）
        portal/km_feishu_hub.py（知识库摘要卡，push_lib 内）
        K-PUSH-001 底座埋点（自动，kind 带 auto: 前缀）：
          news_collector/feishu_client.send_msg（OpenAPI 通道全量）
          news_collector/send_feishu.send_content（lark-cli 回退通道）
读  方：portal/push_admin_server.py（今日用量 + 近 7 天趋势 → 推送中台总览）
行格式：{"ts","date","kind","channel","target","count","ok","tenant"}
原则：fail-safe——记录失败只吞掉，绝不影响推送主链路；只记"真实发送"，
      跳过（无变更/熔断/停用）不记。
计费口径：auto: 前缀行是底座追溯账（全量覆盖、kind 从主脚本名推断），
      stats()/quota_check() 不计入——与业务方手动 record（计费账）隔离，
      防双计；后续批次收编手动记账后再并账。
"""
import json
import os
import sys
import threading
import logging

FLOW_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "portal", "work", "push_flow.jsonl")
_lock = threading.Lock()

# W38 送达证据分级白名单（与 portal/notify_contracts.EVIDENCE_DELIVERY 对齐；
# 不直接 import：本模块被 4 个独立进程以不同 sys.path 姿态导入，须零依赖）。
_EVIDENCE_DELIVERY = ("api_accepted", "receiver_verified", "dead_letter")


def record(kind, channel, target, count=1, ok=True, tenant="default",
           evidence=None, domain=None, message_id=None):
    """追加一行流水。任何异常吞掉（计费依据丢失可容忍，推送中断不可容忍）。

    W38：evidence 非 None 时行内多写 "evidence" 键（值须在
    EVIDENCE_DELIVERY 内，非法值醒目写 "dead_letter"）；None 时行结构与
    历史完全一致（向后兼容）。
    P2：domain 非 None 时行内多写 "domain" 键（owner/ops/customer 分域
    一级路由字段）；缺省 None 不写键，旧调用零变化。
    2026-11-09：message_id 非空时行内多写 "message_id" 键（飞书回执单号，
    用于 watchtower/流水对账）；None 时不写键，旧调用零变化。"""
    try:
        from datetime import datetime
        row = {"ts": datetime.now().isoformat(timespec="seconds"),
               "date": datetime.now().strftime("%Y-%m-%d"),
               "kind": kind, "channel": channel, "target": str(target)[:120],
               "count": int(count), "ok": bool(ok), "tenant": str(tenant)[:40]}
        if evidence is not None:
            ev = str(evidence).strip()
            row["evidence"] = ev if ev in _EVIDENCE_DELIVERY else "dead_letter"
        if domain is not None:
            row["domain"] = str(domain)[:20]
        if message_id is not None:
            mid = str(message_id).strip()
            if mid:
                row["message_id"] = mid[:120]
        data = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
        with _lock:
            # 跨进程原子追加：O_APPEND + 单次 os.write 整行（本文件被 4 个独立
            # 进程写入，threading.Lock 只保护同进程；半行交错=计费账目损坏）
            fd = os.open(FLOW_PATH, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
            try:
                os.write(fd, data)
            finally:
                os.close(fd)
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "push_flow.py", exc)


def infer_kind():
    """K-PUSH-001 底座埋点用 kind 推断：取主脚本名（进程内 import 场景
    即业务脚本名，如 news_digest；子进程 send_feishu 场景退化为发送器
    名）。一律加 auto: 前缀——追溯账标记，stats 不计费防双计。"""
    try:
        stem = os.path.splitext(os.path.basename(sys.argv[0] or ""))[0]
        return "auto:" + (stem or "unknown")
    except Exception:
        return "auto:unknown"


def stats(days=7):
    """近 N 天统计：逐日成功发送条数 + 今日按 kind 汇总（读侧容错降级为空）。"""
    import datetime as dt
    today = dt.date.today()
    start = (today - dt.timedelta(days=days - 1)).isoformat()
    by_day, today_by_kind = {}, {}
    try:
        # K-STOCK-400：errors="replace"——半行交错若切断多字节字符，
        # 原 for 迭代内解码抛 UnicodeDecodeError（在下方内层 try 之外）
        # 整体逃逸 → 调用方 except 兜住后 quota_check 持续走异常分支
        # fail-open，配额保护静默失效。replace 后坏行 json.loads 失败
        # 被 continue 吞掉，恢复「降级为空」承诺。
        with open(FLOW_PATH, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                # K-PUSH-001：auto: 追溯账不计入计费统计（防与业务手动
                # record 双计）——并账在手动记账收编批次再做
                if str(r.get("kind", "")).startswith("auto:"):
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
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "push_flow.py", exc)
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
