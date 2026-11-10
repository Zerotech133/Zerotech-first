"""council_ops.py — 议会厅交互桥（v3，2026-10-23）。

职责：议会厅页面的本地后端——新会议/留言/预约/文档上传。
形态：127.0.0.1:8798 单线程 HTTP（会话级后台进程，不动系统服务、不写注册表）。
落盘：全部进老田的AI助手仓（议会厅账本 + uploads/），git 可溯。

端点：
  GET  /healthz                     存活
  POST /api/council/auth/login      口令登录（须白名单 Origin）
  GET  /api/council/state           议题+留言快照（复用 gen_council_data 逻辑 + messages）
  POST /api/council/preflight/confirm  会前确认配置→新议题与配置记录（幂等编号）
  POST /api/council/message         {topic,seat,text}→发言留言（落 council_messages.jsonl）
  /new、/schedule、/idea_promote 与自然语言直建会已拦截；预约确认尚未接入
  POST /api/council/upload          multipart 文件→uploads/（限 10MB，白名单后缀）
纪律：仅 loopback；口令会话鉴权与 Origin 门禁；原子写；上传不执行只存证。
"""

from __future__ import annotations


import logging

import datetime as dt
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import tempfile
import time
import threading
import uuid
import urllib.parse
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
try:
    from safe_urlopen import safe_urlopen
except ImportError:
    from portal.safe_urlopen import safe_urlopen


try:  # Phase B1: 8787 密码认证后自动带 hermes_session
    import sys as _sys
    from pathlib import Path as _P
    _sys.path.insert(0, str(_P(__file__).resolve().parents[1]))
    from agent_platform.legion_command.hermes_auth import auth_urlopen as safe_urlopen  # noqa: E402
except Exception:  # noqa: BLE001
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, ".."))  # portal 的上级 = 老田的AI助手
DATA_DIR = os.path.join(REPO, "portal", "data")
UPLOAD_DIR = os.path.join(DATA_DIR, "council_uploads")
MESSAGES = os.path.join(DATA_DIR, "council_messages.jsonl")
SCHEDULED = os.path.join(DATA_DIR, "council_scheduled.jsonl")
REGISTRY_SNIPPET = os.path.join(DATA_DIR, "council_new_topics.json")
PREFLIGHT = os.path.join(DATA_DIR, "council_preflight.json")
MEETING_WRITE_LOCK = threading.RLock()
ARCHIVE_ROOT = os.path.join(DATA_DIR, "council_meetings")  # 建档：月/会议号/五件套

ALLOWED_EXT = {
    ".md",
    ".txt",
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".json",
    ".csv",
    ".docx",
    ".xlsx",
    ".log",
    ".py",
    ".js",
    ".html",
}
MAX_UPLOAD = 10 * 1024 * 1024
SAN = re.compile(r"[^\w\u4e00-\u9fff.-]+")
SESSION_TTL = 30 * 60
SESSION_COOKIE = "council_ops_session"
DEFAULT_ORIGINS = ("http://127.0.0.1:8080", "http://localhost:8080")


def _loopback(address: str) -> bool:
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return False


def _allowed_origins() -> frozenset[str]:
    configured = os.environ.get("COUNCIL_OPS_ORIGINS")
    origins = DEFAULT_ORIGINS if configured is None else tuple(configured.split(","))
    if not origins:
        raise ValueError("COUNCIL_OPS_ORIGINS 必须包含本机页面源")
    for origin in origins:
        parsed = urllib.parse.urlsplit(origin)
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("COUNCIL_OPS_ORIGINS 包含无效端口") from exc
        if (
            not origin
            or parsed.scheme != "http"
            or not parsed.hostname
            or port is None
            or not 1 <= port <= 65535
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
            or origin != f"http://{parsed.netloc}"
            or not (parsed.hostname == "localhost" or _loopback(parsed.hostname))
        ):
            raise ValueError("COUNCIL_OPS_ORIGINS 只允许带端口的本机 http 页面源")
    return frozenset(origins)


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
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "silent-except %s: %s", "council_ops.py", exc
        )
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
    return {
        "generated_at": _now(),
        "topics": topics,
        "extra_topics": extra,
        "messages": msgs[-200:],
    }


# ---------------- 一句话发起 · 规则版中文解析 ----------------
import re as _re

_SEAT_WORDS = [
    ("田丰", "tianfeng"),
    ("赵云", "zhaoyun"),
    ("诸葛亮", "zhuge"),
    ("孔明", "zhuge"),
    ("军师", "zhuge"),
    ("WorkBuddy", "workbuddy"),
    ("workbuddy", "workbuddy"),
]
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
        rest = rest[: m.start()] + rest[m.end() :]
        return m.group(0)

    # 日期
    _eat(_re.compile(r"(\d{4})[年./-](\d{1,2})[月./-](\d{1,2})[号日]?"))
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
    _CN_NUM = {
        "一": 1,
        "二": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "十": 10,
        "两": 2,
        "半": 30,
    }
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
    for w in (
        _ACTION_SCHED
        + _ACTION_NOW
        + (
            "的会议",
            "个会议",
            "开个会",
            "开会",
            "会议",
            "参加",
            "参与",
            "哪些人",
            "谁来",
            "主题是",
            "主题",
            "讨论",
            "一下",
            "安排",
            "预约",
            "然后",
            "这个",
            "号",
            "和",
            "与",
            "跟",
            "及",
            "的",
            "上",
            "会",
        )
    ):
        rest = rest.replace(w, "")
    topic = _re.sub(r"[，,。.、;；!！?？\s]+", " ", rest).strip()
    return {
        "action": action,
        "at": at.strftime("%Y-%m-%d %H:%M") if at else "",
        "seats": seats,
        "title": topic[:60] or text[:40],
        "raw": text,
    }


# ---------------- 定稿一句话解析（开源判词：规则宁漏勿编） ----------------
_ACTION_ARCHIVE = ("定稿", "归档", "结案", "收口定稿")


def parse_archive(text: str) -> dict:
    """「定稿：决议X。田丰做Y，期限10-24；赵云盯守Z」→ decision+tasks。

    规则：[谁]做/负责/盯 … (期限|by|due 日期)? ；缺 owner 标 TBD 不编造。
    """
    decision = ""
    m = _re.search(r"决议[是为：:]?\s*([^；;。]+)", text)
    if m:
        decision = m.group(1).strip()[:500]
    tasks = []
    for seg in _re.split(r"[；;。\n]", text):
        seg = seg.strip()
        if not seg or seg in ("定稿", "归档") or (decision and seg == decision):
            continue
        tm = _re.search(
            r"\[([^\]]+)\]\s*(.+?)(?:[，,]\s*(?:期限|by|due)\s*([\d/-]+))?$", seg
        ) or _re.search(
            r"(田丰|赵云|诸葛亮|老田|WorkBuddy|workbuddy|INTEL0|A0)[做负责盯跟跟进送]\s*(.+?)(?:[，,]\s*(?:期限|by|due)\s*([\d/-]+))?$",
            seg,
        )
        if tm:
            tasks.append(
                {
                    "owner": tm.group(1),
                    "text": tm.group(2).strip()[:200],
                    "due": tm.group(3) or "",
                }
            )
        elif seg and not decision:
            decision = seg[:500]  # 无决议标时，首段视为决议
    return {"decision": decision, "tasks": tasks}


# ---------------- 会议建档 · 纪要 · 决议 · 检索 ----------------


def _sanitize(name: str) -> str:
    return SAN.sub("_", name)[:40] or "meeting"


