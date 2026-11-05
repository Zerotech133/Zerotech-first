import json, subprocess
def info(rev):
    raw = subprocess.check_output(["git","show",rev+":tools/portal_route_baseline.json"])
    d = json.loads(raw.decode("utf-8-sig"))
    sigs = d.get("signatures")
    return {"total_field": d.get("total"), "signatures_len": len(sigs) if isinstance(sigs, (dict, list)) else None, "generated_at": d.get("generated_at"), "source": d.get("source")}
for rev in ["HEAD", "task/K-STOCK-ANALYSIS-MON-20261104"]:
    print(rev, json.dumps(info(rev), ensure_ascii=False))
