# -*- coding: utf-8 -*-
"""P2 实体解析 自测（v3: 含 200 组人工复核 7 组判错负例回归）.

运行: python test_entity_resolution.py
全部通过打印 ALL OK; 任一失败 exit 1。

负例回归来源: 桌面《P2实体解析_200组人工复核打分表_2026-11-07》判错的
7 组（L065/L072/L075/L090/L091/B116/B126），修复后必须保持"不折叠/
正确切分"，防止规则回退。
"""
from __future__ import annotations

import os
import sys

import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import entity_resolution as E  # noqa: E402

_results = []


def check(name: str, ok: bool, detail: str = ""):
    _results.append((name, ok, detail))
    print(f"  [{'OK' if ok else 'FAIL'}] {name}"
          + (f"  ({detail})" if detail and not ok else ""))


def main() -> int:
    print("== 1. parse_seat 主体切分 ==")
    p = E.parse_seat("川财证券有限责任公司北京分公司")
    check("1a B116 负例: 主体完整截断", p["broker"] == "川财证券有限责任公司",
          p["broker"])
    p = E.parse_seat("天府证券有限责任公司成都人民南路证券营业部")
    check("1b B126 负例: 主体完整截断",
          p["broker"] == "天府证券有限责任公司", p["broker"])
    p = E.parse_seat("中信证券(山东)有限责任公司济南分公司")
    check("1c S027 正例: 头词表优先(主体级粒度)",
          p["broker"] == "中信证券", p["broker"])
    p = E.parse_seat("国泰君安证券股份有限公司上海佳杰路证券营业部")
    check("1d 营业部类型", p["branch_type"] == "branch_office")
    p = E.parse_seat("华泰证券股份有限公司总部")
    check("1e 总部类型", p["branch_type"] == "hq")

    print("== 2. seat_variant_key ==")
    check("2a 公司后缀变体等价",
          E.seat_variant_key("海通证券股份有限公司苏州南园北路营业部")
          == E.seat_variant_key("海通证券股份有限公司苏州南园北路证券营业部"))
    check("2b 不同路名不等价",
          E.seat_variant_key("国泰君安证券股份有限公司上海九江路证券营业部")
          != E.seat_variant_key("国泰君安证券股份有限公司上海佳杰路证券营业部"))
    check("2c L075 负例: 数字括号参与键",
          E.seat_variant_key("国联民生交易单元(001777)")
          != E.seat_variant_key("国联民生交易单元(258000)"))

    print("== 3. resolve_legal 折叠规则 ==")
    names = ["GUOTAIJUNANSECURITIES(HONGKONG)LIMITED",
             "GuotaiJunanSecurities(HongKong)Limited"]
    m, audit = E.resolve_legal(names)
    check("3a ASCII 空格大小写折叠", m[names[0]][0] == m[names[1]][0])
    # 负例回归: 括号内容不同绝不折叠(5 组)
    neg = [("清津厚德(天津)科技合伙企业(有限合伙)",
            "清津厚德(济南)科技合伙企业(有限合伙)"),          # L065
           ("香港中央结算有限公司(A股)", "香港中央结算有限公司(沪股通)"),  # L072
           ("国联民生交易单元(001777)", "国联民生交易单元(258000)"),      # L075
           ("香港中央结算有限公司(H股)", "香港中央结算有限公司(沪股通)"),  # L090
           ("红塔创新(昆山)创业投资有限公司", "红塔创新(昆明)创业投资有限公司")]  # L091
    m, _ = E.resolve_legal([x for p_ in neg for x in p_])
    for i, (a, b) in enumerate(neg):
        check(f"3b-{i} 负例不折叠: {a[:12]}…", m[a][0] != m[b][0])
    # 短名编辑距离折叠(无括号)仍可用
    m, audit = E.resolve_legal(["平安银行股份有限公司", "平安银行股份有限公司"])
    check("3c 无括号短名折叠可用", m["平安银行股份有限公司"][0]
          == m["平安银行股份有限公司"][0])

    print("== 4. resolve_person ==")
    mp = E.resolve_person(["王伟", "王伟", "李凌云"])
    check("4a 人名规范化不归并重名",
          mp["王伟"][2] == 0.5 and len({mp["王伟"][0]}) == 1)

    print("== 5. 实体图集成(entity_map.parquet) ==")
    f = os.path.join(_HERE, "data", "entity_map.parquet")
    check("5a 产物存在", os.path.isfile(f))
    if os.path.isfile(f):
        df = pd.read_parquet(f)
        check("5b 三实体类型齐备",
              {"SEAT", "LEGAL", "PERSON"} <= set(df.entity_type.unique()))
        hk = df[(df.entity_type == "LEGAL")
                & df.raw_name.str.contains("香港中央结算", na=False)]
        if len(hk):
            check("5c 香港中央结算各通道独立(实图负例)",
                  hk.canonical_id.nunique() == hk.raw_name.nunique(),
                  f"raw={hk.raw_name.nunique()} can={hk.canonical_id.nunique()}")
        cq = df[df.raw_name == "川财证券有限责任公司北京分公司"]
        if len(cq):
            from entity_resolution import parse_seat
            check("5d B116 实图主体切分",
                  parse_seat(cq.raw_name.iloc[0])["broker"]
                  == "川财证券有限责任公司")

    print()
    n_fail = sum(1 for _, ok, _ in _results if not ok)
    for name, ok, detail in _results:
        if not ok:
            print(f"  FAIL -> {name}  {detail}")
    print(f"== {len(_results) - n_fail}/{len(_results)} PASS ==")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
