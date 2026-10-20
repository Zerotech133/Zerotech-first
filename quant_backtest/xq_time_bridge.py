"""Cross-chain time bridge: Unix ms INTEGER -> local-time display string.

Read-only conversion for cross-chain display and reconciliation; the new
chain stores Unix ms INTEGER exclusively. This module intentionally provides
no reverse (str -> ms) parsing path: display/reconciliation code must never
feed human-readable strings back into the new-chain time domain.

Reference format parity: xueqiu_common.py ms_to_dt (legacy chain) emits
'%Y-%m-%d %H:%M:%S' in the local timezone; this module reproduces that
format byte-for-byte.
"""

from datetime import datetime

__all__ = ["ms_to_local_str"]

_FORMAT = "%Y-%m-%d %H:%M:%S"


def ms_to_local_str(value: int) -> str:
    """Convert Unix epoch milliseconds to 'YYYY-MM-DD HH:MM:SS' (local tz).

    Read-only conversion for cross-chain display and reconciliation; the
    new chain stores Unix ms INTEGER exclusively.

    Validation (family contract): only a genuine non-negative ``int`` is
    accepted. ``bool``, ``float``, ``str``, ``None`` and negative integers
    all raise ``ValueError``. Millisecond precision is truncated by the
    second-granularity output format, matching the legacy chain.

    Raises:
        ValueError: if ``value`` is not a non-negative ``int``, or if it
            is too large for the platform datetime range (out of
            representable range).
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(
            f"ms_to_local_str expects a non-negative int, got {type(value).__name__}"
        )
    if value < 0:
        raise ValueError(f"ms_to_local_str expects a non-negative int, got {value}")
    try:
        return datetime.fromtimestamp(value / 1000).strftime(_FORMAT)
    except (OSError, OverflowError) as error:
        raise ValueError(
            f"ms_to_local_str value out of representable range: {value}"
        ) from error
