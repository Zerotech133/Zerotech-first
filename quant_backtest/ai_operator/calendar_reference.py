"""从声明的官方正文推导交易日参考，不认证下载链或例外检索完整性。"""

from datetime import date, timedelta
import hashlib
import json
import re
from urllib.parse import urlsplit

from . import contracts
from .source_review import _url
from .trading_calendar import _coverage, validate_calendar_capture


_DOCUMENT_FIELDS = {"kind", "url", "raw_utf8", "sha256"}
_REFERENCE_FIELDS = {"schema_version", "exchange", "coverage_start", "coverage_end", "compiled_at",
                     "documents", "clauses", "days", "reference_id", "exception_completeness_verified",
                     "source_authenticated", "production_eligible"}
_KINDS = {"week_rule", "annual_notice", "supplement"}
_DATE = r"(?:[12][0-9]{3}年)?[0-9]{1,2}月[0-9]{1,2}日[（(]星期[一二三四五六日天][）)]"
_DATE_PARTS = re.compile(r"(?:(?P<year>[12][0-9]{3})年)?(?P<month>[0-9]{1,2})月(?P<day>[0-9]{1,2})日[（(]星期(?P<weekday>[一二三四五六日天])[）)]")
_HOLIDAY = re.compile(r"(?:[^：:。]{1,40}[：:])?(?P<start>" + _DATE + r")(?:至(?P<end>" + _DATE + r"))?休市[，,](?P<reopen>" + _DATE + r")起照常开市[。.]"
                      r"(?:另外[，,](?P<weekends>" + _DATE + r"(?:、" + _DATE + r")*)为周末休市[。.])?")
_WEEK_RULE = re.compile(r"本所交易日为(?:每周一至周五|每周星期一至星期五|每周周一至周五)[。.]"
                        r"国家法定(?:节)?假日和本所公告的休市日[，,]本所市场休市[。.]")
_YEAR = re.compile(r"([12][0-9]{3})年(?:部分)?(?:节假日|春节|元旦|清明节|劳动节|端午节|中秋节|国庆节)?休市安排")
_PLAIN_DATE = re.compile(r"[0-9]{1,2}月[0-9]{1,2}日")
_DATE_MARKER = re.compile(r"[0-9]{1,2}月[0-9]{1,2}日|[12][0-9]{3}[-/][0-9]{1,2}[-/][0-9]{1,2}|[0-9]{1,2}/[0-9]{1,2}|[一二三四五六七八九十]{1,3}月[一二三四五六七八九十]{1,3}日")
_UNKNOWN = re.compile(r"更正|取消|变更|调整|临时|暂停|停市|顺延|提前|延后|补班|补市|复市|恢复交易|停止交易|不交易|(?:周|星期)[六日天](?![）)])[^。]*(?:开市|交易)")


def _canonical(value):
    try:
        text = contracts.canonical_json(value)
        text.encode("utf-8")
        return text
    except UnicodeError as exc:
        raise ValueError("参考证据必须使用有效 UTF-8 字符") from exc


def _sha(value):
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _reference_id(reference):
    return _sha({key: value for key, value in reference.items() if key != "reference_id"})


