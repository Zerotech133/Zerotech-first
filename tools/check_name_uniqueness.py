# -*- coding: utf-8 -*-
"""名称唯一性 CI 规则（P3）——编制内一名一席、治理/虚拟席必须带席位限定。

背景：2026-10-26 发现 IDE 里有两个"赵云"（R2 编制·保守风控哨 / ZHAOYUN 不占编·治理席），
展示层按名字聚合就会把人认错；P1 已用「赵云·治理席（不占编）」消歧。本工具把规则固化成
可执行检查，接每日质量门（QualityGate）与 CI。

规则：
  R1 编制内一名一席：bundle 里非虚拟治理卡 `cn_name` 必填，且不得两名同字；
  R2 治理/虚拟席必须带席位限定：`entry_source=virtual_governance` 的卡名必须含
     "·" 或 "（" 或 "("（例：赵云·治理席（不占编）），且不得与编内名完全相同；
  R3 HR 名册内 `cn_name` 唯一（HR 是名字真源，编制一档一名）；
  R4 提示（不阻断）：org profile 自带 cn_name 与 bundle 名不一致时给 warning。

只读：不写任何真源。用法：
    python tools/check_name_uniqueness.py            # 人读
    python tools/check_name_uniqueness.py --json     # 机器读
    python tools/check_name_uniqueness.py --root <repo>
退出码：0 全过；1 有违规；2 自身错误。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[1]
QUALIFIERS = ("·", "（", "(")


def _load_bundle(root: Path) -> dict:
    return json.loads((root / "portal" / "agent_ide_bundle.json").read_text(encoding="utf-8"))


def _load_hr_names(root: Path) -> dict:
    out = {}
    hr = root / "agent_platform" / "hr"
    if not hr.exists():
        return out
    for d in sorted(hr.iterdir()):
        f = d / "resume.yaml"
        if not f.is_file():
            continue
        cn = ""
        try:
            for ln in f.read_text(encoding="utf-8", errors="replace").splitlines():
                if ln.startswith("cn_name:"):
                    cn = ln.split(":", 1)[1].strip().strip("'\"")
                    break
                if ln.startswith("name:") and not cn:
                    cn = ln.split(":", 1)[1].strip().strip("'\"")
        except OSError:
            continue
        if cn:
            out[d.name] = cn
    return out


def _load_org_names(root: Path) -> dict:
    """{agent_id: cn_name}（org profile 自带名，未必有）。"""
    try:
        org = json.loads((root / "agent_platform" / "registry" / "org.json")
                         .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    for d in org.get("departments") or []:
        profs = d.get("agent_profiles") or {}
        items = profs.values() if isinstance(profs, dict) else profs
        for p in items or []:
            aid = str(p.get("agent_id") or "")
            cn = str(p.get("cn_name") or "")
            if aid and cn:
                out[aid] = cn
    return out


def check(root: Path = DEFAULT_ROOT) -> dict:
    root = Path(root)
    bundle = _load_bundle(root)
    cards = bundle.get("agents") or {}
    hr = _load_hr_names(root)
    org = _load_org_names(root)

    violations, warnings = [], []

    in_head = {aid: c for aid, c in cards.items()
               if (c or {}).get("entry_source") != "virtual_governance"}
    virtual = {aid: c for aid, c in cards.items()
               if (c or {}).get("entry_source") == "virtual_governance"}

    # R1：编制内必填 + 唯一
    by_name = {}
    for aid, c in in_head.items():
        cn = str(c.get("cn_name") or c.get("name") or "").strip()
        if not cn:
            violations.append({"code": "NAME_MISSING", "seat": aid,
                               "detail": "编制内席位缺 cn_name"})
            continue
        by_name.setdefault(cn, []).append(aid)
    for cn, ids in sorted(by_name.items()):
        if len(ids) > 1:
            violations.append({"code": "DUP_IN_HEADCOUNT", "name": cn, "holders": ids,
                               "detail": "编制内 %d 席同名「%s」：一名一席被破坏" % (len(ids), cn)})

    # R2：治理/虚拟席带席位限定
    for aid, c in virtual.items():
        cn = str(c.get("cn_name") or c.get("name") or "").strip()
        if not cn:
            violations.append({"code": "VIRTUAL_NAME_MISSING", "seat": aid,
                               "detail": "治理/虚拟席缺 cn_name"})
        elif not any(q in cn for q in QUALIFIERS):
            violations.append({"code": "VIRTUAL_UNQUALIFIED", "seat": aid, "name": cn,
                               "detail": "治理/虚拟席名「%s」缺席位限定（须含 · / （ ）" % cn})
        if cn in by_name:
            violations.append({"code": "VIRTUAL_SHADOWS_HEADCOUNT", "seat": aid, "name": cn,
                               "detail": "治理/虚拟席名与编制内席完全相同：%s" % by_name[cn]})

    # R3：HR 名册内唯一
    hr_by_name = {}
    for aid, cn in hr.items():
        hr_by_name.setdefault(cn, []).append(aid)
    for cn, ids in sorted(hr_by_name.items()):
        if len(ids) > 1:
            violations.append({"code": "DUP_HR_NAME", "name": cn, "holders": ids,
                               "detail": "HR 名册 %d 席同名「%s」" % (len(ids), cn)})

    # R4：org 自带名与 bundle 不一致（提示）
    for aid, cn in org.items():
        card = cards.get(aid) or {}
        bcn = str(card.get("cn_name") or "").strip()
        if bcn and bcn != cn:
            warnings.append({"code": "ORG_BUNDLE_NAME_DIFF", "seat": aid,
                             "org": cn, "bundle": bcn,
                             "detail": "org 档案名与 bundle 显示名不一致（检查是否漏消歧）"})
        if cn in by_name and aid not in by_name[cn]:
            warnings.append({"code": "ORG_NAME_COLLIDES", "seat": aid, "name": cn,
                             "detail": "org 档案名与编内他席同名"})

    return {"ok": not violations, "schema": "name_uniqueness/v1",
            "generated": __import__("time").strftime("%Y-%m-%dT%H:%M:%S%z"),
            "root": str(root),
            "stats": {"cards": len(cards), "in_headcount": len(in_head),
                      "virtual": len(virtual), "hr_names": len(hr)},
            "violations": violations, "warnings": warnings}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="名称唯一性 CI（HR + org + bundle）")
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        rep = check(Path(args.root))
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": type(exc).__name__,
                          "detail": str(exc)[:200]}, ensure_ascii=False))
        return 2
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    else:
        s = rep["stats"]
        print("名称唯一性检查 | 卡 %d（编内 %d / 治理 %d）| HR %d 名"
              % (s["cards"], s["in_headcount"], s["virtual"], s["hr_names"]))
        if rep["violations"]:
            for v in rep["violations"]:
                print("[违规] %-24s %s" % (v["code"], v["detail"]))
        else:
            print("✓ 无违规（编制内一名一席；治理/虚拟席均带席位限定）")
        for w in rep["warnings"]:
            print("[提示] %-24s %s" % (w["code"], w["detail"]))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
