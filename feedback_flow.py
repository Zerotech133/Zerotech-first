# -*- coding: utf-8 -*-
"""调优反馈流水（K-PUSH-002 B1）——客户反馈调优闭环的全生命周期事件账。

定位：与 push_flow（发送流水）并排的闭环第二本账。客户经飞书 bot
「调优」指令（语音=飞书客户端自带转文字）提交的每一条调优请求，
从受理到落地的每个阶段落一行，供 8097 推送中台透视与追溯。

写入方：portal/tune_gate.py（submit/approve/reject/verify/deploy 全阶段）
读  方：portal/push_admin_server.py（/api/state tune 概览 + action tune_list）
行格式（白名单序列化，多传丢弃——学 risk_feedback_ledger v0）：
    {"ts","date","req_id","stage","customer","plan","source",
     "intent","scope","operator","note"}
stage 枚举：
    received      已受理（submit 入口）
    parsed        已解析（意图/标的/范围结构化）
    approved      审核通过（触发沙盒草案）
    rejected      已驳回（note 带驳回原因，回执客户）
    verified      沙盒草案就绪（note 带回测参数草案摘要）
    deployed      已落地（私有空间写入 or 共享模型变更挂账）
    pending_manual 环境不可用降级（沙盒需人工跑，不 block 审批流）
    receipt       回执已发（客户侧闭环反馈）
原则：fail-safe——记账失败只吞掉，绝不影响闭环主链路（同 push_flow）。
"""
import json
import os
import threading

FLOW_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "portal", "work", "tune_requests.jsonl")

STAGES = ("received", "parsed", "approved", "rejected", "verified",
          "deployed", "pending_manual", "receipt")

# schema v0 字段白名单（调用方多传的一律丢弃，防大对象入账）
FIELDS = ("ts", "date", "req_id", "stage", "customer", "plan", "source",
          "intent", "scope", "operator", "note")

NOTE_MAX = 200
_lock = threading.Lock()


def record(req_id, stage, customer="", plan="internal", source="text",
           intent="", scope="private", operator="", note="", path=None):
    """追加一行阶段事件。任何异常吞掉（账目丢失可容忍，闭环中断不可容忍）。

    stage 非法时同样吞掉不抛——tune_gate 调用处不依赖返回值。"""
    try:
        from datetime import datetime
        if stage not in STAGES:
            return None
        row = {"ts": datetime.now().isoformat(timespec="seconds"),
               "date": datetime.now().strftime("%Y-%m-%d"),
               "req_id": str(req_id)[:40], "stage": stage,
               "customer": str(customer or "")[:60],
               "plan": str(plan or "internal")[:20],
               "source": str(source or "text")[:20],
               "intent": str(intent or "")[:60],
               "scope": str(scope or "private")[:20],
               "operator": str(operator or "")[:60],
               "note": str(note or "")[:NOTE_MAX]}
        data = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
        with _lock:
            # 跨进程原子追加（O_APPEND + 单次 os.write 整行，同 push_flow
            # 判例：threading.Lock 只护同进程，账本可能被 bot/8097 双写）
            fd = os.open(path or FLOW_PATH,
                         os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
            try:
                os.write(fd, data)
            finally:
                os.close(fd)
        return row
    except Exception:
        return None


def events(req_id=None, path=None):
    """按时间序读取事件（可按 req_id 过滤）。坏行跳过不抛。"""
    p = path or FLOW_PATH
    out = []
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                if req_id and r.get("req_id") != req_id:
                    continue
                out.append(r)
    except OSError:
        pass
    return out


def requests_view():
    """账本 → 请求单状态机视图：{req_id: {stage, first_ts, last_ts,
    customer, plan, source, intent, scope, events:[...]}}。
    stage 取该单最后一条「状态事件」的 stage——receipt 是伴随通知
    不是状态（通过/驳回后的回执记录），不计入状态机，否则「已通过」
    会被自己的回执事件掩盖成 receipt。"""
    view = {}
    for e in events():
        rid = e.get("req_id")
        if not rid:
            continue
        v = view.setdefault(rid, {"req_id": rid, "stage": "received",
                                  "first_ts": e.get("ts", ""),
                                  "last_ts": e.get("ts", ""),
                                  "customer": e.get("customer", ""),
                                  "plan": e.get("plan", "internal"),
                                  "source": e.get("source", "text"),
                                  "intent": e.get("intent", ""),
                                  "scope": e.get("scope", "private"),
                                  "events": []})
        v["last_ts"] = e.get("ts", v["last_ts"])
        if e.get("stage") != "receipt":
            v["stage"] = e.get("stage", v["stage"])
        # 首条 received/parsed 事件携带的客户元信息最全，后续事件回填空缺
        for k in ("customer", "plan", "source", "intent", "scope"):
            if e.get(k) and not v.get(k):
                v[k] = e[k]
        v["events"].append(e)
    return view


def stats(days=7):
    """近 N 天：逐日提交单数（按 received 计）+ 今日按 stage 汇总。"""
    import datetime as dt
    today = dt.date.today()
    start = (today - dt.timedelta(days=days - 1)).isoformat()
    by_day, today_by_stage = {}, {}
    for e in events():
        d = e.get("date", "")
        if not (start <= d <= today.isoformat()):
            continue
        if e.get("stage") == "received":
            by_day[d] = by_day.get(d, 0) + 1
        if d == today.isoformat():
            s = e.get("stage", "?")
            today_by_stage[s] = today_by_stage.get(s, 0) + 1
    seq = [(today - dt.timedelta(days=days - 1 - i)).isoformat()
           for i in range(days)]
    return {"by_day": [{"date": d, "count": by_day.get(d, 0)} for d in seq],
            "today_by_stage": today_by_stage}


if __name__ == "__main__":
    print(json.dumps(stats(7), ensure_ascii=False, indent=1))
