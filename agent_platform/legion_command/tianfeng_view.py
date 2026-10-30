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
from datetime import datetime
from pathlib import Path
import logging

try:
    from safe_http import safe_urlopen
except ImportError:  # 作为包导入时
    from legion_command.safe_http import safe_urlopen


HOME = Path.home() / ".hermes" / "profiles" / "tianfeng"


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _health(url: str, timeout=3):
    try:
        with safe_urlopen(url, timeout=timeout) as r:
            return r.status
    except Exception:
        return None


def _pid(port: int) -> str:
    try:
        out = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        ).stdout
        for line in out.splitlines():
            parts = line.split()
            if (
                len(parts) >= 5
                and parts[1].endswith(":%d" % port)
                and parts[3] == "LISTENING"
            ):
                return parts[4]
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/legion_command/tianfeng_view.py", exc)
    return "—"


def _audit(repo: Path) -> dict:
    try:
        cp = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                "tools/seat_system_audit.py",
                "--seat",
                "tianfeng",
                "--json",
            ],
            cwd=str(repo),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=45,
        )
        text = cp.stdout
        start = text.find("{")
        data = json.loads(text[start:]) if start >= 0 else {}
        s = data.get("summary") or {}
        return {
            "pass": int(s.get("pass", 0)),
            "warn": int(s.get("warn", 0)),
            "fail": int(s.get("fail", 0)),
            "verdict": data.get("verdict", ""),
        }
    except Exception as e:  # noqa: BLE001
        return {
            "pass": 0,
            "warn": 0,
            "fail": -1,
            "verdict": "audit_error:%s" % type(e).__name__,
        }


