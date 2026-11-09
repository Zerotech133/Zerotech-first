# -*- coding: utf-8 -*-
"""军团指挥界面套件 HTTP 服务（WO-LEGION-S22）。

端口 8796（独立于 8795 门面）。端点（全部只读 GET，除会终指令）：
  GET /api/snapshot        —— org/hr/tasks/schedules 聚合快照（沙盘数据源）
  GET /api/intent          —— 席位意图卡契约（六行一次算好，前端只渲染）
  GET /api/health          —— 服务与健康
  GET /                    —— 指挥界面（index.html，沙盘+会议室+工作台三视图）
  POST /api/dispatch       —— 会终指令：写军团门面 inbox（走 S21 投递协议）

纪律：快照端点只读；dispatch 是唯一写路径且只写 bridge_inbox inbox/
（田丰登记去重的既有机制），不直改 org/tasks/任何生产状态。
"""

from __future__ import annotations

import os

import argparse
import json
import re
import subprocess
import sys
import time
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import pathlib
from urllib.parse import urlparse
from contextlib import nullcontext

_PLATFORM = Path(__file__).resolve().parents[1]
if str(_PLATFORM) not in sys.path:
    sys.path.insert(0, str(_PLATFORM))

from gateway import inbox_gateway, llm_gateway  # noqa: E402, S21
from engine.org_profiles import fallbacks_index, profile_index, stamp  # noqa: E402, S41
from engine.openclaw_cli import agent_primary_model, is_relay, run as oc_run  # noqa: E402
from legion_command.snapshot import build_snapshot  # noqa: E402
from legion_command import hq_meeting  # noqa: E402
from legion_command import case_api  # noqa: E402
from legion_command import case_orchestrator  # noqa: E402
from legion_command import hq_agenda  # noqa: E402
from legion_command import hq_acceptance  # noqa: E402
from legion_command import hq_stats  # noqa: E402
import logging

try:
    from safe_http import safe_urlopen
except ImportError:  # 作为包导入时
    from legion_command.safe_http import safe_urlopen


_STATIC = Path(__file__).resolve().parent / "static"
# 作战会议室纪要落盘目录（模块级，测试可 monkeypatch 到 tmp）
MEETINGS_DIR = _PLATFORM / "data" / "meetings" / "warroom"
HQ_MEETINGS_DIR = _PLATFORM / "data" / "meetings" / "hq"
COUNCIL_DATA = _PLATFORM.parent / "portal" / "data"
_BASELINE_FILE = _PLATFORM / "data" / "test_baseline" / "latest.json"
_REDTEAM_DIR = _PLATFORM.parent / "work" / "p1" / "redteam"
_ACCEPTANCE_DIR = _PLATFORM.parent / "work" / "huangzhong"
_KIND_MAP = {"work": "task_request", "trade": "task_request", "study": "question"}


def load_experiment_cards(exp_dir: Path, limit: int = 20) -> list[dict]:
    """读取实验卡：忽略点状态文件、按 created_at（回退 mtime）取末 limit 张。

    WO-LEGION-CLOCKFIX-20261026：`agent_platform/data/experiments/` 里同时存在
    `.closeout_state.json`（去重状态）与 `EX-*.json`（卡）；旧实现 glob("*.json")
    会把状态文件当卡片（渲染出 card_id=null 的空卡）。点文件一律跳过；
    `card_id` 缺失的坏文件也跳过（fail-visible：不渲染假卡）。
    """
    exp_dir = Path(exp_dir)
    if not exp_dir.exists():
        return []
    loaded = []
    for p in exp_dir.glob("*.json"):
        if p.name.startswith("."):
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(d, dict) or not d.get("card_id"):
                continue
            loaded.append((str(d.get("created_at") or ""), p.stat().st_mtime, d))
        except (ValueError, OSError):
            continue
    loaded.sort(key=lambda it: (it[0], it[1]))
    cards = []
    for created_at, _mtime, d in loaded[-limit:]:
        verdict = d.get("verdict") if isinstance(d.get("verdict"), dict) else {}
        sig = (
            verdict.get("signature")
            if isinstance(verdict.get("signature"), dict)
            else {}
        )
        cards.append(
            {
                "card_id": d.get("card_id"),
                "hypothesis": str(d.get("hypothesis", ""))[:60],
                "status": d.get("status"),
                "arms": list((d.get("arms") or {}).keys()),
                "created_at": d.get("created_at") or created_at,
                "kind": d.get("kind") or "experiment",
                "decision_required": bool(d.get("decision_required", False)),
                "window_summary": d.get("window_summary") or {},
                "signature_verified": sig.get("verified"),
            }
        )
    return cards


# 参会席位 → 模型绑定（10-51 异族裁决 + org.json 主事实源；未列出席位默认本地 9b）
AGENT_BINDING = {
    "A0": "glm/glm-5.3",
    "AI1": "openai/gpt-6.0",
    "R2": "ollama/qwen3.5:9b",
    "OPT1": "glm/glm-4.6",
    "ST1": "glm/glm-5.3",
    "ARCH1": "glm/glm-5.3",
    "SEC2": "glm/glm-5.3",
    "LEG1": "glm/glm-5.3",
    "DEV2": "ollama/qwen3.6:35b-a3b",
}
_DEFAULT_BINDING = "ollama/qwen3.5:9b"
# 平台级兜底降级档：席位没配 llm_fallback 时只降这一档（A0/AI1 等无链席位的旧行为）
_DEFAULT_FALLBACK = _DEFAULT_BINDING
REPORT_PURPOSES = {"dept_report", "case", "case_autorun", "personal_brief"}


# ---- 五角色实际岗位 persona（2026-11-04 入职台账为唯一口径） ----
_DATA1_PERSONA = {
    "seat": "数据智能部·兰台令", "name": "张松", "actual_position": "数据资源主官（兰台令）",
    "reports_to": "A0", "duties": ["资产目录", "血缘", "接入", "权限分级", "生命周期", "质量", "开放边界"],
}
_ACADEMY_TEAM_FILE = _PLATFORM / "registry" / "academy_team_onboard.json"
_ACADEMY_CACHE = {"mtime": None, "map": {}}


def academy_team_persona(agent: str) -> str:
    """司马徽研究院 8 席岗位 persona；非团队成员返回空串。"""
    try:
        mt = _ACADEMY_TEAM_FILE.stat().st_mtime
        if _ACADEMY_CACHE["mtime"] != mt:
            data = json.loads(_ACADEMY_TEAM_FILE.read_text(encoding="utf-8"))
            _ACADEMY_CACHE["map"] = {x["agent_id"]: x for x in data.get("members", [])}
            _ACADEMY_CACHE["mtime"] = mt
    except (OSError, ValueError):
        return ""
    rec = _ACADEMY_CACHE["map"].get(agent)
    if not rec:
        return ""
    duties = "、".join(rec.get("duties") or [])
    return ("你是零界%s的%s（%s），实际岗位：%s；汇报对象：%s；职责：%s。"
            "必须以该实际岗位身份回答，不越权、不冒充其他席位。"
            % (rec.get("seat", ""), rec.get("name", ""), agent,
               rec.get("actual_position", ""), rec.get("reports_to", ""), duties))


_FIVE_ROLES_FILE = _PLATFORM / "registry" / "five_roles_onboard.json"
_FIVE_ROLE_CACHE = {"mtime": None, "map": {}}


def five_role_persona(agent: str) -> str:
    """返回五角色岗位 persona；非五角色返回空串。缓存按 mtime 失效。"""
    try:
        mt = _FIVE_ROLES_FILE.stat().st_mtime
        if _FIVE_ROLE_CACHE["mtime"] != mt:
            data = json.loads(_FIVE_ROLES_FILE.read_text(encoding="utf-8"))
            _FIVE_ROLE_CACHE["map"] = {r["agent_id"]: r for r in data.get("roles", [])}
            _FIVE_ROLE_CACHE["mtime"] = mt
    except (OSError, ValueError):
        return ""
    rec = _FIVE_ROLE_CACHE["map"].get(agent)
    if not rec and agent == "DATA1":
        rec = _DATA1_PERSONA
    if not rec:
        return academy_team_persona(agent)
    if not rec:
        return ""
    duties = "、".join(rec.get("duties") or [])
    return ("你是零界指挥部%s的%s（%s），实际岗位：%s；汇报对象：%s；职责：%s。"
            "必须以该实际岗位身份回答，不越权、不冒充其他席位。"
            % (rec.get("seat", ""), rec.get("name", ""), agent,
               rec.get("actual_position", ""), rec.get("reports_to", ""), duties))
# 会议场景绑定覆盖：治理席 ZHAOYUN 的正式绑定是 DSH 会话载体（deepseek-v4-pro），
# 不适配同步 /api/chat；会议仅用本地模型发声，不改变正式治理席位绑定。
MEETING_BINDING_OVERRIDES = {"ZHAOYUN": "ollama/qwen3.5:9b"}

# P0 写接口防护（2026-10-30）：同源校验 + 浏览器写请求 token。
# 非浏览器本地客户端（无 Origin：curl/测试）保持兼容；浏览器跨源一律拒绝。
WRITE_TOKEN = __import__("secrets").token_urlsafe(32)



def resolve_meeting_binding(agent: str, platform_dir: Path | None = None) -> str:
    """会议 purpose 的绑定解析：仅对覆盖表内席位替换，且不改 none/规则执行体。"""
    orig = resolve_binding(agent, platform_dir)
    if agent in MEETING_BINDING_OVERRIDES and orig != "none":
        return MEETING_BINDING_OVERRIDES[agent]
    return orig
# 同一 stamp 指纹管两张索引：席位绑定（map）与席位降级链（fallbacks）
_BINDING_CACHE = {"mtime": None, "map": {}, "fallbacks": {}}


def _binding_cache(base: Path) -> dict:
    """按 stamp 指纹刷新「绑定表 + 降级链」双索引，返回缓存本体（零 IO 命中路径）。

    为什么同一指纹管两个索引：两者都只由 org.json / team_*.json 决定，分开缓存会
    出现「绑定已换、降级链还是旧档」的半拍状态（S41 判例：改档后会议层仍按旧档说话）。
    `fallbacks` 键缺失（老进程热更新 / 测试只重置了 map）时也强制重算——
    读到空链会让 glm 失败后静默回落到平台默认档，正是本单要消灭的行为。
    """
    cache = _BINDING_CACHE
    key = stamp(base)
    if cache.get("mtime") != key or "fallbacks" not in cache:
        cache["map"] = profile_index(base)
        cache["fallbacks"] = fallbacks_index(base)
        cache["mtime"] = key
    return cache


def resolve_binding(agent: str, platform_dir: Path | None = None) -> str:
    """席位 → 模型绑定。**org.json 是真源，优先于本文件的硬编码表**。

    为什么必须查真源：S37 给 BEACON1 填了 `llm_binding: none`（规则巡检哨，无 LLM 依赖），
    而会议层若只查硬编码表就会给它默认 9b —— 于是「档案写明无 LLM、会议却能发言」自相矛盾。
    编制造出来的事实，展示层必须认。

    返回：
      - 具体绑定串（如 ollama/qwen3.5:9b）
      - "none" 表示该席位为规则执行体，无 LLM 发言能力
    """
    cache = _binding_cache(platform_dir or _PLATFORM)
    hit = cache["map"].get(agent) or {}
    if hit.get("llm_binding"):
        return hit["llm_binding"]
    return AGENT_BINDING.get(agent, _DEFAULT_BINDING)


def resolve_fallbacks(agent: str, platform_dir: Path | None = None) -> list[str]:
    """席位 → 有序降级链（org.json 的 `llm_fallback`）；未配置/无档案返回 `[]`。

    与 `resolve_binding` 共用同一 stamp 缓存：org 改档后绑定与降级链同时失效，
    不会出现「主绑换新、链还是旧」的错配。返回**副本**——调用方就地改列表
    （如去重/裁剪）不会污染缓存，也不会串到下一个席位。
    """
    cache = _binding_cache(platform_dir or _PLATFORM)
    return list(cache["fallbacks"].get(agent) or [])


