#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""组合清单校验器 / 组合运行时（P1，v0.2）。

能力（只读、不装载、不改运行时；**任何子命令都不写文件**）：
  validate  校验组合 manifest：schema + 引用解析（agent/skill 硬；strategy/dataset/page/job 软，--strict 时转错）
  plan      compositions/registry.json 与 compositions/*/manifest.yaml 的漂移计划
            （缺失 manifest / registry 多余 / 版本 / 生命周期 / owner / tier / host / manifest 路径）
  diff      两个 manifest 的 metadata/spec/引用差异；或 manifest 与 registry.json 登记快照的差异
  retire    组合退役预演：--dry-run 只打印将置 retired 的变更（P1 禁止写 manifest，不提供 --apply）
  status    组合账本状态 + 校验摘要
  audit     引用缺口/孤儿审计

规则来源：docs/adr/ADR-015-platform-kernel-composition-model.md、compositions/README.md。

用法：
  python -X utf8 tools/composition.py validate --all
  python -X utf8 tools/composition.py validate compositions/acad1-legion --strict
  python -X utf8 tools/composition.py plan --strict --json
  python -X utf8 tools/composition.py diff compositions/a/manifest.yaml compositions/b/manifest.yaml
  python -X utf8 tools/composition.py diff compositions/acad1-legion --registry --strict
  python -X utf8 tools/composition.py retire acad1-legion --dry-run
  python -X utf8 tools/composition.py status
  python -X utf8 tools/composition.py audit

退出码：
  0 通过 / 无漂移 / 预演成功；1 有 error（--strict 时 warning/差异也计 error）；2 参数/环境错误。
  说明：diff 非 strict 时"差异=warning"只报告（rc=0），--strict 时差异计错（rc=1）。
  --json 时 stdout 只输出可解析 JSON（人类汇总走 stderr）。
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

try:
    import yaml
except Exception:  # noqa: BLE001
    yaml = None

try:
    import jsonschema
except Exception:  # noqa: BLE001
    jsonschema = None

ROOT = Path(__file__).resolve().parents[1]
COMP = ROOT / "compositions"
SCHEMA_PATH = COMP / "_schema" / "composition.schema.json"
JOB_MAP_PATH = COMP / "_schema" / "job_map.yaml"
REGISTRY_PATH = COMP / "registry.json"

# 与 composition.schema.json metadata.lifecycle enum 保持一致
LIFECYCLES = ("draft", "validated", "registered", "active", "degraded", "retired")

# diff：metadata 关键字段（与 registry.json 登记字段同构，可跨源比较）
DIFF_META_FIELDS = ("id", "name", "version", "owner", "lifecycle", "tier", "host")
DIFF_RUNTIME_FIELDS = ("service", "mode", "endpoint")
DIFF_STATUS_FIELDS = ("phase", "health", "validated")
# (spec 内 key, 展示用扁平路径)
REF_GROUPS = (
    ("seats", "spec.seats"),
    ("skills", "spec.skills"),
    ("strategies", "spec.strategies"),
    ("pages", "spec.pages"),
    ("jobs", "spec.jobs"),
    ("data.requires", "spec.data.requires"),
)


# ---------- 基础 ----------
def _load_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        return default


def _load_manifest(path) -> tuple[dict | None, str]:
    """读 manifest → (data, error)。只读；不抛异常。"""
    p = Path(path)
    if yaml is None:
        return None, "缺少 PyYAML，无法解析 manifest"
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return None, f"YAML 解析失败：{e}"
    if not isinstance(data, dict):
        return None, "YAML 为空或顶层不是对象"
    return data, ""


def _display(path) -> str:
    """仓内路径转相对斜杠路径；仓外路径原样返回（测试临时目录用）。"""
    p = Path(path)
    try:
        return str(p.resolve().relative_to(ROOT.resolve())).replace("\\", "/")
    except (ValueError, OSError):
        return str(p).replace("\\", "/")


def _walk_ids(obj, keys=("agent_id", "agents", "agent")):
    out = set()
    def walk(o, k=None):
        if isinstance(o, dict):
            for kk, vv in o.items():
                walk(vv, kk)
        elif isinstance(o, list):
            for it in o:
                walk(it, k)
        elif isinstance(o, str) and k in keys:
            out.add(o)
    walk(obj)
    return out


def load_registries() -> dict:
    regs = {"agent": set(), "skill": set(), "strategy": set(),
            "dataset": set(), "page": set(), "job": set(), "job_planned": set()}
    # agents：hr 目录 + org/team
    hr = ROOT / "agent_platform" / "hr"
    if hr.exists():
        regs["agent"] |= {p.name for p in hr.iterdir() if p.is_dir()}
    org = _load_json(ROOT / "agent_platform" / "registry" / "org.json", {})
    regs["agent"] |= _walk_ids(org)
    for tf in (ROOT / "agent_platform" / "registry").glob("team_*.json"):
        regs["agent"] |= _walk_ids(_load_json(tf, {}))
    # skills
    sk = _load_json(ROOT / "agent_platform" / "registry" / "skill_registry.json", {})
    for item in (sk.get("skills") or []):
        if isinstance(item, dict) and item.get("id"):
            regs["skill"].add(str(item["id"]))
    # strategy：本地可见口径（v2_strategy 表口径由 P1 解析器后续补）
    for f in (ROOT / "portal" / "strategies.json",
              ROOT / "agent_platform" / "data" / "academy" / "strategy_portfolio.json"):
        d = _load_json(f, {})
        items = d if isinstance(d, list) else (d.get("strategies") or d.get("items") or [])
        for it in items:
            if isinstance(it, dict) and it.get("id"):
                regs["strategy"].add(str(it["id"]))
    # dataset：最新 inventory
    invs = sorted((ROOT / "research" / "reports").glob("dataset_inventory_*.json"))
    if invs:
        inv = _load_json(invs[-1], {})
        def walk_ds(o):
            if isinstance(o, dict):
                for k, v in o.items():
                    if isinstance(v, str):
                        regs["dataset"].add(v); regs["dataset"].add(k)
                    else:
                        walk_ds(v)
            elif isinstance(o, list):
                for it in o:
                    walk_ds(it)
        walk_ds(inv)
    # pages：portal html 文件名
    for p in (ROOT / "portal").glob("*.html"):
        regs["page"].add(p.stem)
    # jobs：映射表
    jm = {}
    if JOB_MAP_PATH.exists() and yaml is not None:
        jm = yaml.safe_load(JOB_MAP_PATH.read_text(encoding="utf-8")) or {}
    for k, v in (jm or {}).items():
        if v:
            regs["job"].add(str(k))
        else:
            regs["job_planned"].add(str(k))
    return regs


def resolve_ref(ref: str, regs: dict, errors: list, warnings: list) -> None:
    if "/" not in ref:
        errors.append(f"非法 ref（缺类型前缀）：{ref}")
        return
    kind, rest = ref.split("/", 1)
    token = rest.split("/")[-1]
    if kind == "agent":
        if rest not in regs["agent"]:
            errors.append(f"agent 未登记：{ref}")
    elif kind == "skill":
        if token not in regs["skill"]:
            warnings.append(f"skill 未在 skill_registry：{ref}")
    elif kind == "strategy":
        if token not in regs["strategy"]:
            warnings.append(f"strategy 未在本地口径（v2_strategy 待 P1 解析）：{ref}")
    elif kind == "dataset":
        if not _dataset_exists(rest, regs):
            warnings.append(f"dataset 未在登记/路径不存在：{ref}")
    elif kind == "page":
        variants = {rest.replace("/", "_"), rest.replace("/", "_").replace("-", "_")}
        if "/" in rest:
            parent, leaf = rest.rsplit("/", 1)
            variants |= {leaf, f"{parent}_{leaf}", f"{parent}_{leaf}".replace("-", "_")}
        ok = any(v in regs["page"] for v in variants)
        if not ok:
            warnings.append(f"page 未找到对应 portal html（或为整合后/规划中页面）：{ref}")
    elif kind == "job":
        if rest in regs["job_planned"]:
            warnings.append(f"job 为 planned（未建计划任务）：{ref}")
        elif rest not in regs["job"]:
            warnings.append(f"job 未在 job_map：{ref}")
    elif kind in ("ext", "service", "interface"):
        warnings.append(f"{kind} 类型暂为软校验（P1 后续）：{ref}")
    else:
        errors.append(f"未知 ref 类型：{kind}")


def _dataset_exists(rest: str, regs: dict) -> bool:
    kind = rest.split("/")[0] if "/" in rest else "file"
    token = rest.split("/")[-1]
    if rest in regs["dataset"] or token in regs["dataset"]:
        return True
    candidates = []
    if kind == "file":
        candidates += [ROOT / "agent_platform" / "data" / f"{token}.json",
                       ROOT / "agent_platform" / "data" / token,
                       ROOT / "agent_platform" / "data" / f"{token}.jsonl",
                       ROOT / "research" / f"{token}.json",
                       ROOT / "research" / f"{token}.db",
                       Path(r"C:\Temp") / f"{token}.json"]
        candidates += list((ROOT / "agent_platform" / "data").rglob(f"{token}.json"))
        candidates += list((ROOT / "agent_platform" / "data").rglob(f"{token}.jsonl"))
    elif kind == "dir":
        candidates += [ROOT / "quant_backtest" / "data" / token,
                       Path(r"C:\项目") / "知识库" / token]
    elif kind == "pg":
        return token.startswith("v2_")  # P1 软校验：v2_* 表按命名约定通过
    elif kind == "registry":
        return token in {"dataset_registry", "catalog"}
    return any(p.exists() for p in candidates)


def collect_refs(obj):
    refs = []
    def walk(o):
        if isinstance(o, dict):
            r = o.get("ref")
            if isinstance(r, str) and "/" in r:
                refs.append(r)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for it in o:
                walk(it)
    walk(obj)
    return refs


def validate_manifest(path: Path, regs: dict, strict: bool):
    errors, warnings = [], []
    data, err = _load_manifest(path)
    if err or data is None:
        return None, [err or "YAML 为空"], []
    if jsonschema is not None and SCHEMA_PATH.exists():
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        try:
            jsonschema.validate(instance=data, schema=schema)
        except Exception as e:  # noqa: BLE001
            return data, [f"schema: {getattr(e, 'message', e)}"], []
    for ref in collect_refs(data):
        resolve_ref(ref, regs, errors, warnings)
    if strict:
        errors += warnings
        warnings = []
    return data, errors, warnings


# ---------- 目标解析 ----------
def resolve_manifest_target(target) -> Path | None:
    """把 "组合 id / 目录 / manifest 路径" 解析为 manifest 路径；找不到返回 None。"""
    raw = str(target or "").strip().strip('"')
    if not raw:
        return None
    p = Path(raw)
    candidates = [p] if p.is_absolute() else [ROOT / p, COMP / p]
    for cand in candidates:
        try:
            if cand.is_file():
                return cand
            if cand.is_dir() and (cand / "manifest.yaml").is_file():
                return cand / "manifest.yaml"
        except OSError:
            continue
    m = COMP / raw / "manifest.yaml"
    return m if m.is_file() else None


def _load_registry() -> dict:
    data = _load_json(REGISTRY_PATH, {})
    return data if isinstance(data, dict) else {}


def _registry_entries(reg: dict) -> dict:
    out = {}
    for c in (reg.get("compositions") or []):
        if isinstance(c, dict) and c.get("id"):
            out[str(c["id"])] = c
    return out


def summarize_findings(findings: list, strict: bool = False) -> dict:
    """findings 计数与退出码：默认 error 阻断；strict 时 warning 全部转 error。"""
    n = len(findings)
    if strict:
        errors, warnings = n, 0
    else:
        errors = sum(1 for f in findings if f.get("level") == "error")
        warnings = n - errors
    return {"findings": n, "errors": errors, "warnings": warnings,
            "strict": bool(strict), "rc": 1 if errors else 0}


# ---------- plan：registry vs manifests ----------
PLAN_FIELD_KINDS = (
    ("version", "version_changed"),
    ("lifecycle", "lifecycle_changed"),
    ("owner", "owner_changed"),
    ("tier", "tier_changed"),
    ("host", "host_changed"),
)


def plan_drift(reg_path: Path = REGISTRY_PATH, comp_dir: Path = COMP) -> list[dict]:
    """对比 registry 账本与 manifest 目录，返回漂移 findings（只读，不写文件）。

    kind：missing_manifest（registry 有、manifest 无，error）/ missing_registry（manifest 有、registry 无，
    warning）/ version_changed / lifecycle_changed / owner_changed / tier_changed / host_changed（warning，
    --strict 升级为错）/ manifest_path_mismatch（warning）/ manifest_unreadable（error）/
    id_dir_mismatch（warning）/ duplicate_manifest_id（error）。
    """
    findings: list[dict] = []
    reg = _load_json(Path(reg_path), {})
    reg = reg if isinstance(reg, dict) else {}
    entries = _registry_entries(reg)
    manifests: dict[str, tuple[Path, dict]] = {}

    for p in sorted(Path(comp_dir).glob("*/manifest.yaml")):
        data, err = _load_manifest(p)
        if err or data is None:
            findings.append({"kind": "manifest_unreadable", "id": p.parent.name,
                             "level": "error", "manifest": _display(p), "detail": err})
            continue
        md = data.get("metadata") or {}
        cid = str(md.get("id") or p.parent.name)
        if cid != p.parent.name:
            findings.append({"kind": "id_dir_mismatch", "id": cid, "level": "warning",
                             "manifest": _display(p),
                             "detail": f"metadata.id={cid!r} 与目录名 {p.parent.name!r} 不一致"})
        if cid in manifests:
            findings.append({"kind": "duplicate_manifest_id", "id": cid, "level": "error",
                             "detail": "多个 manifest 声明同一 metadata.id"})
            continue
        manifests[cid] = (p, data)

    for cid in sorted(entries):
        ent = entries[cid]
        if cid not in manifests:
            findings.append({"kind": "missing_manifest", "id": cid, "level": "error",
                             "registry_manifest": str(ent.get("manifest") or ""),
                             "detail": "registry 已登记但 manifest 不存在"})
            continue
        p, data = manifests[cid]
        md = data.get("metadata") or {}
        for field, kind in PLAN_FIELD_KINDS:
            rv, mv = ent.get(field), md.get(field)
            if rv != mv:
                findings.append({"kind": kind, "id": cid, "level": "warning",
                                 "field": field, "registry": rv, "manifest": mv,
                                 "manifest_path": _display(p)})
        declared = str(ent.get("manifest") or "").replace("\\", "/").strip()
        if declared.startswith("./"):
            declared = declared[2:]
        expected = "compositions/%s/manifest.yaml" % cid
        if declared and declared != expected:
            findings.append({"kind": "manifest_path_mismatch", "id": cid, "level": "warning",
                             "registry": declared, "expected": expected})

    for cid in sorted(manifests):
        if cid not in entries:
            findings.append({"kind": "missing_registry", "id": cid, "level": "warning",
                             "manifest": _display(manifests[cid][0]),
                             "detail": "manifest 存在但 registry 未登记"})
    return findings


