# -*- coding: utf-8 -*-
"""Read-only Watch Admin snapshot aggregation (M2 real read-only + M3 A/B shadow).

This module is a PURE ADDITION to the quant trading portal. It does not import or
modify any production file (notably ``portal_api.py`` is never touched). Every
access it performs is read-only against existing persisted files / read-only
aggregators (``SnapshotAggregator`` from ``watch_tower_snapshot``, the org /
model-tier / combo registries, the shadow ledger, backtest output files, the
ST1 缠论 signal pool, and — *only when a read-only key is present in env* — the
8097 quant 中台 read-only GET endpoints and the 8098 model-admin static config).

Fail-visible contract (hard constraint):
  * A single unavailable source only marks THAT source ``unknown`` / SOURCE_UNVERIFIED.
  * The overall ``build()`` never raises because of a missing source; every zone
    builder is wrapped so one failure cannot break the whole snapshot.

No business state is written. The shadow zone never connects to a real broker or
Feishu; every "follow / order" affordance is locked (rehearsal valve).

Schema: ``watch_admin.snapshot.v1``. 15s TTL cache (mirrors SnapshotAggregator).

Data-source honesty (M2 correction, applied):
  * The production three-pools are PostgreSQL-backed (quant-pg:5442
    ``quant_system.trade_pool``) exposed read-only via the 8097 中台 GET
    endpoints. They are NOT the in-memory OFFLINE_SHADOW / RESEARCH_ONLY
    ``StockPoolSource`` / ``SignalPool`` (production_permitted=False). Those
    offline shadow pools are explicitly EXCLUDED from this page.
  * If a read-only hub key is present in env, we MAY attempt GET
    ``/api/quant/active`` + ``/api/quant/pending`` (read-only SELECT). If no key
    or 8097 offline, the production pools are marked SOURCE_UNVERIFIED and the
    real source (PG-backed 8097, requires key) is noted.
  * The one genuinely-readable real pool file is the ST1 缠论信号池
    (``quant_backtest/data/chanlun_pool.jsonl``) — safe file read.
  * Upstream agent ACK has NO persistence or queryable endpoint. We surface the
    contract (org.json QS1.identity.ack_semantics) and explicitly mark live ACK
    status as SOURCE_UNVERIFIED / 仅契约.
  * Model runtime binding has NO runtime log. We surface static config
    (org.json + model_tiers.json + 8098 /api/agents + /api/models) and explicitly
    note "运行时实际用量不可查，仅静态配置".
  * Backtest (Zone5): the real file ``quant_backtest/last_backtest.json`` is
    surfaced as-is and marked 缺 model/agent/combo 维度.
  * compare_portfolios (Zone7) is for k_stock/paper SIMULATION combos only —
    labeled 模拟盘 realm, never presented as production.
"""
from __future__ import annotations

import json
import math
import os
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Same-directory import; guaranteeing portal/ is importable regardless of CWD so
# that `import watch_admin_snapshot` from an arbitrary working directory works.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from watch_tower_snapshot import SnapshotAggregator, parse_replay_at  # noqa: E402
import logging


SCHEMA = "watch_admin.snapshot.v1"
DEFAULT_TTL_SEC = 15

# 8097 quant 中台 (read-only pool endpoints) — local by default, never 10.205.0.118.
QUANT_MID_HOST = os.environ.get("QUANT_MID_HOST", "127.0.0.1")
QUANT_MID_PORT = int(os.environ.get("QUANT_MID_PORT", "8097"))
# 8098 model-admin (static config endpoints, no auth on the read routes).
MODEL_ADMIN_HOST = os.environ.get("MODEL_ADMIN_HOST", "127.0.0.1")
MODEL_ADMIN_PORT = int(os.environ.get("MODEL_ADMIN_PORT", "8098"))

# Read-only GET timeout (seconds) — bounded so build() never hangs.
# 2026-10-30 WO-WATCH-ADMIN：8098 /api/models 实测 4s（Ollama 列表），3s 会误报超时。
_HTTP_TIMEOUT = 6.0


# --------------------------------------------------------------------------- #
# Source record helper (the canonical per-source envelope used by every zone).
# --------------------------------------------------------------------------- #
def _source(
    name: str,
    *,
    state: str,
    source_mode: str,
    error_code: Optional[str],
    note: str,
    data: Any,
    source: str,
    verified: bool,
    realm: str = "production",
) -> Dict[str, Any]:
    """Build a single source record.

    ``verified`` distinguishes a real, observed source from one we could not
    reach. Unverified sources are explicitly tagged ``SOURCE_UNVERIFIED`` so the
    UI and any QA assertion can detect "missing source" without ambiguity.
    ``realm`` carries the data-domain (production | simulation_paper) so the UI
    can label 模拟盘 vs 生产 distinctly.
    """
    return {
        "name": name,
        "state": state,                 # healthy | stale | unknown
        "source_mode": source_mode,     # file | http | computed
        "error_code": error_code,       # None | missing | invalid_json | import_error | ...
        "note": note,
        "data": data,
        "source": source,
        "verified": verified,
        "realm": realm,
        "source_quality": "verified" if verified else "SOURCE_UNVERIFIED",
    }


def _overall(zones: Dict[str, Dict[str, Any]]) -> str:
    """Derive an overall health from the per-zone states (fail-visible)."""
    states = [z.get("state") for z in zones.values()]
    if not states:
        return "unknown"
    if any(s == "unknown" for s in states):
        return "unknown"
    if any(s in ("stale",) for s in states):
        return "stale"
    return "healthy"


def _warnings(zones: Dict[str, Dict[str, Any]]) -> List[str]:
    """Collect every unverified source as a warning line."""
    out: List[str] = []
    for zid, z in zones.items():
        for s in z.get("sources", []):
            if not s.get("verified"):
                out.append("%s/%s:%s" % (zid, s.get("name"), s.get("error_code") or "unverified"))
    return out


