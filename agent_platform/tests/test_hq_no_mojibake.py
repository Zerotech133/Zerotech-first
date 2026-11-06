# -*- coding: utf-8 -*-
"""会议纪要编码守卫（R12 D6）。

背景：`hq-20261029-054846.json` / `hq-20261103-075303.json` 的部分条目在**入参**
阶段就被代码页替换成 0x3F（`?`），写入端编码本来是对的，落盘后不可逆。

两道防线：
1. 入口拒收——`validate()` 对 agenda/decisions/orders/transcript 里出现连续 `?`
   的请求返回 `bad_encoding`，从今天起不再产生新乱码；
2. 全量守卫——本文件扫 `data/meetings/hq/*.json`。**不在豁免名单里的文件一旦出现
   乱码即失败**；名单内文件按「未作废乱码条数」钉死基线，数字变化（处置完成或
   新坏一批）都必须回来更新基线，不允许静默漂移。
"""
import json
import sys
from pathlib import Path

import pytest

WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT / "agent_platform"))

from legion_command import hq_meeting as hq  # noqa: E402

MEETINGS = WT / "agent_platform" / "data" / "meetings" / "hq"

# 存量乱码文件 → 仍未作废的乱码条数基线。
# 数字变小说明有人处置了（请下调）；变大说明又坏了一批（先查入参再从源头修）。
LEGACY_BASELINE = {
    "hq-20261028-225844.json": 2,
    "hq-20261029-054846.json": 0,   # 2026-11-06 已作废 3 条（agenda/decision/transcript）
    "hq-20261029-070823.json": 2,
    "hq-20261029-070921.json": 2,
    "hq-20261029-080811.json": 2,
    "hq-20261103-075303.json": 0,   # 2026-11-06 已作废 1 条（agenda[1]）
}


def _valid(**kw):
    body = {
        "type": "morning",
        "attendees": ["A0", "AI1"],
        "agenda": [{"item": "数据仓口径对齐", "owner": "DATA1", "minutes": 5}],
        "decisions": [{"decision": "本周完成对账", "owner": "DATA1"}],
    }
    body.update(kw)
    return body


def test_looks_garbled_detects_codepage_loss():
    assert hq.looks_garbled("????[OBSERVE]:?????")
    assert hq.looks_garbled("abc??def")
    assert not hq.looks_garbled("正常中文，含问号？全角不算")
    assert not hq.looks_garbled("一个问题?两个问题?")   # 单问号不拒收
    assert not hq.looks_garbled("")


@pytest.mark.parametrize("section,payload", [
    ("agenda", {"agenda": [{"item": "??????:??/??", "owner": "DATA1"}]}),
    ("decisions", {"decisions": [{"decision": "????[OBSERVE]:?????", "owner": "DATA1"}]}),
    ("orders", {"orders": [{"title": "??????", "type": "work", "owner": "DATA1"}]}),
    ("transcript", {"transcript": [{"by": "HUANGZHONG", "text": "????????????"}]}),
])
def test_validate_rejects_garbled_payload(section, payload):
    """入口拒收：四个文本入口任一出现连续 ? 都返回 bad_encoding。"""
    doc, err = hq.validate(_valid(**payload))
    assert doc is None and err == "bad_encoding", section


def test_validate_accepts_clean_chinese():
    doc, err = hq.validate(_valid())
    assert err is None and doc["agenda"][0]["item"] == "数据仓口径对齐"


def _scan_file(path: Path) -> tuple[int, int]:
    """返回（未作废的乱码条数, 全部乱码条数）。"""
    doc = json.loads(path.read_text(encoding="utf-8"))
    active = total = 0
    for section in ("agenda", "decisions", "transcript", "orders", "acceptance"):
        for row in doc.get(section) or []:
            if not isinstance(row, dict):
                continue
            blob = json.dumps(row, ensure_ascii=False)
            if not hq.looks_garbled(blob):
                continue
            total += 1
            if row.get("status") != "voided":
                active += 1
    return active, total


def test_no_mojibake_outside_legacy_allowlist():
    """新文件一旦出现乱码必须红——不允许用「再加一条豁免」蒙过去。"""
    if not MEETINGS.is_dir():
        pytest.skip("会议目录不存在")
    offenders = []
    for p in sorted(MEETINGS.glob("hq-*.json")):
        active, _ = _scan_file(p)
        if active and p.name not in LEGACY_BASELINE:
            offenders.append("%s（%d 条）" % (p.name, active))
    assert not offenders, (
        "出现未登记的乱码会议：%s\n乱码源头在入参编码（控制台/命令行代码页），"
        "先修输入再考虑是否作废。" % "、".join(offenders))


@pytest.mark.parametrize("name,expect", sorted(LEGACY_BASELINE.items()))
def test_legacy_baseline_matches(name, expect):
    """存量基线钉死：处置完成要下调，新坏一批要上调——不许静默漂移。"""
    p = MEETINGS / name
    if not p.is_file():
        pytest.skip("%s 已归档或删除，请同步清理基线" % name)
    active, total = _scan_file(p)
    assert active == expect, (
        "%s 未作废乱码条数 %d != 基线 %d（全部乱码 %d 条）。"
        "处置了旧条目或新坏了条目，都要回来更新 LEGACY_BASELINE。"
        % (name, active, expect, total))


def test_voided_entries_keep_original_text():
    """作废只加元数据，**不得删改原文**——原文是唯一证据。"""
    p = MEETINGS / "hq-20261029-054846.json"
    if not p.is_file():
        pytest.skip("样本文件不存在")
    doc = json.loads(p.read_text(encoding="utf-8"))
    voided = [r for sec in ("agenda", "decisions", "transcript")
              for r in doc.get(sec) or []
              if isinstance(r, dict) and r.get("status") == "voided"]
    assert voided, "样本应包含已作废条目"
    for r in voided:
        assert r.get("void_reason") == "encoding_loss"
        assert r.get("voided_at")
        body = r.get("item") or r.get("decision") or r.get("text") or ""
        assert hq.looks_garbled(body), "作废条目的原文必须原样保留（不允许清空/改写）"
