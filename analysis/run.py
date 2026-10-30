# -*- coding: utf-8 -*-
"""一键 CLI：S1-S6 + 质量门 + 归档。

    python -m analysis.run --code 600337.SH
    python -m analysis.run --code 688023.SH --no-llm
    python -m analysis.run --code 605258.SH --skip-events --no-llm
"""
import argparse
import json
import os
import sys
import time

from . import __version__, config, db, events as events_mod, extras as extras_mod, features as feat_mod
from . import gates, publish, render, snapshot, verdict as verdict_mod, views as views_mod
import logging


def _log(msg, logf):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line)
    try:
        logf.write(line + "\n")
        logf.flush()
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "analysis/run.py", exc)


def _analysis_json(ctx, artifacts=None):
    snap, feat, vd, ev = ctx["snapshot"], ctx["features"], ctx["verdict"], ctx["events"]
    sources = []
    for dom, d in snap["domains"].items():
        if d.get("rows"):
            sources.append({"source_id": "dom:" + dom, "type": "tushare" if dom != "news" else "news",
                            "title": "%s（%s）" % (dom, d.get("source", "")), "as_of": d.get("as_of") or snap["as_of"]})
    for e in ev[:30]:
        sources.append({"source_id": "ev:" + e["event_id"], "type": "announcement", "title": e["title"],
                        "url": e.get("url", ""), "as_of": e["date"]})
    checks = {k: v for k, v in (ctx.get("gates") or {}).items()}
    return {
        "meta": ctx["meta"], "snapshot": {"domains": snap["domains"], "coverage_pct": snap.get("coverage"),
                                          "required_missing": snap.get("required_missing")},
        "events": [{k: e.get(k) for k in ("event_id", "date", "category", "title", "url", "summary", "confidence", "reviewed")} | {"source_id": "ev:" + e["event_id"]} for e in ev[:60]],
        "features": feat, "views": ctx.get("views") or {"channels": [], "issues": ["未运行"]},
        "verdict": {k: vd.get(k) for k in ("rating", "confidence", "horizon", "thesis", "key_signals", "triggers", "score", "score_v2", "score_detail", "nonrecurring",
                                          "invalidations", "position_note", "disclaimer")},
        "scenarios": vd.get("scenarios"), "levels": vd.get("levels"), "risks": [{"title": r, "detail": r} for r in vd.get("risks", [])],
        "watch": vd.get("watch"), "sources": sources, "checks": checks,
        "artifacts": artifacts or {},
    }


