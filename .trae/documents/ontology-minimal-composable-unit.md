# 图谱本体论重构：最小颗粒 + 可组合

> 拍板日期：2026-10-19
> 背景：当前图谱把"有 3+ 参与者的业务事实"压成一条边（如 TRADED_ON {buy, sell, net_buy}），写图时丢了维度（side 方向）、查图时拼不出时空联合视角（"上海游资买了江苏标的"要跨 3 条边才能凑齐）。目标：每一条业务事实保留为不可再分的原子事件节点，用带类型的边连到所有参与者——查询端按需重新组合。

---

## 一、原子单元判定框架（三个原则）

| 原则 | 判定 | 反例（当前错压成边的） | 正例（应保持为节点的） |
|---|---|---|---|
| **有 3+ 参与者** | 升为节点 | TRADED_ON（Seat + Security + TradeDate 三方）、HOLDS（Org + Security + TradeDate + ratio 快照） | Event/MarketEvent 已是节点 ✅ |
| **有可枚举的方向维度** | 升为节点 | TRADED_ON 丢了 side=0(净买)/1(净卖) | 应保留 TradeAction {side, amount} |
| **自身就是可被引用/聚合的主体** | 升为节点 | 2026Q3 多家机构同时持有联亚药业 → 聚合查询需要一个公共锚点 | TradeDate 已是锚点 ✅ |

**一句话总结：边只做"二实体间的静态归属/层级/引用"，事件/快照/交易一律升为节点。**

### POLE+O 五类型本体映射到我们项目

| POLE+O 类型 | 定义 | 我们的映射 |
|---|---|---|
| **P**hysical | 有物理形态的实体 | Security、Company、Person、Seat、HotMoney、Location |
| **O**rg 👈 我们当前最缺的 | 有组织结构的实体 | Security 已建模，缺 Company 内部 Person→Company→Organization 链 |
| **L**ocation | 时空锚点 | Region（已有 32 个）+ 新增 Location（城市/营业部级） |
| **E**vent | 发生的事（多参与者的事实） | Event、MarketEvent、**新增 TradeAction / HoldingEvent** |
| **+O**ntology | 分类/类型系统 | Industry、Concept、Theme |

---

## 二、四类关系逐一审计

### 关系 1：网络/分类关系 → 保持为边（当前正确）

```
Security -[INDUSTRY_OF]-> Industry
Security -[AREA_OF]-> Region
Security -[SW_L1_OF]-> Concept
Company -[SUB_OF]-> Company
Person -[CONDUCTION]-> Security
```
✅ 无聚合、无方向丢失、无跨 3 方。唯一增强：Security→Region 当前只有省级 AREA_OF，新增 Company→Location（注册地）、Seat→Location（营业部所在地）。

### 关系 2：组织/行为关系 → 事件节点化

#### 2a. TRADED_ON → TradeAction（本轮施工目标）

**当前错模（探查实据）：**
| 指标 | 数值 |
|---|---|
| ts_top_inst 原始行 | 431,892 行 |
| 聚合后 TRADED_ON 边 | 431,586 条 → 压缩比 1.00x（几乎没压缩） |
| 丢了 side 方向（净买/净卖） | side=0(净买) 252,293 行 / side=1(净卖) 179,599 行 → 合成 buy/sell/net_buy 属性后方向不可查 |
| Seat 节点覆盖 | 247 个（仅游资词表命中）/ 实际原始 13,165 个 |
| TradeAction → TradeDate 时序锚点 | ❌ 完全缺失（当前 TRADED_ON 没有 ON_DATE 边） |
| Seat → Location 空间锚点 | ❌ 完全缺失（Location 命中率 82.6% 可提） |

**重构后：**
```
TradeAction {
    id: 'ta:{exalter_hash}:{ts_code}:{trade_date}:{side}'
    source: 'ts_top_inst'
    side: 0=净买 / 1=净卖          ← 保留方向
    amount: float                   ← 原始 buy 或 sell
    knowledge_time: datetime       ← 双时态之一
}

边：
  Seat -[:EXECUTED_BY {role='primary'}]-> TradeAction
  HotMoney -[:OCCUPIED_BY {confidence}]-> TradeAction    ← 经 Seat→OCCUPIES 直挂
  Security -[:TARGETS]-> TradeAction
  TradeDate -[:ON_DATE]-> TradeAction
  Location -[:LOCATED_AT]-> TradeAction                  ← 空间锚点
```

