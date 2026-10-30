# -*- coding: utf-8 -*-
"""赵云 · 治理席前端视图聚合（只读，供 8796 `/api/zhaoyun`）。

数据源（全部只读，不写任何真源）：
  · 身份/定位：本模块常量（与 docs/legion 身份对照表一致）
  · 能力卡：<profile>/skills/CAPABILITIES.json
  · 判例库：engine.experience_store.public_summary（默认 Z4 私有库；可传 root 供测试）
    —— 只暴露身份/统计/短 cue 摘要，**不返回 lesson 正文**：
       `recent` 保留最近 5 条（兼容旧口径），`all` 为全量条目（页面判例库展示全部）
  · 治理 AI 建议：<platform>/data/governance_ai/advice/<date>/{anomaly_digest,priority_advice,case_draft}.json
  · 治理健康（governance_health）：
      - ai_advice：tools/gov_ai.health_status()（健康卡 `governance.ai_advice` 单一真源）
      - experience_library：经验库统计判级（与健康卡 `governance.experience_library` 同口径）
  · 待签 payload：C:\\项目\\_vault\\sign_requests 下 **\\EXP-*.payload.json 只读扫描
    （只看文件是否存在签名，不读 payload 正文、不碰任何私钥）
  · 时钟：<platform>/data/governance_clock_state.json + governance_clock.lock
  · 保活：C:\\Temp\\governance_keepalive.log 最后一行（读不到则留空）

失败关闭：单个数据源读不到 → 对应字段为空 + note，不影响其余；整体异常由 serve 返回 500。
"""

from __future__ import annotations

import datetime as dt
import json
import os
import time
from pathlib import Path
from typing import Optional

IDENTITY = {
    "name": "赵云",
    "display_name": "赵云·治理",
    "seat": "ZHAOYUN（治理席）",
    "role": "治理面：扫描/收口/健康卡/AI 归纳建议/判例沉淀",
    "compilation": "profile 角色，不占 org.json 编制（D5-C）",
    "boundary": "只产建议与证据；不裁决、不自动执行、不持私钥",
    "r2_note": "R2（风控席）是 org 编制成员，与 ZHAOYUN 是两个 id，报表按 id 区分",
}

# 判例库：recent 保留条数（旧口径兼容）与 all 全量上限（防御性上限，12 条现状远未触顶）
_RECENT_KEEP = 5
_ALL_ENTRIES_LIMIT = 2000

# 本模块所在平台的 tools 目录（gov_ai 的导入位置；与 platform 参数解耦，测试可注入数据根）
_TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"

# 待签 payload 默认扫描根（只读；测试通过 build_view(sign_requests_root=...) 注入 tmp 目录）
DEFAULT_SIGN_REQUESTS_ROOT = Path(r"C:\\项目\\_vault\\sign_requests")  # 兼容常量；运行时用 _sign_root()
# 配置真源：registry 提供 display_name；路径支持环境变量覆盖（默认值保持兼容）
_REGISTRY_DIR = Path(__file__).resolve().parents[1] / "registry"
_DEFAULT_PROFILE = Path.home() / ".hermes" / "profiles" / "zhaoyun"
_DEFAULT_SIGN_ROOT = Path(r"C:\项目\_vault\sign_requests")
_DEFAULT_KEEPALIVE = Path(r"C:\Temp\governance_keepalive.log")


def _env_path(name: str, default: Path) -> Path:
    val = os.environ.get(name)
    return Path(val) if val else default


def _profile_dir() -> Path:
    return _env_path("ZHAOYUN_PROFILE_DIR", _DEFAULT_PROFILE)


def _sign_root() -> Path:
    return _env_path("ZHAOYUN_SIGN_REQUESTS_ROOT", _DEFAULT_SIGN_ROOT)


def _keepalive_path() -> Path:
    return _env_path("ZHAOYUN_KEEPALIVE_LOG", _DEFAULT_KEEPALIVE)


def _fail_log_path() -> Path:
    return _env_path("ZHAOYUN_VIEW_FAIL_LOG", Path(r"C:\Temp\zhaoyun_view_failures.jsonl"))


