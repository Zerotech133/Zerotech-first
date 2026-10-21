# CnOpenData 数据源 · 运行手册

> 2026-10-21｜维护者须知。这一天的坑都在这里，照着做不会重犯。
> 配套：`SCHEMA.md`（档案字段契约）、`接入路线_v1.md`（选源判断）、`S0验收报告.md`、`FEPU因子检验报告.md`

## 一、这套东西是什么

| 层 | 位置 | 说明 |
|---|---|---|
| 档案 | `data_sources/cnopendata/registry.json` | 408 条数据源元信息（**不含数据内容**） |
| 原件 | `data_sources/cnopendata/open/<id>/raw/` | 下载的原始文件 + `meta.json`（sha256/来源/许可） |
| 产物 | `quant_backtest/data/cnopendata/<table>/part_NNNN.parquet` | 解析后入湖，数据根按仓库约定 |
| 清单 | `quant_backtest/data/cnopendata/_manifest.json` | 表级行数、来源、许可、解析备注 |
| 目录 | `quant_backtest/data/catalog.json` | 已注册 6 张表，`source=cnopendata` |

**硬边界**：`data_sources/` 整棵树**未纳入 git**（`git ls-files` = 0）。代码与报告改动必须同步到 `deliverables/数据源档案_CnOpenData_20261021/`，否则被覆盖即丢。

## 二、日常操作

```bash
cd C:/项目/老田的AI助手/data_sources/cnopendata

# 1) 解析原件 -> parquet（幂等，可反复跑）
C:/Program Files/Python311/python.exe ingest_open.py

# 2) 只看会发生什么，不落盘
C:/Program Files/Python311/python.exe ingest_open.py --dry-run

# 3) 只跑一张表
C:/Program Files/Python311/python.exe ingest_open.py --only cnod-czyx

# 4) 重建档案索引（改了 registry 分片后）
C:/Program Files/Python311/python.exe build_registry.py && C:/Program Files/Python311/python.exe build_html.py

# 5) 增量刷新：扫分类页与档案对账，出漂移报告
C:/Program Files/Python311/python.exe refresh_registry.py           # 只出报告
C:/Program Files/Python311/python.exe refresh_registry.py --merge   # 同时把新条目写成分片（再跑一次 build_registry.py 合并）

# 6) 重建数据目录（改了 build_catalog.py 后）
cd C:/项目/老田的AI助手/quant_backtest && C:/Program Files/Python311/python.exe build_catalog.py

# 7) 校验：链接能开（状态码）+ 页面内容对（标题比对）+ 页面渲染正确
C:/Program Files/Python311/python.exe check_links.py --all
C:/Program Files/Python311/python.exe check_titles.py --all      # 全量约 8 分钟
C:/Program Files/Python311/python.exe check_titles.py --self-test  # 变异自检，改判据后必跑
C:/Users/t/.workbuddy/binaries/node/versions/22.22.2-6/node.exe render_check.cjs 档案总览.html

# 8) 一条命令跑完全部离线验收（改任何脚本后都该跑）
C:/Program Files/Python311/python.exe verify_all.py            # 编译/幂等/不变量/渲染/报告
C:/Program Files/Python311/python.exe verify_all.py --network  # 另跑判据自检

# 9) 记录可信度审计（联网）
C:/Program Files/Python311/python.exe alias_report.py    # 同名重复分档 -> 核对清单
C:/Program Files/Python311/python.exe alias_probe.py     # 去页面原文判哪条写错
C:/Program Files/Python311/python.exe coverage_audit.py  # 全部「至今」记载 vs 页面止期
```

**解释器**：必须用系统 Python 3.11（`C:/Program Files/Python311/python.exe`）。托管 3.13 无 pandas/openpyxl/psycopg2，导入即失败。

## 三、八个必踩的坑（都实测过）

1. **多工作表 Excel 只取首表 = 0 词入库**
   词典 xlsx 有「说明/negative/positive」三张表，只取第 1 张会把 9,228 个词全丢掉、只入 2 行说明文字。现按表逐张落 parquet，并跳过「说明」类页。

2. **词条带换行符**
   词典每个词末尾有 `\n`（`败坏名声\n`），不 `strip` 则整本词典匹配不上任何文本。

