# 第七批 C：独立日历参考与逐日对照自审报告

冻结接口：`build_calendar_reference(documents, coverage_start, coverage_end)`、`validate_calendar_reference(reference)`、`compare_calendar_reference(reference, calendar_capture)`。

仅修改 C 的实现、测试及本报告；另根据主代理明确指示生成 `reports/calendar-reference-input.json` 供 CLI/实际验收复用。未编辑 A/B/CLI/memory/DDL/E 盘或真实日历文件，未执行同步、交易或训练，未运行全量测试。

## 实现与输出

文档为严格四字段 `kind/url/raw_utf8/sha256`，支持 `week_rule/annual_notice/supplement`。URL 只允许 SSE 无凭据 HTTPS 真域与真实子域；每份完整 UTF-8 原文大小 1 字节至 1 MiB，摘要绑定实际原始字节，拒绝未知字段、错误类型、重复 URL 或重复原文摘要。URL 与摘要为来源声明，不能自行构成下载或签名认证。

使用 BeautifulSoup，正文容器按 `.allZoom`、`article`、`main` 识别，要求唯一。剔除 head/title/nav/header/footer/comment/script/style/noscript/template 和明确隐藏元素。完整遍历可见正文，按块元素划分段落，行内标签保持相邻文本，不会因存在 p 而忽略 p 以外的 div 或裸文本，也不重复读取嵌套正文。

交易周正文必须明确每周一至周五及法定假日/本所公告休市例外。移除这项标准规则后，其他休市条款严格拒绝；额外星期交易规则或具体日期休市/停市/开市规则也拒绝。规则书中一般临时停市权条款不据此制造具体日期，临时例外完整性始终为 false。

年度/补充公告年份必须在正文明确，不能使用 title 中的年份或把发布年度当安排年度。窄格式支持数值月日、星期、单日/同年跨月休市、照常重开以及额外周末休市；核对真实日期、星期、区间方向及重开日。每个覆盖年份必须有年度公告。除明确纯发布日期外，未识别的额外日期或未知更正/开放/临时条款拒绝；ISO、斜杠或中文数字日期标记不会绕过拒绝规则。多份材料重叠休市范围或重开日期冲突拒绝，不用最后一份替换先前依据；相同范围兼容补充材料保留双方引用。明确“周末休市”的日期必须确为周末，补班周末不会被推为证券开市日。

参考固定输出 `schema_version/exchange/coverage_start/coverage_end/compiled_at/documents/clauses/days/reference_id/exception_completeness_verified/source_authenticated/production_eligible`。每个自然日为 `session/is_open/basis`；basis 引用 `clause_index/document_index/kind/url/sha256`。条款记录年份、休市起止、重开日、周末日期、完整条款正文及证据类型。`reference_id` 绑定除自身外全部输出，validate 保留系统编译时点并从完整原文重建条款、引用、日期及资格，重新哈希的虚假开市不能通过。

DOCX/PDF 派生正文要求 article 的 `data-evidence-kind` 明确为对应 `docx_extracted_text/pdf_extracted_text`，URL 实际 path 的文件后缀与类型必须相符。派生 UTF-8 摘要不当作原二进制摘要，原件与提取链由 A 单独归档。

compare 先重验参考和第四批日历，限制 SSE 且逐日比较参考覆盖区间，不核验参考以外的日期；相同日历源字节摘要拒绝当作独立参考。返回 `status/compared_days/differences/missing_sessions/reference_id/capture_id`；differences 为 `session/expected/actual/basis`，有缺日时为 incomplete，覆盖完整而有差异时为 mismatch，否则 match。`exception_completeness_verified/source_authenticated/source_verified/historical_availability_verified/training_eligible/eligible/production_eligible` 始终全部 false。

## RED 与 GREEN 证据

初始 RED：`python -m unittest tests_ai_operator.test_calendar_reference -v`，19 项、0.028 秒，`FAILED (errors=36)`，新模块尚不存在。工程期首次实现 18 项通过，1 项失败来自测试 helper 把显式 None 替换默认 fixture；改为直接调用生产 API 测试 None 后，19 项、0.628 秒，`OK`。

快审正文边界 RED 命令：

```powershell
python -m unittest tests_ai_operator.test_calendar_reference.CalendarReferenceTests.test_visible_div_and_bare_body_clauses_cannot_be_ignored tests_ai_operator.test_calendar_reference.CalendarReferenceTests.test_year_intro_cannot_hide_extra_dates_without_weekdays tests_ai_operator.test_calendar_reference.CalendarReferenceTests.test_publication_date_does_not_replace_explicit_notice_year -v
```

结果：3 项、0.343 秒，`FAILED (failures=3, errors=1)`。确认 p 外可见闭市条款被遗漏、简介缺星期日期被忽略、发布年度被误当通知年度。修复完整可见正文遍历、严格简介及明确年度匹配后，22 项、0.645 秒，`OK`。

