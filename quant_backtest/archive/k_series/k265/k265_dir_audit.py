# -*- coding: utf-8 -*-
"""K265 方向一致性离线审计 —— 治"src/buyer 字段自洽但整体反向"漏网。

原理: qwen pv2 的 reason 通常带角色句式((X)标注 + 供应/采购动词),
      从 reason 反推 supplier 侧, 与 ext.src 字段比对; 推断冲突也报。
用法:
  python k265_dir_audit.py               # 扫影子 pv2 正向判定
  python k265_dir_audit.py --graph       # 回扫图内 concept_llm_v1 边(清洗用)
  python k265_dir_audit.py --sample 20   # FLAG 之外随机抽样自检
"""
import json
import random
import re
import sys

SHADOW = r"C:\项目\老田的AI助手\quant_backtest\data\semantica\k265_phase1_shadow.jsonl"

# 概念名 + (A|B) 角色标注 (全角/半角括号)
P_TAG = re.compile(r"([^，。；、（）()·\s]{2,14})[（(]\s*(?:概念)?\s*([AB])\s*[)）]")
SUP_VERB = r"(?:供应|供货|销售|提供|交付|输出|供货方|生产并销售)"
BUY_VERB = r"(?:采购|购买|买方|需求)"


def infer_sides(reason):
    """从 reason 反推 {side: 'supplier'|'buyer'}。返回 dict 或空(无可检测信号)。

    只留高置信句式（防"需要B提供"类误报）:
    R1 向/输送给 X(A) ... 供应|供货|销售|交付 → X=buyer(接收方)
    R2 X(A) ... 被 ... 采购|购买 → X=supplier
    R3 X(A) 作为 供应商|供应方|上游供应|核心供应 → X=supplier
    R4 销售给|卖给 X(A) → X=buyer
    R5 X(A) 作为 采购方|需求方|下游|买方|应用端 → X=buyer
    """
    out = {}
    clauses = re.split(r"[，。；]", reason)
    for cl in clauses:
        tags = P_TAG.findall(cl)
        if len(tags) >= 2:
            {side: name for name, side in tags}
            # R1: 向 X(A) ... 供应 → supplier = X 的对面
            m = re.search(r"[向给][^，]{0,16}" + P_TAG.pattern + r"[^，]{0,12}(供应|供货|销售|交付|输送)", cl)
            if m:
                recv = m.group(2)
                out["B" if recv == "A" else "A"] = "supplier"
            # R2: X(A) 被 ... 采购 → supplier=X
            elif re.search(r"被", cl) and re.search(BUY_VERB, cl):
                out[tags[0][1]] = "supplier"
            # R4: 销售给/卖给 X(A) → buyer=X
            m4 = re.search(r"(?:销售给|卖给)[^，]{0,12}" + P_TAG.pattern, cl)
            if m4:
                out[m4.group(2)] = "buyer"
        elif len(tags) == 1:
            side, name = tags[0][1], tags[0][0]
            head = cl.strip().startswith(name[:2])   # 标注须为句首主语, 防"作为供应商向X(A)供应"误抓
            # R3: X(A) 作为 ... 供应商 → supplier=A
            if head and re.search(r"作为[^，]{0,12}(核心供应|上游供应|供应方|供应商|原材料供应)", cl):
                out[side] = "supplier"
            # R5: X(A) 作为 ... 采购方/下游 → buyer=A
            elif head and re.search(r"作为[^，]{0,10}(采购方|需求方|下游|买方|应用端)", cl):
                out[side] = "buyer"
    return out


def audit_pair(a, b, ext):
    """返回 (flag, detail)。flag=None 表示无信号/一致。"""
    reason = ext.get("reason") or ""
    sides = infer_sides(reason)
    if not sides:
        return None, "no-signal"
    src = ext.get("src") or ""
    issues = []
    for side, role in sides.items():
        if role == "supplier":
            want = side          # 供应方应是 src
        else:
            want = "B" if side == "A" else "A"   # buyer 侧的对面才是 src
        if want != src:
            issues.append(f"reason指认{side}=supplier-buyer链但ext.src={src}")
    sup = {s for s, r in sides.items() if r == "supplier"}
    byr = {s for s, r in sides.items() if r == "buyer"}
    if sup and byr and sup & byr:
        issues.append("reason内部自相矛盾")
    return ("DIR" if issues else None), ";".join(sorted(set(issues))) or "ok"


def main():
    args = sys.argv[1:]
    n_sample = 0
    if "--sample" in args:
        n_sample = int(args[args.index("--sample") + 1])

    rows = [json.loads(l) for l in open(SHADOW, encoding="utf-8") if l.strip()]
    pv2 = [r for r in rows if r.get("pv") == 2 and r.get("ext", {}).get("ok") is True]
    print(f"pv2 positive records: {len(pv2)}")

    flags, nosig, oks = [], 0, 0
    for r in pv2:
        f, detail = audit_pair(r["a"], r["b"], r["ext"])
        if f:
            flags.append((r, detail))
        elif detail == "no-signal":
            nosig += 1
        else:
            oks += 1
    print(f"audited: ok={oks}  no-signal={nosig}  FLAG={len(flags)} "
          f"({len(flags)/max(1,len(pv2))*100:.1f}%)")
    for r, d in flags:
        e = r["ext"]
        print(f"FLAG {e.get('edge')} {r['a']} x {r['b']} src={e.get('src')} "
              f"conf={e.get('conf')} | {d} | {(e.get('reason') or '')[:60]}")
    if n_sample:
        pool = [r for r in pv2 if (r, None) not in flags]
        seen = {id(r[0]) for r in flags}
        pool = [r for r in pv2 if id(r) not in seen]
        for r in random.Random(11).sample(pool, min(n_sample, len(pool))):
            f, d = audit_pair(r["a"], r["b"], r["ext"])
            print(f"SAMPLE[{d}] {r['a']} x {r['b']} src={r['ext'].get('src')} "
                  f"| {(r['ext'].get('reason') or '')[:60]}")


if __name__ == "__main__":
    main()
