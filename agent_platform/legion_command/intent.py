# -*- coding: utf-8 -*-
"""军团 OS · 席位意图卡契约（v1）——**现役实现（8796 /api/intent 唯一数据源）**

这不是一个页面，是一份契约：每席固定 6 行，后端一次算好，任何壳（H5/小程序/飞书）
都只负责把这些字段画出来。换壳不改契约。

六行固定结构（顺序与字段名不得变，改就升版本）：
  L1_role       主要作用（职责原文 + 是否登记）
  L2_doing      当前在办（dept→lead→seat 三级链 + 未认领显式计数）
  L3_model      模型（云/本地 + 档位 + 能干/干不了的活类）
  L4_capability 能力三态（declared / evidence / verified **三件事分开**）
  L5_boundary   数据边界与汇报边（链条 + 质量）
  L6_gaps       缺口与下一步（每条带 requires：谁能补）

三条硬规矩：
  1. **未认领必须显式**：认不出来就给 UNCLAIMED，不按“看起来合适”分摊给人。
  2. **能力三态绝不合并**：声明 ≠ 有产出证据 ≠ 已验收；没有台账就是 verified=False
     并显式标 ledger_missing，而不是默认“应该能用”。
  3. **口径写进契约**：在途含不含 review、算哪些前缀、每类活最低模型档，全在这里定死，
     由 test_legion_intent_contract.py 锁住；快照/页面/报表都读这一份。

只读：不写 org.json / tasks.json，不派工；缺口只产草稿（DRAFT_UNSIGNED）。
"""

from __future__ import annotations

import collections
import json
import pathlib
import time

SCHEMA = "legion.os.intent_card/v1"
PLATFORM = pathlib.Path(__file__).resolve().parents[1]
LEDGER = PLATFORM / "data" / "capability_ledger.json"
DRAFT_SCHEMA = "legion.wo_draft/v1"

# ── 口径锁 1：什么算“在途” ─────────────────────────────────────
# 曾经争论“19 还是 18”，根因是没人定死 review 算不算在途。
# 定死：**在途只算 status == "doing"**；review 单列 pending_review，不进在途。
DOING_STATUS = ("doing",)
REVIEW_STATUS = ("review",)
ACTIVE_STATUS = DOING_STATUS + REVIEW_STATUS

# ── 口径锁 2：前缀 → 部门（部门边界定死，只到部门不猜人）────────
PREFIX_MAP = {
    "K-INTEL": ("intel", "情报侧立项单"),
    "K-ORG": ("ops", "组织治理单"),
    "K-QUANT": ("invest", "量化专项单"),
    "K-MDC": ("intel", "多源数据归集（现存单产出 intel_lhb_shadow.py）"),
    "K-STOCK": ("invest", "量化盯盘/行情单"),
    "CM-": ("chief_line", "参谋长线交办"),
    "legion-": ("invest", "军团指挥台派单"),
}
PREFIX_MAP_VERSION = "1.0"

# ── 口径锁 3：任务分级 → 最低模型档 ───────────────────────────
# 模型不绑人，绑“任务类”。档位由弱到强：L1 本地小模型 < L2 本地大参数 < C1 云端主力 < C3 云端顶配
MODEL_TIER = {
    "ollama/qwen3.5:9b": ("L1", 1),
    "ollama/qwen3.6:35b-a3b": ("L2", 2),
    "glm/glm-5.3-flash": ("C1", 3),
    "glm/glm-5.3": ("C1", 3),
    "openai/gpt-6.0": ("C3", 5),
}
NONE_TIER = ("X", 0)

TASK_CLASS_MIN = {
    "format": ("L1", 1, "高频格式化/摘要/字段抽取"),
    "extract": ("L1", 1, "规则化抽取、批量清洗"),
    "analyze": ("L2", 2, "单域分析、指标解释"),
    "reason": ("C1", 3, "跨域推理、方案比较、长文写作"),
    "decide": ("C3", 5, "需最终判断的决策/风控复核"),
}

GAP_TITLE = {
    "NO_CHARTER": "补齐席位职责登记",
    "NO_SPECIALTY": "补齐席位专长/经历",
    "NO_TOOLS": "补齐席位工具登记",
    "NO_GRANT": "补齐席位数据授权（最小权限）",
    "NO_MODEL": "确定席位模型（配置或明确不需要）",
    "LEAD_UNSIGNED": "补齐部门主官正式任命",
    "NO_PROFILE": "席位建档（backfill）",
    "NOT_VERIFIED": "能力验收（跑一次真实任务并记台账）",
}


def model_tier(binding: str):
    return MODEL_TIER.get((binding or "").strip(), NONE_TIER)