3. **EXCEL 多表加列要白名单**
   `dim` 列只对 `DIM_TABLES = {"cnod-czyx"}` 生效。否则词典表下次全量重跑会静默多一列（schema 2→3）。

4. **主键声明会静默去重**
   `czyx` 曾填 `["prov","year"]`，datamart 按主键去重把 **1,995 行塌成 879 行**，不报错。现用 `(prov,year,dim)`，且末片无键走 `~complete` 分支保留全部 135 行。**任何主键改动后必须验行数**。

5. **同一份数据两套省名**
   片 0000 用简称（上海）、片 0001-0004 用全称（上海市），按原列 join 得 **0 行**。改用 `prov_std` 后 **372 行全中**。**跨表按省份关联一律用 `prov_std`**。

6. **截断文件伪装成 zip**
   `lit_panel.zip` 实际 445MB，下载断在 8.2MB，文件头是 `PK` 但无中央目录。**只验文件头会误判成功**，必须查 `PK\x05\x06`。

7. **Git Bash 的 `/tmp` 与 Windows Python 不互通**
   `cp x /tmp/y` 后 Windows 上的 Python 读不到。快照一律放仓库内相对路径。

8. **测试要在仓库根跑**
   `pytest quant_backtest/xxx.py`（在仓库根）→ 正常；`cd quant_backtest && pytest xxx.py` → 多进程用例 `ModuleNotFoundError: quant_backtest` 假失败，会误判成代码缺陷。

## 五、增量刷新怎么读（`refresh_registry.py`）

- 扫描范围**从档案自身反推**：取每条详情 URL 的 `/data/m/<leaf>/` 段去重，档案扩类目后刷新范围自动跟上。
- **不静默删记录**：页面上没再出现的只标 `not_seen` 并写日期，由人判断是下架还是改版。
- 新增条目走**分片**（`_fragments/frag_refresh_<date>.json`）再合并，不直改 `registry.json`——直改会在下次 build 时被覆盖。
- **已知误报**（已修，保留说明防回退）：
  - API 商店折叠区不渲染成 `<a href>`，只在 RSC 数据对象里（`{"slug":…,"name":…}`）。早期只匹配相对 href 会漏 5 个，现改为**先反转义 `\"`→`"` 再匹配产品对象**，29 个接口全数命中。
  - 同一数据集常在多个类目重复挂载（如碳交易数据同挂 ESG 与 regional）。报表现在把它们单列为 `跨类目重复`，**不再混进"新增"**——否则每次刷新都报"新增 120"，人会逐渐无视报告。
- 当前稳定输出：`真新增 0 / 跨类目重复 120 / 未见 0`。**未见不为 0 才需要人看。**
- **链接健康**：`check_links.py`（默认只查自动收录的未分诊条目，`--all` 全量）。只探状态码，不下载内容。`/data/m/` 下不存在的路径确实返回 404（title 为 `404 | CnOpenData`），因此对 404 类错误有判别力；但 200 只证「可访问」，**不证「内容对」**——所以要配合下面的标题校验。
- **内容一致性**：`check_titles.py`（`--all` / `--limit N` / `--self-test` / `--calibrate`）。
  - 判据是**归一化后精确相等**（详情页 `title` 形如「数据集名 | CnOpenData」），不是相似度过阈值。
  - 为什么不用阈值：档案里名称最相近的一对是「中国上市公司股票复权行情数据」vs「…未复权行情数据」，相似度 **0.966**，任何阈值都挡不住这种错配，而它俩互换在量化上是致命的。实测 30/30 条精确命中，所以「不等即报」既灵敏又不误报。
  - 输出分三档：`exact` 对上 / `near`(≥0.80) 疑似兄弟数据集链错 / `mismatch` 指向不相干页面。
  - `--self-test` 用**最相近的一对**做变异（不是随便挑两条），证明判别力真实存在。
- **同名重复记录**（重要口径）：上游把同一数据集挂在多个类目下，两条 URL 都有效、内容同一份，但 id 带类目前缀而不同，按 id 去重抓不住。`build_registry.py` 的 `mark_aliases()` 会保留两条并给非正本打 `alias_of`（**不删除**，删记录要人判断）。
  - 正本规则：已分诊 > 优先级高 > 有 notes > id 字典序。刻意**不比描述长度**——描述随再抓取变长变短，正本会翻转，外部按 id 引用的地方就漂了。
  - 统计口径：`total_records` 506 条 ≠ `unique_datasets` 486 个。做覆盖统计请用后者。
