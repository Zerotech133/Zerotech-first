# -*- coding: utf-8 -*-
"""个股分析（飞书 Base 表单输入 → 全信息判断 → 回写 + 推卡）。

流程:
  表单/表格输入股票名 → 自选名单定位（名称/别名/ticker）
  → 新浪实时行情 + news.db 近 7 天关联新闻
  → 关联新闻逐条 JEV 结构化判断（方向/置信 + Ollama 依据）
  → 汇总多空统计 + 加权信号强度 → 综合倾向（利多/利空/中性/分歧）
  → 本地 Ollama 未来展望（80~150 字）
  → 回写 Base「个股分析」行（状态=完成）+ 飞书卡片推送

用法:
  python stock_analyze.py --stock 英伟达   # 直接分析并推卡（调试）
  python stock_analyze.py --poll          # 轮询 Base 待处理行（计划任务 5min）
"""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
import logging

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
DB = os.path.join(HERE, "news.db")
LOG = os.path.join(HERE, "logs", "analyze.log")


def _lark_exe():
    """lark-cli 解析：仓库 bin/ 固定副本优先（K-STOCK-396 解耦）；PATH
    次之；计划任务 PATH 无插件目录时按插件版本通配回退（trae-remote
    lark 插件 bin 路径带版本号会漂移）。"""
    _bin = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "bin",
        "lark-cli.exe",
    )
    if os.path.isfile(_bin):
        return _bin
    p = shutil.which("lark-cli")
    if p:
        return p
    import glob

    for pat in (
        os.path.expandvars(
            r"%USERPROFILE%\.trae-cn\plugins\trae-remote-official"
            r"\lark\*\bin\lark-cli.exe"
        ),
    ):
        m = sorted(glob.glob(pat))
        if m:
            return m[-1]
    return "lark-cli"


LARK = _lark_exe()


def log(msg):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write("[%s] %s\n" % (now_cst().strftime("%m-%d %H:%M:%S"), msg))


def _cfg():
    # 2026-10-25 SEC 收口（EXEC-R14）：敏感字段读 env/本地私有覆盖，见 config_local.py
    from config_local import load_news_config

    return load_news_config()


def now_cst():
    from datetime import datetime, timezone

    return datetime.now(timezone(timedelta(hours=8)))


def _lark(args, timeout=120):
    r = subprocess.run(
        [LARK] + args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
        cwd=HERE,
    )
    t = (r.stdout or "") + (r.stderr or "")
    i = t.find("{")
    return json.loads(t[i:]) if i >= 0 else {}


# ── 1) 股票定位 ────────────────────────────────────────────
def resolve_stock(q):
    """输入 → 条目 {name, code, aliases}；自选名单优先，
    未命中按 ticker/代码直通回退（美股字母 / A股6位 / 港股1-5位）。"""
    q2 = q.strip()
    ql = q2.lower()
    for e in _cfg().get("watchlist") or []:
        if q2 == e.get("name") or ql == e.get("code", "").lower():
            return e
        for a in [e.get("name")] + (e.get("aliases") or []):
            if a and ql == str(a).lower():
                return e
    for e in _cfg().get("watchlist") or []:  # 第二轮：ticker 尾缀匹配
        code = e.get("code", "").lower()
        for pref in ("gb_", "rt_hk", "sh", "sz", "bj"):
            if (
                code.startswith(pref)
                and ql
                and ql == code[len(pref) :].replace(".", "")
            ):
                return e
    # 回退：按代码形态构造（自选名单外也能查行情+新闻标题匹配）
    import re

    if re.fullmatch(r"[A-Za-z][A-Za-z.]{0,9}", q2):  # 美股 TSLA / BRK.B
        return {"name": q2.upper(), "code": "gb_" + ql, "aliases": [q2.upper()]}
    if re.fullmatch(r"\d{6}", q2):  # A股 600519
        pre = "sh" if q2[0] == "6" else ("bj" if q2[0] in "48" else "sz")
        return {"name": q2, "code": pre + q2, "aliases": []}
    if re.fullmatch(r"\d{1,5}", q2):  # 港股 0700
        return {"name": q2, "code": "rt_hk" + q2.zfill(5), "aliases": []}
    return None


