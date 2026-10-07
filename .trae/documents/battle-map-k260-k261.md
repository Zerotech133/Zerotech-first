# 「作战地图」—— 对话入口 × 独立作战图 × 数据集批量体检

## Context

用户心智模型（现已对齐，非首版）：
- 打开是一个 **AI 对话入口**（复用项目已写好的图谱问答对话框 `/kg/ask`，不重写），和它说话：报一只股票、说一个实体、或「选出的 20 只股票」，「出一个点（实体）」。
- **作战地图是独立的地图**（独立于对话，通过命令/URL 进入，与现有 `/kg/map` 游资地图并列），把点展开成时间轴联动画布。
- 支持 **数据集导入**：比如缠论选股选出的 20 只股票批量导入 → 地图聚合展示、批量体检「有什么问题」。

目标能力：
1. 前置验证：公告前一日收盘形态是否满足缠论三买/量价突破；游资（陈小群）上榜前该股图形是否已具买入条件。
2. 后置验证：资金出逃后是否走出二买/三买、是否背驰。
3. 呈现：事件、缠论买卖点、游资上榜、资金出逃、股价涨跌沿时间轴叠加，图论布局（force/环形）可探索。

核心结论：**数据全部已就绪**，缺的只是「按交易日压平的多图层时序聚合接口」+ 独立作战图前端 + 对话→地图的对接。

## 已确认复用（勿重造）

- **对话组件（复用升级）**：`portal/portal_api.py` `_kg_ask_page()`(L19497) 前端对话框 + `/kg/ask` 路由；后端 `kg_portal.canvas_ask()`(L1613) 调 `/kg/cask/{q}`——实体链接→canvas_expand 子图→Ollama qwen3.5:9b 生成→`_fallback_answer` 兜底。返回 `{ok,answer,sources,model}`。
- **缠论信号** `data/chanlun_pool.jsonl`（db.py SQLite 镜像）：type=1买/1卖/2买/2卖/3买/3卖；bei=P5盘整背驰、mbei=MACD背驰、tbe=趋势背驰、reg=up/down/flat；dt=信号发生日(YYYY-MM-DD)。读 `agent_tools.py pool_rows()`。
- **龙虎榜/资金流** `data/catalog.json`：`ts_top_list`(reason上榜原因/net_amount)、`ts_top_inst`、`ts_moneyflow`(net_mf_amount净流入)；统一读 `datamart.read(表,start,end,codes,fields)`，date_col=trade_date(YYYYMMDD)、code_col=ts_code。
- **公告事件** `data/semantica/events.jsonl`(kind=notice，K260 已回填 bias/tag/conf) + `notice_llm.parquet`；`event_graph.py events_for_stock()`。
- **交易日链** `kg_tradedate.py` 建 TradeDate(NEXT_TRADE_DAY) + ON_DATE 挂日。
- **个股情报聚合** `kg_portal.stock(code,days)`(L115)：structures/candles/topgaps/hotmoney/drive_groups——多图层雏形。
- **独立地图先例** `/kg/map`(L637 游资地图) + 导航 L562-568；前端 `_kg_canvas_v2_page`(L19222) 原生 JS+G6。
- **agent 工具** `agent_tools.py`(L247)：signal_precedents/signal_enrich/events_for_stock，call(name,**kw) 通用入口。
- **数据集来源**：chanlun_pool.jsonl / tdx_sector_member(9183) / ts_ths_member(37万) / us|hk _active.json，均可拉一批股票。

## 关键关联键

`ts_code` 统一个股键；日期归一统一 `YYYYMMDD`（缠论 dt `YYYY-MM-DD`→整、moneyflow/龙虎榜本就整、公告 ts ISO→日）。NaN 归一 None（仿 stock() L193）。

## 实现步骤

### 1. 后端聚合接口 `kg_portal.battle_timeline(code, days=250)`
复用 stock() structures/candles/topgaps/hotmoney/drive + `datamart.read` 补资金流/龙虎榜；逐层按 `YYYYMMDD` 压平：
```json
{"ok":true,"code":"000338.SZ","layers":[
  {"date":"20261001","layer":"缠论|游资|资金|公告|五日线",
   "kind":"3买|mbei|上榜|卖出|notice_bull","label":"3买@0721",
   "dir":"bull|bear|neutral","value":123.45,"meta":{}}],
 "rules":[{"rule":"公告前1日买点|上榜前形态|资金出逃后买点背驰",
   "hits":3,"hit_rate":0.12,"samples":[...]}]}
```
`backbeat_rules` 在 Python 侧实现三条研究规则。`kg_portal.api()` L1684 加 `if kind=="timeline"`。

### 2. 批量体检接口 `kg_portal.battle_batch(codes)`
输入一批股票代码（数据集导入），逐只跑 battle_timeline 摘要，聚合成体检表：
```json
{"ok":true,"n":20,"items":[{"code":..,"name":..,"problems":[{"severity":"high|mid|low","kind":..,"text":..,"date":..}],"pre_event_hits":2,...}]}
```
体检信号源：缠论信号缺失/背驰后未出买点、游资出逃后 N 日走弱、公告利空后破位、资金持续净流出等；severity 分级使前端可用红黄绿标点。

### 3. 数据导入支持
支持两种：读现成池（chanlun_pool/板块成分 传 codes）+ `/kg/battle?codes=...` 手输集合。批量接口自动加载 Security 名称。

### 4. 前端：独立作战图页 `/kg/battle`
`portal_api.py` 新增 `_kg_battle_page()`（参照 `_kg_canvas_v2_page` 原生 JS+G6），导航 L562 加「🗺 作战地图」。能力：URL 输入 code 或 codes 集合 → fetch `/kg/timeline/{code}` 单股时间轴 / `/kg/batch?codes=` 批量。G6 力导向画实体-边子图 + canvas 底部时间轴轨道（拖动、图层开关、布局 force/环形 切换占位）+ 批量节点红黄绿标问题严重度、点节点下钻。

### 5. 对话→地图对接
`/kg/ask` 对话框的交互加「出点」：当用户问实体/股票/选股 → canvas_ask 返回的 sources/实体链接结果里可点击「打开作战图」跳 `/kg/battle?code=...`。复用 `_name_dictionary` 的实体链接输出作为「点」候选。

## 验证

1. `battle_timeline` 与 `stock()` 的 structures/topgaps/日期对账一致；缠论 dt 与 moneyflow 同股同日对齐到同一 YYYYMMDD。
2. 三条研究规则命中率与 `stock()['structures']`/龙虎榜明细人工抽查一致。
3. `battle_batch([...20只])` 返回体检表，severity 分级合理。
4. `/kg/battle` 打开不崩：URL 进单股、codes 批量、时间轴拖动、图层开关、布局切换可用；红黄绿标记正确。
5. `/kg/ask` 对话出「点」→ 点开作战图 链路打通。

## 暂缓（二期，另立项待数据源确认）

- 舆论股吧/贴吧数据采集与词云图（数据源未定）
- 布局算法完整四套（本期 G6 force + 环形/组织切换占位）
- 六度/社群/中心性算法下钻（L2 后续批次）

## 依赖状态

- K260 backfill 运行中（后台 job，~710/1948，~25min 自完），完成后对账。
- 本项目计划：作战地图（K261 L2/L3 落地）。