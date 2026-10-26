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

import argparse
import json
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

_PLATFORM = Path(__file__).resolve().parents[1]
if str(_PLATFORM) not in sys.path:
    sys.path.insert(0, str(_PLATFORM))

from gateway import inbox_gateway, llm_gateway  # noqa: E402  S21 门面复用
from engine.org_profiles import profile_index, stamp  # noqa: E402  S41 统一取档口径
from engine.openclaw_cli import agent_primary_model, is_relay, run as oc_run  # noqa: E402  OpenClaw 载体桥
from legion_command.snapshot import build_snapshot  # noqa: E402

_STATIC = Path(__file__).resolve().parent / "static"
# 作战会议室纪要落盘目录（模块级，测试可 monkeypatch 到 tmp）
MEETINGS_DIR = _PLATFORM / "data" / "meetings" / "warroom"
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
        sig = verdict.get("signature") if isinstance(verdict.get("signature"), dict) else {}
        cards.append({
            "card_id": d.get("card_id"),
            "hypothesis": str(d.get("hypothesis", ""))[:60],
            "status": d.get("status"),
            "arms": list((d.get("arms") or {}).keys()),
            "created_at": d.get("created_at") or created_at,
            "kind": d.get("kind") or "experiment",
            "decision_required": bool(d.get("decision_required", False)),
            "window_summary": d.get("window_summary") or {},
            "signature_verified": sig.get("verified"),
        })
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
_BINDING_CACHE = {"mtime": None, "map": {}}


def resolve_binding(agent: str, platform_dir: Path | None = None) -> str:
    """席位 → 模型绑定。**org.json 是真源，优先于本文件的硬编码表**。

    为什么必须查真源：S37 给 BEACON1 填了 `llm_binding: none`（规则巡检哨，无 LLM 依赖），
    而会议层若只查硬编码表就会给它默认 9b —— 于是「档案写明无 LLM、会议却能发言」自相矛盾。
    编制造出来的事实，展示层必须认。

    返回：
      - 具体绑定串（如 ollama/qwen3.5:9b）
      - "none" 表示该席位为规则执行体，无 LLM 发言能力
    """
    base = platform_dir or _PLATFORM
    cache = _BINDING_CACHE
    # 缓存指纹覆盖 org.json + 全部 team_*.json：A0A 的档案只在 team_chief.json 里，
    # 只认 org.json 的 mtime 会让它的改档永远读不到（S41 实测）。
    key = stamp(base)
    if cache["mtime"] != key:
        cache["map"] = profile_index(base)
        cache["mtime"] = key
    hit = cache["map"].get(agent) or {}
    if hit.get("llm_binding"):
        return hit["llm_binding"]
    return AGENT_BINDING.get(agent, _DEFAULT_BINDING)


