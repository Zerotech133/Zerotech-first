# -*- coding: utf-8 -*-
"""Independent verification for watch_admin_snapshot (M2/M3 read-only admin).

Run with the project Python, e.g.:
  python portal/verify_watch_admin.py

Checks required by the task:
  * import watch_admin_snapshot succeeds
  * WatchAdminAggregator().build() returns a dict
  * schema == "watch_admin.snapshot.v1"
  * all 7 zones (zone1..zone7) present
  * every MISSING source is tagged SOURCE_UNVERIFIED (no silent fabrication)
  * a single missing source must NOT raise (fail-visible)
"""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


def main() -> int:
    import watch_admin_snapshot as m  # noqa: E402
    from watch_admin_snapshot import WatchAdminAggregator, SCHEMA  # noqa: E402

    snap = WatchAdminAggregator().build()
    assert isinstance(snap, dict), "build() must return a dict"

    assert snap.get("schema") == SCHEMA, "schema must be %r, got %r" % (SCHEMA, snap.get("schema"))

    zones = snap.get("zones", {})
    required = ["zone1", "zone2", "zone3", "zone4", "zone5", "zone6", "zone7"]
    missing_zones = [z for z in required if z not in zones]
    assert not missing_zones, "missing zones: %s" % missing_zones

    # Every unverified source MUST be explicitly SOURCE_UNVERIFIED.
    unverified = []
    for zid, z in zones.items():
        for s in z.get("sources", []):
            if not s.get("verified"):
                assert s.get("source_quality") == "SOURCE_UNVERIFIED", (
                    "missing source %s/%s is not flagged SOURCE_UNVERIFIED" % (zid, s.get("name")))
                unverified.append("%s/%s:%s" % (zid, s.get("name"), s.get("error_code")))

    print("OK  schema            :", snap["schema"])
    print("OK  zones present     :", sorted(zones.keys()))
    print("OK  overall           :", snap.get("overall"))
    print("OK  replay            :", snap.get("replay"), snap.get("replay_at"))
    print("OK  SOURCE_UNVERIFIED : %d source(s)" % len(unverified))
    for u in unverified:
        print("      -", u)
    print("\nVERIFICATION PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
