# -*- coding: utf-8 -*-
"""档案索引（WO-LEGION-S41）——「某席位到底有没有档案、绑的是哪个模型」的唯一口径。

为什么要有它：2026-10-21 发现同一份编制被两处**各扫各的**，而两处都只扫
`departments[].agent_profiles`：

  * `engine/org_audit.py` 因此把 A0（田丰）报成 P1「主官无档案」——
    事实上他的档案就在 org.json 顶层 `chief` 块里；
  * `legion_command/serve.resolve_binding()` 因此查不到 A0A 的 `glm/glm-5.3-flash`，
    回落到默认 9b —— 档案写的是云端强模型，会议层用的却是本地小模型，
    **档案与能力不符，且现场看不出**（S37 已立判例：编制造出的事实，展示层必须认）。

档案可能登记在四处，优先级从高到低：

  1. `departments[].agent_profiles`（现行标准位置，org_enroll 写这里）
  2. `org.json` 顶层 `chief` 块（参谋长 A0，历史结构）
  3. `registry/team_*.json` 的 `agents[]`（各团队注册表，含 A0A）
  4. `registry/profile_seats.json` 的 `seats[]`（**不占编 profile 席位**，如治理席 ZHAOYUN；
     2026-10-30 WO-HZ-ZY-PROFILE-20261030 追加：治理席不进部门编制表，但其正式绑定
     必须可被 resolve_binding 读到——否则「档案写着 DSH 载体、会议层回落 9b」现场看不出，
     与 S37 判例「编制造出的事实，展示层必须认」同源）

只读，不写任何真源。
"""

from __future__ import annotations

import json
from pathlib import Path

SRC_DEPT = "org.departments"
SRC_CHIEF = "org.chief"
SRC_TEAM = "team_file"
SRC_PROFILE = "profile_seats"


