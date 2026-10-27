# -*- coding: utf-8 -*-
"""飞书私聊 → DSH headless → 回复（远程桥，2026-10-24 增强版）。

两层能力：
  1. 一次性问答：每个问题独立；
  2. **连续对话**：按 key（飞书上=open_id）记住 headless 会话 id，用
     `--session-id` 复用同一 DSH 持久会话，支持"接着改/再查一下"。
     `reset(key)` 可清空上下文开新对话。

约束：
  · 仅白名单 open_id（push_targets「个人」）可用（由调用方校验）；
  · 每次调用一个子进程，超时 240s、输出截断 1800 字；
  · 问题经 stdin 传入、不拼接 shell（防注入与引号问题）；
  · 同一时间只处理一个（进程内锁）。
"""

import json
import os
import subprocess
import sys
import threading
import time
import logging

DSH_EXE = os.environ.get("DSH_EXE") or (
    r"C:\Users\t\AppData\Local\Programs\DeepSeek Harness"
    r"\DeepSeek Harness.exe"
)
DSH_CLI = os.environ.get("DSH_CLI") or (
    r"C:\Users\t\AppData\Local\Programs\DeepSeek Harness"
    r"\resources\app.asar\dsh\node_modules\@deepseek-ai"
    r"\dsh-desktop-host\lib\cli.js"
)
DSH_REMOTE_DIR = os.environ.get("DSH_REMOTE_DIR") or r"C:\项目\dsh_runtime"
PATCH = os.environ.get("DSH_HEADLESS_PATCH") or (
    os.path.join(DSH_REMOTE_DIR, "headless-account.patch.yml")
)
CWD = os.environ.get("DSH_RELAY_CWD") or r"C:\项目"
TIMEOUT = int(os.environ.get("DSH_RELAY_TIMEOUT") or "240")
MAX_OUT = 1800
SESS_FILE = os.environ.get("DSH_RELAY_SESSIONS") or (
    os.path.join(DSH_REMOTE_DIR, "conv_sessions.json")
)
SESSION_TTL_SEC = int(os.environ.get("DSH_RELAY_SESSION_TTL") or str(3 * 86400))
OWNERS_FILE = os.environ.get("DSH_OWNER_FILE") or (
    r"C:\项目\老田的AI助手\portal\work\feishu_secrets\owners.json"
)

PREFIXES = ("机器问：", "机器问:", "问：", "问:", "AI：", "AI:", "ai：", "ai:")
_LOCK = threading.Lock()


def strip_prefix(text):
    """命中前缀则返回去掉前缀的问题（可为空串）；未命中返回 None。"""
    t = (text or "").strip()
    for prefix in PREFIXES:
        if t.startswith(prefix):
            return t[len(prefix) :].strip()
    return None


def _owner_config():
    try:
        with open(OWNERS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def owner_ids():
    """老田在各 app 域下的 open_id 集合（多域白名单）。

    优先级：env FEISHU_OWNER_OPEN_IDS（逗号分隔）→ owners.json.open_ids
    → 旧 push_targets「个人」（过渡期兼容）。
    """
    ids = set()
    env_ids = os.environ.get("FEISHU_OWNER_OPEN_IDS", "")
    ids.update(x.strip() for x in env_ids.split(",") if x.strip())
    cfg = _owner_config()
    ids.update(str(x).strip() for x in (cfg.get("open_ids") or []) if str(x).strip())
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        nc = os.path.join(root, "news_collector")
        if nc not in sys.path:
            sys.path.insert(0, nc)
        import push_targets

        legacy = push_targets.target_id("个人")
        if legacy:
            ids.add(legacy)
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "portal/feishu_dsh_relay.py", exc)
    return ids


def allowed(open_id, chat_type="", chat_id=""):
    """白名单校验：多 app 域 owner open_id；执行 bot 可用 owner 级 p2p 兜底。

    2026-10-25 修复：换新 app 后 open_id 是 app 维度的，旧单值判断把老田
    自己拒了（"无权限：零界·执行仅限老田本人"）。改为 owners.json 多域列表；
    另 owners.json.allow_any_p2p=true 时，私聊（p2p）放行——前提是该 app
    可用范围仅老田（当前零界·执行=部分成员仅田峰）。放开范围时必须关掉。
    """
    oid = (open_id or "").strip()
    if not oid:
        return False
    if oid in owner_ids():
        return True
    cfg = _owner_config()
    if chat_type == "p2p" and cfg.get("allow_any_p2p"):
        return True
    return False


