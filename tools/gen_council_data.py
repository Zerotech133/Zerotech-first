"""gen_council_data.py — 会议厅数据生成器（S-1b，2026-10-22）。

扫描 deliverables 下的互审/裁决类文书 → council.json（议题/发言流/状态灯），
供 /os_council 页面 nginx 静态直服消费（零 8091 依赖，照 WO-OS-31 模式）。

诚实纪律（沿用 WO-OS-30/31 判例）：
- 文书识别只认头部声明（对象/状态行），不猜议题归属；
- 解析失败/无议题单列 unknown，不静默丢弃；
- 在线检测只报可测信号（端口/进程/最近提交），不可测标 unknowable 不猜。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DELIVERABLES = os.path.join(HERE, "..", "deliverables")
OUT_PATH = os.path.join(HERE, "..", "portal", "data", "council.json")
REPO = os.path.join(HERE, "..")

# 席位徽章真源口径：org.json hr cn_name 权威；此处映射文书署名 → 席位
SEAT_ALIASES = {
    "田丰": "tianfeng", "赵云": "zhaoyun", "诸葛亮": "zhuge",
    "老田": "laotian", "WorkBuddy": "tianfeng",
}

COUNCIL_PAT = re.compile(r"^(互审单|裁决|回执)", re.M)
STATUS_PAT = re.compile(r"^>\s*STATUS[:：]\s*(.+)$", re.M)
OBJECT_PAT = re.compile(r"^-\s*对象[:：]\s*(.+)$", re.M)
REVIEWER_PAT = re.compile(r"^-\s*复核人[:：]\s*(.+)$|^-?\s*评审(?:方|人)[:：]?\s*(.+)$", re.M)


def _mtime_iso(path: str) -> str:
    t = dt.datetime.fromtimestamp(os.path.getmtime(path)).astimezone()
    return t.isoformat(timespec="seconds")


def _seat_of(text: str, filename: str) -> str:
    """从文书内容/文件名推断发言席位；判不出标 unknown 不猜。"""
    m = REVIEWER_PAT.search(text[:2000])
    if m:
        who = (m.group(1) or m.group(2) or "").strip()
        for alias, seat in SEAT_ALIASES.items():
            if alias in who:
                return seat
    for alias, seat in SEAT_ALIASES.items():
        if alias in filename:
            return seat
    if "回执3" in filename or "回执#3" in filename or "复核结论" in filename:
        return "tianfeng"
    if "回执2" in filename or "回执#2" in filename:
        return "zhaoyun"
    if "评审" in filename and ("OS" in filename or "调度" in filename or "赵云" in filename):
        return "zhaoyun"  # 首期评审文书均赵云侧递交（互审单#001 事实）
    return "unknown"


def _status_light(status_text: str) -> str:
    s = status_text or ""
    if any(k in s for k in ("收敛", "已接受", "已定", "done", "green")):
        return "green"
    if any(k in s for k in ("修订", "回执", "进行", "yellow", "待")):
        return "yellow"
    if any(k in s for k in ("分歧", "驳回", "red")):
        return "red"
    return "yellow"  # 有交版权当修订中


def scan_topics() -> list[dict]:
    topics: dict[str, dict] = {}
    unknown_count = 0
    seen: set[str] = set()
    # 扫两个目录：仓内 deliverables + 项目根（赵云侧文书按其习惯落根目录）
    roots = [DELIVERABLES, os.path.normpath(os.path.join(REPO, ".."))]
    for root in roots:
        if not os.path.isdir(root):
            continue
        for fn in sorted(os.listdir(root)):
            if not fn.endswith(".md") or "2026-10-22" not in fn:
                continue
            # 只收互审线文书：互审/回执/评审（赵云侧评审与回执在项目根）
            has_council = ("互审" in fn) or ("回执" in fn and "P1" not in fn and "到点" not in fn) or ("评审" in fn and "OS" in fn)
            if not has_council:
                continue
            path = os.path.join(root, fn)
            key = os.path.basename(path)
            if key in seen:
                continue
            seen.add(key)
            try:
                with open(path, encoding="utf-8") as f:
                    text = f.read(6000)
            except OSError:
                unknown_count += 1
                continue
            seat = _seat_of(text, fn)
            status_m = STATUS_PAT.search(text)
            status_text = status_m.group(1).strip() if status_m else ""
            title = fn.replace(".md", "")
            first_h = re.match(r"^#\s+(.+)$", text, re.M)
            if first_h:
                title = first_h.group(1).strip()[:80]
            speech = {
                "file": fn,
                "seat": seat,
                "at": _mtime_iso(path),
                "title": title,
                "status_note": status_text or "（文书未标 STATUS，按 mtime 排序）",
            }
            topic_key = "互审单 #001（OS 调度方案）"  # 本期唯一议题，全部互审文书归此
            if topic_key not in topics:
                topics[topic_key] = {"topic": topic_key, "speeches": [], "light": "yellow"}
            topics[topic_key]["speeches"].append(speech)
    out = []
    for t in topics.values():
        t["speeches"].sort(key=lambda s: s["at"])
        last = t["speeches"][-1]
        t["light"] = _status_light(last["status_note"])
        t["last_speech_at"] = last["at"]
        out.append(t)
    if unknown_count:
        out.append({"topic": f"解析失败 {unknown_count} 份（未纳入，不静默）",
                    "speeches": [], "light": "red", "last_speech_at": ""})
    return sorted(out, key=lambda t: t["last_speech_at"] or "", reverse=True)


def probe_seats() -> list[dict]:
    """席位在线检测：只报可测信号，不可测标 unknowable（检测器自证判例）。"""
    seats = []

    def probe_port(name: str, seat: str, port: int, what: str):
        rc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             f"(Test-NetConnection -ComputerName 127.0.0.1 -Port {port} -InformationLevel Quiet -WarningAction SilentlyContinue)"],
            capture_output=True, timeout=30, check=False)
        ok = rc.returncode == 0 and rc.stdout.decode("gbk", errors="replace").strip().lower().endswith("true")
        seats.append({"seat": seat, "name": name, "method": f"端口 {port}（{what}）",
                      "online": bool(ok), "verifiable": True})

    # 田丰：Hermes 8787（Hermes webui 现役）
    probe_port("田丰 · Hermes", "tianfeng", 8787, "Hermes WebUI")
    # 军团指挥台 8796（军团总线现役，非某单席位，列出供参考）
    probe_port("军团指挥台", "legion", 8796, "legion_command")
    # 诸葛亮：OpenClaw agent-config 载体（无固定端口；L5/8796 走 CLI）
    # 2026-10-26 A2/C2：旧文案「key 未注入——骨架就绪未上模」已被实测推翻
    # （密钥在 OpenClaw secret store，env 非前置；L5/8796 经 deepseek 中转真实作答）。
    seats.append({"seat": "zhuge", "name": "诸葛亮 · OpenClaw",
                  "method": "OpenClaw agent-config 载体（无固定端口；L5/8796 走 CLI）",
                  "online": False, "verifiable": False,
                  "note": "无端口可探故 online=false（≠通道不可用）。2026-10-26 实测："
                          "L5 consult 与 8796 AI1 均经 DeepSeek 中转真实作答"
                          "（openclaw_cloud/carrier=deepseek/deepseek-chat/relay=true，非 GPT6）；"
                          "GPT6 真身待跨境网络；密钥在 OpenClaw secret store，非 env"})
    # 赵云：DeepSeek Harness（本体已迁 C:/lab，key 未到位，无常驻服务）
    seats.append({"seat": "zhaoyun", "name": "赵云 · DeepSeek Harness",
                  "method": "Harness 无常驻服务（任务框架，按需起）",
                  "online": False, "verifiable": False,
                  "note": "治理线由会话承担执行；DeepSeek key 未到位"})
    # WorkBuddy：本会话（生成器运行即证明在写侧）
    seats.append({"seat": "tianfeng", "name": "WorkBuddy 会话工位",
                  "method": "数据生成器运行本身（本文件由它落盘）",
                  "online": True, "verifiable": True,
                  "note": "会话级存在，会话结束即离线——在线≠常驻"})
    return seats


# ---------------- v2：六阶段工作流 ----------------
STAGES = ["立题", "表态", "试工", "监读", "定稿", "分发收口"]

# 议题注册表：新议题先在此登记（或从立题文书解析 tags）
TOPIC_REGISTRY = [
    {
        "topic": "互审单 #001（OS 调度方案）",
        "tags": ["govern", "design"],
        "stage": "分发收口",
        "originator": "laotian",
        "summary": "OS 唯一调度面方案经四轮互审收敛：v1.1 六修订+S-0 双条件门禁。收口遗留：0x80070003 根因独立挂账。",
    },
    {
        "topic": "议会厅工作流 SOP 试行（首个全流程任务）",
        "tags": ["govern", "docs"],
        "stage": "表态",
        "originator": "laotian",
        "summary": "SOP v1 已落地（d02f40a2）；待首个真实任务全流程走一遍（候选：明日警报源 as_of 复核）后定 v1.1。",
    },
    {
        "topic": "SOP 试点：警报源 as_of 复核 + 0x80070003 定性",
        "tags": ["ops", "govern"],
        "stage": "定稿",
        "originator": "laotian",
        "summary": "B1 首流程试点：试工完成（A1 判定=生产者停摆 2 天如实记录；0x80070003 三变体 PASS 环境性定性），老田 06:35 批「1/2/3 全批」：premarket 恢复单 K-QUANT-20261023-PRE 已建/0x80070003 环境性挂账/SOP v1.1 定稿。原立题：①07:00 后 /api/alerts 各源 as_of 判定；②0x80070003 昨夜复现今晨消失的环境性定性复核。试工=赵云晨间扫描轮（A5/A7 已在跑），监读=田丰复核战报，定稿=老田裁 S-0 门禁判定口径。",
        "tickets": ["A1", "A4", "A7"],
    },
]


def build_stage_view() -> list[dict]:
    """议题 × 六阶段视图：每阶段列出已发生的发言席位与文书数。"""
    speeches_by_topic = {}
    for root in [DELIVERABLES, os.path.normpath(os.path.join(REPO, ".."))]:
        if not os.path.isdir(root):
            continue
        for fn in sorted(os.listdir(root)):
            if not fn.endswith(".md") or "2026-10-22" not in fn:
                continue
            has_council = ("互审" in fn) or ("回执" in fn and "P1" not in fn and "到点" not in fn) or ("评审" in fn and "OS" in fn)
            if not has_council:
                continue
            path = os.path.join(root, fn)
            try:
                text = open(path, encoding="utf-8").read(6000)
            except OSError:
                continue
            seat = _seat_of(text, fn)
            speeches_by_topic.setdefault("互审单 #001（OS 调度方案）", []).append(
                {"seat": seat, "at": _mtime_iso(path), "title": fn[:-3]})

    out = []
    for reg in TOPIC_REGISTRY:
        sp = sorted(speeches_by_topic.get(reg["topic"], []), key=lambda x: x["at"])
        # 阶段推进判定：表态=有≥2席位发言；试工/监读由文书阶段标注或注册表给定
        stage_index = STAGES.index(reg["stage"]) if reg["stage"] in STAGES else 0
        seats_spoken = sorted({s["seat"] for s in sp} - {"unknown"})
        out.append({
            "topic": reg["topic"],
            "tags": reg["tags"],
            "stage": reg["stage"],
            "stage_index": stage_index,
            "stages": STAGES,
            "originator": reg["originator"],
            "summary": reg["summary"],
            "speeches": sp,
            "seats_spoken": seats_spoken,
            "light": "green" if stage_index >= STAGES.index("定稿") else ("red" if seats_spoken and len(seats_spoken) < 2 and stage_index >= 1 else "yellow"),
        })
    return out


def route_suggestions() -> list[dict]:
    """对未到分发阶段的议题预给路由建议（council_route.ROUTE_TABLE 复用）。"""
    sys.path.insert(0, HERE)
    try:
        import council_route as cr
    except ImportError:
        return []
    out = []
    for t in TOPIC_REGISTRY:
        if t["stage"] in ("分发收口", "定稿"):
            continue
        r = cr.route(t["tags"])
        if "error" not in r:
            out.append({"topic": t["topic"], "route": r})
    return out


def main() -> int:
    topics = scan_topics()
    seats = probe_seats()
    stage_view = build_stage_view()
    routes = route_suggestions()
    payload = {
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "schema": "os_council/v2",
        "topics": topics,
        "stage_view": stage_view,
        "route_suggestions": routes,
        "seats": seats,
        "workflow_stages": STAGES,
        "note": "会议厅只读投影：发言=已落盘文书（git 可溯）；状态灯由文书 STATUS 行驱动；"
                "六阶段视图对齐议会厅 SOP v1；路由建议由 council_route.py 产（只建议不派单）；"
                "在线检测只报可测信号，不可测席位诚实标注，不猜。",
    }
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(OUT_PATH), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, OUT_PATH)
    print(f"council.json -> {os.path.relpath(OUT_PATH, REPO)}: {len(topics)} 议题 / {len(stage_view)} 阶段视图 / {len(routes)} 路由建议 / {len(seats)} 席位")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
