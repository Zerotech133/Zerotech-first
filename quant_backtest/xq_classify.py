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
  python xq_classify.py --reticker         # 提取器升级后重算存量 tickers（秒级）
  python xq_classify.py --recontent --limit 1200   # v1.3 语义存量补标
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
PROMPT_VERSION = "v1.3"    # v1.3：+ad/edu 出口、tech 定义收紧（实效检验揪出广告污染 98 条，老田拍板 2026-10-18）
OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
TIMEOUT_SEC = 60           # think:false 后单条 ~1s；60s 纯兜底
BODY_CHARS = 300           # 正文截断（短文本分类足够）

EVENT_TYPES = ["行情观点", "个股研判", "宏观政策", "基金理财", "交易策略", "广告推广", "闲聊杂谈"]
SENTIMENTS = ["bullish", "bearish", "neutral"]
CONTENT_TYPES = ["tech", "community", "news", "fundamental", "edu", "ad"]

CONTENT_TYPE_DESC = {
    "tech": "技术面分析（只收K线/均线/形态/指标/缠论/量价/点位/图形/买卖点的技术性讨论；投教科普不在此类）",
    "community": "社区探讨（散户交流/情绪表达/争论吐槽/求点评/打卡跟风，无论据链）",
    "news": "个股资讯（事实性转述：公告/业绩/订单/政策/新闻，少主观判断）",
    "fundamental": "基本面逻辑（业绩/行业/估值/商业模式/竞争格局论证）",
    "edu": "投教科普（方法论教学：怎么选基金/怎么打新/概念科普/投资入门）",
    "ad": "广告活动（悬赏/竞猜/红包/课程推广/平台活动，直接归此类）",
}

SYSTEM_PROMPT = (
    "你是金融舆情分类器。只输出一行 JSON，不要任何其他文字："
    '{"event_type":"<七选一>","content_type":"<六选一>",'
    '"sentiment":"<三选一>","confidence":<0到1小数>}。'
    "event_type 只能取：" + "、".join(EVENT_TYPES) + "。"
    "content_type 只能取（内容形态，与 event_type 正交）：" +
    "；".join("%s=%s" % (k, v) for k, v in CONTENT_TYPE_DESC.items()) + "。"
    "sentiment 只能取：" + "、".join(SENTIMENTS) + "（对 A股/港股大盘或个股的态度）。"
)

# 标的提取（纯规则）：A股 6 位数字（含前缀提示词）、港股 5 位、ETF/基金名常见词
# v1.1（2026-10-18）：RE_FUND 词干分支后缀改必选、删贪婪中间段——原版把
# 「红利指数基金的四大投」「债券B<br/>1、」等文本碎片整段吞进 tickers（R3 校准取数预演发现）；
# extract_tickers 增加噪音清洗兜底（<br/> 残片/中文虚词/年月形数字）。
# v1.2（2026-10-18，老田拍板「A 股代码提取升级」）：
#   ① 修 \b 病根——Python re 的 \b 按 \w 定边界，中文与数字同为 \w，
#      「贵州茅台600519」中「台|6」之间无边界 → 中文紧贴的代码全量漏提；
#      左界改 (?<![0-9.%])（中文/字母紧贴放行，小数/百分比防误截）。
#   ② 6 位码段白名单（232020 等 23 开头噪音段 52 行实证污染）——只收
#      合法交易所段：沪深主板/科创板/创业板/北交所/场内基金 ETF LOF；
#      20 开头年月形、23 开头编号形自动出局（白名单替代黑名单）。
#   ③ 右界 (?![元块])：金额语义（「150000 元」）不收。
RE_A = re.compile(r"(?<![0-9.%])([0-9]{6})(?![0-9])(?!\s*[元块])")
# 港股 5 位 v1.3：三通道判定（实证 status_id=144250900：tickers=["10676","23200",
# "18000","20000"] 全是金额/点位形 5 位噪音，且港股段 00-99 全合法无法白名单——
# 语境共现 + 后缀/区间硬排除双保险）：
#   ① pfx  「港股00700」「HK:00700」前缀紧邻 → 直接命中（不受共现限制）；
#   ② sfx  「00700.HK」「00700hk」后缀 → 直接命中（小写兼容）；
#   ③ bare 裸 5 位：语境共现（_HK_CONTEXT）或中文紧贴（「腾讯00700」，左界
#      允许 \w 中文；前置字是虚词/动词如「赚了18000」不算紧贴名）才收。
#   ④ ct   雪球 cashtag 形态「$腾讯控股(00700)$」——代码被 $/( 紧邻包裹
#      （实证 171914290/150923887：v1.3 首版 bare 通道误杀此主力形态，补直击）。
# 硬排除（③）：数字区间「18000-20000」（连字符双界）、点位/金额后缀
# 「10676点」「18000 元/块/万/手」——144250900 四个噪音全由此出局。
RE_HK = re.compile(
    r"(?:港股|HK:)\s*(?<![0-9])(?P<pfx>[0-9]{5})(?![0-9])"
    r"|(?<![0-9.])(?P<sfx>[0-9]{5})(?![0-9])\.?[Hh][Kk](?![A-Za-z0-9])"
    r"|(?<![0-9.\-])(?P<bare>[0-9]{5})(?![0-9\-])(?!\s*[元块万亿手点])"
    r"|[(（$](?P<ct>[0-9]{5})(?![0-9\-])(?!\s*[元块万亿手点])")