# ── 2) 行情 ────────────────────────────────────────────────
def fetch_quote(code):
    """新浪行情；加密等无源返回 None。"""
    if "/" in code:  # BTC/USDT 等
        return None
    try:
        from quote_snapshot import fetch_quotes

        for q in fetch_quotes([code]):
            if q.get("price"):
                return q
    except Exception as e:
        log("quote FAIL %s" % str(e)[:100])
    return None


# ── 3) 关联新闻 ────────────────────────────────────────────
def related_news(name, aliases, hours=24 * 7):
    """近 7 天：stocks 标注命中 或 标题含名称/别名（≥2 字）。"""
    since = (now_cst() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M")
    db = sqlite3.connect(DB)
    rows = db.execute(
        "SELECT ts, title, stocks FROM news WHERE created_at>=? "
        "ORDER BY created_at DESC LIMIT 3000",
        (since,),
    ).fetchall()
    db.close()
    kws = {name} | {a for a in aliases if len(str(a)) >= 2}
    hits = []
    for ts, title, stocks_j in rows:
        if not title:
            continue
        try:
            tagged = name in (json.loads(stocks_j or "[]"))
        except Exception:
            tagged = False
        if tagged or any(k in title for k in kws):
            hits.append(
                {"id": ts + "|" + title[:20], "ts": ts, "title": title, "stocks": []}
            )
    return hits


# ── 4) 判断汇总 + 展望 ─────────────────────────────────────
def summarize(judges):
    """judges = {id: jev_dict} → (倾向, 信号强度, 统计文本)。"""
    vs = [j for j in judges.values() if j]
    if not vs:
        return "中性", 0, "近 7 天无关联新闻命中"
    bull = sum(1 for j in vs if j["bias"] == "bull")
    bear = sum(1 for j in vs if j["bias"] == "bear")
    neu = len(vs) - bull - bear
    score = sum(
        (1 if j["bias"] == "bull" else -1 if j["bias"] == "bear" else 0) * j["conf"]
        for j in vs
    ) / len(vs)
    if bull >= 2 and bear >= 2:
        bias = "分歧"
    elif score > 0.15:
        bias = "利多"
    elif score < -0.15:
        bias = "利空"
    else:
        bias = "中性"
    return (
        bias,
        round(score * 100),
        "利多 %d / 利空 %d / 中性 %d（共判 %d 条）" % (bull, bear, neu, len(vs)),
    )


def outlook(name, px_s, stat_s, news_lines):
    """本地 Ollama：80~150 字未来展望。失败返回占位文本。"""
    import requests
    import news_ai

    cfg = _cfg()
    news = "\n".join("· %s" % t for t in news_lines[:6]) or "（近7天无直接关联新闻）"
    try:
        r = requests.post(
            news_ai.OLLAMA,
            json={
                "model": cfg.get("ai_model", "qwen3.5:9b"),
                "messages": [
                    {
                        "role": "user",
                        "content": "你是量化分析师。基于以下信息给出「%s」短期（1-2周）展望，"
                        "80~150字：先给方向判断（偏多/偏空/震荡），再讲2-3条逻辑，"
                        "最后一句风险提示。不要列标题，直接输出正文。\n"
                        "现价：%s\nJEV 新闻判断汇总：%s\n关联新闻：\n%s"
                        % (name, px_s, stat_s, news),
                    }
                ],
                "stream": False,
                "think": False,
                "options": {"temperature": 0.4, "num_predict": 400},
            },
            timeout=120,
        )
        r.raise_for_status()
        return r.json()["message"]["content"].strip()[:300]
    except Exception as e:
        log("outlook FAIL %s" % str(e)[:100])
        return "（展望生成失败，参考 JEV 统计与关联新闻自行判断）"


# ── 5) Base 回写 + 卡片 ────────────────────────────────────
def update_record(rid, fields):
    d = _lark(
        [
            "base",
            "+record-batch-update",
            "--as",
            "user",
            "--base-token",
            _cfg().get("jev_base_token"),
            "--table-id",
            _cfg().get("sa_table_id"),
            "--json",
            json.dumps({"update_records": {rid: fields}}, ensure_ascii=False),
            "--format",
            "json",
        ]
    )
    if not d.get("ok"):
        log(
            "update_record FAIL %s %s"
            % (rid, json.dumps(d.get("error"), ensure_ascii=False)[:150])
        )
    return d


def push_card(
    name, bias, strength, n_news, px_s, stat_s, outlook_txt, judged, base_url
):
    tpl = {"利多": "green", "利空": "red"}.get(bias, "blue")
    jev_lines = [
        "· %s [JEV:%s%02d%%]%s"
        % (
            (j["title"] or "")[:46],
            j["bias_cn"],
            j["conf"],
            " %s" % j["reason"][:30] if j.get("reason") else "",
        )
        for j in judged[:3]
    ]
    md1 = "**行情**：%s\n**多空统计**：%s（近 7 天关联 %d 条）" % (px_s, stat_s, n_news)
    md2 = "**未来展望（1-2 周）**\n%s" % outlook_txt
    md3 = "**Top 关联新闻判断**\n%s" % ("\n".join(jev_lines) if jev_lines else "（无）")
    cfg = _cfg()
    card = {
        "config": {"update_multi": True, "width_mode": "default"},
        "header": {
            "title": {"tag": "plain_text", "content": "🎯 个股分析 · %s" % name},
            "subtitle": {
                "tag": "plain_text",
                "content": "综合倾向 %s · 信号 %+d · %s"
                % (bias, strength, now_cst().strftime("%m-%d %H:%M")),
            },
            "template": tpl,
        },
        "elements": [
            {
                "tag": "column_set",
                "flex_mode": "none",
                "columns": [
                    {
                        "tag": "column",
                        "width": "weighted",
                        "weight": 1,
                        "elements": [
                            {
                                "tag": "markdown",
                                "content": "<font color='grey'>综合倾向"
                                "</font>\n**%s**" % bias,
                            }
                        ],
                    },
                    {
                        "tag": "column",
                        "width": "weighted",
                        "weight": 1,
                        "elements": [
                            {
                                "tag": "markdown",
                                "content": "<font color='grey'>信号强度"
                                "</font>\n**%+d**" % strength,
                            }
                        ],
                    },
                    {
                        "tag": "column",
                        "width": "weighted",
                        "weight": 1,
                        "elements": [
                            {
                                "tag": "markdown",
                                "content": "<font color='grey'>关联新闻"
                                "</font>\n**%d**" % n_news,
                            }
                        ],
                    },
                ],
            },
            {"tag": "hr"},
            {"tag": "markdown", "content": md1},
            {"tag": "hr"},
            {"tag": "markdown", "content": md2},
            {"tag": "hr"},
            {"tag": "markdown", "content": md3},
            {"tag": "hr"},
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "🖼 分析记录"},
                        "type": "primary",
                        "url": "%s?table=%s" % (base_url, cfg.get("sa_table_id", "")),
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "📝 输入下一只"},
                        "type": "default",
                        "url": cfg.get("sa_form_url", ""),
                    },
                ],
            },
            {"tag": "hr"},
            {
                "tag": "markdown",
                "text_size": "notation",
                "content": "<font color='grey'>纸面研究 · 不构成投资建议 · "
                "%s</font>" % now_cst().strftime("%m-%d %H:%M"),
            },
        ],
    }
    from card_build import push_card as _push

    return _push(card)