def check_model_for(task_class: str, binding: str) -> dict:
    """派活前校验：这个模型够不够干这类活。不够就明确说不够，不降级硬跑。"""
    tc = TASK_CLASS_MIN.get(task_class)
    if not tc:
        return {"ok": False, "why": "未知任务类：%s（未定义最低模型档）" % task_class}
    name, need, desc = tc
    got_name, got = model_tier(binding)
    ok = got >= need
    return {
        "ok": ok, "task_class": task_class, "need": name, "need_desc": desc,
        "got": got_name, "binding": binding or "none",
        "why": ("满足最低档（%s ≥ %s）" % (got_name, name)) if ok else
               ("模型档不足：该类活最低要 %s（%s），该席是 %s —— 应改派或升级模型"
                % (name, desc, got_name)),
    }


def _load(p: pathlib.Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def load_ledger(ledger_path: pathlib.Path | None = None) -> dict:
    """能力验证台账。没有台账 ≠ 有能力：缺文件时显式标 missing。"""
    p = pathlib.Path(ledger_path) if ledger_path else LEDGER
    d = _load(p, None)
    if not isinstance(d, dict):
        return {"schema": "legion.capability.ledger/v1", "entries": {}, "missing": True}
    d.setdefault("entries", {})
    d["missing"] = False
    return d


def classify_key(key: str):
    """返回 (dept, rule, unmapped)。命中即停；不命中就是 unmapped，不猜。"""
    k = str(key or "")
    for pat, (dept, note) in PREFIX_MAP.items():
        if k.startswith(pat):
            return dept, "%s（%s）" % (pat, note), False
    return "", "", True


def _dedup_chain(raw: list) -> list:
    """汇报链相邻去重：本人即主官、主官即 A0 时不重复出现。

    只去相邻重复，不做全局去重——全局去重会把"跨部门兼任回到同一个人"
    这类真实结构抹掉，链就不再反映实际路径。
    """
    out: list = []
    for x in raw:
        if not out or out[-1] != x:
            out.append(x)
    return out


def build_cards(platform_dir: pathlib.Path | None = None,
                ledger_path: pathlib.Path | None = None) -> dict:
    """一次算好所有席位的意图卡 + 任务归属 + 口径声明。"""
    root = pathlib.Path(platform_dir) if platform_dir else PLATFORM
    import sys
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from legion_command.snapshot import build_snapshot

    snap = build_snapshot(root)
    org = _load(root / "registry" / "org.json", {}) or {}
    tasks = _load(root.parent / ".worktrees" / "tasks.json", {}) or {}
    ledger = load_ledger(ledger_path)
    # 员工编号台账（engine/emp_numbering.py 发号）：只读挂载，缺失就是没发号
    emp_map = (_load(root / "data" / "emp_ledger.json", {}) or {}).get("employees") or {}

    # ---- 部门：主官与正式任命 ----
    org_depts = {d.get("dept"): d for d in (org.get("departments") or [])}
    lead_of, lead_signed = {}, {}
    for dep_id, od in org_depts.items():
        lead_of[dep_id] = od.get("lead") or ""
        lead_signed[dep_id] = bool(od.get("lead_appointed"))

    # ---- 席位深字段 ----
    prof, home, depts_of = {}, {}, {}
    for dep_id, od in org_depts.items():
        for p in od.get("agent_profiles") or []:
            aid = p.get("agent_id")
            if not aid:
                continue
            prof.setdefault(aid, p)
            home.setdefault(aid, dep_id)
            depts_of.setdefault(aid, set()).add(dep_id)

    # ---- 任务：按口径分桶（doing 不含 review）----
    by_seat_doing, unclaimed = {}, {}
    doing_list, review_list, unmapped = [], [], []
    counts = {"doing": 0, "review": 0, "done": 0, "aborted": 0, "other": 0}
    for k, v in tasks.items():
        st = (v or {}).get("status")
        if st == "done":
            counts["done"] += 1
        elif st == "aborted":
            counts["aborted"] += 1
        elif st in REVIEW_STATUS:
            counts["review"] += 1
            review_list.append(k)
        elif st in DOING_STATUS:
            counts["doing"] += 1
            dept, rule, miss = classify_key(k)
            doing_list.append({"key": k, "dept": dept, "rule": rule, "unmapped": miss,
                               "owner": (v or {}).get("owner") or ""})
            if miss:
                unmapped.append(k)
            own = str((v or {}).get("owner") or "")
            if own and own not in ("unknown-agent", ""):
                by_seat_doing.setdefault(own, []).append(k)
            else:
                unclaimed.setdefault(dept or "UNCLAIMED", []).append(k)
        else:
            counts["other"] += 1

    # 已完成任务按 owner 计数：只作“有产出证据”的粗略代理，**不是能力验收**
    done_by_owner = collections.Counter(
        str((v or {}).get("owner") or "") for v in tasks.values()
        if (v or {}).get("status") == "done")
    done_by_owner.pop("", None)
    done_by_owner.pop("unknown-agent", None)

    agent_rows = [(d.get("id"), a) for d in (snap.get("departments") or [])
                  for a in (d.get("agents") or [])]
    cards, seen = [], set()
    for snap_dept, a in agent_rows:
        aid = a.get("id")
        if aid in seen:
            continue
        seen.add(aid)
        p = prof.get(aid) or {}
        attrs = p.get("attributes") or {}
        grants = p.get("memory_grants") or {}
        binding = p.get("llm_binding") or a.get("binding") or ""
        tname, trank = model_tier(binding)
        dep = home.get(aid) or snap_dept or ""

        charter = p.get("charter_detail") or ""
        specs = attrs.get("specialties") or []
        tools = p.get("tools") or []
        g_read = grants.get("read") or []
        g_write = grants.get("write") or []
        led = lead_of.get(dep) or ""
        signed = bool(lead_signed.get(dep))
        my_doing = by_seat_doing.get(aid) or []

        # L4 三态：三个独立布尔，禁止合成
        declared = bool(charter or specs or tools)
        ev_n = int(done_by_owner.get(aid, 0))
        entry = (ledger.get("entries") or {}).get(aid) or {}
        verified = bool(entry.get("verified"))

        gaps = []
        if not charter:
            gaps.append({"code": "NO_CHARTER", "t": "职责未登记", "lv": "high",
                         "requires": ["tianfeng", "laotian"],
                         "solve": "田丰归口起草职责，老田裁定后走 org_enroll 补档"})
        if not specs:
            gaps.append({"code": "NO_SPECIALTY", "t": "专长/经历未登记", "lv": "mid",
                         "requires": ["tianfeng"], "solve": "补 attributes.specialties"})
        if not tools:
            gaps.append({"code": "NO_TOOLS", "t": "工具未登记", "lv": "mid",
                         "requires": ["tianfeng"], "solve": "补 tools（登记后仍需实测才算可用）"})
        if not g_read:
            gaps.append({"code": "NO_GRANT", "t": "数据授权未登记", "lv": "mid",
                         "requires": ["tianfeng", "risk", "laotian"],
                         "solve": "按最小权限补 memory_grants，风控核实"})
        if trank == 0:
            gaps.append({"code": "NO_MODEL", "t": "无可用模型", "lv": "high",
                         "requires": ["tianfeng", "laotian"],
                         "solve": "补配模型，或明确“该岗不需要模型、纯脚本执行”"})
        if not signed:
            gaps.append({"code": "LEAD_UNSIGNED", "t": "主官缺正式任命（链断在真源）", "lv": "high",
                         "requires": ["tianfeng", "laotian"],
                         "solve": "补 lead_appointed 签批记录（补丁包已备，等工单号）"})
        if a.get("has_profile") is False:
            gaps.append({"code": "NO_PROFILE", "t": "有名无档", "lv": "high",
                         "requires": ["tianfeng", "laotian"], "solve": "先 --backfill 建档再任命"})
        if not verified:
            gaps.append({"code": "NOT_VERIFIED", "t": "能力未验收（无台账）", "lv": "mid",
                         "requires": ["tianfeng"],
                         "solve": "跑一次真实任务并记入 capability_ledger，才算 verified"})

        cards.append({
            "schema": SCHEMA,
            "seat": {"id": aid, "cn": a.get("cn") or p.get("cn_name") or "",
                     "home": dep, "depts": sorted(depts_of.get(aid) or []),
                     "grade": p.get("grade") or "",
                     "has_profile": a.get("has_profile") is not False,
                     # 员工编号（emp_numbering 发号，只读挂载；空串=尚未发号）
                     "emp_no": (emp_map.get(aid) or {}).get("emp_no") or "",
                     "enroll_no": (emp_map.get(aid) or {}).get("enroll_no") or ""},
            "L1_role": {"text": charter, "declared": bool(charter),
                        "role_key": p.get("role_profile") or p.get("role") or ""},
            "L2_doing": {
                "items": my_doing, "count": len(my_doing),
                "chain": {"dept": dep, "lead": led or None, "seat": aid,
                          "lead_signed": signed},
                "unclaimed_in_dept": len(unclaimed.get(dep) or []),
                "note": "" if my_doing else "无在办 ≠ 停工：只是没派活给它",
            },
            "L3_model": {
                "binding": binding or "none", "tier": tname, "rank": trank,
                "kind": ("local" if binding.startswith("ollama/") else
                         ("none" if not binding or binding == "none" else "cloud")),
                "fits": [tc for tc, (_, need, _d) in TASK_CLASS_MIN.items() if trank >= need],
                "cannot": [tc for tc, (_, need, _d) in TASK_CLASS_MIN.items() if trank < need],
            },
            "L4_capability": {
                "declared": declared,
                "evidence": {"has": ev_n > 0, "n": ev_n,
                             "note": ("%d 个已完成任务记录" % ev_n) if ev_n else "无完成记录"},
                "verified": verified,
                "ledger_missing": ledger.get("missing", False),
                "specialties": specs, "tools": tools,
            },
            "L5_boundary": {
                "read": g_read, "write": g_write,
                "report_to": {
                    # 链语义：本人 → 主官（若本人就是主官则跳过）→ A0（若已是 A0 则跳过）→ human。
                    # 不去重会让主官自己显示成 "D1 → D1 → A0 → human"、A0 显示成 "A0 → A0 → A0 → human"，
                    # 看起来像链条有环，实际是自己指向自己。这里按相邻去重收敛。
                    "chain": _dedup_chain([aid, led or "UNSIGNED_LEAD", "A0", "human"]),
                    "quality": ("ok" if signed else "broken_at_lead"),
                    "note": "" if signed else "本部门主官未正式任命，链条在真源处断开",
                },
            },
            "L6_gaps": gaps,
        })

    cards.sort(key=lambda c: (c["seat"]["home"], c["seat"]["id"]))

    return {
        "schema": SCHEMA,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "rulings": {
            "doing_definition": {
                "in_transit_status": list(DOING_STATUS),
                "excluded_status": list(REVIEW_STATUS),
                "note": "在途只算 doing；review 单列，不计入在途（消灭 19/18 之争）",
            },
            "prefix_map_version": PREFIX_MAP_VERSION,
            "prefix_map": {k: {"dept": v[0], "note": v[1]} for k, v in PREFIX_MAP.items()},
            "task_class_min_model": {k: {"tier": v[0], "rank": v[1], "desc": v[2]}
                                     for k, v in TASK_CLASS_MIN.items()},
            "shell_policy": {
                "primary": "H5 + 服务端识别",
                "mini_program": "暂不做（web-view 拿不到麦克风；个人主体不支持 web-view）",
                "feishu": "仅提醒与跳转，不承担签准",
            },
        },
        "counts": {
            "seats": len(cards), "entries": len(agent_rows),
            "doing": counts["doing"], "review": counts["review"],
            "done": counts["done"], "aborted": counts["aborted"], "other": counts["other"],
            "doing_incl_review": counts["doing"] + counts["review"],
            "unmapped": len(unmapped), "unclaimed_keys": sum(len(v) for v in unclaimed.values()),
            "lead_signed": sum(1 for v in lead_signed.values() if v),
            "lead_total": len(lead_signed),
            "verified": sum(1 for c in cards if c["L4_capability"]["verified"]),
            "ledger_missing": ledger.get("missing", False),
        },
        "doing": doing_list,
        "review": review_list,
        "unmapped": unmapped,
        "unclaimed": unclaimed,
        "cards": cards,
    }


def make_drafts(cards_payload: dict, out_dir) -> dict:
    """按 (部门 × 缺口类型) 聚合草稿。草稿固定 DRAFT_UNSIGNED、writes=[]，
    不含任何派工动作——签批后方可转正式工单。"""
    out_dir = pathlib.Path(out_dir)
    groups = {}
    for c in cards_payload["cards"]:
        for g in c["L6_gaps"]:
            key = (c["seat"]["home"], g["code"])
            groups.setdefault(key, {"dept": c["seat"]["home"], "code": g["code"],
                                    "t": GAP_TITLE.get(g["code"], g["t"]),
                                    "lv": g["lv"], "requires": g["requires"],
                                    "solve": g["solve"], "seats": []})
            groups[key]["seats"].append(c["seat"]["id"])

    out_dir.mkdir(parents=True, exist_ok=True)
    drafts = []
    for (dep, code), v in sorted(groups.items()):
        v["seats"] = sorted(set(v["seats"]))
        draft = {
            "schema": DRAFT_SCHEMA, "status": "DRAFT_UNSIGNED",
            "id": "DRAFT-%s-%s" % (dep.upper(), code), "kind": code, "dept": dep,
            "title": "[草稿] %s：%s（%d 席）" % (dep, v["t"], len(v["seats"])),
            "seats": v["seats"], "level": v["lv"], "requires": v["requires"],
            "solve": v["solve"], "writes": [],
            "note": "草稿不构成工单、不含派工动作；需田丰出号、老田裁定后方可转正式单",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        (out_dir / (draft["id"] + ".json")).write_text(
            json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
        drafts.append(draft)
    return {"dir": str(out_dir), "count": len(drafts),
            "by_kind": {k: sum(1 for d in drafts if d["kind"] == k)
                        for k in sorted({d["kind"] for d in drafts})},
            "drafts": drafts}