def _paragraphs(document):
    try:
        from bs4 import BeautifulSoup, Comment, NavigableString
    except ImportError as exc:
        raise ValueError("官方 HTML 正文解析需要既有 BeautifulSoup") from exc
    soup = BeautifulSoup(document["raw_utf8"], "html.parser")
    for comment in soup.find_all(string=lambda value: isinstance(value, Comment)):
        comment.extract()
    for tag in soup.select("head,title,script,style,nav,header,footer,noscript,template,[hidden],[aria-hidden='true']"):
        if tag.parent is not None:
            tag.decompose()
    roots = []
    for selector in (".allZoom", "article", "main"):
        roots = soup.select(selector)
        if roots:
            break
    if len(roots) != 1:
        raise ValueError("证据必须包含一个明确可识别的正文容器")
    root = roots[0]
    evidence_type = root.get("data-evidence-kind", "html_body")
    if evidence_type not in ("html_body", "pdf_extracted_text", "docx_extracted_text"):
        raise ValueError("未知正文提取证据类型")
    source_path = urlsplit(document["url"]).path.lower()
    for suffix in ("pdf", "docx"):
        if source_path.endswith("." + suffix) and evidence_type != suffix + "_extracted_text":
            raise ValueError("PDF/DOCX 派生文本必须明确标记对应提取证据类型")
    result, fragments = [], []
    blocks = {"p", "div", "section", "article", "main", "li", "ul", "ol", "tr", "td",
              "blockquote", "h1", "h2", "h3", "h4", "h5", "h6", "br"}

    def flush():
        text = re.sub(r"\s+", "", "".join(fragments))
        fragments.clear()
        if text:
            result.append(text)

    def visit(node):
        if isinstance(node, NavigableString):
            fragments.append(str(node))
            return
        boundary = node.name in blocks
        if boundary:
            flush()
        for child in node.children:
            visit(child)
        if boundary:
            flush()

    visit(root)
    flush()
    if not result:
        raise ValueError("证据正文为空")
    return result, evidence_type


def _documents(documents):
    _canonical(documents)
    if type(documents) is not list or not documents:
        raise ValueError("documents 必须是非空 JSON 数组")
    seen_urls, seen_hashes = set(), set()
    frozen = json.loads(_canonical(documents))
    for document in frozen:
        if type(document) is not dict or set(document) != _DOCUMENT_FIELDS:
            raise ValueError("参考 documents 字段必须严格匹配契约")
        if type(document["kind"]) is not str or document["kind"] not in _KINDS:
            raise ValueError("未知参考证据类别")
        _url(document["url"], "SSE")
        raw = document["raw_utf8"]
        if type(raw) is not str or not 1 <= len(raw.encode("utf-8")) <= 1024 * 1024:
            raise ValueError("参考原文必须为 1 字节至 1 MiB 的 UTF-8 字符串")
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        if type(document["sha256"]) is not str or document["sha256"] != digest:
            raise ValueError("参考文档 SHA256 与完整原文不匹配")
        if document["url"] in seen_urls or digest in seen_hashes:
            raise ValueError("参考材料 URL 或原文重复，不能重复计算独立依据")
        seen_urls.add(document["url"])
        seen_hashes.add(digest)
    return frozen


def _date(text, year):
    match = _DATE_PARTS.fullmatch(text)
    if match is None:
        raise ValueError("休市日期不符合受支持的月日与星期格式")
    if match["year"] is not None and int(match["year"]) != year:
        raise ValueError("休市条款年份与正文明确年度不一致")
    try:
        value = date(year, int(match["month"]), int(match["day"]))
    except ValueError as exc:
        raise ValueError("休市条款自然日期不存在") from exc
    weekdays = {name: index for index, name in enumerate("一二三四五六日")}
    weekdays["天"] = 6
    if value.weekday() != weekdays[match["weekday"]]:
        raise ValueError("休市条款日期与声明星期不一致")
    return value


def _clause(document, index, evidence_type, text, *, year=None, start=None, end=None, reopen=None, weekends=None):
    return {"kind": document["kind"], "year": year, "start": start, "end": end,
            "reopen_at": reopen, "weekend_days": weekends or [], "document_index": index,
            "url": document["url"], "sha256": document["sha256"], "text": text, "evidence_type": evidence_type}


