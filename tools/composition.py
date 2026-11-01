#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""组合清单校验器（P1 MVP，v0.1）。

能力（只读、不装载、不改运行时）：
  validate  校验组合 manifest：schema + 引用解析（agent/skill 硬；strategy/dataset/page/job 软，--strict 时转错）
  status    组合账本状态 + 校验摘要
  audit     引用缺口/孤儿审计
  plan/diff 预留（打印待办，不做运行时动作）

规则来源：docs/adr/ADR-015-platform-kernel-composition-model.md、compositions/README.md。

用法：
  python -X utf8 tools/composition.py validate --all
  python -X utf8 tools/composition.py validate compositions/acad1-legion --strict
  python -X utf8 tools/composition.py status
  python -X utf8 tools/composition.py audit
退出码：0 通过；1 有错误（--strict 时警告也计错）；2 环境/参数错误。
"""
from __future__ import annotations

import argparse
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


# ---------- 基础 ----------
def _load_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        return default


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
        if "/" in rest:
            ok = rest.split("/")[-1] in regs["page"] or rest.replace("/", "_") in regs["page"]
        else:
            ok = rest in regs["page"]
        if not ok:
            warnings.append(f"page 未找到对应 portal html：{ref}")
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
                       Path(r"C:\Temp") / f"{token}.json"]
        candidates += list((ROOT / "agent_platform" / "data").rglob(f"{token}.json"))
    elif kind == "dir":
        candidates += [ROOT / "quant_backtest" / "data" / token,
                       Path(r"C:\项目") / "知识库" / token]
    elif kind == "pg":
        mig = ROOT / "docs" / "specs"
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
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) if yaml else None
    except Exception as e:  # noqa: BLE001
        return None, [f"YAML 解析失败：{e}"], []
    if data is None:
        return None, ["YAML 为空或缺少 PyYAML"], []
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
    files = _manifests(args.target)
    if not files:
        print("没有找到 manifest", file=sys.stderr); return 2
    total_errors = total_warn = 0
    out = []
    for f in files:
        data, errors, warnings = validate_manifest(f, regs, args.strict)
        ok = not errors
        total_errors += len(errors); total_warn += len(warnings)
        out.append({"manifest": str(f.relative_to(ROOT)), "ok": ok,
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
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"汇总：{len(files)} 个组合；errors={total_errors}；warnings={total_warn}；strict={args.strict}")
    return 1 if total_errors else 0


def cmd_status(args):
    reg = _load_json(REGISTRY_PATH, {})
    regs = load_registries()
    print(f"host={reg.get('host', {}).get('id')} kernel={reg.get('kernel', {}).get('version')} status={reg.get('status')}")
    for c in reg.get("compositions", []):
        f = ROOT / c.get("manifest", "")
        data, errors, warnings = validate_manifest(f, regs, strict=False) if f.exists() else (None, ["manifest missing"], [])
        print(f"- {c['id']:<24} tier={c['tier']:<9} lifecycle={c['lifecycle']:<8} "
              f"schema={'OK' if not errors else 'FAIL'} warn={len(warnings)}")
    return 0


def cmd_audit(args):
    reg = _load_json(REGISTRY_PATH, {})
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
    ap = argparse.ArgumentParser(description="组合清单校验器（P1 MVP）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate"); v.add_argument("target", nargs="?", default="--all")
    v.add_argument("--strict", action="store_true"); v.add_argument("--json", action="store_true")
    sub.add_parser("status"); sub.add_parser("audit")
    args = ap.parse_args(argv)
    if args.cmd == "validate":
        return cmd_validate(args)
    if args.cmd == "status":
        return cmd_status(args)
    if args.cmd == "audit":
        return cmd_audit(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
