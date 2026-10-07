# -*- coding: utf-8 -*-
"""公众号选题与撰文 — 本地 Ollama 生成草稿

流程:
  news.db 近 Nh 素材 → LLM 选题(3个) → LLM 撰文(选中最佳)
  → drafts/YYYY-MM-DD-*.md + preview.html(公众号内联样式) → 飞书审阅推送
草稿状态: index.json [{id,title,status: draft/reviewed/published}]
用法: python gzh_writer.py [--dry]
"""
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(HERE, "config.json")
DRAFTS = os.path.join(HERE, "drafts")
INDEX = os.path.join(DRAFTS, "index.json")
NEWS_DIR = os.path.abspath(os.path.join(HERE, "..", "news_collector"))
LOG_PATH = os.path.join(HERE, "logs", "gzh.log")
CST = timezone(timedelta(hours=8))
OLLAMA = "http://127.0.0.1:11434/api/chat"

SYS_PROMPT = (
    "你是一名资深公众号主编兼作者，为个人创作者供稿。"
    "规则：①只能依据用户提供的素材归纳写作，禁止编造数据、引语或事件；"
    "②标题克制、不标题党；③全部用简体中文；④输出严格遵守用户要求的格式。"
)

TOPIC_PROMPT = (
    "公众号定位：{pos}\n写作风格：{style}\n固定栏目：{cols}\n\n"
    "以下是近 {h} 小时财经素材（⭐为创作者关注命中）：\n{mat}\n\n"
    "请提出 {n} 个公众号选题。要求：贴合定位、能从上述素材中取材、"
    "避免纯新闻搬运（要有创作者视角）。每个选题严格一行，格式：\n"
    "选题N｜标题（不超过20字）｜栏目｜切入角度（30字内）｜素材编号如1,5,9\n"
    "只输出选题行，不要其他内容。"
)

ARTICLE_PROMPT = (
    "公众号定位：{pos}\n写作风格：{style}\n\n"
    "选题：{title}\n栏目：{col}\n切入角度：{angle}\n\n"
    "相关素材：\n{mat}\n\n"
    "请围绕该选题写一篇约 {words} 字的公众号文章，Markdown 格式：\n"
    "· 第一行「# 标题」（可微调选题标题）\n"
    "· 导语 2-3 句（手机阅读钩子，点明读者收益）\n"
    "· 2-4 个「## 」小标题正文，短段落，可少量 - 列表\n"
    "· 素材数据须注明来源媒体，禁止编造；没有的数据不要硬造\n"
    "· 结尾一段互动引导（提问式）\n"
    "只输出文章本身。"
)

HTML_STYLE = {
    "h1": "font-size:20px;font-weight:600;color:#1a1a1a;margin:24px 0 16px;"
          "line-height:1.4;text-align:center;",
    "h2": "font-size:17px;font-weight:600;color:#1a1a1a;margin:28px 0 12px;"
          "line-height:1.4;border-left:4px solid #07c160;padding-left:10px;",
    "p": "font-size:15px;color:#333;line-height:1.8;margin:0 0 14px;"
         "letter-spacing:.5px;text-align:justify;",
    "strong": "color:#07c160;font-weight:600;",
    "blockquote": "font-size:14px;color:#888;line-height:1.7;margin:14px 0;"
                  "padding:10px 14px;background:#f7f7f7;border-radius:4px;",
    "li": "font-size:15px;color:#333;line-height:1.8;margin:0 0 8px;",
}


def log(msg):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write("[%s] %s\n" % (datetime.now(CST).strftime("%m-%d %H:%M:%S"), msg))


def llm(user, num_predict=1200, temperature=0.5):
    model = _CFG["model"]
    r = requests.post(OLLAMA, json={
        "model": model,
        "messages": [{"role": "system", "content": SYS_PROMPT},
                     {"role": "user", "content": user}],
        "stream": False, "think": False,
        "options": {"temperature": temperature, "num_predict": num_predict},
    }, timeout=600)
    r.raise_for_status()
    return r.json()["message"]["content"].strip()


