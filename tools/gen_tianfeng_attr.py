# -*- coding: utf-8 -*-
"""生成田丰（A0）属性页：legion_command/static/tianfeng.html。

数据源（只读）：SOUL/SEAT_CARD/CAPABILITIES/config、org.json A0、审计结果、
任务板、证据包、GLM 探测日志、8787 健康。页面为静态快照，刷新=重跑本脚本。
"""
from __future__ import annotations

import html
import json
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from _repo_paths import repo_root  # noqa: E402

REPO = repo_root(HERE)
HOME = Path.home() / ".hermes" / "profiles" / "tianfeng"
# 注意：线上页面已改为动态 API（/api/tianfeng）。本脚本只生成静态参考快照，
# 默认写 work/，不要覆盖 static/tianfeng.html（除非显式 TIANFENG_ATTR_OUT 指定）。
import os  # noqa: E402
import logging
# safe_path=true 时脚本目录不入 sys.path：把本树根加入 path（helper 同目录）
import sys as _sys
from pathlib import Path as _Path
for _p in _Path(__file__).resolve().parents:
    if (_p / "safe_urlopen.py").exists():
        _sys.path.insert(0, str(_p))
        break

try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包导入时
    try:
        from safe_urlopen import safe_urlopen
    except ImportError:  # 作为包/仓库根导入时
        from tools.safe_urlopen import safe_urlopen

OUT = Path(os.environ.get("TIANFENG_ATTR_OUT",
                          str(REPO / "work" / "tianfeng_attr_static.html")))


def esc(x) -> str:
    return html.escape("" if x is None else str(x))


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def http_health(url: str, timeout=3):
    try:
        with safe_urlopen(url, timeout=timeout) as r:
            return r.status
    except Exception:
        return None


def port_pid(port: int):
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=10).stdout
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[1].endswith(":%d" % port) and parts[3] == "LISTENING":
                return parts[4]
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "tools/gen_tianfeng_attr.py", exc)
    return "—"


def run_audit():
    try:
        cp = subprocess.run([sys.executable, "-X", "utf8", "tools/seat_system_audit.py",
                             "--seat", "tianfeng", "--json"],
                            cwd=str(REPO), capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=60)
        text = cp.stdout
        start = text.find("{")
        data = json.loads(text[start:]) if start >= 0 else {}
        summary = data.get("summary") or {}
        checks = data.get("checks") or []
        if summary:
            return {"pass": int(summary.get("pass", 0)),
                    "warn": int(summary.get("warn", 0)),
                    "fail": int(summary.get("fail", 0)),
                    "verdict": data.get("verdict", "")}
        return {"pass": sum(1 for c in checks if c.get("level") == "pass"),
                "warn": sum(1 for c in checks if c.get("level") == "warn"),
                "fail": sum(1 for c in checks if c.get("level") == "fail"),
                "verdict": data.get("verdict", "")}
    except Exception as e:  # noqa: BLE001
        return {"pass": 0, "warn": 0, "fail": -1, "verdict": "audit_error:%s" % e}


caps_raw = load_json(HOME / "skills" / "CAPABILITIES.json", {})
caps = caps_raw.get("capabilities", [])
card = load_json(HOME / "SEAT_CARD.json", {})
soul = (HOME / "SOUL.md").read_text(encoding="utf-8", errors="replace") if (HOME / "SOUL.md").exists() else ""
soul_title = next((ln.lstrip("# ").strip() for ln in soul.splitlines() if ln.startswith("# ")), "田丰")
cfg_text = (HOME / "config.yaml").read_text(encoding="utf-8", errors="replace") if (HOME / "config.yaml").exists() else ""
toolsets = []
in_ts = False
for ln in cfg_text.splitlines():
    s = ln.strip()
    if s.startswith("platform_toolsets:"):
        in_ts = True
        continue
    if in_ts:
        if s.startswith("- "):
            toolsets.append(s[2:].strip())
        elif s and not s.startswith("#") and not s.startswith("-"):
            break

