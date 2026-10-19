# 图谱本体论重构 —— 研发计划（R&D Plan）

> 版本：v1.0　日期：2026-10-19　状态：待拍板
> 前置方案：《图谱本体论重构：最小颗粒+可组合》
> 目标：把"有 3+ 参与者"的业务事实从边升为原子事件节点，查询端按需重组，补齐时序+时空双维度。

---

## 0. 背景与动机

当前图谱把多参与者事实压成边，写图丢维度、查图拼不齐：

- `Seat -[TRADED_ON {buy,sell,net_buy}]-> Security`：
  - 探查实证：ts_top_inst 原始 431,892 行 → 聚合 TRADED_ON **431,586 边**（压缩比 1.00x，几乎没压缩却丢了东西）
  - **side 方向丢失**（净买 side=0：252,293 行 / 净卖 side=1：179,599 行），合并成三属性后方向不可查
  - **无时序锚点**：TRADED_ON 无 ON_DATE → TradeDate 边
  - **无空间锚点**：Seat 无 Location（名字含城市可提取，探查命中率 **82.6%**）
- **时空联合查询不可行**："上海游资在 2026-10-14 净买了江苏标的"需跨 3+ 条边且各边缺维度才能凑齐。

---

## 1. 设计原则（判定框架）

| 原则 | 判定 | 动作 |
|---|---|---|
| 3+ 参与者 | 升为节点 | TRADED_ON、HOLDS |
| 有可枚举方向维度 | 升为节点 | TS 的 side（净买/净卖） |
| 自身可被引用/聚合 | 升为节点 | 同一报告期多家机构同持一票（需公共锚点 TradeDate） |
| 仅二实体静态归属/层级 | 保持边 | INDUSTRY_OF、AREA_OF、SUB_OF |
| 仅描述一个值 | 降为属性 | name、amount |

**一句话：边只做"二实体间静态归属/层级/引用"，事件/快照/交易一律升为节点。**

POLE+O 五类型映射：P=Security/Company/Seat/HotMoney/Location，O=公司链，L=Region+新增 Location，E=Event/MarketEvent/新增 TradeAction/HoldingEvent，+O=Industry/Concept/Theme。

---

## 2. 里程碑总览

| Phase | 工单 | 交付物 | 验收标准（关键） |
|---|---|---|---|
| **P1** | K-SPATIAL-01 | TradeAction + Location + Seat.LOCATED_AT 时空打底 | TradeAction 节点 ≈ 431,892；3 条新查询跑通 |
| **P2** | K-SPATIAL-02 | HOLDS → HoldingEvent + 双时态(knowledge_time) | HoldingEvent 可被 TradeDate 锚点聚合 |
| **P3** | K-SPATIAL-03 | 全图双时态 + 回测 Knowledge Time 过滤 | 前视偏差测试套件通过 |
| **P4** | K-SPATIAL-04 | INVOLVES 方向统一 + 旧边清理 | 兼容回归 56 passed |

> 逐阶段独立可验收、可独立上线。P1 完成后后续阶段单独拍板。

---

## 3. Phase 1：时空关系打底（K-SPATIAL-01）

### 3.1 切片边界

| 做 | 不做 |
|---|---|
| 新增节点 TradeAction/Location + 约束 | ❌ 不动 HOLDS |
| Seat.name→Location 正则提取 | ❌ 不动 INVOLVES 方向 |
| kg_build_cl1 双写（TRADED_ON 旧边 + TradeAction 新节点） | ❌ 不清理旧边 |
| HotMoney→TradeAction 直挂（经 OCCUPIES 投影） | ❌ 不动 MarketEvent/Event 逻辑 |

### 3.2 Schema 变更

**新增节点：**
| Label | Key | 核心属性 | 约束 |
|---|---|---|---|
| `TradeAction` | `id` | `source:'ts_top_inst'`, `side:0/1`, `amount`, `knowledge_time`, `updated_at` | `TradeAction(id)` UNIQUE |
| `Location` | `key`(city_kind_name) | `kind: province/city/seat/virtual`, `province`, `city`, `parent` | `Location(key)` UNIQUE |

**新增边：**
| 边 | 方向 | 核心属性 |
|---|---|---|
| `EXECUTED_BY` | TradeAction → Seat | — |
| `TARGETS` | TradeAction → Security | — |
| `OCCUPIED_BY` | TradeAction → HotMoney | `confidence`（经 OCCUPIES 投影） |
| `LOCATED_AT` | TradeAction → Location | — |
| `SEAT_LOCATED_AT` | Seat → Location | `validStart` |
| `HAS_TRADED` | TradeDate → TradeAction | — |

### 3.3 关键实现模块

1. `_seat_locator.py`：`Seat.name → {province, city, kind}|None`
   - 命中率目标 ≥ 82%（与探查一致）
   - 未命中行返回 None（不炸），虚拟席位（沪股通/机构专用/总部）标 kind='virtual'
2. `kg_schema.py`：注册 TRADE_ACTION / LOCATION 节点 + 六边类型 + UNIQUE 约束
3. `kg_build_cl1.py` 重构：旧 TRADED_ON 边 + 新 TradeAction 双写
   - 逐原始行建 `ta:{exalter_hash}:{ts_code}:{trade_date}:{side}`