def _toolsets() -> list:
    try:
        text = (HOME / "config.yaml").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    try:
        import yaml  # PyYAML 在 py312 可用；失败回退文本解析

        cfg = yaml.safe_load(text) or {}
        tools = (cfg.get("platform_toolsets") or {}).get("cli") or []
        return [str(x) for x in tools if str(x).strip()]
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/legion_command/tianfeng_view.py", exc)
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
    doing = sum(
        1 for v in tasks.values() if isinstance(v, dict) and v.get("status") == "doing"
    )
    briefs = sorted((repo / "work" / "seat_brief").glob("evidence_packet_*.json"))
    glm = {}
    glm_log = repo / "work" / "glm_probe" / "retry_log.jsonl"
    if glm_log.exists():
        lines = (
            glm_log.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        )
        if lines:
            try:
                glm = json.loads(lines[-1])
            except ValueError:
                glm = {}
    glm_ready = (repo / "work" / "glm_probe" / "JUDGMENT_READY.txt").exists()
    glm_state = (
        "可用"
        if glm_ready
        else ("429 降级" if "429" in str(glm.get("error")) else "未知")
    )

    # ── 动态工作流 / 卡点 ──────────────────────────────────────────────
    now = time.time()
    wf_rows = []
    for tid, m in tasks.items() if isinstance(tasks, dict) else []:
        if not isinstance(m, dict) or m.get("status") not in ("doing", "review"):
            continue
        created = str(m.get("created") or "")
        try:
            age_h = round(
                (now - datetime.strptime(created, "%Y-%m-%d %H:%M:%S").timestamp())
                / 3600,
                1,
            )
        except Exception:
            age_h = -1
        wf_rows.append(
            {
                "tid": tid,
                "status": m.get("status"),
                "owner": m.get("owner") or "?",
                "age_h": age_h,
                "desc": str(m.get("desc") or "")[:80],
            }
        )
    doing_rows = [r for r in wf_rows if r["status"] == "doing"]
    review_rows = [r for r in wf_rows if r["status"] == "review"]
    stale = sorted([r for r in wf_rows if r["age_h"] > 24], key=lambda r: -r["age_h"])
    owners = {}
    for r in wf_rows:
        owners[r["owner"]] = owners.get(r["owner"], 0) + 1
    blockers = []
    if "429" in glm_state:
        blockers.append(
            {
                "type": "外部依赖",
                "name": "GLM-5.3 判读不可用",
                "severity": "high",
                "detail": "429 持续；决策/判读降级为只出选项与风险",
                "next": "等额度恢复/换 key/备用模型（老田拍板）",
            }
        )
    if not glm_ready:
        blockers.append(
            {
                "type": "判读链",
                "name": "A0 真样例未出",
                "severity": "medium",
                "detail": "JUDGMENT_READY 不存在，A0 18:10 常驻未挂",
                "next": "GLM 恢复后生成样例，老田确认质量后再挂",
            }
        )
    if stale:
        blockers.append(
            {
                "type": "任务板",
                "name": "%d 单超 24h 未收口" % len(stale),
                "severity": "high" if len(stale) >= 3 else "medium",
                "detail": "；".join(
                    "%s(%sh,%s)" % (r["tid"], r["age_h"], r["owner"]) for r in stale[:4]
                ),
                "next": "逐单收口/认领/abort，明确 owner 与验收",
            }
        )
    e2e_candidates = [
        repo / "work" / "_dsh_mobile_e2e" / "mobile-e2e-auto.json",
        repo.parent / "work" / "_dsh_mobile_e2e" / "mobile-e2e-auto.json",
    ]
    e2e_path = next((p for p in e2e_candidates if p.exists()), None)
    if e2e_path:
        try:
            e2e = json.loads(e2e_path.read_text(encoding="utf-8"))
            auth = e2e.get("auth") or {}
            if "20029" in json.dumps(auth, ensure_ascii=False) or (
                auth.get("markers") or {}
            ).get("redirect_invalid"):
                blockers.append(
                    {
                        "type": "外部依赖",
                        "name": "飞书回调白名单未生效",
                        "severity": "high",
                        "detail": "授权页 20029 redirect_uri 不合法（自动检测 %s）"
                        % e2e_path.name,
                        "next": "老田后台加白名单或开通 application:application:patch 后自动加",
                    }
                )
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/legion_command/tianfeng_view.py", exc)
    else:
        blockers.append(
            {
                "type": "外部依赖",
                "name": "手机 E2E 待验证",
                "severity": "medium",
                "detail": "未找到 mobile-e2e-auto.json；OAuth 白名单/真机验证未完成",
                "next": "跑 work/_dsh_mobile_e2e/run_auto_e2e.py 并补白名单",
            }
        )
    e2e_pass = any(
        p.exists()
        for p in (
            repo / "work" / "_dsh_mobile_e2e" / "E2E_PASS.txt",
            repo.parent / "work" / "_dsh_mobile_e2e" / "E2E_PASS.txt",
        )
    )
    if not e2e_pass and not any(
        b["name"] in ("飞书回调白名单未生效", "手机 E2E 待验证") for b in blockers
    ):
        blockers.append(
            {
                "type": "外部依赖",
                "name": "手机 E2E / 飞书白名单 待完成",
                "severity": "medium",
                "detail": "OAuth 白名单未确认或未真机验证（无 E2E_PASS 标记）",
                "next": "老田后台加白名单/开 scope；真机验证后写 E2E_PASS.txt",
            }
        )
    blockers.append(
        {
            "type": "治理",
            "name": "P2c 全量暂缓",
            "severity": "info",
            "detail": "老田 22:5x 决策：P2c 暂缓（仅 P2a/P2b 已跑）",
            "next": "手机 E2E + GLM 样例 + P2b 观察后再议",
        }
    )
    blockers.append(
        {
            "type": "节奏",
            "name": "下午锚未首跑",
            "severity": "info",
            "detail": "田丰/诸葛/赵云 17:4x 锚点等待首个工作日",
            "next": "下一工作日 17:4x 后核对 rc 与产物",
        }
    )
    dispatch_path = repo / "work" / "tianfeng_dispatch.jsonl"
    drows = []
    if dispatch_path.exists():
        for ln in dispatch_path.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines():
            ln = ln.strip()
            if ln:
                try:
                    d = json.loads(ln)
                except ValueError:
                    d = None
                if isinstance(d, dict):
                    drows.append(d)
    d_open = [r for r in drows if r.get("status") in ("dispatched", "accepted")]
    d_blocked = [r for r in drows if r.get("status") == "blocked"]
    today = time.strftime("%Y-%m-%d")
    d_overdue = [r for r in d_open if r.get("due") and str(r.get("due")) < today]
    if d_overdue:
        blockers.append(
            {
                "type": "派工",
                "name": "%d 条派工超期" % len(d_overdue),
                "severity": "medium",
                "detail": "；".join(
                    "%s→%s" % (r.get("task"), r.get("to")) for r in d_overdue[:4]
                ),
                "next": "催办/升级/重派；更新回执状态",
            }
        )
    if d_blocked:
        blockers.append(
            {
                "type": "派工",
                "name": "%d 条派工 blocked" % len(d_blocked),
                "severity": "high",
                "detail": "；".join(
                    "%s→%s" % (r.get("task"), r.get("to")) for r in d_blocked[:4]
                ),
                "next": "解阻/换人/上报老田（L2）",
            }
        )
    workflow = {
        "wip": {
            "doing": len(doing_rows),
            "review": len(review_rows),
            "by_owner": owners,
            "avg_age_h": round(
                sum(r["age_h"] for r in wf_rows if r["age_h"] >= 0)
                / max(1, len(wf_rows)),
                1,
            ),
            "max_age_h": max([r["age_h"] for r in wf_rows] or [0]),
        },
        "stale": stale[:10],
        "dispatch": {
            "total": len(drows),
            "open": len(d_open),
            "blocked": len(d_blocked),
            "overdue": d_overdue[-5:],
            "recent": drows[-5:],
        },
        "blockers": blockers,
        "next_actions": [
            b["next"] for b in blockers if b["severity"] in ("high", "medium")
        ][:5],
    }
    ready = sum(1 for c in caps if c.get("status") == "ready")
    soul_title = "田丰 · 产品经理+研发（全在性）"
    try:
        for ln in (
            (HOME / "SOUL.md")
            .read_text(encoding="utf-8", errors="replace")
            .splitlines()
        ):
            if ln.startswith("# "):
                soul_title = ln[2:].strip()
                break
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/legion_command/tianfeng_view.py", exc)
    guard = _load(repo / "agent_platform" / "registry" / "tianfeng_guard.json", {})
    guard_desc = "allow_repo=%d / deny_repo=%d / exec_confirm=%d（检测层，非强制）" % (
        len(guard.get("allow_repo_prefixes") or []),
        len(guard.get("deny_repo_prefixes") or []),
        len(guard.get("exec_confirm_patterns") or []),
    )
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
            "attributes": {
                "experience": attrs.get("experience", ""),
                "specialties": attrs.get("specialties", []),
            },
        },
        "chips": [
            {"label": "SOUL", "value": "v3 · 产品/研发", "state": "good"},
            {
                "label": "能力",
                "value": "%d/%d" % (len(caps), len(caps)),
                "state": "good",
            },
            {
                "label": "审计",
                "value": "%d 通过 / %d 失败" % (audit["pass"], max(audit["fail"], 0)),
                "state": "good" if audit["fail"] == 0 else "bad",
            },
            {"label": "决策", "value": "L0/L1 自主 · L2 报批", "state": ""},
            {
                "label": "判读",
                "value": glm_state,
                "state": "warn" if "429" in glm_state else "good",
            },
            {
                "label": "Hermes",
                "value": "8787 PID %s" % pid,
                "state": "good" if health == 200 else "bad",
            },
        ],
        "status": [
            {
                "k": "Hermes profile",
                "v": "tianfeng（127.0.0.1:8787，PID %s，health %s）" % (pid, health),
            },
            {"k": "SOUL", "v": "v3 · 产品经理+研发（全在性）"},
            {"k": "能力清单", "v": "%d 项 / %d ready" % (len(caps), ready)},
            {"k": "工具集", "v": ", ".join(_toolsets()) or "—"},
            {"k": "L2 写守卫", "v": guard_desc},
            {"k": "席位卡", "v": "base=%s" % card.get("base")},
            {
                "k": "审计",
                "v": "pass %d / warn %d / fail %d（%s）"
                % (
                    audit["pass"],
                    audit["warn"],
                    max(audit["fail"], 0),
                    audit["verdict"],
                ),
            },
            {"k": "任务板", "v": "doing %d 单（全平台账本）" % doing},
            {"k": "最新证据包", "v": briefs[-1].name if briefs else "—"},
            {"k": "GLM 判读", "v": "%s（最近 %s）" % (glm_state, glm.get("at", "—"))},
            {"k": "双锚", "v": "田丰 08:45 / 17:45（S4U，battery-off）"},
        ],
        "capabilities": [
            {
                "name": c.get("name"),
                "what": c.get("what"),
                "boundary": c.get("boundary"),
                "status": c.get("status"),
            }
            for c in caps
        ],
        "decision_authority": [
            {
                "level": "L0 自主",
                "scope": auth.get(
                    "L0_autonomous",
                    "常规研发/文档/取数/测试/方案/代码参考/任务拆解与派发",
                ),
                "action": "直接执行 · 事件 + chief/decision 留痕",
                "human": "否",
            },
            {
                "level": "L1 执行后通报",
                "scope": auth.get(
                    "L1_notify_after", "跨部门派单/非生产配置/开发环境计划任务"
                ),
                "action": "可回滚即执行 · 执行后即时报",
                "human": "否",
            },
            {
                "level": "L2 必须先批",
                "scope": auth.get(
                    "L2_requires_human",
                    "生产/客户消息、资金/交易/审批、宿主机危险操作、上线/真实数据删除/密钥",
                ),
                "action": "五要素 + 老田明确批准；沉默≠同意",
                "human": "是",
            },
        ],
        "workflow": workflow,
        "links": {
            "hermes": "http://127.0.0.1:8787/?profile=tianfeng",
            "ide": "http://127.0.0.1:8734/ide",
            "legion": "http://127.0.0.1:8734/legion",
        },
    }
