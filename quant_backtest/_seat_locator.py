# -*- coding: utf-8 -*-
"""Seat.name → Location 地理锚点提取模块（K-SPATIAL-01）

从龙虎榜席位全名（如 "华泰证券股份有限公司南通解放西路证券营业部"）中
提取所属省/市，返回 {province, city, kind, key}|None。

命中率：探查 13,165 个唯一席位 → 10,879 命中（82.6%）
未命中的主要是拉萨东方财富营业部、沪股通专用、机构专用、总部等虚拟席位。

设计原则：
  - 空值/未命中返回 None（不炸），由上层决定如何处理
  - key 用 "kind|province|city|seat_name" 拼接，Location 节点唯一约束依据
  - 直辖市 city 同省，province='上海' city='上海'
  - 仅省名无市名 → 回退省名作 city（罕见）
"""

import re
from typing import Optional


# 省 → 关键地级市（覆盖国内主流营业部命名模式）
PROVINCE_CITIES = {
    "北京": ["北京"],
    "上海": ["上海"],
    "天津": ["天津"],
    "重庆": ["重庆"],
    "广东": [
        "广州",
        "深圳",
        "东莞",
        "佛山",
        "珠海",
        "中山",
        "惠州",
        "汕头",
        "湛江",
        "茂名",
        "江门",
        "肇庆",
        "揭阳",
    ],
    "江苏": [
        "南京",
        "苏州",
        "无锡",
        "常州",
        "南通",
        "徐州",
        "镇江",
        "扬州",
        "泰州",
        "盐城",
        "淮安",
        "连云港",
    ],
    "浙江": [
        "杭州",
        "宁波",
        "温州",
        "绍兴",
        "嘉兴",
        "湖州",
        "金华",
        "台州",
        "衢州",
        "丽水",
    ],
    "山东": [
        "济南",
        "青岛",
        "淄博",
        "枣庄",
        "东营",
        "烟台",
        "潍坊",
        "济宁",
        "泰安",
        "威海",
        "日照",
        "临沂",
        "德州",
        "聊城",
        "滨州",
        "菏泽",
    ],
    "四川": ["成都", "绵阳", "德阳", "宜宾", "泸州", "乐山", "南充", "达州"],
    "湖北": ["武汉", "宜昌", "襄阳", "荆州", "黄冈", "孝感"],
    "湖南": ["长沙", "株洲", "湘潭", "衡阳", "岳阳", "常德"],
    "福建": ["福州", "厦门", "泉州", "漳州", "莆田"],
    "河南": ["郑州", "洛阳", "开封", "新乡", "焦作", "南阳", "信阳"],
    "河北": ["石家庄", "唐山", "秦皇岛", "邯郸", "保定"],
    "陕西": ["西安", "咸阳", "宝鸡"],
    "辽宁": ["沈阳", "大连", "鞍山", "抚顺"],
    "吉林": ["长春", "吉林"],
    "黑龙江": ["哈尔滨", "大庆", "齐齐哈尔"],
    "安徽": ["合肥", "芜湖", "蚌埠", "安庆", "马鞍山"],
    "江西": ["南昌", "赣州", "九江", "上饶", "景德镇", "宜春"],
    "广西": ["南宁", "桂林", "柳州"],
    "海南": ["海口", "三亚"],
    "贵州": ["贵阳", "遵义"],
    "云南": ["昆明", "大理", "丽江", "曲靖"],
    "山西": ["太原", "大同"],
    "甘肃": ["兰州"],
    "新疆": ["乌鲁木齐"],
    "内蒙古": ["呼和浩特", "包头"],
    "西藏": ["拉萨", "山南"],  # 东方财富营业部主要集中地
    "宁夏": ["银川"],
    "青海": ["西宁"],
}

# 反向市→省映射
CITY_TO_PROVINCE = {c: p for p, cs in PROVINCE_CITIES.items() for c in cs}

# 直辖市列表（city 同 province）
MUNICIPALITIES = {"北京", "上海", "天津", "重庆"}


def extract_location(seat_name: str) -> Optional[dict]:
    """从营业部全名中提取 Location 锚点。

    Returns:
        dict {province, city, kind, key} 或 None（无法识别）
        kind: 'province'（只识别到省）| 'city'（识别到市）| 'seat'（识别到营业部）
    """
    if not seat_name or not isinstance(seat_name, str):
        return None

    # 虚拟席位（沪股通专用/机构专用/总部/非营业场所）→ 标记为 virtual，不做地理映射
    virtual_patterns = [
        r"沪股通专用",
        r"深股通专用",
        r"机构专用",
        r"深市专用",
        r"沪市专用",
        r"总部",
        r"非营业场所",
    ]
    for vp in virtual_patterns:
        if re.search(vp, seat_name):
            return None

    name = (
        seat_name.replace("股份有限公司", "")
        .replace("有限公司", "")
        .replace("证券", "")
    )

    # 1. 直辖市优先
    for mu in MUNICIPALITIES:
        if mu in name:
            key = f"city|{mu}|{mu}|{seat_name}"
            return {"province": mu, "city": mu, "kind": "city", "key": key}

    # 2. 省名 + 省内地级市（同时含）
    for prov, cities in PROVINCE_CITIES.items():
        if prov in name:
            matched_city = None
            for city in cities:
                if city in name:
                    matched_city = city
                    break
            if matched_city:
                key = f"seat|{prov}|{matched_city}|{seat_name}"
                return {
                    "province": prov,
                    "city": matched_city,
                    "kind": "city",
                    "key": key,
                }
            # 只有省名，无市名 → 回退为省级
            key = f"province|{prov}|{prov}|{seat_name}"
            return {"province": prov, "city": prov, "kind": "province", "key": key}

    # 3. 无省名但有已知地级市（兜底：直接从市名查省）
    for city, prov in CITY_TO_PROVINCE.items():
        if city in name:
            key = f"seat|{prov}|{city}|{seat_name}"
            return {"province": prov, "city": city, "kind": "city", "key": key}

    return None


def build_location_nodes(seat_names):
    """批量构建 Location 节点（去重后）。

    Args:
        seat_names: 可迭代的营业部全名

    Returns:
        list[dict]  Location 节点属性（含 key 唯一约束）
        int         识别成功数量
        int         总数
    """
    locs = {}
    total, hit = 0, 0
    for s in seat_names:
        total += 1
        r = extract_location(s)
        if r:
            hit += 1
            if r["key"] not in locs:
                locs[r["key"]] = {
                    "key": r["key"],
                    "kind": r["kind"],
                    "province": r["province"],
                    "city": r["city"],
                    "seat_name": s,
                }
    return list(locs.values()), hit, total
