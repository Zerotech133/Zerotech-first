# -*- coding: utf-8 -*-
"""知识图谱 Phase 2-C ETL：产品/业务品类实体抽取 + PRODUCES 关系建图。

数据流：
  stock_company.business_scope (99.9% 覆盖) → Product 节点 + PRODUCES 边
  stock_company.main_business  (100% 覆盖)  → 补充 Product 节点 + PRODUCES 边

抽取策略：
  1. business_scope：按分号切分经营范围项，去掉括号注释和动词后缀
     （制造/生产/销售/设计/研发/加工/服务/批发/零售/…），剩余名词 = 产品名
  2. main_business：匹配"主要产品为XXX"模式 + 分隔符切分去动词
  3. 频率过滤：产品词须出现 ≥2 次（跨公司），消除一次性噪声
  4. 黑名单过滤：纯动词/活动/泛词不作为产品节点

关系边：
  Company -[PRODUCES {source, validStart, validEnd}]-> Product
  · source: "business_scope" / "main_business"
  · validStart: Security.list_date（上市日，保守下界）
  · validEnd: 9999-12-31（当前快照）

时序图谱铁律：所有边带 validStart/validEnd。

用法：
  python kg_build_p2_prod.py             # 全量建图（幂等可重跑）
  python kg_build_p2_prod.py --dryrun    # 只打印不落库
  python kg_build_p2_prod.py --sample 688411.SH   # 抽检某标的产品
"""
import argparse
import os
import re
import sys
from collections import Counter
from datetime import datetime

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import kg_schema as S
import datamart as dm


def _now():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _safe_str(v):
    if v is None:
        return None
    if isinstance(v, float) and pd.isna(v):
        return None
    if isinstance(v, (list, tuple)):
        return "; ".join(str(x) for x in v)
    return str(v).strip() or None


# ── 动词后缀（按长度降序匹配）─────────────────────────────────
ACTION_SUFFIXES = sorted([
    '技术开发', '技术咨询', '技术服务', '技术转让', '技术推广',
    '技术进出口', '货物进出口', '进出口业务', '进出口代理',
    '信息系统集成', '系统集成', '工程建设', '工程管理', '建设工程',
    '物业管理', '企业管理', '餐饮管理', '酒店管理', '供应链管理',
    '制造', '生产', '销售', '设计', '研发', '加工', '开发',
    '服务', '批发', '零售', '维修', '安装', '租赁', '咨询',
    '代理', '施工', '检测', '检验', '研究', '试验发展',
    '经纪', '出版', '发行', '运营', '管理', '培训', '教育',
], key=len, reverse=True)

