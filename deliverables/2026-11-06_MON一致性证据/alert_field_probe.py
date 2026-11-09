import json, os, sys
sys.path.insert(0, os.getcwd())
from analysis import monitor as m
def provider(spec):
    return {
        "daily": [],
        "basic": [],
        "runs": [],
        "events": [
            {"code": "600337.SH", "date": spec["as_of"], "title": "业绩预警", "category": "风险", "category_final": "业绩风险"},
            {"code": "600337.SH", "date": "2026-11-04", "title": "股东减持", "category": "", "category_final": ""},
            {"code": "600337.SH", "date": "2026-11-06", "title": "未来事件不应入窗", "category": "", "category_final": ""},
        ],
        "market_latest": "2026-11-06",
    }
alerts = m.evaluate(["600337.SH"], as_of="2026-11-05", rules=["new_event"], data_provider=provider)
out = {"count": len(alerts), "items": []}
for a in alerts:
    out["items"].append({k: a.get(k) for k in ("code","rule","severity","title","detail","trade_date","dedupe_key")})
out["keys"] = sorted(alerts[0].keys()) if alerts else None
print(json.dumps(out, ensure_ascii=False, indent=1))
