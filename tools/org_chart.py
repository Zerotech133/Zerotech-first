# -*- coding: utf-8 -*-
"""研发部组织架构图生成/校验（2026-10-27 WO-ORG-CHART）。

真源：org.json dev.deputy/groups/command_chain + 席位 group/org_role/report_to。
输出：docs/dev/org_chart.md + org_chart.html（tokens/nav 三件套）。
校验：汇报线 / 组长 E3 / 组名成员一致 / A0A 挂 ARCH1 / 矩阵不占指挥链。

用法：--json / --md / --html / --check
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORG = ROOT / "agent_platform" / "registry" / "org.json"
OUT_MD = ROOT / "docs" / "dev" / "org_chart.md"
OUT_HTML = ROOT / "docs" / "dev" / "org_chart.html"
DEV = "dev"


def load(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        return default


def _depts(org):
    return {d.get("dept"): d for d in (org or {}).get("departments") or []}


def _profiles(dept):
    out = {}
    for p in (dept or {}).get("agent_profiles") or []:
        if isinstance(p, dict) and p.get("agent_id"):
            out[p["agent_id"]] = p
    return out


def find_seat(org, seat):
    for d in (org or {}).get("departments") or []:
        for p in d.get("agent_profiles") or []:
            if p.get("agent_id") == seat:
                return d, p
    return None, None


def _hr_names(root=None):
    """从 agent_platform/hr/<席码>/resume.yaml 读 cn_name（唯一真源）。"""
    base = Path(root or ROOT) / "agent_platform" / "hr"
    out = {}
    if not base.is_dir():
        return out
    for d in base.iterdir():
        f = d / "resume.yaml"
        if not d.is_dir() or not f.is_file():
            continue
        try:
            for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                m = re.match(r'^cn_name:\s*"?([^"\s#]+)"?', line.strip())
                if m:
                    out[d.name] = m.group(1)
                    break
        except OSError:
            continue
    return out


def collect(org):
    depts = _depts(org)
    dev = depts.get(DEV) or {}
    prof = _profiles(dev)
    groups = {g.get("id"): g for g in dev.get("groups") or []}
    a0d, a0 = find_seat(org, "A0")
    _, a0a = find_seat(org, "A0A")
    _, qa1 = find_seat(org, "QA1")
    _, guanyu = find_seat(org, "GUANYU")
    _, zhaoyun = find_seat(org, "ZHAOYUN")
    matrix = {}
    hr = _hr_names()
    for sid, p in prof.items():
        if not p.get("cn_name") and hr.get(sid):
            p["cn_name"] = hr[sid]
    for sid, p in (("A0", a0), ("A0A", a0a), ("QA1", qa1),
                   ("GUANYU", guanyu), ("ZHAOYUN", zhaoyun)):
        if isinstance(p, dict) and not p.get("cn_name") and hr.get(sid):
            p["cn_name"] = hr[sid]
    for sid, p in prof.items():
        rows = []
        for m in p.get("matrix_assignments") or []:
            if isinstance(m, dict) and m.get("dept"):
                rows.append({"dept": m["dept"],
                             "role_profile": m.get("role_profile", "")})
        if rows:
            matrix[sid] = rows
    return {"dev": dev, "profiles": prof, "groups": groups,
            "a0": a0, "a0a": a0a, "qa1": qa1, "guanyu": guanyu,
            "zhaoyun": zhaoyun, "matrix": matrix}


def validate(org):
    c = collect(org)
    errors = []
    dev = c["dev"]
    if not dev:
        return ["org 缺 dev 部门"], c
    lead = dev.get("lead")
    dev.get("deputy")
    if c["profiles"].get(lead, {}).get("report_to") != "A0":
        errors.append("部门主官 %s report_to 应为 A0（实际 %r）"
                      % (lead, c["profiles"].get(lead, {}).get("report_to")))
    if c["a0a"] and c["a0a"].get("report_to") != lead:
        errors.append("A0A 交付协调 report_to 应为 %s（实际 %r）"
                      % (lead, c["a0a"].get("report_to")))
    for gid, g in c["groups"].items():
        glead = g.get("lead")
        gp = c["profiles"].get(glead) or {}
        if gp.get("grade") != "E3":
            errors.append("%s 组长 %s 职级=%r（需 E3）" % (gid, glead, gp.get("grade")))
        if gp.get("report_to") != lead:
            errors.append("%s 组长 %s report_to 应为 %s（实际 %r）"
                          % (gid, glead, lead, gp.get("report_to")))
        for m in g.get("members") or []:
            mp = c["profiles"].get(m) or {}
            if mp.get("report_to") != glead:
                errors.append("%s 组员 %s report_to 应为 %s（实际 %r）"
                              % (gid, m, glead, mp.get("report_to")))
            if mp.get("group") != gid:
                errors.append("%s 组员 %s group 应为 %s（实际 %r）"
                              % (gid, m, gid, mp.get("group")))
    for sid, p in c["profiles"].items():
        if sid == lead:
            continue
        if not p.get("report_to"):
            errors.append("%s 缺 report_to" % sid)
        if not p.get("org_role"):
            errors.append("%s 缺 org_role" % sid)
        if not p.get("group") and sid != lead:
            errors.append("%s 缺 group" % sid)
    return errors, c


def _name(p, fallback):
    return (p or {}).get("cn_name") or fallback


def render_md(org, c=None):
    c = c or collect(org)
    dev = c["dev"]
    prof = c["profiles"]
    lead = dev.get("lead")
    lp = prof.get(lead) or {}
    lines = ["# 研发部组织架构（将作监）", "",
             "> 实线=行政/指挥；虚线=独立质量/审计/治理与项目矩阵；矩阵不占研发指挥链。", "",
             "```text",
             "老田（人类所有权 / L2 最终批准）",
             "  └─ A0 %s（研发总负责人：目标/优先级/跨部门/L2 转呈）"
             % _name(c["a0"], "田丰"),
             "       └─ %s %s（研发部主官 · 架构终审 · L1_dev_max）"
             % (lead, _name(lp, lead)),
             ]
    if c["a0a"]:
        lines.append("            ├─ A0A %s（交付协调 0.5，虚线汇报）"
                     % _name(c["a0a"], "A0A"))
    else:
        lines.append("            ├─ A0A（交付协调 0.5）")
    for idx, gid in enumerate(("G1", "G2")):
        g = c["groups"].get(gid)
        if not g:
            continue
        prefix = "            ├─" if idx == 0 else "            └─"
        child = "            │   " if idx == 0 else "                "
        lines.append("%s %s %s（组长 %s）"
                     % (prefix, gid, g.get("name", ""),
                        _name(prof.get(g.get("lead")), g.get("lead"))))
        members = g.get("members") or []
        for j, m in enumerate(members):
            mp = prof.get(m) or {}
            tail = "├─" if j < len(members) - 1 else "└─"
            lines.append("%s%s %s %s（%s）"
                         % (child, tail, m, _name(mp, m),
                            mp.get("discipline", "")))
    lines += ["```", "",
              "## 独立线（不占研发指挥链）", ""]
    if c["qa1"]:
        lines.append("- **QA1 %s 质量门**（质量工程部）：独立验收；ARCH1/组长不得代替质量门。"
                     % _name(c["qa1"], ""))
    if c["guanyu"]:
        lines.append("- **关羽（GUANYU）代码审计**：洁净上下文评审/审计，ARCH1 不自审。")
    if c["zhaoyun"]:
        lines.append("- **赵云（ZHAOYUN）治理席**：不占编，只产建议与证据，不裁决。")
    lines += ["", "## 项目矩阵（不改变行政归属）", ""]
    if c["matrix"]:
        for sid, rows in c["matrix"].items():
            depts = "、".join("%s(%s)" % (r["dept"], r["role_profile"])
                             for r in rows)
            lines.append("- **%s %s**：%s" % (sid, _name(prof.get(sid), sid), depts))
        lines += ["", "> 矩阵任务按项目部门派单，但人的行政归属/考核仍在研发部→组；"
                  "不得以矩阵角色绕过组长/ARCH1。"]
    else:
        lines.append("- 无")
    return "\n".join(lines) + "\n"


def render_html(org, c=None):
    c = c or collect(org)
    dev = c["dev"]
    prof = c["profiles"]
    lead = dev.get("lead")
    lp = prof.get(lead) or {}
    g1 = c["groups"].get("G1") or {}
    g2 = c["groups"].get("G2") or {}

    def box(sid, p, extra=""):
        return ("<div class='node'><b>%s %s</b><span>%s</span>%s</div>"
                % (html.escape(sid), html.escape(_name(p, sid)),
                   html.escape((p or {}).get("role_title", "") or ""), extra))

    def group_box(gid, g):
        if not g:
            return ""
        leadp = prof.get(g.get("lead")) or {}
        members = "".join(box(m, prof.get(m)) for m in g.get("members") or [])
        return ("<div class='group'><h3>%s %s</h3><div class='lead'>%s</div>"
                "<div class='members'>%s</div></div>"
                % (gid, html.escape(g.get("name", "")),
                   box(g.get("lead"), leadp), members))

    matrix = ""
    if c["matrix"]:
        rows = []
        for sid, rr in c["matrix"].items():
            rows.append("<li><b>%s %s</b>：%s</li>"
                        % (html.escape(sid), html.escape(_name(prof.get(sid), sid)),
                           html.escape("、".join("%s(%s)" % (r["dept"], r["role_profile"])
                                                for r in rr))))
        matrix = "<ul>%s</ul>" % "".join(rows)
    dashed = ""
    if c["a0a"]:
        dashed = ("<div class='dashed'>A0A %s（交付协调 0.5，虚线汇报 → %s）</div>"
                  % (html.escape(_name(c["a0a"], "A0A")), html.escape(lead)))
    indep = ["QA1 质量门（质量工程部）",
             "关羽 GUANYU 代码审计（洁净评审）",
             "赵云 ZHAOYUN 治理席（不占编、只产建议）"]
    parts = [
        "<!DOCTYPE html>",
        "<html lang='zh-CN'>",
        "<head>",
        "<meta charset='utf-8'>",
        "<title>研发部组织架构</title>",
        "<link rel='stylesheet' href='/static/tokens.css'>",
        "<script src='/static/nav.js' defer></script>",
        "<style>",
        "body{background:var(--bg,#0d1117);color:var(--fg,#e6edf3;"
        "font-family:'Segoe UI','Microsoft YaHei',sans-serif;padding:24px}",
        "h1{font-size:20px}h3{margin:4px 0 8px;font-size:14px;color:#8b949e}",
        ".top{max-width:980px;margin:0 auto}",
        ".node{display:inline-block;border:1px solid var(--border,#30363d);"
        "border-radius:8px;padding:6px 10px;margin:4px;"
        "background:var(--panel,#161b22);font-size:12px}",
        ".node span{color:#8b949e;margin-left:6px}",
        ".group{border:1px dashed var(--border,#30363d);border-radius:10px;"
        "padding:10px;margin:12px 0}",
        ".lead{margin:6px 0}.members{margin-left:12px}",
        ".dashed{color:#8b949e;font-size:12px;margin:6px 0}",
        "ul{font-size:12px;color:#c9d1d9}",
        "</style>",
        "</head>",
        "<body>",
        "<div class='top'>",
        "<h1>研发部组织架构 · 将作监</h1>",
        "<p class='node'><b>老田</b><span>人类所有权 / L2 最终批准</span></p>",
        "<div>└─ " + box("A0", c["a0"], "<span>研发总负责人</span>") + "</div>",
        "<div>" + dashed + "</div>",
        "<p class='node'><b>%s %s</b><span>研发部主官 · 架构终审 · L1_dev_max</span></p>"
        % (html.escape(lead), html.escape(_name(lp, lead))),
        "<h3>G1 / G2</h3>",
        group_box("G1", g1),
        group_box("G2", g2),
        "<h3>独立线（不占研发指挥链）</h3>",
        "<ul>" + "".join("<li>%s</li>" % html.escape(x) for x in indep) + "</ul>",
        "<h3>项目矩阵（不改变行政归属）</h3>",
        matrix,
        "<p class='dashed'>矩阵任务按项目部门派单，行政归属/考核仍在研发部→组；"
        "不得绕过组长/ARCH1。</p>",
        "</div>",
        "</body>",
        "</html>",
    ]
    return "\n".join(parts) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="研发部组织架构图生成/校验")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--md", action="store_true")
    ap.add_argument("--html", action="store_true")
    ap.add_argument("--out-md", default=str(OUT_MD))
    ap.add_argument("--out-html", default=str(OUT_HTML))
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    org = load(ORG) or {}
    errors, c = validate(org)
    if args.check:
        out = Path(args.out_html)
        ok = not errors and out.exists()
        text = out.read_text(encoding="utf-8") if out.exists() else ""
        ok = ok and all(k in text for k in ("研发部组织架构", "G1", "G2", "tokens.css"))
        print(("PASS" if ok else "FAIL") + "：" +
              ("组织架构一致" if not errors else "；".join(errors)))
        return 0 if ok else 1
    if args.json:
        print(json.dumps({"errors": errors,
                          "seats": {sid: {k: p.get(k) for k in
                                          ("cn_name", "role_title", "discipline",
                                           "group", "org_role", "report_to", "grade")}
                                    for sid, p in c["profiles"].items()},
                          "matrix": c["matrix"]},
                         ensure_ascii=False, indent=1))
        return 0
    md = render_md(org, c)
    if args.md:
        print(md)
        return 0 if not errors else 1
    if args.html:
        print(render_html(org, c))
        return 0 if not errors else 1
    out_md = Path(args.out_md)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(md, encoding="utf-8")
    out_html = Path(args.out_html)
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(render_html(org, c), encoding="utf-8")
    print("org_chart -> %s / %s" % (out_md, out_html))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
