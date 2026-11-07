# -*- coding: utf-8 -*-
"""个股分析对话内核（C1）——会话 / 意图 / 反问 / 动作。

原则（见 deliverables/2026-11-07_个股分析对话式交互_设计方案_v1.md）：
- 分析完不是结束，而是对话的开始：每轮分析后主动反问「下一步关注什么」；
- 反问是结构化选项 + 自由输入，最多两层，可永久关闭；
- 客户回答即动作：深挖 / 建订阅 / 对比 / 换方向。

本模块纯 Python + SQLite，不依赖 LLM；对 H1 内部网关的调用可注入 AnalysisClient 以便测试。
Portal 路由见 portal/api_dialog.py；PC 页面 portal/sa_chat.html。
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import quote
from urllib.request import Request, urlopen

# ---- 环境 ----
WORK = os.environ.get("ANALYSIS_WORK", r"C:\项目\analysis_work")
DB = os.environ.get("DIALOG_DB") or os.path.join(WORK, "dialog", "dialog.db")
GATEWAY = (os.environ.get("ANALYSIS_GATEWAY_URL") or "http://127.0.0.1:8091").rstrip("/")
TOKEN_FILE = (os.environ.get("ANALYSIS_INTERNAL_TOKEN_FILE")
              or r"C:\项目\commercial\analysis_internal.token")

CODE_RE = re.compile(r"^[0-9]{6}\.(SH|SZ|BJ)$")
RAW_CODE_RE = re.compile(r"(?<!\d)((?:60|68|00|30|92)\d{4})(?!\d)")
MAX_TEXT = 2000

DIMENSIONS = [
    {"key": "risk", "label": "风险与退市", "section": ("风险", "结论")},
    {"key": "valuation", "label": "估值与位置", "section": ("估值", "财务")},
    {"key": "events", "label": "事件与催化", "section": ("事件", "催化")},
    {"key": "flow", "label": "资金与筹码", "section": ("资金", "筹码", "交易")},
    {"key": "compare", "label": "与同行对比", "section": ()},
]
DIM_BY_KEY = {d["key"]: d for d in DIMENSIONS}
STOP_WORDS = ("不用问了", "别问了", "关闭反问", "不要再问", "不用反问")

HELP_TEXT = """我可以做这些事：
- 输入股票代码（如 600337.SH）→ 跑完整分析，并主动问你下一步关注点；
- 分析完成后说「风险」「估值」「事件」「资金」→ 深挖某个方向；
- 说「和 605258.SH 对比」→ 对比两只股票；
- 说「盯着」→ 建立监控订阅（当前 C1 先登记，C4 接入盘中实时提醒）；
- 说「不用问了」→ 关闭反问，之后只做分析不追问。
（研究分析，不构成投资建议）"""


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def new_id(prefix: str) -> str:
    return "%s_%s" % (prefix, uuid.uuid4().hex[:12])


def normalize_code(text: str) -> Optional[str]:
    """识别股票代码；6 位裸代码按板块补交易所后缀。"""
    if not text:
        return None
    m = CODE_RE.search(text.upper())
    if m:
        return m.group(0)
    m = RAW_CODE_RE.search(text)
    if not m:
        return None
    raw = m.group(1)
    if raw.startswith(("60", "68")):
        return raw + ".SH"
    if raw.startswith(("00", "30")):
        return raw + ".SZ"
    return raw + ".BJ"


class Store:
    """SQLite 会话存储（默认 analysis_work/dialog/dialog.db）。"""

    def __init__(self, path: Optional[str] = None):
        self.path = path or DB
        if self.path != ":memory:":
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.con = sqlite3.connect(self.path)
        self.con.row_factory = sqlite3.Row
        self._init()

    def _init(self) -> None:
        self.con.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversations(
              id TEXT PRIMARY KEY, customer_id TEXT, channel TEXT,
              created_at TEXT, last_active_at TEXT, status TEXT DEFAULT 'active');
            CREATE TABLE IF NOT EXISTS sessions(
              id TEXT PRIMARY KEY, conversation_id TEXT, customer_id TEXT,
              title TEXT, stock_code TEXT, job_id TEXT, run_id TEXT,
              state TEXT, depth INTEGER DEFAULT 0, last_dim TEXT DEFAULT '',
              created_at TEXT, updated_at TEXT);
            CREATE TABLE IF NOT EXISTS turns(
              id TEXT PRIMARY KEY, session_id TEXT, role TEXT, content TEXT,
              payload TEXT, created_at TEXT);
            CREATE TABLE IF NOT EXISTS questions(
              id TEXT PRIMARY KEY, session_id TEXT, turn_id TEXT,
              question TEXT, options TEXT, kind TEXT DEFAULT '',
              answer TEXT, answered_at TEXT, created_at TEXT);
            CREATE TABLE IF NOT EXISTS actions(
              id TEXT PRIMARY KEY, session_id TEXT, type TEXT,
              params TEXT, result TEXT, status TEXT, created_at TEXT);
            CREATE TABLE IF NOT EXISTS subscriptions(
              id TEXT PRIMARY KEY, customer_id TEXT, code TEXT,
              dimensions TEXT, active INTEGER DEFAULT 1, created_at TEXT);
            CREATE TABLE IF NOT EXISTS profile(
              customer_id TEXT PRIMARY KEY, prefs TEXT, updated_at TEXT);
            CREATE INDEX IF NOT EXISTS idx_sessions_customer ON sessions(customer_id, updated_at);
            CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_questions_session ON questions(session_id, created_at);
            """
        )
        # 兼容早期无 kind 列的库
        cols = {r[1] for r in self.con.execute("PRAGMA table_info(questions)").fetchall()}
        if "kind" not in cols:
            self.con.execute("ALTER TABLE questions ADD COLUMN kind TEXT DEFAULT ''")
        self.con.commit()

    def close(self) -> None:
        try:
            self.con.close()
        except Exception:
            pass

    # ---- 会话 ----
    def create_session(self, customer_id: str, channel: str, title: str,
                       stock_code: str = "") -> Dict[str, Any]:
        cid = new_id("conv")
        ts = now_str()
        self.con.execute("INSERT INTO conversations VALUES (?,?,?,?,?,'active')",
                         (cid, customer_id, channel, ts, ts))
        sid = new_id("sess")
        self.con.execute(
            "INSERT INTO sessions (id,conversation_id,customer_id,title,stock_code,"
            "job_id,run_id,state,depth,last_dim,created_at,updated_at) "
            "VALUES (?,?,?,?,?,'','','new',0,'',?,?)",
            (sid, cid, customer_id, title, stock_code, ts, ts))
        self.con.commit()
        return self.get_session(sid)

    def get_session(self, sid: str) -> Optional[Dict[str, Any]]:
        row = self.con.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
        return dict(row) if row else None

    def list_sessions(self, customer_id: str, limit: int = 30) -> List[Dict[str, Any]]:
        rows = self.con.execute(
            "SELECT id,title,stock_code,state,updated_at FROM sessions WHERE customer_id=? "
            "ORDER BY updated_at DESC LIMIT ?", (customer_id, limit)).fetchall()
        return [dict(r) for r in rows]

    def update_session(self, sid: str, **kw) -> None:
        if not kw:
            return
        cols = ",".join("%s=?" % k for k in kw)
        self.con.execute("UPDATE sessions SET %s,updated_at=? WHERE id=?" % cols,
                         list(kw.values()) + [now_str(), sid])
        self.con.commit()

    # ---- 轮次 ----
    def add_turn(self, sid: str, role: str, content: str,
                 payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        tid = new_id("turn")
        ts = now_str()
        self.con.execute("INSERT INTO turns VALUES (?,?,?,?,?,?)",
                         (tid, sid, role, content,
                          json.dumps(payload or {}, ensure_ascii=False), ts))
        self.con.commit()
        return {"id": tid, "role": role, "content": content,
                "payload": payload or {}, "created_at": ts}

    def list_turns(self, sid: str, limit: int = 120) -> List[Dict[str, Any]]:
        rows = self.con.execute(
            "SELECT * FROM turns WHERE session_id=? ORDER BY created_at,rowid LIMIT ?",
            (sid, limit)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["payload"] = json.loads(d.get("payload") or "{}")
            except ValueError:
                d["payload"] = {}
            out.append(d)
        return out

    # ---- 问题 ----
    def create_question(self, sid: str, question: str,
                        options: List[Dict[str, str]],
                        kind: str = "") -> Dict[str, Any]:
        qid = new_id("q")
        self.con.execute(
            "INSERT INTO questions (id,session_id,turn_id,question,options,kind,answer,created_at) "
            "VALUES (?,?,?,?,?,?,'',?)",
            (qid, sid, "", question, json.dumps(options, ensure_ascii=False), kind, now_str()))
        self.con.commit()
        return {"id": qid, "text": question, "options": options, "kind": kind}

    def pending_question(self, sid: str) -> Optional[Dict[str, Any]]:
        row = self.con.execute(
            "SELECT * FROM questions WHERE session_id=? AND (answer IS NULL OR answer='') "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1", (sid,)).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["options"] = json.loads(d.get("options") or "[]")
        except ValueError:
            d["options"] = []
        return d

    def answer_question(self, qid: str, answer: str) -> Optional[Dict[str, Any]]:
        row = self.con.execute("SELECT * FROM questions WHERE id=?", (qid,)).fetchone()
        if not row:
            return None
        d = dict(row)
        self.con.execute("UPDATE questions SET answer=?, answered_at=? WHERE id=?",
                         (answer, now_str(), qid))
        self.con.commit()
        try:
            d["options"] = json.loads(d.get("options") or "[]")
        except ValueError:
            d["options"] = []
        d["answer"] = answer
        return d

    # ---- 动作 / 订阅 / 画像 ----
    def add_action(self, sid: str, typ: str, params: Dict[str, Any],
                   result: Dict[str, Any], status: str = "done") -> str:
        aid = new_id("act")
        self.con.execute("INSERT INTO actions VALUES (?,?,?,?,?,?,?)",
                         (aid, sid, typ, json.dumps(params, ensure_ascii=False),
                          json.dumps(result, ensure_ascii=False), status, now_str()))
        self.con.commit()
        return aid

    def add_subscription(self, customer_id: str, code: str,
                         dimensions: List[str]) -> str:
        row = self.con.execute(
            "SELECT id,dimensions FROM subscriptions WHERE customer_id=? AND code=? AND active=1",
            (customer_id, code)).fetchone()
        if row:
            dims = set(json.loads(row["dimensions"] or "[]")) | set(dimensions)
            self.con.execute("UPDATE subscriptions SET dimensions=? WHERE id=?",
                             (json.dumps(sorted(dims), ensure_ascii=False), row["id"]))
            self.con.commit()
            return row["id"]
        sid = new_id("sub")
        self.con.execute("INSERT INTO subscriptions VALUES (?,?,?,?,1,?)",
                         (sid, customer_id, code,
                          json.dumps(sorted(set(dimensions)), ensure_ascii=False), now_str()))
        self.con.commit()
        return sid

    def list_subscriptions(self, customer_id: str) -> List[Dict[str, Any]]:
        rows = self.con.execute(
            "SELECT * FROM subscriptions WHERE customer_id=? AND active=1 ORDER BY created_at DESC",
            (customer_id,)).fetchall()
        return [dict(r) for r in rows]

    def get_profile(self, customer_id: str) -> Dict[str, Any]:
        row = self.con.execute("SELECT prefs FROM profile WHERE customer_id=?",
                               (customer_id,)).fetchone()
        prefs: Dict[str, Any] = {"followup_enabled": True}
        if row:
            try:
                prefs.update(json.loads(row["prefs"] or "{}"))
            except ValueError:
                pass
        return prefs

    def set_profile(self, customer_id: str, **prefs) -> Dict[str, Any]:
        cur = self.get_profile(customer_id)
        cur.update(prefs)
        self.con.execute(
            "INSERT INTO profile (customer_id,prefs,updated_at) VALUES (?,?,?) "
            "ON CONFLICT(customer_id) DO UPDATE SET prefs=excluded.prefs,"
            "updated_at=excluded.updated_at",
            (customer_id, json.dumps(cur, ensure_ascii=False), now_str()))
        self.con.commit()
        return cur


class AnalysisClient:
    """H1 内部网关客户端（loopback + X-Analysis-Token），可注入替身用于测试。"""

    def __init__(self, base: Optional[str] = None, token: Optional[str] = None,
                 timeout: float = 15.0):
        self.base = (base or GATEWAY).rstrip("/")
        self.timeout = timeout
        self._token = token

    def token(self) -> str:
        if self._token:
            return self._token
        self._token = (os.environ.get("ANALYSIS_INTERNAL_TOKEN") or "").strip()
        if not self._token:
            try:
                self._token = open(TOKEN_FILE, encoding="utf-8").read().strip()
            except OSError:
                self._token = ""
        return self._token

    def _req(self, method: str, path: str,
             body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        data = None
        headers = {"X-Analysis-Token": self.token()}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        req = Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": "gateway 调用失败: %s" % str(exc)[:160]}

    def health(self) -> Dict[str, Any]:
        return self._req("GET", "/api/analysis/internal/health")

    def submit(self, code: str, llm: bool = False) -> Dict[str, Any]:
        return self._req("POST", "/api/analysis/internal/analyze",
                         {"code": code, "llm": llm, "push": False})

    def job(self, job_id: str) -> Dict[str, Any]:
        return self._req("GET", "/api/analysis/internal/job/" + quote(job_id))

    def report(self, run_id: str) -> Dict[str, Any]:
        return self._req("GET", "/api/analysis/internal/report?run_id=" + quote(run_id))

    def events(self, code: str, limit: int = 8, status: str = "all") -> Dict[str, Any]:
        return self._req(
            "GET", "/api/analysis/internal/events?code=%s&limit=%d&status=%s"
            % (quote(code), limit, status))


def extract_sections(md: str, keywords, limit_chars: int = 1200) -> str:
    """从 report.md 抽取匹配关键字的小节；无匹配返回空串。"""
    if not md:
        return ""
    lines = md.splitlines()
    heads = [i for i, ln in enumerate(lines) if ln.startswith("## ")]
    for kw in keywords:
        picked = []
        for idx, i in enumerate(heads):
            title = lines[i][3:].strip()
            if kw in title:
                end = heads[idx + 1] if idx + 1 < len(heads) else len(lines)
                picked.append("\n".join(lines[i:end]).strip())
        if picked:
            return "\n\n".join(picked)[:limit_chars]
    return ""


class DialogService:
    """对话服务：消息路由 + 反问 + 动作执行。"""

    def __init__(self, customer_id: str = "laotian", store: Optional[Store] = None,
                 client: Optional[AnalysisClient] = None):
        self.customer_id = customer_id
        self.store = store or Store()
        self.client = client or AnalysisClient()

    # ================= 对外入口 =================
    def handle_message(self, text: str, session_id: Optional[str] = None,
                       channel: str = "pc") -> Dict[str, Any]:
        text = (text or "").strip()
        if not text:
            return {"ok": False, "error": "消息为空"}
        if len(text) > MAX_TEXT:
            text = text[:MAX_TEXT]

        session = self.store.get_session(session_id) if session_id else None
        if session and session.get("state") == "analyzing":
            self._refresh_analysis(session)
            session = self.store.get_session(session["id"])

        # 无会话：必须有代码才能开始
        if not session:
            code = normalize_code(text)
            if not code:
                return {"ok": False, "error": "请先输入股票代码开始分析（例如 600337.SH）",
                        "hint": HELP_TEXT[:200]}
            return self._start_analysis(code, channel)

        self.store.add_turn(session["id"], "user", text, {"channel": channel})
        self.store.update_session(session["id"], state=session.get("state"))

        # 1) 未回答问题优先
        pending = self.store.pending_question(session["id"])
        if pending:
            code = normalize_code(text)
            if pending.get("kind") == "compare_ask" and code:
                self.store.answer_question(pending["id"], code)
                return self._compare(session, code)
            key = self._match_option(pending, text)
            if key:
                return self._apply_answer(session, pending, key, text)
            if pending.get("kind") == "compare_ask":
                return self._reply(session, "请直接回复第二只股票代码（例如 605258.SH）。",
                                   {"intent": "compare_bad_code"})
            # 不是选项：继续走关键词，会话里保留未答问题
        # 2) 控制类
        if any(w in text for w in STOP_WORDS):
            self.store.set_profile(self.customer_id, followup_enabled=False)
            return self._reply(session, "好，后续我只做分析、不再主动追问。"
                               "需要时直接说「开启反问」恢复。", {"intent": "stop_followup"})
        if "开启反问" in text or "恢复反问" in text:
            self.store.set_profile(self.customer_id, followup_enabled=True)
            return self._reply(session, "已恢复主动反问：分析完成后我会问你下一步关注点。",
                               {"intent": "enable_followup"})
        if text in ("怎么用", "帮助", "help", "?", "？"):
            return self._reply(session, HELP_TEXT, {"intent": "help"})
        if "订阅" in text and ("查看" in text or "列表" in text):
            subs = self.store.list_subscriptions(self.customer_id)
            if not subs:
                return self._reply(session, "当前还没有订阅。分析后说「盯着」即可建立。",
                                   {"intent": "list_subs"})
            lines = ["**当前监控订阅**"]
            for s in subs:
                lines.append("- %s｜%s" % (s["code"], s.get("dimensions") or "[]"))
            return self._reply(session, "\n".join(lines), {"intent": "list_subs"})

        # 3) 二维码 → 新股票
        code = normalize_code(text)
        if code and code != session.get("stock_code"):
            return self._start_analysis(code, channel)
        if code:
            if session.get("state") == "ready":
                return self._reply(session, "这只票已经分析过了。可以继续说「风险」「估值」"
                                   "「事件」「资金」深挖，或说「重新分析」再跑一次。",
                                   {"intent": "already_analyzed"})
            return self._reply(session, "正在分析中，请稍候……", {"intent": "analyzing"})
        if "重新分析" in text:
            return self._start_analysis(session.get("stock_code") or "", channel)

        # 4) 维度 → 深挖
        dim = self._detect_dimension(text)
        if dim:
            return self._deepdive(session, dim)

        # 5) 兜底
        return self._reply(session, "我没太理解这句。可以点上面的关注方向，或说：风险 / 估值 / "
                           "事件 / 资金 / 盯着 / 不用问了。", {"intent": "unclear"})

    def handle_answer(self, session_id: str, question_id: str,
                      option_key: Optional[str] = None,
                      text: Optional[str] = None) -> Dict[str, Any]:
        session = self.store.get_session(session_id)
        if not session:
            return {"ok": False, "error": "会话不存在"}
        q = self.store.pending_question(session_id)
        if not q or q["id"] != question_id:
            return self._reply(session, "这个问题已经处理过了；你可以说「风险」「估值」继续。",
                               {"intent": "stale_question"})
        key = (option_key or "").strip()
        if not key:
            key = self._match_option(q, text or "") or ""
        if not key:
            return self._reply(session, "没识别出你选的选项，请点选按钮或回复选项文字。",
                               {"intent": "bad_answer"})
        self.store.add_turn(session["id"], "user", text or key, {"answer_key": key})
        return self._apply_answer(session, q, key, text or "")

    def _apply_answer(self, session: Dict[str, Any], q: Dict[str, Any],
                      key: str, text: str) -> Dict[str, Any]:
        label = key
        for opt in q.get("options") or []:
            if opt.get("key") == key:
                label = opt.get("label") or key
                break
        self.store.answer_question(q["id"], label)
        if key == "compare":
            return self._compare_ask(session)
        if key == "watch":
            return self._watch(session, session.get("last_dim") or "risk")
        if key == "watch_yes":
            return self._watch(session, session.get("last_dim") or "risk")
        if key == "no":
            return self._reply(session, "好，先不盯。需要时随时说「盯着」。",
                               {"intent": "no_watch"})
        if key == "enable_followup":
            self.store.set_profile(self.customer_id, followup_enabled=True)
            return self._reply(session, "好，已恢复主动反问。", {"intent": "enable_followup"})
        if key in DIM_BY_KEY:
            return self._deepdive(session, key)
        return self._reply(session, "已记录。你可以继续说「风险」「估值」「事件」「资金」。",
                           {"intent": "answer_logged"})

    def list_sessions(self) -> Dict[str, Any]:
        return {"ok": True, "sessions": self.store.list_sessions(self.customer_id)}

    def get_session(self, session_id: str) -> Dict[str, Any]:
        session = self.store.get_session(session_id)
        if not session:
            return {"ok": False, "error": "会话不存在"}
        if session.get("state") == "analyzing":
            self._refresh_analysis(session)
            session = self.store.get_session(session["id"])
        turns = self.store.list_turns(session["id"])
        q = self.store.pending_question(session["id"])
        # 若分析刚完成但还没发过总结（例如页面轮询触发），补一次总结+反问
        if session.get("state") == "ready" and not any(
                (t.get("payload") or {}).get("intent") == "analysis_done" for t in turns):
            self._announce_done(session)
            turns = self.store.list_turns(session["id"])
            q = self.store.pending_question(session["id"])
        return {"ok": True, "session": session, "turns": turns, "question": q,
                "subscriptions": self.store.list_subscriptions(self.customer_id)}

    def set_profile(self, **prefs) -> Dict[str, Any]:
        return {"ok": True, "profile": self.store.set_profile(self.customer_id, **prefs)}

    # ================= 内部实现 =================
    def _start_analysis(self, code: str, channel: str) -> Dict[str, Any]:
        if not code:
            return {"ok": False, "error": "请输入股票代码（例如 600337.SH）"}
        session = self.store.create_session(self.customer_id, channel,
                                            title="分析 %s" % code, stock_code=code)
        self.store.add_turn(session["id"], "user", "分析 %s" % code, {"channel": channel})
        res = self.client.submit(code)
        if not res.get("ok"):
            self.store.update_session(session["id"], state="failed")
            msg = "分析提交失败：%s" % (res.get("error") or "未知错误")
            return self._reply(session, msg, {"intent": "analyze_failed"}, ok=False)
        job_id = res.get("job_id") or ""
        self.store.update_session(session["id"], state="analyzing", job_id=job_id)
        turn = self._add_assistant(
            session["id"],
            "已收到 %s，正在跑完整分析（通常 30–60 秒）。完成后我会把结论和「下一步关注点」"
            "一起给你。" % code, {"intent": "analyze_submitted", "job_id": job_id})
        return {"ok": True, "session": self.store.get_session(session["id"]),
                "reply": {"text": turn["content"], "job_id": job_id},
                "turns": [turn], "question": None}

    def _refresh_analysis(self, session: Dict[str, Any]) -> Dict[str, Any]:
        job = self.client.job(session.get("job_id") or "")
        state = job.get("state") or "unknown"
        data = job.get("job") or {}
        if state == "done":
            self.store.update_session(session["id"], state="ready",
                                      run_id=data.get("run_id") or "")
            session = self.store.get_session(session["id"]) or session
            self._announce_done(session, job)
        return session

    def _announce_done(self, session: Dict[str, Any],
                       job: Optional[Dict[str, Any]] = None) -> None:
        job = job or {}
        text = self._summary_text(session, job)
        self._add_assistant(session["id"], text,
                            {"intent": "analysis_done", "run_id": session.get("run_id") or ""})
        self._ask_dimension_question(session)

    def _summary_text(self, session: Dict[str, Any], job: Dict[str, Any]) -> str:
        code = session.get("stock_code") or ""
        parts = ["**%s 分析完成**" % code]
        labels = {"name": "名称", "rating": "评级", "score": "总分",
                  "coverage": "覆盖率", "as_of": "数据日"}
        for k, label in labels.items():
            if job.get(k) not in (None, ""):
                parts.append("%s：%s" % (label, job[k]))
        run_id = job.get("run_id") or session.get("run_id") or ""
        if run_id:
            rep = self.client.report(run_id)
            if rep.get("ok"):
                snippet = extract_sections(rep.get("content") or "", ("结论", "风险"), 700)
                if not snippet:
                    snippet = (rep.get("content") or "")[:700]
                parts.append("\n" + snippet)
        parts.append("\n直接说：风险 / 估值 / 事件 / 资金 / 盯着。")
        return "\n".join(parts)

    def _ask_dimension_question(self, session: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not self.store.get_profile(self.customer_id).get("followup_enabled", True):
            return None
        options = [{"key": d["key"], "label": d["label"]} for d in DIMENSIONS]
        options.append({"key": "watch", "label": "帮我盯着"})
        q = self.store.create_question(
            session["id"], "分析完成。这只票你下一步更想关注哪一块？", options,
            kind="dimension")
        self._add_assistant(session["id"], q["text"], {"intent": "ask_dimension"},
                            questions=[q])
        return q

    def _detect_dimension(self, text: str) -> Optional[str]:
        t = (text or "").lower()
        mapping = [
            ("risk", ("风险", "退市", "质押", "诉讼", "负债")),
            ("valuation", ("估值", "pe", "pb", "ps", "分位", "贵不贵", "便宜")),
            ("events", ("事件", "公告", "财报", "重整", "催化", "新闻")),
            ("flow", ("资金", "龙虎榜", "北向", "主力", "筹码", "换手")),
            ("compare", ("对比", "比较", "同行")),
            ("watch", ("盯着", "监控", "订阅", "提醒")),
        ]
        for key, kws in mapping:
            if any(k in t for k in kws):
                return key
        return None

    def _match_option(self, question: Dict[str, Any], text: str) -> Optional[str]:
        t = (text or "").strip()
        if not t:
            return None
        options = question.get("options") or []
        for opt in options:
            if t == (opt.get("label") or "").strip() or t == (opt.get("key") or "").strip():
                return opt.get("key")
        for opt in options:
            label = (opt.get("label") or "").strip()
            if label and label in t:
                return opt.get("key")
        return None

    def _deepdive(self, session: Dict[str, Any], dim_key: str) -> Dict[str, Any]:
        dim = DIM_BY_KEY.get(dim_key)
        if not dim:
            return self._reply(session, "未识别的方向。", {"intent": "bad_dim"})
        if session.get("state") == "analyzing":
            self._refresh_analysis(session)
            session = self.store.get_session(session["id"])
            if session.get("state") != "ready":
                return self._reply(session, "分析还在进行中，完成后我再按「%s」深挖。"
                                   % dim["label"], {"intent": "still_analyzing"})
        if session.get("state") == "failed":
            return self._reply(session, "上一次分析失败了，请说「重新分析」再试。",
                               {"intent": "failed"})
        code = session.get("stock_code") or ""
        self.store.update_session(session["id"], last_dim=dim_key,
                                  depth=int(session.get("depth") or 0) + 1)
        session = self.store.get_session(session["id"]) or session
        text = self._deepdive_text(session, code, dim)
        self.store.add_action(session["id"], "deepdive",
                              {"code": code, "dim": dim_key}, {"chars": len(text)})
        q = None
        subs = self.store.list_subscriptions(self.customer_id)
        already = any(s.get("code") == code for s in subs)
        prefs = self.store.get_profile(self.customer_id)
        if prefs.get("followup_enabled", True) and not already and (session.get("depth") or 0) <= 2:
            opts = [{"key": "watch_yes", "label": "帮我盯着这个方向"},
                    {"key": "no", "label": "先不盯"}]
            q = self.store.create_question(
                session["id"],
                "要不要我把「%s」纳入这只股票的监控？" % dim["label"], opts,
                kind="watch_confirm")
            text = text + "\n\n" + q["text"]
        turn = self._add_assistant(session["id"], text,
                                   {"intent": "deepdive", "dim": dim_key})
        return {"ok": True, "session": self.store.get_session(session["id"]),
                "reply": {"text": text}, "turns": [turn], "question": q}

    def _deepdive_text(self, session: Dict[str, Any], code: str,
                       dim: Dict[str, Any]) -> str:
        key = dim["key"]
        if key == "events":
            ev = self.client.events(code, limit=8, status="all")
            if not ev.get("ok"):
                return "事件库暂不可用：%s" % (ev.get("error") or "")
            items = ev.get("events") or []
            if not items:
                return "事件库中暂无 %s 的已登记事件。可以让我「盯着」公告关键词。" % code
            lines = ["**%s 近期事件（%d 条）**" % (code, len(items))]
            for e in items[:8]:
                lines.append("- %s｜%s｜%s" % (e.get("date") or "",
                                               e.get("category") or "未分类",
                                               (e.get("title") or "")[:80]))
            return "\n".join(lines)
        if key == "compare":
            return self._compare_ask(session)["reply"]["text"]
        if key == "watch":
            return self._watch(session, "risk")["reply"]["text"]
        run_id = session.get("run_id") or ""
        if not run_id:
            return "该会话还没有可用报告；先等分析完成。"
        rep = self.client.report(run_id)
        if not rep.get("ok"):
            return "报告暂不可用：%s" % (rep.get("error") or "")
        content = extract_sections(rep.get("content") or "", dim.get("section") or (), 1400)
        if not content:
            content = (rep.get("content") or "")[:1400]
        return "**%s · %s**（从最近一次分析报告提取）\n\n%s" % (code, dim["label"], content)

    def _compare_ask(self, session: Dict[str, Any]) -> Dict[str, Any]:
        q = self.store.create_question(session["id"],
                                       "想和哪只股票对比？直接回复代码（例如 605258.SH）。",
                                       [], kind="compare_ask")
        text = "好，对比需要第二只股票。"
        turn = self._add_assistant(session["id"], text + "\n\n" + q["text"],
                                   {"intent": "compare_ask"})
        return {"ok": True, "session": session, "reply": {"text": text},
                "turns": [turn], "question": q}

    def _compare(self, session: Dict[str, Any], other: str) -> Dict[str, Any]:
        code = session.get("stock_code") or ""
        self.store.add_action(session["id"], "compare",
                              {"a": code, "b": other},
                              {"note": "C1 仅登记，逐项对比在 C2 接入"},
                              status="pending_c2")
        text = ("**%s vs %s**\n当前 C1 先登记对比请求；逐项对比（估值/事件/资金）"
                "将在 C2 接入专项数据后自动生成。\n你可以先分别问这两只票的风险/事件。"
                % (code, other))
        return self._reply(session, text, {"intent": "compare_pending", "a": code, "b": other})

    def _watch(self, session: Dict[str, Any], dim_key: str) -> Dict[str, Any]:
        code = session.get("stock_code") or ""
        self.store.add_subscription(self.customer_id, code, [dim_key])
        self.store.add_action(session["id"], "watch.add",
                              {"code": code, "dim": dim_key}, {"subscription": code})
        label = DIM_BY_KEY.get(dim_key, {}).get("label", dim_key)
        return self._reply(session,
                           "已为 %s 建立「%s」监控订阅（对话内登记；盘中实时提醒在 C4 接入）。"
                           % (code, label), {"intent": "watch_added", "dim": dim_key})

    # ---- 回复工具 ----
    def _add_assistant(self, sid: str, text: str, payload: Dict[str, Any],
                       questions: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        turn = self.store.add_turn(sid, "assistant", text, payload)
        if questions:
            turn["questions"] = questions
        return turn

    def _reply(self, session: Dict[str, Any], text: str, payload: Dict[str, Any],
               ok: bool = True,
               questions: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        turn = self._add_assistant(session["id"], text, payload, questions)
        return {"ok": ok, "session": self.store.get_session(session["id"]),
                "reply": {"text": text}, "turns": [turn],
                "question": questions[0] if questions else
                self.store.pending_question(session["id"])}


def demo() -> None:  # pragma: no cover - 手工演示
    svc = DialogService()
    out = svc.handle_message("分析 600337.SH")
    print(json.dumps(out, ensure_ascii=False, indent=2)[:2000])


if __name__ == "__main__":
    demo()