# ---------- diff：两个 manifest / manifest vs registry 快照 ----------
def _manifest_scalars(data: dict, registry_mode: bool = False) -> dict:
    md = data.get("metadata") or {}
    out = {f"metadata.{k}": md.get(k) for k in DIFF_META_FIELDS}
    if not registry_mode:
        sp = data.get("spec") or {}
        out["spec.kernel"] = sp.get("kernel")
        rt = sp.get("runtime") or {}
        for k in DIFF_RUNTIME_FIELDS:
            out[f"spec.runtime.{k}"] = rt.get(k)
        auth = (sp.get("interfaces") or {}).get("auth") or {}
        out["spec.interfaces.auth.public"] = auth.get("public")
        out["spec.data.contract"] = (sp.get("data") or {}).get("contract")
    st = data.get("status") or {}
    for k in DIFF_STATUS_FIELDS:
        out[f"status.{k}"] = st.get(k)
    return out


def _manifest_refs(data: dict) -> dict:
    sp = data.get("spec") or {}
    out = {}
    for key, label in REF_GROUPS:
        if key == "data.requires":
            items = (sp.get("data") or {}).get("requires") or []
        else:
            items = sp.get(key) or []
        out[label] = sorted({str(i.get("ref")) for i in items
                             if isinstance(i, dict) and i.get("ref")})
    return out