def run(code, as_of=None, no_llm=False, with_legion=False, skip_events=False, max_pdf=10, push=False):
    os.makedirs(config.WORK, exist_ok=True)
    log_path = os.path.join(config.WORK, "logs")
    os.makedirs(log_path, exist_ok=True)
    logf = open(os.path.join(log_path, "%s-%s.log" % (code.replace(".", "_"), time.strftime("%Y%m%d-%H%M%S"))),
                "w", encoding="utf-8")
    t0 = time.time()
    _log("analysis v%s start code=%s as_of=%s" % (__version__, code, as_of or "auto"), logf)

    # P2.1 商业控制（默认 open 模式零影响）：license 校验 + 报告配额扣减 + 租户隔离目录
    tenant_ctx = None
    tenant_root = None
    commercial_i = None
    if (os.environ.get("COMMERCIAL_MODE") or "open").strip().lower() == "commercial":
        from commercial import integration as commercial_i
        try:
            tenant_ctx = commercial_i.ensure(metric="reports_month")
            tenant_root = commercial_i.deliver_root()
            _log("commercial tenant=%s plan=%s" % (tenant_ctx["tenant"], tenant_ctx["plan"]), logf)
        except Exception as e:
            _log("commercial blocked: %s" % e, logf)
            raise

    # 自动 as_of：该股票最新交易日
    if not as_of:
        import pandas as pd
        daily = pd.read_parquet(os.path.join(config.CURATED, "daily.parquet"), columns=["ts_code", "trade_date"])
        d = daily[daily["ts_code"] == code]["trade_date"].astype(str).max()
        as_of = "%s-%s-%s" % (d[:4], d[4:6], d[6:8])
    _log("S1 数据快照 ...", logf)
    snap = snapshot.load_snapshot(code, as_of)
    name = snap.get("name") or code
    run_id = db.new_run(code, name, as_of)
    _log("S1 done name=%s coverage=%.1f%% last_trade=%s" % (name, snap["coverage"], snap.get("last_trade_date")), logf)

    _log("S2 事件库 ...", logf)
    if skip_events:
        ev = events_mod.load(code, 80)
        ev_note = "skipped, 读库 %d 条" % len(ev)
    else:
        ev, ev_note = events_mod.build_events(code, as_of, name=name, max_pdf=max_pdf)
        # 重新读库：带出人工复核状态（reviewed/category_final），供报告与 H5 标注
        ev = events_mod.load(code, 200) or ev
    snap["events"] = ev
    _log("S2 done " + ev_note, logf)

    _log("S3 特征 ...", logf)
    feat = feat_mod.build_features(snap)
    try:
        feat["extras"] = extras_mod.build(snap)
    except Exception as e:
        feat["extras"] = {}
        _log("S3 extras 失败（不阻断）：" + str(e)[:120], logf)
    _log("S3 done price=%s ps=%s peers=%d extras=%d" % (feat["technical"].get("price"),
                                                        feat["valuation"].get("ps_ttm"),
                                                        len(feat.get("peers") or []),
                                                        sum(1 for v in (feat.get("extras") or {}).values() if v)), logf)

    views = None
    if not no_llm:
        _log("S4 AI 研判 ...", logf)
        views = views_mod.run_views(code, snap, ev, with_legion=with_legion)
        _log("S4 done channels=%d issues=%d" % (len(views.get("channels", [])), len(views.get("issues", []))), logf)

    _log("S5 结论 ...", logf)
    vd = verdict_mod.build_verdict(snap, feat, ev)
    _log("S5 done rating=%s score=%s" % (vd["rating"], vd.get("score")), logf)

    meta = {"code": code, "name": name, "as_of": as_of, "intraday": None, "run_id": run_id,
            "schema_version": "1.0", "generated_at": time.strftime("%F %T"), "trigger": "user",
            "versions": {"pipeline": __version__, "prompt": config.PROMPT_VERSION}}
    meta["tenant"] = tenant_ctx["tenant"] if tenant_ctx else None
    now = time.strftime("%F %T")
    ctx = {"meta": meta, "snapshot": snap, "features": feat, "events": ev, "verdict": vd, "views": views,
           "now": now, "intraday": None}

    g1 = gates.g1_data(snap)
    g2 = gates.g2_facts(snap, ev, vd)
    g3 = gates.g3_consistency(snap, vd, views, ev)
    ctx["gates"] = {"G1": g1, "G2": g2, "G3": g3}
    _log("G1-G3 %s" % {k: (v["passed"], v["score"]) for k, v in ctx["gates"].items()}, logf)

    _log("S6 渲染 ...", logf)
    outdir = os.path.join(config.WORK, "runs", run_id)
    arts = render.render_all(ctx, outdir)
    arts["rating"] = vd["rating"]
    g4 = gates.g4_delivery(arts, outdir)
    ctx["gates"]["G4"] = g4
    # analysis.json
    aj = _analysis_json(ctx, {k: os.path.basename(v) if isinstance(v, str) else v for k, v in arts.items()})
    aj_path = os.path.join(outdir, "analysis.json")
    open(aj_path, "w", encoding="utf-8").write(json.dumps(aj, ensure_ascii=False, indent=1, default=str))
    shutil = __import__("shutil")
    shutil.copy2(aj_path, os.path.join(outdir, "manifest.json")) if False else None

    dest = publish.archive(code, name, as_of, {"report": arts["report"], "h5": arts["h5"],
                                               "manifest": arts["manifest"], "charts": arts["charts"],
                                               "rating": vd["rating"]}, outdir, root=tenant_root)
    _log("归档 %s" % dest, logf)
    if tenant_ctx and commercial_i is not None:
        commercial_i.audit_report(run_id, dest, vd["rating"])
    pushed = {"ok": False}
    if push:
        pushed = publish.feishu_push(dest, code, name, vd["rating"], vd["thesis"])
        _log("飞书推送 %s" % pushed, logf)

    ok = all(ctx["gates"][k]["passed"] for k in ("G1", "G2", "G3", "G4"))
    db.finish_run(run_id, "ok" if ok else "warn", vd["rating"], snap["coverage"], dest,
                  error=None if ok else "gates failed", meta={"gates": ctx["gates"], "elapsed": round(time.time() - t0, 1)})
    _log("DONE status=%s elapsed=%.1fs out=%s" % ("ok" if ok else "warn", time.time() - t0, dest), logf)
    return {"run_id": run_id, "ok": ok, "rating": vd["rating"], "coverage": snap["coverage"],
            "outdir": outdir, "archive": dest, "gates": ctx["gates"], "elapsed": round(time.time() - t0, 1),
            "analysis_json": aj_path, "report": arts["report"], "h5": arts["h5"]}


def main():
    p = argparse.ArgumentParser(description="个股全维分析流水线")
    p.add_argument("--code", required=True, help="如 600337.SH")
    p.add_argument("--as-of", default=None)
    p.add_argument("--no-llm", action="store_true")
    p.add_argument("--with-legion", action="store_true")
    p.add_argument("--skip-events", action="store_true")
    p.add_argument("--max-pdf", type=int, default=10)
    p.add_argument("--push", action="store_true")
    a = p.parse_args()
    r = run(a.code, a.as_of, a.no_llm, a.with_legion, a.skip_events, a.max_pdf, a.push)
    print(json.dumps({k: r[k] for k in ("run_id", "ok", "rating", "coverage", "elapsed", "outdir")}, ensure_ascii=False))
    return 0 if r["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
