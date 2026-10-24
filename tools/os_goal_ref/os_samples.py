# -*- coding: utf-8 -*-
"""合成事件账本样本工厂 —— 给 os_gate_check 自测与演示用。

纪律：
  - 全部为**合成数据**，不含任何真实采集、真实交易、真实用户信息。
  - 只写调用方指定目录（测试用 tmp_path / 演示用 samples/out）。
  - 正例 P1/P2/P3 与 18 个负例一一对应 05_负例与故障注入矩阵.md。

用法：
  python os_samples.py --out samples/out          # 生成全部样本
  python os_samples.py --list                     # 列出样本
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

BEIJING = timezone(timedelta(hours=8))
ITEM = "ITM-20261024-0001"
START = datetime(2026, 10, 24, 8, 30, 0, tzinfo=BEIJING)
STEP = timedelta(minutes=2)
ISOLATION_DIR = "portal/data/os_items/%s/workspace" % ITEM


# ---------------------------------------------------------------- 组装

def _ev(etype: str, actor: Dict[str, Any], payload: Dict[str, Any],
        refs: Optional[Dict[str, Any]] = None,
        idem: Optional[str] = None) -> Dict[str, Any]:
    ev = {"item_id": ITEM, "type": etype, "actor": actor,
          "refs": refs or {}, "payload": payload}
    if idem:
        ev["idempotency_key"] = idem
    return ev


def base_events(claim: str = "real", rule_only: bool = False,
                env: str = "isolated",
                signer_method: str = "session_nonce") -> List[Dict[str, Any]]:
    """生成一条完整正例事件链（未加 seq/hash）。"""
    user = {"kind": "user", "id": "laotian", "seat": "laotian"}
    system = {"kind": "system", "id": "os_item_service"}
    consumer = {"kind": "consumer", "id": "DEVWORKER-01", "seat": "DEVWORKER-01"}
    qa = {"kind": "consumer", "id": "QA1", "seat": "QA1"}
    b = {"brief_version": "v1"}
    brief = {
        "brief_version": "v1",
        "goals": ["把老田的一句需求变成可在隔离环境试用的小页面"],
        "non_goals": ["不接真实数据", "不发布到生产", "不涉及交易"],
        "constraints": ["仅本机隔离目录", "产物可撤回"],
        "success_criteria": [
            {"id": "SC1", "statement": "页面能打开并显示需求标题",
             "verify_method": "浏览器打开 /preview 断言标题文本"},
            {"id": "SC2", "statement": "修改一处文案后能返工出新版本",
             "verify_method": "验收回执 changes_requested 后生成 v2 需求与产物 a2"},
        ],
        "open_questions": [],
    }
    call_p = {"provider": "ollama", "model": "qwen3.5:9b",
              "call_id": "CALL-20261024-0837-zhuge",
              "prompt_sha256": "sha256:" + "a" * 64,
              "response_sha256": "sha256:" + "b" * 64,
              "raw_available": True, "latency_ms": 2140}
    call_z = dict(call_p, call_id="CALL-20261024-0838-zhaoyun",
                  response_sha256="sha256:" + "c" * 64)
    if rule_only:
        roles = [
            {"role": "诸葛亮", "source_kind": "rule_assisted",
             "rules_ref": "council_ops.auto_discuss 规则表",
             "call": {"label": "规则辅助（非模型结论）"},
             "counter_arguments": ["（规则）先做最小可试用页，不做多页"],
             "unknowns": ["真实模型未调用"]},
            {"role": "赵云", "source_kind": "rule_assisted",
             "rules_ref": "docs/plans/2026-10-19_Agent危险操作治理规范",
             "call": {"label": "规则辅助（非模型结论）"},
             "counter_arguments": ["隔离目录外的读写必须拒绝，先证明拒绝"],
             "unknowns": []},
            {"role": "田丰", "source_kind": "missing",
             "failure_reason": "诸葛亮 key 未注入，无法做独立核查面",
             "counter_arguments": [], "unknowns": ["核查席位缺失"]},
        ]
    else:
        roles = [
            {"role": "诸葛亮", "source_kind": "model_call", "call": call_p,
             "evidence_refs": ["EV-ITEM-TEXT"],
             "counter_arguments": ["把范围压在单页，先不接登录与真实数据"],
             "unknowns": ["真实预览须人工打开确认"]},
            {"role": "赵云", "source_kind": "model_call", "call": call_z,
             "evidence_refs": ["EV-ISOLATION-RULES"],
             "counter_arguments": ["必须先在笼子里证明越界写入被拒，再做生成"],
             "unknowns": ["回退目录若被人为改过需重新取指纹"]},
            {"role": "田丰", "source_kind": "rule_assisted",
             "rules_ref": "docs/plans/2026-10-19_Agent危险操作治理规范_K-STOCK-416.md",
             "call": {"label": "规则辅助（非模型结论）"},
             "counter_arguments": ["权威前置未核验前只许隔离，不许称已派工"],
             "unknowns": ["真实工单消费者尚未接通"]},
        ]
    roles = copy.deepcopy(roles)
    card = {
        "card_version": "c1", "brief_version": "v1",
        "recommended": {"option": "A｜隔离单页原型＋自动化冒烟",
                        "why": "可逆、可试用、验收标准可机械核对"},
        "discarded": [{"option": "B｜直接改现网首页",
                       "why": "不可逆且用户未确认前不得动现网"},
                      {"option": "C｜做三页流程",
                       "why": "超出首版验收标准，返工成本高"}],
        "alternatives": ["上策 A＝隔离单页原型；中策 只出设计稿；下策 只出文档"],
        "risks": ["隔离目录可能被误改——产物带 sha256 + 撤回配方"],
        "acceptance_method": "本人打开预览，接受或指出一处修改",
        "evidence_refs": ["ARG-v1"], "disconfirm_refs": ["ARG-v1"],
        "unknowns": (["诸葛亮真实模型未调用（key 未注入）", "田丰核查席位缺失"]
                     if rule_only else []),
    }
    grant = {
        "grant_id": "GRT-20261024-0001", "card_version": "c1",
        "actions": ["prototype.generate", "prototype.preview",
                    "prototype.test", "workspace.cleanup"],
        "env": env,
        "target": {"paths": ["portal/data/os_items/%s/" % ITEM]},
        "valid_until": "2026-10-25T20:00:00+08:00",
        "limits": {"max_cost_cny": 0, "reversibility_required": True},
        "signer": {"id": "laotian", "method": signer_method,
                   "nonce_id": "NONCE-20261024-0842"},
    }
    claim_payload = {
        "workorder_id": "WO-OS-20261024-01",
        "claim": {"consumer_id": "DEVWORKER-01", "seat": "DEVWORKER-01",
                  "method": claim, "claimed_at": "2026-10-24T08:48:00+08:00"},
    }
    if claim == "simulated":
        claim_payload["isolation_label"] = "隔离验证/未获真实认领"
    events = [
        _ev("item.created", user,
            {"title": "做一个可试用的需求小页", "source_text": "我想看看零界能不能把一句想法做成可点开的东西",
             "tenant": "personal"},
            idem="IDEM-ITEM-0001"),
        _ev("brief.drafted", system, brief),
        _ev("brief.frozen", user, brief, b, idem="IDEM-BRIEF-V1"),
        _ev("argument.recorded", system,
            {"argument_id": "ARG-v1", "brief_version": "v1", "roles": roles}, b),
        _ev("card.drafted", system, card, b),
        _ev("card.frozen", system, card, b, idem="IDEM-CARD-C1"),
        _ev("card.selected", user, {"card_version": "c1"}),
        _ev("grant.signed", user, grant, {"card_version": "c1"}, idem="IDEM-GRANT-0001"),
        _ev("workorder.created", system,
            {"workorder_id": "WO-OS-20261024-01", "grant_id": "GRT-20261024-0001",
             "card_version": "c1", "action": "prototype.generate",
             "task_spec": "在隔离目录生成单页原型并跑冒烟",
             "prerequisites_checked": True,
             "authority_refs": [{"kind": "devtask", "id": "OS-CLOSE-03"}]},
            {"grant_id": "GRT-20261024-0001"}, idem="IDEM-WO-0001"),
        _ev("workorder.claimed", consumer, claim_payload,
            {"workorder_id": "WO-OS-20261024-01"}),
        _ev("artifact.produced", consumer,
            {"artifact_id": "ART-OS-20261024-01-a1", "workorder_id": "WO-OS-20261024-01",
             "version": "a1",
             "path": ISOLATION_DIR + "/index.html",
             "sha256": "sha256:" + "d" * 64, "bytes": 2048,
             "preview_uri": "/api/os/item/%s/preview/a1" % ITEM,
             "rollback": {"method": "delete_dir",
                          "recipe": "删除 %s 即回到执行前；SHA256SUMS 保留对账" % ISOLATION_DIR}},
            {"workorder_id": "WO-OS-20261024-01"}),
        _ev("test.recorded", qa,
            {"evidence_id": "TES-OS-20261024-01", "artifact_id": "ART-OS-20261024-01-a1",
             "type": "browser", "command": "node verify_preview.mjs",
             "result": "pass", "output_sha256": "sha256:" + "e" * 64,
             "executed_by": "QA1"},
            {"artifact_id": "ART-OS-20261024-01-a1"}),
        _ev("trial.opened", user,
            {"artifact_id": "ART-OS-20261024-01-a1", "opened_by": "laotian"},
            {"artifact_id": "ART-OS-20261024-01-a1"}),
        _ev("verdict.recorded", user,
            {"verdict_id": "VER-20261024-0001",
             "artifact_id": "ART-OS-20261024-01-a1",
             "decision": "accepted", "feedback": "能打开也可以用，接受。",
             "brief_version_after": None},
            {"artifact_id": "ART-OS-20261024-01-a1"}, idem="IDEM-VERDICT-0001"),
    ]
    return events


def seal(events: List[Dict[str, Any]],
         schema_version: str = "os-item/1.0") -> List[Dict[str, Any]]:
    """补 seq/event_id/at/schema_version/prev_hash/hash（按当前顺序重算整条链）。"""
    from os_gate_check import event_hash  # 延迟导入，避免循环
    out: List[Dict[str, Any]] = []
    prev = None
    for i, ev in enumerate(events, 1):
        rec = copy.deepcopy(ev)
        rec.pop("hash", None)
        rec.pop("prev_hash", None)
        rec["schema_version"] = schema_version
        rec["seq"] = i
        rec["event_id"] = "EVT-%s-%03d" % (rec.get("item_id", ITEM), i)
        rec["at"] = (START + STEP * (i - 1)).isoformat(timespec="seconds")
        rec["prev_hash"] = prev
        rec["hash"] = event_hash(rec)
        prev = rec["hash"]
        out.append(rec)
    return out


def find(events: List[Dict[str, Any]], etype: str) -> Dict[str, Any]:
    for ev in events:
        if ev.get("type") == etype:
            return ev
    raise KeyError("样本中无事件类型：%s" % etype)


def drop(events: List[Dict[str, Any]], etype: str) -> List[Dict[str, Any]]:
    return [ev for ev in events if ev.get("type") != etype]


# ---------------------------------------------------------------- 负例定义

def _role(events: List[Dict[str, Any]], name: str) -> Dict[str, Any]:
    for role in find(events, "argument.recorded")["payload"]["roles"]:
        if role["role"] == name:
            return role
    raise KeyError(name)


def neg_no_trial(e):
    return drop(e, "trial.opened")


def neg_old_card(e):
    find(e, "grant.signed")["payload"]["card_version"] = "c0"
    return e


def neg_scope_expand(e):
    find(e, "grant.signed")["payload"]["actions"].append("data.write.prod")
    return e


def neg_forged_confirm(e):
    g = find(e, "grant.signed")["payload"]["signer"]
    g["method"] = "client_confirmed:true"
    g.pop("nonce_id", None)
    return e


def neg_agent_freeze(e):
    find(e, "brief.frozen")["actor"] = {"kind": "agent", "id": "zhuge", "seat": "zhuge"}
    return e


def neg_no_claim(e):
    return drop(e, "workorder.claimed")


def neg_no_test(e):
    return drop(e, "test.recorded")


def neg_version_mismatch(e):
    find(e, "argument.recorded")["payload"]["brief_version"] = "v2"
    return e


def neg_missing_argument(e):
    return drop(e, "argument.recorded")


def neg_tamper(e):
    """篡改但不重封：哈希链必断。"""
    find(e, "grant.signed")["payload"]["actions"].append("prototype.preview")
    return e


def neg_cross_item(e):
    find(e, "card.frozen")["item_id"] = "ITM-20261024-9999"
    return e


def neg_expired_grant(e):
    find(e, "grant.signed")["payload"]["valid_until"] = "2026-10-24T08:00:00+08:00"
    return e


def neg_duplicate_submit(e):
    wo = copy.deepcopy(find(e, "workorder.created"))
    idx = e.index(wo)
    e.insert(idx + 1, copy.deepcopy(wo))
    return e


def neg_fake_model(e):
    _role(e, "诸葛亮")["call"].pop("call_id", None)
    return e


def neg_production_env(e):
    find(e, "grant.signed")["payload"]["env"] = "production"
    return e


def neg_path_escape(e):
    find(e, "artifact.produced")["payload"]["path"] = "C:/Windows/Temp/proto.html"
    return e


def neg_agent_verdict(e):
    find(e, "verdict.recorded")["actor"] = {"kind": "agent", "id": "zhuge", "seat": "zhuge"}
    return e


def neg_no_artifact(e):
    return drop(e, "artifact.produced")


def neg_no_counter(e):
    for role in find(e, "argument.recorded")["payload"]["roles"]:
        role["counter_arguments"] = []
    return e


def neg_mock_argument(e):
    _role(e, "诸葛亮")["source_kind"] = "mock"
    return e


NEGATIVES = [
    ("N01_no_trial", neg_no_trial, "TRIAL_NOT_OPENED"),
    ("N02_stale_card", neg_old_card, "STALE_CARD"),
    ("N03_scope_expand", neg_scope_expand, "GRANT_SCOPE"),
    ("N04_forged_confirm", neg_forged_confirm, "ACTOR_TRUST"),
    ("N05_agent_freeze", neg_agent_freeze, "ACTOR_TRUST"),
    ("N06_no_claim", neg_no_claim, "TRANSITION"),
    ("N07_no_test", neg_no_test, "TRANSITION"),
    ("N08_version_mismatch", neg_version_mismatch, "VERSION_MATCH"),
    ("N09_missing_argument", neg_missing_argument, "CARD_ARGUMENT"),
    ("N10_tamper", neg_tamper, "CHAIN_HASH"),
    ("N11_cross_item", neg_cross_item, "ITEM_CONSISTENCY"),
    ("N12_expired_grant", neg_expired_grant, "GRANT_EXPIRED"),
    ("N13_duplicate_submit", neg_duplicate_submit, "IDEMPOTENCY"),
    ("N14_fake_model", neg_fake_model, "MODEL_CALL"),
    ("N16_production_env", neg_production_env, "ENV_ISOLATED"),
    ("N17_path_escape", neg_path_escape, "ARTIFACT_CONFINEMENT"),
    ("N18_agent_verdict", neg_agent_verdict, "VERDICT_NOT_USER"),
    ("N19_no_artifact", neg_no_artifact, "TRANSITION"),
    ("N20_no_counter", neg_no_counter, "ARGUMENT_COUNTER"),
    ("N21_mock_argument", neg_mock_argument, "MOCK"),
]


# ---------------------------------------------------------------- 写出

def write_item(out_root: str, name: str, events: List[Dict[str, Any]],
               reseal: bool = True) -> str:
    if reseal:
        events = seal(events)
    d = os.path.join(out_root, name)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "events.jsonl"), "w", encoding="utf-8", newline="\n") as fh:
        for ev in events:
            fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
    return d


def _blocked_events() -> List[Dict[str, Any]]:
    """P3：授权签准后，权威前置未核验 → 按纪律停住（不派工）。"""
    from os_gate_check import event_hash

    blocked = base_events("real")
    for t in ("workorder.created", "workorder.claimed", "artifact.produced",
              "test.recorded", "trial.opened", "verdict.recorded"):
        blocked = drop(blocked, t)
    blocked = seal(blocked)
    ev = {"schema_version": "os-item/1.0", "item_id": ITEM, "type": "gate.blocked",
          "actor": {"kind": "system", "id": "os_item_service"},
          "refs": {"gate": "PREFLIGHT"},
          "payload": {"gate": "PREFLIGHT", "reason_code": "AUTHORITY_UNVERIFIED",
                      "detail": "真实工单消费者未接通，按纪律停住"}}
    seq = len(blocked) + 1
    ev.update({"seq": seq, "event_id": "EVT-%s-%03d" % (ITEM, seq),
               "at": (START + STEP * (seq - 1)).isoformat(timespec="seconds")})
    ev["prev_hash"] = blocked[-1]["hash"]
    ev["hash"] = event_hash(ev)
    blocked.append(ev)
    return blocked


def build_all(out_root: str) -> List[str]:
    made = []
    made.append(write_item(out_root, "P1_pass_real_claim", base_events("real")))
    made.append(write_item(out_root, "P2_isolated_only_simulated_claim",
                           base_events("simulated")))
    made.append(write_item(out_root, "P3_blocked", _blocked_events(),
                           reseal=False))
    made.append(write_item(out_root, "P4_rule_only", base_events("real", rule_only=True)))
    for name, fn, _cid in NEGATIVES:
        if name == "N10_tamper":
            # 篡改类负例：先封链，再改内容且不重封——哈希链必须断
            tampered = fn(seal(base_events("real")))
            made.append(write_item(out_root, name, tampered, reseal=False))
        else:
            made.append(write_item(out_root, name, fn(base_events("real"))))
    return made


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="samples/out")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args(argv)
    if a.list:
        print("正例: P1_pass_real_claim / P2_isolated_only_simulated_claim / "
              "P3_blocked / P4_rule_only")
        print("负例: " + " / ".join(n for n, _f, _c in NEGATIVES))
        return 0
    made = build_all(a.out)
    print("已生成 %d 个样本到 %s" % (len(made), a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