def registry_snapshot(entry: dict) -> dict:
    """把 registry 账本条目投影成可 diff 的 manifest 形状（只有 metadata/status）。"""
    return {"metadata": {k: entry.get(k) for k in DIFF_META_FIELDS},
            "status": {k: (entry.get("status") or {}).get(k) for k in DIFF_STATUS_FIELDS}}


def diff_manifests(left: dict, right: dict, registry_mode: bool = False) -> list[dict]:
    """比较两个 manifest（或右值为 registry 快照）的关键字段；返回差异列表（只读）。"""
    a = _manifest_scalars(left, registry_mode)
    b = _manifest_scalars(right, registry_mode)
    diffs = []
    for path in a:
        if a[path] != b.get(path):
            diffs.append({"path": path, "change": "changed",
                          "left": a[path], "right": b.get(path)})
    if not registry_mode:
        ar, br = _manifest_refs(left), _manifest_refs(right)
        for _, label in REF_GROUPS:
            la, rb = set(ar.get(label) or []), set(br.get(label) or [])
            for ref in sorted(rb - la):
                diffs.append({"path": label, "change": "ref_added", "right": ref})
            for ref in sorted(la - rb):
                diffs.append({"path": label, "change": "ref_removed", "left": ref})
    return diffs


# ---------- retire：只读预演 ----------
def build_retire_plan(data: dict, manifest_path, registry_entry: dict | None = None,
                      today: str | None = None) -> dict:
    """生成退役预演（不写盘）：lifecycle/updated/status.phase + registry 补丁预览。"""
    findings: list[dict] = []
    changes: list[dict] = []
    md = data.get("metadata") or {}
    cid = str(md.get("id") or Path(manifest_path).parent.name)
    cur = md.get("lifecycle")
    today = today or datetime.date.today().isoformat()
    if cur == "retired":
        findings.append({"kind": "already_retired", "level": "warning", "id": cid,
                         "detail": "lifecycle 已是 retired（幂等：无 lifecycle 变更）"})
    elif cur not in LIFECYCLES:
        findings.append({"kind": "invalid_lifecycle", "level": "error", "id": cid,
                         "detail": "未知 lifecycle=%r（合法：%s）"
                                   % (cur, "/".join(LIFECYCLES))})
    else:
        changes.append({"path": "metadata.lifecycle", "from": cur, "to": "retired"})
        if md.get("updated") != today:
            changes.append({"path": "metadata.updated",
                            "from": md.get("updated"), "to": today})
    st = data.get("status") or {}
    if st.get("phase") != "retired":
        changes.append({"path": "status.phase", "from": st.get("phase"), "to": "retired"})

    registry_patch: list[dict] = []
    if registry_entry is not None:
        if registry_entry.get("lifecycle") != "retired":
            registry_patch.append({"path": "compositions[%s].lifecycle" % cid,
                                   "from": registry_entry.get("lifecycle"),
                                   "to": "retired"})
        rst = registry_entry.get("status") or {}
        if rst.get("phase") != "retired":
            registry_patch.append({"path": "compositions[%s].status.phase" % cid,
                                   "from": rst.get("phase"), "to": "retired"})
    return {"id": cid, "manifest": _display(manifest_path),
            "current_lifecycle": cur, "planned_lifecycle": "retired",
            "changes": changes, "registry_patch": registry_patch, "findings": findings}