# ── 6) 主分析流程 ──────────────────────────────────────────
def analyze_detail(q):
    """纯分析（不推卡不写库）→ 结果 dict；门户页与 analyze() 共用。"""
    e = resolve_stock(q)
    if not e:
        return {
            "ok": False,
            "q": q,
            "msg": "未识别「%s」。支持：英伟达 / TSLA / 600519 / 00700" % q,
        }
    name = e["name"]
    qte = fetch_quote(e.get("code", ""))
    px_s = (
        "%.4g（日 %+.2f%%）" % (qte["price"], qte["pct"])
        if qte and qte.get("pct") is not None
        else ("%.4g" % qte["price"] if qte else "暂无实时行情")
    )
    news = related_news(name, e.get("aliases") or [])
    judges = {}
    if news:
        try:
            import news_ai

            judges = news_ai.jev_judge_batch(news[:6])
        except Exception as ex:
            log("jev batch FAIL %s" % str(ex)[:100])
    bias, strength, stat_s = summarize(judges)
    out = outlook(name, px_s, stat_s, [h["title"] for h in news])
    judged = [(h, judges[h["id"]]) for h in news[:6] if judges.get(h["id"])]
    judged.sort(key=lambda x: -x[1]["conf"])
    return {
        "ok": True,
        "q": q,
        "name": name,
        "bias": bias,
        "strength": strength,
        "px": px_s,
        "stat": stat_s,
        "news_n": len(news),
        "outlook": out,
        "judged": [
            {
                "title": h["title"],
                "bias_cn": j["bias_cn"],
                "conf": round(j["conf"] * 100),
                "reason": j.get("reason", ""),
            }
            for h, j in judged[:3]
        ],
        "t": now_cst().strftime("%Y-%m-%d %H:%M"),
    }


