"""执行反馈账本 v0（盯盘→策略回流 · 协作协议 v0 账本解耦）。

RiskGuard 每次 evaluate（拦截与放行）落一行决策粒度留痕，供策略 Agent
做误杀校准（critical 拦截样本后验 +0.65% vs 放行 -0.25% 的机械触发线）。

- 文件：portal/data/risk_feedback.jsonl，append-only，每行一个 JSON
  （ensure_ascii=False，键字典序）。
- 字段白名单（schema v0 冻结，多传丢弃）：
  ts/date/strategy/symbol/action(blocked|allowed)/rule/score/
  decision_price/holding_days/fwd5/fwd20/note
- 隐私与体积：只记结构化字段，note 截断 200 字符，单行 <1KB，
  不落新闻原文等大对象。
- 原子追加：O_APPEND + 单次 os.write（行 <4KB，同引擎
  KGStockAnalysis/kg_riskguard.py append_ledger 范式）。
- fwd5/fwd20 写入时恒 null，窗口成熟后由回填器原子重写（先例
  quant_backtest/chanlun_sample.py _backfill_r60/_atomic_write，
  回填器 v0 后续实现，不在本模块）。
"""

import json
import os

LEDGER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "data", "risk_feedback.jsonl")

ACTIONS = ("blocked", "allowed")

# schema v0 字段表（白名单序列化：调用方多传的一律丢弃，防大对象入账）
FIELDS = ("ts", "date", "strategy", "symbol", "action", "rule", "score",
          "decision_price", "holding_days", "fwd5", "fwd20", "note")

NOTE_MAX = 200


class LedgerError(ValueError):
    """record 非法（缺必填/枚举越界）时抛出。"""


def append(record, path=None):
    """校验并原子追加一行；返回落盘的 dict（白名单化后）。

    目录不存在自动创建；action 必须 ∈ ACTIONS；ts/date/strategy/symbol/
    action/rule 为必填。行按 UTF-8 写入，单次 os.write 保证追加原子。
    """
    path = path or LEDGER_PATH
    row = _sanitize(record)
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    line = (json.dumps(row, ensure_ascii=False, sort_keys=True)
            + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, line)
    finally:
        os.close(fd)
    return row


def query(strategy=None, action=None, path=None):
    """过滤读取；strategy/action 为 None 表示不过滤。坏行跳过不抛。"""
    path = path or LEDGER_PATH
    if not os.path.isfile(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                r = json.loads(ln)
            except Exception:
                continue
            if strategy is not None and r.get("strategy") != strategy:
                continue
            if action is not None and r.get("action") != action:
                continue
            out.append(r)
    return out


def _sanitize(record):
    """白名单序列化 + 必填/枚举校验 + note 截断。"""
    if not isinstance(record, dict):
        raise LedgerError("record 必须是 dict")
    missing = [k for k in ("ts", "date", "strategy", "symbol", "action",
                           "rule") if not record.get(k)]
    if missing:
        raise LedgerError("缺必填字段: %s" % ",".join(missing))
    if record["action"] not in ACTIONS:
        raise LedgerError("action 非法: %r (须为 blocked|allowed)"
                          % (record["action"],))
    row = {}
    for k in FIELDS:
        if k not in record:
            continue
        v = record[k]
        if k == "note":
            v = str(v or "")[:NOTE_MAX]
        row[k] = v
    return row