def sanitize_inf_nan(obj: Any) -> Any:
    """Recursively replace non-finite floats (NaN/Inf) with None.

    Browser JSON.parse rejects ``NaN``/``Infinity``; the snapshot source files
    (e.g. last_backtest.json) contain raw ``NaN`` token which Python's json
    parses to float('nan'). We normalize before serving so the payload is
    strictly valid JSON.
    """
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: sanitize_inf_nan(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sanitize_inf_nan(v) for v in obj]
    return obj


class WatchAdminAggregator:
    """Aggregate a non-mutating snapshot of the Watch multi-Agent / model view.

    Zones (7):
      zone1 席位编组 (seat编组 from org.json + 张郃 contract note + team_invest 控辩审)
      zone2 模型路由 (org.json llm_binding + model_tiers.json + 8098 /api/agents+/api/models)
      zone3 三池漏斗 (ST1 chanlun_pool.jsonl 真实文件源 + 8097 生产池 key-gated; 离线影子池已排除)
      zone4 组合决策 (combo_registry listing + voting rules + runtime voting SOURCE_UNVERIFIED)
      zone5 回测对比 (last_backtest.json 实测, 标 缺 model/agent/combo 维度)
      zone6 上行时间线 (org.json ack_semantics 契约 + 实时 ACK SOURCE_UNVERIFIED/仅契约)
      zone7 A/B 影子对比 (M3: shadow_ledger.jsonl + combo_review.compare_portfolios 模拟盘)
    """

    def __init__(
        self,
        portal_root: Optional[str] = None,
        station_root: Optional[str] = None,
        ttl_sec: int = DEFAULT_TTL_SEC,
    ) -> None:
        self.portal_root = Path(portal_root or _HERE).resolve()
        self.station_root = Path(station_root or self.portal_root.parent).resolve()
        self.ttl_sec = max(5, min(int(ttl_sec), 30))
        # Derived paths ----------------------------------------------------- #
        self.registry_dir = self.station_root / "agent_platform" / "registry"
        self.kpaper_dir = self.station_root / "k_stock" / "paper"
        self.reports_dir = self.station_root / "quant_backtest" / "reports"
        self.agent_platform_data = self.station_root / "agent_platform" / "data"
        self.quant_backtest_dir = self.station_root / "quant_backtest"
        self.chanlun_pool_path = self.quant_backtest_dir / "data" / "chanlun_pool.jsonl"
        self.runtime_decisions_path = (self.quant_backtest_dir / "data" /
                                       "semantica" / "decisions_runtime.jsonl")
        # Cache (mirrors SnapshotAggregator._lock/_cached/_cached_at) --------- #
        self._lock = threading.Lock()
        self._cached: Optional[Dict[str, Any]] = None
        self._cached_at = 0.0
        # Transient flag used by zone7 to record why A/B compare was skipped.
        self._combo_review_import_err = False

    # ------------------------------------------------------------------ #
    # Low-level readers (read-only, never throw)                         #
    # ------------------------------------------------------------------ #
    def _read_json(self, path: Path) -> Tuple[Optional[Any], Optional[str]]:
        """Read a JSON file. Returns (value, error_code)."""
        try:
            with Path(path).open("r", encoding="utf-8") as handle:
                return json.load(handle), None
        except FileNotFoundError:
            return None, "missing"
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None, "invalid_json"
        except OSError:
            return None, "read_error"

    def _read_jsonl_count_sample(
        self, path: Path, max_sample: int = 5, max_keys: int = 8, max_val_len: int = 60
    ) -> Tuple[Optional[int], Optional[List[Dict[str, Any]]], Optional[str]]:
        """Safe read of a .jsonl file: total line count + a small key-truncated sample.

        Never loads the whole file into memory; only the first ``max_sample``
        records are parsed and truncated.
        """
        p = Path(path)
        if not p.exists():
            return None, None, "missing"
        try:
            total = 0
            sample: List[Dict[str, Any]] = []
            with p.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    total += 1
                    if len(sample) < max_sample:
                        try:
                            rec = json.loads(line)
                        except Exception:
                            sample.append({"_unparseable": True})
                            continue
                        if isinstance(rec, dict):
                            trimmed = {
                                k: (str(v)[:max_val_len] if not isinstance(v, (dict, list)) else v)
                                for k, v in list(rec.items())[:max_keys]
                            }
                            sample.append(trimmed)
                        else:
                            sample.append({"_scalar": str(rec)[:max_val_len]})
            return total, sample, None
        except OSError:
            return None, None, "read_error"

    def _http_get_json(
        self, url: str, headers: Optional[Dict[str, str]] = None
    ) -> Tuple[Optional[Any], Optional[str]]:
        """Read-only GET returning parsed JSON. Bounded timeout; never throws."""
        import urllib.error
        import urllib.request
        try:
            req = urllib.request.Request(url, headers=headers or {})
            # Avoid picking up proxy settings that would route the local call.
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(req, timeout=_HTTP_TIMEOUT) as resp:
                if resp.status != 200:
                    return None, "http_%d" % resp.status
                raw = resp.read().decode("utf-8", "replace")
            return json.loads(raw), None
        except urllib.error.HTTPError as exc:
            return None, "http_%d" % getattr(exc, "code", 0)
        except Exception as exc:  # socket.timeout / URLError / JSONDecode / etc.
            return None, "http_error:%s" % type(exc).__name__

    def _snapshot(self, replay_at: Optional[Any]) -> Optional[Dict[str, Any]]:
        """Reuse SnapshotAggregator (watch_tower.snapshot.v1) read-only.

        Per M2 correction #5: only used for Zone6 tower-health / replay context,
        NOT as the source of the three production pools.
        """
        try:
            agg = SnapshotAggregator(str(self.portal_root), str(self.station_root))
            if replay_at is not None:
                if isinstance(replay_at, str):
                    replay_at = parse_replay_at(replay_at)
                return agg.snapshot(replay_at)
            return agg.snapshot()
        except Exception:
            return None

    def _ensure_kpaper_path(self) -> None:
        d = str(self.kpaper_dir)
        if d not in sys.path:
            sys.path.insert(0, d)

    # ------------------------------------------------------------------ #
    # 8097 read-only key resolver (mirrors portal/quant_mid/api_routes.py)#
    # NEVER hardcode a secret; read from env or push_secrets.json only.   #
    # ------------------------------------------------------------------ #
    def _quant_mid_key(self) -> Optional[str]:
        """Resolve a read-only 8097 key from env / push_secrets.json.

        Mirrors ``portal/quant_mid/api_routes._auth_ok``:
          env PUSH_HUB_KEY|push_hub_key, or QUANT_MID_KEY|quant_mid_key,
          with a push_secrets.json fallback. Returns None when no key is present
          (in which case the page must NOT claim production pool data).
        """
        for env_name in ("PUSH_HUB_KEY", "push_hub_key", "QUANT_MID_KEY", "quant_mid_key"):
            v = os.environ.get(env_name)
            if v and v.strip():
                return v.strip()
        sp = os.environ.get(
            "PUSH_SECRETS_PATH",
            str(self.portal_root / "work" / "push_secrets.json"),
        )
        try:
            if os.path.exists(sp):
                sec = json.loads(Path(sp).read_text(encoding="utf-8"))
                for sk in ("push_hub_key", "quant_mid_key"):
                    if sec.get(sk):
                        return str(sec[sk]).strip()
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "portal/watch_admin_snapshot.py", exc)
        return None

    # ------------------------------------------------------------------ #
    # Zone builders                                                     #
    # ------------------------------------------------------------------ #
    def _zone(self, zid: str, title: str, sources: List[Dict[str, Any]], **extra: Any) -> Dict[str, Any]:
        states = [s["state"] for s in sources]
        if any(s == "unknown" for s in states):
            zstate = "unknown"
        elif any(s in ("stale",) for s in states):
            zstate = "stale"
        else:
            zstate = "healthy"
        z: Dict[str, Any] = {
            "zone_id": zid,
            "title": title,
            "state": zstate,
            "sources": sources,
            "verified_count": sum(1 for s in sources if s["verified"]),
            "unverified_count": sum(1 for s in sources if not s["verified"]),
        }
        z.update(extra)
        return z

    # ---- Zone 1: 席位编组 --------------------------------------------- #
    def _zone1_seats(self) -> Dict[str, Any]:
        sources: List[Dict[str, Any]] = []
        org_path = self.registry_dir / "org.json"
        org, err = self._read_json(org_path)
        invest_profiles: List[Dict[str, Any]] = []
        if isinstance(org, dict):
            for dept in org.get("departments", []):
                if dept.get("dept") == "invest":
                    invest_profiles = dept.get("agent_profiles") or []
                    break

        def _prof(aid: str) -> Optional[Dict[str, Any]]:
            return next((p for p in invest_profiles if p.get("agent_id") == aid), None)

        seat_data: Dict[str, Any] = {
            "WA1": {"role": "watch_monitor", "present": _prof("WA1") is not None,
                    "persona": (_prof("WA1") or {}).get("persona")},
            "QS1": {"role": "quant_sector_receiver", "present": _prof("QS1") is not None},
            "ST1": {"role": "quant_analyst", "present": _prof("ST1") is not None},
        }
        sources.append(_source(
            "org_invest_profiles",
            state="healthy" if org is not None else "unknown",
            source_mode="file", error_code=err,
            note=("org.json 投资部 agent_profiles（WA1 许褚 / QS1 司马懿 / ST1 缠论分析师）"
                  if org is not None else "org.json 缺失/不可读 → SOURCE_UNVERIFIED"),
            data={"profiles": seat_data,
                  "present": [k for k, v in seat_data.items() if v.get("present")]},
            source=str(org_path), verified=(org is not None)))

        # 张郃: M0 contract fact — 许褚辖下执行体, no independent registry entry.
        sources.append(_source(
            "zhang_he",
            state="healthy", source_mode="computed", error_code=None,
            note=("M0 契约冻结：张郃 = 许褚(WA1) 辖下执行体，组织层无独立注册项"
                  "（org.json 注释 + 研发计划评审稿 V1）。非独立席位，不计入盯盘 Agent 编组。"),
            data={"agent_id": "张郃", "relation": "许褚(WA1)辖下执行体",
                  "registered": False, "m0_contract": True},
            source="M0 契约冻结（研发计划评审稿 V1）", verified=True))

        # team_invest.json 控辩审拓扑
        team_path = self.registry_dir / "team_invest.json"
        team, terr = self._read_json(team_path)
        ctrl_roles: List[Dict[str, Any]] = []
        if isinstance(team, dict):
            for a in team.get("agents", []):
                rp = a.get("role_profile", "")
                if a.get("role") in ("bull", "bear", "evaluator") or rp in (
                        "t1_bull", "t2_bear", "d1_decision"):
                    ctrl_roles.append({
                        "agent_id": a.get("agent_id"),
                        "role": a.get("role"),
                        "role_profile": rp,
                        "llm_binding": a.get("llm_binding"),
                    })
        sources.append(_source(
            "alpha_team_roles",
            state="healthy" if team is not None else "unknown",
            source_mode="file", error_code=terr,
            note=("team_invest.json 控辩审拓扑（T1=bull / T2=bear / D1=evaluator）"
                  if team is not None else "team_invest.json 缺失 → SOURCE_UNVERIFIED"),
            data={"control_debate_review": ctrl_roles},
            source=str(team_path), verified=(team is not None)))
        return self._zone("zone1", "① 席位编组", sources)

    # ---- Zone 2: 模型路由 --------------------------------------------- #
    def _zone2_models(self) -> Dict[str, Any]:
        sources: List[Dict[str, Any]] = []
        org, err = self._read_json(self.registry_dir / "org.json")
        bindings: Dict[str, Optional[str]] = {}
        if isinstance(org, dict):
            for dept in org.get("departments", []):
                for p in (dept.get("agent_profiles") or []):
                    aid = p.get("agent_id")
                    if aid in ("WA1", "QS1", "ST1"):
                        bindings[aid] = p.get("llm_binding")
        # M0 默认（契约冻结口径）。2026-10-23 P2：移除 QS1 行，与
        # watch_admin_route_contract.M0_DEFAULT_BINDING 对齐——WO-LEGION-S43 已显式
        # 裁定 QS1 llm_binding=none，不设默认对照，也不把默认改成 none 伪装命中。
        m0_default = {
            "WA1": {"default": "ollama/qwen3.5:9b", "fallback": "云 glm-5.3 强判读"},
            "张郃": {"default": "ollama/qwen3.6:35b-a3b", "fallback": "本地 9b"},
            "ST1": {"default": "glm/glm-5.3", "fallback": "—"},
        }
        rows = []
        for aid in ("WA1", "张郃", "QS1", "ST1"):
            real = bindings.get(aid)
            d = m0_default.get(aid, {})
            rows.append({
                "agent": aid,
                "binding": real,
                "verified": bool(real),
                "suggested_default": d.get("default"),
                "fallback": d.get("fallback"),
            })
        sources.append(_source(
            "org_binding",
            state="healthy" if org is not None else "unknown",
            source_mode="file", error_code=err,
            note=("org.json llm_binding + model_tiers.json 静态档位表。"
                  "运行时实际用量不可查，仅静态配置；WA1=ollama/qwen3.5:9b、"
                  "ST1=glm/glm-5.3 已在册；QS1 已由 WO-LEGION-S43 显式裁定 "
                  "llm_binding=none（无模型绑定，不设 M0 默认对照、不再回落默认）；"
                  "M0 默认仅作契约建议，非运行时路由。"),
            data={"rows": rows,
                  "note": "运行时实际用量未接真源=SOURCE_UNVERIFIED（静态绑定字段在册可读）；M0 默认仅作建议，非真实运行时路由。",
                  "tiers_file": str(self.registry_dir / "model_tiers.json")},
            source=str(self.registry_dir / "org.json"), verified=(org is not None)))

        tiers, terr = self._read_json(self.registry_dir / "model_tiers.json")
        sources.append(_source(
            "model_tiers",
            state="healthy" if tiers is not None else "unknown",
            source_mode="file", error_code=terr,
            note=("model_tiers.json 模型档位表（E3=强模型/glm-5.3；E1=本地 qwen3.5:9b 降级档）。"
                  "静态配置，非运行时实例。" if tiers is not None
                  else "model_tiers.json 缺失 → SOURCE_UNVERIFIED"),
            data=tiers, source=str(self.registry_dir / "model_tiers.json"),
            verified=(tiers is not None)))

        # 8098 model-admin static config (no auth on read routes) — fail-visible.
        agents_url = "http://%s:%d/api/agents" % (MODEL_ADMIN_HOST, MODEL_ADMIN_PORT)
        models_url = "http://%s:%d/api/models" % (MODEL_ADMIN_HOST, MODEL_ADMIN_PORT)
        agents_val, agents_err = self._http_get_json(agents_url)
        models_val, models_err = self._http_get_json(models_url)
        if isinstance(agents_val, dict):
            ag = agents_val.get("agents") or []
            sources.append(_source(
                "model_admin_agents", state="healthy", source_mode="http", error_code=None,
                note=("8098 model-admin GET /api/agents：全军团绑定聚合（org.json 主源 + team_* 投影），"
                      "静态配置，非运行时用量。"),
                data={"agent_count": len(ag),
                      "sample": [{"agent_id": a.get("agent_id"),
                                  "binding": a.get("binding"),
                                  "grade": a.get("grade")} for a in ag[:8]]},
                source=agents_url, verified=True))
        else:
            sources.append(_source(
                "model_admin_agents", state="unknown", source_mode="http",
                error_code=agents_err or "unreachable",
                note=("8098 model-admin /api/agents 不可达（服务未启动或端口占用）→ SOURCE_UNVERIFIED。"
                      "静态绑定仍可经 org.json 读取。"),
                data=None, source=agents_url, verified=False))

        if isinstance(models_val, dict):
            loaded = models_val.get("loaded") or []
            cloud = models_val.get("cloud") or {}
            sources.append(_source(
                "model_admin_models", state="healthy", source_mode="http", error_code=None,
                note=("8098 model-admin GET /api/models：本地 Ollama 已加载实例 + 云端智谱 key 配置态。"
                      "仅反映 Ollama 当前加载实例，不表示某 Agent 此刻正在用哪个模型（运行时用量不可查）。"),
                data={"loaded_models": loaded,
                      "cloud_key_configured": bool(cloud.get("ok")),
                      "cloud_key_src": cloud.get("key_src")},
                source=models_url, verified=True))
        else:
            sources.append(_source(
                "model_admin_models", state="unknown", source_mode="http",
                error_code=models_err or "unreachable",
                note=("8098 model-admin /api/models 不可达 → SOURCE_UNVERIFIED。"),
                data=None, source=models_url, verified=False))
        return self._zone("zone2", "② 模型路由", sources)

    # ---- Zone 3: 三池漏斗 --------------------------------------------- #
    def _zone3_pools(self) -> Dict[str, Any]:
        sources: List[Dict[str, Any]] = []

        # (1) ST1 缠论信号池 — 唯一真实可读的池文件（安全文件读，仅计数+小样本）。
        total, sample, cerr = self._read_jsonl_count_sample(self.chanlun_pool_path, max_sample=3)
        if total is not None:
            sources.append(_source(
                "st1_chanlun_pool", state="healthy", source_mode="file", error_code=None,
                note=("ST1 缠论信号池（quant_backtest/data/chanlun_pool.jsonl）：真实可读文件源，"
                      "作为 ST1 信号候选数据点。本页仅计数+小样本，不加载全量、不写回。"),
                data={"total_lines": total, "sample_keys": list((sample or [{}])[0].keys())
                      if sample else [], "sample": sample},
                source=str(self.chanlun_pool_path), verified=True))
        else:
            sources.append(_source(
                "st1_chanlun_pool", state="unknown", source_mode="file", error_code=cerr,
                note=("ST1 缠论信号池（quant_backtest/data/chanlun_pool.jsonl）缺失/不可读"
                      " → SOURCE_UNVERIFIED。"),
                data=None, source=str(self.chanlun_pool_path), verified=False))

        # (2) 生产三池（PG-backed 8097）。离线影子 StockPoolSource/SignalPool 已排除。
        # 保守默认门控（待裁决项① 推荐默认 B）：默认不主动出网读生产三池；
        # 仅当显式设 WATCH_ADMIN_ALLOW_PROD_POOL=1 才真读 8097。
        active_url = "http://%s:%d/api/quant/active" % (QUANT_MID_HOST, QUANT_MID_PORT)
        pending_url = "http://%s:%d/api/quant/pending" % (QUANT_MID_HOST, QUANT_MID_PORT)
        health_url = "http://%s:%d/api/quant/health" % (QUANT_MID_HOST, QUANT_MID_PORT)
        allow_prod_pool = os.environ.get("WATCH_ADMIN_ALLOW_PROD_POOL") == "1"
        if not allow_prod_pool:
            # 保守默认：不出网、不持 key、不发任何请求；显式标注 opted_out。
            sources.append(_source(
                "production_trade_pool", state="unknown", source_mode="http", error_code="opted_out",
                note=("按保守默认未主动读取生产三池（默认不出网、不持 key、不发任何请求）。"
                      "设环境变量 WATCH_ADMIN_ALLOW_PROD_POOL=1 显式开启后，才会持只读 key 真读 8097"
                      " 中台（/api/quant/active + /api/quant/pending，仅 SELECT、不写回）。"
                      "真实源 = PostgreSQL（quant-pg:5442 quant_system.trade_pool）。"
                      "RESEARCH_ONLY 离线影子池 StockPoolSource/SignalPool 非生产、已排除，绝不作为生产真源呈现。"),
                data=None,
                source="%s + %s (opt-in via WATCH_ADMIN_ALLOW_PROD_POOL=1)" % (active_url, pending_url),
                verified=False))
        else:
            # 已显式开启：仅在此时才持只读 key 真读 8097（仍 fail-visible）。
            key = self._quant_mid_key()
            if key:
                headers = {"X-Push-Hub-Key": key}
                active_val, active_err = self._http_get_json(active_url, headers)
                pending_val, pending_err = self._http_get_json(pending_url, headers)
                health_val, _ = self._http_get_json(health_url)  # 探活无需 key
                if isinstance(active_val, dict) and isinstance(pending_val, dict):
                    sources.append(_source(
                        "production_trade_pool", state="healthy", source_mode="http", error_code=None,
                        note=("生产三池（quant-pg:5442 quant_system.trade_pool）经 8097 中台只读 GET 接入："
                              "active_watch + watch_alert（盯盘轮询）/ pending_research（投研拿票）。"
                              "本次持只读 key 成功拉取，仅 SELECT，不写回。"),
                        data={"active": {"count": active_val.get("count"),
                                         "items": (active_val.get("items") or [])[:8]},
                              "pending": {"count": pending_val.get("count"),
                                          "items": (pending_val.get("items") or [])[:8]},
                              "db_health": health_val},
                        source="%s + %s" % (active_url, pending_url), verified=True))
                else:
                    sources.append(_source(
                        "production_trade_pool", state="unknown", source_mode="http",
                        error_code=(active_err or pending_err or "unreachable"),
                        note=("持只读 key 尝试 8097 生产池只读 GET 失败（%s / %s）→ SOURCE_UNVERIFIED。"
                              "真实源为 PG-backed 8097（需 key），本页未做写操作。"
                              % (active_err, pending_err)),
                        data=None, source="%s + %s" % (active_url, pending_url), verified=False))
            else:
                sources.append(_source(
                    "production_trade_pool", state="unknown", source_mode="http", error_code="no_key",
                    note=("生产三池真实源 = PostgreSQL（quant-pg:5442 quant_system.trade_pool）"
                          "经 8097 中台只读 GET（/api/quant/active + /api/quant/pending，需 key）。"
                          "已显式开启 WATCH_ADMIN_ALLOW_PROD_POOL=1，但本页未持有只读 key → "
                          "SOURCE_UNVERIFIED（RESEARCH_ONLY 离线影子池 StockPoolSource/SignalPool 非生产、已排除）。"),
                    data=None, source="%s + %s (key-gated)" % (active_url, pending_url), verified=False))
        return self._zone("zone3", "③ 三池漏斗", sources)

    # ---- Zone 4: 组合决策 --------------------------------------------- #
    def _read_combo_registry(self) -> Tuple[Optional[Any], Optional[str], str]:
        """Return (registry_dict|None, error_code|None, method)."""
        path = self.kpaper_dir / "combos.json"
        try:
            self._ensure_kpaper_path()
            import combo_registry as _cr  # noqa: WPS433
            return _cr.load_registry(str(path)), None, "combo_registry.load_registry"
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "portal/watch_admin_snapshot.py", exc)
        try:
            with path.open("r", encoding="utf-8") as handle:
                return json.load(handle), None, "direct_json"
        except FileNotFoundError:
            return None, "missing", "direct_json"
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None, "invalid_json", "direct_json"
        except OSError:
            return None, "read_error", "direct_json"

    def _zone4_combos(self) -> Dict[str, Any]:
        sources: List[Dict[str, Any]] = []
        reg, err, method = self._read_combo_registry()
        combos = (reg or {}).get("combos", []) if isinstance(reg, dict) else []
        combo_rows: List[Dict[str, Any]] = []
        for c in combos:
            quad = c.get("quad", {})
            combo_rows.append({
                "combo_id": c.get("combo_id"),
                "name": c.get("name"),
                "quad": {k: "%s@%s" % (quad.get(k, {}).get("id"), quad.get(k, {}).get("version"))
                         for k in ("pick_source", "persona", "watch_style", "trader")},
                "red_line": c.get("_wo"),
            })
        sources.append(_source(
            "combo_registry",
            state="healthy" if reg is not None else "unknown",
            source_mode="file", error_code=err,
            note=("combo_registry（combos.json）：已注册组合列表 + 四元组印记（agent_id×model_id"
                  "×combo_version→run_id）。" if reg is not None
                  else "combos.json 缺失/不可读 → SOURCE_UNVERIFIED"),
            data={"count": len(combo_rows), "combos": combo_rows, "method": method},
            source=str(self.kpaper_dir / "combos.json"), verified=(reg is not None)))
        sources.append(_source(
            "voting_rules", state="healthy", source_mode="computed", error_code=None,
            note=("组合器规则（M0 契约/默认）：简单多数投票；弃权不计票；冲突 → 降级为"
                  "“分歧上报”，不强行合成信号。"),
            data={"rule": "majority", "abstain_counts": False,
                  "conflict": "escalate_divergence",
                  "combo_key": "agent_id × model_id × combo_version → run_id"},
            source="M0 契约冻结（研发计划评审稿 V1）", verified=True))
        sources.append(_source(
            "runtime_voting", state="unknown", source_mode="computed",
            error_code="no_runtime_feed",
            note=("运行时投票/分歧上报无实时供源接入（本页只读、不连运行体）→ SOURCE_UNVERIFIED。"),
            data=None, source="运行时投票源（未接入）", verified=False))
        # 2026-10-30 WO-WATCH-ADMIN：运行期决策流水（真实文件源；非投票）。
        rows, rerr = self._read_runtime_decisions()
        if rows:
            closes = [r for r in rows if r.get("t") == "close"]
            sources.append(_source(
                "runtime_decisions", state="healthy", source_mode="file", error_code=None,
                note=("quant_backtest/data/semantica/decisions_runtime.jsonl：运行期决策流水"
                      "（record/close，只读；含决策与后验结果）。"),
                data={"count": len(rows), "closed": len(closes), "recent": rows[-5:]},
                source=str(self.runtime_decisions_path), verified=True))
        else:
            sources.append(_source(
                "runtime_decisions", state="unknown", source_mode="file",
                error_code=rerr or "no_data",
                note=("运行期决策流水缺失/不可读 → SOURCE_UNVERIFIED。"), data=None,
                source=str(self.runtime_decisions_path), verified=False))
        return self._zone("zone4", "④ 组合决策", sources)

    # ---- Zone 5: 回测对比 --------------------------------------------- #
    def _scan_backtest_files(self) -> Dict[str, Any]:
        found: Dict[str, Any] = {}
        candidates = [
            self.quant_backtest_dir / "last_backtest.json",
            self.reports_dir / "rps_latest.json",
            self.reports_dir / "rps_custom_backtest.json",
            self.reports_dir / "rps_custom_batches.jsonl",
            self.reports_dir / "cbt_jobs.jsonl",
            self.reports_dir / "cbt_queue_state.json",
        ]
        for p in candidates:
            if not p.exists():
                continue
            try:
                if p.suffix == ".jsonl":
                    rows = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines()
                            if line.strip()]
                    found[str(p)] = rows[:50]
                else:
                    found[str(p)] = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                found[str(p)] = {"_error": "unreadable"}
        return found

    def _read_backtest(self) -> Dict[str, Any]:
        files = self._scan_backtest_files()
        # Best-effort reuse of api_backtest.get_backtest (guarded; never required).
        live_val: Any = None
        import_err: Optional[str] = None
        try:
            import importlib
            mod = importlib.import_module("api_backtest")
            fn = getattr(mod, "get_backtest", None)
            if callable(fn):
                live_val = fn()
        except Exception as exc:  # noqa: BLE001
            import_err = "import_error:%s" % type(exc).__name__

        bt = live_val if isinstance(live_val, dict) else {}
        # Prefer the directly-read file (authoritative), fall back to get_backtest().
        file_bt = files.get(str(self.quant_backtest_dir / "last_backtest.json"))
        if isinstance(file_bt, dict):
            bt = file_bt
        if isinstance(bt, dict) and bt.get("results") is not None:
            results = bt.get("results") if isinstance(bt.get("results"), list) else []
            # Sort by sharpe desc for a stable, useful sample (NaN sanitized for view).
            def _sharpe(r: Any) -> float:
                v = (r or {}).get("sharpe")
                return v if isinstance(v, (int, float)) and math.isfinite(v) else -1e9
            ordered = sorted(results, key=_sharpe, reverse=True)
            top = ordered[:12]
            by_market: Dict[str, int] = {}
            for r in results:
                m = (r or {}).get("market", "UNKNOWN")
                by_market[m] = by_market.get(m, 0) + 1
            return _source(
                "backtest_outputs", state="healthy", source_mode="file", error_code=None,
                note=("真实回测输出（quant_backtest/last_backtest.json，ts=%s）：策略级扫描结果，"
                      "按 ts/strategy/market/mode 呈现实测。文件缺 model/agent/combo 维度字段"
                      "（仅策略级结果），故无法按 agent/model/combo 分面，仅按 market 归并；"
                      "benchmark_return 含 NaN（已规整为 null）。绝不使用 [演示值]。"
                      % bt.get("ts")),
                data={
                    "meta": {"ts": bt.get("ts"), "strategy": bt.get("strategy"),
                             "market": bt.get("market"), "mode": bt.get("mode"),
                             "result_count": len(results)},
                    "missing_dimensions": ["model", "agent", "combo"],
                    "by_market": by_market,
                    "top_by_sharpe": sanitize_inf_nan(top),
                    "get_backtest": ({"ok": True} if live_val is not None
                                     else {"ok": False, "err": import_err}),
                },
                source=str(self.quant_backtest_dir / "last_backtest.json"), verified=True)
        # No real backtest output → [演示值] placeholder, explicitly labelled.
        demo = {
            "is_demo": True,
            "note": ("[演示值] 占位，不可验证；真实口径待 M2 接入真实回测"
                     "（统一窗口 + FeeSchedule + 样本外 walk-forward）。绝不伪造真实数字。"),
            "rows": [
                {"model": "本地 qwen3.5:9b", "samples": 1200, "annual": 14.2, "sharpe": 1.10,
                 "win": 53, "dd": -9.8},
                {"model": "云 glm-5.3", "samples": 1200, "annual": 17.6, "sharpe": 1.34,
                 "win": 56, "dd": -8.1},
            ],
        }
        return _source(
            "backtest_outputs", state="unknown", source_mode="computed", error_code="missing",
            note=("回测输出文件（quant_backtest/last_backtest.json、reports/*.json 等）均不存在；"
                  "api_backtest.get_backtest 未取到数据（%s）。按任务以 [演示值] 占位并明确标注，"
                  "绝不伪造真实数字。" % (import_err or "无实时数据")),
            data=demo,
            source="quant_backtest/reports（缺失）/api_backtest（无数据）", verified=False)

    def _zone5_backtest(self) -> Dict[str, Any]:
        return self._zone("zone5", "⑤ 回测对比", [self._read_backtest()])

    # ---- Zone 6: 上行时间线 ------------------------------------------- #
    def _find_qs1_ack_semantics(self) -> Optional[str]:
        org, _ = self._read_json(self.registry_dir / "org.json")
        if not isinstance(org, dict):
            return None
        for dept in org.get("departments", []):
            for p in (dept.get("agent_profiles") or []):
                if p.get("agent_id") == "QS1":
                    ident = p.get("identity") or {}
                    return ident.get("ack_semantics")
        return None

    def _zone6_upstream(self) -> Dict[str, Any]:
        sources: List[Dict[str, Any]] = []
        # 契约：org.json QS1.identity.ack_semantics（真实可读静态契约）
        ack = self._find_qs1_ack_semantics()
        sources.append(_source(
            "ack_contract", state="healthy" if ack else "unknown", source_mode="file",
            error_code=None if ack else "missing",
            note=("上行受理契约（org.json QS1.identity.ack_semantics）：" + (ack or "未找到"))
                  if ack else "org.json 中 QS1.identity.ack_semantics 缺失 → SOURCE_UNVERIFIED",
            data={"ack_semantics": ack,
                  "receiver": "QS1 司马懿（量化上行受理）",
                  "semantics": "受理=agent 级 ACK（AgentAck）；受理≠授权≠研判确认"},
            source=str(self.registry_dir / "org.json"), verified=bool(ack)))

        # 实时 ACK 状态：无持久化/无可查询端点（route_outbox.py 内存态/离线）→ 仅契约
        sources.append(_source(
            "upstream_ack_live", state="unknown", source_mode="computed",
            error_code="contract_only",
            note=("实时 ACK 状态：route_outbox.py（RouteReceipt/ActiveAckPort）为内存态/离线"
                      "（No send, no trade, no persistence root；production_permitted=False），"
                      "无持久化、无可查询端点。故实时 ACK 状态仅契约、不可查 → SOURCE_UNVERIFIED/仅契约。"),
            data={"queryable": False, "persistence": False,
                  "blocks": ["飞书真实下发", "GAP-4", "051 时序缺口", "真实通知", "交易"]},
            source="上行链路（仅契约，无可查询端点）", verified=False))

        # 塔台健康（SnapshotAggregator 代理，M2 修正#5：仅作 tower-health/replay 上下文）
        snap = self._snapshot(None)
        sources.append(_source(
            "tower_health",
            state=snap.get("overall", "unknown") if snap else "unknown",
            source_mode="computed", error_code=(None if snap else "import_error"),
            note=("复用 SnapshotAggregator 的 portal_health/jev_stack/linkage 作为上行链路塔台健康"
                      "只读代理视图（非生产三池源）。"),
            data=snap, source="SnapshotAggregator", verified=bool(snap)))
        return self._zone("zone6", "⑥ 上行时间线", sources)

    # ---- Zone 7: A/B 影子对比 (M3) ------------------------------------ #
    def _read_runtime_decisions(self) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """只读运行期决策流水（decisions_runtime.jsonl）；缺失不编造。"""
        p = Path(self.runtime_decisions_path)
        if not p.exists():
            return [], "missing"
        rows: List[Dict[str, Any]] = []
        try:
            with p.open("r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        item = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(item, dict):
                        rows.append(item)
            return rows, None
        except OSError:
            return [], "read_error"

    def _read_shadow_ledger(self, path: Path) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
        p = Path(path)
        if not p.exists():
            return None, "missing"
        try:
            rows: List[Dict[str, Any]] = []
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            return rows, None
        except OSError:
            return None, "read_error"

    @staticmethod
    def _aggregate_shadow(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        by_date: Dict[str, int] = {}
        rec_dist: Dict[str, int] = {}
        scores: List[float] = []
        for r in rows:
            d = r.get("date")
            if d:
                by_date[d] = by_date.get(d, 0) + 1
            rec = r.get("recommendation")
            if rec is not None:
                rec_dist[str(rec)] = rec_dist.get(str(rec), 0) + 1
            sc = r.get("score")
            if isinstance(sc, (int, float)):
                scores.append(float(sc))
        return {
            "count": len(rows),
            "by_date": by_date,
            "recommendation_distribution": rec_dist,
            "score_avg": round(sum(scores) / len(scores), 4) if scores else None,
            "recent": rows[-20:],
        }

    def _compare_combos(self) -> Optional[Dict[str, Any]]:
        """Best-effort A/B ranking via combo_review.compare_portfolios (guarded).

        IMPORTANT (M2 correction #5): compare_portfolios is for k_stock/paper
        SIMULATION combos only — this is the 模拟盘 realm, never production.
        """
        self._combo_review_import_err = False
        try:
            self._ensure_kpaper_path()
            import combo_review  # noqa: WPS433
            reg = self._read_combo_registry()[0]
            if not isinstance(reg, dict):
                return None
            ids = [c["combo_id"] for c in reg.get("combos", []) if c.get("combo_id")]
            if not ids:
                return None
            return combo_review.compare_portfolios(ids)
        except Exception:  # noqa: BLE001
            self._combo_review_import_err = True
            return None

    def _zone7_shadow(self) -> Dict[str, Any]:
        sources: List[Dict[str, Any]] = []
        ledger_path = self.agent_platform_data / "shadow" / "shadow_ledger.jsonl"
        rows, lerr = self._read_shadow_ledger(ledger_path)
        if rows is not None:
            sources.append(_source(
                "shadow_ledger", state="healthy", source_mode="file", error_code=None,
                note=("run_shadow 影子账本（data/shadow/shadow_ledger.jsonl）：逐票决策快照（只读）。"),
                data=self._aggregate_shadow(rows), source=str(ledger_path), verified=True))
        else:
            sources.append(_source(
                "shadow_ledger", state="unknown", source_mode="file", error_code=lerr,
                note=("影子账本（agent_platform/data/shadow/shadow_ledger.jsonl）缺失/不可读"
                      " → SOURCE_UNVERIFIED。无后验数据。"),
                data=None, source=str(ledger_path), verified=False))

        cmp = self._compare_combos()
        if cmp is not None:
            sources.append(_source(
                "ab_compare", state="healthy", source_mode="computed", error_code=None,
                note=("combo_review.compare_portfolios 横向对比（已注册组合，ROI 待结算后验回填）。"
                      "⚠ 数据域=模拟盘（k_stock/paper SIMULATION combos），非生产盘，"
                      "仅用于 M3 A/B 影子对比只读观察。"),
                data=cmp, source="k_stock/paper/combo_review.compare_portfolios",
                verified=True, realm="simulation_paper"))
        else:
            sources.append(_source(
                "ab_compare", state="unknown", source_mode="computed",
                error_code="import_error" if self._combo_review_import_err else "no_data",
                note=("A/B 横向对比：combo_review.compare_portfolios 不可用时"
                      "（模块未导入/无后验）→ SOURCE_UNVERIFIED。组合 ROI 需结算后验，当前未接入。"),
                data=None, source="k_stock/paper/combo_review.compare_portfolios",
                verified=False, realm="simulation_paper"))

        sources.append(_source(
            "posterior_metrics", state="unknown", source_mode="computed", error_code="not_wired",
            note=("后验指标（夏普/胜率/回撤/ROI 排名）需结算后验配对，尚未接入 → SOURCE_UNVERIFIED。"),
            data=None, source="结算后验（未接入）", verified=False))
        # 2026-10-30 WO-WATCH-ADMIN：从运行期决策流水计算可复核的后验统计（胜率/均值）。
        rows, rerr = self._read_runtime_decisions()
        closes = [r for r in rows or [] if r.get("t") == "close"
                  and r.get("outcome") in ("win", "loss")]
        if closes:
            wins = sum(1 for r in closes if r.get("outcome") == "win")
            realized = [float(r.get("realized") or 0.0) for r in closes]
            sources.append(_source(
                "posterior_metrics", state="healthy", source_mode="computed", error_code=None,
                note=("从 decisions_runtime.jsonl 的 close 记录计算：胜率/均值收益/样本数"
                      "（只读；非夏普/最大回撤全量指标）。"),
                data={"sample": len(closes), "wins": wins,
                      "win_rate": round(wins / len(closes), 4),
                      "avg_realized": round(sum(realized) / len(realized), 6),
                      "source_file": str(self.runtime_decisions_path)},
                source=str(self.runtime_decisions_path), verified=True))
        else:
            sources.append(_source(
                "posterior_metrics", state="unknown", source_mode="computed",
                error_code=rerr or "no_closed_sample",
                note=("无 close 后验样本 → SOURCE_UNVERIFIED。"),
                data=None, source=str(self.runtime_decisions_path), verified=False))

        z = self._zone("zone7", "⑦ A/B 影子对比（M3）", sources)
        z["interactive_locked"] = True
        z["trading_permitted"] = False
        z["rehearsal_note"] = ("所有“跟随/下单”按钮恒置灰：受 run_shadow --rehearsal 预演阀约束，"
                               "拒绝真仓；影子账本 trading_permitted=False。A/B 对比为模拟盘域。")
        return z

    # ------------------------------------------------------------------ #
    # Public entry point                                                #
    # ------------------------------------------------------------------ #
    def build(self, replay_at: Optional[Any] = None) -> Dict[str, Any]:
        """Build (and cache) the full watch-admin snapshot. Never raises.

        A ``replay_at`` may be an ISO-8601 string/datetime; invalid values are
        downgraded to a live snapshot (fail-visible, never throws).
        """
        parsed_replay: Optional[Any] = None
        replay_iso: Optional[str] = None
        if replay_at is not None:
            try:
                if isinstance(replay_at, str):
                    parsed_replay = parse_replay_at(replay_at)
                else:
                    parsed_replay = replay_at
                replay_iso = (parsed_replay.isoformat()
                              if hasattr(parsed_replay, "isoformat") else str(parsed_replay))
            except Exception:
                parsed_replay = None
                replay_iso = None

        now = time.monotonic()
        if parsed_replay is None:
            with self._lock:
                if self._cached is not None and (now - self._cached_at) < self.ttl_sec:
                    return self._cached

        builders = [
            ("zone1", self._zone1_seats),
            ("zone2", self._zone2_models),
            ("zone3", self._zone3_pools),
            ("zone4", self._zone4_combos),
            ("zone5", self._zone5_backtest),
            ("zone6", self._zone6_upstream),
            ("zone7", self._zone7_shadow),
        ]
        zones: Dict[str, Dict[str, Any]] = {}
        for zid, fn in builders:
            try:
                zones[zid] = fn()
            except Exception as exc:  # noqa: BLE001
                zones[zid] = self._zone(zid, zid, [_source(
                    zid + "_error", state="unknown", source_mode="computed",
                    error_code="build_error",
                    note="区域构建异常（fail-visible，不影响其他区域）：%s" % str(exc)[:160],
                    data=None, source="internal", verified=False)])

        out: Dict[str, Any] = {
            "schema": SCHEMA,
            "snapshot_id": uuid.uuid4().hex,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "replay": parsed_replay is not None,
            "replay_at": replay_iso,
            "overall": _overall(zones),
            "zones": zones,
            "blocks": ["飞书真实下发", "GAP-4", "051 时序缺口", "真实通知", "交易"],
            "warnings": _warnings(zones),
        }
        if parsed_replay is None:
            with self._lock:
                self._cached = out
                self._cached_at = now
        return out


if __name__ == "__main__":
    import pprint
    pprint.pprint(sanitize_inf_nan(WatchAdminAggregator().build()))
