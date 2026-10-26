# -*- coding: utf-8 -*-
"""田丰（A0）· 产品/研发席前端视图聚合（只读，供 8796 `/api/tianfeng`）。

对齐 zhaoyun_view.py：一次算好，前端只渲染；不写任何真源。
数据源：SOUL/SEAT_CARD/CAPABILITIES/config、org.json A0、seat_system_audit、
任务板、证据包、GLM 探测日志、8787 健康。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HOME = Path.home() / ".hermes" / "profiles" / "tianfeng"


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _health(url: str, timeout=3):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status
    except Exception:
        return None


def _pid(port: int) -> str:
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=10).stdout
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[1].endswith(":%d" % port) and parts[3] == "LISTENING":
                return parts[4]
    except Exception:
        pass
    return "—"


def _audit(repo: Path) -> dict:
    try:
        cp = subprocess.run([sys.executable, "-X", "utf8", "tools/seat_system_audit.py",
                             "--seat", "tianfeng", "--json"],
                            cwd=str(repo), capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=45)
        text = cp.stdout
        start = text.find("{")
        data = json.loads(text[start:]) if start >= 0 else {}
        s = data.get("summary") or {}
        return {"pass": int(s.get("pass", 0)), "warn": int(s.get("warn", 0)),
                "fail": int(s.get("fail", 0)), "verdict": data.get("verdict", "")}
    except Exception as e:  # noqa: BLE001
        return {"pass": 0, "warn": 0, "fail": -1,
                "verdict": "audit_error:%s" % type(e).__name__}


def _toolsets() -> list:
    try:
        text = (HOME / "config.yaml").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    try:
        import yaml  # PyYAML 在 py312 可用；失败回退文本解析
        cfg = yaml.safe_load(text) or {}
        tools = ((cfg.get("platform_toolsets") or {}).get("cli") or [])
        return [str(x) for x in tools if str(x).strip()]
    except Exception:
        pass
    out, in_ts = [], False
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith("platform_toolsets:"):
            in_ts = True
            continue
        if in_ts:
            if s.startswith("- "):
                out.append(s[2:].strip())
            elif s.endswith(":"):
                continue
            elif s and not s.startswith("#"):
                break
    return out


def build_view(platform: Path) -> dict:
    repo = Path(platform).parent
    caps_raw = _load(HOME / "skills" / "CAPABILITIES.json", {})
    caps = caps_raw.get("capabilities", [])
    card = _load(HOME / "SEAT_CARD.json", {})
    org = _load(repo / "agent_platform" / "registry" / "org.json", {})
    a0 = {}
    for dept in org.get("departments", []):
        if dept.get("dept") == "chief_line":
            for p in dept.get("agent_profiles", []):
                if p.get("agent_id") == "A0":
                    a0 = p
    role_model = a0.get("role_model") or {}
    auth = role_model.get("decision_authority") or {}
    attrs = a0.get("attributes") or {}
    audit = _audit(repo)
    health = _health("http://127.0.0.1:8787/health")
    pid = _pid(8787)
    tasks = _load(repo / ".worktrees" / "tasks.json", {})
    doing = sum(1 for v in tasks.values() if isinstance(v, dict) and v.get("status") == "doing")
    briefs = sorted((repo / "work" / "seat_brief").glob("evidence_packet_*.json"))
    glm = {}
    glm_log = repo / "work" / "glm_probe" / "retry_log.jsonl"
    if glm_log.exists():
        lines = glm_log.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        if lines:
            try:
                glm = json.loads(lines[-1])
            except ValueError:
                glm = {}
    glm_ready = (repo / "work" / "glm_probe" / "JUDGMENT_READY.txt").exists()
    glm_state = "可用" if glm_ready else ("429 降级" if "429" in str(glm.get("error")) else "未知")
    ready = sum(1 for c in caps if c.get("status") == "ready")
    soul_title = "田丰 · 产品经理+研发（全在性）"
    try:
        for ln in (HOME / "SOUL.md").read_text(encoding="utf-8", errors="replace").splitlines():
            if ln.startswith("# "):
                soul_title = ln[2:].strip()
                break
    except OSError:
        pass
    return {
        "ok": True,
        "schema": "tianfeng_view/v1",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "identity": {
            "role": "产品经理 + 研发（全在性人才）：取数→方案/架构/代码参考→拆单派发→AI 自动执行→验收；无人能定案时做最优决策并指挥 AI 执行",
            "compilation": "org.json A0（参谋长）+ Hermes profile tianfeng；不占部门编制，全组织向其汇报",
            "boundary": "L0/L1 自主决策执行；仅 L2 报老田；代码是思路载体，不亲自交付生产代码",
            "distinction": "A0=田丰本席；A0A=参谋长助理（只调度不决策）；ZHAOYUN=治理席（不占编）",
            "soul_title": soul_title,
            "role_model": role_model,
            "attributes": {"experience": attrs.get("experience", ""),
                           "specialties": attrs.get("specialties", [])},
        },
        "chips": [
            {"label": "SOUL", "value": "v3 · 产品/研发", "state": "good"},
            {"label": "能力", "value": "%d/%d" % (len(caps), len(caps)), "state": "good"},
            {"label": "审计", "value": "%d 通过 / %d 失败" % (audit["pass"], max(audit["fail"], 0)),
             "state": "good" if audit["fail"] == 0 else "bad"},
            {"label": "决策", "value": "L0/L1 自主 · L2 报批", "state": ""},
            {"label": "判读", "value": glm_state, "state": "warn" if "429" in glm_state else "good"},
            {"label": "Hermes", "value": "8787 PID %s" % pid, "state": "good" if health == 200 else "bad"},
        ],
        "status": [
            {"k": "Hermes profile", "v": "tianfeng（127.0.0.1:8787，PID %s，health %s）" % (pid, health)},
            {"k": "SOUL", "v": "v3 · 产品经理+研发（全在性）"},
            {"k": "能力清单", "v": "%d 项 / %d ready" % (len(caps), ready)},
            {"k": "工具集", "v": ", ".join(_toolsets()) or "—"},
            {"k": "席位卡", "v": "base=%s" % card.get("base")},
            {"k": "审计", "v": "pass %d / warn %d / fail %d（%s）"
             % (audit["pass"], audit["warn"], max(audit["fail"], 0), audit["verdict"])},
            {"k": "任务板", "v": "doing %d 单（全平台账本）" % doing},
            {"k": "最新证据包", "v": briefs[-1].name if briefs else "—"},
            {"k": "GLM 判读", "v": "%s（最近 %s）" % (glm_state, glm.get("at", "—"))},
            {"k": "双锚", "v": "田丰 08:45 / 17:45（S4U，battery-off）"},
        ],
        "capabilities": [
            {"name": c.get("name"), "what": c.get("what"), "boundary": c.get("boundary"),
             "status": c.get("status")}
            for c in caps
        ],
        "decision_authority": [
            {"level": "L0 自主", "scope": auth.get("L0_autonomous", "常规研发/文档/取数/测试/方案/代码参考/任务拆解与派发"),
             "action": "直接执行 · 事件 + chief/decision 留痕", "human": "否"},
            {"level": "L1 执行后通报", "scope": auth.get("L1_notify_after", "跨部门派单/非生产配置/开发环境计划任务"),
             "action": "可回滚即执行 · 执行后即时报", "human": "否"},
            {"level": "L2 必须先批", "scope": auth.get("L2_requires_human", "生产/客户消息、资金/交易/审批、宿主机危险操作、上线/真实数据删除/密钥"),
             "action": "五要素 + 老田明确批准；沉默≠同意", "human": "是"},
        ],
        "links": {
            "hermes": "http://127.0.0.1:8787/?profile=tianfeng",
            "ide": "http://127.0.0.1:8734/ide",
            "legion": "http://127.0.0.1:8734/legion",
        },
    }
