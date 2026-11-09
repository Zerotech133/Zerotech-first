import json, os, sys, hashlib, traceback
sys.path.insert(0, os.getcwd())
out = {"cwd": os.getcwd()}
try:
    from analysis import monitor as m
    out["module_file"] = os.path.abspath(m.__file__)
    out["module_sha256"] = hashlib.sha256(open(m.__file__, "rb").read()).hexdigest()
    def keys_of(obj):
        if isinstance(obj, dict): return sorted(obj.keys())
        return None
    out["summary_keys"] = keys_of(m.summary())
    lp = m.load_pools(); out["load_pools_type"] = type(lp).__name__; out["load_pools_keys"] = keys_of(lp) if isinstance(lp, dict) else None
    lpl = m.list_pools(); out["list_pools_type"] = type(lpl).__name__; out["list_pools"] = lpl if not isinstance(lpl, dict) else {k: (v if not isinstance(v, (list, dict)) else (len(v) if isinstance(v, list) else sorted(v.keys()))) for k, v in lpl.items()}
    al = m.list_alerts(limit=3); out["list_alerts_type"] = type(al).__name__
    if isinstance(al, list):
        out["list_alerts_len"] = len(al); out["alert_item_keys"] = keys_of(al[0]) if al else None
    else:
        out["list_alerts"] = al
    try:
        r = m.run_monitor(pool="watchlist", as_of="2026-11-05", dry_run=True)
        out["run_monitor_keys"] = keys_of(r); out["run_monitor_ok"] = r.get("ok") if isinstance(r, dict) else None
    except Exception as e:
        out["run_monitor_error"] = type(e).__name__ + ": " + str(e)
    try:
        res = m.evaluate(["600337.SH"], as_of="2026-11-05")
        out["evaluate_keys"] = keys_of(res) if isinstance(res, dict) else None
        if isinstance(res, dict) and res.get("alerts"):
            out["evaluate_alert_item_keys"] = keys_of(res["alerts"][0])
    except Exception as e:
        out["evaluate_error"] = type(e).__name__ + ": " + str(e)
except Exception as e:
    out["import_error"] = type(e).__name__ + ": " + str(e)
    out["trace"] = traceback.format_exc()[-800:]
print(json.dumps(out, ensure_ascii=False, indent=1))