def profile_index(root: Path) -> dict:
    """返回 {agent_id: {"llm_binding", "source", "source_detail", "cn_name"}}。

    先到先得：部门档案表 > 顶层 chief > team_*.json。**不是覆盖写**——同一 id 出现
    在多处时，以更权威的位置为准，并保留来源便于排障。
    """
    out: dict[str, dict] = {}

    def put(aid, binding, source, detail, cn=""):
        aid = str(aid or "")
        if not aid or aid in out:
            return
        out[aid] = {
            "llm_binding": str(binding or ""),
            "source": source,
            "source_detail": detail,
            "cn_name": str(cn or ""),
        }

    org: dict = {}
    p = root / "registry" / "org.json"
    try:
        org = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        org = {}

    for d in org.get("departments") or []:
        dept = str(d.get("dept") or "")
        for prof in d.get("agent_profiles") or []:
            persona = prof.get("persona") or {}
            put(
                prof.get("agent_id"),
                prof.get("llm_binding"),
                SRC_DEPT,
                "dept:%s" % dept,
                cn=prof.get("cn_name") or persona.get("name") or "",
            )

    chief = org.get("chief") or {}
    if isinstance(chief, dict):
        put(
            chief.get("agent_id"),
            chief.get("llm_binding"),
            SRC_CHIEF,
            "chief",
            cn=chief.get("name") or "",
        )

    reg = root / "registry"
    if reg.exists():
        for f in sorted(reg.glob("team_*.json")):
            try:
                t = json.loads(f.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            for a in t.get("agents") or []:
                put(
                    a.get("agent_id"),
                    a.get("llm_binding"),
                    SRC_TEAM,
                    "registry/%s" % f.name,
                    cn=a.get("name") or "",
                )

    # 2026-10-30 WO-HZ-ZY-PROFILE-20261030：不占编 profile 席位（治理席等）。
    # 放在部门/chief/team 之后（先到先得，不覆盖正式档案）：治理席在 org.json
    # 编制表中无档案，但其正式绑定（如 ZHAOYUN=deepseek/deepseek-v4-pro）必须可解析。
    if reg.exists():
        _ps = reg / "profile_seats.json"
        try:
            _data = json.loads(_ps.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            _data = {}
        _seats = _data.get("seats") if isinstance(_data, dict) else None
        if isinstance(_seats, dict):  # 容错 dict 写法（agent_id -> 档案），键补 agent_id
            _seats = [
                (dict(v, agent_id=v.get("agent_id") or k)
                 if isinstance(v, dict) else None)
                for k, v in _seats.items()
            ]
        for _seat in _seats or []:
            if not isinstance(_seat, dict):
                continue
            put(
                _seat.get("agent_id"),
                _seat.get("llm_binding"),
                SRC_PROFILE,
                "registry/profile_seats.json",
                cn=_seat.get("cn_name") or _seat.get("name") or "",
            )
    return out


def fallbacks_index(root: Path) -> dict[str, list[str]]:
    """返回 {agent_id: [降级绑定串, ...]}——席位级 fallback 链的唯一读取口径。

    真源只有一个：`registry/org.json` 的 `departments[].agent_profiles[].llm_fallback`
    （`llm_fallback` 是**有序**列表，首项优先；与 `profile_index` 的三处登记不同，
    降级链只写在部门档案表里，顶层 chief 与 team_*.json 均无此字段）。

    归一规则（宁可少一条，不编造一条）：
      · 只收**字符串**项；非串（dict/None/数字/嵌套 list）与空白串一律过滤——
        `{"model": ...}` 这类写法不猜语义，避免把 dict 序列化后当成 binding 发出去；
      · 保序去重：同一 binding 只保留首次出现的位置（降级链顺序即优先级）；
      · 缺省 `[]`：有档案但没配 fallback 的席位也必须出现在索引里（空链≠无档案）。

    同 key 先到先得（与 `profile_index` 同策略）：同一 agent_id 在多部门重复登记时，
    以**先出现**的档案为准，不覆盖（后来者可能是复制粘贴残留，不应改写降级顺序）。

    只读，不写任何真源；坏 JSON/缺文件降级为 `{}`（与 profile_index 同容错口径）。
    """
    org: dict = {}
    p = root / "registry" / "org.json"
    try:
        org = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        org = {}
    if not isinstance(org, dict):
        return {}

    out: dict[str, list[str]] = {}
    for d in org.get("departments") or []:
        if not isinstance(d, dict):
            continue
        for prof in d.get("agent_profiles") or []:
            if not isinstance(prof, dict):
                continue
            aid = str(prof.get("agent_id") or "")
            if not aid or aid in out:
                continue
            raw = prof.get("llm_fallback")
            if isinstance(raw, str):  # 容错单串写法（真源标准是 list）
                raw = [raw]
            if not isinstance(raw, (list, tuple)):
                out[aid] = []
                continue
            chain: list[str] = []
            for item in raw:
                if not isinstance(item, str):
                    continue
                token = item.strip()
                if token and token not in chain:
                    chain.append(token)
            out[aid] = chain

    # 2026-10-30 WO-HZ-ZY-PROFILE-20261030：不占编 profile 席位也可声明 llm_fallback。
    # 先到先得：部门档案已登记的席位不覆盖（部门表仍是主要真源）。
    reg = root / "registry"
    if reg.exists():
        _ps = reg / "profile_seats.json"
        try:
            _data = json.loads(_ps.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            _data = {}
        _seats = _data.get("seats") if isinstance(_data, dict) else None
        if isinstance(_seats, dict):  # 容错 dict 写法（agent_id -> 档案），键补 agent_id
            _seats = [
                (dict(v, agent_id=v.get("agent_id") or k)
                 if isinstance(v, dict) else None)
                for k, v in _seats.items()
            ]
        for _seat in _seats or []:
            if not isinstance(_seat, dict):
                continue
            _aid = str(_seat.get("agent_id") or "")
            if not _aid or _aid in out:
                continue
            _raw = _seat.get("llm_fallback")
            if isinstance(_raw, str):
                _raw = [_raw]
            _chain: list[str] = []
            if isinstance(_raw, (list, tuple)):
                for _item in _raw:
                    if not isinstance(_item, str):
                        continue
                    _tok = _item.strip()
                    if _tok and _tok not in _chain:
                        _chain.append(_tok)
            out[_aid] = _chain
    return out


def _is_project_dept(d: dict) -> bool:
    return bool(
        d.get("type") == "project" or str(d.get("dept") or "").startswith("proj_")
    )


def matrix_assignments(org: dict) -> dict:
    """P2：{项目部门: {agent_id: matrix_assignment}}（项目角色已并入职能部主档）。"""
    out: dict[str, dict] = {}
    for d in org.get("departments") or []:
        if _is_project_dept(d):
            continue
        profs = d.get("agent_profiles") or {}
        items = profs.values() if isinstance(profs, dict) else profs
        for p in items or []:
            aid = str(p.get("agent_id") or "")
            if not aid:
                continue
            for m in p.get("matrix_assignments") or []:
                dep = str(m.get("dept") or "")
                if dep:
                    out.setdefault(dep, {})[aid] = m
    return out


def dept_profile_ids(org: dict, dept: str) -> set:
    """某部门"可视为有档案"的席位：本部门档案表 ∪ 主档 matrix_assignments。"""
    ids: set[str] = set()
    for d in org.get("departments") or []:
        if str(d.get("dept")) != dept:
            continue
        profs = d.get("agent_profiles") or {}
        items = profs.values() if isinstance(profs, dict) else profs
        ids.update(str(p.get("agent_id")) for p in items or [] if p.get("agent_id"))
    ids.update(matrix_assignments(org).get(dept, {}).keys())
    return ids


def source_paths(root: Path) -> list:
    """参与索引的文件清单（供缓存失效判断：任何一个变了都要重算）。"""
    reg = root / "registry"
    paths = [reg / "org.json"]
    if reg.exists():
        paths.extend(sorted(reg.glob("team_*.json")))
        # 2026-10-30：不占编 profile 席位档案也参与索引（改档需触发绑定缓存失效）
        paths.append(reg / "profile_seats.json")
    return paths


def stamp(root: Path) -> tuple:
    """索引指纹：所有源文件的 (路径, mtime, size)。mtime 单独不够——同秒内改写会漏。"""
    out = []
    for p in source_paths(root):
        try:
            st = p.stat()
            out.append((str(p), st.st_mtime, st.st_size))
        except OSError:
            out.append((str(p), None, None))
    return tuple(out)
