"""Tests for xq_time_bridge.ms_to_local_str (WO-QT-20261020-048).

Pure offline. Verifies:
1. Byte-for-byte format parity with the legacy reference implementation
   (datetime.fromtimestamp(ms/1000).strftime('%Y-%m-%d %H:%M:%S')) across
   multiple ms values, without hardcoding timezone-dependent literals.
2. Family validation contract: bool/float/str/negative/None -> ValueError.
3. Static: no reverse (str -> ms) parsing in the module.
4. Static: no consumer imports the module yet (write path must stay detached).
"""

import ast
from datetime import datetime
from pathlib import Path

import pytest

from xq_time_bridge import ms_to_local_str

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "xq_time_bridge.py"
THIS_TEST_PATH = Path(__file__).resolve()

# Fixed integer ms values: 0, whole-day boundaries, second boundaries with
# millisecond remainder (truncation cases like ...999), and large epochs.
MS_SAMPLES = [
    0,               # epoch origin
    1,               # 1 ms above origin (truncated)
    999,             # first-second truncation edge
    86_400_000,      # exactly one whole day
    86_400_000 * 100,  # whole-day boundary far out
    1_700_000_000_000,     # second boundary, zero ms
    1_700_000_000_001,     # +1 ms (truncated)
    1_700_000_000_999,     # +999 ms (truncation edge)
    1_798_765_432_999,     # large epoch with 999 ms
    2_147_483_647_999,     # legacy 32-bit epoch ceiling with ms remainder
]


def _reference(ms: int) -> str:
    """Legacy-chain reference implementation (xueqiu_common.py ms_to_dt core)."""
    return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M:%S")


class TestFormatParity:
    @pytest.mark.parametrize("ms", MS_SAMPLES)
    def test_matches_reference_implementation(self, ms):
        # Byte-for-byte parity with the legacy chain reference on any host tz.
        assert ms_to_local_str(ms) == _reference(ms)

    def test_format_shape_is_timezone_agnostic(self):
        out = ms_to_local_str(1_700_000_000_000)
        assert len(out) == 19
        assert out[4] == "-" and out[7] == "-"
        assert out[10] == " "  # space separator, not 'T'
        assert out[13] == ":" and out[16] == ":"


class TestValidationContract:
    @pytest.mark.parametrize(
        "bad",
        [True, False, 1.0, 0.0, "100", "", -1, -86_400_000, None],
    )
    def test_invalid_inputs_raise_value_error(self, bad):
        with pytest.raises(ValueError):
            ms_to_local_str(bad)

    @pytest.mark.parametrize("huge", [10**14, 2**60])
    def test_out_of_range_value_raises_value_error(self, huge):
        """F4 (WO-057): values beyond the platform datetime range surface
        as the documented ValueError, never a raw OSError/OverflowError."""
        with pytest.raises(ValueError):
            ms_to_local_str(huge)


class TestNoReverseParsing:
    def test_module_source_has_no_str_to_ms_parsing(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        for forbidden in ("strptime", "fromisoformat", "mktime"):
            assert forbidden not in source, f"reverse parsing found: {forbidden}"

    def test_module_ast_defines_single_public_function(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        top_funcs = [
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        assert top_funcs == ["ms_to_local_str"]


class TestNoConsumersYet:
    def test_no_module_imports_xq_time_bridge(self):
        offenders = []
        py_files = sorted(REPO_ROOT.rglob("*.py"))
        assert py_files, "expected python files under repo root"
        for path in py_files:
            if path.resolve() in (MODULE_PATH.resolve(), THIS_TEST_PATH.resolve()):
                continue
            # __pycache__ and caches hold no real sources; skip non-file noise.
            if "__pycache__" in path.parts or ".venv" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if "import xq_time_bridge" in text:
                offenders.append(str(path.relative_to(REPO_ROOT)))
        assert offenders == [], f"unexpected consumers: {offenders}"
