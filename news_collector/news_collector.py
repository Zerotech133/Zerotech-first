# -*- coding: utf-8 -*-
"""金融新闻采集器 — 15 分钟一次：拉源 → 去重入库 → 关注词命中即时推送

数据源（免费公开接口，单个失败自动跳过不影响整体）:
  sina        新浪财经直播（A 股/国内市场）
  eastmoney   东方财富 7x24 快讯
  wallstreetcn 华尔街见闻全球快讯

存储: news.db (SQLite) — 表 news(id PK 去重) / runs(运行审计)
推送: 命中 watch_keywords 或 watch_stocks 的新闻 → send_feishu.py
状态: status.json 供门户控制台读取
用法: python news_collector.py [--dry]   (--dry 只采集推送测试到日志，不发飞书)
"""

import html
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

from news_time import parse_full_time, parse_news_time

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "news.db")
CFG_PATH = os.path.join(HERE, "news_config.json")
STATUS_PATH = os.path.join(HERE, "status.json")
LOG_PATH = os.path.join(HERE, "logs", "collect.log")
CST = timezone(timedelta(hours=8))  # 财经语境统一用北京时间
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def _h5_entry():
    """统一 H5 入口链接（未启用/失败返回 ''，卡片不出死链）。"""
    try:
        _portal = os.path.join(os.path.dirname(HERE), "portal")
        if _portal not in sys.path:
            sys.path.insert(0, _portal)
        from feishu_card_v2 import m_url

        return m_url("entry")
    except Exception:  # noqa: BLE001
        return ""


# 短别名的区分性后缀：别名后紧跟这些词时通常指其他主体
# （平安→平安银行/平安证券；茅台→茅台镇；宁德→宁德市 等）
DISAMBIG_SUFFIX = (
    "银行",
    "证券",
    "基金",
    "人寿",
    "产险",
    "信托",
    "租赁",
    "健康",
    "好医生",
    "科技",
    "不动产",
    "养老",
    "镇",
    "市",
    "区",
)

# K-STOCK-355 金融新闻速报同步目标：内部测试群（飞书实名，owner=本人；
# 与 news_config.push_targets「内部测试」同群，该处旧注释「家人群」系命名漂移）
# K-STOCK-403：id 改由 push_targets 单一事实源按名解析
try:
    import push_targets

    TEST_GROUP_ID = push_targets.target_id("内部测试") or ""
except ImportError:  # 独立分发场景兜底为空（发送失败可见）
    TEST_GROUP_ID = ""


def term_in(term, text):
    """子串命中，但排除别名后紧跟区分性后缀的情况（所有出现位置都被排除才算未命中）。"""
    i = text.find(term)
    while i >= 0:
        tail = text[i + len(term) : i + len(term) + 2]
        if not any(tail.startswith(s) for s in DISAMBIG_SUFFIX):
            return True
        i = text.find(term, i + 1)
    return False


def log(msg):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write("[%s] %s\n" % (datetime.now(CST).strftime("%m-%d %H:%M:%S"), msg))


def now_cst():
    return datetime.now(CST)


def strip_html(s):
    s = re.sub(r"<[^>]+>", "", s or "")
    return html.unescape(s).strip()


def http_json(url, params=None, timeout=12):
    r = requests.get(url, params=params, headers=UA, timeout=timeout)
    r.raise_for_status()
    return r.json()


# ── 数据源 ────────────────────────────────────────────


def fetch_sina(size):
    j = http_json(
        "https://zhibo.sina.com.cn/api/zhibo/feed",
        {"page": 1, "page_size": size, "zhibo_id": 152},
    )
    items = j["result"]["data"]["feed"]["list"]
    out = []
    for it in items:
        text = strip_html(it.get("rich_text", ""))
        if not text:
            continue
        out.append(
            {
                "id": "sina:%s" % it.get("id"),
                "ts": (it.get("create_time") or "")[:16],
                "title": text[:80],
                "content": text[:300],
            }
        )
    return out


def fetch_eastmoney(size):
    j = http_json(
        "https://np-listapi.eastmoney.com/comm/web/getFastNewsList",
        {
            "client": "web",
            "biz": "web_724",
            "fastColumn": "102",
            "sortEnd": "",
            "pageSize": size,
            "req_trace": str(int(time.time())),
        },
    )
    items = j["data"]["fastNewsList"]
    out = []
    for it in items:
        title = (it.get("title") or "").strip()
        if not title:
            continue
        out.append(
            {
                "id": "em:%s" % it.get("code"),
                "ts": (it.get("showTime") or "")[:16],
                "title": title[:80],
                "content": (it.get("summary") or "")[:300],
            }
        )
    return out


