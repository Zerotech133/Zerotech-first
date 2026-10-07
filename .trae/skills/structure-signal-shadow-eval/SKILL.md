---
name: structure-signal-shadow-eval
description: 结构类量化信号（缠论三类买卖点等）新增过滤规则或评估接入推送前的标准流程——可成交价确认滞后重放、影子标注、按日面板对照、达标再启用。触发词：缠论信号评估、背驰过滤、信号接入、影子模式、可成交价复核、样本统计。不适用于一次性看数或纯指标打分策略。
---

# 结构信号影子评估流程（缠论 / 笔段中枢类策略）

在本工作区（E:\老田的AI助手）为结构类信号新增过滤规则、或评估能否接入榜单/卡片推送时，严格按本流程执行。硬约束：**任何新过滤先影子（只标注不拦截），可成交口径达标后才允许进入执行链路。**

## 第一步：识破「理论价 win=100%」假象

结构信号的确认依赖后续笔/段形成，信号点（极值价）在当下不可知。直接以理论价为基准算胜率必然虚高（1买 27 样本理论口径 win 100% / avg +25%）。评估前必须做确认滞后重放：

1. 对每条样本 `(type, dt, price)`，找到其在 K 线中的索引 i0
2. 逐根截短重放 `analyze(bars[:i0+1+m])`（m=1..60），找信号**最早可检出**的 lag
3. 真正可成交基准 = 检测窗末根收盘价 `bars[i0+lag]["c"]`，按方向收益口径（买点涨为正、卖点跌为负取反）重算 r20/r60
4. 同时记录确认时相对理论价的偏离（1买实测中位 +18%、均值 +23.6%——大部分涨幅在可执行前已发生）

现成实现：`quant_backtest/chanlun_backtest.py` 的 `eval_samples(rows)`（PASS_N=10 达标线常量，勿擅自改阈值，改阈值需用户确认）。

## 第二步：达标线与结论口径

- 达标 = 可成交口径 n≥10 且方向均值不反转（买点 avg>0、卖点 avg<0 侧）
- 达不到就如实报告并冻结推送，不得为了接入而放宽线
- 样本全部单日入库 ≠ 跨时间样本；r60 窗口未成熟的子集单独标 n，不拿 r20 替代
- 重放检不出的样本（如 2买）单独计数，提示在线/离线不一致风险，不静默丢弃

## 第三步：新过滤规则落地（影子模式）

1. 规则用独立函数承载（如 `chanlun_signal._panzheng_beichi(leave, pull)`），阈值提为模块常量（如 `BEICHI_RATIO=1.0`），力度口径与既有信号层保持一致（笔端点绝对价差）
2. 信号字典加布尔标注字段（`bei`）+ 证据字段（`fv`），**绝不删除/拦截原信号**
3. 在 `eval_samples` 重放命中处捕获标注，额外统计「过滤通过子集」的 exe/exe60，与全集并排输出
4. 面板新增过滤列（✓标注数/检出数 + 子集 n/win/avg），图表圆标加 ✓/✗，图例注明「影子标注·不拦截」
5. 模块 docstring 与门户页面「当前状态」段口径双向同步

口径踩坑（P6-b）：做「进入段 vs 回踩段」类同向力度比较时，不要取中枢紧前一笔、也不要只在中枢首笔之前回溯——大量 3 类信号由数据窗口的**首个中枢**产生（首中枢无 entry，或紧前笔方向相反），会导致 bei 大面积 None。可行口径：在离开笔之前的整段笔序列里回溯最近一根与回踩笔同向的笔作前序腿（bis[:k] backward scan），全部信号才可判定。两个相近口径先并行影子（P5 bei 与 P6-b bei2），让面板数据择优，不要用新口径推翻旧口径。

多级别共振（P7）：用次周期（60m）信号给日线信号加标注时，判定基于「信号终结笔窗口内次周期是否出现同向 1类+MACD 背驰」，次周期 analyze 复用已有信号层。**次周期数据覆盖窗口才允许判 False；覆盖不到（US/HK 分钟存量短、样本早于采集起点）必须 None，面板分母单列「覆盖数」，绝不把无数据当无共振**。样本库里的历史行用 `_backfill_mt` 式回填（只填缺、覆盖才写），随每日任务自然积累。

资金/大盘共振（P8，chanlun_flow.py）：量能标注（vv）只依赖 bars+bis+sig，放进 `signal.analyze` 随截断重放自然可得，eval 在重放命中处直接取 hit.vv（不是行字段）；大盘环境（reg）依赖市场外部数据，由样本任务/接口按 market 加载指数后附加，eval 读行字段。指数代理 CN=000001.SH、CRYPTO=BTC/USDT；**US/HK 库内无指数/ETF，reg 恒 None 不判逆风**。另注意真实存量：上证日线仅自 2025-07（~304 根）、BTC 日线仅 ~105 根（load_kline limit 给 500 也只回 105），早于 MA60 前置窗口的信号一律 None——回填后先抽查 None 是否符合采集起点，别急着怀疑函数。量能口径：笔内 K 线均量（bar.v 缺失全 0 → None），信号终结笔 vs 上一同向笔，缩量=True（VOL_RATIO=1.0）。顺风：买 up（收>MA20>MA60）/卖 down，flat 明确不顺风但可展示。