# ---------- CLI ----------
def _manifests(target: str):
    if target in ("--all", "all"):
        return sorted(COMP.glob("*/manifest.yaml"))
    p = Path(target)
    if not p.is_absolute():
        p = ROOT / p
    if p.is_file():
        return [p]
    if p.is_dir() and (p / "manifest.yaml").exists():
        return [p / "manifest.yaml"]
    return []


def cmd_validate(args):
    regs = load_registries()
    target = "--all" if (getattr(args, "all", False) or args.target in (None, "--all", "all")) else args.target
    files = _manifests(target)
    if not files:
        print("没有找到 manifest", file=sys.stderr); return 2
    total_errors = total_warn = 0
    out = []
    for f in files:
        data, errors, warnings = validate_manifest(f, regs, args.strict)
        ok = not errors
        total_errors += len(errors); total_warn += len(warnings)
        out.append({"manifest": _display(f), "ok": ok,
                    "errors": errors, "warnings": warnings,
                    "id": (data or {}).get("metadata", {}).get("id", "?")})
        if not args.json:
            mark = "PASS" if ok else "FAIL"
            print(f"[{mark}] {f.relative_to(ROOT)}  errors={len(errors)} warnings={len(warnings)}")
            for e in errors:
                print("   ERR ", e)
            for w in warnings[:8]:
                print("   WARN", w)
            if len(warnings) > 8:
                print(f"   ... {len(warnings)-8} more warnings")
    summary = (f"汇总：{len(files)} 个组合；errors={total_errors}；"
               f"warnings={total_warn}；strict={args.strict}")
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        print(summary, file=sys.stderr)
    else:
        print(summary)
    return 1 if total_errors else 0