org = load_json(REPO / "agent_platform" / "registry" / "org.json", {})
a0_prof = {}
for dept in org.get("departments", []):
    if dept.get("dept") == "chief_line":
        for p in dept.get("agent_profiles", []):
            if p.get("agent_id") == "A0":
                a0_prof = p
role_model = a0_prof.get("role_model") or {}
authority = role_model.get("decision_authority") or {}
charter = a0_prof.get("charter_detail") or "—"
attrs = a0_prof.get("attributes") or {}

audit = run_audit()
health = http_health("http://127.0.0.1:8787/health")
pid = port_pid(8787)

tasks = load_json(REPO / ".worktrees" / "tasks.json", {})
doing = sum(1 for v in tasks.values() if isinstance(v, dict) and v.get("status") == "doing")

briefs = sorted((REPO / "work" / "seat_brief").glob("evidence_packet_*.json"))
latest_brief = briefs[-1].name if briefs else "—"
glm_lines = []
glm_log = REPO / "work" / "glm_probe" / "retry_log.jsonl"
if glm_log.exists():
    glm_lines = glm_log.read_text(encoding="utf-8", errors="replace").strip().splitlines()
glm_last = json.loads(glm_lines[-1]) if glm_lines else {}
glm_state = "可用" if (REPO / "work" / "glm_probe" / "JUDGMENT_READY.txt").exists() else (
    "429 降级" if "429" in str(glm_last.get("error")) else "未知")
glm_time = glm_last.get("at", "—")

gen_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")

rows_identity = [
    ("定位", "产品经理 + 研发（全在性人才）：取数→方案/架构/代码参考→拆单派发→AI 自动执行→验收；无人能定案时做最优决策并指挥其他 AI 执行"),
    ("编制", "org.json A0（参谋长）+ Hermes profile tianfeng（席位门面）；不占部门编制，全组织向其汇报"),
    ("纪律", "L0/L1 自主决策执行；仅 L2（生产/客户消息/资金/审批/宿主机危险操作/上线/真实数据删除/密钥）报老田；不亲自交付生产代码"),
    ("区分", "A0=田丰本席；A0A=参谋长助理（只调度不决策）；ZHAOYUN=治理席（不占编）"),
]

chips = [
    ("SOUL", "v3 · 产品/研发", "good"),
    ("能力", "%d/%d" % (len(caps), len(caps)), "good"),
    ("审计", "%d 通过 / %d 失败" % (audit["pass"], max(audit["fail"], 0)), "good" if audit["fail"] == 0 else "bad"),
    ("决策", "L0/L1 自主 · L2 报批", ""),
    ("判读", glm_state, "warn" if "429" in glm_state else "good"),
    ("Hermes", "8787 PID %s" % pid, "good" if health == 200 else "bad"),
]

cards = ""
for c in caps:
    cards += ("<tr><td><b>%s</b></td><td>%s</td><td class='muted'>%s</td>"
              "<td><span class='pill %s'>%s</span></td></tr>\n") % (
        esc(c.get("name")), esc(c.get("what")), esc(c.get("boundary")),
        "ok" if c.get("status") == "ready" else "pend", esc(c.get("status")))

identity = "".join(
    "<div class='row'><span class='k'>%s</span><span class='v'>%s</span></div>" % (esc(k), esc(v))
    for k, v in rows_identity)

status = [
    ("Hermes profile", "tianfeng（localhost:8787，PID %s，health %s）" % (pid, health)),
    ("SOUL", soul_title),
    ("能力清单", "%d 项 / %d ready · 技能卡 %d 张" % (len(caps), sum(1 for c in caps if c.get("status") == "ready"), len(list((HOME / "skills").glob("*/SKILL.md"))))),
    ("工具集", ", ".join(toolsets) or "—"),
    ("席位卡", "base=%s" % card.get("base")),
    ("审计", "pass %d / warn %d / fail %d（%s）" % (audit["pass"], audit["warn"], max(audit["fail"], 0), audit["verdict"])),
    ("任务板", "doing %d 单（全平台账本）" % doing),
    ("最新证据包", latest_brief),
    ("GLM 判读", "%s（最近 %s）" % (glm_state, glm_time)),
    ("双锚", "田丰 08:45 / 17:45（S4U，battery-off）"),
]
status_html = "".join(
    "<div class='row'><span class='k'>%s</span><span class='v'>%s</span></div>" % (esc(k), esc(v))
    for k, v in status)

