# -*- coding: utf-8 -*-
"""情报五要素抽取 v0 —— 人 / 地 / 事 / 物 / 组织。

定位
----
AIDE（情报研判引擎）的地基。把 news 的标题正文抽成结构化要素，供
「时间关系 / 空间关系 / 关联关系」三类关系计算。纯规则 + 词典，零重型
依赖，13.7 万条全量抽取在分钟级完成。

五要素口径（v0 保守版：宁可少抽，不可错抽）
------------------------------------------
org     组织   A 股公司名（stock_names.name2code，含别名）+ 机构名（部委 /
               交易所 / 央行 / 银行 / 券商 / 协会 / 研究院 / 高校）
place   地     省级行政区 + 主要城市 + 国家地区 + 特殊经济区
person  人     **V0 只抽角色，不抽人名**——董事长 / 总经理 / 实控人等。
               人名识别无 NER 时误报率过高（"张江高科"里也有"张"），
               故留到 v1。
thing   物     产品 / 技术 / 资产类别概念词（内置高频词典，可外挂扩充）
event   事     事件类型 9 类分类 + 命中关键词（替代原 event_type 空读）

关系口径（由关系层消费，本模块只产出要素）
------------------------------------------
时间关系  事件 ts 序列本身（先后 / 并发 / 间隔）
空间关系  place + 组织所属地域与行业
关联关系  同 org / 同 place / 同 event_type / 同信源

schema：intel_facts.v0（见 analyze() 返回）
"""
from __future__ import annotations

import io
import json
import os
import re

SCHEMA_VERSION = "intel_facts.v0"

# ── 事件类型：9 类 + other ───────────────────────────────────────────────
# 关键词刻意取"专指名"而非泛词（如用"净利"不用"利润"），降低串类。
EVENT_TYPES = {
    "业绩": ("年报", "中报", "半年报", "季报", "业绩快报", "业绩预告", "预增",
             "预减", "预亏", "扭亏", "净利", "营收", "营业收入", "净利润",
             "毛利率", "同比增长", "同比下降", "财报"),
    "并购重组": ("收购", "并购", "重组", "资产注入", "吸收合并", "股权转让",
                 "要约收购", "借壳", "资产置换", "剥离", "出售资产"),
    "融资": ("定增", "定向增发", "配股", "可转债", "增发", "募资",
             "募集资金", "发债", "公司债", "中期票据", "超短融", "IPO",
             "上市辅导", "过会"),
    "股东治理": ("减持", "增持", "回购", "股权激励", "董事会", "监事会",
                 "辞职", "离职", "质押", "解禁", "限售", "实控人",
                 "控股股东", "举牌", "换届", "选举"),
    "经营订单": ("中标", "订单", "合同", "签约", "投产", "产能", "扩产",
                 "开工", "框架协议", "战略合作", "供货", "定点", "量产线"),
    "监管风险": ("问询", "关注函", "立案", "处罚", "违规", "诉讼", "仲裁",
                 "退市", "风险警示", "监管函", "警示函", "调查", "ST",
                 "失信", "被执行", "冻结"),
    "分红": ("分红", "派息", "送转", "除权", "利润分配", "现金红利",
             "股息", "每 10 股"),
    "研发产品": ("研发", "获批", "临床试验", "注册证", "专利", "新品",
                 "认证", "注册批件", "上市许可", "获批上市", "中试"),
    "政策宏观": ("政策", "补贴", "降准", "降息", "关税", "规划",
                 "指导意见", "试点", "国务院", "发改委", "工信部", "两会",
                 "专项债", "以旧换新"),
}

_EVENT_RE = {k: re.compile("|".join(re.escape(w) for w in v))
             for k, v in EVENT_TYPES.items()}