# ── 非产品黑名单（纯活动/泛词/动词独立出现）──────────────────
BLACKLIST = {
    # 纯动词独立出现
    '销售', '生产', '制造', '设计', '研发', '加工', '开发', '经营',
    '建设', '工程', '施工', '安装', '投资', '研制', '买卖', '代理',
    '出版', '发行', '运营', '管理', '培训', '经纪', '研究', '试验',
    # 工商登记套话
    '技术', '货物', '技术交流', '技术进出口', '货物进出口', '进出口业务',
    '进出口代理', '一般', '许可', '依法', '须经', '批准', '项目',
    '相关', '经营活动', '经营范围', '营业执照', '自主经营', '进出口',
    '同时', '代理发行', '买卖政府债券', '研制',
    # 管理/咨询类
    '企业管理', '物业管理', '餐饮管理', '酒店管理', '供应链管理',
    '建设工程', '工程管理', '工程建设', '工程咨询', '工程',
    '信息咨询', '信息技术咨询', '信息咨询服务', '社会经济咨询',
    '技术咨询', '技术服务', '技术开发', '技术转让', '技术推广',
    '企业管理咨询', '企业形象策划', '市场营销策划',
    '会议及展览服务', '展览馆', '体育竞赛组织',
    '仓储', '装卸搬运', '供应链',
    '人力资源服务', '劳务服务', '业务培训', '教育咨询服务',
    '护理机构服务', '养老服务', '医疗服务', '医院管理',
    '餐饮服务', '住宿服务', '物业管理',
    '城市配送', '普通货物仓储', '低温仓储',
    '信息系统集成服务', '信息技术咨询服务', '互联网数据服务',
    '大数据服务', '创业空间服务', '科技中介服务', '知识产权代理',
    '商标代理', '版权代理', '工商登记代理代办',
    '税务服务', '财务咨询', '资产评估', '市场调查',
    '社会调查', '安全咨询服务', '公共安全管理咨询',
    '工程和技术研究', '农业科学研究和试验发展',
    '医学研究和试验发展', '自然科学研究和试验发展',
    # 房地产/电信类
    '非居住房地产', '住房租赁', '房地产经纪', '房地产咨询', '房地产评估',
    '房地产', '物业',
    '第二类增值电信业务', '第一类增值电信业务', '基础电信业务',
    '互联网信息服务', '互联网销售',
    # 金融活动类
    '证券自营', '证券经纪', '证券交易', '证券承销与保荐',
    '证券资产管理', '证券投资基金代销', '融资融券',
    '代销金融产品', '实业投资', '证券投资', '投资',
    '为期货公司提供中间介绍业务',
    '证券投资活动有关的财务顾问',
    # 泛词
    '服务', '技术', '货物', '代理', '中介', '经纪人',
    '广告发布', '广告设计', '广告制作', '广告',
    '知识产权服务', '商务代理代办服务',
    '新材料技术', '物联网技术', '技术',
    # 业务/公司类
    '业务', '公司', '主营', '产品', '系列', '一体', '整体',
    '解决方案', '提供', '平台', '系统', '集成', '应用',
    '销售和服务', '销售及服务', '销售与服务', '销售及技术',
    '销售及相关', '销售业务', '销售及技术服务', '销售和技术服务',
    '销售及相关技术服务', '生产和销售业务', '生产和销售.',
    '生产和销售的高新技术企业',
    '经相关部门批准后方可开展经营活动',
    '（依法须经批准的项目',
    # 短泛词
    '材料', '设备', '产品', '系统', '平台',
}

# 前缀清理正则
_PREFIX_RE = re.compile(r'^(一般|许可)[经营项目项]+[是:：]+')


def _strip_action(word):
    """去掉动词后缀，返回核心名词。"""
    for act in ACTION_SUFFIXES:
        if word.endswith(act) and len(word) > len(act) + 1:
            return word[:-len(act)].strip('的与及和 ')
    return word


def extract_from_scope(text):
    """从 business_scope 提取产品词列表。"""
    if not text or (isinstance(text, float) and pd.isna(text)):
        return []
    text = str(text)
    products = []
    for part in re.split(r'[；;]', text):
        part = _PREFIX_RE.sub('', part.strip())
        for sp in re.split(r'[、，,]', part):
            sp = re.sub(r'[（(].*?[)）]', '', sp).strip()
            if not (2 <= len(sp) <= 15):
                continue
            sp = _strip_action(sp)
            if 2 <= len(sp) <= 15 and sp not in BLACKLIST:
                products.append(sp)
    return products


def extract_from_main(text):
    """从 main_business 提取产品词列表。"""
    if not text or (isinstance(text, float) and pd.isna(text)):
        return []
    text = str(text)
    products = []

    # 模式1: "主要产品为/是XXX"
    m = re.search(r'主要产品[为是](.+?)[。.等，,；;]', text)
    if m:
        for p in re.split(r'[、，,；;]', m.group(1)):
            p = p.strip()
            if 2 <= len(p) <= 15 and p not in BLACKLIST:
                products.append(p)

    # 模式2: 分隔符切分后去动词
    for p in re.split(r'[，,、；;。\s]+', text):
        p = p.strip()
        if not (2 <= len(p) <= 15):
            continue
        p = _strip_action(p)
        if 2 <= len(p) <= 15 and p not in BLACKLIST:
            products.append(p)

    return products