auth_rows = [
    ("L0 自主", "常规研发/文档/取数/测试/方案/代码参考/任务拆解与派发（开发环境）", "直接执行 · 事件+chief/decision 留痕", "否"),
    ("L1 执行后通报", "跨部门派单/非生产配置/开发环境计划任务", "可回滚即执行 · 执行后即时报", "否"),
    ("L2 必须先批", "生产/客户消息、资金/交易/审批、宿主机危险操作、上线/真实数据删除/密钥", "五要素 + 老田明确批准；沉默≠同意", "是"),
]
auth_html = "".join(
    "<tr><td><b>%s</b></td><td>%s</td><td>%s</td><td>%s</td></tr>" % tuple(esc(x) for x in row)
    for row in auth_rows)

identity_table = """
<table>
<thead><tr><th>对象</th><th>载体</th><th>编制/身份</th><th>用途</th></tr></thead>
<tbody>
<tr><td>A0</td><td>org.json chief_line + Hermes profile</td><td>org 参谋长 + profile 角色</td><td>产品/研发/决策/编排（本页）</td></tr>
<tr><td>A0A</td><td>agent_platform team_chief</td><td>org 编制成员</td><td>参谋长助理：只调度不决策</td></tr>
<tr><td>DSH</td><td>DeepSeek Harness 桌面版</td><td>执行载体</td><td>会话/子代理执行</td></tr>
</tbody></table>
"""

