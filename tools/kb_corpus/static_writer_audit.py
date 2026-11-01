# -*- coding: utf-8 -*-
"""静态产物写入脚本审计（wave26 支线 CR）：防「并发会话覆盖门户静态 JSON」事故再发。

背景
----
并发会话运行 `learning_cards.py` 曾整文件覆盖 `portal/static/kb_learning_cards.json`（70 张卡被覆盖，
已修）。需系统性审计所有**写门户静态 JSON** 的脚本：写入目标 / 是否备份 / 是否合并旧数据 /
是否原子写 / 是否被页面消费 → 风险评级 → 给出最小修复建议（只出清单，不改代码）。

规则（启发式，供人工复核）
------------------------
- 候选：tools/ 与 portal/ 下的 .py（排除 .worktrees/__pycache__/tests），文本含写盘操作
  （`.write_text(` / `json.dump(` / `open(...,'w')` / `shutil.copy` / `os.replace`）
  **且** 引用了 portal/static 在册的某个 .json 文件名（直接字面量或 OUT_/PATH 常量赋值）；
- 备份：backup/.bak/copy2/shutil.copy 迹象；
- 合并旧数据：读取旧文件（read_text/json.load）后再写，或出现 merge/existing/old/保留 等合并语义；
- 原子写：os.replace/.replace(/tempfile/.tmp/atomic；
- 人工数据（curated）：kb_learning_cards.json / kb_units_all.json / kb_qa_samples.json /
  kb_qa_questions*.json / report_panel.json 等人工编辑或长期资产 → 覆盖不可从那台机器重算；
- 页面消费：portal/ 下 .html/.js 出现该静态文件名；
- 风险：高 = 人工数据 + 无备份 + 无原子写（直接整文件覆盖）；中 = 生成数据可重算，或缺备份/原子写之一；
  低 = 只读引用 / 有备份+原子写+合并。

输出：research/reports/static_writer_audit_20261101.{json,md}
运行：C:\\项目\\py312\\python.exe -X utf8 tools/kb_corpus/static_writer_audit.py audit
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PORTAL = ROOT / "portal"
STATIC = PORTAL / "static"
OUT = ROOT / "research" / "reports"
OUT_JSON = OUT / "static_writer_audit_v2_20261101.json"
OUT_MD = OUT / "static_writer_audit_v2_20261101.md"
V1_JSON = OUT / "static_writer_audit_20261101.json"
SCHEMA_VERSION = "static_writer_audit.v2"

# 扫描范围（相对 ROOT）
SCAN_DIRS = ("tools", "portal")
EXCLUDE_PARTS = {".worktrees", "__pycache__", "node_modules", ".git", "tmp", "tests"}

# 人工编辑/长期资产（覆盖后难以重算 → 高风险判定关键）
CURATED_TARGETS = {
    "kb_learning_cards.json", "kb_units_all.json", "kb_qa_samples.json",
    "report_panel.json", "selection_daily.json",
}

WRITE_PATTERNS = (".write_text(", "json.dump(", "json.dumps(", "open(", "shutil.copy",
                  "os.replace", "dump(")
WRITE_OP_RE = re.compile(
    r"(?P<recv>[A-Za-z_][\w\.]*?)\.write_text\(|"
    r"json\.dump\(|open\([^)]*['\"][wa]['\"]|shutil\.copy|os\.replace")
BACKUP_RE = re.compile(r"backup|\.bak|copy2|shutil\.copy|backups", re.I)
MERGE_RE = re.compile(r"merge|existing|old_data|保留旧|合并|setdefault|update\(|json\.load", re.I)
# v2：辅助函数写盘识别（safe_write_json / atomic_write / write_json_safe / backup_write 等）
SAFE_HELPER_NAMES = ("safe_write", "write_safe", "safe_json", "atomic_write",
                     "json_safe", "safe_dump", "backup_write")
CALL_RE = re.compile(r"\b(?P<fn>[A-Za-z_]\w*)\s*\(")
ATOMIC_RE = re.compile(r"os\.replace|\.replace\(|tempfile|NamedTemporaryFile|\.tmp|atomic", re.I)
# 参数默认值别名（def f(base: Path | str = BASE)）与简单变量别名（portal_path = BASE）
PARAM_ALIAS_RE = re.compile(r"(?P<arg>[A-Za-z_]\w*)\s*:\s*[^=,)\n]+=\s*(?P<src>[A-Za-z_]\w*)")
VAR_ALIAS_RE = re.compile(r"(?m)^\s*(?P<dst>[A-Za-z_]\w*)\s*=\s*(?P<src>[A-Za-z_]\w*)\s*$")
ASSIGN_RE = re.compile(
    r"(?m)^\s*(?P<var>[A-Za-z_][\w]*)\s*=\s*[^\n]*?"
    r"(?P<name>[A-Za-z0-9_\-]+\.json)")
JSON_RE = re.compile(r"[\"']([A-Za-z0-9_\-]+\.json)[\"']")
FIX_HIGH = [
    "写前备份：写前 copy2 到 research/reports/backups/<name>.<时间戳>.bak（保留最近 N 份）；",
    "读-合并-写：先读旧文件，按 id/主键合并新数据（新增/更新而不是整文件替换），"
    "保留旧记录与未知字段；",
    "原子写：写临时文件（同目录 .tmp）后 os.replace 到目标，避免半截文件；",
    "变更确认：写前记录 sha256/条数，写后校验条数与差异 count；提供 --dry-run / 二次确认；",
    "串行约束：加锁文件或检查其他会话是否在跑，禁止并发写同一静态资产。",
]


# --------------------------------------------------------------------------- #
# 扫描
# --------------------------------------------------------------------------- #
def static_json_names(static_dir: Path | str = STATIC) -> list[str]:
    return sorted(p.name for p in Path(static_dir).glob("*.json"))


def consumer_map(static_names: list[str], portal_dir: Path | str = PORTAL,
                 root: Path | str = ROOT) -> dict[str, list[str]]:
    """静态文件名 → 引用它的页面（portal 下 .html/.js 相对路径）。"""
    mapping: dict[str, list[str]] = {n: [] for n in static_names}
    for p in Path(portal_dir).rglob("*"):
        if p.suffix.lower() not in (".html", ".js"):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for name in static_names:
            if name in text:
                mapping[name].append(str(p.relative_to(Path(root))))
    return mapping


def _write_evidence(text: str, targets: set[str], limit: int = 3) -> list[dict]:
    """收集写盘证据行（行号 + 片段），并标出目标变量。"""
    lines = text.splitlines()
    evidence: list[dict] = []
    for i, line in enumerate(lines, 1):
        if not WRITE_OP_RE.search(line):
            continue
        hit = [t for t in targets if t in line]
        evidence.append({"line": i, "snippet": line.strip()[:160],
                         "targets_in_line": hit})
        if len(evidence) >= limit:
            break
    return evidence


def _helper_body(text: str, name: str) -> str:
    """取辅助函数定义体（用于识别 backup/atomic/merge 保护）；外部导入返回空串。"""
    m = re.search(rf"(?m)^\s*def\s+{re.escape(name)}\s*\(", text)
    if not m:
        return ""
    tail = text[m.start():]
    nxt = re.search(r"(?m)^\s*(?:def|class)\s+\w+", tail[1:])
    body = tail[:nxt.start() + 1] if nxt else tail
    return body[:6000]


def audit_script(path: Path, static_names: list[str], statics: dict[str, list[str]],
                 root: Path | str = ROOT) -> dict | None:
    """审计单个脚本；返回记录（无写盘迹象/未引用静态文件 → None）。"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if not any(p in text for p in WRITE_PATTERNS) and \
            not any(n in text for n in static_names):
        return None
    # 变量 → 静态文件名映射（OUT_JSON = ... / "kb_x.json"）
    var_target: dict[str, str] = {}
    for m in ASSIGN_RE.finditer(text):
        if m.group("name") in static_names:
            var_target[m.group("var")] = m.group("name")
    direct = {m.group(1) for m in JSON_RE.finditer(text)} & set(static_names)
    # 别名闭包：portal_path = OUT_PORTAL / def f(base=BASE) → 把别名变量解析到静态目标
    raw_alias: dict[str, str] = {}
    for m in PARAM_ALIAS_RE.finditer(text):
        raw_alias[m.group("arg")] = m.group("src")
    for m in VAR_ALIAS_RE.finditer(text):
        raw_alias.setdefault(m.group("dst"), m.group("src"))

    def root_var(v: str) -> str:
        seen: set[str] = set()
        while v in raw_alias and v not in seen:
            seen.add(v)
            v = raw_alias[v]
        return v

    writable_vars = {v for v in var_target if root_var(v) == v}
    writable_vars |= {v for v in list(raw_alias) + list(var_target)
                      if root_var(v) in var_target}
    write_targets: set[str] = set()
    evidence: list[dict] = []
    lines = text.splitlines()
    for i, line in enumerate(lines, 1):
        if not WRITE_OP_RE.search(line):
            continue
        hit = [t for t in direct if t in line]
        for var in writable_vars:
            if re.search(rf"\b{re.escape(var)}\b", line):
                hit.append(var_target[root_var(var)])
                if var != root_var(var):
                    hit = list(dict.fromkeys(hit))
        if hit:
            write_targets.update(hit)
            evidence.append({"line": i, "snippet": line.strip()[:180],
                             "targets_in_line": sorted(set(hit))})
    # v2：辅助函数写盘识别（safe_write_json(TARGET, ...) / atomic_write(PATH, ...) 等）
    helper_calls: list[dict] = []
    helper_names: set[str] = set()
    helper_protection = {"backup": False, "merge": False, "atomic": False}
    for i, line in enumerate(lines, 1):
        for m in CALL_RE.finditer(line):
            fn = m.group("fn")
            if not any(k in fn.lower() for k in SAFE_HELPER_NAMES):
                continue
            after = line[m.end():]
            args = after.split(",")[0].strip().rstrip(")")
            hit: list[str] = []
            for var in writable_vars:
                if re.search(rf"\b{re.escape(var)}\b", args):
                    hit.append(var_target[root_var(var)])
            hit += [t for t in direct if t in args]
            lit = JSON_RE.search(args)
            if lit and lit.group(1) in static_names:
                hit.append(lit.group(1))
            hit = sorted(set(hit))
            if not hit:
                continue
            write_targets.update(hit)
            helper_names.add(fn)
            body = _helper_body(text, fn)
            lower = fn.lower()
            prot = {"backup": bool(BACKUP_RE.search(body)) or "safe" in lower or "backup" in lower,
                    "atomic": bool(ATOMIC_RE.search(body)) or "safe" in lower or "atomic" in lower,
                    "merge": bool(re.search(r"merge_fn|counts_fn", line))
                             or bool(re.search(r"def _?merge|merge_fn", body))}
            helper_protection = {k: helper_protection[k] or prot[k] for k in prot}
            helper_calls.append({"line": i, "helper": fn,
                                 "snippet": line.strip()[:180],
                                 "targets_in_line": hit})
    if not write_targets:
        # 引用了静态文件但没有可定位的写入目标 → 只读引用
        refs = sorted(direct)
        if not refs:
            return None
        return {"script": str(path.relative_to(Path(root))), "targets": refs,
                "read_only": True, "write_evidence": [],
                "helper_based": False, "helper_calls": [], "helper_names": [],
                "protection": {},
                "flags": {"backup": bool(BACKUP_RE.search(text)),
                          "merge_existing": bool(MERGE_RE.search(text)),
                          "atomic_write": bool(ATOMIC_RE.search(text)),
                          "read_only": True},
                "data_kind": "read_only",
                "risk": "low", "risk_reasons": ["未发现可定位的写盘目标（只读引用）"],
                "fix_suggestions": []}
    is_merge = bool(MERGE_RE.search(text)) and bool(
        re.search(r"read_text|json\.load", text))
    flags = {"backup": bool(BACKUP_RE.search(text)) or helper_protection["backup"],
             "merge_existing": is_merge or helper_protection["merge"],
             "atomic_write": bool(ATOMIC_RE.search(text)) or helper_protection["atomic"],
             "read_only": False}
    helper_based = bool(helper_names)
    protection = {k: bool(flags[k]) for k in ("backup", "merge_existing", "atomic_write")}
    protected_all = all(protection.values())
    curated = sorted(t for t in write_targets if t in CURATED_TARGETS)
    generated = sorted(t for t in write_targets if t not in CURATED_TARGETS)
    data_kind = "curated" if curated else "generated"
    helper_txt = ("（safe_write 辅助函数：" + "、".join(sorted(helper_names)) + "）"
                  if helper_based else "")
    reasons: list[str] = []
    risk = "low"
    if curated:
        if not flags["backup"] and not flags["atomic_write"] and not flags["merge_existing"]:
            risk = "high"
            reasons += ["写人工/长期资产（" + "、".join(curated) + "）",
                        "无备份/无原子写/无合并 → 直接整文件覆盖风险"]
        elif protected_all:
            risk = "low"
            reasons += ["写人工资产（" + "、".join(curated) + "）但三件套齐全" + helper_txt]
        else:
            risk = "medium"
            missing = [k for k, label in (("backup", "备份"), ("atomic_write", "原子写"),
                                          ("merge_existing", "合并旧数据"))
                       if not flags[k]]
            reasons += ["写人工资产（" + "、".join(curated) + "）",
                        "缺" + "、".join(missing) + "（有部分保护）"]
    elif protected_all:
        risk = "low"
        reasons += ["生成数据 + 三件套齐全" + helper_txt]
    elif not flags["backup"] and not flags["atomic_write"]:
        risk = "medium"
        reasons += ["写生成数据（可重算）但无备份/原子写"]
    else:
        risk = "low"
        reasons += ["生成数据可重算，且有备份/原子写保护"]
    if flags["read_only"]:
        risk = "low"
    return {"script": str(path.relative_to(Path(root))), "targets": sorted(write_targets),
            "curated_targets": curated, "generated_targets": generated,
            "read_only": False, "write_evidence": (helper_calls + evidence)[:5],
            "helper_based": helper_based, "helper_calls": helper_calls,
            "helper_names": sorted(helper_names), "protection": protection,
            "flags": flags, "data_kind": data_kind, "risk": risk,
            "risk_reasons": reasons,
            "fix_suggestions": FIX_HIGH if risk == "high" else []}


