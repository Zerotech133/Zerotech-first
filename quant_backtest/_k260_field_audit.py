# -*- coding: utf-8 -*-
"""K260 专项整理·字段自洽性审计（只读）：175 条重算变更的 reason/conf 残留检查。"""
import io
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
EVENT_F = os.path.join(HERE, "data", "semantica", "events.jsonl")
BAK_F = EVENT_F + ".bak-20261008-retag"


def load(path):
    with io.open(path, encoding="utf-8") as fh:
        return [json.loads(ln) for ln in fh if ln.strip()]


def main():
    cur = {e["id"]: e for e in load(EVENT_F)}
    old = {e["id"]: e for e in load(BAK_F)}
    changed = [k for k in old if k in cur
               and (cur[k].get("tag"), cur[k].get("bias"))
               != (old[k].get("tag"), old[k].get("bias"))]
    print("变更条目: %d" % len(changed))

    bad_reason = bad_conf = bad_both = 0
    samples = []
    for k in changed:
        e = cur[k]
        tag_none = e.get("tag") is None
        r_has = "规则命中" in (e.get("reason") or "")
        c07 = e.get("conf") == 0.7
        # 不自洽：tag 已 None 但 reason 仍带旧判据 / conf 仍 0.7
        if tag_none and r_has:
            bad_reason += 1
            if len(samples) < 3:
                samples.append((e["id"], e.get("tag"), e.get("reason"),
                                e.get("conf")))
        if tag_none and c07:
            bad_conf += 1
        if tag_none and r_has and c07:
            bad_both += 1
    print("tag→None 但 reason 残留「规则命中」: %d" % bad_reason)
    print("tag→None 但 conf 仍 0.7: %d" % bad_conf)
    print("两者都残留: %d" % bad_both)
    for s in samples:
        print("  样本 %s | tag=%s | reason=%r | conf=%r" % s)

    # 对照：全库规则命中形态（14 类 tag 命中总量，非仅三错类；
    # 三错类残留真值=119，其余为 bull 段等正常命中）
    n_hit = sum(1 for k in cur
                if (cur[k].get("reason") or "").startswith("规则命中"))
    print("\n全库 reason 以「规则命中」开头: %d 条（14 类 tag 全量命中，含 bull 段正常值）" % n_hit)


if __name__ == "__main__":
    main()
