"""M2-P3 统一 Skill 目录：跨部门复用动作的注册表。

总纲规格：
- 八要素缺一不可：输入、输出、适用场景、禁用场景、验收器、样例、
  版本、回滚；
- 知识必须经过候选、人工审查、评测、批准、版本化、可回滚；
- L3 组织知识 = 已批准 Skill；验收句 = 一个批准 Skill 可被投研、
  研发、内容和路演复用。

状态机（绿地域：仓内无既有 Skill 注册表，新建账本并声明依据）：
    draft → reviewed → approved → deprecated
    - draft→reviewed：人工审查记录，走 move（单一写路径 + history）
    - →approved：必须 Approval（跨域复用 = 组织级承诺）
    - →deprecated：必须 Approval（下线也是承诺）
    - 批准时强制校验八要素齐全（缺一项拒绝批准）
    - 评测门属 M5，本轮不建（红线：不提前）

幂等键 skill:<slug>:v<ver>:<from>→<to>；崩溃窗口 catch-up；
账本 append-only history（{at, stage, by, note}）。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_DOMAINS_DIR = Path(__file__).resolve().parent
_AGENT_PLATFORM = _DOMAINS_DIR.parent
if str(_AGENT_PLATFORM) not in sys.path:
    sys.path.insert(0, str(_AGENT_PLATFORM))

from contracts.objects import Approval, fact_hash_of  # noqa: E402
from adapters.credential_trace import cross_check, verdict_for  # noqa: E402
from engine.approvals import ApprovalStore    # noqa: E402

STAGES = ["draft", "reviewed", "approved", "deprecated"]
LEGAL_TRANSITIONS = {
    "draft": ["reviewed"],
    "reviewed": ["approved", "draft"],        # 可打回
    "approved": ["deprecated"],
    "deprecated": [],
}
GATED = {"approved", "deprecated"}            # 用户级承诺 → Approval 门

# 八要素（总纲原文口径）
ELEMENTS = ["inputs", "outputs", "applies_to", "not_for",
            "acceptor", "samples", "version", "rollback"]


class SkillWiringError(Exception):
    """受控操作非法。"""


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _skill_content_hash(skill: Dict) -> str:
    """Skill 定义内容指纹（M3-P3 门绑定锚点）：八要素 canonical JSON。

    排除运行态字段（status/history/approved_at）——它们随批准行为
    演进，纳入会使重放校验永远失配（绑的是「决策所基于的定义」，
    不是决策产生的状态）。
    """
    core = {k: v for k, v in skill.items()
            if k not in ("status", "history", "approved_at")}
    return fact_hash_of(json.dumps(
        core, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8"))


def _resolve(root: Optional[Path]):
    """路径解析统一走函数（模块级常量即测试债务判例）。"""
    r = Path(root) if root else _AGENT_PLATFORM
    return (r / "data" / "skills" / "skills.json",
            r / "data" / "approvals" / "approvals.db")


def _load(root: Optional[Path] = None) -> List[Dict]:
    p, _ = _resolve(root)
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))


def _save(root: Optional[Path], skills: List[Dict]) -> None:
    p, _ = _resolve(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(skills, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(p)                              # 原子替换防半写


def _find(skills: List[Dict], slug: str) -> Optional[Dict]:
    return next((s for s in skills if s.get("slug") == slug), None)


def _missing_elements(skill: Dict) -> List[str]:
    return [k for k in ELEMENTS
            if not str(skill.get(k, "")).strip()
            and not skill.get(k)]


# ----------------------------------------------------------------------
def add_skill(root: Optional[Path], slug: str, title: str, by: str,
              owner: str = "", domains: List[str] = None,
              **elements) -> Dict:
    """登记候选 Skill（draft）。八要素可先缺——批准时强制齐全。"""
    if not slug or not title or not by:
        raise SkillWiringError("slug/title/by 必填")
    skills = _load(root)
    if _find(skills, slug):
        raise SkillWiringError("slug 已存在：%s（升级=新版本条目）" % slug)
    skill = {"slug": slug, "title": title, "owner": owner or by,
             "domains": domains or [], "status": "draft",
             "history": [{"at": _now(), "stage": "draft", "by": by,
                          "note": "registered"}],
             "created_at": _now()}
    skill.update({k: elements.get(k, "") for k in ELEMENTS})
    skills.append(skill)
    _save(root, skills)
    return skill


def move_stage(root: Optional[Path], slug: str, to_stage: str, by: str,
               note: str = "") -> Dict:
    """自由推进（draft→reviewed / reviewed→draft 打回）；受门段拒绝。"""
    if not by:
        raise SkillWiringError("操作人 by 必填")
    skills = _load(root)
    skill = _find(skills, slug)
    if skill is None:
        raise SkillWiringError("Skill 不存在：%s" % slug)
    if to_stage in GATED:
        raise SkillWiringError(
            "→%s 是用户级承诺，必须走 approve（Approval 门）" % to_stage)
    if to_stage not in LEGAL_TRANSITIONS.get(skill["status"], []):
        raise SkillWiringError(
            "非法转移 %s→%s（合法：%s）"
            % (skill["status"], to_stage,
               LEGAL_TRANSITIONS.get(skill["status"], [])))
    skill["status"] = to_stage
    skill["history"].append({"at": _now(), "stage": to_stage, "by": by,
                             "note": note or "stage move"})
    _save(root, skills)
    return skill


def approve(root: Optional[Path], slug: str, to_stage: str, by: str,
            reason: str = "", store: ApprovalStore = None
            ) -> Tuple[Approval, Dict, bool]:
    """受门转移（→approved / →deprecated）：凭据先落，再改真源。"""
    if to_stage not in GATED:
        raise SkillWiringError("approve 仅用于受门段 %s" % sorted(GATED))
    if not by:
        raise SkillWiringError("批准人 by 必填")
    skills = _load(root)
    skill = _find(skills, slug)
    if skill is None:
        raise SkillWiringError("Skill 不存在：%s" % slug)
    from_status = skill["status"]
    # 幂等快路径先于合法性检查（GZH 判例③）：同状态+凭据在=重放
    own = store is None
    _, adb = _resolve(root)
    store = store or ApprovalStore(adb)
    try:
        # 键内直接嵌 version 原文（'v1'），勿再拼 'v' 前缀（vv1 双 v 判例）。
        # 重放匹配用 startswith(skill:<slug>:)+endswith(>to)，键格式演进不影响旧凭据。
        key = "skill:%s:%s:%s>%s" % (slug, skill.get("version", ""),
                                      from_status, to_stage)
        if from_status == to_stage:
            # 重放语义：找原始凭据（如 reviewed>approved），而非当前态键
            existing = [c for c in store.list()
                        if c.idempotency_key.startswith("skill:%s:" % slug)
                        and c.idempotency_key.endswith(">%s" % to_stage)]
            if existing:
                # M3-P3 批 A 发 B 拒绝：凭据绑定过定义指纹且 Skill 定义
                # 已被改动 → 原审批失效，须走 deprecated/新版本流程
                hit = existing[0]
                if hit.content_hash:
                    cur = _skill_content_hash(skill)
                    if cur != hit.content_hash:
                        raise SkillWiringError(
                            "批 A 发 B 拒绝：凭据 %s 绑定 content_hash=%s…，"
                            "当前 Skill 定义=%s…（定义已变更，原审批失效）"
                            "——走 deprecated 下线后注册新版本"
                            % (hit.approval_id[:12], hit.content_hash[:12],
                               cur[:12]))
                return hit, skill, True   # 崩溃后重复批准=重放
        if to_stage not in LEGAL_TRANSITIONS.get(from_status, []):
            raise SkillWiringError(
                "非法转移 %s→%s" % (from_status, to_stage))
        if to_stage == "approved":
            missing = _missing_elements(skill)
            if missing:
                raise SkillWiringError(
                    "八要素不全，拒绝批准，缺：%s" % ", ".join(missing))

        approval, replayed = store.record(Approval(
            target_type="skill",
            target_id="skill:%s" % slug,
            from_status=from_status, to_status=to_stage,
            decision="approved", decided_by=by, reason=reason,
            idempotency_key=key,
            content_hash=_skill_content_hash(skill),   # M3-P3 批准时点绑定
        ))
        if replayed and skill["status"] == to_stage:
            return approval, skill, True          # 全量重放
        # 首次 / 崩溃窗口 catch-up（凭据在而真源未达）
        skills = _load(root)
        skill = _find(skills, slug)
        skill["status"] = to_stage
        skill["history"].append({"at": _now(), "stage": to_stage,
                                 "by": by, "note": reason or to_stage,
                                 "approval_id": approval.approval_id})
        if to_stage == "approved":
            skill["approved_at"] = _now()
        _save(root, skills)
        return approval, skill, False
    finally:
        if own:
            store.close()


# ----------------------------------------------------------------------
def audit_report(root: Optional[Path] = None) -> Dict:
    """Skill 目录审计：八要素齐全性 + 绕门检测 + 凭据痕迹交叉校验。"""
    skills = _load(root)
    _, adb = _resolve(root)
    store = ApprovalStore(adb)
    try:
        skill_creds = [c for c in store.list()
                       if c.target_id.startswith("skill:")]
    finally:
        store.close()
    creds = {c.idempotency_key for c in skill_creds}

    findings, checks = [], []
    for s in skills:
        slug, st = s.get("slug", ""), s.get("status", "")
        if st in ("approved", "deprecated"):
            missing = _missing_elements(s)
            checks.append({"slug": slug, "status": st,
                           "missing_elements": missing})
            if missing and st == "approved":
                findings.append("approved 但八要素不全：%s 缺 %s"
                                % (slug, ",".join(missing)))
        if st == "approved":
            # 该状态必须有凭据背书
            hit = any(k.startswith("skill:%s:" % slug)
                      and k.endswith(">approved") for k in creds)
            if not hit:
                findings.append("bypass_approve: %s 已 approved 无凭据"
                                % slug)
        if st == "deprecated":
            hit = any(k.startswith("skill:%s:" % slug)
                      and k.endswith(">deprecated") for k in creds)
            if not hit:
                findings.append("bypass_deprecate: %s 已 deprecated 无凭据"
                                % slug)
    # 交叉校验：凭据轮次 vs history 痕迹（skill=trace 域，stage 即痕迹）
    traces = {}
    for s in skills:
        traces["skill:%s" % s.get("slug", "")] = {
            "statuses": [h.get("stage") for h in (s.get("history") or [])
                         if isinstance(h, dict)],
            "consumed_keys": set(),
        }
    cross_v = cross_check("skill", skill_creds, traces,
                          {"skill:%s" % s.get("slug", ""): s.get("status", "")
                           for s in skills})
    verdict = verdict_for(
        ([{"severity": "FAIL"}] if findings else []) + cross_v)
    return {"verdict": verdict, "skill_total": len(skills),
            "by_status": {st: sum(1 for s in skills
                                  if s.get("status") == st)
                          for st in STAGES},
            "checks": checks, "findings": findings,
            "cross_check": cross_v, "read_only": True}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="M2-P3 统一 Skill 目录")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add")
    for f in ("slug", "title"):
        p_add.add_argument(f)
    p_add.add_argument("--by", required=True)
    p_add.add_argument("--owner", default="")
    p_add.add_argument("--domains", default="")
    for k in ELEMENTS:
        p_add.add_argument("--" + k.replace("_", "-"), default="")

    p_mv = sub.add_parser("move")
    p_mv.add_argument("slug")
    p_mv.add_argument("to_stage", choices=["reviewed", "draft"])
    p_mv.add_argument("--by", required=True)
    p_mv.add_argument("--note", default="")

    p_ap = sub.add_parser("approve")
    p_ap.add_argument("slug")
    p_ap.add_argument("to_stage", choices=["approved", "deprecated"])
    p_ap.add_argument("--by", required=True)
    p_ap.add_argument("--reason", default="")

    sub.add_parser("list")
    p_show = sub.add_parser("show")
    p_show.add_argument("slug")
    sub.add_parser("audit")

    args = ap.parse_args(argv)
    if args.cmd == "add":
        s = add_skill(_AGENT_PLATFORM, args.slug, args.title, args.by,
                      owner=args.owner,
                      domains=[d for d in args.domains.split(",") if d],
                      **{k: getattr(args, k) for k in ELEMENTS})
        print(json.dumps({"slug": s["slug"], "status": s["status"]},
                         ensure_ascii=False))
    elif args.cmd == "move":
        s = move_stage(_AGENT_PLATFORM, args.slug, args.to_stage,
                       args.by, args.note)
        print(json.dumps({"slug": s["slug"], "status": s["status"]},
                         ensure_ascii=False))
    elif args.cmd == "approve":
        apv, s, replayed = approve(_AGENT_PLATFORM, args.slug,
                                   args.to_stage, args.by, args.reason)
        print(json.dumps({"approval_id": apv.approval_id,
                          "status": s["status"], "replayed": replayed},
                         ensure_ascii=False))
    elif args.cmd == "list":
        for s in _load():
            print("%-24s %-10s %s %s"
                  % (s["slug"], s["status"], s.get("version", "?"),
                     s.get("title", "")))
    elif args.cmd == "show":
        print(json.dumps(_find(_load(), args.slug), ensure_ascii=False,
                         indent=1))
    else:
        print(json.dumps(audit_report(), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
