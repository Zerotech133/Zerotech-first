# -*- coding: utf-8 -*-
"""RiskGuard 校准落地准备件（WO-QT-20261020-023 · b 项）。

背景：老田 2026-10-20 16:00 拍板 1.1 的 a-d 四项。其中 **b = R3 绝对数闸
5→8 进入下一轮影子校准**——但**生产 params.yaml 改动属生产变更**，本单
**只出准备件**（参数候选值 + calibrated_keys 登记草案 + 影子期方案 +
recon 满月复跑判据 + 回滚预案 + 五要素审批单草案），**不改生产、不改
zerobound-kg 生产参数**；五要素审批单由主会话提交老田，批准后才动生产。

本模块职责边界（硬约束）：
  · **只读**生产 params.yaml（解析出当前值作对照），**不写**任何生产文件；
  · 产出的准备件是一份**审查件**（dict + Markdown），带
    `applied_to_production=False` / `production_write=False` 标记；
  · 依赖仅标准库（**不引 pyyaml**——生产参数只做行级读取，不整体解析，
    避免解析器差异引入误判）；零网络出口。

口径来源（只引用，不复算）：WS-RG《RiskGuard 四规则误杀归因与校准方案》
§3.3（R3 双闸回放：绝对数 5→8，拦截 28→10 收窄 64%，命中组 ret5
-9.79%→-13.29%，settled n=13 小样本）——**n 小，仅作方向候选**。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

# ── 生产参数（只读锚点；本模块绝不写它）────────────────────────────
KEY = "riskguard_bear_min_n"                 # R3 绝对数闸（params.yaml 单一事实源）
DENSITY_KEY = "riskguard_bear_density"       # R3 密度闸（维持 0.5，不动）
SHADOW_PERIOD_DAYS = 14                      # 影子期 ≥2 周
APPROVAL_REQUIRED = "五要素审批单（主会话提交老田批准后方可动生产）"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_params_path() -> Path:
    """生产 params.yaml（只读）。"""
    env = os.environ.get("RISKGUARD_PARAMS")
    if env:
        return Path(env)
    return _repo_root().parent / "zerobound-kg" / "params.yaml"


def read_param(params_path=None, key: str = KEY):
    """从 params.yaml **行级只读**取参数当前值（不引 pyyaml）。

    只认顶层 `key: value` 行（缩进子项不误取）；取不到返回 None（不臆造）。
    """
    p = Path(params_path) if params_path else default_params_path()
    if not p.exists():
        return None
    pat = re.compile(r"^\s*%s\s*:\s*(\S+)" % re.escape(key))
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            m = pat.match(line)
            if m and not line[:1].isspace():
                v = m.group(1).strip().strip('"').strip("'")
                try:
                    return int(v)
                except ValueError:
                    try:
                        return float(v)
                    except ValueError:
                        return v
    return None


def build_r3_threshold_prep_package(params_path=None) -> dict:
    """构造 R3 绝对数闸 5→8 的**准备件**（审查件；零生产写入）。"""
    current = read_param(params_path, KEY)
    density = read_param(params_path, DENSITY_KEY)
    candidate = 8
    return {
        "prep_id": "WO-QT-20261020-023-b-R3-min_n-5to8",
        "prepared_at": "2026-10-20",
        "decision_ref": "老田 2026-10-20 16:00 拍板 1.1 之 b 项（R3 绝对数闸 5→8 "
                        "进入下一轮影子校准）",
        # **本单只出准备件**：以下两个标记为 False 是审查件的核心语义
        "applied_to_production": False,
        "production_write": False,
        "approval_required": APPROVAL_REQUIRED,
        "params_candidate": {
            KEY: {
                "current": current,
                "candidate": candidate,
                "file": "zerobound-kg/params.yaml",
                "change_kind": "value_only",
                "note": "密度闸 %s 维持不动（现行 0.5，三档零区分）" % DENSITY_KEY,
            },
            DENSITY_KEY: {
                "current": density,
                "candidate": density,
                "change_kind": "unchanged",
            },
        },
        "calibrated_keys_registration_draft": {
            "calibrated_keys_before": ["%s 已在列" % KEY],
            "calibrated_keys_draft": [
                {
                    "key": KEY,
                    "action": "value_change_under_existing_key",
                    "from": current,
                    "to": candidate,
                    "calib_ver": "待老田批准后写（形如 2026-XX-XX-user-approved）",
                    "basis": "WS-RG §3.3 双闸回放：绝对数 5→8 拦截 28→10（收窄 64%），"
                             "命中组 ret5 -9.79%→-13.29%（拦得更准），settled n=13 属小样本"
                             "→ 仅方向候选，须经影子期与满月 OOS 复跑确认",
                    "note": "**登记草案，未写入生产**；enforce 解锁三条件不变"
                            "（显式 --enforce + 全键 calibrated + 样本外 |t|≥3 + 老田批准）",
                },
            ],
            "enforce_conditions_unchanged": [
                "显式 --enforce", "GUARD_KEYS 全键 calibrated",
                "样本外 |t|≥3", "老田批准",
            ],
            "enforce_allowed_mechanical_refusal": "保持不动（kg_riskguard.enforce_allowed() 恒 False）",
        },
        "shadow_plan": {
            "shadow_period_days": SHADOW_PERIOD_DAYS,
            "minimum": "≥2 周（方案 §四.2 要求）",
            "mode": "shadow（只记录不拦截）",
            "observation_items": [
                "每日 shadow_block 行数与拦截率（按 (rule_id, rule_version) 归集）",
                "拦截组 vs 放行组后验 ret5/ret10 均值差与 Welch t（|t|≥3 为样本外门）",
                "拦截组胜率 vs 放行组胜率（误杀贡献度口径）",
                "日线未成熟样本的 pending 比例（成熟度跟踪）",
                "与 WS-RG §3.3 回放预期的一致性核对（拦截面收窄 ~64%）",
            ],
            "review_cadence": "每交易日读账；第 7 日中间复核；第 14 日出影子期小结",
        },
        "recon_full_month_criteria": {
            "recon_tool": "zerobound-kg/kg_riskguard_recon.py（只读消费方，零写入）",
            "horizons": {"fwd5": "10 月下旬", "fwd20": "11 月"},
            "pass_criteria": [
                "R3 命中组（绝对数闸 8 档）ret5/ret10 均值**不劣于**现行 5 档（方向同向）",
                "R3 拦截面收窄幅度落在回放预期量级（~64%），无异常塌缩/暴涨",
                "Welch |t| 与放行组对照在满月窗内达 |t|≥3 样本外门（沿用既有门）",
                "成熟样本量足（settled n≥30，方案 §3.3 的 n=13 不足以单独支撑定值）",
                "R2 满月 OOS 结论不受本次改动干扰（c 项另议，不在本单范围）",
            ],
            "fail_actions": [
                "任一 pass_criteria 不满足 → 不固化，回到现行 5 档（默认什么都不改）",
                "满月前不动生产；满月后由主会话按同口径复跑并出对照表",
            ],
        },
        "rollback_plan": {
            "trigger": ["影子期内出现与回放方向相反的实测偏离",
                        "满月复跑未达 pass_criteria",
                        "老田主动要求撤回"],
            "steps": [
                "1) 保持 params.yaml 原值不动（本单本就未改生产 → 回滚即**无需生产动作**）",
                "2) 影子期账本行保留为历史证据（账本 append-only，不删不改）",
                "3) 在准备件上登记撤回结论与原因（可回放引用）",
            ],
            "irreversibility_assessment": "**无不可逆点**：生产参数未改，影子账本 append-only "
                                          "只增不改，回滚不产生数据损失",
            "post_rollback_state": "R3 绝对数闸维持现行值，enforce 三条件与机械拒绝均不变",
        },
        "evidence_refs": [
            "deliverables/2026-10-20_RiskGuard四规则误杀归因与校准方案.md §3.3（R3 双闸回放）",
            "deliverables/2026-10-20_RiskGuard四规则误杀归因与校准方案.md §四（灰度实施建议）",
            "zerobound-kg/params.yaml（生产单一事实源，只读引用）",
        ],
        "scope_exclusions": [
            "不改生产 params.yaml（不落值）",
            "不改 zerobound-kg 任何生产代码/凭据/计划任务",
            "R1/R2/R4 不动（d 项：默认什么都不改是数据支持的默认）",
            "enforce 解锁三条件与 enforce_allowed() 机械拒绝保持不动",
        ],
    }


def render_prep_markdown(pkg: dict) -> str:
    """准备件 → Markdown（可直接贴审批单附件）。"""
    c = pkg["params_candidate"]
    main = c[KEY]
    lines = [
        f"# RiskGuard R3 绝对数闸 5→8 校准准备件（{pkg['prep_id']}）", "",
        f"- 依据：{pkg['decision_ref']}",
        f"- 状态：**准备件（未落地）**｜applied_to_production="
        f"{pkg['applied_to_production']}｜production_write={pkg['production_write']}",
        f"- 审批前置：{pkg['approval_required']}", "",
        "## 一、参数候选", "",
        "| 键 | 现值 | 候选 | 文件 | 变更 |",
        "|---|---|---|---|---|",
        f"| `{KEY}` | {main['current']} | **{main['candidate']}** | {main['file']} | 仅改值 |",
        f"| `{DENSITY_KEY}` | {c[DENSITY_KEY]['current']} | {c[DENSITY_KEY]['candidate']} | "
        f"{main['file']} | 不动 |",
        "", f"> {main['note']}", "",
        "## 二、calibrated_keys 登记草案（未写入生产）", "",
    ]
    for d in pkg["calibrated_keys_registration_draft"]["calibrated_keys_draft"]:
        lines += [
            f"- 键：`{d['key']}`｜{d['action']}｜{d['from']} → {d['to']}",
            f"  - 校准版本：{d['calib_ver']}",
            f"  - 依据：{d['basis']}",
            f"  - 注：{d['note']}", "",
        ]
    lines += ["enforce 解锁三条件（不变）：" +
              "、".join(pkg["calibrated_keys_registration_draft"]
                        ["enforce_conditions_unchanged"]) +
              "；" + pkg["calibrated_keys_registration_draft"]
              ["enforce_allowed_mechanical_refusal"], ""]
    sp = pkg["shadow_plan"]
    lines += ["## 三、影子期方案（≥2 周）", "",
              f"- 时长：{sp['shadow_period_days']} 天（{sp['minimum']}）｜模式：{sp['mode']}",
              f"- 复核节奏：{sp['review_cadence']}", "",
              "观测项：", ""]
    lines += [f"- {x}" for x in sp["observation_items"]]
    rc = pkg["recon_full_month_criteria"]
    lines += ["", "## 四、recon 满月复跑判据", "",
              f"- 工具：{rc['recon_tool']}｜horizons：fwd5 {rc['horizons']['fwd5']}、"
              f"fwd20 {rc['horizons']['fwd20']}", "", "通过条件：", ""]
    lines += [f"- {x}" for x in rc["pass_criteria"]]
    lines += ["", "不通过处置：", ""]
    lines += [f"- {x}" for x in rc["fail_actions"]]
    rb = pkg["rollback_plan"]
    lines += ["", "## 五、回滚预案", "",
              f"- 触发条件：{'；'.join(rb['trigger'])}", "", "步骤：", ""]
    lines += [f"- {x}" for x in rb["steps"]]
    lines += ["", f"- 不可逆点评估：{rb['irreversibility_assessment']}",
              f"- 回滚后状态：{rb['post_rollback_state']}", "",
              "## 六、本单范围外（明确不做）", ""]
    lines += [f"- {x}" for x in pkg["scope_exclusions"]]
    lines += ["", "## 七、证据引用", ""]
    lines += [f"- {x}" for x in pkg["evidence_refs"]]
    return "\n".join(lines)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="RiskGuard 校准准备件（只读，不落生产）")
    ap.add_argument("--params", default=None, help="生产 params.yaml 路径（只读）")
    ap.add_argument("--out-json", default=None, help="准备件 JSON 落盘路径（显式给定才写）")
    ap.add_argument("--out-md", default=None, help="准备件 Markdown 落盘路径")
    a = ap.parse_args(argv)
    pkg = build_r3_threshold_prep_package(a.params)
    if a.out_json:
        import json
        with open(a.out_json, "w", encoding="utf-8") as f:
            json.dump(pkg, f, ensure_ascii=False, indent=1)
    if a.out_md:
        with open(a.out_md, "w", encoding="utf-8") as f:
            f.write(render_prep_markdown(pkg))
    print(render_prep_markdown(pkg))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