html_doc = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>田丰 · 产品/研发席</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#060F1D;font-family:-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;color:#E8F0F8;min-height:100vh}
header{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:14px 22px;border-bottom:.5px solid #1E3A5F;flex-wrap:wrap}
.brand{display:flex;align-items:center;gap:10px}
.avatar{width:34px;height:34px;border-radius:10px;background:#0F2440;border:1px solid #1D9E75;display:flex;align-items:center;justify-content:center;font-size:16px}
h1{font-size:17px;font-weight:500}.sub{font-size:11.5px;color:#8FA8C0}
.chips{display:flex;gap:8px;flex-wrap:wrap}
.chip{font-size:11.5px;padding:6px 10px;border-radius:999px;border:.5px solid #1E3A5F;color:#8FA8C0}
.chip b{color:#4FD1A5;font-weight:500}.chip.warn b{color:#EF9F27}.chip.bad b{color:#E24B4A}
main{padding:18px 22px 26px;display:flex;flex-direction:column;gap:14px}
.grid{display:grid;grid-template-columns:1.15fr 1fr;gap:14px}
@media(max-width:980px){.grid{grid-template-columns:1fr}}
section{background:#0A1626;border:.5px solid #1E3A5F;border-radius:12px;padding:14px 16px}
section h2{font-size:13px;font-weight:500;margin-bottom:10px;display:flex;gap:8px;align-items:center}
section h2 span{color:#5A7190;font-size:11px;font-weight:400}
.row{display:flex;justify-content:space-between;gap:10px;padding:7px 0;border-bottom:.5px dashed #16304F;font-size:12.5px}
.row:last-child{border-bottom:0}.row .k{color:#8FA8C0;flex:0 0 auto}.row .v{text-align:right;word-break:break-all}
table{width:100%;border-collapse:collapse;font-size:12px}
th{text-align:left;color:#5A7190;font-weight:400;padding:6px;border-bottom:.5px solid #1E3A5F}
td{padding:6px;border-bottom:.5px dashed #16304F;vertical-align:top}
tr:last-child td{border-bottom:0}
.pill{display:inline-block;font-size:10.5px;padding:2px 8px;border-radius:999px;border:.5px solid #1E3A5F;color:#8FA8C0}
.pill.ok{color:#4FD1A5;border-color:#1D9E75;background:#0F2440}.pill.pend{color:#EF9F27;border-color:#7A5A1E}
.muted{color:#5A7190;font-size:11px}
.links{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px}
.links a{color:#4FD1A5;text-decoration:none;font-size:12px;border:.5px solid #1E3A5F;border-radius:8px;padding:6px 10px}
footer{padding:10px 22px 16px;border-top:.5px solid #1E3A5F;color:#5A7190;font-size:11px;display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap}
button.refresh{border:.5px solid #1E3A5F;background:none;color:#8FA8C0;font-size:12px;padding:6px 12px;border-radius:8px;cursor:pointer;font-family:inherit}
</style></head><body>
<header>
<div class="brand"><div class="avatar">田</div><div><h1>田丰 · 产品经理+研发（全在性）</h1>
<div class="sub">A0 · 参谋长 / 产品/研发席 · 不亲自交付代码 · 指挥 AI 自动执行</div></div></div>
<div class="chips">__CHIPS__</div>
<button class="refresh" onclick="location.reload()">刷新</button>
</header>
<main>
<div class="grid">
<section><h2>角色与边界 <span>2026-10-27 v3 定调</span></h2>__IDENTITY__<div style="margin-top:8px">__IDENTITY_TABLE__</div></section>
<section><h2>运行状态 <span>Hermes / 能力 / 审计 / 双锚</span></h2>__STATUS__</section>
</div>
<section><h2>决策授权 <span>L0/L1 自主 · L2 报老田（不逐项点确定）</span></h2>
<table><thead><tr><th style="width:140px">级别</th><th>范围</th><th>动作</th><th style="width:50px">找老田</th></tr></thead><tbody>__AUTH__</tbody></table></section>
<section><h2>能力卡 <span>__CAPCOUNT__ / CAPABILITIES.json</span></h2>
<table><thead><tr><th style="width:160px">能力</th><th>说明</th><th>边界</th><th style="width:80px">状态</th></tr></thead><tbody>__CARDS__</tbody></table></section>
<section><h2>执行闭环 <span>取数→方案/架构/代码参考→拆单→派 AI→验收</span></h2>
<div class="row"><span class="k">代码哲学</span><span class="v">代码是思路载体：写参考实现/伪代码/契约，执行 AI 落地，田丰 review 与验收</span></div>
<div class="row"><span class="k">编排工具</span><span class="v">devtask 拆单 · delegation 派工 · events 留痕 · chief/decision 记决策</span></div>
<div class="row"><span class="k">能力入口</span><span class="v">python -X utf8 tools/seat_evidence_packet.py（证据包）</span></div>
<div class="links">
<a href="http://127.0.0.1:8787/?profile=tianfeng" target="_blank">Hermes 田丰</a>
<a href="http://127.0.0.1:8734/ide" target="_blank">IDE 角色卡</a>
<a href="http://127.0.0.1:8734/legion" target="_blank">军团作战大屏</a>
</div></section>
</main>
<footer><span>数据源：SOUL.md / SEAT_CARD / CAPABILITIES / org.json / 审计 / tasks / 证据包 · 静态快照，重跑 gen_tianfeng_attr.py 刷新</span><span>生成 __GEN_AT__</span></footer>
</body></html>"""

chips_html = "".join("<span class='chip %s'>%s <b>%s</b></span>" % (s, esc(k), esc(v)) for k, v, s in chips)
doc = (html_doc
       .replace("__CHIPS__", chips_html)
       .replace("__IDENTITY__", identity)
       .replace("__IDENTITY_TABLE__", identity_table)
       .replace("__STATUS__", status_html)
       .replace("__AUTH__", auth_html)
       .replace("__CARDS__", cards)
       .replace("__CAPCOUNT__", "%d 项" % len(caps))
       .replace("__GEN_AT__", esc(gen_at)))
OUT.write_text(doc, encoding="utf-8")
print("wrote", OUT, "bytes", len(doc.encode("utf-8")), "caps", len(caps))