# 港股语境词（v1.3 bare 通道共现检测，整条 title+desc 匹配一次）。
# 「恒生」不单列——恒生电子(600570) 是 A 股，只收「恒指/恒生指数/恒生科技」。
_HK_CONTEXT_RE = re.compile(r"港股|港交所|恒指|恒生指数|恒生科技|国企指数|红筹|(?<![A-Za-z])[Hh][Kk](?![A-Za-z])")
# 中文紧贴名的前置字黑名单（v1.3）：「赚了18000」「涨到20000」这类动词+数字
# 不是公司名紧贴形；白名单制不现实（公司名无限），黑名单兜高频虚词/动词。
_ADJ_NOISE = set("了的到至达约近超破剩付花收给缴补返省欠赔是价净均倍赢输")
RE_FUND = re.compile(r"([0-9]{6})(?:\.OF| 基金)|((?:沪深300|中证500|中证1000|创业板|科创50|纳斯达克|标普500|恒生科技|红利|白酒|医药|半导体|新能源|军工|黄金|债券|货币)(?:ETF|基金|指数|LOF|联接))")
_RE_NOISE = re.compile(r"[<>]|br/|[的了这那哦吧呀]")
# 6 位代码段白名单（v1.2）：命中任一前缀才算场内证券，其余 6 位数字（编号/
# 年月/订单号）一律出局。港股东 5 位不走此表（港股 00-99 段全合法）。
_CODE_SEGS = (
    "600", "601", "603", "605", "688", "689",   # 沪：主板 + 科创板
    "000", "001", "002", "003",                 # 深：主板 + 原中小板
    "300", "301", "302",                        # 创业板
    "430", "920", "83", "87",                   # 北交所 / 新三板
    "50", "51", "52", "56", "58", "15", "16",   # 场内基金：ETF / LOF
)
_TICKER_VERSION = "v1.3"   # v1.3（2026-10-18）：港股裸 5 位改三通道判定——前缀/.HK 后缀
                           # 直接命中、中文紧贴名命中、裸数字需港股语境共现；加数字区间
                           # 连字符双界与「点/元/块/万/手」后缀硬排除（144250900 实证：
                           # 恒指点位 10676/23200、区间 18000-20000 全被误收）。

def _clean_ticker(code: str) -> bool:
    """噪音清洗（v1.2 白名单制）：6 位纯数字必须命中 _CODE_SEGS 合法段；
    其余（5 位港股、基金词）保留 HTML 残片/中文虚词清洗。"""
    if not code or len(code) > 10 or _RE_NOISE.search(code):
        return False
    if code.isdigit() and len(code) == 6:
        return code.startswith(_CODE_SEGS)
    return True


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
    # v1.2 迁移：content_type 列（已存在则跳过；automation 旧进程并发写兼容）
    cols = [r[1] for r in con.execute("PRAGMA table_info(xq_status_classified)")]
    if "content_type" not in cols:
        con.execute("ALTER TABLE xq_status_classified ADD COLUMN content_type TEXT")
        con.commit()


