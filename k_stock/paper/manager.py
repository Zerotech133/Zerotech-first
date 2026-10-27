# -*- coding: utf-8 -*-
"""K-STOCK-137 S-b · 经理实体（可雇佣/解聘/名册）

复刻 trainee.py 的账本模式，独立账本 manager_hires.json（与操盘手 hires.json 互不干扰）。
模型绑定硬约束：经理档案 model 字段必须在 gaorta 目录（models.json）内——fail-closed。
"""

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
import logging

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

MANAGERS_DIR = HERE / "managers"
HIRES_PATH = HERE / "manager_hires.json"

_MID_RE = re.compile(r"^[a-z0-9_]{1,32}$")
_SOURCE_RE = re.compile(r"^[A-Za-z0-9_\u4e00-\u9fff-]{1,32}$")


class Reject(Exception):
    """业务拒绝（雇佣/解聘语义错误），与 trainee.Reject 同款语义。"""


def _check_mid(mid: str) -> None:
    if not isinstance(mid, str) or not _MID_RE.match(mid):
        raise Reject(f"非法经理ID: {mid!r}（限 1-32 位小写字母/数字/下划线）")


def load_profile(mid: str) -> dict:
    _check_mid(mid)
    fp = MANAGERS_DIR / f"{mid}.json"
    if fp.parent != MANAGERS_DIR:
        raise Reject(f"非法经理ID: {mid!r}")
    if not fp.exists():
        raise Reject(f"未知经理: {mid}")
    try:
        prof = json.loads(fp.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise Reject(f"经理档案损坏（{fp.name} JSON 解析失败）: {e}")
    prof["hire_price_cny"] = _hire_price(prof)
    return prof


def _hire_price(prof: dict) -> int:
    """聘请价 = 档案基价 × 模型系数（系数来自 models.json；fail-closed，不静默降级）。"""
    try:
        cat = json.loads((HERE / "models.json").read_text(encoding="utf-8"))
    except Exception as e:
        raise Reject(f"gaorta 模型目录（models.json）不可读: {e}")
    model = prof.get("model")
    for m in cat.get("models", []):
        if m["model"] == model:
            return round(
                prof.get("hire_base_price_cny", 99) * m.get("coef_vs_glm52", 1.0)
            )
    raise Reject(f"模型 {model!r} 不在 gaorta 目录，计价失败")


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
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "k_stock/paper/manager.py", exc)
    return {"hires": []}


def _save_hires(d: dict) -> None:
    import secrets

    tmp = HIRES_PATH.with_suffix(f".json.tmp.{os.getpid()}.{secrets.token_hex(2)}")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(HIRES_PATH))


def _check_source(source: str) -> None:
    if not isinstance(source, str) or not _SOURCE_RE.match(source):
        raise Reject(
            f"非法组合源: {source!r}（限 1-32 位字母/数字/下划线/中文/连字符）"
        )


def _check_note(note: str) -> str:
    if not isinstance(note, str):
        raise Reject("非法备注: 必须为字符串")
    cleaned = "".join(ch for ch in note if ch.isprintable())
    if len(cleaned) > 200:
        raise Reject("备注过长（限 200 字符）")
    return cleaned


def hire(mid: str, source: str, note: str = "") -> dict:
    """雇佣经理到某组合源。模型必须在 gaorta 目录内。"""
    from gaorta_client import allowed_models

    _check_mid(mid)
    _check_source(source)
    note = _check_note(note)
    prof = load_profile(mid)
    if prof.get("status") != "live":
        raise Reject(f"{mid} 状态 {prof.get('status')} ≠ live，不可雇佣")
    if prof.get("model") not in allowed_models():
        raise Reject(f"{mid} 绑定模型 {prof.get('model')!r} 不在 gaorta 目录，雇佣被拒")
    d = _load_hires()
    for h in d["hires"]:
        if (
            h.get("manager") == mid
            and h.get("source") == source
            and h.get("status") == "active"
        ):
            raise Reject(f"{mid} 已被 {source} 雇佣（先 fire 再操作）")
    rec = {
        "manager": mid,
        "manager_name": prof.get("name"),
        "model": prof.get("model"),
        "source": source,
        "status": "active",
        "hire_price_cny": prof["hire_price_cny"],
        "hired_at": datetime.now().isoformat(timespec="seconds"),
        "note": note,
    }
    d["hires"].append(rec)
    _save_hires(d)
    return rec


def fire(mid: str, source: str) -> dict:
    _check_mid(mid)
    _check_source(source)
    d = _load_hires()
    for h in d["hires"]:
        if (
            h.get("manager") == mid
            and h.get("source") == source
            and h.get("status") == "active"
        ):
            h["status"] = "ended"
            h["fired_at"] = datetime.now().isoformat(timespec="seconds")
            _save_hires(d)
            return h
    raise Reject(f"无 active 雇佣: {mid}×{source}")


def hires_list() -> list[dict]:
    """注册表 + 档案名册合并视图（悬空审计：manager_status 取档案侧状态，
    档案文件缺失标 missing / 存在但不可读标 corrupted）。"""
    profs = {p["id"]: p for p in list_profiles()}
    out = []
    for h in _load_hires().get("hires", []):
        p = profs.get(h.get("manager"), {})
        if p:
            mstatus = p.get("status")
        elif (MANAGERS_DIR / f"{h.get('manager')}.json").exists():
            mstatus = "corrupted"
        else:
            mstatus = "missing"
        out.append({**h, "style": p.get("style"), "manager_status": mstatus})
    return out


def _main(argv=None):
    import argparse

    ap = argparse.ArgumentParser(prog="manager", description="投资部经理管线")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("list", help="经理名册")
    p.add_argument(
        "--json", action="store_true", help="单行 JSON 输出（portal 子进程用）"
    )
    p.set_defaults(fn=lambda a: list_profiles(), json_key="profiles")
    p = sub.add_parser("hires", help="雇佣注册表")
    p.add_argument(
        "--json", action="store_true", help="单行 JSON 输出（portal 子进程用）"
    )
    p.set_defaults(fn=lambda a: hires_list(), json_key="hires")
    p = sub.add_parser("hire")
    p.add_argument("mid")
    p.add_argument("source")
    p.add_argument("--note", default="")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=lambda a: [hire(a.mid, a.source, a.note)], cmd_name="hire")
    p = sub.add_parser("fire")
    p.add_argument("mid")
    p.add_argument("source")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=lambda a: [fire(a.mid, a.source)], cmd_name="fire")
    args = ap.parse_args(argv)
    try:
        out = args.fn(args)
        # 单行 JSON 仅限设置了 json_key 的子命令（list/hires）；hire/fire 维持 137c 原状
        if getattr(args, "json", False) and getattr(args, "json_key", None):
            print(json.dumps({"ok": True, args.json_key: out}, ensure_ascii=False))
            return 0
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0
    except Reject as e:
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
            return 2
        print(f"❌ {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(_main())