def cmd_plan(args):
    findings = plan_drift()
    summary = summarize_findings(findings, args.strict)
    ok = summary["rc"] == 0
    reg = _load_registry()
    n_reg = len(_registry_entries(reg))
    n_man = len(sorted(COMP.glob("*/manifest.yaml")))
    if args.json:
        print(json.dumps({"command": "plan", "ok": ok, "strict": bool(args.strict),
                          "registry": _display(REGISTRY_PATH), "manifests_dir": _display(COMP),
                          "registry_count": n_reg, "manifest_count": n_man,
                          "findings": findings, "summary": summary},
                         ensure_ascii=False, indent=1))
        return summary["rc"]
    print("== plan：registry.json vs compositions/*/manifest.yaml（只读，不写文件）==")
    print(f"  registry 组合 {n_reg} 个；manifest {n_man} 个")
    if not findings:
        print("  [OK] 无漂移：registry 账本与 manifests 一致")
    for f in findings:
        tag = "ERR " if f.get("level") == "error" else "WARN"
        print(f"  {tag} {f['kind']:<22} id={f['id']}")
        if f.get("registry") is not None or f.get("manifest") is not None:
            print(f"       registry={f.get('registry')!r} manifest={f.get('manifest')!r}")
        if f.get("detail"):
            print(f"       {f['detail']}")
    print(f"汇总：findings={summary['findings']}；errors={summary['errors']}；"
          f"warnings={summary['warnings']}；strict={bool(args.strict)}")
    return summary["rc"]