def analyze(q, rid=None):
    """完整分析一股；rid 提供则回写 Base。"""
    cfg = _cfg()
    base_url = cfg.get("jev_base_url", "")
    try:
        d = analyze_detail(q)
        if not d.get("ok"):
            msg = d["msg"]
            if rid:
                update_record(
                    rid,
                    {
                        "状态": ["失败"],
                        "核心依据": msg,
                        "分析时间": now_cst().strftime("%Y-%m-%d %H:%M"),
                    },
                )
            log("resolve FAIL %s" % q)
            # 失败也推飞书反馈：否则提交端无任何响应（实测用户困惑点）
            try:
                from send_feishu import send_text

                send_text(
                    "⚠️ 个股分析失败\n%s\n\n可直接输入代码再试，"
                    "如 TSLA / 600519 / 00700" % msg
                )
            except Exception as e2:
                log("失败反馈推送 FAIL %s" % str(e2)[:100])  # C2批2
            return False
        name, bias, strength = d["name"], d["bias"], d["strength"]
        px_s, stat_s, out = d["px"], d["stat"], d["outlook"]
        core = (
            "；".join(
                "%s（%s%02d%%：%s）"
                % (j["title"][:24], j["bias_cn"], j["conf"], j["reason"][:24])
                for j in d["judged"][:2]
            )
            or stat_s
        )
        if rid:
            update_record(
                rid,
                {
                    "状态": ["完成"],
                    "综合倾向": [bias],
                    "信号强度": strength,
                    "现价": px_s,
                    "多空统计": stat_s,
                    "未来展望": out,
                    "核心依据": core[:500],
                    "关联新闻数": d["news_n"],
                    "分析时间": now_cst().strftime("%Y-%m-%d %H:%M"),
                },
            )
        ok = push_card(
            name, bias, strength, d["news_n"], px_s, stat_s, out, d["judged"], base_url
        )
        log(
            "analyze %s → %s %+d news=%d card=%s"
            % (name, bias, strength, d["news_n"], ok)
        )
        return ok
    except Exception as ex:
        log("analyze ERR %s | %s" % (q, str(ex)[:150]))
        if rid:
            update_record(
                rid,
                {
                    "状态": ["失败"],
                    "核心依据": str(ex)[:200],
                    "分析时间": now_cst().strftime("%Y-%m-%d %H:%M"),
                },
            )
        try:
            from send_feishu import send_text

            send_text("⚠️ 个股分析异常：%s\n%s" % (q, str(ex)[:150]))
        except Exception as e2:
            log("异常反馈推送 FAIL %s" % str(e2)[:100])  # C2批2
        return False


STATE = os.path.join(HERE, "sa_state.json")


def _recent_done(rid, minutes=30):
    """本地去重：Base 读副本可能滞后看到旧状态（实测重复分析过），
    同一 rid 30 分钟内不再处理。"""
    try:
        st = json.load(open(STATE, encoding="utf-8"))
    except Exception as e:
        log(
            "sa_state.json 读取失败，30 分钟去重失效（可能重复分析一次）: %s"
            % str(e)[:100]
        )  # C2批2
        return False
    ts = st.get(rid)
    if not ts:
        return False
    try:
        return (
            now_cst()
            - datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=now_cst().tzinfo
            )
        ) < timedelta(minutes=minutes)
    except Exception:
        return False  # 豁免（C2批2）：时间戳坏值→去重失效一次，容错


def _mark_done(rid):
    try:
        st = json.load(open(STATE, encoding="utf-8"))
    except Exception:
        st = {}
    st[rid] = now_cst().strftime("%Y-%m-%d %H:%M:%S")
    if len(st) > 500:  # 只留最近 500 个 rid
        st = dict(sorted(st.items(), key=lambda kv: kv[1], reverse=True)[:500])
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False)


