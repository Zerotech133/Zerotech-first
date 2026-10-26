# -*- coding: utf-8 -*-
"""OSLINK-W4 契约骨架测试（v1.0 已签认；当前占位 skip，实施期逐条落实现后转实跑）。

对应契约：outputs/2026-10-25_OSLINK-W4_司马懿到田丰归口契约v0.1_草案.md
归属单：K-QUANT-20261025-OSLEG-DRAFT（归口单 K-QUANT-20261023-OSLINK 的 W4）

为什么先写骨架而不是先写实现
--------------------------
W1/W2/W3/W5 四单已合并，W4 是"归口段"——消息从司马懿(QS1)进、田丰(A0)受理答复。
契约里最贵的不是 happy path，是六条负例（缺消费/重复/拒接/缺report_id/缺trace_id/通道混用）。
先把负例写成可执行的用例名（skip 状态），实施时"填一个删一个 skip"，
避免最后靠人脑回忆契约要求。

运行：python -X utf8 -B -m pytest quant_backtest/tests/test_osleg_contract_skeleton.py -q
"""
from __future__ import annotations

import pytest

# 契约 v0.1 尚未签认前，全部用例 skip；A0 签认后逐条移除 skip。
DRAFT = pytest.mark.skip(reason="契约 v1.0 已签认（2026-10-26 老田代 A0，见契约 §十）；实施期落实现后逐条去 skip，未接 8795/真实消费者")


@DRAFT
def test_c1_submit_returns_msg_id(tmp_path):
    """§三/§四：submit 必须落账并返回 msg_id；accepted_by 初始为空。"""
    raise NotImplementedError


@DRAFT
def test_c2_lifecycle_all_states_reachable(tmp_path):
    """§四：submitted→accepted→consumed→answered→delivered 全状态可达。"""
    raise NotImplementedError


@DRAFT
def test_c3_answer_must_link_report_id(tmp_path):
    """§五-1：答复的 report_id 必须等于原提交；否则 receipt().linked=false。"""
    raise NotImplementedError


@DRAFT
def test_c4_trace_id_consistent_across_stages(tmp_path):
    """§五-2：提交/消费/答复/投递四步 trace_id 一致，缺失则为 unknown。"""
    raise NotImplementedError


@DRAFT
def test_c5_receipt_aggregates_both_channels(tmp_path):
    """§五-3：按 report_id 聚合时，同进程 inbox 与 HTTP 8795 两条记录都要出现。"""
    raise NotImplementedError


@DRAFT
def test_n1_expire_without_consumer(tmp_path):
    """§六 N1：无消费者超过 T_expire → expired，receipt.alive=false。"""
    raise NotImplementedError


@DRAFT
def test_n2_duplicate_submit_is_idempotent(tmp_path):
    """§六 N2：同 payload 重复提交 → 原 msg_id，duplicate=true，不新增记录。"""
    raise NotImplementedError


@DRAFT
def test_n3_rejection_requires_reason(tmp_path):
    """§六 N3：拒接 must 带 reason；重复拒接幂等。"""
    raise NotImplementedError


@DRAFT
def test_n4_answer_without_report_id_rejected(tmp_path):
    """§六 N4：答复缺 report_id → 拒收，linked=false，不写 answered。"""
    raise NotImplementedError


@DRAFT
def test_n5_missing_trace_id_is_contract_violation(tmp_path):
    """§六 N5：缺 trace_id → status=unknown，禁止回填默认值冒充。"""
    raise NotImplementedError


@DRAFT
def test_n6_channel_mix_distinct_msg_id_same_report_id(tmp_path):
    """§六 N6：同 payload 双通道 → 两个 msg_id、同一 report_id。"""
    raise NotImplementedError


@DRAFT
def test_c7_no_side_effects(tmp_path):
    """§七：隔离树内 RouteOutbox.trading_permitted() 必须 False；不得触真投递/8795。"""
    raise NotImplementedError


def test_contract_doc_exists():
    """契约正文必须与骨架同批入库（防止只写测试不落契约）。"""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    doc = root / "docs" / "plans" / "2026-10-25_OSLINK-W4_司马懿到田丰归口契约v0.1_草案.md"
    assert doc.is_file(), "契约草案缺失：%s" % doc