def cmd_diff(args):
    left_path = resolve_manifest_target(args.left)
    if left_path is None:
        print(f"[diff] ERROR: 找不到组合/manifest：{args.left}", file=sys.stderr)
        return 2
    left_data, lerr = _load_manifest(left_path)
    if lerr or left_data is None:
        print(f"[diff] ERROR: 左值解析失败 {_display(left_path)}: {lerr}", file=sys.stderr)
        return 2

    mode = "manifest"
    if args.registry:
        if args.right:
            print("[diff] ERROR: 给了第二个 manifest 时不能再带 --registry", file=sys.stderr)
            return 2
        lid = str((left_data.get("metadata") or {}).get("id") or "")
        entry = _registry_entries(_load_registry()).get(lid)
        if entry is None:
            print(f"[diff] ERROR: registry 无组合 id={lid!r}（先跑 plan 看漂移）",
                  file=sys.stderr)
            return 2
        right_data = registry_snapshot(entry)
        right_label = f"registry.json#{lid}"
        mode = "registry"
    elif args.right:
        right_path = resolve_manifest_target(args.right)
        if right_path is None:
            print(f"[diff] ERROR: 找不到组合/manifest：{args.right}", file=sys.stderr)
            return 2
        right_data, rerr = _load_manifest(right_path)
        if rerr or right_data is None:
            print(f"[diff] ERROR: 右值解析失败 {_display(right_path)}: {rerr}", file=sys.stderr)
            return 2
        right_label = _display(right_path)
    else:
        print("[diff] ERROR: 需要第二个 manifest，或 --registry 与 registry 快照比较",
              file=sys.stderr)
        return 2

    diffs = diff_manifests(left_data, right_data,
                           registry_mode=(mode == "registry"))
    strict = bool(args.strict)
    errors = len(diffs) if strict else 0
    warnings = 0 if strict else len(diffs)
    rc = 1 if errors else 0
    payload = {"command": "diff", "ok": rc == 0, "mode": mode, "strict": strict,
               "left": _display(left_path), "right": right_label,
               "differences": diffs,
               "summary": {"differences": len(diffs), "errors": errors,
                           "warnings": warnings, "strict": strict, "rc": rc}}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
        return rc
    mode_cn = "manifest vs manifest" if mode == "manifest" else "manifest vs registry 快照"
    print(f"== diff（{mode_cn}）==")
    print(f"  left : {payload['left']}")
    print(f"  right: {payload['right']}")
    if not diffs:
        print("  [OK] 无差异")
    for d in diffs:
        change = d.get("change")
        if change == "ref_added":
            print(f"  + {d['path']}: {d.get('right')}（右侧新增引用）")
        elif change == "ref_removed":
            print(f"  - {d['path']}: {d.get('left')}（右侧缺失引用）")
        else:
            print(f"  ~ {d['path']}: {d.get('left')!r} -> {d.get('right')!r}")
    print(f"汇总：差异={len(diffs)}；errors={errors}；warnings={warnings}；strict={strict}")
    return rc


