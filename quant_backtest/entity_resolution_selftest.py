# -*- coding: utf-8 -*-
"""entity_resolution 自测契约（12 项）.

跑法: python test_entity_resolution.py   （全绿退出码 0）
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from entity_resolution import (classify_holder, normalize_name, parse_seat,
                               resolve_legal, resolve_seats, seat_variant_key)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL"), name, detail)


# ---- 1. 规范化 ----
check("1 全角转半角", normalize_name("中信证券（北京）　总部 ") == "中信证券(北京)总部",
      normalize_name("中信证券（北京）　总部 "))
check("2 空白压缩", normalize_name("UBS  AG") == "UBSAG")

# ---- 2. 结构解析 ----
p = parse_seat("华泰证券股份有限公司成都人民南路证券营业部")
check("3 券商主体", p["broker"] == "华泰证券", p["broker"])
check("4 分支类型", p["branch_type"] == "branch_office", p["branch_type"])
p2 = parse_seat("中信证券股份有限公司总部(非营业场所)")
check("5 总部识别+括号备注", p2["branch_type"] == "hq" and p2["note"] == "非营业场所")

# ---- 3. 变体折叠 ----
check("6 变体键: 有限↔股份", seat_variant_key("财信证券有限责任公司中山三路营业部")
      == seat_variant_key("财信证券股份有限公司中山三路营业部"))
check("7 变体键: 证券营业部归一",
      seat_variant_key("申港证券股份有限公司深圳深南东路营业部")
      == seat_variant_key("申港证券股份有限公司深圳深南东路证券营业部"))

# ---- 4. 归并纪律 ----
m, audit = resolve_seats(["财信证券有限责任公司天津分公司",
                          "财信证券股份有限公司天津分公司"])
check("8 变体折叠为同一实体", m["财信证券有限责任公司天津分公司"][0]
      == m["财信证券股份有限公司天津分公司"][0])
m, _ = resolve_seats(["国泰君安证券股份有限公司上海九江路证券营业部",
                      "国泰君安证券股份有限公司上海佳杰路证券营业部"])
check("9 不同营业部不折叠(九江路≠佳杰路)", m["国泰君安证券股份有限公司上海九江路证券营业部"][0]
      != m["国泰君安证券股份有限公司上海佳杰路证券营业部"][0])
m, _ = resolve_seats(["申港证券股份有限公司北京分公司", "申港证券股份有限公司湖北分公司"])
check("10 不同分公司不折叠", m["申港证券股份有限公司北京分公司"][0]
      != m["申港证券股份有限公司湖北分公司"][0])
m, _ = resolve_seats(["华泰证券第1营业部", "华泰证券第2营业部"])
check("11 分支号差异不折叠", m["华泰证券第1营业部"][0] != m["华泰证券第2营业部"][0])
m, _ = resolve_seats(["中信证券营业部", "华泰证券营业部"])
check("12 不同券商永不折叠", m["中信证券营业部"][0] != m["华泰证券营业部"][0])

# ---- 5. LEGAL ----
m, _ = resolve_legal(["HONGDANIEL", "HongDaniel"])
check("13 ASCII 大小写空格折叠", m["HONGDANIEL"][0] == m["HongDaniel"][0])
m, _ = resolve_legal(["中证500交易型开放式指数证券投资基金",
                      "中证1000交易型开放式指数证券投资基金"])
check("14 长基金名不模糊折叠", m["中证500交易型开放式指数证券投资基金"][0]
      != m["中证1000交易型开放式指数证券投资基金"][0])

# ---- 6. 分类 ----
check("15 法人识别", classify_holder("香港中央结算有限公司") == "legal")
check("16 基金产品识别", classify_holder("中国农业银行股份有限公司-中证500交易型开放式指数证券投资基金")
      == "fund_product")
check("17 自然人识别", classify_holder("王伟") == "person")
check("18 QFII识别", classify_holder("UBSAG") == "legal")

# ---- 7. 产物 ----
import pandas as pd
path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "data", "entity_map.parquet")
check("19 entity_map 存在", os.path.isfile(path))
if os.path.isfile(path):
    df = pd.read_parquet(path)
    need = {"entity_type", "source_table", "raw_name", "canonical_id",
            "canonical_name", "confidence", "method"}
    check("20 schema 完整", need <= set(df.columns))
    check("21 confidence 全部在档", df.confidence.between(0, 1).all())
    check("22 同一 raw 名唯一 canonical",
          df.groupby(["entity_type", "raw_name"]).canonical_id.nunique().max() == 1)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