4. `OCCUPIED_BY` 投影：遍历 HotMoney→OCCUPIES→Seat，把该 Seat 的 TradeAction 直挂

### 3.4 验收查询（3 条必跑通）

```cypher
-- Q1 跨省时空：上海游资 2026-10-14 净买了江苏标的
MATCH (ta:TradeAction {side:0})-[:ON_DATE]->(td:TradeDate {date:'2026-10-14'})
MATCH (ta)-[:EXECUTED_BY]->(st:Seat)-[:SEAT_LOCATED_AT]->(loc:Location {province:'上海'})
MATCH (ta)-[:TARGETS]->(sec:Security)-[:AREA_OF]->(r:Region {name:'江苏省'})
RETURN st.name, sec.ts_code, ta.amount

-- Q2 方向偏好：某游资当天只卖不买
MATCH (hm:HotMoney)-[:OCCUPIED_BY]->(s:TradeAction {side:1})-[:ON_DATE]->($d)
WHERE NOT EXISTS { MATCH (hm)-[:OCCUPIED_BY]->(b:TradeAction {side:0})-[:ON_DATE]->($d) }
RETURN DISTINCT s

-- Q3 多省聚集：同标的 5 日被 3+ 省席位盯上
MATCH (sec:Security)<-[:TARGETS]-(ta:TradeAction)-[:ON_DATE]->(td:TradeDate)
WITH sec, td, collect(DISTINCT loc.province) AS ps, count(DISTINCT ta) AS c
WHERE size(ps) >= 3 AND c >= 5
RETURN sec
```

### 3.5 验收标准

- `python kg_schema.py --check` 通过
- TradeAction 节点数 ≈ 431,892（与原始行一致）
- OCCUPIED_BY 覆盖：HotMoney 命中席位对应的 TradeAction 均有直挂边
- Q1/Q2/Q3 均返回非空、量级合理
- 旧 TRADED_ON 边仍存在（双写生效），前端 `/kg/` 现有查询不回归

---

## 4. Phase 2：组织关系事件化（K-SPATIAL-02）

- `HOLDS` → `HoldingEvent {ratio, shares, knowledge_time}`：共享 TradeDate 锚点，支持"2026Q3 谁同时买联亚药业和 A"聚合
- `SERVES_AS` 方向验证 + validStart 严格维护
- 补 Company 内部组织链（Person→Company→Org，POLE+O 之 Org）

验收：HoldingEvent 可被 `TradeDate` 聚合查询；SERVES_AS 无覆盖丢历史。

---

## 5. Phase 3：双时态全量增强（K-SPATIAL-03）

- 所有时序边 + Event 类节点补 `knowledge_time`（Event/Knowledge Time 分离，防前视偏差）
- 回测/推演管线增加 Knowledge Time 过滤器：只看 `knowledge_time < 推演时点`
- 判例：联亚药业公告 10-14 发布、10-20 入库 → 10-14~10-19 回测不得"知晓"

验收：构造前视样本，回测结果不含 knowledge_time > 模拟时点 的记录。

---

## 6. Phase 4：INVOLVES 方向统一 + 清理（K-SPATIAL-04）

- 统一 `Event → Security`（消灭 ontology 声明与写入方向不一致）
- 涉及 `kg_build_p3_evt.py`、`kg_portal.py canvas_timeline()` 方向反转
- 待 P1 上线稳 2 周后清理旧 TRADED_ON 边

验收：全量查询兼容回归 56 passed；方向一致性用例通过。

---

## 7. 风险与注意事项

1. **空值稳健**：Seat 无城市返回弱降级 kind='virtual'，不得报错阻断全量构建
2. **幂等**：`TradeAction(id)` UNIQUE 保证重复跑不重复建；双写须先 MERGE 再 SET
3. **性能**：431,892 节点 + 投影边用 UNWIND + MERGE 批量，勿逐条往返
4. **不干预并行会话**：若 Seat/ts_top_inst 相关文件正被并行会话修改，剔除待收口
5. **双写过渡**：旧 TRADED_ON 边在 P1-P3 保留，避免现有前端查询回归

---

## 8. 资源与文件清单

| 文件 | 动作 | 阶段 |
|---|---|---|
| `quant_backtest/kg_schema.py` | 新增节点/边类型+约束 | P1 |
| `quant_backtest/_seat_locator.py` | 新建（空间提取） | P1 |
| `quant_backtest/kg_build_cl1.py` | 重构（双写 TradeAction） | P1 |
| `quant_backtest/test_spatial_kg.py` | 新建（单测） | P1 |
| 验收查询 Q1/Q2/Q3 | 落地为测试脚本 | P1 |
| HOLDS/时序/INVOLVES 相关 | 后续阶段 | P2-P4 |

---

## 9. 打板确认项

拍板前请确认以下口径：
1. P1 切片边界是否符合预期？（先在时空 TradeAction 打底）
2. 旧 TRADED_ON 边双写过渡你接受保留多久？（建议 ≥2 周）
3. TradeAction 粒度：按 `(seat, ts_code, date, side)` 一条节点（431,892），而非按日均聚合——确认是否接受此"最小颗粒"成本与收益