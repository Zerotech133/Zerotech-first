# -*- coding: utf-8 -*-
"""档案索引（WO-LEGION-S41）——「某席位到底有没有档案、绑的是哪个模型」的唯一口径。

为什么要有它：2026-10-21 发现同一份编制被两处**各扫各的**，而两处都只扫
`departments[].agent_profiles`：

  * `engine/org_audit.py` 因此把 A0（田丰）报成 P1「主官无档案」——
    事实上他的档案就在 org.json 顶层 `chief` 块里；
  * `legion_command/serve.resolve_binding()` 因此查不到 A0A 的 `glm/glm-5.3-flash`，
    回落到默认 9b —— 档案写的是云端强模型，会议层用的却是本地小模型，
    **档案与能力不符，且现场看不出**（S37 已立判例：编制造出的事实，展示层必须认）。

档案可能登记在三处，优先级从高到低：

  1. `departments[].agent_profiles`（现行标准位置，org_enroll 写这里）
  2. `org.json` 顶层 `chief` 块（参谋长 A0，历史结构）
  3. `registry/team_*.json` 的 `agents[]`（各团队注册表，含 A0A）

只读，不写任何真源。
"""

from __future__ import annotations

import json
from pathlib import Path

SRC_DEPT = "org.departments"
SRC_CHIEF = "org.chief"
SRC_TEAM = "team_file"


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
        out[aid] = {"llm_binding": str(binding or ""), "source": source,
                    "source_detail": detail, "cn_name": str(cn or "")}

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
            put(prof.get("agent_id"), prof.get("llm_binding"), SRC_DEPT,
                "dept:%s" % dept,
                cn=prof.get("cn_name") or persona.get("name") or "")

    chief = org.get("chief") or {}
    if isinstance(chief, dict):
        put(chief.get("agent_id"), chief.get("llm_binding"), SRC_CHIEF, "chief",
            cn=chief.get("name") or "")

    reg = root / "registry"
    if reg.exists():
        for f in sorted(reg.glob("team_*.json")):
            try:
                t = json.loads(f.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            for a in t.get("agents") or []:
                put(a.get("agent_id"), a.get("llm_binding"), SRC_TEAM,
                    "registry/%s" % f.name, cn=a.get("name") or "")
    return out


def source_paths(root: Path) -> list:
    """参与索引的文件清单（供缓存失效判断：任何一个变了都要重算）。"""
    reg = root / "registry"
    paths = [reg / "org.json"]
    if reg.exists():
        paths.extend(sorted(reg.glob("team_*.json")))
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
