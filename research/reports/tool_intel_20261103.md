# 第二期工具情报库（2026-11-03）

> 来源：3 份 clean 文本（3 文件）+ 二期 units（1116 条）· **工具 45 个** · 纯规则+人工筛选（不调模型/不联网）
> 类别分布：{"行情": 12, "绘图": 2, "Agent": 17, "研报": 3, "量化": 7, "回测": 4} · 置信：{"高": 16, "中": 29}
> **营销话术已剔除**（词表/命中见 §四）；工具情报不构成推荐。

## 一、工具清单

| # | 工具 | 类别 | 一句话描述 | 关键能力 | 地址 | 来源数/提及 | 置信 |
|---|---|---|---|---|---|---|---|
| 1 | **TradingView** | 行情 | 项目叫TradingView MCP已经3.8K Star。 | 行情图表、指标、Pine脚本、图表 | tradingview.com | 1/37 | 高 |
| 2 | **Pine Script** | 绘图 | 这个开源项目把 TradingView 的指标搜索、Pine Script 源码获取和策略采集接入了 AI Agent。 | 指标脚本、策略、指标、Agent | tradingview.com/pine-script-docs | 1/26 | 高 |
| 3 | **MCP** | Agent | 热点舆情全自动更新，不用等静态结果，支持DeepSick Gemini Teami等模型，策略自由组合，还有智能记忆系统加MCP工具扩展。 | 协议、工具接入、数据 | modelcontextprotocol.io | 1/24 | 高 |
| 4 | **Codex** | Agent | 而且专门做成了Skill，直接把Skill.md放到Cloud Codex或Cursor里就能用。 | 代码、AI编程 | github.com/openai/codex | 1/20 | 中 |
| 5 | **刺猬投研** | 研报 | 接下来，刺猬投研会持续帮大家看懂这些行业、头部公司和产业链背后的长期变量、技术路线怎么变、产业咽喉在哪里、资本最终流向谁。 | AI投研、研究 | — | 1/14 | 中 |
| 6 | **Claude Code** | Agent | 你可以在Claude Code里做到，但你会需要处理那些Crons和所有那些东西，因此我目前没有在Gbrain上运行，但我已经在和我的Hermes项目一起玩。 | AI编程、终端Agent | — | 2/12 | 中 |
| 7 | **通达信** | 行情 | 它通过通达信协议、直连市场数据、覆盖A股、港股、美股和期货、行情K线、资金流、板块轮动与主笔成交，都能直接交给Python。 | 行情、指标公式、选股、数据 | — | 1/10 | 中 |
| 8 | **yfinance** | 行情 | 我们叫它们Apple，就是YF,Yfinance library，我们可以下载它们。 | 行情数据、Python、数据 | github.com/ranaroussi/yfinance | 1/7 | 高 |
| 9 | **ChatGPT** | Agent | 初期的ChatGPT。 | 对话、LLM | chatgpt.com | 2/5 | 中 |
| 10 | **FinRL** | 量化 | FinRL Trading，也就是FinRLXIS，正要补上这条断链，一套AI原生量化交易基础设施。 | 强化学习、交易 | github.com/AI4Finance-Foundation/FinRL | 1/5 | 中 |
| 11 | **Instock** | 行情 | 这个项目叫Instock Me Hubstock，已经13.4K Stock。 | A股数据、指标、选股、回测 | github.com | 1/5 | 高 |
| 12 | **TradingView MCP** | Agent | 项目叫TradingView MCP已经3.8K Star。 | MCP、技术分析、AI接入 | github.com | 1/5 | 高 |
| 13 | **Gemini** | Agent | 说白了，就是因为大模型本身不够稳定，人类程序员就得用死代码写一个管理外壳，在外面强行规定死，第一步用GPT搞规划，第二步用Cloud写代码，第三步用Gemini | 对话、LLM | gemini.google.com | 2/4 | 中 |
| 14 | **RDAgent** | Agent | 内置大量SOTA模型，支持监督学习、市场动态建模和强化学习，还集成RDAgent实现AI自动挖因子和优化。 | AI研究、因子挖掘、Agent、因子 | github.com/microsoft/RD-Agent | 1/4 | 中 |
| 15 | **Cursor** | Agent | 而且专门做成了Skill，直接把Skill.md放到Cloud Codex或Cursor里就能用。 | AI编程、IDE | cursor.com | 1/3 | 中 |
| 16 | **Finviz** | 行情 | 这个Python项目叫Finviz Finance，它从Finviz.iz获取股票资料和筛选结果，把网页里的内容整理成后续代码可以使用的数据。 | 筛选、行情、数据 | finviz.com | 1/3 | 高 |
| 17 | **Qlib** | 量化 | 数据进入Qlib、因子、机器学习、组合优化和滚动训练开始工作。 | 量化研究、因子、回测、数据 | github.com/microsoft/qlib | 1/3 | 中 |
| 18 | **东方财富** | 行情 | 它直接给你整了一个A股权家桶，实时抓东方财富数据，赞30家技术指标，划筹码分布，识别61种K线形态，200家维度综合选股，多种策略回测验证，甚至还能自动打新交易 | 行情、研报、资金流、回测 | — | 1/3 | 中 |
| 19 | **雪球** | 行情 | 滚轮缩放、拖拽平移、悬浮查看个股、双击跳转雪球，盘后还能一键生成快照，直接下载、复制或分享。 | 行情、社区 | xueqiu.com | 1/3 | 中 |
| 20 | **飞书** | Agent | 通知再分发到Telegram、企业、微信、丁丁、飞书、Bark或Webhook。 | 协作、推送提醒 | feishu.cn | 1/3 | 中 |
| 21 | **Alpha Vantage** | 行情 | 所以，在Alpha Vantage中，它们基本上是为了下载。 | 行情API、基本面、数据、行情 | alphavantage.co | 1/2 | 中 |
| 22 | **FreeStockDB** | 量化 | FreeStockDB把行情放在本地，让查询和计算围着数据转。 | 本地数据、增量更新、回测数据、数据 | github.com | 1/2 | 中 |
| 23 | **GSQuant** | 量化 | 高盛把25年的量化经验放进了一个Python工具包，它叫GSQuant。 | 衍生品、风险、量化开发 | github.com/goldmansachs/gsquant | 1/2 | 高 |
| 24 | **OpenBB** | 研报 | 前四张先打数据基础，用OpenBB获取行情，用Pandas处理，再把数据存起来。 | 数据终端、研究、基本面、数据 | openbb.co | 1/2 | 中 |
| 25 | **Polygon.io** | 行情 | 所以有另一个公司叫Polygon.io。 | 行情API、数据、行情 | polygon.io | 1/2 | 高 |
| 26 | **QSTrader** | 回测 | 事件驱动回测框架（QSTrader）。 | 回测、事件驱动 | github.com/mhallsmoore/qstrader | 1/2 | 高 |
| 27 | **Vela** | 绘图 | Vela把图表能力接近产品，把界面和数据的选择留给你。 | 金融图表、可视化、可嵌入、数据 | github.com/luxalgo | 1/2 | 中 |
| 28 | **同花顺** | 行情 | 同花顺行情/数据终端。 | 行情、研报、选股、数据 | — | 1/2 | 中 |
| 29 | **Composio** | Agent | 那就是你可以创建一个名字叫Composio的帐号，里面有几百几百几百的帐号。 | Agent、工具集成 | composio.dev | 1/1 | 高 |
| 30 | **DeepSeek** | Agent | 然后是L2级初级分析师阶段，能把好几个步骤连起来，做点浅层的研究报告，调用工具的能力还有限，如DeepSeek、Manos。 | 对话、LLM、代码 | deepseek.com | 1/1 | 高 |
| 31 | **EasyTDX** | 行情 | EasyTDX想把它们重新接成一条线。 | 通达信协议、行情、Python、数据 | github.com | 1/1 | 中 |
| 32 | **Gbrain** | Agent | 然后第五层，我们有更多的常在的脑子软件，比如 Gbrain。 | 知识管理、AI | — | 1/1 | 中 |
| 33 | **GrokBot** | Agent | 叫log grokbot work to click up.Hey, go ahead and create a quick project where I n | Agent、自动化 | — | 1/1 | 中 |
| 34 | **Notion** | Agent | 我认为，再一次，这不是一个问题是我们使用Google Drive，或Notion，或GitHub，或Cloud Plugins。 | 知识管理、协作、数据 | notion.so | 1/1 | 高 |
| 35 | **Obsidian** | Agent | 所以，例如，这是我的Wiki的Obsidian View。 | 知识管理、笔记 | obsidian.md | 1/1 | 中 |
| 36 | **Perplexity** | Agent | 你可以看到我能够连接到一些像是Perplexity、YouTube、其他我不能做的东西。 | AI搜索、研究 | perplexity.ai | 1/1 | 中 |
| 37 | **QuantConnect** | 回测 | 最后是QuantConnect的Lin，覆盖多资产回测与石盘执行。 | 回测、实盘、多资产 | quantconnect.com | 1/1 | 中 |
| 38 | **QuantQuester** | 量化 | 以前学量化得肯英文论文，翻QuantNet论坛，现在quant-wiki.com一个网站全整理了，GitHub开源可供建，内容持续更新，由社区共同维护，还配套Q | 刷题、量化面试 | — | 1/1 | 中 |
| 39 | **QuantWiki** | 研报 | QuantWiki，一个剑桥牛津哈佛量化大佬联手打造的中文量化百科。 | 量化百科、知识库 | quant-wiki.com | 1/1 | 中 |
| 40 | **Riskfolio-Lib** | 量化 | 项目叫Riskfolio Libby，已经4.4K star的Python组合优化。 | 组合优化、风险 | github.com/dcajasn/Riskfolio-Lib | 1/1 | 高 |
| 41 | **StockData** | 行情 | 这个项目叫StockData。 | A股数据、多数据源、数据 | github.com | 1/1 | 高 |
| 42 | **TA4J** | 回测 | 它叫TA4J，专门给Java做技术分析和交易系统研究。 | 技术指标、Java、回测、指标 | github.com/ta4j/ta4j | 1/1 | 高 |
| 43 | **TensorTrade** | 回测 | TensorTrade正在回答这个问题。 | 强化学习、回测 | github.com/tensortrade-org/tensortrade | 1/1 | 中 |
| 44 | **TikFlow Stock Panel** | 量化 | TikFlow Stock Panel，一个自托管的A股量化工作台，基于TikFlow数据。 | 自托管、选股、回测、监控 | — | 1/1 | 中 |
| 45 | **韭菜盘GCP AI** | Agent | 这个叫韭菜盘GCP AI。 | 多Agent、投研桌面、Agent | — | 1/1 | 中 |