def _seat_chat(
    chat_fn,
    binding: str,
    fallbacks,
    system: str,
    user: str,
    default_fallback: str = _DEFAULT_FALLBACK,
    allow_non_glm_fallback: bool = False,
) -> tuple[dict, list[str]]:
    """会议/指挥台单席位对话：主绑失败时按**席位级降级链**依次尝试。

    纯函数（chat_fn 注入、不读全局、不写缓存），便于离线验收与复用；返回
    `(env, attempts)`，attempts = 主绑 + 全部已尝试 fallback（按真实调用顺序）。

    分支（与旧 /api/chat 的兼容契约）：
      · 主绑 ok → `(env, [binding])`：不标 degraded、不碰 data，行为与旧实现一致；
      · 非 glm 主绑失败 → **原样返回** `(env, [binding])`，一次 fallback 都不调：
        本地 9b 失败就是失败（fail-visible），伪造「降级成功」会掩盖真故障；
        openai 族 / 规则执行体同理——降级只对云端判读席（glm）开放；
      · glm 主绑失败 → 依次尝试 `fallbacks or [default_fallback]`：
          - 首个 ok 的信封被标 `degraded=True` + `fallback_from`（主绑）+
            `fallback_reason`（主错 message）+ `fallback_chain`（已尝试的 fallback，
            含成功项），并补齐 `data.binding_resolved`（缺失时补成实际生效的 fallback，
            已有则尊重底层口径，供响应 `binding` 字段取值）；
          - 全部失败 → 返回**主失败信封** + 完整 attempts：展示层要看到主绑原错，
            而不是最后一条 fallback 的错（否则排障只看到「9b also failed」）。
    """
    env = chat_fn(binding=binding, system=system, user=user)
    attempts = [binding]
    if env.get("ok"):
        return env, attempts
    if not binding.startswith("glm/") and not allow_non_glm_fallback:
        return env, attempts
    reason = (env.get("error") or {}).get("message") or "cloud unavailable"
    chain = list(fallbacks or [default_fallback])
    if not binding.startswith("glm/") and allow_non_glm_fallback:
        # 日报/Case 场景：非 glm 主绑失败时允许云端 glm 兜底，再走原降级链
        chain = ["glm/glm-5.3"] + [x for x in chain if x != "glm/glm-5.3"]
    for fb in chain:
        fb_env = chat_fn(binding=fb, system=system, user=user)
        attempts.append(fb)
        if not fb_env.get("ok"):
            continue
        fb_env["degraded"] = True
        fb_env["fallback_from"] = binding
        fb_env["fallback_reason"] = reason
        fb_env["fallback_chain"] = list(attempts[1:])
        data = fb_env.get("data")
        if not isinstance(data, dict):
            data = {}
            fb_env["data"] = data
        data.setdefault("binding_resolved", fb)
        return fb_env, attempts
    return env, attempts


# ---------------------------------------------------------------- LiteLLM 子进程
# AUDIT-01 A-03（2026-10-27）：LiteLLM 上游子进程三处健壮性修复，纯函数便于 mock 单测。
# prompt 经 argv 传入：tools/litellm_route.py 的 route 子命令只收位置参数（无 stdin 支持，
# 2026-10-27 核对 litellm_route.py:162-163），故保持 argv；Windows CreateProcess 命令行
# 上限 32767 字符，这里留足 exe/工具路径/参数余量取 15000，超限不启子进程直接 413。
LITELLM_MAX_PROMPT_CHARS = 15000
LITELLM_TIMEOUT_S = 180


def _parse_last_json(stdout: str):
    """取子进程 stdout 中最靠后的可解析 JSON dict；无则返回 None。

    旧实现「扫到首个 `{` 开头的行就 json.loads(整个 stdout)」：litellm_route 用
    `json.dumps(indent=1)` **多行**输出，且工具可能先打日志 → 首个 `{` 行匹配后
    json.loads(整个 stdout) 必然 ValueError → 502。这里三级回退：
      1) 整个 stdout 就是 JSON（无日志的正常路径，含 indent=1 多行）；
      2) 逐行倒序取最后一行可单独解析的 JSON（日志 + 单行 JSON）；
      3) 从每个 `{` 位置 raw_decode，取最后一个成功块（日志 + 多行 JSON）。
    """
    text = (stdout or "").strip()
    if not text:
        return None
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except ValueError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/legion_command/serve.py", exc)
    for raw_line in reversed(text.splitlines()):
        line = raw_line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    decoder = json.JSONDecoder()
    best = None
    for idx, char in enumerate(text):
        if char != "{":
            continue
        try:
            obj, _end = decoder.raw_decode(text[idx:])
        except ValueError:
            continue
        if isinstance(obj, dict):
            best = obj
    return best