def fetch_wallstreetcn(size):
    j = http_json(
        "https://api-one.wallstcn.com/apiv1/content/lives",
        {"channel": "global-channel", "limit": size, "accept": "live"},
    )
    items = j["data"]["items"]
    out = []
    for it in items:
        text = (it.get("content_text") or "").strip()
        if not text:
            continue
        ts = datetime.fromtimestamp(int(it["display_time"]), CST).strftime(
            "%Y-%m-%d %H:%M"
        )
        out.append(
            {
                "id": "wsc:%s" % it.get("id"),
                "ts": ts,
                "title": text[:80],
                "content": text[:300],
            }
        )
    return out


def fetch_announcement(size):
    """东财 A 股公告（沪深京，财报/重大事项，按公告时间倒序）。"""
    j = http_json(
        "https://np-anotice-stock.eastmoney.com/api/security/ann",
        {
            "page_size": size,
            "page_index": 1,
            "ann_type": "A",
            "client_source": "web",
            "f_node": "0",
            "s_node": "0",
        },
    )
    out = []
    for it in ((j.get("data") or {}).get("list")) or []:
        title = (it.get("title") or "").strip()
        if not title:
            continue
        codes = it.get("codes") or []
        code = (codes[0].get("stock_code") or "") if codes else ""
        nm = (codes[0].get("short_name") or "") if codes else ""
        nd = it.get("notice_date") or ""
        out.append(
            {
                "id": "ann:%s" % it.get("art_code"),
                "ts": nd[:16],
                "title": ("[%s %s] %s" % (code, nm, title))[:80],
                "content": title[:300],
            }
        )
    return out


def fetch_survey(size):
    """东财机构调研（datacenter RPT_ORG_SURVEYNEW；同票同日多机构合并一条）。"""
    j = http_json(
        "https://datacenter-web.eastmoney.com/api/data/v1/get",
        {
            "reportName": "RPT_ORG_SURVEYNEW",
            "columns": "ALL",
            "pageSize": size,
            "pageNumber": 1,
            "sortColumns": "NOTICE_DATE",
            "sortTypes": "-1",
        },
    )
    items = ((j.get("result") or {}).get("data")) or []
    out, seen = [], set()
    for it in items:
        code = (it.get("SECUCODE") or it.get("OBJECT_CODE") or "").replace(".", "")
        nm = (it.get("SECURITY_NAME_ABBR") or "").strip()
        if not code:
            continue
        key = "%s|%s" % (code, (it.get("RECEIVE_START_DATE") or "")[:10])
        if key in seen:
            continue
        seen.add(key)
        org = (it.get("ORG_NAME") or "").strip()
        nd = it.get("NOTICE_DATE") or ""
        out.append(
            {
                "id": "survey:%s" % key,
                "ts": nd[:16],
                "title": ("[%s %s] 机构调研：%s" % (code, nm, org))[:80],
                "content": (
                    "机构 %s 于 %s 调研 %s（%s）"
                    % (org, key.split("|")[1], nm, it.get("RECEIVE_PLACE") or "")
                )[:300],
            }
        )
    return out