**可组合的查询视角（当前做不到）：**
```cypher
// Q1: 上海游资在 2026-10-14 净买了江苏标的的有哪些？
MATCH (ta:TradeAction {side:0})-[:ON_DATE]->(td:TradeDate {date:'2026-10-14'})
MATCH (ta)-[:EXECUTED_BY]->(st:Seat)-[:LOCATED_AT]->(loc_sh:Location {province:'上海'})
MATCH (ta)-[:TARGETS]->(sec:Security)-[:AREA_OF]->(rgn:Region {name:'江苏省'})
RETURN st.name, sec.ts_code, ta.amount

// Q2: 章建平今天只卖不买了哪些票？
MATCH (hm:HotMoney)-[:OCCUPIES]->(st:Seat)
MATCH (hm)-[:OCCUPIED_BY]->(ta_sell:TradeAction {side:1})-[:ON_DATE]->($today)
MATCH (hm)-[:OCCUPIED_BY]->(ta_buy:TradeAction {side:0})-[:ON_DATE]->($today)
WHERE NOT (st)-[:EXECUTED_BY]->(ta_buy)
RETURN DISTINCT ta_sell

// Q3: 同一标的 5 个交易日内被 3+ 个不同省的游资席位盯上了？
MATCH (sec:Security)<-[:TARGETS]-(ta:TradeAction)-[:ON_DATE]->(td:TradeDate)
WITH sec, td, collect(DISTINCT loc.province) AS provs, count(DISTINCT ta) AS cnt
WHERE size(provs) >= 3 AND cnt >= 5
// ...
```

#### 2b. HOLDS → HoldingEvent（下一轮）

当前 `Organization -[HOLDS {ratio, shares, validStart}]-> Security` 也是快照。同一报告期多家机构同时持有同一 Security → 应共享 TradeDate 锚点便于"2026Q3 谁同时买了联亚药业和 A"的聚合查询。

### 关系 3：时序关系 → 双时态增强

**当前只有 Event Time（validStart/validEnd），缺 Knowledge Time。**

为什么必须双时态（金融场景防前视偏差）：
- Event Time：事实在世界中何时成立（公告发布日 / 龙虎榜上榜日）
- Knowledge Time：我们何时知道这件事（news_collector 入库日 / tdx 同步日）

举例：联亚药业上市公告 2026-10-14 发布（Event Time），我们 2026-10-20 才抓到（Knowledge Time）。10-14 至 10-19 期间任何回测/推演不应"知道"这条公告。

**重构：** 所有带时序的边 + 所有 Event 类节点增加 `knowledge_time` 属性。TradeDate 节点保持不动（已是最优时序锚点）。

### 关系 4：时空关系 → Location + LOCATED_AT（本轮含在 TradeAction 里）

Location 节点两种粒度：
```
Location {kind='province', name='江苏省', code='320000'}
Location {kind='city',    name='南通市', code='320600', parent='江苏省'}
Location {kind='seat',    name='华泰证券南通营业部', address='...', parent='南通市'}
```

Seat → Location 映射从 `Seat.name` 正则提取，实测命中率 **82.6%**（10,879 / 13,165）。未命中的主要是：拉萨东方财富营业部（可归为"西藏"省级）、沪股通专用/机构专用/总部（标记为 `kind='virtual'` 不做地理映射）。

---

## 三、INVOLVES 方向统一

当前 ontology.py 注册 `frm="Event", to="Security"`，但 kg_schema.py 写入 `Security -[INVOLVES]-> Event`。**统一为 Event → Security**，理由：事件是叙事的触发方，"上市公告"指向"联亚药业"符合自然语义。

修正影响范围：
- `kg_build_p3_evt.py` 约 3 处写入方向反转
- `kg_portal.py canvas_timeline()` 查询方向反转
- 已有的 Security→Event 旧边可保留做兼容，但新写入一律 Event→Security

---

## 四、Neo4j Schema 变更清单

### 新增节点类型

