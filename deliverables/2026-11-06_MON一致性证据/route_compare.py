import json
import subprocess

MON = "task/K-STOCK-ANALYSIS-MON-20261104"


def load(rev):
    raw = subprocess.check_output(["git", "show", rev + ":tools/portal_route_baseline.json"])
    d = json.loads(raw.decode("utf-8-sig"))
    routes = d.get("routes") or d.get("items") or []
    return {
        "total": len(routes),
        "generated_at": d.get("generated_at") or d.get("generated"),
        "top_keys": sorted(d.keys()),
    }


for rev in ["HEAD", MON]:
    try:
        print(rev, json.dumps(load(rev), ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001
        print(rev, "ERR", exc)