def fetch_report(size):
    """东财研报（行业 qType=1 + 个股 qType=0，各拉一半，近 7 天）。"""
    out = []
    half = max(size // 2, 10)
    today = now_cst().strftime("%Y-%m-%d")
    week_ago = (now_cst() - timedelta(days=7)).strftime("%Y-%m-%d")
    for qtype, tag in ((1, "行业研报"), (0, "个股研报")):
        try:
            j = http_json(
                "https://reportapi.eastmoney.com/report/list",
                {
                    "industryCode": "*",
                    "pageSize": half,
                    "industry": "*",
                    "rating": "*",
                    "ratingChange": "*",
                    "beginTime": week_ago,
                    "endTime": today,
                    "pageNo": 1,
                    "qType": qtype,
                },
            )
            for it in j.get("data") or []:
                title = (it.get("title") or "").strip()
                if not title:
                    continue
                org = it.get("orgSName") or ""
                subj = (
                    "%s %s" % (it.get("stockCode") or "", it.get("stockName") or "")
                    if qtype == 0
                    else (it.get("industryName") or "")
                )
                pd_ = it.get("publishDate") or ""
                out.append(
                    {
                        "id": "report:%s" % it.get("infoCode"),
                        "ts": pd_[:16],
                        "title": ("[%s·%s] %s" % (tag, org, title))[:80],
                        "content": ("%s：%s" % (subj, title))[:300],
                    }
                )
        except Exception:
            continue  # 豁免（C2批2）：研报单条解析坏条跳过，正常容错
    return out


# 全球开源情报 RSS（策展参照 koala73/worldmonitor 源目录 _feeds.ts，
# AGPL-3.0 公开清单；按本机可达性实测筛选，Google News 包装源 CN 不可达已剔除）
WORLD_FEEDS = [
    ("CNBC", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    ("YahooFin", "https://finance.yahoo.com/news/rssindex"),
    ("SeekingAlpha", "https://seekingalpha.com/market_currents.xml"),
    ("SEC", "https://www.sec.gov/news/pressreleases.rss"),
    ("Cointelegraph", "https://cointelegraph.com/rss"),
    ("Decrypt", "https://decrypt.co/feed"),
    ("OilPrice", "https://oilprice.com/rss/main"),
    ("MiningCom", "https://www.mining.com/feed/"),
]


def fetch_world(size):
    """全球开源情报（市场快讯/官方监管/加密/商品 四组 RSS 直连）。

    单源失败自动跳过；条目 id 稳定（源名+guid 哈希）入库去重。
    英文源命中中文自选股/关键词概率低，主要价值：入库存档 +
    日报聚合 + 后续情绪/情报研究取数。"""
    import hashlib
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime

    atom = "{http://www.w3.org/2005/Atom}"
    out = []
    per = max(size // len(WORLD_FEEDS), 5) if size else 8
    for name, url in WORLD_FEEDS:
        try:
            r = requests.get(url, headers=UA, timeout=12)
            r.raise_for_status()
            root = ET.fromstring(r.content)  # nosec B314 - RSS 固定源，非任意用户输入
            items = root.findall(".//item") or root.findall(".//%sentry" % atom)
            got = 0
            for it in items:
                title = (
                    it.findtext("title") or it.findtext(atom + "title") or ""
                ).strip()
                if not title:
                    continue
                ln = it.find("link")
                link = (
                    (ln.text or ln.get("href") or "").strip() if ln is not None else ""
                )
                guid = it.findtext("guid") or link or title
                pd = (
                    it.findtext("pubDate")
                    or it.findtext(atom + "updated")
                    or it.findtext(atom + "published")
                    or ""
                )
                ts = ""
                if pd:
                    try:
                        dt = parsedate_to_datetime(pd)
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        ts = dt.astimezone(CST).strftime("%Y-%m-%d %H:%M")
                    except Exception:
                        dt = parse_full_time(pd)  # Atom 使用 ISO 8601 时间。
                        if dt is not None:
                            ts = dt.strftime("%Y-%m-%d %H:%M")
                desc = strip_html(
                    it.findtext("description")
                    or it.findtext(atom + "summary")
                    or it.findtext(atom + "content")
                    or ""
                )
                hid = hashlib.md5(("%s|%s" % (name, guid)).encode("utf-8")).hexdigest()[
                    :16
                ]
                out.append(
                    {
                        "id": "world:%s:%s" % (name.lower(), hid),
                        "ts": ts,
                        "title": ("[%s] %s" % (name, title))[:80],
                        "content": desc[:300],
                    }
                )
                got += 1
                if got >= per:
                    break
        except Exception:
            continue  # 豁免（C2批2）：docstring 声明——单源失败自动跳过
        if len(out) >= size:
            break
    return out


# ── K-ORG-207 政策权威源（2026-10-19 实测探测后注册，探测脚本放 %TEMP% 不入仓）──
# 口径澄清：sources.yaml（zerobound-kg/ingestion/conf/）是 KG 采集域权威清单，
# 新闻采集源权威 = 本文件 SOURCES + news_config.json，两者互不替代，本单不动 sources.yaml。
# 探测结论（HTTP 200 且返回可解析 XML/JSON 才注册，timeout=5）：
#   注册 2 源——govpolicy 中国政府网政策文件库 JSON API（国务院文件，最权威，
#   实测 200 + 2026 当月文件）；chinanews 中国新闻网滚动+财经双 RSS（国家通讯社
#   中新社口径，实测 200 XML 且带 RFC822 时间）。
# 落选如实记录——gov.cn/rss/zhengce.xml 等旧 RSS 404（政策库 API 顶上）；
# 新华网/人民网 RSS 返回 200 但无 pubDate 或整包停更（时间不可解析，注册即空转）；
# 发改委 RSS 200 但 0 条；央行官网无 RSS（跳过）；财联社电报泛资讯不入权威白名单。
GOV_POLICY_API = (
    "https://sousuo.www.gov.cn/search-gov/data"
    "?t=zhengcelibrary_gw&q=&timetype=timeqb&sort=score&sortType=1"
    "&searchfield=title&pcodeJiguan=&childtype=&subchildtype="
    "&tsbq=&pubtimeyear=&puborg=&pcodeYear=&pcodeNum=&filetype="
    "&page=1&n=20&inpro=&bmfl=&dup=&orpro=&type=zhengcelibrary"
)

CN_FEEDS = [
    ("scroll", "https://www.chinanews.com.cn/rss/scroll-news.xml"),
    ("finance", "https://www.chinanews.com.cn/rss/finance.xml"),
]


def fetch_govpolicy(size):
    """中国政府网政策文件库（国务院文件检索 API，ptime 毫秒 → 北京时间）。"""
    j = http_json(GOV_POLICY_API, timeout=12)
    out = []
    for it in ((j.get("searchVO") or {}).get("listVO")) or []:
        title = strip_html(it.get("title") or "")
        if not title:
            continue
        ts = ""
        if it.get("ptime"):
            try:
                ts = datetime.fromtimestamp(int(it["ptime"]) / 1000, CST).strftime(
                    "%Y-%m-%d %H:%M"
                )
            except Exception:
                ts = ""  # 时间不可解析交由 insert_items 丢弃（不造采集时间）
        org = (it.get("puborg") or "").strip()
        out.append(
            {
                "id": "govpol:%s" % (it.get("code") or it.get("id") or title[:40]),
                "ts": ts,
                "title": ("[%s] %s" % (org, title) if org else title)[:80],
                "content": (strip_html(it.get("summary") or "") or title)[:300],
            }
        )
        if len(out) >= size:
            break
    return out


def fetch_chinanews(size):
    """中国新闻网（中新社）滚动+财经双 RSS，guid 哈希入库去重。

    feed 的 RFC822 pubDate 无时区后缀，按北京时间处理（中新社发布口径）；
    单 feed 失败自动跳过，不拖垮整源。
    """
    import hashlib
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime

    out, seen = [], set()
    per = max(size // len(CN_FEEDS), 5) if size else 8
    for tag, url in CN_FEEDS:
        try:
            r = requests.get(url, headers=UA, timeout=12)
            r.raise_for_status()
            root = ET.fromstring(r.content)  # nosec B314 - RSS 固定源，非任意用户输入
            got = 0
            for it in root.findall(".//item"):
                title = (it.findtext("title") or "").strip()
                if not title:
                    continue
                guid = (it.findtext("guid") or it.findtext("link") or title).strip()
                if guid in seen:  # 滚动/财经两 feed 交叉条目去重
                    continue
                seen.add(guid)
                ts = ""
                pd = (it.findtext("pubDate") or "").strip()
                if pd:
                    try:
                        dt = parsedate_to_datetime(pd)
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=CST)
                        ts = dt.astimezone(CST).strftime("%Y-%m-%d %H:%M")
                    except Exception:
                        ts = ""
                desc = strip_html(it.findtext("description") or "")
                hid = hashlib.md5(guid.encode("utf-8")).hexdigest()[:16]
                out.append(
                    {
                        "id": "cns:%s:%s" % (tag, hid),
                        "ts": ts,
                        "title": title[:80],
                        "content": desc[:300],
                    }
                )
                got += 1
                if got >= per:
                    break
        except Exception:
            continue  # 单 feed 失败自动跳过（与 world 源同待遇）
        if len(out) >= size:
            break
    return out


SOURCES = {
    "sina": fetch_sina,
    "eastmoney": fetch_eastmoney,
    "wallstreetcn": fetch_wallstreetcn,
    "announcement": fetch_announcement,
    "survey": fetch_survey,
    "report": fetch_report,
    "world": fetch_world,
    "govpolicy": fetch_govpolicy,
    "chinanews": fetch_chinanews,
}


# ── 存储 ──────────────────────────────────────────────


def init_db():
    db = sqlite3.connect(DB)
    db.executescript("""
    CREATE TABLE IF NOT EXISTS news (
      id TEXT PRIMARY KEY, source TEXT, ts TEXT, title TEXT, content TEXT,
      hit INTEGER DEFAULT 0, pushed INTEGER DEFAULT 0, digested INTEGER DEFAULT 0,
      created_at TEXT);
    CREATE TABLE IF NOT EXISTS runs (
      id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, ts TEXT,
      ok INTEGER, new_count INTEGER, push_count INTEGER, detail TEXT);
    """)

    # 自选股关联标注 + JEV 判定列（老库平滑升级，幂等）
    def _add_col(col):
        # 2026-10-16 审计：仅豁免 duplicate column（幂等预期异常）；
        # 其余 OperationalError（如 locked）原 pass 会吞真故障，上抛
        try:
            db.execute("ALTER TABLE news ADD COLUMN %s TEXT" % col)
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise

    _add_col("stocks")
    _add_col("jev")
    db.commit()
    return db


def insert_items(db, items, source):
    """插入并返回真正新入库的条目（rowcount 判定，不做位置假设）"""
    fresh = []
    for it in items:
        available = now_cst()
        event = parse_news_time(it.get("ts"), reference=available, source=source)
        if event is None:
            log("unresolved news timestamp id=%s source=%s" % (it["id"], source))
            continue
        normalized = dict(it, ts=event.strftime("%Y-%m-%d %H:%M"))
        c = db.execute(
            "INSERT OR IGNORE INTO news(id,source,ts,title,content,created_at) VALUES(?,?,?,?,?,?)",
            (
                normalized["id"],
                source,
                normalized["ts"],
                normalized["title"],
                normalized["content"],
                available.strftime("%Y-%m-%d %H:%M"),
            ),
        )
        if c.rowcount > 0:
            fresh.append(dict(normalized, source=source))
    db.commit()
    return fresh


def write_status(**kw):
    st = {}
    if os.path.exists(STATUS_PATH):
        try:
            with open(STATUS_PATH, "r", encoding="utf-8") as f:
                st = json.load(f)
        except Exception:
            st = {}
    st.update(kw)
    st["updated"] = now_cst().strftime("%Y-%m-%d %H:%M")
    tmp = STATUS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATUS_PATH)


def push_message(text):
    tmp = os.path.join(HERE, "outbox", "pending_msg.txt")
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    import subprocess

    r = subprocess.run(
        [sys.executable, os.path.join(HERE, "send_feishu.py"), tmp],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    out = (r.stdout or "").strip()
    log("push -> " + out)
    return out.startswith("SEND_OK")


# --- K266 桥接：新新闻批次落 txt → inbox/docs/pending（词云管线自动摄取） ---
# K-STOCK-414：原硬编码 C:\项目\老田的AI助手\...（旧机 E 盘），换机后 makedirs
# 每轮抛 FileNotFoundError → K266 词云桥接整体死亡且逐轮 FAIL。
# 改为按本仓库根（HERE 上级）推导，仓库迁到哪跟到哪。
K266_PENDING = os.path.join(os.path.dirname(HERE), "inbox", "docs", "pending")
K266_STATE = os.path.join(HERE, "k266_state.json")
K266_BATCH_CHARS = 5000  # 单批字符预算（M3 结构化截断 6000 字，留余量防丢条）


def export_k266(db, cfg):
    """新新闻 → 批次 txt 落 K266 inbox/pending（ingest→M3 tags→/kg/doc 词云）。

    水位线 k266_state.json 记 last_rowid，幂等；首跑只记水位不回灌历史。
    单条 content 截 500 字；返回写入文件数；异常由调用方豁免。
    """
    if cfg.get("k266_export") is False:
        return 0
    st = {}
    try:
        with open(K266_STATE, encoding="utf-8") as f:
            st = json.load(f)
    except Exception:
        st = {}
    if "last_rowid" not in st:  # 首跑：对齐当前水位，只导此后新新闻
        last = db.execute("SELECT MAX(rowid) FROM news").fetchone()[0] or 0
    else:
        last = int(st["last_rowid"])
    rows = db.execute(
        "SELECT rowid, ts, source, title, content FROM news "
        "WHERE rowid > ? ORDER BY rowid",
        (last,),
    ).fetchall()
    files, batch, cur = 0, [], 0

    def _flush():
        nonlocal files, batch, cur
        if not batch:
            return
        files += 1
        fp = os.path.join(
            K266_PENDING,
            "news_%s_%d.txt" % (now_cst().strftime("%Y%m%d_%H%M%S"), files),
        )
        os.makedirs(K266_PENDING, exist_ok=True)
        with open(fp, "w", encoding="utf-8") as f:
            f.write("\n".join(batch))
        batch, cur = [], 0

    for rid, ts, src, title, content in rows:
        item = "[%s][%s] %s\n%s" % (ts, src, title or "", (content or "")[:500])
        if batch and cur + len(item) > K266_BATCH_CHARS:
            _flush()
        batch.append(item)
        cur += len(item)
        last = rid
    _flush()
    tmp = K266_STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(
            {"last_rowid": last, "updated": now_cst().strftime("%Y-%m-%d %H:%M")}, f
        )
    os.replace(tmp, K266_STATE)
    return files


def build_card_digest(shown, judges, total, intel_url):
    """K-ORG-207 B3：速报卡片正文改 top3 摘要行（用户抱怨卡片塞 6 条长标记不营养）。

    排序：自选股命中的条目优先（stable 排序，同组保持采集顺序）；
    每条一行「[股票tags] HH:MM 标题(截40字) + JEV 短标」——JEV 只留
    ↗利多/↘利空/→中性 + 冲击档位，百分比/范围长串去掉；
    尾行指向新闻情报台页（完整命中/政策/舆情三分栏视图），
    K43-c 判例：markdown 超链接，不做内网按钮。
    纯文本回退分支与群同步仍用 lines 全量（push_max_per_run cap=6
    语义不变，JEV 判定范围不受本函数影响）。
    """
    top3 = sorted(shown, key=lambda it: 0 if it.get("stocks") else 1)[:3]
    lines = []
    for it in top3:
        tag = ("【%s】" % "/".join(it["stocks"])) if it.get("stocks") else ""
        j = judges.get(it["id"])
        mark = ""
        if j:
            arrow = {"bull": "↗利多", "bear": "↘利空"}.get(j["bias"], "→中性")
            mark = " %s·冲击%s" % (arrow, j.get("impact_cn", "未知"))
        lines.append(
            "· %s%s %s%s" % (tag, it["ts"][-5:] or "----", it["title"][:40], mark)
        )
    lines.append("完整 %d 条 · 政策 · 舆情 → [新闻情报台](%s)" % (total, intel_url))
    return lines


def main():
    dry = "--dry" in sys.argv
    # 2026-10-25 SEC 收口（EXEC-R14）：敏感字段（jev_doc_url 等）读 env/本地私有覆盖
    from config_local import load_news_config

    cfg = load_news_config()
    size = int(cfg.get("collect_page_size", 30))
    macro_terms = list(cfg.get("watch_keywords", [])) + list(
        cfg.get("watch_stocks", [])
    )
    # 自选股：名称 + 别名 → 标准名（一只票多个别名只标一次）
    stock_terms = []
    for w in cfg.get("watchlist", []):
        nm = (w.get("name") or "").strip()
        if not nm:
            continue
        for term in [nm] + list(w.get("aliases", [])):
            term = (term or "").strip()
            if term:
                stock_terms.append((term, nm))
    db = init_db()

    source_status, new_total, all_new = {}, 0, []
    for name, fn in SOURCES.items():
        try:
            items = fn(size)
            fresh = insert_items(db, items, name)
            source_status[name] = {"ok": True, "got": len(items), "new": len(fresh)}
            new_total += len(fresh)
            all_new.extend(fresh)
        except Exception as e:
            source_status[name] = {"ok": False, "err": str(e)[:120]}
            log("source %s FAIL %s" % (name, str(e)[:120]))

    # 关注词/自选股命中（仅本次新入库的）
    hits = []
    for it in all_new:
        text = it["title"] + it["content"]
        names = sorted({nm for term, nm in stock_terms if term_in(term, text)})
        if names or any(k and k in text for k in macro_terms):
            it["stocks"] = names
            hits.append(it)

    push_count = 0
    if hits and not dry:
        cap = int(cfg.get("push_max_per_run", 6))
        shown = hits[:cap]
        # JEV 逐条判断市场影响（失败静默跳过，不影响推送）
        judges = {}
        try:
            import news_ai

            judges = news_ai.jev_judge_batch(shown)
        except Exception as e:
            log("jev_judge_batch FAIL %s" % str(e)[:100])
        lines = ["⚡ 金融新闻速报（%d 条命中）" % len(hits)]
        for it in shown:
            tag = ("【%s】" % "/".join(it["stocks"])) if it.get("stocks") else ""
            j = judges.get(it["id"])
            mark = (
                (
                    " [JEV:%s%02d%%·%s·冲击%s]"
                    % (
                        j["bias_cn"],
                        round(j["conf"] * 100),
                        j["scope_cn"],
                        j.get("impact_cn", "未知"),
                    )
                )
                if j
                else ""
            )
            lines.append(
                "· %s%s %s%s" % (tag, it["ts"][-5:] or "----", it["title"][:60], mark)
            )
        if len(hits) > cap:
            lines.append("…另有 %d 条见日报" % (len(hits) - cap))
        if judges:
            n_bull = sum(1 for j in judges.values() if j["bias"] == "bull")
            n_bear = sum(1 for j in judges.values() if j["bias"] == "bear")
            lines.append(
                "JEV 批次判定：%d 利多 / %d 利空 / %d 中性"
                % (n_bull, n_bear, len(judges) - n_bull - n_bear)
            )
        judge = None  # AI 即时市场判断（失败静默，不影响推送）
        try:
            import news_ai

            judge = news_ai.quick_judge(cfg.get("ai_model", "qwen3.5:9b"), shown)
        except Exception as e:
            log("quick_judge import FAIL %s" % str(e)[:100])
        if judge:
            lines.append("🤖 AI 判断：%s" % judge)
        lines.append("—— %s · 信息采集域" % now_cst().strftime("%H:%M"))
        text = "\n".join(lines)
        sent = False
        card = None
        try:
            from card_build import build_card, push_card

            doc = cfg.get("doc_url", "")
            # K-ORG-207 B3：卡片正文改 top3 摘要（stocks 优先 + JEV 短标截断），
            # 完整命中/政策/舆情转新闻情报台页；JEV 批次汇总行与 AI 判断行
            # 原样保留（从 lines 滤掉条目行取回），footer 由 build_card 提供；
            # 纯文本回退（lines 全量 6 条）不动，群同步不受影响。
            card_lines = build_card_digest(
                shown,
                judges,
                len(hits),
                cfg.get("intel_page_url", "http://127.0.0.1:8734/news_intel.html"),
            )
            md = "\n".join(
                card_lines
                + [
                    l
                    for l in lines[1:-1]
                    if not l.startswith("· ") and not l.startswith("…")
                ]
            )
            # K43-c 判例：内网按钮/二维码手机不可达，一律不进卡；
            # 2.0 卡按钮正解 = markdown 超链接（K42-d 测试卡 F：v1 action 被 400 拒）
            links = []
            if doc:
                links.append("[📄 详情文档](%s)" % doc)
            if cfg.get("jev_doc_url"):
                links.append("[⚖️ JEV 判断](%s)" % cfg["jev_doc_url"])
            if links:
                md += "\n\n" + " · ".join(links)
            card = build_card(
                "⚡ 金融新闻速报",
                "%d 条命中 · %s" % (len(hits), now_cst().strftime("%H:%M")),
                "orange",
                [md],
                footer="关键词/自选股命中 · AI 判断本地生成 · 信息采集域",
                use_schema2=True,
            )
            # 2026-10-25 P2：统一分装（默认发新版；PUSH_UNIFIED=0 或
            # push_unified.json shadow=true 时保持旧卡并只记双跑）
            card_to_send = card
            try:
                import os as _os

                _portal = _os.path.join(
                    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                    "portal",
                )
                import sys as _sys

                if _portal not in _sys.path:
                    _sys.path.insert(0, _portal)
                import feishu_push as _fp

                _send_new, _shadow = _fp.should_unify("news")
                if _send_new or _shadow:
                    _uni = _fp.from_sections(
                        "news",
                        "news",
                        "金融新闻速报",
                        card_lines
                        + [
                            l
                            for l in lines[1:-1]
                            if not l.startswith("· ") and not l.startswith("…")
                        ],
                        ts=now_cst().strftime("%Y-%m-%d %H:%M"),
                        source="信息采集域",
                        footer="关键词/自选股命中 · AI 判断本地生成",
                        links=[
                            x
                            for x in [
                                ("📄 详情文档", doc) if doc else None,
                                ("⚖️ JEV 判断", cfg.get("jev_doc_url") or "")
                                if cfg.get("jev_doc_url")
                                else None,
                            ]
                            if x
                        ],
                        buttons=[("📱 移动端总览（H5）", _h5_entry(), "primary")]
                        if _h5_entry()
                        else None,
                        level="news",
                    )
                    if _shadow:
                        _fp.shadow_log("news", card, _uni)
                    elif _send_new:
                        card_to_send = _uni
            except Exception as _ue:  # noqa: BLE001
                log("unified card FAIL -> legacy: %s" % str(_ue)[:100])
            sent = push_card(card_to_send)
            if not sent:
                log("card FAIL -> text fallback")
        except Exception as e:
            log("card build FAIL %s" % str(e)[:120])
        if not sent:
            sent = push_message(text)
        # K-STOCK-355 金融新闻速报同步到内部测试群（个人照常收，群另发一份；
        # 卡失败降级文本，群同步失败不影响个人推送）
        try:
            from send_feishu import send_content_to

            if card is not None:
                g_sent = send_content_to(
                    TEST_GROUP_ID,
                    "chat_id",
                    json.dumps(card, ensure_ascii=False),
                    "interactive",
                )
            else:
                g_sent = False
            if not g_sent:
                g_sent = send_content_to(
                    TEST_GROUP_ID,
                    "chat_id",
                    json.dumps({"text": text}, ensure_ascii=False),
                    "text",
                )
            log(
                "speed-report test-group(%s) -> %s"
                % (TEST_GROUP_ID[:10], "SEND_OK" if g_sent else "SEND_FAIL")
            )
        except Exception as e:
            log("test-group sync FAIL %s" % str(e)[:100])
        if sent:
            push_count = len(hits)
            for it in hits:
                db.execute(
                    "UPDATE news SET hit=1, pushed=1, stocks=? WHERE id=?",
                    (json.dumps(it.get("stocks", []), ensure_ascii=False), it["id"]),
                )
            for nid, j in judges.items():  # JEV 判断结果落库（晨报汇总用）
                db.execute(
                    "UPDATE news SET jev=? WHERE id=?",
                    (json.dumps(j, ensure_ascii=False), nid),
                )
            db.commit()

    ok = any(v.get("ok") for v in source_status.values())
    db.execute(
        "INSERT INTO runs(kind,ts,ok,new_count,push_count,detail) VALUES(?,?,?,?,?,?)",
        (
            "collect",
            now_cst().strftime("%Y-%m-%d %H:%M"),
            int(ok),
            new_total,
            push_count,
            json.dumps(source_status, ensure_ascii=False)[:400],
        ),
    )
    db.commit()
    row = db.execute(
        "SELECT COUNT(*) FROM news WHERE created_at>=?",
        ((now_cst() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M"),),
    ).fetchone()
    total = db.execute("SELECT COUNT(*) FROM news").fetchone()[0]
    write_status(
        last_collect=now_cst().strftime("%Y-%m-%d %H:%M"),
        last_ok=bool(ok),
        new_last_run=new_total,
        push_last_run=push_count,
        news_24h=row[0],
        news_total=total,
        sources=source_status,
    )
    # K266 桥接：新新闻批次落 pending，词云管线 5 分钟内自动摄取（豁免：不阻采集）
    try:
        n_k266 = export_k266(db, cfg)
        if n_k266:
            log("k266 export %d file(s) -> inbox/docs/pending" % n_k266)
    except Exception as e:
        log("k266 export FAIL %s" % str(e)[:120])
    db.close()
    log(
        "collect done ok=%s new=%d hits=%d pushed=%d"
        % (ok, new_total, len(hits), push_count)
    )
    print(
        "COLLECT %s new=%d hits=%d pushed=%d" % (ok, new_total, len(hits), push_count)
    )
    # K-STOCK-393（20261015 告警补盲）：ok 必须传到退出码——审计实证
    # 全源失败 rc 恒 0，start_news_collect.ps1 的 alert_fail_wrap
    # （rc!=0 才触发）永不告警，"所有上游全挂"静默。
    return bool(ok)


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