def _parse(document, index):
    paragraphs, evidence_type = _paragraphs(document)
    text = "".join(paragraphs)
    if document["kind"] == "week_rule":
        matches = list(_WEEK_RULE.finditer(text))
        definitions = re.findall(r"本所交易日为[^。]*[。.]", text)
        if len(matches) != 1 or any(re.fullmatch(r"本所交易日为(?:每周一至周五|每周星期一至星期五|每周周一至周五)[。.]", value) is None for value in definitions):
            raise ValueError("交易周正文必须明确周一至周五与法定假日/公告休市例外")
        if re.search(r"周[六日天][^。]*开市", text):
            raise ValueError("交易周正文含受支持规则以外的开市条款")
        remaining = _WEEK_RULE.sub("", text)
        if "休市" in remaining:
            raise ValueError("交易周正文含标准例外以外的休市条款，不能默默忽略")
        if re.search(r"(?:每周|周|星期)[一二三四五六日天][^。]*(?:休市|开市|交易)", remaining):
            raise ValueError("交易周正文含无法解释的额外星期休市/交易条款")
        if any(_DATE_MARKER.search(paragraph) and re.search(r"休市|开市|停市|停止交易|不交易|正常交易", paragraph) for paragraph in paragraphs):
            raise ValueError("交易周文档含无法解释的额外日期休市/交易条款")
        return [_clause(document, index, evidence_type, matches[0].group())]
    years = {int(match) for match in _YEAR.findall(text)}
    if len(years) != 1:
        raise ValueError("年度/补充公告正文必须明确唯一休市安排年份")
    year = years.pop()
    result = []
    for paragraph in paragraphs:
        if _UNKNOWN.search(paragraph):
            raise ValueError("公告含未知更正、开放或临时规则，不能默默忽略")
        match = _HOLIDAY.fullmatch(paragraph)
        if match is not None:
            first, last = _date(match["start"], year), _date(match["end"] or match["start"], year)
            reopen = _date(match["reopen"], year)
            if last < first or reopen <= last:
                raise ValueError("休市区间方向或同年重开顺序非法")
            weekends = [_date(item.group(), year) for item in _DATE_PARTS.finditer(match["weekends"] or "")]
            if any(day.weekday() < 5 for day in weekends):
                raise ValueError("声明为周末休市的日期必须是周六或周日")
            result.append(_clause(document, index, evidence_type, paragraph, year=year,
                                  start=first.isoformat(), end=last.isoformat(), reopen=reopen.isoformat(),
                                  weekends=[day.isoformat() for day in weekends]))
        elif _DATE_MARKER.search(paragraph) and re.fullmatch(r"[12][0-9]{3}年[0-9]{1,2}月[0-9]{1,2}日", paragraph) is None:
            raise ValueError("公告正文包含受支持条款以外的额外日期，不能默默忽略")
        elif "休市" in paragraph or "开市" in paragraph:
            header = re.fullmatch(r"(?:[一二三四五六七八九十]+、)?休市安排[：:]?", paragraph)
            introduction = (_YEAR.search(paragraph) is not None and _PLAIN_DATE.search(paragraph) is None
                            and re.search(r"(?:通知|公告|安排)如下[。:：]?\Z", paragraph) is not None)
            if not header and not introduction:
                raise ValueError("公告含无法严格解释的休市/重开条款")
    if not result:
        raise ValueError("年度/补充公告没有受支持的休市与重开条款")
    return result