def cmd_retire(args):
    if not args.dry_run:
        msg = ("[retire] P1 只读：未带 --dry-run，拒绝执行（本命令不提供 --apply）。"
               "退役写盘走 P2 审阅通道；P1 只做预演打印。")
        print(msg, file=sys.stderr if args.json else sys.stdout)
        return 2
    path = resolve_manifest_target(args.target)
    if path is None:
        print(f"[retire] ERROR: 找不到组合/manifest：{args.target}", file=sys.stderr)
        return 2
    data, err = _load_manifest(path)
    if err or data is None:
        print(f"[retire] ERROR: manifest 解析失败 {_display(path)}: {err}", file=sys.stderr)
        return 2
    cid = str((data.get("metadata") or {}).get("id") or Path(path).parent.name)
    entry = _registry_entries(_load_registry()).get(cid)
    plan = build_retire_plan(data, path, entry)
    summary = summarize_findings(plan["findings"], args.strict)
    payload = {"command": "retire", "ok": summary["rc"] == 0, "dry_run": True,
               "strict": bool(args.strict), "plan": plan,
               "summary": {"findings": summary["findings"], "errors": summary["errors"],
                           "warnings": summary["warnings"],
                           "strict": summary["strict"], "rc": summary["rc"]}}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
        return summary["rc"]
    print("== retire 预演（dry-run，只读：不写 manifest / registry）==")
    print(f"  组合 {plan['id']}  |  {plan['manifest']}")
    print(f"  lifecycle: {plan['current_lifecycle']} -> {plan['planned_lifecycle']}")
    if not plan["changes"]:
        print("  [OK] 无待执行变更")
    for c in plan["changes"]:
        print(f"  manifest ~ {c['path']}: {c.get('from')!r} -> {c.get('to')!r}")
    for c in plan["registry_patch"]:
        print(f"  registry ~ {c['path']}: {c.get('from')!r} -> {c.get('to')!r}")
    for f in plan["findings"]:
        tag = "ERR " if f.get("level") == "error" else "WARN"
        print(f"  {tag} {f['kind']}: {f.get('detail', '')}")
    print(f"汇总：findings={summary['findings']}；errors={summary['errors']}；"
          f"warnings={summary['warnings']}；strict={bool(args.strict)}；未写盘（dry-run）")
    return summary["rc"]


