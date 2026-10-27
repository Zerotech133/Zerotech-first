# -*- coding: utf-8 -*-
"""中文名迁移（WO-LEGION-S43）——「重名 4 对」的消歧通道。

背景：历史上有 4 对席位共用同一个中文名（司马懿/满宠/陆逊/马良），账面混淆。
名字散落在三处，**只改一处就会出现「hr 名册叫甲、指挥台叫乙」的分裂**：

  1. `hr/<ID>/resume.yaml` 的 `cn_name:`（名册主源，org_audit / _cn_of_id 读这里）
  2. `org.json` 档案的 `persona.name`（个别席位有，如 QS1）
  3. `registry/team_*.json` 的 `agents[].name`（个别席位有，如 MANCHONG1）

设计红线（与其他编制工具一致）：
  - 默认 dry-run；真写需工单 + 放行人
  - 新名不得与**其他席位**已占用的名字冲突（本席位自己的旧名不算冲突）
  - 先备份后写，逐文件原子替换；写后自检「目标已改名、别的席位一个没动、
    JSON 其他字段一字未变」，失败整体回滚
  - **去名**（new_cn 为空）是合法操作：纯工程组件（如记忆桥）本就不该占三国人名，
    此时 `cn_name: ""`，展示层回落 id，不伪造一个人名

不改 agent_id：id 牵着记忆 namespace、日报链、工单引用，改名不该动这些。
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from pathlib import Path

RULES_VERSION = "1.0"
_TICKET_RE = re.compile(r"^(WO-|K-|LEGION-|WOLEGION-)", re.I)
_CN_LINE = re.compile(r"^(\s*)cn_name:\s*(.*?)(\r?)$")


class RenameError(ValueError):
    """改名校验失败——一律 fail-visible。"""


def _registry(root: Path) -> Path:
    return root / "registry"


def _unique_backup(bdir: Path, tag: str) -> Path:
    bdir.mkdir(parents=True, exist_ok=True)
    base = "rename-%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), tag)
    p = bdir / base
    n = 1
    while p.exists():
        p = bdir / ("%s-%d" % (base, n))
        n += 1
    return p


def _read_cn_from_resume(path: Path) -> str:
    try:
        txt = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    for ln in txt.split("\n"):
        m = _CN_LINE.match(ln)
        if m:
            return m.group(2).strip().strip("'\"")
    return ""


def _roster(root: Path) -> dict:
    """{中文名: [持有者 id]}——三处取并集，用于冲突检测。"""
    out: dict[str, list[str]] = {}

    def add(cn, aid):
        cn = (cn or "").strip()
        if not cn or not aid:
            return
        out.setdefault(cn, [])
        if aid not in out[cn]:
            out[cn].append(aid)

    hr = root / "hr"
    if hr.exists():
        for d in sorted(hr.iterdir()):
            f = d / "resume.yaml"
            if f.is_file():
                add(_read_cn_from_resume(f), d.name)
    org_p = _registry(root) / "org.json"
    try:
        org = json.loads(org_p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        org = {}
    for d in org.get("departments") or []:
        for p in d.get("agent_profiles") or []:
            add(
                (p.get("persona") or {}).get("name") or p.get("cn_name"),
                p.get("agent_id"),
            )
    chief = org.get("chief") or {}
    if isinstance(chief, dict):
        add(chief.get("name"), chief.get("agent_id"))
    # ⚠️ **不扫 registry/team_*.json 的 name**：那里的 `name` 历史上被填成岗位名
    # （ INTEL0 = 「军情司上行受理席」、还有「PENDING（待人工授名）」这类占位），
    # 把它当人名会污染名册、并让「改人名」误伤岗位名。人名权威只有 hr cn_name 与
    # org persona.name 两处。
    return out


def _known_ids(root: Path) -> set:
    ids = set()
    hr = root / "hr"
    if hr.exists():
        ids.update(d.name for d in hr.iterdir() if d.is_dir())
    try:
        org = json.loads((_registry(root) / "org.json").read_text(encoding="utf-8"))
    except (ValueError, OSError):
        org = {}
    for d in org.get("departments") or []:
        ids.update(str(a) for a in (d.get("agents") or []))
        ids.update(str(p.get("agent_id")) for p in (d.get("agent_profiles") or []))
    return ids


def build_rename_plan(
    root: Path,
    *,
    agent_id: str,
    new_cn: str,
    ticket: str = "",
    signed_by: str = "",
    reason: str = "",
    also_team: bool = False,
) -> dict:
    """计划。`cur` 为空时是**授名**（原本三处都没有人名），不是改名。

    `also_team`：团队文件里那个 name 若确实是人名（只是写错了/是旧名），
    需要显式要求才覆盖——默认不动，因为 team.name 多数被填成了岗位名。
    """
    aid = (agent_id or "").strip()
    if not aid:
        raise RenameError("缺少 --id")
    if aid not in _known_ids(root):
        raise RenameError("未知席位 %r：hr 名册与组织真源都查不到" % aid)
    cn = (new_cn or "").strip()
    if cn and len(cn) > 8:
        raise RenameError("中文名过长（%d 字）：编制里的名字都是 2-4 字" % len(cn))

    roster = _roster(root)
    cur = next((k for k, ids in roster.items() if aid in ids), "")
    if cn and roster.get(cn):
        others = [x for x in roster[cn] if x != aid]
        if others:
            raise RenameError(
                "中文名 %r 已被 %s 占用（重名消歧不能造出新重名）"
                % (cn, ", ".join(others))
            )
    if cn == cur:
        raise RenameError("%s 的中文名已是 %r，无需改名" % (aid, cn))
    if not (ticket or "").strip() or not _TICKET_RE.match(ticket.strip()):
        raise RenameError("工单号必填且须 WO-/K-/LEGION- 前缀（--ticket）")
    if not (signed_by or "").strip():
        raise RenameError("缺少放行人（--signed-by）")

    targets = []
    hr_f = root / "hr" / aid / "resume.yaml"
    if hr_f.exists():
        targets.append(
            {
                "file": str(hr_f.relative_to(root)),
                "where": "hr.resume.cn_name",
                "from": _read_cn_from_resume(hr_f),
                "to": cn,
            }
        )
    org_p = _registry(root) / "org.json"
    try:
        org_txt = org_p.read_text(encoding="utf-8")
        org = json.loads(org_txt)
    except (ValueError, OSError):
        org = {}
    for d in org.get("departments") or []:
        for p in d.get("agent_profiles") or []:
            if str(p.get("agent_id")) != aid:
                continue
            pn = (p.get("persona") or {}).get("name")
            if pn:
                targets.append(
                    {
                        "file": "registry/org.json",
                        "where": "dept:%s.persona.name" % d.get("dept"),
                        "from": pn,
                        "to": cn,
                    }
                )
            if p.get("cn_name"):
                targets.append(
                    {
                        "file": "registry/org.json",
                        "where": "dept:%s.cn_name" % d.get("dept"),
                        "from": p.get("cn_name"),
                        "to": cn,
                    }
                )
            if not pn and not p.get("cn_name"):
                # 授名：档案里一个字都没有，需要**新增** cn_name，否则名字写不进去
                targets.append(
                    {
                        "file": "registry/org.json",
                        "where": "dept:%s.cn_name(新增)" % d.get("dept"),
                        "from": "",
                        "to": cn,
                    }
                )
    reg = _registry(root)
    if reg.exists():
        for f in sorted(reg.glob("team_*.json")):
            try:
                t = json.loads(f.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            for a in t.get("agents") or []:
                # 只有「该 name 确实是人名（等于当前人名）」才同步；
                # 岗位名（如「军情司上行受理席」）不因改人名被覆盖。
                # 授名（cur 为空）时必须显式 also_team 才动——无法自动判断是人名还是岗位名。
                nm = str(a.get("name") or "")
                if str(a.get("agent_id")) != aid or not nm:
                    continue
                if (cur and nm == cur) or (also_team and not cur):
                    targets.append(
                        {
                            "file": "registry/%s" % f.name,
                            "where": "agents[].name",
                            "from": nm,
                            "to": cn,
                        }
                    )
    if not targets:
        raise RenameError("%s 在 hr/org/team 三处都没有中文名记录，无从改名" % aid)

    return {
        "rules_version": RULES_VERSION,
        "mode": "rename_cn",
        "agent_id": aid,
        "from_cn": cur,
        "to_cn": cn,
        "action": "clear" if not cn else ("rename" if cur else "name"),
        "also_team": bool(also_team),
        "targets": targets,
        "ticket": ticket.strip(),
        "signed_by": signed_by.strip(),
        "reason": (reason or "").strip(),
        "note": "dry-run 改名计划：未写入任何字节",
    }


def _apply_org(root: Path, aid: str, new_cn: str) -> None:
    p = _registry(root) / "org.json"
    org = json.loads(p.read_text(encoding="utf-8"))
    for d in org.get("departments") or []:
        for prof in d.get("agent_profiles") or []:
            if str(prof.get("agent_id")) != aid:
                continue
            wrote = False
            if isinstance(prof.get("persona"), dict) and prof["persona"].get("name"):
                prof["persona"]["name"] = new_cn
                wrote = True
            if prof.get("cn_name"):
                prof["cn_name"] = new_cn
                wrote = True
            if not wrote:
                # 授名：档案里原本一个字都没有，新增 cn_name（否则名字无处落地）
                prof["cn_name"] = new_cn
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(org, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def _apply_team(
    root: Path, aid: str, new_cn: str, old_cn: str = "", force: bool = False
) -> None:
    for f in sorted(_registry(root).glob("team_*.json")):
        try:
            t = json.loads(f.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        hit = False
        for a in t.get("agents") or []:
            if str(a.get("agent_id")) != aid or not a.get("name"):
                continue
            nm = str(a.get("name"))
            # 改名：只动「等于旧人名」的；授名（old_cn 空）：只有 force 才动，
            # 因为无法自动区分那是人名还是岗位名。
            if (old_cn and nm == old_cn) or (not old_cn and force):
                a["name"] = new_cn
                hit = True
        if hit:
            tmp = f.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(t, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            tmp.replace(f)


def _apply_hr(root: Path, aid: str, new_cn: str) -> None:
    f = root / "hr" / aid / "resume.yaml"
    if not f.exists():
        return
    raw = f.read_text(encoding="utf-8")
    lines = raw.split("\n")
    done = False
    for i, ln in enumerate(lines):
        m = _CN_LINE.match(ln)
        if m and not done:
            indent, _, cr = m.group(1), m.group(2), m.group(3)
            lines[i] = '%scn_name: "%s"%s' % (indent, new_cn, cr)
            done = True
    if not done:
        # 原本没有 cn_name 行：追加在文件末尾，保持可见
        tail = "" if raw.endswith("\n") else "\n"
        raw = raw + tail + 'cn_name: "%s"\n' % new_cn
        f.write_text(raw, encoding="utf-8")
        return
    f.write_text("\n".join(lines), encoding="utf-8")


def apply_rename_plan(root: Path, plan: dict, *, signed_by: str) -> dict:
    if (signed_by or "").strip() != plan.get("signed_by"):
        raise RenameError(
            "放行人与计划不一致：plan=%r apply=%r" % (plan.get("signed_by"), signed_by)
        )
    aid, new_cn = plan["agent_id"], plan["to_cn"]

    roster_before = _roster(root)  # 改名前的名册快照
    bdir = _registry(root) / "backups"
    # 显式映射 rel → 备份路径。**不能靠文件名前缀去猜**：两个同前缀文件会串档，
    # 回滚时可能把 A 的备份写进 B。
    backups: dict[str, str] = {}
    files = sorted({t["file"] for t in plan["targets"]})
    for rel in files:
        src = root / rel
        if not src.exists():
            continue
        dst = _unique_backup(bdir, Path(rel).stem[:24])
        shutil.copy2(src, dst)
        backups[rel] = str(dst)

    try:
        _apply_hr(root, aid, new_cn)
        _apply_org(root, aid, new_cn)
        _apply_team(
            root, aid, new_cn, plan["from_cn"], force=bool(plan.get("also_team"))
        )
        _selfcheck(root, aid, new_cn, roster_before)
    except RenameError:
        for rel, bak in backups.items():
            shutil.copy2(bak, root / rel)
        raise

    return {
        "rules_version": RULES_VERSION,
        "ok": True,
        "mode": "rename_cn",
        "agent_id": aid,
        "from_cn": plan["from_cn"],
        "to_cn": new_cn,
        "backups": sorted(backups.values()),
        "note": "中文名已同步 hr/org/team 三处并自检通过；回滚见 backups",
    }


def _selfcheck(root: Path, aid: str, new_cn: str, roster_before: dict) -> None:
    def names_of(roster: dict, x: str) -> set:
        """一个 id 在名册里占用的**全部**名字。

        必须按集合比：旧实现用 `next(第一个含该 id 的 key)`，当一个 id 同时挂在
        「hr 真名」与「team 岗位名」下时会取错，把没动过的席位报成被改动
        （S43 实测：INTEL0 被误报 '军情司上行受理席' → '是仪'）。
        """
        return {k for k, v in roster.items() if x in v}

    roster_after = _roster(root)
    got = names_of(roster_after, aid)
    if new_cn:
        if got != {new_cn}:
            raise RenameError(
                "自检失败：%s 的中文名未变为 %r（当前 %r）" % (aid, new_cn, sorted(got))
            )
    elif got:
        raise RenameError("自检失败：%s 应已去名，实际仍为 %r" % (aid, sorted(got)))
    # 其他席位一个名字都不许动
    for other in {x for ids in roster_before.values() for x in ids}:
        if other == aid:
            continue
        if names_of(roster_after, other) != names_of(roster_before, other):
            raise RenameError(
                "自检失败：非目标席位 %s 的名字被改动（%r → %r）"
                % (
                    other,
                    sorted(names_of(roster_before, other)),
                    sorted(names_of(roster_after, other)),
                )
            )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="中文名迁移（默认 dry-run）")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    ap.add_argument("--id", dest="agent_id", required=False)
    ap.add_argument(
        "--name",
        dest="new_cn",
        default="",
        help="新中文名；留空表示去名（纯工程组件不占人名）",
    )
    ap.add_argument("--clear", action="store_true", help="显式去名（等同 --name 空）")
    ap.add_argument(
        "--also-team",
        action="store_true",
        help="授名时同时覆盖 team 文件里的 name（该 name 确是人名、只是写错或旧名时用）",
    )
    ap.add_argument("--ticket", default="")
    ap.add_argument("--signed-by", default="")
    ap.add_argument("--reason", default="")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--roster", action="store_true", help="只打印当前名册与重名")
    args = ap.parse_args(argv)
    root = Path(args.root)
    try:
        if args.roster:
            roster = _roster(root)
            dups = {k: v for k, v in roster.items() if len(v) > 1}
            print(
                json.dumps(
                    {"names": len(roster), "duplicates": dups},
                    ensure_ascii=False,
                    indent=1,
                )
            )
            return 0
        if not args.agent_id:
            print("缺少 --id", file=sys.stderr)
            return 2
        plan = build_rename_plan(
            root,
            agent_id=args.agent_id,
            new_cn="" if args.clear else args.new_cn,
            ticket=args.ticket,
            signed_by=args.signed_by,
            reason=args.reason,
            also_team=args.also_team,
        )
        if not args.apply:
            print(json.dumps(plan, ensure_ascii=False, indent=1))
            print(
                "\n【dry-run】以上为改名计划，未写入任何字节。确认无误加 --apply 执行。",
                file=sys.stderr,
            )
            return 0
        print(
            json.dumps(
                apply_rename_plan(root, plan, signed_by=args.signed_by),
                ensure_ascii=False,
                indent=1,
            )
        )
        return 0
    except RenameError as e:
        print("改名校验失败：%s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