# ── 地（place）────────────────────────────────────────────────────────
PROVINCES = (
    "北京", "天津", "上海", "重庆", "河北", "山西", "辽宁", "吉林", "黑龙江",
    "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南", "湖北", "湖南",
    "广东", "海南", "四川", "贵州", "云南", "陕西", "甘肃", "青海", "台湾",
    "内蒙古", "广西", "西藏", "宁夏", "新疆", "香港", "澳门",
)
CITIES = (
    "深圳", "广州", "杭州", "南京", "苏州", "无锡", "常州", "宁波", "温州",
    "绍兴", "嘉兴", "金华", "台州", "合肥", "芜湖", "福州", "厦门", "泉州",
    "南昌", "赣州", "济南", "青岛", "烟台", "潍坊", "郑州", "洛阳", "武汉",
    "宜昌", "襄阳", "长沙", "株洲", "成都", "绵阳", "宜宾", "贵阳", "昆明",
    "西安", "兰州", "西宁", "银川", "乌鲁木齐", "呼和浩特", "包头", "大连",
    "沈阳", "长春", "哈尔滨", "石家庄", "唐山", "保定", "太原", "南宁",
    "柳州", "海口", "东莞", "佛山", "珠海", "中山", "惠州", "汕头", "江门",
    "湛江", "肇庆", "义乌", "慈溪", "余姚", "江阴", "昆山", "张家港",
    "常熟", "太仓", "晋江", "龙岩", "莆田", "盐城", "扬州", "泰州", "镇江",
    "徐州", "淮安", "连云港", "宿迁", "湖州", "衢州", "丽水", "舟山",
)
COUNTRIES = (
    "美国", "欧盟", "欧洲", "日本", "韩国", "德国", "法国", "英国", "意大利",
    "西班牙", "荷兰", "瑞士", "瑞典", "挪威", "丹麦", "芬兰", "俄罗斯",
    "乌克兰", "印度", "越南", "泰国", "马来西亚", "新加坡", "印尼",
    "印度尼西亚", "菲律宾", "澳大利亚", "新西兰", "加拿大", "墨西哥",
    "巴西", "阿根廷", "智利", "南非", "埃及", "沙特", "阿联酋", "伊朗",
    "以色列", "土耳其", "哈萨克斯坦", "巴基斯坦", "缅甸", "柬埔寨", "老挝",
)
ZONES = ("自贸区", "自贸港", "保税区", "经开区", "高新区", "开发区",
         "综保区", "粤港澳", "长三角", "珠三角", "京津冀", "雄安", "海南自贸港")

# 按长度倒序，保证"内蒙古"先于"内蒙"、"黑龙江"先于"黑龙"
_PLACE_WORDS = tuple(sorted(set(PROVINCES + CITIES + COUNTRIES + ZONES),
                            key=lambda x: -len(x)))

# ── 物（thing）：高频概念 / 产品 / 资产类别 ────────────────────────────
THING_WORDS = tuple(sorted(set((
    # 能源与电力
    "锂电", "锂矿", "碳酸锂", "动力电池", "储能", "光伏", "硅料", "组件",
    "逆变器", "风电", "海上风电", "核电", "氢能", "燃料电池", "绿电",
    "电网", "特高压", "充电桩", "换电",
    # 半导体与电子
    "芯片", "半导体", "晶圆", "封测", "光刻", "EDA", "存储芯片", "面板",
    "OLED", "PCB", "被动元件", "MLCC", "传感器", "射频", "功率器件",
    "第三代半导体", "碳化硅",
    # 算力与通信
    "算力", "数据中心", "IDC", "液冷", "服务器", "光模块", "交换机",
    "5G", "6G", "卫星", "卫星互联网", "北斗", "物联网", "云计算", "AI",
    "大模型", "机器人", "人形机器人", "减速器", "丝杠",
    # 汽车与装备
    "新能源车", "整车", "汽车零部件", "智能驾驶", "激光雷达", "一体化压铸",
    "工程机械", "工业母机", "机床", "刀具", "注塑机",
    # 医药与消费
    "创新药", "仿制药", "CXO", "原料药", "疫苗", "医疗器械", "中药",
    "白酒", "啤酒", "乳制品", "调味品", "预制菜", "餐饮", "免税", "旅游",
    "医美", "化妆品", "宠物",
    # 资源与材料
    "稀土", "钨", "钼", "铜", "铝", "黄金", "白银", "镍", "钴", "锡",
    "钢铁", "水泥", "玻璃", "纯碱", "化工", "农药", "化肥", "磷矿",
    "石墨", "碳纤维", "电解液", "隔膜", "正极", "负极",
    # 地产金融
    "地产", "物业", "REITs", "券商", "保险", "银行", "信托", "理财",
    "数字货币", "稳定币", "跨境支付",
    # 军工与航天
    "军工", "航空发动机", "导弹", "无人机", "低空经济", "商业航天",
    # 农业
    "种业", "转基因", "生猪", "白羽鸡", "饲料", "水产",
)), key=lambda x: -len(x)))

# ── 人（person）：V0 只认角色 ─────────────────────────────────────────
ROLE_WORDS = ("董事长", "副董事长", "总经理", "副总经理", "总裁", "副总裁",
              "财务总监", "董事会秘书", "董秘", "实控人", "实际控制人",
              "控股股东", "创始人", "CEO", "CTO", "CFO", "首席",
              "高管", "董监高", "独立董事", "监事")