- **去重优先级**：已分诊记录 > 自动收录记录（`status=new_untriaged`）。曾用"描述更长者胜"，结果自动记录的长描述把 P0 从 17 条压到 10 条，已改为显式比权重。
- **「至今」是新奇度陷阱（实测）**：`coverage_audit.py` 核了全部 38 条写「至今」的记载，**12 条与页面矛盾**（对应 **7 个数据集**，另 5 条是别名副本）：
  页面给了明确止期，档案却写"至今"。最狠的一条止于 2021.12（差 58 个月），从业人员系列止于 2023.5.30（差 41 个月）。
  这类错误链接照样 200、标题照样对，只会在选票日让你把一份停更两年的数据当成实时的用。
  - 数「有多少份数据停更」请用 `by_freshness_canonical`（7），别用 `by_freshness`（12 条记录）。
  - 分档按"能不能追新"：页面自己写至今/实时 → 一致；止期 6 个月内 → 一致；7~18 个月 → 轻微滞后；>18 个月 → 实质高估。
  - **别用"页面有没有止期"当判据**：`1990.12~2026.6` 明明是今年 6 月，说"至今"没错，按有无止期判会把大量正常记录误报。
  - **只写年份时的口径**：4 条页面只给到年（`2000~2024`、`1998~2022`、`2004-2025年`、`2014.05~2025`），缺月按**年末**算，属偏宽松。
    若改按年初算，其中 2 条会从 lag 跨过阈值变 stale。判读这几个时应留一点余量。
  - **审计对象是合并后的 coverage**：2 条记录某个分片写过「至今」但合并值不含（`cnod-chinese-listed-company-stocks-comment`、`cnod-ssgsjyyb`），故没进审计。两条都不像停更。
  - 集中度：`cnod-b` 一片 12 条可疑，属抄录习惯问题，不是个案笔误。
  - **处置方式：叠加证据层，不改原记载**。`build_registry.apply_freshness_overlay()` 把 `_coverage_audit.json` 的页面原文挂成 `coverage_page` + `freshness_flag`，原 `coverage` 一字不动。
    理由是原记载是采集席抄录的事实来源，改掉就丢了溯源；两份并存才看得出"档案说至今、页面说止于 2023"。
  - **闸门怎么验（很容易验错）**：直接改 `registry.json` 再跑 `verify_all.py` **测不出来**——它会先跑两遍 `build_registry.py` 从分片重建，篡改在被检查前就被覆盖。要真验必须**改源头分片**或单独调 `check_invariants()`。
  - **证据文件有哈希锁** `_coverage_audit.lock.json`（条数 + sha256）：证据被删/被改都会打红。否则证据没了，所有不变量真空成立、检查全绿。手工改过证据必须 `coverage_audit.py --lock-only` 重新加锁。
  - 页面只对 `stale`（>18 个月）打「至今存疑」徽标，`lag` 属正常发布延迟不刷屏——否则 24 条全标等于没标。