def scan(root: Path | str = ROOT, static_dir: Path | str = STATIC,
         scan_dirs=SCAN_DIRS) -> list[dict]:
    root = Path(root)
    static_names = static_json_names(static_dir)
    statics = consumer_map(static_names, Path(root) / "portal", root)
    records: list[dict] = []
    for d in scan_dirs:
        base = root / d
        if not base.exists():
            continue
        for p in base.rglob("*.py"):
            if any(part in EXCLUDE_PARTS for part in p.relative_to(root).parts):
                continue
            rec = audit_script(p, static_names, statics, root)
            if rec is not None:
                rec["consumers"] = sorted({c for t in rec["targets"] for c in statics.get(t, [])})
                records.append(rec)
    return records


# --------------------------------------------------------------------------- #
# 报告
# --------------------------------------------------------------------------- #
RISK_ORDER = {"high": 3, "medium": 2, "low": 1}


def load_v1_report(path: Path | str | None = None) -> dict:
    """读 wave26 v1 审计报告（用于 v1→v2 复审对比；缺失则返回空）。"""
    p = Path(path) if path is not None else V1_JSON
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def review_vs_v1(writers: list[dict], v1: dict) -> dict:
    """对比 v1/v2 风险：输出降级/升级清单与 v1/v2 汇总。"""
    v1_risks = {w.get("script"): w.get("risk") for w in (v1.get("writers") or [])
                if isinstance(w, dict) and w.get("script")}
    changes: list[dict] = []
    for w in writers:
        old = v1_risks.get(w["script"])
        if old is None or old == w["risk"]:
            continue
        direction = "downgrade" if RISK_ORDER.get(w["risk"], 0) < RISK_ORDER.get(old, 0) else "upgrade"
        changes.append({"script": w["script"], "targets": w["targets"],
                        "v1_risk": old, "v2_risk": w["risk"], "direction": direction,
                        "helper_based": bool(w.get("helper_based")),
                        "helper_names": w.get("helper_names") or [],
                        "reasons": w.get("risk_reasons") or []})
    changes.sort(key=lambda c: (c["direction"] != "downgrade", c["script"]))
    v1_summary = v1.get("summary") or {}
    return {
        "v1_path": str(V1_JSON),
        "v1_available": bool(v1),
        "v1_summary": {k: v1_summary.get(k) for k in
                       ("n_writers", "high", "medium", "low", "high_targets")},
        "changes": changes,
        "downgraded": [c for c in changes if c["direction"] == "downgrade"],
        "upgraded": [c for c in changes if c["direction"] == "upgrade"],
        "unchanged_count": len(v1_risks) - len({c["script"] for c in changes}),
        "v2_high_scripts": sorted(w["script"] for w in writers if w["risk"] == "high"),
    }