自审补充交易周与提取类型 RED：新增 `test_week_rule_unknown_weekday_or_dated_closure_rejected` 和 `test_extracted_evidence_marker_matches_declared_document_type`，2 项、0.569 秒，`FAILED (failures=4)`；额外周三/具体日期休市和 DOCX/PDF 类型错配均被证实。修复后 24 项、0.674 秒，`OK`。

自审未知措辞 RED：`test_unknown_closure_or_amendment_never_silently_ignored` 补充“不交易”和“周六正常交易”，1 项、0.473 秒，`FAILED (failures=2)`。收紧未知条款与额外日期规则；实际原文联调又发现星期日休市区间被过宽未知开市匹配误拒，先补合法周末区间重开测试复现，随后修复为区分括号中的日期星期与独立周末交易规则。

A 独立审查 ISO/斜杠日期与合法周末边界 RED 命令：

```powershell
python -m unittest tests_ai_operator.test_calendar_reference.CalendarReferenceTests.test_week_rule_unknown_weekday_or_dated_closure_rejected tests_ai_operator.test_calendar_reference.CalendarReferenceTests.test_weekend_boundary_holiday_allows_weekday_reopening -v
```

结果：2 项、0.486 秒，`FAILED (failures=2, errors=1)`；ISO/斜杠闭市遗漏与合法周末日期误拒均复现。修复后最终定向 GREEN：`python -m unittest tests_ai_operator.test_calendar_reference -v`，25 项、0.841 秒，`OK`，0 跳过。

覆盖：单日/跨月、春节/周末补班、年份/星期/日期、缺年度/周规则/公告例外、标题导航注释脚本剔除、正文裸文本/div、未知规则与措辞、原件提取类型、重开冲突、多源冲突/重复、篡改后重新哈希、合法日期边界、交换所错配、匹配/差异/缺日，以及全部资格仍为 false。仅使用 C 自有定向测试。

## 实际 455 天原文联调

参考输入：`reports/calendar-reference-input.json`，覆盖 `2025-01-02` 至 `2026-04-01`。原件实际字节以 `read_bytes().decode('utf-8')` 保存，不通过文本模式换行规范化改变 CRLF 原始字节。首次输入生成预检正确拒绝了换行转换导致的摘要不符；修正只读解码后生成输入，未修改任何原始材料。

四份输入材料摘要：

- A 的 2023 官方交易规则 DOCX 派生包装：`233c90647a0e37e4c489533b9bb84c2f02f59be25a4ee412a8ac0e6d272f7aba`，明确 `docx_extracted_text`。原 DOCX 摘要为 `7aa2319f6dcf597be1e86b3b69d7c2ad0e6acb2a5d0cc6be48a01af602fded40`，提取链在 `evidence/sse_trading_rules_2023_attachment.extracted.json` 和 A 报告。
- 第六批归档 2025 年年度公告原 HTML：`b88e5777d868192c156b85f7514fafcba8638a8bcec0cd80190b2e67769f5fff`。
- 第六批归档 2026 年年度公告原 HTML：`4e260b815ce1309175ed994e6490a4ccd51b998a5f92bcdc41cec00fc6823dc7`。
- 第六批归档 2026 年春节补充原 HTML：`0e05934820bcd89e88165e7725d14c9b5463ca7ad24b049a9b1aef5fcd44085e`。

从以上原文 build 并 validate 得到 455 个自然日、300 个开市日、15 个解析条款。再读取主代理实际验收的 `outputs/第七批真实文件验收/隔离验收/calendar-compare-请求.json` 内 `calendar_capture`，调用 compare，结果 `match`，`compared_days=455`，差异 0、缺日 0，七项认证及资格标记全部 false。最后一轮解析规则修复后重新构建、验证、对照的命令 exit code=0，未从本地日历推导参考关闭日期。

## 冻结 SHA256

- `ai_operator/calendar_reference.py`：`e29c18919a64bed4e30651edad040af567624b66f09ba4128bc5b0b68505d4a4`
- `tests_ai_operator/test_calendar_reference.py`：`5a075ce2a8941f754d931f521305b0e6629baff4948c5baa1f4e6578dc059249`
- `reports/calendar-reference-input.json`：`7633cfb423fa7c20adcece4ffe7d53a6a214ecae860cf6470b743fbff40c0134`

## 保留限制

这是已取得规则与公告的独立推导参考，match 不证明临时休市检索完整、所有规则已确认适用或真实来源已认证。A 报告明确：2023 规则只有首只注册制主板股票上市日起的条件生效正文，当前未归档该首日的官方佐证；2026 新版明确 2026-07-06 废止旧规则，但不能补齐过去生效证据。临时休市、暂停交易及变更检索尚未完整闭环，未找到公告不等于证明没有例外。参考重建、URL 和 SHA 也不能单独认证真人、下载链或历史到达，所有训练及生产资格保持阻断。