# ── 组织：机构通用名词（与公司名互补）────────────────────────────────
INSTITUTIONS = ("证监会", "银保监会", "金融监管总局", "央行", "人民银行",
                "交易所", "上交所", "深交所", "北交所", "发改委", "工信部",
                "财政部", "商务部", "科技部", "国资委", "国务院", "住建部",
                "生态环境部", "农业农村部", "能源局", "药监局", "市场监管总局",
                "国家税务总局", "海关总署", "统计局", "网信办", "外汇局",
                "证券业协会", "上市公司协会", "中科院", "工程院", "社科院",
                "国常会", "政治局", "工信部装备司", "国家能源局", "工信部电子司")

# ── 组织：A 股公司名词典（trie）──────────────────────────────────────
_ORG_TRIE: dict = {}
_ORG_NAME_LEN = (2, 8)


def _trie_add(root: dict, word: str) -> None:
    node = root
    for ch in word:
        node = node.setdefault(ch, {})
    node["$"] = True


def load_org_names(path: str = "") -> dict:
    """构建 A 股公司名 trie（进程级单例）。返回 {word: True} 便于调用方判空。"""
    global _ORG_TRIE
    if _ORG_TRIE:
        return _ORG_TRIE
    if not path:
        here = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(os.path.dirname(here), "quant_backtest", "data",
                            "stock_names.json")
    trie: dict = {}
    try:
        with io.open(path, encoding="utf-8") as f:
            d = json.load(f) or {}
        names = list((d.get("name2code") or {}).keys())
    except Exception:  # noqa: BLE001 —— 词典缺失不阻断，退化为仅机构名
        names = []
    for nm in names:
        w = _clean_org_name(nm)
        # 2 字公司名（如"万科"）歧义极大，只收 4 字及以上，3 字需在别名表
        if len(w) >= 4 or (len(w) == 3 and nm in (d.get("alias_of") or {})):
            _trie_add(trie, w)
    for w in INSTITUTIONS:
        _trie_add(trie, w)
    _ORG_TRIE = trie
    return trie


def _clean_org_name(nm: str) -> str:
    """"*ST海航(退)" / "ST星源(退)" → 主体名，去掉风险标记与退市后缀。"""
    s = str(nm or "").strip()
    s = re.sub(r"\(退\)$|（退）$", "", s)
    s = re.sub(r"^\*?ST|^\*ST|^S\*ST|^SST", "", s)
    s = re.sub(r"^N|^C(?=[\u4e00-\u9fa5])", "", s)
    return s.strip()


def _scan_orgs(text: str) -> list:
    """trie 最长匹配，单次线性扫描（不回溯，命中即收）。同一公司在文中
    多次出现只保留一次——关联关系按「实体去重集合」算，重复会放大权重。"""
    trie = load_org_names()
    if not trie or not text:
        return []
    hits, L = [], len(text)
    i = 0
    lo, hi = _ORG_NAME_LEN
    while i < L:
        node = trie.get(text[i])
        if node is None:
            i += 1
            continue
        j, last = i + 1, -1
        while j < L and (j - i) < hi:
            node = node.get(text[j])
            if node is None:
                break
            j += 1
            if node.get("$") and (j - i) >= lo:
                last = j
        if last > 0:
            hits.append(text[i:last])
            i = last
        else:
            i += 1
    return list(dict.fromkeys(hits))


def _find_words(text: str, words) -> list:
    """词典命中（按长度倒序去重叠，长词优先）。"""
    out, taken = [], []
    for w in words:
        if len(w) < 2 or w not in text:
            continue
        start = text.find(w)
        while start >= 0:
            span = (start, start + len(w))
            if not any(span[0] < t[1] and t[0] < span[1] for t in taken):
                out.append(w)
                taken.append(span)
                break
            start = text.find(w, start + 1)
    return out


def classify_event(text: str) -> tuple:
    """返回 (event_type, 命中关键词列表)。无命中 → ("other", [])。"""
    if not text:
        return "other", []
    best, best_hits = "other", []
    for etype, rx in _EVENT_RE.items():
        hits = rx.findall(text)
        if len(hits) > len(best_hits):
            best, best_hits = etype, list(dict.fromkeys(hits))
    return best, best_hits


def analyze(title: str, content: str = "", body_chars: int = 240) -> dict:
    """一条新闻 → 五要素。content 只取前 body_chars 字（尾部多为免责声明）。

    返回 intel_facts.v0：{
        event_type, event_kw, org[], place[], person[], thing[], bias_hint
    }
    """
    t = (title or "").strip()
    body = (content or "").strip()[:body_chars]
    text = t + "。" + body if body else t
    etype, ekw = classify_event(text)
    return {
        "schema": SCHEMA_VERSION,
        "event_type": etype,
        "event_kw": ekw[:6],
        "org": _scan_orgs(text)[:8],
        "place": _find_words(text, _PLACE_WORDS)[:6],
        "person": _find_words(text, ROLE_WORDS)[:4],
        "thing": _find_words(text, THING_WORDS)[:8],
    }
