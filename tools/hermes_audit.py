#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数字军团 Hermes 底层使用审计 v1（只读）。

检查面：
1. Hermes 资产：目录、8787、WebUI/扩展、profiles 数量与名称
2. 代码引用：agent_platform/portal/compositions/tools 中 hermes 引用计数与文件
3. 席位映射：org.json 席位 vs ~/.hermes/profiles 名称
4. 组合契约：6 个 composition 是否声明 Hermes interface/service/'hermes' 依赖
5. 统一 UI：nginx /hermes/、/extensions/、portal nav、/legion 页
6. MCP/集成：常见 hermes_mcp/hermes_bridge/hermes_orchestrator 是否存在

用法：python -X utf8 tools/hermes_audit.py [--json]
只读，不写文件、不启停服务。
"""
from __future__ import annotations

import argparse
import json
import re
import socket
from pathlib import Path

try:
    import yaml
except Exception:  # noqa: BLE001
    yaml = None

ROOT = Path(__file__).resolve().parents[1]
HERMES = ROOT / "Hermes"
ORG = ROOT / "agent_platform" / "registry" / "org.json"
COMP = ROOT / "compositions"
NGINX = ROOT / "portal" / "nginx.conf"
NAV = ROOT / "portal" / "static" / "nav.js"
LEGION = ROOT / "portal" / "legion.html"


def _port_open(port: int, timeout=0.5) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def _scan_refs() -> dict:
    areas = {
        "agent_platform": ROOT / "agent_platform",
        "portal": ROOT / "portal",
        "compositions": COMP,
        "tools": ROOT / "tools",
    }
    out = {}
    for name, base in areas.items():
        hits = []
        if base.is_dir():
            for p in base.rglob("*"):
                if not p.is_file() or p.suffix.lower() not in (".py", ".yaml", ".yml", ".json", ".html", ".js", ".md"):
                    continue
                if any(x in str(p) for x in ("__pycache__", "node_modules", ".venv", ".worktrees")):
                    continue
                try:
                    t = p.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                if re.search(r"(?i)hermes", t):
                    hits.append(str(p.relative_to(ROOT)))
        out[name] = {"files": len(hits), "samples": hits[:8]}
    return out


def _profiles() -> list[str]:
    p = Path.home() / ".hermes" / "profiles"
    return sorted(x.name for x in p.iterdir() if x.is_dir()) if p.is_dir() else []


def _seat_profile_map() -> dict:
    profiles = _profiles()
    seats = []
    try:
        org = json.loads(ORG.read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        org = {}
    def walk(o):
        if isinstance(o, dict):
            aid = o.get("agent_id")
            cn = o.get("cn_name") or o.get("name")
            if aid:
                seats.append((str(aid), str(cn or "")))
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(org)
    aliases = {
        "tianfeng": ("田丰",), "zhaoyun": ("赵云",), "huangzhong": ("黄忠",),
        "kanze": ("阚泽",), "zhangsong": ("张松",),
    }
    matched = {}
    for prof in profiles:
        keys = aliases.get(prof, (prof,))
        cand = [sid for sid, cn in seats if cn and any(k in cn for k in keys)]
        matched[prof] = cand[:3]
    return {"profiles": profiles, "profiles_count": len(profiles),
            "seat_count": len(set(s[0] for s in seats)),
            "matched": matched,
            "unmapped_profiles": [p for p, c in matched.items() if not c]}


def _compositions() -> list[dict]:
    rows = []
    if not COMP.is_dir() or yaml is None:
        return rows
    for p in sorted(COMP.glob("*/manifest.yaml")):
        try:
            d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            continue
        spec = d.get("spec", {}) or {}
        blob = json.dumps(d, ensure_ascii=False).lower()
        rows.append({
            "id": d.get("metadata", {}).get("id") or p.parent.name,
            "declares_hermes": "hermes" in blob,
            "interfaces": [x.get("ref") for x in (spec.get("interfaces") or []) if isinstance(x, dict)],
            "seats": len(spec.get("seats") or []),
        })
    return rows


def _mcp_servers() -> list:
    p = Path.home() / ".hermes" / "config.yaml"
    if not p.is_file() or yaml is None:
        return []
    try:
        d = yaml.safe_load(p.read_text(encoding="utf-8", errors="replace")) or {}
        return sorted((d.get("mcp_servers") or {}).keys())
    except Exception:  # noqa: BLE001
        return []


def _ui_routes() -> dict:
    ng = NGINX.read_text(encoding="utf-8", errors="ignore") if NGINX.is_file() else ""
    nav = NAV.read_text(encoding="utf-8", errors="ignore") if NAV.is_file() else ""
    return {
        "nginx_hermes": bool(re.search(r"location \^~ /hermes/", ng)),
        "nginx_extensions": bool(re.search(r"location \^~ /extensions/", ng)),
        "nav_hermes": "/hermes/" in nav,
        "nav_legion": '"/legion"' in nav or "'/legion'" in nav,
        "legion_page": LEGION.is_file(),
        "hermes_dir": HERMES.is_dir(),
        "legion_ide_ext": (HERMES / "extensions" / "legion-ide" / "app.js").is_file(),
        "a0_mainchat": (HERMES / "extensions" / "legion-ide" / "a0-mainchat.js").is_file(),
    }


def build_audit() -> dict:
    refs = _scan_refs()
    comps = _compositions()
    return {
        "generated_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S%z"),
        "hermes": {
            "dir": str(HERMES),
            "port_8787": _port_open(8787),
            "port_8796": _port_open(8796),
            "port_8096": _port_open(8096),
            "profiles": _seat_profile_map(),
            "components": {
                "orchestrator": (ROOT / "agent_platform" / "legion_command" / "hermes_orchestrator.py").is_file(),
                "auth": (ROOT / "agent_platform" / "legion_command" / "hermes_auth.py").is_file(),
                "health": (ROOT / "agent_platform" / "legion_command" / "hermes_health.py").is_file(),
                "bridge": (ROOT / "agent_platform" / "hermes_bridge.py").is_file(),
                "ui_ext": (HERMES / "extensions" / "legion-ide").is_dir(),
            },
        },
        "mcp_servers": _mcp_servers(),
        "refs": refs,
        "compositions": comps,
        "composition_hermes_declared": sum(1 for c in comps if c["declares_hermes"]),
        "ui": _ui_routes(),
    }


def render_md(a: dict) -> str:
    h = a["hermes"]
    lines = ["# 数字军团 Hermes 底层使用审计 v1", "",
             f"- 生成：{a['generated_at']}",
             f"- 8787 WebUI：{'LISTEN' if h['port_8787'] else 'off'}；8796：{'LISTEN' if h['port_8796'] else 'off'}；8096：{'LISTEN' if h['port_8096'] else 'off'}",
             f"- profiles：{h['profiles']['profiles_count']} 个（{', '.join(h['profiles']['profiles']) or '无'}）；席位 {h['profiles']['seat_count']}",
             "",
             "## 一、Hermes 组件",
             ""]
    for k, v in h["components"].items():
        lines.append(f"- {k}: {'存在' if v else '缺失'}")
    lines += ["", "## 二、MCP 工具面", ""]
    mcp = a.get("mcp_servers") or []
    lines.append(f"- 已配置 MCP servers：**{len(mcp)}**" + ("：" + ", ".join(mcp) if mcp else ""))
    lines += ["", "## 三、代码引用面", "", "| 区域 | 含 hermes 文件数 |", "|---|---:|"]
    for area, row in a["refs"].items():
        lines.append(f"| {area} | {row['files']} |")
    lines += ["", "## 四、组合契约", "", "| 组合 | 声明 hermes | seats | interfaces |", "|---|---|---:|---|"]
    for c in a["compositions"]:
        lines.append(f"| {c['id']} | {'是' if c['declares_hermes'] else '**否**'} | {c['seats']} | {len(c['interfaces'])} |")
    lines += ["", f"**声明 Hermes 的组合：{a['composition_hermes_declared']}/{len(a['compositions'])}**", "",
              "## 五、统一 UI", ""]
    for k, v in a["ui"].items():
        lines.append(f"- {k}: {'是' if v else '否'}")
    lines += ["", "## 六、席位 profile 映射", ""]
    for prof, cand in h["profiles"]["matched"].items():
        lines.append(f"- `{prof}` → {', '.join(cand) if cand else '未匹配到组织席位'}")
    lines += ["", "> 说明：本工具只做只读盘点；'未声明 hermes' 不等于不可用，而是未进入统一框架契约。"]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="数字军团 Hermes 底层使用审计 v1")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    a = build_audit()
    print(json.dumps(a, ensure_ascii=False, indent=1) if args.json else render_md(a), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