def _log_fail(source: str, exc: BaseException) -> None:
    """结构化失败日志（JSONL）：数据源降级必须留痕，不许静默。"""
    try:
        fp = _fail_log_path()
        fp.parent.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "source": source,
            "error": "%s: %s" % (type(exc).__name__, str(exc)[:200]),
        }
        with fp.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _registry_display_name() -> str:
    try:
        data = _read_json(_REGISTRY_DIR / "profile_seats.json") or {}
        for seat in data.get("seats") or []:
            if seat.get("agent_id") == "ZHAOYUN" and seat.get("display_name"):
                return str(seat["display_name"])
    except Exception:  # noqa: BLE001
        pass
    return str(IDENTITY.get("display_name") or "赵云·治理")


_PAYLOAD_SUFFIX = ".payload.json"


def _read_json(path: Path) -> Optional[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _mtime_iso(path: Path) -> str:
    try:
        return (
            dt.datetime.fromtimestamp(path.stat().st_mtime)
            .astimezone()
            .isoformat(timespec="seconds")
        )
    except OSError:
        return ""


def _capabilities(profile: Path) -> dict:
    data = _read_json(profile / "skills" / "CAPABILITIES.json") or {}
    caps = data.get("capabilities") or []
    rows = [
        {
            "name": str(c.get("name")),
            "what": str(c.get("what") or ""),
            "boundary": str(c.get("boundary") or ""),
            "status": str(c.get("status") or "unknown"),
        }
        for c in caps
        if isinstance(c, dict)
    ]
    return {
        "total": int(data.get("total") or len(rows)),
        "ready": int(
            data.get("ready") or sum(1 for r in rows if r["status"] == "ready")
        ),
        "pending_key": int(data.get("pending_key") or 0),
        "items": rows,
    }


def _experience(experience_root: Optional[Path]) -> dict:
    """经验库摘要 + 全量条目（只读、不含 lesson 正文）。"""
    try:
        from engine import experience_store as S

        summary = S.public_summary(root=experience_root, recent=_ALL_ENTRIES_LIMIT)
    except Exception as exc:  # noqa: BLE001
        _log_fail("experience", exc)
        return {
            "ok": False,
            "error": "%s: %s" % (type(exc).__name__, str(exc)[:160]),
            "entries_total": 0,
            "verified_total": 0,
            "recent": [],
            "all": [],
        }
    rows = list(summary.get("recent") or [])
    return {
        "ok": True,
        **summary,
        "recent": rows[:_RECENT_KEEP],  # 兼容字段：最近 5 条
        "all": rows,  # 全量条目（摘要字段，无 lesson 正文）
    }


def _load_gov_ai():
    """惰性导入 tools/gov_ai.py（tools 目录先入 sys.path）。

    单独成函数便于测试注入 import 失败：健康不可用时页面必须 fail-soft，不得 500。
    """
    import sys

    tools = str(_TOOLS_DIR)
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import gov_ai  # noqa: WPS433

    return gov_ai


def _gov_ai_health(platform: Path, now: float) -> dict:
    """治理 AI 健康（真源 gov_ai.health_status；import/调用失败一律 fail-soft 黄）。"""
    try:
        G = _load_gov_ai()
        status = G.health_status(
            root=Path(platform) / "data" / "governance_ai", now=now
        )
        level = str(status.get("level") or "yellow")
        if level not in ("green", "yellow", "red"):
            level = "yellow"
        return {
            "level": level,
            "detail": str(status.get("detail") or "")[:300],
            "last_success": status.get("last_success"),
            "age_hours": status.get("age_hours"),
            "consecutive_failures": status.get("consecutive_failures", 0),
        }
    except Exception as exc:  # noqa: BLE001
        _log_fail("gov_ai_health", exc)
        return {
            "level": "yellow",
            "detail": "不可用: %s: %s" % (type(exc).__name__, str(exc)[:160]),
        }


def _experience_health(exp: dict) -> dict:
    """经验库健康判级（口径与健康卡 `governance.experience_library` 完全一致）。

    空库=red；全部 verified=green；有条目但存在未验签=yellow。
    """
    if not exp.get("ok"):
        return {
            "level": "yellow",
            "detail": "经验库不可用: %s" % str(exp.get("error") or "")[:200],
        }
    total = int(exp.get("entries_total") or 0)
    verified = int(exp.get("verified_total") or 0)
    supervision = int(
        ((exp.get("domains") or {}).get("supervision") or {}).get("entries") or 0
    )
    if total == 0:
        return {"level": "red", "detail": "经验库为空：签字链尚未入库任何判例"}
    if verified >= total:
        return {
            "level": "green",
            "detail": "经验库 %d 条全部 verified；最近签字 %s；supervision=%d"
            % (total, exp.get("latest_signed_at") or "?", supervision),
        }
    return {
        "level": "yellow",
        "detail": "经验库 %d 条中 verified %d（存在未验签）" % (total, verified),
    }


def _governance_health(platform: Path, exp: dict, now: float) -> dict:
    """治理健康摘要：AI 建议（gov_ai 真源）+ 经验库（健康卡同口径）。"""
    return {
        "ai_advice": _gov_ai_health(platform, now),
        "experience_library": _experience_health(exp),
    }


def _archived_path(path: Path, base: Path) -> bool:
    """`_` 前缀目录视为归档（`_superseded`/`_expired` 约定）：归档 payload 不催签。"""
    try:
        rel = path.relative_to(base)
    except ValueError:
        return False
    return any(part.startswith("_") for part in rel.parts[:-1])


def _has_signature(payload: Path) -> bool:
    """签名文件与 payload 同目录，兼容两种命名：

      <item_id>.signature.json
      <item_id>.payload.json.signature.json

    item_id 取自 payload 文件名去掉 `.payload.json` 后缀。
    """
    item_id = payload.name[: -len(_PAYLOAD_SUFFIX)]
    for cand in (
        payload.parent / (item_id + ".signature.json"),
        payload.parent / (payload.name + ".signature.json"),
    ):
        try:
            if cand.is_file():
                return True
        except OSError:
            continue
    return False


def _pending_signatures(root: Optional[Path]) -> dict:
    """扫描待签 payload（只读）：没有对应签名的 EXP-*.payload.json 计入 pending。

    注入点：root 默认真实 `C:\\项目\\_vault\\sign_requests`，测试传 tmp 目录。
    归档目录（任一上级目录名以 `_` 开头）不催签，只记 `archived_pending` 供审计。
    """
    base = Path(root) if root is not None else _sign_root()
    out = {
        "ok": True,
        "root": str(base),
        "count": 0,
        "items": [],
        "scanned": 0,
        "signed": 0,
        "archived_pending": 0,
    }
    try:
        if not base.is_dir():
            out.update(ok=False, note="签名请求目录不存在: %s" % base)
            return out
        payloads = sorted(base.rglob("*" + _PAYLOAD_SUFFIX))
    except OSError as exc:
        out.update(
            ok=False, note="扫描失败: %s: %s" % (type(exc).__name__, str(exc)[:120])
        )
        return out
    for payload in payloads:
        if not payload.name.startswith("EXP-"):
            continue
        signed = _has_signature(payload)
        if _archived_path(payload, base):
            if not signed:
                out["archived_pending"] += 1
            continue
        out["scanned"] += 1
        if signed:
            out["signed"] += 1
            continue
        out["items"].append(
            {
                "item_id": payload.name[: -len(_PAYLOAD_SUFFIX)],
                "payload_path": str(payload),
            }
        )
    out["count"] = len(out["items"])
    return out


def _latest_advice(platform: Path, now: float) -> dict:
    root = platform / "data" / "governance_ai" / "advice"
    actions = ("anomaly_digest", "priority_advice", "case_draft")
    out: dict = {}
    for action in actions:
        cands = sorted(
            root.glob("*/%s.json" % action),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not cands:
            out[action] = {"exists": False, "items": []}
            continue
        path = cands[0]
        data = _read_json(path) or {}
        items = [
            {
                "id": it.get("id"),
                "title": str(it.get("title") or it.get("cue") or "")[:60],
                "confidence": it.get("confidence"),
                "evidence": (it.get("evidence") or [])[:3],
            }
            for it in (data.get("items") or [])
            if isinstance(it, dict)
        ]
        out[action] = {
            "exists": True,
            "status": data.get("status"),
            "generated_at": data.get("generated_at"),
            "input_digest": str(data.get("input_digest") or "")[:12],
            "model": data.get("model"),
            "items": items,
            "validator": data.get("validator") or {},
            "path": str(path),
            "updated_at": _mtime_iso(path),
        }
    return out


def _pid_alive(pid) -> bool:
    """进程存活探测（只读）。

    严禁 `os.kill(pid, 0)` 探活：Windows 上 os.kill 对非 Ctrl-C 信号的语义是
    **TerminateProcess**，探活可能误杀进程（本页 hotfix 教训）。Windows 用
    OpenProcess + GetExitCodeProcess(STILL_ACTIVE)；POSIX 才用 kill(pid, 0)。
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False
            try:
                code = ctypes.c_ulong(0)
                ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
                return bool(ok) and code.value == STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        except Exception:  # noqa: BLE001
            return False
    try:
        os.kill(pid, 0)  # POSIX：0 = 仅检查存在，不投递信号
        return True
    except OSError:
        return False


def _clock(platform: Path) -> dict:
    lock = _read_json(platform / "data" / "governance_clock.lock")
    state = _read_json(platform / "data" / "governance_clock_state.json") or {}
    pid = lock.get("pid") if isinstance(lock, dict) else None
    alive = _pid_alive(pid) if pid else False
    anchors = None
    try:
        import sys

        ops = str(platform.parent / "ops")
        if ops not in sys.path:
            sys.path.insert(0, ops)
        import governance_clock as GC  # noqa: WPS433

        anchors = list(GC.parse_anchors(GC.DEFAULT_ANCHORS))
    except Exception as exc:  # noqa: BLE001
        _log_fail("governance_clock", exc)
        anchors = None
    return {
        "pid": pid,
        "alive": alive,
        "started_at": (lock or {}).get("started_at"),
        "last_tick": state.get("last_tick_iso"),
        "fired_today": [k for k in (state.get("fired") or {}) if k],
        "anchors": anchors,
    }


def _keepalive() -> dict:
    log = _keepalive_path()
    try:
        lines = log.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    except OSError:
        return {"available": False, "last": ""}
    return {
        "available": True,
        "last": (lines[-1] if lines else "")[:200],
        "lines": len(lines),
    }


def build_view(
    platform: Path,
    profile: Optional[Path] = None,
    experience_root: Optional[Path] = None,
    now: Optional[float] = None,
    sign_requests_root: Optional[Path] = None,
) -> dict:
    """构建赵云页只读视图。

    可测试注入：platform / profile / experience_root / sign_requests_root
    （默认仍是真实路径）；now 用于确定性判级（测试传固定时刻）。
    """
    platform = Path(platform)
    profile = Path(profile) if profile else _profile_dir()
    now_ts = float(now if now is not None else time.time())
    caps = _capabilities(profile)
    exp = _experience(experience_root)
    advice = _latest_advice(platform, now_ts)
    clock = _clock(platform)
    keepalive = _keepalive()
    health = _governance_health(platform, exp, now_ts)
    pending = _pending_signatures(sign_requests_root)
    return {
        "ok": True,
        "generated_at": dt.datetime.fromtimestamp(now_ts)
        .astimezone()
        .isoformat(timespec="seconds"),
        "identity": {**IDENTITY, "display_name": _registry_display_name()},
        "failure_log": str(_fail_log_path()),
        "capabilities": caps,
        "experience": exp,
        "advice": advice,
        "clock": clock,
        "keepalive": keepalive,
        "governance_health": health,
        "pending_signatures": pending,
        "sign_chain": {
            "desc": "AI draft → 待签 payload → 本人签（Ed25519）→ 公钥验签 → Z4 supervision",
            "private_key_holder": "老田（Agent 不持私钥、不代签）",
            "strict_env": "EVOLVE_PRINCIPAL_PUBKEY_FILE + EVOLVE_SIGNATURE_STRICT=1",
            "verified_total": exp.get("verified_total", 0),
            "entries_total": exp.get("entries_total", 0),
        },
        "note": "只读视图；AI 建议均为 draft，需人评审/人签；不写任何真源",
    }
