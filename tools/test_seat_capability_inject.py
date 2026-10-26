# -*- coding: utf-8 -*-
"""三席位能力/技能卡结构测试。

K-QUANT-20261026-TFSEAT：田丰 15 能力 / 15 卡（原有断言保留）。
K-QUANT-20261026-SEATCARDS：赵云补到 14 能力 / 14 卡；诸葛亮 8 能力 / 8 卡
（3 张既有卡不重复注入 + 5 张新卡），并新增「三席位每项能力都有卡」一致性断言。
"""
import importlib.util
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
MOD_PATH = HERE / "seat_capability_inject.py"


def _load():
    spec = importlib.util.spec_from_file_location("seat_capability_inject_t", MOD_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


M = _load()

SEATS = ("tianfeng", "zhaoyun", "zhuge")

TIANFENG_EXPECTED = {
    "devtask", "ticket-gate", "council-route", "health-card", "task-exit-audit",
    "experience-review", "z-series", "memory-card", "council-hall", "docs-index",
    "llm-glm", "mutual-review", "case-sign-chain", "governance-ai",
    "worktree-hygiene",
    "product-spec", "architecture-design", "code-reference",
    "task-orchestration", "decision-arbitration",
    "rd-planning", "workflow-dynamics", "dispatch-live",
    "implementation-supervision", "qa-acceptance", "release-rollback",
    "codebase-intelligence", "sandbox-verification", "eval-harness", "observability-trace",
    "spec-driven-delivery", "security-gate", "approval-inbox", "browser-verification",
    "memory-rag", "durable-workflow",
}

ZHAOYUN_EXPECTED = {
    "task-exit-audit", "health-card", "task-launch-probe", "backup-bundles",
    "worktree-hygiene", "docs-archive", "experience-write", "mutual-review",
    "lookahead-guard", "council-hall", "harness-runner", "llm-deepseek",
    "governance-ai", "case-sign-chain",
}

ZHONGE_EXPECTED = {
    "adversarial-review", "evidence-audit", "pre-mortem",
    "zhuge-brief", "os-suggest", "council-advise", "brief-trend", "llm-gpt6",
}

# 诸葛亮 OpenClaw workspace 既有 3 卡：不在 SKILL_CARDS 内（注入器缺才建、不覆盖重写）。
ZHONGE_PREEXISTING_CARDS = {"adversarial-review", "evidence-audit", "pre-mortem"}
# 本工单实际新增的卡：赵云 11 张（既有 3 张已落盘）+ 诸葛 5 张 = dry-run 预期 16 张。
INJECTED_ZHAOYUN_CARDS = ZHAOYUN_EXPECTED - {"task-exit-audit", "task-launch-probe", "mutual-review"}
INJECTED_ZHONGE_CARDS = ZHONGE_EXPECTED - ZHONGE_PREEXISTING_CARDS

REQUIRED_KEYS = {"name", "what", "how", "boundary", "status"}
VALID_STATUS = {"ready", "pending_key", "pending_wiring"}
PENDING_STATUS = {"pending_key", "pending_wiring"}


def _card_names(seat):
    """该席位当前可用的全部技能卡名（含历史已落盘、不在 SKILL_CARDS 里的卡）。"""
    names = set(M.SKILL_CARDS[seat])
    if seat == "zhuge":
        names |= ZHONGE_PREEXISTING_CARDS
    return names


# ---------------- 田丰（ROLEV3：15 能力 + 5 全在性能力 = 20 能力 / 20 卡） ----------------

def test_tianfeng_capability_set_complete():
    caps = M.CAPABILITIES["tianfeng"]
    names = [c["name"] for c in caps]
    assert len(names) == len(set(names)), "能力名重复"
    assert set(names) == TIANFENG_EXPECTED
    assert len(caps) == 36


def test_tianfeng_capability_fields_valid():
    for c in M.CAPABILITIES["tianfeng"]:
        assert REQUIRED_KEYS <= set(c), c
        assert c["status"] in VALID_STATUS, c
        assert c["what"] and c["how"] and c["boundary"], c


def test_every_tianfeng_capability_has_skill_card():
    card_names = set(M.SKILL_CARDS["tianfeng"])
    cap_names = {c["name"] for c in M.CAPABILITIES["tianfeng"]}
    assert card_names == cap_names, "缺卡: %s / 多卡: %s" % (
        sorted(cap_names - card_names), sorted(card_names - cap_names))


# ---------------- 赵云（K-QUANT-20261026-SEATCARDS：14 能力 / 14 卡） ----------------

def test_zhaoyun_capability_set_complete():
    caps = M.CAPABILITIES["zhaoyun"]
    names = [c["name"] for c in caps]
    assert len(names) == len(set(names)), "赵云能力名重复"
    assert set(names) == ZHAOYUN_EXPECTED
    assert len(caps) == 14


def test_zhaoyun_skill_card_set_complete():
    card_names = set(M.SKILL_CARDS["zhaoyun"])
    assert len(card_names) == 14
    assert card_names == ZHAOYUN_EXPECTED, "缺卡: %s / 多卡: %s" % (
        sorted(ZHAOYUN_EXPECTED - card_names), sorted(card_names - ZHAOYUN_EXPECTED))


# ---------------- 诸葛亮（K-QUANT-20261026-SEATCARDS：8 能力 / 8 卡） ----------------

def test_zhuge_capability_set_complete():
    caps = M.CAPABILITIES["zhuge"]
    names = [c["name"] for c in caps]
    assert len(names) == len(set(names)), "诸葛亮能力名重复"
    assert set(names) == ZHONGE_EXPECTED
    assert len(caps) == 8


def test_zhuge_skill_card_set_complete():
    injected = set(M.SKILL_CARDS["zhuge"])
    assert injected == INJECTED_ZHONGE_CARDS, "本工单应新增 5 张: %s" % sorted(INJECTED_ZHONGE_CARDS)
    assert injected & ZHONGE_PREEXISTING_CARDS == set(), "既有 3 卡不得重复注入（不覆盖重写）"
    assert injected | ZHONGE_PREEXISTING_CARDS == ZHONGE_EXPECTED
    assert len(injected | ZHONGE_PREEXISTING_CARDS) == 8


# ---------------- 三席位一致性 / 卡格式 / pending 标注 ----------------

def test_skill_card_format():
    for seat in SEATS:
        for name, body in M.SKILL_CARDS[seat].items():
            assert body.startswith("---\nname: "), (seat, name)
            header = body.split("---")[1]
            assert "description:" in header, (seat, name)
            assert "name: %s" % name in header, (seat, name)
            assert "\n# " in body, (seat, name)


def test_every_seat_capability_has_skill_card():
    """三席位一致性：每项能力都有卡，且不存在能力清单之外的幽灵卡。"""
    for seat in SEATS:
        cap_names = {c["name"] for c in M.CAPABILITIES[seat]}
        card_names = _card_names(seat)
        assert cap_names - card_names == set(), "%s 缺卡: %s" % (
            seat, sorted(cap_names - card_names))
        assert card_names - cap_names == set(), "%s 多卡: %s" % (
            seat, sorted(card_names - cap_names))


def test_every_seat_capability_fields_valid():
    for seat in SEATS:
        for c in M.CAPABILITIES[seat]:
            assert REQUIRED_KEYS <= set(c), (seat, c)
            assert c["status"] in VALID_STATUS, (seat, c)
            assert c["what"] and c["how"] and c["boundary"], (seat, c)


def test_pending_capabilities_marked_pending_in_card():
    """pending 项必须在卡里如实标注 pending_key/pending_wiring，不虚报 ready。"""
    pending = [(seat, c["name"], c["status"])
               for seat in SEATS for c in M.CAPABILITIES[seat]
               if c["status"] in PENDING_STATUS]
    assert pending, "缺少 pending 项样本（诸葛 brief-trend/llm-gpt6）"
    for seat, name, status in pending:
        card = M.SKILL_CARDS[seat].get(name, "")
        assert status in card, "卡未如实标注 %s: %s/%s" % (status, seat, name)


def test_new_skill_card_count_matches_ticket():
    """工单验收口径：dry-run 预期新增卡 = 赵云 11 + 诸葛 5 = 16（田丰 15 张已落盘）。"""
    assert len(INJECTED_ZHAOYUN_CARDS) == 11
    assert len(INJECTED_ZHONGE_CARDS) == 5
    assert len(INJECTED_ZHAOYUN_CARDS) + len(INJECTED_ZHONGE_CARDS) == 16