def extract_tickers(title: str, desc: str) -> list:
    text = (title or "") + " " + (desc or "")
    out = []
    for m in RE_A.findall(text):
        code = m if isinstance(m, str) else m[0]
        if code not in out and _clean_ticker(code):
            out.append(code)
    hk_ctx = bool(_HK_CONTEXT_RE.search(text))  # v1.3：港股语境共现整条只查一次
    for m in RE_HK.finditer(text):
        code = m.group("pfx") or m.group("sfx") or m.group("ct")  # 直击通道
        if not code:
            code = m.group("bare")
            if not hk_ctx:  # 裸 5 位：无语境时仅「中文紧贴名」形放行
                prev = text[m.start() - 1] if m.start() > 0 else ""
                if prev not in _ADJ_NOISE and "\u4e00" <= prev <= "\u9fff":
                    pass            # 紧贴名（腾讯00700）
                else:
                    continue        # 孤立裸数字，无语境不出局
        if code and code not in out and len(out) < 8 and _clean_ticker(code):
            out.append(code)
    for m in RE_FUND.findall(text):
        code = m[0] if m[0] else m[1]
        if code and code not in out and len(out) < 8 and _clean_ticker(code):
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
    event_type = sentiment = content_type = None
    confidence = None
    try:
        m = re.search(r"\{.*\}", answer, re.S)
        obj = json.loads(m.group(0)) if m else {}
        if obj.get("event_type") in EVENT_TYPES:
            event_type = obj["event_type"]
        if obj.get("content_type") in CONTENT_TYPES:   # v1.2 内容形态（缺失不降级 parse_ok，可 --recontent 补）
            content_type = obj["content_type"]
        if obj.get("sentiment") in SENTIMENTS:
            sentiment = obj["sentiment"]
        conf = obj.get("confidence")
        if isinstance(conf, (int, float)) and 0 <= conf <= 1:
            confidence = round(float(conf), 2)
        parse_ok = 1 if (event_type and sentiment) else 0
    except Exception:
        parse_ok = 0
    # v1.3 规则短路（优先于 LLM）：event_type=广告推广 → content_type 强制 'ad'
    # 实效检验实证：v1.2 四类无出口，98 条广告帖被迫污染 fundamental/community
    if event_type == "广告推广":
        content_type = "ad"
    if not parse_ok:
        log("DEGRADED %s: unparseable=%r" % (status_id, answer[:80]))

    con.execute(
        """INSERT OR REPLACE INTO xq_status_classified
           (status_id, event_type, sentiment, tickers, confidence,
            parse_ok, model, prompt_version, labeled_at, content_type)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (str(status_id), event_type, sentiment,
         json.dumps(extract_tickers(title, desc), ensure_ascii=False),
         confidence, parse_ok, MODEL, PROMPT_VERSION,
         datetime.now().strftime("%Y-%m-%d %H:%M:%S"), content_type))
    if parse_ok:
        log("OK %s cat=%s %s/%s | %s conf=%s %.1fs"
            % (status_id, category, event_type, content_type or "-", sentiment, confidence, dt))
    return True


def recontent_run(limit: int) -> int:
    """v1.2 存量补标：对已分类但 content_type 为空的行重跑 LLM 只补内容形态列。

    判例：lineage 诚实——prompt_version 标 'v1.2-re'（event_type 系 v1 判定，
    content_type 系 v1.2 补判），两段 lineage 不混淆。
    """
    con = sqlite3.connect(str(DB_PATH), timeout=30)
    con.execute("PRAGMA busy_timeout=30000")
    ensure_table(con)
    rows = con.execute(
        """SELECT c.status_id, s.title, s.desc
           FROM xq_status_classified c JOIN xq_status s ON s.status_id = c.status_id
           WHERE c.parse_ok = 1 AND c.content_type IS NULL LIMIT ?""",
        (limit,)).fetchall()
    log("recontent start n=%d" % len(rows))
    if not rows:
        log("recontent end rc=0 nothing-to-backfill")
        con.close()
        return 0
    ok = hard = consec = 0
    for status_id, title, desc in rows:
        body = ((title or "") + "\n" + (desc or "")).strip()[:BODY_CHARS + 60]
        payload = json.dumps({
            "model": MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": body or "（无文字内容）"},
            ],
            "stream": False, "think": False,
            "options": {"temperature": 0.1, "num_ctx": 2048, "num_predict": 256},
        }).encode("utf-8")
        req = urllib.request.Request(OLLAMA_URL, data=payload,
                                     headers={"Content-Type": "application/json"})
        try:
            resp = urllib.request.urlopen(req, timeout=TIMEOUT_SEC)
            answer = json.loads(resp.read())["message"]["content"].strip()
            answer = re.sub(r"<think>.*?</think>", "", answer, flags=re.S).strip()
        except Exception as e:
            hard += 1
            consec += 1
            log("RE-HARD-FAIL %s: %s" % (status_id, str(e)[:120]))
            if consec >= 8:
                log("RE-ABORT consecutive-fail=%d" % consec)
                break
            continue
        consec = 0
        try:
            m = re.search(r"\{.*\}", answer, re.S)
            obj = json.loads(m.group(0)) if m else {}
            ct = obj.get("content_type") if obj.get("content_type") in CONTENT_TYPES else None
        except Exception:
            ct = None
        con.execute(
            "UPDATE xq_status_classified SET content_type=?, prompt_version=?, labeled_at=? WHERE status_id=?",
            (ct, "v1.2-re", datetime.now().strftime("%Y-%m-%d %H:%M:%S"), str(status_id)))
        ok += 1
        con.commit()
    con.commit()
    n_left = con.execute(
        "SELECT COUNT(*) FROM xq_status_classified WHERE parse_ok=1 AND content_type IS NULL").fetchone()[0]
    con.close()
    log("recontent end rc=%d ok=%d hard=%d left=%d" % (0 if hard == 0 else 0, ok, hard, n_left))
    return 0


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
    ok = hard = consec = 0
    for i, row in enumerate(rows):
        if classify_one(con, row):
            ok += 1
            consec = 0
        else:
            hard += 1
            consec += 1
            if consec >= 8:
                # 熔断看「连续失败」而非累计占比：前期成功抬高占比分母会让
                # 后期超时空转数小时（07-18 实证：385 ok 后每条 60s 超时，
                # 旧条件需再熬 200 条才触发）
                log("ABORT %d consecutive hard-fails (model/network busy?)"
                    % consec)
                break
        con.commit()
        if (i + 1) % 25 == 0:
            log("progress %d/%d" % (i + 1, len(rows)))
    total_c = con.execute("SELECT COUNT(*) FROM xq_status_classified").fetchone()[0]
    rc = 1 if consec >= 8 else 0
    log("round end rc=%d labeled_this=%d hard_fail=%d derived_total=%d"
        % (rc, ok, hard, total_c))
    con.close()
    return rc


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


def reticker() -> int:
    """一次性重算：对已分类行按新正则重提取 tickers（不走 LLM，秒级）。

    tickers 是纯规则派生列，修复正则后历史行原地重算即可对齐新逻辑；
    只更新 xq_status_classified.tickers（派生表自身），原表零改动哲学不受影响。
    v1.2（2026-10-18）：段白名单 + 中文紧贴左界修复后重算，
    total=2012 changed=144 kept=1868 now_with_ticker=619，232020 污染 52→0。
    """
    con = sqlite3.connect(str(DB_PATH), timeout=30)
    con.execute("PRAGMA busy_timeout=30000")
    ensure_table(con)
    rows = con.execute(
        "SELECT status_id, (SELECT s.title FROM xq_status s WHERE s.status_id=c.status_id),"
        " (SELECT s.desc FROM xq_status s WHERE s.status_id=c.status_id), tickers"
        " FROM xq_status_classified c").fetchall()
    changed = kept = 0
    for status_id, title, desc, old in rows:
        new = json.dumps(extract_tickers(title, desc), ensure_ascii=False)
        if new != (old or "[]"):
            con.execute("UPDATE xq_status_classified SET tickers=? WHERE status_id=?",
                        (new, status_id))
            changed += 1
        else:
            kept += 1
    con.commit()
    n_t = con.execute(
        "SELECT COUNT(*) FROM xq_status_classified WHERE tickers != '[]'").fetchone()[0]
    log("reticker done total=%d changed=%d kept=%d now_with_ticker=%d version=%s"
        % (len(rows), changed, kept, n_t, _TICKER_VERSION))
    con.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="雪球舆情分类管道 v1")
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--reticker", action="store_true",
                    help="v1.1 按新正则重算已分类行的 tickers（不过 LLM）")
    ap.add_argument("--recontent", action="store_true",
                    help="v1.2 对已分类但 content_type 为空的行重跑 LLM 补内容形态")
    a = ap.parse_args()
    if a.status:
        return show_status()
    if a.reticker:
        return reticker()
    if a.recontent:
        return recontent_run(a.limit)
    return run(a.limit)


if __name__ == "__main__":
    sys.exit(main())