def fetch_material(db, hours, limit):
    since = (datetime.now(CST) - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M")
    rows = db.execute(
        "SELECT source, ts, title, hit, COALESCE(stocks,'') FROM news "
        "WHERE created_at>=? ORDER BY hit DESC, created_at DESC LIMIT ?",
        (since, limit)).fetchall()
    src = {"wallstreetcn": "见闻", "sina": "新浪", "eastmoney": "东财"}
    lines = []
    for i, r in enumerate(rows, 1):
        star = "⭐" if r[3] else "  "
        tag = ("【%s】" % "/".join(json.loads(r[4]))) if r[4] else ""
        lines.append("%d. %s[%s]%s %s" % (i, star, src.get(r[0], r[0]), tag,
                                          (r[2] or "")[:80]))
    return lines


def parse_topics(text):
    """解析 LLM 选题行 → [{n,title,col,angle,refs}]。兼容 4/5 段式。"""
    out = []
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln.startswith("选题"):
            continue
        parts = re.split(r"[｜|]", ln)
        if len(parts) < 4:
            continue
        n = re.sub(r"\D", "", parts[0])
        title = parts[1].strip()
        col = parts[2].strip()
        rest = "｜".join(parts[3:]).strip()
        # 结尾的素材编号（如 "角度内容 2,5,9"）剥离出来
        m = re.match(r"(.+?)\s*(\d+(?:[,\s，]\d+)*)\s*$", rest)
        if m and re.search(r"[^\d,\s，]", m.group(1)):
            angle, refs = m.group(1).strip(), m.group(2)
        else:
            angle, refs = rest, ""
        if title:
            out.append({"n": int(n or len(out) + 1), "title": title,
                        "col": col, "angle": angle, "refs": refs})
    return out


def _inline(tag, text):
    return '<%s style="%s">%s</%s>' % (tag, HTML_STYLE[tag], text, tag)


def md_to_gzh_html(md):
    """Markdown → 公众号兼容内联样式 HTML（支持 #/##/p/strong/-列表/引用）。"""
    html, in_list = [], False
    for raw in md.splitlines():
        ln = raw.strip()
        if not ln:
            if in_list:
                html.append("</ul>")
                in_list = False
            continue
        ln = re.sub(r"\*\*(.+?)\*\*", lambda m: _inline("strong", m.group(1)), ln)
        if ln.startswith("# "):
            html.append(_inline("h1", ln[2:]))
        elif ln.startswith("## "):
            html.append(_inline("h2", ln[3:]))
        elif ln.startswith("> "):
            html.append(_inline("blockquote", ln[2:]))
        elif ln.startswith(("- ", "* ")):
            if not in_list:
                html.append('<ul style="padding-left:18px;margin:0 0 14px">')
                in_list = True
            html.append("<li style=\"%s\">%s</li>" % (HTML_STYLE["li"], ln[2:]))
        else:
            if in_list:
                html.append("</ul>")
                in_list = False
            html.append(_inline("p", ln))
    if in_list:
        html.append("</ul>")
    return "\n".join(html)


def load_index():
    try:
        return json.load(open(INDEX, encoding="utf-8"))
    except Exception:
        return []


def save_index(items):
    os.makedirs(DRAFTS, exist_ok=True)
    tmp = INDEX + ".tmp"
    json.dump(items, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, INDEX)


def push_message(text):
    tmp = os.path.join(NEWS_DIR, "outbox", "pending_gzh.txt")
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    r = subprocess.run([sys.executable, os.path.join(NEWS_DIR, "send_feishu.py"), tmp],
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    out = (r.stdout or "").strip()
    log("push -> " + out)
    return out.startswith("SEND_OK")


def run(dry):
    cfg = _CFG
    db = sqlite3.connect(os.path.join(NEWS_DIR, "news.db"))
    mat = fetch_material(db, int(cfg["material_hours"]), int(cfg["material_limit"]))
    db.close()
    if len(mat) < 10:
        log("material too few %d, skip" % len(mat))
        print("GZH SKIP material=%d" % len(mat))
        return
    mat_text = "\n".join(mat)

    # 1) 选题
    topics_raw = llm(TOPIC_PROMPT.format(
        pos=cfg["positioning"], style=cfg["style"], cols="、".join(cfg["columns"]),
        h=cfg["material_hours"], mat=mat_text, n=cfg["topics_per_day"]),
        num_predict=500, temperature=0.6)
    topics = parse_topics(topics_raw)
    if not topics:
        log("topics parse FAIL:\n%s" % topics_raw[:300])
        print("GZH_FAIL topics parse")
        return
    log("topics ok=%d: %s" % (len(topics),
                              " / ".join(t["title"] for t in topics)))

    # 2) 撰文（取第一个选题）
    t = topics[0]
    refs = [mat[int(x) - 1] for x in re.findall(r"\d+", t["refs"])
            if 0 < int(x) <= len(mat)]
    article = llm(ARTICLE_PROMPT.format(
        pos=cfg["positioning"], style=cfg["style"], title=t["title"],
        col=t["col"], angle=t["angle"], mat="\n".join(refs[:15] or mat[:15]),
        words=cfg["words"]), num_predict=2600, temperature=0.7)
    if not article.startswith("#"):
        article = "# " + t["title"] + "\n\n" + article

    # 3) 草稿落盘
    today = datetime.now(CST).strftime("%Y-%m-%d")
    slug = re.sub(r"[^\w\u4e00-\u9fa5]+", "-", t["title"]).strip("-")[:24] or "draft"
    md_path = os.path.join(DRAFTS, "%s-%s.md" % (today, slug))
    html_path = md_path.replace(".md", ".preview.html")
    os.makedirs(DRAFTS, exist_ok=True)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(article)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write('<meta charset="utf-8">' + md_to_gzh_html(article))

    items = load_index()
    item = {"id": "%s-%02d" % (today, len(items) + 1), "date": today,
            "title": t["title"], "col": t["col"], "status": "draft",
            "md": os.path.basename(md_path), "ts": datetime.now(CST).strftime("%m-%d %H:%M")}
    items.insert(0, item)
    save_index(items)

    # 4) 飞书审阅
    msg_lines = ["📝 公众号草稿待审阅 · %s" % today, ""]
    msg_lines.append("今日选题：")
    for tp in topics:
        mark = "→ 已成稿" if tp is t else "  备选"
        msg_lines.append("%s｜%s｜%s" % (mark, tp["title"], tp["col"]))
    msg_lines += ["", "=" * 30, "", article[:1200]]
    if len(article) > 1200:
        msg_lines += ["", "……（全文见草稿）"]
    msg_lines += ["", "草稿: gzh\\drafts\\%s" % item["md"],
                  "预览: %s（浏览器打开可直接复制进公众号编辑器）" % os.path.basename(html_path),
                  "—— 公众号运营域 · 本地 %s 生成" % cfg["model"]]
    sent = dry or push_message("\n".join(msg_lines))

    log("draft ok %s sent=%s" % (item["id"], sent))
    print("GZH_OK %s topics=%d sent=%s" % (item["id"], len(topics), sent))
    if dry:
        print("=" * 60)
        print("\n".join(msg_lines))


_CFG = json.load(open(CFG_PATH, encoding="utf-8"))

if __name__ == "__main__":
    run("--dry" in sys.argv)