def archive_meeting(topic: str, decision: str = "", tasks: list | None = None) -> dict:
    """会议定稿建档：月/会议号/五件套（meta.json/minutes.md/decision.md/tasks.jsonl/README）。

    纪要=该议题全部留言+文书聚合（AI 归纳骨架 + 原文流水，不丢原始记录）。
    决议=老田定稿文字。任务=决议分解的督促条目（status 驱动复用 devtask 习惯）。
    """
    msgs = [m for m in _load_messages() if topic[:8] in (m.get("topic") or "")]
    now = dt.datetime.now()
    mid = now.strftime("%Y%m%d-%H%M")
    folder = os.path.join(
        ARCHIVE_ROOT, now.strftime("%Y-%m"), f"{mid}_{_sanitize(topic)}"
    )
    os.makedirs(folder, exist_ok=True)
    seats = sorted({m.get("seat", "?") for m in msgs})
    meta = {
        "meeting_id": mid,
        "topic": topic,
        "archived_at": _now(),
        "seats": seats,
        "message_count": len(msgs),
        "decision": bool(decision),
        "task_count": len(tasks or []),
    }
    minutes = [
        f"# 会议纪要 · {topic}",
        "",
        f"- 会议号：{mid} · 归档：{meta['archived_at']}",
        f"- 参与：{'、'.join(seats) or '（无留言记录）'} · 发言 {len(msgs)} 条",
        "",
    ]
    if decision:
        minutes += ["## 决议（定稿）", "", f"> {decision}", ""]
    if tasks:
        minutes += ["## 任务分解", ""]
        for i, t in enumerate(tasks or [], 1):
            minutes.append(
                f"{i}. [{t.get('owner', '?')}] {t.get('text', '')} —— {t.get('due', '无期限')}"
            )
        minutes.append("")
    minutes += ["## 发言流水（原文，未删改）", ""]
    for m in msgs:
        minutes.append(
            f"- **{m.get('seat', '?')}** {str(m.get('at', ''))[5:16]}：{m.get('text', '')}"
            + (f"（📎 {os.path.basename(m.get('path', ''))}）" if m.get("path") else "")
        )
    fd, tmp = tempfile.mkstemp(dir=folder)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(minutes))
    os.replace(tmp, os.path.join(folder, "minutes.md"))
    with open(os.path.join(folder, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    with open(os.path.join(folder, "tasks.jsonl"), "w", encoding="utf-8") as f:
        for t in tasks or []:
            t = dict(
                t,
                meeting_id=mid,
                topic=topic,
                created=_now(),
                status=t.get("status", "todo"),
            )
            f.write(json.dumps(t, ensure_ascii=False) + "\n")
    _append(
        MESSAGES,
        {
            "at": _now(),
            "topic": topic,
            "seat": "laotian",
            "kind": "archive",
            "text": f"📦 会议定稿建档 {mid}（纪要+决议+任务{len(tasks or [])}项）",
            "path": folder,
        },
    )
    try:
        post_archive_hooks(topic, mid, decision, tasks or [])
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "silent-except %s: %s", "council_ops.py", exc
        )
    return {"ok": True, "meeting_id": mid, "folder": folder, "meta": meta}


def post_archive_hooks(topic: str, meeting_id: str, decision: str, tasks: list) -> dict:
    """定稿后联动：①飞书/队列通知 ②任务→devtask 直通 ③回执入对话流。"""
    body = (
        f"**{topic}** 已定稿\n决议：{decision or '（无）'}\n任务 {len(tasks)} 项：\n"
        + "\n".join(
            f"- [{t.get('owner')}] {t.get('text', '')}"
            + (f" · {t['due']}" if t.get("due") else "")
            for t in tasks[:8]
        )
    )
    n = notify(f"📦 会议定稿 {meeting_id}", body)
    d = dispatch_to_devtask(tasks, meeting_id) if tasks else []
    ok_n = sum(1 for x in d if x.get("dispatched"))
    _append(
        MESSAGES,
        {
            "at": _now(),
            "topic": topic,
            "seat": "sys",
            "kind": "sys",
            "text": f"🔔 多端通知：{n['channel']}"
            + (
                "（已发飞书）"
                if n.get("sent")
                else "（待发队列——未配 COUNCIL_FEISHU_WEBHOOK）"
            )
            + (
                f"\n🚀 工单直通：{ok_n}/{len(d)} 已建 devtask"
                + (
                    "\n· "
                    + "\n· ".join(
                        f"❌ {x['task'][:20]}：{x.get('why', '')[:40]}"
                        for x in d
                        if not x.get("dispatched")
                    )
                    if ok_n < len(d)
                    else ""
                )
                if d
                else ""
            ),
        },
    )
    return {"notify": n, "dispatch": d}


def _load_messages() -> list[dict]:
    out = []
    if os.path.isfile(MESSAGES):
        with open(MESSAGES, encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out


def load_archives() -> list[dict]:
    """全部会议档案索引（月/会议号/meta）。"""
    out = []
    if not os.path.isdir(ARCHIVE_ROOT):
        return out
    for month in sorted(os.listdir(ARCHIVE_ROOT), reverse=True):
        mdir = os.path.join(ARCHIVE_ROOT, month)
        if not os.path.isdir(mdir):
            continue
        for name in sorted(os.listdir(mdir), reverse=True):
            mp = os.path.join(mdir, name, "meta.json")
            if os.path.isfile(mp):
                try:
                    out.append(json.load(open(mp, encoding="utf-8")))
                except (OSError, json.JSONDecodeError):
                    continue
    return out


def load_tasks() -> list[dict]:
    """督促视图：全部档案的任务汇总。"""
    out = []
    if not os.path.isdir(ARCHIVE_ROOT):
        return out
    for month in os.listdir(ARCHIVE_ROOT):
        mdir = os.path.join(ARCHIVE_ROOT, month)
        if not os.path.isdir(mdir):
            continue
        for name in os.listdir(mdir):
            tp = os.path.join(mdir, name, "tasks.jsonl")
            if os.path.isfile(tp):
                with open(tp, encoding="utf-8") as f:
                    for line in f:
                        try:
                            out.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
    return out


def search_all(q: str) -> list[dict]:
    """快速查询：留言账本 + 全部会议纪要全文（子串匹配，返回命中上下文）。"""
    q = q.strip()
    if not q:
        return []
    hits: list[dict] = []

    def _scan_text(text: str, src: str, ref: str):
        idx = text.find(q)
        if idx >= 0:
            hits.append(
                {
                    "src": src,
                    "ref": ref,
                    "ctx": text[max(0, idx - 30) : idx + len(q) + 50].replace(
                        "\n", " "
                    ),
                }
            )

    for m in _load_messages():
        _scan_text(
            str(m.get("text", "")),
            "留言",
            f"{m.get('seat')} {str(m.get('at', ''))[5:16]}",
        )
    if os.path.isdir(ARCHIVE_ROOT):
        for month in os.listdir(ARCHIVE_ROOT):
            mdir = os.path.join(ARCHIVE_ROOT, month)
            if not os.path.isdir(mdir):
                continue
            for name in os.listdir(mdir):
                fp = os.path.join(mdir, name, "minutes.md")
                if os.path.isfile(fp):
                    try:
                        _scan_text(
                            open(fp, encoding="utf-8").read(),
                            "会议纪要",
                            f"{month}/{name}",
                        )
                    except OSError:
                        continue
    return hits[:30]


def _latest_active_topic() -> str:
    """最近一次「开会」的议题名（定稿无当前议题时兜底）。"""
    if not os.path.isfile(REGISTRY_SNIPPET):
        return ""
    try:
        cur = json.load(open(REGISTRY_SNIPPET, encoding="utf-8"))
        return cur[-1]["topic"] if cur else ""
    except (OSError, json.JSONDecodeError):
        return ""


# ---------------- 会议类型自动归类（不刻意：词表判日常/专项） ----------------
_DAILY_CUES = (
    "聊聊",
    "聊一下",
    "碰一下",
    "碰个头",
    "同步",
    "sync",
    "晨会",
    "周会",
    "日报",
    "看看",
    "过一下",
    "随手",
    "日常",
    "随便聊聊",
    "打招呼",
    "状态",
    "站会",
    "例会",
)
_PROJECT_CUES = (
    "修复",
    "开发",
    "方案",
    "评审",
    "立项",
    "工单",
    "重构",
    "上线",
    "部署",
    "设计",
    "改造",
    "排查",
    "根因",
    "恢复",
    "迁移",
    "定稿",
)


def classify_meeting(text: str) -> str:
    """日常/专项自动归类：专项词命中→project；日常词命中→daily；都没中→daily（默认轻流程，不刻意）。"""
    if any(k in text for k in _PROJECT_CUES):
        return "project"
    return "daily"


# ---------------- 想法池 + 三席位自动对答 ----------------
IDEA_POOL = os.path.join(DATA_DIR, "council_ideas.jsonl")

_IDEA_CUES = (
    "想法",
    "点子",
    "灵感",
    "我觉得可以",
    "要是能",
    "不如",
    "可以搞",
    "说不定",
    "或许可以",
)


def _seat_say(profile: str, question: str, timeout: int = 100) -> str:
    """经 8787 真席位问一句（精简版 client；失败返回空串如实降级）。"""
    import urllib.request as _ur

    try:

        def _post(path, payload):
            req = _ur.Request(
                "http://127.0.0.1:8787" + path,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Cookie": f"hermes_profile={profile}",
                },
                method="POST",
            )
            with safe_urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))

        ses = _post("/api/session/new", {"name": "council-auto"})
        sid = ses["session"]["session_id"]
        st = _post("/api/chat/start", {"session_id": sid, "message": question})
        stream_id = st.get("stream_id")
        if not stream_id:
            return ""
        req = _ur.Request(
            f"http://127.0.0.1:8787/api/chat/stream?session_id={sid}&stream_id={stream_id}&replay=1",
            headers={"Cookie": f"hermes_profile={profile}"},
        )
        chunks, ev = [], ""
        with safe_urlopen(req, timeout=timeout) as r:
            for raw in r:
                line = raw.decode("utf-8", errors="replace").rstrip("\n")
                if line.startswith("event:"):
                    ev = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    try:
                        d = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
                    if ev == "token":
                        chunks.append(str(d.get("text") or d.get("token") or ""))
                    elif ev in ("stream_end", "done"):
                        break
        return "".join(chunks).strip()[:400]
    except Exception:
        return ""


def auto_discuss(idea: str) -> list[dict]:
    """三席位自动对答：赵云（风险/治理视角）+ 田丰（落地/拆解视角）并行各一句。"""
    import threading

    out = {}

    def ask(seat, cn, role):
        txt = _seat_say(
            seat,
            f"老田随口抛出一个想法：「{idea}」。你是{cn}（{role}视角）。只用一两句话给观点：值不值得做/最大风险或最大价值/下一步建议。不确定就说不确定。",
        )
        out[seat] = txt

    ts = [
        threading.Thread(target=ask, args=a)
        for a in (("zhaoyun", "赵云", "治理与风险"), ("tianfeng", "田丰", "落地与拆解"))
    ]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=110)
    recs = []
    for seat, cn in (("zhaoyun", "赵云"), ("tianfeng", "田丰")):
        recs.append({"seat": seat, "cn": cn, "view": out.get(seat, "")})
    return recs