def build_report(records: list[dict], root: Path | str = ROOT) -> dict:
    root = Path(root)
    static_names = static_json_names(Path(root) / "portal" / "static")
    statics = consumer_map(static_names, Path(root) / "portal", root)
    writers = [r for r in records if not r.get("read_only")]
    read_only = [r for r in records if r.get("read_only")]
    by_risk = {k: [r for r in writers if r["risk"] == k] for k in ("high", "medium", "low")}
    targets: dict[str, dict] = {}
    for r in writers:
        for t in r["targets"]:
            entry = targets.setdefault(t, {"target": t, "consumers": sorted(statics.get(t, [])),
                                           "writers": [], "max_risk": "low",
                                           "curated": t in CURATED_TARGETS})
            entry["writers"].append(r["script"])
            if RISK_ORDER[r["risk"]] > RISK_ORDER[entry["max_risk"]]:
                entry["max_risk"] = r["risk"]
    high = [{"script": r["script"], "targets": r["targets"],
             "missing_controls": [k for k in ("backup", "merge_existing", "atomic_write")
                                  if not r["flags"].get(k)],
             "risk_reasons": r["risk_reasons"],
             "minimal_fixes": r["fix_suggestions"],
             "write_evidence": r["write_evidence"][:2]}
            for r in by_risk["high"]]
    review = review_vs_v1(writers, load_v1_report())
    v1_summary = review.get("v1_summary") or {}
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "scope": {"dirs": list(SCAN_DIRS), "static_files": len(static_names),
                  "scanned_root": str(root)},
        "policy": {
            "high": "人工/长期资产 + 无备份 + 无原子写 + 无合并旧数据（直接整文件覆盖）",
            "medium": "人工资产缺备份/原子写/合并之一（部分保护）；或生成数据无备份/原子写",
            "low": "只读引用；或生成数据+三件套；或人工资产三件套齐全（含 safe_write 辅助函数识别）",
            "curated_targets": sorted(CURATED_TARGETS),
            "heuristic": "基于源码文本/变量赋值/辅助函数调用（v2）的启发式扫描，需人工复核（可能漏报/误报）",
            "v2_new": ["识别 safe_write_json/atomic_write/write_json_safe 等辅助函数调用写盘",
                       "识别辅助函数定义体内的备份/tmp+os.replace/merge_fn 保护",
                       "识别 read-merge-write 模式并可据此降级（有合并但缺备份/原子写→中风险）",
                       "v1（wave26 报告）→ v2 风险对比（降级确认清单）"],
        },
        "summary": {"n_records": len(records), "n_writers": len(writers),
                    "n_read_only": len(read_only),
                    "high": len(by_risk["high"]), "medium": len(by_risk["medium"]),
                    "low": len(by_risk["low"]),
                    "helper_based_writers": sum(1 for r in writers if r.get("helper_based")),
                    "targets_total": len(targets),
                    "high_targets": sorted({t for r in by_risk["high"] for t in r["targets"]}),
                    "v1_n_writers": v1_summary.get("n_writers"),
                    "v1_high": v1_summary.get("high"),
                    "v1_medium": v1_summary.get("medium"),
                    "v1_low": v1_summary.get("low"),
                    "downgraded": len(review.get("downgraded") or []),
                    "upgraded": len(review.get("upgraded") or [])},
        "writers": sorted(writers, key=lambda r: ({"high": 0, "medium": 1, "low": 2}[r["risk"]],
                                                  r["script"])),
        "read_only_references": sorted(read_only, key=lambda r: r["script"]),
        "targets": sorted(targets.values(), key=lambda x: ({"high": 0, "medium": 1, "low": 2}[x["max_risk"]],
                                                           x["target"])),
        "high_risk": high,
        "review_v1_v2": review,
    }


