# 第六批A：公开原始材料归档与证据缺口

核查日期：2026-10-07。范围仅为公开官方网页及供应商公开文档；未运行已有同步器，未读取凭据，未修改源码、CLI、E盘文件或任何生产数据。

## 取得结果

本批共21次HTTP获取：17次HTTP 200、1次HTTP 404、3次TLS传输失败。归档18份原始HTTP响应正文（含404错误正文）及21份获取回执。另保存获取脚本、字节复核脚本和汇总 `../evidence/manifest.json`。18份正文的大小与SHA256均已重新计算核对一致。

每份 `*.receipt.json` 保存请求URL、开始/完成UTC时点、实际最终URL、HTTP状态、响应头、原始文件名、字节数、SHA256或失败原因。`manifest.json` 完整汇总所有回执，不用人工摘录代替完整获取记录。Node.js fetch申请 `Accept-Encoding: identity`，成功响应没有Content-Encoding，保存响应正文原始字节；这里的字节不包含TLS/HTTP分块传输报文。证书校验未关闭，没有登录或使用API token。

获取时点来自本机时钟，未取得可信时间戳签名。此次响应头Date比本机完成时点约晚24秒，原值均保留，不把任一值伪装为历史到达或认证时间。页面自带的发布日期/页面生成时间也只属于当前下载内容里的声明。

## 可追溯材料

以下URL均是本批成功获取的最终URL，正文/回执在 `../evidence/`。其余官网首页、地图、交易服务与列表导航页同样在manifest中留存；导航页数量不算额外真实性证据。

| 类别 | 原始正文文件 | 最终URL | 关键内容与适用边界 |
| --- | --- | --- | --- |
| 官方参考 | `sse_2025_annual.raw.html` | https://www.sse.com.cn/disclosure/announcement/general/c/c_20241223_10767108.shtml | 上证公告〔2024〕38号，2025年部分节假日休市通知 |
| 官方参考 | `sse_2026_annual.raw.html` | https://www.sse.com.cn/disclosure/announcement/general/c/c_20251222_10802507.shtml | 上证公告〔2025〕45号，2026年部分节假日休市通知 |
| 官方参考 | `sse_2026_spring.raw.html` | https://www.sse.com.cn/disclosure/announcement/general/c/c_20260205_10808618.shtml | 上证公告〔2026〕5号，春节休市2月15日至23日，24日开市；14/28日为周末休市 |
| 官方参考 | `sse_closed.raw.html` | https://www.sse.com.cn/disclosure/dealinstruc/closed/ | 现行2026年度休市安排，明确若干调休周末仍休市 |
| 导航边界 | `sse_market_calendar.raw.html` | https://www.sse.com.cn/disclosure/dealinstruc/calendar/ | 市场事件日历入口；取得静态HTML不等于取得完整交易日日历 |
| 供应商说明 | `tushare_trade_cal_node.raw.html` | https://tushare.pro/document/2?doc_id=26 | trade_cal默认上交所；exchange/cal_date/is_open/pretrade_date字段，与第五批候选字段相符 |
| 价格口径 | `tushare_daily.raw.html` | https://tushare.pro/document/2?doc_id=27 | daily股票未复权行情；交易日15至16点入库；停牌不提供数据；pre_close是昨收价【除权价】 |
| 指数接口 | `tushare_index_daily.raw.html` | https://tushare.pro/document/2?doc_id=95 | index_daily指数日线；close为收盘点位、pre_close为昨日收盘点；与候选生成路径相符 |
| 复权口径 | `tushare_adjusted.raw.html` | https://tushare.pro/document/2?doc_id=146 | pro_bar动态利用因子；qfq=收盘价×当日因子/最新因子；hfq=收盘价×当日因子；前复权按请求end_date基准，分红再投模式 |
| 因子说明 | `tushare_adj_factor.raw.html` | https://tushare.pro/document/2?doc_id=28 | adj_factor自称由Tushare生产；盘前9:15至9:20入库；只取得说明，没有取得具体证券/修订的因子数据 |
| 供应商自述 | `tushare_introduction.raw.html` | https://tushare.pro/document/1 | 平台介绍与自述数据治理；页面链接沃远数据官网；不构成存量文件执行链或供应商身份的独立证明 |
| 修订线索 | `tushare_changes.raw.html` | https://tushare.pro/document/1?doc_id=9 | 数据上新与功能变更记录；2026-07-06声明daily新增盘后成交字段；无存量300行对应修订ID/前后内容/纠正到达记录 |
| 时间自述 | `tushare_update_schedule.raw.html` | https://tushare.pro/document/1?doc_id=108 | 导航标为数据更新说明，实际标题为关于权限；daily声明15至17点更新，与daily文档15至16点存在范围差异，不可由此推导精确历史available_at |