# ---------------- 辩论收敛协议（3 轮 → 轮值主席总结 + 投票） ----------------
import threading as _th

DEBATE_SEATS = [("zhaoyun", "赵云", "治理与风险"), ("tianfeng", "田丰", "落地与拆解")]
CHAIR_POOL = [
    ("tianfeng", "田丰"),
    ("zhaoyun", "赵云"),
    ("zhuge", "诸葛亮(建议面代行)"),
]


def chair_of_today() -> tuple[str, str]:
    """轮值主席按日期轮换（防长期固定走偏——党组值班思想）。"""
    day = dt.date.today().day
    sid, cn = CHAIR_POOL[day % len(CHAIR_POOL)]
    return sid, cn


def _ask_seats_parallel(questions: dict, timeout: int = 110) -> dict:
    """{seat: question} 并行问真席位，返回 {seat: reply}。"""
    out: dict[str, str] = {}

    def run(seat, q):
        out[seat] = _seat_say(seat, q, timeout=timeout)

    ts = [_th.Thread(target=run, args=(s, q)) for s, q in questions.items()]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=timeout + 5)
    return out


def run_debate(idea: str, rounds: int = 2) -> dict:
    """辩论收敛：R1 三席位初表态（并行）→ R2 针对分歧反驳（并行）→ 轮值主席总结 + 投票。

    产出全部落对话流；返回 {chair, conclusion, votes, transcript}。
    """
    trans: list[dict] = []
    # R1 初表态
    r1 = _ask_seats_parallel(
        {
            sid: f"老田抛出议题：「{idea}」。你是{cn}（{role}视角），一两句初表态（支持/反对/条件支持+核心理由）。"
            for sid, cn, role in DEBATE_SEATS
        }
    )
    for sid, cn, _ in DEBATE_SEATS:
        trans.append({"round": 1, "seat": sid, "cn": cn, "view": r1.get(sid, "")})
    # R2 反驳/修正（带 R1 全文）
    r1_text = "\n".join(
        f"{cn}：{v}" for _, cn, _ in DEBATE_SEATS for v in [r1.get(_, "")] if v
    )
    r1_text = "\n".join(f"{cn}：{r1.get(sid, '')}" for sid, cn, _ in DEBATE_SEATS)
    r2 = _ask_seats_parallel(
        {
            sid: f"议题「{idea}」。其他席位观点：\n{r1_text}\n你是{cn}：针对分歧点一句话反驳或修正立场（可坚持可让步，说理由）。"
            for sid, cn, _ in DEBATE_SEATS
        }
    )
    for sid, cn, _ in DEBATE_SEATS:
        trans.append({"round": 2, "seat": sid, "cn": cn, "view": r2.get(sid, "")})
    # 轮值主席总结
    chair_id, chair_cn = chair_of_today()
    full_text = "\n".join(
        f"R{t['round']} {t['cn']}：{t['view']}" for t in trans if t["view"]
    )
    conclusion = (
        _seat_say(
            chair_id if chair_id != "zhuge" else "tianfeng",
            f"你是本期轮值主席{chair_cn}。议题「{idea}」，两轮辩论记录：\n{full_text}\n"
            f"请做最终总结（党组值班总结）：3 句内给出统一结论——做/不做/怎么做+关键前提；如仍有分歧给出总裁决建议交老田。",
        )
        if chair_id != "zhuge"
        else ""
    )
    if chair_id == "zhuge":
        # 2026-10-26 A2/C2：载体已可用（OpenClaw+DeepSeek 中转，非GPT6），
        # 但议会厅按既定策略仍规则代行（非模型表态），故如实标注"规则代行"。
        conclusion = "（诸葛亮·建议面代行·轮值主席·规则代行非模型）综合两轮：分歧焦点已明确，建议按'条件推进'收口——采纳田丰的落地三问作为前置条件，采纳赵云的隐私红线作为硬约束；执行方案由老田拍板。"
    # 投票（两席位对结论表态）
    votes = _ask_seats_parallel(
        {
            sid: f"轮值主席结论：「{conclusion[:300]}」。你是{cn}，对这份总结投一票：赞成/反对（一词）+一句理由。"
            for sid, cn, _ in DEBATE_SEATS
        }
    )
    return {
        "chair": {"seat": chair_id, "cn": chair_cn},
        "conclusion": conclusion,
        "votes": {cn: votes.get(sid, "") for sid, cn, _ in DEBATE_SEATS},
        "transcript": trans,
    }


# ---------------- 主题层（商业化：皮肤与机制解耦） ----------------
THEME_PATH = os.path.join(DATA_DIR, "council_theme.json")


def load_theme() -> dict:
    """读主题配置（缺省=三国皮肤；客户复制改字段即换品牌）。"""
    try:
        return json.load(open(THEME_PATH, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "product_name": "会议厅",
            "owner_display": "老田",
            "seats": {},
            "experts": [],
        }


def seat_display(seat: str) -> str:
    t = load_theme()
    return (t.get("seats") or {}).get(seat, {}).get("display", seat)


# ---------------- 领域专家智能召唤（议题主题 → 军团编制席位） ----------------
# 词表→领域→列席 AI（真席位走 8787，未建席位以"召唤卡"形式列席显示）
EXPERT_ROUTES = [
    (
        ["量化", "策略", "回测", "选股", "持仓", "交易"],
        "量化线",
        "司马懿（量化总管·OS 板块代表）",
    ),
    (
        ["缠论", "chanlun", "笔段", "中枢", "背驰"],
        "缠论专项",
        "缠论值守 AI（chanlun 线）",
    ),
    (["UI", "界面", "页面", "设计稿", "交互"], "设计线", "设计值守（UI 专项）"),
    (["前端", "框架", "React", "Vue", "组件"], "前端架构", "前端架构值守"),
    (["后端", "API", "服务", "数据库", "SQL"], "后端架构", "后端架构值守"),
    (["爬虫", "采集", "数据源"], "数据线", "采集值守"),
    (["安全", "漏洞", "合规", "隐私", "权限"], "安全线", "赵云（治理面·安全对口）"),
    (["情报", "信源", "舆情"], "情报线", "曹操统筹线（情报受理）"),
]


def summon_experts(text: str) -> list[dict]:
    """按议题词召回列席专家（主题层优先——客户可自定义领域路由；多域叠加）。"""
    theme = load_theme()
    routes = [
        (e.get("cues", []), e.get("domain", "?"), e.get("who", "?"))
        for e in theme.get("experts", [])
    ] or EXPERT_ROUTES
    out = []
    for cues, domain, who in routes:
        if any(c in text for c in cues):
            out.append({"domain": domain, "who": who})
    return out


# ---------------- 多端通知层（飞书卡片/待发队列） ----------------
FEISHU_HOOK = os.environ.get("COUNCIL_FEISHU_WEBHOOK", "")  # 客户部署时配置
OUTBOX = os.path.join(DATA_DIR, "council_outbox.jsonl")  # 未配 webhook 时落待发队列


def notify(title: str, body: str, deep_link: str = "") -> dict:
    """多端通知：飞书 OpenAPI 卡片直发老田个人（既有应用凭据+历史 open_id）；失败落待发队列。"""
    import urllib.request as _ur

    rec = {
        "at": _now(),
        "title": title,
        "body": body[:600],
        "sent": False,
        "channel": "outbox",
    }
    try:
        cfg = json.load(
            open(os.path.expanduser("~/.lark-cli/config.json"), encoding="utf-8")
        )
        app = next(
            (
                a
                for a in cfg.get("apps", [])
                if a.get("appId", "").startswith("cli_aa3e4f1b")
            ),
            None,
        )
        import sys as _sys

        # A8（2026-11-10）：消除两处路径硬编码
        _aios_root = os.environ.get("AIOS_ROOT", "C:/aios")
        _news_dir = os.path.join(_aios_root, "news_collector")
        _sys.path.insert(0, os.path.join(REPO, "news_collector"))
        os.environ.setdefault("LARK_CLI_CHDIR_DONE", "1")
        old_cwd = os.getcwd()
        os.chdir(_news_dir)
        try:
            import feishu_client as _fc

            tat = _fc.get_tat(app["appId"], app["appSecret"])
        finally:
            os.chdir(old_cwd)
        if app and tat:
            uid = "ou_50a95455c7dffec1090ade400886d0d4"  # 老田个人（news_config 历史投递目标）
            payload = {
                "receive_id": uid,
                "msg_type": "interactive",
                "content": json.dumps(
                    {
                        "config": {"wide_screen_mode": True},
                        "header": {
                            "title": {"tag": "plain_text", "content": title[:60]},
                            "template": "blue",
                        },
                        "elements": [
                            {
                                "tag": "div",
                                "text": {"tag": "lark_md", "content": body[:1700]},
                            },
                            {
                                "tag": "action",
                                "actions": [
                                    {
                                        "tag": "button",
                                        "text": {
                                            "tag": "plain_text",
                                            "content": "打开指挥联动台",
                                        },
                                        "type": "primary",
                                        "url": deep_link
                                        or "http://192.168.43.240:8080/os_council.html",
                                    }
                                ],
                            },
                        ],
                    }
                ),
            }
            req = _ur.Request(
                "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer " + tat,
                },
                method="POST",
            )
            with safe_urlopen(req, timeout=15) as r:
                d = json.loads(r.read().decode("utf-8"))
                rec["sent"] = d.get("code") == 0
                rec["channel"] = "feishu-openapi"
                rec["code"] = d.get("code")
    except Exception as e:
        rec["err"] = str(e)[:150]
    _append(OUTBOX, rec)
    return rec


