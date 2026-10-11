# -*- coding: utf-8 -*-
"""K-STOCK-137 S-b · 经理实体（可雇佣/解聘/名册）

复刻 trainee.py 的账本模式，独立账本 manager_hires.json（与操盘手 hires.json 互不干扰）。
模型绑定硬约束：经理档案 model 字段必须在 gaorta 目录（models.json）内——fail-closed。
"""
import json
import os
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANAGERS_DIR = HERE / "managers"
HIRES_PATH = HERE / "manager_hires.json"


class Reject(Exception):
    """业务拒绝（雇佣/解聘语义错误），与 trainee.Reject 同款语义。"""


def load_profile(mid: str) -> dict:
    fp = MANAGERS_DIR / f"{mid}.json"
    if not fp.exists():
        raise Reject(f"未知经理: {mid}")
    prof = json.loads(fp.read_text(encoding="utf-8"))
    prof["hire_price_cny"] = _hire_price(prof)
    return prof


def _hire_price(prof: dict) -> int:
    """聘请价 = 档案基价 × 模型系数（系数来自 models.json；未知模型按 1.0 并标警）。"""
    coef = 1.0
    try:
        cat = json.loads((HERE / "models.json").read_text(encoding="utf-8"))
        for m in cat.get("models", []):
            if m["model"] == prof.get("model"):
                coef = m.get("coef_vs_glm52", 1.0)
                break
        else:
            prof = dict(prof, _model_warning="模型不在 gaorta 目录")
    except Exception:
        pass
    return round(prof.get("hire_base_price_cny", 99) * coef)


def list_profiles() -> list[dict]:
    out = []
    for fp in sorted(MANAGERS_DIR.glob("*.json")):
        try:
            out.append(load_profile(fp.stem))
        except Exception:
            continue
    return out


def _load_hires() -> dict:
    try:
        d = json.loads(HIRES_PATH.read_text(encoding="utf-8"))
        if isinstance(d, dict) and isinstance(d.get("hires"), list):
            return d
    except Exception:
        pass
    return {"hires": []}


def _save_hires(d: dict) -> None:
    tmp = HIRES_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(HIRES_PATH))


def hire(mid: str, source: str, note: str = "") -> dict:
    """雇佣经理到某组合源。模型必须在 gaorta 目录内。"""
    from gaorta_client import allowed_models
    prof = load_profile(mid)
    if prof.get("status") != "live":
        raise Reject(f"{mid} 状态 {prof.get('status')} ≠ live，不可雇佣")
    if prof.get("model") not in allowed_models():
        raise Reject(f"{mid} 绑定模型 {prof.get('model')!r} 不在 gaorta 目录，雇佣被拒")
    d = _load_hires()
    for h in d["hires"]:
        if h.get("manager") == mid and h.get("source") == source and h.get("status") == "active":
            raise Reject(f"{mid} 已被 {source} 雇佣（先 fire 再操作）")
    rec = {"manager": mid, "manager_name": prof.get("name"), "model": prof.get("model"),
           "source": source, "status": "active",
           "hire_price_cny": prof["hire_price_cny"],
           "hired_at": datetime.now().isoformat(timespec="seconds"), "note": note}
    d["hires"].append(rec)
    _save_hires(d)
    return rec


def fire(mid: str, source: str) -> dict:
    d = _load_hires()
    for h in d["hires"]:
        if h.get("manager") == mid and h.get("source") == source and h.get("status") == "active":
            h["status"] = "ended"
            h["fired_at"] = datetime.now().isoformat(timespec="seconds")
            _save_hires(d)
            return h
    raise Reject(f"无 active 雇佣: {mid}×{source}")


def hires_list() -> list[dict]:
    profs = {p["id"]: p for p in list_profiles()}
    return [{**h, "style": profs.get(h.get("manager"), {}).get("style")}
            for h in _load_hires().get("hires", [])]


def _main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="manager", description="投资部经理管线")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="经理名册").set_defaults(fn=lambda a: list_profiles())
    sub.add_parser("hires", help="雇佣注册表").set_defaults(fn=lambda a: hires_list())
    p = sub.add_parser("hire")
    p.add_argument("mid"); p.add_argument("source"); p.add_argument("--note", default="")
    p.set_defaults(fn=lambda a: [hire(a.mid, a.source, a.note)])
    p = sub.add_parser("fire")
    p.add_argument("mid"); p.add_argument("source")
    p.set_defaults(fn=lambda a: [fire(a.mid, a.source)])
    args = ap.parse_args(argv)
    try:
        print(json.dumps(args.fn(args), ensure_ascii=False, indent=1))
        return 0
    except Reject as e:
        print("REJECT:", e)
        return 2


if __name__ == "__main__":
    raise SystemExit(_main())