# ── 主流程 ─────────────────────────────────────────────────────
def build(dryrun=False, min_freq=2):
    print("=== 知识图谱 Phase 2-C 产品抽取开始 %s ===" % _now())

    # 1) 读源数据
    print("[1/6] 读源数据…")
    df_company = dm.read('stock_company')
    df_basic = dm.read('stock_basic')
    print("  stock_company: %d 行" % len(df_company))
    print("  stock_basic: %d 行" % len(df_basic))

    # ts_code → list_date 映射
    list_date_map = {}
    for _, r in df_basic.iterrows():
        tc = _safe_str(r.get('ts_code'))
        ld = _safe_str(r.get('list_date'))
        if tc and ld:
            list_date_map[tc] = ld

    # 2) 第一遍：抽取所有候选产品 + 频率统计
    print("[2/6] 第一遍抽取候选产品词…")
    # company → set(products) 去重（同公司 scope+main 可能重复提到）
    freq = Counter()
    company_products = {}  # com_id → {product: source}
    for _, r in df_company.iterrows():
        com_id = _safe_str(r.get('com_id'))
        ts_code = _safe_str(r.get('ts_code'))
        if not com_id:
            continue
        scope_prods = set(extract_from_scope(r.get('business_scope')))
        main_prods = set(extract_from_main(r.get('main_business')))

        prods = {}
        for p in scope_prods:
            prods[p] = 'business_scope'
        for p in main_prods:
            if p not in prods:  # scope 优先
                prods[p] = 'main_business'

        company_products[com_id] = (ts_code, prods)
        for p in prods:
            freq[p] += 1

    # 频率过滤
    qualified = {p for p, c in freq.items() if c >= min_freq}
    print("  候选产品词: %d  (≥%d次: %d)" % (len(freq), min_freq, len(qualified)))

    # 3) 构建 Product 节点 + PRODUCES 边
    print("[3/6] 构建 Product 节点 + PRODUCES 边…")
    now = _now()
    product_rows = [{"name": p, "source": "business_scope+main_business"}
                    for p in sorted(qualified)]

    edge_rows = []
    companies_with_products = 0
    for com_id, (ts_code, prods) in company_products.items():
        ld = list_date_map.get(ts_code, '19900101')
        has_product = False
        for p, src in prods.items():
            if p in qualified:
                edge_rows.append({
                    "src": com_id,
                    "dst": p,
                    "validStart": ld,
                    "validEnd": S.VALID_END_OPEN,
                    "source": src,
                    "updated_at": now,
                })
                has_product = True
        if has_product:
            companies_with_products += 1

    print("  Product 节点: %d" % len(product_rows))
    print("  PRODUCES 边: %d (覆盖 %d/%d 公司)" % (
        len(edge_rows), companies_with_products, len(df_company)))

    if dryrun:
        print("DRYRUN 结束，未连接 Neo4j。")
        # 打印 Top 20 产品
        print("\n=== Top 20 高频产品 ===")
        for p, c in freq.most_common(20):
            tag = "✓" if p in qualified else "✗"
            print("  %s %4d  %s" % (tag, c, p))
        return

    # 4) 落库
    print("[4/6] 连接 Neo4j 并落库…")
    from kg_client import KgClient
    cli = KgClient()
    cli.ensure_schema()

    n_prod = cli.upsert_nodes(S.LBL_PRODUCT, ["name"], product_rows)
    print("  Product upsert: %d" % n_prod)

    # PRODUCES: Company.com_id -> Product.name
    n_edge = cli.upsert_edge(
        S.LBL_COMPANY, "com_id", S.LBL_PRODUCT, "name",
        S.REL_PRODUCES, edge_rows)
    print("  PRODUCES upsert: %d" % n_edge)

    # 5) 统计
    print("[5/6] 统计…")
    cli.stats()
    cli.close()
    print("=== Phase 2-C 建图完成 %s ===" % _now())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dryrun', action='store_true', help='只打印不落库')
    ap.add_argument('--sample', help='抽检某 ts_code 子图')
    ap.add_argument('--min-freq', type=int, default=2,
                    help='产品词最低出现次数（默认2）')
    args = ap.parse_args()

    if args.sample:
        from kg_client import KgClient
        cli = KgClient()
        cli.sample_subgraph(args.sample)
        cli.close()
        return

    build(dryrun=args.dryrun, min_freq=args.min_freq)


if __name__ == "__main__":
    main()