关键原件SHA256：

- 2025年度通知：`b88e5777d868192c156b85f7514fafcba8638a8bcec0cd80190b2e67769f5fff`。
- 2026年度通知：`4e260b815ce1309175ed994e6490a4ccd51b998a5f92bcdc41cec00fc6823dc7`。
- 2026春节公告：`0e05934820bcd89e88165e7725d14c9b5463ca7ad24b049a9b1aef5fcd44085e`。
- 现行休市安排：`7ea10c16202c4748f0ad5bcdfe6ee07b85b5462589504604b17c9d63865e7d8c`。
- trade_cal说明：`942d0ff91c4d15ab65e0e2c02bffb89732f1f605e667cf61c5af875ef0242461`。
- daily说明：`4cfb933a6fe33de8d5390d7253138cb92731726bf7f9c25f7b3f13e3926e1ab0`。
- index_daily说明：`89a4dea32ad3fc1ff1351fffb3109d4a1ffe99e83a4de115b78b8b0c7acee8bc`。
- 复权口径说明：`b3dd09f69baf164d3941da631e8f3ec6c27529dfddca5e52cf54ab294f09bfbd`。

## 覆盖与审核缺口

两份年度官方通知的年份涵盖目标区间2025-01-02至2026-04-01，取得独立于Tushare日历的官方参考原文，改善了第五批“官方原文完全未取得”的缺口。原文明确的是部分节假日及部分调休周末，并不是覆盖455个自然日的规范JSON。尚未核验常规周末/交易周规则、区间临时休市或变更的完整检索，也未将455天逐日与存量日历独立对照。**official_calendar仍不能填pass，不声称455天完整覆盖已核验。** 当前只核查SSE，不推及SZSE；1991至2027的存量分片完整区间也未核验。

供应商公开文档可解释候选接口，但不能绑定实际300行Parquet与供应商原始响应、具体请求参数、账户、SDK版本或执行日志。daily明确是股票接口，index_daily为指数接口；不能因候选代码相符就认证上证文件实际来源，也不能把混有qfq候选支路的整个ashare目录声明为raw。

ChangeLog是接口/产品更新记录，不是绑定存量文件hash的修订证明。HTTP ETag属于当前文档表示身份，不是行情修订ID。仍缺具体数据修订、原始响应hash、供应商纠正通知/前后内容、持久到达记录与必要签名。复权公式页面也不能替代具体因子快照、计算输入、end_date及算法版本认证。

未生成五批审核门禁的四份“匹配JSON”，避免用人工自填source_id/code/revision与同源数据生成一致文档来替代证据。现有审核状态仍应保持needs_evidence，来源未认证、历史可用未核验、训练目标null、audit_only、production_eligible=false。AI没有批准任何来源。

## 失败记录与访问边界

- 上交所旧猜测入口 `https://www.sse.com.cn/services/tradingservice/tradingcalendar/`：浏览器和Node.js均为404。Node回执与150字节错误正文保留，正文hash为 `d03c9c9c5fef19b7e84125dd79b77167d00e1df5f56da4818992e23e67360bbb`，不把错误页当日历材料。
- .NET HttpClient对官网首页、旧入口、trade_cal文档三次TLS握手失败，错误0x8009030E“安全包中没有可用的凭证”。三份回执中的final_url/status/raw_file/hash均为null，未虚构远端响应或正文。
- 浏览器只读页面观察用于确认入口与可见文档；部分locator/click失败后采用文档支持的domSnapshot取得官网实际导航链接。浏览器观察不是原始字节获取证明，原始字节证明来自独立HTTP获取回执。
- 没有绕过证书警告或关闭TLS校验，没有访问登录/API请求，没有读取本地输出凭据，没有用同源Tushare日历互证升级认证，没有把本次下载倒填为过去可用。

## 可复核命令

在工作区运行 `node work/ai_operator_phase6/evidence/build_manifest.mjs` 会仅重算已保存文件的字节数与SHA256并汇总回执，不联网、不读取生产文件。此次执行结果：21次获取回执、17成功、1 HTTP失败、3传输失败、18份正文，全部归档字节复核一致。
