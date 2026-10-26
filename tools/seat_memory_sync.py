"""seat_memory_sync.py — 三席位三层记忆装配器（一次性注入工具，2026-10-22）。

三层记忆架构（对齐既有 Hermes 四级记忆与 Z 系引擎，零新平台）：
  L1 工作层（会话级）  —— 每席位自己的运行时上下文：任务单/会话记录/临时笔记
  L2 席位层（长期）    —— 席位身份与职责：SOUL/职责卡/判例经验/裁决依据
  L3 组织层（共享）    —— 全军团共享：org.json 编制/五要素红线/ROADMAP/总线账本口径

注入口径（全部真源，不复制第二本账）：
  - L2 职责卡从 org.json + ROADMAP v2.1 动态生成，标注生成时刻与源版本
  - L3 只放引用（路径+版本号），不放内容副本——单源原则
  - 幂等：重复运行覆盖 L2 卡片（生成物），不触碰任何既有文件

用法：
    python seat_memory_sync.py --dry-run          # 只打印将写入什么
    python seat_memory_sync.py --apply            # 实际落盘
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from _repo_paths import repo_root  # noqa: E402

REPO = str(repo_root(HERE))
ORG = os.path.join(REPO, "agent_platform", "registry", "org.json")

# 席位 × 底座 × 工作根（全部现存目录）
SEATS = {
    "tianfeng": {
        "cn": "田丰", "base": "Hermes（本地 qwen3.5:9b 主脑；判读 GLM-5.3）",
        "home": os.path.expanduser("~/.hermes/profiles/tianfeng"),
        "duty": "归口面：工单验收、经验复核、去重、差距呈报；复核不改写原始记录。",
    },
    "zhaoyun": {
        "cn": "赵云", "base": "DeepSeek Harness（会话承担执行）",
        "home": os.path.expanduser("~/.hermes/profiles/zhaoyun"),
        "duty": "治理面：扫描、收口、健康卡、归档、判例沉淀；只产建议与证据，不派研发不裁决（V3.1）。",
    },
    "zhuge": {
        "cn": "诸葛亮", "base": "OpenClaw 载体（实际 deepseek/deepseek-chat，中转替代）",
        "home": os.path.join(REPO, "legion_agents", "zhuge", "workspace"),
        "duty": "建议面：今日优先级、下钻引导、跨板块提醒；只读归集不造第二账。",
    },
}

L3_REFS = [
    ("组织编制真源", "agent_platform/registry/org.json"),
    ("唯一战略源", "docs/plans/2026-10-22_PRODUCT_ROADMAP_v2.md"),
    ("工单账本", ".worktrees/tasks.json"),
    ("退出码账本", "portal/data/task_exit_audit/"),
    ("健康卡工具", "tools/gen_health_card.py"),
    ("会议厅数据", "portal/data/council.json（tools/gen_council_data.py 生成）"),
    ("经验库引擎", "agent_platform/engine/experience_store.py（Z4，五域）"),
    ("实验卡引擎", "agent_platform/engine/experiment_card.py（Z2，无签字不结案）"),
    ("防穿越守卫", "agent_platform/engine/lookahead_guard.py（Z5）"),
]

HARD_RULES = [
    "五要素：任何生产/交易/系统级变更须老田明示批准（命令全文+影响面+不可逆点+替代方案+回滚预案），沉默≠同意。",
    "S1-S5 同步规：收工即收口 / 现状文档带 TTL / 索引同提交 / 一话题一归档 / 跨仓成对提交。",
    "总线纪律：跨席位协同只走五账本（工单/退出码/事件计数/健康卡/索引），禁止私聊通道与第二本账。",
    "异族互镜：MUTUAL-REVIEW 互审单——执行者不结案，复核者不施工；分歧自动升级 OS 待裁区。",
    "判例 · 检测器自证：任何用于判定的读数，采信前必须用已知为真样本验证正反例；给不出反例的检测器结论记「未知」不记「正常」。",
    "判例 · 假成功：判据必须落在被改对象的 mtime/返回值，不信屏幕 OK。",
    "判例 · 快照假绿：LastTaskResult 快照会把「每次失败」拍成「正在运行」，必须窗口计数。",
]


def _capability_summary(home: str) -> dict:
    """C2 联动：读 skills/CAPABILITIES.json 摘要（读不到如实报 None，不猜）。"""
    p = os.path.join(home, "skills", "CAPABILITIES.json")
    if not os.path.isfile(p):
        return None
    try:
        cap = json.load(open(p, encoding="utf-8"))
        caps = cap.get("capabilities", [])
        return {
            "total": cap.get("total"),
            "ready": cap.get("ready"),
            # 2026-10-26 A2/C2：新增降级/纸面状态计数（旧卡只有 ready/pending_*）
            "degraded_relay": cap.get("degraded_relay", 0),
            "degraded_rule": cap.get("degraded_rule", 0),
            "pending_key": cap.get("pending_key"),
            "pending_wiring": cap.get("pending_wiring"),
            "paper": cap.get("paper", 0),
            "ready_names": [c["name"] for c in caps
                            if c.get("status") == "ready"],
            "pending_names": [c["name"] for c in caps
                              if c.get("status") != "ready"],
            "statuses": {c["name"]: c.get("status") for c in caps},
            "source_generated_at": cap.get("generated_at"),
        }
    except (OSError, json.JSONDecodeError):
        return None


def build_card(seat_id: str) -> dict:
    cfg = SEATS[seat_id]
    cap = _capability_summary(cfg["home"])
    return {
        "schema": "seat_memory_card/v1.1",
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "seat": seat_id,
        "cn_name": cfg["cn"],
        "base": cfg["base"],
        "capabilities_summary": cap if cap is not None else "（能力清单未注入：先跑 seat_capability_inject.py）",
        "layers": {
            "L1_work": {
                "what": "会话级运行时：任务单、会话记录、临时笔记",
                "where": f"{cfg['home']}（各底座自管，随会话生死）",
            },
            "L2_seat": {
                "what": "席位身份与职责（本卡）+ 判例经验（Z4 对应域）+ 裁决依据",
                "duty": cfg["duty"],
                "experience_domain": {"tianfeng": "E5-supervision（赵云写田丰读）之复核侧",
                                      "zhaoyun": "E5-supervision（赵云写田丰读）之写入侧",
                                      "zhuge": "建议面自有域（建议采纳率在健康卡）"}[seat_id],
            },
            "L3_org": {
                "what": "全军团共享层——只引用不复制（单源原则，改源即全员生效）",
                "refs": [{"name": n, "path": p} for n, p in L3_REFS],
                "hard_rules": HARD_RULES,
            },
        },
        "note": "本卡为生成物可随时重生成；L3 为引用清单非内容副本；org.json 编制以 hr.cn_name/org.persona.name 为权威。",
    }


def plan() -> list[tuple[str, dict]]:
    out = []
    for seat_id in SEATS:
        cfg = SEATS[seat_id]
        path = os.path.join(cfg["home"], "SEAT_CARD.json")
        out.append((path, build_card(seat_id)))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="三席位三层记忆装配器")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)
    if not args.dry_run and not args.apply:
        ap.print_help()
        return 2
    for path, card in plan():
        if args.dry_run:
            exists = "覆盖" if os.path.isfile(path) else "新建"
            print(f"[dry-run] {exists} {path}（seat={card['cn_name']} base={card['base']}）")
            continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(card, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        print(f"[apply] 写入 {path}")
    print("完成：L2 卡片就位；L1 由各底座自管；L3 为引用清单（单源）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