# ---------------- 工单直通桥（定稿 tasks → devtask） ----------------
DEVTASK = os.path.join(REPO, "tools", "devtask.py")


def dispatch_to_devtask(tasks: list[dict], meeting_id: str) -> list[dict]:
    """定稿任务→devtask 工单（执行侧落地；失败如实记录不静默）。"""
    import subprocess

    results = []
    for t in tasks:
        owner = t.get("owner", "TBD")
        if owner in ("TBD", "?", ""):
            results.append(
                {
                    "task": t.get("text", ""),
                    "dispatched": False,
                    "why": "owner 未定——留督促台人工认领",
                }
            )
            continue
        tid = f"CM-{meeting_id}-{owner[:6].upper()}"
        desc = f"[{meeting_id}] {t.get('text', '')}" + (
            f"（期限 {t['due']}）" if t.get("due") else ""
        )
        try:
            r = subprocess.run(
                ["python", DEVTASK, "new", tid, desc],
                capture_output=True,
                timeout=60,
                check=False,
                cwd=REPO,
            )
            ok = r.returncode == 0 and "已开" in r.stdout.decode(
                "utf-8", errors="replace"
            )
            results.append(
                {
                    "task": t.get("text", ""),
                    "tid": tid,
                    "dispatched": ok,
                    "why": ""
                    if ok
                    else r.stderr.decode("utf-8", errors="replace")[:120],
                }
            )
        except Exception as e:
            results.append(
                {"task": t.get("text", ""), "dispatched": False, "why": str(e)[:120]}
            )
    return results