def _litellm_route(prompt: str) -> tuple[dict, int]:
    """调用 `litellm_route.py route --json`，返回 (result, http_status)。

    状态码：200 成功；400 空 prompt；413 prompt 超长（不启子进程）；502 上游/解析/
    启动失败（含 stderr 尾部）；504 子进程超时。失败时 result 是带 error 的 dict。
    """
    if not isinstance(prompt, str):
        prompt = str(prompt or "")
    if not prompt.strip():
        return {"ok": False, "error": "empty_prompt"}, 400
    if len(prompt) > LITELLM_MAX_PROMPT_CHARS:
        return {
            "ok": False,
            "error": "prompt_too_long chars=%d max=%d（litellm_route 仅支持 argv 传参，"
            "超 Windows 32767 命令行上限前拒绝）"
            % (len(prompt), LITELLM_MAX_PROMPT_CHARS),
        }, 413
    tool = _PLATFORM.parent / "tools" / "litellm_route.py"
    py = Path(r"C:\项目\py312\python.exe")
    exe = str(py) if py.exists() else sys.executable
    env2 = dict(
        os.environ,
        LITELLM_LOCAL_MODEL_COST_MAP="True",
        PYTHONIOENCODING="utf-8",
        PYTHONUTF8="1",
    )
    try:
        cp = subprocess.run(
            [exe, "-X", "utf8", str(tool), "route", prompt, "--json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=LITELLM_TIMEOUT_S,
            env=env2,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error": "litellm_tool_timeout timeout=%ds（上游无响应，未重试）"
            % LITELLM_TIMEOUT_S,
            "timeout": LITELLM_TIMEOUT_S,
        }, 504
    except OSError as e:
        return {"ok": False, "error": "litellm_tool_spawn_failed: %s" % e}, 502
    lr = _parse_last_json(cp.stdout)
    if lr is None:
        tail = (cp.stderr or "").strip()[-300:] or "(empty)"
        return {
            "ok": False,
            "error": "litellm_tool_no_json rc=%s stderr=%s" % (cp.returncode, tail),
        }, 502
    return lr, (200 if lr.get("ok") else 502)


def _case_actor(headers) -> tuple[str, set]:
    """Case API 身份：优先 nginx 身份头；否则用浏览器 Cookie 问 8091 /api/auth/me。"""
    sub = (headers.get("X-Auth-Sub") or "").strip()
    roles = {x.strip() for x in (headers.get("X-Auth-Roles") or "").split(",") if x.strip()}
    if sub:
        return sub, roles
    cookie = headers.get("Cookie") or ""
    if not cookie:
        return "", set()
    try:
        req = urllib.request.Request("http://127.0.0.1:8091/api/auth/me",
                                     headers={"Cookie": cookie, "Accept": "application/json"})
        with safe_urlopen(req, timeout=3) as resp:
            d = json.loads(resp.read().decode("utf-8"))
        sess = d.get("session") or {}
        ctx = sess.get("identity_context") or {}
        return str(sess.get("sub") or ""), {str(x) for x in (ctx.get("roles") or [])}
    except Exception:
        return "", set()


class Handler(BaseHTTPRequestHandler):
    server_version = "legion-command/1.0"

    def _origin_allowed(self):
        """同源判定：Origin 缺省（非浏览器）放行；浏览器必须与本服务 Host 同源。"""
        origin = (self.headers.get("Origin") or "").strip()
        if not origin:
            return True
        host = (self.headers.get("Host") or "").strip()
        if not host:
            return False
        return origin in ("http://" + host, "https://" + host)

    def _write_guard(self, path):
        """所有 8796 写路径统一守卫：跨源拒绝；浏览器同源写必须带 X-DSH-Token。"""
        guarded = path == "/api/dispatch" or path.startswith("/api/zhaoyun/")
        if not guarded:
            return True
        if not self._origin_allowed():
            self._send_json({"ok": False, "error": "forbidden_origin"}, 403)
            return False
        origin = (self.headers.get("Origin") or "").strip()
        token = (self.headers.get("X-DSH-Token") or "").strip()
        if origin and token != WRITE_TOKEN:
            self._send_json({"ok": False, "error": "invalid_write_token"}, 403)
            return False
        return True

    def _send_json(self, obj, status=200):
        import json

        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers",
                         "Content-Type, Authorization, X-DSH-Agent, X-DSH-Task, X-DSH-Backend")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, ctype: str):
        if not path.exists():
            self._send_json({"ok": False, "error": "not_found"}, 404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers",
                         "Content-Type, Authorization, X-DSH-Agent, X-DSH-Task, X-DSH-Backend")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ---------- O6 P2c：请求级 run 上下文 ----------
    def _request_scope(self):
        """每个 HTTP 请求一个 trace run；trace 不可用时降级为 nullcontext。"""
        try:
            from observability import trace as _tr

            return _tr.run_scope()
        except Exception:  # noqa: BLE001
            return nullcontext()

    def do_GET(self):
        with self._request_scope():
            return self._do_GET_impl()

    def _do_GET_impl(self):
        import re

        path = urlparse(self.path).path
        if path == "/api/zhaoyun/write-token":
            if not self._origin_allowed():
                self._send_json({"ok": False, "error": "forbidden_origin"}, 403)
                return
            self._send_json({"ok": True, "token": WRITE_TOKEN})
            return
        if path == "/api/governance/kpi":
            try:
                from legion_command import governance_kpi as _gk
                self._send_json(_gk.collect())
            except Exception as exc:  # noqa: BLE001
                self._send_json({"ok": False, "error": "kpi_failed",
                                 "detail": "%s: %s" % (type(exc).__name__, str(exc)[:200])}, 500)
            return
        if path == "/" or path == "/index.html":
            self._send_file(_STATIC / "index.html", "text/html; charset=utf-8")
            return
        if path in ("/zhaoyun", "/zhaoyun.html"):
            # 干净直达路由（顶栏 iframe 走 /static/zhaoyun.html，二者同一文件）
            self._send_file(_STATIC / "zhaoyun.html", "text/html; charset=utf-8")
            return
        if path in ("/tianfeng", "/tianfeng.html"):
            # 田丰属性页（产品/研发席）直达路由
            self._send_file(_STATIC / "tianfeng.html", "text/html; charset=utf-8")
            return
        if path in ("/keys", "/keys/", "/keys.html"):
            # Phase A 收口：默认新页；KEYS_V2=0 或 ?v2=0 回滚旧页
            try:
                from urllib.parse import parse_qs
                _q = parse_qs(urlparse(self.path).query)
                _qv = (_q.get("v2") or [""])[0]
                _on = (_qv == "1") if _qv in ("0", "1") else (os.environ.get("KEYS_V2", "1") == "1")
            except Exception:
                _on = False
            _page = "keypool_v2.html" if _on else "keypool.html"
            self._send_file(_STATIC / _page, "text/html; charset=utf-8")
            return
        if path == "/mission":
            try:
                from urllib.parse import parse_qs
                _q = parse_qs(urlparse(self.path).query)
                _qv = (_q.get("v2") or [""])[0]
                _on = (_qv == "1") if _qv in ("0", "1") else (os.environ.get("KEYS_V2", "1") == "1")
            except Exception:
                _on = False
            if _on and (_STATIC / "mission.html").exists():
                self._send_file(_STATIC / "mission.html", "text/html; charset=utf-8")
            else:
                self._send_json({"ok": False, "error": "mission_page_disabled"}, 404)
            return
        m = re.match(r"^/static/([a-z0-9_-]+\.(html|css|js))$", path)
        if m and re.fullmatch(r"[a-z0-9_-]+", m.group(1).rsplit(".", 1)[0]):
            _ct = {"html": "text/html; charset=utf-8",
                   "css": "text/css; charset=utf-8",
                   "js": "application/javascript; charset=utf-8"}[m.group(2)]
            self._send_file(_STATIC / m.group(1), _ct)
            return
        if path.startswith("/api/ide/fs/"):
            # IDE 调试通道只读文件工具；根目录/敏感路径限制在 ide_fs 内。
            from urllib.parse import parse_qs
            from legion_command import ide_fs

            action = path.rsplit("/", 1)[-1]
            qs = parse_qs(urlparse(self.path).query)
            try:
                if action == "list":
                    res = ide_fs.list_dir((qs.get("path") or ["."])[0])
                elif action == "read":
                    res = ide_fs.read_file((qs.get("path") or [""])[0])
                else:
                    res = ide_fs.dispatch(action, {
                        "path": (qs.get("path") or ["."])[0],
                        "query": (qs.get("q") or [""])[0],
                        "glob": (qs.get("glob") or [""])[0],
                    })
                self._send_json(res)
            except ide_fs.FsError as e:
                self._send_json({"ok": False, "error": e.code,
                                 "detail": e.detail}, e.status)
            except Exception as e:  # noqa: BLE001
                self._send_json({"ok": False, "error": "ide_fs_failed",
                                 "detail": "%s: %s" % (type(e).__name__, str(e)[:160])}, 500)
            return
        if path == "/api/ide/agents":
            import json as _json
            bundle = Path(__file__).resolve().parents[2] / "portal" / "agent_ide_bundle.json"
            try:
                data = _json.loads(bundle.read_text(encoding="utf-8"))
                agents = list((data.get("agents") or {}).values())
                self._send_json({"ok": True, "agents": agents,
                                 "generated": data.get("generated")})
            except (OSError, ValueError) as e:
                self._send_json({"ok": False, "error": "bundle_unavailable",
                                 "detail": type(e).__name__}, 503)
            return
        if path == "/api/ide/context":
            from urllib.parse import parse_qs
            from legion_command import ide_context

            qs = parse_qs(urlparse(self.path).query)
            q = (qs.get("q") or [""])[0]
            try:
                budget = int((qs.get("budget") or ["8000"])[0])
            except ValueError:
                budget = 8000
            try:
                self._send_json(ide_context.build_context(q, budget))
            except Exception as e:  # noqa: BLE001
                self._send_json({"ok": False, "error": "context_failed",
                                 "detail": "%s: %s" % (type(e).__name__, str(e)[:160])}, 500)
            return
        if path == "/api/ide/openhands/health":
            from legion_command import openhands_engine
            self._send_json(openhands_engine.health())
            return
        if path == "/api/ide/openhands/tasks":
            from legion_command import ide_worktree
            self._send_json({"ok": True, "tasks": ide_worktree.list_tasks()})
            return
        if path == "/api/ide/tasks":
            from legion_command import hermes_orchestrator
            self._send_json({"ok": True, "tasks": hermes_orchestrator.list_tasks()})
            return
        if path == "/api/ide/openhands/sessions":
            from urllib.parse import parse_qs
            from legion_command import openhands_engine
            qs = parse_qs(urlparse(self.path).query)
            try:
                limit = int((qs.get("limit") or ["30"])[0])
            except ValueError:
                limit = 30
            self._send_json({"ok": True, "sessions": openhands_engine.sessions(limit)})
            return
        if path == "/api/ide/openhands/conversation":
            from urllib.parse import parse_qs
            from legion_command import openhands_engine
            qs = parse_qs(urlparse(self.path).query)
            cid = (qs.get("id") or [""])[0]
            self._send_json({"ok": bool(cid), "events": openhands_engine.events(cid)})
            return
        if path == "/api/ide/openhands/stream":
            import json as _json
            import time as _time
            from urllib.parse import parse_qs
            from legion_command import openhands_engine
            qs = parse_qs(urlparse(self.path).query)
            cid = (qs.get("id") or [""])[0]
            if not cid:
                self._send_json({"ok": False, "error": "conversation_id_required"}, 400)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            try:
                while True:
                    d = openhands_engine.status(cid)
                    payload = _json.dumps(d, ensure_ascii=False)
                    self.wfile.write(("event: status\ndata: " + payload + "\n\n").encode("utf-8"))
                    self.wfile.flush()
                    if d.get("terminal") or d.get("ok") is False:
                        break
                    _time.sleep(0.8)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            return
        if path == "/api/ide/openhands/status":
            from urllib.parse import parse_qs
            from legion_command import openhands_engine
            qs = parse_qs(urlparse(self.path).query)
            self._send_json(openhands_engine.status((qs.get("id") or [""])[0]))
            return
        if path == "/api/snapshot":
            self._send_json(build_snapshot())
            return
        if path == "/api/intent":
            # 席位意图卡契约（只读）：六行一次算好，任何壳只渲染，不做二次派生。
            # 失败关闭：构建异常一律 500 + 错误码，绝不返回半份卡——
            # 半份卡会被前端当成全量渲染，比报错更危险。
            try:
                from legion_command.intent import build_cards

                self._send_json(build_cards(_PLATFORM))
            except Exception as e:  # noqa: BLE001
                self._send_json(
                    {
                        "ok": False,
                        "error": "intent_failed",
                        "detail": "%s: %s" % (type(e).__name__, str(e)[:200]),
                    },
                    500,
                )
            return
        if path == "/api/evolution":
            self._send_json(self._evolution())
            return
        if path == "/api/case/list":
            self._send_json({"ok": True, "cases": case_api.list_cases()})
            return
        if path == "/api/case/latest":
            c = case_api.latest_case()
            self._send_json({"ok": True, "case": c})
            return
        m_case = re.fullmatch(r"/api/case/get/(CASE-[A-Za-z0-9._-]{3,80})", path)
        if m_case:
            c = case_api.get_case(m_case.group(1))
            self._send_json({"ok": bool(c), "case": c}, 200 if c else 404)
            return
        if path == "/api/hq/meeting/stats":
            self._send_json(hq_stats.board(HQ_MEETINGS_DIR))
            return
        if path == "/api/hq/meeting/acceptance/evidence":
            self._send_json(hq_acceptance.gather(
                _BASELINE_FILE, _REDTEAM_DIR, _ACCEPTANCE_DIR))
            return
        if path == "/api/hq/agenda":
            self._send_json(hq_agenda.load_agenda(COUNCIL_DATA))
            return
        if path == "/api/hq/meeting/list":
            self._send_json(hq_meeting.list_meetings(HQ_MEETINGS_DIR))
            return
        m_hq = re.fullmatch(r"/api/hq/meeting/(hq-\d{8}-\d{6})", path)
        if m_hq:
            self._send_json(hq_meeting.get_meeting(HQ_MEETINGS_DIR, m_hq.group(1)))
            return
        if path == "/api/meeting/list":
            self._send_json(self._meeting_list())
            return
        m2 = re.fullmatch(r"/api/meeting/(warroom-\d{8}-\d{6})", path)
        if m2:
            res = self._meeting_get(m2.group(1))
            if res.get("ok"):
                self._send_json(res)
            elif res.get("error") == "meeting_not_found":
                self._send_json(res, 404)
            else:
                self._send_json(res, 400)  # 非法 id / 不可读一律 4xx，不拿 200 装成功
            return
        if path.startswith("/api/meeting/"):
            # 形如 /api/meeting/../x 一律拒绝，不回显原始串（避免反射注入面）
            self._send_json({"ok": False, "error": "bad_meeting_id"}, 400)
            return
        if path == "/api/org/audit":
            # 编制完整性审计（只读）：回答「这个军团到底多少人、谁在干嘛」的前置事实。
            # 不写任何真源；缺口清单不是档案，charter_detail 须田丰归口、老田裁定。
            try:
                from engine.org_audit import audit

                self._send_json(audit(_PLATFORM))
            except FileNotFoundError as e:
                self._send_json(
                    {"ok": False, "error": "org_missing", "detail": str(e)[:200]}, 500
                )
            except Exception as e:  # noqa: BLE001
                self._send_json(
                    {
                        "ok": False,
                        "error": "audit_failed",
                        "detail": "%s: %s" % (type(e).__name__, str(e)[:200]),
                    },
                    500,
                )
            return
        if path == "/api/zhaoyun":
            # 赵云 · 治理席只读视图（身份/能力卡/判例库/AI建议/时钟/保活/签字链）
            try:
                from legion_command.zhaoyun_view import build_view

                self._send_json(build_view(_PLATFORM))
            except Exception as e:  # noqa: BLE001
                self._send_json(
                    {
                        "ok": False,
                        "error": "zhaoyun_view_failed",
                        "detail": "%s: %s" % (type(e).__name__, str(e)[:200]),
                    },
                    500,
                )
            return
        if path in ("/v1", "/v1/"):
            self._send_json({"ok": True, "service": "dsh-llm-gateway",
                             "endpoints": {
                                 "chat": "POST /v1/chat/completions",
                                 "models": "GET /v1/models",
                                 "health": "GET /v1/health",
                                 "aliases": {"auto": "强模型优先→本地兜底",
                                             "strong": "仅云端，不降级本地",
                                             "local": "仅本地 9B"}},
                             "auth": "可选：Authorization: Bearer vk-..."})
            return
        if path == "/v1/health":
            self._send_json({"ok": True, "service": "dsh-llm-gateway"})
            return
        if path == "/v1/models":
            self._send_json(
                {
                    "object": "list",
                    "data": [
                        {"id": "gpt-6-sol", "object": "model", "owned_by": "relay"},
                        {"id": "glm-5.3", "object": "model", "owned_by": "zhipu"},
                        {
                            "id": "deepseek-chat",
                            "object": "model",
                            "owned_by": "deepseek",
                        },
                        {"id": "qwen3.5:9b", "object": "model", "owned_by": "local"},
                        {"id": "auto", "object": "model", "owned_by": "gateway"},
                        {"id": "gpt6", "object": "model", "owned_by": "gateway"},
                        {"id": "gpt-6-sol", "object": "model", "owned_by": "relay"},
                        {"id": "strong", "object": "model", "owned_by": "gateway"},
                        {"id": "local", "object": "model", "owned_by": "gateway"},
                        {"id": "local4", "object": "model", "owned_by": "gateway"},
                        {"id": "local9", "object": "model", "owned_by": "gateway"},
                        {"id": "local35", "object": "model", "owned_by": "gateway"},
                    ],
                }
            )
            return
        if path == "/api/keys/state":
            try:
                from legion_command import keypool

                self._send_json(keypool.state())
            except Exception as e:  # noqa: BLE001
                self._send_json(
                    {
                        "ok": False,
                        "error": "keypool_state_failed",
                        "detail": "%s: %s" % (type(e).__name__, str(e)[:200]),
                    },
                    500,
                )
            return
        if path == "/api/auto/status":
            out = {"ok": True, "mode": "smart", "routing": {
                "auto": "平台中转站优先 → 强模型 → 本地兜底",
                "strong": "仅云端强模型，不降级本地",
                "local": "仅本地 qwen3.5:9b",
                "gpt6": "平台中转站 GPT-6"}}
            try:
                from legion_command import keypool
                st = keypool.state()
                relay = (st.get("seats") or {}).get("relay") or {}
                rk = relay.get("keys") or []
                healthy = sum(1 for k in rk if k.get("status") == "healthy")
                total_keys = sum(len((v.get("keys") or [])) for v in (st.get("seats") or {}).values())
                out["upstream"] = {"relay_configured": bool(rk),
                                   "relay_keys": [{"masked": k.get("masked"),
                                                   "status": k.get("status"),
                                                   "model": k.get("model")} for k in rk],
                                   "healthy": healthy}
                out["keys_total"] = total_keys
                if (not rk) or healthy == 0:
                    out["mode"] = "needs_admin_key"
                    out["reason"] = "no_healthy_upstream_key"
            except Exception as e:  # noqa: BLE001
                out["upstream"] = {"relay_configured": False,
                                   "error": "%s: %s" % (type(e).__name__, str(e)[:120])}
            try:
                with safe_urlopen("http://127.0.0.1:11434/api/tags", timeout=2) as r:
                    tags = json.loads(r.read().decode("utf-8", "replace"))
                names = [m.get("name", "") for m in (tags.get("models") or [])]
                out["local"] = {"online": True,
                                "present": any(n.startswith("qwen3.5:9b") for n in names)}
            except Exception:  # noqa: BLE001
                out["local"] = {"online": False, "present": False}
            try:
                import json as _json
                reg = _json.loads((_PLATFORM.parent / "config" / "ai_registry.json").read_text(encoding="utf-8"))
                out["agents"] = {"count": len(reg or [])}
            except Exception:  # noqa: BLE001
                out["agents"] = {"count": 0}
            self._send_json(out)
            return
        if path == "/api/models/status":
            out = {"ok": True, "local": {}, "relay_gpt6": {}}
            try:
                with safe_urlopen("http://127.0.0.1:11434/api/tags", timeout=2) as r:
                    tags = json.loads(r.read().decode("utf-8", "replace"))
                ms = []
                for m in (tags.get("models") or []):
                    n = m.get("name", "")
                    if n.lower().startswith("bge"):
                        continue
                    ms.append({"name": n,
                               "size_gb": round(float(m.get("size") or 0) / (1024 ** 3), 2)})
                hit = next((m for m in ms if m["name"].startswith("qwen3.5:9b")), None)
                out["local"] = {"online": True, "model": "qwen3.5:9b",
                                "endpoint": "http://127.0.0.1:11434",
                                "present": bool(hit), "models": ms}
            except Exception as e:  # noqa: BLE001
                out["local"] = {"online": False, "model": "qwen3.5:9b",
                                "endpoint": "http://127.0.0.1:11434",
                                "error": "%s: %s" % (type(e).__name__, str(e)[:120])}
            try:
                from legion_command import keypool
                st = keypool.state()
                relay = (st.get("seats") or {}).get("relay") or {}
                keys = relay.get("keys") or []
                out["relay_gpt6"] = {"configured": bool(keys),
                                     "active_key_id": relay.get("active_key_id"),
                                     "keys": [{"masked": k.get("masked"),
                                               "status": k.get("status"),
                                               "model": k.get("model")} for k in keys],
                                     "model": "gpt-6-sol"}
            except Exception as e:  # noqa: BLE001
                out["relay_gpt6"] = {"configured": False,
                                     "error": "%s: %s" % (type(e).__name__, str(e)[:120])}
            self._send_json(out)
            return
        if path == "/api/model/prefs":
            try:
                from legion_command import model_prefs
                self._send_json(model_prefs.state())
            except Exception as e:  # noqa: BLE001
                self._send_json({"ok": False, "error": "model_prefs_failed",
                                 "detail": "%s: %s" % (type(e).__name__, str(e)[:200])}, 500)
            return
        if path == "/api/planner/status":
            try:
                from legion_command import planner_rules
                self._send_json({"ok": True, "signed": planner_rules.signed_rules(),
                                 "candidate": planner_rules.candidate_rules(),
                                 "active": planner_rules.active_rules()})
            except Exception as e:  # noqa: BLE001
                self._send_json({"ok": False, "error": "planner_status_failed",
                                 "detail": "%s: %s" % (type(e).__name__, str(e)[:200])}, 500)
            return
        if path == "/api/mission/evolve":
            try:
                from legion_command import mission_evolver
                self._send_json({"ok": True, "data": mission_evolver.evolve()})
            except Exception as e:  # noqa: BLE001
                self._send_json({"ok": False, "error": "mission_evolve_failed",
                                 "detail": "%s: %s" % (type(e).__name__, str(e)[:200])}, 500)
            return
        if path == "/api/mission/experience":
            try:
                from legion_command import mission_evolver
                self._send_json({"ok": True, "latest": mission_evolver.load_latest(),
                                 "candidates": mission_evolver.list_candidates()})
            except Exception as e:  # noqa: BLE001
                self._send_json({"ok": False, "error": "mission_experience_failed",
                                 "detail": "%s: %s" % (type(e).__name__, str(e)[:200])}, 500)
            return
        if path == "/api/mission/list":
            try:
                from legion_command import mission_planner
                self._send_json({"ok": True, "items": mission_planner.list_missions()})
            except Exception as e:  # noqa: BLE001
                self._send_json({"ok": False, "error": "mission_list_failed",
                                 "detail": "%s: %s" % (type(e).__name__, str(e)[:200])}, 500)
            return
        if path == "/api/credentials":
            try:
                from legion_command import credential_catalog
                self._send_json(credential_catalog.catalog())
            except Exception as e:  # noqa: BLE001
                self._send_json({"ok": False, "error": "credential_catalog_failed",
                                 "detail": "%s: %s" % (type(e).__name__, str(e)[:200])}, 500)
            return
        if path == "/api/mcp/tushare/tools":
            try:
                from legion_command import mcp_bridge
                tools = mcp_bridge.list_tools()
                self._send_json({"ok": True, "count": len(tools), "tools": tools})
            except Exception as e:  # noqa: BLE001
                self._send_json({"ok": False, "error": "mcp_tools_failed",
                                 "detail": "%s: %s" % (type(e).__name__, str(e)[:200])}, 500)
            return
        if path == "/api/router/vk":
            try:
                from legion_command import seat_router
                _cfg = seat_router.load_config()
                self._send_json({"ok": True, "items": seat_router.list_vk(),
                                 "aliases": sorted((_cfg.get("aliases") or {}).keys())})
            except Exception as e:  # noqa: BLE001
                self._send_json({"ok": False, "error": "vk_list_failed",
                                 "detail": "%s: %s" % (type(e).__name__, str(e)[:200])}, 500)
            return
        if path.startswith("/api/zhaoyun/"):
            # WO-ZHAOYUN-DRILL：治理席"下钻"数据（能力详证/任务偏差/记忆/模型/请求）
            try:
                from legion_command import zhaoyun_drill as zd

                sub = path[len("/api/zhaoyun/") :]
                if sub == "tasks":
                    self._send_json(zd.tasks_view(_PLATFORM))
                elif sub == "memory":
                    self._send_json(zd.memory_view(_PLATFORM))
                elif sub == "model":
                    self._send_json(zd.model_view(_PLATFORM))
                elif sub == "requests":
                    self._send_json(zd.requests_view(_PLATFORM))
                elif sub == "capabilities":
                    self._send_json(zd.capabilities_detail(_PLATFORM))
                elif sub == "skills":
                    sys.path.insert(0, str(_PLATFORM / "tools"))
                    from skill_registry import list_skills

                    self._send_json(list_skills())
                elif sub == "selfaudit":
                    from legion_command import zhaoyun_cap2 as c2

                    self._send_json(c2.selfaudit(_PLATFORM))
                elif sub == "routing":
                    from legion_command import zhaoyun_cap2 as c2

                    self._send_json(c2.routing(_PLATFORM))
                elif sub == "interventions":
                    from legion_command import zhaoyun_cap2 as c2

                    self._send_json(c2.interventions(_PLATFORM))
                elif sub == "memory/curation":
                    from legion_command import zhaoyun_cap2 as c2

                    self._send_json(c2.memory_curation())
                elif sub == "overrides":
                    from legion_command import zhaoyun_cap2 as c2

                    self._send_json(c2.overrides(_PLATFORM))
                elif sub == "traces":
                    sys.path.insert(0, str(_PLATFORM))
                    from observability import trace as tr

                    self._send_json(tr.list_traces(50))
                elif re.fullmatch(r"trace/[0-9a-f]{8,64}", sub):
                    sys.path.insert(0, str(_PLATFORM))
                    from observability import trace as tr

                    self._send_json(tr.get_trace(sub.split("/", 1)[1]))
                elif sub == "evals":
                    latest = _PLATFORM / "evals" / "reports" / "latest.json"
                    try:
                        self._send_json(json.loads(latest.read_text(encoding="utf-8")))
                    except (OSError, ValueError):
                        self._send_json({"ok": False, "error": "no_report"})
                elif sub == "mcp":
                    sys.path.insert(0, str(_PLATFORM))
                    from legion_mcp import registry as mreg

                    self._send_json(mreg.summary())
                elif sub == "quant":
                    from urllib.parse import parse_qs

                    from legion_command import zhaoyun_quant as zq

                    qs = parse_qs(urlparse(self.path).query)
                    self._send_json(zq.quant_view(
                        tool=(qs.get("tool") or ["stock_basic"])[0],
                        code=(qs.get("code") or [""])[0],
                        start=(qs.get("start") or [""])[0],
                        end=(qs.get("end") or [""])[0],
                        limit=(qs.get("limit") or ["20"])[0]))
                elif sub == "wargame":
                    from urllib.parse import parse_qs

                    from legion_command import zhaoyun_wargame as zw

                    qs = parse_qs(urlparse(self.path).query)
                    self._send_json(zw.wargame_view(
                        question=(qs.get("question") or [""])[0],
                        code=(qs.get("code") or [""])[0],
                        vars_json=(qs.get("vars") or [""])[0]))
                elif sub == "debate":
                    from urllib.parse import parse_qs

                    from legion_command import zhaoyun_debate as zd2

                    qs = parse_qs(urlparse(self.path).query)
                    self._send_json(zd2.debate_view(
                        question=(qs.get("question") or [""])[0],
                        code=(qs.get("code") or [""])[0]))
                elif sub == "memory/v2":
                    sys.path.insert(0, str(_PLATFORM))
                    from memory import store as ms

                    self._send_json(ms.stats())
                elif sub == "sandbox":
                    sys.path.insert(0, str(_PLATFORM))
                    from sandbox import runner as sbr

                    self._send_json(sbr.recent())
                elif sub == "outbox":
                    sys.path.insert(0, str(_PLATFORM))
                    from notify import outbox as ob

                    self._send_json(ob.recent())
                elif sub == "research":
                    sys.path.insert(0, str(_PLATFORM))
                    from research import web as rw

                    self._send_json(rw.recent_citations())
                elif sub == "repair":
                    sys.path.insert(0, str(_PLATFORM))
                    from repair import lane as rl

                    self._send_json(rl.recent())
                elif sub == "evals/judge":
                    sys.path.insert(0, str(_PLATFORM / "evals"))
                    from judge import latest  # noqa: WPS433

                    self._send_json(latest())
                elif re.fullmatch(r"cap/[A-Za-z0-9_-]{1,64}", sub):
                    cap = sub.split("/", 1)[1]
                    rep = zd.capabilities_detail(_PLATFORM)
                    row = next(
                        (x for x in (rep.get("items") or []) if x.get("name") == cap),
                        None,
                    )
                    if row is None:
                        self._send_json(
                            {"ok": False, "error": "cap_not_found", "cap": cap}, 404
                        )
                    else:
                        # drill-evidence：记录一次真实"下钻使用"+最近使用
                        try:
                            from legion_command import zhaoyun_cap2 as c2

                            usage = c2.last_used(_PLATFORM, cap)
                            c2.log_usage(_PLATFORM, cap, "page")
                        except Exception:  # noqa: BLE001
                            usage = {"ok": False}
                        try:
                            from observability import trace as tr

                            tr.record(
                                tr.trace_id(),
                                "cap.drill",
                                "tool",
                                attrs={
                                    "zhaoyun.capability": cap,
                                    "zhaoyun.source": "page",
                                },
                            )
                        except Exception as exc:
                            logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/legion_command/serve.py", exc)
                        self._send_json({"ok": True, "capability": row, "usage": usage})
                else:
                    self._send_json({"ok": False, "error": "not_found: %s" % path}, 404)
            except Exception as e:  # noqa: BLE001
                self._send_json(
                    {
                        "ok": False,
                        "error": "zhaoyun_drill_failed",
                        "detail": "%s: %s" % (type(e).__name__, str(e)[:200]),
                    },
                    500,
                )
            return
        if path == "/api/tianfeng":
            # 田丰 · 产品/研发席只读视图（角色/授权/能力/运行态/链接）
            try:
                from legion_command.tianfeng_view import build_view

                self._send_json(build_view(_PLATFORM))
            except Exception as e:  # noqa: BLE001
                self._send_json(
                    {
                        "ok": False,
                        "error": "tianfeng_view_failed",
                        "detail": "%s: %s" % (type(e).__name__, str(e)[:200]),
                    },
                    500,
                )
            return
        if path == "/api/health":
            self._send_json({"ok": True, "service": "legion-command", "version": "1.0"})
            return
        if path == "/api/ide/version":
            # 2026-10-31 发布单元：只读 release artifact + manifest hash 校验
            try:
                from ide_release_info import build_info
                info = build_info()
                self._send_json(info, 200 if info.get("ok") else 503)
            except Exception as e:  # noqa: BLE001
                self._send_json({"ok": False, "error": "release_info_failed",
                                 "detail": "%s: %s" % (type(e).__name__, str(e)[:160])}, 500)
            return
        self._send_json({"ok": False, "error": "not_found: %s" % path}, 404)

    @staticmethod
    def _meeting_list():
        """纪要清单（只元信息，不带正文）：时间倒序，供回看页左侧列表。

        只读；目录不存在返回空清单而非报错（新建环境不应因无纪要而红）。
        """
        rows = []
        if MEETINGS_DIR.exists():
            for p in sorted(MEETINGS_DIR.glob("warroom-*.json"), reverse=True)[:200]:
                if not re.fullmatch(r"warroom-\d{8}-\d{6}\.json", p.name):
                    continue
                try:
                    d = json.loads(p.read_text(encoding="utf-8"))
                except (ValueError, OSError):
                    rows.append(
                        {
                            "id": p.stem,
                            "ts": "",
                            "broken": True,
                            "count": 0,
                            "participants": [],
                            "src_stats": {},
                            "source_verification": "unverified",
                        }
                    )
                    continue
                ms = d.get("minutes") or []
                stats = {"live": 0, "degraded": 0, "script": 0, "host": 0}
                for m in ms:
                    k = str(m.get("src", ""))
                    if k in stats:
                        stats[k] += 1
                rows.append(
                    {
                        "id": p.stem,
                        "ts": d.get("ts", ""),
                        "count": len(ms),
                        "participants": d.get("participants") or [],
                        "src_stats": stats,
                        "source_verification": "unverified",
                        "broken": False,
                    }
                )
        return {
            "ok": True,
            "schema_version": "1.0",
            "meetings": rows,
            "total": len(rows),
            "note": "只读清单；src_stats 统计客户端来源声明（含旧纪要），不是经服务端验证的真模型次数",
        }

    @staticmethod
    def _meeting_get(mid: str):
        """单份纪要正文。mid 已由路由正则限定为 warroom-YYYYMMDD-HHMMSS。"""
        target = (MEETINGS_DIR / (mid + ".json")).resolve()
        # 双保险：即便正则被绕过，也不允许越出纪要目录
        try:
            target.relative_to(MEETINGS_DIR.resolve())
        except ValueError:
            return {"ok": False, "error": "bad_meeting_id"}
        if not target.exists():
            return {"ok": False, "error": "meeting_not_found", "id": mid}
        try:
            doc = json.loads(target.read_text(encoding="utf-8"))
        except (ValueError, OSError) as e:
            return {
                "ok": False,
                "error": "meeting_unreadable: %s" % type(e).__name__,
                "id": mid,
            }
        doc["source_verification"] = "unverified"
        return {
            "ok": True,
            "schema_version": "1.0",
            "id": mid,
            "meeting": doc,
            "note": "只读回看；src 为客户端声明，未凭服务端真实模型回执验证（含历史纪要）",
        }

    @staticmethod
    def _evolution():
        """进化层只读视图：实验卡清单 + 经验库五域统计（不读不写经验正文）。"""
        import json as _json

        exp_dir = _PLATFORM / "data" / "experiments"
        cards = load_experiment_cards(exp_dir)
        lib = Path.home() / ".hermes" / "profiles" / "zhaoyun" / "experience"
        domains = {}
        index_rows = []
        idx = lib / "_index.jsonl"
        if idx.exists():
            for line in idx.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.strip():
                    try:
                        index_rows.append(_json.loads(line))
                    except ValueError:
                        continue
        for d in ("stock_screen", "execution", "tooling", "riskguard", "supervision"):
            domains[d] = {
                "entries": sum(1 for r in index_rows if r.get("domain") == d),
                "has_dir": (lib / d).exists(),
                "readonly": d == "riskguard",
            }
        recent = sorted(
            index_rows, key=lambda r: str(r.get("written_at") or ""), reverse=True
        )[:5]
        return {
            "ok": True,
            "experiments": cards,
            "experience_domains": domains,
            "entries_total": len(index_rows),
            "experience_verified_total": sum(
                1 for r in index_rows if r.get("verified")
            ),
            "experience_recent": [
                {
                    k: r.get(k)
                    for k in (
                        "entry_id",
                        "domain",
                        "confidence",
                        "signed_by",
                        "written_at",
                        "verified",
                    )
                }
                for r in recent
            ],
            "note": "只读视图；经验入库须签字（Z4）；风控域只读",
        }

    def do_POST(self):
        with self._request_scope():
            return self._do_POST_impl()

    def _oh_denied(self):
        try:
            from legion_command import oh_auth
            return oh_auth.authorize(dict(self.headers))
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": "oh_auth_error", "message": str(exc)[:160]}

    def _do_POST_impl(self):
        import json

        path = urlparse(self.path).path
        if not self._write_guard(path):
            return
        if path == "/api/ide/openhands/chat":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > 256 * 1024:
                    raise ValueError("bad_length")
                req = json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self._send_json({"ok": False, "error": "bad_json"}, 400)
                return
            if not isinstance(req, dict):
                self._send_json({"ok": False, "error": "bad_body"}, 400)
                return
            denied = self._oh_denied()
            if denied:
                self._send_json(denied, 403)
                return
            from legion_command import openhands_engine
            res = openhands_engine.chat(
                str(req.get("message") or ""),
                str(req.get("conversation_id") or ""),
                int(req.get("timeout") or 600),
                task=str(req.get("task") or ""),
                worktree=str(req.get("worktree") or ""),
                model=str(req.get("model") or "deepseek-flash"),
            )
            self._send_json(res, 200 if res.get("ok") else 502)
            return
        if path == "/api/ide/orchestrate":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > 64 * 1024:
                    raise ValueError("bad_length")
                req = json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self._send_json({"ok": False, "error": "bad_json"}, 400)
                return
            if not isinstance(req, dict):
                self._send_json({"ok": False, "error": "bad_body"}, 400)
                return
            denied = self._oh_denied()
            if denied:
                self._send_json(denied, 403)
                return
            from legion_command import hermes_orchestrator
            res = hermes_orchestrator.orchestrate(
                str(req.get("message") or ""),
                task=str(req.get("task") or ""),
                model=str(req.get("model") or ""),
                agent=str(req.get("agent") or ""),
            )
            self._send_json(res, 200 if res.get("ok") else 502)
            return
        if path in ("/api/ide/openhands/start", "/api/ide/openhands/interrupt",
                    "/api/ide/openhands/respond",
                    "/api/ide/openhands/sessions/delete"):
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > 64 * 1024:
                    raise ValueError("bad_length")
                req = json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self._send_json({"ok": False, "error": "bad_json"}, 400)
                return
            if not isinstance(req, dict):
                self._send_json({"ok": False, "error": "bad_body"}, 400)
                return
            denied = self._oh_denied()
            if denied:
                self._send_json(denied, 403)
                return
            from legion_command import openhands_engine
            if path.endswith("/start"):
                res = openhands_engine.start(
                    str(req.get("message") or ""),
                    str(req.get("conversation_id") or ""),
                    task=str(req.get("task") or ""),
                    worktree=str(req.get("worktree") or ""),
                    model=str(req.get("model") or "deepseek-flash"),
                    agent=str(req.get("agent") or ""),
                )
            elif path.endswith("/interrupt"):
                res = openhands_engine.interrupt(str(req.get("conversation_id") or ""))
            elif path.endswith("/sessions/delete"):
                cid = str(req.get("cid") or req.get("conversation_id") or "")
                res = {"ok": openhands_engine.delete_session(cid), "cid": cid}
            else:
                res = openhands_engine.respond(
                    str(req.get("conversation_id") or ""),
                    bool(req.get("accept")),
                    str(req.get("reason") or ""),
                )
            self._send_json(res, 200 if res.get("ok") else 409)
            return
        if path in ("/api/ide/openhands/diff", "/api/ide/openhands/commit"):
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > 64 * 1024:
                    raise ValueError("bad_length")
                req = json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self._send_json({"ok": False, "error": "bad_json"}, 400)
                return
            if not isinstance(req, dict):
                self._send_json({"ok": False, "error": "bad_body"}, 400)
                return
            if path.endswith("/commit"):
                denied = self._oh_denied()
                if denied:
                    self._send_json(denied, 403)
                    return
            from legion_command import ide_worktree
            task = str(req.get("task") or "").strip()
            if path.endswith("/diff"):
                res = ide_worktree.diff(task)
            else:
                res = ide_worktree.commit(task, str(req.get("message") or ""))
            self._send_json(res, 200 if res.get("ok") else 400)
            return
        if path == "/v1/chat/completions":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > 1024 * 1024:
                    self._send_json(
                        {
                            "error": {
                                "message": "bad_request",
                                "type": "invalid_request_error",
                            }
                        },
                        400,
                    )
                    return
                req = json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self._send_json(
                    {"error": {"message": "bad_json", "type": "invalid_request_error"}},
                    400,
                )
                return
            if not isinstance(req, dict):
                self._send_json(
                    {"error": {"message": "bad_body", "type": "invalid_request_error"}},
                    400,
                )
                return
            stream = bool(req.get("stream"))
            messages = req.get("messages") or []
            backend = str(self.headers.get("X-DSH-Backend") or req.get("backend") or "").strip()
            if backend == "litellm":
                # 可选上游：LiteLLM Router（Key 池→多 provider/fallback/成本）；默认仍是 keypool
                if req.get("stream"):
                    self._send_json({"error": {"message": "stream_not_supported_for_litellm",
                                               "type": "invalid_request_error"}}, 400)
                    return
                prompt = ""
                for m in reversed(messages):
                    if isinstance(m, dict) and m.get("role") == "user":
                        prompt = str(m.get("content") or "")
                        break
                lr, lr_status = _litellm_route(prompt)
                if not lr.get("ok"):
                    self._send_json(
                        {
                            "error": {
                                "message": lr.get("error") or "litellm_failed",
                                "type": (
                                    "invalid_request_error"
                                    if lr_status < 500
                                    else "upstream_error"
                                ),
                            },
                            "x_backend": "litellm",
                            "x_status": lr_status,
                        },
                        lr_status,
                    )
                    return
                self._send_json({
                    "id": "chatcmpl-dsh-lite-%s" % int(time.time() * 1000),
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": str(lr.get("used_model") or req.get("model") or "auto"),
                    "choices": [{"index": 0,
                                 "message": {"role": "assistant",
                                             "content": lr.get("reply", "")},
                                 "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                    "x_backend": "litellm",
                    "x_cost_usd": lr.get("cost_usd", 0),
                })
                return
            try:
                from legion_command import seat_router

                _auth = str(self.headers.get("Authorization") or "").strip()
                _token = _auth[7:].strip() if _auth.lower().startswith("bearer ") else _auth
                route = seat_router.resolve(str(req.get("model") or ""), _token)
                if not route.get("ok"):
                    self._send_json({"error": {"message": route.get("error"),
                                               "type": "auth_error"}},
                                    int(route.get("code") or 401))
                    return
                _model_req = str(req.get("model") or "").strip()
                _agent = str(self.headers.get("X-DSH-Agent") or route.get("owner")
                             or req.get("agent") or "").strip()
                _task = str(self.headers.get("X-DSH-Task") or req.get("task") or "").strip()
                _alloc = None
                _pref = "auto"
                if _model_req in ("", "auto") and _agent:
                    try:
                        from legion_command import model_prefs
                        _pref = model_prefs.get(_agent)
                    except Exception:  # noqa: BLE001
                        _pref = "auto"
                if _pref and _pref != "auto":
                    _pr = seat_router.resolve(_pref, _token)
                    if _pr.get("ok"):
                        route = _pr
                        route["alias"] = _pref
                        route["pref"] = _pref
                if _model_req in ("", "auto") and (_pref in ("", "auto")):
                    from legion_command import model_allocator
                    _alloc = model_allocator.allocate(_agent, _task, messages)
                    route["seats"] = list(_alloc.get("seats") or [])
                    route["allow_local"] = bool(_alloc.get("allow_local", True))
                    route["local_model"] = _alloc.get("local_model")
                    route["alias"] = _alloc.get("tier")
                    owner = route.get("owner")
                    if owner and _alloc.get("tier") != "local" and owner not in route["seats"]:
                        route["seats"] = [owner] + route["seats"]
                from legion_command import keypool

                try:
                    _max_tokens = int(req.get("max_tokens") or 4000)
                except (TypeError, ValueError):
                    _max_tokens = 4000
                _max_tokens = max(256, min(_max_tokens, 16000))
                res = keypool.auto_chat(messages, seats=route.get("seats") or [],
                                        allow_local=bool(route.get("allow_local", True)),
                                        local_model=route.get("local_model") or None,
                                        model_filter=route.get("model") or None,
                                        max_tokens=_max_tokens,
                                        tools=req.get("tools") or None,
                                        tool_choice=req.get("tool_choice"))
                if route.get("vk_id"):
                    try:
                        seat_router.record_usage(
                            str(route["vk_id"]), ok=bool(res.get("ok")),
                            model=str(route.get("model") or _model_req or route.get("alias") or ""))
                    except Exception:
                        pass
            except Exception as e:  # noqa: BLE001
                res = {
                    "ok": False,
                    "error": "gateway_failed",
                    "detail": "%s: %s" % (type(e).__name__, str(e)[:200]),
                }
            if not res.get("ok"):
                self._send_json(
                    {
                        "error": {
                            "message": res.get("detail") or res.get("error"),
                            "type": "upstream_error",
                        },
                        "x_attempts": res.get("attempts", []),
                    },
                    502,
                )
                return
            channel = res.get("channel", "")
            reply = str(res.get("reply", ""))
            model = str(req.get("model") or "auto")
            if stream:
                # OpenAI SSE 兼容（OpenClaw 需要流式）；按完整回答切片回放
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                cid = "chatcmpl-dsh-%s" % int(time.time() * 1000)
                created = int(time.time())

                def _chunk(delta, finish=None):
                    payload = {
                        "id": cid,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "x_channel": channel,
                        "choices": [
                            {"index": 0, "delta": delta, "finish_reason": finish}
                        ],
                    }
                    self.wfile.write(
                        (
                            "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
                        ).encode("utf-8")
                    )
                    self.wfile.flush()

                try:
                    _chunk({"role": "assistant", "content": ""})
                    for i in range(0, len(reply), 32):
                        _chunk({"content": reply[i : i + 32]})
                    stream_tool_calls = res.get("tool_calls") or []
                    if stream_tool_calls:
                        for idx, tc in enumerate(stream_tool_calls):
                            fn = tc.get("function") or {}
                            _chunk({"tool_calls": [{
                                "index": idx,
                                "id": tc.get("id") or "call_dsh_%d_%d" % (created, idx),
                                "type": tc.get("type") or "function",
                                "function": {
                                    "name": fn.get("name") or "",
                                    "arguments": fn.get("arguments") or "{}",
                                },
                            }]})
                        _chunk({}, "tool_calls")
                    else:
                        _chunk({}, "stop")
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError) as exc:
                    logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/legion_command/serve.py", exc)
                return
            tool_calls = res.get("tool_calls") or []
            message = {"role": "assistant", "content": reply}
            if tool_calls:
                message["tool_calls"] = tool_calls
            self._send_json(
                {
                    "id": "chatcmpl-dsh-%s" % int(time.time() * 1000),
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "message": message,
                            "finish_reason": res.get("finish_reason")
                            or ("tool_calls" if tool_calls else "stop"),
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": 0,
                    },
                    "x_channel": channel,
                    "x_alias": route.get("alias"),
                    "x_allocator": (_alloc or {}),
                    "x_pref": (route.get("pref") or "auto"),
                    "x_key_owner": route.get("owner"),
                    "x_attempts": res.get("attempts", []),
                }
            )
            return
        if path == "/api/keys/auto_chat":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > 128 * 1024:
                    self._send_json(
                        {
                            "ok": False,
                            "error": "bad_request",
                            "reason": "content_length_out_of_range",
                        },
                        400,
                    )
                    return
                req = json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self._send_json({"ok": False, "error": "bad_request"}, 400)
                return
            try:
                from legion_command import keypool

                res = keypool.auto_chat((req or {}).get("messages") or [])
            except Exception as e:  # noqa: BLE001
                res = {
                    "ok": False,
                    "error": "auto_chat_failed",
                    "detail": "%s: %s" % (type(e).__name__, str(e)[:200]),
                }
            self._send_json(res, 200 if res.get("ok") else 400)
            return
        if path == "/api/router/vk":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > 32 * 1024:
                    self._send_json({"ok": False, "error": "bad_request"}, 400)
                    return
                req = json.loads(self.rfile.read(length).decode("utf-8"))
                from legion_command import seat_router
                act = str((req or {}).get("action") or "create")
                if act == "create":
                    item = seat_router.create_vk(
                        str(req.get("owner") or ""),
                        str(req.get("alias") or "auto"),
                        req.get("allowed"),
                        quota=int(req.get("quota") or 0))
                    self._send_json({"ok": True, "item": item})
                    return
                if act == "disable":
                    item = seat_router.disable_vk(str(req.get("id") or ""))
                    self._send_json({"ok": True, "item": item})
                    return
                if act == "enable":
                    item = seat_router.enable_vk(str(req.get("id") or ""))
                    self._send_json({"ok": True, "item": item})
                    return
                if act == "quota":
                    item = seat_router.set_quota(str(req.get("id") or ""),
                                                 int(req.get("quota") or 0))
                    self._send_json({"ok": True, "item": item})
                    return
                self._send_json({"ok": False, "error": "unknown_action"}, 400)
                return
            except ValueError as e:
                self._send_json({"ok": False, "error": str(e)[:200]}, 400)
                return
            except Exception as e:  # noqa: BLE001
                self._send_json({"ok": False, "error": "vk_action_failed",
                                 "detail": "%s: %s" % (type(e).__name__, str(e)[:200])}, 500)
                return
        if path == "/api/stt":
            try:
                import base64
                import tempfile
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > 20 * 1024 * 1024:
                    self._send_json({"ok": False, "error": "audio_too_large"}, 400)
                    return
                req = json.loads(self.rfile.read(length).decode("utf-8"))
                audio_b64 = str(req.get("audio_b64") or "")
                mime = str(req.get("mime") or "audio/webm")
                if not audio_b64:
                    self._send_json({"ok": False, "error": "empty_audio"}, 400)
                    return
                raw = base64.b64decode(audio_b64)
                ext = ".webm" if "webm" in mime else (".ogg" if "ogg" in mime else ".wav")
                repo = _PLATFORM.parent
                ffmpeg = repo / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
                script = repo / "tools" / "stt_sapi.ps1"
                if not ffmpeg.exists() or not script.exists():
                    self._send_json({"ok": False, "error": "stt_engine_missing"}, 500)
                    return
                t0 = time.time()
                with tempfile.TemporaryDirectory(prefix="dsh_stt_") as td:
                    src = pathlib.Path(td) / ("audio" + ext)
                    wav = pathlib.Path(td) / "audio16k.wav"
                    src.write_bytes(raw)
                    conv = subprocess.run(
                        [str(ffmpeg), "-y", "-i", str(src), "-ar", "16000",
                         "-ac", "1", "-f", "wav", str(wav)],
                        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
                    if conv.returncode != 0 or not wav.exists():
                        self._send_json({"ok": False, "error": "audio_convert_failed",
                                         "detail": (conv.stderr or "")[-300:]}, 500)
                        return
                    pr = subprocess.run(
                        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                         "-File", str(script), "-Wav", str(wav)],
                        capture_output=True, text=True, encoding="utf-8",
                        errors="replace", timeout=90)
                    text = (pr.stdout or "").strip()
                self._send_json({"ok": True, "text": text, "engine": "windows-sapi-zh-CN",
                                 "seconds": round(time.time() - t0, 2)})
            except Exception as e:  # noqa: BLE001
                self._send_json({"ok": False, "error": "stt_failed",
                                 "detail": "%s: %s" % (type(e).__name__, str(e)[:200])}, 500)
            return
        if path == "/api/mission/promote":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > 16 * 1024:
                    self._send_json({"ok": False, "error": "bad_request"}, 400)
                    return
                req = json.loads(self.rfile.read(length).decode("utf-8"))
                from legion_command import mission_evolver
                out = mission_evolver.promote(str(req.get("candidate_id") or ""),
                                              str(req.get("signed_by") or ""),
                                              str(req.get("note") or ""))
                self._send_json(out)
            except Exception as e:  # noqa: BLE001
                self._send_json({"ok": False, "error": "mission_promote_failed",
                                 "detail": "%s: %s" % (type(e).__name__, str(e)[:200])}, 500)
            return
        if path == "/api/mission/plan":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > 2 * 1024 * 1024:
                    self._send_json({"ok": False, "error": "doc_too_large"}, 400)
                    return
                req = json.loads(self.rfile.read(length).decode("utf-8"))
                from legion_command import mission_planner
                res = mission_planner.plan(str(req.get("name") or ""),
                                           str(req.get("doc") or ""),
                                           use_llm=bool(req.get("use_llm", True)))
                self._send_json({"ok": bool(res.get("valid")), "plan": res})
            except Exception as e:  # noqa: BLE001
                self._send_json({"ok": False, "error": "mission_plan_failed",
                                 "detail": "%s: %s" % (type(e).__name__, str(e)[:200])}, 500)
            return
        if path == "/api/mission/dispatch":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > 2 * 1024 * 1024:
                    self._send_json({"ok": False, "error": "bad_request"}, 400)
                    return
                req = json.loads(self.rfile.read(length).decode("utf-8"))
                from legion_command import mission_dispatch, mission_planner
                plan = req.get("plan")
                if not plan and req.get("mission_id"):
                    plan = mission_planner.load_plan(str(req["mission_id"]))
                if not plan:
                    self._send_json({"ok": False, "error": "plan_required"}, 400)
                    return
                res = mission_dispatch.dispatch(plan, int(req.get("parallel") or 3),
                                                create=bool(req.get("create", True)))
                self._send_json(res)
            except Exception as e:  # noqa: BLE001
                self._send_json({"ok": False, "error": "mission_dispatch_failed",
                                 "detail": "%s: %s" % (type(e).__name__, str(e)[:200])}, 500)
            return
        if path == "/api/mcp/tushare/call":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > 256 * 1024:
                    self._send_json({"ok": False, "error": "bad_request"}, 400)
                    return
                req = json.loads(self.rfile.read(length).decode("utf-8"))
                from legion_command import mcp_bridge
                result = mcp_bridge.call_tool(str(req.get("tool") or ""),
                                              req.get("args") if isinstance(req.get("args"), dict) else {})
                self._send_json({"ok": True, "tool": req.get("tool"), "result": result})
            except ValueError as e:
                self._send_json({"ok": False, "error": str(e)[:200]}, 400)
            except Exception as e:  # noqa: BLE001
                self._send_json({"ok": False, "error": "mcp_call_failed",
                                 "detail": "%s: %s" % (type(e).__name__, str(e)[:200])}, 500)
            return
        if path == "/api/credentials/set":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > 128 * 1024:
                    self._send_json({"ok": False, "error": "bad_request"}, 400)
                    return
                req = json.loads(self.rfile.read(length).decode("utf-8"))
                name = str(req.get("name") or "").strip()
                secret = str(req.get("secret") or "").strip()
                category = str(req.get("category") or "data").strip()
                env = str(req.get("env") or "").strip()
                import re as _re
                if not _re.fullmatch(r"[A-Za-z0-9_.-]{2,64}", name):
                    self._send_json({"ok": False, "error": "invalid_name"}, 400)
                    return
                if len(secret) < 6 or len(secret) > 4096:
                    self._send_json({"ok": False, "error": "invalid_secret"}, 400)
                    return
                if env and not _re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{1,63}", env):
                    self._send_json({"ok": False, "error": "invalid_env"}, 400)
                    return
                # 服务解释器不一定有 pywin32；调用带 DPAPI 的 py312 子进程
                _cs_path = _PLATFORM.parent / "tools" / "credential_store.py"
                _py = _PLATFORM.parent.parent / "py312" / "python.exe"
                if not _py.is_file():
                    _py = Path(sys.executable)
                _env = dict(os.environ)
                _env["DSH_CREDENTIAL_VALUE"] = secret
                _env["PYTHONIOENCODING"] = "utf-8"
                _p = subprocess.run(
                    [str(_py), "-X", "utf8", str(_cs_path), "set", name,
                     "--category", category, "--env", env],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    env=_env, timeout=30,
                )
                if _p.returncode != 0:
                    raise RuntimeError("credential_store rc=%s" % _p.returncode)
                item = json.loads(_p.stdout)
                self._send_json({"ok": True, "item": {k: v for k, v in item.items() if k != "secret"}})
            except Exception as e:  # noqa: BLE001
                self._send_json({"ok": False, "error": "credential_set_failed",
                                 "detail": "%s: %s" % (type(e).__name__, str(e)[:200])}, 500)
            return
        if path == "/api/planner/build-rules":
            try:
                from legion_command import planner_rules
                self._send_json(planner_rules.build_rules())
            except Exception as e:  # noqa: BLE001
                self._send_json({"ok": False, "error": "planner_build_failed",
                                 "detail": "%s: %s" % (type(e).__name__, str(e)[:200])}, 500)
            return
        if path == "/api/planner/ab":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > 2 * 1024 * 1024:
                    self._send_json({"ok": False, "error": "bad_request"}, 400)
                    return
                req = json.loads(self.rfile.read(length).decode("utf-8"))
                from legion_command import planner_rules
                rep = planner_rules.ab_compare(str(req.get("doc") or ""),
                                               str(req.get("name") or "A/B 对比"),
                                               bool(req.get("use_signed", True)))
                self._send_json({"ok": True, "report": rep})
            except Exception as e:  # noqa: BLE001
                self._send_json({"ok": False, "error": "planner_ab_failed",
                                 "detail": "%s: %s" % (type(e).__name__, str(e)[:200])}, 500)
            return
        if path == "/api/mission/worker":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > 64 * 1024:
                    self._send_json({"ok": False, "error": "bad_request"}, 400)
                    return
                req = json.loads(self.rfile.read(length).decode("utf-8"))
                from legion_command import mission_worker
                mid = str(req.get("mission_id") or "")
                tid = str(req.get("task_id") or "")
                dry = bool(req.get("dry_run", False))
                if tid:
                    res = mission_worker.run_task(mid, tid, dry)
                else:
                    res = mission_worker.poll(mid, int(req.get("limit") or 1), dry)
                self._send_json(res)
            except Exception as e:  # noqa: BLE001
                self._send_json({"ok": False, "error": "mission_worker_failed",
                                 "detail": "%s: %s" % (type(e).__name__, str(e)[:200])}, 500)
            return
        if path == "/api/mission/advance":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > 64 * 1024:
                    self._send_json({"ok": False, "error": "bad_request"}, 400)
                    return
                req = json.loads(self.rfile.read(length).decode("utf-8"))
                from legion_command import mission_dispatch
                self._send_json(mission_dispatch.advance(str(req.get("mission_id") or "")))
            except Exception as e:  # noqa: BLE001
                self._send_json({"ok": False, "error": "mission_advance_failed",
                                 "detail": "%s: %s" % (type(e).__name__, str(e)[:200])}, 500)
            return
        if path == "/api/mission/dry-run":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > 2 * 1024 * 1024:
                    self._send_json({"ok": False, "error": "bad_request"}, 400)
                    return
                req = json.loads(self.rfile.read(length).decode("utf-8"))
                from legion_command import mission_dispatch, mission_planner
                plan = req.get("plan")
                if not plan and req.get("mission_id"):
                    plan = mission_planner.load_plan(str(req["mission_id"]))
                if not plan:
                    self._send_json({"ok": False, "error": "plan_required"}, 400)
                    return
                res = mission_dispatch.dry_run(plan, int(req.get("parallel") or 3))
                self._send_json(res)
            except Exception as e:  # noqa: BLE001
                self._send_json({"ok": False, "error": "mission_dry_run_failed",
                                 "detail": "%s: %s" % (type(e).__name__, str(e)[:200])}, 500)
            return
        if path == "/api/model/prefs":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > 16 * 1024:
                    self._send_json({"ok": False, "error": "bad_request"}, 400)
                    return
                req = json.loads(self.rfile.read(length).decode("utf-8"))
                from legion_command import model_prefs
                out = model_prefs.set_pref(str(req.get("agent") or ""),
                                           str(req.get("model") or ""))
                self._send_json(out)
            except ValueError as e:
                self._send_json({"ok": False, "error": str(e)[:200]}, 400)
            except Exception as e:  # noqa: BLE001
                self._send_json({"ok": False, "error": "model_pref_failed",
                                 "detail": "%s: %s" % (type(e).__name__, str(e)[:200])}, 500)
            return
        if path == "/api/keys/action":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > 64 * 1024:
                    self._send_json(
                        {
                            "ok": False,
                            "error": "bad_request",
                            "reason": "content_length_out_of_range",
                        },
                        400,
                    )
                    return
                req = json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self._send_json({"ok": False, "error": "bad_request"}, 400)
                return
            try:
                from legion_command import keypool

                res = keypool.action(req if isinstance(req, dict) else {})
            except Exception as e:  # noqa: BLE001
                res = {
                    "ok": False,
                    "error": "keypool_action_failed",
                    "detail": "%s: %s" % (type(e).__name__, str(e)[:200]),
                }
            self._send_json(res, 200 if res.get("ok") else 400)
            return
        if path == "/api/zhaoyun/approve":
            # 审批闭环：approve/reject → 执行器落可逆文件（不自动改 org/bundle/CAPABILITIES）
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > 64 * 1024:
                    self._send_json(
                        {
                            "ok": False,
                            "error": "bad_request",
                            "reason": "content_length_out_of_range",
                        },
                        400,
                    )
                    return
                req = json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self._send_json({"ok": False, "error": "bad_request"}, 400)
                return
            try:
                from legion_command import zhaoyun_cap2 as c2

                res = c2.approve(
                    _PLATFORM,
                    str(req.get("id") or ""),
                    str(req.get("decision") or ""),
                    str(req.get("signer") or ""),
                    str(req.get("note") or ""),
                )
            except Exception as e:  # noqa: BLE001
                res = {
                    "ok": False,
                    "error": "approve_failed",
                    "detail": "%s: %s" % (type(e).__name__, str(e)[:200]),
                }
            try:
                from observability import trace as tr

                tr.record(
                    tr.trace_id(),
                    "approval",
                    "approval",
                    attrs={
                        "zhaoyun.approval_id": str(req.get("id") or ""),
                        "zhaoyun.decision": str(req.get("decision") or ""),
                        "zhaoyun.result": "ok" if res.get("ok") else "error",
                        "zhaoyun.applied": bool(
                            (res.get("request") or {}).get("applied")
                        ),
                    },
                )
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/legion_command/serve.py", exc)
            self._send_json(res, 200 if res.get("ok") else 400)
            return
        if path == "/api/zhaoyun/evals/run":
            # 评估飞轮：进程内跑 smoke 子集（避免 venv/子进程 stdio 环境差异）
            try:
                sys.path.insert(0, str(_PLATFORM))
                from evals import run_evals as ev  # noqa: WPS433

                res = ev.build_report(smoke=True)
            except Exception as e:  # noqa: BLE001
                res = {
                    "ok": False,
                    "error": "evals_run_failed",
                    "detail": "%s: %s" % (type(e).__name__, str(e)[:160]),
                }
            self._send_json(res, 200 if res.get("ok") else 500)
            return
        if path == "/api/zhaoyun/mcp/probe":
            try:
                sys.path.insert(0, str(_PLATFORM))
                from legion_mcp import registry as mreg  # noqa: WPS433

                res = mreg.summary(force=True)
            except Exception as e:  # noqa: BLE001
                res = {
                    "ok": False,
                    "error": "mcp_probe_failed",
                    "detail": "%s: %s" % (type(e).__name__, str(e)[:160]),
                }
            self._send_json(res, 200 if res.get("ok") else 500)
            return
        if path == "/api/zhaoyun/mcp/call":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > 64 * 1024:
                    self._send_json({"ok": False, "error": "bad_request"}, 400)
                    return
                req = json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self._send_json({"ok": False, "error": "bad_request"}, 400)
                return
            try:
                sys.path.insert(0, str(_PLATFORM))
                from legion_mcp import registry as mreg  # noqa: WPS433

                res = mreg.call(
                    str(req.get("server") or ""),
                    str(req.get("tool") or ""),
                    req.get("args") or {},
                )
            except Exception as e:  # noqa: BLE001
                res = {
                    "ok": False,
                    "error": "mcp_call_failed",
                    "detail": "%s: %s" % (type(e).__name__, str(e)[:160]),
                }
            self._send_json(res, 200 if res.get("ok") else 400)
            return
        if path == "/api/zhaoyun/memory/search":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > 64 * 1024:
                    self._send_json({"ok": False, "error": "bad_request"}, 400)
                    return
                req = json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self._send_json({"ok": False, "error": "bad_request"}, 400)
                return
            try:
                sys.path.insert(0, str(_PLATFORM))
                from memory import store as ms  # noqa: WPS433

                res = ms.search(
                    str(req.get("q") or ""),
                    layer=str(req.get("layer") or "") or None,
                    limit=int(req.get("limit") or 5),
                )
            except Exception as e:  # noqa: BLE001
                res = {
                    "ok": False,
                    "error": "memory_search_failed",
                    "detail": "%s: %s" % (type(e).__name__, str(e)[:160]),
                }
            self._send_json(res, 200 if res.get("ok") else 400)
            return
        if path == "/api/zhaoyun/memory/ingest":
            try:
                sys.path.insert(0, str(_PLATFORM))
                from memory import store as ms  # noqa: WPS433

                res = ms.ingest_all()
                res["eval"] = ms.run_eval()
            except Exception as e:  # noqa: BLE001
                res = {
                    "ok": False,
                    "error": "memory_ingest_failed",
                    "detail": "%s: %s" % (type(e).__name__, str(e)[:160]),
                }
            self._send_json(res, 200 if res.get("ok") else 500)
            return
        if path == "/api/zhaoyun/memory/eval":
            try:
                sys.path.insert(0, str(_PLATFORM))
                from memory import store as ms  # noqa: WPS433

                res = ms.run_eval()
            except Exception as e:  # noqa: BLE001
                res = {
                    "ok": False,
                    "error": "memory_eval_failed",
                    "detail": "%s: %s" % (type(e).__name__, str(e)[:160]),
                }
            self._send_json(res, 200 if res.get("ok") else 500)
            return
        if path == "/api/zhaoyun/sandbox/verify":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > 64 * 1024:
                    self._send_json({"ok": False, "error": "bad_request"}, 400)
                    return
                req = json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self._send_json({"ok": False, "error": "bad_request"}, 400)
                return
            try:
                sys.path.insert(0, str(_PLATFORM))
                from sandbox import runner as sbr  # noqa: WPS433

                res = sbr.verify_skill(
                    str(req.get("skill") or ""), timeout=int(req.get("timeout") or 120)
                )
            except Exception as e:  # noqa: BLE001
                res = {
                    "ok": False,
                    "error": "sandbox_failed",
                    "detail": "%s: %s" % (type(e).__name__, str(e)[:160]),
                }
            self._send_json(res, 200 if res.get("ok") else 400)
            return
        if path == "/api/zhaoyun/outbox":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > 64 * 1024:
                    self._send_json({"ok": False, "error": "bad_request"}, 400)
                    return
                req = json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self._send_json({"ok": False, "error": "bad_request"}, 400)
                return
            try:
                sys.path.insert(0, str(_PLATFORM))
                from notify import outbox as ob  # noqa: WPS433

                res = ob.enqueue(
                    str(req.get("title") or "赵云通知"),
                    str(req.get("body") or ""),
                    kind=str(req.get("kind") or "notify"),
                )
            except Exception as e:  # noqa: BLE001
                res = {
                    "ok": False,
                    "error": "outbox_failed",
                    "detail": "%s: %s" % (type(e).__name__, str(e)[:160]),
                }
            self._send_json(res, 200 if res.get("ok") else 400)
            return
        if path == "/api/zhaoyun/outbox/send":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                req = (
                    json.loads(self.rfile.read(length).decode("utf-8"))
                    if length
                    else {}
                )
            except (ValueError, UnicodeDecodeError):
                self._send_json({"ok": False, "error": "bad_request"}, 400)
                return
            try:
                sys.path.insert(0, str(_PLATFORM))
                from notify import outbox as ob  # noqa: WPS433

                res = ob.send(str(req.get("id") or ""))
            except Exception as e:  # noqa: BLE001
                res = {
                    "ok": False,
                    "error": "outbox_send_failed",
                    "detail": "%s: %s" % (type(e).__name__, str(e)[:160]),
                }
            self._send_json(res, 200)
            return
        if path == "/api/zhaoyun/approval-card":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                req = (
                    json.loads(self.rfile.read(length).decode("utf-8"))
                    if length
                    else {}
                )
            except (ValueError, UnicodeDecodeError):
                self._send_json({"ok": False, "error": "bad_request"}, 400)
                return
            try:
                rid = str(req.get("request_id") or "").strip()
                p = _PLATFORM / "data" / "zhaoyun_requests" / (rid + ".json")
                request = json.loads(p.read_text(encoding="utf-8-sig"))
                sys.path.insert(0, str(_PLATFORM))
                from notify import outbox as ob  # noqa: WPS433

                res = ob.enqueue_approval_card(request)
            except (OSError, ValueError):
                res = {
                    "ok": False,
                    "error": "request_not_found",
                    "id": rid if "rid" in dir() else "",
                }
            except Exception as e:  # noqa: BLE001
                res = {
                    "ok": False,
                    "error": "approval_card_failed",
                    "detail": "%s: %s" % (type(e).__name__, str(e)[:160]),
                }
            self._send_json(res, 200 if res.get("ok") else 400)
            return
        if path == "/api/zhaoyun/research/fetch":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > 64 * 1024:
                    self._send_json({"ok": False, "error": "bad_request"}, 400)
                    return
                req = json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self._send_json({"ok": False, "error": "bad_request"}, 400)
                return
            try:
                sys.path.insert(0, str(_PLATFORM))
                from research import web as rw  # noqa: WPS433

                res = rw.fetch(str(req.get("url") or ""), force=bool(req.get("force")))
            except Exception as e:  # noqa: BLE001
                res = {
                    "ok": False,
                    "error": "research_fetch_failed",
                    "detail": "%s: %s" % (type(e).__name__, str(e)[:160]),
                }
            self._send_json(res, 200 if res.get("ok") else 400)
            return
        if path == "/api/zhaoyun/repair/triage":
            try:
                sys.path.insert(0, str(_PLATFORM))
                from repair import lane as rl  # noqa: WPS433

                res = rl.triage()
            except Exception as e:  # noqa: BLE001
                res = {
                    "ok": False,
                    "error": "repair_triage_failed",
                    "detail": "%s: %s" % (type(e).__name__, str(e)[:160]),
                }
            self._send_json(res, 200 if res.get("ok") else 500)
            return
        if path == "/api/zhaoyun/repair/run":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                req = (
                    json.loads(self.rfile.read(length).decode("utf-8"))
                    if length
                    else {}
                )
            except (ValueError, UnicodeDecodeError):
                self._send_json({"ok": False, "error": "bad_request"}, 400)
                return
            try:
                sys.path.insert(0, str(_PLATFORM))
                from repair import lane as rl  # noqa: WPS433

                res = rl.run_batch(str(req.get("batch_id") or ""))
            except Exception as e:  # noqa: BLE001
                res = {
                    "ok": False,
                    "error": "repair_run_failed",
                    "detail": "%s: %s" % (type(e).__name__, str(e)[:160]),
                }
            self._send_json(res, 200 if res.get("ok") else 400)
            return
        if path == "/api/zhaoyun/request":
            # WO-ZHAOYUN-DRILL：调整类动作只登记请求（pending_human），不自动改真源
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > 64 * 1024:
                    self._send_json(
                        {
                            "ok": False,
                            "error": "bad_request",
                            "reason": "content_length_out_of_range",
                        },
                        400,
                    )
                    return
                req = json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self._send_json({"ok": False, "error": "bad_request"}, 400)
                return
            try:
                from legion_command import zhaoyun_drill as zd

                res = zd.write_request(
                    _PLATFORM,
                    str(req.get("kind") or ""),
                    req.get("payload") or {},
                    str(req.get("reason") or ""),
                    requested_by="老田（页面提交）",
                )
            except Exception as e:  # noqa: BLE001
                res = {
                    "ok": False,
                    "error": "request_failed",
                    "detail": "%s: %s" % (type(e).__name__, str(e)[:200]),
                }
            self._send_json(res, 200 if res.get("ok") else 400)
            return
        if path == "/api/chat":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                # 2026-10-26 A2-B04：兄弟端点都有上限，本端点此前缺失。
                # 负值/超 64KB 一律 400（防 read(-1) 悬挂与无界内存/参数超长）。
                if length <= 0 or length > 64 * 1024:
                    self._send_json(
                        {
                            "ok": False,
                            "error": "bad_request",
                            "reason": "content_length_out_of_range",
                        },
                        400,
                    )
                    return
                req = json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self._send_json({"ok": False, "error": "bad_request"}, 400)
                return
            agent = str(req.get("agent", "")).strip()
            if not agent or not agent.replace("_", "").isalnum():
                self._send_json({"ok": False, "error": "bad_agent"}, 400)
                return
            purpose = str(req.get("purpose", "")).strip()
            _persona = five_role_persona(agent)
            if _persona:
                _caller_system = str(req.get("system", ""))
                req["system"] = _persona + ("\n\n" + _caller_system if _caller_system else "")
            binding_orig = resolve_binding(agent)
            meeting_override = purpose == "meeting" and agent in MEETING_BINDING_OVERRIDES
            binding = (resolve_meeting_binding(agent) if meeting_override else binding_orig)
            if binding == "none":
                # 规则执行体：编制上就没给它 LLM。不能兜底成 9b，
                # 那会让一个「判例手册驱动的巡检哨」凭空开口说话。
                self._send_json(
                    {
                        "schema_version": "1.0",
                        "ok": False,
                        "kind": "llm_chat",
                        "data": {},
                        "error": {
                            "code": "no_llm_capability",
                            "message": "%s 为规则执行体（编制绑定 none），无 LLM 发言能力"
                            % agent,
                        },
                        "degraded": False,
                        "latency_ms": 0,
                        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                        "agent": agent,
                        "binding": binding,
                    }
                )
                return
            if binding.startswith("openai/"):
                # 2026-10-26 WG-8796OC：会议厅 AI1 的「真通道」改走 OpenClaw
                # agent-config 载体（与 L5 同一载体）：GPT6 跨境不可达期间由
                # deepseek 中转，网络恢复后自动回 GPT6，无需改码。诚实标注
                # carrier/relay/mode，不再一律 channel_not_ready（那会连中转也拒掉）。
                t0 = time.time()
                if agent != "AI1":
                    self._send_json(
                        {
                            "schema_version": "1.0",
                            "ok": False,
                            "kind": "llm_chat",
                            "data": {},
                            "error": {
                                "code": "channel_not_ready",
                                "message": "%s 的 openai 绑定暂无 OpenClaw 载体映射"
                                % agent,
                            },
                            "degraded": False,
                            "latency_ms": 0,
                            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                            "agent": agent,
                            "binding": binding,
                        }
                    )
                    return
                prompt = " ".join(
                    (
                        str(req.get("system", "")) + " " + str(req.get("user", ""))
                    ).split()
                )[:16000]
                text, oc_err = oc_run("zhuge", prompt)
                carrier = agent_primary_model("zhuge") or "agent-config"
                relay = is_relay(carrier)
                if text:
                    self._send_json(
                        {
                            "schema_version": "1.0",
                            "ok": True,
                            "kind": "llm_chat",
                            "data": {
                                "binding_resolved": "openclaw/agent-config",
                                "carrier": carrier,
                                "relay": relay,
                                "mode": "openclaw_cloud" if relay else "openclaw_gpt",
                                "text": text,
                                "status": "normal",
                            },
                            "degraded": False,
                            "latency_ms": int((time.time() - t0) * 1000),
                            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                            "agent": agent,
                            "binding": "openclaw/agent-config",
                        }
                    )
                else:
                    if purpose in REPORT_PURPOSES:
                        fb_env, _fb_attempts = _seat_chat(
                            llm_gateway.chat, "glm/glm-5.3", resolve_fallbacks(agent),
                            str(req.get("system", ""))[:4000],
                            str(req.get("user", ""))[:8000],
                        )
                        if fb_env.get("ok"):
                            fb_env["degraded"] = True
                            fb_env["fallback_from"] = binding
                            fb_env["fallback_reason"] = "openclaw_unavailable:%s" % (oc_err or "unknown")
                            fb_env["agent"] = agent
                            fb_env["binding"] = fb_env.get("data", {}).get("binding_resolved", "glm/glm-5.3")
                            self._send_json(fb_env)
                            return
                    self._send_json(
                        {
                            "schema_version": "1.0",
                            "ok": False,
                            "kind": "llm_chat",
                            "data": {},
                            "error": {
                                "code": "channel_not_ready",
                                "message": "OpenClaw 载体不可用：%s"
                                % (oc_err or "unknown"),
                            },
                            "degraded": False,
                            "latency_ms": int((time.time() - t0) * 1000),
                            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                            "agent": agent,
                            "binding": binding,
                        }
                    )
                return
            # 席位级 fallback 链（WO-LEGION-DEVEXEC2-20261027）：glm 主绑失败时不再
            # 硬编码降 9b，而是按 org.json 的 llm_fallback 依次尝试（缺链→平台默认 9b）。
            # 非 glm 失败原样透传（不伪造降级）；判读席位语义仍在 engine 层不变，
            # 此处仅会议通道可用性兜底，并诚实标注 degraded/fallback_from/chain。
            env, _attempts = _seat_chat(
                llm_gateway.chat,
                binding,
                resolve_fallbacks(agent),
                str(req.get("system", ""))[:4000],
                str(req.get("user", ""))[:8000],
                allow_non_glm_fallback=(purpose in REPORT_PURPOSES),
            )
            env["agent"] = agent
            env["binding"] = env.get("data", {}).get("binding_resolved", binding)
            if isinstance(env.get("data"), dict):
                env["data"]["role_persona_applied"] = bool(_persona)
            if meeting_override:
                env["meeting_override"] = True
                env["binding_original"] = binding_orig
            self._send_json(env)
            return
        if path == "/api/hq/agenda/message":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > 64 * 1024:
                    raise ValueError("bad length")
                req = json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self._send_json({"ok": False, "error": "bad_request"}, 400)
                return
            code, env = hq_agenda.append_message(COUNCIL_DATA, req)
            self._send_json(env, code)
            return
        if path == "/api/case/autorun":
            sub, roles = _case_actor(self.headers)
            if not sub:
                self._send_json({"ok": False, "error": "unauthorized"}, 401)
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > 256 * 1024:
                    raise ValueError("bad length")
                req = json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self._send_json({"ok": False, "error": "bad_request"}, 400)
                return
            cid = str(req.get("case_id") or "").strip()
            if not cid:
                import random
                cid = "CASE-%s-%s" % (time.strftime("%Y%m%d"), "".join(random.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(4)))
            try:
                c = case_api.get_case(cid) if cid else None
                if not c:
                    c = case_api.save_case({"case_id": cid, "title": str(req.get("title") or "未命名 Case"),
                                            "materials": str(req.get("materials") or ""),
                                            "status": "DRAFT", "opinions": [], "meeting_id": "", "proposal": ""})
                if (c.get("orchestration") or {}).get("state") == "running":
                    self._send_json({"ok": False, "error": "case_already_running", "case": c}, 409)
                    return
                threading.Thread(target=case_orchestrator.autorun, args=(cid,), daemon=True).start()
            except ValueError as exc:
                self._send_json({"ok": False, "error": str(exc)[:200]}, 400)
                return
            self._send_json({"ok": True, "case": c, "status": "running"}, 202)
            return
        if path == "/api/case/save":
            _sub, _roles = _case_actor(self.headers)
            if not _sub:
                self._send_json({"ok": False, "error": "unauthorized"}, 401)
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > 1024 * 1024:
                    raise ValueError("bad length")
                req = json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self._send_json({"ok": False, "error": "bad_request"}, 400)
                return
            try:
                c = case_api.save_case(req.get("case") or {})
            except ValueError as exc:
                self._send_json({"ok": False, "error": str(exc)[:200]}, 400)
                return
            self._send_json({"ok": True, "case": c})
            return
        if path == "/api/case/approve":
            sub, roles = _case_actor(self.headers)
            if not sub:
                self._send_json({"ok": False, "error": "unauthorized"}, 401)
                return
            if not (roles & {"APPROVER", "ADMIN", "OWNER"}):
                self._send_json({"ok": False, "error": "approval_forbidden"}, 403)
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > 64 * 1024:
                    raise ValueError("bad length")
                req = json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self._send_json({"ok": False, "error": "bad_request"}, 400)
                return
            try:
                c = case_api.approve_case(str(req.get("case_id") or ""), str(req.get("approver") or ""),
                                          str(req.get("note") or ""), sub)
            except ValueError as exc:
                self._send_json({"ok": False, "error": str(exc)[:200]}, 400)
                return
            self._send_json({"ok": True, "case": c})
            return
        if path == "/api/case/dispatch":
            sub, roles = _case_actor(self.headers)
            if not sub:
                self._send_json({"ok": False, "error": "unauthorized"}, 401)
                return
            if not (roles & {"APPROVER", "ADMIN", "OWNER"}):
                self._send_json({"ok": False, "error": "approval_forbidden"}, 403)
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > 64 * 1024:
                    raise ValueError("bad length")
                req = json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self._send_json({"ok": False, "error": "bad_request"}, 400)
                return
            c = case_api.get_case(str(req.get("case_id") or ""))
            if not c:
                self._send_json({"ok": False, "error": "case_not_found"}, 404)
                return
            if not ((c.get("approval") or {}).get("approved")):
                self._send_json({"ok": False, "error": "human_approval_required"}, 409)
                return
            mid = c.get("meeting_id")
            if not mid:
                self._send_json({"ok": False, "error": "meeting_required"}, 409)
                return
            owner = str(req.get("owner") or "A0")[:40]
            code, env = hq_meeting.dispatch_meeting(
                HQ_MEETINGS_DIR,
                {"meeting_id": mid, "title": ("CASE " + c["case_id"] + "：" + c.get("title", ""))[:120],
                 "order_type": "work", "owner": owner},
                inbox_gateway.submit)
            if code != 200 or not env.get("ok"):
                self._send_json(env, code)
                return
            c2 = case_api.mark_dispatched(c["case_id"], owner, env.get("dispatch") or env)
            self._send_json({"ok": True, "dispatch": env.get("dispatch"), "case": c2})
            return
        if path == "/api/hq/meeting/save":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > 512 * 1024:
                    raise ValueError("bad length")
                req = json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self._send_json({"ok": False, "error": "bad_request"}, 400)
                return
            code, env = hq_meeting.save_meeting(HQ_MEETINGS_DIR, req)
            self._send_json(env, code)
            return
        if path == "/api/hq/meeting/dispatch":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > 64 * 1024:
                    raise ValueError("bad length")
                req = json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self._send_json({"ok": False, "error": "bad_request"}, 400)
                return
            code, env = hq_meeting.dispatch_meeting(HQ_MEETINGS_DIR, req, inbox_gateway.submit)
            self._send_json(env, code)
            return
        if path == "/api/meeting/minutes":
            # 会终纪要落盘：来源是客户端声明，不能据此认定为真模型回执。
            # 只接受对话内容与参会名单，文件名一律服务端生成（不收用户路径）。
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > 512 * 1024:
                    raise ValueError("bad length")
                req = json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self._send_json({"ok": False, "error": "bad_request"}, 400)
                return
            raw = req.get("minutes")
            if not isinstance(raw, list) or not raw:
                self._send_json({"ok": False, "error": "empty_minutes"}, 400)
                return
            rows = []
            for m in raw[:200]:
                if not isinstance(m, dict):
                    continue
                rows.append(
                    {
                        "by": str(m.get("id", ""))[:24],
                        "src": str(m.get("src", ""))[:12],
                        "source_verification": "unverified",
                        "text": str(m.get("text", ""))[:2000],
                    }
                )
            stamp = time.strftime("%Y%m%d-%H%M%S")
            out_dir = MEETINGS_DIR
            doc = {
                "schema_version": "1.0",
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "participants": [str(p)[:24] for p in (req.get("participants") or [])][
                    :30
                ],
                "orders": req.get("orders")
                if isinstance(req.get("orders"), list)
                else [],
                "model_stats": req.get("model_stats")
                if isinstance(req.get("model_stats"), dict)
                else {},
                "minutes": rows,
                "source_verification": "unverified",
                "note": "指挥台作战会议室会终纪要；src=live/degraded/script 仅为客户端来源声明；须有服务端真实模型回执才能验证，当前均未验证",
            }
            target = out_dir / ("warroom-%s.json" % stamp)
            tmp = target.with_suffix(".json.tmp")
            try:
                out_dir.mkdir(parents=True, exist_ok=True)
                tmp.write_text(
                    json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8"
                )
                tmp.replace(target)
            except OSError:
                self._send_json({"ok": False, "error": "minutes_save_failed"}, 500)
                return
            try:
                rel = str(target.relative_to(_PLATFORM))
            except ValueError:
                rel = str(target)
            self._send_json({"ok": True, "saved": rel, "count": len(rows)})
            return
        if path == "/api/org/preview-enroll":
            # 编制预览（只读）：调 engine.org_enroll.build_plan 校验并返回计划，
            # 一个字节都不写。真写入只能由人在命令行带 --apply + 签字执行——
            # 组织真源变更不经 HTTP 落地，这是红线。
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > 64 * 1024:
                    raise ValueError("bad length")
                req = json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self._send_json({"ok": False, "error": "bad_request"}, 400)
                return
            try:
                from engine.org_enroll import build_plan

                plan = build_plan(
                    _PLATFORM,
                    dept=str(req.get("dept", "")),
                    agent_id=str(req.get("agent_id", "")),
                    cn_name=str(req.get("cn_name", "")),
                    role_profile=str(req.get("role_profile", "")),
                    duty=str(req.get("duty", "")),
                    binding=str(req.get("binding", "")),
                    grade=str(req.get("grade", "E2")),
                    ticket=str(req.get("ticket", "")),
                    signed_by=str(req.get("signed_by", "")),
                )
            except Exception as e:  # noqa: BLE001
                code = getattr(e, "__class__", type(e)).__name__
                self._send_json(
                    {
                        "ok": False,
                        "error": "enroll_invalid",
                        "detail": str(e)[:400],
                        "kind": code,
                    },
                    400,
                )
                return
            self._send_json(
                {
                    "ok": True,
                    "plan": plan,
                    "note": "仅预览：未写入任何字节。真入编须命令行 "
                    "--apply 并由老田签字（组织真源不经 HTTP 落地）",
                }
            )
            return
        if path != "/api/dispatch":
            self._send_json({"ok": False, "error": "not_found"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            req = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._send_json({"ok": False, "error": "bad_request"}, 400)
            return
        otype = req.get("type", "work")
        kind = _KIND_MAP.get(otype)
        if kind is None:
            self._send_json({"ok": False, "error": "bad_type: %s" % otype}, 400)
            return
        payload = {
            "command": "legion_meeting_dispatch",
            "order_type": otype,
            "title": str(req.get("title", ""))[:120],
            "attendees": req.get("attendees", []),
            "owner": str(req.get("owner") or "")[:40],
            "host": "laotian",
            "note": str(req.get("note", ""))[:400],
        }
        env = inbox_gateway.submit(kind, payload, source="warroom")
        self._send_json(env)


def build_server(host="127.0.0.1", port=8796):
    return ThreadingHTTPServer((host, port), Handler)


def main(argv=None):
    ap = argparse.ArgumentParser(description="legion command console")
    ap.add_argument("--port", type=int, default=8796)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args(argv)
    # 端口占用自愈：上一次实例退出后 TIME_WAIT 未释放时 bind 会失败，
    # 重试 3 次（每次 2s），仍失败则明确报错退出（不静默死掉）
    srv = None
    for attempt in range(1, 4):
        try:
            srv = build_server(args.host, args.port)
            break
        except OSError as exc:
            print(
                "[legion-command] bind %s:%d failed (attempt %d/3): %s"
                % (args.host, args.port, attempt, exc),
                file=sys.stderr,
            )
            time.sleep(2)
    if srv is None:
        print(
            "[legion-command] giving up: port %d unavailable" % args.port,
            file=sys.stderr,
        )
        return 3
    print("legion-command serving on %s:%d" % (args.host, args.port), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/legion_command/serve.py", exc)
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
