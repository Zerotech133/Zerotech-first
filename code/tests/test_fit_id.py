"""TDD: fit_id() — ID 长度适配工具。

先跑红（ids.py 未存在），实现后全绿。
"""
from __future__ import annotations

import hashlib


def _expected_hash(prefix: str, body: str) -> str:
    s = f"{prefix}{body}"
    return hashlib.md5(s.encode()).hexdigest()[:10]


# ── 正常路径 ─────────────────────────────────────────────────────────────────


class TestFitIdShortInput:
    """短输入（总长 ≤64）原样返回，不压缩。"""

    def test_exact_64(self):
        from zhice_os_v2.domain.ids import fit_id

        prefix = "pos-"
        body = "x" * 60  # 4+60=64
        result = fit_id(prefix, body)
        assert result == f"{prefix}{body}"
        assert len(result) == 64

    def test_under_64(self):
        from zhice_os_v2.domain.ids import fit_id

        result = fit_id("pos-", "short-body")
        assert result == "pos-short-body"

    def test_empty_body(self):
        from zhice_os_v2.domain.ids import fit_id

        result = fit_id("pos-", "")
        assert result == "pos-"


# ── 压缩路径 ─────────────────────────────────────────────────────────────────


class TestFitIdLongInput:
    """超长输入压缩到恰好 64 字节，格式 {prefix}{body[:keep]}-{md5[:10]}。"""

    def test_result_length_equals_64(self):
        from zhice_os_v2.domain.ids import fit_id

        body = "a" * 100
        result = fit_id("pos-", body)
        assert len(result) == 64

    def test_starts_with_prefix(self):
        from zhice_os_v2.domain.ids import fit_id

        result = fit_id("pos-", "b" * 100)
        assert result.startswith("pos-")

    def test_ends_with_md5_tail(self):
        from zhice_os_v2.domain.ids import fit_id

        body = "c" * 100
        result = fit_id("pos-", body)
        tail = result[-10:]
        expected_h = _expected_hash("pos-", body)
        assert tail == expected_h, f"末尾 hash 不匹配：{tail!r} != {expected_h!r}"

    def test_separator_before_hash(self):
        from zhice_os_v2.domain.ids import fit_id

        result = fit_id("pos-", "d" * 100)
        # 结尾 11 个字符: '-' + 10 位 hash
        assert result[-11] == "-"

    def test_uniqueness_different_bodies(self):
        from zhice_os_v2.domain.ids import fit_id

        body_a = "apr-plan-cand-v1-crypto-sv-3-SOL/USDT-202609020148-sim-1756789012"
        body_b = "apr-plan-cand-v1-crypto-sv-3-ETH/USDT-202609020148-sim-1756789012"
        r_a = fit_id("pos-", body_a)
        r_b = fit_id("pos-", body_b)
        assert r_a != r_b, "不同 body 必须产生不同压缩 id"

    def test_deterministic(self):
        from zhice_os_v2.domain.ids import fit_id

        body = "e" * 100
        assert fit_id("pos-", body) == fit_id("pos-", body)


# ── 关键回归：压缩后仍含 apr-plan-cand ────────────────────────────────────────


class TestFitIdPreservesApprovalSubstring:
    """sim 单 body 压缩后 position_id 仍包含 'apr-plan-cand'，
    验证精确过滤 'pos-apr-plan-cand-%-sim' 不误伤压缩后新格式（结尾是 hash，非 -sim）。"""

    SIM_BODY = "apr-plan-cand-v1-crypto-sv-3-SOL/USDT-202609020148-sim-1756789012"

    def test_compressed_id_contains_apr_plan_cand(self):
        from zhice_os_v2.domain.ids import fit_id

        result = fit_id("pos-", self.SIM_BODY)
        # 总长应 == 64（超长路径）
        assert len(result) == 64, f"压缩后长度 {len(result)} ≠ 64"
        # body[:keep] 需包含 'apr-plan-cand'（keep ≥ 13）
        assert "apr-plan-cand" in result, (
            f"压缩后 id 不含 'apr-plan-cand'，宽匹配过滤将失效: {result!r}"
        )

    def test_compressed_id_startswith_pos(self):
        from zhice_os_v2.domain.ids import fit_id

        result = fit_id("pos-", self.SIM_BODY)
        assert result.startswith("pos-")

    def test_precise_like_filter_does_not_match(self):
        """精确过滤 'pos-apr-plan-cand-%-sim' 不命中压缩后的新格式 id。

        压缩后结尾是 -{hash10}，不以 -sim 结尾，
        因此精确过滤不应将其排除（新格式正常 sim 持仓得以保留）。
        """
        from zhice_os_v2.domain.ids import fit_id

        result = fit_id("pos-", self.SIM_BODY)
        # Python 等价 LIKE 'pos-apr-plan-cand-%-sim'：
        #   startswith("pos-apr-plan-cand-") AND endswith("-sim")
        precise_match = result.startswith("pos-apr-plan-cand-") and result.endswith("-sim")
        assert not precise_match, (
            f"精确过滤意外命中了压缩后新格式 id: {result!r}"
        )