# ── 会话映射（key -> sessionId）────────────────────────────────
def _load_sessions():
    try:
        with open(SESS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_sessions(data):
    try:
        os.makedirs(os.path.dirname(SESS_FILE), exist_ok=True)
        tmp = SESS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, SESS_FILE)
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "portal/feishu_dsh_relay.py", exc)


def get_session(key):
    if not key:
        return ""
    item = _load_sessions().get(key) or {}
    sid = str(item.get("session_id") or "")
    ts = float(item.get("updated") or 0)
    if sid and (time.time() - ts) <= SESSION_TTL_SEC:
        return sid
    return ""


def set_session(key, sid):
    if not key or not sid:
        return
    data = _load_sessions()
    data[key] = {"session_id": sid, "updated": time.time()}
    _save_sessions(data)


def reset(key):
    """清空某个 key 的连续会话（下次提问开新会话）。"""
    data = _load_sessions()
    if key in data:
        data.pop(key, None)
        _save_sessions(data)
        return True
    return False


# ── headless 调用 ───────────────────────────────────────────────
def _run_headless(question, session_id):
    """→ (text, session_id, error)。"""
    cmd = [
        DSH_EXE,
        "--expose-internals",
        DSH_CLI,
        "headless",
        "--patch",
        PATCH,
        "--json",
    ]
    if session_id:
        cmd += ["--session-id", session_id]
    cmd.append("-")
    env = dict(os.environ)
    env["ELECTRON_RUN_AS_NODE"] = "1"
    try:
        proc = subprocess.run(
            cmd,
            cwd=CWD,
            input=(question + "\n").encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=TIMEOUT,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return "", "", "⏱ DSH 处理超时（>%d 秒）已终止；可把问题拆小再问。" % TIMEOUT
    except OSError as exc:
        return "", "", "❌ 启动 DSH 失败：%s" % exc

    sid = ""
    final_text = ""
    err_text = ""
    for raw in (proc.stdout or b"").decode("utf-8", "replace").splitlines():
        line = raw.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        etype = ev.get("type")
        if etype == "session":
            sid = str(ev.get("sessionId") or "")
        elif etype == "final":
            final_text = str(ev.get("text") or "")
        elif etype == "error":
            err_text = str(ev.get("message") or ev.get("text") or "")
    if proc.returncode != 0 or not final_text:
        tail = ""
        stderr = (proc.stderr or b"").decode("utf-8", "replace").strip()
        if stderr:
            tail = stderr.splitlines()[-1][:200]
        return (
            "",
            sid,
            "❌ DSH 执行失败（rc=%d）%s"
            % (
                proc.returncode,
                "：" + (err_text[:200] or tail) if (err_text or tail) else "",
            ),
        )
    return final_text.strip(), sid, ""


def _clean(text):
    out = []
    for line in (text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("[llm-") or s.startswith("dsh: "):
            continue
        out.append(line.rstrip())
    return "\n".join(out).strip()


def ask(question, key=""):
    """key=飞书 open_id；有 key 则连续对话（复用 --session-id）。"""
    q = (question or "").strip()
    if not q:
        return "用法：直接发问题，或「问：<你的问题>」（例：帮我总结今天的行情）"
    if not os.path.exists(DSH_EXE) or not os.path.exists(PATCH):
        return "❌ DSH 未就绪：缺 %s 或补丁 %s" % (DSH_EXE, PATCH)
    if not _LOCK.acquire(blocking=False):
        return "⏳ 上一个问题还在处理，处理完再发。"
    try:
        sid = get_session(key)
        text, new_sid, err = _run_headless(q, sid)
        if err and sid:
            # 旧会话失效（被清/过期）→ 清映射后重试一次
            reset(key)
            text, new_sid, err = _run_headless(q, "")
        if err:
            return err
        if key and new_sid:
            set_session(key, new_sid)
        text = _clean(text)
        if len(text) > MAX_OUT:
            text = text[:MAX_OUT] + "\n…（已截断，问题拆小可拿全）"
        return "🤖 DSH：\n" + text
    finally:
        _LOCK.release()