趋势背驰完整版（P6-c，chanlun_trend.py，2026-09-26）：教学 a+A+b+B+c——信号前 ≥2 个同向不重叠中枢。关键踩坑三条：①趋势方向由两中枢**位置关系**定（下跌 B.zg<A.zd、上涨 B.zd>A.zg），不要用「中枢离开笔方向」——find_pivots 下中枢可先被反向假离开再被新中枢越过，加离开笔方向检查会把真趋势全部误杀（实测 ETH/BTC 60m 零 True）；②find_pivots 按「笔区间与中枢区间是否重叠」延伸，一根**贯穿中枢的大离开笔**（高低点跨区间）会被记成中枢末笔，所以 b 段搜索窗必须从 A 的末笔 ai1 开始（[ai1, min(bi0+2,bi1)] 内取最强趋势同向笔），从 ai1+1 起会漏掉真正的推动腿导致 b 偏小、背驰永不成立；③c 允许就是 B 的离开笔本身（i1+1==k），中枢筛选条件用 i1<k 而非 i1+1<k。判据还要信号价在 B 趋势侧之外（防折返）；中枢<2 记 None（日线存量短，绝大多数 1 类信号 None 是正常的），结构可见但不满足记 False。字段 tbe/tfv（tfv={np,A,B,fb,fc}），eval 子集 flt7 在重放命中取 hit.tbe；样本行存 tbe + `_backfill_trend` 回填。查看器口径对齐：structure 接口默认 limit 必须与样本/eval 的 500 一致（原默认 300 会截断中枢链导致在线 tbe=None、离线 True 的不一致；前端不传 limit）。ZG/ZD 区间沿是严格 GG/DD 的简化，须在页面/docstring 如实声明。

## 第四步：按日自动跟踪与启用门槛

- 样本任务 ChanlunSample（每日 06:00 +08:00，Administrator/Interactive）跑积累后自动重算评估，原子写 `quant_backtest/data/chanlun_eval_cache.json`
- 门户 /bt-chanlun 只读缓存渲染，页面内不做重放计算（60 次 analyze/样本，太重）
- 启用拦截门槛：过滤子集 n≥10 且可成交口径显著优于全集；达到后向用户提方案，确认后才把标注转成过滤
- 改完代码重启门户必须先清全部 portal_api.py python 进程（Stop 命令杀不死 Start-Process 子进程），用 `portal/start_portal_api.ps1`，末尾 Wait-Process 会阻塞需后台运行

## 第五步：全市场扫描 + 预警池 + 模拟交易（chanlun_scan.py，2026-09-26）

- 架构：每日 07:10 ChanlunScan 任务跑 `--scan`（A 股 5593 只 parquet，analyze 1.3ms/只）；检出日收盘=可成交入场，HOLD_DAYS=20 反向信号可提前离场；入池 data/chanlun_pool.jsonl、账本 chanlun_paper.json、战绩 chanlun_pool_eval_cache.json，门户 /bt-chanlun-pool 只读渲染
- **「今日新检出」语义（核心踩坑）**：每日增量扫描绝不能把 500 根窗内 analyze 出的**全部**信号记成今日检出——回填只覆盖 120 根窗，首跑 scan 会把窗外历史信号全部以今日收盘虚假入场（实测污染 +29220 条）。正确口径：新检出 = analyze(bars) 可见而 analyze(bars[:-1]) 不可见（每票双 analyze 差分），与回填重放的最早可检出口径无缝衔接；修复后必须**清空 pool/paper/eval 三文件重建**
- 幂等三层：pool 按 (code,type,sig_dt) 去重；账本 _open_positions 对 open+closed 同键去重；closed 截留 5000 条。参数：BACKFILL_DAYS=120/MAX_LAG=60/HOLD_DAYS=20/MIN_BARS=120/LIMIT_BARS=500
- 除权处理：entry→exit 间 pre_close 与前收盘缺口 >0.5% 判除权，仓位标 adj_flag 且不计入 pool_eval
- 计划任务本机枚举坑：`New-ScheduledTaskPrincipal` 有效值是 `-LogonType Interactive -RunLevel Highest`（不是 InteractiveToken/HighestAvailable——前者报无效枚举、后者 0x8007052f 拒注册）；对齐 ChanlunSample 现役形态
- 侧面战绩列（mbei/bei/bei2/vv/reg/tbe）与 eval_samples 的 flt 口径一一对应，n<10 灰显不构成结论
- 1卖·趋✓ 已升级「强预警标注」（2026-09-26，复评 n=79 胜60.8% avg+4.94% 优于全集 +2.31%）：仅展示层——池页今日强预警卡+🚨徽标+口径⑥，不推送不拦截；左尾声明：5 条信号后反涨30%+（轧空无保护）；卖点 ret>0=下跌=预警成功，解读别反

## 环境铁律

- 临时评估脚本命名 `_t_*.py` 放 quant_backtest 下，跑完立即删；数据文件写入用 tempfile + os.replace 原子写
- PowerShell 5.1 命令行禁中文（会变 `?` 通配符），python 脚本用 cwd=E:\老田的AI助手 + 相对路径跑，`&&` 不支持用 `;`
- 验证：`python -m py_compile` 三文件 → 跑一轮 `python chanlun_sample.py` → 重启后用 urllib 轮询页面关键字 + 调 `/api/chanlun/structure` 回归
- 评估数值展示注意单位：小数（0.0755）与百分数（7.55）不要二次 ×100，曾因此把 +23.57% 显示成 +2357%
