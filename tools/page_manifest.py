#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""页面清单（Page Manifest）机器版 v0 —— 零界 UI 宪法 §10 / 页面清单 v0 的落地工具。

用途：
- 把 `portal` 生产页集（123 页）登记为机器可校验的 `portal/config/page_manifest.json`；
- `--check` 做四类校验：页面缺 manifest / manifest 多余 / 路由漂移 / active 页缺
  tokens·nav.js·m.js 依赖（另含 schema、三态备注、入口职责、路由基线对账）；
- 页面清单与 `portal_route_inventory` 路由基线（tools/portal_route_baseline.json）
  对账：基线 sha256/条数变化即视为漂移，需同批 `--generate` 刷新。

范围（scan 口径，见 UI 宪法 §1 / 页面清单 doc §2）：
  portal 根 *.html + portal/m/**/*.html + portal/protos/*.html +
  portal/czsc-eval/*.html + portal/chanlun-learn-report/*.html + portal/quant/*.html
  排除：portal/static/**（资产/静态模板）、portal/_templates/**（壳模板）、
  portal/work/**（回滚/未发布）、portal/data/**、portal/picks_public/**（发布生成物）、
  rollback/backup/_old/archive 等备份目录，以及运行时生成页（agent_board.html）。

用法：
  python tools/page_manifest.py --generate          # 写 portal/config/page_manifest.json
  python tools/page_manifest.py --generate --refresh # 忽略现有条目的人工字段，按内置判定重算
  python tools/page_manifest.py --check             # 校验（差异 exit 1）
  python tools/page_manifest.py --check --skip-route-baseline   # 跳过路由基线对账
  python tools/page_manifest.py --check --json      # 机器可读校验结果
  python tools/page_manifest.py --list              # 列表（--status/--family 过滤）
  python tools/page_manifest.py --json              # 打印 manifest JSON
  python tools/page_manifest.py --root <REPO> ...   # 指定仓库根（测试/临时树用）

退出码：
  0 = 通过/成功；1 = 校验失败（有 violation）；2 = 用法/环境错误（manifest 缺失等）。
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):  # GBK 控制台兼容（同 tools/portal_route_inventory.py P132）
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

SCHEMA = "lingjie/page-manifest/v0"
TOOL_VERSION = "v0.1"
DEFAULT_MANIFEST_REL = "portal/config/page_manifest.json"
ROUTE_BASELINE_REL = "tools/portal_route_baseline.json"
NGINX_CONF_REL = "portal/nginx.conf"
NAV_SOURCE_REL = "portal/api_common.py"

# ---- 扫描口径（v0）----
SCAN_DIRS = (
    "portal",
    "portal/m",
    "portal/protos",
    "portal/czsc-eval",
    "portal/chanlun-learn-report",
    "portal/quant",
)
EXCLUDE_DIR_NAMES = {
    "static", "_templates", "work", "data", "picks_public", "tests", "fixtures", "logs",
    "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache", ".workbuddy", "private",
    "kg", "docs", "briefs", "themes", "assets",
}
EXCLUDE_DIR_RE = re.compile(r"(rollback|backup|_bak|\.bak|_old|archive|snapshot|unpublished)", re.I)
EXCLUDE_FILE_NAMES = {"agent_board.html"}  # 运行时生成页（refresh_agent_board 产物，生成物不入清单）
# 不入清单的具体文件（仓库根相对路径）：churn 的构建/运行时产物，文件存在但不在 git 追踪
EXCLUDE_REL_FILES = {
    "portal/agent_board.html",   # 运行时生成页
    "portal/quant/app.html",     # L1 Quant 构建产物（未跟踪；nginx /quant/ 302 目标）
}
EXCLUDE_FILE_RE = re.compile(r"(\.bak|\.orig|\.old|~$|copy\.html$|^tmp_)", re.I)

FAMILIES = ("cockpit", "os", "portal", "h5", "adapter", "legacy", "review")
THEMES = ("light-cockpit", "dark-os", "h5", "adapter:hermes", "adapter:quant", "legacy-dark")
SHELLS = ("cockpit-shell", "os-shell", "h5-shell", "none")
NAVS = ("nav-js", "own-6", "m-shell", "none")
AUTHS = ("none", "oidc", "portal-token")
STATUSES = ("active", "legacy", "review", "archived")
REQUIRED_FIELDS = ("id", "path", "family", "title", "theme", "shell", "nav", "auth",
                   "status", "owner", "apis", "note", "source")
LEGACY_NOTE_HINTS = ("迁移", "重定向", "退役", "保留", "归档", "待", "动态页", "旧")
STATUS_ORDER = {s: i for i, s in enumerate(STATUSES)}

# ---- 入口职责（UI 宪法 §1 canonical map，先定死）----
ENTRY_MAP = [
    {"entry": "/", "source": "portal/home_v3.html", "role": "默认首页（个人 OS 总览）",
     "theme": "dark-os", "nav": "nav-js", "status": "active",
     "note": "nginx / → home_v3.html（try_files）；四入口中唯一默认入口"},
    {"entry": "/portal", "source": "dynamic:portal_api", "role": "旧动态门户/运维入口",
     "theme": "legacy-dark", "nav": "nav-js", "status": "legacy",
     "note": "portal_api 动态渲染，无静态文件；保留运维入口，不再作为视觉基准"},
    {"entry": "/zero_proto.html", "source": "portal/zero_proto.html", "role": "人看首页（浅色驾驶舱）",
     "theme": "light-cockpit", "nav": "own-6", "status": "active",
     "note": "固定 6 项自有导航；按宪法 §3.1 不加载 nav.js（现存注入待 W1 移除）"},
    {"entry": "/os-entry", "source": "portal/os_entry_v9_prod.html", "role": "深色工作台/深屏（auth）",
     "theme": "dark-os", "nav": "nav-js", "status": "active",
     "note": "nginx /os-entry → os_entry_v9_prod.html，auth_request OIDC 保护"},
    {"entry": "/os-board", "source": "portal/os.html", "role": "实况深页",
     "theme": "dark-os", "nav": "nav-js", "status": "active",
     "note": "nginx /os-board → os.html；当前 GitHub Dark 基底，W2 迁 dark-os"},
    {"entry": "/hq", "source": "portal/hq.html", "role": "指挥部（只读控制面）",
     "theme": "dark-os", "nav": "nav-js", "status": "active",
     "note": "单入口只读；数据面见 portal/config/hq_manifest.json"},
    {"entry": "/m/*", "source": "dynamic:m-prefix", "role": "移动 H5 套件",
     "theme": "h5", "nav": "m-shell", "status": "active",
     "note": "nginx /m/ → m/index.html，/m/<x> → m/<x>.html；m.js 壳导航"},
    {"entry": "/login", "source": "portal/login.html", "role": "登录入口",
     "theme": "dark-os", "nav": "nav-js", "status": "active",
     "note": "auth-gateway 登录页；页面免鉴权，登录后写操作走 portal-token"},
    {"entry": "/home.html", "source": "portal/home.html", "role": "旧首页",
     "theme": "legacy-dark", "nav": "nav-js", "status": "legacy",
     "note": "保留入口，迁移/重定向到 /（L2 变更，不在本清单工具范围）"},
    {"entry": "/quant/", "source": "portal/quant/index.html", "role": "Quant React 页面包（适配器例外）",
     "theme": "adapter:quant", "nav": "none", "status": "active",
     "note": "宪法 §3.2 适配器例外；现行 nginx /quant/ 302 → /quant/app.html（构建产物未入库），"
             "追踪的 index.html 仅为旧构建产物（legacy）"},
    {"entry": "/hermes/", "source": "external:hermes", "role": "Hermes 个人助理（8787 同源反代）",
     "theme": "adapter:hermes", "nav": "none", "status": "active",
     "note": "宪法 §3.2 适配器例外；外部独立 UI，不在 portal HTML 扫描范围"},
]
REQUIRED_ENTRIES = ("/", "/portal", "/zero_proto.html", "/os-entry", "/os-board", "/hq",
                    "/m/*", "/login", "/home.html")

# ---- 人工判定（仅 --generate 首次/--refresh 使用；其后以 manifest 落盘值为准）----
# 规则：非 active 页必须逐页登记（不确定一律 review/legacy + note，禁伪造 active）；
#       active 页只在此处登记需要覆盖默认字段/补充说明的（入口页、适配器、auth 页等）。
CURATED = {
    # —— 入口职责（UI 宪法 §1）——
    "portal/home_v3.html": dict(family="os", theme="dark-os", shell="os-shell", nav="nav-js",
                                auth="none", status="active",
                                note="默认首页（nginx / → home_v3.html try_files）；dark-os 为迁移目标"),
    "portal/zero_proto.html": dict(
        family="cockpit", theme="light-cockpit", shell="cockpit-shell", nav="own-6",
        auth="none", status="active",
        note="人看首页（浅色驾驶舱）；宪法 §3.1 要求不加载 nav.js，实测文件仍有 page_shell_gen 注入 1 处，W1 移除"),
    "portal/os_entry_v9_prod.html": dict(
        family="os", theme="dark-os", shell="os-shell", nav="nav-js", auth="oidc", status="active",
        note="深色工作台/深屏主入口（nginx /os-entry → 本文件，auth_request OIDC）"),
    "portal/os.html": dict(
        family="os", theme="dark-os", shell="os-shell", nav="nav-js", auth="oidc", status="active",
        note="实况深页（nginx /os-board → os.html）；当前 GitHub Dark 基底，W2 迁 dark-os 令牌（宪法 §3.1 已知偏差）"),
    "portal/hq.html": dict(
        family="os", theme="dark-os", shell="os-shell", nav="nav-js", auth="oidc", status="active",
        owner="ARCH1",
        note="指挥部只读控制面（nginx /hq，单入口、只读）；数据面 schema 见 portal/config/hq_manifest.json"),
    "portal/login.html": dict(
        family="os", theme="dark-os", shell="os-shell", nav="nav-js", auth="none", status="active",
        note="登录入口（Phase A auth-gateway；页面本身免鉴权、nginx no-store；登录后写操作走 portal-token/X-Portal-Token）"),
    # —— legacy（保留入口，待迁移/重定向）——
    "portal/home.html": dict(
        family="portal", theme="legacy-dark", shell="none", nav="nav-js", auth="none",
        status="legacy",
        note="旧首页（保留入口，迁移/重定向到 /；UI 宪法 §1 legacy-dark）"),
    "portal/index.html": dict(
        family="portal", theme="legacy-dark", shell="none", nav="nav-js", auth="none",
        status="legacy",
        note="旧 index（nginx /hall → index.html；保留入口，迁移/重定向待 L2，页面清单 doc §2.2）"),
    "portal/picks_index.html": dict(
        family="portal", theme="legacy-dark", shell="none", nav="nav-js", auth="none",
        status="legacy",
        note="旧跳转页（nginx /picks_index.html 301 → /picks.html；保留入口，不迁移）"),
    "portal/approvals.html": dict(
        family="os", theme="legacy-dark", shell="none", nav="nav-js", auth="oidc",
        status="legacy",
        note="静态注册表页（nginx /approvals.html 302 → /approvals 8091 动态页）；保留入口，以动态页为准"),
    "portal/capability.html": dict(
        status="legacy", note="静态旧版；nav.js 指向动态 /capability（8091 渲染），静态版保留/待归档"),
    "portal/flow.html": dict(
        status="legacy", note="静态旧版；nav.js 指向动态 /flow（8091），静态版保留/待归档"),
    "portal/org.html": dict(
        status="legacy", note="静态组织架构图；nav.js 指向动态 /org（8091），静态版保留/待归档"),
    "portal/perf.html": dict(
        status="legacy", note="静态绩效看板；nav.js 指向动态 /perf（8091），静态版保留/待归档"),
    "portal/bt_view.html": dict(
        status="legacy", note="静态回测中心旧版；现行 /bt、/btview 为 8091 动态页，静态版保留/待归档"),
    "portal/bt2_view.html": dict(
        status="legacy", note="静态回测框架 v2 旧版；现行动态 /bt2view（8091），静态版保留/待归档"),
    "portal/dev_legion.html": dict(
        status="legacy", note="研发军团作战屏 v1；nav.js 已指向 /dev_legion_v2.html，v1 保留/待归档"),
    "portal/stock_analysis.html": dict(
        status="legacy", note="静态个股分析旧版；现行 /sa 为 8091 动态页，静态版保留/待归档"),
    "portal/dw.html": dict(
        theme="legacy-dark", shell="none", auth="oidc", status="legacy",
        note="数据仓驾驶舱（nginx /dw，OIDC 保护）；未接 tokens（宪法 §11 缺口 13 页之一），W1 补齐后转 active"),
    "portal/h14_shadow.html": dict(
        theme="legacy-dark", shell="none", status="legacy",
        note="H14 影子盘（nav.js 挂 /h14_shadow.html）；未接 tokens（宪法 §11 缺口），W1 补齐后转 active"),
    "portal/kb_cards.html": dict(
        theme="legacy-dark", shell="none", status="legacy",
        note="学习卡知识库（nav.js 挂 /kb_cards.html）；未接 tokens（宪法 §11 缺口），W1 补齐后转 active"),
    "portal/kb_corpus.html": dict(
        theme="legacy-dark", shell="none", status="legacy",
        note="语料知识库（nav.js 挂 /kb_corpus.html）；未接 tokens（宪法 §11 缺口），W1 补齐后转 active"),
    "portal/kb_ingest.html": dict(
        theme="legacy-dark", shell="none", status="legacy",
        note="PDF 知识入库（nav.js 挂 /kb_ingest.html）；未接 tokens（宪法 §11 缺口），W1 补齐后转 active"),
    "portal/kb_reports.html": dict(
        theme="legacy-dark", shell="none", status="legacy",
        note="研报知识库（nav.js 挂 /kb_reports.html）；未接 tokens（宪法 §11 缺口），W1 补齐后转 active"),
    "portal/minervini.html": dict(
        theme="legacy-dark", shell="none", status="legacy",
        note="趋势模板选股（nav.js 挂 /minervini.html）；未接 tokens（宪法 §11 缺口），W1 补齐后转 active"),
    "portal/selection.html": dict(
        theme="legacy-dark", shell="none", status="legacy",
        note="策略库·选股（nav.js 挂 /selection.html）；未接 tokens（宪法 §11 缺口），W1 补齐后转 active"),
    "portal/shadow.html": dict(
        theme="legacy-dark", shell="none", status="legacy",
        note="影子盘总览（nav.js 挂 /shadow.html）；未接 tokens（宪法 §11 缺口），W1 补齐后转 active"),
    "portal/quant/index.html": dict(
        family="adapter", theme="adapter:quant", shell="none", nav="none", auth="none",
        status="legacy",
        note="Quant React 构建产物旧入口（nginx /quant/ 302 → /quant/app.html；app.html 为未跟踪构建产物），index.html 仅存档"),
    # —— review（评审稿/原型，非生产入口；os_entry_v1..v8 已 302 归档）——
    "portal/os_entry.html": dict(
        status="review",
        note="旧版入口总览（nginx 302 → /os-entry，已下线归档；评审稿，不开放直达）"),
    "portal/os_entry_v4.html": dict(
        status="review", note="主节点工作台 v4 评审稿·非生产页（nginx 302 → /os-entry，归档）"),
    "portal/os_entry_v5.html": dict(
        status="review", note="主节点工作台 v5 业务重组版评审稿·非生产页（nginx 302 → /os-entry，归档）"),
    "portal/os_entry_v6.html": dict(
        status="review", note="AI 主入口 v6 评审稿（nginx 302 → /os-entry，归档）"),
    "portal/os_entry_v7.html": dict(
        status="review", note="诸葛亮联动评审稿 v7（nginx 302 → /os-entry，归档）"),
    "portal/os_entry_v8.html": dict(
        status="review", note="诸葛亮今日工作台 v8 评审稿·非生产页（nginx 302 → /os-entry，归档）"),
    "portal/zero_home.html": dict(
        status="review", note="旧版产品工作台（页面清单 doc §2.3 评审稿归档；未设 nginx 路由，保留不开放）"),
    "portal/os_map.html": dict(
        status="review", note="OS 全板块作战图（页面清单 doc §2.3 评审稿归档）"),
    "portal/os_demo.html": dict(
        status="review", note="个人 OS 指挥大屏 Demo v3（评审演示，非生产入口）"),
    "portal/os_integrated_demo.html": dict(
        status="review", note="集成驾驶舱评审稿（非生产）"),
    "portal/d1_war_room.html": dict(
        status="review", note="量化作战台 v2 产品原型（评审稿，非生产入口）"),
    "portal/sandbox.html": dict(
        status="review", note="研发沙盘 R&D Sandbox（原型/非生产，nginx /sandbox 可直达但未入 nav）"),
    "portal/watch_admin_page.html": dict(
        status="review", note="盯盘多 Agent 管理·只读页（评审稿·非生产）"),
    "portal/os_room_preview.html": dict(
        status="review", note="议事厅房间只读预览（G2 联调，非生产入口）"),
    "portal/os_goal.html": dict(
        status="review", note="目标与事项 G0 骨架（评审稿）"),
    "portal/os_personal.html": dict(
        status="review", note="个人 OS 驾驶舱旧稿（非生产）"),
    "portal/os_software.html": dict(
        status="review", note="软件面工作台 G1b（骨架阶段，未接入 nav）"),
    "portal/os_approval_voice.html": dict(
        status="review", note="语音审批台（实验/评审稿，未接入 nav）"),
    "portal/os_voice_eval.html": dict(
        status="review", note="语音质量门槛评测页（评测工具，非生产入口）"),
    "portal/intel_dossier_skeleton.html": dict(
        status="review", note="情报对象案卷离线骨架（评审稿）"),
    "portal/preview_hq.html": dict(
        auth="oidc", status="review",
        note="指挥部本机只读预览（nginx /preview/hq，OIDC 保护）；非生产入口"),
    "portal/quant_v2_demo.html": dict(
        status="review", note="量化股票 v2 Demo（评审稿，非生产入口）"),
    "portal/card_review.html": dict(
        status="active", note="学习卡审核工具页（nav.js 挂 /card_review.html，100 候选审核）"),
    # protos / czsc-eval / chanlun-learn-report：评审/原型/报告类，非生产入口
    "portal/protos/proto_backtest.html": dict(
        status="review", note="原型 v1 · 回测工作台（待拍板，页面清单 doc §2.3 review）"),
    "portal/protos/proto_push_hub.html": dict(
        status="review", note="推送中台·动线管理台原型（待拍板，doc §2.3 review）"),
    "portal/protos/proto_realtime.html": dict(
        status="review", note="Watch Tower 实时快照原型（doc §2.3 review）"),
    "portal/protos/proto_tail_buy.html": dict(
        status="review", note="原型 v1 · 尾盘买入操作台（待拍板，doc §2.3 review）"),
    "portal/czsc-eval/czsc-p0-eval.html": dict(
        status="review", note="CZSC 接入评估 P0 报告（K-ORG-198，静态证据页，非生产入口）"),
    "portal/czsc-eval/czsc-phase1-plan.html": dict(
        status="review", note="CZSC 接入 Phase 1 执行计划（承 K-ORG-198，静态证据页，非生产入口）"),
    "portal/czsc-eval/czsc-phase1-report.html": dict(
        status="review", note="CZSC Phase 1 无偏复跑报告（K-ORG-200，静态证据页，非生产入口）"),
    "portal/chanlun-learn-report/chanlun-learn-report.html": dict(
        status="review", note="缠论学习环运行报告 L1-L4（静态证据页，非生产入口）"),
    # —— H5 特例 ——
    "portal/m/index.html": dict(note="移动端入口（nginx /m/ → m/index.html；m.js 壳导航、移动优先）"),
    "portal/m/auth/callback.html": dict(
        nav="none", shell="h5-shell", status="active",
        note="飞书 OAuth 回调中转页（JS 跳转 /m/subscription）；无导航壳，仅静态中转"),
}


# ---------- 基础工具 ----------
def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def resolve_root(root=None) -> Path:
    return Path(root).resolve() if root else repo_root()


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _norm_title(text: str, fallback: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    if not m:
        return fallback
    title = re.sub(r"\s+", " ", m.group(1)).strip()
    return title[:120] or fallback


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------- 页面扫描 ----------
def production_files(root=None) -> list:
    """扫描生产页集（排序后返回 Path 列表）。"""
    root = resolve_root(root)
    out = []
    for d in SCAN_DIRS:
        base = root / d
        if not base.is_dir():
            continue
        # m 套件含二级子页（m/auth/callback.html），用 rglob；其它目录只取一级
        candidates = sorted(base.rglob("*.html")) if d == "portal/m" else sorted(base.glob("*.html"))
        for p in candidates:
            rel_parts = p.relative_to(root).parts
            inner = p.relative_to(root / "portal")
            if p.name in EXCLUDE_FILE_NAMES or EXCLUDE_FILE_RE.search(p.name):
                continue
            if _rel(root, p) in EXCLUDE_REL_FILES:
                continue
            if any(part in EXCLUDE_DIR_NAMES for part in rel_parts):
                continue
            if EXCLUDE_DIR_RE.search(inner.as_posix()):
                continue
            out.append(p)
    # 去重（portal 根扫描与子目录扫描不会重叠，这里防御性去重）
    uniq = []
    seen = set()
    for p in out:
        key = str(p.resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)
    return uniq


def scan_page(path: Path) -> dict:
    """扫描单个页面依赖/标题/API（纯静态读）。"""
    text = _read_text(path)
    return {
        "title": _norm_title(text, path.stem),
        "tokens": "/static/tokens.css" in text,
        "os_theme": "os_theme.css" in text,
        "ui_css": "/static/ui/ui.css" in text,
        "ui_js": "/static/ui/ui.js" in text,
        "nav_js": "/static/nav.js" in text,
        "m_js": bool(re.search(r"/m/(?:assets/)?m\.js", text)),
        "m_css": bool(re.search(r"/m/(?:assets/)?m(?:-os)?\.css", text)),
        "shortscreen": "shortscreen.js" in text,
        "portal_token": ("X-Portal-Key" in text or "portal-token" in text.lower()
                         or "portal_token" in text.lower()),
        "apis": sorted(set(re.findall(r"[\"'](/api/[A-Za-z0-9_\-/\.]+)", text))),
        "refresh": bool(re.search(r"http-equiv=[\"']?refresh", text, re.I)),
    }


# ---------- nginx 路由映射（只读解析，不改 nginx）----------
def _iter_location_blocks(lines: list):
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        m = re.match(r"location\s+(?:(=)\s*|(~?\*?)\s*)?(\S+)\s*\{", s)
        if not m:
            i += 1
            continue
        mod = m.group(1) or m.group(2) or ""
        target = m.group(3)
        depth = s.count("{") - s.count("}")
        body = [s]
        j = i + 1
        while j < len(lines) and depth > 0:
            body.append(lines[j])
            depth += lines[j].count("{") - lines[j].count("}")
            j += 1
        yield {"mod": mod, "target": target, "body": "\n".join(body), "line": i + 1}
        i = j


def parse_nginx(conf_path: Path) -> dict:
    """解析 nginx.conf：文件→规范路由/鉴权、显式与正则重定向。

    返回 {"file_routes": {rel: [{"path","auth","line"}]}, "exact_redirects": {url: target},
          "regex_redirects": [{"pattern","target","line"}]}
    """
    out = {"file_routes": {}, "exact_redirects": {}, "regex_redirects": [],
           "present": False}
    if not conf_path.is_file():
        return out
    out["present"] = True
    lines = _read_text(conf_path).splitlines()
    for b in _iter_location_blocks(lines):
        body = b["body"]
        auth = "auth_request" in body
        rets = re.findall(r"return\s+(30[12478])\s+([^\s;]+)", body)
        if b["mod"] == "=":
            path = b["target"]
            files = []
            for tm in re.finditer(r"try_files\s+([^;]+);", body):
                for tok in tm.group(1).split():
                    if tok.startswith("/") and tok.endswith(".html") and "$" not in tok:
                        files.append(tok.lstrip("/"))
                    elif tok == "$uri" and path.endswith(".html"):
                        files.append(path.lstrip("/"))
            if files:
                rec = {"path": path, "auth": auth, "line": b["line"]}
                for f in files:
                    out["file_routes"].setdefault(f, []).append(rec)
            elif rets:
                out["exact_redirects"][path] = rets[0][1]
        elif b["mod"].startswith("~") and rets:
            out["regex_redirects"].append(
                {"pattern": b["target"], "target": rets[0][1], "line": b["line"]})
    return out


def _mount_path(inner: str, nginx: dict) -> tuple:
    """(规范路径, 鉴权, 重定向目标)；inner = portal/ 相对路径。"""
    direct = "/" + inner
    redirect = nginx["exact_redirects"].get(direct)
    if redirect is None:
        for rp in nginx["regex_redirects"]:
            try:
                if re.match(rp["pattern"], direct):
                    redirect = rp["target"]
                    break
            except re.error:
                continue
    routes = nginx["file_routes"].get(inner) or []
    if routes:
        # 主路由：无 .html 后缀优先（短路由），其次短、再字典序
        best = sorted(routes, key=lambda r: (r["path"].endswith(".html"),
                                             len(r["path"]), r["path"]))[0]
        return best["path"], ("oidc" if best["auth"] else "none"), redirect
    if inner == "home_v3.html":
        return "/", "none", redirect
    if inner == "m/index.html":
        return "/m/", "none", redirect
    if inner.startswith("m/") and inner.endswith(".html"):
        return "/m/" + inner[2:-5], "none", redirect
    if inner == "quant/index.html":
        return "/quant/", "none", redirect
    return direct, "none", redirect


# ---------- 页面记录派生 ----------
def _page_id(inner: str) -> str:
    slug = inner[:-5] if inner.endswith(".html") else inner
    slug = re.sub(r"[^A-Za-z0-9_\-/]", "-", slug.replace("/", "_"))
    return "page/" + slug.strip("_-")


def _auto_note(rec: dict, meta: dict, nginx_route: bool, redirect: str) -> str:
    parts = []
    if rec["status"] == "active":
        parts.append("生产页")
    if nginx_route:
        parts.append("nginx 路由 %s → 本页" % rec["path"])
    elif redirect:
        parts.append("直链 %s 重定向 → %s（保留/归档）" % (("/" + rec["source"][len("portal/"):]), redirect))
    else:
        parts.append("静态直链 %s" % rec["path"])
    if rec["status"] == "active" and rec["theme"] == "legacy-dark":
        parts.append("legacy-dark 基底，待 W2 迁 dark-os/light-cockpit")
    if rec["auth"] == "oidc":
        parts.append("OIDC 登录保护")
    elif rec["auth"] == "portal-token":
        parts.append("写操作走 X-Portal-Token")
    if rec["family"] == "h5":
        parts.append("m.js 壳导航、移动优先")
    return "；".join(parts)


def derive_record(root: Path, src: Path, nginx: dict) -> dict:
    rel = _rel(root, src)
    inner = rel[len("portal/"):] if rel.startswith("portal/") else rel
    meta = scan_page(src)
    path, auth, redirect = _mount_path(inner, nginx)
    is_m = inner.startswith("m/")
    is_adapter = inner.startswith("quant/")
    family = "h5" if is_m else ("adapter" if is_adapter else "os")
    if is_m:
        theme = "h5"
    elif is_adapter:
        theme = "adapter:quant"
    else:
        theme = "dark-os" if meta["os_theme"] else "legacy-dark"
    shell = {"h5": "h5-shell", "adapter": "none", "dark-os": "os-shell",
             "legacy-dark": "none"}[theme if theme != "adapter:quant" else "adapter"]
    if is_m:
        nav = "m-shell"
    elif is_adapter:
        nav = "none"
    else:
        nav = "nav-js" if meta["nav_js"] else "none"
    if meta["portal_token"] and auth == "none":
        auth = "portal-token"
    status = "active"
    if not meta["tokens"] and not is_adapter:
        status = "legacy"  # 安全网：缺 tokens 不伪造 active（宪法 §11 缺口页）
    rec = {
        "id": _page_id(inner),
        "path": path,
        "family": family,
        "title": meta["title"],
        "theme": theme,
        "shell": shell,
        "nav": nav,
        "auth": auth,
        "status": status,
        "owner": "A0",
        "apis": meta["apis"],
        "note": "",
        "source": rel,
    }
    curated = CURATED.get(rel)
    if curated:
        rec.update(curated)
    rec["id"] = _page_id(inner)
    rec["path"] = path
    rec["source"] = rel
    rec["title"] = meta["title"]
    if not curated or "note" not in curated:
        rec["note"] = _auto_note(rec, meta, bool(nginx["file_routes"].get(inner)), redirect)
    return rec


def _nav_routes(root: Path) -> set:
    """从 api_common.py DOMAIN_NAV_GROUPS 提取导航路由（只读；缺失返回空集）。"""
    src = root / NAV_SOURCE_REL
    if not src.is_file():
        return set()
    try:
        tree = ast.parse(_read_text(src))
    except SyntaxError:
        return set()
    paths = set()

    def walk(node):
        if isinstance(node, (ast.Tuple, ast.List)):
            for e in node.elts:
                walk(e)
        elif isinstance(node, ast.Dict):
            for e in node.values:
                walk(e)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and node.value.startswith("/"):
            paths.add(node.value.split("#")[0].rstrip("/") or "/")

    for node in tree.body:
        if isinstance(node, ast.Assign) and node.targets and \
                isinstance(node.targets[0], ast.Name) and node.targets[0].id == "DOMAIN_NAV_GROUPS":
            walk(node.value)
    return paths


# ---------- manifest 读写/生成 ----------
def manifest_path(root: Path) -> Path:
    return root / DEFAULT_MANIFEST_REL


def _route_baseline_state(root: Path) -> dict:
    bp = root / ROUTE_BASELINE_REL
    if not bp.is_file():
        return {"present": False, "path": ROUTE_BASELINE_REL}
    raw = _read_text(bp)
    try:
        data = json.loads(raw)
    except ValueError:
        data = {}
    return {
        "present": True,
        "path": ROUTE_BASELINE_REL,
        "generated_at": data.get("generated_at", ""),
        "total": data.get("total", len(data.get("signatures") or [])),
        "sha256": _sha256_text(raw),
    }


def _counts(pages: list) -> dict:
    by_status = {s: 0 for s in STATUSES}
    by_family = {f: 0 for f in FAMILIES}
    for p in pages:
        by_status[p.get("status", "")] = by_status.get(p.get("status", ""), 0) + 1
        by_family[p.get("family", "")] = by_family.get(p.get("family", ""), 0) + 1
    return {"total": len(pages), "by_status": by_status, "by_family": by_family}


def build_manifest(root=None, existing: dict | None = None, refresh: bool = False) -> dict:
    root = resolve_root(root)
    nginx = parse_nginx(root / NGINX_CONF_REL)
    old_by_source = {}
    if existing and not refresh:
        for p in existing.get("pages") or []:
            if isinstance(p, dict) and p.get("source"):
                old_by_source[p["source"]] = p
    pages = []
    preserved = 0
    for src in production_files(root):
        rec = derive_record(root, src, nginx)
        old = old_by_source.get(rec["source"])
        if old:
            preserved += 1
            for key in ("status", "family", "theme", "shell", "nav", "auth", "owner",
                        "note", "apis", "title", "visual_baseline"):
                if key in old:
                    rec[key] = old[key]
        pages.append(rec)
    pages.sort(key=lambda p: (p["path"], p["id"]))
    for rec in pages:
        rec["apis"] = sorted(set(rec.get("apis") or []))
    manifest = {
        "schema": SCHEMA,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "generator": "tools/page_manifest.py %s" % TOOL_VERSION,
        "scan": {
            "root": "portal",
            "dirs": list(SCAN_DIRS),
            "excluded_dirs": sorted(EXCLUDE_DIR_NAMES),
            "excluded_files": sorted(EXCLUDE_FILE_NAMES),
            "exclude_dir_pattern": EXCLUDE_DIR_RE.pattern,
            "note": "生成物/备份/静态模板不入清单；页面新增/删除须同批更新 manifest 与路由基线",
        },
        "route_baseline": _route_baseline_state(root),
        "entry_map": ENTRY_MAP,
        "counts": _counts(pages),
        "pages": pages,
    }
    manifest["_preserved"] = preserved  # 仅供 --generate 打印，写盘前移除
    return manifest


def _strip_private(manifest: dict) -> dict:
    return {k: v for k, v in manifest.items() if not k.startswith("_")}


def write_manifest(path: Path, manifest: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_strip_private(manifest), ensure_ascii=False, indent=1) + "\n",
                    encoding="utf-8", newline="\n")
    return path


def load_manifest(root: Path, path: Path | None = None) -> dict:
    p = path or manifest_path(root)
    return json.loads(_read_text(p))


# ---------- 校验 ----------
def _check_schema(manifest: dict, violations: list) -> None:
    if manifest.get("schema") != SCHEMA:
        violations.append({"code": "schema", "ref": "schema",
                           "message": "schema 须为 %s（当前 %r）" % (SCHEMA, manifest.get("schema"))})
    pages = manifest.get("pages")
    if not isinstance(pages, list) or not pages:
        violations.append({"code": "schema", "ref": "pages", "message": "pages 缺失或为空"})
        return
    seen_id, seen_path, seen_source = {}, {}, {}
    for i, p in enumerate(pages):
        ref = "pages[%d]" % i
        if not isinstance(p, dict):
            violations.append({"code": "schema", "ref": ref, "message": "条目不是对象"})
            continue
        missing = [f for f in REQUIRED_FIELDS if f not in p]
        if missing:
            violations.append({"code": "schema", "ref": ref,
                               "message": "缺字段：%s" % ",".join(missing)})
            continue
        for field, allowed in (("family", FAMILIES), ("theme", THEMES), ("shell", SHELLS),
                               ("nav", NAVS), ("auth", AUTHS), ("status", STATUSES)):
            if p[field] not in allowed:
                violations.append({"code": "schema", "ref": p.get("id", ref),
                                   "message": "%s=%r 不在枚举 %s 内" % (field, p[field], allowed)})
        if not isinstance(p["apis"], list):
            violations.append({"code": "schema", "ref": p.get("id", ref), "message": "apis 须为数组"})
        if not isinstance(p["owner"], str) or not p["owner"].strip():
            violations.append({"code": "schema", "ref": p.get("id", ref), "message": "owner 不能为空"})
        if not isinstance(p["path"], str) or not p["path"].startswith("/") or " " in p["path"]:
            violations.append({"code": "schema", "ref": p.get("id", ref),
                               "message": "path 须以 / 开头且不含空格：%r" % p.get("path")})
        if not isinstance(p["id"], str) or not re.match(r"^page/[A-Za-z0-9][A-Za-z0-9_\-/\.]*$", p["id"]):
            violations.append({"code": "schema", "ref": p.get("id", ref),
                               "message": "id 须形如 page/<slug>"})
        for attr, cache in (("id", seen_id), ("path", seen_path), ("source", seen_source)):
            val = p.get(attr)
            if val in cache:
                violations.append({"code": "duplicate_%s" % attr, "ref": val,
                                   "message": "%s 重复：%r（%s 与 %s）" % (
                                       attr, val, cache[val], p.get("source", ""))})
            else:
                cache[val] = p.get("source", "")


def _check_status_semantics(pages: list, violations: list) -> None:
    for p in pages:
        status = p.get("status")
        ref = p.get("source", p.get("id"))
        note = (p.get("note") or "").strip()
        if status == "active":
            if p.get("theme") in (None, "") or p.get("shell") in (None, ""):
                violations.append({"code": "active_meta", "ref": ref,
                                   "message": "active 页 theme/shell 不得为空"})
            if p.get("nav") in (None, ""):
                violations.append({"code": "active_meta", "ref": ref,
                                   "message": "active 页 nav 不得为空（可用 none）"})
        if status == "legacy" and not note:
            violations.append({"code": "status_note", "ref": ref,
                               "message": "legacy 页必须有迁移/退役备注（note）"})
        if status == "review" and not note:
            violations.append({"code": "status_note", "ref": ref,
                               "message": "review 页必须有评审/归档说明（note）"})


def _check_nav_isolation(pages: list, root: Path, violations: list) -> None:
    nav = _nav_routes(root)
    if not nav:
        return
    for p in pages:
        if p.get("status") not in ("review", "archived"):
            continue
        inner = p.get("source", "")[len("portal/"):]
        cand = {"/" + inner, "/" + (inner[:-5] if inner.endswith(".html") else inner)}
        hit = cand & nav
        if hit:
            violations.append({"code": "nav_isolation", "ref": p.get("source"),
                               "message": "review/archived 页不得出现在导航：%s 命中 %s" % (
                                   p.get("source"), sorted(hit))})


def _check_active_deps(pages: list, root: Path, violations: list) -> None:
    for p in pages:
        if p.get("status") != "active":
            continue
        src = root / p.get("source", "")
        if not src.is_file():
            continue  # extra_page 已报
        meta = scan_page(src)
        ref = p.get("source")
        theme = p.get("theme") or ""
        if not theme.startswith("adapter:") and not meta["tokens"]:
            violations.append({"code": "active_deps", "ref": ref,
                               "message": "active 页缺 tokens.css 依赖（宪法 §6.1）"})
        if p.get("nav") == "nav-js" and not meta["nav_js"]:
            violations.append({"code": "active_deps", "ref": ref,
                               "message": "active 页 nav=nav-js 但文件缺 nav.js 依赖"})
        if p.get("nav") == "m-shell" and not (meta["m_js"] or meta["m_css"]):
            violations.append({"code": "active_deps", "ref": ref,
                               "message": "active 页 nav=m-shell 但文件缺 m.js/m.css 依赖"})


def _check_entry_map(manifest: dict, root: Path, pages: list, violations: list) -> None:
    entries = manifest.get("entry_map")
    if not isinstance(entries, list) or not entries:
        violations.append({"code": "entry_map", "ref": "entry_map", "message": "entry_map 缺失"})
        return
    by_entry = {e.get("entry"): e for e in entries if isinstance(e, dict)}
    for required in REQUIRED_ENTRIES:
        if required not in by_entry:
            violations.append({"code": "entry_map_missing", "ref": required,
                               "message": "入口职责缺失：%s（UI 宪法 §1）" % required})
    by_source = {p.get("source"): p for p in pages}
    for entry, e in by_entry.items():
        if not isinstance(e, dict):
            violations.append({"code": "entry_map", "ref": str(entry), "message": "entry_map 条目不是对象"})
            continue
        if not e.get("role") or not e.get("note"):
            violations.append({"code": "entry_map", "ref": str(entry),
                               "message": "entry_map 条目须含 role/note"})
        if not str(e.get("theme", "")).startswith("adapter:") and e.get("theme") not in THEMES:
            violations.append({"code": "entry_map", "ref": str(entry),
                               "message": "entry_map theme 非法：%r" % e.get("theme")})
        source = str(e.get("source", ""))
        if source.startswith(("dynamic:", "external:")):
            continue
        page = by_source.get(source)
        if page is None:
            violations.append({"code": "entry_map_mismatch", "ref": str(entry),
                               "message": "entry_map.source 未登记为 manifest 页面：%s" % source})
            continue
        if page.get("status") != e.get("status") and not str(e.get("theme", "")).startswith("adapter:"):
            # 适配器入口按路由语义登记（页面文件生命周期可不同，见 note）
            violations.append({"code": "entry_map_mismatch", "ref": str(entry),
                               "message": "入口 %s status 与页面登记不一致（%s vs %s）" % (
                                   entry, e.get("status"), page.get("status"))})
        if entry in ("/", "/zero_proto.html", "/os-entry", "/os-board", "/hq", "/login") and \
                page.get("path") != entry:
            violations.append({"code": "entry_map_mismatch", "ref": str(entry),
                               "message": "入口 %s 的页面 path 应为 %s（实际 %s）" % (
                                   entry, entry, page.get("path"))})
    # 硬约束：默认首页必须是 home_v3
    home = by_entry.get("/")
    if home and home.get("source") != "portal/home_v3.html":
        violations.append({"code": "entry_map_mismatch", "ref": "/",
                           "message": "默认首页 / 必须映射 portal/home_v3.html"})


def _check_route_drift(manifest: dict, root: Path, pages: list, nginx: dict,
                       violations: list) -> None:
    for p in pages:
        src = root / p.get("source", "")
        if not src.is_file():
            continue
        inner = p["source"][len("portal/"):] if p["source"].startswith("portal/") else p["source"]
        path, _auth, _redirect = _mount_path(inner, nginx)
        if path != p.get("path"):
            violations.append({"code": "route_drift", "ref": p.get("source"),
                               "message": "路由漂移：%s 规范路由为 %s，manifest 记为 %s（改路由/文件须同批 --generate）" % (
                                   p.get("source"), path, p.get("path"))})


def _check_route_baseline(manifest: dict, root: Path, violations: list) -> None:
    stored = manifest.get("route_baseline") or {}
    actual = _route_baseline_state(root)
    if actual["present"]:
        if not stored.get("present"):
            violations.append({"code": "route_baseline_drift", "ref": ROUTE_BASELINE_REL,
                               "message": "manifest 未记录路由基线；运行 --generate 对账"})
        elif stored.get("sha256") != actual["sha256"] or stored.get("total") != actual.get("total"):
            violations.append({"code": "route_baseline_drift", "ref": ROUTE_BASELINE_REL,
                               "message": "路由基线漂移（manifest 记录 total=%s，现 total=%s）；"
                                          "跑 tools/portal_route_inventory.py --write-baseline 后同批 --generate" % (
                                              stored.get("total"), actual.get("total"))})
    elif stored.get("present"):
        violations.append({"code": "route_baseline_drift", "ref": ROUTE_BASELINE_REL,
                           "message": "manifest 记录了路由基线但基线文件缺失"})


def _check_counts(manifest: dict, pages: list, violations: list) -> None:
    actual = _counts(pages)
    stored = manifest.get("counts") or {}
    if stored.get("total") != actual["total"]:
        violations.append({"code": "counts_stale", "ref": "counts",
                           "message": "counts.total=%r 与页面数 %d 不一致（--generate 刷新）" % (
                               stored.get("total"), actual["total"])})
    for key, val in actual["by_status"].items():
        if (stored.get("by_status") or {}).get(key) != val:
            violations.append({"code": "counts_stale", "ref": "counts.by_status.%s" % key,
                               "message": "counts.by_status.%s=%r 应为 %d" % (
                                   key, (stored.get("by_status") or {}).get(key), val)})


def check_manifest(root=None, manifest: dict | None = None, path: Path | None = None,
                   skip_route_baseline: bool = False) -> dict:
    """执行全部校验，返回 {"ok", "violations", "counts", "pages_total"}。"""
    root = resolve_root(root)
    violations = []
    if manifest is None:
        manifest = load_manifest(root, path)
    _check_schema(manifest, violations)
    pages = [p for p in (manifest.get("pages") or []) if isinstance(p, dict)]
    if not pages:
        return {"ok": False, "violations": violations, "counts": {}, "pages_total": 0}
    disk = {_rel(root, p) for p in production_files(root)}
    registered = {p.get("source") for p in pages}
    for src in sorted(disk - registered):
        violations.append({"code": "missing_page", "ref": src,
                           "message": "页面缺 manifest：%s（运行 --generate 登记）" % src})
    for p in pages:
        src = p.get("source", "")
        if src and src not in disk and not src.startswith(("dynamic:", "external:")):
            violations.append({"code": "extra_page", "ref": src,
                               "message": "manifest 多余：%s 文件不存在（删除或重定向后须同批 --generate）" % src})
    nginx = parse_nginx(root / NGINX_CONF_REL)
    _check_route_drift(manifest, root, pages, nginx, violations)
    _check_status_semantics(pages, violations)
    _check_nav_isolation(pages, root, violations)
    _check_active_deps(pages, root, violations)
    _check_entry_map(manifest, root, pages, violations)
    if not skip_route_baseline:
        _check_route_baseline(manifest, root, violations)
    _check_counts(manifest, pages, violations)
    violations.sort(key=lambda v: (v.get("code", ""), str(v.get("ref", ""))))
    return {
        "ok": not violations,
        "violations": violations,
        "pages_total": len(pages),
        "counts": _counts(pages),
        "route_baseline_checked": not skip_route_baseline,
    }


# ---------- 输出 ----------
def render_list(manifest: dict, status: str | None = None, family: str | None = None) -> str:
    pages = [p for p in manifest.get("pages") or [] if isinstance(p, dict)]
    if status:
        pages = [p for p in pages if p.get("status") == status]
    if family:
        pages = [p for p in pages if p.get("family") == family]
    pages.sort(key=lambda p: (STATUS_ORDER.get(p.get("status"), 9), p.get("path", "")))
    counts = _counts(pages)
    lines = ["[page_manifest] schema=%s pages=%d active=%d legacy=%d review=%d archived=%d"
             % (manifest.get("schema", "?"), counts["total"],
                counts["by_status"].get("active", 0), counts["by_status"].get("legacy", 0),
                counts["by_status"].get("review", 0), counts["by_status"].get("archived", 0)),
             "%-7s %-44s %-8s %-13s %-6s %s" % ("status", "path", "family", "theme", "auth", "title")]
    for p in pages:
        lines.append("%-7s %-44s %-8s %-13s %-6s %s" % (
            p.get("status", ""), p.get("path", ""), p.get("family", ""),
            p.get("theme", ""), p.get("auth", ""), (p.get("title") or "")[:60]))
    return "\n".join(lines)


def _print_check_result(result: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return
    counts = result.get("counts") or {}
    by_status = counts.get("by_status") or {}
    head = "[page_manifest] pages=%d active=%d legacy=%d review=%d" % (
        result.get("pages_total", 0), by_status.get("active", 0),
        by_status.get("legacy", 0), by_status.get("review", 0))
    if result["ok"]:
        print("OK " + head)
        return
    print("FAIL " + head + " violations=%d" % len(result["violations"]))
    for v in result["violations"]:
        print("  - [%s] %s：%s" % (v.get("code"), v.get("ref"), v.get("message")))


# ---------- 命令行 ----------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="零界页面清单（page manifest）机器版 v0：生成/校验/列表")
    ap.add_argument("--root", default="", help="仓库根（默认本仓库；测试/临时树用）")
    ap.add_argument("--manifest", default="", help="manifest 路径（默认 %s）" % DEFAULT_MANIFEST_REL)
    ap.add_argument("--generate", action="store_true", help="扫描并写 manifest")
    ap.add_argument("--refresh", action="store_true",
                    help="配合 --generate：忽略现有条目人工字段，按内置判定重算")
    ap.add_argument("--check", action="store_true", help="校验当前树与 manifest")
    ap.add_argument("--list", action="store_true", help="列表输出")
    ap.add_argument("--json", action="store_true", help="JSON 输出（--check/--list/--json）")
    ap.add_argument("--status", default="", help="--list 过滤 status")
    ap.add_argument("--family", default="", help="--list 过滤 family")
    ap.add_argument("--skip-route-baseline", action="store_true",
                    help="--check 跳过 route_baseline 对账（仅本地排查用）")
    args = ap.parse_args(argv)

    root = resolve_root(args.root or None)
    mpath = Path(args.manifest) if args.manifest else manifest_path(root)
    if args.manifest and not Path(args.manifest).is_absolute():
        mpath = root / args.manifest

    if args.generate:
        existing = None
        if mpath.is_file() and not args.refresh:
            try:
                existing = load_manifest(root, mpath)
            except ValueError as exc:
                print("manifest 解析失败，按全量重建处理：%s" % exc, file=sys.stderr)
        try:
            manifest = build_manifest(root, existing, refresh=args.refresh)
        except OSError as exc:
            print("扫描失败：%s" % exc, file=sys.stderr)
            return 2
        out = mpath
        write_manifest(out, manifest)
        counts = manifest["counts"]
        old_sources = {p.get("source") for p in (existing or {}).get("pages") or []}
        new_sources = {p["source"] for p in manifest["pages"]}
        added = sorted(new_sources - old_sources)
        dropped = sorted(old_sources - new_sources)
        msg = ("[page_manifest] generate -> %s pages=%d active=%d legacy=%d review=%d"
               % (out, counts["total"], counts["by_status"].get("active", 0),
                  counts["by_status"].get("legacy", 0), counts["by_status"].get("review", 0)))
        if existing and not args.refresh:
            msg += " preserved=%d" % manifest.get("_preserved", 0)
        if added:
            msg += " new=%d" % len(added)
        if dropped:
            msg += " dropped=%d" % len(dropped)
        if args.json:
            payload = {"ok": True, "manifest": str(out), "pages": counts["total"],
                       "counts": counts, "added": added, "dropped": dropped}
            print(json.dumps(payload, ensure_ascii=False, indent=1))
        else:
            print(msg)
            for s in added:
                print("  + %s" % s)
            for s in dropped:
                print("  - %s" % s)
        return 0

    if args.check:
        if not mpath.is_file():
            print("manifest 不存在：%s（先运行 --generate）" % mpath, file=sys.stderr)
            return 2
        try:
            manifest = load_manifest(root, mpath)
        except ValueError as exc:
            print("manifest 解析失败：%s" % exc, file=sys.stderr)
            return 2
        result = check_manifest(root, manifest=manifest, skip_route_baseline=args.skip_route_baseline)
        _print_check_result(result, args.json)
        return 0 if result["ok"] else 1

    if args.list:
        if not mpath.is_file():
            print("manifest 不存在：%s（先运行 --generate）" % mpath, file=sys.stderr)
            return 2
        manifest = load_manifest(root, mpath)
        pages = [p for p in manifest.get("pages") or [] if isinstance(p, dict)]
        if args.status:
            pages = [p for p in pages if p.get("status") == args.status]
        if args.family:
            pages = [p for p in pages if p.get("family") == args.family]
        if args.json:
            payload = {"schema": manifest.get("schema"),
                       "counts": _counts(pages),
                       "pages": [{k: p.get(k) for k in REQUIRED_FIELDS} for p in pages]}
            print(json.dumps(payload, ensure_ascii=False, indent=1))
        else:
            filtered = dict(manifest, pages=pages)
            print(render_list(filtered, args.status or None, args.family or None))
        return 0

    if args.json:
        if not mpath.is_file():
            print("manifest 不存在：%s（先运行 --generate）" % mpath, file=sys.stderr)
            return 2
        print(json.dumps(load_manifest(root, mpath), ensure_ascii=False, indent=1))
        return 0

    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