| Label | Key | 核心属性 | 索引/约束 |
|---|---|---|---|
| `TradeAction` | `id` | `side, amount, source, knowledge_time` | `TradeAction(id)` UNIQUE |
| `Location` | `key`（`city_kind_name`） | `kind, province, city, parent` | `Location(key)` UNIQUE |

### 新增边类型

| 边 | 方向 | 核心属性 |
|---|---|---|
| `EXECUTED_BY` | `TradeAction → Seat` | |
| `TARGETS` | `TradeAction → Security` | |
| `OCCUPIED_BY` | `TradeAction → HotMoney` | `confidence`（经 Seat→OCCUPIES 推导） |
| `LOCATED_AT` | `TradeAction → Location` | |
| `SEAT_LOCATED_AT` | `Seat → Location` | `validStart` |
| `HAS_TRADED` | `TradeDate → TradeAction` | |

### 保留旧边（双写过渡至少 2 周）

- `TRADED_ON` 旧边保留，新 TradeAction 写图时同步写一份 TRADED_ON 作为兼容层
- `ON_DATE` 旧边不动（已有 729,918 条，覆盖 Event/MarketEvent/Security 全景），TradeAction 新增的 `HAS_TRADED` 或复用 `ON_DATE` → 统一为 TradeDate → TradeAction 的方向

---

## 五、分阶段 R&D 计划

### Phase 1：时空关系打底（K-SPATIAL-01）— 本轮
**目标：TradeAction + Location + Seat.LOCATED_AT**

| 步骤 | 交付物 | 验收标准 |
|---|---|---|
| 1.1 | `kg_schema.py` 新增 TRADE_ACTION / LOCATION 节点类型 + 约束 | `python kg_schema.py --check` 通过 |
| 1.2 | `_seat_locator.py`：Seat.name → Location 正则提取模块 | 命中率 ≥ 82%（与探查一致），空值/未命中返回 None 不报错 |
| 1.3 | `kg_build_cl1.py` 重构：TRADED_ON 旧边 + TradeAction 双写 | Neo4j TradeAction 节点数 ≈ 431,892（原始行数），比旧 TRADED_ON（101,518）多 |
| 1.4 | 补充 HotMoney→TradeAction 直挂边（经 Seat→OCCUPIES 投影） | `HotMoney` 命中的 seat 对应的 TradeAction 都有 OCCUPIED_BY 边 |
| 1.5 | 验收查询 | 至少 3 条新查询能跑通：Q1 跨省时空、Q2 方向偏好、Q3 多省聚集 |

**切片边界：不动 HOLDS、不动 INVOLVES 方向、不清理旧 TRADED_ON 边。**

### Phase 2：组织关系事件化（K-SPATIAL-02）— 下一轮
- HOLDS → HoldingEvent + 双时态（knowledge_time）
- SERVES_AS 方向验证 + validStart 严格维护
- OCCUPIES 增强：HotMoney 更名追踪链

### Phase 3：双时态全量增强（K-SPATIAL-03）
- 所有时序边 + Event 节点增加 knowledge_time
- 回测管线增加"Knowledge Time 过滤器"：推演时只看 knowledge_time < 推演时点 的事实

### Phase 4：INVOLVES 方向统一 + 旧边清理
- Event ← Security 反转
- 旧 TRADED_ON 边清理（待 Phase 1 线上跑稳 2 周后）
- 图谱 API 层适配新方向

---

## 六、术语速查

| 术语 | 定义 |
|---|---|
| **最小颗粒** | 图中不可再分的原子事实，每条原始 ts_top_inst 行就是一个 TradeAction 的原子单元 |
| **可组合** | 原子单元通过带类型的边连接到不同参与者，查询时按需沿边遍历，不依赖写图时预聚合 |
| **Reified Relationship** | 把 3+ 参与者的"关系"升为节点（reify = 具体化），RDF 标准模式，Neo4j Event 建模通用实践 |
| **双时态** | Event Time（事实成立时点）+ Knowledge Time（我们知道的时点），Graphiti/Zep bi-temporal 模型 |
| **POLE+O 五类型** | Physical / Org / Location / Event / +Ontology，Neo4j 2026 年发布的知识图谱设计原则 |