def cmd_status(args):
    reg = _load_registry()
    regs = load_registries()
    print(f"host={reg.get('host', {}).get('id')} kernel={reg.get('kernel', {}).get('version')} status={reg.get('status')}")
    for c in reg.get("compositions", []):
        f = ROOT / c.get("manifest", "")
        data, errors, warnings = validate_manifest(f, regs, strict=False) if f.exists() else (None, ["manifest missing"], [])
        print(f"- {c['id']:<24} tier={c['tier']:<9} lifecycle={c['lifecycle']:<8} "
              f"schema={'OK' if not errors else 'FAIL'} warn={len(warnings)}")
    return 0


def cmd_audit(args):
    reg = _load_registry()
    ids = {c.get("id") for c in reg.get("compositions", [])}
    dirs = {p.parent.name for p in COMP.glob("*/manifest.yaml")}
    print("registry 与目录一致：" , "OK" if ids == dirs else f"DIFF missing_dirs={ids-dirs} missing_reg={dirs-ids}")
    regs = load_registries()
    for p in sorted(COMP.glob("*/manifest.yaml")):
        data, errors, warnings = validate_manifest(p, regs, strict=False)
        if errors or warnings:
            print(f"- {p.parent.name}: errors={len(errors)} warnings={len(warnings)}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="组合清单校验器 / 组合运行时（P1，只读）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate", help="校验 manifest（schema + 引用解析）")
    v.add_argument("target", nargs="?", default="--all")
    v.add_argument("--strict", action="store_true", help="warning 转 error")
    v.add_argument("--json", action="store_true", help="stdout 输出 JSON")
    v.add_argument("--all", action="store_true")

    p = sub.add_parser("plan", help="registry 与 manifests 漂移计划（只读，不写文件）")
    p.add_argument("--strict", action="store_true", help="warning 转 error（漂移阻断）")
    p.add_argument("--json", action="store_true", help="stdout 输出 JSON")

    d = sub.add_parser("diff", help="两个 manifest（或 manifest vs registry 快照）差异")
    d.add_argument("left", help="左 manifest 路径或组合 id")
    d.add_argument("right", nargs="?", default=None,
                   help="右 manifest 路径或组合 id；缺省时用 --registry")
    d.add_argument("--registry", action="store_true",
                   help="右值取 registry.json 中同一 id 的登记快照")
    d.add_argument("--strict", action="store_true", help="差异计为错误（rc=1）")
    d.add_argument("--json", action="store_true", help="stdout 输出 JSON")

    r = sub.add_parser("retire", help="组合退役预演（只读；必须 --dry-run）")
    r.add_argument("target", help="组合 id 或 manifest 路径")
    r.add_argument("--dry-run", action="store_true",
                   help="只打印将置 retired 的变更（P1 必填；不写 manifest）")
    r.add_argument("--strict", action="store_true", help="warning 转 error")
    r.add_argument("--json", action="store_true", help="stdout 输出 JSON")

    sub.add_parser("status", help="组合账本状态 + 校验摘要")
    sub.add_parser("audit", help="引用缺口/孤儿审计")

    args = ap.parse_args(argv)
    if args.cmd == "validate":
        return cmd_validate(args)
    if args.cmd == "plan":
        return cmd_plan(args)
    if args.cmd == "diff":
        return cmd_diff(args)
    if args.cmd == "retire":
        return cmd_retire(args)
    if args.cmd == "status":
        return cmd_status(args)
    if args.cmd == "audit":
        return cmd_audit(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
