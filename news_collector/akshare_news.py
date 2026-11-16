# -*- coding: utf-8 -*-
"""akshare_news.py — 文本语料三源采集：快讯 / 个股新闻 / 研报（CR-20261116-03）

三源与落盘（OUT 口径与 irm/sse/taoguba 相同：dirname(HERE)/data/）：
- flash    ak.stock_news_global_cls()（1.20.41 前名 stock_info_global_cls）
           → data/flash/flash_YYYYMMDD.jsonl
           全市场快讯**无个股归因维度**，不进 gap_scan 个股分母（注册会制造
           全池假 MISSING，与"不适用不能表现为故障"同族），以源级自报代之。
- aknews   东财个股新闻 → data/aknews/{code6}.jsonl
           直连 search-api-web.eastmoney.com（与 ak.stock_news_em 同源同端点）。
           不直接调 ak.stock_news_em 的原因（2026-11-16 判例）：本机
           pandas 3.0.1 + pyarrow 26.0 下 akshare 内部 str.replace 委托
           pyarrow/RE2，``\\u`` 转义不认 → ArrowInvalid（1.18.40/1.20.65 实测
           双双命中）。请求层照抄 akshare 头信息，后处理纯 Python。
- research ak.stock_research_report_em(symbol) → data/research/{code6}.jsonl
           注意：该接口每票固定 8 页分页（约 8 请求/票），批量跑前先算请求量。
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(os.path.dirname(HERE), "data")
DIR_FLASH = os.path.join(DATA, "flash")
DIR_AKNEWS = os.path.join(DATA, "aknews")
DIR_RESEARCH = os.path.join(DATA, "research")

SLEEP_S = 1.2          # 个股请求间隔
FAIL_CIRCUIT = 3       # 单段连续失败熔断（batch_collect 同判据）

_EM_HEADERS = {
    "accept": "*/*",
    "accept-encoding": "gzip, deflate, br, zstd",
    "accept-language": "en,zh-CN;q=0.9,zh;q=0.8",
    "cache-control": "no-cache",
    "connection": "keep-alive",
    "host": "search-api-web.eastmoney.com",
    "pragma": "no-cache",
    "referer": "https://so.eastmoney.com/news/s?keyword={kw}",
    "sec-ch-ua": '"Chromium";v="142", "Google Chrome";v="142", "Not_A Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "script",
    "sec-fetch-mode": "no-cors",
    "sec-fetch-site": "same-site",
    "user-agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/142.0.0.0 Safari/537.36"),
}

_TAG_RE = re.compile(r"</?em>")


def strip_em(s):
    """东财搜索结果里的 <em>关键词高亮</em> 标记剥除。"""
    return _TAG_RE.sub("", s or "")


def norm_code(c):
    """6 位 A 股代码校验；非法返回 ''（fail-loud 交给调用方退出码 2）。"""
    c = str(c or "").strip()
    return c if len(c) == 6 and c.isdigit() else ""


def _row_key(row, key_fields):
    return json.dumps([str(row.get(k) or "") for k in key_fields],
                      ensure_ascii=False)


def append_jsonl(path, rows, key_fields):
    """append-on-new：按 key_fields 去重后追加，返回新增条数。文件损坏不静默清空。"""
    seen = set()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for ln in fh:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    seen.add(_row_key(json.loads(ln), key_fields))
                except Exception:
                    continue          # 单行坏不拖垮整文件
    os.makedirs(os.path.dirname(path), exist_ok=True)
    n_new = 0
    with open(path, "a", encoding="utf-8") as fh:
        for r in rows:
            k = _row_key(r, key_fields)
            if k in seen:
                continue
            seen.add(k)
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            n_new += 1
    return n_new


# ---------------------------------------------------------------- flash
def fetch_flash():
    """财联社电报（全局快讯）。返回行列表；字段名随 akshare 版本可能变，缺列报错。"""
    import akshare as ak
    df = ak.stock_news_global_cls()
    cols = set(df.columns)
    need = {"标题", "内容", "发布日期", "发布时间"}
    missing = need - cols
    if missing:
        raise RuntimeError("cls 接口缺列: %s（akshare 改版，需适配）" % sorted(missing))
    rows = []
    for _, r in df.iterrows():
        d, t = r["发布日期"], r["发布时间"]
        ts = "%s %s" % (d, t) if t else str(d)
        rows.append({"ts": ts, "title": strip_em(str(r["标题"])),
                     "content": strip_em(str(r["内容"]))})
    return rows


# ---------------------------------------------------------------- aknews
_EM_HTTP = None          # 缓存 akshare 内部 http 模块（curl_cffi 指纹）
_EM_HTTP_TRIED = False


def _em_http():
    """借 akshare 内部 http.curl_get（curl_cffi TLS 指纹）。

    实测（2026-11-16）：裸 requests 打同一端点被反爬降级——result 里只有
    passportWeb、拿不到 cmsArticleWebOld；akshare 的 curl_get 正常返回 10 条。
    故请求层必须走 akshare；其 pandas 后处理才是 ArrowInvalid 崩点，已绕开。
    """
    global _EM_HTTP, _EM_HTTP_TRIED
    if _EM_HTTP_TRIED:
        return _EM_HTTP
    _EM_HTTP_TRIED = True
    try:
        import importlib.util
        import inspect
        import sys
        import akshare
        f = inspect.getfile(akshare.stock_news_em)
        spec = importlib.util.spec_from_file_location("_aksne_http", f)
        m = importlib.util.module_from_spec(spec)
        sys.modules.setdefault("_aksne_http", m)
        spec.loader.exec_module(m)
        _EM_HTTP = m.http
    except Exception as e:
        print("em 请求层降级为 requests（akshare http 模块加载失败: %s）" % str(e)[:80])
        _EM_HTTP = None
    return _EM_HTTP


def fetch_em_news(code6, page_size=10):
    """东财个股新闻（直连搜索端点，与 ak.stock_news_em 同源）。"""
    import requests
    inner = {
        "uid": "", "keyword": code6, "type": ["cmsArticleWebOld"],
        "client": "web", "clientType": "web", "clientVersion": "curr",
        "param": {"cmsArticleWebOld": {
            "searchScope": "default", "sort": "default", "pageIndex": 1,
            "pageSize": page_size, "preTag": "<em>", "postTag": "</em>"}},
    }
    params = {"cb": "jQuery1", "param": json.dumps(inner, ensure_ascii=False)}
    headers = dict(_EM_HEADERS)
    headers["referer"] = headers["referer"].format(kw=code6)
    http = _em_http()
    if http is not None:
        r = http.curl_get("https://search-api-web.eastmoney.com/search/jsonp",
                          params=params, headers=headers)
    else:
        r = requests.get("https://search-api-web.eastmoney.com/search/jsonp",
                         params=params, headers=headers, timeout=20)
    t = r.text
    i, j = t.index("("), t.rindex(")")
    d = json.loads(t[i + 1:j])
    res = d.get("result") or {}
    arts = res.get("cmsArticleWebOld") or []
    if not arts and "passportWeb" in res:
        # 反爬降级形态：搜到的是用户/社区而非新闻——必须报错，不能当"无新闻"落盘
        raise RuntimeError("em 搜索端点降级(反爬)，未获 cmsArticleWebOld")
    rows = []
    for a in arts:
        rows.append({
            "ts": str(a.get("date") or ""),
            "title": strip_em(str(a.get("title") or "")),
            "content": strip_em(str(a.get("content") or ""))[:500],
            "source": str(a.get("mediaName") or ""),
            "url": "http://finance.eastmoney.com/a/%s.html" % (a.get("code") or ""),
        })
    return rows


# ---------------------------------------------------------------- research
def fetch_research(code6):
    """东财研报目录（每票固定 8 页分页，约 8 请求）。"""
    import akshare as ak
    df = ak.stock_research_report_em(symbol=code6)
    need = {"报告名称", "机构", "东财评级", "日期", "报告PDF链接"}
    missing = need - set(df.columns)
    if missing:
        raise RuntimeError("research 接口缺列: %s（akshare 改版，需适配）" % sorted(missing))
    rows = []
    for _, r in df.iterrows():
        rows.append({
            "ts": str(r["日期"]),
            "title": strip_em(str(r["报告名称"])),
            "org": str(r["机构"]),
            "rating": str(r["东财评级"]),
            "pdf": str(r["报告PDF链接"]),
        })
    return rows


def _fmt_flash_report(rows, n_new):
    today = datetime.now().strftime("%Y-%m-%d")
    last = max((r["ts"] for r in rows), default="-")
    return "flash: 行=%d 新增=%d 最新=%s → flash_%s.jsonl" % (
        len(rows), n_new, last, today.replace("-", ""))


def main(argv=None):
    ap = argparse.ArgumentParser(description="akshare 文本语料三源采集")
    ap.add_argument("--sources", default="flash,aknews,research")
    ap.add_argument("--codes", default="", help="逗号分隔 6 位代码（aknews/research 用）")
    ap.add_argument("--codes-file", default="", help="每行一个代码；CRLF/空白自动剥")
    ap.add_argument("--quota", type=int, default=200,
                    help="本次个股请求上限（research 每票计 8）")
    a = ap.parse_args(argv)

    srcs = [s.strip() for s in a.sources.split(",") if s.strip()]
    bad = [s for s in srcs if s not in ("flash", "aknews", "research")]
    if bad:
        print("未知源: %s（可用 flash/aknews/research）" % bad)
        return 2

    codes = []
    if a.codes:
        codes += a.codes.split(",")
    if a.codes_file:
        with open(a.codes_file, encoding="utf-8") as fh:
            codes += [ln.strip() for ln in fh if ln.strip()]
    codes = [norm_code(c) for c in codes if norm_code(c)]
    raw_n = len(a.codes.split(",")) if a.codes else 0
    if a.codes and len(codes) < len([c for c in a.codes.split(",") if c.strip()]):
        print("存在非法代码，已拒绝整批（fail-loud）")
        return 2
    if not flash_ok(srcs, codes):
        return 2

    rc = 0
    budget = a.quota

    # ---- flash：一次拉取，源级自报
    if "flash" in srcs:
        try:
            rows = fetch_flash()
            p = os.path.join(DIR_FLASH,
                             "flash_%s.jsonl" % datetime.now().strftime("%Y%m%d"))
            n = append_jsonl(p, rows, ["ts", "title"])
            print(_fmt_flash_report(rows, n))
        except Exception as e:
            print("flash FAIL: %s" % str(e)[:120])
            rc = rc or 1

    # ---- aknews / research：逐票
    for seg in ("aknews", "research"):
        if seg not in srcs:
            continue
        if not codes:
            print("%s: 未提供 --codes/--codes-file，跳过" % seg)
            continue
        out_dir = DIR_AKNEWS if seg == "aknews" else DIR_RESEARCH
        fetcher = fetch_em_news if seg == "aknews" else fetch_research
        cost = 1 if seg == "aknews" else 8
        keyf = ["url"] if seg == "aknews" else ["ts", "title", "org"]
        ok = fail = n_new_total = 0
        consec_fail = 0
        t0 = time.time()
        for i, c6 in enumerate(codes, 1):
            if budget < cost:
                print("[%s] %d/%d 配额耗尽（剩 %d 票未跑）" % (seg, i - 1, len(codes),
                                                            len(codes) - i + 1))
                break
            budget -= cost
            try:
                rows = fetcher(c6)
                p = os.path.join(out_dir, "%s.jsonl" % c6)
                n = append_jsonl(p, rows, keyf)
                ok += 1
                n_new_total += n
                consec_fail = 0
                print("[%s] %d/%d %s: %d 行 +%d" % (seg, i, len(codes), c6,
                                                    len(rows), n))
            except Exception as e:
                # akshare 研报接口对无研报覆盖的票（多为次新）在内部
                # big_df["infoCode"] 处抛 KeyError —— 这是「目录空」的内容事实，
                # 不是断供：按合法空处理，不计失败、不进熔断（2026-11-16 判例）。
                if seg == "research" and "infoCode" in str(e):
                    ok += 1
                    consec_fail = 0
                    print("[%s] %d/%d %s: 无研报覆盖(合法空)" % (
                        seg, i, len(codes), c6), flush=True)
                else:
                    fail += 1
                    consec_fail += 1
                    print("[%s] %d/%d %s: FAIL %s" % (seg, i, len(codes), c6,
                                                      str(e)[:80]), flush=True)
                    if consec_fail >= FAIL_CIRCUIT:
                        print("[%s] 连续失败 %d → 熔断停段" % (seg, consec_fail))
                        rc = 1
                        break
            if i < len(codes):
                time.sleep(SLEEP_S)
        print("[%s] DONE ok=%d fail=%d 新增=%d 耗时=%.0fs" % (
            seg, ok, fail, n_new_total, time.time() - t0))
    return rc


def flash_ok(srcs, codes):
    """flash 与个股无关：只跑 flash 时不需要代码；跑个股源则必须有代码。"""
    if set(srcs) - {"flash"} and not codes:
        print("aknews/research 需要 --codes 或 --codes-file（fail-loud，不空跑）")
        return False
    return True


if __name__ == "__main__":
    sys.exit(main())