def _list_rows(out="sa_poll.ndjson"):
    """record-list 一遍；返回 {rid: (股票, 状态文本)}。"""
    cfg = _cfg()
    r = subprocess.run(
        [
            LARK,
            "base",
            "+record-list",
            "--as",
            "user",
            "--base-token",
            cfg.get("jev_base_token"),
            "--table-id",
            cfg.get("sa_table_id"),
            "--limit",
            "50",
            "--format",
            "ndjson",
            "--output",
            out,
            "--overwrite",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        cwd=HERE,
    )
    rows = {}
    try:
        with open(os.path.join(HERE, out), encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    j = json.loads(ln)
                except Exception:
                    continue  # 豁免（C2批2）：ndjson 坏行跳过，正常容错
                rid = j.get("record_id")
                if not rid:
                    continue
                st = j.get("状态")
                rows[rid] = (
                    str(j.get("股票") or "").strip(),
                    st[0] if isinstance(st, list) and st else str(st or ""),
                )
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "news_collector/stock_analyze.py", exc)
    return rows


def poll_ndjson():
    """轮询待处理行。坑: Base 读副本按调用随机路由，可能读到旧数据
    （实测新行 5 分钟不可见/状态回写不可见）→ 双读合并，按 rid 取
    状态非空者（较新），本地 sa_state.json 兜底去重。"""
    import time

    a = _list_rows()
    if not a:
        print("SA poll pending=0")
        return 0
    time.sleep(6)
    b = _list_rows()
    for rid, (q, st) in b.items():
        if st and rid in a and not a[rid][1]:  # b 有状态而 a 空 → 取 b
            a[rid] = (q, st)
        elif rid not in a:
            a[rid] = (q, st)
    recs = [(rid, q) for rid, (q, st) in a.items() if q and st not in ("完成", "失败")]
    print("SA poll pending=%d" % len(recs))
    for rid, q in recs:
        if _recent_done(rid):
            print("SA skip dup %s" % q)
            continue
        analyze(q, rid)
        _mark_done(rid)
    return len(recs)


PUSH_QUEUE = os.path.join(HERE, "sa_push_queue.jsonl")


def drain_push_queue():
    """门户页(SYSTEM 上下文)无 lark-cli 凭证无法直接推卡：
    页面分析完成时入队，由本任务(Administrator，有凭证)代推飞书卡片。"""
    if not os.path.exists(PUSH_QUEUE):
        return
    with open(PUSH_QUEUE, encoding="utf-8") as f:
        lines = [x for x in f.read().splitlines() if x.strip()]
    if not lines:
        return
    left = []
    for x in lines:
        try:
            r = json.loads(x)
            push_card(
                r["name"],
                r["bias"],
                r["strength"],
                r["news_n"],
                r["px"],
                r["stat"],
                r["outlook"],
                r["judged"],
                _cfg().get("jev_base_url", ""),
            )
            print("SA queued-card OK %s" % r["name"])
        except Exception as e:
            print("SA queued-card FAIL %s: %s" % (r.get("name", "?"), str(e)[:100]))
            left.append(x)
    with open(PUSH_QUEUE, "w", encoding="utf-8") as f:
        f.write("".join(l + "\n" for l in left))


def _poll_lock():
    """并发锁：计划任务与手动 --poll 撞车会重复分析+重复推卡
    （实测英伟达 20s 内被分析两次推两张卡）。锁文件超 10 分钟视为
    残留（进程被杀没来得及清理）自动失效。"""
    import time

    lock = os.path.join(HERE, "logs", "sa_poll.lock")
    try:
        if os.path.exists(lock) and time.time() - os.path.getmtime(lock) < 600:
            return False  # 已有实例在跑
        os.makedirs(os.path.dirname(lock), exist_ok=True)
        fd = os.open(lock, os.O_CREAT | os.O_TRUNC | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except OSError:
        return True  # 豁免（C2批2）：docstring 声明——锁不可用时不阻塞业务


def main():
    if "--stock" in sys.argv:
        q = sys.argv[sys.argv.index("--stock") + 1]
        print("SA ok" if analyze(q) else "SA FAIL")
    elif "--poll" in sys.argv:
        if not _poll_lock():
            print("SA poll busy, skip")
            return
        try:
            drain_push_queue()
            poll_ndjson()
        finally:
            try:
                os.remove(os.path.join(HERE, "logs", "sa_poll.lock"))
            except OSError as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "news_collector/stock_analyze.py", exc)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