def _pct_risk(r: str) -> str:
    return {"high": "🔴 高", "medium": "🟡 中", "low": "🟢 低"}.get(r, r)


def render_md(rep: dict) -> str:
    s = rep["summary"]
    L = ["# 静态产物写入脚本审计（门户静态 JSON 覆盖风险）", ""]
    L.append(f"- 生成：{rep['generated_at']}；扫描 {rep['scope']['dirs']}（{rep['scope']['static_files']} 个静态 JSON）")
    L.append(f"- 结果：**写盘脚本 {s['n_writers']} 个**（高风险 **{s['high']}** / 中 {s['medium']} / 低 {s['low']}）；"
             f"只读引用 {s['n_read_only']} 个；涉及静态目标 {s['targets_total']} 个")
    L.append(f"- 高危目标：{('、'.join(s['high_targets']) if s['high_targets'] else '无')}")
    L.append("")
    L.append("## 评级规则（启发式，需人工复核）")
    L.append("")
    L.append(f"- **高**：{rep['policy']['high']}")
    L.append(f"- **中**：{rep['policy']['medium']}")
    L.append(f"- **低**：{rep['policy']['low']}")
    L.append(f"- 人工资产名单：{'、'.join(rep['policy']['curated_targets'])}")
    L.append("")
    rv = rep.get("review_v1_v2") or {}
    L.append("## v1 → v2 复审对比（规则升级：辅助函数/read-merge-write 识别）")
    L.append("")
    if rv.get("v1_available"):
        v1s = rv.get("v1_summary") or {}
        L.append(f"- v1（wave26 报告）：写盘 {v1s.get('n_writers')} 个"
                 f"（高 {v1s.get('high')} / 中 {v1s.get('medium')} / 低 {v1s.get('low')}）")
        L.append(f"- v2（本次）：写盘 {s['n_writers']} 个（高 {s['high']} / 中 {s['medium']} / "
                 f"低 {s['low']}）；辅助函数写入识别 {s['helper_based_writers']} 个；"
                 f"降级 {s['downgraded']}、升级 {s['upgraded']}")
        if rv.get("downgraded"):
            L.append("")
            L.append("| 脚本 | 目标 | v1 | v2 | 依据 |")
            L.append("|---|---|---|---|---|")
            for c in rv["downgraded"]:
                L.append(f"| `{c['script']}` | {'、'.join(c['targets'])} | "
                         f"{_pct_risk(c['v1_risk'])} | {_pct_risk(c['v2_risk'])} | "
                         f"{'；'.join(c['reasons'][:2])} |")
            L.append("")
            L.append("**降级确认**：" + "、".join(f"`{c['script']}`" for c in rv["downgraded"]))
        if rv.get("upgraded"):
            L.append("")
            L.append("**升级（v2 更严）**：" + "、".join(
                f"`{c['script']}`（{c['v1_risk']}→{c['v2_risk']}）" for c in rv["upgraded"]))
        if rv.get("v2_high_scripts"):
            L.append("")
            L.append("**剩余高风险（v2）**：" + "、".join(
                f"`{x}`" for x in rv["v2_high_scripts"]))
    else:
        L.append("- v1 报告不可用，无法对比（仅输出 v2 结果）。")
    L.append("")
    L.append("## 高风险清单（优先整改）")
    L.append("")
    if rep["high_risk"]:
        L.append("| 脚本 | 目标 | 缺控制 | 原因 |")
        L.append("|---|---|---|---|")
        for h in rep["high_risk"]:
            L.append(f"| `{h['script']}` | {'、'.join(h['targets'])} | "
                     f"{'、'.join(h['missing_controls'])} | {'；'.join(h['risk_reasons'][:2])} |")
        L.append("")
        L.append("### 最小修复建议（不直接改代码）")
        L.append("")
        for h in rep["high_risk"]:
            L.append(f"**`{h['script']}`**（{', '.join(h['targets'])}）")
            for fix in h["minimal_fixes"]:
                L.append(f"- {fix}")
            if h["write_evidence"]:
                ev = h["write_evidence"][0]
                L.append(f"- 证据：L{ev['line']} `{ev['snippet'][:120]}`")
            L.append("")
    else:
        L.append("- 无高风险脚本 ✅")
        L.append("")
    L.append("## 全部写盘脚本清单")
    L.append("")
    L.append("| 风险 | 脚本 | 目标 | 备份 | 合并旧数据 | 原子写 | 辅助函数 | 页面消费 |")
    L.append("|---|---|---|---|---|---|---|---|")
    for r in rep["writers"]:
        f = r["flags"]
        cons = "、".join(r.get("consumers") or []) or "—"
        helper = "、".join(r.get("helper_names") or []) or "—"
        L.append(f"| {_pct_risk(r['risk'])} | `{r['script']}` | {'、'.join(r['targets'])} | "
                 f"{'✅' if f['backup'] else '❌'} | {'✅' if f['merge_existing'] else '❌'} | "
                 f"{'✅' if f['atomic_write'] else '❌'} | {helper} | {cons} |")
    L.append("")
    if rep["read_only_references"]:
        L.append("## 只读引用（低风险，仅提示）")
        L.append("")
        for r in rep["read_only_references"]:
            L.append(f"- `{r['script']}` → {'、'.join(r['targets'])}")
        L.append("")
    L.append("## 目标视角（谁在写、谁在读）")
    L.append("")
    L.append("| 静态目标 | 风险 | 写脚本 | 页面消费 |")
    L.append("|---|---|---|---|")
    for t in rep["targets"]:
        L.append(f"| {t['target']} | {_pct_risk(t['max_risk'])} | "
                 f"{'、'.join('`'+w+'`' for w in t['writers'])} | "
                 f"{'、'.join(t['consumers']) or '—'} |")
    L.append("")
    L.append("## 口径与局限")
    L.append("")
    L.append("- 启发式扫描：写盘目标由变量赋值 + 写操作行匹配推断，**可能漏报/误报**；高风险项建议人工确认源码；")
    L.append("- 未执行任何写盘脚本、未修改代码；本报告只做静态审计；")
    L.append("- 备份/合并/原子写仅检测**迹象**（关键词/函数名），不保证运行时一定生效；")
    L.append("- 建议把本审计纳入发布前检查：高风险脚本改动需 review + 备份/原子写/合并三件套。")
    L.append("")
    return "\n".join(L)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def cmd_audit(args) -> int:
    root = Path(args.root) if args.root else ROOT
    records = scan(root)
    rep = build_report(records, root)
    out_json = Path(args.json) if args.json else OUT_JSON
    out_md = Path(args.md) if args.md else OUT_MD
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    out_md.write_text(render_md(rep), encoding="utf-8")
    print(json.dumps({"n_writers": rep["summary"]["n_writers"],
                      "high": rep["summary"]["high"], "medium": rep["summary"]["medium"],
                      "low": rep["summary"]["low"],
                      "high_scripts": [h["script"] for h in rep["high_risk"]]},
                     ensure_ascii=False))
    print(f"[static_writer_audit] -> {out_json} / {out_md}", flush=True)
    return 0


def _parse_args(argv):
    p = argparse.ArgumentParser(prog="static_writer_audit",
                                description="门户静态 JSON 写入脚本审计（只读审计，不改代码）")
    sub = p.add_subparsers(dest="cmd", required=True)
    pe = sub.add_parser("audit", help="扫描并输出审计报告")
    pe.add_argument("--root", default=None)
    pe.add_argument("--json", default=None)
    pe.add_argument("--md", default=None)
    pe.set_defaults(func=cmd_audit)
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