## 二、来源文件

- `20261103_刺猬投研.md`（413 行，sha256 `2021d9a94e52…`）
- `20261103_指标说明书.md`（1978 行，sha256 `e24a8eb210b0…`）
- `20261103_爱吃折耳根的Ace.md`（2053 行，sha256 `14ee17544bfb…`）
- 二期 units：`C:\项目\知识库\30_知识单元\视频号学习_第二期_units_draft.jsonl`（1116 条）

## 三、方法与置信口径

- **高**：明确介绍句（叫/是/开源/平台等）+（多来源或地址）
- **中**：明确提及但介绍信息有限
- **低**：仅顺带提及
- 工具词典匹配（名称/别名）+ 定义句抽取 + 域名/行号来源；纯规则+人工筛选，不调模型、不联网

## 四、营销话术剔除说明

> 二期 3 份资料以 AI 工具推广为主（46 有效候选终审 0 通过）；本库仅提取工具事实信息，描述句已剔除营销话术（见 marketing_removed 词表与命中统计），不采信收益承诺/夸张宣传。

- `关注`：命中 24 次（描述中已剔除/不采信）
- `神器`：命中 17 次（描述中已剔除/不采信）
- `评论区`：命中 15 次（描述中已剔除/不采信）
- `点赞`：命中 8 次（描述中已剔除/不采信）
- `兄弟们`：命中 5 次（描述中已剔除/不采信）
- `卧槽`：命中 4 次（描述中已剔除/不采信）
- `暴涨`：命中 3 次（描述中已剔除/不采信）
- `翻倍`：命中 3 次（描述中已剔除/不采信）
- `0元`：命中 3 次（描述中已剔除/不采信）
- `炸裂`：命中 2 次（描述中已剔除/不采信）
- `狠角色`：命中 2 次（描述中已剔除/不采信）
- `躺赚`：命中 1 次（描述中已剔除/不采信）
- `上车`：命中 1 次（描述中已剔除/不采信）
- `家人们`：命中 1 次（描述中已剔除/不采信）
- `暴富`：命中 1 次（描述中已剔除/不采信）
- `必赚`：命中 1 次（描述中已剔除/不采信）
- `稳赚`：命中 1 次（描述中已剔除/不采信）
- `割韭菜`：命中 1 次（描述中已剔除/不采信）
- `王炸`：命中 1 次（描述中已剔除/不采信）
- `吃灰`：命中 1 次（描述中已剔除/不采信）

## 五、可信度说明

- 总体：**中（规则抽取+人工筛选）**
- 工具条目来自文本显式提及（名称/别名+定义句/域名/行号），非联网抓取，不保证工具当前可用性；
- 描述为原文句子清理营销话术后截断，未做事实核验；地址仅当原文出现域名/已知主页时才填；
- 同名/别名已合并；source_lines 记录前 20 处提及；units_mentions 为二期 units 中提及次数；
- 置信为『证据强度』而非工具质量评级；低置信条目建议人工复核后再用。

## 六、消费提示

- 量化/回测/行情类工具可用于军团（司马微/张松/FACTOR1）调研；Agent 类工具仅供参考，不替代审核流程；
- 地址未填者=原文未给域名/地址；低置信条目建议人工复核后再登记；
- 本库为静态 JSON/CSV，后续可扩为页面/API（契约先行）。

> 工具情报为原文结构化整理，不构成工具推荐或投资建议；使用前请自行核验。
