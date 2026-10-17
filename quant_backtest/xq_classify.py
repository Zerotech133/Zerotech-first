# -*- coding: utf-8 -*-
"""雪球舆情分类管道 v1（K-ORG-165 P1：水镜情报线治理第一刀）。

设计（2026-10-18 深夜 Hermes 治理提案 P1，老田「下一步」放行）：
  - 原表零改动：分类结果落派生表 xq_status_classified（upsert 幂等，
    重跑零副作用——tushare staging materialize 闸同款哲学）；
  - 增量优先：只标 xq_status 中未标行（新帖优先 ORDER BY created_at
    DESC），存量 14212 行靠每日轮逐步追平；
  - LLM 分工（省 token 防幻觉）：分类+情绪交给 qwen3.5:9b（A0 参谋长
    判读同款，质量已验证）；标的代码用纯正则预提取（不进 prompt）；
  - JSON-only 契约（D1 判例：system 只要求 JSON，与用户消息无散文冲突）；
    解析失败 parse_ok=0 → degraded 状态位落账，重跑自动补（NOT IN 幂等）；
  - fail-visible：模型/网络异常退出码 1，状态日志落盘（ASCII，判例）；
  - 空壳行跳过（纯图帖无文字，分类无意义）。

分类维度（对齐 RiskGuard 事件风险校准取数需求）：
  event_type ∈ {行情观点, 个股研判, 宏观政策, 基金理财, 交易策略, 广告推广, 闲聊杂谈}
  sentiment  ∈ {bullish, bearish, neutral}
  tickers    = 正则提取（6 位 A 股/ETF、5 位港股、常见基金关键词）

用法：
  python xq_classify.py --limit 500        # 增量一轮（每日 18:30 调度用）
  python xq_classify.py --limit 12         # 小批量实弹验证
  python xq_classify.py --status           # 只读看进度
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB_PATH = HERE / "data" / "xueqiu" / "xueqiu_info.db"
LOG_DIR = Path(r"C:\aios\automation_runs") / "xq_classify"   # 定时任务产物统一根（2026-10-18 老田拍板）
STATUS_LOG = LOG_DIR / "xq_classify_status.log"

MODEL = "qwen3.5:9b"
PROMPT_VERSION = "v1"
OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
TIMEOUT_SEC = 60           # think:false 后单条 ~1s；60s 纯兜底
BODY_CHARS = 300           # 正文截断（短文本分类足够）

EVENT_TYPES = ["行情观点", "个股研判", "宏观政策", "基金理财", "交易策略", "广告推广", "闲聊杂谈"]
SENTIMENTS = ["bullish", "bearish", "neutral"]

SYSTEM_PROMPT = (
    "你是金融舆情分类器。只输出一行 JSON，不要任何其他文字："
    '{"event_type":"<七选一>","sentiment":"<三选一>","confidence":<0到1小数>}。'
    "event_type 只能取：" + "、".join(EVENT_TYPES) + "。"
    "sentiment 只能取：" + "、".join(SENTIMENTS) + "（对 A股/港股大盘或个股的态度）。"
)

# 标的提取（纯规则）：A股 6 位数字（含前缀提示词）、港股 5 位、ETF/基金名常见词
RE_A = re.compile(r"(?:SZ|SH|sh|sz)?[Â€]*(?:\b)([0-9]{6})(?![0-9])")
RE_HK = re.compile(r"(?:\b)([0-9]{5})(?![0-9])\.?HK|(?:港股)?([0-9]{5})(?![0-9])")
RE_FUND = re.compile(r"([0-9]{6})(?:\.OF| 基金)|((?:沪深300|中证500|中证1000|创业板|科创50|纳斯达克|标普500|恒生科技|红利|白酒|医药|半导体|新能源|军工|黄金|债券|货币)[^，。；,\s]{0,8}(?:ETF|基金|指数)?)")


def log(msg: str) -> None:
    """ASCII 安全状态日志（计划任务/自动化排障判例）。"""
    LOG_DIR.mkdir(exist_ok=True)
    line = "%s | %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        with STATUS_LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    print(line)


def ensure_table(con: sqlite3.Connection) -> None:
    con.execute(
        """CREATE TABLE IF NOT EXISTS xq_status_classified (
             status_id TEXT PRIMARY KEY,
             event_type TEXT,
             sentiment TEXT,
             tickers TEXT,
             confidence REAL,
             parse_ok INTEGER,
             model TEXT,
             prompt_version TEXT,
             labeled_at TEXT
           )"""
    )
    con.commit()


def extract_tickers(title: str, desc: str) -> list:
    text = (title or "") + " " + (desc or "")
    out = []
    for m in RE_A.findall(text):
        code = m if isinstance(m, str) else m[0]
        if code not in out:
            out.append(code)
    for m in RE_FUND.findall(text):
        code = m[0] if m[0] else m[1]
        if code and code not in out and len(out) < 8:
            out.append(code)
    return out[:8]


def classify_one(con: sqlite3.Connection, row: tuple) -> bool:
    """单条标注：成功(含 degraded)返回 True（落账），硬异常返回 False。"""
    status_id, title, desc, category = row
    body = ((title or "") + "\n" + (desc or "")).strip()[:BODY_CHARS + 60]
    payload = json.dumps({
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": body or "（无文字内容）"},
        ],
        "stream": False,
        "think": False,        # 判例：qwen3.5 思考块吃满小 ctx 会把答案挤空（探针实证 0.7s vs 34.8s/失控分钟级）
        "options": {"temperature": 0.1, "num_ctx": 2048, "num_predict": 256},
    }).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA_URL, data=payload,
        headers={"Content-Type": "application/json"})
    try:
        t0 = time.time()
        resp = urllib.request.urlopen(req, timeout=TIMEOUT_SEC)
        answer = json.loads(resp.read())["message"]["content"].strip()
        # 防御：老版客户端/模型把思考块写进 content 时剥离
        answer = re.sub(r"<think>.*?</think>", "", answer, flags=re.S).strip()
        dt = time.time() - t0
    except Exception as e:
        log("HARD-FAIL %s: %s" % (status_id, str(e)[:120]))
        return False

    parse_ok = 0
    event_type = sentiment = None
    confidence = None
    try:
        m = re.search(r"\{.*\}", answer, re.S)
        obj = json.loads(m.group(0)) if m else {}
        if obj.get("event_type") in EVENT_TYPES:
            event_type = obj["event_type"]
        if obj.get("sentiment") in SENTIMENTS:
            sentiment = obj["sentiment"]
        conf = obj.get("confidence")
        if isinstance(conf, (int, float)) and 0 <= conf <= 1:
            confidence = round(float(conf), 2)
        parse_ok = 1 if (event_type and sentiment) else 0
    except Exception:
        parse_ok = 0
    if not parse_ok:
        log("DEGRADED %s: unparseable=%r" % (status_id, answer[:80]))

    con.execute(
        """INSERT OR REPLACE INTO xq_status_classified
           (status_id, event_type, sentiment, tickers, confidence,
            parse_ok, model, prompt_version, labeled_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (str(status_id), event_type, sentiment,
         json.dumps(extract_tickers(title, desc), ensure_ascii=False),
         confidence, parse_ok, MODEL, PROMPT_VERSION,
         datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    if parse_ok:
        log("OK %s cat=%s %s | %s conf=%s %.1fs"
            % (status_id, category, event_type, sentiment, confidence, dt))
    return True


def run(limit: int) -> int:
    con = sqlite3.connect(str(DB_PATH), timeout=30)
    con.execute("PRAGMA busy_timeout=30000")
    ensure_table(con)
    rows = con.execute(
        """SELECT s.status_id, s.title, s.desc, s.category
           FROM xq_status s
           WHERE s.status_id NOT IN
                 (SELECT status_id FROM xq_status_classified)
             AND NOT ((s.title IS NULL OR s.title='')
                      AND (s.desc IS NULL OR s.desc=''))
           ORDER BY s.created_at DESC LIMIT ?""",
        (limit,)).fetchall()
    log("round start pending_sampled=%d limit=%d" % (len(rows), limit))
    if not rows:
        log("round end rc=0 all-caught-up total=%d"
            % con.execute("SELECT COUNT(*) FROM xq_status").fetchone()[0])
        con.close()
        return 0
    ok = hard = 0
    for i, row in enumerate(rows):
        if classify_one(con, row):
            ok += 1
        else:
            hard += 1
            if hard >= 5 and hard > i // 2:
                log("ABORT hard-fail majority (model/network down?)")
                break
        con.commit()
        if (i + 1) % 25 == 0:
            log("progress %d/%d" % (i + 1, len(rows)))
    total_c = con.execute("SELECT COUNT(*) FROM xq_status_classified").fetchone()[0]
    log("round end rc=%d labeled_this=%d hard_fail=%d derived_total=%d"
        % (0 if hard < 5 else 1, ok, hard, total_c))
    con.close()
    return 0 if hard < 5 else 1


def show_status() -> int:
    con = sqlite3.connect("file:%s?mode=ro" % DB_PATH.as_posix(), uri=True)
    src = con.execute("SELECT COUNT(*) FROM xq_status").fetchone()[0]
    try:
        c = con.execute("SELECT COUNT(*) FROM xq_status_classified").fetchone()[0]
        ok = con.execute(
            "SELECT COUNT(*) FROM xq_status_classified WHERE parse_ok=1").fetchone()[0]
        deg = c - ok
        dist = con.execute(
            """SELECT event_type, sentiment, COUNT(*) FROM xq_status_classified
               WHERE parse_ok=1 GROUP BY event_type, sentiment
               ORDER BY 3 DESC LIMIT 10""").fetchall()
    except sqlite3.OperationalError:
        print("derived table not created yet; source=%d" % src)
        return 0
    print("source=%d classified=%d (ok=%d degraded=%d) pending=%d"
          % (src, c, ok, deg, src - c))
    for r in dist:
        print("  %-8s %-8s %d" % r)
    con.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="雪球舆情分类管道 v1")
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    if a.status:
        return show_status()
    return run(a.limit)


if __name__ == "__main__":
    sys.exit(main())