class CouncilServer(ThreadingHTTPServer):
    def __init__(self, address, password: str, allowed_origins: frozenset[str]):
        if len(password) < 16:
            raise ValueError("COUNCIL_OPS_PASSWORD 至少需要 16 个字符")
        if not _loopback(address[0]):
            raise ValueError("COUNCIL_OPS_BIND 必须是 loopback IP")
        self.allowed_origins = allowed_origins
        self.password_salt = secrets.token_bytes(16)
        self.password_digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), self.password_salt, 200_000
        )
        self.sessions: dict[str, float] = {}
        self.session_lock = threading.Lock()
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # 静默
        pass

    def _cors(self):
        origin = self.headers.get("Origin")
        if origin in self.server.allowed_origins:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header("Vary", "Origin")

    def _json(self, obj, status: int = 200, cookie: str | None = None):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if cookie is not None:
            self.send_header("Set-Cookie", cookie)
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _local_peer(self) -> bool:
        if _loopback(self.client_address[0]):
            return True
        self._json({"error": "forbidden"}, 403)
        return False

    def _authenticated(self) -> bool:
        try:
            cookies = SimpleCookie()
            cookies.load(self.headers.get("Cookie", ""))
            token = cookies[SESSION_COOKIE].value if SESSION_COOKIE in cookies else ""
        except (CookieError, ValueError):
            token = ""
        if token:
            digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
            with self.server.session_lock:
                expiry = self.server.sessions.get(digest, 0)
                if expiry > time.monotonic():
                    return True
                self.server.sessions.pop(digest, None)
        self._json({"error": "unauthorized"}, 401)
        return False

    def do_OPTIONS(self):
        if not self._local_peer():
            return
        origin = self.headers.get("Origin")
        if origin not in self.server.allowed_origins:
            return self._json({"error": "forbidden origin"}, 403)
        # CORS 预检不携带 Cookie；仅返回许可元数据，不执行受保护路由。
        self.send_response(204)
        self._cors()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if not self._local_peer():
            return
        u = urllib.parse.urlparse(self.path)
        if u.path == "/healthz":
            return self._json({"ok": True, "at": _now()})
        if not self._authenticated():
            return
        if u.path == "/api/council/state":
            st = load_state()
            st["archives"] = load_archives()
            st["tasks"] = load_tasks()
            if os.path.isfile(REGISTRY_SNIPPET):
                try:
                    cur = json.load(open(REGISTRY_SNIPPET, encoding="utf-8"))
                    if cur:
                        st["experts"] = summon_experts(
                            cur[-1].get("topic", "") + " " + cur[-1].get("summary", "")
                        )
                except (OSError, json.JSONDecodeError) as exc:
                    logging.getLogger(__name__).warning(
                        "silent-except %s: %s", "council_ops.py", exc
                    )
            st["theme"] = load_theme()
            return self._json(st)
        q = urllib.parse.parse_qs(u.query).get("q", [""])[0]
        if u.path == "/api/council/search":
            return self._json({"q": q, "hits": search_all(q)})
        if u.path == "/api/content/brief":
            # 内容生产线一屏：gzh/drafts 真数据（推文/选题/状态）+ 待办动作
            out = {"at": _now(), "recent": [], "week_n": 0, "total": 0}
            try:
                idx = json.load(
                    open(
                        os.path.join(REPO, "gzh", "drafts", "index.json"),
                        encoding="utf-8",
                    )
                )
                week = [
                    x
                    for x in idx
                    if str(x.get("date", ""))
                    >= (dt.date.today() - dt.timedelta(days=7)).isoformat()
                ]
                out["week_n"] = len(week)
                out["total"] = len(idx)
                out["recent"] = [
                    {
                        "date": x.get("date"),
                        "title": str(x.get("title", ""))[:30],
                        "col": x.get("col"),
                        "status": x.get("status"),
                    }
                    for x in idx[:6]
                ]
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "silent-except %s: %s", "council_ops.py", exc
                )
            return self._json(out)
        if u.path == "/api/issues":
            try:
                import issues_store as _is

                return self._json(
                    {
                        "ok": True,
                        "stats": _is.stats(),
                        "open": _is.load("open"),
                        "doing": _is.load("doing"),
                        "fixed": _is.load("fixed", n=15),
                        "archived": _is.load("archived", n=10),
                    }
                )
            except Exception as e:
                return self._json({"error": str(e)[:120]}, 500)
        if u.path == "/api/contacts/brief":
            # 商业关系库：联系人(contacts.jsonl)+文档盘点(deliverables)
            out = {"at": _now(), "contacts": [], "docs": []}
            try:
                import contacts_store as _cs

                out["contacts"] = _cs.load(12)
                out["docs"] = _cs.docs_inventory()[:6]
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "silent-except %s: %s", "council_ops.py", exc
                )
            return self._json(out)
        if u.path == "/api/tech/brief":
            # 科技前沿：github trending + arXiv cs.AI
            out = {"at": _now(), "recent": []}
            try:
                import tech_feed as _tf

                out["recent"] = _tf.load_recent(10)
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "silent-except %s: %s", "council_ops.py", exc
                )
            return self._json(out)
        if u.path == "/api/env/brief":
            # 环境日程域：天气(账本)+议会厅预约(未来日程)
            out = {"at": _now(), "weather": None, "sched": []}
            try:
                import weather_store as _ws

                out["weather"] = _ws.load()
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "silent-except %s: %s", "council_ops.py", exc
                )
            try:
                rows = []
                if os.path.isfile(SCHEDULED):
                    with open(SCHEDULED, encoding="utf-8") as f:
                        for line in f:
                            try:
                                rows.append(json.loads(line))
                            except json.JSONDecodeError:
                                continue
                out["sched"] = [
                    {"title": r.get("title"), "for": r.get("for")} for r in rows[-5:]
                ][::-1]
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "silent-except %s: %s", "council_ops.py", exc
                )
            return self._json(out)
        if u.path == "/api/intel/policy":
            # 政策地缘域真数据（gov.cn 政策文件库采集账本）
            out = {"at": _now(), "recent": []}
            try:
                import policy_geo_collector as _pg

                out["recent"] = _pg.load_recent(8)
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "silent-except %s: %s", "council_ops.py", exc
                )
            return self._json(out)
        if u.path == "/api/intel/brief":
            # 情报一屏：盘面(premarket) + 情报对账(guojia) + 最新裁定(verdicts) —— 情报视角驾驶舱
            out = {"at": _now(), "market": None, "intel": None, "verdicts": []}
            try:
                pm = os.path.join(
                    REPO, "quant_backtest", "reports", "premarket_latest.json"
                )
                d = json.load(open(pm, encoding="utf-8"))
                alerts = [str(x)[:70] for x in (d.get("alerts") or [])[:4]]
                out["market"] = {
                    "ts": d.get("ts"),
                    "overall": d.get("overall"),
                    "alerts": alerts,
                }
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "silent-except %s: %s", "council_ops.py", exc
                )
            try:
                g = json.load(
                    open(
                        r"C://aios//news_collector//data//guojia//summary.json",
                        encoding="utf-8",
                    )
                )
                o = g.get("overall", {})
                mk = g.get("markets", {})
                out["intel"] = {
                    "generated_at": g.get("generated_at"),
                    "hit_rate": o.get("hit_rate"),
                    "n": o.get("n_reconciled"),
                    "markets": {
                        k: v.get("name", k)
                        for k, v in mk.items()
                        if isinstance(v, dict)
                    },
                }
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "silent-except %s: %s", "council_ops.py", exc
                )
            try:
                vs = []
                with open(
                    r"C://aios//news_collector//data//guojia//verdicts.jsonl",
                    encoding="utf-8",
                ) as f:
                    for line in f:
                        try:
                            vs.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
                out["verdicts"] = [
                    {
                        "day": v.get("day"),
                        "scope": v.get("scope"),
                        "bias": v.get("bias"),
                        "conf": v.get("conf"),
                    }
                    for v in vs[-6:]
                ][::-1]
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "silent-except %s: %s", "council_ops.py", exc
                )
            return self._json(out)
        mid = urllib.parse.parse_qs(u.query).get("mid", [""])[0]
        if u.path == "/api/council/minutes":
            import re as _re2

            if not mid or not _re2.match(r"^[\w-]+$", mid):
                return self._json({"error": "mid required"}, 400)
            for month in (
                os.listdir(ARCHIVE_ROOT) if os.path.isdir(ARCHIVE_ROOT) else []
            ):
                mdir2 = os.path.join(ARCHIVE_ROOT, month)
                for name in os.listdir(mdir2) if os.path.isdir(mdir2) else []:
                    if mid in name:
                        fp = os.path.join(mdir2, name, "minutes.md")
                        if os.path.isfile(fp):
                            with open(fp, encoding="utf-8") as f:
                                return self._json(
                                    {
                                        "ok": True,
                                        "mid": mid,
                                        "minutes": f.read()[:20000],
                                    }
                                )
            return self._json({"error": "not found"}, 404)
        return self._json({"error": "not found"}, 404)

    def do_POST(self):
        if not self._local_peer():
            return
        if self.headers.get("Origin") not in self.server.allowed_origins:
            return self._json({"error": "forbidden origin"}, 403)
        u = urllib.parse.urlparse(self.path)
        if u.path != "/api/council/auth/login" and not self._authenticated():
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._json({"error": "bad length"}, 400)
        if n < 0 or (u.path == "/api/council/auth/login" and n > 4096):
            return self._json({"error": "bad length"}, 400)
        if u.path == "/api/council/upload":
            return self._upload(n)
        body = self.rfile.read(n) if n else b"{}"
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._json({"error": "bad json"}, 400)
        if u.path == "/api/council/auth/login":
            if not isinstance(data, dict) or not isinstance(data.get("password"), str):
                return self._json({"error": "invalid credentials"}, 401)
            digest = hashlib.pbkdf2_hmac(
                "sha256",
                data["password"].encode("utf-8"),
                self.server.password_salt,
                200_000,
            )
            if not hmac.compare_digest(digest, self.server.password_digest):
                return self._json({"error": "invalid credentials"}, 401)
            token = secrets.token_urlsafe(32)
            with self.server.session_lock:
                now = time.monotonic()
                self.server.sessions = {
                    k: v for k, v in self.server.sessions.items() if v > now
                }
                self.server.sessions[
                    hashlib.sha256(token.encode("utf-8")).hexdigest()
                ] = now + SESSION_TTL
            cookie = f"{SESSION_COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_TTL}"
            return self._json({"ok": True}, cookie=cookie)
        if u.path == "/api/council/debate":
            topic = str(data.get("topic") or "").strip()[:120]
            if not topic:
                return self._json({"error": "topic required"}, 400)
            result = run_debate(topic)
            # 全部落对话流
            for t in result["transcript"]:
                if t["view"]:
                    _append(
                        MESSAGES,
                        {
                            "at": _now(),
                            "topic": topic,
                            "seat": t["seat"],
                            "kind": "say",
                            "text": f"[R{t['round']}] {t['view']}",
                        },
                    )
            ch = result["chair"]
            _append(
                MESSAGES,
                {
                    "at": _now(),
                    "topic": topic,
                    "seat": ch["seat"],
                    "kind": "sys",
                    "text": f"⚖️ 轮值主席（{ch['cn']}·今日值班）总结：{result['conclusion']}",
                },
            )
            for cn, v in result["votes"].items():
                if v:
                    _append(
                        MESSAGES,
                        {
                            "at": _now(),
                            "topic": topic,
                            "seat": "vote",
                            "kind": "say",
                            "text": f"🗳 {cn} 投票：{v}",
                        },
                    )
            _append(
                MESSAGES,
                {
                    "at": _now(),
                    "topic": topic,
                    "seat": "laotian",
                    "kind": "sys",
                    "text": "🤖 辩论已收敛（3~5 轮上限）。结论在上方——认可说「定稿：…」采纳，或继续一轮说「再辩一轮」。",
                },
            )
            return self._json(
                {
                    "ok": True,
                    "chair": ch,
                    "conclusion": result["conclusion"],
                    "votes": result["votes"],
                }
            )
        if u.path == "/api/council/theme":
            name = str(data.get("name") or "").strip()[:40]
            if not name:
                # 列出可用主题
                tdir = os.path.join(HERE, "themes")
                names = (
                    [f[:-5] for f in os.listdir(tdir) if f.endswith(".json")]
                    if os.path.isdir(tdir)
                    else ["sanguo"]
                )
                return self._json(
                    {
                        "ok": True,
                        "themes": names,
                        "current": load_theme().get("name", "sanguo"),
                    }
                )
            src = os.path.join(HERE, "themes", name + ".json")
            if not os.path.isfile(src):
                return self._json({"error": "主题不存在：" + name}, 404)
            fd, tmp = tempfile.mkstemp(dir=DATA_DIR, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(open(src, encoding="utf-8").read())
            os.replace(tmp, THEME_PATH)
            return self._json({"ok": True, "applied": name})
        if u.path == "/api/council/idea_discuss":
            # 想法对答（页面在 idea 意图后异步调用）：三席位观点互补入对话流
            idea = str(data.get("idea") or "").strip()[:500]
            if not idea:
                return self._json({"error": "idea required"}, 400)
            views = auto_discuss(idea)
            for v in views:
                if v["view"]:
                    _append(
                        MESSAGES,
                        {
                            "at": _now(),
                            "topic": "💡 想法池",
                            "seat": v["seat"],
                            "kind": "say",
                            "text": v["view"],
                        },
                    )
            # 诸葛亮建议面由规则代行（载体可用但不走模型，如实标注）
            _append(
                MESSAGES,
                {
                    "at": _now(),
                    "topic": "💡 想法池",
                    "seat": "zhuge",
                    "kind": "say",
                    "text": "（诸葛亮·建议面代行·规则代行非模型）想法价值在于先承载：入池后不定进度，与现有工作不抢资源；若后续要执行，从池中一键转专项会议即可。",
                },
            )
            _append(
                MESSAGES,
                {
                    "at": _now(),
                    "topic": "💡 想法池",
                    "seat": "laotian",
                    "kind": "sys",
                    "text": "🤖 归档确认：这个想法已存「💡想法池」。要不要挂到某个专项？直接说「把它转成专项会议」或就留在池里——留池即默认，无需操作。",
                },
            )
            return self._json({"ok": True, "views": views})
        if u.path == "/api/council/idea_promote":
            return self._json({"error": "先到会前问答确认配置，再提交建会"}, 409)
        if False:  # 旧想法直升会议实现保留作迁移参考，不作为可达写入入口
            # 池中想法 → 专项会议（后面想执行时）
            idea = str(data.get("idea") or "").strip()[:500]
            if not idea:
                return self._json({"error": "idea required"}, 400)
            tid = dt.datetime.now().strftime("%m%d-%H%M")
            topic = {
                "topic": f"💡→🎯 {idea[:30]}（#{tid}）",
                "kind": "project",
                "tags": ["govern"],
                "stage": "立题",
                "originator": "laotian",
                "summary": f"想法转专项 {tid}",
                "created_at": _now(),
            }
            _append(
                MESSAGES,
                {
                    "at": _now(),
                    "topic": topic["topic"],
                    "seat": "laotian",
                    "kind": "new",
                    "text": f"🎯 想法转专项：「{idea}」",
                },
            )
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
            # 池中状态翻转
            if os.path.isfile(IDEA_POOL):
                lines = open(IDEA_POOL, encoding="utf-8").read().splitlines()
                for i, line in enumerate(lines):
                    try:
                        r = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if r.get("idea") == idea:
                        r["status"] = "promoted"
                        r["promoted_at"] = _now()
                        lines[i] = json.dumps(r, ensure_ascii=False)
                _ = tempfile.mkstemp(dir=DATA_DIR, suffix=".tmp")
                with open(IDEA_POOL, "w", encoding="utf-8") as f:
                    f.write("\n".join(lines) + "\n")
            return self._json(
                {"ok": True, "topic": topic, "routed": "已转专项：" + topic["topic"]}
            )
        if u.path == "/api/council/archive":
            # v5：一句话定稿——text 一段话，AI 规则拆决议+任务（缺 owner 标 TBD 不编造）
            text = str(data.get("text") or "").strip()[:2000]
            topic = str(data.get("topic") or "").strip()[:120]
            if text and any(k in text for k in _ACTION_ARCHIVE) or data.get("decision"):
                parsed = parse_archive(text) if text else {}
                decision = str(
                    data.get("decision") or parsed.get("decision") or ""
                ).strip()[:2000]
                raw_tasks = data.get("tasks") or parsed.get("tasks") or []
            else:
                decision = str(data.get("decision") or "").strip()[:2000]
                raw_tasks = data.get("tasks") or []
            if not topic:
                return self._json({"error": "topic required"}, 400)
            if not isinstance(raw_tasks, list):
                raw_tasks = []
            tasks = [
                {
                    "owner": str(t.get("owner", "TBD"))[:20],
                    "text": str(t.get("text", ""))[:300],
                    "due": str(t.get("due", ""))[:20],
                }
                for t in raw_tasks[:20]
                if isinstance(t, dict)
            ]
            return self._json(archive_meeting(topic, decision, tasks))
        if u.path == "/api/council/archive_preview":
            # 预览：一句话→决议+任务（不落盘，用户确认人性化闭环）
            text = str(data.get("text") or "").strip()[:2000]
            if not text:
                return self._json({"error": "text required"}, 400)
            return self._json({"ok": True, "parsed": parse_archive(text)})
        if u.path == "/api/council/task_update":
            meeting_id = str(data.get("meeting_id") or "").strip()
            idx = data.get("idx")
            status = str(data.get("status") or "").strip()[:12]
            if (
                not meeting_id
                or idx is None
                or status not in ("todo", "doing", "done", "dropped")
            ):
                return self._json(
                    {"error": "meeting_id/idx/status 合法值 required"}, 400
                )
            for month in (
                os.listdir(ARCHIVE_ROOT) if os.path.isdir(ARCHIVE_ROOT) else []
            ):
                mdir = os.path.join(ARCHIVE_ROOT, month)
                if not os.path.isdir(mdir):
                    continue
                for name in os.listdir(mdir):
                    fdir = os.path.join(mdir, name)
                    tp = os.path.join(fdir, "tasks.jsonl")
                    if not os.path.isfile(tp) or not meeting_id in name:
                        continue
                    lines = open(tp, encoding="utf-8").read().splitlines()
                    if 0 <= int(idx) < len(lines):
                        rec = json.loads(lines[idx])
                        rec["status"] = status
                        rec["updated"] = _now()
                        lines[idx] = json.dumps(rec, ensure_ascii=False)
                        fd, tmp = tempfile.mkstemp(dir=fdir)
                        with os.fdopen(fd, "w", encoding="utf-8") as f:
                            f.write("\n".join(lines) + "\n")
                        os.replace(tmp, tp)
                        return self._json({"ok": True, "task": rec})
            return self._json({"error": "task not found"}, 404)
        if u.path == "/api/council/natural":
            text = str(data.get("text") or "").strip()[:500]
            current_topic = str(data.get("current_topic") or "").strip()[:120]
            if not text:
                return self._json({"error": "text required"}, 400)
            # ── 意图路由；新会/预约/想法升级意图只拒绝，不在自然接口写议题 ──
            intent = None
            if any(k in text for k in _ACTION_ARCHIVE):
                intent = "archive"
            elif any(
                k in text
                for k in ("查一下", "查查", "搜索", "搜一下", "谁说", "找回", "检索")
            ):
                intent = "search"
            elif any(k in text for k in _ACTION_SCHED) or ("预约" in text):
                intent = "schedule"
            elif any(
                k in text
                for k in (
                    "转成专项",
                    "转专项",
                    "把它转",
                    "立项这个",
                    "开始执行这个想法",
                    "想法转",
                )
            ):
                intent = "promote"
            elif any(
                k in text for k in ("辩一辩", "辩论", "让他们辩", "辩一轮", "再辩")
            ):
                intent = "debate"
            elif current_topic and not any(
                k in text for k in ("开会", "发起", "新会议", "开个会")
            ):
                intent = "say"
            else:
                intent = "new"
            if intent == "debate":
                topic = current_topic or _latest_active_topic()
                if not topic:
                    return self._json({"error": "没有可辩论的议题——先开会"}, 400)
                _append(
                    MESSAGES,
                    {
                        "at": _now(),
                        "topic": topic,
                        "seat": "laotian",
                        "kind": "sys",
                        "text": "⚖️ 启动辩论收敛协议：R1 初表态 → R2 反驳 → 轮值主席总结 + 投票（约 2-4 分钟）",
                    },
                )
                result = run_debate(topic)
                for t in result["transcript"]:
                    if t["view"]:
                        _append(
                            MESSAGES,
                            {
                                "at": _now(),
                                "topic": topic,
                                "seat": t["seat"],
                                "kind": "say",
                                "text": f"[R{t['round']}] {t['view']}",
                            },
                        )
                ch = result["chair"]
                _append(
                    MESSAGES,
                    {
                        "at": _now(),
                        "topic": topic,
                        "seat": ch["seat"],
                        "kind": "sys",
                        "text": f"⚖️ 轮值主席（{ch['cn']}·今日值班）总结：{result['conclusion']}",
                    },
                )
                for cn, v in result["votes"].items():
                    if v:
                        _append(
                            MESSAGES,
                            {
                                "at": _now(),
                                "topic": topic,
                                "seat": "vote",
                                "kind": "say",
                                "text": f"🗳 {cn} 投票：{v}",
                            },
                        )
                _append(
                    MESSAGES,
                    {
                        "at": _now(),
                        "topic": topic,
                        "seat": "laotian",
                        "kind": "sys",
                        "text": "🤖 辩论收敛完成。认可说「定稿：…」；继续说「再辩一轮」。",
                    },
                )
                return self._json(
                    {
                        "ok": True,
                        "intent": "debate",
                        "chair": ch,
                        "conclusion": result["conclusion"],
                        "votes": result["votes"],
                        "routed": "⚖️ 辩论收敛完成——轮值主席 "
                        + ch["cn"]
                        + " 已总结，投票已入流",
                    }
                )
            if intent in ("new", "schedule", "promote"):
                return self._json(
                    {
                        "error": "请先完成会前问答并确认配置，再创建或预约会议",
                        "intent": "preflight_required",
                    },
                    409,
                )
            if intent == "promote":
                idea = _re.sub(r"^(把想法池里的?|把这个想法|把|想法池里的)", "", text)
                idea = _re.sub(
                    r"(转成专项会议|转成专项|转专项|立项|开始执行|想法)", "", idea
                ).strip("：: 「」 ")[:200]
                if not idea:
                    idea = "（未指明——用池中最新想法）"
                r = {"idea": idea}
                tid = dt.datetime.now().strftime("%m%d-%H%M")
                topic = {
                    "topic": f"💡→🎯 {idea[:30]}（#{tid}）",
                    "kind": "project",
                    "tags": ["govern"],
                    "stage": "立题",
                    "originator": "laotian",
                    "summary": f"想法转专项 {tid}",
                    "created_at": _now(),
                }
                _append(
                    MESSAGES,
                    {
                        "at": _now(),
                        "topic": topic["topic"],
                        "seat": "laotian",
                        "kind": "new",
                        "text": f"🎯 想法转专项（老田指令）：「{idea}」——按赵云/田丰对答意见推进",
                    },
                )
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
                return self._json(
                    {
                        "ok": True,
                        "intent": "promote",
                        "topic": topic,
                        "routed": "🎯 已转专项：" + topic["topic"] + "（对答观点随行）",
                    }
                )
            # 想法识别：想法词 + 无当前议题 或 显式开想法会 → idea（承载不执行，三席位自动对答）
            if any(k in text for k in _IDEA_CUES) and intent in ("new", "say"):
                intent = "idea"
            if intent == "idea":
                idea_text = (
                    _re.sub(
                        r"^(我有个想法|有个想法|想法[:：]|我觉得可以|要是能|不如|可以搞)",
                        "",
                        text,
                    ).strip()
                    or text
                )
                rec = {
                    "at": _now(),
                    "idea": idea_text,
                    "status": "parked",
                    "note": "随口想法·承载不执行——后面想做时再启动",
                }
                _append(IDEA_POOL, rec)
                _append(
                    MESSAGES,
                    {
                        "at": _now(),
                        "topic": "💡 想法池",
                        "seat": "laotian",
                        "kind": "new",
                        "text": f"💡 想法入池：「{idea_text}」（只承载，不执行）",
                    },
                )
                return self._json(
                    {
                        "ok": True,
                        "intent": "idea",
                        "routed": "💡 想法已入池（承载不执行）——正在请赵云/田丰进场对答…",
                        "idea": rec,
                        "discuss_async": True,
                    }
                )
            if intent == "archive":
                topic = current_topic or _latest_active_topic()
                if not topic:
                    return self._json({"error": "没有可定稿的议题——先开会"}, 400)
                parsed = parse_archive(text)
                tasks = [
                    {"owner": t["owner"], "text": t["text"], "due": t["due"]}
                    for t in parsed["tasks"]
                ]
                rec = archive_meeting(topic, parsed["decision"], tasks)
                return self._json(
                    {
                        "ok": True,
                        "intent": "archive",
                        "routed": "已定稿归档 " + rec["meeting_id"],
                        "detail": {"decision": parsed["decision"], "tasks": tasks},
                    }
                )
            if intent == "search":
                q = _re.sub(r"^(查一下|查查|搜索|搜一下|检索|帮我|找回)", "", text)
                q = _re.sub(r"(谁说过?|一下|什么|吗|呢)", "", q).strip() or text[:20]
                hits = search_all(q)
                _append(
                    MESSAGES,
                    {
                        "at": _now(),
                        "topic": current_topic or "(查询)",
                        "seat": "laotian",
                        "kind": "sys",
                        "text": f"🔍 检索「{q}」：命中 {len(hits)} 条"
                        + (
                            "".join(
                                "\n· [" + h["src"] + "] " + h["ctx"][:60]
                                for h in hits[:3]
                            )
                        ),
                    },
                )
                return self._json(
                    {
                        "ok": True,
                        "intent": "search",
                        "routed": f"检索命中 {len(hits)} 条（对话流见结果）",
                        "hits": hits[:5],
                    }
                )
            if intent == "schedule":
                parsed = parse_natural(text)
                if not parsed["at"]:
                    intent, parsed = "new", parse_natural(text)
                else:
                    rec = {
                        "at": _now(),
                        "title": parsed["title"],
                        "for": parsed["at"],
                        "seats": parsed["seats"],
                        "raw": text,
                        "status": "scheduled",
                    }
                    _append(SCHEDULED, rec)
                    _append(
                        MESSAGES,
                        {
                            "at": _now(),
                            "topic": parsed["title"],
                            "seat": "laotian",
                            "kind": "new",
                            "text": f"🎤 预约：「{text}」→ {parsed['at']}，参与：{'、'.join(parsed['seats']) or '全体'}",
                        },
                    )
                    notify(
                        f"📅 会议已预约 {parsed['at']}",
                        f"**{parsed['title']}**\n参与：{'、'.join(parsed['seats']) or '全体'}\n到点自动立题开会",
                    )
                    return self._json(
                        {
                            "ok": True,
                            "intent": "schedule",
                            "routed": f"已预约 {parsed['at']} · {'、'.join(parsed['seats']) or '全体'} · {parsed['title']}",
                        }
                    )
            if intent == "say":
                _append(
                    MESSAGES,
                    {
                        "at": _now(),
                        "topic": current_topic,
                        "seat": str(data.get("seat") or "laotian")[:20],
                        "kind": "say",
                        "text": text,
                    },
                )
                return self._json(
                    {
                        "ok": True,
                        "intent": "say",
                        "routed": "已发言（" + current_topic[:16] + "…）",
                    }
                )
            # new：防抖——同句 10 秒内不重复建会
            nowts = dt.datetime.now().timestamp()
            if (
                getattr(Handler, "_last_new", None)
                and Handler._last_new[0] == text
                and nowts - Handler._last_new[1] < 10
            ):
                return self._json(
                    {
                        "ok": True,
                        "intent": "new",
                        "routed": "（重复指令已忽略）",
                        "dup": True,
                        "topic": {"topic": Handler._last_new[2]},
                    }
                )
            parsed = parse_natural(text)
            kind = classify_meeting(text)
            tid = dt.datetime.now().strftime("%m%d-%H%M")
            topic = {
                "topic": f"{parsed['title']}（#{tid}）",
                "kind": kind,
                "tags": parsed["seats"]
                or (["daily"] if kind == "daily" else ["govern"]),
                "stage": "立题",
                "originator": "laotian",
                "summary": f"🎤 发起 {tid}"
                + (f" · {parsed['at']}" if parsed["at"] else ""),
                "created_at": _now(),
            }
            _append(
                MESSAGES,
                {
                    "at": _now(),
                    "topic": topic["topic"],
                    "seat": "laotian",
                    "kind": "new",
                    "text": ("☕" if kind == "daily" else "🎯")
                    + (" 日常会议" if kind == "daily" else " 专项会议")
                    + f"自动归类：「{text}」"
                    + (f" · {parsed['at']}" if parsed["at"] else ""),
                },
            )
            experts = summon_experts(text)
            if experts:
                _append(
                    MESSAGES,
                    {
                        "at": _now(),
                        "topic": topic["topic"],
                        "seat": "summon",
                        "kind": "sys",
                        "text": "🔔 智能召唤列席："
                        + "；".join(
                            e["who"] + "〔" + e["domain"] + "〕" for e in experts
                        )
                        + "——相关领域席位请入场表态",
                    },
                )
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
            Handler._last_new = (text, nowts, topic["topic"])
            return self._json(
                {
                    "ok": True,
                    "intent": "new",
                    "kind": kind,
                    "routed": (
                        "☕ 日常会议（轻流程，随聊随记）："
                        if kind == "daily"
                        else "🎯 专项会议（六阶段）："
                    )
                    + topic["topic"],
                    "topic": topic,
                }
            )
        if u.path == "/api/issues":
            act = data.get("act")
            try:
                import issues_store as _is

                if act == "add":
                    r = _is.add(
                        str(data.get("title", "")),
                        str(data.get("severity", "P2")),
                        str(data.get("board", "OS")),
                        str(data.get("owner", "")),
                        str(data.get("evidence", "")),
                    )
                    _append(
                        MESSAGES,
                        {
                            "at": _now(),
                            "topic": "OS问题账本",
                            "seat": str(data.get("seat", "laotian"))[:20],
                            "kind": "sys",
                            "text": "🆕 问题登记 "
                            + r["id"]
                            + " ["
                            + r["severity"]
                            + "] "
                            + r["title"][:40],
                        },
                    )
                    return self._json({"ok": True, "issue": r})
                if act == "update":
                    r = _is.update(
                        str(data.get("id", "")),
                        status=data.get("status"),
                        owner=data.get("owner"),
                        note=str(data.get("note", "")),
                    )
                    ev = {
                        "fixed": "✅ 已解决（待归档）",
                        "archived": "📦 已清除归档",
                        "doing": "🔧 转进行中",
                    }.get(str(data.get("status") or ""), "更新")
                    note_s = str(data.get("note") or "")[:60]
                    _append(
                        MESSAGES,
                        {
                            "at": _now(),
                            "topic": "OS问题账本",
                            "seat": str(data.get("seat", "laotian"))[:20],
                            "kind": "sys",
                            "text": ev
                            + " "
                            + r["id"]
                            + " "
                            + r["title"][:32]
                            + ((" · " + note_s) if note_s else ""),
                        },
                    )
                    return self._json({"ok": True, "issue": r})
                return self._json({"error": "act?"}, 400)
            except Exception as e:
                return self._json({"error": str(e)[:120]}, 400)
        if u.path == "/api/council/preflight/confirm":
            key = str(data.get("request_id") or "").strip()
            title = str(data.get("title") or "").strip()[:80]
            scope = str(data.get("scope") or "").strip()[:160]
            if (
                not re.fullmatch(
                    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", key
                )
                or not title
                or not scope
                or data.get("confirmed") is not True
            ):
                return self._json(
                    {"error": "request_id、议题、范围与本人确认均必需"}, 400
                )
            if data.get("meeting_kind") == "schedule" or data.get("scheduled_for"):
                return self._json(
                    {"error": "预约确认与时间核验尚未接入，不得创建即时会议"}, 409
                )
            mode = str(data.get("mode") or "待确认")[:20]
            goal = str(data.get("goal") or "待确认")[:160]
            humans = str(data.get("humans") or "待确认")[:160]
            candidates = (
                [str(s)[:30] for s in data.get("candidates", []) if isinstance(s, str)][
                    :60
                ]
                if isinstance(data.get("candidates", []), list)
                else []
            )
            payload = {
                "title": title,
                "scope": scope,
                "mode": mode,
                "goal": goal,
                "humans": humans,
                "candidates": candidates,
            }
            with MEETING_WRITE_LOCK:
                ledger = {}
                if os.path.isfile(PREFLIGHT):
                    try:
                        with open(PREFLIGHT, encoding="utf-8") as f:
                            ledger = json.load(f)
                    except (OSError, json.JSONDecodeError):
                        return self._json(
                            {"error": "会前确认账本无法读取，已停止写入"}, 503
                        )
                existing = ledger.get(key)
                if existing:
                    if existing.get("payload") != payload:
                        return self._json(
                            {"error": "同一确认编号的配置已变更，请核对原记录"}, 409
                        )
                    if not existing.get("config_recorded"):
                        recorded = False
                        if os.path.isfile(MESSAGES):
                            with open(MESSAGES, encoding="utf-8") as f:
                                for line in f:
                                    try:
                                        msg = json.loads(line)
                                    except json.JSONDecodeError:
                                        continue
                                    if (
                                        msg.get("request_id") == key
                                        and msg.get("kind") == "config"
                                    ):
                                        recorded = True
                                        break
                        if not recorded:
                            _append(
                                MESSAGES,
                                {
                                    "at": _now(),
                                    "topic": existing["topic"]["topic"],
                                    "seat": "laotian",
                                    "kind": "config",
                                    "request_id": key,
                                    "text": f"会前确认配置：会议方式：{mode}；讨论范围：{scope}；预期产出：{goal}；"
                                    f"人类参与者（拟）：{humans}；AI候选（拟，未通知）：{'、'.join(candidates) or '待确认'}。",
                                },
                            )
                        existing["config_recorded"] = True
                        fd, tmp = tempfile.mkstemp(dir=DATA_DIR, suffix=".tmp")
                        try:
                            with os.fdopen(fd, "w", encoding="utf-8") as f:
                                json.dump(ledger, f, ensure_ascii=False, indent=1)
                            os.replace(tmp, PREFLIGHT)
                        finally:
                            if os.path.exists(tmp):
                                os.unlink(tmp)
                    return self._json(
                        {
                            "ok": True,
                            "topic": existing["topic"],
                            "duplicate": True,
                            "config_recorded": existing["config_recorded"],
                        }
                    )
                cur = []
                if os.path.isfile(REGISTRY_SNIPPET):
                    try:
                        with open(REGISTRY_SNIPPET, encoding="utf-8") as f:
                            cur = json.load(f)
                    except (OSError, json.JSONDecodeError):
                        return self._json(
                            {"error": "议题账本无法读取，已停止写入"}, 503
                        )
                recovered = next((t for t in cur if t.get("request_id") == key), None)
                if recovered:
                    if recovered.get("preflight_payload") != payload:
                        return self._json(
                            {"error": "原确认配置不可核对，已停止恢复，请人工复核议题"},
                            409,
                        )
                    ledger[key] = {
                        "payload": payload,
                        "topic": recovered,
                        "config_recorded": False,
                    }
                    fd, tmp = tempfile.mkstemp(dir=DATA_DIR, suffix=".tmp")
                    try:
                        with os.fdopen(fd, "w", encoding="utf-8") as f:
                            json.dump(ledger, f, ensure_ascii=False, indent=1)
                        os.replace(tmp, PREFLIGHT)
                    finally:
                        if os.path.exists(tmp):
                            os.unlink(tmp)
                    return self._json(
                        {
                            "error": "已恢复既有议题关联，请用原确认编号重试写入配置",
                            "topic": recovered,
                        },
                        503,
                    )
                topic = {
                    "topic": f"{title}（#{dt.datetime.now().strftime('%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}）",
                    "tags": ["govern"],
                    "stage": "立题",
                    "originator": "laotian",
                    "summary": "会前配置已确认；候选未通知",
                    "created_at": _now(),
                    "request_id": key,
                    "preflight_payload": payload,
                }
                cur.append(topic)
                os.makedirs(DATA_DIR, exist_ok=True)
                fd, tmp = tempfile.mkstemp(dir=DATA_DIR, suffix=".tmp")
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        json.dump(cur, f, ensure_ascii=False, indent=1)
                    os.replace(tmp, REGISTRY_SNIPPET)
                finally:
                    if os.path.exists(tmp):
                        os.unlink(tmp)
                ledger[key] = {
                    "payload": payload,
                    "topic": topic,
                    "config_recorded": False,
                }
                fd, tmp = tempfile.mkstemp(dir=DATA_DIR, suffix=".tmp")
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        json.dump(ledger, f, ensure_ascii=False, indent=1)
                    os.replace(tmp, PREFLIGHT)
                finally:
                    if os.path.exists(tmp):
                        os.unlink(tmp)
                summary = (
                    f"会前确认配置：会议方式：{mode}；讨论范围：{scope}；预期产出：{goal}；"
                    f"人类参与者（拟）：{humans}；AI候选（拟，未通知）：{'、'.join(candidates) or '待确认'}。"
                )
                _append(
                    MESSAGES,
                    {
                        "at": _now(),
                        "topic": topic["topic"],
                        "seat": "laotian",
                        "kind": "config",
                        "text": summary,
                        "request_id": key,
                    },
                )
                ledger[key]["config_recorded"] = True
                fd, tmp = tempfile.mkstemp(dir=DATA_DIR, suffix=".tmp")
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        json.dump(ledger, f, ensure_ascii=False, indent=1)
                    os.replace(tmp, PREFLIGHT)
                finally:
                    if os.path.exists(tmp):
                        os.unlink(tmp)
                return self._json({"ok": True, "topic": topic, "config_recorded": True})
        if u.path == "/api/council/new":
            return self._json({"error": "旧建会入口已关闭；请使用会前确认提交"}, 409)
            title = str(data.get("title") or "").strip()[:80]
            tags = [str(t)[:20] for t in (data.get("tags") or []) if str(t).strip()][:5]
            originator = str(data.get("originator") or "laotian")[:20]
            if not title:
                return self._json({"error": "title required"}, 400)
            tid = dt.datetime.now().strftime("%m%d-%H%M")
            topic = {
                "topic": f"{title}（#{tid}）",
                "tags": tags or ["govern"],
                "stage": "立题",
                "originator": originator,
                "summary": f"老田 {tid} 发起",
                "created_at": _now(),
            }
            _append(
                MESSAGES,
                {
                    "at": _now(),
                    "topic": topic["topic"],
                    "seat": originator,
                    "kind": "new",
                    "text": f"发起议题：{title}",
                },
            )
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
            if str(data.get("kind") or "") == "config":
                return self._json({"error": "配置记录只可由会前确认接口写入"}, 409)
            topic = str(data.get("topic") or "")[:120]
            seat = str(data.get("seat") or "laotian")[:20]
            text = str(data.get("text") or "").strip()[:4000]
            if not text:
                return self._json({"error": "text required"}, 400)
            rec = {
                "at": _now(),
                "topic": topic,
                "seat": seat,
                "kind": str(data.get("kind") or "say"),
                "text": text,
            }
            _append(MESSAGES, rec)
            return self._json({"ok": True, "message": rec})
        if u.path == "/api/council/schedule":
            return self._json({"error": "旧预约入口已关闭；请使用会前确认提交"}, 409)
            title = str(data.get("title") or "").strip()[:80]
            at = str(data.get("at") or "").strip()[:30]
            if not title or not at:
                return self._json({"error": "title/at required"}, 400)
            rec = {
                "at": _now(),
                "title": title,
                "for": at,
                "tags": [str(t)[:20] for t in (data.get("tags") or [])][:5],
                "note": str(data.get("note") or "")[:300],
                "status": "scheduled",
            }
            _append(SCHEDULED, rec)
            return self._json({"ok": True, "schedule": rec})
        return self._json({"error": "not found"}, 404)

    def _upload(self, n: int):
        ct = self.headers.get("Content-Type") or ""
        if "multipart/form-data" not in ct or n <= 0 or n > MAX_UPLOAD + 65536:
            return self._json({"error": "bad upload"}, 400)
        raw = self.rfile.read(n)
        m = re.search(rb'filename="([^"]+)"', raw)
        name = m.group(1).decode("utf-8", errors="replace") if m else "file"
        safe = SAN.sub("_", os.path.basename(name))[:60] or "file"
        ext = os.path.splitext(safe)[1].lower()
        if ext not in ALLOWED_EXT:
            return self._json(
                {"error": f"不支持的类型 {ext}（白名单：文档/图片/表格/代码/日志）"},
                400,
            )
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
        _append(
            MESSAGES,
            {
                "at": _now(),
                "topic": "(上传)",
                "seat": "laotian",
                "kind": "upload",
                "text": f"上传文档：{safe}（{len(body)}B）",
                "path": dest,
            },
        )
        return self._json({"ok": True, "upload": rec})


def _daily_collector_loop():
    """每日 07:35 采集：政策域(gov.cn)+天气(本地账本)。桥常驻自带调度，零系统任务。
    失败只打日志不影响服务；次日重试。"""
    while True:
        try:
            now = dt.datetime.now()
            nxt = (now + dt.timedelta(days=1)).replace(hour=7, minute=35, second=0)
            time.sleep(max(1.0, (nxt - now).total_seconds()))
            try:
                import policy_geo_collector as _pg

                _pg.main()
            except Exception as e:
                print("[daily-collect] policy FAIL:", str(e)[:100], flush=True)
            try:
                import weather_store as _ws

                _ws.refresh()
            except Exception as e:
                print("[daily-collect] weather FAIL:", str(e)[:100], flush=True)
            try:
                import tech_feed as _tf

                _tf.main()
            except Exception as e:
                print("[daily-collect] tech FAIL:", str(e)[:100], flush=True)
            try:
                import contacts_store as _cs2

                for c in _cs2.mine_meetings():
                    _cs2.add(c)
            except Exception as e:
                print("[daily-collect] contacts-mine FAIL:", str(e)[:100], flush=True)
        except Exception:
            time.sleep(3600)


def main() -> int:
    bind = os.environ.get("COUNCIL_OPS_BIND", "127.0.0.1")
    password = os.environ.get("COUNCIL_OPS_PASSWORD", "")
    if len(password) < 16:
        raise ValueError("启动需要 COUNCIL_OPS_PASSWORD，且至少 16 个字符")
    origins = _allowed_origins()
    srv = CouncilServer((bind, 8798), password, origins)
    threading.Thread(target=_daily_collector_loop, daemon=True).start()
    print(
        f"council_ops listening http://{bind}:8798 (会话级进程，Ctrl+C 退出)",
        flush=True,
    )
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
