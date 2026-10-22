"""council_ops.py — 议会厅交互桥（v3，2026-10-23）。

职责：议会厅页面的本地后端——新会议/留言/预约/文档上传。
形态：127.0.0.1:8798 单线程 HTTP（会话级后台进程，不动系统服务、不写注册表）。
落盘：全部进老田的AI助手仓（议会厅账本 + uploads/），git 可溯。

端点：
  GET  /healthz                     存活
  GET  /api/council/state           议题+留言快照（复用 gen_council_data 逻辑 + messages）
  POST /api/council/new             {title,tags,originator}→新议题（写 registry 片段+留言）
  POST /api/council/message         {topic,seat,text}→发言留言（落 council_messages.jsonl）
  POST /api/council/schedule        {title,tags,at,note}→预约会议（落 scheduled.jsonl）
  POST /api/council/upload          multipart 文件→uploads/（限 10MB，白名单后缀）
纪律：仅 127.0.0.1；不鉴权（本机单用户）；原子写；上传不执行只存证。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import tempfile
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, ".."))  # portal 的上级 = 老田的AI助手
DATA_DIR = os.path.join(REPO, "portal", "data")
UPLOAD_DIR = os.path.join(DATA_DIR, "council_uploads")
MESSAGES = os.path.join(DATA_DIR, "council_messages.jsonl")
SCHEDULED = os.path.join(DATA_DIR, "council_scheduled.jsonl")
REGISTRY_SNIPPET = os.path.join(DATA_DIR, "council_new_topics.json")

ALLOWED_EXT = {".md", ".txt", ".pdf", ".png", ".jpg", ".jpeg", ".json", ".csv", ".docx", ".xlsx", ".log", ".py", ".js", ".html"}
MAX_UPLOAD = 10 * 1024 * 1024
SAN = re.compile(r"[^\w\u4e00-\u9fff.-]+")


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _append(path: str, rec: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def load_state() -> dict:
    """议题（TOPIC_REGISTRY 片段合并）+ 留言流。"""
    import sys
    sys.path.insert(0, os.path.join(REPO, "tools"))
    topics: list[dict] = []
    try:
        import gen_council_data as gcd
        topics = gcd.build_stage_view()
    except Exception:
        pass
    extra = []
    if os.path.isfile(REGISTRY_SNIPPET):
        try:
            extra = json.load(open(REGISTRY_SNIPPET, encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            extra = []
    msgs = []
    if os.path.isfile(MESSAGES):
        with open(MESSAGES, encoding="utf-8") as f:
            for line in f:
                try:
                    msgs.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return {"generated_at": _now(), "topics": topics, "extra_topics": extra,
            "messages": msgs[-200:]}


# ---------------- 一句话发起 · 规则版中文解析 ----------------
import re as _re

_SEAT_WORDS = [("田丰", "tianfeng"), ("赵云", "zhaoyun"), ("诸葛亮", "zhuge"),
               ("孔明", "zhuge"), ("军师", "zhuge"), ("WorkBuddy", "workbuddy"),
               ("workbuddy", "workbuddy")]
_ACTION_SCHED = ("预约", "安排", "定个", "定一", "排个", "排一")
_ACTION_NOW = ("开个会", "开一个会", "发起", "开会议", "现在开", "立刻开", "马上开")
_WEEK = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}


def parse_natural(text: str) -> dict:
    """规则版：从一句话提取 时间/参与席位/主题/动作。解析不出的字段如实留空。"""
    now = dt.datetime.now()
    at: dt.datetime | None = None
    rest = text

    def _eat(pat: _re.Pattern) -> str | None:
        nonlocal rest
        m = pat.search(rest)
        if not m:
            return None
        rest = rest[:m.start()] + rest[m.end():]
        return m.group(0)

    # 日期
    if _eat(_re.compile(r"(\d{4})[年./-](\d{1,2})[月./-](\d{1,2})[号日]?")):
        g = _.groups() if False else None
    m = _re.search(r"(\d{4})[年./-](\d{1,2})[月./-](\d{1,2})", text)
    if m:
        at = dt.datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        rest = text.replace(m.group(0), "")
    else:
        m = _re.search(r"(\d{1,2})月(\d{1,2})[号日]", text)
        if m:
            y = now.year
            cand = dt.datetime(y, int(m.group(1)), int(m.group(2)))
            if cand < now - dt.timedelta(days=1):
                cand = cand.replace(year=y + 1)
            at = cand
            rest = text.replace(m.group(0), "")
        else:
            if "后天" in text:
                at = (now + dt.timedelta(days=2)).replace(hour=9, minute=0)
                rest = text.replace("后天", "")
            elif "明天" in text:
                at = (now + dt.timedelta(days=1)).replace(hour=9, minute=0)
                rest = text.replace("明天", "")
            elif "今天" in text:
                at = now.replace(minute=0)
                rest = text.replace("今天", "")
            else:
                m = _re.search(r"周([一二三四五六日天])", text)
                if m:
                    delta = (_WEEK[m.group(1)] - now.weekday()) % 7 or 7
                    at = (now + dt.timedelta(days=delta)).replace(hour=9, minute=0)
                    rest = text.replace(m.group(0), "")
    # 时间点（支持中文数字：九点/十点半）
    _CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
               "两": 2, "半": 30}
    m = _re.search(r"(\d{1,2})[点:：时](\d{1,2})?[分]?", rest)
    hh = mm = None
    if m:
        hh, mm = int(m.group(1)), int(m.group(2) or 0)
        rest = rest.replace(m.group(0), "", 1)
    else:
        m = _re.search(r"([一二三四五六七八九十两]+)点(半)?", rest)
        if m:
            word = m.group(1)
            hh = _CN_NUM.get(word, 10 if word == "十" else None)
            if word.startswith("十") and len(word) > 1:
                hh = 10 + _CN_NUM.get(word[1], 0)
            mm = 30 if m.group(2) else 0
            rest = rest.replace(m.group(0), "", 1)
    if hh is not None and at:
        at = at.replace(hour=min(hh, 23), minute=min(mm or 0, 59))
    # 参与人
    seats = []
    for w, sid in _SEAT_WORDS:
        if w in rest:
            seats.append(sid)
            rest = rest.replace(w, "")
    # 动作
    action = None
    for w in _ACTION_SCHED:
        if w in text:
            action = "schedule"
            break
    if action is None and at and at > now + dt.timedelta(hours=2):
        action = "schedule"
    if action is None:
        action = "new"
    # 主题：去动作词/会议词/连接词/时间残字
    for w in _ACTION_SCHED + _ACTION_NOW + ("的会议", "个会议", "开个会", "开会", "会议", "参加", "参与", "哪些人", "谁来",
                                            "主题是", "主题", "讨论", "一下", "安排", "预约", "然后", "这个",
                                            "号", "和", "与", "跟", "及", "的", "上", "会"):
        rest = rest.replace(w, "")
    topic = _re.sub(r"[，,。.、;；!！?？\s]+", " ", rest).strip()
    return {"action": action, "at": at.strftime("%Y-%m-%d %H:%M") if at else "",
            "seats": seats, "title": topic[:60] or text[:40],
            "raw": text}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # 静默
        pass

    def _json(self, obj, status: int = 200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/healthz":
            return self._json({"ok": True, "at": _now()})
        if u.path == "/api/council/state":
            return self._json(load_state())
        return self._json({"error": "not found"}, 404)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        if u.path == "/api/council/upload":
            return self._upload(n)
        body = self.rfile.read(n) if n else b"{}"
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._json({"error": "bad json"}, 400)
        if u.path == "/api/council/natural":
            text = str(data.get("text") or "").strip()[:500]
            if not text:
                return self._json({"error": "text required"}, 400)
            parsed = parse_natural(text)
            # 自动执行：schedule→预约账本；new→立题（留言记录解析结果）
            if parsed["action"] == "schedule" and parsed["at"]:
                rec = {"at": _now(), "title": parsed["title"], "for": parsed["at"],
                       "seats": parsed["seats"], "raw": text, "status": "scheduled"}
                _append(SCHEDULED, rec)
                _append(MESSAGES, {"at": _now(), "topic": parsed["title"], "seat": "laotian",
                                   "kind": "new", "text": f"🎤 一句话预约：「{text}」→ {parsed['at']}，参与：{'、'.join(parsed['seats']) or '全体'}"})
                return self._json({"ok": True, "parsed": parsed, "executed": "scheduled"})
            tid = dt.datetime.now().strftime("%m%d-%H%M")
            topic = {"topic": f"{parsed['title']}（#{tid}）",
                    "tags": parsed["seats"] or ["govern"], "stage": "立题",
                    "originator": "laotian", "summary": f"🎤 语音发起 {tid}" + (f" · {parsed['at']}" if parsed["at"] else ""),
                    "created_at": _now()}
            _append(MESSAGES, {"at": _now(), "topic": topic["topic"], "seat": "laotian",
                               "kind": "new", "text": f"🎤 一句话发起：「{text}」" + (f" · 时间 {parsed['at']}" if parsed["at"] else "")})
            cur = []
            if os.path.isfile(REGISTRY_SNIPPET):
                try:
                    cur = json.load(open(REGISTRY_SNIPPET, encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    cur = []
            cur.append(topic)
            fd, tmp = tempfile.mkstemp(dir=DATA_DIR, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(cur, f, ensure_ascii=False, indent=1)
            os.replace(tmp, REGISTRY_SNIPPET)
            return self._json({"ok": True, "parsed": parsed, "executed": "new", "topic": topic})
        if u.path == "/api/council/new":
            title = str(data.get("title") or "").strip()[:80]
            tags = [str(t)[:20] for t in (data.get("tags") or []) if str(t).strip()][:5]
            originator = str(data.get("originator") or "laotian")[:20]
            if not title:
                return self._json({"error": "title required"}, 400)
            tid = dt.datetime.now().strftime("%m%d-%H%M")
            topic = {"topic": f"{title}（#{tid}）", "tags": tags or ["govern"],
                    "stage": "立题", "originator": originator,
                    "summary": f"老田 {tid} 发起", "created_at": _now()}
            _append(MESSAGES, {"at": _now(), "topic": topic["topic"],
                               "seat": originator, "kind": "new", "text": f"发起议题：{title}"})
            if os.path.isfile(REGISTRY_SNIPPET):
                try:
                    cur = json.load(open(REGISTRY_SNIPPET, encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    cur = []
            else:
                cur = []
            cur.append(topic)
            fd, tmp = tempfile.mkstemp(dir=DATA_DIR, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(cur, f, ensure_ascii=False, indent=1)
            os.replace(tmp, REGISTRY_SNIPPET)
            return self._json({"ok": True, "topic": topic})
        if u.path == "/api/council/message":
            topic = str(data.get("topic") or "")[:120]
            seat = str(data.get("seat") or "laotian")[:20]
            text = str(data.get("text") or "").strip()[:4000]
            if not text:
                return self._json({"error": "text required"}, 400)
            rec = {"at": _now(), "topic": topic, "seat": seat,
                   "kind": str(data.get("kind") or "say"), "text": text}
            _append(MESSAGES, rec)
            return self._json({"ok": True, "message": rec})
        if u.path == "/api/council/schedule":
            title = str(data.get("title") or "").strip()[:80]
            at = str(data.get("at") or "").strip()[:30]
            if not title or not at:
                return self._json({"error": "title/at required"}, 400)
            rec = {"at": _now(), "title": title, "for": at,
                   "tags": [str(t)[:20] for t in (data.get("tags") or [])][:5],
                   "note": str(data.get("note") or "")[:300],
                   "status": "scheduled"}
            _append(SCHEDULED, rec)
            return self._json({"ok": True, "schedule": rec})
        return self._json({"error": "not found"}, 404)

    def _upload(self, n: int):
        ct = self.headers.get("Content-Type") or ""
        if "multipart/form-data" not in ct or n <= 0 or n > MAX_UPLOAD + 65536:
            return self._json({"error": "bad upload"}, 400)
        raw = self.rfile.read(n)
        m = re.search(rb'filename="([^"]+)"', raw)
        name = (m.group(1).decode("utf-8", errors="replace") if m else "file")
        safe = SAN.sub("_", os.path.basename(name))[:60] or "file"
        ext = os.path.splitext(safe)[1].lower()
        if ext not in ALLOWED_EXT:
            return self._json({"error": f"不支持的类型 {ext}（白名单：文档/图片/表格/代码/日志）"}, 400)
        # 取 multipart 最后一个边界前的正文（简单解析足够：单文件场景）
        b = re.search(rb"boundary=(.+)", ct.encode() if ct else b"")
        body = raw
        if b:
            boundary = b.group(1).strip()
            parts = raw.split(b"--" + boundary)
            for p in parts:
                if p and name.encode()[:5] in p[:600]:
                    seg = p.split(b"\r\n\r\n", 1)
                    if len(seg) == 2:
                        body = seg[1].rsplit(b"\r\n", 1)[0]
                        break
        if len(body) > MAX_UPLOAD:
            return self._json({"error": "超过 10MB"}, 400)
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        stamp = dt.datetime.now().strftime("%m%d-%H%M%S")
        dest = os.path.join(UPLOAD_DIR, f"{stamp}_{safe}")
        with open(dest, "wb") as f:
            f.write(body)
        rec = {"at": _now(), "file": dest, "size": len(body), "name": safe}
        _append(MESSAGES, {"at": _now(), "topic": "(上传)", "seat": "laotian",
                           "kind": "upload", "text": f"上传文档：{safe}（{len(body)}B）",
                           "path": dest})
        return self._json({"ok": True, "upload": rec})


def main() -> int:
    srv = ThreadingHTTPServer(("127.0.0.1", 8798), Handler)
    print("council_ops listening http://127.0.0.1:8798 (会话级进程，Ctrl+C 退出)", flush=True)
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