class Handler(BaseHTTPRequestHandler):
    server_version = "legion-command/1.0"

    def _send_json(self, obj, status=200):
        import json
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
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

    def do_GET(self):
        import re
        path = urlparse(self.path).path
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
        if path in ("/keys", "/keys.html"):
            # 三席多 Key 池页面
            self._send_file(_STATIC / "keypool.html", "text/html; charset=utf-8")
            return
        m = re.match(r"^/static/([a-z_]+\.html)$", path)
        if m and re.fullmatch(r"[a-z_]+", m.group(1).rsplit(".", 1)[0]):
            self._send_file(_STATIC / m.group(1), "text/html; charset=utf-8")
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
            except Exception as e:                       # noqa: BLE001
                self._send_json({"ok": False, "error": "intent_failed",
                                 "detail": "%s: %s" % (type(e).__name__, str(e)[:200])}, 500)
            return
        if path == "/api/evolution":
            self._send_json(self._evolution())
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
                self._send_json(res, 400)      # 非法 id / 不可读一律 4xx，不拿 200 装成功
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
                self._send_json({"ok": False, "error": "org_missing", "detail": str(e)[:200]}, 500)
            except Exception as e:                       # noqa: BLE001
                self._send_json({"ok": False, "error": "audit_failed",
                                 "detail": "%s: %s" % (type(e).__name__, str(e)[:200])}, 500)
            return
        if path == "/api/zhaoyun":
            # 赵云 · 治理席只读视图（身份/能力卡/判例库/AI建议/时钟/保活/签字链）
            try:
                from legion_command.zhaoyun_view import build_view
                self._send_json(build_view(_PLATFORM))
            except Exception as e:                       # noqa: BLE001
                self._send_json({"ok": False, "error": "zhaoyun_view_failed",
                                 "detail": "%s: %s" % (type(e).__name__, str(e)[:200])}, 500)
            return
        if path == "/api/keys/state":
            try:
                from legion_command import keypool
                self._send_json(keypool.state())
            except Exception as e:                       # noqa: BLE001
                self._send_json({"ok": False, "error": "keypool_state_failed",
                                 "detail": "%s: %s" % (type(e).__name__, str(e)[:200])}, 500)
            return
        if path.startswith("/api/zhaoyun/"):
            # WO-ZHAOYUN-DRILL：治理席"下钻"数据（能力详证/任务偏差/记忆/模型/请求）
            try:
                from legion_command import zhaoyun_drill as zd
                sub = path[len("/api/zhaoyun/"):]
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
                elif re.fullmatch(r"cap/[A-Za-z0-9_-]{1,64}", sub):
                    cap = sub.split("/", 1)[1]
                    rep = zd.capabilities_detail(_PLATFORM)
                    row = next((x for x in (rep.get("items") or [])
                                if x.get("name") == cap), None)
                    if row is None:
                        self._send_json({"ok": False, "error": "cap_not_found",
                                         "cap": cap}, 404)
                    else:
                        self._send_json({"ok": True, "capability": row})
                else:
                    self._send_json({"ok": False, "error": "not_found: %s" % path}, 404)
            except Exception as e:                       # noqa: BLE001
                self._send_json({"ok": False, "error": "zhaoyun_drill_failed",
                                 "detail": "%s: %s" % (type(e).__name__, str(e)[:200])}, 500)
            return
        if path == "/api/tianfeng":
            # 田丰 · 产品/研发席只读视图（角色/授权/能力/运行态/链接）
            try:
                from legion_command.tianfeng_view import build_view
                self._send_json(build_view(_PLATFORM))
            except Exception as e:                       # noqa: BLE001
                self._send_json({"ok": False, "error": "tianfeng_view_failed",
                                 "detail": "%s: %s" % (type(e).__name__, str(e)[:200])}, 500)
            return
        if path == "/api/health":
            self._send_json({"ok": True, "service": "legion-command", "version": "1.0"})
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
                    rows.append({"id": p.stem, "ts": "", "broken": True,
                                 "count": 0, "participants": [], "src_stats": {},
                                 "source_verification": "unverified"})
                    continue
                ms = d.get("minutes") or []
                stats = {"live": 0, "degraded": 0, "script": 0, "host": 0}
                for m in ms:
                    k = str(m.get("src", ""))
                    if k in stats:
                        stats[k] += 1
                rows.append({"id": p.stem, "ts": d.get("ts", ""),
                             "count": len(ms),
                             "participants": d.get("participants") or [],
                             "src_stats": stats,
                             "source_verification": "unverified",
                             "broken": False})
        return {"ok": True, "schema_version": "1.0", "meetings": rows,
                "total": len(rows),
                "note": "只读清单；src_stats 统计客户端来源声明（含旧纪要），不是经服务端验证的真模型次数"}

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
            return {"ok": False, "error": "meeting_unreadable: %s" % type(e).__name__,
                    "id": mid}
        doc["source_verification"] = "unverified"
        return {"ok": True, "schema_version": "1.0", "id": mid, "meeting": doc,
                "note": "只读回看；src 为客户端声明，未凭服务端真实模型回执验证（含历史纪要）"}

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
        recent = sorted(index_rows, key=lambda r: str(r.get("written_at") or ""),
                        reverse=True)[:5]
        return {"ok": True, "experiments": cards, "experience_domains": domains,
                "entries_total": len(index_rows),
                "experience_verified_total": sum(1 for r in index_rows if r.get("verified")),
                "experience_recent": [{
                    k: r.get(k) for k in ("entry_id", "domain", "cue", "confidence",
                                          "signed_by", "written_at", "verified")}
                    for r in recent],
                "note": "只读视图；经验入库须签字（Z4）；风控域只读"}

    def do_POST(self):
        import json
        path = urlparse(self.path).path
        if path == "/api/keys/action":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > 64 * 1024:
                    self._send_json({"ok": False, "error": "bad_request",
                                     "reason": "content_length_out_of_range"}, 400)
                    return
                req = json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self._send_json({"ok": False, "error": "bad_request"}, 400)
                return
            try:
                from legion_command import keypool
                res = keypool.action(req if isinstance(req, dict) else {})
            except Exception as e:                       # noqa: BLE001
                res = {"ok": False, "error": "keypool_action_failed",
                       "detail": "%s: %s" % (type(e).__name__, str(e)[:200])}
            self._send_json(res, 200 if res.get("ok") else 400)
            return
        if path == "/api/zhaoyun/request":
            # WO-ZHAOYUN-DRILL：调整类动作只登记请求（pending_human），不自动改真源
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > 64 * 1024:
                    self._send_json({"ok": False, "error": "bad_request",
                                     "reason": "content_length_out_of_range"}, 400)
                    return
                req = json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self._send_json({"ok": False, "error": "bad_request"}, 400)
                return
            try:
                from legion_command import zhaoyun_drill as zd
                res = zd.write_request(_PLATFORM, str(req.get("kind") or ""),
                                       req.get("payload") or {},
                                       str(req.get("reason") or ""),
                                       requested_by="老田（页面提交）")
            except Exception as e:                       # noqa: BLE001
                res = {"ok": False, "error": "request_failed",
                       "detail": "%s: %s" % (type(e).__name__, str(e)[:200])}
            self._send_json(res, 200 if res.get("ok") else 400)
            return
        if path == "/api/chat":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                # 2026-10-26 A2-B04：兄弟端点都有上限，本端点此前缺失。
                # 负值/超 64KB 一律 400（防 read(-1) 悬挂与无界内存/参数超长）。
                if length <= 0 or length > 64 * 1024:
                    self._send_json({"ok": False, "error": "bad_request",
                                     "reason": "content_length_out_of_range"}, 400)
                    return
                req = json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self._send_json({"ok": False, "error": "bad_request"}, 400)
                return
            agent = str(req.get("agent", "")).strip()
            if not agent or not agent.replace("_", "").isalnum():
                self._send_json({"ok": False, "error": "bad_agent"}, 400)
                return
            binding = resolve_binding(agent)
            if binding == "none":
                # 规则执行体：编制上就没给它 LLM。不能兜底成 9b，
                # 那会让一个「判例手册驱动的巡检哨」凭空开口说话。
                self._send_json({"schema_version": "1.0", "ok": False,
                                 "kind": "llm_chat", "data": {},
                                 "error": {"code": "no_llm_capability",
                                           "message": "%s 为规则执行体（编制绑定 none），无 LLM 发言能力" % agent},
                                 "degraded": False, "latency_ms": 0,
                                 "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                                 "agent": agent, "binding": binding})
                return
            if binding.startswith("openai/"):
                # 2026-10-26 WG-8796OC：会议厅 AI1 的「真通道」改走 OpenClaw
                # agent-config 载体（与 L5 同一载体）：GPT6 跨境不可达期间由
                # deepseek 中转，网络恢复后自动回 GPT6，无需改码。诚实标注
                # carrier/relay/mode，不再一律 channel_not_ready（那会连中转也拒掉）。
                t0 = time.time()
                if agent != "AI1":
                    self._send_json({"schema_version": "1.0", "ok": False,
                                     "kind": "llm_chat", "data": {},
                                     "error": {"code": "channel_not_ready",
                                               "message": "%s 的 openai 绑定暂无 OpenClaw 载体映射" % agent},
                                     "degraded": False, "latency_ms": 0,
                                     "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                                     "agent": agent, "binding": binding})
                    return
                prompt = " ".join((str(req.get("system", "")) + " "
                                   + str(req.get("user", ""))).split())[:16000]
                text, oc_err = oc_run("zhuge", prompt)
                carrier = agent_primary_model("zhuge") or "agent-config"
                relay = is_relay(carrier)
                if text:
                    self._send_json({"schema_version": "1.0", "ok": True,
                                     "kind": "llm_chat",
                                     "data": {"binding_resolved": "openclaw/agent-config",
                                              "carrier": carrier,
                                              "relay": relay,
                                              "mode": "openclaw_cloud" if relay else "openclaw_gpt",
                                              "text": text, "status": "normal"},
                                     "degraded": False,
                                     "latency_ms": int((time.time() - t0) * 1000),
                                     "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                                     "agent": agent, "binding": "openclaw/agent-config"})
                else:
                    self._send_json({"schema_version": "1.0", "ok": False,
                                     "kind": "llm_chat", "data": {},
                                     "error": {"code": "channel_not_ready",
                                               "message": "OpenClaw 载体不可用：%s" % (oc_err or "unknown")},
                                     "degraded": False,
                                     "latency_ms": int((time.time() - t0) * 1000),
                                     "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                                     "agent": agent, "binding": binding})
                return
            env = llm_gateway.chat(
                binding=binding,
                system=str(req.get("system", ""))[:4000],
                user=str(req.get("user", ""))[:8000])
            # 云端限额/故障 → 会议展示层显式降级本地 9b（标 degraded + 原因，
            # 不静默；判读席位语义在 engine 层不变，此处仅会议通道可用性兜底）
            if not env.get("ok") and binding.startswith("glm/"):
                fallback = llm_gateway.chat(
                    binding="ollama/qwen3.5:9b",
                    system=str(req.get("system", ""))[:4000],
                    user=str(req.get("user", ""))[:8000])
                if fallback.get("ok"):
                    fallback["degraded"] = True
                    fallback["fallback_from"] = binding
                    fallback["fallback_reason"] = (env.get("error") or {}).get("message", "cloud unavailable")
                    env = fallback
            env["agent"] = agent
            env["binding"] = env.get("data", {}).get("binding_resolved", binding)
            self._send_json(env)
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
                rows.append({"by": str(m.get("id", ""))[:24],
                             "src": str(m.get("src", ""))[:12],
                             "source_verification": "unverified",
                             "text": str(m.get("text", ""))[:2000]})
            stamp = time.strftime("%Y%m%d-%H%M%S")
            out_dir = MEETINGS_DIR
            doc = {"schema_version": "1.0", "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                   "participants": [str(p)[:24] for p in (req.get("participants") or [])][:30],
                   "orders": req.get("orders") if isinstance(req.get("orders"), list) else [],
                   "model_stats": req.get("model_stats") if isinstance(req.get("model_stats"), dict) else {},
                   "minutes": rows,
                   "source_verification": "unverified",
                   "note": "指挥台作战会议室会终纪要；src=live/degraded/script 仅为客户端来源声明；须有服务端真实模型回执才能验证，当前均未验证"}
            target = out_dir / ("warroom-%s.json" % stamp)
            tmp = target.with_suffix(".json.tmp")
            try:
                out_dir.mkdir(parents=True, exist_ok=True)
                tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
                tmp.replace(target)
            except OSError:
                self._send_json({"ok": False, "error": "minutes_save_failed"}, 500)
                return
            try:
                rel = str(target.relative_to(_PLATFORM))
            except ValueError:
                rel = str(target)
            self._send_json({"ok": True, "saved": rel,
                             "count": len(rows)})
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
                    signed_by=str(req.get("signed_by", "")))
            except Exception as e:                       # noqa: BLE001
                code = getattr(e, "__class__", type(e)).__name__
                self._send_json({"ok": False, "error": "enroll_invalid",
                                 "detail": str(e)[:400], "kind": code}, 400)
                return
            self._send_json({"ok": True, "plan": plan,
                             "note": "仅预览：未写入任何字节。真入编须命令行 "
                                     "--apply 并由老田签字（组织真源不经 HTTP 落地）"})
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
            print("[legion-command] bind %s:%d failed (attempt %d/3): %s"
                  % (args.host, args.port, attempt, exc), file=sys.stderr)
            time.sleep(2)
    if srv is None:
        print("[legion-command] giving up: port %d unavailable" % args.port,
              file=sys.stderr)
        return 3
    print("legion-command serving on %s:%d" % (args.host, args.port), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