- **页面会自己打架（全量已量化）**：`freq_audit.py` 核了 506 条的 `更新频率` × `时间区间`，结果分四档：

  | 分档 | 条数 | 含义 |
  |---|---|---|
  | 频率低频但区间标实时 | **6**（全 P0） | 页面写「更新频率：年度」，同一页「时间区间」却标「（实时更新）」，**页面自相矛盾** |
  | 档案与页面频率不一致 | 1 | ETF 基础信息：档案「不定期」、页面「按需」 |
  | 档案未填可补齐 | 96 | 档案「未标注」、页面有值——是可补齐，不是故障 |
  | 抽取可疑 | 0 | 抽取错位，需重抓（本轮已修到 0） |

  那 6 条正是追新最依赖的：A股股吧文本、A股行情、港股/美股雪球发帖、雪球关注与实盘、雪球发帖实时。
  档案忠实抄了「年度」，**抄录没错**。取证结论：页面这两个口径多指**不同交付版本**——批量历史库按年度滚动更新，
  实时/增量通道单独售卖（如股吧 API 每 30 分钟增量抓取），不是互斥事实，故定性为「**口径待确认**」而非「矛盾」。
  排期应**分两档**：批量库按年度、实时需求走 API；不能一律按年度，也不能一律改日频。
  - 判据坑：频率词表含单字「日」，正文里任何「日」都能让整段说明文通过校验，实测把「最高可达每2分钟一次…」当频率。现要求**以频率词开头且 ≤10 字**。
  - 分档坑：首版把「档案未标注、页面有值」也算矛盾，102 条里 96 条是噪音，把 6 条真问题淹了。**机会与故障必须分开算。**
  - **已挂成证据**：`apply_freq_overlay()` 把 103 条（6 待确认 + 1 不一致 + 96 可补齐）的页面原文挂成 `freq_page`/`freq_conflict`/`freq_note`，`frequency` 一字不动。页面只对那 6 条打「频率口径待确认」徽标，96 条可补齐不标——标了就把真问题淹了。
  - 口径坑：旧判据「没抽到页面频率 → 当一致」把 74 条**无证据**记录算成了"与档案一致"，导致"核对 506 条"虚高（真实一致 329 条）、`抽取可疑需重抓` 恒为 0 成死档。现单列「未取到证据需重抓」一档，只进审计 JSON 与报告，不进 registry（没有页面原文可挂）。
  - 落盘顺序坑：`_freq_audit.json` 原先在**分类之前**写盘，结果 506 条 `conflict` 全空，而报告 md 显示 6 条矛盾——同一份审计两处口径打架。现改为分类完成后再写盘并加锁。
- **核对清单**：`alias_report.py` → `同名数据集核对清单.md`（分 B 内容冲突 / C 仅分诊不一 / A 完全一致三档）；
  `alias_probe.py` → `同名数据集页面核对.md`（去页面原文逐字段比对，判哪条写错）。


## 六、改动后的验收清单

```
□ 六表行数守恒：1995 / 744 / 651 / 57044 / 81759 / 9228
□ datamart.ready() 六表全 True
□ catalog 表数 244，除目标表外无字段变化（改前改后快照做 diff）
□ 若动了主键：必须实测 read() 行数，防静默塌行
□ 若动了归一列：必须真跑一次跨表 join，不能只看 nunique
□ 提交只 add 目标文件（pathspec），不整树 add、不 stash 他人改动
□ 改了 build_registry.py：`total_records` 与上次差 = 分片净增数，`unique_datasets`+`alias_records` = `total_records`
□ 改了 build_registry.py：连跑两次构建，除 `generated_at` 外输出逐字节一致（幂等）
□ 改了 build_html.py：跑 `render_check.cjs`，并**故意摘掉新代码重跑一次**确认转红（变异测试）
□ 改了 check_titles.py：先跑 `--self-test`，它用的是档案里最相近的一对，不是随便两条
□ 改任何脚本后：跑 `verify_all.py`，五项全绿再算完成
□ 动过抽取/判定逻辑：故意造一份反例数据走一遍失败分支（只测"全绿"分支等于没测）
□ 长跑脚本：输出重定向落文件，别用 `| tail -N`（全缓冲，过程不可见）；结束必须核退出码 + 产物 mtime
□ 改过证据层：`_coverage_audit.json` 与 `_coverage_audit.lock.json` 必须成对且 sha 对得上（手改过就跑 `--lock-only` 重新加锁）
□ 报「条目数」与「数据集数」时必须说清是哪一个，别把 12 条当成 12 份数据
```

## 五、当前已知局限（不是 bug，别当故障修）

| 现象 | 原因 | 处理 |
|---|---|---|
| 5 张年度表常驻 `stale` | freshness 是 2014–2024，超 400 天预算 | **保持可见**，它们就是终态历史包；藏起来等于重演"停摆无人发现" |
| `czyx` 末片 135 行 `prov_std` 为空 | 该片是无省份列的宽表 | **不填占位值**，填了就是伪维度 |
| 词典表 `verification_status=static_no_date` | 无时间维 | 正常态，告警映射为 ok |
| FEPU 无预测力 | 19 年 IC t=−0.51，且含前视偏差 | 结论已定：不建因子管线，仅作控制变量 |
| 10 条数据取不到 | 只能注册 CnOpenData 账号 | 待老田决定，未擅自注册 |