def _derive(documents, start, end, compiled_at):
    _, _, expected_days = _coverage(start, end)
    frozen = _documents(documents)
    clauses = []
    for index, document in enumerate(frozen):
        clauses.extend(_parse(document, index))
    weekly = [index for index, clause in enumerate(clauses) if clause["kind"] == "week_rule"]
    annual_years = {clause["year"] for clause in clauses if clause["kind"] == "annual_notice"}
    if not weekly or not {day.year for day in expected_days} <= annual_years:
        raise ValueError("缺少交易周依据或覆盖每个目标年份的年度公告")
    holidays, closed = [], {}
    for index, clause in enumerate(clauses):
        if clause["kind"] == "week_rule":
            continue
        first, last = date.fromisoformat(clause["start"]), date.fromisoformat(clause["end"])
        for previous in holidays:
            if first <= date.fromisoformat(previous["end"]) and date.fromisoformat(previous["start"]) <= last:
                if (clause["start"], clause["end"], clause["reopen_at"]) != (previous["start"], previous["end"], previous["reopen_at"]):
                    raise ValueError("多份材料的休市区间或重开条款冲突，不能选择最后一条")
        holidays.append(clause)
        cursor = first
        while cursor <= last:
            closed.setdefault(cursor, []).append(index)
            cursor += timedelta(days=1)
        for weekend in clause["weekend_days"]:
            closed.setdefault(date.fromisoformat(weekend), []).append(index)
    for clause in holidays:
        cursor = date.fromisoformat(clause["end"]) + timedelta(days=1)
        while cursor.weekday() >= 5 or cursor in closed:
            cursor += timedelta(days=1)
        if cursor.isoformat() != clause["reopen_at"]:
            raise ValueError("公告重开日必须等于完整推导日历的首个开市日")
    days = []
    for day in expected_days:
        basis = []
        for clause_index in weekly + closed.get(day, []):
            clause = clauses[clause_index]
            basis.append({"clause_index": clause_index, "document_index": clause["document_index"],
                          "kind": clause["kind"], "url": clause["url"], "sha256": clause["sha256"]})
        days.append({"session": day.isoformat(), "is_open": int(day.weekday() < 5 and day not in closed), "basis": basis})
    result = {"schema_version": 1, "exchange": "SSE", "coverage_start": start, "coverage_end": end,
              "compiled_at": compiled_at, "documents": frozen, "clauses": clauses, "days": days,
              "exception_completeness_verified": False, "source_authenticated": False, "production_eligible": False}
    result["reference_id"] = _reference_id(result)
    return result


def build_calendar_reference(documents, coverage_start, coverage_end) -> dict:
    """从明示交易周及年度正文推导参考，系统生成编译时间。"""
    return _derive(documents, coverage_start, coverage_end, contracts.normalize_time(contracts.utc_now()))


def validate_calendar_reference(reference) -> dict:
    """保留编译时点并从完整原文重建全部条款与日期，不重读源文件。"""
    _canonical(reference)
    if type(reference) is not dict or set(reference) != _REFERENCE_FIELDS:
        raise ValueError("calendar reference 字段不符合冻结契约")
    if type(reference["schema_version"]) is not int or reference["schema_version"] != 1:
        raise ValueError("仅支持整数 schema_version=1")
    compiled = reference["compiled_at"]
    if type(compiled) is not str or contracts.normalize_time(compiled) != compiled or compiled > contracts.normalize_time(contracts.utc_now()):
        raise ValueError("compiled_at 必须是非未来的规范 UTC 时间")
    if reference["reference_id"] != _reference_id(reference):
        raise ValueError("reference_id 与完整参考不匹配")
    rebuilt = _derive(reference["documents"], reference["coverage_start"], reference["coverage_end"], compiled)
    if _canonical(reference) != _canonical(rebuilt):
        raise ValueError("参考条款、日期或资格与原文重建语义不一致")
    return rebuilt


def compare_calendar_reference(reference, calendar_capture) -> dict:
    """逐日描述性比较，匹配也不认证来源、例外检索或训练/生产资格。"""
    reference = validate_calendar_reference(reference)
    capture = validate_calendar_capture(calendar_capture)
    if capture["exchange"] != "SSE":
        raise ValueError("SSE 参考不能比较其他交易所日历")
    if {document["sha256"] for document in reference["documents"]} & {item["sha256"] for item in capture["source_files"]}:
        raise ValueError("相同本地日历分片不能充当独立参考来源")
    actual = {day["session"]: day["is_open"] for day in capture["days"]}
    differences, missing, compared = [], [], 0
    for day in reference["days"]:
        session = day["session"]
        if session not in actual:
            missing.append(session)
            continue
        compared += 1
        if actual[session] != day["is_open"]:
            differences.append({"session": session, "expected": day["is_open"], "actual": actual[session], "basis": day["basis"]})
    status = "incomplete" if missing else "mismatch" if differences else "match"
    return {"status": status, "compared_days": compared, "differences": differences, "missing_sessions": missing,
            "reference_id": reference["reference_id"], "capture_id": capture["capture_id"],
            "exception_completeness_verified": False, "source_authenticated": False, "source_verified": False,
            "historical_availability_verified": False, "training_eligible": False, "eligible": False, "production_eligible": False}
